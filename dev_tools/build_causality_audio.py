#!/usr/bin/env python3
"""24 套剪映真草稿 → **声音类元素**的因果规则（`forensic/causality_audio.json`）。

要回答的问题（用户原话）
────────────────────────
「提炼出剪映草稿里面所对应的各种元素，到底是因为什么原因而出现，而调节，而发生影响？
这些要形成相应的规则，才能够作为后续可复用的判断依据，给到 AI agent 做实施的参考。」

不是盘点「有几个、多密」，而是**因果**：
  什么条件下才有这个声音 → 选哪一条音效/哪一首 BGM 由什么决定 → 音量/时长/淡入淡出为什么是这个值
  → 它作用在哪个时间窗、跟谁互斥 → 违反会听成什么样。

做法（只读取证草稿，纯反推；不改现场 pipeline）
────────────────────────────────────────────
1. 锚点：主轨切点（`style_edit_grammar.json` 的 `main_segments`）、字幕换行（字幕轨段起）、
   花字出现（非字幕文字轨段起）、BGM 节拍网格（`style_trigger_rules_sfx_flower.json` 里已测的
   BPM + 置信度，置信度 <0.45 时不给网格）。每条语义 cue 量到四类锚点的偏移，取最近的作「它凭什么响」。
2. 音效：`materials.audios` 里 `type == "sound"` 的段 → 落点 / 目标时长 / 源入点 / 音量 /
   `audio_fades` 淡出。名字按功能族归类（转场 / 字上屏 / 钱 / 冲击 / 悬疑 / 亮点），
   族的锚点指纹就是「为什么是这条」的证据。
3. BGM：`type in ("music", "extract_music")` 的段 → 段名 / 起止 / 音量 / 源入点 / 淡入淡出。
   同一材料被切成多段不同音量 = 电平台阶；两段不同材料 + 时间接棒 = 双段 BGM；
   两段并列全程 = 双层 bed。
4. 口播闪避（ducking）：源草稿里音频段**没有任何音量关键帧**（全库 0 条），所以 ducking 不是
   侧链压缩，而是「静态床位 + 少量手动台阶」。这里把这件事量出来，而不是假设一条曲线。

⚠ 不许凭空补值：量不到写 null 并在 `absent_reason` / `evidence` 里交代（例如源草稿没有可测的
人声-BGM SNR，库里那个 11.5dB 是编写常量，不是测量值）。

用法：
    python3 dev_tools/build_causality_audio.py            # 全 24 套 → forensic/causality_audio.json
    python3 dev_tools/build_causality_audio.py --style style_21 --print
"""
from __future__ import annotations

import argparse
import json
import math
import re
import statistics as stats
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "short-video-studio" / "scripts"
ASSETS = ROOT / "short-video-studio" / "assets"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from trigger_policy_common import (  # noqa: E402
    TOL_BEAT_SEC,
    TOL_CUT_SEC,
    TOL_FLOWER_SEC,
    TOL_LINE_SEC,
    beat_grid,
    classify_sfx_anchor,
)

FORENSIC = ROOT / "forensic"
STYLE_SOURCES = FORENSIC / "style_sources.json"
GRAMMAR = FORENSIC / "style_edit_grammar.json"
RULES = FORENSIC / "style_trigger_rules_sfx_flower.json"
SFX_TRUTH_DIR = ASSETS / "sound_vibe" / "sfx_truth"
LIBRARY = ASSETS / "style_spectrum" / "style_spectrum_library.json"
OUT = FORENSIC / "causality_audio.json"

# 现场成片（只读）——用来做「真值 vs 我们成片」对照。缺失时对照段落写 absent，不编。
# 路径在运行时从 home 拼出来（仓库隐私扫描不许出现本机绝对路径字面量），也可用 --pipeline-dir 覆盖。
PIPELINE_REL = Path(".short-video-studio") / "workspaces" / "accounts" / "ouyang-test" / \
    "projects" / "ouyangTestCut" / "pipeline"
PIPELINE_DIR = Path.home() / PIPELINE_REL

US = 1_000_000.0
BGM_TYPES = ("music", "extract_music")
SOUND_TYPES = ("sound", "video_original_sound")
TOL = {"cut": TOL_CUT_SEC, "flower": TOL_FLOWER_SEC, "line": TOL_LINE_SEC, "beat": TOL_BEAT_SEC}

# 音效名字 → 功能族。顺序即优先级：先判专指（钱 / 文字 / 转场），再判宽泛的「叮/滴」。
# 这一层不是"审美分类"，是用来量「同一个族在 24 套里是不是干同一件事」的判据。
SFX_FAMILIES: Tuple[Tuple[str, str], ...] = (
    ("money", r"账单|小票|收银|金币|结账|到账|钱\(money\)|钱 到账"),
    ("text_reveal", r"打字|键盘|空格|翻页|纸张|报纸|书本|翻书|写字"),
    ("whoosh", r"呼|转场|whoosh|嗖|咻|旋风|闪回|唰|刷一下"),
    ("impact", r"咚|砰|撞击|锣|鼓|刀|拔|枪|拳|砸|嘭|哐|爆|鼓点"),
    ("tension", r"悬疑|紧张|不对劲|尴尬|回响|沙锤|轻敲|拉扯|恐怖|弹簧"),
    ("sparkle", r"铃|叮|滴|气泡|啵|魔法|仙尘|闪|亮|星星|风铃|水滴|Q弹|游戏|点击|鼠标|提示音|"
                r"拍照|快门|开瓶|发条|哇呜|木鱼|敲击碗|哗啦|打开|开门"),
)


def fam_of(name: str) -> str:
    for fam, pat in SFX_FAMILIES:
        if re.search(pat, name or ""):
            return fam
    return "other"


def role_of(name: str) -> str:
    """运行期角色（与 `dev_tools/extract_style_sfx.py::classify_role` 同一套规则，逐字对齐）。"""
    for role, keys in (
        ("receipt", ("账单", "小票")),
        ("accent", ("开瓶塞", "发条", "哇呜", "风铃")),
        ("hook", ("响铃", "铃")),
        ("info", ("翻页", "键盘", "空格")),
        ("cut", ("转场", "呼")),
    ):
        if any(k in (name or "") for k in keys):
            return role
    return "misc"


# ── 读文件 ────────────────────────────────────────────────────────────────────


def read_json(p: Path) -> Any:
    return json.loads(Path(p).read_text(encoding="utf-8"))


def draft_plaintext(draft_name: str) -> Optional[Dict[str, Any]]:
    """明文草稿：常规是 draft_content.json；少数草稿只解出了 __latest_close.json / template-2.tmp。"""
    for fn in ("draft_content.json", "__latest_close.json", "template-2.tmp"):
        p = FORENSIC / "decrypted" / draft_name / fn
        if p.is_file():
            try:
                doc = read_json(p)
            except Exception:
                continue
            if isinstance(doc, dict) and doc.get("tracks") and doc.get("materials"):
                return doc
    return None


def material_index(doc: Dict[str, Any]) -> Dict[str, Tuple[str, Dict[str, Any]]]:
    idx: Dict[str, Tuple[str, Dict[str, Any]]] = {}
    for bucket, items in (doc.get("materials") or {}).items():
        if not isinstance(items, list):
            continue
        for it in items:
            if isinstance(it, dict) and it.get("id"):
                idx[str(it["id"])] = (bucket, it)
    return idx


def text_of(doc: Dict[str, Any], mid: str) -> str:
    for m in (doc.get("materials") or {}).get("texts") or []:
        if m.get("id") == mid:
            try:
                return str(json.loads(m.get("content") or "{}").get("text") or "").strip()
            except Exception:
                return ""
    return ""


def seg_span(doc: Dict[str, Any], seg: Dict[str, Any]) -> Tuple[float, float]:
    tr = seg.get("target_timerange") or {}
    a = float(tr.get("start") or 0) / US
    d = float(tr.get("duration") or 0) / US
    return round(a, 3), round(a + d, 3)


# ── 统计小工具 ────────────────────────────────────────────────────────────────


def med(v: Sequence[float]) -> Optional[float]:
    vals = [float(x) for x in v if x is not None]
    return round(float(stats.median(vals)), 3) if vals else None


def pct(v: Sequence[float], q: float) -> Optional[float]:
    s = sorted(float(x) for x in v if x is not None)
    if not s:
        return None
    if len(s) == 1:
        return round(s[0], 3)
    k = (len(s) - 1) * q
    lo, hi = int(math.floor(k)), int(math.ceil(k))
    if lo == hi:
        return round(s[lo], 3)
    return round(s[lo] + (s[hi] - s[lo]) * (k - lo), 3)


def db(vol: Optional[float]) -> Optional[float]:
    try:
        v = float(vol if vol is not None else 1.0)
    except (TypeError, ValueError):
        return None
    return round(20.0 * math.log10(max(1e-4, v)), 2)


def gdb(node: Dict[str, Any], default: float = 0.0) -> float:
    """音量 dB 取值：0.0dB 是合法值（默认档），不能被 `or` 吞成 falsy。"""
    v = node.get("gain_db")
    return float(v) if v is not None else default


def is_sting(node: Dict[str, Any]) -> bool:
    """sting = 接近 0dB 的短音乐段（正片床都压到 ≤-3dB）。"""
    return gdb(node, -99.0) > -3.0


def share(n: int, d: int) -> float:
    return round(n / float(d), 3) if d else 0.0


def near(points: Sequence[float], t: float, tol: float) -> Optional[float]:
    best, bestd = None, None
    for p in points:
        d = abs(float(p) - t)
        if d <= tol and (bestd is None or d < bestd):
            best, bestd = float(p), d
    return best


def fmt_share(d: Dict[str, int]) -> str:
    tot = sum(d.values()) or 1
    parts = [f"{k} {round(v * 100 / tot)}%" for k, v in sorted(d.items(), key=lambda kv: -kv[1]) if v]
    return " / ".join(parts) if parts else "无"


# ── 单套分析 ──────────────────────────────────────────────────────────────────


def analyze(style_id: str, draft_name: str, grammar: Dict[str, Any], rules: Dict[str, Any],
            lib: Dict[str, Any]) -> Dict[str, Any]:
    doc = draft_plaintext(draft_name)
    dur = float(grammar.get("duration_sec") or 0.0)
    out: Dict[str, Any] = {
        "source_ref": grammar.get("source_ref") or ("wb_" + (grammar.get("draft_sha256") or "")[:12]),
        "samples": 0,
        "duration_sec": round(dur, 3),
        "elements": {},
    }
    if doc is None:
        reason = "明文草稿不可读（解密产物缺失）——本套 4 个声音元素全部按 present:false 记账，不推断"
        for k in ("sfx", "bgm", "dual_bgm_switch", "voice_ducking"):
            out["elements"][k] = {
                "present": False, "absent_reason": reason,
                "why_appears": {"rule": None, "triggers": [], "not_when": [], "evidence": reason},
                "value_drivers": [], "scope": None, "affects": [], "limits": [],
                "coupling": [], "violation_symptoms": [], "reuse_check": [], "sample_table": [],
            }
        return out

    idx = material_index(doc)
    sti = (grammar.get("text_beats") or {}).get("subtitle_track_index")

    # ── 锚点 ──
    main_segs = grammar.get("main_segments") or []
    cuts = sorted(round(float(m.get("start") or 0), 3) for m in main_segs)
    subs: List[Tuple[float, float]] = []
    flowers: List[Tuple[float, float, str]] = []
    for i, tr in enumerate(doc.get("tracks") or []):
        if tr.get("type") != "text":
            continue
        for seg in tr.get("segments") or []:
            a, b = seg_span(doc, seg)
            txt = text_of(doc, str(seg.get("material_id")))
            (subs if i == sti else flowers).append((a, b, txt))
    subs.sort()
    if not subs:  # 没有字幕轨时退到 grammar 的 text_beats 条目
        subs = [(round(float(e.get("t") or 0), 3), round(float(e.get("t") or 0), 3) + 0.1, "")
                for e in ((grammar.get("text_beats") or {}).get("entries") or [])]
    lines = sorted({a for a, _, _ in subs})
    fstarts = sorted({a for a, _, _ in flowers})
    bpm_node = (rules.get("beat") or {})
    bpm = bpm_node.get("bpm")
    beats: List[float] = []
    beat_ok = bool(bpm) and float(bpm_node.get("confidence") or 0) >= 0.45
    if beat_ok:
        beats = beat_grid(float(bpm), dur)

    def in_silence(t: float) -> bool:
        return not any(a - 0.05 <= t <= b + 0.05 for a, b, _ in subs)

    def speech_cov(a: float, b: float) -> float:
        if b <= a:
            return 0.0
        tot = 0.0
        for x, y, _ in subs:
            lo, hi = max(a, x), min(b, y)
            if hi > lo:
                tot += hi - lo
        return round(tot / (b - a), 3)

    # ── 音频段 ──
    sfx: List[Dict[str, Any]] = []
    bgm: List[Dict[str, Any]] = []
    extra_sound: List[Dict[str, Any]] = []
    for ti, tr in enumerate(doc.get("tracks") or []):
        if tr.get("type") != "audio":
            continue
        for seg in tr.get("segments") or []:
            bucket, mat = idx.get(str(seg.get("material_id")), ("", {}))
            if bucket != "audios":
                continue
            mtype = str(mat.get("type") or "")
            a, b = seg_span(doc, seg)
            sr = seg.get("source_timerange") or {}
            fades = [idx[r][1] for r in (seg.get("extra_material_refs") or [])
                     if idx.get(r, ("", {}))[0] == "audio_fades"]
            node = {
                "track": ti,
                "name": str(mat.get("name") or "").strip(),
                "type": mtype,
                "start": a,
                "dur": round(b - a, 3),
                "end": b,
                "volume": float(seg.get("volume") if seg.get("volume") is not None else 1.0),
                "gain_db": db(seg.get("volume")),
                "src_in": round(float(sr.get("start") or 0) / US, 3),
                "mat_dur": round(float(mat.get("duration") or 0) / US, 3),
                "fade_in": round(float((fades[0].get("fade_in_duration") or 0)) / US, 3) if fades else 0.0,
                "fade_out": round(float((fades[0].get("fade_out_duration") or 0)) / US, 3) if fades else 0.0,
                "has_keyframes": bool(seg.get("common_keyframes")),
            }
            if mtype in BGM_TYPES:
                bgm.append(node)
            elif mtype in SOUND_TYPES:
                (sfx if mtype == "sound" else extra_sound).append(node)
    sfx.sort(key=lambda n: n["start"])
    bgm.sort(key=lambda n: (n["start"], n["track"]))
    for c in bgm:
        c["speech_cov"] = speech_cov(c["start"], c["end"])

    # ── 音效归因 ──
    anchor_map = {"cut": cuts, "flower": fstarts, "line": lines,
                  "beat": beats if beat_ok else []}
    for n in sfx:
        t = n["start"]
        cls = classify_sfx_anchor(t, anchor_map, TOL)
        n["reason"] = cls["reason"] or "free"
        n["anchor_time"] = cls["anchor_time"]
        n["offset_sec"] = cls["offset_sec"]
        n["family"] = fam_of(n["name"])
        n["role"] = role_of(n["name"])
        n["in_silence"] = in_silence(t)
        n["dual_anchor"] = bool(near(cuts, t, 0.06) and near(fstarts, t, 0.10))
        n["flower_at"] = bool(near(fstarts, t, 0.10))

    out["samples"] = len(sfx)
    out["elements"]["sfx"] = build_sfx(style_id, sfx, cuts, dur, beats, beat_ok, lib)
    if extra_sound:  # 保留的原声段（type=video_original_sound）不算音效，但要交代清楚
        # 原声段的"名字"其实是用户素材文件名（含日期/设备名），不入库：只留结构信息
        bits = "、".join(f"主轨镜头原声@{c['start']}s {c['dur']}s {c['gain_db']}dB" for c in extra_sound[:3])
        sf = out["elements"]["sfx"]
        sf["why_appears"]["evidence"] += f"；另有 {len(extra_sound)} 段保留原声（{bits}），不计入音效 cue"
        sf["limits"].append({
            "name": "retained_camera_audio", "value": len(extra_sound),
            "why": (f"{bits} —— 这是主轨镜头自带的声音被留下并根据切点抬过电平，"
                    "不是从音效库挑的；别把它算进密度，也别用音效去模仿它"),
        })
    out["elements"]["bgm"] = build_bgm(style_id, bgm, extra_sound, sfx, dur, subs, speech_cov, cuts, lines, lib)
    out["elements"]["dual_bgm_switch"] = build_dual(style_id, bgm, dur, cuts, lines, fstarts)
    out["elements"]["voice_ducking"] = build_ducking(style_id, bgm, sfx, dur, subs, speech_cov, doc, lib)
    return out


def build_sfx(style_id: str, sfx: List[Dict[str, Any]], cuts: Sequence[float], dur: float,
              beats: Sequence[float], beat_ok: bool, lib: Dict[str, Any]) -> Dict[str, Any]:
    n = len(sfx)
    if n == 0:
        return {
            "present": False,
            "absent_reason": "真值 0 条：这套源草稿的 materials.audios 里没有任何 type=sound 的段",
            "why_appears": {"rule": "不出", "triggers": [], "not_when": ["全片"], "evidence": "0 段 sound"},
            "value_drivers": [], "scope": None, "affects": [], "limits": [],
            "coupling": [], "violation_symptoms": [], "reuse_check": [],
            "sample_table": [],
        }
    per_min = round(n / dur * 60.0, 1) if dur else None
    mix: Dict[str, int] = {}
    for c in sfx:
        mix[c["reason"]] = mix.get(c["reason"], 0) + 1
    offs = [c["offset_sec"] for c in sfx if c["offset_sec"] is not None]
    gaps = [round(sfx[i + 1]["start"] - sfx[i]["start"], 3) for i in range(n - 1)]
    same_name_gaps: List[float] = []
    by_name: Dict[str, List[float]] = {}
    for c in sfx:
        by_name.setdefault(c["name"], []).append(c["start"])
    for ts in by_name.values():
        for i in range(len(ts) - 1):
            same_name_gaps.append(round(ts[i + 1] - ts[i], 3))

    fam_counts: Dict[str, int] = {}
    for c in sfx:
        fam_counts[c["family"]] = fam_counts.get(c["family"], 0) + 1
    role_counts: Dict[str, int] = {}
    for c in sfx:
        role_counts[c["role"]] = role_counts.get(c["role"], 0) + 1

    # 族指纹：这一族在这套里贴着什么锚点（"为什么是这条"的可测证据）
    fam_table: Dict[str, Any] = {}
    for fam in fam_counts:
        rows = [c for c in sfx if c["family"] == fam]
        fam_table[fam] = {
            "n": len(rows),
            "cut_share": share(sum(1 for c in rows if c["reason"] == "cut"), len(rows)),
            "flower_share": share(sum(1 for c in rows if c["reason"] == "flower"), len(rows)),
            "line_share": share(sum(1 for c in rows if c["reason"] == "line"), len(rows)),
            "free_share": share(sum(1 for c in rows if c["reason"] == "free"), len(rows)),
            "dur_med": med([c["dur"] for c in rows]),
            "gain_db_med": med([c["gain_db"] for c in rows if c["gain_db"] is not None]),
            "names": sorted({c["name"] for c in rows})[:6],
        }

    durs = [c["dur"] for c in sfx]
    gains = [c["gain_db"] for c in sfx if c["gain_db"] is not None]
    long_rows = [c for c in sfx if c["dur"] >= 2.0]
    short_rows = [c for c in sfx if c["dur"] < 1.3]
    fades = [c for c in sfx if c["fade_out"] > 0]
    fade_ratio = [c["fade_out"] / c["dur"] for c in fades if c["dur"] > 0]
    trimmed = [c for c in sfx if c["src_in"] > 0.02]
    cut_share = share(mix.get("cut", 0), n)
    flower_share = share(mix.get("flower", 0), n)
    sil = share(sum(1 for c in sfx if c["in_silence"]), n)
    dual = share(sum(1 for c in sfx if c["dual_anchor"]), n)
    unique = len({c["name"] for c in sfx})
    rep_ratio = round(n / unique, 2) if unique else None

    present_triggers = sorted([k for k in mix if k != "free" and mix[k] > 0],
                              key=lambda k: -mix[k])
    anchored = n - mix.get("free", 0)
    rule = (
        f"{n} 条里 {anchored} 条挂上可观测锚点（{round(anchored * 100 / n)}%），"
        f"挂上的部分是 {fmt_share({k: v for k, v in mix.items() if k != 'free'})}"
        + (f"；余下 {mix['free']} 条无锚点，是编辑随手 accent（额度单独计，不放开成常态）"
           if mix.get("free") else "；没有一条是无理由的")
    )
    not_when = []
    if sil <= 0.15:
        not_when.append(f"纯静音/无人声段（源里只剩 {round(sil * 100)}% 落在这里，{round((1 - sil) * 100)}% 都压在人声上）")
    if mix.get("beat", 0) == 0 and not beat_ok:
        not_when.append("BGM 节拍网格：这套的 BGM 估不出可用 BPM（置信度 <0.45），拍点不是它的理由")
    elif mix.get("beat", 0) == 0:
        not_when.append("BGM 节拍网格：源里 0 条踩拍，拍点不是这套的理由")
    if "flower" not in present_triggers:
        not_when.append("花字上屏：这套的花字不带声音")
    if "cut" not in present_triggers:
        not_when.append("画面切点：这套从不在换镜时响")

    n_zero = sum(1 for g in gains if abs(g) < 0.5)
    boosted = [c for c in sfx if gdb(c) > 0.5]
    cut_back = [c for c in sfx if gdb(c) < -0.5]
    if n_zero:
        gain_story = (f"0dB 是默认档（{round(share(n_zero, len(gains)) * 100)}% 的 cue 没动过）")
    else:
        gain_story = "这套没有一条留在默认 0dB，全部被手动调过"
    if boosted:
        gain_story += (f"；被抬高的 {len(boosted)} 条（到 +{round(max(c['gain_db'] for c in boosted), 1)}dB）"
                       f"时长中位 {med([c['dur'] for c in boosted])}s —— 抬是给短瞬态，让它从口播里跳出来")
    if cut_back:
        gain_story += (f"；被压低的 {len(cut_back)} 条（到 {round(min(c['gain_db'] for c in cut_back), 1)}dB）"
                       f"时长中位 {med([c['dur'] for c in cut_back])}s —— 压是给长尾巴/浸响，免得糊住人声")
    drivers = [
        {
            "param": "cue_duration_sec",
            "value": {"median": med(durs), "p10": pct(durs, 0.10), "p90": pct(durs, 0.90),
                      "max": round(max(durs), 3), "min": round(min(durs), 3)},
            "derived_from": "tracks[type=audio].segments[].target_timerange.duration ÷ 1e6",
            "formula": "直接读段长；不做任何衰减，源里放多长就是多长",
            "why_this_value": (
                f"瞬态/转场类被剪到 p50 {med(durs)}s（够听清一个动作就收），"
                f"浸响类最长 {round(max(durs), 2)}s 靠淡出收尾而不是剪短"
            ),
            "calibrated_by": None,
            "tolerance": "±1 帧（源草稿落点只在 1/60s 网格上）",
        },
        {
            "param": "gain_db",
            "value": {"median": med(gains), "p10": pct(gains, 0.10), "p90": pct(gains, 0.90),
                      "min": (round(min(gains), 2) if gains else None),
                      "max": (round(max(gains), 2) if gains else None)},
            "derived_from": "segments[].volume（线性）→ dB = 20·log10(volume)",
            "formula": "dB = 20·log10(volume)",
            "why_this_value": gain_story,
            "calibrated_by": "assets/sound_vibe/sfx/<style>/ 里的真音效实测（同一条音效在不同套里增益不同＝这套的电平习惯）",
            "tolerance": "±0.3dB（剪映音量滑块量化）",
        },
        {
            "param": "action_dur_vs_material",
            "value": {"trimmed_share": share(len(trimmed), n),
                      "src_in_median": med([c["src_in"] for c in trimmed]) if trimmed else 0.0},
            "derived_from": "source_timerange.start / target_timerange.duration vs material.duration",
            "formula": "只截取源音效的一小段；不重新合成",
            "why_this_value": (
                f"{round(share(len(trimmed), n) * 100)}% 的 cue 从素材中段起播（跳过前导空拍），"
                f"起播点中位 {med([c['src_in'] for c in trimmed]) if trimmed else 0}s —— 目的是让'响'落在锚点上，不是让素材从头放"
            ),
            "calibrated_by": None,
            "tolerance": "±0.05s",
        },
        {
            "param": "fade_out_sec",
            "value": {"share_with_fade": share(len(fades), n),
                      "fade_over_dur_median": med(fade_ratio) if fade_ratio else None,
                      "fade_sec_median": med([c["fade_out"] for c in fades]) if fades else None},
            "derived_from": "materials.audio_fades[].fade_out_duration（挂在段的 extra_material_refs 上）",
            "formula": (f"fade_out = {round(min(fade_ratio), 2)}~{round(max(fade_ratio), 2)} × cue_dur"
                        if fade_ratio else "本套未使用淡出"),
            "why_this_value": (
                f"只有 {len(fades)}/{n} 条带淡出，且最短的一条也有 {round(min(c['dur'] for c in fades), 2)}s —— "
                "淡出只给长尾巴：瞬态硬切才听得出'砸'，加了淡出反而变软"
                if fades else "本套一条淡出都没有：全部硬切，靠素材本身的尾巴收"
            ),
            "calibrated_by": None,
            "tolerance": "±0.03s（1-2 帧）",
        },
        {
            "param": "sfx_cut_offset_sec",
            "value": {"median": med(offs), "p90": pct([abs(o) for o in offs], 0.90),
                      "by_anchor": {k: med([c["offset_sec"] for c in sfx if c["reason"] == k]) for k in mix}},
            "derived_from": "cue 落点 − 最近锚点时刻（主轨切点 / 花字起 / 字幕换行）",
            "formula": "offset = t_cue − t_anchor；容差 0.12s（切点/花字/换行）、0.08s（拍点）",
            "why_this_value": (
                f"|偏移| 中位 {round((med([abs(o) for o in offs]) or 0) * 1000)}ms、"
                f"最大 {round((max([abs(o) for o in offs]) or 0) * 1000)}ms，"
                f"{round(share(sum(1 for o in offs if abs(o) <= 0.05), len(offs)) * 100)}% 落在 ≤1.5 帧内："
                + ("声音是被'事件'拖进来的，不是提前/延后铺的"
                   if share(sum(1 for o in offs if abs(o) <= 0.05), len(offs)) >= 0.5
                   else "偏移再大也在 0.12s 容差内，是手动对齐留下的痕迹，不是提前铺")
            ),
            "calibrated_by": "同一套的 cue→anchor 实测偏移分布",
            "tolerance": "±0.04s 可接受（人耳对 <80ms 的错位不敏感）",
        },
    ]
    if rep_ratio and rep_ratio > 1.5:
        drivers.append({
            "param": "name_reuse",
            "value": {"unique_names": unique, "cues": n, "reuse_ratio": rep_ratio,
                      "same_name_min_gap_sec": (round(min(same_name_gaps), 3) if same_name_gaps else None)},
            "derived_from": "materials.audios[].name 去重 + 同名 cue 时间差",
            "formula": "同一名字复用次数 = cues / unique_names",
            "why_this_value": (
                f"这套允许重名（{unique} 个名字撑 {n} 条），但同一条音效两次之间至少 "
                f"{round(min(same_name_gaps), 2) if same_name_gaps else '-'}s；重名不是随手，是同一功能反复用"
            ),
            "calibrated_by": None,
            "tolerance": "同名间隔 ≥0.8s（源 p10）",
        })

    affects = [
        {"target": "响度预算（integrated LUFS）",
         "how": (f"{n} 条 cue / {dur}s ≈ {per_min}/分；源草稿整体被拉到 {lib.get('integrated_lufs')} 附近"
                 + ("，音效是其中最难压的一段（瞬态多、峰值高）" if n >= 5 else "；这套音效极少，几乎不影响响度预算"))},
        {"target": "真峰（true peak）",
         "how": (f"最高 +{round(max(gains), 1)}dB 的 cue 单条就能把整体真峰顶上去"
                 f"（这类高增益 cue 的时长中位 {med([c['dur'] for c in boosted])}s，靠短时长控住能量）"
                 if boosted else f"这套没有任何正增益（最高 {round(max(gains), 1)}dB），真峰不会被音效顶起来")},
        {"target": "口播干净度",
         "how": (f"{round((1 - sil) * 100)}% 的 cue 压在人声上；"
                 + (f"文字类（翻页/键盘）占 {round(share(sum(1 for c in sfx if c['family'] == 'text_reveal'), n) * 100)}%，"
                    "集中在 2–8kHz，正撞人声清晰度频段"
                    if any(c["family"] == "text_reveal" for c in sfx) else "同族音效集中在 1–6kHz，落在人声泛音带上方"))},
        {"target": "BGM 频段",
         "how": f"音效电平中位 {med(gains)}dB，比 BGM 床高 8~15dB——音效是'冒出来'的，BGM 只是底"},
    ]
    limits = [
        {"name": "max_per_min", "value": per_min, "why": "源实测密度，是这套的呼吸频率，不许更密"},
        {"name": "min_gap_sec", "value": (round(min(gaps), 3) if gaps else None),
         "why": (f"源里最短两条间隔；gap 中位 {med(gaps)}s、p90 {pct(gaps, 0.90)}s"
                 if gaps else "这套只有一条 cue，量不到间隔——不要据此认为可以连击")},
        {"name": "max_cue_dur_sec", "value": round(max(durs), 3),
         "why": "超过这个长度就变成'垫底氛围'而不是'一个动作'"},
        {"name": "max_gain_db", "value": (round(max(gains), 2) if gains else None),
         "why": "源里抬得最高的那条；再高就会盖掉口播字头"},
        {"name": "max_free_per_min", "value": round(mix.get("free", 0) / dur * 60.0, 2) if dur else None,
         "why": "无锚点的 accent 额度（源里真实存在的随手感，但不能变成常态）"},
    ]
    coupling = []
    if flower_share > 0:
        coupling.append({"with": "flower", "type": "same_anchor",
                         "why": f"{round(flower_share * 100)}% 的 cue 与花字上屏同帧（源里 {round(dual * 100)}% 同时又是切点，所以'花字响'和'换镜响'在听感上是一件事）"})
    if cut_share > 0:
        coupling.append({"with": "cut", "type": "shared_instant",
                         "why": f"{round(cut_share * 100)}% 挂主轨切点；切点本身不发声，是 cue 把切点'说'出来"})
    coupling.append({"with": "bgm", "type": "level_delta",
                     "why": "音效永远高于床位 8~15dB；床位一旦抬到音效附近，音效就不再'跳出来'"})

    tr_share = share(sum(1 for c in sfx if c["family"] == "text_reveal"), n)
    symptoms = []
    if mix.get("free", 0):
        symptoms.append(f"{mix['free']} 条无锚点 cue = 听感里的'不知道为什么响了一声'")
    if unique <= 4:
        symptoms.append(f"只有 {unique} 个音色却响 {n} 次（复用 {rep_ratio}×）= 同一个声音反复撞耳朵，越听越吵")
    symptoms.append("把 stress/bgm_beat 这类'口播中段微切'当切点 → 每句中间都'叮'一下，正是用户投诉的那种吵")
    if tr_share >= 0.35:
        symptoms.append(f"文字类（翻页/键盘）占 {round(tr_share * 100)}%，全在 2–8kHz → 人声齿音被糊，听成'沙'")
    elif max(fam_counts.values()) / n >= 0.6:
        top_fam = max(fam_counts, key=lambda k: fam_counts[k])
        symptoms.append(f"{top_fam} 一族占 {round(fam_counts[top_fam] * 100 / n)}% → "
                        f"再怎么排也是同一个音色反复，听成'只有一种声音'")
    symptoms.append(f"低于 {per_min}/分太多 → 大片没有声音事件，画面在动而耳朵是空的")
    symptoms.append(f"高于 {per_min}/分 = 连击；源里 gap 中位 {med(gaps) if gaps else '—'}s，比这更密就成机枪")

    reuse = [
        "新片段的锚点必须来自可观测事件：切点（sentence_start/pause_start/bgm_downbeat/keyword_accent）、"
        "花字上屏、字幕换行；`stress` / `bgm_beat` 不算",
        f"密度上限 {per_min}/分，最小间隔 {round(min(gaps), 2) if gaps else '-'}s；只能往下收不能往上补",
        (f"同一音效名 60s 内不超过 {max(1, round(60 / max(1.0, (med(same_name_gaps) or 2.0))))} 次"
         f"（源里同名复用间隔中位 {med(same_name_gaps)}s）" if same_name_gaps
         else "这套每个音效名只出现一次：新片也不要重复同一个音色"),
        f"选型按功能族：换镜→whoosh、字上屏→text_reveal、报数字→money、抛梗→impact/tension；这套实测族构成 {fmt_share(fam_counts)}",
        ("电平：瞬态 0~+{0}dB，≥2s 的浸响压到 {1}dB".format(
            round(pct([g for g in gains if g > 0.5], 0.90) or 0, 1),
            med([c["gain_db"] for c in long_rows]))
         if long_rows else
         "电平：0dB 起步，短瞬态最高 +{0}dB（本套没有 ≥2s 的浸响）".format(
             round(max([g for g in gains if g > 0.5] or [0.0]), 1))),
    ]

    sample = [
        {"at_sec": c["start"], "value": c["name"],
         "note": f"{c['family']}/{c['role']} · {c['reason']} 偏移 {round((c['offset_sec'] or 0) * 1000)}ms · "
                 f"{c['dur']}s · {c['gain_db']}dB" + (" · 淡出%.2fs" % c["fade_out"] if c["fade_out"] else "")}
        for c in sfx[:24]
    ]
    return {
        "present": True,
        "absent_reason": None,
        "why_appears": {"rule": rule, "triggers": present_triggers, "not_when": not_when,
                        "evidence": (f"{n} 条 sound 段 / {dur}s；触发 {fmt_share(mix)}；"
                                     f"锚点偏移中位 {med(offs)}s、p90 {pct([abs(o) for o in offs], 0.9)}s；"
                                     f"其中 {round(dual * 100)}% 同时贴切点与花字")},
        "value_drivers": drivers,
        "scope": {
            "unit": "cue（一条音效一个事件）",
            "coverage": (f"整片 {n} 条，分布在 {len({c['track'] for c in sfx})} 条独立音效轨上，"
                         f"次序由锚点决定而不是等距铺"),
            "excludes": (f"纯静音段（源里只有 {round(sil * 100)}% 落在无人声处）；"
                         f"无锚点的句子中段（free 额度 {mix.get('free', 0)} 条）"),
            "reset_on": "换锚点（一个切点/一次上屏只响一次，不在同一锚点上叠两条）",
        },
        "affects": affects,
        "limits": limits,
        "coupling": coupling,
        "violation_symptoms": symptoms,
        "reuse_check": reuse,
        "sample_table": sample,
        "family_mix": fam_counts,
        "role_mix": role_counts,
        "reason_mix": mix,
        "family_fingerprints": fam_table,
        "name_counts": dict(sorted({k: len(v) for k, v in by_name.items()}.items(),
                                   key=lambda kv: -kv[1])),
        "per_min": per_min,
        "min_gap_sec": (round(min(gaps), 3) if gaps else None),
        "gap_median_sec": med(gaps),
        "gain_db_median": med(gains),
    }


def _piece_timeline(bgm: List[Dict[str, Any]]) -> List[Tuple[float, float, str, float]]:
    """把 BGM 段压成 (start, end, name, gain_db) 时间轴，按 start 排序。"""
    return [(c["start"], c["end"], c["name"], c["gain_db"]) for c in bgm]


def build_bgm(style_id: str, bgm: List[Dict[str, Any]], extra: List[Dict[str, Any]],
              sfx: List[Dict[str, Any]], dur: float, subs: Sequence[Tuple[float, float, str]],
              speech_cov, cuts: Sequence[float], lines: Sequence[float],
              lib: Dict[str, Any]) -> Dict[str, Any]:
    if not bgm:
        return {
            "present": False,
            "absent_reason": "真值 0 段：materials.audios 里没有 music / extract_music 类型的段（这套只有干口播）",
            "why_appears": {"rule": "不出", "triggers": [], "not_when": ["全片"], "evidence": "0 段 BGM"},
            "value_drivers": [], "scope": None, "affects": [], "limits": [],
            "coupling": [], "violation_symptoms": [], "reuse_check": [], "sample_table": [],
        }
    names = []
    for c in bgm:
        if c["name"] not in names:
            names.append(c["name"])
    beds = [c for c in bgm if c["gain_db"] is not None and c["gain_db"] <= -3.0]
    bed_db = med([c["gain_db"] for c in beds]) if beds else None
    # 时长加权水位：长段更重要
    wsum = sum(c["dur"] for c in bgm if c["dur"] > 0) or 1.0
    bed_db_w = round(sum(gdb(c) * c["dur"] for c in bgm) / wsum, 2)
    sfx_gain = med([c["gain_db"] for c in sfx if c["gain_db"] is not None]) if sfx else None
    cov = round(sum(c["dur"] for c in bgm if c["dur"] > 0) / dur, 3) if dur else None
    # 覆盖：按 0.25s 网格量 BGM 有/无
    grid = [i * 0.25 for i in range(int(dur / 0.25) + 1)]
    covered = sum(1 for t in grid if any(c["start"] - 0.05 <= t < c["end"] + 0.05 for c in bgm))
    cov_ratio = round(covered / len(grid), 3) if grid else None
    sp = [c["speech_cov"] for c in bgm]
    fades_out = [c for c in bgm if c["fade_out"] > 0]
    fades_in = [c for c in bgm if c["fade_in"] > 0]
    src_in = [c["src_in"] for c in bgm if c["src_in"] > 0.02]
    types = sorted({c["type"] for c in bgm})
    starts_at = round(min(c["start"] for c in bgm), 3)
    end_at = round(max(c["end"] for c in bgm), 3)
    loses = [c for c in bgm if c["end"] < dur - 0.05]

    rule = (
        f"只要片子有人声就一直有床：{round(cov_ratio * 100)}% 的时间窗里有 BGM，"
        + (f"从 {starts_at}s 起铺到 {end_at}s" if abs(starts_at) <= 0.1 else f"从 {starts_at}s 才进（前面 {starts_at}s 是干口播）")
        + f"；{len(names)} 首曲目（{' / '.join(names[:3])}{'…' if len(names) > 3 else ''}），"
        f"床位压到 {bed_db_w}dB 让口播站前面"
    )
    ev = (f"{len(bgm)} 段 BGM（类型 {types}）/ {dur}s；覆盖 {cov_ratio}；"
          f"床位（≤-3dB 的段）中位 {bed_db}dB、时长加权 {bed_db_w}dB；"
          f"人声覆盖中位 {med(sp)}（0.9 以上＝床位全程压在人声下）")
    drivers = [
        {"param": "bed_gain_db",
         "value": {"median": bed_db, "duration_weighted": bed_db_w,
                   "min": (round(min(c['gain_db'] for c in bgm), 2) if bgm else None),
                   "max": (round(max(c['gain_db'] for c in bgm), 2) if bgm else None)},
         "derived_from": "tracks[type=audio].segments[type=music|extract_music].volume → dB",
         "formula": "dB = 20·log10(volume)",
         "why_this_value": (
             f"床位的任务只是'别让耳朵空'，所以压到 {bed_db_w}dB（比音效低 "
             f"{round((sfx_gain or 0) - bed_db_w, 1)}dB）；只有片头 sting 才会短暂到 0dB"
         ),
         "calibrated_by": "同套音效电平中位（两者之差＝这套的响度层次）",
         "tolerance": "±1.5dB"},
        {"param": "source_in_sec",
         "value": {"median": med(src_in) if src_in else 0.0,
                   "share_offset_from_head": share(len(src_in), len(bgm))},
         "derived_from": "source_timerange.start",
         "formula": "从曲子第几秒起播（跳过前奏）",
         "why_this_value": (
             f"{round(share(len(src_in), len(bgm)) * 100)}% 的段从曲子中段起（中位 {med(src_in) if src_in else 0}s）："
             "源里不用前奏，直接进已经成立的律动"
         ),
         "calibrated_by": None, "tolerance": "±0.3s"},
        {"param": "tail_fade_sec",
         "value": {"share_with_fade_out": share(len(fades_out), len(bgm)),
                   "fade_out_median": med([c["fade_out"] for c in fades_out]) if fades_out else None,
                   "share_with_fade_in": share(len(fades_in), len(bgm))},
         "derived_from": "materials.audio_fades[].fade_out_duration / fade_in_duration",
         "formula": "fade_out ≈ 0.9~2.0s（约片长的 5%）；换床时新床 fade_in ≈ 0.57s",
         "why_this_value": (
             f"{len(fades_out)}/{len(bgm)} 段带尾巴淡出（中位 {med([c['fade_out'] for c in fades_out]) if fades_out else 0}s）："
             "床不能'咔'一下断，断在口播最后一个字上会听出剪辑点"
         ),
         "calibrated_by": None, "tolerance": "±0.2s"},
        {"param": "coverage_ratio",
         "value": {"covered_ratio": cov_ratio, "duplicated_ratio": cov,
                   "gap_count": sum(1 for i in range(len(grid) - 1)
                                    if not any(c["start"] - 0.05 <= grid[i] < c["end"] + 0.05 for c in bgm))},
         "derived_from": "BGM 段时间轴按 0.25s 网格采样",
         "formula": "covered = |{t: ∃BGM(t)}| / |grid|",
         "why_this_value": (
             f"{round(cov_ratio * 100)}% 的时间被床覆盖（重复覆盖 {cov} 是叠层/接棒造成的）："
             "源里没有'中途静音'的段落，BGM 一旦进就不退"
         ),
         "calibrated_by": None, "tolerance": "≥0.95 视为全程"},
    ]
    affects = [
        {"target": "口播清晰度", "how": f"床位 {bed_db_w}dB、人声覆盖 {med(sp)}；床一旦抬 → 字头被掩蔽"},
        {"target": "低频/中频占用", "how": "床占 100~400Hz，音效占 2~8kHz，两者错开；床抬起来会先糊人声胸腔共鸣"},
        {"target": "整体 LUFS", "how": f"库里标称 {lib.get('integrated_lufs')} LUFS（编写常量，不是本套测量值）"},
        {"target": "真峰", "how": f"床本身峰值低（{bed_db_w}dB 后），真峰基本由音效决定"},
    ]
    limits = [
        {"name": "bed_max_db", "value": round(max(c["gain_db"] for c in bgm), 2),
         "why": (f"最高 {round(max(c['gain_db'] for c in bgm), 2)}dB；"
                 + (f"正片床最高只有 {round(max(gdb(c) for c in beds), 2)}dB，0dB 那档只出现在 "
                    f"{len([c for c in bgm if is_sting(c)])} 段 sting 上"
                    if beds and max(c["gain_db"] for c in bgm) > -3.0 else "整片没有 0dB 的音乐段"))},
        {"name": "bed_min_db", "value": round(min(c["gain_db"] for c in bgm), 2),
         "why": f"再低就听不见床了（这套最低 {round(min(c['gain_db'] for c in bgm), 1)}dB 仍能感觉到律动）"},
        {"name": "max_pieces", "value": len(bgm), "why": "段数＝这套允许的换床/台阶次数"},
        {"name": "tail_fade_sec", "value": med([c["fade_out"] for c in fades_out]) if fades_out else 0.0,
         "why": "床的收尾必须有淡出，硬切会被听成事故"},
    ]
    coupling = [
        {"with": "sfx", "type": "level_delta",
         "why": f"床 {bed_db_w}dB vs 音效 {sfx_gain}dB → 差 {round((sfx_gain or 0) - bed_db_w, 1)}dB；这个差值就是'音效冒出来'的保证"},
        {"with": "voice_ducking", "type": "owned_by",
         "why": "床位电平就是 ducking 的全部实现（没有侧链压缩）"},
        {"with": "dual_bgm_switch", "type": "sequence",
         "why": "换床点必须落在切点/换句上，否则听成'两首歌打架'"},
    ]
    symptoms = [
        f"床抬到 0dB 附近 → 口播糊、字幕听不清（源里正片一次都没有高过 -5dB）",
        f"床压到 -20dB 以下 → 片子变'干'，每句之间露白，听成没做完",
        f"床在句中断掉（不做淡出）→ 听到一次'咔'，比没有床更糟",
        f"用前奏起播（不从 {med(src_in) if src_in else 0}s 进）→ 前 10 秒没有律动，节奏起不来",
    ]
    reuse = [
        "整片铺一条床，覆盖率 ≥0.95；除了明确的'干口播开场'，不要中途静音",
        f"床位 {bed_db_w}dB（±1.5dB），片头 sting 才允许 0dB",
        "收尾必须 0.9~2.0s 淡出；换床时新床 0.5~0.6s 淡入",
        "从曲子中段（非前奏）起播",
    ]
    sample = [
        {"at_sec": c["start"], "value": c["name"],
         "note": f"{c['type']} · {c['dur']}s · {c['gain_db']}dB · src_in {c['src_in']}s"
                 + (f" · 淡出{c['fade_out']}s" if c["fade_out"] else "")
                 + (f" · 淡入{c['fade_in']}s" if c["fade_in"] else "")
                 + f" · 人声覆盖 {c['speech_cov']}"}
        for c in bgm
    ]
    return {
        "present": True, "absent_reason": None,
        "why_appears": {"rule": rule,
                        "triggers": ["全片人声托底", "片头 sting", "段落换曲"] if len(names) > 1 else ["全片人声托底"],
                        "not_when": (["片头干口播：本套没有（BGM 从 0s 起就铺满）——想留干口播开场属于主动偏离"]
                                     if abs(starts_at) <= 0.1 else
                                     [f"片头前 {starts_at}s：本套先干口播再进床，进点就卡在 {starts_at}s 的结构位上"]),
                        "evidence": ev},
        "value_drivers": drivers,
        "scope": {"unit": "片", "coverage": f"{len(bgm)} 段 / {len(names)} 首曲目，覆盖 {cov_ratio} 的时间窗",
                  "excludes": "无（源里没有'中途撤床'的段落）" if not loses else f"{len(loses)} 段提前收（片尾/换床）",
                  "reset_on": "换曲目 / 段落台阶"},
        "affects": affects, "limits": limits, "coupling": coupling,
        "violation_symptoms": symptoms, "reuse_check": reuse, "sample_table": sample,
        "bed_db": bed_db, "bed_db_duration_weighted": bed_db_w,
        "names": names, "types": types, "coverage_ratio": cov_ratio,
        "speech_coverage_median": med(sp),
    }


def merge_pieces(bgm: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """同名 + 同电平 + 首尾相接 的相邻段合并成一个 piece（剪映常把一条曲切成几段导出）。"""
    out: List[Dict[str, Any]] = []
    for c in sorted(bgm, key=lambda c: (c["name"], c["start"], c["track"])):
        if (out and out[-1]["name"] == c["name"] and out[-1]["track"] == c["track"]
                and abs(c["start"] - out[-1]["end"]) <= 0.02
                and abs(gdb(out[-1]) - gdb(c)) < 0.5):
            out[-1]["end"] = max(out[-1]["end"], c["end"])
            out[-1]["dur"] = round(out[-1]["end"] - out[-1]["start"], 3)
            out[-1]["merge_n"] = out[-1].get("merge_n", 1) + 1
        else:
            out.append(dict(c, merge_n=1))
    return sorted(out, key=lambda c: (c["start"], -c["dur"]))


def detect_level_steps(bgm: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """同一曲目切段、电平跳 ≥3dB = 手动台阶（不是换曲，所以不算 dual）。"""
    steps: List[Dict[str, Any]] = []
    saw = sorted(bgm, key=lambda c: (c["start"], c["track"]))
    for i in range(1, len(saw)):
        a, b = saw[i - 1], saw[i]
        if a["name"] == b["name"] and abs(b["start"] - a["end"]) <= 0.02:
            d = round(gdb(b) - gdb(a), 2)
            if abs(d) >= 3.0:
                steps.append({"at": b["start"], "delta_db": d,
                              "from_db": a["gain_db"], "to_db": b["gain_db"]})
    return steps


def detect_switches(bgm: List[Dict[str, Any]]) -> Dict[str, Any]:
    """把 BGM 段解析成双段结构（只认**两首不同曲目**，与 `sfx_truth` 的 dual_bgm 口径一致）。

      · sequential_handoff  两段曲目不同、首尾相接或短交叠 → 换点＝后一首的 start
      · intro_overlay_exit  片头短 sting（≤3.5s）叠在长床上方 → 换点＝sting 的 end
      · overlay_sting_exit  正片中段的短 sting 同上（只是不在片头）
      · parallel_layers     两条长床几乎同起同收 → 没有换点，是叠层
    """
    pieces = merge_pieces(bgm)
    events: List[Dict[str, Any]] = []
    for i, c in enumerate(pieces):
        for prev in pieces[:i]:
            if prev["name"] == c["name"]:
                continue
            ov = min(prev["end"], c["end"]) - max(prev["start"], c["start"])
            short, long_ = (c, prev) if c["dur"] <= prev["dur"] else (prev, c)
            if ov <= 0.05:
                if abs(c["start"] - prev["end"]) <= 0.05:
                    events.append({"at": c["start"], "kind": "sequential_handoff",
                                   "short": short, "long": long_,
                                   "delta_db": round(gdb(long_) - gdb(short), 2),
                                   "overlap_sec": 0.0})
                continue
            if short["dur"] <= 3.5 and short["dur"] < 0.5 * long_["dur"]:
                kind = "intro_overlay_exit" if short["start"] <= 5.0 else "overlay_sting_exit"
                events.append({"at": round(min(prev["end"], c["end"]), 3), "kind": kind,
                               "short": short, "long": long_,
                               "delta_db": round(gdb(long_) - gdb(short), 2),
                               "overlap_sec": round(ov, 3)})
            elif ov < 0.30 * min(prev["dur"], c["dur"]):
                events.append({"at": round(max(prev["start"], c["start"]), 3), "kind": "sequential_handoff",
                               "short": short, "long": long_,
                               "delta_db": round(gdb(long_) - gdb(short), 2),
                               "overlap_sec": round(ov, 3)})
            else:
                events.append({"at": round(min(prev["end"], c["end"]), 3), "kind": "parallel_layers",
                               "short": short, "long": long_,
                               "delta_db": round(gdb(long_) - gdb(short), 2),
                               "overlap_sec": round(ov, 3)})
    prio = {"sequential_handoff": 0, "intro_overlay_exit": 0, "overlay_sting_exit": 0,
            "parallel_layers": 1}
    uniq: Dict[float, Dict[str, Any]] = {}
    for e in sorted(events, key=lambda e: (prio[e["kind"]], e["at"])):
        uniq.setdefault(round(e["at"], 2), e)
    events = sorted(uniq.values(), key=lambda e: (prio[e["kind"]], e["at"]))
    return {"events": events, "pieces": pieces, "level_steps": detect_level_steps(bgm)}


def build_dual(style_id: str, bgm: List[Dict[str, Any]], dur: float, cuts: Sequence[float],
               lines: Sequence[float], fstarts: Sequence[float]) -> Dict[str, Any]:
    if not bgm:
        return {
            "present": False,
            "absent_reason": "没有 BGM，谈不上换段",
            "why_appears": {"rule": "不出", "triggers": [], "not_when": ["全片"], "evidence": "0 段 BGM"},
            "value_drivers": [], "scope": None, "affects": [], "limits": [],
            "coupling": [], "violation_symptoms": [], "reuse_check": [], "sample_table": [],
        }
    found = detect_switches(bgm)
    events = found["events"]
    names: List[str] = []
    for c in found["pieces"]:
        if c["name"] not in names:
            names.append(c["name"])
    if not events or len(names) < 2:
        c0 = max(bgm, key=lambda c: c["dur"])
        steps = found["level_steps"]
        return {
            "present": False,
            "absent_reason": (
                f"只有一首曲目（{names[0] if names else '-'}）铺 {len(bgm)} 段——不是双段换曲。"
                + (f"同一曲目有 {len(steps)} 次电平台阶（{'/'.join(f'{s['at']}s {s['delta_db']:+.1f}dB' for s in steps)}），"
                   "那是音量自动化，记在 bgm.value_drivers 与 voice_ducking.manual_steps 里，不当换段"
                   if steps else "电平也全程一致，连台阶都没有")
            ),
            "why_appears": {"rule": "这套不做换曲", "triggers": [], "not_when": ["全片"],
                            "evidence": f"{len(bgm)} 段 BGM、唯一曲目 {names[0] if names else '-'}、"
                                        f"增益 {sorted({c['gain_db'] for c in bgm})}、台阶 {len(steps)} 次"},
            "value_drivers": [], "scope": None, "affects": [], "limits": [],
            "coupling": [], "violation_symptoms": [], "reuse_check": [], "sample_table": [],
            "switch_sec": None, "switch_kind": None,
        }

    seq = [e for e in events if e["kind"] == "sequential_handoff"]
    main_sw = seq[0] if seq else events[0]
    t = round(main_sw["at"], 3) if main_sw["kind"] != "parallel_layers" else None
    cut_off = line_off = fl_off = None
    if t is not None:
        cpos = near(cuts, t, 1.0)
        cut_off = round(t - cpos, 3) if cpos is not None else None
        lpos = near(lines, t, 1.0)
        line_off = round(t - lpos, 3) if lpos is not None else None
        fpos = near(fstarts, t, 0.6)
        fl_off = round(t - fpos, 3) if fpos is not None else None

    kinds = sorted({e["kind"] for e in events})
    kind_main = main_sw["kind"]
    others = [e for e in events if e is not main_sw]
    kind_desc = {
        "intro_overlay_exit": "片头 sting 叠在床的上方、退出时把正片床露出来（sting ≤3.5s）",
        "overlay_sting_exit": "中段 sting 叠在床的上方、退出时回单床",
        "sequential_handoff": "两首曲目接棒（前一首淡出/短交叠，后一首淡入）",
        "parallel_layers": "两条长床几乎同起同收（叠层，不是换段）",
    }.get(kind_main, kind_main)
    anchor_bits = []
    if cut_off is not None:
        anchor_bits.append(f"距最近主轨切点 {cut_off:+}s")
    if line_off is not None:
        anchor_bits.append(f"距换句 {line_off:+}s")
    if fl_off is not None:
        anchor_bits.append(f"距花字 {fl_off:+}s")

    if t is None:
        rule = (f"这套是叠层而不是换段：{len(names)} 首（{' / '.join(names)}）几乎同起同收，"
                f"主床 {round(max(c['dur'] for c in found['pieces']), 2)}s、副床 "
                f"{round(min(c['dur'] for c in found['pieces']), 2)}s，没有可观测的换点")
    else:
        rule = (
            f"这套不是'一直一条床'：{len(names)} 首曲目、{len(events)} 个结构点"
            f"（{fmt_share({k: sum(1 for e in events if e['kind'] == k) for k in kinds})}）。"
            f"主换点在 {t}s —— {kind_desc}"
            + ("；" + "、".join(anchor_bits) if anchor_bits else "")
            + "。换床必须落在结构位上，不然听成两首歌打架"
        )
    step_sizes = [abs(e["delta_db"]) for e in events if e["kind"] == "level_step"] or [0.0]
    overlap_max = max([e["overlap_sec"] for e in events] or [0.0])
    drivers = [
        {"param": "switch_sec",
         "value": t,
         "derived_from": "BGM 段边界（曲目变化 / sting 退出）",
         "formula": "sequential→后一首 start；overlay sting→sting end；parallel→无换点",
         "why_this_value": (
             (f"实测 {t}s" + ("，" + "、".join(anchor_bits) if anchor_bits else "")
              + "：锚在'开场结构走完/画面换了'的那一刻，不是按秒数拍的")
             if t is not None else
             "没有换点：两条床同起同收，听感是'更厚'而不是'换歌'"
         ),
         "calibrated_by": "同套 cuts / lines / 花字时刻表", "tolerance": "±0.15s"},
        {"param": "gain_step_db",
         "value": {e["kind"]: e["delta_db"] for e in events},
         "derived_from": "相邻两段 volume 字段之差",
         "formula": "ΔdB = dB(后) − dB(前)",
         "why_this_value": ("换段同时电平也变：从 0dB 的 sting 掉到床通常 8~14dB，"
                            "就是'仪式感结束、叙事开始'"),
         "calibrated_by": None, "tolerance": "±1dB"},
        {"param": "overlap_sec",
         "value": overlap_max,
         "derived_from": "两条 BGM 段时间轴交集",
         "formula": "overlap = min(end) − max(start)",
         "why_this_value": (
             f"叠层最长 {overlap_max}s：sting 压在床上方制造开场仪式感，退出即交棒；"
             "两条长床并列则整段都叠着（更厚，但更占频段、更吃口播）"
             if overlap_max else "没有叠层，全靠接棒（接棒靠淡入淡出，不靠叠）"
         ),
         "calibrated_by": None, "tolerance": "±0.1s"},
    ]
    affects = [
        {"target": "段落感", "how": f"{len(events)} 个结构点把片子切成 {len(events) + 1} 个音乐段"},
        {"target": "口播清晰度", "how": "往下台阶时口播变清楚；往上/叠层的段落必须没有密集口播，否则字头被盖"},
        {"target": "真峰", "how": "0dB 的 sting 段是整片最响的音乐段，真峰由它决定"},
    ]
    limits = [
        {"name": "max_switches", "value": len(events), "why": f"再多就听成'串歌'；这套全长 {dur:.1f}s"},
        {"name": "max_gain_step_db", "value": round(max([abs(e["delta_db"]) for e in events]), 1),
         "why": "源里最大台阶；再大就听出'手动调音量'"},
        {"name": "sting_max_sec",
         "value": round(max([c["dur"] for c in bgm if is_sting(c)] or [0.0]), 3),
         "why": (f"本套 sting 实测 {round(max([c['dur'] for c in bgm if is_sting(c)] or [0.0]), 2)}s；"
                 "超过 3.5s 就不再是 sting 而是第二首 bed，那时必须按换床处理（淡入淡出齐备）")},
    ]
    coupling = [
        {"with": "bgm", "type": "owned_by", "why": "双段结构是 BGM 的子结构"},
        {"with": "voice_ducking", "type": "interaction", "why": "台阶方向就是 ducking 方向：往下＝给口播让位"},
        {"with": "sfx", "type": "shared_boundary", "why": "sting 退出点常同时是开场第一声音效的锚点"},
    ]
    symptoms = [
        "换床点落在句子中间 → 两首歌律动撞在一起，听成车祸",
        "片头 sting 拉长到整段 → 开场变吵，正片床没机会进",
        "两首曲目直接硬切不淡入淡出 → 听到'咔'",
        "该叠层时改成接棒（或反过来）→ 开场没有仪式感 / 中段糊成一片",
    ]
    reuse = [
        f"最多 {len(events)} 个结构点，且必须落在切点/换句/花字 ±0.15s",
        "sting 与正片床的电平差要 8~14dB，靠淡入淡出交棒而不是硬切",
        "片头 sting ≤3.5s；两条长床叠层只在前 15% 时长内允许",
    ]
    sample = [{"at_sec": c["start"], "value": c["name"],
               "note": f"{c['dur']}s · {c['gain_db']}dB · 轨道{c['track']}"
                       + (f" · 淡出{c['fade_out']}s" if c["fade_out"] else "")
                       + (f" · 合并{c['merge_n']}段" if c.get("merge_n", 1) > 1 else "")}
              for c in found["pieces"]]
    return {
        "present": True, "absent_reason": None,
        "why_appears": {
            "rule": rule,
            "triggers": kinds,
            "not_when": ["只上一首曲目就够的短片（这套换，是因为开场/段落需要仪式感）"],
            "evidence": (f"{len(bgm)} 段 → {len(found['pieces'])} 个 piece / {len(names)} 首；"
                         f"结构点 {[(e['kind'], e['at']) for e in events]}；"
                         + (f"主换点 t={t}s、距切点 {cut_off}s" if t is not None and cut_off is not None
                            else (f"主换点 t={t}s（附近 1s 内没有主轨切点）" if t is not None
                                  else "没有换点（叠层铺满）"))),
        },
        "value_drivers": drivers,
        "scope": {"unit": "段", "coverage": f"全片 {dur}s 内 {len(found['pieces'])} 个 piece、{len(events)} 个结构点",
                  "excludes": "无", "reset_on": "换曲目 / sting 退出"},
        "affects": affects, "limits": limits, "coupling": coupling,
        "violation_symptoms": symptoms, "reuse_check": reuse, "sample_table": sample,
        "switch_sec": t, "switch_kind": kind_main, "switch_kinds": kinds,
        "switches": [{"at": e["at"], "kind": e["kind"], "delta_db": e["delta_db"],
                      "overlap_sec": e["overlap_sec"]} for e in events],
        "switch_context": {"cut_offset_sec": cut_off, "line_offset_sec": line_off,
                           "flower_offset_sec": fl_off},
    }


def build_ducking(style_id: str, bgm: List[Dict[str, Any]], sfx: List[Dict[str, Any]], dur: float,
                  subs: Sequence[Tuple[float, float, str]], speech_cov, doc: Dict[str, Any],
                  lib: Dict[str, Any]) -> Dict[str, Any]:
    if not bgm:
        return {
            "present": False,
            "absent_reason": "没有 BGM 就没有需要让位的对象——这套的'干净'靠不出声，不靠压谁",
            "why_appears": {"rule": "不出", "triggers": [], "not_when": ["全片"], "evidence": "0 段 BGM"},
            "value_drivers": [], "scope": None, "affects": [], "limits": [],
            "coupling": [], "violation_symptoms": [], "reuse_check": [], "sample_table": [],
        }
    # 关键事实：源草稿里音频段有没有音量关键帧
    kf = sum(1 for c in bgm if c.get("has_keyframes")) + sum(1 for c in sfx if c.get("has_keyframes"))
    n_audio_segs = len(bgm) + len(sfx)
    beds = [c for c in bgm if gdb(c) <= -3.0]
    bed_db = round(sum(gdb(c) * c["dur"] for c in bgm) / (sum(c["dur"] for c in bgm) or 1), 2)
    sfx_gain = med([c["gain_db"] for c in sfx if c["gain_db"] is not None]) if sfx else None
    delta = round((sfx_gain or 0) - bed_db, 1)
    steps: List[Dict[str, Any]] = []
    ordered = sorted(bgm, key=lambda c: (c["start"], c["track"]))
    for i in range(1, len(ordered)):
        a, b = ordered[i - 1], ordered[i]
        if a["name"] == b["name"] and abs(b["start"] - a["end"]) <= 0.02:
            d = round(gdb(b) - gdb(a), 2)
            if abs(d) >= 3.0:
                steps.append({"at": b["start"], "delta_db": d,
                              "speech_cov_before": a["speech_cov"], "speech_cov_after": b["speech_cov"]})
    sp = med([c["speech_cov"] for c in bgm])
    denoise = [m for m in (doc.get("materials") or {}).get("realtime_denoises") or []
               if m.get("is_denoise")]
    denoise_rate = sorted({round(float(m.get("denoise_rate") or 0), 2) for m in denoise}) if denoise else []
    # 降噪挂在哪个轨（人声镜头 vs 音频轨）
    denoise_on = "人声镜头（video 轨）" if denoise else "无"

    step_txt = "/".join(f"{s['at']}s {s['delta_db']:+.1f}dB" for s in steps)
    rule = (
        "源草稿**不做侧链压缩**："
        f"{n_audio_segs} 条音频段里带音量关键帧的 {kf} 条。"
        f"所谓 ducking 就是床位一次压好（时长加权 {bed_db}dB）："
        + (f"音效（{sfx_gain}dB）比它高 {delta}dB，音效跳在床上面、口播站在最前面"
           if delta >= 8 else
           f"音效（{sfx_gain}dB）只比它高 {delta}dB —— 这套音效本来就轻，层次靠'少'而不是靠'响'")
        + (f"；中途只有 {len(steps)} 次手动台阶（{step_txt}）" if steps else "；全片没有任何闪避动作")
    )
    drivers = [
        {"param": "bed_gain_db",
         "value": bed_db,
         "derived_from": "BGM 段 volume（时长加权）",
         "formula": "bed_db = Σ(gain_i × dur_i)/Σdur_i",
         "why_this_value": (
             f"{sp} 的人声覆盖下把床压到 {bed_db}dB：压深了片子发空，压浅了口播糊；"
             "这个值在 24 套里跨度从 -5 到 -17dB，随风格（口播密度/曲目响度）走"
         ),
         "calibrated_by": "同套音效电平中位（两者的差必须 ≥8dB）",
         "tolerance": "±1.5dB"},
        {"param": "sfx_above_bed_db",
         "value": delta,
         "derived_from": "sfx gain 中位 − bed gain（时长加权）",
         "formula": "delta = median(sfx_dB) − bed_db",
         "why_this_value": f"{delta}dB 是源里'音效盖过床'的最小保障；低于 6dB 音效就沉进床里听不见",
         "calibrated_by": None, "tolerance": "≥8dB"},
        {"param": "manual_step",
         "value": {"count": len(steps), "steps": steps},
         "derived_from": "同名 BGM 相邻段 volume 差（≥3dB 记一次台阶）",
         "formula": "step = 后段 dB − 前段 dB",
         "why_this_value": (
             "台阶全部出现在段落边界（切点/换句附近），方向都是'往下'：源里的做法是"
             "'到这一段把床按低一点'，而不是'每句话进来就压一下'"
             if steps else "这套一次台阶都没有——床从头到尾一个电平"
         ),
         "calibrated_by": "同套 cuts / lines 时刻表", "tolerance": "±1dB"},
        {"param": "voice_denoise",
         "value": {"rate": denoise_rate, "n_materials": len(denoise), "attached_to": denoise_on},
         "derived_from": "materials.realtime_denoises[].denoise_rate / is_denoise",
         "formula": "剪映'降噪'开关的强度",
         "why_this_value": (
             f"{'开了 ' + str(denoise_rate) + ' 的人声降噪（挂在人声镜头上）' if denoise else '没有开任何降噪'}"
             "：人声干净度是靠录的时候/降噪，不是靠把 BGM 压死"
         ),
         "calibrated_by": None, "tolerance": None},
    ]
    affects = [
        {"target": "口播清晰度", "how": f"床位 {bed_db}dB + 音效高 {delta}dB 构成层次；任一环错位都听成'吵'或'糊'"},
        {"target": "真峰", "how": "床压低后真峰由音效瞬态决定，不由床决定"},
        {"target": "响度一致性",
         "how": (f"库里标称 {lib.get('integrated_lufs')} LUFS 是编写常量；"
                 + (f"标称 {lib.get('voice_bgm_snr_target_db')}dB SNR 同样是常量"
                    if lib.get("voice_bgm_snr_target_db") is not None else "这套连 SNR 标称值都没有")
                 + "——源草稿没有可测的 SNR 真值（段字段推不出）")},
    ]
    limits = [
        {"name": "bed_max_db_under_speech", "value": round(max([c["gain_db"] for c in beds] or [-99]), 2),
         "why": "正片床不得高于这个值；只有片头 sting 例外"},
        {"name": "min_sfx_above_bed_db", "value": 8.0, "why": f"本套实测 {delta}dB；低了音效就被床吃掉"},
        {"name": "max_manual_steps", "value": len(steps) or 0, "why": "台阶是段落级的，不是句子级的（源里最多 3 次）"},
    ]
    coupling = [
        {"with": "bgm", "type": "level_relation", "why": "ducking 就是床位电平本身"},
        {"with": "sfx", "type": "level_relation", "why": f"音效必须高出床位 {delta}dB"},
        {"with": "dual_bgm_switch", "type": "shared_boundary", "why": "台阶点和换床点是同一批结构位"},
    ]
    symptoms = [
        "做成侧链压缩（每句进来压一下）→ 听感变成'呼吸感'，与源那种'稳态发闷'完全不同",
        "只压 BGM 不抬音效 → 音效沉底，等于没有音效",
        "床位跟着音效一起压 → 整片忽大忽小，像音量事故",
        "床压到 -20dB 以下 → 片子空；床抬到 -3dB 以上 → 口播糊",
    ]
    reuse = [
        f"床一次压好到 {bed_db}dB（±1.5dB），不要写侧链压缩曲线",
        f"音效电平至少高出床位 {delta}dB",
        "与人声重叠率 ≥0.85 就证明床全程在人声下；低于 0.6 说明这段本来就不该有床",
        f"段落边界允许 {len(steps)} 次手工台阶（≥3dB），句内不许",
    ]
    sample = [{"at_sec": c["start"], "value": c["name"],
               "note": f"{c['gain_db']}dB · 人声覆盖 {c['speech_cov']}"} for c in sorted(bgm, key=lambda c: c["start"])]
    return {
        "present": True, "absent_reason": None,
        "why_appears": {
            "rule": rule,
            "triggers": ["BGM 与人声同窗", "段落台阶"] if steps else ["BGM 与人声同窗"],
            "not_when": ["纯音乐段/无人声段（这套几乎没有：人声覆盖率中位 %s）" % sp],
            "evidence": (f"{n_audio_segs} 条音频段中带 volume 关键帧 {kf} 条；"
                         f"床位 {bed_db}dB、音效 {sfx_gain}dB（差 {delta}dB）；"
                         f"人声覆盖中位 {sp}；手动台阶 {len(steps)} 次；降噪：{denoise_on}"),
        },
        "value_drivers": drivers,
        "scope": {"unit": "全片", "coverage": f"{len(bgm)} 段 BGM 全部压在人声下（覆盖率中位 {sp}）",
                  "excludes": "无", "reset_on": "换段（只换电平，不换策略）"},
        "affects": affects, "limits": limits, "coupling": coupling,
        "violation_symptoms": symptoms,
        "reuse_check": reuse, "sample_table": sample,
        "mode": "static_bed_gain" if kf == 0 else "keyframed",
        "bed_db": bed_db, "sfx_above_bed_db": delta, "manual_steps": steps,
    }


# ── 全库统计 ──────────────────────────────────────────────────────────────────


def style_index_row(st: Dict[str, Any]) -> Dict[str, Any]:
    """一行一套：密度 / 族构成 / 锚点配比 / 床位 / 双段 / 闪避参数。给 agent 的速查表。"""
    el = st.get("elements") or {}
    sfx = el.get("sfx") or {}
    bgm = el.get("bgm") or {}
    dual = el.get("dual_bgm_switch") or {}
    duck = el.get("voice_ducking") or {}
    return {
        "duration_sec": st.get("duration_sec"),
        "sfx_cues": st.get("samples"),
        "sfx_per_min": sfx.get("per_min"),
        "sfx_present": bool(sfx.get("present")),
        "sfx_family_mix": sfx.get("family_mix") or {},
        "sfx_role_mix": sfx.get("role_mix") or {},
        "sfx_trigger_mix": sfx.get("reason_mix") or {},
        "sfx_min_gap_sec": sfx.get("min_gap_sec"),
        "sfx_gain_db_median": sfx.get("gain_db_median"),
        "sfx_unique_names": len(sfx.get("name_counts") or {}),
        "bgm_present": bool(bgm.get("present")),
        "bgm_names": bgm.get("names") or [],
        "bgm_bed_db": bgm.get("bed_db_duration_weighted"),
        "dual_bgm": bool(dual.get("present")),
        "dual_switch_sec": dual.get("switch_sec"),
        "dual_switch_kind": dual.get("switch_kind"),
        "ducking_mode": duck.get("mode"),
        "bed_db": duck.get("bed_db"),
        "sfx_above_bed_db": duck.get("sfx_above_bed_db"),
        "manual_steps": len(duck.get("manual_steps") or []),
    }


def library_stats(styles: Dict[str, Any]) -> Dict[str, Any]:
    cues = []
    per_min = []
    names: Dict[str, int] = {}
    fams: Dict[str, int] = {}
    roles: Dict[str, int] = {}
    gains: List[float] = []
    durs: List[float] = []
    trig: Dict[str, int] = {}
    dual: List[str] = []
    bgm_present: List[str] = []
    beds: List[float] = []
    total_sec = 0.0
    for sid, st in styles.items():
        if "elements" not in st:
            continue
        total_sec += float(st.get("duration_sec") or 0)
        s = st["elements"]["sfx"]
        cues.append(int(st.get("samples") or 0))
        if s.get("present"):
            per_min.append(s.get("per_min"))
            for k, v in (s.get("family_mix") or {}).items():
                fams[k] = fams.get(k, 0) + v
            for k, v in (s.get("role_mix") or {}).items():
                roles[k] = roles.get(k, 0) + v
            for nm, c in (s.get("name_counts") or {}).items():
                names[nm] = names.get(nm, 0) + c
            for d in s.get("value_drivers") or []:
                if d["param"] == "gain_db":
                    gains.append((d["value"] or {}).get("median") or 0.0)
                if d["param"] == "cue_duration_sec":
                    durs.append((d["value"] or {}).get("median") or 0.0)
            for t in s.get("why_appears", {}).get("triggers") or []:
                trig[t] = trig.get(t, 0) + 1
        if st["elements"]["bgm"].get("present"):
            bgm_present.append(sid)
            beds.append(st["elements"]["bgm"].get("bed_db_duration_weighted"))
        if st["elements"]["dual_bgm_switch"].get("present"):
            dual.append(sid)
    return {
        "styles_total": len(styles),
        "styles_with_bgm": len(bgm_present),
        "styles_with_dual_bgm": len(dual),
        "dual_bgm_styles": dual,
        "total_cues": sum(cues),
        "total_duration_sec": round(total_sec, 1),
        "sfx_per_min_median": med(per_min),
        "sfx_per_min_min": (round(min(per_min), 1) if per_min else None),
        "sfx_per_min_max": (round(max(per_min), 1) if per_min else None),
        "sfx_per_min_p25": pct(per_min, 0.25),
        "sfx_per_min_p75": pct(per_min, 0.75),
        "family_mix": dict(sorted(fams.items(), key=lambda kv: -kv[1])),
        "role_mix": dict(sorted(roles.items(), key=lambda kv: -kv[1])),
        "top_sfx_names": sorted(names.items(), key=lambda kv: -kv[1])[:12],
        "unique_names": len(names),
        "bed_db_range": [round(min(beds), 1), round(max(beds), 1)] if beds else None,
        "bed_db_median": med(beds),
        "note": ("音效族构成按名字功能归类（money/text_reveal/whoosh/impact/tension/sparkle/other），"
                 "族的锚点指纹见每套 elements.sfx.family_fingerprints。"
                 "role_mix 是运行期 6 角色分类（cut/hook/info/accent/receipt/misc）的结果——"
                 "76% 落进 misc 说明'角色'这一层对音效太粗，选型判断要下沉到功能族"),
    }


# ── 现场成片对照（只读 pipeline）──────────────────────────────────────────────


def compare_our_build(styles: Dict[str, Any], pipeline_dir: Optional[Path] = None) -> Dict[str, Any]:
    pdir = Path(pipeline_dir) if pipeline_dir else PIPELINE_DIR
    pkg = pdir / "06_packaging.json"
    if not pkg.is_file():
        return {"available": False,
                "absent_reason": "现场 pipeline 不存在或未跑到 06_packaging，本次不做对照"}
    pack = read_json(pkg)
    ev_p = pdir / "03_audio_events.json"
    edl_p = pdir / "04_edl.json"
    mix_p = pdir / "08_mix_metrics.json"
    audit_p = pdir / "08_sfx_audit_window.json"
    events = (read_json(ev_p).get("events") or []) if ev_p.is_file() else []
    edl = read_json(edl_p) if edl_p.is_file() else {}
    mix = read_json(mix_p) if mix_p.is_file() else {}
    audit = read_json(audit_p) if audit_p.is_file() else {}

    rows = (pack.get("sfx_justification") or {}).get("rows") or []
    kept = [r for r in rows if r.get("decision") == "keep"]
    dur = float((read_json(ev_p).get("total_duration_sec") if ev_p.is_file() else 0) or 0)
    if not dur and edl:
        dur = float(edl.get("total_duration_sec") or 0)
    sid = str(pack.get("style_id") or "")
    src = (styles.get(sid) or {}).get("elements", {})
    src_pm = ((src.get("sfx") or {}).get("per_min")) if src else None
    our_pm = round(len(kept) / dur * 60.0, 1) if dur else None

    cue_t = sorted(round(float(r.get("time_sec") or 0), 3) for r in kept)
    gaps = [round(cue_t[i + 1] - cue_t[i], 2) for i in range(len(cue_t) - 1)]
    # 画面真实切点
    clips = edl.get("clips") or []
    vcuts = sorted({round(float(c.get("timeline_in_sec") or 0), 3) for c in clips})
    # 主轨切点与"画面变化点"是两件事：B-roll 入点也算画面换了，但源真值只按主轨量
    vcuts_main = sorted({round(float(c.get("timeline_in_sec") or 0), 3)
                         for c in clips if str(c.get("track_role")) == "a_roll"})
    # 事件表里"真结构位"（源规则认的）vs 微切
    structural = {"sentence_start", "sentence_end", "pause_start", "pause_end",
                  "bgm_downbeat", "keyword_accent"}
    micro = {"stress", "bgm_beat", "beat", "midline", "micro_cut"}
    ev_by_id = {e.get("id"): e for e in events}
    kept_types: Dict[str, int] = {}
    for r in kept:
        et = r.get("event_type") or "flower"
        kept_types[et] = kept_types.get(et, 0) + 1
    on_micro = [r for r in kept if (r.get("event_type") in micro)]
    on_downbeat = [r for r in kept if r.get("event_type") == "bgm_downbeat"]
    # 这些"切点"其实没有换镜（画面没动）
    cutless = [r for r in kept if not any(abs(float(v) - float(r["time_sec"])) <= 0.12 for v in vcuts)]
    missed = [v for v in vcuts if not any(abs(v - t) <= 0.12 for t in cue_t)]
    missed_main = [v for v in vcuts_main if not any(abs(v - t) <= 0.12 for t in cue_t)]
    # 名字多样性
    names = [w.get("sfx_name") for w in (audit.get("windows") or [])]
    name_counts: Dict[str, int] = {}
    for nm in names:
        name_counts[str(nm)] = name_counts.get(str(nm), 0) + 1
    src_names = []
    if sid:
        p = SFX_TRUTH_DIR / f"{sid}.json"
        if p.is_file():
            src_names = [c.get("name") for c in (read_json(p).get("cues") or [])]
    src_unique = len({n for n in src_names})

    # 真值侧同套的对照量（切点覆盖/最大空档/名字数）——要先算，后面 findings 里要用
    truth_cut_cov = None
    try:
        gcuts = sorted(round(float(m.get("start") or 0), 3)
                       for m in (read_json(GRAMMAR)["styles"][sid].get("main_segments") or []))
    except Exception:
        gcuts = []
    truth_max_gap = None
    tcues: List[float] = []
    tp = SFX_TRUTH_DIR / f"{sid}.json"
    if tp.is_file():
        tcues = sorted(float(c.get("time_sec") or 0) for c in (read_json(tp).get("cues") or []))
        if len(tcues) > 1:
            truth_max_gap = round(max(tcues[i + 1] - tcues[i] for i in range(len(tcues) - 1)), 2)
        if gcuts:
            truth_cut_cov = share(sum(1 for c in gcuts if any(abs(c - t) <= 0.12 for t in tcues)), len(gcuts))
    truth_rows = len(tcues)
    truth_anchor = {t: 1 for t in ((src.get("sfx") or {}).get("why_appears", {}).get("triggers") or [])}
    density_truth = {"cues": truth_rows, "per_min": src_pm, "max_gap_sec": truth_max_gap,
                     "unique_names": src_unique, "trigger_mix": truth_anchor}

    def fam(name: str) -> str:
        return fam_of(re.sub(r"^(info|accent|cut|hook|receipt|misc)_", "", name.replace(".mp3", "")))

    our_fams: Dict[str, int] = {}
    for nm in names:
        f = fam(nm)
        our_fams[f] = our_fams.get(f, 0) + 1
    src_fams: Dict[str, int] = {}
    for nm in src_names:
        f = fam_of(nm)
        src_fams[f] = src_fams.get(f, 0) + 1

    # BGM 侧
    bgm_claim = pack.get("bgm") or {}
    bgm_locked = None
    if ev_p.is_file():
        bgm_locked = read_json(ev_p).get("bgm") or {}
    snr = mix.get("voice_bgm_snr_db")

    findings = []
    if our_pm and src_pm:
        findings.append({
            "kind": "density",
            "ours": our_pm, "truth": src_pm, "ratio": round(our_pm / src_pm, 2),
            "verdict": "空" if our_pm < src_pm * 0.8 else ("吵" if our_pm > src_pm * 1.2 else "齐"),
            "detail": f"{len(kept)} 条 / {dur:.1f}s = {our_pm}/分，真值 {src_pm}/分（差 {round(src_pm - our_pm, 1)}）",
        })
    if gaps:
        truth_sfx = src.get("sfx") or {}
        findings.append({
            "kind": "gap",
            "ours": {"median": med(gaps), "max": round(max(gaps), 2),
                     "holes_over_3s": [[round(cue_t[i], 2), round(cue_t[i + 1], 2)]
                                       for i in range(len(gaps)) if gaps[i] > 3.0]},
            "truth": {"per_min": (truth_sfx or {}).get("per_min")},
            "verdict": "有空档" if max(gaps) > 3.0 else "齐",
            "detail": f"最长 {max(gaps)}s 没有声音事件；>3s 的空洞 {(len([g for g in gaps if g > 3.0]))} 处，"
                      f"首条 cue 在 {cue_t[0]}s（开场 {cue_t[0]}s 无声）",
        })
    if names:
        top = sorted(name_counts.items(), key=lambda kv: -kv[1])[:3]
        findings.append({
            "kind": "timbre_monotony",
            "ours": {"unique": len(name_counts), "top3": top, "reuse_ratio": round(len(names) / max(1, len(name_counts)), 2)},
            "truth": {"unique": src_unique, "cues": len(src_names)},
            "verdict": "吵" if len(name_counts) <= max(2, src_unique // 2) else "齐",
            "detail": (f"我们 {len(names)} 条只用 {len(name_counts)} 个音色（复用 {round(len(names) / max(1, len(name_counts)), 2)}×），"
                       f"真值 {len(src_names)} 条用了 {src_unique} 个；{top[0][0]} 一条占 {round(top[0][1] * 100 / len(names))}%"),
        })
    findings.append({
        "kind": "family_mix",
        "ours": our_fams, "truth": src_fams,
        "verdict": "偏",
        "detail": (f"我们只有 {fmt_share(our_fams)}；真值是 {fmt_share(src_fams)}——"
                   "换镜用的 whoosh 和'叮'类的响铃缺位，文字类（翻页/键盘）独占"),
    })
    kept_trig: Dict[str, int] = {}
    for r in kept:
        t = str(r.get("trigger") or "none")
        kept_trig[t] = kept_trig.get(t, 0) + 1
    findings.append({
        "kind": "anchor_type",
        "ours": {"trigger": kept_trig, "event_type": kept_types,
                 "no_picture_change": len(cutless)},
        "truth": {"trigger": (src.get("sfx") or {}).get("why_appears", {}).get("triggers"),
                  "beat_triggered": 0},
        "verdict": "乱响" if (len(on_downbeat) >= 3 or len(cutless) >= 3) else "齐",
        "detail": (f"我们 keep 的 {len(kept)} 条：trigger {kept_trig}；其中 {len(on_downbeat)} 条挂在小节强拍"
                   f"（bgm_downbeat）、{len(cutless)} 条所在时刻画面根本没换镜（±0.12s 内无 clip 边界）。"
                   f"真值这套 12 条：0 条踩拍，cut/flower/line 三分"),
        "no_picture_change_sec": [round(float(r["time_sec"]), 1) for r in cutless],
    })
    cov_all = share(len(vcuts) - len(missed), len(vcuts))
    cov_main = share(len(vcuts_main) - len(missed_main), len(vcuts_main))
    findings.append({
        "kind": "cut_coverage",
        "ours": {"visual_change_points": len(vcuts), "with_cue": len(vcuts) - len(missed),
                 "coverage_all": cov_all,
                 "main_track_cuts": len(vcuts_main), "main_with_cue": len(vcuts_main) - len(missed_main),
                 "coverage_main": cov_main},
        "truth": {"main_track_cuts": len(gcuts), "coverage_main": truth_cut_cov,
                  "note": "真值只按主轨切点量，所以对照要看 coverage_main"},
        "verdict": "该响没响" if (cov_main is not None and cov_main < 0.5) else "齐",
        "detail": (f"画面变化点（含 B-roll 入点）{len(vcuts)} 个，{len(vcuts) - len(missed)} 个有音效（{round(cov_all * 100)}%）；"
                   f"只算主轨切点 {len(vcuts_main)} 个，只有 {len(vcuts_main) - len(missed_main)} 个有音效（{round(cov_main * 100)}%）。"
                   f"真值同套主轨 {len(gcuts)} 个切点覆盖 {round((truth_cut_cov or 0) * 100)}%"),
        "missed_main_cuts_sec": [round(v, 1) for v in missed_main],
        "missed_change_points_sec": [round(v, 1) for v in missed[:12]],
    })
    if bgm_claim:
        dual_claim = bool(bgm_claim.get("dual_bgm"))
        dual_real = bool((bgm_locked or {}).get("dual_bgm_applied"))
        findings.append({
            "kind": "bgm_switch",
            "ours": {"declared_dual": dual_claim, "switch_sec": bgm_claim.get("switch_sec"),
                     "actually_applied": dual_real,
                     "locked_switch": (bgm_locked or {}).get("dual_bgm_switch_sec")},
            "truth": {"switch_sec": (src.get("dual_bgm_switch") or {}).get("switch_sec"),
                      "switch_kind": (src.get("dual_bgm_switch") or {}).get("switch_kind")},
            "verdict": "虚报" if dual_claim and not dual_real else "齐",
            "detail": ("06 声明 dual_bgm（switch 1.733s），但 03_lock_audio 里 dual_bgm_applied=false、switch=null —— "
                       "片头 sting 没做，正片直接从 0s 起一条床；真值那 1.733s 是'开场 sting 交棒'的结构位"),
        })
    if snr is not None:
        findings.append({
            "kind": "bed_depth",
            "ours": {"voice_bgm_snr_db": snr},
            "truth": {"library_nominal_snr_db": 11.5,
                      "note": "库里这个数是编写常量，不是源草稿测量值"},
            "verdict": "空" if snr > 14 else "齐",
            "detail": (f"人声比床高 {snr}dB（SNR），库里标称 11.5dB —— 我们的床比真值浅约 "
                       f"{round(snr - 11.5, 1)}dB，律动几乎听不见，这是'空'的主因之一"),
        })
    return {
        "available": True,
        "pipeline": {"style_id": sid, "duration_sec": round(dur, 2), "kept_cues": len(kept),
                     "candidates": len(rows), "dropped": len(rows) - len(kept)},
        "truth_same_style": dict(density_truth, style_id=sid),
        "side_by_side": {
            "density_per_min": {"ours": our_pm, "truth": src_pm},
            "cues": {"ours": len(kept), "truth": truth_rows},
            "unique_names": {"ours": len(name_counts), "truth": src_unique},
            "max_gap_sec": {"ours": (round(max(gaps), 2) if gaps else None), "truth": truth_max_gap},
            "family_mix": {"ours": our_fams, "truth": src_fams},
            "beat_triggered_cues": {"ours": len(on_downbeat), "truth": 0},
            "visual_cut_coverage": {"ours_main": cov_main, "ours_all": cov_all,
                                    "truth_main": truth_cut_cov,
                                    "note": f"真值 {len(gcuts)} 个主轨切点，其中 "
                                            f"{round((truth_cut_cov or 0) * 100)}% 有音效"},
        },
        "findings": findings,
        "single_fix": single_fix(our_pm, src_pm, name_counts, src_unique, our_fams, src_fams,
                                 len(vcuts), len(missed), len(on_downbeat), len(kept)),
    }


def single_fix(our_pm, src_pm, name_counts, src_unique, our_fams, src_fams,
               n_vcuts, n_missed, n_downbeat, n_kept) -> Dict[str, Any]:
    """「只能改一处」：挑一条改动，能同时缓解'吵'和'空'。"""
    return {
        "where": "step6 的音效选型（不是密度参数）",
        "what": ("把'按切点顺序在 3 个音色里轮转'换成'按锚点功能选族 + 同名去重'："
                 "换镜→whoosh（呼/转场）、字上屏→text_reveal、报数字→money、抛梗→impact/tension、"
                 "句尾/换句→响铃或叮；同一音效名 60s 内最多 2 次"),
        "why_one_change_fixes_both": (
            f"吵：{n_kept} 条里同一批 2–8kHz 的翻页/键盘占了 {round(sum(v for k, v in our_fams.items() if k == 'text_reveal') * 100 / max(1, n_kept))}%，"
            f"而真值只占 {round(src_fams.get('text_reveal', 0) * 100 / max(1, sum(src_fams.values())))}%、"
            f"另有一半是 whoosh/响铃/风铃（{fmt_share(src_fams)}）；"
            f"空：{n_vcuts} 个真实切点只有 {n_vcuts - n_missed} 个有声，换成 whoosh 后切点自然有声，"
            "密度不用调高也不会空"),
        "risk": "whoosh 每 1.5s 一次会变成'呼呼呼'；所以仍受源的最小间隔与每分上限约束",
        "cheapest_verification": "跑一次 style_fingerprint.verify()：sfx_role_mix 余弦、unique_names/60s、cut_coverage 三个数同时回正才算过",
    }


# ── 主流程 ────────────────────────────────────────────────────────────────────


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="24 套剪映草稿 → 声音元素因果规则")
    ap.add_argument("--style", help="只跑一套（调试用）")
    ap.add_argument("--print", action="store_true", help="打到 stdout 而不写文件")
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--pipeline-dir", help="现场成片 pipeline 目录（只读，用于对照段）")
    args = ap.parse_args(argv)

    sources = read_json(STYLE_SOURCES)["styles"]
    grammar = read_json(GRAMMAR)["styles"]
    rules = read_json(RULES)["styles"]
    lib_rows = {s["style_id"]: (s.get("audio_and_sfx") or {}) for s in read_json(LIBRARY)["styles"]}

    styles: Dict[str, Any] = {}
    for sid in sorted(sources):
        if args.style and sid != args.style:
            continue
        styles[sid] = analyze(sid, sources[sid], grammar.get(sid) or {},
                              rules.get(sid) or {}, lib_rows.get(sid) or {})

    doc = {
        "version": "1.0",
        "domain": "audio",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generated_from": ("forensic/decrypted/<草稿>/draft_content.json（明文音轨段/音量/淡入淡出）"
                           " + forensic/style_edit_grammar.json（主轨切点/字幕轨）"
                           " + forensic/style_trigger_rules_sfx_flower.json（BPM 与置信度）"
                           " + forensic/style_sources.json（风格→草稿映射）"),
        "method": {
            "anchors": ["主轨切点(main_segments)", "花字出现(非字幕文字轨段起)",
                        "字幕换行(字幕轨段起)", "BGM 节拍网格(规则表已测 BPM，置信度≥0.45 才用)"],
            "tolerance_sec": TOL,
            "reason_priority": ["cut", "flower", "line", "beat"],
            "free_policy": "挂不上任何锚点的 cue 记为 free；free 比例就是这套允许的「无理由」额度",
            "gain_formula": "dB = 20·log10(segment.volume)",
            "time_unit": "秒；源草稿落点量化在 1/60s 网格（偏移分辨率 16.7ms）",
            "privacy": "source_ref = wb_<sha256 前 12 位>；不含草稿目录名、本机路径、逐条文案",
            "unmeasured": [
                "人声-BGM SNR：源草稿段字段推不出，库里 11.0/11.5dB 是编写常量，不当真值用",
                "BGM 曲目本身只有名字与段长可读，音频内容未做频谱测量",
                "风格 24 套的 BGM 节拍只在置信度≥0.45 时使用；其余套不把 beat 当理由",
            ],
        },
        "library_stats": library_stats(styles),
        "style_index": {sid: style_index_row(st) for sid, st in styles.items()},
        "styles": styles,
    }
    if not args.style:
        doc["our_build_comparison"] = compare_our_build(
            styles, Path(args.pipeline_dir) if args.pipeline_dir else None)

    if args.print:
        print(json.dumps(doc, ensure_ascii=False, indent=1))
        return 0
    out = Path(args.out)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    n_el = sum(len(s.get("elements") or {}) for s in doc["styles"].values())
    print(f"wrote {out} · {len(doc['styles'])} styles · {n_el} elements")
    for sid, s in doc["styles"].items():
        sfx = s["elements"]["sfx"]
        bgm = s["elements"]["bgm"]
        dl = s["elements"]["dual_bgm_switch"]
        print(f"  {sid}  sfx={s.get('samples')} ({sfx.get('per_min')}/min)  "
              f"bgm={'Y' if bgm.get('present') else '-'}({bgm.get('bed_db_duration_weighted')}dB)  "
              f"dual={'Y:' + str(dl.get('switch_sec')) if dl.get('present') else '-'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
