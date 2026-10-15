# 正式单站点发布

正式发布源是当前已经验证的 `dist/site/`，不是旧的 Pages/local profile，也不生成第二棵
产品树。运行时站点、localhost 预览和 GitHub Pages 使用同一套静态文件。

## 流程

```powershell
bgmb sync 2026 7
bgmb build --all                 # 可选；prepare 会再次离线收敛
bgmb release prepare
git push origin main
bgmb release publish
```

`release prepare` 要求 `main`、干净工作树、可确认的 HEAD、可读配置和当前 SQLite。它先执行
本地统一资料审计，再调用 `UnifiedSiteBuilder` 离线收敛正式 `dist/site`，可能更新站点、
`workspace/build-state.json` 和构建报告等本地派生状态。独立的 `bgmb build` 完全离线；
prepare 不执行 Bangumi sync，也不要求此时 `HEAD == origin/main`。
随后会验证实际 `dist/site`：核心 HTML/CSS/JS/PWA 文件、archive
索引、至少一个完整公开季度，以及数据库、workspace、报告、敏感文件、绝对本机路径和旧
角色媒体安全规则。

成功后写入 `workspace/state/prepared-release.json`（schema 2），只绑定 source commit、
程序版本、候选内容哈希、文件数、总字节数、公开季度和准备时的远端 `gh-pages` commit。
候选哈希来自排序后的相对路径、文件 SHA-256 和大小；build state 只作为一致性校验，不能
替代真实文件树。prepared state 的 source/content/remote identity 必须是严格的小写 Git
hex，公开季度必须是排序唯一的合法季度列表，报告路径必须是项目内相对路径。

构建和正式候选验证完成后，`release prepare` 将 `dist/site` 复制到临时 staging，再验证复制的
候选并执行发布 dry-run。这个 dry-run 阶段不修改正式 `dist/site` 或远端，但会写入本地
dry-run report。

dry-run 会读取远端 `gh-pages` 的 commit 和提交消息，以确认远端身份并推导候选批次；实现使用
`git ls-remote`，并在远端分支存在时向临时 bare repository 做 shallow fetch。因此整个
prepare 需要 Git 远端可读，不能作为完全无网络操作。它不访问 Bangumi、不修改源码分支、
不执行任何 Git push，也不发布 Pages。

`release publish` 不 sync、不 build；它重新检查 prepared state、干净工作树、`HEAD`、
`HEAD == origin/main`、`dist/site` 精确哈希和远端 `gh-pages` commit。任何变化都会拒绝，
要求重新 prepare。

如果 validated `dist/site` 与当前 `gh-pages` 文件树完全相同，发布会拒绝创建只改变提交消息的
空版本；资料版本号不会通过 `--allow-empty` 人为推进。

真实发布通过临时 Git worktree，把 `dist/site` 原样复制到 `gh-pages`，创建普通提交并执行
`git push HEAD:gh-pages`；提交消息严格为 `release: YYYY.MM.DD.N [source <12位 commit>]`，
版本只从当前远端 `gh-pages` HEAD 消息推导，发布树不包含 `release-report.json`。不 force
push、不修改 main、不发布 `gh-pages` 以外的内容。高层真实发布要求官方项目 origin；dry-run
和测试使用隔离目录或 bare remote，禁止操作用户真实站点。

普通 push 前会记录本地 release commit SHA；push 返回后，只有远端 `gh-pages` HEAD 与该 SHA
完全一致才确认本次发布。如果远端在确认前再次推进，不会把另一个 actor 的 commit 归因为本次
release。远端已确认成功后，本地 report 定稿或 prepared state 清理失败会由 CLI 输出 warning，
退出结果仍表示远端发布已经发生；残留 prepared state 会因远端绑定失效而阻止重复发布。

## 状态与报告

```powershell
bgmb status
bgmb doctor --local
bgmb doctor
```

`status` 不访问网络；`doctor --local` 也只读本地，普通 `doctor` 才刷新 `origin/main` 和
`gh-pages`。build state、dry-run report 和 prepared state 均为 workspace 内的本地派生状态，
不提交 Git。
