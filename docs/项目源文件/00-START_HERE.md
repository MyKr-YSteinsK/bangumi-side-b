# Bangumi Side B 项目接手

这是 `MyKr-YSteinsK/bangumi-side-b` 的接手入口。本次按孵化器 01–06 和模型推荐规则更新，采用 `MyKr-YSteinsK/ai-workflow-assets` 的 `f50e2b1682af21842a730f20c8533cbc3372a72f`，此前编制版本为 `750c69673ccaeac9b5999f3b38b8ebc11abebc24`。本项目的维护源是 Bangumi Side B 仓库 `docs/项目源文件/`。[本次孵化器来源](https://github.com/MyKr-YSteinsK/ai-workflow-assets/commit/f50e2b1682af21842a730f20c8533cbc3372a72f)

## GPT 项目同步清单

项目名使用 **Bangumi Side B**。由项目所有者在 GitHub 打开回执所列的 Plan-80 源码提交，从该提交取下表文件。同步以后继续记录所用 Bangumi Side B 提交，避免只凭文件名或日期认版本。

| 操作 | 同一源码提交下的文件 |
| --- | --- |
| 整份替换项目指令 | 粘贴 [PROJECT_INSTRUCTIONS.md](PROJECT_INSTRUCTIONS.md) 的完整正文。 |
| 替换四份常驻附件 | 单独上传 [PROJECT_BRIEF.md](PROJECT_BRIEF.md)、[CURRENT_STATE.md](CURRENT_STATE.md)、[DECISIONS.md](DECISIONS.md) 和 [RUNBOOK.md](RUNBOOK.md)。 |
| 讨论升级时补充 | 单独上传同版 [升级路线与技术审查.md](升级路线与技术审查.md)。 |

在新项目对话中核对实际读到的文件及来源提交。检查 `CURRENT_STATE.md` 的观察日期、应用版本和 Plan-80 小节，检查 `PROJECT_BRIEF.md` 的 TV 与剧场版收录范围、`DECISIONS.md` 的 D-025 和 D-026，以及 `RUNBOOK.md` 的验证入口。若新旧同名附件同时出现，明确指定新提交的文件。核对正文通过后退役旧版。只有附件可读时说明其快照版本及待 Codex 核实的本机差异。临近新一轮开发时，可根据项目指令读取上游的固定版本模型规则，也可使用注明上游原路径和提交的同源快照。

可复制的首条消息

> 这是 Bangumi Side B 的新对话。当前有效附件取自我给出的源码提交。请读 PROJECT_BRIEF.md、CURRENT_STATE.md、DECISIONS.md 和 RUNBOOK.md，说明你实际读到的版本、观察日期、Plan-80 小节及 D-025 和 D-026 的含义。遇到同名旧附件请指出差异。说明哪些事实仍由 Codex 在本机核实。

文件已写进仓库、同一提交已推送、项目指令和附件已在新 GPT 对话实际生效，分别核对。GPT 项目的生效以所有者同步后的新对话为准。

## 从 Codex 恢复

把 Codex 工作目录设在 `bangumi-side-b` 仓库根目录。从根 [AGENTS.md](../../AGENTS.md) 核对项目目标、分支、上游、工作树、当前状态和本次权限，再按 [运行手册](RUNBOOK.md#任务恢复与交接) 找任务正文和所需验证。普通任务可直接发短 Prompt 或续原对话，复杂任务读取获授权 Issue 的完整正文或可读的私有正文。保留既有 Plan 编号。Plan-73 确认的工程方向可在升级路线追溯，具体实施取决于当前授权。
