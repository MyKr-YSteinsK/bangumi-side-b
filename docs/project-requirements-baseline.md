# Bangumi Side B｜项目需求基线

本文件保存已经确认的产品事实。普通技术选择不得改变这些行为。

## 1. 标识与边界

- 展示名称：`Bangumi Side B by MyKr`
- 仓库：`https://github.com/MyKr-YSteinsK/bangumi-side-b`
- 本地目录：`D:\CS\bangumi-side-b`
- Python 包：`bgm_side_b`
- CLI：`bgmb`
- 网站标题：`Bangumi Side B｜MyKr`
- PWA 短名称：`BGM B`
- MIT 仅覆盖源码，不授权 Bangumi 数据、封面、角色图片或其他第三方内容。

项目独立于 MyKr-ops。当前不得修改 MyKr-ops、复制其实现、使用 submodule 或提前建设集成层和插件系统。

## 2. 产品架构

```text
Bangumi API
    ↓
本地同步与确定性规范化
    ↓
SQLite + workspace/covers
    ↓
唯一静态网站生成（dist/site）
    └── localhost preview / GitHub Pages / PWA
```

页面运行时只使用静态 HTML、CSS、原生 JavaScript；不读取 SQLite、不访问 Bangumi API、不请求远程业务数据。页面通过同源生成 JSON 读取详情 payload；不生成 Subject 独立详情文件。

禁止 React、Vue、Node.js 前端构建链、大型 UI 框架、SQLite WASM 和运行时业务 IndexedDB。

## 3. 第一版范围

当前开发资料范围以 SQLite 中已验证的受管季度为准（当前为 `2026-04` 与 `2026-07`）。
只同步、保存和构建 Anime type=2 的 TV 与剧场版；TV 支持 premiere/continuing，Movie 只支持
premiere。仍不处理 WEB、OVA、OAD、角色、声优、角色图片或声优图片。

日本 TV 与剧场版判定完全自动化；无法确定的候选进入持久化 REVIEW，不以默认规则放行。
国家/地区分类按以下确定性顺序执行：

1. 已验证的 `Subject.meta_tags` 公共地区证据为 primary evidence；
2. 缺失公共地区证据时，严格回退到 Infobox 国家/地区 key；
3. 精确的日本 token（`日本` 或 `Japan`）收录，明确 non-Japanese 值排除；
4. 日本与其它地区并存、两类结构化 evidence 冲突或没有 evidence，均进入 REVIEW。

只处理 Anime `type == 2` 的 TV 与剧场版。标签和 token 均只做 NFKC、trim 和精确匹配；绝不以
标题、简介、语言、公司、普通社区标签或 AI 推断国家。同步审计保存使用的证据与 REVIEW 原因。

## 4. 季度

合法季度月：`1 / 4 / 7 / 10`。

完整结构化首播日期是事实日期；TV 的 premiere quarter 是 archive/cour ownership。
通常自然季度与 premiere quarter 相同；季度边界前后 7 天以内，只有精确社区季度标签达到
高置信共识时才可解析为下一 cour，否则进入 REVIEW。跨季度 TV 只在可靠结构化 `end_date`
或临时 MainStory Episode airdate probe 证据下增加 continuing appearance；Episode 不持久化、
不展示。Movie 不得 continuing。缺日期、范围或续播证据时不猜测，不进入新的公开季度。

## 5. API 与同步

候选发现按同步目标季度查询 TV 与 Movie，合并并按 subject ID 去重。最终季度归属以详情中的
完整结构化日期、人工裁决和续播证据为准，随后执行自动的日本-only 分类。

- 匿名访问；
- 明确 User-Agent；
- 默认并发 3；
- 临时错误最多重试 3 次；
- 429 尊重 `Retry-After`；
- 指数退避与少量抖动；
- 非临时 400/404 不盲目重试；
- 官方文档结合少量真实 Fixture 验证。

命令：

```powershell
bgmb sync 2026 4
```

`sync` 联网获取事实与封面；事实成功提交后触发受影响范围的增量 build。`build` 完全离线，
`serve` 只服务已有 `dist/site`，`bgmb release publish` 从不调用 `sync` 或 `build`。

评分和评分人数每次刷新；稳定详情可增量复用；失败项重试；成功数据保留；局部失败最终
退出码非零；Ctrl+C 停止新请求并保证当前事务完成或回滚。发现候选但日本 TV 收录为 0 时
同步失败且不生成数据代次；候选至少 20 部而收录率低于 20% 时只报警。只有通过格式、日期
和自动国家分类的作品才请求正片章节和封面；不请求角色、人物、角色图片或续播刷新。
任何失败或中断的同步都会把资料标记为未验证；只有完整成功的同步才推进数据代次并解除
Pages 发布限制。

## 6. 标题与事实

首选标题：中文名优先，否则原名。

只保存结构化标题、别名和事实。禁止机器翻译、简介推断、补造日期和其他事实。简介只规范空白与换行。缺失字段隐藏，不显示虚假占位。

## 7. 来源标签

内部来源：

```text
manga
light_novel
novel
game
visual_novel
original
other
unknown
```

优先结构化 Infobox，再使用有限、精确的社区标签回退。禁止简介推断。多来源必须有明确证据。`visual_novel` 优先于 `game`，`light_novel` 优先于 `novel`。原创与改编冲突记录警告；无法解释的冲突归 `unknown`。保存证据类型和值。规则变化后可离线重新 build。

## 8. 社区标签

保存全部原始标签。展示链路：

```text
原始标签
→ NFKC + trim
→ allowed-tags.toml exact membership
→ 白名单顺序
```

禁止 alias/synonym mapping、模糊匹配、包含匹配、编辑距离、AI 判断和自动同义词。

第一版白名单：

```text
喜剧 恋爱 奇幻 科幻 冒险 战斗 悬疑 推理 恐怖 惊悚
历史 武侠 运动 音乐 美食 职场 校园 青春 日常 异世界
穿越 机战 百合 BL 后宫 乙女 治愈 热血 萌系
```

不在白名单中的原始标签不展示；需要新增展示词时直接修改白名单并离线重建。

## 9. 黑名单

配置：`config/bangumi.toml`

```toml
[filters]
excluded_subject_ids = []
```

命中项不得进入页面、搜索、抽屉、详情、PWA 或离线清单。同步和构建均防御性处理。清理时先读取受影响季度，再在一个事务中物理删除作品及其级联事实；作品封面文件按该作品的唯一派生路径清理。移除黑名单后需重新 sync 对应季度恢复。

## 10. SQLite

SQLite 是事实来源，HTML 是可重建产物。

核心表：

```text
database_metadata
subjects
subject_titles
subject_infobox
subject_tags
subject_sources
subject_quarters
subject_covers
subject_review_issues
sync_states
```

正式 Schema v2 从空库直接创建，只接受 `TV` 和 `MOVIE`。Subject 保存原始标题、中文标题、
原始简介、日期、单一集数、评分及 Japanese-only 结构化证据；别名、Infobox、候选标签、
标准化来源、季度 appearances、封面元数据和待复核问题分别保存。`subject_quarters` 是季度
appearance 表：TV 最多一个 premiere、可以有多个 continuing，Movie 只有一个 premiere。
同步状态按季度记录 facts 与 covers 是否完整，不保存实体级状态。

数据库以 family/version 元数据识别，未知或更高版本直接拒绝；不维护旧开发 migration chain。
使用纯日期、外键、原子建库和事务失败回滚。短暂异常通常不立即删除历史事实。

## 11. 季度、集数与封面

`subject_quarters` 保存 `(subject_id, year, quarter_month, appearance_kind)`；TV 最多一个
premiere、可以有多个 continuing，Movie 只有一个 premiere。季度来源明确区分
automatic/manual；未确认时允许没有季度行。SQLite 不保存单集记录，也不从任何列表推断总集数，
只保存上游明确提供的单一集数字段。

只登记通过过滤作品的唯一最终封面元数据；相对路径固定由 Subject ID 派生为
`covers/<subject_id>.webp`，不写入 SQLite。第一版不保存、下载、展示或查询角色、声优与角色图片；
local 和 Pages 均无角色区。

## 12. 仓库与 CLI

本地数据：

```text
workspace/data/bangumi-side-b.sqlite3
workspace/covers/
workspace/reports/
workspace/tmp/
workspace/backups/
```

构建唯一写入：

```text
dist/site/
```

`main` 不提交 SQLite、媒体、报告、临时文件、备份或生成站点。Pages 最终发布到 `gh-pages`。

`config/bangumi.toml` 的正式活动配置是 `[filters]` 与 `[sync]`；同步通过
`archive_config.py` 读取这些字段。Build、Frontend 与统一 release workflow 不依赖旧季度范围、
国家默认放行、角色配置或已移除的旧发布链路。

命令严格解耦：

- `sync`：联网同步并在事实成功后触发受影响范围的增量 build；
- `build`：完全离线构建唯一 `dist/site`；
- `serve`：只服务已有 `dist/site`，不读 SQLite、不 build；
- `release prepare`：可离线 build 并绑定当前候选，不 sync、不 push；
- `release publish`：只验证并发布 prepared `dist/site`，不 sync、不 build。

构建按季度与其 archive/year 依赖规划 dirty artifacts，只将脏 artifacts 写入临时 staging，
校验后增量 patch `dist/site`。`workspace/build-state.json` 是可删除的 derived state；缺失或
损坏时允许一次安全的完整收敛。单个 blocked quarter 只能保留自己的 last-good artifacts，
不能冻结其它健康季度；无法恢复时省略并给出 WARNING。

## 13. 页面与浏览

路径：

```text
YYYY-MM/index.html
data/quarters/YYYY-MM.json
archive/index.html
settings/index.html
```

首页进入最新已构建季度；无季度时显示简洁 empty state。

导航按 archive index 显示所有可用历史季度；季度页面默认 TV，并将 Movie 与 continuing 分开。

季度页面使用静态 master list + detail workspace；运行时只读取同源季度/year JSON，不生成
Subject 独立详情页。Archive 支持季度、年度和年份范围浏览；浏览器返回、Hash 直达、筛选、
排序、分页和响应式 detail/filter workspace 均属于当前正式前端契约。

搜索只匹配首选标题、原名、结构化别名和 Bangumi Subject ID；NFKC、trim、拉丁字符大小写
不敏感、子串匹配；不做分词、拼音、模糊匹配或翻译。

同维度 OR，不同维度 AND。

排序只保留评分高低和评分人数多少四种，默认评分高到低。有评分排在无评分前；同分按评分人数，再按首播日期稳定排序。

## 14. 视觉

核心概念：

```text
唱片 B 面 × 日本播出档案 × 现代编辑部动画年鉴
```

只做浅色模式。要求精致排版、清晰层级、独特季度编号/日期/侧标/分区、中高密度、丰富但克制的细节、高质量 hover/focus/press/过渡、响应式、reduced-motion、高对比正文、系统字体栈、四季小面积强调色。

避免 SaaS 仪表盘、AI 模板页、蓝紫渐变、玻璃拟态、发光边框、背景网格、Netflix 仿制、过量圆角/阴影/3D、持续自动动画和过量 emoji。

前端必须单独进行详细设计规划、设计令牌、状态、动效和截图级视觉验收。

## 15. 卡片、抽屉、详情

列表行保留封面、标题、原名、媒体、集数、日期、评分、人数、一个 normalized source label 和
最多两个社区标签；详情展示全部命中白名单的社区标签。季度与 Archive 使用同一状态/筛选/
排序引擎，手机和桌面均保持可读密度。

桌面 detail workspace 位于主列表右侧。小于 768px 时，未选择作品的 scope 模式使用全宽列表和
紧凑控件；选择作品或进入 Filter 后保留窄 context rail，并在右侧展示 detail/filter workspace，
不使用底部面板、全屏 overlay 或单列详情替代 master-detail。详情不显示章节、角色、声优、
STAFF 或角色图片。

详情展示封面、首播/当前季度、日期、集数、评分、来源、标签、别名和简介等已验证事实；缺失
字段省略，不补造占位。continuing 详情保留 premiere quarter 证据。季度页的 `#bgm-ID`
始终在当前季度 scope 内定位该 appearance，不自动跳转到 premiere quarter。Archive hash
在当前 scope 内优先定位 premiere appearance；若 scope 不含 premiere，才定位合适的
continuing appearance。

## 16. PWA、版本与发布

GitHub Pages 本身是可联网直接浏览的正式静态站点。PWA precache minimal app shell，并用
runtime cache 缓存访问过的页面、数据和封面；默认不下载全部历史，也不要求完整离线初始化后
才能进入。

用户可以主动下载单个 quarter；Settings 可按 current/year/range/all 排队，但 quarter 始终是完整
离线单位，并以 `data/offline/YYYY-MM.json` 的逐文件 hash/size、active/staging 状态与
content-addressed verified cache 做差分和已完成资源复用。下载只在页面保持打开时进行，不使用
Background Fetch，也不承诺关闭应用后的后台下载。支持 pause、continue、cancel；重新打开后按
manifest diff resume，网络恢复后继续，重试间隔为 1s、3s、10s。失败保留已完成内容并标记
INCOMPLETE；remove quarter 与 cancel 是不同操作。storage estimate 与 persist 只陈述浏览器真实
返回的结果。

更新只显示轻量、非阻塞提示，由用户主动 refresh；不得意外 reload。不得把全部历史资料库作为
单一 monolithic snapshot 产品。

程序版本使用语义化版本；资料版本使用 `YYYY.MM.DD.N`。

系统变更记录在 `CHANGELOG.md`。每次 publication 的版本、source commit、候选 identity 与远端
commit 写入本地 workspace report 和 `gh-pages` commit message；正式运行时树不额外生成资料日志。

第一版本地手动发布，不使用 GitHub Actions。`bgmb release prepare` 离线收敛并绑定候选；
`bgmb release publish` 只允许官方 origin，验证 prepared state 后普通 push `gh-pages`，并确认远端
HEAD 精确等于本次 release commit。无变化拒绝空版本；禁止 force push 和自动重试。确认前失败不
覆盖已有站点，确认后的本地 housekeeping 问题作为 warning 报告。第一版不提供 rollback。

禁止发布 SQLite、workspace、报告、临时文件、备份、Token、本地路径、用户名、完整堆栈和角色图片。

## 17. 测试与协作

测试精简、风险优先：季度 projection、TV/Movie/continuing、结构化国家 token、标签/来源、少量真实 Fixture、
SQLite upsert/黑名单引用、静态构建/链接、增量 rollback、localhost Pages 子路径、PWA 在线浏览/
quarter 离线下载/断网续传/失败保留已完成内容、发布边界。

不追求覆盖率数字，不做大规模浏览器矩阵和低价值 UI 自动化。

开发默认使用 `frugal-dev-runner`。每个 Phase 独立 commit，不在 Phase 边界 push；整份 Plan
通过 integrated validation 后，由 Codex 普通 push 当前分支 upstream。禁止 force push、改写
历史或把源码 push 当作 Pages publish。
