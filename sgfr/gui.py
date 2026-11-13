"""轻量桌面图形界面 (tkinter, 零第三方依赖).

设计取舍:
  * 界面进程只加载 tkinter + 一张常量表, 不 import numpy / Pillow;
    实际渲染放在子进程 (sfg_gui.py --cli) 里, 界面主线程永远空闲。
  * 默认开启「低占用模式」, 进程数收敛到 2, 后台渲染时前台依然流畅。
  * 启动有一次 350ms 的标题淡入动画, 之后界面完全静止, 不常驻刷帧。
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
from tkinter import filedialog, messagebox, scrolledtext, ttk
from typing import Dict, List, Optional

from .presets import PRESET_DESC, PRESETS, SIZE_PRESETS

# --------------------------------------------------------------------------- #
# 配色 (与「暗夜极光」主题呼应)
# --------------------------------------------------------------------------- #
BG = "#121821"
CARD = "#1B2431"
CARD2 = "#222E3E"
LINE = "#2E3C50"
TEXT = "#E8EFF7"
DIM = "#90A2B8"
ACCENT = "#5FB2FF"
ACCENT2 = "#8CE0FF"
WARM = "#F0B45A"
GREEN = "#6FD39B"

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

_FF = "TkDefaultFont"       # launch() 里按系统可用字体替换
_FB = "TkDefaultFont"
_FM = "TkDefaultFont"


def _hex(c: str):
    return tuple(int(c[i:i + 2], 16) for i in (1, 3, 5))


def _mix(a: str, b: str, t: float) -> str:
    t = max(0.0, min(1.0, t))
    x, y = _hex(a), _hex(b)
    return "#%02X%02X%02X" % tuple(int(x[i] + (y[i] - x[i]) * t) for i in range(3))


def _pick_font(root: tk.Tk) -> None:
    global _FF, _FB, _FM
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
    _FB = _FF
    for name in ("Consolas", "Cascadia Mono", "DejaVu Sans Mono", _FF):
        if name in fams:
            _FM = name
            break


def _hide_console() -> None:
    """Windows 控制台版 exe 启动 GUI 时把黑框藏起来 (CLI 模式仍需 stdout)."""
    if os.name == "nt" and getattr(sys, "frozen", False):
        try:
            import ctypes
            hwnd = ctypes.windll.kernel32.GetConsoleWindow()
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 0)   # SW_HIDE
        except Exception:
            pass


def _base_cmd() -> List[str]:
    """GUI 调起渲染子进程: 打包后是 exe 自己, 源码模式下是入口脚本."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--cli"]
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return [sys.executable, os.path.join(root, "sgf_gui.py"), "--cli"]


def launch() -> int:
    _hide_console()
    root = tk.Tk()
    _pick_font(root)
    App(root)
    root.mainloop()
    return 0


# --------------------------------------------------------------------------- #
class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("SGF Review · 围棋复盘工作台  v2.0")
        self.root.geometry("980x880")
        self.root.minsize(900, 740)
        self.root.configure(bg=BG)

        self.proc: Optional[subprocess.Popen] = None
        self.q: queue.Queue = queue.Queue()
        self.extra: Dict = {}
        self.mode = "render"
        self.cancelled = False
        self.finished = True
        self.tail = 0
        self.photo = None
        self.buttons: Dict[str, tk.Button] = {}
        self.tmpdir = tempfile.mkdtemp(prefix="sgfr_gui_")
        self.preview_png = os.path.join(self.tmpdir, "preview.png")

        self._vars()
        self._style()
        self._build_ui()
        # 任一参数变化都即时刷新右侧摘要, 不必等到点「开始渲染」
        for var in (self.v_preset, self.v_theme, self.v_size, self.v_fps,
                    self.v_duration, self.v_quality, self.v_codec, self.v_jobs,
                    self.v_out, self.v_heat, self.v_analysis, self.v_comment,
                    self.v_eco):
            var.trace_add("write", lambda *_: self._update_summary())
        self._apply_preset("heat")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._hdr_ratio = 0.0
        self._animate(0)

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
        s.configure("SGFR.TCombobox", fieldbackground=CARD2, background=CARD2,
                    foreground=TEXT, arrowcolor=ACCENT, bordercolor=LINE,
                    lightcolor=LINE, darkcolor=LINE, padding=4)
        s.map("SGFR.TCombobox",
              fieldbackground=[("readonly", CARD2)],
              foreground=[("readonly", TEXT)],
              selectbackground=[("readonly", CARD2)],
              selectforeground=[("readonly", TEXT)])
        s.configure("SGFR.Horizontal.TProgressbar", troughcolor=CARD2,
                    background=ACCENT, bordercolor=LINE, lightcolor=ACCENT,
                    darkcolor=ACCENT, thickness=12)

    # ------------------------------------------------------------------ #
    # 界面骨架
    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        r = self.root
        self.header = tk.Canvas(r, height=76, bg=BG, highlightthickness=0)
        self.header.pack(fill="x", side="top")
        self.header.bind("<Configure>", lambda e: self._draw_header())

        # 底部先占位: 保证进度条 / 日志在任何窗口尺寸下都可见
        bottom = tk.Frame(r, bg=BG)
        bottom.pack(fill="x", side="bottom", padx=14, pady=(2, 12))
        self._build_actions(bottom)

        body = tk.Frame(r, bg=BG)
        body.pack(fill="both", expand=True)
        # 左侧表单放进可滚动画布, 选项再多也不会把底部区域顶掉
        outer = tk.Frame(body, bg=BG)
        outer.pack(side="left", fill="both", expand=True, padx=(14, 6), pady=10)
        cv = tk.Canvas(outer, bg=BG, highlightthickness=0)
        sb = tk.Scrollbar(outer, orient="vertical", command=cv.yview,
                          bg=CARD2, troughcolor=BG, relief="flat", width=9)
        cv.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        cv.pack(side="left", fill="both", expand=True)
        inner = tk.Frame(cv, bg=BG)
        self._left_win = cv.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>",
                   lambda e: cv.configure(scrollregion=cv.bbox("all")))
        cv.bind("<Configure>",
                lambda e: cv.itemconfigure(self._left_win, width=e.width))
        def _wheel(e):
            cv.yview_scroll(int(-e.delta / 120), "units")
        cv.bind("<Enter>", lambda e: cv.bind_all("<MouseWheel>", _wheel))
        cv.bind("<Leave>", lambda e: cv.unbind_all("<MouseWheel>"))

        right = tk.Frame(body, bg=BG, width=366)
        right.pack(side="right", fill="y", padx=(6, 14), pady=10)
        right.pack_propagate(False)
        self._build_left(inner)
        self._build_right(right)

    def _card(self, parent, title: str):
        f = tk.Frame(parent, bg=CARD, highlightbackground=LINE,
                     highlightthickness=1)
        f.pack(fill="x", pady=(0, 8))
        tk.Label(f, text=title, bg=CARD, fg=ACCENT,
                 font=(_FF, 10, "bold")).pack(anchor="w", padx=12, pady=(8, 3))
        inner = tk.Frame(f, bg=CARD)
        inner.pack(fill="x", padx=12, pady=(0, 9))
        return inner

    def _label(self, parent, text: str, **kw):
        kw.setdefault("bg", CARD)
        kw.setdefault("fg", DIM)
        return tk.Label(parent, text=text, font=(_FF, 9), **kw)

    def _entry(self, parent, var, width: int = 30):
        return tk.Entry(parent, textvariable=var, bg=CARD2, fg=TEXT,
                        insertbackground=ACCENT, relief="flat", width=width,
                        font=(_FF, 9), highlightbackground=LINE,
                        highlightthickness=1)

    def _combo(self, parent, var, values, width: int = 14):
        c = ttk.Combobox(parent, textvariable=var, values=list(values),
                         state="readonly", width=width,
                         style="SGFR.TCombobox", font=(_FF, 9))
        return c

    def _check(self, parent, text: str, var):
        return tk.Checkbutton(parent, text=text, variable=var, bg=CARD,
                              fg=TEXT, selectcolor=CARD2, activebackground=CARD,
                              activeforeground=ACCENT2, relief="flat",
                              highlightthickness=0, bd=0, font=(_FF, 9),
                              anchor="w")

    def _build_left(self, parent) -> None:
        # ① 棋谱与输出
        c = self._card(parent, "① 棋谱与输出")
        c.grid_columnconfigure(1, weight=1)
        self._label(c, "SGF 棋谱").grid(row=0, column=0, sticky="w", pady=3)
        self._entry(c, self.v_sgf).grid(row=0, column=1, sticky="ew", padx=6)
        self._button(c, "浏览…", self._browse_sgf, primary=False,
                     small=True).grid(row=0, column=2)
        self._label(c, "输出视频").grid(row=1, column=0, sticky="w", pady=3)
        self._entry(c, self.v_out).grid(row=1, column=1, sticky="ew", padx=6)
        self._button(c, "浏览…", self._browse_out, primary=False,
                     small=True).grid(row=1, column=2)
        self._label(c, "支持 UTF-8 / GBK、合集 .sgfs、LizzieYzy 与 KataGo 分析棋谱",
                    fg="#6E8098").grid(row=2, column=0, columnspan=3,
                                      sticky="w", pady=(7, 0))

        # ② 方案与画面
        c = self._card(parent, "② 方案与画面")
        c.grid_columnconfigure(1, weight=1)
        c.grid_columnconfigure(3, weight=1)
        self._label(c, "一键方案").grid(row=0, column=0, sticky="w", pady=3)
        pcb = self._combo(c, self.v_preset, ["custom"] + list(PRESETS), 12)
        pcb.grid(row=0, column=1, sticky="w", padx=6)
        pcb.bind("<<ComboboxSelected>>",
                 lambda e: self._apply_preset(self.v_preset.get()))
        self.preset_desc = self._label(c, "", fg=ACCENT2, anchor="w")
        self.preset_desc.grid(row=0, column=2, columnspan=2, sticky="w")

        self._label(c, "主题").grid(row=1, column=0, sticky="w", pady=3)
        self._combo(c, self.v_theme, [k for k, _ in THEME_LABELS],
                    12).grid(row=1, column=1, sticky="w", padx=6)
        self._label(c, "分辨率").grid(row=1, column=2, sticky="w")
        self._combo(c, self.v_size, list(SIZE_PRESETS),
                    12).grid(row=1, column=3, sticky="w", padx=6)

        self._label(c, "帧率").grid(row=2, column=0, sticky="w", pady=3)
        tk.Spinbox(c, from_=12, to=60, textvariable=self.v_fps, width=6,
                   bg=CARD2, fg=TEXT, relief="flat", font=(_FF, 9),
                   buttonbackground=CARD2, highlightbackground=LINE,
                   highlightthickness=1).grid(row=2, column=1, sticky="w", padx=6)
        self._label(c, "时长(秒)").grid(row=2, column=2, sticky="w")
        self._entry(c, self.v_duration, 10).grid(row=2, column=3,
                                                 sticky="w", padx=6)

        self._label(c, "画质").grid(row=3, column=0, sticky="w", pady=3)
        self._combo(c, self.v_quality, ["low", "medium", "high", "ultra"],
                    12).grid(row=3, column=1, sticky="w", padx=6)
        self._label(c, "编码").grid(row=3, column=2, sticky="w")
        self._combo(c, self.v_codec, ["h264", "hevc", "vp9"],
                    12).grid(row=3, column=3, sticky="w", padx=6)

        self._label(c, "水印").grid(row=4, column=0, sticky="w", pady=3)
        self._entry(c, self.v_watermark, 22).grid(row=4, column=1,
                                                  columnspan=3, sticky="w", padx=6)
        self._label(c, "热力图浓度").grid(row=5, column=0, sticky="w", pady=(6, 0))
        tk.Scale(c, from_=0.3, to=1.5, resolution=0.05, orient="horizontal",
                 variable=self.v_strength, bg=CARD, fg=DIM, troughcolor=CARD2,
                 activebackground=ACCENT, highlightthickness=0, bd=0,
                 length=230, font=(_FF, 8), showvalue=True).grid(
            row=5, column=1, columnspan=3, sticky="w", padx=6)

        # ③ 渲染选项
        c = self._card(parent, "③ 渲染选项与性能")
        c.grid_columnconfigure(0, weight=1)
        c.grid_columnconfigure(1, weight=1)
        opts = [
            ("领地热力图", self.v_heat), ("AI 分析面板", self.v_analysis),
            ("评论字幕", self.v_comment), ("领地流动过渡", self.v_transition),
            ("配色图例", self.v_legend), ("导出章节标记", self.v_chapters),
            ("导出海报帧", self.v_poster), ("跳过已存在", self.v_skip),
            ("低占用模式", self.v_eco),
        ]
        for i, (txt, var) in enumerate(opts):
            self._check(c, txt, var).grid(row=i // 2, column=i % 2,
                                          sticky="w", pady=2)
        self._label(c, "并行进程 (0=自动, 低占用最多 2)").grid(
            row=5, column=0, sticky="w", pady=(6, 0))
        tk.Spinbox(c, from_=0, to=8, textvariable=self.v_jobs, width=5,
                   bg=CARD2, fg=TEXT, relief="flat", font=(_FF, 9),
                   buttonbackground=CARD2, highlightbackground=LINE,
                   highlightthickness=1).grid(row=5, column=1, sticky="w",
                                              padx=6, pady=(6, 0))

    def _build_right(self, parent) -> None:
        tk.Label(parent, text="画面预览", bg=BG, fg=ACCENT,
                 font=(_FF, 10, "bold")).pack(anchor="w", pady=(0, 6))
        self.preview = tk.Canvas(parent, width=350, height=216, bg=CARD,
                                 highlightbackground=LINE, highlightthickness=1)
        self.preview.pack(fill="x")
        self.preview.bind("<Configure>", lambda e: self._redraw_preview())

        tk.Label(parent, text="当前配置", bg=BG, fg=ACCENT,
                 font=(_FF, 10, "bold")).pack(anchor="w", pady=(14, 6))
        self.summary = tk.Text(parent, height=9, bg=CARD, fg=TEXT,
                               relief="flat", font=(_FF, 9),
                               highlightbackground=LINE,
                               highlightthickness=1, wrap="word")
        self.summary.pack(fill="x")
        self.summary.configure(state="disabled")

        tk.Label(parent, text="提示: 低占用模式会关闭逐帧过渡动画、\n"
                              "限制 BLAS 线程并把进程数收敛到 2,\n"
                              "后台渲染时可正常浏览网页、办公。",
                 bg=BG, fg="#6E8098", font=(_FF, 8), justify="left",
                 anchor="w").pack(anchor="w", pady=(14, 0))

    def _build_actions(self, parent) -> None:
        bar = tk.Frame(parent, bg=BG)
        bar.pack(fill="x")
        specs = [
            ("棋谱信息", "info", False), ("预览画面", "preview", False),
            ("试运行", "dry", False), ("性能基准", "bench", False),
            ("开始渲染", "render", True),
        ]
        for text, mode, primary in specs:
            b = self._button(bar, text, lambda m=mode: self._run(m),
                             primary=primary)
            b.pack(side="left", padx=(0, 7))
            self.buttons[mode] = b
        self.btn_stop = self._button(bar, "停止", self._stop, primary=False)
        self.btn_stop.pack(side="right")
        self.btn_stop.configure(state="disabled")

        pf = tk.Frame(parent, bg=BG)
        pf.pack(fill="x", pady=(10, 4))
        self.progress = ttk.Progressbar(
            pf, style="SGFR.Horizontal.TProgressbar",
            mode="determinate", maximum=100)
        self.progress.pack(side="left", fill="x", expand=True)
        tk.Label(pf, textvariable=self.v_status, width=16, anchor="e",
                 bg=BG, fg=ACCENT2, font=(_FF, 9, "bold")).pack(side="right",
                                                                padx=(10, 0))

        self.log = scrolledtext.ScrolledText(
            parent, height=7, bg="#0E141D", fg=DIM, relief="flat",
            font=(_FM, 9), wrap="word", highlightbackground=LINE,
            highlightthickness=1, insertbackground=ACCENT)
        self.log.pack(fill="x", pady=(4, 0))
        self.log.configure(state="disabled")
        self._log("就绪。选择 SGF 棋谱后可直接点「开始渲染」，或先用「预览画面」看效果。")

    def _button(self, parent, text, cmd, primary=False, small=False):
        bg = ACCENT if primary else CARD2
        fg = "#0B1420" if primary else TEXT
        b = tk.Button(parent, text=text, command=cmd, font=(_FF, 9, "bold"),
                      bg=bg, fg=fg, activebackground=ACCENT2 if primary else LINE,
                      activeforeground=fg, relief="flat", bd=0,
                      padx=8 if small else 14, pady=4 if small else 7,
                      cursor="hand2")
        b.bind("<Enter>", lambda e, w=b, c=bg: w.configure(
            bg=_mix(c, "#FFFFFF", 0.14)))
        b.bind("<Leave>", lambda e, w=b, c=bg: w.configure(bg=c))
        return b

    # ------------------------------------------------------------------ #
    # 视觉: 标题栏淡入 + 预览占位
    # ------------------------------------------------------------------ #
    def _animate(self, step: int) -> None:
        self._hdr_ratio = min(1.0, step / 12.0)
        self._draw_header()
        if step < 12:
            self.root.after(28, lambda: self._animate(step + 1))

    def _draw_header(self) -> None:
        c = self.header
        w, h = max(1, c.winfo_width()), 76
        k = self._hdr_ratio
        c.delete("all")
        a, b = _mix(BG, "#16283E", k), _mix(BG, "#264B6E", k)
        for x in range(0, w, 6):
            c.create_rectangle(x, 0, x + 7, h,
                               fill=_mix(a, b, x / float(w)), outline="")
        c.create_text(20, 27, anchor="w", text="SGF Review · 围棋复盘工作台",
                      fill=_mix(BG, TEXT, k), font=(_FF, 15, "bold"))
        c.create_text(21, 53, anchor="w",
                      text="领地热力图 · AI 分析 · 一键成片     v2.0.0",
                      fill=_mix(BG, ACCENT2, k), font=(_FF, 9))
        for i, col in enumerate(("#F2EDE0", ACCENT, WARM)):
            cx = w - 34 - i * 20
            c.create_oval(cx - 6, 32, cx + 6, 44, fill=col, outline="",
                          stipple="gray50" if k < 0.9 else "")
        c.create_line(0, h - 1, w, h - 1,
                      fill=_mix(BG, ACCENT, k * 0.65), width=1)

    def _placeholder(self, w: int, h: int) -> None:
        c = self.preview
        c.create_text(w / 2, 18, text="选择棋谱后点「预览画面」",
                      fill=DIM, font=(_FF, 9))
        s = min(w - 60, h - 54) / 8.0
        x0, y0 = (w - s * 8) / 2, (h - s * 8) / 2 + 12
        for i in range(9):
            c.create_line(x0 + i * s, y0, x0 + i * s, y0 + 8 * s,
                          fill="#3B4C63")
            c.create_line(x0, y0 + i * s, x0 + 8 * s, y0 + i * s,
                          fill="#3B4C63")
        for gx, gy, col in ((2, 2, "#F5F0E4"), (6, 2, "#10151D"),
                            (4, 4, "#F5F0E4"), (2, 6, "#10151D")):
            cx, cy = x0 + gx * s, y0 + gy * s
            rr = s * 0.40
            c.create_oval(cx - rr, cy - rr, cx + rr, cy + rr,
                          fill=col, outline="#5A6B82")

    def _redraw_preview(self) -> None:
        c = self.preview
        w, h = c.winfo_width() or 350, c.winfo_height() or 216
        c.delete("all")
        c.create_rectangle(0, 0, w, h, fill=CARD, outline="")
        if self.photo is not None:
            c.create_image(w / 2, h / 2, image=self.photo, anchor="center")
        else:
            self._placeholder(w, h)

    def _show_preview(self, path: str) -> None:
        try:
            img = tk.PhotoImage(file=path)
            w = self.preview.winfo_width() or 350
            k = max(1, int(math.ceil(img.width() / float(max(1, w - 10)))))
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
        # GUI 默认保持低占用; 预设只负责「打开」eco, 不负责关掉它
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
            f"方案    {scheme}",
            f"画面    {self.v_size.get()} @ {self.v_fps.get()}fps · {label}",
            f"时长    {self.v_duration.get() or '自动(按棋谱节奏)'} 秒",
            f"编码    {self.v_codec.get()} / {self.v_quality.get()}",
            f"特效    {' · '.join(feat) if feat else '纯棋盘'}",
            f"资源    {res}",
            f"输出    {self.v_out.get() or '自动命名'}",
        ]
        self.summary.configure(state="normal")
        self.summary.delete("1.0", "end")
        self.summary.insert("1.0", "\n".join(lines))
        self.summary.configure(state="disabled")

    # ------------------------------------------------------------------ #
    # 子进程执行
    # ------------------------------------------------------------------ #
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
        self.progress["value"] = 0
        argv = self._build_args(mode)
        self._update_summary()
        self._log(f"$ sgf-review {' '.join(argv[1:])}")
        try:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            self.proc = subprocess.Popen(
                _base_cmd() + argv, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                creationflags=flags)
        except Exception as e:
            self._log(f"启动失败: {e}")
            self._finish()
            return
        self._set_busy(True)
        threading.Thread(target=self._reader, args=(self.proc, self.q),
                         daemon=True).start()
        self.root.after(70, self._poll)

    def _reader(self, proc, q: queue.Queue) -> None:
        """逐字符读, 以便把 CLI 用 \\r 刷新的进度条也当成一行."""
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
            # 进程已退出, 留给读取线程一点时间把尾部日志推完
            self.tail += 1
            if self.tail < 40 and not self.finished:
                self.root.after(70, self._poll)
            elif not self.finished:
                self._finish()

    def _handle_line(self, line: str) -> None:
        m = re.search(r"\[[█░ ]+\]\s*([\d.]+)%", line)
        if m:
            pct = float(m.group(1))
            self.progress["value"] = pct
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
            self.progress["value"] = 100
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
        self._log("正在停止渲染…")
        self.proc.terminate()
        try:
            self.proc.wait(timeout=3)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass

    def _set_busy(self, busy: bool) -> None:
        for b in self.buttons.values():
            b.configure(state="disabled" if busy else "normal")
        self.btn_stop.configure(state="normal" if busy else "disabled")
        if busy:
            self.v_status.set("运行中…")

    def _log(self, text: str) -> None:
        self.log.configure(state="normal")
        # 控制日志长度, 长时间批量渲染不会让内存无限增长
        if int(self.log.index("end-1c").split(".")[0]) > 800:
            self.log.delete("1.0", "200.0")
        self.log.insert("end", text.rstrip() + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _on_close(self) -> None:
        if self.proc and self.proc.poll() is None:
            if not messagebox.askokcancel("退出", "渲染仍在进行，确定停止并退出？",
                                          parent=self.root):
                return
            self.cancelled = True
            try:
                self.proc.terminate()
            except Exception:
                pass
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        self.root.destroy()
