"""围棋棋盘引擎: 落子 / 提子 / 气 / 合法性 / 势力估算."""
from __future__ import annotations

from typing import Iterable, List, Optional, Sequence, Tuple

from .sgf import BLACK, WHITE

EMPTY, B, W = 0, 1, 2


class Board:
    __slots__ = ("w", "h", "grid", "ko_point", "captures")

    def __init__(self, w: int = 19, h: int = 19):
        self.w = w
        self.h = h
        self.grid: List[List[int]] = [[EMPTY] * w for _ in range(h)]
        self.ko_point: Optional[Tuple[int, int]] = None
        self.captures = {BLACK: 0, WHITE: 0}   # 各方"提掉对方"的子数

    # ------------------------------------------------------------------ #
    def copy(self) -> "Board":
        nb = Board(self.w, self.h)
        nb.grid = [row[:] for row in self.grid]
        nb.ko_point = self.ko_point
        nb.captures = dict(self.captures)
        return nb

    def get(self, x: int, y: int) -> int:
        if 0 <= x < self.w and 0 <= y < self.h:
            return self.grid[y][x]
        return -1

    def set(self, x: int, y: int, color: int) -> None:
        if 0 <= x < self.w and 0 <= y < self.h:
            self.grid[y][x] = color

    # ------------------------------------------------------------------ #
    def _neighbors(self, x: int, y: int):
        if x > 0:
            yield x - 1, y
        if x < self.w - 1:
            yield x + 1, y
        if y > 0:
            yield x, y - 1
        if y < self.h - 1:
            yield x, y + 1

    def _group(self, x: int, y: int) -> Tuple[List[Tuple[int, int]], int]:
        """返回 (同色连通块, 气数)."""
        color = self.grid[y][x]
        if color == EMPTY:
            return [], 0
        seen = {(x, y)}
        stack = [(x, y)]
        libs = set()
        while stack:
            cx, cy = stack.pop()
            for nx, ny in self._neighbors(cx, cy):
                v = self.grid[ny][nx]
                if v == EMPTY:
                    libs.add((nx, ny))
                elif v == color and (nx, ny) not in seen:
                    seen.add((nx, ny))
                    stack.append((nx, ny))
        return list(seen), len(libs)

    def is_legal(self, x: int, y: int, color: int) -> bool:
        if not (0 <= x < self.w and 0 <= y < self.h):
            return False
        if self.grid[y][x] != EMPTY:
            return False
        if self.ko_point == (x, y):
            return False
        opp = W if color == BLACK else B
        self.grid[y][x] = color
        enemy_dead = 0
        for nx, ny in self._neighbors(x, y):
            if self.grid[ny][nx] == opp:
                _, libs = self._group(nx, ny)
                if libs == 0:
                    enemy_dead += 1
        ok = True
        if enemy_dead == 0:
            _, my_libs = self._group(x, y)
            ok = my_libs > 0
        self.grid[y][x] = EMPTY
        return ok

    # ------------------------------------------------------------------ #
    def play(self, x: int, y: int, color: int) -> Tuple[bool, List[Tuple[int, int]]]:
        """落子, 返回 (是否成功, 被提子列表)."""
        if not (0 <= x < self.w and 0 <= y < self.h):
            return False, []
        if self.grid[y][x] != EMPTY:
            return False, []
        opp = W if color == BLACK else B
        self.grid[y][x] = color

        captured: List[Tuple[int, int]] = []
        for nx, ny in self._neighbors(x, y):
            if self.grid[ny][nx] == opp:
                group, libs = self._group(nx, ny)
                if libs == 0:
                    for gx, gy in group:
                        self.grid[gy][gx] = EMPTY
                        captured.append((gx, gy))

        if not captured:
            _, my_libs = self._group(x, y)
            if my_libs == 0:
                # 自杀, 回滚
                self.grid[y][x] = EMPTY
                return False, []

        # 简单劫争判定: 单子提单子且提后自身仅一气
        self.ko_point = None
        if len(captured) == 1:
            group, libs = self._group(x, y)
            if len(group) == 1 and libs == 1:
                self.ko_point = captured[0]

        self.captures[color] += len(captured)
        return True, captured

    # ------------------------------------------------------------------ #
    def territory_estimate(self) -> List[List[float]]:
        """粗略势力图, 值域 [-1, 1], 正为黑. 用于背景热力图(默认关闭)."""
        import math

        inf = [[0.0] * self.w for _ in range(self.h)]
        stones = [(x, y, self.grid[y][x]) for y in range(self.h) for x in range(self.w)
                  if self.grid[y][x] != EMPTY]
        if not stones:
            return inf
        for y in range(self.h):
            for x in range(self.w):
                if self.grid[y][x] != EMPTY:
                    inf[y][x] = 1.0 if self.grid[y][x] == B else -1.0
                    continue
                num = den = 0.0
                for sx, sy, c in stones:
                    d = math.hypot(sx - x, sy - y)
                    wgt = 1.0 / (d * d + 1.0) ** 1.6
                    num += (1.0 if c == B else -1.0) * wgt
                    den += wgt
                inf[y][x] = num / den if den else 0.0
        return inf


def replay(review, upto: Optional[int] = None) -> Tuple[Board, List[int], List[List[Tuple[int, int]]]]:
    """按棋谱重演, 返回 (棋盘, 每手是否成功, 每手提子)."""
    b = Board(review.info.size_x, review.info.size_y)
    for x, y in review.setup_black:
        b.set(x, y, BLACK)
    for x, y in review.setup_white:
        b.set(x, y, WHITE)

    ok_flags: List[int] = []
    caps: List[List[Tuple[int, int]]] = []
    limit = len(review.moves) if upto is None else min(upto, len(review.moves))
    for mv in review.moves[:limit]:
        if mv.is_pass:
            ok_flags.append(1)
            caps.append([])
            continue
        ok, captured = b.play(mv.x, mv.y, mv.color)
        if not ok:
            ok, captured = False, []
            ok_flags.append(0)
            caps.append([])
            continue
        caps.append(captured)
        ok_flags.append(1)
    return b, ok_flags, caps


def validate(review) -> List[str]:
    """校验棋谱, 顺便把提子结果写回 Move 对象."""
    b = Board(review.info.size_x, review.info.size_y)
    for x, y in review.setup_black:
        b.set(x, y, BLACK)
    for x, y in review.setup_white:
        b.set(x, y, WHITE)
    problems: List[str] = []
    for mv in review.moves:
        if mv.is_pass:
            mv.captured = []
            mv.valid = True
            continue
        ok, captured = b.play(mv.x, mv.y, mv.color)
        if not ok:
            problems.append(f"第 {mv.number} 手 {mv.coord} 非法(已有子或自杀), 已跳过")
            mv.captured = []
            mv.valid = False
        else:
            mv.captured = captured
            mv.valid = True
    return problems
