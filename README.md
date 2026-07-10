# iWAN Gateway v6.1

适用于 Debian、Ubuntu、Rocky Linux、AlmaLinux 等使用 systemd 的 Linux 服务器。

v6.1 延续“底层服务和 Web 面板完全分离”的方向：面板升级只替换面板程序，并从服务器当前的 sing-box、iWAN、mosdns 和系统状态中采样信息，不写死任何人的节点、密码、服务器地址或分流选择。

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

不使用 Base64 分片、XZ 多段包或临时打包文件。发布清单锁定一个不可变 Git 提交，并记录每个源文件的 Git Blob 校验值。

升级顺序：

1. 从 GitHub Contents API 读取发布清单。
2. 从固定提交下载 Python、HTML、CSS 和 JavaScript 文件。
3. 使用 GitHub 官方 Blob SHA 和解码后的 Git Blob 双重校验。
4. 执行 Python 语法检查、内置自检和单元测试覆盖的核心逻辑。
5. 在随机本机端口启动候选版本，测试健康接口、登录和配置采样。
6. 全部通过后原子切换 `current` 软链接并重启面板服务。
7. 正式健康检查失败时恢复旧服务文件、旧账号和旧面板。

候选版本通过之前，当前面板不会停止，sing-box 和 mosdns 始终不受影响。

## v6.1 界面与操作

- 去掉侧边栏和无意义的系统设置页
- 桌面使用顶部分类栏，手机使用底部五项导航
- 顶部只保留品牌、服务状态、主题、保存和退出
- 保存按钮不会跳动：无修改时显示“已保存”，有修改时显示“保存并应用”
- 深色、浅色、跟随系统三种主题
- 手机、iPad 和电脑自适应

## 节点与分流

- 自动读取全部 Shadowsocks 落地节点
- 新增、编辑、删除节点
- 节点加密方式使用下拉选择，包括：
  - `aes-128-gcm`
  - `aes-256-gcm`
  - `chacha20-ietf-poly1305`
  - `xchacha20-ietf-poly1305`
  - `2022-blake3-aes-128-gcm`
  - `2022-blake3-aes-256-gcm`
  - `2022-blake3-chacha20-poly1305`
- 一键导入多行 `ss://`
- 一键导入节点 JSON 数组
- 一键导入完整 sing-box 配置中的 `outbounds`
- 按需进行 TCP 延迟检测
- Netflix、AI、YouTube、Telegram 和其他流量独立选择出口

## iWAN 管理

- 自动读取当前 iWAN inbound、监听地址、端口、地址池和 MTU
- 支持修改 iWAN 用户名和密码
- 密码不会在页面回显，留空保持原密码
- 保存前执行 `sing-box check`
- 自动备份配置并重启 sing-box，使 iWAN 自动重新连接
- 启动失败自动恢复旧配置

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

卸载只删除面板程序和 systemd 服务，保留账号数据、sing-box、mosdns、节点、密码、分流配置及备份。
