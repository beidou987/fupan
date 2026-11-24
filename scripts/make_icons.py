#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 PWA 图标：深底 + 红色弧线 + 「盘」字。"""

import os

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "app", "icons")
FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"

BG = (11, 14, 20, 255)
RED = (226, 84, 76, 255)
SOFT = (28, 36, 50, 255)
FG = (232, 237, 245, 255)


def rounded(size, radius_ratio=0.22):
    img = Image.new("RGBA", (size, size), BG)
    d = ImageDraw.Draw(img)
    r = int(size * radius_ratio)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=r, fill=BG)
    # 顶部红色渐层弧
    for i in range(int(size * 0.30)):
        alpha = int(60 * (1 - i / (size * 0.30)))
        d.line([(0, i), (size, i)], fill=(226, 84, 76, alpha))
    # 中心圆环
    ring_w = max(2, int(size * 0.018))
    pad = int(size * 0.115)
    d.arc([pad, pad, size - pad, size - pad], start=-120, end=200, fill=RED, width=ring_w)
    return img


def glyph(img, size, maskable=False):
    d = ImageDraw.Draw(img)
    scale = 0.62 if not maskable else 0.50
    font = ImageFont.truetype(FONT, int(size * scale), index=2)
    text = "盘"
    box = d.textbbox((0, 0), text, font=font)
    w, h = box[2] - box[0], box[3] - box[1]
    x = (size - w) / 2 - box[0]
    y = (size - h) / 2 - box[1] - size * 0.01
    d.text((x, y), text, font=font, fill=FG)
    return img


def save(img, name, size):
    img.resize((size, size), Image.LANCZOS).save(os.path.join(OUT, name), "PNG", optimize=True)
    print("wrote", name)


def main():
    os.makedirs(OUT, exist_ok=True)
    base = glyph(rounded(1024), 1024)
    save(base, "icon-192.png", 192)
    save(base, "icon-512.png", 512)
    save(base, "apple-touch-icon.png", 180)

    # maskable：安全区留白 20%
    m = glyph(rounded(1024, 0.0), 1024, maskable=True)
    save(m, "maskable-512.png", 512)

    # favicon
    base.resize((48, 48), Image.LANCZOS).save(os.path.join(OUT, "favicon.png"), "PNG")


if __name__ == "__main__":
    main()