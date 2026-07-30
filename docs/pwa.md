# Pages PWA

## Current release scope

The PWA candidate contains only the configured `2026-04` Japan-TV archive,
its subject detail pages, subject covers, shell, settings, updates, and offline
pages. It contains no historical or future quarter, movie, continuation,
role, person, or character media. A normal startup reads the active verified
snapshot and never checks for a new release automatically.

Pages 使用完整静态快照，不使用业务 IndexedDB、SQLite、运行时 Bangumi API 或在线阅读回退。业务 HTML、CSS、JavaScript、封面、图标、设置、更新日志和离线页都进入 Cache Storage；角色图片永远不进入 Pages。

首次启动没有 active snapshot 时，Gate 会阻止资料浏览。用户点击初始化后，Service Worker 读取 `release.json` 与 `snapshot-manifest.json`，检查 schema、scope、大小、SHA-256 和完整 content hash，再以最多三个并发请求下载 staging cache。暂停、关闭后重开会保留已校验内容；取消会删除 staging。只有完整校验成功后才原子写入 active pointer，之后才删除旧 cache。

正常启动只读取 active snapshot，不自动请求 release 或下载。设置页的“检查资料库更新”是唯一常规更新入口；失败更新保留旧 active，重下当前版本也先写新 attempt cache。清除资料只删除 active/staging，保留最小应用 Shell 与设置界面。详情页离线匹配忽略 `?from=YYYY-MM` 等导航 query；静态资源必须精确匹配。下载命令会立即确认，暂停或关闭保留 staging，恢复时重新校验缓存文件。
