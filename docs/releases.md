# Release metadata

程序版本来自包的语义化版本。资料版本使用 UTC `YYYY.MM.DD.N`：同一 UTC 日期的成功发布依次递增，未成功 push 的 tentative version 不会被登记或消耗。

`snapshot-manifest.json` 是完整离线文件清单。每项保存同源 URL、SHA-256、字节数、MIME 类型和类别；manifest 自身、`release.json` 与 release history 不参与自己的 content hash，避免循环依赖。`release.json` 保存 manifest SHA-256、版本、数量、大小、变更摘要与候选内容 hash。`release-history.json` 仅保留精简历史，更新日志页面在发布 staging 中渲染，不在运行时请求历史。

本地 `workspace/releases/` 只在远程 push 成功后登记快照事实索引和发布历史。它们、报告和候选 staging 都不进入 Git。每次成功 Pages build 会同时写入 marker 与紧凑事实快照；publish 只读取这些 build-bound 文件。sync 成功后必须重新 build 才能发布。
