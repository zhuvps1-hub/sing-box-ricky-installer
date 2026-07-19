# iWAN Gateway 1.1

基于 Ricky-Hao `sing-box 1.13.13-rickyhao.22 with_iwan` 的轻量管理面板。

## 主要功能

- Python 轻量后端，无 Node.js、无 Docker
- 手机、iPad、电脑自适应
- iWAN 入口管理
- 固定分流：国内、AI、Google、YouTube、Netflix、TikTok、Telegram、默认出口
- 配置写入前自动执行 `sing-box check`
- 检查通过后优先 reload，失败时自动 restart
- SQLite 登录与 30 天会话
- 节点重命名、删除和端口连通测试

## 一键导入节点

节点页面点击 **一键导入**，直接粘贴以下任意内容，后台会自动识别、解析、去重、保存并应用：

- Shadowsocks：`ss://`
- VMess：`vmess://`
- VLESS：`vless://`
- Trojan：`trojan://`
- TUIC：`tuic://`
- Hysteria2：`hy2://`、`hysteria2://`
- Clash YAML 订阅
- sing-box JSON 订阅或完整配置中的 `outbounds`
- Base64 编码订阅
- HTTP/HTTPS 订阅地址
- 一次粘贴多条分享链接

导入时默认保留已有节点并自动跳过重复项。勾选“清空现有节点后再导入”可替换全部节点。订阅内容最大为 4 MB，面板请求内容最大为 6 MB。

> Clash YAML 解析使用 Debian 的 `python3-yaml` 软件包，一键安装脚本会自动安装。

## 一键安装或覆盖升级

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/zhuvps1-hub/sing-box-ricky-installer/main/install.sh)
```

重新执行同一条命令会更新面板程序和 sing-box 核心，并保留：

- 面板账号和密码
- 已导入节点
- 分流设置
- iWAN 设置
- `/etc/iwan-gateway` 中的数据和备份

默认面板端口：`8088`

首次登录：

```text
用户名：admin
密码：admin
```

登录后请立即修改密码。

## 文件位置

```text
/opt/iwan-gateway/app/app.py          面板后端
/opt/iwan-gateway/app/importers.py    节点与订阅解析器
/etc/iwan-gateway/gateway.db          面板数据
/etc/iwan-gateway/backups             配置备份
/etc/sing-box/config.json             实际 sing-box 配置
```

## 服务命令

```bash
systemctl status iwan-gateway
systemctl status sing-box
journalctl -u iwan-gateway -n 100 --no-pager
journalctl -u sing-box -n 100 --no-pager
```

## 安全说明

- 订阅下载仅允许 HTTP/HTTPS。
- 节点密码、UUID、Reality 公钥等完整参数只保存在服务器 SQLite 中，节点列表接口仅返回协议、名称、服务器和端口。
- 所有节点变更都会先生成临时配置并通过核心检查；检查失败不会覆盖当前配置。
- 面板具有修改系统代理配置的权限，请修改默认密码并使用防火墙限制面板访问来源。

## 分流说明

国内规则使用远程 `geosite-cn` 与 `geoip-cn` 二进制规则集；其余主要业务使用内置域名集合，Telegram 同时包含常见 IP 网段。默认出口对应 sing-box `route.final`。
