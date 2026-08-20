# Bangumi Side B 用户指南

本指南面向项目所有者和日常使用者。所有命令均在仓库根目录运行。

## 安装与环境

Bangumi Side B 需要 Python 3.11 或更高版本。安装当前源码并确认 CLI：

```powershell
python -m pip install -e .
bgmb --version
bgmb --help
```

日常使用也可以用 pipx editable 安装：`pipx install --editable .`。这样直接使用 `bgmb`，
无需手动激活 `.venv`；项目命令仍应在项目根目录或其子目录执行。

本地事实、封面、报告和临时状态位于 `workspace/`，不会提交到 Git。唯一生成站点是
`dist/site/`。

## 日常数据同步

同步单个季度：

```powershell
bgmb sync 2026 7
```

同步一段季度范围，或显式刷新范围内已完成的季度：

```powershell
bgmb sync --from 2026 4 --to 2026 7
bgmb sync --from 2026 4 --to 2026 7 --refresh-existing
```

`sync` 会联网访问 Bangumi，获取可验证的事实和封面。事实成功提交后，它会自动触发受影响
范围的增量 build。局部失败或中断不会把未验证资料标记为完整。

同步在确认 Anime、日本、TV/MOVIE 基础范围后，还会应用两条自动永久冷门规则：可靠首播超过 7 天且
评分人数少于 30；或明确 allowlist 中的信息不足型 REVIEW 立即自动排除，与目标季度成熟度和评分人数无关。
第二条规则只处理信息不足的 allowlist issue；冲突型 REVIEW 不会自动排除。
命中作品会写入 `config/bangumi.toml` 的
`auto_excluded_subject_ids`，同时记录标题注释和审计证据，并在本次同步中跳过季度归属、REVIEW、
封面和站点输出。评分人数后来上涨也不会自动恢复。

自动黑名单是永久状态。只有人工从 `auto_excluded_subject_ids` 删除对应 ID 后，作品才有机会在
后续 sync 中重新评估。人工 `excluded_subject_ids` 与自动列表来源不同，均应保留配置中的现有注释。
同步报告会分别显示人工命中、历史自动命中和本次新增自动拉黑数量，并在 `new_auto_by_reason` 中
区分可靠首播低评分和信息不足型未决冷门两类原因。

Browse/Search 只发现候选，正式 subject facts 会再通过 canonical detail 验证。`episode_count`
只表示作品计划正片总话数，优先使用 canonical detail 的 `eps`；Bangumi 的 `total_episodes`
只是当前数据库 episode row 数量，不等同于计划总话数。`0`、缺失、负数和无效文本都显示为未知，
只有精确 Infobox `话数` 的纯数字值或完整连续主线 registry 可以作为严格 fallback，不会从简介、
季度长度或已播章节估算。

同步报告中的来源和集数是 bounded aggregate。`source_counts` 统计已持久化季度事实，
`episode_count` 区分 known、unknown 与 legacy zero，`canonical_detail_requests` 记录本次正式
detail 请求数；不会把原始 API 响应写入报告。来源顺序是精确 Infobox、已验证 exact source tag，
再到能够明确区分类型的结构化 relation；当前 relations 不能区分小说与轻小说，因此保持来源未知。

## REVIEW 与人工裁决

查看全部或某季度尚未解决的 REVIEW：

```powershell
bgmb review
bgmb review 2026 4
```

REVIEW 表示确定性规则无法确认季度归属等事实，不会由 AI 自动判断。由使用者查证后作出明确
决定：

```powershell
bgmb assign BGM_ID 2026 4
bgmb assign BGM_ID --unassigned
bgmb assign BGM_ID --clear
```

`YEAR MONTH` 明确指定首播季度；`--unassigned` 明确保持未分配；`--clear` 移除已有人工决定并
恢复自动规则处理。对尚未在 SQLite 中的 BGM ID 执行 assign 可能联网导入该作品。

同步输出中的 `persisted REVIEW` 只指已经写入 SQLite 且带当前季度作用域的 REVIEW 行，
因此它与 `bgmb review YEAR QUARTER_MONTH` 和 `bgmb audit` 的待裁决季度保持一致。无季度作用域的
证据问题和 Search-only finding 会以独立的当前运行统计显示，不会伪装成季度队列。

## 离线 build

构建一个季度或全部受管季度：

```powershell
bgmb build 2026 7
bgmb build --all
```

`build` 完全离线，只读取 SQLite、已验证封面、配置和静态源文件，唯一输出为 `dist/site/`。
缺少事实、存在相关 REVIEW 或季度状态不完整时，该季度可能保留自己的 last-known-good 输出，
或在没有可用旧输出时被省略并报告 warning；系统不会补造资料。

## 本地浏览

```powershell
bgmb serve
bgmb serve --port 8000
bgmb serve --open
```

默认端口是 8000。服务地址为
`http://127.0.0.1:8000/bangumi-side-b/`，使用与 GitHub Pages 相同的项目子路径。
`serve` 成功绑定后会打印完整 URL 和 `Press Ctrl+C to stop.`，默认不会自动打开浏览器；只有
明确使用 `--open` 时才会调用系统默认浏览器。浏览器启动失败只显示 warning，服务仍继续。
`serve` 只提供已有 `dist/site`，不读 SQLite、不 build、不同步也不发布。

Settings 页面包含直接构建进 `settings/index.html` 的 05 / CHANGELOG：显示当前程序版本、
尚未发布内容和当前版本 release；历史 release 默认折叠。它离线可读，不会运行时请求 GitHub
或读取外部 CHANGELOG 文件。

## 网站浏览

- 首页进入最新已构建季度。
- 季度页默认显示 TV，并分别呈现 TV 首播、TV continuing 与 Movie 首播。
- 搜索匹配标题、原名、结构化别名和 Bangumi Subject ID；支持来源与社区标签筛选。
- 筛选工作区会显示按当前搜索和其它条件计算的结果数；选项以可换行 chip 展示，已选条件可在活动筛选区单独移除，也可以清除全部。
- 排序提供评分和评分人数的升降序，结果可分页浏览。
- Archive 可按季度、年份或年份范围查看已生成资料。
- 桌面端在列表右侧打开 detail / filter workspace；手机端选择作品或筛选时切换到不保留 context rail 的全宽单栏工作区，使用“返回结果”回到列表。
- URL 中的 `#bgm-ID` 可直达当前浏览范围内的作品详情，并支持浏览器前进/后退。

页面不提供章节、角色、声优、收藏、账户或网页编辑功能。

## PWA 与手机离线

网站在线时可直接使用；支持的浏览器可从 Settings 或浏览器菜单安装到主屏幕。PWA 默认只缓存
最小应用 shell 和访问过的资源，不会先下载整个 Archive。

Settings 可以下载单个季度，也可把 current、year、range 或 all 展开为季度队列。季度始终是
完整离线单位：

- 支持 pause、continue 和 cancel；cancel 不等同于删除已保存季度。
- 下载失败会保留已验证内容并显示 `INCOMPLETE`，可稍后继续。
- 已下载季度有新内容时显示 `UPDATE_AVAILABLE`；更新中断且旧版仍可用时显示
  `UPDATE_INCOMPLETE`。
- 可在 Settings 删除季度离线数据，并查看浏览器实际返回的 storage estimate、持久存储状态或
  请求持久存储。
- 应用更新只显示非阻塞提示，由使用者主动刷新，不会突然 reload。

下载仅保证在页面保持打开时运行；关闭页面或操作系统终止应用后，不承诺后台继续下载。未完成
下载的季度在断网时不保证可用。

## 状态检查

```powershell
bgmb status
bgmb doctor --local
bgmb doctor
bgmb audit
```

- `status` 快速读取本地 SQLite、站点和 prepared state，不联网。
- `doctor --local` 执行更完整的本地环境与资料检查，不联网。
- `doctor` 会读取 `origin/main` 与远端 `gh-pages` 状态，但不访问 Bangumi API。
- `audit` 只读检查当前 SQLite 是否具备可发布的完整季度以及是否存在 REVIEW 阻塞；输出中的
  `数据库总作品` 是全库 unique subject 数，不是某个季度的条目数。每个可发布季度还会显示
  `TV首播`、`TV续播`、`剧场版` 与 `合计` appearance 组成，同一作品跨季度出现时会分别计入条目。

## 正式发布

```powershell
bgmb release prepare
git push origin main
bgmb release publish
```

`release prepare` 要求 main、干净工作树和当前 SQLite；它会执行资料审计、离线收敛 build、校验
`dist/site` 并做发布 dry-run，但不 sync、不 push。准备结果绑定 source commit、候选文件树和
准备时的远端 `gh-pages`。

先把同一 source commit 普通 push 到 `origin/main`，再运行 `release publish`。publish 不 build、
不 sync，只接受官方项目 origin；prepared state、HEAD、候选树、`origin/main` 或远端
`gh-pages` 任一变化都会拒绝发布。成功路径只做普通 `gh-pages` push，并精确确认远端 HEAD 是
本次 release commit；不 force、不自动重试。相同文件树不会创建只改变 metadata 的空版本。

## 常见问题

### 为什么某季度没有生成？

该季度可能尚未同步完整、封面或事实状态未完成、没有可发布 appearance，或被相关 REVIEW
阻止。运行 `bgmb audit`、`bgmb review YEAR MONTH` 和 `bgmb status` 查看原因。

### REVIEW 怎么办？

先人工查证，再用 `bgmb assign` 明确分配、明确未分配或清除旧决定。不要用标题、简介或 AI 猜测。
自动黑名单不通过 REVIEW 处理；如需重新评估，先人工删除对应自动 ID，再重新执行目标季度同步。

### 为什么 build 不联网？

这是正式边界：sync 获取并提交事实，build 只把本地已验证事实投影成可重建的静态站点。

### 为什么 PWA 不能保证后台下载？

当前实现不使用 Background Fetch。页面关闭或被系统终止后，浏览器可能停止任务；重新打开后可按
已验证进度继续。

### 为什么离线季度显示“更新未完成”？

新 manifest 的 staging 内容尚未全部下载或验证。旧 active 版本仍保留可用；联网后继续更新即可。

### 为什么 release publish 被拒绝？

常见原因是 prepared state 失效、工作树不干净、main 尚未同步到 origin、候选树变化、远端
`gh-pages` 已推进、origin 不是官方仓库，或候选与远端没有可发布变化。按提示重新检查，必要时
重新运行 `bgmb release prepare`。

### 为什么相同 tree 不能再次发布？

资料版本必须对应真实站点变化。系统不会为了推进 `YYYY.MM.DD.N` 而创建 `--allow-empty` 提交。
