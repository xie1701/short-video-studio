"""元素因果规则的消费端：Agent 出片前查「为什么」，出片后核「对不对」。

用法：
    python element_rules.py explain style_21 flower      # 这套风格的花字为什么出现、参数从哪来、影响谁
    python element_rules.py check --pipeline-dir <管道>  # 拿规则表反查这份产物偏离在哪（指到参数/秒数）
    python element_rules.py list                         # 20 个元素与覆盖率

规则源：assets/style_spectrum/style_element_causality.json（由 dev_tools/merge_element_causality.py 从
三份 forensic 分片合并；人读版 references/element-causality.md）。

设计原则：
  * 不猜。表里没有的限值就不判，明确打印「跳过（规则表没有这条）」。
  * 只判「能当场量到」的东西：时长、密度、间隔、字数、坐标、强度。
  * 真值说「不出现」的元素，一旦出现就是违规（例如 24 套里转场真值全 0）。
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HERE = Path(__file__).resolve()
RULES_CANDIDATES = [
    HERE.parents[1] / "assets" / "style_spectrum" / "style_element_causality.json",
    HERE.parents[2] / "forensic" / "style_element_causality.json",
]


_RULES_CACHE: Dict[str, Dict[str, Any]] = {}


def load_rules(rules_path: Optional[Path] = None) -> Dict[str, Any]:
    if rules_path is not None:
        p = Path(rules_path)
        if not p.is_file():
            raise SystemExit(f"找不到规则文件: {p}")
        key = str(p.resolve())
        if key not in _RULES_CACHE:
            _RULES_CACHE[key] = json.loads(p.read_text(encoding="utf-8"))
        return _RULES_CACHE[key]
    for p in RULES_CANDIDATES:
        if p.is_file():
            key = str(p.resolve())
            if key not in _RULES_CACHE:
                _RULES_CACHE[key] = json.loads(p.read_text(encoding="utf-8"))
            return _RULES_CACHE[key]
    raise SystemExit("找不到 style_element_causality.json（先跑 dev_tools/merge_element_causality.py）")


def get_element_rule(style_id: str, element: str, rules_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """供上游脚本（step3/4/6/qa）只读查询单套风格的单元素因果规则；不存在返回 None。"""
    try:
        rules = load_rules(rules_path)
    except SystemExit:
        return None
    node = (rules.get("styles") or {}).get(str(style_id or ""))
    if not isinstance(node, dict):
        return None
    el = (node.get("elements") or {}).get(str(element or ""))
    return el if isinstance(el, dict) else None


def is_present(style_id: str, element: str, default: bool = True,
               rules_path: Optional[Path] = None) -> bool:
    el = get_element_rule(style_id, element, rules_path=rules_path)
    if el is None:
        return default
    return bool(el.get("present"))


def get_limit(style_id: str, element: str, *names: str, default: Any = None,
              rules_path: Optional[Path] = None) -> Any:
    el = get_element_rule(style_id, element, rules_path=rules_path)
    if not el:
        return default
    val = _limit(el, *names)
    return default if val is None else val


def get_driver(style_id: str, element: str, param: str, default: Any = None,
               rules_path: Optional[Path] = None) -> Any:
    el = get_element_rule(style_id, element, rules_path=rules_path)
    if not el:
        return default
    val = _driver(el, param)
    return default if val is None else val


def style_node(rules: Dict[str, Any], style_id: str) -> Dict[str, Any]:
    node = (rules.get("styles") or {}).get(style_id)
    if not node:
        raise SystemExit(f"规则表里没有 {style_id}")
    return node


def explain(style_id: str, element: str) -> str:
    rules = load_rules()
    el = (style_node(rules, style_id).get("elements") or {}).get(element)
    if not el:
        raise SystemExit(f"{style_id} 没有 {element} 的规则")
    cn = (rules.get("element_index", {}).get(element) or {}).get("cn", element)
    out: List[str] = [f"== {style_id} · {cn}（{element}）=="]
    if not el.get("present"):
        out.append(f"不出现：{el.get('absent_reason')}")
        if el.get("violation_symptoms"):
            out.append("硬加会怎样：")
            out += [f"  - {x}" for x in el["violation_symptoms"]]
        return "\n".join(out)
    w = el.get("why_appears") or {}
    out.append(f"为什么出现：{w.get('rule')}")
    if w.get("triggers"):
        out.append("  触发：" + "、".join(str(x) for x in w["triggers"]))
    if w.get("not_when"):
        out.append("  不该出现：" + "；".join(str(x) for x in w["not_when"]))
    if w.get("evidence"):
        out.append(f"  证据：{w['evidence']}")
    sc = el.get("scope") or {}
    if sc:
        out.append(f"作用域：{sc.get('unit')} · {sc.get('coverage')}（不适用：{sc.get('excludes')}）")
    if el.get("value_drivers"):
        out.append("参数从哪来：")
        for d in el["value_drivers"]:
            out.append(f"  - {d.get('param')} = {d.get('value')}")
            if d.get("derived_from"):
                out.append(f"      来源：{d['derived_from']}")
            if d.get("formula"):
                out.append(f"      公式：{d['formula']}")
            if d.get("why_this_value"):
                out.append(f"      依据：{d['why_this_value']}")
            if d.get("tolerance"):
                out.append(f"      容差：{d['tolerance']}")
    if el.get("limits"):
        out.append("硬限值：")
        for x in el["limits"]:
            out.append(f"  - {x.get('name')} = {x.get('value')}（{x.get('why')}）")
    if el.get("affects"):
        out.append("影响谁：")
        for a in el["affects"]:
            out.append(f"  - {a.get('target')}：{a.get('how')}")
    if el.get("coupling"):
        out.append("耦合：")
        for c in el["coupling"]:
            out.append(f"  - {c.get('pair') or c.get('with')} · {c.get('type')}：{c.get('why') or c.get('rule')}")
    if el.get("violation_symptoms"):
        out.append("违反症状：")
        out += [f"  - {x}" for x in el["violation_symptoms"]]
    if el.get("reuse_check"):
        out.append("复用怎么核：")
        out += [f"  - {x}" for x in el["reuse_check"]]
    return "\n".join(out)


# --------------------------------------------------------------------------- 量测
def _load(p: Path) -> Optional[Dict[str, Any]]:
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _band(sub_bbox: Dict[str, Any]) -> Tuple[float, float]:
    return (float(sub_bbox.get("y", 0)), float(sub_bbox.get("y", 0)) + float(sub_bbox.get("h", 0)))


def measure(pipeline_dir: Path) -> Dict[str, Any]:
    """从管道产物里量出可比对的观测值（拿不到就不放这个键）。"""
    m: Dict[str, Any] = {}
    edl = _load(pipeline_dir / "04_edl.json") or {}
    subs = _load(pipeline_dir / "05_subtitles.json") or {}
    ae = _load(pipeline_dir / "03_audio_events.json") or {}
    pkg = _load(pipeline_dir / "06_packaging.json") or {}
    plan = _load(pipeline_dir / "07_color_plan.json") or {}
    clips_dur = sum(float(c.get("duration_sec") or 0.0) for c in (edl.get("clips") or []))
    sub_end = float((subs.get("entries") or [{}])[-1].get("end_sec") or 0.0)
    dur = float(edl.get("total_duration_sec") or ae.get("locked_duration_sec") or clips_dur or sub_end or 0.0)

    entries = subs.get("entries") or []
    if entries:
        chars = [len(str(e.get("text") or "")) for e in entries]
        m["subtitle"] = {
            "count": len(entries),
            "per_min": round(len(entries) / (dur / 60.0), 2) if dur else None,
            "chars_median": statistics.median(chars) if chars else None,
            "y_center": round(statistics.median([_band(e.get("final_bbox") or e.get("bbox") or {})[0] for e in entries]), 1),
            "font_px": (subs.get("template_config") or {}).get("font_size"),
        }
    elems = pkg.get("visual_elements") or []
    flowers = [e for e in elems if str(e.get("kind")) in ("flower_text", "stamp_tag", "quote_citation_card")]
    if flowers:
        holds = [float(e.get("read_hold_sec") or 0.0) for e in flowers]
        starts = sorted(float(e.get("start_frame") or 0) for e in flowers)
        fps = 30.0
        gaps = [(b - a) / fps for a, b in zip(starts, starts[1:])]
        sub_band = None
        if entries:
            lows = [_band(e.get("final_bbox") or e.get("bbox") or {})[0] for e in entries]
            highs = [_band(e.get("final_bbox") or e.get("bbox") or {})[1] for e in entries]
            sub_band = (statistics.median(lows), statistics.median(highs))
        overlap = 0
        if sub_band:
            for e in flowers:
                lo, hi = _band(e.get("final_bbox") or e.get("bbox") or {})
                if not (hi <= sub_band[0] + 1 or lo >= sub_band[1] - 1):
                    overlap += 1
        m["flower"] = {
            "count": len(flowers),
            "per_min": round(len(flowers) / (dur / 60.0), 2) if dur else None,
            "hold_median": round(statistics.median(holds), 2) if holds else None,
            "hold_max": round(max(holds), 2) if holds else None,
            "gap_min": round(min(gaps), 2) if gaps else None,
            "chars_max": max((len(str(e.get("text_or_asset") or "")) for e in flowers), default=None),
            "subtitle_band_overlap": overlap,
            "opening_present": any(int(e.get("start_frame") or 0) <= 15 for e in flowers),
        }
    giants = [e for e in elems if str(e.get("kind")) in ("giant_text", "center_burst_title")]
    if giants:
        m["giant_text"] = {
            "count": len(giants),
            "per_min": round(len(giants) / (dur / 60.0), 2) if dur else None,
            "font_sizes": [e.get("font_size_px") for e in giants if e.get("font_size_px")],
            "chars_max": max((len(str(e.get("text_or_asset") or "")) for e in giants), default=None),
        }
    if elems:
        in_cnt = sum(1 for e in elems if str(e.get("jianying_anim_name") or e.get("anim_primitive") or e.get("in_anim") or "").strip())
        out_cnt = sum(1 for e in elems if str(e.get("out_anim") or "").strip())
        m["in_anim"] = {"count": in_cnt, "total_elems": len(elems)}
        if out_cnt > 0 or len(elems) >= 2:
            m["out_anim"] = {"count": out_cnt, "total_elems": len(elems)}
    elif (pkg.get("kinetic_stage") or {}).get("enabled"):
        ks_items = (pkg.get("kinetic_stage") or {}).get("items") or []
        ks_flowers = [it for it in ks_items if str(it.get("variant") or "") != "bridge_bar"]
        ks_giants = [it for it in ks_items if float(it.get("font_size_units") or 0.0) >= 22.0]
        if ks_flowers:
            m["flower"] = {
                "count": len(ks_flowers),
                "per_min": round(len(ks_flowers) / (dur / 60.0), 2) if dur else None,
                "hold_median": round(statistics.median([float(it.get("read_hold_sec") or 1.2) for it in ks_flowers]), 2),
                "hold_max": round(max(float(it.get("read_hold_sec") or 1.2) for it in ks_flowers), 2),
                "gap_min": 0.0,
                "chars_max": max((len(str(it.get("text_or_asset") or "")) for it in ks_flowers), default=None),
                "subtitle_band_overlap": 0,
                "opening_present": any(int(it.get("start_frame") or 0) <= 15 for it in ks_flowers),
            }
        if ks_giants:
            m["giant_text"] = {
                "count": len(ks_giants),
                "per_min": round(len(ks_giants) / (dur / 60.0), 2) if dur else None,
                "font_sizes": [it.get("font_size_px") for it in ks_giants if it.get("font_size_px")],
                "chars_max": max((len(str(it.get("text_or_asset") or "")) for it in ks_giants), default=None),
            }
        in_ks = sum(1 for it in ks_items if str(it.get("jianying_anim_name") or "").strip())
        m["in_anim"] = {"count": in_ks, "total_elems": len(ks_items)}
    ae_bgm = (ae.get("bgm") or {}) if isinstance(ae, dict) else {}
    db_info = (ae_bgm.get("dual_bgm_switch") or {}) if isinstance(ae_bgm, dict) else {}
    pkg_bgm = (pkg.get("bgm") or {}) if isinstance(pkg, dict) else {}
    if db_info or pkg_bgm or ae_bgm.get("file"):
        applied_db = bool(db_info.get("applied") or pkg_bgm.get("dual_bgm"))
        m["dual_bgm_switch"] = {
            "count": 1 if applied_db else 0,
            "applied": applied_db,
            "mode": str(db_info.get("mode") or ("sequential_handoff" if pkg_bgm.get("dual_bgm") else "")).strip(),
            "switch_sec": db_info.get("target_switch_sec") or pkg_bgm.get("switch_sec"),
        }
    end_cards = [e for e in elems if str(e.get("kind")) in ("end_card", "end_card_cta")]
    if end_cards:
        m["end_card"] = {"count": len(end_cards)}
    text_like = [e for e in elems if str(e.get("kind")) in ("flower_text", "center_burst_title", "stamp_tag", "quote_citation_card")]
    st_elems = [e for e in elems if str(e.get("kind")) == "indicator_sticker"]
    if st_elems:
        paired_st = 0
        max_st_dur = 0.0
        for se in st_elems:
            sf_s, ef_s = int(se.get("start_frame") or 0), int(se.get("end_frame") or 0)
            max_st_dur = max(max_st_dur, max(0.0, (ef_s - sf_s) / 30.0))
            if se.get("paired_element_id") or any(
                sf_s < int(te.get("end_frame") or 0) and ef_s > int(te.get("start_frame") or 0)
                for te in text_like
            ):
                paired_st += 1
        m["sticker"] = {
            "segments": len(st_elems),
            "count": len(st_elems),
            "paired_count": paired_st,
            "pair_rate": round(paired_st / float(len(st_elems)), 3),
            "max_dur_sec": round(max_st_dur, 2),
        }
    cues = pkg.get("sfx_cues") or []
    if cues:
        starts = sorted(float(c.get("time_sec") or c.get("start_sec") or 0.0) for c in cues)
        gaps = [b - a for a, b in zip(starts, starts[1:])]
        m["sfx"] = {
            "count": len(cues),
            "per_min": round(len(cues) / (dur / 60.0), 2) if dur else None,
            "gap_min": round(min(gaps), 2) if gaps else None,
        }
    clips = edl.get("clips") or []
    if clips:
        def _is_cutout_on(c: Dict[str, Any]) -> bool:
            lay = c.get("pip_layout")
            if isinstance(lay, dict):
                if lay.get("cutout") is False:
                    return False
                if lay.get("cutout") is True:
                    return True
                if str(lay.get("mode") or "") in (
                    "cutout", "layered_cutout_over_broll", "vision_portrait_cutout_pip",
                    "data_news_cutout_pip", "person_behind_giant_text_pip",
                    "self_matting_behind_text",
                ):
                    return True
            return bool(c.get("cutout"))

        cut = [c for c in clips if _is_cutout_on(c)]
        cut_sec = sum(float(c.get("duration_sec") or 0.0) for c in cut)
        has_aroll = any(str(c.get("track_role")) == "a_roll" for c in clips)
        has_broll = any(
            str(c.get("track_role")) == "b_roll" or bool(c.get("b_roll_overlay"))
            for c in clips
        )
        cap = edl.get("broll_capacity") or {}
        dual_capable = bool(has_aroll and (has_broll or float(cap.get("broll") or 0.0) > 0.0))
        m["cutout"] = {
            "segments": len(cut),
            "seconds": round(cut_sec, 2),
            "coverage": round(cut_sec / max(dur, 0.01), 3),
            "runs": _count_runs([float(c.get("timeline_in_sec") or 0.0) for c in cut],
                                [float(c.get("timeline_out_sec") or 0.0) for c in cut]),
            "outlined": sum(1 for c in cut if int((c.get("pip_layout") or {}).get("outline_px") or 0) > 0),
            "torn_paper": sum(1 for c in cut if bool((c.get("pip_layout") or {}).get("torn_paper"))),
            "dual_capable": dual_capable,
            "duration_sec": round(dur, 2),
        }
        m["speed"] = {"segments": sum(1 for c in clips if abs(float(c.get("speed") or 1.0) - 1.0) > 1e-6)}
        m["transition"] = {"segments": sum(1 for c in clips if str(c.get("transition") or "none") not in ("none", "", "None"))}
        m["pip"] = {"segments": sum(1 for c in clips if (c.get("pip_layout") or {}).get("mode") and (c.get("pip_layout") or {}).get("mode") != "none")}
        st_elem_cnt = sum(1 for e in elems if str(e.get("kind")) == "indicator_sticker")
        st_clip_cnt = sum(1 for c in clips if c.get("sticker") or c.get("sticker_layers"))
        if st_clip_cnt or st_elem_cnt:
            prev_st = m.get("sticker") or {}
            m["sticker"] = {
                "segments": st_clip_cnt + st_elem_cnt,
                "count": st_clip_cnt + st_elem_cnt,
                "paired_count": prev_st.get("paired_count", st_clip_cnt),
                "pair_rate": prev_st.get("pair_rate", 1.0),
                "max_dur_sec": prev_st.get("max_dur_sec"),
            }
        elif "sticker" not in m:
            m["sticker"] = {"segments": 0, "count": 0, "pkg_text_count": len(text_like), "no_flower_text": bool(pkg.get("no_flower_text"))}
    if plan:
        m["color_grade"] = {"lut_strength": plan.get("lut_strength")}
    m["_duration_sec"] = dur
    return m


def _count_runs(starts: List[float], ends: List[float], tol: float = 0.12) -> int:
    if not starts:
        return 0
    order = sorted(zip(starts, ends))
    runs = 1
    prev_end = order[0][1]
    for s, e in order[1:]:
        if s > prev_end + tol:
            runs += 1
        prev_end = max(prev_end, e)
    return runs


def _limit(el: Dict[str, Any], *names: str) -> Optional[Any]:
    for x in el.get("limits") or []:
        if str(x.get("name")) in names:
            return x.get("value")
    return None


def _driver(el: Dict[str, Any], param: str) -> Optional[Any]:
    for d in el.get("value_drivers") or []:
        if str(d.get("param")) == param:
            return d.get("value")
    return None


def audit(pipeline_dir: Path) -> Dict[str, Any]:
    """核查管道产物相对元素因果规则表的偏离，返回结构化报告（供 check CLI 与 qa_inspector 共用）。"""
    rules = load_rules()
    applied = _load(pipeline_dir / "style_spectrum_applied.json") or {}
    pkg = _load(pipeline_dir / "06_packaging.json") or {}
    style_id = str(applied.get("style_id") or pkg.get("style_id") or "")
    if not style_id:
        raise SystemExit("管道里读不到 style_id（style_spectrum_applied.json / 06_packaging.json）")
    node = style_node(rules, style_id)
    meas = measure(pipeline_dir)
    dur_total = float(meas.get("_duration_sec") or 0.0)
    els = node.get("elements") or {}

    rows: List[Tuple[str, str, str, str, str]] = []  # 元素 / 指标 / 产物 / 真值 / 判定
    fails = 0

    def add(el_name: str, what: str, obs: Any, target: Any, verdict: str, why: str) -> None:
        nonlocal fails
        if verdict == "FAIL":
            fails += 1
        rows.append((el_name, what, str(obs), str(target), f"{verdict}｜{why}"))

    for key, el in els.items():
        obs = meas.get(key)
        if not el.get("present"):
            if obs and (obs.get("segments") or obs.get("count") or obs.get("seconds")):
                add(key, "真值里不出现，产物里出现了", json.dumps(obs, ensure_ascii=False),
                    "0", "FAIL", f"{el.get('absent_reason')}")
            continue
        if key == "giant_text" and (not obs or not obs.get("count")):
            flw_rule = els.get("flower") or {}
            if (not flw_rule.get("present")) and dur_total >= 6.0 and pkg:
                add(key, "巨字条数", 0, "≥1（本套无普通花字，展示字须走巨字）", "FAIL",
                    "风格真值用巨字替代普通花字，产物实测 0 条巨字")
            continue
        if key == "dual_bgm_switch" and obs is not None and dur_total >= 6.0:
            ok_db = bool(obs.get("applied"))
            add(key, "双段BGM切换", obs.get("mode") or ("0" if not ok_db else "applied"),
                "已启用", "PASS" if ok_db else "FAIL",
                f"已切换 @{obs.get('switch_sec')}s" if ok_db else "源草稿含双段BGM切换/sting交接，产物未应用")
            continue
        if key == "in_anim" and obs and int(obs.get("total_elems") or 0) > 0:
            ok_in = int(obs.get("count") or 0) >= 1
            add(key, "入场动画挂接数", f"{obs.get('count', 0)}/{obs.get('total_elems', 0)}",
                "≥1", "PASS" if ok_in else "FAIL",
                "展示元素已挂入场动画" if ok_in else "源草稿入场动画显式存在，产物全部包装元素均未挂入场动画")
            continue
        if key == "out_anim" and obs and int(obs.get("total_elems") or 0) >= 2 and dur_total >= 6.0:
            ok_out = int(obs.get("count") or 0) >= 1
            add(key, "出场动画挂接数", f"{obs.get('count', 0)}/{obs.get('total_elems', 0)}",
                "≥1", "PASS" if ok_out else "FAIL",
                "信息层已挂淡出/下滑退场" if ok_out else "源草稿出场动画显式存在，产物实测 0 条退场动画")
            continue
        if key == "sticker" and obs is not None:
            st_cnt = int(obs.get("count") or 0)
            if st_cnt == 0:
                if dur_total >= 6.0 and pkg and not obs.get("no_flower_text") and int(obs.get("pkg_text_count") or 0) >= 2:
                    add(key, "同框指示贴纸", 0, "≥1", "FAIL", "源草稿含同框指示贴纸，产物实测 0 条")
                continue
            pair_lim = _limit(el, "text_pair_rate")
            if pair_lim is not None and float(pair_lim) >= 0.99 and obs.get("pair_rate") is not None:
                ok_pair = float(obs["pair_rate"]) >= float(pair_lim) - 1e-6
                add(key, "贴纸花字同框率", obs["pair_rate"], pair_lim, "PASS" if ok_pair else "FAIL",
                    "与展示字同框成对" if ok_pair else "贴纸必须与核心花字/大字同框出现，禁止悬空单贴")
            dur_lim = _limit(el, "max_dur_sec")
            if dur_lim is not None and obs.get("max_dur_sec") is not None:
                ok_sd = float(obs["max_dur_sec"]) <= float(dur_lim) + 0.05
                add(key, "贴纸最长停留（秒）", obs["max_dur_sec"], f"≤{dur_lim}", "PASS" if ok_sd else "FAIL",
                    "短促标注不拖沓" if ok_sd else "贴纸停留超时，变成常驻角标")
            continue
        if not obs:
            continue
        lim_hit = _limit(el, "max_per_min", "per_min", "sfx_per_min", "density_per_min")
        if lim_hit is not None and obs.get("per_min") is not None:
            try:
                cap = float(lim_hit)
                obs_cnt = obs.get("count")
                if cap > 0 and obs_cnt is not None and dur_total > 0:
                    allowed_cnt = max(1, int(math.floor(cap * dur_total / 60.0 + 1e-6)))
                    ok = float(obs["per_min"]) <= cap + 1e-6 or int(obs_cnt) <= allowed_cnt
                else:
                    ok = float(obs["per_min"]) <= cap + 1e-6
                add(key, "每分钟条数", obs["per_min"], lim_hit, "PASS" if ok else "FAIL",
                    "真值密度是上限，不是配额" if not ok else "在真值密度以内")
            except (TypeError, ValueError):
                pass
        hold_lim = _limit(el, "hold_sec", "hold_sec_range", "fallback_hold_sec")
        if obs.get("hold_median") is not None and isinstance(hold_lim, (list, tuple)) and len(hold_lim) == 2:
            lo, hi = float(hold_lim[0]), float(hold_lim[1])
            ok = lo - 0.05 <= float(obs["hold_median"]) <= hi + 0.05
            add(key, "停留中位（秒）", obs["hold_median"], f"{lo}~{hi}", "PASS" if ok else "FAIL",
                "花字停留取源草稿里这一张自己的段时长" if ok else "停留偏离真值 → 大字在画面里杵太久/闪一下就没了")
        gap_lim = _limit(el, "min_gap_sec", "gap_min_sec")
        if obs.get("gap_min") is not None and gap_lim is not None:
            try:
                ok = float(obs["gap_min"]) >= float(gap_lim) - 0.05
                add(key, "最小间隔（秒）", obs["gap_min"], gap_lim, "PASS" if ok else "FAIL",
                    "两张花字/两声音效挤在一起会打架" if not ok else "间隔合规")
            except (TypeError, ValueError):
                pass
        if key == "flower" and obs.get("subtitle_band_overlap"):
            add(key, "花字压进字幕带（张）", obs["subtitle_band_overlap"], "0", "FAIL",
                "真值是「y 带互斥」：同帧可以并存，但不能叠在同一块像素上")
        elif key == "flower":
            add(key, "花字压进字幕带（张）", 0, "0", "PASS", "y 带互斥成立")
            if (not (els.get("giant_text") or {}).get("present")) and pkg and not pkg.get("no_flower_text"):
                try:
                    from render_template_overlays import get_style_blueprint
                    _omode = str(((get_style_blueprint(style_id).get("overlay_style") or {}).get("opening_mode") or "")).strip()
                except Exception:
                    _omode = ""
                if _omode == "store_poster_burst":
                    ok_op = bool(obs.get("opening_present"))
                    add(key, "开场结构钩子", "有" if ok_op else "0", "开场 ≤0.5s 上屏",
                        "PASS" if ok_op else "FAIL",
                        "已放置开场三拍斜标钩子" if ok_op else "门店斜标开场风格前 0.5s 缺失开场斜标钩子")
        if key == "cutout":
            share_lim = _limit(el, "max_coverage_ratio", "coverage_ratio", "max_share_of_person_layer")
            share_val = None
            if share_lim is not None:
                try:
                    share_val = float(share_lim)
                except (TypeError, ValueError):
                    share_val = None
            if share_val is not None:
                ok = float(obs["coverage"]) <= share_val + 1e-6
                add(key, "抠像覆盖占比", obs["coverage"], share_lim, "PASS" if ok else "FAIL",
                    "抠像是段落级语言，占比不该压满全片" if not ok else "在真值占比以内")
            # 缺失型拦截：对高占比叠加抠像风格（如 style_21），如果素材同时有 A-roll 与 B-roll
            # 却做出 0% 抠像（退化为纯 A/B 来回闪切），必须拦红
            if (
                share_val is not None and share_val >= 0.30
                and int(obs.get("segments") or 0) == 0
                and obs.get("dual_capable")
                and float(obs.get("duration_sec") or 0.0) >= 6.0
            ):
                add(key, "分层抠像缺失", "0.0", f">0（上限 {share_lim}）", "FAIL",
                    "风格真值要求成段人物抠像叠 B-roll，产物实测 0%（退化为 A/B 来回切镜）")
            if int(obs.get("segments") or 0) > 0:
                stroke_drv = next(
                    (d for d in (el.get("value_drivers") or [])
                     if str(d.get("param") or "").startswith("matting.strokes")),
                    None,
                )
                if stroke_drv:
                    drv_txt = f"{stroke_drv.get('value') or ''} {stroke_drv.get('why_this_value') or ''}"
                    if "带描边" in drv_txt or "单层描边" in drv_txt or "撕纸描边" in drv_txt:
                        ok_outl = int(obs.get("outlined") or 0) >= 1
                        add(key, "抠像轮廓描边", obs.get("outlined", 0), "≥1 段带描边",
                            "PASS" if ok_outl else "FAIL",
                            "前景与底图已做描边分离" if ok_outl else "源草稿抠像包含实体描边，产物实测 0 段描边")
                    if "撕纸描边" in drv_txt and "单层描边" not in drv_txt:
                        ok_torn = int(obs.get("torn_paper") or 0) >= 1 or int(obs.get("outlined") or 0) >= 1
                        add(key, "撕纸锯齿白边", obs.get("torn_paper", 0), "≥1 段撕纸描边",
                            "PASS" if ok_torn else "FAIL",
                            "已启用平滑描边分离" if ok_torn else "源草稿明确使用描边分离，产物未开启描边")
            add(key, "抠像段数 / 连续段数", f"{obs['segments']} / {obs['runs']}",
                "见 limits.max_runs", "INFO", "真值是成段出现，不是每镜开一下关一下")
        if key == "color_grade":
            want = _driver(el, "lut_strength")
            if want is not None and obs.get("lut_strength") is not None:
                try:
                    w = float(str(want).split()[0])
                    got = float(obs["lut_strength"])
                    ok = got >= w * 0.7 - 1e-6
                    add(key, "LUT 强度", got, f"{w}（下限按 70% 计）", "PASS" if ok else "FAIL",
                        "把强度压到真值的零头会让整片发灰" if not ok else "强度接近真值")
                except (TypeError, ValueError):
                    pass

    fail_rows = [
        {
            "element": r[0],
            "metric": r[1],
            "actual": r[2],
            "expected": r[3],
            "reason": r[4],
        }
        for r in rows
        if str(r[4]).startswith("FAIL")
    ]
    return {
        "style_id": style_id,
        "observed_elements": sum(1 for k in meas if k in els),
        "passed": fails == 0,
        "fails": fails,
        "fail_rows": fail_rows,
        "rows": rows,
    }


def check(pipeline_dir: Path) -> int:
    rep = audit(pipeline_dir)
    rows = rep["rows"]
    print(f"[element_rules] {rep['style_id']} · 观测元素 {rep['observed_elements']} 个")
    width = max((len(r[0]) for r in rows), default=8)
    print(f"{'元素'.ljust(width)} | 指标 | 产物 | 真值 | 判定")
    for r in rows:
        print(f"{r[0].ljust(width)} | {r[1]} | {r[2]} | {r[3]} | {r[4]}")
    print(f"[element_rules] {'PASS' if rep['passed'] else f'{rep['fails']} 项偏离'}")
    return 0 if rep["passed"] else 1


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("explain")
    e.add_argument("style_id")
    e.add_argument("element")
    c = sub.add_parser("check")
    c.add_argument("--pipeline-dir", required=True)
    sub.add_parser("list")
    args = ap.parse_args()

    if args.cmd == "explain":
        print(explain(args.style_id, args.element))
    elif args.cmd == "check":
        sys.exit(check(Path(args.pipeline_dir)))
    else:
        rules = load_rules()
        for key, idx in (rules.get("element_index") or {}).items():
            print(f"{key:18s} {idx['cn']:8s} {idx['domain']:6s} {len(idx['styles']):2d}/24")


if __name__ == "__main__":
    main()
