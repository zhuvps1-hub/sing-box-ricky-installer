#!/usr/bin/env bash
set -Eeuo pipefail

REPO="zhuvps1-hub/sing-box-ricky-installer"
STAMP="$(date +%s)"
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

PRIMARY="https://raw.githubusercontent.com/$REPO/main/install-panel-stable.sh?ts=$STAMP"
FALLBACK="https://cdn.jsdelivr.net/gh/$REPO@main/install-panel-stable.sh?ts=$STAMP"

curl -fL --retry 3 --retry-delay 1 --connect-timeout 15 --max-time 120 "$PRIMARY" -o "$TMP" \
  || curl -fL --retry 3 --retry-delay 1 --connect-timeout 15 --max-time 120 "$FALLBACK" -o "$TMP"

bash -n "$TMP"
bash "$TMP" "$@"
