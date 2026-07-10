# iWAN Gateway v4

适用于 Debian、Ubuntu、CentOS、Rocky Linux 和 AlmaLinux 的 systemd 系统。

内置 sing-box `1.13.13-rickyhao.22`，安装时检查 `with_iwan` 标签。Web 面板使用 Python 标准库，不依赖 Docker、npm 或 pip。

## 一键安装或升级

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/zhuvps1-hub/sing-box-ricky-installer/main/install-web.sh)
```

安装器会自动：

- 安装或保留现有 sing-box iWAN 配置
- 安装官方 mosdns v5，并创建 systemd 服务
- 安装 iWAN Gateway v4 Web 面板
- 读取并保留现有 iWAN、落地节点和密码
- 自动迁移旧版登录账号、流量历史和备份
- 停止旧面板并重建稳定的 `iwan-gateway.service`
- 启动后自动访问 `/healthz` 进行健康检查

默认访问地址：

```text
http://你的VPS公网IP:8088
```

云安全组需要放行：

```text
8000/TCP+UDP   iWAN 入口
8088/TCP       Web 面板
```

建议只允许自己的公网 IP 访问 8088，或通过 HTTPS 反向代理访问。

## 节点管理

- 自由新增、编辑、启用、禁用和删除 Shadowsocks 节点
- 支持服务器域名或 IP、端口、加密方式、密码和插件参数
- 批量导入多行 `ss://` 节点
- 导入节点 JSON 数组
- 导入完整 sing-box 配置中的 `outbounds`
- 节点 TCP 延迟检测和可用状态
- 删除正在使用的节点前给出提示

## 业务分流

每类业务都可以在网页下拉框里自由选择任意已启用节点：

```text
国内网站        → direct
Netflix         → 自由选择节点
ChatGPT/Claude  → 自由选择节点
YouTube         → 自由选择节点
Telegram        → 自由选择节点
其他流量        → 自由选择默认节点
```

内置规则包括：

- 国内域名和国内 IP
- Netflix 规则集
- OpenAI、ChatGPT、Claude、Gemini、GitHub Copilot
- YouTube、GoogleVideo 和相关静态域名
- Telegram 域名及官方 IP 段
- 自定义域名、域名后缀、IP/CIDR 和端口规则
- 保存前执行 `sing-box check`
- 自动备份、原子写入、重启和失败回滚

## 主要功能

### 仪表盘

- iWAN、sing-box、mosdns 实时运行状态
- CPU、内存、负载和系统运行时间
- 实时上传、下载速度和流量历史
- 节点延迟与可用状态
- 服务日志和快捷重启

### iWAN 管理

- 修改监听地址、端口和地址池
- 修改用户名和密码
- 多用户管理
- 保存后自动生成并应用 sing-box 配置

### mosdns v5

- 自动安装官方 mosdns 二进制
- 自动创建并启动 `mosdns.service`
- 默认监听 `127.0.0.1:5335`
- DNS 缓存和多上游转发
- 查看运行状态和日志
- 在线编辑 YAML 配置
- 保存前校验、备份和失败恢复

### 系统管理

- 查看网卡、路由、监听端口和 nftables
- 查看 sing-box、mosdns、面板和系统日志
- 配置备份、恢复和下载
- 修改面板登录密码

## 服务与文件

```text
面板服务：iwan-gateway.service
面板程序：/opt/iwan-gateway
面板配置：/etc/iwan-gateway
历史数据：/var/lib/iwan-gateway/gateway.db
sing-box：/etc/sing-box/config.json
mosdns：/etc/mosdns/config.yaml
备份目录：/etc/sing-box/backups
```

常用命令：

```bash
systemctl status iwan-gateway --no-pager
journalctl -u iwan-gateway -f
systemctl restart iwan-gateway

systemctl status sing-box --no-pager
journalctl -u sing-box -f

systemctl status mosdns --no-pager
journalctl -u mosdns -f
```

## 8088 无法访问时检查

```bash
systemctl status iwan-gateway --no-pager
journalctl -u iwan-gateway -n 100 --no-pager
curl -v http://127.0.0.1:8088/healthz
ss -lntp | grep 8088
```

安装器只有在本机健康检查成功后才会显示安装完成。如果本机正常而手机不能访问，请检查云安全组和系统防火墙是否放行 `8088/TCP`。

## 只安装命令行版 sing-box

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/zhuvps1-hub/sing-box-ricky-installer/main/install.sh)
```

## 卸载面板

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/zhuvps1-hub/sing-box-ricky-installer/main/install-gateway.sh) uninstall
```

卸载面板时会保留登录配置、流量历史、mosdns、备份和 sing-box 配置。
