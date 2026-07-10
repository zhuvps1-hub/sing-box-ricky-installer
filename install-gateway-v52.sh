#!/usr/bin/env bash
set -Eeuo pipefail

BASE_URL="https://raw.githubusercontent.com/zhuvps1-hub/sing-box-ricky-installer/main"
TMP_SCRIPT="$(mktemp)"
PATCHED_SCRIPT="$(mktemp)"
cleanup(){ rm -f "$TMP_SCRIPT" "$PATCHED_SCRIPT"; }
trap cleanup EXIT

curl -fsSL --retry 3 --connect-timeout 15 \
  "$BASE_URL/install-gateway-v51.sh" -o "$TMP_SCRIPT" || {
  echo "[错误] 无法下载安装器，现有面板未受影响。" >&2
  exit 1
}

awk '
/^PACKAGE_SHA256=/ { next }
/sha256sum -c/ {
  print "xz -t \"$TMP_DIR/package.tar.xz\" || die \"XZ 完整性检查失败；旧面板未受影响。\""
  print "tar -tf \"$TMP_DIR/package.tar.xz\" >/dev/null || die \"归档结构检查失败；旧面板未受影响。\""
  next
}
{
  gsub(/v5\.1/, "v5.2")
  print
}
' "$TMP_SCRIPT" > "$PATCHED_SCRIPT"

bash -n "$PATCHED_SCRIPT" || {
  echo "[错误] 修复后的安装器语法检查失败，现有面板未受影响。" >&2
  exit 1
}

exec bash "$PATCHED_SCRIPT" "$@"
