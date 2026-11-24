# Bangumi Side B 当前状态

观察日为 2026-11-24。应用版本 `0.9.1`，处于 Production 维护。本批源码起点为 `38759cd6e28766ba83314c01aa8c58b78e0132ee`，本机 main 与配置的 origin/main 一致。工程范围与授权见 [Plan-73 · Issue #1](https://github.com/MyKr-YSteinsK/bangumi-side-b/issues/1)。

## Plan-73 本机恢复

| 故障点 | 修复与证据 |
| --- | --- |
| 配置替换后、SQLite 提交前后 | 基线中两个进程均可写入。终止第一个进程后，作品 101 的 TOML 留在十月，事实仍在四月。现在所有 CLI 写者争用同一系统锁。事务按 SQLite 内的提交标记保留已提交修改或恢复原始字节，涵盖既有裁决、未知作品导入、自动排除及需要同步删除的封面。 |
| 事实完成、封面未齐 | 季度事实提交时已记录封面 incomplete。区间重试会跳过先前完整季度并处理未完成季度。捕获取消时等待已经开始的封面任务退出。 |
| 文件替换及状态提交 | 构建先原地备份写集，以原子替换的小清单标记可开始写入。实测 Windows 曾阻止整个备份目录重命名，现行协议只需提交清单文件。中断后按当前 build state 决定回滚或确认。恢复本身可以再次中断。故障后出现第三种内容时保留文件及记录，完整候选校验拒绝待恢复树。 |
| 只读访问与发布 | status 仅读取轻量状态；serve 可逐请求读到跨代文件。prepare 的嵌套 build 与 publish 的候选验证各在所属命令锁内，准备记录绑定的实际树受到完整校验。 |

故障回归入口为 [进程与写入恢复](../../tests/test_write_recovery.py)、[增量写入](../../tests/test_incremental_writer.py)、[合成同步](../../tests/test_archive_sync.py) 和 [隔离发布](../../tests/test_release_workflow.py)。本机以 Windows、Python 3.14、父进程强制结束子进程、真实 Windows 文件占用及临时 Git remote 验证。真实断电、磁盘损坏、生产 Pages 与真实 Bangumi 均无本批验收证据。语义化版本升至补丁版，原因是并发写入的退出行为和故障后的恢复结果发生变化。

同一合成 SQLite、封面与静态源字节下，0.9.0 与 0.9.1 都生成 23 个相同路径。三个内容差异来自 Settings 的版本/更新日志及下游 `data/pwa-shell.json`、`sw.js`。将基线的版本与更新日志对齐后，23 个 SHA-256 完全一致。故障回归另比较当前版全量、增量、无变化重跑及重启恢复的同一棵树。

最终本机验收使用 Windows 11、Python 3.14.6 和 SQLite 3.50.4。Ruff、428 项非浏览器测试、21 项 Chromium release smoke 通过。中间一次并行运行在离线 Archive 排序菜单等待超时，最终独立 smoke 通过。实际断电与多次 HTTP 请求锁定同一站点代际仍在上述验证范围之外。

## 上一批观察（2026-11-23）

| 对象 | 观察值 |
| --- | --- |
| 本机 SQLite | `bgmb audit` 通过，513 部 unique subject，7 个可发布季度，审计输出无待裁决季度。 |
| 可发布季度 | `2025-01`、`2025-04`、`2025-07`、`2025-10`、`2026-01`、`2026-04`、`2026-07`。每季度 appearance 总数依次为 66、76、87、81、83、84、91。 |
| 本机正式站点 | `bgmb status` 检测到现有 `dist/site` 为 valid，公开季度与审计一致。清理后逐文件核对这棵树的 1045 个文件与原 build state 的哈希和大小。无 prepared release。 |
| 远端 Pages 来源 | GitHub branch publishing 使用 `gh-pages:/`。观察的远端 HEAD 为 `d413753495992b4202290ddddd09a9153dfd209a`，其批次是 `2026.10.16.1`，元数据标记源码 `87c712cfeb91`。本机发布分支仍在 `ddec0cf`。 |
| 真实数据范围 | 本轮只读核对已有资料库。无新增 live Bangumi 证据。当前 2026-10 不在可发布季度清单内。 |
| 工程接手 | 有效项目资料与 GPT 上传源已迁入本目录；旧版本可从 [历史摘要](../archive/README.md) 追溯。 |
| 回归证据 | 本机 416 项非浏览器测试和 21 项 Chromium release smoke 通过。基线与重构版用同一合成 SQLite 和封面构建，23 个站点文件的相对路径和 SHA-256 全部一致。Ruff 与 Markdown 相对链接检查通过。 |

源上次更新包含 `501729` 的低评分规则豁免。代码/规则生效与作品已收录是两件事，现有 SQLite 和 Pages 均不提供本轮授权刷新后的证据。旧季度的资料新鲜度取决于上次成功同步。

## 当前能力与风险

- 同步支持单季度和闭区间、阶段耗时、结构化报告及 REVIEW。可按用户确认分别裁决季度与日本性。本机站点构建保留增量产物状态及失败回滚。
- Quarter 浏览 TV 首播/续播与剧场版，Archive 支持季度、年份、范围和本机离线检索。已应用的查询和精确 occurrence 可通过 URL 恢复。本机跨季度模式只读取已验证活动季度。移动端详情、筛选 draft/apply 与浏览器历史是回归重点。
- 已验证季度可先显示保存内容，以 `data_revision` 检查更新并保持 per-client 选择。Root 仍先获取 archive index 才选目标季度。历史模拟弱网中这一步有可见成本。真实 Safari、已安装 PWA、当前生产网络以及 Pages 运行在本轮没有实测。
- 默认 CI 与深度 Chromium/WebKit 分层。历史 WebKit Archive 导航曾出现等待超时，证据条件见 [历史摘要](../archive/README.md)。页面、搜索、下载、缓存和构建模块的升级以回归行为和实测数据为准。

## 续接

维护从根 [AGENTS.md](../../AGENTS.md) 恢复。已接受的方向、取消范围与分批顺序见 [升级路线与技术审查](升级路线与技术审查.md)。准备 GPT 项目见 [接手说明](00-START_HERE.md)。
