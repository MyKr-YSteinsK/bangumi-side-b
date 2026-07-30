# 日本 TV 自动范围过滤

第一版对 Bangumi `GET /v0/subjects/{id}` 的候选执行确定性、完全自动的国家/地区分类；
不需要逐条人工审核。规则不使用 AI、模糊匹配、标题、简介、语言或公司推断。

## 判定顺序

1. 优先读取已核验的 Infobox key：`制片国家/地区` 与 `国家/地区`。值先作 Unicode NFKC
   与 trim，再仅以 `/`、`／`、`、`、`,`、`，`、`;`、`；` 和 `・` 分隔。精确 token 为
   `日本` 或 `Japan` 时收录；明确非日本值时排除；同一一致值中的合拍国家允许。
2. 当结构化证据缺失、无法解析或冲突时，使用 `config/bangumi.toml` 的
   `[country_filter]` 精确标签列表。`positive_tags` 收录，`negative_tags` 排除，正负标签
   同时命中时排除。标签同样只作 NFKC、trim 和精确匹配。
3. 若仍没有地区证据，仅官方动画 `type == 2`、`platform == "tv"`、且完整首播日期落在
   当前目标季度的作品，因 `allow_tv_default_without_country = true` 自动收录。存在负向
   标签、不是 TV、类型不符或日期不在该季度时，一律不使用默认规则。

这保证了“无明确地区证据的季度 TV，在没有精确负向地区标签时自动收录”，同时保留了
可配置、可复现的边界。`日本风`、`日本語`、未知分隔符和任意近似词都不是证据。

## 审计

同步审计记录 subject ID、安全标题、最终决策、证据来源、结构化 token、命中的正负标签与
默认原因。`included_structured_japan`、`included_tag_japan` 和 `included_tv_default` 都是
正式收录决策；结构化字段缺失、无法解析或冲突只表示转入标签回退，不会单独排除。
