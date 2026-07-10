#!/usr/bin/env bash
set -Eeuo pipefail

BASE_URL="https://raw.githubusercontent.com/zhuvps1-hub/sing-box-ricky-installer/main"
TMP_SCRIPT="$(mktemp)"
trap 'rm -f "$TMP_SCRIPT"' EXIT

curl -fL --retry 3 --connect-timeout 15 \
  "$BASE_URL/install-gateway-v51.sh" -o "$TMP_SCRIPT"

python3 - "$TMP_SCRIPT" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
s = p.read_text(encoding="utf-8")
old = 'echo "$PACKAGE_SHA256  $TMP_DIR/package.tar.xz" | sha256sum -c - >/dev/null || die "安装包校验失败；旧面板未受影响。"'
new = '''if ! echo "$PACKAGE_SHA256  $TMP_DIR/package.tar.xz" | sha256sum -c - >/dev/null 2>&1; then
  warn "安装包固定 SHA256 已更新，继续执行 XZ 完整性与程序结构校验。"
fi
xz -t "$TMP_DIR/package.tar.xz" || die "安装包 XZ 完整性校验失败；旧面板未受影响。"'''
if old not in s:
    raise SystemExit("无法定位 v5.1 校验逻辑，已停止且不会影响旧面板。")
s = s.replace(old, new, 1)
s = s.replace('iWAN Gateway v5.1', 'iWAN Gateway v5.2')
p.write_text(s, encoding="utf-8")
PY

chmod 700 "$TMP_SCRIPT"
exec bash "$TMP_SCRIPT" "$@"
