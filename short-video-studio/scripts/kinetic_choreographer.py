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
)
from step5_subtitles import (  # noqa: E402
    load_protected_terms,
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


def plan_kinetic_stage(
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
            gather_t = min(ge - 0.55, max(list_end - 0.15, (first_item_hits[-1][1] + 0.45) if first_item_hits else list_end))
            sum_start = max(gather_t + 0.35, sum_s)
            if sum_start >= ge - 0.3:
                sum_start = max(gs + gd * 0.65, ge - 1.0)
                gather_t = max(gs + gd * 0.48, sum_start - 0.45)

            scatter_pos = [(-0.26, -0.28), (0.26, -0.28), (-0.26, -0.45), (0.26, -0.45)]
            gather_ys = [-0.26, -0.39, -0.52, -0.65]
            item_anims = ["in:向右集合", "in:随机上升", "in:渐显", "in:向下露出"]

            for ki, ((_raw_it, ks, _ke), num_txt) in enumerate(zip(first_item_hits, numbered_items)):
                xn, yn = scatter_pos[ki % len(scatter_pos)]
                # 阶段 1：随口播错落弹出并累积驻留到 gather_t
                if gather_t > ks + 0.12:
                    g_items.append(_make_item(
                        item_id=next_id(), group_id=f"grp_{gi:02d}", text=num_txt,
                        start_sec=ks, end_sec=gather_t, fps=fps,
                        variant="hero_lime", x_norm=xn, y_norm=yn, font_size_units=16.5,
                        anim_key=item_anims[ki % len(item_anims)], track_lane=1 + ki,
                    ))
                    g_end_s = gather_t
                else:
                    g_end_s = ks
                # 阶段 2：全部收拢居中竖排；在 sum_start 前急速压暗并在 sum_start 砸出终句时干脆退场，不再与前景大字叠压成团
                phase2_end = min(ge, sum_start + 0.08)
                dim_rel = max(0.05, min(phase2_end - g_end_s - 0.05, (sum_start - 0.14) - g_end_s))
                g_items.append(_make_item(
                    item_id=next_id(), group_id=f"grp_{gi:02d}", text=num_txt,
                    start_sec=g_end_s, end_sec=phase2_end, fps=fps,
                    variant="hero_lime", x_norm=0.0, y_norm=gather_ys[ki % len(gather_ys)],
                    font_size_units=16.5, anim_key=None, track_lane=1 + ki,
                    dim_after_sec=dim_rel, dim_alpha=0.14,
                ))

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
