#!/usr/bin/env python3
"""
[跨平台「风格谱」一键应用与复刻导出工具]
将提炼出的 20 套参考视频「风格谱」(style_01 ~ style_20) 一键导出或应用至：
1. --list                             : 查看 6 大流派母版与 20 套风格谱核心参数
2. --export-jianying-template <dir>   : 生成包含对应 .cube LUT、字幕样式、节奏切点轨的剪映专业版样板草稿
3. --export-remotion-props <json>     : 导出可直接用于 Remotion StyleSpectrumShowcase 渲染的 props JSON
4. --export-hyperframes <dir|html>   : 导出对应风格的完整可双击 HyperFrames 工程目录（或单文件 HTML）
5. --apply-pipeline <pipeline_dir>    : 将指定风格谱的字幕模板与 3D LUT 同步并应用到 short-video-studio 流水线目录
"""

from __future__ import annotations

import argparse
import json
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from common import ASSETS_DIR, maybe_reexec_in_venv, read_json, write_json
from export_hyperframes import attach_hyperframes_launchers

maybe_reexec_in_venv()

STYLE_DIR = ASSETS_DIR / "style_spectrum"
LIB_PATH = STYLE_DIR / "style_spectrum_library.json"
JY_PRESETS_PATH = STYLE_DIR / "jianying" / "jianying_style_presets.json"
HF_TEMPLATE_PATH = STYLE_DIR / "hyperframes" / "style-spectrum-template.html"
HF_CSS_PATH = STYLE_DIR / "hyperframes" / "style-spectrum-presets.css"


def load_library() -> Dict[str, Any]:
    if not LIB_PATH.exists():
        raise FileNotFoundError(f"风格谱主数据库不存在，请先运行 build_style_spectrum_assets.py: {LIB_PATH}")
    return read_json(LIB_PATH)


def get_style_entry(lib: Dict[str, Any], style_id: str) -> Dict[str, Any]:
    for s in lib["styles"]:
        if s["style_id"] == style_id:
            return s
    raise KeyError(f"未找到风格 ID: {style_id}，可选范围为 style_01 ~ style_20")


def list_styles(lib: Dict[str, Any]) -> None:
    print("\n=== 六大包装流派母版 (Archetypes) ===")
    for arch in lib["archetypes"]:
        print(f"- {arch['archetype_id']:30s} | {arch['name']} | 成员: {', '.join(arch['members'])}")

    print("\n=== 20 套参考视频「风格谱」总览 ===")
    header = (
        f"{'ID':8s} | {'流派':26s} | {'画幅':5s} | {'BPM':5s} | {'中位镜':6s} | "
        f"{'对齐率':6s} | {'音效/m':6s} | {'字幕字号/位置':12s} | {'风格名称'}"
    )
    print(header)
    print("-" * len(header) + "-" * 20)
    for s in lib["styles"]:
        sh = s["shot_distribution"]
        ba = s["beat_alignment"]
        ax = s["audio_and_sfx"]
        sb = s["subtitle_spec"]
        print(
            f"{s['style_id']:8s} | {s['archetype_id']:26s} | {s['meta']['aspect_ratio']:5s} | "
            f"{ba['estimated_bpm']:5.1f} | {sh['median_sec']:5.2f}s | "
            f"{ba['cut_event_align_3f_pct']:5.1f}% | {ax['sfx_per_min_measured']:6.1f} | "
            f"{sb['font_size_1080p_px']:2d}px @ y={sb['safe_y_ratio']:.2f} | {s['style_name_cn']}"
        )


def export_jianying_template(
    lib: Dict[str, Any],
    style_id: str,
    out_dir: Path,
    cover_title: str,
    subtitle_text: str,
    duration_sec: float,
) -> Path:
    """导出包含对应 .cube LUT 调节层、封面卡、字幕轨与节拍切点骨架的剪映专业版样板草稿。"""
    entry = get_style_entry(lib, style_id)
    jy_all = read_json(JY_PRESETS_PATH)
    jy_preset = jy_all["presets"][style_id]

    out_dir.mkdir(parents=True, exist_ok=True)
    assets_sub = out_dir / "assets"
    assets_sub.mkdir(parents=True, exist_ok=True)

    lut_src = STYLE_DIR / "luts" / f"{style_id}.cube"
    lut_dst = assets_sub / f"{style_id}.cube"
    if lut_src.exists():
        shutil.copy2(lut_src, lut_dst)

    total_us = int(round(duration_sec * 1_000_000))
    fps = float(jy_preset["fps"])
    bpm = float(jy_preset["rhythm"]["bpm"])
    beat_us = int(round((60.0 / bpm) * 1_000_000))
    draft_id = str(uuid.uuid4()).upper()

    # 构建视频占位片段（按该风格中位镜头时长切分节奏槽位，且对齐节拍）
    shot_sec = max(0.8, min(4.0, float(entry["shot_distribution"]["median_sec"])))
    slot_us = int(round(shot_sec * 1_000_000))
    # 若存在强拍对齐，向最近拍子取整
    if beat_us > 0:
        n_beats = max(1, round(slot_us / beat_us))
        slot_us = n_beats * beat_us

    video_materials: List[Dict[str, Any]] = []
    video_segments: List[Dict[str, Any]] = []
    cursor_us = 0
    shot_idx = 1
    while cursor_us < total_us:
        dur_us = min(slot_us, total_us - cursor_us)
        mat_id = str(uuid.uuid4()).upper()
        video_materials.append({
            "id": mat_id,
            "path": f"placeholders/shot_{shot_idx:02d}.mp4",
            "material_name": f"镜头{shot_idx:02d}_{'前3秒钩子' if cursor_us < 3_000_000 else '主叙事'}",
            "type": "video",
            "width": jy_preset["canvas"]["width"],
            "height": jy_preset["canvas"]["height"],
            "duration": dur_us,
        })
        video_segments.append({
            "id": str(uuid.uuid4()).upper(),
            "material_id": mat_id,
            "source_timerange": {"start": 0, "duration": dur_us},
            "target_timerange": {"start": cursor_us, "duration": dur_us},
            "volume": 1.0 if shot_idx == 1 else 0.0,
        })
        cursor_us += dur_us
        shot_idx += 1

    # LUT 调色滤镜素材与特效轨
    lut_mat_id = str(uuid.uuid4()).upper()
    effect_materials = [{
        "id": lut_mat_id,
        "type": "filter",
        "name": f"{style_id}_{entry['color_and_lut']['tone_mood']}",
        "path": str(lut_dst.resolve()),
        "value": jy_preset["color_grade"]["lut_intensity"],
    }]
    effect_segments = [{
        "id": str(uuid.uuid4()).upper(),
        "material_id": lut_mat_id,
        "target_timerange": {"start": 0, "duration": total_us},
    }]

    # 文本素材（封面大字 + 主体字幕示例）
    ts = jy_preset["text_style"]
    cover_tid = str(uuid.uuid4()).upper()
    sub_tid = str(uuid.uuid4()).upper()
    text_materials = [
        {
            "id": cover_tid,
            "type": "text",
            "content": cover_title,
            "font_name": ts["font_name"],
            "font_size": round(ts["font_size_jy"] * jy_preset["cover_layout"]["main_sub_ratio"], 1),
            "text_color": ts["keyword_color_hex"],
            "border_color": ts["stroke_color_hex"],
            "border_width": ts["stroke_width_jy"],
            "style_reference": entry["title_cover_layout"]["layout_name"],
        },
        {
            "id": sub_tid,
            "type": "subtitle",
            "content": subtitle_text,
            "font_name": ts["font_name"],
            "font_size": ts["font_size_jy"],
            "text_color": ts["primary_color_hex"],
            "keyword_color": ts["keyword_color_hex"],
            "border_color": ts["stroke_color_hex"],
            "border_width": ts["stroke_width_jy"],
            "animation": ts["animation_name"],
        },
    ]
    text_segments = [
        {
            "id": str(uuid.uuid4()).upper(),
            "material_id": cover_tid,
            "target_timerange": {"start": 0, "duration": min(2_600_000, total_us)},
            "clip": {"transform": {"x": 0.0, "y": 0.25}},
        },
        {
            "id": str(uuid.uuid4()).upper(),
            "material_id": sub_tid,
            "target_timerange": {"start": 400_000, "duration": max(1_000_000, total_us - 400_000)},
            "clip": {"transform": {"x": 0.0, "y": ts["transform_y"]}},
        },
    ]

    draft_content = {
        "id": draft_id,
        "version": 360000,
        "fps": fps,
        "duration": total_us,
        "canvas_config": jy_preset["canvas"],
        "style_spectrum_meta": {
            "style_id": style_id,
            "style_name_cn": entry["style_name_cn"],
            "archetype_id": entry["archetype_id"],
            "bpm": bpm,
            "j_cut_lead_us": jy_preset["rhythm"]["j_cut_lead_us"],
        },
        "materials": {
            "videos": video_materials,
            "effects": effect_materials,
            "texts": text_materials,
        },
        "tracks": [
            {"id": str(uuid.uuid4()).upper(), "type": "video", "segments": video_segments},
            {"id": str(uuid.uuid4()).upper(), "type": "effect", "segments": effect_segments},
            {"id": str(uuid.uuid4()).upper(), "type": "text", "segments": text_segments},
        ],
    }

    now_us = int(time.time() * 1_000_000)
    draft_meta = {
        "draft_id": draft_id,
        "draft_name": f"style_spectrum_{style_id}_{entry['style_name_cn']}",
        "tm_draft_create": now_us,
        "tm_draft_modified": now_us,
        "tm_duration": total_us,
    }

    write_json(out_dir / "draft_content.json", draft_content)
    write_json(out_dir / "draft_meta_info.json", draft_meta)
    print(f"[apply-style] 已生成剪映样板草稿 ({style_id}) -> {out_dir}")
    return out_dir


def export_remotion_props(
    lib: Dict[str, Any],
    style_id: str,
    out_json: Path,
    cover_title: str,
    subtitle_text: str,
) -> Path:
    entry = get_style_entry(lib, style_id)
    props = {
        "styleId": style_id,
        "coverTitle": cover_title,
        "coverSubTitle": f"{entry['style_name_cn']} · {entry['title_cover_layout']['layout_name']}",
        "subtitleText": subtitle_text,
        "subtitleEnglish": "Replicated from Reference Style Spectrum",
        "keywords": [subtitle_text[:4]] if len(subtitle_text) >= 4 else [subtitle_text],
        "chapters": ["① 钩子", "② 拆解", "③ 对比", "④ 收口"],
        "bullets": [
            f"镜头中位 {entry['shot_distribution']['median_sec']}s · 前3秒{entry['shot_distribution']['first_3s_cuts_count']}切",
            f"BPM {entry['beat_alignment']['estimated_bpm']} · 事件对齐 {entry['beat_alignment']['cut_event_align_3f_pct']}%",
            f"字幕 {entry['subtitle_spec']['font_size_1080p_px']}px · {entry['subtitle_spec']['animation']}",
        ],
    }
    write_json(out_json, props)
    print(f"[apply-style] 已导出 Remotion StyleSpectrumShowcase Props ({style_id}) -> {out_json}")
    return out_json


def export_hyperframes_html(
    lib: Dict[str, Any],
    style_id: str,
    out_target: Path,
    cover_title: str,
    subtitle_text: str,
    duration_sec: float,
) -> Path:
    entry = get_style_entry(lib, style_id)
    html = HF_TEMPLATE_PATH.read_text(encoding="utf-8")
    css = HF_CSS_PATH.read_text(encoding="utf-8")

    # 内联样式表，确保导出后的独立 HTML 可任意路径单文件运行
    html = html.replace('<link rel="stylesheet" href="./style-spectrum-presets.css" />', f"<style>\n{css}\n</style>")

    w = 1080 if entry["meta"]["orientation"] == "portrait_9_16" else 1920
    h = 1920 if entry["meta"]["orientation"] == "portrait_9_16" else 1080
    fps = int(round(entry["meta"]["fps"]))
    css_cls = style_id.replace("_", "-")

    html = html.replace('class="composition-root style-02"', f'class="composition-root {css_cls}"')
    html = html.replace('data-width="1080"', f'data-width="{w}"')
    html = html.replace('data-height="1920"', f'data-height="{h}"')
    html = html.replace('data-fps="30"', f'data-fps="{fps}"')
    html = html.replace('data-duration="9"', f'data-duration="{int(round(duration_sec))}"')
    html = html.replace("STYLE-02 风格谱模板", f"{style_id.upper()} · {entry['style_name_cn']}")
    html = html.replace("没上过大学不丢人", cover_title)
    html = html.replace("没文化才可怕 · 3秒三切强反差", entry["title_cover_layout"]["layout_name"])
    html = html.replace("这是你身上的一个", subtitle_text)

    is_single_html = out_target.suffix.lower() in (".html", ".htm")
    if is_single_html:
        out_target.parent.mkdir(parents=True, exist_ok=True)
        out_target.write_text(html, encoding="utf-8")
        print(f"[apply-style] 已导出独立 HyperFrames 合成 HTML ({style_id}, {w}x{h}@{fps}fps) -> {out_target}")
        return out_target

    out_target.mkdir(parents=True, exist_ok=True)
    index_html = out_target / "index.html"
    index_html.write_text(html, encoding="utf-8")
    hf_cfg = out_target / "hyperframes.json"
    if not hf_cfg.exists():
        write_json(hf_cfg, {
            "$schema": "https://hyperframes.heygen.com/schema/hyperframes.json",
            "media": {"autoProxy": True},
        })
    attach_hyperframes_launchers(out_target)
    print(
        f"[apply-style] 已导出可双击启动的 HyperFrames 工程 ({style_id}, {w}x{h}@{fps}fps) -> {out_target} "
        f"（直接双击 启动HyperFrames工程.command 即可打开 Studio）"
    )
    return out_target


def apply_to_pipeline(lib: Dict[str, Any], style_id: str, pipeline_dir: Path) -> Path:
    """将选定风格谱的 LUT 与字幕/节奏配置同步到 short-video-studio 流水线目录。"""
    entry = get_style_entry(lib, style_id)
    # 1. 将 style_XX.cube 同步到 assets/luts/ 供 step7_color_grade.py 直接读取
    lut_src = STYLE_DIR / "luts" / f"{style_id}.cube"
    lut_assets_dst = ASSETS_DIR / "luts" / f"{style_id}.cube"
    if lut_src.exists():
        shutil.copy2(lut_src, lut_assets_dst)

    pipeline_dir.mkdir(parents=True, exist_ok=True)
    applied_cfg = {
        "style_id": style_id,
        "style_name_cn": entry["style_name_cn"],
        "recommended_track": entry["recommended_track"],
        "subtitle_template_id": style_id,
        "lut_file": f"{style_id}.cube",
        "j_cut_frames": entry["beat_alignment"]["recommended_j_cut_frames"],
        "bpm": entry["beat_alignment"]["estimated_bpm"],
        "shot_median_sec": entry["shot_distribution"]["median_sec"],
        "sfx_per_min_target": entry["audio_and_sfx"]["sfx_per_min_target"],
        "run_pipeline_hint": (
            f"python3 short-video-studio/scripts/run_pipeline.py "
            f"--project-dir {pipeline_dir.parent} --track {entry['recommended_track']} "
            f"--style-id {style_id} --j-cut-frames {entry['beat_alignment']['recommended_j_cut_frames']}"
        ),
    }
    out_cfg = pipeline_dir / "style_spectrum_applied.json"
    write_json(out_cfg, applied_cfg)

    # 若流水线已走到 step3/step4，则自动刷新 step5 字幕与 step7 调色方案
    if (pipeline_dir / "03_audio_events.json").exists():
        from step5_subtitles import run_step5
        run_step5(pipeline_dir, track=entry["recommended_track"], template_id=style_id)
    if (pipeline_dir / "04_edl.json").exists():
        from step7_color_grade import run_step7
        run_step7(pipeline_dir, track=entry["recommended_track"], lut_name=f"{style_id}.cube")

    print(f"[apply-style] 已将 {style_id} 绑定到流水线配置 -> {out_cfg}")
    return out_cfg


def main() -> None:
    parser = argparse.ArgumentParser(description="跨平台「风格谱」一键查询与导出工具 (Remotion / HyperFrames / 剪映)")
    parser.add_argument("--list", action="store_true", help="列出 6 大母版与 20 套风格谱摘要")
    parser.add_argument("--style-id", default="style_02", help="目标风格 ID (style_01 ~ style_20)")
    parser.add_argument("--title", default="先把音轨锁死再剪画面", help="示例封面主标题")
    parser.add_argument("--subtitle", default="真正拉开差距的是节奏与包装", help="示例字幕文本")
    parser.add_argument("--duration", type=float, default=9.0, help="样板导出时长（秒）")
    parser.add_argument("--export-jianying-template", help="导出剪映专业版样板草稿目录路径")
    parser.add_argument("--export-remotion-props", help="导出 Remotion StyleSpectrumShowcase props JSON 路径")
    parser.add_argument("--export-hyperframes", help="导出可直接双击启动的 HyperFrames 工程目录路径（若以 .html 结尾则导出单文件）")
    parser.add_argument("--apply-pipeline", help="应用至指定 pipeline 目录（并同步 LUT 与刷新字幕/调色）")
    args = parser.parse_args()

    lib = load_library()
    if args.list:
        list_styles(lib)
        return

    if args.export_jianying_template:
        export_jianying_template(
            lib, args.style_id, Path(args.export_jianying_template),
            args.title, args.subtitle, args.duration,
        )
    if args.export_remotion_props:
        export_remotion_props(
            lib, args.style_id, Path(args.export_remotion_props),
            args.title, args.subtitle,
        )
    if args.export_hyperframes:
        export_hyperframes_html(
            lib, args.style_id, Path(args.export_hyperframes),
            args.title, args.subtitle, args.duration,
        )
    if args.apply_pipeline:
        apply_to_pipeline(lib, args.style_id, Path(args.apply_pipeline))

    if not any([
        args.export_jianying_template,
        args.export_remotion_props,
        args.export_hyperframes,
        args.apply_pipeline,
    ]):
        entry = get_style_entry(lib, args.style_id)
        print(json.dumps(entry, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
