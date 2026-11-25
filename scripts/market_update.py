#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""复盘终端 · 每日收盘后数据更新

流程：确定最近交易日 -> 抓取 -> 构建 -> 校验 -> 原子写入 app/data.json 与 app/data.js

用法：
    python3 scripts/market_update.py                 # 在线更新到最近已收盘交易日
    python3 scripts/market_update.py --force         # 忽略收盘时间门禁
    python3 scripts/market_update.py --offline       # 不联网，复用 data/raw 现有快照重建
    python3 scripts/market_update.py --dry-run       # 只构建与校验，不写文件

失败行为：不覆盖已有数据文件，退出码非 0，并在 state/last_run.json 记录原因。
"""

import argparse
import json
import os
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import requests

CST = timezone(timedelta(hours=8))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data", "raw")
APP = os.path.join(ROOT, "app")
STATE = os.path.join(ROOT, "state")
BACKUP = os.path.join(STATE, "backup")

H = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "https://quote.eastmoney.com/",
}
S = requests.Session()
S.headers.update(H)

TENCENT = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
DEGRADED = []


def tencent_symbol(secid):
    market, code = secid.split(".", 1)
    if market == "1":
        return "sh" + code
    if market == "0":
        return "sz" + code
    return None


def tencent_kline(secid, count=60):
    """腾讯日线：返回 {YYYY-MM-DD: {close, pct, amount}}，涨跌幅按前收盘计算。"""
    sym = tencent_symbol(secid)
    if not sym:
        raise RuntimeError("腾讯行情不支持 secid=%s" % secid)
    d = get_json(TENCENT, {"param": "%s,day,,,%d,qfq" % (sym, count)})
    node = ((d.get("data") or {}).get(sym) or {})
    rows = node.get("qfqday") or node.get("day") or []
    if not rows:
        raise RuntimeError("腾讯日线为空 %s" % secid)
    series, prev = {}, None
    for p in rows:
        close = float(p[2])
        series[p[0]] = {
            "close": close,
            "pct": round((close / prev - 1) * 100, 2) if prev else 0.0,
            "amount": 0.0,
        }
        prev = close
    return series


INDEX_SECIDS = {
    "000001.SH": "1.000001",
    "399001.SZ": "0.399001",
    "399006.SZ": "0.399006",
}

EXCLUDE = [
    "融资融券", "深股通", "沪股通", "转债标的", "昨日涨停", "昨日连板", "昨日首板",
    "机构重仓", "基金重仓", "社保重仓", "QFII重仓", "证金持股", "预盈预增", "预亏预减",
    "高送转", "次新股", "破净股", "股权转让", "参股", "板块", "地区", "同花顺",
    "MSCI", "富时", "标普", "央视50", "上证50", "深证100", "中证500", "沪深300",
]

TOPIC_ALLOW = [
    "人工智能", "半导体概念", "华为概念", "芯片概念", "机器人概念", "光刻胶", "光刻机",
    "医疗器械概念", "光伏概念", "物联网", "电商概念", "虚拟现实", "储能概念", "算力概念",
    "大数据", "新材料", "小米概念", "新能源", "智能家居", "OLED", "电池", "锂电池概念",
    "文化传媒概念", "影视院线", "DeepSeek概念", "低空经济", "商业航天", "卫星导航",
    "创新药", "细胞免疫治疗", "量子计算", "超导概念", "固态电池", "稀土永磁",
    "有色金属概念", "煤炭概念", "电力", "核电", "军工", "无人驾驶", "汽车零部件",
    "数据要素", "信创", "网络安全", "消费电子", "短剧游戏", "游戏", "AIGC概念",
    "多模态AI", "AI智能体", "AI应用", "东数西算", "算力租赁", "CPO概念",
    "液冷服务器", "先进封装", "RISC-V芯片", "光通信", "6G概念", "5G概念",
    "云计算", "边缘计算", "数字货币", "跨境支付", "免税概念", "海南自贸", "一带一路",
]


# ---------------------------------------------------------------- 基础工具
def get_json(url, params, tries=3):
    last = None
    for _ in range(tries):
        try:
            r = S.get(url, params=params, timeout=20)
            if r.status_code == 200:
                return r.json()
            last = "HTTP %s" % r.status_code
        except Exception as exc:  # noqa: BLE001
            last = str(exc)
        time.sleep(0.6)
    raise RuntimeError("%s -> %s" % (url, last))


def mmdd(date):
    return date[5:10]


def md_cn(date):
    return "%d月%d日" % (int(date[5:7]), int(date[8:10]))


def excluded(name):
    return any(k in name for k in EXCLUDE)


# ---------------------------------------------------------------- 同花顺 / 腾讯
THS_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                   "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"),
    "Referer": "https://data.10jqka.com.cn/",
}
THS_FIELDS = ("199112,10,9001,330323,330324,330325,9002,330329,133971,133970,"
              "1968584,3475914,9003,9004")


def hms(value):
    """把 unix 秒或 HHMMSS 整数统一成 HH:MM:SS。"""
    if value in (None, "", 0, "0"):
        return ""
    try:
        n = int(float(value))
    except Exception:  # noqa: BLE001
        return ""
    if n > 100000000:  # unix 秒
        return datetime.fromtimestamp(n, CST).strftime("%H:%M:%S")
    s = str(n).zfill(6)
    return "%s:%s:%s" % (s[0:2], s[2:4], s[4:6])


def ths_limit_up(compact):
    """同花顺涨停池：涨停类型、涨停原因、开板次数、封单额、换手率、流通市值、几日几板。"""
    r = requests.get("https://data.10jqka.com.cn/dataapi/limit_up/limit_up_pool", params={
        "page": 1, "limit": 200, "field": THS_FIELDS, "filter": "HS,GEM2STAR",
        "order_field": "330324", "order_type": 0, "date": compact,
    }, headers=THS_HEADERS, timeout=25)
    d = r.json()
    if d.get("status_code") not in (0, "0"):
        raise RuntimeError("同花顺涨停池返回 %s" % d.get("status_msg"))
    data = d.get("data") or {}
    items = data.get("info") or []
    total = int((data.get("page") or {}).get("total") or len(items))
    return total, items


def ths_blocks(compact):
    """同花顺题材板块：板块涨跌幅 + 当日涨停成分股（含涨停原因、首封时间、几日几板）。"""
    r = requests.get("https://data.10jqka.com.cn/dataapi/limit_up/block_top", params={
        "filter": "HS,GEM2STAR", "date": compact,
    }, headers=THS_HEADERS, timeout=25)
    d = r.json()
    if d.get("status_code") not in (0, "0"):
        raise RuntimeError("同花顺板块返回 %s" % d.get("status_msg"))
    return d.get("data") or []


def _qq_sym(code):
    if code[0] in "69":
        return "sh" + code
    if code[:2] in ("43", "83", "87", "92") or code[0] == "8":
        return "bj" + code
    return "sz" + code


def tencent_snapshot(codes):
    """腾讯批量快照（每批 60 只）：开盘/最高/最低/收盘/成交额/换手/流通市值/总市值。"""
    out = {}
    codes = [c for c in codes if c]
    for i in range(0, len(codes), 60):
        batch = codes[i:i + 60]
        try:
            r = requests.get("https://qt.gtimg.cn/q=" + ",".join(_qq_sym(c) for c in batch),
                             headers={"User-Agent": H["User-Agent"], "Referer": "https://gu.qq.com/"},
                             timeout=20)
            r.encoding = "gbk"
        except Exception as exc:  # noqa: BLE001
            print("    腾讯快照失败（%s），个股开高低收留空" % exc)
            DEGRADED.append("腾讯快照不可用，个股开高低收字段留空")
            continue
        for line in r.text.split(";"):
            line = line.strip()
            if '="' not in line:
                continue
            body = line.split('="', 1)[1].rstrip('"')
            f = body.split("~")
            if len(f) < 46:
                continue

            def num(idx):
                try:
                    return float(f[idx])
                except Exception:  # noqa: BLE001
                    return None

            out[f[2]] = {
                "open": num(5), "close": num(3), "high": num(33), "low": num(34),
                "pct": num(32), "amount": round((num(37) or 0) / 1e4, 2),
                "turnover": num(38), "floatMv": num(44), "totalMv": num(45),
            }
    return out


# ---------------------------------------------------------------- 抓取
def index_klines(beg, end):
    """指数日 K：优先东方财富，失败改腾讯。返回 ({code: {date: {close, pct}}}, 交易日序列)。"""
    try:
        out, calendar = {}, set()
        for code, secid in INDEX_SECIDS.items():
            d = get_json("https://push2his.eastmoney.com/api/qt/stock/kline/get", {
                "secid": secid, "klt": 101, "fqt": 1, "beg": beg, "end": end,
                "fields1": "f1,f2,f3,f4,f5,f6",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            })
            rows = ((d.get("data") or {}).get("klines")) or []
            series = {}
            for line in rows:
                p = line.split(",")
                series[p[0]] = {
                    "close": round(float(p[2]), 2),
                    "pct": round(float(p[8]), 2),
                }
                calendar.add(p[0])
            out[code] = series
        if not calendar:
            raise RuntimeError("东财指数日线为空")
        return out, sorted(calendar)
    except Exception as exc:  # noqa: BLE001
        print("    东财指数日线不可用（%s），改用腾讯行情" % exc)
        DEGRADED.append("指数日线改由腾讯行情提供")
        out, calendar = {}, set()
        for code, secid in INDEX_SECIDS.items():
            series = tencent_kline(secid, 90)
            filtered = {k: v for k, v in series.items() if beg <= k.replace("-", "") <= end}
            out[code] = filtered
            calendar |= set(filtered)
        if not calendar:
            raise RuntimeError("腾讯指数日线也为空")
        return out, sorted(calendar)


def concept_universe():
    try:
        out = {}
        for pn in range(1, 8):
            d = get_json("https://push2.eastmoney.com/api/qt/clist/get", {
                "pn": pn, "pz": 100, "po": 1, "np": 1, "fltt": 2, "invt": 2,
                "fid": "f3", "fs": "m:90+t:3", "fields": "f12,f14,f3",
            })
            data = d.get("data") or {}
            diff = data.get("diff") or []
            if not diff:
                break
            for x in diff:
                out[x["f14"]] = x["f12"]
            if len(out) >= (data.get("total") or 0):
                break
        if not out:
            raise RuntimeError("板块列表为空")
        return out
    except Exception as exc:  # noqa: BLE001
        path = os.path.join(RAW, "concept-universe.json")
        if not os.path.exists(path):
            raise
        print("    概念板块列表不可用（%s），沿用上次快照" % exc)
        DEGRADED.append("概念板块清单沿用上次快照")
        return json.load(open(path, encoding="utf-8"))


def zt_pool(compact):
    d = get_json("https://push2ex.eastmoney.com/getTopicZTPool", {
        "ut": "7eea3edcaed734bea9cbfc24409ed989", "dpt": "wz.ztzt",
        "Pageindex": 0, "pagesize": 200, "sort": "fbt:asc", "date": compact,
    })
    data = d.get("data") or {}
    return int(data.get("tc") or 0), data.get("pool") or []


def stock_boards(item):
    code, market = item["c"], item.get("m", 0)
    try:
        d = get_json("https://push2.eastmoney.com/api/qt/slist/get", {
            "spt": 3, "fltt": 1, "invt": 2, "fields": "f12,f13,f14",
            "secid": "%s.%s" % (market, code), "pn": 1, "pz": 60, "po": 1, "np": 1,
        })
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("个股板块接口不可用：%s" % exc)
    return code, [x["f14"] for x in ((d.get("data") or {}).get("diff") or [])]


def kline(secid, beg, end):
    try:
        d = get_json("https://push2his.eastmoney.com/api/qt/stock/kline/get", {
            "secid": secid, "klt": 101, "fqt": 1, "beg": beg, "end": end,
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        })
        out = {}
        for line in (((d.get("data") or {}).get("klines")) or []):
            p = line.split(",")
            out[p[0]] = {"close": float(p[2]), "pct": float(p[8]), "amount": float(p[6])}
        if not out:
            raise RuntimeError("东财日线为空")
        return out
    except Exception:  # noqa: BLE001
        series = tencent_kline(secid, 90)
        return {k: v for k, v in series.items() if beg <= k.replace("-", "") <= end}


def billboard(date):
    rows, page = [], 1
    while True:
        d = get_json("https://datacenter-web.eastmoney.com/api/data/v1/get", {
            "reportName": "RPT_DAILYBILLBOARD_DETAILSNEW", "columns": "ALL",
            "filter": "(TRADE_DATE='%s')" % date, "pageNumber": page, "pageSize": 500,
        })
        res = d.get("result") or {}
        batch = res.get("data") or []
        rows.extend(batch)
        if page >= (res.get("pages") or 1) or not batch:
            break
        page += 1
    seen, uniq = set(), []
    for r in rows:
        code = r.get("SECURITY_CODE")
        if code in seen:
            continue
        seen.add(code)
        uniq.append({
            "code": code,
            "name": r.get("SECURITY_NAME_ABBR"),
            "net": round((r.get("BILLBOARD_NET_AMT") or 0) / 1e8, 2),
            "buy": round((r.get("BILLBOARD_BUY_AMT") or 0) / 1e8, 2),
            "sell": round((r.get("BILLBOARD_SELL_AMT") or 0) / 1e8, 2),
            "change": round(r.get("CHANGE_RATE") or 0, 2),
            "reason": r.get("EXPLANATION") or "",
        })
    uniq.sort(key=lambda x: x["net"], reverse=True)
    return {"totalNet": round(sum(x["net"] for x in uniq), 2), "rows": uniq}


# ---------------------------------------------------------------- 构建
def build(dates, raw):
    """按给定的三个交易日构建 dataset。dates 为升序 YYYY-MM-DD。"""
    pools = {d: json.load(open(os.path.join(raw, "zt-%s.json" % d), encoding="utf-8")) for d in dates}
    boards = json.load(open(os.path.join(raw, "stock-boards.json"), encoding="utf-8"))
    universe = json.load(open(os.path.join(raw, "concept-universe.json"), encoding="utf-8"))
    stock_series = json.load(open(os.path.join(raw, "stock-klines.json"), encoding="utf-8"))
    bills = json.load(open(os.path.join(raw, "billboard.json"), encoding="utf-8"))
    board_klines = {}
    bk_path = os.path.join(raw, "board-klines.json")
    if os.path.exists(bk_path):
        board_klines = json.load(open(bk_path, encoding="utf-8"))

    d0, d1, d2 = dates

    # 题材板块：优先同花顺（板块涨跌幅 + 当日涨停成分股），缺失时回退东财聚合
    def load_opt(name):
        p = os.path.join(raw, name)
        if os.path.exists(p):
            try:
                return json.load(open(p, encoding="utf-8"))
            except Exception:  # noqa: BLE001
                return {}
        return {}

    ths_blocks_raw = load_opt("ths-blocks.json")
    ths_pools_raw = load_opt("ths-limitup.json")
    snapshots = load_opt("snapshots.json")
    news_items = load_opt("news.json")

    def bad_theme(name):
        return (not name) or excluded(name) or len(name.strip()) < 2 or name.endswith("板块")

    def theme_snapshot(d):
        """当日题材 -> {pct, count, first, lian, maxLbc, stocks[]}，来源同花顺板块涨停股。"""
        rows = {}
        for b in (ths_blocks_raw.get(d) or []):
            name = (b.get("name") or "").strip()
            if bad_theme(name):
                continue
            stocks = []
            for s in (b.get("stock_list") or []):
                if not s.get("code"):
                    continue
                lbc = int(s.get("continue_num") or 1)
                stocks.append({
                    "c": s["code"], "n": s.get("name") or s["code"],
                    "pct": round(s.get("change_rate") or 0, 2),
                    "time": hms(s.get("first_limit_up_time")),
                    "lbc": lbc,
                    "label": s.get("high") or ("首板" if lbc < 2 else "%d连板" % lbc),
                    "type": s.get("limit_up_type") or "",
                    "reason": s.get("reason_type") or "",
                })
            stocks.sort(key=lambda x: (-x["lbc"], x["time"]))
            rows[name] = {
                "pct": round((b.get("change") or 0), 2),
                "count": len(stocks) or int(b.get("limit_up_num") or 0),
                "first": sum(1 for s in stocks if s["lbc"] < 2),
                "lian": sum(1 for s in stocks if s["lbc"] >= 2),
                "maxLbc": max([s["lbc"] for s in stocks] or [0]),
                "stocks": stocks,
            }
        return rows

    ths_by_day = {d: theme_snapshot(d) for d in dates}

    if ths_by_day.get(d2):
        ranked = sorted(((n, v) for n, v in ths_by_day[d2].items() if v["count"] >= 2),
                        key=lambda kv: (-kv[1]["count"], -kv[1]["maxLbc"], kv[0]))
        top = [n for n, _ in ranked[:8]]
    else:
        # 回退：东财概念板块聚合（无当日涨停股明细）
        DEGRADED.append("同花顺题材板块不可用，题材页回退东财概念聚合（无涨停股明细）")
        agg = {}
        for d in dates:
            per = {}
            for x in pools[d]["pool"]:
                for b in boards.get(x["c"], []):
                    if b not in TOPIC_ALLOW:
                        continue
                    item = per.setdefault(b, {"count": 0, "first": 0, "lian": 0, "maxLbc": 0})
                    item["count"] += 1
                    lbc = x.get("lbc") or 1
                    item["lian" if lbc >= 2 else "first"] += 1
                    item["maxLbc"] = max(item["maxLbc"], lbc)
            agg[d] = per
        ranked = sorted(((b, v) for b, v in agg[d2].items() if v["count"] >= 2),
                        key=lambda kv: (-kv[1]["count"], -kv[1]["maxLbc"], kv[0]))
        top = [b for b, _ in ranked[:8]]
        for d in dates:
            ths_by_day[d] = {}
            for b, v in (agg.get(d) or {}).items():
                ths_by_day[d][b] = {"pct": None, "count": v["count"], "first": v["first"],
                                    "lian": v["lian"], "maxLbc": v["maxLbc"], "stocks": []}

    def em_stocks(theme, d):
        """同花顺当日未给出该题材成分股时，用东财涨停池按个股概念反推（口径标 em）。"""
        out = []
        for x in (pools.get(d) or {}).get("pool", []):
            if theme not in (boards.get(x["c"]) or []):
                continue
            lbc = int(x.get("lbc") or 1)
            out.append({
                "c": x["c"], "n": x["n"], "pct": round(x.get("zdp") or 0, 2),
                "time": hms(x.get("fbt")), "lbc": lbc,
                "label": "首板" if lbc < 2 else "%d连板" % lbc,
                "type": "", "reason": "",
            })
        out.sort(key=lambda s: (-s["lbc"], s["time"]))
        return out

    sector_rows = []
    for name in top:
        row = {"name": name, "code": universe.get(name, "")}
        row["stocks"] = {}
        for d in (d1, d2):
            v = ths_by_day.get(d, {}).get(name)
            cell = {"count": None, "first": None, "lian": None, "maxLbc": None, "pct": None, "src": None}
            if v:
                cell.update({"count": v["count"], "first": v["first"], "lian": v["lian"],
                             "maxLbc": v["maxLbc"], "pct": v["pct"], "src": "ths"})
                if v["stocks"]:
                    row["stocks"][mmdd(d)] = v["stocks"]
            if not cell["src"]:
                fill = em_stocks(name, d)
                if fill:
                    cell.update({"count": len(fill),
                                 "first": sum(1 for s in fill if s["lbc"] < 2),
                                 "lian": sum(1 for s in fill if s["lbc"] >= 2),
                                 "maxLbc": max(s["lbc"] for s in fill), "src": "em"})
                    row["stocks"][mmdd(d)] = fill
            row["d" + mmdd(d)] = cell
        latest = row.get("d" + mmdd(d2)) or {}
        row.update({"pct": latest.get("pct"), "count": latest.get("count") or 0,
                    "first": latest.get("first") or 0, "lian": latest.get("lian") or 0,
                    "maxLbc": latest.get("maxLbc") or 0})
        row["hasPrev"] = bool(row.get("d" + mmdd(d1), {}).get("count"))
        sector_rows.append(row)

    # 个股详情：合并东财涨停池（成交额/总市值）+ 同花顺（类型/原因/封单/换手）+ 腾讯快照（开高低收）
    stocks_detail = {}
    # 倒序遍历：同一只股票优先保留最新交易日的详情（含腾讯快照）
    for d in reversed(dates):
        ths_map = {}
        for s in ((ths_pools_raw.get(d) or {}).get("list") or []):
            if s.get("code"):
                ths_map[s["code"]] = s
        for x in (pools.get(d) or {}).get("pool", []):
            code = x["c"]
            if code in stocks_detail:
                continue
            t = ths_map.get(code) or {}
            # 腾讯快照只对应最新交易日；历史交易日不用快照，避免开高低收串日
            snap = (snapshots.get(code) or {}) if d == d2 else {}

            def num(a, b, scale=1.0):
                v = a if a not in (None, "") else b
                try:
                    return round(float(v) / scale, 2)
                except Exception:  # noqa: BLE001
                    return None

            stocks_detail[code] = {
                "code": code,
                "name": x.get("n") or t.get("name") or code,
                "date": d,
                "pct": round(float(x.get("zdp") or t.get("change_rate") or 0), 2),
                "price": round((x.get("p") or 0) / 1000.0, 2) if x.get("p") else t.get("latest"),
                "type": t.get("limit_up_type") or "",
                "reason": t.get("reason_type") or "",
                "first": hms(t.get("first_limit_up_time")) or hms(x.get("fbt")),
                "last": hms(t.get("last_limit_up_time")) or hms(x.get("lbt")),
                "openTimes": int(t.get("open_num") if t.get("open_num") is not None else (x.get("zbc") or 0)),
                "seal": num(t.get("order_amount"), x.get("fund"), 1e8),
                "turnover": round(float(t.get("turnover_rate") or x.get("hs") or 0), 2),
                "amount": num(x.get("amount"), (snap.get("amount") or 0) * 1e8, 1e8),
                "floatMv": num(t.get("currency_value"), x.get("ltsz") or (snap.get("floatMv") or 0) * 1e8, 1e8),
                "totalMv": num(x.get("tshare"), (snap.get("totalMv") or 0) * 1e8, 1e8),
                "open": snap.get("open"), "high": snap.get("high"),
                "low": snap.get("low"), "close": snap.get("close"),
                "days": t.get("high_days") or ("%d连板" % (x.get("lbc") or 1)
                                               if (x.get("lbc") or 1) >= 2 else "首板"),
            }

    theme_of = {}
    for name, v in ths_by_day.get(d2, {}).items():
        for s in v["stocks"]:
            theme_of.setdefault(s["c"], []).append(name)
    theme_of_names = set(theme_of.keys())

    # 指数
    idx = {}
    idx_path = os.path.join(raw, "indices.json")
    if os.path.exists(idx_path):
        idx_raw = json.load(open(idx_path, encoding="utf-8"))
        for r in idx_raw.get("rows", []):
            code = r.get("thscode")
            if not code or r.get("time") not in dates:
                continue
            idx.setdefault(code, {})[r["time"]] = {
                "close": round(r["close"], 2) if r.get("close") is not None else None,
                "pct": round(r["change_ratio"], 2) if r.get("change_ratio") is not None else None,
            }
    idx_path2 = os.path.join(raw, "index-klines.json")
    if os.path.exists(idx_path2):
        for code, series in json.load(open(idx_path2, encoding="utf-8")).items():
            for d in dates:
                if d in series:
                    idx.setdefault(code, {})[d] = series[d]

    pool_by_day = {d: {x["c"]: x for x in pools[d]["pool"]} for d in dates}
    bills_map = {d: {x["code"]: x for x in (bills.get(d) or {}).get("rows", [])} for d in dates}

    stock_names = {}
    for p in pools.values():
        for x in p["pool"]:
            stock_names[x["c"]] = {"n": x["n"], "hybk": x.get("hybk") or ""}

    union = set()
    for d in dates:
        union |= set(pool_by_day[d])

    def day_cell(code, day_pool, date):
        x = day_pool.get(code)
        if x:
            lbc = x.get("lbc") or 1
            return {"t": ("%d连板" % lbc if lbc >= 2 else "首板"), "k": "state",
                    "sub": "回封" if (x.get("zbc") or 0) > 0 else ""}
        pct = stock_series.get(code, {}).get(date, {}).get("pct")
        if pct is None:
            return {"t": "—", "k": "num", "dir": "down"}
        return {"t": "%+.2f" % pct, "k": "num", "dir": "up" if pct >= 0 else "down"}

    lian, shou, duan = [], [], []
    for code in union:
        info = stock_names.get(code) or {"n": code, "hybk": ""}
        thematic = [b for b in (boards.get(code) or []) if b in TOPIC_ALLOW]
        concept_text = thematic[0] if thematic else ""
        if info["hybk"] and info["hybk"] != concept_text:
            concept_text = (concept_text + "+" + info["hybk"]) if concept_text else info["hybk"]
        row = {"name": info["n"], "code": code,
               "side": {"t": "龙虎", "c": "green"} if code in bills_map[d2] else None,
               "concepts": concept_text or "—", "boards": thematic,
               "themes": theme_of.get(code, [])}
        for d in dates:
            row["d" + mmdd(d)] = day_cell(code, pool_by_day[d], d)
        if code in pool_by_day[d2]:
            (lian if (pool_by_day[d2][code].get("lbc") or 1) >= 2 else shou).append((pool_by_day[d2][code], row))
        elif code in pool_by_day[d1]:
            duan.append((pool_by_day[d1][code], row))

    lian.sort(key=lambda kv: (-(kv[0].get("lbc") or 1), -(kv[0].get("amount") or 0)))
    shou.sort(key=lambda kv: -(kv[0].get("amount") or 0))
    duan.sort(key=lambda kv: -(kv[0].get("amount") or 0))
    max_lbc = max([(x.get("lbc") or 1) for x in pools[d2]["pool"]] or [0])

    groups = [
        {"title": "连板梯队", "stat": "%d 只 · 最高 %d 连板" % (len(lian), max_lbc), "tone": "",
         "rows": [r for _, r in lian]},
        {"title": "断板 / 炸板", "stat": "%d 只 · %s涨停未延续" % (len(duan), md_cn(d1)), "tone": "alt",
         "rows": [r for _, r in duan]},
        {"title": "首板股", "stat": "%d 只 · 首板" % len(shou), "tone": "last",
         "rows": [r for _, r in shou]},
    ]

    # 消息面：真实新闻（财联社电报 / 中国政府网 / 发改委），缺失时回退盘后速览
    briefs = []
    if news_items:
        tone_map = {"official": "brown", "cls": "red"}
        for i, it in enumerate(news_items[:12], 1):
            tags = []
            for t in (it.get("tags") or [])[:3]:
                tags.append([t, "hot" if t in theme_of_names else "concept"])
            if not tags:
                tags = [[it.get("source") or "市场消息", "concept"]]
            briefs.append({
                "id": "b%d" % i, "rank": i,
                "hot": (it.get("score") or 0) >= 4,
                "tone": tone_map.get(it.get("kind"), "dark") if i <= 3 else "dark",
                "title": it.get("title") or "",
                "tags": tags,
                "summary": it.get("body") or "",
                "time": it.get("time") or "",
                "source": it.get("source") or "",
                "url": it.get("url") or "",
            })
    if not briefs:
        DEGRADED.append("消息面新闻源不可用，回退收盘行情速览")
        rank = 1
        for d in reversed(dates):
            sh = idx.get("000001.SH", {}).get(d, {})
            sz = idx.get("399001.SZ", {}).get(d, {})
            cy = idx.get("399006.SZ", {}).get(d, {})
            p = sh.get("pct")
            tc = pools[d]["tc"]
            briefs.append({
                "id": "b%d" % rank, "rank": rank, "hot": True,
                "tone": "red" if (p or 0) > 0 else "green",
                "title": ("%s收盘：沪指%+.2f%%，涨停 %d 家" % (md_cn(d), p, tc)) if p is not None
                         else ("%s收盘：涨停 %d 家" % (md_cn(d), tc)),
                "tags": [["上证指数", "hot"], ["收盘复盘", "concept"]],
                "summary": "沪指 %s（%s%%）· 深成指 %s（%s%%）· 创业板 %s（%s%%）· 涨停 %d 家。"
                           % (sh.get("close", "—"), ("%+.2f" % p) if p is not None else "—",
                              sz.get("close", "—"), ("%+.2f" % sz["pct"]) if sz.get("pct") is not None else "—",
                              cy.get("close", "—"), ("%+.2f" % cy["pct"]) if cy.get("pct") is not None else "—", tc),
                "time": "%s 15:00" % d, "source": "收盘复盘", "url": "",
            })
            rank += 1

    now = datetime.now(CST)
    return {
        "generatedAt": now.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
        "refreshStamp": "%s 15:30" % d2,
        "dates": dates,
        "sectorDates": [mmdd(d1), mmdd(d2)],
        "indices": idx,
        "sectorRows": sector_rows,
        "stocks": stocks_detail,
        "briefs": briefs,
        "billboard": bills,
        "ladder": {"dates": [mmdd(d) for d in dates], "maxLbc": max_lbc, "groups": groups},
        "sources": [
            {"name": "东方财富", "detail": "指数日线、涨停池（成交额/总市值/连板数）、龙虎榜明细"},
            {"name": "同花顺", "detail": "题材板块与当日涨停成分股（涨停类型、涨停原因、开板次数、封单额、换手率、流通市值）"},
            {"name": "腾讯证券", "detail": "个股收盘快照（开盘/最高/最低/收盘/成交额/流通市值/总市值）"},
            {"name": "财联社 / 中国政府网 / 国家发展改革委", "detail": "消息面电报与官方要闻（标题、时间、原文链接）"},
        ],
        "gaps": [
            "板块涨跌家数与资金流接口未开通，题材页以真实首板/连板结构替代",
            "炸板池明细不可用，回封标记取自涨停池 zbc 字段",
        ],
        "dataDate": d2,
    }


# ---------------------------------------------------------------- 校验
def validate(data, dates):
    errs = []
    if len(dates) != 3:
        errs.append("交易日数量不是 3：%s" % dates)
    if not data["sectorRows"]:
        errs.append("题材板块为空")
    if len(data["sectorRows"]) < 3:
        errs.append("题材板块少于 3 个：%d" % len(data["sectorRows"]))
    idx = data["indices"]
    for code in INDEX_SECIDS:
        if not (idx.get(code) or {}).get(dates[-1], {}).get("close"):
            errs.append("指数 %s 缺少 %s 收盘" % (code, dates[-1]))
    latest_pool_total = sum(data["ladder"]["groups"][i]["stat"].split(" ")[0] for i in [])
    total_rows = sum(len(g["rows"]) for g in data["ladder"]["groups"])
    if total_rows < 20:
        errs.append("涨停梯队个股过少：%d 行（疑似当日数据未就绪）" % total_rows)
    bill = data["billboard"].get(dates[-1]) or {}
    if len(bill.get("rows") or []) < 20:
        errs.append("龙虎榜明细过少：%d 行" % len(bill.get("rows") or []))
    if not data["briefs"]:
        errs.append("消息面为空")
    if len(data.get("stocks") or {}) < 20:
        errs.append("个股详情过少：%d 只" % len(data.get("stocks") or {}))
    # 新闻源失败不阻断行情更新：只降级提示，避免一次抓取失败导致整站停更
    if not any((b.get("url") or "") for b in data["briefs"]):
        data["gaps"].append("消息面本次未取到新闻原文，展示的是收盘行情速览")
    return errs


# ---------------------------------------------------------------- 写盘
def atomic_write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def write_outputs(data, dry_run):
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    js = "window.REAL_DATA = %s;\nwindow.DATA_META = %s;\n" % (
        payload,
        json.dumps({"dataDate": data["dataDate"], "generatedAt": data["generatedAt"]},
                   ensure_ascii=False, separators=(",", ":")),
    )
    if dry_run:
        print("[dry-run] 不写文件；data.json %d 字节，data.js %d 字节" % (len(payload), len(js)))
        return
    os.makedirs(BACKUP, exist_ok=True)
    cur = os.path.join(APP, "data.js")
    if os.path.exists(cur):
        try:
            old = json.loads(open(os.path.join(APP, "data.json"), encoding="utf-8").read())
            stamp = old.get("dataDate") or "unknown"
        except Exception:  # noqa: BLE001
            stamp = "unknown"
        shutil.copy2(cur, os.path.join(BACKUP, "data-%s.js" % stamp))
        shutil.copy2(os.path.join(APP, "data.json"), os.path.join(BACKUP, "data-%s.json" % stamp))
        # 只保留最近 10 份
        backups = sorted(os.listdir(BACKUP))
        while len(backups) > 20:
            os.remove(os.path.join(BACKUP, backups.pop(0)))
    atomic_write(os.path.join(APP, "data.json"), payload)
    atomic_write(cur, js)


def record(status, detail):
    os.makedirs(STATE, exist_ok=True)
    atomic_write(os.path.join(STATE, "last_run.json"), json.dumps({
        "at": datetime.now(CST).strftime("%Y-%m-%dT%H:%M:%S+08:00"),
        "status": status,
        "detail": detail,
    }, ensure_ascii=False, indent=1))


# ---------------------------------------------------------------- 主流程
def pick_dates(calendar, now, force):
    """从指数交易日序列里取最近三个可用交易日。"""
    today = now.strftime("%Y-%m-%d")
    usable = list(calendar)
    if today in usable and not force and now.hour * 60 + now.minute < 15 * 60 + 5:
        usable.remove(today)  # 未收盘，剔除当日不完整数据
    if not usable:
        raise RuntimeError("指数日线为空，无法确定交易日")
    return usable[-3:]


def run_offline(dates):
    """复用 data/raw 重建，不联网。"""
    errs = []
    for d in dates:
        if not os.path.exists(os.path.join(RAW, "zt-%s.json" % d)):
            errs.append("缺少 zt-%s.json" % d)
    if errs:
        raise RuntimeError("离线模式缺少原始文件：" + "；".join(errs))
    return build(dates, RAW)


def run_online(dates):
    os.makedirs(RAW, exist_ok=True)
    beg = (datetime.strptime(dates[0], "%Y-%m-%d") - timedelta(days=20)).strftime("%Y%m%d")
    end = dates[-1].replace("-", "")

    print("1/8 概念板块列表")
    universe = concept_universe()
    print("    板块数 %d" % len(universe))
    atomic_write(os.path.join(RAW, "concept-universe.json"), json.dumps(universe, ensure_ascii=False))

    print("2/8 涨停池 %s" % ", ".join(dates))
    pools = {}
    for d in dates:
        tc, pool = zt_pool(d.replace("-", ""))
        pools[d] = {"tc": tc, "pool": pool}
        print("    %s 涨停 %d 家 / 明细 %d 行" % (d, tc, len(pool)))
        atomic_write(os.path.join(RAW, "zt-%s.json" % d), json.dumps(pools[d], ensure_ascii=False))

    stocks = {}
    for d, p in pools.items():
        for x in p["pool"]:
            stocks.setdefault(x["c"], {"c": x["c"], "m": x.get("m", 0), "n": x["n"]})

    print("3/8 个股所属板块 %d 只" % len(stocks))
    old_boards = {}
    bp = os.path.join(RAW, "stock-boards.json")
    if os.path.exists(bp):
        old_boards = json.load(open(bp, encoding="utf-8"))
    board_map = {}
    with ThreadPoolExecutor(max_workers=10) as ex:
        futs = {ex.submit(stock_boards, v): k for k, v in stocks.items()}
        for f in as_completed(futs):
            code = futs[f]
            try:
                _, names = f.result()
                board_map[code] = [n for n in names if n in universe and not excluded(n)]
                if not board_map[code] and old_boards.get(code):
                    board_map[code] = old_boards[code]
                    DEGRADED.append("部分个股概念板块沿用上次快照")
            except Exception as exc:  # noqa: BLE001
                print("    板块失败", code, exc)
                board_map[code] = []
    atomic_write(os.path.join(RAW, "stock-boards.json"), json.dumps(board_map, ensure_ascii=False))

    print("4/8 主题板块涨停聚合")
    agg = {}
    for d, p in pools.items():
        per = {}
        for x in p["pool"]:
            for b in board_map.get(x["c"], []):
                if b not in TOPIC_ALLOW:
                    continue
                item = per.setdefault(b, {"count": 0, "first": 0, "lian": 0, "maxLbc": 0})
                item["count"] += 1
                lbc = x.get("lbc") or 1
                item["lian" if lbc >= 2 else "first"] += 1
                item["maxLbc"] = max(item["maxLbc"], lbc)
        agg[d] = per
    ranked = sorted(((b, v) for b, v in agg[dates[-1]].items() if v["count"] >= 2),
                    key=lambda kv: (-kv[1]["count"], -kv[1]["maxLbc"]))
    top = [b for b, _ in ranked[:7]]
    print("    首位板块：%s" % (top or ["—"]))

    print("5/8 板块日线 %d 个" % len(top))
    old_bk = {}
    bkp = os.path.join(RAW, "board-klines.json")
    if os.path.exists(bkp):
        old_bk = json.load(open(bkp, encoding="utf-8"))
    board_series = dict(old_bk)
    for b in top:
        code = universe.get(b)
        try:
            board_series[b] = {"code": code, "series": kline("90." + code, beg, end)}
        except Exception as exc:  # noqa: BLE001
            print("    板块日线失败", b, exc)
            if b not in board_series:
                board_series[b] = {"code": code, "series": {}}
            DEGRADED.append("部分主题板块涨跌幅沿用上次快照")
    atomic_write(os.path.join(RAW, "board-klines.json"), json.dumps(board_series, ensure_ascii=False))

    print("6/8 个股日线 %d 只" % len(stocks))
    stock_series = {}
    with ThreadPoolExecutor(max_workers=12) as ex:
        futs = {ex.submit(kline, "%s.%s" % (v["m"], k), beg, end): k for k, v in stocks.items()}
        for f in as_completed(futs):
            code = futs[f]
            try:
                stock_series[code] = f.result()
            except Exception as exc:  # noqa: BLE001
                print("    个股日线失败", code, exc)
                stock_series[code] = {}
    atomic_write(os.path.join(RAW, "stock-klines.json"), json.dumps(stock_series, ensure_ascii=False))

    print("7/8 龙虎榜 %s" % ", ".join(dates))
    old_bills = {}
    blp = os.path.join(RAW, "billboard.json")
    if os.path.exists(blp):
        old_bills = json.load(open(blp, encoding="utf-8"))
    bills = {}
    for d in dates:
        try:
            bills[d] = billboard(d)
            print("    %s 明细 %d 行 / 合计 %s 亿元" % (d, len(bills[d]["rows"]), bills[d]["totalNet"]))
        except Exception as exc:  # noqa: BLE001
            print("    龙虎榜失败", d, exc)
            bills[d] = old_bills.get(d) or {"totalNet": 0, "rows": []}
            if old_bills.get(d):
                DEGRADED.append("部分交易日龙虎榜沿用上次快照")
    atomic_write(os.path.join(RAW, "billboard.json"), json.dumps(bills, ensure_ascii=False))

    print("8/8 指数日线")
    idx, _ = index_klines(beg, end)
    atomic_write(os.path.join(RAW, "index-klines.json"), json.dumps(idx, ensure_ascii=False))
    # 离线重建时优先读 index-klines.json；清掉旧的 iFinD 快照避免口径混用
    old_idx = os.path.join(RAW, "indices.json")
    if os.path.exists(old_idx):
        os.rename(old_idx, old_idx + ".ifind.bak")

    print("9/11 同花顺题材板块与涨停池")
    ths_blocks_raw, ths_pools_raw = {}, {}
    for d in dates:
        compact = d.replace("-", "")
        try:
            ths_pools_raw[d] = {"total": 0, "list": []}
            total, items = ths_limit_up(compact)
            ths_pools_raw[d] = {"total": total, "list": items}
            print("    %s 同花顺涨停 %d 家 / 明细 %d 行" % (d, total, len(items)))
        except Exception as exc:  # noqa: BLE001
            print("    同花顺涨停池失败", d, exc)
            DEGRADED.append("同花顺涨停池不可用（%s），个股细节字段减少" % d)
        try:
            ths_blocks_raw[d] = ths_blocks(compact)
            print("    %s 题材板块 %d 个" % (d, len(ths_blocks_raw[d])))
        except Exception as exc:  # noqa: BLE001
            ths_blocks_raw[d] = []
            print("    同花顺题材板块失败", d, exc)
            DEGRADED.append("同花顺题材板块不可用（%s），题材页回退东财聚合" % d)
    atomic_write(os.path.join(RAW, "ths-limitup.json"), json.dumps(ths_pools_raw, ensure_ascii=False))
    atomic_write(os.path.join(RAW, "ths-blocks.json"), json.dumps(ths_blocks_raw, ensure_ascii=False))

    print("10/11 腾讯个股快照")
    codes = sorted({x["c"] for p in pools.values() for x in p["pool"]})
    snaps = tencent_snapshot(codes)
    print("    快照 %d / %d 只" % (len(snaps), len(codes)))
    atomic_write(os.path.join(RAW, "snapshots.json"), json.dumps(snaps, ensure_ascii=False))

    print("11/11 消息面新闻")
    theme_names = []
    for b in (ths_blocks_raw.get(dates[-1]) or []):
        nm = (b.get("name") or "").strip()
        if nm and not excluded(nm) and not nm.endswith("板块"):
            theme_names.append(nm)
    try:
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        import news_sources  # noqa: E402
        got, nerrs = news_sources.collect(theme_names, limit=12)
        print("    消息 %d 条" % len(got))
        for e in nerrs:
            DEGRADED.append("消息面：" + e)
        atomic_write(os.path.join(RAW, "news.json"), json.dumps(got, ensure_ascii=False))
    except Exception as exc:  # noqa: BLE001
        print("    消息面抓取失败", exc)
        DEGRADED.append("消息面新闻抓取失败：%s" % exc)

    data = build(dates, RAW)
    seen = set()
    for item in DEGRADED:
        if item not in seen and item not in data["gaps"]:
            seen.add(item)
            data["gaps"].append(item)
    return data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="忽略收盘时间门禁")
    ap.add_argument("--offline", action="store_true", help="不联网，复用 data/raw 重建")
    ap.add_argument("--dry-run", action="store_true", help="只构建与校验，不写文件")
    ap.add_argument("--dates", nargs=3, help="指定三个交易日，如 2026-11-13 2026-11-16 2026-11-17")
    args = ap.parse_args()

    now = datetime.now(CST)
    try:
        if args.dates:
            dates = args.dates
        elif args.offline:
            cal = json.load(open(os.path.join(RAW, "index-klines.json"), encoding="utf-8")) if os.path.exists(
                os.path.join(RAW, "index-klines.json")) else {}
            if cal:
                calendar = sorted({d for s in cal.values() for d in s})
                dates = pick_dates(calendar, now, args.force)
            else:
                dates = ["2026-11-13", "2026-11-16", "2026-11-17"]
        else:
            beg = (now - timedelta(days=45)).strftime("%Y%m%d")
            end = now.strftime("%Y%m%d")
            _, calendar = index_klines(beg, end)
            dates = pick_dates(calendar, now, args.force)
        print("目标交易日：%s" % " ".join(dates))

        data = run_offline(dates) if args.offline else run_online(dates)
        errs = validate(data, dates)
        if errs:
            raise RuntimeError("数据校验未通过：" + "；".join(errs))
        write_outputs(data, args.dry_run)
        record("ok", {"dates": dates, "dryRun": args.dry_run})
        print("完成：%s → 指标 %d 行 / 题材 %d 个 / 梯队 %d 行 / 龙虎榜 %d 行"
              % (data["dataDate"], sum(len(g["rows"]) for g in data["ladder"]["groups"]),
                 len(data["sectorRows"]), sum(len(g["rows"]) for g in data["ladder"]["groups"]),
                 len((data["billboard"].get(data["dataDate"]) or {}).get("rows") or [])))
        return 0
    except Exception as exc:  # noqa: BLE001
        record("fail", str(exc))
        print("更新失败：%s" % exc, file=sys.stderr)
        print("已保留上一版数据文件，站点仍可用旧数据。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())