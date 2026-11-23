# Bangumi Side B 项目接手

这是现有仓库的孵化迁入入口。工程资料按工作流资产仓库的 `750c69673ccaeac9b5999f3b38b8ebc11abebc24` 编制。文件已经落地到 `MyKr-YSteinsK/bangumi-side-b` 的 `docs/项目源文件/`，以选定源码提交下的文件为维护源。

## 创建 ChatGPT 项目

1. 项目名使用 **Bangumi Side B**。进入该项目的 **项目指令**，整份粘贴 [PROJECT_INSTRUCTIONS.md](PROJECT_INSTRUCTIONS.md)。
2. 上传同一源码提交的 [PROJECT_BRIEF.md](PROJECT_BRIEF.md)、[DECISIONS.md](DECISIONS.md)、[CURRENT_STATE.md](CURRENT_STATE.md) 和 [RUNBOOK.md](RUNBOOK.md)。需要讨论大改时同时上传 [升级路线与技术审查.md](升级路线与技术审查.md)。这些正文足以恢复目标、约束、运行方式及下一步。
3. 在一个新项目对话中确认能读到 `PROJECT_BRIEF.md` 的收录范围、`CURRENT_STATE.md` 的观察日期和 `DECISIONS.md` 的 D-024。记下上传所用源码提交。日后替换同名资料时，核对新对话读到的正文，再移除旧附件。

可复制的首条消息

> 这是 Bangumi Side B 现有项目接手。先读 PROJECT_BRIEF.md、CURRENT_STATE.md、DECISIONS.md 和 RUNBOOK.md，指出你的资料版本、现行产品边界和需要我批准的决定。讨论升级时再按《升级路线与技术审查》比较可选路径。当前请准备评审，等我确定本次升级范围后再交接执行。

## 从 Codex 恢复

把 Codex 工作目录设在 `bangumi-side-b` 仓库根目录。根 [AGENTS.md](../../AGENTS.md) 指向有效源文件。先核对分支、上游、工作区和本次权限，再按 [RUNBOOK.md](RUNBOOK.md) 选择所需的本地验证。普通任务可直接给 Codex，复杂交接使用获授权 Issue 中的完整 Plan，或可读的私有正文。多个交接由一个 Codex 对话按依赖和共用资源协调。

首次接手已批准的工作是整理项目资料、汇总历史、移动退役材料、做行为等价的内部拆分以及同步源码。升级候选留待逐项确认。后续初始化 GPT 项目与上传由项目所有者操作。
