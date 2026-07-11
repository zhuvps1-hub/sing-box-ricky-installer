#!/usr/bin/env bash
set -Eeuo pipefail

REPO="zhuvps1-hub/sing-box-ricky-installer"
REQUESTED_PORT="${PANEL_PORT:-8088}"
STAMP="$(date +%s)"
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

info(){ printf '\033[1;32m[信息]\033[0m %s\n' "$*"; }
warn(){ printf '\033[1;33m[提示]\033[0m %s\n' "$*"; }
die(){ printf '\033[1;31m[错误]\033[0m %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "请使用 root 用户运行。"
[[ "$REQUESTED_PORT" =~ ^[0-9]+$ ]] && ((REQUESTED_PORT >= 1 && REQUESTED_PORT <= 65535)) || die "面板端口无效：$REQUESTED_PORT"
command -v python3 >/dev/null 2>&1 || die "请先安装 Python 3。"

SELECTED_PORT="$REQUESTED_PORT"

if [[ ${1:-} != uninstall ]]; then
  SELECTED_PORT="$(REQUESTED_PORT="$REQUESTED_PORT" python3 - <<'PY'
import json
import os
import socket
import urllib.request

requested = int(os.environ["REQUESTED_PORT"])


def is_iwan_gateway(port: int) -> bool:
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/healthz",
            headers={"User-Agent": "iwan-gateway-installer/3", "Cache-Control": "no-cache"},
        )
        with urllib.request.urlopen(request, timeout=1.5) as response:
            payload = json.loads(response.read(4096).decode("utf-8"))
        return bool(payload.get("ok")) and bool(payload.get("version"))
    except Exception:
        return False


def can_bind(port: int) -> bool:
    sockets = []
    try:
        for family, host in ((socket.AF_INET, "0.0.0.0"), (socket.AF_INET6, "::")):
            sock = socket.socket(family, socket.SOCK_STREAM)
            sockets.append(sock)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if family == socket.AF_INET6:
                try:
                    sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                except OSError:
                    pass
            sock.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        for sock in sockets:
            sock.close()


if is_iwan_gateway(requested) or can_bind(requested):
    print(requested)
    raise SystemExit(0)

start = max(1024, requested + 1)
stop = min(65535, requested + 200)
for port in range(start, stop + 1):
    if can_bind(port):
        print(port)
        raise SystemExit(0)

raise SystemExit("无法找到可用的 Web 面板端口")
PY
)"

  if [[ "$SELECTED_PORT" == "$REQUESTED_PORT" ]]; then
    info "Web 面板将使用端口 $SELECTED_PORT。"
  else
    warn "端口 $REQUESTED_PORT 已被其他程序占用，自动改用端口 $SELECTED_PORT。"
  fi
fi

PRIMARY="https://raw.githubusercontent.com/$REPO/main/install-panel-stable.sh?ts=$STAMP"
FALLBACK="https://cdn.jsdelivr.net/gh/$REPO@main/install-panel-stable.sh?ts=$STAMP"
curl -fL --retry 3 --retry-delay 1 --connect-timeout 15 --max-time 120 "$PRIMARY" -o "$TMP" \
  || curl -fL --retry 3 --retry-delay 1 --connect-timeout 15 --max-time 120 "$FALLBACK" -o "$TMP"

bash -n "$TMP"
PANEL_PORT="$SELECTED_PORT" bash "$TMP" "$@"

if [[ ${1:-} != uninstall ]]; then
  if command -v ufw >/dev/null 2>&1; then
    ufw allow "$SELECTED_PORT/tcp" >/dev/null 2>&1 || true
  fi
  if command -v firewall-cmd >/dev/null 2>&1; then
    firewall-cmd --permanent --add-port="$SELECTED_PORT/tcp" >/dev/null 2>&1 || true
    firewall-cmd --reload >/dev/null 2>&1 || true
  fi
  info "面板端口：$SELECTED_PORT"
fi
