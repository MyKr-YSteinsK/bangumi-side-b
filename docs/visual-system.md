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

基础控件在 1920、1440、1280、1024、768、390 和 360 宽度下保持可用：桌面工具栏使用紧凑网格，窄屏转为可换行布局，搜索和选择器不压缩到不可读；可触控控件至少 44px 高，页面不产生新的横向滚动。Plan 38 只保证基础控件的响应式，不改变详情或筛选页的信息架构。

## 页面骨架

所有页面由统一 builder 生成同一套原生 HTML shell：skip link、档案栏、品牌、季度标识、主内容和页脚保持一致，不存在第二套模板或前端输出。

页脚说明资料归属及“无运行时远程请求”。每个可交互元素需要可见的 `:focus-visible`；无数据、缺图、无评分、长标题等状态都有稳定的文档流位置，不用伪造事实填补。

## 动效与无障碍

普通交互使用 `--motion-fast` 或 `--motion-standard` 自然减速过渡：卡片 hover 最多上移 2px，边线变色而非厚阴影。抽屉、菜单和更新提示才使用浮层阴影。无持续动画、3D 翻转或视差。`prefers-reduced-motion: reduce` 把位移、缩放和非必要过渡压缩到近乎即时。

CSS 不加载远程字体或 CDN。运行时 JavaScript 只处理页面交互、PWA 缓存与下载，并请求构建生成的同源 JSON 和静态资源；不能读取 SQLite、访问 Bangumi 或其它远程业务 API、载入远程图片或埋点。
