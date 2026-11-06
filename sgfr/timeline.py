"""时间轴: 依据棋谱长度/注释/提子自动编排节奏."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


@dataclass
class Timing:
    intro: float = 3.1
    outro: float = 3.4
    setup_hold: float = 1.0
    speed: float = 1.0
    total: Optional[float] = None      # 目标总时长(秒), 优先于 speed
    comment_hold: float = 1.0          # 注释停留倍率
    min_move: float = 0.085
    max_move: float = 1.5
    anim_ratio: float = 0.62           # 单手动画时间占比上限


@dataclass
class MoveSlot:
    index: int
    start: float
    dur: float
    anim: float

    @property
    def end(self) -> float:
        return self.start + self.dur


@dataclass
class Timeline:
    timing: Timing
    intro_end: float
    setup_end: float
    slots: List[MoveSlot]
    outro_start: float
    total: float

    # ------------------------------------------------------------------ #
    def slot_at(self, t: float) -> Tuple[int, float]:
        """返回 (手序号 0-based, 该手内进度 0..1). 用二分查找."""
        lo, hi = 0, len(self.slots) - 1
        if not self.slots:
            return 0, 0.0
        if t <= self.slots[0].start:
            return 0, 0.0
        if t >= self.slots[-1].end:
            return len(self.slots) - 1, 1.0
        while lo < hi:
            mid = (lo + hi) // 2
            s = self.slots[mid]
            if t < s.start:
                hi = mid - 1
            elif t >= s.end:
                lo = mid + 1
            else:
                lo = mid
                break
        s = self.slots[lo]
        p = (t - s.start) / max(1e-6, s.dur)
        return lo, max(0.0, min(1.0, p))


def _comment_weight(comment: str) -> float:
    n = len(comment.strip())
    if n == 0:
        return 0.0
    # 中文按字数, 英文按词数折算
    cjk = sum(1 for c in comment if "\u4e00" <= c <= "\u9fff")
    eff = cjk + (n - cjk) / 5.0
    return min(4.2, 0.55 + eff * 0.085)


def build_timeline(review, timing: Timing) -> Timeline:
    moves = review.moves
    n = max(1, len(moves))

    base = 0.62 * (60.0 / n) ** 0.30
    base = max(0.16, min(0.78, base))

    weights: List[float] = []
    for mv in moves:
        w = base
        if mv.comment:
            w += _comment_weight(mv.comment) * timing.comment_hold
        if mv.captured:
            w += 0.12
        if mv.is_pass:
            w = max(w, 0.9)
        weights.append(w)

    intro = timing.intro
    outro = timing.outro
    setup = timing.setup_hold if (review.setup_black or review.setup_white) else 0.45

    total_move = sum(weights)
    if timing.total:
        avail = timing.total - intro - outro - setup
        if avail < n * 0.03:
            avail = n * 0.03
        scale = avail / total_move
    else:
        scale = 1.0 / max(1e-6, timing.speed)

    slots: List[MoveSlot] = []
    t = intro + setup
    for i, (mv, w) in enumerate(zip(moves, weights)):
        dur = max(timing.min_move, min(timing.max_move, w * scale))
        anim = min(0.34, max(0.10, dur * timing.anim_ratio))
        slots.append(MoveSlot(i, t, dur, anim))
        t += dur

    outro_start = t
    return Timeline(timing, intro, intro + setup, slots, outro_start,
                    outro_start + outro)
