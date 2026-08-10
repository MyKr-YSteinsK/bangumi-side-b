# 开发与验收

## 环境

在项目根目录激活虚拟环境；首次使用或依赖变动后安装开发依赖：

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

程序版本直接来自源码。普通代码修改不需要为了版本读取而重新执行
editable install；安装包元数据只会在 `bgmb doctor` 中作为环境提示显示。

## 日常检查

```powershell
bgmb status
bgmb doctor
```

`status` 只读取本地事实且不 fetch remote，输出一个主要下一步。`doctor --local`
同样只读本地；不带 `--local` 的 `doctor` 会读取 `origin/main` 和远端
`gh-pages`，网络失败只影响远端检查项，绝不访问 Bangumi API。

## 修改后的验收

代码或静态资源修改后，先运行自动测试，再构建唯一站点：

```powershell
python -m pytest tests -q
python -m ruff check .
bgmb build --all
bgmb serve --port 8000
```

`build` 只读 SQLite、配置、静态源文件和已校验封面，写入 `dist/site`；第二次相同
构建应无 artifact 写入。`serve` 只服务已有 `dist/site`，不读 SQLite、不构建、不同步
也不发布。release prepare 属于后续发布生命周期。

整份 Plan 的集成验证通过后，按仓库规则由 Codex 执行一次普通分支 push；它不是 Pages
发布。真实发布仍需明确执行：

```powershell
git push origin main
bgmb release publish
```

真实发布会重新确认 prepared state、`HEAD == origin/main`、候选内容、远端 `gh-pages`
和 pending promotion。任一事实改变后都必须重新运行 `bgmb release prepare`。

## CI

GitHub Actions 只运行测试和 Ruff：Linux 使用 Chromium 执行 synthetic PWA
回归；Windows 运行非浏览器测试以覆盖输出 promotion、路径安全、CLI 和
release state。CI 不会访问真实 Bangumi 数据、读取本地 workspace、push、
publish 或使用 secrets。
