# 更新日志

本文档记录项目的重要变更。

## 尚未发布

### 新增

- 统一生成唯一正式站点 `dist/site`，覆盖当前 SQLite 中已验证的 TV 首播、TV continuing 与剧场版首播季度。
- 新增季度与 Archive 共用的 master-detail 浏览体验，支持搜索、筛选、排序、分页、Hash 直达和移动端 context rail。
- PWA 改为最小 shell、访问资源 runtime cache 与显式季度离线包，Settings 支持 current/year/range/all 队列和人工控制更新。
- 季度离线资源使用 active/staging 状态与 content-addressed verified cache，支持差分续传、更新保留和引用安全清理。
- 统一 `bgmb release prepare/publish`：prepared state 绑定候选与远端状态，仅允许官方 origin，并确认精确 release commit。

### 修复

- Windows 构建预检在输出恢复失败时保留完整 recovery tree，避免丢失唯一旧输出。
- `bgmb status` 与 `bgmb doctor` 识别 prepared release 的有效、过期、无效与可发布状态。
- `bgmb release publish` 对 push 前后远端竞争 fail closed；远端确认成功后的本地 report 或 prepared state 清理问题诚实报告为 warning。
- 更新日志归档已发布内容，并兼容历史 release 中的 `both` 变更类型显示。

### 调整

- 移除旧 Pages profile、快照初始化、角色媒体与多输出发布基础设施；本地预览、Pages 与 PWA 统一使用 `dist/site`。
- 发布不再创建纯 metadata 空版本，也不把本地报告写入正式运行时站点树。

## 0.1.3

### 新增

- 新增 `bgmb doctor`、`bgmb status` 和 `bgmb release prepare/publish`，将本地状态、发布准备与真实发布的安全检查收口。
- 新增只运行测试与静态检查的 GitHub Actions CI；Linux 覆盖 Chromium PWA 回归，Windows 覆盖非浏览器测试。

### 修复

- 程序版本改为源码单一来源，避免虚拟环境的旧包元数据写入错误的 build 或 release 版本。
- 修复 release 变更类型与公开摘要可能互相矛盾的问题。
- PWA 更新会复用旧 active snapshot 中已通过校验的未变化文件。
- Pages WebP 封面加入基于源哈希与转换配方的构建缓存。
- Windows 构建加入输出锁预检、有限短重试与 pending promotion 恢复。

## 0.1.2

### 新增

- 首次初始化完成后，设置页提供季度资料库入口和校验完成提示。
- 更新日志页面展示当前资料版本、程序版本、发布时间、系统变更和资料变更。

### 修复

- Service Worker 已激活但尚未接管页面时，首次初始化会等待控制器并自动继续。
- 一次点击初始化会依次读取资料版本、下载、校验并激活完整快照。

### 调整

- 设置页按主要操作和资料维护分组，清除资料后可直接重新初始化。
- README、更新日志、发布摘要和仓库公开说明改为中文。

## 0.1.1

### 新增

- 提供可安装的 Pages PWA、完整快照校验、暂停、继续、重新下载、清除和手动检查更新。
- 提供 `bgmb` CLI、SQLite 迁移、静态本地与 Pages 构建，以及人工触发的 `gh-pages` 发布流程。

### 修复

- 完整快照排除部署占位文件，并为下载失败提供可恢复的逐文件诊断。
- 发布前拒绝空资料、未验证同步结果和不完整的 Pages 候选内容。

### 调整

- 正式资料范围收窄为 `2026-04` 的日本 TV 动画；不生成续播、角色、声优或相关图片。
- 统一 `sync`、`build` 和 `publish` 的进度报告，并保留独立命令边界。
