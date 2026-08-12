# Release 元数据

正式候选是当前经过校验的 `dist/site` 文件树。候选 identity 的 schema、source commit、
artifact count、total bytes 和 content hash 都来自实际文件；content hash 排除 workspace、
报告、时间戳和绝对路径，只使用排序后的相对路径、文件 SHA-256 与大小。

`workspace/build-state.json` 是增量构建的派生状态。`release prepare` 会验证它与
`dist/site` 一致，但发布绑定始终以实际站点树为准。`workspace/state/prepared-release.json`
使用 schema 2，发布前会检查 source commit、程序版本、候选 hash、文件统计、公开季度和
准备时的远端 `gh-pages` commit；source/content/remote identity 必须是严格的小写 Git
hex，公开季度必须是排序唯一的 `YYYY-(01|04|07|10)` 列表，状态变化后必须重新 prepare。

发布版本 `YYYY.MM.DD.N` 的唯一权威是当前远端 `gh-pages` HEAD 的提交消息。正式提交严格使用
`release: YYYY.MM.DD.N [source <12位 commit>]`；缺少、过期、旧格式或无法解析时从当天 `.1`
开始，同日合法版本才递增。版本只用于发布报告和提交元数据，不参与运行时 PWA 或 build
identity；dry-run 报告的版本只是候选值，正式发布会重新读取远端并再次检查安全状态。
真实发布通过临时 worktree 对 `gh-pages` 做一次普通 push，发布内容与 validated `dist/site`
逐字节相同，不生成 `release-report.json` 或额外的运行时快照、历史页或详情产品。
若候选树与当前 `gh-pages` 完全相同，则拒绝创建纯 metadata 的空 release commit。

高层 `bgmb release publish` 只接受当前项目的官方 origin
(`github.com/MyKr-YSteinsK/bangumi-side-b` 的 HTTPS/SSH 形式)；本地 bare remote 仅可用于
隔离测试，不能作为真实项目发布 origin。

数据库、下载媒体、报告、build state、prepared state、候选 staging 和发布树都不进入源码
提交；正式站点发布只能由明确的 `bgmb release publish` 流程执行。
