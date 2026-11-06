#!/usr/bin/env python3
"""下载公开的真实棋谱 (KGS 高段对局 / 日本职业棋谱合集).

用法:
    python3 tools/fetch_records.py kgs 2019_04      # 某一月的 KGS 7d+ 对局
    python3 tools/fetch_records.py kgs 2014          # 某一整年
    python3 tools/fetch_records.py pro alphago       # AlphaGo 全部对局(含解说)
    python3 tools/fetch_records.py pro cho_chikun    # 赵治勋
    python3 tools/fetch_records.py pro go_seigen     # 吴清源
    python3 tools/fetch_records.py pro shusaku       # 本因坊秀策

数据来源 (均为公开发布、可自由下载的棋谱归档):
  * KGS 高段对局: https://u-go.net/gamerecords/
  * 日本职业棋谱: https://homepages.cwi.nl/~aeb/go/games/
"""
from __future__ import annotations

import os
import sys
import tarfile
import zipfile
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RECORDS = os.path.join(ROOT, "records")

KGS_BASE = "https://dl.u-go.net/gamerecords/KGS-{}-19-{}-.zip"
PRO_BASE = "https://homepages.cwi.nl/~aeb/go/games/{}.tgz"

# 各归档的棋谱数量 (用于拼出正确文件名)
KGS_COUNT = {
    "2019_04": 1255, "2019_03": 1478, "2019_02": 1412, "2019_01": 2095,
    "2018_12": 1992, "2018_11": 1879, "2018_10": 1209, "2018_09": 1587,
    "2018_08": 1447, "2018_07": 949, "2018_06": 1002, "2018_05": 1590,
    "2018_04": 1612, "2018_03": 833, "2018_02": 1167, "2018_01": 1526,
    "2015": 8133, "2014": 13029, "2013": 13783, "2012": 13665,
    "2011": 19099, "2010": 17536, "2009": 18837, "2008": 14002,
}
PRO_FILES = {"alphago", "cho_chikun", "go_seigen", "shusaku", "dosaku", "jowa"}


def _download(url: str, dest: str) -> None:
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        print(f"已存在: {dest}")
        return
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    print(f"下载 {url}")
    tmp = dest + ".part"
    with urllib.request.urlopen(url, timeout=180) as resp, open(tmp, "wb") as fh:
        total = int(resp.headers.get("Content-Length") or 0)
        got = 0
        while True:
            chunk = resp.read(1 << 16)
            if not chunk:
                break
            fh.write(chunk)
            got += len(chunk)
            if total:
                pct = got * 100 // total
                print(f"\r  {got / 1048576:.1f}/{total / 1048576:.1f} MB ({pct}%)",
                      end="", flush=True)
    print()
    os.replace(tmp, dest)


def fetch_kgs(period: str) -> None:
    count = KGS_COUNT.get(period)
    if count is None:
        raise SystemExit(f"未收录的时间段: {period} (可用: {', '.join(sorted(KGS_COUNT))})")
    url = KGS_BASE.format(period, count)
    dest = os.path.join(RECORDS, "_dl", f"kgs-{period}.zip")
    _download(url, dest)
    out = os.path.join(RECORDS, "kgs", period)
    os.makedirs(out, exist_ok=True)
    with zipfile.ZipFile(dest) as zf:
        for name in zf.namelist():
            if name.lower().endswith(".sgf"):
                base = os.path.basename(name)
                with zf.open(name) as src, open(os.path.join(out, base), "wb") as dst:
                    dst.write(src.read())
    n = len([f for f in os.listdir(out) if f.endswith(".sgf")])
    print(f"完成: {out} ({n} 局)")


def fetch_pro(key: str) -> None:
    if key not in PRO_FILES:
        raise SystemExit(f"未收录的合集: {key} (可用: {', '.join(sorted(PRO_FILES))})")
    url = PRO_BASE.format(key)
    dest = os.path.join(RECORDS, "_dl", f"{key}.tgz")
    _download(url, dest)
    out = os.path.join(RECORDS, "pro", key)
    os.makedirs(out, exist_ok=True)
    with tarfile.open(dest, "r:gz") as tf:
        for m in tf.getmembers():
            if m.isfile() and m.name.lower().endswith(".sgf"):
                src = tf.extractfile(m)
                if src is None:
                    continue
                with open(os.path.join(out, os.path.basename(m.name)), "wb") as fh:
                    fh.write(src.read())
    n = len([f for f in os.listdir(out) if f.endswith(".sgf")])
    print(f"完成: {out} ({n} 局)")


def main(argv) -> int:
    if len(argv) < 3 or argv[1] not in ("kgs", "pro"):
        print(__doc__)
        return 1
    if argv[1] == "kgs":
        fetch_kgs(argv[2])
    else:
        fetch_pro(argv[2])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
