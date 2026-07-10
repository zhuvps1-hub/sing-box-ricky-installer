#!/usr/bin/env bash
set -Eeuo pipefail

REPO="zhuvps1-hub/sing-box-ricky-installer"
RAW_BASE="${RAW_BASE:-https://raw.githubusercontent.com}"
JSDELIVR_BASE="${JSDELIVR_BASE:-https://cdn.jsdelivr.net/gh}"
BASE_DIR="/opt/iwan-gateway-panel"
RELEASES_DIR="$BASE_DIR/releases"
CURRENT_LINK="$BASE_DIR/current"
CONFIG_DIR="/etc/iwan-gateway"
DATA_DIR="/var/lib/iwan-gateway"
SERVICE="iwan-gateway"
SERVICE_FILE="/etc/systemd/system/iwan-gateway.service"
PANEL_PORT="${PANEL_PORT:-8088}"
PANEL_USER="${PANEL_USER:-admin}"
TMP=""
PID=""
OLD_CURRENT=""
OLD_SERVICE_FILE=""
OLD_AUTH_FILE=""
OLD_SERVICE_ACTIVE=0
OLD_LEGACY_ACTIVE=0
AUTH_CHANGED=0

info(){ printf '\033[1;32m[信息]\033[0m %s\n' "$*"; }
warn(){ printf '\033[1;33m[提示]\033[0m %s\n' "$*"; }
die(){ printf '\033[1;31m[错误]\033[0m %s\n' "$*" >&2; exit 1; }
cleanup(){ [[ -n "$PID" ]] && kill "$PID" 2>/dev/null || true; [[ -n "$TMP" && -d "$TMP" ]] && rm -rf "$TMP"; }
trap cleanup EXIT

[[ $EUID -eq 0 ]] || die "请使用 root 用户运行。"
command -v systemctl >/dev/null 2>&1 || die "系统未使用 systemd。"

if [[ ${1:-} == uninstall ]]; then
  systemctl disable --now "$SERVICE" 2>/dev/null || true
  rm -f "$SERVICE_FILE"
  rm -rf "$BASE_DIR"
  systemctl daemon-reload
  warn "仅卸载 Web 面板；sing-box、mosdns、节点、密码和分流配置均已保留。"
  exit 0
fi

if ! command -v python3 >/dev/null 2>&1 || ! command -v curl >/dev/null 2>&1; then
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update -y
    DEBIAN_FRONTEND=noninteractive apt-get install -y python3 curl ca-certificates
  elif command -v dnf >/dev/null 2>&1; then
    dnf install -y python3 curl ca-certificates
  elif command -v yum >/dev/null 2>&1; then
    yum install -y python3 curl ca-certificates
  else
    die "请先安装 Python 3.10+ 和 curl。"
  fi
fi

python3 - <<'PY'
import sys
if sys.version_info < (3,10):
    raise SystemExit('需要 Python 3.10 或更高版本，当前为 '+sys.version.split()[0])
print('Python',sys.version.split()[0])
PY

TMP="$(mktemp -d)"
mkdir -p "$TMP/stage" "$TMP/candidate-config" "$TMP/candidate-data" "$TMP/new-auth" "$CONFIG_DIR" "$DATA_DIR" "$RELEASES_DIR"
chmod 700 "$CONFIG_DIR" "$DATA_DIR" "$TMP/candidate-config" "$TMP/candidate-data" "$TMP/new-auth"

info "通过固定提交 Raw 下载并验证稳定版本（不使用 GitHub API）…"
REPO="$REPO" RAW_BASE="$RAW_BASE" JSDELIVR_BASE="$JSDELIVR_BASE" DEST="$TMP/stage" META="$TMP/release.env" python3 - <<'PY'
import hashlib
import json
import os
import pathlib
import re
import time
import urllib.error
import urllib.parse
import urllib.request

repo = os.environ["REPO"]
raw_base = os.environ["RAW_BASE"].rstrip("/")
jsdelivr_base = os.environ["JSDELIVR_BASE"].rstrip("/")
dest = pathlib.Path(os.environ["DEST"])
meta = pathlib.Path(os.environ["META"])
headers = {
    "User-Agent": "iwan-gateway-installer/2",
    "Accept": "application/octet-stream",
    "Cache-Control": "no-cache",
}

def fetch_bytes(urls, label):
    errors = []
    for url in urls:
        for attempt in range(1, 4):
            request = urllib.request.Request(url, headers=headers)
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    data = response.read()
                if not data:
                    raise RuntimeError("返回内容为空")
                return data
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError, RuntimeError) as exc:
                errors.append(f"{url} [{attempt}/3]: {exc}")
                if attempt < 3:
                    time.sleep(attempt)
    raise SystemExit(label + " 下载失败：\n" + "\n".join(errors[-6:]))

cache_key = str(int(time.time()))
manifest_data = fetch_bytes([
    f"{raw_base}/{repo}/main/panel-release.json?ts={cache_key}",
    f"{jsdelivr_base}/{repo}@main/panel-release.json?ts={cache_key}",
], "发布清单")
try:
    manifest = json.loads(manifest_data.decode("utf-8"))
except (UnicodeDecodeError, json.JSONDecodeError) as exc:
    raise SystemExit("发布清单解析失败：" + str(exc))

version = str(manifest.get("version", ""))
ref = str(manifest.get("ref", ""))
files = manifest.get("files", [])
if not re.fullmatch(r"\d+\.\d+\.\d+", version):
    raise SystemExit("发布版本号无效")
if not re.fullmatch(r"[0-9a-f]{40}", ref):
    raise SystemExit("固定提交无效")
if not isinstance(files, list) or not 1 <= len(files) <= 30:
    raise SystemExit("发布文件清单无效")

for item in files:
    src = str(item.get("source", ""))
    target = str(item.get("target", ""))
    expected = str(item.get("git_blob", ""))
    mode = str(item.get("mode", "0644"))
    if not re.fullmatch(r"[A-Za-z0-9_./-]+", src) or ".." in src.split("/"):
        raise SystemExit("源路径无效：" + src)
    if not re.fullmatch(r"[A-Za-z0-9_./-]+", target) or ".." in target.split("/"):
        raise SystemExit("目标路径无效：" + target)
    if not re.fullmatch(r"[0-9a-f]{40}", expected):
        raise SystemExit("Blob SHA 无效：" + src)
    if mode not in ("0644", "0755"):
        raise SystemExit("权限无效：" + src)

    encoded_src = urllib.parse.quote(src, safe="/")
    data = fetch_bytes([
        f"{raw_base}/{repo}/{ref}/{encoded_src}?blob={expected}",
        f"{jsdelivr_base}/{repo}@{ref}/{encoded_src}?blob={expected}",
    ], src)
    actual = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
    if actual != expected:
        raise SystemExit(f"文件校验失败 {src}：期望 {expected}，实际 {actual}")
    output = dest / target
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(data)
    output.chmod(int(mode, 8))

required = ["app.py", "web/index.html", "web/app.css", "web/app.js"]
for name in required:
    if not (dest / name).is_file():
        raise SystemExit("缺少发布文件：" + name)

meta.write_text(f"VERSION={version}\nREF={ref}\n", encoding="utf-8")
print(f"已验证 {len(files)} 个文件，版本 {version}，提交 {ref[:12]}")
PY

# 版本号与提交均已通过正则校验，可安全导入。
source "$TMP/release.env"
python3 -m py_compile "$TMP/stage/app.py" || die "Python 语法检查失败；旧面板未受影响。"
SINGBOX_BACKUP_DIR="$TMP/selftest-backup" IWAN_PANEL_CONFIG_DIR="$TMP/selftest-config" IWAN_PANEL_DATA_DIR="$TMP/selftest-data" \
  python3 "$TMP/stage/app.py" --self-test >/dev/null || die "面板内置自检失败；旧面板未受影响。"

if [[ -t 0 ]]; then
  read -r -p "Web 面板端口 [$PANEL_PORT]：" input
  PANEL_PORT="${input:-$PANEL_PORT}"
fi
[[ "$PANEL_PORT" =~ ^[0-9]+$ ]] && ((PANEL_PORT>=1 && PANEL_PORT<=65535)) || die "面板端口无效。"

AUTH_OK=0
if [[ -f "$CONFIG_DIR/auth.json" ]]; then
  python3 - "$CONFIG_DIR/auth.json" <<'PY' && AUTH_OK=1 || true
import json,sys
try:
 d=json.load(open(sys.argv[1],encoding='utf-8')); p=d.get('password',{})
 assert isinstance(d.get('username'),str) and p.get('algorithm')=='pbkdf2-sha256' and p.get('hash') and p.get('salt')
except Exception: raise SystemExit(1)
PY
fi

if ((AUTH_OK)); then
  info "保留现有兼容登录账号。"
else
  if [[ -t 0 ]]; then read -r -p "面板用户名 [$PANEL_USER]：" input; PANEL_USER="${input:-$PANEL_USER}"; fi
  if [[ -z "${PANEL_PASSWORD:-}" ]]; then
    [[ -t 0 ]] || die "首次安装请设置 PANEL_PASSWORD 环境变量。"
    while true; do
      read -r -s -p "设置面板密码（至少 8 位）：" PANEL_PASSWORD; printf '\n'
      read -r -s -p "再次输入密码：" password2; printf '\n'
      [[ ${#PANEL_PASSWORD} -ge 8 ]] || { warn "密码至少 8 位。"; continue; }
      [[ "$PANEL_PASSWORD" == "$password2" ]] || { warn "两次密码不一致。"; continue; }
      break
    done
  fi
  PANEL_ADMIN_USER="$PANEL_USER" PANEL_ADMIN_PASSWORD="$PANEL_PASSWORD" SINGBOX_BACKUP_DIR="$TMP/auth-backup" \
  IWAN_PANEL_CONFIG_DIR="$TMP/new-auth" IWAN_PANEL_DATA_DIR="$TMP/new-auth-data" \
    python3 "$TMP/stage/app.py" --init-auth >/dev/null || die "账号初始化失败；旧面板未受影响。"
fi

# 用完全隔离的测试账号运行真实候选进程，不提前修改正式账号和底层配置。
PANEL_ADMIN_USER="candidate" PANEL_ADMIN_PASSWORD="candidate-password-123" SINGBOX_BACKUP_DIR="$TMP/candidate-backup" \
IWAN_PANEL_CONFIG_DIR="$TMP/candidate-config" IWAN_PANEL_DATA_DIR="$TMP/candidate-data" \
  python3 "$TMP/stage/app.py" --init-auth >/dev/null
PORT="$(python3 - <<'PY'
import socket
s=socket.socket(); s.bind(('127.0.0.1',0)); print(s.getsockname()[1]); s.close()
PY
)"
info "在临时端口 $PORT 验证启动、登录与现有配置采样…"
SINGBOX_BACKUP_DIR="$TMP/candidate-backup" IWAN_PANEL_CONFIG_DIR="$TMP/candidate-config" IWAN_PANEL_DATA_DIR="$TMP/candidate-data" \
  python3 "$TMP/stage/app.py" --host 127.0.0.1 --port "$PORT" >"$TMP/candidate.log" 2>&1 &
PID=$!
OK=0
for _ in $(seq 1 30); do
  curl -fsS --max-time 2 "http://127.0.0.1:$PORT/healthz" | grep -q '"ok": true' && { OK=1; break; }
  kill -0 "$PID" 2>/dev/null || break
  sleep .3
done
if ((OK==0)); then cat "$TMP/candidate.log" >&2 || true; die "候选面板启动失败；旧面板未受影响。"; fi
LOGIN="$(curl -fsS --max-time 4 -c "$TMP/cookie" -H 'Content-Type: application/json' \
  -d '{"username":"candidate","password":"candidate-password-123"}' "http://127.0.0.1:$PORT/api/login")"
printf '%s' "$LOGIN" | grep -q '"ok": true' || die "候选登录测试失败；旧面板未受影响。"
curl -fsS --max-time 5 -b "$TMP/cookie" "http://127.0.0.1:$PORT/api/config" | grep -q '"ok": true' \
  || die "候选配置采样失败；旧面板未受影响。"
kill "$PID" 2>/dev/null || true; wait "$PID" 2>/dev/null || true; PID=""

RELEASE_DIR="$RELEASES_DIR/${VERSION}-${REF:0:12}"
rm -rf "$RELEASE_DIR"; mkdir -p "$RELEASE_DIR"; cp -a "$TMP/stage/." "$RELEASE_DIR/"
cat >"$TMP/service" <<EOF
[Unit]
Description=iWAN Gateway Web Panel
After=network-online.target
Wants=network-online.target
[Service]
Type=simple
User=root
Group=root
WorkingDirectory=$CURRENT_LINK
ExecStart=/usr/bin/python3 $CURRENT_LINK/app.py --host 0.0.0.0 --port $PANEL_PORT
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=full
ReadWritePaths=$CONFIG_DIR $DATA_DIR -/etc/sing-box -/etc/mosdns -/var/log
LimitNOFILE=65536
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONDONTWRITEBYTECODE=1
[Install]
WantedBy=multi-user.target
EOF

systemctl is-active --quiet "$SERVICE" && OLD_SERVICE_ACTIVE=1 || true
systemctl is-active --quiet f7010u-gateway && OLD_LEGACY_ACTIVE=1 || true
[[ -L "$CURRENT_LINK" ]] && OLD_CURRENT="$(readlink -f "$CURRENT_LINK")" || true
[[ -f "$SERVICE_FILE" ]] && { OLD_SERVICE_FILE="$TMP/old-service"; cp -a "$SERVICE_FILE" "$OLD_SERVICE_FILE"; }
[[ -f "$CONFIG_DIR/auth.json" ]] && { OLD_AUTH_FILE="$TMP/old-auth"; cp -a "$CONFIG_DIR/auth.json" "$OLD_AUTH_FILE"; }

rollback(){
  warn "正式启动失败，恢复旧面板和旧账号…"
  systemctl stop "$SERVICE" 2>/dev/null || true
  if [[ -n "$OLD_CURRENT" && -d "$OLD_CURRENT" ]]; then ln -sfn "$OLD_CURRENT" "$CURRENT_LINK.rollback"; mv -Tf "$CURRENT_LINK.rollback" "$CURRENT_LINK"; fi
  [[ -n "$OLD_SERVICE_FILE" ]] && cp -a "$OLD_SERVICE_FILE" "$SERVICE_FILE"
  if ((AUTH_CHANGED)); then [[ -n "$OLD_AUTH_FILE" ]] && cp -a "$OLD_AUTH_FILE" "$CONFIG_DIR/auth.json" || rm -f "$CONFIG_DIR/auth.json"; fi
  systemctl daemon-reload
  ((OLD_SERVICE_ACTIVE)) && systemctl start "$SERVICE" 2>/dev/null || true
  ((OLD_LEGACY_ACTIVE)) && systemctl start f7010u-gateway 2>/dev/null || true
}

info "全部验证通过，原子切换 Web 面板；sing-box 和 mosdns 保持运行…"
systemctl stop "$SERVICE" 2>/dev/null || true
systemctl stop f7010u-gateway 2>/dev/null || true
if ((AUTH_OK==0)); then install -m 0600 "$TMP/new-auth/auth.json" "$CONFIG_DIR/auth.json"; AUTH_CHANGED=1; fi
ln -sfn "$RELEASE_DIR" "$CURRENT_LINK.new"; mv -Tf "$CURRENT_LINK.new" "$CURRENT_LINK"
install -m 0644 "$TMP/service" "$SERVICE_FILE"
systemctl daemon-reload; systemctl enable --now "$SERVICE" >/dev/null
OK=0
for _ in $(seq 1 30); do curl -fsS --max-time 2 "http://127.0.0.1:$PANEL_PORT/healthz" | grep -q '"ok": true' && { OK=1; break; }; sleep .3; done
if ((OK==0)); then journalctl -u "$SERVICE" -n 100 --no-pager || true; rollback; die "正式健康检查失败，旧面板已恢复。"; fi

systemctl disable f7010u-gateway sing-box-panel 2>/dev/null || true
PUBLIC_IP="$(curl -4fsS --connect-timeout 3 https://api.ipify.org 2>/dev/null || true)"
info "iWAN Gateway v$VERSION 安装/升级完成。"
printf '\n访问：http://%s:%s\n' "${PUBLIC_IP:-你的VPS公网IP}" "$PANEL_PORT"
printf '底层：未安装、未覆盖、未重启 sing-box 和 mosdns。\n'
printf '验证：固定提交 Raw → Git Blob 校验 → 语法 → 自检 → 临时启动 → 登录 → 配置采样 → 正式健康检查。\n'
