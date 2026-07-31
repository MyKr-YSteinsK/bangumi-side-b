# 仓库公开元数据

本文件准备 GitHub 仓库 About 信息，供仓库维护者手动更新。Codex 不执行以下命令，也不向
GitHub API 写入任何内容；不需要在项目中保存额外 token。

## 仓库描述

基于 Bangumi 数据生成的本地优先季度动画资料库，支持静态网页、GitHub Pages 与离线 PWA。

## Homepage

https://mykr-ysteinsk.github.io/bangumi-side-b/

## Topics

`bangumi`、`anime`、`static-site`、`pwa`、`offline-first`、`python`、`sqlite`、`github-pages`

## 手动更新命令

在已登录且有仓库管理权限的终端中运行：

```powershell
gh repo edit MyKr-YSteinsK/bangumi-side-b `
  --description "基于 Bangumi 数据生成的本地优先季度动画资料库，支持静态网页、GitHub Pages 与离线 PWA。" `
  --homepage "https://mykr-ysteinsk.github.io/bangumi-side-b/"
```

```powershell
gh repo edit MyKr-YSteinsK/bangumi-side-b `
  --add-topic bangumi `
  --add-topic anime `
  --add-topic static-site `
  --add-topic pwa `
  --add-topic offline-first `
  --add-topic python `
  --add-topic sqlite `
  --add-topic github-pages
```

如果本机没有 `gh`，可在 GitHub 网页打开仓库，依次进入 **Settings → General →
Repository details** 更新 Description 与 Website；在仓库首页右侧 About 区域使用齿轮按钮更新
Topics。
