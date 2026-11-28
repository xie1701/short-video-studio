#!/usr/bin/env python3
"""
测「字幕到底长什么样」的真值：描边还是阴影？

起因（用户实测 2026-11-23）：
  「style_01 的字幕是深灰阴影，而实际执行却变成了描黑边。」
追查发现库里 24 套的 `stroke_width` 全是同一个假值：
  build_style_library_v4.py 里 `stroke_width = sub_stroke_ratio * 5`，
而 `sub_stroke_ratio` 又把剪映文字素材的 `border_width`（面板上描边滑杆的
记忆值，默认 0.08，几乎每段文字都有）当成了「有描边」。
于是 24 套全部被画上 5px 黑边，而真草稿里绝大多数根本没有描边、用的是阴影。

本脚本只认剪映草稿 JSON 里真正决定渲染的两个数组：
  styles[].strokes  → [{content:{solid:{color}}, width, ...}]   width 是相对字号的比例
  styles[].shadows  → [{content:{solid:{color}}, distance, alpha, diffuse, angle}]
以及素材级的 has_shadow / shadow_color / shadow_alpha / shadow_distance /
shadow_smoothing / shadow_angle / shadow_point。

单位换算：草稿字号 size(unit) × PX_PER_UNIT(4.0) = 1080p 像素。
  shadow.distance 与 size 同一单位制，故 offset_px = distance × 4.0
  stroke.width 是相对字号比例，故 stroke_px = width × font_size_px

产物：forensic/text_style_truth.json
  { "PX_PER_UNIT": 4.0,
    "all_styles": {"total": N, "with_real_stroke": n, "with_shadow": n},
    "projects": { "<draft 名>": { font_size_units, stroke_mode, stroke_ratio,
                                  stroke_width_rel, stroke_color, stroke_px,
                                  shadow_ratio, shadow:{enabled,color,alpha,distance_units,
                                                        offset_px,blur_px,angle_deg} } } }

用法：
  JY_DRAFT_ROOT=<剪映草稿目录> python3 dev_tools/measure_text_style_truth.py   # 全量
  python3 dev_tools/measure_text_style_truth.py --limit 5       # 抽查
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "forensic" / "text_style_truth.json"
SOURCE_MAP = ROOT / "forensic" / "style_sources.json"

PX_PER_UNIT = 4.0


def draft_root() -> Path:
    """草稿根目录只从环境变量取：本机取证路径不写进仓库（隐私闸门会拦）。"""
    env = os.environ.get("JY_DRAFT_ROOT")
    if not env:
        raise SystemExit("需要 JY_DRAFT_ROOT 环境变量指向剪映草稿目录（本脚本只读它，不内置本机路径）")
    return Path(env).expanduser()


def decrypted_dir() -> Path:
    return ROOT / "forensic" / "decrypted"


def rgb01_to_hex(c: Any) -> str:
    try:
        return "#{:02X}{:02X}{:02X}".format(*[int(round(float(v) * 255)) for v in c[:3]])
    except Exception:
        return "#000000"


def _solid_hex(obj: Any) -> Optional[str]:
    try:
        sol = ((obj.get("content") or {}).get("solid") or {})
        col = sol.get("color")
        if col:
            return rgb01_to_hex(col)
    except Exception:
        return None
    return None


def parse_style_block(st: Dict[str, Any]) -> Dict[str, Any]:
    """拆一个 styles[] 块：填色 / 字号 / 真描边 / 真阴影。"""
    size = st.get("size")
    size = float(size) if isinstance(size, (int, float)) else None
    fill = _solid_hex(st.get("fill") or {}) if st.get("fill") else None

    strokes: List[Dict[str, Any]] = []
    for sk in st.get("strokes") or []:
        try:
            w = float(sk.get("width") or 0.0)
        except Exception:
            w = 0.0
        if w <= 0:
            continue
        strokes.append({"width_rel": round(w, 4), "color": _solid_hex(sk) or "#000000"})

    shadows: List[Dict[str, Any]] = []
    for sh in st.get("shadows") or []:
        try:
            dist = float(sh.get("distance") or 0.0)
            alpha = float(sh.get("alpha") if sh.get("alpha") is not None else 1.0)
            diffuse = float(sh.get("diffuse") or 0.0)
            angle = float(sh.get("angle") or -45.0)
        except Exception:
            continue
        shadows.append({
            "color": _solid_hex(sh) or "#000000",
            "distance_units": round(dist, 3),
            "alpha": round(alpha, 3),
            "diffuse": round(diffuse, 4),
            "angle_deg": round(angle, 1),
        })

    return {"size_units": size, "fill": fill, "strokes": strokes, "shadows": shadows}


def load_draft(path_dir: Path) -> Optional[Dict[str, Any]]:
    for name in ("draft_content.json", "__latest_close.json"):
        p = path_dir / name
        if p.is_file():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
    return None


def subtitle_materials(data: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """按与取证脚本同一口径挑出字幕轨，返回它的文字素材列表与轨迹摘要。"""
    tracks = (((data.get("tracks")) or []))
    mats = ((data.get("materials") or {}).get("texts") or [])
    txt_map = {m.get("id"): m for m in mats}
    text_tracks = [t for t in tracks if t.get("type") == "text" and (t.get("segments") or [])]
    best: Tuple[float, List[Dict[str, Any]], Dict[str, Any]] = (-1.0, [], {})
    for tt in text_tracks:
        segs = tt.get("segments") or []
        types: Counter = Counter()
        ys: List[float] = []
        picked: List[Dict[str, Any]] = []
        for seg in segs:
            tr = seg.get("target_timerange") or {}
            trans = (seg.get("clip") or {}).get("transform") or {}
            y_scr = (1.0 - float(trans.get("y", 0.0))) / 2.0
            ys.append(y_scr)
            m = txt_map.get(seg.get("material_id")) or {}
            types[str(m.get("type") or "text")] += 1
            if m:
                picked.append(m)
        if not segs:
            continue
        med_y = statistics.median(ys) if ys else 0.5
        y_std = statistics.pstdev(ys) if len(ys) > 1 else 0.0
        is_sub = (types.get("subtitle", 0) >= len(segs) * 0.5) or (len(segs) >= 10 and med_y >= 0.62 and y_std <= 0.08)
        if not is_sub:
            continue
        score = float(len(segs))
        if score > best[0]:
            best = (score, picked, {"seg_count": len(segs), "med_y": round(med_y, 3), "types": dict(types)})
    if best[0] < 0:
        # 没有像样的字幕轨：退回全部文字素材（如 style_15 源草稿那种 0 条基础字幕的项目）
        return mats, {"seg_count": len(mats), "med_y": None, "types": {"fallback_all_texts": len(mats)}}
    return best[1], best[2]


def _aggregate(blocks: List[Dict[str, Any]], mat_level: List[Dict[str, Any]]) -> Dict[str, Any]:
    """从一批 style 块里算「描边/阴影」签名（字幕与花字共用同一口径）。"""
    total = len(blocks)
    with_stroke = sum(1 for b in blocks if b["strokes"])
    with_shadow = sum(1 for b in blocks if b["shadows"])
    sizes = [b["size_units"] for b in blocks if b["size_units"]]
    font_size_units = round(statistics.median(sizes), 2) if sizes else None
    font_size_px = round(font_size_units * PX_PER_UNIT) if font_size_units else None

    stroke_pairs: List[Tuple[float, str]] = []
    for b in blocks:
        for sk in b["strokes"]:
            stroke_pairs.append((sk["width_rel"], sk["color"]))
    stroke_mode = "stroke" if with_stroke >= max(1, total * 0.25) else "none"
    if stroke_pairs and stroke_mode == "stroke":
        width_rel = round(statistics.median([x[0] for x in stroke_pairs]), 4)
        stroke_color = Counter([x[1] for x in stroke_pairs]).most_common(1)[0][0]
    else:
        width_rel, stroke_color = 0.0, None
    stroke_px = int(round((width_rel or 0.0) * (font_size_px or 0))) if stroke_mode == "stroke" else 0

    sh_list: List[Dict[str, Any]] = []
    for b in blocks:
        sh_list.extend(b["shadows"])
    src = "styles.shadows"
    if not sh_list and mat_level:
        sh_list = [x for x in mat_level if x["has_shadow"]]
        src = "material_fields"
    shadow: Dict[str, Any] = {"enabled": False}
    if sh_list:
        dists = [float(x["distance_units"]) for x in sh_list]
        alphas = [float(x["alpha"]) for x in sh_list]
        diffuses = [float(x.get("diffuse") if x.get("diffuse") is not None else (x.get("smoothing") or 0.0)) for x in sh_list]
        angles = [float(x["angle_deg"]) for x in sh_list]
        colors = [str(x["color"]) for x in sh_list]
        dist_u = round(statistics.median(dists), 3)
        alpha = round(statistics.median(alphas), 3)
        diffuse = round(statistics.median(diffuses), 4)
        angle = round(statistics.median(angles), 1)
        color = Counter(colors).most_common(1)[0][0]
        rad = math.radians(angle)
        dx = round(math.cos(rad) * dist_u * PX_PER_UNIT)
        dy = round(-math.sin(rad) * dist_u * PX_PER_UNIT)
        blur_px = round(max(0.0, diffuse) * (font_size_px or 48) * 0.9, 1)
        shadow = {
            "enabled": True, "color": color, "alpha": alpha, "distance_units": dist_u,
            "offset_px": [int(dx), int(dy)], "blur_px": blur_px, "angle_deg": angle,
            "source": src, "ratio_measured": round(len(sh_list) / max(total, 1), 3),
        }
    return {
        "style_blocks": total,
        "font_size_units": font_size_units,
        "font_size_1080p_px": font_size_px,
        "stroke_mode": stroke_mode,
        "stroke_ratio": round(with_stroke / total, 3) if total else 0.0,
        "stroke_blocks": with_stroke,
        "stroke_width_rel": width_rel,
        "stroke_width_px": stroke_px,
        "stroke_color": stroke_color,
        "shadow_ratio": round(with_shadow / total, 3) if total else 0.0,
        "shadow": shadow,
    }


def flower_text_materials(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """非字幕的文字素材（花字/大字/顶栏）：字幕轨以外的所有 text 轨。"""
    tracks = data.get("tracks") or []
    mats = (data.get("materials") or {}).get("texts") or []
    txt_map = {m.get("id"): m for m in mats}
    out: List[Dict[str, Any]] = []
    for tt in tracks:
        if tt.get("type") != "text":
            continue
        segs = tt.get("segments") or []
        if not segs:
            continue
        types: Counter = Counter()
        ys: List[float] = []
        for seg in segs:
            m = txt_map.get(seg.get("material_id")) or {}
            types[str(m.get("type") or "text")] += 1
            ys.append((1.0 - float(((seg.get("clip") or {}).get("transform") or {}).get("y", 0.0))) / 2.0)
        med_y = statistics.median(ys) if ys else 0.5
        if types.get("subtitle", 0) >= len(segs) * 0.5:
            continue
        if len(segs) >= 10 and med_y >= 0.62:
            continue
        for seg in segs:
            m = txt_map.get(seg.get("material_id"))
            if m:
                out.append(m)
    return out


def _blocks_of(materials: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    blocks: List[Dict[str, Any]] = []
    mat_level: List[Dict[str, Any]] = []
    for m in materials:
        raw = m.get("content")
        if isinstance(raw, str) and raw.strip().startswith("{"):
            try:
                js = json.loads(raw)
            except Exception:
                js = {}
            for st in js.get("styles") or []:
                rng = st.get("range") or []
                if len(rng) == 2 and float(rng[1]) - float(rng[0]) <= 0:
                    continue
                blocks.append(parse_style_block(st))
        if m.get("has_shadow") or m.get("shadow_distance") is not None:
            mat_level.append({
                "has_shadow": bool(m.get("has_shadow")),
                "color": str(m.get("shadow_color") or "#000000"),
                "alpha": round(float(m.get("shadow_alpha") or 0.0), 3),
                "distance_units": round(float(m.get("shadow_distance") or 0.0), 3),
                "smoothing": round(float(m.get("shadow_smoothing") or 0.0), 3),
                "angle_deg": round(float(m.get("shadow_angle") if m.get("shadow_angle") is not None else -45.0), 1),
            })
    return blocks, mat_level


def measure_project(name: str, data: Dict[str, Any]) -> Dict[str, Any]:
    picked, track_info = subtitle_materials(data)
    agg = _aggregate(*_blocks_of(picked))
    # 花字（非字幕文字轨）同样量一份：花字实测也没有描边
    flower = _aggregate(*_blocks_of(flower_text_materials(data)))
    return {
        "draft": name,
        "subtitle_track": track_info,
        "style_blocks": agg["style_blocks"],
        "font_size_units": agg["font_size_units"],
        "font_size_1080p_px": agg["font_size_1080p_px"],
        "stroke_mode": agg["stroke_mode"],
        "stroke_ratio": agg["stroke_ratio"],
        "stroke_blocks": agg["stroke_blocks"],
        "stroke_width_rel": agg["stroke_width_rel"],
        "stroke_width_px": agg["stroke_width_px"],
        "stroke_color": agg["stroke_color"],
        "shadow_ratio": agg["shadow_ratio"],
        "shadow": agg["shadow"],
        "flower": flower,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="从解密草稿量字幕真值：描边还是阴影")
    ap.add_argument("--limit", type=int, default=0, help="只测前 N 个草稿（抽查用）")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()

    base = decrypted_dir()
    if not base.is_dir():
        raise SystemExit(f"找不到解密目录：{base}（先跑 forensic/jy_draft_decrypt.py）")

    projects: Dict[str, Any] = {}
    dirs = sorted([d for d in base.iterdir() if d.is_dir()])
    if args.limit:
        dirs = dirs[: args.limit]
    for d in dirs:
        data = load_draft(d)
        if not data:
            continue
        try:
            projects[d.name] = measure_project(d.name, data)
        except Exception as exc:  # 单个项目坏掉不影响全量
            projects[d.name] = {"draft": d.name, "error": f"{type(exc).__name__}: {exc}"}

    ok = [v for v in projects.values() if "error" not in v]
    tot_blocks = sum(v["style_blocks"] for v in ok)
    tot_stroke = sum(v["stroke_blocks"] for v in ok)
    with_shadow_projects = sum(1 for v in ok if (v.get("shadow") or {}).get("enabled"))
    doc = {
        "note": "字幕描边/阴影真值。只认 styles[].strokes / styles[].shadows，"
                "素材级 border_width 是剪映面板滑杆的记忆值，不代表真实描边，已弃用。",
        "px_per_unit": PX_PER_UNIT,
        "generated_from": "forensic/decrypted/<草稿>/draft_content.json",
        "all_styles": {
            "projects": len(ok),
            "text_style_blocks": tot_blocks,
            "with_real_stroke": tot_stroke,
            "real_stroke_ratio": round(tot_stroke / max(tot_blocks, 1), 4),
            "projects_with_shadow": with_shadow_projects,
            "projects_without_stroke": sum(1 for v in ok if v["stroke_mode"] == "none"),
            "flower_style_blocks": sum(v["flower"]["style_blocks"] for v in ok),
            "flower_with_real_stroke": sum(v["flower"]["stroke_blocks"] for v in ok),
            "projects_with_flower_shadow": sum(1 for v in ok if (v["flower"].get("shadow") or {}).get("enabled")),
        },
        "projects": projects,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"[text-truth] {len(ok)} 个草稿 · {tot_blocks} 个字样式 · 真描边 {tot_stroke} 个"
          f"（{doc['all_styles']['real_stroke_ratio'] * 100:.1f}%）· 有阴影的项目 {with_shadow_projects}/{len(ok)}")
    src_map = {}
    if SOURCE_MAP.is_file():
        src_map = json.loads(SOURCE_MAP.read_text(encoding="utf-8")).get("styles") or {}
        src_map = {k: (v if isinstance(v, str) else v.get("draft")) for k, v in src_map.items()}
    if src_map:
        print(f"{'风格':<10} {'草稿':<14} {'字号px':>6} {'描边':>12} {'阴影':>34}")
        for sid in sorted(src_map):
            v = projects.get(src_map[sid]) or {}
            if not v or "error" in v:
                print(f"{sid:<10} {src_map[sid][:12]:<14} 缺失")
                continue
            sh = v.get("shadow") or {}
            stroke_desc = f"无" if v["stroke_mode"] == "none" else f"{v['stroke_width_px']}px {v['stroke_color']}"
            sh_desc = ("无" if not sh.get("enabled") else
                       f"{sh['color']} a={sh['alpha']} 偏移{sh['offset_px']} 虚化{sh['blur_px']}px")
            print(f"{sid:<10} {src_map[sid][:12]:<14} {str(v.get('font_size_1080p_px')):>6} {stroke_desc:>12} {sh_desc:>34}")
    print(f"[text-truth] 写出 -> {args.out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
