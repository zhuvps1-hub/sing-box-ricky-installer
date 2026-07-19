# iWAN Gateway v9 Architecture

## 目标

v9 不再继承 `runtime_v712 -> runtime_v8` 补丁链。运行时只有一个入口、一个领域模型、一个配置生成器和一个状态读取器。

## 单一事实源

- 用户期望配置：`/var/lib/iwan-gateway/state.json`
- sing-box 实际配置：`/etc/sing-box/config.json`
- 保存成功的定义：候选配置校验通过、原子写入成功、sing-box 重启成功、配置回读一致、iWAN 端口真实监听。
- 页面状态永远从实际配置和系统状态生成，禁止从浏览器表单或缓存推断。

## 进程边界

### Web/API（非 root）

- 只处理认证、输入校验和展示。
- 不直接写 `/etc/sing-box`。
- 通过 Unix Socket 调用控制进程。

### Control helper（root）

只开放固定 RPC：

- `state.get`
- `state.apply`
- `state.rollback`
- `service.status`
- `logs.read`

不接受任意命令、任意路径或 shell 文本。

## 保存事务

1. 校验 API payload。
2. 生成规范化 `DesiredState`。
3. 使用单一 renderer 生成完整 sing-box 配置。
4. `sing-box check -c candidate.json`。
5. 备份当前配置。
6. 原子替换配置。
7. 重启 sing-box。
8. 检查 systemd active。
9. 从 `/etc/sing-box/config.json` 回读。
10. 检查 iWAN TCP/UDP 监听端口。
11. 对比回读状态与 DesiredState。
12. 全部成功后才提交 `state.json` 并返回 200；任何一步失败自动回滚。

## 分流模型

- 规则首项必须是 `{"action":"sniff"}`。
- 业务规则必须使用 `{"action":"route","outbound":"..."}`。
- 默认出口写入 `route.final`。
- renderer 输出后使用实际 sing-box 核心执行校验。

## UI 状态语义

- `已保存`：仅代表最近一次事务完成且回读一致。
- `未应用`：表单与实际配置存在差异。
- `服务在线`：systemd active。
- `iWAN 监听`：TCP/UDP 套接字真实存在。
- `客户端在线`：存在已建立连接，不与服务在线混淆。

## 兼容与迁移

- 首次启动读取旧 sing-box 配置并导入 DesiredState。
- 旧 runtime 文件不参与 v9 运行，只保留回滚用途。
- 未识别字段默认保留在 passthrough 区域，避免破坏人工高级配置。

## 发布门禁

稳定发布必须同时通过：

- 领域模型单元测试。
- renderer 快照测试。
- 真实 sing-box `check`。
- 临时 systemd/进程集成测试。
- 保存、回读、回滚故障注入。
- TCP/UDP iWAN 监听验证。
- 节点和分流端到端测试。
- 登录 Cookie 重启后持久化测试。
- 移动端 Playwright 截图回归。

任何门禁失败，不更新稳定安装入口。
