"""SGF (Smart Game Format) 解析器.

设计目标: 尽最大可能吃下真实世界里"奇形怪状"的 SGF:
  * UTF-8 / UTF-16 BOM / GBK / Shift_JIS / EUC-KR / Big5 自动检测
  * 标准 19 路, 也支持 9/13 路, 甚至矩形棋盘 SZ[19:13] 与 52 路以内任意尺寸
  * 变体分支 ( ... (;B[..]) (;B[..]) ), 可按策略挑选主线
  * 一个文件内的多局 (collection)
  * 让子 AB/AW, 空节点, 无手数局面, 注释 C, 节点名 N, 各类标记 (TR/SQ/CR/MA/LB)
  * 转义字符, 软换行, 属性名小写等不严谨写法
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import lz

BLACK, WHITE = 1, 2
COLOR_NAME = {BLACK: "B", WHITE: "W"}


# --------------------------------------------------------------------------- #
# 树的节点
# --------------------------------------------------------------------------- #
class Node:
    __slots__ = ("props", "children")

    def __init__(self, props: Optional[Dict[str, List[str]]] = None):
        self.props: Dict[str, List[str]] = props or {}
        self.children: List["Node"] = []

    # -- 便捷访问 -----------------------------------------------------------
    def get(self, key: str, default: str = "") -> str:
        vals = self.props.get(key)
        return vals[0] if vals else default

    def get_all(self, key: str) -> List[str]:
        return self.props.get(key, [])

    def has(self, key: str) -> bool:
        return key in self.props

    def count_subtree(self) -> int:
        """子树节点数(含自身), 用于挑主线."""
        total = 1
        stack = list(self.children)
        while stack:
            n = stack.pop()
            total += 1
            stack.extend(n.children)
        return total


# --------------------------------------------------------------------------- #
# 词法 / 语法
# --------------------------------------------------------------------------- #
def _parse_value(s: str, i: int) -> Tuple[str, int]:
    """解析 [ ... ], 处理 \\] \\\\ \\换行 等转义."""
    i += 1  # 跳过 '['
    out: List[str] = []
    n = len(s)
    while i < n:
        c = s[i]
        if c == "\\":
            nxt = s[i + 1] if i + 1 < n else ""
            if nxt == "\r":
                i += 3 if s[i + 2:i + 3] == "\n" else 2
                continue
            if nxt == "\n":
                i += 2
                continue
            out.append(nxt)
            i += 2
            continue
        if c == "]":
            return "".join(out), i + 1
        out.append(c)
        i += 1
    return "".join(out), i


def _parse_node(s: str, i: int) -> Tuple[Node, int]:
    i += 1  # 跳过 ';'
    props: Dict[str, List[str]] = {}
    n = len(s)
    while i < n:
        while i < n and s[i] in " \t\r\n":
            i += 1
        if i >= n or s[i] in ";()":
            break
        start = i
        while i < n and s[i] not in "[;() \t\r\n":
            i += 1
        ident = s[start:i].upper()
        values: List[str] = []
        while i < n and s[i] in " \t\r\n[":
            if s[i] == "[":
                v, i = _parse_value(s, i)
                values.append(v)
            else:
                i += 1
        if ident:
            props.setdefault(ident, []).extend(values)
    return Node(props), i


def _parse_tree(s: str, i: int) -> Tuple[Optional[Node], int]:
    i += 1  # 跳过 '('
    head: Optional[Node] = None
    tail: Optional[Node] = None
    subtrees: List[Node] = []
    n = len(s)
    while i < n:
        c = s[i]
        if c in " \t\r\n":
            i += 1
        elif c == ";":
            node, i = _parse_node(s, i)
            if head is None:
                head = tail = node
            else:
                assert tail is not None
                tail.children = [node]
                tail.children[0].children = []
                tail = node
        elif c == "(":
            sub, i = _parse_tree(s, i)
            if sub is not None:
                subtrees.append(sub)
        elif c == ")":
            i += 1
            break
        else:  # 垃圾字符, 跳过
            i += 1
    if head is not None and subtrees:
        assert tail is not None
        tail.children = subtrees
    return head, i


def parse_collection(text: str) -> List[Node]:
    trees: List[Node] = []
    i, n = 0, len(text)
    while i < n:
        if text[i] == "(":
            t, i = _parse_tree(text, i)
            if t is not None:
                trees.append(t)
        else:
            i += 1
    return trees


# --------------------------------------------------------------------------- #
# 编码检测
# --------------------------------------------------------------------------- #
_CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]")


# 常见汉字/假名: 用于判断解码是否"读得通" (错编码往往产出生僻字)
_COMMON_CJK = set(
    "的一是在不了有和人这中大为上个国我以要他时来用们生到作地于出就分对成会可主发年动同工也能下过子说产种面而方后多定行学法所民得经十三之进着等部度家电力里如水化高自二理起小物现实加量都两体制机当使点从业本去把性好应开它合还因由其些然前外天政四日那社义事平形相全表间样与关各重新线内数正心反你明看原又么利比或但质气第向道命此变条只没结解问意建月公无系军很情者最立代想已通并提直题党程展五果料象员革位入常文总次品式活设及管特件长求老头基资边流路级少图山统接知较将组见计别她手角期根论运农指几九区强放决西被干做必战先回则任取据处队南给色光门即保治北造百规热领七海口东导器压志世金增争济阶油思术极交受联什认六共权收证改清己美再采转更单风切打白教速花带安场身车例真务具万每目至达走积示议声报斗完类八离华名确才科张信马节话米整空元况今集温传土许步群广石记需段研界拉林律叫且究观越织装影算低持音众书布复容儿须际商非验连断深难近矿千周委素技备半办青省列习响约支般史感劳便团往酸历市克何除消构府称太准精值号率族维划选标写存候毛亲快效斯院查江型眼王按格养易置派层片始却专状育厂京识适属圆包火住调满县局照参红细引听该铁价严龙飞"
    "棋院段位黑白云子对局比赛胜负冠军联赛杯届年月份日认输超时中盘贴目让先相谈"
    "あいうえおかきくけこさしすせそたちつてとなにぬねのはひふへほまみむめもやゆよらりるれろわをん"
    "アイウエオカキクケコサシスセソタチツテトナニヌネノハヒフヘホマミムメモヤユヨラリルレロワヲン"
)


def _decode_score(txt: str) -> float:
    if not txt:
        return -1e9
    # 结构完整性: 解码错位会吞掉 ']' 造成括号失衡
    score = 0.0
    if txt.count("[") != txt.count("]"):
        score -= 60.0
    ctrl = sum(1 for ch in txt if ord(ch) < 32 and ch not in "\n\r\t")
    score -= ctrl * 4.0
    score -= txt.count("\ufffd") * 40.0

    common = 0
    rare = 0
    half_kana = 0
    for ch in txt:
        o = ord(ch)
        if o < 128:
            continue
        if ch in _COMMON_CJK:
            common += 1
        elif 0xFF61 <= o <= 0xFF9F:      # 半角片假名: 典型的乱码产物
            half_kana += 1
        elif (0x3400 <= o <= 0x4DBF) or (0x20000 <= o <= 0x3FFFF) or (0xE000 <= o <= 0xF8FF):
            rare += 1
    score += common * 1.0
    score -= rare * 1.2
    score -= half_kana * 1.5
    # 轻微偏好较少的非 ASCII "噪音"
    return score


_CA_RE = re.compile(rb"CA\s*\[\s*([A-Za-z0-9_\-]{2,20})\s*\]")


def _ca_hint(raw: bytes) -> Optional[str]:
    m = _CA_RE.search(raw)
    if not m:
        return None
    key = m.group(1).decode("ascii", "ignore").lower().replace("_", "-")
    table = {
        "utf-8": "utf-8", "utf8": "utf-8",
        "gb2312": "gb18030", "gbk": "gb18030", "gb18030": "gb18030", "cp936": "gb18030",
        "big5": "big5", "big-5": "big5", "cp950": "big5",
        "shift-jis": "shift_jis", "shiftjis": "shift_jis", "sjis": "shift_jis", "cp932": "shift_jis",
        "euc-jp": "euc-jp", "euc-kr": "euc-kr", "cp949": "cp949", "uhc": "cp949",
        "iso-8859-1": "latin-1", "latin1": "latin-1",
    }
    return table.get(key)


def decode_bytes(raw: bytes) -> Tuple[str, str]:
    """返回 (文本, 编码名). 依次尝试 BOM / 严格 UTF-8 / CA 声明 / 常见中文编码."""
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", "replace"), "utf-8-sig"
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        enc = "utf-16-le" if raw.startswith(b"\xff\xfe") else "utf-16-be"
        return raw[2:].decode(enc, "replace"), enc

    # 1) 严格 UTF-8: 真正的 GBK/Big5 文件几乎不可能整体通过 UTF-8 校验
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass

    # 2) SGF 自带的 CA[] 声明
    hint = _ca_hint(raw)
    if hint and hint != "utf-8":
        try:
            return raw.decode(hint), hint
        except (UnicodeDecodeError, LookupError):
            pass

    # 3) 逐个候选打分
    candidates = ["gb18030", "big5", "shift_jis", "euc-kr", "cp949", "euc-jp", "latin-1"]
    best, best_enc, best_score = "", "utf-8", -1e18
    for enc in candidates:
        try:
            txt = raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
        sc = _decode_score(txt)
        if sc > best_score:
            best, best_enc, best_score = txt, enc, sc
    if not best:
        return raw.decode("utf-8", "replace"), "utf-8"
    return best, best_enc


def read_sgf_text(path: str) -> Tuple[str, str]:
    with open(path, "rb") as fh:
        raw = fh.read()
    return decode_bytes(raw)


# --------------------------------------------------------------------------- #
# 数据模型
# --------------------------------------------------------------------------- #
@dataclass
class GameInfo:
    size_x: int = 19
    size_y: int = 19
    komi: Optional[float] = None
    handicap: int = 0
    black: str = ""
    white: str = ""
    black_rank: str = ""
    white_rank: str = ""
    result: str = ""
    date: str = ""
    event: str = ""
    round: str = ""
    place: str = ""
    rules: str = ""
    name: str = ""
    source: str = ""
    time_limit: str = ""

    # ---- 派生展示字段 -----------------------------------------------------
    @property
    def title(self) -> str:
        if self.name:
            return self.name
        if self.event:
            return f"{self.event} {self.round}".strip()
        return ""

    @property
    def is_handicap(self) -> bool:
        return self.handicap > 0


@dataclass
class Mark:
    kind: str            # TR / SQ / CR / MA / SL / LB / TB / TW
    x: int
    y: int
    label: str = ""


@dataclass
class Move:
    color: int
    x: int               # -1 表示 pass
    y: int
    number: int          # 从 1 开始(不含让子)
    comment: str = ""    # 人类评论(已剥离 LizzieYzy 自动写入的引擎块)
    engine_comment: str = ""
    node_name: str = ""
    marks: List[Mark] = field(default_factory=list)
    captured: List[Tuple[int, int]] = field(default_factory=list)
    is_pass: bool = False
    valid: bool = True
    analysis: Optional[lz.Analysis] = None
    analysis2: Optional[lz.Analysis] = None

    @property
    def coord(self) -> str:
        if self.is_pass:
            return "pass"
        return f"{chr(97 + self.x)}{chr(97 + self.y)}"


@dataclass
class Review:
    info: GameInfo
    setup_black: List[Tuple[int, int]] = field(default_factory=list)
    setup_white: List[Tuple[int, int]] = field(default_factory=list)
    moves: List[Move] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    branch_count: int = 0
    total_nodes: int = 0
    source_encoding: str = "utf-8"
    source_path: str = ""
    initial_analysis: Optional[lz.Analysis] = None
    series: Optional[lz.AnalysisSeries] = None


# --------------------------------------------------------------------------- #
# 坐标
# --------------------------------------------------------------------------- #
def parse_point(txt: str, size_x: int, size_y: int) -> Optional[Tuple[int, int]]:
    if len(txt) < 2:
        return None
    x, y = ord(txt[0]) - 97, ord(txt[1]) - 97
    if 0 <= x < size_x and 0 <= y < size_y:
        return x, y
    # 有些程序用大写字母表示 >26 路
    x, y = ord(txt[0]) - 65, ord(txt[1]) - 65
    if 0 <= x < size_x and 0 <= y < size_y:
        return x, y
    return None


def parse_point_list(vals, size_x: int, size_y: int) -> List[Tuple[int, int]]:
    """解析点列表, 支持 SGF 压缩矩形写法 AB[aa:cc]."""
    pts: List[Tuple[int, int]] = []
    for v in vals:
        v = (v or "").strip()
        if not v:
            continue
        if ":" in v:
            a, b = v.split(":", 1)
            p0 = parse_point(a, size_x, size_y)
            p1 = parse_point(b, size_x, size_y)
            if p0 and p1:
                x0, y0 = p0
                x1, y1 = p1
                for y in range(min(y0, y1), max(y0, y1) + 1):
                    for x in range(min(x0, x1), max(x0, x1) + 1):
                        if (x, y) not in pts:
                            pts.append((x, y))
            continue
        p = parse_point(v, size_x, size_y)
        if p and p not in pts:
            pts.append(p)
    return pts


def standard_handicap_stones(nx: int, ny: int, n: int) -> List[Tuple[int, int]]:
    """标准让子星位 (日本/中国通用摆法).

    很多真实棋谱只写 HA[4] 而不写 AB[], 渲染时棋盘会是空的.
    这里按惯例补全: 2 子对角 / 3 子对角+天元 / 4 子四角 /
    5 子四角+天元 / 6 子四角+左右边星 / 7 子再加天元 /
    8 子四角+四边星 / 9 子再加天元.
    """
    if n < 2 or nx < 7 or ny < 7:
        return []
    e = 3 if (nx >= 13 and ny >= 13) else 2
    mx, my = nx // 2, ny // 2
    corners = [(e, e), (nx - 1 - e, ny - 1 - e),
               (nx - 1 - e, e), (e, ny - 1 - e)]
    # 去重 (小棋盘 e 可能与 mid 重合)
    uniq: List[Tuple[int, int]] = []
    for p in corners:
        if p not in uniq:
            uniq.append(p)
    corners = uniq
    if len(corners) < 4 and n >= 4:
        return []
    sides_lrtb = [(e, my), (nx - 1 - e, my), (mx, e), (mx, ny - 1 - e)]
    sides = [p for p in sides_lrtb if p not in corners]
    center = [(mx, my)] if (mx, my) not in corners else []

    if n <= 3:
        pts = [corners[0], corners[1]]  # 对角: 左上 + 右下
        if n == 3:
            pts.extend(center)
        return pts[:n]
    if n == 4:
        return corners
    if n == 5:
        return corners + center
    if n == 6:
        return corners + sides[:2]
    if n == 7:
        return corners + sides[:2] + center
    if n == 8:
        return corners + sides[:4]
    return corners + sides[:4] + center  # 9+


def _parse_size(root: Node) -> Tuple[int, int]:
    sz = root.get("SZ", "19").strip()
    if ":" in sz:
        a, b = sz.split(":", 1)
        try:
            return max(2, min(52, int(a))), max(2, min(52, int(b)))
        except ValueError:
            return 19, 19
    try:
        n = int(sz)
        n = max(2, min(52, n))
        return n, n
    except ValueError:
        return 19, 19


def _to_float(txt: str) -> Optional[float]:
    try:
        return float(str(txt).strip())
    except (TypeError, ValueError):
        m = re.search(r"-?\d+(\.\d+)?", str(txt))
        return float(m.group()) if m else None


def _to_int(txt: str, default: int = 0) -> int:
    try:
        return int(str(txt).strip())
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- #
# 主线选择
# --------------------------------------------------------------------------- #
def _choose_line(root: Node, strategy: str = "longest") -> Tuple[List[Node], int]:
    """返回 (主线节点列表, 分支总数)."""
    line: List[Node] = []
    branches = 0
    node = root
    while True:
        line.append(node)
        kids = node.children
        if not kids:
            break
        if len(kids) > 1:
            branches += len(kids) - 1
            if strategy == "first":
                node = kids[0]
            elif strategy == "last":
                node = kids[-1]
            else:
                node = max(kids, key=lambda k: k.count_subtree())
        else:
            node = kids[0]
    # 统计整棵树的分支
    total_branches = 0
    stack = [root]
    seen = set()
    while stack:
        n = stack.pop()
        if id(n) in seen:
            continue
        seen.add(id(n))
        if len(n.children) > 1:
            total_branches += len(n.children) - 1
        stack.extend(n.children)
    return line, max(branches, total_branches)


# --------------------------------------------------------------------------- #
# 主解析入口
# --------------------------------------------------------------------------- #

def infer_result(text: str) -> str:
    """从注释里推断结果(很多棋谱 RE[] 为空, 但根注释写了"黑中盘胜")."""
    if not text:
        return ""
    t = text.replace("\n", "\n")
    m = re.search(r"(黑|白)\s*(中盘胜|超时胜|弃权胜|获胜|胜)", t)
    if m:
        side = "B" if m.group(1) == "黑" else "W"
        reason = m.group(2)
        if "中盘" in reason:
            return f"{side}+R"
        if "超时" in reason:
            return f"{side}+T"
        if "弃权" in reason:
            return f"{side}+F"
        return f"{side}+"
    m = re.search(r"(黑|白)\s*(\d+(?:\.\d+)?)\s*目\s*(?:半)?\s*胜", t)
    if m:
        side = "B" if m.group(1) == "黑" else "W"
        return f"{side}+{m.group(2)}"
    m = re.search(r"(黑|白)\s*胜\s*(\d+(?:\.\d+)?)\s*目", t)
    if m:
        side = "B" if m.group(1) == "黑" else "W"
        return f"{side}+{m.group(2)}"
    if re.search(r"(黑|白)\s*胜\s*半\s*目", t):
        side = "B" if re.search(r"黑\s*胜\s*半\s*目", t) else "W"
        return f"{side}+0.5"
    return ""

def parse_sgf(path: str, line_strategy: str = "longest") -> Review:
    text, enc = read_sgf_text(path)
    trees = parse_collection(text)
    if not trees:
        raise ValueError(f"无法在文件中找到 SGF 棋谱: {path}")

    root = trees[0]
    info = GameInfo()
    info.size_x, info.size_y = _parse_size(root)
    info.komi = _to_float(root.get("KM")) if root.has("KM") else None
    info.handicap = _to_int(root.get("HA"))
    info.black = root.get("PB").strip()
    info.white = root.get("PW").strip()
    info.black_rank = root.get("BR").strip()
    info.white_rank = root.get("WR").strip()
    info.result = root.get("RE").strip()
    if not info.result or info.result in ("?", "??"):
        info.result = infer_result(root.get("C"))
    info.date = root.get("DT").strip()
    info.event = root.get("EV").strip()
    info.round = root.get("RO").strip()
    info.place = root.get("PC").strip()
    info.rules = root.get("RU").strip()
    info.name = root.get("GN").strip()
    info.source = root.get("SO").strip()
    info.time_limit = root.get("TM").strip()

    line, branches = _choose_line(root, line_strategy)

    review = Review(info=info, branch_count=branches, source_encoding=enc,
                    source_path=os.path.abspath(path))

    # 只写了 HA[] 却没给 AB[] 的让子谱: 按标准星位补全 (真实 KGS/野狐谱常见)
    setup_b: List[Tuple[int, int]] = []
    setup_w: List[Tuple[int, int]] = []
    for node in line[:4]:
        for p in parse_point_list(node.get_all("AB"), info.size_x, info.size_y):
            if p not in setup_b:
                setup_b.append(p)
    if info.handicap >= 2 and not setup_b:
        setup_b = standard_handicap_stones(info.size_x, info.size_y, info.handicap)
        if setup_b:
            review.warnings.append(
                f"HA[{info.handicap}] 无 AB[], 已按标准星位摆 {len(setup_b)} 子")

    root_lz = root.get("LZ")
    if root_lz:
        review.initial_analysis = lz.parse_lz(root_lz, info.size_x, info.size_y)

    number = 0
    color_to_play = BLACK if info.handicap == 0 else WHITE

    for idx, node in enumerate(line):
        # ---- 初始布置 ------------------------------------------------------
        for key, bucket in (("AB", setup_b), ("AW", setup_w)):
            for p in parse_point_list(node.get_all(key), info.size_x, info.size_y):
                if p not in bucket:
                    bucket.append(p)

        # ---- 节点标记 ------------------------------------------------------
        marks: List[Mark] = []
        if node.has("LB"):
            for v in node.get_all("LB"):
                if ":" in v:
                    ptxt, label = v.split(":", 1)
                    p = parse_point(ptxt, info.size_x, info.size_y)
                    if p:
                        marks.append(Mark("LB", p[0], p[1], label))
        for kind in ("TR", "SQ", "CR", "MA", "SL", "TB", "TW", "DD"):
            for p in parse_point_list(node.get_all(kind), info.size_x, info.size_y):
                marks.append(Mark(kind, p[0], p[1]))

        # ---- 着手 ----------------------------------------------------------
        mv = None
        for key, col in (("B", BLACK), ("W", WHITE)):
            if node.has(key):
                vals = node.get_all(key)
                raw = vals[0] if vals else ""
                raw = raw.strip()
                if raw in ("", "tt") and info.size_x <= 19:
                    mv = Move(col, -1, -1, 0, is_pass=True)
                else:
                    p = parse_point(raw, info.size_x, info.size_y)
                    mv = Move(col, p[0], p[1], 0) if p else Move(col, -1, -1, 0, is_pass=True)
                break

        if mv is not None:
            number += 1
            mv.number = number
            human, engine = lz.split_comment(node.get("C").strip())
            mv.comment = human
            mv.engine_comment = engine
            mv.node_name = node.get("N").strip()
            mv.marks = marks
            raw_lz = node.get("LZ")
            if raw_lz:
                mv.analysis = lz.parse_lz(raw_lz, info.size_x, info.size_y)
            raw_lz2 = node.get("LZ2")
            if raw_lz2:
                mv.analysis2 = lz.parse_lz(raw_lz2, info.size_x, info.size_y)
            review.moves.append(mv)
            color_to_play = WHITE if mv.color == BLACK else BLACK
        elif marks and review.moves:
            # 纯标记节点: 挂到上一手
            review.moves[-1].marks.extend(marks)
        elif marks and not review.moves:
            # 无手数的纯标记局面, 用一个虚拟 pass 承载
            number += 1
            phantom = Move(color_to_play, -1, -1, number, is_pass=True)
            phantom.marks = marks
            review.moves.append(phantom)

    review.setup_black = setup_b
    review.setup_white = setup_w

    # 数据卫生: 同名初始化子去重 / 冲突
    overlap = set(setup_b) & set(setup_w)
    if overlap:
        review.warnings.append(f"{len(overlap)} 个交叉点同时被 AB/AW 指定, 已按黑子处理")
        review.setup_white = [p for p in setup_w if p not in overlap]

    if info.handicap == 0 and len(setup_b) > 1:
        info.handicap = len(setup_b)
    if not info.komi and info.handicap:
        info.komi = 0.5 if info.handicap else None

    review.total_nodes = sum(1 for _ in _walk(root))
    return review


def _walk(node: Node):
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        stack.extend(n.children)


def parse_collection_reviews(path: str, line_strategy: str = "longest") -> List[Review]:
    """一个文件里可能有多局棋 (SGF collection)."""
    text, enc = read_sgf_text(path)
    trees = parse_collection(text)
    if not trees:
        raise ValueError(f"无法在文件中找到 SGF 棋谱: {path}")
    out: List[Review] = []
    for idx, tree in enumerate(trees):
        # 复用单局逻辑: 临时把该树当作唯一树
        info = GameInfo()
        info.size_x, info.size_y = _parse_size(tree)
        info.komi = _to_float(tree.get("KM")) if tree.has("KM") else None
        info.handicap = _to_int(tree.get("HA"))
        info.black = tree.get("PB").strip()
        info.white = tree.get("PW").strip()
        info.black_rank = tree.get("BR").strip()
        info.white_rank = tree.get("WR").strip()
        info.result = tree.get("RE").strip()
        if not info.result or info.result in ("?", "??"):
            info.result = infer_result(tree.get("C"))
        info.date = tree.get("DT").strip()
        info.event = tree.get("EV").strip()
        info.round = tree.get("RO").strip()
        info.place = tree.get("PC").strip()
        info.rules = tree.get("RU").strip()
        info.name = tree.get("GN").strip()
        line, branches = _choose_line(tree, line_strategy)

        rv = Review(info=info, branch_count=branches, source_encoding=enc,
                    source_path=os.path.abspath(path))
        # HA 无 AB 补星位 (与 parse_sgf 一致)
        has_ab = False
        for node in line[:4]:
            if node.has("AB"):
                has_ab = True
                break
        if info.handicap >= 2 and not has_ab:
            auto = standard_handicap_stones(info.size_x, info.size_y, info.handicap)
            if auto:
                rv.setup_black = auto
                rv.warnings.append(
                    f"HA[{info.handicap}] 无 AB[], 已按标准星位摆 {len(auto)} 子")
        root_lz = tree.get("LZ")
        if root_lz:
            rv.initial_analysis = lz.parse_lz(root_lz, info.size_x, info.size_y)
        number = 0
        for node in line:
            for key, bucket in (("AB", rv.setup_black), ("AW", rv.setup_white)):
                for p in parse_point_list(node.get_all(key), info.size_x, info.size_y):
                    if p not in bucket:
                        bucket.append(p)
            marks: List[Mark] = []
            if node.has("LB"):
                for v in node.get_all("LB"):
                    if ":" in v:
                        ptxt, label = v.split(":", 1)
                        p = parse_point(ptxt, info.size_x, info.size_y)
                        if p:
                            marks.append(Mark("LB", p[0], p[1], label))
            for kind in ("TR", "SQ", "CR", "MA", "SL", "TB", "TW", "DD"):
                for p in parse_point_list(node.get_all(kind), info.size_x, info.size_y):
                    marks.append(Mark(kind, p[0], p[1]))
            mv = None
            for key, col in (("B", BLACK), ("W", WHITE)):
                if node.has(key):
                    vals = node.get_all(key)
                    raw = (vals[0] if vals else "").strip()
                    if raw in ("", "tt") and info.size_x <= 19:
                        mv = Move(col, -1, -1, 0, is_pass=True)
                    else:
                        p = parse_point(raw, info.size_x, info.size_y)
                        mv = Move(col, p[0], p[1], 0) if p else Move(col, -1, -1, 0, is_pass=True)
                    break
            if mv is not None:
                number += 1
                mv.number = number
                human, engine = lz.split_comment(node.get("C").strip())
                mv.comment = human
                mv.engine_comment = engine
                mv.node_name = node.get("N").strip()
                mv.marks = marks
                raw_lz = node.get("LZ")
                if raw_lz:
                    mv.analysis = lz.parse_lz(raw_lz, info.size_x, info.size_y)
                raw_lz2 = node.get("LZ2")
                if raw_lz2:
                    mv.analysis2 = lz.parse_lz(raw_lz2, info.size_x, info.size_y)
                rv.moves.append(mv)
            elif marks and rv.moves:
                rv.moves[-1].marks.extend(marks)
        rv.series = lz.build_series(rv.moves, info.size_x, info.size_y, info.komi)
        out.append(rv)
    return out
