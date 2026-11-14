"""轻量常量: 分辨率 / 预设方案 / 可持久化配置键.

单独成模块是为了让图形界面只加载这些常量, 不必为了拿一张预设表就把
numpy / Pillow 一起 import 进来 (GUI 常驻进程应保持尽可能轻)。
"""
from __future__ import annotations

import argparse
from typing import Dict, Tuple

SIZE_PRESETS: Dict[str, Tuple[int, int]] = {
    "1080p": (1920, 1080),
    "720p": (1280, 720),
    "1440p": (2560, 1440),
    "4k": (3840, 2160),
    "2160p": (3840, 2160),
    "vertical": (1080, 1920),
    "shorts": (1080, 1920),
    "square": (1080, 1080),
}

# 一键套用的常用方案。优先级: 命令行显式参数 > 配置文件 > 预设 > 默认值。
# 只放「可复用」的渲染参数, 不含输入输出路径与一次性开关。
PRESETS: Dict[str, Dict] = {
    "review": dict(size="1080p", fps=30, quality="high", theme="classic"),
    "shorts": dict(size="vertical", fps=30, quality="high", theme="midnight",
                   labels="last", duration=60, intro=1.8, outro=2.0),
    "heat": dict(size="1080p", fps=30, quality="high", theme="midnight",
                 duration=90, heatmap=True),
    "fast": dict(size="720p", fps=24, quality="medium", eco=True,
                 no_intro=True, no_outro=True, no_audio=True),
    "eco": dict(size="720p", fps=24, quality="medium", eco=True, jobs=2),
}

PRESET_DESC: Dict[str, str] = {
    "review": "1080p 高清复盘, 默认主题与节奏",
    "shorts": "9:16 竖屏 60 秒短视频, 暗夜主题, 只标最后一手",
    "heat": "领地热力图复盘: 90 秒 / 暗夜主题 / 开启热力图",
    "fast": "快速草稿: 720p24 / 无片头片尾 / 无音频 / 低占用",
    "eco": "低占用: 720p24 / 双进程 / 关动画, 适合后台边用电脑边渲染",
}

# 可写进配置文件的参数 (一次性动作 / 路径类不保存)
CONFIG_KEYS = (
    "theme", "size", "fps", "speed", "duration", "labels", "label_count",
    "lang", "watermark", "bgm_gain", "line", "game", "quality", "codec",
    "jobs", "intro", "outro", "preset", "heatmap", "heat_strength",
    "eco", "skip_existing", "kit",
    "no_comment", "no_coords", "no_audio", "no_analysis",
    "no_ai_commentary", "no_intro", "no_outro", "gif_fps", "gif_scale",
)


def parse_size(txt: str) -> Tuple[int, int]:
    s = (txt or "").strip().lower()
    if s in SIZE_PRESETS:
        return SIZE_PRESETS[s]
    if "x" in s:
        a, b = s.split("x", 1)
        try:
            w, h = int(a), int(b)
            if w >= 240 and h >= 240:
                return (w // 2 * 2, h // 2 * 2)
        except ValueError:
            pass
    raise argparse.ArgumentTypeError(
        f"无法识别的分辨率 '{txt}' (示例: 1920x1080, vertical, 4k)")
