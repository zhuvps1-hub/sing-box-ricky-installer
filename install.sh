#!/usr/bin/env bash
set -Eeuo pipefail

VERSION="1.13.13-rickyhao.22"
ARCHIVE="sing-box-${VERSION}-linux-amd64.tar.gz"
REPO="zhuvps1-hub/sing-box-ricky-installer"
DOWNLOAD_URL="https://github.com/Ricky-Hao/sing-box/releases/download/v${VERSION}/${ARCHIVE}"
EXPECTED_SHA256="d650de9b0cb3852ec4e878ae2291631f0891d3b71fdd1998745c20ea8780bc1f"
INSTALL_DIR="/usr/local/lib/sing-box"
BIN_LINK="/usr/local/bin/sing-box"
CONFIG_DIR="/etc/sing-box"
CONFIG_FILE="${CONFIG_DIR}/config.json"
SERVICE_FILE="/etc/systemd/system/sing-box.service"
PANEL_PORT="${PANEL_PORT:-8088}"
TMP_DIR=""

info() { printf '\033[1;32m[信息]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[提示]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[错误]\033[0m %s\n' "$*" >&2; exit 1; }
cleanup() { [[ -n "${TMP_DIR}" && -d "${TMP_DIR}" ]] && rm -rf "${TMP_DIR}"; }
trap cleanup EXIT

install_deps() {
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update -y
    DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates curl tar python3
  elif command -v dnf >/dev/null 2>&1; then
    dnf install -y ca-certificates curl tar python3
  elif command -v yum >/dev/null 2>&1; then
    yum install -y ca-certificates curl tar python3
  else
    command -v curl >/dev/null 2>&1 && command -v tar >/dev/null 2>&1 && command -v python3 >/dev/null 2>&1 || \
      die "请先安装 curl、tar 和 Python 3.10+。"
  fi
  command -v sha256sum >/dev/null 2>&1 || die "系统缺少 sha256sum。"
}

open_panel_port() {
  if command -v ufw >/dev/null 2>&1; then
    ufw allow "${PANEL_PORT}/tcp" >/dev/null || true
    info "已尝试通过 UFW 放行 ${PANEL_PORT}/TCP。"
  elif command -v firewall-cmd >/dev/null 2>&1 && systemctl is-active --quiet firewalld; then
    firewall-cmd --permanent --add-port="${PANEL_PORT}/tcp" >/dev/null
    firewall-cmd --reload >/dev/null
    info "已通过 firewalld 放行 ${PANEL_PORT}/TCP。"
  else
    warn "未检测到 UFW/firewalld，请在云安全组中放行 ${PANEL_PORT}/TCP。"
  fi
}

uninstall_all() {
  info "正在卸载 Web 面板和 sing-box 程序…"
  bash <(curl -fsSL "https://raw.githubusercontent.com/${REPO}/main/install-web.sh?ts=$(date +%s)") uninstall || true
  systemctl disable --now sing-box 2>/dev/null || true
  rm -f "${SERVICE_FILE}" "${BIN_LINK}"
  rm -rf "${INSTALL_DIR}"
  systemctl daemon-reload
  warn "已保留 ${CONFIG_DIR}、/etc/iwan-gateway 和相关备份。"
}

[[ ${EUID} -eq 0 ]] || die "请使用 root 用户运行。"
[[ $(uname -s) == "Linux" ]] || die "仅支持 Linux。"
case "$(uname -m)" in
  x86_64|amd64) ;;
  *) die "当前安装包仅支持 Linux amd64/x86_64。" ;;
esac
command -v systemctl >/dev/null 2>&1 || die "系统未使用 systemd。"

case "${1:-}" in
  uninstall|uninstall-all)
    uninstall_all
    exit 0
    ;;
esac

printf '\n将安装公开通用版 iWAN Gateway：\n'
printf '  1. 安装 sing-box with_iwan 核心\n'
printf '  2. 新机器创建空白 direct 配置\n'
printf '  3. 自动安装 Web 管理面板\n'
printf '  4. iWAN 账号、入口和落地节点全部在网页中设置\n\n'
printf '不会预设 HKT、SG、用户名或任何业务密码。\n\n'

install_deps
TMP_DIR=$(mktemp -d)
ARCHIVE_PATH="${TMP_DIR}/${ARCHIVE}"

info "下载 sing-box ${VERSION}…"
curl -fL --retry 3 --retry-delay 1 --connect-timeout 15 --max-time 300 -o "${ARCHIVE_PATH}" "${DOWNLOAD_URL}"
echo "${EXPECTED_SHA256}  ${ARCHIVE_PATH}" | sha256sum -c - >/dev/null || die "安装包校验失败。"
info "安装包校验通过。"

tar -xzf "${ARCHIVE_PATH}" -C "${TMP_DIR}"
SRC_DIR=$(find "${TMP_DIR}" -mindepth 1 -maxdepth 1 -type d -name 'sing-box-*linux-amd64' | head -n1)
[[ -n "${SRC_DIR}" && -x "${SRC_DIR}/sing-box" ]] || die "安装包结构不正确。"

mkdir -p "${INSTALL_DIR}" "${CONFIG_DIR}"
install -m 0755 "${SRC_DIR}/sing-box" "${INSTALL_DIR}/sing-box"
[[ -f "${SRC_DIR}/libcronet.so" ]] && install -m 0644 "${SRC_DIR}/libcronet.so" "${INSTALL_DIR}/libcronet.so"
[[ -f "${SRC_DIR}/LICENSE" ]] && install -m 0644 "${SRC_DIR}/LICENSE" "${INSTALL_DIR}/LICENSE"
ln -sfn "${INSTALL_DIR}/sing-box" "${BIN_LINK}"

VERSION_OUTPUT=$("${BIN_LINK}" version)
grep -q 'with_iwan' <<<"${VERSION_OUTPUT}" || die "当前二进制未检测到 with_iwan 标签。"

if [[ -f "${CONFIG_FILE}" ]]; then
  BACKUP_FILE="${CONFIG_FILE}.bak.$(date +%Y%m%d-%H%M%S)"
  cp -a "${CONFIG_FILE}" "${BACKUP_FILE}"
  warn "检测到现有配置，已保留原配置并备份到 ${BACKUP_FILE}。"
else
  cat >"${CONFIG_FILE}" <<'EOF_CONFIG'
{
  "log": {
    "level": "info",
    "timestamp": true
  },
  "inbounds": [],
  "outbounds": [
    {
      "type": "direct",
      "tag": "direct"
    }
  ],
  "route": {
    "rules": [],
    "final": "direct"
  }
}
EOF_CONFIG
  chmod 600 "${CONFIG_FILE}"
  info "已创建公开通用空白配置：无 iWAN 账号、无落地节点、默认 direct。"
fi

cat >"${SERVICE_FILE}" <<EOF_SERVICE
[Unit]
Description=sing-box iWAN routing service
After=network-online.target nss-lookup.target
Wants=network-online.target

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=${CONFIG_DIR}
Environment=LD_LIBRARY_PATH=${INSTALL_DIR}
ExecStart=${BIN_LINK} run -c ${CONFIG_FILE}
ExecReload=/bin/kill -HUP \$MAINPID
Restart=on-failure
RestartSec=3s
LimitNOFILE=1048576

[Install]
WantedBy=multi-user.target
EOF_SERVICE

"${BIN_LINK}" check -c "${CONFIG_FILE}" || die "配置检查失败。"
systemctl daemon-reload
systemctl enable --now sing-box
sleep 1
systemctl is-active --quiet sing-box || {
  journalctl -u sing-box -n 80 --no-pager || true
  die "sing-box 启动失败。"
}

open_panel_port
info "安装 Web 管理面板…"
PANEL_PORT="${PANEL_PORT}" bash <(curl -fsSL "https://raw.githubusercontent.com/${REPO}/main/install-web.sh?ts=$(date +%s)")

PUBLIC_IP=$(curl -4fsS --connect-timeout 3 https://api.ipify.org 2>/dev/null || true)
info "公开通用版安装完成。"
printf '\n访问面板：http://%s:%s\n' "${PUBLIC_IP:-你的VPS公网IP}" "${PANEL_PORT}"
printf '\n首次使用顺序：\n'
printf '  1. 登录 Web 面板\n'
printf '  2. 打开 iWAN 页面，填写监听端口、地址池、用户名和密码\n'
printf '  3. 点击“保存并重连”，面板会自动创建 iWAN 入口\n'
printf '  4. 打开节点页面，新增或一键导入自己的节点\n'
printf '  5. 在分流页面为各类业务独立选择出口\n'
printf '\n请按实际 iWAN 监听端口放行云安全组 TCP+UDP。\n'
printf '\n常用命令：\n'
printf '  systemctl status sing-box iwan-gateway --no-pager\n'
printf '  journalctl -u sing-box -f\n'
printf '  journalctl -u iwan-gateway -f\n'
printf '  curl -s http://127.0.0.1:%s/healthz\n' "${PANEL_PORT}"
