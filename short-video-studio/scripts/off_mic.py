#!/usr/bin/env python3
"""谁在说话：用「离麦电平」把不是主讲人的话从成稿里挑出来。

用户实测反馈（2026-11-24）：「仲有好多人中意我哋嘅干炒蚝，同埋」这一句是导演/摄影师的话，
要去掉。查实以后发现根因不是 ASR 听错字，而是**声源离麦太远**：

    同一段录音里主讲人对着无线麦说话，电平稳定在 -15 ~ -20 dB；
    那 4 秒（23.28–27.54s）电平掉到 -30 ~ -49 dB —— 有人在几米外插话。

这条信号是确定性的、可复算的，比「按文字猜是不是口播」靠谱得多：字幕对不上文字时
`script_align` 才会砍，插话如果恰好被 ASR 听成通顺的话，就一路混进成片。
所以这里单独立一条门槛：**相对主讲人基准电平掉超过 `OFF_MIC_DROP_DB`、
持续超过 `MIN_RUN_SEC` 的连续字，一律不算口播**。

本模块只做判定，不碰音频、不碰切点；调用方（`step3_lock_audio`）把结果交给
`script_align.keep_intervals(off_mic_spans=...)`，那些字就配不上、只能被跳过。
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# ── 判定参数（只在这里改一次）──────────────────────────────────────────
OFF_MIC_DROP_DB = 8.0      # 比主讲人基准电平低这么多，就认为声源不在麦前（软档：交给对齐去裁）
# 硬档：落差大到这个程度，不管文字对不对得上都直接砍。为什么分两档 —— 实测同一段里
# 「同埋」这种连接词只是说得轻（-27.7dB，落差 13.8dB），属于主讲人自己的轻声；
# 而插话动不动差 27–33dB（2026-11-24 实测：「干炒蚝」那 1.38 秒差 32.6dB）。
# 只按 8dB 一刀切会把「凤爪同埋叉烧包」的连接词也砍掉。
OFF_MIC_HARD_DB = 18.0
MIN_RUN_SEC = 0.30         # 连续这么久的离麦话才算「插话」，避免把气口/尾音判死
MERGE_GAP_SEC = 0.12       # 两段离麦字之间只隔这么点，并成一段
HOST_REF_PERCENTILE = 90.0  # 主讲人基准电平：该文件字的电平 90 分位（对着麦说话的那一档）
                            # 用 75 分位会被「一大段都是别人在说」的文件反噬（实测合成用例基准跑到插话上）
MIN_LEVEL_DB = -70.0       # 底噪地板：比这还低的字不算「说话」，不参与基准
TRIM_SEC = 0.02            # 字两端各让 20ms，避开起音/收尾的过渡
MIN_WORD_SEC = 0.05        # 太短的字扩到这个长度再量电平
MAX_SPAN_SEC = 8.0         # 一段离麦话最长算到 8 秒，超出的多半是整段环境声


def _load_audio(path: str) -> Tuple[Any, int]:
    """读单声道 float32 与采样率。优先 soundfile，缺库时退 ffmpeg 解到 /tmp。"""
    import numpy as np

    p = str(path)
    try:
        import soundfile as sf  # type: ignore

        a, sr = sf.read(p, dtype="float32", always_2d=False)
        if getattr(a, "ndim", 1) > 1:
            a = a.mean(axis=1)
        return a, int(sr)
    except Exception:
        pass
    import subprocess
    import tempfile

    try:
        from common import find_bin  # 复用仓库里的 ffmpeg 定位口径
        ff = find_bin("ffmpeg")
    except Exception:
        ff = "ffmpeg"
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
        wav = tf.name
    subprocess.run(
        [str(ff), "-v", "error", "-y", "-i", p, "-ac", "1", "-ar", "48000", "-f", "wav", wav],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    import wave

    with wave.open(wav, "rb") as w:
        sr = w.getframerate()
        raw = w.readframes(w.getnframes())
    a = np.frombuffer(raw, dtype=np.int16).astype("float32") / 32768.0
    return a, sr


def _db(x: Any) -> Optional[float]:
    import numpy as np

    if x is None or len(x) == 0:
        return None
    rms = float(np.sqrt(np.mean(np.square(x, dtype="float64"))))
    if rms <= 0:
        return None
    return 20.0 * math.log10(rms)


def word_levels(audio: Any, sr: int, words: Sequence[Dict[str, Any]]) -> List[Optional[float]]:
    """每个字的电平（dBFS）。字的时间相对它自己的源文件。"""
    out: List[Optional[float]] = []
    n = len(audio)
    for w in words or []:
        try:
            s = float(w.get("start_sec", 0.0))
            e = float(w.get("end_sec", s))
        except (TypeError, ValueError):
            out.append(None)
            continue
        if e < s:
            s, e = e, s
        if e - s < MIN_WORD_SEC:
            mid = (s + e) * 0.5
            s, e = mid - MIN_WORD_SEC * 0.5, mid + MIN_WORD_SEC * 0.5
        # 让边改成「最多让 20ms，且不超过字长的 1/4」：短字（0.05s 级别）本来就是一个个
        # 单字，按固定 20ms 两端让完就剩不下东西了，量不出电平（2026-11-24 合成用例暴露）。
        trim = min(TRIM_SEC, max(0.0, (e - s) * 0.25))
        s += trim
        e -= trim
        a = max(0, min(n, int(round(s * sr))))
        b = max(0, min(n, int(round(e * sr))))
        if b - a < int(0.02 * sr):
            out.append(None)
            continue
        out.append(_voiced_db(audio[a:b], sr))
    return out


# ── 一个字「说了多大声」怎么量 ──────────────────────────────────────────
# 不能直接拿整段求 RMS：ASR 的词时间常常把前后的停顿也圈进来（实测「全金湾」被圈了 1.8 秒，
# 一半是静音），RMS 一平均就把主讲人的正常音量算成「离麦」——2026-11-24 因此误标了
# 「全金湾」「六百平嘅点」这些主讲人自己的词。改成只看这一段里**真正有声的那部分**：
# 按 40ms 窗 / 20ms 步长算帧电平，取帧电平的 80 分位。
VOICED_PCT = 80.0
FRAME_WIN_SEC = 0.04
FRAME_HOP_SEC = 0.02


def _voiced_db(seg_audio: Any, sr: int, pct: float = VOICED_PCT) -> Optional[float]:
    import numpy as np

    win = max(64, int(FRAME_WIN_SEC * sr))
    hop = max(32, int(FRAME_HOP_SEC * sr))
    if len(seg_audio) <= win:
        return _db(seg_audio)
    vals: List[float] = []
    for i in range(0, len(seg_audio) - win + 1, hop):
        d = _db(seg_audio[i:i + win])
        if d is not None and d > MIN_LEVEL_DB:
            vals.append(d)
    if not vals:
        return _db(seg_audio)
    vals.sort()
    k = (len(vals) - 1) * (pct / 100.0)
    lo = int(math.floor(k))
    hi = min(lo + 1, len(vals) - 1)
    return float(vals[lo] * (1 - (k - lo)) + vals[hi] * (k - lo))


def host_ref_db(levels: Sequence[Optional[float]], percentile: float = HOST_REF_PERCENTILE) -> Optional[float]:
    """主讲人基准电平。取 75 分位：插话总是少数，不会被它拖低。"""
    vals = sorted(v for v in levels if v is not None and v > MIN_LEVEL_DB)
    if not vals:
        return None
    if len(vals) < 4:
        return float(vals[-1])
    k = (len(vals) - 1) * (percentile / 100.0)
    lo = int(math.floor(k))
    hi = min(lo + 1, len(vals) - 1)
    frac = k - lo
    return float(vals[lo] * (1 - frac) + vals[hi] * frac)


def detect_off_mic(
    words: Sequence[Dict[str, Any]],
    levels: Sequence[Optional[float]],
    ref_db: Optional[float],
    drop_db: float = OFF_MIC_DROP_DB,
    min_run_sec: float = MIN_RUN_SEC,
    merge_gap_sec: float = MERGE_GAP_SEC,
) -> List[Dict[str, Any]]:
    """把「明显比主讲人低」的连续字收成离麦段。

    返回 `[{"start_sec", "end_sec", "dur_sec", "drop_db", "words": [字...], "text"}]`。
    段内允许夹一两个「量不清电平」的短字（气口、辅音），间隔不超过 `merge_gap_sec`。
    """
    if ref_db is None or not words:
        return []
    thr = ref_db - drop_db
    flagged: List[Tuple[int, float]] = []  # (字下标, 电平)
    for i, w in enumerate(words):
        lv = levels[i] if i < len(levels) else None
        if lv is None:
            continue
        if lv < thr:
            flagged.append((i, lv))
    if not flagged:
        return []

    groups: List[List[Tuple[int, float]]] = [[flagged[0]]]
    for idx, lv in flagged[1:]:
        prev_i = groups[-1][-1][0]
        gap = 0.0
        try:
            gap = float(words[idx].get("start_sec", 0.0)) - float(words[prev_i].get("end_sec", 0.0))
        except (TypeError, ValueError):
            gap = 0.0
        if 0 <= idx - prev_i <= 3 and gap <= merge_gap_sec:
            groups[-1].append((idx, lv))
        else:
            groups.append([(idx, lv)])

    spans: List[Dict[str, Any]] = []
    for g in groups:
        try:
            s = float(words[g[0][0]].get("start_sec", 0.0))
            e = float(words[g[-1][0]].get("end_sec", s))
        except (TypeError, ValueError):
            continue
        dur = e - s
        if dur < min_run_sec:
            continue
        if dur > MAX_SPAN_SEC:
            e = s + MAX_SPAN_SEC
            dur = MAX_SPAN_SEC
        worst = round(float(ref_db) - min(lv for _, lv in g), 2)
        spans.append({
            "start_sec": round(s, 4),
            "end_sec": round(e, 4),
            "dur_sec": round(dur, 4),
            "drop_db": worst,
            "severity": "hard" if worst >= OFF_MIC_HARD_DB else "soft",
            "words": [str(words[i].get("word") or "") for i, _ in g],
            "text": "".join(str(words[i].get("word") or "") for i, _ in g),
        })
    return spans


def hard_spans(spans: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """只留「硬档」：落差 ≥ `OFF_MIC_HARD_DB`，不管文字对不对得上都得砍。"""
    return [s for s in (spans or []) if str(s.get("severity") or "hard") == "hard"]


def analyze_spans(audio_path: str, words: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """一个源文件的离麦段。失败就返回空结果 + `error`，绝不静默当「全部是口播」。"""
    if not words:
        return {"spans": [], "ref_db": None, "error": "no_words"}
    try:
        audio, sr = _load_audio(audio_path)
    except Exception as e:  # 读不了音频时说清楚
        return {"spans": [], "ref_db": None, "error": f"{type(e).__name__}: {e}"}
    return spans_from_audio(audio, sr, words)


def spans_from_audio(audio: Any, sr: int, words: Sequence[Dict[str, Any]],
                     ref_db: Optional[float] = None) -> Dict[str, Any]:
    """给定音频与一组字，算离麦段。`ref_db` 不给就用这组字自己的 75 分位。"""
    levels = word_levels(audio, sr, words)
    ref = host_ref_db(levels) if ref_db is None else float(ref_db)
    spans = detect_off_mic(words, levels, ref)
    return {
        "spans": spans,
        "ref_db": None if ref is None else round(float(ref), 2),
        "off_mic_sec": round(sum(s["dur_sec"] for s in spans), 3),
        "error": None,
    }


def annotate_segments(segments: Iterable[Dict[str, Any]], cache: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """给每个转写段落写 `off_mic_spans`（原地）。

    关键：同一个源文件会被切成好几个转写段（一镜里说了五行话），主讲人基准电平必须
    **按文件统一算**，否则每段各自算基准，「整段都离麦」或「基准被插话拉低」都会误判。
    音频只读一次（按文件缓存），电平按段算。2026-11-24 第一版按段缓存整个分析结果，
    结果第二个段直接复用了第一段的结论，`off_mic_spans` 全是空的。
    """
    cache = {} if cache is None else cache
    segs = list(segments)
    # 1) 按源文件把字聚起来，读一次音频
    by_file: Dict[str, List[Dict[str, Any]]] = {}
    for seg in segs:
        sa = str(seg.get("source_audio") or "")
        if not sa or not (seg.get("words") or []):
            continue
        by_file.setdefault(sa, []).extend(seg.get("words") or [])
    failures: List[str] = []
    refs: Dict[str, Optional[float]] = {}
    for sa, words in by_file.items():
        if sa not in cache:
            try:
                audio, sr = _load_audio(sa)
                cache[sa] = {"audio": audio, "sr": sr}
            except Exception as e:
                cache[sa] = {"error": f"{type(e).__name__}: {e}"}
                failures.append(f"{Path(sa).name}: {cache[sa]['error']}")
                continue
        item = cache[sa]
        if item.get("error"):
            continue
        refs[sa] = host_ref_db(word_levels(item["audio"], item["sr"], words))

    # 2) 每段各自算离麦段（基准用本文件的）
    total_spans = 0
    total_sec = 0.0
    hard_spans = 0
    hard_sec = 0.0
    soft_spans = 0
    soft_sec = 0.0
    for seg in segs:
        sa = str(seg.get("source_audio") or "")
        words = seg.get("words") or []
        if not sa or not words or sa not in refs or sa not in cache:
            continue
        item = cache[sa]
        if item.get("error"):
            continue
        res = spans_from_audio(item["audio"], item["sr"], words, ref_db=refs[sa])
        if res.get("spans"):
            seg["off_mic_spans"] = res["spans"]
            total_spans += len(res["spans"])
            total_sec += res["off_mic_sec"]
            for sp in res["spans"]:
                if sp["severity"] == "hard":
                    hard_spans += 1
                    hard_sec += sp["dur_sec"]
                else:
                    soft_spans += 1
                    soft_sec += sp["dur_sec"]
    return {
        "spans": total_spans,
        "sec": round(total_sec, 3),
        "hard_spans": hard_spans,
        "hard_sec": round(hard_sec, 3),
        "soft_spans": soft_spans,
        "soft_sec": round(soft_sec, 3),
        "failures": failures,
        "ref_db": {Path(k).name: v for k, v in refs.items()},
    }


def format_report(result: Dict[str, Any]) -> str:
    """一行给日志看的摘要。"""
    if result.get("hard_spans"):
        tail = f"，另有 {result['soft_spans']} 段轻档（判给对齐去裁）" if result.get("soft_spans") else ""
        fail = f"，{len(result['failures'])} 个文件读不了" if result.get("failures") else ""
        return (f"检出 {result['hard_spans']} 段离麦插话（硬档 ≥{OFF_MIC_HARD_DB:.0f}dB），"
                f"共 {result['hard_sec']:.1f} 秒不是主讲人的话{tail}{fail}")
    if result.get("spans"):
        return f"只有 {result['soft_spans']} 段轻档离麦（{result['soft_sec']:.1f} 秒），交给逐字对齐去裁"
    if result.get("failures"):
        return f"离麦检测未生效：{len(result['failures'])} 个文件读不了（{result['failures'][0]}）"
    return "没有检出离麦插话"
