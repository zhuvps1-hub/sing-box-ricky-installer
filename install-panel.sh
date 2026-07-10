#!/usr/bin/env bash
set -Eeuo pipefail

BASE_URL="https://raw.githubusercontent.com/zhuvps1-hub/sing-box-ricky-installer/main"
PANEL_DIR="/opt/sing-box-panel"
PANEL_CONFIG_DIR="/etc/sing-box-panel"
PANEL_SERVICE="/etc/systemd/system/sing-box-panel.service"
PANEL_PORT="${PANEL_PORT:-8088}"
PANEL_USER="${PANEL_USER:-admin}"

info(){ printf '\033[1;32m[信息]\033[0m %s\n' "$*"; }
warn(){ printf '\033[1;33m[提示]\033[0m %s\n' "$*"; }
die(){ printf '\033[1;31m[错误]\033[0m %s\n' "$*" >&2; exit 1; }

[[ ${EUID} -eq 0 ]] || die "请使用 root 用户运行。"
command -v systemctl >/dev/null 2>&1 || die "系统未使用 systemd。"
[[ -x /usr/local/bin/sing-box ]] || die "未找到 /usr/local/bin/sing-box，请先安装 sing-box。"
[[ -f /etc/sing-box/config.json ]] || die "未找到 /etc/sing-box/config.json。"

if command -v apt-get >/dev/null 2>&1; then
  apt-get update -y
  DEBIAN_FRONTEND=noninteractive apt-get install -y python3 ca-certificates curl gzip coreutils
elif command -v dnf >/dev/null 2>&1; then
  dnf install -y python3 ca-certificates curl gzip coreutils
elif command -v yum >/dev/null 2>&1; then
  yum install -y python3 ca-certificates curl gzip coreutils
else
  command -v python3 >/dev/null 2>&1 && command -v curl >/dev/null 2>&1 && command -v gzip >/dev/null 2>&1 && command -v base64 >/dev/null 2>&1 || die "请先安装 python3、curl、gzip 和 base64。"
fi

if [[ -t 0 ]]; then
  read -r -p "面板登录用户名 [${PANEL_USER}]：" input_user
  PANEL_USER="${input_user:-$PANEL_USER}"
  read -r -p "面板端口 [${PANEL_PORT}]：" input_port
  PANEL_PORT="${input_port:-$PANEL_PORT}"
fi
[[ "$PANEL_PORT" =~ ^[0-9]+$ ]] && (( PANEL_PORT >= 1 && PANEL_PORT <= 65535 )) || die "面板端口无效。"

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

mkdir -p "$PANEL_DIR" "$PANEL_CONFIG_DIR" /etc/sing-box/backups
chmod 700 "$PANEL_CONFIG_DIR"
info "下载 Web 管理面板…"
PAYLOAD="$PANEL_DIR/panel.py.gz.b64"
: > "$PAYLOAD"
for part in 00 01 02; do
  curl -fL --retry 3 --connect-timeout 15 "$BASE_URL/panel.py.gz.b64.part${part}" >> "$PAYLOAD"
done
base64 -d "$PAYLOAD" | gzip -dc > "$PANEL_DIR/panel.py"
rm -f "$PAYLOAD"
echo "f7451d8cca416c384a4abe786cddeb6d0998333b4799dc58112467046cb19f04  $PANEL_DIR/panel.py" | sha256sum -c - >/dev/null || die "面板文件完整性校验失败。"
chmod 755 "$PANEL_DIR/panel.py"
python3 -m py_compile "$PANEL_DIR/panel.py" || die "面板程序语法检查失败。"

PANEL_ADMIN_USER="$PANEL_USER" PANEL_ADMIN_PASSWORD="$PANEL_PASSWORD" \
  python3 "$PANEL_DIR/panel.py" --init-auth
python3 "$PANEL_DIR/panel.py" --sync-from-config
chmod 600 /etc/sing-box/panel.json "$PANEL_CONFIG_DIR/auth.json"

cat > "$PANEL_SERVICE" <<EOF
[Unit]
Description=sing-box iWAN Web Panel
After=network-online.target sing-box.service
Wants=network-online.target

[Service]
Type=simple
User=root
Group=root
ExecStart=/usr/bin/python3 ${PANEL_DIR}/panel.py --host 0.0.0.0 --port ${PANEL_PORT}
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true

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
  warn "未检测到 UFW/firewalld，请自行确认防火墙放行 ${PANEL_PORT}/TCP。"
fi

systemctl daemon-reload
systemctl enable --now sing-box-panel
sleep 2
systemctl is-active --quiet sing-box-panel || {
  journalctl -u sing-box-panel -n 50 --no-pager || true
  die "Web 面板启动失败。"
}

info "Web 面板安装完成。"
printf '\n访问地址：http://你的VPS公网IP:%s\n' "$PANEL_PORT"
printf '登录用户：%s\n' "$PANEL_USER"
printf '\n请同时在云服务商安全组放行 %s/TCP。\n' "$PANEL_PORT"
printf '为了安全，建议安全组只允许你自己的 IP 访问该端口。\n'
printf '\n常用命令：\n'
printf '  面板状态：systemctl status sing-box-panel --no-pager\n'
printf '  面板日志：journalctl -u sing-box-panel -f\n'
printf '  重启面板：systemctl restart sing-box-panel\n'
