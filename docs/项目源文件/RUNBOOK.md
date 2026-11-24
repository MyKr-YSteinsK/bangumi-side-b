# Bangumi Side B 运行手册

命令默认在仓库根目录执行。已落地的保护边界见根 [AGENTS.md](../../AGENTS.md)，当前 SQLite、候选和分支观察见 [CURRENT_STATE.md](CURRENT_STATE.md)。

## 源码与开发环境

- Python 最低 3.11，依赖与开发工具范围以 `pyproject.toml` 为准。Windows 接手时已用既有 `.venv/Scripts/python.exe`，版本 3.14.6，SQLite 3.50.4，httpx 0.28.1，Pillow 11.3.0。CI 使用 Python 3.11。Node 可用于校验原生 JavaScript 语法，构建和运行不依赖 Node。
- 新检出先核对官方 origin、分支和工作树。已有可用 `.venv` 可直接使用。需要新环境时运行以下命令；Windows 可以直接调用解释器，Linux/macOS 用 `.venv/bin/python`。

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[dev]"
.venv/Scripts/python.exe -m bgm_side_b --version
.venv/Scripts/python.exe -m bgm_side_b --help
```

- 应用版本由 `src/bgm_side_b/_version.py` 和 `pyproject.toml` 共同确定。`CHANGELOG.md` 参与构建 Settings 更新记录。纯内部重构和交接文档整理无需例行改版本。本机源码版本调整后，可以用 `python -m pip install --no-deps -e .` 刷新已有开发环境的包元数据。
- 可选择测试所需浏览器。已用 `python -m playwright install --list` 核对本机 Chromium、WebKit 存在。新环境安装使用 `python -m playwright install chromium webkit`，Linux 系统依赖按 CI 工作流处理。

## 数据和输出位置

| 位置 | 用途 |
| --- | --- |
| `workspace/data/bangumi-side-b.sqlite3` | 本机事实，`bangumi-side-b-archive` schema 2。数据库和备份留在本机。 |
| `workspace/covers/<ID>.webp` | 已校验的作品封面。缺失或损坏按现行验证处理。 |
| `config/bangumi.toml` | 同步、过滤、人工排除与自动排除设置。自动排除的刷新以授权和真实证据为准。 |
| `config/quarter-overrides.toml`、`config/japanese-overrides.toml` | 用户明确确认的季度和日本性裁决，职责独立。 |
| `config/source-rules.toml`、`config/allowed-tags.toml` | 精确的来源映射和标签白名单。 |
| `dist/site`、`workspace/build-state.json` | 正式生成树和增量构建状态。保留上次可用输出，以及恢复所需的 staging/backup。 |
| `workspace/reports`、`workspace/state/prepared-release.json` | 本地运行报告与候选绑定。真实发布报告可用于追溯。 |
| `docs/delete/` | 本机隔离的待清理材料。Git 忽略此目录。 |

跨机器恢复时，在没有进程写入的条件下从受控备份恢复 SQLite 和对应的已验证封面，保留原机备份。仓库源码无法还原真实归档数据。恢复后用 `bgmb audit` 和 `bgmb status` 核查，需要时再离线 `bgmb build --all`。未知 schema 应停止、保护原库并查明来源。

## 命令与副作用

| 命令 | 意义 |
| --- | --- |
| `bgmb --version`、`bgmb --help` | 检查应用身份和实际参数。 |
| `bgmb audit`、`bgmb review [YEAR MONTH]` | 只读检查本机 facts、可发布季度或 REVIEW。audit 通过表示聚合检查通过，单个目标季度的 facts/covers 与 REVIEW 仍可从其结果和状态单独核对。 |
| `bgmb status`、`bgmb doctor --local` | 检查本地 SQLite、站点和 prepared 状态。status 快速检查 build state 与部分文件，doctor 做更完整的本地验证。无 `--local` 的 doctor 也查询 Git 远端。 |
| `bgmb build --all` / `bgmb build YEAR MONTH` | 离线生成或更新 `dist/site`、build state 和本地报告。 |
| `bgmb serve --port 8000` | 预览已有站点 `http://127.0.0.1:8000/bangumi-side-b/`。 |
| `bgmb sync YEAR MONTH [--refresh-existing]` | 需要本次 live 授权。成功提交事实与封面后可能自动构建受影响范围。不完整状态须保留旧可用产物。 |
| `bgmb assign`、`bgmb classify` | 按人工决定进行变更；未知作品的 `assign` 导入可能联网，须先取得 live 授权。 |
| `bgmb release prepare` | 审计、离线收敛 build、校验实际树、dry-run 并写准备状态，期间会只读访问 Git 远端。 |
| `bgmb release publish` | 另行授权的发布。绑定当前源码、实际候选树、`origin/main` 与准备时的 `gh-pages`，通过一次普通 push 发布。 |

发布操作及命令例子见 [用户指南](../USER_GUIDE.md) 和 [发布说明](../publish.md)。应用版本与 Pages 批次的关系见 [版本说明](../releases.md)。项目源码 SHA 只能追溯 tracked 输入；要证明某个站点的内容，核验候选文件树和发布记录。

## 写命令冲突与中断恢复

- `sync`（含自动 build）、`assign`、`classify`、`build`、`release prepare` 和 `release publish` 在整个命令期间独占 workspace 写锁。收到 `workspace writer busy` 时，等当前写命令结束再重试。进程退出或被终止时内核释放锁。保留 `workspace/writer.lock`，删除活跃锁文件可能让两个写者进入不同的锁。
- 若 status 显示事实 `recovery pending`，可先用离线 `bgmb build --all`。写者在读取配置前按 SQLite 提交标记恢复对应 TOML、封面和事实。封面阶段中断时检查受影响季度的 facts/covers 状态，在具备 live 授权后重跑原区间。此前已完整的季度按原有范围跳过规则处理。
- 若站点显示 `recovery pending`，关闭占用输出文件的程序，再运行 `bgmb build --all`。待恢复清单位于 `workspace/build-staging/recovery-*`，事实写集位于数据库旁的 `*.undo-*`。保留这两类目录、原库与现有站点。旧版未带清单的残余备份需人工比对。预览按请求读取磁盘文件，在构建中途可见不同代际；正式候选须通过完整校验。
- 遇到 `recovery conflict`，先另存用户修改与整个受影响写集。对照清单内的路径、原始证据和目标摘要，由维护者确定该文件应保留的版本。恢复到记录能识别的字节后重跑命令，再通过正常裁决命令重放另存的决定。不要通过删除证据或强制发布来处理冲突。

## 离线协议 v2 升级

1. 从 0.10.0 开始，CSS、应用脚本和 PWA 脚本的实际路径带有内容摘要。先保留同一代的 `dist/site` 与 `workspace/build-state.json` 备份。数据库、封面、人工配置与发布分支继续按原边界保护。
2. 用完整的本机资料运行 `bgmb build --all`，再用 `bgmb doctor --local` 检查。有已记录的受阻季度且只剩旧协议或损坏文件时，构建会指出季度和出错资源。可恢复经过验证的 v2 站点与对应 build state，或等本机事实完成后再构建。保留故障现场及备份。
3. 准备正式候选时重新运行 `bgmb release prepare`，为新源码和站点建立绑定。回退到旧源码时使用同一代的旧站点与状态副本。生产发布仍按独立授权。
4. 访问新版 Settings 时保持联网，确认应用刷新，然后重新下载需要的季度。旧 `bsb-*-v1` 缓存由用户决定是否清理。v2 的中断续传继续复用当前代已经验证过的字节。

若完整检查发现同长度损坏而增量构建仍写入 0 项，优先还原经过验证的同代站点和 build state。只有全部本机事实与封面准备妥当、写锁空闲、且没有待恢复写集时，才可把 `dist/site` 与 `workspace/build-state.json` 成对移到备份位置，再离线构建并运行 `bgmb doctor --local`。重建失败时可成对还原，保留原诊断证据。

## 验证与接续

```powershell
python -m ruff check --no-cache .
python -m pytest -m "not browser" -q
python -m pytest -m release_smoke -q
```

- 重验同步职责变更可先运行 `python -m pytest tests/test_admission.py tests/test_archive_sync.py tests/test_sync_replay.py tests/test_write_recovery.py -q`。其中 `test_cli_sync_replay` 在临时根目录走真实 CLI、SQLite 和构建，用合成 API 提供输入。跨版本对照可让两份源码各调用一次同一测试模块的 `replay(root, monkeypatch)`，分别给空目录，保持 fixture、静态资源、配置和更新日志字节一致，再逐步比较返回的映射。
- 构建变更优先跑 `tests/test_site_builder.py`、`tests/test_build_fingerprint.py`、`tests/test_incremental_writer.py` 和 `tests/test_sync_build_integration.py`。比较全量与增量时，固定版本、更新日志、静态字节、Pillow/libwebp、合成 SQLite 和封面。需要保留季度的两次实验应从相同的上次可用树与 build state 开始。中断与实际候选绑定另见 `tests/test_write_recovery.py`、`tests/test_unified_release.py` 和 `tests/test_release_workflow.py`。
- 公共资源身份变更另跑 `python -m pytest tests/test_retained_resources.py tests/test_versioned_assets_browser.py -q`。该组在隔离目录生成三代站点，浏览器测试使用本机 Chromium 与 WebKit。
- 默认 CI 跑 Python 3.11 的 Linux/Windows 非浏览器测试与 Linux Chromium release smoke。手动 Deep regression 才会跑完整 Chromium 和 WebKit 集。源码与工作流位于 `.github/workflows/`。回归依赖 fixtures、loopback HTTP 和临时 Git 仓库。需要避免 Windows 历史临时目录权限问题时，给 pytest 的 `--basetemp` 指定 `workspace/tmp/` 下本次未使用的目录。
- 事实变更跑候选发现、规则、REVIEW、事务和增量构建用例。重点检查重复执行、失败中断和目标/全局影响。本地构建或 PWA 改动查候选文件树、离线资源闭包、完整包替换及老数据/应用组合。真机、真实弱网、真实 Bangumi 与生产 Pages 按任务授权分别验收。
- 确认当前工作树和配置的上游再提交。只把授权的改动暂存，查询实际远端 ref 核对推送结果。正式 Plan 默认按获授权 Issue 交接，保留既有编号和可追溯证据。私有正文不进公开 Issue 或 Git。复杂依赖和下一步记入 [当前状态](CURRENT_STATE.md)。

## 专项文档与代码入口

- 领域证据与裁决由 [日本范围规则](../country-filter.md)、[API 字段记录](../api-field-notes.md) 及 `discovery.py`、`admission.py`、`sync_candidate.py`、`rules.py`、`sync.py`、`adjudication.py` 维护。
- 数据读写在 `database.py`、`repository.py`、`media.py`。生成链路见 [静态构建](../static-build.md) 与 `src/bgm_side_b/build/`。
- [视觉系统](../visual-system.md) 对应 `static/css/site.css` 和 `static/js/app.js`。[PWA 合同](../pwa.md) 对应 `static/js/pwa.js` 和 `static/pwa/sw.js`。发布编排和候选校验在 `src/bgm_side_b/release/`。
