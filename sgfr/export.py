"""附加导出: GIF / SRT 字幕 / JSON 元数据 / 海报帧 / 主题对比图 / 关键手集锦."""
from __future__ import annotations

import json
import math
import os
from typing import Dict, List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFilter

from . import __version__, lz
from .renderer import Renderer, describe_komi, describe_result
from .sgf import BLACK, WHITE, Review
from .theme import THEMES, Theme, font
from .timeline import Timeline


def _ensure_parent(path: str) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)


def _srt_ts(sec: float) -> str:
    sec = max(0.0, sec)
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    ms = int(round((sec - int(sec)) * 1000))
    if ms >= 1000:
        ms = 999
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(rv: Review, tl: Timeline, path: str, lang: str = "zh",
              ai_commentary: bool = True) -> int:
    """解说字幕: 人类评论优先, 其次 AI 点评, 再其次手数坐标."""
    cues: List[Tuple[float, float, str]] = []
    zh = lang == "zh"

    # 片头
    info = rv.info
    title = f"{info.black or ('黑方' if zh else 'Black')} vs {info.white or ('白方' if zh else 'White')}"
    sub = "  ·  ".join([p for p in (info.event, info.round, info.date) if p])
    head = title if not sub else f"{title}\n{sub}"
    cues.append((0.0, tl.intro_end, head))

    for slot in tl.slots:
        mv = rv.moves[slot.index]
        text = (mv.comment or "").strip()
        if not text and ai_commentary and rv.series is not None:
            text = lz.ai_commentary(rv.series, slot.index, lang).strip()
        if not text:
            if mv.is_pass:
                text = "虚手" if zh else "Pass"
            else:
                who = "黑" if mv.color == BLACK else "白"
                text = f"第 {mv.number} 手  {who}  {mv.coord}" if zh else \
                       f"Move {mv.number}  {'B' if mv.color == BLACK else 'W'}  {mv.coord}"
        # 单手字幕不要盖到下一手
        end = slot.end - 0.08
        if slot.index + 1 < len(tl.slots):
            end = min(end, tl.slots[slot.index + 1].start - 0.02)
        cues.append((slot.start, max(slot.start + 0.2, end), text))

    # 片尾
    main, _ = describe_result(info.result, lang)
    tail = main or ("对局结束" if zh else "Game complete")
    extra = ""
    if rv.series is not None and rv.series.count:
        rate = rv.series.matched_moves / max(1, rv.series.judged_moves) * 100
        extra = f"\nAI 吻合度 {rate:.0f}%" if zh else f"\nAI match {rate:.0f}%"
    cues.append((tl.outro_start, tl.total, tail + extra))

    _ensure_parent(path)
    with open(path, "w", encoding="utf-8") as fh:
        for i, (a, b, text) in enumerate(cues, 1):
            if not text.strip():
                continue
            fh.write(f"{i}\n{_srt_ts(a)} --> {_srt_ts(b)}\n{text.strip()}\n\n")
    return len(cues)


def write_json(rv: Review, tl: Timeline, path: str) -> None:
    """机器可读的对局 + 分析 + 时间轴元数据 (剪辑/二次创作用)."""
    info = rv.info
    moves = []
    for i, mv in enumerate(rv.moves):
        item: Dict = {
            "number": mv.number,
            "color": "B" if mv.color == BLACK else "W",
            "coord": mv.coord,
            "is_pass": mv.is_pass,
            "valid": mv.valid,
            "captures": [[x, y] for x, y in mv.captured],
            "comment": mv.comment,
        }
        an = rv.series.at(i) if rv.series else None
        if an is not None and an.winrate is not None:
            item["analysis"] = {
                "engine": an.engine,
                "black_winrate": an.black_winrate,
                "black_lead": an.black_lead,
                "playouts": an.playouts,
                "swing": an.swing,
                "best": [
                    {"coord": b.coord, "winrate": b.winrate, "visits": b.visits}
                    for b in an.best[:3] if b.valid
                ],
            }
        moves.append(item)

    series = None
    if rv.series is not None and rv.series.count:
        ser = rv.series
        series = {
            "engine": ser.engine,
            "engine2": ser.engine2 or None,
            "analysed": ser.count,
            "total_moves": len(rv.moves),
            "coverage": round(ser.coverage, 4),
            "match_rate": round(ser.matched_moves / max(1, ser.judged_moves), 4),
            "avg_loss": round(ser.avg_loss, 2),
            "has_ownership": ser.has_ownership,
            "key_moments": [
                {
                    "move": k.number,
                    "color": "B" if k.color == BLACK else "W",
                    "swing": round(k.swing, 1),
                    "label": k.label,
                }
                for k in ser.key_moments
            ],
        }

    data = {
        "tool": "sgf-review",
        "version": __version__,
        "source": os.path.basename(rv.source_path),
        "encoding": rv.source_encoding,
        "info": {
            "black": info.black, "white": info.white,
            "black_rank": info.black_rank, "white_rank": info.white_rank,
            "event": info.event, "round": info.round, "date": info.date,
            "place": info.place, "result": info.result,
            "komi": info.komi, "handicap": info.handicap,
            "size": [info.size_x, info.size_y],
            "rules": info.rules, "name": info.name,
        },
        "setup_black": [[x, y] for x, y in rv.setup_black],
        "setup_white": [[x, y] for x, y in rv.setup_white],
        "n_moves": len(rv.moves),
        "branches": rv.branch_count,
        "warnings": rv.warnings,
        "timeline": {
            "total": round(tl.total, 3),
            "intro_end": round(tl.intro_end, 3),
            "setup_end": round(tl.setup_end, 3),
            "outro_start": round(tl.outro_start, 3),
            "slots": [
                {"move": s.index + 1, "start": round(s.start, 3),
                 "dur": round(s.dur, 3), "anim": round(s.anim, 3)}
                for s in tl.slots
            ],
        },
        "series": series,
        "moves": moves,
    }
    _ensure_parent(path)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)


def export_gif(rnd: Renderer, path: str, fps: float = 10.0, scale: float = 0.4,
               f0: int = 0, f1: Optional[int] = None, max_frames: int = 220,
               loop: int = 0) -> int:
    """把渲染结果导出为循环 GIF (社交平台/IM 预览)."""
    src_fps = float(rnd.fps)
    fps = max(2.0, min(20.0, fps))
    step = max(1, int(round(src_fps / fps)))
    if f1 is None:
        f1 = rnd.n_frames
    f1 = min(f1, rnd.n_frames)
    idxs = list(range(f0, f1, step))
    if len(idxs) > max_frames:
        # 均匀抽稀
        sel = [int(round(i * (len(idxs) - 1) / (max_frames - 1))) for i in range(max_frames)]
        idxs = [idxs[i] for i in sel]
    w = max(80, int(rnd.W * scale))
    h = max(80, int(rnd.H * scale))
    frames: List[Image.Image] = []
    for i in idxs:
        im = rnd.render_frame(i).resize((w, h), Image.LANCZOS)
        frames.append(im)
    if not frames:
        return 0
    _ensure_parent(path)
    dur = int(round(1000.0 / fps))
    frames[0].save(
        path, save_all=True, append_images=frames[1:],
        duration=dur, loop=loop, optimize=True,
    )
    return len(frames)


def render_poster(rnd: Renderer, path: str, move: Optional[int] = None) -> None:
    """单帧海报: 默认取约 55% 手数、动画落定后的画面."""
    n = len(rnd.moves)
    if n and rnd.tl.slots:
        idx = n // 2 if move is None else max(0, min(n - 1, move - 1))
        slot = rnd.tl.slots[idx]
        t = slot.start + slot.anim + 0.18
        img = rnd.render_frame(int(t * rnd.fps))
    else:
        img = rnd.render_frame(int(rnd.tl.intro_end * 0.5 * rnd.fps))
    _ensure_parent(path)
    img.save(path)


def render_theme_sheet(rnd_proto: Renderer, rv: Review, tl: Timeline,
                       path: str, themes: Optional[Sequence[str]] = None) -> None:
    """同一局面的多主题对比图, 选主题时用."""
    keys = list(themes) if themes else list(THEMES.keys())
    keys = [k for k in keys if k in THEMES] or list(THEMES.keys())
    n = len(rnd_proto.moves)
    if n and rnd_proto.tl.slots:
        idx = min(n - 1, int(n * 0.58))
        slot = rnd_proto.tl.slots[idx]
        t = slot.start + slot.anim + 0.18
        frame_i = int(t * rnd_proto.fps)
    else:
        idx, frame_i = 0, int(rnd_proto.tl.intro_end * rnd_proto.fps)

    tiles: List[Image.Image] = []
    for k in keys:
        from .theme import get_theme
        from .timeline import build_timeline, Timing
        th = get_theme(k)
        tl2 = tl if th is rnd_proto.th else build_timeline(rv, Timing(
            total=tl.timing.total, speed=tl.timing.speed,
            intro=tl.timing.intro, outro=tl.timing.outro,
        ))
        r = Renderer(rv, tl2, th, 640, 360, rnd_proto.fps,
                     show_coords=rnd_proto.show_coords, labels=rnd_proto.labels,
                     label_count=rnd_proto.label_count, show_comment=False,
                     lang=rnd_proto.lang, watermark="",
                     show_analysis=False, show_heatmap=False,
                     ai_commentary=False)
        im = r.render_frame(min(frame_i, r.n_frames - 1)).resize((480, 270), Image.LANCZOS)
        tiles.append(im)

    cols = 2 if len(tiles) > 2 else len(tiles)
    rows = (len(tiles) + cols - 1) // cols
    gap = 10
    sheet = Image.new("RGB", (cols * 480 + gap * (cols + 1),
                              rows * 270 + 28 + gap * (rows + 1)), (16, 14, 12))
    d = ImageDraw.Draw(sheet)
    for i, (k, im) in enumerate(zip(keys, tiles)):
        x = gap + (i % cols) * (480 + gap)
        y = 22 + gap + (i // cols) * (270 + gap)
        sheet.paste(im, (x, y))
        d.text((x + 8, y - 16), f"{k}  ·  {THEMES[k].label}",
               font=font("sans", 13), fill=(230, 220, 200))
    _ensure_parent(path)
    sheet.save(path)


def render_highlights(rv: Review, tl: Timeline, theme: Theme,
                      w: int, h: int, fps: int, path: str,
                      lang: str = "zh", top_n: int = 5,
                      hold: float = 2.2) -> int:
    """关键手集锦短片: 每个关键转折停留 hold 秒, 顶部横幅标注."""
    ser = rv.series
    if not ser or not ser.key_moments:
        return 0
    moments = sorted(ser.key_moments, key=lambda m: abs(m.swing), reverse=True)[:top_n]
    moments = sorted(moments, key=lambda m: m.index)
    rnd = Renderer(rv, tl, theme, w, h, fps, show_comment=False,
                   lang=lang, show_analysis=True, ai_commentary=False)

    from . import encoder as senc
    import tempfile, shutil
    work = tempfile.mkdtemp(prefix="sgfr_hi_")
    seg = os.path.join(work, "hi.mp4")
    proc = senc.open_writer(seg, w, h, fps, "high", "h264", audio=None)
    zh = lang == "zh"
    try:
        # 片头卡
        title_frames = int(0.9 * fps)
        title = "关 键 手" if zh else "KEY MOMENTS"
        for i in range(title_frames):
            img = rnd.bg.copy()
            a = min(1.0, i / (0.4 * fps))
            rnd._text(img, (w / 2, h / 2), title, "serif_bold",
                      int(52 * min(w, h) / 1080), theme.accent, "mm", a, 0.12)
            proc.stdin.write(img.convert("RGB").tobytes())

        for m in moments:
            idx = m.index
            slot = tl.slots[idx]
            base_t = slot.start + slot.anim + 0.12
            n_f = int(hold * fps)
            side = "黑" if m.color == 1 else "白"
            if zh:
                banner = f"第 {m.number} 手（{side}）{m.swing:+.1f}%   {m.label}"
            else:
                banner = f"move {m.number} ({'B' if m.color == 1 else 'W'}) {m.swing:+.1f}%  {m.label}"
            for i in range(n_f):
                t = base_t + i / fps
                img = rnd._board_scene(t, idx, 1.0)
                # 顶部横幅
                bw, bh = int(w * 0.72), int(46 * min(w, h) / 1080)
                bx = (w - bw) // 2
                by = int(h * 0.035)
                layer = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
                ld = ImageDraw.Draw(layer)
                ld.rounded_rectangle([0, 0, bw, bh], radius=bh // 2,
                                     fill=(12, 10, 8, 200))
                img.paste(layer, (bx, by), layer)
                col = (0.92, 0.42, 0.36) if m.swing < 0 else (0.42, 0.85, 0.58)
                rnd._text(img, (w / 2, by + bh / 2), banner, "sans_bold",
                          max(12, int(bh * 0.52)), col, "mm", 1.0, 0.02)
                proc.stdin.write(img.convert("RGB").tobytes())

        # 片尾
        for i in range(int(0.7 * fps)):
            img = rnd.bg.copy()
            main, _ = describe_result(rv.info.result, lang)
            rnd._text(img, (w / 2, h / 2), main or "", "serif_bold",
                      int(44 * min(w, h) / 1080), theme.text, "mm",
                      min(1.0, i / (0.3 * fps)))
            proc.stdin.write(img.convert("RGB").tobytes())
        senc.close_writer(proc)
        if not os.path.exists(seg):
            raise RuntimeError("关键手集锦编码失败: 未生成文件")
        _ensure_parent(path)
        shutil.move(seg, path)
        return len(moments)
    finally:
        if proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass
        shutil.rmtree(work, ignore_errors=True)
