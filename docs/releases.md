# Release 元数据

正式候选是当前经过校验的 `dist/site` 文件树。候选 identity 的 schema、source commit、
artifact count、total bytes 和 content hash 都来自实际文件；content hash 排除 workspace、
报告、时间戳和绝对路径，只使用排序后的相对路径、文件 SHA-256 与大小。

`workspace/build-state.json` 是增量构建的派生状态。`release prepare` 会验证它与
`dist/site` 一致，但发布绑定始终以实际站点树为准。`workspace/state/prepared-release.json`
使用 schema 2，发布前会检查 source commit、程序版本、候选 hash、文件统计、公开季度和
准备时的远端 `gh-pages` commit；状态变化后必须重新 prepare。

发布版本 `YYYY.MM.DD.N` 仅用于本地发布报告和提交元数据，不参与运行时 PWA 或 build identity。
dry-run 不写远端；真实发布通过临时 worktree 对 `gh-pages` 做一次普通 push，发布内容与
validated `dist/site` 相同，不生成额外的运行时快照、历史页或详情产品。

数据库、下载媒体、报告、build state、prepared state、候选 staging 和发布树都不进入源码
提交；正式站点发布只能由明确的 `bgmb release publish` 流程执行。
