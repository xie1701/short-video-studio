#!/usr/bin/env python3
"""SenseVoice 本地转写（Sherpa-onnx 绑定）——这套 skill 的主 ASR 引擎。

为什么是它（2026-11-24 实测，同一段 54.9 秒粤语口播）：

| 引擎 | 耗时 | 结果 |
|---|---|---|
| SenseVoice int8（本模块） | **1.12 s** | 自动判出粤语，字面准确，带标点与字级时间戳 |
| `coli asr`（同一模型，JS CLI） | 2.1 s | 同上（多一次 node 启动 + 文件解码） |
| faster-whisper small | 26 s | 同一句复读 4 遍 |
| faster-whisper medium | 52 s | 一句，但「金湾」听成「今晚」 |

关键点是**方言**：whisper 被 `language="zh"` 强制按普通话听粤语素材，只能靠猜同音字；
SenseVoice 会自己判出 `<|yue|>`，直接给字面。术语表纠错因此从「救火」变成「微调」。

本模块只做三件事：找模型、转写、按停顿把字级时间戳拼回句子。
后面那套（术语表纠错、繁转简、画外声/NG 择优、静音重计时）仍在 step2 里统一跑，两条引擎共用。
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 模型目录：任意一层带 model.int8.onnx / model.onnx + tokens.txt 的目录都认。
MODEL_ONNX_NAMES = ("model.int8.onnx", "model.onnx", "model.fp16.onnx")

# SenseVoice 不给句界，只能靠「停顿 + 标点」反推。温州/粤语语速快，静音门槛不能太大。
PAUSE_SPLIT_SEC = 0.42
MAX_SEG_SEC = 14.0
MIN_SEG_SEC = 0.30
STRONG_PUNCT = "。！？!?…"
SOFT_PUNCT = "，,、；;：:"
ALL_PUNCT = STRONG_PUNCT + SOFT_PUNCT + "「」『』\"'（）()《》【】—～~"
SV_CFG = "sv1"  # 改动切句/解码参数时改这里，旧缓存自动失效


# ---------------------------------------------------------------- 模型发现

def _candidate_dirs() -> List[Path]:
    out: List[Path] = []
    env = os.environ.get("SVS_SENSEVOICE_MODEL")
    if env:
        out.append(Path(env).expanduser())
    out.append(Path.home() / ".short-video-studio" / "models")
    out.append(Path.home() / ".coli" / "models")
    try:
        from common import ASSETS_DIR, VENDOR_DIR  # 延迟导入，避免与 step2 循环

        out.append(ASSETS_DIR / "models")
        out.append(VENDOR_DIR / "models")
    except Exception:
        pass
    return out


def is_model_dir(p: Path) -> bool:
    if not p.is_dir():
        return False
    has_onnx = any((p / n).is_file() and (p / n).stat().st_size > 10_000_000 for n in MODEL_ONNX_NAMES)
    return bool(has_onnx and (p / "tokens.txt").is_file())


def find_model_dir() -> Optional[Path]:
    """按优先级找 SenseVoice 模型：环境变量 → 用户级目录 → Cola 自带 → 随包 assets/vendor。"""
    for root in _candidate_dirs():
        if not root.is_dir():
            continue
        if is_model_dir(root):
            return root
        for sub in sorted(root.iterdir()):
            if is_model_dir(sub):
                return sub
    return None


def model_summary(p: Optional[Path] = None) -> str:
    d = p or find_model_dir()
    if not d:
        return "未找到 SenseVoice 模型"
    onnx = next((d / n for n in MODEL_ONNX_NAMES if (d / n).is_file()), None)
    mb = (onnx.stat().st_size / 1e6) if onnx else 0.0
    return f"{d.name}（{onnx.name if onnx else '?'}，{mb:.0f} MB）"


def available() -> Tuple[bool, str]:
    """能不能用：sherpa_onnx 装没装 + 模型在不在。两种情况都要说清，不许静默降级。"""
    try:
        import sherpa_onnx  # noqa: F401
    except Exception as exc:  # pragma: no cover - 环境相关
        return False, f"没装 sherpa-onnx（pip install sherpa-onnx）：{exc}"
    d = find_model_dir()
    if not d:
        return False, (
            "没找到 SenseVoice 模型（需要 model.int8.onnx + tokens.txt）。"
            "放这三个位置之一即可：~/.short-video-studio/models/、~/.coli/models/、<skill>/vendor/models/"
        )
    return True, model_summary(d)


# ---------------------------------------------------------------- 跑模型

_REC_CACHE: Dict[Tuple[str, int], Any] = {}


def _get_recognizer(model_dir: Path, threads: int) -> Any:
    key = (str(model_dir), int(threads))
    if key in _REC_CACHE:
        return _REC_CACHE[key]
    import sherpa_onnx

    onnx = next((model_dir / n for n in MODEL_ONNX_NAMES if (model_dir / n).is_file()), None)
    rec = sherpa_onnx.OfflineRecognizer.from_sense_voice(
        model=str(onnx),
        tokens=str(model_dir / "tokens.txt"),
        num_threads=max(1, int(threads)),
        use_itn=True,          # 数字/标点由模型自己给，下游切句全靠它
        language="auto",       # 方言自判（yue/zh/en/ja/ko）——这是换掉 whisper 的头号理由
        debug=False,
    )
    _REC_CACHE[key] = rec
    return rec


def _decode_wav_16k(path: Path) -> Tuple[Any, int]:
    """ffmpeg 直出 16k 单声道 f32le。不依赖 soundfile/librosa，也不吃多声道。"""
    from common import find_bin

    cmd = [
        find_bin("ffmpeg"), "-v", "error", "-i", str(path),
        "-ac", "1", "-ar", "16000", "-f", "f32le", "-",
    ]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"解码音频失败：{path}：{proc.stderr.decode('utf-8', 'ignore')[:300]}")
    import numpy as np

    return np.frombuffer(proc.stdout, dtype=np.float32).copy(), 16000


def _token_end_times(tokens: List[str], ts: List[float]) -> List[float]:
    """SenseVoice 的 timestamps 是每个 token 的起点；终点按下一个 token 的起点推，末个字给 0.2s。"""
    ends: List[float] = []
    for i, t in enumerate(ts):
        nxt = ts[i + 1] if i + 1 < len(ts) else t + 0.2
        dur = max(0.04, min(float(nxt) - float(t), 1.2))
        ends.append(float(t) + dur)
    return ends


def _is_ascii_word(tok: str) -> bool:
    return bool(tok) and all(ch.isascii() and (ch.isalnum() or ch in "'-_&.") for ch in tok)


def segments_from_tokens(tokens: List[str], ts: List[float]) -> List[Dict[str, Any]]:
    """把 token 流拼成句子。

    切句三条件：与下一个字之间停顿 > PAUSE_SPLIT_SEC；遇到强标点（。！？）；
    已经超过 MAX_SEG_SEC。软标点（，）只在整句已经超过 4 秒时才切，避免一句被切得太碎。
    """
    if not tokens:
        return []
    ends = _token_end_times(tokens, ts)
    segs: List[Dict[str, Any]] = []
    cur_tokens: List[str] = []
    cur_words: List[Dict[str, Any]] = []
    cur_start: Optional[float] = None
    cur_end = 0.0
    pending_ascii: Optional[Dict[str, Any]] = None

    def flush() -> None:
        nonlocal cur_tokens, cur_words, cur_start, cur_end, pending_ascii
        text = "".join(cur_tokens).strip()
        text = text.strip("".join(ALL_PUNCT))
        if text and cur_start is not None and (cur_end - cur_start) >= MIN_SEG_SEC:
            words = [w for w in cur_words if w["word"] and w["word"] not in ALL_PUNCT]
            segs.append({
                "text": text,
                "start_sec": round(float(cur_start), 4),
                "end_sec": round(float(cur_end), 4),
                "words": words,
            })
        cur_tokens, cur_words, cur_start, cur_end, pending_ascii = [], [], None, 0.0, None

    for i, tok in enumerate(tokens):
        s = float(ts[i])
        e = float(ends[i])
        if cur_start is None:
            cur_start = s
        cur_tokens.append(tok)
        cur_end = e
        if tok.strip() and tok not in ALL_PUNCT:
            if _is_ascii_word(tok) and pending_ascii is not None and abs(pending_ascii["end_sec"] - s) < 0.25:
                pending_ascii["word"] += tok
                pending_ascii["end_sec"] = round(e, 4)
            else:
                word = {"word": tok, "start_sec": round(s, 4), "end_sec": round(e, 4),
                        "is_keyword": False, "is_accent": False, "energy": 0.75}
                cur_words.append(word)
                pending_ascii = word if _is_ascii_word(tok) else None
        # 停顿按「下一个字的起点 − 本字起点」算：直接拿 end 相减会被 duration 上限（1.2s）吃掉长静音。
        # 一个字的时长约 0.15–0.3s，所以门槛给 PAUSE_SPLIT_SEC + 0.25。
        nxt_gap = (float(ts[i + 1]) - s) if i + 1 < len(tokens) else 999.0
        pause_split = PAUSE_SPLIT_SEC + 0.25
        dur = cur_end - (cur_start or cur_end)
        hard = tok in STRONG_PUNCT
        soft = tok in SOFT_PUNCT and dur >= 4.0
        if hard or soft or nxt_gap > pause_split or dur >= MAX_SEG_SEC:
            flush()
    flush()
    return segs


# ---------------------------------------------------------------- 缓存

def _cache_dir() -> Path:
    return Path.home() / ".short-video-studio" / "cache" / "asr"


def _cache_key(wav_path: Path, model_dir: Path, language: str) -> str:
    h = hashlib.sha256()
    h.update(f"{SV_CFG}|{language}|{model_dir.name}|".encode())
    onnx = next((model_dir / n for n in MODEL_ONNX_NAMES if (model_dir / n).is_file()), None)
    if onnx:
        st = onnx.stat()
        h.update(f"{onnx.name}:{st.st_size}:{int(st.st_mtime)}|".encode())
    st = wav_path.stat()
    h.update(f"{wav_path.name}:{st.st_size}:{int(st.st_mtime)}|".encode())
    with wav_path.open("rb") as f:
        h.update(f.read(65536))
        if st.st_size > 131072:
            f.seek(-65536, os.SEEK_END)
            h.update(f.read(65536))
    return "sv_" + h.hexdigest()[:32]


def try_transcribe(
    wav_path: Path,
    language: str = "auto",
    threads: Optional[int] = None,
    use_cache: bool = True,
) -> Optional[List[Dict[str, Any]]]:
    """转写一段 wav，返回 step2 口径的句子列表（只含 text/start/end/words）。

    - 静音段返回 `[]`（调用方据此把这条素材降级成空镜）；
    - 引擎/模型不可用返回 `None`（调用方可以回落到 whisper，或按显式要求报错）。
    """
    ok, why = available()
    if not ok:
        if os.environ.get("SVS_ASR_VERBOSE"):
            print(f"[asr] SenseVoice 不可用：{why}")
        return None
    model_dir = find_model_dir()
    assert model_dir is not None

    if use_cache:
        try:
            ck = _cache_key(wav_path, model_dir, language)
            c_p = _cache_dir() / f"{ck}.json"
            if c_p.is_file():
                import json

                cached = json.loads(c_p.read_text("utf-8"))
                segs = cached.get("segments")
                if isinstance(segs, list):
                    print(f"[step2] 转写命中缓存：{wav_path.name}（{len(segs)} 句）")
                    return segs
        except Exception:
            pass

    y16, sr = _decode_wav_16k(wav_path)
    if len(y16) == 0:
        return []
    rms = float((y16.astype("float64") ** 2).mean() ** 0.5)
    if rms < 0.002:
        return []

    if threads is None:
        try:
            from common import adaptive_workers

            threads = adaptive_workers(n_items=1, cap=4, floor=1)
        except Exception:
            threads = 2
    rec = _get_recognizer(model_dir, int(threads))
    stream = rec.create_stream()
    stream.accept_waveform(sr, y16)
    rec.decode_stream(stream)
    result = stream.result
    lang = str(getattr(result, "lang", "") or "")
    tokens = list(getattr(result, "tokens", []) or [])
    stamps = [float(t) for t in (getattr(result, "timestamps", []) or [])]
    if len(tokens) != len(stamps):
        stamps = stamps[: len(tokens)]
        while len(stamps) < len(tokens):
            stamps.append(stamps[-1] if stamps else 0.0)

    segs = segments_from_tokens(tokens, stamps)
    for i, seg in enumerate(segs, start=1):
        seg["id"] = f"seg_{i:03d}"
        seg["asr_lang"] = lang.strip("<>|") or "auto"
        seg["asr_engine"] = "sensevoice"

    if use_cache and segs:
        try:
            import json

            d = _cache_dir()
            d.mkdir(parents=True, exist_ok=True)
            ck = _cache_key(wav_path, model_dir, language)
            (d / f"{ck}.json").write_text(
                json.dumps({"wav": wav_path.name, "lang": lang, "model": model_dir.name, "segments": segs},
                           ensure_ascii=False),
                "utf-8",
            )
        except Exception:
            pass
    return segs


def main() -> None:
    """自检 + 手动转写：python asr_sensevoice.py <wav> [--tokens]"""
    import argparse

    ap = argparse.ArgumentParser(description="SenseVoice 转写自检")
    ap.add_argument("wav", nargs="?")
    ap.add_argument("--tokens", action="store_true", help="打印字级时间戳")
    a = ap.parse_args()
    ok, why = available()
    print(f"[asr] SenseVoice：{'OK' if ok else '不可用'} — {why}")
    if not a.wav:
        sys.exit(0 if ok else 3)
    segs = try_transcribe(Path(a.wav), use_cache=False)
    if segs is None:
        sys.exit(3)
    print(f"[asr] {len(segs)} 句")
    for s in segs:
        print(f"  {s['start_sec']:7.2f}-{s['end_sec']:7.2f} [{s.get('asr_lang','')}] {s['text']}")
        if a.tokens:
            for w in s["words"]:
                print(f"      {w['start_sec']:7.2f} {w['word']}")


if __name__ == "__main__":
    main()
