# iWAN Gateway v6.6

轻量管理现有 iWAN、sing-box 多落地分流和 mosdns，适用于 Debian、Ubuntu、Rocky Linux、AlmaLinux 等使用 systemd 的 Linux 服务器。

## 完整教程

从全新 VPS、已有底层只装面板、节点导入、独立业务分流、iWAN 账号修改、mosdns、升级回滚到故障排查，都已整理到：

**[完整安装、升级与使用教程](docs/完整安装与使用教程.md)**

## 快速开始

### 全新 VPS

先安装 sing-box iWAN 核心：

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/zhuvps1-hub/sing-box-ricky-installer/main/install.sh)
```

再安装 Web 面板：

```bash
bash <(curl -fsSL "https://raw.githubusercontent.com/zhuvps1-hub/sing-box-ricky-installer/main/install-web.sh?ts=$(date +%s)")
```

### 已有 sing-box / iWAN，只安装或升级面板

```bash
bash <(curl -fsSL "https://raw.githubusercontent.com/zhuvps1-hub/sing-box-ricky-installer/main/install-web.sh?ts=$(date +%s)")
```

默认访问：

```text
http://你的VPS公网IP:8088
```

## v6.6 重点

- 底层服务与 Web 面板完全分离；
- 面板升级不安装、不覆盖、不重启 sing-box 和 mosdns；
- 节点和分流采用可靠自动保存；
- 保存请求先原子暂存，再由后台校验、备份、应用和回滚；
- 网络短暂断开时自动确认和重试，不重复执行；
- 面板进程重启后可恢复未完成的保存任务；
- Netflix、AI、YouTube、Telegram、其他流量完全独立选择节点；
- iWAN 在独立页面“保存并重连”；
- mosdns 在独立页面“保存并重启”；
- 支持 `ss://`、节点 JSON 和 sing-box `outbounds` 导入；
- 深色、浅色、跟随系统，手机、iPad 和电脑自适应。

## 稳定升级机制

面板安装器采用：

```text
固定提交 Raw 下载
→ Git Blob 逐文件校验
→ Python 语法检查
→ 内置自检
→ 隔离候选进程
→ 健康、登录和配置采样测试
→ 原子切换
→ 正式健康检查
→ 失败自动恢复旧版
```

候选版本通过之前，当前面板不会停止，sing-box 和 mosdns 不受影响。

## 常用命令

```bash
systemctl status sing-box iwan-gateway mosdns --no-pager
curl -s http://127.0.0.1:8088/healthz
journalctl -u iwan-gateway -n 100 --no-pager
```

## 主要路径

```text
sing-box 配置：/etc/sing-box/config.json
mosdns 配置：/etc/mosdns/config.yaml
面板当前版本：/opt/iwan-gateway-panel/current
面板版本目录：/opt/iwan-gateway-panel/releases
面板账号：/etc/iwan-gateway/auth.json
面板数据：/var/lib/iwan-gateway
自动保存暂存：/var/lib/iwan-gateway/autosave-pending.json
```

## 卸载面板

```bash
bash <(curl -fsSL "https://raw.githubusercontent.com/zhuvps1-hub/sing-box-ricky-installer/main/install-web.sh?ts=$(date +%s)") uninstall
```

只删除面板程序和 systemd 服务，保留 sing-box、mosdns、节点、密码、分流配置和备份。

> 不要把真实节点密码、iWAN 密码或完整生产配置提交到公开仓库。公开部署时请显式设置自己的服务器地址、端口、协议和密码。
