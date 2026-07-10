# sing-box Ricky 一键安装脚本

适用于 **Debian / Ubuntu / CentOS / Rocky Linux / AlmaLinux** 的 systemd 系统，仅支持 **amd64 / x86_64**。

内置版本：`sing-box 1.13.13-rickyhao.22`

## 一键安装

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/zhuvps1-hub/sing-box-ricky-installer/main/install.sh)
```

默认配置会在本机监听 SOCKS5：`127.0.0.1:1080`。

配置文件：`/etc/sing-box/config.json`

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
