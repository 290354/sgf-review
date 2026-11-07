"""视频编码: 裸帧经管道送入 ffmpeg, 支持分段并行后无损拼接."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from typing import List, Optional, Sequence

QUALITY = {
    "low": {"h264": "3M", "hevc": "3M", "vp9": "28"},
    "medium": {"h264": "6M", "hevc": "5M", "vp9": "24"},
    "high": {"h264": "11M", "hevc": "8M", "vp9": "19"},
    "ultra": {"h264": "18M", "hevc": "13M", "vp9": "14"},
}


def ffmpeg_exe() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError(
            "未找到 ffmpeg。请先安装: macOS `brew install ffmpeg` / "
            "Ubuntu `sudo apt install ffmpeg` / Windows 从 ffmpeg.org 下载并加入 PATH。"
        )
    return exe


def has_encoder(name: str) -> bool:
    try:
        out = subprocess.run([ffmpeg_exe(), "-hide_banner", "-encoders"],
                             capture_output=True, text=True, timeout=20).stdout
        return name in out
    except Exception:
        return False


def _pick_codec(kind: str):
    if kind == "vp9" and has_encoder("libvpx-vp9"):
        return ["-c:v", "libvpx-vp9", "-row-mt", "1", "-tile-columns", "2",
                "-frame-parallel", "1", "-auto-alt-ref", "1"]
    if kind == "hevc" and has_encoder("libx265"):
        return ["-c:v", "libx265", "-preset", "medium", "-tag:v", "hvc1"]
    if kind == "h264":
        if has_encoder("libx264"):
            return ["-c:v", "libx264", "-preset", "slow", "-profile:v", "high"]
        if has_encoder("libopenh264"):
            return ["-c:v", "libopenh264"]
    if kind == "hevc":
        # 没有 libx265 就回退 h264
        return _pick_codec("h264")
    return ["-c:v", "libvpx-vp9"]


def open_writer(path: str, w: int, h: int, fps: int, quality: str = "high",
                codec: str = "h264", audio: Optional[str] = None,
                audio_offset: float = 0.0) -> subprocess.Popen:
    """打开一个 ffmpeg 进程, stdin 接受 rgb24 裸帧.

    audio_offset: 只渲染片段时音频输入要跳过的时间(秒). 不加的话,
    片段画面从中间开始而音轨仍从头播, 落子声会全部错位.
    """
    q = QUALITY.get(quality, QUALITY["high"])
    webm = (codec == "vp9")
    cmd = [ffmpeg_exe(), "-y", "-v", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", f"{w}x{h}", "-r", str(fps), "-i", "pipe:0"]
    if audio:
        # -ss 放在对应 -i 之前, 只作用于音频输入, 不影响裸视频流
        if audio_offset > 0:
            cmd += ["-ss", f"{audio_offset:.6f}"]
        cmd += ["-i", audio]
    cargs = _pick_codec(codec)
    cmd += cargs
    if codec == "vp9" or "libvpx-vp9" in cargs:
        cmd += ["-crf", q["vp9"], "-b:v", "0", "-pix_fmt", "yuv420p", "-g", str(fps * 2)]
    elif "libx265" in cargs or codec == "hevc":
        cmd += ["-b:v", q.get("hevc", q["h264"]), "-pix_fmt", "yuv420p", "-g", str(fps * 2)]
    else:
        cmd += ["-b:v", q["h264"], "-pix_fmt", "yuv420p", "-g", str(fps * 2)]
        if "libopenh264" in cargs:
            cmd += ["-bf", "0"]
    cmd += ["-threads", "0"]
    if audio:
        if webm:
            # WebM 容器不允许 AAC, 必须用 Opus / Vorbis
            acodec = "libopus" if has_encoder("libopus") else "libvorbis"
            cmd += ["-c:a", acodec, "-b:a", "128k", "-shortest"]
        else:
            cmd += ["-c:a", "aac", "-b:a", "192k", "-shortest"]
    if webm:
        # 临时分段文件名可能是 .mp4, 显式指定容器避免把 WebM 写成 MP4
        cmd += ["-f", "webm"]
    elif path.lower().endswith((".mp4", ".mov", ".m4v")):
        cmd += ["-movflags", "+faststart"]
    cmd += [path]
    log = tempfile.NamedTemporaryFile(suffix=".fflog", delete=False)
    log.close()
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                            stderr=open(log.name, "wb"))
    proc._sgfr_log = log.name  # type: ignore[attr-defined]
    return proc


def close_writer(proc: subprocess.Popen) -> None:
    if proc.stdin:
        try:
            proc.stdin.close()
        except Exception:
            pass
    code = proc.wait()
    logp = getattr(proc, "_sgfr_log", None)
    err = ""
    if logp and os.path.exists(logp):
        try:
            with open(logp, "rb") as fh:
                err = fh.read().decode("utf-8", "replace")
        except Exception:
            pass
        try:
            os.unlink(logp)
        except Exception:
            pass
    if code != 0:
        raise RuntimeError(f"ffmpeg 编码失败 (code {code}):\n{err[:2000]}")


def concat_segments(paths: Sequence[str], out_path: str, workdir: str) -> None:
    if len(paths) == 1:
        shutil.move(paths[0], out_path)
        return
    lst = os.path.join(workdir, "concat.txt")
    with open(lst, "w", encoding="utf-8") as fh:
        for p in paths:
            fh.write(f"file '{os.path.abspath(p).replace(chr(92), '/')}'\n")
    cmd = [ffmpeg_exe(), "-y", "-v", "error", "-f", "concat", "-safe", "0",
           "-i", lst, "-c", "copy", out_path]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"分段拼接失败:\n{r.stderr[:1500]}")


def mux_audio(video: str, audio: str, out_path: str, copy_video: bool = True,
              codec: str = "h264", audio_offset: float = 0.0) -> None:
    cmd = [ffmpeg_exe(), "-y", "-v", "error", "-i", video]
    if audio_offset > 0:
        cmd += ["-ss", f"{audio_offset:.6f}"]
    cmd += ["-i", audio]
    if copy_video:
        cmd += ["-c:v", "copy"]
    if codec == "vp9":
        acodec = "libopus" if has_encoder("libopus") else "libvorbis"
        cmd += ["-c:a", acodec, "-b:a", "128k", "-shortest", "-f", "webm"]
    else:
        cmd += ["-c:a", "aac", "-b:a", "192k", "-shortest",
                "-movflags", "+faststart"]
    cmd += [out_path]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"音轨合成失败:\n{r.stderr[:1500]}")
