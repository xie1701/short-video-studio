#!/usr/bin/env python3
"""Build a review sheet, score the edit offline, and validate one director revision."""
from __future__ import annotations

import argparse
import copy
import io
import json
import random
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

from PIL import Image, ImageDraw, ImageFont, ImageOps

from common import find_bin, read_json, write_json
from composition_profile import PROFILE_FILENAME
from director_plan import (
    PLAN_FILENAME,
    _coverage,
    _display_length,
    _short_conclusion,
    load_or_build_director_plan,
    validate_director_plan,
)
from kinetic_choreographer import composition_for_sentence, duplicate_short_captions

REVIEW_TASKS_FILE = "10b_aesthetic_review_tasks.json"
REVIEW_RESULT_FILE = "10b_aesthetic_review.json"
CRITIC_DIMENSIONS = (
    "info_distillation", "rhythm_breathing", "spatial_avoidance",
    "audio_tension", "motion_craft", "theme_fit",
)
QUIET = {"plain_bridge", "clean_breath"}
BREATHING_CUES = {"delay_entry", "silence_drop"}
RUBRIC = {
    "info_distillation": "重点每行不超过 7 字，能在当幕口播中找到出处。",
    "rhythm_breathing": "高光占约 35% 至 55%，相邻高光之间有足够的阅读和留白时间。",
    "spatial_avoidance": "检查字和人脸是否有重叠，大头近景只用前景安全区。",
    "audio_tension": "主动音效 6 至 14 条，相邻至少隔 1.2 秒，配乐在开头或反转处给人声让出空间。",
    "motion_craft": "动效入场有缓动和收稳，淡出干净，文字始终可读。",
    "theme_fit": "商业口播和探店分别检查其色彩、字号层级及包装密度的一致性。",
}
PATCH_GUIDE = {
    "shorten_visual_copy": {"act_id": "act_01", "visual_copy": {"headline": "台词中的短词组"}},
    "convert_to_clean_breath": {"act_id": "act_02"},
    "change_primitive": {"act_id": "act_03", "primitive": "negation_strike"},
    "reduce_sfx_budget": {"max_total": 14},
    "add_silence_drop": {"act_id": "act_03"},
    "suppress_duplicate_captions": {"note": "step6 会自动隐藏与前景大字重复的短底字幕"},
}


def _read_object(path: Path) -> Dict[str, Any]:
    try:
        data = read_json(path)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _read_edit(pipeline_dir: Path) -> Dict[str, Any]:
    files = {
        "plan": PLAN_FILENAME, "profile": PROFILE_FILENAME, "audio": "03_audio_events.json",
        "subtitles": "05_subtitles.json", "package": "06_packaging.json", "mix": "08_mix_metrics.json", "qa": "10_qa_report.json",
    }
    documents = {key: _read_object(pipeline_dir / name) for key, name in files.items()}
    documents["stage"] = documents["package"].get("kinetic_stage") or {}
    stage = documents["stage"]
    if not documents["plan"] and stage.get("director_source"):
        documents["plan"] = {
            "source": stage["director_source"], "theme_profile": stage.get("theme_profile"),
            "highlight_coverage_ratio": stage.get("highlight_coverage_ratio"),
            "acts": stage.get("director_acts") or [], "music_cues": stage.get("music_cues") or [],
        }
    return documents


def _timed_acts(plan: Dict[str, Any], sentences: Dict[str, Dict[str, Any]]) -> list[Dict[str, Any]]:
    result = []
    for act in plan.get("acts") or []:
        spoken = [sentences[sid] for sid in act.get("sentence_ids") or [] if sid in sentences]
        if spoken:
            start = min(s["start_sec"] for s in spoken)
            end = max(s["end_sec"] for s in spoken)
        else:
            start, end = act.get("start_sec"), act.get("end_sec")
        if isinstance(start, (int, float)) and isinstance(end, (int, float)) and 0 <= start < end:
            result.append(dict(act, start_sec=start, end_sec=end))
    return sorted(result, key=lambda act: act["start_sec"])


def _sample_points(acts: list[Dict[str, Any]], duration: float) -> list[Dict[str, Any]]:
    targets = {t: "opening" for t in (0.5, 1.5, 2.5) if not duration or t < duration}
    quiet = [act for act in acts if act.get("primitive") in QUIET]
    representative = random.Random(0).sample(quiet, min(3, len(quiet)))
    for act in acts:
        if act.get("primitive") not in QUIET or act in representative:
            midpoint = round((act["start_sec"] + act["end_sec"]) / 2, 4)
            targets.setdefault(midpoint, "breath" if act in representative else "highlight")

    samples = []
    for sec, reason in sorted(targets.items()):
        act = next((a for a in acts if a["start_sec"] <= sec < a["end_sec"]), {})
        samples.append({"time_sec": sec, "reason": reason, "act_id": act.get("act_id"),
                        "primitive": act.get("primitive"), "extracted": False})
    return samples


def _extract_frame(ffmpeg: str, video: Path, sec: float) -> Optional[Image.Image]:
    try:
        result = subprocess.run(
            [ffmpeg, "-nostdin", "-v", "error", "-ss", f"{sec:.6f}", "-i", str(video),
             "-map", "0:v:0", "-frames:v", "1", "-vf", "scale=240:384:force_original_aspect_ratio=decrease",
             "-c:v", "png", "-f", "image2pipe", "pipe:1"],
            capture_output=True, timeout=12,
        )
        if result.returncode == 0 and result.stdout:
            with Image.open(io.BytesIO(result.stdout)) as image:
                return image.convert("RGB")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return None


def _write_contact_sheet(video: Path, path: Path, samples: list[Dict[str, Any]]) -> bool:
    ffmpeg = find_bin("ffmpeg")
    if not ffmpeg or not samples:
        return False
    width, thumb_h, cell_h = 250, 384, 432
    cols = min(4, len(samples))
    rows = (len(samples) + cols - 1) // cols
    sheet = Image.new("RGB", (width * cols, cell_h * rows), "#141820")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(size=13)
    for idx, sample in enumerate(samples):
        x, y = (idx % cols) * width, (idx // cols) * cell_h
        frame = _extract_frame(ffmpeg, video, sample["time_sec"])
        if frame is not None:
            thumb = ImageOps.pad(frame, (240, thumb_h), color="#0C0F16")
            sheet.paste(thumb, (x + 5, y + 4))
            sample["extracted"] = True
        else:
            draw.text((x + 8, y + thumb_h // 2), "Frame unavailable", fill="#EF8484", font=font)
        draw.text((x + 8, y + thumb_h + 5),
                  f"{sample['time_sec']:.3f}s  {sample['act_id'] or '-'}", fill="#F8FAFC", font=font)
        draw.text((x + 8, y + thumb_h + 23), sample["primitive"] or "-",
                  fill="#BAC4D4", font=font)
    if not any(sample["extracted"] for sample in samples):
        return False
    sheet.save(path, quality=92, subsampling=0)
    return True


def _highlights(stage: Dict[str, Any]) -> list[Dict[str, Any]]:
    """前景高光项：底字幕、过渡条、结构徽标与小号微锚点都不算主要高光。"""
    return [
        item for item in stage.get("items") or []
        if item.get("variant") not in {"bridge_bar", "editorial_ticker"} and not item.get("base_caption")
    ]


def _copy_lines(visual_copy: Dict[str, Any]) -> list[str]:
    return [line for line in [visual_copy.get("headline"), *(visual_copy.get("items") or [])] if line]


def _qa_fail_count(qa: Dict[str, Any]) -> Optional[int]:
    if type(qa.get("fail_count")) is int:
        return qa["fail_count"]
    if isinstance(qa.get("checks"), list) and qa["checks"]:
        return sum(row.get("status") == "FAIL" for row in qa["checks"])
    return None


def _timeline(documents: Dict[str, Any]) -> tuple[Dict[str, Dict[str, Any]], list[Dict[str, Any]], float]:
    sentences = {s["id"]: s for s in documents["audio"].get("sentences") or [] if s.get("id")}
    acts = _timed_acts(documents["plan"], sentences)
    max_end = max((act["end_sec"] for act in acts), default=0.0)
    duration = float(documents["audio"].get("locked_duration_sec") or documents["mix"].get("duration_sec") or max_end)
    return sentences, acts, duration


def build_aesthetic_review_package(
    pipeline_dir: Path, out_image_name: str = "10b_critic_sheet.jpg",
) -> Dict[str, Any]:
    """Write a timed review package with a local contact sheet when video is available."""
    pipeline_dir = Path(pipeline_dir)
    if Path(out_image_name).name != out_image_name or Path(out_image_name).suffix.lower() not in {".jpg", ".jpeg"}:
        raise ValueError("审片联系表需要本目录内的 JPEG 文件名")
    documents = _read_edit(pipeline_dir)
    _, acts, duration = _timeline(documents)
    samples = _sample_points(acts, duration)
    candidates = (pipeline_dir / "output_final.mp4", pipeline_dir.parent / "out" / "output_final.mp4")
    video = next((candidate for candidate in candidates if candidate.is_file()), None)
    has_sheet = video is not None and _write_contact_sheet(video, pipeline_dir / out_image_name, samples)
    plan, profile, mix, qa = (documents[name] for name in ("plan", "profile", "mix", "qa"))
    tasks = {
        "step": "10b_aesthetic_review_tasks",
        "contact_sheet": out_image_name if has_sheet else None,
        "samples": samples,
        "duration_sec": duration,
        "director": {
            "source": plan.get("source"), "theme_profile": plan.get("theme_profile"),
            "highlight_coverage_ratio": plan.get("highlight_coverage_ratio"),
            "acts": [{key: act.get(key) for key in
                      ("act_id", "primitive", "start_sec", "end_sec", "visual_copy", "camera_intent", "sfx_budget")}
                     for act in acts],
            "music_cues": [{key: cue.get(key) for key in ("type", "start_sec", "end_sec", "depth_db", "fade_ms")}
                           for cue in plan.get("music_cues") or []],
        },
        "composition": {
            "primary_stage_mode": profile.get("primary_stage_mode"),
            "allow_behind_person": profile.get("allow_behind_person"),
            "takes": [{key: row.get(key) for key in
                       ("face_area_ratio", "headroom_ratio", "bg_complexity", "safe_fg_y_norm", "allow_behind_person")}
                      for row in profile.get("aroll_profiles") or []],
        },
        "packaging": {
            "highlight_items": len(_highlights(documents["stage"])),
            "behind_person_items": sum(item.get("z_layer") == "behind_person"
                                       for item in documents["stage"].get("items") or []),
            "sfx_count": len(documents["package"].get("sfx_cues") or []),
        },
        "mix": {key: mix.get(key) for key in ("measured_lufs", "true_peak_dbtp", "mono_phase_corr", "music_cue_metrics")},
        "qa": {"fail_count": _qa_fail_count(qa), "warn_count": qa.get("warn_count"),
               "checks": [{key: row.get(key) for key in ("id", "status")} for row in qa.get("checks") or []]},
        "rubric": RUBRIC,
        "result_contract": {"filename": REVIEW_RESULT_FILE, "scores": dict.fromkeys(CRITIC_DIMENSIONS, "0–10"),
                            "patch_suggestions": {"type": PATCH_GUIDE}},
    }
    write_json(pipeline_dir / REVIEW_TASKS_FILE, tasks)
    return tasks


def _overfull_acts(acts: list[Dict[str, Any]], duration: float, upper: float = 0.55, lower: float = 0.35) -> list[str]:
    active = [act for act in acts if act["primitive"] not in QUIET]
    low_priority = sorted(active, key=lambda a: (a.get("dramatic_role") == "hook", a.get("emotion_intensity", 0)))
    removed = []
    for act in low_priority:
        keep = [a for a in active if a is not act]
        ratio = _coverage([(a["start_sec"], a["end_sec"]) for a in keep], duration)
        if ratio >= lower:
            active = keep
            removed.append(act["act_id"])
            if ratio <= upper:
                break
    return removed


def score_aesthetic_review(pipeline_dir: Path) -> Dict[str, Any]:
    """Compute review scores and patch suggestions without writing project files."""
    docs = _read_edit(Path(pipeline_dir))
    plan, profile, stage, mix = (docs[key] for key in ("plan", "profile", "stage", "mix"))
    sentences, acts, duration = _timeline(docs)
    highlights = _highlights(stage)
    plan_lines = []
    patches: list[Dict[str, Any]] = []
    for act in plan.get("acts") or []:
        if act.get("primitive") in QUIET:
            continue
        lines = _copy_lines(act.get("visual_copy") or {})
        plan_lines.extend(lines)
        if any(_display_length(line) > 7 for line in lines):
            patches.append({"type": "shorten_visual_copy", "act_id": act.get("act_id")})
    item_lines = []
    for item in highlights:
        if item.get("variant") == "inline_rich" and isinstance(item.get("inline_parts"), list) and item["inline_parts"]:
            item_lines.extend(str(p.get("text") or "").strip() for p in item["inline_parts"] if str(p.get("text") or "").strip())
        else:
            item_lines.extend(item.get("card_lines") or str(item.get("text_or_asset") or "").splitlines())
    max_chars = max((_display_length(line) for line in [*plan_lines, *item_lines]), default=0)
    duplicates = duplicate_short_captions(docs["stage"].get("items") or [])
    choreo_items = sum(1 for item in highlights if item.get("element_choreo"))
    choreo_ratio = choreo_items / len(highlights) if highlights else 0.0
    active = [(a["start_sec"], a["end_sec"]) for a in acts if a["primitive"] not in QUIET]
    coverage = _coverage(active, duration) if acts and duration else plan.get("highlight_coverage_ratio")
    theme = plan.get("theme_profile") or stage.get("theme_profile")
    cov_lo, cov_hi = (0.50, 0.85) if theme == "style_02_unified" else (0.35, 0.55)
    conversions = _overfull_acts(acts, duration, upper=cov_hi, lower=cov_lo) if coverage is not None and coverage > cov_hi + 0.08 else []
    patches = [patch for patch in patches if patch["act_id"] not in conversions]
    patches.extend({"type": "convert_to_clean_breath", "act_id": aid} for aid in conversions)

    def behind_allowed(context: list[Dict[str, Any]]) -> bool:
        return all(composition_for_sentence(profile, sentence)["allow_behind_person"] for sentence in context or [{}])

    stage_violation = 0
    for item in highlights:
        if item.get("z_layer") == "behind_person":
            start, end = float(item.get("start_sec") or 0), float(item.get("end_sec") or 0)
            context = [s for s in sentences.values() if s["start_sec"] < end and start < s["end_sec"]]
            stage_violation += not behind_allowed(context)
    bad_acts = [a for a in (plan.get("acts") or []) if a["primitive"] == "hero_anchor_plus_danmaku"
                and not behind_allowed([sentences[sid] for sid in a.get("sentence_ids", []) if sid in sentences])]
    patches.extend({"type": "change_primitive", "act_id": a["act_id"], "primitive": "hero_intro_pop"}
                   for a in bad_acts if a["act_id"] not in conversions)

    cues = docs["package"].get("sfx_cues") or []
    sfx_times = sorted(float(c["time_sec"]) for c in cues if c.get("time_sec") is not None)
    crowded_sfx = sum(b - a < 1.2 - 0.0001 for a, b in zip(sfx_times, sfx_times[1:]))
    banned_sfx = sum(cue.get("sfx_role", cue.get("role")) in {"ding", "chime"} for cue in cues)
    music = mix.get("music_cue_metrics") or {}
    has_breathing = any(cue.get("type") in BREATHING_CUES for cue in music.get("applied_cues") or [])
    if len(cues) > 14 or crowded_sfx:
        patches.append({"type": "reduce_sfx_budget", "max_total": 14})
    if not has_breathing and not any(cue.get("type") in BREATHING_CUES for cue in plan.get("music_cues") or []):
        top = max((a for a in acts if a["act_id"] not in conversions and a.get("emotion_intensity", 0) >= 0.70),
                  key=lambda a: a["emotion_intensity"], default=None)
        if top is not None:
            patches.append({"type": "add_silence_drop", "act_id": top["act_id"]})

    # V7 审片去水分诊断：跨幕花字重叠、相邻连推弹回、字幕破词/跨句序数吞并、音画逐字咬合率
    fg_items = [
        it for it in highlights
        if it.get("z_layer", "foreground") == "foreground"
        and it.get("variant") not in {"bridge_bar", "editorial_ticker", "pillar_badge"}
    ]
    cross_overlaps = 0
    for i_a, ia in enumerate(fg_items):
        ga = str(ia.get("group_id") or ia.get("act_id") or "")
        if not ga:
            continue
        sa, ea = float(ia.get("start_sec") or 0.0), float(ia.get("end_sec") or 0.0)
        ya = float(ia.get("y_norm") or 0.0)
        for ib in fg_items[i_a + 1 :]:
            gb = str(ib.get("group_id") or ib.get("act_id") or "")
            if not gb or gb == ga:
                continue
            sb, eb = float(ib.get("start_sec") or 0.0), float(ib.get("end_sec") or 0.0)
            if (min(ea, eb) - max(sa, sb)) > 0.15 and abs(ya - float(ib.get("y_norm") or 0.0)) < 0.14:
                cross_overlaps += 1

    act_rows = list(plan.get("acts") or [])
    camera_snapbacks = sum(
        1 for a, b in zip(act_rows, act_rows[1:])
        if a.get("camera_intent") == "slow_push_115" and b.get("camera_intent") == "slow_push_115"
    )
    sub_entries = [
        e for e in (docs.get("subtitles", {}).get("entries") or [])
        if str(e.get("text") or "").strip() and (not e.get("suppressed") or e.get("suppress_reason") == "kinetic_typography_stage")
    ]
    sub_break_issues = 0
    for idx_s, se in enumerate(sub_entries):
        ptxt = str(se.get("text") or "").replace(" ", "")
        if len(ptxt) >= 6 and ptxt.endswith(("第一", "第二", "第三", "第四")):
            sub_break_issues += 1
        if idx_s + 1 < len(sub_entries):
            ntxt = str(sub_entries[idx_s + 1].get("text") or "").replace(" ", "")
            joint = ptxt[-4:] + ntxt[:4]
            cut_i = len(ptxt[-4:])
            for term_p in ("百度文库", "管理制度", "第一反应", "哪一环", "发给", "别人", "喺𠮶度"):
                p_idx = joint.find(term_p)
                if p_idx >= 0 and p_idx < cut_i < p_idx + len(term_p):
                    sub_break_issues += 1
    beat_synced = sum(1 for item in highlights if item.get("beat_offsets"))
    beat_sync_ratio = round(beat_synced / choreo_items, 4) if choreo_items else None

    theme = plan.get("theme_profile") or stage.get("theme_profile")
    matching_theme = sum(item.get("theme_profile", theme) == theme for item in highlights)
    motion_items = sum(item.get("render_backend") == "web_spring" or bool(item.get("motion_mode")) or bool(item.get("element_choreo")) for item in highlights)
    cov_lo, cov_hi = (0.50, 0.85) if theme == "style_02_unified" else (0.35, 0.55)
    coverage_gap = (max(0, cov_lo - coverage, coverage - cov_hi) if coverage is not None else 0.28)
    sfx_gap = max(0, 6 - len(cues), len(cues) - 14)
    # 底字幕复读大字是信息减法与节奏呼吸的共同扣分项；元素级编舞只给商业赛道加分，
    # 探店风没有商业卡片编舞是设计使然，不能因此扣分。
    duplicate_penalty = min(1.5, 0.35 * len(duplicates))
    choreo_weight = (0.70 + 0.30 * choreo_ratio) if theme == "business_editorial" else 1.0
    if duplicates:
        patches.append({"type": "suppress_duplicate_captions", "count": len(duplicates)})
    scores = {
        "info_distillation": (
            9.4 - 0.65 * max(0, max_chars - 7) - duplicate_penalty - min(1.6, 0.45 * sub_break_issues)
        ) if max_chars else 6.5,
        "rhythm_breathing": 9.4 - 13.0 * coverage_gap - duplicate_penalty,
        "spatial_avoidance": (
            (9.5 if profile else 8.2) - min(6.0, 1.7 * (stage_violation + len(bad_acts))) - min(2.0, 0.8 * cross_overlaps)
        ),
        "audio_tension": 9.5 - (0 if has_breathing else 2.4) - 0.36 * sfx_gap - min(2.5, 0.30 * crowded_sfx + banned_sfx),
        "motion_craft": (
            max(0.0, (8.2 + 1.2 * motion_items / len(highlights) * choreo_weight) - min(1.5, 0.45 * camera_snapbacks))
        )
        if highlights else 6.5,
        "theme_fit": 7.0 + 2.4 * matching_theme / len(highlights)
        if theme in {"style_02_unified", "business_editorial", "store_vibrant"} and highlights else 7.0,
    }
    scores = {key: round(max(0.0, min(10.0, score)), 2) for key, score in scores.items()}
    mean, minimum = round(sum(scores.values()) / len(CRITIC_DIMENSIONS), 2), min(scores.values())
    return {
        "step": "10b_aesthetic_review", "source": "heuristics", "scores": scores,
        "mean_score": mean, "min_score": minimum, "passed": mean >= 8.5 and minimum >= 8.0,
        "metrics": {
            "qa_fail_count": _qa_fail_count(docs["qa"]), "max_headline_chars": max_chars,
            "highlight_ratio": round(coverage, 4) if coverage is not None else None,
            "sfx_count": len(cues), "has_music_breathing": has_breathing,
            "behind_person_violations": stage_violation, "crowded_sfx": crowded_sfx,
            "duplicate_short_captions": len(duplicates),
            "element_choreo_ratio": round(choreo_ratio, 4),
            "cross_act_overlaps": cross_overlaps,
            "camera_snapbacks": camera_snapbacks,
            "subtitle_seg_issues": sub_break_issues,
            "beat_sync_ratio": beat_sync_ratio,
        },
        "patch_suggestions": patches,
    }


def evaluate_review_heuristics(pipeline_dir: Path) -> Dict[str, Any]:
    review = score_aesthetic_review(pipeline_dir)
    write_json(Path(pipeline_dir) / REVIEW_RESULT_FILE, review)
    return review


def _shorten_copy(act: Dict[str, Any], sentences: list[Dict[str, Any]]) -> None:
    visual = act["visual_copy"]

    def short(text: str) -> str:
        if _display_length(text) <= 7:
            return text
        prefix = text[0] + " " if text[0] in "✕✓" else ""
        label = _short_conclusion(text.lstrip("✕✓ "), sentences, limit=7)
        return prefix + label if label else ""

    visual["headline"] = short(visual.get("headline", ""))
    visual["items"] = [shortened for text in visual.get("items", []) if (shortened := short(text))]


def _reduce_sfx_budget(plan: Dict[str, Any], limit: int) -> None:
    acts = plan["acts"]
    for act in acts:
        if act["primitive"] in QUIET:
            act["sfx_budget"] = 0
    excess = sum(a.get("sfx_budget", 0) for a in acts) - limit
    for a in sorted(acts, key=lambda row: (row.get("dramatic_role") == "hook", row.get("emotion_intensity", 0))):
        if excess <= 0:
            break
        cut = min(excess, a.get("sfx_budget", 0))
        a["sfx_budget"] = a.get("sfx_budget", 0) - cut
        excess -= cut


def _apply_patch(
    plan: Dict[str, Any], patch: Dict[str, Any], audio: Dict[str, Any],
) -> None:
    kind = patch.get("type")
    if kind == "reduce_sfx_budget":
        limit = patch.get("max_total", 14)
        if type(limit) is int:
            _reduce_sfx_budget(plan, max(6, min(14, limit)))
        return

    act = next((row for row in plan.get("acts") or [] if row.get("act_id") == patch.get("act_id")), None)
    if act is None:
        return
    ids = set(act.get("sentence_ids") or [])
    sentences = [s for s in audio.get("sentences") or [] if s["id"] in ids]
    if kind == "shorten_visual_copy":
        if isinstance(patch.get("visual_copy"), dict):
            act["visual_copy"] = {**act.get("visual_copy", {}), **patch["visual_copy"]}
        elif act.get("visual_copy"):
            _shorten_copy(act, sentences)
    elif kind == "convert_to_clean_breath" or (kind == "change_primitive" and patch.get("primitive") == "clean_breath"):
        act.update(primitive="clean_breath", visual_copy={}, sfx_budget=0, broll_policy="lock_aroll")
        if act.get("camera_intent") == "side_shift_card":
            act["camera_intent"] = "slow_push_115"
    elif kind == "change_primitive":
        act["primitive"] = patch.get("primitive")
        if "visual_copy" in patch:
            act["visual_copy"] = patch["visual_copy"]
    elif kind == "add_silence_drop" and sentences:
        start = min(s["start_sec"] for s in sentences)
        end = min(start + 0.45, max(s["end_sec"] for s in sentences))
        if end - start < 0.25:
            return
        cues = plan.setdefault("music_cues", [])
        if not any(c["type"] in BREATHING_CUES and c["start_sec"] < end and start < c["end_sec"] for c in cues):
            cues.append({"type": "silence_drop", "start_sec": start, "end_sec": end, "depth_db": -26.0,
                         "fade_ms": 65, "reason": "在重点句给人声留出空间"})


def apply_critic_patches_to_director_plan(
    pipeline_dir: Path, review_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Apply a validated patch set once and leave the previous plan intact on rejection."""
    pipeline_dir = Path(pipeline_dir)
    path = pipeline_dir / PLAN_FILENAME
    review = review_data if review_data is not None else _read_object(pipeline_dir / REVIEW_RESULT_FILE)
    patches = review.get("patch_suggestions") or []
    if not isinstance(patches, list) or not patches:
        return {"applied": False, "reason": "no_patches"}
    if not pipeline_dir.is_dir():
        return {"applied": False, "reason": "missing_pipeline"}
    lock = pipeline_dir / ".aesthetic-revision"
    try:
        lock.mkdir()
    except FileExistsError:
        return {"applied": False, "reason": "revision_in_progress"}

    try:
        original = _read_object(path)
        plan = original or load_or_build_director_plan(pipeline_dir)
        if not plan:
            return {"applied": False, "reason": "missing_plan"}
        if plan.get("critic_revised") is True:
            return {"applied": False, "reason": "already_revised", "critic_revised": True}
        audio = _read_object(pipeline_dir / "03_audio_events.json")
        profile = _read_object(pipeline_dir / PROFILE_FILENAME)
        revised = copy.deepcopy(plan)
        try:
            for patch in patches:
                if isinstance(patch, dict):
                    _apply_patch(revised, patch, audio)
        except (KeyError, TypeError, ValueError):
            return {"applied": False, "reason": "invalid_patch"}
        if revised == plan:
            return {"applied": False, "reason": "no_changes"}

        result = validate_director_plan(revised, audio, profile)
        if not result["ok"]:
            return {"applied": False, "reason": "validation_failed", "errors": result["errors"]}
        if _read_object(path) != original:
            return {"applied": False, "reason": "plan_changed"}
        revised = dict(result["sanitized_plan"], critic_revised=True)
        write_json(path, revised)
        return {"applied": True, "critic_revised": True, "director_plan": PLAN_FILENAME,
                "rerun_from_step": "04_build_edl", "highlight_coverage_ratio": revised["highlight_coverage_ratio"]}
    finally:
        lock.rmdir()


def apply_review_file(pipeline_dir: Path, review_file: Path) -> Dict[str, Any]:
    """消费外部多模态大模型手写的审片结论，校验后原子落盘导演总谱。"""
    review = _read_object(Path(review_file))
    if not review or not isinstance(review.get("patch_suggestions"), list):
        return {"applied": False, "reason": "invalid_review_file"}
    return apply_critic_patches_to_director_plan(Path(pipeline_dir), review)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("package", "heuristics", "apply"))
    parser.add_argument("--pipeline-dir", required=True, type=Path)
    parser.add_argument(
        "--review-file", type=Path,
        help="apply 时消费外部手写的 10b_aesthetic_review.json（经总谱校验后原子落盘）",
    )
    args = parser.parse_args()
    actions = {"package": build_aesthetic_review_package, "heuristics": evaluate_review_heuristics,
               "apply": apply_critic_patches_to_director_plan}
    if args.review_file is not None:
        if args.action != "apply":
            parser.error("--review-file 只支持 apply 动作（把外部审片结论回写总谱）")
        result = apply_review_file(args.pipeline_dir, args.review_file)
    else:
        result = actions[args.action](args.pipeline_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
