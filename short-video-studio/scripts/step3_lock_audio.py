#!/usr/bin/env python3
"""
[Step 3: 锁定音轨 (抗混叠加载 + 无线麦救援 + 零拍漂整数小节循环 + Equal-Power Room Tone 拼接)]
v5 升级：
1. 自动调用 common.rescue_stereo_channels 拯救单边失效无线麦；兼容 .m4a/.wav BGM。
2. BGM 循环采用整数小节步长对齐 (zero beat drift)，彻底解决 200ms 交叉淡化导致第 2 轮起每轮累计漂移 6 帧 Bug。
3. extract_real_room_tone 改用全量原始语音段（含 NG 与画外音）建立屏蔽掩膜，防止废句人声渗入房底循环；
   句间拼接采用 Equal-Power 平滑淡入淡出消除底噪开关抽吸与阶跃点击。
4. 修复 detect_bgm_beats 缺 bpm 兜底与真实波形校验，补齐 persona_pause_sec 消费。
"""

from __future__ import annotations

import argparse
import math
import re
import sys
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

SCRIPTS_ROOT = Path(__file__).resolve().parent
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from common import (
    ASSETS_DIR,
    DEFAULT_FPS,
    find_bin,
    load_style_applied,
    load_style_spectrum_entry,
    maybe_reexec_in_venv,
    read_json,
    read_wav_as_48k_stereo,
    rescue_stereo_channels,
    run_cmd,
    sec_to_frames,
    write_json,
    write_wav_48k_stereo,
)

if __name__ == "__main__":
    maybe_reexec_in_venv()

import numpy as np

SR = 48000
TWIST_CUES = ("但是", "其实", "真相", "结果", "反而", "没想到", "记住", "说白了", "关键是")


def load_stereo_wav_np(path: Path) -> Tuple[np.ndarray, int]:
    """统一读取任意格式音频 (.wav/.m4a/.mp3) 并抗混叠转换为 48kHz stereo float32。"""
    return read_wav_as_48k_stereo(path, target_sr=SR)


def save_stereo_wav_np(path: Path, audio: np.ndarray, sr: int = SR) -> None:
    write_wav_48k_stereo(path, audio, sr=sr, clip_limit=0.985)


def _loop_with_crossfade(unit: np.ndarray, target_len: int, fade_samples: int) -> np.ndarray:
    """用 Hann Overlap-Add 无缝拼长音频块（用于房底噪音等非严格节拍素材，末块不强制淡出）。"""
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
        is_last = (cursor + len(unit) >= target_len)
        if not is_last:
            blk[-xf:] *= fade_out
        if first:
            out[cursor : cursor + len(blk)] = blk
            first = False
        else:
            out[cursor : cursor + xf] += blk[:xf]
            out[cursor + xf : cursor + len(blk)] = blk[xf:]
        cursor += hop
    return out[:target_len].astype(np.float32)


def _loop_bgm_exact_bars(aligned_bgm: np.ndarray, bar_samples: int, target_len: int) -> Tuple[np.ndarray, int]:
    """
    零拍漂 BGM 整数小节循环器：
    严格保证第 k 轮循环的起始点位于 k * usable_bars，利用尾部预留余音（或短 20ms 过渡）平滑接缝，
    使全曲多轮循环后拍点累积误差严格为 0 采样！返回 (looped_audio, cycle_samples)。
    """
    n_src = len(aligned_bgm)
    if n_src >= target_len:
        return aligned_bgm[:target_len].copy(), n_src
    if n_src < 100 or bar_samples <= 0:
        return np.zeros((target_len, 2), dtype=np.float32), max(1, bar_samples)

    usable_bars = (n_src // bar_samples) * bar_samples
    if usable_bars < bar_samples:
        usable_bars = n_src
    xf_tail = min(int(0.080 * SR), max(0, n_src - usable_bars))
    out = np.zeros((target_len + usable_bars + xf_tail + 64, 2), dtype=np.float32)

    cursor = 0
    first = True
    while cursor < target_len:
        out[cursor : cursor + usable_bars] = aligned_bgm[:usable_bars]
        if first:
            first = False
        elif xf_tail >= 16:
            # 将上一轮超出 usable_bars 的自然余音交叉淡化混入新循环开头（不提前步长！）
            fi = (0.5 - 0.5 * np.cos(np.linspace(0.0, np.pi, xf_tail, dtype=np.float32)))[:, None]
            tail_tail = aligned_bgm[usable_bars : usable_bars + xf_tail]
            head_seg = aligned_bgm[:xf_tail]
            out[cursor : cursor + xf_tail] = tail_tail * (1.0 - fi) + head_seg * fi
        else:
            # 无多余余音时仅在接缝处做极短 15ms 回绕防爆音过渡，不改变步长
            micro = min(int(0.015 * SR), usable_bars // 8)
            if micro >= 8:
                fi = np.linspace(0.0, 1.0, micro, dtype=np.float32)[:, None]
                tail_s = aligned_bgm[usable_bars - micro : usable_bars]
                head_s = aligned_bgm[:micro]
                out[cursor : cursor + micro] = tail_s * (1.0 - fi) + head_s * fi
        cursor += usable_bars

    return out[:target_len].astype(np.float32), usable_bars


def detect_bgm_beats(bgm_path: Path, audio_arr: Optional[np.ndarray] = None) -> Dict[str, Any]:
    """检测 BGM 节拍与强拍位置：优先校验同目录节拍 JSON（并复用已加载波形核对首拍偏移），次选 librosa。"""
    meta_json = bgm_path.with_suffix(".json")
    meta_bpm: Optional[float] = None
    if meta_json.exists():
        meta = read_json(meta_json)
        beats = [float(x) for x in meta.get("beat_times_sec", [])]
        downs = [float(x) for x in meta.get("downbeat_times_sec", [])]
        if meta.get("bpm"):
            meta_bpm = float(meta["bpm"])
        elif len(beats) >= 2:
            diffs = [beats[i + 1] - beats[i] for i in range(len(beats) - 1) if beats[i + 1] > beats[i]]
            if diffs:
                meta_bpm = round(60.0 / float(np.median(diffs)), 1)
        if beats and downs:
            method = "meta_only"
            try:
                audio = audio_arr if audio_arr is not None else load_stereo_wav_np(bgm_path)[0]
                if len(audio) > int(SR * 0.5):
                    env = np.sqrt(np.mean(audio[: int(SR * 1.5)] ** 2, axis=1) + 1e-12)
                    act = np.where(env >= float(np.max(env)) * 0.15)[0]
                    if len(act) > 0 and act[0] / float(SR) <= 0.15:
                        method = "meta+waveform_verified"
            except Exception:
                pass
            return {
                "bpm": float(meta_bpm or 120.0),
                "beat_times_sec": beats,
                "downbeat_times_sec": downs,
                "method": method,
            }

    try:
        import librosa
        y, sr_l = librosa.load(str(bgm_path), sr=22050)
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
        audio, sr = load_stereo_wav_np(bgm_path)
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
    all_segments: List[Dict[str, Any]],
    sr: int = SR,
    target_sec: float = 2.5,
) -> Tuple[np.ndarray, str]:
    """抽取真实房间底噪：屏蔽全部原始语音（含被淘汰 NG 与画外导演声），自适应门店真实底噪上限。"""
    target_len = int(round(target_sec * sr))
    hop = int(0.020 * sr)

    seg_audio_resolved: List[Tuple[str, float, float]] = []
    for seg in all_segments:
        sa = seg.get("source_audio")
        if sa:
            try:
                sa_res = str(Path(sa).resolve())
            except Exception:
                sa_res = str(sa)
            seg_audio_resolved.append((sa_res, float(seg["start_sec"]), float(seg["end_sec"])))

    candidates: List[Tuple[float, np.ndarray]] = []
    for path_str, arr in raw_audio_cache.items():
        if len(arr) < int(0.4 * sr):
            continue
        try:
            cur_res = str(Path(path_str).resolve())
        except Exception:
            cur_res = str(path_str)
        speech_mask_hops = np.zeros(max(1, len(arr) // hop), dtype=bool)
        for sa_res, st_s, en_s in seg_audio_resolved:
            if sa_res != cur_res:
                continue
            h0 = max(0, int((st_s - 0.06) / 0.020))
            h1 = min(len(speech_mask_hops), int((en_s + 0.06) / 0.020))
            speech_mask_hops[h0:h1] = True

        mono = arr.mean(axis=1)
        n_hops = len(speech_mask_hops)
        rms = np.sqrt(np.mean(mono[: n_hops * hop].reshape(n_hops, hop) ** 2, axis=1) + 1e-12)
        nonzero = rms[rms > 2e-5]
        if len(nonzero) < 10:
            continue
        p20 = float(np.percentile(nonzero, 20))
        # 按实际底噪自适应（允许最高 ~-28dBFS 环境声），不再强设极低 0.015 导致嘈杂门店退化成极暗白噪黑洞
        ceil_rms = min(0.040, max(0.004, p20 * 1.6))
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
    """实拍多段原声逐句独立响度拉平（仅基于有效发声帧统计 RMS），并使用 tanh 软限幅防削波。"""
    if len(slices) < 2:
        return slices, {"applied": False, "spread_db": 0.0, "adjusted_count": 0}
    rms_dbs: List[float] = []
    for sl in slices:
        if len(sl) < 160:
            rms_dbs.append(-24.0)
            continue
        mono = sl.mean(axis=1)
        abs_m = np.abs(mono)
        act = mono[abs_m >= max(0.004, float(np.max(abs_m)) * 0.12)]
        r = float(np.sqrt(np.mean((act if len(act) >= 80 else mono) ** 2) + 1e-10))
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


def _keywords_of_sentence(text: str, raw_keywords: Sequence[Any]) -> List[str]:
    """只保留真的出现在这句正文里的关键词；一个都不剩就从正文重新抠（数字+量词优先）。"""
    txt = str(text or "")
    if not txt:
        return []
    kept: List[str] = []
    for kw in raw_keywords:
        k = str(kw).strip()
        if len(k) < 2:
            continue
        if k in txt and k not in kept:
            kept.append(k)
    if kept:
        return kept
    try:
        import keyword_extract  # type: ignore
        return list(keyword_extract.extract_keywords(txt) or [])
    except Exception:
        nums = re.findall(r"[零一二两三四五六七八九十百千万\d]+(?:\.[\d]+)?[个张间家次倍元块万亿%％折平米天小时分钟秒]", txt)
        return nums[:3]


def resolve_dynamic_pause(
    cur_seg: Dict[str, Any],
    next_seg: Optional[Dict[str, Any]],
    base_pause: float,
    account_profile: Optional[Dict[str, Any]] = None,
) -> Tuple[float, str]:
    """语义动态呼吸停顿（解除 0.24~0.32s 硬钳位死区，确保 --pause-sec 与实验参数全程单调生效）。"""
    if next_seg is None:
        return 0.0, "tail_end"
    prof = account_profile if (account_profile and account_profile.get("sample_sufficient") is True) else {}
    next_txt = str(next_seg.get("text", ""))
    next_emo = str(next_seg.get("emotion", "info"))
    if next_emo in ("twist", "hook") or any(cue in next_txt for cue in TWIST_CUES):
        val = float(prof.get("twist_pause_sec", base_pause * 1.35))
        return round(max(0.22, min(0.45, val)), 3), "dramatic_beat_before_twist"
    if next_emo in ("cta", "persona"):
        val = float(prof.get("persona_pause_sec", base_pause * 1.15))
        return round(max(0.18, min(0.38, val)), 3), "breathing_before_persona_cta"
    val = float(prof.get("narrative_pause_sec", base_pause * 0.85))
    return round(max(0.10, min(0.32, val)), 3), "tight_narrative_pace"


def find_source_bgm(style_id: Optional[str]) -> Optional[Path]:
    """按源模版 BGM 曲名在 sound_vibe/user/<style_id>/ 里找用户自备的原曲。

    匹配规则：文件名包含源曲名（去空格、忽略大小写）即命中。
    命中即用原曲（像素/听感层面 1:1），否则回落到自带 CC0 音库。
    """
    if not style_id:
        return None
    user_dir = ASSETS_DIR / "sound_vibe" / "user" / str(style_id)
    if not user_dir.is_dir():
        return None
    try:
        manifest = read_json(ASSETS_DIR / "sound_vibe" / "vibe_sound_manifest.json")
    except Exception:
        return None
    node = (manifest.get("styles") or {}).get(str(style_id)) or {}
    names = [str(n) for n in (node.get("source_bgm_names") or []) if str(n).strip()]
    if not names:
        return None
    audio_ext = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"}
    files = [p for p in sorted(user_dir.glob("**/*")) if p.is_file() and p.suffix.lower() in audio_ext]
    for want in names:
        key = want.replace(" ", "").lower()
        for f in files:
            if key and key in f.stem.replace(" ", "").lower():
                return f
    return None


def _resolve_default_bgm(track: str, style_id: Optional[str] = None) -> Path:
    """统一优先使用 CC0 真录 sound_vibe 音库，彻底告别正弦波合成占位音。

    若用户把源模版原曲放进 sound_vibe/user/<style_id>/ 且文件名含源曲名，
    则优先用原曲（听感 1:1），不再用替代乐段。
    """
    src = find_source_bgm(style_id)
    if src is not None:
        return src
    # 只认这个风格自己的 manifest 节点。旧写法在最后写死 `style_01_bgm.m4a`（creator 轨写 style_11），
    # 于是「源草稿没有 BGM」的风格会静悄悄放上别家的曲子（2026-11-24 实测：整片 BGM 是 style_01 的）。
    node: Dict[str, Any] = {}
    try:
        mf = read_json(ASSETS_DIR / "sound_vibe" / "vibe_sound_manifest.json")
        node = (mf.get("styles") or {}).get(str(style_id or "")) or {}
    except Exception:
        node = {}
    rel = node.get("bgm_wav")
    if rel and not node.get("bgm_is_silence_placeholder"):
        cand = ASSETS_DIR / str(rel)
        if cand.is_file():
            return cand
    # 本风格确实没有 BGM：给一条等长静音占位（下游会按小节循环），绝不再借别家的曲子。
    quiet = ASSETS_DIR / "sound_vibe" / "bgm" / "_no_bgm.wav"
    if not quiet.is_file():
        try:
            quiet.parent.mkdir(parents=True, exist_ok=True)
            save_stereo_wav_np(quiet, np.zeros((SR * 3, 2), dtype=np.float32), SR)
        except Exception:
            pass
    if quiet.is_file():
        print(f"[step3] [WARN] {style_id or track} 没有自己的 BGM（{node.get('bgm_disabled_reason') or '源草稿里没有'}），"
              f"整片不放 BGM，用静音占位顶住")
        return quiet
    sid = "style_01" if track == "store" else "style_11"
    return ASSETS_DIR / "sound_vibe" / "bgm" / f"{sid}_bgm.m4a"


def _equal_power_append(buf: List[np.ndarray], seg: np.ndarray, xf_samples: int) -> Tuple[np.ndarray, int]:
    """真·等功率重叠相加拼接 (cos²θ + sin²θ = 1)，消除零重叠拼接制造的 24ms 底噪塌陷凹坑。"""
    if not buf:
        buf.append(seg.copy())
        return seg, 0
    prev = buf[-1]
    xf = min(xf_samples, len(prev) // 4, len(seg) // 4)
    if xf < 4:
        buf.append(seg.copy())
        return seg, 0
    theta = np.linspace(0.0, np.pi * 0.5, xf, dtype=np.float32)[:, None]
    out_prev = prev.copy()
    out_prev[-xf:] = prev[-xf:] * np.cos(theta) + seg[:xf] * np.sin(theta)
    buf[-1] = out_prev
    buf.append(seg[xf:].copy())
    return seg, xf


def _detect_internal_long_pauses(slice_arr: np.ndarray, sr: int = SR, min_pause_sec: float = 0.45) -> List[float]:
    """检测单句内部的 >0.45s 卡顿犹豫气口位置（秒），供审片或纸面精剪定位。"""
    hop = int(0.020 * sr)
    n_hops = len(slice_arr) // hop
    if n_hops < 25:
        return []
    mono = slice_arr[: n_hops * hop].mean(axis=1).reshape(n_hops, hop)
    rms = np.sqrt(np.mean(mono ** 2, axis=1) + 1e-12)
    nz = rms[rms > 0.004]
    if len(nz) < 6:
        return []
    thr = max(0.004, float(np.percentile(nz, 60)) * 0.14)
    silent = rms < thr
    min_hops = int(round(min_pause_sec / 0.020))
    found: List[float] = []
    run_s = -1
    for hi, is_sil in enumerate(silent):
        if is_sil and run_s == -1:
            run_s = hi
        elif not is_sil and run_s != -1:
            # 只报句内中间停顿，不报句首/句尾
            if run_s >= 4 and hi <= n_hops - 4 and (hi - run_s) >= min_hops:
                found.append(round(run_s * 0.020, 2))
            run_s = -1
    return found


def _snap_tail_to_closed_mouth_valley(arr: np.ndarray, s1: int, sr: int = SR) -> int:
    """句尾切点在 ±45ms 内向能量最低谷+过零点微滑，减少张嘴/气音截断导致的口型硬跳。"""
    win = int(0.045 * sr)
    lo = max(1, s1 - win)
    hi = min(len(arr) - 1, s1 + win)
    if hi - lo < int(0.020 * sr):
        return snap_to_zero_crossing(arr, s1, int(0.010 * sr))
    hop = int(0.005 * sr)
    seg = arr[lo:hi].mean(axis=1)
    n_h = len(seg) // hop
    if n_h < 3:
        return snap_to_zero_crossing(arr, s1, int(0.010 * sr))
    rms = np.sqrt(np.mean(seg[: n_h * hop].reshape(n_h, hop) ** 2, axis=1) + 1e-12)
    valley_idx = lo + int(np.argmin(rms)) * hop + hop // 2
    return snap_to_zero_crossing(arr, valley_idx, int(0.008 * sr))


def run_step3(
    pipeline_dir: Path,
    track: str = "store",
    bgm_path: Optional[Path] = None,
    style_id: Optional[str] = None,
    pause_sec: float = 0.20,
    fps: int = DEFAULT_FPS,
    account_profile: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    pause_sec = max(0.14, min(0.32, pause_sec))
    if not style_id:
        try:
            from common import load_style_applied
            style_id = load_style_applied(Path(pipeline_dir))
        except Exception:
            style_id = None
    eff_prof = account_profile if (account_profile and account_profile.get("sample_sufficient") is True) else None
    if eff_prof and "pause_sec" in eff_prof:
        pause_sec = max(0.14, min(0.32, float(eff_prof["pause_sec"])))

    transcript = read_json(pipeline_dir / "02_transcript.json")
    raw_audio_cache: Dict[str, np.ndarray] = {}

    def get_audio(path_str: str) -> np.ndarray:
        if path_str not in raw_audio_cache:
            arr, _ = load_stereo_wav_np(Path(path_str))
            arr_fixed, _ = rescue_stereo_channels(arr, imbalance_db=8.0)
            raw_audio_cache[path_str] = arr_fixed
        return raw_audio_cache[path_str]

    if bgm_path is None:
        bgm_path = _resolve_default_bgm(track, style_id)
    elif not bgm_path.exists() and bgm_path.with_suffix(".m4a").exists():
        bgm_path = bgm_path.with_suffix(".m4a")
    if not bgm_path.exists():
        raise FileNotFoundError(f"内置 BGM 不存在，请先运行 check_and_setup.py --init-assets: {bgm_path}")

    bgm_arr, _ = load_stereo_wav_np(bgm_path)
    bgm_info = detect_bgm_beats(bgm_path, audio_arr=bgm_arr)

    all_segments = list(transcript.get("segments") or [])
    valid_segments = [s for s in all_segments if not s.get("is_filler", False) and str(s.get("text", "")).strip()]
    if not valid_segments:
        raise ValueError("转写稿过滤废话与淘汰 NG 后无有效口播句子。")

    # 谁在说话（2026-11-24）：主讲人对着无线麦，电平稳定；导演/摄影师在几米外插话，
    # 电平会掉 20–35dB。这是确定性信号，比「按文字猜」靠谱。硬档（≥18dB）的字一律不算口播。
    try:
        import off_mic as _OM
        # 整理成稿那一步（script_polish）已经标过就复用，别把 500 多秒音频再解一遍
        if any("off_mic_spans" in s for s in all_segments):
            _n = sum(len(s.get("off_mic_spans") or []) for s in all_segments)
            _sec = sum(float(sp.get("dur_sec") or 0.0) for s in all_segments for sp in (s.get("off_mic_spans") or []))
            _mic = {"spans": _n, "sec": round(_sec, 3), "hard_spans": _n, "hard_sec": round(_sec, 3),
                    "soft_spans": 0, "soft_sec": 0.0, "failures": []}
            print(f"[step3] 复用成稿阶段标好的离麦插话：{_n} 段 / {_sec:.1f} 秒")
        else:
            _mic = _OM.annotate_segments(all_segments)
        print(f"[step3] {_OM.format_report(_mic)}")
        for _f in (_mic.get("failures") or [])[:3]:
            print(f"[step3] [WARN] 离麦检测读不到音频：{_f}")
    except Exception as _e:
        print(f"[step3] [WARN] 离麦检测不可用（{_e}）")

    # 按 Agent 整理好的成稿剪（2026-11-24）：02c_script_final.json 是用户核过的那 10 句，
    # 其余 80 多句逐字稿是口癖、重拍版本和半截句。不认这份，砍掉的句子照样会被剪进去。
    try:
        import script_polish as _SP
        _plan = _SP.plan_lines(pipeline_dir)
    except Exception:
        _plan = []
    if _plan:
        _by_id = {str(s.get("id")): s for s in all_segments}
        _merged: List[Dict[str, Any]] = []
        _missing: List[str] = []
        for _ln in _plan:
            _segs = [_by_id[i] for i in _ln["segs"] if i in _by_id]
            _missing += [i for i in _ln["segs"] if i not in _by_id]
            if not _segs:
                continue
            _base = dict(_segs[0])
            _st = min(float(x["start_sec"]) for x in _segs)
            _en = max(float(x["end_sec"]) for x in _segs)
            _base.update({
                "start_sec": _st,
                "end_sec": _en,
                "text": _ln["text"],
                "is_filler": False,
                "plan_line": True,
                "plan_src_segs": [str(x.get("id")) for x in _segs],
                # 跨多段的成稿行要把各段的逐字时间戳接起来，否则只有第一段有词表，
                # 后半句的字幕只能按比例瞎分（2026-11-24 实测）
                "words": [w for x in _segs for w in (x.get("words") or [])],
                "off_mic_spans": [sp for x in _segs for sp in (x.get("off_mic_spans") or [])],
            })
            _gap = 0.0
            for _a, _b in zip(_segs, _segs[1:]):
                _gap = max(_gap, float(_b["start_sec"]) - float(_a["end_sec"]))
            if _gap > 1.5:
                print(f"[step3] [WARN] 这一行中间空了 {_gap:.1f}s（原声里有半句没上屏）：{_ln['text'][:18]}")
            _merged.append(_base)
        if _missing:
            print(f"[step3] [WARN] 成稿里有 {len(_missing)} 个转写段找不到：{_missing[:5]}")
        if _merged:
            # 2026-11-24：一行成稿是按「转写段」整段取的，段里往往夹着打板口令、重来的半句和
            # 现场其他人的话（用户实测：「还是混有大量导演/摄影师的声音」）。所以再按字级时间戳
            # 把成稿的每个字对回去，只保留对得上的那一小段，对不上的连画面一起砍。
            try:
                import script_align as _SA
                _expanded: List[Dict[str, Any]] = []
                _cut_total = 0.0
                _trim_notes: List[str] = []
                for _m in _merged:
                    _r = _SA.keep_intervals(
                        str(_m.get("text") or ""), _m.get("words") or [],
                        off_mic_spans=_m.get("off_mic_spans") or [],
                    )
                    _ivs = _r.get("intervals") or []
                    if not _ivs:
                        print(f"[step3] [WARN] 这一行对不上逐字稿，按原窗口保留：{str(_m.get('text'))[:16]}")
                        _expanded.append(_m)
                        continue
                    _win = float(_m["end_sec"]) - float(_m["start_sec"])
                    _kept = sum(float(x["end"]) - float(x["start"]) for x in _ivs)
                    _cut_total += max(0.0, _win - _kept)
                    if _r.get("low_coverage"):
                        _trim_notes.append(str(_r.get("reason") or ""))
                    for _iv in _ivs:
                        _sub = dict(_m)
                        _sub.update({
                            "start_sec": float(_iv["start"]),
                            "end_sec": float(_iv["end"]),
                            "text": str(_iv["text"]),
                            "words": list(_iv["words"]),
                            "plan_split": len(_ivs) > 1,
                            "plan_trim_head_sec": _r.get("head_trim_sec", 0.0),
                            "plan_trim_tail_sec": _r.get("tail_trim_sec", 0.0),
                        })
                        _expanded.append(_sub)
                if _expanded:
                    print(
                        f"[step3] 按成稿逐字对齐：砍掉 {_cut_total:.1f} 秒非成稿声音"
                        f"（口令/重来/插话/废开头），{len(_merged)} 行 → {len(_expanded)} 段"
                    )
                    for _t in dict.fromkeys(_trim_notes):
                        if _t:
                            print(f"[step3] [WARN] {_t}")
                    _merged = _expanded
            except Exception as _e:  # 对齐失败不许静默降级成「整段保留」而不说
                print(f"[step3] [WARN] 逐字对齐不可用（{_e}），退回按整段保留")
            _plan_dur = sum(float(x["end_sec"]) - float(x["start_sec"]) for x in _merged)
            _raw_dur = sum(float(x["end_sec"]) - float(x["start_sec"]) for x in valid_segments)
            print(f"[step3] 按成稿剪：{len(_merged)} 句 / {_plan_dur:.1f} 秒（原始逐字稿 {len(valid_segments)} 句 / {_raw_dur:.1f} 秒）")
            valid_segments = _merged

    for seg in all_segments:
        sa = seg.get("source_audio") or transcript.get("primary_audio")
        if sa and Path(sa).exists():
            get_audio(sa)
    # 屏蔽所有原始段落（含淘汰 NG / 导演插话），保证 Room Tone 绝对纯净！
    room_tone_bed, room_tone_source = extract_real_room_tone(raw_audio_cache, all_segments, SR)

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

    speech_islands_map = transcript.get("speech_islands") or {}
    prepared_slices: List[Tuple[np.ndarray, int, int]] = []
    raw_only_slices: List[np.ndarray] = []
    for seg in valid_segments:
        src_audio_path = seg.get("source_audio") or transcript["primary_audio"]
        src_arr = get_audio(src_audio_path)
        src_key = str(Path(src_audio_path).resolve()) if src_audio_path else ""
        islands = speech_islands_map.get(src_key) or []

        st_t = float(seg["start_sec"])
        en_t = float(seg["end_sec"])
        if islands:
            # 实拍口播切点优先落入语音能量岛静音中段并吸附视频帧网格
            cand_st = refine_cut_to_island_midpoint(st_t, islands, search_radius_sec=0.14, fps=fps)
            cand_en = refine_cut_to_island_midpoint(en_t, islands, search_radius_sec=0.16, fps=fps)
            if cand_en - cand_st >= max(0.25, (en_t - st_t) * 0.65):
                st_t, en_t = cand_st, cand_en
        else:
            st_t = snap_cut_to_frame_grid(st_t, fps=fps)
            en_t = snap_cut_to_frame_grid(en_t, fps=fps)

        raw_s0 = max(0, int(round(st_t * SR)))
        raw_s1 = min(len(src_arr), int(round(en_t * SR)))
        s0 = snap_to_zero_crossing(src_arr, raw_s0, int(0.010 * SR))
        s1 = _snap_tail_to_closed_mouth_valley(src_arr, raw_s1, SR)
        if s1 <= s0 + int(0.05 * SR):
            s0, s1 = raw_s0, raw_s1

        raw_slice = apply_edge_micro_fade(src_arr[s0:s1].copy(), fade_sec=0.010, sr=SR)
        prepared_slices.append((raw_slice, s0, s1))
        raw_only_slices.append(raw_slice)

    leveled_slices, leveling_info = level_speech_segments(raw_only_slices)

    # 真 Equal-Power 重叠相加拼接 (cos²θ + sin²θ = 1)：消除 24ms 零电平凹坑与底噪抽吸
    xf_joint = int(0.012 * SR)
    rt_offset = 0
    for s_idx, (seg, leveled_slice, (_, s0, s1)) in enumerate(
        zip(valid_segments, leveled_slices, prepared_slices), start=1
    ):
        sid = f"sent_{s_idx:03d}"

        chunk = leveled_slice.copy()
        if s_idx == 1:
            n_head = min(len(chunk) // 8, int(0.008 * SR))
            if n_head > 2:
                chunk[:n_head] *= np.sin(np.linspace(0.0, np.pi * 0.5, n_head, dtype=np.float32))[:, None]
        internal_pauses = _detect_internal_long_pauses(chunk, SR)
        _, ov_lead = _equal_power_append(voice_chunks, chunk, xf_joint)
        chunk_start_samples = max(0, cursor_samples - ov_lead)
        cursor_samples = chunk_start_samples + len(chunk)
        chunk_dur = len(chunk) / float(SR)

        new_start_sec = round(chunk_start_samples / float(SR), 4)
        new_end_sec = round(cursor_samples / float(SR), 4)

        cut_s0 = s0 / float(SR)
        cut_s1 = s1 / float(SR)
        cut_span = max(0.05, cut_s1 - cut_s0)
        new_words: List[Dict[str, Any]] = []
        keyword_anchors: List[float] = []
        accent_anchors: List[float] = []
        word_energy_peaks: List[Tuple[float, float, str]] = []
        for w in seg.get("words", []):
            rel_s = (float(w["start_sec"]) - cut_s0) / cut_span
            rel_e = (float(w["end_sec"]) - cut_s0) / cut_span
            ws = round(new_start_sec + max(0.0, min(1.0, rel_s)) * chunk_dur, 4)
            we = round(max(ws + 0.02, new_start_sec + max(0.0, min(1.0, rel_e)) * chunk_dur), 4)
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
        # 同一行成稿被切成几段时，段与段之间是「剪掉口令后的直接拼接」，
        # 中间不能再墊一个呼吸停顿，否则听起来像两句话（2026-11-24 实测）。
        if (
            next_seg
            and seg.get("plan_src_segs")
            and seg.get("plan_src_segs") == next_seg.get("plan_src_segs")
        ):
            cur_pause_sec, pause_reason = 0.0, "same_plan_line_join"

        sent_obj = {
            "id": sid,
            "text": seg["text"],
            "say_text": str(seg.get("say_text") or seg.get("text") or ""),
            "source_media": seg.get("source_media"),
            "voice_source": seg.get("voice_source"),
            "voice_source_offset_sec": seg.get("voice_source_offset_sec"),
            "source_video_grade": seg.get("source_video_grade", "usable"),
            "source_in_sec": round(s0 / float(SR), 4),
            "source_out_sec": round(s1 / float(SR), 4),
            "start_sec": new_start_sec,
            "end_sec": new_end_sec,
            "start_frame": sec_to_frames(new_start_sec, fps),
            "end_frame": sec_to_frames(new_end_sec, fps),
            "pause_after_sec": cur_pause_sec,
            "pause_reason": pause_reason,
            "internal_long_pauses": internal_pauses,
            "emotion_or_info": seg.get("emotion", "info"),
            # `keywords` 保留逐字稿原样：配镜/对位要用它（同段里别的词的语义线索是有用的）。
            "keywords": seg.get("keywords", []),
            # 但**上屏**必须只用出现这句正文里的词：原来直接把 keywords 拿去打花字，
            # 口令/废话带的词会跟着上屏（2026-11-24 实测：成片 61.5s 打出「三二一组」、
            # 43s 打出「组嗰边咧」——那两句的正文里根本没有这些字）。
            "display_keywords": _keywords_of_sentence(seg.get("text") or "", seg.get("keywords") or []),
            "words": new_words,
        }
        locked_sentences.append(sent_obj)

        if s_idx == 1:
            add_event("sentence_start", new_start_sec, "开篇首字对齐BGM强拍", sid, {"priority": "high"})
        else:
            add_event("sentence_start", new_start_sec, f"第{s_idx}句开始", sid)

        for k_t in keyword_anchors:
            add_event("keyword_accent", k_t, f"关键词重音 ({seg.get('emotion', 'info')})", sid)
            add_event("sfx_cue", k_t, "情绪/信息点音效落点", sid)

        for a_t in accent_anchors:
            if all(abs(a_t - kt) >= 0.45 for kt in keyword_anchors):
                add_event("stress", a_t, "语气重音峰", sid)

        if s_idx == 1 and word_energy_peaks:
            existing_3s = [t for t in (keyword_anchors + accent_anchors) if 0.50 <= t <= 2.75]
            if not existing_3s:
                in_win = [p for p in word_energy_peaks if 0.50 <= p[1] <= 2.75]
                if in_win:
                    in_win.sort(key=lambda x: -x[0])
                    add_event("stress", in_win[0][1], f"开篇实测能量峰值字: {in_win[0][2]}", sid)

        add_event("sentence_end", new_end_sec, f"第{s_idx}句结束", sid)

        if cur_pause_sec > 0.0 and s_idx < len(valid_segments):
            p_samples = int(round(cur_pause_sec * SR)) + xf_joint * 2
            if rt_offset + p_samples > len(room_tone_bed):
                rt_offset = 0
            p_buf = room_tone_bed[rt_offset : rt_offset + p_samples].copy()
            rt_offset += p_samples
            add_event("pause_start", new_end_sec, f"动态呼吸停顿({cur_pause_sec}s: {pause_reason})", sid)
            _, ov_p = _equal_power_append(voice_chunks, p_buf, xf_joint)
            cursor_samples = cursor_samples - ov_p + len(p_buf)
            eff_xf_next = min(xf_joint, len(p_buf) // 4)
            pause_end_sec = round(max(new_end_sec, (cursor_samples - eff_xf_next) / float(SR)), 4)
            add_event("pause_end", pause_end_sec, "句间停顿结束", sid)

    tail_samples = int(0.24 * SR)
    tail_rt = room_tone_bed[:tail_samples].copy()
    _equal_power_append(voice_chunks, tail_rt, xf_joint)
    locked_voice = np.concatenate(voice_chunks, axis=0)
    tail_fo = min(len(locked_voice) // 8, int(0.020 * SR))
    if tail_fo > 2:
        locked_voice[-tail_fo:] *= np.cos(np.linspace(0.0, np.pi * 0.5, tail_fo, dtype=np.float32))[:, None]
    total_samples = len(locked_voice)
    total_dur = round(total_samples / float(SR), 4)

    if len(aligned_bgm) < total_samples:
        beat_sec = 60.0 / max(40.0, float(bgm_info["bpm"]))
        bar_samples = int(round(beat_sec * 4.0 * SR))
        aligned_bgm, cycle_samples = _loop_bgm_exact_bars(aligned_bgm, bar_samples, total_samples)
        cycle_dur = cycle_samples / float(SR)
        base_beats = [t for t in shifted_beats if t < cycle_dur - 1e-4] or [round(i * beat_sec, 4) for i in range(int(cycle_dur / beat_sec))]
        base_downs = [t for t in shifted_downbeats if t < cycle_dur - 1e-4] or base_beats[::4]
        shifted_beats = []
        shifted_downbeats = []
        n_cycles = int(math.ceil(total_dur / max(0.1, cycle_dur)))
        for k_c in range(n_cycles):
            off = k_c * cycle_dur
            for b in base_beats:
                if off + b <= total_dur:
                    shifted_beats.append(round(off + b, 4))
            for d in base_downs:
                if off + d <= total_dur:
                    shifted_downbeats.append(round(off + d, 4))

    locked_bgm = aligned_bgm[:total_samples].copy()

    # 消费风格谱 dual_bgm_structure：开篇引子段切换至正文主 BGM（默认 0~2.83s 引子切主曲）
    dual_bgm_applied = False
    dual_bgm_switch_sec: Optional[float] = None
    intro_beats: List[float] = []
    intro_downs: List[float] = []
    active_sid = load_style_applied(pipeline_dir) or ("style_01" if track == "store" else "style_11")
    style_entry = load_style_spectrum_entry(active_sid) if active_sid else None
    dual_cfg = (style_entry or {}).get("dual_bgm_structure") or {}
    if bool(dual_cfg.get("enabled")) and total_dur >= 6.0:
        intro_sec = max(1.8, min(3.8, float(dual_cfg.get("intro_duration_sec") or 2.83)))
        snap_cands = [s["end_sec"] for s in locked_sentences if 1.6 <= s["end_sec"] <= 3.8]
        if snap_cands:
            intro_sec = min(snap_cands, key=lambda t: abs(t - intro_sec))
        intro_bgm_p = ASSETS_DIR / "sound_vibe" / "bgm" / "style_06_bgm.m4a"
        if intro_bgm_p.is_file() and intro_bgm_p.resolve() != bgm_path.resolve():
            try:
                intro_arr, _ = load_stereo_wav_np(intro_bgm_p)
                sw_i = min(total_samples - int(1.0 * SR), max(int(1.2 * SR), int(round(intro_sec * SR))))
                xf_b = min(int(0.22 * SR), sw_i // 3, (total_samples - sw_i) // 3)
                if len(intro_arr) >= sw_i + xf_b and xf_b >= 16:
                    i_seg = intro_arr[: sw_i + xf_b].copy()
                    i_rms = float(np.sqrt(np.mean(i_seg ** 2) + 1e-10))
                    m_rms = float(np.sqrt(np.mean(locked_bgm[: sw_i + xf_b] ** 2) + 1e-10))
                    i_seg *= min(1.8, max(0.4, (m_rms * 0.85) / max(1e-5, i_rms)))
                    th = np.linspace(0.0, np.pi * 0.5, xf_b, dtype=np.float32)[:, None]
                    locked_bgm[:sw_i] = i_seg[:sw_i]
                    locked_bgm[sw_i : sw_i + xf_b] = i_seg[sw_i : sw_i + xf_b] * np.cos(th) + locked_bgm[sw_i : sw_i + xf_b] * np.sin(th)
                    dual_bgm_applied = True
                    dual_bgm_switch_sec = round(sw_i / float(SR), 4)
                    intro_meta_p = ASSETS_DIR / "sound_vibe" / "bgm" / "style_06_bgm.json"
                    intro_meta = read_json(intro_meta_p) if intro_meta_p.is_file() else {}
                    i_beats_raw = intro_meta.get("beat_times_sec") or []
                    i_downs_raw = intro_meta.get("downbeat_times_sec") or []
                    intro_beats = [round(float(t), 4) for t in i_beats_raw if float(t) < dual_bgm_switch_sec - 0.05]
                    intro_downs = [round(float(t), 4) for t in i_downs_raw if float(t) < dual_bgm_switch_sec - 0.05]
                    add_event("bgm_section", dual_bgm_switch_sec, "双段BGM切换：引子转入正文主曲")
            except Exception:
                pass

    fade_out_n = min(len(locked_bgm), int(0.25 * SR))
    if fade_out_n > 2:
        locked_bgm[-fade_out_n:] *= np.linspace(1.0, 0.15, fade_out_n, dtype=np.float32)[:, None]

    main_beat_floor = (dual_bgm_switch_sec - 0.05) if (dual_bgm_applied and dual_bgm_switch_sec is not None) else 0.0
    eff_beats = (intro_beats + [b for b in shifted_beats if b >= main_beat_floor]) if dual_bgm_applied else shifted_beats
    eff_downs = (intro_downs + [d for d in shifted_downbeats if d >= main_beat_floor]) if dual_bgm_applied else shifted_downbeats
    downbeat_set = set(eff_downs)
    for b_idx, b_t in enumerate(eff_beats):
        if b_t <= min(4.0, total_dur - 0.1) and b_t not in downbeat_set:
            add_event("bgm_beat", b_t, f"BGM开篇第{b_idx+1}拍")
    for db_idx, db_t in enumerate(eff_downs):
        if db_t <= total_dur - 0.1:
            add_event("bgm_downbeat", db_t, f"BGM第{db_idx+1}小节强拍")
            if db_idx > 0 and db_idx % 4 == 0:
                add_event("bgm_section", db_t, f"BGM第{db_idx//4 + 1}乐段切换")

    events.sort(key=lambda e: (e["frame"], e["time_sec"], e["id"]))
    for ei, e in enumerate(events, start=1):
        e["id"] = f"evt_{ei:03d}"

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
        "total_duration_sec": total_dur,
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
            "dual_bgm_applied": dual_bgm_applied,
            "dual_bgm_switch_sec": dual_bgm_switch_sec,
            "beat_times_sec": [t for t in eff_beats if t <= total_dur],
            "downbeat_times_sec": [t for t in eff_downs if t <= total_dur],
            "hook_first_word_sec": locked_sentences[0]["start_sec"],
            "hook_aligned_downbeat_sec": 0.0,
        },
        "sentences": locked_sentences,
        "events": events,
    }
    write_json(pipeline_dir / "03_audio_events.json", audio_events)
    rt_cn = "已提取真实环境底噪" if room_tone_source == "extracted_real" else "柔和环境垫底"
    print(
        f"[step3] 音轨已锁定：有效 {len(locked_sentences)} 句，总长 {total_dur:.2f} 秒，"
        f"{rt_cn}，句间停顿 {audio_events['pause_range_sec']} 秒 -> 03_audio_events.json"
    )
    return audio_events


def snap_cut_to_frame_grid(t_sec: float, fps: int = DEFAULT_FPS) -> float:
    """将粗剪切点吸附至视频帧网格 round(t*fps)/fps，确保音画切点误差严格小于 1 帧。"""
    f = max(1, int(fps))
    return round(round(float(t_sec) * f) / float(f), 4)


def apply_edge_micro_fade(audio_48k: np.ndarray, fade_sec: float = 0.012, sr: int = SR) -> np.ndarray:
    """对单段硬切语音首尾施加 12ms 防爆音平滑淡入淡出（不做双端讲话交叉淡化，杜绝叠字）。"""
    if len(audio_48k) < 16:
        return audio_48k
    out = audio_48k.copy()
    fn = min(int(round(max(0.004, float(fade_sec)) * sr)), len(out) // 4)
    if fn >= 4:
        ramp = np.linspace(0.0, 1.0, fn, dtype=np.float32)[:, None]
        out[:fn] *= ramp
        out[-fn:] *= ramp[::-1]
    return out


def refine_cut_to_island_midpoint(
    cut_sec: float,
    islands: List[Dict[str, float]],
    search_radius_sec: float = 0.25,
    fps: int = DEFAULT_FPS,
) -> float:
    """将切点优先落在相邻语音能量岛之间的静音中段，再吸附帧网格，避开辅音起音与轻声尾音。"""
    if not islands:
        return snap_cut_to_frame_grid(cut_sec, fps=fps)
    best_t = float(cut_sec)
    best_dist = search_radius_sec + 1.0
    for a, b in zip(islands, islands[1:]):
        gap_s = float(a["end_sec"])
        gap_e = float(b["start_sec"])
        if gap_e - gap_s < 0.04:
            continue
        mid = (gap_s + gap_e) * 0.5
        d = abs(mid - cut_sec)
        if d <= search_radius_sec and d < best_dist:
            best_dist = d
            best_t = mid
    return snap_cut_to_frame_grid(best_t, fps=fps)


def patch_locked_audio_sentences(
    pipeline_dir: Path,
    patches: List[Dict[str, Any]],
    track: str = "store",
    fps: int = DEFAULT_FPS,
) -> Dict[str, Any]:
    """
    增量局部改句（实测 #13 根治）：
    仅替换指定 sentence_id 的单句文字与音频（支持传入 replacement_wav 或自动 edge-tts 局部补录），
    其余句子保持原封不动原声切片，随后平滑平移后续句子与全部音频事件时间轴并重裁 BGM 对齐总长。
    patches 每项格式：
      {"sentence_id": "sent_003", "new_text": "修改后的台词", "replacement_wav": "/可选/自录新句.wav", "voice": "可选TTS音色"}
      或 {"sentence_id": "sent_003", "delete": True} 删除该句。
    """
    ae_p = pipeline_dir / "03_audio_events.json"
    voice_p = pipeline_dir / "03_locked_voice.wav"
    bgm_p = pipeline_dir / "03_locked_bgm.wav"
    if not ae_p.is_file() or not voice_p.is_file():
        raise FileNotFoundError("未找到已锁定的 03_audio_events.json 或 03_locked_voice.wav，无法执行增量改句。")

    ae = read_json(ae_p)
    orig_voice, _ = load_stereo_wav_np(voice_p)
    patch_map = {str(p.get("sentence_id")): p for p in patches if p.get("sentence_id")}
    if not patch_map:
        return ae

    orig_sents = list(ae.get("sentences") or [])
    new_chunks: List[np.ndarray] = []
    new_sents: List[Dict[str, Any]] = []
    cursor_samples = 0
    patched_ids: List[str] = []
    deleted_ids: List[str] = []

    # 提取片头前导静音（若有）
    lead_n = int(round(float(orig_sents[0]["start_sec"]) * SR)) if orig_sents else 0
    if lead_n > 0:
        new_chunks.append(orig_voice[: min(len(orig_voice), lead_n)].copy())
        cursor_samples = len(new_chunks[-1])

    for idx, sent in enumerate(orig_sents):
        sid = str(sent.get("id") or f"sent_{idx + 1:03d}")
        s0 = max(0, min(len(orig_voice), int(round(float(sent.get("start_sec", 0.0)) * SR))))
        s1 = max(s0, min(len(orig_voice), int(round(float(sent.get("end_sec", 0.0)) * SR))))
        next_s0 = (
            max(s1, min(len(orig_voice), int(round(float(orig_sents[idx + 1].get("start_sec", 0.0)) * SR))))
            if idx + 1 < len(orig_sents)
            else len(orig_voice)
        )
        pause_slice = orig_voice[s1:next_s0].copy() if next_s0 > s1 else np.zeros((0, 2), dtype=np.float32)

        patch = patch_map.get(sid)
        if patch and patch.get("delete"):
            deleted_ids.append(sid)
            continue

        if patch:
            new_text = str(patch.get("new_text") or sent.get("text") or "").strip()
            rep_wav = patch.get("replacement_wav")
            if rep_wav and Path(str(rep_wav)).is_file():
                seg_arr, _ = load_stereo_wav_np(Path(str(rep_wav)))
            else:
                # 若未提供外部替换音频，则按需调用 edge-tts 单句补录并按原句音量匹配
                patch_dir = pipeline_dir / "02_tts_segments"
                patch_dir.mkdir(parents=True, exist_ok=True)
                tts_out = patch_dir / f"patch_{sid}.mp3"
                voice_name = str(patch.get("voice") or ("zh-CN-YunxiNeural" if track == "store" else "zh-CN-XiaoxiaoNeural"))
                try:
                    import asyncio
                    import edge_tts

                    async def _gen() -> None:
                        comm = edge_tts.Communicate(new_text, voice=voice_name)
                        await comm.save(str(tts_out))

                    asyncio.run(_gen())
                    seg_arr, _ = load_stereo_wav_np(tts_out)
                except Exception:
                    # 无网兜底：保留原音频切片，仅修正文字（供口误同音纠错）
                    seg_arr = orig_voice[s0:s1].copy()

            # 对齐原句参考 RMS，避免补录句突兀偏大/偏小
            orig_seg = orig_voice[s0:s1]
            if len(orig_seg) > 160 and len(seg_arr) > 160:
                ref_rms = float(np.sqrt(np.mean(orig_seg ** 2) + 1e-10))
                cur_rms = float(np.sqrt(np.mean(seg_arr ** 2) + 1e-10))
                if ref_rms > 1e-4 and cur_rms > 1e-4:
                    gain = max(0.35, min(2.8, ref_rms / cur_rms))
                    seg_arr = (0.95 * np.tanh((seg_arr * gain) / 0.95)).astype(np.float32)

            seg_arr = apply_edge_micro_fade(seg_arr, fade_sec=0.012, sr=SR)
            new_start_sec = round(cursor_samples / float(SR), 4)
            new_chunks.append(seg_arr)
            cursor_samples += len(seg_arr)
            new_end_sec = round(cursor_samples / float(SR), 4)
            updated_sent = dict(sent)
            updated_sent["text"] = new_text
            updated_sent["start_sec"] = new_start_sec
            updated_sent["end_sec"] = new_end_sec
            updated_sent["duration_sec"] = round(new_end_sec - new_start_sec, 4)
            updated_sent["patched"] = True
            # 若替换了音频，原逐字时间戳失效则按新字数均匀重估
            if rep_wav or (new_text != sent.get("text")):
                chars = [ch for ch in new_text if not ch.isspace()]
                if chars:
                    step_c = max(0.02, (new_end_sec - new_start_sec) / len(chars))
                    updated_sent["words"] = [
                        {
                            "word": ch,
                            "start_sec": round(new_start_sec + ci * step_c, 4),
                            "end_sec": round(min(new_end_sec, new_start_sec + (ci + 1) * step_c), 4),
                        }
                        for ci, ch in enumerate(chars)
                    ]
            new_sents.append(updated_sent)
            patched_ids.append(sid)
        else:
            seg_arr = orig_voice[s0:s1].copy()
            new_start_sec = round(cursor_samples / float(SR), 4)
            delta_shift = new_start_sec - float(sent.get("start_sec", 0.0))
            new_chunks.append(seg_arr)
            cursor_samples += len(seg_arr)
            new_end_sec = round(cursor_samples / float(SR), 4)
            updated_sent = dict(sent)
            updated_sent["start_sec"] = new_start_sec
            updated_sent["end_sec"] = new_end_sec
            updated_sent["duration_sec"] = round(new_end_sec - new_start_sec, 4)
            if abs(delta_shift) > 1e-4 and updated_sent.get("words"):
                shifted_words = []
                for w in updated_sent["words"]:
                    nw = dict(w)
                    nw["start_sec"] = round(max(0.0, float(w.get("start_sec", 0.0)) + delta_shift), 4)
                    nw["end_sec"] = round(max(nw["start_sec"], float(w.get("end_sec", 0.0)) + delta_shift), 4)
                    shifted_words.append(nw)
                updated_sent["words"] = shifted_words
            new_sents.append(updated_sent)

        if len(pause_slice) > 0:
            new_chunks.append(pause_slice)
            cursor_samples += len(pause_slice)

    if not new_sents:
        raise RuntimeError("增量改句后有效句子为 0，已拒绝覆盖原锁轨。")

    locked_voice = np.concatenate(new_chunks, axis=0) if new_chunks else np.zeros((SR, 2), dtype=np.float32)
    total_samples = len(locked_voice)
    total_dur = round(total_samples / float(SR), 4)
    save_stereo_wav_np(voice_p, locked_voice, SR)

    # 同步调整 BGM 长度至新总长
    # style_id 不在本函数签名里，从已落盘的风格应用记录读（读不到则按账号轨回落），
    # 供 _resolve_default_bgm 挑对应风格的原曲/替代乐段。
    style_id = load_style_applied(pipeline_dir) or ("style_01" if track == "store" else "style_11")
    bgm_src_file = Path(str((ae.get("bgm") or {}).get("file") or ""))
    if not bgm_src_file.is_file():
        bgm_src_file = bgm_p if bgm_p.is_file() else _resolve_default_bgm(track, style_id)
    bgm_arr, _ = load_stereo_wav_np(bgm_src_file)
    bpm = float((ae.get("bgm") or {}).get("bpm") or 120.0)
    bar_samples = int(round((60.0 / max(40.0, bpm)) * 4.0 * SR))
    if len(bgm_arr) < total_samples:
        bgm_arr, _ = _loop_bgm_exact_bars(bgm_arr, bar_samples, total_samples)
    locked_bgm = bgm_arr[:total_samples].copy()
    fo = min(len(locked_bgm), int(0.25 * SR))
    if fo > 2:
        locked_bgm[-fo:] *= np.linspace(1.0, 0.15, fo, dtype=np.float32)[:, None]
    save_stereo_wav_np(bgm_p, locked_bgm, SR)

    # 重建干净的句界与节拍事件表
    new_events: List[Dict[str, Any]] = []
    for si, s in enumerate(new_sents, start=1):
        new_events.append({
            "id": f"evt_{len(new_events)+1:03d}",
            "type": "sentence_start",
            "time_sec": s["start_sec"],
            "frame": sec_to_frames(s["start_sec"], fps),
            "sentence_id": s["id"],
            "description": f"第{si}句开始",
        })
        if si < len(new_sents):
            new_events.append({
                "id": f"evt_{len(new_events)+1:03d}",
                "type": "pause_start",
                "time_sec": s["end_sec"],
                "frame": sec_to_frames(s["end_sec"], fps),
                "sentence_id": s["id"],
                "description": f"第{si}句结束停顿",
            })
    for db_i, db_t in enumerate((ae.get("bgm") or {}).get("downbeat_times_sec") or []):
        if float(db_t) <= total_dur - 0.1:
            new_events.append({
                "id": f"evt_{len(new_events)+1:03d}",
                "type": "bgm_downbeat",
                "time_sec": round(float(db_t), 4),
                "frame": sec_to_frames(float(db_t), fps),
                "sentence_id": None,
                "description": f"BGM第{db_i+1}小节强拍",
            })
    new_events.sort(key=lambda e: (e["frame"], e["time_sec"]))
    for ei, e in enumerate(new_events, start=1):
        e["id"] = f"evt_{ei:03d}"

    ae["locked_duration_sec"] = total_dur
    ae["total_duration_sec"] = total_dur
    ae["locked_duration_frames"] = sec_to_frames(total_dur, fps)
    ae["sentences"] = new_sents
    ae["events"] = new_events
    ae["incremental_patch_history"] = list(ae.get("incremental_patch_history") or []) + [{
        "patched_ids": patched_ids,
        "deleted_ids": deleted_ids,
    }]
    write_json(ae_p, ae)

    ffmpeg_bin = find_bin("ffmpeg")
    blind_m4a = pipeline_dir / "03_blind_audio.m4a"
    if ffmpeg_bin:
        run_cmd([
            ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(voice_p), "-c:a", "aac", "-b:a", "192k", "-ar", str(SR),
            str(blind_m4a),
        ], check=False)

    print(
        f"[step3] 增量改句完成：替换 {len(patched_ids)} 句 {patched_ids}，删除 {len(deleted_ids)} 句 {deleted_ids}，"
        f"其余原声原封不动 -> 新总长 {total_dur:.2f}s"
    )
    return ae


def main() -> None:
    maybe_reexec_in_venv()
    parser = argparse.ArgumentParser(description="Step 3: 锁定音轨 (零拍漂Bgm循环 + 纯净RoomTone + 过零点精剪)")
    parser.add_argument("--pipeline-dir", required=True)
    parser.add_argument("--track", choices=["store", "creator"], default="store")
    parser.add_argument("--bgm", help="可选：自定义 BGM 音频路径")
    parser.add_argument("--pause-sec", type=float, default=0.20, help="基准句间停顿时长 (0.14-0.32s)")
    parser.add_argument("--account-profile", help="可选：account_profile.json 路径")
    parser.add_argument("--patch-json", help="可选：增量改句 JSON 路径（仅替换指定 sentence_id，不动其余原声）")
    args = parser.parse_args()
    if args.patch_json:
        patches_raw = read_json(Path(args.patch_json))
        patches = patches_raw if isinstance(patches_raw, list) else list(patches_raw.get("patches") or [])
        patch_locked_audio_sentences(Path(args.pipeline_dir), patches, track=args.track)
        return
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
