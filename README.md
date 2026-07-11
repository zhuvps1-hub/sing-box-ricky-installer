# iWAN Gateway v6.7

面向公众的轻量 iWAN、sing-box 多落地分流和 mosdns Web 管理方案。

项目不再预设任何人的 iWAN 用户名、HKT、SG、节点地址或业务密码。全新服务器安装完成后，直接登录 Web 面板填写自己的 iWAN 信息并导入自己的节点。

## 完整教程

**[完整安装、升级与使用教程](docs/完整安装与使用教程.md)**

教程包含全新 VPS、已有底层只装面板、首次初始化、节点导入、独立业务分流、mosdns、升级回滚和故障排查。

## 全新 VPS：一条命令

```bash
bash <(curl -fsSL "https://raw.githubusercontent.com/zhuvps1-hub/sing-box-ricky-installer/main/install.sh?ts=$(date +%s)")
```

脚本会自动完成：

```text
安装 sing-box with_iwan 核心
→ 创建无账号、无节点的 direct 空白配置
→ 安装并启动 systemd 服务
→ 安装 Web 面板
→ 设置面板登录账号和密码
```

安装过程中不会询问：

- iWAN 用户名或密码；
- HKT、SG、JP 等落地节点；
- Shadowsocks 节点密码；
- Netflix、AI、YouTube 或 Telegram 出口。

这些信息全部在安装完成后通过 Web 面板设置。

## 首次登录后的操作顺序

默认访问：

```text
http://你的VPS公网IP:8088
```

1. 打开 **iWAN** 页面；
2. 填写监听地址、监听端口、地址池、MTU、用户名和密码；
3. 点击 **保存并重连**，面板会自动创建 iWAN inbound；
4. 打开 **节点** 页面，新增或一键导入自己的 Shadowsocks 节点；
5. 打开 **分流** 页面，分别选择 Netflix、AI、YouTube、Telegram 和其他流量出口。

所有业务完全独立，不会自动跟随其他分类。

## 已有 sing-box / iWAN：只安装或升级面板

```bash
bash <(curl -fsSL "https://raw.githubusercontent.com/zhuvps1-hub/sing-box-ricky-installer/main/install-web.sh?ts=$(date +%s)")
```

面板安装器不会安装、覆盖或主动重启现有 sing-box 和 mosdns。它会从当前服务器配置中采样 iWAN、节点和分流信息。

## 公开版设计

- 初始 sing-box 配置只有 `direct`；
- 不写死任何用户名、服务器、端口或密码；
- 首次保存 iWAN 设置时自动创建 inbound；
- 节点支持手动新增、`ss://`、JSON 和 sing-box `outbounds` 导入；
- Netflix、ChatGPT/Claude/Gemini、YouTube、Telegram、其他流量独立选择出口；
- 节点和分流可靠自动保存；
- iWAN 在独立页面手动确认并自动重连；
- mosdns 在独立页面保存、备份、恢复和重启；
- 配置校验失败不覆盖现有文件，服务启动失败自动回滚；
- 面板升级与底层服务分离。

## 支持环境

- Linux amd64 / x86_64；
- systemd；
- Debian、Ubuntu、Rocky Linux、AlmaLinux；
- root 用户；
- Python 3.10 或更高版本。

暂不支持 ARM、OpenWrt、Alpine/OpenRC。

## 常用命令

```bash
systemctl status sing-box iwan-gateway --no-pager
journalctl -u sing-box -n 100 --no-pager
journalctl -u iwan-gateway -n 100 --no-pager
curl -s http://127.0.0.1:8088/healthz
/usr/local/bin/sing-box check -c /etc/sing-box/config.json
```

## 卸载

只卸载 Web 面板：

```bash
bash <(curl -fsSL "https://raw.githubusercontent.com/zhuvps1-hub/sing-box-ricky-installer/main/install-web.sh?ts=$(date +%s)") uninstall
```

卸载面板和 sing-box 程序，但保留配置目录与备份：

```bash
bash <(curl -fsSL "https://raw.githubusercontent.com/zhuvps1-hub/sing-box-ricky-installer/main/install.sh?ts=$(date +%s)") uninstall
```
