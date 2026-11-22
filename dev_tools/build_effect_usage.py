#!/usr/bin/env python3
"""画面特效（video_effects）真值提取 + 回写 color_effects.json。

为什么要有这一步：之前 `color_effects.json` 的 `effects` 是按风格命名「推测」出来的，
只要名字里有「暗灰」「方角」就填一个 `irregular_black_frame`，导致源里根本没加特效的风格
也被压了暗角/噪点。剪映草稿里的真值很干脆：`materials.video_effects` 里放了哪些特效、
挂在哪条 effect 轨的哪个时间段、`adjust_params` 里的参数值是多少。

本脚本只做两件事：
1. 从解密草稿里把 24 套的**真实特效**（名称 + 时间窗 + 参数）抽成 `forensic/effect_usage.json`；
2. 按参数映射改写 `color_effects.json` 每个风格的 `effects` 块：源里没有的一律 false，
   源里有的带上 `source_effect`/`source_params`/`window`，供渲染时按时间窗生效。

用法：
    python3 dev_tools/build_effect_usage.py            # 需要 forensic/decrypted + style_sources.json
    python3 dev_tools/build_effect_usage.py --check    # 只校验不回写
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
FORENSIC = ROOT / "forensic"
COLOR_EFFECTS = ROOT / "short-video-studio" / "assets" / "style_spectrum" / "color_effects.json"

# 剪映特效名 → 我们的滤镜键 + 参数换算
EFFECT_MAP: Dict[str, Dict[str, Any]] = {
    "暗角": {"key": "vignette", "params": ("effects_adjust_texture",)},
    "模糊": {"key": "blur", "params": ("effects_adjust_blur",)},
    "聚光灯": {"key": "spotlight", "params": ("effects_adjust_speed",)},
    "不规则黑框": {"key": "irregular_black_frame", "params": ("effects_adjust_background_animation",)},
    "怀旧边框": {"key": "film_frame",
             "params": ("effects_adjust_size", "effects_adjust_vertical_shift",
                        "effects_adjust_intensity", "effects_adjust_filter")},
    "荧幕噪点": {"key": "grain", "params": ("effects_adjust_speed",)},
    "颤抖": {"key": "shake", "params": ("effects_adjust_range", "effects_adjust_speed")},
}


def _draft_path(project: str) -> Optional[Path]:
    for name in ("draft_content.json", "__latest_close.json", "template-2.tmp"):
        p = FORENSIC / "decrypted" / project / name
        if p.is_file():
            return p
    return None


def _num(params: Dict[str, Any], key: str, default: float) -> float:
    val = params.get(key)
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def build_usage() -> Dict[str, Any]:
    sources = json.loads((FORENSIC / "style_sources.json").read_text(encoding="utf-8"))["styles"]
    styles: Dict[str, Any] = {}
    for sid in sorted(sources):
        project = sources[sid]
        path = _draft_path(project)
        if path is None:
            raise SystemExit(f"{sid} 找不到解密草稿：{project}")
        draft = json.loads(path.read_text(encoding="utf-8"))
        mats = {m["id"]: m for m in (draft.get("materials", {}).get("video_effects") or [])}
        timeline_dur = float(draft.get("duration") or 0) / 1_000_000.0
        used: List[Dict[str, Any]] = []
        for track in draft.get("tracks") or []:
            if track.get("type") != "effect":
                continue
            for seg in track.get("segments") or []:
                mat = mats.get(seg.get("material_id"))
                if not mat:
                    continue
                tr = seg.get("target_timerange") or {}
                params = {str(p.get("name")): p.get("value") for p in (mat.get("adjust_params") or [])}
                used.append({
                    "source_effect": str(mat.get("name") or ""),
                    "start": round(float(tr.get("start") or 0) / 1_000_000.0, 3),
                    "duration": round(float(tr.get("duration") or 0) / 1_000_000.0, 3),
                    "source_params": params,
                    "effect_id": str(mat.get("resource_id") or ""),
                })
        used.sort(key=lambda e: e["start"])
        styles[sid] = {"project": project, "timeline_dur": round(timeline_dur, 2), "effects": used}
    return {"version": "1.0.0",
            "source": "materials.video_effects + effect 轨 target_timerange（解密草稿直读）",
            "count": len(styles), "styles": styles}


def to_filter(entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """剪映特效条目 → 我们的滤镜参数对象（含时间窗）。无法映射的返回 None。"""
    name = entry["source_effect"]
    spec = EFFECT_MAP.get(name)
    if not spec:
        return None
    prm = entry.get("source_params") or {}
    window = {"start": entry["start"], "duration": entry["duration"]}
    base = {"source_effect": name, "source_params": prm, "window": window}
    key = spec["key"]
    if key == "vignette":
        texture = _num(prm, "effects_adjust_texture", 0.5)
        return {**base, "angle": round(0.45 + 0.55 * texture, 2),
                "x": 540, "y": 960}
    if key == "blur":
        strength = _num(prm, "effects_adjust_blur", 0.5)  # 源里可能是 null → 取默认 0.5
        return {**base, "sigma": round(1.0 + 14.0 * strength, 1), "whole_frame": True}
    if key == "spotlight":
        speed = _num(prm, "effects_adjust_speed", 0.33)
        return {**base, "angle": round(0.5 + 0.4 * speed, 2), "x": 540, "y": 960,
                "inner_frac": 0.34, "feather_frac": 0.62}
    if key == "irregular_black_frame":
        anim = _num(prm, "effects_adjust_background_animation", 1.0)
        edge = int(round(10 + 22 * anim))
        return {**base, "edges": {"top": edge, "bottom": edge + 6,
                                  "left": edge - 4, "right": edge - 4},
                "color": "0x0B0B0B"}
    if key == "film_frame":
        size = _num(prm, "effects_adjust_size", 0.5)
        shift = _num(prm, "effects_adjust_vertical_shift", 0.44)
        return {**base, "style": "black_polaroid" if shift >= 0.35 else "black",
                "border_frac": round(0.012 + 0.05 * size, 3),
                "color": "0xE9E2D4"}
    if key == "grain":
        speed = _num(prm, "effects_adjust_speed", 0.33)
        return {**base, "strength": int(round(4 + 10 * speed)), "flags": "t+u"}
    if key == "shake":
        rng = _num(prm, "effects_adjust_range", 0.09)
        speed = _num(prm, "effects_adjust_speed", 0.12)
        amp = max(1, int(round(rng * 45)))
        return {**base, "amp_x": amp, "amp_y": amp, "speed": round(0.15 + 3.0 * speed, 2)}
    return None


def rewrite_color_effects(usage: Dict[str, Any], check_only: bool = False) -> List[str]:
    doc = json.loads(COLOR_EFFECTS.read_text(encoding="utf-8"))
    issues: List[str] = []
    keys = ("vignette", "blur", "spotlight", "irregular_black_frame", "film_frame", "grain", "shake")
    for sid, node in doc["styles"].items():
        use = (usage["styles"].get(sid) or {}).get("effects") or []
        effects: Dict[str, Any] = {k: False for k in keys}
        mapped: List[str] = []
        for entry in use:
            val = to_filter(entry)
            if val is None:
                issues.append(f"{sid}: 未能映射源特效「{entry['source_effect']}」")
                continue
            key = EFFECT_MAP[entry["source_effect"]]["key"]
            if isinstance(effects.get(key), dict):
                # 同种特效出现多次：留时间最早的一段，其余记入 extra_windows
                effects[key].setdefault("extra_windows", []).append(val["window"])
                continue
            effects[key] = val
            mapped.append(entry["source_effect"])
        effects["source_effects"] = mapped
        if not mapped:
            effects["evidence_cn"] = "源草稿 effect 轨为空：本风格不放任何画面特效"
        else:
            effects["evidence_cn"] = (
                "源草稿 effect 轨实测：" + "、".join(
                    f"{e['source_effect']}({e['start']:.2f}s 起 {e['duration']:.2f}s)" for e in use))
        node["effects"] = effects
    if not check_only:
        COLOR_EFFECTS.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return issues


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    usage = build_usage()
    if not args.check:
        (FORENSIC / "effect_usage.json").write_text(
            json.dumps(usage, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    used = {sid: [e["source_effect"] for e in node["effects"]]
            for sid, node in usage["styles"].items() if node["effects"]}
    print(f"effect_usage: {usage['count']} 套，其中 {len(used)} 套真的有画面特效")
    for sid, names in used.items():
        print(f"  {sid}: {names}")
    issues = rewrite_color_effects(usage, check_only=args.check)
    if issues:
        print("映射告警：" + "；".join(issues))
    else:
        print("color_effects.effects 已按源特效轨回写，无未映射项" if not args.check
              else "color_effects.effects 与源特效轨一致")
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
