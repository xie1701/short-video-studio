#!/usr/bin/env python3
"""Generate STYLE_SPECTRUM_REPORT.md from the style library — single source of truth.

禁止手写第二套数：报告里的每个数字都从 style_spectrum_library.json 读，
库里的每个数字都由 dev_tools/build_style_library_v4.py 从草稿推导。

    /tmp/jyvenv/bin/python dev_tools/gen_style_report.py
"""

from __future__ import annotations

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
LIB = ROOT / "short-video-studio/assets/style_spectrum/style_spectrum_library.json"
OUT = ROOT / "short-video-studio/assets/style_spectrum/STYLE_SPECTRUM_REPORT.md"


def pct(v) -> str:
    return f"{v*100:.1f}%" if isinstance(v, (int, float)) else "—"


def main() -> None:
    lib = json.loads(LIB.read_text(encoding="utf-8"))
    styles = lib["styles"]
    policy = lib.get("measurement_policy") or {}
    lines: list[str] = []
    add = lines.append

    add(f"# 24 套视觉与镜头模板实测报告（v{lib['version']}）")
    add("")
    add("> 本文件由 `dev_tools/gen_style_report.py` 从 `style_spectrum_library.json` 自动生成，**禁止手改**。")
    add("> 库数据由 `dev_tools/build_style_library_v4.py` 从**解密后的剪映主稿** `draft_content.json` 推导，")
    add("> 原始指标见 `forensic/source_manifest.json`（含每套源文件的 sha256）。")
    add("")
    add("## 口径声明")
    add("")
    add(f"- **源版本**：{policy.get('save_version', 'draft_content.json')}；每套记录了 `source_hash`，换保存版本即视为换源。")
    add("- **转场**：24/24 源草稿转场数为 0（112 套全库交叉验证），因此所有风格默认硬切，渲染器带硬约束不允许开 xfade。")
    add("- **插入画面占比**：库里的值是从源草稿量出来的**风格先验**，不是硬指标；运行时按实际素材在 ±15 个百分点内自适应"
      "（`broll_target_hard=false`），KPI 只看是否落在带内并记录调整原因。")
    add("- **字体**：`源字体` 是草稿里真实引用的字体（多为商用/平台字体）；`交付字体` 是找不到源字体时的商用替代。"
      "把源字体文件放进 `fonts/<style_id>/` 即可字形 1:1，未放时 `font_substitution.exact_1to1=false`。")
    add("- **中位镜长**：源草稿只提供主轨（V1）中位值，全片与主轨共用同一测量口径。")
    add("")
    add("## 逐套实测")
    add("")
    add("| 编号 | 风格 | 源指纹 | 时长 | 镜数(主轨/全片) | 中位镜长 | 覆盖率 | 关键帧/min | 底框 | 字数上限 | 源字体 → 交付字体 | 顶栏 | 金句 | 巨字 | 抠像 | 蒙版 | 贴纸 | 变速 | 音效/min | 转场 |")
    add("| :---: | :--- | :--- | ---: | :---: | ---: | ---: | ---: | ---: | :--- | ---: | :--- | :---: | :---: | :---: | ---: | :--- | ---: | ---: | ---: | :---: |")
    for s in styles:
        sd = s["shot_distribution"]
        t = s["packaging_techniques"]
        sub = s["subtitle_spec"]
        a = s["audio_and_sfx"]
        bg = sub.get("bg_shape")
        bg_label = "无" if bg == "none" else f"{'圆角' if bg == 'rounded' else '方角'} {sub.get('bg_ratio_measured', 0)*100:.0f}% {sub.get('bg_box_color')}"
        masks = "、".join(f"{k}×{v}" for k, v in (t.get("mask_shapes") or {}).items()) or "—"
        font = f"{sub.get('source_sub_font_family') or sub.get('source_font_family') or '—'} → {sub.get('font_family','—')}"
        add(
            f"| `{s['style_id']}` | {s['style_name_cn']} | {s.get('source_actual','—')} "
            f"| {s['meta']['duration_sec']:.2f}s | {sd.get('main_track_clips','—')}/{sd.get('total_shots','—')} "
            f"| {sd.get('median_sec','—')}s | {pct(sd.get('broll_coverage_ratio'))} "
            f"| {sd.get('kf_per_min','—')} "
            f"| {bg_label} | {sub.get('max_chars_per_line') or '无基础字幕'} | {font} "
            f"| {'✓' if t.get('persistent_top_bar') else '—'} | {'✓' if t.get('quote_citation_card') else '—'} "
            f"| {t.get('giant_impact_count',0)} | {t.get('ai_matting_cutout',0)} | {masks} "
            f"| {t.get('sticker_count',0)} | {t.get('speed_changed_segs',0)} | {a.get('sfx_per_min_measured','—')} "
            f"| {s.get('visual_blueprint',{}).get('transition_style','none')} |"
        )
    add("")
    add("## 动态文字（展示字）密度")
    add("")
    add("| 编号 | 展示字条数 | 条/min | 主字体 | 主色 | 前 3 名入场动效 |")
    add("| :---: | ---: | ---: | :--- | :--- | :--- |")
    for s in styles:
        d = s.get("display_text_spec") or {}
        fonts = d.get("fonts") or []
        colors = d.get("colors") or []
        anims = d.get("anims_top") or []
        add(
            f"| `{s['style_id']}` | {d.get('count') or 0} | {d.get('per_min') or 0} "
            f"| {fonts[0][0] if fonts else '—'} | {colors[0][0] if colors else '—'} "
            f"| {'、'.join(f'{n}({c})' for n, c in anims[:3]) or '—'} |"
        )
    add("")
    add("## 音频与包装元素")
    add("")
    add("| 编号 | 音效/min | BGM | 包装元素 |")
    add("| :---: | ---: | :--- | :--- |")
    for s in styles:
        a = s["audio_and_sfx"]
        bgm = "、".join(a.get("bgm_names") or []) or "无（纯人声）"
        els = "、".join(s["transition_and_packaging"].get("elements") or [])
        add(f"| `{s['style_id']}` | {a.get('sfx_per_min_measured','—')} | {bgm} | {els} |")
    add("")
    add("## 一致性与验证")
    add("")
    add("- 校验脚本：`dev_tools/verify_style_parity.py`，逐字段比对库 ↔ 源 manifest，任何一项不符即非零退出。")
    add("- 机器可读矩阵：`reports/style_parity_matrix.csv`（由同一脚本生成）。")
    add("- 验收 KPI 与 fixture 方法：`reports/style_parity_review_2026-11-22.md` §7。")
    add("")
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)} ({len(lines)} lines)")


if __name__ == "__main__":
    main()
