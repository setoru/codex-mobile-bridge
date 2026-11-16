# Codex 会话菜单：从会话恢复（App / CLI）

Status: ready-for-agent
Type: feature
Created: 2026-11-16

## Problem Statement

用户希望在一个新的 Codex 会话中继续旧会话的工作。当前需要手动读取旧会话标题和 ID、确认运行接入与模型等设置、创建新会话，再输入引用旧会话的消息。这个过程重复、容易选错目录或使用新建默认配置，也容易在网络异常后重复创建或重复发送。

功能范围包含 **Codex App 和 Codex CLI**。用户需要的是“创建新会话并自动发送一条指定消息”的快捷操作。

## Solution

在共用会话菜单中，紧跟“会话详情”下一行增加“从会话恢复”。用户对某个会话执行该操作后，系统读取该会话的身份和设置，在相同接入与实际工作目录下创建新的空会话，核对继承设置，再自动发送以下固定格式的首条消息：

> 读取会话[源会话标题](codex://threads/源会话ID)，继续完成

本机链接采用上述格式；其他已支持接入按现有会话链接规则保留必要的原生主机身份。标题中的 Markdown 特殊字符需要正确转义。消息模板保持固定中文，不随界面语言变更。

Codex App 会话创建新的 App 会话；Codex CLI 会话创建新的 CLI 会话。操作显示创建、设置确认与发送进度，成功后刷新会话列表并打开新会话。正常流程不增加确认弹窗，也不要求用户再次选择模型、目录或手动点击发送。

新会话名称沿用 Codex 的原生命名机制。源会话保留；新会话通过这条消息请求 Codex 读取旧会话。

## User Stories

1. As a Codex Web user, I want a “从会话恢复” action immediately below “会话详情”, so that I can find it in the existing session menu.
2. As a Codex Web user, I want the action to use the session whose menu I opened, so that another currently displayed conversation cannot become the source by mistake.
3. As a Codex App user, I want recovery to create a new App conversation, so that work continues through my existing App connection.
4. As a Codex CLI user, I want recovery to create a new CLI conversation, so that work continues through my existing CLI connection.
5. As a user with multiple Codex connections, I want the source connection, host and configuration identity preserved, so that the new conversation runs in the correct environment.
6. As a Codex user, I want the source conversation’s actual working directory preserved, so that the new conversation accesses the same workspace.
7. As a user working in a Git worktree, I want that worktree directory preserved, so that recovery does not unexpectedly use the project root or create another worktree.
8. As a user with an independent conversation, I want its existing working directory reused, so that recovery does not allocate a different independent directory.
9. As a Codex user, I want the source model preserved, so that the new conversation does not silently use the new-conversation default.
10. As a Codex user, I want the source reasoning effort preserved when applicable, so that the new conversation uses the intended reasoning setting.
11. As a user of a model without reasoning controls, I want recovery to avoid inventing a reasoning setting, so that an unsupported override is not introduced.
12. As a Codex user, I want the source speed tier preserved where supported and verifiable, so that recovery keeps my existing model settings.
13. As a Codex user, I want supported source permission settings preserved and checked before sending, so that the new conversation uses the intended permissions.
14. As a Codex user, I want the original model provider and connection identity checked, so that recovery cannot silently switch my provider or account.
15. As a Codex user, I want the source title and session ID filled into the message automatically, so that I do not need to copy them manually.
16. As a user whose conversation title contains Markdown punctuation or line breaks, I want the generated reference to remain valid, so that it points to the intended source conversation.
17. As a user of a conversation without an explicit title, I want the existing title fallback rules used, so that the reference remains understandable without adding a naming request.
18. As a Codex user, I want the fixed message sent automatically after creation and configuration confirmation, so that one action completes the workflow.
19. As a Codex user, I want a new conversation ID with the generated message as its first user message, so that I can continue the task in a fresh conversation.
20. As a Codex user, I want the source conversation and its existing execution state preserved, so that using recovery does not send to, stop or alter the original conversation.
21. As a Codex user, I want progress feedback during creation and sending, so that I can tell the operation is still being handled.
22. As a Codex user, I want repeated clicks during a pending operation to reuse that operation, so that they do not create multiple conversations or duplicate the first message.
23. As a Codex user, I want the new conversation opened when the operation succeeds, so that I can immediately follow its response.
24. As a Codex user, I want the new conversation to use normal native naming, so that recovery does not add an unwanted title prefix or extra model request.
25. As a Codex user, I want an actionable error when the source directory or required configuration is unavailable, so that I can resolve the problem without an unexpected fallback.
26. As a Codex user, I want connection or account changes detected before dependent writes, so that a stale menu cannot create or send in another identity.
27. As a Codex user, I want an already-created conversation retained when settings cannot be confirmed, so that resolving the problem does not require another creation.
28. As a Codex user, I want an already-created conversation and its pending text retained when sending is known not to have happened, so that I can explicitly continue with the same conversation.
29. As a Codex user, I want uncertain creation results shown as pending verification, so that refreshing or reopening the menu cannot blindly create a duplicate.
30. As a Codex user, I want uncertain send results kept distinct from an unsent draft, so that an interrupted response cannot cause the same message to be sent again.
31. As a Codex user, I want operation identity and confirmed results preserved across a page reload or gateway restart, so that recovery does not replay completed or uncertain writes.
32. As a Codex user, I want to deliberately start another recovery after the previous operation is resolved, so that duplicate protection does not permanently prevent creating another conversation from the same source.
33. As a user who changes conversations while a request is running, I want the request to retain its original target and late responses to respect my newer navigation, so that another conversation is not overwritten or unexpectedly opened.
34. As a keyboard or narrow-screen user, I want the new action to follow the existing shared-menu interaction, so that I can use the same feature through supported keyboard, title-menu and long-press entry points.
35. As an authenticated Codex Web user, I want recovery to use the existing authorization and request validation rules, so that the shortcut has the same access boundaries as ordinary creation and sending.

## Implementation Decisions

- **模块与测试入口：**在共享会话业务模块中封装“从源会话新建并发送”这一完整操作，通过现有 Bridge 会话操作入口对外提供。共享菜单只负责发起操作、显示结果和导航。应用组装模块选择原接入；Codex 客户端模块分别执行已有 App 与 CLI 协议。无需新增通用工作流框架或操作系统业务分支。
- **请求契约：**请求携带稳定的操作 ID 与源会话完整身份，包括会话 ID、主机及接入实例。服务端根据源身份解析标题、目录和设置；客户端列表缓存或当前打开会话不能作为执行身份与权限的依据。相同操作 ID 不得绑定另一来源或不同操作内容。
- **来源读取：**在写入前捕获并核验源配置快照。打开菜单及读取源信息不启动、恢复执行或切换源会话。已有保存信息与已核验原生状态按现有读取规则使用；必要设置不足时明确报错，不通过激活原任务来补齐。一个操作确定的源身份、标题、提示消息和配置意图在后续恢复中保持稳定。
- **继承内容：**保留 App／CLI 接入类型、原主机及配置目录身份、实际工作目录、模型提供商、模型和适用的思考强度。速度档位与权限设置按现有原生协议支持范围转换并核验。不能保证任意自定义权限均已支持；不支持或无法核验的必要设置必须说明原因，并在发送前停止，不静默使用当前默认配置。不存在的可选设置不凭空补值。
- **目录与归属：**使用源会话实际工作目录，正确处理 worktree 和独立会话目录。项目归属与工作目录是不同概念，按现有原生规则处理项目，不借项目选择把目录改成项目根目录，也不创建新的 worktree。沿用原接入已经支持的本机或远端路由；不增加新的接入类型或平台能力。
- **App 执行：**复用空会话创建与桌面接管流程。临时创建运行时退出后，由新会话的原生 App owner 确认设置并执行首条发送；不能把源会话的 owner ID 直接用于新会话，也不能让临时创建进程代替 App 执行消息。
- **CLI 执行：**复用相同 CLI 接入的原生创建、设置与发送能力。新会话保持源主机、配置目录与实际工作目录，并验证创建回执；不将 CLI 会话转换为 App 会话，不为它打开桌面聊天。
- **账号与身份：**沿用原接入上下文，不复制登录凭据或自动切换账号。配置目录相同不等于原账号身份仍然一致；如果无法确认来源与目标执行身份符合现有约束，应保留已确认结果并报告问题。
- **消息与命名：**服务端按固定模板生成消息，使用真实源会话 ID、现有标题回退规则和合法深度链接。正确处理标题转义、换行和必要的原生主机信息，不将 Bridge 内部接入标识冒充原生主机 ID。生成消息仅包含用户指定的“读取会话…，继续完成”内容，不追加额外任务指令。新会话名称保持原生默认命名流程，不复制源名称、添加恢复后缀或发起额外命名请求。
- **顺序：**先核验来源并记录操作，再创建新的空会话，保存新 ID，确认继承设置，最后通过既有共享发送入口提交一次首条消息。首条发送使用稳定提交 ID；设置尚未确认时不能先发送再修正。
- **持久化：**在现有私有操作记录机制中保存恢复操作与创建、设置和发送回执的关联、源身份、配置意图、目标 ID及所处阶段。复用已有创建和消息去重规则，不建立互相矛盾的第二套回执。记录不保存认证秘密，不进入版本控制。
- **重复与继续：**忙碌按钮、重新打开菜单、页面重载和网关重启都不能绕过未决操作生成另一组写入。存在进行中或结果未知的操作时继续展示或核对该操作。已确认完成后，新的明确点击可以创建另一个独立新会话。重新读取已完成回执不得覆盖用户后来修改的新会话设置。
- **结果契约：**调用方能区分验证失败、创建结果未知、已创建待确认设置、明确未发送、发送结果未知和已确认发送。只要已可靠取得目标 ID，就在响应与后续查询中保留该 ID。不能将缺失回执解释成未执行，也不能声称所有创建失败都能得到新 ID。
- **失败与导航：**明确未发送时保留目标会话与待发文本，供用户显式继续；发送结果未知时保留待核对状态，不能转成可自动发送的普通草稿。创建或发送结果未知不自动重放、不自动删除已创建会话。前端绑定原始操作身份；用户仍停留在该操作上下文时，成功后自动打开新会话，否则保留结果入口并尊重后续导航。
- **现有约束：**继续使用现有登录、同源、CSRF、文件目录边界及接入身份校验。写操作不进入只读请求合并。共享业务不反向依赖应用、HTTP 或平台实现，不扩大依赖例外。新增必要路由或资源时同步现有消费者与资源约束。

## Testing Decisions

用户已确认：以**现有 Bridge 会话操作入口**为主要测试 seam，通过模拟 Codex 原生 runtime／owner 验证完整行为；浏览器测试补充菜单位置、重复点击、失败提示和成功跳转。

- **什么是有效测试：**从调用者可观察的结果、目标身份、实际送往原生端的写入及其次数验证契约。跨越真实共享业务流程，只在原生通信等外部依赖处使用替身；不逐个模拟内部 helper，不断言内部函数拆分、局部变量或固定文件布局。一次操作同时覆盖创建、设置和首发之间的约束。
- **共同成功路径：**同一组契约分别用于 App 与 CLI，验证新 ID、原接入、实际工作目录、提供商、模型、思考强度及支持的速度／权限设置。新会话第一条用户消息与固定模板一致，且发送时配置已经确认；源会话没有收到写入。用源设置与新建默认设置不同的夹具避免默认值掩盖错误。
- **来源与目录：**覆盖被右键选中的会话不同于当前会话、同名会话、多个主机或独立配置、worktree 目录不同于项目根目录、独立会话、目录失效及主机／账号／接入身份变化。现有已支持远端路由使用合成夹具验证；不把本机模拟视为远端实机通过。
- **消息与名称：**覆盖普通中文标题、括号、方括号、反斜线、换行、无显式标题及主机链接。确认源 ID 不变、显示标题可读，消息不会因界面语言改变；新会话不额外命名或产生额外模型请求。
- **故障与去重：**在创建前、创建后但回执丢失、设置确认中、发送前及发送已派发但回执丢失等阶段注入失败。重复点击、相同请求、多次读取和重新建立 Bridge 实例后，已完成或未知的原生写入不重放。可靠取得的新 ID 不丢失；明确未发送与结果未知的响应及 UI 行为不同。
- **有条件的继续：**在同一新会话中恢复明确尚未执行的后续阶段，验证不会再次创建。未知设置或发送只允许原生读取核对；完成后再发起新的独立操作应能创建第二个会话。重复查询旧操作不能覆盖目标后来修改的设置。
- **已有先例：**复用现有“创建设置到达桌面 owner 后首发”“CLI 创建与原生标题”“重复创建及网关重启”“未知发送不重放”等高层夹具与断言方式。重启场景仍通过同一业务入口测试，不为操作记录额外暴露生产接口。
- **浏览器补充：**使用生产共用菜单及合成请求，通过真实会话行打开菜单，确认新项紧跟“会话详情”、源身份捕获正确、忙碌状态防重入、进度和错误可见、成功跳转以及迟到响应不劫持后续导航。覆盖现有 Chromium／WebKit 宽窄视口与键盘、长按入口；实际运行断言，不能只加载脚本。
- **请求与共同回归：**在现有请求校验夹具中确认未认证、CSRF 不匹配、源身份无效和陈旧执行上下文不会派发写入；运行与改动相关的创建、模型、CLI 集成、发送、共用菜单和模块依赖检查，新增路由时包含中继合约检查。仅新增公共行为改变所需的测试，不为可逆文案单独建测试体系。
- **验证结果表述：**规格发布阶段不执行真实客户端写入。后续实现区分源码模拟、真实浏览器夹具与经授权的真实 App／CLI 联调，真实联调记录新会话 ID、配置和首条消息结果。模型是否实际读取并完成旧任务属于原生能力表现，不以合成测试宣称其已经验证。

## Out of Scope

- Claude、DeepSeek 或其他客户端。
- 新增 CLI、SSH、WSL 接入或平台能力；已有 Codex 接入按其原路由复用，不能因本功能静默迁移接入。
- 原生 fork、复制旧会话全部历史，或者由 Bridge 把深度链接自动展开为历史内容。
- 自动停止、归档、删除、重命名或恢复执行源会话；复制源会话的 Goal、任务、待审批请求、owner 或登录凭据。
- 新建 worktree、复制项目、切换账号或模型提供商、修改全局默认配置、放宽目录与权限限制。
- 可编辑消息模板、批量恢复、自动再次恢复、历史摘要生成、额外命名模型请求。
- 重新设计会话菜单、添加原生手机专用菜单，或新建独立的工作流框架。
- 保证 Codex 一定读取成功或完成源会话中的所有工作。此功能保证按规定创建、核对并提交指定消息；实际执行仍由 Codex 完成。
- 本次规格发布不包含功能代码、真实会话操作、构建、安装、推送、版本号调整或产品发布。

## Further Notes

- 用户明确要求同时覆盖 Codex App 和 Codex CLI；不再要求用户选择客户端范围。
- “从会话恢复”是菜单文案，实际行为为创建新的空会话并发送引用源会话的消息；与现有归档恢复、重连以及创建聊天分支保持各自语义。
- 目前已有 App／CLI 空会话创建、模型与思考强度选择、App owner 接管、共享发送、持久化去重和共享菜单。实现重点是读取并核验源配置、保持源实际目录，以及安全衔接创建和第一条消息。
- 现有新建能力尚不能直接承诺继承任意提供商、速度档位或自定义权限；实施时按上述契约补齐显式传递和回执核验。无法支持的来源或设置要给出明确原因，不以默认值替代。
- 本规格使用占位会话身份，不公开真实聊天 ID、聊天内容、凭据或本地运行数据。
