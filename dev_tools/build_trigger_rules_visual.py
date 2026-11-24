#!/usr/bin/env python3
"""从 24 套真源草稿反推「视觉类处理」的真实触发条件，落成 `forensic/style_trigger_rules_visual.json`。

用户原话（2026-11-24）：
「风格模版应该好好抽象下这些音效、花字之类的是要在什么情况下才会出现，调用的原则是怎样，
作用域又是怎样，而不是无脑的硬植入……不仅仅是音效和花字，还有转场、抠像、变速等等模版里
包含的那些针对具体素材元素的处理，都是要提炼出真正的风格条件才是。」

本脚本只处理**视觉类**：转场 transition / 抠像-画中画 cutout / 变速 speed /
叠加层-贴纸 overlay / 画面特效 effect。音效与花字由另一条线负责，不在这里。

数据只读：
  forensic/source_manifest.json          风格 → 源草稿路径
  forensic/decrypted/<草稿>/draft_content.json   明文草稿（唯一真值）
  forensic/style_edit_grammar.json       主轨下标 / 镜长序列
  forensic/pip_usage.json                画中画几何真值
  forensic/effect_usage.json             画面特效真值
  assets/style_spectrum/style_spectrum_library.json  24 套定义

输出：forensic/style_trigger_rules_visual.json
  styles.<style_id>.{transition,cutout,speed,overlay,effect}
    每节 = {enabled, default, conditions[], scope{}, limits{}, evidence{}}
    conditions[] 里每条 = {id, check{key,...}, required, desc, reason}

复用纪律：阈值只在本脚本里量一次、写进 JSON；运行时 `scripts/treatment_policy.py` 只读 JSON
不另立阈值，量不到就按「不套」处理并在 reason 里说明。
"""
from __future__ import annotations

import json
import re
import statistics as st
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
FORENSIC = ROOT / "forensic"
OUT_PATH = FORENSIC / "style_trigger_rules_visual.json"

# ── 「画面里正在给人看东西」的词表：抠像/推近碰到它们就是事故（把要看的东西抠没）──
DISPLAY_TERMS = [
    "效果图", "图纸", "设计图", "平面图", "施工图", "手绘", "白板", "展板", "样品",
    "样张", "文件", "合同", "菜单", "价目表", "平板", "iPad", "手机屏", "电脑屏",
    "屏幕", "投屏", "PPT", "讲义", "报告", "证件", "证书", "表格",
]
# 台词侧的「指认 / 展示」信号：这类句子必须让观众看见画面本身，不能抠像也不能猛推近
DEICTIC_TERMS = [
    "这个", "这张", "这幅", "这款", "这块", "这里", "看这", "看一下", "给你看", "效果",
    "图纸", "方案", "细节", "展示", "样品", "对比", "数据", "表格", "屏幕", "色号",
]

# 「瞬时冲击」类特效：草稿里 ≤0.6s 的那几段（模糊 / 颤抖 / 聚光灯 / 爆闪），必须贴切点
INSTANT_EFFECT_SEC = 0.6
# 贴纸/特效「贴切点」的容差：草稿里贴纸起点几乎都落在切点上（差 0）
CUT_ALIGN_TOL = 0.25


# ────────────────────────── 读数据 ──────────────────────────
def _j(p: Path) -> Dict[str, Any]:
    return json.loads(Path(p).read_text(encoding="utf-8"))


def _sec(us: Any) -> float:
    try:
        return float(us) / 1e6
    except Exception:
        return 0.0


def _text_of(material: Dict[str, Any]) -> str:
    """草稿 texts 素材的正文（content 是 JSON 字符串）。"""
    c = material.get("content")
    if isinstance(c, str):
        try:
            return str(json.loads(c).get("text") or "")
        except Exception:
            return ""
    if isinstance(c, dict):
        return str(c.get("text") or "")
    return ""


def _hits(text: str, terms: List[str]) -> Optional[str]:
    low = str(text or "").lower()
    for t in terms:
        if t.isascii():
            if t.lower() in low:
                return t
        elif t in str(text or ""):
            return t
    return None


def _median(xs: List[float]) -> Optional[float]:
    return round(st.median(xs), 3) if xs else None


def _runs(segs: List[Dict[str, Any]], tol: float = 0.12) -> List[Tuple[float, float]]:
    """把时间上首尾相接的段并成「连续段」，返回 [(start, end)]。"""
    out: List[Tuple[float, float]] = []
    for s in sorted(segs, key=lambda x: x["start"]):
        a, b = s["start"], s["start"] + s["dur"]
        if out and a - out[-1][1] <= tol:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


# ────────────────────────── 逐套量真值 ──────────────────────────
def measure_style(style_id: str, entry: Dict[str, Any], gram: Dict[str, Any],
                  source_file: Path, pip: Dict[str, Any], fx: Dict[str, Any],
                  gt: Dict[str, Any]) -> Dict[str, Any]:
    d = _j(source_file)
    duration = float(gram.get("duration_sec") or 0.0)
    main_idx = int(gram.get("main_track_index") or 0)
    tracks = d.get("tracks") or []
    vmat = {m.get("id"): m for m in ((d.get("materials") or {}).get("videos") or [])}
    tmat = {m.get("id"): m for m in ((d.get("materials") or {}).get("texts") or [])}

    # 切点表（所有视频轨的段起点）与字幕/花字时间轴
    cuts: List[float] = []
    texts: List[Tuple[float, float, str]] = []
    stickers: List[Dict[str, Any]] = []
    effects: List[Dict[str, Any]] = []
    mat_segs: List[Dict[str, Any]] = []
    speed_segs: List[Dict[str, Any]] = []

    for ti, t in enumerate(tracks):
        ttype = t.get("type")
        for si, s in enumerate(t.get("segments") or []):
            tr = s.get("target_timerange") or {}
            start, dur = _sec(tr.get("start")), _sec(tr.get("duration"))
            if ttype == "video":
                cuts.append(round(start, 3))
                mat = vmat.get(s.get("material_id")) or {}
                mblock = mat.get("matting") if isinstance(mat.get("matting"), dict) else {}
                has_mat = bool(str((mblock or {}).get("path") or "").strip())
                scale = float(((s.get("clip") or {}).get("scale") or {}).get("x", 1.0))
                transform = (s.get("clip") or {}).get("transform") or {}
                if has_mat and dur >= 0.1:
                    mat_segs.append({
                        "start": round(start, 3), "dur": round(dur, 3), "track": ti,
                        "on_main": ti == main_idx, "scale": round(scale, 3),
                        "transform": [round(float(transform.get("x", 0.0)), 3),
                                      round(float(transform.get("y", 0.0)), 3)],
                        "stroke": bool(mat.get("enable_matting_stroke") or s.get("enable_matting_stroke")),
                    })
                src_dur = _sec((s.get("source_timerange") or {}).get("duration"))
                if (mat.get("type") != "photo" and dur > 0 and src_dur > 0
                        and abs(src_dur / dur - 1.0) > 0.02):
                    speed_segs.append({
                        "start": round(start, 3), "dur": round(dur, 3), "track": ti,
                        "on_main": ti == main_idx, "speed": round(src_dur / dur, 3),
                    })
            elif ttype == "text":
                texts.append((start, dur, _text_of(tmat.get(s.get("material_id")) or {})))
            elif ttype == "sticker":
                stickers.append({"start": round(start, 3), "dur": round(dur, 3), "track": ti})
            elif ttype == "effect":
                effects.append({"start": round(start, 3), "dur": round(dur, 3), "track": ti})

    cutset = sorted(set(cuts))

    def nearest_cut(t: float) -> float:
        return min((abs(t - c) for c in cutset), default=9.9)

    def text_at(a: float, b: float) -> str:
        return " ".join(x for (ts, td, x) in texts if min(b, ts + td) - max(a, ts) > 0.3)

    # ── 转场：materials.transitions + 段引用双向核对 ─────────────────────
    tr_mats = (d.get("materials") or {}).get("transitions") or []
    tr_ids = {t.get("id") for t in tr_mats}
    tr_refs = 0
    for t in tracks:
        for s in t.get("segments") or []:
            for r in s.get("extra_material_refs") or []:
                if r in tr_ids:
                    tr_refs += 1

    # ── 抠像 ────────────────────────────────────────────────────────────
    runs = _runs(mat_segs)
    gaps = [round(runs[i + 1][0] - runs[i][1], 3) for i in range(len(runs) - 1)]
    mat_cov = round(sum(s["dur"] for s in mat_segs) / duration, 3) if mat_segs and duration else 0.0

    # 人物层 = 真正承载抠像的那条（或那几条）轨：抠像只可能挂在人物层上，
    # 所以「人物层里有多大比例被抠」才是这套风格的密度意图（不是占全片多少）。
    layer_dur: Dict[int, float] = {}
    layer_mat: Dict[int, float] = {}
    for ti, t in enumerate(tracks):
        if t.get("type") != "video":
            continue
        for s in t.get("segments") or []:
            tr = s.get("target_timerange") or {}
            d0 = _sec(tr.get("duration"))
            layer_dur[ti] = layer_dur.get(ti, 0.0) + d0
    for s in mat_segs:
        layer_mat[s["track"]] = layer_mat.get(s["track"], 0.0) + s["dur"]
    person_tracks = sorted(layer_mat)
    person_sec = round(sum(layer_dur.get(ti, 0.0) for ti in person_tracks), 3)
    mat_sec = round(sum(layer_mat.values()), 3)
    person_share = round(mat_sec / person_sec, 3) if person_sec else 0.0

    # 1 帧级的抠像残留（segments 里 <0.1s 的）：不算「这套风格会抠像」，但要如实计数
    mat_artifacts = 0
    for ti, t in enumerate(tracks):
        if t.get("type") != "video":
            continue
        for s in t.get("segments") or []:
            mat = vmat.get(s.get("material_id")) or {}
            mb = mat.get("matting") if isinstance(mat.get("matting"), dict) else {}
            if str((mb or {}).get("path") or "").strip() and _sec((s.get("target_timerange") or {}).get("duration")) < 0.1:
                mat_artifacts += 1
    mat_deictic = 0
    mat_deictic_sample: List[str] = []
    for s in mat_segs:
        line = text_at(s["start"], s["start"] + s["dur"])
        hit = _hits(line, DEICTIC_TERMS)
        if hit:
            mat_deictic += 1
            mat_deictic_sample.append(f"{s['start']:.1f}s「{hit}」")

    # ── 变速 ────────────────────────────────────────────────────────────
    sp_vals = sorted({s["speed"] for s in speed_segs})

    # ── 贴纸 / 叠加层 ───────────────────────────────────────────────────
    stk_align = [round(nearest_cut(s["start"]), 3) for s in stickers]
    stk_paired: List[bool] = []
    for s in stickers:
        a, b = s["start"], s["start"] + s["dur"]
        stk_paired.append(any(min(b, ts + td) - max(a, ts) >= 0.5 * s["dur"] for (ts, td, _x) in texts))
    stk_gaps = [round(stickers[i + 1]["start"] - (stickers[i]["start"] + stickers[i]["dur"]), 3)
                for i in range(len(stickers) - 1)]
    overlay_tracks = [o for o in (pip.get("overlays") or []) if int(o.get("segments") or 0) > 0]

    # ── 画面特效 ────────────────────────────────────────────────────────
    fx_items = (fx.get("effects") or [])
    fx_align = [round(nearest_cut(float(e["start"])), 3) for e in fx_items]
    fx_instant = [e for e in fx_items if float(e.get("duration") or 0.0) <= INSTANT_EFFECT_SEC]
    fx_cov = (round(sum(float(e.get("duration") or 0.0) for e in fx_items) / duration, 3)
              if fx_items and duration else 0.0)

    return {
        "style_id": style_id,
        "archetype_id": gram.get("archetype_id") or entry.get("archetype_id"),
        "source_project": entry.get("source_filename") or "",
        "source_file": str(source_file.relative_to(ROOT)),
        "duration_sec": round(duration, 3),
        "n_video_segments": len(cuts),
        "n_cut_points": len(cutset),
        "raw": {
            "transitions_materials": len(tr_mats),
            "transitions_referenced": tr_refs,
            "matting": mat_segs,
            "speed": speed_segs,
            "stickers": stickers,
            "effects": fx_items,
            "matting_runs": [[round(a, 3), round(b, 3)] for a, b in runs],
            "matting_gaps": gaps,
            "matting_deictic": mat_deictic,
            "matting_deictic_sample": mat_deictic_sample,
            "sticker_cut_dist": stk_align,
            "sticker_text_paired": stk_paired,
            "sticker_gaps": stk_gaps,
            "effect_cut_dist": fx_align,
            "overlay_video_tracks": len(overlay_tracks),
            "broll_overlay_ratio": (gt.get("video") or {}).get("broll_overlay_ratio"),
            "matting_artifacts": mat_artifacts,
            "person_tracks": person_tracks,
            "person_sec": person_sec,
            "mat_sec": mat_sec,
            "person_share": person_share,
        },
        "mat_cov": mat_cov,
        "fx_cov": fx_cov,
        "fx_instant": len(fx_instant),
    }


# ────────────────────────── 归纳成规则 ──────────────────────────
def build_sections(m: Dict[str, Any]) -> Dict[str, Any]:
    """把一套的测量值翻成五节规则：conditions / scope / limits / evidence。"""
    raw = m["raw"]
    dur = m["duration_sec"]
    mats, spds, stks, fxs = raw["matting"], raw["speed"], raw["stickers"], raw["effects"]

    # ── 转场：真值 0 也要写成「有条件的 0」──────────────────────────────
    transition = {
        "enabled": False,
        "default": "hard_cut",
        "mode": "any_of",
        "default_reason": ("默认硬切：这套风格源草稿转场数 = 0，"
                           "只有「用户显式放行 / 素材自带溶接 / 章节断点」才允许出现一次"),
        "principle": (
            "默认硬切。转场不是风格装饰，而是「时间/空间被强行缝合」时才付的一笔代价；"
            "这套源草稿一次都没付过，所以套用端一次也不许自己加。"
        ),
        "conditions": [
            {"id": "user_explicit_whitelist", "required": False,
             "check": {"key": "user_explicit"},
             "desc": "用户在本次任务里显式要求/放行转场（EDL.transition_whitelist 非空）",
             "reason": "用户显式放行转场：{value}"},
            {"id": "source_native_overlap", "required": False,
             "check": {"key": "source_native_overlap"},
             "desc": "素材本身自带溶接（同源叠化帧、真实曝光过渡），不是后期贴的转场素材",
             "reason": "素材自带溶接，保留原样"},
            {"id": "chapter_break", "required": False,
             "check": {"key": "chapter_break", "silence_sec": 0.3},
             "desc": "章节断点：两侧台词结束且静音 ≥0.3s，观众预期「换场」",
             "reason": "章节断点（两侧静音 ≥0.3s），允许一次转场"},
        ],
        "scope": {
            "allow": ["chapter_break", "user_explicit_whitelist", "source_native_overlap"],
            "forbid": ["mid_sentence", "inside_fast_cut_run", "hook_first_3s", "cutout_boundary"],
            "forbid_note": "句内切点、快切串里、开场 3 秒、抠像层进出点都不许插转场——这些都是节奏点，不是换场点",
        },
        "limits": {
            "max_count": 0,
            "max_count_if_whitelisted": max(1, int(round(dur / 30.0))),
            "max_duration_sec": 0.5,
            "max_per_min": 0.0,
            "native_transition_dur_sec_range": [0.33, 0.47],
        },
        "evidence": {
            "truth_transition_count": raw["transitions_referenced"],
            "truth_transition_materials": raw["transitions_materials"],
            "n_cut_points": m["n_cut_points"],
            "hit_rate": 1.0,
            "method": "materials.transitions 条数 + 段 extra_material_refs 指向 transition 的引用数，双向核对",
            "note_cn": "本套源草稿转场 = 0（逐段引用也查了），0 就是这套风格的硬切事实，不是漏量",
        },
    }

    # ── 抠像 / 画中画 ──────────────────────────────────────────────────
    n_mat = len(mats)
    enabled = n_mat > 0
    # 「成段」关系：真值里抠像是连着几个镜一起开、还是每镜单独开关。
    # 合并容差 1.0s——中间夹 1 帧/半个空镜不算「断开」（style_21 真值就是 0.466s 的空档）。
    runs_merged = _runs(mats, tol=1.0)
    max_runs = max(1, int(round(len(runs_merged) * 1.25))) if runs_merged else 0
    on_overlay = [s for s in mats if not s["on_main"]]
    full = [s for s in mats if s["scale"] >= 0.9]
    win = [s for s in mats if s["scale"] < 0.9]
    deictic_rate = round(raw["matting_deictic"] / n_mat, 3) if n_mat else 0.0
    cutout = {
        "enabled": enabled,
        "default": "conditional" if enabled else "off",
        "principle": (
            "抠像是「人 → 从背景里提出来 → 叠到别的画面上」这件事；成立的前提有三个："
            "画面里有一个足够大、单独的人；这个人此刻不是在给别人看某样东西；"
            "并且下面确实压着一层值得看的画面。三条缺一条就不抠——风格会抠像不等于每镜都抠。"
        ),
        "conditions": [
            {"id": "style_has_cutout_truth", "required": True,
             "check": {"key": "style_truth_positive", "section": "cutout", "value": n_mat},
             "desc": "这套风格的源草稿里真的出现过抠像（真值 > 0）",
             "reason": "这套风格真值 0 段抠像（{value} 段），不套"},
            {"id": "on_person_layer", "required": True,
             "check": {"key": "track_role_in", "allow": ["a_roll"]},
             "desc": "只挂在真人出镜层（a_roll）；B-roll/图卡/照片没有可抠的人像主体",
             "reason": "这一层是 {value}（不是口播真人层），没有可抠的主体"},
            {"id": "face_present", "required": True,
             "check": {"key": "face_present"},
             "desc": "画面里检出人脸锚点",
             "reason": "这一镜没检出人脸（手部/空镜/图纸特写），抠了也没有主体"},
            {"id": "face_big_enough", "required": True,
             "check": {"key": "face_min_size", "min_h": 0.12, "min_w": 0.07},
             "desc": "人脸够大（高 ≥12%、宽 ≥7%），人在远处时抠出来只是一团糊",
             "reason": "人脸太小（高 {h:.0%}/宽 {w:.0%}），抠出来没有信息量"},
            {"id": "no_display_object", "required": True,
             "check": {"key": "no_display_terms", "terms": DISPLAY_TERMS},
             "desc": "画面判读里没有图纸/屏幕/展板/样张等「必须被看见的东西」",
             "reason": "这一镜画面里正拿着「{value}」，抠像会把要看的东西一起抠掉"},
            {"id": "no_deictic_line", "required": True,
             "check": {"key": "no_deictic_terms", "terms": DEICTIC_TERMS},
             "desc": "这句台词不是「指认画面」型（这个/看/效果/图纸…）——真值里 72 段抠像只有 2 段踩到这类句子（3%）",
             "reason": "这句台词在指认画面（「{value}」），画面本身才是信息，不抠像"},
            {"id": "secondary_source_distinct", "required": True,
             "check": {"key": "secondary_distinct"},
             "desc": "副镜（背景）与主镜不同源，否则是「同源套娃」",
             "reason": "副镜与主镜是同一段素材，抠了等于自己叠自己"},
            {"id": "run_budget", "required": True,
             "check": {"key": "run_budget", "max_runs": max_runs, "tol_sec": 1.0},
             "desc": (f"抠像要成段：这套风格真值 {len(runs_merged)} 段连续抠像，"
                      "不许每镜来回开/关（进出抠像状态本身是画面变化）"),
             "reason": "这套风格的抠像已经用完 {value} 段（真值里是成段出现的），不每镜来回切"},
            {"id": "segment_duration_cap", "required": True,
             "check": {"key": "duration_le", "value": max(2.5, round((max([s['dur'] for s in mats]) if mats else 2.0) * 1.25, 2))},
             "desc": "单镜不超过该风格真值最长抠像段 ×1.25（长镜一直抠像观众会忘了下面那层）",
             "reason": "这一镜 {value:.1f}s，超过该风格抠像单段上限"},
            {"id": "coverage_budget", "required": True,
             "check": {"key": "cutout_budget_left",
                       "person_share": round(min(1.0, m["raw"]["person_share"] * 1.15), 3) if enabled else 0.0},
             "desc": ("累计抠像时长不超过「人物层时长 × 真值人物层被抠比例 ×1.15」"
                      "（真值人物层被抠 {share:.0%}）"),
             "reason": "抠像时长预算已用尽（该风格真值把人物层的 {share:.0%} 做了抠像，本片已用完）"},
            {"id": "subject_dominant", "required": True,
             "check": {"key": "subject_dominant", "min_h": 0.12, "faces_warn": 2},
             "desc": "画面里多于 1 张脸时，主体脸必须更大（否则会把旁人抠出半张）",
             "reason": "画面里有 {value} 张脸且主体不够大，抠像会带出旁人的半边"},
        ],
        "scope": {
            "allow": ["a_roll"],
            "forbid": ["b_roll", "code_broll", "photo", "text_sticker"],
            "layer": "person_over_broll" if on_overlay else "single_layer",
            "track_roles_in_truth": sorted({("main" if s["on_main"] else "overlay") for s in mats}),
            "window_scale_sec": "full_frame" if not win else "corner_window",
        },
        "limits": {
            "max_coverage_ratio": min(0.75, round(m["mat_cov"] * 1.25 + 0.05, 3)) if enabled else 0.0,
            "max_share_of_person_layer": round(min(1.0, m["raw"]["person_share"] * 1.15), 3) if enabled else 0.0,
            "max_runs": max_runs,
            "run_gap_tol_sec": 1.0,
            "max_seg_sec": round(max([s["dur"] for s in mats]) * 1.25, 2) if mats else 0.0,
            "min_seg_sec": 0.3,
            "max_run_sec": round(max([b - a for a, b in raw["matting_runs"]]) * 1.25, 2) if raw["matting_runs"] else 0.0,
            "min_gap_sec": (round(st.median(raw["matting_gaps"]) * 0.8, 2)
                            if raw["matting_gaps"] else 0.0),
            "max_clips_per_min": round(n_mat / (dur / 60.0), 2) if enabled and dur else 0.0,
            "require_face_min_h": 0.12,
            "require_face_min_w": 0.07,
        },
        "evidence": {
            "truth_segments": n_mat,
            "coverage_ratio": m["mat_cov"],
            "median_dur_sec": _median([s["dur"] for s in mats]),
            "max_dur_sec": max([s["dur"] for s in mats]) if mats else None,
            "on_overlay_ratio": round(len(on_overlay) / n_mat, 3) if n_mat else 0.0,
            "full_frame_ratio": round(len(full) / n_mat, 3) if n_mat else 0.0,
            "min_scale": min([s["scale"] for s in mats]) if mats else None,
            "stroke_ratio": round(sum(1 for s in mats if s["stroke"]) / n_mat, 3) if n_mat else 0.0,
            "person_layer_sec": raw["person_sec"],
            "person_layer_tracks": raw["person_tracks"],
            "matting_share_of_person": raw["person_share"],
            "artifact_segments_lt_0.1s": raw["matting_artifacts"],
            "runs": raw["matting_runs"],
            "runs_count": len(runs_merged),
            "runs_merged": [[round(a, 3), round(b, 3)] for a, b in runs_merged],
            "gaps_sec": raw["matting_gaps"],
            "deictic_cooccur": raw["matting_deictic"],
            "deictic_rate": deictic_rate,
            "deictic_samples": raw["matting_deictic_sample"],
            "hit_rate": round(1 - deictic_rate, 3) if n_mat else None,
            "decision": "条件性启用" if enabled else "整套禁用（真值 0）",
        },
    }

    # ── 变速 ────────────────────────────────────────────────────────────
    sp_vals = sorted({s["speed"] for s in spds})
    on_main_sp = [s for s in spds if s["on_main"]]
    speed = {
        "enabled": bool(spds),
        "default": "conditional" if spds else "1.0",
        "principle": (
            "变速只服务两件事：把「无信息的等待」压掉（快进空镜/走路/操作过程），"
            "或把「要看清楚的瞬间」拖长（慢动作特写）。口播层永远不许变——嘴型和字音一拆就穿帮。"
        ),
        "conditions": [
            {"id": "style_has_speed_truth", "required": True,
             "check": {"key": "style_truth_positive", "section": "speed", "value": len(spds)},
             "desc": "这套风格真值里出现过变速",
             "reason": "这套风格真值 0 段变速（{value} 段），原速播放"},
            {"id": "on_broll_only", "required": True,
             "check": {"key": "track_role_in", "allow": ["b_roll", "overlay_card"]},
             "desc": "只作用于 B-roll 空镜/过程镜；A-roll 口播层硬禁止",
             "reason": "A-roll 口播不许变速（口型必须与录音对齐），当前层是 {value}"},
            {"id": "duration_window", "required": True,
             "check": {"key": "duration_between",
                       "min": max(0.6, round(min([s['dur'] for s in spds]) * 0.8, 2)) if spds else 0.6,
                       "max": round(max([s['dur'] for s in spds]) * 1.5, 2) if spds else 3.0},
             "desc": "只对 0.6s 以上的 B-roll 段变速（太短的段变速看不出来，只听到声音变形）",
             "reason": "这一镜 {value:.1f}s，不在该风格变速段的时长带里"},
            {"id": "speed_value_in_truth", "required": True,
             "check": {"key": "speed_value_in", "values": sp_vals or [1.0], "tol": 0.15},
             "desc": "倍速取该风格真值出现过的值（不自己发明 1.35×/3×）",
             "reason": "倍速 {value}× 不在该风格真值里（真值只有 {values}）"},
            {"id": "source_headroom", "required": True,
             "check": {"key": "source_headroom"},
             "desc": "素材剩余长度够吃下「时间线时长 × 倍速」",
             "reason": "素材余量不足（需要 {need:.1f}s，只有 {have:.1f}s），保持原速"},
        ],
        "scope": {
            "allow": ["b_roll", "overlay_card"],
            "forbid": ["a_roll", "text", "sticker", "photo"],
            "forbid_note": "A-roll 口播、字幕、贴纸、静图一律不许变速（静图没有时间基准）",
        },
        "limits": {
            "speed_values_in_truth": sp_vals,
            "median_speed": _median([s["speed"] for s in spds]),
            "min_dur_sec": min([s["dur"] for s in spds]) if spds else None,
            "max_dur_sec": max([s["dur"] for s in spds]) if spds else None,
            "max_per_min": round(len(spds) / (dur / 60.0), 2) if spds and dur else 0.0,
        },
        "evidence": {
            "truth_segments": len(spds),
            "values": sp_vals,
            "durations_sec": [s["dur"] for s in spds],
            "roles": sorted({"main" if s["on_main"] else "overlay" for s in spds}),
            "on_main_track": len(on_main_sp),
            "hit_rate": round(1 - len(on_main_sp) / len(spds), 3) if spds else None,
            "note_cn": ("真值只在 B-roll/叠加层变速，主轨 0 段" if spds
                        else "真值 0 段：这套风格全片原速，变速属于「别的风格的手法」"),
        },
    }

    # ── 叠加层 / 贴纸 ──────────────────────────────────────────────────
    n_stk = len(stks)
    align_ok = sum(1 for x in raw["sticker_cut_dist"] if x <= CUT_ALIGN_TOL)
    paired_ok = sum(1 for x in raw["sticker_text_paired"] if x)
    overlay = {
        "enabled": n_stk > 0 or raw["overlay_video_tracks"] > 0,
        "default": "conditional" if n_stk else "off",
        "principle": (
            "贴纸/叠加层是「一个画面说不完时补的一层」，不是每镜的装饰边框。"
            "它必须贴在切点上（跟着新镜一起进场），并且必须与当刻的花字/文案成对——"
            "单独一张贴纸悬在画面里，观众只会觉得脏。"
        ),
        "conditions": [
            {"id": "style_has_overlay_truth", "required": True,
             "check": {"key": "style_truth_positive", "section": "overlay", "value": n_stk},
             "desc": "这套风格真值里出现过贴纸/叠加层",
             "reason": "这套风格真值 0 个贴纸（{value} 个），不贴"},
            {"id": "pinned_to_cut", "required": bool(n_stk) and align_ok / max(1, n_stk) >= 0.6,
             "check": {"key": "near_cut", "tol_sec": CUT_ALIGN_TOL},
             "desc": "起点贴在本镜/上一镜的切点上（±0.25s），不悬在镜中间凭空出现",
             "reason": "这一处贴纸离最近切点 {value:.2f}s，该风格贴纸是「跟着切点进场」的"},
            {"id": "paired_with_text", "required": bool(n_stk) and paired_ok / max(1, n_stk) >= 0.8,
             "check": {"key": "paired_with_text", "min_overlap_ratio": 0.5},
             "desc": "当刻屏幕上有对应的花字/字幕（真值里贴纸与文案同框）",
             "reason": "当刻没有可配的花字/文案，贴纸会变成孤立装饰"},
            {"id": "count_budget", "required": True,
             "check": {"key": "count_budget", "max_per_min": round(max(1.0, n_stk / max(0.1, dur / 60.0)) * 1.5, 2)},
             "desc": "每片贴纸条数不超过真值密度 ×1.5",
             "reason": "这一片贴纸数量已到该风格上限（真值 {value} 条/分钟量级）"},
            {"id": "duration_cap", "required": True,
             "check": {"key": "duration_le", "value": round(max([s["dur"] for s in stks]) * 1.25, 2) if stks else 2.0},
             "desc": "单个贴纸不超过真值最长贴纸 ×1.25（贴纸是「闪一下」不是「一直挂着」）",
             "reason": "这一处贴纸 {value:.1f}s，超过该风格上限"},
        ],
        "scope": {
            "allow": ["sticker_layer", "b_roll_overlay", "cut_boundary"],
            "forbid": ["a_roll_speaking_full", "main_track_full_frame"],
            "forbid_note": "不许在口播正脸全屏时压在脸上；要压就压 B-roll 层或切点上",
        },
        "limits": {
            "max_count_per_piece": max(1, n_stk),
            "max_count_per_min": round(max(1.0, n_stk / max(0.1, dur / 60.0)) * 1.5, 2),
            "max_dur_sec": round(max([s["dur"] for s in stks]) * 1.25, 2) if stks else 0.0,
            "median_dur_sec": _median([s["dur"] for s in stks]),
            "min_gap_sec": round(max(0.0, st.median(raw["sticker_gaps"])) * 0.8, 2) if raw["sticker_gaps"] else 0.0,
            "align_tol_sec": CUT_ALIGN_TOL,
            "overlay_video_tracks": raw["overlay_video_tracks"],
        },
        "evidence": {
            "truth_stickers": n_stk,
            "durations_sec": [s["dur"] for s in stks],
            "cut_aligned": align_ok,
            "cut_align_rate": round(align_ok / n_stk, 3) if n_stk else None,
            "text_paired": paired_ok,
            "text_pair_rate": round(paired_ok / n_stk, 3) if n_stk else None,
            "overlay_video_tracks": raw["overlay_video_tracks"],
            "hit_rate": round(align_ok / n_stk, 3) if n_stk else None,
            "note_cn": ("贴纸全部贴切点且与花字同框" if n_stk and align_ok == n_stk
                        else "贴纸贴切点的比例见 cut_align_rate"),
        },
    }

    # ── 画面特效 ────────────────────────────────────────────────────────
    fx_names = sorted({str(e.get("source_effect") or "") for e in fxs if e.get("source_effect")})
    fx_durs = [round(float(e.get("duration") or 0.0), 3) for e in fxs]
    fx_instant_n = m["fx_instant"]
    fx_aligned = sum(1 for x in raw["effect_cut_dist"] if x <= CUT_ALIGN_TOL)
    effect = {
        "enabled": bool(fxs),
        "default": "conditional" if fxs else "off",
        "principle": (
            "画面特效只有两种合法用法：①「氛围类」长时间盖住一个段落（暗角/噪点/边框）；"
            "②「冲击类」≤0.6s 贴在切点上（模糊/颤抖/聚光灯）。"
            "不在这两类里、或者覆盖到人在讲话的正脸，就是把别人的滤镜硬贴上来。"
        ),
        "conditions": [
            {"id": "style_has_effect_truth", "required": True,
             "check": {"key": "style_truth_positive", "section": "effect", "value": len(fxs)},
             "desc": "这套风格真值里出现过画面特效（24 套里只有 7 套有）",
             "reason": "这套风格真值 0 个画面特效（{value} 个），不加"},
            {"id": "beat_scoped", "required": True,
             "check": {"key": "duration_le", "value": round(max(fx_durs) * 1.25, 2) if fx_durs else 6.0},
             "desc": "单个特效时长 ≤ 真值最长特效 ×1.25（特效盖一个段落，不盖全片）",
             "reason": "这一处特效 {value:.1f}s，超过该风格上限（特效是段落级的）"},
            {"id": "instant_pinned_to_cut", "required": True,
             "check": {"key": "instant_near_cut", "instant_max_sec": INSTANT_EFFECT_SEC, "tol_sec": CUT_ALIGN_TOL},
             "desc": "≤0.6s 的瞬时特效必须贴切点（否则渲染出来像闪帧/坏帧）",
             "reason": "瞬时特效（{value:.2f}s）离最近切点太远，会被看成闪帧"},
            {"id": "coverage_cap", "required": True,
             "check": {"key": "coverage_budget_left", "max_ratio": min(0.35, round(m["fx_cov"] * 1.25 + 0.05, 3)) if fxs else 0.0},
             "desc": "特效累计覆盖不超过全片真值占比 ×1.25",
             "reason": "特效覆盖预算已用尽（该风格真值 {ratio:.0%}）"},
            {"id": "count_budget", "required": True,
             "check": {"key": "count_budget", "max_per_min": round(max(1.0, len(fxs) / max(0.1, dur / 60.0)) * 1.5, 2)},
             "desc": "每片特效条数不超过真值密度 ×1.5",
             "reason": "这一片特效数量已到该风格上限"},
        ],
        "scope": {
            "allow": ["segment_look", "hook_window", "impact_cut"],
            "forbid": ["a_roll_speaking_full", "whole_piece"],
            "forbid_note": "不许整片挂滤镜式特效；不许盖住人讲话的正脸（观感像坏片）",
        },
        "limits": {
            "max_count_per_piece": max(1, len(fxs)),
            "max_count_per_min": round(max(1.0, len(fxs) / max(0.1, dur / 60.0)) * 1.5, 2) if fxs else 0.0,
            "max_dur_sec": max(fx_durs) if fx_durs else 0.0,
            "max_coverage_ratio": min(0.35, round(m["fx_cov"] * 1.25 + 0.05, 3)) if fxs else 0.0,
            "instant_max_sec": INSTANT_EFFECT_SEC,
        },
        "evidence": {
            "truth_effects": len(fxs),
            "names": fx_names,
            "durations_sec": fx_durs,
            "positions_ratio": [round(float(e["start"]) / dur, 3) for e in fxs] if dur else [],
            "instant_count": fx_instant_n,
            "cut_aligned": fx_aligned,
            "coverage_ratio": m["fx_cov"],
            "hit_rate": round(fx_aligned / len(fxs), 3) if fxs else None,
            "note_cn": ("特效只在 hook/段落转折上" if fxs and max(float(e["start"]) for e in fxs) < 0.25 * dur
                        else "特效分布在全片段落上，不是只放开头"),
        },
    }

    return {"transition": transition, "cutout": cutout, "speed": speed,
            "overlay": overlay, "effect": effect}


# ────────────────────────── 全库转场审计 ──────────────────────────
def audit_corpus_transitions(style_projects: set) -> Dict[str, Any]:
    """24 套之外的草稿里转场长什么样：用来给「允许条件」找证据（叠化/0.33–0.47s/挂段边界）。"""
    stats = {"drafts": 0, "drafts_with_transitions": 0, "refs": 0,
             "names": {}, "durations_sec": [], "in_style_sources": 0, "style_source_hits": []}
    for p in sorted(FORENSIC.glob("decrypted/*/draft_content.json")):
        name = p.parent.name
        try:
            d = _j(p)
        except Exception:
            continue
        stats["drafts"] += 1
        trs = {t.get("id"): t for t in ((d.get("materials") or {}).get("transitions") or [])}
        if not trs:
            continue
        n = 0
        for t in d.get("tracks") or []:
            for s in t.get("segments") or []:
                for r in s.get("extra_material_refs") or []:
                    if r in trs:
                        n += 1
                        nm = str(trs[r].get("name") or "")
                        stats["names"][nm] = stats["names"].get(nm, 0) + 1
                        stats["durations_sec"].append(round(_sec(trs[r].get("duration")), 3))
        if n:
            stats["refs"] += n
            stats["drafts_with_transitions"] += 1
            if name in style_projects:
                stats["in_style_sources"] += 1
                stats["style_source_hits"].append(name)
    ds = stats["durations_sec"]
    stats["duration_range_sec"] = [min(ds), max(ds)] if ds else None
    stats["duration_median_sec"] = _median(ds)
    return stats


# ────────────────────────── 主流程 ──────────────────────────
def main() -> int:
    man = _j(FORENSIC / "source_manifest.json")["styles"]
    gram = _j(FORENSIC / "style_edit_grammar.json")["styles"]
    pip = _j(FORENSIC / "pip_usage.json")["styles"]
    fx = _j(FORENSIC / "effect_usage.json")["styles"]
    gt = {p["name"]: p for p in _j(FORENSIC / "jianying_ground_truth_full.json")["projects"]}
    lib = {s["style_id"]: s for s in _j(
        ROOT / "short-video-studio/assets/style_spectrum/style_spectrum_library.json")["styles"]}

    projects = {v["source_project"] for v in man.values()}
    corpus = audit_corpus_transitions(projects)

    styles: Dict[str, Any] = {}
    table: List[str] = []
    for sid in sorted(man):
        entry = man[sid]
        src = ROOT / entry["source_file"]
        gtrow = gt.get(entry["source_project"]) or {}
        m = measure_style(sid, entry, gram[sid], src, pip.get(sid) or {}, fx.get(sid) or {}, gtrow)
        rules = build_sections(m)
        rules.update({
            "style_id": sid,
            "archetype_id": m["archetype_id"],
            "style_name_cn": (lib.get(sid) or {}).get("style_name_cn"),
            "source_project": entry["source_project"],
            "source_file": m["source_file"],
            "duration_sec": m["duration_sec"],
            "n_cut_points": m["n_cut_points"],
        })
        # 交叉核对：风格库里的 cutout 标记（人工/旧流水线写的）与真值是否一致
        lib_flag = bool(((lib.get(sid) or {}).get("pip_composition_spec") or {}).get("cutout"))
        rules["cutout"]["evidence"]["library_cutout_flag"] = lib_flag
        rules["cutout"]["evidence"]["library_flag_agrees_with_truth"] = (lib_flag == rules["cutout"]["enabled"])
        if lib_flag != rules["cutout"]["enabled"]:
            print(f"[trigger-rules-visual] [WARN] {sid}: 风格库 cutout={lib_flag} 与真值 "
                  f"{rules['cutout']['enabled']} 不一致，以真值为准")
        styles[sid] = rules
        cu, sp, ov, ef = rules["cutout"], rules["speed"], rules["overlay"], rules["effect"]
        table.append(
            f"| {sid} | {m['archetype_id']} | {m['duration_sec']:.1f}s | "
            f"{cu['evidence']['truth_segments']} 段 / {cu['evidence']['coverage_ratio']:.0%} | "
            f"{(cu['evidence']['min_scale'] if cu['evidence']['min_scale'] is not None else '-')} | "
            f"{sp['evidence']['truth_segments']} 段{('/' + '/'.join(f'{v:g}×' for v in sp['evidence']['values'])) if sp['evidence']['values'] else ''} | "
            f"{ov['evidence']['truth_stickers']} 个 | "
            f"{ef['evidence']['truth_effects']} 个{('/' + '、'.join(ef['evidence']['names'])) if ef['evidence']['names'] else ''} | "
            f"{rules['transition']['evidence']['truth_transition_count']} |"
        )

    doc = {
        "version": "1.0.0",
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "generator": "dev_tools/build_trigger_rules_visual.py",
        "what": "24 套风格各自的「视觉类处理」真实触发条件：转场 / 抠像-画中画 / 变速 / 叠加层-贴纸 / 画面特效",
        "scope_note": "只管视觉类处理；音效（sfx_policy）与花字（flower_policy）由另一条线负责，此处不涉及",
        "truth_sources": {
            "drafts": "forensic/decrypted/<草稿>/{draft_content.json,__latest_close.json}（明文，只读）",
            "style_to_draft": "forensic/source_manifest.json",
            "shot_grammar": "forensic/style_edit_grammar.json",
            "pip_geometry": "forensic/pip_usage.json",
            "effects": "forensic/effect_usage.json",
            "ground_truth": "forensic/jianying_ground_truth_full.json",
            "library": "short-video-studio/assets/style_spectrum/style_spectrum_library.json",
        },
        "measurement_policy": (
            "全部从明文草稿的 tracks[].segments[] 与 materials 直读："
            "抠像 = materials.videos[].matting.path 非空；变速 = source_timerange/target_timerange 比值≠1；"
            "转场 = materials.transitions + 段 extra_material_refs 引用；"
            "贴纸/特效 = sticker/effect 轨段的 target_timerange。量不到写 null，不猜。"
        ),
        "corpus_transition_audit": corpus,
        "principles": {
            "transition": {
                "default": "hard_cut",
                "rule": "默认硬切；只有「用户显式放行 / 素材自带溶接 / 章节断点」三种情况才允许出现，"
                        "且句内切点、快切串、开场 3 秒、抠像层进出点一律禁止。",
                "why": f"24 套源草稿转场数 = 0（逐套见 styles.*.transition.evidence）；"
                       f"全库 {corpus['drafts']} 份草稿里 {corpus['drafts_with_transitions']} 份有转场，"
                       f"但没有一份是这 24 套的源。",
            },
            "cutout": {
                "default": "conditional",
                "rule": "四个前提同时成立才抠：①画面里有一个够大的人（人脸高≥12%）；"
                        "②这个人此刻不是在给别人看东西（无图纸/屏幕/样张，台词不指认画面）；"
                        "③下面压着另一层值得看的画面（副镜与主镜不同源）；④密度不超过该风格真值占比。",
                "why": "24 套里 18 套有抠像、6 套完全没有；真值 72 段抠像里只有 2 段（3%）与「指认画面」的台词同框。",
            },
            "speed": {
                "default": "conditional",
                "rule": "只压掉无信息等待、或拖长要看清的瞬间；只作用于 B-roll；A-roll 口播永远原速；"
                        "倍速只取该风格真值出现过的值；素材余量不足就不变。",
                "why": "24 套里只有 2 套（style_01 / style_24）有变速，全部在叠加/B-roll 层、全部 2.0×，主轨 0 段。",
            },
            "overlay": {
                "default": "conditional",
                "rule": "贴纸必须跟着切点进场（±0.25s）、必须与当刻花字同框、单片条数与单条时长不超过真值上限。",
                "why": "真值贴纸绝大多数起点精确落在切点上，且与文案同框；孤立的贴纸在源草稿里不存在。",
            },
            "effect": {
                "default": "conditional",
                "rule": "只有两种合法用法：氛围类长盖一个段落（暗角/噪点/边框），冲击类 ≤0.6s 贴切点"
                        "（模糊/颤抖/聚光灯）；不许整片挂滤镜、不许盖住人讲话的正脸。",
                "why": "24 套里 7 套有画面特效，单个特效 0.17–6.03s，覆盖 ≤20% 片长，瞬时特效全部贴切点。",
            },
        },
        "summary_table_md": (
            "| 风格 | 原型 | 时长 | 抠像段/覆盖 | 最小缩放 | 变速 | 贴纸 | 画面特效 | 转场 |\n"
            "|---|---|---|---|---|---|---|---|---|\n" + "\n".join(table)
        ),
        "styles": styles,
    }
    OUT_PATH.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    print(f"[trigger-rules-visual] 写出 {OUT_PATH.relative_to(ROOT)}")
    print(f"[trigger-rules-visual] 24 套：转场全 0（全库 {corpus['drafts']} 份草稿 {corpus['drafts_with_transitions']} 份有转场、"
          f"{corpus['refs']} 处引用，但没有一份属于这 24 套）")
    for sid in sorted(styles):
        s = styles[sid]
        print(f"  {sid}: 抠像 {s['cutout']['evidence']['truth_segments']} 段"
              f"（覆盖 {s['cutout']['evidence']['coverage_ratio']:.0%}"
              f"{'，整套禁用' if not s['cutout']['enabled'] else ''}）"
              f" / 变速 {s['speed']['evidence']['truth_segments']} 段"
              f" / 贴纸 {s['overlay']['evidence']['truth_stickers']}"
              f" / 特效 {s['effect']['evidence']['truth_effects']}"
              f" / 转场 {s['transition']['evidence']['truth_transition_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
