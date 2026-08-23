# Bangumi Side B 视觉系统

## 设计职责

页面把「唱片 B 面 × 日本播出档案 × 现代编辑部动画年鉴」用于资料编排，而不是音乐播放器拟物。`SIDE B`、季度编号、细线、双线分隔和档案代码提供识别；作品事实、标题和评分保持阅读优先。页面不使用唱片纹理、霓虹、玻璃拟态、发光边框、渐变大背景或后台式卡片墙。

## 令牌

基础令牌在 `static/css/site.css` 的 `:root`。Visual System V2 先用语义别名把纸面内容层和应用交互层分开；组件只引用语义令牌，不直接复制颜色。

| 语义层 | V2 令牌 | 当前含义 |
| --- | --- | --- |
| 页面 / 内容 | `--surface-page` / `--surface` | 页面纸面与普通内容表面 |
| 提升 / 弱化 | `--surface-raised` / `--surface-subtle` | 输入、内容承载与弱背景 |
| 选中 / 浮层 | `--surface-selected` / `--surface-overlay` | 选中状态与更新/菜单浮层 |
| 边界 | `--border-subtle` / `--border-default` / `--border-strong` | 弱分隔、普通控件边框、强调边框 |

基础颜色令牌仍为：

| 用途 | 令牌 | 起始色 |
| --- | --- | --- |
| 纸面背景 | `--paper` / `--paper-raised` | `#F5F1E8` / `#FFFDF8` |
| 正文 / 次级文字 | `--ink` / `--ink-muted` | `#17201D` / `#56605B` |
| 细线 / 强分隔 | `--rule` / `--rule-strong` | `#C9C6BB` / `#72776F` |
| 弱表面 | `--surface-muted` | `#E8E4DA` |
| 可见键盘焦点 | `--focus` | `#1E5D9C` |

控件基础度量为 `--control-height-compact`（32px）、`--control-height-normal`（40px）和
`--control-height-touch`（44px）；默认圆角为 `--radius-small`（4px）、
`--radius-control`（7px）和 `--radius-popover`（10px）。标签仍可使用 pill 语义，但普通按钮、输入框和菜单不使用胶囊圆角。
浮层只使用 `--shadow-popover` / `--shadow-dialog`，普通列表、按钮和输入框不堆叠厚阴影。

季度强调色仅用于编号、细线、选中态、来源小标签和评分：01 `#8A3147`，04 `#287565`，07 `#C95C32`，10 `#3C4F72`。正文始终使用深色墨色；浅色纸面上的正文和焦点均以人工审查的高对比配对使用，不把季节色用于长段落正文。

## 控件与浮层

按钮、输入框、搜索框、分页和 segmented control 共用 V2 的高度、边界和状态层级。默认状态使用普通边界，hover 只提升表面对比，pressed / selected 使用 `--surface-selected` 与当前季度强调色，disabled 降低对比但不隐藏文字；键盘操作始终使用清晰的 `:focus-visible` 外框。普通控件不使用厚阴影，菜单、选择器和更新提示才使用 `--surface-overlay` 与浮层阴影。

排序菜单和选择器都锚定触发器，打开时更新 `aria-expanded`，关闭时支持 Escape、点击外部和再次点击触发器。带明确触发器的浮层关闭后把焦点还给触发器；鼠标打开菜单不会制造不必要的焦点跳闪。

## Select / Listbox

站点不向用户展示原生 `<select>` 展开 UI。Archive、Quarter 和 Settings 使用共享的轻量 `window.BsbListbox`：按钮触发器关联 `role="listbox"`，选项使用 `role="option"` 与 selected 状态，支持 Arrow Up / Down、Home / End、Enter / Space、Escape、Tab、点击外部关闭和再次打开。选择后立即调用原有状态更新与 localStorage 逻辑，长选项在自身可滚动的浮层内显示，不撑宽页面。

Listbox 在桌面锚定触发器，在接近视口边缘时保持可见；移动端触发器和选项至少使用 `--control-height-touch`。它是原生 JavaScript 小型基础层，不引入组件框架或第三方定位依赖。

## 排版与网格

系统字体按 `system-ui`、Windows/Apple 中文系统字体与 `Noto Sans CJK SC` 回退；档案编号使用系统等宽栈。正文行高为 1.6，显示标题不依赖极粗或营销式巨型 Hero。内容最大宽度为 1640px。

卡片网格明确为：1920px 附近 7 列、1440px 附近 6 列、普通桌面 5 列、小屏笔记本 4 列、760px 以下 2 列。卡片以封面外的信息区承载标题、事实与评分；小圆角只用于标签，主体使用细线和留白建立层级。

Quarter 与 Archive 的长分页共用紧凑窗口：始终保留首尾页与当前页上下文，缺口使用不可交互的省略号，不随历史页数线性增加页码按钮。

Quarter 与 Archive 的结果工具栏共用轻量的 Grid / List segmented control。Grid 在移动端使用两列封面卡片，在桌面使用响应式卡片网格；List 在所有宽度保留小封面，并把标题、原名、日期、来源和评分压缩为高密度行。模式只改变展示层，不重置搜索、筛选、排序、分页、详情 hash 或 history；偏好使用 `bsb-browse-view-mode` 写入 localStorage，缺失或不可写时回退到 Grid。

基础控件在 1920、1440、1280、1199、1024、900、768、767、390 和 360 宽度下保持可用：桌面工具栏使用紧凑网格，窄屏转为可换行布局，搜索和选择器不压缩到不可读；可触控控件至少 44px 高，页面不产生新的横向滚动。Plan 39 进一步将 Quarter / Archive 的桌面 master/detail/filter 工作区延伸到可用 viewport，高度内容在工作区内部滚动；767px 及以下详情与筛选改为不保留 context rail 的单栏全宽工作区。

筛选工作区使用 context-aware counts 和可换行 chip cloud。计数会保留当前搜索与其它维度的约束，当前维度自身按未选条件计算；已选项、零结果项和键盘焦点均保持可见层级。移动端筛选面板同时提供活动筛选、清除全部、当前结果数和返回结果，不把实时筛选伪装成提交型 Apply。

## 页面骨架

所有页面由统一 builder 生成同一套原生 HTML shell：skip link、档案栏、品牌、季度标识、主内容和页脚保持一致，不存在第二套模板或前端输出。

Settings 按 01 / 离线季度、02 / 下载任务、03 / 批量下载、04 / 存储与应用、05 / 高级诊断、
06 / CHANGELOG 排列。更新日志使用原生 `<details>` / `<summary>`：standalone patch 直接显示，
`major.minor.0` 作为默认收起的 milestone，并在一级 summary 同时显示 anchor release 的正式
日期；展开 milestone 后 child release 直接全部可见，不再嵌套第二层折叠。长条目可换行，内容
直接写入静态 HTML，手机宽度不新增横向滚动。版本号与日期来自同一 CHANGELOG 事实源，缺少
对应 release 时只显示版本号，不伪造 release。

移动端 Quarter / Archive 的 Grid 采用两列、2:3 封面优先卡片，元信息分别使用 `MM-DD` / `YY-MM-DD` 紧凑日期并保持单行；List 采用保留小封面的高密度行，继续显示完整日期。季度页连续展示结果，Archive 保留自己的分页。详情在移动端是覆盖视口的全屏层，打开即冻结背景文档和已布局的 master list，顶部为安全区内的紧凑返回箭头，筛选是带草稿状态和安全区内边距的 bottom sheet；短详情不制造无效滚动，长详情只滚动自身，返回会恢复列表位置。standalone PWA 左缘右滑只在水平拖动确认后移动完整 foreground detail surface，背景列表保持冻结，起始小距离、取消或 commit 都不会切换底层 visibility 或重复触发 history。海报 Lightbox 使用透明 dialog、页面级半透明 backdrop 和 contain 原图，不提供可见关闭按钮，点击遮罩或按 Escape 退出，点击海报本身不会关闭。`viewport-fit=cover` 配合统一 safe-area 变量保护 header、详情、sheet、toast、Lightbox 和底部操作，`prefers-reduced-motion` 会把回弹和非必要过渡压缩到近乎即时。

页脚说明资料归属及“无运行时远程请求”。每个可交互元素需要可见的 `:focus-visible`；无数据、缺图、无评分、长标题等状态都有稳定的文档流位置，不用伪造事实填补。

## 动效与无障碍

普通交互使用 `--motion-fast` 或 `--motion-standard` 自然减速过渡：卡片 hover 最多上移 2px，边线变色而非厚阴影。抽屉、菜单和更新提示才使用浮层阴影。无持续动画、3D 翻转或视差。`prefers-reduced-motion: reduce` 把位移、缩放和非必要过渡压缩到近乎即时。

## 浏览连续性

Quarter 与 Archive 的 TV 结果使用同一条真实 DOM 序列，排序按当前 pipeline 结果移动已有作品节点，不重新创建重复卡片。排序浮层使用固定定位锚定触发按钮，打开和关闭不改变结果列表几何位置；Grid / List 只改变展示层，作品节点身份基于 subject、季度和 appearance 保持稳定。

浏览连续性不依赖 CSS View Transition 或跨文档 root snapshot。运行时使用很薄的
`withResultMotion(reason, root, mutate)`：只捕获 viewport 及上下缓冲区内、最多 32 个已有作品节点，
在 DOM mutation 后用 FLIP/WAAPI 恢复其位置；TV/MOVIE 或新季度集合使用最多 12 个短促入场节点。
快速重入会取消旧节点动画，不建立队列；新季度首屏最多 10 项使用一次性的轻量渐入。`prefers-
reduced-motion: reduce` 会跳过位置、入场和 stagger，但不跳过状态更新。

CSS 不加载远程字体或 CDN。运行时 JavaScript 只处理页面交互、PWA 缓存与下载，并请求构建生成的同源 JSON 和静态资源；不能读取 SQLite、访问 Bangumi 或其它远程业务 API、载入远程图片或埋点。
