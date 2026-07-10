# iWAN Gateway v5.1 轻量版

适用于 Debian、Ubuntu、CentOS、Rocky Linux 和 AlmaLinux 的 systemd 系统。内置 sing-box `1.13.13-rickyhao.22`（检查 `with_iwan`），支持 mosdns v5。

Web 面板仅使用 Python 标准库，不依赖 Docker、npm、Node.js、pip 或外部 CDN。

## 一键安装或升级

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/zhuvps1-hub/sing-box-ricky-installer/main/install-web.sh)
```

默认地址：

```text
http://你的VPS公网IP:8088
```

安全组放行：

```text
8000/TCP+UDP   iWAN 入口
8088/TCP       Web 面板
```

## v5.1 重点改进

- 安装包全部下载、解码、SHA256 校验和 Python 语法检查完成后，才停止旧面板
- 切换过程仅中断几秒；新版健康检查失败会自动恢复旧程序和旧 systemd 服务
- 修复分片缺失导致 `404` 后旧面板被提前停止的问题
- 深色、浅色、跟随系统三种主题，自动记住选择
- 手机、iPad、电脑自适应；移动端使用卡片布局，减少横向滚动
- 单一 Python 后台进程，状态短时缓存，页面隐藏时暂停刷新
- 节点延迟按需检测，不启动额外常驻测速服务
- 日志、路由、端口和 nftables 仅在打开对应页面时读取

## 节点与分流

支持新增、编辑、启用、禁用、删除 Shadowsocks 节点，并导入：

- 多行 `ss://`
- 节点 JSON 数组
- sing-box 配置中的 `outbounds`

每类业务可自由选择出口：

```text
国内网站        → direct
Netflix         → 任意已启用节点
ChatGPT/Claude  → 任意已启用节点
YouTube         → 任意已启用节点
Telegram        → 任意已启用节点
其他流量        → 任意默认节点
```

保存时自动执行 `sing-box check`，自动备份、原子写入、重启；失败时恢复旧配置。

## 服务与文件

```text
面板服务：iwan-gateway.service
面板程序：/opt/iwan-gateway
面板配置：/etc/iwan-gateway
sing-box：/etc/sing-box/config.json
mosdns：/etc/mosdns/config.yaml
备份目录：/etc/sing-box/backups
```

检查命令：

```bash
systemctl status iwan-gateway sing-box mosdns --no-pager
curl -s http://127.0.0.1:8088/healthz
journalctl -u iwan-gateway -n 100 --no-pager
```

卸载面板（保留配置）：

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/zhuvps1-hub/sing-box-ricky-installer/main/install-gateway.sh) uninstall
```
