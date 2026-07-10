#!/usr/bin/env bash
set -Eeuo pipefail
BASE_URL="https://raw.githubusercontent.com/zhuvps1-hub/sing-box-ricky-installer/main"
printf '\n第一步：安装 sing-box iWAN 与当前分流配置\n\n'
bash <(curl -fsSL "$BASE_URL/install.sh")
printf '\n第二步：安装 Web 管理面板\n\n'
bash <(curl -fsSL "$BASE_URL/install-panel.sh")
