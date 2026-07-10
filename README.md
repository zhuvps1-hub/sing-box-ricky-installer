# iWAN Gateway v3

适用于 Debian、Ubuntu、CentOS、Rocky Linux 和 AlmaLinux 的 systemd 系统。

内置 sing-box `1.13.13-rickyhao.22`，安装时检查 `with_iwan` 标签。完整 Web 面板使用 Python 标准库，不依赖 Docker、npm 或 pip。

## 一键安装或升级

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/zhuvps1-hub/sing-box-ricky-installer/main/install-web.sh)
```

安装器会自动：

- 安装或保留现有 sing-box iWAN 配置
- 安装官方 mosdns v5，并创建 systemd 服务
- 安装 iWAN Gateway Web 面板
- 读取并保留现有 iWAN、落地节点和密码
- 自动迁移旧版登录账号、流量历史和备份
- 创建配置备份并执行健康检查

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

## 主要功能

### 仪表盘

- iWAN、sing-box、mosdns 实时运行状态
- CPU、内存、负载和系统运行时间
- 实时上传、下载速度和流量历史
- 节点延迟与可用状态
- 服务日志和快捷重启

### iWAN 与节点管理

- 修改监听地址、端口、地址池、用户名和密码
- 多用户管理
- 添加、编辑、启用、禁用和删除 Shadowsocks 节点
- 导入多行 `ss://`、节点 JSON 或 sing-box `outbounds`
- 节点 TCP 延迟检测和历史结果

### 分流路由

- 国内域名和国内 IP 直连
- Netflix、OpenAI、Claude、Gemini、Copilot 独立选择出口
- 默认出口选择
- 自定义域名、IP/CIDR 和端口规则
- 规则启用、禁用、排序和删除
- 保存前执行 `sing-box check`
- 自动备份、原子写入、重启和失败回滚

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

## 只安装命令行版 sing-box

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/zhuvps1-hub/sing-box-ricky-installer/main/install.sh)
```

## 卸载面板

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/zhuvps1-hub/sing-box-ricky-installer/main/install-gateway.sh) uninstall
```

卸载面板时会保留登录配置、流量历史、mosdns、备份和 sing-box 配置。
