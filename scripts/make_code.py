#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成兑换码记录。

明文码只在这里打印一次（发给客户），`app/codes.js` 里只存 SHA-256 摘要，
所以站点源码泄露也拿不到可用的码。

用法：
  python3 scripts/make_code.py 13800138000 2026-12-31            # 只打印
  python3 scripts/make_code.py 13800138000 2026-12-31 --append   # 直接写进 app/codes.js
  python3 scripts/make_code.py 13800138000 2027-03-31 --append --note "老客户续费"
  python3 scripts/make_code.py --batch 5 2026-12-31 --append     # 批量 5 个（手机号留空）

约定：
  - 一个手机号对应一个兑换码；同一手机号重复 --append 会替换旧记录。
  - 有效期含当日，格式 YYYY-MM-DD，过期即失效。
"""
import argparse
import datetime as dt
import hashlib
import os
import re
import secrets
import sys

SALT = "fupan-license-v1|"
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # 去掉 I/O/0/1，避免手抄歧义
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CODES = os.path.join(ROOT, "app", "codes.js")


def normalize(code):
    """与前端一致：去空格/连字符/下划线，转大写。"""
    return re.sub(r"[\s\-_]", "", str(code)).upper()


def digest(code):
    return hashlib.sha256((SALT + normalize(code)).encode("utf-8")).hexdigest()


def make_plain(groups=2, size=4):
    parts = ["".join(secrets.choice(ALPHABET) for _ in range(size)) for _ in range(groups)]
    return "FP-" + "-".join(parts)


def load_entries():
    if not os.path.exists(CODES):
        return []
    src = open(CODES, encoding="utf-8").read()
    out = []
    for m in re.finditer(r"\{[^{}]*\}", src):
        body = m.group(0)
        p = re.search(r'p\s*:\s*"([^"]*)"', body)
        h = re.search(r'h\s*:\s*"([^"]*)"', body)
        e = re.search(r'e\s*:\s*"([^"]*)"', body)
        n = re.search(r'n\s*:\s*"([^"]*)"', body)
        if p and h and e:
            out.append({"p": p.group(1), "h": h.group(1), "e": e.group(1), "n": n.group(1) if n else ""})
    return out


def write_entries(entries):
    lines = [
        "/* 盘感罗盘 · 兑换码表",
        " * 校验完全在浏览器内完成；这里只存兑换码的 SHA-256 摘要，不存明文码。",
        " * 新增/续期：python3 scripts/make_code.py 手机号 到期日 --append",
        " * 字段：p 手机号 / h 摘要 / e 有效期（含当日）/ n 备注",
        " */",
        "window.ACCESS_CODES = [",
    ]
    for it in sorted(entries, key=lambda x: x["p"]):
        note = (' n: "%s",' % it["n"]) if it["n"] else ""
        lines.append('  { p: "%s", h: "%s", e: "%s",%s },' % (it["p"], it["h"], it["e"], note))
    lines.append("];")
    lines.append("")
    open(CODES, "w", encoding="utf-8").write("\n".join(lines))


def check_date(value):
    try:
        dt.date.fromisoformat(value)
    except ValueError:
        sys.exit("有效期格式应为 YYYY-MM-DD：%s" % value)
    return value


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("phone", nargs="?", default="", help="11 位手机号；批量时留空")
    ap.add_argument("expire", nargs="?", default="", help="有效期 YYYY-MM-DD")
    ap.add_argument("--append", action="store_true", help="写入 app/codes.js")
    ap.add_argument("--note", default="", help="备注，例如客户名")
    ap.add_argument("--batch", type=int, default=0, help="批量生成 N 个（手机号留空，由客户自行填写时使用）")
    a = ap.parse_args()

    if a.batch:
        if not a.expire:
            sys.exit("批量生成需要给出有效期：python3 scripts/make_code.py --batch 5 2026-12-31")
        check_date(a.expire)
        entries = load_entries() if a.append else []
        rows = []
        for i in range(a.batch):
            plain = make_plain()
            rows.append(plain)
            if a.append:
                entries.append({"p": "batch-%d-%s" % (i + 1, plain), "h": digest(plain), "e": a.expire, "n": a.note})
        print("有效期 %s，共 %d 个：\n" % (a.expire, a.batch))
        for r in rows:
            print("  " + r)
        if a.append:
            write_entries(entries)
            print("\n已写入 %s" % CODES)
        return

    phone = re.sub(r"\D", "", a.phone or "")
    if not re.fullmatch(r"1\d{10}", phone):
        sys.exit("手机号应为 11 位数字：%s" % a.phone)
    check_date(a.expire)
    plain = make_plain()
    print("明文码（发给客户，只显示这一次）：%s" % plain)
    print("手机号：%s    有效期：%s" % (phone, a.expire))
    print("摘要：%s" % digest(plain))
    if a.append:
        entries = [e for e in load_entries() if e["p"] != phone]
        entries.append({"p": phone, "h": digest(plain), "e": a.expire, "n": a.note})
        write_entries(entries)
        print("已写入 %s（同手机号旧记录已替换）" % CODES)
    else:
        print('记录：  { p: "%s", h: "%s", e: "%s" },' % (phone, digest(plain), a.expire))


if __name__ == "__main__":
    main()