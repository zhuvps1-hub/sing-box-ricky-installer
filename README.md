# iWAN Gateway v6

适用于 Debian、Ubuntu、Rocky Linux、AlmaLinux 等使用 systemd 的 Linux 服务器。

v6 重新确定了项目方向：**底层服务和 Web 面板完全分离**。面板升级只替换面板程序，并从服务器当前的 sing-box、iWAN、mosdns 和系统状态中采样信息，不再下载压缩分片，不再写死任何人的节点、密码、服务器地址或分流选择。

## 已有 sing-box / iWAN：只安装或升级面板

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/zhuvps1-hub/sing-box-ricky-installer/main/install-web.sh)
```

默认访问地址：

```text
http://你的VPS公网IP:8088
```

面板安装器不会安装、覆盖或重启 sing-box 与 mosdns。只有登录面板后主动点击“保存并应用”，才会修改 sing-box 配置。

## 全新服务器

先安装 sing-box iWAN 核心：

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/zhuvps1-hub/sing-box-ricky-installer/main/install.sh)
```

再安装面板：

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/zhuvps1-hub/sing-box-ricky-installer/main/install-web.sh)
```

## 稳定升级机制

v6 不再使用 Base64、XZ 或多段压缩包。发布清单会锁定一个不可变 Git 提交，并记录每个源文件的 Git Blob 校验值。

升级顺序：

1. 下载发布清单。
2. 从固定提交逐个下载 Python、HTML、CSS 和 JavaScript 文件。
3. 逐文件校验 Git Blob 哈希。
4. 执行 Python 语法检查和内置自检。
5. 在随机本机端口启动候选版本并访问健康接口。
6. 全部通过后，原子切换 `current` 软链接并重启面板服务。
7. 正式健康检查失败时恢复旧服务文件和旧面板。

在候选版本通过检查之前，当前面板不会停止，sing-box 和 mosdns 始终不受影响。

## 面板功能

- 自动读取现有 iWAN inbound、监听端口、地址池和 MTU
- 自动读取全部 Shadowsocks 落地节点
- 新增、编辑、删除节点
- 批量导入 `ss://` 节点
- 按需进行 TCP 延迟检测
- Netflix、AI、YouTube、Telegram 和其他流量独立选择出口
- 深色、浅色、跟随系统三种主题
- CPU、内存、实时上下行和服务状态采样
- sing-box、mosdns 和面板日志
- 路由、监听端口和网络状态
- 保存前执行 `sing-box check`
- 配置自动备份、原子写入、启动失败自动回滚

## 轻量架构

- 单个 Python 标准库后台进程
- 不依赖 Docker、Node.js、npm、pip 或外部 CDN
- 系统状态每 3 秒在后台统一采样
- 浏览器仪表盘默认每 15 秒刷新，页面隐藏时暂停刷新
- 日志、路由和端口仅在打开对应页面时读取
- 节点测速仅在用户点击时执行，最多 6 个并发
- 不额外运行测速、nftables 或监控守护进程

## 文件与服务

```text
面板服务：iwan-gateway.service
版本目录：/opt/iwan-gateway-panel/releases
当前版本：/opt/iwan-gateway-panel/current
账号配置：/etc/iwan-gateway/auth.json
面板数据：/var/lib/iwan-gateway
sing-box：/etc/sing-box/config.json
mosdns：/etc/mosdns/config.yaml
备份目录：/etc/sing-box/backups
```

## 常用命令

```bash
systemctl status iwan-gateway --no-pager
journalctl -u iwan-gateway -n 100 --no-pager
curl -s http://127.0.0.1:8088/healthz
```

## 防火墙

通常需要在云安全组中放行：

```text
8000/TCP+UDP   iWAN 入口，具体以现有配置为准
8088/TCP       Web 面板，建议只允许自己的公网 IP
```

面板目前默认使用 HTTP。公开部署时建议通过反向代理提供 HTTPS。

## 卸载面板

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/zhuvps1-hub/sing-box-ricky-installer/main/install-web.sh) uninstall
```

卸载只删除 v6 面板程序和 systemd 服务，保留账号数据、sing-box、mosdns、节点、密码、分流配置及备份。
