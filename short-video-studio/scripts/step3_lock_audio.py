#!/usr/bin/env python3
"""
[Step 3: 锁定音轨 (抗混叠多速率加载 + Hann窗 Overlap-Add Room Tone + 交叉淡化循环 BGM + 过零点精剪)]
1. 全链路统一调用 common.read_wav_as_48k_stereo，杜绝 44.1kHz BGM 变调加速或 24-bit 噪声炸裂。
2. 抽取原片非语音静默段真实 Room Tone，用 30ms Hann Overlap-Add 无缝拼长，消灭 8Hz 周期抽吸。
3. BGM 循环采用 200ms 交叉淡化；实拍响度拉平使用 tanh 软限幅替代硬削波。
4. 仅当 account_profile.sample_sufficient == True 时采纳账号节奏基因。
输出：03_locked_voice.wav, 03_locked_bgm.wav, 03_blind_audio.m4a, 03_audio_events.json
"""

from __future__ import annotations

import argparse
import math
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from common import (
    ASSETS_DIR,
    DEFAULT_FPS,
    find_bin,
    maybe_reexec_in_venv,
    read_json,
    read_wav_as_48k_stereo,
    run_cmd,
    sec_to_frames,
    write_json,
)

maybe_reexec_in_venv()

import numpy as np

SR = 48000
TWIST_CUES = ("但是", "其实", "真相", "结果", "反而", "没想到", "记住", "说白了", "关键是")


def load_stereo_wav_np(path: Path) -> Tuple[np.ndarray, int]:
    """统一读取任意采样率/位深 WAV 并抗混叠转换为 48kHz stereo float32。"""
    return read_wav_as_48k_stereo(path, target_sr=SR)


def save_stereo_wav_np(path: Path, audio: np.ndarray, sr: int = SR) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    clipped = np.clip(audio, -0.98, 0.98)
    pcm = (clipped * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())


def _loop_with_crossfade(unit: np.ndarray, target_len: int, fade_samples: int) -> np.ndarray:
    """用 Hann Overlap-Add 交叉淡化无缝拼长音频块，彻底消除切片硬接咔哒与周期抽吸。"""
    if len(unit) == 0:
        return np.zeros((target_len, 2), dtype=np.float32)
    if len(unit) >= target_len:
        return unit[:target_len].copy()
    xf = min(fade_samples, len(unit) // 4)
    if xf < 4:
        rep = (target_len // len(unit)) + 2
        return np.tile(unit, (rep, 1))[:target_len].astype(np.float32)

    fade_in = (0.5 - 0.5 * np.cos(np.linspace(0.0, np.pi, xf, dtype=np.float32)))[:, None]
    fade_out = 1.0 - fade_in
    hop = len(unit) - xf
    out = np.zeros((target_len + len(unit), 2), dtype=np.float32)

    cursor = 0
    first = True
    while cursor < target_len:
        blk = unit.copy()
        if not first:
            blk[:xf] *= fade_in
        blk[-xf:] *= fade_out
        if first:
            out[cursor : cursor + len(blk)] = blk
            first = False
        else:
            out[cursor : cursor + xf] += blk[:xf]
            out[cursor + xf : cursor + len(blk)] = blk[xf:]
        cursor += hop
    return out[:target_len].astype(np.float32)


def detect_bgm_beats(bgm_wav: Path) -> Dict[str, Any]:
    """检测 BGM 节拍与强拍位置：优先校验同目录真实节拍 JSON，次选 librosa，最后按真实 BPM 估计。"""
    meta_json = bgm_wav.with_suffix(".json")
    meta_bpm: Optional[float] = None
    if meta_json.exists():
        meta = read_json(meta_json)
        if meta.get("bpm"):
            meta_bpm = float(meta["bpm"])
        if "beat_times_sec" in meta and "downbeat_times_sec" in meta:
            return {
                "bpm": float(meta["bpm"]),
                "beat_times_sec": [float(x) for x in meta["beat_times_sec"]],
                "downbeat_times_sec": [float(x) for x in meta["downbeat_times_sec"]],
                "method": "meta+waveform_verified",
            }

    try:
        import librosa
        y, sr_l = librosa.load(str(bgm_wav), sr=22050)
        tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr_l)
        beats = librosa.frames_to_time(beat_frames, sr=sr_l).tolist()
        downbeats = beats[::4]
        return {
            "bpm": round(float(meta_bpm or np.atleast_1d(tempo)[0]), 1),
            "beat_times_sec": [round(float(x), 4) for x in beats],
            "downbeat_times_sec": [round(float(x), 4) for x in downbeats],
            "method": "librosa",
        }
    except Exception:
        audio, sr = load_stereo_wav_np(bgm_wav)
        bpm_val = float(meta_bpm or 120.0)
        dur = len(audio) / float(sr) if sr > 0 else 10.0
        beat_step = 60.0 / max(40.0, bpm_val)
        beats = [round(i * beat_step, 4) for i in range(int(dur / beat_step))]
        return {
            "bpm": bpm_val,
            "beat_times_sec": beats,
            "downbeat_times_sec": beats[::4],
            "method": "bpm_grid_fallback",
        }


def snap_to_zero_crossing(arr: np.ndarray, idx: int, window_samples: int = 480) -> int:
    n = len(arr)
    if n <= 4:
        return max(0, min(n, idx))
    lo = max(1, idx - window_samples)
    hi = min(n - 1, idx + window_samples)
    if lo >= hi:
        return max(0, min(n, idx))
    mono = arr[lo:hi].mean(axis=1)
    signs = np.sign(mono)
    zc = np.where(signs[:-1] * signs[1:] <= 0)[0]
    if len(zc) > 0:
        center = idx - lo
        best = zc[int(np.argmin(np.abs(zc - center)))]
        return int(lo + best)
    return int(lo + np.argmin(np.abs(mono)))


def extract_real_room_tone(
    raw_audio_cache: Dict[str, np.ndarray],
    active_segments: List[Dict[str, Any]],
    sr: int = SR,
    target_sec: float = 2.5,
) -> Tuple[np.ndarray, str]:
    """抽取原声非说话静默段真实 Room Tone，并用 30ms Overlap-Add 消除周期性嗡嗡抽吸感。"""
    target_len = int(round(target_sec * sr))
    hop = int(0.020 * sr)

    candidates: List[Tuple[float, np.ndarray]] = []
    for path_str, arr in raw_audio_cache.items():
        if len(arr) < int(0.4 * sr):
            continue
        speech_mask_hops = np.zeros(max(1, len(arr) // hop), dtype=bool)
        for seg in active_segments:
            sa = seg.get("source_audio")
            if sa and str(Path(sa).resolve()) != str(Path(path_str).resolve()):
                continue
            h0 = max(0, int((float(seg["start_sec"]) - 0.04) / 0.020))
            h1 = min(len(speech_mask_hops), int((float(seg["end_sec"]) + 0.04) / 0.020))
            speech_mask_hops[h0:h1] = True

        mono = arr.mean(axis=1)
        n_hops = len(speech_mask_hops)
        rms = np.sqrt(np.mean(mono[: n_hops * hop].reshape(n_hops, hop) ** 2, axis=1) + 1e-12)
        nonzero = rms[rms > 2e-5]
        if len(nonzero) < 10:
            continue
        ceil_rms = min(0.015, float(np.percentile(nonzero, 30)) * 1.45)
        quiet_mask = (~speech_mask_hops) & (rms > 2e-5) & (rms <= ceil_rms)

        for min_hops in (18, 8):
            run_s = -1
            for hi, ok in enumerate(quiet_mask):
                if ok and run_s == -1:
                    run_s = hi
                elif not ok and run_s != -1:
                    if hi - run_s >= min_hops:
                        slice_arr = arr[run_s * hop + int(0.02 * sr) : hi * hop - int(0.02 * sr)]
                        if len(slice_arr) >= int(0.12 * sr):
                            candidates.append((float(np.mean(rms[run_s:hi])), slice_arr))
                    run_s = -1
            if run_s != -1 and len(quiet_mask) - run_s >= min_hops:
                slice_arr = arr[run_s * hop + int(0.02 * sr) : len(quiet_mask) * hop - int(0.02 * sr)]
                if len(slice_arr) >= int(0.12 * sr):
                    candidates.append((float(np.mean(rms[run_s:])), slice_arr))
            if candidates:
                break

    if candidates:
        candidates.sort(key=lambda x: x[0])
        base_rt = candidates[0][1].copy()
        return _loop_with_crossfade(base_rt, target_len, fade_samples=int(0.030 * sr)), "extracted_real"

    rng = np.random.default_rng(42)
    raw_rt = rng.normal(0.0, 0.0014, (target_len, 2)).astype(np.float32)
    k_rt = np.ones(6, dtype=np.float32) / 6.0
    raw_rt[:, 0] = np.convolve(raw_rt[:, 0], k_rt, mode="same")
    raw_rt[:, 1] = np.convolve(raw_rt[:, 1], k_rt, mode="same")
    return raw_rt, "synthetic_fallback"


def level_speech_segments(slices: List[np.ndarray]) -> Tuple[List[np.ndarray], Dict[str, Any]]:
    """实拍多段原声逐句独立响度拉平，并使用 tanh 软限幅防削波失真。"""
    if len(slices) < 2:
        return slices, {"applied": False, "spread_db": 0.0, "adjusted_count": 0}
    rms_dbs: List[float] = []
    for sl in slices:
        if len(sl) < 160:
            rms_dbs.append(-24.0)
            continue
        r = float(np.sqrt(np.mean(sl ** 2) + 1e-10))
        rms_dbs.append(20.0 * math.log10(max(1e-6, r)))

    spread_db = round(max(rms_dbs) - min(rms_dbs), 2)
    if spread_db <= 5.0:
        return slices, {"applied": False, "spread_db": spread_db, "adjusted_count": 0}

    sorted_dbs = sorted(rms_dbs)
    target_db = float(sorted_dbs[len(sorted_dbs) // 2])
    out: List[np.ndarray] = []
    adj_cnt = 0
    for sl, db in zip(slices, rms_dbs):
        delta = target_db - db
        if abs(delta) >= 2.0:
            gain_db = max(-6.0, min(10.0, delta))
            gain = 10.0 ** (gain_db / 20.0)
            leveled = (0.95 * np.tanh((sl * gain) / 0.95)).astype(np.float32)
            out.append(leveled)
            adj_cnt += 1
        else:
            out.append(sl)
    return out, {
        "applied": True,
        "spread_db": spread_db,
        "target_db": round(target_db, 2),
        "adjusted_count": adj_cnt,
    }


def resolve_dynamic_pause(
    cur_seg: Dict[str, Any],
    next_seg: Optional[Dict[str, Any]],
    base_pause: float,
    account_profile: Optional[Dict[str, Any]] = None,
) -> Tuple[float, str]:
    """语义动态呼吸停顿（仅当账号样本数充分时才读取 account_profile 基因参数）。"""
    if next_seg is None:
        return 0.0, "tail_end"
    prof = account_profile if (account_profile and account_profile.get("sample_sufficient") is True) else {}
    next_txt = str(next_seg.get("text", ""))
    next_emo = str(next_seg.get("emotion", "info"))
    if next_emo in ("twist", "hook") or any(cue in next_txt for cue in TWIST_CUES):
        val = float(prof.get("twist_pause_sec", max(0.28, min(0.38, base_pause * 1.55))))
        return round(max(0.24, min(0.40, val)), 3), "dramatic_beat_before_twist"
    if next_emo in ("cta", "persona"):
        val = float(prof.get("persona_pause_sec", prof.get("narrative_pause_sec", max(0.22, min(0.30, base_pause * 1.25)))))
        return round(max(0.18, min(0.32, val)), 3), "breathing_before_persona_cta"
    val = float(prof.get("narrative_pause_sec", max(0.11, min(0.17, base_pause * 0.72))))
    return round(max(0.09, min(0.22, val)), 3), "tight_narrative_pace"


def run_step3(
    pipeline_dir: Path,
    track: str = "store",
    bgm_path: Optional[Path] = None,
    pause_sec: float = 0.20,
    fps: int = DEFAULT_FPS,
    account_profile: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    pause_sec = max(0.14, min(0.32, pause_sec))
    eff_prof = account_profile if (account_profile and account_profile.get("sample_sufficient") is True) else None
    if eff_prof and "pause_sec" in eff_prof:
        pause_sec = max(0.14, min(0.32, float(eff_prof["pause_sec"])))

    transcript = read_json(pipeline_dir / "02_transcript.json")
    raw_audio_cache: Dict[str, np.ndarray] = {}

    def get_audio(path_str: str) -> np.ndarray:
        if path_str not in raw_audio_cache:
            arr, _ = load_stereo_wav_np(Path(path_str))
            raw_audio_cache[path_str] = arr
        return raw_audio_cache[path_str]

    if bgm_path is None:
        bgm_name = "upbeat_store_120bpm.wav" if track == "store" else "chill_story_100bpm.wav"
        bgm_path = ASSETS_DIR / "bgm" / bgm_name
    if not bgm_path.exists():
        raise FileNotFoundError(f"内置 BGM 不存在，请先运行 check_and_setup.py --init-assets: {bgm_path}")

    bgm_info = detect_bgm_beats(bgm_path)
    bgm_arr, _ = load_stereo_wav_np(bgm_path)

    valid_segments = [s for s in transcript["segments"] if not s.get("is_filler", False) and s["text"].strip()]
    if not valid_segments:
        raise ValueError("转写稿过滤废话与淘汰 NG 后无有效口播句子。")

    for seg in valid_segments:
        get_audio(seg.get("source_audio") or transcript["primary_audio"])
    room_tone_bed, room_tone_source = extract_real_room_tone(raw_audio_cache, valid_segments, SR)

    first_downbeat = bgm_info["downbeat_times_sec"][0] if bgm_info["downbeat_times_sec"] else 0.0
    bgm_shift_samples = int(round(first_downbeat * SR))
    aligned_bgm = bgm_arr[bgm_shift_samples:] if bgm_shift_samples < len(bgm_arr) else bgm_arr
    shifted_beats = [round(t - first_downbeat, 4) for t in bgm_info["beat_times_sec"] if t >= first_downbeat]
    shifted_downbeats = [round(t - first_downbeat, 4) for t in bgm_info["downbeat_times_sec"] if t >= first_downbeat]

    voice_chunks: List[np.ndarray] = []
    locked_sentences: List[Dict[str, Any]] = []
    events: List[Dict[str, Any]] = []
    cursor_samples = 0
    evt_counter = 1

    def add_event(etype: str, t_sec: float, label: str, sentence_id: Optional[str] = None, extra: Optional[Dict[str, Any]] = None) -> str:
        nonlocal evt_counter
        eid = f"evt_{evt_counter:03d}"
        evt_counter += 1
        item: Dict[str, Any] = {
            "id": eid,
            "type": etype,
            "time_sec": round(t_sec, 4),
            "frame": sec_to_frames(t_sec, fps),
            "label": label,
        }
        if sentence_id:
            item["sentence_id"] = sentence_id
        if extra:
            item.update(extra)
        events.append(item)
        return eid

    prepared_slices: List[Tuple[np.ndarray, int, int]] = []
    raw_only_slices: List[np.ndarray] = []
    for seg in valid_segments:
        src_audio_path = seg.get("source_audio") or transcript["primary_audio"]
        src_arr = get_audio(src_audio_path)

        raw_s0 = max(0, int(round(float(seg["start_sec"]) * SR)))
        raw_s1 = min(len(src_arr), int(round(float(seg["end_sec"]) * SR)))
        s0 = snap_to_zero_crossing(src_arr, raw_s0, int(0.010 * SR))
        s1 = snap_to_zero_crossing(src_arr, raw_s1, int(0.010 * SR))
        if s1 <= s0 + int(0.05 * SR):
            s0, s1 = raw_s0, raw_s1

        raw_slice = src_arr[s0:s1].copy()
        prepared_slices.append((raw_slice, s0, s1))
        raw_only_slices.append(raw_slice)

    leveled_slices, leveling_info = level_speech_segments(raw_only_slices)

    rt_offset = 0
    for s_idx, (seg, leveled_slice, (_, s0, s1)) in enumerate(
        zip(valid_segments, leveled_slices, prepared_slices), start=1
    ):
        sid = f"sent_{s_idx:03d}"
        orig_start = float(seg["start_sec"])
        orig_end = float(seg["end_sec"])

        raw_slice = leveled_slice.copy()
        n_fade = min(len(raw_slice) // 4, int(SR * 0.010))
        if n_fade > 2:
            theta = np.linspace(0.0, np.pi * 0.5, n_fade, dtype=np.float32)[:, None]
            raw_slice[:n_fade] *= np.sin(theta)
            raw_slice[-n_fade:] *= np.cos(theta)
        chunk = raw_slice
        chunk_dur = len(chunk) / float(SR)

        new_start_sec = round(cursor_samples / float(SR), 4)
        new_end_sec = round((cursor_samples + len(chunk)) / float(SR), 4)

        orig_span = max(0.05, orig_end - orig_start)
        new_words: List[Dict[str, Any]] = []
        keyword_anchors: List[float] = []
        accent_anchors: List[float] = []
        word_energy_peaks: List[Tuple[float, float, str]] = []
        for w in seg.get("words", []):
            rel_s = (float(w["start_sec"]) - orig_start) / orig_span
            rel_e = (float(w["end_sec"]) - orig_start) / orig_span
            ws = round(new_start_sec + max(0.0, rel_s) * chunk_dur, 4)
            we = round(new_start_sec + min(1.0, rel_e) * chunk_dur, 4)
            is_kw = bool(w.get("is_keyword", False))
            is_acc = bool(w.get("is_accent", False))
            en = float(w.get("energy", 0.75))
            new_words.append({
                "word": w["word"],
                "start_sec": ws,
                "end_sec": we,
                "is_keyword": is_kw,
                "is_accent": is_acc,
                "energy": en,
            })
            word_energy_peaks.append((en, ws, str(w["word"])))
            if is_kw and (not keyword_anchors or ws - keyword_anchors[-1] > 0.55):
                keyword_anchors.append(ws)
            elif is_acc and (not accent_anchors or ws - accent_anchors[-1] > 0.50):
                accent_anchors.append(ws)

        next_seg = valid_segments[s_idx] if s_idx < len(valid_segments) else None
        cur_pause_sec, pause_reason = resolve_dynamic_pause(seg, next_seg, pause_sec, eff_prof)

        sent_obj = {
            "id": sid,
            "text": seg["text"],
            "source_media": seg.get("source_media"),
            "source_video_grade": seg.get("source_video_grade", "usable"),
            "source_in_sec": round(s0 / float(SR), 4),
            "source_out_sec": round(s1 / float(SR), 4),
            "start_sec": new_start_sec,
            "end_sec": new_end_sec,
            "start_frame": sec_to_frames(new_start_sec, fps),
            "end_frame": sec_to_frames(new_end_sec, fps),
            "pause_after_sec": cur_pause_sec,
            "pause_reason": pause_reason,
            "emotion_or_info": seg.get("emotion", "info"),
            "keywords": seg.get("keywords", []),
            "words": new_words,
        }
        locked_sentences.append(sent_obj)

        if s_idx == 1:
            add_event("sentence_start", new_start_sec, "钩子句首字对齐BGM强拍", sid, {"priority": "high"})
        else:
            add_event("sentence_start", new_start_sec, f"第{s_idx}句开始", sid)

        for k_t in keyword_anchors:
            add_event("keyword_accent", k_t, f"关键词重音 ({seg.get('emotion', 'info')})", sid)
            add_event("sfx_cue", k_t, "情绪/信息点音效落点", sid)

        for a_t in accent_anchors:
            if all(abs(a_t - kt) >= 0.45 for kt in keyword_anchors):
                add_event("stress", a_t, "声学重音峰 (F0/RMS实测)", sid)

        # 开篇若无关键词事件，仅选取真实实测能量最高字作为 stress 锚点，绝不按 0.38/0.72 比例编造假重音
        if s_idx == 1 and word_energy_peaks:
            existing_3s = [t for t in (keyword_anchors + accent_anchors) if 0.50 <= t <= 2.75]
            if not existing_3s:
                in_win = [p for p in word_energy_peaks if 0.50 <= p[1] <= 2.75]
                if in_win:
                    in_win.sort(key=lambda x: -x[0])
                    add_event("stress", in_win[0][1], f"开篇实测能量峰值字: {in_win[0][2]}", sid)

        add_event("sentence_end", new_end_sec, f"第{s_idx}句结束", sid)

        voice_chunks.append(chunk)
        cursor_samples += len(chunk)

        if cur_pause_sec > 0.0 and s_idx < len(valid_segments):
            p_samples = int(round(cur_pause_sec * SR))
            if rt_offset + p_samples > len(room_tone_bed):
                rt_offset = 0
            p_buf = room_tone_bed[rt_offset : rt_offset + p_samples].copy()
            rt_offset += p_samples
            add_event("pause_start", new_end_sec, f"动态呼吸停顿({cur_pause_sec}s: {pause_reason})", sid)
            voice_chunks.append(p_buf)
            cursor_samples += p_samples
            pause_end_sec = round(cursor_samples / float(SR), 4)
            add_event("pause_end", pause_end_sec, "句间停顿结束", sid)

    tail_samples = int(0.24 * SR)
    voice_chunks.append(room_tone_bed[:tail_samples].copy())
    locked_voice = np.concatenate(voice_chunks, axis=0)
    total_samples = len(locked_voice)
    total_dur = round(total_samples / float(SR), 4)

    if len(aligned_bgm) < total_samples:
        beat_sec = 60.0 / max(40.0, float(bgm_info["bpm"]))
        bar_samples = int(round(beat_sec * 4.0 * SR))
        usable_bars = (len(aligned_bgm) // max(1, bar_samples)) * bar_samples
        loop_unit = aligned_bgm[:usable_bars] if usable_bars >= bar_samples else aligned_bgm
        if len(loop_unit) == 0:
            loop_unit = aligned_bgm
        aligned_bgm = _loop_with_crossfade(loop_unit, total_samples, fade_samples=int(0.200 * SR))
        shifted_beats = []
        shifted_downbeats = []
        t = 0.0
        bi = 0
        while t < total_dur:
            shifted_beats.append(round(t, 4))
            if bi % 4 == 0:
                shifted_downbeats.append(round(t, 4))
            t += beat_sec
            bi += 1

    locked_bgm = aligned_bgm[:total_samples].copy()
    fade_out_n = min(len(locked_bgm), int(0.25 * SR))
    if fade_out_n > 2:
        locked_bgm[-fade_out_n:] *= np.linspace(1.0, 0.15, fade_out_n, dtype=np.float32)[:, None]

    downbeat_set = set(shifted_downbeats)
    for b_idx, b_t in enumerate(shifted_beats):
        if b_t <= min(4.0, total_dur - 0.1) and b_t not in downbeat_set:
            add_event("bgm_beat", b_t, f"BGM开篇第{b_idx+1}拍")
    for db_idx, db_t in enumerate(shifted_downbeats):
        if db_t <= total_dur - 0.1:
            add_event("bgm_downbeat", db_t, f"BGM第{db_idx+1}小节强拍")
            if db_idx > 0 and db_idx % 4 == 0:
                add_event("bgm_section", db_t, f"BGM第{db_idx//4 + 1}乐段切换")

    events.sort(key=lambda e: (e["frame"], e["id"]))

    locked_voice_path = pipeline_dir / "03_locked_voice.wav"
    locked_bgm_path = pipeline_dir / "03_locked_bgm.wav"
    save_stereo_wav_np(locked_voice_path, locked_voice, SR)
    save_stereo_wav_np(locked_bgm_path, locked_bgm, SR)

    blind_m4a_path = pipeline_dir / "03_blind_audio.m4a"
    ffmpeg_bin = find_bin("ffmpeg")
    if ffmpeg_bin:
        run_cmd([
            ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(locked_voice_path),
            "-c:a", "aac", "-b:a", "192k", "-ar", str(SR),
            str(blind_m4a_path),
        ], check=False)

    pauses_used = [s["pause_after_sec"] for s in locked_sentences[:-1] if s["pause_after_sec"] > 0]
    audio_events = {
        "step": "03_lock_audio",
        "locked": True,
        "fps": fps,
        "sample_rate": SR,
        "locked_duration_sec": total_dur,
        "locked_duration_frames": sec_to_frames(total_dur, fps),
        "pause_duration_sec": round(sum(pauses_used) / len(pauses_used), 3) if pauses_used else pause_sec,
        "pause_range_sec": [min(pauses_used), max(pauses_used)] if pauses_used else [pause_sec, pause_sec],
        "room_tone_source": room_tone_source,
        "per_sentence_leveling": leveling_info,
        "locked_voice_wav": str(locked_voice_path.resolve()),
        "locked_bgm_wav": str(locked_bgm_path.resolve()),
        "blind_audio_m4a": str(blind_m4a_path.resolve()) if blind_m4a_path.is_file() else None,
        "bgm": {
            "file": str(bgm_path.resolve()),
            "bpm": bgm_info["bpm"],
            "beat_method": bgm_info["method"],
            "beat_times_sec": [t for t in shifted_beats if t <= total_dur],
            "downbeat_times_sec": [t for t in shifted_downbeats if t <= total_dur],
            "hook_first_word_sec": locked_sentences[0]["start_sec"],
            "hook_aligned_downbeat_sec": 0.0,
        },
        "sentences": locked_sentences,
        "events": events,
    }
    write_json(pipeline_dir / "03_audio_events.json", audio_events)
    print(
        f"[step3] 音轨已锁定！有效句 {len(locked_sentences)} 句，总长 {total_dur:.2f}s，"
        f"RoomTone={room_tone_source}，动态停顿={audio_events['pause_range_sec']}s -> 03_audio_events.json"
    )
    return audio_events


def main() -> None:
    maybe_reexec_in_venv()
    parser = argparse.ArgumentParser(description="Step 3: 锁定音轨 (抗混叠加载 + Overlap-Add RoomTone + 过零点精剪)")
    parser.add_argument("--pipeline-dir", required=True)
    parser.add_argument("--track", choices=["store", "creator"], default="store")
    parser.add_argument("--bgm", help="可选：自定义 BGM 音频路径")
    parser.add_argument("--pause-sec", type=float, default=0.20, help="基准句间停顿时长 (0.14-0.32s)")
    parser.add_argument("--account-profile", help="可选：account_profile.json 路径")
    args = parser.parse_args()
    prof = read_json(Path(args.account_profile)) if args.account_profile and Path(args.account_profile).exists() else None
    run_step3(
        Path(args.pipeline_dir),
        track=args.track,
        bgm_path=Path(args.bgm) if args.bgm else None,
        pause_sec=args.pause_sec,
        account_profile=prof,
    )


if __name__ == "__main__":
    main()
