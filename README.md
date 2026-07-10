# sing-box Ricky iWAN 一键安装 + Web 管理面板

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
        ├─ Netflix / AI → 指定落地节点
        └─ 其他流量      → 指定默认节点
```

初始预设：

- iWAN 用户名：`hkl`
- iWAN 端口：`8000`
- 地址池：`10.10.10.0/24`
- HKT：`hkboil.ddos.top:24895`
- SG：`217.116.172.44:22222`
- Netflix、OpenAI、Claude、Gemini、GitHub Copilot 等走 SG
- 国内域名和国内 IP 直连
- 其他流量默认走 HKT

密码不会保存在公开仓库，只保存在 VPS 本地，并设置为仅 root 可读。

## 全新 VPS：一键安装 sing-box + Web 面板

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/zhuvps1-hub/sing-box-ricky-installer/main/install-web.sh)
```

安装过程会让你输入：

1. iWAN 用户密码
2. HKT Shadowsocks 密码
3. SG Shadowsocks 密码
4. Web 面板用户名、端口和登录密码

默认 Web 面板端口：`8088`。

安装完成后访问：

```text
http://你的VPS公网IP:8088
```

需要在云服务商安全组放行：

- `8000/TCP+UDP`：iWAN
- `8088/TCP`：Web 面板

为了安全，建议 `8088/TCP` 只允许你自己的公网 IP 访问。

## 已经安装 sing-box：只安装 Web 面板

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/zhuvps1-hub/sing-box-ricky-installer/main/install-panel.sh)
```

面板会自动读取当前 `/etc/sing-box/config.json`，不会要求重新填写现有节点密码。

## Web 面板功能

- 登录鉴权和修改面板密码
- 修改 iWAN 监听地址、端口、地址池、用户名和密码
- 手动添加、修改、删除 Shadowsocks 落地节点
- 批量导入多行 `ss://` 节点链接
- 导入节点 JSON、sing-box 完整配置中的 `outbounds`
- 指定 Netflix / AI 使用哪个落地节点
- 指定其他流量的默认落地节点
- 添加自定义域名分流规则
- 查看 sing-box 运行状态和最近日志
- 保存前运行 `sing-box check`
- 自动备份旧配置、应用新配置并重启服务
- 新配置启动失败时自动恢复旧配置

配置文件：

```text
/etc/sing-box/config.json
/etc/sing-box/panel.json
/etc/sing-box-panel/auth.json
```

## 仅安装命令行版本

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/zhuvps1-hub/sing-box-ricky-installer/main/install.sh)
```

## 常用命令

```bash
systemctl status sing-box --no-pager
journalctl -u sing-box -f
systemctl restart sing-box

systemctl status sing-box-panel --no-pager
journalctl -u sing-box-panel -f
systemctl restart sing-box-panel
```

## 一键卸载 sing-box

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/zhuvps1-hub/sing-box-ricky-installer/main/install.sh) uninstall
```

卸载 sing-box 时会保留 `/etc/sing-box` 配置目录。Web 面板可手动停止并删除：

```bash
systemctl disable --now sing-box-panel
rm -f /etc/systemd/system/sing-box-panel.service
rm -rf /opt/sing-box-panel /etc/sing-box-panel
systemctl daemon-reload
```
