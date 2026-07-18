#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

REPO="${REPO:-zhuvps1-hub/sing-box-ricky-installer}"
INSTALLER_REF="${IWAN_INSTALLER_REF:-main}"
PANEL_PORT="${PANEL_PORT:-8088}"
DATA_DIR="${IWAN_PANEL_DATA_DIR:-/var/lib/iwan-gateway}"
PANEL_USER="${IWAN_PANEL_USER:-iwan-gateway}"
PANEL_GROUP="${IWAN_PANEL_GROUP:-iwan-gateway}"
RAW_BASE="${RAW_BASE:-https://raw.githubusercontent.com}"
JSDELIVR_BASE="${JSDELIVR_BASE:-https://cdn.jsdelivr.net/gh}"

log(){ printf '[iwan-gateway-v7.1.2] %s\n' "$*"; }
die(){ printf '[iwan-gateway-v7.1.2] ERROR: %s\n' "$*" >&2; exit 1; }

[[ ${EUID:-$(id -u)} -eq 0 ]] || die "请使用 root 运行"
command -v curl >/dev/null 2>&1 || die "缺少 curl"

export PANEL_BIND="${PANEL_BIND:-0.0.0.0}"
export IWAN_ALLOW_PUBLIC_BIND="${IWAN_ALLOW_PUBLIC_BIND:-1}"
export PANEL_PORT

repair_data_permissions(){
  if id -u "$PANEL_USER" >/dev/null 2>&1 && getent group "$PANEL_GROUP" >/dev/null 2>&1; then
    install -d -o "$PANEL_USER" -g "$PANEL_GROUP" -m 0700 "$DATA_DIR"
    chown -R "$PANEL_USER:$PANEL_GROUP" "$DATA_DIR"
    find "$DATA_DIR" -type d -exec chmod 0700 {} +
    find "$DATA_DIR" -type f -exec chmod 0600 {} +
  fi
}

open_firewall(){
  if command -v ufw >/dev/null 2>&1; then
    ufw allow "${PANEL_PORT}/tcp" >/dev/null 2>&1 || true
  fi
  if command -v firewall-cmd >/dev/null 2>&1 && systemctl is-active --quiet firewalld; then
    firewall-cmd --permanent --add-port="${PANEL_PORT}/tcp" >/dev/null 2>&1 || true
    firewall-cmd --reload >/dev/null 2>&1 || true
  fi
}

repair_data_permissions

temporary="$(mktemp /tmp/install-panel-v71-base.XXXXXX.sh)"
trap 'rm -f "$temporary"' EXIT
raw_url="${RAW_BASE%/}/${REPO}/${INSTALLER_REF}/install-panel-v71.sh"
cdn_url="${JSDELIVR_BASE%/}/${REPO}@${INSTALLER_REF}/install-panel-v71.sh"

log "下载 ${INSTALLER_REF:0:12} 的签名发布安装器"
if ! curl -fsSL --retry 3 --connect-timeout 10 "$raw_url" -o "$temporary"; then
  curl -fsSL --retry 3 --connect-timeout 10 "$cdn_url" -o "$temporary"
fi
bash -n "$temporary" || die "基础安装器语法检查失败"
chmod 0700 "$temporary"

bash "$temporary" "$@"
repair_data_permissions
open_firewall

if [[ "${1:-}" != "--rollback" ]]; then
  systemctl restart iwan-gateway-helper.service iwan-gateway.service >/dev/null 2>&1 || true
  log "公网监听已启用：http://服务器IP:${PANEL_PORT}"
  log "请同时在云厂商安全组放行 TCP ${PANEL_PORT}。"
  log "当前为 HTTP；长期使用仍建议配置 HTTPS。"
fi
