# Codex 项目分组与平台差异查证

查证日期：2026-11-10（Asia/Shanghai）。范围为官方 GitHub 仓库当前默认分支、发行标签、源代码、回归测试及相关 PR；未修改运行代码、原生项目或会话数据。

## 结论

当前**本机 Codex 会话列表**按 Codex 原生项目归属分组；缺少归属时显示“未归类”，不再自动用工作目录建立分组。这是 macOS、Windows、Linux 共用的逻辑，不是 Linux 专有限制。`AppHosts.decorate()` 在该规则中只区分本机与远程主机，没有按操作系统开放工作目录分组的分支；测试明确覆盖 Windows、UNC、POSIX 路径并要求未归属会话进入同一组。[共享实现](https://github.com/try2love/codex-mobile-bridge/blob/41e833ab12c240aca1574dd7518f37c63a3fe5b1/bridge/clients/codex/remote.py#L564-L594) · [跨路径格式测试](https://github.com/try2love/codex-mobile-bridge/blob/41e833ab12c240aca1574dd7518f37c63a3fe5b1/tests/test_project_grouping.py#L9-L24)

“整个项目从来不支持按工作目录分组”的概括过宽：旧版 Codex 列表有该兜底规则，且当前其他接入仍有目录分组通路。应区分客户端、接入方式与版本。

## 本机与 GitHub 版本关系

- GitHub 默认分支为 `main`，查证时 HEAD 为 `41e833ab12c240aca1574dd7518f37c63a3fe5b1`，与本机 HEAD 完全一致。[GitHub 当前提交](https://github.com/try2love/codex-mobile-bridge/commit/41e833ab12c240aca1574dd7518f37c63a3fe5b1)
- 最新正式发行版为 `v2.2.0`，其标签指向 `76acf47b91ab3ae41f57fc7a8664da1c9224d206`。当前 `main` 比该标签多一个 README/介绍网站同步提交，没有分组实现差异。[发行页](https://github.com/try2love/codex-mobile-bridge/releases/tag/v2.2.0) · [标签至当前提交比较](https://github.com/try2love/codex-mobile-bridge/compare/76acf47b91ab3ae41f57fc7a8664da1c9224d206...41e833ab12c240aca1574dd7518f37c63a3fe5b1)
- 本次直接读取 GitHub 固定 SHA 的六个相关文件，与本机逐字节比较一致：`remote.py`、`test_project_grouping.py`、`web/README.md`、`ARCHITECTURE.md`、`desktop-sessions.js`、`wsl.py`。因此当前显示行为不能归因于服务器代码落后于上游。

## 当前不同接入的规则

| 接入 | 代码中的分组规则 | 证据 |
| --- | --- | --- |
| 本机 Codex，包括 CLI/桌面来源 | 优先原生项目成员关系，兼容旧版明确归属记录；没有归属则为“未归类” | [remote.py](https://github.com/try2love/codex-mobile-bridge/blob/41e833ab12c240aca1574dd7518f37c63a3fe5b1/bridge/clients/codex/remote.py#L564-L594) |
| 已登记的 SSH Codex 项目 | 在没有明确归属、没有显式移出标记等条件下，允许工作目录与已登记远程项目根目录精确匹配；不是为任意目录自动创建分组 | [实现](https://github.com/try2love/codex-mobile-bridge/blob/41e833ab12c240aca1574dd7518f37c63a3fe5b1/bridge/clients/codex/remote.py#L583-L589) · [精确匹配与排除测试](https://github.com/try2love/codex-mobile-bridge/blob/41e833ab12c240aca1574dd7518f37c63a3fe5b1/tests/test_project_grouping.py#L101-L145) |
| WSL Codex 接入 | 仍以该接入的 `host + cwd` 生成项目键，以目录末级名称生成分组名 | [wsl.py](https://github.com/try2love/codex-mobile-bridge/blob/41e833ab12c240aca1574dd7518f37c63a3fe5b1/bridge/features/endpoints/wsl.py#L348-L360) |
| Claude/DSH 共用列表组件 | 行数据含 `projectId` 字段时按项目/分区分组；没有该字段时，项目视图使用 `cwd` 分组。具体呈现取决于适配器返回的行数据，不能概括成所有 Claude 会话一定按目录分组 | [desktop-sessions.js](https://github.com/try2love/codex-mobile-bridge/blob/41e833ab12c240aca1574dd7518f37c63a3fe5b1/web/features/clients/desktop-sessions.js#L301-L303) |

因此，比较其他人的界面时，应先确认是同一版本、同一客户端和同一种接入。本机运行在 Linux 上，不等于使用 WSL 接入。

## 历史变化

1. [PR #70](https://github.com/try2love/codex-mobile-bridge/pull/70) 修复 Windows 路径分组，旧实现保留最长根目录匹配与目录边界、主机隔离。
2. [PR #73](https://github.com/try2love/codex-mobile-bridge/pull/73) 明确描述 Bridge 2.1.5 在未匹配已登记项目时，用 `cwd` 末级目录名作为备用分组名；该 PR 修复 Windows 反斜杠、UNC 和扩展路径的名称问题。
3. [PR #92](https://github.com/try2love/codex-mobile-bridge/pull/92) 改为共享菜单和原生项目归属。其源提交 [e47445aa](https://github.com/try2love/codex-mobile-bridge/commit/e47445aaca0bf1e9558f7cae6708548e5a6dff7e) 删除了 `cwd` 兜底分组，把缺省项目键改为 `unassigned`、名称改为“未归类”，并增加相应测试。GitHub 记录的合并时间为 2026-10-30 18:37（Asia/Shanghai）；维护者表示随 v2.1.7 发布。[维护者评论](https://github.com/try2love/codex-mobile-bridge/pull/92#issuecomment-6517406205)
4. [提交 1f9fa239](https://github.com/try2love/codex-mobile-bridge/commit/1f9fa2396a3169bce4d2fea8b6df5261e7430647) 补回已登记 SSH 项目根目录的精确匹配，以跟随原客户端；它没有恢复本机会话的任意 `cwd` 兜底分组。

已检索官方 issue/PR 中的“分组”“未归类”“cwd”“目录分组”“工作目录”“Linux 分组”，未发现把当前限制定义为 Linux 专属的记录。结论主要依据当前实现和明确回归测试，而不是依赖搜索无结果。

## 文档存在的滞后

当前 [ARCHITECTURE.md 第 41–43 行](https://github.com/try2love/codex-mobile-bridge/blob/41e833ab12c240aca1574dd7518f37c63a3fe5b1/ARCHITECTURE.md#L41-L43) 仍描述最长工作目录前缀匹配及目录末级名称兜底。这与当前 `remote.py`、回归测试及 [web/README.md 第 38 行](https://github.com/try2love/codex-mobile-bridge/blob/41e833ab12c240aca1574dd7518f37c63a3fe5b1/web/README.md#L38) 不一致；后者明确说明按原生项目归属分组，其余汇入“未归类”。判断当前运行行为应以源码和测试为准。

若希望本机 Codex 的纯 CLI 会话再次按工作目录展示，可新增独立的目录显示模式，同时保留原生项目归属与执行上下文。此查证没有实施该功能，也未在 macOS/Windows/WSL 真机上运行客户端；平台结论属于已核对的共享源码与测试规则。
