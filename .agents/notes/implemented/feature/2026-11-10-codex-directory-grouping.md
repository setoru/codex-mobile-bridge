# Agent Note: Codex 会话按工作目录分类

Status: implemented

## 问题与选择

没有原生项目归属的 Codex 会话即使保留有效 `cwd`，本机“按项目”仍会将其放入“未归类”。用户在对照 Codex History Viewer 的目录分组规则后，选择新增“按工作目录”，保留旧“按项目”；首版不包含项目别名和手动项目组。

原生项目归属表示客户端保存的项目成员关系；目录分组表示网关按会话记录路径生成的浏览分类。两者使用独立身份。直接覆盖 `projectKey/projectName` 会影响新建聊天时的项目预选及原生菜单，因此本次在列表行增加 `directoryGroup`，而不改变已有项目字段或原始 `cwd`。

## 实现决定

`features/sessions/directory_grouping.py` 提供纯函数 `directory_group(cwd, *, scope, target_path_style)`，输出 `key/name/path/missing`。键包含客户端族、完整接入/路由主机身份、路径风格及完整规范目录；名称取末级目录名，路径保留原记录。没有目录时使用独立缺失值身份，前端翻译为“无工作目录”。不同主机或独立 Codex 配置中的同名目录不合并，也不把子目录或 worktree 自动折叠到 Git 根目录。

本机和普通 SSH 在 `Bridge.list()` 的原项目装饰后生成目录元数据；WSL 的直接列表路径调用同一函数，保证直接读取和聚合读取一致。独立 Codex 使用完整 `codex:` 路由身份，不以原生协议的 `local` 主机身份替换。

目录比较根据路径本身识别 Windows 盘符、反斜杠 UNC 和扩展前缀，Windows 比较统一大小写与分隔符。POSIX 保留大小写、合法反斜杠、有效空格和含 `..` 的记录身份。只有 `//` 等歧义才使用目标平台提示：本机取本机平台，WSL 固定 POSIX，SSH 在已有只读列表脚本中附加私有 `_targetPathStyle`，缓存保留提示，网关投影后移除。提示缺失时保守保留原字符串键，不借用网关系统推断远端。

不引入目录访问、Git 查询、符号链接解析或额外 SSH 探测。`SessionStore` 继续读取原有数据库字段并保持可独立注入远端；显示规则只在网关执行，因此新模块无需作为 SSH 源文件发送。

## 列表与兼容

三个显示选项是“最近交互”“按项目”“按工作目录”。旧“按项目”继续保留原生/旧版明确归属、普通 SSH 已登记根目录精确匹配，以及 WSL 既有目录分组规则，不能将它统一改名为“原生项目”。目录组头显示目录名、主机/接入和可折行的原路径，便于触屏区分同名目录。

目录模式复用非置顶的最近交互分页，每批仍为 100 条，对列表请求发送既有 `order=project`。前端只将已加载会话分组，跨页同目录合并；不为分类读取全部历史，不把首屏当作完整目录或会话总数。显示偏好和目录折叠状态按网关/认证范围及完整接入隔离。

缓存缺少 `directoryGroup` 与真实缺少 `cwd` 分开处理：前者等待列表刷新，新响应仍缺字段时显示不支持提示；后者进入“无工作目录”。目录模式不改动当前聊天、草稿、搜索、来源、归档条件、原生项目菜单或新建项目预选。

分类投影不启动或恢复线程，不修改 owner、provider、认证、执行目录、会话 ID 或原生项目数据，也不为发送、授权或文件访问提供新的许可。Claude/DSH 的分类规则不随本次变更调整。

## 验证边界

定向回归在既定接口验证：纯目录分类函数；使用合成 SQLite 的 Bridge 列表与鉴权 HTTP；WSL 直接与聚合列表；在无本地 `bridge` 包的隔离 Python 中执行 SSH 源码；生产 Web 列表渲染、偏好与缓存；Chromium/WebKit 宽窄视口的真实页面操作。浏览器夹具使用隔离合成数据，不访问真实网关。

相关用例位于 `tests/test_directory_grouping.py`、`tests/test_directory_grouping_integration.py`、`tests/test_cli_bridge_integration.py`、`tests/test_wsl_entries.py`、`tests/session-management-ui.test.cjs`、`tests/notifications-ui.test.cjs` 和 `tests/browser/codex-directory-grouping.spec.cjs`。新增目录接口、列表元数据与浏览器入口均先验证失败，再实现并验证通过。

最终验证结果：

- 后端完整源码回归：3,420 项，53 项因平台或原生执行边界跳过，其余通过。使用已有 `.tmp/default-ide-backend/venv/bin/python -B scripts/test-backend.py --source-only`。
- 前端完整回归：`npm run test:desktop`，1,524 项中 1,523 项通过、1 项跳过。
- 浏览器完整回归：236 项通过，包含新增的 16 项目录用例，覆盖 Chromium/WebKit 的 390 px 与 1280 px 视口。使用项目已有浏览器目录 `PLAYWRIGHT_BROWSERS_PATH=.local/tools/playwright`；已核对宽窄屏截图。
- 模块边界检查通过，保留既有 22 条例外；修改的 Python/JavaScript 语法检查通过，没有新增依赖或资源 URL。移动端构建范围检查为 `mobile=false`。
- 以分支起点 `f2f0d376` 为基准并行完成 Standards 与 Spec 审查，两轴均为 0 项发现。
- 使用当前源码只读投影本机元数据时，345 条可见会话归入 13 个目录组，缺目录为 0；原项目分类仍有 345 条“未归类”。这是检查时快照，不是运行中旧网关已加载新代码的证明。

首次后端全量误用了 MCP 工具的 Python 环境，因缺少 `cryptography` 导致签名测试导入失败、Claude 加密回执连接用例失败。切换到已有项目测试环境后，先分别验证这两项，再完成上述全量复核；未修改产品代码或测试以绕过环境问题。首次 WebKit 启动也因宿主默认缓存路径缺失而未运行，改用项目已安装浏览器后完成全部断言。首次失败记录及最终日志保留在 `.tmp/directory-*`。

本次实现不包含运行中网关部署、真实会话操作、原生客户端构建安装或发布。源码与合成浏览器结果不能替代 Windows/WSL 实机、Android WebView、iOS WKWebView 或已安装网关验收。

## 切换分类时筛选栏换行的修复

部署后的使用反馈指出，选择“按工作目录”会把筛选栏挤到下一行。生产页面的合成浏览器复现确认：1280 px 中文宽屏下，左侧栏的选择框从 y=64 移到 y=103，工具栏由 36 px 增高到 75 px。原因是 `bindChatListToolbar()` 仅根据当前选项文字计算宽度；目录选项比另外两项更长，跨过了侧栏的换行临界点。

共用筛选栏改为按每个选择框的最长选项预留宽度，继续使用当前容器决定自然换行。选项节点/标签变化和字体就绪时重新测量，不将选中值变化当成增减控件占位的依据。修复不改页面断点、宿主判断、会话数据或目录分类规则。

回归在既有工具栏绑定接口和生产页面上验证：同一容器内切换三种显示方式及来源后，搜索框、筛选组和下拉框的矩形保持稳定；中英文、320/390 px 与宽屏侧栏均覆盖。还验证选项延迟更新、字体增大与窗口尺寸变化会重新测量。

修复后前端全量 1,525 项中 1,524 项通过、1 项跳过；布局与资源定向 6 项通过。目录与工具栏浏览器共 24 项通过，覆盖 Chromium/WebKit 宽窄配置；其中迟到选项/字号用例先修正语言同步前置条件，并用 CSSOM 设置字号以遵循生产 CSP，再通过 4 个配置的定向复核。Standards 与 Spec 两轴审查均无发现。只修改网页交互，未重跑无变化的后端全量。公网与回环返回的 `/client-navigation.js` 均与当前源码一致，刷新页面即可加载修复，不需要重启网关。

## 相关决定

- [实施方案](../../../../docs/architecture/codex-directory-grouping-plan.md)：记录用户确认的首版范围和验收标准。
- [Codex History Viewer 查证](../../../../docs/codex-history-viewer-grouping-research.md)：借鉴完整目录分组，保留本项目的主机隔离和 POSIX 路径语义。
- [默认 Codex 的 IDE 历史](../bug-fix/2026-11-10-default-codex-ide-inventory.md)：沿用已确认的 APP/CLI/IDE 来源、分页和被动读取边界。
