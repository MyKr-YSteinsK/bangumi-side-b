# 静态构建

`bgmb build` 只读取本地 SQLite、配置、模板、静态源文件和已验证媒体；不会同步、联网或发布。

```powershell
bgmb build 2026 7
bgmb build 2026
bgmb build 2022-2026
bgmb build --all
```

默认同时生成 `dist/local/` 与 `dist/pages/`。`--target local` 或 `--target pages` 只生成一个 profile。local 保留已验证封面和主要角色图片；Pages 生成 WebP 封面且不生成任何角色图片。

为避免单季度构建留下旧导航、失效详情或已移除黑名单作品，每次 build 都从当前数据库完整重建全部可用季度、详情与首页；命令 scope 仍会记录在构建报告中。构建在 `dist/.staging/` 完成验证后再整体替换 profile 输出，失败时保留之前的完整输出。

首页按季度年月进入最新已构建季度，可直接通过 `file://` 打开。所有站内路径为相对路径，Pages 也兼容仓库子路径。页面运行时不读取 SQLite、不请求 JSON 或远程业务数据。

季度页支持标题搜索、来源/标签/形式/分区筛选、四种评分/人数排序和快速资料抽屉；详情页包含正片章节、主要角色与作品内声优。页面使用 browser history 恢复从详情页返回时的筛选、排序和滚动位置。当前不包含 PWA、资料版本、更新检查、`release.json`、publish 或 `gh-pages`。
