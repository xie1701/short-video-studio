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
    load_protected_terms,
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
            "SourceHanSerifCN-Bold.otf",
            "AlimamaShuHeiTi-Bold.otf",
            "SourceHanSansCN-Bold.otf",
        ],
    }
}


def get_style_dna(style_id: str) -> Optional[Dict[str, Any]]:
    return STYLE_DNA_REGISTRY.get(str(style_id or "").strip())


def is_kinetic_stage_style(style_id: str) -> bool:
    dna = get_style_dna(style_id)
    return bool(dna and dna.get("subtitle_stage_mode") == "kinetic_typography_stage")


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

    # 1. 优先复用资产库中已通过质检的高品质真实录音音效
    static_sources = {
        "swish": root_assets / "sound_vibe" / "sfx" / "style_02_transition.wav",
        "tip": root_assets / "sound_vibe" / "sfx" / "style_02_ui.wav",
        "boom_hollow": root_assets / "sound_vibe" / "sfx" / "style_02_accent.wav",
        "boom_heavy": root_assets / "sound_vibe" / "sfx" / "style_03_accent.wav",
        "cash_register": root_assets / "sound_vibe" / "sfx" / "style_15_receipt.wav",
        "whoosh_in": root_assets / "sound_vibe" / "sfx" / "style_03_transition.wav",
    }
    for role, src in static_sources.items():
        if role not in BANNED_SFX_ROLES and src.is_file():
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

    # 3. 专属“打字机 4 连敲”（间隔 65ms 连续机械键击），精确还原 `打字光标 ↔ 打字声`
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


def clean_display_text(text: str) -> str:
    """清除结尾/孤立标点与多余空格，保持视觉干净。"""
    t = re.sub(r"[，。！？、；：,.!?;:]+", "", str(text or "")).strip()
    return t


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
    for w in words:
        wt = clean_display_text(str(w.get("text") or w.get("word") or ""))
        if not wt:
            continue
        ws = float(w.get("start_sec", st))
        we = max(ws + 0.04, float(w.get("end_sec", ws + 0.08)))
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


def split_clean_phrases(
    text: str,
    target_min: int = 3,
    target_max: int = 8,
    protected_terms: Optional[List[str]] = None,
) -> List[str]:
    """
    通用中文/粤语口播短语切分器（绝对不断碎固定搭配/数字量词，不遗漏字符）。
    优先按自然停顿标点切，过长再在 tokenize_semantic_words 词边界归并拆分，过短并入邻块。
    """
    raw = str(text or "").strip()
    if not raw:
        return []
    clauses = [clean_display_text(p) for p in re.split(r"[，。！？、；：,.!?;:]+", raw) if clean_display_text(p)]
    if not clauses:
        return []

    raw_chunks: List[str] = []
    # 先剔除前置助词/介词/冠词（如「的/嘅/在/把/一间」）被单独孤立在行尾；
    # 严禁把动态助词「了/着/过」和普通「种/个」强推到下一行行首（否则会把「改了几个数字」劈成「改 / 了几个数字」）。
    _no_hang_tail = set("的嘅只在把被从向和跟与同") | {
        "一间", "一个", "一家", "一套", "一份", "一种",
        "这间", "那间", "呢间", "𠮶间", "这种", "那种", "呢种", "𠮶种", "这个", "那个", "呢个", "𠮶个",
        "也不",
    }
    _attach_tail_suffix = set("了着过得")
    for cl in clauses:
        if len(cl) <= target_max:
            raw_chunks.append(cl)
            continue
        toks = tokenize_semantic_words(cl, terms=protected_terms)
        # 进一步保证「的/嘅/一间」绝不单独悬在上一块末尾，同时「了/着/过」粘附前词不作行首
        cur = ""
        for idx_t, tk in enumerate(toks):
            nxt_tk = toks[idx_t + 1] if idx_t + 1 < len(toks) else ""
            if not cur:
                cur = tk
            elif tk in _attach_tail_suffix and len(cur) + len(tk) <= target_max + 1:
                cur += tk
            elif len(cur) + len(tk) <= target_max:
                # 防止把“的/嘅/在/一间”当块尾虚词（除非整句刚好在这里收尾或下块是完整独立名词太长）
                if tk in _no_hang_tail and nxt_tk and len(cur) >= 4 and len(tk) + len(nxt_tk) <= target_max:
                    raw_chunks.append(cur)
                    cur = tk
                else:
                    cur += tk
            else:
                raw_chunks.append(cur)
                cur = tk
        if cur:
            raw_chunks.append(cur)

    # 合并孤立单字/2字过短残留块（不把序数引导词「第一/第二/第三」硬吞进长句里）
    merged: List[str] = []
    for chunk in raw_chunks:
        prev_is_ord = bool(merged and _ORDINAL_SENT_RE.match(merged[-1]))
        cur_is_ord = bool(_ORDINAL_SENT_RE.match(chunk))
        if (
            merged
            and len(chunk) < target_min
            and len(merged[-1]) + len(chunk) <= target_max + 2
            and not (cur_is_ord and len(merged[-1]) >= 4)
            and not (prev_is_ord and len(chunk) >= 5)
        ):
            merged[-1] += chunk
        elif (
            merged
            and len(merged[-1]) < target_min
            and len(merged[-1]) + len(chunk) <= target_max + 2
            and not (prev_is_ord and len(chunk) >= 5)
            and not (cur_is_ord and len(merged[-1]) >= 4)
        ):
            merged[-1] += chunk
        else:
            merged.append(chunk)
    # 边界保护：防止指示代词「这/那/呢/𠮶」与紧随量词「种/个/间/些/啲/边」跨块腰斩，也防止块尾单独挂助词
    _demo_heads = set("这那呢𠮶")
    _demo_tails = set("种个间些啲边度")
    for k in range(len(merged) - 1):
        if len(merged[k]) >= 3 and merged[k][-1] in _demo_heads and merged[k + 1][0] in _demo_tails:
            merged[k + 1] = merged[k][-1] + merged[k + 1]
            merged[k] = merged[k][:-1]
    return [m for m in merged if m]


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
    r"[0-9]+(?:[.:][0-9]+)?\s*(?:[%％\+＋]|万|亿|千|百|平|平米|平方|年|倍|成|折|元|块|家|间|店|人|份)?"
    r"|[二三四五六七八九十两廿][万千百平倍成]+"
    r"|[一二三四五六七八九十两廿][万千百]"
    r"|[零〇一二三四五六七八九十百千万亿两廿]{2,}[平平米万千百亿年倍成]+"
    r")"
)
_ORDINAL_SENT_RE = re.compile(r"^第[一二三四五六七八九十0-9]+[条点步个]?$")
_ENGLISH_UNIT_TOKEN_RE = re.compile(r"[A-Za-z0-9]+|[%％\+＋]")
_EXTRA_COHESIVE = [
    "𠮶种", "这种", "那种", "呢种", "呢个", "𠮶个", "呢间", "𠮶间",
    "一间", "一个", "一家", "一套", "一份", "一种", "有种", "几个", "没这个", "这个", "前提",
    "负债", "破产", "那年", "替你", "也不",
    "纯手工", "原汁原味", "四大天王", "叉烧包", "桑拿鸡", "点赞茶楼",
    "评论区", "三分之一", "非常之", "我觉得", "性价比", "实体店", "工厂老板",
    "品质OK", "OK嘅", "好多", "好食", "有啲", "中空", "卡座", "灯笼", "岭南元素",
]


def _split_into_n_at_word_bounds(
    text: str,
    n: int,
    protected_terms: Optional[List[str]] = None,
) -> List[str]:
    """沿语义词界与完整数字跨度把短句分为 n 块，避免暴力按下标腰斩双字词（如「负债」「200多万」）。"""
    clean = clean_display_text(text)
    L = len(clean)
    if n <= 1 or L < n * 2:
        return [clean] if clean else []
    toks = tokenize_semantic_words(clean, terms=protected_terms)
    num_spans = [m.span() for m in re.finditer(r"[0-9]+(?:[.:][0-9]+)?[多余来上下左右]?[%％\+＋万亿千百平年倍成折元块家间店人份]*", clean)]
    bounds: List[int] = []
    pos = 0
    for tk in toks[:-1]:
        pos += len(tk)
        if not (2 <= pos <= L - 2):
            continue
        if any(s < pos < e for s, e in num_spans):
            continue
        if clean[pos] in "的嘅了着过得":
            continue
        bounds.append(pos)
    if n == 2:
        if bounds:
            b = min(bounds, key=lambda x: abs(x - L / 2.0))
            return [clean[:b], clean[b:]]
        mid = L // 2
        return [clean[:mid], clean[mid:]]
    if n == 3:
        pairs = [(b1, b2) for i, b1 in enumerate(bounds) for b2 in bounds[i + 1 :] if b2 - b1 >= 2]
        if pairs:
            b1, b2 = min(pairs, key=lambda p: abs(p[0] - L / 3.0) + abs(p[1] - 2.0 * L / 3.0))
            return [clean[:b1], clean[b1:b2], clean[b2:]]
        mid = max(2, L // 3)
        return [clean[:mid], clean[mid : mid * 2], clean[mid * 2 :]]
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
        r"(四大天王|三[件个点步项大法宝]|(?:[一二三四五六七八九十两]大(?:\S{2}))|主要有|包括|分为)",
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
        groups.append({
            "index": len(groups),
            "sentences": list(cur_sents),
            "sentence_ids": [s.get("id") for s in cur_sents if s.get("id")],
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


def _extract_anchor_phrase(clean_txt: str, max_len: int = 6) -> str:
    """提炼痛点/数字/总结句的短锚点词（绝不截断半个词，也不砸出孤立干瘪的二字「问题」）。"""
    for pat in (
        r"(?:好傻|难做|陷阱|误区|三分之一[^，。]{0,3}|[0-9一二三四五六七八九十百千万两]+平(?:[的嘅]厨房)?|[0-9一二三四五六七八九十百千万两]+万)",
        r"(?:不是|没有)[^，。]{1,3}问题|[^，。]{1,2}[的嘅]?问题|问题",
    ):
        m = re.search(pat, clean_txt)
        if m and 2 <= len(m.group(0)) <= max_len + 2:
            return m.group(0)[: max_len + 1]
    parts = split_clean_phrases(clean_txt, target_min=2, target_max=max_len)
    return parts[-1][:max_len] if parts else clean_txt[:max_len]


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

    terms_p = None
    if pipeline_dir:
        for cand in (
            Path(pipeline_dir) / "terms.txt",
            Path(pipeline_dir).parent / "_SVS工作区" / "术语表.txt",
        ):
            if cand.is_file():
                terms_p = cand
                break
    protected_terms = load_protected_terms(terms_p, extra_terms=_EXTRA_COHESIVE)
    groups_raw = _group_sentences(sents)
    ev_map = broll_evidence_by_sent or {}

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
            phrases = split_clean_phrases(g_split_txt, target_min=3, target_max=7, protected_terms=protected_terms)
            if len(phrases) < 3:
                phrases = _split_into_n_at_word_bounds(g_txt, 3, protected_terms=protected_terms)
            elif len(phrases) > 3:
                # 均并回 3 层，保证与原片一致的三行金字塔
                k1 = phrases[0]
                k2 = "".join(phrases[1 : max(2, len(phrases) - 1)])
                if len(k2) > 9:
                    k2 = phrases[1]
                    k3 = "".join(phrases[2:])
                else:
                    k3 = phrases[-1]
                phrases = [k1, k2, k3]
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
            anchor = _extract_anchor_phrase(g_txt, max_len=6)
            # 1) 前景红色重磅主题词（用缩小 II + 拳击砰砸出，贯穿整个意群）
            g_items.append(_make_item(
                item_id=next_id(), group_id=f"grp_{gi:02d}", text=anchor,
                start_sec=gs, end_sec=ge, fps=fps,
                variant="red_slam", x_norm=0.0, y_norm=-0.34, font_size_units=27.0,
                anim_key="in:缩小 II", z_layer="foreground", track_lane=0,
            ))
            # 2) 中下区第一小节先出1条小过桥，紧接着用自抠像把剩下痛点句全部分配到人后滚动弹幕（确保不缺漏台词）
            phrases = split_clean_phrases(g_split_txt, target_min=3, target_max=6, protected_terms=protected_terms)
            mapped = map_chunks_to_times(phrases, tl, gs, ge)
            if mapped:
                first_txt, f_s, f_e = mapped[0]
                g_items.append(_make_item(
                    item_id=next_id(), group_id=f"grp_{gi:02d}", text=first_txt,
                    start_sec=max(gs + 0.30, f_s), end_sec=min(ge, max(gs + 1.35, f_e + 0.15)), fps=fps,
                    variant="bridge_bar", x_norm=0.0, y_norm=-0.52, font_size_units=15.0,
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
                sum_phrase = _extract_anchor_phrase(sum_txt, max_len=7)
                if len(sum_txt) > len(sum_phrase) + 2:
                    pre_body = clean_display_text(sum_txt.replace(sum_phrase, "", 1))
                    pre_list = split_clean_phrases(pre_body, target_min=3, target_max=8, protected_terms=protected_terms)
                    mapped_sum = map_chunks_to_times([*pre_list, sum_phrase], sum_tl, sum_orig_s, ge)
                    if mapped_sum:
                        sum_pre_chunks = mapped_sum[:-1]
                        sum_s = mapped_sum[-1][1]
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
                    variant="bridge_bar", x_norm=0.0, y_norm=-0.73, font_size_units=14.5,
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
                    if len(rp) > 9:
                        blocks.extend(split_clean_phrases(rp, target_min=3, target_max=8, protected_terms=protected_terms))
                    else:
                        blocks.append(rp)
            else:
                blocks = split_clean_phrases(g_split_txt, target_min=3, target_max=8, protected_terms=protected_terms)
            if len(blocks) == 1 and len(blocks[0]) >= 4:
                blocks = _split_into_n_at_word_bounds(blocks[0], 2, protected_terms=protected_terms)
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
                    variant="bridge_bar", x_norm=0.0, y_norm=-0.52, font_size_units=15.5,
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
            m_num = _NUM_UNIT_RE.search(g_txt)
            if m_num:
                num_str = m_num.group(0)
                pre_str = clean_display_text(g_txt[: m_num.start()])
                post_str = clean_display_text(g_txt[m_num.end() :])
                pre_chunks = split_clean_phrases(pre_str, target_min=2, target_max=8, protected_terms=protected_terms) if pre_str else []
                post_chunks = split_clean_phrases(post_str, target_min=2, target_max=8, protected_terms=protected_terms) if post_str else []
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
                        variant="bridge_bar", x_norm=0.0, y_norm=-0.52, font_size_units=15.0,
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
            phrases = split_clean_phrases(g_split_txt, target_min=4, target_max=9, protected_terms=protected_terms)
            if len(phrases) < 2:
                mid = max(2, len(g_txt) // 2)
                phrases = [g_txt[:mid], g_txt[mid:]]
            mapped = map_chunks_to_times(phrases, tl, gs, ge)
            for ci_e, (e_txt, es, _) in enumerate(mapped):
                nxt_s = mapped[ci_e + 1][1] if ci_e + 1 < len(mapped) else ge
                if ci_e == len(mapped) - 1:
                    g_items.append(_make_item(
                        item_id=next_id(), group_id=f"grp_{gi:02d}", text=e_txt,
                        start_sec=es, end_sec=ge, fps=fps,
                        variant="bridge_bar", x_norm=0.0, y_norm=-0.52, font_size_units=16.0,
                        anim_key=None, sfx_role_override=None, track_lane=1, trigger="cta",
                    ))
                elif ci_e == len(mapped) - 2 and len(e_txt) >= 4:
                    words_e = tokenize_semantic_words(e_txt, terms=protected_terms)
                    boundaries: List[int] = []
                    pos_acc = 0
                    for w_tok in words_e[:-1]:
                        pos_acc += len(w_tok)
                        if 2 <= pos_acc <= len(e_txt) - 2:
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
                        variant="bridge_bar", x_norm=0.0, y_norm=-0.52, font_size_units=15.5,
                        anim_key=None, track_lane=0, trigger="bridge",
                    ))

        # ── 原语 H：极简半透黑底呼吸过渡短句（bg=0.7，无动效、严格无音效） ──
        if pattern == "plain_bridge":
            phrases = split_clean_phrases(g_split_txt, target_min=3, target_max=8, protected_terms=protected_terms)
            mapped = map_chunks_to_times(phrases, tl, gs, ge)
            for pi, (b_txt, bs, be) in enumerate(mapped):
                next_s = mapped[pi + 1][1] if pi + 1 < len(mapped) else ge
                g_items.append(_make_item(
                    item_id=next_id(), group_id=f"grp_{gi:02d}", text=b_txt,
                    start_sec=bs, end_sec=max(bs + 0.25, next_s), fps=fps,
                    variant="bridge_bar", x_norm=0.0, y_norm=-0.52, font_size_units=15.5,
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


def _retarget_legacy_composition(
    stage: Dict[str, Any], sentences: List[Dict[str, Any]], profile: Dict[str, Any],
) -> None:
    if not profile:
        return
    shots = [(s["start_sec"], s["end_sec"], composition_for_sentence(profile, s)) for s in sentences]

    def closeups(start: float, end: float) -> List[Dict[str, Any]]:
        return [c for st, en, c in shots if not c["allow_behind_person"] and st < end and start < en]

    for item in stage["items"]:
        if item["z_layer"] != "behind_person":
            continue
        constrained = closeups(item["start_sec"], item["end_sec"])
        if constrained:
            y = min(_stage_y(c) for c in constrained)
            # Reserve the upper chest row for red_slam and stagger the smaller rows below it.
            yn = round(max(-0.65, min(-0.49, y - 0.05)) - (item["track_lane"] - 3) * 0.075, 4)
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
        elif variant == "hero_lime" and _NUMBER_RUN.search(text):
            item["element_choreo"] = "number_roll"
        items.append(item)
        return item

    if primitive == "negation_strike":
        add(headline, "hero_lime", start, end, 0.0, y + 0.10, 19.0, "in:缩小 II", 1)
        for line, (st, en) in zip(lines, _director_rows(
            lines, timeline, start, end, fps, sentence_starts, word_backed)):
            label = line if line.startswith("✕") else f"✕ {line}"
            # Use a separate row so a headline and the current negation can be read together.
            add(label, "negation_line", st, en, 0.0, y, 11.5, "in:向右露出", 2, "punch")
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
            add(card_lines[0], "concept_card", start, end, x, y + 0.04, 12.0, "in:向右露出", 1, "boom_hollow")
            items[-1].update(card_lines=card_lines, card_has_headline=bool(headline))
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
_NUMBER_RUN = re.compile(r"\d{2,}")
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
    # 结尾挂着方位/助词（共享表格里）不是真词，宁可换一句也不上屏。
    if not (2 <= _display_length(phrase) <= 5) or phrase[-1:] in "里中上下的地得是了在":
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


def plan_kinetic_stage(
    audio_events: Dict[str, Any],
    style_id: str = "style_02",
    pipeline_dir: Optional[Path] = None,
    fps: int = DEFAULT_FPS,
    broll_evidence_by_sent: Optional[Dict[str, Dict[str, Any]]] = None,
    *,
    director_plan: Optional[Dict[str, Any]] = None,
    composition_profile: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Keep the original stage for unprofiled jobs and layer the director's decisions over V6 jobs."""
    profile = composition_profile
    if profile is None and pipeline_dir is not None:
        profile = _read_optional_json(Path(pipeline_dir) / PROFILE_FILENAME)
    profile = profile or {}
    plan = director_plan
    if style_id == "style_02" and plan is None:
        if pipeline_dir is not None and (profile or (Path(pipeline_dir) / PLAN_FILENAME).is_file()):
            plan = load_or_build_director_plan(pipeline_dir, style_id)
        if plan is None and profile:
            try:
                plan = generate_deterministic_director_plan(audio_events, style_id, composition_profile=profile)
            except ValueError:
                pass
    if style_id != "style_02":
        plan, profile = None, {}
    theme = (plan or {}).get("theme_profile", "store_vibrant")

    if plan is None or theme == "store_vibrant":
        stage = _plan_legacy_stage(audio_events, style_id, pipeline_dir, fps, broll_evidence_by_sent)
        if stage["groups"]:
            _retarget_legacy_composition(stage, audio_events["sentences"], profile)
    else:
        stage = {
            "style_id": style_id, "dna": get_style_dna(style_id), "enabled": True,
            "groups": [], "items": [], "matting_windows": [], "stats": {},
        }
    stage["theme_profile"] = theme
    for item in stage["items"]:
        item["theme_profile"] = theme
    if not plan:
        return stage

    by_id = {s["id"]: s for s in audio_events["sentences"]}
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
    legacy_highlights = [item for item in stage["items"] if item["variant"] != "bridge_bar"]
    acts: List[Dict[str, Any]] = []
    for idx, raw in enumerate(plan.get("acts", []), 1):
        sentences = [by_id[sid] for sid in raw["sentence_ids"] if sid in by_id]
        if not sentences:
            continue
        st, en = min(s["start_sec"] for s in sentences), max(s["end_sec"] for s in sentences)
        compositions = [composition_for_sentence(profile, sentence) for sentence in sentences]
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
        new_items = _director_items(act, sentences, composition, idx, theme, fps, pillar_state)
        stage["items"].extend(new_items)
        self_matting = any(item["z_layer"] == "behind_person" for item in new_items)
        if self_matting:
            stage["matting_windows"].append({"start_sec": st, "end_sec": en, "group_id": act["group_id"]})
        if theme == "business_editorial" or new_items:
            locked = act["broll_policy"] == "lock_aroll" or self_matting
            stage["groups"].append({
                "group_id": act["group_id"], "sentence_ids": act["sentence_ids"],
                "start_sec": st, "end_sec": en, "duration_sec": en - st,
                "text": "，".join(s["text"] for s in sentences), "pattern": act["primitive"],
                "a_roll_locked": locked, "self_matting": self_matting,
                "allow_broll_evidence": not locked, "item_count": len(new_items),
            })

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
