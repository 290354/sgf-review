"""音效: 程序化合成棋子敲击声 (+ 可选 BGM 混音)."""
from __future__ import annotations

import math
import os
import struct
import subprocess
import tempfile
import wave
from typing import List, Optional, Sequence, Tuple

import numpy as np

from .encoder import _NOWIN, ffmpeg_exe

SR = 44100


# --------------------------------------------------------------------------- #
def _bandpass(sig: np.ndarray, sr: int, lo: float, hi: float) -> np.ndarray:
    n = len(sig)
    spec = np.fft.rfft(sig)
    freqs = np.fft.rfftfreq(n, 1.0 / sr)
    lo_t = np.clip((freqs - lo) / max(1.0, lo * 0.4), 0, 1)
    hi_t = np.clip((hi - freqs) / max(1.0, hi * 0.4), 0, 1)
    spec *= (lo_t * hi_t)
    return np.fft.irfft(spec, n)


def _click(seed: int, pitch: float = 1.0, bright: float = 1.0,
           gain: float = 1.0, kit: str = "kaya") -> np.ndarray:
    """一次棋子落盘: 噪声瞬态 + 木质模态共振.

    kit: kaya 榧木 / slate 石板 / paper 纸面 / ice 清脆
    """
    presets = {
        "kaya":  dict(modes=[(298, 0.052, 0.52), (474, 0.038, 0.40), (742, 0.024, 0.30),
                             (1290, 0.015, 0.20), (2140, 0.0085, 0.13)],
                      body_g=0.62, low=118, bright=1.0),
        "slate": dict(modes=[(420, 0.038, 0.48), (880, 0.022, 0.36), (1420, 0.014, 0.24),
                             (2400, 0.008, 0.16), (3600, 0.005, 0.10)],
                      body_g=0.48, low=90, bright=1.15),
        "paper": dict(modes=[(220, 0.070, 0.42), (360, 0.048, 0.34), (560, 0.030, 0.26),
                             (880, 0.018, 0.18), (1400, 0.010, 0.12)],
                      body_g=0.55, low=95, bright=0.85),
        "ice":   dict(modes=[(580, 0.028, 0.50), (1180, 0.016, 0.40), (1980, 0.010, 0.28),
                             (3200, 0.006, 0.18), (4800, 0.004, 0.10)],
                      body_g=0.36, low=140, bright=1.35),
    }
    P = presets.get(kit, presets["kaya"])
    rng = np.random.default_rng(seed)
    dur = 0.30
    n = int(SR * dur)
    t = np.arange(n, dtype=np.float32) / SR

    noise = rng.normal(0.0, 1.0, n).astype(np.float32)
    noise = _bandpass(noise, SR, 1100 * bright * P["bright"], 9500 * bright * P["bright"]).astype(np.float32)
    trans = noise * np.exp(-t / 0.0055) * 0.55

    det = 1.0 + rng.normal(0.0, 0.012)
    body = np.zeros(n, dtype=np.float32)
    for f, tau, amp in P["modes"]:
        phase = rng.random() * 2 * math.pi
        body += (amp * np.sin(2 * math.pi * f * pitch * det * t + phase)
                 * np.exp(-t / tau)).astype(np.float32)

    body += (0.22 * np.sin(2 * math.pi * P["low"] * pitch * t)
             * np.exp(-t / 0.045)).astype(np.float32)

    attack = (1.0 - np.exp(-t / 0.0006)).astype(np.float32)
    sig = (trans + body * P["body_g"]) * attack
    sig *= np.exp(-t / 0.11)
    return (sig * gain).astype(np.float32)


def _capture_sound(seed: int, kit: str = "kaya") -> np.ndarray:
    a = _click(seed, pitch=1.34, bright=1.25, gain=0.30, kit=kit)
    b = _click(seed + 1, pitch=1.52, bright=1.30, gain=0.18, kit=kit)
    shift = int(SR * 0.035)
    out = np.zeros(len(a) + shift, dtype=np.float32)
    out[:len(a)] += a
    out[shift:shift + len(b)] += b
    return out


def _soft_whoosh(seed: int, dur: float = 0.9) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = int(SR * dur)
    t = np.arange(n, dtype=np.float32) / SR
    noise = rng.normal(0.0, 1.0, n).astype(np.float32)
    noise = _bandpass(noise, SR, 180, 2600).astype(np.float32)
    env = np.sin(np.clip(t / dur, 0, 1) * math.pi) ** 2
    return (noise * env * 0.05).astype(np.float32)


# --------------------------------------------------------------------------- #
class Mixer:
    def __init__(self, duration: float):
        self.n = int(math.ceil(duration * SR)) + SR // 4
        self.buf = np.zeros((2, self.n), dtype=np.float32)

    def add(self, sig: np.ndarray, at: float, pan: float = 0.0,
            gain: float = 1.0) -> None:
        start = int(round(at * SR))
        if start >= self.n:
            return
        if start < 0:
            sig = sig[-start:]
            start = 0
        end = min(self.n, start + len(sig))
        seg = sig[:end - start] * gain
        # 等功率声像
        ang = (pan + 1.0) * math.pi / 4.0
        gl, gr = math.cos(ang), math.sin(ang)
        self.buf[0, start:end] += seg * gl * 1.414
        self.buf[1, start:end] += seg * gr * 1.414

    # ------------------------------------------------------------------ #
    def add_bgm(self, path: str, gain: float = 0.22, fade_in: float = 1.2,
                fade_out: float = 2.5) -> bool:
        wav = path
        tmp = None
        if not path.lower().endswith(".wav"):
            tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            tmp.close()
            cmd = [ffmpeg_exe(), "-y", "-v", "error", "-i", path,
                   "-ac", "2", "-ar", str(SR), "-f", "wav", tmp.name]
            try:
                subprocess.run(cmd, check=True, creationflags=_NOWIN)
                wav = tmp.name
            except Exception:
                if os.path.exists(tmp.name):
                    os.unlink(tmp.name)
                return False
        try:
            with wave.open(wav, "rb") as wf:
                ch = wf.getnchannels()
                sw = wf.getsampwidth()
                fr = wf.getframerate()
                raw = wf.readframes(wf.getnframes())
            if sw != 2:
                return False
            data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            if ch == 2:
                data = data.reshape(-1, 2).T
            else:
                data = np.vstack([data, data])
            if fr != SR:
                # 简易重采样
                idx = np.linspace(0, data.shape[1] - 1, int(data.shape[1] * SR / fr))
                data = np.vstack([np.interp(idx, np.arange(data.shape[1]), data[c])
                                  for c in range(2)])
            reps = int(math.ceil(self.n / max(1, data.shape[1])))
            loop = np.tile(data, (1, reps))[:, :self.n]
            env = np.ones(self.n, dtype=np.float32)
            fi, fo = int(fade_in * SR), int(fade_out * SR)
            if fi > 0:
                env[:fi] = np.linspace(0, 1, fi) ** 1.5
            if fo > 0:
                env[-fo:] *= np.linspace(1, 0, fo) ** 1.5
            self.buf += loop * env[None, :] * gain
            return True
        except Exception:
            return False
        finally:
            if tmp and os.path.exists(tmp.name):
                os.unlink(tmp.name)

    # ------------------------------------------------------------------ #
    def finish(self, path: str, normalize: float = 0.86) -> None:
        buf = self.buf
        # 短混响: 几次早期反射, 让声音不干
        for delay_ms, g in ((19, 0.14), (37, 0.085), (61, 0.045)):
            d = int(SR * delay_ms / 1000)
            if d < self.n:
                buf[:, d:] += buf[:, :self.n - d] * g

        peak = float(np.max(np.abs(buf))) if buf.size else 0.0
        if peak > 1e-6:
            buf = buf * (normalize / peak)
        # 软削波, 保护瞬态
        buf = np.tanh(buf * 1.06) * 0.94
        # 末尾淡出, 避免爆音
        tail = min(int(SR * 0.25), self.n)
        buf[:, -tail:] *= np.linspace(1, 0, tail) ** 2

        pcm = np.clip(buf.T * 32767, -32768, 32767).astype(np.int16)
        with wave.open(path, "wb") as wf:
            wf.setnchannels(2)
            wf.setsampwidth(2)
            wf.setframerate(SR)
            wf.writeframes(pcm.tobytes())


# --------------------------------------------------------------------------- #
def build_audio(review, timeline, path: str, mode: str = "click",
                bgm: Optional[str] = None, bgm_gain: float = 0.20,
                kit: str = "kaya") -> None:
    mixer = Mixer(timeline.total)
    if mode not in ("none", "off", "silent"):
        # 片头: 极轻的空气声
        mixer.add(_soft_whoosh(11, 1.6), 0.05, 0.0, 0.9)

        nx = max(1, review.info.size_x)
        for slot in timeline.slots:
            mv = review.moves[slot.index]
            at = slot.start + slot.anim * 0.52
            if mv.is_pass:
                continue
            pan = (mv.x / max(1, nx - 1)) * 2 - 1
            pan *= 0.45
            pitch = 0.94 if mv.color == 1 else 1.07
            gain = 0.85 + 0.15 * ((slot.index * 37) % 5) / 4.0
            mixer.add(_click(1000 + slot.index * 7, pitch=pitch, gain=gain, kit=kit),
                      at, pan, 0.62)
            if mv.captured:
                mixer.add(_capture_sound(2000 + slot.index * 3, kit=kit),
                          at + 0.045, pan * 0.8, 0.55)

        # 片尾: 一声收束
        mixer.add(_click(9999, pitch=0.86, gain=0.55, kit=kit),
                  timeline.outro_start + 0.10, 0.0, 0.5)
        mixer.add(_soft_whoosh(23, 1.2), timeline.outro_start + 0.2, 0.0, 0.7)

    if bgm:
        mixer.add_bgm(bgm, gain=bgm_gain)
    mixer.finish(path)
