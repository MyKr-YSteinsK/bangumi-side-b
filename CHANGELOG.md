# 更新日志

本文档记录项目的重要变更。

## 尚未发布

## 0.6.1 - 2026-08-23

### 修复

- 修复 Grid/List、排序与筛选虽然调用 View Transition API，但正常路径立即取消动画的问题；支持的浏览器现在会真正执行作品位置连续过渡。

## 0.6.0 - 2026-08-23

### 新增

- 全站正式页面统一显示 `Bangumi Side B · MyKr` 署名，并在 Settings ABOUT 显示作者与版本。
- 增加 progressive View Transition 浏览动效，支持 Grid / List、排序、筛选与季度导航的连续浏览。
- Grid / List 偏好在首次绘制前恢复，切换季度时保持已选择的布局。

### 调整

- 海报预览改为无可见关闭按钮、点击遮罩退出的居中预览；点击海报本身保持打开，Escape 仍可退出。
- 排序菜单改为独立锚定浮层，打开和关闭不推动作品列表。
- TV 首播与续播改为真实统一 DOM 排序序列，同时保留续播事实标识。

### 修复

- 修复 List 偏好切季度时短暂显示 Grid。
- 修复首播/续播数据已统一排序但实际 DOM 仍分段的问题。
- 修复排序菜单打开时推动作品列表的问题。

## 0.5.1 - 2026-08-23

### 修复

- 移动端 Grid 元信息改用紧凑日期并稳定保持单行，不优先隐藏来源标签。
- 短详情页打开后冻结背景文档与作品列表；长详情只滚动详情自身，关闭后精确恢复列表位置。
- standalone edge swipe 从手势起始阶段就保持前景详情与底层列表稳定，小距离取消不会重复触发 history。
- 海报放大预览改为透明 dialog 与页面级半透明遮罩，扩大图片并提供安全区内 44px 关闭按钮。
- 补齐 0.1.1、0.1.2、0.1.3 的历史发布日期，并继续由 Settings 复用同一 CHANGELOG 解析结果。

## 0.5.0 - 2026-08-22

### 新增

- Quarter 与 Archive 增加 Grid / List 浏览视图切换；Grid 保持封面优先，List 保留小封面并提供更高密度的标题、原名、日期、来源和评分信息。
- 视图选择使用 `bsb-browse-view-mode` 写入浏览器 localStorage，刷新、切换季度或进入 Archive 后继续保持。

### 修复

- 移动端卡片评分固定在封面右上角，缺失评分不再伪造 `0.0`；标题、原名和元信息行预留稳定高度。
- 移动端详情改为安全区内紧凑返回顶栏，保留 hash/history 返回与焦点/滚动恢复。
- standalone PWA 详情右滑返回改为显式 possible-drag / dragging / cancel / commit 状态机，拖动期间冻结背景列表，取消与提交都不会重复触发 history。

## 0.4.0 - 2026-08-21

### 新增

- 移动端季度浏览改为封面优先的两列连续列表，手机季度页隐藏桌面分页，并提供一次点击相邻季度与两步内选择公开季度的快速导航。
- TV 首播与续播在 Quarter / Archive 中合并为同一排序列表，以“续播”标识保留事实差异。
- 移动端筛选改为可取消的 draft bottom sheet；作品详情改为全屏层，统一支持返回、history 与 standalone PWA 左缘右滑。
- Settings 离线资料、下载任务、批量下载、存储与应用、高级诊断、更新日志按用户任务重新分组。

### 修复

- 修复移动端封面被旧规则隐藏、详情返回滚动位置跳动，以及季度浏览首屏被离线管理区挤占的问题。
- 修复下载进度触发整个 Settings 高频重绘、selector/focus 闪动和旧异步结果覆盖新状态的问题。
- 统一手机安全区与 `viewport-fit=cover`，并让普通离线状态使用用户可读文案，技术状态下沉到高级诊断。

## 0.3.1 - 2026-08-20

### 修复

- 修复 Bangumi `total_episodes` 被错误当作计划总话数证据的问题。
- 在 `eps` 与当前数据库章节数量不一致时，不再把已知计划总话数错误降级为未知。
- 明确 `episode_count` 仅表示计划正片总话数；episode registry 只有在完整连续主线验证后才作为 fallback。

## 0.3.0 - 2026-08-19

### 修复

- Quarter 与 Archive 的评分列表和 Detail 统一固定显示一位小数；缺失评分继续显示为缺失，不伪造 `0.0`。
- 修复当前季度 TV 总集数事实链：571784 等作品使用已验证的计划正片总话数；未知集数不再显示为 `0`。
- 同步报告增加总话数来源审计，区分 canonical subject、精确 Infobox、主线 episode registry 与 unknown。

### 新增

- Settings 新增静态 05 / CHANGELOG，离线显示当前程序版本、尚未发布内容、当前 release 与历史版本折叠状态；不在运行时请求 GitHub 或 CHANGELOG。
- `bgmb serve` 成功绑定后打印可复制的 Pages 子路径 URL 与 Ctrl+C 退出提示；默认不打开浏览器，`bgmb serve --open` 才会请求系统默认浏览器，启动失败只告警并继续服务。
- `bgmb audit` 将全库 subject 数明确标为“数据库总作品”，并为每个可发布季度输出 TV 首播、TV 续播、剧场版与合计 appearance 组成。
- Quarter 与 Archive 浏览工作区完成响应式收口：桌面 detail / filter 使用 viewport 高度和内部滚动，767px 及以下改为无 context rail 的全宽单栏详情与筛选。
- 筛选工作区增加 context-aware counts、可换行 chip cloud、活动筛选、清除全部和当前结果数；实时筛选返回列表时保留浏览上下文与可恢复滚动位置。
- 补齐 1199、1024、900、768、767、390、360 等边界宽度和 Quarter / Archive 浏览交互回归，覆盖 hash、历史导航、焦点返回、空结果、缺失事实与移动端无横向溢出。
- 建立 Visual System V2：统一 surface、边界、圆角、控件高度、浮层和动效状态；Quarter、Archive 与 Settings 的用户可见选择器改为支持键盘与触屏的自定义 Select/Listbox，并补齐移动端触控尺寸与 reduced-motion 行为。
- 同步新增自动永久冷门黑名单：可靠首播超过 7 天且评分人数少于 30 的日本 TV/MOVIE 会在季度归属前被审计化排除；配置保留人工与自动来源、标题注释和可恢复的跨资源清理状态。
- 修正同步报告的黑名单来源统计，明确区分人工命中、历史自动命中和本次新增自动拉黑，不再把历史自动条目误报为人工条目。
- 修复 discovery partial payload 覆盖最终事实的问题：Browse/Search 现在只发现候选，canonical subject detail 负责持久化 facts，并在报告中记录正式 detail 请求数。
- 收敛来源与集数事实：加入经实际数据验证的精确 source tags；只接受正整数集数，严格回退 Infobox `话数`，旧 `0` 在 public projection 中表现为未知。
- 统一 REVIEW 口径：sync summary、`bgmb review YEAR QUARTER_MONTH` 与 `audit` 只统计带季度作用域且已持久化的 REVIEW；无作用域和 Search-only findings 单独报告。
- 同步报告增加 bounded 来源计数与 known/unknown 集数聚合，便于审计 unknown / legacy zero 的原因而不泄漏原始 API 响应。
- 新增信息不足型未决冷门自动淘汰：明确 allowlist 中的 REVIEW 立即永久写入自动黑名单，与季度成熟度和评分人数无关；冲突 REVIEW 仍保留人工裁决。
- 同步审计报告增加 `insufficient_airing_information` 与 `low_rating_count` 原因维度；Search-only 冷门候选不会创建 SQLite 作品或封面，也不会污染 external REVIEW。

## 0.2.0 - 2026-08-13

### 新增

- 统一生成唯一正式站点 `dist/site`，覆盖当前 SQLite 中已验证的 TV 首播、TV continuing 与剧场版首播季度。
- 新增季度与 Archive 共用的 master-detail 浏览体验，支持搜索、筛选、排序、分页、Hash 直达和移动端 context rail。
- PWA 改为最小 shell、访问资源 runtime cache 与显式季度离线包，Settings 支持 current/year/range/all 队列，以及暂停、继续、取消、重开与联网恢复。
- 季度离线资源使用 active/staging 状态与 content-addressed verified cache，支持差分续传、更新保留和引用安全清理。
- 统一 `bgmb release prepare/publish`：prepared state 绑定候选与远端状态，仅允许官方 origin，并确认精确 release commit。

### 修复

- Quarter 与 Archive 统一使用紧凑分页；筛选选项搜索在重渲染与媒体切换后保持，详情完整展示全部结构化别名。
- 增量构建使用 staging 与原子 patch，失败时恢复旧输出并按季度保留 last-good；Windows 预检在极端恢复失败时保留唯一 recovery tree。
- `bgmb status` 与 `bgmb doctor` 识别 prepared release 的有效、过期、无效与可发布状态。
- `bgmb release publish` 对 push 前后远端竞争 fail closed；远端确认成功后的本地 report 或 prepared state 清理问题诚实报告为 warning。
- 更新日志归档已发布内容，并兼容历史 release 中的 `both` 变更类型显示。

### 调整

- Repository、sync 与站点投影改为批量事实读取，并对大型 ID 集合使用有界分块查询，避免 N+1 与 SQLite 参数上限。
- CI 明确区分 Linux Chromium 浏览器回归与 Windows 非浏览器套件，并收紧浏览器端口和并发完成信号的确定性。
- 移除旧 Pages profile、旧的全库快照初始化、角色媒体与多输出发布基础设施；本地预览、Pages 与 PWA 统一使用 `dist/site`。
- 发布不再创建纯 metadata 空版本，也不把本地报告写入正式运行时站点树。

## 0.1.3 - 2026-08-07

### 新增

- 新增 `bgmb doctor`、`bgmb status` 和 `bgmb release prepare/publish`，将本地状态、发布准备与真实发布的安全检查收口。
- 新增只运行测试与静态检查的 GitHub Actions CI；Linux 覆盖 Chromium PWA 回归，Windows 覆盖非浏览器测试。

### 修复

- 程序版本改为源码单一来源，避免虚拟环境的旧包元数据写入错误的 build 或 release 版本。
- 修复 release 变更类型与公开摘要可能互相矛盾的问题。
- PWA 更新会复用旧 active snapshot 中已通过校验的未变化文件。
- Pages WebP 封面加入基于源哈希与转换配方的构建缓存。
- Windows 构建加入输出锁预检、有限短重试与 pending promotion 恢复。

## 0.1.2 - 2026-08-01

### 新增

- 首次初始化完成后，设置页提供季度资料库入口和校验完成提示。
- 更新日志页面展示当前资料版本、程序版本、发布时间、系统变更和资料变更。

### 修复

- Service Worker 已激活但尚未接管页面时，首次初始化会等待控制器并自动继续。
- 一次点击初始化会依次读取资料版本、下载、校验并激活完整快照。

### 调整

- 设置页按主要操作和资料维护分组，清除资料后可直接重新初始化。
- README、更新日志、发布摘要和仓库公开说明改为中文。

## 0.1.1 - 2026-08-01

### 新增

- 提供可安装的 Pages PWA、完整快照校验、暂停、继续、重新下载、清除和手动检查更新。
- 提供 `bgmb` CLI、SQLite 迁移、静态本地与 Pages 构建，以及人工触发的 `gh-pages` 发布流程。

### 修复

- 完整快照排除部署占位文件，并为下载失败提供可恢复的逐文件诊断。
- 发布前拒绝空资料、未验证同步结果和不完整的 Pages 候选内容。

### 调整

- 正式资料范围收窄为 `2026-04` 的日本 TV 动画；不生成续播、角色、声优或相关图片。
- 统一 `sync`、`build` 和 `publish` 的进度报告，并保留独立命令边界。
