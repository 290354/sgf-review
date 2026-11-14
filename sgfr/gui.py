"""轻量桌面图形界面 — 液态玻璃视觉 (Liquid Glass).

设计取舍:
  * 背景由 Pillow 预渲染: 深空渐变 + 三团柔光 + 细噪点;
  * 每张卡片用「裁切背景 -> 高斯模糊 -> 提亮 -> 内高光描边」生成
    真实的毛玻璃材质 (backdrop blur), 而不是半透明色块;
  * 输入框 / 按钮 / 勾选 / 滑块 / 进度条全部自绘圆角, 避开 Windows
    原生控件的方角、3D 边框与浅色滚动条;
  * 界面进程只加载 tkinter + Pillow, 不加载 numpy;
    渲染仍在 --cli 子进程执行, 界面主线程始终空闲.
"""
from __future__ import annotations

import math
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageTk

from .presets import PRESET_DESC, PRESETS, SIZE_PRESETS
from . import __version__ as _VER

# --------------------------------------------------------------------------- #
# 调色板 (液态玻璃: 冷调深空 + 冰蓝主色 + 一点暖金)
# --------------------------------------------------------------------------- #
INK = "#05080E"
INK2 = "#0A111B"
PANE = "#111823"          # 卡片内部近似色 (与毛玻璃中心一致, 边界不可见)
FIELD = "#0B1119"         # 输入框底色
TEXT = "#F2F7FF"
DIM = "#93A7BF"
FAINT = "#5D7088"
ICE = "#CFE8FF"
ACCENT = "#5AC8FF"
ACCENT2 = "#7C8CFF"
WARM = "#FFC27A"
GOOD = "#6FE3B0"

THEME_LABELS = (
    ("classic", "榧木经典"), ("midnight", "暗夜极光"),
    ("paper", "素纸禅意"), ("ink", "水墨留白"),
    ("snow", "雪地青瓷"), ("sakura", "绯樱夜宴"),
)

# 预设里界面未直接暴露的键 -> 命令行开关
EXTRA_FLAGS = {
    "labels": "--labels",
    "label_count": "--label-count",
    "intro": "--intro",
    "outro": "--outro",
    "no_intro": "--no-intro",
    "no_outro": "--no-outro",
    "no_audio": "--no-audio",
}

_FF = "Microsoft YaHei UI"
_FM = "Consolas"

_R = Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS


# --------------------------------------------------------------------------- #
# 系统适配
# --------------------------------------------------------------------------- #
def _dpi_aware() -> None:
    """Windows 高分屏下声明 per-monitor DPI 感知, 否则整窗被系统拉伸发虚."""
    if os.name != "nt":
        return
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def _win_chrome(root: tk.Tk) -> None:
    """Windows 11: 深色标题栏 + 圆角窗口, 贴近 macOS 观感."""
    if os.name != "nt":
        return
    try:
        import ctypes
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        one = ctypes.c_int(1)
        # DWMWA_USE_IMMERSIVE_DARK_MODE = 20
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 20, ctypes.byref(one), ctypes.sizeof(one))
        # DWMWA_WINDOW_CORNER_PREFERENCE = 33, DWMWCP_ROUND = 2
        pref = ctypes.c_int(2)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 33, ctypes.byref(pref), ctypes.sizeof(pref))
    except Exception:
        pass


def _hide_console() -> None:
    """Windows 控制台版 exe 启动 GUI 时把黑框藏起来 (CLI 模式仍需 stdout)."""
    if os.name == "nt" and getattr(sys, "frozen", False):
        try:
            import ctypes
            hwnd = ctypes.windll.kernel32.GetConsoleWindow()
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 0)
        except Exception:
            pass


def _pick_font(root: tk.Tk) -> None:
    global _FF, _FM
    try:
        fams = set(root.tk.call("font", "families"))
    except Exception:
        fams = set()
    for name in ("Microsoft YaHei UI", "微软雅黑", "Microsoft YaHei",
                 "PingFang SC", "Noto Sans CJK SC", "WenQuanYi Micro Hei",
                 "DejaVu Sans"):
        if name in fams:
            _FF = name
            break
    for name in ("Cascadia Mono", "Consolas", "SF Mono",
                 "DejaVu Sans Mono", _FF):
        if name in fams:
            _FM = name
            break


# --------------------------------------------------------------------------- #
# 玻璃材质
# --------------------------------------------------------------------------- #
def _rgb(h: str) -> Tuple[int, int, int]:
    return (int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16))


def _radial(radius: int, color: Tuple[int, int, int], peak: int) -> Image.Image:
    r = max(8, int(radius))
    m = Image.radial_gradient("L").resize((r * 2, r * 2), _R)
    m = ImageEnhance.Brightness(m).enhance(1.0)
    m = m.point(lambda v: int((255 - v) / 255.0 * peak))
    layer = Image.new("RGBA", (r * 2, r * 2), color + (255,))
    layer.putalpha(m)
    return layer


def build_backdrop(w: int, h: int) -> Image.Image:
    """深空渐变 + 柔光斑 + 细噪点."""
    w, h = max(320, w), max(320, h)
    top, bot = _rgb("#0B131F"), _rgb("#04060B")
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    for y in range(h):
        t = y / float(h - 1)
        d.line([(0, y), (w, y)],
               fill=tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(3)))
    img = img.convert("RGBA")

    spots = [
        (w * 0.16, h * 0.02, max(w, h) * 0.62, (52, 140, 255), 58),
        (w * 0.90, h * 0.22, max(w, h) * 0.50, (118, 86, 255), 44),
        (w * 0.58, h * 1.04, max(w, h) * 0.58, (255, 166, 84), 24),
        (w * 0.02, h * 0.72, max(w, h) * 0.34, (60, 220, 210), 20),
    ]
    glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    for cx, cy, r, col, a in spots:
        g = _radial(int(r), col, a)
        glow.paste(g, (int(cx - r), int(cy - r)), g)
    img = Image.alpha_composite(img, glow)

    try:
        noise = Image.effect_noise((w, h), 10).convert("L")
        nz = Image.new("RGBA", (w, h), (255, 255, 255, 0))
        nz.putalpha(noise.point(lambda v: int(v * 0.030)))
        img = Image.alpha_composite(img, nz)
    except Exception:
        pass
    return img.convert("RGB")


def glass_panel(bg: Image.Image, box: Tuple[int, int, int, int],
                radius: int = 22, blur: int = 26, lift: float = 1.16,
                tint_a: int = 13, border_a: int = 40) -> Image.Image:
    """从背景裁一块做 backdrop blur, 再叠提亮与高光描边."""
    x0, y0, x1, y1 = [int(v) for v in box]
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(bg.width, max(x0 + 4, x1)), min(bg.height, max(y0 + 4, y1))
    w, h = x1 - x0, y1 - y0
    base = bg.crop((x0, y0, x1, y1)).filter(ImageFilter.GaussianBlur(blur))
    base = ImageEnhance.Brightness(base).enhance(lift).convert("RGBA")
    if tint_a:
        base = Image.alpha_composite(
            base, Image.new("RGBA", (w, h), (255, 255, 255, tint_a)))

    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, w - 1, h - 1), radius=radius, fill=255)
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    out.paste(base, (0, 0), mask)

    edge = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ed = ImageDraw.Draw(edge)
    ed.rounded_rectangle((0, 0, w - 1, h - 1), radius=radius,
                         outline=(255, 255, 255, border_a), width=1)
    # 顶部内侧再压一道更亮的弧线, 模拟玻璃厚度
    ed.rounded_rectangle((1, 1, w - 2, max(2, h // 2)), radius=max(4, radius - 1),
                         outline=(255, 255, 255, 22), width=1)
    out.alpha_composite(edge)
    return out


def _pill(w: int, h: int, radius: int, top: str, bottom: str,
          border: Optional[Tuple[int, int, int, int]] = None) -> Image.Image:
    """竖直渐变胶囊, 用于按钮/输入框底."""
    w, h = max(2, int(w)), max(2, int(h))
    a, b = _rgb(top), _rgb(bottom)
    grad = Image.new("RGB", (1, h))
    gd = ImageDraw.Draw(grad)
    for y in range(h):
        t = y / float(h - 1)
        gd.point((0, y), fill=tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3)))
    grad = grad.resize((w, h), _R).convert("RGBA")
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, w - 1, h - 1), radius=radius, fill=255)
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    out.paste(grad, (0, 0), mask)
    if border:
        e = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ImageDraw.Draw(e).rounded_rectangle(
            (0, 0, w - 1, h - 1), radius=radius, outline=border, width=1)
        out.alpha_composite(e)
    return out


def _round_mask(w: int, h: int, radius: int) -> Image.Image:
    m = Image.new("L", (max(2, w), max(2, h)), 0)
    ImageDraw.Draw(m).rounded_rectangle(
        (0, 0, w - 1, h - 1), radius=radius, fill=255)
    return m


# --------------------------------------------------------------------------- #
# 自绘控件
# --------------------------------------------------------------------------- #
class GlassButton(tk.Canvas):
    def __init__(self, master, text: str, command, kind: str = "ghost",
                 width: int = 104, height: int = 34, font=None, bg=PANE):
        super().__init__(master, width=width, height=height, bg=bg,
                         highlightthickness=0, bd=0, cursor="hand2")
        self._text = text
        self._cmd = command
        self._kind = kind
        self._cw, self._ch = width, height
        self._font = font
        self._enabled = True
        self._hover = False
        self._imgs: Dict[bool, ImageTk.PhotoImage] = {}
        self._draw()
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)

    def _build(self, hover: bool) -> ImageTk.PhotoImage:
        w, h, r = self._cw, self._ch, self._ch // 2
        if self._kind == "primary":
            top = "#7ADCFF" if hover else "#5AC8FF"
            bot = "#2E7BFF" if hover else "#3B62E8"
            img = _pill(w, h, r, top, bot,
                        border=(255, 255, 255, 90 if hover else 70))
        else:
            a = 34 if hover else 22
            img = _pill(w, h, r, "#2A3646", "#1B2532",
                        border=(255, 255, 255, a + 20))
        return ImageTk.PhotoImage(img)

    def _draw(self) -> None:
        self.delete("all")
        if self._enabled:
            key = self._hover
            if key not in self._imgs:
                self._imgs[key] = self._build(key)
            img = self._imgs[key]
        else:
            img = self._imgs.setdefault(False, self._build(False))
        self.create_image(0, 0, image=img, anchor="nw")
        fg = "#07121C" if self._kind == "primary" else TEXT
        if not self._enabled:
            fg = FAINT
        self.create_text(self._cw / 2, self._ch / 2 + 1, text=self._text,
                         fill=fg, font=self._font or (_FF, 9, "bold"))

    def _on_enter(self, _e=None):
        if self._enabled:
            self._hover = True
            self._draw()

    def _on_leave(self, _e=None):
        if self._enabled:
            self._hover = False
            self._draw()

    def _on_click(self, _e=None):
        if self._enabled and self._cmd:
            self._cmd()

    def set_enabled(self, ok: bool) -> None:
        self._enabled = bool(ok)
        self.configure(cursor="hand2" if ok else "arrow")
        self._draw()

    def set_text(self, text: str) -> None:
        self._text = text
        self._draw()


class GlassCheck(tk.Canvas):
    def __init__(self, master, text: str, variable: tk.BooleanVar,
                 font=None, bg=PANE):
        self._var = variable
        self._label = text
        self._font = font
        super().__init__(master, height=24, bg=bg, highlightthickness=0,
                         bd=0, cursor="hand2")
        self._box = 17
        tw = tkfont.Font(font=font or (_FF, 9)).measure(text)
        self.configure(width=self._box + 8 + tw + 4)
        self._draw()
        self.bind("<Button-1>", self._toggle)
        self.bind("<Enter>", lambda e: self._draw(hover=True))
        self.bind("<Leave>", lambda e: self._draw(hover=False))
        self._var.trace_add("write", lambda *_: self._draw())

    def _draw(self, hover: bool = False) -> None:
        self.delete("all")
        b, y = self._box, 2
        on = bool(self._var.get())
        if on:
            img = _pill(b, b, 5, "#7ADCFF", "#3B62E8",
                        border=(255, 255, 255, 160))
            self.create_image(0, y, image=self._img(img, "on", b, b, 5), anchor="nw")
            self.create_line(4.2, y + b * 0.52, b * 0.44, y + b - 4.6,
                             b - 4.0, y + 4.6, fill="#06121C", width=2,
                             capstyle="round", joinstyle="round")
        else:
            img = _pill(b, b, 5, "#1A2432", "#141C28",
                        border=(255, 255, 255, 70 if hover else 50))
            self.create_image(0, y, image=self._img(img, "off", b, b, 5), anchor="nw")
        self.create_text(b + 8, y + b / 2 + 1, text=self._label, anchor="w",
                         fill=TEXT if on else DIM,
                         font=self._font or (_FF, 9))

    def _img(self, img: Image.Image, tag: str, w: int, h: int, r: int):
        key = f"_ck_{tag}_{w}_{h}"
        photo = getattr(self, key, None)
        if photo is None:
            photo = ImageTk.PhotoImage(img)
            setattr(self, key, photo)
        return photo

    def _toggle(self, _e=None):
        self._var.set(not self._var.get())


class GlassSlider(tk.Canvas):
    def __init__(self, master, variable: tk.DoubleVar, lo: float, hi: float,
                 width: int = 220, font=None, bg=PANE):
        super().__init__(master, width=width, height=26, bg=bg,
                         highlightthickness=0, bd=0, cursor="hand2")
        self._var = variable
        self._lo, self._hi = lo, hi
        self._cw = width
        self._font = font
        self._drag = False
        self._draw()
        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_move)
        self.bind("<ButtonRelease-1>", self._on_release)
        self._var.trace_add("write", lambda *_: self._draw())

    def _knob_x(self) -> float:
        v = float(self._var.get())
        t = (v - self._lo) / max(1e-6, self._hi - self._lo)
        t = max(0.0, min(1.0, t))
        return 12 + t * (self._cw - 46)

    def _draw(self) -> None:
        self.delete("all")
        y = 14
        x0, x1 = 12, self._cw - 34
        kx = self._knob_x()
        self.create_line(x0, y, x1, y, fill="#1C2634", width=4,
                         capstyle="round")
        if kx > x0 + 1:
            self.create_line(x0, y, kx, y, fill=ACCENT, width=4,
                             capstyle="round")
            self.create_line(x0, y, kx, y, fill="#9BE3FF", width=1,
                             capstyle="round")
        self.create_oval(kx - 9, y - 9, kx + 9, y + 9,
                         fill=ACCENT, outline="")
        self.create_oval(kx - 6, y - 6, kx + 6, y + 6,
                         fill="#EAF8FF", outline="")
        self.create_text(self._cw - 2, 13, anchor="e",
                         text=f"{self._var.get():.2f}", fill=DIM,
                         font=(_FM, 7))

    def _set_from_x(self, x: float) -> None:
        t = (x - 12) / max(1.0, self._cw - 46)
        t = max(0.0, min(1.0, t))
        self._var.set(round(self._lo + t * (self._hi - self._lo), 2))

    def _on_press(self, e):
        self._drag = True
        self._set_from_x(e.x)

    def _on_move(self, e):
        if self._drag:
            self._set_from_x(e.x)

    def _on_release(self, _e):
        self._drag = False


class GlassProgress(tk.Canvas):
    def __init__(self, master, width: int = 520, height: int = 10, bg=PANE):
        super().__init__(master, width=width, height=height, bg=bg,
                         highlightthickness=0, bd=0)
        self._cw, self._ch = width, height
        self._v = 0.0
        self.bind("<Configure>", self._on_cfg)
        self._draw()

    def _on_cfg(self, e) -> None:
        if e.width > 20 and abs(e.width - self._cw) > 1:
            self._cw = e.width
            self._draw()

    def _draw(self) -> None:
        self.delete("all")
        self._cw = max(self._cw, self.winfo_width() or 0)
        y = self._ch / 2
        x0, x1 = 2, self._cw - 2
        self.create_line(x0, y, x1, y, fill="#2C3D52", width=self._ch - 2,
                         capstyle="round")
        if self._v > 0.4:
            kx = x0 + (x1 - x0) * min(1.0, self._v / 100.0)
            self.create_line(x0, y, max(x0 + 2, kx), y, fill="#3B62E8",
                             width=self._ch - 2, capstyle="round")
            self.create_line(x0, y, max(x0 + 2, kx), y, fill="#6FD8FF",
                             width=max(2, self._ch - 6), capstyle="round")

    def set_value(self, v: float) -> None:
        self._v = max(0.0, min(100.0, float(v)))
        self._draw()


class RoundText(tk.Canvas):
    """圆角凹槽 + Text, 避免日志区出现直角黑块."""

    def __init__(self, master, height: int = 96, font=None, bg=PANE):
        super().__init__(master, height=height, bg=bg,
                         highlightthickness=0, bd=0)
        self._ch = height
        self._img = ImageTk.PhotoImage(
            _pill(420, height, 10, "#080D14", "#080D14",
                  border=(255, 255, 255, 26)))
        self.create_image(0, 0, image=self._img, anchor="nw", tags="tbg")
        self.text = tk.Text(self, bg="#080D14", fg=DIM, relief="flat", bd=0,
                            font=font or (_FM, 8), wrap="word",
                            highlightthickness=0, insertbackground=ACCENT,
                            spacing1=1, spacing3=1, padx=0, pady=0)
        self.create_window(10, 7, window=self.text, anchor="nw",
                           width=300, height=height - 14, tags="txt")
        self.bind("<Configure>", self._on_cfg)

    def _on_cfg(self, e) -> None:
        w = max(60, e.width)
        self._img = ImageTk.PhotoImage(
            _pill(w, self._ch, 10, "#080D14", "#080D14",
                  border=(255, 255, 255, 26)))
        self.itemconfigure("tbg", image=self._img)
        self.itemconfigure("txt", width=w - 20)


class RoundEntry(tk.Canvas):
    """圆角胶囊输入框: Canvas 画底, 真 Entry 叠在上面."""

    def __init__(self, master, textvariable, width: int = 280, height: int = 30,
                 font=None, bg=PANE, show: Optional[str] = None):
        super().__init__(master, width=width, height=height, bg=bg,
                         highlightthickness=0, bd=0)
        self._cw, self._ch = width, height
        self._bg = bg
        self._focus = False
        self._img_n = ImageTk.PhotoImage(
            _pill(width, height, 9, "#0D1520", "#0A1018",
                  border=(255, 255, 255, 46)))
        self._img_f = ImageTk.PhotoImage(
            _pill(width, height, 9, "#0D1520", "#0A1018",
                  border=(90, 200, 255, 170)))
        self.create_image(0, 0, image=self._img_n, anchor="nw", tags="bgi")
        self.entry = tk.Entry(
            self, textvariable=textvariable, bg=FIELD, fg=TEXT,
            insertbackground=ACCENT, relief="flat", bd=0,
            highlightthickness=0, font=font or (_FF, 9),
            show=show or "")
        self.create_window(11, height / 2, window=self.entry, anchor="w",
                           width=width - 22, height=height - 10, tags="ent")
        self.entry.bind("<FocusIn>", lambda e: self._set_focus(True))
        self.entry.bind("<FocusOut>", lambda e: self._set_focus(False))

    def _set_focus(self, on: bool) -> None:
        self._focus = on
        self.itemconfigure("bgi", image=self._img_f if on else self._img_n)

    def get(self) -> str:
        return self.entry.get()

    def set(self, v: str) -> None:
        self.entry.delete(0, "end")
        self.entry.insert(0, v)

    def resize(self, width: int) -> None:
        self._cw = width
        self.configure(width=width)
        self._img_n = ImageTk.PhotoImage(
            _pill(width, self._ch, 9, "#0D1520", "#0A1018",
                  border=(255, 255, 255, 46)))
        self._img_f = ImageTk.PhotoImage(
            _pill(width, self._ch, 9, "#0D1520", "#0A1018",
                  border=(90, 200, 255, 170)))
        self.itemconfigure("bgi", image=self._img_n)
        self.coords("ent", 11, self._ch / 2)
        self.itemconfigure("ent", width=width - 22)


class RoundCombo(tk.Canvas):
    def __init__(self, master, textvariable, values: List[str], width: int = 140,
                 height: int = 30, font=None, bg=PANE):
        super().__init__(master, width=width, height=height, bg=bg,
                         highlightthickness=0, bd=0)
        self._img = ImageTk.PhotoImage(
            _pill(width, height, 9, "#0D1520", "#0A1018",
                  border=(255, 255, 255, 46)))
        self.create_image(0, 0, image=self._img, anchor="nw")
        self.combo = ttk.Combobox(
            self, textvariable=textvariable, values=values, state="readonly",
            style="Glass.TCombobox", font=font or (_FF, 9))
        self.create_window(10, height / 2, window=self.combo, anchor="w",
                           width=width - 34, height=height - 8)
        # 自绘小箭头 (原生箭头在深色下是浅灰三角, 不协调)
        self.create_line(width - 20, height / 2 - 2, width - 15,
                         height / 2 + 3, width - 10, height / 2 - 2,
                         fill=ACCENT, width=1.6, capstyle="round",
                         joinstyle="round")

    def get(self) -> str:
        return self.combo.get()

    def bind_select(self, cb) -> None:
        self.combo.bind("<<ComboboxSelected>>", cb)


# 让 tk.font 可用
import tkinter.font as tkfont  # noqa: E402


# --------------------------------------------------------------------------- #
class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("SGF Review · 围棋复盘工作台")
        self.root.configure(bg=INK)

        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        w = min(1040, max(920, sw - 160))
        h = max(720, min(930, sh - 120))
        self.root.geometry(f"{w}x{h}+{(sw - w) // 2}+{max(16, (sh - h) // 2 - 24)}")
        self.root.minsize(900, 700)

        self.proc: Optional[subprocess.Popen] = None
        self.q: queue.Queue = queue.Queue()
        self.extra: Dict = {}
        self.mode = "render"
        self.cancelled = False
        self.finished = True
        self.tail = 0
        self.photo = None
        self.buttons: Dict[str, GlassButton] = {}
        self.tmpdir = tempfile.mkdtemp(prefix="sgfr_gui_")
        self.preview_png = os.path.join(self.tmpdir, "preview.png")
        self._bg_img: Optional[Image.Image] = None
        self._bg_photo = None
        self._card_photos: List[ImageTk.PhotoImage] = []
        self._resize_job = None
        self._size = (0, 0)
        self._density = 0

        self._vars()
        self._style()
        self._build()
        for var in (self.v_preset, self.v_theme, self.v_size, self.v_fps,
                    self.v_duration, self.v_quality, self.v_codec, self.v_jobs,
                    self.v_out, self.v_heat, self.v_analysis, self.v_comment,
                    self.v_eco, self.v_strength):
            var.trace_add("write", lambda *_: self._update_summary())
        self._apply_preset("heat")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.bind("<Configure>", self._on_configure)
        self.root.after(60, self._relayout)
        self._fade_in(0)

    # ------------------------------------------------------------------ #
    def _vars(self) -> None:
        v = tk
        self.v_sgf = v.StringVar()
        self.v_out = v.StringVar()
        self.v_preset = v.StringVar(value="heat")
        self.v_theme = v.StringVar(value="midnight")
        self.v_size = v.StringVar(value="1080p")
        self.v_fps = v.StringVar(value="30")
        self.v_duration = v.StringVar(value="90")
        self.v_quality = v.StringVar(value="high")
        self.v_codec = v.StringVar(value="h264")
        self.v_jobs = v.StringVar(value="0")
        self.v_watermark = v.StringVar()
        self.v_strength = v.DoubleVar(value=1.0)
        self.v_heat = v.BooleanVar(value=True)
        self.v_analysis = v.BooleanVar(value=True)
        self.v_comment = v.BooleanVar(value=True)
        self.v_transition = v.BooleanVar(value=True)
        self.v_legend = v.BooleanVar(value=True)
        self.v_chapters = v.BooleanVar(value=True)
        self.v_poster = v.BooleanVar(value=False)
        self.v_skip = v.BooleanVar(value=True)
        self.v_eco = v.BooleanVar(value=True)
        self.v_status = v.StringVar(value="就绪")

    def _style(self) -> None:
        s = ttk.Style()
        try:
            s.theme_use("clam")
        except Exception:
            pass
        s.configure("Glass.TCombobox", fieldbackground=FIELD, background=FIELD,
                    foreground=TEXT, arrowcolor=FIELD, bordercolor=FIELD,
                    lightcolor=FIELD, darkcolor=FIELD, padding=0,
                    relief="flat", borderwidth=0)
        s.map("Glass.TCombobox",
              fieldbackground=[("readonly", FIELD)],
              foreground=[("readonly", TEXT)],
              selectbackground=[("readonly", FIELD)],
              selectforeground=[("readonly", TEXT)],
              background=[("readonly", FIELD)],
              arrowcolor=[("readonly", FIELD)],
              bordercolor=[("readonly", FIELD)],
              lightcolor=[("readonly", FIELD)],
              darkcolor=[("readonly", FIELD)])
        # 下拉列表 (popdown) 是原生 Listbox, 只能通过 option 数据库着色
        self.root.option_add("*TCombobox*Listbox.background", "#0F1620")
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#06121C")
        self.root.option_add("*TCombobox*Listbox.font", (_FF, 9))

    # ------------------------------------------------------------------ #
    # 骨架
    # ------------------------------------------------------------------ #
    def _build(self) -> None:
        self.cv = tk.Canvas(self.root, bg=INK, highlightthickness=0, bd=0)
        self.cv.pack(fill="both", expand=True)

        self.head = tk.Canvas(self.cv, bg=INK, highlightthickness=0, height=76)
        self._head_win = self.cv.create_window(0, 0, window=self.head,
                                              anchor="nw")
        self.head.bind("<Configure>", lambda e: self._draw_header())

        self.card1 = self._card_frame()
        self.card2 = self._card_frame()
        self.card3 = self._card_frame()
        self.side = self._card_frame()
        self.foot = self._card_frame()

        self._build_card1(self.card1)
        self._build_card2(self.card2)
        self._build_card3(self.card3)
        self._build_side(self.side)
        self._build_foot(self.foot)

    def _card_frame(self) -> tk.Frame:
        f = tk.Frame(self.cv, bg=PANE)
        return f

    def _title(self, parent, text: str, dot: str = ACCENT):
        bar = tk.Frame(parent, bg=PANE)
        bar.pack(fill="x", pady=(0, 6))
        c = tk.Canvas(bar, width=16, height=14, bg=PANE,
                      highlightthickness=0, bd=0)
        c.pack(side="left")
        c.create_oval(4, 4, 12, 12, fill=dot, outline="")
        tk.Label(bar, text=text, bg=PANE, fg=ICE,
                 font=(_FF, 10, "bold")).pack(side="left", padx=(2, 0))

    def _label(self, parent, text: str, fg: str = DIM, size: int = 9):
        return tk.Label(parent, text=text, bg=PANE, fg=fg,
                        font=(_FF, size), anchor="w", justify="left")

    # ------------------------------------------------------------------ #
    def _build_card1(self, p: tk.Frame) -> None:
        inner = self._pad(p)
        self._title(inner, "棋谱与输出")
        g = tk.Frame(inner, bg=PANE)
        g.pack(fill="x")
        g.grid_columnconfigure(1, weight=1)

        self._label(g, "棋谱").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.e_sgf = RoundEntry(g, self.v_sgf, width=420)
        self.e_sgf.grid(row=0, column=1, sticky="ew")
        GlassButton(g, "浏览", self._browse_sgf, width=58, height=30,
                    font=(_FF, 9)).grid(row=0, column=2, padx=(8, 0))

        self._label(g, "输出").grid(row=1, column=0, sticky="w",
                                    padx=(0, 8), pady=(7, 0))
        self.e_out = RoundEntry(g, self.v_out, width=420)
        self.e_out.grid(row=1, column=1, sticky="ew", pady=(7, 0))
        GlassButton(g, "浏览", self._browse_out, width=58, height=30,
                    font=(_FF, 9)).grid(row=1, column=2, padx=(8, 0),
                                        pady=(7, 0))
        self._label(inner, "支持 UTF-8 / GBK、合集 .sgfs、LizzieYzy 与 KataGo 分析棋谱",
                    fg=FAINT, size=8).pack(anchor="w", pady=(7, 0))

    def _build_card2(self, p: tk.Frame) -> None:
        inner = self._pad(p)
        self._title(inner, "方案与画面")
        g = tk.Frame(inner, bg=PANE)
        g.pack(fill="x")
        g.grid_columnconfigure(1, weight=1)
        g.grid_columnconfigure(3, weight=1)

        self._label(g, "方案").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.c_preset = RoundCombo(g, self.v_preset, ["custom"] + list(PRESETS),
                                   width=150, height=28)
        self.c_preset.grid(row=0, column=1, sticky="w")
        self.c_preset.bind_select(
            lambda e: self._apply_preset(self.v_preset.get()))
        self.preset_desc = self._label(g, "", fg=ACCENT2, size=8)
        self.preset_desc.grid(row=0, column=2, columnspan=2, sticky="w",
                              padx=(10, 0))

        self._label(g, "主题").grid(row=1, column=0, sticky="w",
                                    padx=(0, 8), pady=(7, 0))
        RoundCombo(g, self.v_theme, [k for k, _ in THEME_LABELS],
                   width=150, height=28).grid(row=1, column=1, sticky="w",
                                              pady=(7, 0))
        self._label(g, "分辨率").grid(row=1, column=2, sticky="w",
                                      padx=(14, 8), pady=(7, 0))
        RoundCombo(g, self.v_size, list(SIZE_PRESETS), width=128,
                   height=28).grid(row=1, column=3, sticky="w", pady=(7, 0))

        self._label(g, "帧率").grid(row=2, column=0, sticky="w",
                                    padx=(0, 8), pady=(7, 0))
        fr = tk.Frame(g, bg=PANE)
        fr.grid(row=2, column=1, sticky="w", pady=(7, 0))
        self._spin(fr, self.v_fps, 12, 60, 56).pack(side="left")
        self._label(fr, "fps", fg=FAINT, size=8).pack(side="left", padx=(6, 0))
        self._label(g, "时长").grid(row=2, column=2, sticky="w",
                                    padx=(14, 8), pady=(7, 0))
        dur = tk.Frame(g, bg=PANE)
        dur.grid(row=2, column=3, sticky="w", pady=(7, 0))
        RoundEntry(dur, self.v_duration, width=74, height=28).pack(side="left")
        self._label(dur, "秒 (空=自动)", fg=FAINT, size=8).pack(
            side="left", padx=(6, 0))

        self._label(g, "画质").grid(row=3, column=0, sticky="w",
                                    padx=(0, 8), pady=(7, 0))
        RoundCombo(g, self.v_quality, ["low", "medium", "high", "ultra"],
                   width=150, height=28).grid(row=3, column=1, sticky="w",
                                              pady=(7, 0))
        self._label(g, "编码").grid(row=3, column=2, sticky="w",
                                    padx=(14, 8), pady=(7, 0))
        RoundCombo(g, self.v_codec, ["h264", "hevc", "vp9"], width=128,
                   height=28).grid(row=3, column=3, sticky="w", pady=(7, 0))

        self._label(g, "水印").grid(row=4, column=0, sticky="w",
                                    padx=(0, 8), pady=(7, 0))
        RoundEntry(g, self.v_watermark, width=150, height=28).grid(
            row=4, column=1, sticky="ew", pady=(7, 0))
        self._label(g, "热力图浓度").grid(row=4, column=2, sticky="w",
                                          padx=(14, 8), pady=(7, 0))
        GlassSlider(g, self.v_strength, 0.3, 1.5, width=150).grid(
            row=4, column=3, sticky="w", pady=(7, 0))

    def _build_card3(self, p: tk.Frame) -> None:
        inner = self._pad(p)
        self._title(inner, "渲染选项与性能", dot=WARM)
        g = tk.Frame(inner, bg=PANE)
        g.pack(fill="x")
        for i in range(3):
            g.grid_columnconfigure(i, weight=1, uniform="opt")
        opts = [
            ("领地热力图", self.v_heat), ("AI 分析面板", self.v_analysis),
            ("评论字幕", self.v_comment), ("领地流动过渡", self.v_transition),
            ("配色图例", self.v_legend), ("导出章节标记", self.v_chapters),
            ("导出海报帧", self.v_poster), ("跳过已存在", self.v_skip),
            ("低占用模式", self.v_eco),
        ]
        for i, (txt, var) in enumerate(opts):
            GlassCheck(g, txt, var, font=(_FF, 9)).grid(
                row=i // 3, column=i % 3, sticky="w", pady=2)

        row = tk.Frame(inner, bg=PANE)
        row.pack(fill="x", pady=(7, 0))
        self._label(row, "并行进程").pack(side="left", padx=(0, 8))
        self._spin(row, self.v_jobs, 0, 8, 52).pack(side="left")
        self._label(row, "0 = 自动; Windows 单文件版建议 1", fg=FAINT,
                    size=8).pack(side="left", padx=(8, 0))

    def _spin(self, parent, var, lo: int, hi: int, width: int) -> tk.Canvas:
        cv = tk.Canvas(parent, width=width, height=28, bg=PANE,
                       highlightthickness=0, bd=0)
        img = ImageTk.PhotoImage(
            _pill(width, 28, 9, "#0D1520", "#0A1018",
                  border=(255, 255, 255, 46)))
        cv._img = img  # type: ignore[attr-defined]
        cv.create_image(0, 0, image=img, anchor="nw")
        e = tk.Entry(cv, textvariable=var, bg=FIELD, fg=TEXT,
                     insertbackground=ACCENT, relief="flat", bd=0,
                     highlightthickness=0, font=(_FF, 9), justify="center")
        cv.create_window(width / 2, 14, window=e, width=width - 14, height=18)
        return cv

    def _build_side(self, p: tk.Frame) -> None:
        inner = self._pad(p)
        self._title(inner, "画面预览", dot=ACCENT2)
        self.preview = tk.Canvas(inner, bg="#0A0F16", highlightthickness=0,
                                 bd=0, height=200)
        self.preview.pack(fill="x")
        self.preview.bind("<Configure>", lambda e: self._redraw_preview())

        tk.Frame(inner, bg="#1D2836", height=1).pack(fill="x", pady=(12, 10))
        tk.Label(inner, text="当前配置", bg=PANE, fg=ICE,
                 font=(_FF, 10, "bold")).pack(anchor="w", pady=(0, 5))
        self.summary = tk.Text(inner, height=9, bg=PANE, fg=DIM,
                               relief="flat", bd=0, font=(_FF, 9),
                               highlightthickness=0, wrap="word",
                               spacing1=3, spacing3=3)
        self.summary.pack(fill="x")
        self.summary.configure(state="disabled")

        tk.Frame(inner, bg="#1D2836", height=1).pack(fill="x", pady=(12, 9))
        tk.Label(inner,
                 text="低占用模式会关闭逐帧过渡、限制线程并收敛进程数，"
                      "后台渲染时可正常浏览网页、办公。",
                 bg=PANE, fg=FAINT, font=(_FF, 8), anchor="w",
                 justify="left", wraplength=300).pack(anchor="w", fill="x")

    def _build_foot(self, p: tk.Frame) -> None:
        inner = self._pad(p)
        bar = tk.Frame(inner, bg=PANE)
        bar.pack(fill="x")
        specs = [
            ("棋谱信息", "info", "ghost"), ("预览画面", "preview", "ghost"),
            ("试运行", "dry", "ghost"), ("性能基准", "bench", "ghost"),
        ]
        for text, mode, kind in specs:
            b = GlassButton(bar, text, lambda m=mode: self._run(m),
                            kind=kind, width=84, height=32, font=(_FF, 9))
            b.pack(side="left", padx=(0, 7))
            self.buttons[mode] = b

        self.btn_stop = GlassButton(bar, "停止", self._stop, width=66,
                                    height=32, font=(_FF, 9))
        self.btn_stop.pack(side="right")
        self.btn_stop.set_enabled(False)
        self.btn_render = GlassButton(bar, "开始渲染", lambda: self._run("render"),
                                      kind="primary", width=112, height=32,
                                      font=(_FF, 9, "bold"))
        self.btn_render.pack(side="right", padx=(0, 8))
        self.buttons["render"] = self.btn_render

        pf = tk.Frame(inner, bg=PANE)
        pf.pack(fill="x", pady=(10, 3))
        self.progress = GlassProgress(pf, width=520, height=8)
        self.progress.pack(side="left", fill="x", expand=True)
        tk.Label(pf, textvariable=self.v_status, width=10, anchor="e",
                 bg=PANE, fg=ACCENT2, font=(_FF, 9, "bold")).pack(
            side="right", padx=(10, 2))

        lf = tk.Frame(inner, bg=PANE)
        lf.pack(fill="x", pady=(3, 0))
        self.log = RoundText(lf, height=96 if self._density == 0 else 78,
                             font=(_FM, 8))
        self.log.pack(fill="x")
        self.log.text.configure(state="disabled")
        self._log("就绪。选择 SGF 棋谱后可直接点「开始渲染」，或先用「预览画面」看效果。")

    # ------------------------------------------------------------------ #
    def _pad(self, p: tk.Frame) -> tk.Frame:
        d = 14 if self._density == 0 else 10
        f = tk.Frame(p, bg=PANE)
        f.pack(fill="both", expand=True, padx=d, pady=d)
        return f

    # ------------------------------------------------------------------ #
    # 布局与玻璃材质
    # ------------------------------------------------------------------ #
    def _on_configure(self, e) -> None:
        if e.widget is not self.root:
            return
        size = (e.width, e.height)
        if size == self._size:
            return
        self._size = size
        if self._resize_job:
            try:
                self.root.after_cancel(self._resize_job)
            except Exception:
                pass
        self._resize_job = self.root.after(110, self._relayout)

    def _relayout(self) -> None:
        self._resize_job = None
        W, H = self.root.winfo_width(), self.root.winfo_height()
        if W < 200 or H < 200:
            return
        self._density = 0 if H >= 800 else 1
        pad, gap = 16, 12
        head_h = 74
        right_w = 366 if W >= 980 else 336

        bg = build_backdrop(W, H)
        self._bg_img = bg
        self._bg_photo = ImageTk.PhotoImage(bg)
        self.cv.delete("all")
        self.cv.create_image(0, 0, image=self._bg_photo, anchor="nw")
        self._card_photos = []

        self._head_win = self.cv.create_window(
            pad, 0, window=self.head, anchor="nw", width=W - pad * 2,
            height=head_h)
        self.head.configure(width=W - pad * 2, height=head_h)
        self._draw_header(W - pad * 2, head_h)

        left_w = W - pad * 2 - gap - right_w
        body_y = head_h + 10
        foot_h = 176

        # 左侧三张卡
        cards = [self.card1, self.card2, self.card3]
        inner_w = left_w - 8
        y = body_y
        for f in cards:
            f.configure(width=inner_w)
            wid = self.cv.create_window(pad + 4, y + 4, window=f, anchor="nw",
                                        width=inner_w)
            self.root.update_idletasks()
            ch = max(60, f.winfo_reqheight())
            self._glass(pad, y, pad + left_w, y + ch + 8)
            self.cv.coords(wid, pad + 4, y + 4)
            y += ch + 8 + gap

        # 右侧栏
        side_top = body_y
        side_h = max(300, H - side_top - foot_h - pad)
        self.side.configure(width=right_w)
        self.cv.create_window(pad + left_w + gap + 4, side_top + 4,
                              window=self.side, anchor="nw",
                              width=right_w - 8, height=side_h - 8)
        self._glass(pad + left_w + gap, side_top,
                    pad + left_w + gap + right_w, side_top + side_h)
        self.side.update_idletasks()

        # 底部
        ftop = H - foot_h - pad + 2
        self.cv.create_window(pad + 4, ftop + 4, window=self.foot, anchor="nw",
                              width=W - pad * 2 - 8, height=foot_h - 8)
        self._glass(pad, ftop, W - pad, ftop + foot_h)

        # 让卡片内部真正的圆角露出来: 玻璃图在 window item 下方,
        # window item 恒在 canvas item 之上, 边缘高光自然可见.

    def _glass(self, x0: int, y0: int, x1: int, y1: int) -> None:
        if self._bg_img is None:
            return
        # 外阴影: 让玻璃板从背景里"浮"起来
        pad = 26
        sh = Image.new("RGBA", (x1 - x0 + pad * 2, y1 - y0 + pad * 2),
                       (0, 0, 0, 0))
        ImageDraw.Draw(sh).rounded_rectangle(
            (pad, pad + 7, pad + x1 - x0, pad + y1 - y0 + 7),
            radius=24, fill=(0, 0, 0, 120))
        sh = sh.filter(ImageFilter.GaussianBlur(13))
        photo_s = ImageTk.PhotoImage(sh)
        self._card_photos.append(photo_s)
        self.cv.create_image(x0 - pad, y0 - pad, image=photo_s, anchor="nw")

        img = glass_panel(self._bg_img, (x0, y0, x1, y1), radius=22)
        photo = ImageTk.PhotoImage(img)
        self._card_photos.append(photo)
        self.cv.create_image(x0, y0, image=photo, anchor="nw")

    def _draw_header(self, w: Optional[int] = None,
                     h: Optional[int] = None) -> None:
        c = self.head
        w = w or max(1, c.winfo_width())
        h = h or 74
        c.configure(width=w, height=h)
        c.delete("all")
        c.create_text(16, 24, anchor="w", text="SGF Review",
                      fill="#F6FBFF", font=(_FF, 18, "bold"))
        c.create_text(17, 47, anchor="w",
                      text="围棋复盘工作台   ·   领地热力图 / AI 分析 / 一键成片",
                      fill="#8EA5BE", font=(_FF, 9))
        c.create_text(w - 4, 16, anchor="e", text=f"v{_VER}",
                      fill="#6FB8E8", font=(_FM, 9))
        # 三颗棋子 + 柔光
        for i, (col, gl) in enumerate(
                (("#F4EEE2", "#FFE9C0"), ("#0E141C", "#2A4A6E"),
                 ("#E9F6FF", "#7CD4FF"))):
            cx = w - 26 - i * 22
            cy = 50
            c.create_oval(cx - 10, cy - 10, cx + 10, cy + 10, fill="",
                          outline=gl, width=1)
            c.create_oval(cx - 5, cy - 5, cx + 5, cy + 5, fill=col, outline="")
        c.create_line(0, h - 1, w, h - 1, fill="#233246", width=1)

    def _redraw_preview(self) -> None:
        c = self.preview
        w, h = c.winfo_width() or 340, c.winfo_height() or 200
        c.delete("all")
        c.create_rectangle(0, 0, w, h, fill="#0A0F16", outline="")
        if self.photo is not None:
            c.create_image(w / 2, h / 2, image=self.photo, anchor="center")
        else:
            self._placeholder(w, h)

    def _placeholder(self, w: int, h: int) -> None:
        c = self.preview
        c.create_text(w / 2, 14, text="选择棋谱后点「预览画面」",
                      fill=FAINT, font=(_FF, 8))
        s = min(w - 44, h - 40) / 8.0
        x0, y0 = (w - s * 8) / 2, (h - s * 8) / 2 + 8
        c.create_rectangle(x0 - 7, y0 - 7, x0 + 8 * s + 7, y0 + 8 * s + 7,
                           fill="#0D131C", outline="")
        for i in range(9):
            c.create_line(x0 + i * s, y0, x0 + i * s, y0 + 8 * s,
                          fill="#26364A")
            c.create_line(x0, y0 + i * s, x0 + 8 * s, y0 + i * s,
                          fill="#26364A")
        for gx, gy, col in ((2, 2, "#F5F0E4"), (6, 2, "#0B1017"),
                            (4, 4, "#F5F0E4"), (2, 6, "#0B1017")):
            cx, cy = x0 + gx * s, y0 + gy * s
            rr = s * 0.40
            c.create_oval(cx - rr + 1, cy - rr + 2, cx + rr + 1, cy + rr + 2,
                          fill="#05080D", outline="")
            c.create_oval(cx - rr, cy - rr, cx + rr, cy + rr,
                          fill=col, outline="#3E5168")

    def _show_preview(self, path: str) -> None:
        try:
            img = tk.PhotoImage(file=path)
            w = self.preview.winfo_width() or 340
            k = max(1, int(math.ceil(img.width() / float(max(1, w - 6)))))
            if k > 1:
                img = img.subsample(k, k)
            self.photo = img
            self._redraw_preview()
            self._log("预览图已刷新 (2×2 分镜拼图)")
        except Exception as e:
            self._log(f"预览图显示失败: {e}")

    # ------------------------------------------------------------------ #
    # 参数
    # ------------------------------------------------------------------ #
    def _apply_preset(self, key: str) -> None:
        if key == "custom":
            self.extra = {}
            self.preset_desc.configure(text="自定义参数")
            self._update_summary()
            return
        p = dict(PRESETS.get(key, {}))
        self.extra = {k: p[k] for k in EXTRA_FLAGS if k in p}
        self.v_theme.set(p.get("theme", "classic"))
        self.v_size.set(p.get("size", "1080p"))
        self.v_fps.set(str(p.get("fps", 30)))
        self.v_duration.set(str(p.get("duration", "")))
        self.v_quality.set(p.get("quality", "high"))
        self.v_jobs.set(str(p.get("jobs", 0)))
        self.v_heat.set(bool(p.get("heatmap", False)))
        if p.get("eco", False):
            self.v_eco.set(True)
        self.preset_desc.configure(text=PRESET_DESC.get(key, ""))
        self._update_summary()

    def _browse_sgf(self) -> None:
        p = filedialog.askopenfilename(
            parent=self.root, title="选择 SGF 棋谱",
            filetypes=[("围棋棋谱", "*.sgf *.sgfs"), ("所有文件", "*.*")])
        if p:
            self.v_sgf.set(p)
            if not self.v_out.get().strip():
                self._auto_output()

    def _browse_out(self) -> None:
        ext = ".webm" if self.v_codec.get() == "vp9" else ".mp4"
        p = filedialog.asksaveasfilename(
            parent=self.root, title="输出视频", defaultextension=ext,
            filetypes=[("视频文件", f"*{ext}"), ("所有文件", "*.*")])
        if p:
            self.v_out.set(p)

    def _auto_output(self) -> None:
        sgf = self.v_sgf.get().strip().strip('"')
        if not sgf:
            return
        ext = ".webm" if self.v_codec.get() == "vp9" else ".mp4"
        self.v_out.set(os.path.splitext(sgf)[0] + "_回顾" + ext)

    def _int(self, var, default: int) -> int:
        try:
            return int(float(var.get()))
        except (ValueError, TypeError):
            return default

    def _build_args(self, mode: str) -> List[str]:
        a = [self.v_sgf.get().strip().strip('"')]
        for key, flag in EXTRA_FLAGS.items():
            val = self.extra.get(key)
            if val is None:
                continue
            if isinstance(val, bool):
                if val:
                    a.append(flag)
            else:
                a += [flag, str(val)]
        a += ["--theme", self.v_theme.get(), "--size", self.v_size.get(),
              "--fps", str(self._int(self.v_fps, 30)),
              "--quality", self.v_quality.get(), "--codec", self.v_codec.get()]
        dur = self.v_duration.get().strip()
        if dur:
            a += ["--duration", dur]
        jobs = self._int(self.v_jobs, 0)
        if jobs > 0:
            a += ["-j", str(jobs)]
        if self.v_heat.get():
            a.append("--heatmap")
        hs = self.v_strength.get()
        if abs(hs - 1.0) > 1e-6:
            a += ["--heat-strength", f"{hs:.2f}"]
        if not self.v_transition.get():
            a.append("--no-heat-transition")
        if not self.v_legend.get():
            a.append("--no-heat-legend")
        if not self.v_analysis.get():
            a.append("--no-analysis")
        if not self.v_comment.get():
            a.append("--no-comment")
        if self.v_eco.get():
            a.append("--eco")
        if self.v_skip.get():
            a.append("--skip-existing")
        wm = self.v_watermark.get().strip()
        if wm:
            a += ["--watermark", wm]
        out = self.v_out.get().strip().strip('"')
        if out:
            a += ["-o", out]
            stem = os.path.splitext(out)[0]
            if self.v_chapters.get():
                a += ["--chapters", stem + "_chapters.txt"]
            if self.v_poster.get():
                a += ["--poster", stem + "_poster.png"]
        if mode == "info":
            a.append("--info")
        elif mode == "preview":
            a += ["--preview", self.preview_png]
        elif mode == "dry":
            a.append("--dry-run")
        elif mode == "bench":
            a.append("--benchmark")
        return a

    def _update_summary(self) -> None:
        key = self.v_preset.get()
        scheme = "自定义" if key == "custom" else f"预设 · {key}"
        label = dict(THEME_LABELS).get(self.v_theme.get(), self.v_theme.get())
        feat = []
        if self.v_heat.get():
            feat.append("领地热力图")
        if self.v_analysis.get():
            feat.append("AI 分析")
        if self.v_comment.get():
            feat.append("评论字幕")
        if self.v_eco.get():
            res = "低占用 / 最多 2 进程"
        elif self._int(self.v_jobs, 0) > 0:
            res = f"{self._int(self.v_jobs, 0)} 进程"
        else:
            res = "自动 (按 CPU/内存)"
        lines = [
            f"方案   {scheme}",
            f"画面   {self.v_size.get()} @ {self.v_fps.get()}fps",
            f"主题   {label}",
            f"时长   {self.v_duration.get() or '自动'} 秒",
            f"编码   {self.v_codec.get()} / {self.v_quality.get()}",
            f"特效   {' · '.join(feat) if feat else '纯棋盘'}",
            f"资源   {res}",
            f"输出   {os.path.basename(self.v_out.get()) or '自动命名'}",
        ]
        self.summary.configure(state="normal")
        self.summary.delete("1.0", "end")
        self.summary.insert("1.0", "\n".join(lines))
        self.summary.configure(state="disabled")

    # ------------------------------------------------------------------ #
    # 子进程
    # ------------------------------------------------------------------ #
    def _base_cmd(self) -> List[str]:
        if getattr(sys, "frozen", False):
            return [sys.executable, "--cli"]
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return [sys.executable, os.path.join(root, "sgf_gui.py"), "--cli"]

    def _run(self, mode: str) -> None:
        if self.proc and self.proc.poll() is None:
            messagebox.showinfo("正在运行", "已有任务在执行，请等待或点「停止」。",
                                parent=self.root)
            return
        sgf = self.v_sgf.get().strip().strip('"')
        if not sgf or not os.path.isfile(sgf):
            messagebox.showwarning("缺少棋谱", "请先选择一个存在的 SGF 棋谱文件。",
                                   parent=self.root)
            return
        if not self.v_out.get().strip() and mode in ("render", "dry", "bench"):
            self._auto_output()
        self.v_out.set(self.v_out.get().strip().strip('"'))
        self.mode, self.cancelled, self.finished, self.tail = mode, False, False, 0
        self.q = queue.Queue()
        self.progress.set_value(0)
        argv = self._build_args(mode)
        self._update_summary()
        self._log(f"$ sgf-review {' '.join(argv[1:])}")
        env = dict(os.environ)
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"
        try:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            self.proc = subprocess.Popen(
                self._base_cmd() + argv, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                creationflags=flags, env=env)
        except Exception as e:
            self._log(f"启动失败: {e}")
            self._finish()
            return
        self._set_busy(True)
        threading.Thread(target=self._reader, args=(self.proc, self.q),
                         daemon=True).start()
        self.root.after(70, self._poll)

    def _reader(self, proc, q: queue.Queue) -> None:
        buf: List[str] = []
        try:
            while True:
                ch = proc.stdout.read(1)
                if not ch:
                    break
                if ch in "\r\n":
                    line = "".join(buf).strip()
                    if line:
                        q.put(line)
                    buf = []
                else:
                    buf.append(ch)
        except Exception as e:
            q.put(f"[日志读取异常] {e}")
        finally:
            line = "".join(buf).strip()
            if line:
                q.put(line)
            q.put(None)

    def _poll(self) -> None:
        try:
            while True:
                item = self.q.get_nowait()
                if item is None:
                    self._finish()
                    return
                self._handle_line(item)
        except queue.Empty:
            pass
        if self.proc and self.proc.poll() is None:
            self.tail = 0
            self.root.after(70, self._poll)
        else:
            self.tail += 1
            if self.tail < 40 and not self.finished:
                self.root.after(70, self._poll)
            elif not self.finished:
                self._finish()

    def _handle_line(self, line: str) -> None:
        m = re.search(r"\[[█░ ]+\]\s*([\d.]+)%", line)
        if m:
            pct = float(m.group(1))
            self.progress.set_value(pct)
            self.v_status.set(f"{pct:.0f}%")
            return
        self._log(line)

    def _finish(self) -> None:
        if self.finished:
            return
        self.finished = True
        code = self.proc.poll() if self.proc else None
        self._set_busy(False)
        if self.cancelled:
            self._log("■ 已停止")
            self.v_status.set("已停止")
        elif code == 0:
            self.progress.set_value(100)
            self._log("✓ 任务完成")
            self.v_status.set("完成")
        else:
            self._log(f"✗ 任务失败 (退出码 {code})")
            self.v_status.set("失败")
        if self.mode == "preview" and os.path.isfile(self.preview_png):
            self._show_preview(self.preview_png)
        if self.mode == "render" and code == 0 and not self.cancelled:
            out = self.v_out.get()
            if out and os.path.isfile(out):
                self._log(f"输出文件: {out}  "
                          f"({os.path.getsize(out) / 1024 / 1024:.1f} MB)")

    def _stop(self) -> None:
        if not self.proc or self.proc.poll() is not None:
            return
        self.cancelled = True
        self._log("正在停止…")
        # Windows 上 terminate 只杀父进程, ffmpeg 会成为孤儿并占用输出文件
        if os.name == "nt":
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(self.proc.pid)],
                    capture_output=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except Exception:
                pass
        try:
            self.proc.terminate()
        except Exception:
            pass
        try:
            self.proc.wait(timeout=3)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass

    def _set_busy(self, busy: bool) -> None:
        for b in self.buttons.values():
            b.set_enabled(not busy)
        self.btn_stop.set_enabled(busy)
        if busy:
            self.v_status.set("运行中…")

    def _log(self, text: str) -> None:
        t = self.log.text
        t.configure(state="normal")
        if int(t.index("end-1c").split(".")[0]) > 600:
            t.delete("1.0", "160.0")
        t.insert("end", text.rstrip() + "\n")
        t.see("end")
        t.configure(state="disabled")

    def _fade_in(self, step: int) -> None:
        try:
            self.root.attributes("-alpha", min(1.0, 0.35 + step * 0.065))
        except Exception:
            return
        if step < 10:
            self.root.after(16, lambda: self._fade_in(step + 1))

    def _on_close(self) -> None:
        if self.proc and self.proc.poll() is None:
            if not messagebox.askokcancel("退出", "渲染仍在进行，确定停止并退出？",
                                          parent=self.root):
                return
            self.cancelled = True
            self._stop()
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        self.root.destroy()


def launch() -> int:
    _dpi_aware()
    _hide_console()
    root = tk.Tk()
    _pick_font(root)
    try:
        root.tk.call("tk", "scaling", max(1.0, root.winfo_fpixels("1i") / 72.0))
    except Exception:
        pass
    _win_chrome(root)
    App(root)
    root.mainloop()
    return 0
