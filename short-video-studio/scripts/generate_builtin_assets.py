#!/usr/bin/env python3
"""
生成 100% 原创 CC0 可商用的内置资产：
1. BGM (.wav + 节拍元数据 .json): upbeat_store_120bpm, chill_story_100bpm
2. SFX (.wav): whoosh, ding, pop, bass_drop, shutter
3. 3D LUTs (.cube): warm_store, clean_creator, high_contrast
4. 矢量贴纸 (.svg): arrow_highlight, badge_must_see, check_mark, warning_tag
仅使用 Python 标准库，确保零外部依赖即可初始化。
"""

from __future__ import annotations

import math
import random
import struct
import wave
from pathlib import Path
from typing import Callable, List

from common import ASSETS_DIR, write_json

SAMPLE_RATE = 48000


def write_stereo_wav(path: Path, samples_l: List[float], samples_r: List[float], sr: int = SAMPLE_RATE) -> None:
    """将归一化 [-1.0, 1.0] 双声道浮点采样写入 16-bit PCM WAV。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    peak = max(1e-6, max(max(abs(x) for x in samples_l), max(abs(x) for x in samples_r)))
    scale = 0.92 / max(1.0, peak)
    frames = bytearray()
    for l_val, r_val in zip(samples_l, samples_r):
        li = max(-32767, min(32767, int(l_val * scale * 32767.0)))
        ri = max(-32767, min(32767, int(r_val * scale * 32767.0)))
        frames += struct.pack("<hh", li, ri)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(bytes(frames))


def generate_bgm(name: str, bpm: float, duration_sec: float, chord_freqs: List[List[float]]) -> None:
    """程序化合成节拍清晰、听感温暖的免版税 CC0 背景音乐。"""
    total_samples = int(duration_sec * SAMPLE_RATE)
    beat_sec = 60.0 / bpm
    samples_l = [0.0] * total_samples
    samples_r = [0.0] * total_samples
    rng = random.Random(42)

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

    # 1. 温暖和弦铺底（每 4 拍换一次和弦，加入轻微立体声声场偏移）
    bar_sec = beat_sec * 4.0
    for i in range(total_samples):
        ts = i / SAMPLE_RATE
        bar_idx = int(ts // bar_sec) % len(chord_freqs)
        bar_local = (ts % bar_sec) / bar_sec
        # 柔和呼吸感包络
        pad_env = 0.28 + 0.12 * math.sin(math.pi * bar_local)
        chord = chord_freqs[bar_idx]
        val_l = 0.0
        val_r = 0.0
        for idx, freq in enumerate(chord):
            phase = 2.0 * math.pi * freq * ts
            tone = math.sin(phase) * 0.7 + math.sin(phase * 2.0) * 0.15
            pan = 0.5 + 0.25 * ((-1) ** idx)
            val_l += tone * (1.0 - pan * 0.3)
            val_r += tone * (0.7 + pan * 0.3)
        # 首尾 0.05s 微淡入淡出避免爆音
        edge_env = min(1.0, ts / 0.05, max(0.0, (duration_sec - ts) / 0.15))
        samples_l[i] = (val_l / len(chord)) * pad_env * 0.45 * edge_env
        samples_r[i] = (val_r / len(chord)) * pad_env * 0.45 * edge_env

    # 2. 叠加清晰节拍瞬态（强拍底鼓 + 次拍轻军鼓/踩镲），方便节拍检测器精准捕捉
    for b_idx, bt in enumerate(beat_times):
        start_s = int(bt * SAMPLE_RATE)
        is_down = (b_idx % 4 == 0)
        is_snare = (b_idx % 4 == 2)
        if is_down:
            # 强拍 Kick（55Hz->120Hz 俯冲 + 清晰点击音头）
            kick_len = int(0.18 * SAMPLE_RATE)
            for j in range(kick_len):
                pos = start_s + j
                if pos >= total_samples:
                    break
                dt = j / SAMPLE_RATE
                freq = 52.0 + 95.0 * math.exp(-dt * 28.0)
                env = math.exp(-dt * 18.0)
                kick = math.sin(2.0 * math.pi * freq * dt) * env * 0.65
                samples_l[pos] += kick
                samples_r[pos] += kick
        elif is_snare:
            # 轻拍 Clack/Snare
            snare_len = int(0.10 * SAMPLE_RATE)
            for j in range(snare_len):
                pos = start_s + j
                if pos >= total_samples:
                    break
                dt = j / SAMPLE_RATE
                env = math.exp(-dt * 35.0)
                noise = (rng.random() * 2.0 - 1.0) * 0.28 + math.sin(2.0 * math.pi * 190.0 * dt) * 0.22
                samples_l[pos] += noise * env
                samples_r[pos] += noise * env
        else:
            # 轻踩镲 Hi-hat
            hat_len = int(0.05 * SAMPLE_RATE)
            for j in range(hat_len):
                pos = start_s + j
                if pos >= total_samples:
                    break
                dt = j / SAMPLE_RATE
                env = math.exp(-dt * 75.0)
                hat = (rng.random() * 2.0 - 1.0) * 0.16 * env
                samples_l[pos] += hat * 0.9
                samples_r[pos] += hat * 1.1

    wav_path = ASSETS_DIR / "bgm" / f"{name}.wav"
    meta_path = ASSETS_DIR / "bgm" / f"{name}.json"
    write_stereo_wav(wav_path, samples_l, samples_r)
    write_json(meta_path, {
        "name": name,
        "bpm": bpm,
        "duration_sec": duration_sec,
        "beat_times_sec": beat_times,
        "downbeat_times_sec": downbeat_times,
        "license": "CC0-1.0-Generated",
    })


def generate_sfx_all() -> None:
    """合成 5 种常用短视频包装音效。"""
    rng = random.Random(101)

    def build_sfx(filename: str, dur: float, fn: Callable[[float, int], float]) -> None:
        n = int(dur * SAMPLE_RATE)
        l_ch = [0.0] * n
        r_ch = [0.0] * n
        for i in range(n):
            v = fn(i / SAMPLE_RATE, i)
            l_ch[i] = v
            r_ch[i] = v
        write_stereo_wav(ASSETS_DIR / "sfx" / filename, l_ch, r_ch)

    # 1. whoosh.wav: 滤波风声转场
    prev = [0.0]
    def sfx_whoosh(t: float, _: int) -> float:
        env = math.sin(math.pi * min(1.0, t / 0.35)) ** 1.5
        raw = rng.random() * 2.0 - 1.0
        alpha = 0.08 + 0.35 * math.sin(math.pi * min(1.0, t / 0.35))
        prev[0] = prev[0] * (1.0 - alpha) + raw * alpha
        return prev[0] * env * 0.85

    build_sfx("whoosh.wav", 0.35, sfx_whoosh)

    # 2. ding.wav: 清脆提示音（1046Hz C6 + 泛音）
    build_sfx(
        "ding.wav",
        0.55,
        lambda t, _: (math.sin(2 * math.pi * 1046.5 * t) * 0.65 + math.sin(2 * math.pi * 2093.0 * t) * 0.3)
        * math.exp(-t * 7.5)
        * min(1.0, t / 0.005),
    )

    # 3. pop.wav: 气泡弹窗音（频率上挑）
    build_sfx(
        "pop.wav",
        0.16,
        lambda t, _: math.sin(2 * math.pi * (320.0 + 980.0 * (t / 0.16)) * t)
        * math.exp(-t * 22.0)
        * min(1.0, t / 0.004),
    )

    # 4. bass_drop.wav: 低频重音冲击
    build_sfx(
        "bass_drop.wav",
        0.65,
        lambda t, _: math.sin(2 * math.pi * (110.0 * math.exp(-t * 3.2) + 38.0) * t)
        * math.exp(-t * 4.5)
        * min(1.0, t / 0.006),
    )

    # 5. shutter.wav: 快门定格音
    build_sfx(
        "shutter.wav",
        0.12,
        lambda t, _: ((rng.random() * 2.0 - 1.0) * 0.55 + math.sin(2 * math.pi * 1600.0 * t) * 0.35)
        * math.exp(-t * 40.0),
    )


def write_cube_lut(path: Path, title: str, transform_fn: Callable[[float, float, float], tuple[float, float, float]]) -> None:
    """生成标准 17x17x17 3D .cube LUT 文件。"""
    size = 17
    lines = [f'TITLE "{title}"', f"LUT_3D_SIZE {size}", "DOMAIN_MIN 0.0 0.0 0.0", "DOMAIN_MAX 1.0 1.0 1.0"]
    for b in range(size):
        fb = b / (size - 1)
        for g in range(size):
            fg = g / (size - 1)
            for r in range(size):
                fr = r / (size - 1)
                nr, ng, nb = transform_fn(fr, fg, fb)
                nr = max(0.0, min(1.0, nr))
                ng = max(0.0, min(1.0, ng))
                nb = max(0.0, min(1.0, nb))
                lines.append(f"{nr:.6f} {ng:.6f} {nb:.6f}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def generate_luts_all() -> None:
    """生成 3 套内置风格 LUT。"""
    def clamp_s_curve(v: float, contrast: float = 1.08) -> float:
        return 0.5 + (v - 0.5) * contrast

    # 1. 门店暖调提亮：红黄微暖、阴影提亮、高光不溢出
    write_cube_lut(
        ASSETS_DIR / "luts" / "warm_store.cube",
        "Warm Store",
        lambda r, g, b: (
            clamp_s_curve(r * 1.05 + 0.02, 1.06),
            clamp_s_curve(g * 1.02 + 0.01, 1.05),
            clamp_s_curve(b * 0.94, 1.04),
        ),
    )
    # 2. 博主清透自然：干净白平衡、轻对比、轻微冷青高光
    write_cube_lut(
        ASSETS_DIR / "luts" / "clean_creator.cube",
        "Clean Creator",
        lambda r, g, b: (
            clamp_s_curve(r * 1.01, 1.07),
            clamp_s_curve(g * 1.02 + 0.005, 1.07),
            clamp_s_curve(b * 1.04 + 0.01, 1.07),
        ),
    )
    # 3. 高反差纪实：强对比度、电影暗部压实
    write_cube_lut(
        ASSETS_DIR / "luts" / "high_contrast.cube",
        "High Contrast",
        lambda r, g, b: (
            clamp_s_curve(r, 1.18),
            clamp_s_curve(g, 1.18),
            clamp_s_curve(b * 0.98, 1.18),
        ),
    )


def generate_stickers_all() -> None:
    """生成 4 个原创可商用 SVG 贴纸。"""
    stickers = {
        "arrow_highlight.svg": (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 220 120">'
            '<path d="M15 60 L155 60 L125 25 L145 15 L205 60 L145 105 L125 95 L155 60" '
            'fill="#FFD54F" stroke="#111111" stroke-width="6" stroke-linejoin="round"/></svg>'
        ),
        "badge_must_see.svg": (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 260 90">'
            '<rect x="6" y="6" width="248" height="78" rx="18" fill="#FF3B30" stroke="#FFFFFF" stroke-width="5"/>'
            '<text x="130" y="58" text-anchor="middle" fill="#FFFFFF" font-size="38" font-weight="bold" '
            'font-family="sans-serif">重点必看</text></svg>'
        ),
        "check_mark.svg": (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120">'
            '<circle cx="60" cy="60" r="52" fill="#22C55E" stroke="#FFFFFF" stroke-width="6"/>'
            '<path d="M34 62 L53 80 L88 40" fill="none" stroke="#FFFFFF" stroke-width="11" '
            'stroke-linecap="round" stroke-linejoin="round"/></svg>'
        ),
        "warning_tag.svg": (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 140 120">'
            '<polygon points="70,10 130,110 10,110" fill="#F59E0B" stroke="#111111" stroke-width="6" stroke-linejoin="round"/>'
            '<text x="70" y="96" text-anchor="middle" fill="#111111" font-size="60" font-weight="bold">!</text></svg>'
        ),
    }
    s_dir = ASSETS_DIR / "stickers"
    s_dir.mkdir(parents=True, exist_ok=True)
    for name, content in stickers.items():
        (s_dir / name).write_text(content, encoding="utf-8")


def main() -> None:
    print("[assets] 正在生成内置 CC0 可商用 BGM...")
    # C大调进行 C - G - Am - F
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
    # 温暖叙事 Fmaj7 - Em7 - Dm7 - Cmaj7
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
    print("[assets] 正在生成内置 CC0 音效 SFX...")
    generate_sfx_all()
    print("[assets] 正在生成内置 3D LUTs...")
    generate_luts_all()
    print("[assets] 正在生成内置 SVG 贴纸...")
    generate_stickers_all()
    print("[assets] 所有内置资产已就绪。")


if __name__ == "__main__":
    main()
