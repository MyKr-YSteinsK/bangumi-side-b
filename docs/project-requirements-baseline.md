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

日本 TV 判定完全自动化，不要求逐条人工审核。国家/地区分类按以下确定性顺序执行：

1. 已验证的 Infobox 国家/地区 key 优先；一致的精确 token 包含 `日本` 或 `Japan` 时
   收录，明确的非日本值排除；
2. 缺失、无法解析或结构化字段冲突时，使用配置中的精确社区标签；正向标签收录，负向
   标签排除，正负冲突排除；
3. 没有地区证据时，仅 `type == 2`、TV、首播日期完整落在当前季度的候选按默认规则收录；
   其余候选排除。

同一结构化值中的合拍国家允许。标签和 token 均只做 NFKC、trim 和精确匹配；绝不以标题、
简介、语言、公司或模糊标签推断国家。同步审计保存使用的证据与默认原因。

## 4. 季度

合法季度月：`1 / 4 / 7 / 10`。

作品永久归属于完整结构化首播日期所在季度；跨季度 TV 只在有证据时增加 continuing
appearance，Movie 不得 continuing。缺完整日期或范围证据时不猜测，不进入新的公开季度并写入审计。

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

`sync` 只联网同步，不 build、不 publish。

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
→ 精确别名映射
→ 白名单过滤
→ 白名单顺序
```

禁止模糊匹配、包含匹配、编辑距离、AI 判断和自动同义词。

第一版白名单：

```text
喜剧 恋爱 奇幻 科幻 冒险 战斗 悬疑 推理 恐怖 惊悚
历史 武侠 运动 音乐 美食 职场 校园 青春 日常 异世界
穿越 机战 百合 BL 后宫 乙女 治愈 热血 萌系
```

明确别名：

```text
搞笑→喜剧
喜剧动画→喜剧
日常系→日常
治愈系→治愈
热血系→热血
百合向→百合
耽美→BL
BL向→BL
乙女向→乙女
```

首次同步后生成标签审计报告，由人工决定是否扩充。

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

正式 Schema v1 从空库直接创建，只接受 `TV` 和 `MOVIE`。Subject 保存原始标题、中文标题、
原始简介、日期、单一集数、评分及 Japanese-only 结构化证据；别名、Infobox、候选标签、
标准化来源、唯一归档季度、封面元数据和待复核问题分别保存。同步状态按季度记录 facts 与
covers 是否完整，不保存实体级状态。

数据库以 family/version 元数据识别，未知或更高版本直接拒绝；不维护旧开发 migration chain。
使用纯日期、外键、原子建库和事务失败回滚。短暂异常通常不立即删除历史事实。

## 11. 季度、集数与封面

一个 Subject 最多属于一个归档季度，季度来源明确区分 automatic/manual；未确认时允许没有
季度行。SQLite 不保存单集记录，也不从任何列表推断总集数，只保存上游明确提供的单一集数字段。

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

命令严格解耦：

- `sync`：联网同步并在事实成功后触发受影响范围的增量 build；
- `build`：完全离线构建唯一 `dist/site`；
- `serve`：只服务已有 `dist/site`，不读 SQLite、不 build；
- `publish`：验证并发布已有准备好的站点，不调用 sync/build。

构建只将脏 artifacts 写入临时 staging，校验后增量 patch `dist/site`；失败回滚受影响文件，
不破坏上一版。

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

卡片打开由同源季度 JSON 驱动的抽屉；不生成 Subject 独立详情页。浏览器返回与复杂交互属于后续前端阶段。

搜索只匹配首选标题、原名、结构化别名；NFKC、trim、拉丁字符大小写不敏感、子串匹配；不做分词、拼音、模糊匹配或翻译。

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

卡片：2:3 封面 + 独立信息区；标题、原名、TV、集数、日期、评分、人数、最多两个来源标签、最多两个社区标签；手机 2 列，桌面约 5～7 列。

桌面抽屉右侧约 520～600px；手机为底部高面板。抽屉不显示章节、角色、声优、STAFF。

详情桌面为封面与资料双栏，手机上下布局；章节为紧凑列表，超过 50 集时初始前 24 集并可展开；不显示角色或声优区。返回进入时季度，直接打开时返回永久归属季度。

## 16. PWA、版本与发布

PWA 使用完整离线快照，不提供在线浏览模式。首次完整初始化并校验后才可进入；支持暂停、继续、失败重试、关闭续传、取消 staging。正常启动只读本地快照，不自动联网。

用户主动检查更新时请求小型 `release.json`。新快照下载到 staging，校验成功后原子切换并删除旧应用缓存；失败保留旧版。允许跨版本直接升级；第一版不做差分和降级。

程序版本使用语义化版本；资料版本使用 `YYYY.MM.DD.N`。

系统日志：`CHANGELOG.md`。资料日志每次发布自动生成。

第一版本地手动发布，不使用 GitHub Actions。`publish` 使用 staging，完整验证后推送 `gh-pages`。无变化拒绝空版本。发布失败不影响旧站点、旧版本或旧 PWA 快照。第一版不提供 rollback。

禁止发布 SQLite、workspace、报告、临时文件、备份、Token、本地路径、用户名、完整堆栈和角色图片。

## 17. 测试与协作

测试精简、风险优先：季度 projection、TV/Movie/continuing、结构化国家 token、标签/来源、少量真实 Fixture、
SQLite upsert/黑名单引用、静态构建/链接、增量 rollback、localhost Pages 子路径、PWA 初始化/断网/
失败保旧版、发布边界。

不追求覆盖率数字，不做大规模浏览器矩阵和低价值 UI 自动化。

开发默认使用 `frugal-dev-runner`。目标约 6 份正式 Plan，每份约 5～7 个高内聚 Phase。每个 Phase 独立 commit，不自动 push。用户 push 后由 ChatGPT 联网检查仓库，再生成下一份 Plan。
