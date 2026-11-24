# 复盘终端 · 部署与每日更新

站点是纯静态 PWA，`app/` 目录即根目录，无需后端与数据库。行情数据由定时任务改写
`app/data.js` / `app/data.json`，页面下次打开即为最新交易日。

## 1. 目录结构

```
复盘终端/
├── app/                 # 站点根目录（对外暴露）
│   ├── index.html
│   ├── data.js          # 每日更新写入：window.REAL_DATA = {...}
│   ├── data.json        # 同一份数据的 JSON（供校验/二次消费）
│   ├── manifest.webmanifest
│   ├── sw.js
│   ├── icons/
│   └── ...
├── scripts/
│   ├── market_update.py # 每日更新入口
│   ├── make_pwa.py      # 由单文件 index.html 重新生成 app/index.html
│   └── make_icons.py
├── data/raw/            # 抓取的原始响应（留作溯源）
├── state/
│   ├── update.log       # 定时任务日志
│   ├── last_run.json    # 最近一次结果
│   └── backup/          # 每次更新前的上一版数据
└── deploy/
    ├── fupan-update.sh
    ├── fupan-update.service
    ├── fupan-update.timer
    ├── crontab.example
    └── nginx.conf.example
```

## 2. 部署到服务器

```bash
# 1) 放置代码
sudo mkdir -p /opt/fupan
sudo rsync -av --exclude state --exclude data/raw ./ /opt/fupan/
sudo chown -R www-data:www-data /opt/fupan/app
sudo chmod +x /opt/fupan/deploy/fupan-update.sh

# 2) Python 依赖
python3 -m pip install --user requests
# 或在虚拟环境中安装，并把 service 的 ExecStart 指向该解释器

# 3) 手动跑一次，确认能写数据
cd /opt/fupan && python3 scripts/market_update.py

# 4) Nginx（示例配置含 HTTPS、可选口令、MIME 与缓存策略）
sudo cp deploy/nginx.conf.example /etc/nginx/sites-available/fupan
sudo ln -s /etc/nginx/sites-available/fupan /etc/nginx/sites-enabled/fupan
sudo nginx -t && sudo systemctl reload nginx
```

**必须走 HTTPS**：Service Worker、安装到主屏、离线缓存都要求安全上下文。
`http://` 或 `file://` 打开时 SW 会静默不注册，页面仍可用但没有离线能力。

## 3. 定时任务（二选一）

### systemd timer（推荐）

```bash
sudo cp deploy/fupan-update.service /etc/systemd/system/
sudo cp deploy/fupan-update.timer   /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now fupan-update.timer
systemctl list-timers | grep fupan      # 确认下次触发时间
journalctl -u fupan-update -n 50        # 查看执行记录
```

### cron

见 `deploy/crontab.example`，一行即可。

## 4. 更新逻辑与失败处理

`scripts/market_update.py` 的执行顺序：

1. 拉上证指数日线，得到最近交易日序列；
2. 若当日尚未收盘（15:05 前）则剔除当日，取最近三个**完整**交易日；
3. 抓涨停池、概念板块列表、个股所属板块、板块与个股日线、龙虎榜、指数日线；
4. 构建数据集并做硬校验：交易日 3 个、三个指数收盘齐全、题材板块 ≥3、
   梯队个股 ≥20、龙虎榜 ≥20 行；
5. 校验通过才写盘：先把上一版复制到 `state/backup/`，再原子替换
   `app/data.json` 与 `app/data.js`（先写 `.tmp` 再 `os.replace`）。

任一步失败：**不覆盖**现有数据，退出码 1，`state/last_run.json` 记录原因，
站点继续使用上一交易日快照。

常用参数：

```bash
python3 scripts/market_update.py --dry-run     # 只构建与校验，不写文件
python3 scripts/market_update.py --force       # 忽略收盘时间门禁（调试用）
python3 scripts/market_update.py --offline     # 不联网，复用 data/raw 重建
python3 scripts/market_update.py --dates 2026-11-13 2026-11-16 2026-11-17
```

## 5. 回滚

```bash
ls /opt/fupan/state/backup                 # 形如 data-2026-11-17.js
sudo cp /opt/fupan/state/backup/data-2026-11-17.js /opt/fupan/app/data.js
sudo cp /opt/fupan/state/backup/data-2026-11-17.json /opt/fupan/app/data.json
```

回滚后用户下次打开页面即为该版本数据（`data.js` 走 network-first，不做强缓存）。

## 6. 告警

脚本支持环境变量 `ALERT_WEBHOOK`（企业微信/钉钉/飞书/Slack 的机器人地址均可）：

```
Environment=ALERT_WEBHOOK=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx
```

非交易日（周末、法定节假日）运行会因取不到当日数据而失败并触发告警。
如果不希望节假日报警，有两个做法：

- 把 cron 保留为 `1-5`，并在告警平台侧配置静默规则；
- 或改用 `systemd` timer 并让脚本在识别到「当日非交易日」时退出码 0
  （需要修改 `market_update.py` 的 `pick_dates` 分支，仅在确认非交易日时返回 0）。

## 7. 安装到手机

- **Android / 鸿蒙**：Chrome / 华为浏览器打开站点 → 菜单「添加到主屏幕」/「安装应用」。
- **iOS / iPadOS**：Safari 打开 → 分享 → 「添加到主屏幕」。iOS 16.4+ 支持 Web Push，
  本应用未使用推送，因此不依赖通知权限。

安装后以独立窗口运行，断网仍可打开上一次缓存的数据。

## 8. 已知限制

- 指数来自东方财富指数日线（`1.000001` / `0.399001` / `0.399006`），
  与原 iFinD 快照口径可能有小数级差异，已在页面「数据来源」中标注。
- 板块涨跌家数、资金流接口未开通，题材页以真实首板 / 连板结构替代。
- 炸板池明细接口不可用，「回封」标记取自涨停池 `zbc` 字段。
- 消息面是根据收盘行情自动生成的盘后速览，不是新闻原文。
- 数据仅覆盖交易日收盘后，盘中不更新。
- 页面状态（登录、收藏、笔记、标记）只存在当前会话，不落盘、不上传。

## 9. 数据源与限流应对

东方财富按子域分别限流：`push2his`（历史 K 线）与 `push2`（板块列表、个股所属板块）
可能断连，而 `push2ex`（涨停池）与 `datacenter-web`（龙虎榜）仍可用。脚本已按字段做了降级。
下表为 2026-11-19 的实测结论：

| 字段 | 主来源 | 已内置降级 | 替代源实测 |
|---|---|---|---|
| 指数日线 | 东财 `push2his` | 腾讯 `web.ifzq.gtimg.cn` | 腾讯可用，收盘价一致 |
| 个股日线 | 东财 `push2his` | 腾讯 | 腾讯可用 |
| 涨停池 | 东财 `push2ex` | 无 | 腾讯、通达信均无现成接口 |
| 龙虎榜 | 东财 `datacenter-web` | 无 | 腾讯、通达信均无 |
| 概念板块清单 | 东财 `push2` | 上次快照 | 通达信 `block_gn.dat` 解析错位，不可用 |
| 个股所属概念 | 东财 `push2` `slist` | 上次快照 | 腾讯无此接口 |
| 板块涨跌幅 | 东财板块 K 线 | 无（页面显示「—」） | 通达信板块指数取不到 |

**腾讯证券**：`web.ifzq.gtimg.cn` 日线（指数、个股）与 `qt.gtimg.cn` 收盘快照稳定可用，
可作为行情底座；但没有板块与龙虎榜接口（`getStockPlate` 等 controller 不存在），
涨停池只能靠全市场快照自算（含 ST 5%、北交所 30%、新股首日无涨跌幅限制等口径差异）。

**通达信**：没有公开行情 API。`pytdx` 是逆向协议，实测三台服务器均能建立连接，
但 `get_security_bars` / `get_index_bars` / `get_security_quotes` 一律返回空；
`get_and_parse_block_info('block_gn.dat')` 虽返回 138 万条记录，但只解析出 157 个板块名、
其中 21 个是 `\x00xxxxxx` 错位垃圾名，且缺「人工智能 / 半导体 / 芯片 / 机器人」等主流概念，
单板块成分上限 400。官网 `tdx.com.cn` 的历史数据下载路径已 404。因此不适合作为生产数据源。

**结论**：保持「东财为主 + 腾讯兜底」，不要切到通达信。若东财长期不可用，
需另找付费数据源（Wind、聚源、聚宽、Tushare Pro 等）或直接对接交易所公开数据；
交易所龙虎榜接口需按各自参数调试，本轮未验证通过，不作为承诺。

> 使用第三方接口请遵守其服务条款；逆向协议（如 pytdx）存在稳定性与合规风险，生产环境不建议依赖。