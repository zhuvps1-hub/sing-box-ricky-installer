# iWAN Gateway 1.0

基于 Ricky-Hao `sing-box 1.13.13-rickyhao.22 with_iwan` 的轻量管理面板。

## 特点

- Python 标准库后端，无 Node.js、无 Docker
- 手机、iPad、电脑自适应
- Shadowsocks 落地节点管理与 TCP 延迟测试
- 固定分流：国内、AI、Google、YouTube、Netflix、TikTok、Telegram、默认出口
- 点击应用后自动生成配置、快速 reload，必要时自动 restart
- iWAN 入口管理
- SQLite 登录与 30 天会话

## 一键安装

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/zhuvps1-hub/sing-box-ricky-installer/main/install.sh)
```

默认面板端口：`8088`

首次登录：

```text
用户名：admin
密码：admin
```

登录后请立即修改密码。

## 文件位置

```text
/opt/iwan-gateway             面板程序
/etc/iwan-gateway/gateway.db  面板数据
/etc/iwan-gateway/backups     配置备份
/etc/sing-box/config.json     实际 sing-box 配置
```

## 服务命令

```bash
systemctl status iwan-gateway
systemctl status sing-box
journalctl -u iwan-gateway -n 100 --no-pager
journalctl -u sing-box -n 100 --no-pager
```

## 说明

国内规则使用远程 `geosite-cn` 与 `geoip-cn` 二进制规则集；其余主要业务使用内置域名集合，Telegram 同时包含官方常见 IP 网段。默认出口对应 sing-box `route.final`。
