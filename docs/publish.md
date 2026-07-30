# Manual publishing

The first release may be considered only after the reviewed `main` has been
manually pushed, an old workspace/output has been recoverably moved outside the
repository, and this scoped sequence has passed:

```powershell
bgmb sync --progress plain 2026 4
bgmb audit
bgmb build --progress plain --all
bgmb publish --progress plain --dry-run
```

The candidate must contain only `2026-04` Japan TV, its subject covers, and
the Pages shell/PWA files. A dry run does not push or create a `gh-pages`
commit. Real publication remains a separate operator decision after reviewing
the output and report.

```powershell
bgmb build --all
bgmb publish --dry-run
bgmb publish
```

`publish` 不会调用 sync 或 build。它要求 `main` 干净、Pages build marker 与当前 HEAD 匹配且
`subject_count > 0`、build-bound snapshot 含作品与季度卡片、候选目录含详情页和季度卡片；
此外 `dist/pages` 不得含角色图片/数据库/本机路径/敏感信息，并验证 manifest、Service Worker、
链接和 PWA Shell。上述任何空产物在 dry-run 前即拒绝，因此不会计算版本、创建 `gh-pages` commit
或 push。

dry-run 会完整组装 release staging、计算 tentative 版本并写安全报告，但不创建 `gh-pages` commit、不会 push、不会登记本地成功状态。真实发布从远程 `gh-pages` 读取 release 版本，以临时 worktree 创建 `release: YYYY.MM.DD.N`，只 push `HEAD:gh-pages`，绝不 force push 或修改 main。无变化会拒绝空版本；push 失败会保留远程旧站点、旧 PWA snapshot 和本地成功状态。

第一次真实发布前，操作人应手动 push 已审查的 `main`，运行 dry-run，并确认 Git 远程和 `gh-pages` 写权限。dry-run 同样只读远端 release 来计算 tentative 版本；真实 origin 发布只允许 `gh-pages` 且要求 `HEAD == origin/main`。远端成功后的本地镜像失败仅报告 warning，不应重复发布。第三方 Bangumi 数据、封面和角色图片不随 MIT 源码许可证授权。

## 控制台进度

`publish` 默认使用 `--progress auto`，并支持 `--progress plain|off`、`--verbose` 和
`--quiet`（quiet 与 verbose 互斥）。进度、远端读取、retry、warning 和 heartbeat 写入
stderr；最终结果与 `workspace/reports/...` 报告路径写入 stdout。不会显示认证信息、
本机路径或 Git 输出详情。

dry-run 依次显示 main/worktree、远端与分支、build marker、build-bound facts、data
generation、candidate index、远端 gh-pages、tentative 版本、变更、release staging、
snapshot manifest、updates 页面、安全扫描和报告。读取远端前会先显示该阶段。真实发布
额外显示临时 worktree、release commit、即将推送的 release、本地状态/镜像和清理；推送
期间会 heartbeat。若 push 被中断，命令只重新读取远端判断结果，绝不自动重复 push；无法
确认时会要求人工检查。
