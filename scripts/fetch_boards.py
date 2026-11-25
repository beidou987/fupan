#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""东财板块行情采集：概念板块 + 行业板块的涨跌幅与涨跌家数（f104 上涨家数 / f105 下跌家数）。

用途：题材轮动页给当日题材补「板块涨幅 / 涨跌家数 / 是否行业板块」。
东财只提供当日快照，历史日期的家数无法回溯，缺口由前端显示「—」并在数据里标注。
"""
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

CST = timezone(timedelta(hours=8))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data", "raw")
HOSTS = ["https://push2delay.eastmoney.com", "https://push2.eastmoney.com"]
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
    "Referer": "https://quote.eastmoney.com/center/boardlist.html",
}


def board_page(host, fs, pn, pz=100, tries=2):
    p = {"pn": pn, "pz": pz, "po": 1, "np": 1, "fltt": 2, "invt": 2, "fid": "f3",
         "fs": fs, "fields": "f12,f14,f3,f104,f105"}
    last = None
    for i in range(tries):
        try:
            r = requests.get(host + "/api/qt/clist/get", params=p, headers=HEADERS, timeout=15)
            j = r.json()
            rows = (j.get("data") or {}).get("diff") or []
            if rows:
                return rows
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(1.2 + i)
    if last:
        print("    分页失败 pn=%d %s" % (pn, last))
    return []


def board_list(fs):
    """取板块列表（分页 100/页）：fs=m:90+t:3 概念，m:90+t:2 行业。"""
    out = []
    for host in HOSTS:
        out = []
        for pn in range(1, 12):
            rows = board_page(host, fs, pn)
            if not rows:
                break
            out.extend(rows)
            if len(rows) < 100:
                break
            time.sleep(0.6)
        if len(out) >= 50:
            print("    来源 %s，共 %d 个" % (host.split("//")[1], len(out)))
            break
    if not out:
        raise RuntimeError("板块行情获取失败：所有入口均无数据")
    return out


def main():
    day = datetime.now(CST).strftime("%Y-%m-%d")
    out = {"date": day, "fetchedAt": datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S"),
           "concept": {}, "industry": {}}
    for fs, key in (("m:90+t:3", "concept"), ("m:90+t:2", "industry")):
        rows = board_list(fs)
        for x in rows:
            name = (x.get("f14") or "").strip()
            if not name:
                continue
            out[key][name] = {
                "code": x.get("f12") or "",
                "pct": x.get("f3"),
                "up": x.get("f104"),
                "down": x.get("f105"),
            }
        print("%s 板块 %d 个" % (key, len(out[key])))
        out[key + "Count"] = len(out[key])
    os.makedirs(RAW, exist_ok=True)
    tmp = os.path.join(RAW, "board-quote.json.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False)
    os.replace(tmp, os.path.join(RAW, "board-quote.json"))
    print("写出 board-quote.json（%s，概念 %d / 行业 %d）" % (day, len(out["concept"]), len(out["industry"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
