#!/usr/bin/env python3
"""
「风格谱 (Style Spectrum)」运行时应用与 HyperFrames 样板导出器
1. resolve_style_runtime_config(style_id): 供 run_pipeline.py 等脚本读取风格对应的模板、LUT、J-cut 与节奏配置。
2. --apply-pipeline: 将 style_spectrum_applied.json 落盘至流水线目录，绝不污染全局 assets/luts/。
3. --export-hyperframes: 导出无 STYLE-XX 徽标泄漏、零外部 CDN 的 HyperFrames 风格样板 HTML。
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (
    ASSETS_DIR,
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    DEFAULT_FPS,
    maybe_reexec_in_venv,
    read_json,
    resolve_lut_path,
    write_json,
)

if __name__ == "__main__":
    maybe_reexec_in_venv()

from export_hyperframes import attach_hyperframes_launchers

STYLE_DIR = ASSETS_DIR / "style_spectrum"
LIB_PATH = STYLE_DIR / "style_spectrum_library.json"


def load_library() -> Dict[str, Any]:
    if not LIB_PATH.is_file():
        raise FileNotFoundError(f"风格谱主库不存在: {LIB_PATH}")
    return read_json(LIB_PATH)


def get_style_entry(lib: Dict[str, Any], style_id: str) -> Dict[str, Any]:
    for s in lib.get("styles", []):
        if s.get("style_id") == style_id:
            return s
    raise KeyError(f"未找到风格 ID: {style_id}")


def resolve_style_runtime_config(style_id: str) -> Dict[str, Any]:
    """返回指定风格的完整运行时配置（供 run_pipeline.py / Step 4 / Step 7 共享）。"""
    lib = load_library()
    entry = get_style_entry(lib, style_id)
    color = entry.get("color_and_lut") or {}
    beat = entry.get("beat_alignment") or {}
    meta = entry.get("meta") or {}
    lut_file = color.get("lut_file") or f"{style_id}.cube"
    lut_path = resolve_lut_path(lut_file, style_id=style_id)
    j_cut_rec = beat.get("recommended_j_cut_frames")
    j_cut = int(j_cut_rec) if j_cut_rec is not None else 3
    return {
        "style_id": style_id,
        "style_name_cn": entry.get("style_name_cn", style_id),
        "archetype_id": entry.get("archetype_id", "arch_01"),
        "recommended_track": entry.get("recommended_track", "store"),
        "subtitle_template": style_id,
        "lut_file": lut_file,
        "lut_path": str(lut_path) if lut_path.exists() else "",
        "j_cut_frames": j_cut,
        "target_fps": int(round(float(meta.get("fps") or DEFAULT_FPS))),
        "beat_bpm": float(beat.get("estimated_bpm") or 110.0),
        "opening_structure": entry.get("opening_structure") or {},
        "title_cover_layout": entry.get("title_cover_layout") or {},
        "transition_and_packaging": entry.get("transition_and_packaging") or {},
        "packaging_techniques": entry.get("packaging_techniques") or {},
        "pip_composition_spec": entry.get("pip_composition_spec") or {},
        "giant_text_spec": entry.get("giant_text_spec") or {},
        "audio_and_sfx": entry.get("audio_and_sfx") or {},
        "shot_distribution": entry.get("shot_distribution") or {},
        "beat_alignment": beat,
        "commercial_safe_font": entry.get("commercial_safe_font") or "AlimamaShuHeiTi-Bold.otf",
        "source_jianying_font": entry.get("source_jianying_font") or "",
        "plain_cn_pitch": entry.get("plain_cn_pitch") or "",
        "whitebox_source": entry.get("whitebox_source") or {},
    }


def list_styles(lib: Dict[str, Any]) -> None:
    print(f"{'ID':<10} {'名称':<26} {'轨道':<8} {'中位镜长':<9} {'BPM':<7} {'字幕模板'}")
    print("-" * 76)
    def _num(v: Any, fmt: str) -> str:
        """v4 里有些推演值被作废置空（如 estimated_bpm），显示为 —，绝不假装有值。"""
        try:
            return format(float(v), fmt) if v is not None else "—"
        except (TypeError, ValueError):
            return "—"

    for s in lib.get("styles", []):
        sid = s["style_id"]
        name = str(s.get("style_name_cn", ""))[:24]
        track = s.get("recommended_track", "creator")
        med = s.get("shot_distribution", {}).get("median_sec")
        bpm = s.get("beat_alignment", {}).get("estimated_bpm")
        print(f"{sid:<10} {name:<26} {track:<8} {_num(med, '.2f'):<9} {_num(bpm, '.1f'):<7} {sid}")


def bg_slab_hex(bp: Dict[str, Any]) -> str:
    """把蓝图里的 rgba 底条色换成色相相近的实色，供样本 CSS 用。"""
    raw = str(bp.get("bg_slab") or "")
    m = raw.replace(" ", "").lower()
    if m.startswith("rgba(") and m.endswith(")"):
        parts = m[5:-1].split(",")
        if len(parts) >= 3:
            def h(x: str) -> int:
                try:
                    return max(0, min(255, int(round(float(x)))))
                except Exception:
                    return 0
            return "#%02X%02X%02X" % (h(parts[0]), h(parts[1]), h(parts[2]))
    if raw.startswith("#"):
        return raw
    return "rgba(20,24,28,0.86)"


def quote_font_family(sub: Dict[str, Any]) -> str:
    fam = str(sub.get("font_family") or "sans-serif")
    return f"'{fam}', 'PingFang SC', sans-serif"


def _sub_lines(max_chars: int, count: int = 3) -> List[str]:
    """按该风格实测字数上限切样板字幕行：只用中性示范文案，不带任何源片原文。"""
    n = max(6, min(int(max_chars or 14), 48))
    seeds = [
        "这一套样板只演示字幕排版节奏",
        "字号描边底框按源实测逐套校准",
        "插入画面与花字位置照原版走",
        "示范文案不承载任何真实内容",
    ]
    return [seeds[i % len(seeds)][:n] for i in range(count)]


def export_hyperframes_html(
    lib: Dict[str, Any],
    style_id: str,
    out_html: Path,
    duration_sec: float = 15.0,
    title: Optional[str] = None,
) -> Path:
    """按单套风格的真实实测参数生成 HyperFrames 样板（非通用标题+一行字幕）。

    样板元素全部由风格库派生：巨字 hook、顶栏、画中画占位、字幕底框与字数上限、
    描边/阴影、配色与调色近似，逐套不同；不写任何 STYLE-XX 徽标，不引外部 CDN。
    """
    entry = get_style_entry(lib, style_id)
    sub = entry.get("subtitle_spec") or {}
    color = entry.get("color_and_lut") or {}
    pack = entry.get("packaging_techniques") or {}
    bp = entry.get("visual_blueprint") or {}
    giant = entry.get("giant_text_spec") or {}
    pip = entry.get("pip_composition_spec") or {}
    palette = color.get("top5_palette_hex") or ["#111827", "#38BDF8", "#FFFFFF", "#FACC15", "#E5E7EB"]
    bg_hex = palette[0] if palette else "#111827"
    accent_hex = palette[1] if len(palette) > 1 else "#FACC15"
    third_hex = palette[2] if len(palette) > 2 else "#FFFFFF"

    dur = max(2.0, float(duration_sec))
    if out_html.suffix.lower() != ".html":
        out_html = out_html / "index.html"
    out_html.parent.mkdir(parents=True, exist_ok=True)
    safe_title = html.escape(str(title or entry.get("style_name_cn") or "核心视觉样板"))

    y_px = min(1560, max(900, int(sub.get("safe_y_px_1080x1920") or 1340)))
    font_sz = max(36, int(sub.get("font_size_1080p_px") or 58))
    pri_col = str(sub.get("primary_color") or "#FFFFFF")
    kw_col = str(sub.get("keyword_color") or accent_hex)
    stroke_col = str(sub.get("stroke_color") or "#0D1117")
    stroke_w = int(sub.get("stroke_width") or 0)
    stroke_css = "0px" if stroke_w <= 0 else f"{max(1, min(20, round(stroke_w * 2.0)))}px {stroke_col}"
    shadow = bool(sub.get("shadow"))
    shadow_off = sub.get("shadow_offset") or [2, 3]
    shadow_css = (
        f"text-shadow:{shadow_off[0] * 3}px {shadow_off[1] * 3}px 6px {sub.get('shadow_color') or '#000000C8'};"
        if shadow else "text-shadow:none;"
    )
    max_chars = int(sub.get("max_chars_per_line") or 14)
    bg_alpha = float(sub.get("bg_box_alpha") or 0.0)
    bg_ratio = float(sub.get("bg_ratio_measured") or 0.0)
    bg_color = str(sub.get("bg_box_color") or bg_slab_hex(bp))
    bg_radius = float(sub.get("bg_radius") or 0.0)
    bg_shape = str(sub.get("bg_shape") or "square")
    pad_y = 12 if bg_ratio > 0 else 0
    pad_x = 26 if bg_ratio > 0 else 0
    box_css = (
        f"background:{bg_color}; border-radius:{'999px' if bg_shape == 'capsule' else f'{max(0.0, bg_radius) * 3:.0f}px'};"
        f" padding:{pad_y}px {pad_x}px; display:inline-block;"
        if bg_ratio > 0.01 or bg_alpha > 0.01 else "background:transparent; padding:0;"
    )

    giant_fs = int(giant.get("font_size_px") or 128)
    giant_fg = str(giant.get("fg_hex") or third_hex)
    giant_accent = str(giant.get("accent_hex") or accent_hex)
    giant_stroke_w = int(giant.get("stroke_width") or 0)
    giant_stroke = "none" if giant_stroke_w <= 0 else f"{max(1, min(26, round(giant_stroke_w * 2.0)))}px {giant.get('stroke_hex') or '#000000'}"
    giant_tag = str(giant.get("tag_label") or "")
    giant_on = (int(pack.get("giant_impact_count") or 0) > 0) or (entry.get("display_text_spec") or {}).get("count")
    topbar_on = bool(pack.get("persistent_top_bar") or bp.get("persistent_top_bar"))
    quote_on = bool(pack.get("quote_citation_card"))
    pip_mode = str(pip.get("mode") or pack.get("pip_mode") or "none")
    pip_on = pip_mode not in ("none", "", "None")
    tilt = float(bp.get("tilt_deg") or 0)
    anim_mode = str(bp.get("anim_mode") or "none")
    entry_anim = str(bp.get("entry_anim") or "none")
    css_filter = str(color.get("css_filter_approx") or "")

    hook_sec = min(3.4, max(1.2, dur * 0.24))
    tail_sec = min(2.6, max(1.0, dur * 0.18))
    body_start = hook_sec
    body_end = max(body_start + 1.0, dur - tail_sec)
    lines = _sub_lines(max_chars, 3)
    step = (body_end - body_start) / 3.0

    caption_divs = []
    for i, text in enumerate(lines):
        st = body_start + step * i
        body_html = html.escape(text)
        if len(text) >= 4:
            cut = max(1, len(text) // 2)
            body_html = f'{html.escape(text[:cut])}<span class="kw">{html.escape(text[cut:])}</span>'
        caption_divs.append(
            f'    <div id="sample-caption-{i + 1}" class="clip cap" data-start="{st:.4f}" '
            f'data-duration="{step:.4f}" data-track-index="3"><span class="cap-box">{body_html}</span></div>'
        )

    pip_css = ""
    pip_div = ""
    if pip_on:
        pr = 999 if "circle" in pip_mode else 28
        pw, ph = (420, 420) if "circle" in pip_mode else (380, 500)
        right, top = (60, 180)
        pip_css = (
            f"    .pip {{ position:absolute; right:{right}px; top:{top}px; width:{pw}px; height:{ph}px;"
            f" border-radius:{pr}px; background:{bp.get('bg_slab') or bg_hex}; border:{6}px solid {accent_hex};"
            f" box-shadow:0 18px 48px rgba(0,0,0,.55); overflow:hidden; }}"
        )
        pip_div = (
            f'    <div id="sample-pip" class="clip pip" data-start="0" data-duration="{dur:.4f}" '
            f'data-track-index="2" data-pip-mode="{html.escape(pip_mode)}"></div>'
        )

    topbar_div = ""
    if topbar_on:
        topbar_div = (
            f'    <div id="sample-topbar" class="clip topbar" data-start="0" data-duration="{dur:.4f}" '
            f'data-track-index="1"></div>'
        )

    giant_div = ""
    if giant_on:
        tag_html = f'<span class="g-tag">{html.escape(giant_tag)}</span>' if giant_tag else ""
        giant_div = (
            f'    <div id="sample-giant" class="clip giant" data-start="0" data-duration="{hook_sec:.4f}" '
            f'data-track-index="1" data-anim="{html.escape(entry_anim)}">'
            f'<span class="g-main">{safe_title.split("·")[0].strip() or safe_title}</span>{tag_html}</div>'
        )

    tail_div = ""
    if quote_on or giant_on:
        tail_div = (
            f'    <div id="sample-endcard" class="clip endcard" data-start="{body_end:.4f}" '
            f'data-duration="{max(0.6, dur - body_end):.4f}" data-track-index="1">'
            f'<span class="e-bar"></span><span>{safe_title}</span></div>'
        )

    html_doc = "\n".join([
        "<!DOCTYPE html>",
        '<html lang="zh-CN">',
        "<head>",
        '  <meta charset="utf-8" />',
        f"  <title>{safe_title}</title>",
        "  <style>",
        f"    html, body {{ margin:0; padding:0; width:{CANVAS_WIDTH}px; height:{CANVAS_HEIGHT}px; overflow:hidden; background:{bg_hex}; }}",
        f"    #main {{ position:relative; width:{CANVAS_WIDTH}px; height:{CANVAS_HEIGHT}px; font-family:{quote_font_family(sub)}; filter:{css_filter or 'none'}; }}",
        *([f"    .topbar {{ position:absolute; left:0; right:0; top:0; height:96px; background:{bg_slab_hex(bp)}; border-bottom:6px solid {accent_hex}; }}"] if topbar_on else []),
        f"    .giant {{ position:absolute; left:64px; right:64px; top:190px; transform:rotate({tilt}deg); line-height:1.08; }}",
        f"    .g-main {{ display:block; font-size:{giant_fs}px; font-weight:900; color:{giant_fg}; -webkit-text-stroke:{giant_stroke}; paint-order:stroke fill; }}",
        f"    .g-tag {{ display:inline-block; margin-top:22px; padding:8px 22px; font-size:44px; font-weight:800; color:#FFFFFF; background:{giant_accent}; border-radius:8px; }}",
        pip_css,
        f"    .cap {{ position:absolute; left:0; right:0; top:{y_px}px; text-align:center; }}",
        f"    .cap-box {{ {box_css} font-size:{font_sz}px; font-weight:800; color:{pri_col}; -webkit-text-stroke:{stroke_css}; paint-order:stroke fill; {shadow_css} max-width:{int(sub.get('symmetry_max_width_px') or 640)}px; }}",
        f"    .kw {{ color:{kw_col}; }}",
        f"    .endcard {{ position:absolute; left:80px; right:80px; bottom:220px; color:{pri_col}; font-size:{max(40, font_sz - 6)}px; font-weight:800; {shadow_css} }}",
        f"    .e-bar {{ display:block; width:132px; height:12px; border-radius:6px; background:{accent_hex}; margin-bottom:20px; }}",
        "  </style>",
        "</head>",
        "<body>",
        f'<div id="main" data-composition-id="main" data-no-timeline data-start="0" data-duration="{dur:.4f}" data-fps="{DEFAULT_FPS}" data-width="{CANVAS_WIDTH}" data-height="{CANVAS_HEIGHT}" data-anim-mode="{html.escape(anim_mode)}" data-bg-shape="{html.escape(bg_shape)}" data-max-chars="{max_chars}">',
        topbar_div,
        giant_div,
        pip_div,
        *caption_divs,
        tail_div,
        "</div>",
        "</body>",
        "</html>",
    ])
    html_doc = "\n".join([d for d in html_doc.split("\n") if d.strip()]) + "\n"
    out_html.write_text(html_doc, encoding="utf-8")
    if out_html.name == "index.html":
        config = out_html.parent / "hyperframes.json"
        if not config.exists():
            write_json(config, {"media": {"autoProxy": True}})
        attach_hyperframes_launchers(out_html.parent)
    print(f"[apply-style] HyperFrames 样板 HTML 已导出 -> {out_html}")
    return out_html


def apply_to_pipeline(lib: Dict[str, Any], style_id: str, pipeline_dir: Path) -> Path:
    """将风格参数写入 pipeline_dir/style_spectrum_applied.json，绝不修改全局 assets/。"""
    entry = get_style_entry(lib, style_id)
    rt = resolve_style_runtime_config(style_id)
    pipeline_dir.mkdir(parents=True, exist_ok=True)
    out = pipeline_dir / "style_spectrum_applied.json"
    write_json(out, {
        "style_id": style_id,
        "style_name_cn": entry.get("style_name_cn"),
        "archetype_id": entry.get("archetype_id"),
        "recommended_track": rt["recommended_track"],
        "subtitle_template": rt["subtitle_template"],
        "lut_file": rt["lut_file"],
        "lut_path": rt["lut_path"],
        "j_cut_frames": rt["j_cut_frames"],
        "target_fps": rt["target_fps"],
        "beat_bpm": rt["beat_bpm"],
        "color_and_lut": entry.get("color_and_lut"),
        "subtitle_spec": entry.get("subtitle_spec"),
        "transition_and_packaging": entry.get("transition_and_packaging"),
        "opening_structure": entry.get("opening_structure"),
        "title_cover_layout": rt["title_cover_layout"],
        "subtitle_symmetry_lock": entry.get("subtitle_symmetry_lock"),
        "broll_overlay_ratio_target": entry.get("broll_overlay_ratio_target"),
        "broll_ambient_sound_policy": entry.get("broll_ambient_sound_policy"),
        "dual_bgm_structure": entry.get("dual_bgm_structure"),
        "multi_zone_flower_spec": entry.get("multi_zone_flower_spec"),
        "packaging_techniques": entry.get("packaging_techniques"),
        "pip_composition_spec": entry.get("pip_composition_spec"),
        "visual_blueprint": entry.get("visual_blueprint"),
        "requires_aroll": entry.get("requires_aroll", "optional"),
        "allow_explainer_pip": entry.get("allow_explainer_pip", False),
        "giant_text_spec": entry.get("giant_text_spec"),
        "audio_and_sfx": entry.get("audio_and_sfx"),
        "shot_distribution": entry.get("shot_distribution"),
        "beat_alignment": entry.get("beat_alignment"),
        "commercial_safe_font": entry.get("commercial_safe_font"),
        "source_jianying_font": entry.get("source_jianying_font"),
        "plain_cn_pitch": entry.get("plain_cn_pitch"),
        "whitebox_source": entry.get("whitebox_source"),
    })
    print(f"[apply-style] 已应用 {style_id} ({entry.get('style_name_cn')}) -> {out}")
    return out


def filter_styles_by_aroll(lib: Dict[str, Any], has_aroll: bool) -> List[Dict[str, Any]]:
    """按有无出镜口播素材智能筛出适配模板（实测 #15：无出镜时主推无需口播的全空镜/图文/画框模板）。"""
    styles = list(lib.get("styles") or [])
    if has_aroll:
        return styles
    return [s for s in styles if str(s.get("requires_aroll") or "optional") != "required"]


def apply_full_visual_refresh(
    lib: Dict[str, Any],
    style_id: str,
    pipeline_dir: Path,
    keep_edl: bool = True,
    no_flower_text: bool = False,
) -> Dict[str, Any]:
    """
    一键切换视觉风格并真正刷完下游文件（根治实测 #6 只写 JSON 不联动 + #7 换风格不丢人工 EDL/花字）：
    保留 03_audio_events 与（keep_edl=True 时的）04_edl.json 选镜，刷新 step5/step6/step7/step9。
    """
    apply_to_pipeline(lib, style_id, pipeline_dir)
    rt = resolve_style_runtime_config(style_id)
    track = str(rt.get("recommended_track") or "store")
    edl_p = pipeline_dir / "04_edl.json"
    if edl_p.is_file():
        edl = read_json(edl_p)
        edl["style_id"] = style_id
        if keep_edl:
            entry = get_style_entry(lib, style_id)
            pip_spec = entry.get("pip_composition_spec") or {"mode": "none"}
            pip_mode = str(pip_spec.get("mode") or "none")
            edl["pip_mode"] = pip_mode
            for c in edl.get("clips") or []:
                if pip_mode in ("", "none"):
                    c.pop("pip_layout", None)
                elif pip_spec.get("single_source"):
                    c["pip_layout"] = {**pip_spec, "mode": pip_mode, "single_source": True}
        write_json(edl_p, edl)
    refreshed: List[str] = ["style_spectrum_applied.json"]
    if (pipeline_dir / "03_audio_events.json").is_file() and edl_p.is_file():
        from step5_subtitles import run_step5
        from step6_packaging import run_step6
        from step7_color_grade import run_step7
        from step9_title_cover import run_step9

        run_step5(pipeline_dir, track=track, template_id=rt["subtitle_template"])
        refreshed.append("05_subtitles.json")
        run_step6(pipeline_dir, style_id=style_id, no_flower_text=no_flower_text, keep_manual_texts=keep_edl)
        refreshed.append("06_packaging.json")
        run_step7(pipeline_dir, track=track, lut_name=rt["lut_file"], style_id=style_id)
        refreshed.append("07_color_plan.json")
        if (pipeline_dir / "01_manifest.json").is_file():
            run_step9(pipeline_dir, track=track, style_id=style_id)
            refreshed.append("09_title_cover.json")
    print(f"[apply-style] 全视觉层联动刷新完成 ({style_id}, keep_edl={keep_edl}) -> {refreshed}")
    return {"style_id": style_id, "keep_edl": keep_edl, "refreshed": refreshed}


def main() -> None:
    parser = argparse.ArgumentParser(description="应用「风格谱」至流水线或导出 HyperFrames 样板")
    parser.add_argument("--list", action="store_true", help="列出全部风格谱")
    parser.add_argument("--has-aroll", choices=["auto", "yes", "no"], default="auto", help="按有无出镜素材过滤推荐模板")
    parser.add_argument("--style-id", help="风格 ID（如 style_01）")
    parser.add_argument("--apply-pipeline", help="目标流水线目录")
    parser.add_argument("--apply-full", action="store_true", help="写入风格并立即联动刷新 step5/6/7/9 使换装真正生效")
    parser.add_argument("--keep-edl", action="store_true", help="与 --apply-full 搭配：保留已有 EDL 选镜与花字文案，只换视觉样式")
    parser.add_argument("--no-flower-text", action="store_true", help="与 --apply-full 搭配：关闭自动花字")
    parser.add_argument("--export-hyperframes", help="导出 HyperFrames HTML 目录或文件路径")
    parser.add_argument("--duration", type=float, default=15.0, help="样板时长（秒）")
    parser.add_argument("--title", default=None, help="样板标题（默认取该风格的 style_name_cn）")
    parser.add_argument("--print-config", action="store_true", help="打印运行时 JSON 配置")
    args = parser.parse_args()

    lib = load_library()
    if args.list:
        if args.has_aroll == "no":
            filtered = {**lib, "styles": filter_styles_by_aroll(lib, has_aroll=False)}
            print(f"[apply-style] 无出镜素材模式：为你筛出 {len(filtered['styles'])} 套纯空镜/画框适配风格")
            list_styles(filtered)
        else:
            list_styles(lib)
        return
    if not args.style_id:
        parser.error("请指定 --style-id 或使用 --list")

    if args.print_config:
        print(json.dumps(resolve_style_runtime_config(args.style_id), ensure_ascii=False, indent=2))
    if args.apply_pipeline:
        if args.apply_full:
            apply_full_visual_refresh(
                lib, args.style_id, Path(args.apply_pipeline),
                keep_edl=args.keep_edl, no_flower_text=args.no_flower_text,
            )
        else:
            apply_to_pipeline(lib, args.style_id, Path(args.apply_pipeline))
    if args.export_hyperframes:
        export_hyperframes_html(
            lib, args.style_id, Path(args.export_hyperframes),
            duration_sec=args.duration, title=args.title,
        )


if __name__ == "__main__":
    main()
