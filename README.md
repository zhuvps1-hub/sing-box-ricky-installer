# iWAN Gateway v5 轻量版

适用于 Debian、Ubuntu、CentOS、Rocky Linux 和 AlmaLinux 的 systemd 系统。

内置 sing-box `1.13.13-rickyhao.22`，安装时检查 `with_iwan` 标签；同时支持 mosdns v5。Web 面板只使用 Python 标准库，不依赖 Docker、npm、Node.js 或 pip。

## 一键安装或升级

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/zhuvps1-hub/sing-box-ricky-installer/main/install-web.sh)
```

再次运行同一条命令即可升级。安装器会保留现有：

- iWAN 用户、端口和地址池
- Shadowsocks 落地节点和密码
- 分流规则
- 面板登录账号
- mosdns 配置
- 流量历史和配置备份

默认访问地址：

```text
http://你的VPS公网IP:8088
```

云安全组需要放行：

```text
8000/TCP+UDP   iWAN 入口
8088/TCP       Web 面板
```

建议只允许自己的公网 IP 访问 `8088/TCP`，或者在前面配置 HTTPS 反向代理。

## v5 架构优化

v5 以低占用和简单维护为目标：

- 单一 Python 后台进程，不运行额外前端服务
- HTML、CSS 和 JavaScript 本地提供，不依赖外部 CDN
- 状态接口短时缓存，避免多个页面重复读取系统信息
- CPU 使用率采用非阻塞采样，不在请求中等待
- 流量历史缓存并使用 SQLite WAL，减少磁盘写入和锁等待
- 节点延迟按需测试，最多 6 个并发，结果缓存 90 秒
- 仪表盘默认每 15 秒刷新；页面切到后台时自动暂停
- 日志、路由和 nftables 等较重数据只在打开对应页面时读取
- 不为 nftables、测速或监控额外启动常驻守护进程

## UI 与操作

- 深色、浅色、跟随系统三种主题
- 主题选择保存在浏览器，下次访问自动恢复
- 手机、iPad、电脑自适应布局
- 手机端节点列表和分流规则使用卡片展示
- 顶部显示未保存状态
- 修改节点或规则后统一点击“保存并应用”
- 保存前自动运行 `sing-box check`
- 自动备份、原子写入、重启和失败回滚

## 节点管理

- 自由新增、编辑、启用、禁用和删除 Shadowsocks 节点
- 支持服务器域名或 IP、端口、加密方式、密码和插件参数
- 批量导入多行 `ss://`
- 导入节点 JSON 数组
- 导入完整 sing-box 配置中的 `outbounds`
- 手动刷新节点延迟和可用状态

## 业务分流

每类业务都可在网页中自由选择任意已启用节点：

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
- Netflix
- OpenAI、ChatGPT、Claude、Gemini、GitHub Copilot
- YouTube、GoogleVideo 及相关静态域名
- Telegram 域名及官方 IP 段
- 自定义域名、域名后缀、IP/CIDR 和端口规则

## mosdns v5

- 自动安装官方 mosdns 二进制
- 自动创建并启动 `mosdns.service`
- 默认监听 `127.0.0.1:5335`
- DNS 缓存和多上游转发
- 查看状态和日志
- 在线编辑 YAML 配置
- 保存前校验、备份和失败恢复

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

## 无法访问 8088 时检查

```bash
systemctl status iwan-gateway --no-pager
journalctl -u iwan-gateway -n 100 --no-pager
curl -v http://127.0.0.1:8088/healthz
ss -lntp | grep 8088
```

安装器只有在本机健康检查成功后才会显示安装完成。本机正常而手机无法访问时，请检查云安全组和系统防火墙是否放行 `8088/TCP`。

## 卸载面板

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/zhuvps1-hub/sing-box-ricky-installer/main/install-gateway.sh) uninstall
```

卸载面板时会保留登录配置、流量历史、mosdns、备份和 sing-box 配置。
