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
issue family 可触发 `automatic permanent exclusion`。`factual conflict`
继续保留为 REVIEW 并交由显式人工裁决；manual exclusion 与 automatic
permanent exclusion 是不同状态。

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
