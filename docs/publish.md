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

`release prepare` 要求 `main`、干净工作树、可确认的 HEAD、可读配置和当前 SQLite。它使用
当前统一资料审计与 `UnifiedSiteBuilder`，不 sync，也不要求此时 `HEAD == origin/main`。
随后会验证实际 `dist/site`：核心 HTML/CSS/JS/PWA 文件、archive
索引、至少一个完整公开季度，以及数据库、workspace、报告、敏感文件、绝对本机路径和旧
角色媒体安全规则。

成功后写入 `workspace/state/prepared-release.json`（schema 2），只绑定 source commit、
程序版本、候选内容哈希、文件数、总字节数、公开季度和准备时的远端 `gh-pages` commit。
候选哈希来自排序后的相对路径、文件 SHA-256 和大小；build state 只作为一致性校验，不能
替代真实文件树。

`release prepare` 会在临时 staging 上执行 dry-run，不修改 `main`、`dist/site` 或远端。
`release publish` 不 sync、不 build；它重新检查 prepared state、干净工作树、`HEAD`、
`HEAD == origin/main`、`dist/site` 精确哈希和远端 `gh-pages` commit。任何变化都会拒绝，
要求重新 prepare。

真实发布通过临时 Git worktree，把 `dist/site` 原样复制到 `gh-pages`，创建普通提交并执行
`git push HEAD:gh-pages`；不 force push、不修改 main、不发布 `gh-pages` 以外的内容。dry-run
和测试使用隔离目录或 bare remote，禁止操作用户真实站点。

## 状态与报告

```powershell
bgmb status
bgmb doctor --local
bgmb doctor
```

`status` 不访问网络；`doctor --local` 也只读本地，普通 `doctor` 才刷新 `origin/main` 和
`gh-pages`。报告与 build state 位于 workspace，均为派生状态，不提交 Git。
