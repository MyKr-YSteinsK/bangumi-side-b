# 开发与验收

## 环境

在项目根目录激活虚拟环境；首次使用或依赖变动后安装开发依赖：

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

程序版本直接来自源码。普通代码修改不需要为了版本读取而重新执行
editable install；安装包元数据只会在 `bgmb doctor` 中作为环境提示显示。

## Version impact 与提交规则

每个 Phase commit 前都要记录一次 `Version impact`：`none`、`patch`、`minor`
或 `major`，并按实际用户可见影响选择，而不是按 Plan 数量或发布动作机械递增。

- `none`：纯测试、内部重构、CI 或不影响用户行为的文档维护；不改版本号和 CHANGELOG release。
- `patch`：局部 bugfix、正确性修复、可靠性提升或小型用户可见改善。
- `minor`：完整的新能力或明显改变主要使用方式的产品里程碑。
- `major`：产品定位、核心兼容契约或底层架构发生大范围不兼容变化。

`patch`、`minor`、`major` 都是 version-bearing commit，必须把实际代码变更、
`src/bgm_side_b/_version.py`、对应 concrete `CHANGELOG.md` 条目和必要测试放在同一
commit。纯测试和文档 commit 不升版本。一个 Plan 可以形成多个应用版本，但只在
Plan 完成并通过 integrated validation 后统一普通 push；不会因为准备 publish 再生成
额外版本。

普通开发提交不得顺手发布 Pages。只有完整 Plan 的集成验证、普通分支 push、CI
精确 SHA 校验和 `release prepare` 全部通过后，才能按明确授权执行
`release publish`；报告中的推荐版本必须与源码单一版本号和 Settings 构建结果一致。

## 日常检查

```powershell
bgmb status
bgmb doctor
```

`status` 只读取本地事实且不 fetch remote，输出一个主要下一步。`doctor --local`
同样只读本地；不带 `--local` 的 `doctor` 会读取 `origin/main` 和远端
`gh-pages`，网络失败只影响远端检查项，绝不访问 Bangumi API。

## Live Bangumi sync policy

正常开发、测试、build、audit、`release prepare` 和 `release publish` 的事实源是
已有 SQLite 与 fixtures，默认不访问 Bangumi API：

> **Live Bangumi sync is opt-in, not part of normal development validation.**

`bgmb sync` 默认禁止。只有当前 approved Plan 明确写出
`Live Bangumi sync: AUTHORIZED`，或用户在当前任务明确要求真实数据同步时，才可以
执行 live sync；缺少明确授权时一律视为 `FORBIDDEN`。不得通过包装脚本、测试、build
或 release 命令间接触发。若命令意外触发 live sync，应立即停止并报告 scope violation。

## 分级验证

每份 approved Plan 必须声明 `Validation tier: light / standard / full`；未声明时按
`standard`，绝不默认完整回归。开发期间优先运行本次改动的 focused tests，不为小修改
重复执行昂贵套件。

| 改动类型 | Tier | 本地测试 | build/audit | full pytest |
|---|---|---|---|---|
| 文案、CSS、小 UI | light | focused test / smoke | 通常否 | 禁止 |
| tests、docs、CI-only | light | changed test only | 禁止 | 禁止 |
| Archive/Quarter 普通逻辑 | standard | related suite | 必要时一次 | 默认否 |
| PWA 一般功能 | standard | targeted PWA tests | 必要时一次 | 默认否 |
| schema、sync、release、PWA 核心队列 | full | focused + full once | 一次 | 一次 |

light 的本地验证目标不超过五分钟；超过八分钟必须重新评估。standard 的目标不超过
十五分钟。full-tier Plan 最多正常执行一次完整本地验证；之后若只追加 tests/docs/CI
修复，不重新跑 full pytest、build 或 audit。

`build` 只读 SQLite、配置、静态源文件和已校验封面，写入 `dist/site`；第二次相同
构建应无 artifact 写入。`serve` 只服务已有 `dist/site`，不读 SQLite、不构建、不同步
也不发布。成功 bind 后 CLI 打印 loopback URL 和 Ctrl+C 退出提示；默认不打开浏览器，
只有 `--open` 才请求系统默认浏览器，启动失败仅报告 warning。`release prepare` 仅属于
真正产品发布的后续生命周期，tests/docs/CI-only 改动禁止执行。

Settings 的 06 / CHANGELOG 是 build-time 静态 HTML：builder 读取仓库 `CHANGELOG.md` 与
源码单一版本号，严格 escape 文本后写入 `settings/index.html`；运行时不 fetch changelog。
Settings 的 milestone `x.y` 日期严格来自对应的 concrete `x.y.0` release 日期，不从 child patch、
Pages 发布批次或构建时间推导；`0.6.1` 及之后的 version-bearing CHANGELOG 条目必须有明确的
`YYYY-MM-DD` 日期，历史 anchor 也必须能从仓库证据恢复，否则 build 直接报告错误。
`bgmb audit` 的 `数据库总作品` 是全库 unique subject 数，季度条目统计按
TV premiere / TV continuing / Movie premiere appearance 分解。

站点控件保持原生 HTML / CSS / JavaScript：builder 输出选择器根节点，`static/js/app.js`
提供 Quarter / Archive 共用的 `window.BsbListbox`，`static/js/pwa.js` 复用它渲染
Settings 队列筛选。用户可见选择器不使用原生 `<select>` 展开 UI；键盘、触屏和
Escape / 外部点击关闭由该基础层统一处理，业务选择值和 localStorage 契约不变。

Quarter / Archive 浏览工作区遵循同一套响应式契约：桌面 detail 和 filter 占满可用
viewport 并在面板内部滚动；767px 及以下隐藏 master context rail，详情和筛选使用
单栏全宽工作区。筛选面板展示上下文计数、可换行 chip、活动筛选、清除全部和当前
结果数；返回结果会保留搜索、筛选、排序、分页和可恢复的列表滚动位置。响应式验收
至少覆盖 1199、1024、900、768、767、390 和 360 宽度，并检查无横向溢出。

移动主菜单优先使用 native Popover top layer；不支持时使用 fixed fallback，并以明确的 nav
overlay layer 高于普通 control/sheet、低于 detail。排序、季度和筛选浮层与主菜单互斥。
浏览结果动效只作用于可见作品节点，不启用 root 或跨文档 View Transition；所有操作都必须在
reduced-motion 与快速重复操作下保持最终状态确定。

同步还会在基础 Anime、日本、TV/MOVIE 范围确认后检查两条自动冷门规则：A）可靠首播日期超过 7 天且
评分人数少于 30；B）明确 allowlist 中的信息不足型 REVIEW 立即自动排除，与季度成熟度和评分人数无关。
冲突型 REVIEW 仍保留 REVIEW。命中项会永久写入
`auto_excluded_subject_ids`，并在同一次同步中停止季度归属、REVIEW 和封面处理。评分人数后来上涨不会
自动恢复；需要人工删除配置中的自动 ID 后再重新 sync。
获授权的真实数据裁决流程（不属于普通开发验证）仍是：

```powershell
cd <repository-root>
bgmb sync YEAR QUARTER_MONTH
bgmb review YEAR QUARTER_MONTH
```

同步报告的黑名单汇总分别记录人工命中、历史自动命中和本次新增自动拉黑，并用
`new_auto_by_reason` 区分 `low_rating_count` 与 `insufficient_airing_information`；三者之和必须等于
黑名单总命中数。

Browse/Search 返回的字段只用于候选发现；最终标题、日期、集数、Infobox、tags、来源和图片事实
必须来自 canonical subject detail。同步报告还提供 bounded `source_counts`、`episode_count`
（known/unknown/legacy_zero_written）和 `canonical_detail_requests` 聚合，不保存原始 API 响应。
`episode_count` 只表示作品计划正片总话数：`eps` 是首要 subject-level 事实；Bangumi 的
`total_episodes` 只是当前数据库 episode row 数量，不参与计划总话数判定。集数 `0` 不表示零集：
只持久化正整数，严格的 Infobox `话数` 或完整连续主线 registry fallback 不可得时保持 unknown。

同步 summary 与 `bgmb review YEAR QUARTER_MONTH` 使用同一 scoped persisted REVIEW 定义。
只有实际写入 `subject_review_issues` 且带目标季度的行才称为 `persisted REVIEW`；无季度作用域的
当前运行 finding 和 Search-only finding 会单独计数，不会被误报为该季度队列。

整份 Plan 的集成验证通过后，按仓库规则由 Codex 执行一次普通分支 push；它不是 Pages
发布。真实发布仍需明确执行：

```powershell
bgmb release prepare
git push origin main
bgmb release publish
```

真实发布会重新确认 prepared state、`HEAD == origin/main`、候选内容和远端 `gh-pages`。
任一事实改变后都必须重新运行 `bgmb release prepare`。

## CI

GitHub Actions 只运行测试和 Ruff：Linux 使用 Chromium 执行 synthetic PWA
回归；Windows 运行非浏览器测试以覆盖输出 promotion、路径安全、CLI 和
release state。CI 不会访问真实 Bangumi 数据、读取本地 workspace、push、
publish 或使用 secrets。
