# 用 GitHub Pages 上线（零成本、每天自动更新）

这套方案不需要服务器、不需要电脑常开：页面托管在 GitHub Pages，数据由 GitHub Actions
每个交易日收盘后自动抓取并提交。手机浏览器打开网址即可，还能「添加到主屏幕」。

## 一、创建仓库

1. 登录 GitHub，点右上角 **+** → **New repository**。
2. 名称填 `fupan`（或你喜欢的名字），可见性选 **Public**。
   - Public 仓库才能免费使用 Pages，而且这个项目里没有密钥，代码公开没有风险。
3. 不要勾选初始化 README，直接 **Create repository**。

## 二、上传文件

解压 `复盘终端-PWA-工程.zip`，在该目录下执行（把 `你的用户名` 换成实际账号）：

```bash
cd 复盘终端-PWA-工程
git init
git add .
git commit -m "init: 复盘终端 PWA"
git branch -M main
git remote add origin https://github.com/你的用户名/fupan.git
git push -u origin main
```

没有装 Git 的话，也可以在仓库页面点 **uploading an existing file**，
把解压出来的 `app`、`scripts`、`data` 等目录拖进去上传。

`.github` 是隐藏目录，网页上传时经常选不到。这种情况改用网页新建文件：

1. 仓库页点 **Add file** → **Create new file**。
2. 文件名直接输入 `.github/workflows/update.yml`（斜杠会自动建目录）。
3. 把本工程里 `.github/workflows/update.yml` 的内容整段粘进去，提交。
4. `.gitignore` 同理，用 Create new file 输入 `.gitignore` 后粘贴内容。

## 三、开启 Pages

1. 进入仓库 → **Settings** → 左侧 **Pages**。
2. **Build and deployment** → Source 选 **GitHub Actions**（不要选 Deploy from a branch）。
3. 回到 **Actions** 标签页，左侧选「每日更新行情数据」，点 **Run workflow** 手动跑一次。
4. 等工作流转绿，Pages 会给出网址，形如：

```
https://你的用户名.github.io/fupan/
```

## 四、手机打开

- 安卓/鸿蒙：Chrome 或华为浏览器打开网址 → 菜单「添加到主屏幕」。
- iOS：Safari 打开 → 分享 → 「添加到主屏幕」。

之后桌面图标点开是无地址栏的全屏页面。

## 五、每天怎么更新

工作流里写的是：

```yaml
schedule:
  - cron: '10 7 * * 1-5'
```

GitHub Actions 的 cron **固定按 UTC 计时**，`07:10 UTC` 等于北京时间 `15:10`。
周一到周五触发，节假日也会跑，但取到的仍是最近交易日的数据，没有变化就不会产生提交。

执行链路是：抓取 → 构建 → 校验 → 提交 `app/data.js` → 重新部署 Pages。
任一步失败（接口不可用、数据不完整），脚本会保留上一版数据并让工作流标红，
站点继续用旧数据，不会出现半截数据。

## 六、注意事项

- **Actions 在境外机房运行**，访问东方财富可能比国内慢或偶发失败；
  脚本已经内置「东财失败 → 腾讯行情」的回退，板块类接口失败则沿用上次快照。
- **免费额度**：Public 仓库的 Actions 不计分钟数；Private 仓库每月 2000 分钟，
  这个任务每天约 1–2 分钟，够用。
- **手动补跑**：Actions 页面点 **Run workflow** 即可立刻跑一次。
- **改触发时间**：改 `.github/workflows/update.yml` 里的 cron，注意换算成 UTC（北京时间 −8 小时）。
- **想用自己的域名**：仓库 Settings → Pages → Custom domain，填域名后在域名商加一条 CNAME。
- **数据更新不落盘到手机**：页面每次打开会取最新 `data.js`，Service Worker 会做离线缓存，
  断网时显示上一次缓存的数据并标注「数据未更新」。

## 七、排查

| 现象 | 原因与处理 |
|---|---|
| 打开是 404 | Pages 的 Source 没选 GitHub Actions，或工作流还没跑成功 |
| 页面白屏提示数据未加载 | `app/data.js` 没上传成功，确认仓库里 `app/` 下有这个文件 |
| 工作流标红 | 点进去看日志；常见是接口临时不可用，手动 Run workflow 再跑一次 |
| 数据日期没变 | 当天不是交易日，或接口还没出数据；看 `state/last_run.json` 的记录 |