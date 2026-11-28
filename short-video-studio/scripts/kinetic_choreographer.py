#!/usr/bin/env python3
"""
[可复用动态花字舞台编排引擎]
从 5 套草稿（style_01 / style_02 / style_03 / style_17 / style_21）与
常用动画音效搭配规范提炼出的正交编排架构：

1. STYLE_DNA_REGISTRY：4 个正交维度描述风格本质
   - video_topology: 画面拓扑（pure_talking_head / talking_head_with_evidence_broll / ...）
   - matting_role: 抠像角色（none / self_matting_behind_text / cutout_over_broll_depth / ...）
   - subtitle_stage_mode: 字幕模式（kinetic_typography_stage / inline_highlight_track / ...）
   - broll_policy: 面对丰富实拍 B-roll 时的策略（evidence_anchored / none / ...）

2. ANIM_SFX_BINDING_TABLE：剪映原生动画 ↔ 渲染动效原语 ↔ 0ms 锁定音效角色字典

3. plan_kinetic_stage()：词级时间戳对齐 + 通用语义分词 + 修辞舞台原语编排，输出同时驱动
   MP4 动画合成（render_mp4）与剪映原生多轨草稿导出（export_jianying_native）的统一 IR。
"""

from __future__ import annotations

import math
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from common import (  # noqa: E402
    ASSETS_DIR,
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    DEFAULT_FPS,
    find_commercial_font,
    find_source_font,
    local_assets_dir,
    read_json,
)
from composition_profile import PROFILE_FILENAME
from director_plan import (
    PLAN_FILENAME,
    _coverage,
    _display_length,
    _media_name,
    _short_conclusion,
    _validate_copy,
    generate_deterministic_director_plan,
    load_or_build_director_plan,
)
from step5_subtitles import (  # noqa: E402
    _ALNUM_MIXED_TOKEN_RE,
    _ASCII_UNIT_PAT,
    _CLAUSE_START_PROTECT_RE,
    _DP_NO_LEAD_CHARS,
    _DP_NO_TAIL_CHARS,
    _HYPHEN_ASCII_TOKEN_RE,
    _MULTI_UNIT_PAT,
    _NUM_UNIT_PRESPLIT_RE,
    _SINGLE_UNIT_CHARS,
    _SINGLE_UNIT_CHARS_NO_PCT,
    _SPEC_MOD_HEAD_PREFIXES,
    _SUFFIX_DERIV_STEM_POS,
    _SUFFIX_MOD_CHARS,
    _UNIT_TAIL_PAT,
    _guard_no_aux_head,
    _is_cantonese_ne_demo_head,
    _is_completed_le_clause,
    _measure_text_px,
    _normalize_pos_tag,
    _pseg_cut_clean,
    _terms_file_for,
    dp_segment_short_lines,
    is_compound_np_body,
    is_degree_complement_haoduo,
    is_independent_svo_clause,
    is_predicative_after_headless_de,
    iter_num_unit_spans,
    load_protected_terms,
    rebalance_asr_sentences,
    split_sentence_into_chunks,
    tokenize_semantic_words,
)

# ── 1. 正交风格 DNA 注册表 ────────────────────────────────────────────────

STYLE_DNA_REGISTRY: Dict[str, Dict[str, Any]] = {
    "style_02": {
        "style_id": "style_02",
        "video_topology": "talking_head_with_evidence_broll",
        "matting_role": "self_matting_behind_text",
        "subtitle_stage_mode": "kinetic_typography_stage",
        "broll_policy": {
            "strategy": "evidence_anchored",
            "target_ratio": 0.22,
            "band": (0.0, 0.30),
            "min_semantic_score": 0.30,
        },
        "palette": {
            "white": "#FFFFFF",
            "lime": "#BCFF00",
            "red": "#B71C1C",
            "pill_bg": "#FFFFFF",
            "bridge_bg": "#000000B3",  # rgba(0,0,0,0.70)
            "stroke": "#000000",
        },
        "font_candidates": [
            "SourceHanSansCN-Bold.otf",
            "NotoSansSC-Bold.ttf",
            "AlimamaShuHeiTi-Bold.otf",
        ],
    },
    # Multi-style opt-in skins (--v6-director) for editorial & store tracks without altering default V5 legacy behavior.
    "style_01": {
        "style_id": "style_01",
        "video_topology": "talking_head_with_evidence_broll",
        "matting_role": "self_matting_behind_text",
        "subtitle_stage_mode": "inline_highlight_track",
        "broll_policy": {"strategy": "evidence_anchored", "target_ratio": 0.35, "band": (0.15, 0.55), "min_semantic_score": 0.25},
        "palette": {"white": "#FFFDF7", "lime": "#D4F542", "red": "#DC2626", "pill_bg": "#FFFDF7", "bridge_bg": "#18181BB8", "stroke": "#0A0A0A"},
        "font_candidates": ["AlimamaShuHeiTi-Bold.otf", "SourceHanSansCN-Bold.otf"],
    },
    "style_03": {
        "style_id": "style_03",
        "video_topology": "pure_talking_head",
        "matting_role": "none",
        "subtitle_stage_mode": "inline_highlight_track",
        "broll_policy": {"strategy": "evidence_anchored", "target_ratio": 0.12, "band": (0.0, 0.25), "min_semantic_score": 0.35},
        "palette": {"white": "#F8FAFC", "lime": "#F59E0B", "red": "#EF4444", "pill_bg": "#0F172A", "bridge_bg": "#0C0F16CC", "stroke": "#000000"},
        "font_candidates": ["SourceHanSerifCN-Bold.otf", "NotoSansCJKsc-Bold.otf"],
    },
    "style_17": {
        "style_id": "style_17",
        "video_topology": "pure_talking_head",
        "matting_role": "none",
        "subtitle_stage_mode": "inline_highlight_track",
        "broll_policy": {"strategy": "evidence_anchored", "target_ratio": 0.15, "band": (0.0, 0.30), "min_semantic_score": 0.30},
        "palette": {"white": "#FAF8F5", "lime": "#CA8A04", "red": "#B91C1C", "pill_bg": "#1C1917", "bridge_bg": "#1C1917CC", "stroke": "#0C0A09"},
        "font_candidates": ["SourceHanSerifCN-Bold.otf", "SourceHanSansCN-Bold.otf"],
    },
}


ALL_24_STYLE_IDS = frozenset(f"style_{i:02d}" for i in range(1, 25))
DIRECTOR_ENABLED_STYLES = ALL_24_STYLE_IDS


def is_director_plan_supported(style_id: str) -> bool:
    """全部 24 套风格均支持大模型总导演规划与圣经闭环。"""
    return str(style_id or "").strip() in ALL_24_STYLE_IDS


def get_style_dna(style_id: str) -> Optional[Dict[str, Any]]:
    sid = str(style_id or "").strip()
    if sid in STYLE_DNA_REGISTRY:
        return STYLE_DNA_REGISTRY[sid]
    if sid not in ALL_24_STYLE_IDS:
        return None
    try:
        from common import load_style_spectrum_entry
        entry = load_style_spectrum_entry(sid) or {}
    except Exception:
        entry = {}
    if not entry:
        return None
    sub_spec = entry.get("subtitle_spec") or {}
    f_cands = list(sub_spec.get("font_candidates") or []) or ["SourceHanSansCN-Bold.otf", "AlimamaShuHeiTi-Bold.otf"]
    kw_col = str(sub_spec.get("keyword_color") or "#FFBF17")
    broll_t = float(entry.get("broll_overlay_ratio_target") if entry.get("broll_overlay_ratio_target") is not None else 0.25)
    return {
        "style_id": sid,
        "video_topology": "pure_talking_head" if broll_t < 0.05 else "talking_head_with_evidence_broll",
        "matting_role": "none",
        "subtitle_stage_mode": "inline_highlight_track",
        "broll_policy": {
            "strategy": "evidence_anchored",
            "target_ratio": round(broll_t, 3),
            "band": (max(0.0, round(broll_t - 0.15, 2)), min(0.95, round(broll_t + 0.20, 2))),
            "min_semantic_score": 0.30,
        },
        "palette": {
            "white": "#FFFFFF",
            "lime": kw_col,
            "red": "#B71C1C",
            "pill_bg": "#18181B",
            "bridge_bg": "#000000B3",
            "stroke": str(sub_spec.get("outline_color") or "#000000"),
        },
        "font_candidates": f_cands,
    }


def is_kinetic_stage_style(style_id: str, enable_v6_director: bool = False) -> bool:
    sid = str(style_id or "").strip()
    dna = get_style_dna(sid)
    if dna and dna.get("subtitle_stage_mode") == "kinetic_typography_stage":
        return True
    return bool(enable_v6_director and sid in DIRECTOR_ENABLED_STYLES)


# ── 2. 动画原语 ↔ 0ms 音效映射字典（常用文字动画与音效搭配） ──

ANIM_SFX_BINDING_TABLE: Dict[str, Dict[str, Any]] = {
    "in:向右集合": {
        "jianying_anim_name": "向右集合",
        "anim_type": "in",
        "anim_primitive": "滑动出入",
        "motion_mode": "slide_in_out",
        "direction": "right",
        "anim_duration_ms": 400,
        "sfx_role": "swish",
        "jianying_sfx_name": "回忆-刷一下",
    },
    "in:打字光标": {
        "jianying_anim_name": "打字光标",
        "anim_type": "in",
        "anim_primitive": "打字机",
        "motion_mode": "typewriter_char",
        "direction": "right",
        "anim_duration_ms": 400,
        "sfx_role": "typewriter",
        "jianying_sfx_name": "打字（字幕专用）",
    },
    "in:向下露出": {
        "jianying_anim_name": "向下露出",
        "anim_type": "in",
        "anim_primitive": "滑动出入",
        "motion_mode": "slide_in_out",
        "direction": "down",
        "anim_duration_ms": 400,
        "sfx_role": "pop",
        "jianying_sfx_name": "气泡弹出",
    },
    "in:向上露出": {
        "jianying_anim_name": "向上露出",
        "anim_type": "in",
        "anim_primitive": "滑动出入",
        "motion_mode": "slide_in_out",
        "direction": "up",
        "anim_duration_ms": 400,
        "sfx_role": "swish",
        "jianying_sfx_name": "回忆-刷一下",
    },
    "in:向左露出": {
        "jianying_anim_name": "向左露出",
        "anim_type": "in",
        "anim_primitive": "滑动出入",
        "motion_mode": "slide_in_out",
        "direction": "left",
        "anim_duration_ms": 300,
        "sfx_role": "swish",
        "jianying_sfx_name": "回忆-刷一下",
    },
    "in:向右露出": {
        "jianying_anim_name": "向右露出",
        "anim_type": "in",
        "anim_primitive": "滑动出入",
        "motion_mode": "slide_in_out",
        "direction": "right",
        "anim_duration_ms": 400,
        "sfx_role": "swish",
        "jianying_sfx_name": "回忆-刷一下",
    },
    "in:随机上升": {
        "jianying_anim_name": "随机上升",
        "anim_type": "in",
        "anim_primitive": "滑动出入",
        "motion_mode": "slide_in_out",
        "direction": "up",
        "anim_duration_ms": 400,
        "sfx_role": "paper_shake",
        "jianying_sfx_name": "纸张抖动声",
    },
    "in:渐显": {
        "jianying_anim_name": "渐显",
        "anim_type": "in",
        "anim_primitive": "滑动出入",
        "motion_mode": "slide_in_out",
        "direction": "up",
        "anim_duration_ms": 400,
        "sfx_role": "paper_shake",
        "jianying_sfx_name": "纸张抖动声",
    },
    "in:开幕": {
        "jianying_anim_name": "开幕",
        "anim_type": "in",
        "anim_primitive": "擦除",
        "motion_mode": "wipe_reveal",
        "direction": "right",
        "anim_duration_ms": 400,
        "sfx_role": "pop",
        "jianying_sfx_name": "气泡弹出",
    },
    "in:激光雕刻": {
        "jianying_anim_name": "激光雕刻",
        "anim_type": "in",
        "anim_primitive": "擦除",
        "motion_mode": "wipe_reveal",
        "direction": "right",
        "anim_duration_ms": 400,
        "sfx_role": "paper_shake",
        "jianying_sfx_name": "纸张抖动声",
    },
    "in:雪光模糊": {
        "jianying_anim_name": "雪光模糊",
        "anim_type": "in",
        "anim_primitive": "模糊显影",
        "motion_mode": "ink_bloom",
        "direction": "center",
        "anim_duration_ms": 400,
        "sfx_role": "boom_hollow",
        "jianying_sfx_name": "综艺开头-咚（空旷）",
    },
    "in:晕开": {
        "jianying_anim_name": "晕开",
        "anim_type": "in",
        "anim_primitive": "模糊显影",
        "motion_mode": "ink_bloom",
        "direction": "center",
        "anim_duration_ms": 400,
        "sfx_role": None,  # 压暗叠压时紧接前数字音效，不叠加噪杂
        "jianying_sfx_name": "",
    },
    "in:缩小 II": {
        "jianying_anim_name": "缩小 II",
        "anim_type": "in",
        "anim_primitive": "缩小",
        "motion_mode": "scale_in",
        "direction": "center",
        "anim_duration_ms": 400,
        "sfx_role": "punch",
        "jianying_sfx_name": "砰，拳击声",
    },
    "in:呐喊声波": {
        "jianying_anim_name": "呐喊声波",
        "anim_type": "in",
        "anim_primitive": "弹入",
        "motion_mode": "bounce_smash",
        "direction": "center",
        "anim_duration_ms": 400,
        "sfx_role": "boom_heavy",
        "jianying_sfx_name": "综艺嘭字幕音效震撼出场",
    },
    "in:滚入": {
        "jianying_anim_name": "滚入",
        "anim_type": "in",
        "anim_primitive": "快速翻页",
        "motion_mode": "page_flip",
        "direction": "down",
        "anim_duration_ms": 300,
        "sfx_role": "cash_register",
        "jianying_sfx_name": "超市电子钱柜 结账",
    },
    "loop:弹幕滚动": {
        "jianying_anim_name": "弹幕滚动",
        "anim_type": "loop",
        "anim_primitive": "弹幕滚动",
        "motion_mode": "barrel_scroll",
        "direction": "left",
        "anim_duration_ms": 1700,
        "sfx_role": "whoosh_in",
        "jianying_sfx_name": "回忆-刷一下",
    },
}


BANNED_SFX_ROLES = frozenset({"ding", "chime"})


def ensure_kinetic_sfx_assets(
    out_dir: Path,
    style_id: str = "style_02",
    assets_dir: Optional[Path] = None,
) -> Dict[str, Path]:
    """准备全套与花字动画一一对应的 48kHz 立体声 WAV 音效，兼具质感与强瞬态起音。"""
    import numpy as np
    from synth_motion_sfx import SR, synthesize_sfx_waveform, write_wav_stereo

    sfx_dir = Path(out_dir)
    sfx_dir.mkdir(parents=True, exist_ok=True)
    result: Dict[str, Path] = {}
    root_assets = Path(assets_dir) if assets_dir is not None else ASSETS_DIR

    # 1. 优先复用资产库中本风格真实录音音效（先随仓，再本机 local_assets/sound_vibe/sfx/<style_id>）
    sfx_role_keywords = {
        "punch": ("拳击", "砰"),
        "cash_register": ("钱柜", "结账"),
        "paper_shake": ("纸张", "空格"),
        "boom_hollow": ("咚", "突然出现"),
        "boom_heavy": ("震撼", "嘭"),
        "pop": ("气泡", "轻敲"),
        "swish": ("刷一下", "回忆"),
        "whoosh_in": ("刷一下", "回忆"),
        "typewriter": ("打字", "空格"),
    }
    search_sfx_dirs = [root_assets / "sound_vibe" / "sfx" / style_id]
    loc_assets = local_assets_dir(root_assets)
    if loc_assets is not None:
        search_sfx_dirs.append(loc_assets / "sound_vibe" / "sfx" / style_id)
    local_pool_files: List[Path] = []
    for d_s in search_sfx_dirs:
        if d_s.is_dir():
            for p_s in sorted(d_s.iterdir()):
                if p_s.is_file() and p_s.suffix.lower() in (".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"):
                    if not any(ban in p_s.name for ban in ("叮", "滴", "放屁", "尴尬")):
                        local_pool_files.append(p_s.resolve())
    for role_k, kws_k in sfx_role_keywords.items():
        if role_k in BANNED_SFX_ROLES:
            continue
        hit_s = next((p_s for p_s in local_pool_files if any(kw in p_s.name for kw in kws_k)), None)
        if hit_s is not None:
            result[role_k] = hit_s

    static_sources = {
        "swish": root_assets / "sound_vibe" / "sfx" / "style_02_transition.wav",
        "tip": root_assets / "sound_vibe" / "sfx" / "style_02_ui.wav",
        "boom_hollow": root_assets / "sound_vibe" / "sfx" / "style_02_accent.wav",
        "boom_heavy": root_assets / "sound_vibe" / "sfx" / "style_03_accent.wav",
        "cash_register": root_assets / "sound_vibe" / "sfx" / "style_15_receipt.wav",
        "whoosh_in": root_assets / "sound_vibe" / "sfx" / "style_03_transition.wav",
    }
    for role, src in static_sources.items():
        if role not in BANNED_SFX_ROLES and role not in result and src.is_file():
            result[role] = src.resolve()

    # 2. 为其余关键动画角色按音色特征精准合成 WAV（无外网依赖、无版权风险、峰值对齐干净）
    synth_specs = {
        "pop": ("pop", 19),
        "paper_shake": ("peel", 23),
        "punch": ("impact", 31),
        "whoosh_in": ("whoosh_in", 17),
        "swish": ("swish", 13),
        "boom_hollow": ("impact", 47),
        "boom_heavy": ("impact", 61),
        "cash_register": ("pop", 71),
        "tip": ("pop_low", 29),
    }
    for role, (kind, seed) in synth_specs.items():
        if role in BANNED_SFX_ROLES or role in result:
            continue
        p = sfx_dir / f"{style_id}_{role}.wav"
        if not p.is_file():
            write_wav_stereo(p, synthesize_sfx_waveform(kind, seed=seed))
        result[role] = p.resolve()

    # 3. 专属“打字机 4 连敲”（优先真实打字音效，否则合成机械键击）
    if "typewriter" not in result:
        tw_p = sfx_dir / f"{style_id}_typewriter.wav"
        if not tw_p.is_file():
            click_w = synthesize_sfx_waveform("tick", seed=11)
            step = int(0.068 * SR)
            total = step * 4 + len(click_w)
            buf = np.zeros((total, 2), dtype=np.float32)
            for k, mul in enumerate((1.0, 0.85, 0.92, 0.78)):
                st = k * step
                buf[st : st + len(click_w)] += click_w * mul
            peak = float(np.max(np.abs(buf))) + 1e-9
            buf = (buf / peak * 0.72).astype(np.float32)
            write_wav_stereo(tw_p, buf)
        result["typewriter"] = tw_p.resolve()

    return result


# ── 3. 坐标换算与词级语义对齐工具 ───────────────────────────────────────────

def norm_to_canvas_px(x_norm: float, y_norm: float) -> Tuple[int, int]:
    """将剪映归一化坐标 (-1..1, 原点在画布正中，Y 向上为正) 转为 1080×1920 中心像素坐标。"""
    cx = int(round((0.5 + float(x_norm) * 0.5) * CANVAS_WIDTH))
    cy = int(round((0.5 - float(y_norm) * 0.5) * CANVAS_HEIGHT))
    return cx, cy


def canvas_px_to_norm(cx: int, cy: int) -> Tuple[float, float]:
    xn = (float(cx) / float(CANVAS_WIDTH) - 0.5) * 2.0
    yn = (0.5 - float(cy) / float(CANVAS_HEIGHT)) * 2.0
    return round(xn, 4), round(yn, 4)


_DISPLAY_PUNCT_RE = re.compile(
    r"(?<![A-Za-z0-9])\.|(?<=[A-Za-z0-9])\.(?!\d)"
    r"|(?<!\d)[:：](?!\d)|(?<!\d)[:：](?=\d)|(?<=\d)[:：](?!\d)"
    r"|(?<!\d),(?!\d)|(?<!\d),(?=\d)|(?<=\d),(?!\d{3}(?!\d))"
    r"|[，。、；！？!?;]+"
)


def clean_display_text(text: str) -> str:
    """清除结尾/孤立标点与中文间多余空格，保留数字间小数点/冒号/千分位逗号与英文词间单空格。"""
    t = re.sub(r"(?<=\d)：(?=\d)", ":", str(text or ""))
    t = _DISPLAY_PUNCT_RE.sub("", t)
    t = re.sub(r"(?<=[\u4e00-\u9fff])\s+|\s+(?=[\u4e00-\u9fff])", "", t)
    return re.sub(r"\s+", " ", t).strip()


_FILLER_CHARS = set("啊呀哦呃诶嘛呢吧咧啦哇呵嗯")


def _has_word_timing(sent: Dict[str, Any]) -> bool:
    """该句是否带可用的词级时间戳（决定能否把句首连词并进大字起点）。"""
    return any(
        clean_display_text(str(w.get("text") or w.get("word") or ""))
        for w in sent.get("words") or [] if isinstance(w, dict)
    )


def _extract_char_timeline(sent: Dict[str, Any]) -> List[Tuple[str, float, float]]:
    """
    从句子的 `words` 字段提取每个字的精准时间段；缺失时按字符均分兜底。
    对粤语/口语填充字也能兼容映射到邻近时间。
    """
    st = float(sent.get("start_sec") or 0.0)
    en = float(sent.get("end_sec") or st + 0.4)
    words = sent.get("words") or []
    spans: List[Tuple[str, float, float]] = []
    all_words_timed = bool(words) and all(
        (w.get("start_sec") is not None or w.get("start") is not None)
        and (w.get("end_sec") is not None or w.get("end") is not None)
        for w in words
        if clean_display_text(str(w.get("text") or w.get("word") or ""))
    )
    if all_words_timed:
        for w in words:
            wt = clean_display_text(str(w.get("text") or w.get("word") or ""))
            if not wt:
                continue
            ws = float(w.get("start_sec") if w.get("start_sec") is not None else w["start"])
            we = max(ws + 0.04, float(w.get("end_sec") if w.get("end_sec") is not None else w["end"]))
            if len(wt) == 1:
                spans.append((wt, ws, we))
            else:
                dt = (we - ws) / len(wt)
                for idx_c, ch in enumerate(wt):
                    spans.append((ch, ws + idx_c * dt, ws + (idx_c + 1) * dt))
    if not spans:
        txt = clean_display_text(str(sent.get("text") or ""))
        n = max(1, len(txt))
        dt = max(0.05, (en - st) / n)
        for idx_c, ch in enumerate(txt):
            spans.append((ch, st + idx_c * dt, min(en, st + (idx_c + 1) * dt)))
    return spans


def map_chunks_to_times(
    chunks: List[str],
    char_timeline: List[Tuple[str, float, float]],
    group_start: float,
    group_end: float,
) -> List[Tuple[str, float, float]]:
    """
    用顺序游标匹配把若干分块文字精准对齐到 ASR 词级时间戳。
    即使句中存在微调正字或口癖字差异，也保持时间单调不倒退。
    """
    if not chunks:
        return []
    tl_chars = [c for c, _, _ in char_timeline]
    total_chars = sum(max(1, len(clean_display_text(c))) for c in chunks)
    cursor = 0
    raw_hits: List[Tuple[str, float, float]] = []
    consumed = 0

    for c_idx, ch_txt in enumerate(chunks):
        c_clean = clean_display_text(ch_txt)
        if not c_clean:
            continue
        fallback_s = group_start + (group_end - group_start) * (consumed / max(1, total_chars))
        consumed += len(c_clean)
        fallback_e = group_start + (group_end - group_start) * (consumed / max(1, total_chars))

        hit_indices: List[int] = []
        scan_pos = cursor
        for ch in c_clean:
            found = -1
            for k in range(scan_pos, min(len(tl_chars), scan_pos + 8)):
                if tl_chars[k] == ch:
                    found = k
                    break
            if found >= 0:
                hit_indices.append(found)
                scan_pos = found + 1
        if hit_indices:
            s_t = char_timeline[hit_indices[0]][1]
            e_t = char_timeline[hit_indices[-1]][2]
            cursor = hit_indices[-1] + 1
        else:
            # 按字符配比平滑估算
            if cursor < len(char_timeline):
                s_t = char_timeline[cursor][1]
                cursor = min(len(char_timeline), cursor + len(c_clean))
                e_t = char_timeline[max(0, cursor - 1)][2]
            else:
                s_t, e_t = fallback_s, fallback_e
        raw_hits.append((ch_txt, round(max(group_start, s_t), 3), round(min(group_end, max(s_t + 0.15, e_t)), 3)))

    # 保证首块齐group_start（避免0s开场黑隙）、后续块时间单调递增
    out: List[Tuple[str, float, float]] = []
    for i, (txt, s_t, e_t) in enumerate(raw_hits):
        if i == 0 and abs(s_t - group_start) <= 0.12:
            s_t = group_start
        if out and s_t < out[-1][1] + 0.15:
            s_t = min(group_end - 0.25, out[-1][1] + 0.18)
        e_t = max(s_t + 0.18, min(group_end, e_t))
        out.append((txt, round(s_t, 3), round(e_t, 3)))
    return out


_ZH_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_ZH_UNITS = {"十": 10, "百": 100, "千": 1000}


def _parse_zh_int_below_wan(token: str) -> Optional[int]:
    """解析千位及以内的中文整数（如 两百 -> 200，三百 -> 300，两千 -> 2000，二十六 -> 26）。"""
    if not token:
        return None
    if not any(ch in token for ch in ("十", "百", "千")):
        return None
    total = 0
    cur = 0
    for ch in token:
        if ch in _ZH_DIGITS:
            cur = _ZH_DIGITS[ch]
        elif ch in _ZH_UNITS:
            unit = _ZH_UNITS[ch]
            if cur == 0 and ch == "十":
                cur = 1
            elif cur == 0:
                return None
            total += cur * unit
            cur = 0
        else:
            return None
    return total + cur


def _normalize_zh_numerals_with_map(text: str) -> Tuple[str, List[int]]:
    """通用中文数词转阿拉伯数字归一化（返回归一化串与每个字符在原串的起始下标映射）。
    - 4 位年份如「二零二五(?=年)」->「2025」
    - 含十/百/千的位值中文整数如「两百」->「200」、「三百万」->「300万」、「两千」->「2000」、「十万」->「10万」
    - 保留「十分」「第一」「一个」「一直」等非位值量词/副词不动。
    """
    src = str(text or "")
    if not src:
        return "", []
    repls: List[Tuple[int, int, str]] = []
    # 1) 四位年份
    for m in re.finditer(r"[零〇一二三四五六七八九]{4}(?=年)", src):
        digits = "".join(str(_ZH_DIGITS[ch]) for ch in m.group(0))
        repls.append((m.start(), m.end(), digits))
    # 2) 位值中文整数（含百/千，或 2..9+十[1..9]，或 十+[1..9万亿]）
    for m in re.finditer(r"[零〇一二两三四五六七八九十百千]+", src):
        sa, sb = m.start(), m.end()
        if any(sa < rb and sb > ra for ra, rb, _ in repls):
            continue
        tok = m.group(0)
        if tok == "十" and not (sb < len(src) and src[sb] in ("万", "亿")):
            continue
        if len(tok) == 1 and tok not in ("十",):
            continue
        if tok.startswith("十") and len(tok) == 2 and tok[1] not in _ZH_DIGITS:
            continue
        val = _parse_zh_int_below_wan(tok)
        if val is not None and val >= 10:
            repls.append((sa, sb, str(val)))
    repls.sort(key=lambda x: x[0])
    out_chars: List[str] = []
    idx_map: List[int] = []
    cur = 0
    for ra, rb, rep in repls:
        while cur < ra:
            out_chars.append(src[cur])
            idx_map.append(cur)
            cur += 1
        for ch_r in rep:
            out_chars.append(ch_r)
            idx_map.append(ra)
        cur = rb
    while cur < len(src):
        out_chars.append(src[cur])
        idx_map.append(cur)
        cur += 1
    return "".join(out_chars), idx_map


def _normalize_zh_numerals(text: str) -> str:
    return _normalize_zh_numerals_with_map(text)[0]


def _style02_font_path() -> Optional[str]:
    return (
        find_source_font(None, "style_02", _STYLE02_UNIFIED_FONT_CANDS)
        or find_commercial_font(_STYLE02_UNIFIED_FONT_CANDS, verified_only=True)
        or find_commercial_font(_STYLE02_UNIFIED_FONT_CANDS)
    )


def _compute_sentence_word_gaps(sent: Dict[str, Any], text: str) -> Dict[int, float]:
    """将句子逐字时间戳之间的停顿（>=0.08s）对齐到句子原始字符串的字符断点下标。"""
    s_txt = str(text or "")
    if not s_txt:
        return {}
    tl = [c for c in _extract_char_timeline(sent) if c[0].strip()]
    if len(tl) < 2:
        return {}
    # 把 clean 字符序列与原始字符串中的非标点字符对齐
    txt_clean_positions: List[int] = [
        idx for idx, ch in enumerate(s_txt)
        if clean_display_text(ch) and not ch.isspace()
    ]
    if len(txt_clean_positions) != len(tl):
        tl_no_punct = [c for c in tl if clean_display_text(c[0]) and not c[0].isspace()]
        if len(txt_clean_positions) == len(tl_no_punct):
            tl = tl_no_punct
        else:
            return {}
    gaps: Dict[int, float] = {}
    for k in range(1, len(tl)):
        g = float(tl[k][1]) - float(tl[k - 1][2])
        if g >= 0.08:
            gaps[txt_clean_positions[k]] = round(g, 4)
    return gaps


def split_clean_phrases(
    text: str,
    target_min: int = 3,
    target_max: int = 8,
    protected_terms: Optional[List[str]] = None,
    *,
    max_width_px: int = 640,
    font_path: Optional[str] = None,
    font_size: int = 69,
    word_gaps: Optional[Dict[int, float]] = None,
    strict_width: bool = True,
) -> List[str]:
    """
    通用中文/粤语口播短语切分器：统一复用 step5_subtitles.dp_segment_short_lines 词性+DP 引擎，
    保证零挂尾助词、零助词行首、不断碎固定搭配/数字量词，且单行严格落在安全像素宽内。
    """
    raw = str(text or "").strip()
    if not raw:
        return []
    # 按句末硬标点拆分后再喂入 DP（保留句内逗号供 DP zero-cost 自然断开，同时映射 word_gaps）
    hard_spans = [
        (m.start(), m.end(), m.group(0).strip())
        for m in re.finditer(r"[^。！？!?]+", raw)
        if clean_display_text(m.group(0))
    ]
    if not hard_spans:
        return []
    eff_terms = list(dict.fromkeys(list(protected_terms or []) + _EXTRA_COHESIVE))
    if font_path is None:
        eff_fs = min(int(font_size), max(40, int((max_width_px - 32) // max(1, target_max))))
    else:
        eff_fs = int(font_size)

    out_chunks: List[Dict[str, Any]] = []
    part_meta: List[Tuple[str, Optional[Dict[int, float]]]] = []
    for part_idx, (sp_start, sp_end, part) in enumerate(hard_spans):
        lead_ws = len(raw[sp_start:sp_end]) - len(raw[sp_start:sp_end].lstrip())
        part_off = sp_start + lead_ws
        part_gaps: Optional[Dict[int, float]] = None
        if word_gaps:
            part_gaps = {
                gp - part_off: gv
                for gp, gv in word_gaps.items()
                if part_off < gp < part_off + len(part)
            }
        part_meta.append((part, part_gaps))
        dp_segs = dp_segment_short_lines(
            part,
            max_width_px=max_width_px,
            font_path=font_path,
            font_size=eff_fs,
            terms=eff_terms,
            max_chars=target_max,
            strip_punct=True,
            word_gaps=part_gaps,
            strict_width=strict_width,
        )
        for _sa, _sb, seg_t in dp_segs:
            ct = clean_display_text(seg_t)
            if ct:
                out_chunks.append({
                    "text": ct,
                    "start_sec": 0.0,
                    "end_sec": 1.0,
                    "words": [],
                    "_part_idx": part_idx,
                    "_sa": _sa,
                    "_sb": _sb,
                    "_orig_ct": ct,
                })
    if not out_chunks:
        return []
    out_chunks = _guard_no_aux_head(
        out_chunks,
        max_chars=target_max,
        max_width_px=max_width_px if strict_width else None,
        font_path=font_path,
        font_size=eff_fs,
    )
    chunk_entries: List[Tuple[str, int, int, int]] = []
    for c in out_chunks:
        ct_now = str(c.get("text") or "").strip()
        if not ct_now:
            continue
        p_idx = int(c.get("_part_idx", 0))
        sa_c = int(c.get("_sa", 0))
        sb_c = int(c.get("_sb", 0))
        if ct_now != str(c.get("_orig_ct") or "") and 0 <= p_idx < len(part_meta):
            p_str = part_meta[p_idx][0]
            idx_f = p_str.find(ct_now, max(0, sa_c - 6))
            if idx_f >= 0:
                sa_c, sb_c = idx_f, idx_f + len(ct_now)
        chunk_entries.append((ct_now, p_idx, sa_c, sb_c))
    if len(chunk_entries) <= 1:
        return [e[0] for e in chunk_entries]

    # 仅合并长度 < target_min 的碎片，且严禁跨句界、跨标点或跨 >=0.22s 声学气口融合，亦不可超宽或吞并序数词
    _strict_no_tail = frozenset("的嘅地得之把被从向给俾在喺这那呢嗰𠮶")
    merged: List[Tuple[str, int, int, int]] = []
    for ph, c_idx, cur_sa, cur_sb in chunk_entries:
        prev_ord = bool(merged and _ORDINAL_SENT_RE.match(merged[-1][0]))
        cur_ord = bool(_ORDINAL_SENT_RE.match(ph))
        blocked_by_pause_or_punct = False
        if merged:
            _p_txt, p_idx_prev, _prev_sa, prev_sb = merged[-1]
            if p_idx_prev != c_idx:
                blocked_by_pause_or_punct = True
            elif 0 <= c_idx < len(part_meta):
                part_str, p_gaps = part_meta[c_idx]
                bnd_slice = part_str[max(0, prev_sb - 1) : min(len(part_str), cur_sa + 1)]
                if any(ch in "，、；：,;:" for ch in bnd_slice):
                    blocked_by_pause_or_punct = True
                elif p_gaps and any(gv >= 0.22 for gp, gv in p_gaps.items() if prev_sb - 1 <= gp <= cur_sa + 1):
                    blocked_by_pause_or_punct = True
        if (
            merged
            and not blocked_by_pause_or_punct
            and len(ph) < target_min
            and len(merged[-1][0]) + len(ph) <= target_max + 1
            and not (cur_ord and len(merged[-1][0]) >= 4)
            and not (prev_ord and len(ph) >= 5)
        ):
            cand = merged[-1][0] + ph
            pw, _ = _measure_text_px(cand, font_path, eff_fs)
            if pw <= max_width_px and cand[-1] not in _strict_no_tail:
                merged[-1] = (cand, c_idx, merged[-1][2], cur_sb)
                continue
        if (
            merged
            and not blocked_by_pause_or_punct
            and len(merged[-1][0]) < target_min
            and len(merged[-1][0]) + len(ph) <= target_max + 1
            and not (prev_ord and len(ph) >= 5)
            and not (cur_ord and len(merged[-1][0]) >= 4)
        ):
            cand = merged[-1][0] + ph
            pw, _ = _measure_text_px(cand, font_path, eff_fs)
            if pw <= max_width_px:
                merged[-1] = (cand, c_idx, merged[-1][2], cur_sb)
                continue
        merged.append((ph, c_idx, cur_sa, cur_sb))

    # <=3字残句同句跨顿/跨逗号安全并回 + 2-chunk 语义重切兜底（仅在严格像素宽内且不制造挂尾/序数尾时生效）
    def _clean_len_p(s_val: str) -> int:
        return len(re.sub(r"[\s，。！？、；：,.!?;:]+", "", str(s_val or "")))

    for _ in range(4):
        if len(merged) < 2:
            break
        orphan_fixed = False
        for k_idx in range(len(merged)):
            ph_k, c_idx_k, sa_k, sb_k = merged[k_idx]
            if _clean_len_p(ph_k) > 3 or _ORDINAL_SENT_RE.match(ph_k) or _CLAUSE_START_PROTECT_RE.match(ph_k):
                continue
            if 0 <= c_idx_k < len(part_meta) and _clean_len_p(part_meta[c_idx_k][0]) < 5:
                continue
            # 1) 优先尝试与同句内较短的相邻块直接合并（允许跨越句内顿号/逗号/0.22s以上气口，只要总宽<=max_width_px且零挂尾）
            merge_cands: List[Tuple[int, int, int, str]] = []
            for nb_i in (k_idx - 1, k_idx + 1):
                if not (0 <= nb_i < len(merged)):
                    continue
                lo, hi = (nb_i, k_idx) if nb_i < k_idx else (k_idx, nb_i)
                l_txt_m, l_pidx, _lsa, _lsb = merged[lo]
                r_txt_m, r_pidx, _rsa, _rsb = merged[hi]
                if l_pidx != r_pidx:
                    continue
                join_sp = " " if (l_txt_m and r_txt_m and l_txt_m[-1].isascii() and l_txt_m[-1].isalnum() and r_txt_m[0].isascii() and r_txt_m[0].isalnum()) else ""
                cand_m = l_txt_m + join_sp + r_txt_m
                if _clean_len_p(cand_m) > target_max + 1:
                    continue
                if strict_width and _measure_text_px(cand_m, font_path, eff_fs)[0] > max_width_px:
                    continue
                if len(_clean_len_p(cand_m) * "x") >= 4 and re.search(r"(第[一二三四五六七八九十两\d]+)$", cand_m):
                    continue
                if cand_m[-1] in _ADAPTIVE_STRICT_TAIL:
                    continue
                if cand_m[0] in _DP_NO_LEAD_CHARS and not (cand_m[0] == "呢" and _is_cantonese_ne_demo_head(cand_m)):
                    continue
                ctx_before = [merged[lo - 1][0]] if lo - 1 >= 0 else []
                ctx_after = [merged[hi + 1][0]] if hi + 1 < len(merged) else []
                old_brks = _count_genitive_or_tail_breaks(ctx_before + [l_txt_m, r_txt_m] + ctx_after, protected_terms=eff_terms)
                new_brks = _count_genitive_or_tail_breaks(ctx_before + [cand_m] + ctx_after, protected_terms=eff_terms)
                if new_brks > old_brks:
                    continue
                merge_cands.append((_clean_len_p(merged[nb_i][0]), 0 if nb_i < k_idx else 1, lo, cand_m))
            if merge_cands:
                _, _, lo, cand_m = min(merge_cands)
                hi = lo + 1
                merged[lo] = (cand_m, merged[lo][1], merged[lo][2], merged[hi][3])
                merged.pop(hi)
                orphan_fixed = True
                break
        if not orphan_fixed:
            break

    for _ in range(3):
        if len(merged) < 2:
            break
        resplit_done = False
        for k_idx in range(len(merged)):
            ph_k, c_idx_k, sa_k, sb_k = merged[k_idx]
            if _clean_len_p(ph_k) > 3 or _ORDINAL_SENT_RE.match(ph_k) or _CLAUSE_START_PROTECT_RE.match(ph_k):
                continue
            if 0 <= c_idx_k < len(part_meta) and _clean_len_p(part_meta[c_idx_k][0]) < 5:
                continue
            best_rs: Optional[Tuple[Tuple[int, int, int], int, int, str, str]] = None
            for nb_i in (k_idx - 1, k_idx + 1):
                if not (0 <= nb_i < len(merged)):
                    continue
                lo, hi = (nb_i, k_idx) if nb_i < k_idx else (k_idx, nb_i)
                l_txt_m, l_pidx, l_sa, l_sb = merged[lo]
                r_txt_m, r_pidx, r_sa, r_sb = merged[hi]
                if l_pidx != r_pidx:
                    continue
                cross_comma_orphan = False
                if 0 <= l_pidx < len(part_meta):
                    part_str, p_gaps = part_meta[l_pidx]
                    bnd_slice = part_str[max(0, l_sb - 1) : min(len(part_str), r_sa + 1)]
                    if p_gaps and any(gv >= 0.22 for gp, gv in p_gaps.items() if l_sb - 1 <= gp <= r_sa + 1):
                        continue
                    if any(ch in "；：;:" for ch in bnd_slice):
                        continue
                    if any(ch in "，、," for ch in bnd_slice):
                        cross_comma_orphan = True
                join_sp = " " if (l_txt_m and r_txt_m and l_txt_m[-1].isascii() and l_txt_m[-1].isalnum() and r_txt_m[0].isascii() and r_txt_m[0].isalnum()) else ""
                comb = l_txt_m + join_sp + r_txt_m
                if not comb or len(comb) > 22:
                    continue
                toks_c = tokenize_semantic_words(comb, terms=eff_terms)
                acc_c = 0
                for tk_c in toks_c[:-1]:
                    acc_c += len(tk_c)
                    l_new = comb[:acc_c].strip()
                    r_new = comb[acc_c:].strip()
                    if not l_new or not r_new:
                        continue
                    if _clean_len_p(l_new) < 4 or _clean_len_p(r_new) < 4:
                        continue
                    if _clean_len_p(l_new) > (target_max if cross_comma_orphan else target_max + 1) or _clean_len_p(r_new) > (target_max if cross_comma_orphan else target_max + 1):
                        continue
                    if strict_width and (
                        _measure_text_px(l_new, font_path, eff_fs)[0] > max_width_px
                        or _measure_text_px(r_new, font_path, eff_fs)[0] > max_width_px
                    ):
                        continue
                    if _ORDINAL_SENT_RE.match(l_new) or _ORDINAL_SENT_RE.match(r_new):
                        continue
                    if l_new[-1] in _ADAPTIVE_STRICT_TAIL:
                        continue
                    if r_new[0] in _DP_NO_LEAD_CHARS and not (r_new[0] == "呢" and _is_cantonese_ne_demo_head(r_new)):
                        continue
                    brk_cnt = _count_genitive_or_tail_breaks([l_new, r_new], protected_terms=eff_terms)
                    if cross_comma_orphan and brk_cnt > 0:
                        continue
                    if brk_cnt > 1 or (brk_cnt == 1 and not l_new.endswith(("几个", "一间", "一份", "一种"))):
                        continue
                    pen_tail = 1 if brk_cnt == 1 else 0
                    sc_rs = (pen_tail, abs(_clean_len_p(l_new) - _clean_len_p(r_new)), _clean_len_p(r_new))
                    if best_rs is None or sc_rs < best_rs[0]:
                        best_rs = (sc_rs, lo, hi, l_new, r_new)
            if best_rs is not None:
                _, lo, hi, l_new, r_new = best_rs
                l_pidx = merged[lo][1]
                l_sa0 = merged[lo][2]
                r_sb0 = merged[hi][3]
                merged[lo] = (l_new, l_pidx, l_sa0, l_sa0 + len(l_new))
                merged[hi] = (r_new, l_pidx, max(l_sa0 + len(l_new), r_sb0 - len(r_new)), r_sb0)
                resplit_done = True
                break
        if not resplit_done:
            break

    return [m[0] for m in merged if m[0]]


_ADAPTIVE_STRICT_TAIL = frozenset("的嘅地得之把被从向给俾在喺这那呢嗰𠮶某每各哪")
_ADAPTIVE_GENITIVE_AUX = frozenset("的嘅地得之")
_ADAPTIVE_GENITIVE_NEXT_POS = frozenset({"n", "nr", "ns", "nt", "nz", "vn", "s", "f", "m", "mq", "q", "a", "i", "l"})
_MOD_NOUN_NEXT_POS = frozenset({"n", "nr", "ns", "nt", "nz", "vn", "s", "f"})
_ADAPTIVE_MULTI_MOD_TAILS = (
    "有啲", "好多", "呢啲", "𠮶啲", "嗰啲", "呢个", "嗰个", "𠮶个",
    "这个", "那个", "哪个", "每个", "某个", "几个", "一间", "一份", "一种",
    "𠮶种", "嗰种", "这种", "那种", "呢种", "哪种", "每种", "某种",
)
_ADAPTIVE_NEG_MOD_TAILS = ("没有", "没", "不", "唔", "别", "不要", "不能", "不会", "不想", "不敢", "不用")
_ADAPTIVE_DEG_NEG_SINGLE_ADV = frozenset("最很太极更不没唔")


def _count_genitive_or_tail_breaks(
    phrases: List[str],
    protected_terms: Optional[List[str]] = None,
) -> int:
    if not phrases:
        return 0
    breaks = 0
    for idx, cur in enumerate(phrases):
        if not cur:
            continue
        if cur[0] in _DP_NO_LEAD_CHARS and not (cur[0] == "呢" and _is_cantonese_ne_demo_head(cur)):
            breaks += 1
        if idx + 1 >= len(phrases):
            continue
        nxt = phrases[idx + 1]
        if not nxt:
            continue
        if protected_terms:
            joint_pt = cur + nxt
            cut_pt = len(cur)
            if any(
                m_pt.start() < cut_pt < m_pt.end()
                for pt in protected_terms
                if len(pt) >= 2 and pt in joint_pt
                for m_pt in re.finditer(re.escape(pt), joint_pt)
            ):
                breaks += 1

        _bound_cache: Optional[Tuple[str, str, str, str]] = None

        def _get_bound() -> Tuple[str, str, str, str]:
            nonlocal _bound_cache
            if _bound_cache is not None:
                return _bound_cache
            pairs_b = _pseg_cut_clean(cur + nxt, prot_terms=protected_terms)
            pos_acc = 0
            wb_r, pb_r, wa_r, pa_r = "", "", "", ""
            for idx_p, (wp, fp) in enumerate(pairs_b):
                if pos_acc == len(cur):
                    wa_r, pa_r = wp, fp
                    if idx_p > 0:
                        wb_r, pb_r = pairs_b[idx_p - 1]
                    break
                pos_acc += len(wp)
            _bound_cache = (wb_r, pb_r, wa_r, pa_r)
            return _bound_cache

        if nxt[0] in _ADAPTIVE_GENITIVE_AUX:
            breaks += 1
        elif cur[-1] in {"的", "嘅"} and not cur.endswith(("出来的", "起来的", "下去的", "过来的", "过去的")):
            _wb, _pb, _wa, pa = _get_bound()
            pa = _normalize_pos_tag(_wa, pa)
            if (
                pa in _ADAPTIVE_GENITIVE_NEXT_POS
                and not is_independent_svo_clause(nxt, prot_terms=protected_terms, prev_plain=cur)
                and not is_predicative_after_headless_de(cur, nxt, prot_terms=protected_terms)
            ):
                breaks += 1
        elif cur[-1] in {"了", "咗", "着", "过"} and not cur.endswith(("出来了", "起来了", "下去了", "回来了", "过去了", "进来了", "过来了")):
            _wb, _pb, _wa, pa = _get_bound()
            pa = _normalize_pos_tag(_wa, pa)
            if (
                (
                    pa in {"m", "mq", "q", "n", "nr", "ns", "nt", "nz", "vn"}
                    or (
                        pa in {"b", "i", "a"}
                        and not _is_completed_le_clause(cur, prot_terms=protected_terms)
                        and (
                            is_compound_np_body(nxt, prot_terms=protected_terms, prev_plain=cur)
                            or (pa in {"b", "i"} and _wa.startswith(_SPEC_MOD_HEAD_PREFIXES))
                        )
                    )
                )
                and not is_independent_svo_clause(nxt, prot_terms=protected_terms, prev_plain=cur)
            ):
                nxt_pairs = _pseg_cut_clean(nxt, prot_terms=protected_terms)
                first_np_len = len(nxt_pairs[0][0]) if nxt_pairs else 0
                first_np_pos = _normalize_pos_tag(nxt_pairs[0][0], nxt_pairs[0][1]) if nxt_pairs else ""
                is_num_head_np = first_np_pos in {"m", "mq", "q"}
                is_heavy_mod_np = (
                    len(nxt) >= 8
                    and ("的" in nxt[2:] or "嘅" in nxt[2:])
                    and not nxt.endswith(("的", "嘅"))
                    and (
                        (
                            is_num_head_np
                            and len(nxt_pairs) >= 2
                            and nxt_pairs[1][0] in {"的", "嘅"}
                            and ((len(cur) >= 3 and first_np_len >= 4) or (len(cur) >= 5 and first_np_len >= 3))
                        )
                        or (not is_num_head_np and len(cur) >= 3 and first_np_len >= 4)
                    )
                )
                if not is_heavy_mod_np:
                    breaks += 1
        elif cur[-1] in _ADAPTIVE_STRICT_TAIL:
            if cur[-1] == "呢":
                if _is_cantonese_ne_demo_head("呢" + nxt):
                    breaks += 1
            else:
                breaks += 1
        elif cur.endswith(_ADAPTIVE_MULTI_MOD_TAILS):
            _wb, _pb, _wa, pa = _get_bound()
            pa = _normalize_pos_tag(_wa, pa)
            if not (
                cur.endswith(("好多", "有啲"))
                and is_degree_complement_haoduo(cur, nxt, prot_terms=protected_terms)
            ):
                breaks += 1
        else:
            wb, pb, _wa, pa = _get_bound()
            pa = _normalize_pos_tag(_wa, pa)
            pb = _normalize_pos_tag(wb, pb)
            if (
                (pa == "k" and len(_wa) == 1 and pb not in {"", "x", "u", "uj", "ug", "ud", "ul", "uz", "uv", "c", "p"} and wb not in _DP_NO_TAIL_CHARS)
                or (pb == "j" and pa in {"g", "ng", "k"} and len(wb) == 1 and len(_wa) == 1)
                or (
                    len(_wa) == 1
                    and _wa in _SUFFIX_MOD_CHARS
                    and pa in {"k", "ng", "n", "q", "m"}
                    and pb in _SUFFIX_DERIV_STEM_POS
                    and len(wb) >= 2
                    and wb not in _DP_NO_TAIL_CHARS
                )
            ):
                breaks += 1
            elif cur.endswith(_ADAPTIVE_NEG_MOD_TAILS) and pa in {"v", "p", "a", "n", "vn"}:
                breaks += 1
            elif len(wb) == 1 and pb in {"d", "zg"} and cur[-1] in _ADAPTIVE_DEG_NEG_SINGLE_ADV and pa in {"v", "a", "ad", "an", "p", "d", "m", "n", "vn"}:
                breaks += 1
            elif len(wb) == 1 and pb == "v" and pa in {"n", "nr", "ns", "nt", "nz", "vn"}:
                breaks += 1
            elif (
                wb
                and (
                    (
                        pa in _MOD_NOUN_NEXT_POS
                        and (
                            pb == "b"
                            or (
                                pb == "i"
                                and (
                                    wb.startswith(_SPEC_MOD_HEAD_PREFIXES)
                                    or is_compound_np_body(wb + nxt, prot_terms=protected_terms)
                                )
                            )
                        )
                    )
                    or (
                        pb in {"nz", "b"}
                        and pa == "v"
                        and is_compound_np_body(wb + re.split(r"[，。！？、；：,;:!?]", nxt, 1)[0], prot_terms=protected_terms)
                    )
                )
            ):
                breaks += 1
            elif pa in _MOD_NOUN_NEXT_POS and wb:
                cur_ps = _pseg_cut_clean(cur, prot_terms=protected_terms)
                if len(cur_ps) >= 2:
                    prev_w, prev_f = cur_ps[-2]
                    prev_fn = _normalize_pos_tag(prev_w, prev_f)
                    if (
                        (pb == "a" and prev_fn in {"v", "vn", "ul", "zg", "d", "m", "mq", "q", "uj", "x"} and (prev_fn != "x" or prev_w == "嘅"))
                        or (prev_w in {"的", "嘅"} and (pb in {"a", "b"} or len(wb) == 1 or wb.endswith(("式", "型", "级", "类", "款", "版"))))
                    ):
                        breaks += 1
        joint_b = cur[-min(4, len(cur)):] + nxt[:min(4, len(nxt))]
        cut_b = min(4, len(cur))
        pos_b = 0
        for tk_b in tokenize_semantic_words(joint_b, terms=protected_terms):
            nxt_b = pos_b + len(tk_b)
            if (
                len(tk_b) >= 2
                and pos_b < cut_b < nxt_b
                and tk_b[0] not in _DP_NO_LEAD_CHARS
                and tk_b[-1] not in _DP_NO_LEAD_CHARS
            ):
                if not (
                    len(tk_b) == 4
                    and pos_b + 2 == cut_b
                    and len(cur) >= 8
                    and cur[-3] in {"的", "嘅"}
                    and bool(re.search(
                        r"(?:[零〇一二两三四五六七八九十百千万亿廿\d%％平间套家斤克元米层项吨箱台]{3,}|[零〇一二两三四五六七八九十百千万亿廿]+分之[零〇一二两三四五六七八九十百千万亿两廿\d]+)[的嘅]$",
                        cur[:-2],
                    ))
                ):
                    breaks += 1
                    break
            pos_b = nxt_b
    return breaks


def _has_genitive_or_tail_break(
    phrases: List[str],
    protected_terms: Optional[List[str]] = None,
) -> bool:
    return _count_genitive_or_tail_breaks(phrases, protected_terms=protected_terms) > 0


def _split_bridge_phrases_adaptive(
    text: str,
    target_min: int = 3,
    target_max: int = 8,
    protected_terms: Optional[List[str]] = None,
    font_path: Optional[str] = None,
    word_gaps: Optional[Dict[int, float]] = None,
) -> List[str]:
    max_prot_in_text = max((len(t) for t in (protected_terms or []) if len(t) >= 2 and t in text), default=0)
    eff_max = max(target_max, max_prot_in_text) if max_prot_in_text > target_max else target_max
    c69 = split_clean_phrases(
        text,
        target_min=target_min,
        target_max=eff_max,
        protected_terms=protected_terms,
        max_width_px=640,
        font_path=font_path,
        font_size=69,
        word_gaps=word_gaps,
        strict_width=True,
    )
    if not c69:
        return []
    def _orphan_cnt(chs: List[str]) -> int:
        return sum(1 for ch in chs if len(re.sub(r"[\s，。！？、；：,.!?;:]+", "", ch)) <= 3)

    def _has_collapsed_long(chs: List[str]) -> bool:
        cap = max(12, max_prot_in_text + 2)
        for ch in chs:
            comp = re.sub(r"[\s，。！？、；：,.!?;:]+", "", ch)
            ascii_n = sum(1 for c in comp if c.isascii() and not c.isspace())
            cjk_n = len(comp) - ascii_n
            eff_n = (cjk_n + (ascii_n * 55 + 99) // 100) if ascii_n >= 4 else len(comp)
            if eff_n > cap:
                return True
        return False

    b69 = _count_genitive_or_tail_breaks(c69, protected_terms=protected_terms)
    o69 = _orphan_cnt(c69)
    bad69 = _has_collapsed_long(c69)
    if b69 == 0 and o69 == 0 and not bad69:
        return c69
    c60 = split_clean_phrases(
        text,
        target_min=target_min,
        target_max=max(eff_max, 10),
        protected_terms=protected_terms,
        max_width_px=640,
        font_path=font_path,
        font_size=60,
        word_gaps=word_gaps,
        strict_width=True,
    )
    if c60 and not _has_collapsed_long(c60):
        b60 = _count_genitive_or_tail_breaks(c60, protected_terms=protected_terms)
        o60 = _orphan_cnt(c60)
        if (bad69 or b60 < b69 or (b60 == b69 and o60 < o69)) and all(_measure_text_px(ch, font_path, 60)[0] <= 640 for ch in c60):
            return c60
    if bad69 and max_prot_in_text >= 9:
        prot_px_60 = max(
            (_measure_text_px(t, font_path, 60)[0] for t in (protected_terms or []) if len(t) >= 2 and t in text),
            default=0,
        )
        c_wide = split_clean_phrases(
            text,
            target_min=target_min,
            target_max=max(eff_max, 10),
            protected_terms=protected_terms,
            max_width_px=max(640, prot_px_60 + 16, max_prot_in_text * 66 + 24),
            font_path=font_path,
            font_size=60,
            word_gaps=word_gaps,
            strict_width=True,
        )
        if c_wide and not _has_collapsed_long(c_wide):
            return c_wide
    return c69


def _bridge_units_for_text(text: str, font_path: Optional[str]) -> float:
    if _measure_text_px(text, font_path, 69)[0] <= 640:
        return _UNIFIED_L1_U
    for u in (11.0, 10.5, 10.0):
        fs = int(round(u * 6))
        if _measure_text_px(text, font_path, fs)[0] <= 640:
            return u
    return 10.0


# ── 4. 修辞意群识别与 `style_02` 六大舞台原语编排 ─────────────────────────────

_PAIN_CUES = ("难", "傻", "坑", "贵", "错", "失败", "问题", "痛", "烦", "三分之一", "束手无策", "为什么", "点解")
_CTA_CUES = ("评论区", "联系", "私信", "关注我", "关注下", "底部留言", "底下评论", "告诉我", "讲下", "找我", "推荐给你")
_CONTRAST_PAIRS = (
    (r"朝早|早上|白天", r"晚上|夜晚|晚市"),
    (r"之前|原来|以前|过去", r"现在|如今|依家|之后"),
    (r"线上", r"线下"),
    (r"表面", r"其实|背后"),
)
# 只匹配真正的计量/比例/面积/倍数/年份数字，不误抓「一个」「一次」「一间」「一种」「一起」等泛指量词
_NUM_UNIT_RE = re.compile(
    r"(?:"
    r"[零〇一二两三四五六七八九十百千万亿廿]+分之(?:[十百千万亿]?[一二三四五六七八九十零〇]+|两(?![百千万亿]))(?:点[零〇一二两三四五六七八九十]+)?"
    rf"|(?<![A-Za-z0-9.])[0-9]+(?:,[0-9]{{3}})*(?:[.:][0-9]+)?(?:{_ASCII_UNIT_PAT}|[A-Za-z][A-Za-z0-9]*|\s*(?:[多余来]?(?:[%％\+＋]|{_UNIT_TAIL_PAT})[+＋]?))?(?![A-Za-z0-9])"
    rf"|[零〇一二三四五六七八九十百千万亿两廿]{{2,}}(?:{_MULTI_UNIT_PAT}|[平平米万千百亿年倍成匹节颗粒片支根盏扇口头亩瓦伏安度克斤米吨升]+)"
    r"|[二三四五六七八九十两廿][万千百平倍成]+"
    rf"|[一二三四五六七八九十两廿][万千百]+(?:{_MULTI_UNIT_PAT}|[{_SINGLE_UNIT_CHARS_NO_PCT}]半?)?"
    r")"
)
_ORDINAL_SENT_RE = re.compile(r"^第[一二三四五六七八九十0-9]+[条点步个]?$")
_ENGLISH_UNIT_TOKEN_RE = re.compile(r"[A-Za-z0-9]+|[%％\+＋]")
_EXTRA_COHESIVE = [
    "𠮶种", "嗰种", "这种", "那种", "呢种",
    "呢个", "𠮶个", "嗰个", "这个", "那个",
    "呢间", "𠮶间", "嗰间", "一间", "一个", "一家", "一套", "一份", "一种", "有种", "几个",
]


def _split_into_n_at_word_bounds(
    text: str,
    n: int,
    protected_terms: Optional[List[str]] = None,
) -> List[str]:
    """沿语义词界与完整数字/英文跨度把短句分为 n 块，严防腰斩英文单位/品牌或残留首尾空格与挂尾虚词。"""
    clean = clean_display_text(text)
    L = len(clean)
    if n <= 1 or L < n * 2:
        return [clean] if clean else []
    toks = tokenize_semantic_words(clean, terms=protected_terms)
    num_spans = list(iter_num_unit_spans(clean))
    num_spans.extend(m.span() for m in _ALNUM_MIXED_TOKEN_RE.finditer(clean))
    num_spans.extend(m.span() for m in _HYPHEN_ASCII_TOKEN_RE.finditer(clean))
    _no_tail_bound = frozenset("的嘅地得之了咗着过把被从向给俾在喺和与及跟或这那呢嗰𠮶某每各哪")
    _dir_allow_bound = ("出来了", "起来了", "下去了", "回来了", "过去了", "进来了", "过来了")
    _brand_space_num_re = re.compile(
        r"(?:iOS|Android|Windows|macOS|GPT|Claude|Gemini|Pro|Max|Ultra|Plus|Type|USB|Wi-Fi|WiFi)$",
        re.I,
    )
    bounds: List[int] = []
    pos = 0
    for tk in toks[:-1]:
        pos += len(tk)
        if not (2 <= pos <= L - 2):
            continue
        if any(s < pos < e for s, e in num_spans):
            continue
        if clean[pos - 1].isspace():
            continue
        left_s = clean[:pos].rstrip()
        right_s = clean[pos:].lstrip()
        if len(left_s) < 2 or len(right_s) < 2:
            continue
        if right_s[0] in _DP_NO_LEAD_CHARS and not (right_s[0] == "呢" and _is_cantonese_ne_demo_head(right_s)):
            continue
        if left_s[-1] in _no_tail_bound and not left_s.endswith(_dir_allow_bound):
            continue
        if left_s.endswith(_ADAPTIVE_MULTI_MOD_TAILS) and not (
            left_s.endswith(("好多", "有啲"))
            and is_degree_complement_haoduo(left_s, right_s, prot_terms=protected_terms)
        ):
            continue
        if clean[pos].isspace() and _brand_space_num_re.search(left_s) and re.match(r"^[0-9]+", right_s):
            continue
        bounds.append(pos)
    if n == 2:
        if bounds:
            b = min(bounds, key=lambda x: abs(x - L / 2.0))
            return [p for p in (clean[:b].strip(), clean[b:].strip()) if p]
        return [clean]
    if n == 3:
        pairs = [(b1, b2) for i, b1 in enumerate(bounds) for b2 in bounds[i + 1 :] if len(clean[b1:b2].strip()) >= 2]
        if pairs:
            b1, b2 = min(pairs, key=lambda p: abs(p[0] - L / 3.0) + abs(p[1] - 2.0 * L / 3.0))
            return [p for p in (clean[:b1].strip(), clean[b1:b2].strip(), clean[b2:].strip()) if p]
        if bounds:
            b = min(bounds, key=lambda x: abs(x - L / 2.0))
            return [p for p in (clean[:b].strip(), clean[b:].strip()) if p]
        return [clean]
    return [clean]


def _make_item(
    *,
    item_id: str,
    group_id: str,
    text: str,
    start_sec: float,
    end_sec: float,
    fps: int,
    variant: str,
    x_norm: float,
    y_norm: float,
    font_size_units: float,
    anim_key: Optional[str] = None,
    z_layer: str = "foreground",
    track_lane: int = 0,
    dim_after_sec: Optional[float] = None,
    dim_alpha: float = 1.0,
    inline_parts: Optional[List[Dict[str, Any]]] = None,
    sfx_role_override: Optional[str] = None,
    trigger: str = "kinetic_stage",
) -> Dict[str, Any]:
    cx, cy = norm_to_canvas_px(x_norm, y_norm)
    font_px = max(28, int(round(float(font_size_units) * 6.0)))
    anim_meta = ANIM_SFX_BINDING_TABLE.get(anim_key or "") if anim_key else None
    sfx_role = sfx_role_override if sfx_role_override is not None else (anim_meta.get("sfx_role") if anim_meta else None)
    if sfx_role in BANNED_SFX_ROLES:
        sfx_role = None

    fg_hex = "#FFFFFF"
    bg_hex = None
    bg_alpha = 0.0
    stroke_hex = "#000000"
    if variant == "hero_lime":
        fg_hex = "#BCFF00"
    elif variant == "red_pill":
        fg_hex = "#B71C1C"
        bg_hex = "#FFFFFF"
        bg_alpha = 1.0
        stroke_hex = ""
    elif variant == "red_slam":
        fg_hex = "#B71C1C"
        stroke_hex = "#FFFFFF"
    elif variant == "bridge_bar":
        fg_hex = "#FFFFFF"
        bg_hex = "#000000"
        bg_alpha = 0.70
        stroke_hex = ""
    elif variant == "danmaku_line":
        fg_hex = "#FFFFFF"
        stroke_hex = "#000000"

    sf = int(round(start_sec * fps))
    ef = max(sf + 1, int(round(end_sec * fps)))
    item: Dict[str, Any] = {
        "id": item_id,
        "group_id": group_id,
        "kind": "kinetic_stage_item",
        "variant": variant,
        "text_or_asset": text,
        "inline_parts": inline_parts,
        "start_sec": round(start_sec, 4),
        "end_sec": round(end_sec, 4),
        "start_frame": sf,
        "end_frame": ef,
        "read_hold_sec": round(max(0.05, end_sec - start_sec), 3),
        "x_norm": round(float(x_norm), 4),
        "y_norm": round(float(y_norm), 4),
        "center_px": [cx, cy],
        "font_size_units": round(float(font_size_units), 1),
        "font_size_px": font_px,
        "fg_hex": fg_hex,
        "bg_hex": bg_hex,
        "bg_alpha": bg_alpha,
        "stroke_hex": stroke_hex,
        "z_layer": z_layer,
        "track_lane": track_lane,
        "jianying_anim_key": anim_key or "",
        "jianying_anim_name": (anim_meta or {}).get("jianying_anim_name", ""),
        "anim_primitive": (anim_meta or {}).get("anim_primitive", ""),
        "motion_mode": (anim_meta or {}).get("motion_mode", ""),
        "direction": (anim_meta or {}).get("direction", "up"),
        "anim_duration_ms": int((anim_meta or {}).get("anim_duration_ms") or 0),
        "sfx_role": sfx_role,
        "paired_jianying_sfx": (anim_meta or {}).get("jianying_sfx_name", ""),
        "dim_after_sec": round(float(dim_after_sec), 3) if dim_after_sec is not None else None,
        "dim_alpha": round(float(dim_alpha), 3),
        "aligned_sfx_or_accent_frame": sf,
        "alignment_error_frames": 0,
        "trigger": trigger,
    }
    return item


def _extract_parallel_list(text: str) -> Optional[Tuple[str, List[str], str]]:
    """
    通用并列清单检测器：
    - 编号型：「1.xxx 2.xxx 3.xxx」或「第一...第二...第三...」
    - 列举总起型：「四大XX/三个XX/分别是 + 多个短词 + 同埋/以及/还有 + 末项」
    返回 (lead_phrase, list_items[3..4], tail_phrase) 或 None。
    """
    clean = clean_display_text(text)
    if not clean:
        return None
    # 模式 A：同埋 / 以及 / 还有 串联 3~4 项短词，如「我哋嘅四大天王虾饺排骨凤爪同埋叉烧包」
    m_num_group = re.search(
        r"(三[件个点步项大法宝]|(?:[一二三四五六七八九十两]大(?:\S{2}))|主要有|包括|分为)",
        clean,
    )
    if m_num_group:
        lead = clean[: m_num_group.end()]
        rest = clean[m_num_group.end() :]
        # 用「同埋 / 以及 / 还有 / 跟 / 和 / 、」切开后，把超 3 字的再拆为 2~3 字并列词
        parts = [p for p in re.split(r"(?:同埋|以及|还有|加埋|、|和|跟)", rest) if p]
        items: List[str] = []
        for p in parts:
            if len(p) <= 4:
                items.append(p)
            else:
                # 若总长 4/6/8 字，优先按 2 字均分（如「虾饺排骨凤爪」->「虾饺」「排骨」「凤爪」）
                pos = 0
                while pos < len(p):
                    rem = len(p) - pos
                    step = 3 if (rem == 3 or (rem == 5 and pos == 2)) else (2 if rem >= 2 else rem)
                    items.append(p[pos : pos + step])
                    pos += step
        if 3 <= len(items) <= 5:
            return lead, items[:4], ""

    # 模式 B：1. / 2. / 3. 或 第一 / 第二 / 第三
    enum_parts = re.split(r"(?=(?:[1-9][\.、]|[一二三四]、|第[一二三四]))", text)
    enum_parts = [clean_display_text(p) for p in enum_parts if clean_display_text(p)]
    if len(enum_parts) >= 3:
        first_idx = next((i for i, p in enumerate(enum_parts) if re.match(r"^(?:[1-9]|第[一二三]|一)", p)), 0)
        lead = "".join(enum_parts[:first_idx])
        return lead, enum_parts[first_idx : first_idx + 4], ""
    return None


def _group_sentences(sentences: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    按呼吸停顿与视觉节奏将 ASR 句子合并为自然的「舞台意群 Beat Group」：
    - 避免把 0.35s / 0.40s 的极短残句独自切块导致画面闪断；
    - 控制每组在 1.5s ~ 6.0s 之间。
    """
    groups: List[Dict[str, Any]] = []
    cur_sents: List[Dict[str, Any]] = []

    def _flush() -> None:
        nonlocal cur_sents
        if not cur_sents:
            return
        st = float(cur_sents[0].get("start_sec") or 0.0)
        en = float(cur_sents[-1].get("end_sec") or st + 0.5)
        txt = "，".join(clean_display_text(str(s.get("text") or "")) for s in cur_sents if clean_display_text(str(s.get("text") or "")))
        tl: List[Tuple[str, float, float]] = []
        for s in cur_sents:
            tl.extend(_extract_char_timeline(s))
        g_sids: List[Any] = []
        for s in cur_sents:
            for sid_x in (s.get("merged_sentence_ids") or ([s.get("id")] if s.get("id") else [])):
                if sid_x and sid_x not in g_sids:
                    g_sids.append(sid_x)
        groups.append({
            "index": len(groups),
            "sentences": list(cur_sents),
            "sentence_ids": g_sids,
            "start_sec": round(st, 4),
            "end_sec": round(en, 4),
            "duration_sec": round(max(0.1, en - st), 4),
            "text": txt,
            "clean_text": clean_display_text(txt),
            "char_timeline": tl,
        })
        cur_sents = []

    for idx_s, s in enumerate(sentences):
        s_dur = max(0.1, float(s.get("end_sec") or 0.0) - float(s.get("start_sec") or 0.0))
        s_txt = clean_display_text(str(s.get("text") or ""))
        if not cur_sents:
            cur_sents.append(s)
            if idx_s == 0 and s_dur >= 0.85 and len(s_txt) >= 4:
                _flush()
            continue

        cur_dur = float(s.get("end_sec") or 0.0) - float(cur_sents[0].get("start_sec") or 0.0)
        prev_s = cur_sents[-1]
        gap = float(s.get("start_sec") or 0.0) - float(prev_s.get("end_sec") or 0.0)
        prev_txt = clean_display_text(str(prev_s.get("text") or ""))
        prev_dur = max(0.1, float(prev_s.get("end_sec") or 0.0) - float(prev_s.get("start_sec") or 0.0))
        cur_is_short = (s_dur < 1.25 or len(s_txt) <= 6)
        prev_is_short = (prev_dur < 1.25 or len(prev_txt) <= 6)
        prev_full_len = sum(len(clean_display_text(str(x.get("text") or ""))) for x in cur_sents)
        # 相邻残句合并；序数条陈词（第一/第二/第三）绝不吞进前一句结尾，仅允许与其后的正文引导合成新组
        should_merge = (
            gap <= 0.35
            and cur_dur <= 6.2
            and not (prev_full_len >= 14 and prev_dur >= 2.4)
            and (cur_is_short or prev_is_short)
            and not _ORDINAL_SENT_RE.match(s_txt)
            and not any(k in prev_txt for k in _CTA_CUES)
        )
        if not should_merge and _ORDINAL_SENT_RE.match(prev_txt) and gap <= 0.35 and cur_dur <= 6.2:
            should_merge = True
        # 特殊合并：清单紧跟评价句（例如 S12 清单 + S13 紧接好评），形成「弹出→压暗→总结叠前景」闭环
        prev_first = str(cur_sents[0].get("text") or "")
        if not should_merge and len(cur_sents) == 1 and _extract_parallel_list(prev_first) and s_dur <= 5.0 and cur_dur <= 10.5 and not _ORDINAL_SENT_RE.match(s_txt):
            should_merge = True
        # 特殊合并：痛点质疑前半句（如“好多人觉得好傻...”）+ 后半句核心数字痛点（“厨房花两百平”），绝不吞掉结尾 CTA 引导句
        if not should_merge and any(p in prev_txt for p in _PAIN_CUES) and gap <= 0.35 and cur_dur <= 7.2 and not _ORDINAL_SENT_RE.match(s_txt) and not any(k in s_txt for k in _CTA_CUES):
            should_merge = True

        if should_merge:
            cur_sents.append(s)
        else:
            _flush()
            cur_sents.append(s)
    _flush()
    return groups


def _load_stage_protected_terms(
    audio_events: Dict[str, Any],
    pipeline_dir: Optional[Path] = None,
    extra_terms: Optional[List[str]] = None,
) -> List[str]:
    """对齐 director_plan._load_pipeline_protected_terms：兼容 terms.txt 与 00_raw_intel.json（含 pain_keywords 与 dict entities，2..12 字）。"""
    terms: List[str] = []
    seen: set[str] = set()

    def _push(val: Any) -> None:
        raw = str(
            val.get("name") or val.get("text") or val.get("term") or ""
            if isinstance(val, dict) else (val or "")
        ).strip()
        if "=>" in raw:
            raw = raw.split("=>", 1)[1].strip()
        if 2 <= len(raw) <= 12 and raw not in seen:
            seen.add(raw)
            terms.append(raw)

    for t in (audio_events.get("protected_terms") or []):
        _push(t)
    if pipeline_dir is not None:
        p_dir = Path(pipeline_dir)
        tf_p = _terms_file_for(p_dir)
        if tf_p is not None and tf_p.is_file():
            for t in load_protected_terms(tf_p):
                _push(t)
        intel_p = p_dir / "00_raw_intel.json"
        if intel_p.is_file():
            intel_d = _read_optional_json(intel_p) or {}
            for k_t in ("pain_keywords", "terms", "protected_terms", "entities"):
                for v_t in (intel_d.get(k_t) or []):
                    _push(v_t)
    for t in (extra_terms or []):
        _push(t)
    terms.sort(key=len, reverse=True)
    return terms


def _extract_anchor_phrase(
    clean_txt: str,
    max_len: int = 6,
    protected_terms: Optional[List[str]] = None,
) -> str:
    """提炼痛点/数字/总结句的短锚点词（绝不截断半个词或保护词，也不砸出孤立干瘪的二字「问题」）。"""
    prot_active = [t for t in (protected_terms or []) if len(t) >= 2 and t in clean_txt]

    def _splits_prot(cand: str) -> bool:
        for tm in prot_active:
            pos = 0
            while True:
                ta = clean_txt.find(tm, pos)
                if ta < 0:
                    break
                tb = ta + len(tm)
                ca = clean_txt.find(cand)
                while ca >= 0:
                    cb = ca + len(cand)
                    if ca < tb and cb > ta and (ca > ta or cb < tb):
                        return True
                    ca = clean_txt.find(cand, ca + 1)
                pos = ta + 1
        return False

    for pat in (
        r"(?:好傻|难做|陷阱|误区|三分之一[^，。]{0,3}|[0-9一二三四五六七八九十百千万两]+平(?:[的嘅]厨房)?|[0-9一二三四五六七八九十百千万两]+万)",
        r"(?:不是|没有)[^，。]{1,3}问题|[^，。]{1,2}[的嘅]?问题|问题",
    ):
        m = re.search(pat, clean_txt)
        if m and 2 <= len(m.group(0)) <= max_len + 2:
            cand_m = m.group(0)[: max_len + 1]
            if not _splits_prot(cand_m):
                return cand_m
    eff_max = max(max_len, max((len(t) for t in prot_active), default=0))
    parts = split_clean_phrases(clean_txt, target_min=2, target_max=eff_max, protected_terms=protected_terms)
    for pt in reversed(parts):
        if len(pt) <= max_len and not _splits_prot(pt):
            return pt
    for pt in reversed(parts):
        if not _splits_prot(pt):
            return pt
    return parts[-1] if parts else clean_txt[:max_len]


def _plan_legacy_stage(
    audio_events: Dict[str, Any],
    style_id: str = "style_02",
    pipeline_dir: Optional[Path] = None,
    fps: int = DEFAULT_FPS,
    broll_evidence_by_sent: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    核心对外 API：
    根据 03_audio_events 的词级口播时间轴，为 style_02 产出完整的：
    - groups[]: 每个意群使用的舞台原语、是否锁死 A-roll、是否开启自抠像、是否允许证据锚定 B-roll
    - items[]: 逐条动态文字/弹幕渲染指令（含坐标、字号、色彩、动效、0ms 音效角色、压暗时间）
    - stats: 动静比例、自抠像区间、锁定 A-roll 时段列表等
    """
    dna = get_style_dna(style_id) or STYLE_DNA_REGISTRY["style_02"]
    sents = audio_events.get("sentences") or []
    if not sents:
        return {"style_id": style_id, "enabled": True, "groups": [], "items": [], "stats": {}}
    sents = rebalance_asr_sentences(sents, max_chars=9) or sents

    protected_terms = _load_stage_protected_terms(audio_events, pipeline_dir, extra_terms=_EXTRA_COHESIVE)
    groups_raw = _group_sentences(sents)
    ev_map = broll_evidence_by_sent or {}

    _leg_fp = _style02_font_path()
    items: List[Dict[str, Any]] = []
    beat_groups: List[Dict[str, Any]] = []
    item_seq = 0
    kinetic_group_cnt = 0
    bridge_group_cnt = 0
    consecutive_bridge = 0

    def next_id() -> str:
        nonlocal item_seq
        item_seq += 1
        return f"ks_{item_seq:03d}"

    for gi, g in enumerate(groups_raw):
        gs, ge, gd = g["start_sec"], g["end_sec"], g["duration_sec"]
        g_txt = g["clean_text"]
        g_split_txt = g["text"]  # 含“，”句界分隔符，绝不跨句把末尾虚词粘到下句开头
        tl = g["char_timeline"]

        def _seg_legacy_sents(sents_in: List[Dict[str, Any]], t_min: int, t_max: int) -> List[Tuple[str, float, float]]:
            mapped_s: List[Tuple[str, float, float]] = []
            prev_end_s = gs
            for s_leg in sents_in:
                st_l_txt = str(s_leg.get("text") or "").strip()
                if not st_l_txt:
                    continue
                s_l_start = max(prev_end_s, float(s_leg.get("start_sec") or gs))
                s_l_end = max(s_l_start + 0.25, float(s_leg.get("end_sec") or ge))
                s_l_tl = [c for c in _extract_char_timeline(s_leg) if c[0].strip()]
                s_l_gaps = _compute_sentence_word_gaps(s_leg, st_l_txt)
                phs_l = _split_bridge_phrases_adaptive(
                    st_l_txt,
                    target_min=t_min,
                    target_max=t_max,
                    protected_terms=protected_terms,
                    font_path=_leg_fp,
                    word_gaps=s_l_gaps,
                ) or [clean_display_text(st_l_txt)]
                for b_txt, bs, be in map_chunks_to_times(phs_l, s_l_tl, s_l_start, s_l_end):
                    mapped_s.append((b_txt, bs, be))
                    prev_end_s = max(prev_end_s, be)
            return mapped_s
        is_first = (gi == 0)
        is_last = (gi == len(groups_raw) - 1)
        has_num = bool(_NUM_UNIT_RE.search(g_txt))
        has_pain = any(c in g_txt for c in _PAIN_CUES)
        plist = _extract_parallel_list(str(g["sentences"][0].get("text") or "")) or _extract_parallel_list(g["text"])
        contrast_hit = None
        for p_a, p_b in _CONTRAST_PAIRS:
            ma = re.search(p_a, g_txt)
            mb = re.search(p_b, g_txt)
            if ma and mb and ma.start() < mb.start():
                contrast_hit = (ma, mb)
                break
        is_cta = is_last or (gi >= len(groups_raw) - 2 and any(c in g_txt for c in _CTA_CUES))

        # 检查当前意群内是否有高质量 B-roll 证据（仅在非强修辞锁定的意群允许切实拍）
        ev_scores = [
            float((ev_map.get(sid) or {}).get("score") or 0.0)
            for sid in g["sentence_ids"]
        ]
        max_ev_score = max(ev_scores) if ev_scores else 0.0

        # ─ 修辞模式裁决 ─
        pattern = "plain_bridge"
        if is_first:
            pattern = "hero_intro_pop" if gd < 2.1 else "staged_3tier_build"
        elif gi == 1 and groups_raw[0]["duration_sec"] < 2.1:
            # 紧接短开场自我介绍后的第一句重信息陈述 → 做标志性「三层阶梯累积」
            pattern = "staged_3tier_build"
        elif is_cta:
            pattern = "inline_emphasis_cta"
        elif plist is not None:
            pattern = "list_build_gather_dim_overlay"
        elif has_pain and gd >= 2.8:
            pattern = "hero_anchor_plus_danmaku"
        elif contrast_hit is not None:
            pattern = "entity_stack_plus_top"
        elif has_num and (max_ev_score < 0.72 or consecutive_bridge >= 1):
            pattern = "num_anchor_dim_overlay"
        elif consecutive_bridge >= 2:
            # 动静防乏味兜底：连续 2 段纯呼吸过渡后，下一段升级为亮点高亮
            pattern = "num_anchor_dim_overlay" if has_num else "entity_stack_plus_top"
        else:
            pattern = "plain_bridge"

        g_items: List[Dict[str, Any]] = []
        a_roll_locked = (pattern != "plain_bridge")
        self_matting = (pattern == "hero_anchor_plus_danmaku")
        allow_broll = (pattern == "plain_bridge")

        # ── 原语 A：开场人名/短钩子 ──
        if pattern == "hero_intro_pop":
            name_m = re.search(r"((?:我系|我是)?)([\u4e00-\u9fff]{2,3})(?:我)?$", g_txt)
            if name_m and name_m.group(1):
                mapped = map_chunks_to_times([name_m.group(1), g_txt[len(name_m.group(1)):]], tl, gs, ge)
                for ci_m, (txt_m, ms, _me) in enumerate(mapped):
                    g_items.append(_make_item(
                        item_id=next_id(), group_id=f"grp_{gi:02d}", text=txt_m,
                        start_sec=ms, end_sec=ge, fps=fps,
                        variant="hero_white" if ci_m == 0 else "hero_lime",
                        x_norm=(-0.22 if ci_m == 0 else 0.14),
                        y_norm=(-0.38 if ci_m == 0 else -0.50),
                        font_size_units=(16.0 if ci_m == 0 else 22.0),
                        anim_key=("in:向右集合" if ci_m == 0 else "in:打字光标"),
                        track_lane=ci_m,
                    ))
            else:
                g_items.append(_make_item(
                    item_id=next_id(), group_id=f"grp_{gi:02d}", text=g_txt,
                    start_sec=gs, end_sec=ge, fps=fps,
                    variant="hero_lime", x_norm=0.0, y_norm=-0.42, font_size_units=20.0,
                    anim_key="in:向右集合", track_lane=0,
                ))

        # ── 原语 B：三层阶梯累积建构（还原 00.03..03.07） ──
        elif pattern == "staged_3tier_build":
            def _join_ph(ps_list: List[str]) -> str:
                acc_s = ""
                for ps_i in ps_list:
                    if not ps_i:
                        continue
                    if acc_s and acc_s[-1].isascii() and acc_s[-1].isalnum() and ps_i[0].isascii() and ps_i[0].isalnum():
                        acc_s += " " + ps_i
                    else:
                        acc_s += ps_i
                return acc_s

            _raw_sent_joined_3 = "，".join(
                str(s_3.get("text") or "").strip() for s_3 in (g.get("sentences") or []) if str(s_3.get("text") or "").strip()
            ) or g_split_txt
            _clause_split_3 = re.compile(r"(?:(?<!\d)[，；,;]|(?<=\d)[，；;]|(?<=\d),(?!\d{3}(?!\d))|[。！？!?])+")
            _raw_clauses_split = [clean_display_text(c) for c in _clause_split_3.split(_raw_sent_joined_3) if clean_display_text(c)]
            _raw_clauses_3: List[str] = []
            for _rc_i in _raw_clauses_split:
                if (
                    _raw_clauses_3
                    and _raw_clauses_3[-1].endswith(("好多", "有啲"))
                    and not is_degree_complement_haoduo(_raw_clauses_3[-1], _rc_i, prot_terms=protected_terms)
                ):
                    _raw_clauses_3[-1] = _join_ph([_raw_clauses_3[-1], _rc_i])
                else:
                    _raw_clauses_3.append(_rc_i)
            phrases = [p for p, _, _ in _seg_legacy_sents(g["sentences"], 3, 7)]
            if len(phrases) < 3:
                if len(_raw_clauses_3) >= 2 and min(len(c) for c in _raw_clauses_3) >= 4:
                    if len(_raw_clauses_3) >= 3:
                        phrases = list(_raw_clauses_3)
                    else:
                        c0, c1 = _raw_clauses_3[0], _raw_clauses_3[1]
                        if len(c0) >= len(c1):
                            sp = _split_into_n_at_word_bounds(c0, 2, protected_terms=protected_terms)
                            phrases = (sp if len(sp) == 2 else [c0]) + [c1]
                        else:
                            sp = _split_into_n_at_word_bounds(c1, 2, protected_terms=protected_terms)
                            phrases = [c0] + (sp if len(sp) == 2 else [c1])
                if len(phrases) < 3:
                    phrases = _split_into_n_at_word_bounds(g_txt, 3, protected_terms=protected_terms)
            if len(phrases) > 3:
                # 均并回 3 层，保证与原片一致的三行金字塔，同时严禁跨逗号/句界融合或生成序数挂尾
                _clause_ends_3: set[int] = set()
                _acc_ce = 0
                for _cl in _raw_clauses_3[:-1]:
                    _acc_ce += len(_cl)
                    _clause_ends_3.add(_acc_ce)
                work_ph = list(phrases)
                while len(work_ph) > 3:
                    _cum = 0
                    _best_idx = -1
                    _best_score: Optional[Tuple[int, int, int, int]] = None
                    for _pi in range(len(work_ph) - 1):
                        _cum += len(work_ph[_pi])
                        if _cum in _clause_ends_3:
                            continue
                        _cand_m = _join_ph([work_ph[_pi], work_ph[_pi + 1]])
                        _ord_pen = 1 if (len(_cand_m) >= 6 and re.search(r"第[一二三四五六七八九十两\d]+$", _cand_m)) else 0
                        _mid_pen = 1 if (_pi == 1 and len(work_ph) == 4 and len(_cand_m) >= 8) else 0
                        _sc = (_ord_pen, _mid_pen, len(_cand_m), _pi)
                        if _best_score is None or _sc < _best_score:
                            _best_score = _sc
                            _best_idx = _pi
                    if _best_idx >= 0:
                        _m_str = _join_ph([work_ph[_best_idx], work_ph[_best_idx + 1]])
                        work_ph = work_ph[:_best_idx] + [_m_str] + work_ph[_best_idx + 2 :]
                    else:
                        k1 = work_ph[0]
                        k2 = _join_ph(work_ph[1 : max(2, len(work_ph) - 1)])
                        if len(k2) > 9:
                            k2 = work_ph[1]
                            k3 = _join_ph(work_ph[2:])
                        else:
                            k3 = work_ph[-1]
                        work_ph = [k1, k2, k3]
                        break
                phrases = work_ph
            mapped = map_chunks_to_times(phrases, tl, gs, ge)
            tiers = [
                ("hero_white", -0.34, -0.28, 16.0, "in:向右集合"),
                ("hero_lime", 0.00, -0.41, 21.0, "in:打字光标"),
                ("red_pill", -0.04, -0.56, 14.5, "in:向下露出"),
            ]
            for ti, (txt_p, s_p, _e_p) in enumerate(mapped[:3]):
                var_t, xn_t, yn_t, sz_t, ak_t = tiers[ti]
                # 动态自适应横坐标避免过长文字越出安全带 [-0.72, 0.72]
                if len(txt_p) >= 7:
                    xn_t = 0.0
                g_items.append(_make_item(
                    item_id=next_id(), group_id=f"grp_{gi:02d}", text=txt_p,
                    start_sec=s_p, end_sec=ge, fps=fps,
                    variant=var_t, x_norm=xn_t, y_norm=yn_t, font_size_units=sz_t,
                    anim_key=ak_t, track_lane=ti,
                ))

        # ── 原语 C：反差痛点母题红大字 + 自抠像人后滚动弹幕（还原 11.00..16.17） ──
        elif pattern == "hero_anchor_plus_danmaku":
            anchor = _extract_anchor_phrase(g_txt, max_len=6, protected_terms=protected_terms)
            # 1) 前景红色重磅主题词（用缩小 II + 拳击砰砸出，贯穿整个意群）
            g_items.append(_make_item(
                item_id=next_id(), group_id=f"grp_{gi:02d}", text=anchor,
                start_sec=gs, end_sec=ge, fps=fps,
                variant="red_slam", x_norm=0.0, y_norm=-0.34, font_size_units=27.0,
                anim_key="in:缩小 II", z_layer="foreground", track_lane=0,
            ))
            # 2) 中下区第一小节先出1条小过桥，紧接着用自抠像把剩下痛点句全部分配到人后滚动弹幕（确保不缺漏台词）
            mapped = _seg_legacy_sents(g["sentences"], 3, 6)
            if mapped:
                first_txt, f_s, f_e = mapped[0]
                g_items.append(_make_item(
                    item_id=next_id(), group_id=f"grp_{gi:02d}", text=first_txt,
                    start_sec=max(gs + 0.30, f_s), end_sec=min(ge, max(gs + 1.35, f_e + 0.15)), fps=fps,
                    variant="bridge_bar", x_norm=0.0, y_norm=-0.52, font_size_units=_bridge_units_for_text(first_txt, _leg_fp),
                    anim_key=None, z_layer="foreground", track_lane=1,
                ))
            danmaku_lanes_y = [0.34, 0.20, 0.40, 0.14]
            danmaku_slots = mapped[1:] if len(mapped) > 1 else mapped
            # 仅合并结尾过碎块（合并后不超过 6 字），避免拼成超长条被人物或画边切半
            while len(danmaku_slots) > 4 and len(danmaku_slots[-2][0] + danmaku_slots[-1][0]) <= 6:
                t_a, sa, _ = danmaku_slots[-2]
                t_b, _, eb = danmaku_slots[-1]
                danmaku_slots[-2:] = [(t_a + t_b, sa, eb)]
            for di, (d_txt, ds, de) in enumerate(danmaku_slots):
                d_end = min(ge, max(ds + 1.15, de + 0.40))
                is_left = (di % 2 == 0)
                g_items.append(_make_item(
                    item_id=next_id(), group_id=f"grp_{gi:02d}", text=d_txt,
                    start_sec=ds, end_sec=d_end, fps=fps,
                    variant="danmaku_line", x_norm=(-0.24 if is_left else 0.24),
                    y_norm=danmaku_lanes_y[di % len(danmaku_lanes_y)],
                    font_size_units=14.0, anim_key=("in:向右露出" if is_left else "in:向左露出"),
                    z_layer="behind_person", track_lane=3 + (di % 3),
                ))

        # ── 原语 D：清单弹出 → 收拢居中 → 压暗留底叠前景白大字（还原 19.27..23.40） ──
        elif pattern == "list_build_gather_dim_overlay":
            assert plist is not None
            lead, l_items, _ = plist
            # 把意群按清单句与总结句切分时间点
            s_list = g["sentences"][0]
            list_end = float(s_list.get("end_sec") or (gs + gd * 0.6))
            sum_pre_chunks: List[Tuple[str, float, float]] = []
            if len(g["sentences"]) > 1:
                summary_sent = g["sentences"][1]
                sum_tl = _extract_char_timeline(summary_sent)
                sum_orig_s = float(summary_sent.get("start_sec") or list_end)
                sum_txt = clean_display_text(str(summary_sent.get("text") or ""))
                sum_phrase = _extract_anchor_phrase(sum_txt, max_len=7, protected_terms=protected_terms)
                if len(sum_txt) > len(sum_phrase) + 2:
                    idx_sp = sum_txt.find(sum_phrase)
                    before_body = clean_display_text(sum_txt[:idx_sp]) if idx_sp > 0 else ""
                    after_body = clean_display_text(sum_txt[idx_sp + len(sum_phrase) :]) if idx_sp >= 0 else ""
                    if after_body and len(after_body) <= 2 and len(sum_phrase) + len(after_body) <= 9:
                        sum_phrase = sum_phrase + after_body
                        after_body = ""
                    pre_list = (
                        _split_bridge_phrases_adaptive(
                            before_body,
                            target_min=3,
                            target_max=8,
                            protected_terms=protected_terms,
                            font_path=_leg_fp,
                            word_gaps=_compute_sentence_word_gaps(summary_sent, before_body),
                        )
                        if len(before_body) >= 2
                        else ([before_body] if before_body else [])
                    )
                    post_list = (
                        _split_bridge_phrases_adaptive(
                            after_body,
                            target_min=3,
                            target_max=8,
                            protected_terms=protected_terms,
                            font_path=_leg_fp,
                            word_gaps=_compute_sentence_word_gaps(summary_sent, after_body),
                        )
                        if len(after_body) >= 2
                        else ([after_body] if after_body else [])
                    )
                    mapped_sum = map_chunks_to_times([*pre_list, sum_phrase, *post_list], sum_tl, sum_orig_s, ge)
                    if mapped_sum:
                        sum_pre_chunks = mapped_sum[: len(pre_list)]
                        sum_s = mapped_sum[min(len(pre_list), len(mapped_sum) - 1)][1]
                    else:
                        sum_s = sum_orig_s
                else:
                    sum_s = sum_orig_s
            else:
                sum_s = max(gs + gd * 0.68, ge - 1.1)
                sum_phrase = lead or clean_display_text(g_txt[-6:])
                list_end = sum_s

            list_tl = _extract_char_timeline(s_list)
            all_list_chunks = ([lead] if lead else []) + list(l_items)
            all_list_hits = map_chunks_to_times(all_list_chunks, list_tl, gs, list_end)
            if lead and len(all_list_hits) == len(all_list_chunks):
                lead_hit = all_list_hits[0]
                first_item_hits = all_list_hits[1:]
                lead_end_t = min(first_item_hits[0][1], max(gs + 0.9, lead_hit[2])) if first_item_hits else lead_hit[2]
                g_items.append(_make_item(
                    item_id=next_id(), group_id=f"grp_{gi:02d}", text=lead,
                    start_sec=gs, end_sec=lead_end_t, fps=fps,
                    variant="hero_white", x_norm=0.0, y_norm=-0.38, font_size_units=19.0,
                    anim_key="in:雪光模糊", track_lane=0,
                ))
            else:
                first_item_hits = all_list_hits

            numbered_items = [f"{k+1}.{it}" for k, it in enumerate(l_items)]
            # 清单阶段 2 最迟在 list_end（或总结前景大字出现前 0.04s）必须干脆退场，绝不能跨到下一句砸字幕
            list_hard_end = min(ge, list_end)
            gather_t = min(list_hard_end - 0.35, max(gs + 0.65, (first_item_hits[-1][1] + 0.30) if first_item_hits else (list_hard_end - 0.55)))
            sum_start = max(gather_t + 0.32, min(ge - 0.55, sum_s))
            if sum_start >= ge - 0.3:
                sum_start = max(gs + gd * 0.65, ge - 1.0)
                gather_t = min(list_hard_end - 0.35, max(gs + gd * 0.42, sum_start - 0.45))

            scatter_pos = [(-0.34, -0.25), (0.34, -0.25), (-0.34, -0.47), (0.34, -0.47)]
            gather_ys = [-0.25, -0.38, -0.51, -0.64]
            item_anims = ["in:向右集合", "in:随机上升", "in:渐显", "in:向下露出"]

            for ki, ((_raw_it, ks, _ke), num_txt) in enumerate(zip(first_item_hits, numbered_items)):
                xn, yn = scatter_pos[ki % len(scatter_pos)]
                # 阶段 1：随口播错落弹出并累积驻留到 gather_t
                if gather_t > ks + 0.12:
                    g_items.append(_make_item(
                        item_id=next_id(), group_id=f"grp_{gi:02d}", text=num_txt,
                        start_sec=ks, end_sec=gather_t, fps=fps,
                        variant="hero_lime", x_norm=xn, y_norm=yn, font_size_units=15.0,
                        anim_key=item_anims[ki % len(item_anims)], track_lane=1 + ki,
                    ))
                    g_end_s = gather_t
                else:
                    g_end_s = ks
                # 阶段 2：全部收拢居中竖排；严格在 sum_start 前清场退场（带 fade_out），彻底消灭跨幕叠字
                phase2_end = min(ge, list_hard_end, max(g_end_s + 0.12, sum_start - 0.04))
                if phase2_end > g_end_s + 0.04:
                    dim_rel = max(0.04, (phase2_end - g_end_s) * 0.45)
                    it_p2 = _make_item(
                        item_id=next_id(), group_id=f"grp_{gi:02d}", text=num_txt,
                        start_sec=g_end_s, end_sec=phase2_end, fps=fps,
                        variant="hero_lime", x_norm=0.0, y_norm=gather_ys[ki % len(gather_ys)],
                        font_size_units=15.0, anim_key=None, track_lane=1 + ki,
                        dim_after_sec=dim_rel, dim_alpha=0.14,
                    )
                    it_p2["out_anim"] = "fade_out"
                    g_items.append(it_p2)

            # 阶段 3：若有前置小句（如“招牌特色炒面”）在居中上方过桥展示，随后压暗并砸出前景大字
            for pi_s, (p_stxt, p_ss, _p_se) in enumerate(sum_pre_chunks):
                nxt_ps = sum_pre_chunks[pi_s + 1][1] if pi_s + 1 < len(sum_pre_chunks) else sum_start
                g_items.append(_make_item(
                    item_id=next_id(), group_id=f"grp_{gi:02d}", text=p_stxt,
                    start_sec=max(gather_t, p_ss), end_sec=max(max(gather_t, p_ss) + 0.25, nxt_ps), fps=fps,
                    variant="bridge_bar", x_norm=0.0, y_norm=-0.73, font_size_units=_bridge_units_for_text(p_stxt, _leg_fp),
                    anim_key=None, track_lane=5, trigger="bridge",
                ))
            g_items.append(_make_item(
                item_id=next_id(), group_id=f"grp_{gi:02d}", text=sum_phrase,
                start_sec=sum_start, end_sec=ge, fps=fps,
                variant="hero_white", x_norm=0.0, y_norm=-0.43, font_size_units=23.5,
                anim_key="in:呐喊声波", track_lane=5,
            ))

        # ── 原语 E：双实体对比堆叠 + 顶部高光（确保每条文字不超过 9 字，多出词完整拆接为前置过渡） ──
        elif pattern == "entity_stack_plus_top":
            if contrast_hit is not None:
                ma, mb = contrast_hit
                part_pre = clean_display_text(g_txt[: ma.start()])
                part_a = clean_display_text(g_txt[ma.start() : mb.start()])
                part_b = clean_display_text(g_txt[mb.start() :])
                raw_parts = [
                    x for x in (part_pre, part_a, part_b) if len(x) >= 2
                ]
                blocks = []
                for rp in raw_parts:
                    if len(rp) > 8 or _measure_text_px(rp, _leg_fp, 69)[0] > 640:
                        blocks.extend(_split_bridge_phrases_adaptive(
                            rp, target_min=3, target_max=8, protected_terms=protected_terms, font_path=_leg_fp,
                        ))
                    else:
                        blocks.append(rp)
                if len(blocks) == 1 and len(blocks[0]) >= 4:
                    blocks = _split_into_n_at_word_bounds(blocks[0], 2, protected_terms=protected_terms)
                mapped = map_chunks_to_times(blocks, tl, gs, ge)
            else:
                mapped = _seg_legacy_sents(g["sentences"], 3, 8)
                if len(mapped) == 1 and len(mapped[0][0]) >= 4:
                    blocks = _split_into_n_at_word_bounds(mapped[0][0], 2, protected_terms=protected_terms)
                    mapped = map_chunks_to_times(blocks, tl, gs, ge)
            # 若超 3 块：前 (len-2) 块作清爽无截断前置过渡句，最后 2~3 块做左右/上下对比压轴！
            n_pre = max(0, len(mapped) - 3)
            pre_blocks = mapped[:n_pre]
            tail_blocks = mapped[n_pre:]
            stage_start = tail_blocks[0][1] if tail_blocks else gs
            for pi_b, (p_txt, ps, pe) in enumerate(pre_blocks):
                nxt_s = mapped[pi_b + 1][1] if pi_b + 1 < len(mapped) else stage_start
                g_items.append(_make_item(
                    item_id=next_id(), group_id=f"grp_{gi:02d}", text=p_txt,
                    start_sec=ps, end_sec=max(ps + 0.25, nxt_s), fps=fps,
                    variant="bridge_bar", x_norm=0.0, y_norm=-0.52, font_size_units=_bridge_units_for_text(p_txt, _leg_fp),
                    anim_key=None, track_lane=0, trigger="bridge",
                ))
            styles_seq = [
                ("hero_white", -0.28, -0.49, 17.5, "in:渐显"),
                ("hero_lime", 0.28, -0.49, 17.5, "in:开幕"),
                ("red_pill", 0.00, -0.33, 15.5, "in:向上露出"),
            ]
            if len(tail_blocks) == 2:
                styles_seq = [
                    ("hero_white", 0.0, -0.35, 19.5, "in:开幕"),
                    ("hero_lime", 0.0, -0.52, 21.0, "in:随机上升"),
                ]
            for bi, (b_txt, bs, _be) in enumerate(tail_blocks[:3]):
                var_b, xn_b, yn_b, sz_b, ak_b = styles_seq[bi]
                # 只要任意一行 >=6 字或同组三层文字较长，统一改用居中三层金字塔 (-0.27 / -0.42 / -0.57)，拉开 140px 绝对零重叠！
                if len(tail_blocks) >= 3 and any(len(tb[0]) >= 6 for tb in tail_blocks[:3]):
                    xn_b = 0.0
                    yn_b = (-0.27, -0.42, -0.57)[bi]
                elif len(b_txt) >= 6:
                    xn_b = 0.0
                if len(b_txt) >= 8:
                    sz_b = min(sz_b, round(152.0 / max(1, len(b_txt)), 1))
                g_items.append(_make_item(
                    item_id=next_id(), group_id=f"grp_{gi:02d}", text=b_txt,
                    start_sec=bs, end_sec=ge, fps=fps,
                    variant=var_b, x_norm=xn_b, y_norm=yn_b, font_size_units=sz_b,
                    anim_key=ak_b, track_lane=1 + bi,
                ))

        # ── 原语 F：数字锚点先滚入再压暗叠白结论（还原 24.97..26.30 `6000+` → `实体企业`，零字截断） ──
        elif pattern == "num_anchor_dim_overlay":
            _brand_num_spans = [
                m.span()
                for m in re.finditer(
                    r"(?<![A-Za-z0-9])[A-Za-z]+(?:-[A-Za-z0-9]+)*\s+[0-9]+(?![A-Za-z0-9])",
                    g_txt,
                )
            ]
            _brand_num_spans.extend(m.span() for m in _ALNUM_MIXED_TOKEN_RE.finditer(g_txt))
            _brand_num_spans.extend(m.span() for m in _HYPHEN_ASCII_TOKEN_RE.finditer(g_txt))
            for _pt_n in (protected_terms or ()):
                if len(_pt_n) >= 2 and _pt_n in g_txt:
                    _brand_num_spans.extend(m.span() for m in re.finditer(re.escape(_pt_n), g_txt))
            m_num = next(
                (
                    (ms, me)
                    for ms, me in iter_num_unit_spans(g_txt, pattern=_NUM_UNIT_RE)
                    if not any(bs <= ms < be for bs, be in _brand_num_spans)
                ),
                None,
            )
            if m_num:
                num_start, num_end = m_num
                num_str = g_txt[num_start:num_end]
                pre_str = clean_display_text(g_txt[:num_start])
                raw_post_str = clean_display_text(g_txt[num_end:])
                post_str = raw_post_str
                if (
                    post_str
                    and post_str[0] in "的地得之了着过嘅"
                    and not post_str.startswith(("之内", "之外", "之间", "之上", "之下", "之中", "之后", "之前", "之一", "之类"))
                ):
                    if len(post_str) >= 3:
                        post_str = post_str[1:].strip()
                    else:
                        num_str = f"{num_str}{post_str[0]}"
                        post_str = post_str[1:].strip()

                def _split_leg_subspan(span_txt: str, start_offset_in_g: int) -> List[str]:
                    if not span_txt:
                        return []
                    if len(g["sentences"]) == 1:
                        s0 = g["sentences"][0]
                        s0_raw = str(s0.get("text") or "").strip()
                        s0_gaps = _compute_sentence_word_gaps(s0, span_txt) if clean_display_text(s0_raw) == span_txt else None
                        return _split_bridge_phrases_adaptive(
                            span_txt, target_min=2, target_max=8, protected_terms=protected_terms,
                            font_path=_leg_fp, word_gaps=s0_gaps,
                        )
                    pieces: List[str] = []
                    cursor_g = 0
                    span_end_in_g = start_offset_in_g + len(span_txt)
                    for s_leg in g["sentences"]:
                        s_clean = clean_display_text(str(s_leg.get("text") or ""))
                        s_len = len(s_clean)
                        ov_a = max(start_offset_in_g, cursor_g)
                        ov_b = min(span_end_in_g, cursor_g + s_len)
                        if ov_b > ov_a:
                            sub_t = g_txt[ov_a:ov_b]
                            if sub_t:
                                pieces.extend(_split_bridge_phrases_adaptive(
                                    sub_t, target_min=2, target_max=8, protected_terms=protected_terms,
                                    font_path=_leg_fp, word_gaps=_compute_sentence_word_gaps(s_leg, sub_t) if sub_t == s_clean else None,
                                ) or [sub_t])
                        cursor_g += s_len
                    return pieces or _split_bridge_phrases_adaptive(
                        span_txt, target_min=2, target_max=8, protected_terms=protected_terms, font_path=_leg_fp,
                    )

                pre_chunks = _split_leg_subspan(pre_str, 0) if pre_str else []
                post_off = num_end + max(0, len(raw_post_str) - len(post_str))
                post_chunks = _split_leg_subspan(post_str, post_off) if post_str else []
                sub_parts = [*pre_chunks, num_str, *post_chunks]
                mapped = map_chunks_to_times(sub_parts, tl, gs, ge)
                num_idx = len(pre_chunks)
                num_s = mapped[num_idx][1] if num_idx < len(mapped) else gs
                post_s = mapped[num_idx + 1][1] if num_idx + 1 < len(mapped) else min(ge - 0.35, num_s + 0.65)
                for pi_n, (p_txt, ps, _) in enumerate(mapped[:num_idx]):
                    nxt_s = mapped[pi_n + 1][1]
                    g_items.append(_make_item(
                        item_id=next_id(), group_id=f"grp_{gi:02d}", text=p_txt,
                        start_sec=ps, end_sec=max(ps + 0.25, nxt_s), fps=fps,
                        variant="bridge_bar", x_norm=0.0, y_norm=-0.52, font_size_units=_bridge_units_for_text(p_txt, _leg_fp),
                        anim_key=None, track_lane=0, trigger="bridge",
                    ))
                has_post = bool(post_chunks)
                dim_rel = max(0.08, post_s - num_s) if has_post else None
                g_items.append(_make_item(
                    item_id=next_id(), group_id=f"grp_{gi:02d}", text=num_str,
                    start_sec=num_s, end_sec=ge, fps=fps,
                    variant="hero_lime", x_norm=0.0, y_norm=-0.34, font_size_units=25.0,
                    anim_key="in:滚入", track_lane=1,
                    dim_after_sec=dim_rel, dim_alpha=0.38 if has_post else 1.0,
                ))
                for oi, (o_txt, os_t, _) in enumerate(mapped[num_idx + 1 :]):
                    o_end = mapped[num_idx + 1 + oi + 1][1] if (num_idx + 1 + oi + 1) < len(mapped) else ge
                    g_items.append(_make_item(
                        item_id=next_id(), group_id=f"grp_{gi:02d}", text=o_txt,
                        start_sec=os_t, end_sec=max(os_t + 0.25, o_end), fps=fps,
                        variant="hero_white", x_norm=0.0, y_norm=-0.59, font_size_units=19.0,
                        anim_key=("in:晕开" if oi == 0 else None), track_lane=2,
                    ))
            else:
                pattern = "plain_bridge"
                a_roll_locked = False
                allow_broll = True

        # ── 原语 G：尾句双色混排强调 + 呼吸互动 CTA（零字截断，长句自动保留完整） ──
        elif pattern == "inline_emphasis_cta":
            mapped = _seg_legacy_sents(g["sentences"], 4, 8)
            if len(mapped) < 2:
                base_txt = mapped[0][0] if mapped else g_txt
                if len(base_txt) >= 4:
                    phrases = _split_into_n_at_word_bounds(base_txt, 2, protected_terms=protected_terms)
                    mapped = map_chunks_to_times(phrases, tl, gs, ge)
            for ci_e, (e_txt, es, _) in enumerate(mapped):
                nxt_s = mapped[ci_e + 1][1] if ci_e + 1 < len(mapped) else ge
                if ci_e == len(mapped) - 1:
                    g_items.append(_make_item(
                        item_id=next_id(), group_id=f"grp_{gi:02d}", text=e_txt,
                        start_sec=es, end_sec=ge, fps=fps,
                        variant="bridge_bar", x_norm=0.0, y_norm=-0.52, font_size_units=_bridge_units_for_text(e_txt, _leg_fp),
                        anim_key=None, sfx_role_override=None, track_lane=1, trigger="cta",
                    ))
                elif ci_e == len(mapped) - 2 and len(e_txt) >= 4:
                    words_e = tokenize_semantic_words(e_txt, terms=protected_terms)
                    boundaries: List[int] = []
                    pos_acc = 0
                    for w_tok in words_e[:-1]:
                        pos_acc += len(w_tok)
                        if 2 <= pos_acc <= len(e_txt) - 2 and not e_txt[pos_acc].isspace() and not e_txt[pos_acc - 1].isspace():
                            boundaries.append(pos_acc)
                    if boundaries:
                        split_i = min(boundaries, key=lambda p: (abs(p - len(e_txt) / 2.0), -p))
                    else:
                        split_i = max(2, min(len(e_txt) - 2, len(e_txt) // 2))
                    sz_lo = round(min(16.0, 128.0 / len(e_txt)), 1) if len(e_txt) >= 8 else 16.0
                    sz_hi = round(min(22.0, 148.0 / len(e_txt)), 1) if len(e_txt) >= 8 else 22.0
                    g_items.append(_make_item(
                        item_id=next_id(), group_id=f"grp_{gi:02d}", text=e_txt,
                        start_sec=es, end_sec=max(es + 0.25, nxt_s), fps=fps,
                        variant="inline_rich", x_norm=0.0, y_norm=-0.50, font_size_units=min(17.0, sz_hi),
                        anim_key="in:激光雕刻", track_lane=0,
                        inline_parts=[
                            {"text": e_txt[:split_i], "color": "#FFFFFF", "size_units": sz_lo},
                            {"text": e_txt[split_i:], "color": "#BCFF00", "size_units": sz_hi},
                        ],
                    ))
                else:
                    g_items.append(_make_item(
                        item_id=next_id(), group_id=f"grp_{gi:02d}", text=e_txt,
                        start_sec=es, end_sec=max(es + 0.25, nxt_s), fps=fps,
                        variant="bridge_bar", x_norm=0.0, y_norm=-0.52, font_size_units=_bridge_units_for_text(e_txt, _leg_fp),
                        anim_key=None, track_lane=0, trigger="bridge",
                    ))

        # ── 原语 H：极简半透黑底呼吸过渡短句（bg=0.7，无动效、严格无音效） ──
        if pattern == "plain_bridge":
            mapped_all = _seg_legacy_sents(g["sentences"], 3, 8)
            for pi, (b_txt, bs, be) in enumerate(mapped_all):
                next_s = mapped_all[pi + 1][1] if pi + 1 < len(mapped_all) else ge
                g_items.append(_make_item(
                    item_id=next_id(), group_id=f"grp_{gi:02d}", text=b_txt,
                    start_sec=bs, end_sec=max(bs + 0.25, next_s), fps=fps,
                    variant="bridge_bar", x_norm=0.0, y_norm=-0.52, font_size_units=_bridge_units_for_text(b_txt, _leg_fp),
                    anim_key=None, track_lane=0, trigger="bridge",
                ))
            bridge_group_cnt += 1
            consecutive_bridge += 1
        else:
            kinetic_group_cnt += 1
            consecutive_bridge = 0

        items.extend(g_items)
        beat_groups.append({
            "group_id": f"grp_{gi:02d}",
            "sentence_ids": g["sentence_ids"],
            "start_sec": gs,
            "end_sec": ge,
            "duration_sec": gd,
            "text": g["text"],
            "pattern": pattern,
            "a_roll_locked": a_roll_locked,
            "self_matting": self_matting,
            "allow_broll_evidence": allow_broll,
            "item_count": len(g_items),
        })

    # 统计汇总
    total_dur = max(0.1, sum(b["duration_sec"] for b in beat_groups))
    sfx_items = [it for it in items if it.get("sfx_role")]
    locked_dur = sum(b["duration_sec"] for b in beat_groups if b["a_roll_locked"])
    matting_windows = [
        {"start_sec": b["start_sec"], "end_sec": b["end_sec"], "group_id": b["group_id"]}
        for b in beat_groups if b["self_matting"]
    ]
    return {
        "style_id": style_id,
        "dna": dna,
        "enabled": True,
        "groups": beat_groups,
        "items": items,
        "matting_windows": matting_windows,
        "stats": {
            "total_groups": len(beat_groups),
            "kinetic_groups": kinetic_group_cnt,
            "bridge_groups": bridge_group_cnt,
            "total_items": len(items),
            "sfx_trigger_items": len(sfx_items),
            "a_roll_locked_ratio": round(locked_dur / total_dur, 3),
            "broll_evidence_budget_ratio": round(1.0 - locked_dur / total_dur, 3),
        },
    }



_QUIET_STAGE_PRIMITIVES = {"plain_bridge", "clean_breath"}
_DIRECTOR_SFX_GAP_SEC = 1.2


def _read_optional_json(path: Path) -> Dict[str, Any]:
    try:
        data = read_json(path)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def composition_for_sentence(
    profile: Dict[str, Any], sentence: Dict[str, Any],
) -> Dict[str, Any]:
    """Resolve the take-level framing, with the same normalized-name fallback as the director."""
    source = str(sentence.get("source_media") or "").replace("\\", "/")
    stem = Path(_media_name(source)).stem
    takes = [take for take in profile.get("aroll_profiles") or [] if isinstance(take, dict)]
    matches = [
        take for take in takes
        if stem and Path(_media_name(take.get("media_path"))).stem == stem
    ]
    exact = [take for take in matches if str(take.get("media_path") or "").replace("\\", "/") == source]
    fallback = profile.get("allow_behind_person", profile.get("primary_stage_mode") != "closeup_chest_stage")

    def fits(take: Dict[str, Any]) -> bool:
        return bool(take.get("allow_behind_person", fallback)) and float(take.get("face_area_ratio") or 0) < 0.085

    matches = exact or matches or [profile]
    take = min(matches, key=fits)
    return {**profile, **take, "allow_behind_person": fits(take)}


def _stage_y(composition: Dict[str, Any]) -> float:
    return float(composition.get("safe_fg_y_norm", -0.46))


def _union_face_bbox(compositions: List[Dict[str, Any]]) -> Optional[Dict[str, float]]:
    """计算同一段时间重叠的所有镜头人脸框联合最宽/最高安全外包络，绝不因切换镜头而切耳朵半张脸。"""
    boxes = [c.get("face_bbox") for c in compositions if isinstance(c.get("face_bbox"), dict)]
    if not boxes:
        return None
    try:
        x0 = min(float(b["x"]) for b in boxes)
        y0 = min(float(b["y"]) for b in boxes)
        x1 = max(float(b["x"]) + float(b["w"]) for b in boxes)
        y1 = max(float(b["y"]) + float(b["h"]) for b in boxes)
    except (KeyError, TypeError, ValueError):
        return None
    return {
        "x": round(x0, 4),
        "y": round(y0, 4),
        "w": round(max(0.0, x1 - x0), 4),
        "h": round(max(0.0, y1 - y0), 4),
    }


def _retarget_legacy_composition(
    stage: Dict[str, Any], sentences: List[Dict[str, Any]], profile: Dict[str, Any],
) -> None:
    if not profile:
        return
    shots = [(s["start_sec"], s["end_sec"], composition_for_sentence(profile, s)) for s in sentences]

    def overlapping_shots(start: float, end: float) -> List[Dict[str, Any]]:
        return [c for st, en, c in shots if st < end and start < en]

    for item in stage["items"]:
        if item["z_layer"] != "behind_person":
            continue
        shots_here = overlapping_shots(item["start_sec"], item["end_sec"])
        constrained = [c for c in shots_here if not c["allow_behind_person"]]
        if constrained:
            y = min(_stage_y(c) for c in constrained)
            # Reserve the upper chest row for red_slam and stagger the smaller rows below it.
            yn = round(max(-0.65, min(-0.49, y - 0.05)) - (item["track_lane"] - 3) * 0.075, 4)
            item.update(
                z_layer="foreground", x_norm=0.0, y_norm=yn,
                center_px=list(norm_to_canvas_px(0.0, yn)), font_size_px=76, font_size_units=12.7,
            )
            continue
        # 使用全重叠镜头人脸联合外包络：若头顶 <= 0.19 UI 线有足够高度，上提至最严格头的上方；否则降级安全前景区
        fb = _union_face_bbox(shots_here)
        if fb is not None:
            fy, fh = float(fb.get("y", 0.3)), float(fb.get("h", 0.2))
            iy_px = float((item.get("center_px") or norm_to_canvas_px(item["x_norm"], item["y_norm"]))[1])
            iy_norm_01 = iy_px / float(CANVAS_HEIGHT)
            if (fy - 0.04) <= iy_norm_01 <= (fy + fh + 0.04):
                if fy >= 0.27:
                    target_y01 = max(0.20, min(fy - 0.05, 0.215 + (item["track_lane"] % 2) * 0.035))
                    new_yn = round(1.0 - 2.0 * target_y01, 4)
                    item.update(
                        y_norm=new_yn,
                        center_px=list(norm_to_canvas_px(item["x_norm"], new_yn)),
                    )
                else:
                    fg_y = min((_stage_y(c) for c in shots_here), default=-0.46)
                    yn = round(max(-0.65, min(-0.50, fg_y - 0.05)) - (item["track_lane"] - 3) * 0.075, 4)
                    item.update(
                        z_layer="foreground", x_norm=0.0, y_norm=yn,
                        center_px=list(norm_to_canvas_px(0.0, yn)), font_size_px=76, font_size_units=12.7,
                    )
    behind_groups = {it["group_id"] for it in stage["items"] if it["z_layer"] == "behind_person"}
    for group in stage["groups"]:
        group["self_matting"] = group["group_id"] in behind_groups
    stage["matting_windows"] = [w for w in stage["matting_windows"] if w["group_id"] in behind_groups]


# 句首单字连词：词级时间戳从「也不是行情差」的「不」开始时，要把起点前移对齐这个连词，
# 否则大字比同句底字幕晚出，底字幕就会被判成“没被完整覆盖”而复读一屏。
_SENTENCE_LEAD_CONJUNCTIONS = "也都并又还"


def _director_rows(
    texts: List[str], timeline: List[Tuple[str, float, float]], start: float, end: float, fps: int,
    sentence_starts: Optional[set] = None, word_backed: Optional[set] = None,
) -> List[Tuple[float, float]]:
    # Later rows get at least one frame even when ASR places a word close to the act boundary.
    count = min(len(texts), max(1, int(round((end - start) * fps))))
    labels = [clean_display_text(text.lstrip("✕✓ ")) for text in texts[:count]]
    timeline = [(ch, st, en) for ch, st, en in timeline if not ch.isspace()]
    lead_starts = sentence_starts or frozenset()
    timed_chars = word_backed or frozenset()
    spoken = "".join(ch for ch, _, _ in timeline)
    estimates = map_chunks_to_times(labels, timeline, start, end)
    starts: List[float] = []
    cursor = 0
    for idx, (label, estimate, _) in enumerate(estimates):
        phrase = "".join(label.split())
        match = spoken.find(phrase, cursor)
        if match >= 0:
            lead = match - 1
            if (lead >= 0 and lead in lead_starts and lead in timed_chars
                    and spoken[lead] in _SENTENCE_LEAD_CONJUNCTIONS):
                at = timeline[lead][1]
            else:
                at = timeline[match][1]
            cursor = match + len(phrase)
        else:
            at = estimate
        latest = max(start, end - (len(estimates) - idx) / fps)
        starts.append(max(starts[-1] + 1 / fps if starts else start, min(latest, at)))
    return list(zip(starts, [*starts[1:], end]))


def _pillar_rows(visual: Dict[str, Any]) -> List[Tuple[str, str]]:
    """一条结构幕里的全部条陈：(序号, 短结论)。序号为空表示没有编号。"""
    badge = str(visual.get("accent_token") or "")
    headline = str(visual.get("headline") or "").strip()
    rows: List[Tuple[str, str]] = [(badge if badge.isdecimal() else "", headline)]
    for raw in visual.get("items", []) or []:
        line = str(raw).strip()
        if not line or _display_length(line) > 7:
            continue
        number = re.fullmatch(r"(\d+)(?:[\s·.:：]+(.+))?", line)
        rows.append((number[1], number[2] or "") if number else ("", line))
    return [(number, label) for number, label in rows if number or label]


def _locate_phrase_offset(
    phrase: str,
    timeline: List[Tuple[str, float, float]],
    ref_start: float,
    max_offset: float,
) -> Optional[float]:
    """从逐字时间轴定位短语（忽略装饰符与空格）首次被说出的相对秒数偏移。"""
    clean_p = clean_display_text(re.sub(r"[✕✓·\s+]+", "", str(phrase or "")))
    if not clean_p or not timeline:
        return None
    tl_clean = [(ch, st, en) for ch, st, en in timeline if ch.strip()]
    spoken = "".join(ch for ch, _, _ in tl_clean)
    idx_m = spoken.find(clean_p)
    if idx_m < 0 and len(clean_p) >= 2:
        for sub_len in range(min(4, len(clean_p)), 1, -1):
            for start_i in range(len(clean_p) - sub_len + 1):
                sub = clean_p[start_i : start_i + sub_len]
                idx_m = spoken.find(sub)
                if idx_m >= 0:
                    break
            if idx_m >= 0:
                break
    if idx_m < 0:
        # 对任意中文数字与阿拉伯数字表达（如 200 <-> 两百、15 <-> 十五、2000 <-> 两千）做通用归一化互查
        norm_p = _normalize_zh_numerals(clean_p)
        norm_spoken, norm_idx_map = _normalize_zh_numerals_with_map(spoken)
        if norm_p and norm_spoken:
            idx_norm = norm_spoken.find(norm_p)
            if idx_norm < 0:
                m_d = re.search(r"\d+", norm_p)
                if m_d:
                    idx_norm = norm_spoken.find(m_d.group(0))
            if idx_norm < 0 and len(norm_p) >= 2:
                for sub_len in range(min(4, len(norm_p)), 1, -1):
                    for start_i in range(len(norm_p) - sub_len + 1):
                        sub_n = norm_p[start_i : start_i + sub_len]
                        idx_norm = norm_spoken.find(sub_n)
                        if idx_norm >= 0:
                            break
                    if idx_norm >= 0:
                        break
            if 0 <= idx_norm < len(norm_idx_map):
                idx_m = norm_idx_map[idx_norm]
    if idx_m < 0 or idx_m >= len(tl_clean):
        return None
    rel = float(tl_clean[idx_m][1]) - ref_start
    return round(max(0.0, min(max(0.0, max_offset), rel)), 4)


_UNIFIED_L1_U = 11.5  # ~69px（5.3% 画面高度）常规桥接 / 副标 / inline 引导
_UNIFIED_L2_U = 16.5  # ~99px（7.5% 画面高度）主讲高光 / 清单条目 / red_pill 胶囊
_UNIFIED_L3_U = 22.5  # ~135px（10.5% 画面高度）痛点 red_slam / 荧光绿焦点 / 大数字
_STYLE02_UNIFIED_FONT_CANDS = list(STYLE_DNA_REGISTRY["style_02"]["font_candidates"])

_UNIFIED_PRIMITIVE_ALIAS = {
    "hero_intro_pop": "opening_triad",
    "staged_3tier_build": "opening_triad",
    "hero_anchor_plus_danmaku": "pain_slam_danmaku",
    "negation_strike": "pain_slam_danmaku",
    "concept_split_card": "left_right_punch",
    "framework_pillar": "stacked_dual_color",
    "semantic_diagram_card": "giant_anchor_sub",
    "list_build_gather_dim_overlay": "list_gather_depth",
    "num_anchor_dim_overlay": "number_burst_pair",
    "entity_stack_plus_top": "stacked_dual_color",
    "inline_emphasis_cta": "inline_highlight_cta",
}


def _unified_style02_items(
    act: Dict[str, Any],
    sentences: List[Dict[str, Any]],
    composition: Dict[str, Any],
    shot_comps: List[Dict[str, Any]],
    ordinal: int,
    fps: int,
    pillar_state: Optional[Dict[str, int]] = None,
    *,
    protected_terms: Optional[List[str]] = None,
    subtitle_entries: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """
    style_02 一体化单层动态舞台（商业与探店绝对共用同一字体、三级字号标尺与唯一色盘）：
    - 无双层底字幕复读；
    - 完整落地 8 大复合排版原语 + 逐字声画 0ms 咬合；
    - 全重叠镜头联合人脸包络避头防切耳。
    """
    primitive = act["primitive"]
    start, end = act["start_sec"], act["end_sec"]
    _s02_fp = _style02_font_path()
    _strict_tail_chars = frozenset("的嘅地得之把被从向给俾在喺这那呢嗰𠮶")

    def _strip_tofu_symbols(val: Any) -> str:
        # 统一粗黑体舞台禁用易缺失的装饰叉号/勾号，防止渲染出方框豆腐块 ☒
        return re.sub(r"[✕✓✘✖×✔\u2715-\u2718]+", "", str(val or "")).strip()

    items: List[Dict[str, Any]] = []

    def add(
        text: str, variant: str, st: float, en: float, x: float, yn: float,
        size_u: float, anim: str, lane: int, sfx: Optional[str] = None,
        layer: str = "foreground", dim_after: Optional[float] = None,
        inline_parts: Optional[List[Dict[str, Any]]] = None,
        tier: str = "L2",
    ) -> Optional[Dict[str, Any]]:
        clean_t = _strip_tofu_symbols(text)
        if not clean_t or en <= st:
            return None
        if layer == "foreground":
            yn = round(max(-0.47, min(0.55, float(yn))), 4)
        item = _make_item(
            item_id=f"ks_u2_{ordinal:02d}_{len(items) + 1:02d}",
            group_id=act["group_id"],
            text=clean_t,
            start_sec=st,
            end_sec=en,
            fps=fps,
            variant=variant,
            x_norm=x,
            y_norm=yn,
            font_size_units=size_u,
            anim_key=anim,
            track_lane=lane,
            z_layer=layer,
            dim_after_sec=dim_after,
            dim_alpha=0.42 if dim_after is not None else 1.0,
            inline_parts=inline_parts,
            sfx_role_override=sfx,
        )
        item.update(
            act_id=act["act_id"],
            anim_style_v6=primitive,
            theme_profile="style_02_unified",
            typography_tier=tier,
            font_candidates=list(_STYLE02_UNIFIED_FONT_CANDS),
            style_id="style_02",
            out_anim="fade_out" if variant != "bridge_bar" else "",
        )
        if variant == "hero_lime" and _NUMBER_RUN.search(clean_t):
            item["element_choreo"] = "number_roll"
        items.append(item)
        return item

    def _sync_timing(it: Dict[str, Any], new_st: Optional[float] = None, new_en: Optional[float] = None) -> None:
        if new_st is not None:
            it["start_sec"] = round(float(new_st), 4)
            it["start_frame"] = int(round(float(new_st) * fps))
            it["aligned_sfx_or_accent_frame"] = int(round(float(new_st) * fps))
        if new_en is not None:
            it["end_sec"] = round(float(new_en), 4)
            it["end_frame"] = int(round(float(new_en) * fps))
        it["read_hold_sec"] = round(max(0.1, float(it["end_sec"]) - float(it["start_sec"])), 3)

    def _build_act_speech_chunks(target_min: int = 4, target_max: int = 9) -> List[Tuple[str, float, float]]:
        # 先做通用跨句重平衡，再逐句接入 step5 词性+声学停顿+DP 切分（绝不跨句拼接成跨句错行）
        if not sentences:
            return []

        reb_sents = rebalance_asr_sentences(sentences, max_chars=target_max) or sentences
        all_chunks: List[Tuple[str, float, float]] = []
        prev_end = start
        for s_reb in reb_sents:
            st_t = str(s_reb.get("text") or "").strip()
            if not clean_display_text(st_t):
                continue
            seg_s = max(prev_end, float(s_reb["start_sec"]))
            seg_e = max(seg_s + 0.25, float(s_reb["end_sec"]))
            seg_tl = [c for c in _extract_char_timeline(s_reb) if c[0].strip()]
            s_gaps = _compute_sentence_word_gaps(s_reb, st_t)
            chunks = _split_bridge_phrases_adaptive(
                st_t,
                target_min=target_min,
                target_max=target_max,
                protected_terms=protected_terms,
                font_path=_s02_fp,
                word_gaps=s_gaps,
            ) or [clean_display_text(st_t)]
            mapped = map_chunks_to_times(chunks, seg_tl, seg_s, seg_e)
            for ci, (c_txt, cs, _ce) in enumerate(mapped):
                nxt_s = mapped[ci + 1][1] if ci + 1 < len(mapped) else seg_e
                cs_ok = max(prev_end, min(seg_e - 0.25, float(cs)))
                ce_ok = max(cs_ok + 0.25, min(seg_e, float(nxt_s)))
                if all_chunks and cs_ok < all_chunks[-1][2]:
                    all_chunks[-1] = (all_chunks[-1][0], all_chunks[-1][1], max(all_chunks[-1][1] + 0.20, cs_ok))
                all_chunks.append((c_txt, round(cs_ok, 4), round(ce_ok, 4)))
                prev_end = ce_ok
        return all_chunks

    def _norm_cov(text_v: str) -> str:
        return _normalize_zh_numerals(clean_display_text(text_v))

    act_speech_chunks = _build_act_speech_chunks(target_min=4, target_max=9)

    # 过渡句/呼吸段：无黑方底框通透白字 bridge_bar（0 音效，<=9 字严格居中不碰右侧点赞区）
    if primitive in ("plain_bridge", "clean_breath"):
        for c_txt, cs, ce in act_speech_chunks:
            add(c_txt, "bridge_bar", cs, ce, 0.0, -0.45, _bridge_units_for_text(c_txt, _s02_fp), "", 0, sfx=None, tier="L1")
        return items

    # 收集逐字时间轴
    timeline: List[Tuple[str, float, float]] = []
    sentence_starts: set = set()
    word_backed: set = set()
    for sentence in sentences:
        chars = [entry for entry in _extract_char_timeline(sentence) if not entry[0].isspace()]
        s_idx = len(timeline)
        sentence_starts.add(s_idx)
        timeline.extend(chars)
        if _has_word_timing(sentence):
            word_backed.update(range(s_idx, s_idx + len(chars)))

    visual = act.get("visual_copy") or {}
    _act_plain_for_snap = "".join(clean_display_text(str(s.get("text") or "")) for s in sentences)
    _snap_atomic_spans: List[Tuple[int, int]] = []
    if _act_plain_for_snap:
        _snap_atomic_spans.extend(iter_num_unit_spans(_act_plain_for_snap, pattern=_NUM_UNIT_RE))
        _snap_atomic_spans.extend(m.span() for m in _ALNUM_MIXED_TOKEN_RE.finditer(_act_plain_for_snap))
        _snap_atomic_spans.extend(m.span() for m in _HYPHEN_ASCII_TOKEN_RE.finditer(_act_plain_for_snap))

    def _snap_visual_copy_bounds(raw_t: str) -> str:
        t_clean = _strip_tofu_symbols(raw_t)
        if not t_clean or not _act_plain_for_snap or not _snap_atomic_spans:
            return t_clean
        idx_m = _act_plain_for_snap.find(t_clean)
        if idx_m < 0:
            return t_clean
        new_s, new_e = idx_m, idx_m + len(t_clean)
        for sp_s, sp_e in _snap_atomic_spans:
            if sp_s < new_s < sp_e:
                new_s = sp_s
            if sp_s < new_e < sp_e:
                new_e = sp_e
        if new_s != idx_m or new_e != idx_m + len(t_clean):
            return _act_plain_for_snap[new_s:new_e]
        return t_clean

    raw_items_list = [_snap_visual_copy_bounds(str(t or "").strip()) for t in (visual.get("items") or []) if str(t or "").strip()]
    neg_items = {
        _strip_tofu_symbols(t)
        for t in raw_items_list
        if t.startswith(("✕", "✘", "✖")) or _strip_tofu_symbols(t).startswith(("不是", "没有", "没", "别", "不能", "不要"))
    }
    headline = _snap_visual_copy_bounds(visual.get("headline") or "")
    lines = [_strip_tofu_symbols(t) for t in raw_items_list if _strip_tofu_symbols(t) and _display_length(_strip_tofu_symbols(t)) <= 8]
    if _display_length(headline) > 7:
        headline = ""
    accent_tok = _snap_visual_copy_bounds(visual.get("accent_token") or "")
    footnote = _snap_visual_copy_bounds(visual.get("footnote") or "")
    y_base = max(-0.34, min(-0.24, _stage_y(composition)))
    u_prim = _UNIFIED_PRIMITIVE_ALIAS.get(primitive, primitive)

    def timed_spans(texts: List[str]) -> List[Tuple[float, float]]:
        return _director_rows(texts, timeline, start, end, fps, sentence_starts, word_backed)

    if u_prim == "opening_triad":
        rows = [r for r in [headline, *lines, footnote] if r]
        if len(rows) == 1:
            add(rows[0], "hero_lime", start, end, 0.0, y_base + 0.04, _UNIFIED_L3_U, "in:缩小 II", 1, "pop", tier="L3")
        elif len(rows) == 2:
            spans = timed_spans(rows)
            add(rows[0], "hero_white", start, end, 0.0, y_base + 0.09, _UNIFIED_L2_U, "in:向上露出", 1, "swish", tier="L2")
            add(rows[1], "hero_lime", spans[1][0], end, 0.0, y_base - 0.04, _UNIFIED_L3_U, "in:缩小 II", 2, "pop", tier="L3")
        elif rows:
            trio = rows[:3]
            spans = timed_spans(trio)
            t0_off = _locate_phrase_offset(trio[0], timeline, start, max(0.2, spans[1][0] - start))
            trio0_s = start
            if t0_off is not None and t0_off >= 1.15 and spans[1][0] - (start + t0_off) >= 0.45:
                trio0_s = round(max(start, min(spans[1][0] - 0.55, start + t0_off - 0.15)), 4)
            add(trio[0], "hero_white", trio0_s, end, -0.05, y_base + 0.12, _UNIFIED_L2_U, "in:向上露出", 1, "swish", tier="L2")
            add(trio[1], "hero_lime", max(trio0_s, spans[1][0]), end, 0.04, y_base - 0.01, _UNIFIED_L3_U, "in:缩小 II", 2, "pop", tier="L3")
            add(trio[2], "red_pill", max(trio0_s, spans[2][0]), end, 0.0, max(-0.46, y_base - 0.13), _UNIFIED_L2_U, "in:向下露出", 3, "pop", tier="L2")

    elif u_prim == "left_right_punch":
        pair = lines[:2] if len(lines) >= 2 else ([headline, lines[0]] if headline and lines else [])
        if len(pair) >= 2:
            spans = timed_spans(pair)
            if headline and pair[0] != headline:
                add(headline, "hero_white", start, end, 0.0, y_base + 0.14, _UNIFIED_L2_U, "in:向上露出", 1, tier="L2")
            l_var = "red_slam" if pair[0] in neg_items else "hero_white"
            r_var = "hero_lime"
            add(pair[0], l_var, spans[0][0], end, -0.16, y_base + 0.01, _UNIFIED_L2_U, "in:向左露出", 2, "swish", tier="L2")
            add(pair[1], r_var, spans[1][0], end, 0.14, max(-0.46, y_base - 0.10), _UNIFIED_L3_U, "in:缩小 II", 3, "pop", tier="L3")
        elif headline or lines:
            txt = headline or lines[0]
            add(txt, "hero_lime", start, end, 0.0, y_base + 0.02, _UNIFIED_L3_U, "in:缩小 II", 1, "pop", tier="L3")

    elif u_prim == "giant_anchor_sub":
        main_txt = headline or accent_tok or (lines[0] if lines else "")
        sub_txt = (lines[0] if headline and lines and lines[0] != headline else "") or (footnote if footnote != main_txt else "")
        has_num = bool(_NUMBER_RUN.search(main_txt))
        m_var = "hero_lime" if (has_num or accent_tok) else "hero_white"
        anchor_s = start
        if main_txt and (end - start) >= 2.8:
            ph_off = _locate_phrase_offset(main_txt, timeline, start, max(0.2, end - start - 1.4))
            if ph_off is not None and ph_off >= 1.35:
                anchor_s = round(max(start, min(end - 1.65, start + ph_off - 0.18)), 4)
        add(main_txt, m_var, anchor_s, end, 0.0, y_base + 0.05, _UNIFIED_L3_U, "in:缩小 II", 1, "cash_register" if has_num else "pop", tier="L3")
        if sub_txt:
            sub_span = timed_spans([main_txt, sub_txt])[-1][0]
            is_pill = len(sub_txt) <= 6
            add(sub_txt, "red_pill" if is_pill else "bridge_bar", max(anchor_s, sub_span), end,
                0.0, max(-0.46, y_base - 0.10), _UNIFIED_L2_U if is_pill else _bridge_units_for_text(sub_txt, _s02_fp), "in:向下露出", 2,
                tier="L2" if is_pill else "L1")

    elif u_prim == "pain_slam_danmaku":
        slam_txt = headline or (lines[0] if lines else "")
        slam_start = start
        slam_end = end
        slam_item: Optional[Dict[str, Any]] = None
        if slam_txt:
            slam_lbl = slam_txt.lstrip("✕✓ ")
            sl_off = _locate_phrase_offset(slam_lbl, timeline, start, max(0.2, end - start - 1.0))
            if sl_off is not None:
                if sl_off >= 0.90 and (end - (start + sl_off)) >= 1.35:
                    slam_start = round(max(start, min(end - 1.50, start + sl_off - 0.15)), 4)
                if (end - slam_start) >= 3.2 and (start + sl_off + len(slam_lbl) * 0.25) <= end - 1.6:
                    slam_end = round(min(end, max(slam_start + 1.15, start + sl_off + len(slam_lbl) * 0.25 + 0.20)), 4)
            slam_item = add(slam_lbl, "red_slam", slam_start, slam_end, 0.0, y_base + 0.04, _UNIFIED_L3_U, "in:震动冲击", 1, "punch", tier="L3")
        extra_rows = [r.lstrip("✕✓ ") for r in lines if r.lstrip("✕✓ ") != (slam_txt or "").lstrip("✕✓ ")]
        if extra_rows:
            all_behind_ok = bool(shot_comps) and all(c.get("allow_behind_person") for c in shot_comps)
            fb_union = _union_face_bbox(shot_comps)
            top_head_ok = bool(fb_union is None or float(fb_union.get("y", 0.35)) >= 0.27)
            spans = timed_spans(extra_rows)
            # 最多保留前 3 条弹幕，防止人后/前景多行堆叠越界或触发同屏行数上限
            fg_rows = extra_rows[:3]
            fg_spans = spans[:len(fg_rows)]
            fy_min = float(fb_union["y"]) if fb_union else 0.33
            max_y01 = fy_min - 0.035
            if max_y01 >= 0.280:
                behind_y01s = [0.225, 0.280, 0.170]
            elif len(fg_rows) == 1:
                behind_y01s = [min(0.225, max_y01)]
            elif len(fg_rows) == 2:
                behind_y01s = [max(0.130, max_y01 - 0.055), max_y01]
            else:
                behind_y01s = [max(0.130, max_y01 - 0.052), max_y01, max(0.110, max_y01 - 0.104)]
            if not (all_behind_ok and top_head_ok):
                row_ends: List[float] = []
                for ri in range(len(fg_rows)):
                    rs_i = fg_spans[ri][0]
                    nxt_rs = fg_spans[ri + 1][0] if ri + 1 < len(fg_rows) else None
                    if nxt_rs is not None and (nxt_rs - rs_i) >= 0.45:
                        row_ends.append(round(min(end, nxt_rs - 0.04), 4))
                    else:
                        row_ends.append(end)
                conc_indices: List[int] = [
                    ri for ri in range(len(fg_rows))
                    if any(
                        min(row_ends[ri], row_ends[rj]) - max(fg_spans[ri][0], fg_spans[rj][0]) > 0.02
                        for rj in range(len(fg_rows))
                        if rj != ri
                    )
                ]
                conc_slot = {ri: idx_c for idx_c, ri in enumerate(conc_indices)}
                if len(conc_indices) >= 3 and slam_item is not None:
                    slam_yn_new = round(min(-0.18, y_base + 0.06), 4)
                    slam_item["y_norm"] = slam_yn_new
                    slam_item["center_px"] = [slam_item["center_px"][0], int(round((1.0 - slam_yn_new) * (CANVAS_HEIGHT / 2.0)))]
            for ri, (rtxt, (rs, _)) in enumerate(zip(fg_rows, fg_spans)):
                if all_behind_ok and top_head_ok:
                    target_y01 = behind_y01s[ri]
                    dn_yn = round(1.0 - 2.0 * target_y01, 4)
                    dn_xn = round((-0.27 if ri % 2 == 0 else 0.27), 4)
                    add(rtxt, "danmaku_line", rs, end, dn_xn, dn_yn, 14.0, "in:向右露出", 2 + ri,
                        sfx=None, layer="behind_person", tier="L2")
                else:
                    slot = conc_slot.get(ri)
                    if slot is None:
                        fg_xn, fg_yn = 0.0, round(max(-0.46, y_base - 0.10), 4)
                    elif slot == 0:
                        fg_xn, fg_yn = -0.24, round(max(-0.46, y_base - 0.10), 4)
                    elif slot == 1:
                        fg_xn, fg_yn = 0.24, round(max(-0.46, y_base - 0.10), 4)
                    else:
                        fg_xn, fg_yn = 0.0, round(max(-0.38, y_base - 0.02), 4)
                    add(rtxt, "hero_white", rs, row_ends[ri], fg_xn, fg_yn, _UNIFIED_L2_U, "in:向上露出", 2 + ri,
                        sfx=None, layer="foreground", tier="L2")

    elif u_prim == "stacked_dual_color":
        rows = [r for r in [headline, *lines] if r]
        if len(rows) >= 2:
            # 将短截词（<=5字且不含数字，含主标与辅行）在原话存在 <=9 字完整分句时舒展为完整短句
            raw_clauses = [clean_display_text(p) for s in sentences for p in re.split(r"[，。！？、；：,.!?;:]+", str(s.get("text") or "")) if clean_display_text(p)]
            expanded_rows = []
            for rtxt in rows[:3]:
                if len(rtxt) <= 5 and not _NUMBER_RUN.search(rtxt):
                    hit_cl = next((c for c in raw_clauses if rtxt in c and len(c) <= 9 and len(c) > len(rtxt)), None)
                    expanded_rows.append(hit_cl or rtxt)
                else:
                    expanded_rows.append(rtxt)
            spans = timed_spans(expanded_rows)
            r0_start = start
            if (end - start) >= 2.6:
                r0_off = _locate_phrase_offset(expanded_rows[0], timeline, start, max(0.2, end - start - 0.85))
                if r0_off is not None and r0_off >= 0.95 and (end - (start + r0_off)) >= 1.25:
                    r0_start = round(max(start, min(end - 1.40, start + r0_off - 0.15)), 4)
            for ri, (rtxt, (rs, _)) in enumerate(zip(expanded_rows, spans)):
                is_focus = (ri == len(expanded_rows) - 1) or bool(_NUMBER_RUN.search(rtxt))
                st_r = max(r0_start, rs) if ri > 0 else r0_start
                yn_row = max(-0.46, y_base + 0.10 - ri * 0.105)
                if ri == 1 and len(expanded_rows[0]) <= 3 and (st_r - r0_start) >= 1.25:
                    s_chunks = [
                        (ct, cs, ce) for ct, cs, ce in act_speech_chunks
                        if cs < st_r - 0.15 and ce > r0_start + 0.15
                    ]
                    h_c = _norm_cov(expanded_rows[0])
                    r_c = _norm_cov(expanded_rows[ri])
                    mid_ps: List[Tuple[str, float]] = []
                    for ct, cs, _ in s_chunks:
                        ct_clean = ct[len(expanded_rows[0]):].strip() if ct.startswith(expanded_rows[0]) else ct
                        ct_clean = clean_display_text(ct_clean)
                        c_n = _norm_cov(ct_clean)
                        if not c_n or len(ct_clean) < 3 or len(ct_clean) > 8:
                            continue
                        if c_n in h_c or h_c in c_n or c_n in r_c or r_c in c_n:
                            continue
                        if any(c_n[k : k + 2] in r_c for k in range(max(0, len(c_n) - 1))):
                            continue
                        if (ct_clean[0] in _DP_NO_LEAD_CHARS and not (ct_clean[0] == "呢" and _is_cantonese_ne_demo_head(ct_clean))) or ct_clean[-1] in _strict_tail_chars:
                            continue
                        mid_ps.append((ct_clean, cs))
                    if mid_ps:
                        m_txt = mid_ps[0][0]
                        m_cs = max(r0_start + 0.25, min(mid_ps[0][1], st_r - 0.65))
                        m_end = round(st_r - 0.04, 4)
                        m_yn = round(max(-0.46, yn_row - 0.025), 4)
                        if m_end - m_cs >= 0.55:
                            add(m_txt, "hero_white", m_cs, m_end, 0.0, m_yn, _UNIFIED_L2_U, "in:向上露出", 1, sfx=None, tier="L2")
                add(
                    rtxt,
                    "hero_lime" if is_focus else "hero_white",
                    st_r,
                    end,
                    0.0,
                    yn_row,
                    _UNIFIED_L3_U if is_focus else _UNIFIED_L2_U,
                    "in:缩小 II" if is_focus else "in:向上露出",
                    1 + ri,
                    "pop" if ri == 0 else None,
                    tier="L3" if is_focus else "L2",
                )
        elif rows:
            add(rows[0], "hero_lime", start, end, 0.0, y_base + 0.02, _UNIFIED_L3_U, "in:缩小 II", 1, "pop", tier="L3")

    elif u_prim == "list_gather_depth":
        row_items = lines or ([headline] if headline else [])
        has_top_lead = bool(headline and lines and headline not in lines)
        max_rows = min(len(row_items), 4)
        total_lines = max_rows + int(has_top_lead)
        if total_lines >= 5:
            head_u, row_u, step_y = 11.8, 10.4, 0.084
            top_yn = -0.115
            first_row_yn = -0.205 if has_top_lead else -0.115
        elif total_lines == 4:
            head_u, row_u, step_y = 11.8, 10.4, 0.091
            top_yn = -0.195
            first_row_yn = -0.286 if has_top_lead else -0.195
        else:
            head_u, row_u, step_y = 16.0, 15.0, 0.115
            top_yn = -0.22
            first_row_yn = -0.335 if has_top_lead else -0.22
        if has_top_lead:
            add(headline, "hero_lime", start, end, 0.0, top_yn, head_u, "in:向上露出", 1, "pop", tier="L2")
        spans = timed_spans(row_items)
        # 若首行列表延迟 >=1.25s（仅限总行数<=4，绝不叠加第6行），插入严格早于 Row 1 结束且不共享 first_row_yn 的前景过渡行
        if has_top_lead and total_lines <= 4 and spans and (spans[0][0] - start) >= 1.25:
            lead_end = spans[0][0]
            lead_chunks = [
                (ct, cs, ce) for ct, cs, ce in act_speech_chunks
                if cs < lead_end - 0.25 and ce > start + 0.25
            ]
            head_clean = _norm_cov(headline)
            row0_clean = _norm_cov(row_items[0]) if row_items else ""
            row_all_clean = [_norm_cov(r) for r in row_items[:max_rows]]
            lead_pieces: List[Tuple[str, float]] = []
            for ct, cs, _ in lead_chunks:
                ct_trim = ct[len(headline):].strip() if ct.startswith(headline) else ct
                ct_trim = clean_display_text(ct_trim)
                c_n = _norm_cov(ct_trim)
                if not c_n or len(ct_trim) < 3 or len(ct_trim) > 9:
                    continue
                if c_n in head_clean or head_clean in c_n:
                    continue
                if row0_clean and (c_n in row0_clean or row0_clean in c_n):
                    continue
                if any(len(rn) >= 2 and (rn in c_n or any(c_n[k : k + 2] in rn for k in range(max(0, len(c_n) - 1)))) for rn in row_all_clean):
                    continue
                if (ct_trim[0] in _DP_NO_LEAD_CHARS and not (ct_trim[0] == "呢" and _is_cantonese_ne_demo_head(ct_trim))) or ct_trim[-1] in _strict_tail_chars:
                    continue
                lead_pieces.append((ct_trim, cs))
            if lead_pieces:
                lead_txt = lead_pieces[0][0]
                lead_cs = max(start + 0.30, min(lead_pieces[0][1], lead_end - 0.85))
                lead_stop = round(lead_end - 0.05, 4)
                lead_yn = round(max(-0.468, first_row_yn - 0.025), 4)
                if lead_txt and lead_stop - lead_cs >= 0.65:
                    add(lead_txt, "hero_white", lead_cs, lead_stop, 0.0, lead_yn, row_u, "in:向上露出", 1, sfx=None, tier="L2")
        for idx_r in range(max_rows):
            rtxt = row_items[idx_r]
            rs, _ = spans[idx_r]
            nxt_s = spans[idx_r + 1][0] if idx_r + 1 < max_rows else None
            dim_after = round(max(0.18, nxt_s - rs), 3) if nxt_s is not None else None
            yn_r = round(max(-0.468, first_row_yn - idx_r * step_y), 4)
            add(
                rtxt, "hero_white", rs, end, 0.0, yn_r,
                row_u, "in:向右露出", 2 + idx_r, "pop" if idx_r == 0 else None,
                dim_after=dim_after, tier="L2",
            )

    elif u_prim == "number_burst_pair":
        num_txt = headline or accent_tok or (lines[0] if lines else "")
        note_txt = (lines[0] if headline and lines and lines[0] != headline else "") or footnote
        num_s = start
        if num_txt and (end - start) >= 2.0:
            n_off = _locate_phrase_offset(num_txt, timeline, start, max(0.2, end - start - 0.65))
            if n_off is not None and n_off >= 0.90 and (end - (start + n_off)) >= 0.95:
                num_s = round(max(start, min(end - 1.05, start + n_off - 0.15)), 4)
        if num_txt:
            num_it = add(num_txt, "hero_lime", num_s, end, 0.0, y_base + 0.06, _UNIFIED_L3_U, "in:缩小 II", 1, "cash_register", tier="L3")
            if num_it is not None:
                num_dur = max(0.35, end - num_s)
                num_it["beat_offsets"] = {"roll_start": 0.0, "roll_end": round(min(0.32, num_dur * 0.42), 4)}
        if note_txt:
            ns = timed_spans([num_txt, note_txt])[-1][0] if num_txt else num_s
            add(note_txt, "hero_white", max(num_s, ns), end, 0.0, max(-0.46, y_base - 0.09), _UNIFIED_L2_U, "in:向上露出", 2, tier="L2")

    else:  # inline_highlight_cta 及回退默认
        rows = [r for r in lines if r != headline]
        kw = accent_tok or headline or (rows[-1] if rows else "")
        lead = (rows[0] if rows and rows[0] != kw else (headline if headline and headline != kw else ""))
        if lead and kw and len(clean_display_text(lead + kw)) <= 13:
            in_s = start
            in_off = _locate_phrase_offset(lead or kw, timeline, start, max(0.2, end - start - 1.0))
            if in_off is not None and in_off >= 0.42 and (end - (start + in_off)) >= 1.35:
                in_s = round(max(start, min(end - 1.35, start + in_off - 0.06)), 4)
            add(
                lead + kw, "inline_rich", in_s, end, 0.0, y_base - 0.02,
                _UNIFIED_L2_U, "in:向右露出", 1, "pop",
                inline_parts=[
                    {"text": lead, "color": "#FFFFFF", "size_units": _UNIFIED_L1_U},
                    {"text": kw, "color": "#BCFF00", "size_units": _UNIFIED_L2_U},
                ],
                tier="L2",
            )
        else:
            if lead:
                add(lead, "hero_white", start, end, 0.0, y_base + 0.08, _UNIFIED_L1_U, "in:向上露出", 1, tier="L1")
            if kw:
                ks = timed_spans([lead, kw])[-1][0] if lead else start
                add(kw, "hero_lime", max(start, ks), end, 0.0, max(-0.46, y_base - 0.04), _UNIFIED_L2_U, "in:缩小 II", 2, "pop", tier="L2")

    # ── 语义块边界贴合 + 语义级未覆盖残差选择（彻底禁用按字硬切）──
    fg_hi_items = [it for it in items if it.get("z_layer", "foreground") == "foreground" and it.get("variant") != "bridge_bar"]
    if fg_hi_items and act_speech_chunks and u_prim != "list_gather_depth":
        fg_sorted = sorted(fg_hi_items, key=lambda x: (float(x["start_sec"]), int(x.get("track_lane") or 0)))
        for idx_f, it_f in enumerate(fg_sorted):
            st_f = float(it_f["start_sec"])
            en_f = float(it_f["end_sec"])
            if en_f - st_f < 0.45:
                continue
            it_y = float(it_f["y_norm"])
            it_x = float(it_f["x_norm"])
            prev_st_guard = (
                float(fg_sorted[idx_f - 1]["start_sec"]) + 0.08
                if idx_f > 0 and float(fg_sorted[idx_f - 1]["start_sec"]) < st_f - 0.02
                else start
            )
            colliding_prev = [
                p for p in fg_sorted[:idx_f]
                if abs(float(p["y_norm"]) - it_y) < 0.06
                and abs(float(p["x_norm"]) - it_x) < 0.45
                and float(p["end_sec"]) <= st_f + 0.01
            ]
            for _c_txt, c_st, c_en in act_speech_chunks:
                if c_st + 0.02 < st_f < c_en and (st_f - c_st) <= 0.45:
                    new_st = max(start, c_st)
                    if new_st >= prev_st_guard and (en_f - new_st) >= 0.45:
                        can_snap = True
                        pending_trims: List[Tuple[Dict[str, Any], float]] = []
                        for p_col in colliding_prev:
                            p_end = float(p_col["end_sec"])
                            if new_st < p_end + 0.04:
                                trim_target = round(new_st - 0.04, 4)
                                if p_end < end - 0.10 and (trim_target - float(p_col["start_sec"])) >= 0.55:
                                    pending_trims.append((p_col, trim_target))
                                else:
                                    can_snap = False
                                    break
                        if can_snap:
                            for p_col, trim_en in pending_trims:
                                _sync_timing(p_col, new_en=trim_en)
                            _sync_timing(it_f, new_st=new_st)
                    break

    full_tl: List[Tuple[str, float, float]] = [
        (ch, cs_c, ce_c) for ch, cs_c, ce_c in timeline if ch.strip()
    ]
    _filler_chars = set("的嘅了我你佢吧呢啊嘛也喺在咗")

    def _chunk_covered_by_items(chunk_txt: str, check_items: List[Dict[str, Any]]) -> bool:
        cn = _norm_cov(chunk_txt)
        if not cn or not check_items:
            return False
        ov_norms = [_norm_cov(str(it.get("text_or_asset") or "")) for it in check_items if str(it.get("text_or_asset") or "").strip()]
        if not ov_norms:
            return False
        ov_all = "".join(ov_norms)
        if any(len(it_n) >= 2 and (it_n in cn or cn in it_n) for it_n in ov_norms):
            return True
        core = [ch for ch in cn if ch not in _filler_chars]
        if not core:
            return False
        hit_cnt = sum(1 for ch in core if ch in ov_all)
        return hit_cnt >= max(3, int(len(core) * 0.55 + 0.5))

    def _uncovered_chunk_runs() -> List[Tuple[float, float]]:
        runs: List[Tuple[float, float]] = []
        cur_s: Optional[float] = None
        cur_e = 0.0
        for c_txt, cs_c, ce_c in act_speech_chunks:
            active_in_c = [
                it for it in fg_hi_items
                if float(it["start_sec"]) < ce_c - 0.12 and float(it["end_sec"]) > cs_c + 0.12
            ]
            if not _chunk_covered_by_items(c_txt, active_in_c):
                if cur_s is None:
                    cur_s, cur_e = cs_c, ce_c
                elif cs_c - cur_e <= 0.15:
                    cur_e = max(cur_e, ce_c)
                else:
                    if (cur_e - cur_s) >= 0.72:
                        runs.append((cur_s, cur_e))
                    cur_s, cur_e = cs_c, ce_c
            else:
                if cur_s is not None:
                    if (cur_e - cur_s) >= 0.72:
                        runs.append((cur_s, cur_e))
                    cur_s = None
        if cur_s is not None and (cur_e - cur_s) >= 0.72:
            runs.append((cur_s, cur_e))
        return runs

    # 1) 在非 list_gather_depth 幕里，若连续未覆盖语义块区段持续 >=0.75s，把已说完的先期大字精准收束到语义块边界 u_s
    if fg_hi_items and u_prim != "list_gather_depth":
        for u_s, u_e in _uncovered_chunk_runs():
            nxt_fg_s = min(
                (float(it["start_sec"]) for it in fg_hi_items if u_s + 0.30 < float(it["start_sec"]) < u_e),
                default=u_e,
            )
            eff_ue = min(u_e, nxt_fg_s)
            if (eff_ue - u_s) < 0.75:
                continue
            ov_fg = [it for it in fg_hi_items if float(it["start_sec"]) < u_s - 0.25 and float(it["end_sec"]) > u_s + 0.12]
            if not ov_fg:
                continue
            later_text = "".join(ch for ch, cs_c, _ in full_tl if eff_ue - 0.10 <= cs_c <= u_e + 0.12)
            later_norm = _norm_cov(later_text)
            can_trim = True
            pending_adj: List[Tuple[Dict[str, Any], float]] = []
            for it in ov_fg:
                it_txt = str(it.get("text_or_asset") or "")
                if (u_s - float(it["start_sec"])) < 0.85:
                    can_trim = False
                    break
                in_later = sum(1 for ch in _norm_cov(it_txt) if ch in later_norm)
                if len(it_txt) >= 3 and eff_ue < u_e - 0.10 and in_later >= max(3, int(len(it_txt) * 0.7)):
                    can_trim = False
                    break
                pending_adj.append((it, u_s))
            if can_trim:
                for it, new_en in pending_adj:
                    _sync_timing(it, new_en=new_en)

    # 2) 构建严格前景封锁区间，仅在整条完整语义块 act_speech_chunks 边界上排入零重叠 bridge_bar
    raw_fg_spans = sorted(
        (max(0.0, float(it["start_sec"]) - 0.02), float(it["end_sec"]) + 0.02)
        for it in items
        if it.get("z_layer", "foreground") == "foreground" and it.get("variant") != "bridge_bar"
    )
    fg_spans: List[Tuple[float, float]] = []
    for fa, fb in raw_fg_spans:
        if fg_spans and fa <= fg_spans[-1][1]:
            fg_spans[-1] = (fg_spans[-1][0], max(fg_spans[-1][1], fb))
        else:
            fg_spans.append((fa, fb))

    def _free_slices(ws: float, we: float) -> List[Tuple[float, float]]:
        cur = [(ws, we)]
        for ba, bb in fg_spans:
            nxt_cur: List[Tuple[float, float]] = []
            for sa, sb in cur:
                if bb <= sa or ba >= sb:
                    nxt_cur.append((sa, sb))
                else:
                    if ba > sa:
                        nxt_cur.append((sa, ba))
                    if bb < sb:
                        nxt_cur.append((bb, sb))
            cur = nxt_cur
        return [(a, b) for a, b in cur if (b - a) >= 0.45]

    last_bb_end = 0.0
    for c_txt, cs, ce in act_speech_chunks:
        clean_c = clean_display_text(c_txt)
        if not clean_c or len([c for c in clean_c if c not in _filler_chars]) < 2:
            continue
        for sa, sb in _free_slices(cs, ce):
            sa_ok = max(sa, last_bb_end)
            if (sb - sa_ok) >= 0.45:
                active_during_slice = [
                    it for it in fg_hi_items
                    if float(it["start_sec"]) < sb - 0.08 and float(it["end_sec"]) > sa_ok + 0.08
                ]
                if not _chunk_covered_by_items(clean_c, active_during_slice):
                    it_bb = add(clean_c, "bridge_bar", sa_ok, sb, 0.0, -0.45, _bridge_units_for_text(clean_c, _s02_fp), "", 0, sfx=None, tier="L1")
                    if it_bb is not None:
                        last_bb_end = float(it_bb["end_sec"])
    return items


def _director_items(
    act: Dict[str, Any], sentences: List[Dict[str, Any]], composition: Dict[str, Any],
    ordinal: int, theme: str, fps: int,
    pillar_state: Optional[Dict[str, int]] = None,
) -> List[Dict[str, Any]]:
    primitive = act["primitive"]
    if primitive in _QUIET_STAGE_PRIMITIVES:
        return []

    visual = act.get("visual_copy") or {}
    headline = str(visual.get("headline") or "").strip()
    lines = [text for text in visual.get("items", []) if text.strip() and _display_length(text) <= 7]
    if _display_length(headline) > 7:
        headline = ""
    start, end = act["start_sec"], act["end_sec"]
    timeline: List[Tuple[str, float, float]] = []
    sentence_starts: set = set()
    word_backed: set = set()
    for sentence in sentences:
        chars = [entry for entry in _extract_char_timeline(sentence) if not entry[0].isspace()]
        start_idx = len(timeline)
        sentence_starts.add(start_idx)
        timeline.extend(chars)
        if _has_word_timing(sentence):
            word_backed.update(range(start_idx, start_idx + len(chars)))
    any_word_timed = bool(word_backed)
    y = max(-0.43, min(-0.32, _stage_y(composition)))
    items: List[Dict[str, Any]] = []

    def add(
        text: str, variant: str, st: float, en: float, x: float, yn: float, size: float,
        anim: str, lane: int, sfx: Optional[str] = None, layer: str = "foreground",
    ) -> Optional[Dict[str, Any]]:
        if not text or en <= st:
            return None
        item = _make_item(
            item_id=f"ks_v6_{ordinal:02d}_{len(items) + 1:02d}", group_id=act["group_id"],
            text=text, start_sec=st, end_sec=en, fps=fps, variant=variant,
            x_norm=x, y_norm=yn, font_size_units=size, anim_key=anim,
            track_lane=lane, z_layer=layer, sfx_role_override=sfx,
        )
        item.update(
            act_id=act["act_id"], anim_style_v6=primitive, theme_profile=theme,
            render_backend="web_spring", out_anim="fade_out",
        )
        if variant == "negation_line":
            item["element_choreo"] = "strike_slash"
        elif variant == "concept_card":
            item["element_choreo"] = "stagger_card"
        elif variant == "semantic_diagram_card":
            item["element_choreo"] = "semantic_diagram_card"
        elif variant == "hero_lime" and _NUMBER_RUN.search(text):
            item["element_choreo"] = "number_roll"
        items.append(item)
        return item

    if primitive == "negation_strike":
        head_start = start
        head_sfx: Optional[str] = None
        if any_word_timed and headline and _NUMBER_RUN.search(headline):
            dur_head = max(0.4, end - start)
            num_rel = _locate_phrase_offset(headline, timeline, start, dur_head - 0.55)
            if num_rel is not None and num_rel >= 0.45:
                head_start = round(min(end - 0.55, start + max(0.0, num_rel - 0.14)), 4)
                head_sfx = "cash_register"
        head_item = add(headline, "hero_lime", head_start, end, 0.0, y + 0.10, 19.0, "in:缩小 II", 1, head_sfx)
        if head_item is not None and head_item.get("element_choreo") == "number_roll":
            head_dur = max(0.4, end - head_start)
            head_item["beat_offsets"] = {"roll_start": 0.0, "roll_end": round(min(0.32, head_dur * 0.4), 4)}
        for line, (st, en) in zip(lines, _director_rows(
            lines, timeline, start, end, fps, sentence_starts, word_backed)):
            label = line if line.startswith("✕") else f"✕ {line}"
            # Use a separate row so a headline and the current negation can be read together.
            neg_it = add(label, "negation_line", st, en, 0.0, y, 11.5, "in:向右露出", 2, "punch")
            if neg_it is not None and any_word_timed:
                core_neg = line.lstrip("✕✓ ").lstrip(_SENTENCE_LEAD_CONJUNCTIONS)
                neg_dur = max(0.3, en - st)
                rel_sl = _locate_phrase_offset(core_neg, timeline, st, max(0.1, neg_dur - 0.28))
                slash_at = round(max(0.10, min(neg_dur - 0.24, rel_sl if rel_sl is not None else 0.14)), 4)
                neg_it["beat_offsets"] = {"slash_start": slash_at}
    elif primitive == "framework_pillar":
        rows = _pillar_rows(visual)
        # 结构进度需要全片总格数：总格由 plan_kinetic_stage 提前数好，这里只按顺序领号。
        pillar_total = int((pillar_state or {}).get("total", 0))
        row_spans = _director_rows(
            [label or number for number, label in rows], timeline, start, end, fps,
            sentence_starts, word_backed)
        for idx, ((number, label), (st, en)) in enumerate(zip(rows, row_spans)):
            if idx == 0:
                st = start
            progress: Dict[str, Any] = {}
            if number and pillar_total >= 2 and pillar_state is not None:
                pillar_state["index"] = int(pillar_state.get("index", 0)) + 1
                progress = {
                    "element_choreo": "pillar_progress",
                    "pillar_index": pillar_state["index"],
                    "pillar_total": pillar_total,
                }
            badge_item = add(number.zfill(2) if number else "", "pillar_badge", st, en, -0.55 if label else 0.0, y,
                             11.5, "in:缩小 II", 1, "pop")
            if badge_item is not None:
                badge_item.update(progress)
            add(label, "hero_white", st, en, 0.08 if number else 0.0, y,
                12.5, "in:渐显", 2, None if number else "pop")
    elif primitive == "concept_split_card":
        card_lines = list(dict.fromkeys([*([headline] if headline else []), *lines]))
        shift = act["side_shift_x"] if act["camera_intent"] == "side_shift_card" else 0.0
        x = -0.46 if shift > 0 else (0.38 if shift < 0 else 0.0)
        if card_lines:
            card_it = add(card_lines[0], "concept_card", start, end, x, y + 0.04, 12.0, "in:向右露出", 1, "boom_hollow")
            if card_it is not None:
                card_it.update(card_lines=card_lines, card_has_headline=bool(headline))
                dur_c = max(0.6, end - start)
                if any_word_timed and len(lines) == 2:
                    t_left = _locate_phrase_offset(lines[0], timeline, start, dur_c - 0.50)
                    t_right = _locate_phrase_offset(lines[1], timeline, start, dur_c - 0.30)
                    lc = round(max(0.12, min(dur_c - 0.52, t_left if t_left is not None else 0.16)), 4)
                    rc = round(max(lc + 0.16, min(dur_c - 0.28, t_right if t_right is not None else lc + 0.26)), 4)
                    card_it["beat_offsets"] = {"card": 0.0, "left": lc, "right": rc}
    elif primitive == "semantic_diagram_card":
        diag_nodes = [n.strip() for n in headline.split("·") if n.strip()] if headline else []
        diag_rows = list(lines)
        card_lines = list(dict.fromkeys([*([headline] if headline else []), *diag_rows]))
        if card_lines:
            diag_it = add(card_lines[0], "semantic_diagram_card", start, end, 0.0, y + 0.02, 12.0, "in:渐显", 1, "paper_shake")
            if diag_it is not None:
                dur_d = max(0.6, end - start)
                node_offsets: List[float] = []
                out_offsets: List[float] = []
                cursor_t = 0.08
                for nd in diag_nodes:
                    off = _locate_phrase_offset(nd, timeline, start, dur_d - 0.55) if any_word_timed else None
                    val = round(max(cursor_t, min(dur_d - 0.55, off if off is not None else cursor_t)), 4)
                    node_offsets.append(val)
                    cursor_t = val + 0.16
                for oi, rw in enumerate(diag_rows):
                    off = _locate_phrase_offset(rw, timeline, start, dur_d - 0.30) if any_word_timed else None
                    fallback = cursor_t if not (not diag_nodes and oi == 0) else 0.08
                    val = round(max(cursor_t if (oi > 0 or diag_nodes) else 0.05,
                                    min(dur_d - 0.28, off if off is not None else fallback)), 4)
                    out_offsets.append(val)
                    cursor_t = val + 0.18
                diag_it.update(
                    card_lines=card_lines,
                    card_has_headline=bool(headline),
                    diagram_nodes=diag_nodes,
                    diagram_conclusion=diag_rows,
                    beat_offsets={"card": 0.0, "nodes": node_offsets, "outs": out_offsets},
                )
    elif primitive == "inline_emphasis_cta":
        rows = [line for line in lines if line != headline]
        if rows and any_word_timed and headline:
            dur_cta = max(0.4, end - start)
            t_head = _locate_phrase_offset(headline, timeline, start, dur_cta - 0.35)
            t_sub = _locate_phrase_offset(rows[0], timeline, start, dur_cta - 0.35)
            if t_head is not None and t_sub is not None and t_head > t_sub + 0.12:
                # 若前句为痛点条件（如「答不上来」）且 CTA 在后（「私信我」），按真实发声先后依次出字
                sub_s = round(start + max(0.0, t_sub), 4)
                head_s = round(min(end - 0.35, max(sub_s + 0.25, start + t_head - 0.08)), 4)
                add(rows[0], "hero_white", sub_s, end, 0.0, y + 0.10, 14.0, "in:渐显", 1)
                add(headline, "hero_lime", head_s, end, 0.0, y - 0.01, 18.0, "in:缩小 II", 2, "pop")
                return items
        add(headline, "hero_white", start, end, 0.0, y + 0.10 if rows else y, 17.0, "in:缩小 II", 1)
        for idx, (line, (st, en)) in enumerate(zip(rows, _director_rows(
                rows, timeline, start, end, fps, sentence_starts, word_backed))):
            add(line, "hero_white", st, en, 0.0, y, 11.5, "in:向右露出", 2)
    else:
        variant = "hero_lime" if primitive in {"hero_intro_pop", "num_anchor_dim_overlay"} else "hero_white"
        behind = primitive == "hero_anchor_plus_danmaku" and composition["allow_behind_person"]
        rows = [line for line in lines if line != headline]
        title_y = y + 0.10 if rows and not behind else y
        add(headline, variant, start, end, 0.0, title_y, 17.0, "in:缩小 II", 1)
        for idx, (line, (st, en)) in enumerate(zip(rows, _director_rows(
                rows, timeline, start, end, fps, sentence_starts, word_backed))):
            add(line, "danmaku_line" if behind else "hero_white", st, en,
                0.0, 0.28 if behind else y, 11.5, "in:向右露出",
                3 if behind else 2, layer="behind_person" if behind else "foreground")
    return items


def _director_sfx(
    items: List[Dict[str, Any]], acts: List[Dict[str, Any]], duration: float, fps: int,
    *, theme: str = "business_editorial",
) -> int:
    act_by_id = {act["act_id"]: act for act in acts}
    half_frame_sec = 0.5 / fps
    candidates = []
    retained_candidates = []
    for item in items:
        role = item["sfx_role"]
        item.update(sfx_role=None, paired_jianying_sfx="")
        if not role or role in BANNED_SFX_ROLES or item.get("variant") == "bridge_bar":
            continue

        item_act_id = item.get("act_id")
        act = act_by_id.get(item_act_id)
        if not item_act_id:
            # Match legacy items on their timeline time. Quantized frames are for cue spacing.
            time = item["start_sec"]
            act = next((a for a in acts if a["start_sec"] - half_frame_sec <= time < a["end_sec"]), None)
        if act is None:
            continue

        priority = (
            act["dramatic_role"] == "hook",
            role in {"punch", "boom_heavy", "boom_hollow", "cash_register", "pop"},
            act["emotion_intensity"],
        )
        candidate = (priority, item["start_frame"], act["act_id"], item, role)
        if act["primitive"] not in _QUIET_STAGE_PRIMITIVES and act["sfx_budget"] > 0:
            candidates.append(candidate)
        elif theme == "store_vibrant" and not item_act_id and act["primitive"] == "plain_bridge":
            retained_candidates.append(candidate)

    budgets = {act["act_id"]: act["sfx_budget"] for act in acts}
    gap_frames = math.ceil(_DIRECTOR_SFX_GAP_SEC * fps)
    limit = max(6, min(14, math.ceil(duration / 5.0)))

    def has_gap(frame: int, selected: List[tuple]) -> bool:
        return all(abs(frame - row[1]) >= gap_frames for row in selected)

    def choose(ordered: List[tuple]) -> List[tuple]:
        available = budgets.copy()
        selected: List[tuple] = []
        for row in ordered:
            _, frame, act_id, _, _ = row
            if available[act_id] > 0 and has_gap(frame, selected):
                selected.append(row)
                available[act_id] -= 1
                if len(selected) == limit:
                    break
        return selected

    # Prefer high-information accents, then retry earliest-first if priorities leave a sparse cut.
    selected = choose(sorted(candidates, key=lambda row: (row[0], -row[1]), reverse=True))
    if len(selected) < limit:
        earliest = choose(sorted(candidates, key=lambda row: row[1]))
        if len(earliest) > len(selected):
            selected = earliest
    for row in sorted(retained_candidates, key=lambda row: row[1]):
        if len(selected) >= limit:
            break
        if has_gap(row[1], selected):
            selected.append(row)
    for _, _, _, item, role in selected:
        item["sfx_role"] = role
        anim = ANIM_SFX_BINDING_TABLE.get(item["jianying_anim_key"]) or {}
        if anim.get("sfx_role") == role:
            item["paired_jianying_sfx"] = anim["jianying_sfx_name"]
    return limit


_DUPLICATE_CAPTION_MAX = 7
_NUMBER_RUN = re.compile(r"(?:\d+(?:,\d{3})+|\d{2,})")
_DUPLICATE_CAPTION_OVERLAP = 0.50
_DUPLICATE_TEXT_RATIO = 0.85
_DUPLICATE_COVER_TOLERANCE = 0.02
_DUPLICATE_DECOR = "✕✓≠→:：·"
_DUPLICATE_SOFT_LEADS = "也都就还并而又但可"
_DUPLICATE_EXCLUDED_VARIANTS = frozenset({"bridge_bar", "pillar_badge", "editorial_ticker"})


_TICKER_MIN_WINDOW_SEC = 10.0
_TICKER_OPENING_GUARD_SEC = 3.0
_TICKER_HIGHLIGHT_GUARD_SEC = 1.5
_TICKER_FONT_UNITS = 5.2
# 顶部 20% 是平台 UI 与标题区，微锚点只走侧胸安全带。
_TICKER_BELTS = ((-0.62, -0.22), (0.62, -0.22), (-0.66, -0.05), (0.66, -0.05))
_TICKER_CAPTION_TOP_NORM = -0.62
_TICKER_TOP_UI_SAFE_PX = 380.0
_TICKER_SIDE_MARGIN_PX = 48.0
_TICKER_MAX_ITEMS = 2
_TICKER_GENERIC_TOKENS = (
    "的人", "自己", "别人", "別人", "大家", "我们", "我們", "他们", "他們", "她们", "她們",
    "东西", "東西", "时候", "時候", "那张", "那張", "这个", "這個", "那个", "那個", "什么", "什麼",
    "几个", "幾個", "一些", "很多", "问题", "問題", "感觉", "感覺", "地方", "这样", "那样", "一种",
)


def _ticker_box(text: str, x_norm: float, y_norm: float) -> Tuple[float, float, float, float]:
    """小号胶囊的实际像素框：按字数估宽，高度按 30px 字号估算。"""
    cx, cy = norm_to_canvas_px(x_norm, y_norm)
    width = min(360.0, max(140.0, 34.0 * max(2, len(text)) + 28.0))
    height = 30.0 * 1.5 + 12.0
    return (cx - width / 2.0, cy - height / 2.0, cx + width / 2.0, cy + height / 2.0)


def _box_iou(a: Tuple[float, float, float, float], b: Tuple[float, float, float, float]) -> float:
    inter = max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    if inter <= 0.0:
        return 0.0
    area = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / area if area > 0 else 0.0


def _face_box_px(composition: Dict[str, Any]) -> Optional[Tuple[float, float, float, float]]:
    bbox = composition.get("face_bbox")
    if not isinstance(bbox, dict):
        return None
    try:
        x, y = float(bbox["x"]), float(bbox["y"])
        w, h = float(bbox["w"]), float(bbox["h"])
    except (KeyError, TypeError, ValueError):
        return None
    return (x * CANVAS_WIDTH, y * CANVAS_HEIGHT, (x + w) * CANVAS_WIDTH, (y + h) * CANVAS_HEIGHT)


def _safe_ticker_anchor(
    composition: Dict[str, Any], text: str,
) -> Optional[Tuple[float, float]]:
    """只在侧胸安全带落位：避开顶部 20% 平台 UI、左右边距与底部字幕区，碰脸一律放弃。"""
    face = _face_box_px(composition)
    caption_y = (0.5 - _TICKER_CAPTION_TOP_NORM / 2.0) * CANVAS_HEIGHT
    for x_norm, y_norm in _TICKER_BELTS:
        box = _ticker_box(text, x_norm, y_norm)
        if box[1] < _TICKER_TOP_UI_SAFE_PX:
            continue
        if box[0] < _TICKER_SIDE_MARGIN_PX or box[2] > CANVAS_WIDTH - _TICKER_SIDE_MARGIN_PX:
            continue
        if box[3] >= caption_y:
            continue
        if face is not None and _box_iou(box, face) > 0.0:
            continue
        return x_norm, y_norm
    return None


def _ticker_copy(sentence: Dict[str, Any]) -> str:
    phrase = _short_conclusion(str(sentence.get("text") or ""), [sentence], limit=5)
    # 结尾挂着方位/助词（共享表格里/贴上去之后）不是真信息词，宁可换一句也不上屏。
    if not (2 <= _display_length(phrase) <= 5) or phrase[-1:] in "里中上下的地得是了在":
        return ""
    if phrase.endswith(("之后", "以后", "之前", "以前", "时候")):
        return ""
    # 口语泛指词（大家/自己/时候…）与句首虚词开头的半截话（就能看明白）都没有信息量，宁可不出。
    if any(token in phrase for token in _TICKER_GENERIC_TOKENS):
        return ""
    if phrase[0] in _DUPLICATE_SOFT_LEADS:
        return ""
    errors: List[str] = []
    _validate_copy({"headline": phrase}, "editorial_ticker", [sentence], errors)
    return "" if errors else phrase


def _long_quiet_window(start: float, end: float) -> bool:
    return end - start >= _TICKER_MIN_WINDOW_SEC


def _editorial_ticker_items(
    acts: List[Dict[str, Any]], sentences: List[Dict[str, Any]], profile: Dict[str, Any],
    fps: int, theme: str,
) -> List[Dict[str, Any]]:
    """商业长叙述里最多补 2 个极克制的小胶囊锚点；宁可不出，也不压人声或挡脸。"""
    if theme != "business_editorial" or not sentences:
        return []
    active = sorted(
        (act for act in acts if act.get("primitive") not in _QUIET_STAGE_PRIMITIVES),
        key=lambda act: act["start_sec"],
    )
    quiet = [act for act in acts if act.get("primitive") == "plain_bridge"]
    items: List[Dict[str, Any]] = []
    for left, right in zip(active, active[1:]):
        if len(items) >= _TICKER_MAX_ITEMS:
            break
        window_start = max(left["end_sec"] + _TICKER_HIGHLIGHT_GUARD_SEC, _TICKER_OPENING_GUARD_SEC)
        window_end = right["start_sec"]
        if window_end - window_start < _TICKER_MIN_WINDOW_SEC:
            continue
        in_window = [
            act for act in quiet
            if act["start_sec"] >= window_start and act["end_sec"] <= window_end
        ]
        middle = (window_start + window_end) / 2.0
        for act in sorted(in_window, key=lambda row: abs((row["start_sec"] + row["end_sec"]) / 2.0 - middle)):
            spoken = {str(sid) for sid in act.get("sentence_ids") or []}
            picked = next(
                (
                    (sentence, text, anchor)
                    for sentence in sentences if str(sentence.get("id")) in spoken
                    for text in (_ticker_copy(sentence),) if text
                    for anchor in (_safe_ticker_anchor(composition_for_sentence(profile, sentence), text),)
                    if anchor is not None
                ),
                None,
            )
            if picked is None:
                continue
            sentence, text, (x_norm, y_norm) = picked
            item = _make_item(
                item_id=f"ks_ticker_{len(items) + 1:03d}", group_id=act["group_id"], text=text,
                start_sec=sentence["start_sec"], end_sec=sentence["end_sec"], fps=fps,
                variant="editorial_ticker", x_norm=x_norm, y_norm=y_norm,
                font_size_units=_TICKER_FONT_UNITS, anim_key="in:渐显",
                track_lane=4, sfx_role_override=None, z_layer="foreground",
            )
            item.update(
                theme_profile="business_editorial", element_choreo="ticker_fade",
                sfx_role=None, act_id=act.get("act_id"), editorial_ticker=True,
            )
            items.append(item)
            break
    return items
def _caption_key(text: str) -> str:
    """底字幕去重比对用的实词串：去掉装饰符号、标点、空白与句首虚词。"""
    plain = "".join(ch for ch in str(text or "") if ch not in _DUPLICATE_DECOR and not ch.isspace())
    plain = re.sub(r"[，。！？、；：,.!?;:·]+", "", plain)
    return plain.lstrip(_DUPLICATE_SOFT_LEADS)


def _caption_overlap_ratio(short_key: str, long_key: str) -> float:
    """短串能覆盖主串的字符比例：短串只是主串的一部分时不算复读。"""
    if not short_key or not long_key:
        return 0.0
    return sum(1 for ch in set(short_key) if ch in long_key) / float(len(set(long_key)))


def is_duplicate_short_caption(
    caption: Dict[str, Any], highlights: List[Dict[str, Any]],
) -> bool:
    """短底字幕只是同段前景大字的复读、且大字完整罩住它的时段时返回 True。

    安全保底：超过 7 字的长句底字幕一律保留；大字没有完整覆盖底字幕窗口（头尾会留空）
    时也一律保留，宁可多一行底字幕，也不让口播出现没有文字的空档。
    """
    text = str(caption.get("text_or_asset") or "")
    if _display_length(text) > _DUPLICATE_CAPTION_MAX:
        return False
    key = _caption_key(text)
    if len(key) < 2:
        return False
    start, end = float(caption.get("start_sec") or 0.0), float(caption.get("end_sec") or 0.0)
    span = end - start
    if span <= 0:
        return False
    for item in highlights:
        if item.get("base_caption") or item.get("variant") in _DUPLICATE_EXCLUDED_VARIANTS:
            continue
        if not (str(item.get("motion_mode") or "").strip() or item.get("element_choreo")):
            continue
        h_start = float(item.get("start_sec") or 0.0)
        h_end = float(item.get("end_sec") or 0.0)
        if h_start > start + _DUPLICATE_COVER_TOLERANCE or h_end < end - _DUPLICATE_COVER_TOLERANCE:
            continue
        if (min(end, h_end) - max(start, h_start)) / span < _DUPLICATE_CAPTION_OVERLAP:
            continue
        main_key = _caption_key(item.get("text_or_asset") or "")
        if not main_key:
            continue
        short_key, long_key = sorted((key, main_key), key=len)
        if short_key == long_key or _caption_overlap_ratio(short_key, long_key) >= _DUPLICATE_TEXT_RATIO:
            return True
    return False


def duplicate_short_captions(stage_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """商业模式下仍然和前景大字重复的多余底字幕（供审片扣分与建议使用）。"""
    highlights = [item for item in stage_items if not item.get("base_caption")]
    return [
        item for item in stage_items
        if item.get("base_caption") and is_duplicate_short_caption(item, highlights)
    ]


def business_caption_items(
    audio_events: Dict[str, Any], subtitles: List[Dict[str, Any]], fps: int,
    highlight_items: Optional[List[Dict[str, Any]]] = None,
    out_stats: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Use the ordinary subtitle text in the stage that owns style_02's on-screen text."""
    if not subtitles:
        subtitles = [line for sentence in audio_events["sentences"] for line in split_sentence_into_chunks(sentence)]
    highlights = list(highlight_items or [])
    items = []
    suppressed = 0
    for idx, line in enumerate(subtitles, 1):
        if line.get("suppressed") and line.get("suppress_reason") != "kinetic_typography_stage":
            continue
        start, end = line["start_sec"], line["end_sec"]
        if not line["text"].strip() or end <= start:
            continue
        item = _make_item(
            item_id=f"ks_caption_{idx:03d}", group_id="captions", text=line["text"],
            start_sec=start, end_sec=end, fps=fps, variant="bridge_bar",
            x_norm=0.0, y_norm=-0.51, font_size_units=8.5, track_lane=0, trigger="subtitle",
        )
        item.update(theme_profile="business_editorial", base_caption=True)
        if highlights and is_duplicate_short_caption(item, highlights):
            suppressed += 1
            continue
        items.append(item)
    if out_stats is not None:
        out_stats["suppressed_duplicate_captions"] = suppressed
    return items


def _foreground_block_spans(items: List[Dict[str, Any]], pad_sec: float = 0.02) -> List[Tuple[float, float]]:
    """收集前景大字/图卡占用的不重叠时间并集（供非同层底字幕让路裁剪）。"""
    raw = sorted(
        (max(0.0, float(it.get("start_sec") or 0.0) - pad_sec), float(it.get("end_sec") or 0.0) + pad_sec)
        for it in items
        if it.get("z_layer", "foreground") == "foreground"
        and not it.get("base_caption")
        and it.get("variant") not in {"bridge_bar", "editorial_ticker", "pillar_badge"}
    )
    merged: List[Tuple[float, float]] = []
    for a_s, b_s in raw:
        if b_s <= a_s:
            continue
        if merged and a_s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b_s))
        else:
            merged.append((a_s, b_s))
    return merged


def clip_bridge_items_against_foreground(
    items: List[Dict[str, Any]],
    foreground_pool: Optional[List[Dict[str, Any]]] = None,
    fps: int = DEFAULT_FPS,
    min_slice_sec: float = 0.42,
) -> List[Dict[str, Any]]:
    """按前景大字占用区间裁剪同窗底字幕/bridge_bar，仅保留互不叠加的自由片段，根除双层复读。"""
    fg_spans = _foreground_block_spans(foreground_pool if foreground_pool is not None else items)
    if not fg_spans:
        return list(items)
    out: List[Dict[str, Any]] = []
    for it in items:
        is_bridge = bool(it.get("base_caption") or it.get("variant") == "bridge_bar")
        if foreground_pool is None and not is_bridge:
            out.append(it)
            continue
        ws = float(it.get("start_sec") or 0.0)
        we = float(it.get("end_sec") or 0.0)
        cur = [(ws, we)]
        for ba, bb in fg_spans:
            nxt: List[Tuple[float, float]] = []
            for sa, sb in cur:
                if bb <= sa or ba >= sb:
                    nxt.append((sa, sb))
                else:
                    if ba > sa:
                        nxt.append((sa, ba))
                    if bb < sb:
                        nxt.append((bb, sb))
            cur = nxt
        kept_slices = [(a, b) for a, b in cur if (b - a) >= min_slice_sec]
        if not kept_slices:
            continue
        sa0, sb0 = max(kept_slices, key=lambda pair: pair[1] - pair[0])
        it_upd = dict(it)
        it_upd["start_sec"] = round(sa0, 4)
        it_upd["end_sec"] = round(sb0, 4)
        it_upd["start_frame"] = int(round(sa0 * fps))
        it_upd["end_frame"] = int(round(sb0 * fps))
        it_upd["read_hold_sec"] = round(max(0.1, sb0 - sa0), 3)
        it_upd["aligned_sfx_or_accent_frame"] = it_upd["start_frame"]
        out.append(it_upd)
    return out


def enforce_foreground_safe_y(items: List[Dict[str, Any]], min_y: float = -0.45) -> None:
    """全风格前景舞台统一底部安全区收口：确保文本下缘不超出 PLATFORM SAFE_ZONE y_max=1480px。"""
    for it in items:
        if it.get("z_layer", "foreground") != "foreground":
            continue
        yn = float(it.get("y_norm") or 0.0)
        if yn < min_y:
            it["y_norm"] = round(min_y, 4)
            cpx = list(it.get("center_px") or [CANVAS_WIDTH // 2, 1392])
            cpx[1] = int(round((1.0 - min_y) * (CANVAS_HEIGHT / 2.0)))
            it["center_px"] = cpx


def plan_kinetic_stage(
    audio_events: Dict[str, Any],
    style_id: str = "style_02",
    pipeline_dir: Optional[Path] = None,
    fps: int = DEFAULT_FPS,
    broll_evidence_by_sent: Optional[Dict[str, Dict[str, Any]]] = None,
    *,
    director_plan: Optional[Dict[str, Any]] = None,
    composition_profile: Optional[Dict[str, Any]] = None,
    enable_v6_director: bool = False,
) -> Dict[str, Any]:
    """Keep the original stage for unprofiled jobs and layer the director's decisions over V6/V7 jobs."""
    profile = composition_profile
    if profile is None and pipeline_dir is not None:
        profile = _read_optional_json(Path(pipeline_dir) / PROFILE_FILENAME)
    profile = profile or {}
    plan = director_plan
    director_eligible = (style_id == "style_02") or (enable_v6_director and style_id in DIRECTOR_ENABLED_STYLES)
    if director_eligible and plan is None:
        if pipeline_dir is not None and (profile or (Path(pipeline_dir) / PLAN_FILENAME).is_file()):
            plan = load_or_build_director_plan(pipeline_dir, style_id)
        if plan is None and profile:
            try:
                plan = generate_deterministic_director_plan(
                    audio_events, style_id, composition_profile=profile, pipeline_dir=pipeline_dir,
                )
            except ValueError:
                pass
    if not director_eligible:
        plan, profile = None, {}
    theme = (plan or {}).get("theme_profile", "style_02_unified" if style_id == "style_02" else "store_vibrant")

    if plan is None or theme == "store_vibrant":
        stage = _plan_legacy_stage(audio_events, style_id, pipeline_dir, fps, broll_evidence_by_sent)
        if stage["groups"]:
            _retarget_legacy_composition(stage, audio_events["sentences"], profile)
        if plan is not None and theme == "store_vibrant":
            for it in stage["items"]:
                if it.get("variant") != "bridge_bar" and not it.get("out_anim"):
                    it["out_anim"] = "fade_out"
                if it.get("variant") == "hero_lime" and _NUMBER_RUN.search(str(it.get("text_or_asset") or "")) and not it.get("element_choreo"):
                    it["element_choreo"] = "number_roll"
    else:
        dna_cur = dict(get_style_dna(style_id) or {})
        if theme == "style_02_unified":
            dna_cur["font_candidates"] = list(_STYLE02_UNIFIED_FONT_CANDS)
            dna_cur["subtitle_stage_mode"] = "kinetic_typography_stage"
        stage = {
            "style_id": style_id, "dna": dna_cur, "enabled": True,
            "groups": [], "items": [], "matting_windows": [], "stats": {},
        }
    stage["theme_profile"] = theme
    for item in stage["items"]:
        item["theme_profile"] = theme
    if not plan:
        return stage

    by_id = {s["id"]: s for s in audio_events["sentences"]}
    if theme == "style_02_unified" and audio_events.get("sentences"):
        sid_prim = {
            str(sid): str(raw.get("primitive") or "")
            for raw in plan.get("acts", [])
            for sid in (raw.get("sentence_ids") or [])
        }
        sid_act = {
            str(sid): str(raw.get("act_id") or "")
            for raw in plan.get("acts", [])
            for sid in (raw.get("sentence_ids") or [])
        }
        # 先保留跨幕句尾代词剥离（如「我系牛哥我」+「花咗…」），再按同幕或真正跨幕破词依赖分簇重平衡，
        # 绝不让不同 act_id/原语的正常非残碎句被吞并清空。
        raw_sents = [dict(s, words=[dict(w) for w in (s.get("words") or [])]) for s in audio_events["sentences"]]
        _pron_keep_prev = (
            "包括", "私信", "告诉", "觉得", "帮", "替", "给", "俾", "对", "向",
            "把", "被", "拿", "问", "找", "爱", "信", "懂", "看", "睇", "拍", "跟", "陪",
        )
        for idx_s in range(1, len(raw_sents)):
            prev_s = raw_sents[idx_s - 1]
            cur_s = raw_sents[idx_s]
            p_txt = str(prev_s.get("text") or "").strip()
            c_txt = str(cur_s.get("text") or "").strip()
            gap_s = float(cur_s.get("start_sec", 0.0)) - float(prev_s.get("end_sec", 0.0))
            if (
                str(prev_s.get("source_media") or "") == str(cur_s.get("source_media") or "")
                and -0.08 <= gap_s < 0.25
                and len(p_txt) >= 4
                and p_txt[-1] in ("我", "你", "他", "佢")
                and not any(p_txt[:-1].endswith(k) for k in _pron_keep_prev)
                and c_txt
                and not c_txt.startswith(("的", "嘅", "哋", "们", "自己", "也", "都"))
            ):
                tail_ch = p_txt[-1]
                prev_s["text"] = p_txt[:-1]
                cur_s["text"] = tail_ch + c_txt
                p_ws = list(prev_s.get("words") or [])
                if p_ws and str(p_ws[-1].get("word") or "").strip() == tail_ch:
                    moved_w = p_ws.pop()
                    prev_s["words"] = p_ws
                    if p_ws:
                        prev_s["end_sec"] = float(p_ws[-1].get("end_sec", prev_s["end_sec"]))
                    cur_s["words"] = [moved_w] + list(cur_s.get("words") or [])
                    cur_s["start_sec"] = min(float(cur_s.get("start_sec", 0.0)), float(moved_w.get("start_sec", cur_s["start_sec"])))

        _cross_tail_triggers = (
            "的", "嘅", "地", "得", "了", "咗", "着", "过", "喺", "在", "好多", "有啲",
            "呢啲", "𠮶啲", "嗰啲", "呢个", "嗰个", "𠮶个", "这个", "那个", "一间", "一个", "一份", "一种", "就系", "就是",
        )
        _dir_end_allow = ("出来了", "起来了", "下去了", "回来了", "过去了", "进来了", "过来了")

        def _can_merge_across_act(sa: Dict[str, Any], sb: Dict[str, Any]) -> bool:
            id_a, id_b = str(sa.get("id") or ""), str(sb.get("id") or "")
            act_a, act_b = sid_act.get(id_a, ""), sid_act.get(id_b, "")
            if act_a and act_a == act_b:
                return True
            pa_txt = clean_display_text(str(sa.get("text") or ""))
            pb_txt = clean_display_text(str(sb.get("text") or ""))
            if not pa_txt or not pb_txt:
                return False
            prim_a, prim_b = sid_prim.get(id_a, ""), sid_prim.get(id_b, "")
            both_hi = (prim_a not in ("", "plain_bridge", "clean_breath")) and (prim_b not in ("", "plain_bridge", "clean_breath"))
            if pb_txt.startswith(("的", "嘅", "地", "得", "了", "咗", "着", "过")):
                return True
            if pa_txt.endswith(_cross_tail_triggers) and not pa_txt.endswith(_dir_end_allow):
                return True
            if len(pa_txt) < 4 and not both_hi:
                return True
            return False

        clusters: List[List[Dict[str, Any]]] = []
        for s_item in raw_sents:
            if clusters and _can_merge_across_act(clusters[-1][-1], s_item):
                clusters[-1].append(s_item)
            else:
                clusters.append([s_item])
        reb_all: List[Dict[str, Any]] = []
        for cl in clusters:
            reb_all.extend(rebalance_asr_sentences(cl, max_chars=9) or cl)

        if reb_all:
            new_by_id: Dict[str, Dict[str, Any]] = {}
            for s_reb in reb_all:
                mids = [str(m) for m in (s_reb.get("merged_sentence_ids") or ([s_reb.get("id")] if s_reb.get("id") else []))]
                if not mids:
                    continue
                hi_mids = [m for m in mids if sid_prim.get(m, "") not in ("", "plain_bridge", "clean_breath")]
                pb_mids = [m for m in mids if sid_prim.get(m, "") == "plain_bridge"]
                keep_sid = hi_mids[0] if hi_mids else (pb_mids[0] if pb_mids else mids[0])
                s_entry = dict(s_reb)
                s_entry["id"] = keep_sid
                new_by_id[keep_sid] = s_entry
            if new_by_id:
                by_id = new_by_id
    # 结构进度格要跨幕连续：先数清全片 framework_pillar 的条陈总数，再逐幕领号。
    pillar_state: Dict[str, int] = {
        "index": 0,
        "total": sum(
            sum(1 for number, _label in _pillar_rows(raw.get("visual_copy") or {}) if number)
            for raw in plan.get("acts", [])
            if raw.get("primitive") == "framework_pillar"
            and any(str(sid) in by_id for sid in raw.get("sentence_ids") or [])
        ),
    }
    stage_terms: List[str] = _load_stage_protected_terms(audio_events, pipeline_dir)
    stage_sub_entries: Optional[List[Dict[str, Any]]] = None
    if pipeline_dir is not None:
        sub_p = Path(pipeline_dir) / "05_subtitles.json"
        if sub_p.is_file():
            sub_d = _read_optional_json(sub_p) or {}
            if isinstance(sub_d.get("entries"), list):
                stage_sub_entries = sub_d["entries"]

    legacy_highlights = [item for item in stage["items"] if item["variant"] != "bridge_bar"]
    acts: List[Dict[str, Any]] = []
    shots_comp_all = [
        (float(s["start_sec"]), float(s["end_sec"]), composition_for_sentence(profile, s))
        for s in audio_events["sentences"]
    ]
    for idx, raw in enumerate(plan.get("acts", []), 1):
        sentences = [by_id[sid] for sid in raw["sentence_ids"] if sid in by_id]
        if not sentences:
            continue
        st, en = min(s["start_sec"] for s in sentences), max(s["end_sec"] for s in sentences)
        compositions = [composition_for_sentence(profile, sentence) for sentence in sentences]
        shot_comps = [c for s_st, s_en, c in shots_comp_all if s_st < en and st < s_en] or compositions
        composition = min(compositions, key=lambda c: (c["allow_behind_person"], _stage_y(c)))
        shift = composition.get("safe_center_shift_x", 0.08)
        act = dict(
            raw, start_sec=st, end_sec=en, group_id=f"v6_{idx:02d}",
            side_shift_x=max(-0.12, min(0.12, float(shift))),
        )
        for key, default in (
            ("act_id", f"act_{idx:02d}"), ("camera_intent", "steady_hold"),
            ("broll_policy", "lock_aroll"), ("sfx_budget", 0),
            ("dramatic_role", "setup"), ("emotion_intensity", 0.0),
        ):
            act.setdefault(key, default)
        acts.append(act)
        if theme == "store_vibrant" and any(
            it["start_sec"] < en and st < it["end_sec"] for it in legacy_highlights
        ):
            # The existing multi-layer store stage owns this beat. Keep all of its visual layers.
            continue
        if theme == "style_02_unified":
            new_items = _unified_style02_items(
                act,
                sentences,
                composition,
                shot_comps,
                idx,
                fps,
                pillar_state,
                protected_terms=stage_terms,
                subtitle_entries=stage_sub_entries,
            )
        else:
            new_items = _director_items(act, sentences, composition, idx, theme, fps, pillar_state)
        stage["items"].extend(new_items)
        self_matting = any(item["z_layer"] == "behind_person" for item in new_items)
        if self_matting:
            stage["matting_windows"].append({"start_sec": st, "end_sec": en, "group_id": act["group_id"]})
        if theme in ("business_editorial", "style_02_unified") or new_items:
            locked = act["broll_policy"] == "lock_aroll" or self_matting
            stage["groups"].append({
                "group_id": act["group_id"], "sentence_ids": act["sentence_ids"],
                "start_sec": st, "end_sec": en, "duration_sec": en - st,
                "text": "，".join(s["text"] for s in sentences), "pattern": act["primitive"],
                "a_roll_locked": locked, "self_matting": self_matting,
                "allow_broll_evidence": not locked, "item_count": len(new_items),
            })

    if director_eligible and style_id and style_id != "style_02":
        skin_palette = dict((get_style_dna(style_id) or {}).get("palette") or {})
        for it in stage["items"]:
            it.setdefault("style_id", style_id)
            if skin_palette:
                it.setdefault("style_palette", dict(skin_palette))

    apply_v9_single_layer = bool(
        plan and (plan.get("single_layer_stage") or (director_eligible and style_id and style_id != "style_02"))
    )
    if apply_v9_single_layer:
        if theme == "store_vibrant":
            stage["items"] = clip_bridge_items_against_foreground(stage["items"], fps=fps, min_slice_sec=0.40)
        enforce_foreground_safe_y(stage["items"], min_y=-0.45)
    elif theme == "style_02_unified":
        enforce_foreground_safe_y(stage["items"], min_y=-0.47)
    stage["single_layer_stage"] = apply_v9_single_layer

    duration = float(audio_events.get("locked_duration_sec") or max((a["end_sec"] for a in acts), default=0.0))
    ticker_items = _editorial_ticker_items(acts, audio_events["sentences"], profile, fps, theme)
    stage["items"].extend(ticker_items)
    limit = _director_sfx(stage["items"], acts, duration, fps, theme=theme)
    groups = stage["groups"]
    quiet = sum(g["pattern"] in _QUIET_STAGE_PRIMITIVES for g in groups)
    locked_spans = [(g["start_sec"], g["end_sec"]) for g in groups if g["a_roll_locked"]]
    locked_spans.extend((a["start_sec"], a["end_sec"]) for a in acts if a["broll_policy"] == "lock_aroll")
    locked_ratio = _coverage(locked_spans, duration)
    stage["stats"].update(
        total_groups=len(groups), kinetic_groups=len(groups) - quiet, bridge_groups=quiet,
        a_roll_locked_ratio=round(locked_ratio, 3), broll_evidence_budget_ratio=round(1.0 - locked_ratio, 3),
        total_items=len(stage["items"]), sfx_trigger_items=sum(bool(it["sfx_role"]) for it in stage["items"]),
        ticker=bool(ticker_items), ticker_items=len(ticker_items),
    )
    stage.update(
        director_source=plan.get("source", "agent"), director_acts=acts, sfx_budget=limit,
        composition_stage_mode=profile.get("primary_stage_mode"),
        highlight_coverage_ratio=plan.get("highlight_coverage_ratio"),
        music_cues=plan.get("music_cues", []),
    )
    return stage


def main() -> None:
    import argparse
    import json

    ap = argparse.ArgumentParser(description="动态花字舞台编排器调试输出")
    ap.add_argument("--audio-events", required=True)
    ap.add_argument("--style-id", default="style_02")
    args = ap.parse_args()
    ae = json.loads(Path(args.audio_events).read_text(encoding="utf-8"))
    res = plan_kinetic_stage(ae, style_id=args.style_id, pipeline_dir=Path(args.audio_events).parent)
    print(json.dumps(res["stats"], ensure_ascii=False, indent=2))
    print("\n=== BEAT GROUPS ===")
    for g in res["groups"]:
        print(f"  {g['group_id']} {g['start_sec']:05.2f}..{g['end_sec']:05.2f} ({g['duration_sec']:.2f}s) | {g['pattern']:30s} lock={g['a_roll_locked']} mat={g['self_matting']} broll={g['allow_broll_evidence']} | {g['text']}")
    print("\n=== ITEMS ===")
    for it in res["items"]:
        print(f"  {it['id']} {it['start_sec']:05.2f}..{it['end_sec']:05.2f} L{it['track_lane']} [{it['z_layer']:13s}] {it['variant']:13s} xn={it['x_norm']:+.2f} yn={it['y_norm']:+.2f} anim={it['jianying_anim_name']:6s} sfx={str(it['sfx_role']):12s} dim={it['dim_after_sec']} | {it['text_or_asset']}")


if __name__ == "__main__":
    main()
