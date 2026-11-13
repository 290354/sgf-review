#!/usr/bin/env python3
"""生成 Windows exe 图标: 榧木棋盘 + 黑白子, 输出 assets/icon.ico.

构建脚本 (build_win.ps1) 与 GitHub Actions 都会先跑它, 保证图标随版本更新。
"""
from __future__ import annotations

import os

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "assets", "icon.ico")
S = 256


def stone(d: ImageDraw.ImageDraw, cx: float, cy: float, r: float,
          dark: bool) -> None:
    # 投影
    d.ellipse((cx - r + 3, cy - r + 5, cx + r + 3, cy + r + 5),
              fill=(0, 0, 0, 110))
    base = (20, 27, 36, 255) if dark else (245, 241, 230, 255)
    edge = (96, 122, 152, 255) if dark else (198, 192, 178, 255)
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=base,
              outline=edge, width=2)
    # 左上球面高光
    hx, hy, hr = cx - r * 0.52, cy - r * 0.60, r * 0.60
    d.ellipse((hx, hy, hx + hr, hy + hr),
              fill=(255, 255, 255, 72 if dark else 165))
    # 暖色边缘反光
    d.arc((cx - r, cy - r, cx + r, cy + r), 20, 140,
          fill=(255, 214, 160, 60 if dark else 90), width=2)


def main() -> None:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((6, 6, S - 6, S - 6), radius=52,
                        fill=(24, 33, 46, 255),
                        outline=(95, 178, 255, 255), width=5)
    d.rounded_rectangle((26, 26, S - 26, S - 26), radius=32,
                        fill=(70, 54, 36, 255))
    x0, y0, x1, y1 = 42, 42, S - 42, S - 42
    step = (x1 - x0) / 8.0
    for i in range(9):
        xx, yy = x0 + i * step, y0 + i * step
        d.line((xx, y0, xx, y1), fill=(214, 176, 112, 205), width=2)
        d.line((x0, yy, x1, yy), fill=(214, 176, 112, 205), width=2)
    for gx, gy in ((2, 2), (6, 2), (4, 4), (2, 6), (6, 6)):
        cx, cy = x0 + gx * step, y0 + gy * step
        d.ellipse((cx - 3, cy - 3, cx + 3, cy + 3), fill=(120, 88, 44, 255))
    r = step * 0.46
    stone(d, x0 + 3 * step, y0 + 3 * step, r, False)
    stone(d, x0 + 5 * step, y0 + 3 * step, r, True)
    stone(d, x0 + 4 * step, y0 + 5 * step, r, False)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    img.save(OUT, sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                         (64, 64), (128, 128), (256, 256)])
    print("图标已生成:", OUT)


if __name__ == "__main__":
    main()
