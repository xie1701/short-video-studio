#!/usr/bin/env python3
"""Verify every style library field against the source manifest — and emit the matrix.

Compares the shipped library (what the renderer reads) with `forensic/source_manifest.json`
(what the decrypted JianYing draft actually says). Any field that drifts fails the check and
the process exits non-zero, so CI / pre-release can gate on it.

    /tmp/jyvenv/bin/python dev_tools/verify_style_parity.py           # 校验 + 写矩阵
    /tmp/jyvenv/bin/python dev_tools/verify_style_parity.py --check   # 只校验，不写文件
"""

from __future__ import annotations

import argparse
import csv
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
LIB = ROOT / "short-video-studio/assets/style_spectrum/style_spectrum_library.json"
MANIFEST = ROOT / "forensic/source_manifest.json"
CSV_OUT = ROOT / "reports/style_parity_matrix.csv"

TOL = 0.011  # 百分比类字段容差（1.1pp ≈ 浮点串联误差）


def close(a, b, tol=TOL) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return abs(float(a) - float(b)) <= tol


def checks(s: dict, m: dict) -> list[tuple[str, object, object, bool]]:
    sd = s["shot_distribution"]
    sub = s["subtitle_spec"]
    t = s["packaging_techniques"]
    a = s["audio_and_sfx"]
    gt = m["metrics"]
    rows = [
        ("时长", s["meta"]["duration_sec"], m["duration_sec"], close(s["meta"]["duration_sec"], m["duration_sec"], 0.05)),
        ("主轨镜数", sd.get("main_track_clips"), gt["v1_clips"], sd.get("main_track_clips") == gt["v1_clips"]),
        ("全片段数", sd.get("total_shots"), gt["total_video_segs"], sd.get("total_shots") == gt["total_video_segs"]),
        ("中位镜长", sd.get("median_sec"), gt["median_shot_sec"], close(sd.get("median_sec"), gt["median_shot_sec"], 0.02)),
        ("覆盖率", sd.get("broll_coverage_ratio"), gt["broll_overlay_ratio"], close(sd.get("broll_coverage_ratio"), gt["broll_overlay_ratio"])),
        ("单镜<1s比", sd.get("fast_cuts_under_1s_ratio"), gt["fast_cut_lt1s_ratio"], close(sd.get("fast_cuts_under_1s_ratio"), gt["fast_cut_lt1s_ratio"])),
        ("关键帧/min", sd.get("kf_per_min"), gt["kf_per_min"], close(sd.get("kf_per_min"), gt["kf_per_min"], 0.2)),
        ("音效/min", a.get("sfx_per_min_measured"), gt["sfx_per_min"], close(a.get("sfx_per_min_measured"), gt["sfx_per_min"], 0.2)),
        ("双BGM", bool(a.get("dual_bgm_enabled")), bool(gt["dual_bgm"]), bool(a.get("dual_bgm_enabled")) == bool(gt["dual_bgm"])),
        ("字幕y", sub.get("safe_y_ratio"), gt["sub_y_ratio"],
         (sub.get("safe_y_ratio") is not None and sub.get("safe_y_source") == "densest_text_track") if gt["sub_y_ratio"] is None
         else close(sub.get("safe_y_ratio"), gt["sub_y_ratio"], 0.005)),
        ("字数上限", sub.get("max_chars_per_line"), gt["sub_max_chars"],
         (sub.get("max_chars_per_line") is not None and sub.get("max_chars_source") == "densest_text_track") if gt["sub_max_chars"] is None
         else sub.get("max_chars_per_line") == gt["sub_max_chars"]),
        ("底框占比", sub.get("bg_ratio_measured"), gt["sub_bg_spec"]["ratio"], close(sub.get("bg_ratio_measured"), gt["sub_bg_spec"]["ratio"], 0.02)),
        ("顶栏", bool(t.get("persistent_top_bar")), bool(gt["has_top_bar"]), bool(t.get("persistent_top_bar")) == bool(gt["has_top_bar"])),
        ("金句卡", bool(t.get("quote_citation_card")), bool(gt["has_quote_card"]), bool(t.get("quote_citation_card")) == bool(gt["has_quote_card"])),
        ("巨字", t.get("giant_impact_count"), gt["giant_n"], t.get("giant_impact_count") == gt["giant_n"]),
        ("抠像", t.get("ai_matting_cutout"), gt["matting_count"], t.get("ai_matting_cutout") == gt["matting_count"]),
        ("蒙版", json.dumps(t.get("mask_shapes"), sort_keys=True), json.dumps(gt["mask_types"], sort_keys=True), t.get("mask_shapes") == gt["mask_types"]),
        ("展示字/min", (s.get("display_text_spec") or {}).get("per_min"), gt["display_per_min"], close((s.get("display_text_spec") or {}).get("per_min"), gt["display_per_min"], 0.2)),
        ("转场", s["visual_blueprint"].get("transition_style"), "none（源 0）", s["visual_blueprint"].get("transition_style") == "none" and gt["transition_count"] == 0),
        ("pip一致性", t.get("pip_mode"), (s.get("pip_composition_spec") or {}).get("mode"), t.get("pip_mode") == (s.get("pip_composition_spec") or {}).get("mode")),
        ("源hash", s.get("source_hash", "")[:12], m["source_sha256"][:12], s.get("source_hash") == m["source_sha256"]),
        ("字体声明", bool(sub.get("font_substitution")), "必填", bool(sub.get("font_substitution"))),
    ]
    return rows


def draft_facts(rel_path: str) -> dict:
    """直接从解密草稿重新量一遍，不经过 manifest/gt，用于自证。"""
    import hashlib

    f = ROOT / rel_path
    h = hashlib.sha256()
    with f.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    d = json.loads(f.read_text(encoding="utf-8"))
    tracks = d.get("tracks") or []
    v1 = next((t for t in tracks if t.get("type") == "video"), {})
    segs = v1.get("segments") or []
    total = sum(len(t.get("segments") or []) for t in tracks if t.get("type") == "video")
    mats = d.get("materials") or {}
    trans = len(mats.get("transitions") or [])
    matting = sum(1 for v in (mats.get("videos") or []) if float((v.get("matting") or {}).get("flag") or 0) > 0)
    return {
        "sha256": h.hexdigest(),
        "duration_sec": round((d.get("duration") or 0) / 1e6, 3),
        "v1_clips": len(segs),
        "total_video_segs": total,
        "transition_count": trans,
        "matting_count": matting,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只校验，不写矩阵")
    args = ap.parse_args()

    lib = json.loads(LIB.read_text(encoding="utf-8"))
    man = json.loads(MANIFEST.read_text(encoding="utf-8"))["styles"]
    styles = {s["style_id"]: s for s in lib["styles"]}
    library_styles = styles

    failures: list[str] = []
    matrix_rows = []
    total_checks = 0

    for sid in sorted(styles):
        s = styles[sid]
        m = man.get(sid)
        if not m:
            failures.append(f"{sid}: 不在 source_manifest 中")
            continue
        rows = checks(s, m)
        total_checks += len(rows)
        bad = [f"{name}(库={lib_v} 源={gt_v})" for name, lib_v, gt_v, ok in rows if not ok]
        if bad:
            failures.append(f"{sid}: " + "；".join(bad))
        gt = m["metrics"]
        matrix_rows.append(
            {
                "style": sid,
                "name": s["style_name_cn"],
                "source_actual": m["source_project"],
                "source_sha256": m["source_sha256"][:16],
                "duration_lib": s["meta"]["duration_sec"],
                "duration_real": m["duration_sec"],
                "median_lib": s["shot_distribution"].get("median_sec"),
                "median_real": gt["median_shot_sec"],
                "broll_lib_pct": round(s["shot_distribution"]["broll_coverage_ratio"] * 100, 1),
                "broll_real_pct": round(gt["broll_overlay_ratio"] * 100, 1),
                "broll_band_pct": "/".join(str(x) for x in s.get("broll_target_band_pct") or []),
                "broll_target_hard": s.get("broll_target_hard"),
                "kf_lib": s["shot_distribution"].get("kf_per_min"),
                "kf_real": gt["kf_per_min"],
                "sfx_lib": s["audio_and_sfx"].get("sfx_per_min_measured"),
                "sfx_real": gt["sfx_per_min"],
                "luma_lib": s["color_and_lut"].get("y_mean_255"),
                "luma_real": gt["cover_luma"],
                "warm_lib": s["color_and_lut"].get("warmth_index"),
                "warm_real": gt["cover_warmth"],
                "sat_lib_pct": s["color_and_lut"].get("sat_mean_pct"),
                "sat_real_pct": round((gt["cover_sat"] or 0) * 100, 1) if gt["cover_sat"] is not None else None,
                "sub_y_lib": s["subtitle_spec"].get("safe_y_ratio"),
                "sub_y_real": gt["sub_y_ratio"],
                "sub_bg_lib": s["subtitle_spec"].get("bg_box_color"),
                "sub_bg_real_pct": round(gt["sub_bg_spec"]["ratio"] * 100, 1),
                "sub_bg_alltext_pct": round(gt["sub_bg_ratio"] * 100, 1),
                "sub_bg_shape": s["subtitle_spec"].get("bg_shape"),
                "maxchars_lib": s["subtitle_spec"].get("max_chars_per_line"),
                "maxchars_real": gt["sub_max_chars"],
                "source_font": s["subtitle_spec"].get("source_sub_font_family"),
                "delivered_font": s["subtitle_spec"].get("font_family"),
                "topbar_lib": s["packaging_techniques"].get("persistent_top_bar"),
                "topbar_real": gt["has_top_bar"],
                "quote_lib": s["packaging_techniques"].get("quote_citation_card"),
                "quote_real": gt["has_quote_card"],
                "matte_lib": s["packaging_techniques"].get("ai_matting_cutout"),
                "matte_real": gt["matting_count"],
                "giant_lib": s["packaging_techniques"].get("giant_impact_count"),
                "giant_real": gt["giant_n"],
                "display_pm_lib": (s.get("display_text_spec") or {}).get("per_min"),
                "display_pm_real": gt["display_per_min"],
                "pip_mode": s["packaging_techniques"].get("pip_mode"),
                "transition_lib": s["visual_blueprint"].get("transition_style"),
                "transition_real_count": gt["transition_count"],
                "parity": "PASS" if not bad else "FAIL",
                "fail_fields": ";".join(n for n, *_ in [(b.split("(")[0],) for b in bad]),
            }
        )

    if not args.check:
        CSV_OUT.parent.mkdir(parents=True, exist_ok=True)
        with CSV_OUT.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(matrix_rows[0].keys()))
            w.writeheader()
            w.writerows(matrix_rows)
        print(f"wrote {CSV_OUT.relative_to(ROOT)}")

    # ── 自证：直接从解密草稿重量关键事实（不经过 gt / manifest） ──
    deep_fail = []
    for sid in sorted(styles):
        m = man.get(sid)
        if not m:
            continue
        f = draft_facts(m["source_file"])
        for key, want in (
            ("sha256", m["source_sha256"]),
            ("v1_clips", m["metrics"]["v1_clips"]),
            ("total_video_segs", m["metrics"]["total_video_segs"]),
            ("transition_count", m["metrics"]["transition_count"]),
            ("matting_count", m["metrics"]["matting_count"]),
        ):
            if f[key] != want:
                deep_fail.append(f"{sid}.{key}: 草稿={f[key]} manifest={want}")
        if not close(f["duration_sec"], m["duration_sec"], 0.01):
            deep_fail.append(f"{sid}.duration: 草稿={f['duration_sec']} manifest={m['duration_sec']}")
    print(f"self-check vs decrypted drafts: {24 - len({d.split('.')[0] for d in deep_fail})}/24 clean")
    failures.extend(deep_fail)

    # ── 蓝图 overlay 字段 vs 库：防止有人只改蓝图不改库（或反之）造成漂移 ──
    bp_path = ROOT / "short-video-studio/assets/style_spectrum/template_blueprints.json"
    if bp_path.is_file():
        bp = json.loads(bp_path.read_text(encoding="utf-8")).get("blueprints") or {}
        tpl_doc = json.loads((ROOT / "short-video-studio/assets/subtitle_templates/templates.json").read_text(encoding="utf-8"))
        templates = tpl_doc.get("templates") or tpl_doc
        bp_checks = 0
        for row in matrix_rows:
            sid = row["style"]
            node = bp.get(sid) or {}
            ov = node.get("overlay_style") or {}
            entry = library_styles[sid]
            tech = entry.get("packaging_techniques") or {}
            bp_checks += 1
            if ov.get("transition_style") != "none" or ov.get("transition_source_count") not in (0, None):
                failures.append(f"{sid}.blueprint transition: {ov.get('transition_style')} / {ov.get('transition_source_count')}")
            bp_checks += 1
            if int(ov.get("giant_count") if ov.get("giant_count") is not None else -1) != int(tech.get("giant_impact_count") or 0):
                failures.append(f"{sid}.blueprint giant_count {ov.get('giant_count')} != 库 {tech.get('giant_impact_count')}")
            bp_checks += 1
            pal = ov.get("flower_colors") or []
            if not pal:
                failures.append(f"{sid}.blueprint 缺 flower_colors")
            else:
                measured = {str(c[0]).upper() for c in ((entry.get("display_text_spec") or {}).get("colors") or []) if c}
                legacy = {str(v).upper() for k, v in (entry.get("multi_zone_flower_spec") or {}).items()
                          if isinstance(v, str) and str(v).startswith("#") and "color" in k}
                legacy |= {str(c).upper() for c in ((entry.get("visual_blueprint") or {}).get("accent_colors") or []) if c}
                kwc = str((entry.get("subtitle_spec") or {}).get("keyword_color") or "").upper()
                allowed = measured | legacy | ({kwc} if kwc else set())
                if allowed and not {c.upper() for c in pal} <= allowed:
                    failures.append(f"{sid}.blueprint flower_colors 越出源实测色 {sorted(pal)}")
            bp_checks += 1
            if not str(ov.get("source_font") or "").strip():
                failures.append(f"{sid}.blueprint 缺 source_font")
            # subtitle_override 必须与 templates.json（库派生）逐字段一致
            tpl = templates.get(sid) or {}
            sub_ov = node.get("subtitle_override") or {}
            for key in ("keyword_color", "primary_color", "stroke_color", "bg_box_color",
                        "bg_radius", "font_size", "safe_y_center", "max_chars_per_line"):
                bp_checks += 1
                if key in tpl and sub_ov.get(key) != tpl.get(key):
                    failures.append(f"{sid}.subtitle_override.{key} 蓝图={sub_ov.get(key)} 模板={tpl.get(key)}")
        print(f"blueprint overlay: {bp_checks} checks")

    # ── 画面特效 vs 源草稿 effect 轨：不许凭空加特效，也不许漏掉源里真有的 ──
    usage_path = ROOT / "forensic/effect_usage.json"
    ce_path = ROOT / "short-video-studio/assets/style_spectrum/color_effects.json"
    if usage_path.is_file() and ce_path.is_file():
        usage = json.loads(usage_path.read_text(encoding="utf-8"))["styles"]
        ce = json.loads(ce_path.read_text(encoding="utf-8"))["styles"]
        name_to_key = {"暗角": "vignette", "模糊": "blur", "聚光灯": "spotlight",
                       "不规则黑框": "irregular_black_frame", "怀旧边框": "film_frame",
                       "荧幕噪点": "grain", "颤抖": "shake"}
        fx_checks = 0
        for sid in sorted(ce):
            node = ce[sid].get("effects") or {}
            want = {name_to_key[e["source_effect"]] for e in (usage.get(sid) or {}).get("effects") or []
                    if e["source_effect"] in name_to_key}
            got = {k for k, v in node.items() if isinstance(v, dict)}
            fx_checks += 1
            if got != want:
                failures.append(f"{sid}.effects 与源 effect 轨不符：库={sorted(got)} 源={sorted(want)}")
            for key in got:
                val = node[key]
                fx_checks += 1
                if not val.get("source_effect"):
                    failures.append(f"{sid}.effects.{key} 缺 source_effect 溯源")
                if not isinstance(val.get("window"), dict):
                    fx_checks -= 1
                    failures.append(f"{sid}.effects.{key} 缺时间窗（源里特效只在一段生效）")
        print(f"effects vs source track: {fx_checks} checks")

    passed = sum(1 for r in matrix_rows if r["parity"] == "PASS")
    print(f"parity: {passed}/{len(matrix_rows)} styles PASS  ({total_checks} field checks)")
    if failures:
        print("\nFAILURES:")
        for f_ in failures:
            print("  -", f_)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
