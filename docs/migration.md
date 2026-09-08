# 新电脑迁移与 Plan 接续

本指南把“可从 Git 恢复的项目内容”和“必须从旧电脑另行迁移的本地状态”分开。仓库是
local-first 项目：源码、规则、配置、静态源资源、测试和文档在 Git 中；SQLite 事实库和
已校验封面属于本机工作数据，不进入 Git。

## 1. 新电脑首次恢复

准备 Git 和 Python 3.11 或更高版本。推荐使用与 CI 一致的 Python 3.11；项目声明的最低
版本是 3.11。然后执行：

~~~powershell
git clone https://github.com/MyKr-YSteinsK/bangumi-side-b.git
Set-Location bangumi-side-b

python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python -m bgm_side_b --version
python -m bgm_side_b --help
~~~

若 PowerShell 阻止激活脚本，可以不激活虚拟环境，直接调用
.\.venv\Scripts\python.exe 执行后续命令。运行完整浏览器/PWA 测试前，再执行：

~~~powershell
python -m playwright install chromium webkit
~~~

只运行非浏览器测试时不需要这一步。Linux 的 CI 工作流会额外安装系统依赖，Windows 不要
照搬 Linux 的 --with-deps。

完成安装后，先做不依赖私有数据库的仓库烟雾检查：

~~~powershell
git status --short --branch
git log -1 --oneline
python -m ruff check .
python -m pytest -m "not browser" -q
~~~

## 2. 恢复旧电脑的本地工作数据

如果要继续当前归档、重建现有站点或保留已核验封面，需要从旧电脑的仓库外备份复制：

- workspace/data/bangumi-side-b.sqlite3：当前 SQLite 事实库；
- workspace/covers/：已核验封面。

workspace/media/ 下的旧版下载封面缓存不被当前 build 或 serve 读取；除非需要保留历史原始
下载资产，否则不必迁移。workspace/backups/、workspace/state/、workspace/reports/、
workspace/cache/ 和 workspace/releases/ 也属于回滚、审计或构建派生物，不是恢复开发的
必要输入。

假设旧电脑导出的 workspace 备份目录用 <OLD_WORKSPACE_BACKUP> 表示，在新仓库根目录执行：

~~~powershell
New-Item -ItemType Directory -Force workspace\data, workspace\covers | Out-Null
Copy-Item "<OLD_WORKSPACE_BACKUP>\data\bangumi-side-b.sqlite3" "workspace\data\bangumi-side-b.sqlite3" -Force
Copy-Item "<OLD_WORKSPACE_BACKUP>\covers\*" "workspace\covers\" -Recurse -Force
~~~

报告、缓存、dist/site、构建状态、虚拟环境和临时目录都是派生内容，不是恢复开发所必需
的；它们应在新电脑重新生成。恢复后可离线验证并重建：

~~~powershell
python -m bgm_side_b audit
python -m bgm_side_b review
python -m bgm_side_b build --all
python -m bgm_side_b serve --open
~~~

build 完全离线，输出会重新生成到 dist/site/；serve 只服务已经存在的静态站点。
如果没有旧电脑数据备份，clone 仍能恢复源码和开发工具链，但不能恢复当前事实库、封面
和由它们生成的完整归档。重新获取 Bangumi 数据必须由当时的 Plan 或用户明确授权，不能
把迁移动作默认为 live sync。

## 3. Git、凭据与可恢复边界

新电脑需要单独配置 GitHub 的 SSH key 或 HTTPS credential manager，并用只读命令确认：

~~~powershell
git remote -v
git ls-remote origin HEAD refs/heads/main refs/heads/gh-pages
~~~

凭据、token、授权头、.env、数据库和封面都不应写入仓库，也不应写进 Plan 或提交信息。
当前仓库没有 Git submodule 或 Git LFS 对象，因此 clone 后不需要额外执行 submodule/LFS
初始化；如未来仓库出现 .gitmodules 或 .gitattributes，再按其内容补充初始化步骤。

源码 main 与 Pages 的 gh-pages 是两条不同交付边界。日常开发只需要 main；恢复线上站点的
源码能力不等于重新发布 Pages。发布仍必须遵守 release prepare → 普通推送源码 →
明确执行 release publish 的流程。

## 4. 无缝继续 Plan-48

在新电脑完成 clone 和本地数据恢复后，先把以下事实作为 Plan-48 的开场信息：

~~~powershell
git status --short --branch
git rev-parse HEAD
git rev-parse --abbrev-ref --symbolic-full-name '@{u}'
git diff --exit-code
~~~

然后把仓库外的 BGMB-Plan-48-*.md 重新附加到对话，或直接粘贴其内容，并给出类似下面的
开场要求（把尖括号内容替换成命令实际输出）：

> 这是 bangumi-side-b 的 Plan-48。新电脑已从官方 origin clone，当前基线是
> <git rev-parse HEAD>，上游是 <git rev-parse --abbrev-ref --symbolic-full-name @{u}>。
> 请先阅读 AGENTS.md、README.md、docs/project/PROJECT_BRIEF.md、
> docs/project/DECISIONS.md、docs/project/CURRENT_STATE.md、
> docs/project/DOC_OWNERSHIP.md、docs/development.md 和本次 Plan-48 文件。
> 不要重做 Plan-47；先完成 preflight，区分 tracked source 与本机 workspace，严格按
> Plan-48 的 MUST/Acceptance 执行。除非 Plan-48 明确写明或我在本轮明确授权，不要执行
> live Bangumi sync/assign；完成实现、文档和验证后，创建最终 source commit，执行一次
> 当前分支的普通 git push，确认本地 HEAD 等于上游 HEAD，再报告 TASK_RESULT。

Plan-48 不应依赖旧电脑的绝对路径、旧虚拟环境、旧浏览器缓存或旧的远端登录状态。真正需要
跨机保存的项目事实应进入受支持的 tracked 配置或 canonical project docs；不能把 SQLite、
封面、token 或临时报告塞进提交来“方便迁移”。

## 5. 迁移完成判定

新电脑满足以下条件即可视为恢复完成：

1. git status 干净，main 的 HEAD 与 origin/main 一致；
2. editable install、bgmb --help、Ruff 和非浏览器测试通过；
3. 复制 SQLite 与封面后，audit、build --all、serve 能运行；
4. 如需浏览器/PWA 验收，Playwright 浏览器已安装；
5. Plan-48 文件已重新附加，且开场明确当前 commit、数据恢复情况和是否授权 live sync。
