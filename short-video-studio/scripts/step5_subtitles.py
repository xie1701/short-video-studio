#!/usr/bin/env python3
"""
[Step 5: 字幕生成]
1. 直接读取锁定音轨 03_audio_events.json 中的逐字时间戳，绝不重新估算。
2. 对核心关键词进行高亮标识。
3. 从内置 4 套可商用字体模板 (templates.json) 中选用一套。
4. 严格把字幕排版限定在平台安全区 (X:[100,860], Y:[1220,1440]) 内。
输出：pipeline/05_subtitles.json 与 pipeline/05_subtitles.ass
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Dict, List

from common import (
    check_bbox_in_safe_zone,
    find_commercial_font,
    load_subtitle_templates,
    maybe_reexec_in_venv,
    read_json,
    write_json,
)


def fmt_ass_time(sec: float) -> str:
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def split_sentence_into_chunks(sent: Dict[str, Any], max_chars: int = 14) -> List[Dict[str, Any]]:
    """把长句按语义与均衡长度切分为适合手机竖屏单行阅读的字幕块（绝不切出<5字残句），并精确继承首尾字时间码。"""
    import math

    words = sent.get("words") or []
    if not words:
        return [{
            "text": sent["text"],
            "start_sec": sent["start_sec"],
            "end_sec": sent["end_sec"],
            "words": [],
            "keywords": sent.get("keywords", []),
        }]

    n_words = len(words)
    # 15 字以内单句完整展示（由 render_mp4.py 自适应缩号确保不超框），避免硬切出“都冇嘅店”等残句
    if n_words <= max_chars + 1:
        return [{
            "text": "".join(x["word"] for x in words),
            "start_sec": words[0]["start_sec"],
            "end_sec": words[-1]["end_sec"],
            "words": list(words),
            "keywords": sent.get("keywords", []),
        }]

    # 超过 15 字则按均等块数切分，保证每块 >= 6 字
    n_parts = max(2, math.ceil(n_words / float(max_chars)))
    base_len = n_words // n_parts
    rem = n_words % n_parts
    chunks: List[Dict[str, Any]] = []
    pos = 0
    for i in range(n_parts):
        part_len = base_len + (1 if i < rem else 0)
        cur = words[pos : pos + part_len]
        pos += part_len
        if cur:
            chunks.append({
                "text": "".join(x["word"] for x in cur),
                "start_sec": cur[0]["start_sec"],
                "end_sec": cur[-1]["end_sec"],
                "words": list(cur),
                "keywords": sent.get("keywords", []),
            })
    return chunks


def run_step5(
    pipeline_dir: Path,
    track: str = "store",
    template_id: Optional[str] = None,
) -> Dict[str, Any]:
    audio_events = read_json(pipeline_dir / "03_audio_events.json")
    templates = load_subtitle_templates()

    if not template_id or template_id not in templates:
        template_id = "store_bold_yellow" if track == "store" else "creator_clean_white"
    tpl = templates[template_id]
    font_path = find_commercial_font(tpl.get("font_candidates"))

    entries: List[Dict[str, Any]] = []
    ass_dialogues: List[str] = []
    kw_hex_bgr = tpl["keyword_color"].lstrip("#")
    ass_kw_color = f"&H00{kw_hex_bgr[4:6]}{kw_hex_bgr[2:4]}{kw_hex_bgr[0:2]}&"

    idx = 1
    for sent in audio_events["sentences"]:
        for ch in split_sentence_into_chunks(sent):
            # 估算包围盒并检验 UI 安全区
            w_est = min(760, max(200, len(ch["text"]) * tpl["font_size"]))
            bbox = {
                "x": max(100, 480 - w_est // 2),
                "y": int(tpl["safe_y_center"]) - 48,
                "w": w_est,
                "h": 96,
            }
            safe_ok, safe_msg = check_bbox_in_safe_zone(bbox)
            if not safe_ok:
                # 若越界则强制夹回安全区
                bbox["x"] = max(100, min(bbox["x"], 860 - bbox["w"]))
                bbox["y"] = min(bbox["y"], 1440 - bbox["h"])
                safe_ok, safe_msg = check_bbox_in_safe_zone(bbox)

            entries.append({
                "id": f"sub_{idx:03d}",
                "sentence_id": sent["id"],
                "text": ch["text"],
                "start_sec": round(float(ch["start_sec"]), 4),
                "end_sec": round(float(ch["end_sec"]), 4),
                "words": ch["words"],
                "bbox": bbox,
                "safe_zone_ok": safe_ok,
                "safe_msg": safe_msg,
            })

            # 生成带关键词变色标签的 ASS 行
            ass_text = ""
            for w in ch["words"]:
                if w.get("is_keyword"):
                    ass_text += f"{{\\c{ass_kw_color}\\b1}}{w['word']}{{\\r}}"
                else:
                    ass_text += w["word"]
            if not ass_text:
                ass_text = ch["text"]
            ass_dialogues.append(
                f"Dialogue: 0,{fmt_ass_time(ch['start_sec'])},{fmt_ass_time(ch['end_sec'])},Default,,0,0,0,,{ass_text}"
            )
            idx += 1

    ass_content = "\n".join([
        "[Script Info]",
        "ScriptType: v4.00+",
        "PlayResX: 1080",
        "PlayResY: 1920",
        "WrapStyle: 2",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        tpl["ass_style"],
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        *ass_dialogues,
    ]) + "\n"

    (pipeline_dir / "05_subtitles.ass").write_text(ass_content, encoding="utf-8")

    out_json = {
        "step": "05_subtitles",
        "template_id": template_id,
        "template_name": tpl["name"],
        "font_path": font_path,
        "font_license": tpl["license"],
        "template_config": tpl,
        "count": len(entries),
        "entries": entries,
    }
    write_json(pipeline_dir / "05_subtitles.json", out_json)
    print(f"[step5] 字幕生成完成：{len(entries)} 条（模板 `{template_id}`, 字体 `{Path(font_path).name if font_path else 'default'}`）-> 05_subtitles.json")
    return out_json


def main() -> None:
    maybe_reexec_in_venv()
    parser = argparse.ArgumentParser(description="Step 5: 逐字时间戳关键词高亮字幕生成")
    parser.add_argument("--pipeline-dir", required=True)
    parser.add_argument("--track", choices=["store", "creator"], default="store")
    parser.add_argument("--template", help="字幕模板ID")
    args = parser.parse_args()
    run_step5(Path(args.pipeline_dir), track=args.track, template_id=args.template)


if __name__ == "__main__":
    main()
