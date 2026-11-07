"""主题系统: 配色 / 材质 / 字体."""
from __future__ import annotations

import os
import platform
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Dict, Optional, Tuple

RGB = Tuple[float, float, float]


def hx(s: str) -> RGB:
    s = s.lstrip("#")
    return (int(s[0:2], 16) / 255.0, int(s[2:4], 16) / 255.0, int(s[4:6], 16) / 255.0)


def mix(a: RGB, b: RGB, t: float) -> RGB:
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t)


def shade(c: RGB, f: float) -> RGB:
    return (max(0.0, min(1.0, c[0] * f)), max(0.0, min(1.0, c[1] * f)), max(0.0, min(1.0, c[2] * f)))


# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class StoneStyle:
    base: RGB
    ambient: float = 0.10
    diffuse: float = 0.55
    spec: float = 0.80
    spec_power: float = 70.0
    spec2: float = 0.10
    rim: float = 0.25
    rim_color: RGB = (0.6, 0.65, 0.8)
    sheen: float = 0.06
    sheen_color: RGB = (1.0, 0.95, 0.85)
    grain: float = 0.0          # 石材细纹强度


@dataclass
class BoardStyle:
    wood: bool = True
    base: RGB = (0.86, 0.68, 0.40)
    grain_dark: RGB = (0.62, 0.44, 0.24)
    grain_light: RGB = (0.95, 0.82, 0.58)
    grain_strength: float = 0.55
    line: RGB = (0.16, 0.11, 0.07)
    line_alpha: int = 225
    star: RGB = (0.12, 0.08, 0.05)
    coord: RGB = (0.95, 0.88, 0.76)
    coord_on_board: bool = False
    border: RGB = (0.55, 0.38, 0.20)
    shadow_strength: float = 0.55
    edge_lift: float = 0.10


@dataclass
class Backdrop:
    top: RGB = (0.10, 0.075, 0.06)
    bottom: RGB = (0.045, 0.033, 0.028)
    glow: RGB = (0.55, 0.38, 0.20)
    glow_strength: float = 0.30
    glow_x: float = 0.30
    glow_y: float = 0.22
    vignette: float = 0.55
    noise: float = 0.022
    paper: float = 0.0


@dataclass
class Theme:
    key: str
    label: str
    dark: bool
    bg: Backdrop
    brd: BoardStyle
    black: StoneStyle
    white: StoneStyle
    text: RGB = (0.94, 0.90, 0.84)
    dim: RGB = (0.66, 0.60, 0.53)
    faint: RGB = (0.40, 0.36, 0.32)
    accent: RGB = (0.85, 0.68, 0.33)
    accent2: RGB = (0.62, 0.76, 0.92)
    card: RGB = (1.0, 1.0, 1.0)
    card_alpha: int = 14
    hair: RGB = (1.0, 1.0, 1.0)
    hair_alpha: int = 26


# --------------------------------------------------------------------------- #
THEMES: Dict[str, Theme] = {}


def _add(t: Theme) -> None:
    THEMES[t.key] = t


_add(Theme(
    key="classic", label="榧木经典", dark=True,
    bg=Backdrop(top=hx("#241812"), bottom=hx("#0C0806"), glow=hx("#C98A3C"),
                glow_strength=0.34, glow_x=0.28, glow_y=0.18, vignette=0.62, noise=0.020),
    brd=BoardStyle(wood=True, base=hx("#E6BC74"), grain_dark=hx("#A9773C"),
                   grain_light=hx("#F6DCA8"), grain_strength=0.55,
                   line=hx("#2A1B0E"), line_alpha=235, star=hx("#1C1108"),
                   coord=hx("#E8D3AF"), border=hx("#8A5A2B")),
    black=StoneStyle(base=hx("#1A1A20"), ambient=0.030, diffuse=0.46, spec=0.78,
                     spec_power=150, spec2=0.055, rim=0.30, rim_color=hx("#8FA6C8"),
                     sheen=0.045, sheen_color=hx("#FFE9C8")),
    white=StoneStyle(base=hx("#F4F0E6"), ambient=0.82, diffuse=0.20, spec=0.34,
                     spec_power=22, spec2=0.10, rim=0.13, rim_color=hx("#FFFFFF"),
                     sheen=0.11, sheen_color=hx("#E8D5B4"), grain=0.025),
    text=hx("#F6EDDC"), dim=hx("#CBB99B"), faint=hx("#9C8A6E"),
    accent=hx("#D9A94F"), accent2=hx("#8FB3D9"),
    card=hx("#FFFFFF"), card_alpha=10, hair=hx("#FFE6BF"), hair_alpha=30,
))

_add(Theme(
    key="midnight", label="暗夜极光", dark=True,
    bg=Backdrop(top=hx("#0A1424"), bottom=hx("#04070E"), glow=hx("#1E5FA8"),
                glow_strength=0.40, glow_x=0.72, glow_y=0.14, vignette=0.72, noise=0.016),
    brd=BoardStyle(wood=False, base=hx("#1B2432"), grain_dark=hx("#151C27"),
                   grain_light=hx("#232E3F"), grain_strength=0.30,
                   line=hx("#5D7B9E"), line_alpha=190, star=hx("#8FB4DE"),
                   coord=hx("#7E97B4"), border=hx("#2E3D52")),
    black=StoneStyle(base=hx("#0B0F17"), ambient=0.03, diffuse=0.50, spec=0.90,
                     spec_power=120, spec2=0.08, rim=0.62, rim_color=hx("#5AA9FF"),
                     sheen=0.03, sheen_color=hx("#7FC4FF")),
    white=StoneStyle(base=hx("#DDE8F7"), ambient=0.74, diffuse=0.30, spec=0.72,
                     spec_power=45, spec2=0.16, rim=0.24, rim_color=hx("#9ED4FF"),
                     sheen=0.16, sheen_color=hx("#79B8FF")),
    text=hx("#EAF2FC"), dim=hx("#A6BAD0"), faint=hx("#74879E"),
    accent=hx("#5FB2FF"), accent2=hx("#8CE0FF"),
    card=hx("#FFFFFF"), card_alpha=8, hair=hx("#8FC6FF"), hair_alpha=26,
))

_add(Theme(
    key="paper", label="素纸禅意", dark=False,
    bg=Backdrop(top=hx("#F6F2E7"), bottom=hx("#E4DCC9"), glow=hx("#FFFFFF"),
                glow_strength=0.36, glow_x=0.30, glow_y=0.12, vignette=0.30,
                noise=0.014, paper=0.55),
    brd=BoardStyle(wood=True, base=hx("#EFE4CC"), grain_dark=hx("#D8C6A4"),
                   grain_light=hx("#FBF4E5"), grain_strength=0.22,
                   line=hx("#5B5344"), line_alpha=205, star=hx("#4A4335"),
                   coord=hx("#6E6553"), border=hx("#C9B896")),
    black=StoneStyle(base=hx("#1E1C1A"), ambient=0.05, diffuse=0.45, spec=0.70,
                     spec_power=90, spec2=0.08, rim=0.22, rim_color=hx("#A9A296"),
                     sheen=0.04, sheen_color=hx("#FFF6E4")),
    white=StoneStyle(base=hx("#FCFAF4"), ambient=0.86, diffuse=0.18, spec=0.42,
                     spec_power=30, spec2=0.10, rim=0.10, rim_color=hx("#FFFFFF"),
                     sheen=0.08, sheen_color=hx("#E9DFC8"), grain=0.02),
    text=hx("#2B2721"), dim=hx("#5C5346"), faint=hx("#8A8070"),
    accent=hx("#94341F"), accent2=hx("#3F5F4B"),
    card=hx("#000000"), card_alpha=8, hair=hx("#5B5344"), hair_alpha=34,
))

_add(Theme(
    key="ink", label="水墨留白", dark=False,
    bg=Backdrop(top=hx("#FBFAF7"), bottom=hx("#EDEAE1"), glow=hx("#E8E4D8"),
                glow_strength=0.34, glow_x=0.5, glow_y=0.0, vignette=0.24,
                noise=0.012, paper=0.75),
    brd=BoardStyle(wood=False, base=hx("#EDE9DC"), grain_dark=hx("#D5CFBC"),
                   grain_light=hx("#FAF8F2"), grain_strength=0.26,
                   line=hx("#33302A"), line_alpha=190, star=hx("#26231D"),
                   coord=hx("#6E6759"), border=hx("#B9B2A0")),
    black=StoneStyle(base=hx("#121212"), ambient=0.04, diffuse=0.38, spec=0.55,
                     spec_power=80, spec2=0.06, rim=0.16, rim_color=hx("#9A958C"),
                     sheen=0.03, sheen_color=hx("#FFFFFF")),
    white=StoneStyle(base=hx("#FFFFFF"), ambient=0.88, diffuse=0.16, spec=0.34,
                     spec_power=26, spec2=0.07, rim=0.24, rim_color=hx("#7E7970"),
                     sheen=0.06, sheen_color=hx("#E5E0D0")),
    text=hx("#22201C"), dim=hx("#514C41"), faint=hx("#837D6E"),
    accent=hx("#9B2F20"), accent2=hx("#33513F"),
    card=hx("#000000"), card_alpha=6, hair=hx("#3A3730"), hair_alpha=26,
))

_add(Theme(
    key="snow", label="雪地青瓷", dark=False,
    bg=Backdrop(top=hx("#DCE4EA"), bottom=hx("#C4CED6"), glow=hx("#FFFFFF"),
                glow_strength=0.32, glow_x=0.32, glow_y=0.14, vignette=0.30,
                noise=0.016, paper=0.18),
    brd=BoardStyle(wood=False, base=hx("#7FA0A8"), grain_dark=hx("#5F848C"),
                   grain_light=hx("#A8C4C8"), grain_strength=0.28,
                   line=hx("#0E242C"), line_alpha=240, star=hx("#081C24"),
                   coord=hx("#E8F4F4"), border=hx("#4A6A74")),
    black=StoneStyle(base=hx("#0E161E"), ambient=0.04, diffuse=0.52, spec=0.88,
                     spec_power=120, spec2=0.10, rim=0.30, rim_color=hx("#6AB0C0"),
                     sheen=0.05, sheen_color=hx("#D8ECF4")),
    white=StoneStyle(base=hx("#F4F8F8"), ambient=0.78, diffuse=0.24, spec=0.48,
                     spec_power=30, spec2=0.14, rim=0.18, rim_color=hx("#C8D8D8"),
                     sheen=0.10, sheen_color=hx("#B0C8C8"), grain=0.02),
    text=hx("#0E1C24"), dim=hx("#243E48"), faint=hx("#3E5864"),
    accent=hx("#0E5A6C"), accent2=hx("#1A4A6C"),
    card=hx("#000000"), card_alpha=12, hair=hx("#122A34"), hair_alpha=40,
))

_add(Theme(
    key="sakura", label="绯樱夜宴", dark=True,
    bg=Backdrop(top=hx("#1E1218"), bottom=hx("#0E080C"), glow=hx("#C05A70"),
                glow_strength=0.26, glow_x=0.72, glow_y=0.16, vignette=0.70,
                noise=0.014, paper=0.0),
    brd=BoardStyle(wood=True, base=hx("#D8C0B4"), grain_dark=hx("#A88878"),
                   grain_light=hx("#F0E0D4"), grain_strength=0.38,
                   line=hx("#3A1C14"), line_alpha=225, star=hx("#2A120C"),
                   coord=hx("#6A483C"), border=hx("#7A5248")),
    black=StoneStyle(base=hx("#181014"), ambient=0.03, diffuse=0.50, spec=0.86,
                     spec_power=140, spec2=0.06, rim=0.30, rim_color=hx("#C87A8A"),
                     sheen=0.04, sheen_color=hx("#F0C8D0")),
    white=StoneStyle(base=hx("#F6EEEA"), ambient=0.82, diffuse=0.22, spec=0.42,
                     spec_power=26, spec2=0.10, rim=0.20, rim_color=hx("#D8A8B0"),
                     sheen=0.10, sheen_color=hx("#E8C8C0"), grain=0.02),
    text=hx("#F4E6E2"), dim=hx("#C0A098"), faint=hx("#907068"),
    accent=hx("#D06878"), accent2=hx("#E8A8B0"),
    card=hx("#FFFFFF"), card_alpha=10, hair=hx("#D8A8B0"), hair_alpha=26,
))


def get_theme(name: str) -> Theme:
    key = (name or "classic").lower()
    if key not in THEMES:
        raise KeyError(f"未知主题 '{name}', 可选: {', '.join(THEMES)}")
    return THEMES[key]


# --------------------------------------------------------------------------- #
# 字体
# --------------------------------------------------------------------------- #
_SERIF_CANDIDATES = [
    ("/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc", 2),
    ("/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttf", 0),
    ("/usr/share/fonts/truetype/noto/NotoSerifCJK-Regular.ttc", 2),
    ("/System/Library/Fonts/Supplemental/Songti.ttc", 0),
    ("/System/Library/Fonts/STHeiti Light.ttc", 0),
    ("C:/Windows/Fonts/simsun.ttc", 0),
    ("C:/Windows/Fonts/STSONG.TTF", 0),
]
_SERIF_BOLD = [
    ("/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc", 2),
    ("/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttf", 0),
    ("C:/Windows/Fonts/simsunb.ttf", 0),
    ("/System/Library/Fonts/Supplemental/Songti.ttc", 1),
]
_SANS_CANDIDATES = [
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 2),
    ("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc", 2),
    ("/System/Library/Fonts/PingFang.ttc", 0),
    ("/System/Library/Fonts/Helvetica.ttc", 0),
    ("C:/Windows/Fonts/msyh.ttc", 0),
    ("C:/Windows/Fonts/arial.ttf", 0),
]
_SANS_BOLD = [
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", 2),
    ("/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc", 2),
    ("C:/Windows/Fonts/msyhbd.ttc", 0),
    ("/System/Library/Fonts/PingFang.ttc", 0),
]
_LATIN = [
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 0),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf", 0),
    ("C:/Windows/Fonts/arial.ttf", 0),
    ("/System/Library/Fonts/Helvetica.ttc", 0),
]


def _first_existing(cands) -> Optional[Tuple[str, int]]:
    for p, idx in cands:
        if os.path.exists(p):
            return p, idx
    return None


@lru_cache(maxsize=256)
def font(kind: str, size: int):
    from PIL import ImageFont

    table = {
        "serif": _SERIF_CANDIDATES,
        "serif_bold": _SERIF_BOLD,
        "sans": _SANS_CANDIDATES,
        "sans_bold": _SANS_BOLD,
    }
    cand = _first_existing(table.get(kind, _SANS_CANDIDATES)) or _first_existing(_LATIN)
    if cand is None:
        return ImageFont.load_default()
    path, idx = cand
    try:
        return ImageFont.truetype(path, size, index=idx)
    except Exception:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            return ImageFont.load_default()


def has_cjk_font() -> bool:
    return _first_existing(_SANS_CANDIDATES) is not None or _first_existing(_SERIF_CANDIDATES) is not None
