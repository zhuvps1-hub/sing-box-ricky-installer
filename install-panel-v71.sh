#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

REPO="${REPO:-zhuvps1-hub/sing-box-ricky-installer}"
MANIFEST_URL="${MANIFEST_URL:-https://raw.githubusercontent.com/${REPO}/main/panel-release.json}"
PUBLIC_KEY_URL="${PUBLIC_KEY_URL:-https://raw.githubusercontent.com/${REPO}/main/release/iwan-release-ed25519-public.pem}"
PUBLIC_KEY_SHA256="ebc439b79669d73666d10989b5dfe9438976c6f5f4f9a5064cc3ab02edbdc25e"
INSTALL_ROOT="${INSTALL_ROOT:-/opt/iwan-gateway}"
RELEASES_DIR="${INSTALL_ROOT}/releases"
LEGACY_DIR="${INSTALL_ROOT}/legacy"
CURRENT_LINK="${INSTALL_ROOT}/current"
CONFIG_DIR="${IWAN_PANEL_CONFIG_DIR:-/etc/iwan-gateway}"
DATA_DIR="${IWAN_PANEL_DATA_DIR:-/var/lib/iwan-gateway}"
PANEL_USER="${IWAN_PANEL_USER:-iwan-gateway}"
PANEL_GROUP="${IWAN_PANEL_GROUP:-iwan-gateway}"
PANEL_BIND="${PANEL_BIND:-127.0.0.1}"
PANEL_PORT="${PANEL_PORT:-8088}"
ALLOW_PUBLIC_BIND="${IWAN_ALLOW_PUBLIC_BIND:-0}"
LOCK_FILE="/run/lock/iwan-gateway-install.lock"

log(){ printf '[iwan-gateway] %s\n' "$*"; }
die(){ printf '[iwan-gateway] ERROR: %s\n' "$*" >&2; exit 1; }
require_root(){ [[ ${EUID:-$(id -u)} -eq 0 ]] || die "请使用 root 运行"; }

atomic_link(){
  local target="$1" temporary="${CURRENT_LINK}.new"
  ln -sfn "$target" "$temporary"
  mv -Tf "$temporary" "$CURRENT_LINK"
}

health_check(){
  local index
  for index in $(seq 1 40); do
    if curl -fsS --max-time 2 "http://127.0.0.1:${PANEL_PORT}/healthz" >/dev/null; then return 0; fi
    sleep 0.5
  done
  return 1
}

latest_legacy_snapshot(){
  [[ -d "$LEGACY_DIR" ]] || return 1
  find "$LEGACY_DIR" -mindepth 1 -maxdepth 1 -type d -name 'legacy-*' -printf '%T@ %p\n' \
    | sort -rn | head -n1 | cut -d' ' -f2-
}

restore_legacy(){
  local snapshot="$1"
  [[ -f "$snapshot/iwan-gateway.service" ]] || die "旧版服务快照无效：$snapshot"
  log "恢复旧版面板服务快照 $(basename "$snapshot")"
  systemctl disable --now iwan-gateway-helper.service >/dev/null 2>&1 || true
  cp -a "$snapshot/iwan-gateway.service" /etc/systemd/system/iwan-gateway.service
  rm -f /etc/systemd/system/iwan-gateway-helper.service
  rm -rf /etc/systemd/system/iwan-gateway.service.d
  if [[ -d "$snapshot/iwan-gateway.service.d" ]]; then
    cp -a "$snapshot/iwan-gateway.service.d" /etc/systemd/system/iwan-gateway.service.d
  fi
  systemctl daemon-reload
  systemctl enable iwan-gateway.service >/dev/null 2>&1 || true
  systemctl restart iwan-gateway.service
  systemctl is-active --quiet iwan-gateway.service || die "旧版服务恢复后未进入 active 状态"
  log "已恢复旧版面板；新架构文件、配置备份和审计数据均保留。"
}

rollback(){
  require_root
  install -d -m 0755 "$RELEASES_DIR" "$LEGACY_DIR"
  local requested="${1:-}" current="" target="" legacy=""
  [[ -L "$CURRENT_LINK" ]] && current="$(readlink -f "$CURRENT_LINK")"
  if [[ -n "$requested" && -d "$LEGACY_DIR/$requested" ]]; then
    restore_legacy "$LEGACY_DIR/$requested"
    return
  fi
  if [[ -n "$requested" ]]; then
    target="${RELEASES_DIR}/${requested}"
    [[ -d "$target" ]] || die "未找到发布版本或旧版快照：$requested"
  else
    while IFS= read -r candidate; do
      [[ -d "$candidate" && "$candidate" != "$current" ]] || continue
      target="$candidate"; break
    done < <(find "$RELEASES_DIR" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' | sort -rn | cut -d' ' -f2-)
  fi
  if [[ -z "$target" ]]; then
    legacy="$(latest_legacy_snapshot || true)"
    [[ -n "$legacy" ]] || die "没有可回滚的历史发布或旧版快照"
    restore_legacy "$legacy"
    return
  fi
  atomic_link "$target"
  systemctl daemon-reload
  systemctl restart iwan-gateway-helper.service
  systemctl restart iwan-gateway.service
  health_check || die "回滚后健康检查失败"
  log "已回滚到 $(basename "$target")"
}

if [[ "${1:-}" == "--rollback" ]]; then
  rollback "${2:-}"
  exit 0
fi

require_root
exec 9>"$LOCK_FILE"
flock -n 9 || die "另一个安装/升级任务正在运行"

command -v systemctl >/dev/null || die "需要 systemd"
if ! command -v curl >/dev/null || ! command -v python3 >/dev/null || ! command -v openssl >/dev/null; then
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates curl openssl python3 util-linux
fi

WORK_DIR="$(mktemp -d /tmp/iwan-panel-v71.XXXXXX)"
trap 'rm -rf "$WORK_DIR"' EXIT
MANIFEST="$WORK_DIR/panel-release.json"
PUBLIC_KEY="$WORK_DIR/release-public.pem"
STAGE="$WORK_DIR/release"
LIST="$WORK_DIR/files.tsv"
mkdir -p "$STAGE"

log "下载并验证签名发布清单"
curl -fsSL --retry 3 --connect-timeout 10 "$MANIFEST_URL" -o "$MANIFEST"
curl -fsSL --retry 3 --connect-timeout 10 "$PUBLIC_KEY_URL" -o "$PUBLIC_KEY"
[[ "$(sha256sum "$PUBLIC_KEY" | awk '{print $1}')" == "$PUBLIC_KEY_SHA256" ]] || die "发布公钥指纹不匹配"
python3 - "$MANIFEST" "$PUBLIC_KEY" <<'PY'
import base64, copy, json, subprocess, sys, tempfile
from pathlib import Path
manifest_path, public_key = map(Path, sys.argv[1:])
manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
signature=manifest.get('signature')
if not isinstance(signature,dict) or signature.get('algorithm')!='ed25519':
    raise SystemExit('发布清单缺少 Ed25519 签名')
value=copy.deepcopy(manifest); value.pop('signature',None)
canonical=json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
try: raw=base64.b64decode(str(signature.get('value','')),validate=True)
except Exception as exc: raise SystemExit('发布签名 Base64 无效') from exc
with tempfile.TemporaryDirectory() as td:
    root=Path(td); data=root/'manifest.json'; sig=root/'manifest.sig'
    data.write_bytes(canonical); sig.write_bytes(raw)
    subprocess.run(['openssl','pkeyutl','-verify','-rawin','-pubin','-inkey',str(public_key),'-sigfile',str(sig),'-in',str(data)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
print('signature verified')
PY

readarray -t META < <(python3 - "$MANIFEST" <<'PY'
import json,re,sys
m=json.load(open(sys.argv[1],encoding='utf-8'))
version=str(m.get('version',''))
ref=str(m.get('ref',''))
if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9_.-]+)?',version): raise SystemExit('version invalid')
if not re.fullmatch(r'[0-9a-f]{40}',ref): raise SystemExit('ref invalid')
print(version); print(ref)
PY
)
VERSION="${META[0]}"
REF="${META[1]}"
RELEASE_ID="${VERSION}-${REF:0:12}"
RELEASE_DIR="${RELEASES_DIR}/${RELEASE_ID}"

python3 - "$MANIFEST" >"$LIST" <<'PY'
import base64,json,sys
m=json.load(open(sys.argv[1],encoding='utf-8'))
files=m.get('files')
if not isinstance(files,list) or not files: raise SystemExit('files invalid')
seen=set()
for item in files:
    source=str(item.get('source','')); target=str(item.get('target',''))
    sha=str(item.get('sha256','')); blob=str(item.get('git_blob','')); mode=str(item.get('mode','0644'))
    for value in (source,target):
        if not value or value.startswith('/') or '\\' in value or '..' in value.split('/'): raise SystemExit(f'unsafe path: {value}')
    if target in seen: raise SystemExit(f'duplicate target: {target}')
    seen.add(target)
    if len(sha)!=64 or any(c not in '0123456789abcdef' for c in sha): raise SystemExit(f'sha256 invalid: {target}')
    if blob and (len(blob)!=40 or any(c not in '0123456789abcdef' for c in blob)): raise SystemExit(f'git blob invalid: {target}')
    if mode not in {'0644','0755'}: raise SystemExit(f'mode invalid: {target}')
    print('\t'.join(base64.b64encode(x.encode()).decode() for x in (source,target,sha,blob,mode)))
PY

log "下载固定提交 ${REF:0:12} 的发布文件"
while IFS=$'\t' read -r source64 target64 sha64 blob64 mode64; do
  source="$(printf '%s' "$source64" | base64 -d)"
  target="$(printf '%s' "$target64" | base64 -d)"
  expected_sha="$(printf '%s' "$sha64" | base64 -d)"
  expected_blob="$(printf '%s' "$blob64" | base64 -d)"
  mode="$(printf '%s' "$mode64" | base64 -d)"
  temporary="$WORK_DIR/download"
  raw_url="https://raw.githubusercontent.com/${REPO}/${REF}/${source}"
  cdn_url="https://cdn.jsdelivr.net/gh/${REPO}@${REF}/${source}"
  if ! curl -fsSL --retry 3 --connect-timeout 10 "$raw_url" -o "$temporary"; then
    curl -fsSL --retry 3 --connect-timeout 10 "$cdn_url" -o "$temporary"
  fi
  [[ "$(sha256sum "$temporary" | awk '{print $1}')" == "$expected_sha" ]] || die "SHA-256 校验失败：$source"
  if [[ -n "$expected_blob" ]]; then
    actual_blob="$(python3 - "$temporary" <<'PY'
import hashlib,sys
b=open(sys.argv[1],'rb').read(); print(hashlib.sha1(b'blob '+str(len(b)).encode()+b'\0'+b).hexdigest())
PY
)"
    [[ "$actual_blob" == "$expected_blob" ]] || die "Git Blob 校验失败：$source"
  fi
  install -D -m "$mode" "$temporary" "$STAGE/$target"
done <"$LIST"

[[ -f "$STAGE/app.py" && -f "$STAGE/core.py" && -f "$STAGE/gateway/helper.py" ]] || die "发布包缺少新架构文件"
python3 "$STAGE/app.py" --self-test >/dev/null
! grep -R -E 'import (reliablecore|autosavecore|routingcore|statuscore|interactioncore|authcore|moscore)' "$STAGE/gateway" >/dev/null || die "发布包仍依赖旧版本栈"

getent group "$PANEL_GROUP" >/dev/null || groupadd --system "$PANEL_GROUP"
id -u "$PANEL_USER" >/dev/null 2>&1 || useradd --system --gid "$PANEL_GROUP" --home-dir "$DATA_DIR" --shell /usr/sbin/nologin "$PANEL_USER"
install -d -m 0755 "$INSTALL_ROOT" "$RELEASES_DIR" "$LEGACY_DIR"
install -d -o "$PANEL_USER" -g "$PANEL_GROUP" -m 0750 "$CONFIG_DIR" "$DATA_DIR"
install -d -o root -g root -m 0700 /etc/sing-box/backups /etc/mosdns/backups

LEGACY_SNAPSHOT=""
if [[ ! -L "$CURRENT_LINK" ]]; then
  OLD_UNIT_PATH="$(systemctl show -p FragmentPath --value iwan-gateway.service 2>/dev/null || true)"
  if [[ -n "$OLD_UNIT_PATH" && -f "$OLD_UNIT_PATH" ]]; then
    LEGACY_SNAPSHOT="$LEGACY_DIR/legacy-$(date +%Y%m%d-%H%M%S)"
    install -d -m 0700 "$LEGACY_SNAPSHOT"
    cp -a "$OLD_UNIT_PATH" "$LEGACY_SNAPSHOT/iwan-gateway.service"
    if [[ -d /etc/systemd/system/iwan-gateway.service.d ]]; then
      cp -a /etc/systemd/system/iwan-gateway.service.d "$LEGACY_SNAPSHOT/iwan-gateway.service.d"
    fi
    systemctl is-active --quiet iwan-gateway.service && printf 'active\n' >"$LEGACY_SNAPSHOT/previous-state" || printf 'inactive\n' >"$LEGACY_SNAPSHOT/previous-state"
    if [[ -L /opt/iwan-gateway-panel/current ]]; then
      readlink -f /opt/iwan-gateway-panel/current >"$LEGACY_SNAPSHOT/previous-current"
    fi
    log "已保存旧版服务快照：$(basename "$LEGACY_SNAPSHOT")"
  fi
fi

if [[ ! -d "$RELEASE_DIR" ]]; then
  install -d -m 0755 "$RELEASE_DIR"
  cp -a "$STAGE/." "$RELEASE_DIR/"
  chown -R root:root "$RELEASE_DIR"
  find "$RELEASE_DIR" -type d -exec chmod 0755 {} +
fi
OLD_TARGET=""
[[ -L "$CURRENT_LINK" ]] && OLD_TARGET="$(readlink -f "$CURRENT_LINK")"
[[ "$OLD_TARGET" == "$RELEASE_DIR" ]] && OLD_TARGET=""
atomic_link "$RELEASE_DIR"

cat >"$CONFIG_DIR/gateway.env" <<EOF
IWAN_PANEL_CONFIG_DIR=$CONFIG_DIR
IWAN_PANEL_DATA_DIR=$DATA_DIR
IWAN_HELPER_SOCKET=/run/iwan-gateway/helper.sock
IWAN_HELPER_LOCK_DIR=/run/iwan-gateway/locks
IWAN_PANEL_USER=$PANEL_USER
IWAN_PANEL_GROUP=$PANEL_GROUP
IWAN_AUDIT_LOG=$DATA_DIR/audit.jsonl
SINGBOX_CONFIG=${SINGBOX_CONFIG:-/etc/sing-box/config.json}
SINGBOX_BINARY=${SINGBOX_BINARY:-/usr/local/bin/sing-box}
SINGBOX_BACKUP_DIR=${SINGBOX_BACKUP_DIR:-/etc/sing-box/backups}
MOSDNS_CONFIG=${MOSDNS_CONFIG:-/etc/mosdns/config.yaml}
MOSDNS_BACKUP_DIR=${MOSDNS_BACKUP_DIR:-/etc/mosdns/backups}
PANEL_BIND=$PANEL_BIND
PANEL_PORT=$PANEL_PORT
IWAN_ALLOW_PUBLIC_BIND=$ALLOW_PUBLIC_BIND
IWAN_SECURE_COOKIES=auto
IWAN_TRUST_LOCAL_PROXY=1
EOF
chown root:"$PANEL_GROUP" "$CONFIG_DIR/gateway.env"
chmod 0640 "$CONFIG_DIR/gateway.env"

cat >/etc/systemd/system/iwan-gateway-helper.service <<EOF
[Unit]
Description=iWAN Gateway privileged helper
After=local-fs.target
Before=iwan-gateway.service

[Service]
Type=simple
User=root
Group=root
EnvironmentFile=$CONFIG_DIR/gateway.env
ExecStart=/usr/bin/python3 $CURRENT_LINK/app.py --helper
Restart=on-failure
RestartSec=2
RuntimeDirectory=iwan-gateway
RuntimeDirectoryMode=0755
UMask=0007
NoNewPrivileges=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectSystem=strict
ProtectHome=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectKernelLogs=yes
ProtectControlGroups=yes
RestrictRealtime=yes
RestrictSUIDSGID=yes
LockPersonality=yes
RestrictAddressFamilies=AF_UNIX
ReadWritePaths=/etc/sing-box /etc/mosdns $DATA_DIR /run/iwan-gateway

[Install]
WantedBy=multi-user.target
EOF

cat >/etc/systemd/system/iwan-gateway.service <<EOF
[Unit]
Description=iWAN Gateway nonprivileged web panel
Requires=iwan-gateway-helper.service
After=network-online.target iwan-gateway-helper.service
Wants=network-online.target

[Service]
Type=simple
User=$PANEL_USER
Group=$PANEL_GROUP
EnvironmentFile=$CONFIG_DIR/gateway.env
ExecStart=/usr/bin/python3 $CURRENT_LINK/app.py --host \${PANEL_BIND} --port \${PANEL_PORT}
Restart=on-failure
RestartSec=2
UMask=0077
NoNewPrivileges=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectSystem=strict
ProtectHome=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectKernelLogs=yes
ProtectControlGroups=yes
RestrictRealtime=yes
RestrictSUIDSGID=yes
LockPersonality=yes
MemoryDenyWriteExecute=yes
CapabilityBoundingSet=
AmbientCapabilities=
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
ReadWritePaths=$CONFIG_DIR $DATA_DIR /run/iwan-gateway

[Install]
WantedBy=multi-user.target
EOF

if [[ -f "$CONFIG_DIR/auth.json" ]]; then
  chown "$PANEL_USER":"$PANEL_GROUP" "$CONFIG_DIR/auth.json"
  chmod 0600 "$CONFIG_DIR/auth.json"
else
  GENERATED_PASSWORD=0
  if [[ -z "${PANEL_ADMIN_PASSWORD:-}" ]]; then
    PANEL_ADMIN_PASSWORD="$(openssl rand -base64 24 | tr -d '\n')"
    GENERATED_PASSWORD=1
  fi
  PANEL_ADMIN_USER="${PANEL_ADMIN_USER:-admin}"
  runuser -u "$PANEL_USER" -- env \
    IWAN_PANEL_CONFIG_DIR="$CONFIG_DIR" IWAN_PANEL_DATA_DIR="$DATA_DIR" \
    PANEL_ADMIN_USER="$PANEL_ADMIN_USER" PANEL_ADMIN_PASSWORD="$PANEL_ADMIN_PASSWORD" \
    python3 "$CURRENT_LINK/app.py" --init-auth >/dev/null
fi

systemctl daemon-reload
systemctl enable iwan-gateway-helper.service iwan-gateway.service >/dev/null
systemctl restart iwan-gateway-helper.service
systemctl restart iwan-gateway.service
if ! health_check; then
  journalctl -u iwan-gateway-helper.service -u iwan-gateway.service -n 120 --no-pager >&2 || true
  if [[ -n "$OLD_TARGET" && -d "$OLD_TARGET" ]]; then
    log "新版本启动失败，自动回滚到上一新架构版本"
    atomic_link "$OLD_TARGET"
    systemctl restart iwan-gateway-helper.service || true
    systemctl restart iwan-gateway.service || true
  elif [[ -n "$LEGACY_SNAPSHOT" && -d "$LEGACY_SNAPSHOT" ]]; then
    log "新架构首次升级失败，自动恢复旧版服务"
    restore_legacy "$LEGACY_SNAPSHOT" || true
  fi
  die "安装后健康检查失败"
fi

CURRENT_REAL="$(readlink -f "$CURRENT_LINK")"
count=0
while IFS= read -r candidate; do
  [[ "$candidate" == "$CURRENT_REAL" ]] && continue
  count=$((count+1))
  (( count <= 5 )) && continue
  rm -rf --one-file-system "$candidate"
done < <(find "$RELEASES_DIR" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' | sort -rn | cut -d' ' -f2-)

log "v${VERSION} 安装/升级完成"
log "面板仅监听 http://${PANEL_BIND}:${PANEL_PORT}"
log "公网访问请使用 Caddy/Nginx HTTPS 反向代理到 127.0.0.1:${PANEL_PORT}，不要直接开放 8088。"
log "回滚命令：bash install-panel-v71.sh --rollback"
if [[ -n "$LEGACY_SNAPSHOT" ]]; then
  log "首次升级旧版快照：$(basename "$LEGACY_SNAPSHOT")"
fi
if [[ "${GENERATED_PASSWORD:-0}" == 1 ]]; then
  printf '\n管理员：%s\n一次性随机密码：%s\n请立即保存并登录后更换。\n' "$PANEL_ADMIN_USER" "$PANEL_ADMIN_PASSWORD"
fi
