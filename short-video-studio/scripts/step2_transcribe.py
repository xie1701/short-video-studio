#!/usr/bin/env python3
"""
[Step 2: 语音转写、真实声学强制对齐、向量化 STFT 画外声剥离 & 多条 NG 择优]
v5 升级：
1. --script-json 同时兼容 {"sentences": [...]}, {"segments": [...]} 与 list；内嵌示范文案符合 <=16 字呼吸红线。
2. extract_auto_keywords 基于虚词/标点边界切分完整实词短语（禁止 4 字硬切残词）；Whisper 词表精准按词边界标 keyword。
3. 修正流水线次序：TTS 先过合规与语义校对、刷新 keywords 再合音（音字严密一致）；实拍先收紧静音边界再重算韵律/提词。
4. 按 source_audio 分组计算主讲 RMS 防止多机位误杀；双声道自动拯救无线麦单边；向量化 STFT 计算声学指纹。
"""

from __future__ import annotations

import argparse
import asyncio
import difflib
import json
import math
import os
import re
import sys
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCRIPTS_ROOT = Path(__file__).resolve().parent
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from common import (
    find_bin,
    get_wav_duration_sec,
    maybe_reexec_in_venv,
    read_json,
    read_wav_as_48k_stereo,
    rescue_stereo_channels,
    run_cmd,
    write_json,
)

maybe_reexec_in_venv(("faster_whisper",))

import numpy as np

FILLER_WORDS = {"呃", "嗯", "那个", "就是说", "然后呢", "啊", "这个", "哎呀"}
DIRECTOR_CUE_EXACT = {
    "ok", "好", "对", "来", "开始", "重来", "三二一", "321", "action",
    "等一下", "再讲一遍", "再说一遍", "再试一次", "准备", "可以", "卡", "过", "中间",
}

DEFAULT_STORE_SCRIPT = [
    {"text": "价格越高的菜，利润反而越低。", "keywords": ["价格越高", "利润"], "is_filler": False, "emotion": "hook"},
    {"text": "呃那个我先说一下，", "keywords": [], "is_filler": True, "emotion": "filler"},
    {"text": "清晨市场带回的鲜羊排，成本花了三千块。", "keywords": ["鲜羊排", "三千块"], "is_filler": False, "emotion": "info"},
    {"text": "看起来卖四百能赚一百多，", "keywords": ["卖四百"], "is_filler": False, "emotion": "info"},
    {"text": "其实要腌两小时，炭火再烤三小时。", "keywords": ["两小时", "三小时"], "is_filler": False, "emotion": "detail"},
    {"text": "开了八年烧烤店，绝不用冻肉糊弄人。", "keywords": ["八年", "绝不用冻肉"], "is_filler": False, "emotion": "persona"},
    {"text": "想吃鲜嫩烤羊排，评论区发你门店地址。", "keywords": ["评论区", "门店地址"], "is_filler": False, "emotion": "cta"},
]

DEFAULT_CREATOR_SCRIPT = [
    {"text": "做短视频，懂越多反而越做不好。", "keywords": ["短视频", "越做不好"], "is_filler": False, "emotion": "hook"},
    {"text": "呃就是说，", "keywords": [], "is_filler": True, "emotion": "filler"},
    {"text": "八成新人把时间全耗在了学转场上。", "keywords": ["八成新人", "学转场"], "is_filler": False, "emotion": "info"},
    {"text": "决定完播率的，其实只有画面密度和节奏。", "keywords": ["完播率", "画面密度"], "is_filler": False, "emotion": "twist"},
    {"text": "先锁死声音，再贴上镜头，记住这条铁律。", "keywords": ["声音", "铁律"], "is_filler": False, "emotion": "persona"},
    {"text": "写文案还是剪片子最难？评论区聊聊。", "keywords": ["评论区"], "is_filler": False, "emotion": "cta"},
]

_WHISPER_MODEL_CACHE: Dict[str, Any] = {}
_ASR_STOP_WORDS = {
    "我们", "你们", "他们", "这个", "那个", "就是", "其实", "然后", "因为", "所以",
    "可以", "大家", "什么", "怎么", "一个", "没有", "还是", "觉得", "如果", "自己", "为什么",
}
_PUNCT_STRIP_SET = set(" \t\r\n，。！？、；：“”‘’（）().,!?;:\"'《》【】[]")


def _clean_zh_text(text: str) -> str:
    return re.sub(r"[\s，。！？、；：“”‘’（）().,!?;:\"'《》【】\[\]]+", "", text).lower()


def is_filler_or_director_cue(text: str) -> bool:
    cleaned = _clean_zh_text(text)
    if not cleaned:
        return True
    if cleaned in DIRECTOR_CUE_EXACT:
        return True
    return any(fw == cleaned or (len(cleaned) <= 5 and fw in cleaned) for fw in FILLER_WORDS)


def extract_auto_keywords(text: str) -> List[str]:
    """按标点/虚词边界与数字量词模式提取完整中文实词短语，绝不 4 字贪婪盲切半截词。"""
    kws: List[str] = []
    # 1. 优先捕获带单位的数字或时间词
    for m in re.finditer(r"([0-9一二三四五六七八九十百千万两半\.]+[元块万千百十个年天月日%倍次度件斤克米小时分钟秒]+)", text):
        tok = m.group(1)
        if tok not in kws:
            kws.append(tok)
    # 2. 按虚词、连接词及标点分割语义块，从连续块尾提取 2–4 字完整名动词
    clauses = re.split(r"[，。！？、；：“”‘’（）().,!?;:\s]+|[的了吗呢吧啊呀就把被给从向用凭靠为和与跟而却才都还很太更最]+", text)
    for cl in clauses:
        sub = "".join(ch for ch in cl if "\u4e00" <= ch <= "\u9fff")
        if len(sub) < 2:
            continue
        tok = sub if len(sub) <= 4 else sub[-4:]
        if tok not in _ASR_STOP_WORDS and tok not in kws and all(tok not in existing for existing in kws):
            kws.append(tok)
    return kws[:3]


def _is_content_char_at(text: str, idx: int) -> bool:
    ch = text[idx]
    if ch.isspace():
        return False
    if ch in ".:：" and 0 < idx < len(text) - 1 and text[idx - 1].isdigit() and text[idx + 1].isdigit():
        return True
    return ch not in _PUNCT_STRIP_SET


def _extract_clean_chars(text: str) -> List[str]:
    """抽取正文字符流：剔除常规标点，但保留数字间的 `.` / `:`。"""
    out: List[str] = []
    for idx_c, ch in enumerate(text):
        if not _is_content_char_at(text, idx_c):
            continue
        out.append(":" if (ch == "：" and 0 < idx_c < len(text) - 1 and text[idx_c - 1].isdigit() and text[idx_c + 1].isdigit()) else ch)
    return out


def _word_content_spans(words: List[Dict[str, Any]]) -> Tuple[List[str], List[int]]:
    """按整句拼合边界计算每个 word 的有效字符与有效长度（兼容跨 token 的 18:30）。"""
    raw_strs = [str(w.get("word", "")) for w in words]
    joined = "".join(raw_strs)
    tokens: List[str] = []
    lengths: List[int] = []
    off = 0
    for rs in raw_strs:
        buf: List[str] = []
        for k in range(len(rs)):
            gi = off + k
            if _is_content_char_at(joined, gi):
                ch = joined[gi]
                buf.append(":" if ch == "：" and 0 < gi < len(joined) - 1 and joined[gi - 1].isdigit() and joined[gi + 1].isdigit() else ch)
        tok = "".join(buf)
        tokens.append(tok)
        lengths.append(len(tok))
        off += len(rs)
    return tokens, lengths


def _mark_word_keywords(words: List[Dict[str, Any]], keywords: List[str]) -> None:
    """严格按跨词拼合位置精确标记 is_keyword，彻底杜绝单字巧合命中全局变黄。"""
    if not words or not keywords:
        return
    clean_tokens, _ = _word_content_spans(words)
    spans: List[Tuple[int, int]] = []
    pos = 0
    for w, wt_c in zip(words, clean_tokens):
        spans.append((pos, pos + len(wt_c)))
        pos += len(wt_c)
        w["is_keyword"] = False

    joined = "".join(clean_tokens)
    for kw in keywords:
        kw_c = "".join(_extract_clean_chars(str(kw)))
        if len(kw_c) < 2:
            continue
        start_p = joined.find(kw_c)
        while start_p != -1:
            end_p = start_p + len(kw_c)
            for idx, (s_i, e_i) in enumerate(spans):
                if e_i > s_i and s_i < end_p and e_i > start_p:
                    words[idx]["is_keyword"] = True
            start_p = joined.find(kw_c, start_p + 1)
    for w in words:
        if w.get("is_keyword"):
            w["is_accent"] = True


def load_wav_16k_mono(wav_path: Path) -> Tuple[np.ndarray, int]:
    """统一复用 common.read_wav_as_48k_stereo(target_sr=16000) 并自动拯救单边无线麦故障。"""
    arr, sr = read_wav_as_48k_stereo(wav_path, target_sr=16000)
    if len(arr) > 0:
        arr_fixed, _ = rescue_stereo_channels(arr, imbalance_db=8.0)
        mono = arr_fixed.mean(axis=1).astype(np.float32)
    else:
        mono = np.zeros(0, dtype=np.float32)
    return mono, sr


def compute_10ms_rms(y16: np.ndarray, sr: int = 16000) -> np.ndarray:
    hop = int(sr * 0.010)
    n_hops = max(1, len(y16) // hop)
    trimmed = y16[: n_hops * hop].reshape(n_hops, hop)
    return np.sqrt(np.mean(trimmed ** 2, axis=1) + 1e-10)


def split_chars_energy_aligned(
    text: str,
    start_sec: float,
    end_sec: float,
    keywords: List[str],
    rms_10ms: Optional[np.ndarray] = None,
) -> List[Dict[str, Any]]:
    """基于 10ms 能量包络累积分布将字符映射至声学活动区间，保留数字间小数点/冒号。"""
    clean_chars = _extract_clean_chars(text)

    if not clean_chars:
        return []
    n_c = len(clean_chars)
    total_dur = max(0.03 * n_c, end_sec - start_sec)
    eff_end = start_sec + total_dur if end_sec < start_sec + 0.02 * n_c else end_sec

    if rms_10ms is not None and len(rms_10ms) >= 4:
        i0 = max(0, int(round(start_sec * 100)))
        i1 = min(len(rms_10ms), max(i0 + 2, int(round(eff_end * 100))))
        seg_env = rms_10ms[i0:i1]
        if len(seg_env) >= 2 and float(np.max(seg_env)) > 1e-4:
            weights = seg_env + 0.18 * float(np.mean(seg_env) + 1e-5)
            cdf = np.concatenate([[0.0], np.cumsum(weights)])
            cdf /= max(1e-8, float(cdf[-1]))
            t_grid = np.linspace(start_sec, eff_end, len(cdf))
            q_pts = np.linspace(0.0, 1.0, n_c + 1)
            bounds = np.interp(q_pts, cdf, t_grid)
        else:
            bounds = np.linspace(start_sec, eff_end, n_c + 1)
    else:
        bounds = np.linspace(start_sec, eff_end, n_c + 1)

    min_step = min(0.02, (eff_end - start_sec) / max(1, n_c))
    for i in range(1, len(bounds)):
        bounds[i] = max(bounds[i], bounds[i - 1] + min_step)
    if bounds[-1] > eff_end:
        span_cur = max(1e-6, bounds[-1] - bounds[0])
        bounds = bounds[0] + (bounds - bounds[0]) * ((eff_end - bounds[0]) / span_cur)

    words: List[Dict[str, Any]] = []
    for i, ch in enumerate(clean_chars):
        ws = round(float(bounds[i]), 4)
        we = round(float(bounds[i + 1]), 4)
        if we <= ws:
            we = round(ws + 0.005, 4)
        words.append({
            "word": ch,
            "start_sec": ws,
            "end_sec": we,
            "is_keyword": False,
            "is_accent": False,
            "energy": 0.75,
        })
    _mark_word_keywords(words, keywords)
    return words


def compute_prosody_for_segments(
    wav_path: Path,
    segments: List[Dict[str, Any]],
    audio_pair: Optional[Tuple[np.ndarray, int]] = None,
) -> None:
    """基于收紧后有效语音段计算 RMS 与 YIN F0 声学重音（只对有效段算 F0 提速并精准）。"""
    active_segs = [s for s in segments if not s.get("is_filler") and (s.get("words") or [])]
    if not active_segs:
        return
    if audio_pair is None:
        if not wav_path.exists():
            return
        y16, sr = load_wav_16k_mono(wav_path)
    else:
        y16, sr = audio_pair
    if len(y16) < 400:
        return
    rms_10ms = compute_10ms_rms(y16, sr)

    try:
        import librosa
        has_librosa = True
    except Exception:
        has_librosa = False

    for seg in active_segs:
        words = seg.get("words") or []
        s0 = max(0, int(round(float(seg["start_sec"]) * 100)))
        s1 = min(len(rms_10ms), max(s0 + 1, int(round(float(seg["end_sec"]) * 100))))
        seg_rms = rms_10ms[s0:s1]
        med_rms = float(np.median(seg_rms[seg_rms > 0.005])) if np.any(seg_rms > 0.005) else float(np.mean(seg_rms) + 1e-5)

        seg_f0_arr: Optional[np.ndarray] = None
        seg_f0_med = 0.0
        a0 = max(0, int(round(float(seg["start_sec"]) * sr)))
        a1 = min(len(y16), int(round(float(seg["end_sec"]) * sr)))
        if has_librosa and a1 - a0 >= 1024:
            try:
                f0 = librosa.yin(y16[a0:a1].astype(np.float64), fmin=70.0, fmax=420.0, sr=sr, frame_length=1024, hop_length=160)
                f0 = np.where((f0 >= 75.0) & (f0 <= 390.0), f0, 0.0)
                seg_f0_arr = f0.astype(np.float32)
                vf0 = seg_f0_arr[seg_f0_arr > 0]
                if len(vf0) >= 3:
                    seg_f0_med = float(np.median(vf0))
            except Exception:
                seg_f0_arr = None

        for w in words:
            w0 = max(0, int(round(float(w["start_sec"]) * 100)))
            w1 = min(len(rms_10ms), max(w0 + 1, int(round(float(w["end_sec"]) * 100))))
            w_rms = float(np.percentile(rms_10ms[w0:w1], 80))
            rms_ratio_db = 20.0 * math.log10(max(1e-5, w_rms) / max(1e-5, med_rms))
            rms_score = max(0.15, min(1.0, 0.62 + rms_ratio_db / 14.0))

            f0_score = 0.5
            w_f0 = 0.0
            if seg_f0_arr is not None:
                lw0 = max(0, w0 - s0)
                lw1 = min(len(seg_f0_arr), max(lw0 + 1, w1 - s0))
                wf = seg_f0_arr[lw0:lw1]
                wf = wf[wf > 0]
                if len(wf) > 0 and seg_f0_med > 0:
                    w_f0 = float(np.median(wf))
                    f0_lift = (w_f0 - seg_f0_med) / max(1.0, seg_f0_med)
                    f0_score = max(0.2, min(1.0, 0.55 + f0_lift * 1.4))

            prosody_e = max(0.2, min(1.0, 0.68 * rms_score + 0.32 * f0_score))
            w["energy"] = round(prosody_e, 3)
            if w_f0 > 0:
                w["f0_hz"] = round(w_f0, 1)
            w["is_accent"] = bool(prosody_e >= 0.78 or w.get("is_keyword", False))


def vad_speech_intervals(y16: np.ndarray, sr: int = 16000) -> List[Tuple[float, float]]:
    """基于 20ms RMS 的纯物理语音活动区间检测 (VAD)。"""
    hop = int(sr * 0.020)
    n_hops = max(1, len(y16) // hop)
    rms = np.sqrt(np.mean(y16[: n_hops * hop].reshape(n_hops, hop) ** 2, axis=1) + 1e-10)
    noise_floor = float(np.percentile(rms, 20))
    thresh = max(0.008, noise_floor * 2.4)
    active = rms >= thresh

    raw_spans: List[Tuple[float, float]] = []
    in_seg = False
    st = 0
    for i, act in enumerate(active):
        if act and not in_seg:
            in_seg = True
            st = i
        elif not act and in_seg:
            in_seg = False
            raw_spans.append((st * 0.020, i * 0.020))
    if in_seg:
        raw_spans.append((st * 0.020, n_hops * 0.020))

    merged: List[Tuple[float, float]] = []
    for s, e in raw_spans:
        if merged and s - merged[-1][1] < 0.18:
            merged[-1] = (merged[-1][0], e)
        else:
            merged.append((s, e))
    return [(round(s, 4), round(e, 4)) for s, e in merged if e - s >= 0.16]


def get_huggingface_hub_dir() -> Path:
    if os.environ.get("HF_HUB_CACHE"):
        return Path(os.environ["HF_HUB_CACHE"]).expanduser()
    if os.environ.get("HF_HOME"):
        return Path(os.environ["HF_HOME"]).expanduser() / "hub"
    return Path.home() / ".cache" / "huggingface" / "hub"


def _find_cached_whisper_model() -> Optional[str]:
    hf_hub = get_huggingface_hub_dir()
    for name in ("large-v3", "large-v3-turbo", "medium", "small", "base", "tiny"):
        d = hf_hub / f"models--Systran--faster-whisper-{name}"
        if (d / "snapshots").is_dir() and any((d / "snapshots").iterdir()):
            return name
    return None


def _get_whisper_model(model_name: str):
    if model_name in _WHISPER_MODEL_CACHE:
        return _WHISPER_MODEL_CACHE[model_name]
    from faster_whisper import WhisperModel
    m = WhisperModel(model_name, device="cpu", compute_type="int8", local_files_only=True)
    _WHISPER_MODEL_CACHE[model_name] = m
    return m


def try_faster_whisper(
    wav_path: Path,
    model_name: Optional[str] = None,
    y16_cached: Optional[np.ndarray] = None,
) -> Optional[List[Dict[str, Any]]]:
    """使用模块缓存 Faster-Whisper 提取词级时间戳；严格检查本地缓存与有效语音能量防静音幻觉。"""
    try:
        y16 = y16_cached if y16_cached is not None else load_wav_16k_mono(wav_path)[0]
        global_rms = float(np.sqrt(np.mean(y16 ** 2) + 1e-12)) if len(y16) else 0.0
        if global_rms < 0.002 or not vad_speech_intervals(y16, 16000):
            return []
        chosen = model_name or _find_cached_whisper_model()
        if not chosen:
            return None
        model = _get_whisper_model(chosen)
        segments, _ = model.transcribe(
            str(wav_path),
            language="zh",
            initial_prompt="以下是普通话简体中文口播内容：",
            word_timestamps=True,
            vad_filter=True,
        )
        rms_10ms = compute_10ms_rms(y16)
        out = []
        for idx, seg in enumerate(segments, start=1):
            text = seg.text.strip()
            if not text:
                continue
            is_filler = is_filler_or_director_cue(text)
            kws = [] if is_filler else extract_auto_keywords(text)
            words = []
            for w in getattr(seg, "words", []) or []:
                wt = w.word.strip()
                if not wt:
                    continue
                words.append({
                    "word": wt,
                    "start_sec": round(float(w.start), 4),
                    "end_sec": round(float(w.end), 4),
                    "is_keyword": False,
                    "is_accent": False,
                    "energy": 0.75,
                })
            if words:
                _mark_word_keywords(words, kws)
            else:
                words = split_chars_energy_aligned(text, float(seg.start), float(seg.end), kws, rms_10ms=rms_10ms)
            out.append({
                "id": f"seg_{idx:03d}",
                "text": text,
                "start_sec": round(float(seg.start), 4),
                "end_sec": round(float(seg.end), 4),
                "is_filler": is_filler,
                "emotion": "filler" if is_filler else "info",
                "keywords": kws,
                "words": words,
            })
        return out if out else None
    except Exception:
        return None


def align_script_to_existing_wav(
    wav_path: Path,
    script_items: List[Dict[str, Any]],
    source_media: str,
    model_name: Optional[str] = None,
    audio_pair: Optional[Tuple[np.ndarray, int]] = None,
) -> Tuple[List[Dict[str, Any]], str]:
    """将已知台词对齐到现有 WAV：优先使用 Whisper 句界/字长局部比例投影，退化才用 VAD。"""
    y16, sr = audio_pair if audio_pair is not None else load_wav_16k_mono(wav_path)
    rms_10ms = compute_10ms_rms(y16, sr)
    total_dur = len(y16) / float(sr) if sr > 0 else 0.0

    asr_segs = try_faster_whisper(wav_path, model_name=model_name, y16_cached=y16)
    if asr_segs:
        core_asr = [s for s in asr_segs if not is_filler_or_director_cue(s["text"])] or asr_segs
        if len(core_asr) == len(script_items):
            segments: List[Dict[str, Any]] = []
            for idx, (item, aseg) in enumerate(zip(script_items, core_asr), start=1):
                s_sec, e_sec = float(aseg["start_sec"]), float(aseg["end_sec"])
                kws = item.get("keywords", []) or extract_auto_keywords(item["text"])
                is_f = bool(item.get("is_filler", False)) or is_filler_or_director_cue(item["text"])
                segments.append({
                    "id": f"seg_{idx:03d}",
                    "text": item["text"],
                    "start_sec": round(s_sec, 4),
                    "end_sec": round(e_sec, 4),
                    "source_media": source_media,
                    "source_audio": str(wav_path.resolve()),
                    "is_filler": is_f,
                    "emotion": "filler" if is_f else item.get("emotion", "info"),
                    "keywords": kws,
                    "words": split_chars_energy_aligned(item["text"], s_sec, e_sec, kws, rms_10ms=rms_10ms),
                })
            return segments, "asr_script_forced"
        # 句数略有差异时保留 Whisper 整体活动区间 [(s,e)...] 做字符配比，比纯能量 VAD 更精准！
        vad_spans = [(float(s["start_sec"]), float(s["end_sec"])) for s in core_asr if float(s["end_sec"]) > float(s["start_sec"])]
        mode_tag = "asr_char_mapped"
    else:
        vad_spans = vad_speech_intervals(y16, sr)
        mode_tag = "vad_energy_aligned"

    if not vad_spans:
        vad_spans = [(0.0, total_dur)]

    active_total = sum(e - s for s, e in vad_spans)
    char_counts = [max(2, len(_clean_zh_text(it["text"]))) for it in script_items]
    total_chars = max(1, sum(char_counts))

    cum_virt = [0.0]
    for s, e in vad_spans:
        cum_virt.append(cum_virt[-1] + (e - s))

    def virt_to_real(vt: float) -> float:
        vt = max(0.0, min(active_total, vt))
        for idx, (s, e) in enumerate(vad_spans):
            if vt <= cum_virt[idx + 1] + 1e-6:
                return round(s + (vt - cum_virt[idx]), 4)
        return round(vad_spans[-1][1], 4)

    segments = []
    v_cursor = 0.0
    for idx, (item, cc) in enumerate(zip(script_items, char_counts), start=1):
        v_dur = active_total * (cc / float(total_chars))
        s_sec = virt_to_real(v_cursor)
        e_sec = virt_to_real(min(active_total, v_cursor + v_dur))
        v_cursor += v_dur
        if e_sec <= s_sec:
            e_sec = round(min(total_dur, s_sec + 0.25), 4)
        kws = item.get("keywords", []) or extract_auto_keywords(item["text"])
        is_f = bool(item.get("is_filler", False)) or is_filler_or_director_cue(item["text"])
        segments.append({
            "id": f"seg_{idx:03d}",
            "text": item["text"],
            "start_sec": s_sec,
            "end_sec": e_sec,
            "source_media": source_media,
            "source_audio": str(wav_path.resolve()),
            "is_filler": is_f,
            "emotion": "filler" if is_f else item.get("emotion", "info"),
            "keywords": kws,
            "words": split_chars_energy_aligned(item["text"], s_sec, e_sec, kws, rms_10ms=rms_10ms),
        })
    return segments, mode_tag


def _extract_segment_acoustic_sig(y16: np.ndarray, sr: int, s_sec: float, e_sec: float) -> Dict[str, Any]:
    """向量化 STFT 计算整句有效发声帧的 8 频段对数能量轮廓、质心与句内卡壳停顿。"""
    i0 = max(0, int(round(s_sec * sr)))
    i1 = min(len(y16), max(i0 + 400, int(round(e_sec * sr))))
    seg = y16[i0:i1]
    if len(seg) < 400:
        return {"rms_db": -60.0, "centroid": 0.0, "bands": np.zeros(8, dtype=np.float32), "internal_pauses": 0}

    frame_len = min(400, len(seg))
    hop = 160
    n_frames = 1 + (len(seg) - frame_len) // hop
    if n_frames <= 0:
        return {"rms_db": -60.0, "centroid": 0.0, "bands": np.zeros(8, dtype=np.float32), "internal_pauses": 0}

    strides = seg.strides[0]
    frames = np.lib.stride_tricks.as_strided(seg, shape=(n_frames, frame_len), strides=(hop * strides, strides))
    rms_arr = np.sqrt(np.mean(frames ** 2, axis=1) + 1e-12)
    win = np.hanning(frame_len).astype(np.float32)
    spec = np.abs(np.fft.rfft(frames * win, axis=1))
    freqs = np.fft.rfftfreq(frame_len, 1.0 / sr)
    tot_e = np.sum(spec, axis=1, keepdims=True) + 1e-8
    cent_arr = np.sum(spec * freqs[None, :], axis=1) / tot_e[:, 0]

    band_edges = [80, 220, 450, 850, 1400, 2200, 3500, 5200, 7600]
    b_cols = []
    for bi in range(len(band_edges) - 1):
        m = (freqs >= band_edges[bi]) & (freqs < band_edges[bi + 1])
        b_cols.append(np.sum(spec[:, m], axis=1) / tot_e[:, 0])
    bands_mat = np.stack(b_cols, axis=1)  # (n_frames, 8)
    bands_log = np.log1p(bands_mat * 30.0)
    bands_log /= np.maximum(1e-6, np.linalg.norm(bands_log, axis=1, keepdims=True))

    peak_r = float(np.max(rms_arr))
    active_mask = rms_arr >= max(0.004, peak_r * 0.18)
    if not np.any(active_mask):
        active_mask = np.ones_like(rms_arr, dtype=bool)

    eff_rms = float(np.median(rms_arr[active_mask]))
    rms_db = 20.0 * math.log10(max(1e-6, eff_rms))
    bands_mean = np.mean(bands_log[active_mask], axis=0)
    centroid = float(np.median(cent_arr[active_mask]))

    sil_gaps = 0
    in_gap = False
    gap_len = 0
    sil_thr = max(0.003, eff_rms * 0.25)
    for r in rms_arr[::2]:
        if r < sil_thr:
            gap_len = gap_len + 1 if in_gap else 1
            in_gap = True
        else:
            if in_gap and gap_len >= 7:
                sil_gaps += 1
            in_gap = False
            gap_len = 0

    return {
        "rms_db": round(rms_db, 2),
        "centroid": round(centroid, 1),
        "bands": bands_mean.astype(np.float32),
        "internal_pauses": sil_gaps,
    }


def filter_offcamera_and_retakes(
    segments: List[Dict[str, Any]],
    audio_cache: Optional[Dict[str, Tuple[np.ndarray, int]]] = None,
) -> Dict[str, int]:
    """按 source_audio 各自独立分组剥离画外远场导演声，并严谨收敛 NG 重复重录择优。"""
    cache = audio_cache if audio_cache is not None else {}
    sigs: List[Optional[Dict[str, Any]]] = []
    by_source: Dict[str, List[int]] = {}

    for idx, seg in enumerate(segments):
        sa = seg.get("source_audio")
        if sa and Path(sa).exists():
            sa_key = str(Path(sa).resolve())
            if sa_key not in cache:
                cache[sa_key] = load_wav_16k_mono(Path(sa))
            y16, sr = cache[sa_key]
            sigs.append(_extract_segment_acoustic_sig(y16, sr, float(seg["start_sec"]), float(seg["end_sec"])))
            by_source.setdefault(sa_key, []).append(idx)
        else:
            sigs.append(None)

    offcam_dropped = 0
    primary_rms_global = -20.0
    for sa_key, idx_list in by_source.items():
        valid_rms = sorted([sigs[i]["rms_db"] for i in idx_list if sigs[i] and not segments[i].get("is_filler")], reverse=True)
        if len(valid_rms) < 3:
            if valid_rms:
                primary_rms_global = max(primary_rms_global, valid_rms[0])
            continue
        top_n = max(2, int(math.ceil(len(valid_rms) * 0.65)))
        primary_rms = float(np.median(valid_rms[:top_n]))
        primary_rms_global = primary_rms
        core_bands = [
            sigs[i]["bands"] for i in idx_list
            if sigs[i] and not segments[i].get("is_filler") and sigs[i]["rms_db"] >= primary_rms - 6.0
        ]
        primary_band = np.mean(core_bands, axis=0) if core_bands else None

        for i in idx_list:
            seg, sg = segments[i], sigs[i]
            if seg.get("is_filler") or not sg:
                continue
            seg["acoustic_rms_db"] = sg["rms_db"]
            db_drop = primary_rms - sg["rms_db"]
            spec_dist = float(np.linalg.norm(sg["bands"] - primary_band)) if primary_band is not None else 0.0
            if (db_drop >= 9.0 and spec_dist >= 0.14) or db_drop >= 12.0:
                seg["is_filler"] = True
                seg["emotion"] = "filler"
                seg["drop_reason"] = f"offcamera_speaker (rms_drop={db_drop:.1f}dB, spec_dist={spec_dist:.2f})"
                offcam_dropped += 1

    active_indices = [i for i, s in enumerate(segments) if not s.get("is_filler")]
    retake_dropped = 0
    for pos_a in range(len(active_indices)):
        i = active_indices[pos_a]
        if segments[i].get("is_filler"):
            continue
        ci = _clean_zh_text(segments[i]["text"])
        if len(ci) < 4:
            continue

        group = [i]
        for pos_b in range(pos_a + 1, min(len(active_indices), pos_a + 4)):
            j = active_indices[pos_b]
            if segments[j].get("is_filler"):
                continue
            cj = _clean_zh_text(segments[j]["text"])
            if len(cj) < 4:
                continue
            sim = difflib.SequenceMatcher(None, ci, cj).ratio()
            common_pref = 0
            for a_c, b_c in zip(ci, cj):
                if a_c == b_c:
                    common_pref += 1
                else:
                    break
            # 防排比句误杀：需整句高相似 >=0.76，或前缀重合>=5字且后句明显覆盖前句
            if sim >= 0.76 or (common_pref >= 5 and len(cj) > len(ci)):
                group.append(j)

        if len(group) > 1:
            best_idx = group[0]
            best_score = -1e9
            for order_k, gi in enumerate(group):
                sg = sigs[gi]
                txt_c = _clean_zh_text(segments[gi]["text"])
                internal_pauses = sg["internal_pauses"] if sg else 0
                rms_db = sg["rms_db"] if sg else primary_rms_global
                fluency_bonus = 8.0 if txt_c[-1:] not in ("呃", "啊", "那", "这") else -6.0
                energy_pen = max(0.0, (primary_rms_global - rms_db) * 1.5)
                sc = len(txt_c) * 1.4 + fluency_bonus - (internal_pauses * 5.0) - energy_pen + (order_k * 1.2)
                if sc > best_score:
                    best_score = sc
                    best_idx = gi

            for gi in group:
                if gi != best_idx:
                    segments[gi]["is_filler"] = True
                    segments[gi]["emotion"] = "filler"
                    segments[gi]["drop_reason"] = f"ng_retake (superseded_by={segments[best_idx]['id']})"
                    retake_dropped += 1

    return {"offcamera_dropped": offcam_dropped, "retake_dropped": retake_dropped}


NEGATION_CHARS = set("不没别非无莫唔冇")
PARTICLE_STRIP_RE = re.compile(r"[的了啊呢吗吧就也都还很太着过把被给]+")
LIST_ENUM_RE = re.compile(r"^(?:第[一二三四五六七八九十123456789]|[一二三四五六七八九十][是点个种步条]|首先|其次|最后)")
ANOMALY_QTY_RE = re.compile(r"([十百千万亿\d]+(?:\.\d+)?)\s*(万米|亿米|亿块|万吨|万元一[碗串份]|千度|万度)")


def refine_word_boundaries_with_energy(
    segments: List[Dict[str, Any]],
    audio_cache: Optional[Dict[str, Tuple[np.ndarray, int]]] = None,
) -> int:
    """用 10ms 真实语音能量包络收紧句首前导与句尾长静音，防止字幕或切点漂移。"""
    cache_rms: Dict[str, np.ndarray] = {}
    if audio_cache:
        for k, (y16, sr16) in audio_cache.items():
            cache_rms[k] = compute_10ms_rms(y16, sr16)
    adjusted = 0
    for seg in segments:
        if seg.get("is_filler"):
            continue
        sa = seg.get("source_audio")
        if not sa or not Path(sa).is_file():
            continue
        sa_key = str(Path(sa).resolve())
        if sa_key not in cache_rms:
            y16, sr16 = load_wav_16k_mono(Path(sa))
            cache_rms[sa_key] = compute_10ms_rms(y16, sr16)
        rms_10ms = cache_rms[sa_key]
        if len(rms_10ms) < 5:
            continue

        words = seg.get("words") or []
        s0 = max(0, int(round(float(seg["start_sec"]) * 100)))
        s1 = min(len(rms_10ms), max(s0 + 2, int(round(float(seg["end_sec"]) * 100))))
        seg_env = rms_10ms[s0:s1]
        if len(seg_env) < 5:
            continue
        nonzero = seg_env[seg_env > 0.004]
        p75 = float(np.percentile(nonzero, 75)) if len(nonzero) >= 3 else float(np.max(seg_env))
        thr = max(0.008, p75 * 0.16)

        # 收紧句首前导长静音 (> 150ms)
        seg_act = np.where(seg_env >= thr)[0]
        if len(seg_act) > 0 and int(seg_act[0]) >= 15:
            lead_hop = max(0, int(seg_act[0]) - 3)
            new_seg_start = round(float(seg["start_sec"]) + lead_hop * 0.010, 4)
            if words and new_seg_start < float(words[-1]["end_sec"]) - 0.15:
                seg["start_sec"] = new_seg_start
                words[0]["start_sec"] = max(float(words[0]["start_sec"]), new_seg_start)
                adjusted += 1

        for w in words:
            w0 = max(0, int(round(float(w["start_sec"]) * 100)))
            w1 = min(len(rms_10ms), int(round(float(w["end_sec"]) * 100)))
            if w1 - w0 >= 28:
                w_env = rms_10ms[w0:w1]
                active_idx = np.where(w_env >= thr)[0]
                if len(active_idx) > 0:
                    last_act = int(active_idx[-1])
                    tail_sil_hops = (len(w_env) - 1) - last_act
                    if tail_sil_hops >= 25:
                        new_end = round(float(w["start_sec"]) + (last_act + 4) * 0.010, 4)
                        new_end = max(float(w["start_sec"]) + 0.06, min(float(w["end_sec"]), new_end))
                        if new_end < float(w["end_sec"]) - 0.10:
                            w["end_sec"] = new_end
                            adjusted += 1

        if words:
            last_w_end = float(words[-1]["end_sec"])
            if float(seg["end_sec"]) - last_w_end >= 0.25:
                seg["end_sec"] = round(max(float(seg["start_sec"]) + 0.15, last_w_end + 0.05), 4)
    return adjusted


def detect_prompt_stumbles(segments: List[Dict[str, Any]]) -> int:
    """A1 提词半截词精准识别：仅在同一段原始音频内对比相邻有效句间隔。"""
    active = [i for i, s in enumerate(segments) if not s.get("is_filler")]
    dropped = 0
    for k in range(len(active) - 1):
        i_prev = active[k]
        i_next = active[k + 1]
        prev_s = segments[i_prev]
        next_s = segments[i_next]
        sa_prev = prev_s.get("source_audio")
        sa_next = next_s.get("source_audio")
        if sa_prev and sa_next and str(sa_prev) != str(sa_next):
            continue
        gap = float(next_s["start_sec"]) - float(prev_s["end_sec"])
        if gap < 0.90:
            continue
        p_raw = _clean_zh_text(prev_s["text"])
        n_raw = _clean_zh_text(next_s["text"])
        if not (2 <= len(p_raw) <= 7):
            continue
        if LIST_ENUM_RE.match(p_raw) or LIST_ENUM_RE.match(n_raw):
            continue
        p_core = PARTICLE_STRIP_RE.sub("", p_raw)
        n_core = PARTICLE_STRIP_RE.sub("", n_raw)
        if len(p_core) < 2 or len(n_core) < 2:
            continue
        if p_core[:2] == n_core[:2] or (len(p_core) >= 3 and p_core[:3] in n_core[:6]):
            prev_s["is_filler"] = True
            prev_s["emotion"] = "filler"
            prev_s["drop_reason"] = f"prompt_stumble (gap={gap:.2f}s, superseded_by={next_s['id']})"
            dropped += 1
    return dropped


def _sync_words_with_clean_text(words: List[Dict[str, Any]], clean_new: List[str]) -> None:
    """等长替换后同步更新 words，保留数字间 `:` / `.` 并跳过纯标点占位 token 防错位。"""
    if not words:
        return
    clean_norm = _extract_clean_chars("".join(clean_new))
    _, wt_lens = _word_content_spans(words)
    non_punct_indices = [idx for idx, wt_len in enumerate(wt_lens) if wt_len > 0]
    if len(clean_norm) == len(non_punct_indices):
        for idx, ch in zip(non_punct_indices, clean_norm):
            words[idx]["word"] = ch
        return
    cursor = 0
    total = len(clean_norm)
    for pos_k, idx in enumerate(non_punct_indices):
        w = words[idx]
        wt_len = wt_lens[idx]
        if wt_len == 0:
            continue
        if pos_k == len(non_punct_indices) - 1:
            w["word"] = "".join(clean_norm[cursor:]) if cursor < total else w["word"]
        else:
            w["word"] = "".join(clean_norm[cursor : min(total, cursor + wt_len)]) or w["word"]
            cursor = min(total, cursor + wt_len)


def apply_semantic_sanity_check(
    segments: List[Dict[str, Any]],
    replacements: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    """A5 上屏前语义常识校对（等长替换同步词组时间戳 + 否定词守卫 + 数量级预警）。"""
    warnings: List[Dict[str, Any]] = []
    rep_map = dict(replacements or {})

    for seg in segments:
        if seg.get("is_filler"):
            continue
        txt = str(seg.get("text") or "")
        for m in ANOMALY_QTY_RE.finditer(txt):
            warnings.append({
                "sentence_id": seg.get("id"),
                "type": "anomaly_quantity",
                "term": m.group(0),
                "text": txt,
                "needs_confirm": True,
                "reason": "检测到疑似 ASR 听错引起的反常数量级，请人工复核",
            })

        for old, new in rep_map.items():
            if old not in txt:
                continue
            if any(ch in NEGATION_CHARS for ch in (old + new)):
                warnings.append({
                    "sentence_id": seg.get("id"),
                    "type": "negation_guard_blocked",
                    "old": old,
                    "new": new,
                    "text": txt,
                    "needs_confirm": True,
                    "reason": "涉及否定词语义反转，禁止静默自动替换",
                })
                continue
            if len(old) != len(new):
                warnings.append({
                    "sentence_id": seg.get("id"),
                    "type": "non_equal_length_blocked",
                    "old": old,
                    "new": new,
                    "text": txt,
                    "needs_confirm": True,
                    "reason": "替换词不等长会导致逐字时间戳漂移，已阻断自动替换",
                })
                continue
            pos = txt.find(old)
            while pos != -1:
                txt = txt[:pos] + new + txt[pos + len(old):]
                pos = txt.find(old, pos + len(new))
            seg["text"] = txt
            clean_new = _extract_clean_chars(txt)
            _sync_words_with_clean_text(seg.get("words") or [], clean_new)
    return warnings


def synthesize_tts_segment(text: str, out_wav: Path, voice: str = "zh-CN-YunxiNeural") -> Tuple[bool, float]:
    """合成单句 48kHz 立体声 WAV：edge-tts -> macOS say；全部失败时 Fail-Fast 抛错。"""
    ffmpeg = find_bin("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("需要 ffmpeg 转换 TTS 音频格式。")

    tmp_raw = out_wav.with_suffix(".raw.mp3")
    try:
        import edge_tts

        async def _run_edge() -> None:
            comm = edge_tts.Communicate(text, voice=voice, rate="+8%")
            await comm.save(str(tmp_raw))

        asyncio.run(_run_edge())
        if tmp_raw.exists() and tmp_raw.stat().st_size > 500:
            run_cmd([
                ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(tmp_raw), "-ar", "48000", "-ac", "2", "-acodec", "pcm_s16le",
                str(out_wav),
            ])
            tmp_raw.unlink(missing_ok=True)
            dur = get_wav_duration_sec(out_wav)
            if dur > 0.1:
                return True, dur
    except Exception:
        tmp_raw.unlink(missing_ok=True)

    say_bin = find_bin("say")
    if say_bin:
        tmp_aiff = out_wav.with_suffix(".aiff")
        try:
            run_cmd([say_bin, "-r", "215", "-o", str(tmp_aiff), text])
            if tmp_aiff.exists() and tmp_aiff.stat().st_size > 500:
                run_cmd([
                    ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                    "-i", str(tmp_aiff), "-ar", "48000", "-ac", "2", "-acodec", "pcm_s16le",
                    str(out_wav),
                ])
                tmp_aiff.unlink(missing_ok=True)
                dur = get_wav_duration_sec(out_wav)
                if dur > 0.1:
                    return True, dur
        except Exception:
            tmp_aiff.unlink(missing_ok=True)

    raise RuntimeError(f"TTS 配音合成失败（edge-tts 与系统 say 均不可用），拒绝输出电子合成假语音：{text[:24]}")


def concat_wavs_with_gaps(seg_wavs: List[Path], gap_sec: float, out_wav: Path) -> List[Tuple[float, float]]:
    sr = 48000
    gap_frames = int(round(gap_sec * sr))
    silence_bytes = b"\x00\x00\x00\x00" * gap_frames
    all_pcm = bytearray()
    spans: List[Tuple[float, float]] = []
    cursor_frames = 0

    for idx, wp in enumerate(seg_wavs):
        with wave.open(str(wp), "rb") as wf:
            pcm = wf.readframes(wf.getnframes())
            n_frames = wf.getnframes()
        s_sec = round(cursor_frames / sr, 4)
        e_sec = round((cursor_frames + n_frames) / sr, 4)
        spans.append((s_sec, e_sec))
        all_pcm += pcm
        cursor_frames += n_frames
        if idx < len(seg_wavs) - 1 and gap_frames > 0:
            all_pcm += silence_bytes
            cursor_frames += gap_frames

    out_wav.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out_wav), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(bytes(all_pcm))
    return spans


def run_step2(
    pipeline_dir: Path,
    track: str = "store",
    script_json_path: Optional[Path] = None,
    force_tts: bool = False,
    whisper_model: Optional[str] = None,
    whitelist_path: Optional[Path] = None,
    sanity_replacements: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    from compliance_guard import audit_script_compliance

    manifest = read_json(pipeline_dir / "01_manifest.json")
    arolls = [it for it in manifest["items"] if it["role"] == "a_roll" and it.get("normalized_wav")]
    vgrade_by_mp4 = {
        str(it.get("normalized_mp4")): str(it.get("video_grade", "usable"))
        for it in manifest.get("items", [])
        if it.get("normalized_mp4")
    }

    has_user_script = bool(script_json_path and script_json_path.exists())
    if has_user_script:
        raw_scr = read_json(script_json_path)  # type: ignore[arg-type]
        if isinstance(raw_scr, dict):
            script_items = raw_scr.get("sentences") or raw_scr.get("segments") or []
        else:
            script_items = raw_scr
    else:
        script_items = DEFAULT_STORE_SCRIPT if track == "store" else DEFAULT_CREATOR_SCRIPT

    if force_tts or not arolls:
        if not has_user_script:
            print("[step2] [WARN] 无口播原声且未传 --script-json，正在使用内置示例台词生成演示 TTS。")
        tts_dir = pipeline_dir / "02_tts_segments"
        tts_dir.mkdir(parents=True, exist_ok=True)
        seg_wavs: List[Path] = []
        pre_items = [dict(it, id=f"seg_{i:03d}") for i, it in enumerate(script_items, start=1)]
        pre_comp = audit_script_compliance(
            pre_items,
            source_type="tts",
            pipeline_dir=pipeline_dir,
            whitelist_path=whitelist_path,
            auto_action=True,
        )
        rewritten_by_idx = {
            f["sentence_id"]: f["suggested_text"]
            for f in pre_comp.get("findings", [])
            if f.get("action") == "auto_rewrite_or_block" and f.get("suggested_text")
        }
        effective_items = []
        for idx, item in enumerate(script_items, start=1):
            it_copy = dict(item, id=f"seg_{idx:03d}")
            if it_copy["id"] in rewritten_by_idx:
                it_copy["original_text_before_compliance"] = it_copy["text"]
                it_copy["text"] = rewritten_by_idx[it_copy["id"]]
                # 合规改写后同步刷新 keywords，严防违禁原词穿透到花字与封面
                it_copy["keywords"] = extract_auto_keywords(it_copy["text"])
            effective_items.append(it_copy)

        # 先做 TTS 前文本常识校对，确保音频与最终文字 100% 一致
        sanity_warnings = apply_semantic_sanity_check(effective_items, replacements=sanity_replacements)
        for item in effective_items:
            if not item.get("keywords") or item.get("original_text_before_compliance"):
                item["keywords"] = extract_auto_keywords(item["text"])

        for idx, item in enumerate(effective_items, start=1):
            sp = tts_dir / f"seg_{idx:03d}.wav"
            synthesize_tts_segment(item["text"], sp)
            seg_wavs.append(sp)

        master_tts_wav = pipeline_dir / "02_tts_voice.wav"
        spans = concat_wavs_with_gaps(seg_wavs, gap_sec=0.38, out_wav=master_tts_wav)
        total_dur = get_wav_duration_sec(master_tts_wav)
        y16, sr16 = load_wav_16k_mono(master_tts_wav)
        rms_10ms = compute_10ms_rms(y16, sr16)

        segments: List[Dict[str, Any]] = []
        for idx, (item, (s_sec, e_sec)) in enumerate(zip(effective_items, spans), start=1):
            kws = item.get("keywords", []) or extract_auto_keywords(item["text"])
            segments.append({
                "id": f"seg_{idx:03d}",
                "text": item["text"],
                "start_sec": s_sec,
                "end_sec": e_sec,
                "source_media": None,
                "source_video_grade": "usable",
                "source_audio": str(master_tts_wav.resolve()),
                "is_filler": bool(item.get("is_filler", False)),
                "emotion": item.get("emotion", "info"),
                "keywords": kws,
                "words": split_chars_energy_aligned(item["text"], s_sec, e_sec, kws, rms_10ms=rms_10ms),
            })
        compute_prosody_for_segments(master_tts_wav, segments, audio_pair=(y16, sr16))
        source_type = "tts"
        primary_audio = str(master_tts_wav.resolve())
        filter_stats = {"offcamera_dropped": 0, "retake_dropped": 0, "stumbles_dropped": 0, "silence_retimed": 0}
        compliance_report = pre_comp
    else:
        segments = []
        source_types: List[str] = []
        total_dur = 0.0
        audio_cache: Dict[str, Tuple[np.ndarray, int]] = {}
        for ar in arolls:
            wav_path = Path(ar["normalized_wav"])
            wav_key = str(wav_path.resolve())
            dur_ar = get_wav_duration_sec(wav_path)
            total_dur += dur_ar
            if wav_key not in audio_cache:
                audio_cache[wav_key] = load_wav_16k_mono(wav_path)
            y16_ar, sr16_ar = audio_cache[wav_key]

            if has_user_script and len(arolls) == 1:
                ar_segs, stype = align_script_to_existing_wav(
                    wav_path, script_items, ar["normalized_mp4"], model_name=whisper_model, audio_pair=(y16_ar, sr16_ar)
                )
            else:
                asr_segs = try_faster_whisper(wav_path, model_name=whisper_model, y16_cached=y16_ar)
                if asr_segs:
                    for seg in asr_segs:
                        seg["source_media"] = ar["normalized_mp4"]
                        seg["source_audio"] = wav_key
                    ar_segs = asr_segs
                    stype = "asr_whisper"
                elif has_user_script:
                    raise RuntimeError(
                        f"存在 {len(arolls)} 段实拍口播且 faster-whisper 转写失败，无法安全分配单份脚本至多段原声。"
                    )
                else:
                    raise RuntimeError(
                        f"检测到实拍口播原声 ({Path(ar['normalized_mp4']).name})，但 faster-whisper 不可用且未传 --script-json。"
                        "已拒绝静默套用内置示范台词，请补充 --script-json 或运行 check_and_setup.py --install-ml。"
                    )

            for seg in ar_segs:
                seg["source_video_grade"] = vgrade_by_mp4.get(str(ar["normalized_mp4"]), "usable")
            segments.extend(ar_segs)
            source_types.append(stype)

        for gi, seg in enumerate(segments, start=1):
            seg["id"] = f"seg_{gi:03d}"

        # 正确顺序：先收紧句首/句尾静音 -> 再重算韵律 F0/RMS -> 分组剥离画外音 -> 再排查提词半截 -> 合规与同音校对
        retimed_cnt = refine_word_boundaries_with_energy(segments, audio_cache=audio_cache)
        for ar in arolls:
            wk = str(Path(ar["normalized_wav"]).resolve())
            ar_slice = [s for s in segments if s.get("source_audio") == wk]
            compute_prosody_for_segments(Path(ar["normalized_wav"]), ar_slice, audio_pair=audio_cache.get(wk))
        filter_stats = filter_offcamera_and_retakes(segments, audio_cache=audio_cache)
        stumble_cnt = detect_prompt_stumbles(segments)
        filter_stats["stumbles_dropped"] = stumble_cnt
        filter_stats["silence_retimed"] = retimed_cnt
        source_type = source_types[0] if len(set(source_types)) == 1 else "multi_aroll_mixed"
        primary_audio = str(Path(arolls[0]["normalized_wav"]).resolve())
        compliance_report = audit_script_compliance(
            segments,
            source_type="real_voice",
            pipeline_dir=pipeline_dir,
            whitelist_path=whitelist_path,
            auto_action=True,
        )
        if not compliance_report.get("passed", True):
            dropped = compliance_report.get("dropped_real_voice_ids", [])
            raise RuntimeError(f"实拍口播合规拦截阻断：违规词已淘汰 {len(dropped)} 句，导致有效原声不足。")

        sanity_warnings = apply_semantic_sanity_check(segments, replacements=sanity_replacements)
        for seg in segments:
            if not seg.get("is_filler") and sanity_replacements:
                seg["keywords"] = extract_auto_keywords(seg["text"])
                _mark_word_keywords(seg.get("words") or [], seg["keywords"])

    transcript = {
        "step": "02_transcribe",
        "source_type": source_type,
        "language": "zh",
        "primary_audio": primary_audio,
        "duration_sec": round(total_dur, 4),
        "filter_stats": filter_stats,
        "compliance_passed": compliance_report.get("passed", True),
        "compliance_report": str((pipeline_dir / "00_compliance_report.json").resolve()),
        "sanity_warnings": sanity_warnings,
        "segments": segments,
    }
    write_json(pipeline_dir / "02_transcript.json", transcript)
    active_cnt = sum(1 for s in segments if not s.get("is_filler"))
    print(
        f"[step2] 转写与声学对齐完成 ({source_type}, 有效 {active_cnt}/{len(segments)} 句, "
        f"画外声={filter_stats['offcamera_dropped']}, NG择优={filter_stats['retake_dropped']}, "
        f"提词半截={filter_stats['stumbles_dropped']}, 静音重计时={filter_stats['silence_retimed']}, "
        f"实拍合规弃用={len(compliance_report.get('dropped_real_voice_ids', []))}) -> 02_transcript.json"
    )
    return transcript


def main() -> None:
    maybe_reexec_in_venv(("faster_whisper",))
    parser = argparse.ArgumentParser(description="Step 2: 语音转写、声学强制对齐、向量化指纹去画外音与 NG 择优")
    parser.add_argument("--pipeline-dir", required=True)
    parser.add_argument("--track", choices=["store", "creator"], default="store")
    parser.add_argument("--script-json", help="可选：自定义文案句列表 JSON")
    parser.add_argument("--force-tts", action="store_true", help="强制用文案生成 TTS 配音")
    parser.add_argument("--whisper-model", help="指定 faster-whisper 模型名")
    parser.add_argument("--whitelist", help="可选：compliance_whitelist.json 路径")
    parser.add_argument("--sanity-replacements", help="可选：同音纠偏等长替换字典 JSON 文件或 JSON 字符串")
    args = parser.parse_args()

    reps: Optional[Dict[str, str]] = None
    if args.sanity_replacements:
        p_rep = Path(args.sanity_replacements)
        reps = read_json(p_rep) if p_rep.exists() else json.loads(args.sanity_replacements)

    run_step2(
        Path(args.pipeline_dir),
        track=args.track,
        script_json_path=Path(args.script_json) if args.script_json else None,
        force_tts=args.force_tts,
        whisper_model=args.whisper_model,
        whitelist_path=Path(args.whitelist) if args.whitelist else None,
        sanity_replacements=reps,
    )


if __name__ == "__main__":
    main()
