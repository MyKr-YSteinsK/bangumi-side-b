# Bangumi Side B by MyKr

## 项目简介

Bangumi Side B 是一个本地优先的季度动画资料库。它把经过规则校验的 Bangumi
事实同步到 SQLite，再离线生成静态网页；页面运行时不读取 SQLite，也不会请求
Bangumi 数据或依赖业务后端。

在线资料库：[https://mykr-ysteinsk.github.io/bangumi-side-b/](https://mykr-ysteinsk.github.io/bangumi-side-b/)

## 当前收录范围

- 仅收录首播日期完整落在 `2026-04` 的日本 TV 动画；
- 当前资料快照共有 77 部作品；这是一份当前结果，不是固定的收录数量；
- 不收录剧场版、跨季度续播、角色、声优、角色图片或声优图片；
- 收录作品的主线章节与封面会随资料页生成。

国家/地区判定使用结构化 Infobox、精确配置标签和受限的季度 TV 默认规则；不会从
标题、简介或语言猜测事实。

## 功能特点

- 同一份资料模型生成 Windows 本地完整站点和 GitHub Pages 轻量站点；
- 季度页提供搜索、筛选、排序、资料抽屉和独立详情页；
- Pages 采用完整校验的 PWA 快照。首次初始化后可离线浏览季度、详情和封面；
- 正常启动不会自动检查新资料，更新只能由用户在设置页主动触发；
- `sync`、`build` 与 `publish` 相互独立，发布不会反向触发同步或构建。

## 使用方式

需要 Python 3.11 或更高版本。安装后可查看命令帮助：

```powershell
python -m bgm_side_b --help
```

## PWA 安装与首次初始化

打开 Pages 站点后，浏览器会先启用离线控制器并读取可用资料版本。点击“初始化本地资料库”后，
程序会下载并校验完整快照；完成后可从设置页进入季度资料库。首次初始化需要联网，之后可离线
浏览已校验的内容。

## 本地构建

```powershell
bgmb build 2026 4
bgmb build --all
```

构建结果位于 `dist/local/` 和 `dist/pages/`。构建只读取本地 SQLite 事实和已下载媒体，
可以离线执行。

## 数据同步

```powershell
bgmb sync 2026 4
bgmb audit
```

`sync` 仅同步当前季度资料；`audit` 检查资料是否满足发布边界。同步失败或中断时不会把
不完整资料标记为可发布。

## 发布流程

```powershell
bgmb build --progress plain --all
bgmb publish --progress plain --dry-run
```

dry-run 会组装并验证候选发布物，但不会发布。真实 `publish` 是单独的人工操作；它只使用
已验证的 Pages 构建结果，并保留原有完整快照直到替代版本校验并激活。

## 开发与测试

```powershell
python -m pip install -e ".[dev]"
python -m pytest tests -q
python -m ruff check .
```

## 开发状态与推荐发布流程

先用本地状态命令确认下一步；`doctor` 仅在未加 `--local` 时读取远端 Git 状态：

```powershell
bgmb status
bgmb doctor
```

日常发布使用高层编排，不需要手动串联 Pages 构建与 dry-run：

```powershell
bgmb release prepare
git push origin main
bgmb release publish
```

`release prepare` 只会审计、构建 Pages 和执行发布 dry-run，随后把当前
HEAD、源码程序版本、资料代次、Pages candidate 与远端 `gh-pages` 绑定到
prepared state。真实 `release publish` 是明确动作，且会再次确认这些事实、
干净工作树、没有 pending promotion，以及 `HEAD == origin/main`。任一项变化
都会要求重新 prepare。旧的 `build`、`publish --dry-run` 和 `publish` 命令继续保留。

更多开发与验收说明见 [开发文档](docs/development.md)。

仓库跟踪源代码、配置、模板、静态资源、测试和文档。SQLite 数据库、下载封面、报告、备份、
生成站点、缓存、临时文件和密钥不应提交。

更多说明见[同步说明](docs/subject-sync.md)、[国家/地区规则](docs/country-filter.md)、
[静态构建说明](docs/static-build.md)、[PWA 说明](docs/pwa.md)、
[发布说明](docs/publish.md)和[数据重置流程](docs/data-reset.md)。

## 数据与版权说明

MIT 许可证仅覆盖本仓库源码，不授予 Bangumi 数据、作品封面及其他第三方内容的使用许可。
