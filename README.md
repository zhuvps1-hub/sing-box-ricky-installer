# sing-box Ricky iWAN 一键安装脚本

适用于 **Debian / Ubuntu / CentOS / Rocky Linux / AlmaLinux** 的 systemd 系统，仅支持 **amd64 / x86_64**。

内置版本：`sing-box 1.13.13-rickyhao.22`，安装时会检查 `with_iwan` 标签。

## 当前架构

```text
手机（Panabit App）
        ↓
深圳 Panabit 网关
        ↓
本 VPS：sing-box iWAN 服务端，监听 8000
        ├─ 国内网站      → VPS 直连
        ├─ Netflix / AI → 新加坡落地
        └─ 其他流量      → HKT 落地
```

当前预设：

- iWAN 用户名：`hkl`
- iWAN 端口：`8000`
- 地址池：`10.10.10.0/24`
- HKT：`hkboil.ddos.top:24895`
- SG：`217.116.172.44:22222`
- Netflix、OpenAI、Claude、Gemini、GitHub Copilot 等走 SG
- 国内域名和国内 IP 直连
- 其他流量默认走 HKT

密码不会保存在公开仓库。安装时会在终端中隐藏输入并写入 `/etc/sing-box/config.json`。

## 一键安装

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/zhuvps1-hub/sing-box-ricky-installer/main/install.sh)
```

运行后依次输入：

1. iWAN 用户密码
2. HKT Shadowsocks 密码
3. SG Shadowsocks 密码

安装脚本会自动：

- 下载并校验 sing-box
- 写入完整 iWAN 和分流配置
- 备份旧配置
- 创建 systemd 服务
- 设置开机自启和异常重启
- 尝试放行 `8000/TCP+UDP`
- 检查配置并确认服务成功启动

还需要在云服务商安全组中放行 `8000/TCP+UDP`。

## 常用命令

```bash
systemctl status sing-box --no-pager
journalctl -u sing-box -f
nano /etc/sing-box/config.json
systemctl restart sing-box
```

## 一键卸载

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/zhuvps1-hub/sing-box-ricky-installer/main/install.sh) uninstall
```

卸载时会保留 `/etc/sing-box` 配置目录。
