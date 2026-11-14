"""命令行入口."""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import math
import os
import shutil
import sys
import tempfile
import time
from typing import Dict, List, Optional, Sequence, Tuple

from . import audio as saudio
from . import encoder as senc
from . import export as sexp
from .board import validate
from .renderer import Renderer, describe_komi, describe_result
from .sgf import parse_collection_reviews
from .theme import THEMES, get_theme
from .timeline import Timing, build_timeline
# 常量独立成模块, 图形界面复用时不会连带加载 numpy / Pillow
from .presets import PRESETS, parse_size


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
  python sgf_review.py game.sgf --gif preview.gif --srt out.srt --json out.json
  python sgf_review.py game.sgf --no-video --poster poster.png --theme-sheet themes.png
  python sgf_review.py game.sgf --highlights key.mp4 --no-video
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
    p.add_argument("--kit", default="kaya",
                   choices=["kaya", "slate", "paper", "ice"],
                   help="落子音效包: kaya 榧木 / slate 石板 / paper 纸面 / ice 清脆")
    p.add_argument("--no-analysis", action="store_true",
                   help="不显示 AI 分析面板(即使棋谱含 LizzieYzy 分析数据)")
    p.add_argument("--heatmap", action="store_true",
                   help="在棋盘上叠加 AI 领地热力图(需 ownership 数据)")
    p.add_argument("--heat-strength", type=float, default=1.0, metavar="0~1.5",
                   help="领地热力图浓度 (默认 1.0)")
    p.add_argument("--no-heat-transition", action="store_true",
                   help="关闭领地色块的逐手流动过渡(静态显示, 更省资源)")
    p.add_argument("--no-heat-legend", action="store_true",
                   help="不显示棋盘上方的领地配色图例")
    p.add_argument("--no-ai-commentary", action="store_true",
                   help="不根据胜率变化自动生成 AI 点评字幕")
    p.add_argument("--line", default="longest", choices=["longest", "first", "last"],
                   help="变体主线选择策略")
    p.add_argument("--game", default="1", help="多局棋谱中选择第几局 (默认 1, all=全部)")
    p.add_argument("--quality", default="high", choices=["low", "medium", "high", "ultra"])
    p.add_argument("--codec", default="h264", choices=["h264", "hevc", "vp9"])
    p.add_argument("-j", "--jobs", type=int, default=0, help="并行渲染进程数 (默认自动)")
    p.add_argument("--preview", default=None, help="只生成预览拼图 PNG, 不渲染视频")
    p.add_argument("--range", default=None,
                   help="只渲染指定片段: '80-120' (手数) 或 '12.5-30.2' (秒), 用于导出素材")
    p.add_argument("--gif", default=None,
                   help="额外导出循环 GIF (社交/IM 预览), 如 --gif out.gif")
    p.add_argument("--gif-fps", type=float, default=10.0, help="GIF 帧率 (默认 10)")
    p.add_argument("--gif-scale", type=float, default=0.40, help="GIF 缩放 (默认 0.4)")
    p.add_argument("--srt", default=None, help="导出 SRT 字幕 (可导入剪映/PR)")
    p.add_argument("--json", default=None, help="导出 JSON 元数据 (手数/分析/时间轴)")
    p.add_argument("--poster", default=None, help="导出海报帧 PNG (默认中盘局面)")
    p.add_argument("--poster-move", type=int, default=None, help="海报帧显示到第几手")
    p.add_argument("--theme-sheet", default=None,
                  help="导出多主题对比图 (同一局面), 如 --theme-sheet themes.png")
    p.add_argument("--highlights", default=None,
                   help="另出关键手集锦短片 (AI 失误/好手)")
    p.add_argument("--no-video", action="store_true",
                   help="不渲染主视频, 只做 GIF/SRT/JSON/海报/主题图/集锦")
    p.add_argument("--intro", type=float, default=3.1, help="片头时长秒")
    p.add_argument("--outro", type=float, default=3.4, help="片尾时长秒")
    p.add_argument("--no-intro", action="store_true", help="不要片头")
    p.add_argument("--no-outro", action="store_true", help="不要片尾")
    p.add_argument("--version", action="store_true", help="打印版本")
    p.add_argument("--info", action="store_true", help="只打印棋谱信息")
    p.add_argument("--list-themes", action="store_true", help="列出所有主题")
    p.add_argument("--list-presets", action="store_true", help="列出所有预设方案")
    p.add_argument("--quiet", action="store_true")

    # --- 工作流 / 资源 ---
    g = p.add_argument_group("工作流与资源")
    g.add_argument("--preset", default=None, metavar="NAME",
                   help="一键套用方案: " + ", ".join(PRESETS) + "  (--list-presets 查看详情)")
    g.add_argument("--config", default=None, metavar="JSON",
                   help="从 JSON 配置文件读取参数 (命令行显式参数优先)")
    g.add_argument("--save-config", default=None, metavar="JSON",
                   help="把本次生效的参数保存成 JSON 配置, 方便复用")
    g.add_argument("--dry-run", action="store_true",
                   help="只打印渲染计划(分辨率/帧数/时长/输出), 不实际渲染")
    g.add_argument("--benchmark", action="store_true",
                   help="试渲染若干帧并报告单帧耗时 / 预估总耗时 / 峰值内存")
    g.add_argument("--chapters", default=None, metavar="TXT",
                   help="导出 YouTube / B 站章节时间戳 (基于 AI 关键手)")
    g.add_argument("--eco", action="store_true",
                   help="低占用模式: 单/双进程、限制缓存与分辨率, 适合后台长时间渲染")
    g.add_argument("--skip-existing", action="store_true",
                   help="输出文件已存在时跳过, 便于批量任务中断后续跑")
    g.add_argument("--version", action="store_true", help="显示版本号并退出")
    return p


# --------------------------------------------------------------------------- #
def math_ceil(x: float) -> int:
    return int(math.ceil(x))


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
        heatmap_strength=cfg.get("heatmap_strength", 1.0),
        heatmap_transition=cfg.get("heatmap_transition", True),
        heatmap_legend=cfg.get("heatmap_legend", True),
        eco=cfg.get("eco", False),
        ai_commentary=cfg.get("ai_commentary", True),
    )


def _render_segment(job) -> Tuple[int, str]:
    start, end, path, cfg = job
    rnd = build_renderer(cfg)
    if start > 0:
        idx, _ = rnd.tl.slot_at(start / float(cfg["fps"]))
        rnd.seek(idx)
    proc = senc.open_writer(path, cfg["width"], cfg["height"], cfg["fps"],
                            cfg["quality"], cfg["codec"], audio=None,
                            threads=2 if cfg.get("eco") else 0)
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
def _stem_of(path: str) -> str:
    return os.path.splitext(path)[0]


def _side_exports(rnd, rv, tl, args, video_path: str) -> None:
    """主视频之外的一揽子导出 (GIF/SRT/JSON/海报/主题图/集锦)."""
    stem = _stem_of(video_path)
    if args.srt:
        p = args.srt if str(args.srt).endswith(".srt") else stem + ".srt"
        n = sexp.write_srt(rv, tl, p, args.lang,
                           ai_commentary=not args.no_ai_commentary)
        if not args.quiet:
            print(f"SRT 字幕: {p}  ({n} 条)")
    if args.json:
        p = args.json if str(args.json).endswith(".json") else stem + ".json"
        sexp.write_json(rv, tl, p)
        if not args.quiet:
            print(f"JSON 元数据: {p}")
    if args.poster:
        p = args.poster if str(args.poster).endswith(".png") else stem + "_poster.png"
        sexp.render_poster(rnd, p, args.poster_move)
        if not args.quiet:
            print(f"海报帧: {p}")
    if args.gif:
        p = args.gif if str(args.gif).endswith(".gif") else stem + ".gif"
        n = sexp.export_gif(rnd, p, fps=args.gif_fps, scale=args.gif_scale)
        if not args.quiet:
            print(f"GIF: {p}  ({n} 帧)")
    if args.theme_sheet:
        p = args.theme_sheet if str(args.theme_sheet).endswith(".png") else stem + "_themes.png"
        sexp.render_theme_sheet(rnd, rv, tl, p)
        if not args.quiet:
            print(f"主题对比图: {p}")
    if args.highlights:
        p = args.highlights if str(args.highlights).endswith(".mp4") else stem + "_key.mp4"
        n = sexp.render_highlights(rv, tl, rnd.th, rnd.W, rnd.H, rnd.fps, p,
                                   lang=args.lang)
        if not args.quiet:
            print(f"关键手集锦: {p}  ({n} 手)")
    if getattr(args, "chapters", None):
        from .workflow import write_chapters
        p = args.chapters if str(args.chapters).endswith(".txt") else stem + "_chapters.txt"
        n = write_chapters(rv, tl, p, args.lang)
        if not args.quiet:
            print(f"章节标记: {p}  ({n} 条)")


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
        timing = Timing(total=args.duration, speed=args.speed,
                        intro=0.0 if args.no_intro else args.intro,
                        outro=0.0 if args.no_outro else args.outro)
        tl = build_timeline(rv, timing)

        cfg = dict(
            review=rv, timeline=tl, theme=theme, width=w, height=h, fps=args.fps,
            coords=not args.no_coords, labels=args.labels,
            label_count=args.label_count, comment=not args.no_comment,
            lang=args.lang, watermark=args.watermark,
            analysis=not args.no_analysis, heatmap=args.heatmap,
            heatmap_strength=getattr(args, "heat_strength", 1.0),
            heatmap_transition=getattr(args, "heatmap_transition", True),
            heatmap_legend=getattr(args, "heatmap_legend", True),
            eco=getattr(args, "eco", False),
            ai_commentary=not args.no_ai_commentary,
            quality=args.quality, codec=args.codec,
            srt=args.srt, json=args.json, poster=args.poster, gif=args.gif,
            theme_sheet=args.theme_sheet, highlights=args.highlights,
            chapters=getattr(args, "chapters", None),
        )
        rnd = build_renderer(cfg)

        # 热力图请求但棋谱没有 ownership 数据时明确提示, 而不是静默忽略
        if args.heatmap and not rnd.show_heatmap and not args.quiet:
            if rv.series is None or not any(
                    an is not None and an.ownership for an in rv.series.points):
                print("提示: 该棋谱不含 ownership 领地数据, 已跳过热力图 "
                      "(需 LizzieYzy / KataGo 分析过的 SGF)")

        if getattr(args, "benchmark", False):
            from .workflow import run_benchmark, print_benchmark
            jobs = _auto_jobs() if args.jobs <= 0 else args.jobs
            jobs = max(1, min(2 if args.eco else 8, jobs))
            res = run_benchmark(rnd)
            print_benchmark(res, jobs)
            return 0

        if args.preview:
            os.makedirs(os.path.dirname(os.path.abspath(args.preview)) or ".", exist_ok=True)
            make_preview(rnd, args.preview, cfg)
            print(f"预览图已生成: {args.preview}  ({tl.total:.1f}s, {rnd.n_frames} 帧)")
            if args.json:
                sexp.write_json(rv, tl, args.json)
                print(f"JSON 已生成: {args.json}")
            return 0

        # 纯导出模式: 不渲染主视频
        only_side = any([args.gif, args.srt, args.json, args.poster,
                         args.theme_sheet, args.highlights,
                         getattr(args, "chapters", None)])

        target = out_path
        if target is None:
            stem = os.path.splitext(os.path.basename(path))[0]
            suffix = f"_g{gi + 1}" if len(rv_list) > 1 else ""
            ext = ".webm" if args.codec == "vp9" else ".mp4"
            os.makedirs("output", exist_ok=True)
            target = os.path.join("output", f"{stem}{suffix}_{args.theme}{ext}")

        # 容器由 codec 决定, 避免用户给的后缀与真实容器不符
        # (例如 vp9 写成 .mp4, 以前会把 WebM 内容塞进 MP4 文件名)
        ext = os.path.splitext(target)[1].lower()
        if args.codec == "vp9" and ext in (".mp4", ".mov", ".m4v"):
            target = os.path.splitext(target)[0] + ".webm"
            if not args.quiet:
                print(f"提示: vp9 使用 WebM 容器, 输出改名为 {target}")
        elif args.codec != "vp9" and ext == ".webm":
            target = os.path.splitext(target)[0] + ".mp4"
            if not args.quiet:
                print(f"提示: 该编码使用 MP4 容器, 输出改名为 {target}")

        os.makedirs(os.path.dirname(os.path.abspath(target)) or ".", exist_ok=True)

        # 断点续跑: 输出已存在且非空, 且没有指定附属导出时直接跳过
        if getattr(args, "skip_existing", False) and os.path.exists(target) \
                and os.path.getsize(target) > 4096 and not only_side:
            if not args.quiet:
                print(f"跳过(已存在): {target}")
            continue

        if getattr(args, "dry_run", False):
            from .workflow import print_plan
            jobs = _auto_jobs() if args.jobs <= 0 else args.jobs
            jobs = max(1, min(2 if args.eco else 8, jobs))
            print_plan(rv, tl, cfg, target, jobs)
            continue

        t0 = time.time()
        nframes = rnd.n_frames
        f0, f1 = 0, nframes

        if getattr(args, "no_video", False):
            _side_exports(rnd, rv, tl, args, target or "output/clip")
            if not args.quiet:
                print("已完成附属导出 (未渲染主视频)")
            continue

        if args.range:
            a, b = (args.range.split("-", 1) + [""])[:2]
            try:
                if a.strip().endswith("手") or (a.strip().isdigit() and "秒" not in a and "." not in a):
                    m0 = max(1, int(a.strip().rstrip("手")))
                    m1 = max(m0, int(b.strip().rstrip("手"))) if b.strip() else m0
                    f0 = max(0, int(rnd.tl.slots[min(m0 - 1, len(rnd.moves) - 1)].start * args.fps))
                    e_idx = min(m1, len(rnd.moves)) - 1
                    f1 = min(nframes, int(math_ceil((rnd.tl.slots[e_idx].end) * args.fps)))
                else:
                    s0 = float(a)
                    s1 = float(b) if b.strip() else tl.total
                    f0 = max(0, int(s0 * args.fps))
                    f1 = min(nframes, int(math_ceil(s1 * args.fps)))
            except Exception:
                print(f"错误: --range 格式应为 '80-120' 或 '12.5-30.2', 收到 '{args.range}'", file=sys.stderr)
                return 2
            f1 = max(f0 + 1, min(f1, nframes))
            if not args.quiet:
                print(f"片段模式: 帧 {f0}-{f1} ({(f1 - f0) / args.fps:.1f}s)")

        work = tempfile.mkdtemp(prefix="sgfr_")
        seg_ext = ".webm" if args.codec == "vp9" else ".mp4"
        try:
            # --- 音轨 ---
            wav = os.path.join(work, "audio.wav")
            has_audio = not args.no_audio or bool(args.bgm)
            if has_audio:
                saudio.build_audio(rv, tl, wav,
                                   mode="none" if args.no_audio else "click",
                                   bgm=args.bgm, bgm_gain=args.bgm_gain,
                                   kit=getattr(args, "kit", "kaya"))

            jobs = args.jobs
            if jobs <= 0:
                jobs = _auto_jobs()
            if getattr(args, "eco", False):
                jobs = min(jobs, 2)   # 低占用: 最多两个渲染进程
            if args.range:
                jobs = 1  # 片段模式不并行, 避免起点状态不一致
            # Windows 单文件版: 每个 worker 都要把 onefile 解压一份到临时目录,
            # spawn 开销远大于并行收益; 未显式指定进程数时收敛到单进程.
            if (jobs > 1 and os.name == "nt" and getattr(sys, "frozen", False)
                    and args.jobs <= 0):
                jobs = 1
            jobs = max(1, min(jobs, max(1, nframes // 45)))

            if args.codec == "hevc" and not senc.has_encoder("libx265") and not args.quiet:
                print("提示: 当前 ffmpeg 缺少 libx265, HEVC 已降级为 H.264 编码")

            span = f1 - f0
            if not args.quiet:
                print(f"\n渲染中: {span} 帧 · {w}x{h}@{args.fps} · "
                      f"时长 {human_time(span / args.fps)} · {jobs} 进程")
                print(f"       主题 {theme.label} / {rv.info.size_x}路 / "
                      f"{len(rv.moves)} 手")

            if jobs <= 1:
                seg = os.path.join(work, f"seg_000{seg_ext}")
                # 片段模式下音轨要跳到 f0 对应的时间, 否则音画错位
                aoff = (f0 / args.fps) if args.range else 0.0
                proc = senc.open_writer(seg, w, h, args.fps, args.quality,
                                        args.codec, audio=wav if has_audio else None,
                                        audio_offset=aoff,
                                        threads=2 if getattr(args, "eco", False) else 0)
                assert proc.stdin is not None
                try:
                    for i in range(f0, f1):
                        proc.stdin.write(rnd.render_frame(i).tobytes())
                        if not args.quiet and (i - f0) % 30 == 0:
                            _bar(i - f0, span, t0)
                finally:
                    senc.close_writer(proc)
                if not args.quiet:
                    _bar(span, span, t0)
                shutil.move(seg, target)
            else:
                segs = _run_parallel(nframes, jobs, work, cfg, args.quiet, t0)
                joined = os.path.join(work, f"joined{seg_ext}")
                senc.concat_segments(segs, joined, work)
                if has_audio:
                    senc.mux_audio(joined, wav, target, codec=args.codec,
                                   audio_offset=(f0 / args.fps) if args.range else 0.0)
                else:
                    shutil.move(joined, target)

            _side_exports(rnd, rv, tl, args, target)

            dt = time.time() - t0
            size_mb = os.path.getsize(target) / 1024 / 1024
            if not args.quiet:
                print(f"\n完成: {target}")
                dur = (f1 - f0) / args.fps if args.range else tl.total
                print(f"      {human_time(dur)} · {size_mb:.1f} MB · 耗时 {dt:.1f}s")
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
    # done==0 时 frac 为 0, 旧的 `el / 1e-6` 会算出几小时的荒谬剩余时间
    if done > 0 and frac > 0:
        eta_txt = human_time(el / frac - el)
    else:
        eta_txt = "--:--"
    sys.stdout.write(f"\r  [{bar}] {frac * 100:5.1f}%  {done}/{total}  "
                     f"剩余 {eta_txt}   ")
    sys.stdout.flush()


# --------------------------------------------------------------------------- #
def main(argv: Optional[Sequence[str]] = None) -> int:
    from . import __version__
    from .workflow import (apply_preset_config, eco_environment,
                           list_presets, save_config)

    args = build_parser().parse_args(argv)

    if args.version:
        print(f"sgf-review {__version__}")
        return 0
    if args.list_themes:
        print("可用主题:")
        for k, t in THEMES.items():
            print(f"  {k:<10} {t.label}")
        return 0
    if args.list_presets:
        list_presets()
        return 0

    try:
        args, notes = apply_preset_config(args, argv)
    except (ValueError, OSError, json.JSONDecodeError) as e:
        print(f"错误: {e}", file=sys.stderr)
        return 2
    if args.eco:
        eco_environment()
    if notes and not args.quiet:
        print(f"已套用: {' + '.join(notes)}")

    if args.save_config:
        save_config(args, args.save_config)

    if not args.inputs:
        if args.save_config:
            return 0
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
    jobs = int(max(1, min(8, cpus, by_mem)))
    if os.name == "nt":
        # Windows 桌面机还要留出前台交互的余量, 默认最多 2 进程
        jobs = min(jobs, 2)
    return jobs


def _run_parallel(nframes: int, jobs: int, work: str, cfg,
                  quiet: bool, t0: float):
    """分段并行渲染; 进程被杀时自动降级重试."""
    attempt = jobs
    seg_ext = ".webm" if cfg.get("codec") == "vp9" else ".mp4"
    while True:
        bounds = _split(nframes, attempt)
        tasks = [(a, b, os.path.join(work, f"seg_{k:03d}{seg_ext}"), cfg)
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
