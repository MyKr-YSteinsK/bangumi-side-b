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

UI 或静态资源修改后，先运行自动测试，再构建和准备发布：

```powershell
python -m pytest tests -q
python -m ruff check .
bgmb build --all --target pages
bgmb release prepare
```

`release prepare` 依次执行本地预检、资料审计、Pages-only build 和 publish
dry-run，并生成绑定当前 HEAD、程序版本、资料代次、Pages candidate 与远端
`gh-pages` 的 prepared state。它不会同步、构建 local、push 或真实发布。

确认准备结果后，由操作者明确执行：

```powershell
git push origin main
bgmb release publish
```

真实发布会重新确认 prepared state、`HEAD == origin/main`、候选内容、远端
`gh-pages` 和 pending promotion。任一事实改变后都必须重新运行
`bgmb release prepare`。项目不提供 preview 或 watch 工具。

## CI

GitHub Actions 只运行测试和 Ruff：Linux 使用 Chromium 执行 synthetic PWA
回归；Windows 运行非浏览器测试以覆盖输出 promotion、路径安全、CLI 和
release state。CI 不会访问真实 Bangumi 数据、读取本地 workspace、push、
publish 或使用 secrets。
