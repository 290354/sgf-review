#!/usr/bin/env python3
"""SGF Review 图形界面 / Windows 绿色版入口.

用法:
    python sgf_gui.py                 启动图形界面
    python sgf_gui.py --cli <参数…>    把参数交给命令行引擎

`--cli` 分支是给界面自己用的: 渲染放在子进程里跑, 界面主线程始终空闲,
既能实时打印日志, 又能随时「停止」而不卡住窗口。PyInstaller 打包成单个
exe 后 sys.executable 就是 exe 本身, 这条通道依旧有效。
"""
from __future__ import annotations

import multiprocessing

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--cli":
        # Windows 控制台默认 GBK, 中文日志在管道里会乱码; 统一成 UTF-8
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
            except Exception:
                pass
        from sgfr.cli import main as cli_main
        return cli_main(sys.argv[2:])

    from sgfr.gui import launch
    return launch()


if __name__ == "__main__":
    # 必须在任何进程池创建之前调用: Windows 用 spawn 启动子进程,
    # PyInstaller 冻结后子进程会重新执行入口; 没有这行会无限套娃.
    multiprocessing.freeze_support()
    sys.exit(main() or 0)
