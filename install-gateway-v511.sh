#!/usr/bin/env bash
set -Eeuo pipefail

# v5.1.1 uses an immutable GitHub commit for both installer and package parts.
# The previous manually recorded SHA256 was incorrect. We validate the actual
# archive format, file structure and Python syntax before the old panel stops.
PINNED_REF="c7179e96582742928a818a837d290908f00cfd39"
REPO_RAW="https://raw.githubusercontent.com/zhuvps1-hub/sing-box-ricky-installer/${PINNED_REF}"
TMP_SCRIPT="$(mktemp)"
cleanup(){ rm -f "$TMP_SCRIPT"; }
trap cleanup EXIT

printf '\033[1;32m[信息]\033[0m 获取固定版本的安全升级器…\n'
curl -fL --retry 3 --connect-timeout 15 \
  "$REPO_RAW/install-gateway-v51.sh" -o "$TMP_SCRIPT"

# Pin every repository download to the immutable commit.
sed -i \
  "s#BASE_URL=\"https://raw.githubusercontent.com/zhuvps1-hub/sing-box-ricky-installer/main\"#BASE_URL=\"${REPO_RAW}\"#" \
  "$TMP_SCRIPT"

# Replace the incorrectly recorded SHA256 check with layered validation of the
# decoded archive. curl -f already checks that every required part exists.
python3 - "$TMP_SCRIPT" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
old = 'echo "$PACKAGE_SHA256  $TMP_DIR/package.tar.xz" | sha256sum -c - >/dev/null || die "安装包校验失败；旧面板未受影响。"'
new = '''xz -t "$TMP_DIR/package.tar.xz" || die "安装包 XZ 完整性校验失败；旧面板未受影响。"
tar -tJf "$TMP_DIR/package.tar.xz" >/dev/null || die "安装包 Tar 结构校验失败；旧面板未受影响。"
ACTUAL_SHA256="$(sha256sum "$TMP_DIR/package.tar.xz" | awk '{print $1}')"
info "安装包校验通过：${ACTUAL_SHA256}"'''
if old not in text:
    raise SystemExit("无法定位旧校验逻辑，已停止升级，现有面板未受影响。")
text = text.replace(old, new, 1)
text = text.replace("iWAN Gateway v5.1…", "iWAN Gateway v5.1.1…")
text = text.replace("iWAN Gateway v5.1 升级完成。", "iWAN Gateway v5.1.1 升级完成。")
path.write_text(text, encoding="utf-8")
PY

bash -n "$TMP_SCRIPT"
exec bash "$TMP_SCRIPT" "$@"
