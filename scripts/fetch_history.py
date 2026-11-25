#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抓取近 30 个交易日的涨停池与题材板块（同花顺），供梯队/题材的横向历史使用。

输出 data/raw/history.json：
{
  "generatedAt": "...",
  "dates": ["2026-10-14", ...],                       # 升序交易日
  "pools": {"2026-10-14": {"total": 36, "list": [...]}},
  "blocks": {"2026-10-14": [{"name","change","stocks":[...]}]}
}

涨停池字段精简为：c 代码、n 名称、pct 涨幅、time 首封、lbc 连板数、label 几日几板、type 涨停类型、reason 涨停原因。
"""

import json
import os
import re
import sys
import time

import requests

CST_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
          "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
THS = {"User-Agent": CST_UA, "Referer": "https://data.10jqka.com.cn/"}
FIELDS = ("199112,10,9001,330323,330324,330325,9002,330329,133971,133970,"
          "1968584,3475914,9003,9004")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data", "raw")
OUT = os.path.join(RAW, "history.json")
WANT = 30


def pick_label(obj):
    """涨停池的 high 是文本、high_days 是文本；block_top 里 high 是文本、high_days 是数字编码。"""
    for key in ("high", "high_days"):
        v = obj.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def parse_lbc(label):
    """从「5天5板」「3连板」「首板」解析连板数；接口的 continue_num / high 常为 null。"""
    label = str(label or "")
    if not label:
        return 1
    m = re.search(r"(\d+)板", label)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)连板", label)
    if m:
        return int(m.group(1))
    return 1


def board_label(label, lbc):
    """统一展示：天数等于板数时用「N连板」，否则保留「N天M板」。"""
    label = str(label or "")
    if not label or label == "首板":
        return "首板" if lbc < 2 else "%d连板" % lbc
    m = re.search(r"(\d+)天", label)
    if m and int(m.group(1)) == lbc:
        return "%d连板" % lbc
    return label


def hms(value):
    try:
        n = int(float(value))
    except Exception:  # noqa: BLE001
        return ""
    if n > 100000000:
        return time.strftime("%H:%M:%S", time.localtime(n))
    s = str(n).zfill(6)
    return "%s:%s:%s" % (s[0:2], s[2:4], s[4:6])


def calendar(days=90):
    """用腾讯指数日线取交易日序列（升序）。"""
    d = requests.get("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
                     params={"param": "sh000001,day,,,%d,qfq" % days},
                     headers={"User-Agent": "Mozilla/5.0"}, timeout=30).json()
    node = ((d.get("data") or {}).get("sh000001") or {})
    rows = node.get("qfqday") or node.get("day") or []
    return sorted(r[0] for r in rows)


def pool(date):
    r = requests.get("https://data.10jqka.com.cn/dataapi/limit_up/limit_up_pool", params={
        "page": 1, "limit": 200, "field": FIELDS, "filter": "HS,GEM2STAR",
        "order_field": "330324", "order_type": 0, "date": date.replace("-", ""),
    }, headers=THS, timeout=30)
    d = r.json()
    if d.get("status_code") not in (0, "0"):
        raise RuntimeError("涨停池返回 %s" % d.get("status_msg"))
    data = d.get("data") or {}
    items = data.get("info") or []
    out = []
    for x in items:
        raw_label = pick_label(x)
        lbc = parse_lbc(raw_label)
        out.append({
            "c": x.get("code") or "", "n": x.get("name") or "",
            "pct": round(float(x.get("change_rate") or 0), 2),
            "time": hms(x.get("first_limit_up_time")),
            "lbc": lbc,
            "label": board_label(raw_label, lbc),
            "type": x.get("limit_up_type") or "",
            "reason": x.get("reason_type") or "",
        })
    return {"total": int((data.get("page") or {}).get("total") or len(out)), "list": out}


def blocks(date):
    r = requests.get("https://data.10jqka.com.cn/dataapi/limit_up/block_top",
                     params={"filter": "HS,GEM2STAR", "date": date.replace("-", "")},
                     headers=THS, timeout=30)
    d = r.json()
    if d.get("status_code") not in (0, "0"):
        raise RuntimeError("板块返回 %s" % d.get("status_msg"))
    out = []
    for b in (d.get("data") or []):
        name = (b.get("name") or "").strip()
        if not name:
            continue
        stocks = []
        for s in (b.get("stock_list") or []):
            if not s.get("code"):
                continue
            raw_label = pick_label(s)
            lbc = parse_lbc(raw_label)
            stocks.append({
                "c": s["code"], "n": s.get("name") or "",
                "pct": round(float(s.get("change_rate") or 0), 2),
                "time": hms(s.get("first_limit_up_time")), "lbc": lbc,
                "label": board_label(raw_label, lbc),
            })
        stocks.sort(key=lambda x: (-x["lbc"], x["time"]))
        out.append({"name": name, "change": round(float(b.get("change") or 0), 2),
                    "stocks": stocks})
    return out


def main():
    cal = calendar()
    dates = cal[-WANT:]
    print("交易日 %s ~ %s 共 %d 天" % (dates[0], dates[-1], len(dates)))
    pools, blks = {}, {}
    for i, d in enumerate(dates, 1):
        for attempt in (1, 2, 3):
            try:
                pools[d] = pool(d)
                break
            except Exception as exc:  # noqa: BLE001
                if attempt == 3:
                    print("  %s 涨停池失败 %s" % (d, exc))
                    pools[d] = {"total": 0, "list": []}
                time.sleep(2)
        time.sleep(0.4)
        for attempt in (1, 2, 3):
            try:
                blks[d] = blocks(d)
                break
            except Exception as exc:  # noqa: BLE001
                if attempt == 3:
                    print("  %s 板块失败 %s" % (d, exc))
                    blks[d] = []
                time.sleep(2)
        time.sleep(0.4)
        print("  %2d/%d %s 涨停 %3d 家 · 板块 %2d 个" %
              (i, len(dates), d, pools[d]["total"], len(blks[d])))

    payload = {"generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S+08:00", time.localtime()),
               "dates": dates, "pools": pools, "blocks": blks}
    os.makedirs(RAW, exist_ok=True)
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, OUT)
    empty = [d for d in dates if not pools[d]["list"]]
    print("写入 %s（%.1f KB），无数据日：%s" % (OUT, os.path.getsize(OUT) / 1024, empty or "无"))
    return 0


if __name__ == "__main__":
    sys.exit(main())