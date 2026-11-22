#!/usr/bin/env python3
"""把 24 套的画中画/叠加构图按真草稿重排进库与蓝图。

以前 `pip_mode` / `pip_composition_spec` 是手写的，只有 11 套有值、13 套写 `none`；
而真草稿里 24 套**都有**叠加视频轨（1–9 条），19 套有人像/物体抠像，主轨还各不相同
（style_19 主轨推近 1.3、style_21 1.2、style_23 1.1、style_08 缩到 0.9、style_09 缩到 0.6 并偏到左下）。

本脚本按 `forensic/pip_usage.json`（由 extract_pip_truth.py 从草稿直读）重写：

- 库 `packaging_techniques.pip_mode` → 真值分类
- 库 `pip_composition_spec` → `{mode: geometry_pip, main:{x,y,scale}, windows:[...], cutout, stroke}`
- 蓝图 `pip_spec` → 同一份几何（渲染端按数字摆，不再按风格名字猜版面）

用法：
    python3 dev_tools/apply_pip_truth.py            # 回写
    python3 dev_tools/apply_pip_truth.py --check    # 只比对
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
FORENSIC = ROOT / "forensic"
LIB = ROOT / "short-video-studio" / "assets" / "style_spectrum" / "style_spectrum_library.json"
BP = ROOT / "short-video-studio" / "assets" / "style_spectrum" / "template_blueprints.json"


def _styles(doc: Dict[str, Any]) -> Dict[str, Any]:
    st = doc["styles"]
    return st if isinstance(st, dict) else {s["style_id"]: s for s in st}


def _windows(node: Dict[str, Any], limit: int = 3) -> List[Dict[str, Any]]:
    """所有出现过的非满屏叠加几何，按出现段数排序（只看主导几何会漏掉少数段的小窗）。"""
    seen: Dict[tuple, Dict[str, Any]] = {}
    for ov in node["overlays"]:
        stroke = ov.get("matting_stroke") or {}
        color = stroke.get("color")
        hex_color = None
        if isinstance(color, list) and len(color) >= 3:
            hex_color = "#" + "".join(f"{int(round(float(c) * 255)):02X}" for c in color[:3])
        for g in ov.get("geometries") or []:
            if g["scale"] >= 0.999 and abs(g["x"]) < 0.02 and abs(g["y"]) < 0.02:
                continue
            key = (g["x"], g["y"], g["scale"])
            if key not in seen:
                seen[key] = {"x": g["x"], "y": g["y"], "scale": g["scale"],
                             "segments": 0, "matting": bool(ov.get("matting")),
                             "stroke_hex": hex_color}
            seen[key]["segments"] += 1
    out = sorted(seen.values(), key=lambda w: -w["segments"])
    return out[:limit]


def build_spec(node: Dict[str, Any]) -> Dict[str, Any]:
    main = node["main"]
    wins = _windows(node)
    cut = bool(node.get("main_matting") or any(o.get("matting") for o in node["overlays"]))
    spec: Dict[str, Any] = {
        "mode": "geometry_pip",   # 具体变体（window/shrunk/punch_in/full）在 mode_name() 里回填
        "main": {"x": main["x"], "y": main["y"], "scale": main["scale"]},
        "windows": wins,
        "cutout": cut,
        "single_source": not bool(wins),
        "source": "forensic/pip_usage.json",
        "note_cn": "几何全部来自真草稿 clip.transform / uniform_scale；渲染端按数字摆，不按风格名字猜",
    }
    if wins and wins[0].get("stroke_hex"):
        spec["border_hex"] = wins[0]["stroke_hex"]
    return spec


def mode_name(node: Dict[str, Any], spec: Dict[str, Any]) -> str:
    m = spec["main"]
    if m["scale"] > 1.001:
        return "geometry_pip_main_punch_in"
    if m["scale"] < 0.999:
        return "geometry_pip_main_shrunk"
    if spec["windows"]:
        return "geometry_pip_window"
    return "geometry_pip_full"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    usage = json.loads((FORENSIC / "pip_usage.json").read_text(encoding="utf-8"))["styles"]
    lib = json.loads(LIB.read_text(encoding="utf-8"))
    bp = json.loads(BP.read_text(encoding="utf-8"))
    styles = _styles(lib)
    blueprints = bp["blueprints"]

    drift: List[str] = []
    for sid in sorted(usage):
        node = usage[sid]
        spec = build_spec(node)
        mode = mode_name(node, spec)
        entry = styles[sid]
        tech = entry.setdefault("packaging_techniques", {})
        spec["mode"] = mode
        if args.check:
            if tech.get("pip_mode") != mode or entry.get("pip_composition_spec") != spec:
                drift.append(sid)
            continue
        tech["pip_mode"] = mode
        spec["mode"] = mode
        entry["pip_composition_spec"] = spec
        node_bp = blueprints.setdefault(sid, {})
        spec["mode"] = mode
        node_bp["pip_spec"] = dict(spec)
        node_bp["allow_explainer_pip"] = bool(spec["windows"])

    if args.check:
        print("pip 真值回写" + ("：一致" if not drift else f"：漂移 {sorted(drift)}"))
        return 1 if drift else 0

    LIB.write_text(json.dumps(lib, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    BP.write_text(json.dumps(bp, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{'style':9} {'pip_mode':30} 主轨(x,y,scale)            叠加窗数")
    for sid in sorted(usage):
        spec = styles[sid]["pip_composition_spec"]
        m = spec["main"]
        print(f"{sid:9} {styles[sid]['packaging_techniques']['pip_mode']:30} "
              f"({m['x']},{m['y']},{m['scale']})".ljust(60) + f"{len(spec['windows'])}")
    print(f"\n回写 {LIB.name} 与 {BP.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
