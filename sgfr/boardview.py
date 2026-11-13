"""棋盘画布: 预渲染纹理 + 棋子层增量合成 (高性能逐帧渲染的关键)."""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from . import graphics as gx
from .theme import Theme, font, mix, shade

BLACK, WHITE = 1, 2


def hoshi_points(n: int) -> List[Tuple[int, int]]:
    if n >= 17:
        e, mid = 3, n // 2
        coords = [e, mid, n - 1 - e]
        return [(a, b) for a in coords for b in coords]
    if n >= 11:
        e, mid = 3, n // 2
        return [(e, e), (e, n - 1 - e), (n - 1 - e, e), (n - 1 - e, n - 1 - e), (mid, mid)]
    if n >= 7:
        e, mid = 2, n // 2
        return [(e, e), (e, n - 1 - e), (n - 1 - e, e), (n - 1 - e, n - 1 - e), (mid, mid)]
    return []


_COL_LABELS = "ABCDEFGHJKLMNOPQRSTUVWXYZ"


def col_label(x: int, n: int) -> str:
    """围棋坐标: 跳过 I."""
    if x < len(_COL_LABELS):
        return _COL_LABELS[x]
    return f"X{x}"


class BoardCanvas:
    """固定像素尺寸的棋盘绘制器."""

    def __init__(self, size: int, nx: int, ny: int, theme: Theme,
                 show_coords: bool = True, coords_on_board: bool = False,
                 shadow: bool = True):
        self.size = int(size)
        self.nx, self.ny = nx, ny
        self.theme = theme
        self.show_coords = show_coords
        self.coords_on_board = coords_on_board

        pad_ratio = 1.06 if show_coords else 0.52
        n = max(nx, ny)
        self.cell = self.size / (n - 1 + 2 * pad_ratio)
        self.origin_x = (self.size - (nx - 1) * self.cell) / 2.0
        self.origin_y = (self.size - (ny - 1) * self.cell) / 2.0

        self.stone_r = self.cell * 0.475
        self.shadow_margin = int(self.stone_r * 1.55)

        self.base = self._build_base()
        self.layer = Image.new("RGBA", (self.size, self.size), (0, 0, 0, 0))
        self.stones: Dict[Tuple[int, int], int] = {}
        self._sprites = {
            BLACK: gx.stone_sprite(int(round(self.stone_r)), theme.black, ss=3),
            WHITE: gx.stone_sprite(int(round(self.stone_r)), theme.white, ss=3),
        }
        self._sprite_off = [s.size[0] // 2 for s in self._sprites.values()]

    # ------------------------------------------------------------------ #
    def center(self, x: int, y: int) -> Tuple[float, float]:
        return self.origin_x + x * self.cell, self.origin_y + y * self.cell

    def hit(self, px: float, py: float) -> Tuple[int, int]:
        x = int(round((px - self.origin_x) / self.cell))
        y = int(round((py - self.origin_y) / self.cell))
        return max(0, min(self.nx - 1, x)), max(0, min(self.ny - 1, y))

    # ------------------------------------------------------------------ #
    def _build_base(self) -> Image.Image:
        """3x 超采样绘制棋盘, 缩小后得到极干净的抗锯齿边缘."""
        SS = 3
        S = self.size * SS
        st = self.theme.brd
        if st.wood:
            img = gx.wood_texture(S, S, st, seed=17)
        else:
            img = gx.flat_texture(S, S, st, seed=17)

        draw = ImageDraw.Draw(img, "RGBA")
        cell = self.cell * SS
        ox, oy = self.origin_x * SS, self.origin_y * SS
        n = max(self.nx, self.ny)

        line_w = max(2, int(round(SS * max(0.85, self.size / 1150))))
        line_rgb = tuple(int(c * 255) for c in st.line)
        a = st.line_alpha

        # 网格
        for i in range(self.nx):
            x = ox + i * cell
            draw.line([(x, oy), (x, oy + (self.ny - 1) * cell)],
                      fill=line_rgb + (a,), width=line_w)
        for j in range(self.ny):
            y = oy + j * cell
            draw.line([(ox, y), (ox + (self.nx - 1) * cell, y)],
                      fill=line_rgb + (a,), width=line_w)

        # 星位 (超采样后缩小, 避免低分辨率下变成方块)
        r_star = max(2.4, cell * 0.105)
        star_rgb = tuple(int(c * 255) for c in st.star)
        try:
            ss = 4
            rr = r_star * ss
            spr = Image.new("RGBA", (int(rr * 2) + 2, int(rr * 2) + 2), (0, 0, 0, 0))
            ImageDraw.Draw(spr).ellipse([1, 1, 1 + rr * 2, 1 + rr * 2],
                                        fill=star_rgb + (int(a * 235),))
            spr = spr.resize((max(3, int(r_star * 2) + 1), max(3, int(r_star * 2) + 1)),
                             Image.LANCZOS)
            for (hx_, hy_) in hoshi_points(self.nx if self.nx == self.ny else min(self.nx, self.ny)):
                if hx_ < self.nx and hy_ < self.ny:
                    cx, cy = ox + hx_ * cell, oy + hy_ * cell
                    img.paste(spr, (int(cx - spr.width / 2), int(cy - spr.height / 2)),
                              spr.getchannel("A"))
        except Exception:
            pass

        # 坐标
        if self.show_coords:
            lum = 0.299 * st.base[0] + 0.587 * st.base[1] + 0.114 * st.base[2]
            ccol = st.line if lum > 0.55 else st.coord
            ccol = tuple(int(c * 255) for c in ccol) + (int(a * 0.92),)
            fs = int(cell * 0.40)
            fnt = font("sans", max(8, fs))
            gap = cell * 0.56
            for i in range(self.nx):
                lab = col_label(i, self.nx)
                cx = ox + i * cell
                for cy in (oy - gap, oy + (self.ny - 1) * cell + gap + cell * 0.06):
                    draw.text((cx, cy), lab, font=fnt, fill=ccol, anchor="mm")
            for j in range(self.ny):
                lab = str(self.ny - j)
                cy = oy + j * cell
                for cx in (ox - gap, ox + (self.nx - 1) * cell + gap):
                    draw.text((cx, cy), lab, font=fnt, fill=ccol, anchor="mm")

        # 板边: 外沿压暗 + 内沿微亮, 制造厚度感
        edge = max(2, int(SS * 2.2))
        overlay = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        for k in range(edge):
            aa = int(70 * (1 - k / edge) ** 2)
            if aa <= 0:
                break
            od.rectangle([k, k, S - 1 - k, S - 1 - k], outline=(0, 0, 0, aa))
        img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")

        hi = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        hd = ImageDraw.Draw(hi)
        hd.line([(0, edge), (S - 1, edge)], fill=(255, 255, 255, 26), width=max(1, SS))
        hd.line([(edge, 0), (edge, S - 1)], fill=(255, 255, 255, 20), width=max(1, SS))
        img = Image.alpha_composite(img.convert("RGBA"), hi).convert("RGB")

        # 细边框: 让棋盘有实体边界 (浅色主题尤其需要)
        bd = ImageDraw.Draw(img, "RGBA")
        bc = tuple(int(c * 255) for c in st.border) + (170,)
        bd.rectangle([0, 0, S - 1, S - 1], outline=bc, width=max(1, int(SS * 1.6)))

        return img.resize((self.size, self.size), Image.LANCZOS)

    # ------------------------------------------------------------------ #
    def reset(self) -> None:
        self.layer = Image.new("RGBA", (self.size, self.size), (0, 0, 0, 0))
        self.stones.clear()

    def _blit(self, x: int, y: int, color: int) -> None:
        sprite = self._sprites[color]
        cx, cy = self.center(x, y)
        px = int(round(cx - sprite.size[0] / 2))
        py = int(round(cy - sprite.size[1] / 2))
        self.layer.alpha_composite(sprite, (px, py))

    def _region_of(self, x: int, y: int, grow: int = 0) -> Tuple[int, int, int, int]:
        cx, cy = self.center(x, y)
        # 贴图(含投影)可能比 stone_r + shadow_margin 更大, 用实际尺寸兜底,
        # 否则清空区域会切掉相邻棋子的边缘, 留下"半圆"残影.
        sw = max(s.size[0] for s in self._sprites.values()) / 2.0
        sh = max(s.size[1] for s in self._sprites.values()) / 2.0
        m = max(self.stone_r + self.shadow_margin, sw, sh) + grow + 1
        return (int(cx - m), int(cy - m), int(cx + m), int(cy + m))

    def _repaint(self, box: Tuple[int, int, int, int]) -> None:
        """清空 box 并重绘所有会影响该区域的棋子(闭包求解)."""
        x0, y0, x1, y1 = box
        affected = set()
        cur = box
        for _ in range(4):
            grew = False
            for (sx, sy), col in self.stones.items():
                if (sx, sy) in affected:
                    continue
                bx = self._region_of(sx, sy)
                if bx[0] < cur[2] and bx[2] > cur[0] and bx[1] < cur[3] and bx[3] > cur[1]:
                    affected.add((sx, sy))
                    cur = (min(cur[0], bx[0]), min(cur[1], bx[1]),
                           max(cur[2], bx[2]), max(cur[3], bx[3]))
                    grew = True
            if not grew:
                break
        x0, y0, x1, y1 = cur
        self.layer.paste((0, 0, 0, 0), (max(0, x0), max(0, y0),
                                        min(self.size, x1), min(self.size, y1)))
        for (sx, sy) in affected:
            self._blit(sx, sy, self.stones[(sx, sy)])

    def place(self, x: int, y: int, color: int) -> None:
        self._repaint(self._region_of(x, y))
        self.stones[(x, y)] = color
        self._blit(x, y, color)

    def remove_many(self, pts: List[Tuple[int, int]]) -> None:
        if not pts:
            return
        boxes = [self._region_of(x, y) for x, y in pts if (x, y) in self.stones]
        for p in pts:
            self.stones.pop(p, None)
        if boxes:
            self._repaint((min(b[0] for b in boxes), min(b[1] for b in boxes),
                           max(b[2] for b in boxes), max(b[3] for b in boxes)))

    def set_position(self, stones: Dict[Tuple[int, int], int]) -> None:
        self.layer = Image.new("RGBA", (self.size, self.size), (0, 0, 0, 0))
        self.stones = dict(stones)
        for (x, y), c in self.stones.items():
            self._blit(x, y, c)

    # ------------------------------------------------------------------ #
    def compose(self, with_shadow_base: Optional[Image.Image] = None,
                underlay: Optional[Image.Image] = None,
                underlay_at: Tuple[int, int] = (0, 0)) -> Image.Image:
        """合成棋盘。

        underlay 插在棋盘纹理与棋子层之间, 用于领地热力图这类
        「属于棋盘、但必须被棋子盖住」的底纹。
        """
        out = (with_shadow_base or self.base).copy().convert("RGBA")
        if underlay is not None:
            # alpha_composite 是标准 source-over, 不会把半透明色块压暗
            out.alpha_composite(underlay, dest=underlay_at)
        out.alpha_composite(self.layer)
        return out.convert("RGB")
