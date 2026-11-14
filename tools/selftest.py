#!/usr/bin/env python3
"""快速回归: 解析器 + 棋盘 + 让子补全 + 压缩坐标 + LZ 归一.

用法: python3 tools/selftest.py
"""
from __future__ import annotations

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from sgfr.sgf import parse_collection_reviews
from sgfr.board import validate, Board, BLACK, WHITE
from sgfr import lz

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}  {extra}")


def write(sgf: str) -> str:
    fd, path = tempfile.mkstemp(suffix=".sgf")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(sgf)
    return path


def main() -> int:
    print("== SGF 解析 ==")
    p = write("(;GM[1]FF[4]SZ[19]KM[6.5]PB[A]PW[B]RE[B+R];B[cc];W[dd])")
    rv = parse_collection_reviews(p)[0]
    check("基本属性", rv.info.black == "A" and rv.info.result == "B+R")
    check("手数", len(rv.moves) == 2)

    p = write("(;GM[1]FF[4]SZ[19]HA[4]PB[A]PW[B];B[cc];W[dd])")
    rv = parse_collection_reviews(p)[0]
    check("HA4 无 AB 自动摆 4 子", len(rv.setup_black) == 4)
    p = write("(;GM[1]FF[4]SZ[19]HA[9]PB[A]PW[B];B[cc];W[dd])")
    rv = parse_collection_reviews(p)[0]
    check("HA9 自动摆 9 子", len(rv.setup_black) == 9)
    p = write("(;GM[1]FF[4]SZ[19]HA[2]PB[A]PW[B];B[cc];W[dd])")
    rv = parse_collection_reviews(p)[0]
    check("HA2 对角", rv.setup_black == [(3, 3), (15, 15)], str(rv.setup_black))

    p = write("(;GM[1]FF[4]SZ[19]AB[aa:cc];B[dd];W[ee])")
    rv = parse_collection_reviews(p)[0]
    check("压缩矩形 AB", len(rv.setup_black) == 9, str(rv.setup_black))

    # 显式 KM[0] 是合法的 0 贴目, 不得被让子局默认贴目覆盖;
    # 单局 API 与合集 API 两条解析路径必须给出一致结果。
    p = write("(;GM[1]FF[4]SZ[19]HA[4]KM[0]PB[A]PW[B]"
              ";AB[pd][dp][dd][pp];W[qp];B[qo])")
    from sgfr.sgf import parse_sgf
    k_single = parse_sgf(p).info.komi
    k_multi = parse_collection_reviews(p)[0].info.komi
    check("KM[0] 单局 API 保留 0 贴目", k_single == 0.0, k_single)
    check("KM[0] 合集 API 保留 0 贴目", k_multi == 0.0, k_multi)
    check("KM[0] 两条路径一致", k_single == k_multi, (k_single, k_multi))
    p = write("(;GM[1]FF[4]SZ[19]HA[4]PB[A]PW[B];B[cc];W[dd])")
    check("无 KM 让子局补默认贴目", parse_sgf(p).info.komi == 0.5,
          parse_sgf(p).info.komi)

    p = write("(;GM[1]FF[4]SZ[9];B[aa];B[aa];W[bb];B[];W[];B[cc])")
    rv = parse_collection_reviews(p)[0]
    problems = validate(rv)
    check("非法手标记", rv.moves[1].valid is False)
    check("虚手 pass", rv.moves[3].is_pass and rv.moves[4].is_pass)
    check("非法不中断", len(problems) == 1)

    p = write("(;GM[1]FF[4]SZ[19]PB[T]PW[W];AW[aa];B[ba];W[];B[ab])")
    rv = parse_collection_reviews(p)[0]
    validate(rv)
    check("提子写回", rv.moves[2].captured == [(0, 0)], str(rv.moves[2].captured))

    p = write("(;GM[1]FF[4]SZ[19]PB[T]PW[W]RE[?];B[aa])")
    # infer_result 走注释
    p = write("(;GM[1]FF[4]SZ[19]PB[T]PW[W]RE[]C[黑中盘胜];B[aa])")
    rv = parse_collection_reviews(p)[0]
    check("注释推结果", "B+" in rv.info.result, rv.info.result)

    print("== LZ ==")
    p = os.path.join(ROOT, "records", "analysis",
                     "2022_LG_cup_semifinal_DingHao_vs_JiangDongrun.sgf")
    if os.path.exists(p):
        rv = parse_collection_reviews(p)[0]
        ser = rv.series
        check("LZ 解析", ser.count > 0, f"count={ser.count}")
        check("黑方视角 0-100", all(v is None or 0 <= v <= 100 for v in ser.black_wr))
        check("吻合度有值", ser.judged_moves > 0)
        # 第 1 手若有分析, black_wr 应等于落子方视角换算
        for i, mv in enumerate(rv.moves):
            an = ser.points[i]
            if an and an.winrate is not None:
                bwr = an.winrate if mv.color == BLACK else 100 - an.winrate
                check("视角归一", abs(ser.black_wr[i] - bwr) < 1e-6)
                break
    else:
        print("  skip LZ fixture")

    print("== examples 全量 ==")
    ex = os.path.join(ROOT, "examples")
    n = 0
    for fn in sorted(os.listdir(ex)):
        if not fn.endswith(".sgf"):
            continue
        path = os.path.join(ex, fn)
        if os.path.isdir(path):
            continue
        for rv in parse_collection_reviews(path):
            validate(rv)
            n += 1
    lz_dir = os.path.join(ROOT, "examples", "lz")
    if os.path.isdir(lz_dir):
        for fn in sorted(os.listdir(lz_dir)):
            if fn.endswith(".sgf"):
                for rv in parse_collection_reviews(os.path.join(lz_dir, fn)):
                    validate(rv)
                    n += 1
    ana = os.path.join(ROOT, "records", "analysis")
    if os.path.isdir(ana):
        for fn in sorted(os.listdir(ana)):
            if fn.endswith(".sgf"):
                for rv in parse_collection_reviews(os.path.join(ana, fn)):
                    validate(rv)
                    n += 1
    check("examples+analysis 无异常", n >= 10, f"n={n}")

    print("== CLI / workflow ==")
    from sgfr.cli import build_parser, parse_size
    from sgfr.renderer import describe_result
    from sgfr.workflow import apply_preset_config, write_chapters
    from sgfr.timeline import Timing, build_timeline
    from collections import Counter

    p = build_parser()
    c = Counter()
    for a in p._actions:
        for s in a.option_strings:
            c[s] += 1
    dups = [s for s, n in c.items() if n > 1]
    check("argparse 无重复选项", not dups, str(dups))
    vers = [a for a in p._actions if "--version" in a.option_strings]
    check("--version 只注册一次", len(vers) == 1, len(vers))

    check("parse_size 1080p", parse_size("1080p") == (1920, 1080))
    check("parse_size vertical", parse_size("vertical") == (1080, 1920))
    check("parse_size custom", parse_size("1280x720") == (1280, 720))
    try:
        parse_size("bogus")
        check("parse_size 非法报错", False)
    except Exception:
        check("parse_size 非法报错", True)

    check("describe B+R", describe_result("B+R", "zh")[0] == "黑中盘胜",
          describe_result("B+R", "zh"))
    check("describe W+3.5", "白" in describe_result("W+3.5", "zh")[0],
          describe_result("W+3.5", "zh"))
    check("describe 和局", describe_result("0", "zh")[0] == "和局")

    args = p.parse_args(["x.sgf", "--preset", "fast"])
    args, notes = apply_preset_config(args, ["x.sgf", "--preset", "fast"])
    check("preset fast eco", bool(getattr(args, "eco", False)))
    check("preset fast no_intro", bool(getattr(args, "no_intro", False)))
    check("preset fast size", args.size == "720p", args.size)
    check("preset fast fps", args.fps == 24, args.fps)

    # chapters
    demo = os.path.join(ROOT, "examples", "demo_19x19.sgf")
    if os.path.exists(demo):
        rv = parse_collection_reviews(demo)[0]
        tl = build_timeline(rv, Timing())
        ch_path = os.path.join(tempfile.gettempdir(), "sgfr_chapters_test.txt")
        n = write_chapters(rv, tl, ch_path, "zh")
        check("chapters 生成", n >= 2 and os.path.getsize(ch_path) > 10, n)

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
