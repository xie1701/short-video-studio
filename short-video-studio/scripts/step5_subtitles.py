#!/usr/bin/env python3
"""
[Step 5: 字幕生成与统一权威布局]
1. 读取锁定音轨 03_audio_events.json 的逐字时间戳，按 >=0.30s 真静音呼吸组 + 92% 像素宽断句（绝不留 <=3 字残句）。
2. 修复数字旁中文标点残留（仅保住夹在数字间的小数点 . 与时间冒号 : 以及数字后 %），尊重模板 punct_stripped 配置。
3. 基于唯一权威 SUBTITLE_SAFE_ZONE 与 01_manifest.json 裁切后精确人脸框一次性完成像素测宽与避脸位移，
   写出权威 final_bbox（同步写入 bbox），供 render / jianying / hyperframes / qa 四端同源消费。
4. B9 双通道互斥：code_broll 在场覆盖 >=60% 时标记 suppressed=True 并跳过上屏。
输出：pipeline/05_subtitles.json 与 pipeline/05_subtitles.ass
"""

from __future__ import annotations

import argparse
import math
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from common import (
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    SUBTITLE_SAFE_ZONE,
    check_bbox_in_safe_zone,
    find_commercial_font,
    load_subtitle_templates,
    maybe_reexec_in_venv,
    read_json,
    write_json,
)

# 仅保留夹在数字间的小数点/冒号(如 3.5 / 18:30)以及数字后的%，彻底清掉紧贴数字的中文逗号句号！
_NON_CORE_PUNCT_RE = re.compile(r"(?<!\d)[.:：](?!\d)|(?<!\d)[.:：](?=\d)|(?<=\d)[.:：](?!\d)|[，。、；！？,!?;“”‘’\"'（）()]+")

_PIL_FONT_CACHE: Dict[Tuple[str, int], Any] = {}


def strip_display_punctuation(text: str) -> str:
    return _NON_CORE_PUNCT_RE.sub("", str(text or ""))


def fmt_ass_time(sec: float) -> str:
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _escape_ass_text(txt: str) -> str:
    return txt.replace("{", r"\{").replace("}", r"\}")


def _get_cached_font(font_path: Optional[str], font_size: int):
    if not font_path:
        return None
    key = (font_path, int(font_size))
    if key not in _PIL_FONT_CACHE:
        try:
            from PIL import ImageFont
            _PIL_FONT_CACHE[key] = ImageFont.truetype(font_path, int(font_size))
        except Exception:
            _PIL_FONT_CACHE[key] = None
    return _PIL_FONT_CACHE[key]


def _measure_text_px(text: str, font_path: Optional[str], font_size: int) -> Tuple[int, int]:
    fnt = _get_cached_font(font_path, font_size)
    if fnt is not None:
        try:
            from PIL import Image, ImageDraw
            d = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
            bb = d.textbbox((0, 0), text, font=fnt)
            return int(max(40, (bb[2] - bb[0]) + max(8, len(text) * 3))), int(max(font_size + 16, (bb[3] - bb[1]) + 28))
        except Exception:
            pass
    return int(len(text) * font_size * 1.04), int(font_size * 1.35 + 24)


def _rect_iou_px(b1: Dict[str, int], b2: Dict[str, float]) -> float:
    x1 = max(float(b1["x"]), float(b2["x"]))
    y1 = max(float(b1["y"]), float(b2["y"]))
    x2 = min(float(b1["x"] + b1["w"]), float(b2["x"] + b2["w"]))
    y2 = min(float(b1["y"] + b1["h"]), float(b2["y"] + b2["h"]))
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    if inter <= 0.0:
        return 0.0
    a1 = max(1.0, float(b1["w"] * b1["h"]))
    a2 = max(1.0, float(b2["w"] * b2["h"]))
    return inter / max(1e-6, a1 + a2 - inter)


def _compute_authoritative_subtitle_bbox(
    text: str,
    tpl: Dict[str, Any],
    font_path: Optional[str],
    face_boxes_px: List[Dict[str, float]],
) -> Dict[str, int]:
    """按唯一 SUBTITLE_SAFE_ZONE 精确计算尺寸并一次性避开人脸五官，产出三端统一 final_bbox。"""
    base_fs = int(tpl.get("font_size", 54))
    kw_scale = float(tpl.get("keyword_scale", 1.16))
    eff_fs = int(round(base_fs * min(1.22, max(1.0, (1.0 + kw_scale) * 0.5))))
    safe_w_cap = SUBTITLE_SAFE_ZONE["x_max"] - SUBTITLE_SAFE_ZONE["x_min"]  # 780px
    center_x = (SUBTITLE_SAFE_ZONE["x_min"] + SUBTITLE_SAFE_ZONE["x_max"]) // 2  # 470

    est_w, est_h = _measure_text_px(text, font_path, eff_fs)
    box_w = min(safe_w_cap, max(200, est_w + 36))
    box_h = min(220, max(84, est_h + 16))
    box_x = max(SUBTITLE_SAFE_ZONE["x_min"], min(SUBTITLE_SAFE_ZONE["x_max"] - box_w, center_x - box_w // 2))

    # 钳位 safe_y_center，杜绝如 style_18 等 y=1420+半高>1480 的越界问题
    safe_yc = int(tpl.get("safe_y_center", 1340))
    box_y = max(SUBTITLE_SAFE_ZONE["y_min"], min(SUBTITLE_SAFE_ZONE["y_max"] - box_h, safe_yc - box_h // 2))
    cand = {"x": int(box_x), "y": int(box_y), "w": int(box_w), "h": int(box_h)}

    if face_boxes_px:
        if max(_rect_iou_px(cand, fb) for fb in face_boxes_px) > 0.01:
            for try_y in (
                SUBTITLE_SAFE_ZONE["y_max"] - box_h,
                max(SUBTITLE_SAFE_ZONE["y_min"], box_y - 120),
                SUBTITLE_SAFE_ZONE["y_min"],
            ):
                alt = dict(cand, y=int(try_y))
                if max(_rect_iou_px(alt, fb) for fb in face_boxes_px) <= 0.01:
                    cand = alt
                    break
    return cand


def split_sentence_into_chunks(
    sent: Dict[str, Any],
    max_chars: int = 13,
    font_path: Optional[str] = None,
    font_size: int = 54,
    max_safe_px: int = 698,
    strip_punct: bool = True,
) -> List[Dict[str, Any]]:
    """把长句按真静音 (>=0.30s) 呼吸组与 92% 安全宽拆分为无残句字幕块（放行 4 字，严禁 <=3 字残组）。"""
    clean_fn = strip_display_punctuation if strip_punct else (lambda s: str(s or "").strip())
    raw_words = sent.get("words") or []
    words = [w for w in raw_words if clean_fn(str(w.get("word", ""))).strip()]
    if not words:
        clean_txt = clean_fn(str(sent.get("text", ""))).strip()
        return [{
            "text": clean_txt or str(sent.get("text", "")).strip(),
            "start_sec": sent["start_sec"],
            "end_sec": sent["end_sec"],
            "words": [],
            "keywords": sent.get("keywords", []),
        }]

    breath_groups: List[List[Dict[str, Any]]] = []
    cur_grp: List[Dict[str, Any]] = [words[0]]
    for prev_w, cur_w in zip(words, words[1:]):
        gap = float(cur_w["start_sec"]) - float(prev_w["end_sec"])
        if gap >= 0.30 and len(cur_grp) >= 4:
            breath_groups.append(cur_grp)
            cur_grp = [cur_w]
        else:
            cur_grp.append(cur_w)
    if cur_grp:
        if breath_groups and len(cur_grp) <= 3:
            breath_groups[-1].extend(cur_grp)
        else:
            breath_groups.append(cur_grp)

    chunks: List[Dict[str, Any]] = []
    for grp in breath_groups:
        txt = "".join(clean_fn(str(x["word"])) for x in grp)
        px_w, _ = _measure_text_px(txt, font_path, font_size)
        if len(grp) <= max_chars + 1 and px_w <= max_safe_px:
            chunks.append({
                "text": txt,
                "start_sec": grp[0]["start_sec"],
                "end_sec": grp[-1]["end_sec"],
                "words": list(grp),
                "keywords": sent.get("keywords", []),
            })
            continue

        cap = max(6, min(max_chars, int(len(grp) * (max_safe_px / max(1, px_w))) if px_w > max_safe_px else max_chars))
        n_parts = max(2, math.ceil(len(grp) / float(cap)))
        base_len = len(grp) // n_parts
        rem = len(grp) % n_parts
        pos = 0
        sub_slices: List[List[Dict[str, Any]]] = []
        for i in range(n_parts):
            part_len = base_len + (1 if i < rem else 0)
            nxt_pos = pos + part_len
            if 4 <= nxt_pos < len(grp) - 4 and grp[nxt_pos - 1].get("is_keyword") and grp[nxt_pos].get("is_keyword"):
                nxt_pos += 1
            nxt_pos = min(len(grp), nxt_pos)
            sub_slices.append(grp[pos:nxt_pos])
            pos = nxt_pos
        if pos < len(grp) and sub_slices:
            sub_slices[-1].extend(grp[pos:])
        if len(sub_slices) >= 2 and len(sub_slices[-1]) <= 3:
            tail = sub_slices.pop()
            sub_slices[-1].extend(tail)

        for sl in sub_slices:
            if not sl:
                continue
            chunks.append({
                "text": "".join(clean_fn(str(x["word"])) for x in sl),
                "start_sec": sl[0]["start_sec"],
                "end_sec": sl[-1]["end_sec"],
                "words": list(sl),
                "keywords": sent.get("keywords", []),
            })
    return chunks


def run_step5(
    pipeline_dir: Path,
    track: str = "store",
    template_id: Optional[str] = None,
) -> Dict[str, Any]:
    audio_events = read_json(pipeline_dir / "03_audio_events.json")
    edl_p = pipeline_dir / "04_edl.json"
    edl = read_json(edl_p) if edl_p.exists() else {"clips": []}
    man_p = pipeline_dir / "01_manifest.json"
    manifest = read_json(man_p) if man_p.exists() else {"items": []}

    # 获取每段规范素材在 1080x1920 裁切后画布的精确人脸框
    faces_by_mp4: Dict[str, List[Dict[str, float]]] = {}
    for it in manifest.get("items", []):
        mp = str(it.get("normalized_mp4", ""))
        f_list = []
        for f_obj in it.get("faces") or []:
            fb = f_obj.get("bbox")
            if isinstance(fb, dict):
                f_list.append({
                    "x": float(fb["x"]) * CANVAS_WIDTH,
                    "y": float(fb["y"]) * CANVAS_HEIGHT,
                    "w": float(fb["w"]) * CANVAS_WIDTH,
                    "h": float(fb["h"]) * CANVAS_HEIGHT,
                })
        if mp and f_list:
            faces_by_mp4[mp] = f_list

    code_ranges: List[Tuple[float, float, str]] = [
        (float(c["timeline_in_sec"]), float(c["timeline_out_sec"]), str(c["clip_id"]))
        for c in edl.get("clips", [])
        if c.get("track_role") == "code_broll"
    ]
    templates = load_subtitle_templates()
    if not template_id or template_id not in templates:
        template_id = "store_bold_yellow" if track == "store" else "creator_clean_white"
    tpl = templates[template_id]
    font_path = find_commercial_font(tpl.get("font_candidates"))
    strip_punct = bool(tpl.get("punct_stripped", True))

    entries: List[Dict[str, Any]] = []
    ass_dialogues: List[str] = []
    kw_hex_bgr = tpl["keyword_color"].lstrip("#")
    ass_kw_color = f"&H00{kw_hex_bgr[4:6]}{kw_hex_bgr[2:4]}{kw_hex_bgr[0:2]}&"

    idx = 1
    suppressed_count = 0
    for sent in audio_events["sentences"]:
        for ch in split_sentence_into_chunks(
            sent,
            font_path=font_path,
            font_size=int(tpl.get("font_size", 54)),
            strip_punct=strip_punct,
        ):
            st_sec = round(float(ch["start_sec"]), 4)
            en_sec = round(float(ch["end_sec"]), 4)
            sub_dur = max(0.05, en_sec - st_sec)

            # 收集该字幕在时间线上重叠镜头的精确人脸框以执行源头避脸
            active_faces: List[Dict[str, float]] = []
            for c in edl.get("clips", []):
                if float(c["timeline_in_sec"]) < en_sec and float(c["timeline_out_sec"]) > st_sec:
                    active_faces.extend(faces_by_mp4.get(str(c.get("media_path", "")), []))

            final_bbox = _compute_authoritative_subtitle_bbox(ch["text"], tpl, font_path, active_faces)
            safe_ok, safe_msg = check_bbox_in_safe_zone(final_bbox)

            suppressed = False
            suppressed_by = None
            for cs, ce, cid in code_ranges:
                ov = max(0.0, min(en_sec, ce) - max(st_sec, cs))
                if ov / sub_dur >= 0.60:
                    suppressed = True
                    suppressed_by = cid
                    suppressed_count += 1
                    break

            entry_obj: Dict[str, Any] = {
                "id": f"sub_{idx:03d}",
                "sentence_id": sent["id"],
                "text": ch["text"],
                "start_sec": st_sec,
                "end_sec": en_sec,
                "words": ch["words"],
                "bbox": final_bbox,
                "final_bbox": final_bbox,
                "safe_zone_ok": safe_ok,
                "safe_msg": safe_msg,
                "suppressed": suppressed,
            }
            if suppressed:
                entry_obj["suppressed_by"] = suppressed_by
                entry_obj["suppress_reason"] = "B9_code_broll_dual_channel_dedup"

            entries.append(entry_obj)

            if not suppressed:
                clean_fn = strip_display_punctuation if strip_punct else (lambda s: str(s or ""))
                ass_text = ""
                for w in ch["words"]:
                    w_txt = _escape_ass_text(clean_fn(str(w["word"])))
                    if not w_txt:
                        continue
                    if w.get("is_keyword"):
                        ass_text += f"{{\\c{ass_kw_color}\\b1}}{w_txt}{{\\r}}"
                    else:
                        ass_text += w_txt
                if not ass_text:
                    ass_text = _escape_ass_text(ch["text"])
                ass_dialogues.append(
                    f"Dialogue: 0,{fmt_ass_time(st_sec)},{fmt_ass_time(en_sec)},Default,,0,0,0,,{ass_text}"
                )
            idx += 1

    ass_content = "\n".join([
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {CANVAS_WIDTH}",
        f"PlayResY: {CANVAS_HEIGHT}",
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
        "suppressed_count": suppressed_count,
        "entries": entries,
    }
    write_json(pipeline_dir / "05_subtitles.json", out_json)
    print(
        f"[step5] 字幕生成完成：{len(entries)} 条（B9让位挂起 {suppressed_count} 条，"
        f"模板 `{template_id}`, 字体 `{Path(font_path).name if font_path else 'default'}`）-> 05_subtitles.json"
    )
    return out_json


def main() -> None:
    maybe_reexec_in_venv()
    parser = argparse.ArgumentParser(description="Step 5: 逐字时间戳字幕与统一避脸布局生成")
    parser.add_argument("--pipeline-dir", required=True)
    parser.add_argument("--track", choices=["store", "creator"], default="store")
    parser.add_argument("--template", help="字幕模板ID")
    args = parser.parse_args()
    run_step5(Path(args.pipeline_dir), track=args.track, template_id=args.template)


if __name__ == "__main__":
    main()
