# CLI 空标题创建与完成回执

Status: implemented

## 故障与反馈循环

本机 Codex CLI 留空名称创建时，现场运行时的 `thread/read(includeTurns=true)` 返回 `AppServerUnsupported: list_turns is not supported yet`。异常进入 HTTP 500，页面显示网关错误。`thread/start` 已返回的 ID 此前已经保存到 `creations.json`；同 UUID 的第二次提交直接复用 ID 报成功，却跳过新会话初始化。新线程尚未被持久化列表发现时，打开和手动发送均失败，原生端没有收到首条消息。

`python -B scripts/test-backend.py --source-only -p test_cli_empty_creation.py` 在修复前复现同一异常。合成完整流程进一步确认首次创建错误、再次假成功、打开/发送 `KeyError`、一次 `thread/start` 和零次 `turn/start`。夹具只替代原生 I/O，保留真实 Bridge 创建、读取和发送调用。

## 修复边界

CLI 完整读取仅在明确匹配上述原生 Unsupported 时降级为 `includeTurns=false`，重新核对原连接、加载状态、ID、工作目录和 provider。其他错误、断连和结果未知均不作为兼容成功。元数据读取保留现有实时 turns，并标记历史未完整加载；原生保存历史继续通过已有历史合并流程展示。

创建回执的 `phase=created` 只表示已知新 ID；本机安装可用会话后才写 `phase=ready`，保存实际 `runtimeCwd` 与 provider。已有 ID 的 pending 重试只读取同一个已核验的原生连接，不重放 `thread/start`、`thread/name/set`、模型设置或消息。人工名称或模型未确认时继续保留回执并提示核对。丢失原连接的 pending 回执不能自动恢复；已完成回执只有匹配 ID、CLI 来源、原目录和 provider 的持久化元数据才可继续报告可读。

`CliSessions.require_current()` 在网络读取前后核验 process 与 connection token，再即时核对原生身份及明确返回的名称/模型设置。网络不持有安装锁；短期保护本地 LiveSession 安装，避免断连回调在新会话尚未注册时遗漏失效。新建与 CLI 恢复共用该安装流程，保留已有 Desktop owner。

独立 `codex:` 接入在库存缺失时，仅允许已有、已核验的 CLI 原生连接接受即时身份检查；未验证会话和已确认的 Desktop 来源继续拒绝。没有放宽通用库存、文件、认证、CSRF、账号或 provider 边界。没有修改 App、WSL 的独立创建协议。

## 验证

- 新增 CLI 原生兼容及身份回归 12 项，完成回执和共用调用链回归 13 项，通过。覆盖首条消息只发一次、未知结果不重放、在途较新名称/设置、历史不丢失、断连/重连/身份变化、人工名称未知、模型错配、独立入口和原 Desktop owner 保留。
- 浏览器实际运行创建表单、同 UUID 重试、`openChat`、timeline 和 composer 发送；Chromium/WebKit 的宽窄四组合，以及相邻空标题用例共 8 项通过。
- Node 全量 1,540 项，1,539 通过，1 项按环境跳过。模块边界与固定资源检查通过。
- 已有项目环境的完整后端 source-only 回归 3,509 项，通过，48 项按源码或平台条件跳过。进一步收紧库存夹具，让未保存的新线程的库存与保存历史同时不可用，原生身份仍允许真实 timeline 打开和首条发送；相关 25 项再次通过。

默认测试 Python 缺少 `aiohttp` 与 `cryptography`，初次全量 source-only 回归出现 Claude 连接夹具失败及 ZCode 测试导入失败。两项均由 `cryptography` 缺失引起；Claude 精准用例在当前代码和干净 HEAD 都表现为缺依赖环境失败、已有项目环境通过。完整后端验收使用既有 `.tmp/session-recovery-venv/bin/python`，不为测试安装依赖或改变产品行为。

验证使用合成原生数据及只读现场探测，不代表已执行真实新建、模型消息、Windows/SSH/WSL 客户端联调或打包发布。

## 运行中网关

源码验证完成后，即时核对本机 systemd 网关的启动身份、完整 cgroup、控制记录、账号切换和登录状态。网关没有自有终端或命令任务，`KillMode=process`；原生 Codex daemon、updater 及其任务位于其他 cgroup。控制读取可能启动短暂的元数据探测，等待其自然结束后再次核验，没有向它们发送信号。

已重新加载 `codex-mobile-bridge.service`。本机健康接口确认新的网关实例与新控制记录一致，原生 daemon/updater 的进程启动身份、Codex 认证和配置以及网关配置未变。没有调用真实新聊天创建或发送模型任务。后端 Python 修复现已由运行中的网关加载，页面可刷新后继续使用。
