"""工作流辅助: 预设、配置文件、试运行、性能基准、章节导出、资源上限.

这些功能与渲染本身解耦, 便于命令行和图形界面共用。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Dict, List, Optional, Sequence

from .cli import build_parser
from .presets import CONFIG_KEYS, PRESET_DESC, PRESETS


# --------------------------------------------------------------------------- #
# 参数预设与配置文件
# --------------------------------------------------------------------------- #
def _explicit_dests(argv: Sequence[str]) -> set:
    """找出命令行里用户显式指定过的参数名 (用于确定覆盖优先级)."""
    parser = build_parser()
    table = parser._option_string_actions
    out = set()
    for tok in argv or []:
        key = tok.split("=", 1)[0]
        act = table.get(key)
        if act is not None:
            out.add(act.dest)
    return out


def _coerce(parser: argparse.ArgumentParser, dest: str, value):
    """把配置里的字符串按目标参数的类型/choices 规整一遍."""
    parser = parser
    act = next((a for a in parser._actions if a.dest == dest), None)
    if act is None:
        return value
    try:
        if isinstance(act, argparse._StoreTrueAction):
            return bool(value)
        if isinstance(act, argparse._StoreAction) and act.type is not None:
            if isinstance(value, str) or not isinstance(value, act.type):
                return act.type(value)
    except (ValueError, TypeError, argparse.ArgumentTypeError):
        return value
    return value


def apply_preset_config(args, argv: Optional[Sequence[str]] = None):
    """按 预设 -> 配置文件 -> 显式命令行 的顺序合并参数.

    返回 (args, notes): notes 是给用户看的合并说明。
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    explicit = _explicit_dests(argv)
    parser = build_parser()
    notes: List[str] = []

    # 配置文件里也可能带 preset
    cfg: Dict = {}
    if args.config:
        with open(args.config, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
        cfg = cfg.get("settings", cfg) if isinstance(cfg, dict) else {}
        if "preset" not in explicit and cfg.get("preset") and not args.preset:
            args.preset = cfg["preset"]

    if args.preset:
        key = str(args.preset).lower()
        if key not in PRESETS:
            raise ValueError(f"未知预设 '{key}', 可选: {', '.join(PRESETS)}")
        for k, v in PRESETS[key].items():
            if k not in explicit and hasattr(args, k):
                setattr(args, k, _coerce(parser, k, v))
        notes.append(f"预设 {key}")

    for k, v in cfg.items():
        if k in ("preset",) or k not in CONFIG_KEYS:
            continue
        if k not in explicit and hasattr(args, k):
            setattr(args, k, _coerce(parser, k, v))
    if cfg:
        notes.append(f"配置 {os.path.basename(args.config)}")

    # 派生开关 -> Renderer 参数
    args.heatmap_transition = not getattr(args, "no_heat_transition", False)
    args.heatmap_legend = not getattr(args, "no_heat_legend", False)
    if getattr(args, "eco", False):
        if args.jobs <= 0 or "jobs" not in explicit:
            args.jobs = min(args.jobs if args.jobs > 0 else 2, 2)
        args.heatmap_transition = False
    return args, notes


def save_config(args, path: str) -> None:
    data = {"tool": "sgf-review", "settings": {}}
    for k in CONFIG_KEYS:
        # preset 不再写回: 其余参数已是展开后的最终值, 否则会二次覆盖
        if k == "preset":
            continue
        if hasattr(args, k):
            v = getattr(args, k)
            if isinstance(v, (str, int, float, bool)) or v is None:
                data["settings"][k] = v
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    print(f"配置已保存: {path}")


def list_presets() -> None:
    print("可用预设方案 (--preset NAME):\n")
    for k, v in PRESETS.items():
        desc = PRESET_DESC.get(k, "")
        print(f"  {k:<10} {desc}")
        print(f"             {'  '.join(f'{a}={b}' for a, b in v.items())}")


# --------------------------------------------------------------------------- #
# 资源上限
# --------------------------------------------------------------------------- #
def eco_environment() -> None:
    """低占用模式: 限制底层 BLAS 线程, 避免 numpy 抢占所有核心.

    必须在 import numpy 之前调用才完全生效, 因此入口脚本会提前设置。
    """
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS",
                 "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ.setdefault(name, "1")


def cap_jobs(jobs: int, eco: bool = False) -> int:
    if eco:
        return max(1, min(2, jobs))
    return max(1, min(4, jobs))


# --------------------------------------------------------------------------- #
# 试运行 / 性能基准
# --------------------------------------------------------------------------- #
def print_plan(rv, tl, cfg: Dict, target: str, jobs: int) -> None:
    """不渲染, 只打印这次会做什么 (便于批量前确认参数)."""
    w, h = cfg["width"], cfg["height"]
    fps = cfg["fps"]
    n = max(1, int(round(tl.total * fps)))
    has_hm = bool(cfg.get("heatmap")) and getattr(rv.series, "has_ownership", False)
    print("\n渲染计划 (--dry-run, 未生成任何文件)")
    print(f"  棋谱     {os.path.basename(rv.source_path)}  "
          f"{rv.info.size_x}路 / {len(rv.moves)} 手")
    print(f"  输出     {target}")
    print(f"  画面     {w}x{h} @ {fps}fps · {cfg['theme'].label} · "
          f"{cfg['quality']}/{cfg['codec']}")
    print(f"  时长     {tl.total:.1f}s  ({n} 帧, 片头 {tl.timing.intro:.1f}s / "
          f"片尾 {tl.timing.outro:.1f}s)")
    print(f"  资源     {jobs} 进程" + (" · 低占用模式" if cfg.get("eco") else ""))
    feat = []
    if cfg.get("analysis"):
        feat.append("AI 分析面板")
    if has_hm:
        feat.append("领地热力图")
    if cfg.get("comment"):
        feat.append("评论字幕")
    print(f"  特性     {' / '.join(feat) if feat else '纯棋盘'}")
    print(f"  附属导出 {', '.join(_side_names(cfg)) or '无'}")


def _side_names(cfg: Dict) -> List[str]:
    out = []
    for key, label in (("srt", "SRT"), ("json", "JSON"), ("poster", "海报"),
                       ("gif", "GIF"), ("theme_sheet", "主题图"),
                       ("highlights", "集锦"), ("chapters", "章节")):
        if cfg.get(key):
            out.append(label)
    return out


def run_benchmark(rnd, samples: int = 36) -> Dict:
    """试渲染一段连续帧, 报告稳态单帧耗时与整片预估, 不写文件.

    真实渲染是逐帧连续的, 同一手内多帧会命中图层缓存; 用连续帧测量
    才能反映实际吞吐 (分散采样会不断触发栅格化, 严重高估耗时)。
    """
    n = max(1, rnd.n_frames)
    # 从中盘开始取一段连续帧, 覆盖若干手
    start = max(0, min(n - samples, int(n * 0.5)))
    picks = list(range(start, min(n, start + samples)))
    t0 = time.perf_counter()
    for i in picks:
        rnd.render_frame(i)
    dt = time.perf_counter() - t0
    per = dt / max(1, len(picks))
    rss = _peak_rss_mb()
    return {
        "frames": len(picks), "per_frame_ms": per * 1000,
        "total_frames": n, "estimate_s": per * n,
        "fps_equiv": 1.0 / max(1e-6, per), "rss_mb": rss,
        "steady": True,
    }


def _peak_rss_mb() -> float:
    try:
        import resource
        v = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return v / 1024.0 if sys.platform != "darwin" else v / (1024 ** 2)
    except Exception:
        return 0.0


def print_benchmark(res: Dict, jobs: int) -> None:
    print(f"\n渲染性能基准 ({res['frames']} 连续帧, 含图层缓存命中)")
    print(f"  稳态单帧   {res['per_frame_ms']:.1f} ms  "
          f"({res['fps_equiv']:.0f} 帧/秒 单进程)")
    print(f"  整片预估   {res['estimate_s'] / max(1, jobs):.0f}s "
          f"({res['total_frames']} 帧 / {jobs} 进程, 不含编码)")
    if res["rss_mb"]:
        print(f"  峰值内存   {res['rss_mb']:.0f} MB / 进程")


# --------------------------------------------------------------------------- #
# 章节导出
# --------------------------------------------------------------------------- #
def _ts(sec: float) -> str:
    s = max(0, int(round(sec)))
    h, m, x = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}:{m:02d}:{x:02d}" if h else f"{m:02d}:{x:02d}"


def write_chapters(rv, tl, path: str, lang: str = "zh", max_items: int = 24) -> int:
    """导出 YouTube / B 站章节时间戳.

    以片头为 00:00, 之后按 AI 关键手(失误/好手/转折)生成章节; 关键手太少时
    用等间隔手数补齐, 保证长视频也能快速跳转。
    """
    zh = lang == "zh"
    info = rv.info
    lines: List[str] = []
    title = f"{info.black or ('黑方' if zh else 'Black')} vs " \
            f"{info.white or ('白方' if zh else 'White')}"
    lines.append(f"00:00 {'开局' if zh else 'Opening'} · {title}")

    items = []
    ser = rv.series
    if ser is not None and ser.key_moments:
        for k in ser.key_moments:
            if 0 <= k.index < len(tl.slots):
                items.append((tl.slots[k.index].start, k.number, k.label))
    if len(items) < 6 and rv.moves:
        step = max(1, len(rv.moves) // 8)
        for i in range(step - 1, len(rv.moves), step):
            if i < len(tl.slots):
                mv = rv.moves[i]
                items.append((tl.slots[i].start, mv.number,
                              f"第 {mv.number} 手" if zh else f"Move {mv.number}"))
    items.sort(key=lambda x: x[0])
    # 去重 + 最小间隔, 避免章节挤在一起
    last = -10.0
    for t, num, label in items:
        if t - last < 4.0:
            continue
        last = t
        if zh:
            lines.append(f"{_ts(t)} 第 {num} 手 · {label}")
        else:
            lines.append(f"{_ts(t)} Move {num} · {label}")
        if len(lines) >= max_items:
            break
    if tl.outro_start > last + 4.0:
        main = (info.result or "").strip()
        lines.append(f"{_ts(tl.outro_start)} "
                     f"{'终局 · ' + main if zh and main else ('终局' if zh else 'Endgame')}")
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return len(lines)
