#!/usr/bin/env bash
set -Eeuo pipefail

BASE_URL="https://raw.githubusercontent.com/zhuvps1-hub/sing-box-ricky-installer/main"
APP_DIR="/opt/f7010u-gateway"
CONFIG_DIR="/etc/f7010u-gateway"
DATA_DIR="/var/lib/f7010u-gateway"
SERVICE_FILE="/etc/systemd/system/f7010u-gateway.service"
PANEL_PORT="${PANEL_PORT:-8088}"
PANEL_USER="${PANEL_USER:-admin}"

info(){ printf '\033[1;32m[信息]\033[0m %s\n' "$*"; }
warn(){ printf '\033[1;33m[提示]\033[0m %s\n' "$*"; }
die(){ printf '\033[1;31m[错误]\033[0m %s\n' "$*" >&2; exit 1; }

[[ ${EUID} -eq 0 ]] || die "请使用 root 用户运行。"
command -v systemctl >/dev/null 2>&1 || die "系统未使用 systemd。"

install_deps(){
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update -y
    DEBIAN_FRONTEND=noninteractive apt-get install -y python3 ca-certificates curl iproute2 tar xz-utils coreutils
    DEBIAN_FRONTEND=noninteractive apt-get install -y nftables || true
  elif command -v dnf >/dev/null 2>&1; then
    dnf install -y python3 ca-certificates curl iproute tar xz coreutils
    dnf install -y nftables || true
  elif command -v yum >/dev/null 2>&1; then
    yum install -y python3 ca-certificates curl iproute tar xz coreutils
    yum install -y nftables || true
  else
    command -v python3 >/dev/null 2>&1 && command -v curl >/dev/null 2>&1 || die "请先安装 python3 和 curl。"
  fi
}

if [[ ${1:-} == "uninstall" ]]; then
  systemctl disable --now f7010u-gateway 2>/dev/null || true
  rm -f "$SERVICE_FILE"
  rm -rf "$APP_DIR"
  systemctl daemon-reload
  warn "已保留 $CONFIG_DIR、$DATA_DIR 和 sing-box 配置。"
  info "Web 管理面板已卸载。"
  exit 0
fi

install_deps

if [[ ! -x /usr/local/bin/sing-box || ! -f /etc/sing-box/config.json ]]; then
  warn "未检测到完整 sing-box iWAN 环境，先运行仓库中的 sing-box 安装器。"
  bash <(curl -fsSL "$BASE_URL/install.sh")
fi

[[ -x /usr/local/bin/sing-box ]] || die "sing-box 安装失败。"
[[ -f /etc/sing-box/config.json ]] || die "未找到 /etc/sing-box/config.json。"

if [[ -t 0 ]]; then
  read -r -p "Web 面板端口 [${PANEL_PORT}]：" input_port
  PANEL_PORT="${input_port:-$PANEL_PORT}"
fi
[[ "$PANEL_PORT" =~ ^[0-9]+$ ]] && (( PANEL_PORT >= 1 && PANEL_PORT <= 65535 )) || die "面板端口无效。"

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

info "停止旧版简易面板（如存在）…"
systemctl disable --now sing-box-panel 2>/dev/null || true

mkdir -p "$APP_DIR/static" "$APP_DIR/templates" "$CONFIG_DIR" "$DATA_DIR" /etc/sing-box/backups /etc/mosdns /usr/local/etc/mosdns
chmod 700 "$CONFIG_DIR" "$DATA_DIR" /etc/sing-box/backups

info "下载 F7010U iWAN Gateway 完整版…"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
for part in 00 01 02 03 04 05 06 07 08 09 10 11; do
  curl -fL --retry 3 --connect-timeout 15 "$BASE_URL/gateway-bundle.tar.xz.b64.part${part}" -o "$TMP_DIR/part${part}"
done
cat "$TMP_DIR"/part* > "$TMP_DIR/gateway-bundle.tar.xz.b64"
base64 -d "$TMP_DIR/gateway-bundle.tar.xz.b64" > "$TMP_DIR/gateway-bundle.tar.xz"
echo "230b0372f2ead87ac469ac4249a9ada134efac5d3c4dd41418e7206ff233223c  $TMP_DIR/gateway-bundle.tar.xz" | sha256sum -c - >/dev/null || die "面板安装包校验失败。"
tar -xJf "$TMP_DIR/gateway-bundle.tar.xz" -C "$TMP_DIR"
[[ -f "$TMP_DIR/gateway/app.py" && -f "$TMP_DIR/gateway/core.py" ]] || die "面板安装包结构错误。"
rm -rf "$APP_DIR"
mkdir -p "$APP_DIR"
cp -a "$TMP_DIR/gateway/." "$APP_DIR/"
chmod 755 "$APP_DIR/app.py" "$APP_DIR/core.py"

python3 -m py_compile "$APP_DIR/app.py" "$APP_DIR/core.py" || die "Python 语法检查失败。"

if (( AUTH_EXISTS == 0 )); then
  PANEL_ADMIN_USER="$PANEL_USER" PANEL_ADMIN_PASSWORD="$PANEL_PASSWORD" \
    python3 "$APP_DIR/app.py" --init-auth
fi

cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=F7010U iWAN Gateway Web Panel
After=network-online.target sing-box.service
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
ProtectSystem=full
ReadWritePaths=/etc/f7010u-gateway /var/lib/f7010u-gateway /etc/sing-box /etc/mosdns /usr/local/etc/mosdns /run /var/log

[Install]
WantedBy=multi-user.target
EOF

if command -v ufw >/dev/null 2>&1; then
  ufw allow "${PANEL_PORT}/tcp" >/dev/null || true
  info "已尝试通过 UFW 放行 ${PANEL_PORT}/TCP。"
elif command -v firewall-cmd >/dev/null 2>&1 && systemctl is-active --quiet firewalld; then
  firewall-cmd --permanent --add-port="${PANEL_PORT}/tcp" >/dev/null
  firewall-cmd --reload >/dev/null
  info "已通过 firewalld 放行 ${PANEL_PORT}/TCP。"
else
  warn "未检测到 UFW/firewalld，请自行确认防火墙和安全组放行 ${PANEL_PORT}/TCP。"
fi

systemctl daemon-reload
systemctl enable --now f7010u-gateway
sleep 2
if ! systemctl is-active --quiet f7010u-gateway; then
  journalctl -u f7010u-gateway -n 80 --no-pager || true
  die "面板启动失败。"
fi

if command -v curl >/dev/null 2>&1; then
  curl -fsS "http://127.0.0.1:${PANEL_PORT}/healthz" >/dev/null || die "面板健康检查失败。"
fi

PUBLIC_IP="$(curl -4fsS --connect-timeout 3 https://api.ipify.org 2>/dev/null || true)"
info "F7010U iWAN Gateway 安装完成。"
printf '\n访问地址：http://%s:%s\n' "${PUBLIC_IP:-你的VPS公网IP}" "$PANEL_PORT"
if (( AUTH_EXISTS == 0 )); then printf '登录用户：%s\n' "$PANEL_USER"; fi
printf '\n请在云服务商安全组放行 %s/TCP，建议只允许你自己的公网 IP。\n' "$PANEL_PORT"
printf '\n常用命令：\n'
printf '  面板状态：systemctl status f7010u-gateway --no-pager\n'
printf '  面板日志：journalctl -u f7010u-gateway -f\n'
printf '  重启面板：systemctl restart f7010u-gateway\n'
printf '  升级面板：bash <(curl -fsSL %s/install-gateway.sh)\n' "$BASE_URL"
