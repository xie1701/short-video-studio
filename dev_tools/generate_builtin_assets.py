#!/usr/bin/env python3
"""
生成 100% 原创 CC0 可商用的内置资产（NumPy 向量化 + 160kbps M4A 轻量化版本）：
1. BGM (.m4a + 节拍元数据 .json): upbeat_store_120bpm, chill_story_100bpm
2. SFX (.wav): whoosh, ding, pop, bass_drop, shutter
3. 3D LUTs (.cube): warm_store, clean_creator
"""

from __future__ import annotations

import subprocess
import sys
import wave
from pathlib import Path
from typing import List

SCRIPTS_ROOT = Path(__file__).resolve().parents[1] / "short-video-studio" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from common import ASSETS_DIR, find_bin, maybe_reexec_in_venv, write_cube_lut, write_json

maybe_reexec_in_venv()

import numpy as np

SAMPLE_RATE = 48000


def write_stereo_wav(path: Path, samples_l: np.ndarray, samples_r: np.ndarray, sr: int = SAMPLE_RATE) -> None:
    """将双声道浮点采样向量化归一化并写入 16-bit PCM WAV。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    l_arr = np.asarray(samples_l, dtype=np.float32)
    r_arr = np.asarray(samples_r, dtype=np.float32)
    peak = max(1e-6, float(np.max(np.abs(l_arr))), float(np.max(np.abs(r_arr))))
    scale = 0.92 / max(1.0, peak)
    inter = np.empty((len(l_arr), 2), dtype=np.int16)
    inter[:, 0] = np.clip(np.round(l_arr * scale * 32767.0), -32767, 32767).astype(np.int16)
    inter[:, 1] = np.clip(np.round(r_arr * scale * 32767.0), -32767, 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(inter.tobytes())


def generate_bgm(name: str, bpm: float, duration_sec: float, chord_freqs: List[List[float]]) -> None:
    """向量化合成免版税 CC0 背景音乐，优先封装为轻量化 160kbps M4A。"""
    total_samples = int(duration_sec * SAMPLE_RATE)
    beat_sec = 60.0 / bpm
    rng = np.random.default_rng(42)

    beat_times: List[float] = []
    downbeat_times: List[float] = []
    t = 0.0
    beat_idx = 0
    while t < duration_sec - 0.05:
        beat_times.append(round(t, 4))
        if beat_idx % 4 == 0:
            downbeat_times.append(round(t, 4))
        t += beat_sec
        beat_idx += 1

    ts = np.arange(total_samples, dtype=np.float64) / SAMPLE_RATE
    bar_sec = beat_sec * 4.0
    bar_idx = (ts // bar_sec).astype(np.int32) % len(chord_freqs)
    bar_local = (ts % bar_sec) / bar_sec
    pad_env = 0.28 + 0.12 * np.sin(np.pi * bar_local)
    edge_env = np.minimum(1.0, np.minimum(ts / 0.05, np.maximum(0.0, (duration_sec - ts) / 0.15)))

    samples_l = np.zeros(total_samples, dtype=np.float64)
    samples_r = np.zeros(total_samples, dtype=np.float64)
    for b_i, chord in enumerate(chord_freqs):
        mask = bar_idx == b_i
        if not np.any(mask):
            continue
        ts_m = ts[mask]
        vl = np.zeros_like(ts_m)
        vr = np.zeros_like(ts_m)
        for idx, freq in enumerate(chord):
            phase = 2.0 * np.pi * freq * ts_m
            tone = np.sin(phase) * 0.7 + np.sin(phase * 2.0) * 0.15
            pan = 0.5 + 0.25 * ((-1) ** idx)
            vl += tone * (1.0 - pan * 0.3)
            vr += tone * (0.7 + pan * 0.3)
        gain = (pad_env[mask] * 0.45 * edge_env[mask]) / max(1, len(chord))
        samples_l[mask] = vl * gain
        samples_r[mask] = vr * gain

    for b_idx, bt in enumerate(beat_times):
        start_s = int(bt * SAMPLE_RATE)
        if b_idx % 4 == 0:
            kick_len = min(int(0.18 * SAMPLE_RATE), total_samples - start_s)
            if kick_len > 0:
                dt = np.arange(kick_len, dtype=np.float64) / SAMPLE_RATE
                freq = 52.0 + 95.0 * np.exp(-dt * 28.0)
                kick = np.sin(2.0 * np.pi * freq * dt) * np.exp(-dt * 18.0) * 0.65
                samples_l[start_s : start_s + kick_len] += kick
                samples_r[start_s : start_s + kick_len] += kick
        elif b_idx % 4 == 2:
            snare_len = min(int(0.10 * SAMPLE_RATE), total_samples - start_s)
            if snare_len > 0:
                dt = np.arange(snare_len, dtype=np.float64) / SAMPLE_RATE
                noise = (rng.uniform(-1.0, 1.0, snare_len) * 0.28 + np.sin(2.0 * np.pi * 190.0 * dt) * 0.22) * np.exp(-dt * 35.0)
                samples_l[start_s : start_s + snare_len] += noise
                samples_r[start_s : start_s + snare_len] += noise
        else:
            hat_len = min(int(0.05 * SAMPLE_RATE), total_samples - start_s)
            if hat_len > 0:
                dt = np.arange(hat_len, dtype=np.float64) / SAMPLE_RATE
                hat = rng.uniform(-1.0, 1.0, hat_len) * 0.16 * np.exp(-dt * 75.0)
                samples_l[start_s : start_s + hat_len] += hat * 0.9
                samples_r[start_s : start_s + hat_len] += hat * 1.1

    wav_path = ASSETS_DIR / "bgm" / f"{name}.wav"
    m4a_path = ASSETS_DIR / "bgm" / f"{name}.m4a"
    meta_path = ASSETS_DIR / "bgm" / f"{name}.json"
    write_stereo_wav(wav_path, samples_l, samples_r)
    ffmpeg = find_bin("ffmpeg")
    if ffmpeg:
        subprocess.run([
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(wav_path), "-c:a", "aac", "-b:a", "160k", "-ar", str(SAMPLE_RATE), "-ac", "2",
            "-movflags", "+faststart", str(m4a_path),
        ], check=True)
        wav_path.unlink(missing_ok=True)
    write_json(meta_path, {
        "name": name,
        "bpm": bpm,
        "duration_sec": duration_sec,
        "beat_times_sec": beat_times,
        "downbeat_times_sec": downbeat_times,
        "license": "CC0-1.0-Generated",
    })


def generate_sfx_all() -> None:
    """合成 5 种通用 CC0 基础兜底音效（主流程优先使用 vibe_sound 真实物理材质 CC0 SFX）。"""
    rng = np.random.default_rng(101)

    n_w = int(0.35 * SAMPLE_RATE)
    t_w = np.arange(n_w, dtype=np.float64) / SAMPLE_RATE
    env_w = np.sin(np.pi * np.minimum(1.0, t_w / 0.35)) ** 1.5
    raw_w = rng.uniform(-1.0, 1.0, n_w)
    from scipy.signal import lfilter
    b_coef = np.array([0.22], dtype=np.float64)
    a_coef = np.array([1.0, -0.78], dtype=np.float64)
    w_sig = lfilter(b_coef, a_coef, raw_w) * env_w * 0.85
    write_stereo_wav(ASSETS_DIR / "sfx" / "whoosh.wav", w_sig, w_sig)

    n_d = int(0.55 * SAMPLE_RATE)
    t_d = np.arange(n_d, dtype=np.float64) / SAMPLE_RATE
    d_sig = (np.sin(2 * np.pi * 1046.5 * t_d) * 0.65 + np.sin(2 * np.pi * 2093.0 * t_d) * 0.3) * np.exp(-t_d * 7.5) * np.minimum(1.0, t_d / 0.005)
    write_stereo_wav(ASSETS_DIR / "sfx" / "ding.wav", d_sig, d_sig)

    n_p = int(0.16 * SAMPLE_RATE)
    t_p = np.arange(n_p, dtype=np.float64) / SAMPLE_RATE
    p_sig = np.sin(2 * np.pi * (320.0 + 980.0 * (t_p / 0.16)) * t_p) * np.exp(-t_p * 22.0) * np.minimum(1.0, t_p / 0.004)
    write_stereo_wav(ASSETS_DIR / "sfx" / "pop.wav", p_sig, p_sig)

    n_b = int(0.65 * SAMPLE_RATE)
    t_b = np.arange(n_b, dtype=np.float64) / SAMPLE_RATE
    b_sig = np.sin(2 * np.pi * (110.0 * np.exp(-t_b * 3.2) + 38.0) * t_b) * np.exp(-t_b * 4.5) * np.minimum(1.0, t_b / 0.006)
    write_stereo_wav(ASSETS_DIR / "sfx" / "bass_drop.wav", b_sig, b_sig)

    n_s = int(0.12 * SAMPLE_RATE)
    t_s = np.arange(n_s, dtype=np.float64) / SAMPLE_RATE
    s_sig = (rng.uniform(-1.0, 1.0, n_s) * 0.55 + np.sin(2 * np.pi * 1600.0 * t_s) * 0.35) * np.exp(-t_s * 40.0)
    write_stereo_wav(ASSETS_DIR / "sfx" / "shutter.wav", s_sig, s_sig)


def generate_luts_all() -> None:
    """使用向量化与共享 common.write_cube_lut 生成通用 3D LUT。"""
    size = 17
    grid = np.linspace(0.0, 1.0, size, dtype=np.float32)
    bb, gg, rr = np.meshgrid(grid, grid, grid, indexing="ij")
    r, g, b = rr.reshape(-1), gg.reshape(-1), bb.reshape(-1)

    def s_curve(v: np.ndarray, c: float) -> np.ndarray:
        return 0.5 + (v - 0.5) * c

    write_cube_lut(
        ASSETS_DIR / "luts" / "warm_store.cube",
        np.stack([s_curve(r * 1.05 + 0.02, 1.06), s_curve(g * 1.02 + 0.01, 1.05), s_curve(b * 0.94, 1.04)], axis=-1),
        size,
        "Warm Store",
    )
    write_cube_lut(
        ASSETS_DIR / "luts" / "clean_creator.cube",
        np.stack([s_curve(r * 1.01, 1.07), s_curve(g * 1.02 + 0.005, 1.07), s_curve(b * 1.04 + 0.01, 1.07)], axis=-1),
        size,
        "Clean Creator",
    )


def main() -> None:
    print("[assets] 正在向量化生成内置 CC0 可商用 BGM...")
    generate_bgm(
        "upbeat_store_120bpm",
        bpm=120.0,
        duration_sec=32.0,
        chord_freqs=[
            [261.63, 329.63, 392.00],
            [196.00, 246.94, 293.66],
            [220.00, 261.63, 329.63],
            [174.61, 220.00, 261.63],
        ],
    )
    generate_bgm(
        "chill_story_100bpm",
        bpm=100.0,
        duration_sec=36.0,
        chord_freqs=[
            [174.61, 220.00, 261.63, 329.63],
            [164.81, 196.00, 246.94, 293.66],
            [146.83, 174.61, 220.00, 261.63],
            [130.81, 164.81, 196.00, 246.94],
        ],
    )
    print("[assets] 正在生成内置 CC0 基础音效 SFX...")
    generate_sfx_all()
    print("[assets] 正在生成内置通用 3D LUTs...")
    generate_luts_all()
    print("[assets] 所有内置资产已就绪。")


if __name__ == "__main__":
    main()
