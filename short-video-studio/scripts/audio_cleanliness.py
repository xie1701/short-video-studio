#!/usr/bin/env python3
"""成片人声到底干不干净——**拿 ASR 反过来验一遍锁定音轨**，不靠脚本自己说剪干净了。

为什么必须有这一步（2026-11-24 用户实测，原话）：
「口播的声音还是不干净，比如 16-19 秒、35-39 秒期间都有导演/摄影师的声音，也不属于口播文案的内容」

剪的时候是按成稿逐字对位的，但「对位算法认为该砍」和「音轨里真的没有别人的话」是两件事：
阈值松一点、成稿覆盖率低一点、DP 把插话当成成稿的字配上了——都会让别人的声音留在片子里，
而所有离线指标（时长、响度、字幕条数）都还是绿的。所以这里做一次独立复核：

1. 用 SenseVoice 重新转写**已经锁定的成片人声**（不是原始素材）；
2. 逐句把转写结果对回该句的成稿文字；
3. 报告「这句话里有多少秒的语音对不上成稿」——那就是别人的话/口癖/废话。

产物：`03b_voice_cleanliness.json`，每句一行，写明脏秒数、脏文字与所在时间码。
默认是**报告**；`--strict` 下脏秒数超过阈值就非 0 退出，可直接当门禁。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

SCRIPTS_ROOT = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_ROOT.parent
for _p in (str(SCRIPTS_ROOT),):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import script_align as SA  # noqa: E402
from common import read_json, write_json  # noqa: E402

OUT_NAME = "03b_voice_cleanliness.json"
# 一句话里对不上成稿的语音超过这么多秒，就算「不干净」
JUNK_FAIL_SEC = 1.0
# 单段脏语音短于这个就当口癖，不进账（口癖切了反而不像人说话）
JUNK_MIN_SEG_SEC = 0.30


def _asr_words(voice_wav: Path) -> List[Dict[str, Any]]:
    """转写成片人声，返回带字级时间的词序列（SenseVoice 优先，缺模型时用 whisper）。"""
    import asr_sensevoice as sv

    segs = sv.try_transcribe(str(voice_wav)) or []
    words: List[Dict[str, Any]] = []
    for s in segs:
        for w in (s.get("words") or []):
            words.append({
                "word": str(w.get("word") or w.get("text") or ""),
                "start_sec": float(w.get("start_sec") or 0.0),
                "end_sec": float(w.get("end_sec") or 0.0),
            })
    return words


def _best_plan_cost(run: str, plan: str) -> float:
    """这段「脏语音」跟成稿里有没有一段听起来一样的字（同音/近音）。

    为什么要分：锁定后的音轨是拼接出来的，同一个人同一句话，重新转写的字会不一样
    （「山上捉翻嚟」→「沙场入边粥」）。如果直接把「对不上成稿」当成脏，就会把
    **他自己的话被 ASR 听错**也记成「别人的声音」，报告就不敢看了。
    """
    chars = [c for c in str(run or "") if c.strip()]
    if not chars:
        return 0.0
    n = len(chars)
    pc = SA.strip_punct(plan)
    if n > len(pc):
        return 9.0
    best = 9.0
    for i in range(0, len(pc) - n + 1):
        tot = 0.0
        for a, b in zip(chars, pc[i:i + n]):
            c = SA._same_kind(a, b)
            tot += 3.0 if c is None else float(c)
        best = min(best, tot / float(n))
    return round(best, 3)


def classify_dirty(run: str, plan: str, source_text: str = "") -> str:
    """把脏语音分类：

    - `self_take`：源素材逐字稿里本来就有这句（≥一半的字出现过）——就是他自己的话，只是拼接后重新转写听错了；
    - `asr_mismatch`：跟成稿某一段同音；
    - `filler`：口癖/语气词；
    - `foreign`：都不是——才是「别人的话」。
    """
    txt = str(run or "").strip()
    if not txt:
        return "filler"
    if source_text:
        # 同一句源窗口里的逐字稿就是「他当时说了什么」。拼接后重新转写会听错字
        # （「山上入边捉翻嚟」被听成「沙场入边粥」），所以按「有几个字出自他自己那句话」判：
        # ≥50% 明确是他的话；≥30% 只能算「疑似他自己的话」——都不当作别人的插话。
        hit = sum(1 for c in txt if c in source_text) / float(max(1, len(txt)))
        if hit >= 0.5:
            return "self_take"
        if hit >= 0.30:
            return "self_take_weak"
    fr = SA._filler_ratio(txt)
    cost = _best_plan_cost(txt, plan)
    if cost <= 0.65 and fr < 0.5:
        return "asr_mismatch"
    if fr >= 0.5:
        return "filler"
    return "foreign"


def _source_words_index(pipe: Path) -> Dict[str, List[Dict[str, Any]]]:
    """源素材逐字稿（`02_transcript.json`）按文件名建索引，用作「他自己的话」的第二个证人。"""
    idx: Dict[str, List[Dict[str, Any]]] = {}
    p = pipe / "02_transcript.json"
    if not p.is_file():
        return idx
    try:
        data = read_json(p)
    except Exception:
        return idx
    for seg in data.get("segments") or []:
        key = Path(str(seg.get("source_media") or "")).name
        for w in seg.get("words") or []:
            idx.setdefault(key, []).append({
                "word": str(w.get("word") or ""),
                "start_sec": float(w.get("start_sec") or 0.0),
                "end_sec": float(w.get("end_sec") or 0.0),
            })
    return idx


def audit(pipeline_dir: Path, voice_wav: Optional[Path] = None) -> Dict[str, Any]:
    pipe = Path(pipeline_dir)
    events = read_json(pipe / "03_audio_events.json")
    wav = Path(voice_wav) if voice_wav else (pipe / "03_locked_voice.wav")
    if not wav.is_file():
        return {"ok": False, "reason": f"找不到锁定人声：{wav}", "sentences": []}

    words = _asr_words(wav)
    src_idx = _source_words_index(pipe)
    rows: List[Dict[str, Any]] = []
    total_junk = 0.0
    total_foreign = 0.0
    total_win = 0.0
    for s in events.get("sentences") or []:
        st = float(s.get("start_sec") or 0.0)
        en = float(s.get("end_sec") or 0.0)
        if en <= st:
            continue
        span = en - st
        total_win += span
        in_win = [w for w in words if st - 0.02 <= float(w["start_sec"]) < en + 0.02]
        text = str(s.get("text") or s.get("say_text") or "")
        # 源素材里这句话原本说了什么（作为「他自己的话」的第二个证人）
        src_key = Path(str(s.get("source_media") or "")).name
        s_in = float(s.get("source_in_sec") or 0.0)
        s_out = float(s.get("source_out_sec") or s_in)
        src_text = "".join(
            str(w["word"]) for w in (src_idx.get(src_key) or [])
            if s_in - 0.10 <= float(w["start_sec"]) <= s_out + 0.10
        )
        if not in_win or not text:
            rows.append({"id": s.get("id"), "start": round(st, 2), "end": round(en, 2),
                         "junk_sec": 0.0, "junk": "", "note": "这句没有可复核的语音"})
            continue
        al = SA.align_text_to_words(text, in_win)
        matched = {tj for _, tj in al["pairs"]}
        # 未被成稿占用的字，按时间连续收成脏段
        dirty: List[List[Dict[str, Any]]] = []
        for ti, w in enumerate(in_win):
            if ti in matched:
                continue
            if dirty and w["start_sec"] - float(dirty[-1][-1]["end_sec"]) <= 0.35:
                dirty[-1].append(w)
            else:
                dirty.append([w])
        junk_sec = 0.0
        junk_txt: List[str] = []
        foreign_sec = 0.0
        foreign_txt: List[str] = []
        kinds: Dict[str, float] = {}
        for grp in dirty:
            dur = float(grp[-1]["end_sec"]) - float(grp[0]["start_sec"])
            if dur < JUNK_MIN_SEG_SEC:
                continue
            run_txt = "".join(str(w["word"]) for w in grp)
            kind = classify_dirty(run_txt, text, src_text)
            kinds[kind] = round(kinds.get(kind, 0.0) + dur, 2)
            junk_sec += dur
            junk_txt.append(run_txt)
            if kind == "foreign":
                foreign_sec += dur
                foreign_txt.append(run_txt)
        total_junk += junk_sec
        total_foreign += foreign_sec
        rows.append({
            "id": s.get("id"),
            "start": round(st, 2),
            "end": round(en, 2),
            "coverage": round(al["coverage"], 3),
            "junk_sec": round(junk_sec, 2),
            "foreign_sec": round(foreign_sec, 2),
            "junk": " | ".join(junk_txt),
            "foreign": " | ".join(foreign_txt),
            "kinds": kinds,
            "text": text[:40],
        })

    worst = sorted((r for r in rows if r.get("foreign_sec")), key=lambda r: -r["foreign_sec"])[:8]
    return {
        "ok": total_foreign <= JUNK_FAIL_SEC,
        "engine": "sensevoice",
        "voice_wav": str(wav),
        "speech_sec": round(total_win, 2),
        "junk_sec": round(total_junk, 2),
        "foreign_sec": round(total_foreign, 2),
        "junk_ratio": round(total_junk / total_win, 4) if total_win else 0.0,
        "foreign_ratio": round(total_foreign / total_win, 4) if total_win else 0.0,
        "threshold_sec": JUNK_FAIL_SEC,
        "worst": worst,
        "sentences": rows,
        "note_cn": (
            "junk_sec = 这句锁定音轨里对不上成稿的语音秒数；其中再分三类："
            "asr_mismatch=他自己的话被重新转写听错（不算脏）、filler=单个语气词、"
            "foreign=实义但不在成稿里（别人的话/故障重来），门禁只看 foreign。"
            "这是拿 ASR 反过来验成片，不是脚本自评。"
        ),
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="复核锁定人声里还有多少「不是成稿」的话")
    ap.add_argument("--pipeline-dir", required=True)
    ap.add_argument("--voice-wav", help="默认 <pipeline>/03_locked_voice.wav")
    ap.add_argument("--strict", action="store_true", help="脏语音超过阈值就非 0 退出（门禁用法）")
    args = ap.parse_args(argv)

    pipe = Path(args.pipeline_dir)
    res = audit(pipe, Path(args.voice_wav) if args.voice_wav else None)
    write_json(pipe / OUT_NAME, res)
    print(f"[voice-cleanliness] 锁定人声 {res.get('speech_sec', 0):.1f}s，"
          f"对不上成稿的语音 {res.get('junk_sec', 0):.2f}s，"
          f"其中确属别人的话 {res.get('foreign_sec', 0):.2f}s"
          f"（{res.get('foreign_ratio', 0) * 100:.1f}%）")
    for r in res.get("worst") or []:
        print(f"  @{r['start']:.2f}-{r['end']:.2f}s  别人的话 {r['foreign_sec']:.2f}s  "
              f"「{r.get('foreign', '')[:40]}」")
    print(f"[voice-cleanliness] -> {pipe / OUT_NAME}")
    if args.strict and not res.get("ok"):
        print(f"[voice-cleanliness] FAIL：别人的话 {res.get('foreign_sec')}s > 阈值 {JUNK_FAIL_SEC}s")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
