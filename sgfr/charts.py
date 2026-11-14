"""分析曲线绘制: 胜率 / 目差 / 计算量.

曲线一次性预渲染成 RGBA 图层, 每帧只做水平裁剪 + 游标, 保证 60fps 渲染不卡.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import numpy as np
from PIL import Image, ImageDraw

from .lz import AnalysisSeries
from .theme import RGB, Theme

SS = 3  # 超采样倍数


def _paste(dst: Image.Image, layer: Image.Image, xy: Tuple[int, int]) -> None:
    if dst.mode == "RGBA":
        dst.alpha_composite(layer, xy)
    else:
        dst.paste(layer, xy, layer)


def _col(c: RGB, a: int = 255) -> Tuple[int, int, int, int]:
    return (int(max(0, min(1, c[0])) * 255),
            int(max(0, min(1, c[1])) * 255),
            int(max(0, min(1, c[2])) * 255), a)


@dataclass
class ChartBox:
    x: int
    y: int
    w: int
    h: int


def _segments(values: Sequence[Optional[float]], max_gap: int = 8):
    """把带缺口的序列切成连续段; 短缺口线性插值补上."""
    out: List[List[Tuple[int, float]]] = []
    cur: List[Tuple[int, float]] = []
    gap: List[int] = []

    def flush():
        nonlocal cur, gap
        if len(cur) >= 2:
            out.append(cur)
        cur, gap = [], []

    for i, v in enumerate(values):
        if v is None:
            if cur:
                gap.append(i)
                if len(gap) > max_gap:
                    flush()
            continue
        if cur and gap:
            # 用两端点线性插值填补缺口
            i0, v0 = cur[-1]
            for j in gap:
                t = (j - i0) / max(1e-6, (i - i0))
                cur.append((j, v0 + (v - v0) * t))
        gap = []
        cur.append((i, float(v)))
    flush()
    return out


def _grad_fill(size: Tuple[int, int], color: RGB, top_a: int, bot_a: int) -> Image.Image:
    w, h = size
    a = np.linspace(top_a, bot_a, h).astype(np.uint8)[:, None]
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    rgba[..., 0] = int(color[0] * 255)
    rgba[..., 1] = int(color[1] * 255)
    rgba[..., 2] = int(color[2] * 255)
    rgba[..., 3] = a
    return Image.fromarray(rgba, "RGBA")


class CurveChart:
    """一条 (或两条) 曲线的静态图层 + 游标绘制."""

    def __init__(self, box: ChartBox, values: Sequence[Optional[float]],
                 theme: Theme, k: float = 1.0, *, kind: str = "winrate",
                 values2: Optional[Sequence[Optional[float]]] = None,
                 label: str = "", label2: str = "",
                 vmin: float = 0.0, vmax: float = 100.0,
                 baseline: float = 50.0, ticks: Sequence[float] = (),
                 show_visits: Optional[Sequence[Optional[float]]] = None,
                 lang: str = "zh",
                 move_numbers: Optional[Sequence[int]] = None,
                 key_marks: Optional[Sequence[Tuple[int, float, int]]] = None):
        self.box = box
        self.values = list(values)
        self.values2 = list(values2) if values2 else None
        self.th = theme
        self.k = k
        self.kind = kind
        self.label = label
        self.label2 = label2
        self.vmin, self.vmax = vmin, vmax
        self.baseline = baseline
        self.ticks = list(ticks)
        self.lang = lang
        self.show_visits = list(show_visits) if show_visits else None
        self.move_numbers = list(move_numbers) if move_numbers else None
        self.key_marks = list(key_marks) if key_marks else None

        self.w, self.h = max(2, int(box.w)), max(2, int(box.h))
        self.pad_l = int(27 * k)
        self.pad_r = int(10 * k)
        self.head = int(20 * k)
        self.plot_top = self.head + int(6 * k)
        self.plot_bot = self.h - int(18 * k)
        self.bg = self._build_bg()
        self.line = self._build_line()
        self.line_faint = self.line.copy()
        _a = self.line_faint.getchannel("A").point(lambda v: int(v * 0.13))
        self.line_faint.putalpha(_a)

    # ------------------------------------------------------------------ #
    def _xy(self, i: int, v: float) -> Tuple[float, float]:
        n = max(2, len(self.values))
        pw = self.w - self.pad_l - self.pad_r
        x = self.pad_l + pw * (i / (n - 1))
        t = (v - self.vmin) / max(1e-6, (self.vmax - self.vmin))
        y = self.plot_bot - t * (self.plot_bot - self.plot_top)
        return x, y

    def _build_bg(self) -> Image.Image:
        k, th = self.k, self.th
        W, H = self.w * SS, self.h * SS
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)

        # 计算量背景(极淡的面积图, 对数刻度)
        if self.show_visits:
            vis = [math.log10(max(1.0, v)) if v else None for v in self.show_visits]
            vs = [v for v in vis if v is not None]
            if vs:
                lo, hi = 0.0, max(vs) + 0.001
                pts = []
                for i, v in enumerate(vis):
                    if v is None:
                        continue
                    x = self.pad_l * SS + (W - (self.pad_l + self.pad_r) * SS) * (i / max(1, len(vis) - 1))
                    y = self.plot_bot * SS - (v - lo) / max(1e-6, hi - lo) * (self.plot_bot - self.plot_top) * SS * 0.55
                    pts.append((x, y))
                if len(pts) > 1:
                    poly = pts + [(pts[-1][0], self.plot_bot * SS), (pts[0][0], self.plot_bot * SS)]
                    d.polygon(poly, fill=_col(th.accent, 16))

        # 网格
        for t in self.ticks:
            y = self._xy(0, t)[1] * SS
            a = 34 if abs(t - self.baseline) < 1e-6 else 16
            d.line([(self.pad_l * SS, y), (W - self.pad_r * SS, y)],
                   fill=_col(th.hair, a), width=max(1, int(1 * SS)))
        # 基线
        yb = self._xy(0, self.baseline)[1] * SS
        d.line([(self.pad_l * SS, yb), (W - self.pad_r * SS, yb)],
               fill=_col(th.dim, 60), width=max(1, int(1.2 * SS)))

        # 刻度文字
        from .theme import font
        f = font("sans", max(8, int(9 * k * SS)))
        for t in self.ticks:
            y = self._xy(0, t)[1] * SS
            txt = f"{t:g}" if self.kind == "lead" else f"{t:.0f}"
            bb = d.textbbox((0, 0), txt, font=f)
            d.text((self.pad_l * SS - bb[2] - 3 * SS, y - (bb[3] - bb[1]) / 2 - bb[1]),
                   txt, font=f, fill=_col(th.faint, 180))

        # X 轴手数刻度
        if self.move_numbers:
            n = len(self.move_numbers)
            f2 = font("sans", max(8, int(8 * k * SS)))
            step = max(1, (n - 1) // 4)
            targets = list(range(0, n - 1, step))
            targets.append(n - 1)
            for i in targets:
                x, _ = self._xy(i, self.vmin)
                txt = str(self.move_numbers[i])
                d.text((x * SS, self.plot_bot * SS + 3 * SS), txt,
                       font=f2, fill=_col(th.faint, 150), anchor="ma")
        return img.resize((self.w, self.h), Image.LANCZOS)

    def _build_line(self) -> Image.Image:
        k, th = self.k, self.th
        W, H = self.w * SS, self.h * SS
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)

        main = (0.55, 0.76, 0.98) if th.dark else (0.10, 0.33, 0.58)
        alt = (0.96, 0.74, 0.35) if th.dark else (0.72, 0.45, 0.10)
        fill_c = main

        def draw(values, color, width, alpha):
            for seg in _segments(values):
                pts = [self._xy(i, v) for i, v in seg]
                pts = [(x * SS, y * SS) for x, y in pts]
                if len(pts) < 2:
                    continue
                # 填充到基线
                base_y = self._xy(0, self.baseline)[1] * SS
                poly = pts + [(pts[-1][0], base_y), (pts[0][0], base_y)]
                d.polygon(poly, fill=_col(fill_c, alpha))
                d.line(pts, fill=_col(color, 255), width=max(1, int(width * SS)), joint="curve")

        if self.values2:
            draw(self.values2, alt, max(1.0, 1.4 * k), 10)
        draw(self.values, main, max(1.4, 2.2 * k), 40)

        # 关键手参考线: 只取影响最大的三手, 极淡虚线, 不压住曲线
        if self.kind == "winrate" and self.key_marks:
            marks = sorted(self.key_marks, key=lambda m: -abs(m[1]))[:3]
            top = self.plot_top * SS
            bot = self.plot_bot * SS
            for idx, swing, _color in marks:
                if not 0 <= idx < len(self.values):
                    continue
                x = self._xy(idx, self.vmin)[0] * SS
                col = (0.92, 0.36, 0.32) if swing < 0 else (0.36, 0.80, 0.58)
                y = top
                while y < bot:
                    d.line([(x, y), (x, min(y + 2.4 * SS, bot))],
                           fill=_col(col, 64), width=max(1, int(SS * 0.8)))
                    y += 6.4 * SS

        # 关键点标记(仅胜率图)
        if self.kind == "winrate":
            for i, v in enumerate(self.values):
                if v is None:
                    continue
                x, y = self._xy(i, v)
                prev = self.values[i - 1] if i > 0 else None
                if prev is None:
                    continue
                dv = v - prev
                if abs(dv) >= 9.0:
                    big = abs(dv) >= 15.0
                    r = (max(2.6, 3.0 * k) if big else max(2.0, 2.4 * k)) * SS
                    col = (0.90, 0.36, 0.32) if dv < 0 else (0.38, 0.80, 0.58)
                    d.ellipse([x * SS - r, y * SS - r, x * SS + r, y * SS + r],
                              fill=_col(col, 240), outline=_col(th.bg.bottom, 180),
                              width=max(1, int(SS * 0.8)))
                    if big:
                        # 严重失误/好手: 底轴小三角
                        tri = 3.2 * k * SS
                        ty = self.plot_bot * SS + 2 * SS
                        cc = _col(col, 220)
                        if dv < 0:
                            d.polygon([(x * SS, ty + tri), (x * SS - tri * 0.7, ty),
                                       (x * SS + tri * 0.7, ty)], fill=cc)
                        else:
                            d.polygon([(x * SS, ty), (x * SS - tri * 0.7, ty + tri),
                                       (x * SS + tri * 0.7, ty + tri)], fill=cc)
        return img.resize((self.w, self.h), Image.LANCZOS)

    # ------------------------------------------------------------------ #
    def blit(self, img: Image.Image, cursor_x: int, alpha: float = 1.0) -> None:
        """把背景 + 已绘制到 cursor_x 的曲线贴到 img(绝对坐标)."""
        x0, y0 = int(self.box.x), int(self.box.y)
        layer = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        layer.alpha_composite(self.bg, (0, 0))
        # 完整曲线先铺一层极淡的"预告", 再叠加已经走过的实色部分
        layer.alpha_composite(self.line_faint, (0, 0))
        if cursor_x > 1:
            layer.alpha_composite(self.line.crop((0, 0, min(cursor_x, self.w), self.h)), (0, 0))
        if alpha < 1:
            a = layer.getchannel("A").point(lambda v: int(v * alpha))
            layer.putalpha(a)
        _paste(img, layer, (x0, y0))

    def draw_cursor(self, img: Image.Image, idx: int, value: Optional[float],
                    theme: Theme, k: float) -> None:
        if value is None:
            return
        x, y = self._xy(idx, value)
        ax, ay = int(self.box.x + x), int(self.box.y + y)
        top = int(self.box.y + self.plot_top)
        bot = int(self.box.y + self.plot_bot)
        mode = img.mode

        def c(col, a):
            v = _col(col, a)
            return v if mode == "RGBA" else v[:3]

        d = ImageDraw.Draw(img)
        d.line([(ax, top), (ax, bot)], fill=c(theme.text, 28), width=max(1, int(1 * k)))
        r = max(4.0, 5.2 * k)
        d.ellipse([ax - r * 2.2, ay - r * 2.2, ax + r * 2.2, ay + r * 2.2],
                  fill=c(theme.accent2, 55))
        d.ellipse([ax - r, ay - r, ax + r, ay + r],
                  fill=c(theme.accent2, 255), outline=c(theme.bg.bottom, 230),
                  width=max(1, int(1.4 * k)))


# --------------------------------------------------------------------------- #
# 复合面板
# --------------------------------------------------------------------------- #
@dataclass
class ChartSet:
    wr: Optional[CurveChart] = None
    lead: Optional[CurveChart] = None

    @property
    def active(self) -> bool:
        return self.wr is not None


def lead_range(values: Sequence[Optional[float]]) -> Tuple[float, float, List[float]]:
    vs = [v for v in values if v is not None]
    if not vs:
        return -20.0, 20.0, [-20, -10, 0, 10, 20]
    lo, hi = min(vs), max(vs)
    m = max(10.0, max(abs(lo), abs(hi)))
    m = math.ceil(m / 10.0) * 10.0
    m = min(60.0, m)
    return -m, m, [-m, -m / 2, 0, m / 2, m]


def build_chart_set(x: int, y: int, w: int, h: int, series: AnalysisSeries,
                    theme: Theme, k: float = 1.0, lang: str = "zh",
                    show_visits: bool = True, horizontal: bool = False,
                    show_lead: bool = True
                    ) -> ChartSet:
    """胜率 + 目差曲线. 默认上下叠放; 竖屏时左右并排.

    show_lead=False 时只用胜率图填满高度 (空间不够时不硬塞两张).
    """
    if series.count == 0:
        return ChartSet()
    gap = int(12 * k)
    if not show_lead:
        wr_box = ChartBox(x, y, w, h)
        lead_box = None
    elif horizontal:
        wr_w = int((w - gap) * 0.52)
        lead_w = w - gap - wr_w
        wr_box = ChartBox(x, y, wr_w, h)
        lead_box = ChartBox(x + wr_w + gap, y, lead_w, h)
    else:
        wr_h = int((h - gap) * (0.60 if h > 200 * k else 1.0))
        lead_h = h - gap - wr_h
        wr_box = ChartBox(x, y, w, wr_h)
        lead_box = ChartBox(x, y + wr_h + gap, w, lead_h) if lead_h > 40 * k else None
    zh = lang == "zh"

    visits = None
    if show_visits:
        visits = [None if p is None else float(max(1, p.playouts)) for p in series.points]

    wr = CurveChart(
        wr_box, series.black_wr, theme, k,
        kind="winrate",
        values2=series.black_wr2 if any(v is not None for v in series.black_wr2) else None,
        label=("AI 胜率" if zh else "AI winrate"),
        label2=series.engine2,
        vmin=0, vmax=100, baseline=50,
        ticks=[0, 25, 50, 75, 100],
        show_visits=visits,
        lang=lang,
        move_numbers=list(range(1, len(series.black_wr) + 1)),
        key_marks=[(m.index, m.swing, m.color) for m in series.key_moments],
    )
    lead = None
    if lead_box is not None:
        lo, hi, ticks = lead_range(series.black_lead + (series.black_lead2 or []))
        lead = CurveChart(
            lead_box, series.black_lead, theme, k,
            kind="lead",
            values2=series.black_lead2 if any(v is not None for v in series.black_lead2) else None,
            label=("目差" if zh else "Score lead"),
            vmin=lo, vmax=hi, baseline=0, ticks=ticks, lang=lang,
        )
    return ChartSet(wr, lead)
