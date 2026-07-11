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

MOSDNS_VERSION="5.3.4"
MOSDNS_ARCHIVE="mosdns-linux-amd64.zip"
MOSDNS_URL="https://github.com/IrineSistiana/mosdns/releases/download/v${MOSDNS_VERSION}/${MOSDNS_ARCHIVE}"
MOSDNS_SHA256="3abcc73080789eb1ccca78dab5049b85ac1e9b8f865ab60158a527b77cd72e85"
MOSDNS_INSTALL_DIR="/usr/local/lib/mosdns"
MOSDNS_BIN_LINK="/usr/local/bin/mosdns"
MOSDNS_CONFIG_DIR="/etc/mosdns"
MOSDNS_CONFIG_FILE="${MOSDNS_CONFIG_DIR}/config.yaml"
MOSDNS_SERVICE_FILE="/etc/systemd/system/mosdns.service"

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
    DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates curl tar unzip python3
  elif command -v dnf >/dev/null 2>&1; then
    dnf install -y ca-certificates curl tar unzip python3
  elif command -v yum >/dev/null 2>&1; then
    yum install -y ca-certificates curl tar unzip python3
  else
    command -v curl >/dev/null 2>&1 \
      && command -v tar >/dev/null 2>&1 \
      && command -v unzip >/dev/null 2>&1 \
      && command -v python3 >/dev/null 2>&1 \
      || die "请先安装 curl、tar、unzip 和 Python 3.10+。"
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
    warn "未检测到 UFW/firewalld，请在云安全组中放行实际面板 TCP 端口。"
  fi
}

uninstall_all() {
  info "正在卸载 Web 面板、sing-box 和 mosdns 程序…"
  bash <(curl -fsSL "https://raw.githubusercontent.com/${REPO}/main/install-web.sh?ts=$(date +%s)") uninstall || true
  systemctl disable --now sing-box mosdns 2>/dev/null || true
  rm -f "${SERVICE_FILE}" "${BIN_LINK}" "${MOSDNS_SERVICE_FILE}" "${MOSDNS_BIN_LINK}"
  rm -rf "${INSTALL_DIR}" "${MOSDNS_INSTALL_DIR}"
  systemctl daemon-reload
  warn "已保留 ${CONFIG_DIR}、${MOSDNS_CONFIG_DIR}、/etc/iwan-gateway 和相关备份。"
}

install_singbox() {
  local archive_path="${TMP_DIR}/${ARCHIVE}"
  info "下载 sing-box ${VERSION}…"
  curl -fL --retry 3 --retry-delay 1 --connect-timeout 15 --max-time 300 -o "${archive_path}" "${DOWNLOAD_URL}"
  echo "${EXPECTED_SHA256}  ${archive_path}" | sha256sum -c - >/dev/null || die "sing-box 安装包校验失败。"
  info "sing-box 安装包校验通过。"

  tar -xzf "${archive_path}" -C "${TMP_DIR}"
  local src_dir
  src_dir=$(find "${TMP_DIR}" -mindepth 1 -maxdepth 1 -type d -name 'sing-box-*linux-amd64' | head -n1)
  [[ -n "${src_dir}" && -x "${src_dir}/sing-box" ]] || die "sing-box 安装包结构不正确。"

  mkdir -p "${INSTALL_DIR}" "${CONFIG_DIR}"
  install -m 0755 "${src_dir}/sing-box" "${INSTALL_DIR}/sing-box"
  [[ -f "${src_dir}/libcronet.so" ]] && install -m 0644 "${src_dir}/libcronet.so" "${INSTALL_DIR}/libcronet.so"
  [[ -f "${src_dir}/LICENSE" ]] && install -m 0644 "${src_dir}/LICENSE" "${INSTALL_DIR}/LICENSE"
  ln -sfn "${INSTALL_DIR}/sing-box" "${BIN_LINK}"

  local version_output
  version_output=$("${BIN_LINK}" version)
  grep -q 'with_iwan' <<<"${version_output}" || die "当前二进制未检测到 with_iwan 标签。"

  if [[ -f "${CONFIG_FILE}" ]]; then
    local backup_file="${CONFIG_FILE}.bak.$(date +%Y%m%d-%H%M%S)"
    cp -a "${CONFIG_FILE}" "${backup_file}"
    warn "检测到现有 sing-box 配置，已保留并备份到 ${backup_file}。"
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

  "${BIN_LINK}" check -c "${CONFIG_FILE}" || die "sing-box 配置检查失败。"
  systemctl daemon-reload
  systemctl enable --now sing-box
  sleep 1
  systemctl is-active --quiet sing-box || {
    journalctl -u sing-box -n 80 --no-pager || true
    die "sing-box 启动失败。"
  }
}

install_mosdns() {
  local archive_path="${TMP_DIR}/${MOSDNS_ARCHIVE}"
  local extract_dir="${TMP_DIR}/mosdns-extract"
  mkdir -p "${extract_dir}"

  info "下载 mosdns v${MOSDNS_VERSION}…"
  curl -fL --retry 3 --retry-delay 1 --connect-timeout 15 --max-time 300 -o "${archive_path}" "${MOSDNS_URL}"
  echo "${MOSDNS_SHA256}  ${archive_path}" | sha256sum -c - >/dev/null || die "mosdns 安装包校验失败。"
  info "mosdns 安装包校验通过。"

  unzip -q "${archive_path}" -d "${extract_dir}"
  local mosdns_binary
  mosdns_binary=$(find "${extract_dir}" -type f -name mosdns | head -n1)
  [[ -n "${mosdns_binary}" ]] || die "mosdns 安装包结构不正确。"

  mkdir -p "${MOSDNS_INSTALL_DIR}" "${MOSDNS_CONFIG_DIR}" "${MOSDNS_CONFIG_DIR}/rules" "${MOSDNS_CONFIG_DIR}/backups"
  install -m 0755 "${mosdns_binary}" "${MOSDNS_INSTALL_DIR}/mosdns"
  ln -sfn "${MOSDNS_INSTALL_DIR}/mosdns" "${MOSDNS_BIN_LINK}"

  if [[ -f "${MOSDNS_CONFIG_FILE}" ]]; then
    local backup_file="${MOSDNS_CONFIG_FILE}.bak.$(date +%Y%m%d-%H%M%S)"
    cp -a "${MOSDNS_CONFIG_FILE}" "${backup_file}"
    warn "检测到现有 mosdns 配置，已保留并备份到 ${backup_file}。"
  else
    cat >"${MOSDNS_CONFIG_FILE}" <<'EOF_MOSDNS'
log:
  level: info
  production: true

api:
  http: "127.0.0.1:9091"

plugins:
  - tag: cache
    type: cache
    args:
      size: 10240
      lazy_cache_ttl: 86400

  - tag: forward_public
    type: forward
    args:
      concurrent: 2
      upstreams:
        - addr: "udp://223.5.5.5"
        - addr: "udp://119.29.29.29"
        - addr: "udp://1.1.1.1"
        - addr: "udp://8.8.8.8"

  - tag: main_sequence
    type: sequence
    args:
      - exec: $cache
      - matches: has_resp
        exec: accept
      - exec: $forward_public

  - tag: udp_server
    type: udp_server
    args:
      entry: main_sequence
      listen: "127.0.0.1:5335"

  - tag: tcp_server
    type: tcp_server
    args:
      entry: main_sequence
      listen: "127.0.0.1:5335"
EOF_MOSDNS
    chmod 600 "${MOSDNS_CONFIG_FILE}"
    info "已创建 mosdns 默认配置：DNS 127.0.0.1:5335，API 127.0.0.1:9091。"
  fi

  cat >"${MOSDNS_SERVICE_FILE}" <<EOF_SERVICE
[Unit]
Description=mosdns v5 DNS forwarder
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=${MOSDNS_CONFIG_DIR}
ExecStart=${MOSDNS_BIN_LINK} start -c ${MOSDNS_CONFIG_FILE}
Restart=on-failure
RestartSec=3s
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
EOF_SERVICE

  "${MOSDNS_BIN_LINK}" start -c "${MOSDNS_CONFIG_FILE}" --dry-run >/dev/null 2>&1 \
    || warn "当前 mosdns 版本不支持 dry-run，将通过实际启动验证配置。"

  systemctl daemon-reload
  systemctl enable --now mosdns
  sleep 1
  systemctl is-active --quiet mosdns || {
    journalctl -u mosdns -n 80 --no-pager || true
    die "mosdns 启动失败。"
  }
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
printf '  3. 安装 mosdns v5 并创建通用默认配置\n'
printf '  4. 自动安装 Web 管理面板\n'
printf '  5. iWAN 账号、入口和落地节点全部在网页中设置\n\n'
printf '不会预设 HKT、SG、用户名或任何业务密码。\n\n'

install_deps
TMP_DIR=$(mktemp -d)
install_singbox
install_mosdns

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
printf '  6. DNS 页面已具备可直接编辑的 mosdns 配置\n'
printf '\n请按实际 iWAN 监听端口放行云安全组 TCP+UDP。\n'
printf '\n常用命令：\n'
printf '  systemctl status sing-box mosdns iwan-gateway --no-pager\n'
printf '  journalctl -u sing-box -f\n'
printf '  journalctl -u mosdns -f\n'
printf '  journalctl -u iwan-gateway -f\n'
printf '  curl -s http://127.0.0.1:%s/healthz\n' "${PANEL_PORT}"
