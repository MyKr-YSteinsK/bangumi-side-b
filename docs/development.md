# 开发与验收

本文只记录仓库特有的工程语义。通用的任务规划、提交阶段、验证分级
和协作流程由 `frugal-dev-runner` 等活动 Skills/runtime 负责；仓库级安全
边界见根目录 `AGENTS.md`。

有效任务／Plan 的权限、冻结约束与必要验收仍是执行依据；Skill、上传资料
或研究建议不会自行扩大权限或替代现行合同。研究测量、浏览器自动化、线上
观察和真机验收应分别标明基线、环境与证据类型；未执行的验证不能报告通过，
已批准的必要验收不能改写为可选 USER CHECK。

## 浏览器测试依赖

完整浏览器/PWA 测试还需要在当前电脑安装 Playwright 浏览器；Windows 使用：

~~~powershell
python -m playwright install chromium webkit
~~~

Linux CI 还会按工作流需要使用 --with-deps 安装系统依赖。只运行不带
browser 标记的单元和集成测试时，不需要安装浏览器。

## 环境与版本

项目需要 Python 3.11 或更高版本。程序版本直接来自
`src/bgm_side_b/_version.py`，`pyproject.toml` 使用同一来源；普通文档或
测试维护不应为了迁移或发布动作机械增加版本。

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

应用 SemVer 与 Pages 的 `YYYY.MM.DD.N` 批次身份分离。具体版本历史由
`CHANGELOG.md` 维护，发布身份和候选绑定规则由[版本说明](releases.md)
与[发布说明](publish.md)维护。

## Git 提交身份与追溯

沿用当前源码分支与独立 `gh-pages` 发布分支，不增加分支或历史整理要求。
以下身份描述提交内容和用途，不要求每类变更必须拆成独立提交：

| 提交内容 | 身份与追溯 |
| --- | --- |
| 源码实现、测试与工程文档 | 用实际源码分支 commit SHA、diff 和任务回传追溯；应用版本仅按现行版本合同变化，文档提交不机械增加 SemVer。 |
| 受控数据／配置更新 | 受支持的 tracked 配置仍在源码分支，用实际 commit SHA、配置 diff 及对应授权／裁决或确定性运行证据追溯；历史 `data:` 提交是现有用法，前缀本身不证明内容或授权。SQLite 事实／封面事务提交不等于 Git 提交。 |
| `gh-pages` 发布 | 用实际远端 release commit SHA、`release: YYYY.MM.DD.N [source <12位 commit>]` 与 prepared candidate identity 追溯；`source` 指准备时的整个源码 HEAD，可能包含实现、配置和文档提交，不等于最近一个实现提交。详细绑定与远端确认规则以[版本说明](releases.md)为准。 |

源码 HEAD 可追溯 tracked 输入；SQLite、封面不进 Git，因此单凭 source SHA
不能复现完整数据或证明站点内容。候选内容由实际 `dist/site` 哈希绑定，私有
运行／发布证据保留在本地 workspace，不为追溯而提交数据库、媒体或报告。
Git author／committer 也不代替任务授权、验收或发布归因；沿用已有身份配置。

正式 Plan 的提交、一次 ordinary push、upstream HEAD 核实及失败保留本地提交
的要求以 `AGENTS.md` 为准。推送前确认 configured upstream 与其部署副作用；
源码 push、受控配置持久化、Pages publication 分别报告，不相互代替。

## 命令副作用

- `bgmb build` 只读取 SQLite、已校验封面、配置和静态源文件，完全离线，
  只生成 `dist/site` 及可重建的本地派生状态。
- `bgmb serve` 只服务已经存在的 `dist/site`，不读取 SQLite、不构建、不
  同步，也不发布。
- `bgmb sync` 是唯一的常规 Bangumi 网络入口；事实和封面成功提交后才
  触发受影响范围的增量 build。未获计划或用户明确授权时不得执行 live
  sync；未知 Subject 的远程 `assign` 导入同样受此边界约束。同步报告以
  monotonic 时钟追加分阶段诊断耗时；计时只作观测，不参与事实判断或处理顺序。
  scope 时间排除报告序列化和 CLI 后续自动 build，阶段状态与摘要口径见[用户指南](USER_GUIDE.md)。
- `bgmb release prepare` 执行本地资料审计和离线 build，可更新正式 `dist/site`
  与 build state、报告等派生状态；随后在临时 staging 验证候选并做 dry-run，
  写入 prepared state。它会只读访问 Git 远端 `gh-pages`，整个 prepare 并非
  完全离线；它不访问 Bangumi、不修改源码分支、不 sync、不 push、不发布
  Pages。`bgmb release publish` 只发布仍与 source/content/remote identity
  绑定一致的 prepared `dist/site`，不调用 sync/build。阶段边界见[发布说明](publish.md)。

日常状态检查使用 `bgmb status` 或 `bgmb doctor --local`。不带 `--local`
的 `doctor` 只用于明确需要的远端 Git 状态检查，不代表 Bangumi 访问授权。

## 事实与运行时边界

Browse/Search 只负责候选发现；最终 subject facts 必须来自 canonical detail
和确定性规则。国家、季度、媒体、来源、标签、黑名单和生成事实不由 AI
推断。证据不足不是单一 disposition：特定 country evidence 的缺失按专项
合同处理并可能进入 REVIEW；当前 rule-bound `information-insufficient`
issue family 可触发自动排除缓存。自动排除在相关季度再次同步时会以新
canonical evidence 重新评估；`factual conflict`
继续保留为 REVIEW 并交由显式人工裁决；manual exclusion 与 automatic
exclusion cache 是不同状态。

季度归属使用自然日历季度。Movie 只接受其 canonical premiere date 所在的
自然季度；TV 只有在 canonical premiere date 落在目标季度开始前 1～7 天时
才进入边界规则：计划总集数为 1 或 2 的作品仍归自然季度，长篇 TV 必须同时
有连续多周、固定间隔的主线播出日期和明确的目标季度证据，且不能存在结构化
冲突。标题中的季节词不是证据。`TV_QUARTER_BOUNDARY` 仅表示待人工裁决的
季度 REVIEW，不属于 cold-cleanup allowlist，也不能单独触发自动黑名单。
候选在 canonical 媒体与日期可用后先判定目标季度相关性，再进入日本性判定；
Search 的前一季度回看只服务 TV 边界，Movie 的非目标季度候选直接忽略。
未决或冲突日本性若已被可靠日期与评分人数确定性地支配为排除，会记录
`outcome_dominated_low_rating` 而不伪造事实结论；仍可能改变收录的日本性
问题只能通过独立的 `config/japanese-overrides.toml` 与
`bgmb classify` 人工裁决。
`config/bangumi.toml` 的 `[filters].low_rating_exempt_subject_ids` 仅记录
经过明确批准的低评分规则豁免；当前只允许 subject `501729`。它只影响
`low_rating_count` 和 `outcome_dominated_low_rating` 两条判断，不影响
`information-insufficient` 自动排除、REVIEW 或其它准入门槛，也不代表无条件收录。
canonical `platform=剧场版` 仍需通过非院线特殊场所门：当前只识别 Infobox
`其他` 中两个已核验的精确值，不读取标题或 URL。旧自动排除在 fresh canonical
结果变为非日本或非院线硬拒绝时，会事务性删除并记录 `auto_reconciled`，不会继续
计入自动黑名单。

运行时使用静态 HTML、CSS 和原生 JavaScript，只请求同源生成的 JSON、页面
和资源；不读取 SQLite，不请求 Bangumi 或第三方业务 API。SQLite 使用严格
的 `bangumi-side-b-archive` / schema 2 合同，未知或更高版本直接失败。

## 前端工程约束

Quarter 与 Archive 共享浏览状态引擎。767px 及以下详情和筛选不保留
context rail，使用全宽单栏工作区；手机筛选为 draft/apply，关闭或返回会
取消未应用草稿。Quarter 移动端连续浏览，Archive 保持分页。

站点保持原生 HTML/CSS/JavaScript，不引入前端框架或远程字体。浏览连续性
使用局部结果动效和 reduced-motion 处理，不把 root 或跨文档 View Transition
作为默认基础。详细视觉、PWA、构建和发布合同分别由所有权登记中的专项文档
维护。

## CI 与测试边界

默认 CI 只使用 fixtures、本地代码和测试环境；不访问真实 Bangumi 数据，
不读取开发者本地 workspace，不 push，也不发布 Pages。默认与手工深度覆盖
的具体工作流分别以 `.github/workflows/ci.yml` 和
`.github/workflows/deep-regression.yml` 为准。
