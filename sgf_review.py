#!/usr/bin/env python3
"""SGF 对局回顾视频生成器 - 命令行入口."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sgfr.cli import main

if __name__ == "__main__":
    sys.exit(main())
