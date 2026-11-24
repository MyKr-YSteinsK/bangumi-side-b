# Bangumi Side B 项目说明

## 产品价值与现行范围

Bangumi Side B 将可核验的 Bangumi 事实编成日本动画季度档案。维护者可以在本机查证、裁决、重建和发布，访客可以在线浏览 Quarter 与 Archive。用户可以为指定季度建立可验证的离线副本。原地续接已有项目和 Production 维护阶段。

- 当前范围是 Japanese Anime（`type == 2`）的 TV 与剧场版。TV 的首播和续播是不同季度条目，剧场版只有首播。WEB、OVA、OAD、人员资料、角色和声优图片在当前范围外。
- Browse/Search 提供候选；canonical detail 和结构化 evidence 确定事实。查不清时按照规则处理，保留需要人工决定的 REVIEW 并区分不足证据与事实冲突。数据库中可发布季度随授权维护变化，缺失季度按实际状态显示。
- 在线用户可直接使用站点。Quarter 保持移动端连续浏览，Archive 提供分页、搜索、筛选与分享已应用的范围和 occurrence。移动详情使用全宽单栏并保持背景列表位置，移动筛选以草稿应用，返回或关闭放弃未应用项。保留本地动效和 reduced-motion 行为。PWA 短名称为 `Side B`。

## 运行边界

```text
Bangumi Browse/Search → canonical detail 和 deterministic rules → SQLite / 作品封面
SQLite + 封面 + tracked config + static → 离线 build → dist/site
dist/site → 本地 serve 或单独授权的 Pages → 同源页面、JSON 和可选 PWA 离线包
```

- 事实库是 `bangumi-side-b-archive` schema 2。当前程序对未知 schema fail closed。配置维护来源标签、收录政策及人工决定。只对有正典证据的作品封面生成公开素材。
- `bgmb sync` 和远程单作品导入须另行授权。正常开发使用 fixtures。有完整的事实与封面状态才进入发布候选，失败或中断保留 last-known-good。
- `dist/site` 是正式站点输出。在线与离线共用静态页面、CSS、JavaScript 和同源数据；应用无需业务后端或浏览器直连 Bangumi。PWA 下载完整季度，以 manifest 和哈希验证所需资源，保留上一个可用版本直到替代闭合。
- 版本号来自源码；发布的 Pages 批次身份、产物内容和实际远端提交独立核对。源码 push 通常触发 CI，Pages 从 `gh-pages` 独立发布。

## 维护与验收

开发可以重构内部模块，现有数据、CLI、路由、浏览交互、PWA 更新和发布条件作为当前可验证基线。涉及改动这些合同、接入新媒体范围、改变 canonical 数据或增加服务依赖时，由用户决定相关变更与验收。已接受方向和取消范围见 [升级路线与技术审查](升级路线与技术审查.md)。

事实类改动同时验证 admission、REVIEW、持久化和生成结果。构建类改动核对增量、全量、失败回退和候选闭包。PWA、移动 Web 或性能工作使用对应的浏览器、真机或网络环境完成任务要求的验收。已有接受决定、当前数据观察和运行入口分别见 [长期决定](DECISIONS.md)、[当前状态](CURRENT_STATE.md)、[运行手册](RUNBOOK.md)。
