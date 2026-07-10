# F7010U iWAN Gateway v2.0.0

适用于 **Debian / Ubuntu / CentOS / Rocky Linux / AlmaLinux** 的 systemd 系统，仅支持 **amd64 / x86_64**。

内置 `sing-box 1.13.13-rickyhao.22`，安装时检查 `with_iwan` 标签。Web 面板使用 Python 标准库，不依赖 npm、Docker 或 pip。

## 一键安装或升级

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/zhuvps1-hub/sing-box-ricky-installer/main/install-web.sh)
```

- 全新 VPS：先安装 sing-box iWAN 和初始分流，再安装完整 Web 面板。
- 已安装 sing-box：自动读取 `/etc/sing-box/config.json`，保留现有 iWAN、节点和密码。
- 已安装旧版面板：停止旧服务并升级为 `f7010u-gateway.service`。
- 再次运行：升级面板程序，保留面板账号、历史流量、备份和 sing-box 配置。

默认面板地址：

```text
http://你的VPS公网IP:8088
```

云服务商安全组需要放行：

- `8000/TCP+UDP`：iWAN 入口
- `8088/TCP`：Web 面板

面板目前使用 HTTP。建议将 `8088/TCP` 只开放给自己的固定公网 IP，或在前面配置 HTTPS 反向代理。

## 当前初始架构

```text
手机（Panabit App）
        ↓
深圳 Panabit 网关
        ↓
本 VPS：sing-box iWAN 服务端
        ├─ 国内域名/IP     → direct
        ├─ Netflix / AI   → 指定落地
        └─ 其他流量        → 指定默认落地
```

初始值：

- iWAN 用户名：`hkl`
- iWAN 端口：`8000`
- 地址池：`10.10.10.0/24`
- HKT：`hkboil.ddos.top:24895`
- SG：`217.116.172.44:22222`

所有密码只写入 VPS 本地配置，不保存在公开仓库。

## 完整版功能

### 仪表盘

- iWAN、sing-box、mosdns 实时服务状态
- CPU、内存、负载、系统运行时间
- 实时上传/下载速度
- 今日、本月和累计流量
- 历史流量折线图
- 节点延迟与可用状态
- 最近系统事件和服务日志
- 快速重启服务、刷新状态和创建备份

### iWAN 与节点

- 修改 iWAN 监听地址、端口、地址池、用户名和密码
- 多用户管理
- 添加、编辑、启用、禁用和删除 Shadowsocks 节点
- 导入多行 `ss://` 链接
- 导入节点 JSON 或 sing-box `outbounds`
- 节点 TCP 延迟测试和历史结果

### 分流路由

- 国内域名和国内 IP 直连
- Netflix、OpenAI、Claude、Gemini、Copilot 独立选择出口
- 默认出口选择
- 自定义域名、IP/CIDR 和端口规则
- 规则启用、禁用、排序和删除
- 保存前执行 `sing-box check`
- 自动备份、原子写入、重启和失败回滚

### mosdns、网络与系统

- 识别已有 mosdns 服务
- 查看和编辑 mosdns YAML 原始配置
- 保存前备份，重启失败时恢复
- 查看系统网卡、路由、监听端口和 nftables 规则
- 查看 sing-box、mosdns、面板和系统日志
- 配置备份、恢复和下载
- 修改面板登录密码

> mosdns 页面管理服务器上已经安装的 mosdns；当前安装器不会替你自动安装 mosdns 二进制。
>
> 节点延迟为 TCP 建连测试，不等同于 Netflix 解锁或代理出口质量测试。

## 服务与文件

```text
面板服务：f7010u-gateway.service
面板程序：/opt/f7010u-gateway
面板配置：/etc/f7010u-gateway
历史数据：/var/lib/f7010u-gateway/gateway.db
sing-box：/etc/sing-box/config.json
备份目录：/etc/sing-box/backups
```

常用命令：

```bash
systemctl status f7010u-gateway --no-pager
journalctl -u f7010u-gateway -f
systemctl restart f7010u-gateway

systemctl status sing-box --no-pager
journalctl -u sing-box -f
systemctl restart sing-box
```

## 只安装命令行版 sing-box

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/zhuvps1-hub/sing-box-ricky-installer/main/install.sh)
```

## 卸载面板

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/zhuvps1-hub/sing-box-ricky-installer/main/install-gateway.sh) uninstall
```

卸载面板时保留登录配置、流量历史、备份和 sing-box 配置。
