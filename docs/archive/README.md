# Bangumi Side B 历史摘要

2026-11-23 接手时，旧的过程记录归并到这里，有效项目资料集中到 [项目源文件](../项目源文件/00-START_HERE.md)。本页记录已发生的工作及其证据边界。应用版本记录见 [CHANGELOG.md](../../CHANGELOG.md)。

## 主要演进

| 阶段 | 保留结论 |
| --- | --- |
| 早期单季度原型 | 从 `2026-04` TV 的同步与审计起步。旧 reset、单季度操作和 split Local/Pages 结构已退役。 |
| 事实、构建与统一发布 | 事实库转为严格可验证的 SQLite schema。作品与季度 appearance 分开。统一生成 `dist/site`，引入校验过的 prepared 发布与季度级 PWA 下载。 |
| Plan-39 与迁移接续 | 用户反馈 iPhone/iPad Safari 与 standalone 检查未发现问题。设备及系统版本未提供，该次验收无合适的应用更新候选。迁移审计起点为 `4c458a7`，接收基线为 `7e7c767`。 |
| Plan-42/44/45/47（2026-08） | 分次完成有界事实验证，涵盖自然季度和 TV 边界、合拍/冲突、日本性人工裁决、可重新评估的自动排除，以及特殊场所剧场版过滤。Plan-45 记录 369 个旧自动排除 ID 的正典审阅和藤本树分篇身份。Plan-47 的 2025-01 刷新将旧自动项收敛为能解释的硬拒绝，事务完成后通过 review、audit 和受控增量 build。 |
| Plan-49–54、56–60、68–69（2026-10） | 局部改进搜索、分页/连续浏览、首屏信息密度、预览封面、菜单开合、移动筛选与 Back/Forward 还原。Plan-68/69 将加载耗时、主线程动效和 reduced-motion 结果分别验证。Plan-58 的整合审查纠正了操作文档，相关修改由 Plan-59 落地。 |
| Plan-55、61–64（2026-10） | 明确应用更新与季度新鲜度信号。局部模拟弱网证实旧网络等待成本；采用 verified active quarter 优先显示的有界行为，保留显式在线切换、per-client pin 与旧完整包。 |
| Plan-65–67（2026-10） | Archive 扩展至只读已验证活动包的本机跨季检索，支持分享已应用查询和 occurrence。同步摘要细化阶段耗时，保留作品与出现条目两种口径。 |
| Plan-70（2026-10） | 保留 Root 网络优先选择。合成弱网中多一次顺序 index 请求，冷/暖中位差约 350–631 ms；历史暖测未证明 HTTP cache 命中。旧 runtime index 与新 shell 可并存，离线仅进入已验证目标或走现有回退。结论 `KEEP_CURRENT`。 |
| Plan-71（2026-10） | 应用版本推进到 `0.9.0`。修订移动浏览历史回归假设，以 URL、`occ`、用户可见恢复结果为准。WebKit Archive 曾在导航/点击阶段超时，历史定向复测有通过记录。当前可核对的 Pages 批次见 [当前状态](../项目源文件/CURRENT_STATE.md)。 |
| Plan-72（2026-10） | 授权的 2026-10 尝试报告为 facts/covers incomplete。后续 Rev2 明确 `501729` 的低评分豁免，已在 `b6c21de` 进入源码。收录状态需要新授权和数据证据。 |
| 本轮孵化接入（2026-11） | 保留 Production 阶段与 D-001 至 D-023 的可追溯性。有效状态、运行、产品边界与 GPT 指令统一维护。后续路线等待所有者批准。 |

2026-10 的整体 A–F 体验研究涵盖渲染、首屏与图片、状态、本机数据、私人工作区和维护效率。获准落地的是独立、有限的 Plan。整体方案及未来大改见 [本轮审查](../项目源文件/升级路线与技术审查.md)。历史性能数字源自指定 fixture 和模拟条件，适用于方案对照；生产网络、真实设备和应用跨版本需要自己的验收。

## 查历史正文

下列文件从源码基线 `b6c21de22be97e3c731452fa470668ed4be9c56d` 取回。旧 Plan 正文、私有运行报告和外来上下文导出沿用各自的授权存放位置。

| 旧路径 | 现行承接 |
| --- | --- |
| `docs/project/CURRENT_STATE.md`、`DECISIONS.md`、`PROJECT_BRIEF.md`、`DOC_OWNERSHIP.md` | 本页、当前状态、长期决定、项目说明和运行手册；旧版本保留历史取证细节。 |
| `docs/migration.md`、`docs/development.md`、`docs/使用手册.md` | 运行手册和 [用户指南](../USER_GUIDE.md)，沿用仍适用的恢复与验收方法。 |
| `docs/archive/project-requirements-baseline.md`、`subject-sync.md`、`data-reset.md` | 旧阶段需求与做法。涉及数据操作按根 AGENTS 与现行 CLI 处理。 |
| `docs/repository-metadata.md` | GitHub About 的历史准备文档，仓库设置以实际 GitHub 状态为准。 |

例如从仓库根目录读旧的详细状态：

```powershell
git show b6c21de:docs/project/CURRENT_STATE.md
```

本轮退役文件与已确认过期的派生物搬到 Git 忽略的 `docs/delete/`，本机清单用于人工清理。保留 SQLite、已校验封面、备份、正式站点与 build state、现行环境、最新发布报告，以及后续还需要的测量和复核证据。
