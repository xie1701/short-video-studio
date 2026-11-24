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
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HERE = Path(__file__).resolve()
RULES_CANDIDATES = [
    HERE.parents[1] / "assets" / "style_spectrum" / "style_element_causality.json",
    HERE.parents[2] / "forensic" / "style_element_causality.json",
]


def load_rules() -> Dict[str, Any]:
    for p in RULES_CANDIDATES:
        if p.is_file():
            return json.loads(p.read_text(encoding="utf-8"))
    raise SystemExit("找不到 style_element_causality.json（先跑 dev_tools/merge_element_causality.py）")


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
    pkg = _load(pipeline_dir / "06_packaging.json") or {}
    plan = _load(pipeline_dir / "07_color_plan.json") or {}
    dur = float(edl.get("total_duration_sec") or 0.0) or float((subs.get("entries") or [{}])[-1].get("end_sec") or 0.0)

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
        cut = [c for c in clips if c.get("cutout") or (c.get("pip_layout") or {}).get("mode") == "cutout"]
        cut_sec = sum(float(c.get("duration_sec") or 0.0) for c in cut)
        m["cutout"] = {
            "segments": len(cut),
            "seconds": round(cut_sec, 2),
            "coverage": round(cut_sec / max(dur, 0.01), 3),
            "runs": _count_runs([float(c.get("timeline_in_sec") or 0.0) for c in cut],
                                [float(c.get("timeline_out_sec") or 0.0) for c in cut]),
        }
        m["speed"] = {"segments": sum(1 for c in clips if abs(float(c.get("speed") or 1.0) - 1.0) > 1e-6)}
        m["transition"] = {"segments": sum(1 for c in clips if str(c.get("transition") or "none") not in ("none", "", "None"))}
        m["pip"] = {"segments": sum(1 for c in clips if (c.get("pip_layout") or {}).get("mode"))}
        m["sticker"] = {"segments": sum(1 for c in clips if c.get("sticker") or c.get("sticker_layers"))}
    if plan:
        m["color_grade"] = {"lut_strength": plan.get("lut_strength")}
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


def check(pipeline_dir: Path) -> int:
    rules = load_rules()
    style_id = ""
    applied = _load(pipeline_dir / "style_spectrum_applied.json") or {}
    style_id = str(applied.get("style_id") or (_load(pipeline_dir / "06_packaging.json") or {}).get("style_id") or "")
    if not style_id:
        raise SystemExit("管道里读不到 style_id（style_spectrum_applied.json / 06_packaging.json）")
    node = style_node(rules, style_id)
    meas = measure(pipeline_dir)

    rows: List[Tuple[str, str, str, str, str]] = []  # 元素 / 观测 / 真值 / 判定 / 依据
    fails = 0

    def add(el_name: str, what: str, obs: Any, target: Any, verdict: str, why: str) -> None:
        nonlocal fails
        if verdict == "FAIL":
            fails += 1
        rows.append((el_name, what, str(obs), str(target), f"{verdict}｜{why}"))

    for key, el in (node.get("elements") or {}).items():
        obs = meas.get(key)
        if not el.get("present"):
            if obs and (obs.get("segments") or obs.get("count") or obs.get("seconds")):
                add(key, "真值里不出现，产物里出现了", json.dumps(obs, ensure_ascii=False),
                    "0", "FAIL", f"{el.get('absent_reason')}")
            continue
        if not obs:
            continue
        lim_hit = _limit(el, "max_per_min", "per_min", "sfx_per_min", "density_per_min")
        if lim_hit is not None and obs.get("per_min") is not None:
            try:
                cap = float(lim_hit)
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
        if key == "cutout":
            share_lim = _limit(el, "max_coverage_ratio", "coverage_ratio", "max_share_of_person_layer")
            if share_lim is not None and str(share_lim).replace(".", "").isdigit():
                try:
                    ok = float(obs["coverage"]) <= float(share_lim) + 1e-6
                    add(key, "抠像覆盖占比", obs["coverage"], share_lim, "PASS" if ok else "FAIL",
                        "抠像是段落级语言，占比不该压满全片" if not ok else "在真值占比以内")
                except (TypeError, ValueError):
                    pass
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

    print(f"[element_rules] {style_id} · 观测元素 {sum(1 for k in meas if k in (node.get('elements') or {}))} 个")
    width = max((len(r[0]) for r in rows), default=8)
    print(f"{'元素'.ljust(width)} | 指标 | 产物 | 真值 | 判定")
    for r in rows:
        print(f"{r[0].ljust(width)} | {r[1]} | {r[2]} | {r[3]} | {r[4]}")
    print(f"[element_rules] {'PASS' if fails == 0 else f'{fails} 项偏离'}")
    return 0 if fails == 0 else 1


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
