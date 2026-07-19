#!/usr/bin/env bash
set -Eeuo pipefail

REPO="zhuvps1-hub/sing-box-ricky-installer"
VERSION="1.13.13-rickyhao.22"
ARCHIVE="sing-box-${VERSION}-linux-amd64-musl.tar.gz"
CORE_URL="https://github.com/Ricky-Hao/sing-box/releases/download/v${VERSION}/${ARCHIVE}"
APP_DIR="/opt/iwan-gateway"
DATA_DIR="/etc/iwan-gateway"
SB_DIR="/etc/sing-box"
PANEL_PORT="${PANEL_PORT:-8088}"

info(){ printf '\033[1;32m[iWAN]\033[0m %s\n' "$*"; }
die(){ printf '\033[1;31m[错误]\033[0m %s\n' "$*" >&2; exit 1; }
[[ ${EUID} -eq 0 ]] || die "请使用 root 运行"
command -v systemctl >/dev/null || die "系统必须使用 systemd"

if command -v apt-get >/dev/null; then
  apt-get update -y
  DEBIAN_FRONTEND=noninteractive apt-get install -y python3 curl tar ca-certificates
fi

tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
info "安装 sing-box ${VERSION}"
curl -fL --retry 3 -o "$tmp/$ARCHIVE" "$CORE_URL"
tar -xzf "$tmp/$ARCHIVE" -C "$tmp"
bin=$(find "$tmp" -type f -name sing-box | head -n1)
[[ -x "$bin" ]] || die "安装包中未找到 sing-box"
install -m 0755 "$bin" /usr/local/bin/sing-box
sing-box version | grep -q with_iwan || die "当前核心不包含 with_iwan"

info "安装 iWAN Gateway"
rm -rf "$APP_DIR"
mkdir -p "$APP_DIR/app/static" "$DATA_DIR/backups" "$SB_DIR"
for f in app.py static/index.html static/style.css static/app.js; do
  mkdir -p "$APP_DIR/app/$(dirname "$f")"
  curl -fsSL "https://raw.githubusercontent.com/${REPO}/main/app/${f}" -o "$APP_DIR/app/$f"
done
chmod 700 "$DATA_DIR"

cat > /etc/systemd/system/iwan-gateway.service <<EOF
[Unit]
Description=iWAN Gateway lightweight panel
After=network-online.target sing-box.service
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=$APP_DIR/app
Environment=IWAN_DATA=$DATA_DIR
Environment=IWAN_PANEL_PORT=$PANEL_PORT
ExecStart=/usr/bin/python3 $APP_DIR/app/app.py
Restart=always
RestartSec=2
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/sing-box.service <<EOF
[Unit]
Description=sing-box iWAN core
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=$SB_DIR
ExecStart=/usr/local/bin/sing-box run -c $SB_DIR/config.json
ExecReload=/bin/kill -HUP \$MAINPID
Restart=on-failure
RestartSec=2
LimitNOFILE=1048576

[Install]
WantedBy=multi-user.target
EOF

if [[ ! -f "$SB_DIR/config.json" ]]; then
cat > "$SB_DIR/config.json" <<'EOF'
{"log":{"level":"info","timestamp":true},"inbounds":[],"outbounds":[{"type":"direct","tag":"direct"}],"route":{"rules":[],"final":"direct"}}
EOF
fi
chmod 600 "$SB_DIR/config.json"
sing-box check -c "$SB_DIR/config.json"
systemctl daemon-reload
systemctl enable --now sing-box iwan-gateway

if command -v ufw >/dev/null; then ufw allow "$PANEL_PORT/tcp" >/dev/null || true; fi
ip=$(hostname -I 2>/dev/null | awk '{print $1}')
info "安装完成"
printf '面板地址: http://%s:%s\n' "${ip:-服务器IP}" "$PANEL_PORT"
printf '首次账号: admin\n首次密码: admin\n登录后请立即修改密码。\n'
