# 开发与验收

本文只记录仓库特有的工程语义。通用的任务规划、提交阶段、验证分级
和协作流程由 `frugal-dev-runner` 等活动 Skills/runtime 负责；仓库级安全
边界见根目录 `AGENTS.md`。

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

## 命令副作用

- `bgmb build` 只读取 SQLite、已校验封面、配置和静态源文件，完全离线，
  只生成 `dist/site` 及可重建的本地派生状态。
- `bgmb serve` 只服务已经存在的 `dist/site`，不读取 SQLite、不构建、不
  同步，也不发布。
- `bgmb sync` 是唯一的常规 Bangumi 网络入口；事实和封面成功提交后才
  触发受影响范围的增量 build。未获计划或用户明确授权时不得执行 live
  sync；未知 Subject 的远程 `assign` 导入同样受此边界约束。
- `bgmb release prepare` 只做离线候选收敛和验证，不 sync、不 push；
  `bgmb release publish` 只发布已验证的 prepared `dist/site`，不调用
  sync/build。

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
