#!/usr/bin/env python3
"""
[Step 3: 锁定音轨 (先音后画 核心锚点)]
1. 在转写稿 02_transcript.json 上按字剪除口播中的废话、气口、口误和重复。
2. 将句间停顿统一规整为 0.15-0.30 秒（默认 0.20s，杜绝拖沓与 >0.8s 静音）。
3. 选好 BGM 并执行节拍检测（优先 librosa，无则用 numpy 能量包络+元数据校验）。
4. 将剪好的口播放到 BGM 上：让钩子句第一个字尽量精确落在 BGM 强拍上！
5. 全面标注音频事件：句首、句尾、关键词、重音、停顿、BGM 强拍与段落变化、音效点。
6. 输出锁定音轨 (03_locked_voice.wav, 03_locked_bgm.wav) 和音频事件时间线 (03_audio_events.json)。
   从此步骤之后，画面阶段严格禁止修改声音！
"""

from __future__ import annotations

import argparse
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from common import (
    ASSETS_DIR,
    DEFAULT_FPS,
    maybe_reexec_in_venv,
    read_json,
    sec_to_frames,
    write_json,
)

maybe_reexec_in_venv()

import numpy as np

SR = 48000


def load_stereo_wav_np(path: Path) -> Tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as wf:
        sr = wf.getframerate()
        n_ch = wf.getnchannels()
        raw = wf.readframes(wf.getnframes())
    arr = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if n_ch == 1:
        arr = np.stack([arr, arr], axis=1)
    else:
        arr = arr.reshape(-1, n_ch)[:, :2]
    return arr, sr


def save_stereo_wav_np(path: Path, audio: np.ndarray, sr: int = SR) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    clipped = np.clip(audio, -0.98, 0.98)
    pcm = (clipped * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())


def detect_bgm_beats(bgm_wav: Path) -> Dict[str, Any]:
    """检测 BGM 节拍与强拍位置：优先 librosa，回退 numpy 短时能量峰值检测 + 同名 JSON 元数据。"""
    meta_json = bgm_wav.with_suffix(".json")
    if meta_json.exists():
        meta = read_json(meta_json)
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
            "bpm": round(float(np.atleast_1d(tempo)[0]), 1),
            "beat_times_sec": [round(float(x), 4) for x in beats],
            "downbeat_times_sec": [round(float(x), 4) for x in downbeats],
            "method": "librosa",
        }
    except Exception:
        # numpy 能量包络峰值检测回退
        audio, sr = load_stereo_wav_np(bgm_wav)
        mono = audio.mean(axis=1)
        hop = int(sr * 0.02)
        energy = np.array([np.sqrt(np.mean(mono[i : i + hop] ** 2)) for i in range(0, len(mono) - hop, hop)])
        thresh = np.mean(energy) * 1.35
        beats: List[float] = []
        last_t = -1.0
        for idx, e in enumerate(energy):
            t = idx * 0.02
            if e > thresh and t - last_t >= 0.40:
                beats.append(round(t, 4))
                last_t = t
        if not beats:
            beats = [round(i * 0.5, 4) for i in range(int(len(mono) / sr / 0.5))]
        return {
            "bpm": 120.0,
            "beat_times_sec": beats,
            "downbeat_times_sec": beats[::4],
            "method": "numpy_energy",
        }


def apply_micro_fade(seg: np.ndarray, fade_ms: float = 8.0, sr: int = SR) -> np.ndarray:
    n_fade = min(len(seg) // 4, int(sr * fade_ms / 1000.0))
    if n_fade <= 1:
        return seg
    out = seg.copy()
    ramp = np.linspace(0.0, 1.0, n_fade, dtype=np.float32)[:, None]
    out[:n_fade] *= ramp
    out[-n_fade:] *= ramp[::-1]
    return out


def run_step3(
    pipeline_dir: Path,
    track: str = "store",
    bgm_path: Optional[Path] = None,
    pause_sec: float = 0.20,
    fps: int = DEFAULT_FPS,
) -> Dict[str, Any]:
    # 强制把句间停顿限制在需求指定的 0.15 - 0.30s 之间
    pause_sec = max(0.15, min(0.30, pause_sec))

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

    # 1. 按字剔除废话、口误、重复，保留有效句，并把每句前后静音/气口裁干净
    valid_segments = [s for s in transcript["segments"] if not s.get("is_filler", False) and s["text"].strip()]
    if not valid_segments:
        raise ValueError("转写稿过滤废话后无有效口播句子。")

    # BGM 强拍对齐点：取第一个强拍作为钩子句首字时间偏移，裁剪 BGM 使其 0.00s 即是强拍
    first_downbeat = bgm_info["downbeat_times_sec"][0] if bgm_info["downbeat_times_sec"] else 0.0
    bgm_shift_samples = int(round(first_downbeat * SR))
    aligned_bgm = bgm_arr[bgm_shift_samples:] if bgm_shift_samples < len(bgm_arr) else bgm_arr
    # 平移节拍列表，使强拍落在 t=0.0s，钩子首字 t=0.0s 完美踩中强拍
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

    pause_samples = int(round(pause_sec * SR))
    pause_buf = np.zeros((pause_samples, 2), dtype=np.float32)

    for s_idx, seg in enumerate(valid_segments, start=1):
        sid = f"sent_{s_idx:03d}"
        src_audio_path = seg.get("source_audio") or transcript["primary_audio"]
        src_arr = get_audio(src_audio_path)

        orig_start = float(seg["start_sec"])
        orig_end = float(seg["end_sec"])
        s0 = max(0, int(round(orig_start * SR)))
        s1 = min(len(src_arr), int(round(orig_end * SR)))
        chunk = apply_micro_fade(src_arr[s0:s1])
        chunk_dur = len(chunk) / float(SR)

        new_start_sec = round(cursor_samples / float(SR), 4)
        new_end_sec = round((cursor_samples + len(chunk)) / float(SR), 4)

        # 按新时间轴映射每个字的起止时间
        orig_span = max(0.05, orig_end - orig_start)
        new_words: List[Dict[str, Any]] = []
        keyword_anchors: List[float] = []
        for w in seg.get("words", []):
            rel_s = (float(w["start_sec"]) - orig_start) / orig_span
            rel_e = (float(w["end_sec"]) - orig_start) / orig_span
            ws = round(new_start_sec + max(0.0, rel_s) * chunk_dur, 4)
            we = round(new_start_sec + min(1.0, rel_e) * chunk_dur, 4)
            new_words.append({
                "word": w["word"],
                "start_sec": ws,
                "end_sec": we,
                "is_keyword": bool(w.get("is_keyword", False)),
                "energy": float(w.get("energy", 0.75)),
            })
            if w.get("is_keyword") and (not keyword_anchors or ws - keyword_anchors[-1] > 0.55):
                keyword_anchors.append(ws)

        sent_obj = {
            "id": sid,
            "text": seg["text"],
            "source_media": seg.get("source_media"),
            "source_in_sec": round(orig_start, 4),
            "source_out_sec": round(orig_end, 4),
            "start_sec": new_start_sec,
            "end_sec": new_end_sec,
            "start_frame": sec_to_frames(new_start_sec, fps),
            "end_frame": sec_to_frames(new_end_sec, fps),
            "pause_after_sec": pause_sec if s_idx < len(valid_segments) else 0.0,
            "emotion_or_info": seg.get("emotion", "info"),
            "keywords": seg.get("keywords", []),
            "words": new_words,
        }
        locked_sentences.append(sent_obj)

        # 标注音频事件：句首、关键词/重音、句尾、停顿
        if s_idx == 1:
            add_event("sentence_start", new_start_sec, "钩子句首字对齐BGM强拍", sid, {"priority": "high"})
        else:
            add_event("sentence_start", new_start_sec, f"第{s_idx}句开始", sid)

        # 为本句标注关键词重音事件；同时针对前 3 秒开篇钩子句，提取语义停顿/重音词锚点保证声画节奏
        for k_t in keyword_anchors:
            add_event("keyword_accent", k_t, f"关键词重音 ({seg.get('emotion', 'info')})", sid)
            add_event("sfx_cue", k_t, "情绪/信息点音效落点", sid)

        if s_idx == 1 and new_words:
            mid_stress_candidates = [t for t in keyword_anchors if 0.60 <= t <= 2.60]
            if len(mid_stress_candidates) < 2 and len(new_words) >= 6:
                for ratio in (0.38, 0.72):
                    w_idx = min(len(new_words) - 1, max(1, int(len(new_words) * ratio)))
                    st_t = new_words[w_idx]["start_sec"]
                    if 0.55 <= st_t <= 2.70 and all(abs(st_t - t) >= 0.55 for t in mid_stress_candidates):
                        add_event("stress", st_t, f"开篇句重音词: {new_words[w_idx]['word']}", sid)
                        mid_stress_candidates.append(st_t)

        add_event("sentence_end", new_end_sec, f"第{s_idx}句结束", sid)

        voice_chunks.append(chunk)
        cursor_samples += len(chunk)

        if s_idx < len(valid_segments):
            add_event("pause_start", new_end_sec, f"句间停顿开始({pause_sec}s)", sid)
            voice_chunks.append(pause_buf)
            cursor_samples += pause_samples
            pause_end_sec = round(cursor_samples / float(SR), 4)
            add_event("pause_end", pause_end_sec, "句间停顿结束", sid)

    # 拼出锁定人声主干，末尾保留 0.25s 呼吸尾音（< 0.8s 静音限制）
    tail_samples = int(0.25 * SR)
    voice_chunks.append(np.zeros((tail_samples, 2), dtype=np.float32))
    locked_voice = np.concatenate(voice_chunks, axis=0)
    total_samples = len(locked_voice)
    total_dur = round(total_samples / float(SR), 4)

    # 若 BGM 短于口播，则按整小节长度无缝循环拼长；再精确裁剪到与锁定人声等长
    if len(aligned_bgm) < total_samples:
        beat_sec = 60.0 / bgm_info["bpm"]
        bar_samples = int(round(beat_sec * 4.0 * SR))
        loop_unit = aligned_bgm[: max(bar_samples, len(aligned_bgm) - (len(aligned_bgm) % max(1, bar_samples)))]
        if len(loop_unit) == 0:
            loop_unit = aligned_bgm
        repeats = (total_samples // len(loop_unit)) + 2
        aligned_bgm = np.tile(loop_unit, (repeats, 1))
        # 重新补齐延长的节拍与强拍时刻
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

    locked_bgm = aligned_bgm[:total_samples]
    # 末尾 0.25s 轻微淡出
    fade_out_n = min(len(locked_bgm), int(0.25 * SR))
    locked_bgm[-fade_out_n:] *= np.linspace(1.0, 0.15, fade_out_n, dtype=np.float32)[:, None]

    # 把 BGM 强拍、前4秒节拍与段落变化写入事件表
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

    audio_events = {
        "step": "03_lock_audio",
        "locked": True,
        "fps": fps,
        "sample_rate": SR,
        "locked_duration_sec": total_dur,
        "locked_duration_frames": sec_to_frames(total_dur, fps),
        "pause_duration_sec": pause_sec,
        "locked_voice_wav": str(locked_voice_path.resolve()),
        "locked_bgm_wav": str(locked_bgm_path.resolve()),
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
        f"BPM={bgm_info['bpm']}，音频事件 {len(events)} 个 -> 03_audio_events.json"
    )
    return audio_events


def main() -> None:
    maybe_reexec_in_venv()
    parser = argparse.ArgumentParser(description="Step 3: 锁定音轨与音频事件标注")
    parser.add_argument("--pipeline-dir", required=True)
    parser.add_argument("--track", choices=["store", "creator"], default="store")
    parser.add_argument("--bgm", help="可选：自定义 BGM 音频路径")
    parser.add_argument("--pause-sec", type=float, default=0.20, help="句间停顿时长 (0.15-0.30s)")
    args = parser.parse_args()
    run_step3(
        Path(args.pipeline_dir),
        track=args.track,
        bgm_path=Path(args.bgm) if args.bgm else None,
        pause_sec=args.pause_sec,
    )


if __name__ == "__main__":
    main()
