#!/usr/bin/env python3
"""
[Step 8: 广播级人声母带链 + dB域双频段闪避 + 三端同源分轨 + 一次校准 EBU R128 混音]
1. 从 01_manifest 未剪静音原声测真实静音谷值（TTS 跳过降噪），杜绝紧凑干声误触发 afftdn 水声。
2. dB 对数域平滑 + 相对人声门限的 4 阶 Butterworth (250–4000Hz) 双频段闪避；缺 scipy 时如实记 False。
3. 一次校准增益始终基于未限幅原混源 pre_mix 重算，彻底杜绝多代串联限幅与 TP 空转。
4. 同步产出 08_voice_master.wav、08_bgm_ducked.wav (供剪映/HyperFrames 同源听感) 及轻量 08_sfx_audit_window.json (供 tidy 后 #20 二次质检)。
输出：08_mixed_master.wav, 08_stem_nosfx.wav, 08_voice_master.wav, 08_bgm_ducked.wav, 08_mix_metrics.json, 08_sfx_audit_window.json
"""

from __future__ import annotations

import argparse
import importlib.util
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from common import (
    TARGET_LUFS,
    TP_MAX_DBTP,
    compute_sfx_band_windows,
    find_bin,
    load_style_applied,
    maybe_reexec_in_venv,
    measure_lufs_ebur128,
    read_json,
    run_cmd,
    write_json,
)

if __name__ == "__main__":
    maybe_reexec_in_venv()

import numpy as np
from step3_lock_audio import SR, load_stereo_wav_np, save_stereo_wav_np


LIMITER_LIMIT = 0.65  # 母带/分轨统一峰值上限（-3.74 dBFS）：留足余量抑制过采样真峰与 AAC MDCT 重建过冲
# AAC 封装代真峰抬升实测 1.0~1.1dB（2026-11-24：wav -1.6dBTP → 成片 -0.6dBTP），母带按扣掉余量的目标卡
AAC_TP_HEADROOM_DB = 1.2
_MASTER_TP_TARGET = TP_MAX_DBTP  # 真正的封口在 render 封装那次（AAC 首编时挂 alimiter），母带这里只按交付目标卡


def suppress_offmic_edge_bleed(
    voice_arr: np.ndarray,
    audio_events: Dict[str, Any],
    sr: int = SR,
) -> tuple:
    """
    抑制句首/句尾及短停顿间隙中的低响度画外杂声（如导演/摄影师轻声提示「走」「好」等）。
    判据：独立语音岛时长 <= 0.32s、峰值比本句/邻句主讲中位低 >= 10 dB、且与强主讲区被 >=40ms 谷底隔开。
    使用 15ms 余弦平滑斜坡衰减 42dB，零咔哒爆音且不伤及正常主讲。
    """
    if voice_arr.size == 0 or not isinstance(audio_events, dict):
        return voice_arr, []
    sentences = [s for s in (audio_events.get("sentences") or []) if isinstance(s, dict)]
    if not sentences:
        return voice_arr, []

    arr = voice_arr.astype(np.float32, copy=True)
    mono = arr.mean(axis=1)
    n_samples = len(mono)
    hop = max(1, int(0.010 * sr))
    win = max(hop, int(0.025 * sr))
    sq = np.convolve(mono * mono, np.ones(win, dtype=np.float32) / float(win), mode="same")
    n_frames = n_samples // hop
    if n_frames < 8:
        return arr, []
    frame_db = (10.0 * np.log10(np.maximum(sq[: n_frames * hop].reshape(n_frames, hop).mean(axis=1), 1e-12))).astype(np.float64)

    # 每句的主讲正文参考电平
    sent_refs: List[tuple] = []
    for s in sentences:
        st = max(0.0, float(s.get("start_sec") or 0.0))
        en = max(st, float(s.get("end_sec") or st))
        f0 = max(0, int(round(st / 0.010)))
        f1 = min(n_frames, int(round(en / 0.010)))
        if f1 - f0 < 10:
            sent_refs.append((st, en, -99.0))
            continue
        seg = frame_db[f0:f1]
        act = seg[seg > -38.0]
        ref_db = float(np.median(act)) if len(act) >= 5 else -99.0
        sent_refs.append((st, en, ref_db))

    suppress_spans: List[tuple] = []
    hits: List[Dict[str, Any]] = []

    def _scan_window_islands(w0_sec: float, w1_sec: float, ref_db: float, ctx_start_f: int, ctx_end_f: int, kind: str) -> None:
        if ref_db <= -38.0:
            return
        i0 = max(0, int(round(w0_sec / 0.010)))
        i1 = min(n_frames, int(round(w1_sec / 0.010)))
        if i1 - i0 < 2:
            return
        active_thresh = max(-47.0, ref_db - 24.0)
        valley_thresh = min(-41.0, ref_db - 17.0)
        main_thresh = ref_db - 6.5

        idx = i0
        while idx < i1:
            if frame_db[idx] <= active_thresh:
                idx += 1
                continue
            j = idx
            while j < i1 and frame_db[j] > active_thresh:
                j += 1
            dur_s = (j - idx) * 0.010
            peak_db = float(np.max(frame_db[idx:j]))
            # 确认不是主讲自身轻起手连音：若紧邻两侧存在强主讲帧，则之间必须有 >= 40ms 静音谷底隔离
            if dur_s <= 0.34 and (-46.5 < peak_db <= ref_db - 10.0):
                isolated = True
                # 检查右侧向主讲的连接处是否有 >= 4 帧(40ms)低谷
                right_main = next((k for k in range(j, min(ctx_end_f, j + 35)) if frame_db[k] >= main_thresh), None)
                if right_main is not None:
                    gap_val = frame_db[j:right_main]
                    if len(gap_val) < 4 or int(np.count_nonzero(gap_val <= valley_thresh)) < 4:
                        isolated = False
                # 检查左侧向主讲的连接处是否有 >= 4 帧低谷
                left_main = next((k for k in range(idx - 1, max(ctx_start_f - 1, idx - 36), -1) if frame_db[k] >= main_thresh), None)
                if left_main is not None:
                    gap_val = frame_db[left_main + 1:idx]
                    if len(gap_val) < 4 or int(np.count_nonzero(gap_val <= valley_thresh)) < 4:
                        isolated = False
                if isolated:
                    s_t = round(idx * 0.010, 3)
                    e_t = round(j * 0.010, 3)
                    suppress_spans.append((s_t, e_t))
                    hits.append({
                        "start_sec": s_t,
                        "end_sec": e_t,
                        "peak_db": round(peak_db, 1),
                        "ref_speech_db": round(ref_db, 1),
                        "drop_below_speech_db": round(ref_db - peak_db, 1),
                        "kind": kind,
                    })
            idx = j

    for idx_s, (st, en, ref_db) in enumerate(sent_refs):
        if ref_db <= -38.0:
            continue
        f_ctx0 = max(0, int(round((st - 0.12) / 0.010)))
        f_ctx1 = min(n_frames, int(round((en + 0.12) / 0.010)))
        # 1) 句首与句尾边缘 0.42s 内的孤立轻声画外杂音
        _scan_window_islands(max(0.0, st - 0.08), min(en, st + 0.42), ref_db, f_ctx0, f_ctx1, "sentence_head_bleed")
        _scan_window_islands(max(st, en - 0.42), en + 0.08, ref_db, f_ctx0, f_ctx1, "sentence_tail_bleed")
        # 2) 相邻句间短停顿空隙内的孤立画外词
        if idx_s > 0:
            prev_st, prev_en, prev_ref = sent_refs[idx_s - 1]
            gap = st - prev_en
            if 0.05 <= gap <= 0.70:
                use_ref = max(ref_db, prev_ref)
                _scan_window_islands(
                    prev_en - 0.03,
                    st + 0.03,
                    use_ref,
                    max(0, int(round(prev_st / 0.010))),
                    f_ctx1,
                    "inter_sentence_bleed",
                )

    # 去重合并时间窗并平滑压低 -42 dB（零硬截断爆音）
    merged: List[List[float]] = []
    for s0, e0 in sorted(suppress_spans):
        if merged and s0 <= merged[-1][1] + 0.03:
            merged[-1][1] = max(merged[-1][1], e0)
        else:
            merged.append([s0, e0])

    fade_n = max(1, int(0.015 * sr))
    floor_gain = 0.008  # -42 dB
    for s0, e0 in merged:
        a = max(0, int(round((s0 - 0.025) * sr)))
        b = min(n_samples, int(round((e0 + 0.025) * sr)))
        if b - a < fade_n * 2 + 4:
            continue
        env_g = np.full(b - a, floor_gain, dtype=np.float32)
        t_fade = np.linspace(0.0, math.pi, fade_n, dtype=np.float32)
        fade_down = floor_gain + (1.0 - floor_gain) * 0.5 * (1.0 + np.cos(t_fade))
        fade_up = floor_gain + (1.0 - floor_gain) * 0.5 * (1.0 - np.cos(t_fade))
        env_g[:fade_n] = fade_down
        env_g[-fade_n:] = fade_up
        arr[a:b] *= env_g[:, None]

    # 去重 hits
    uniq_hits: List[Dict[str, Any]] = []
    seen_t: set = set()
    for h in sorted(hits, key=lambda x: x["start_sec"]):
        k = round(h["start_sec"], 2)
        if any(abs(k - sk) <= 0.05 for sk in seen_t):
            continue
        seen_t.add(k)
        uniq_hits.append(h)
    return arr, uniq_hits


def _normalize_bgm_ref_level(
    bgm_arr: np.ndarray,
    target_rms_db: float = -14.5,
    floor_trigger_db: float = -16.0,
) -> tuple:
    """将偏安静的源片提取 BGM（如 -24dB 抒情前奏）参考归一至 -14.5dB 并软限幅护峰。"""
    if bgm_arr.size == 0:
        return bgm_arr, 0.0
    nz = np.abs(bgm_arr) > 0.002
    if int(np.count_nonzero(nz)) < 400:
        return bgm_arr, 0.0
    act_rms = float(np.sqrt(np.mean(bgm_arr[nz].astype(np.float64) ** 2)))
    act_rms_db = 20.0 * math.log10(max(1e-6, act_rms))
    if act_rms_db >= floor_trigger_db or act_rms_db <= -42.0:
        return bgm_arr, 0.0
    gain_db = min(14.0, max(0.0, target_rms_db - act_rms_db))
    if gain_db < 0.25:
        return bgm_arr, 0.0
    boosted = bgm_arr.astype(np.float32) * float(10.0 ** (gain_db / 20.0))
    ceil_lin, knee = 0.891, 0.65  # -1.0 dBFS 软饱和峰限幅，防止瞬态鼓点削波
    abs_x = np.abs(boosted)
    over = abs_x > knee
    if np.any(over):
        span = max(1e-5, ceil_lin - knee)
        soft = np.sign(boosted) * (knee + span * np.tanh((abs_x - knee) / span))
        normed = np.where(over, soft, boosted).astype(np.float32)
    else:
        normed = boosted.astype(np.float32)
    return normed, round(gain_db, 2)


def _estimate_raw_noise_floor_db(pipeline_dir: Path, fallback_voice: np.ndarray) -> float:
    """
    优先从 01_manifest 原始长音轨测未裁切环境谷值底噪（p8），防止在已去静音的 03_locked_voice 上测到轻声词尾。
    """
    man_p = pipeline_dir / "01_manifest.json"
    sample_arr = fallback_voice
    if man_p.exists():
        try:
            man = read_json(man_p)
            ar_wav = next((it.get("normalized_wav") for it in man.get("items", []) if it.get("role") == "a_roll" and it.get("normalized_wav")), None)
            if ar_wav and Path(ar_wav).is_file():
                sample_arr, _ = load_stereo_wav_np(Path(ar_wav))
        except Exception:
            pass

    hop = int(0.02 * SR)
    usable_len = (len(sample_arr) // hop) * hop
    if usable_len < hop:
        return -70.0
    n_hops = usable_len // hop
    mono = sample_arr[:usable_len].mean(axis=1).reshape(n_hops, hop)
    rms = np.sqrt(np.mean(mono ** 2, axis=1) + 1e-12)
    nonzero = rms[rms > 2e-6]
    if len(nonzero) == 0:
        return -70.0
    p8 = float(np.percentile(nonzero, 8))
    return 20.0 * math.log10(max(1e-6, p8))


def split_bgm_dual_band(bgm_arr: np.ndarray, sr: int = SR) -> tuple[np.ndarray, np.ndarray, bool]:
    """零相位拆分 BGM 为中频冲突带 (250Hz-4kHz) 与外围低/高频带，并如实返回是否生效。"""
    if len(bgm_arr) <= 128 or importlib.util.find_spec("scipy") is None:
        return bgm_arr.copy(), np.zeros_like(bgm_arr), False
    try:
        from scipy.signal import butter, sosfiltfilt
        sos = butter(4, [250.0, 4000.0], btype="bandpass", fs=sr, output="sos")
        bgm_mid = sosfiltfilt(sos, bgm_arr, axis=0).astype(np.float32)
        bgm_outer = (bgm_arr - bgm_mid).astype(np.float32)
        return bgm_mid, bgm_outer, True
    except Exception:
        return bgm_arr.copy(), np.zeros_like(bgm_arr), False


def compute_mono_phase_correlation(audio: np.ndarray) -> float:
    if len(audio) < 10:
        return 1.0
    l_ch = audio[:, 0].astype(np.float64)
    r_ch = audio[:, 1].astype(np.float64)
    denom = float(np.linalg.norm(l_ch) * np.linalg.norm(r_ch))
    if denom < 1e-10:
        return 1.0
    return round(float(np.dot(l_ch, r_ch) / denom), 4)


def apply_music_cues_envelope(
    bgm: np.ndarray, music_cues: List[Dict[str, Any]], sr: int = SR, *, return_metrics: bool = False,
) -> np.ndarray | tuple[np.ndarray, Dict[str, Any]]:
    """Apply sample-accurate raised-cosine ramps around each cue window to every channel."""
    length = len(bgm)
    attenuation = np.zeros(length, dtype=np.float32)
    swell = np.zeros(length, dtype=np.float32)
    applied = []
    for cue in music_cues:
        if not isinstance(cue, dict):
            continue
        kind = cue.get("type")
        if kind not in ("delay_entry", "silence_drop", "section_swell"):
            continue
        default_depth = {"delay_entry": -36.0, "silence_drop": -26.0, "section_swell": 2.5}[kind]
        default_fade = 120.0 if kind == "delay_entry" else 65.0
        try:
            start, end = float(cue["start_sec"]), float(cue["end_sec"])
            depth = float(cue.get("depth_db", default_depth))
            fade_ms = float(cue.get("fade_ms", default_fade))
        except (KeyError, TypeError, ValueError):
            continue
        if not all(math.isfinite(value) for value in (start, end, depth, fade_ms)):
            continue
        if kind == "silence_drop":
            depth, fade_ms = max(-60.0, min(-18.0, depth)), max(50.0, min(80.0, fade_ms))
        elif kind == "delay_entry":
            depth, fade_ms = max(-60.0, min(-36.0, depth)), max(50.0, min(300.0, fade_ms))
        else:
            depth, fade_ms = max(2.0, min(3.0, depth)), max(120.0, min(450.0, fade_ms))

        if not 0 <= start < end or start >= length / sr:
            continue
        start_index = round(start * sr)
        end_index = round(min(end, length / sr) * sr)
        if end_index <= start_index:
            continue
        fade = max(1, round(fade_ms * sr / 1000.0))
        window_start, window_end = max(0, start_index - fade), min(length, end_index + fade)
        if not np.any(np.abs(bgm[window_start:window_end]) > 1e-6):
            continue
        samples = np.arange(window_start, window_end, dtype=np.int64)
        distance = np.maximum(start_index - samples, samples - end_index)
        phase = 1.0 - np.clip(distance / fade, 0.0, 1.0)
        delta = ((0.5 - 0.5 * np.cos(np.pi * phase)) * depth).astype(np.float32)
        curve = attenuation if depth < 0.0 else swell
        combine = np.minimum if depth < 0.0 else np.maximum
        combine(curve[window_start:window_end], delta, out=curve[window_start:window_end])
        applied.append({"type": kind, "start_sec": start_index / sr, "end_sec": end_index / sr,
                        "depth_db": depth, "fade_ms": fade_ms})

    # Attenuation smoothly overrides any swell beneath it, preserving the full drop in the plateau.
    gain_db = attenuation + swell * np.maximum(0.0, 1.0 + attenuation / 18.0)
    gain = np.power(10.0, gain_db / 20.0)
    if bgm.ndim > 1:
        gain = gain.reshape((length,) + (1,) * (bgm.ndim - 1))
    out = bgm * gain
    if return_metrics:
        return out, {
            "applied_count": len(applied),
            "min_gain_db": round(min(0.0, float(np.min(gain_db))) if length else 0.0, 2),
            "applied_cues": applied,
        }
    return out


def _planned_music_cues(
    pipeline_dir: Path, style_id: Optional[str], packaging: Dict[str, Any],
) -> List[Dict[str, Any]]:
    effective_style = style_id or packaging.get("style_id")
    if effective_style not in (None, "style_02"):
        return []
    stage = packaging.get("kinetic_stage") or {}
    if stage.get("director_source") and isinstance(stage.get("music_cues"), list):
        return stage["music_cues"]

    try:
        plan = read_json(pipeline_dir / "03b_director_plan.json")
    except (OSError, ValueError):
        return []
    if not isinstance(plan, dict) or plan.get("style_id") not in (None, "style_02"):
        return []
    cues = plan.get("music_cues")
    return cues if isinstance(cues, list) else []


def run_step8(
    pipeline_dir: Path,
    target_lufs: float = TARGET_LUFS,
    bgm_base_gain_db: Optional[float] = None,
    duck_depth_db: Optional[float] = None,
    licenses_path: Optional[Path] = None,
    out_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    ffmpeg = find_bin("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("需要 ffmpeg 执行广播级人声处理与混音。")

    style_id = load_style_applied(pipeline_dir)
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
        duck_depth_db = float(vibe_prof.get("duck_depth_db", -11.0)) if vibe_prof else -11.0

    tr_p = pipeline_dir / "02_transcript.json"
    is_tts = (read_json(tr_p).get("source_type") == "tts") if tr_p.exists() else False
    audio_events = read_json(pipeline_dir / "03_audio_events.json")
    voice_in = Path(audio_events["locked_voice_wav"])
    bgm_in = Path(audio_events["locked_bgm_wav"])
    sfx_in = pipeline_dir / "06_sfx_track.wav"

    raw_v_arr, _ = load_stereo_wav_np(voice_in)
    noise_floor_db = _estimate_raw_noise_floor_db(pipeline_dir, raw_v_arr)
    # 按输入人声活性电平动态确定压缩阈值，避免小声片段压缩不触发或大声过压
    act_samples = raw_v_arr[np.abs(raw_v_arr) > 0.005]
    if len(act_samples) > 100:
        act_rms_db = 20.0 * math.log10(max(1e-5, float(np.sqrt(np.mean(act_samples.astype(np.float64) ** 2)))))
        comp_thresh_db = round(max(-28.0, min(-12.0, act_rms_db - 2.0)), 1)
    else:
        comp_thresh_db = -18.0

    # 仅真实采得有效低静音噪底 (-55dB <= nf <= -28dB) 才启轻量降噪；高于 -28dB 说明无静音采样、绝不误杀持续信号
    use_denoise = (not is_tts) and (-55.0 <= noise_floor_db <= -28.0)
    afftdn_nf = max(-55.0, min(-38.0, noise_floor_db - 4.0))
    denoise_stage = f",afftdn=nf={afftdn_nf:.1f}:tn=0" if use_denoise else ""
    vocal_chain = (
        f"highpass=f=80{denoise_stage},"
        "equalizer=f=240:t=q:w=1.2:g=-2.5,"
        "equalizer=f=3400:t=q:w=1.4:g=2.2,"
        "deesser=i=0.35:m=0.5:f=0.5:s=o,"
        f"acompressor=threshold={comp_thresh_db:.1f}dB:ratio=2.0:attack=8:release=140:makeup=1.5"
    )

    voice_clean_wav = pipeline_dir / "08_voice_denoised.wav"
    run_cmd([
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(voice_in),
        "-af", vocal_chain,
        "-ar", str(SR), "-ac", "2", "-acodec", "pcm_s16le",
        str(voice_clean_wav),
    ])

    voice_arr, _ = load_stereo_wav_np(voice_clean_wav)
    offmic_bleed_hits: List[Dict[str, Any]] = []
    if not is_tts:
        voice_arr, offmic_bleed_hits = suppress_offmic_edge_bleed(voice_arr, audio_events, sr=SR)
        if offmic_bleed_hits:
            print(
                f"[step8] 画外低响度杂声净化：{len(offmic_bleed_hits)} 处 ("
                + ", ".join(f"{h['start_sec']:.2f}s[{h['peak_db']:.0f}dB]" for h in offmic_bleed_hits[:4])
                + ")"
            )
    bgm_arr, _ = load_stereo_wav_np(bgm_in)
    n_samples = len(voice_arr)

    if len(bgm_arr) < n_samples:
        bgm_arr = np.concatenate([bgm_arr, np.zeros((n_samples - len(bgm_arr), 2), dtype=np.float32)], axis=0)
    bgm_arr = bgm_arr[:n_samples]
    bgm_arr, bgm_ref_norm_gain_db = _normalize_bgm_ref_level(bgm_arr)

    if sfx_in.exists():
        sfx_arr, _ = load_stereo_wav_np(sfx_in)
        if len(sfx_arr) < n_samples:
            sfx_arr = np.concatenate([sfx_arr, np.zeros((n_samples - len(sfx_arr), 2), dtype=np.float32)], axis=0)
        sfx_arr = sfx_arr[:n_samples]
    else:
        sfx_arr = np.zeros_like(voice_arr)

    # 2. 自适应相对人声门限 + 前瞻包络 + dB 对数域平滑闪避
    mono_v = np.abs(voice_arr).mean(axis=1)
    win = int(0.025 * SR)
    env = np.convolve(mono_v, np.ones(win, dtype=np.float32) / win, mode="same")
    nz_env = env[env > 0.002]
    speech_ref = float(np.percentile(nz_env, 75)) if len(nz_env) > 20 else 0.05
    gate_low = max(0.002, speech_ref * 0.14)
    gate_span = max(0.006, speech_ref * 0.45)

    lookahead = int(0.035 * SR)
    shifted_env = np.concatenate([env[lookahead:], env[-lookahead:]]) if len(env) > lookahead else env
    raw_active = np.clip((shifted_env - gate_low) / gate_span, 0.0, 1.0).astype(np.float32)

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

    t_hops = (np.arange(n_hops, dtype=np.float32) + 0.5) * hop
    duck_factor = np.interp(np.arange(n_samples, dtype=np.float32), t_hops, duck_ctrl).astype(np.float32)

    pkg_p = pipeline_dir / "06_packaging.json"
    packaging = read_json(pkg_p) if pkg_p.is_file() else {}
    music_cues = _planned_music_cues(pipeline_dir, style_id, packaging)
    bgm_mid, bgm_outer, dual_band_ok = split_bgm_dual_band(bgm_arr, SR)
    mid_gain_db = bgm_base_gain_db + duck_depth_db * duck_factor
    outer_gain_db = bgm_base_gain_db + (duck_depth_db * 0.32) * duck_factor
    mid_lin = np.power(10.0, mid_gain_db / 20.0).astype(np.float32)
    outer_lin = np.power(10.0, outer_gain_db / 20.0).astype(np.float32)
    ducked_bgm = bgm_mid * mid_lin[:, None] + bgm_outer * outer_lin[:, None]
    music_cue_metrics = {"applied_count": 0, "min_gain_db": 0.0, "applied_cues": []}
    if music_cues:
        ducked_bgm, music_cue_metrics = apply_music_cues_envelope(ducked_bgm, music_cues, SR, return_metrics=True)

    act_mask = duck_factor > 0.35
    if np.any(act_mask):
        v_rms = float(np.sqrt(np.mean((voice_arr[act_mask] * 0.95) ** 2) + 1e-12))
        b_rms = float(np.sqrt(np.mean(ducked_bgm[act_mask] ** 2) + 1e-12))
        voice_bgm_snr_db = round(20.0 * math.log10(max(1e-6, v_rms) / max(1e-6, b_rms)), 2)
    else:
        voice_bgm_snr_db = 20.0

    pre_mix_nosfx = voice_arr * 0.95 + ducked_bgm

    # B-roll 现场动作同期声轻混（彻底修复 step1 -an 导致旧版抽 media_path 100% 失败的死链：优先从 normalized_wav / ambient_audio_path 抽取）
    broll_ambient_arr = np.zeros_like(voice_arr)
    broll_ambient_mixed_count = 0
    edl_p = pipeline_dir / "04_edl.json"
    man_p2 = pipeline_dir / "01_manifest.json"
    wav_by_mp4: Dict[str, str] = {}
    dur_by_mp4: Dict[str, float] = {}
    if man_p2.is_file():
        for mit in read_json(man_p2).get("items") or []:
            mp_k = str(mit.get("normalized_mp4") or "")
            w_cand = mit.get("normalized_wav") or (mit.get("source_path") if mit.get("has_audio") else None)
            if mp_k and w_cand:
                wav_by_mp4[mp_k] = str(w_cand)
            if mp_k and mit.get("duration_sec"):
                dur_by_mp4[mp_k] = float(mit["duration_sec"])
    if edl_p.is_file():
        try:
            import tempfile
            edl_clips = read_json(edl_p).get("clips") or []
            amb_clips = [c for c in edl_clips if c.get("keep_broll_ambient")]
            if amb_clips:
                with tempfile.TemporaryDirectory(prefix="svs_amb_") as amb_td:
                    for ai, ac in enumerate(amb_clips):
                        mp_path_str = str(ac.get("media_path") or "")
                        aud_src = str(ac.get("ambient_audio_path") or wav_by_mp4.get(mp_path_str) or "")
                        if not aud_src or not Path(aud_src).is_file():
                            continue
                        t0 = max(0.0, float(ac.get("timeline_in_sec", 0.0)))
                        t1 = min(n_samples / float(SR), float(ac.get("timeline_out_sec", t0)))
                        dur_a = t1 - t0
                        src_in = max(0.0, float(ac.get("source_in_sec", 0.0)))
                        src_cap = float(ac.get("source_out_sec") or dur_by_mp4.get(mp_path_str) or 0.0)
                        if src_cap > src_in:
                            dur_a = min(dur_a, src_cap - src_in)
                        if dur_a < 0.25:
                            continue
                        amb_wav = Path(amb_td) / f"amb_{ai:02d}.wav"
                        res_a = run_cmd([
                            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                            "-ss", f"{src_in:.4f}", "-t", f"{dur_a:.4f}",
                            "-i", aud_src,
                            "-vn", "-af", "highpass=f=150,lowpass=f=7500",
                            "-ar", str(SR), "-ac", "2", "-acodec", "pcm_s16le",
                            str(amb_wav),
                        ], check=False)
                        if res_a.returncode != 0 or not amb_wav.is_file():
                            continue
                        amb_arr, _ = load_stereo_wav_np(amb_wav)
                        if len(amb_arr) < 128 or float(np.max(np.abs(amb_arr))) < 1e-4:
                            continue
                        s_i = int(round(t0 * SR))
                        e_i = min(n_samples, s_i + len(amb_arr))
                        seg_len = e_i - s_i
                        if seg_len < 128:
                            continue
                        seg_amb = amb_arr[:seg_len].copy()
                        # 有人声时限制现场声比人声低至少 11dB 防掩蔽；无口播的纯特写动作间隙按参考人声/设定增益自然放出滋啦声
                        gain_db = min(-9.5, float(ac.get("ambient_gain_db", -10.0)))
                        v_seg_rms = float(np.sqrt(np.mean((voice_arr[s_i:e_i] * 0.95) ** 2) + 1e-10))
                        a_seg_rms = float(np.sqrt(np.mean(seg_amb ** 2) + 1e-10))
                        v_ref_rms = max(v_seg_rms, 0.08) if v_seg_rms < 0.015 else v_seg_rms
                        lin_g = min(10.0 ** (gain_db / 20.0), (v_ref_rms * 0.30) / max(1e-5, a_seg_rms))
                        fade_n = min(int(0.035 * SR), seg_len // 4)
                        if fade_n > 1:
                            seg_amb[:fade_n] *= np.linspace(0.0, 1.0, fade_n, dtype=np.float32)[:, None]
                            seg_amb[-fade_n:] *= np.linspace(1.0, 0.0, fade_n, dtype=np.float32)[:, None]
                        broll_ambient_arr[s_i:e_i] += seg_amb * lin_g
                        pre_mix_nosfx[s_i:e_i] += seg_amb * lin_g
                        broll_ambient_mixed_count += 1
        except Exception:
            pass

    # 局部防掩蔽校准：逐条核对 06_packaging SFX 峰值窗口 [t-0.04, t+0.16] 的 2–8kHz 可听度（不加噪、统一写入 pre_mix 与 08_sfx_master）
    cues_early = packaging.get("sfx_cues", [])
    if cues_early and float(np.max(np.abs(sfx_arr))) > 1e-6:
        try:
            from scipy import signal as sp_sig
            from common import SFX_AUDIT_POST_SEC, SFX_AUDIT_PRE_SEC

            sos_hf = sp_sig.butter(4, [2000.0, 8000.0], btype="bandpass", fs=float(SR), output="sos")
            nosfx_hf = sp_sig.sosfiltfilt(sos_hf, pre_mix_nosfx.mean(axis=1).astype(np.float64))
            sfx_hf = sp_sig.sosfiltfilt(sos_hf, (sfx_arr * 0.95).mean(axis=1).astype(np.float64))
            fade_n = int(0.020 * SR)
            for cue in cues_early:
                pk_t = float(cue.get("peak_time_sec", cue.get("time_sec", 0.0)))
                on_t = float(cue.get("onset_time_sec", max(0.0, pk_t - 0.04)))
                ws_i = max(0, int(round((pk_t - SFX_AUDIT_PRE_SEC) * SR)))
                we_i = min(n_samples, int(round((pk_t + SFX_AUDIT_POST_SEC) * SR)))
                if we_i - ws_i < 64:
                    continue
                bg_rms = float(np.sqrt(np.mean(nosfx_hf[ws_i:we_i] ** 2) + 1e-12))
                cur_sfx_rms = float(np.sqrt(np.mean(sfx_hf[ws_i:we_i] ** 2) + 1e-12))
                if cur_sfx_rms < 1e-5:
                    continue
                # 若直接合成后的 2–8kHz 增量未显著高出人声掩蔽基底（预留 +4.2dB 压限余量），按平滑窗局部抬升
                comb_rms = float(np.sqrt(np.mean((nosfx_hf[ws_i:we_i] + sfx_hf[ws_i:we_i]) ** 2) + 1e-12))
                cur_lift_db = 20.0 * math.log10(max(1e-8, comb_rms) / max(1e-8, bg_rms))
                if cur_lift_db >= 4.2:
                    continue
                target_sfx_rms = bg_rms * (10.0 ** (4.4 / 20.0))
                need_scale = min(3.2, max(1.0, target_sfx_rms / cur_sfx_rms))
                if need_scale <= 1.05:
                    continue
                loc0 = max(0, int(round((on_t - 0.02) * SR)))
                loc1 = min(n_samples, int(round((on_t + 0.45) * SR)))
                if loc1 <= loc0 + 16:
                    continue
                # 对低频峰值已接近上限但 2–8kHz 仍偏暗的音效（如敲锣），优先抬 2–8kHz 泛音并对瞬态波峰做软削峰保护，
                # 避免盲目拉大低频把 alimiter 压限触发，反把人声高频一起压塌。
                loc_seg = sfx_arr[loc0:loc1].astype(np.float64)
                nosfx_loc_pk = float(np.max(np.abs(pre_mix_nosfx[loc0:loc1])))
                hf_boost = min(4.0, max(0.0, (need_scale - 1.0) * 1.25))
                loc_hf = np.column_stack([sp_sig.sosfiltfilt(sos_hf, loc_seg[:, ch]) for ch in range(2)])
                loc_adj = loc_seg * min(1.35, 1.0 + 0.25 * (need_scale - 1.0)) + hf_boost * loc_hf
                safe_ceil = min(0.62, max(0.44, 0.98 - nosfx_loc_pk))
                adj_pk = float(np.max(np.abs(loc_adj)))
                if adj_pk > safe_ceil:
                    loc_adj = np.tanh(loc_adj / max(1e-4, safe_ceil)) * safe_ceil
                fn = min(fade_n, (loc1 - loc0) // 4)
                if fn > 1:
                    blend = np.ones((loc1 - loc0, 1), dtype=np.float64)
                    blend[:fn, 0] = np.linspace(0.0, 1.0, fn)
                    blend[-fn:, 0] = np.linspace(1.0, 0.0, fn)
                    loc_adj = loc_seg * (1.0 - blend) + loc_adj * blend
                sfx_arr[loc0:loc1] = loc_adj.astype(np.float32)
        except Exception:
            pass

    pre_mix = pre_mix_nosfx + sfx_arr * 0.95
    pre_mix_wav = pipeline_dir / "08_premix_raw.wav"
    pre_mix_nosfx_wav = pipeline_dir / "08_premix_nosfx_raw.wav"
    save_stereo_wav_np(pre_mix_wav, pre_mix, SR)
    save_stereo_wav_np(pre_mix_nosfx_wav, pre_mix_nosfx, SR)

    # 3. 单源叠加迭代增益校准：保证最终磁盘 WAV 与 final_gain_db 100% 锁定一致
    master_wav = pipeline_dir / "08_mixed_master.wav"
    stem_nosfx_wav = pipeline_dir / "08_stem_nosfx.wav"
    init_metrics = measure_lufs_ebur128(pre_mix_wav)
    if init_metrics["integrated_lufs"] <= -65.0:
        cur_gain_db = 0.0
    else:
        cur_gain_db = max(-24.0, min(24.0, target_lufs - init_metrics["integrated_lufs"]))

    def _render_master_pair(gain_db: float) -> Dict[str, float]:
        g_clamped = max(-24.0, min(24.0, gain_db))
        for wav_src, wav_dst in ((pre_mix_wav, master_wav), (pre_mix_nosfx_wav, stem_nosfx_wav)):
            run_cmd([
                ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(wav_src),
                "-af", f"volume={g_clamped:.2f}dB,aresample=192000,lowpass=f=18000,alimiter=limit={LIMITER_LIMIT:.3f}:attack=1.5:release=40:level=disabled:latency=true,aresample=48000",
                "-ar", str(SR), "-ac", "2", "-acodec", "pcm_s16le",
                str(wav_dst),
            ])
        return measure_lufs_ebur128(master_wav)

    metrics = _render_master_pair(cur_gain_db)
    if init_metrics["integrated_lufs"] > -65.0:
        for _attempt in range(4):
            err = target_lufs - metrics["integrated_lufs"]
            # 旧循环要求 true peak ≤ -1.5 dBTP，而 alimiter 只压样本峰 0.794(-2.0dB)，
            # 过采样读出来的真峰恒在 -0.8 ~ -1.4 之间 → 每一轮都判「峰值超标」只降不升，
            # 响度一路掉到 -16.5 / -17.4 LUFS（2026-11-24 两次实测）。峰值交给 limiter，
            # 响度这一侧只在真峰越过 -0.5 dBTP 时才让路。
            # AAC 封装会把真峰再抬 ~1.0~1.1dB（实测 wav -1.6 → mp4 -0.6，再编一次 +0.5），
            # 所以母带要按 TP 目标 - AAC_TP_HEADROOM_DB 来卡，交付文件才不会在质检那侧超线。
            if abs(err) <= 0.35 and metrics["true_peak_dbtp"] <= _MASTER_TP_TARGET:
                break
            if metrics["true_peak_dbtp"] > _MASTER_TP_TARGET:
                cur_gain_db -= max(0.3, metrics["true_peak_dbtp"] - _MASTER_TP_TARGET)
            else:
                cur_gain_db += err
            cur_gain_db = max(-24.0, min(24.0, cur_gain_db))
            metrics = _render_master_pair(cur_gain_db)

    # 同步导出三轨分离分轨 (人声规范母带 + 已闪避定电平 BGM含现场动作声 + 定电平 SFX)
    # 统一采用与母带 alimiter 一致的 LIMITER_LIMIT 峰值上限，保证三轨求和真峰值不越线
    final_scale = 10.0 ** (cur_gain_db / 20.0)
    v_stem_raw = voice_arr * 0.95 * final_scale
    b_stem_raw = (ducked_bgm + broll_ambient_arr) * final_scale
    s_stem_raw = sfx_arr * 0.95 * final_scale
    bus_raw = v_stem_raw + b_stem_raw + s_stem_raw
    if n_samples > 0:
        win_n = max(64, int(0.020 * SR))
        n_blocks = max(1, (n_samples + win_n - 1) // win_n)
        pad_n = n_blocks * win_n - n_samples
        abs_bus = np.max(np.abs(bus_raw), axis=1)
        if pad_n > 0:
            abs_bus = np.pad(abs_bus, (0, pad_n), mode="constant")
        blk_peaks = np.max(abs_bus.reshape(n_blocks, win_n), axis=1)
        blk_gains = np.minimum(1.0, LIMITER_LIMIT / np.maximum(1e-5, blk_peaks)).astype(np.float32)
        if n_blocks >= 3:
            # 前看后看平滑避免瞬态陡变
            blk_gains = np.minimum(blk_gains, np.roll(blk_gains, -1))
            blk_gains = np.convolve(np.pad(blk_gains, (1, 1), mode="edge"), np.array([0.25, 0.5, 0.25], dtype=np.float32), mode="valid")
        centers = (np.arange(n_blocks, dtype=np.float64) + 0.5) * win_n
        env_gain = np.interp(np.arange(n_samples, dtype=np.float64), centers, blk_gains).astype(np.float32)[:, None]
        post_peak = float(np.max(np.abs(bus_raw * env_gain)))
        hard_cap = min(1.0, 0.84 / max(1e-5, post_peak))
        stem_gain_curve = env_gain * hard_cap
        bus_safe_scale = float(np.mean(stem_gain_curve))
    else:
        stem_gain_curve = np.ones((0, 1), dtype=np.float32)
        bus_safe_scale = 1.0
    voice_master_path = pipeline_dir / "08_voice_master.wav"
    bgm_ducked_path = pipeline_dir / "08_bgm_ducked.wav"
    sfx_master_path = pipeline_dir / "08_sfx_master.wav"
    save_stereo_wav_np(voice_master_path, v_stem_raw * stem_gain_curve, SR)
    save_stereo_wav_np(bgm_ducked_path, b_stem_raw * stem_gain_curve, SR)
    save_stereo_wav_np(sfx_master_path, s_stem_raw * stem_gain_curve, SR)

    # 使用 common 唯一权威函数 compute_sfx_band_windows 持久化全量 SFX 基准，供 #20 复验
    cues = cues_early
    scaled_nosfx, _ = load_stereo_wav_np(stem_nosfx_wav)
    sfx_windows = compute_sfx_band_windows(cues, scaled_nosfx.mean(axis=1).astype(np.float64), SR)
    write_json(pipeline_dir / "08_sfx_audit_window.json", {
        "step": "08_sfx_audit_window",
        "master_gain_db": round(cur_gain_db, 2),
        "windows": sfx_windows,
    })

    pre_mix_nosfx_wav.unlink(missing_ok=True)
    pre_mix_wav.unlink(missing_ok=True)
    voice_clean_wav.unlink(missing_ok=True)

    master_arr, _ = load_stereo_wav_np(master_wav)
    mono_phase_corr = compute_mono_phase_correlation(master_arr)

    out_metrics = {
        "step": "08_mix_audio",
        "master_wav": str(master_wav.resolve()),
        "stem_nosfx_wav": str(stem_nosfx_wav.resolve()),
        "voice_master_wav": str(voice_master_path.resolve()),
        "bgm_ducked_wav": str(bgm_ducked_path.resolve()),
        "sfx_master_wav": str(sfx_master_path.resolve()),
        "target_lufs": target_lufs,
        "master_gain_db": round(cur_gain_db, 2),
        "measured_lufs": metrics["integrated_lufs"],
        "lra_lu": metrics["lra_lu"],
        "true_peak_dbtp": metrics["true_peak_dbtp"],
        "voice_bgm_snr_db": voice_bgm_snr_db,
        "mono_phase_corr": mono_phase_corr,
        "noise_floor_db": round(noise_floor_db, 1),
        "vocal_comp_threshold_db": comp_thresh_db,
        "vocal_broadcast_chain": True,
        "dual_band_ducking": dual_band_ok,
        "voice_denoised": bool(use_denoise),
        "bgm_ducking_applied": True,
        "bgm_ref_norm_gain_db": bgm_ref_norm_gain_db,
        "music_cue_metrics": music_cue_metrics,
        "broll_ambient_mixed_count": broll_ambient_mixed_count,
        "offmic_bleed_suppressed_count": len(offmic_bleed_hits),
        "offmic_bleed_hits": offmic_bleed_hits,
        "stem_bus_safe_scale": round(bus_safe_scale, 4),
        "duration_sec": round(n_samples / float(SR), 4),
    }
    write_json(pipeline_dir / "08_mix_metrics.json", out_metrics)

    from compliance_guard import build_asset_license_manifest
    build_asset_license_manifest(pipeline_dir, out_dir=out_dir, licenses_path=licenses_path)

    print(
        f"[step8] 广播级混音完成：响度 {metrics['integrated_lufs']:.2f} LUFS | LRA {metrics['lra_lu']:.1f} LU | "
        f"SNR {voice_bgm_snr_db:.1f}dB | 单声道相位 {mono_phase_corr:.2f} -> 08_mixed_master.wav"
    )
    return out_metrics


def main() -> None:
    maybe_reexec_in_venv()
    parser = argparse.ArgumentParser(description="Step 8: 广播级人声母带、双频段闪避与 -14 LUFS 混音")
    parser.add_argument("--pipeline-dir", required=True)
    parser.add_argument("--target-lufs", type=float, default=TARGET_LUFS)
    parser.add_argument("--licenses-json", help="可选：外部商用授权登记 licenses.json")
    parser.add_argument("--out-dir", help="可选：同步写出授权清单的 out 目录")
    args = parser.parse_args()
    run_step8(
        Path(args.pipeline_dir),
        target_lufs=args.target_lufs,
        licenses_path=Path(args.licenses_json) if args.licenses_json else None,
        out_dir=Path(args.out_dir) if args.out_dir else None,
    )


if __name__ == "__main__":
    main()
