# Bangumi Side B

Bangumi Side B（`MyKr-YSteinsK/bangumi-side-b`，CLI `bgmb`）是本地优先的日本动画季度档案。

## 接手入口

- 确认工作区是 `MyKr-YSteinsK/bangumi-side-b` 的仓库根，核对实际分支、工作树和上游。保护用户已有改动。先读 [项目说明](docs/项目源文件/PROJECT_BRIEF.md) 和 [当前状态](docs/项目源文件/CURRENT_STATE.md)，再按任务查阅 [长期决定](docs/项目源文件/DECISIONS.md) 和 [运行手册](docs/项目源文件/RUNBOOK.md#验证与接续)。
- [升级审查](docs/项目源文件/升级路线与技术审查.md) 记录 D-025 已接受的升级方向、分批顺序和仍需验证的技术选择。[历史摘要](docs/archive/README.md) 供追溯。
- 普通任务可以直接在 Codex 处理。正式 Plan 核对仓库与任务编号，优先读取获授权的 GitHub Issue 完整正文。受限或无法读取时采用可读且获授权的替代正文。以当前授权和可观察的验收为准。[任务恢复与交接](docs/项目源文件/RUNBOOK.md#任务恢复与交接)

## 数据与操作边界

- 当前收录日本 Anime（`type == 2`）TV 与剧场版。TV 可有首播和续播 appearance，剧场版只有首播。以 canonical detail、确定性规则和显式人工裁决确定归属、续播、媒体、来源、标签和排除状态。公开作品图片限于已校验的封面。
- SQLite 事实、已校验封面、受控配置和静态源资源是构建输入。仅接受 `bangumi-side-b-archive` schema 2，遇到未知或更高版本即失败并保留原数据。
- 真实 Bangumi 访问须由用户本次明确授权，或正式 Plan 明示 `Live Bangumi sync: AUTHORIZED`。这也适用于 `bgmb sync`、会远程导入未知作品的 `bgmb assign` 及间接调用。
- `build` 完全离线，仅产出正式 `dist/site`。`serve` 仅读取这棵树。成功提交事实与封面后，sync 可增量构建。上一次可用站点须经得起失败或中断。
- 保护本机数据库、封面、人工裁决、备份、正式站点、prepared state 和本机 `gh-pages`。数据库、下载媒体、报告、缓存、生成站点、凭据、私有路径与原始 API 数据须留在 Git 之外。`docs/delete/` 是本机待用户清理区，已忽略。

## Git 与发布

- 源码同步走当前源码分支。获批准的正式 Plan 有 tracked 变更时，完成必要验证与最终提交后，普通推送一次到配置的上游并核对实际远端 HEAD。获准的 live-data 验证及相关 review、audit、回归须在该次推送前完成。以本地和远端 HEAD 一致作为交付完成依据。推送失败时保留本地提交并报告交付受阻。无 tracked 改动无需提交或推送。
- 保留 Git 历史。禁止强推、改写历史、擅自修改远端或重置本机发布分支。
- Pages 和真机验收各按当前任务授权。先核对推送的部署副作用。`release prepare` 可只读访问 Git 远端并离线构建和校验候选。`release publish` 只向官方 origin 的 `gh-pages` 普通推送绑定该候选的 release commit，期间禁止 sync 或 build。[发布说明](docs/publish.md)
