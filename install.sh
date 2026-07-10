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

IWAN_PORT="${IWAN_PORT:-8000}"
IWAN_USERNAME="${IWAN_USERNAME:-hkl}"
IWAN_ADDRESS_POOL="${IWAN_ADDRESS_POOL:-10.10.10.0/24}"
HKT_SERVER="${HKT_SERVER:-hkboil.ddos.top}"
HKT_PORT="${HKT_PORT:-24895}"
HKT_METHOD="${HKT_METHOD:-2022-blake3-aes-128-gcm}"
SG_SERVER="${SG_SERVER:-217.116.172.44}"
SG_PORT="${SG_PORT:-22222}"
SG_METHOD="${SG_METHOD:-aes-128-gcm}"

info() { printf '\033[1;32m[信息]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[提示]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[错误]\033[0m %s\n' "$*" >&2; exit 1; }
cleanup() { [[ -n "${TMP_DIR}" && -d "${TMP_DIR}" ]] && rm -rf "${TMP_DIR}"; }
trap cleanup EXIT

json_escape() {
  local value="$1"
  value=${value//\\/\\\\}
  value=${value//\"/\\\"}
  value=${value//$'\n'/\\n}
  printf '%s' "$value"
}

read_secret() {
  local variable_name="$1"
  local prompt_text="$2"
  local current_value="${!variable_name:-}"
  if [[ -z "$current_value" ]]; then
    [[ -t 0 ]] || die "缺少 ${variable_name}。请使用交互式命令运行，或通过环境变量传入。"
    read -r -s -p "$prompt_text" current_value
    printf '\n'
  fi
  [[ -n "$current_value" ]] || die "${variable_name} 不能为空。"
  printf -v "$variable_name" '%s' "$current_value"
}

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
  command -v sha256sum >/dev/null 2>&1 || die "系统缺少 sha256sum。"
}

open_firewall_port() {
  if command -v ufw >/dev/null 2>&1; then
    ufw allow "${IWAN_PORT}/tcp" >/dev/null || true
    ufw allow "${IWAN_PORT}/udp" >/dev/null || true
    info "已尝试通过 UFW 放行 ${IWAN_PORT}/TCP+UDP。"
  elif command -v firewall-cmd >/dev/null 2>&1 && systemctl is-active --quiet firewalld; then
    firewall-cmd --permanent --add-port="${IWAN_PORT}/tcp" >/dev/null
    firewall-cmd --permanent --add-port="${IWAN_PORT}/udp" >/dev/null
    firewall-cmd --reload >/dev/null
    info "已通过 firewalld 放行 ${IWAN_PORT}/TCP+UDP。"
  else
    warn "未检测到 UFW/firewalld，请自行确认系统防火墙已放行 ${IWAN_PORT}/TCP+UDP。"
  fi
}

uninstall_singbox() {
  info "正在卸载 sing-box…"
  systemctl disable --now sing-box 2>/dev/null || true
  rm -f "${SERVICE_FILE}" "${BIN_LINK}"
  rm -rf "${INSTALL_DIR}"
  systemctl daemon-reload
  warn "配置目录 ${CONFIG_DIR} 已保留。如需彻底删除：rm -rf ${CONFIG_DIR}"
  info "卸载完成。"
}

[[ ${EUID} -eq 0 ]] || die "请使用 root 用户运行。"
[[ $(uname -s) == "Linux" ]] || die "仅支持 Linux。"
case "$(uname -m)" in
  x86_64|amd64) ;;
  *) die "当前安装包仅支持 Linux amd64/x86_64。" ;;
esac
command -v systemctl >/dev/null 2>&1 || die "系统未使用 systemd。"

if [[ ${1:-} == "uninstall" ]]; then
  uninstall_singbox
  exit 0
fi

printf '\n当前将部署以下分流：\n'
printf '  国内网站      → VPS 直连\n'
printf '  Netflix / AI → 新加坡落地 %s:%s\n' "$SG_SERVER" "$SG_PORT"
printf '  其他流量      → HKT 落地 %s:%s\n' "$HKT_SERVER" "$HKT_PORT"
printf '  iWAN 入口     → [::]:%s，用户 %s\n\n' "$IWAN_PORT" "$IWAN_USERNAME"

read_secret IWAN_PASSWORD "请输入 iWAN 用户 ${IWAN_USERNAME} 的密码："
read_secret HKT_PASSWORD "请输入 HKT 落地 Shadowsocks 密码："
read_secret SG_PASSWORD "请输入 SG 落地 Shadowsocks 密码："

IWAN_PASSWORD_JSON=$(json_escape "$IWAN_PASSWORD")
HKT_PASSWORD_JSON=$(json_escape "$HKT_PASSWORD")
SG_PASSWORD_JSON=$(json_escape "$SG_PASSWORD")

install_deps
TMP_DIR=$(mktemp -d)
ARCHIVE_PATH="${TMP_DIR}/${ARCHIVE}"

info "下载 sing-box ${VERSION}…"
curl -fL --retry 3 --connect-timeout 15 -o "${ARCHIVE_PATH}" "${DOWNLOAD_URL}"
echo "${EXPECTED_SHA256}  ${ARCHIVE_PATH}" | sha256sum -c - >/dev/null || die "安装包校验失败。"
info "安装包校验通过。"

tar -xzf "${ARCHIVE_PATH}" -C "${TMP_DIR}"
SRC_DIR=$(find "${TMP_DIR}" -mindepth 1 -maxdepth 1 -type d -name 'sing-box-*linux-amd64' | head -n1)
[[ -n "${SRC_DIR}" && -x "${SRC_DIR}/sing-box" ]] || die "安装包结构不正确。"

systemctl stop sing-box 2>/dev/null || true
mkdir -p "${INSTALL_DIR}" "${CONFIG_DIR}"
install -m 0755 "${SRC_DIR}/sing-box" "${INSTALL_DIR}/sing-box"
[[ -f "${SRC_DIR}/libcronet.so" ]] && install -m 0644 "${SRC_DIR}/libcronet.so" "${INSTALL_DIR}/libcronet.so"
[[ -f "${SRC_DIR}/LICENSE" ]] && install -m 0644 "${SRC_DIR}/LICENSE" "${INSTALL_DIR}/LICENSE"
ln -sfn "${INSTALL_DIR}/sing-box" "${BIN_LINK}"

"${BIN_LINK}" version | grep -q 'with_iwan' || die "当前二进制未检测到 with_iwan 标签。"

if [[ -f "${CONFIG_FILE}" ]]; then
  BACKUP_FILE="${CONFIG_FILE}.bak.$(date +%Y%m%d-%H%M%S)"
  cp -a "${CONFIG_FILE}" "${BACKUP_FILE}"
  warn "旧配置已备份到 ${BACKUP_FILE}"
fi

cat > "${CONFIG_FILE}" <<EOF_CONFIG
{
  "log": {
    "level": "info",
    "timestamp": true
  },
  "inbounds": [
    {
      "type": "iwan",
      "tag": "iwan-in",
      "listen": "::",
      "listen_port": ${IWAN_PORT},
      "address_pool": "${IWAN_ADDRESS_POOL}",
      "users": [
        { "username": "${IWAN_USERNAME}", "password": "${IWAN_PASSWORD_JSON}" }
      ]
    }
  ],
  "outbounds": [
    {
      "type": "shadowsocks",
      "tag": "hkt-landing",
      "server": "${HKT_SERVER}",
      "server_port": ${HKT_PORT},
      "method": "${HKT_METHOD}",
      "password": "${HKT_PASSWORD_JSON}"
    },
    {
      "type": "shadowsocks",
      "tag": "sg-landing",
      "server": "${SG_SERVER}",
      "server_port": ${SG_PORT},
      "method": "${SG_METHOD}",
      "password": "${SG_PASSWORD_JSON}"
    },
    { "type": "direct", "tag": "direct" }
  ],
  "route": {
    "rule_set": [
      { "tag": "geosite-cn", "type": "remote", "format": "binary", "url": "https://raw.githubusercontent.com/SagerNet/sing-geosite/rule-set/geosite-cn.srs" },
      { "tag": "geoip-cn", "type": "remote", "format": "binary", "url": "https://raw.githubusercontent.com/SagerNet/sing-geoip/rule-set/geoip-cn.srs" },
      { "tag": "geosite-netflix", "type": "remote", "format": "binary", "url": "https://raw.githubusercontent.com/SagerNet/sing-geosite/rule-set/geosite-netflix.srs" },
      { "tag": "geosite-openai", "type": "remote", "format": "binary", "url": "https://raw.githubusercontent.com/SagerNet/sing-geosite/rule-set/geosite-openai.srs" }
    ],
    "rules": [
      { "action": "sniff" },
      { "rule_set": ["geosite-cn", "geoip-cn"], "outbound": "direct" },
      { "rule_set": ["geosite-netflix", "geosite-openai"], "outbound": "sg-landing" },
      {
        "domain_suffix": [
          "claude.ai",
          "anthropic.com",
          "claude.com",
          "githubcopilot.com",
          "gemini.google.com",
          "generativelanguage.googleapis.com",
          "aistudio.google.com",
          "bard.google.com"
        ],
        "outbound": "sg-landing"
      }
    ],
    "final": "hkt-landing"
  }
}
EOF_CONFIG
chmod 600 "${CONFIG_FILE}"

cat > "${SERVICE_FILE}" <<EOF_SERVICE
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

"${BIN_LINK}" check -c "${CONFIG_FILE}" || die "配置检查失败。旧配置备份仍保留在 ${CONFIG_DIR}。"
open_firewall_port
systemctl daemon-reload
systemctl enable --now sing-box
sleep 2
systemctl is-active --quiet sing-box || {
  journalctl -u sing-box -n 50 --no-pager || true
  die "sing-box 启动失败。"
}

info "安装完成，sing-box 已运行并设置为开机自启。"
"${BIN_LINK}" version | head -n 8
printf '\n当前分流：\n'
printf '  国内网站      → direct\n'
printf '  Netflix / AI → sg-landing\n'
printf '  其他流量      → hkt-landing\n'
printf '\n请确认云服务商安全组已放行 %s/TCP+UDP。\n' "$IWAN_PORT"
printf '\n常用命令：\n'
printf '  查看状态：systemctl status sing-box --no-pager\n'
printf '  实时日志：journalctl -u sing-box -f\n'
printf '  修改配置：nano %s\n' "${CONFIG_FILE}"
printf '  重启服务：systemctl restart sing-box\n'
printf '  卸载程序：bash <(curl -fsSL %s/install.sh) uninstall\n' "${BASE_URL}"
