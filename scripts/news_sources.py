#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""消息面新闻源：财联社电报 + 中国政府网要闻 + 国家发展改革委新闻发布。

所有源均为公开页面/公开接口，只读取标题、时间、摘要与原文链接，不做二次分发。
抓取失败不抛异常，返回该源的空列表并把原因写入 errors，由上层决定是否降级。
"""

import hashlib
import html
import json
import os
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

ANNOUNCE_DROP = ["回购", "增持计划", "减持计划", "股东减持", "股东增持", "拟增持", "拟减持",
                 "解除质押", "股份质押", "辞职", "离任", "变更会计师事务所", "业绩预告",
                 "业绩快报", "限售股", "交易异常波动", "投资者关系", "调研", "中标通知书"]
HIGH_IMPACT = ["国务院", "中共中央", "政治局", "国常会", "工业和信息化部", "工信部", "商务部",
               "国家发展改革委", "发改委", "中国人民银行", "央行", "证监会", "财政部", "能源局",
               "药监局", "网信办", "数据局", "国资委", "科技部", "国防科工局", "海关总署"]
EVENT_WORDS = ["印发", "发布", "批复", "获批", "试点", "规划", "意见", "条例", "办法", "通知",
               "首次", "首个", "突破", "量产", "全球最大", "涨价", "提价", "中标", "订单",
               "获批上市", "出口管制", "制裁", "关税", "限制", "断供", "扩产", "投产", "签署"]
OVERSEAS = ["英伟达", "OpenAI", "特斯拉", "苹果", "台积电", "ASML", "三星", "博通", "谷歌",
            "微软", "Meta", "亚马逊", "SK海力士", "美光"]

# 状态类/交易机制类概念不作为产业标签
CONCEPT_DROP = ["昨日", "连板", "首板", "涨停", "跌停", "破净", "高送转", "次新", "预增", "预亏",
                "预盈", "扭亏", "融资融券", "转融券", "沪股通", "深股通", "MSCI", "标普", "富时",
                "机构重仓", "基金重仓", "社保重仓", "QFII", "券商重仓", "汇金", "证金", "举牌",
                "股权转让", "ST", "低价股", "高股息", "百元股", "AH股", "B股", "科创板", "创业板",
                "注册制", "转债", "壳资源", "减持", "回购", "参股", "独角兽", "专精特新", "摘帽",
                "退市", "分拆", "对标", "重组", "国企改革", "央企", "地方国资", "同花顺", "含H股",
                "一季报", "三季报", "中报", "年报", "预减", "首亏", "续亏", "减亏", "送转",
                "派息", "举牌", "增持", "业绩", "龙虎榜", "同花顺指数", "成份股", "样本股"]

CONCEPT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "data", "raw", "concept-universe.json")
_CONCEPT_VOCAB = None


def concept_vocab():
    """东财概念板块清单作为产业标签词表（长名优先，避免「芯片」盖过「存储芯片」）。"""
    global _CONCEPT_VOCAB
    if _CONCEPT_VOCAB is not None:
        return _CONCEPT_VOCAB
    names = []
    try:
        with open(CONCEPT_PATH, encoding="utf-8") as fh:
            raw = json.load(fh)
        pool = list(raw.keys()) if isinstance(raw, dict) else list(raw)
        for name in pool:
            name = str(name).strip()
            if len(name) < 2 or len(name) > 10:
                continue
            if re.fullmatch(r"[A-Za-z0-9\(\)\+\-\. ]{0,2}", name):
                continue
            if any(w in name for w in CONCEPT_DROP):
                continue
            names.append(name)
    except Exception as exc:  # noqa: BLE001
        print("    概念词表加载失败 %s" % exc)
    _CONCEPT_VOCAB = sorted(set(names), key=lambda x: (-len(x), x))
    return _CONCEPT_VOCAB


POLICY_WORDS = ["国务院", "中共中央", "政治局", "国常会", "国家发展改革委", "发改委",
                "工业和信息化部", "工信部", "财政部", "中国人民银行", "央行", "证监会",
                "商务部", "国家统计局", "十五五", "规划", "条例", "意见", "通知", "试点"]


def _clean(text):
    text = html.unescape(re.sub(r"<[^>]+>", "", text or ""))
    return re.sub(r"\s+", " ", text).strip()


def _tags_from(title, body, theme_names):
    """从标题/正文提取板块标签：当日涨停题材优先，其次东财概念板块名，最多 4 个。

    只输出真实存在的板块/概念名，保证每个标签都能在行情软件里找到对应板块。
    """
    hay = (title + " " + body)[:800]
    tags = []
    for name in theme_names:
        if name and len(name) >= 2 and name in hay and name not in tags:
            tags.append(name)
        if len(tags) >= 2:
            break
    for name in concept_vocab():
        if len(tags) >= 4:
            break
        if name in tags or name not in hay:
            continue
        if any(name in t for t in tags):
            continue
        # 更长且更具体的概念命中时，替换掉已选的子串标签
        tags = [t for t in tags if not (len(t) < len(name) and t in name)]
        tags.append(name)
    return tags[:4]


def _is_stock_notice(title):
    """「某公司：回购/增持/减持/业绩预告…」类个股公告，不构成板块级消息。"""
    m = re.match(r"^[\u4e00-\u9fa5A-Za-z0-9\*]{2,10}[：:]", title)
    if not m:
        return False
    tail = title[m.end():]
    return any(w in tail for w in ANNOUNCE_DROP)


def _relevant(it, theme_names):
    """是否与股票/板块相关：先排民生外事与个股公告，再看是否命中市场或产业关键词。"""
    hay = (it["title"] + " " + it["body"])[:800]
    if any(w in it["title"] for w in OFF_TOPIC):
        return False
    if _is_stock_notice(it["title"]):
        return False
    if it.get("stock_list"):
        return True
    if any(n and len(n) >= 2 and n in hay for n in theme_names):
        return True
    return any(w in hay for w in MARKET_WORDS)


def _score(source_kind, level, title, body):
    """影响力打分：政策主体 > 事件强度 > 产业映射 > 海外映射，弱化纯海外行情与个股琐事。"""
    hay = title + body[:300]
    score = 0
    if source_kind == "official":
        score += 6
    if level == "A":
        score += 5
    elif level == "B":
        score += 3
    score += 4 * min(3, sum(1 for w in HIGH_IMPACT if w in hay))
    score += 3 * min(4, sum(1 for w in EVENT_WORDS if w in hay))
    score += 3 * min(2, sum(1 for w in OVERSEAS if w in hay))
    if any(w in hay for w in ("A股", "沪指", "上证", "深证", "创业板", "两市", "涨停", "板块")):
        score += 3
    score -= 5 * sum(1 for w in ("美股", "纳指", "道指", "标普", "美联储", "美债", "欧股") if w in hay)
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
# 陆家嘴金融网（中国金融信息中心）为 Nuxt 站点，栏目页内嵌 __NUXT_DATA__ 数据，
# 可直接取到文章标题、官方摘要、发布时间与栏目名，不必逐篇抓正文。
LJZ_COLS = [
    ("topic", "risk-alert-weekly", "风险预警", 40),
    ("topic", "xinhua-quantum", "新华量子", 14),
    ("section", "industry-tech", "产业科技", 22),
    ("section", "ai-compute", "AI算力", 18),
    ("section", "macro-policy", "宏观政策", 18),
    ("section", "finance", "财经", 12),
]
LJZ_SKIP = ["招聘", "招募", "大赛", "榜单", "活动报名", "公益", "年会", "论坛", "沙龙",
            "课程", "直播预告", "讲座", "非遗", "读书", "诗会", "书画", "演出", "健康科普",
            "文旅", "旅游", "美食", "景区", "演唱会", "亮相", "揭晓"]
LJZ_WORDS = ["A股", "上市公司", "股价", "估值", "市值", "涨停", "板块", "产业链", "业绩",
             "订单", "产能", "出货", "毛利", "融资", "并购", "回购", "减持", "增持", "IPO",
             "基金", "券商", "北向", "指数", "营收", "净利润", "出口", "库存", "价格战"]
DEEP_WORDS = ["深度", "观察", "分析", "展望", "重构", "迁移", "价值", "预警", "深一度", "透视"]
_LJZ_WRAP = {"ShallowReactive", "Reactive", "Ref", "ShallowRef", "EmptyRef"}

try:  # 站点证书链在部分机房不完整，降噪而非中断
    requests.packages.urllib3.disable_warnings()
except Exception:  # noqa: BLE001
    pass


def _nuxt_data(html_text):
    """解析 Nuxt 3 的 __NUXT_DATA__（devalue 扁平数组 + 索引引用）。"""
    m = re.search(r'<script[^>]+id="__NUXT_DATA__"[^>]*>(.*?)</script>', html_text, re.S)
    if not m:
        return None
    try:
        arr = json.loads(m.group(1))
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(arr, list):
        return None

    def deref(i, depth=0):
        if depth > 14:
            return None
        v = arr[i] if isinstance(i, int) and 0 <= i < len(arr) else i
        if isinstance(v, list):
            if v and isinstance(v[0], str) and v[0] in _LJZ_WRAP and len(v) > 1:
                return deref(v[1], depth + 1)
            return [deref(x, depth + 1) for x in v]
        if isinstance(v, dict):
            return {k: deref(i2, depth + 1) for k, i2 in v.items()}
        return v

    try:
        root = deref(1)
    except Exception:  # noqa: BLE001
        return None
    return root if isinstance(root, dict) else None


def _ljz_list(kind, slug, timeout=25):
    """取栏目页内嵌的文章列表（含 publishedAt / summary / sectionName）。"""
    r = requests.get("https://www.ljzfin.com/%s/%s" % (kind, slug),
                     headers={"User-Agent": UA_PC}, timeout=timeout, verify=False)
    root = _nuxt_data(r.text)
    if not root:
        return []
    blk = (root.get("data") or {}).get("portal-%s-%s" % (kind, slug)) or {}
    items = blk.get("items") or []
    return items if isinstance(items, list) else []


def _ljz_meta(html):
    desc = re.search(r'<meta[^>]+(?:name|property)="(?:description|og:description)"[^>]+content="([^"]{10,400})"', html)
    pub = re.search(r'<meta[^>]+property="article:published_time"[^>]+content="([^"]{10,30})"', html)
    return (_clean(desc.group(1)) if desc else "", pub.group(1) if pub else "")


def _ljz_html_fallback(timeout=25):
    """栏目数据解析失败时的兜底：按列表页链接逐篇取 meta。"""
    out = []
    for path, label in (("/topic/risk-alert-weekly", "风险预警"), ("/section/industry-tech", "产业科技")):
        try:
            r = requests.get("https://www.ljzfin.com" + path,
                             headers={"User-Agent": UA_PC}, timeout=timeout, verify=False)
        except Exception as exc:  # noqa: BLE001
            print("    陆家嘴兜底栏目失败 %s %s" % (path, exc))
            continue
        for href, inner in re.findall(r'<a[^>]+href="(/article/[^"#?]+)"[^>]*>(.*?)</a>', r.text, re.S):
            title = _clean(re.sub(r"<[^>]+>", "", inner))
            if len(title) < 8 or any(w in title for w in LJZ_SKIP):
                continue
            try:
                d = requests.get("https://www.ljzfin.com" + href,
                                 headers={"User-Agent": UA_PC}, timeout=timeout, verify=False)
                desc, pub = _ljz_meta(d.text)
            except Exception:  # noqa: BLE001
                continue
            if not pub:
                continue
            out.append({"href": href, "label": label, "title": title, "desc": desc,
                        "pub": pub[:16].replace("T", " ")})
            if len(out) >= 6:
                return out
    return out


def ljz_deep(theme_names, limit=5, max_age_days=21, now=None):
    """陆家嘴金融网深度文章：多栏目合并，按新鲜度 + 当日题材命中 + 股市相关度排序。

    排除招聘/大赛/论坛/公益/文旅等非投资内容；财经频道只保留「证券市场」子栏目。
    """
    now = now or datetime.now(CST)
    cands, seen, colstat = [], set(), []
    for kind, slug, label, weight in LJZ_COLS:
        try:
            items = _ljz_list(kind, slug)
        except Exception as exc:  # noqa: BLE001
            print("    陆家嘴栏目失败 %s/%s %s" % (kind, slug, exc))
            continue
        colstat.append("%s %d" % (label, len(items)))
        for it in items:
            aid = it.get("id") or ""
            title = (it.get("title") or "").strip()
            pub = (it.get("publishedAt") or it.get("sourcePublishedAt") or "")[:16].replace("T", " ")
            if not aid or len(title) < 8 or aid in seen:
                continue
            if any(w in title for w in LJZ_SKIP):
                continue
            sub = it.get("sectionName") or ""
            if slug == "finance":
                if sub != "证券市场":
                    continue
                if not any(w in title for w in DEEP_WORDS):
                    continue
            try:
                day = datetime.strptime(pub[:10], "%Y-%m-%d").replace(tzinfo=CST)
            except Exception:  # noqa: BLE001
                continue
            age = (now - day).days
            if age > max_age_days or age < -1:
                continue
            seen.add(aid)
            body = _clean(it.get("summary") or "")
            text = title + " " + body + " " + sub
            score = weight
            score += 30 if age <= 3 else 20 if age <= 7 else 10 if age <= 14 else 0
            hits = []
            for name in theme_names:
                name = (name or "").strip()
                if len(name) >= 2 and name not in hits and name in text:
                    hits.append(name)
            score += min(25, 10 * len(hits))
            if any(w in text for w in DEEP_WORDS):
                score += 8
            score += min(15, 4 * sum(1 for w in LJZ_WORDS if w in text))
            cands.append({
                "id": aid, "title": title, "body": body, "pub": pub,
                "col": sub if (slug == "finance" and sub) else label,
                "label": label, "score": score, "hits": hits,
                "subjects": [label] + [h for h in hits if h != label],
            })
    if colstat:
        print("    陆家嘴栏目：%s" % "、".join(colstat))

    cands.sort(key=lambda x: (-x["score"], x["pub"]), reverse=False)
    out, per_col, per_topic, titles = [], {}, {}, set()
    for c in cands:
        if len(out) >= limit:
            break
        # 每栏目最多 2 篇，避免同一频道刷屏，保证深度视角分散
        if per_col.get(c["label"], 0) >= 2:
            continue
        # 跨栏目同稿去重：报纸稿与网站稿标题仅前缀不同
        bare = re.sub(r"^【[^】]{2,10}】", "", c["title"])
        bare = re.sub(r"^(新闻分析|财经分析|深度观察|特别观察|记者观察|独家|观察|热点聚焦)\s*[|｜·:：]?\s*", "", bare)
        key = re.sub(r"[^\w\u4e00-\u9fa5]", "", bare)[:18]
        if key in titles:
            continue
        # 同一主题材最多 2 篇，避免整屏都是同一个炒作方向
        topic = c["hits"][0] if c["hits"] else "其他"
        if per_topic.get(topic, 0) >= 2:
            continue
        titles.add(key)
        per_topic[topic] = per_topic.get(topic, 0) + 1
        per_col[c["label"]] = per_col.get(c["label"], 0) + 1
        # 标题尾巴「|《风险预警》252期上新」去掉，便于阅读
        title = re.sub(r"^【[^】]{2,10}】", "", re.split(r"\s*\|\s*《风险预警》", c["title"])[0]).strip()
        out.append({
            "source": "陆家嘴金融网", "kind": "deep", "level": "", "deep": True,
            "col": c["col"], "title": title, "body": c["body"],
            "time": c["pub"], "date": c["pub"][:10],
            "url": "https://www.ljzfin.com/article/" + c["id"],
            "subjects": c["subjects"][:3],
        })
    if not out:
        print("    陆家嘴栏目数据为空，改用列表页兜底")
        for c in _ljz_html_fallback():
            if len(out) >= limit:
                break
            out.append({
                "source": "陆家嘴金融网", "kind": "deep", "level": "", "deep": True,
                "col": c["label"], "title": re.split(r"\s*\|\s*《风险预警》", c["title"])[0].strip(),
                "body": c["desc"], "time": c["pub"], "date": c["pub"][:10],
                "url": "https://www.ljzfin.com" + c["href"], "subjects": [c["label"]],
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


def collect(theme_names, limit=9, days=3, deep_limit=4):
    """合并快讯与深度文章：陆家嘴深度置顶并标记，快讯按影响力随后。

    宁缺毋滥：没有板块指向的消息、个股公告类消息一律丢弃，不为了凑条数留在列表里。
    """
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
        it["score"] += 2 * min(4, len(it["tags"]))

    # 同题去重：标题前 12 字相同视为同一条
    seen, uniq = set(), []
    for it in sorted(items, key=lambda x: (-x["score"], x["time"]), reverse=False):
        key = it["title"][:12]
        if key in seen:
            continue
        seen.add(key)
        uniq.append(it)

    # 宁缺毋滥：没有板块/概念指向的消息不进消息面
    tagged = [x for x in uniq if x["tags"]]
    if len(uniq) > len(tagged):
        print("    剔除无板块指向的消息 %d 条" % (len(uniq) - len(tagged)))

    # 全局按影响力排序，同一来源最多 3 条，避免单一源刷屏
    per_src, picked = {}, []
    feed_limit = max(3, limit - len(deep_items))
    for it in tagged:
        if len(picked) >= feed_limit:
            break
        src = it.get("source") or ""
        if per_src.get(src, 0) >= 3:
            continue
        per_src[src] = per_src.get(src, 0) + 1
        picked.append(it)

    for i, it in enumerate(deep_items):
        it["tags"] = _tags_from(it["title"], it["body"] + " " + " ".join(it.get("subjects") or []),
                                theme_names) or list(it.get("subjects") or [])[:2]
        it["score"] = 3
        it["rank_deep"] = i
    return deep_items + picked, errors


if __name__ == "__main__":
    got, errs = collect(["医药电商", "人工智能", "机器人概念"])
    print(json.dumps({"n": len(got), "errors": errs, "sample": got[:3]}, ensure_ascii=False, indent=1)[:2000])