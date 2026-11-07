"""LizzieYzy / Lizzie 分析数据 (SGF 扩展属性 LZ / LZ2 / LZOP) 解析.

LizzieYzy 保存棋谱时会在每个已分析的节点写入:

    LZ[KataGoTRT 44.6 385 0.2 17.6
    move F2 visits 184 winrate 5540 prior 3979 scoreMean 0.20 pv F2 N5 O5 ...
    info move B3 visits 43 winrate 4888 prior 4103 scoreMean -0.50 pv B3 B4 ...
    ownership -0.03 0.11 ...]

第一行字段依次为:
    engine  winrate  playouts  scoreMean  scoreStdev  [pda]

语义(与 LizzieYzy 源码 SGFParser.formatNodeData 一致):
  * winrate  —— **刚落子一方**的胜率(0-100)
  * scoreMean —— **轮到的一方**的目差(正数=当前方领先)
  * bestmoves 的 winrate —— 当前方视角, 以 1/100 百分点存储(5540 = 54.40%)
  * ownership —— 每个交叉点的归属(-1 白 ~ +1 黑), 行优先, 从左上角开始

本模块负责把这些原始字段归一成「黑方视角」的时间序列, 供渲染层直接使用.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

BLACK, WHITE = 1, 2


# --------------------------------------------------------------------------- #
# 数据结构
# --------------------------------------------------------------------------- #
@dataclass
class BestMove:
    coord: str = ""
    x: int = -1
    y: int = -1
    visits: int = 0
    winrate: float = 0.0          # 当前方视角, 0-100
    prior: float = 0.0            # 策略概率, 0-100
    score_mean: Optional[float] = None
    pv: List[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return self.x >= 0 and self.y >= 0


@dataclass
class Analysis:
    engine: str = ""
    winrate: Optional[float] = None       # 落子方视角 0-100
    playouts: int = 0
    score_mean: Optional[float] = None    # 当前方视角
    score_stdev: Optional[float] = None
    pda: float = 0.0
    best: List[BestMove] = field(default_factory=list)
    ownership: Optional[List[float]] = None

    # ---- 归一化后的字段(黑方视角) ----
    black_winrate: Optional[float] = None
    black_lead: Optional[float] = None
    swing: Optional[float] = None         # 本手落子方胜率变化(负=失误)

    @property
    def has_data(self) -> bool:
        return self.winrate is not None


# --------------------------------------------------------------------------- #
# 坐标
# --------------------------------------------------------------------------- #
def sgf_coord_to_xy(s: str, nx: int, ny: int) -> Tuple[int, int]:
    if not s or len(s) < 2:
        return -1, -1
    x = ord(s[0].lower()) - 97
    y = ord(s[1].lower()) - 97
    if 0 <= x < nx and 0 <= y < ny:
        return x, y
    return -1, -1


def display_coord_to_xy(s: str, nx: int, ny: int) -> Tuple[int, int]:
    """Lizzie 的显示坐标: 列 A-T(跳过 I), 行 1 在底部."""
    s = (s or "").strip().upper()
    if not s or s in ("PASS", "RESIGN", "TT"):
        return -1, -1
    m = re.match(r"([A-HJ-T])(\d{1,2})$", s)
    if not m:
        return -1, -1
    col = m.group(1)
    x = ord(col) - 65
    if col > "I":
        x -= 1
    try:
        row = int(m.group(2))
    except ValueError:
        return -1, -1
    y = ny - row
    if 0 <= x < nx and 0 <= y < ny:
        return x, y
    return -1, -1


# --------------------------------------------------------------------------- #
# 数值
# --------------------------------------------------------------------------- #
def _num(s: str, default: float = 0.0) -> float:
    try:
        return float(s)
    except (TypeError, ValueError):
        return default


def parse_playouts(s: str) -> int:
    s = (s or "").strip().replace(",", "").replace("_", "")
    if not s:
        return 0
    m = re.match(r"([0-9]*\.?[0-9]+)\s*([kKmM]?)", s)
    if not m:
        digits = re.sub(r"[^0-9]", "", s)
        return int(digits) if digits else 0
    v = float(m.group(1))
    unit = m.group(2).lower()
    if unit == "k":
        v *= 1_000
    elif unit == "m":
        v *= 1_000_000
    return int(round(v))


# --------------------------------------------------------------------------- #
# 解析
# --------------------------------------------------------------------------- #
_MOVE_RE = re.compile(r"(?:^|\s)move\s+")


def _split_ownership(body: str) -> Tuple[str, Optional[str]]:
    """把行尾的 ` ownership v1 v2 ...` 摘出来(用字符串切分, 避免正则回溯)."""
    k = body.lower().rfind(" ownership ")
    if k < 0:
        return body, None
    return body[:k], body[k + len(" ownership "):]


def _parse_best_moves(line: str, nx: int, ny: int) -> List[BestMove]:
    out: List[BestMove] = []
    if not line:
        return out
    line, _ = _split_ownership(line)
    starts = [mm.start() for mm in _MOVE_RE.finditer(line)]
    for i, st in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(line)
        chunk = line[st:end].strip()
        chunk = re.sub(r"^move\s+", "", chunk)
        chunk = re.sub(r"\s+info\s*$", "", chunk)
        if not chunk:
            continue
        parts = chunk.split()
        bm = BestMove(coord=parts[0])
        bm.x, bm.y = display_coord_to_xy(bm.coord, nx, ny)

        def grab(key: str) -> Optional[str]:
            try:
                k = parts.index(key)
            except ValueError:
                return None
            return parts[k + 1] if k + 1 < len(parts) else None

        v = grab("visits")
        if v:
            bm.visits = int(_num(v))
        v = grab("winrate")
        if v:
            bm.winrate = _num(v) / 100.0
        v = grab("prior")
        if v:
            bm.prior = _num(v) / 100.0
        v = grab("scoreMean")
        if v:
            bm.score_mean = _num(v)
        v = grab("pv")
        if v:
            i0 = parts.index("pv") + 1
            i1 = len(parts)
            for stop in ("pvVisits", "order", "info"):
                if stop in parts[i0:]:
                    i1 = min(i1, parts.index(stop, i0))
            bm.pv = parts[i0:i1]
        out.append(bm)
    return out


def parse_lz(text: str, nx: int = 19, ny: int = 19) -> Optional[Analysis]:
    """解析一个 LZ[...] 属性的值; 空值 / 无法识别返回 None."""
    if not text or not text.strip():
        return None
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    head = lines[0].strip().split()
    if not head:
        return None
    an = Analysis()
    an.engine = head[0]
    try:
        if len(head) >= 2:
            an.winrate = float(head[1])
        if len(head) >= 3:
            an.playouts = parse_playouts(head[2])
        if len(head) >= 4:
            an.score_mean = float(head[3])
        if len(head) >= 5:
            an.score_stdev = float(head[4])
        if len(head) >= 6:
            an.pda = float(head[5])
    except ValueError:
        # 头部残缺但仍有 bestmoves 时, 尽量保住能读的字段
        pass
    body = "\n".join(lines[1:]).strip()
    if body:
        body, own_text = _split_ownership(body)
        an.best = _parse_best_moves(body, nx, ny)
        if own_text:
            vals: List[float] = []
            for tok in own_text.split():
                try:
                    vals.append(float(tok))
                except ValueError:
                    pass
            if len(vals) >= nx * ny:
                an.ownership = vals[: nx * ny]
    if an.winrate is None and not an.best:
        return None
    return an


# --------------------------------------------------------------------------- #
# C[] 注释: 剥离 LizzieYzy 自动写入的引擎块
# --------------------------------------------------------------------------- #
_ENGINE_HEAD = re.compile(
    r"(?m)^\s*(黑棋|白棋|黑|白|Black|White)\s*(胜率|winrate)\s*[:：]",
    re.I,
)
_ENGINE_TAIL = re.compile(r"(计算量|playouts?)\s*\)", re.I)
_RULE_LINE = re.compile(r"(?m)^\s*引擎规则\s*[:：].*$")
# 绝艺/第三方引擎写在注释首行的胜率标签: /"jueyi黑65.3% 或 /"jueyi白52.2% - 6.5目
_JUEYI_LINE = re.compile(r"(?m)^\s*/\s*[\"“”']?\s*jueyi[^\n]*$", re.I)


def split_comment(comment: str) -> Tuple[str, str]:
    """返回 (人类评论, 引擎评论)."""
    if not comment or not comment.strip():
        return "", ""
    text = comment.replace("\r\n", "\n").replace("\r", "\n").strip()
    text = _RULE_LINE.sub("", text)
    text = _JUEYI_LINE.sub("", text).strip()
    if not text:
        return "", ""

    m = _ENGINE_HEAD.search(text)
    if m:
        tail = text[m.start():]
        if _ENGINE_TAIL.search(tail) or len(tail) > 120:
            human = text[: m.start()].rstrip(" \n-—")
            engine = tail.strip()
            # 纯引擎注释(没有人类内容)时也算剥离成功
            return human.strip(), engine
    return text, ""


def comment_preview_human(comment: str) -> str:
    h, _ = split_comment(comment)
    return h.strip()


# --------------------------------------------------------------------------- #
# 序列 / 摘要
# --------------------------------------------------------------------------- #
@dataclass
class KeyMoment:
    index: int          # 0-based move index
    number: int
    color: int
    swing: float        # 落子方胜率变化
    label: str          # 失误 / 好手 / 转折


@dataclass
class AnalysisSeries:
    """整局的分析时间线(与 review.moves 等长)."""
    points: List[Optional[Analysis]] = field(default_factory=list)
    black_wr: List[Optional[float]] = field(default_factory=list)
    black_lead: List[Optional[float]] = field(default_factory=list)
    points2: List[Optional[Analysis]] = field(default_factory=list)
    black_wr2: List[Optional[float]] = field(default_factory=list)
    black_lead2: List[Optional[float]] = field(default_factory=list)
    key_moments: List[KeyMoment] = field(default_factory=list)
    engine: str = ""
    engine2: str = ""
    has_ownership: bool = False
    matched_moves: int = 0       # 实际落子 == AI 第一推荐
    judged_moves: int = 0
    avg_loss: float = 0.0

    @property
    def count(self) -> int:
        return sum(1 for p in self.points if p is not None)

    @property
    def coverage(self) -> float:
        n = max(1, len(self.points))
        return self.count / n

    def at(self, i: int) -> Optional[Analysis]:
        if 0 <= i < len(self.points):
            return self.points[i]
        return None

    def wr_at(self, i: int) -> Optional[float]:
        if 0 <= i < len(self.black_wr):
            return self.black_wr[i]
        return None

    def lead_at(self, i: int) -> Optional[float]:
        if 0 <= i < len(self.black_lead):
            return self.black_lead[i]
        return None


def build_series(moves: Sequence, nx: int, ny: int,
                 komi: Optional[float] = None) -> AnalysisSeries:
    """把每手的 Analysis 归一成黑方视角, 并统计关键点/吻合度."""
    ser = AnalysisSeries()
    n = len(moves)

    for i, mv in enumerate(moves):
        an: Optional[Analysis] = getattr(mv, "analysis", None)
        ser.points.append(an)
        if an is None or an.winrate is None:
            ser.black_wr.append(None)
            ser.black_lead.append(None)
        else:
            ser.engine = ser.engine or an.engine
            # winrate: 落子方视角 → 黑方视角
            bwr = an.winrate if mv.color == BLACK else 100.0 - an.winrate
            # scoreMean: 轮到的一方视角 → 黑方视角
            blead = None
            if an.score_mean is not None:
                to_play = WHITE if mv.color == BLACK else BLACK
                blead = an.score_mean if to_play == BLACK else -an.score_mean
            an.black_winrate = bwr
            an.black_lead = blead
            ser.black_wr.append(bwr)
            ser.black_lead.append(blead)
            if an.ownership:
                ser.has_ownership = True

        an2: Optional[Analysis] = getattr(mv, "analysis2", None)
        ser.points2.append(an2)
        if an2 is None or an2.winrate is None:
            ser.black_wr2.append(None)
            ser.black_lead2.append(None)
        else:
            ser.engine2 = ser.engine2 or an2.engine
            bwr2 = an2.winrate if mv.color == BLACK else 100.0 - an2.winrate
            blead2 = None
            if an2.score_mean is not None:
                to_play = WHITE if mv.color == BLACK else BLACK
                blead2 = an2.score_mean if to_play == BLACK else -an2.score_mean
            an2.black_winrate = bwr2
            an2.black_lead = blead2
            ser.black_wr2.append(bwr2)
            ser.black_lead2.append(blead2)

    # ---- 胜率变化 & 关键点 ------------------------------------------------
    # LZ 记录的是「落子之后」局面的分析, 因此第 i 手的胜率变化要跟 i-1 比较;
    # 中间缺分析时无法判断单手的得失, 直接跳过, 避免把多手变化算到一手头上.
    prev_bwr: Optional[float] = None
    prev_idx = -1
    for i, mv in enumerate(moves):
        bwr = ser.black_wr[i]
        if bwr is None:
            continue
        if prev_bwr is not None and i - prev_idx == 1:
            d_black = bwr - prev_bwr
            sw = d_black if mv.color == BLACK else -d_black
            an = ser.points[i]
            if an is not None:
                an.swing = sw
            if abs(sw) >= 6.0:
                if sw <= -15:
                    lab = "严重失误"
                elif sw <= -7:
                    lab = "失误"
                elif sw >= 12:
                    lab = "好手"
                else:
                    lab = ""
                if lab:
                    ser.key_moments.append(
                        KeyMoment(i, mv.number, mv.color, sw, lab)
                    )
                if sw < 0:
                    ser.avg_loss += -sw
        prev_bwr = bwr
        prev_idx = i

    # ---- 吻合度 ------------------------------------------------------------
    # analysis[i].best 是「第 i 手落子之后」局面的推荐, 即第 i+1 手的选点,
    # 所以拿 moves[i+1] 与它比较才是真正的「AI 首选命中」.
    for i in range(len(moves) - 1):
        an = ser.points[i]
        if an is None or not an.best:
            continue
        top = an.best[0]
        if not top.valid:
            continue
        nxt = moves[i + 1]
        if nxt.is_pass:
            continue
        ser.judged_moves += 1
        if (nxt.x, nxt.y) == (top.x, top.y):
            ser.matched_moves += 1

    if ser.judged_moves:
        ser.avg_loss = ser.avg_loss / max(1, sum(1 for p in ser.points if p is not None))
    return ser


# --------------------------------------------------------------------------- #
# 点评文本
# --------------------------------------------------------------------------- #
def ai_commentary(ser: AnalysisSeries, i: int, lang: str = "zh") -> str:
    """根据本手胜率变化生成一句 AI 点评(无人类评论时使用)."""
    an = ser.at(i)
    if an is None or an.winrate is None:
        return ""
    sw = an.swing or 0.0
    top = an.best[0] if an.best else None
    rec = ""
    if top is not None and top.valid:
        rec = _coord_label(top)
    zh = lang == "zh"

    if sw <= -15:
        if zh:
            return f"败着。胜率骤降 {abs(sw):.1f}%，AI 推荐 {rec}。"
        return f"Blunder. Winrate drops {abs(sw):.1f}%; AI prefers {rec}."
    if sw <= -7:
        if zh:
            return f"疑问手，胜率下滑 {abs(sw):.1f}%" + (f"，AI 推荐 {rec}。" if rec else "。")
        return f"Inaccuracy, winrate falls {abs(sw):.1f}%" + (f"; AI prefers {rec}." if rec else ".")
    if sw >= 12:
        if zh:
            return f"好手，胜率提升 {sw:.1f}%。"
        return f"Excellent move, winrate gains {sw:.1f}%."
    if sw >= 7:
        if zh:
            return f"不错的判断，胜率回升 {sw:.1f}%。"
        return f"Good move, winrate recovers {sw:.1f}%."
    return ""


def _is_black_turn(ser: AnalysisSeries, i: int) -> bool:
    return True


def _coord_label(bm: BestMove) -> str:
    return bm.coord or ""


def summary_lines(ser: AnalysisSeries, lang: str = "zh") -> List[Tuple[str, str]]:
    """片尾 AI 复盘摘要: [(标签, 值)]."""
    if ser.count == 0:
        return []
    zh = lang == "zh"
    rate = ser.matched_moves / max(1, ser.judged_moves) * 100
    worst = min(ser.key_moments, key=lambda k: k.swing) if ser.key_moments else None
    out = [
        ("AI 吻合度" if zh else "AI match", f"{rate:.0f}%"),
        ("分析手数" if zh else "Analysed", f"{ser.count}"),
        ("平均损失" if zh else "Avg. loss", f"{ser.avg_loss:.1f}%"),
    ]
    if worst:
        side = ("黑" if worst.color == 1 else "白") if zh else ("B" if worst.color == 1 else "W")
        out.append(("最大失误" if zh else "Biggest drop",
                    f"第 {worst.number} 手（{side}）{worst.swing:+.1f}%" if zh
                    else f"move {worst.number} ({side}) {worst.swing:+.1f}%"))
    return out
