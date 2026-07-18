#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

REPO="${REPO:-zhuvps1-hub/sing-box-ricky-installer}"
INSTALLER_REF="215b27ba6e443e77c3a6f97b90bb285bf27f302d"
INSTALLER_SHA256="167f0ae0b3e5f23ccf4d75cc1676c7fe90c633a577998d3dae2f9d55cb8e1835"
RAW_BASE="${RAW_BASE:-https://raw.githubusercontent.com}"
JSDELIVR_BASE="${JSDELIVR_BASE:-https://cdn.jsdelivr.net/gh}"

log(){ printf '[iwan-gateway-bootstrap] %s\n' "$*"; }
die(){ printf '[iwan-gateway-bootstrap] ERROR: %s\n' "$*" >&2; exit 1; }

[[ ${EUID:-$(id -u)} -eq 0 ]] || die "请使用 root 运行"

if [[ "${1:-}" == "uninstall" ]]; then
  systemctl disable --now iwan-gateway.service iwan-gateway-helper.service 2>/dev/null || true
  rm -f /etc/systemd/system/iwan-gateway.service /etc/systemd/system/iwan-gateway-helper.service
  rm -f /run/iwan-gateway/helper.sock
  systemctl daemon-reload
  log "面板服务已卸载；sing-box、mosdns、配置、备份和审计数据均已保留。"
  exit 0
fi

command -v curl >/dev/null 2>&1 || die "缺少 curl"
command -v sha256sum >/dev/null 2>&1 || die "缺少 sha256sum"

temporary="$(mktemp /tmp/install-panel-v71.XXXXXX.sh)"
trap 'rm -f "$temporary"' EXIT
raw_url="${RAW_BASE%/}/${REPO}/${INSTALLER_REF}/install-panel-v71.sh"
cdn_url="${JSDELIVR_BASE%/}/${REPO}@${INSTALLER_REF}/install-panel-v71.sh"

log "下载固定提交 ${INSTALLER_REF:0:12} 的 v7.1 安装器"
if ! curl -fsSL --retry 3 --connect-timeout 10 "$raw_url" -o "$temporary"; then
  curl -fsSL --retry 3 --connect-timeout 10 "$cdn_url" -o "$temporary"
fi
actual="$(sha256sum "$temporary" | awk '{print $1}')"
[[ "$actual" == "$INSTALLER_SHA256" ]] || die "安装器 SHA-256 校验失败：期望 $INSTALLER_SHA256，实际 $actual"
chmod 0700 "$temporary"
log "安装器校验通过，进入签名发布安装流程"
exec bash "$temporary" "$@"
