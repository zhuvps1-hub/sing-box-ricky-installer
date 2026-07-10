#!/usr/bin/env bash
set -Eeuo pipefail

REPO="zhuvps1-hub/sing-box-ricky-installer"
API_ROOT="https://api.github.com/repos/${REPO}"
BASE_DIR="/opt/iwan-gateway-panel"
RELEASES_DIR="${BASE_DIR}/releases"
CURRENT_LINK="${BASE_DIR}/current"
CONFIG_DIR="/etc/iwan-gateway"
DATA_DIR="/var/lib/iwan-gateway"
SERVICE_NAME="iwan-gateway"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
PANEL_PORT="${PANEL_PORT:-8088}"
PANEL_USER="${PANEL_USER:-admin}"
TMP_DIR=""
CANDIDATE_PID=""
PREVIOUS_TARGET=""
SERVICE_BACKUP=""
AUTH_BACKUP=""
AUTH_REPLACED=0
OLD_SERVICE_ACTIVE=0
OLD_LEGACY_ACTIVE=0

info(){ printf '\033[1;32m[信息]\033[0m %s\n' "$*"; }
warn(){ printf '\033[1;33m[提示]\033[0m %s\n' "$*"; }
die(){ printf '\033[1;31m[错误]\033[0m %s\n' "$*" >&2; exit 1; }
cleanup(){
  [[ -n "$CANDIDATE_PID" ]] && kill "$CANDIDATE_PID" 2>/dev/null || true
  [[ -n "$TMP_DIR" && -d "$TMP_DIR" ]] && rm -rf "$TMP_DIR"
}
trap cleanup EXIT

[[ ${EUID} -eq 0 ]] || die "请使用 root 用户运行。"
command -v systemctl >/dev/null 2>&1 || die "系统未使用 systemd。"

install_deps(){
  if command -v python3 >/dev/null 2>&1 && command -v curl >/dev/null 2>&1; then
    return
  fi
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update -y
    DEBIAN_FRONTEND=noninteractive apt-get install -y python3 curl ca-certificates
  elif command -v dnf >/dev/null 2>&1; then
    dnf install -y python3 curl ca-certificates
  elif command -v yum >/dev/null 2>&1; then
    yum install -y python3 curl ca-certificates
  else
    die "请先安装 python3 和 curl。"
  fi
}

api_download(){
  local url="$1" output="$2"
  local -a args=(
    -fL --retry 4 --retry-all-errors --connect-timeout 15 --max-time 90
    -H "Accept: application/vnd.github+json"
    -H "X-GitHub-Api-Version: 2022-11-28"
    -H "User-Agent: iwan-gateway-installer"
  )
  if [[ -n "${GITHUB_TOKEN:-}" ]]; then
    args+=( -H "Authorization: Bearer ${GITHUB_TOKEN}" )
  fi
  curl "${args[@]}" "$url" -o "$output"
}

check_python(){
  python3 - <<'PY'
import sys
if sys.version_info < (3, 10):
    raise SystemExit("需要 Python 3.10 或更高版本，当前为 " + sys.version.split()[0])
print("Python", sys.version.split()[0])
PY
}

if [[ ${1:-} == "uninstall" ]]; then
  systemctl disable --now "$SERVICE_NAME" 2>/dev/null || true
  rm -f "$SERVICE_FILE"
  rm -rf "$BASE_DIR"
  systemctl daemon-reload
  warn "只卸载了 Web 面板；账号数据、sing-box、mosdns、节点及分流配置均已保留。"
  exit 0
fi

install_deps
check_python
TMP_DIR="$(mktemp -d)"
mkdir -p "$TMP_DIR/stage" "$TMP_DIR/candidate-config" "$TMP_DIR/candidate-data" \
         "$TMP_DIR/new-auth" "$CONFIG_DIR" "$DATA_DIR" "$RELEASES_DIR"
chmod 700 "$CONFIG_DIR" "$DATA_DIR" "$TMP_DIR/candidate-config" "$TMP_DIR/candidate-data" "$TMP_DIR/new-auth"

info "通过 GitHub 对象接口读取稳定版发布清单…"
api_download "${API_ROOT}/contents/panel-release.json?ref=main" "$TMP_DIR/manifest-api.json" \
  || die "无法读取发布清单；当前面板和底层服务均未受影响。"

python3 - "$TMP_DIR/manifest-api.json" "$TMP_DIR/manifest.json" <<'PY'
import base64,json,sys
obj=json.load(open(sys.argv[1],encoding='utf-8'))
if obj.get('encoding') != 'base64' or not obj.get('content'):
    raise SystemExit('GitHub API 未返回有效的发布清单')
data=base64.b64decode(obj['content'],validate=True)
open(sys.argv[2],'wb').write(data)
PY

python3 - "$TMP_DIR/manifest.json" "$TMP_DIR/files.tsv" <<'PY'
import json,re,sys
m=json.load(open(sys.argv[1],encoding='utf-8'))
version=str(m.get('version',''))
ref=str(m.get('ref',''))
files=m.get('files',[])
if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+',version): raise SystemExit('版本号无效')
if not re.fullmatch(r'[0-9a-f]{40}',ref): raise SystemExit('固定提交无效')
if not isinstance(files,list) or not files or len(files)>20: raise SystemExit('文件清单无效')
with open(sys.argv[2],'w',encoding='utf-8') as out:
    out.write(version+'\t'+ref+'\n')
    for f in files:
        src=str(f.get('source','')); dst=str(f.get('target','')); blob=str(f.get('git_blob','')); mode=str(f.get('mode','0644'))
        if not re.fullmatch(r'[A-Za-z0-9_./-]+',src) or '..' in src.split('/'): raise SystemExit('源路径无效')
        if not re.fullmatch(r'[A-Za-z0-9_./-]+',dst) or '..' in dst.split('/'): raise SystemExit('目标路径无效')
        if not re.fullmatch(r'[0-9a-f]{40}',blob): raise SystemExit('Blob 校验值无效')
        if mode not in ('0644','0755'): raise SystemExit('文件权限无效')
        out.write('\t'.join((src,dst,blob,mode))+'\n')
PY

IFS=$'\t' read -r VERSION REF < "$TMP_DIR/files.tsv"
[[ -n "$VERSION" && -n "$REF" ]] || die "发布清单解析失败；当前面板未受影响。"

info "从固定提交 ${REF:0:12} 下载 v${VERSION}，并使用 GitHub 官方 Blob SHA 校验…"
while IFS=$'\t' read -r SOURCE TARGET EXPECTED_BLOB MODE; do
  [[ -n "$SOURCE" ]] || continue
  API_JSON="$TMP_DIR/file-api-$(printf '%s' "$TARGET" | tr '/.' '__').json"
  DEST="$TMP_DIR/stage/$TARGET"
  mkdir -p "$(dirname "$DEST")"
  api_download "${API_ROOT}/contents/${SOURCE}?ref=${REF}" "$API_JSON" \
    || die "文件下载失败：${SOURCE}；当前面板未受影响。"
  python3 - "$API_JSON" "$DEST" "$EXPECTED_BLOB" <<'PY'
import base64,hashlib,json,sys
api_file,dest,expected=sys.argv[1:]
obj=json.load(open(api_file,encoding='utf-8'))
if obj.get('sha') != expected:
    raise SystemExit(f"GitHub 返回的 Blob SHA 不一致：期望 {expected}，实际 {obj.get('sha')}")
if obj.get('encoding') != 'base64' or not obj.get('content'):
    raise SystemExit('GitHub API 未返回 Base64 文件内容')
data=base64.b64decode(obj['content'],validate=True)
actual=hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
if actual != expected:
    raise SystemExit(f'解码后的 Git Blob 校验失败：期望 {expected}，实际 {actual}')
open(dest,'wb').write(data)
PY
  chmod "$MODE" "$DEST"
done < <(tail -n +2 "$TMP_DIR/files.tsv")

[[ -f "$TMP_DIR/stage/app.py" && -f "$TMP_DIR/stage/web/index.html" \
   && -f "$TMP_DIR/stage/web/app.css" && -f "$TMP_DIR/stage/web/app.js" ]] \
  || die "发布文件结构不完整；当前面板未受影响。"

info "执行语法、自检、登录和配置采样测试…"
python3 -m py_compile "$TMP_DIR/stage/app.py" || die "Python 语法检查失败；当前面板未受影响。"
SINGBOX_BACKUP_DIR="$TMP_DIR/selftest-backups" \
IWAN_PANEL_CONFIG_DIR="$TMP_DIR/selftest-config" IWAN_PANEL_DATA_DIR="$TMP_DIR/selftest-data" \
  python3 "$TMP_DIR/stage/app.py" --self-test >/dev/null \
  || die "内置自检失败；当前面板未受影响。"

if [[ -t 0 ]]; then
  read -r -p "Web 面板端口 [${PANEL_PORT}]：" INPUT_PORT
  PANEL_PORT="${INPUT_PORT:-$PANEL_PORT}"
fi
[[ "$PANEL_PORT" =~ ^[0-9]+$ ]] && (( PANEL_PORT >= 1 && PANEL_PORT <= 65535 )) || die "面板端口无效。"

AUTH_COMPATIBLE=0
if [[ -f "$CONFIG_DIR/auth.json" ]]; then
  if python3 - "$CONFIG_DIR/auth.json" <<'PY'
import json,sys
try:
    d=json.load(open(sys.argv[1],encoding='utf-8')); p=d.get('password',{})
    assert isinstance(d.get('username'),str) and isinstance(p,dict)
    assert p.get('algorithm') == 'pbkdf2-sha256' and p.get('hash') and p.get('salt')
except Exception:
    raise SystemExit(1)
PY
  then AUTH_COMPATIBLE=1; fi
fi

if (( AUTH_COMPATIBLE )); then
  info "检测到兼容的面板账号，将保留原登录信息。"
else
  if [[ -t 0 ]]; then
    read -r -p "面板登录用户名 [${PANEL_USER}]：" INPUT_USER
    PANEL_USER="${INPUT_USER:-$PANEL_USER}"
  fi
  if [[ -z "${PANEL_PASSWORD:-}" ]]; then
    [[ -t 0 ]] || die "首次安装请设置 PANEL_PASSWORD 环境变量。"
    while true; do
      read -r -s -p "设置面板登录密码（至少 8 位）：" PANEL_PASSWORD; printf '\n'
      read -r -s -p "再次输入面板密码：" PANEL_PASSWORD_2; printf '\n'
      [[ ${#PANEL_PASSWORD} -ge 8 ]] || { warn "密码至少 8 位。"; continue; }
      [[ "$PANEL_PASSWORD" == "$PANEL_PASSWORD_2" ]] || { warn "两次密码不一致。"; continue; }
      break
    done
  fi
  PANEL_ADMIN_USER="$PANEL_USER" PANEL_ADMIN_PASSWORD="$PANEL_PASSWORD" \
  SINGBOX_BACKUP_DIR="$TMP_DIR/auth-backups" \
  IWAN_PANEL_CONFIG_DIR="$TMP_DIR/new-auth" IWAN_PANEL_DATA_DIR="$TMP_DIR/new-auth-data" \
    python3 "$TMP_DIR/stage/app.py" --init-auth >/dev/null \
    || die "新账号初始化失败；当前面板未受影响。"
fi

PANEL_ADMIN_USER="candidate" PANEL_ADMIN_PASSWORD="candidate-password-123" \
SINGBOX_BACKUP_DIR="$TMP_DIR/candidate-backups" \
IWAN_PANEL_CONFIG_DIR="$TMP_DIR/candidate-config" IWAN_PANEL_DATA_DIR="$TMP_DIR/candidate-data" \
  python3 "$TMP_DIR/stage/app.py" --init-auth >/dev/null

CANDIDATE_PORT="$(python3 - <<'PY'
import socket
s=socket.socket(); s.bind(('127.0.0.1',0)); print(s.getsockname()[1]); s.close()
PY
)"
info "在本机临时端口 ${CANDIDATE_PORT} 启动完整候选版本…"
SINGBOX_BACKUP_DIR="$TMP_DIR/candidate-backups" \
IWAN_PANEL_CONFIG_DIR="$TMP_DIR/candidate-config" IWAN_PANEL_DATA_DIR="$TMP_DIR/candidate-data" \
  python3 "$TMP_DIR/stage/app.py" --host 127.0.0.1 --port "$CANDIDATE_PORT" \
  >"$TMP_DIR/candidate.log" 2>&1 &
CANDIDATE_PID=$!
CANDIDATE_OK=0
for _ in $(seq 1 30); do
  if curl -fsS --max-time 2 "http://127.0.0.1:${CANDIDATE_PORT}/healthz" | grep -q '"ok": true'; then
    CANDIDATE_OK=1; break
  fi
  kill -0 "$CANDIDATE_PID" 2>/dev/null || break
  sleep 0.3
done
if (( CANDIDATE_OK == 0 )); then
  cat "$TMP_DIR/candidate.log" >&2 || true
  die "候选版本健康检查失败；当前面板未受影响。"
fi

LOGIN_JSON="$(curl -fsS --max-time 4 -c "$TMP_DIR/cookies" \
  -H 'Content-Type: application/json' \
  -d '{"username":"candidate","password":"candidate-password-123"}' \
  "http://127.0.0.1:${CANDIDATE_PORT}/api/login")"
printf '%s' "$LOGIN_JSON" | grep -q '"ok": true' \
  || die "候选版本登录测试失败；当前面板未受影响。"
curl -fsS --max-time 5 -b "$TMP_DIR/cookies" \
  "http://127.0.0.1:${CANDIDATE_PORT}/api/config" | grep -q '"ok": true' \
  || die "候选版本配置采样测试失败；当前面板未受影响。"

kill "$CANDIDATE_PID" 2>/dev/null || true
wait "$CANDIDATE_PID" 2>/dev/null || true
CANDIDATE_PID=""

RELEASE_DIR="$RELEASES_DIR/${VERSION}-${REF:0:12}"
rm -rf "$RELEASE_DIR"
mkdir -p "$RELEASE_DIR"
cp -a "$TMP_DIR/stage/." "$RELEASE_DIR/"

cat > "$TMP_DIR/iwan-gateway.service" <<EOF
[Unit]
Description=iWAN Gateway Web Panel
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=${CURRENT_LINK}
ExecStart=/usr/bin/python3 ${CURRENT_LINK}/app.py --host 0.0.0.0 --port ${PANEL_PORT}
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=full
ReadWritePaths=${CONFIG_DIR} ${DATA_DIR} -/etc/sing-box -/etc/mosdns -/var/log
LimitNOFILE=65536
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONDONTWRITEBYTECODE=1

[Install]
WantedBy=multi-user.target
EOF

systemctl is-active --quiet "$SERVICE_NAME" && OLD_SERVICE_ACTIVE=1 || true
systemctl is-active --quiet f7010u-gateway && OLD_LEGACY_ACTIVE=1 || true
[[ -L "$CURRENT_LINK" ]] && PREVIOUS_TARGET="$(readlink -f "$CURRENT_LINK")" || true
[[ -f "$SERVICE_FILE" ]] && { SERVICE_BACKUP="$TMP_DIR/service.backup"; cp -a "$SERVICE_FILE" "$SERVICE_BACKUP"; }
if [[ -f "$CONFIG_DIR/auth.json" ]]; then AUTH_BACKUP="$TMP_DIR/auth.backup"; cp -a "$CONFIG_DIR/auth.json" "$AUTH_BACKUP"; fi

rollback(){
  warn "正式版本启动失败，正在恢复旧面板…"
  systemctl stop "$SERVICE_NAME" 2>/dev/null || true
  if [[ -n "$PREVIOUS_TARGET" && -d "$PREVIOUS_TARGET" ]]; then
    ln -sfn "$PREVIOUS_TARGET" "$CURRENT_LINK.rollback"
    mv -Tf "$CURRENT_LINK.rollback" "$CURRENT_LINK"
  fi
  if [[ -n "$SERVICE_BACKUP" && -f "$SERVICE_BACKUP" ]]; then cp -a "$SERVICE_BACKUP" "$SERVICE_FILE"; fi
  if (( AUTH_REPLACED )); then
    if [[ -n "$AUTH_BACKUP" && -f "$AUTH_BACKUP" ]]; then cp -a "$AUTH_BACKUP" "$CONFIG_DIR/auth.json"; else rm -f "$CONFIG_DIR/auth.json"; fi
  fi
  systemctl daemon-reload
  (( OLD_SERVICE_ACTIVE )) && systemctl start "$SERVICE_NAME" 2>/dev/null || true
  (( OLD_LEGACY_ACTIVE )) && systemctl start f7010u-gateway 2>/dev/null || true
}

info "全部预检通过，正在原子切换 Web 面板；底层服务保持运行…"
systemctl stop "$SERVICE_NAME" 2>/dev/null || true
systemctl stop f7010u-gateway 2>/dev/null || true

if (( AUTH_COMPATIBLE == 0 )); then
  install -m 0600 "$TMP_DIR/new-auth/auth.json" "$CONFIG_DIR/auth.json"
  AUTH_REPLACED=1
fi

ln -sfn "$RELEASE_DIR" "$CURRENT_LINK.new"
mv -Tf "$CURRENT_LINK.new" "$CURRENT_LINK"
install -m 0644 "$TMP_DIR/iwan-gateway.service" "$SERVICE_FILE"
systemctl daemon-reload
systemctl enable --now "$SERVICE_NAME" >/dev/null

FINAL_OK=0
for _ in $(seq 1 30); do
  if curl -fsS --max-time 2 "http://127.0.0.1:${PANEL_PORT}/healthz" | grep -q '"ok": true'; then
    FINAL_OK=1; break
  fi
  sleep 0.3
done
if (( FINAL_OK == 0 )); then
  journalctl -u "$SERVICE_NAME" -n 100 --no-pager || true
  rollback
  die "正式健康检查失败，旧面板和旧账号已恢复。"
fi

CURRENT_REAL="$(readlink -f "$CURRENT_LINK")"
mapfile -t RELEASE_DIRS < <(find "$RELEASES_DIR" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' | sort -nr | awk '{print $2}')
KEPT_OLD=0
for DIR in "${RELEASE_DIRS[@]}"; do
  [[ "$DIR" == "$CURRENT_REAL" ]] && continue
  KEPT_OLD=$((KEPT_OLD+1))
  (( KEPT_OLD > 1 )) && rm -rf "$DIR"
done

systemctl disable f7010u-gateway sing-box-panel 2>/dev/null || true
if command -v ufw >/dev/null 2>&1; then ufw allow "${PANEL_PORT}/tcp" >/dev/null || true; fi
PUBLIC_IP="$(curl -4fsS --connect-timeout 3 https://api.ipify.org 2>/dev/null || true)"
info "iWAN Gateway v${VERSION} Web 面板安装/升级完成。"
printf '\n访问地址：http://%s:%s\n' "${PUBLIC_IP:-你的VPS公网IP}" "$PANEL_PORT"
printf '底层服务：没有安装、覆盖或重启 sing-box / mosdns。\n'
printf '验证链：GitHub API Blob → Python 语法 → 内置自检 → 登录测试 → 配置采样 → 正式健康检查。\n'
printf '\n检查命令：systemctl status iwan-gateway --no-pager\n'
