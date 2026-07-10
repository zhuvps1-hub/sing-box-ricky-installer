#!/usr/bin/env bash
set -Eeuo pipefail

VERSION="1.13.13-rickyhao.22"
ARCHIVE="sing-box-${VERSION}-linux-amd64.tar.gz"
BASE_URL="https://raw.githubusercontent.com/zhuvps1-hub/sing-box-ricky-installer/main"
DOWNLOAD_URL="https://github.com/Ricky-Hao/sing-box/releases/download/v${VERSION}/${ARCHIVE}"
EXPECTED_SHA256="d650de9b0cb3852ec4e878ae2291631f0891d3b71fdd1998745c20ea8780bc1f"
INSTALL_DIR="/usr/local/lib/sing-box"
BIN_LINK="/usr/local/bin/sing-box"
CONFIG_DIR="/etc/sing-box"
CONFIG_FILE="${CONFIG_DIR}/config.json"
SERVICE_FILE="/etc/systemd/system/sing-box.service"
TMP_DIR=""

info() { printf '\033[1;32m[信息]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[提示]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[错误]\033[0m %s\n' "$*" >&2; exit 1; }
cleanup() { [[ -n "${TMP_DIR}" && -d "${TMP_DIR}" ]] && rm -rf "${TMP_DIR}"; }
trap cleanup EXIT

[[ ${EUID} -eq 0 ]] || die "请使用 root 用户运行。"
[[ $(uname -s) == "Linux" ]] || die "仅支持 Linux。"
case "$(uname -m)" in
  x86_64|amd64) ;;
  *) die "当前安装包仅支持 Linux amd64/x86_64。" ;;
esac
command -v systemctl >/dev/null 2>&1 || die "系统未使用 systemd。"

install_deps() {
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update -y
    DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates curl tar
  elif command -v dnf >/dev/null 2>&1; then
    dnf install -y ca-certificates curl tar
  elif command -v yum >/dev/null 2>&1; then
    yum install -y ca-certificates curl tar
  else
    command -v curl >/dev/null 2>&1 && command -v tar >/dev/null 2>&1 || \
      die "请先安装 curl 和 tar。"
  fi
}

uninstall_singbox() {
  info "正在卸载 sing-box…"
  systemctl disable --now sing-box 2>/dev/null || true
  rm -f "${SERVICE_FILE}" "${BIN_LINK}"
  rm -rf "${INSTALL_DIR}"
  systemctl daemon-reload
  warn "配置目录 ${CONFIG_DIR} 已保留。如需彻底删除，请执行：rm -rf ${CONFIG_DIR}"
  info "卸载完成。"
}

if [[ ${1:-} == "uninstall" ]]; then
  uninstall_singbox
  exit 0
fi

install_deps
TMP_DIR=$(mktemp -d)
ARCHIVE_PATH="${TMP_DIR}/${ARCHIVE}"

info "下载 sing-box ${VERSION}…"
curl -fL --retry 3 --connect-timeout 15 -o "${ARCHIVE_PATH}" "${DOWNLOAD_URL}"

echo "${EXPECTED_SHA256}  ${ARCHIVE_PATH}" | sha256sum -c - >/dev/null || die "安装包校验失败。"
info "文件校验通过。"

tar -xzf "${ARCHIVE_PATH}" -C "${TMP_DIR}"
SRC_DIR=$(find "${TMP_DIR}" -mindepth 1 -maxdepth 1 -type d -name 'sing-box-*linux-amd64' | head -n1)
[[ -n "${SRC_DIR}" && -x "${SRC_DIR}/sing-box" ]] || die "安装包结构不正确。"

systemctl stop sing-box 2>/dev/null || true
mkdir -p "${INSTALL_DIR}" "${CONFIG_DIR}"
install -m 0755 "${SRC_DIR}/sing-box" "${INSTALL_DIR}/sing-box"
[[ -f "${SRC_DIR}/libcronet.so" ]] && install -m 0644 "${SRC_DIR}/libcronet.so" "${INSTALL_DIR}/libcronet.so"
[[ -f "${SRC_DIR}/LICENSE" ]] && install -m 0644 "${SRC_DIR}/LICENSE" "${INSTALL_DIR}/LICENSE"
ln -sfn "${INSTALL_DIR}/sing-box" "${BIN_LINK}"

if [[ ! -f "${CONFIG_FILE}" ]]; then
  cat > "${CONFIG_FILE}" <<'JSON'
{
  "log": {
    "level": "info",
    "timestamp": true
  },
  "inbounds": [
    {
      "type": "socks",
      "tag": "socks-in",
      "listen": "127.0.0.1",
      "listen_port": 1080
    }
  ],
  "outbounds": [
    {
      "type": "direct",
      "tag": "direct"
    }
  ]
}
JSON
  warn "已创建默认 SOCKS5 配置：127.0.0.1:1080"
else
  info "检测到已有配置，已保留：${CONFIG_FILE}"
fi

cat > "${SERVICE_FILE}" <<EOF_SERVICE
[Unit]
Description=sing-box Service
Documentation=https://github.com/SagerNet/sing-box
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
RestartSec=5s
LimitNOFILE=infinity

[Install]
WantedBy=multi-user.target
EOF_SERVICE

"${BIN_LINK}" check -c "${CONFIG_FILE}" || die "配置检查失败，请修改 ${CONFIG_FILE} 后执行 systemctl restart sing-box。"
systemctl daemon-reload
systemctl enable --now sing-box

info "安装完成。"
"${BIN_LINK}" version | head -n 5
printf '\n常用命令：\n'
printf '  查看状态：systemctl status sing-box --no-pager\n'
printf '  查看日志：journalctl -u sing-box -f\n'
printf '  修改配置：nano %s\n' "${CONFIG_FILE}"
printf '  重启服务：systemctl restart sing-box\n'
printf '  卸载程序：bash <(curl -fsSL %s/install.sh) uninstall\n' "${BASE_URL}"
