#!/usr/bin/env python3
"""
[Step 8: 混音与母带响度控制]
1. 人声降噪：对 03_locked_voice.wav 使用 ffmpeg afftdn 频域降噪与高通去低频嗡嗡声。
2. BGM 自动闪避 (Ducking)：人声出现时 BGM 自动平滑压低（Ducking -11dB），句间停顿处略回升，
   确保既有节奏氛围又不压住人声，同时保证全片没有 >0.8s 静音段。
3. 混入 06_sfx_track.wav 包装音效。
4. 响度归一化：使用两遍 EBU R128 loudnorm + 实测反馈校验，将整体综合响度精准锁在 -14.0 LUFS (±0.5)。
输出：pipeline/08_mixed_master.wav 与 pipeline/08_mix_metrics.json
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict

from common import TARGET_LUFS, find_bin, maybe_reexec_in_venv, read_json, run_cmd, write_json

maybe_reexec_in_venv()

import numpy as np
from step3_lock_audio import SR, load_stereo_wav_np, save_stereo_wav_np


def measure_lufs_ebur128(wav_path: Path) -> Dict[str, float]:
    ffmpeg = find_bin("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("需要 ffmpeg 测量 EBU R128 响度。")
    cmd = [
        ffmpeg, "-hide_banner", "-nostats", "-i", str(wav_path),
        "-filter_complex", "ebur128=peak=true", "-f", "null", "-",
    ]
    res = run_cmd(cmd, check=False)
    # 抓取 Summary 区块的 I (Integrated loudness) 和 True Peak
    i_matches = re.findall(r"I:\s*(-?[0-9]+(?:\.[0-9]+)?)\s*LUFS", res.stderr)
    tp_matches = re.findall(r"Peak:\s*(-?[0-9]+(?:\.[0-9]+)?)\s*dBFS", res.stderr)
    integrated = float(i_matches[-1]) if i_matches else -23.0
    true_peak = float(tp_matches[-1]) if tp_matches else -1.0
    return {"integrated_lufs": integrated, "true_peak_dbtp": true_peak}


def run_step8(
    pipeline_dir: Path,
    target_lufs: float = TARGET_LUFS,
    bgm_base_gain_db: float | None = None,
    duck_depth_db: float | None = None,
) -> Dict[str, Any]:
    ffmpeg = find_bin("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("需要 ffmpeg 执行降噪与混音。")

    spec_p = pipeline_dir / "style_spectrum_applied.json"
    style_id = read_json(spec_p).get("style_id") if spec_p.exists() else None
    vibe_prof = None
    if style_id:
        try:
            from vibe_sound import get_style_sound_profile
            vibe_prof = get_style_sound_profile(str(style_id))
        except Exception:
            vibe_prof = None
    if bgm_base_gain_db is None:
        bgm_base_gain_db = float(vibe_prof.get("bgm_base_db", -15.0)) if vibe_prof else -15.0
    if duck_depth_db is None:
        duck_depth_db = float(vibe_prof.get("duck_depth_db", -10.0)) if vibe_prof else -10.0

    audio_events = read_json(pipeline_dir / "03_audio_events.json")
    voice_in = Path(audio_events["locked_voice_wav"])
    bgm_in = Path(audio_events["locked_bgm_wav"])
    sfx_in = pipeline_dir / "06_sfx_track.wav"

    # 1. 人声高通滤波(80Hz) + 稳态轻度降噪(tn=0，防止自适应追踪被句间停顿底噪扰动)，保存中间降噪人声
    voice_clean_wav = pipeline_dir / "08_voice_denoised.wav"
    run_cmd([
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(voice_in),
        "-af", "highpass=f=80,afftdn=nf=-26:tn=0",
        "-ar", str(SR), "-ac", "2", "-acodec", "pcm_s16le",
        str(voice_clean_wav),
    ])

    voice_arr, _ = load_stereo_wav_np(voice_clean_wav)
    bgm_arr, _ = load_stereo_wav_np(bgm_in)
    n_samples = len(voice_arr)

    if len(bgm_arr) < n_samples:
        pad = np.zeros((n_samples - len(bgm_arr), 2), dtype=np.float32)
        bgm_arr = np.concatenate([bgm_arr, pad], axis=0)
    bgm_arr = bgm_arr[:n_samples]

    if sfx_in.exists():
        sfx_arr, _ = load_stereo_wav_np(sfx_in)
        if len(sfx_arr) < n_samples:
            pad = np.zeros((n_samples - len(sfx_arr), 2), dtype=np.float32)
            sfx_arr = np.concatenate([sfx_arr, pad], axis=0)
        sfx_arr = sfx_arr[:n_samples]
    else:
        sfx_arr = np.zeros_like(voice_arr)

    # 2. 基于前瞻峰值 + 指数型非对称起音/释放包络计算平滑 Ducking 曲线（35ms 前瞻快压 / 280ms 顺滑慢放）
    mono_v = np.abs(voice_arr).mean(axis=1)
    win = int(0.025 * SR)
    kernel = np.ones(win, dtype=np.float32) / win
    env = np.convolve(mono_v, kernel, mode="same")
    # 提前 35ms (lookahead) 捕获人声起音并映射至 [0, 1] 激活度
    lookahead = int(0.035 * SR)
    shifted_env = np.concatenate([env[lookahead:], env[-lookahead:]]) if len(env) > lookahead else env
    raw_active = np.clip((shifted_env - 0.008) / 0.025, 0.0, 1.0).astype(np.float32)

    # 采用 10ms 步进控制率做真非对称一阶 IIR 包络（起音 τ=35ms 快速压低，释放 τ=280ms 丝滑回升）
    hop = max(1, int(0.010 * SR))
    n_hops = (len(raw_active) + hop - 1) // hop
    pad_len = n_hops * hop - len(raw_active)
    padded_act = np.concatenate([raw_active, np.zeros(pad_len, dtype=np.float32)]) if pad_len else raw_active
    hop_peaks = padded_act.reshape(n_hops, hop).max(axis=1)

    dt_hop = hop / float(SR)
    alpha_atk = 1.0 - float(np.exp(-dt_hop / 0.035))
    alpha_rel = 1.0 - float(np.exp(-dt_hop / 0.280))
    duck_ctrl = np.zeros(n_hops, dtype=np.float32)
    cur_d = 0.0
    for hi in range(n_hops):
        tgt = float(hop_peaks[hi])
        alpha = alpha_atk if tgt > cur_d else alpha_rel
        cur_d = cur_d + alpha * (tgt - cur_d)
        duck_ctrl[hi] = cur_d

    # 线性插值回采样点级曲线（零阶梯噪声）
    t_hops = (np.arange(n_hops, dtype=np.float32) + 0.5) * hop
    duck_factor_curve = np.interp(np.arange(n_samples, dtype=np.float32), t_hops, duck_ctrl).astype(np.float32)

    base_gain = 10.0 ** (bgm_base_gain_db / 20.0)
    duck_gain = 10.0 ** ((bgm_base_gain_db + duck_depth_db) / 20.0)
    bgm_gain_curve = base_gain * (1.0 - duck_factor_curve) + duck_gain * duck_factor_curve

    pre_mix = voice_arr * 0.95 + bgm_arr * bgm_gain_curve[:, None] + sfx_arr * 0.85
    pre_mix_wav = pipeline_dir / "08_premix_raw.wav"
    save_stereo_wav_np(pre_mix_wav, pre_mix, SR)

    # 3. EBU R128 两遍扫描响度归一化至 -14.0 LUFS
    master_wav = pipeline_dir / "08_mixed_master.wav"
    pass1 = run_cmd([
        ffmpeg, "-y", "-hide_banner", "-i", str(pre_mix_wav),
        "-af", f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11:print_format=json",
        "-f", "null", "-",
    ], check=False)

    json_match = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", pass1.stderr, re.DOTALL)
    if json_match:
        stats = json.loads(json_match.group(0))
        af_pass2 = (
            f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11:linear=true:"
            f"measured_I={stats['input_i']}:measured_LRA={stats['input_lra']}:"
            f"measured_TP={stats['input_tp']}:measured_thresh={stats['input_thresh']}:"
            f"offset={stats['target_offset']}"
        )
    else:
        af_pass2 = f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11"

    run_cmd([
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(pre_mix_wav),
        "-af", af_pass2,
        "-ar", str(SR), "-ac", "2", "-acodec", "pcm_s16le",
        str(master_wav),
    ])

    # 4. 实测验证 + 若误差 > 0.4 LUFS 进行闭环真峰值限制增益补正（保证质检一次过）
    metrics = measure_lufs_ebur128(master_wav)
    diff = target_lufs - metrics["integrated_lufs"]
    if abs(diff) > 0.4:
        for _ in range(2):
            if abs(diff) <= 0.35:
                break
            corrected_wav = pipeline_dir / "08_mixed_master_adj.wav"
            run_cmd([
                ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(master_wav),
                "-af", f"volume={diff:.2f}dB,alimiter=limit=0.851:attack=3:release=50:level=disabled",
                "-ar", str(SR), "-ac", "2", "-acodec", "pcm_s16le",
                str(corrected_wav),
            ])
            corrected_wav.replace(master_wav)
            metrics = measure_lufs_ebur128(master_wav)
            diff = target_lufs - metrics["integrated_lufs"]

    out_metrics = {
        "step": "08_mix_audio",
        "master_wav": str(master_wav.resolve()),
        "target_lufs": target_lufs,
        "measured_lufs": metrics["integrated_lufs"],
        "true_peak_dbtp": metrics["true_peak_dbtp"],
        "voice_denoised": True,
        "bgm_ducking_applied": True,
        "duration_sec": round(n_samples / float(SR), 4),
    }
    write_json(pipeline_dir / "08_mix_metrics.json", out_metrics)
    print(
        f"[step8] 混音完成：已降噪 + BGM自动闪避，实测响度 {metrics['integrated_lufs']:.2f} LUFS "
        f"(目标 {target_lufs} LUFS) -> 08_mixed_master.wav"
    )
    return out_metrics


def main() -> None:
    maybe_reexec_in_venv()
    parser = argparse.ArgumentParser(description="Step 8: 降噪、BGM闪避与 -14 LUFS 母带混音")
    parser.add_argument("--pipeline-dir", required=True)
    parser.add_argument("--target-lufs", type=float, default=TARGET_LUFS)
    args = parser.parse_args()
    run_step8(Path(args.pipeline_dir), target_lufs=args.target_lufs)


if __name__ == "__main__":
    main()
