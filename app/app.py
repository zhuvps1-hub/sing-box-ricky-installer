#!/usr/bin/env python3
from __future__ import annotations
import hashlib, hmac, json, os, secrets, socket, sqlite3, subprocess, tempfile, threading, time
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

DATA=Path(os.environ.get('IWAN_DATA','/etc/iwan-gateway')); DATA.mkdir(parents=True,exist_ok=True)
DB=DATA/'gateway.db'; SB=Path('/etc/sing-box/config.json'); STATIC=Path(__file__).parent/'static'
PORT=int(os.environ.get('IWAN_PANEL_PORT','8088'))
CATS=['cn','ai','google','youtube','netflix','tiktok','telegram']
LABELS={'cn':'国内','ai':'AI','google':'Google','youtube':'YouTube','netflix':'Netflix','tiktok':'TikTok','telegram':'Telegram'}
DOMAINS={
'ai':['openai.com','chatgpt.com','oaistatic.com','oaiusercontent.com','anthropic.com','claude.ai','gemini.google.com','perplexity.ai','copilot.microsoft.com'],
'google':['google.com','googleapis.com','gstatic.com','googleusercontent.com','ggpht.com','googleadservices.com'],
'youtube':['youtube.com','youtu.be','googlevideo.com','ytimg.com','youtube-nocookie.com','youtubei.googleapis.com'],
'netflix':['netflix.com','netflix.net','nflxext.com','nflximg.com','nflxso.net','nflxvideo.net'],
'tiktok':['tiktok.com','tiktokcdn.com','tiktokv.com','byteoversea.com','ibytedtos.com','muscdn.com','musical.ly'],
'telegram':['telegram.org','telegram.me','t.me','telegra.ph','telesco.pe']}
TG_CIDR=['91.108.4.0/22','91.108.8.0/22','91.108.12.0/22','91.108.16.0/22','91.108.20.0/22','91.108.56.0/22','149.154.160.0/20','2001:b28:f23d::/48','2001:b28:f23f::/48','2001:67c:4e8::/48']
CN_RULES=[
 {'type':'remote','tag':'geosite-cn','format':'binary','url':'https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/sing/geo/geosite/cn.srs','download_detour':'direct'},
 {'type':'remote','tag':'geoip-cn','format':'binary','url':'https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/sing/geo/geoip/cn.srs','download_detour':'direct'}]
LOCK=threading.Lock()

def db():
 c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; return c

def pbkdf(password,salt=None):
 salt=salt or secrets.token_bytes(16); raw=hashlib.pbkdf2_hmac('sha256',password.encode(),salt,260000)
 return salt.hex()+':'+raw.hex()

def verify(password,value):
 try:
  s,d=value.split(':',1); return hmac.compare_digest(pbkdf(password,bytes.fromhex(s)).split(':',1)[1],d)
 except Exception:return False

def init():
 with db() as c:
  c.executescript('''CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY,v TEXT NOT NULL);CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,expires INTEGER NOT NULL);''')
  defaults={'username':'admin','password':pbkdf('admin'),'nodes':'[]','routing':json.dumps({**{x:'direct' for x in CATS},'default':'direct'}),'iwan':json.dumps({'enabled':True,'listen':'::','port':8000,'pool':'10.10.10.0/24','mtu':1400,'username':'','password':''})}
  for k,v in defaults.items(): c.execute('INSERT OR IGNORE INTO settings VALUES(?,?)',(k,v))

def get(k):
 with db() as c:
  r=c.execute('SELECT v FROM settings WHERE k=?',(k,)).fetchone(); return r['v'] if r else ''
def setv(k,v):
 with db() as c:c.execute('INSERT INTO settings VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v',(k,v))
def state(): return {'nodes':json.loads(get('nodes')),'routing':json.loads(get('routing')),'iwan':json.loads(get('iwan'))}

def build(s):
 nodes=s['nodes']; routing=s['routing']; iw=s['iwan']; tags={n['tag'] for n in nodes}|{'direct'}
 for k,v in routing.items():
  if v not in tags: raise ValueError(f'{LABELS.get(k,"默认出口")}选择的节点不存在')
 out=[{'type':'direct','tag':'direct'}]
 for n in nodes:
  item={'type':'shadowsocks','tag':n['tag'],'server':n['server'],'server_port':int(n['port']),'method':n['method'],'password':n['password']}
  if n.get('plugin'): item['plugin']=n['plugin']
  if n.get('plugin_opts'): item['plugin_opts']=n['plugin_opts']
  out.append(item)
 inbound=[]
 if iw.get('enabled'):
  x={'type':'iwan','tag':'iwan-in','listen':iw.get('listen','::'),'listen_port':int(iw.get('port',8000)),'address_pool':iw.get('pool','10.10.10.0/24'),'mtu':int(iw.get('mtu',1400))}
  if iw.get('username') or iw.get('password'): x['users']=[{'username':iw.get('username',''),'password':iw.get('password','')}]
  inbound.append(x)
 rules=[{'ip_is_private':True,'outbound':'direct'}]
 if routing['cn']:
  rules.append({'rule_set':['geosite-cn','geoip-cn'],'outbound':routing['cn']})
 for k in ['ai','youtube','netflix','tiktok','telegram','google']:
  r={'domain_suffix':DOMAINS[k],'outbound':routing[k]}
  if k=='telegram':r['ip_cidr']=TG_CIDR
  rules.append(r)
 return {'log':{'level':'info','timestamp':True},'inbounds':inbound,'outbounds':out,'route':{'rule_set':CN_RULES,'rules':rules,'final':routing['default'],'auto_detect_interface':True}}

def apply_config(s):
 with LOCK:
  cfg=build(s); SB.parent.mkdir(parents=True,exist_ok=True)
  fd,tmp=tempfile.mkstemp(prefix='iwan-',suffix='.json',dir=str(SB.parent)); os.close(fd)
  Path(tmp).write_text(json.dumps(cfg,ensure_ascii=False,indent=2))
  try:
   p=subprocess.run(['/usr/local/bin/sing-box','check','-c',tmp],text=True,capture_output=True,timeout=6)
   if p.returncode: raise ValueError((p.stderr or p.stdout).strip() or '配置检查失败')
   backup=DATA/'backups'/f'config-{int(time.time())}.json'; backup.parent.mkdir(exist_ok=True)
   if SB.exists(): backup.write_bytes(SB.read_bytes())
   os.replace(tmp,SB); os.chmod(SB,0o600)
   r=subprocess.run(['systemctl','reload','sing-box'],capture_output=True,timeout=3)
   if r.returncode: subprocess.run(['systemctl','restart','sing-box'],check=True,timeout=6)
   time.sleep(.15)
   if subprocess.run(['systemctl','is-active','--quiet','sing-box']).returncode: raise ValueError('sing-box 未正常运行')
  finally:
   if os.path.exists(tmp): os.unlink(tmp)

def service_active(): return subprocess.run(['systemctl','is-active','--quiet','sing-box']).returncode==0

def latency(n):
 t=time.perf_counter()
 try:
  with socket.create_connection((n['server'],int(n['port'])),timeout=2): pass
  return {'ok':True,'ms':round((time.perf_counter()-t)*1000)}
 except Exception as e:return {'ok':False,'error':str(e)[:80]}

class H(BaseHTTPRequestHandler):
 server_version='iWAN/1.0'
 def log_message(self,*a): pass
 def sendj(self,obj,code=200,headers=None):
  b=json.dumps(obj,ensure_ascii=False).encode(); self.send_response(code); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Content-Length',str(len(b)))
  for k,v in (headers or {}).items():self.send_header(k,v)
  self.end_headers(); self.wfile.write(b)
 def body(self):
  try:return json.loads(self.rfile.read(int(self.headers.get('Content-Length','0'))) or b'{}')
  except:return {}
 def token(self):
  c=cookies.SimpleCookie(self.headers.get('Cookie','')); return c.get('sid').value if c.get('sid') else ''
 def auth(self):
  t=self.token()
  if not t:return False
  with db() as c:
   r=c.execute('SELECT expires FROM sessions WHERE token=?',(t,)).fetchone()
   return bool(r and r['expires']>time.time())
 def do_GET(self):
  p=urlparse(self.path).path
  if p=='/api/me': return self.sendj({'ok':self.auth(),'username':get('username') if self.auth() else ''})
  if p.startswith('/api/') and not self.auth(): return self.sendj({'error':'未登录'},401)
  if p=='/api/state':
   s=state(); s['service']={'singbox':service_active()}; s['nodes']=[{**n,'password':'','has_password':bool(n.get('password'))} for n in s['nodes']]; return self.sendj(s)
  if p=='/api/status': return self.sendj({'singbox':service_active(),'time':int(time.time())})
  f=STATIC/('index.html' if p in ('/','/index.html') else p.lstrip('/'))
  if not f.exists() or STATIC not in f.resolve().parents:return self.send_error(404)
  b=f.read_bytes(); typ='text/html' if f.suffix=='.html' else 'text/css' if f.suffix=='.css' else 'application/javascript'
  self.send_response(200); self.send_header('Content-Type',typ+'; charset=utf-8'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b)
 def do_POST(self):
  p=urlparse(self.path).path; x=self.body()
  if p=='/api/login':
   if x.get('username')!=get('username') or not verify(str(x.get('password','')),get('password')): return self.sendj({'error':'账号或密码错误'},403)
   t=secrets.token_urlsafe(32)
   with db() as c:c.execute('INSERT INTO sessions VALUES(?,?)',(t,int(time.time()+2592000)))
   return self.sendj({'ok':True},headers={'Set-Cookie':f'sid={t}; Path=/; HttpOnly; SameSite=Strict; Max-Age=2592000'})
  if p=='/api/logout':
   with db() as c:c.execute('DELETE FROM sessions WHERE token=?',(self.token(),))
   return self.sendj({'ok':True},headers={'Set-Cookie':'sid=; Path=/; Max-Age=0'})
  if not self.auth(): return self.sendj({'error':'未登录'},401)
  try:
   if p=='/api/apply':
    nodes=x.get('nodes',[]); routing=x.get('routing',{}); iw=x.get('iwan',{})
    if len(nodes)>100: raise ValueError('节点数量过多')
    seen=set(); old={n['tag']:n for n in state()['nodes']}
    clean=[]
    for n in nodes:
     tag=str(n.get('tag','')).strip(); server=str(n.get('server','')).strip(); method=str(n.get('method','')).strip(); pwd=str(n.get('password','')) or old.get(tag,{}).get('password','')
     if not tag or tag in seen: raise ValueError('节点名称为空或重复')
     if not server or not method or not pwd: raise ValueError(f'节点 {tag} 信息不完整')
     port=int(n.get('port',0));
     if not 1<=port<=65535: raise ValueError(f'节点 {tag} 端口无效')
     seen.add(tag); clean.append({'tag':tag,'server':server,'port':port,'method':method,'password':pwd,'plugin':str(n.get('plugin','')),'plugin_opts':str(n.get('plugin_opts',''))})
    rr={k:str(routing.get(k,'direct')) for k in CATS}; rr['default']=str(routing.get('default','direct'))
    ii={'enabled':bool(iw.get('enabled',True)),'listen':str(iw.get('listen','::')),'port':int(iw.get('port',8000)),'pool':str(iw.get('pool','10.10.10.0/24')),'mtu':int(iw.get('mtu',1400)),'username':str(iw.get('username','')),'password':str(iw.get('password','')) or state()['iwan'].get('password','')}
    s={'nodes':clean,'routing':rr,'iwan':ii}; apply_config(s)
    setv('nodes',json.dumps(clean,ensure_ascii=False)); setv('routing',json.dumps(rr)); setv('iwan',json.dumps(ii)); return self.sendj({'ok':True,'message':'已生效'})
   if p=='/api/latency':
    tag=str(x.get('tag','')); n=next((n for n in state()['nodes'] if n['tag']==tag),None)
    if not n: raise ValueError('节点不存在')
    return self.sendj(latency(n))
   if p=='/api/password':
    if not verify(str(x.get('old','')),get('password')): raise ValueError('原密码错误')
    new=str(x.get('new',''))
    if len(new)<8: raise ValueError('新密码至少 8 位')
    setv('password',pbkdf(new));
    if x.get('username'): setv('username',str(x['username']).strip())
    with db() as c:c.execute('DELETE FROM sessions WHERE token<>?',(self.token(),))
    return self.sendj({'ok':True})
   return self.sendj({'error':'接口不存在'},404)
  except Exception as e:return self.sendj({'error':str(e)},400)

if __name__=='__main__':
 init(); ThreadingHTTPServer(('0.0.0.0',PORT),H).serve_forever()
