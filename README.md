# Bangumi Side B by MyKr

## 项目简介

Bangumi Side B 是一个本地优先的季度动画资料库。它把经过规则校验的 Bangumi
事实同步到 SQLite，再离线生成静态网页；页面运行时不读取 SQLite，也不会请求
Bangumi 数据或依赖业务后端。

在线资料库：[https://mykr-ysteinsk.github.io/bangumi-side-b/](https://mykr-ysteinsk.github.io/bangumi-side-b/)

## 当前收录范围

- 收录 SQLite 中已验证受管季度的日本 TV 与剧场版；
- TV 按 premiere/continuing 分区，Movie 只进入 premiere；
- 不收录 WEB、OVA、OAD、角色、声优、角色图片或声优图片；
- 封面从 `workspace/covers/<ID>.webp` 校验后复制，不重新编码。

旧版资料基线中的“77 部作品”只是历史记录，不代表当前 SQLite 快照的数量。

国家/地区判定以 Bangumi public `meta_tags` 中的精确地区 token 为首要证据，缺失时才
严格回退到结构化 Infobox 国家/地区字段。只有明确的日本证据会被收录；明确的非日本
证据会被排除，混合、冲突或缺失证据都会进入 REVIEW，不会从标题、简介或语言猜测事实。

## 功能特点

- 唯一派生站点是 `dist/site/`，本地预览与 Pages 使用同一棵树；
- 季度页把 TV 首播与续播放进同一连续列表（以“续播”标识区分），Movie 独立浏览；同源 JSON 承载详情 payload；
- Quarter 与 Archive 支持 Grid / List 两种浏览视图；List 保留小封面并提高标题、日期和来源的信息密度，选择会在本地浏览器中持久化；
- 移动端详情使用紧凑返回顶栏；浏览器 history 与 standalone PWA 左缘右滑共用同一返回路径，取消手势不会移动背景列表；
- `build` 完全离线，使用确定性投影、指纹和增量 patch，不复制完整站点快照；
- `sync` 事实提交成功后自动增量构建，失败或未完成事实不会覆盖 last-known-good；
- `release publish` 是明确的 release 动作，不会调用 sync 或 build；正式发布源始终是
  已验证的 `dist/site/`。

## 使用方式

需要 Python 3.11 或更高版本。安装后可查看命令帮助：

```powershell
python -m bgm_side_b --help
```

日常同步、REVIEW 裁决、构建、浏览、PWA 离线与正式发布的完整操作说明见
[用户指南](docs/USER_GUIDE.md)。

## PWA 与离线使用

Pages 站点可以直接在线浏览，不需要先初始化资料库。安装为 PWA 后，最小应用外壳可离线打开，
在线访问过的页面与资源可能由 runtime cache 保留；需要可靠离线浏览时，可由用户主动下载单个
季度。只有完成下载的季度保证离线可用，未下载季度在断网时会明确提示不可用。Settings 下载任务
在后台非阻塞运行，进度不会重建其它设置区或关闭用户正在操作的选择器；暂停、继续、取消和联网恢复
均由用户明确控制。

## 本地构建

```powershell
bgmb build 2026 4
bgmb build --all
```

构建结果唯一位于 `dist/site/`。开发预览：

```powershell
bgmb serve --port 8000
bgmb serve --open
```

预览地址为 `http://127.0.0.1:8000/bangumi-side-b/`，只读取已经生成的静态文件。成功启动
后会打印 URL 和 Ctrl+C 退出提示；默认不打开浏览器，只有 `--open` 才会请求系统默认浏览器。

## 数据同步

```powershell
bgmb sync 2026 4
bgmb audit
```

`sync` 事实和封面提交成功后会自动触发增量 `build`；同步失败或中断时不会把不完整资料
标记为可公开季度，也不会覆盖既有 last-known-good 站点。`audit` 中的 `数据库总作品` 是
全库 unique subject 数；可发布季度另显示 TV 首播、TV 续播、剧场版和合计 appearance 条目。

## 发布流程

```powershell
bgmb sync 2026 7
bgmb build --all                 # 可选；prepare 会再次离线收敛
bgmb release prepare
git push origin main
bgmb release publish
```

`release prepare` 会检查当前分支、工作树、SQLite、资料审计，运行等价于 `build --all` 的
离线收敛，验证 `dist/site` 的实际文件树并写入 schema 2 的 prepared state。它不 sync、
不 push，也不修改远端。`release publish` 只发布仍与 prepared state 完全一致的
`dist/site`，要求 `HEAD == origin/main`，通过临时 worktree 对 `gh-pages` 执行一次普通
push；任一绑定事实变化都要求重新 prepare。

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

`status` 和 `doctor --local` 只报告统一站点状态、候选哈希、公开季度与 prepared state；
`doctor` 额外读取 `origin/main` 和 `gh-pages`。真实 Pages 发布仍只能通过明确的
`release publish` 流程执行。

日常使用从[用户指南](docs/USER_GUIDE.md)开始；开发与验收说明见
[开发文档](docs/development.md)。

仓库跟踪源代码、配置、模板、静态资源、测试和文档。SQLite 数据库、下载封面、报告、备份、
生成站点、缓存、临时文件和密钥不应提交。

更多说明见[用户指南](docs/USER_GUIDE.md)、[同步说明](docs/subject-sync.md)、
[国家/地区规则](docs/country-filter.md)、
[静态构建说明](docs/static-build.md)、[PWA 说明](docs/pwa.md)、
[发布说明](docs/publish.md)和[数据重置流程](docs/data-reset.md)。

## 数据与版权说明

MIT 许可证仅覆盖本仓库源码，不授予 Bangumi 数据、作品封面及其他第三方内容的使用许可。
