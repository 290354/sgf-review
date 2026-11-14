"""场景渲染器: 片头 / 对局 / 片尾, 自适应横竖屏."""
from __future__ import annotations

import math
import re
from collections import OrderedDict
from dataclasses import dataclass
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from . import graphics as gx
from . import lz
from .boardview import BoardCanvas, col_label
from .charts import ChartSet, build_chart_set
from .sgf import BLACK, WHITE, Mark, Move, Review
from .theme import RGB, Theme, font, mix, shade
from .timeline import Timeline


# --------------------------------------------------------------------------- #
def ease_out_cubic(p: float) -> float:
    return 1 - (1 - p) ** 3


_BRACKET_TAG = re.compile(r"[<＜][^>＞]{0,60}[>＞]")


def clean_event_name(name: str, fallback: str = "围 棋 对 局") -> str:
    """赛事名清理: 去掉 <张香槟＆夏夏＆绝艺解说> 这类括注, 压缩空白."""
    s = _BRACKET_TAG.sub(" ", name or "")
    s = re.sub(r"\s+", " ", s).strip(" -—·、,，")
    return s or fallback


def ease_in_out(p: float) -> float:
    return p * p * (3 - 2 * p)


def clamp01(v: float) -> float:
    return 0.0 if v < 0 else (1.0 if v > 1 else v)


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


# --------------------------------------------------------------------------- #
@dataclass
class Layout:
    W: int
    H: int
    mode: str
    board_size: int
    board_x: int
    board_y: int
    panel_x: int
    panel_y: int
    panel_w: int
    panel_h: int

    @classmethod
    def compute(cls, W: int, H: int) -> "Layout":
        ratio = W / max(1, H)
        if ratio >= 1.22:
            bs = int(min(H * 0.888, W * 0.548))
            bx = int(W * 0.048)
            by = int((H - bs) / 2)
            px = bx + bs + int(W * 0.036)
            pw = int(W - px - W * 0.048)
            return cls(W, H, "landscape", bs, bx, by, px, by, pw, bs)
        if ratio <= 0.92:
            bs = int(min(W * 0.905, H * 0.555))
            bx = int((W - bs) / 2)
            by = int(H * 0.103)
            py = by + bs + int(H * 0.028)
            return cls(W, H, "portrait", bs, bx, by, int(W * 0.072),
                       py, int(W * 0.856), int(H - py - H * 0.042))
        bs = int(min(W * 0.855, H * 0.575))
        bx = int((W - bs) / 2)
        by = int(H * 0.085)
        py = by + bs + int(H * 0.030)
        return cls(W, H, "square", bs, bx, by, int(W * 0.070),
                   py, int(W * 0.860), int(H - py - H * 0.040))


# --------------------------------------------------------------------------- #
RESULT_REASON = {
    "R": ("中盘胜", "Resignation"),
    "RESIGN": ("中盘胜", "Resignation"),
    "RESIGNS": ("中盘胜", "Resignation"),
    "T": ("超时胜", "Time"),
    "TIME": ("超时胜", "Time"),
    "F": ("胜（对手弃权）", "Forfeit"),
    "FORFEIT": ("胜（对手弃权）", "Forfeit"),
    "FORFEITS": ("胜（对手弃权）", "Forfeit"),
    "D": ("和局", "Draw"),
}
RESULT_MAP_ZH = {k: v[0] for k, v in RESULT_REASON.items()}


def describe_result(re: str, lang: str = "zh") -> Tuple[str, str]:
    """把各种写法的 RE[] 归一成 (主文案, 副文案), 兼容中英日多种写法."""
    if not re:
        return ("", "")
    s = re.strip().strip("\u3000")
    if not s or s in ("?", "??", "Unknown", "unknown", "N/A", "none", "None"):
        return ("", "")
    up = s.upper()
    zh = lang == "zh"

    # ---- 中文/日文直接书写的结果 ----
    if any(ch in s for ch in "黑白"):
        side = "黑" if "黑" in s or "Ｂ" in s else ("白" if "白" in s else "")
        if "中盘" in s or "提子" in s or "认输" in s:
            reason = ("中盘胜", "Resignation")
        elif "超时" in s:
            reason = ("超时胜", "Time")
        elif "和" in s or "持" in s:
            return ("和局", "Draw") if zh else ("Draw", "Jigo")
        else:
            reason = None
        if side and reason:
            return (f"{side}{reason[0]}", f"{'Black' if side == '黑' else 'White'} wins by {reason[1].lower()}") if zh \
                else (f"{'Black' if side == '黑' else 'White'} wins", reason[1])
        if side:
            return (s, "") if zh else (f"{'Black' if side == '黑' else 'White'} wins", "")

    if up in ("0", "DRAW", "JIGO", "和", "和局", "持"):
        return ("和局", "Draw") if zh else ("Draw", "Jigo")
    if up.startswith("VOID") or up.startswith("CANCEL") or up in ("?", "??"):
        return ("无效对局", "Void") if zh else ("Void", "")

    side_ch = {"B": "黑", "W": "白"}
    side_en = {"B": "Black", "W": "White"}
    if up[0] in ("B", "W"):
        side = up[0]
        if "+" not in up:
            # 只有 B / W
            return (f"{side_ch[side]}方胜", "") if zh else (f"{side_en[side]} wins", "")
        rest = up.split("+", 1)[1].strip().strip(".")
        if not rest:
            return (f"{side_ch[side]}方胜", "") if zh else (f"{side_en[side]} wins", "")
        # 认输 / 超时 / 犯规 等
        key = rest.split()[0].rstrip(".") if rest.split() else rest
        if key in RESULT_REASON:
            zh_r, en_r = RESULT_REASON[key]
            if zh:
                return (f"{side_ch[side]}{zh_r}", f"{side_en[side]} wins by {en_r.lower()}")
            return (f"{side_en[side]} wins", en_r)
        # 目数
        try:
            margin = float(rest)
            mstr = f"{margin:g}"
            if zh:
                if abs(margin - 0.5) < 1e-9:
                    return ("黑胜半目" if side == "B" else "白胜半目", f"{side_en[side]} +{mstr}")
                return (f"{side_ch[side]}胜 {mstr} 目", f"{side_en[side]} +{mstr}")
            return (f"{side_en[side]} +{mstr}", "")
        except ValueError:
            pass
        if zh:
            return (f"{side_ch[side]}胜", rest.title())
        return (f"{side_en[side]} wins", rest.title())
    return (s, "")


def describe_komi(k) -> str:
    if k is None:
        return ""
    return f"贴 {k:g} 目"


# --------------------------------------------------------------------------- #
class Renderer:
    def __init__(self, review: Review, timeline: Timeline, theme: Theme,
                 width: int = 1920, height: int = 1080, fps: int = 30,
                 show_coords: bool = True, labels: str = "recent",
                 label_count: int = 6, show_comment: bool = True,
                 lang: str = "zh", watermark: str = "",
                 subtitle_scale: float = 1.0,
                 show_analysis: bool = True,
                 show_heatmap: bool = False,
                 heatmap_strength: float = 1.0,
                 heatmap_transition: bool = True,
                 heatmap_legend: bool = True,
                 eco: bool = False,
                 ai_commentary: bool = True):
        self.rv = review
        self.tl = timeline
        self.th = theme
        self.W, self.H, self.fps = width, height, fps
        self.show_coords = show_coords
        self.labels = labels
        self.label_count = label_count
        self.show_comment = show_comment
        self.lang = lang
        self.watermark = watermark
        self.subtitle_scale = subtitle_scale
        self.series = review.series
        self.show_analysis = bool(
            show_analysis and review.series is not None and review.series.count > 0
        )
        self.ai_commentary = ai_commentary

        self.L = Layout.compute(width, height)
        self.unit = min(width, height) / 1080.0

        # --- 领地热力图状态 ---
        self.eco = bool(eco)
        self.heatmap_strength = max(0.0, min(1.5, float(heatmap_strength)))
        self.heatmap_transition = bool(heatmap_transition) and not self.eco
        self.heatmap_legend = bool(heatmap_legend)
        self._heat_has_data = False
        if self.series is not None:
            self._heat_has_data = any(
                an is not None and an.ownership for an in self.series.points)
        self.show_heatmap = bool(show_heatmap and self.show_analysis
                                 and self._heat_has_data)
        # 领地场本身是柔和渐变的, 用固定低分辨率栅格化即可 (与输出分辨率解耦)
        self._heat_k = 6 if self.eco else 10
        self._heat_cache_max = 4 if self.eco else 8
        # 过渡档数 (量化后缓存, 每帧不必重新栅格化); eco / 关闭动画时为 0
        self._heat_q = 0 if (self.eco or not self.heatmap_transition) else 6
        self._heat_layers: "OrderedDict[int, Image.Image]" = OrderedDict()
        self._heat_blends: "OrderedDict[Tuple[int, int], Image.Image]" = OrderedDict()
        self._heat_fields: Dict[int, Optional[np.ndarray]] = {}
        self._heat_palette_cache: Optional[Tuple[RGB, RGB]] = None
        self._heat_source: List[int] = []
        self._heat_res = 0
        self._heat_first_t = 0.0
        if self.show_heatmap:
            nx, ny = self.rv.info.size_x, self.rv.info.size_y
            need = nx * ny
            src, first = -1, -1
            for i, an in enumerate(self.series.points):
                ok = (an is not None and an.ownership
                      and len(an.ownership) >= need)
                if ok:
                    src = i
                    if first < 0:
                        first = i
                self._heat_source.append(src)
            if first >= 0:
                self._heat_first_t = (self.tl.slots[first].start
                                      if first < len(self.tl.slots) else 0.0)
            # 固定上限 240px: 柔和场在 4K 下也不需要更高精度, 避免栅格化
            # 随输出分辨率暴涨 (1080p 下从 480px 降到 240px 可省一半时间)
            self._heat_res = min(240, max(120, self.L.board_size // 4)) \
                if self.eco else min(300, max(160, self.L.board_size // 3))

        self.bg = gx.backdrop(width, height, theme.bg, seed=41)

        self.canvas = BoardCanvas(
            self.L.board_size, review.info.size_x, review.info.size_y, theme,
            show_coords=show_coords,
        )
        self._build_board_shadow()

        self.moves = review.moves
        self.cursor = 0
        self._sprite_cache: Dict[tuple, Image.Image] = {}
        self._label_sprites: Dict[tuple, Image.Image] = {}
        self._ghosts: List[Tuple[float, float, int, float]] = []  # (x, y, color, born_t)
        self._last_sync_t = -1.0

        self._precompute_marks()
        self.reset_canvas()   # 让子 / 摆子局: 初始化时就要有设置棋子

        self._chart_box: Optional[Tuple[float, float, float, float]] = None
        self._card_box: Optional[Tuple[float, float, float, float]] = None
        self._analysis_k = 1.0
        self.charts: ChartSet = ChartSet()
        if self.show_analysis:
            self._layout_analysis()
            self.charts = build_chart_set(
                int(self._chart_box[0]), int(self._chart_box[1]),
                int(self._chart_box[2]), int(self._chart_box[3]),
                self.series, self.th, self._analysis_k, self.lang,
                show_visits=True, horizontal=(self.L.mode == "portrait"),
                show_lead=getattr(self, "_lead_visible", True),
            )

    # ------------------------------------------------------------------ #
    def _layout_analysis(self) -> None:
        """分析图表 / AI 卡位置: 由剩余空间反推, 避免写死高度导致重叠.

        同时给整个面板定统一内边距 pad, 所有子块都从 pad 开始画.
        """
        L = self.L
        pad = max(16, int(L.panel_w * 0.026))
        self._pad = pad
        iw = max(120, L.panel_w - pad * 2)
        self._lead_visible = True
        self._list_h = 0.0

        if L.mode == "portrait":
            k = L.panel_h / 610.0
            self._analysis_k = k
            top = L.panel_y + 176 * k          # 赛事+棋手+分隔+手数
            bottom = L.panel_y + L.panel_h - 20 * k
            avail = max(72.0, bottom - top)
            card_h = min(52 * k, max(40.0, avail * 0.30))
            ch = avail - card_h - 8 * k
            if ch < 110 * k:
                self._lead_visible = False     # 竖屏空间紧: 只画胜率
            ch = max(64.0, min(ch, 150 * k))
            self._chart_box = (L.panel_x + pad, top, iw, ch)
            self._card_box = (L.panel_x + pad, top + ch + 8 * k, iw, card_h)
            self._comment_top = self._card_box[1] + card_h + 8 * k
            self._comment_h = max(36.0, bottom - self._comment_top)
            return

        k = L.panel_h / 950.0
        self._analysis_k = k
        top = L.panel_y + 184 * k
        bottom = L.panel_y + L.panel_h - 40 * k   # 进度条离底边远一些
        avail = max(110.0, bottom - top)

        # 从上到下: 图表 → AI卡 → 最近手数(3行) → 注释, 间距 12k
        gaps = 12 * k * 3
        ch = max(150.0, min(300.0, avail * 0.44))
        remaining = avail - ch - gaps
        card_h = min(88 * k, max(64.0, remaining * 0.22))
        list_h = min(78 * k, max(44.0, remaining * 0.20))
        comment_h = remaining - card_h - list_h
        if comment_h < 76:
            comment_h = 76
            list_h = remaining - card_h - 76
            if list_h < 36:
                list_h = 0.0
                comment_h = max(76.0, remaining - card_h)
        self._lead_visible = True
        if ch < 92 * k:
            self._lead_visible = False
            ch = max(92 * k, min(ch, 200 * k))
            list_h = 0.0
            comment_h = max(76.0, avail - ch - card_h - 12 * k * 2)

        self._chart_box = (L.panel_x + pad, top, iw, ch)
        self._card_box = (L.panel_x + pad, top + ch + 9 * k, iw, card_h)
        self._list_top = self._card_box[1] + card_h + 9 * k
        self._list_h = list_h
        self._comment_top = self._list_top + list_h + 9 * k
        self._comment_h = max(36.0, bottom - self._comment_top)

    def _build_board_shadow(self) -> None:
        bs = self.L.board_size
        pad = int(bs * 0.10)
        w = bs + pad * 2
        m = Image.new("L", (w, w), 0)
        d = ImageDraw.Draw(m)
        d.rounded_rectangle([pad, pad, pad + bs, pad + bs], radius=int(bs * 0.008), fill=255)
        m = m.filter(ImageFilter.GaussianBlur(bs * 0.028))
        arr = np.asarray(m, dtype=np.float32) * 0.62
        sh = Image.new("RGBA", (w, w), (0, 0, 0, 0))
        sh.putalpha(Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "L"))
        self.board_shadow = sh
        self.board_shadow_off = (self.L.board_x - pad,
                                 self.L.board_y - pad + int(bs * 0.020))

    def _precompute_marks(self) -> None:
        pass

    # ------------------------------------------------------------------ #
    # 文本精灵
    # ------------------------------------------------------------------ #
    def _sprite(self, txt: str, kind: str, size: int, color: RGB,
                spacing: float = 0.0) -> Tuple[Image.Image, int, int]:
        key = (txt, kind, size, color, round(spacing, 2))
        hit = self._sprite_cache.get(key)
        if hit is not None:
            return hit  # type: ignore
        fnt = font(kind, size)
        col = tuple(int(max(0, min(1, c)) * 255) for c in color)
        if spacing <= 0:
            bbox = fnt.getbbox(txt)
            w = max(1, bbox[2] - bbox[0])
            h = max(1, bbox[3] - bbox[1])
            img = Image.new("RGBA", (w + 2, h + 2), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            d.text((1 - bbox[0], 1 - bbox[1]), txt, font=fnt, fill=col + (255,))
            out = (img, 1, 1)
        else:
            widths = [fnt.getlength(c) for c in txt]
            total = sum(widths) + spacing * max(0, len(txt) - 1)
            bbox = fnt.getbbox(txt)
            asc = -bbox[1] if bbox[1] < 0 else 0
            w = max(1, int(math.ceil(total)))
            h = max(1, bbox[3] - bbox[1])
            img = Image.new("RGBA", (w + 2, h + 2), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            x = 1.0
            for ch, cw in zip(txt, widths):
                d.text((x, 1 - bbox[1]), ch, font=fnt, fill=col + (255,))
                x += cw + spacing
            out = (img, 1, 1)
        self._sprite_cache[key] = out
        return out

    def _text(self, img: Image.Image, xy: Tuple[float, float], txt: str,
              kind: str, size: int, color: RGB, anchor: str = "la",
              alpha: float = 1.0, spacing: float = 0.0) -> Tuple[int, int]:
        if not txt or alpha <= 0.003 or size <= 0:
            return 0, 0
        sp, ox, oy = self._sprite(txt, kind, int(size), color, spacing)
        w, h = sp.width - 2, sp.height - 2
        x, y = xy
        ha, va = anchor[0], anchor[1]
        if ha == "m":
            x -= w / 2
        elif ha == "r":
            x -= w
        if va == "m":
            y -= h / 2
        elif va == "b":
            y -= h
        x -= ox
        y -= oy
        if alpha >= 0.999:
            img.paste(sp, (int(round(x)), int(round(y))), sp.getchannel("A"))
        else:
            s2 = sp.copy()
            s2.putalpha(sp.getchannel("A").point(lambda v: int(v * alpha)))
            img.paste(s2, (int(round(x)), int(round(y))), s2.getchannel("A"))
        return w, h

    def _h(self, txt: str, kind: str, size: int, spacing: float = 0.0) -> float:
        if not txt:
            return 0.0
        return float(self._measure(txt, kind, int(size), spacing)[1])

    def _measure(self, txt: str, kind: str, size: int, spacing: float = 0.0) -> Tuple[int, int]:
        sp, _, _ = self._sprite(txt, kind, int(size), (0, 0, 0), spacing)
        return sp.width - 2, sp.height - 2

    # ------------------------------------------------------------------ #
    # 棋盘状态推进
    # ------------------------------------------------------------------ #
    def reset_canvas(self) -> None:
        self.canvas.reset()
        for x, y in self.rv.setup_black:
            self.canvas.place(x, y, BLACK)
        for x, y in self.rv.setup_white:
            self.canvas.place(x, y, WHITE)
        self.cursor = 0
        self._ghosts = []
        self._last_sync_t = -1.0

    def seek(self, idx: int) -> None:
        """直接把棋盘设为"走完 idx 手"的局面 (并行分段渲染起点用)."""
        from .board import replay
        idx = max(0, min(idx, len(self.moves)))
        b, _, _ = replay(self.rv, idx)
        stones = {}
        for y in range(b.h):
            for x in range(b.w):
                if b.grid[y][x]:
                    stones[(x, y)] = b.grid[y][x]
        self.canvas.set_position(stones)
        self.cursor = idx
        self._ghosts = []
        self._last_sync_t = -1.0

    def _sync(self, target: int) -> None:
        """把 canvas 推进/回退到"已走完 target 手"的局面."""
        target = max(0, min(target, len(self.moves)))
        if target < self.cursor:
            self.reset_canvas()
        # 只在逐手推进时给提子加淡出, 追赶多手时不产生历史残影
        single = (target - self.cursor) == 1
        while self.cursor < target:
            mv = self.moves[self.cursor]
            if not mv.is_pass and getattr(mv, "valid", True):
                self.canvas.place(mv.x, mv.y, mv.color)
                if mv.captured:
                    self.canvas.remove_many(mv.captured)
                    if single and self._last_sync_t >= 0:
                        ghost_c = WHITE if mv.color == BLACK else BLACK
                        for (gx, gy) in mv.captured:
                            self._ghosts.append((float(gx), float(gy),
                                                 ghost_c, self._last_sync_t))
            self.cursor += 1

    # ------------------------------------------------------------------ #
    # 棋子贴图 (动画用, 带缩放)
    # ------------------------------------------------------------------ #
    def _stone_layer(self, radius: float, color: int) -> Image.Image:
        key = (color, int(radius * 4))
        hit = self._label_sprites.get(key)
        if hit is not None:
            return hit
        img = gx.stone_sprite(int(round(radius)), self.th.black if color == BLACK else self.th.white, ss=3)
        self._label_sprites[key] = img
        return img

    def _resized(self, sprite: Image.Image, scale: float) -> Image.Image:
        if abs(scale - 1.0) < 0.004:
            return sprite
        w = max(2, int(round(sprite.width * scale)))
        h = max(2, int(round(sprite.height * scale)))
        return sprite.resize((w, h), Image.BICUBIC)

    # ------------------------------------------------------------------ #
    # 公共绘制
    # ------------------------------------------------------------------ #
    def _draw_captured_ghosts(self, img: Image.Image, t: float) -> None:
        """被提棋子的淡出残影: 缩小 + 变透明, 与提子音效同步."""
        CAP_DUR = 0.34
        alive: List[Tuple[float, float, int, float]] = []
        L = self.L
        for (gx, gy, gcol, born) in self._ghosts:
            age = t - born
            if age < 0:
                alive.append((gx, gy, gcol, born))
                continue
            if age >= CAP_DUR:
                continue
            alive.append((gx, gy, gcol, born))
            e = 1.0 - age / CAP_DUR          # 1 -> 0
            a = e * e
            scale = 0.55 + 0.45 * e
            radius = self.canvas.stone_r * scale
            sprite = self._stone_layer(radius, gcol)
            s = sprite
            if a < 0.995:
                s = sprite.copy()
                s.putalpha(s.getchannel("A").point(lambda v: int(v * a)))
            cx, cy = self.canvas.center(int(gx), int(gy))
            img.paste(s, (int(L.board_x + cx - s.width / 2),
                          int(L.board_y + cy - s.height / 2)),
                      s.getchannel("A"))
        self._ghosts = alive

    def _draw_board(self, img: Image.Image, board_img: Image.Image,
                    alpha: float = 1.0, scale: float = 1.0) -> None:
        L = self.L
        sh = self.board_shadow
        if alpha < 0.999:
            sh = sh.copy()
            sh.putalpha(sh.getchannel("A").point(lambda v: int(v * alpha)))
        img.paste(sh, self.board_shadow_off, sh.getchannel("A"))
        if alpha >= 0.999 and abs(scale - 1.0) < 0.002:
            img.paste(board_img, (L.board_x, L.board_y))
        else:
            w = int(round(L.board_size * scale))
            b = board_img.resize((w, w), Image.BICUBIC).convert("RGBA")
            if alpha < 0.999:
                b.putalpha(int(255 * alpha))
            cx = L.board_x + L.board_size / 2
            cy = L.board_y + L.board_size / 2
            img.paste(b, (int(cx - w / 2), int(cy - w / 2)), b.getchannel("A"))

    # ------------------------------------------------------------------ #
    def _board_scene(self, t: float, idx: int, p: float) -> Image.Image:
        L = self.L
        img = self.bg.copy()

        slot = self.tl.slots[idx] if self.tl.slots else None
        mv = self.moves[idx] if self.moves else None
        anim_p = 0.0
        if slot is not None:
            anim_p = clamp01((t - slot.start) / max(1e-6, slot.anim))

        # ---- 棋盘 (动画中的那一手不进 canvas, 单独画; 动画结束则落定) ----
        settled = idx if (mv is not None and not mv.is_pass and anim_p < 1.0
                          and getattr(mv, "valid", True)) else idx + 1
        # 时间推进: _sync 内部用这个时间戳给提子淡出记生点
        self._last_sync_t = t
        self._sync(settled)
        # 领地热力图作为「棋盘底纹」插在木纹与棋子之间:
        # 色块不会盖住棋子/手数编号, 也不会被棋子边缘切断。
        # 稳态帧下 underlay 是同一个缓存对象, 把「木纹+领地」的合成结果
        # 也缓存起来, 每帧只需合成棋子层 (与不开热力图时开销相近)。
        if self.show_heatmap:
            underlay = self._heat_frame(idx, t)
            if underlay is None:
                board_img = self.canvas.compose()
            else:
                cached = getattr(self, "_heat_base_cache", None)
                if cached is not None and cached[0] is underlay:
                    base = cached[1]
                else:
                    comp = self.canvas.base.copy().convert("RGBA")
                    comp.alpha_composite(underlay)
                    base = comp.convert("RGB")
                    self._heat_base_cache = (underlay, base)
                board_img = self.canvas.compose(with_shadow_base=base)
        else:
            board_img = self.canvas.compose()
        self._draw_board(img, board_img)

        # ---- 提子淡出 (被提棋子缩小 + 变透明) ----
        if self._ghosts:
            self._draw_captured_ghosts(img, t)

        if mv is not None and not mv.is_pass and anim_p < 1.0 and getattr(mv, "valid", True):
            bounce = 1.0 - ease_out_cubic(anim_p)
            radius = self.canvas.stone_r
            sprite = self._stone_layer(radius, mv.color)
            scale = 1.0 + 0.055 * bounce
            s = self._resized(sprite, scale)
            cx, cy = self.canvas.center(mv.x, mv.y)
            off = -radius * 0.42 * bounce
            a = clamp01(anim_p * 3.2)
            if a < 0.999:
                s = s.copy()
                s.putalpha(s.getchannel("A").point(lambda v: int(v * a)))
            img.paste(s, (int(L.board_x + cx - s.width / 2),
                          int(L.board_y + cy - s.height / 2 + off)), s.getchannel("A"))

        # ---- 落子涟漪 ----
        if mv is not None and not mv.is_pass and slot is not None:
            rt = (t - slot.start - slot.anim * 0.48) / 0.52
            if 0.0 <= rt <= 1.0:
                e = ease_out_cubic(rt)
                r = self.canvas.stone_r * (0.55 + 1.15 * e)
                a = (1.0 - rt) ** 2 * 0.42
                cxi, cyi = self.canvas.center(mv.x, mv.y)
                self._ring(img, (L.board_x + cxi, L.board_y + cyi), r, a,
                           self.th.accent, 2)

        # ---- 手数编号 / 最后手高亮 / SGF 标记 ----
        self._draw_markers(img, idx, t)

        # ---- 虚手提示 ----
        if mv is not None and mv.is_pass and slot is not None:
            a = clamp01((t - slot.start) / 0.25) * clamp01((slot.end - t) / 0.25 + 0.35)
            if a > 0.02:
                cx, cy = L.board_x + L.board_size / 2, L.board_y + L.board_size * 0.46
                txt = "虚  手" if self.lang == "zh" else "PASS"
                fs = int(28 * self.unit)
                self._text(img, (cx, cy), txt, "serif_bold", fs,
                           self.th.text, "mm", a * 0.85, 0.18 * fs)

        # ---- 提子浮字 ----
        if mv is not None and getattr(mv, "captured", None) and slot is not None:
            rt = (t - slot.start - slot.anim * 0.55) / 0.85
            if 0.0 <= rt <= 1.0:
                e = ease_out_cubic(rt)
                a = (1.0 - rt) ** 1.6
                n = len(mv.captured)
                # 浮字锚在提子质心上方
                gx = sum(p[0] for p in mv.captured) / n
                gy = sum(p[1] for p in mv.captured) / n
                cx, cy = self.canvas.center(gx, gy)
                cx += L.board_x
                cy += L.board_y - self.canvas.stone_r * (0.6 + 1.4 * e)
                txt = f"+{n}"
                fs = int(16 * self.unit)
                self._text(img, (cx, cy), txt, "sans_bold", fs,
                           self.th.accent, "mm", a, 0.04 * fs)

        # ---- AI 推荐点(下一手) ----
        if self.show_analysis:
            self._draw_ai_overlay(img, idx, t)

        # ---- 面板 ----
        self._draw_panel(img, t, idx, p, mv)

        # ---- 领地热力图图例 ----
        if self.show_heatmap and self.heatmap_legend:
            self._draw_heat_legend(img)

        # ---- 水印 ----
        if self.watermark:
            self._text(img, (L.W - L.W * 0.024, L.H - L.H * 0.026), self.watermark,
                       "sans", int(15 * self.unit), self.th.faint, "rb", 0.55)

        return img

    # ------------------------------------------------------------------ #
    # 领地热力图
    # ------------------------------------------------------------------ #
    # ownership 是「每个交叉点」的离散估值(-1 白 ~ +1 黑)。旧实现直接给每个
    # 空点画一个小圆, 结果是零散硬边点阵、贴图层又整体偏移半格、深蓝在暗色
    # 主题上几乎隐形、切手时整片硬跳变。这里改成:
    #   * 双线性插值为连续场 -> 成片的柔和领地, 而非离散圆点
    #   * 冷暖双色随主题明暗自适应, 深浅主题下双方都清晰
    #   * 相邻两手之间数值插值 + 首次出现淡入, 领地会「流动」
    #   * 作为棋盘底纹(underlay)插在木纹与棋子之间, 色块不盖棋子/编号
    #   * 低分辨率栅格化 + 量化缓存, 避免逐帧重算, 控制 CPU/内存
    # ------------------------------------------------------------------ #
    def _heat_palette(self) -> Tuple[RGB, RGB]:
        """(黑方领地, 白方领地): 冷/暖对比, 随主题明暗自适应."""
        if self._heat_palette_cache is None:
            if self.th.dark:
                cold = mix((0.26, 0.66, 1.00), self.th.accent2, 0.18)
                warm = mix((1.00, 0.70, 0.28), self.th.accent, 0.10)
            else:
                cold = mix((0.12, 0.36, 0.72), self.th.accent2, 0.14)
                warm = mix((0.86, 0.40, 0.14), self.th.accent, 0.10)
            self._heat_palette_cache = (cold, warm)
        return self._heat_palette_cache

    def _heat_field(self, src: int) -> Optional[np.ndarray]:
        """一手 ownership -> (ny, nx) 浮点场, 带缓存."""
        if src in self._heat_fields:
            return self._heat_fields[src]
        an = self.series.at(src) if (src >= 0 and self.series is not None) else None
        if an is None or not an.ownership:
            self._heat_fields[src] = None
            return None
        nx, ny = self.rv.info.size_x, self.rv.info.size_y
        if len(an.ownership) < nx * ny:
            self._heat_fields[src] = None
            return None
        arr = np.asarray(an.ownership[:nx * ny], dtype=np.float32).reshape(ny, nx)
        self._heat_fields[src] = arr
        return arr

    @staticmethod
    def _sample_grid(grid: np.ndarray, gx: np.ndarray,
                     gy: np.ndarray) -> np.ndarray:
        """双线性采样, 边界 clamp (让领地自然延伸到棋盘边缘)."""
        ny, nx = grid.shape
        xf = np.floor(gx)
        yf = np.floor(gy)
        x0 = np.clip(xf.astype(np.int32), 0, nx - 1)
        y0 = np.clip(yf.astype(np.int32), 0, ny - 1)
        x1 = np.clip(x0 + 1, 0, nx - 1)
        y1 = np.clip(y0 + 1, 0, ny - 1)
        fx = np.clip(gx - xf, 0, 1)[None, :]
        fy = np.clip(gy - yf, 0, 1)[:, None]
        a = grid[y0[:, None], x0[None, :]]
        b = grid[y0[:, None], x1[None, :]]
        c = grid[y1[:, None], x0[None, :]]
        d = grid[y1[:, None], x1[None, :]]
        return (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy

    def _rasterize_heat(self, field: np.ndarray) -> Image.Image:
        """把 (ny,nx) 场渲染成 bs×bs 的柔和 RGBA 领地层."""
        c_cold, c_warm = self._heat_palette()
        nx, ny = self.rv.info.size_x, self.rv.info.size_y
        bs = self.canvas.size
        cell = self.canvas.cell
        res = max(24, min(self._heat_res, bs))
        step = bs / float(res)
        px = (np.arange(res, dtype=np.float32) + 0.5) * step
        py = (np.arange(res, dtype=np.float32) + 0.5) * step
        gx = (px - self.canvas.origin_x) / cell
        gy = (py - self.canvas.origin_y) / cell
        v = self._sample_grid(field, gx, gy).astype(np.float32)

        # 颜色: 以 0 为中心软混合, 正负交界不出现硬边
        w = np.clip((v + 0.22) / 0.44, 0.0, 1.0)
        w = w * w * (3 - 2 * w)
        w3 = w[..., None]
        cold = np.asarray(c_cold, dtype=np.float32)[None, None, :]
        warm = np.asarray(c_warm, dtype=np.float32)[None, None, :]
        rgb = warm * (1 - w3) + cold * w3

        # alpha: |ownership| 越大越实; 中间地带留白, 自然形成势力分界
        av = np.abs(v)
        s = np.clip((av - 0.10) / 0.70, 0.0, 1.0)
        s = s * s * (3 - 2 * s)
        peak = 0.78 if self.th.dark else 0.66
        alpha = s * peak * self.heatmap_strength
        # 棋盘边缘羽化, 不铺到坐标标注区; 外扩半格覆盖到棋盘边界
        ex = np.clip(np.minimum(gx + 0.5, (nx - 0.5) - gx) / 0.55, 0, 1)[None, :]
        ey = np.clip(np.minimum(gy + 0.5, (ny - 0.5) - gy) / 0.55, 0, 1)[:, None]
        edge = np.minimum(ex, ey)
        edge = edge * edge * (3 - 2 * edge)
        alpha = np.clip(alpha * edge, 0, 1)

        arr = np.empty((res, res, 4), dtype=np.uint8)
        arr[..., :3] = np.clip(rgb * 255, 0, 255).astype(np.uint8)
        arr[..., 3] = (alpha * 255).astype(np.uint8)
        small = Image.fromarray(arr, "RGBA")
        # 低分辨率柔化后放大: BILINEAR 已足够平滑且明显快于 BICUBIC
        radius = max(0.6, res / max(nx, ny) * 0.16)
        small = small.filter(ImageFilter.GaussianBlur(radius))
        return small.resize((bs, bs), Image.BILINEAR)

    def _heat_layer(self, src: int) -> Optional[Image.Image]:
        """按数据手序缓存的静态领地层 (eco 模式直接用它)."""
        if src in self._heat_layers:
            self._heat_layers.move_to_end(src)
            return self._heat_layers[src]
        field = self._heat_field(src)
        if field is None:
            return None
        layer = self._rasterize_heat(field)
        self._heat_layers[src] = layer
        while len(self._heat_layers) > self._heat_cache_max:
            self._heat_layers.popitem(last=False)
        return layer

    def _heat_frame(self, idx: int, t: float) -> Optional[Image.Image]:
        """当前帧领地层。

        稳态(绝大多数帧)直接复用完整强度的静态层; 只在「AI 数据刚更新」
        的那一手做短暂 crossfade, 让领地是流动变化而非硬跳变, 同时避免
        整段视频的色块都被压淡。过渡结果按 (idx, 档位) 量化缓存。
        """
        if not self._heat_source:
            return None
        src = self._heat_source[min(idx, len(self._heat_source) - 1)]
        if src < 0 or t < self._heat_first_t:
            return None
        cur = self._heat_layer(src)
        if cur is None:
            return None

        Q = self._heat_q
        prev_src = (self._heat_source[idx - 1]
                    if 0 < idx <= len(self._heat_source) else -1)
        # 首次出现: 用淡入系数把整层调淡 (q=0 表示还不显示)
        appear = ease_in_out(clamp01((t - self._heat_first_t) / 0.9))
        if Q > 0 and appear < 0.999:
            key = ("in", int(round(appear * Q)))
            q = key[1]
            if q <= 0:
                return None
            if key in self._heat_blends:
                return self._heat_blends[key]
            faded = cur.copy()
            faded.putalpha(cur.getchannel("A").point(
                lambda v, f=q / float(Q): int(v * f)))
            self._heat_blends[key] = faded
            return faded

        # 数据未更新 / 关闭动画: 直接完整显示
        if Q <= 0 or prev_src == src or prev_src < 0:
            return cur
        slot = self.tl.slots[idx] if idx < len(self.tl.slots) else None
        if slot is None:
            return cur
        span = min(0.55, max(0.18, slot.dur * 0.7))
        k = ease_in_out(clamp01((t - slot.start) / span))
        q = int(round(k * Q))
        if q >= Q:
            return cur
        prev = self._heat_layer(prev_src)
        if prev is None:
            return cur
        key = (idx, q)
        if key in self._heat_blends:
            self._heat_blends.move_to_end(key)
            return self._heat_blends[key]
        layer = Image.blend(prev, cur, q / float(Q))
        self._heat_blends[key] = layer
        while len(self._heat_blends) > self._heat_cache_max * 2:
            self._heat_blends.popitem(last=False)
        return layer

    def _draw_heat_legend(self, img: Image.Image) -> None:
        """棋盘上方迷你图例: 说明冷暖两色分别代表哪一方领地."""
        L = self.L
        if L.board_y < 26:            # 空间不足就让位给棋盘, 不硬塞
            return
        bs = L.board_size
        fs = max(10, int(bs * 0.020))
        dot = max(6, int(fs * 0.72))
        gap = int(fs * 0.55)
        pad = int(fs * 0.62)
        zh = self.lang == "zh"
        lb = "黑方领地" if zh else "Black"
        lw = "白方领地" if zh else "White"
        c_cold, c_warm = self._heat_palette()
        col = tuple(int(c * 255) for c in self.th.dim)
        w = int(pad * 2 + dot * 2 + gap * 3 + fs * (len(lb) + len(lw)) * 0.92)
        h = int(fs + pad * 1.1)
        x0 = L.board_x
        y0 = L.board_y - h - max(3, int(bs * 0.008))
        d = ImageDraw.Draw(img, "RGBA")
        d.rounded_rectangle([x0, y0, x0 + w, y0 + h], radius=h * 0.34,
                            fill=(12, 16, 22, 118) if self.th.dark else (255, 253, 247, 150))
        cy = y0 + h / 2
        cx = x0 + pad
        cc = tuple(int(c * 255) for c in c_cold)
        cw = tuple(int(c * 255) for c in c_warm)
        d.ellipse([cx, cy - dot / 2, cx + dot, cy + dot / 2], fill=cc + (215,))
        cx += dot + gap * 0.6
        self._text(img, (cx, cy), lb, "sans", fs, col, "lm", 0.92)
        cx += fs * len(lb) * 0.92 + gap
        d.ellipse([cx, cy - dot / 2, cx + dot, cy + dot / 2], fill=cw + (215,))
        cx += dot + gap * 0.6
        self._text(img, (cx, cy), lw, "sans", fs, col, "lm", 0.92)

    def _draw_ai_overlay(self, img: Image.Image, idx: int, t: float) -> None:
        """当前局面下 AI 对下一手的推荐选点."""
        an = self.series.at(idx) if self.series is not None else None
        if an is None or not an.best:
            return
        slot = self.tl.slots[idx] if self.tl.slots else None
        if slot is None:
            return
        a = clamp01((t - slot.start) / 0.12) * 0.78
        if a <= 0.02:
            return
        best = [b for b in an.best if b.valid][:1]
        if not best:
            return
        top = best[0]
        for rank, bm in enumerate(reversed(best)):
            r = self.canvas.stone_r * (1.02 - rank * 0.06)
            if (bm.x, bm.y) in self.canvas.stones:
                continue
            cx, cy = self.canvas.center(bm.x, bm.y)
            cx += self.L.board_x
            cy += self.L.board_y
            col = self.th.accent2
            alpha = a * (0.55 if bm is not top else 0.95)
            self._ring(img, (cx, cy), r, alpha * 0.30, shade(col, 0.35),
                       max(2, int(3.6 * self.unit)))
            hot = mix(col, (0.55, 0.88, 1.0), 0.45)
            self._ring(img, (cx, cy), r, alpha, hot, max(2, int(2.3 * self.unit)))
            if bm is top:
                dr = max(2, int(self.canvas.stone_r * 0.15))
                dot = Image.new("RGBA", (dr * 6, dr * 6), (0, 0, 0, 0))
                ImageDraw.Draw(dot).ellipse([dr, dr, dr * 5, dr * 5],
                                            fill=tuple(int(v * 255) for v in col) + (int(235 * a),))
                img.paste(dot, (int(cx - dr * 3), int(cy - dr * 3)), dot.getchannel("A"))
                _ = r

    # ------------------------------------------------------------------ #
    def _ring(self, img: Image.Image, c: Tuple[float, float], r: float,
              alpha: float, color: RGB, width: int = 2) -> None:
        if alpha <= 0.004 or r < 1:
            return
        ss = 4
        d = int(r * 2 + width * 4) * ss
        if d > 1400:
            ss = 2
            d = int(r * 2 + width * 4) * ss
        layer = Image.new("RGBA", (d, d), (0, 0, 0, 0))
        dr = ImageDraw.Draw(layer)
        col = tuple(int(max(0, min(1, x)) * 255) for x in color) + (int(255 * alpha),)
        w = max(1, width * ss)
        dr.ellipse([w, w, d - w, d - w], outline=col, width=w)
        layer = layer.resize((d // ss, d // ss), Image.LANCZOS)
        img.paste(layer, (int(c[0] - layer.width / 2), int(c[1] - layer.height / 2)),
                  layer.getchannel("A"))

    # ------------------------------------------------------------------ #
    def _draw_markers(self, img: Image.Image, idx: int, t: float) -> None:
        L = self.L
        r = self.canvas.stone_r
        mv = self.moves[idx] if self.moves else None
        if mv is None:
            return

        # 1) 最近 N 手编号
        if self.labels in ("all", "recent"):
            start = 0 if self.labels == "all" else max(0, idx - self.label_count + 1)
            for i in range(start, idx + 1):
                m = self.moves[i]
                if m.is_pass or not getattr(m, "valid", True):
                    continue
                if self.canvas.stones.get((m.x, m.y)) != m.color:
                    continue
                age = idx - i
                a = 1.0 if age <= 1 else max(0.58, 0.92 - age * 0.07)
                cx, cy = self.canvas.center(m.x, m.y)
                txt = str(m.number)
                fsize = max(9, int(r * (0.96 if len(txt) == 1 else 0.78)))
                col = (0.98, 0.96, 0.91) if m.color == BLACK else (0.10, 0.08, 0.05)
                # 白棋编号加深色描边, 浅色主题/亮背景上也看得清
                self._text(img, (L.board_x + cx, L.board_y + cy + r * 0.03),
                           txt, "sans_bold", fsize, col, "mm", a)

        # 2) 最后一手: 金环 + 呼吸
        if mv is not None and not mv.is_pass and getattr(mv, "valid", True):
            if self.canvas.stones.get((mv.x, mv.y)) == mv.color:
                slot = self.tl.slots[idx]
                since = t - (slot.start + slot.anim)
                if since >= 0:
                    pulse = 0.5 + 0.5 * math.sin(since * 3.2 - math.pi / 2)
                    a = 0.30 + 0.55 * clamp01(since / 0.45) * (0.75 + 0.25 * pulse)
                    cx, cy = self.canvas.center(mv.x, mv.y)
                    self._ring(img, (L.board_x + cx, L.board_y + cy),
                               r * (0.62 + 0.03 * pulse), a, self.th.accent,
                               max(2, int(r * 0.085)))
                    # 外圈淡光
                    self._ring(img, (L.board_x + cx, L.board_y + cy),
                               r * (0.94 + 0.06 * pulse), a * 0.28, self.th.accent,
                               max(1, int(r * 0.05)))

        # 3) SGF 标记
        self._draw_sgf_marks(img, mv)

    def _draw_sgf_marks(self, img: Image.Image, mv: Move) -> None:
        if not mv.marks:
            return
        L = self.L
        r = self.canvas.stone_r
        accent = self.th.accent
        for mk in mv.marks:
            cx, cy = self.canvas.center(mk.x, mk.y)
            cx += L.board_x
            cy += L.board_y
            if mk.kind == "LB":
                txt = mk.label[:6]
                if not txt:
                    continue
                self._label_text(img, (cx, cy), txt, r, mv)
            elif mk.kind in ("TR", "SQ", "CR", "MA"):
                self._mark_shape(img, (cx, cy), r * 0.60, mk.kind, accent)
            elif mk.kind in ("TB", "TW"):
                col = (0.1, 0.1, 0.1) if mk.kind == "TB" else (0.95, 0.95, 0.95)
                d = int(r * 0.5)
                layer = Image.new("RGBA", (d * 2, d * 2), (0, 0, 0, 0))
                ImageDraw.Draw(layer).ellipse([2, 2, d * 2 - 2, d * 2 - 2],
                                              fill=tuple(int(c * 255) for c in col) + (70,))
                img.paste(layer, (int(cx - d), int(cy - d)), layer.getchannel("A"))

    def _label_text(self, img: Image.Image, c: Tuple[float, float], txt: str,
                    r: float, mv: Move) -> None:
        """SGF 的 LB 标记: 深墨字 + 米色描边, 不用白底贴纸, 避免像贴图."""
        fs = max(10, int(r * (1.42 if len(txt) <= 1 else 1.02)))
        ss = 4
        fnt = font("sans_bold", fs * ss)
        tw = int(fnt.getlength(txt)) + fs * ss
        th_ = int((fnt.getbbox(txt)[3] - fnt.getbbox(txt)[1])) + fs * ss
        layer = Image.new("RGBA", (max(4, tw), max(4, th_)), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        bbox = fnt.getbbox(txt)
        ox = (layer.width - (bbox[2] - bbox[0])) / 2 - bbox[0]
        oy = (layer.height - (bbox[3] - bbox[1])) / 2 - bbox[1]
        # 先画米色描边(光晕), 再画深墨字
        d.text((ox, oy), txt, font=fnt, fill=(255, 246, 226, 235),
               stroke_width=max(2, int(fs * ss * 0.10)), stroke_fill=(255, 246, 226, 235))
        d.text((ox, oy), txt, font=fnt, fill=(38, 28, 16, 255))
        layer = layer.resize((max(2, layer.width // ss), max(2, layer.height // ss)),
                             Image.LANCZOS)
        img.paste(layer, (int(c[0] - layer.width / 2), int(c[1] - layer.height / 2)),
                  layer.getchannel("A"))

    def _mark_shape(self, img: Image.Image, c: Tuple[float, float], r: float,
                    kind: str, color: RGB) -> None:
        ss = 4
        d = int(r * 2.8) * ss
        layer = Image.new("RGBA", (d, d), (0, 0, 0, 0))
        dr = ImageDraw.Draw(layer)
        col = tuple(int(max(0, min(1, x)) * 255) for x in color) + (245,)
        w = max(2, int(r * 0.16 * ss))
        cc = d / 2
        rr = r * ss
        if kind == "TR":
            pts = [(cc, cc - rr), (cc - rr * 0.866, cc + rr * 0.5), (cc + rr * 0.866, cc + rr * 0.5)]
            dr.polygon(pts, outline=col, width=w)
        elif kind == "SQ":
            dr.rectangle([cc - rr * 0.82, cc - rr * 0.82, cc + rr * 0.82, cc + rr * 0.82],
                         outline=col, width=w)
        elif kind == "CR":
            dr.ellipse([cc - rr * 0.78, cc - rr * 0.78, cc + rr * 0.78, cc + rr * 0.78],
                       outline=col, width=w)
        elif kind == "MA":
            s = rr * 0.78
            dr.line([cc - s, cc - s, cc + s, cc + s], fill=col, width=w)
            dr.line([cc - s, cc + s, cc + s, cc - s], fill=col, width=w)
        layer = layer.resize((d // ss, d // ss), Image.LANCZOS)
        img.paste(layer, (int(c[0] - layer.width / 2), int(c[1] - layer.height / 2)),
                  layer.getchannel("A"))

    # ================================================================== #
    # 信息面板
    # ================================================================== #
    def _panel_stone(self, color: int, r: int) -> Image.Image:
        key = ("ps", color, int(r))
        hit = self._sprite_cache.get(key)
        if hit is not None:
            return hit[0]
        s = gx.stone_sprite(int(round(r)), self.th.black if color == BLACK else self.th.white, ss=3)
        self._sprite_cache[key] = (s, 0, 0)
        return s

    def _hair(self, img: Image.Image, x: float, y: float, w: float,
              alpha: float = 1.0, color: Optional[RGB] = None) -> None:
        if w < 1:
            return
        layer = Image.new("RGBA", (max(1, int(w)), 2), (0, 0, 0, 0))
        c = color or self.th.hair
        ImageDraw.Draw(layer).rectangle(
            [0, 0, w, 1], fill=tuple(int(v * 255) for v in c) + (int(self.th.hair_alpha * alpha),))
        img.paste(layer, (int(x), int(y)), layer.getchannel("A"))

    def _event_label(self) -> str:
        info = self.rv.info
        parts = [p for p in (info.event, info.round) if p]
        if parts:
            return clean_event_name("  ".join(parts))
        if info.name:
            return clean_event_name(info.name)
        return "围 棋 对 局"

    def _draw_panel(self, img: Image.Image, t: float, idx: int, p: float,
                    mv: Optional[Move]) -> None:
        if self.L.mode == "portrait":
            self._panel_portrait(img, t, idx, p, mv)
        else:
            self._panel_tall(img, t, idx, p, mv)

    # ------------------------------------------------------------------ #
    def _player_block(self, img, x, y, color, name, rank, name_size, k,
                      align: str = "left", max_w: float = 0.0) -> float:
        th = self.th
        r = name_size * 0.46
        dot = self._panel_stone(color, int(round(r)))
        img.paste(dot, (int(x), int(y + name_size * 0.12)), dot.getchannel("A"))
        tx = x + dot.width + name_size * 0.34
        nm = name or ("黑方" if color == BLACK else "白方")
        avail = (max_w or self.L.panel_w) - (tx - x) - 8
        fnt, sz = gx.fit_font("serif_bold", nm, avail, int(name_size), 12, 0.02 * name_size)
        self._text(img, (tx, y), nm, "serif_bold", int(sz), th.text, "la", 1.0,
                   0.02 * sz)
        bottom = y + sz
        if rank:
            self._text(img, (tx + 2, y + sz * 1.26), rank, "sans", max(9, int(sz * 0.40)),
                       th.dim, "la", 0.98, 0.06 * sz * 0.40)
            bottom = y + sz * 1.26 + sz * 0.40 * 1.2
        return bottom - y

    # ------------------------------------------------------------------ #
    # ------------------------------------------------------------------ #
    def _chart_block(self, img: Image.Image, idx: int, k: float) -> None:
        """曲线 + 标题(当前值)."""
        if not self.charts.active or self._chart_box is None:
            return
        bx, by, bw, bh = self._chart_box
        ck = self._analysis_k
        n = max(1, len(self.moves))
        wr = self.charts.wr
        assert wr is not None
        frac = idx / max(1, n - 1)
        cx = int(wr.pad_l + (wr.w - wr.pad_l - wr.pad_r) * frac)
        wr.blit(img, cx + 1)

        cur = self.series.wr_at(idx)
        wr_r = wr.box.x + wr.box.w
        # 标题画在图表自身头部留白里 (CurveChart.head), 不再外溢压住上一块
        self._text(img, (bx, by + 3 * ck), wr.label, "sans", max(8, int(10.5 * ck)),
                   self.th.faint, "la", 0.95, 0.08 * 10.5 * ck)
        if cur is not None:
            wr.draw_cursor(img, idx, cur, self.th, ck)
            txt = f"{cur:.1f}%"
            col = self.th.accent2 if self.th.dark else mix(self.th.accent2, (0.1, 0.1, 0.1), 0.35)
            self._text(img, (wr_r, by + 2 * ck), txt, "sans_bold", max(12, int(20 * ck)),
                       col, "ra", 1.0, 0.01 * 20 * ck)
        elif idx >= 0:
            self._text(img, (wr_r, by + 3 * ck),
                       "无分析" if self.lang == "zh" else "no analysis",
                       "sans", max(8, int(10 * ck)), self.th.faint, "ra", 0.95)

        # 副引擎标记
        if self.charts.wr is not None and any(v is not None for v in self.series.black_wr2):
            self._text(img, (bx, by + 14 * ck),
                       (self.series.engine2 or "AI-2")[:16], "sans",
                       max(7, int(8.5 * ck)), self.th.faint, "la", 0.9, 0.02)

        # 目差
        lead = self.charts.lead
        if lead is not None:
            ly = lead.box.y
            lv = self.series.lead_at(idx)
            lead.blit(img, cx + 1)
            lead.draw_cursor(img, idx, lv, self.th, ck)
            self._text(img, (bx, ly + 3 * ck), lead.label, "sans", max(8, int(10.5 * ck)),
                       self.th.faint, "la", 0.95, 0.08 * 10.5 * ck)
            # lr / ly 对「本手无目差数据」同样要可用: 以前只在 lv 非空时
            # 才给 lr 赋值, 部分分析的棋谱走到下面的标签绘制会直接崩.
            lr = lead.box.x + lead.box.w
            if lv is not None:
                sign = "+" if lv >= 0 else "−"
                txt = f"{sign}{abs(lv):.1f}"
                col = self.th.text if abs(lv) < 3 else (
                    (0.92, 0.62, 0.42) if lv > 0 else (0.62, 0.78, 0.92))
                self._text(img, (lr, ly + 2 * ck), txt, "sans_bold",
                           max(11, int(16 * ck)), col, "ra", 1.0, 0.01 * 16 * ck)
                lead_txt = (("黑领先" if lv > 0 else "白领先")
                            if self.lang == "zh" else "")
                if lead_txt:
                    self._text(img, (lr, ly + 18 * ck), lead_txt,
                               "sans", max(7, int(8.5 * ck)), self.th.faint, "ra", 0.9)

    # ------------------------------------------------------------------ #
    def _ai_card(self, img: Image.Image, idx: int, k: float) -> None:
        if self._card_box is None or not self.charts.active:
            return
        x, y, w, h = self._card_box
        ck = self._analysis_k
        th = self.th
        an = self.series.at(idx)

        # 半透明卡片底
        card = Image.new("RGBA", (int(w), int(h)), (0, 0, 0, 0))
        cd = ImageDraw.Draw(card)
        if th.dark:
            card_col = tuple(int(v * 255) for v in th.card) + (th.card_alpha + 8,)
        else:
            card_col = (0, 0, 0, 9)
        cd.rounded_rectangle([0, 0, w, h], radius=max(2, int(4 * ck)), fill=card_col)
        img.paste(card, (int(x), int(y)), card)

        q = min(1.0, h / 80.0)
        pad_in = max(6, int(10 * ck))
        usable = max(80, w - pad_in * 2)
        colw = usable / 4.0
        # 胜率
        wr = self.series.wr_at(idx)
        lead = self.series.lead_at(idx)
        plays = an.playouts if an else 0
        items = [
            (f"{wr:.1f}%" if wr is not None else "—",
             "胜率" if self.lang == "zh" else "winrate"),
            ((("+" if lead >= 0 else "−") + f"{abs(lead):.1f}") if lead is not None else "—",
             "目差" if self.lang == "zh" else "lead"),
            (f"{plays/1000:.1f}k" if plays >= 1000 else (str(plays) if plays else "—"),
             "计算量" if self.lang == "zh" else "visits"),
            ((self.series.engine or "—")[:10], "引擎" if self.lang == "zh" else "engine"),
        ]
        for i, (val, lab) in enumerate(items):
            cx = x + pad_in + colw * i
            self._text(img, (cx, y + 6 * ck * q), val, "sans_bold",
                       max(10, int(18 * ck * q)),
                       th.text if i == 0 else th.dim, "la", 1.0, 0.0)
            self._text(img, (cx, y + 28 * ck * q), lab, "sans",
                       max(7, int(9 * ck * q)), th.dim, "la", 1.0, 0.05 * 9 * ck * q)

        # 推荐点
        ry = y + h - 14 * ck * q
        if an and an.best:
            to_play = None
            if idx < len(self.moves):
                to_play = WHITE if self.moves[idx].color == BLACK else BLACK
            label = "AI 推荐 · 黑方胜率" if self.lang == "zh" else "AI picks (black wr)"
            self._text(img, (x, ry), label,
                       "sans", max(7, int(9 * ck)), th.faint, "la", 0.92, 0.04 * 9 * ck)
            bxx = x + w * 0.46
            for n_bm, bm in enumerate([b for b in an.best if b.valid][:2]):
                if to_play is not None:
                    bwr = bm.winrate if to_play == 1 else 100.0 - bm.winrate
                    s = f"{bm.coord} {bwr:.0f}%"
                else:
                    s = f"{bm.coord} {bm.winrate:.0f}%"
                fs = max(9, int(12 * ck * max(q, 0.86)))
                fnt, fs = gx.fit_font("sans", s, w * 0.26, fs, 8, 0.0)
                nw, _ = self._text(img, (bxx, ry - 1 * ck), s, "sans", fs,
                                   th.accent if n_bm == 0 else th.dim, "la", 0.98, 0.02)
                bxx += nw + 18 * ck
        else:
            eng = self.series.engine or "AI"
            self._text(img, (x, ry), f"{eng} · " + ("本手无分析数据" if self.lang == "zh"
                                                    else "no analysis for this move"),
                       "sans", max(7, int(9 * ck)), th.faint, "la", 0.92, 0.04 * 9 * ck)

    def _panel_tall(self, img, t, idx, p, mv) -> None:
        """横屏 / 方屏: 头部实测排布, 分析区用 _layout_analysis 的盒子."""
        L, th = self.L, self.th
        info = self.rv.info
        has_an = self.show_analysis and self.charts.active
        pad = getattr(self, "_pad", 14)
        x = L.panel_x + pad
        y = L.panel_y + pad
        w = L.panel_w - pad * 2
        k = self._analysis_k if has_an else L.panel_h / 950.0

        # ---- 赛事 + 日期 (同一行, 左右对齐) ----
        lab = self._event_label()
        fs = max(10, int(16 * k))
        fnt, fsz = gx.fit_font("sans", lab, w * 0.72, fs, 9, 0.10)
        self._text(img, (x, y), lab, "sans", fsz, th.dim, "la", 1.0, 0.10 * fsz)
        if info.date:
            self._text(img, (x + w, y + 1 * k), info.date, "sans",
                       max(9, int(12 * k)), th.faint, "ra", 0.95, 0.08 * 12 * k)

        # ---- 棋手 (并排) ----
        py = y + max(fsz * 1.55, 30 * k)
        colw = w * 0.5
        self._player_block(img, x, py, BLACK, info.black, info.black_rank,
                           int(30 * k), k, max_w=colw - 8 * k)
        self._player_block(img, x + colw + 4 * k, py, WHITE, info.white, info.white_rank,
                           int(30 * k), k, max_w=colw - 12 * k)

        # ---- 分隔 ----
        sepy = py + 56 * k
        self._hair(img, x, sepy, w, 1.0)
        self._hair(img, x, sepy, 48 * k, 2, th.accent)

        # ---- 手数 + 状态 (同一行, 左右各留余量) ----
        ly = sepy + 10 * k
        num = str(mv.number) if mv else "0"
        total = str(len(self.moves))
        big = int(48 * k)
        nw, nh = self._text(img, (x, ly), num, "serif_bold", big, th.text, "la", 1.0)
        self._text(img, (x + nw + 8 * k, ly + nh - 14 * k), f"/ {total}", "sans",
                   max(9, int(13 * k)), th.faint, "la", 0.95, 0)

        if mv is not None:
            sx = x + w * 0.48
            sy = ly + 6 * k
            dot = self._panel_stone(mv.color, int(7 * k))
            img.paste(dot, (int(sx), int(sy + 2 * k)), dot.getchannel("A"))
            col_name = ("黑方落子" if mv.color == BLACK else "白方落子") if self.lang == "zh" \
                else ("Black to play" if mv.color == BLACK else "White to play")
            self._text(img, (sx + dot.width + 8 * k, sy), col_name, "sans",
                       max(9, int(14 * k)), th.dim, "la", 1.0, 0.02 * 14 * k)
            cb, cw = self._captures_upto(idx + 1)
            self._text(img, (sx + dot.width + 8 * k, sy + 22 * k),
                       f"提子 {cb} : {cw}" if self.lang == "zh" else f"captures {cb} : {cw}",
                       "sans", max(8, int(12 * k)), th.dim, "la", 0.95, 0.02 * 12 * k)

        cmt_text = self._comment_text(idx, mv)
        has_cmt = bool(self.show_comment and cmt_text)
        pby = L.panel_y + L.panel_h - 16 * k

        if has_an:
            self._chart_block(img, idx, k)
            self._ai_card(img, idx, k)
            # 最近手数 (一行, 用布局预算)
            list_h = getattr(self, "_list_h", 0.0)
            list_top = getattr(self, "_list_top", 0.0)
            if list_h > 20:
                # 标题画在最近手数区域内部顶部, 不再上溢压 AI 卡
                self._text(img, (x, list_top),
                           ("最 近 手 数" if self.lang == "zh" else "R E C E N T   M O V E S"),
                           "sans", max(8, int(10 * k)), th.faint, "la", 0.92, 1.6 * k)
                self._recent_moves(img, x, list_top + 16 * k, w,
                                   max(18 * k, list_h - 16 * k), k, idx, 3, 1)
            c_top = getattr(self, "_comment_top", list_top + list_h + 8 * k)
            c_h = max(48.0, getattr(self, "_comment_h", pby - c_top - 8 * k))
            box = (x, c_top, w, c_h)
        else:
            box = (x, ly + nh + 18 * k, w, max(60.0, pby - (ly + nh + 18 * k)))

        if has_cmt:
            base = int(min(24 * k, max(16 * k, box[3] / 6.8)))
            self._draw_comment(img, t, idx, mv, box, k, base=base, text=cmt_text)
        else:
            self._info_block(img, idx, box, k)

        self._progress(img, L.panel_x + pad, pby, w, k, (idx + 1) / max(1, len(self.moves)))

    def _panel_portrait(self, img, t, idx, p, mv) -> None:
        """竖屏: 棋盘在上, 信息面板在下 (流式布局)."""
        L, th = self.L, self.th
        info = self.rv.info
        has_an = self.show_analysis and self.charts.active
        pad = getattr(self, "_pad", 12)
        x = L.panel_x + pad
        y = L.panel_y + pad
        w = L.panel_w - pad * 2
        k = self._analysis_k if has_an else L.panel_h / 610.0

        # ---- 赛事 + 日期 ----
        lab = self._event_label()
        fs = max(10, int(14 * k))
        fnt, fsz = gx.fit_font("sans", lab, w * 0.70, fs, 9, 0.08)
        self._text(img, (x, y), lab, "sans", fsz, th.dim, "la", 1.0, 0.08 * fsz)
        if info.date:
            self._text(img, (x + w, y + 1 * k), info.date, "sans", max(9, int(12 * k)),
                       th.faint, "ra", 0.95, 0.08 * 12 * k)

        # ---- 棋手 ----
        py = y + max(fsz * 1.5, 26 * k)
        colw = w * 0.5
        self._player_block(img, x, py, BLACK, info.black, info.black_rank,
                           int(28 * k), k, max_w=colw - 6 * k)
        self._player_block(img, x + colw + 6 * k, py, WHITE, info.white,
                           info.white_rank, int(28 * k), k, max_w=colw - 10 * k)

        sepy = py + 52 * k
        self._hair(img, x, sepy, w, 1.0)
        self._hair(img, x, sepy, 42 * k, 2, th.accent)

        # ---- 手数 + 状态 ----
        ny = sepy + 14 * k
        num = str(mv.number) if mv else "0"
        total = str(len(self.moves))
        big = int(52 * k)
        nw, nh = self._text(img, (x, ny), num, "serif_bold", big, th.text, "la", 1.0)
        self._text(img, (x + nw + 8 * k, ny + nh - 12 * k), f"/ {total}", "sans",
                   max(9, int(13 * k)), th.faint, "la", 0.95, 0)

        if mv is not None:
            sx = x + w * 0.50
            sy = ny + 2 * k
            dot = self._panel_stone(mv.color, int(7 * k))
            img.paste(dot, (int(sx), int(sy + 2 * k)), dot.getchannel("A"))
            col_name = ("黑方落子" if mv.color == BLACK else "白方落子") if self.lang == "zh" \
                else ("Black" if mv.color == BLACK else "White")
            self._text(img, (sx + dot.width + 8 * k, sy), col_name, "sans",
                       max(9, int(14 * k)), th.dim, "la", 1.0, 0.02 * 14 * k)
            cb, cw = self._captures_upto(idx + 1)
            self._text(img, (sx + dot.width + 8 * k, sy + 18 * k),
                       f"提子 {cb} : {cw}" if self.lang == "zh" else f"captures {cb} : {cw}",
                       "sans", max(8, int(11 * k)), th.dim, "la", 0.95, 0.02 * 11 * k)

        cmt_text = self._comment_text(idx, mv)
        has_cmt = bool(self.show_comment and cmt_text)
        pby = L.panel_y + L.panel_h - 12 * k

        if has_an:
            self._chart_block(img, idx, k)
            self._ai_card(img, idx, k)
            c_top = getattr(self, "_comment_top", self._card_box[1] + self._card_box[3] + 8 * k)
            c_h = max(36.0, getattr(self, "_comment_h", pby - c_top))
            box = (x, c_top, w, c_h)
        else:
            list_top = ny + nh + 12 * k
            reserve = (100 if has_cmt else 64) * k
            avail = max(20.0, pby - reserve - list_top)
            per_col = max(1, min(5, int(avail / (26 * k))))
            row_h = max(20 * k, min(32 * k, avail / per_col if avail > 0 else 22 * k))
            rows = min(per_col * 2, idx + 1)
            if rows >= 2:
                self._recent_moves(img, x, list_top, w, row_h, k, idx, rows, per_col)
            box = (x, list_top + per_col * row_h + 8 * k, w,
                   max(32.0, pby - (list_top + per_col * row_h + 8 * k)))

        if has_cmt:
            base = int(min(18 * k, max(13 * k, box[3] / 5.2)))
            self._draw_comment(img, t, idx, mv, box, k, base=base, text=cmt_text)
        else:
            self._info_block(img, idx, box, k)

        self._progress(img, L.panel_x + pad, pby, w, k, (idx + 1) / max(1, len(self.moves)))


    def _recent_moves(self, img, x, y, w, row_h, k, idx, rows,
                      per_col: Optional[int] = None) -> None:
        """最近几手: 双栏排列, 用满面板宽度."""
        th = self.th
        if not per_col:
            per_col = max(1, (rows + 1) // 2)
        col_w = (w - 26 * k) / 2.0
        num_w = 30 * k
        dot_r = int(round(9 * k))
        for r in range(min(rows, per_col * 2)):
            col = 0 if r < per_col else 1
            rr = r if col == 0 else r - per_col
            i = idx - r
            if i < 0:
                break
            m = self.moves[i]
            if not getattr(m, "valid", True):
                continue
            cx = x + col * (col_w + 26 * k)
            yy = y + rr * row_h
            a = 1.0 if r == 0 else max(0.45, 0.92 - r * 0.075)
            base_y = yy + (row_h - 18 * k) / 2
            self._text(img, (cx + num_w, base_y), str(m.number), "sans",
                       max(9, int(14 * k)), th.faint, "ra", a * 0.9)
            dot = self._panel_stone(m.color, dot_r)
            img.paste(dot, (int(cx + num_w + 9 * k),
                            int(yy + (row_h - dot.height) / 2)), dot.getchannel("A"))
            if m.is_pass:
                coord, ccol = ("虚手" if self.lang == "zh" else "pass"), th.faint
            else:
                coord = f"{col_label(m.x, self.rv.info.size_x)}{self.rv.info.size_y - m.y}"
                ccol = th.text if r == 0 else th.dim
            self._text(img, (cx + num_w + 9 * k + dot.width + 10 * k, base_y), coord,
                       "sans", max(9, int(18 * k)), ccol, "la", a)


    # ------------------------------------------------------------------ #
    RULES_ZH = {
        "chinese": "中国规则", "japanese": "日本规则", "aga": "AGA 规则",
        "korean": "韩国规则", "nz": "新西兰规则", "tromp-taylor": "Tromp-Taylor",
        "goe": "应氏规则", "ing": "应氏规则", "bga": "英国规则",
    }

    def _place_text(self, place: str) -> str:
        if not place:
            return ""
        txt = place.strip()
        low = txt.lower()
        if "kgs" in low and "server" in low:
            return "KGS 对局服务器" if self.lang == "zh" else "KGS Go Server"
        for sep in ("http://", "https://", "www."):
            idx = txt.lower().find(sep)
            if idx > 0:
                txt = txt[:idx]
        txt = txt.strip().strip("·-—,， ").strip()
        return txt[:32]

    def _rules_text(self, rules: str) -> str:
        if not rules:
            return ""
        if self.lang != "zh":
            return rules
        key = rules.strip().lower()
        return self.RULES_ZH.get(key, rules)


    def _ai_summary_block(self, img, idx, box, k) -> bool:
        """没有人类评论时, 用 AI 复盘摘要填充空白."""
        ser = self.series
        if ser is None or ser.count == 0:
            return False
        th = self.th
        x, y, w, h = box
        if h < 68 * k:
            return False
        # 前半盘不剧透「最大失误」, 留给赛后复盘
        if idx < len(self.moves) * 0.45:
            return False
        zh = self.lang == "zh"
        compact = h < 132 * k
        # 只统计「已经下过」的手, 避免提前透露后面的失误
        upto = min(idx + 1, len(ser.points))
        judged = matched = an_cnt = 0
        loss_sum = 0.0
        for i in range(upto):
            an = ser.points[i]
            if an is None:
                continue
            an_cnt += 1
            if an.swing is not None and an.swing < 0:
                loss_sum += -an.swing
            if not an.best or i + 1 >= len(self.moves):
                continue
            top = an.best[0]
            if not top.valid or self.moves[i + 1].is_pass:
                continue
            judged += 1
            nxt = self.moves[i + 1]
            if (nxt.x, nxt.y) == (top.x, top.y):
                matched += 1
        rate = matched / max(1, judged) * 100
        avg_loss = loss_sum / max(1, an_cnt)
        past = [m for m in ser.key_moments if m.index <= idx]
        worst = min(past, key=lambda m: m.swing) if past else None

        self._hair(img, x, y - 6 * k, 26 * k, 1.0, th.accent)
        self._text(img, (x, y), ("AI 复盘" if zh else "AI REVIEW"),
                   "sans", max(8, int(10 * k)), th.faint, "la", 0.95, 1.2 * k)

        # 大数字: 吻合度 (数字与标签同一基线)
        by = y + (18 if compact else 24) * k
        big = max(16, int((28 if compact else 34) * k))
        nw, nh = self._text(img, (x, by), f"{rate:.0f}%", "serif_bold", big,
                            th.accent, "la", 1.0)
        self._text(img, (x + nw + 8 * k, by + nh - 12 * k),
                   ("AI 首选吻合度" if zh else "AI match rate"),
                   "sans", max(8, int(10.5 * k)), th.faint, "la", 0.95, 0.05 * 10.5 * k)

        rx = x + w
        self._text(img, (rx, by + 2 * k), f"{ser.count} / {len(self.moves)}",
                   "sans_bold", max(10, int(14 * k)), th.dim, "ra", 1.0)
        self._text(img, (rx, by + 20 * k), ("已分析手数" if zh else "moves analysed"),
                   "sans", max(8, int(9.5 * k)), th.faint, "ra", 0.95, 0.05 * 9.5 * k)

        # 细节行
        ly = by + nh + (4 if compact else 10) * k
        loss_txt = f"平均损失 {avg_loss:.1f}%"
        if worst is not None:
            side = "黑" if worst.color == 1 else "白"
            loss_txt += f"   ·   最大失误 第 {worst.number} 手（{side}）{worst.swing:+.1f}%"
        if not zh:
            loss_txt = f"avg loss {avg_loss:.1f}%"
            if worst is not None:
                side = "B" if worst.color == 1 else "W"
                loss_txt += f"   ·   biggest drop move {worst.number} ({side}) {worst.swing:+.1f}%"
        fnt, fs = gx.fit_font("sans", loss_txt, w, max(8, int(11.5 * k)), 8, 0.02)
        self._text(img, (x, ly), loss_txt, "sans", fs, th.dim, "la", 0.95, 0.02)

        if compact:
            return True

        # 引擎信息
        ey = y + h - 14 * k
        eng = ser.engine or "AI"
        avg_visits = 0
        cnt = 0
        for p in ser.points:
            if p is not None and p.playouts:
                avg_visits += p.playouts
                cnt += 1
        if cnt:
            avg_visits //= cnt
        etxt = f"{eng}"
        if avg_visits:
            etxt += f"  ·  " + (f"平均 {avg_visits/1000:.1f}k visits" if avg_visits >= 1000
                               else f"平均 {avg_visits} visits")
        if ser.engine2:
            etxt += f"  ·  {ser.engine2}"
        fnt, fs = gx.fit_font("sans", etxt, w, max(8, int(10 * k)), 8, 0.02)
        self._text(img, (x, ey), etxt, "sans", fs, th.faint, "la", 0.9, 0.02)
        return True

    def _info_block(self, img, idx, box, k) -> None:
        """没有解说的局面: 用棋局资料填充, 竖向均匀铺开, 避免大片空白."""
        th = self.th
        info = self.rv.info
        x, y, w, h = box
        if h < 30 * k:
            return
        if self._ai_summary_block(img, idx, box, k):
            return
        # 分析尚未覆盖到这里时, 明确告诉观众从第几手开始
        if self.show_analysis and self.series is not None and self.series.count:
            first = next((i + 1 for i, p in enumerate(self.series.points)
                          if p is not None), 0)
            if first and idx + 1 < first:
                self._text(img, (x, y), 
                           (f"AI 分析 · 第 {first} 手起" if self.lang == "zh"
                            else f"AI analysis starts at move {first}"),
                           "sans", max(9, int(12 * k)), self.th.accent2, "la", 0.9, 0.05 * 12 * k)
                return
        lines: List[Tuple[str, str]] = []
        meta: List[str] = []
        kk = describe_komi(info.komi)
        if kk:
            meta.append(kk)
        if info.handicap:
            meta.append(f"让 {info.handicap} 子" if self.lang == "zh" else f"handicap {info.handicap}")
        rt = self._rules_text(info.rules)
        if rt:
            meta.append(rt)
        if meta:
            lines.append(("     ·     ".join(meta), "dim"))
        pl = self._place_text(info.place)
        if pl:
            lines.append((pl, "accent"))
        tw = info.time_limit.strip()
        if tw:
            lines.append((("用时  " if self.lang == "zh" else "Time  ") + tw[:36], "faint"))
        if not lines:
            return

        fs = max(9, int(15 * k))
        n = len(lines)
        step = min(46 * k, max(fs * 1.75, h / n))
        total_h = step * (n - 1) + fs
        y0 = y + h - total_h - 6 * k
        self._hair(img, x, y0 - 16 * k, 26 * k, 1.0, th.accent)
        for i, (ln, style) in enumerate(lines):
            col = {"dim": th.dim, "accent": th.accent, "faint": th.faint}[style]
            self._text(img, (x, y0 + i * step), ln, "sans", fs, col, "la",
                       0.92 if i == 0 else 0.82, 0.03 * fs)


    def _progress(self, img, x, y, w, k, frac) -> None:
        th = self.th
        hgt = max(2, int(3.0 * k))
        track = Image.new("RGBA", (int(w), hgt * 3), (0, 0, 0, 0))
        td = ImageDraw.Draw(track)
        td.rounded_rectangle([0, hgt, w, hgt * 2], radius=hgt / 2,
                             fill=tuple(int(v * 255) for v in th.hair) + (min(255, int(th.hair_alpha * 1.9)),))
        img.paste(track, (int(x), int(y - hgt)), track.getchannel("A"))

        fw = max(1, int(w * clamp01(frac)))
        fill = Image.new("RGBA", (fw, hgt * 3), (0, 0, 0, 0))
        fd = ImageDraw.Draw(fill)
        fd.rounded_rectangle([0, hgt, fw, hgt * 2], radius=hgt / 2,
                             fill=tuple(int(v * 255) for v in th.accent) + (235,))
        img.paste(fill, (int(x), int(y - hgt)), fill.getchannel("A"))
        # 进度端点光点
        dot_r = max(2, int(2.4 * k))
        dot = Image.new("RGBA", (dot_r * 6, dot_r * 6), (0, 0, 0, 0))
        ImageDraw.Draw(dot).ellipse([dot_r, dot_r, dot_r * 5, dot_r * 5],
                                    fill=tuple(int(v * 255) for v in th.accent) + (255,))
        img.paste(dot, (int(x + fw - dot_r * 3), int(y - dot_r * 3)), dot.getchannel("A"))

    # ------------------------------------------------------------------ #
    def _captures_upto(self, n: int) -> Tuple[int, int]:
        b = w = 0
        for mv in self.moves[:n]:
            if mv.color == BLACK:
                b += len(mv.captured)
            else:
                w += len(mv.captured)
        return b, w

    # ------------------------------------------------------------------ #
    def _comment_text(self, idx: int, mv: Optional[Move]) -> str:
        """人类评论优先; 没有时用 AI 生成的点评(只在关键手出现)."""
        if mv is None:
            return ""
        if mv.comment.strip():
            return mv.comment.strip()
        if self.ai_commentary and self.show_analysis and self.series is not None:
            return lz.ai_commentary(self.series, idx, self.lang).strip()
        return ""

    def _draw_comment(self, img, t, idx, mv, box, k, base: int,
                      text: Optional[str] = None) -> None:
        txt = (text if text is not None else (mv.comment if mv is not None else "")).strip()
        if not self.show_comment or not txt:
            return
        if not self.tl.slots:
            return
        x, y, w, h = box
        if h < 20:
            return
        slot = self.tl.slots[idx]
        a_in = clamp01((t - slot.start - 0.10) / 0.38)
        if idx < len(self.tl.slots) - 1:
            a_out = clamp01((slot.end - t) / 0.22)
        else:
            a_out = 1.0
        a = min(a_in, a_out)
        if a <= 0.01:
            return

        size = base
        lines: List[str] = []
        for _ in range(4):
            fnt = font("sans", max(10, size))
            lines = gx.wrap_text(txt, fnt, w - 4)
            max_lines = max(1, int(h // (size * 1.52)))
            if len(lines) <= max_lines:
                break
            size = int(size * 0.90)
        max_lines = max(1, int(h // (size * 1.52)))
        truncated = len(lines) > max_lines
        lines = lines[:max_lines]
        if truncated and lines:
            last = lines[-1]
            while last and font("sans", max(10, size)).getlength(last + "…") > w - 4:
                last = last[:-1]
            lines[-1] = last + "…"

        lh = size * 1.52
        # 顶部细线 + 小标记
        self._hair(img, x, y - 8 * k, 26 * k, 1.0, self.th.accent)
        for i, ln in enumerate(lines):
            self._text(img, (x, y + i * lh), ln, "sans", max(10, size),
                       self.th.text if i == 0 else self.th.dim, "la", a)
        return

    # ================================================================== #
    # 片头
    # ================================================================== #
    def _intro_ai_text(self) -> str:
        ser = self.series
        if ser is None or not ser.count:
            return ""
        zh = self.lang == "zh"
        eng = ser.engine or "AI"
        if zh:
            return f"AI 分析  ·  {eng}  ·  {ser.count} 手"
        return f"AI review  ·  {eng}  ·  {ser.count} moves"

    def _outro_ai_text(self) -> str:
        ser = self.series
        if ser is None or not ser.count:
            return ""
        zh = self.lang == "zh"
        rate = ser.matched_moves / max(1, ser.judged_moves) * 100
        worst = min(ser.key_moments, key=lambda m: m.swing) if ser.key_moments else None
        if zh:
            txt = f"AI 吻合度 {rate:.0f}%"
            if worst is not None:
                txt += f"   ·   最大失误 第 {worst.number} 手 {worst.swing:+.1f}%"
            return txt
        txt = f"AI match {rate:.0f}%"
        if worst is not None:
            txt += f"   ·   biggest drop: move {worst.number} {worst.swing:+.1f}%"
        return txt

    def _decor_circle(self, img: Image.Image, cx: float, cy: float, r: float,
                      alpha: float = 0.05, width: float = 1.6) -> None:
        if r < 4:
            return
        ss = 2 if r > 300 else 3
        d = int(r * 2 * ss)
        layer = Image.new("RGBA", (d, d), (0, 0, 0, 0))
        dr = ImageDraw.Draw(layer)
        w = max(1, int(width * ss))
        col = tuple(int(v * 255) for v in self.th.accent) + (int(255 * alpha),)
        dr.ellipse([w, w, d - w, d - w], outline=col, width=w)
        layer = layer.resize((int(r * 2), int(r * 2)), Image.LANCZOS)
        img.paste(layer, (int(cx - r), int(cy - r)), layer.getchannel("A"))

    def _logo(self, img: Image.Image, x: float, y: float, size: float) -> None:
        r = size * 0.44
        rb = int(round(r))
        b = gx.stone_sprite(rb, self.th.black, ss=3)
        w = gx.stone_sprite(rb, self.th.white, ss=3)
        img.paste(b, (int(x), int(y)), b.getchannel("A"))
        img.paste(w, (int(x + size * 0.40), int(y + size * 0.18)), w.getchannel("A"))

    def _intro_scene(self, t: float) -> Image.Image:
        L, th = self.L, self.th
        img = self.bg.copy()
        info = self.rv.info
        u = self.unit
        portrait = L.mode == "portrait"
        cx = L.W / 2 if portrait else L.W * 0.098
        anchor = "ma" if portrait else "la"
        maxw = L.W * 0.80 if portrait else L.W * 0.62

        if not portrait:
            self._decor_circle(img, L.W * 0.795, L.H * 0.40, L.H * 0.485, 0.095, 2.0)
            self._decor_circle(img, L.W * 0.795, L.H * 0.40, L.H * 0.30, 0.045, 1.4)

        b = info.black or ("黑方" if self.lang == "zh" else "Black")
        w_ = info.white or ("白方" if self.lang == "zh" else "White")
        title = f"{b}  vs  {w_}"
        target = int(72 * u) if portrait else int(134 * u)
        fnt, tsz = gx.fit_font("serif_bold", title, maxw, target, 22)

        sub_parts = [p for p in (info.event, info.round) if p]
        sub = "  ·  ".join(sub_parts)
        if not sub and info.name:
            sub = clean_event_name(info.name, "")
        if info.date:
            sub = f"{sub}  ·  {info.date}" if sub else info.date
        if not sub:
            sub = f"{len(self.moves)} 手" if self.lang == "zh" else f"{len(self.moves)} moves"
        ss = max(11, int(22 * u))
        _, ssz = gx.fit_font("sans", sub, maxw * 0.92, ss, 9, 0.05)

        bits: List[str] = []
        if self.moves:
            bits.append(f"{len(self.moves)} 手" if self.lang == "zh" else f"{len(self.moves)} moves")
        kk = describe_komi(info.komi)
        if kk:
            bits.append(kk if self.lang == "zh" else f"komi {info.komi:g}")
        if info.handicap:
            bits.append(f"让 {info.handicap} 子" if self.lang == "zh" else f"handicap {info.handicap}")
        rt = self._rules_text(info.rules)
        if rt:
            bits.append(rt)
        meta = "     ·     ".join(bits)

        logo_sz = 0 if portrait else int(84 * u)
        kicker = "围棋对局回顾" if self.lang == "zh" else "GAME REVIEW"
        ks = max(10, int(16 * u))

        items = []
        if logo_sz:
            items.append(("logo", logo_sz * 1.36, 34 * u))
        items.append(("kicker", self._h(kicker, "sans", ks, ks * 0.34), 24 * u))
        items.append(("title", self._h(title, "serif_bold", tsz, 0.02 * tsz), 36 * u))
        items.append(("rule", max(2, 2 * u), 28 * u))
        items.append(("sub", self._h(sub, "sans", ssz, 0.05 * ssz), 24 * u))
        items.append(("meta", self._h(meta, "sans", int(16 * u), 0.06 * 16 * u),
                      18 * u if self.show_analysis else 0))
        if self.show_analysis and self.series is not None and self.series.count:
            ai_txt = self._intro_ai_text()
            items.append(("ai", self._h(ai_txt, "sans", int(14 * u), 0.08 * 14 * u), 0))

        total = sum(h + g for _, h, g in items)
        y = (L.H - total) / 2
        for kind, hh, gap in items:
            if kind == "logo":
                self._logo(img, cx, y, logo_sz)
            elif kind == "kicker":
                e = ease_out_cubic(clamp01((t - 0.08) / 0.70))
                self._text(img, (cx, y + (1 - e) * 12 * u), kicker, "sans_bold", ks,
                           th.accent, anchor, e * 0.98, ks * 0.34)
            elif kind == "title":
                e = ease_out_cubic(clamp01((t - 0.26) / 0.80))
                self._text(img, (cx, y + (1 - e) * 18 * u), title, "serif_bold",
                           int(tsz), th.text, anchor, e, 0.02 * tsz)
            elif kind == "rule":
                e = ease_out_cubic(clamp01((t - 0.66) / 0.62))
                lw = (150 * u if not portrait else 190 * u) * e
                if lw > 1:
                    self._hair(img, cx - (lw / 2 if portrait else 0), y, lw, 1.0, th.accent)
            elif kind == "sub":
                e = ease_out_cubic(clamp01((t - 0.86) / 0.72))
                self._text(img, (cx, y + (1 - e) * 10 * u), sub, "sans", int(ssz),
                           th.dim, anchor, e * 0.98, 0.05 * ssz)
            elif kind == "meta":
                e = ease_out_cubic(clamp01((t - 1.06) / 0.70))
                if meta:
                    self._text(img, (cx, y), meta, "sans", max(9, int(16 * u)),
                               th.faint, anchor, e * 0.92, 0.06 * 16 * u)
            elif kind == "ai":
                e = ease_out_cubic(clamp01((t - 1.26) / 0.72))
                self._text(img, (cx, y), self._intro_ai_text(), "sans",
                           max(9, int(13.5 * u)), th.accent2, anchor, e * 0.92, 0.08 * 13.5 * u)
            y += hh + gap

        if self.watermark:
            self._text(img, (L.W - L.W * 0.024, L.H - L.H * 0.026), self.watermark,
                       "sans", int(15 * u), th.faint, "rb", 0.55)
        return img

    # ================================================================== #
    # 片尾
    # ================================================================== #
    def _outro_scene(self, t: float) -> Image.Image:
        L, th = self.L, self.th
        img = self.bg.copy()
        info = self.rv.info
        u = self.unit
        lt = t - self.tl.outro_start
        portrait = L.mode == "portrait"
        cx = L.W / 2
        anchor = "ma"
        maxw = L.W * 0.84 if portrait else L.W * 0.70

        if not portrait:
            self._decor_circle(img, L.W * 0.5, L.H * 0.46, L.H * 0.52, 0.075, 2.0)

        main, sub = describe_result(info.result, self.lang)
        if not main:
            main = "对局结束" if self.lang == "zh" else "Game Complete"
            sub = ""
        if self.lang == "zh":
            sub = ""

        target = int(78 * u) if portrait else int(124 * u)
        fnt, tsz = gx.fit_font("serif_bold", main, maxw, target, 24)

        bits = []
        if self.moves:
            bits.append(f"{len(self.moves)} 手" if self.lang == "zh" else f"{len(self.moves)} moves")
        cb, cw = self._captures_upto(len(self.moves))
        if cb or cw:
            bits.append(f"提子 {cb} : {cw}" if self.lang == "zh" else f"captures {cb} : {cw}")
        if info.date:
            bits.append(info.date)
        meta = "     ·     ".join(bits)

        names = f"{info.black or ('黑方' if self.lang == 'zh' else 'Black')}   —   " \
                f"{info.white or ('白方' if self.lang == 'zh' else 'White')}"

        kick = "结  果" if self.lang == "zh" else "RESULT"
        items = [("kicker", self._h(kick, "sans", int(16 * u), 4.4 * u), 34 * u),
                 ("main", self._h(main, "serif_bold", tsz, 0.02 * tsz), 40 * u),
                 ("rule", max(2, 2 * u), 34 * u),
                 ("meta", self._h(meta, "sans", int(19 * u), 0.05 * 19 * u), 26 * u),
                 ("names", self._h(names, "sans", int(16 * u), 0.05 * 16 * u),
                  26 * u if self.show_analysis else 0)]
        if self.show_analysis and self.series is not None and self.series.count:
            items.append(("ai", self._h(self._outro_ai_text(), "sans", int(15 * u),
                                        0.04 * 15 * u), 0))
        total = sum(h + g for _, h, g in items)
        y = (L.H - total) / 2
        for kind, hh, gap in items:
            if kind == "kicker":
                e = ease_out_cubic(clamp01((lt - 0.08) / 0.66))
                self._text(img, (cx, y), kick,
                           "sans_bold", max(10, int(16 * u)), th.accent, anchor, e * 0.9, 4.4 * u)
            elif kind == "main":
                e = ease_out_cubic(clamp01((lt - 0.22) / 0.80))
                self._text(img, (cx, y + (1 - e) * 16 * u), main, "serif_bold",
                           int(tsz), th.accent, anchor, e, 0.02 * tsz)
            elif kind == "rule":
                e = ease_out_cubic(clamp01((lt - 0.60) / 0.62))
                lw = 300 * u * e
                if lw > 1:
                    self._hair(img, cx - lw / 2, y, lw, 1.0, th.text)
            elif kind == "meta":
                e = ease_out_cubic(clamp01((lt - 0.78) / 0.66))
                if meta:
                    self._text(img, (cx, y), meta, "sans", max(9, int(18 * u)),
                               th.dim, anchor, e * 0.95, 0.05 * 18 * u)
            elif kind == "names":
                e = ease_out_cubic(clamp01((lt - 0.96) / 0.66))
                self._text(img, (cx, y), names, "sans", max(9, int(15 * u)),
                           th.faint, anchor, e * 0.85, 0.05 * 15 * u)
            elif kind == "ai":
                e = ease_out_cubic(clamp01((lt - 1.20) / 0.70))
                self._text(img, (cx, y), self._outro_ai_text(), "sans",
                           max(9, int(14.5 * u)), th.accent2, anchor, e * 0.9,
                           0.04 * 14.5 * u)
            y += hh + gap

        if self.watermark:
            self._text(img, (L.W - L.W * 0.024, L.H - L.H * 0.026), self.watermark,
                       "sans", int(15 * u), th.faint, "rb", 0.55)
        return img

    # ================================================================== #
    # 帧调度
    # ================================================================== #
    def _board(self, t: float) -> Image.Image:
        if self.moves and self.tl.slots:
            idx, p = self.tl.slot_at(t)
            idx = min(idx, len(self.moves) - 1)
        else:
            idx, p = 0, 0.0
        mv = self.moves[idx] if self.moves else None
        return self._board_scene(t, idx, p)

    def frame(self, t: float) -> Image.Image:
        ti = self.tl.intro_end
        to = self.tl.outro_start
        f = 0.55
        has_intro = self.tl.timing.intro > 0.05
        has_outro = self.tl.timing.outro > 0.05
        # 片头 → 正片: 先淡回背景, 再淡入棋盘, 避免文字压在棋盘上
        if has_intro:
            if t < ti - f:
                return self._intro_scene(t)
            if t < ti:
                k = ease_in_out(clamp01((t - (ti - f)) / f))
                return Image.blend(self._intro_scene(t), self.bg, k)
            if t < ti + f:
                k = ease_in_out(clamp01((t - ti) / f))
                return Image.blend(self.bg, self._board(t), k)

        # 正片 → 片尾: 同样先淡回背景
        if not has_outro:
            return self._board(t)
        if t < to - f:
            return self._board(t)
        if t < to:
            k = ease_in_out(clamp01((t - (to - f)) / f))
            return Image.blend(self._board(t), self.bg, k)
        if t < to + f:
            k = ease_in_out(clamp01((t - to) / f))
            return Image.blend(self.bg, self._outro_scene(t), k)
        return self._outro_scene(t)

    def render_frame(self, i: int, fps: Optional[int] = None) -> Image.Image:
        fps = fps or self.fps
        img = self.frame(i / float(fps))
        return img if img.mode == "RGB" else img.convert("RGB")

    @property
    def n_frames(self) -> int:
        return max(1, int(round(self.tl.total * self.fps)))
