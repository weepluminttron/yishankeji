# -*- coding: utf-8 -*-
"""生成 PWA 图标（192/512 PNG）：光纤信号主题。"""
import math
import os

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "static")


def lerp(a, b, t):
    return int(a + (b - a) * t)


def make_icon(size):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # 圆角渐变底
    radius = int(size * 0.22)
    top, bottom = (14, 90, 200), (0, 160, 210)
    for y in range(size):
        t = y / size
        color = (lerp(top[0], bottom[0], t), lerp(top[1], bottom[1], t), lerp(top[2], bottom[2], t), 255)
        d.line([(0, y), (size, y)], fill=color)
    mask = Image.new("L", (size, size), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    img.putalpha(mask)
    d = ImageDraw.Draw(img)

    c = size / 2
    # 三条光纤曲线
    for amp, y0, width, color in (
        (size * 0.20, size * 0.30, size * 0.045, (255, 255, 255, 220)),
        (size * 0.14, size * 0.50, size * 0.045, (255, 255, 255, 235)),
        (size * 0.20, size * 0.70, size * 0.045, (255, 255, 255, 200)),
    ):
        pts = []
        for x in range(0, size, 2):
            t = (x - c) / (size / 2)
            y = y0 + amp * math.sin(t * math.pi * 1.6)
            pts.append((x, y))
        d.line(pts, fill=color, width=int(width), joint="curve")
    # 中心信号节点
    r = int(size * 0.09)
    d.ellipse([c - r, c - r, c + r, c + r], fill=(200, 235, 255, 255))
    r2 = int(size * 0.05)
    d.ellipse([c - r2, c - r2, c + r2, c + r2], fill=(255, 255, 255, 255))
    return img


os.makedirs(STATIC, exist_ok=True)
icon512 = make_icon(512)
icon512.save(os.path.join(STATIC, "icon-512.png"))
make_icon(192).save(os.path.join(STATIC, "icon-192.png"))
print("图标已生成：static/icon-192.png, static/icon-512.png")
