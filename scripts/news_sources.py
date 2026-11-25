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


def _score(source_kind, level, title, body):
    score = 0
    if source_kind == "official":
        score += 3
    if level == "A":
        score += 4
    elif level == "B":
        score += 2
    hay = title + body[:200]
    score += sum(1 for w in POLICY_WORDS if w in hay)
    return score


# ------------------------------------------------------------------ 财联社
def cls_telegraph(rn=50):
    """财联社电报。接口需要先取首页 cookie（acw_tc），再按 sha1+md5 规则签名。"""
    s = requests.Session()
    s.headers.update({"User-Agent": UA_PC, "Referer": "https://www.cls.cn/",
                       "Accept": "application/json, text/plain, */*"})
    s.get("https://www.cls.cn/telegraph", timeout=15)

    def sign(params):
        raw = "&".join("%s=%s" % (k, params[k]) for k in sorted(params))
        return hashlib.md5(hashlib.sha1(raw.encode()).hexdigest().encode()).hexdigest()

    p = {"app": "CailianpressWeb", "os": "web", "sv": "8.4.6",
         "rn": str(rn), "category": "", "last_time": "", "refresh_type": "1"}
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


def collect(theme_names, limit=12, days=3):
    """合并三源，按重要度打分排序，返回待渲染的消息列表与错误信息。"""
    items, errors = [], []
    for name, fn in (("财联社", cls_telegraph), ("中国政府网", gov_yaowen), ("国家发展改革委", ndrc_news)):
        try:
            got = fn()
            items.extend(got)
            print("    新闻源 %s：%d 条" % (name, len(got)))
        except Exception as exc:  # noqa: BLE001
            errors.append("%s抓取失败：%s" % (name, exc))
            print("    新闻源失败 %s %s" % (name, exc))

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
    picked = picked[:limit]
    picked.sort(key=lambda x: (-x["score"], x["time"]), reverse=False)
    return picked, errors


if __name__ == "__main__":
    got, errs = collect(["医药电商", "人工智能", "机器人概念"])
    print(json.dumps({"n": len(got), "errors": errs, "sample": got[:3]}, ensure_ascii=False, indent=1)[:2000])