# Manual publishing

```powershell
bgmb build --all
bgmb publish --dry-run
bgmb publish
```

`publish` 不会调用 sync 或 build。它要求 `main` 干净、Pages build marker 与当前 HEAD 匹配、`dist/pages` 没有角色图片/数据库/本机路径/敏感信息，并验证 manifest、Service Worker、链接和 PWA Shell。

dry-run 会完整组装 release staging、计算 tentative 版本并写安全报告，但不创建 `gh-pages` commit、不会 push、不会登记本地成功状态。真实发布从远程 `gh-pages` 读取 release 版本，以临时 worktree 创建 `release: YYYY.MM.DD.N`，只 push `HEAD:gh-pages`，绝不 force push 或修改 main。无变化会拒绝空版本；push 失败会保留远程旧站点、旧 PWA snapshot 和本地成功状态。

第一次真实发布前，操作人应手动 push 已审查的 `main`，运行 dry-run，并确认 Git 远程和 `gh-pages` 写权限。第三方 Bangumi 数据、封面和角色图片不随 MIT 源码许可证授权。
