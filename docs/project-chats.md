# 新建项目聊天 / Create a project chat

在 Web 或手机的聊天列表点击 **新建**，先选择运行环境，再选择已有项目或点击 **选择目录**。目录选择器可以打开其他文件夹，也可以展开 **新建文件夹**，在选定的父目录中创建项目文件夹。不要求该目录以前有过聊天，不会初始化 Git 或覆盖已有文件。

只有一个运行环境时直接显示其名称；多个环境时可以切换。项目路径默认显示末两级目录，展开后可查看完整路径。更换环境会清除尚未确认的目录选择，避免把另一台电脑或另一种接入的目录带入新聊天。

| 接入 | 项目目录所在位置 | 最后一步 |
| --- | --- | --- |
| Codex App | 选定的本机或已登记 SSH 主机 | 创建并打开 |
| Codex CLI | 选定的本机或已登记 SSH 主机 | 创建聊天 |
| WSL Codex | 选定发行版、用户及配置目录对应的环境 | 创建聊天 |
| Claude Desktop Code / Cowork | 当前 Desktop 环境 | 填写首条消息，创建并发送 |
| Claude Code CLI | 本机或已登记 SSH 环境 | 填写首条消息，创建并发送 |
| DeepSeek Harness Desktop | 当前 Harness 工作区所在电脑 | 创建并打开 |

浏览和选择目录不会创建聊天或发送消息。Claude 需要首条消息才能创建会话，因此按钮明确标注 **创建并发送**；其他接入先创建空聊天，之后由用户输入发送。提交后若结果尚不能确认，请先检查原环境，不要重复创建。

Codex 的 **聊天名称** 为可选项，默认留空，适用于 App、本机或 SSH CLI、独立配置和 WSL。填写名称时保存该名称；留空时创建未命名聊天。Bridge 新建的 CLI/WSL 聊天在首条消息明确发送成功后异步调用模型命名，排队消息等实际发送后触发；命名固定使用 `gpt-6-luna` 与 `low` 思考强度，沿用原接入的账号、提供商和工作目录，每个符合条件的聊天额外一次模型请求。不支持该命名模型时失败，不回退其他模型。App 聊天由原生 App 命名。手动名称优先，命名失败或结果未知不影响聊天，也不自动重发；已有聊天不补命名。界面优先显示原生名称，没有名称时显示原生内容预览，都没有时显示“未命名聊天”，并跟随原生名称更新。实现与限制见[CLI 模型命名记录](../.agents/notes/implemented/feature/2026-11-19-cli-model-titles.md)。

Codex App 和 DSH 的项目聊天会复用所选目录对应的原生项目。本机 Codex 目录尚未登记时，会打开电脑 App 的空白新建页，由 App 登记项目；若电脑端需要确认目录，请先完成确认，再返回重试。确认原生项目已保存后才创建聊天，登记结果未知时不会重复登记或创建。支持项目归属接口的 Codex 会核对新聊天保存后的归属，DSH 使用原生工作区 ID 创建聊天。此操作不移动已有聊天，也不改写已有项目设置。

Codex 本机新目录需要 App 支持目录登记入口；旧运行时仍可沿用 App 已保存的项目。SSH 的新目录请先在电脑 App 添加为项目，再返回选择；已有 SSH 项目及 Codex CLI 创建不受此限制。DSH 的任意新目录创建需要更新后的连接器及支持原生工作区接口的 Harness；旧连接器仍可使用其已有能力，升级连接器后才显示选择其他目录的入口。DSH 没有独立 CLI 或 SSH 接入选项。

Claude Desktop Code 若提示需要信任工作区，请在电脑的 Claude Desktop Code 页面打开所选目录，亲自确认“信任工作区”，再返回重试。所选目录、聊天名称和首条消息会保留；网关不会自动授予信任。超时或连接中断仍需先检查原环境，不能按信任拒绝直接重试。

目录权限使用原运行环境的权限；创建失败不会自动切换用户、账号或执行环境。选择项目不迁移已有会话，也不修改原项目配置。

## 从会话恢复

在会话列表右键或长按一个支持新建的 Codex App／CLI／IDE／WSL 会话，在“会话详情”下选择 **从会话恢复**。系统读取源会话的标题、实际工作目录及模型设置，在同一接入和主机创建新聊天，再打开它并自动发送 `读取会话[源会话标题](codex://threads/源会话ID)，继续完成`。源会话无需打开或连接；不会停止或修改原任务，也不会复制历史、Goal 或待审批状态。

新聊天使用源实际目录，包括已有 worktree 和独立聊天目录；模型、思考强度及已知速度档位按读取到的值传入。权限、审批、提供商和认证等其他设置沿用该接入当前普通新建的默认值。模型未记录时使用新建默认模型并提示，缺失的思考强度或速度参数省略；缺少目录时说明原因，不创建另一工作区。新会话名称由 Codex 正常生成，SSH 引用链接保留原生主机信息。

创建成功后，首条消息保存在新聊天的普通草稿中。首发明确未提交或聊天尚未连接时，直接在新聊天继续；消息可能已送达时使用普通消息管理核对。取得的新会话 ID 和同一创建请求的回执保留，不因首发失败重新创建。每次新的明确恢复操作均可再创建一个新聊天，不被同源旧恢复记录阻止。

## English

Select **New** in Web or mobile, choose a runtime environment, then select an existing project or **Choose directory**. The directory picker also offers a collapsed **New folder** action. A directory does not need an existing chat. Creating a folder does not initialize Git or overwrite files.

The same picker serves Codex App, local/SSH Codex CLI, WSL Codex, Claude Desktop Code/Cowork, local/SSH Claude Code CLI, and the existing DeepSeek Harness Desktop connection. Paths belong to the selected environment. Changing environments clears an unconfirmed directory selection. Expand the short path summary to see the full path.

Codex chat names are optional and blank by default across App, local/SSH CLI, independent configurations, and WSL. Entering a name saves it; leaving the field blank creates an unnamed chat. The interface displays the native name, falls back to the native message preview, then shows “Unnamed chat” when neither is available. Native name updates are reflected in the interface. Title generation belongs to the native client; Bridge does not create temporary naming sessions or send extra model requests.

Browsing does not create a chat or send a message. Codex App and DSH use **Create and open**; Codex CLI and WSL use **Create chat**. Claude requires an initial message and explicitly uses **Create and send**. An uncertain submission is not replayed automatically. Codex App and DSH reuse the native project for the selected directory. A new local Codex directory opens an empty Desktop creation page so the App can register the project. Complete any folder confirmation on the computer before retrying. The bridge waits for the native project record before creating a chat and does not repeat an unconfirmed registration. Existing chats and project settings are retained. Older runtimes retain the saved-project path. For a new SSH directory, add it as a project in the computer App first; existing SSH projects and Codex CLI creation keep their current behavior. Arbitrary DSH directories require an updated connector and Harness native workspace support. DSH does not offer a separate CLI or SSH connection.

If Claude Desktop Code requires workspace trust, open the selected folder in Desktop Code, explicitly approve trust, and return to retry. The selected folder, chat title and first message remain in the dialog. The bridge never grants trust automatically. A timeout or disconnect remains an uncertain submission: inspect the original environment before retrying.

Creation uses the original environment's permissions and account. Existing chats, project settings and connections are not migrated.

### Continue in a new chat

Right-click or long-press a supported Codex App, CLI, IDE or WSL chat and select **Continue in a new chat** below **Chat details**. Bridge reads its actual working directory and model settings, creates and opens a new chat in the same entry and host, then attempts the fixed continuation message once. The source does not need to be connected. Permissions, approvals, provider and authentication use ordinary creation defaults. Missing reasoning or speed settings are omitted; a missing model uses the creation default with a notice. A missing directory prevents creation.

The first message remains an ordinary draft if sending is not possible. Continue in the new chat, or use ordinary message management when delivery is uncertain. A known new chat ID is retained and a failed first send does not create another chat. New explicit requests may create another chat from the same source.
