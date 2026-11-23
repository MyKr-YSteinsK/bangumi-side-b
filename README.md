# Bangumi Side B

Bangumi Side B 是一个本地优先的日本动画季度资料库。它把经过规则校验的 Bangumi 事实保存到 SQLite，再离线生成静态网页。访客可以在 Quarter 和 Archive 浏览、搜索、筛选和分享链接，也可以为指定季度下载完整离线包。 [在线资料库](https://mykr-ysteinsk.github.io/bangumi-side-b/)

## 当前收录

- 收录可验证的日本 TV 与剧场版。TV 分首播和续播 appearance，剧场版收录首播。作品封面使用本机已校验的文件。WEB、OVA、OAD、人物和声优资料在现行范围外。
- Browse/Search 发现候选，作品详情与确定性规则用于保存事实。日本性优先看 public `meta_tags` 的精确地区证据，按规则严格回退到结构化 Infobox。仍影响收录且无法由规则处理的日本性问题进入 REVIEW。确认后用 `bgmb classify` 裁决日本性，用 `bgmb assign` 裁决季度。 [范围与证据规则](docs/country-filter.md)
- PWA 可直接在线浏览，runtime cache 缓存部分访问过的资源。需要可靠离线使用时，在设置中主动下载单个季度或一组季度；未下载季度在断网时会显示不可用。 [离线与更新](docs/pwa.md)

## 本地使用

需要 Python 3.11 或更高版本。在仓库根目录安装：

```powershell
python -m pip install -e .
bgmb --help
```

已有本机数据库和封面时，可检查资料、离线生成并预览：

```powershell
bgmb audit
bgmb build --all
bgmb serve --port 8000
```

预览读取 `dist/site/`，访问 `http://127.0.0.1:8000/bangumi-side-b/`。克隆源码即可用 fixtures 做开发。完整站点需要另外恢复本机事实库与封面，或由维护者授权同步。已生成的站点、报告、缓存和私有数据保存在本机。 [用户指南](docs/USER_GUIDE.md) · [静态构建](docs/static-build.md)

`bgmb sync` 会访问 Bangumi，在事实与封面成功提交后可触发增量构建。`bgmb release prepare` 校验候选并准备发布；`bgmb release publish` 另行将验证过的候选发布到官方 `gh-pages`。 [发布步骤](docs/publish.md)

## 开发与接手

- Codex 从根 [AGENTS.md](AGENTS.md) 恢复。开发环境、数据位置和验证命令见 [运行手册](docs/项目源文件/RUNBOOK.md)。
- GPT 项目使用 [接手说明与源文件](docs/项目源文件/00-START_HERE.md)。当前基线和待决升级见 [当前状态](docs/项目源文件/CURRENT_STATE.md) 与 [升级路线与技术审查](docs/项目源文件/升级路线与技术审查.md)。
- [历史摘要](docs/archive/README.md) 汇总退役文档和主要历史证据。版本历史由 [CHANGELOG.md](CHANGELOG.md) 维护。

MIT 许可证覆盖本仓库源码。Bangumi 数据、封面等第三方内容遵循其各自许可。 [LICENSE](LICENSE)
