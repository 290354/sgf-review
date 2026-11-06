"""命令行入口."""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import os
import shutil
import sys
import tempfile
import time
from typing import Dict, List, Optional, Sequence, Tuple

from . import audio as saudio
from . import encoder as senc
from .board import validate
from .renderer import Renderer, describe_komi, describe_result
from .sgf import parse_collection_reviews
from .theme import THEMES, get_theme
from .timeline import Timing, build_timeline

SIZE_PRESETS = {
    "1080p": (1920, 1080),
    "720p": (1280, 720),
    "1440p": (2560, 1440),
    "4k": (3840, 2160),
    "2160p": (3840, 2160),
    "vertical": (1080, 1920),
    "shorts": (1080, 1920),
    "square": (1080, 1080),
}


def parse_size(txt: str) -> Tuple[int, int]:
    s = (txt or "").strip().lower()
    if s in SIZE_PRESETS:
        return SIZE_PRESETS[s]
    if "x" in s:
        a, b = s.split("x", 1)
        try:
            w, h = int(a), int(b)
            if w >= 240 and h >= 240:
                return (w // 2 * 2, h // 2 * 2)
        except ValueError:
            pass
    raise argparse.ArgumentTypeError(f"无法识别的分辨率 '{txt}' (示例: 1920x1080, vertical, 4k)")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sgf-review",
        description="把围棋 SGF 棋谱渲染成精美的对局回顾视频。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""示例:
  python sgf_review.py game.sgf
  python sgf_review.py game.sgf --theme midnight --size vertical
  python sgf_review.py game.sgf --duration 90 --labels all -o out.mp4
  python sgf_review.py game.sgf --preview preview.png
""")
    p.add_argument("inputs", nargs="*", help="SGF 文件 (可多个)")
    p.add_argument("-o", "--output", help="输出视频路径")
    p.add_argument("-t", "--theme", default="classic",
                   help="主题: " + ", ".join(THEMES) + " (默认 classic)")
    p.add_argument("-s", "--size", default="1080p", help="分辨率: 1920x1080 / vertical / 4k")
    p.add_argument("--fps", type=int, default=30, help="帧率 (默认 30)")
    p.add_argument("--speed", type=float, default=1.0, help="整体节奏倍率, 越大越快")
    p.add_argument("--duration", type=float, default=None, help="目标总时长(秒), 自动分配每手时长")
    p.add_argument("--labels", default="recent", choices=["none", "last", "recent", "all"],
                   help="棋盘手数标注: none/last/recent/all (默认 recent)")
    p.add_argument("--label-count", type=int, default=6, help="recent 模式显示最近几手")
    p.add_argument("--no-comment", action="store_true", help="不显示棋谱注释字幕")
    p.add_argument("--no-coords", action="store_true", help="不显示棋盘坐标")
    p.add_argument("--lang", default="zh", choices=["zh", "en"], help="界面语言")
    p.add_argument("--watermark", default="", help="右下角水印文字")
    p.add_argument("--bgm", default=None, help="背景音乐文件 (mp3/wav/m4a)")
    p.add_argument("--bgm-gain", type=float, default=0.20, help="BGM 音量 0~1")
    p.add_argument("--no-audio", action="store_true", help="不生成落子音效")
    p.add_argument("--no-analysis", action="store_true",
                   help="不显示 AI 分析面板(即使棋谱含 LizzieYzy 分析数据)")
    p.add_argument("--heatmap", action="store_true",
                   help="在棋盘上叠加 AI 领地热力图(需 ownership 数据)")
    p.add_argument("--no-ai-commentary", action="store_true",
                   help="不根据胜率变化自动生成 AI 点评字幕")
    p.add_argument("--line", default="longest", choices=["longest", "first", "last"],
                   help="变体主线选择策略")
    p.add_argument("--game", default="1", help="多局棋谱中选择第几局 (默认 1, all=全部)")
    p.add_argument("--quality", default="high", choices=["low", "medium", "high", "ultra"])
    p.add_argument("--codec", default="h264", choices=["h264", "vp9"])
    p.add_argument("-j", "--jobs", type=int, default=0, help="并行渲染进程数 (默认自动)")
    p.add_argument("--preview", default=None, help="只生成预览拼图 PNG, 不渲染视频")
    p.add_argument("--info", action="store_true", help="只打印棋谱信息")
    p.add_argument("--list-themes", action="store_true", help="列出所有主题")
    p.add_argument("--quiet", action="store_true")
    return p


# --------------------------------------------------------------------------- #
def human_time(sec: float) -> str:
    sec = max(0, int(round(sec)))
    return f"{sec // 60}:{sec % 60:02d}"


def build_renderer(cfg: Dict) -> Renderer:
    return Renderer(
        review=cfg["review"], timeline=cfg["timeline"], theme=cfg["theme"],
        width=cfg["width"], height=cfg["height"], fps=cfg["fps"],
        show_coords=cfg["coords"], labels=cfg["labels"],
        label_count=cfg["label_count"], show_comment=cfg["comment"],
        lang=cfg["lang"], watermark=cfg["watermark"],
        show_analysis=cfg.get("analysis", True),
        show_heatmap=cfg.get("heatmap", False),
        ai_commentary=cfg.get("ai_commentary", True),
    )


def _render_segment(job) -> Tuple[int, str]:
    start, end, path, cfg = job
    rnd = build_renderer(cfg)
    if start > 0:
        idx, _ = rnd.tl.slot_at(start / float(cfg["fps"]))
        rnd.seek(idx)
    proc = senc.open_writer(path, cfg["width"], cfg["height"], cfg["fps"],
                            cfg["quality"], cfg["codec"], audio=None)
    assert proc.stdin is not None
    try:
        for i in range(start, end):
            img = rnd.render_frame(i)
            proc.stdin.write(img.tobytes())
    finally:
        senc.close_writer(proc)
    return end - start, path


# --------------------------------------------------------------------------- #
def make_preview(rnd: Renderer, path: str, cfg: Dict) -> None:
    from PIL import Image, ImageDraw

    tl = rnd.tl
    n = len(rnd.moves)
    picks: List[float] = [tl.intro_end * 0.55]
    if n:
        picks.append(rnd.tl.slots[min(n - 1, max(0, n // 5))].start + 0.35)
        picks.append(rnd.tl.slots[min(n - 1, n // 2)].start + 0.35)
        picks.append(rnd.tl.slots[min(n - 1, int(n * 0.88))].start + 0.35)
    picks.append(tl.outro_start + 1.6)
    frames = [rnd.frame(t) for t in picks]

    cols = 2
    rows = (len(frames) + 1) // 2
    tw, th = frames[0].width // 2, frames[0].height // 2
    sheet = Image.new("RGB", (tw * cols + 12 * (cols + 1), th * rows + 12 * (rows + 1)),
                      (18, 16, 14))
    for i, f in enumerate(frames):
        x = 12 + (i % cols) * (tw + 12)
        y = 12 + (i // cols) * (th + 12)
        sheet.paste(f.resize((tw, th), Image.LANCZOS), (x, y))
    sheet.save(path)


# --------------------------------------------------------------------------- #
def print_info(rv, path: str) -> None:
    info = rv.info
    print(f"\n棋谱: {os.path.basename(path)}  ({rv.source_encoding})")
    print(f"棋盘: {info.size_x}x{info.size_y}" +
          (f"   让子 {info.handicap}" if info.handicap else "") +
          (f"   贴目 {info.komi:g}" if info.komi is not None else ""))
    print(f"黑方: {info.black or '—'} {info.black_rank}")
    print(f"白方: {info.white or '—'} {info.white_rank}")
    if info.event or info.date:
        print(f"赛事: {info.event} {info.round}  {info.date}")
    main, _ = describe_result(info.result, "zh")
    print(f"结果: {main or '—'}")
    print(f"手数: {len(rv.moves)}" + (f"   分支: {rv.branch_count}" if rv.branch_count else ""))
    if rv.setup_black or rv.setup_white:
        print(f"初始子: 黑 {len(rv.setup_black)} / 白 {len(rv.setup_white)}")
    ser = rv.series
    if ser is not None and ser.count:
        rate = ser.matched_moves / max(1, ser.judged_moves) * 100
        print(f"AI 分析: {ser.engine or 'AI'}  {ser.count}/{len(rv.moves)} 手 "
              f"({ser.coverage*100:.0f}%)  吻合度 {rate:.0f}%"
              + ("  含领地数据" if ser.has_ownership else ""))
        if ser.engine2:
            print(f"副引擎: {ser.engine2}")
        if ser.key_moments:
            worst = min(ser.key_moments, key=lambda k: k.swing)
            print(f"最大失误: 第 {worst.number} 手 {worst.swing:+.1f}%")
    if rv.warnings:
        for w in rv.warnings:
            print(f"  ! {w}")


# --------------------------------------------------------------------------- #
def run_one(args, path: str, out_path: Optional[str]) -> int:
    reviews = parse_collection_reviews(path, args.line)
    if not reviews:
        print(f"错误: 未找到棋谱 {path}", file=sys.stderr)
        return 2

    if args.game == "all" and len(reviews) > 1:
        rv_list = reviews
    else:
        try:
            idx = int(args.game) - 1
        except ValueError:
            idx = 0
        idx = max(0, min(idx, len(reviews) - 1))
        rv_list = [reviews[idx]]

    if len(reviews) > 1 and args.game != "all" and not args.quiet:
        print(f"提示: 文件包含 {len(reviews)} 局棋, 渲染第 {idx + 1} 局 (--game all 可全部渲染)")

    outs: List[str] = []
    for gi, rv in enumerate(rv_list):
        if not args.quiet:
            print_info(rv, path)
        problems = validate(rv)
        rv.warnings.extend(problems)
        if problems and not args.quiet:
            for pb in problems[:5]:
                print(f"  ! {pb}")

        theme = get_theme(args.theme)
        w, h = parse_size(args.size)
        timing = Timing(total=args.duration, speed=args.speed)
        tl = build_timeline(rv, timing)

        cfg = dict(
            review=rv, timeline=tl, theme=theme, width=w, height=h, fps=args.fps,
            coords=not args.no_coords, labels=args.labels,
            label_count=args.label_count, comment=not args.no_comment,
            lang=args.lang, watermark=args.watermark,
            analysis=not args.no_analysis, heatmap=args.heatmap,
            ai_commentary=not args.no_ai_commentary,
            quality=args.quality, codec=args.codec,
        )
        rnd = build_renderer(cfg)

        if args.preview:
            os.makedirs(os.path.dirname(os.path.abspath(args.preview)) or ".", exist_ok=True)
            make_preview(rnd, args.preview, cfg)
            print(f"预览图已生成: {args.preview}  ({tl.total:.1f}s, {rnd.n_frames} 帧)")
            return 0

        target = out_path
        if target is None:
            stem = os.path.splitext(os.path.basename(path))[0]
            suffix = f"_g{gi + 1}" if len(rv_list) > 1 else ""
            ext = ".webm" if args.codec == "vp9" else ".mp4"
            os.makedirs("output", exist_ok=True)
            target = os.path.join("output", f"{stem}{suffix}_{args.theme}{ext}")

        os.makedirs(os.path.dirname(os.path.abspath(target)) or ".", exist_ok=True)
        t0 = time.time()
        nframes = rnd.n_frames

        work = tempfile.mkdtemp(prefix="sgfr_")
        try:
            # --- 音轨 ---
            wav = os.path.join(work, "audio.wav")
            has_audio = not args.no_audio or bool(args.bgm)
            if has_audio:
                saudio.build_audio(rv, tl, wav,
                                   mode="none" if args.no_audio else "click",
                                   bgm=args.bgm, bgm_gain=args.bgm_gain)

            jobs = args.jobs
            if jobs <= 0:
                jobs = _auto_jobs()
            jobs = max(1, min(jobs, max(1, nframes // 45)))

            if not args.quiet:
                print(f"\n渲染中: {nframes} 帧 · {w}x{h}@{args.fps} · "
                      f"时长 {human_time(tl.total)} · {jobs} 进程")
                print(f"       主题 {theme.label} / {rv.info.size_x}路 / "
                      f"{len(rv.moves)} 手")

            if jobs <= 1:
                seg = os.path.join(work, "seg_000.mp4")
                proc = senc.open_writer(seg, w, h, args.fps, args.quality,
                                        args.codec, audio=wav if has_audio else None)
                assert proc.stdin is not None
                try:
                    for i in range(nframes):
                        proc.stdin.write(rnd.render_frame(i).tobytes())
                        if not args.quiet and i % 30 == 0:
                            _bar(i, nframes, t0)
                finally:
                    senc.close_writer(proc)
                if not args.quiet:
                    _bar(nframes, nframes, t0)
                shutil.move(seg, target)
            else:
                segs = _run_parallel(nframes, jobs, work, cfg, args.quiet, t0)
                joined = os.path.join(work, "joined.mp4")
                senc.concat_segments(segs, joined, work)
                if has_audio:
                    senc.mux_audio(joined, wav, target)
                else:
                    shutil.move(joined, target)

            dt = time.time() - t0
            size_mb = os.path.getsize(target) / 1024 / 1024
            if not args.quiet:
                print(f"\n完成: {target}")
                print(f"      {human_time(tl.total)} · {size_mb:.1f} MB · 耗时 {dt:.1f}s")
            outs.append(target)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    if len(outs) > 1 and not args.quiet:
        print(f"\n共生成 {len(outs)} 个视频")
    return 0


def _split(total: int, parts: int) -> List[Tuple[int, int]]:
    parts = max(1, min(parts, total))
    base = total // parts
    rem = total % parts
    out, cur = [], 0
    for i in range(parts):
        n = base + (1 if i < rem else 0)
        if n <= 0:
            continue
        out.append((cur, cur + n))
        cur += n
    return out


def _bar(done: int, total: int, t0: float) -> None:
    frac = done / max(1, total)
    width = 28
    filled = int(width * frac)
    bar = "█" * filled + "░" * (width - filled)
    el = time.time() - t0
    eta = el / max(1e-6, frac) - el
    sys.stdout.write(f"\r  [{bar}] {frac * 100:5.1f}%  {done}/{total}  "
                     f"剩余 {human_time(eta)}   ")
    sys.stdout.flush()


# --------------------------------------------------------------------------- #
def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if args.list_themes:
        print("可用主题:")
        for k, t in THEMES.items():
            print(f"  {k:<10} {t.label}")
        return 0

    if not args.inputs:
        build_parser().print_help()
        return 1

    inputs = []
    for pat in args.inputs:
        if os.path.isdir(pat):
            for f in sorted(os.listdir(pat)):
                if f.lower().endswith((".sgf", ".sgfs")):
                    inputs.append(os.path.join(pat, f))
        elif os.path.exists(pat):
            inputs.append(pat)
        elif any(c in pat for c in "*?"):
            import glob
            inputs.extend(sorted(glob.glob(pat)))
        else:
            print(f"错误: 找不到 {pat}", file=sys.stderr)
            return 2
    if not inputs:
        print("错误: 没有可处理的 SGF 文件", file=sys.stderr)
        return 2

    if args.info:
        for p in inputs:
            for rv in parse_collection_reviews(p, args.line):
                print_info(rv, p)
        return 0

    rc = 0
    for i, p in enumerate(inputs):
        out = args.output if (args.output and len(inputs) == 1) else None
        if args.output and len(inputs) > 1:
            stem = os.path.splitext(os.path.basename(p))[0]
            out = os.path.join(os.path.dirname(args.output),
                               f"{stem}.{os.path.splitext(args.output)[1]}")
        rc = run_one(args, p, out) or rc
    return rc


# --------------------------------------------------------------------------- #
# 并行与资源
# --------------------------------------------------------------------------- #
def _cgroup_cpus() -> float:
    """读取 cgroup 的 CPU 配额 (容器里 os.cpu_count() 常常偏大)."""
    try:
        with open("/sys/fs/cgroup/cpu.max", "r") as fh:
            quota, period = fh.read().split()[:2]
        if quota != "max":
            return max(1.0, int(quota) / max(1, int(period)))
    except Exception:
        pass
    try:
        with open("/sys/fs/cgroup/cpu/cpu.cfs_quota_us", "r") as fh:
            quota = int(fh.read().strip())
        with open("/sys/fs/cgroup/cpu/cpu.cfs_period_us", "r") as fh:
            period = int(fh.read().strip())
        if quota > 0 and period > 0:
            return max(1.0, quota / period)
    except Exception:
        pass
    return float(os.cpu_count() or 4)


def _mem_gb() -> float:
    try:
        with open("/sys/fs/cgroup/memory.max", "r") as fh:
            v = fh.read().strip()
        if v != "max":
            return int(v) / (1024 ** 3)
    except Exception:
        pass
    try:
        with open("/sys/fs/cgroup/memory/memory.limit_in_bytes", "r") as fh:
            v = int(fh.read().strip())
        if 0 < v < (1 << 60):
            return v / (1024 ** 3)
    except Exception:
        pass
    try:
        import shutil as _sh
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / (1024 ** 3)
    except Exception:
        return 8.0


def _auto_jobs() -> int:
    """按 CPU 配额与可用内存决定并行进程数 (每个进程约 1.1GB)."""
    cpus = int(max(1, min(_cgroup_cpus(), os.cpu_count() or 4)))
    by_mem = int(max(1, (_mem_gb() * 0.72) // 1.1))
    return int(max(1, min(8, cpus, by_mem)))


def _run_parallel(nframes: int, jobs: int, work: str, cfg,
                  quiet: bool, t0: float):
    """分段并行渲染; 进程被杀时自动降级重试."""
    attempt = jobs
    while True:
        bounds = _split(nframes, attempt)
        tasks = [(a, b, os.path.join(work, f"seg_{k:03d}.mp4"), cfg)
                 for k, (a, b) in enumerate(bounds)]
        try:
            done = 0
            with cf.ProcessPoolExecutor(max_workers=attempt) as ex:
                futs = {ex.submit(_render_segment, t): t for t in tasks}
                for fut in cf.as_completed(futs):
                    cnt, _ = fut.result()
                    done += cnt
                    if not quiet:
                        _bar(done, nframes, t0)
            return [t[2] for t in tasks]
        except (cf.process.BrokenProcessPool, MemoryError, OSError) as e:
            for t in tasks:
                try:
                    os.unlink(t[2])
                except OSError:
                    pass
            if attempt <= 1:
                raise
            attempt = max(1, attempt // 2)
            if not quiet:
                print(f"\n  ! 并行进程被系统回收 ({type(e).__name__}), "
                      f"降级为 {attempt} 进程重试")
