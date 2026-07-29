# Bangumi Side B｜Codex 开发 Plan 01

## 主题

**项目基础、确定性规则、SQLite 与作品主体同步**

## 里程碑

从空仓库建立完整 Python 项目基础，并完成：

```text
CLI 季度范围
→ Bangumi 候选发现
→ TV / 剧场版筛选
→ 黑名单
→ 作品详情
→ 标题、日期、评分、Infobox、原始标签规范化
→ 确定性来源标签
→ SQLite
→ 同步状态
→ 同步报告与标签审计报告
```

完成后支持：

```powershell
bgmb sync 2022 1
bgmb sync 2022
bgmb sync 2022-2023
```

本 Plan 共 **6 个 Phase**。每个 Phase 完成后独立 commit。整份 Plan 完成后不得 push。

## 执行前

1. 确认目录为 `D:\CS\bangumi-side-b`。
2. 确认 `origin` 为 `https://github.com/MyKr-YSteinsK/bangumi-side-b.git`。
3. 确认当前分支为 `main`。
4. 检查工作树。
5. 加载 `frugal-dev-runner`。
6. 阅读本 Plan、`AGENTS.md`、`docs/project-requirements-baseline.md`。
7. 普通技术细节自行决定，不等待用户确认。

仅在需求冲突、可能破坏用户数据、仓库身份错误或环境无法继续时停止。

## 本 Plan 不实现

- 正片章节；
- 角色、声优、配音关系；
- 封面和角色图片；
- `--force-images`；
- HTML/CSS/JS；
- 正式 `build`；
- PWA；
- `publish`；
- `gh-pages`；
- GitHub Actions；
- MyKr-ops 集成。

不得为这些功能创建空壳服务或假接口。

---

# Phase 1｜仓库基础与长期约束

## 目标

建立可安装、可测试、可持续开发的 Python 项目，并固化需求基线。

## 实现

创建：

```text
AGENTS.md
README.md
LICENSE
CHANGELOG.md
.gitignore
pyproject.toml
docs/project-requirements-baseline.md
src/bgm_side_b/
tests/
config/
templates/
static/
```

使用交付包中的 `AGENTS.md` 和需求基线。

项目要求：

- Python `>=3.11`；
- `src` 布局；
- console script：`bgmb`；
- 包版本单一来源；
- 运行依赖优先仅 `httpx`；
- 开发依赖 `pytest`、`ruff`；
- SQLite 用标准库；
- TOML 用 `tomllib`；
- CLI 优先 `argparse`。

README 只描述真实状态，包含项目定位、第一版范围、架构、当前状态、CLI 规划、Git 边界、第三方内容权利说明。

`.gitignore` 排除 `.venv`、缓存、`.env`、workspace、dist、数据库、报告、备份和日志。

最小命令可运行：

```powershell
python -m bgm_side_b --help
bgmb --help
bgmb --version
```

本 Phase 不实现 sync。

## 验证

```powershell
python -m pip install -e ".[dev]"
python -m bgm_side_b --help
bgmb --help
bgmb --version
python -m pytest
python -m ruff check .
```

## Commit

```text
chore: initialize bangumi side b project
```

---

# Phase 2｜配置与确定性规则

## 目标

建立纯规则层，不依赖网络和数据库。

## 配置

创建：

```text
config/bangumi.toml
config/allowed-tags.toml
config/tag-aliases.toml
config/source-rules.toml
```

`bangumi.toml` 至少包含：

```toml
[filters]
excluded_subject_ids = []

[sync]
api_concurrency = 3
request_timeout_seconds = 20
max_retries = 3
```

白名单和别名严格使用需求基线。`source-rules.toml` 保存规范来源值、固定排序、Infobox 精确规则、标签精确回退和优先级。未通过真实 API 验证的写法不得假装穷尽。

## 领域规则

实现可测试纯函数或小型不可变模型：

- 季度月校验；
- 日期到季度；
- 季度起止日期；
- 年份与年份范围展开；
- 格式规范化；
- 第一版格式允许判断；
- 首选标题；
- 别名 trim、去重、排除主标题；
- NFKC 文本规范化；
- 标签精确别名、白名单、顺序、去重；
- 来源结果与证据；
- Infobox 优先、标签回退、冲突处理；
- UTC 时间格式化。

不得使用 AI、模糊匹配或简介推断。

## 测试

精简覆盖：

- 仅 1/4/7/10 合法；
- 年份范围顺序；
- 日期归属；
- TV/movie 接受，WEB/OVA/other 排除；
- 中文标题优先；
- 标签映射、去重、固定顺序；
- 有损别名不映射；
- 结构化来源优先；
- visual novel/light novel 优先；
- 原创与改编冲突；
- 无证据 unknown；
- 明确多来源。

## 验证

```powershell
python -m pytest tests -q
python -m ruff check .
```

## Commit

```text
feat: add deterministic domain rules and configuration
```

---

# Phase 3｜SQLite schema、迁移与 repository

## 目标

建立完整事实模型基础，使后续章节、角色、声优无需重构核心 schema。

## 数据库

默认路径：

```text
workspace/data/bangumi-side-b.sqlite3
```

运行时创建目录，不提交。

## 迁移

实现：

- `schema_migrations`；
- 编号迁移；
- `PRAGMA foreign_keys = ON`；
- 事务；
- 当前版本检查；
- 未知版本拒绝；
- 既有数据库迁移前备份；
- 失败回滚；
- 备份位于 `workspace/backups/`；
- 最多保留最近 5 份。

## 表

建立：

```text
database_metadata
subjects
subject_titles
subject_infobox_items
subject_raw_tags
subject_sources
subject_quarters
episodes
characters
persons
subject_characters
character_voices
media_files
sync_states
```

本 Plan 只写入作品主体相关表，但后续关系必须正确表达：

- 全局作品、角色、声优；
- 作品—角色；
- 作品—角色—声优；
- 季度出现；
- 多来源；
- 媒体归属；
- 分类型同步状态。

关键要求：

- 原始 Infobox 使用规范 JSON 值保存，不保存整个响应；
- 原始标签保留 count 和顺序；
- 来源保存 evidence type/value；
- 季度关系唯一；
- 同步状态按 entity/data type 唯一；
- 日期纯日期，时间 UTC；
- 作品专属子表可级联；
- 角色、声优不得被 subject 直接错误级联。

## Repository

实现小型、直接的数据访问层：

- 事务上下文；
- subject upsert；
- 标题、Infobox、标签、来源、季度关系的当前快照式替换；
- sync state 读写；
- 黑名单 subject 物理删除；
- 查询存在性与最近状态。

不使用 ORM，不建设通用 repository 框架。

## 测试

覆盖：

- 新库初始化；
- 迁移幂等；
- 外键开启；
- upsert 不重复；
- 评分刷新不覆盖稳定事实；
- 子表替换不重复；
- subject 删除级联作品专属数据；
- 删除失败事务回滚；
- 迁移备份和失败回滚；
- 未来共享实体不会被误删。

## 验证

```powershell
python -m pytest tests -q
python -m ruff check .
```

## Commit

```text
feat: add sqlite schema migrations and repositories
```

---

# Phase 4｜Bangumi API 客户端与候选发现

## 目标

实现可靠匿名 API 层和季度候选发现。

## API 验证

查阅当前官方 Bangumi API，并用少量真实匿名请求验证：

- 动画浏览；
- subject 详情；
- 分页；
- 类型/平台字段；
- 标签；
- Infobox；
- 评分；
- 图片字段。

完整响应不得提交。创建最小匿名化 Fixture：

- TV；
- 剧场版；
- WEB 或 OVA；
- 缺日期；
- 无中文名；
- 无评分；
- Infobox 多值；
- 原始标签。

若文档与实际响应不同，兼容实际响应并记录差异。

## HTTP 客户端

实现：

- `httpx`；
- 项目 User-Agent；
- 匿名访问；
- timeout；
- 并发 3；
- 临时错误最多重试 3 次；
- 退避与抖动；
- 429 Retry-After；
- 非临时错误不盲目重试；
- 明确异常；
- 安全错误摘要；
- 可注入 transport/client 便于测试。

## 候选发现

对季度三个月分别完整分页：

- 合并、按 subject ID 去重；
- 确定性顺序；
- 应用黑名单；
- 尽早排除明确非 TV/movie；
- 候选信息不足时标记详情确认；
- 统计 discovered、duplicate、blacklisted、unsupported、needs_detail、failed。

详情 DTO：

- 未知字段忽略；
- 可选字段缺失降级；
- 必需 ID 缺失失败；
- 标签保持原值；
- Infobox 保持结构和顺序；
- API 层不做来源推断；
- API 层不写数据库。

## 测试

Fixture 与 mock transport 覆盖：

- 三个月分页；
- 去重；
- 黑名单；
- TV/movie；
- WEB/OVA 排除；
- 详情确认；
- 429；
- 5xx；
- 404 不重试；
- 超时；
- 安全错误摘要；
- 未知字段兼容。

## 验证

```powershell
python -m pytest tests -q
python -m ruff check .
```

允许少量真实冒烟，不得提交大响应。

## Commit

```text
feat: add bangumi api client and quarterly discovery
```

---

# Phase 5｜作品主体同步、增量与报告

## 目标

连接 CLI、API、规则和 SQLite，完成可运行的主体同步。

## CLI

实现：

```powershell
bgmb sync YEAR QUARTER_MONTH
bgmb sync YEAR
bgmb sync START-END
bgmb sync ... --force
```

要求：

- 非法季度立即拒绝且不访问网络；
- 年份范围闭区间；
- 年份升序；
- 季度顺序 1/4/7/10；
- 清晰退出码；
- 不 build、不 publish；
- 不实现 `--force-images`。

## 流程

每个季度：

1. 读取配置；
2. 迁移数据库；
3. 清理当前范围内已有黑名单作品；
4. 发现候选；
5. 跳过黑名单和明确不支持形式；
6. 获取需要的详情；
7. 最终只接受 TV/movie；
8. 解析完整首播日期；
9. 计算永久归属季度；
10. 仅首播归属目标季度时建立：
   - TV → `new`
   - movie → `movie`
11. 缺日期或归属不一致时不错误收入；
12. 保存标题、简介、日期、集数、评分；
13. 保存 Infobox 和全部原始标签；
14. 计算来源与证据；
15. 原子写入；
16. 更新 sync state；
17. 局部失败继续；
18. 生成报告。

本 Plan 不计算 `continuing`，不得猜测。

## 增量

- 评分和人数每次刷新；
- `--force` 强制刷新本 Plan 的结构化主体；
- 默认可利用成功状态跳过稳定详情，但不能跳过评分刷新；
- 失败项重试；
- 去重；
- 每条作品事务成功后才标 success；
- 错误记录 code 与简短 summary；
- Ctrl+C 停止新作品，当前事务完成或回滚。

刷新策略保持简单集中，不建设复杂 TTL 框架。

## 报告

同步报告：

```text
workspace/reports/sync-<scope>-<UTC>.json
```

包含 command、version、时间、scope、季度统计、discovered、duplicates、blacklisted、unsupported、details_requested、created、updated、skipped、missing_date、ownership_mismatch、warnings、failed、retries，以及安全的 failure 项。

不保存完整响应、请求头、Token、绝对路径、用户名或完整堆栈。

控制台输出简洁摘要和相对路径。局部失败时退出码非零。

标签审计报告：

```text
workspace/reports/tag-audit-<UTC>.json
```

包含 raw tag、作品数、count 汇总、最多 5 个例子、mapped_to、displayed、白名单状态。不自动修改配置。

## 测试

覆盖：

- 单季度；
- 全年；
- 年份范围；
- 非法季度无网络；
- TV/movie 入库；
- WEB/OVA 不入库；
- 黑名单不请求不入库；
- 缺日期；
- 归属不一致；
- 中文标题；
- 标签/Infobox/来源证据；
- 重复同步；
- 评分刷新；
- 失败重试；
- 局部失败继续；
- 非零退出码；
- 报告安全；
- `--force`；
- Ctrl+C 核心状态。

## 验证

```powershell
python -m pytest tests -q
python -m ruff check .
```

执行较小真实冒烟：

```powershell
bgmb sync 2022 1
```

检查 SQLite、format 仅 tv/movie、报告、重复运行、Git 忽略边界。

网络失败时不得伪称成功。

## Commit

```text
feat: implement subject synchronization pipeline
```

---

# Phase 6｜综合审计与收口

## 目标

审计需求偏差、Git 边界和可维护性，修正问题，形成下一 Plan 可接续的稳定仓库。

## 审计

确认：

- AGENTS 简洁有效；
- 需求基线已存入 docs；
- README 未夸大；
- 仅 TV/movie；
- 未提前实现章节、角色、图片、页面、PWA；
- 无空壳未来服务；
- CLI 范围正确；
- sync 无隐式 build/publish；
- 标签/来源无模糊或 AI；
- 日期不猜测；
- 黑名单使用事务；
- 迁移、备份、外键、回滚正确；
- 报告无敏感内容；
- workspace/dist/database/report 未进入 Git；
- 未修改 MyKr-ops；
- 无无关依赖、重构和格式化。

## 文档

更新 README、CHANGELOG Unreleased，并用简短文档记录：

- 当前已实现 CLI；
- API 已验证字段与差异；
- 当前刷新策略；
- schema 简图；
- 下一 Plan 边界：章节、角色、声优、配音关系、封面与角色图片。

不要复制整个需求基线，不写冗长开发流水账。

## 综合验证

```powershell
python -m pytest tests -q
python -m ruff check .
python -m bgm_side_b --help
bgmb --help
bgmb --version
git status --short
git log --oneline --decorate -10
```

若真实同步成功，执行 SQLite integrity check，并检查 format、季度月和孤儿关系。

仅在实际修正或文档变化时创建：

```text
docs: finalize subject sync foundation
```

没有变化时不创建空 commit。

## 最终报告

列出：

- Skill 是否加载；
- 6 个 Phase 状态；
- commit hash/message；
- 关键文件；
- 实际命令与测试结果；
- 真实 API 冒烟结果；
- 已知限制；
- 下一 Plan 起点；
- 明确声明未执行 `git push`。

完成后停止。
