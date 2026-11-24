#!/usr/bin/env python3
"""字幕排版单源重建器：把三处系统性偏差从库里重算，并同步到 templates.json / 蓝图。

用户实测反馈（2026-11-24）：
  「字幕的字体的投影偏移过多，都有影响观感！」+ 字幕整体偏小。

查明的三处偏差与修复：

1) 字号换算基准错了（根因）
   库里每套 subtitle_spec 同时有 `font_size_jianying_units`（剪映草稿字号单位）和
   `px_per_unit_at_1080 = 4.0`，templates.json 的 font_size = units × 4.0。
   正确基准是 **canvas_w / 172.8 = 6.25**（1080 宽），这条换算在 replica_template.py 里
   已由文字图层 content.styles[0].size 反解验证过（192 units ↔ 1080 宽画布）。
   实测（原版成片墨高 vs 我们渲染）：style_21 70px vs 48px、style_17 100px vs 60px、
   style_01 64px vs 48px → 全片字号偏小 1.4~1.6 倍，24 套全部按新基准重算。

2) 字体族取错
   唯一真源是每套模板的 `ass_style`（`Style: Default,<Fontname>,...`）——剪映导出/预览都按它
   排字；草稿里的 font_path 常被前一套的思源宋体残留覆盖，不能当证据。
   例：style_21 的 font_candidates[0] 是 SourceHanSerifCN-Heavy.otf（宋体），而 ass_style 声明的是
   `Alimama ShuHeiTi`、commercial_safe_font 也是 AlimamaShuHeiTi-Bold.otf —— 实测原版是粗黑体，
   我们渲染成了细宋体。这里按 `assets/fonts/font_aliases.json` 的 `ass_style_families` 映射，
   命中就排到 font_candidates[0]；映射不到才保留原顺序（并在报告里点名）。

3) 投影偏移过大 + 虚化不足
   旧模板 shadow_offset≈[14,14]（合成幅度 19.8px）、shadow_blur_px≈1.1（硬得像描边）。
   实测（style_21 原版成片 540×960 预览取样 ×2，字形掩膜 vs 暗像素掩膜做互相关）：
   最佳位移 dx≈-5/dy≈+3 → 1080 宽下合成幅度 ≈11.7px；草稿真值 distance=5.0、angle=-45°
   → **2.3px 每 distance 单位（1080 宽）**。
   虚化σ = `smoothing × font_px × 0.13`（font_px=75 时 σ≈4.4px），与
   dev_tools/render_replica_from_draft.py 的 SHADOW_BLUR_EM 同一口径。

用法（幂等，可重复跑；不加 --write 只打印差异）：
  python3 dev_tools/rebuild_subtitle_typography.py                 # 干跑 diff
  python3 dev_tools/rebuild_subtitle_typography.py --write         # 落库 + 同步模板/蓝图
  python3 dev_tools/rebuild_subtitle_typography.py --write \
      --pipeline-dir <项目>/pipeline                               # 顺带把已跑过的管线对齐

注意：`build_style_library_v4.py` 从草稿重建库时会把 font_size_1080p_px / shadow_offset 写回旧的
派生值，所以那次重建之后必须再跑一次本脚本（它是排版三件事（字号/字体/阴影）的单源）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "short-video-studio"
ASSETS = SKILL / "assets"
LIB_PATH = ASSETS / "style_spectrum" / "style_spectrum_library.json"
TPL_PATH = ASSETS / "subtitle_templates" / "templates.json"
BP_PATH = ASSETS / "style_spectrum" / "template_blueprints.json"
ALIAS_PATH = ASSETS / "fonts" / "font_aliases.json"

# ── 换算基准（三条都写成常量，改一处就能全库重算）──
CANVAS_W = 1080
PX_PER_UNIT_AT_1080 = CANVAS_W / 172.8          # = 6.25，剪映字号单位 → 1080 宽像素
SHADOW_DIST_PX_PER_UNIT = 2.3                    # 1080 宽下每个 shadow_distance 单位的合成幅度
SHADOW_BLUR_EM = 0.13                            # σ = smoothing × font_px × 0.13
SUBTITLE_SYMMETRIC_MAX_W = 640                   # [220, 860] 对称安全带
DEFAULT_SHADOW_POINT = {"x": 0.636396103067893, "y": -0.636396103067893}  # -45°
DEFAULT_SHADOW_SMOOTHING = 0.45                  # 24 套里 21 套的草稿真值

TMPL_SYNC_KEYS = (
    "font_size",
    "font_candidates",
    "shadow_offset",
    "shadow_blur_px",
    "shadow_distance_units",
    "shadow_point",
    "shadow_smoothing",
    "shadow_alpha",
    "ass_font_family",
)


def read_json(p: Path) -> Any:
    return json.loads(p.read_text(encoding="utf-8"))


def write_json(p: Path, data: Any) -> None:
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_ass_font_family(ass_style: str) -> Optional[str]:
    """从 ASS Style 行取字体族名：`Style: Default,<Fontname>,<Size>,...`。"""
    line = str(ass_style or "").strip()
    if not line.lower().startswith("style:"):
        return None
    parts = [p.strip() for p in line.split(",")]
    if len(parts) < 2 or not parts[1]:
        return None
    return parts[1]


def ass_family_table() -> Dict[str, str]:
    """ASS 族名 → 随包字体文件名（不含 assets/fonts/ 前缀）。"""
    raw = read_json(ALIAS_PATH).get("ass_style_families") or {}
    out: Dict[str, str] = {}
    for fam, ent in raw.items():
        f = (ent or {}).get("file")
        if not f:
            continue
        p = SKILL / str(f)
        if not p.is_file():
            p = ROOT / str(f)
        if p.is_file():
            out[str(fam)] = p.name
    return out


def normalize_family(name: str) -> str:
    return "".join(ch for ch in str(name or "").casefold() if ch.isalnum())


def resolve_font_file(family: Optional[str], table: Dict[str, str]) -> Optional[str]:
    if not family:
        return None
    if family in table:
        return table[family]
    key = normalize_family(family)
    for fam, fname in table.items():
        if normalize_family(fam) == key:
            return fname
    return None


def draft_shadow_truth(style_id: str) -> Dict[str, Any]:
    """从解密草稿里补 shadow_point / shadow_smoothing（库里原本没存这两项）。

    只用于**首次**落库：字段一旦进库，后续以库为单源，换台机器不用草稿也能重算。
    """
    src_p = ROOT / "forensic" / "style_sources.json"
    if not src_p.is_file():
        return {}
    try:
        src = (read_json(src_p).get("styles") or {}).get(style_id)
        if not src:
            return {}
        draft = ROOT / "forensic" / "decrypted" / str(src) / "draft_content.json"
        if not draft.is_file():
            return {}
        data = read_json(draft)
        sys.path.insert(0, str(ROOT / "dev_tools"))
        from measure_text_style_truth import subtitle_materials  # noqa: PLC0415

        mats, _info = subtitle_materials(data)
    except Exception:
        return {}
    smooth: List[float] = []
    points: List[Tuple[float, float]] = []
    dist: List[float] = []
    for m in mats:
        try:
            if m.get("shadow_smoothing") is not None:
                smooth.append(float(m["shadow_smoothing"]))
            if m.get("shadow_distance") is not None:
                dist.append(float(m["shadow_distance"]))
            pt = m.get("shadow_point")
            if isinstance(pt, dict) and pt.get("x") is not None and pt.get("y") is not None:
                points.append((float(pt["x"]), float(pt["y"])))
        except (TypeError, ValueError):
            continue
    out: Dict[str, Any] = {}
    if smooth:
        smooth.sort()
        out["shadow_smoothing"] = round(smooth[len(smooth) // 2], 4)
    if points:
        xs = sorted(p[0] for p in points)
        ys = sorted(p[1] for p in points)
        out["shadow_point"] = {"x": round(xs[len(xs) // 2], 12), "y": round(ys[len(ys) // 2], 12)}
    if dist:
        dist.sort()
        out["shadow_distance_units"] = round(dist[len(dist) // 2], 3)
    return out


def units_of(spec: Dict[str, Any], tpl: Dict[str, Any]) -> Optional[float]:
    """字号单位：库里的 jianying units 优先；没有就按旧 px/4 反推（style_15 就是这种）。"""
    for v in (spec.get("font_size_jianying_units"),):
        if v:
            return float(v)
    old_px = spec.get("font_size_1080p_px") or tpl.get("font_size")
    try:
        if old_px:
            return round(float(old_px) / 4.0, 4)
    except (TypeError, ValueError):
        pass
    return None


def compute_style(sid: str, entry: Dict[str, Any], tpl: Dict[str, Any], fam_table: Dict[str, str]) -> Dict[str, Any]:
    """算一套风格的目标排版参数（不落盘）。"""
    spec = entry.setdefault("subtitle_spec", {})
    units = units_of(spec, tpl)
    if units is None:
        raise SystemExit(f"{sid}: 既没有 font_size_jianying_units 也没有旧 px，无法换算字号")

    font_px = int(round(units * PX_PER_UNIT_AT_1080))

    # ── 阴影：真值 → 位移分量 / σ ──
    truth = draft_shadow_truth(sid) if (
        spec.get("shadow_smoothing") is None or spec.get("shadow_point") is None
    ) else {}
    dist_units = spec.get("shadow_distance_units")
    if dist_units is None:
        dist_units = truth.get("shadow_distance_units")
    smoothing = spec.get("shadow_smoothing")
    if smoothing is None:
        smoothing = truth.get("shadow_smoothing", DEFAULT_SHADOW_SMOOTHING)
    point = spec.get("shadow_point") or truth.get("shadow_point") or DEFAULT_SHADOW_POINT

    if spec.get("shadow") and dist_units:
        mag = float(dist_units) * SHADOW_DIST_PX_PER_UNIT
        ux = float(point.get("x", DEFAULT_SHADOW_POINT["x"]))
        uy = -float(point.get("y", DEFAULT_SHADOW_POINT["y"]))
        norm = (ux * ux + uy * uy) ** 0.5 or 1.0
        shadow_offset = [int(round(mag * ux / norm)), int(round(mag * uy / norm))]
        shadow_blur = round(max(0.0, float(smoothing) * font_px * SHADOW_BLUR_EM), 2)
    else:
        shadow_offset = [0, 0]
        shadow_blur = 0.0

    # ── 字体：ass_style 为唯一真源 → 随包字体文件 ──
    ass_family = parse_ass_font_family(str(tpl.get("ass_style") or ""))
    resolved = resolve_font_file(ass_family, fam_table)
    old_cands = list(spec.get("font_candidates") or tpl.get("font_candidates") or [])
    if resolved:
        cands = [resolved] + [c for c in old_cands if c != resolved]
    else:
        cands = old_cands

    return {
        "units": units,
        "font_px": font_px,
        "old_font_px": spec.get("font_size_1080p_px") or tpl.get("font_size"),
        "shadow_offset": shadow_offset,
        "shadow_blur_px": shadow_blur,
        "shadow_distance_units": dist_units,
        "shadow_smoothing": round(float(smoothing), 4) if smoothing is not None else None,
        "shadow_point": {"x": float(point.get("x")), "y": float(point.get("y"))},
        "shadow_alpha": spec.get("shadow_alpha"),
        "font_candidates": cands,
        "ass_font_family": ass_family,
        "ass_font_resolved": resolved,
        "max_chars_by_font": max(4, int((SUBTITLE_SYMMETRIC_MAX_W - 32) // max(1, font_px))),
    }


def apply_to_library(entry: Dict[str, Any], new: Dict[str, Any]) -> None:
    spec = entry["subtitle_spec"]
    # 单位也回写：style_15 原本 units=None（只有 px），不回写就会每次都拿 px/4 重推，越跑越大
    spec["font_size_jianying_units"] = new["units"]
    spec["px_per_unit_at_1080"] = round(PX_PER_UNIT_AT_1080, 6)
    spec["font_size_1080p_px"] = new["font_px"]
    spec["font_candidates"] = new["font_candidates"]
    spec["ass_font_family"] = new["ass_font_family"]
    spec["shadow_offset"] = new["shadow_offset"]
    spec["shadow_blur_px"] = new["shadow_blur_px"]
    spec["shadow_distance_units"] = new["shadow_distance_units"]
    spec["shadow_smoothing"] = new["shadow_smoothing"]
    spec["shadow_point"] = new["shadow_point"]
    spec["typography_calibration"] = {
        "px_per_unit_at_1080": round(PX_PER_UNIT_AT_1080, 6),
        "shadow_dist_px_per_unit": SHADOW_DIST_PX_PER_UNIT,
        "shadow_blur_em": SHADOW_BLUR_EM,
        "note": "字号 = font_size_jianying_units × canvas_w/172.8；阴影幅度 = distance × 2.3px（1080 宽）；σ = smoothing × font_px × 0.13。重建器 dev_tools/rebuild_subtitle_typography.py",
    }


def apply_to_template(tpl: Dict[str, Any], new: Dict[str, Any]) -> None:
    tpl["font_size"] = new["font_px"]
    tpl["font_candidates"] = new["font_candidates"]
    tpl["shadow_offset"] = new["shadow_offset"]
    tpl["shadow_blur_px"] = new["shadow_blur_px"]
    tpl["shadow_distance_units"] = new["shadow_distance_units"]
    tpl["shadow_point"] = new["shadow_point"]
    tpl["shadow_smoothing"] = new["shadow_smoothing"]
    tpl["shadow_alpha"] = new["shadow_alpha"]
    tpl["ass_font_family"] = new["ass_font_family"]
    tpl["font_size_jianying_units"] = new["units"]
    tpl["px_per_unit_at_1080"] = round(PX_PER_UNIT_AT_1080, 6)


def apply_to_blueprint(node: Dict[str, Any], tpl: Dict[str, Any], new: Dict[str, Any]) -> None:
    so = dict(node.get("subtitle_override") or {})
    so.update(
        {
            "font_size": tpl.get("font_size"),
            "shadow_offset": tpl.get("shadow_offset"),
            "shadow_blur_px": tpl.get("shadow_blur_px"),
            "max_chars_per_line": tpl.get("max_chars_per_line"),
            "source_font_family": tpl.get("source_font_family"),
            "shadow_distance_units": new["shadow_distance_units"],
            "shadow_smoothing": new["shadow_smoothing"],
            "shadow_point": new["shadow_point"],
            "ass_font_family": new["ass_font_family"],
            "synced_from": "subtitle_templates/templates.json（库派生）",
        }
    )
    node["subtitle_override"] = so


def sync_pipeline(pdir: Path, tpls: Dict[str, Any]) -> List[str]:
    """把已经跑过 step5 的管线对齐（05_subtitles.json 的 template_config 是 templates.json 的拷贝）。"""
    touched: List[str] = []
    subs_p = pdir / "05_subtitles.json"
    if not subs_p.is_file():
        return touched
    subs = read_json(subs_p)
    tid = str(subs.get("template_id") or "")
    tpl = tpls.get(tid)
    cfg = subs.get("template_config")
    if not isinstance(tpl, dict) or not isinstance(cfg, dict):
        return touched
    changed = False
    for k in TMPL_SYNC_KEYS:
        if k in tpl and cfg.get(k) != tpl.get(k):
            cfg[k] = tpl.get(k)
            changed = True
    if changed:
        subs["template_config"] = cfg
        write_json(subs_p, subs)
        touched.append(str(subs_p))
    applied_p = pdir / "style_spectrum_applied.json"
    if applied_p.is_file():
        applied = read_json(applied_p)
        if applied.get("style_id") == tid and applied.get("subtitle_template") != tid:
            applied["subtitle_template"] = tid
            write_json(applied_p, applied)
            touched.append(str(applied_p))
    return touched


def main() -> None:
    ap = argparse.ArgumentParser(description="按单一库源重算字幕字号/字体/阴影，并同步模板与蓝图")
    ap.add_argument("--write", action="store_true", help="真的落盘（不加只打印）")
    ap.add_argument("--pipeline-dir", type=Path, default=None, help="顺带对齐这份管线的 05_subtitles.json")
    ap.add_argument("--json", action="store_true", help="机读输出")
    args = ap.parse_args()

    lib = read_json(LIB_PATH)
    tpl_doc = read_json(TPL_PATH)
    tpls = tpl_doc.get("templates") or {}
    bp_doc = read_json(BP_PATH) if BP_PATH.is_file() else {}
    bps = bp_doc.get("blueprints") or {}
    fam_table = ass_family_table()

    rows: List[Dict[str, Any]] = []
    for entry in lib.get("styles") or []:
        sid = str(entry.get("style_id") or "")
        tpl = tpls.get(sid)
        if not sid or not isinstance(tpl, dict):
            continue
        new = compute_style(sid, entry, tpl, fam_table)
        rows.append({"style_id": sid, **new})
        if args.write:
            apply_to_library(entry, new)
            apply_to_template(tpl, new)
            node = bps.get(sid)
            if isinstance(node, dict):
                apply_to_blueprint(node, tpl, new)

    if args.write:
        policy = lib.setdefault("measurement_policy", {})
        policy["subtitle_typography"] = (
            "字号 = font_size_jianying_units × canvas_w/172.8（6.25@1080）；"
            "阴影幅度 = shadow_distance_units × 2.3px（1080 宽，实测互相关 11.7px/5unit）；"
            "σ = shadow_smoothing × font_px × 0.13；字体族以 ass_style 为唯一真源，经 "
            "assets/fonts/font_aliases.json 的 ass_style_families 映射。"
            "单源重建器：dev_tools/rebuild_subtitle_typography.py"
        )
        write_json(LIB_PATH, lib)
        write_json(TPL_PATH, tpl_doc)
        if bps:
            write_json(BP_PATH, bp_doc)
        if args.pipeline_dir:
            touched = sync_pipeline(Path(args.pipeline_dir), tpls)
            for p in touched:
                print(f"[sync] {p}")

    missing = [r["style_id"] for r in rows if not r["ass_font_resolved"]]
    if args.json:
        print(json.dumps({"rows": rows, "unmapped_ass_fonts": missing}, ensure_ascii=False, indent=2))
        return

    print(f"{'style':9} {'units':>6} {'旧px':>5} {'新px':>5} {'阴影':>9} {'σ':>5}  ASS字体名 → 命中文件")
    for r in rows:
        off = r["shadow_offset"]
        print(
            f"{r['style_id']:9} {r['units']:>6} {str(r['old_font_px']):>5} {r['font_px']:>5} "
            f"{str(off):>9} {r['shadow_blur_px']:>5}  {r['ass_font_family']} → {r['ass_font_resolved'] or '（未映射，保留原顺序）'}"
        )
    if missing:
        print(f"\n未映射的 ASS 字体族：{', '.join(missing)}（请在 assets/fonts/font_aliases.json → ass_style_families 补一条）")
    print(f"\n{'已写入' if args.write else '干跑（加 --write 落盘）'}：{len(rows)} 套")


if __name__ == "__main__":
    main()
