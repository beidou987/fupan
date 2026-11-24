#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把单文件 index.html 转换为可部署 PWA 站点（app/index.html）。

改动：
1. 内联 REAL_DATA -> 外链 data.js（由 scripts/market_update.py 生成）
2. 所有硬编码交易日改为按 REAL_DATA.dates / sectorDates 动态取值
3. 注入启动闪屏、数据缺失兜底、service worker 注册、在线刷新
4. 静态文案中的日期范围改为运行时填充
"""

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "index.html")
OUT = os.path.join(ROOT, "app", "index.html")

HEAD_EXTRA = """<meta name="theme-color" content="#0b0e14">
<meta name="application-name" content="复盘终端">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="复盘终端">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<link rel="manifest" href="manifest.webmanifest">
<link rel="icon" type="image/png" sizes="192x192" href="icons/icon-192.png">
<link rel="apple-touch-icon" href="icons/icon-192.png">
"""

EXTRA_CSS = """/* ---- PWA ---- */
#bootSplash{position:fixed;inset:0;z-index:9999;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:10px;background:#0b0e14;color:#8b95a5;font-size:13px;letter-spacing:.06em}
#bootSplash b{color:#e8edf5;font-size:20px;letter-spacing:.18em}
#bootSplash i{display:block;width:26px;height:26px;border-radius:50%;border:2px solid #2a3444;border-top-color:#e2544c;animation:bootspin .8s linear infinite}
@keyframes bootspin{to{transform:rotate(360deg)}}
.is-ready #bootSplash{display:none}
.is-data-missing #bootSplash i{display:none}
.is-data-missing #bootSplash span{color:#e2544c;letter-spacing:.02em}
"""

SPLASH = """<div id="bootSplash"><b>复盘终端</b><i></i><span>正在载入行情数据</span></div>
"""

BOOT = """
<script>
(function () {
  'use strict';
  var D = window.REAL_DATA;
  function txt(id, value) {
    var el = document.getElementById(id);
    if (el) el.textContent = value;
  }
  function mmdd(d) { return d.slice(5); }
  if (!D || !D.dates || D.dates.length < 3) {
    document.documentElement.classList.add('is-data-missing');
    var tip = document.querySelector('#bootSplash span');
    if (tip) tip.textContent = '行情数据未加载，请联网后刷新页面';
    return;
  }
  var dates = D.dates;
  var dd = D.sectorDates || [mmdd(dates[1]), mmdd(dates[2])];
  var d0 = dates[0], d2 = dates[2];
  var rng = mmdd(d0) + ' 至 ' + mmdd(d2);

  txt('newsDate', mmdd(d2) + ' 收盘 · 数据截至 ' + mmdd(d2));
  txt('newsCount', D.briefs.length + ' 条 · 按热度');
  txt('sectorMeta', '板块强度 · 按 ' + dd[1] + ' 涨停数排序 · 左列对比 ' + dd[0]);
  txt('sectorNote', '黄色列为 ' + dd[1] + ' 涨停家数第一的主题板块；涨跌家数接口未开通，以真实首板 / 连板结构替代。');
  txt('mfNote', '数据为 ' + rng + ' 龙虎榜明细，按个股去重、单位亿元；龙虎榜为盘后公开数据，不构成投资建议。');
  txt('ladderNote', rng + ' 真实涨停梯队；左右滑动查看历史列；筛选数量为 3 日相关个股（含断板），断板组指 ' + dd[0] + ' 涨停且 ' + dd[1] + ' 未涨停。');
  txt('feedFoot', '数据来源：东方财富指数日线、涨停池、概念板块日线与龙虎榜明细；消息面为收盘行情自动生成的盘后速览，不构成投资建议。');

  var meta = document.querySelector('meta[name="description"]');
  if (meta) {
    meta.setAttribute('content', '移动端股票复盘工具：消息面、题材轮动、资金大数据、连板梯队与个人复盘。行情覆盖 ' + d0 + ' 至 ' + d2 + '，每个交易日收盘后自动更新。');
  }
  document.documentElement.classList.add('is-ready');

  /* 在线刷新：重新校验 data.js 后重载 */
  window.__refreshData = function () {
    if (!window.fetch) { location.reload(); return; }
    fetch('data.js?_=' + Date.now(), { cache: 'no-store' })
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.text();
      })
      .then(function (text) {
        if (text.indexOf('window.REAL_DATA') === -1) throw new Error('数据格式异常');
        location.reload();
      })
      .catch(function () {
        if (window.UI && window.UI.toast) UI.toast('无法获取最新数据，仍显示 ' + d2);
      });
  };

  /* 每日更新后提示用户切换 */
  window.__dataVersion = D.dataDate || d2;

  if ('serviceWorker' in navigator && location.protocol !== 'file:') {
    navigator.serviceWorker.register('sw.js').then(function (reg) {
      reg.addEventListener('updatefound', function () {
        var sw = reg.installing;
        if (!sw) return;
        sw.addEventListener('statechange', function () {
          if (sw.state === 'installed' && navigator.serviceWorker.controller) {
            if (window.UI && window.UI.toast) UI.toast('有新版本数据，刷新后生效');
          }
        });
      });
    }).catch(function () {});
  }
})();
</script>
"""

# 精确替换：(说明, old, new)
REPLACEMENTS = [
    # ---- 第一个 script：UI store 默认值 ----
    (
        "store 默认日期",
        "    dataDate: '2026-11-17',\n    ladderDate: '2026-11-17'",
        "    dataDate: '',\n    ladderDate: ''",
    ),
    # ---- 第二个 script：顶部引入动态日期 ----
    (
        "第二块 store.set",
        """  store.set({
    newsQuery: '',
    sectorQuery: '',
    sectorHighlight: '11-17',
    refreshStamp: '2026-11-17 15:30',
    favorites: new Set()
  });

  const NEWS_DATE = '2026-11-17';""",
        """  const DATA = window.REAL_DATA;
  const DATES = DATA.dates;
  const SD0 = DATA.sectorDates[0];
  const SD1 = DATA.sectorDates[1];

  store.set({
    newsQuery: '',
    sectorQuery: '',
    sectorHighlight: SD1,
    refreshStamp: DATA.refreshStamp,
    dataDate: DATES[2],
    ladderDate: DATES[2],
    favorites: new Set()
  });

  const NEWS_DATE = DATES[2];""",
    ),
    (
        "题材行取值",
        """      left: Object.assign({ name: row.name }, row['d11-16']),
      right: Object.assign({ name: row.name }, row['d11-17'])""",
        """      left: Object.assign({ name: row.name }, row['d' + SD0]),
      right: Object.assign({ name: row.name }, row['d' + SD1])""",
    ),
    (
        "题材高亮判断",
        "    const highlightName = highlight === '11-16' ? SECTORS[0].left.name : SECTORS[0].right.name;",
        "    const highlightName = highlight === SD0 ? SECTORS[0].left.name : SECTORS[0].right.name;",
    ),
    (
        "题材高亮标记",
        """        left: Object.assign({}, row.left, { highlight: highlight === '11-16' && row.left.name === highlightName }),
        right: Object.assign({}, row.right, { highlight: highlight === '11-17' && row.right.name === highlightName })""",
        """        left: Object.assign({}, row.left, { highlight: highlight === SD0 && row.left.name === highlightName }),
        right: Object.assign({}, row.right, { highlight: highlight === SD1 && row.right.name === highlightName })""",
    ),
    (
        "题材表头按钮",
        """      '<button class="day-col' + (highlight === '11-16' ? ' is-active' : '') + '" type="button" data-sector-highlight="11-16" aria-pressed="' + (highlight === '11-16') + '">11-16<small>前一日</small></button>' +
      '<button class="day-col' + (highlight === '11-17' ? ' is-active' : '') + '" type="button" data-sector-highlight="11-17" aria-pressed="' + (highlight === '11-17') + '">11-17<small>第 1</small></button>' +""",
        """      '<button class="day-col' + (highlight === SD0 ? ' is-active' : '') + '" type="button" data-sector-highlight="' + SD0 + '" aria-pressed="' + (highlight === SD0) + '">' + SD0 + '<small>前一日</small></button>' +
      '<button class="day-col' + (highlight === SD1 ? ' is-active' : '') + '" type="button" data-sector-highlight="' + SD1 + '" aria-pressed="' + (highlight === SD1) + '">' + SD1 + '<small>最新</small></button>' +""",
    ),
    (
        "题材单元格调用",
        """          sectorCell(row.left, '11-16') +
          sectorCell(row.right, '11-17') +""",
        """          sectorCell(row.left, SD0) +
          sectorCell(row.right, SD1) +""",
    ),
    (
        "题材设置项",
        """      '<div class="seg"><button type="button" data-set-highlight="11-16" aria-pressed="' + (highlight === '11-16') + '">11-16</button>' +
      '<button type="button" data-set-highlight="11-17" aria-pressed="' + (highlight === '11-17') + '">11-17</button></div></div>' +""",
        """      '<div class="seg"><button type="button" data-set-highlight="' + SD0 + '" aria-pressed="' + (highlight === SD0) + '">' + SD0 + '</button>' +
      '<button type="button" data-set-highlight="' + SD1 + '" aria-pressed="' + (highlight === SD1) + '">' + SD1 + '</button></div></div>' +""",
    ),
    (
        "刷新按钮",
        """      store.set({ refreshStamp: '2026-11-17 15:30' });
      UI.toast('已更新至 ' + store.get('refreshStamp') + ' 收盘数据');""",
        """      if (window.__refreshData) {
        UI.toast('正在检查最新数据');
        window.__refreshData();
      } else {
        UI.toast('已更新至 ' + store.get('refreshStamp') + ' 收盘数据');
      }""",
    ),
    # ---- 第三个 script ----
    (
        "第三块 store.set",
        """    ladderFilter: '全部梯队',
    ladderCol: '11-17',""",
        """    ladderFilter: '全部梯队',
    ladderCol: window.REAL_DATA.dates[2],""",
    ),
    (
        "梯队日期常量",
        """  const MF_DATES = window.REAL_DATA.dates;
  const MF = window.REAL_DATA.billboard;""",
        """  const MF_DATES = window.REAL_DATA.dates;
  const MF = window.REAL_DATA.billboard;
  const LD = window.REAL_DATA.ladder.dates;
  const L0 = LD[0], L1 = LD[1], L2 = LD[2];""",
    ),
    (
        "龙虎榜空态",
        """      content.innerHTML = '<div class="empty mf-empty"><strong>' + date + ' 暂无龙虎榜数据</strong><span>可用“上一日 / 下一日”切换 2026-11-13 至 11-17 三个交易日。</span></div>';""",
        """      content.innerHTML = '<div class="empty mf-empty"><strong>' + date + ' 暂无龙虎榜数据</strong><span>可用“上一日 / 下一日”切换 ' + MF_DATES[0] + ' 至 ' + MF_DATES[2] + ' 三个交易日。</span></div>';""",
    ),
    (
        "梯队行取值",
        """          d13: row['d11-13'],
          d16: row['d11-16'],
          d17: row['d11-17'],""",
        """          d13: row['d' + L0],
          d16: row['d' + L1],
          d17: row['d' + L2],""",
    ),
    (
        "梯队单元格",
        """            stateCell('11-13', row.d13, activeCol) +
            stateCell('11-16', row.d16, activeCol) +
            stateCell('11-17', row.d17, activeCol) +""",
        """            stateCell(L0, row.d13, activeCol) +
            stateCell(L1, row.d16, activeCol) +
            stateCell(L2, row.d17, activeCol) +""",
    ),
    (
        "梯队表头",
        """      '<th class="' + (activeCol === '11-13' ? 'active' : '') + '" data-ladder-col="11-13">11-13</th>' +
      '<th class="' + (activeCol === '11-16' ? 'active' : '') + '" data-ladder-col="11-16">11-16</th>' +
      '<th class="' + (activeCol === '11-17' ? 'active' : '') + '" data-ladder-col="11-17">11-17</th>' +""",
        """      '<th class="' + (activeCol === L0 ? 'active' : '') + '" data-ladder-col="' + L0 + '">' + L0 + '</th>' +
      '<th class="' + (activeCol === L1 ? 'active' : '') + '" data-ladder-col="' + L1 + '">' + L1 + '</th>' +
      '<th class="' + (activeCol === L2 ? 'active' : '') + '" data-ladder-col="' + L2 + '">' + L2 + '</th>' +""",
    ),
    (
        "龙虎榜边界提示",
        """      if (next < 0 || next >= MF_DATES.length) { UI.toast('已到数据边界（11-13 至 11-17）'); return; }""",
        """      if (next < 0 || next >= MF_DATES.length) { UI.toast('已到数据边界（' + MF_DATES[0].slice(5) + ' 至 ' + MF_DATES[2].slice(5) + '）'); return; }""",
    ),
    # ---- 会员页文案 ----
    (
        "会员页页脚",
        """      '<p class="me-footer"><strong>复盘终端 · 历史数据版</strong><br>行情覆盖 2026-11-13 至 11-17，不构成投资建议；<br>不采集账号、位置或通讯录信息。</p>';""",
        """      '<p class="me-footer"><strong>复盘终端 · 每日更新版</strong><br>行情覆盖 ' + window.REAL_DATA.dates[0].slice(5) + ' 至 ' + window.REAL_DATA.dates[2].slice(5) + '，每个交易日收盘后自动更新；<br>不采集账号、位置或通讯录信息。</p>';""",
    ),
    (
        "分享文案",
        """      '<p class="sheet-copy" style="margin-top:12px;padding:12px;border:1px dashed var(--line-strong);border-radius:10px;user-select:all">复盘终端 · 2026-11-13 至 11-17 历史复盘：盘后速览 9 条｜主题板块 7 个｜龙虎榜净买入 TOP10｜涨停梯队 63 只</p>' +""",
        """      '<p class="sheet-copy" style="margin-top:12px;padding:12px;border:1px dashed var(--line-strong);border-radius:10px;user-select:all">复盘终端 · ' + window.REAL_DATA.dates[0].slice(5) + ' 至 ' + window.REAL_DATA.dates[2].slice(5) + ' 复盘：盘后速览 ' + window.REAL_DATA.briefs.length + ' 条｜主题板块 ' + window.REAL_DATA.sectorRows.length + ' 个｜龙虎榜净买入 TOP10｜涨停梯队 ' + window.REAL_DATA.ladder.groups.reduce(function (n, g) { return n + g.rows.length; }, 0) + ' 只</p>' +""",
    ),
    (
        "常见问题-数据时效",
        """      '<div class="faq-item"><b>数据是实时的吗？</b><p>本版为 2026-11-13 至 11-17 的真实历史行情（iFinD 指数、东方财富涨停池与龙虎榜），不接实时行情。</p></div>' +""",
        """      '<div class="faq-item"><b>数据是实时的吗？</b><p>不是实时行情。每个交易日收盘后（15:10）自动抓取当日数据并写入站点，页面下次打开即为最新交易日；盘中数据不完整，不做展示。</p></div>' +""",
    ),
    (
        "常见问题-本地状态",
        """      '<div class="faq-item"><b>刷新后收藏和笔记去哪了？</b><p>单文件在沙箱 iframe 中运行，不使用本地存储，状态只保留在当前会话。</p></div>' +""",
        """      '<div class="faq-item"><b>刷新后收藏和笔记去哪了？</b><p>为保护隐私，收藏、笔记与登录状态只保留在当前页面会话，不上传服务器。</p></div>' +""",
    ),
    (
        "关于页说明",
        """      '<p class="sheet-copy" style="margin-top:10px">当前版本为单文件 HTML 原型：不联网、不采集账号与持仓、不执行交易；行情来自 iFinD 与东方财富 2026-11-13 至 11-17 历史数据。</p>' +""",
        """      '<p class="sheet-copy" style="margin-top:10px">当前版本为可安装的 PWA：每个交易日收盘后由服务端定时抓取东方财富指数日线、涨停池、概念板块日线与龙虎榜并写入站点；不采集账号与持仓，不执行交易。</p>' +""",
    ),
    # ---- 静态说明文案 ----
    (
        "消息面页脚",
        """        <p class="feed-foot"><strong>数据来源：</strong>iFinD 指数历史行情、东方财富涨停池与龙虎榜；消息面为收盘行情自动生成的盘后速览，不构成投资建议。</p>""",
        """        <p class="feed-foot" id="feedFoot"><strong>数据来源：</strong>东方财富指数日线、涨停池与龙虎榜；消息面为收盘行情自动生成的盘后速览，不构成投资建议。</p>""",
    ),
    (
        "题材页表头说明",
        """        <div class="sector-meta"><span>板块强度 · 按 11-17 涨停数排序 · 左列对比 11-16</span><span id="sectorCount"></span></div>""",
        """        <div class="sector-meta"><span id="sectorMeta">板块强度 · 按最新交易日涨停数排序</span><span id="sectorCount"></span></div>""",
    ),
    (
        "题材页脚注",
        """        <p class="sector-note">黄色列为 11-17 涨停家数第一的主题板块；涨跌家数接口未开通，以真实首板 / 连板结构替代。</p>""",
        """        <p class="sector-note" id="sectorNote">黄色列为最新交易日涨停家数第一的主题板块；涨跌家数接口未开通，以真实首板 / 连板结构替代。</p>""",
    ),
    (
        "龙虎榜页脚注",
        """        <p class="mf-note">数据为 2026-11-13 至 11-17 龙虎榜明细，按个股去重、单位亿元；龙虎榜为盘后公开数据，不构成投资建议。</p>""",
        """        <p class="mf-note" id="mfNote">数据为最近三个交易日龙虎榜明细，按个股去重、单位亿元；龙虎榜为盘后公开数据，不构成投资建议。</p>""",
    ),
    (
        "梯队页脚注",
        """        <p class="ladder-note">2026-11-13 至 11-17 真实涨停梯队；左右滑动查看历史列；筛选数量为 3 日相关个股（含断板），断板组指 11-16 涨停且 11-17 未涨停。</p>""",
        """        <p class="ladder-note" id="ladderNote">最近三个交易日真实涨停梯队；左右滑动查看历史列；筛选数量为 3 日相关个股（含断板）。</p>""",
    ),
    (
        "消息面日期占位",
        """<span id="newsDate">2026-11-17 收盘 · 数据截至 11-17</span>""",
        """<span id="newsDate">收盘 · 数据加载中</span>""",
    ),
    (
        "龙虎榜日期占位",
        """          <span class="mf-date" id="mfDate">2026-11-17</span>""",
        """          <span class="mf-date" id="mfDate">—</span>""",
    ),
]

GUARDS = [
    # 三个 IIFE 增加数据守卫
    (
        """<script>
(function () {
  'use strict';
  const UI = window.UI;
  const store = UI.store;

  const DATA = window.REAL_DATA;""",
        """<script>
(function () {
  'use strict';
  if (!window.REAL_DATA) { return; }
  const UI = window.UI;
  const store = UI.store;

  const DATA = window.REAL_DATA;""",
    ),
    (
        """<script>
(function () {
  'use strict';
  const UI = window.UI;
  const store = UI.store;

  function escapeHtml(value) {""",
        """<script>
(function () {
  'use strict';
  if (!window.REAL_DATA) { return; }
  const UI = window.UI;
  const store = UI.store;

  function escapeHtml(value) {""",
    ),
    (
        """<script>
(function () {
  'use strict';
  const UI = window.UI;
  const store = UI.store;
  const DATA = window.APP_DATA || { NEWS: [] };""",
        """<script>
(function () {
  'use strict';
  if (!window.REAL_DATA) { return; }
  const UI = window.UI;
  const store = UI.store;
  const DATA = window.APP_DATA || { NEWS: [] };""",
    ),
]


def main():
    html = open(SRC, encoding="utf-8").read()
    original_len = len(html)

    # 1. head 补充
    html = re.sub(
        r'<meta name="description" content="[^"]*">',
        '<meta name="description" content="移动端股票复盘工具：消息面、题材轮动、资金大数据、连板梯队与个人复盘。每日收盘后自动更新。">',
        html,
        count=1,
    )
    html = html.replace('<meta name="viewport"', HEAD_EXTRA + '<meta name="viewport"', 1)

    # 2. 内联数据 -> 外链
    html, n = re.subn(
        r"<script>window\.REAL_DATA = [\s\S]*?</script>",
        '<script src="data.js" onerror="document.documentElement.classList.add(\'is-data-missing\')"></script>',
        html,
        count=1,
    )
    assert n == 1, "未找到内联数据脚本"

    # 3. CSS 追加
    html = html.replace("</style>", EXTRA_CSS + "</style>", 1)

    # 4. body 开头加闪屏
    html = html.replace("<body>", "<body>\n" + SPLASH, 1)

    # 5. 精确替换
    failed = []
    for name, old, new in REPLACEMENTS:
        if html.count(old) != 1:
            failed.append("%s (命中 %d 次)" % (name, html.count(old)))
            continue
        html = html.replace(old, new, 1)

    # 6. 守卫（第一处依赖替换后的文本）
    for idx, (old, new) in enumerate(GUARDS, 1):
        if html.count(old) != 1:
            failed.append("守卫 %d (命中 %d 次)" % (idx, html.count(old)))
            continue
        html = html.replace(old, new, 1)

    if failed:
        print("以下替换未命中，请检查源文件：")
        for f in failed:
            print("  -", f)
        return 1

    # 7. 末尾注入启动脚本
    html = html.replace("</body>", BOOT + "</body>", 1)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(html)
    print("已生成 %s（%d -> %d 字节）" % (OUT, original_len, len(html)))
    return 0


if __name__ == "__main__":
    sys.exit(main())