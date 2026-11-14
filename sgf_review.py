#!/usr/bin/env python3
"""SGF 对局回顾视频生成器 - 命令行入口."""
import multiprocessing

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sgfr.cli import main

if __name__ == "__main__":
    # PyInstaller 单文件 + Windows spawn: 不调用它, 每个子进程都会
    # 重新跑一遍入口并继续派生子进程。
    multiprocessing.freeze_support()
    sys.exit(main())
