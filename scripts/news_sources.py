#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""消息面新闻源：财联社电报 + 中国政府网要闻 + 国家发展改革委新闻发布。

所有源均为公开页面/公开接口，只读取标题、时间、摘要与原文链接，不做二次分发。
抓取失败不抛异常，返回该源的空列表并把原因写入 errors，由上层决定是否降级。
"""

import hashlib
import html
import json
import re
import time
from datetime import datetime, timedelta, timezone

import requests

CST = timezone(timedelta(hours=8))

UA_PC = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# 题材关键词：命中即作为消息标签，尽量贴近 A 股常见炒作主线
KEYWORDS = [
    ("芯片", ["芯片", "半导体", "晶圆", "光刻", "存储芯片", "先进封装", "EDA"]),
    ("算力", ["算力", "数据中心", "AIDC", "东数西算", "智算", "服务器"]),
    ("机器人", ["机器人", "人形机器人", "具身智能", "灵巧手", "减速器"]),
    ("人工智能", ["人工智能", "AI", "大模型", "智能体", "多模态"]),
    ("CPO", ["CPO", "光模块", "光通信", "硅光"]),
    ("6G", ["6G", "5G-A", "通信设备", "卫星互联网", "星链"]),
    ("新能源", ["光伏", "储能", "锂电", "固态电池", "风电", "氢能", "钠电"]),
    ("汽车", ["汽车", "智能驾驶", "自动驾驶", "整车", "车规"]),
    ("医药", ["医药", "创新药", "医疗", "疫苗", "流感", "中药", "CXO"]),
    ("军工", ["军工", "国防", "航空发动机", "导弹", "卫星"]),
    ("消费", ["消费", "零售", "免税", "白酒", "食品饮料", "家电"]),
    ("地产基建", ["房地产", "地产", "基建", "城中村", "城市更新", "专项债"]),
    ("金融", ["券商", "银行", "保险", "资本市场", "IPO", "并购重组", "市值管理"]),
    ("政策", ["国务院", "中共中央", "政治局", "国常会", "发改委", "工信部", "财政部",
              "央行", "证监会", "商务部", "国家统计局", "十五五", "规划", "条例", "意见"]),
]

# 与股市无关的官媒要闻：农业、气象、民生、外事礼节、人事纪检等，命中即丢弃
OFF_TOPIC = ["秋冬种", "冬季田管", "夏粮", "秋粮", "春耕", "播种", "稻谷", "小麦", "玉米",
             "气象", "台风", "暴雨", "降雪", "寒潮", "供暖", "春运", "暑运",
             "会见", "会谈", "贺电", "致电", "致贺", "访问", "致辞", "出席", "开幕", "闭幕",
             "任命", "免职", "辞去", "纪委", "监察", "巡视", "审计署", "表彰", "追授", "逝世", "讣告",
             "全国秋冬种", "粮食安全", "耕地", "乡村振兴", "老旧小区", "医保目录"]

# 命中任一即视为与股市/板块相关
MARKET_WORDS = ["A股", "沪深", "上证", "深证", "创业板", "科创板", "北交所", "两市", "大盘", "指数",
                "涨停", "连板", "龙头", "题材", "板块", "概念", "主力", "游资", "北向", "融资",
                "IPO", "上市", "再融资", "定增", "减持", "回购", "增持", "并购", "重组", "借壳",
                "业绩", "预告", "财报", "营收", "净利润", "订单", "中标", "签约", "供货", "涨价",
                "提价", "降价", "产能", "扩产", "停产", "检修", "出口", "进口", "关税", "制裁",
                "补贴", "退税", "试点", "规划", "招标", "集采", "医保", "限价", "牌照",
                "证监会", "央行", "人民银行", "交易所", "国资委", "工信部", "发改委",
                "财政部", "商务部", "能源局", "药监局", "网信办", "数据局", "国防科工局",
                "半导体", "芯片", "算力", "人工智能", "机器人", "新能源", "光伏", "储能",
                "锂电", "固态电池", "医药", "创新药", "军工", "航天", "卫星", "低空",
                "消费", "白酒", "地产", "基建", "券商", "银行", "保险", "有色金属", "稀土",
                "煤炭", "钢铁", "化工", "电力", "核电", "氢能", "汽车", "智能驾驶"]

POLICY_WORDS = ["国务院", "中共中央", "政治局", "国常会", "国家发展改革委", "发改委",
                "工业和信息化部", "工信部", "财政部", "中国人民银行", "央行", "证监会",
                "商务部", "国家统计局", "十五五", "规划", "条例", "意见", "通知", "试点"]


def _clean(text):
    text = html.unescape(re.sub(r"<[^>]+>", "", text or ""))
    return re.sub(r"\s+", " ", text).strip()


def _tags_from(title, body, theme_names):
    """从标题/正文提取标签：先匹配当日真实题材名，再匹配关键词词典。"""
    hay = (title + " " + body)[:600]
    tags = []
    for name in theme_names:
        if name and len(name) >= 2 and name in hay:
            tags.append(name)
            if len(tags) >= 2:
                break
    for label, words in KEYWORDS:
        if len(tags) >= 3:
            break
        if label in tags:
            continue
        if any(w in hay for w in words):
            tags.append(label)
    seen, out = set(), []
    for t in tags:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out[:3]


def _relevant(it, theme_names):
    """是否与股票/板块相关：先排除民生外事类，再看是否命中市场或产业关键词。"""
    hay = (it["title"] + " " + it["body"])[:800]
    if any(w in it["title"] for w in OFF_TOPIC):
        return False
    if it.get("stock_list"):
        return True
    if any(n and len(n) >= 2 and n in hay for n in theme_names):
        return True
    return any(w in hay for w in MARKET_WORDS)


def _score(source_kind, level, title, body):
    """财经相关度：政策主体与市场行为词加权，财联社 A/B 级加权。"""
    score = 0
    if source_kind == "official":
        score += 2
    if level == "A":
        score += 5
    elif level == "B":
        score += 3
    hay = title + body[:300]
    score += sum(1 for w in POLICY_WORDS if w in hay)
    score += 2 * sum(1 for w in ("涨停", "并购", "重组", "减持", "回购", "增持", "订单", "中标",
                                 "涨价", "提价", "制裁", "关税", "试点", "规划") if w in hay)
    if "陆家嘴" in hay:
        score += 3
    # A 股本地消息优先于海外行情
    score += sum(1 for w in ("A股", "沪指", "上证", "深证", "创业板", "科创板", "两市",
                             "涨停", "板块", "概念", "北向", "券商", "公募", "私募",
                             "个股", "龙虎榜", "游资", "主力") if w in hay)
    score -= sum(1 for w in ("美股", "纳指", "道指", "标普", "英伟达", "美联储", "美债", "欧股") if w in hay)
    return score


# ------------------------------------------------------------------ 财联社
def cls_telegraph(rn=100, category="important"):
    """财联社电报。接口需要先取首页 cookie（acw_tc），再按 sha1+md5 规则签名。"""
    s = requests.Session()
    s.headers.update({"User-Agent": UA_PC, "Referer": "https://www.cls.cn/",
                       "Accept": "application/json, text/plain, */*"})
    s.get("https://www.cls.cn/telegraph", timeout=15)

    def sign(params):
        raw = "&".join("%s=%s" % (k, params[k]) for k in sorted(params))
        return hashlib.md5(hashlib.sha1(raw.encode()).hexdigest().encode()).hexdigest()

    p = {"app": "CailianpressWeb", "os": "web", "sv": "8.4.6",
         "rn": str(rn), "category": category, "last_time": "", "refresh_type": "1"}
    p["sign"] = sign(p)
    d = s.get("https://www.cls.cn/v1/roll/get_roll_list", params=p, timeout=20).json()
    rows = ((d.get("data") or {}).get("roll_data")) or []
    out = []
    for r in rows:
        if r.get("is_ad"):
            continue
        title = _clean(r.get("title") or "")
        body = _clean(r.get("content") or r.get("brief") or "")
        if not title:
            # 无标题电报：去掉“财联社X月X日电，”前缀后取正文首句
            lead = re.sub(r"^财联社\d+月\d+日电[，,]", "", body)
            title = lead.split("。", 1)[0][:48] if lead else ""
        if len(title) < 8:
            continue
        ts = int(r.get("ctime") or 0)
        out.append({
            "source": "财联社",
            "kind": "cls",
            "level": r.get("level") or "C",
            "title": title,
            "body": body,
            "time": datetime.fromtimestamp(ts, CST).strftime("%Y-%m-%d %H:%M"),
            "date": datetime.fromtimestamp(ts, CST).strftime("%Y-%m-%d"),
            "url": "https://www.cls.cn/detail/%s" % r.get("id") if r.get("id") else "",
            "subjects": [x.get("subject_name") for x in (r.get("subjects") or []) if x.get("subject_name")][:3],
        })
    if len(out) < 10 and category:
        return cls_telegraph(rn=rn, category="")
    return out




# ------------------------------------------------------------------ 陆家嘴金融网（深度）
LJZ_SECTIONS = [
    ("/topic/risk-alert-weekly", "风险预警"),
    ("/section/industry-tech", "产业科技"),
    ("/section/macro-policy", "宏观政策"),
    ("/section/finance", "财经"),
]
LJZ_SKIP = ["招聘", "招募", "大赛", "榜单", "活动报名", "公益", "年会", "论坛", "沙龙", "课程", "直播预告"]


def _ljz_meta(html):
    desc = re.search(r'<meta[^>]+(?:name|property)="(?:description|og:description)"[^>]+content="([^"]{10,400})"', html)
    pub = re.search(r'<meta[^>]+property="article:published_time"[^>]+content="([^"]{10,30})"', html)
    return (_clean(desc.group(1)) if desc else "", pub.group(1) if pub else "")


def ljz_deep(theme_names, limit=3):
    """陆家嘴金融网深度文章：优先《风险预警》周刊与产业科技栏目，抓正文摘要与发布时间。"""
    cands, seen = [], set()
    for path, label in LJZ_SECTIONS:
        try:
            r = requests.get("https://www.ljzfin.com" + path,
                             headers={"User-Agent": UA_PC}, timeout=25, verify=False)
            r.encoding = r.apparent_encoding or "utf-8"
            html = r.text
        except Exception as exc:  # noqa: BLE001
            print("    陆家嘴栏目失败 %s %s" % (path, exc))
            continue
        for href, inner in re.findall(r'<a[^>]+href="(/article/[^"#?]+)"[^>]*>(.*?)</a>', html, re.S):
            title = _clean(re.sub(r"<[^>]+>", "", inner))
            if len(title) < 8 or href in seen:
                continue
            if any(w in title for w in LJZ_SKIP):
                continue
            seen.add(href)
            cands.append({"href": href, "title": title, "label": label, "section": path})

    # 《风险预警》与产业科技优先
    order = {"风险预警": 0, "产业科技": 1, "宏观政策": 2, "财经": 3}
    cands.sort(key=lambda x: order.get(x["label"], 9))

    out = []
    for c in cands:
        if len(out) >= limit:
            break
        try:
            d = requests.get("https://www.ljzfin.com" + c["href"],
                             headers={"User-Agent": UA_PC}, timeout=25, verify=False)
            d.encoding = d.apparent_encoding or "utf-8"
            desc, pub = _ljz_meta(d.text)
        except Exception as exc:  # noqa: BLE001
            print("    陆家嘴正文失败 %s %s" % (c["href"], exc))
            desc, pub = "", ""
        if not pub:
            continue
        when = pub[:16].replace("T", " ")
        # 标题里带「|《风险预警》252期上新」这类尾巴，去掉便于阅读
        title = re.split(r"\s*\|\s*《风险预警》", c["title"])[0].strip()
        out.append({
            "source": "陆家嘴金融网", "kind": "deep", "level": "", "deep": True,
            "title": title, "body": desc, "time": when, "date": when[:10],
            "url": "https://www.ljzfin.com" + c["href"],
            "subjects": [c["label"]],
        })
    print("    陆家嘴深度：%d 篇" % len(out))
    return out


# ------------------------------------------------------------------ 财经快讯补充源
def sina_7x24(size=60):
    """新浪财经 7×24：财联社被限流时的主力补充，财经属性强。"""
    r = requests.get("https://zhibo.sina.com.cn/api/zhibo/feed", params={
        "page": 1, "page_size": size, "zhibo_id": 152, "tag_id": 0, "dire": "f", "dpc": 1,
    }, headers={"User-Agent": UA_PC, "Referer": "https://finance.sina.com.cn/"}, timeout=25)
    lst = (((r.json().get("result") or {}).get("data") or {}).get("feed") or {}).get("list") or []
    out = []
    for it in lst:
        body = _clean(it.get("rich_text") or it.get("text") or "")
        if len(body) < 8:
            continue
        m = re.match(r"^【([^】]{4,44})】(.*)$", body)
        title = m.group(1) if m else body.split("。")[0][:48]
        if len(title) < 8:
            continue
        stamp = (it.get("create_time") or "")[:16]
        if len(stamp) < 16:
            continue
        out.append({
            "source": "新浪财经", "kind": "feed", "level": "",
            "title": title, "body": body, "time": stamp, "date": stamp[:10],
            "url": it.get("docurl") or "", "subjects": [],
        })
    return out


def ths_news(pagesize=50):
    """同花顺快讯：偏 A 股个股与板块，带 A股/港股标签。"""
    r = requests.get("https://news.10jqka.com.cn/tapp/news/push/stock/",
                     params={"page": 1, "tag": "", "track": "website", "pagesize": pagesize},
                     headers={"User-Agent": UA_PC}, timeout=25)
    lst = ((r.json().get("data") or {}).get("list")) or []
    out = []
    for it in lst:
        title = _clean(it.get("title") or "")
        if len(title) < 8:
            continue
        try:
            ts = int(it.get("ctime") or 0)
        except Exception:  # noqa: BLE001
            ts = 0
        if not ts:
            continue
        when = datetime.fromtimestamp(ts, CST)
        out.append({
            "source": "同花顺", "kind": "feed", "level": "",
            "title": title, "body": _clean(it.get("digest") or ""),
            "time": when.strftime("%Y-%m-%d %H:%M"), "date": when.strftime("%Y-%m-%d"),
            "url": it.get("url") or "",
            "subjects": [x.get("name") for x in (it.get("tags") or []) if x.get("name")][:3],
        })
    return out


# ------------------------------------------------------------------ 中国政府网
def gov_yaowen(days=3):
    url = "https://www.gov.cn/yaowen/liebiao/YAOWENLIEBIAO.json"
    r = requests.get(url, headers={"User-Agent": UA_PC, "Referer": "https://www.gov.cn/yaowen/liebiao/"},
                     timeout=20, verify=False)
    rows = r.json()
    since = (datetime.now(CST) - timedelta(days=days)).strftime("%Y-%m-%d")
    out = []
    for it in rows:
        day = (it.get("DOCRELPUBTIME") or "")[:10]
        if not day or day < since:
            continue
        title = _clean(it.get("TITLE") or "")
        if not title:
            continue
        out.append({
            "source": "中国政府网",
            "kind": "official",
            "level": "",
            "title": title,
            "body": _clean(it.get("SUB_TITLE") or ""),
            "time": day,
            "date": day,
            "url": it.get("URL") or "",
            "subjects": [],
        })
    return out


# ------------------------------------------------------------------ 发改委
def ndrc_news(days=4):
    url = "https://www.ndrc.gov.cn/xwdt/xwfb/"
    r = requests.get(url, headers={"User-Agent": UA_PC}, timeout=20, verify=False)
    r.encoding = r.apparent_encoding or "utf-8"
    pairs = re.findall(r'<a[^>]+href="(\./20\d{4}/t(\d{8})_\d+\.html)"[^>]*>\s*([^<]{6,90})\s*</a>', r.text)
    since = (datetime.now(CST) - timedelta(days=days)).strftime("%Y%m%d")
    out = []
    for href, compact, title in pairs:
        if compact < since:
            continue
        title = _clean(title)
        if not title:
            continue
        out.append({
            "source": "国家发展改革委",
            "kind": "official",
            "level": "",
            "title": title,
            "body": "",
            "time": "%s-%s-%s" % (compact[:4], compact[4:6], compact[6:]),
            "date": "%s-%s-%s" % (compact[:4], compact[4:6], compact[6:]),
            "url": "https://www.ndrc.gov.cn/xwdt/xwfb/" + href.lstrip("./"),
            "subjects": [],
        })
    return out


def collect(theme_names, limit=12, days=3, deep_limit=3):
    """合并快讯与深度文章：快讯按重要度排序在前，深度的陆家嘴文章置末并标记。"""
    items, errors = [], []
    deep_items = []
    try:
        deep_items = ljz_deep(theme_names, limit=deep_limit)
    except Exception as exc:  # noqa: BLE001
        errors.append("陆家嘴深度抓取失败：%s" % exc)
        print("    陆家嘴深度失败 %s" % exc)
    for name, fn in (("财联社", cls_telegraph), ("新浪财经", sina_7x24), ("同花顺快讯", ths_news),
                     ("中国政府网", gov_yaowen), ("国家发展改革委", ndrc_news)):
        try:
            got = fn()
            items.extend(got)
            print("    新闻源 %s：%d 条" % (name, len(got)))
        except Exception as exc:  # noqa: BLE001
            errors.append("%s抓取失败：%s" % (name, exc))
            print("    新闻源失败 %s %s" % (name, exc))

    raw_items = list(items)
    raw_n = len(items)
    items = [it for it in items if _relevant(it, theme_names)]
    dropped = raw_n - len(items)
    if dropped:
        print("    剔除与股市无关的要闻 %d 条" % dropped)
    # 过滤后过少时放宽：只要不是民生外事类，且命中政策主体或产业词即可
    if len(items) < 5:
        loose = [it for it in raw_items if not any(w in it["title"] for w in OFF_TOPIC)
                 and any(w in (it["title"] + it["body"])[:800] for w in MARKET_WORDS + POLICY_WORDS)]
        if len(loose) > len(items):
            print("    相关新闻过少，放宽为政策/产业口径：%d 条" % len(loose))
            items = loose
    for it in items:
        it["tags"] = _tags_from(it["title"], it["body"], theme_names)
        it["score"] = _score(it["kind"], it["level"], it["title"], it["body"])

    # 同题去重：标题前 12 字相同视为同一条
    seen, uniq = set(), []
    for it in sorted(items, key=lambda x: (-x["score"], x["time"]), reverse=False):
        key = it["title"][:12]
        if key in seen:
            continue
        seen.add(key)
        uniq.append(it)

    # 官方要闻与财联社电报各自取前若干条，避免单一来源占满首屏
    official = [x for x in uniq if x["kind"] == "official"]
    feeds = [x for x in uniq if x["kind"] != "official"]
    official.sort(key=lambda x: (-x["score"], x["time"]), reverse=False)
    feeds.sort(key=lambda x: (-x["score"], x["time"]), reverse=False)
    picked = official[:max(4, limit // 2)] + feeds[:max(4, limit - limit // 2)]
    keep = max(4, limit - len(deep_items))
    picked = picked[:keep]
    picked.sort(key=lambda x: (-x["score"], x["time"]), reverse=False)
    for i, it in enumerate(deep_items):
        it["tags"] = _tags_from(it["title"], it["body"], theme_names)
        it["score"] = 3
        it["rank_deep"] = i
    return picked + deep_items, errors


if __name__ == "__main__":
    got, errs = collect(["医药电商", "人工智能", "机器人概念"])
    print(json.dumps({"n": len(got), "errors": errs, "sample": got[:3]}, ensure_ascii=False, indent=1)[:2000])