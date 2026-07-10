#!/usr/bin/env bash
set -Eeuo pipefail

BASE_URL="https://raw.githubusercontent.com/zhuvps1-hub/sing-box-ricky-installer/main"
APP_DIR="/opt/iwan-gateway"
CONFIG_DIR="/etc/iwan-gateway"
DATA_DIR="/var/lib/iwan-gateway"
SERVICE_NAME="iwan-gateway"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
PANEL_PORT="${PANEL_PORT:-8088}"
PANEL_USER="${PANEL_USER:-admin}"
SKIP_MOSDNS="${SKIP_MOSDNS:-0}"
PACKAGE_SHA256="fa6dae6d65ba5f9bd2b96b6ee1d9ae8d4139b91148a2b95bfe8ef48197e402a3"
PARTS=(00 01 02 03)
TMP_DIR=""
STAGE_DIR=""
BACKUP_DIR=""
SERVICE_BACKUP=""
OLD_SERVICE_ACTIVE=0
OLD_F7010_ACTIVE=0

info(){ printf '\033[1;32m[信息]\033[0m %s\n' "$*"; }
warn(){ printf '\033[1;33m[提示]\033[0m %s\n' "$*"; }
die(){ printf '\033[1;31m[错误]\033[0m %s\n' "$*" >&2; exit 1; }
cleanup(){ [[ -n "$TMP_DIR" && -d "$TMP_DIR" ]] && rm -rf "$TMP_DIR"; [[ -n "$STAGE_DIR" && -d "$STAGE_DIR" ]] && rm -rf "$STAGE_DIR"; }
trap cleanup EXIT

[[ ${EUID} -eq 0 ]] || die "请使用 root 用户运行。"
command -v systemctl >/dev/null 2>&1 || die "系统未使用 systemd。"

install_deps(){
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update -y
    DEBIAN_FRONTEND=noninteractive apt-get install -y python3 ca-certificates curl tar xz-utils unzip
  elif command -v dnf >/dev/null 2>&1; then
    dnf install -y python3 ca-certificates curl tar xz unzip
  elif command -v yum >/dev/null 2>&1; then
    yum install -y python3 ca-certificates curl tar xz unzip
  else
    command -v python3 >/dev/null 2>&1 && command -v curl >/dev/null 2>&1 && command -v xz >/dev/null 2>&1 || die "请先安装 python3、curl 和 xz。"
  fi
}

install_mosdns(){
  [[ "$SKIP_MOSDNS" == "1" ]] && return 0
  if command -v mosdns >/dev/null 2>&1; then
    return 0
  fi
  local arch release_json url asset bin out
  case "$(uname -m)" in
    x86_64|amd64) arch="amd64" ;;
    aarch64|arm64) arch="arm64" ;;
    *) warn "mosdns 暂不支持当前架构，已跳过。"; return 0 ;;
  esac
  info "未检测到 mosdns，正在安装官方 v5…"
  release_json="$(curl -fsSL --retry 3 https://api.github.com/repos/IrineSistiana/mosdns/releases/latest)" || { warn "读取 mosdns 版本失败，已跳过。"; return 0; }
  url="$(printf '%s' "$release_json" | ARCH="$arch" python3 -c 'import json,os,sys; d=json.load(sys.stdin); a=os.environ["ARCH"]; c=[x["browser_download_url"] for x in d.get("assets",[]) if "linux" in x.get("name","").lower() and a in x.get("name","").lower() and (x.get("name","").lower().endswith(".zip") or x.get("name","").lower().endswith(".tar.gz"))]; print(c[0] if c else "")')"
  [[ -n "$url" ]] || { warn "未找到 mosdns 安装包，已跳过。"; return 0; }
  asset="$TMP_DIR/mosdns.pkg"; out="$TMP_DIR/mosdns-out"; mkdir -p "$out"
  curl -fL --retry 3 -o "$asset" "$url" || { warn "mosdns 下载失败，已跳过。"; return 0; }
  if [[ "$url" == *.zip ]]; then unzip -q "$asset" -d "$out"; else tar -xzf "$asset" -C "$out"; fi
  bin="$(find "$out" -type f -name mosdns | head -n1)"
  [[ -n "$bin" ]] || { warn "mosdns 安装包无法识别，已跳过。"; return 0; }
  install -m 0755 "$bin" /usr/local/bin/mosdns
}

ensure_mosdns_service(){
  command -v mosdns >/dev/null 2>&1 || return 0
  mkdir -p /etc/mosdns /etc/mosdns/backups
  if [[ ! -f /etc/mosdns/config.yaml ]]; then
    cat > /etc/mosdns/config.yaml <<'MOSDNS'
log:
  level: info
api:
  http: "127.0.0.1:9091"
plugins:
  - tag: cache
    type: cache
    args:
      size: 10240
      lazy_cache_ttl: 86400
  - tag: upstream
    type: forward
    args:
      concurrent: 2
      upstreams:
        - addr: "223.5.5.5"
        - addr: "119.29.29.29"
        - addr: "https://1.1.1.1/dns-query"
  - tag: main
    type: sequence
    args:
      - exec: $cache
      - matches: has_resp
        exec: accept
      - exec: $upstream
  - tag: udp_server
    type: udp_server
    args:
      entry: main
      listen: "127.0.0.1:5335"
  - tag: tcp_server
    type: tcp_server
    args:
      entry: main
      listen: "127.0.0.1:5335"
MOSDNS
  fi
  if ! systemctl cat mosdns >/dev/null 2>&1; then
    cat > /etc/systemd/system/mosdns.service <<'UNIT'
[Unit]
Description=mosdns v5 DNS Service
After=network-online.target
Wants=network-online.target
[Service]
Type=simple
ExecStart=/usr/local/bin/mosdns start -c /etc/mosdns/config.yaml
Restart=on-failure
RestartSec=3
LimitNOFILE=65536
[Install]
WantedBy=multi-user.target
UNIT
  fi
  if mosdns check -c /etc/mosdns/config.yaml >/dev/null 2>&1; then
    systemctl daemon-reload
    systemctl enable --now mosdns >/dev/null 2>&1 || warn "mosdns 未自动启动，可稍后在面板查看日志。"
  else
    warn "mosdns 配置校验失败，已保留原配置。"
  fi
}

rollback(){
  warn "新版启动失败，正在恢复旧面板…"
  systemctl stop "$SERVICE_NAME" 2>/dev/null || true
  rm -rf "$APP_DIR"
  if [[ -n "$BACKUP_DIR" && -d "$BACKUP_DIR" ]]; then mv "$BACKUP_DIR" "$APP_DIR"; BACKUP_DIR=""; fi
  if [[ -n "$SERVICE_BACKUP" && -f "$SERVICE_BACKUP" ]]; then cp -a "$SERVICE_BACKUP" "$SERVICE_FILE"; fi
  systemctl daemon-reload
  if (( OLD_SERVICE_ACTIVE )); then systemctl start "$SERVICE_NAME" 2>/dev/null || true; fi
  if (( OLD_F7010_ACTIVE )); then systemctl start f7010u-gateway 2>/dev/null || true; fi
}

if [[ ${1:-} == "uninstall" ]]; then
  systemctl disable --now "$SERVICE_NAME" 2>/dev/null || true
  rm -f "$SERVICE_FILE"
  rm -rf "$APP_DIR"
  systemctl daemon-reload
  warn "已保留 $CONFIG_DIR、$DATA_DIR、sing-box 和 mosdns 配置。"
  exit 0
fi

install_deps
TMP_DIR="$(mktemp -d)"

if [[ ! -x /usr/local/bin/sing-box || ! -f /etc/sing-box/config.json ]]; then
  warn "未检测到 sing-box iWAN，先安装核心。"
  bash <(curl -fsSL "$BASE_URL/install.sh")
fi
[[ -x /usr/local/bin/sing-box && -f /etc/sing-box/config.json ]] || die "sing-box 环境不完整。"

install_mosdns
ensure_mosdns_service

if [[ -t 0 ]]; then
  read -r -p "Web 面板端口 [${PANEL_PORT}]：" input_port
  PANEL_PORT="${input_port:-$PANEL_PORT}"
fi
[[ "$PANEL_PORT" =~ ^[0-9]+$ ]] && (( PANEL_PORT >= 1 && PANEL_PORT <= 65535 )) || die "面板端口无效。"

mkdir -p "$CONFIG_DIR" "$DATA_DIR" /etc/sing-box/backups /etc/mosdns/backups
chmod 700 "$CONFIG_DIR" "$DATA_DIR" /etc/sing-box/backups
if [[ -f /etc/f7010u-gateway/auth.json && ! -f "$CONFIG_DIR/auth.json" ]]; then cp -a /etc/f7010u-gateway/auth.json "$CONFIG_DIR/auth.json"; fi
if [[ -f /etc/f7010u-gateway/state.json && ! -f "$CONFIG_DIR/state.json" ]]; then cp -a /etc/f7010u-gateway/state.json "$CONFIG_DIR/state.json"; fi

AUTH_EXISTS=0
[[ -f "$CONFIG_DIR/auth.json" ]] && AUTH_EXISTS=1
if (( AUTH_EXISTS == 0 )); then
  if [[ -t 0 ]]; then read -r -p "面板登录用户名 [${PANEL_USER}]：" input_user; PANEL_USER="${input_user:-$PANEL_USER}"; fi
  if [[ -z "${PANEL_PASSWORD:-}" ]]; then
    [[ -t 0 ]] || die "非交互安装请设置 PANEL_PASSWORD 环境变量。"
    while true; do
      read -r -s -p "设置面板登录密码（至少 8 位）：" PANEL_PASSWORD; printf '\n'
      read -r -s -p "再次输入面板密码：" PANEL_PASSWORD_2; printf '\n'
      [[ ${#PANEL_PASSWORD} -ge 8 ]] || { warn "密码至少 8 位。"; continue; }
      [[ "$PANEL_PASSWORD" == "$PANEL_PASSWORD_2" ]] || { warn "两次密码不一致。"; continue; }
      break
    done
  fi
fi

# 先完整下载、校验、解压、语法检查；此阶段绝不停止旧面板。
info "下载并校验 iWAN Gateway v5.1…"
for part in "${PARTS[@]}"; do
  curl -fL --retry 3 --connect-timeout 15 "$BASE_URL/gateway-v51.tar.xz.b64.part${part}" -o "$TMP_DIR/part${part}" || die "安装包分片 ${part} 下载失败；旧面板未受影响。"
done
cat "$TMP_DIR"/part* > "$TMP_DIR/package.b64"
base64 -d "$TMP_DIR/package.b64" > "$TMP_DIR/package.tar.xz" || die "安装包解码失败；旧面板未受影响。"
echo "$PACKAGE_SHA256  $TMP_DIR/package.tar.xz" | sha256sum -c - >/dev/null || die "安装包校验失败；旧面板未受影响。"
tar -xJf "$TMP_DIR/package.tar.xz" -C "$TMP_DIR"
[[ -f "$TMP_DIR/gateway/app.py" && -f "$TMP_DIR/gateway/index.html" && -f "$TMP_DIR/gateway/app.css" && -f "$TMP_DIR/gateway/app.js" ]] || die "安装包结构错误；旧面板未受影响。"
python3 -m py_compile "$TMP_DIR/gateway/app.py" || die "新版语法检查失败；旧面板未受影响。"
STAGE_DIR="${APP_DIR}.new.$$"
rm -rf "$STAGE_DIR"; mkdir -p "$STAGE_DIR"; cp -a "$TMP_DIR/gateway/." "$STAGE_DIR/"
chmod 755 "$STAGE_DIR/app.py"

if (( AUTH_EXISTS == 0 )); then PANEL_ADMIN_USER="$PANEL_USER" PANEL_ADMIN_PASSWORD="$PANEL_PASSWORD" python3 "$STAGE_DIR/app.py" --init-auth; fi
if [[ ! -f "$CONFIG_DIR/state.json" ]]; then python3 "$STAGE_DIR/app.py" --sync-from-config; fi
chmod 600 "$CONFIG_DIR"/*.json 2>/dev/null || true

cat > "$TMP_DIR/iwan-gateway.service" <<EOF_UNIT
[Unit]
Description=iWAN Gateway Web Panel
After=network-online.target sing-box.service mosdns.service
Wants=network-online.target
[Service]
Type=simple
User=root
Group=root
WorkingDirectory=$APP_DIR
ExecStart=/usr/bin/python3 $APP_DIR/app.py --host 0.0.0.0 --port $PANEL_PORT
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
LimitNOFILE=65536
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONDONTWRITEBYTECODE=1
[Install]
WantedBy=multi-user.target
EOF_UNIT

systemctl is-active --quiet "$SERVICE_NAME" && OLD_SERVICE_ACTIVE=1 || true
systemctl is-active --quiet f7010u-gateway && OLD_F7010_ACTIVE=1 || true
[[ -f "$SERVICE_FILE" ]] && { SERVICE_BACKUP="$TMP_DIR/service.backup"; cp -a "$SERVICE_FILE" "$SERVICE_BACKUP"; }

# 只有预检全部通过后才进入几秒钟的切换窗口。
info "预检通过，正在无损切换新版…"
systemctl stop "$SERVICE_NAME" 2>/dev/null || true
systemctl stop f7010u-gateway 2>/dev/null || true
BACKUP_DIR="${APP_DIR}.previous.$$"
rm -rf "$BACKUP_DIR"
[[ -d "$APP_DIR" ]] && mv "$APP_DIR" "$BACKUP_DIR"
mv "$STAGE_DIR" "$APP_DIR"; STAGE_DIR=""
install -m 0644 "$TMP_DIR/iwan-gateway.service" "$SERVICE_FILE"
systemctl daemon-reload
systemctl enable "$SERVICE_NAME" >/dev/null
systemctl start "$SERVICE_NAME"

HEALTH_OK=0
for _ in $(seq 1 15); do
  if curl -fsS --max-time 2 "http://127.0.0.1:${PANEL_PORT}/healthz" >/dev/null 2>&1; then HEALTH_OK=1; break; fi
  sleep 1
done
if (( HEALTH_OK == 0 )); then
  journalctl -u "$SERVICE_NAME" -n 80 --no-pager || true
  rollback
  die "新版健康检查失败，已恢复旧面板。"
fi

rm -rf "$BACKUP_DIR"; BACKUP_DIR=""
if command -v ufw >/dev/null 2>&1; then ufw allow "${PANEL_PORT}/tcp" >/dev/null || true; fi
PUBLIC_IP="$(curl -4fsS --connect-timeout 3 https://api.ipify.org 2>/dev/null || true)"
info "iWAN Gateway v5.1 升级完成。"
printf '\n访问地址：http://%s:%s\n' "${PUBLIC_IP:-你的VPS公网IP}" "$PANEL_PORT"
printf '主题：深色 / 浅色 / 跟随系统\n'
printf '状态：旧面板仅在预检完成后短暂停止；升级失败会自动恢复。\n'
printf '\n检查：systemctl status iwan-gateway --no-pager\n'
