#!/usr/bin/env bash
set -Eeuo pipefail

REPO="zhuvps1-hub/sing-box-ricky-installer"
PANEL_PORT="${PANEL_PORT:-8088}"
STAMP="$(date +%s)"
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

info(){ printf '\033[1;32m[信息]\033[0m %s\n' "$*"; }
warn(){ printf '\033[1;33m[提示]\033[0m %s\n' "$*"; }
die(){ printf '\033[1;31m[错误]\033[0m %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "请使用 root 用户运行。"
[[ "$PANEL_PORT" =~ ^[0-9]+$ ]] && ((PANEL_PORT >= 1 && PANEL_PORT <= 65535)) || die "面板端口无效：$PANEL_PORT"
command -v python3 >/dev/null 2>&1 || die "请先安装 Python 3。"

# 卸载不需要端口预检，直接交给稳定安装器处理。
if [[ ${1:-} != uninstall ]]; then
  HEALTH="$(curl -fsS --max-time 2 "http://127.0.0.1:${PANEL_PORT}/healthz" 2>/dev/null || true)"
  if printf '%s' "$HEALTH" | grep -Eq '"ok"[[:space:]]*:[[:space:]]*true' \
     && printf '%s' "$HEALTH" | grep -Eq '"version"'; then
    info "端口 ${PANEL_PORT} 已运行 iWAN Gateway，按正常升级流程处理。"
  else
    PANEL_PORT="$PANEL_PORT" python3 - <<'PY'
import os
import pathlib
import signal
import sys
import time
import subprocess

port = int(os.environ["PANEL_PORT"])
known_services = ("iwan-gateway", "f7010u-gateway", "sing-box-panel")
known_paths = (
    "/opt/iwan-gateway-panel/",
    "/opt/f7010u-gateway/",
    "/opt/sing-box-panel/",
)

def service_main_pids():
    result = set()
    for name in known_services:
        try:
            value = subprocess.run(
                ["systemctl", "show", "-p", "MainPID", "--value", name],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            ).stdout.strip()
            pid = int(value or 0)
            if pid > 0:
                result.add(pid)
        except Exception:
            pass
    return result

def listen_inodes():
    wanted = f"{port:04X}"
    found = set()
    for filename in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            lines = pathlib.Path(filename).read_text().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            columns = line.split()
            if len(columns) < 10 or columns[3] != "0A":
                continue
            if columns[1].rsplit(":", 1)[-1].upper() == wanted:
                found.add(columns[9])
    return found

def owners():
    inodes = listen_inodes()
    result = []
    if not inodes:
        return result
    for proc in pathlib.Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            pid = int(proc.name)
            matched = False
            for fd in (proc / "fd").iterdir():
                try:
                    target = os.readlink(fd)
                except OSError:
                    continue
                if target.startswith("socket:[") and target[8:-1] in inodes:
                    matched = True
                    break
            if not matched:
                continue
            raw = (proc / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace").strip()
            result.append((pid, raw or "[未知命令]"))
        except (OSError, ValueError):
            continue
    return result

running_service_pids = service_main_pids()
current = owners()
if not current:
    print(f"[信息] 端口 {port} 当前可用于面板。")
    raise SystemExit(0)

unknown = []
stale = []
for pid, command in current:
    if pid in running_service_pids:
        print(f"[信息] 端口 {port} 由现有面板服务占用，升级时会正常切换：PID {pid}")
        continue
    if any(marker in command for marker in known_paths):
        stale.append((pid, command))
    else:
        unknown.append((pid, command))

if unknown:
    print(f"[错误] 端口 {port} 被非 iWAN 面板程序占用，为避免误杀进程，安装已停止。", file=sys.stderr)
    for pid, command in unknown:
        print(f"  PID {pid}: {command}", file=sys.stderr)
    print(f"可改用其他端口：PANEL_PORT=8090 bash <(curl -fsSL 'https://raw.githubusercontent.com/zhuvps1-hub/sing-box-ricky-installer/main/install-web.sh?ts='$(date +%s))", file=sys.stderr)
    raise SystemExit(20)

for pid, command in stale:
    print(f"[提示] 清理旧面板残留进程 PID {pid}: {command}")
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass

if stale:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and owners():
        time.sleep(0.2)
    for pid, command in owners():
        if any(marker in command for marker in known_paths) and pid not in running_service_pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    time.sleep(0.3)

remaining = [(pid, command) for pid, command in owners() if pid not in running_service_pids]
if remaining:
    print(f"[错误] 端口 {port} 仍被残留进程占用：", file=sys.stderr)
    for pid, command in remaining:
        print(f"  PID {pid}: {command}", file=sys.stderr)
    raise SystemExit(21)

print(f"[信息] 端口 {port} 预检通过。")
PY
  fi
fi

PRIMARY="https://raw.githubusercontent.com/$REPO/main/install-panel-stable.sh?ts=$STAMP"
FALLBACK="https://cdn.jsdelivr.net/gh/$REPO@main/install-panel-stable.sh?ts=$STAMP"
curl -fL --retry 3 --retry-delay 1 --connect-timeout 15 --max-time 120 "$PRIMARY" -o "$TMP" \
  || curl -fL --retry 3 --retry-delay 1 --connect-timeout 15 --max-time 120 "$FALLBACK" -o "$TMP"
bash -n "$TMP"
PANEL_PORT="$PANEL_PORT" bash "$TMP" "$@"
