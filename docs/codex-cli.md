# Codex CLI 会话接入（v2.1.1）

聊天列表纳入现有 Codex CLI 会话。列表始终在日期上方显示会话来源，可按 CLI、VS Code 或 APP 筛选；来源取自会话的原始记录，接续后的运行方不改变来源。筛选先于分页，搜索或翻页不会让来源选项消失。打开 CLI 聊天先读已保存历史，并只读查找已有 Codex App 执行连接；不启动 CLI、不向桌面 App 跳转，也不发送模型请求。Codex App 已配置的有效 SSH 连接会参与发现；远端通过用户登录 shell 读取其 `CODEX_HOME`，未设置时使用 `~/.codex`。

本机默认目录中共存的 APP、CLI 和 VS Code 历史均可从默认入口查看，也可用对应来源筛选。独立配置只展示自己的历史，无需将默认目录重复登记。普通 SSH 与 WSL 保留各自的来源范围。VS Code 历史的读取和显式继续沿用原会话身份核验；从通知或深链接打开尚未加载的 CLI / IDE 会话也只读取历史，不自动恢复。列表显示完整并不代表缺失的历史文件或原生运行时已经恢复可用。

## 继续 CLI 中的工作

1. 在手机或 Web 中打开带 `CLI` 标记的聊天，先阅读已保存历史。
2. 如果这条聊天已由 Codex App 接续，网关会核验并复用该执行连接。否则点击“继续会话”，连接原 CLI daemon；原终端可以已经关闭，必要时显式启动该 `CODEX_HOME` 中已安装的原生托管 daemon，再恢复同一条会话。
3. 连接成功后可发送、排队、补充任务、停止，以及处理支持的审批和问题卡片；也可选择模型与推理强度、添加 Skill、切换普通/计划模式。兼容的本机和 SSH 原生 CLI 运行时支持 Goal 的创建、暂停、修改、恢复与关闭。任务继续使用原会话的提供商、认证与工作目录。

只有点击“继续会话”才会启动托管 daemon 或恢复会话；打开聊天、读取历史和列表刷新都不会触发。网关不会安装 CLI、接受新的 SSH 主机密钥、强行夺取其他进程的写入权，或覆盖原会话身份。原生 daemon 未安装、版本不兼容或身份核验失败时，历史仍可阅读，并显示连接失败原因。

SSH 使用已有主机配置和密钥校验，不自动接受新主机密钥或安装远端程序。

由 Codex App 接续的 CLI 聊天复用现有 App 的模型、Skill 和模式操作；由原生 CLI 执行的聊天从同一个 daemon 读取目录并提交设置、Skill 和计划请求。自定义 API 接入可读取上游模型列表；环境变量认证使用经核验的原 CLI 后台服务启动环境，SSH 查询在原服务器执行，不混用网关电脑的凭据。上游未提供的推理强度只按相同模型 ID 的原生元数据补充，不根据模型名称猜测。上游不提供列表时仍可手动填写模型 ID。兼容的本机和 SSH 原生 CLI 通过同一 daemon 操作 Goal；SSH 上由 Codex App 接管的聊天尚不支持 Goal，网关不会另起执行方。连接成功后目录自动加载，短暂断线保留已选 Skill 与输入草稿。

模型列表按主机、CLI 配置、提供商与认证身份分别缓存，显示上次成功更新时间。重复打开优先读取缓存；手动刷新、接入身份变化或到达 12 小时刷新周期时重新查询。相同接入刷新失败时保留上次成功列表与时间，身份变化则不复用旧结果。查询需要原服务器能访问其 API；网关不会为此修改 SSH 代理或网络配置。

CLI 权限可从聊天中的权限按钮切换；原生 CLI 按执行主机的运行时与策略展示可用选项，App 接续的聊天通过原 owner 更新。保存后从后续回合生效，界面以原生确认结果为准；聊天按钮将完全访问权限简写为“完全访问”，确认说明保持完整。消息编辑/分支、重命名和临时侧边聊天暂不开放。网关关闭或手机页面退出只断开网关连接，不停止 CLI 任务。连接中断或写入结果不确定时不自动重发；先查看原会话历史与运行状态，再决定后续操作。

Codex CLI 接入在 Codex 账号管理中保存；Claude CLI 接入在 Claude 账号管理中保存，两者不会混用配置。桌面切换可选择同时应用到本机 CLI，分别核验桌面和 CLI 结果；Codex 共享配置的影响会明确提示，SSH 配置不随本机切换。Claude Code CLI 可从已有本机或 SSH 项目创建聊天，保留所选配置与目录；这不等于接管任意正在另一终端运行的 Claude 进程。Claude Code 支持关闭后恢复、新建、完整回复、模型与权限、Skill、上传、审批和中断。已知容量显示蓝色圆环，未知容量保持中性，不从累计计费 Token 猜测上下文占比。

## 新建与被动阅读

在 Codex 新建聊天中选择 CLI，可使用已有本机或 SSH CLI 会话记录中的项目目录；新建仅创建会话，不发送第一条消息。读取项目列表不激活会话，提交创建时才连接原生运行时。来源标记保持 CLI，即使之后由桌面 App 接续也不改变。

名称为可选项，默认留空。创建时仅对手动填写的名称调用 `thread/name/set`；空名聊天保持原生未命名状态。界面依次使用原生名称、原生内容预览和“未命名聊天”，并接收 `thread/name/updated` 更新。生成标题由原生客户端负责；网关不创建标题临时会话、不发送额外模型请求，也不维护自动命名回执。具体行为见[可选名称记录](../.agents/notes/implemented/feature/2026-11-16-codex-optional-titles.md)。

停留在尚未连接的 Codex CLI 聊天页时，后台会继续读取终端追加的保存历史，不必为了查看新回复先点击“继续会话”。本机与 SSH 的刷新节奏不同，不承诺即时或固定延迟。模型标签按原生回合或保存记录显示；缺少证据时不猜测。后台只读刷新短暂超时不会直接丢弃仍有效的执行连接，真正断开或身份核验失败仍会使连接失效。

## 平台与运行要求

macOS、Windows 与 Linux 支持本机 CLI 接入；SSH 使用服务器上已安装并配置的 CLI。仅安装 Codex MSIX 不等于具备原生 CLI 托管目录。Linux Claude Code CLI 与 Claude Desktop 自动连接是不同能力，后者仍仅适用于 macOS / Windows。

Windows 保存历史读取和冷恢复可识别普通路径与扩展路径指向的同一目录，保持原有文件访问边界。旧版 Claude 进程记录缺少启动标识时，只有确认进程已退出才能恢复；活跃或身份不确定的进程仍不可接管。

## 会话缺失时

从“设置”中的“SSH 连接诊断”查看本机和远端的读取阶段、状态与会话来源计数，可复制摘要或导出 `codex-session-diagnostics.json`。旧版手机 App 可查看和复制；文件导出需更新 App。登录后才能读取诊断。摘要和导出不包含聊天正文、标题、项目路径、SSH 别名或凭据；远端以 `remote-1` 等编号表示。顶部 SSH 错误可关闭：同一故障在自动刷新时不再出现，恢复或错误变化后可重新提示；页面重新加载会重置关闭状态。

调查某一条会话时，可在已登录的网关地址后访问 `/api/sessions/diagnostics?thread=<会话 UUID>`。额外结果区分数据库中不存在、来源不支持、已归档、内部子任务以及历史文件缺失/不可读等情况；导出不回显该 UUID。其他用户电脑不可达时，需要对方提供此诊断，才能区分 SSH、数据目录和会话过滤问题。

## 原生协议边界

当前官方 app-server 接口仍可能变化。显式接入前会核验运行环境与会话身份；冷恢复还会比较已保存的来源、提供商和工作目录。0.162.1 的 `thread/resume` 没有原子化“仅恢复经核验会话”选项：若会话恰在核验与恢复之间变化，不能宣称具有绝对无竞态的 owner 保证。被动阅读不会执行此操作。

## English

The default local entry includes App, CLI and VS Code histories stored in the same `CODEX_HOME`. Independent configurations keep their own histories; SSH and WSL retain their existing source scopes. Opening a CLI or IDE notification or deep link reads history without automatically resuming the session, even when its list row has not loaded yet. Continuing still requires the existing runtime and session identity checks.

Source labels remain visible above dates; type filters include CLI, VS Code and APP. Opening a CLI chat reads saved history and passively checks for an existing Codex App owner. If App already owns the chat, Bridge verifies and follows that owner. Otherwise, select **Continue chat** to connect to the native CLI daemon or explicitly start its already installed, managed daemon and resume the same chat, even if the terminal has closed. It does not install CLI, bypass SSH host-key checks, change the chat's identity, or seize another runtime's writer lock. Supported actions include send, queue, steer, interrupt, approvals and input requests, plus model/effort selection, Skills and default/plan modes. Compatible joined native CLI runtimes, including SSH hosts, support creating, pausing, editing, resuming and clearing Goals through the same daemon. SSH chats owned by the desktop App do not expose Goal controls.

Native CLI controls use the same daemon for catalogs, settings, Skills, modes and Goals. Custom API providers can list upstream models using the verified daemon's launch credentials. SSH queries run on the original host without borrowing the gateway computer's credentials. Missing effort metadata is supplemented only for exact native model IDs. Manual model IDs remain available when the upstream does not expose a catalog. Catalogs are cached separately by host, CLI configuration, provider and authentication identity, with a last-success timestamp. Manual refresh, identity changes and the 12-hour refresh cycle query the upstream again; a failed refresh retains the previous list only for the same identity. Bridge does not configure a proxy for the remote host. Transient disconnects retain draft text and selected Skills. Permission controls follow the execution runtime and its policy on local and SSH CLI chats; App-owned chats use their existing owner. Changes apply to subsequent turns and require effective runtime confirmation. Editing/forking, rename and side chats remain unavailable for CLI-origin chats.

Codex and Claude CLI access have separate account sections and configurations. Desktop switches offer opt-in local CLI synchronization and separate verification results. Codex shared-configuration effects are stated explicitly; SSH settings remain on their hosts. Claude Code CLI chats can be created from existing local or SSH projects with their selected configuration and directory; this does not claim control of an arbitrary live Claude terminal. Claude Code supports closed-session resume, creation, replies, model/permission controls, Skills, uploads, approvals and interrupt. Unknown context capacity remains neutral rather than inferring a percentage from billed tokens.

Open **SSH diagnostics** in Settings to read local and remote status and counts, copy the summary, or export a file. Older native apps can read and copy but need an update to export. Diagnostics require login and omit chat text, titles, paths, aliases and credentials. Dismissing an SSH error suppresses the same fault across automatic refreshes until recovery or a changed fault; reloading the page resets that dismissal. Unknown writes are not automatically replayed. The upstream resume API has no atomic identity guard across preflight and resume.

Local CLI access is available on macOS, Windows and Linux; SSH hosts use their own installed and configured CLI. Windows history and cold resume recognize equivalent extended paths without relaxing file boundaries. Confirmed exited legacy Claude writers can be resumed; active or unverifiable writers remain protected. Linux Claude Code CLI support does not add Claude Desktop automatic connection.

For new Codex chats, choose App or CLI and select a known local/SSH CLI project folder. CLI creation sends no initial message. Names are optional and blank by default; only an explicit name calls `thread/name/set`. The interface uses the native name, then the native preview, then “Unnamed chat”, and follows native name updates. Bridge does not create temporary naming sessions, send extra model requests, or store automatic-naming receipts. Passive reading continues to follow saved history appended by a terminal without claiming its writer; local and SSH refresh cadence differs. Per-turn model labels require native or saved evidence, and transient read timeouts do not discard a still-valid execution connection.
