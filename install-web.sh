#!/usr/bin/env bash
set -Eeuo pipefail
BASE_URL="https://raw.githubusercontent.com/zhuvps1-hub/sing-box-ricky-installer/main"
bash <(curl -fsSL "$BASE_URL/install-panel-v6.sh") "$@"
