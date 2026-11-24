#!/usr/bin/env python3
"""
[Step 6: 包装对齐与预算控制]
1. 修复限幅符号写反 Bug：采用上限 min(-3.0, ...) 确保真实流派音量梯度生效，06_packaging.json 与 WAV 实感完全一致。
2. Freesound CC0 真实物理材质音效排在数学合成兜底之前，同族轮换防疲劳。
3. 视觉包装严格遵守预算上限 max(3, ceil(duration_sec / 3.5))，并写入 style_id 与基于 01_manifest 裁切人脸的权威避脸 final_bbox。
输出：pipeline/06_packaging.json 与 pipeline/06_sfx_track.wav
"""

from __future__ import annotations

import argparse
import math
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from common import (
    ASSETS_DIR,
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    DEFAULT_FPS,
    SAFE_ZONE,
    SUBTITLE_CENTER_X,
    SUBTITLE_SYMMETRIC_MAX_W,
    bbox_iou,
    check_bbox_in_safe_zone,
    contrast_ratio,
    estimate_read_hold_sec,
    find_commercial_font,
    fit_text_line,
    frames_to_sec,
    hex_to_rgba,
    load_animation_sfx_pairs,
    load_sfx_intent_taxonomy,
    load_style_applied,
    load_style_spectrum_entry,
    maybe_reexec_in_venv,
    parse_manifest_faces,
    read_json,
    write_json,
)

if __name__ == "__main__":
    maybe_reexec_in_venv()

import numpy as np
from step3_lock_audio import SR, load_stereo_wav_np, save_stereo_wav_np


_SFX_ROLES = ("cut", "hook", "info", "accent", "receipt", "misc")
_SFX_EXTS = (".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg")


def style_sfx_dir(style_id: str) -> Path:
    return ASSETS_DIR / "sound_vibe" / "sfx" / str(style_id or "").strip()


def scan_style_sfx(style_id: str) -> Dict[str, List[Path]]:
    """本风格自己的真音效池：sound_vibe/sfx/<style_id>/<角色>_<原音效名>.<ext>。

    实测事故根因：旧版把兜底写死成 style_01_*/style_15_* 的真音效和其他风格的合成音，
    于是 style_21 的成片里每 1.5 秒塞一个外来「叮」。这里**只读本风格目录**，
    本风格一条都没有就返回空字典（由调用方直接关掉音效），绝不做跨风格兜底。
    """
    pool: Dict[str, List[Path]] = {r: [] for r in _SFX_ROLES}
    d = style_sfx_dir(style_id)
    if d.is_dir():
        for p in sorted(d.iterdir()):
            if not p.is_file() or p.suffix.lower() not in _SFX_EXTS:
                continue
            role = p.stem.split("_", 1)[0]
            if role not in pool:
                role = "misc"
            pool[role].append(p)
    # 用户自备原版音效按角色名覆盖：sound_vibe/user/<style_id>/cut.mp3
    user_dir = ASSETS_DIR / "sound_vibe" / "user" / str(style_id or "")
    if user_dir.is_dir():
        for r in _SFX_ROLES:
            hit = next((p for p in sorted(user_dir.iterdir())
                        if p.is_file() and p.suffix.lower() in _SFX_EXTS and p.stem.lower() == r), None)
            if hit is not None:
                pool[r] = [hit]
    return {r: v for r, v in pool.items() if v}


def _role_candidates(pool: Dict[str, List[Path]], role: str) -> List[Path]:
    """取本风格该角色的全部候选；本风格缺这个角色就用本风格其它角色（不跨风格）。"""
    if not pool:
        return []
    if pool.get(role):
        return list(pool[role])
    for r in _SFX_ROLES:
        if pool.get(r):
            return list(pool[r])
    return []


def _default_cc0_sfx(role: str, style_id: str) -> Optional[Path]:
    """本风格内的单条兜底音效；本风格整包为空则返回 None（不再借别的风格）。"""
    cands = _role_candidates(scan_style_sfx(style_id), role)
    return cands[0] if cands else None


def load_style_sfx_truth(style_id: str) -> Optional[Dict[str, Any]]:
    """源草稿音效实测表：assets/sound_vibe/sfx_truth/<style_id>.json。"""
    p = ASSETS_DIR / "sound_vibe" / "sfx_truth" / f"{style_id}.json"
    if not p.is_file():
        return None
    try:
        return read_json(p)
    except Exception:
        return None


def load_style_bgm_info(style_id: str) -> Dict[str, Any]:
    """本风格 BGM 归属（只读本风格自己的 manifest 节点），绝不指向别的风格。"""
    info: Dict[str, Any] = {
        "file": None,
        "status": None,
        "name": None,
        "dual_bgm": None,
        "switch_sec": None,
        "disabled_reason": None,
    }
    node: Dict[str, Any] = {}
    try:
        mf = read_json(ASSETS_DIR / "sound_vibe" / "vibe_sound_manifest.json")
        node = (mf.get("styles") or {}).get(str(style_id)) or {}
    except Exception:
        node = {}
    rel = node.get("bgm_wav")
    if rel and (ASSETS_DIR / str(rel)).is_file() and not node.get("bgm_is_silence_placeholder"):
        info["file"] = str(rel)
    info["status"] = node.get("bgm_status")
    info["name"] = node.get("bgm_name")
    info["dual_bgm"] = node.get("bgm_dual")
    info["switch_sec"] = node.get("bgm_switch_sec")
    if info["file"] is None:
        info["disabled_reason"] = (
            node.get("bgm_disabled_reason") or f"{style_id} 源草稿无 BGM，本片不放 BGM"
        )
    return info


_FLOWER_FILLER_PREFIXES = (
    "介绍一下", "给大家介绍", "我介绍一下", "我觉得", "其实", "一个", "因为", "所以",
    "可以", "就是", "然后", "那么", "这个", "那个", "大家看", "你看", "你们看",
    "今天", "现在", "咱们", "我们", "这里", "这边", "还有", "而且", "但是", "不过",
)
_FLOWER_BAN_EXACT = {"介绍", "一下", "一个", "这个", "那个", "觉得", "可以", "就是", "然后", "因为", "所以", "非常", "真的", "很"}


def _extract_clean_flower_phrase(
    sent: Dict[str, Any],
    raw_pain_kws: Optional[List[str]] = None,
    max_len: int = 8,
) -> str:
    """
    高质量花字短语提炼（根治实测 #4“介绍一下/我觉得”半词碎片）：
    1. 优先命中一手原始痛点词；
    2. 其次提取完整「数字+单位+核心名词」短语；
    3. 其次从句子 keywords 中跳过虚词/半词；
    4. 最后剥离口语虚词前缀，取完整语义短语，绝不硬截残词。
    """
    txt = str(sent.get("text") or "").strip()
    clean_txt = re.sub(r"[，。！？、；：“”\"'（）()【】\[\]\s]+", "", txt)
    for pk in (raw_pain_kws or []):
        pks = str(pk).strip()
        if 2 <= len(pks) <= max_len and pks in txt and pks not in _FLOWER_BAN_EXACT:
            return pks

    # 提取数字+量词/核心实体短语（如 25张卡座、8个包房、99元双人餐）
    num_m = re.search(r"([零一二两三四五六七八九十百千万\d]+(?:\.\d+)?[个张间家次倍元块万亿%％折平米天小时分钟秒][\u4e00-\u9fff]{0,4})", clean_txt)
    if num_m and 2 <= len(num_m.group(1)) <= max_len:
        return num_m.group(1)

    for kw in _display_keywords(sent):
        ks = str(kw).strip()
        if len(ks) < 2 or ks in _FLOWER_BAN_EXACT:
            continue
        if any(ks.startswith(p) for p in _FLOWER_FILLER_PREFIXES):
            continue
        if len(ks) <= max_len:
            return ks

    # 剥离常见口语虚词开头后按自然子句提取
    clauses = [c.strip() for c in re.split(r"[，。！？、；：,\.!\?;:]+", txt) if c.strip()]
    best_clause = ""
    for cl in clauses:
        c = cl
        changed = True
        while changed:
            changed = False
            for pref in _FLOWER_FILLER_PREFIXES:
                if c.startswith(pref) and len(c) > len(pref) + 1:
                    c = c[len(pref):].lstrip("的了呢啊呀吧")
                    changed = True
        c = c.strip("的了呢啊呀吧")
        if 2 <= len(c) <= max_len:
            return c
        if len(c) > len(best_clause):
            best_clause = c
    if best_clause:
        return _cut_at_word_boundary(best_clause, max_len)
    return _cut_at_word_boundary(clean_txt, max_len) or "核心亮点"


def _cut_at_word_boundary(text: str, max_len: int) -> str:
    """按词边界截断，绝不把词劈成两半。

    2026-11-24 实测：`我哋嘅四大天王虾饺…` 硬截 8 字变成「我哋嘅四大天王虾」，
    上屏就是个残词。用 jieba 切词后往后累加，最后一个能装下的完整词收口。
    """
    t = str(text or "")
    if len(t) <= max_len:
        return t
    try:
        import jieba  # type: ignore
        toks = [w for w in jieba.lcut(t) if w]
    except Exception:
        toks = [t[i:i + 2] for i in range(0, len(t), 2)]
    out = ""
    for w in toks:
        if len(out) + len(w) > max_len:
            break
        out += w
    if len(out) >= 2:
        return out
    return t[:max_len]


def _display_keywords(sent: Dict[str, Any]) -> List[str]:
    """上屏用的关键词：优先取 step3 过滤好的 `display_keywords`（只在正文里出现过的词）。

    没有这个字段的旧管道产物，退回 `keywords` 里能在正文找到的那几个；
    一个都没有就空着，让花字走「数字+量词」「整句短语」那两条路，绝不把口令打上屏。
    """
    got = sent.get("display_keywords")
    if isinstance(got, list):
        return [str(k) for k in got if str(k).strip()]
    txt = str(sent.get("text") or "")
    return [str(k) for k in (sent.get("keywords") or []) if str(k).strip() and str(k) in txt]


def rotate_sfx(candidates: List[Path], last_used_name: Optional[str]) -> Optional[Path]:
    """同族/候选音效自动轮换，避免连续两次重复同一音色引起听觉疲劳。

    候选为空就返回 None（调用方直接不落这条 cue），不再偷偷兜底到别的风格。
    """
    valid = [p for p in candidates if p and p.is_file()]
    if not valid:
        return None
    if len(valid) == 1 or not last_used_name:
        return valid[0]
    for p in valid:
        if p.name != last_used_name:
            return p
    return valid[0]


def _enhance_sfx_presence(arr: np.ndarray, sr: int = SR) -> np.ndarray:
    """保持 CC0 原生频谱波形不变，对 2–8kHz 过闷暖调 SFX 施加温和广播级自身带通临场补偿。"""
    if len(arr) < 128:
        return arr
    try:
        from scipy import signal as sp_sig
        mono = arr.mean(axis=1).astype(np.float64)
        full_rms = float(np.sqrt(np.mean(mono ** 2) + 1e-12))
        sos = sp_sig.butter(4, [2000.0, 8000.0], btype="bandpass", fs=float(sr), output="sos")
        hf_mono = sp_sig.sosfiltfilt(sos, mono)
        hf_rms = float(np.sqrt(np.mean(hf_mono ** 2) + 1e-12))
        hf_ratio = hf_rms / max(1e-5, full_rms)
        if hf_ratio >= 0.28 or hf_rms < 1e-5:
            return arr
        # 仅按比例混合原 SFX 自身的 2–8kHz 分量，使其清晰度达到 hf_ratio≈0.35（绝不混入人工噪波）
        target_hf = max(hf_rms, full_rms * 0.35)
        alpha = min(6.0, max(0.0, (target_hf - hf_rms) / hf_rms))
        out = arr.astype(np.float64).copy()
        out[:, 0] += alpha * sp_sig.sosfiltfilt(sos, out[:, 0])
        out[:, 1] += alpha * sp_sig.sosfiltfilt(sos, out[:, 1])
        pk = float(np.max(np.abs(out)))
        if pk > 0.85:
            out *= (0.85 / pk)
        return out.astype(np.float32)
    except Exception:
        return arr


def _fade_sfx_edges(arr: np.ndarray, sr: int, fade_in_sec: float = 0.002,
                    fade_out_sec: float = 0.010) -> np.ndarray:
    """给音效两端上淡入淡出，消掉文件硬截留下的电平台阶（不改音色、不改时长）。"""
    if arr.ndim != 2 or len(arr) < 16:
        return arr
    n_in = min(int(sr * fade_in_sec), len(arr) // 4)
    n_out = min(int(sr * fade_out_sec), len(arr) // 4)
    out = arr.copy()
    if n_in > 1:
        out[:n_in] *= np.linspace(0.0, 1.0, n_in, dtype=np.float32)[:, None]
    if n_out > 1:
        out[-n_out:] *= np.linspace(1.0, 0.0, n_out, dtype=np.float32)[:, None]
    return out


def _measure_sfx_peak_offset_sec(arr: np.ndarray, sr: int = SR) -> float:
    if len(arr) < 128:
        return 0.0
    mono = np.abs(arr.mean(axis=1))
    win = max(1, int(0.020 * sr))
    env = np.convolve(mono, np.ones(win, dtype=np.float32) / win, mode="same")
    peak_idx = int(np.argmax(env))
    return round(peak_idx / float(sr), 4)


def _avoid_faces_for_pkg(bbox: Dict[str, int], faces_px: List[Dict[str, Any]]) -> Dict[str, int]:
    """在 Step 6 源头一次性将花字胶囊避开裁切后人脸五官，保持安全区且不侵入字幕带。"""
    cand = dict(bbox)
    if not faces_px:
        return cand
    cur_iou = max(bbox_iou(cand, fb) for fb in faces_px)
    if cur_iou <= 0.01:
        return cand
    best = cand
    best_iou = cur_iou
    for try_y in (176, 224, 280, 340, 460, 780, 880, 960):
        if try_y + cand["h"] > 1120:
            continue
        alt = {"x": cand["x"], "y": int(try_y), "w": cand["w"], "h": cand["h"]}
        ok, _ = check_bbox_in_safe_zone(alt)
        if not ok:
            continue
        iou_v = max(bbox_iou(alt, fb) for fb in faces_px)
        if iou_v <= 0.01:
            return alt
        if iou_v < best_iou:
            best_iou = iou_v
            best = alt
    return best


def _measure_pkg_badge_width(text: str, font_path: Optional[str], max_pkg_w: int, target_fs: int = 40, min_w: int = 240) -> int:
    try:
        from PIL import Image, ImageDraw
        draw = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
        fnt, _ = fit_text_line(draw, text, font_path, max_width=max_pkg_w - 64, max_size=target_fs, min_size=24)
        if fnt is not None:
            bb = draw.textbbox((0, 0), text, font=fnt)
            return int(min(max_pkg_w, max(min_w, (bb[2] - bb[0]) + 72)))
    except Exception:
        pass
    return int(min(max_pkg_w, max(min_w, len(text) * target_fs + 68)))


def _bp_overlay_style(style_id: str) -> Dict[str, Any]:
    try:
        from render_template_overlays import get_style_blueprint

        return (get_style_blueprint(style_id) or {}).get("overlay_style") or {}
    except Exception:
        return {}


def flower_budget_from_source(style_id: str, total_dur: float, legacy: int) -> int:
    """花字条数预算：优先按源草稿实测的「每分钟花字条数」，没有实测值才用旧的经验公式。"""
    ppm = _bp_overlay_style(style_id).get("flower_per_min")
    try:
        ppm_v = float(ppm) if ppm is not None else 0.0
    except (TypeError, ValueError):
        ppm_v = 0.0
    if ppm_v <= 0:
        return int(legacy)
    return max(int(legacy), min(120, int(math.ceil(total_dur * ppm_v / 60.0))))


def giant_count_from_source(style_id: str, total_dur: float, pkg_tech: Dict[str, Any]) -> int:
    """巨字条数：优先蓝图里源草稿实测的 giant_count，再按片长做一次 sanity 上限。"""
    raw = (pkg_tech or {}).get("giant_impact_count")
    if raw is None:
        raw = _bp_overlay_style(style_id).get("giant_count")
    try:
        n = int(raw) if raw is not None else 2
    except (TypeError, ValueError):
        n = 2
    return max(0, min(n, int(math.ceil(max(1.0, float(total_dur)) / 4.0))))


def run_step6(
    pipeline_dir: Path,
    fps: int = DEFAULT_FPS,
    style_id: str | None = None,
    no_flower_text: bool = False,
    keep_manual_texts: bool = False,
) -> Dict[str, Any]:
    audio_events = read_json(pipeline_dir / "03_audio_events.json")
    edl = read_json(pipeline_dir / "04_edl.json")
    man_p = pipeline_dir / "01_manifest.json"
    manifest = read_json(man_p) if man_p.exists() else {"items": []}
    prev_pkg_p = pipeline_dir / "06_packaging.json"
    prev_visuals = (read_json(prev_pkg_p).get("visual_elements") or []) if (keep_manual_texts and prev_pkg_p.is_file()) else []

    faces_by_mp4: Dict[str, List[Dict[str, int]]] = {}
    for it in manifest.get("items", []):
        mp = str(it.get("normalized_mp4", ""))
        fl = parse_manifest_faces(it)
        if mp and fl:
            faces_by_mp4[mp] = fl

    if not style_id:
        style_id = edl.get("style_id") or load_style_applied(pipeline_dir)
    style_id = str(style_id or "style_01")

    vibe_prof = None
    try:
        from vibe_sound import get_style_sound_profile
        vibe_prof = get_style_sound_profile(style_id)
    except Exception:
        vibe_prof = None

    style_entry_pre = load_style_spectrum_entry(style_id) or {}
    broll_target = float(style_entry_pre.get("broll_overlay_ratio_target", 0.5))
    sfx_per_min_tgt = float((style_entry_pre.get("audio_and_sfx") or {}).get("sfx_per_min_target") or 24.0)
    # 密度以源草稿实测为准（sfx_truth/<style_id>.json 的 sfx_per_min，style_21≈46/min）
    sfx_truth = load_style_sfx_truth(style_id)
    if sfx_truth is not None:
        try:
            truth_ppm = float(sfx_truth.get("sfx_per_min") or 0.0)
        except (TypeError, ValueError):
            truth_ppm = 0.0
        # 真值为 0 就按 0 报（下面 sfx 池为空会真的一条都不出）
        sfx_per_min_tgt = max(0.0, truth_ppm)
    total_dur = float(audio_events["locked_duration_sec"])
    total_samples = int(round(total_dur * SR))
    _legacy_budget = max(2, math.ceil(total_dur / 5.5)) if broll_target >= 0.85 else max(3, math.ceil(total_dur / 3.5))
    max_visual_budget = flower_budget_from_source(style_id, total_dur, _legacy_budget)
    # 上限从 48 提到 72：源里 style_15 实测 64.8 次/分，旧的 48 上限会把它削平
    dyn_sfx_budget = max(5, int(math.ceil(total_dur * max(15.0, min(72.0, sfx_per_min_tgt)) / 60.0)))
    max_sfx_budget = max(8, dyn_sfx_budget) if style_id == "style_06" else dyn_sfx_budget

    sentences_map = {s["id"]: s for s in audio_events["sentences"]}
    events = audio_events["events"]
    code_ranges = [
        (int(c["timeline_in_frame"]), int(c["timeline_out_frame"]))
        for c in edl.get("clips", [])
        if c.get("track_role") == "code_broll"
    ]

    def overlaps_code(sf_f: int, ef_f: int) -> bool:
        return any(sf_f < ce and ef_f > cs for cs, ce in code_ranges)

    sfx_cues: List[Dict[str, Any]] = []
    dyn_gap_sec = max(1.5, min(4.8, (60.0 / max(12.0, sfx_per_min_tgt)) * 0.68))
    # 音效池：只认本风格目录下的真音效，整包为空就直接不出 sfx
    sfx_pool = scan_style_sfx(style_id)
    sfx_disabled_reason: Optional[str] = None
    if not sfx_pool:
        truth_count = int((sfx_truth or {}).get("sfx_count") or 0)
        sfx_disabled_reason = (
            f"{style_id} 源草稿 sfx_count=0，本风格无音效可放"
            if (sfx_truth is not None and truth_count == 0)
            else f"{style_id} 音效目录为空（assets/sound_vibe/sfx/{style_id}/ 无文件）"
        )
    native_prof = vibe_prof if (vibe_prof or {}).get("mapped_from_style_id") == style_id else None
    bgm_info = load_style_bgm_info(style_id)
    if native_prof:
        # 尊重流派自己的 sfx_gain_db（仅允许上限到 -3.5dB，避免风格全被拉爆成统一响度）
        base_gain_db = max(-14.0, min(-3.5, float(native_prof.get("sfx_gain_db", -9.5)) + 3.0))
        prof_gap = float(native_prof.get("sfx_min_gap_sec", dyn_gap_sec))
        min_sfx_gap = int(min(prof_gap, dyn_gap_sec) * fps)
    else:
        base_gain_db = -8.5
        min_sfx_gap = int(dyn_gap_sec * fps)

    sfx_files: Dict[str, List[Path]] = {
        "hook": _role_candidates(sfx_pool, "hook") or _role_candidates(sfx_pool, "accent"),
        "info": _role_candidates(sfx_pool, "info"),
        "accent": _role_candidates(sfx_pool, "accent"),
        "twist": _role_candidates(sfx_pool, "accent"),
        "detail": _role_candidates(sfx_pool, "info"),
        "persona": _role_candidates(sfx_pool, "cut"),
        "cta": _role_candidates(sfx_pool, "receipt"),
        "cut": _role_candidates(sfx_pool, "cut"),
    }

    # 接入动画×音效字典与叙事意图分类表（仅接受本风格目录内的音频，杜绝跨风格串味）
    anim_sfx_dict = load_animation_sfx_pairs()
    sfx_taxonomy = load_sfx_intent_taxonomy()
    emotion_routing = sfx_taxonomy.get("pipeline_emotion_routing") or {}
    style_sfx_root = style_sfx_dir(style_id).resolve()
    for emo_key, rel_list in emotion_routing.items():
        if emo_key not in sfx_files or not rel_list:
            continue
        for rel_p in rel_list:
            cand_p = ASSETS_DIR / rel_p
            if not cand_p.is_file():
                continue
            try:
                cand_p.resolve().relative_to(style_sfx_root)
            except Exception:
                continue  # 不是本风格目录内的音频，直接丢弃
            sfx_files[emo_key] = [cand_p]
            break

    used_frames: List[int] = []
    last_sfx_name: Optional[str] = None

    def can_place_sfx(frame: int) -> bool:
        return len(sfx_cues) < max_sfx_budget and all(abs(frame - f) >= min_sfx_gap for f in used_frames)

    # 音效 cue 一律取自本风格真音效池（assets/sound_vibe/sfx/<style_id>/），
    # 池子为空时一条都不出，绝不借别的风格的音效来凑数。
    if sfx_pool:
        for clip in edl["clips"][1:]:
            role = clip.get("track_role", "b_roll")
            ev_frame = int(clip.get("timeline_in_frame", clip["cut_event_frame"])) if role == "code_broll" else int(clip["cut_event_frame"])
            is_end_receipt = (clip is edl["clips"][-1]) and bool(sfx_files["cta"])
            if role != "code_broll" and not is_end_receipt:
                continue
            if not can_place_sfx(ev_frame):
                continue
            cand_list = sfx_files["cta"] if is_end_receipt else [
                *sfx_files["info"],
                *sfx_files["cut"],
                *sfx_files["hook"],
            ]
            sf = rotate_sfx(cand_list, last_sfx_name)
            if sf is None:
                continue
            last_sfx_name = sf.name
            cue_gain = round(max(-14.0, min(-3.5, base_gain_db + 1.5)), 1)
            sfx_cues.append({
                "id": f"sfx_{len(sfx_cues)+1:03d}",
                "file": str(sf.resolve()),
                "sfx_name": sf.name,
                "time_sec": frames_to_sec(ev_frame, fps),
                "frame": ev_frame,
                "gain_db": cue_gain,
                "event_id": clip["cut_event_id"],
                "reason": f"{style_id} 账单小票收尾提示" if is_end_receipt else f"{style_id} 信息卡片物理提示",
            })
            used_frames.append(ev_frame)

        # 本风格真音效轮换池（按角色顺序铺开，同角色内多音色轮转；含 accent 才接近源草稿的听感）
        cut_palette: List[Path] = []
        for _role_key in ("cut", "info", "hook", "accent"):
            for p in sfx_files.get(_role_key) or []:
                if p not in cut_palette:
                    cut_palette.append(p)
        if not cut_palette:
            cut_palette = [p for _r in _SFX_ROLES for p in sfx_pool.get(_r, [])]
        cut_rot = 0
        # 优先选在句首/停顿缝隙切点安放 SFX，确保听感清爽且避免被重读浊音完全掩蔽，第二遍再补足其余切点
        prioritized_clips = [
            c for c in edl["clips"][1:]
            if str(c.get("cut_event_type", "")) in ("sentence_start", "pause_start", "pause_end", "bgm_downbeat")
        ] + [
            c for c in edl["clips"][1:]
            if str(c.get("cut_event_type", "")) not in ("sentence_start", "pause_start", "pause_end", "bgm_downbeat")
        ]
        for clip in prioritized_clips:
            ev_frame = int(clip["cut_event_frame"])
            if not can_place_sfx(ev_frame):
                continue
            rotated_pool = cut_palette[cut_rot % len(cut_palette):] + cut_palette[:cut_rot % len(cut_palette)]
            sf = rotate_sfx(rotated_pool, last_sfx_name)
            if sf is None:
                continue
            last_sfx_name = sf.name
            cut_rot += 1
            cue_gain = round(max(-14.0, min(-4.0, base_gain_db + 0.5)), 1)
            sfx_cues.append({
                "id": f"sfx_{len(sfx_cues)+1:03d}",
                "file": str(sf.resolve()),
                "sfx_name": sf.name,
                "time_sec": frames_to_sec(ev_frame, fps),
                "frame": ev_frame,
                "gain_db": cue_gain,
                "event_id": clip["cut_event_id"],
                "reason": f"{style_id} 流派真音效转场",
            })
            used_frames.append(ev_frame)

        for ev in events:
            if ev["type"] == "sentence_start" and can_place_sfx(ev["frame"]):
                sent = sentences_map.get(ev.get("sentence_id", ""), {})
                emo = sent.get("emotion_or_info", "info")
                if emo not in ("hook", "twist", "persona", "cta"):
                    continue
                sf = rotate_sfx([*sfx_files.get(emo, sfx_files["info"]), *sfx_files["cut"]], last_sfx_name)
                if sf is None:
                    continue
                last_sfx_name = sf.name
                cue_gain = round(max(-15.0, min(-4.5, base_gain_db - 1.2)), 1)
                sfx_cues.append({
                    "id": f"sfx_{len(sfx_cues)+1:03d}",
                    "file": str(sf.resolve()),
                    "sfx_name": sf.name,
                    "time_sec": ev["time_sec"],
                    "frame": ev["frame"],
                    "gain_db": cue_gain,
                    "event_id": ev["id"],
                    "reason": f"{emo} 句首节点轻量提示",
                })
                used_frames.append(ev["frame"])

    sfx_cues.sort(key=lambda x: x["frame"])
    for idx_cue, cue in enumerate(sfx_cues, start=1):
        cue["id"] = f"sfx_{idx_cue:03d}"

    sfx_track = np.zeros((total_samples, 2), dtype=np.float32)
    sfx_cache: Dict[str, np.ndarray] = {}
    for cue in sfx_cues:
        fp = cue["file"]
        if fp not in sfx_cache:
            arr, _ = load_stereo_wav_np(Path(fp))
            m_abs = np.abs(arr.mean(axis=1))
            active_m = m_abs[m_abs >= max(1e-4, float(np.max(m_abs)) * 0.15)]
            core_rms = float(np.sqrt(np.mean(active_m ** 2) + 1e-10)) if len(active_m) > 0 else 0.01
            # 保持原生 CC0 物理频谱不注水：仅做温和活性核心增益归一化与防削波峰值钳位
            norm_gain = min(10.0, 0.25 / max(1e-4, core_rms))
            peak_cap = 0.85 / max(1e-4, float(np.max(np.abs(arr))))
            arr = (arr * min(norm_gain, peak_cap)).astype(np.float32)
            arr = _enhance_sfx_presence(arr, SR)
            # 剪映音效库里不少 mp3 是「硬截」出来的：最后一个采样点还停在半波上，
            # 直接铺进音轨就是一声哒（2026-11-24 实测：42 条音效里 3 条尾音跳变被判爆音）。
            # 2ms 淡入 + 10ms 淡出，声音本身不变，只把两端的台阶磨掉。
            arr = _fade_sfx_edges(arr, SR)
            sfx_cache[fp] = arr
        arr = sfx_cache[fp]
        peak_off = _measure_sfx_peak_offset_sec(arr, SR)
        # 峰值对齐切点：提前 peak_off 起播，使听觉峰值精准落在切点 time_sec
        onset_sec = max(0.0, float(cue["time_sec"]) - peak_off)
        cue["onset_time_sec"] = round(onset_sec, 4)
        cue["peak_time_sec"] = round(onset_sec + peak_off, 4)
        gain = 10.0 ** (min(-3.0, float(cue["gain_db"])) / 20.0)
        start_idx = max(0, int(round(onset_sec * SR)))
        end_idx = min(total_samples, start_idx + len(arr))
        if end_idx > start_idx:
            piece = arr[: end_idx - start_idx]
            if end_idx - start_idx < len(arr):
                # 片子结束把音效截断，同样要磨台阶
                piece = _fade_sfx_edges(piece, SR, fade_in_sec=0.0)
            sfx_track[start_idx:end_idx] += piece * gain

    sfx_wav_path = pipeline_dir / "06_sfx_track.wav"
    save_stereo_wav_np(sfx_wav_path, sfx_track, SR)

    pkg_font_path = find_commercial_font()
    style_entry = load_style_spectrum_entry(style_id) or {}
    flower_spec = style_entry.get("multi_zone_flower_spec") or (style_entry.get("transition_and_packaging") or {}).get("multi_zone_flower_spec") or {}
    pkg_tech = style_entry.get("packaging_techniques") or {}
    is_borderless_flower = bool(flower_spec.get("borderless", False))
    use_top_bar = bool(pkg_tech.get("persistent_top_bar") or flower_spec.get("persistent_top_bar"))
    use_quote_card = bool(pkg_tech.get("quote_citation_card") or flower_spec.get("quote_citation_card"))

    anim_lookup = anim_sfx_dict.get("animation_lookup") or {}
    anim_cn_alias = {
        "hard_cut": "干脆切换",
        "smooth_pop_or_fade": "轻快渐显",
        "渐显": "渐显",
        "渐隐": "渐隐",
        "水墨晕开": "晕开",
        "弹幕滚动": "向右集合",
        "日出": "随机上升",
        "向下滑动": "向下露出",
        "向右露出": "向右缓入",
    }
    raw_anim_str = str((style_entry.get("subtitle_spec") or {}).get("measured_animation_raw") or "in:渐显")
    raw_anim_tokens = [
        p.replace("in:", "").replace("loop:", "").replace("out:", "").strip()
        for p in raw_anim_str.split("/")
        if p.strip()
    ] or ["渐显"]
    default_jy_anim = ""
    matched_pair = None
    for tok in raw_anim_tokens:
        mapped = anim_cn_alias.get(tok, tok)
        if tok in anim_lookup:
            default_jy_anim = tok
            matched_pair = anim_lookup[tok]
            break
        if mapped in anim_lookup:
            default_jy_anim = mapped
            matched_pair = anim_lookup[mapped]
            break
    if not default_jy_anim:
        first_tok = raw_anim_tokens[0]
        default_jy_anim = anim_cn_alias.get(first_tok, "渐显" if first_tok.isascii() else first_tok)
    if matched_pair is None:
        matched_pair = next(iter(anim_lookup.values()), {
            "jianying_anim_name": "渐显",
            "anim_duration_ms": 400,
            "jianying_sfx_name": "“呼”的转场音效",
        })

    def _safe_readable_fg(cand_hex: Optional[str], fallback_hex: str = "#FFE28A") -> str:
        c_str = str(cand_hex or fallback_hex)
        rgb = hex_to_rgba(c_str, 255)[:3]
        # 防止暗红/深色直接用于深底或花字时低于 4.5:1 无障碍对比度
        if contrast_ratio(rgb, (16, 20, 28)) < 4.5:
            return fallback_hex
        return c_str

    visual_elements: List[Dict[str, Any]] = []
    giant_spec = style_entry.get("giant_text_spec") or {}
    giant_mode = str(giant_spec.get("mode") or "giant_smash_single")
    giant_fs = int(giant_spec.get("font_size_px") or 140)
    giant_tag = str(giant_spec.get("tag_label") or "核心重点")
    giant_fg = _safe_readable_fg(giant_spec.get("fg_hex") or flower_spec.get("center_burst_color"), "#FFE28A")
    giant_accent = _safe_readable_fg(giant_spec.get("accent_hex") or flower_spec.get("flank_color"), "#FFF589")
    giant_stroke = str(giant_spec.get("stroke_hex") or flower_spec.get("stroke_color") or "#000000")
    giant_count_target = giant_count_from_source(style_id, total_dur, pkg_tech)
    use_multi_tag = bool(pkg_tech.get("multi_tag_burst") or flower_spec.get("multi_tag_burst"))

    raw_intel_p = pipeline_dir / "00_raw_intel.json"
    raw_pain_kws = [str(k) for k in ((read_json(raw_intel_p).get("pain_keywords") or []) if raw_intel_p.is_file() else []) if str(k).strip()]

    first_sent = audio_events["sentences"][0] if audio_events["sentences"] else {"text": "核心亮点", "keywords": []}
    hook_badge_text = _extract_clean_flower_phrase(first_sent, raw_pain_kws=raw_pain_kws, max_len=8)
    top_ef = min(int(min(4.2, total_dur * 0.45) * fps), int(total_dur * fps))
    hook_ef = min(int(2.2 * fps), int(total_dur * fps))

    suppress_top_dup = False
    try:
        from render_template_overlays import get_style_blueprint
        bp_ov = (get_style_blueprint(style_id).get("overlay_style") or {})
        suppress_top_dup = bool(bp_ov.get("suppress_top_duplicate_flower", False))
    except Exception:
        suppress_top_dup = False

    if no_flower_text or (suppress_top_dup and not (keep_manual_texts and prev_visuals)):
        visual_elements = []
    elif keep_manual_texts and prev_visuals:
        # --keep-edl 换风格时：完整保留已有花字文案与起止帧，只按新风格更新配色与视觉参数
        for idx_p, pe in enumerate(prev_visuals, start=1):
            ne = dict(pe)
            ne["id"] = f"pkg_{idx_p:03d}"
            ne["fg_hex"] = giant_fg if ne.get("kind") == "center_burst_title" else _safe_readable_fg(flower_spec.get("stamp_color"), "#FFF589")
            ne["accent_hex"] = giant_accent
            ne["stroke_hex"] = giant_stroke
            ne["jianying_anim_name"] = default_jy_anim
            fb = dict(ne.get("final_bbox") or ne.get("bbox") or {"x": 220, "y": 240, "w": 480, "h": 110})
            if fb["y"] + fb["h"] > 1120:
                fb["y"] = max(162, 1120 - fb["h"])
            ne["bbox"] = fb
            ne["final_bbox"] = fb
            visual_elements.append(ne)
    else:
        # 1. 若风格启用「顶部常驻标题栏」(如 style_05/06/08/09/12/14/22)，严格落在安全区 y=162
        if use_top_bar and total_dur >= 3.2 and not overlaps_code(0, top_ef):
            top_label = f"{hook_badge_text[:10]}"
            top_w = _measure_pkg_badge_width(top_label, pkg_font_path, SUBTITLE_SYMMETRIC_MAX_W, target_fs=34, min_w=280)
            top_bb = {"x": SUBTITLE_CENTER_X - top_w // 2, "y": 162, "w": top_w, "h": 58}
            ok_top, _ = check_bbox_in_safe_zone(top_bb)
            visual_elements.append({
                "id": f"pkg_{len(visual_elements)+1:03d}",
                "kind": "persistent_top_bar",
                "overlay_mode": "top_bar",
                "fg_hex": _safe_readable_fg(flower_spec.get("center_burst_color"), "#FFF589"),
                "stroke_hex": "#000000",
                "text_or_asset": top_label,
                "start_frame": 0,
                "end_frame": top_ef,
                "read_hold_sec": round(top_ef / float(fps), 2),
                "aligned_sfx_or_accent_frame": 0,
                "alignment_error_frames": 0,
                "bbox": top_bb,
                "final_bbox": top_bb,
                "safe_zone_ok": ok_top,
                "jianying_anim_name": default_jy_anim,
                "anim_duration_ms": int(matched_pair.get("anim_duration_ms", 400)),
                "paired_jianying_sfx": str(matched_pair.get("jianying_sfx_name", "提示音")),
                "reason": f"{style_id} 顶部常驻主题标语栏",
            })

        # 2. 开场重点大字冲击
        can_place_opening_giant = (
            len(visual_elements) < max_visual_budget
            and (giant_count_target > 0 or is_borderless_flower or not use_top_bar)
            and not overlaps_code(0, hook_ef)
            and (hook_ef / float(fps) >= 1.35)
        )
        if can_place_opening_giant:
            hook_label = hook_badge_text[:8]
            hook_w = min(SUBTITLE_SYMMETRIC_MAX_W, max(360, _measure_pkg_badge_width(hook_label, pkg_font_path, SUBTITLE_SYMMETRIC_MAX_W, target_fs=96, min_w=360)))
            hook_x = SUBTITLE_CENTER_X - hook_w // 2
            hook_h = 152 if giant_mode in ("giant_dual_clash", "giant_numeric_trio") else 142
            init_giant_y = 780 if style_id == "style_01" else (224 if use_top_bar else 178)
            active_faces_0 = []
            for c in edl.get("clips", []):
                if int(c["timeline_in_frame"]) < hook_ef and int(c["timeline_out_frame"]) > 0:
                    active_faces_0.extend(faces_by_mp4.get(str(c.get("media_path", "")), []))
            hook_bbox = _avoid_faces_for_pkg({"x": hook_x, "y": init_giant_y, "w": hook_w, "h": hook_h}, active_faces_0)
            if use_top_bar and hook_bbox["y"] < 218:
                hook_bbox["y"] = 222
            if hook_bbox["y"] + hook_bbox["h"] > 1120:
                hook_bbox["y"] = max(162, 1120 - hook_bbox["h"])
            safe_ok, _ = check_bbox_in_safe_zone(hook_bbox)
            _fkw = _display_keywords(first_sent)
            sec_kw = _fkw[1] if len(_fkw) >= 2 else ""
            if not sec_kw and style_id == "style_01":
                sec_kw = giant_tag if giant_tag and giant_tag != "核心重点" else "门店实拍"
            visual_elements.append({
                "id": f"pkg_{len(visual_elements)+1:03d}",
                "kind": "center_burst_title",
                "giant_mode": giant_mode,
                "font_size_px": giant_fs,
                "tag_label": giant_tag,
                "accent_hex": giant_accent,
                "subtext": str(sec_kw or ""),
                "overlay_mode": "borderless",
                "fg_hex": giant_fg,
                "stroke_hex": giant_stroke,
                "text_or_asset": hook_label,
                "start_frame": 0,
                "end_frame": hook_ef,
                "read_hold_sec": round(hook_ef / float(fps), 2),
                "aligned_sfx_or_accent_frame": 0,
                "alignment_error_frames": 0,
                "bbox": hook_bbox,
                "final_bbox": hook_bbox,
                "safe_zone_ok": safe_ok,
                "jianying_anim_name": default_jy_anim,
                "anim_duration_ms": int(matched_pair.get("anim_duration_ms", 400)),
                "paired_jianying_sfx": str(matched_pair.get("jianying_sfx_name", "“呼”的转场音效")),
                "reason": f"{style_id} 开场 {giant_fs}px 砸屏巨字冲击 ({giant_mode})",
            })
        max_pkg_w = SUBTITLE_SYMMETRIC_MAX_W

        last_vis_end = hook_ef + int(2.2 * fps)
        last_sent = audio_events["sentences"][-1] if audio_events["sentences"] else None
        stamp_zone_ys = (280, 820, 230, 890)
        stamp_seq = 0
        has_end_cta = False
        for cue in sfx_cues:
            if len(visual_elements) >= max_visual_budget:
                break
            sf = int(cue["frame"])
            if sf < last_vis_end or sf > int((total_dur - 1.35) * fps):
                continue
            sent = next((s for s in audio_events["sentences"] if s["start_frame"] <= sf <= s["end_frame"] + 15), first_sent)
            if sent.get("emotion_or_info") not in ("hook", "info", "twist", "persona", "cta"):
                continue

            kw_label = _extract_clean_flower_phrase(sent, raw_pain_kws=raw_pain_kws, max_len=8)
            est_hold = estimate_read_hold_sec(kw_label, min_sec=2.0, max_sec=3.0)
            ef = min(int(total_dur * fps), sf + int(math.ceil(est_hold * fps)))
            if (ef - sf) / float(fps) < 1.35 or overlaps_code(sf, ef):
                continue
            is_end_cta = bool(is_borderless_flower and last_sent and sent.get("id") == last_sent.get("id"))
            is_quote_item = bool(use_quote_card and not is_end_cta and stamp_seq == 0)
            box_max_w = SUBTITLE_SYMMETRIC_MAX_W
            target_fs = 64 if is_quote_item else (68 if is_borderless_flower else 48)
            box_w = min(box_max_w, max(320 if is_quote_item else 260, _measure_pkg_badge_width(kw_label, pkg_font_path, box_max_w, target_fs=target_fs, min_w=320 if is_quote_item else 260)))
            box_x = SUBTITLE_CENTER_X - box_w // 2
            box_h = 136 if is_end_cta else (132 if is_quote_item else (112 if is_borderless_flower else 96))
            init_y = 890 if is_end_cta else (820 if is_quote_item else stamp_zone_ys[stamp_seq % len(stamp_zone_ys)])
            active_faces = []
            for c in edl.get("clips", []):
                if int(c["timeline_in_frame"]) < ef and int(c["timeline_out_frame"]) > sf:
                    active_faces.extend(faces_by_mp4.get(str(c.get("media_path", "")), []))
            final_bbox = _avoid_faces_for_pkg({"x": box_x, "y": init_y, "w": box_w, "h": box_h}, active_faces)
            if final_bbox["y"] + final_bbox["h"] > 1120:
                final_bbox["y"] = max(162, 1120 - final_bbox["h"])
            ok, _ = check_bbox_in_safe_zone(final_bbox)
            elem_kind = (
                "end_card_cta" if is_end_cta else
                ("quote_citation_card" if is_quote_item else
                 ("stamp_tag" if is_borderless_flower else "flower_text"))
            )
            fg_key = "end_card_color" if is_end_cta else ("flank_color" if is_quote_item else "stamp_color")
            fg_default = "#FFE28A" if (is_end_cta or is_quote_item) else "#FFF589"
            burst_tags = [str(k)[:5] for k in _display_keywords(sent) if str(k).strip()][:3] if use_multi_tag else []
            elem_obj: Dict[str, Any] = {
                "id": f"pkg_{len(visual_elements)+1:03d}",
                "kind": elem_kind,
                "overlay_mode": "borderless" if is_borderless_flower else ("focus_blur" if sent.get("emotion_or_info") == "twist" else "corner"),
                "fg_hex": _safe_readable_fg(flower_spec.get(fg_key), fg_default),
                "accent_hex": giant_accent,
                "stroke_hex": str(flower_spec.get("stroke_color") or "#000000"),
                "text_or_asset": f"“{kw_label}”" if is_quote_item and not kw_label.startswith("“") else kw_label,
                "subtext": f"—— {giant_tag}" if is_quote_item else "",
                "burst_tags": burst_tags,
                "start_frame": sf,
                "end_frame": ef,
                "read_hold_sec": round((ef - sf) / float(fps), 2),
                "aligned_sfx_or_accent_frame": sf,
                "alignment_error_frames": 0,
                "bbox": final_bbox,
                "final_bbox": final_bbox,
                "safe_zone_ok": ok,
                "jianying_anim_name": default_jy_anim,
                "anim_duration_ms": int(matched_pair.get("anim_duration_ms", 400)),
                "paired_jianying_sfx": str(matched_pair.get("jianying_sfx_name", "“呼”的转场音效")),
                "reason": cue["reason"],
            }
            if is_end_cta:
                elem_obj["subtext"] = "评论区聊聊你的看法"
                has_end_cta = True
            else:
                stamp_seq += 1
            visual_elements.append(elem_obj)
            last_vis_end = ef + int(2.5 * fps)

        # 保障片尾互动大字在无框风格下不会因末句缺少独立 SFX cue 而丢失
        if is_borderless_flower and not has_end_cta and last_sent and len(visual_elements) < max_visual_budget:
            ls_sf = int(last_sent.get("start_frame", 0))
            ls_ef = min(int(total_dur * fps), max(ls_sf + int(1.45 * fps), int(last_sent.get("end_frame", ls_sf))))
            if ls_sf >= last_vis_end and (ls_ef - ls_sf) / float(fps) >= 1.40 and not overlaps_code(ls_sf, ls_ef):
                cta_label = _extract_clean_flower_phrase(last_sent, raw_pain_kws=raw_pain_kws, max_len=8) or "欢迎评论聊聊"
                bw = _measure_pkg_badge_width(cta_label, pkg_font_path, max_pkg_w, target_fs=54, min_w=240)
                bx = SUBTITLE_CENTER_X - bw // 2
                active_f = []
                for c in edl.get("clips", []):
                    if int(c["timeline_in_frame"]) < ls_ef and int(c["timeline_out_frame"]) > ls_sf:
                        active_f.extend(faces_by_mp4.get(str(c.get("media_path", "")), []))
                cta_bb = _avoid_faces_for_pkg({"x": bx, "y": 890, "w": bw, "h": 128}, active_f)
                if cta_bb["y"] + cta_bb["h"] > 1120:
                    cta_bb["y"] = max(162, 1120 - cta_bb["h"])
                ok_cta, _ = check_bbox_in_safe_zone(cta_bb)
                visual_elements.append({
                    "id": f"pkg_{len(visual_elements)+1:03d}",
                    "kind": "end_card_cta",
                    "overlay_mode": "borderless",
                    "fg_hex": str(flower_spec.get("end_card_color") or "#FFE28A"),
                    "stroke_hex": str(flower_spec.get("stroke_color") or "#000000"),
                    "text_or_asset": cta_label,
                    "subtext": "评论区聊聊你的看法",
                    "start_frame": ls_sf,
                    "end_frame": ls_ef,
                    "read_hold_sec": round((ls_ef - ls_sf) / float(fps), 2),
                    "aligned_sfx_or_accent_frame": ls_sf,
                    "alignment_error_frames": 0,
                    "bbox": cta_bb,
                    "final_bbox": cta_bb,
                    "safe_zone_ok": ok_cta,
                    "jianying_anim_name": default_jy_anim,
                    "anim_duration_ms": int(matched_pair.get("anim_duration_ms", 400)),
                    "paired_jianying_sfx": str(matched_pair.get("jianying_sfx_name", "“呼”的转场音效")),
                    "reason": "片尾收束互动召唤大字（绑定末句起声帧）",
                })

    # ── 片尾收尾诊断：话没说完、没有行动指引的，补一张 CTA 尾卡（不改台词、不改原声）──
    ending = None
    try:
        import ending_plan as EP
        tr_p = pipeline_dir / "02_transcript.json"
        transcript = read_json(tr_p) if tr_p.is_file() else {}
        segs = transcript.get("segments") or [{"text": str((audio_events["sentences"][-1] or {}).get("text") or "")}]
        profile = {}
        for cand in (pipeline_dir / "00_account_profile.json", pipeline_dir.parent / "content-log" / "account_profile.json"):
            if cand.is_file():
                try:
                    profile = read_json(cand)
                except Exception:
                    profile = {}
                break
        topic_hint = ""
        if len(segs) >= 2:
            topic_hint = str((audio_events["sentences"][-2] or {}).get("text") or "")
        ending = EP.analyze_ending(segs, profile=profile, topic_hint=topic_hint)
        ending["framework_map"] = EP.map_framework(segs)
        if ending["framework_map"]["missing"]:
            print(f"[step6] 台词五格体检：缺「{'、'.join(ending['framework_map']['missing'])}」")
        has_end_card = any(str(e.get("kind")) == "end_card_cta" for e in visual_elements)
        # 用户显式关了花字（--no-flower-text）就不硬塞尾卡，只在报告里点名"片尾没收住"
        if (ending["verdict"] != "closed" and not has_end_card and not no_flower_text
                and len(visual_elements) < max_visual_budget):
            total_frames = int(round(total_dur * fps))
            sf, ef = EP.card_frames(ending, total_frames, fps)
            if ef - sf >= int(1.2 * fps):
                text = EP.best_card_text(ending)
                _card_main, _card_sub = EP.split_card_text(text)
                fs_try = 46
                bw = _measure_pkg_badge_width(text[:16], pkg_font_path, SUBTITLE_SYMMETRIC_MAX_W, target_fs=fs_try, min_w=520)
                bh = 132
                bx = SUBTITLE_CENTER_X - bw // 2
                bb = {"x": bx, "y": 820, "w": bw, "h": bh}
                active_f = []
                for c in edl.get("clips", []):
                    if int(c["timeline_in_frame"]) < ef and int(c["timeline_out_frame"]) > sf:
                        active_f.extend(faces_by_mp4.get(str(c.get("media_path", "")), []))
                bb = _avoid_faces_for_pkg(bb, active_f)
                ok_card, _ = check_bbox_in_safe_zone(bb)
                visual_elements.append({
                    "id": f"pkg_{len(visual_elements)+1:03d}",
                    "kind": "end_card_cta",
                    "overlay_mode": "borderless",
                    "fg_hex": str(flower_spec.get("end_card_color") or "#FFE28A"),
                    "stroke_hex": str(flower_spec.get("stroke_color") or "#000000"),
                    "text_or_asset": _card_main,
                    "subtext": _card_sub,
                    "start_frame": sf,
                    "end_frame": ef,
                    "read_hold_sec": round((ef - sf) / float(fps), 2),
                    "aligned_sfx_or_accent_frame": sf,
                    "alignment_error_frames": 0,
                    "bbox": bb,
                    "final_bbox": bb,
                    "safe_zone_ok": ok_card,
                    "jianying_anim_name": default_jy_anim,
                    "anim_duration_ms": int(matched_pair.get("anim_duration_ms", 400)),
                    "paired_jianying_sfx": str(matched_pair.get("jianying_sfx_name", "“呼”的转场音效")),
                    "reason": f"片尾收尾补位：{ending['reason']}",
                    # 这一条不是源草稿里的语法，是为了"话没说完"补的内容件，单独打标好审好删
                    "deviation_from_style_source": True,
                })
                ending["card_added"] = True
                print(f"[step6] 片尾补了一张收尾卡（{ending['verdict']}）：{text[:20]}")
        else:
            ending["card_added"] = has_end_card
        if ending.get("needs_operator_line") and not ending.get("card_added"):
            print(f"[step6] [提示] {ending['reason']}；下次拍的时候末句可以补一句行动指引")
    except Exception as exc:
        ending = {"verdict": "unknown", "reason": f"收尾诊断不可用：{type(exc).__name__}: {exc}"}

    packaging = {
        "step": "06_packaging",
        "style_id": style_id,
        "no_flower_text": bool(no_flower_text),
        "sfx_track_wav": str(sfx_wav_path.resolve()),
        "sfx_disabled_reason": sfx_disabled_reason,
        "sfx_per_min_target": round(float(sfx_per_min_tgt), 1),
        "sfx_source_dir": f"sound_vibe/sfx/{style_id}",
        "bgm": bgm_info,
        "bgm_disabled_reason": bgm_info.get("disabled_reason"),
        "budget": {
            "max_visual_elements": max_visual_budget,
            "used_visual_elements": len(visual_elements),
            "max_sfx_cues": max_sfx_budget,
            "used_sfx_cues": len(sfx_cues),
        },
        "sfx_cues": sfx_cues,
        "visual_elements": visual_elements,
        "ending_plan": ending,
    }
    write_json(pipeline_dir / "06_packaging.json", packaging)
    print(
        f"[step6] 包装完成：音效 {len(sfx_cues)}/{max_sfx_budget} 个，"
        f"视觉包装 {len(visual_elements)}/{max_visual_budget} 个（±0 帧对齐）-> 06_packaging.json"
    )
    return packaging


def main() -> None:
    maybe_reexec_in_venv()
    parser = argparse.ArgumentParser(description="Step 6: 音效先行与±1帧对齐视觉包装")
    parser.add_argument("--pipeline-dir", required=True)
    parser.add_argument("--style-id", help="绑定风格谱 ID")
    parser.add_argument("--no-flower-text", action="store_true", help="关闭全部自动花字/巨字，仅保留字幕与音效")
    parser.add_argument("--keep-manual-texts", action="store_true", help="换风格时保留已有人工编辑的花字文案")
    args = parser.parse_args()
    run_step6(
        Path(args.pipeline_dir),
        style_id=args.style_id,
        no_flower_text=args.no_flower_text,
        keep_manual_texts=args.keep_manual_texts,
    )


if __name__ == "__main__":
    main()
