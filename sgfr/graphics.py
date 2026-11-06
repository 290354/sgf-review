"""程序化图形: 木纹 / 纸张 / 球体光照棋子 / 文字排版."""
from __future__ import annotations

import math
from functools import lru_cache
from typing import List, Optional, Sequence, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .theme import RGB, StoneStyle, font

# --------------------------------------------------------------------------- #
# 噪声
# --------------------------------------------------------------------------- #
def _value_noise(w: int, h: int, scale: float, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    gw = max(3, int(math.ceil(w / max(1.0, scale))) + 2)
    gh = max(3, int(math.ceil(h / max(1.0, scale))) + 2)
    grid = (rng.random((gh, gw)) * 255).astype(np.uint8)
    img = Image.fromarray(grid, mode="L").resize((w, h), Image.BICUBIC)
    return np.asarray(img, dtype=np.float32) / 255.0


def fbm(w: int, h: int, scale: float, octaves: int = 4, seed: int = 0,
        persistence: float = 0.5) -> np.ndarray:
    total = np.zeros((h, w), dtype=np.float32)
    amp, norm, sc = 1.0, 0.0, scale
    for o in range(octaves):
        total += amp * _value_noise(w, h, sc, seed + o * 17)
        norm += amp
        amp *= persistence
        sc = max(1.5, sc * 0.45)
    return total / max(norm, 1e-6)


def _smoothstep(a: float, b: float, x: np.ndarray) -> np.ndarray:
    t = np.clip((x - a) / max(1e-6, (b - a)), 0.0, 1.0)
    return t * t * (3 - 2 * t)


# --------------------------------------------------------------------------- #
# 棋盘木纹
# --------------------------------------------------------------------------- #
def wood_texture(w: int, h: int, st, seed: int = 7) -> Image.Image:
    """榧木质感: 纵向顺纹 + 窄年轮暗线 + 大块色差."""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    nx = xx / max(1, w - 1)
    ny = yy / max(1, h - 1)

    # 纹理坐标弯曲: 三个尺度叠加, 让纹路像真的木头一样流动
    warp = ((fbm(w, h, 420, 3, seed) - 0.5) * 0.020
            + (fbm(w, h, 120, 3, seed + 17) - 0.5) * 0.0060
            + (fbm(w, h, 26, 2, seed + 5) - 0.5) * 0.0016) * w
    u = xx + warp

    k1 = 2.0 * math.pi / (w * 0.0295)
    v = np.sin(u * k1 + (fbm(w, h, 1100, 2, seed + 3) - 0.5) * 1.5)
    line = np.power(0.5 + 0.5 * v, 1.9)

    v2 = np.sin(u * k1 * 2.61 + 1.7 + (fbm(w, h, 60, 2, seed + 9) - 0.5) * 2.2)
    line2 = np.power(0.5 + 0.5 * v2, 3.4)

    # 纵向包络: 真实木材同一根纹理会时深时浅, 不会通体一样重
    env = fbm(w, h, 260, 2, seed + 131)
    env = 0.42 + 0.85 * env
    line = line * env
    line2 = line2 * (0.55 + 0.75 * env)

    base = np.array(st.base, dtype=np.float32)
    dark = np.array(st.grain_dark, dtype=np.float32)
    light = np.array(st.grain_light, dtype=np.float32)

    # 年轮暗线: 用较深的颜色压下去, 但保留一点亮度变化
    t = (line * 0.80 + line2 * 0.20) * st.grain_strength
    t = np.clip(t, 0, 1)[..., None]
    grain_col = dark * 0.55 + light * 0.45
    col = base[None, None, :] * (1 - t) + grain_col[None, None, :] * t

    # 大块斑驳 (木材天然色差)
    macro = fbm(w, h, 520, 2, seed + 61)
    col *= (0.945 + 0.11 * macro)[..., None]

    # 高光流动: 顺着纹路方向的柔和明暗
    sheen = fbm(w, h, 210, 2, seed + 83)
    col *= (0.975 + 0.05 * sheen)[..., None]

    # 左上柔光 + 漆面光泽
    col *= (0.955 + 0.075 * (1 - 0.45 * nx - 0.55 * ny))[..., None]
    gloss = np.exp(-(((nx - 0.26) ** 2 + (ny - 0.20) ** 2) / 0.20)) * 0.085
    col *= (1.0 + gloss)[..., None]

    # 边缘微压暗 (整块木板的厚度)
    edge = np.minimum(np.minimum(nx, 1 - nx), np.minimum(ny, 1 - ny))
    col *= (0.88 + 0.12 * _smoothstep(0.0, 0.028, edge))[..., None]

    return Image.fromarray(np.clip(col * 255, 0, 255).astype(np.uint8), "RGB")


def flat_texture(w: int, h: int, st, seed: int = 5) -> Image.Image:
    """石材/纸面: 极细颗粒 + 微渐变."""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    nx, ny = xx / max(1, w - 1), yy / max(1, h - 1)
    n = fbm(w, h, 90, 3, seed)
    fine = fbm(w, h, 2.4, 2, seed + 9)
    base = np.array(st.base, dtype=np.float32)
    col = base * (0.94 + 0.10 * n + 0.035 * (fine - 0.5))[..., None]
    col *= (0.95 + 0.09 * (1 - 0.5 * nx - 0.5 * ny))[..., None]
    return Image.fromarray(np.clip(col * 255, 0, 255).astype(np.uint8), "RGB")


# --------------------------------------------------------------------------- #
# 背景
# --------------------------------------------------------------------------- #
def backdrop(w: int, h: int, bgd, seed: int = 3) -> Image.Image:
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    nx, ny = xx / max(1, w - 1), yy / max(1, h - 1)

    top = np.array(bgd.top, dtype=np.float32)
    bot = np.array(bgd.bottom, dtype=np.float32)
    g = _smoothstep(0.0, 1.0, ny * 0.92 + 0.06 * np.sin(nx * 2.2))
    col = top[None, None, :] * (1 - g[..., None]) + bot[None, None, :] * g[..., None]

    # 主光晕
    aspect = w / max(1, h)
    dx = (nx - bgd.glow_x) * aspect
    dy = ny - bgd.glow_y
    d = np.sqrt(dx * dx + dy * dy)
    glow = np.exp(-np.power(d / 0.62, 2.0)) * bgd.glow_strength
    col += np.array(bgd.glow, dtype=np.float32)[None, None, :] * glow[..., None]

    # 次级冷光(右下), 让画面有层次
    dx2 = (nx - 0.92) * aspect
    dy2 = ny - 0.95
    d2 = np.sqrt(dx2 * dx2 + dy2 * dy2)
    glow2 = np.exp(-np.power(d2 / 0.55, 2.0)) * bgd.glow_strength * 0.30
    col += np.array(bgd.glow, dtype=np.float32)[None, None, :] * 0.55 * glow2[..., None]

    # 暗角
    vx, vy = (nx - 0.5) * 2, (ny - 0.5) * 2
    r = np.sqrt(vx * vx * 0.85 + vy * vy) / 1.35
    vig = 1.0 - bgd.vignette * _smoothstep(0.42, 1.05, r)
    col *= vig[..., None]

    # 颗粒 / 纸纤维
    if bgd.noise > 0:
        grain = fbm(w, h, 3.2, 2, seed + 5) - 0.5
        col += (grain * bgd.noise * 1.7)[..., None]
    if bgd.paper > 0:
        fiber = fbm(w, h, 1.8, 2, seed + 11) - 0.5
        web = fbm(w, h, 130, 3, seed + 23) - 0.5
        col += ((fiber * 0.020 + web * 0.030) * bgd.paper)[..., None]
        col *= (1.0 - 0.03 * bgd.paper)

    return Image.fromarray(np.clip(col * 255, 0, 255).astype(np.uint8), "RGB")


# --------------------------------------------------------------------------- #
# 棋子 (球面光照)
# --------------------------------------------------------------------------- #
def _norm3(v: Sequence[float]) -> Tuple[float, float, float]:
    l = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2) or 1.0
    return (v[0] / l, v[1] / l, v[2] / l)


@lru_cache(maxsize=64)
def stone_sprite(radius: int, style: StoneStyle, ss: int = 3,
                 shadow: bool = True) -> Image.Image:
    """生成带柔和投影的棋子 RGBA 贴图, ss 倍超采样后缩小."""
    R = max(4, int(round(radius * ss)))
    pad = int(round(R * 0.34)) if shadow else int(round(R * 0.08))
    size = 2 * R + 2 * pad
    c = size / 2.0

    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    nx = (xx - c + 0.5) / R
    ny = (yy - c + 0.5) / R
    d2 = nx * nx + ny * ny
    nz = np.sqrt(np.clip(1.0 - d2, 0.0, 1.0))
    dist = np.sqrt(d2)
    alpha = np.clip((1.0 - dist) * R + 0.5, 0.0, 1.0)

    albedo = np.array(style.base, dtype=np.float32)

    # --- 主光: 左上偏前 ---
    L = _norm3((-0.40, -0.52, 0.75))
    ndl = np.clip(nx * L[0] + ny * L[1] + nz * L[2], 0.0, 1.0)

    # 半球环境光(上方更亮) + 边缘补光
    hemi = 0.55 + 0.45 * (-ny)
    ambient = style.ambient * (0.72 + 0.38 * hemi)
    col = albedo[None, None, :] * (ambient[..., None] + (style.diffuse * ndl)[..., None])

    # --- 高光 (Blinn-Phong) ---
    V = (0.0, 0.0, 1.0)
    Hv = _norm3((L[0] + V[0], L[1] + V[1], L[2] + V[2]))
    ndh = np.clip(nx * Hv[0] + ny * Hv[1] + nz * Hv[2], 0.0, 1.0)
    # 小棋子上高光要相对更大, 否则缩到 13px 就只剩一个像素点
    rscale = (radius / 34.0) ** 0.75
    pw = max(6.0, style.spec_power * min(1.0, rscale))
    spec = np.power(ndh, pw) * style.spec
    spec2 = np.power(ndh, 5.0 + 3.0 * min(1.0, rscale)) * style.spec2 * (1.35 if radius < 20 else 1.0)
    col += (spec + spec2)[..., None] * np.array([1.0, 0.99, 0.96], dtype=np.float32)

    # 第二点光(右下弱反光), 让球体不至于死板
    L2 = _norm3((0.55, 0.62, 0.56))
    ndl2 = np.clip(nx * L2[0] + ny * L2[1] + nz * L2[2], 0.0, 1.0)
    H2 = _norm3((L2[0], L2[1], L2[2] + 1.0))
    ndh2 = np.clip(nx * H2[0] + ny * H2[1] + nz * H2[2], 0.0, 1.0)
    col += (np.power(ndh2, 40.0) * 0.16 + np.power(ndl2, 2.0) * 0.045)[..., None] * albedo

    # --- 边缘光 (菲涅尔) ---
    fres = np.power(1.0 - nz, 3.2)
    col += (fres * style.rim)[..., None] * np.array(style.rim_color, dtype=np.float32)

    # --- 底部反光(桌面/棋盘反射) ---
    if style.sheen > 0:
        low = np.clip(ny, 0.0, 1.0) ** 2.2
        col += (low * style.sheen)[..., None] * np.array(style.sheen_color, dtype=np.float32)

    # --- 石材细纹 ---
    if style.grain > 0:
        ang = np.arctan2(ny, nx)
        bands = 0.5 + 0.5 * np.sin(ang * 11.0 + dist * 6.0)
        col *= (1.0 - style.grain * bands * (1.0 - nz) * 2.0)[..., None]

    rgb = np.clip(col * 255.0, 0, 255).astype(np.uint8)
    a = (alpha * 255.0).astype(np.uint8)
    stone = Image.fromarray(np.dstack([rgb, a]), "RGBA")

    if not shadow:
        return stone.resize((size // ss, size // ss), Image.LANCZOS)

    # --- 投影 ---
    sh_alpha = stone.split()[3].filter(ImageFilter.GaussianBlur(radius=R * 0.085))
    arr = np.asarray(sh_alpha, dtype=np.float32) * 0.52
    sh = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "L")
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    shadow_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    shadow_layer.putalpha(sh)
    canvas.alpha_composite(shadow_layer, (int(R * 0.13), int(R * 0.17)))
    canvas.alpha_composite(stone)
    return canvas.resize((size // ss, size // ss), Image.LANCZOS)


def glow_sprite(radius: int, color: RGB, strength: float = 1.0,
                ss: int = 2) -> Image.Image:
    R = max(4, int(radius * ss))
    size = 2 * R
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    c = size / 2.0
    d = np.sqrt((xx - c) ** 2 + (yy - c) ** 2) / R
    a = np.exp(-np.power(d / 0.62, 2.0)) * strength
    arr = np.dstack([
        np.full((size, size), color[0] * 255, dtype=np.float32),
        np.full((size, size), color[1] * 255, dtype=np.float32),
        np.full((size, size), color[2] * 255, dtype=np.float32),
        np.clip(a, 0, 1) * 255,
    ]).astype(np.uint8)
    return Image.fromarray(arr, "RGBA").resize((size // ss, size // ss), Image.LANCZOS)


# --------------------------------------------------------------------------- #
# 阴影 / 圆角
# --------------------------------------------------------------------------- #
def drop_shadow(size: Tuple[int, int], radius: float, blur: float,
                spread: float = 0.0, opacity: float = 0.55) -> Image.Image:
    w, h = size
    m = int(blur * 3)
    canvas = Image.new("L", (w + 2 * m, h + 2 * m), 0)
    d = ImageDraw.Draw(canvas)
    d.rounded_rectangle([m - spread, m - spread, w + m + spread, h + m + spread],
                        radius=radius + spread, fill=int(255 * opacity))
    canvas = canvas.filter(ImageFilter.GaussianBlur(blur))
    out = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    out.putalpha(canvas)
    return out


# --------------------------------------------------------------------------- #
# 文字
# --------------------------------------------------------------------------- #
def text_size(txt: str, fnt, spacing: float = 0.0) -> Tuple[int, int]:
    if not txt:
        return 0, 0
    bbox = fnt.getbbox(txt)
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]
    if spacing:
        w += int(spacing * (len(txt) - 1))
    return w, h


def draw_text(draw: ImageDraw.ImageDraw, xy: Tuple[float, float], txt: str, fnt,
              fill, anchor: str = "la", spacing: float = 0.0,
              shadow: Optional[Tuple[int, int, int, int]] = None) -> Tuple[int, int]:
    """支持字间距的文本绘制. anchor 支持 l/m/r + a/m/s/b/d."""
    if not txt:
        return 0, 0
    w, h = text_size(txt, fnt, spacing)
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

    if spacing <= 0:
        if shadow:
            draw.text((x + 1, y + 2), txt, font=fnt, fill=shadow)
        draw.text((x, y), txt, font=fnt, fill=fill)
        return w, h

    cx = x
    asc, desc = fnt.getmetrics()
    for ch in txt:
        cw = fnt.getlength(ch)
        if shadow:
            draw.text((cx + 1, y + 2), ch, font=fnt, fill=shadow)
        draw.text((cx, y), ch, font=fnt, fill=fill)
        cx += cw + spacing
    return w, h


def wrap_text(txt: str, fnt, max_width: float) -> List[str]:
    """中英混排折行: 中文按字断, 英文按词断."""
    txt = (txt or "").replace("\r\n", "\n").replace("\r", "\n")
    lines: List[str] = []
    for para in txt.split("\n"):
        if not para:
            lines.append("")
            continue
        cur = ""
        i = 0
        while i < len(para):
            ch = para[i]
            # 英文单词整体计算
            if ch.isascii() and (ch.isalnum() or ch in "'-"):
                j = i
                while j < len(para) and para[j].isascii() and (para[j].isalnum() or para[j] in "'-"):
                    j += 1
                token = para[i:j]
            else:
                token = ch
                j = i + 1
            trial = cur + token
            if fnt.getlength(trial) <= max_width or not cur:
                cur = trial
            else:
                lines.append(cur.rstrip())
                cur = token.lstrip()
            i = j
        if cur:
            lines.append(cur.rstrip())
    return lines


def fit_font(kind: str, txt: str, max_width: float, start: int,
             min_size: int = 10, spacing: float = 0.0):
    size = start
    while size > min_size:
        f = font(kind, size)
        w, _ = text_size(txt, f, spacing * size)
        if w <= max_width:
            return f, size
        size -= max(1, size // 24)
    return font(kind, min_size), min_size


def ellipse_soft(size: int, blur: float) -> Image.Image:
    img = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(img)
    d.ellipse([blur * 1.5, blur * 1.5, size - blur * 1.5, size - blur * 1.5], fill=255)
    return img.filter(ImageFilter.GaussianBlur(blur))
