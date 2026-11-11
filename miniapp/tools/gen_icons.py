# -*- coding: utf-8 -*-
"""生成 tabBar 占位图标（81x81 PNG，透明底）。

M3 骨架阶段用简单几何图形占位；正式阶段由设计稿图标替换，
只需覆盖 miniapp/assets/tab-*.png 即可，app.json 无需改动。
"""
import math
import os

from PIL import Image, ImageDraw

NORMAL = (122, 126, 131, 255)  # #7a7e83 未选中灰
SELECT = (212, 60, 51, 255)    # #d43c33 选中红（A股红）

SIZE = 81


def base():
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    return img, ImageDraw.Draw(img)


def rr(d, box, r, fill):
    try:
        d.rounded_rectangle(box, radius=r, fill=fill)
    except AttributeError:  # 极老 Pillow 兜底
        d.rectangle(box, fill=fill)


def icon_rank(color):
    """榜单：三根柱状条"""
    img, d = base()
    rr(d, (8, 45, 24, 73), 3, color)
    rr(d, (33, 27, 49, 73), 3, color)
    rr(d, (58, 8, 74, 73), 3, color)
    return img


def icon_star(color):
    """自选：五角星"""
    img, d = base()
    cx = cy = 40.5
    R, r = 30, 12
    pts = []
    for i in range(10):
        ang = math.pi / 2 + i * math.pi / 5
        rad = R if i % 2 == 0 else r
        pts.append((cx + rad * math.cos(ang), cy - rad * math.sin(ang)))
    d.polygon(pts, fill=color)
    return img


def icon_me(color):
    """我的：人形（头 + 肩，底部自然裁切）"""
    img, d = base()
    d.ellipse((26, 10, 55, 39), fill=color)
    d.ellipse((10, 52, 71, 100), fill=color)
    return img


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    out = os.path.normpath(os.path.join(here, "..", "assets"))
    os.makedirs(out, exist_ok=True)
    for name, fn in [("tab-rank", icon_rank), ("tab-star", icon_star), ("tab-me", icon_me)]:
        fn(NORMAL).save(os.path.join(out, "%s.png" % name))
        fn(SELECT).save(os.path.join(out, "%s-on.png" % name))
    print("icons written to", os.path.abspath(out))


if __name__ == "__main__":
    main()
