#!/usr/bin/env python3
"""
[协作闸门、风格复刻洁净室与完工自动清理守卫]
四合一高内聚模块，严守 KISS 不碎拆：
1. record-stop / audit-stops : 3 必停点打扰审计账本 (stop1_script / stop2_blind_audio / stop3_final，预算=3)
2. lock / verify / approve-final : 方案指纹哈希锁 (approval_lock.json) + 双态人工签字 (MACHINE_PASS_PENDING_HUMAN -> APPROVED)
3. replication-diff : 风格复刻洁净室 (DCT pHash <= 6 禁画面复用 + 12字OCR禁文案照搬) + 7 维客观偏差 + 乱序 A/B/C 盲测页
4. tidy : 完工后一键同步成品至 out/ 并彻底清理全部无用过程临时文件（确保自包含导出工程零断链）
"""

from __future__ import annotations

import argparse
import hashlib
import html
import math
import re
import secrets
import shutil
import statistics
import subprocess
import sys
import tempfile
import urllib.parse
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from common import (
    DEFAULT_FPS,
    SCENE_CUT_THRESHOLD,
    SUBTITLE_DEFAULT_Y_CENTER,
    compute_bt709_luma,
    ffprobe_media,
    find_bin,
    find_commercial_font,
    maybe_reexec_in_venv,
    read_json,
    run_cmd,
    sha256_file,
    write_json,
)

maybe_reexec_in_venv()

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from step2_transcribe import _clean_zh_text
from step3_lock_audio import detect_bgm_beats
from vision_inspector import inspect_images

STOP_TYPES = ("stop1_script", "stop2_blind_audio", "stop3_final")
BUDGET = 3
LOCK_FILENAME = "approval_lock.json"

TEMP_SUBDIRS = (
    "_render_final",
    "_render_preview",
    "01_normalized",
    "01_code_scenes",
    "02_tts_segments",
    "__pycache__",
)

TEMP_PROCESS_FILES = (
    "02_tts_voice.wav",
    "08_voice_denoised.wav",
    "08_premix_raw.wav",
    "08_premix_nosfx_raw.wav",
    "08_mixed_master_adj.wav",
    "output_preview.mp4",
)

DEEP_CLEAN_SUBDIRS = ("04_generated_broll",)
DEEP_CLEAN_FILES = (
    "03_locked_voice.wav",
    "03_locked_bgm.wav",
    "06_sfx_track.wav",
    "08_stem_nosfx.wav",
    "08_voice_master.wav",
    "08_bgm_ducked.wav",
    "08_sfx_master.wav",
    "08_mixed_master.wav",
)

DELIVERABLE_SYNC_FILES = (
    "output_final.mp4",
    "09_cover.jpg",
    "09_cover_3x4.jpg",
    "09_ui_safe_preview.jpg",
    "09_cover_v1.jpg",
    "09_cover_v2.jpg",
    "09_cover_v3.jpg",
    "09_hook_v1.mp4",
    "09_hook_v2.mp4",
    "09_hook_v3.mp4",
    "09_title_cover.md",
    "09_title_cover.json",
    "09_hook_variants.json",
    "03_blind_audio.m4a",
    "00_contact_sheet.jpg",
    "00_compliance_report.md",
    "00_compliance_report.json",
    "asset_license_manifest.json",
    "04_edl.json",
    "04_source_review.json",
    "08_locked_audio_receipt.json",
    "render_profile.json",
    "10_qa_report.md",
    "10_qa_report.json",
    "10_blind_review_pack.md",
    "10_action_strips.jpg",
    "10_code_verify_evidence.json",
    "00_code_spec.json",
    "SPEC.md",
    "STRUCTURE.md",
    "VERIFY.md",
    "decisions_made.md",
    "11_delivery_summary.md",
    "review_player.html",
)


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _load_lock_state(pipeline_dir: Path) -> Dict[str, Any]:
    p = pipeline_dir / LOCK_FILENAME
    if p.is_file():
        try:
            return read_json(p)
        except Exception:
            pass
    return {
        "version": 1,
        "created_at": _now_iso(),
        "interruptions": [],
        "plan_sha256": None,
        "locked_at": None,
        "human_approved": False,
        "approved_at": None,
        "approval_note": "",
    }


def compute_plan_digest(pipeline_dir: Path) -> str:
    """对已定版的 03~08（及纯代码模式下的 00_code_spec.json）全核心流水线决策工件计算组合 SHA-256 指纹。"""
    h = hashlib.sha256()
    if (pipeline_dir / "00_code_spec.json").is_file():
        h.update(b"00_code_spec.json")
        h.update((pipeline_dir / "00_code_spec.json").read_bytes())
    for name in (
        "03_audio_events.json",
        "04_edl.json",
        "05_subtitles.json",
        "06_packaging.json",
        "07_color_plan.json",
        "08_mix_metrics.json",
        "04_source_review.json",
        "asset_license_manifest.json",
    ):
        p = pipeline_dir / name
        h.update(name.encode("utf-8"))
        if p.is_file():
            h.update(p.read_bytes())
    return h.hexdigest()


def package_digest(directory: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(directory.rglob("*")):
        if path.is_file() and path.name != ".DS_Store":
            digest.update(path.relative_to(directory).as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(bytes.fromhex(sha256_file(path)))
    return digest.hexdigest()


def record_stop(pipeline_dir: Path, stop_type: str, summary: str = "", rehearsal: bool = False) -> Dict[str, Any]:
    if stop_type not in STOP_TYPES:
        raise ValueError(f"stop_type 必须为 {STOP_TYPES} 之一")
    st = _load_lock_state(pipeline_dir)
    st.setdefault("interruptions", []).append({
        "type": stop_type,
        "summary": summary,
        "at": _now_iso(),
        "rehearsal": bool(rehearsal),
        "source": "human_interaction",
    })
    write_json(pipeline_dir / LOCK_FILENAME, st)
    return st


def record_milestone(pipeline_dir: Path, step: str, summary: str) -> None:
    path = pipeline_dir / "pipeline_progress.json"
    progress = read_json(path) if path.is_file() else {}
    progress[step] = {"status": summary, "at": _now_iso()}
    write_json(path, progress)


def audit_stops(pipeline_dir: Path, require_all: bool = False) -> Dict[str, Any]:
    st = _load_lock_state(pipeline_dir)
    items = [item for item in st.get("interruptions", [])
             if item.get("source") == "human_interaction" and not item.get("rehearsal")]
    types = [i.get("type") for i in items]
    over = len(items) > BUDGET
    dup = len(types) != len(set(types))
    missing = [t for t in STOP_TYPES if t not in types] if require_all else []
    passed = (not over) and (not dup) and (not missing)
    return {
        "passed": passed,
        "count": len(items),
        "budget": BUDGET,
        "types": types,
        "over_budget": over,
        "duplicate_stops": dup,
        "missing_stops": missing,
    }


def lock_plan(pipeline_dir: Path, note: str = "用户拍板锁定方案") -> Dict[str, Any]:
    st = _load_lock_state(pipeline_dir)
    digest = compute_plan_digest(pipeline_dir)
    edl = read_json(pipeline_dir / "04_edl.json") if (pipeline_dir / "04_edl.json").is_file() else {}
    final_mp4 = pipeline_dir / "output_final.mp4"
    st["plan_sha256"] = digest
    st["output_final_sha256"] = sha256_file(final_mp4) if final_mp4.is_file() else None
    st["locked_at"] = _now_iso()
    st["clip_count"] = int(edl.get("clip_count", 0))
    st["duration_sec"] = float(edl.get("total_duration_sec", 0.0))
    st["lock_note"] = note
    st["human_approved"] = False
    st["approved_at"] = None
    write_json(pipeline_dir / LOCK_FILENAME, st)
    return st


def verify_lock(pipeline_dir: Path) -> Tuple[int, str, Dict[str, Any]]:
    st = _load_lock_state(pipeline_dir)
    saved = st.get("plan_sha256")
    if not saved:
        return 2, "尚未执行方案指纹锁（未落 approval_lock.json）", st
    cur = compute_plan_digest(pipeline_dir)
    if cur != saved:
        return 1, f"方案在拍板锁定后已被改动：锁={saved[:12]} != 当前={cur[:12]}，请重新预览并拍板", st
    saved_mp4_sha = st.get("output_final_sha256")
    final_mp4 = pipeline_dir / "output_final.mp4"
    if saved_mp4_sha and final_mp4.is_file():
        cur_mp4_sha = sha256_file(final_mp4)
        if cur_mp4_sha != saved_mp4_sha:
            return 1, f"成片 output_final.mp4 在锁版后被替换/篡改（{cur_mp4_sha[:12]} != {saved_mp4_sha[:12]}）", st
    return 0, f"方案锁有效 (sha={cur[:12]}, 锁定于 {st.get('locked_at')})", st


def approve_final(
    pipeline_dir: Path,
    note: str = "",
    reviewed_checks: tuple[str, ...] = (),
    out_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    qa_p = pipeline_dir / "10_qa_report.json"
    if not qa_p.is_file():
        raise FileNotFoundError("未找到 10_qa_report.json，请先运行自动+黑盒质检。")
    qa = read_json(qa_p)
    if not qa.get("all_passed", qa.get("machine_passed")):
        raise RuntimeError("必检项尚未通过，禁止人工终审签字。")
    required = set(qa.get("required_manual_checks", []))
    if not required <= set(reviewed_checks) or (required and not note.strip()):
        raise RuntimeError(f"请完成并记录人工审查后签字：{', '.join(sorted(required))}")

    final_mp4 = pipeline_dir / "output_final.mp4"
    if not final_mp4.is_file():
        raise FileNotFoundError("缺少 output_final.mp4，无法签字。")

    st = _load_lock_state(pipeline_dir)
    cur_digest = compute_plan_digest(pipeline_dir)
    cur_mp4_sha = sha256_file(final_mp4)
    if qa.get("plan_sha256") != cur_digest or qa.get("output_final_sha256") != cur_mp4_sha:
        raise RuntimeError("质检报告与当前方案或成片指纹不匹配，请重新质检再签字。")
    for target, expected in qa.get("required_package_hashes", {}).items():
        package = pipeline_dir / f"{target}_draft"
        if not package.is_dir() or package_digest(package) != expected:
            raise RuntimeError(f"{target} 工程与质检版本不一致，请重新质检再签字。")
    from source_review import source_review_status
    provenance_path = pipeline_dir / "04_source_review.json"
    if provenance_path.is_file() and source_review_status(read_json(provenance_path)) == "FAIL":
        raise RuntimeError("主张来源已缺失或变更，请更新来源并重新质检。")
    if (pipeline_dir / "asset_license_manifest.json").is_file():
        from independent_verifier import verify_compliance_and_license_blackbox
        if not verify_compliance_and_license_blackbox(pipeline_dir)["passed"]:
            raise RuntimeError("授权原件或实际资产已变更，请重新质检。")
    if st.get("plan_sha256") and st["plan_sha256"] != cur_digest:
        raise RuntimeError(f"当前 EDL/字幕/包装/音色方案已被修改 (sha={cur_digest[:12]} != 锁={st['plan_sha256'][:12]})，请先重新渲染并质检。")
    if st.get("output_final_sha256") and st["output_final_sha256"] != cur_mp4_sha:
        raise RuntimeError("output_final.mp4 与锁定指纹不一致，禁止人工签字。")
    if not st.get("plan_sha256"):
        st["plan_sha256"] = cur_digest
        st["locked_at"] = _now_iso()
    st["output_final_sha256"] = cur_mp4_sha

    st["human_approved"] = True
    st["approved_at"] = _now_iso()
    st["approval_note"] = note
    st["reviewed_checks"] = sorted(set(reviewed_checks))
    st["export_mode"] = qa.get("export_mode", "auto")
    st["required_package_hashes"] = qa.get("required_package_hashes", {})
    write_json(pipeline_dir / LOCK_FILENAME, st)

    qa["approval_status"] = "APPROVED"
    qa["pending_manual_checks"] = []
    if qa.get("deliverables", {}).get("mp4", {}).get("status") == "PENDING_REVIEW":
        qa["deliverables"]["mp4"]["status"] = "PASS"
    if "jianying_import" in reviewed_checks and "jianying" in qa.get("deliverables", {}):
        qa["deliverables"]["jianying"]["host_import"] = "USER_VERIFIED"
    qa["human_approved_at"] = st["approved_at"]
    qa["human_approval_note"] = note
    write_json(qa_p, qa)

    md_p = pipeline_dir / "10_qa_report.md"
    if md_p.is_file():
        txt = md_p.read_text(encoding="utf-8")
        for state in ("MACHINE_PASS_PENDING_HUMAN", "REVIEW_REQUIRED"):
            txt = txt.replace(f"🟡 **待人工终审确认 ({state})**", "✅ **机检与人工终审完成 (APPROVED)**")
        txt = txt.replace("MACHINE_PASS_PENDING_HUMAN", "APPROVED")
        for pend_banner in (
            "🟡 **全部必检通过，待人工终审确认 (APPROVED)**",
            "🟡 **全部必检通过，待人工终审确认**",
            "🟡 **全部机检通过，待人工三项终审签字 (APPROVED)**",
            "🟡 **全部机检通过，待人工三项终审签字**",
        ):
            txt = txt.replace(pend_banner, "✅ **机检与人工终审全部通过 (APPROVED)**")
        md_p.write_text(txt, encoding="utf-8")

    # 同步最新 APPROVED 报告至 out/ 目录，确保外部交付口径完全一致
    target_out = out_dir or (pipeline_dir.parent / "out")
    archived_mp4 = target_out / final_mp4.name
    if archived_mp4.is_file() and sha256_file(archived_mp4) == cur_mp4_sha:
        shutil.copy2(qa_p, target_out / "10_qa_report.json")
        if md_p.is_file():
            shutil.copy2(md_p, target_out / "10_qa_report.md")
        for doc_name in ("11_delivery_summary.md", "decisions_made.md"):
            for doc_p in (pipeline_dir / doc_name, target_out / doc_name):
                if doc_p.is_file():
                    dtxt = doc_p.read_text(encoding="utf-8")
                    dtxt = dtxt.replace("待人工终审确认", "已通过全部检查并人工确认")
                    dtxt = dtxt.replace("MACHINE_PASS_PENDING_HUMAN", "APPROVED")
                    dtxt = dtxt.replace("REVIEW_REQUIRED", "APPROVED")
                    doc_p.write_text(dtxt, encoding="utf-8")
    return st


def verify_archive(out_dir: Path) -> dict[str, Any]:
    receipt = read_json(out_dir / "archive_receipt.json")
    missing, changed = [], []
    for name, expected in receipt["files"].items():
        path = out_dir / name
        if not path.is_file():
            missing.append(name)
        elif sha256_file(path) != expected:
            changed.append(name)
    qa_file = out_dir / "10_qa_report.json"
    if qa_file.is_file():
        qa = read_json(qa_file)
        if (qa.get("output_final_sha256") != receipt["files"].get("output_final.mp4")
                or qa.get("plan_sha256") != receipt["plan_sha256"]):
            changed.append(qa_file.name)
    return {"passed": "output_final.mp4" in receipt["files"] and not missing and not changed,
            "missing": missing, "changed": changed, "files": len(receipt["files"])}


# ------------------------------------------------------------------
# 风格复刻洁净室 + 7 维客观偏差 + 乱序盲测页
# ------------------------------------------------------------------

def _frame_phash_64(gray_32: np.ndarray) -> int:
    try:
        from scipy.fft import dctn
        dct = dctn(gray_32.astype(np.float32), norm="ortho")
    except Exception:
        # 纯 numpy 1D DCT 分离回退
        n = 32
        k = np.arange(n)[:, None]
        x = np.arange(n)[None, :]
        c = np.cos(np.pi * (2 * x + 1) * k / (2 * n))
        c[0, :] *= 1.0 / math.sqrt(n)
        c[1:, :] *= math.sqrt(2.0 / n)
        dct = c @ gray_32.astype(np.float32) @ c.T
    ac_coeffs = dct[:8, :8].ravel()[1:]
    med = float(np.median(ac_coeffs))
    bits = 0
    for b in (dct[:8, :8].ravel() > med):
        bits = (bits << 1) | int(bool(b))
    return bits


def _hamming_dist(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def _rgb_to_lab_mean(rgb: np.ndarray) -> Tuple[float, float, float]:
    arr = rgb.astype(np.float32) / 255.0
    lin = np.where(arr <= 0.04045, arr / 12.92, ((arr + 0.055) / 1.055) ** 2.4)
    r, g, b = lin[:, 0], lin[:, 1], lin[:, 2]
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 1.00000
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883
    eps = 0.008856
    fx = np.where(x > eps, np.cbrt(x), 7.787 * x + 16 / 116)
    fy = np.where(y > eps, np.cbrt(y), 7.787 * y + 16 / 116)
    fz = np.where(z > eps, np.cbrt(z), 7.787 * z + 16 / 116)
    L = float(np.mean(116 * fy - 16))
    a = float(np.mean(500 * (fx - fy)))
    bv = float(np.mean(200 * (fy - fz)))
    return L, a, bv


def _analyze_video_profile(video_path: Path, tmp_dir: Path, tag: str) -> Dict[str, Any]:
    ffmpeg = find_bin("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("需要 ffmpeg 执行风格复刻对比分析。")

    frames_dir = tmp_dir / f"frames_{tag}"
    frames_dir.mkdir(parents=True, exist_ok=True)
    run_cmd([
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(video_path),
        "-vf", "fps=1,scale=540:960:force_original_aspect_ratio=increase,crop=540:960",
        "-q:v", "3", str(frames_dir / "f_%04d.jpg"),
    ])
    frame_files = sorted(frames_dir.glob("f_*.jpg"))

    phashes: List[int] = []
    lumas: List[float] = []
    sats: List[float] = []
    text_labs: List[Tuple[float, float, float]] = []
    vis_map = inspect_images(frame_files)
    ocr_ys: List[float] = []
    ocr_stream: List[str] = []

    for fp in frame_files:
        with Image.open(fp) as im:
            g32 = np.asarray(im.convert("L").resize((32, 32), Image.Resampling.BILINEAR))
            rgb = np.asarray(im.convert("RGB"), dtype=np.float32)
            hsv = np.asarray(im.convert("HSV"), dtype=np.float32)
        phashes.append(_frame_phash_64(g32))
        luma = compute_bt709_luma(rgb)
        lumas.append(float(np.mean(luma)))
        sats.append(float(np.mean(hsv[:, :, 1])))

        vr = vis_map.get(str(fp), {})
        for t in vr.get("texts") or []:
            txt = _clean_zh_text(str(t.get("text", "")))
            if txt:
                ocr_stream.append(txt)
            ty = float(t.get("y", 0.0))
            if ty >= 0.48:
                ocr_ys.append(ty * 1920.0)
                x0 = max(0, int(float(t["x"]) * 540))
                x1 = min(540, max(x0 + 4, int((float(t["x"]) + float(t["w"])) * 540)))
                y0 = max(0, int(float(t["y"]) * 960))
                y1 = min(960, max(y0 + 4, int((float(t["y"]) + float(t["h"])) * 960)))
                roi_arr = rgb[y0:y1, x0:x1]
                roi = roi_arr.reshape(-1, 3)
                if len(roi) > 8 and roi_arr.shape[0] >= 3 and roi_arr.shape[1] >= 3:
                    roi_l_2d = compute_bt709_luma(roi_arr)
                    border_l = np.concatenate([roi_l_2d[0, :], roi_l_2d[-1, :], roi_l_2d[:, 0], roi_l_2d[:, -1]])
                    bg_l = float(np.mean(border_l))
                    roi_l = roi_l_2d.ravel()
                    # 根据边缘背景明暗自适应采样：亮底采深色字（25%低分位），暗底采亮色字（75%高分位）
                    if bg_l >= 135.0:
                        fg_px = roi[roi_l <= np.percentile(roi_l, 25)]
                    else:
                        fg_px = roi[roi_l >= np.percentile(roi_l, 75)]
                    if len(fg_px) > 0:
                        text_labs.append(_rgb_to_lab_mean(fg_px))

    # 切镜检测计算中位镜长 & 前 3 秒切镜数
    sc_res = run_cmd([
        ffmpeg, "-hide_banner", "-i", str(video_path),
        "-vf", f"select='gt(scene,{SCENE_CUT_THRESHOLD})',showinfo", "-f", "null", "-",
    ], check=False)
    cut_times = sorted(set(float(m) for m in re.findall(r"pts_time:([0-9.]+)", sc_res.stderr)))
    probe = ffprobe_media(video_path)
    dur = float(probe.get("format", {}).get("duration") or max(1.0, len(frame_files)))
    all_bounds = [0.0] + [t for t in cut_times if 0.25 < t < dur - 0.25] + [dur]
    shot_durs = [b - a for a, b in zip(all_bounds, all_bounds[1:]) if b - a >= 0.2]
    med_shot = round(statistics.median(shot_durs) if shot_durs else dur, 2)
    cuts_3s = sum(1 for t in cut_times if 0.1 <= t <= 3.1)

    wav_p = tmp_dir / f"{tag}.wav"
    run_cmd([
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(video_path), "-vn", "-acodec", "pcm_s16le", "-ar", "48000", "-ac", "2",
        str(wav_p),
    ], check=False)
    bpm = 120.0
    if wav_p.is_file() and wav_p.stat().st_size > 2048:
        bpm = float(detect_bgm_beats(wav_p).get("bpm", 120.0))

    avg_lab = (
        tuple(round(sum(x[k] for x in text_labs) / len(text_labs), 1) for k in range(3))
        if text_labs else (95.0, 0.0, 0.0)
    )
    return {
        "video": str(video_path.resolve()),
        "duration_sec": round(dur, 2),
        "phashes": phashes,
        "ocr_joined": "".join(ocr_stream),
        "median_shot_sec": med_shot,
        "first_3s_cuts": cuts_3s,
        "sub_y_px": round(statistics.median(ocr_ys), 1) if ocr_ys else float(SUBTITLE_DEFAULT_Y_CENTER),
        "mean_luma": round(statistics.mean(lumas), 1) if lumas else 128.0,
        "mean_sat": round(statistics.mean(sats), 1) if sats else 64.0,
        "bpm": round(bpm, 1),
        "sub_lab": avg_lab,
    }


def run_replication_diff(
    ref_mp4: Path,
    out_mp4: Path,
    out_dir: Path,
    contrast_mp4: Optional[Path] = None,
) -> Dict[str, Any]:
    """执行风格复刻洁净室防抄袭门禁 (pHash/OCR) + 7 维客观复刻偏差 + 乱序盲测页。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="svs_repdiff_") as td:
        td_p = Path(td)
        ref = _analyze_video_profile(ref_mp4, td_p, "ref")
        out = _analyze_video_profile(out_mp4, td_p, "out")

    # 1. Clean-Room 洁净室防抄袭门禁
    min_hamming = 64
    for ph_r in ref["phashes"]:
        for ph_o in out["phashes"]:
            d = _hamming_dist(ph_r, ph_o)
            if d < min_hamming:
                min_hamming = d

    ref_s = ref["ocr_joined"]
    out_s = out["ocr_joined"]
    ocr_12_hits: List[str] = []
    if len(ref_s) >= 12 and len(out_s) >= 12:
        for i in range(0, len(ref_s) - 11):
            win = ref_s[i : i + 12]
            if win in out_s:
                ocr_12_hits.append(win)
                break

    phash_block = bool(ref["phashes"] and out["phashes"] and min_hamming <= 6)
    ocr_block = len(ocr_12_hits) > 0
    clean_room_block = phash_block or ocr_block

    # 2. 7 维客观复刻偏差表
    de = math.sqrt(sum((a - b) ** 2 for a, b in zip(ref["sub_lab"], out["sub_lab"])))
    dims = [
        {"dim": "中位镜长 (s)", "ref": ref["median_shot_sec"], "out": out["median_shot_sec"],
         "ok": abs(out["median_shot_sec"] - ref["median_shot_sec"]) <= max(0.5, ref["median_shot_sec"] * 0.20), "tol": "±20%"},
        {"dim": "前 3s 切镜次数", "ref": ref["first_3s_cuts"], "out": out["first_3s_cuts"],
         "ok": abs(out["first_3s_cuts"] - ref["first_3s_cuts"]) <= 1, "tol": "±1 次"},
        {"dim": "字幕重心 Y (px)", "ref": ref["sub_y_px"], "out": out["sub_y_px"],
         "ok": abs(out["sub_y_px"] - ref["sub_y_px"]) <= 40.0, "tol": "±40px"},
        {"dim": "音频节奏 BPM", "ref": ref["bpm"], "out": out["bpm"],
         "ok": abs(out["bpm"] - ref["bpm"]) <= max(8.0, ref["bpm"] * 0.10), "tol": "±10%"},
        {"dim": "画面平均亮度", "ref": ref["mean_luma"], "out": out["mean_luma"],
         "ok": abs(out["mean_luma"] - ref["mean_luma"]) <= 18.0, "tol": "±18"},
        {"dim": "画面饱和度均值", "ref": ref["mean_sat"], "out": out["mean_sat"],
         "ok": abs(out["mean_sat"] - ref["mean_sat"]) <= 24.0, "tol": "±24"},
        {"dim": "字幕字色 ΔE", "ref": str(ref["sub_lab"]), "out": f"ΔE={de:.1f}",
         "ok": de <= 15.0, "tol": "ΔE ≤ 15"},
    ]

    report = {
        "step": "replication_diff",
        "clean_room_passed": not clean_room_block,
        "clean_room_block": clean_room_block,
        "min_phash_hamming": min_hamming,
        "phash_block": phash_block,
        "ocr_12char_overlap_hits": ocr_12_hits,
        "ocr_block": ocr_block,
        "dimensions_matched": sum(1 for d in dims if d["ok"]),
        "dimensions_total": len(dims),
        "dimensions": dims,
    }
    write_json(out_dir / "replication_diff.json", report)

    md_lines = [
        "# 风格复刻洁净室与 7 维客观偏差报告",
        "",
        f"- **Clean-Room 洁净室防抄袭判定**：{'❌ BLOCK（存在像素复用或12字连抄风险）' if clean_room_block else '✅ PASS（原创画面与文案）'}（最小帧 pHash 汉明距=`{min_hamming}`，连续12字OCR重合=`{len(ocr_12_hits)}`处）",
        f"- **客观偏差匹配度**：{report['dimensions_matched']}/{report['dimensions_total']} 项落在容差带内",
        "",
        "| 维度 | 参考原片 | 我方成片 | 容差阈值 | 是否达标 |",
        "| :--- | :---: | :---: | :---: | :---: |",
    ]
    for d in dims:
        md_lines.append(f"| {d['dim']} | {d['ref']} | {d['out']} | {d['tol']} | {'✅' if d['ok'] else '⚠️'} |")
    (out_dir / "replication_diff.md").write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    # 3. 乱序 A/B/C 风格盲测 HTML 页（使用真随机洗牌并将答案分离存至独立 answer_key JSON，绝不内嵌剧透）
    samples = [("参考风格原片 (Reference)", ref_mp4), ("我方复刻成片 (Ours)", out_mp4)]
    if contrast_mp4 and contrast_mp4.is_file():
        samples.append(("异风格对照组 (Contrast)", contrast_mp4))
    secrets.SystemRandom().shuffle(samples)

    cards_html = []
    answer_mapping: Dict[str, Dict[str, str]] = {}
    for label_char, (role_name, vp) in zip(("A", "B", "C"), samples):
        cards_html.append(
            f'<div class="card"><h3>样片 {label_char}</h3>'
            f'<video controls playsinline src="{html.escape(vp.resolve().as_uri())}"></video>'
            f'<label>风格相似度 (1-5)：<input type="range" class="blind-score" data-label="{label_char}" min="1" max="5" value="4" '
            f'oninput="this.nextElementSibling.textContent=this.value"><span>4</span></label></div>'
        )
        answer_mapping[label_char] = {"role": role_name, "filename": vp.name}

    write_json(out_dir / "blind_test_answer_key.json", {
        "created_at": _now_iso(),
        "mapping": answer_mapping,
    })

    blind_html = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<title>风格复刻乱序盲测页 (Blind A/B/C Test)</title>
<style>
  body {{ font-family: -apple-system, sans-serif; background: #0f172a; color: #f8fafc; margin: 24px; }}
  .grid {{ display: flex; gap: 20px; flex-wrap: wrap; }}
  .card {{ background: #1e293b; padding: 16px; border-radius: 14px; width: 300px; border: 1px solid #334155; }}
  video {{ width: 100%; border-radius: 10px; background: #000; }}
  button {{ margin-top: 16px; background: #38bdf8; color: #0f172a; border: none; padding: 10px 16px; border-radius: 8px; font-weight: 700; cursor: pointer; }}
  .hint {{ margin-top: 16px; color: #94a3b8; font-size: 13px; }}
</style>
</head>
<body>
  <h2>风格复刻乱序盲测 (请凭直觉判断哪两条气质同源)</h2>
  <div class="grid">{''.join(cards_html)}</div>
  <button type="button" onclick="exportBlindJson()">导出盲测结果 JSON</button>
  <p class="hint">注：为保证双盲客观性，本页不含任何标签剧透。完成打分并导出 JSON 后，可打开同目录 <code>blind_test_answer_key.json</code> 核对真相。</p>
  <script>
    function exportBlindJson() {{
      const scores = Array.from(document.querySelectorAll('.blind-score')).map(el => ({{
        sample: el.dataset.label,
        similarity_score: Number(el.value)
      }}));
      const payload = {{ exported_at: new Date().toISOString(), scores }};
      const blob = new Blob([JSON.stringify(payload, null, 2)], {{ type: 'application/json' }});
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = 'blind_test_results.json';
      a.click();
    }}
  </script>
</body>
</html>
"""
    (out_dir / "replication_blind_test.html").write_text(blind_html, encoding="utf-8")
    return report


def generate_action_strips(
    video_path: Path,
    edl_path: Optional[Path],
    out_jpg: Path,
) -> Optional[Path]:
    """生成动态帧带拼图（10_action_strips.jpg）：0–3s 每 0.20s 抽帧 + 至多 4 个关键切点 ±0.30s 验证动作跳变。"""
    if not video_path.is_file():
        return None
    ffmpeg = find_bin("ffmpeg")
    if not ffmpeg:
        return None

    try:
        info = ffprobe_media(video_path)
        streams = info.get("streams") or []
        vs = next((s for s in streams if s.get("codec_type") == "video"), None)
        dur = float(info.get("format", {}).get("duration") or (vs or {}).get("duration") or 0.0)
    except Exception:
        return None
    if dur <= 0.05:
        return None

    max_t = max(0.02, dur - 0.05)
    sample_map: Dict[float, str] = {}

    open_end = min(3.0, max_t)
    cur_t = 0.0
    while cur_t <= open_end + 1e-4:
        k = round(min(max_t, cur_t), 2)
        sample_map.setdefault(k, "OPEN")
        cur_t += 0.20

    if edl_path and edl_path.is_file():
        try:
            edl = read_json(edl_path)
            cand_cuts = [float(c.get("timeline_in_sec", 0.0)) for c in (edl.get("clips") or [])[1:]]
            selected_cuts: List[float] = []
            for tc in cand_cuts:
                if tc < 3.1 or tc > max_t - 0.1:
                    continue
                if all(abs(tc - prev_tc) >= 1.0 for prev_tc in selected_cuts):
                    selected_cuts.append(tc)
                if len(selected_cuts) >= 4:
                    break
            for tc in selected_cuts:
                for off in (-0.30, -0.15, 0.0, 0.15, 0.30):
                    t_samp = round(max(0.0, min(max_t, tc + off)), 2)
                    sample_map.setdefault(t_samp, "CUT")
        except Exception:
            pass

    samples = sorted(sample_map.items(), key=lambda x: x[0])[:40]
    if not samples:
        return None

    # 计算实际帧率以支持单遍 FFmpeg select='eq(n,...)' 极速抽帧（40 进程 -> 1 进程）
    fps_val = float(DEFAULT_FPS)
    try:
        r_str = str((vs or {}).get("avg_frame_rate") or (vs or {}).get("r_frame_rate") or f"{DEFAULT_FPS}/1")
        if "/" in r_str:
            num, den = r_str.split("/", 1)
            if float(den) > 0:
                fps_val = max(1.0, float(num) / float(den))
        else:
            fps_val = max(1.0, float(r_str))
    except Exception:
        fps_val = float(DEFAULT_FPS)

    max_frame_idx = max(0, int(dur * fps_val) - 1)
    dedup_targets: List[Tuple[int, float, str]] = []
    seen_fn: Set[int] = set()
    for t_val, tag in samples:
        fn = min(max_frame_idx, max(0, int(round(t_val * fps_val))))
        if fn in seen_fn:
            continue
        seen_fn.add(fn)
        dedup_targets.append((fn, t_val, tag))

    def _grab_one(item: Tuple[int, float, str], tmp_dir: Path) -> Optional[Tuple[int, float, str, Path]]:
        idx_i, t_val, tag = item
        dst = tmp_dir / f"fb_{idx_i:03d}.jpg"
        proc = subprocess.run(
            [
                ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                "-ss", f"{t_val:.3f}",
                "-i", str(video_path),
                "-frames:v", "1",
                "-vf", "scale=240:-2",
                "-q:v", "4",
                str(dst),
            ],
            capture_output=True,
        )
        if proc.returncode == 0 and dst.is_file() and dst.stat().st_size > 0:
            return (idx_i, t_val, tag, dst)
        return None

    with tempfile.TemporaryDirectory(prefix="svs_strips_") as td:
        td_path = Path(td)
        frames: List[Tuple[int, float, str, Path]] = []
        if dedup_targets:
            select_expr = "+".join(f"eq(n,{fn})" for fn, _, _ in dedup_targets)
            max_end_t = min(dur + 0.1, dedup_targets[-1][1] + 0.35)
            single_proc = subprocess.run(
                [
                    ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                    "-i", str(video_path),
                    "-t", f"{max_end_t:.3f}",
                    "-vf", f"select='{select_expr}',scale=240:-2",
                    "-vsync", "0",
                    "-q:v", "4",
                    str(td_path / "strip_%03d.jpg"),
                ],
                capture_output=True,
            )
            extracted = sorted(td_path.glob("strip_*.jpg"))
            if single_proc.returncode == 0 and len(extracted) == len(dedup_targets):
                for idx_i, ((_, t_val, tag), fp) in enumerate(zip(dedup_targets, extracted)):
                    frames.append((idx_i, t_val, tag, fp))
            else:
                with ThreadPoolExecutor(max_workers=4) as pool:
                    raw_res = list(pool.map(
                        lambda it: _grab_one((it[0], it[1][1], it[1][2]), td_path),
                        enumerate(dedup_targets),
                    ))
                frames = [r for r in raw_res if r is not None]
        if not frames:
            return None

        frames.sort(key=lambda x: x[0])
        with Image.open(frames[0][3]) as im0:
            fw, fh = im0.size

        hdr_h = 28
        pad = 6
        cols = min(8, max(1, len(frames)))
        rows = math.ceil(len(frames) / cols)
        tile_w = fw + pad * 2
        tile_h = fh + hdr_h + pad * 2
        sheet = Image.new("RGB", (cols * tile_w, rows * tile_h), (12, 16, 26))
        draw = ImageDraw.Draw(sheet)

        font_p = find_commercial_font()
        try:
            font_badge = ImageFont.truetype(font_p, 16) if font_p else ImageFont.load_default()
        except Exception:
            font_badge = ImageFont.load_default()

        for slot_idx, (_, t_val, tag, fpath) in enumerate(frames):
            r = slot_idx // cols
            c = slot_idx % cols
            x0 = c * tile_w + pad
            y0 = r * tile_h + pad
            badge_bg = (30, 41, 59) if tag == "OPEN" else (69, 26, 3)
            draw.rectangle([x0, y0, x0 + fw, y0 + hdr_h], fill=badge_bg)
            draw.text((x0 + 8, y0 + 5), f"{tag} {t_val:.2f}s", fill=(250, 204, 21), font=font_badge)
            with Image.open(fpath) as tile_im:
                sheet.paste(tile_im.convert("RGB"), (x0, y0 + hdr_h))

        out_jpg.parent.mkdir(parents=True, exist_ok=True)
        sheet.save(out_jpg, format="JPEG", quality=88)
        return out_jpg


# ------------------------------------------------------------------
# 完工同步成品 & 安全清理无用过程文件 (tidy)
# ------------------------------------------------------------------

def _collect_protected_paths(pipeline_dir: Path) -> Set[Path]:
    """扫描已导出工程中的实际文件引用，绝不删除仍在被引用的路径。"""
    protected: Set[Path] = set()
    jy_dir = pipeline_dir / "jianying_draft"
    if jy_dir.is_dir():
        for xp in jy_dir.glob("*.fcpxml"):
            try:
                root = ET.parse(xp).getroot()
                for mr in root.iter("media-rep"):
                    src = mr.attrib.get("src", "")
                    if src.startswith("file://"):
                        protected.add(Path(urllib.parse.unquote(urllib.parse.urlparse(src).path)).resolve())
                    elif src:
                        protected.add((xp.parent / urllib.parse.unquote(src)).resolve())
            except Exception:
                pass
    hf_dir = pipeline_dir / "hyperframes_draft"
    if hf_dir.is_dir():
        hf = hf_dir / "index.html"
        if hf.is_file():
            txt = hf.read_text(encoding="utf-8", errors="ignore")
            for rel in re.findall(r'(?:data-src|src)=["\']([^"\']+)["\']', txt):
                if rel.startswith(("http://", "https://", "data:")):
                    continue
                protected.add((hf_dir / rel).resolve())
    return protected


def _dir_size(p: Path) -> int:
    if not p.exists():
        return 0
    if p.is_file():
        return p.stat().st_size
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def tidy_workspace(
    pipeline_dir: Path,
    out_dir: Optional[Path] = None,
    keep_temp: bool = False,
    deep_clean: bool = False,
) -> Dict[str, Any]:
    """
    1. 将全部最终交付物同步归档至 <project_dir>/out/
    2. 除非 --keep-temp，清理大体积过程临时帧/冗余中间音轨（默认保留锁轨音频与代码补镜以支撑 tidy 后二次精修；deep_clean 才彻底清库）
    """
    target_out = out_dir or (pipeline_dir.parent / "out")
    target_out.mkdir(parents=True, exist_ok=True)
    synced: List[str] = []
    for fname in DELIVERABLE_SYNC_FILES:
        src = pipeline_dir / fname
        if src.is_file():
            shutil.copy2(src, target_out / fname)
            synced.append(fname)
    write_json(target_out / "archive_receipt.json", {
        "plan_sha256": compute_plan_digest(pipeline_dir),
        "files": {name: sha256_file(target_out / name) for name in synced
                  if name not in ("10_qa_report.json", "10_qa_report.md")},
    })

    if keep_temp:
        rep = {
            "step": "tidy_workspace",
            "skipped_keep_temp": True,
            "removed_count": 0,
            "freed_bytes": 0,
            "freed_mb": 0.0,
            "synced_out_files": synced,
            "removed_paths": [],
        }
        write_json(pipeline_dir / "tidy_report.json", rep)
        return rep

    protected = _collect_protected_paths(pipeline_dir)
    # 若 04_edl.json 仍存在，默认保护其引用的 code_broll 与锁轨文件，支持 post-tidy 的 reedit-visuals / render-locked
    if not deep_clean:
        edl_p = pipeline_dir / "04_edl.json"
        if edl_p.is_file():
            try:
                edl = read_json(edl_p)
                for c in (edl.get("clips") or []):
                    mp = str(c.get("media_path") or "")
                    if mp and "04_generated_broll" in mp and Path(mp).exists():
                        protected.add(Path(mp).resolve())
            except Exception:
                pass

    target_subdirs = TEMP_SUBDIRS + (DEEP_CLEAN_SUBDIRS if deep_clean else ())
    target_files = TEMP_PROCESS_FILES + (DEEP_CLEAN_FILES if deep_clean else ())

    removed_paths: List[str] = []
    freed_bytes = 0

    for dname in target_subdirs:
        dp = pipeline_dir / dname
        if not dp.is_dir():
            continue
        dp_res = dp.resolve()
        has_prot = any(dp_res == pr or dp_res in pr.parents for pr in protected)
        if has_prot:
            continue
        freed_bytes += _dir_size(dp)
        shutil.rmtree(dp, ignore_errors=True)
        removed_paths.append(dname + "/")

    for fname in target_files:
        fp = pipeline_dir / fname
        if not fp.is_file():
            continue
        if fp.resolve() in protected:
            continue
        freed_bytes += fp.stat().st_size
        fp.unlink(missing_ok=True)
        removed_paths.append(fname)

    # 清理 _bb_tmp* 等临时检测残留目录
    for tmp_cand in pipeline_dir.glob("_bb_tmp*"):
        if tmp_cand.is_dir():
            freed_bytes += _dir_size(tmp_cand)
            shutil.rmtree(tmp_cand, ignore_errors=True)
            removed_paths.append(tmp_cand.name + "/")

    freed_mb = round(freed_bytes / (1024.0 * 1024.0), 2)
    rep = {
        "step": "tidy_workspace",
        "skipped_keep_temp": False,
        "removed_count": len(removed_paths),
        "freed_bytes": freed_bytes,
        "freed_mb": freed_mb,
        "synced_out_files": synced,
        "removed_paths": removed_paths,
    }
    write_json(pipeline_dir / "tidy_report.json", rep)
    write_json(target_out / "tidy_report.json", rep)
    print(f"[tidy] 完工清理完成：删除 {len(removed_paths)} 项过程文件/目录，释放 {freed_mb:.2f} MB；交付物已归档至 {target_out}")
    return rep


def main() -> None:
    parser = argparse.ArgumentParser(description="协作闸门锁、复刻洁净室与完工自动清理守卫")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_rs = sub.add_parser("record-stop")
    p_rs.add_argument("--pipeline-dir", required=True)
    p_rs.add_argument("--type", required=True, choices=STOP_TYPES)
    p_rs.add_argument("--summary", default="")
    p_rs.add_argument("--rehearsal", action="store_true")

    p_as = sub.add_parser("audit-stops")
    p_as.add_argument("--pipeline-dir", required=True)
    p_as.add_argument("--require-all", action="store_true")

    p_lk = sub.add_parser("lock")
    p_lk.add_argument("--pipeline-dir", required=True)
    p_lk.add_argument("--note", default="方案拍板锁定")

    p_vf = sub.add_parser("verify")
    p_vf.add_argument("--pipeline-dir", required=True)

    p_ap = sub.add_parser("approve-final")
    p_ap.add_argument("--pipeline-dir", required=True)
    p_ap.add_argument("--out-dir", help="可选：自定义归档 out 目录路径")
    p_ap.add_argument("--note", default="", help="记录实际复核结果。剪映导入请写明版本和检查到的轨道")
    p_ap.add_argument("--reviewed", action="append", default=[], help="已完成的人工检查 ID，可重复传参")

    p_rd = sub.add_parser("replication-diff")
    p_rd.add_argument("--reference", required=True)
    p_rd.add_argument("--output", required=True)
    p_rd.add_argument("--contrast")
    p_rd.add_argument("--out-dir", required=True)

    p_td = sub.add_parser("tidy")
    p_td.add_argument("--pipeline-dir", required=True)
    p_td.add_argument("--out-dir")
    p_td.add_argument("--keep-temp", action="store_true")
    p_td.add_argument("--deep-clean", action="store_true")
    p_arch = sub.add_parser("verify-out")
    p_arch.add_argument("--out-dir", required=True, type=Path)

    args = parser.parse_args()
    if args.cmd == "record-stop":
        st = record_stop(Path(args.pipeline_dir), args.type, args.summary, args.rehearsal)
        print(f"[workflow] 已记录停点 {args.type} ({len(st['interruptions'])}/{BUDGET})")
    elif args.cmd == "audit-stops":
        res = audit_stops(Path(args.pipeline_dir), require_all=args.require_all)
        print(f"[workflow] 打扰审计: {res['count']}/{res['budget']} {res['types']} passed={res['passed']}")
        if not res["passed"]:
            raise SystemExit(1)
    elif args.cmd == "lock":
        st = lock_plan(Path(args.pipeline_dir), note=args.note)
        print(f"[workflow] 方案已上锁 sha={st['plan_sha256'][:12]} ({st['clip_count']}镜, {st['duration_sec']}s)")
    elif args.cmd == "verify":
        code, msg, _ = verify_lock(Path(args.pipeline_dir))
        print(("[PASS] " if code == 0 else "[BLOCK] ") + msg)
        raise SystemExit(code)
    elif args.cmd == "approve-final":
        st = approve_final(
            Path(args.pipeline_dir),
            note=args.note,
            reviewed_checks=tuple(args.reviewed),
            out_dir=Path(args.out_dir) if args.out_dir else None,
        )
        print(f"[workflow] 人工终审签字完成 -> APPROVED (at={st['approved_at']})")
    elif args.cmd == "verify-out":
        result = verify_archive(args.out_dir)
        print(result)
        raise SystemExit(0 if result["passed"] else 2)
    elif args.cmd == "replication-diff":
        rep = run_replication_diff(
            Path(args.reference),
            Path(args.output),
            Path(args.out_dir),
            contrast_mp4=Path(args.contrast) if args.contrast else None,
        )
        print(
            f"[replication] 洁净室={'PASS' if rep['clean_room_passed'] else 'BLOCK'}, "
            f"min_phash={rep['min_phash_hamming']}, ocr12={len(rep['ocr_12char_overlap_hits'])}, "
            f"匹配维度={rep['dimensions_matched']}/{rep['dimensions_total']}"
        )
        if rep["clean_room_block"]:
            raise SystemExit(2)
    elif args.cmd == "tidy":
        tidy_workspace(
            Path(args.pipeline_dir),
            out_dir=Path(args.out_dir) if args.out_dir else None,
            keep_temp=args.keep_temp,
            deep_clean=args.deep_clean,
        )


if __name__ == "__main__":
    main()
