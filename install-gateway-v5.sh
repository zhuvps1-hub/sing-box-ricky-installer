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

info(){ printf '\033[1;32m[信息]\033[0m %s\n' "$*"; }
warn(){ printf '\033[1;33m[提示]\033[0m %s\n' "$*"; }
die(){ printf '\033[1;31m[错误]\033[0m %s\n' "$*" >&2; exit 1; }

[[ ${EUID} -eq 0 ]] || die "请使用 root 用户运行。"
command -v systemctl >/dev/null 2>&1 || die "系统未使用 systemd。"

install_deps(){
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update -y
    DEBIAN_FRONTEND=noninteractive apt-get install -y python3 ca-certificates curl iproute2 tar xz-utils unzip gzip
  elif command -v dnf >/dev/null 2>&1; then
    dnf install -y python3 ca-certificates curl iproute tar xz unzip gzip
  elif command -v yum >/dev/null 2>&1; then
    yum install -y python3 ca-certificates curl iproute tar xz unzip gzip
  else
    command -v python3 >/dev/null 2>&1 && command -v curl >/dev/null 2>&1 || die "请先安装 python3 和 curl。"
  fi
}

install_mosdns(){
  [[ "$SKIP_MOSDNS" == "1" ]] && { warn "已按 SKIP_MOSDNS=1 跳过 mosdns。"; return; }
  if ! command -v mosdns >/dev/null 2>&1; then
    local arch release_json url tmp asset bin
    case "$(uname -m)" in
      x86_64|amd64) arch="amd64" ;;
      aarch64|arm64) arch="arm64" ;;
      *) warn "mosdns 暂不支持当前架构：$(uname -m)"; return ;;
    esac
    info "安装官方 mosdns v5…"
    release_json="$(curl -fsSL --retry 3 https://api.github.com/repos/IrineSistiana/mosdns/releases/latest)" || die "无法读取 mosdns 发布信息。"
    url="$(printf '%s' "$release_json" | ARCH="$arch" python3 -c 'import json,os,sys; d=json.load(sys.stdin); a=os.environ["ARCH"]; assets=d.get("assets",[]); cand=[x["browser_download_url"] for x in assets if "linux" in x.get("name","").lower() and a in x.get("name","").lower() and (x.get("name","").lower().endswith(".zip") or x.get("name","").lower().endswith(".tar.gz"))]; print(cand[0] if cand else "")')"
    [[ -n "$url" ]] || die "没有找到 mosdns Linux ${arch} 安装包。"
    tmp="$(mktemp -d)"; asset="$tmp/pkg"
    curl -fL --retry 3 -o "$asset" "$url"
    mkdir -p "$tmp/out"
    if [[ "$url" == *.zip ]]; then unzip -q "$asset" -d "$tmp/out"; else tar -xzf "$asset" -C "$tmp/out"; fi
    bin="$(find "$tmp/out" -type f -name mosdns | head -n1)"
    [[ -n "$bin" ]] || die "mosdns 安装包结构无法识别。"
    install -m 0755 "$bin" /usr/local/bin/mosdns
    rm -rf "$tmp"
  fi

  mkdir -p /etc/mosdns /etc/mosdns/backups /etc/mosdns/rules
  if [[ ! -f /etc/mosdns/config.yaml ]]; then
    cat > /etc/mosdns/config.yaml <<'EOF'
log:
  level: info
  file: "/var/log/mosdns.log"
api:
  http: "127.0.0.1:9091"
plugins:
  - tag: cache
    type: cache
    args:
      size: 10240
      lazy_cache_ttl: 86400
      dump_file: "/etc/mosdns/cache.dump"
      dump_interval: 600
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
EOF
  fi

  if [[ ! -f /etc/systemd/system/mosdns.service && ! -f /lib/systemd/system/mosdns.service && ! -f /usr/lib/systemd/system/mosdns.service ]]; then
    cat > /etc/systemd/system/mosdns.service <<'EOF'
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
EOF
  fi

  if mosdns check -c /etc/mosdns/config.yaml >/dev/null 2>&1; then
    systemctl daemon-reload
    systemctl enable --now mosdns >/dev/null 2>&1 || warn "mosdns 服务未能自动启动，请在面板日志中检查。"
    info "mosdns 已安装并接入面板。"
  else
    warn "mosdns 配置校验未通过，已保留原配置，面板仍可修改。"
  fi
}

if [[ ${1:-} == "uninstall" ]]; then
  systemctl disable --now "$SERVICE_NAME" 2>/dev/null || true
  systemctl disable --now f7010u-gateway 2>/dev/null || true
  systemctl disable --now sing-box-panel 2>/dev/null || true
  rm -f "$SERVICE_FILE" /etc/systemd/system/f7010u-gateway.service /etc/systemd/system/sing-box-panel.service
  rm -rf "$APP_DIR" /opt/f7010u-gateway /opt/sing-box-panel
  systemctl daemon-reload
  warn "已保留 $CONFIG_DIR、$DATA_DIR、mosdns 和 sing-box 配置。"
  info "iWAN Gateway 面板已卸载。"
  exit 0
fi

install_deps

if [[ ! -x /usr/local/bin/sing-box || ! -f /etc/sing-box/config.json ]]; then
  warn "未检测到完整 sing-box iWAN 环境，先运行 sing-box 安装器。"
  bash <(curl -fsSL "$BASE_URL/install.sh")
fi
[[ -x /usr/local/bin/sing-box ]] || die "sing-box 安装失败。"
[[ -f /etc/sing-box/config.json ]] || die "未找到 /etc/sing-box/config.json。"

install_mosdns

if [[ -t 0 ]]; then
  read -r -p "Web 面板端口 [${PANEL_PORT}]：" input_port
  PANEL_PORT="${input_port:-$PANEL_PORT}"
fi
[[ "$PANEL_PORT" =~ ^[0-9]+$ ]] && (( PANEL_PORT >= 1 && PANEL_PORT <= 65535 )) || die "面板端口无效。"

mkdir -p "$APP_DIR" "$CONFIG_DIR" "$DATA_DIR" /etc/sing-box/backups /etc/mosdns/backups
chmod 700 "$CONFIG_DIR" "$DATA_DIR" /etc/sing-box/backups

if [[ -f /etc/f7010u-gateway/auth.json && ! -f "$CONFIG_DIR/auth.json" ]]; then
  cp -a /etc/f7010u-gateway/auth.json "$CONFIG_DIR/auth.json"
fi
if [[ -f /etc/f7010u-gateway/state.json && ! -f "$CONFIG_DIR/state.json" ]]; then
  cp -a /etc/f7010u-gateway/state.json "$CONFIG_DIR/state.json"
fi
if [[ -f /var/lib/f7010u-gateway/gateway.db && ! -f "$DATA_DIR/gateway.db" ]]; then
  cp -a /var/lib/f7010u-gateway/gateway.db "$DATA_DIR/gateway.db"
fi

AUTH_EXISTS=0
[[ -f "$CONFIG_DIR/auth.json" ]] && AUTH_EXISTS=1
if (( AUTH_EXISTS == 0 )); then
  if [[ -t 0 ]]; then
    read -r -p "面板登录用户名 [${PANEL_USER}]：" input_user
    PANEL_USER="${input_user:-$PANEL_USER}"
  fi
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
else
  info "检测到已有面板账号，将保留原登录信息。"
fi

info "停止旧版面板服务…"
systemctl disable --now sing-box-panel 2>/dev/null || true
systemctl disable --now f7010u-gateway 2>/dev/null || true
systemctl stop "$SERVICE_NAME" 2>/dev/null || true

info "下载 iWAN Gateway v5 轻量版…"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
for part in 00 01 02 03 04 05 06 07; do
  curl -fL --retry 3 --connect-timeout 15 \
    "$BASE_URL/gateway-v5.tar.xz.b64.part${part}" \
    -o "$TMP_DIR/part${part}"
done
cat "$TMP_DIR"/part* > "$TMP_DIR/gateway-v5.tar.xz.b64"
base64 -d "$TMP_DIR/gateway-v5.tar.xz.b64" > "$TMP_DIR/gateway-v5.tar.xz"
echo "d876d94f3df0f36f54a305b82edbb9ae8e71ea981fe69edef1f60b04b208f68b  $TMP_DIR/gateway-v5.tar.xz" | sha256sum -c - >/dev/null || die "面板安装包校验失败。"
tar -xJf "$TMP_DIR/gateway-v5.tar.xz" -C "$TMP_DIR"
[[ -f "$TMP_DIR/gateway/app.py" && -f "$TMP_DIR/gateway/index.html" ]] || die "面板安装包结构错误。"
rm -rf "$APP_DIR"
mkdir -p "$APP_DIR"
cp -a "$TMP_DIR/gateway/." "$APP_DIR/"
chmod 755 "$APP_DIR/app.py"
python3 -m py_compile "$APP_DIR/app.py" || die "Python 语法检查失败。"

if (( AUTH_EXISTS == 0 )); then
  PANEL_ADMIN_USER="$PANEL_USER" PANEL_ADMIN_PASSWORD="$PANEL_PASSWORD" python3 "$APP_DIR/app.py" --init-auth
fi
if [[ ! -f "$CONFIG_DIR/state.json" ]]; then
  python3 "$APP_DIR/app.py" --sync-from-config
fi
chmod 600 "$CONFIG_DIR"/*.json 2>/dev/null || true

cat > "$SERVICE_FILE" <<EOF
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
EOF

if command -v ufw >/dev/null 2>&1; then
  ufw allow "${PANEL_PORT}/tcp" >/dev/null || true
elif command -v firewall-cmd >/dev/null 2>&1 && systemctl is-active --quiet firewalld; then
  firewall-cmd --permanent --add-port="${PANEL_PORT}/tcp" >/dev/null
  firewall-cmd --reload >/dev/null
fi

systemctl daemon-reload
systemctl enable --now "$SERVICE_NAME"

for _ in $(seq 1 15); do
  if curl -fsS --max-time 2 "http://127.0.0.1:${PANEL_PORT}/healthz" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
if ! systemctl is-active --quiet "$SERVICE_NAME"; then
  journalctl -u "$SERVICE_NAME" -n 100 --no-pager || true
  die "面板启动失败。"
fi
curl -fsS --max-time 3 "http://127.0.0.1:${PANEL_PORT}/healthz" >/dev/null || {
  journalctl -u "$SERVICE_NAME" -n 100 --no-pager || true
  die "面板健康检查失败。"
}

PUBLIC_IP="$(curl -4fsS --connect-timeout 3 https://api.ipify.org 2>/dev/null || true)"
info "iWAN Gateway v5 轻量版安装完成。"
printf '\n访问地址：http://%s:%s\n' "${PUBLIC_IP:-你的VPS公网IP}" "$PANEL_PORT"
if (( AUTH_EXISTS == 0 )); then printf '登录用户：%s\n' "$PANEL_USER"; fi
printf '\n请在云安全组放行：8000/TCP+UDP 和 %s/TCP。\n' "$PANEL_PORT"
printf '\n主要功能：\n'
printf '  国内网站固定 direct\n'
printf '  Netflix / AI / YouTube / Telegram 可分别自由选择节点\n'
printf '  支持新增、编辑、删除、ss:// 批量导入和 JSON 导入\n'
printf '  浅色 / 深色 / 跟随系统主题，手机端自适应\n'
printf '  智能刷新、状态缓存、按需测速，降低 CPU 和磁盘占用\n'
printf '  保存前自动校验，失败自动回滚\n'
printf '\n常用命令：\n'
printf '  面板状态：systemctl status %s --no-pager\n' "$SERVICE_NAME"
printf '  面板日志：journalctl -u %s -f\n' "$SERVICE_NAME"
printf '  重启面板：systemctl restart %s\n' "$SERVICE_NAME"
printf '  mosdns：systemctl status mosdns --no-pager\n'
