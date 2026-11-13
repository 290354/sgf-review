# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller: 生成单文件 Windows exe。

同一个 exe 承担两种角色:
  * 不带参数  -> 图形界面
  * 带 --cli  -> 命令行渲染引擎 (界面用子进程调起)
因此必须保留 console=True 让 --cli 的 stdout 管道可用; 界面启动后会调用
_win32 ShowWindow(SW_HIDE) 把控制台窗口藏起来。
"""
import os
import sys

from PyInstaller.utils.hooks import collect_submodules

hidden = [
    "sgfr", "sgfr.cli", "sgfr.gui", "sgfr.presets", "sgfr.workflow",
    "sgfr.renderer", "sgfr.boardview", "sgfr.board", "sgfr.theme",
    "sgfr.timeline", "sgfr.lz", "sgfr.sgf", "sgfr.charts",
    "sgfr.graphics", "sgfr.audio", "sgfr.encoder", "sgfr.export",
    "numpy", "PIL", "tkinter",
]
hidden += collect_submodules("numpy")
hidden += collect_submodules("PIL")

# .ico 只在 Windows 目标有效; 其他平台构建时置空, 便于本地验证打包链
icon_path = ("assets/icon.ico"
             if sys.platform == "win32" and os.path.isfile("assets/icon.ico")
             else None)

a = Analysis(
    ["sgf_gui.py"],
    pathex=["."],
    binaries=[],
    datas=[],
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["matplotlib", "scipy", "pandas", "pytest", "IPython",
              "notebook", "torch", "tensorflow"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="sgf-review",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                 # 不压缩: 启动更快, 也减少杀软误报
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,              # --cli 通道需要 stdout
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_path,
)
