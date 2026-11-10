# Codex History Viewer 会话分类机制查证

查证日期：2026-11-10（Asia/Shanghai）。对象是 [HizTam/codex-history-viewer](https://github.com/HizTam/codex-history-viewer) 默认分支的固定提交 [`6ff8b5e02cf9a919af5749999bb784204da9d2dc`](https://github.com/HizTam/codex-history-viewer/commit/6ff8b5e02cf9a919af5749999bb784204da9d2dc)。该提交的 README 标注版本为 2.20.2；本文不把源码查证当作发行包或实机验证。[版本说明](https://github.com/HizTam/codex-history-viewer/blob/6ff8b5e02cf9a919af5749999bb784204da9d2dc/README.md#L1-L5)

## 核心结论

它把 **会话记录中的工作目录 `cwd` 当作项目身份**：先规范化完整路径，相同路径的会话放入同一项目桶，再以目录末级名称或用户别名显示。没有 `cwd` 才进入 `No CWD` 组。该链路不依赖 Codex 数据库的 `project_id`，也不要求提前在 Codex App 登记项目。[分组桶](https://github.com/HizTam/codex-history-viewer/blob/6ff8b5e02cf9a919af5749999bb784204da9d2dc/src/tree/historyTree.ts#L734-L763) · [目录与组名](https://github.com/HizTam/codex-history-viewer/blob/6ff8b5e02cf9a919af5749999bb784204da9d2dc/src/tree/historyTree.ts#L1434-L1443) · [缺省组名称](https://github.com/HizTam/codex-history-viewer/blob/6ff8b5e02cf9a919af5749999bb784204da9d2dc/localization/locales/en.json#L870)

因此，它能把没有原生项目归属的 CLI、IDE 历史按目录整理起来。这正是当前网关“全部未归类”场景所缺少的显示规则；不需要改动原始会话的项目成员关系才能实现这种浏览方式。

## 数据从哪里来

默认扫描 `~/.codex/sessions`，可选包含同级 `archived_sessions`；递归寻找 `rollout-*` 会话文件。普通 JSONL 与可选压缩历史通过统一文件读取器处理。这里的项目归类输入来自日志文件，而非 `state_*.sqlite` 的项目表。[默认目录](https://github.com/HizTam/codex-history-viewer/blob/6ff8b5e02cf9a919af5749999bb784204da9d2dc/src/settings.ts#L83-L88) · [启用的数据源](https://github.com/HizTam/codex-history-viewer/blob/6ff8b5e02cf9a919af5749999bb784204da9d2dc/src/sessions/sessionDiscovery.ts#L44-L86) · [递归扫描](https://github.com/HizTam/codex-history-viewer/blob/6ff8b5e02cf9a919af5749999bb784204da9d2dc/src/sessions/sessionDiscovery.ts#L132-L168)

元数据提取在文件头部最多 400 个非空行的窗口内查找 Codex 的 `session_meta`，从 `payload.cwd` 取工作目录，同时读取会话 ID、时间、来源等字段。`extractCodexSessionMeta` 没有把原生项目 ID 作为分组输入。正常索引的摘要扫描在这一元数据窗口之后仍会继续读取，用于预览、最近活动等信息；400 行限制不代表完整历史读取上限。[元数据提取](https://github.com/HizTam/codex-history-viewer/blob/6ff8b5e02cf9a919af5749999bb784204da9d2dc/src/sessions/sessionSummary.ts#L100-L122) · [索引扫描中的元数据窗口](https://github.com/HizTam/codex-history-viewer/blob/6ff8b5e02cf9a919af5749999bb784204da9d2dc/src/sessions/sessionSummary.ts#L344-L411)

概念上，默认分组等价于：

```text
session_meta.payload.cwd
  → 规范化完整路径
  → 相同路径进入同一个项目桶
  → 用目录末级名称显示；有别名则显示别名
```

这是对上述源码的简化，不包含用户手动配置的关联关系。

## 路径如何变成项目

`normalizeProjectKey` 去除首尾空白、统一正反斜杠、做 `path.normalize`、去除非根路径的尾斜杠，最后在所有平台转为小写。项目键保留完整路径；目录末级名称只用于显示。[规范化规则](https://github.com/HizTam/codex-history-viewer/blob/6ff8b5e02cf9a919af5749999bb784204da9d2dc/src/utils/fsUtils.ts#L64-L73) · [标题优先级](https://github.com/HizTam/codex-history-viewer/blob/6ff8b5e02cf9a919af5749999bb784204da9d2dc/src/tree/historyTree.ts#L775-L806)

未设置别名或关联时，源码规则对应以下结果：

| 会话 `cwd` | 默认分组结果 |
| --- | --- |
| `/work/api`、`/work/api/` | 同一项目，显示 `api` |
| `/work/api`、`/other/api` | 两个不同项目；默认显示名都为 `api`，完整路径仍可区分 |
| `/work/api`、`/work/api/tests` | 两个项目，分别显示 `api`、`tests` |
| `/work/api`、`/worktrees/api-feature` | 两个项目；不会自动识别它们属于同一 Git 仓库 |
| 空或缺失 `cwd` | `No CWD` |
| `/work/Project`、`/work/project` | 被合并，因为该实现跨平台忽略大小写 |

这些是静态规则推导的例子，不是本次运行插件得到的实测输出。默认分组调用链没有查找 `.git`、Git 根目录或 worktree 归属的步骤，也没有把子目录自动折叠到仓库根目录。[分组与关联入口](https://github.com/HizTam/codex-history-viewer/blob/6ff8b5e02cf9a919af5749999bb784204da9d2dc/src/tree/historyTree.ts#L734-L763) · [键解析](https://github.com/HizTam/codex-history-viewer/blob/6ff8b5e02cf9a919af5749999bb784204da9d2dc/src/tree/historyTree.ts#L863-L891)

## 用户可以怎样调整分类

### 项目别名

别名以规范化的目录路径为键，保存在 VS Code 扩展的 `globalState` 中；它改变项目标题，不改原会话文件或目录身份。别名也用于会话说明、提示、搜索范围等界面。[别名存储](https://github.com/HizTam/codex-history-viewer/blob/6ff8b5e02cf9a919af5749999bb784204da9d2dc/src/services/projectAliasStore.ts#L12-L63) · [存储作用域](https://github.com/HizTam/codex-history-viewer/blob/6ff8b5e02cf9a919af5749999bb784204da9d2dc/src/extension.ts#L461-L469) · [功能说明](https://github.com/HizTam/codex-history-viewer/blob/6ff8b5e02cf9a919af5749999bb784204da9d2dc/README.md#L72-L74)

### 项目关联

它维护额外的 `sourceCwd → targetCwd` 关联，支持两种模式：

| 模式 | 效果 |
| --- | --- |
| `relocate`，Move/Rename Association | 把来源目录的历史并入目标项目桶，显示时使用目标目录；适合项目移动或重命名后的历史整理。 |
| `groupOnly`，Group Only | 把相关项目放入共同父组，保留各自的项目节点和会话。 |

这两种关联也写入扩展 `globalState`，不会为了分组移动原始历史文件。项目分组分别计算 `groupKey` 和 relocation key：前者决定共同父组，后者决定会话实际进入哪个项目桶；`groupOnly` 关系由树构建器递归生成父子节点。[关联存储与两种键](https://github.com/HizTam/codex-history-viewer/blob/6ff8b5e02cf9a919af5749999bb784204da9d2dc/src/services/projectAssociationStore.ts#L5-L112) · [分组调用](https://github.com/HizTam/codex-history-viewer/blob/6ff8b5e02cf9a919af5749999bb784204da9d2dc/src/tree/historyTree.ts#L734-L772) · [父子树构造](https://github.com/HizTam/codex-history-viewer/blob/6ff8b5e02cf9a919af5749999bb784204da9d2dc/src/tree/projectGroupTreeBuilder.ts#L34-L130)

`relocate` 的影响不止标题：它还为文件链接和 handoff 内容提供路径映射。CLI 恢复仍优先使用可用的原始 `cwd`；原目录不可用时，关联目标才作为需要用户选择的候选。因此不能将其概括为“自动迁移原会话执行目录”。[handoff 路径上下文](https://github.com/HizTam/codex-history-viewer/blob/6ff8b5e02cf9a919af5749999bb784204da9d2dc/src/extension.ts#L3006-L3027) · [CLI 恢复目录选择](https://github.com/HizTam/codex-history-viewer/blob/6ff8b5e02cf9a919af5749999bb784204da9d2dc/src/cliResume/cliResumeCwd.ts#L67-L119)

## 分组、范围、排序是独立控制

- **显示方式**：普通列表或项目视图；项目视图可以是 `项目 → 会话`，也可以继续按 `项目 → 年 → 月 → 日 → 会话` 展开。[实现](https://github.com/HizTam/codex-history-viewer/blob/6ff8b5e02cf9a919af5749999bb784204da9d2dc/src/tree/historyTree.ts#L937-L989)
- **项目范围**：全部项目组或当前项目组。当前项目从活动编辑器所属工作区（否则第一个工作区）出发，在历史目录中寻找相同或位于工作区之下的路径；未找到时选最近祖先，最终按关联后的项目键筛选。这是范围选择，不是对全部会话重新推导 Git 根目录。[工作区选择](https://github.com/HizTam/codex-history-viewer/blob/6ff8b5e02cf9a919af5749999bb784204da9d2dc/src/extension.ts#L9135-L9186) · [项目键匹配](https://github.com/HizTam/codex-history-viewer/blob/6ff8b5e02cf9a919af5749999bb784204da9d2dc/src/types/projectSelection.ts#L73-L83)
- **其他过滤**：日期、来源、标签、归档/隐藏状态、压缩状态与项目范围共同决定可见会话；标签不替代项目键。History 与 Pinned 分别保留筛选状态。[过滤链](https://github.com/HizTam/codex-history-viewer/blob/6ff8b5e02cf9a919af5749999bb784204da9d2dc/src/tree/historyTree.ts#L413-L443) · [界面约定](https://github.com/HizTam/codex-history-viewer/blob/6ff8b5e02cf9a919af5749999bb784204da9d2dc/README.md#L76-L82)
- **排序**：支持开始时间、最近活动、名称、源文件大小；项目节点也按对应聚合值或名称排序。排序不改变会话所属的目录桶。[项目排序](https://github.com/HizTam/codex-history-viewer/blob/6ff8b5e02cf9a919af5749999bb784204da9d2dc/src/tree/historyTree.ts#L819-L860)
- **子代理**：Agent Runs 是另一个可选功能。启用并构建关系图后，已有父关系的子会话不再作为 History 独立根项显示；这不构成项目归类依据。[可见性规则](https://github.com/HizTam/codex-history-viewer/blob/6ff8b5e02cf9a919af5749999bb784204da9d2dc/src/agents/codexAgentRunsService.ts#L73-L101) · [默认关闭说明](https://github.com/HizTam/codex-history-viewer/blob/6ff8b5e02cf9a919af5749999bb784204da9d2dc/README.md#L28-L29)

## 与当前网关的对照

| 比较项 | Codex History Viewer | 当前网关本机 Codex 列表 |
| --- | --- | --- |
| 项目依据 | JSONL 元数据中的 `cwd`，以及扩展本地关联 | SQLite 原生 `project_id` / `projects`，其次旧版明确项目归属 |
| 有 `cwd`、无原生归属 | 可以按目录分组 | “未归类” |
| 项目默认名称 | 目录末级名称，可设显示别名 | 原生项目名称或旧版项目名称 |
| 同一仓库的不同目录 | 默认分开，可手动关联 | 遵循原生项目成员关系 |

网关的相关依据是 [原生项目读取](../bridge/features/sessions/store.py#L284-L292)、[本机分组](../bridge/clients/codex/remote.py#L564-L594) 和 [前端按返回值显示](../web/shell/app.js#L305-L328)。这些本地链接指向研究时工作区版本；网关 HEAD 为 `f2f0d3763cc68d28bb3494110167832bc398eb0c`。

值得借鉴的是将“原生项目”和“按工作目录浏览”分为独立显示方式，并可选支持网关自己的目录别名、相关项目组。网关已经从 SQLite 读取每条会话的 `cwd`，实现目录显示模式不需要把会话发现改成扫描 JSONL，也不需要补写 Codex 原生项目表。[已有会话字段](../bridge/features/sessions/store.py#L236-L244)

迁移算法时应保留本项目的主机与客户端实例隔离，项目键至少带完整实例/主机身份和目录，不能只用末级名称。另外，该扩展在所有平台小写化路径、把反斜杠视为分隔符；Linux 的大小写目录和合法反斜杠目录名可能被误合并。应借鉴其目录分组思路，保留网关现有的 POSIX/Windows 路径语义，而非直接复制这一规范化函数。

## 验证范围

本次下载固定提交，交叉阅读 README、会话发现与元数据解析、项目键、树构建、别名/关联存储、筛选和恢复目录源码。未安装依赖，未运行外部插件代码或其测试，未启动 VS Code 插件，未修改原始会话、原生项目或运行中的网关。文中的分类示例属于源码推导。前轮网关的 346 条会话、13 个目录是当时的只读快照，不代表本次插件实测结果。
