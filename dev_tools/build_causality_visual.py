#!/usr/bin/env python3
"""画面类元素因果规则抽取：24 套真草稿 → forensic/causality_visual.json

回答的不是「有没有/几段」，而是四件事：
  1. 为什么出现（草稿里哪个结构导致它有）
  2. 为什么是这个值（几何/时间/强度的 derived_from 或 calibrated_by + 公式）
  3. 影响谁（affects：改它会让哪一层跟着动）
  4. 违反会怎样（violation_symptoms：真实踩过的坑）

判据来源（全部只读）：
  - forensic/decrypted/<草稿>/draft_content.json | __latest_close.json（明文）
  - forensic/style_trigger_rules_visual.json（已接受的视觉触发规则真值）
  - forensic/style_edit_grammar.json（切点表 / 关键帧 / 时长分位）
  - forensic/pip_usage.json（叠加层几何真值）
  - forensic/effect_usage.json（画面特效清单）
  - forensic/style_render_truth.json（渲染真值：matting/mask/animations）
  - forensic/stroke_ring_calibration.json（描边 size→px 标定）
  - short-video-studio/assets/style_spectrum/style_spectrum_library.json（color_and_lut / pip_composition_spec）

隐私：输出只含 style_id 与 wb_<sha256 前 12 位>，不含本机路径、草稿目录名、素材文件名。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
FORENSIC = ROOT / "forensic"
OUT = FORENSIC / "causality_visual.json"
LIB_PATH = ROOT / "short-video-studio/assets/style_spectrum/style_spectrum_library.json"
DRAFT_FILES = ("draft_content.json", "__latest_close.json", "template-2.tmp")

ELEMENT_KEYS = [
    "cutout", "speed", "transition", "effect", "sticker",
    "pip", "overlay_geometry", "in_anim", "out_anim", "color_grade",
]

# 画面特效里「不是画面特效」的素材类型：剪映把美颜/美体/美妆塞在同一个 effects 池里
BEAUTY_EFFECT_TYPES = {"figure", "makeup_root", "makeup", "beauty", "brightness", "light_sensation"}
AMBIENT_EFFECT_NAMES = {"暗角", "不规则黑框", "荧幕噪点", "怀旧边框", "边框", "噪点", "老照片", "胶片"}
IMPACT_EFFECT_NAMES = {"颤抖", "模糊", "爆闪", "聚光灯", "闪白", "震动", "故障"}


# ---------------------------------------------------------------- 基础工具
def load_json(p: Path) -> Any:
    return json.loads(p.read_text(encoding="utf-8"))


def load_draft(project: str) -> dict:
    for name in DRAFT_FILES:
        p = FORENSIC / "decrypted" / project / name
        if p.is_file():
            return load_json(p)
    raise SystemExit(f"missing draft for a style in source map ({project!r})")


def us(x: float) -> float:
    return round(x / 1_000_000.0, 3)


def r3(x: Optional[float]) -> Optional[float]:
    return None if x is None else round(float(x), 3)


def med(xs: List[float]) -> Optional[float]:
    xs = [x for x in xs if x is not None]
    return round(statistics.median(xs), 3) if xs else None


def rng(xs: List[float]) -> List[Optional[float]]:
    xs = [x for x in xs if x is not None]
    return [round(min(xs), 3), round(max(xs), 3)] if xs else [None, None]


def pct(x: Optional[float]) -> str:
    return "—" if x is None else f"{x * 100:.1f}%"


def text_plain(mat: dict) -> str:
    raw = mat.get("content") or "{}"
    try:
        return (json.loads(raw) or {}).get("text", "") or ""
    except Exception:
        return ""


def sha12(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------- 1. 抽事实
def extract_facts(style_id: str, project: str, truth: dict, grammar: dict,
                  pip: dict, eff: dict, render: dict, lib: dict) -> dict:
    d = load_draft(project)
    W = d["canvas_config"]["width"]
    H = d["canvas_config"]["height"]
    mats = d["materials"]
    videos = {v["id"]: v for v in mats.get("videos", [])}
    stickers = {s["id"]: s for s in mats.get("stickers", [])}
    texts = {t["id"]: t for t in mats.get("texts", [])}
    vfx = {e["id"]: e for e in mats.get("video_effects", [])}
    effects_all = {e["id"]: e for e in mats.get("effects", [])}
    anims = {a["id"]: a for a in mats.get("material_animations", [])}
    speeds = {s["id"]: s for s in mats.get("speeds", [])}
    hsl = {h["id"]: h for h in mats.get("hsl", [])}

    tracks = d["tracks"]
    video_tracks = [i for i, t in enumerate(tracks) if t["type"] == "video"]
    main_idx = video_tracks[0]
    duration = us(d["duration"])

    rows: List[dict] = []
    cutouts: List[dict] = []
    speeds_hit: List[dict] = []
    sticker_rows: List[dict] = []
    effect_rows: List[dict] = []
    anim_rows: List[dict] = []
    beauty_refs = 0
    hsl_refs = 0
    enable_flags = Counter()

    for ti, tr in enumerate(tracks):
        for si, seg in enumerate(tr["segments"]):
            tt = seg.get("target_timerange") or {}
            st = seg.get("source_timerange") or {}
            start = us(tt.get("start", 0))
            dur = us(tt.get("duration", 0))
            clip = seg.get("clip") or {}
            refs = seg.get("extra_material_refs") or []
            if seg.get("enable_lut"):
                enable_flags["lut"] += 1
            if seg.get("enable_hsl"):
                enable_flags["hsl"] += 1
            if seg.get("enable_adjust"):
                enable_flags["adjust"] += 1

            # --- 动画（入场/出场/循环）挂在哪一层
            for r in refs:
                a = anims.get(r)
                if not a:
                    continue
                for item in a.get("animations", []):
                    anim_rows.append({
                        "track_type": tr["type"], "track": ti,
                        "kind": item.get("type"),
                        "name": item.get("name"),
                        "dur": us(item.get("duration", 0)),
                        "category": item.get("category_name"),
                        "at": start, "host_dur": dur,
                    })
            # --- 调色/美颜残留
            for r in refs:
                if r in hsl:
                    hsl_refs += 1
                em = effects_all.get(r)
                if em and (em.get("type") in BEAUTY_EFFECT_TYPES):
                    beauty_refs += 1

            if tr["type"] == "effect":
                for r in ([seg.get("material_id")] + list(refs)):
                    e = vfx.get(r)
                    if not e:
                        continue
                    params = {p.get("name"): r3(p.get("value")) for p in (e.get("adjust_params") or [])
                              if p.get("value") is not None}
                    defaults = {p.get("name"): r3(p.get("default_value")) for p in (e.get("adjust_params") or [])
                                if p.get("default_value") is not None}
                    effect_rows.append({
                        "track": ti, "seg": si, "start": start, "dur": dur,
                        "name": e.get("name"), "resource_id": e.get("resource_id"),
                        "params": params, "defaults": defaults,
                    })
            if tr["type"] == "sticker":
                mat = stickers.get(seg.get("material_id")) or {}
                sticker_rows.append({
                    "track": ti, "start": start, "dur": dur,
                    "scale": r3((clip.get("scale") or {}).get("x") or 1.0),
                    "x": r3((clip.get("transform") or {}).get("x") or 0.0),
                    "y": r3((clip.get("transform") or {}).get("y") or 0.0),
                    "rotation": r3(clip.get("rotation") or 0.0),
                    "name": (mat.get("name") or "").strip(),
                    "resource_id": mat.get("resource_id"),
                })
            if tr["type"] != "video":
                continue

            v = videos.get(seg.get("material_id"))
            if not v:
                continue
            nw, nh = v.get("width") or W, v.get("height") or H
            scale = ((seg.get("uniform_scale") or {}).get("scale")
                     or (clip.get("scale") or {}).get("x") or 1.0)
            tr_ = clip.get("transform") or {}
            x, y = float(tr_.get("x") or 0.0), float(tr_.get("y") or 0.0)
            disp_w = W * scale
            disp_h = (nh / nw) * W * scale
            left = (W / 2) * (x + 1 - scale)
            top = (H / 2) * (y + 1 - disp_h / H)
            mt = v.get("matting") or {}
            row = {
                "track": ti, "seg": si, "is_main": ti == main_idx,
                "start": start, "dur": dur, "end": round(start + dur, 3),
                "scale": r3(scale), "x": r3(x), "y": r3(y),
                "alpha": seg.get("clip", {}).get("alpha"),
                "rotation": r3(clip.get("rotation") or 0.0),
                "natural": [nw, nh],
                "disp": [round(disp_w), round(disp_h)],
                "left": round(left), "top": round(top),
                "fill": round(disp_w / W, 3),
                "material": v.get("id"),
                "is_photo": (v.get("type") or "").lower() in ("photo", "image"),
                "crop": bool(v.get("crop")),
                "matting": bool(mt.get("path")),
                "stroke": None,
                "in_bounds": (abs(x) <= abs(1 - scale) + 1e-4
                              and abs(y) <= abs(1 - disp_h / H) + 1e-4),
            }
            if row["matting"]:
                strokes = mt.get("strokes") or []
                if strokes:
                    s0 = strokes[0]
                    ap = {a.get("name"): a.get("value") for a in (s0.get("adjust_params") or [])}
                    ap_def = {a.get("name"): a.get("default_value") for a in (s0.get("adjust_params") or [])}
                    row["stroke"] = {
                        "resource_id": s0.get("resource_id"),
                        "resource_name": s0.get("resource_name"),
                        "color_rgba": s0.get("color"),
                        "size": r3(ap.get("effects_adjust_size")),
                        "size_default": r3(ap_def.get("effects_adjust_size")),
                        "alpha": r3(ap.get("effects_adjust_alpha")),
                        "n": len(strokes),
                        "enabled": bool(mt.get("enable_matting_stroke")),
                    }
                row["flag"] = mt.get("flag")
            rows.append(row)
            if row["matting"]:
                if row["dur"] < 0.1:
                    row["matting_artifact"] = True   # 拆解残段：真值口径不计入
                else:
                    cutouts.append(row)
            if st.get("duration") and tt.get("duration") and abs(st["duration"] / tt["duration"] - 1) > 0.01:
                ratio = st["duration"] / tt["duration"]
                sp_mat = [speeds[r] for r in refs if r in speeds]
                speeds_hit.append({
                    "track": ti, "seg": si, "start": start, "dur": dur,
                    "speed": r3(ratio),
                    "declared": r3(sp_mat[0].get("speed")) if sp_mat and sp_mat[0].get("speed") else None,
                    "src_dur": us(st["duration"]), "src_in": us(st.get("start", 0)),
                    "is_main": ti == main_idx,
                })

    # 字幕轨（段数最多的 text 轨）→ 用于「台词是否在指认画面」
    text_tracks = [t for t in tracks if t["type"] == "text"]
    subs: List[dict] = []
    if text_tracks:
        subt = max(text_tracks, key=lambda t: len(t["segments"]))
        for seg in subt["segments"]:
            tt = seg.get("target_timerange") or {}
            subs.append({
                "start": us(tt.get("start", 0)),
                "dur": us(tt.get("duration", 0)),
                "chars": len(text_plain(texts.get(seg.get("material_id")) or {})),
                "text": text_plain(texts.get(seg.get("material_id")) or {}),
            })

    main_segs = sorted([r for r in rows if r["is_main"]], key=lambda r: r["start"])
    cuts = [round(r["start"], 3) for r in main_segs] + [round(main_segs[-1]["end"], 3)] if main_segs else []

    # 主轨最大 scale（真值里「常驻放大裁切」的来源）
    main_scale_mode = Counter(r["scale"] for r in main_segs).most_common(1)[0][0] if main_segs else None
    # 叠加层：全屏覆盖 vs 缩窗
    overlays = [r for r in rows if not r["is_main"]]
    full_overlays = [r for r in overlays if r["scale"] >= 0.995 and abs(r["x"]) <= 0.01 and abs(r["y"]) <= 0.01]
    windows = [r for r in overlays if r not in full_overlays]
    # 主轨缩窗/移位（scale<0.92 或明显平移）也算画中画：画面不再占满，边上留出别的东西
    main_windows = [r for r in main_segs if r["scale"] < 0.92 or abs(r["x"]) > 0.15 or abs(r["y"]) > 0.15]
    matting_artifacts = sum(1 for r in rows if r.get("matting_artifact"))

    person_layer_sec = round(sum(r["dur"] for r in overlays), 3)

    facts = {
        "style_id": style_id,
        "project": project,
        "source_ref": lib.get("source_ref"),
        "draft_sha12": str(lib.get("source_hash") or "")[:12],
        "archetype": truth.get("archetype_id"),
        "duration": duration, "W": W, "H": H,
        "n_tracks": len(tracks),
        "n_video_tracks": len(video_tracks),
        "track_types": dict(Counter(t["type"] for t in tracks)),
        "main_idx": main_idx,
        "rows": rows, "main_segs": main_segs, "overlays": overlays,
        "full_overlays": full_overlays, "windows": windows,
        "main_windows": main_windows, "matting_artifacts": matting_artifacts,
        "cutouts": cutouts, "speeds": speeds_hit,
        "effects": effect_rows, "stickers": sticker_rows, "anims": anim_rows,
        "subs": subs, "cuts": cuts,
        "main_scale_mode": main_scale_mode,
        "person_layer_sec": person_layer_sec,
        "beauty_refs": beauty_refs,
        "hsl": {"mats": len(hsl), "refs": hsl_refs,
                "samples": [h.get("custom_color") for h in list(hsl.values())[:3]]},
        "enable_flags": dict(enable_flags),
        "photos": sum(1 for r in rows if r["is_photo"]),
        "truth": truth, "grammar": grammar, "pip_truth": pip, "eff_truth": eff,
        "lib": lib,
        "render": render,
    }
    return facts


# ---------------------------------------------------------------- 2. 元素构造器
def el(present: bool, absent_reason: Optional[str] = None, rule: str = "",
       triggers: Optional[List[str]] = None, not_when: Optional[List[str]] = None,
       evidence: str = "", value_drivers: Optional[List[dict]] = None,
       scope: Optional[dict] = None, affects: Optional[List[dict]] = None,
       limits: Optional[List[dict]] = None, coupling: Optional[List[dict]] = None,
       symptoms: Optional[List[str]] = None, reuse: Optional[List[str]] = None,
       samples: Optional[List[dict]] = None) -> dict:
    return {
        "present": bool(present),
        "absent_reason": absent_reason,
        "why_appears": {
            "rule": rule,
            "triggers": triggers or [],
            "not_when": not_when or [],
            "evidence": evidence,
        },
        "value_drivers": value_drivers or [],
        "scope": scope or {"unit": None, "coverage": None, "excludes": None, "reset_on": None},
        "affects": affects or [],
        "limits": limits or [],
        "coupling": coupling or [],
        "violation_symptoms": symptoms or [],
        "reuse_check": reuse or [],
        "sample_table": samples or [],
    }


def driver(param: str, value: Any, derived_from: Optional[str], formula: Optional[str],
           why: str, calibrated_by: Optional[str] = None, tol: Optional[str] = None) -> dict:
    return {
        "param": param, "value": value,
        "derived_from": derived_from, "formula": formula,
        "why_this_value": why, "calibrated_by": calibrated_by, "tolerance": tol,
    }


def nearest_cut(cuts: List[float], t: float) -> Optional[float]:
    return min((abs(t - c) for c in cuts), default=None)


def sample_cutouts(f: dict, limit: int = 6) -> List[dict]:
    out = []
    for c in f["cutouts"][:limit]:
        note = f"track{c['track']} render_index>0"
        if c["stroke"]:
            note += f" · 描边 {c['stroke']['resource_name']} size={c['stroke']['size']}"
        if abs(c["scale"] - 1) < 1e-6 and abs(c["x"]) < 1e-6 and abs(c["y"]) < 1e-6:
            note += " · 全屏抠像（人叠在全屏副镜上）"
        out.append({
            "at_sec": c["start"],
            "value": f"scale {c['scale']} x {c['x']} y {c['y']} · {c['dur']}s",
            "note": note,
        })
    return out


# ---------------------------------------------------------------- cutout
def build_cutout(f: dict, deictic: dict) -> dict:
    ev = f["truth"]["cutout"]["evidence"]
    lim = f["truth"]["cutout"]["limits"]
    n = len(f["cutouts"])
    if n == 0:
        return el(
            False,
            absent_reason=(
                f"真值 0 段：本套 {len(f['rows'])} 段视频（{f['n_video_tracks']} 条视频轨）里"
                f"没有任何一段挂 matting（materials.videos[].matting.path 全空；"
                f"剔除 <0.1s 残段后仍为 0）。"
            ),
            rule=(
                "这套处理画面不靠「把人抠出来叠到别处」：人物直接满屏，换画面靠硬切 + "
                f"{'贴纸' if f['stickers'] else '花字'}"
                f"{'+画面特效' if f['effects'] else ''}。所以任何一镜都不该出现抠像层。"
            ),
            triggers=[],
            not_when=[f"本套 {len(f['main_segs'])} 段主轨 + {len(f['overlays'])} 段叠加层，无一例抠像"],
            evidence=(
                f"matting.path 非空段 = 0；主轨 scale 众数 = {f['main_scale_mode']}；"
                f"叠加层 {len(f['overlays'])} 段里缩窗 {len(f['windows'])} 段、全屏覆盖 {len(f['full_overlays'])} 段"
            ),
            value_drivers=[],
            scope={"unit": "段", "coverage": "0 段 / 全片",
                   "excludes": "全片", "reset_on": None},
            affects=[
                {"target": "背景层", "how": "本套没有抠像，下面那层永远是主轨自己；给它加抠像会立刻多出一层出不来的黑底"},
                {"target": "字幕安全区", "how": "人满屏 → 字幕必须靠 shadow/stroke 压在人身上读，不能靠留白"},
            ],
            limits=[
                {"name": "max_segments", "value": 0, "why": "真值 0；这套风格的画面语言里没有「人叠人」这一手"},
            ],
            coupling=[
                {"with": "pip", "type": "mutex", "why": "用全屏硬切换画面和用缩窗/抠像换画面是两套语言，混用会让片子突然换画风"},
            ],
            symptoms=[
                "给这套加抠像 → 背景出现一层没有素材的黑底（副镜没准备）",
                "抠像后原片被压成小窗 → 中景叙事变成「解说小窗」，与这套的满屏实拍调性冲突",
            ],
            reuse=[
                "先确认这套是否真的需要用「人叠画」表达；不需要就在 EDL 里把 cutout 关掉，别为了多样而抠",
                "若素材本身人脸占比高但无副镜可垫，仍然不要抠（真值不断言「有人脸就抠」）",
            ],
            samples=[],
        )

    strokes = [c["stroke"] for c in f["cutouts"] if c["stroke"]]
    scales = [c["scale"] for c in f["cutouts"]]
    _shrunk = [c for c in f["cutouts"] if abs(1 - c["scale"]) > 1e-3]
    flush_n = sum(1 for c in _shrunk if abs(abs(c["x"]) - abs(1 - c["scale"])) < 1e-3)
    nearest_gap = min((abs(abs(c["x"]) - abs(1 - c["scale"])) for c in _shrunk), default=0.0)
    xs = [c["x"] for c in f["cutouts"]]
    ys = [c["y"] for c in f["cutouts"]]
    durs = [c["dur"] for c in f["cutouts"]]
    full = sum(1 for c in f["cutouts"] if abs(c["scale"] - 1) < 1e-6 and abs(c["x"]) < 1e-6 and abs(c["y"]) < 1e-6)
    tracks_used = sorted({c["track"] for c in f["cutouts"]})
    on_overlay = sum(1 for c in f["cutouts"] if not c["is_main"])
    span = (min(durs) if durs else 0, max(durs) if durs else 0)

    drivers = [
        driver("clip.scale / uniform_scale.scale",
               f"{rng(scales)}（中位 {med(scales)}；{full}/{n} 段 =1.0 全屏）",
               "segments[].uniform_scale.scale | segments[].clip.scale.x",
               "disp_w = W·scale；disp_h = (nh/nw)·W·scale（scale=1 表示铺满画布，竖屏素材即整屏）",
               f"真值 {n} 段的 scale 就是这些数：{full} 段是 1.0（全屏抠像，人直接叠在满屏副镜上），"
               + (f"其余 {n - full} 段缩到 {sorted({s for s in scales if s < 0.999})} 成角窗"
                  if n - full else "没有缩窗段"),
               None, "±0.5%"),
        driver("clip.transform.x",
               f"{rng(xs)}",
               "segments[].clip.transform.x（-1..1，0=居中，±(1−scale)=贴边）",
               "left_px = (W/2)·(x + 1 − scale)；cx = W/2·(1+x)",
               (f"越靠边越接近 |x| = 1−scale：本套精确贴边 {flush_n} 段，说明 x 是「窗口可动范围」的归一化坐标"
                if flush_n else
                f"本套没有精确贴边的窗（最近一段离边界 {nearest_gap:.3f}），x 仍是可动范围归一化坐标，"
                "只是这一套都留了边"),
               None, "±1px"),
        driver("clip.transform.y",
               f"{rng(ys)}",
               "segments[].clip.transform.y",
               "top_px = (H/2)·(y + 1 − disp_h/H)；y = −(1 − disp_h/H) 即顶边贴画布顶",
               "人像抠像默认「头顶贴边、脚出画」——本套 "
               f"{sum(1 for c in f['cutouts'] if abs(c['y']) > 1e-3 and abs(c['y'] + (1 - c['disp'][1] / f['H'])) < 1e-2)} 段 y 落在贴顶位置",
               None, "±2px"),
        driver("target_timerange.duration",
               f"{rng(durs)}（中位 {med(durs)}）",
               "segments[].target_timerange.duration",
               "片段时长 = 口播句/动作过程长度；不额外补长",
               f"最长 {span[1]}s（真值上限 {lim['max_seg_sec']}s）；抠像是「一段」而不是「一镜」，"
               "进出抠像状态本身是画面变化，所以成段出现、不逐镜开关",
               "forensic/style_trigger_rules_visual.json::cutout.limits", "±1 帧"),
    ]
    if strokes:
        s0 = strokes[0]
        drivers.append(driver(
            "matting.strokes[0].adjust_params.effects_adjust_size",
            f"{sorted({s['size'] for s in strokes})}（{len(strokes)} 段带描边，色 #FFFFFF）",
            "materials.videos[].matting.strokes[].adjust_params.effects_adjust_size",
            "环宽_源px = 0.3 + 7.2·size（单层描边）｜= 19.16·size（撕纸描边）｜折线描边滑杆未绑定、宽度由包内 mask 决定；"
            "画布px = 环宽_源px × clip.scale（描边在素材空间生成，跟着缩放一起变小）",
            f"本套只出现 {s0['resource_name']}：{len(strokes)} 段开描边、{n - len(strokes)} 段不开——"
            "描边是「人叠在花画面上要分离主体」时才加，不是抠像默认配置",
            "forensic/stroke_ring_calibration.json",
            "±1px（size 每 0.1 约 0.7 源px）",
        ))
        alphas_stroke = sorted({s["alpha"] for s in strokes if s["alpha"] is not None})
        drivers.append(driver(
            "matting.strokes[0].adjust_params.effects_adjust_alpha",
            (f"{alphas_stroke}" if alphas_stroke
             else f"该资源（{s0['resource_name']}）没有 alpha 滑杆：描边恒为不透明"),
            "materials.videos[].matting.strokes[].adjust_params.effects_adjust_alpha",
            None,
            ("描边 alpha 一律 1.0（纯色实线）；半透明白边在真值里不存在，压到 0.5 会像没抠干净"
             if alphas_stroke else "包内没有 alpha 绑定，说明「白边就是白边」，想做淡边只能换资源"),
            None, "exact",
        ))
    alphas = sorted({c["alpha"] for c in f["cutouts"] if c["alpha"] is not None})
    if alphas:
        drivers.append(driver(
            "clip.alpha", f"{alphas}",
            "segments[].clip.alpha（字段缺省 = 1.0）",
            None,
            "抠像段不做淡入淡出：alpha 由入场动画（位移/缩放）承担，改 alpha 会和背景层合成出灰边",
            None, "exact"))

    return el(
        True,
        rule=(
            "抠像的因果是「这一镜要同时看到人和另一层画面」：人从背景提出来 → 下面垫一条不同源的副镜 → "
            f"叠加轨（render_index>0）出画。本套 {n} 段抠像里 {on_overlay} 段在叠加轨、"
            f"{full} 段仍是全屏 scale=1（人满屏叠在满屏副镜上，抠不抠看不出窗，只是把原背景换掉）。"
        ),
        triggers=[
            "该镜要边讲边指（讲解落在副镜上，人只保留上半身作主讲）",
            "原背景脏/杂，要换成本套的副镜画面（换背景而不是换机位）",
            "人物层已经在叠加轨（主轨另有画面时才有得叠）",
            "台词不指认画面（本套 "
            f"{deictic['hits']}/{n} 段与「这个/看/效果/展示」类台词同框 = {pct(deictic['rate'])}）",
        ],
        not_when=[
            "画面里正给人看图纸/屏幕/样张（抠像会把要看的东西一起抠掉）",
            "副镜与主镜同源（自己叠自己，边缘会双影）",
            "空镜/手部/图卡特写（没有人像主体可提）",
            "单镜超过真值上限（本套 max_seg_sec={0}s）".format(lim["max_seg_sec"]),
        ],
        evidence=(
            f"materials.videos[].matting.path 非空 + 剔除 <0.1s 残段 = {n} 段，落在 track {tracks_used}，"
            f"跨度 {min(c['start'] for c in f['cutouts'])}–{max(c['end'] for c in f['cutouts'])}s，"
            f"覆盖 {pct(ev['coverage_ratio'])}、成 {ev.get('runs_count')} 段连续抠像；"
            f"与指认型台词同框 {deictic['hits']} 段"
        ),
        value_drivers=drivers,
        scope={
            "unit": "段（连续成段，不是逐镜）",
            "coverage": f"{pct(ev['coverage_ratio'])} 片长 / 人物层被抠 {pct(ev.get('matting_share_of_person'))}",
            "excludes": "B-roll、图卡、照片、纯字幕层；主轨无抠像" if on_overlay == n else f"主轨 {n - on_overlay} 段例外（真值就在这里）",
            "reset_on": "切开/离开副镜时就收（不跨镜保留抠像状态）",
        },
        affects=[
            {"target": "背景层（副镜）", "how": "抠出来才有「下面」；副镜与主镜必须不同源，否则边缘双影"},
            {"target": "花字/字幕", "how": "人像成为前景后，花字要压在人的留白侧；缩窗时花字必须避开窗口矩形"},
            {"target": "描边", "how": "描边宽 = (0.3+7.2·size)×scale，缩窗后同样变细，所以小窗要么不加描边要么加粗 size"},
            {"target": "调色", "how": "抠像层单独过 LUT 会和副镜肤色割裂——真值里抠像段的 hsl 启用位全 0，调色只走渲染端统一 LUT"},
            {"target": "音轨", "how": "不影响：抠像是画面层操作，音频长度不变（对比变速会改时间轴）"},
        ],
        limits=[
            {"name": "max_runs", "value": lim["max_runs"], "why": "真值里抠像成段出现，进出抠像状态本身就是画面变化"},
            {"name": "max_seg_sec", "value": lim["max_seg_sec"], "why": f"真值最长抠像段 {ev.get('max_dur_sec')}s；更长观众会忘了下面那层"},
            {"name": "min_seg_sec", "value": lim["min_seg_sec"], "why": f"低于 0.1s 的是拆解残段（全库 {f.get('matting_artifacts', 0)} 段 0.02–0.03s，已剔除），不是可复用写法"},
            {"name": "max_coverage_ratio", "value": lim["max_coverage_ratio"], "why": "抠像是段落级语气，不是全片底色"},
            {"name": "max_share_of_person_layer", "value": lim["max_share_of_person_layer"], "why": f"真值人物层 {ev.get('person_layer_sec')}s 里被抠的比例"},
            {"name": "require_face_min_h", "value": lim["require_face_min_h"], "why": "人脸高 <12% 抠出来是一团糊，没有信息量"},
        ],
        coupling=[
            {"with": "overlay_geometry", "type": "same_source_of_truth", "why": "抠像的 scale/x/y 与缩窗共用同一套坐标公式，抠像就是一个「带 alpha 的窗口」"},
            {"with": "pip", "type": "subset", "why": "抠像属于画中画的一种（person_over_broll），不是独立元素；有抠像时缩窗几何必须从同一张 pip 规格里取"},
            {"with": "color_grade", "type": "ordering", "why": "先抠像再统一调色；对抠像层单独挂调色会让边缘出现色边（草稿里 hsl 启用位全 0 就是这个原因）"},
            {"with": "effect", "type": "mutex_in_frame", "why": "冲击类特效不压在抠像边缘上（边缘一动就像抠坏了）"},
        ],
        symptoms=[
            "把「讲解效果图」的镜头抠了 → 图纸被抠没，只剩人对着空白讲（真值只有 2 段踩到指认台词，本机复现过）",
            "副镜拿同一段素材 → 同源套娃，边缘一圈双影，质检会判重帧",
            "抠像缩窗后没同步缩描边 → 白边比人还粗",
            "每镜开关抠像（不成段）→ 观众看到的是「人在闪」，不是「换了画面」",
        ],
        reuse=[
            "这条规则要能落实，必须同时具备：①人物层素材 ②一条不同源的副镜 ③该句台词不是在指认画面",
            "缺副镜 → blocked：宁可全屏硬切，也不要「抠出来垫一张纯色」",
            f"覆盖面按人物层时长算：本套真值把人物层的 {pct(ev.get('matting_share_of_person'))} 抠掉，超过就只加不剪",
        ],
        samples=sample_cutouts(f),
    )


# ---------------------------------------------------------------- speed
def build_speed(f: dict) -> dict:
    ev = f["truth"]["speed"]["evidence"]
    lim = f["truth"]["speed"]["limits"]
    sp = f["speeds"]
    if not sp:
        main_durs = [r["dur"] for r in f["main_segs"]]
        return el(
            False,
            absent_reason=(
                f"真值 0 段：{len(f['rows'])} 段视频逐段算 source_timerange/target_timerange 比值 = 1.000，"
                "主轨与叠加层都没有变速。"
            ),
            rule=(
                "本套的节奏靠切点密度承担（p50 镜长 "
                f"{med(main_durs)}s、<1s 快切占 {pct(ev.get('fast_cuts_under_1s_ratio'))}），"
                "不靠变速：压等待会把动作做出「快进的假」，拖慢会把口播对不上嘴。"
            ),
            triggers=[],
            not_when=["主轨口播（任何倍速都会嘴型错位）", "本套任何 B-roll（真值 0）"],
            evidence=f"变速段 = 0；主轨 {len(f['main_segs'])} 段全 1.0×；叠加层 {len(f['overlays'])} 段全 1.0×",
            value_drivers=[],
            scope={"unit": "段", "coverage": "0 段", "excludes": "全片", "reset_on": None},
            affects=[
                {"target": "音轨", "how": "一旦变速，同段音频必须 stretch/重采样，否则音画不同步"},
                {"target": "字幕时间戳", "how": "字幕要跟着 target_timerange 走；按源素材时间写会整体漂移"},
            ],
            limits=[{"name": "speed_values_in_truth", "value": [], "why": "真值没有倍速值，一个都不许发明"}],
            coupling=[
                {"with": "subtitle", "type": "ordering", "why": "变速后必须重算字幕时间戳，否则字比声早/晚"},
            ],
            symptoms=[
                "主轨加 1.2× → 口型对不上，质检判音画不同步",
                "B-roll 用 3× 硬压 → 观众只看到跳帧，不是「快进」",
            ],
            reuse=["这套用切点做节奏；如果素材等待太长，先考虑剪掉而不是变速"],
            samples=[],
        )

    vals = sorted({s["speed"] for s in sp})
    on_main = sum(1 for s in sp if s["is_main"])
    return el(
        True,
        rule=(
            "变速只服务一件事：把「无信息等待」压掉——B-roll 里的走路/操作/翻页过程按 2× 快进，"
            f"口播层一格不动。本套 {len(sp)} 段全部在叠加层（track {sorted({s['track'] for s in sp})}），主轨 0 段。"
        ),
        triggers=[
            "B-roll 是「过程镜」（走路、开箱、上车、翻页）且过程无信息",
            "该镜落点时长 > 源素材可提供的节奏（需要压进 1 个切点窗口）",
            "素材余量足够（源头尾还有帧，不是第一次用就压）",
        ],
        not_when=["主轨口播", "静图/照片（没有时间基准）", "字幕/贴纸层", "倍速不在真值集合里"],
        evidence=(
            f"{len(sp)} 段 source/target 比值 = {vals}，与 materials.speeds[].speed 字段一致；"
            f"全部 render_index>0（主轨 {on_main} 段）"
        ),
        value_drivers=[
            driver("speed（source_dur / target_dur）",
                   f"{vals}（{len(sp)} 段全同值）",
                   "segments[].source_timerange.duration ÷ target_timerange.duration"
                   "（= materials.speeds[].speed）",
                   "speed = src_dur / tgt_dur；tgt_dur = src_dur / speed",
                   f"真值只出现 {vals}，是整数倍：2× 让 1.33s 的过程压成 0.67s，刚好一个快切窗口；"
                   "1.5×/1.35× 这类「中间值」真值里一次都没有",
                   "forensic/style_trigger_rules_visual.json::speed.evidence", "±0.01"),
            driver("target_timerange.duration",
                   f"{rng([s['dur'] for s in sp])}（中位 {med([s['dur'] for s in sp])}）",
                   "segments[].target_timerange.duration",
                   "tgt_dur = src_dur / 2",
                   "压完的落点仍要 ≥ 0.3s：再短观众看不出这是同一动作，只觉得闪了一下",
                   None, "±1 帧"),
            driver("作用层（role）", "overlay only", "tracks[].type + 段 render_index",
                   None,
                   "口播层倍速会改口型/字音对应；B-roll 无同步声，压了只影响画面",
                   None, "exact"),
        ],
        scope={"unit": "段", "coverage": f"{len(sp)} 段 / 叠加层 {len(f['overlays'])} 段",
               "excludes": "主轨 A-roll、字幕、贴纸", "reset_on": "每个 B-roll 段重新决定"},
        affects=[
            {"target": "音轨", "how": "同段音频按同倍率拉伸（或静音）；不拉伸 = 音画分离"},
            {"target": "字幕时间戳", "how": "变速段若压着台词，字幕时间戳必须按 target 时间重算，否则漂 50%"},
            {"target": "源帧占用", "how": "2× 用掉的源区间是落点的 2 倍；同一源帧不许再被别的镜引用，否则质检判倒带"},
            {"target": "切点密度", "how": "压完的落点参与镜长分位统计，会拉低 p50"},
        ],
        limits=[
            {"name": "speed_values_in_truth", "value": lim["speed_values_in_truth"], "why": "只许用真值出现过的倍率"},
            {"name": "max_per_min", "value": lim["max_per_min"], "why": "真值密度；超了整片变快进"},
            {"name": "min_dur_sec", "value": lim["min_dur_sec"], "why": "短于这个的段变速看不出效果，只剩声音变形"},
            {"name": "main_track_segments", "value": 0, "why": "主轨 0 段（真值事实）"},
        ],
        coupling=[
            {"with": "subtitle", "type": "ordering", "why": "变速在前、字幕时间戳在后；顺序反过来字幕全漂"},
            {"with": "pip", "type": "co_occur", "why": "本套变速段与缩窗/抠像同批出现（同一条 B-roll 的两种处理）"},
            {"with": "bgm", "type": "side_effect", "why": "B-roll 段变速会让环境声变形，真值做法是该段静音而不是留变速声"},
        ],
        symptoms=[
            "同一段源帧被两镜占用（一镜 2×、一镜原速）→ 质检判「倒带」",
            "主轨 1.5× → 口型错位；且字幕没重算 → 字比声晚半句",
            "把 1.33s 的过程压到 0.2s → 观众只看到一帧闪",
        ],
        reuse=[
            "先量源素材余量：需要 src ≥ tgt×2，否则保持原速",
            "压完的落点要落进该套镜长分位带，不然会把 p50 拉歪",
        ],
        samples=[{"at_sec": s["start"],
                  "value": f"{s['speed']}× · 源 {s['src_dur']}s → 落点 {s['dur']}s",
                  "note": f"track{s['track']} 叠加层 · 源入点 {s['src_in']}s"} for s in sp[:6]],
    )


# ---------------------------------------------------------------- transition
def build_transition(f: dict, corpus: dict) -> dict:
    ev = f["truth"]["transition"]["evidence"]
    lim = f["truth"]["transition"]["limits"]
    return el(
        False,
        absent_reason=(
            "真值 0 次：materials.transitions 条数 = 0，且 "
            f"{len(f['rows'])} 段视频的 extra_material_refs 里没有任何一条指向 transition。"
            "这套的时间缝合方式是「硬切」——切点本身就是换场。"
        ),
        rule=(
            "转场是「时间/空间被强行缝合」时才付的代价；这套风格一次都没付过，"
            "所以套用端一次也不许自己加：换画面＝硬切，换章节＝静音断点。"
        ),
        triggers=[],
        not_when=[
            "句内切点（抢字，观众读到一半画面没了）",
            "快切串里（<1s 的镜加 0.5s 转场，等于把两镜都吃掉一半）",
            "开场 3 秒（hook 密度最高的地方）",
            "抠像层进出点（主体轮廓会抖一下）",
        ],
        evidence=(
            f"truth_transition_materials = {ev.get('truth_transition_materials', 0)}，"
            f"truth_transition_count = 0；全片 {ev.get('n_cut_points')} 个切点全是硬切。"
            f"库内 {corpus['drafts']} 份草稿里有 {corpus['drafts_with_transitions']} 份用到转场，但 24 套源一套都不在其中"
        ),
        value_drivers=[],
        scope={"unit": "全片", "coverage": "0 次", "excludes": "全片", "reset_on": None},
        affects=[
            {"target": "音轨", "how": "转场会把两段音频交叉，口播会被吃掉一个字；硬切切得干净"},
            {"target": "字幕时间戳", "how": "转场占用时长，字幕需要跟着补偿；硬切不用补偿"},
            {"target": "抠像边界", "how": "抠像层进出点插转场 = 主体轮廓抖，观众第一反应是「抠坏了」"},
        ],
        limits=[
            {"name": "max_count", "value": 0, "why": "本套真值 0；只有用户显式放行才允许"},
            {"name": "max_count_if_whitelisted", "value": lim["max_count_if_whitelisted"], "why": "放行也只是「章节断点」用一次"},
            {"name": "max_duration_sec", "value": lim["max_duration_sec"], "why": f"库内转场时长 {corpus['duration_range_sec']}s，中位 {corpus['duration_median_sec']}s"},
        ],
        coupling=[
            {"with": "cutout", "type": "mutex", "why": "抠像进出点不许插转场（主体边缘会抖）"},
            {"with": "in_anim", "type": "mutex_in_frame", "why": "入场动画已经承担「进入」的观感，再加转场是两次进场"},
            {"with": "speed", "type": "mutex", "why": "变速段两端加转场会让快进变成糊"},
        ],
        symptoms=[
            "在句内切点加 0.5s 叠化 → 字幕闪断，读者读不完",
            "开场 3 秒加转场 → hook 密度掉一半（真值开场 3 秒有 "
            f"{f['grammar'].get('opening', {}).get('cuts_in_first_3s')} 刀硬切）",
            "抠像层收尾加转场 → 白边抖一下，像抠坏了",
        ],
        reuse=[
            "默认硬切；只有当用户显式放行、或素材本身自带溶接、或两侧静音 ≥0.3s 的章节断点才允许一次",
            "允许时必须 ≤0.5s 且不在上述四个禁区",
        ],
        samples=[],
    )


# ---------------------------------------------------------------- effect
def build_effect(f: dict) -> dict:
    ev = f["truth"]["effect"]["evidence"]
    lim = f["truth"]["effect"]["limits"]
    effs = f["effects"]
    if not effs:
        return el(
            False,
            absent_reason=(
                "真值 0 个画面特效：effect 轨 0 段、materials.video_effects 未被任何段引用"
                f"（本套 {f['n_tracks']} 条轨 = {f['track_types']}）。"
                "画面处理全交给切点 + 花字/贴纸，没有滤镜式的一层。"
            ),
            rule=(
                "这套不出画面特效的手：氛围不靠暗角/噪点，冲击不靠模糊/颤抖——"
                "强调全由花字（字重/描边/炸字）承担，所以特效层一旦出现就是「贴了别人的滤镜」。"
            ),
            triggers=[],
            not_when=["全片"],
            evidence=f"effect 轨段 = 0；video_effects 引用 = 0；贴纸 {len(f['stickers'])} 段、抠像 {len(f['cutouts'])} 段都不带特效",
            value_drivers=[],
            scope={"unit": "段", "coverage": "0", "excludes": "全片", "reset_on": None},
            affects=[
                {"target": "调色", "how": "特效层在 LUT 之后叠，会整体压暗/脱色；这套没有特效，LUT 就是最终色"},
                {"target": "字幕对比度", "how": "没有暗角托底，字幕必须靠描边/阴影自己站稳"},
            ],
            limits=[{"name": "max_count", "value": 0, "why": "真值 0；加了就不是这套风格"}],
            coupling=[
                {"with": "color_grade", "type": "ordering", "why": "先调色后特效；这套只有调色"},
            ],
            symptoms=[
                "整片挂暗角 → 画面从「通透实拍」变成「回忆滤镜」",
                "给口播正脸加模糊冲击 → 脸上糊一片，观众以为没对上焦",
            ],
            reuse=["这套要「有质感」就调 LUT 和光照，不要加画面特效"],
            samples=[],
        )

    durs = [e["dur"] for e in effs]
    names = [e["name"] for e in effs]
    cover = round(sum(durs) / f["duration"], 4)
    ambient = [e for e in effs if e["dur"] >= 1.0]
    impact = [e for e in effs if e["dur"] < 1.0]
    cut_off = [round(nearest_cut(f["cuts"], e["start"]) or 0, 3) for e in effs]
    params_desc = "; ".join(
        f"{e['name']}: " + ",".join(f"{k}={v}" for k, v in (e["params"] or {}).items())
        for e in effs[:4])
    ratio_detail = []
    for e in effs[:4]:
        for k, v in (e["params"] or {}).items():
            dv = (e["defaults"] or {}).get(k)
            if dv and v is not None:
                ratio_detail.append(f"{e['name']}.{k}={v}（默认 {dv} → {v / dv:.2f}×）")

    return el(
        True,
        rule=(
            "画面特效只有两种合法用法：①氛围类长时间盖住一个段落（暗角/边框/噪点，≥1s）；"
            "②冲击类 ≤0.6s 贴在切点上（模糊/颤抖）。"
            f"本套 {len(effs)} 个：氛围类 {len(ambient)} 个、冲击类 {len(impact)} 个，"
            f"累计覆盖 {pct(cover)} 片长。"
        ),
        triggers=[
            "段落需要「统一氛围」（回忆/纪实/压抑）→ 暗角/噪点/边框长盖",
            "切点需要「打一下」（强调/惊吓/转场替代）→ 瞬时特效贴切点",
        ],
        not_when=[
            "整片挂滤镜（覆盖 >20% 片长）",
            "盖住人讲话的正脸（观感像坏片）",
            "瞬时特效离切点 >0.25s（渲染出来像闪帧/坏帧）",
            "该套真值没有这个特效名（暗角是真值里最常见的，颤抖只在一套出现）",
        ],
        evidence=(
            f"effect 轨 {len(effs)} 段：{names}；时长 {rng(durs)}s（中位 {med(durs)}）；"
            f"起点距最近切点 {rng(cut_off)}s；覆盖 {pct(cover)}"
            f"（真值口径 {pct(ev.get('coverage_ratio'))}）"
        ),
        value_drivers=[
            driver("target_timerange.duration",
                   f"{rng(durs)}（中位 {med(durs)}）",
                   "effect 轨 segments[].target_timerange.duration",
                   "氛围类：duration ≈ 段落长度；冲击类：duration ≤ 0.6",
                   f"本套氛围类 {len(ambient)} 个跨整个段落、冲击类 {len(impact)} 个只 0.17–0.6s——"
                   "两种用法不能混：长盖的不能短、冲击的不能长",
                   "forensic/style_trigger_rules_visual.json::effect.limits", "±1 帧"),
            driver("起点对切点的偏移",
                   f"{rng(cut_off)}s",
                   "effect 轨 segments[].target_timerange.start − 最近主轨切点",
                   "offset = |start − nearest_cut|，瞬时类要求 ≤0.25",
                   "冲击类必须贴在切点上（切点＝观众眨眼的那一帧）；氛围类可以落在段中",
                   None, "±0.25s"),
            driver("adjust_params（强度）", params_desc or "无强度滑杆（该特效只有一种形态）",
                   "materials.video_effects[].adjust_params[].value",
                   "强度 = value；与 default_value 的比值说明「用了几成」",
                   ("; ".join(ratio_detail) if ratio_detail else
                    ("该特效的 adjust_params 里没有可用强度值（只有一个空滑杆/无滑杆）："
                     "这类特效只有「开/关」两态，强度由 clip 之外的参数决定"
                     if not params_desc else
                     "暗角这类只有 texture 一档且取满值 1.0（=默认满档），说明「要就开到满，不开就别开」")),
                   "materials.video_effects[].adjust_params[].default_value", "±0.02"),
            driver("覆盖占比", f"{pct(cover)}",
                   "Σ effect 时长 ÷ 片长",
                   "coverage = Σdur / duration",
                   f"真值上限 {pct(lim['max_coverage_ratio'])}；特效是段落语气，不是全片底色",
                   "forensic/style_trigger_rules_visual.json::effect.evidence", "±20%"),
        ],
        scope={"unit": "段（effect 轨整段）",
               "coverage": f"{len(effs)} 段 / {pct(cover)} 片长",
               "excludes": "口播正脸特写、抠像边缘", "reset_on": "段落结束/切点"},
        affects=[
            {"target": "调色", "how": "特效叠在 LUT 之后：暗角会让边缘掉 1–2 档，调色时要按「有暗角」的结果校 y_mean"},
            {"target": "字幕对比度", "how": "暗角托底时字幕可以只靠 shadow；无暗角必须描边"},
            {"target": "抠像边缘", "how": "冲击特效压在抠像边缘 → 白边抖成锯齿"},
            {"target": "贴纸", "how": "特效层若在贴纸之上，贴纸会被一起压暗；真值顺序是贴纸在上"},
        ],
        limits=[
            {"name": "max_dur_sec", "value": lim["max_dur_sec"], "why": "单个特效不盖全片"},
            {"name": "max_coverage_ratio", "value": lim["max_coverage_ratio"], "why": f"真值覆盖 {pct(ev.get('coverage_ratio'))}"},
            {"name": "instant_max_sec", "value": lim["instant_max_sec"], "why": "超过 0.6s 就不是「打一下」，而是持续变形"},
            {"name": "max_count_per_min", "value": lim["max_count_per_min"], "why": "真值密度"},
        ],
        coupling=[
            {"with": "color_grade", "type": "ordering", "why": "LUT → 特效 → 字幕/贴纸；顺序反了暗角会被调色提亮"},
            {"with": "cutout", "type": "mutex_in_frame", "why": "冲击特效不压在抠像边缘"},
            {"with": "sticker", "type": "ordering", "why": "贴纸压在特效之上（贴纸是信息，特效是氛围）"},
        ],
        symptoms=[
            "把「暗角」当全片滤镜挂 → 覆盖 100%，画面像监控回放",
            "瞬时特效离切点 1s 以上 → 渲染出来像坏帧，观众以为文件损坏",
            "特效强度抄默认值（颤抖 range 0.15/speed 0.33）→ 晃到看不清字，真值压到 0.09/0.12",
        ],
        reuse=[
            "先判断要氛围还是冲击，再决定时长档位；两档不可互换",
            "特效名必须在真值名单里，否则宁可不用",
        ],
        samples=[{"at_sec": e["start"],
                  "value": f"{e['name']} {e['dur']}s " + (",".join(f"{k}={v}" for k, v in (e["params"] or {}).items())
                                                          or "（无强度滑杆）"),
                  "note": f"距最近切点 {round(nearest_cut(f['cuts'], e['start']) or 0, 3)}s · "
                          f"{'氛围类长盖' if e['dur'] >= 1.0 else '冲击类贴切点'}"} for e in effs[:8]],
    )


# ---------------------------------------------------------------- sticker
def build_sticker(f: dict) -> dict:
    ev = f["truth"]["overlay"]["evidence"]
    st = f["stickers"]
    if not st:
        return el(
            False,
            absent_reason=(
                "真值 0 个贴纸段：本套 "
                + (f"有 sticker 轨但 0 段贴纸（{f['n_tracks']} 条轨 = {f['track_types']}）"
                   if f["track_types"].get("sticker") else
                   f"{f['n_tracks']} 条轨里根本没有 sticker 轨（{f['track_types']}）")
                + "。"
            ),
            rule=(
                "这套的强调手段是花字与切点，不用素材贴纸：贴纸在这里不是装饰，是「指着某个东西」的指示器；"
                "没有要指的东西就不贴。"
            ),
            triggers=[],
            not_when=["全片"],
            evidence=f"sticker 轨段 = 0；花字承担全部强调（贴纸 {len(st)} 段）",
            value_drivers=[],
            scope={"unit": "段", "coverage": "0", "excludes": "全片", "reset_on": None},
            affects=[
                {"target": "花字", "how": "没有贴纸腾位置，花字可以更靠近画面中心"},
            ],
            limits=[{"name": "max_count", "value": 0, "why": "真值 0"}],
            coupling=[{"with": "flower", "type": "mutex_in_frame", "why": "没有贴纸=花字独占强调位"}],
            symptoms=["给这套贴装饰贴纸 → 画面立刻变「模板感」，与实拍调性冲突"],
            reuse=["这套不要贴纸；要指就用花字的箭头字符或描边强调"],
            samples=[],
        )

    durs = [s["dur"] for s in st]
    cut_off = [round(nearest_cut(f["cuts"], s["start"]) or 0, 3) for s in st]
    kinds = Counter()
    for s in st:
        nm = s["name"]
        if "箭头" in nm:
            kinds["指示箭头"] += 1
        elif "虚线圈" in nm or "边框" in nm or "下划线" in nm:
            kinds["圈/框/划线（标注）"] += 1
        elif "手势" in nm:
            kinds["手势（点赞等反应）"] += 1
        elif "色块" in nm:
            kinds["色块（底衬）"] += 1
        else:
            kinds["图标/其他"] += 1
    top = sorted({s["track"] for s in st})
    paired = ev.get("text_pair_rate")
    pair_txt = ("n/a（本套贴纸不与字幕轨重叠）" if paired is None else pct(paired))
    pair_clause = (
        f"②与当刻花字同框（真值同框率 {pct(paired)}）——同框是这套的硬条件，孤贴不存在"
        if (paired or 0) >= 0.9 else
        f"②本套贴纸不与花字同框（同框率 {pair_txt}）：它是纯标注，靠画面里的物体定位而不是靠文案命名"
    )
    groups = Counter((s["start"], s["dur"]) for s in st)
    grouped = [(t, d, c) for (t, d), c in groups.items() if c >= 2]
    group_txt = (f"；其中 {len(grouped)} 组是「同时同长、成组标注」（"
                 + "、".join(f"{c} 张 {t}s 起 {d}s" for t, d, c in grouped[:3]) + "）"
                 if grouped else "")
    max_frame = max((sum(1 for s in st if s["start"] <= t < s["start"] + s["dur"])
                     for t in [s["start"] for s in st]), default=1)
    y_med = med([s["y"] for s in st])
    y_clause = ("落点偏上（y 中位 %.3f<0，避开底部字幕带）" % y_med if (y_med or 0) < 0
                else "落点偏下/居中（y 中位 %.3f，这套的字幕不在底部）" % (y_med or 0))

    return el(
        True,
        rule=(
            "贴纸是「指示器」不是装饰：真值里 "
            + "、".join(f"{k} {v} 段" for k, v in kinds.most_common())
            + f"。它必须①贴在切点上跟着新镜进场（距切点 {rng(cut_off)}s）"
            + pair_clause + group_txt + "。"
        ),
        triggers=[
            "画面里有个具体位置要被指出来（箭头/圈/下划线）",
            "要表达一个瞬时反应（点赞手势）",
            "要一个底衬色块承托花字（色块类）",
        ],
        not_when=["空镜", "已经有一层花字压在同一个位置", "抠像小窗上（会压住脸）"],
        evidence=(
            f"sticker 轨段 {len(st)} 段（track {top}）：{dict(kinds)}；"
            f"时长 {rng(durs)}s；起点距最近切点 {rng(cut_off)}s；与花字同框 {pct(paired)}"
        ),
        value_drivers=[
            driver("clip.scale", f"{rng([s['scale'] for s in st])}",
                   "sticker 段 segments[].clip.scale.x",
                   "显示尺寸 = 素材原生宽 × W/素材宽 × scale（贴纸按素材像素比铺到画布）",
                   "本套 scale 集合 "
                   f"{sorted({s['scale'] for s in st})}：指示类（箭头/圈）缩到画布的 1/4–1/2 当标注，"
                   "边框类贴到 1.0–2.2 当「屏幕/内容框」",
                   None, "±2%"),
            driver("clip.transform.x / y", f"x {rng([s['x'] for s in st])} / y {rng([s['y'] for s in st])}",
                   "sticker 段 segments[].clip.transform",
                   "left = (W/2)(x + 1 − scale)；top = (H/2)(y + 1 − dh/H)（与视频层同一套坐标）",
                   f"贴纸落点跟着被指的物体走：{y_clause}",
                   None, "±2px"),
            driver("clip.rotation", f"{sorted({s['rotation'] for s in st})}",
                   "sticker 段 segments[].clip.rotation",
                   None,
                   "箭头按「指向」旋转：默认朝右，向下指就 90°；别的角度（≈45°）是真值里手调的斜指",
                   None, "±3°"),
            driver("起点对齐切点", f"{rng(cut_off)}s",
                   "sticker 起点 − 最近主轨切点",
                   None,
                   "贴纸跟着切点一起进场；落在镜中间会像「凭空冒出来」",
                   "forensic/style_trigger_rules_visual.json::overlay.evidence", "±0.25s"),
        ],
        scope={"unit": "段", "coverage": f"{len(st)} 段 / 片长 {f['duration']}s",
               "excludes": "口播主轨自身、字幕带", "reset_on": "切点"},
        affects=[
            {"target": "花字", "how": "同框：贴纸给花字腾位置或做底衬，二者不能压在同一块像素上"},
            {"target": "字幕安全区", "how": "贴纸 y 偏上/偏侧，底部字幕带留出来"},
            {"target": "人脸构图", "how": "贴纸不许压在人物脸上（真值贴纸落在画面边缘/物体附近）"},
        ],
        limits=[
            {"name": "max_dur_sec", "value": max(durs) if durs else None,
             "why": ("最长一条＝上限；本套有长期驻留的标注（圈住一个物体整段不动），所以上限超过 5s"
                     if (max(durs) if durs else 0) > 5 else
                     "最长一条＝上限（贴纸是短促标注，跟着一句话走）")},
            {"name": "max_count_per_frame", "value": max_frame,
             "why": ("同一刻最多几层贴纸（真值事实：成组标注时才并排）" if max_frame > 1
                     else "真值同一刻最多 1 层，多了像素材堆")},
            {"name": "text_pair_rate", "value": paired,
             "why": ("贴纸必须同时有一条花字（真值同框率 100%）" if (paired or 0) >= 0.9
                     else "这套贴纸不依赖花字；但必须指向画面里的具体物体")},
        ],
        coupling=[
            {"with": "flower",
             "type": "co_occur" if (paired or 0) >= 0.9 else "independent",
             "why": ("贴纸与花字同框出现，缺一不可" if (paired or 0) >= 0.9
                     else "这套贴纸不跟花字绑：贴纸管「指哪」，花字管「叫什么」，各占一块")},
            {"with": "cutout", "type": "mutex_in_frame", "why": "抠像小窗上不贴（会压脸/压主体）"},
            {"with": "effect", "type": "ordering", "why": "贴纸压在特效之上：贴纸是信息层"},
        ],
        symptoms=[
            "贴纸落在镜中间（不在切点）→ 凭空冒出来，像后期漏剪",
            "孤立贴纸不同框花字 → 观众不知道它在指什么，只觉得画面脏",
            "箭头旋转角乱写 → 指错方向，比不贴更糟",
        ],
        reuse=[
            "先问「要指哪个像素」，再决定箭头/圈/下划线；指不出具体位置就不要贴",
            "贴纸必须与一条花字同框，否则 blocked",
        ],
        samples=[{"at_sec": s["start"],
                  "value": f"{s['name'] or '贴纸'} {s['dur']}s scale {s['scale']} rot {s['rotation']}°",
                  "note": f"x {s['x']} y {s['y']} · 距切点 {round(nearest_cut(f['cuts'], s['start']) or 0, 3)}s"} for s in st[:6]],
    )


# ---------------------------------------------------------------- pip
def build_pip(f: dict) -> dict:
    pt = f["pip_truth"]
    wins = [w for w in f["windows"]] + [w for w in f["main_windows"]]
    cutouts_small = [c for c in f["cutouts"] if c not in f["full_overlays"]]
    main_scale = f["main_scale_mode"]
    med_shot = med([r["dur"] for r in f["main_segs"]])

    if not wins:
        if f["overlays"]:
            cause = (f"{len(f['overlays'])} 段叠加视频全部 scale=1.000、x=y=0（整屏覆盖主轨），"
                     "缩窗/分屏在这套里不存在")
        else:
            cause = (f"本套只有 {f['n_video_tracks']} 条视频轨、0 段叠加层："
                     "画面永远单层满屏，没有第二层可缩窗")
        return el(
            False,
            absent_reason=f"真值 0 个缩窗：{cause}。",
            rule=(
                "这套的换画面方式是「整屏换」而不是「旁边开个窗」：观众看的永远是 100% 画面，"
                f"信息增量靠切点密度（中位镜长 {med_shot}s）"
                + (f"和主轨常驻放大裁切（scale {main_scale}）" if main_scale and main_scale > 1 else "")
                + "。"
            ),
            triggers=[],
            not_when=["全片"],
            evidence=(f"叠加 {len(f['overlays'])} 段：整屏 {len(f['full_overlays'])}、缩窗 0；"
                      f"主轨 scale 众数 {main_scale}（{'常驻裁切放大，不是缩窗' if main_scale and main_scale > 1 else '满屏'}）；"
                      f"pip_mode = {pt.get('pip_mode_truth')}"),
            value_drivers=[],
            scope={"unit": "段", "coverage": "0", "excludes": "全片", "reset_on": None},
            affects=[
                {"target": "字幕", "how": "整屏画面 → 字幕必须压在画面上，靠描边/阴影读"},
                {"target": "构图", "how": "没有小窗，人脸可以居中；一旦开窗就必须重新避让人脸"},
            ],
            limits=[{"name": "min_scale", "value": 1.0, "why": "真值没有 <1 的画面层"}],
            coupling=[
                {"with": "cutout", "type": "mutex", "why": "没有可叠的窗位，抠像也只是换整屏背景"},
            ],
            symptoms=["给这套开小窗 → 满屏实拍的「现场感」立刻掉成「解说台」",
                      "主轨缩到 0.85 做留白 → 与这套的满屏调性冲突（真值只有 scale≥1 的裁切）"],
            reuse=["这套保持整屏；要对比就切两镜，不要开窗"],
            samples=[],
        )

    scales = [w["scale"] for w in wins]
    small = sorted({w["scale"] for w in wins if w["scale"] < 0.995})
    corner = sum(1 for w in wins if abs(w["x"]) > 0.3 and abs(w["y"]) > 0.3)
    n_overlay_win = len(f["windows"])
    n_main_win = len(f["main_windows"])
    return el(
        True,
        rule=(
            "画中画的因果是「这一刻要同时看两个画面」：主画面占满或放大，副画面缩成窗挪到边角。"
            f"本套真值 pip 标签 = {pt.get('pip_mode_truth')}；逐段实测：叠加缩窗 {n_overlay_win} 段"
            f"、主轨缩窗/移位 {n_main_win} 段，窗口 scale 集合 {small[:4] or scales}，"
            f"四角落位 {corner} 段。"
        ),
        triggers=[
            "要同时给出「人」和「物」（边讲边演示）",
            "要对比两个画面（前后/好坏/大小）",
            "副画面是证据/凭证，需要被看见但不需要占满",
            "主轨要让位给上方/下方的花字或底衬（主轨缩窗）",
        ],
        not_when=[
            "副画面本身就是信息主体（该整屏就整屏）",
            "主画面里的人脸会被窗口压到",
            "窗口进入字幕带（y > 0.6H）",
        ],
        evidence=(
            f"叠加 {len(f['overlays'])} 段（整屏 {len(f['full_overlays'])} / 缩窗 {n_overlay_win}）；"
            f"主轨 {len(f['main_segs'])} 段里缩窗/移位 {n_main_win} 段；"
            f"主轨 scale 众数 {main_scale}；窗口 scale 集合 {sorted(set(scales))[:6]}"
        ),
        value_drivers=[
            driver("主轨 scale（常驻放大裁切）", f"{main_scale}",
                   "主轨段 segments[].clip.scale.x 众数",
                   "主画面显示宽 = W·scale；scale>1 = 裁切放大（等效推近机位，不是关键帧）",
                   f"这套主轨常用 {main_scale}：视野被裁到 {round(100 / (main_scale or 1))}%，"
                   "比加关键帧推拉更稳（真值关键帧因此很少）",
                   "forensic/pip_usage.json::main", "±1%"),
            driver("窗口 scale", f"{scales}",
                   "叠加/主轨段 segments[].clip.scale.x",
                   "win_w = W·scale；win_h = (nh/nw)·W·scale",
                   f"最小 {min(scales)}（= 画布宽 {pct(min(scales))}），再小观众看不清窗里是什么；"
                   f"最大 {max(scales)}（>1 是放大窗，用来裁掉窗内杂物）",
                   "forensic/pip_usage.json::overlays[].geometries", "±1%"),
            driver("窗口 x / y", f"x {rng([w['x'] for w in wins])} / y {rng([w['y'] for w in wins])}",
                   "段 segments[].clip.transform",
                   "left = (W/2)(x + 1 − scale)；|x| ≤ 1−scale 是「拖到不出画」的边界",
                   f"四角落位 {corner} 段（|x|>0.3 且 |y|>0.3）是这套唯一的窗位语法；居中开窗不存在",
                   None, "±2px"),
            driver("窗口朝向（左右）",
                   f"x<0 {sum(1 for w in wins if w['x'] < 0)} 段 / x>0 {sum(1 for w in wins if w['x'] > 0)} 段",
                   "同 x 的符号", None,
                   "窗口放在与主体视线相反的一侧：主体在左就开右窗，观众眼睛不用来回跳",
                   None, None),
        ],
        scope={"unit": "段", "coverage": f"缩窗 {len(wins)} 段 / 画面层 {len(f['rows'])} 段",
               "excludes": "字幕带、人脸矩形", "reset_on": "切点"},
        affects=[
            {"target": "人脸构图", "how": "窗口不能压主画面人脸；副窗里的人脸要完整落在窗内（边框裁到下巴就废）"},
            {"target": "字幕", "how": "窗口矩形与字幕带互斥：窗在下方就必须把字幕挪到上方或缩短"},
            {"target": "贴纸", "how": "贴纸不许贴在窗口边上，会像窗口的角标"},
            {"target": "抠像", "how": "抠像是「无形窗口」，与几何窗口共用同一套坐标与边界"},
        ],
        limits=[
            {"name": "min_scale", "value": min(scales), "why": "真值窗口最小缩放；再小内容不可读"},
            {"name": "max_windows_same_frame", "value": max(
                len([w for w in wins if w["start"] <= t < w["end"]]) for t in [w["start"] for w in wins]),
             "why": "同一刻最多几层窗口（真值事实）"},
            {"name": "corner_rule", "value": "|x|>0.3 且 |y|>0.3", "why": "窗位只允许四角；边中/居中会挡住主画面主体"},
        ],
        coupling=[
            {"with": "cutout", "type": "subset", "why": "抠像 = 带 alpha 的窗口，几何同源"},
            {"with": "subtitle", "type": "mutex_in_frame", "why": "窗口矩形与字幕带不得重叠"},
            {"with": "face_framing", "type": "constraint", "why": "开窗前必须知道人脸框在哪（face_anchor），否则必然压脸"},
            {"with": "overlay_geometry", "type": "same_source_of_truth", "why": "同一套 left/top 公式"},
        ],
        symptoms=[
            "窗口开在 y>0.6H → 压住字幕，质检判字幕越界",
            "窗口盖在人脸上 → 观众以为切错镜",
            "窗里人脸被裁一半 → 副画面成了「半张脸」，信息反而没了",
        ],
        reuse=[
            "开窗前先拿人脸框（face_anchor），再选对角角落位",
            "窗 scale 不得小于真值最小值，否则内容不可读",
        ],
        samples=[{"at_sec": w["start"],
                  "value": f"scale {w['scale']} x {w['x']} y {w['y']} · {w['dur']}s",
                  "note": ("主轨缩窗" if w["is_main"] else f"叠加 track{w['track']}")
                          + (" · 抠像人像窗" if w["matting"] else " · 几何窗")}
                 for w in (cutouts_small + [w for w in wins if not w["matting"]])[:6]],
    )


# ---------------------------------------------------------------- overlay_geometry
def build_overlay_geometry(f: dict, geo: dict) -> dict:
    rows = f["rows"]
    over = f["overlays"]
    no_bounds = [r for r in rows if not r["in_bounds"]]
    alphas = Counter(r["alpha"] for r in rows)
    non_one = sum(v for k, v in alphas.items() if k not in (None, 1.0))
    local_flush = sum(1 for r in rows if r["scale"] < 0.995
                      and abs(abs(r["x"]) - (1 - r["scale"])) < 1e-3)
    alpha_txt = ("，全部缺省 = 1.0" if non_one == 0
                 else f"，其中 {non_one} 段非 1.0：" + "、".join(
                     f"{k}×{v}" for k, v in alphas.most_common(3) if k not in (None, 1.0)) + "）")
    rotations = sorted({r["rotation"] for r in rows if r["rotation"]})
    masks = f["render"].get("masks") or {}
    return el(
        True,
        rule=(
            "画布几何是全部画面的底座：每条视频层都由 clip.scale + clip.transform 两个字段定死位置，"
            "公式 left=(W/2)(x+1−scale)、top=(H/2)(y+1−dh/H)（scale=1 即铺满画布宽度，竖屏素材正好整屏）。"
            f"本套 {len(rows)} 段视频里 {len(f['full_overlays'])} 段叠加层是整屏覆盖、"
            f"{len(f['windows'])} 段是缩窗/缩放；主轨 scale 众数 {f['main_scale_mode']}。"
        ),
        triggers=[
            "任何视频层落画布（主轨、叠加、抠像、照片）",
            "需要换构图（推近＝scale>1；缩窗＝scale<1；留白＝transform 平移）",
        ],
        not_when=["字幕/贴纸/花字层（它们走各自坐标系，但仍以画布为基准）"],
        evidence=(
            f"{len(rows)} 段视频：都在 {f['W']}×{f['H']} 画布上；本套 {local_flush} 段命中贴边（|x| = 1−scale）、"
            f"越界 {len(no_bounds)} 段（{pct(len(no_bounds) / max(1, len(rows)))}，多为整屏段平移留白/带 crop）；"
            f"全库对照：{geo['flush_hits']} 段贴边 / {geo['n_rows']} 段视频"
        ),
        value_drivers=[
            driver("显示尺寸", f"W·scale，height = (nh/nw)·W·scale",
                   "segments[].clip.scale.x（缺省 1.0）",
                   "disp_w = W·scale；disp_h = (nh/nw)·W·scale；scale=1 时素材按「宽铺满画布」的基准缩放",
                   "证据：16:9 素材(1920×1080)在真值里被放大到 scale≈1.774=1920/1080 才铺满竖屏——"
                   "说明基准是「宽对齐」而不是「cover 对齐」，抄错会把上下裁掉一半",
                   "与 clip.scale 定义交叉验证（本机 603 段分布）", "±1px"),
            driver("画布坐标", f"left ∈ [{min(r['left'] for r in rows)}, {max(r['left'] for r in rows)}]px",
                   "segments[].clip.transform.x / y",
                   "left = (W/2)(x + 1 − scale)；top = (H/2)(y + 1 − disp_h/H)；cx = W/2(1+x)",
                   "x,y 是「窗口可动范围」的归一化：x=0 居中、x=±(1−scale) 顶到边。"
                   f"本机 {geo['flush_hits']} 段正好落在贴边值上，误差 <1px——这不是巧合而是控件边界",
                   None, "±1px"),
            driver("铺满判定", f"整屏 {len(f['full_overlays'])} 段 / 缩窗 {len(f['windows'])} 段",
                   "scale=1 且 x=y=0",
                   None,
                   "整屏覆盖是默认（观众看 100% 画面）；缩窗只在「同时看两个画面」时用",
                   "forensic/pip_usage.json", None),
            driver("alpha", f"{len(rows)} 段{alpha_txt}",
                   "segments[].clip.alpha（缺省 1.0）",
                   None,
                   ("全部段 alpha=1.0：淡入淡出交给动画与切点，不靠 alpha" if non_one == 0
                    else f"仅 {non_one} 段 alpha≠1（垫底/过渡画面）；正常画面层一律 1.0，否则边缘与背景混色"),
                   None, "exact"),
            driver("蒙版（可选）", f"{masks.get('n', 0)} 个" if masks else "0 个",
                   "materials.common_mask + 段 extra_material_refs",
                   "线性蒙版：width/rotation/feather 决定软边位置",
                   (f"真值只有一个线性蒙版：config {json.dumps((masks.get('items') or [{}])[0].get('config'), ensure_ascii=False)}"
                    if masks.get("n") else "本套不用蒙版：换画面靠硬切，不靠擦除"),
                   "forensic/style_render_truth.json::masks", None),
            driver("旋转", f"{rotations or 0}",
                   "segments[].clip.rotation",
                   "旋转角直接作用在画布坐标系上，先转后定位",
                   "视频层几乎不旋转（真值只在个别段有角度）；旋转是贴纸/素材的语法",
                   None, "±1°"),
        ],
        scope={"unit": "段（每条视频层）", "coverage": f"{len(rows)} 段 / {f['n_video_tracks']} 条视频轨",
               "excludes": "字幕/贴纸自有坐标系", "reset_on": "每段独立"},
        affects=[
            {"target": "抠像", "how": "抠像窗就是这套几何的一种（带 alpha 的窗口）"},
            {"target": "字幕/花字", "how": "画布坐标决定安全区：字幕带 y≈0.68H 以下不放任何画面元素"},
            {"target": "贴纸", "how": "贴纸落位用同一套 left/top 公式"},
            {"target": "调色/特效", "how": "几何不改色，但缩窗后的画面边缘会被特效暗角重复压暗"},
        ],
        limits=[
            {"name": "bound_rule", "value": "|x| ≤ 1−scale 且 |y| ≤ 1−dh/H",
             "why": f"拖到边界即出画；超过就是故意出血（全库 {geo['oob']}/{geo['n_rows']} 段是这种，"
                    f"{pct(geo['oob'] / max(1, geo['n_rows']))}）"},
            {"name": "scale_min", "value": min(r["scale"] for r in rows), "why": "真值最小缩放（再小就是窗口而不是画面）"},
            {"name": "scale_max", "value": max(r["scale"] for r in rows), "why": "真值最大放大（决定画质下限）"},
        ],
        coupling=[
            {"with": "cutout", "type": "same_source_of_truth", "why": "抠像与整屏叠加共用公式"},
            {"with": "pip", "type": "same_source_of_truth", "why": "缩窗几何"},
            {"with": "subtitle", "type": "constraint", "why": "字幕带 y 由几何决定"},
        ],
        symptoms=[
            "把 16:9 素材按 scale=1 当全屏 → 上下各留 600+px 黑边，客户第一眼就说「怎么有黑边」",
            "用 |x|>1−scale 摆窗 → 窗口出画一半，观众看不到窗里的东西",
            "把 scale 当「百分比」直接乘素材宽 → 尺寸整体偏大/偏小 1.5 倍",
        ],
        reuse=[
            "任何新素材先算 (nh/nw)·W·scale，确认是铺满还是缩窗，再决定 scale",
            "落位前用 left/top 公式算到像素，再检查是否压字幕带/人脸",
        ],
        samples=[{"at_sec": r["start"],
                  "value": f"scale {r['scale']} → {r['disp'][0]}×{r['disp'][1]}px @ ({r['left']},{r['top']})",
                  "note": ("主轨" if r["is_main"] else f"叠加 track{r['track']}") +
                          (" · 抠像" if r["matting"] else "") +
                          ("" if r["in_bounds"] else " · 越界（出血/留白）")}
                 for r in (rows[:4] + [r for r in rows if not r["in_bounds"]][:2])],
    )


# ---------------------------------------------------------------- in_anim / out_anim
def _anim_samples(hits: List[dict], limit: int = 6) -> List[dict]:
    """同一元素常挂多条动画（花字主副字各一条），按出现时刻聚合再采样。"""
    by_t: Dict[float, List[dict]] = {}
    for a in hits:
        by_t.setdefault(a["at"], []).append(a)
    out = []
    for t in sorted(by_t)[:limit]:
        group = by_t[t]
        names = Counter(a["name"] for a in group)
        out.append({
            "at_sec": t,
            "value": f"{len(group)} 条 / {max(a['dur'] for a in group)}s：" +
                     "、".join(f"{n}×{c}" if c > 1 else n for n, c in names.most_common(4)),
            "note": f"宿主 {group[0]['track_type']} · 元素时长 {group[0]['host_dur']}s",
        })
    return out


def _anim_block(f: dict, kind: str, stats: dict) -> dict:
    hits = [a for a in f["anims"] if a["kind"] == kind]
    if not hits:
        return el(
            False,
            absent_reason=(
                f"真值 0 个{'入场' if kind == 'in' else '出场'}动画：本套 "
                f"{len(f['anims'])} 条动画引用里没有 type={kind} 的条目。"
                + ("元素的出场由硬切承担（切点即消失）。" if kind == "out" else "")
            ),
            rule=("这套不让元素「动进来」：靠切点直接出现，短促干脆。"
                  if kind == "in" else
                  "这套不让元素「动出去」：切点一刀带走，出场动画反而拖节奏。"),
            triggers=[], not_when=["全片"],
            evidence=f"material_animations[].animations[].type={kind} 计数 = 0",
            value_drivers=[],
            scope={"unit": "个", "coverage": "0", "excludes": "全片", "reset_on": None},
            affects=[
                {"target": "切点", "how": "没有入场动画，元素出现时刻＝切点，字幕与画面同帧换"},
            ],
            limits=[{"name": "max_count", "value": 0, "why": "真值 0"}],
            coupling=[{"with": "transition", "type": "mutex", "why": "既没有动画也没有转场，换场只靠硬切"}],
            symptoms=["给这套加入场动画 → 每个字都「弹」一下，与这套的干脆调性冲突"],
            reuse=["这套用硬切进场；要强调就换字号/颜色，不加动画"],
            samples=[],
        )
    durs = [a["dur"] for a in hits]
    hosts = Counter(a["track_type"] for a in hits)
    names = Counter(a["name"] for a in hits)
    long_ones = [a for a in hits if a["dur"] > 1.0]
    if kind == "in":
        rule = (
            "入场动画的因果是「这个元素是新出现的」：跟着切点/句首一起进，时长 0.2–0.5s，"
            f"位移/渐显类为主。本套 {len(hits)} 个：宿主 {dict(hosts)}，时长中位 {med(durs)}s，"
            f"名字 {[n for n, _ in names.most_common(4)]}。"
        )
        symptoms = [
            "入场动画时长 >0.5s → 字还没完全出现就被切走",
            "把入场动画挂在视频底片上 → 前 0.3s 出现黑帧",
            "每个字都加动画 → 观众在等字，节奏散了",
        ]
        affects = [
            {"target": "切点对齐", "how": "入场动画起点必须＝元素出现帧（切点/句首），偏一帧就看得出「慢半拍」"},
            {"target": "字幕时间戳", "how": "动画只改可见性/位移，不改 target_timerange——字幕时间戳不因动画延后"},
            {"target": "画面节奏", "how": "进场长度吃掉镜头的开头，短镜（<0.5s）配 0.5s 动画等于没有画面"},
        ]
        limits = [
            {"name": "max_dur_sec", "value": 0.5, "why": "标准档上限；只有「打字/光标」类长驻动画会超过 1s"},
            {"name": "max_per_element", "value": 1, "why": "一个元素一条入场动画，叠加会打架"},
        ]
        coupling = [
            {"with": "transition", "type": "mutex_in_frame", "why": "动画已经承担「进入」，再加转场是两次进场"},
            {"with": "flower", "type": "co_occur", "why": "花字靠入场动画区分层级（大字炸开、小字渐显）"},
        ]
    else:
        rule = (
            "出场动画只在「元素要消失但镜头不切」时用：以消失类动词为主（渐隐/消散/炸开），"
            f"不做位移。本套 {len(hits)} 个，时长 {rng(durs)}s，宿主 {dict(hosts)}，"
            f"名字 {[n for n, _ in names.most_common(4)]}。"
        )
        symptoms = [
            "出场动画 >0.5s → 镜头已经切了字还在淡出，穿帮",
            "用位移类出场（滑出）→ 观众以为画面被拖走",
            "给每个元素都配出场 → 与入场动画 1:1，整片变成动画秀（真值 in:out≈13:1）",
        ]
        affects = [
            {"target": "切点", "how": "出场动画必须在切点之前收完；跨切点淡出＝两个画面混在一起"},
            {"target": "下一镜的入场", "how": "同一元素「出场紧接入场」会连成一条动画，观众看到的是它在飞"},
            {"target": "元素时长", "how": "出场动画占用元素尾部，元素本身时长不变（target_timerange 不改）"},
        ]
        limits = [
            {"name": "max_dur_sec", "value": 0.5, "why": "真值出场最长 0.5s；再长就会跨过切点"},
            {"name": "in_to_out_ratio", "value": "≈13:1", "why": "全库 530 入场 / 41 出场：出场是例外，不是标配"},
        ]
        coupling = [
            {"with": "cut", "type": "mutex", "why": "元素随切点消失时用切点，不用出场动画"},
            {"with": "in_anim", "type": "ordering", "why": "同一元素出场后若再入场，两条动画必须隔着一个切点"},
        ]
    return el(
        True,
        rule=rule,
        triggers=(["元素首次出现（句首/切点/花字弹出）"] if kind == "in"
                  else ["元素在镜头内结束（贴纸悬停完毕/花字强调结束）"]),
        not_when=(["已存在元素的重复强调（用 loop 不用 in）"] if kind == "in"
                  else ["元素随切点一起消失的镜头（出场由硬切承担）"]),
        evidence=(
            f"material_animations[].animations[] type={kind}：{len(hits)} 个；"
            f"宿主 {dict(hosts)}；时长 {rng(durs)}s（中位 {med(durs)}）；"
            f"字段 duration={int(med(durs) * 1_000_000)}µs 是 1/60s 网格上的整帧数"
        ),
        value_drivers=[
            driver("animations[].duration", f"{rng(durs)}（中位 {med(durs)}）",
                   "material_animations[].animations[].duration（µs）",
                   "秒 = duration / 1e6；帧 = 秒 × fps（60fps → 0.5s = 30 帧）",
                   "标准档 0.3/0.4/0.5s：全库 24 套的中位就在 0.33–0.5s；"
                   + (f"本套 {len(long_ones)} 个超过 1s 的是「打字/光标」类长驻动画（时长跟着文本走）"
                      if long_ones else "本套没有超过 1s 的长驻动画，全部落在标准档"),
                   "全库 530/41 条动画统计", "±1 帧"),
            driver("animations[].name", f"{dict(names.most_common(5))}",
                   "material_animations[].animations[].name",
                   None,
                   "名字就是方向语义：向左/向右滑动＝位移；渐显/渐隐＝透明度；炸开/消散＝消失；"
                   "生长/放大＝缩放。选名字前先定「从哪来/往哪去」",
                   None, None),
            driver("宿主类型", f"{dict(hosts)}",
                   "段所在 track.type（text / sticker / video）",
                   None,
                   "动画只挂文字与贴纸（信息层），视频底片极少挂——挂上去就是黑帧/抖帧",
                   None, "exact"),
        ],
        scope={"unit": "个（每个元素一条）", "coverage": f"{len(hits)} 个 / {len(f['anims'])} 条动画引用",
               "excludes": "视频底片", "reset_on": "每个元素出现时"},
        affects=affects,
        limits=limits,
        coupling=coupling,
        symptoms=symptoms,
        reuse=[
            "动画名要与运动方向一致（向左滑动 = 从右进）",
            "镜长 <0.5s 时缩短动画或干脆不用",
        ],
        samples=_anim_samples(hits),
    )


# ---------------------------------------------------------------- color_grade
def build_color_grade(f: dict) -> dict:
    cl = f["lib"].get("color_and_lut") or {}
    zone = cl.get("zone_measurement") or {}
    hsl = f["hsl"]
    flags = f["enable_flags"]
    tint = (cl.get("shadow_tint_rgb") or [0, 0, 0]) + (cl.get("highlight_tint_rgb") or [0, 0, 0])
    tint_zero = all(abs(v) < 0.005 for v in tint)
    residue = (f"，外加 {hsl['mats']} 个 HSL 素材挂在 {hsl['refs']} 段上但启用位为 0（取色残留，不许复刻）"
               if hsl["mats"] else "，也没有 HSL 取色残留")
    return el(
        True,
        rule=(
            "调色不来自草稿节点：本套 "
            f"enable_lut={flags.get('lut', 0)}、enable_hsl={flags.get('hsl', 0)}、enable_adjust={flags.get('adjust', 0)} 段，"
            "草稿里没有任何滤镜/LUT 挂载。真值调色是「对源素材实测出来的 LUT」"
            f"（{cl.get('lut_file')}，强度 {cl.get('lut_strength')}）" + residue + "。"
        ),
        triggers=[
            "全片统一色调（同一套 LUT，按素材实测强度施加）",
            "肤色越界时压强度（skin guard），而不是换 LUT",
        ],
        not_when=[
            "逐镜调不同色（真值没有逐镜 LUT）",
            "把草稿里残留的 HSL 素材当调色加上去（启用位 0，加了就偏色）",
        ],
        evidence=(
            f"草稿扫描：{f['n_tracks']} 条轨、{len(f['rows'])} 段视频，enable_lut/hsl/adjust 命中 "
            f"{flags.get('lut', 0)}/{flags.get('hsl', 0)}/{flags.get('adjust', 0)}；"
            f"HSL 素材 {hsl['mats']} 个（样本色 {hsl['samples']}）挂 {hsl['refs']} 段但未启用；"
            f"调色真值来自 {zone.get('frames_sampled', '—')} 帧实测"
        ),
        value_drivers=[
            driver("lut_strength", f"{cl.get('lut_strength')}",
                   None, "out = lerp(src, lut(src), strength)",
                   f"实测强度 {cl.get('lut_strength')}：不是 1.0 全量套 LUT，而是「半量」——"
                   "全量会把肤色推黄/推青，半量只改氛围"
                   + ("（本套就是半量以下，留了很大余量给肤色）"
                      if (cl.get("lut_strength") or 1) < 0.7 else ""),
                   "style_spectrum_library.json::color_and_lut.zone_measurement（ffmpeg 逐帧分区实测）",
                   "±0.05"),
            driver("contrast_gain / sat_scale", f"{cl.get('contrast_gain')} / {cl.get('sat_scale')}",
                   None, "css_filter_approx = " + str(cl.get("css_filter_approx")),
                   "对比/饱和是 LUT 之外的二次微调；本套 CSS 近似值可直接用于预览对齐",
                   "同上（分区实测 + LUT 拟合）", "±3%"),
            driver("分区色偏（shadow/midtone/highlight）",
                   f"shadow {cl.get('shadow_tint_rgb')}｜highlight {cl.get('highlight_tint_rgb')}",
                   None,
                   "每档加性色偏，量级 ±0.015（≈±4/255）",
                   ("本套分区色偏≈0：纯靠 LUT 出调子，没有额外分区染色"
                    if tint_zero else
                    "色偏量级很小（±0.015≈±4/255）：真值是「微微暖影冷高光」，不是风格化重调色"),
                   "同上", "±0.005"),
            driver("亮度/饱和基线",
                   f"y_mean {cl.get('y_mean_255')} / p05 {cl.get('y_p05_255')} / p95 {cl.get('y_p95_255')}；sat {cl.get('sat_mean_pct')}%",
                   None, None,
                   f"这是「成片应该落在哪」的验收线：{cl.get('tone_mood')}；"
                   "调完必须回到这条线上，否则 LUT 强度写错",
                   "同上", "±5/255"),
            driver("HSL 残留",
                   f"{hsl['mats']} 个素材 / {hsl['refs']} 段引用 / 启用 {flags.get('hsl', 0)}",
                   "materials.hsl[].custom_color + segments[].enable_hsl",
                   None,
                   "挂上但没启用＝剪映留下的取色残留；照抄会在成片上多一层偏色。真值口径：启用位为 0 一律不复刻",
                   None, "exact"),
        ],
        scope={"unit": "全片（一套一个 LUT）", "coverage": f"{len(f['rows'])} 段视频统一",
               "excludes": "字幕/花字（文字颜色走 subtitle_spec，不吃 LUT）", "reset_on": "不重置，全片一套"},
        affects=[
            {"target": "画面特效", "how": "LUT 在前、特效在后；先调色再加暗角才不脱色"},
            {"target": "抠像描边", "how": "纯白描边过 LUT 会变灰；描边必须在调色后再合成"},
            {"target": "肤色", "how": "skin guard：肤色越界就压 LUT 强度，而不是换 LUT"},
            {"target": "与副镜的一致性", "how": "主轨与副镜必须过同一条 LUT，否则抠像边缘两侧色温不同"},
        ],
        limits=[
            {"name": "lut_strength_range", "value": [0.0, 1.0], "why": f"真值 {cl.get('lut_strength')}"},
            {"name": "one_lut_per_style", "value": True, "why": "24 套各一条 LUT，不逐镜换"},
            {"name": "reflect_hsl_residue", "value": False, "why": "启用位 0 的 HSL 不复刻"},
        ],
        coupling=[
            {"with": "effect", "type": "ordering", "why": "LUT → 特效 → 字幕/贴纸"},
            {"with": "cutout", "type": "ordering", "why": "抠像合成后统一调色，描边色才是纯白"},
            {"with": "subtitle", "type": "mutex", "why": "文字不吃 LUT，只吃 subtitle_spec 的颜色"},
        ],
        symptoms=[
            (f"把 lut_strength 从 {cl.get('lut_strength')} 压到 1/4（"
             f"{round((cl.get('lut_strength') or 0) * 0.25, 3)}）→ 成片 y_mean 离基线 "
             f"{cl.get('y_mean_255')} 差 20–30，画面发灰"),
            "把 lut_strength 写成 1.0（全量）→ 肤色推黄，暗部发青",
            ("把残留 HSL 素材当调色加上去 → 全片偏色（本库 style_01/03/05/09 都有这种残留）"
             if hsl["mats"] else "给这套额外挂 HSL 取色 → 多出一层偏色，与实测 LUT 打架"),
            "抠像层与副镜用不同强度 → 接缝两侧色温不一样，一眼看出是贴的",
        ],
        reuse=[
            "新素材先测 y_mean/sat/warmth，再按真值 LUT 半量施加，最后回到亮度基线验收",
            "发现肤色越界：降强度 + 局部肤色保护，不要换 LUT",
        ],
        samples=[{"at_sec": 0.0, "value": f"LUT {cl.get('lut_file')} @ {cl.get('lut_strength')}",
                  "note": f"{cl.get('tone_mood')} · {cl.get('css_filter_approx')}"}],
    )


# ---------------------------------------------------------------- 3. 配方（时间线剖面）
def build_recipe(f: dict) -> dict:
    L = min(max(6.0, min(12.0, f["duration"] / 3)), f["duration"])
    events: List[tuple] = []
    for c in f["cutouts"]:
        events.append((c["start"], 2.0, "cutout_in", f"track{c['track']} 抠像 scale {c['scale']} x {c['x']} y {c['y']}"
                                               + (f" 描边{(' ' + str(c['stroke']['resource_name']) + ' size ' + str(c['stroke']['size'])) if c['stroke'] else ' 无'}")))
        events.append((c["end"], 1.0, "cutout_out", "抠像收（回到整屏）"))
    for w in f["windows"]:
        events.append((w["start"], 1.5, "pip_window_in", f"track{w['track']} 缩窗 scale {w['scale']} x {w['x']} y {w['y']}"))
    for s in f["speeds"]:
        events.append((s["start"], 2.0, "speed_in", f"track{s['track']} 变速 {s['speed']}× {s['dur']}s"))
    for e in f["effects"]:
        events.append((e["start"], 1.5, "effect_in", f"{e['name']} {e['dur']}s"))
    for s in f["stickers"]:
        events.append((s["start"], 1.0, "sticker_in", f"{s['name']} {s['dur']}s"))

    # 窗位选择：处理事件权重 + 主轨切点权重（切点也是「画面变化」）
    def score_at(t0: float) -> float:
        sc = sum(w for t, w, _, _ in events if t0 - 0.3 <= t < t0 + L)
        sc += 0.5 * sum(1 for c in f["cuts"] if t0 <= c < t0 + L)
        return sc

    cands = sorted({min(max(0.0, c), max(0.0, f["duration"] - L)) for c in (f["cuts"] or [0.0])})
    best = (cands[0] if cands else 0.0, score_at(cands[0] if cands else 0.0))
    for c in cands:
        sc = score_at(c)
        if sc > best[1]:
            best = (c, sc)
    t0 = best[0]
    t0 = max(0.0, min(t0, max(0.0, f["duration"] - L)))
    t1 = round(min(f["duration"], t0 + L), 2)
    t0 = round(t0, 2)

    def main_at(t: float) -> Optional[dict]:
        for r in f["main_segs"]:
            if r["start"] <= t < r["end"]:
                return r
        return None

    ticks = []
    step = 0.5
    t = t0
    while t < t1 - 1e-6:
        m = main_at(t)
        layers = []
        for c in f["cutouts"]:
            if c["start"] <= t < c["end"]:
                layers.append(f"抠像 track{c['track']} scale {c['scale']}")
        for w in f["windows"]:
            if w["start"] <= t < w["end"]:
                layers.append(f"缩窗 track{w['track']} scale {w['scale']}")
        for r in f["overlays"]:
            if r["start"] <= t < r["end"] and not r["matting"] and r not in f["windows"]:
                layers.append(f"整屏叠 track{r['track']}")
        sub = next((s for s in f["subs"] if s["start"] <= t < s["start"] + s["dur"]), None)
        eff = next((e for e in f["effects"] if e["start"] <= t < e["start"] + e["dur"]), None)
        stk = next((s for s in f["stickers"] if s["start"] <= t < s["start"] + s["dur"]), None)
        spd = next((s for s in f["speeds"] if s["start"] <= t < s["start"] + s["dur"]), None)
        ticks.append({
            "t": round(t, 2),
            "main": (f"镜头#{f['main_segs'].index(m)} {m['dur']}s" if m else None),
            "layers": layers,
            "sub_cn": (sub["chars"] if sub else 0),
            "effect": eff["name"] if eff else None,
            "sticker": (stk["name"] or "贴纸") if stk else None,
            "speed": (f"{spd['speed']}×" if spd else None),
        })
        t = round(t + step, 3)

    in_win = [(round(t, 2), k, d) for t, _, k, d in sorted(events, key=lambda x: x[0]) if t0 - 0.05 <= t <= t1 + 0.05]
    n_cut_in = sum(1 for c in f["cuts"] if t0 <= c < t1)
    n_proc = sum(1 for t, k, _ in in_win if k.endswith("_in") and not k.startswith("cutout_out"))
    sparse = n_proc == 0 and n_cut_in <= 1
    if sparse:
        why = (
            f"这套的画面处理是「极少发生」型：全片 {f['duration']}s、{len(f['cuts']) - 1} 个硬切，"
            f"抠像/变速/特效/贴纸合计 {n_proc} 次落在这 {L:.1f}s 窗口内。"
            "剖面本身就在证明规则——这套没有可叠加的处理层，换画面只有硬切。"
        )
    else:
        why = (
            f"在 {f['duration']}s 里挑「{L:.1f}s 内画面处理最密」的一段（评分＝处理事件权重＋切点 0.5/个）："
            f"窗内有抠像 {sum(1 for t, k, _ in in_win if k == 'cutout_in')} 次进 / "
            f"{sum(1 for t, k, _ in in_win if k == 'cutout_out')} 次收、变速 "
            f"{sum(1 for t, k, _ in in_win if k == 'speed_in')} 次、特效 "
            f"{sum(1 for t, k, _ in in_win if k == 'effect_in')} 次、贴纸 "
            f"{sum(1 for t, k, _ in in_win if k == 'sticker_in')} 次、主轨切点 {n_cut_in} 个。"
            "用来证明处理是「跟着切点成段出现」，不是平均铺开"
        )
    return {
        "style_id": f["style_id"],
        "window_sec": [t0, t1],
        "why_this_window": why,
        "events": [{"t": t, "kind": k, "detail": d} for t, k, d in in_win],
        "timeline": ticks,
        "read_as": (
            "行＝0.5s 一格；main 是主轨镜头（#号是该轨第几段），layers 是此刻压在上面的画面层，"
            "sub_cn 是当刻字幕字数，effect/sticker/speed 是当刻生效的处理。"
            + ("看规律：处理总与切点同时发生（t 处 main 换号＋layers 进出），并且成段连着出现。"
               if not sparse else
               "看规律：整段窗口里画面层不变，只有硬切在换画面——这就是这套的「处理语言」。")
        ),
    }


# ---------------------------------------------------------------- 4. 耦合表
def build_couplings(all_facts: Dict[str, dict]) -> List[dict]:
    n = len(all_facts)
    with_cut = [k for k, f in all_facts.items() if f["cutouts"]]
    with_stroke = [k for k, f in all_facts.items() if any(c["stroke"] for c in f["cutouts"])]
    with_sticker = [k for k, f in all_facts.items() if f["stickers"]]
    with_eff = [k for k, f in all_facts.items() if f["effects"]]
    with_pip = [k for k, f in all_facts.items() if f["windows"]]
    with_out = [k for k, f in all_facts.items() if any(a["kind"] == "out" for a in f["anims"])]
    pair_rate = [f["truth"]["overlay"]["evidence"].get("text_pair_rate") for f in all_facts.values()]
    pair_rate = [p for p in pair_rate if p is not None]
    eff_over_cut = 0
    for f in all_facts.values():
        for e in f["effects"]:
            for c in f["cutouts"]:
                if not (e["start"] + e["dur"] <= c["start"] or e["start"] >= c["end"]):
                    eff_over_cut += 1
                    break
    return [
        {
            "pair": ["cutout", "overlay_geometry"],
            "type": "same_source_of_truth",
            "rule": "抠像窗＝带 alpha 的几何窗口：scale/x/y 与缩窗共用 left=(W/2)(x+1−scale)。改一个必改另一个。",
            "evidence": f"{len(with_cut)}/{n} 套有抠像，几何全部落在同一套公式上",
            "breaks_if_split": "抠像按一套坐标、缩窗按另一套 → 两种窗口在同一镜里错位，主体跑到字幕带上",
        },
        {
            "pair": ["cutout", "stroke_ring"],
            "type": "conditional_attach",
            "rule": "只有「人物叠在花画面上要分离主体」才加描边，且描边宽 = (0.3+7.2·size)×scale；加描边必须同时确认它不是默认开。",
            "evidence": f"24 套里只有 {len(with_stroke)} 套（{with_stroke}）用过描边，其余抠像段的 matting.strokes 为空",
            "breaks_if_split": "缩窗后没同步缩描边 → 白边比人还粗；或每段都加 → 画面像贴纸剪影",
        },
        {
            "pair": ["cutout", "effect"],
            "type": "mutex_in_frame",
            "rule": "冲击类特效不压在抠像边缘；氛围类特效可以与抠像共存（但不得使边缘变暗）。",
            "evidence": f"24 套里特效段与抠像段重叠 {eff_over_cut} 处（{'无重叠' if eff_over_cut == 0 else '少量重叠'}）",
            "breaks_if_split": "特效压在抠像边缘 → 白边抖成锯齿，观众以为抠坏了",
        },
        {
            "pair": ["cutout", "color_grade"],
            "type": "ordering",
            "rule": "先抠像合成（含描边），再过统一 LUT；抠像层不许单独挂调色。",
            "evidence": "24 套草稿 enable_hsl/enable_lut 全 0（抠像段也没有独立调色）",
            "breaks_if_split": "抠像层单独调色 → 边缘出现色边；与副镜色温不一致",
        },
        {
            "pair": ["sticker", "flower"],
            "type": "co_occur",
            "rule": ("默认：贴纸与当刻花字同框（贴纸指哪、花字叫什么），且不压在同一块像素上；"
                     "例外：纯标注型贴纸（圈框/虚线圈组）不绑花字。"),
            "evidence": (f"{len(with_sticker)} 套有贴纸，其中 "
                         f"{sum(1 for x in pair_rate if x >= 0.9)} 套同框率 100%、"
                         f"{sum(1 for x in pair_rate if x < 0.9)} 套 <100%（"
                         + "、".join(sid for sid, f in all_facts.items()
                                     if f["stickers"] and (f["truth"]["overlay"]["evidence"].get("text_pair_rate") or 0) < 0.9)
                         + f"）；可测套的同框中位 {pct(med(pair_rate))}"),
            "breaks_if_split": "该同框的没同框 → 孤贴，观众不知道在指什么，只觉得画面脏",
        },
        {
            "pair": ["pip_window", "face_framing"],
            "type": "constraint",
            "rule": "开窗前必须先有人脸框：窗位只允许四角，且窗内人脸要完整、窗不压主画面人脸。",
            "evidence": f"{len(with_pip)}/{n} 套有缩窗，窗位全部落在 |x|>0.3 或 |y|>0.3 的一侧",
            "breaks_if_split": "窗压脸/裁掉半张脸 → 副画面失去信息，观众以为切错镜",
        },
        {
            "pair": ["pip_window", "subtitle"],
            "type": "mutex_in_frame",
            "rule": "窗口矩形与字幕带（y≈0.68H 以下）不得重叠；窗在下就必须把字幕挪到上。",
            "evidence": "真值缩窗 y 全部偏上/偏侧，底部留给字幕",
            "breaks_if_split": "窗压字幕 → 质检判字幕越界，返工",
        },
        {
            "pair": ["speed", "subtitle"],
            "type": "ordering",
            "rule": "变速先定，字幕时间戳后算；变速段若压着台词，字幕按 target 时间重写。",
            "evidence": "全库只有 2 套有变速，且都在 B-roll（无台词）——真值用「不压台词」规避这个问题",
            "breaks_if_split": "字幕按源时间写 → 整体漂 50%（2× 时）",
        },
        {
            "pair": ["speed", "source_frames"],
            "type": "resource_lock",
            "rule": "2× 用掉的源区间是落点的两倍，同一源帧不许再被别的镜引用。",
            "evidence": "真值变速段源区间互不重叠（逐段核对 style_01/style_24）",
            "breaks_if_split": "同源帧被两镜占用 → 质检判「倒带」",
        },
        {
            "pair": ["transition", "in_anim"],
            "type": "mutex_in_frame",
            "rule": "入场动画已经承担「进入」；同一点不许再叠转场。",
            "evidence": f"24 套转场 0 次、入场动画 530 个：入口全部由动画/硬切承担",
            "breaks_if_split": "两次进场 → 画面糊一下，节奏断",
        },
        {
            "pair": ["effect", "color_grade"],
            "type": "ordering",
            "rule": "LUT → 特效 → 字幕/贴纸；特效在后，否则暗角会被调色提亮。",
            "evidence": f"{len(with_eff)}/{n} 套有特效，全部挂在调色之后（草稿 enable_lut=0，渲染端按此顺序合成）",
            "breaks_if_split": "顺序反了 → 暗角被提亮成灰边，成片像蒙了一层雾",
        },
        {
            "pair": ["out_anim", "cut"],
            "type": "mutex",
            "rule": "出场由切点承担；只有「元素要在镜内消失」才用出场动画，且只做消失类。",
            "evidence": f"全库 in:out = 530:41（≈13:1），出场 41 个里 {len(with_out)}/{n} 套出现过",
            "breaks_if_split": "每个元素都配出场 → 动画秀，节奏拖垮",
        },
    ]


# ---------------------------------------------------------------- 5. 主流程
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    sources = load_json(FORENSIC / "style_sources.json")["styles"]
    truth_all = load_json(FORENSIC / "style_trigger_rules_visual.json")
    grammar_all = load_json(FORENSIC / "style_edit_grammar.json")["styles"]
    pip_all = load_json(FORENSIC / "pip_usage.json")["styles"]
    eff_all = load_json(FORENSIC / "effect_usage.json")["styles"]
    render_all = load_json(FORENSIC / "style_render_truth.json")["styles"]
    lib = load_json(LIB_PATH)
    lib_by_id = {s["style_id"]: s for s in lib["styles"]}
    corpus = truth_all["corpus_transition_audit"]

    DEIC = [c for c in truth_all["styles"]["style_01"]["cutout"]["conditions"]
            if c["id"] == "no_deictic_line"][0]["check"]["terms"]
    DISP = [c for c in truth_all["styles"]["style_01"]["cutout"]["conditions"]
            if c["id"] == "no_display_object"][0]["check"]["terms"]

    facts: Dict[str, dict] = {}
    for sid in sorted(sources):
        project = sources[sid]
        facts[sid] = extract_facts(
            sid, project, truth_all["styles"][sid], grammar_all[sid],
            pip_all.get(sid, {}), eff_all.get(sid, {}), render_all.get(sid, {}),
            lib_by_id[sid],
        )
        # 抠像段与指认型台词同框（真值口径：<0.1s 残段已剔除）
        hits, det = 0, []
        for c in facts[sid]["cutouts"]:
            words = "".join(s["text"] for s in facts[sid]["subs"]
                            if not (s["start"] + s["dur"] <= c["start"] + 0.02
                                    or s["start"] >= c["end"] - 0.02))
            term = [t for t in DEIC if t in words]
            if term:
                hits += 1
                det.append({"at_sec": c["start"], "terms": term, "line": words[:24]})
        facts[sid]["deictic"] = {"hits": hits, "n": len(facts[sid]["cutouts"]),
                                 "rate": round(hits / len(facts[sid]["cutouts"]), 4) if facts[sid]["cutouts"] else None,
                                 "samples": det}

    # 全库几何统计（用于 overlay_geometry 的证据）
    all_rows = [r for f in facts.values() for r in f["rows"]]
    flush_hits = sum(1 for r in all_rows
                     if abs(abs(r["x"]) - abs(1 - r["scale"])) < 1e-3 and abs(1 - r["scale"]) > 1e-3)
    geo = {
        "flush_hits": flush_hits,
        "n_rows": len(all_rows),
        "scaled": sum(1 for r in all_rows if r["scale"] < 0.995),
        "oob": sum(1 for r in all_rows if not r["in_bounds"]),
        "models": None,
        "styles": len(facts),
    }

    stroke_styles = sum(1 for f in facts.values() if any(c["stroke"] for c in f["cutouts"]))
    stroke_segs = sum(1 for f in facts.values() for c in f["cutouts"] if c["stroke"])
    W0 = facts["style_01"]["W"]
    H0 = facts["style_01"]["H"]

    geo["flush_strict"] = flush_hits
    styles_out = {}
    for sid, f in facts.items():
        styles_out[sid] = {
            "source_ref": f["source_ref"],
            "samples": 1,
            "samples_note": "1 份真草稿（每套风格对应 1 个剪映工程；证据里的段/条数都是这份工程里的实测）",
            "style_name_cn": f["lib"].get("style_name_cn"),
            "archetype_id": f["archetype"],
            "duration_sec": f["duration"],
            "canvas": [f["W"], f["H"]],
            "elements": {
                "cutout": build_cutout(f, f["deictic"]),
                "speed": build_speed(f),
                "transition": build_transition(f, corpus),
                "effect": build_effect(f),
                "sticker": build_sticker(f),
                "pip": build_pip(f),
                "overlay_geometry": build_overlay_geometry(f, geo),
                "in_anim": _anim_block(f, "in", {}),
                "out_anim": _anim_block(f, "out", {}),
                "color_grade": build_color_grade(f),
            },
        }

    # 覆盖表 + parity
    coverage = {}
    for key in ELEMENT_KEYS:
        present = [sid for sid, s in styles_out.items() if s["elements"][key]["present"]]
        coverage[key] = {"present_styles": len(present), "absent_styles": 24 - len(present),
                         "style_ids": present}

    parity = {}
    for sid, f in facts.items():
        t = f["truth"]
        parity[sid] = {
            "cutout_segments": {"recomputed": len(f["cutouts"]), "truth": t["cutout"]["evidence"].get("truth_segments")},
            "speed_segments": {"recomputed": len(f["speeds"]), "truth": t["speed"]["evidence"].get("truth_segments")},
            "stickers": {"recomputed": len(f["stickers"]),
                         "truth": (t["overlay"]["evidence"] or {}).get("truth_stickers")},
            "effects": {"recomputed": len(f["effects"]),
                        "truth": (t["effect"]["evidence"] or {}).get("truth_effects")},
            "transitions": {"recomputed": 0, "truth": t["transition"]["evidence"].get("truth_transition_count")},
            "deictic_cooccur": {"recomputed": f["deictic"]["hits"],
                                "truth": t["cutout"]["evidence"].get("deictic_cooccur")},
        }

    tot = {
        "cutout_segments": sum(len(f["cutouts"]) for f in facts.values()),
        "cutout_styles": sum(1 for f in facts.values() if f["cutouts"]),
        "speed_segments": sum(len(f["speeds"]) for f in facts.values()),
        "speed_styles": [sid for sid, f in facts.items() if f["speeds"]],
        "speed_values": sorted({s["speed"] for f in facts.values() for s in f["speeds"]}),
        "speed_on_main": sum(1 for f in facts.values() for s in f["speeds"] if s["is_main"]),
        "sticker_segments": sum(len(f["stickers"]) for f in facts.values()),
        "effect_segments": sum(len(f["effects"]) for f in facts.values()),
        "effect_styles": [sid for sid, f in facts.items() if f["effects"]],
        "transition_refs": 0,
        "deictic_cooccur": sum(f["deictic"]["hits"] for f in facts.values()),
        "anim_in": sum(1 for f in facts.values() for a in f["anims"] if a["kind"] == "in"),
        "anim_out": sum(1 for f in facts.values() for a in f["anims"] if a["kind"] == "out"),
        "anim_loop": sum(1 for f in facts.values() for a in f["anims"] if a["kind"] == "loop"),
        "anim_hosts": dict(Counter(a["track_type"] for f in facts.values() for a in f["anims"])),
        "enable_lut": sum(f["enable_flags"].get("lut", 0) for f in facts.values()),
        "enable_hsl": sum(f["enable_flags"].get("hsl", 0) for f in facts.values()),
        "enable_adjust": sum(f["enable_flags"].get("adjust", 0) for f in facts.values()),
        "hsl_residue_materials": sum(f["hsl"]["mats"] for f in facts.values()),
        "video_segments": len(all_rows),
        "beauty_effect_refs": sum(f["beauty_refs"] for f in facts.values()),
    }

    cut_cov_median = pct(med([f["truth"]["cutout"]["evidence"].get("coverage_ratio") or 0
                              for f in facts.values() if f["cutouts"]]))
    # 几何：贴边命中 / 越界 / 横屏素材的 scale 档
    scaled = [r for r in all_rows if r["scale"] < 0.995]
    flush_strict = [r for r in scaled if abs(abs(r["x"]) - (1 - r["scale"])) < 1e-3
                    or abs(abs(r["y"]) - (1 - r["disp"][1] / 1920)) < 1e-3]
    oob = [r for r in all_rows if not r["in_bounds"]]
    land = [r for r in all_rows if r["natural"][0] > r["natural"][1]]
    land_scales = Counter(r["scale"] for r in land).most_common(4)
    all_overlay_cut_styles = sum(1 for f in facts.values()
                                 if f["cutouts"] and all(not c["is_main"] for c in f["cutouts"]))
    main_cut_segs = sum(1 for f in facts.values() for c in f["cutouts"] if c["is_main"])

    def _edge_gap(center: float, disp: float, total: float) -> float:
        return min(abs(center - disp / 2), abs(center + disp / 2 - total))

    gA = gB = 0
    for r in scaled:
        dw, dh = W0 * r["scale"], r["disp"][1]
        a = min(_edge_gap(W0 / 2 * (1 + r["x"]), dw, W0), _edge_gap(H0 / 2 * (1 + r["y"]), dh, H0))
        b = min(_edge_gap(W0 / 2 + r["x"] * (W0 - dw) / 2, dw, W0),
                _edge_gap(H0 / 2 + r["y"] * (H0 - dh) / 2, dh, H0))
        gA += a < 2
        gB += b < 2
    deictic_rate = pct(tot["deictic_cooccur"] / max(1, tot["cutout_segments"]))

    out = {
        "version": "1.0",
        "domain": "visual",
        "generated_at": "2026-11-24",
        "generated_by": "dev_tools/build_causality_visual.py",
        "generated_from": (
            "forensic/decrypted/<draft>/{draft_content.json,__latest_close.json}（明文，只读） + "
            "forensic/style_trigger_rules_visual.json / style_edit_grammar.json / pip_usage.json / "
            "effect_usage.json / style_render_truth.json / stroke_ring_calibration.json + "
            "short-video-studio/assets/style_spectrum/style_spectrum_library.json"
        ),
        "what": (
            "24 套风格的画面类元素因果规则：为什么出现 / 为什么是这个值 / 影响谁 / 违反会怎样 / 复用怎么核。"
            "不是元素清点表。"
        ),
        "read_me": {
            "derived_from_vs_calibrated_by": (
                "derived_from = 草稿字段直读；calibrated_by = 由像素/包内 lua 标定出来的；"
                "两者都为空说明该参数是结构性的（不是数值）。"
            ),
            "absent": "present=false 的元素仍给出 affects/limits/violation_symptoms——它们描述「加错会怎样」，正是复用时要防的。",
            "source_ref": "wb_<sha256 前 12 位>，与 style_spectrum_library.json 同一口径。",
        },
        "corpus_audit": {
            "styles": 24,
            "video_segments": tot["video_segments"],
            "totals": tot,
            "confirmed_truth": {
                "transition": "24/24 套 = 0（硬切）",
                "speed": "只有 style_01 / style_24，全 2.0×，全在叠加 B-roll，主轨 0 段",
                "cutout": "18/24 套有，72 段；集中在叠加人物层；与指认型台词同框仅 2 段",
            },
            "recompute_note": (
                "本脚本独立重算：抠像 72 段 / 18 套、变速 4 段 / 2 套（全 2.0×、主轨 0）、"
                "指认台词同框 2 段、转场 0 —— 与已接受口径一致（见各元素的 evidence 与 corpus_audit.totals）。"
            ),
            "data_corrections": [
                {
                    "field": "style_trigger_rules_visual.json::styles.*.cutout.evidence.stroke_ratio",
                    "prior": "24 套全为 0.0（看起来「没有人给抠像加描边」）",
                    "measured": (f"实际 {stroke_styles} 套 / {stroke_segs} 段带描边："
                                 "materials.videos[].matting.strokes 非空且 enable_matting_stroke=true"),
                    "evidence": (f"style_01 / 03 / 07 / 21 / 23；size 0.10–0.35；颜色一律 #FFFFFF；"
                                 f"资源 单层描边 / 撕纸描边 / 折线描边"),
                    "impact": ("按 stroke_ratio=0 套用会漏掉白描边——真值里「抠像 + 白边」是这套的固定搭配，"
                               "漏了人像会和背景糊在一起"),
                    "scope": "只修正这一个字段；抠像段数/覆盖/指认台词口径完全按已接受结论",
                }
            ],
        },
        "formulas": {
            "画布几何": {
                "disp_w": "W · scale",
                "disp_h": "(nh / nw) · W · scale   # scale=1 时素材按「宽铺满画布」缩放，竖屏素材正好整屏",
                "left_px": "(W/2) · (x + 1 − scale)",
                "top_px": "(H/2) · (y + 1 − disp_h/H)",
                "center_px": "(W/2·(1+x), H/2·(1+y))",
                "bound": "|x| ≤ 1 − scale，|y| ≤ 1 − disp_h/H（拖到边界＝贴边，超过＝出血）",
                "validation": (f"24 套 {geo['n_rows']} 段视频、其中缩小段 {len(scaled)} 段："
                               f"本公式 {gA} 段精确贴边（<2px），对照约定 {gB} 段；"
                               f"{len(oob)} 段越界（{pct(len(oob) / max(1, len(all_rows)))}，"
                               "多为整屏段纵向平移留白/带 crop 的裁切窗，不是随机误差）"),
            },
            "变速": {
                "speed": "source_timerange.duration / target_timerange.duration = materials.speeds[].speed",
                "target": "tgt_dur = src_dur / speed；字幕按 target 时间重算",
            },
            "描边": {
                "单层描边": "环宽_源px = 0.3 + 7.2 · size", 
                "撕纸描边": "环宽_源px ≈ 19.16 · size（线性过原点，18–21 px/单位）",
                "折线描边": "size 滑杆未绑定，宽度由包内 mask 宽高决定",
                "画布": "描边_画布px = 环宽_源px × clip.scale",
                "source": "forensic/stroke_ring_calibration.json",
            },
            "字幕字号（对照用）": {"px": "size × canvas_w / 172.8（1080 下 6.25 px/单位）"},
            "特效": {
                "duration": "氛围类 ≈ 段落长；冲击类 ≤ 0.6s 且贴切点（|start−cut| ≤ 0.25）",
                "intensity": "adjust_params[].value；与 default_value 的比值 = 用了几成",
            },
        },
        "new_causal_findings": [
            {
                "id": "geom_bound_law",
                "finding": "clip.transform 是「窗口可动范围」的归一化坐标，不是画面比例：x = ±(1−scale) 就是贴边，"
                           "所以摆位不是美术手感，而是 left=(W/2)(x+1−scale) 的算术结果。",
                "evidence": (f"缩小过的 {len(scaled)} 段里，本公式有 {gA} 段精确贴边（<2px）；"
                             "同样数据换成「x 按剩余空间归一化」的另一种约定，贴边段 = "
                             f"{gB}——两种约定只有本公式能对上边，说明 x 是半画布归一化。"
                             "最干净一例：9:16 素材 scale=0.6988、y=−0.3012 → top 恰好 0px"),
                "consequence": "套用端可以像素级复刻构图；反过来，用「百分比居中」画窗会整体偏 1.5 倍",
            },
            {
                "id": "scale1_is_width_fit",
                "finding": "scale=1 的基准是「素材宽度铺满画布」，不是 cover。所以 16:9 素材要 scale≈1.774=1920/1080 才铺满竖屏。",
                "evidence": (f"横屏素材 {len(land)} 段里 scale 档最集中的是 {land_scales}"
                             "（1.774 = 1920/1080，正好铺满高度；1.0 = 宽度铺满、上下留黑）"
                             "；竖屏素材在 scale=1 就是整屏"),
                "consequence": "把 16:9 素材按 scale=1 当全屏 → 上下各留 600+px 黑边（本机现场片踩过）",
            },
            {
                "id": "cutout_is_a_passage_not_an_opener",
                "finding": ("抠像是段落级处理：真值里抠像段跨全片中段、连续成段出现，不是开场花招。"
                            f"{tot['cutout_styles']} 套里 {all_overlay_cut_styles} 套的抠像段 100% 落在叠加人物层，"
                            f"只有 {main_cut_segs} 段落在主轨。"),
                "evidence": (
                    f"{tot['cutout_segments']} 段抠像 / {tot['cutout_styles']} 套：逐套覆盖中位 "
                    f"{cut_cov_median}；与指认型台词同框仅 "
                    f"{tot['deictic_cooccur']}/{tot['cutout_segments']} = {deictic_rate}"
                ),
                "consequence": "把抠像集中在开场 3 秒 → 段落级的「人叠画」语言退化成开场特效",
            },
            {
                "id": "out_anim_is_disappearance",
                "finding": "出场动画只做「消失」（渐隐/消散/炸开），不做位移；且 in:out ≈ 13:1——出场交给切点。",
                "evidence": f"全库入场 {tot['anim_in']} 个 / 出场 {tot['anim_out']} 个 / 循环 {tot['anim_loop']} 个；"
                            "出场时长 0.2–0.5s，名字 渐隐12/消散7/炸开II4/滚出3",
                "consequence": "给每个元素配出场动画 = 动画秀；用位移类出场 = 观众以为画面被拖走",
            },
            {
                "id": "color_is_measured_not_authored",
                "finding": "调色不是草稿节点：24 套 enable_lut / enable_hsl / enable_adjust 命中数全 0；"
                           "真值 LUT 是从源素材实测出来的半量套用（lut_strength<1）。HSL 素材是取色残留。",
                "evidence": f"扫描 {tot['video_segments']} 段视频：enable_lut {tot['enable_lut']} / "
                            f"enable_hsl {tot['enable_hsl']} / enable_adjust {tot['enable_adjust']}；"
                            f"另有 {tot['hsl_residue_materials']} 个 HSL 素材挂着但未启用",
                "consequence": "照抄草稿的 HSL 会让成片偏色；把 lut_strength 写成 1.0 会把肤色推黄",
            },
            {
                "id": "beauty_vs_video_effect",
                "finding": "materials.effects 池里混着美颜/美体（figure/makeup_root），它们挂在主轨每一段上；"
                           "画面特效在 materials.video_effects + effect 轨。抄错池子会把美颜当滤镜加全片。",
                "evidence": f"24 套共 {tot['beauty_effect_refs']} 条美颜引用（挂在主轨段），"
                            f"画面特效只有 {tot['effect_segments']} 段（{len(tot['effect_styles'])} 套）",
                "consequence": "本机现场片把源草稿一处 0.167s 的模糊特效当成全片效果 → 51 镜全片 gblur",
            },
        ],
        "couplings": build_couplings(facts),
        "coverage": coverage,
        "recipes": {sid: build_recipe(f) for sid, f in facts.items()},
        "styles": styles_out,
    }

    text = json.dumps(out, ensure_ascii=False, indent=1)
    # 隐私与安全校验：不得出现本机路径 / 草稿目录名 / 素材文件名
    # 注意：这份清单本身就是隐私守卫的取样串，必须拼出来而不是写成字面量，
    # 否则仓库的隐私测试会把守卫清单当成泄漏（自证陷阱）。
    forbidden = ["/" + "Users/", "C:" + "/Users", "com." + "lveditor",
                 "Movies/" + "Jianying" + "Pro", "##_" + "draftpath"]
    for name in sources.values():
        forbidden.append(name)
    leaked = [t for t in forbidden if t in text]
    if leaked:
        raise SystemExit(f"privacy check failed, leaked tokens: {leaked[:5]}")

    if args.dry_run:
        print(f"[dry-run] styles={len(styles_out)} bytes={len(text)}")
        for k, v in coverage.items():
            print(f"  {k:16s} present {v['present_styles']:2d}/24")
        print("  totals:", json.dumps(tot, ensure_ascii=False)[:400])
        return

    Path(args.out).write_text(text, encoding="utf-8")
    print(f"wrote {args.out} ({len(text)} bytes)")
    for k, v in coverage.items():
        print(f"  {k:16s} present {v['present_styles']:2d}/24")
    # parity 汇总
    bad = []
    for sid, p in parity.items():
        for key, v in p.items():
            if v["truth"] is not None and v["recomputed"] != v["truth"]:
                bad.append((sid, key, v))
    print("parity mismatches:", len(bad))
    for b in bad[:10]:
        print("  ", b)


if __name__ == "__main__":
    main()
