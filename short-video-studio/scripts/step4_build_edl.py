#!/usr/bin/env python3
"""
[Step 4: 以音定画，生成专业剪辑表 (EDL)]
v5 升级：
1. 语义互文（纳入 sharpness_score & stability_score）+ 单调递增源游标彻底消除同源倒带，全 B-roll 严格屏蔽脏乱/穿帮硬禁忌标签。
2. 全面严防 A-roll 句界越界与口型穿帮：首镜 idx==0 同样受 _can_be_safe_aroll 校验，源 A-roll 不可用时降级 B-roll 而非瞎切他人口播。
3. 引入真·L-cut（物证先入 3–4 帧，`j_cut_lead_frames < 0`）与真·J-cut（声先画后 2–4 帧）；移除硬编码 90 帧与悬空角色 `"freeze"`。
4. 计算真实 `first_frame_density_ok`，显式告警 `deictic_gaps` 视觉缺口，统一 `code_broll` 比例口径。
输出：pipeline/04_edl.json 与 pipeline/04_edl_table.md
"""

from __future__ import annotations

import argparse
import math
import re
import statistics
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

SCRIPTS_ROOT = Path(__file__).resolve().parent
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from common import (
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    DEFAULT_FPS,
    EDIT_PROFILES,
    clamp_speed,
    frames_to_sec,
    load_style_applied,
    load_style_spectrum_entry,
    maybe_reexec_in_venv,
    read_json,
    write_json,
)
import os

import broll_match
import element_rules
import kinetic_choreographer
import pip_policy
import scene_budget
import treatment_policy
from broll_semantics import describe_clip, legacy_score
from edit_grammar import check_edl, load_grammar, plan_shot_budget, select_cuts
from generate_code_broll import render_broll_clip_pillow, render_code_clip, resolve_style_palette

PROP_SHOW_CUES = ("睇下", "看下", "看一下", "给大家看", "给你看", "看我手上", "看我手里", "效果图", "实物", "清单", "明细")
DEICTIC_CUES = ("这张图", "四张图", "两张图", "三张图", "效果图", "平面图", "明细表", "账单", "发票", "检测单", "看这里", "看这个", "你看这", "对比一下")
MEDIA_GAP_OBJECT_GROUPS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("效果图/平面图", ("效果图", "平面图", "设计图", "这张图", "两张图", "三张图", "四张图")),
    ("账单/价格/菜单", ("账单", "发票", "明细", "菜单", "价目表", "报价", "检测单")),
    ("卡座/包房/大厅座位", ("卡座", "包房", "包厢", "大厅", "雅间", "座位", "散台")),
    ("后厨/厨房/设备", ("后厨", "厨房", "灶台", "设备", "机器", "明档")),
    ("门头/招牌/外立面", ("门头", "招牌", "门口", "外立面")),
    ("原料/食材/菜品特写", ("原料", "食材", "现切", "现熬", "锅底", "招牌菜")),
    ("改造前后对比", ("改造前", "改造后", "对比一下", "前后对比", "翻新")),
)
POSITIVE_PRAISE_CUES = ("招牌", "正宗", "干净", "舒服", "鲜嫩", "多汁", "放心", "透明", "美味", "好吃", "靓", "靠谱", "新鲜", "卫生", "环境好")
HARD_TABOO_ALWAYS = {"crew_leak", "kitchen_trash", "construction_trash"}
# B-roll 选片口径（v4.6）：语义对位是主序，其它风格加分项只做「同分裁决」。
# 以前选片按「稳/锐/用得少」轮转池子，画面自然对不上台词（用户实测：讲卡座配了 10 条航拍）。
BROLL_SEM_RANK_WEIGHT = 1.0
# 语义同档带：语义分（0~100）差在这以内就视为「一样对得上」，改由场景连续性裁决。
# 这是从 24 套真草稿的段/文件 反推的：原版一条素材反复取 4.3 个时间窗，不是一条用一次。
BROLL_SEM_TIE_BAND = 6.0
# 模糊/剧烈抖动底线：稳定度与清晰度同时跌破可用阈值的镜头，不得作为主口播画面全屏露脸
AROLL_UNUSABLE_SHARP_MAX = 245.0
AROLL_UNUSABLE_STAB_WITH_SOFT_FOCUS = 0.70
AROLL_UNUSABLE_STAB_HARD = 0.08
AROLL_UNUSABLE_JITTER_HARD = 15.0
BROLL_SHAKY_STAB_MIN = 0.20
BROLL_BLURRY_SHARP_MIN = 250.0
BROLL_QUALITY_FLOOR_PEN = 18.0
MAX_AROLL_SHORTFALL_SEC = 0.25
MAX_AROLL_TAIL_EXTENSION_SEC = 1.0
SOURCE_EPSILON_SEC = 0.0001
# 推镜预算：整片挂推镜的镜头占比上限，主轨另有更紧的上限（真值里主轨机位基本不动）
PUSH_SHOT_RATIO = 0.35
PUSH_SHOT_RATIO_AROLL = 0.15
# 对位门禁阈值：写在 broll_match 里（MAX_NONE_RATIO / MIN_HIT_RATIO），这里只做开关


class BrollSemanticGateError(RuntimeError):
    """B-roll 与台词对不上到越线（fail-closed）：默认只审计，显式开启后必须中断出片。"""


class BrollTake(NamedTuple):
    start: float
    end: float
    safe_out: float
    reason: str
    fresh: bool


def _declared_safe_window(data: Dict[str, Any], duration: float) -> Optional[Tuple[float, float]]:
    try:
        start, end = float(data["safe_in_sec"]), float(data["safe_out_sec"])
    except (KeyError, TypeError, ValueError):
        return None
    if math.isfinite(start) and math.isfinite(end) and 0.0 <= start < min(end, duration):
        return start, min(end, duration)
    return None


def _default_broll_handles(duration: float, need_sec: float, stability: float) -> Tuple[float, float]:
    if duration < need_sec + 1.2:
        # 尽量居中取用，短到放不下时也为两端留下少量余量。
        margin = max(duration * 0.05, (duration - need_sec) / 2.0)
        return margin, margin
    tail = min(0.50, duration * 0.10)
    head = min(0.90, duration * 0.18) if stability < 0.78 else min(0.65, duration * 0.12)
    return min(head, duration - need_sec - tail), tail


# 旧口径对照开关：只用于证明"以前为什么对不上"，正常出片必须走概念桥
LEGACY_SEM_MATCH = str(os.environ.get("SVS_LEGACY_SEM_MATCH") or "").strip() in ("1", "true", "yes")


def detect_script_media_gaps(
    sentences: List[Dict[str, Any]],
    shots: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    统一台词-画面缺口预警（供打断 #1 确认单与 Step 4 共用）：
    扫描台词里明确提及的视觉指代词、空间/设施/物证对象，比对现有可用素材的文件名与语义标签，
    缺镜头时提前给出大白话补拍或图解替代建议。
    """
    usable_shots = [
        m for m in (shots or [])
        if m.get("video_grade", "usable") != "ng" and m.get("usable", True) is not False
    ]
    pool_blob = " ".join(
        " ".join(str(t) for t in (m.get("semantic_tags", []) + m.get("manual_tags", [])))
        + " "
        + Path(str(m.get("source_path") or m.get("path") or m.get("normalized_mp4") or m.get("filename") or "")).stem
        for m in usable_shots
    )
    gaps: List[Dict[str, Any]] = []
    seen_labels: set[str] = set()
    for sent in (sentences or []):
        s_txt = str(sent.get("text") or "").strip()
        if not s_txt:
            continue
        # 1) 强指代词优先
        matched_deictic = next((c for c in DEICTIC_CUES if c in s_txt), None)
        if matched_deictic:
            tokens = [matched_deictic] + [tk for tk in ("图", "效果", "平面", "账单", "明细", "发票", "检测", "对比") if tk in matched_deictic]
            if not any(tk in pool_blob for tk in tokens) and matched_deictic not in seen_labels:
                seen_labels.add(matched_deictic)
                gaps.append({
                    "sentence_id": sent.get("id", ""),
                    "matched_cue": matched_deictic,
                    "text": s_txt,
                    "severity": "high",
                    "suggestion": f"台词提到了「{matched_deictic}」，但素材里暂未看到对应画面，建议补拍 3 秒特写或自动生成一张对比图卡顶上",
                })
                continue
        # 2) 具体空间/设备/菜品对象扫描
        for group_label, keywords in MEDIA_GAP_OBJECT_GROUPS:
            hit_kw = next((kw for kw in keywords if kw in s_txt), None)
            if not hit_kw or group_label in seen_labels:
                continue
            if not any(kw in pool_blob for kw in keywords):
                seen_labels.add(group_label)
                gaps.append({
                    "sentence_id": sent.get("id", ""),
                    "matched_cue": hit_kw,
                    "group": group_label,
                    "text": s_txt,
                    "severity": "medium",
                    "suggestion": f"台词讲到「{hit_kw}」（{group_label}），目前素材池里没看到对应实拍标签，如需展示可补拍 3 秒特写",
                })
    return gaps


def _apply_excluded_rules_to_items(pipeline_dir: Path, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """合并 00_excluded_shots.json 与单条记录自带的 usable/excluded_windows，返回过滤后的可用素材列表。"""
    ex_p = pipeline_dir / "00_excluded_shots.json"
    rules = (read_json(ex_p).get("rules") or []) if ex_p.is_file() else []
    out: List[Dict[str, Any]] = []
    for raw_m in items:
        m = dict(raw_m)
        names = {
            str(m.get("filename") or ""),
            Path(str(m.get("source_path") or "")).name,
            Path(str(m.get("normalized_mp4") or "")).name,
        }
        stems = {Path(n).stem for n in names if n}
        wins = list(m.get("excluded_windows") or [])
        for r in rules:
            rf = str(r.get("filename") or "")
            if not rf:
                continue
            if rf in names or Path(rf).stem in stems:
                if r.get("full_clip"):
                    m["usable"] = False
                    m["video_grade"] = "ng"
                elif isinstance(r.get("window"), dict):
                    wins.append(r["window"])
        m["excluded_windows"] = wins
        if m.get("usable", True) is False or m.get("video_grade", "usable") == "ng":
            continue
        out.append(m)
    return out


def _excluded_source_spans(windows: List[Dict[str, Any]]) -> List[Tuple[float, float]]:
    spans = [(float(w.get("start_sec", 0.0)), float(w.get("end_sec", 0.0))) for w in windows]
    return sorted((start, end) for start, end in spans if start < end)


def _avoid_excluded_windows(
    src_in: float,
    seg_dur: float,
    avail: float,
    excluded_windows: List[Dict[str, Any]],
) -> float:
    """若目标切片 [src_in, src_in+seg_dur] 与任一人工标废时间窗重叠，自动前移跳过标废区间。"""
    if not excluded_windows:
        return src_in
    wins = _excluded_source_spans(excluded_windows)
    cand = float(src_in)
    for _ in range(len(wins) + 1):
        overlap = next((w for w in wins if cand < w[1] and (cand + seg_dur) > w[0]), None)
        if not overlap:
            break
        nxt = overlap[1] + 0.05
        if nxt + min(0.5, seg_dur) <= avail:
            cand = nxt
        else:
            # 尾部放不下则尝试窗口之前的干净区段
            before = max(0.0, overlap[0] - seg_dur - 0.05)
            cand = before
            break
    return round(max(0.0, min(cand, max(0.0, avail - 0.05))), 4)


_EXPLAINER_WINDOW_RE = re.compile(
    r"(\d+(?:\.\d+)?\s*[%％倍万亿元块度小时分钟秒天年个次]|第一|第二|第三|步骤|流程|原理|本质|对比|差距|成本|利润|占比|比例|真相|其实)"
)


def detect_explainer_broll_windows(
    sentences: List[Dict[str, Any]],
    lead_sec: float = 0.08,
    min_dur_sec: float = 1.6,
) -> List[Dict[str, Any]]:
    """
    口播动态讲解画面（B-roll）插入窗口探测：
    依据“新信息/数字对比/步骤拆解/原理机制”而非固定秒数，在关键词起音前 0.08s 定位最适合切入
    动态图解 + 平滑圆环人像画中画的时间窗；无解释价值的观点/过渡句保留真人全屏。
    """
    windows: List[Dict[str, Any]] = []
    for idx, sent in enumerate(sentences or []):
        txt = str(sent.get("text") or "").strip()
        if not txt or sent.get("emotion_or_info") in ("cta", "filler"):
            continue
        m = _EXPLAINER_WINDOW_RE.search(txt)
        if not m:
            continue
        s_start = float(sent.get("start_sec", 0.0))
        s_end = float(sent.get("end_sec", s_start + 2.0))
        if (s_end - s_start) < min_dur_sec:
            continue
        hit_tok = m.group(1)
        # 按原句字符位置精确定位触发词的真实逐字起音时间（彻底避免单字误命中句首无关字）
        kw_start = s_start
        words = sent.get("words") or []
        if words:
            clean_hit = re.sub(r"\s+", "", hit_tok)
            joined = ""
            word_spans: List[Tuple[int, int, float]] = []
            for w in words:
                wt = re.sub(r"\s+", "", str(w.get("word") or ""))
                if not wt:
                    continue
                pos0 = len(joined)
                joined += wt
                word_spans.append((pos0, len(joined), float(w.get("start_sec", s_start))))
            hit_pos = joined.find(clean_hit)
            if hit_pos >= 0:
                for p0, p1, wst in word_spans:
                    if p0 <= hit_pos < p1:
                        kw_start = wst
                        break
        win_in = round(max(0.0, kw_start - lead_sec), 3)
        win_out = round(s_end, 3)
        if (win_out - win_in) < min_dur_sec:
            win_in = round(max(0.0, s_start), 3)

        mode_hint = (
            "data_chart" if re.search(r"[%％倍元块万亿]", hit_tok)
            else ("flow_diagram" if re.search(r"第一|第二|第三|步骤|流程", hit_tok)
            else ("versus" if re.search(r"对比|差距|其实|真相", hit_tok) else "kinetic_text"))
        )
        windows.append({
            "window_id": f"exp_win_{len(windows) + 1:02d}",
            "sentence_id": sent.get("id", f"sent_{idx + 1:03d}"),
            "trigger_cue": hit_tok,
            "start_sec": win_in,
            "end_sec": win_out,
            "duration_sec": round(win_out - win_in, 3),
            "suggested_mode": mode_hint,
            "presenter_pip_mode": "smooth_orbit_pip",
            "sentence_text": txt,
        })
    return windows


def pick_first_3s_events(events: List[Dict[str, Any]], fps: int, count: int = 2) -> List[Dict[str, Any]]:
    """在前 3.0 秒内筛选至少 2 个间距 >= 0.55s 的切点音频事件，优先 sentence_start。"""
    if count == 0:
        return []
    candidates = [
        e for e in events
        if 0.50 <= e["time_sec"] <= 2.75
        and e["type"] in ("sentence_start", "pause_end", "keyword_accent", "stress", "bgm_downbeat", "bgm_beat")
    ]
    prio = {"sentence_start": 0, "pause_end": 1, "keyword_accent": 2, "stress": 3, "bgm_downbeat": 4, "bgm_beat": 5}
    candidates.sort(key=lambda e: (prio.get(e["type"], 9), e["time_sec"]))

    picked: List[Dict[str, Any]] = []
    for cand in candidates:
        if all(abs(cand["time_sec"] - p["time_sec"]) >= 0.55 for p in picked):
            picked.append(cand)
        if len(picked) >= count:
            break

    if len(picked) < count:
        fallback = [e for e in events if 0.40 <= e["time_sec"] <= 2.85]
        fallback.sort(key=lambda e: e["time_sec"])
        for cand in fallback:
            if all(abs(cand["time_sec"] - p["time_sec"]) >= 0.45 for p in picked):
                picked.append(cand)
            if len(picked) >= count:
                break

    picked.sort(key=lambda e: e["frame"])
    return picked


def _hydrate_vision_semantics(pipeline_dir: Path, items: List[Dict[str, Any]]) -> int:
    """把抽帧判读出来的画面描述补进素材条目，返回补了几条。

    为什么必须补：`01_manifest.json` 只有 `semantic_tags`（文件名/OCR 猜的），真正的画面判读
    （`description` / `scene_tags_zh` / `scene_labels_source=agent`）落在 `00_inventory.json`
    与 `00_visual_index.json` 里。选片不把这几段文字接进来，等于又回到「不看画面瞎轮转」。
    """
    by_name: Dict[str, Dict[str, Any]] = {}
    inv_p = pipeline_dir / "00_inventory.json"
    if inv_p.is_file():
        for c in (read_json(inv_p).get("clips") or []):
            for key in (c.get("filename"), Path(str(c.get("path") or "")).name):
                if key:
                    by_name.setdefault(str(key), c)
    vis_p = pipeline_dir / "00_visual_index.json"
    if vis_p.is_file():
        for c in (read_json(vis_p).get("clips") or []):
            key = str(c.get("filename") or c.get("file") or "")
            if not key or key in by_name:
                continue
            src = str(c.get("source") or "")
            by_name[key] = {
                "description": c.get("description") or c.get("summary") or "",
                "scene_tags_zh": c.get("tags") or c.get("scene_tags_zh") or [],
                "scene_labels_source": "agent" if src.startswith("agent") else src,
            }
    if not by_name:
        return 0
    filled = 0
    for m in items:
        if str(m.get("description") or "").strip() and (m.get("scene_tags_zh") or []):
            continue
        cand: Optional[Dict[str, Any]] = by_name.get(Path(str(m.get("source_path") or "")).name)
        if cand is None:
            stem = Path(str(m.get("normalized_mp4") or "")).stem
            # norm_20_2026-05-29 183554 -> 2026-05-29 183554
            stem2 = re.sub(r"^norm_\d+_", "", stem)
            for key, val in by_name.items():
                if Path(key).stem == stem2:
                    cand = val
                    break
        if cand is None:
            continue
        if not str(m.get("description") or "").strip():
            m["description"] = str(cand.get("description") or "")
        if not (m.get("scene_tags_zh") or []):
            m["scene_tags_zh"] = list(cand.get("scene_tags_zh") or [])
        if not str(m.get("scene_labels_source") or "").strip():
            m["scene_labels_source"] = str(cand.get("scene_labels_source") or "")
        if str(m.get("description") or "").strip() or (m.get("scene_tags_zh") or []):
            filled += 1
    return filled


def ensure_code_broll_pool(
    pipeline_dir: Path,
    sentences: List[Dict[str, Any]],
    existing_brolls: List[Dict[str, Any]],
    track: str,
    style_id: Optional[str] = None,
    total_dur: float = 0.0,
) -> List[Dict[str, Any]]:
    """若素材池中的 B-roll 少于 2 条或可用净长不足以单向前推覆盖全片，按当前风格谱生成补镜备选。"""
    pool = list(existing_brolls)
    gen_dir = pipeline_dir / "04_generated_broll"
    gen_dir.mkdir(parents=True, exist_ok=True)
    pal = resolve_style_palette(style_id, track=track)
    net_avail = sum(max(0.0, float(m.get("duration_sec", 0.0)) - 0.80) for m in existing_brolls)

    if len(pool) < 2 or (total_dur > 0.0 and net_avail < total_dur * 1.15):
        first_kw_title = sentences[0]["keywords"][0] if sentences and sentences[0].get("keywords") else "核心真相"
        p1 = gen_dir / "auto_code_kinetic.mp4"
        render_broll_clip_pillow(
            p1,
            mode="kinetic_text",
            title=first_kw_title if len(first_kw_title) >= 2 else "开篇必看",
            items=[{"label": s["text"][:16], "subtitle": "真实数据与现场拆解"} for s in sentences[:2]],
            duration_sec=3.0,
            accent_hex=pal["accent_hex"],
            bg_hex=pal["bg_hex"],
            style_id=style_id,
            track=track,
        )
        pool.append({
            "id": "code_auto_01",
            "normalized_mp4": str(p1.resolve()),
            "role": "code_broll",
            "duration_sec": 3.0,
            "best_in_sec": 0.0,
            "sharpness_score": 80.0,
            "stability_score": 1.0,
            "semantic_tags": ["观点", "关键事实", *(sentences[0].get("keywords", []) if sentences else [])],
            "scene_group": "code_gfx",
        })

        p2 = gen_dir / "auto_code_flow.mp4"
        render_broll_clip_pillow(
            p2,
            mode="flow_diagram",
            title="核心逻辑拆解",
            items=[
                {"label": s["keywords"][0] if s.get("keywords") else f"要点 {i+1}", "subtitle": s["text"][:20]}
                for i, s in enumerate(sentences[1:4])
            ] or [{"label": "真实细节", "subtitle": "拒绝套路"}],
            duration_sec=3.5,
            accent_hex=pal["accent_hex"],
            bg_hex=pal["bg_hex"],
            style_id=style_id,
            track=track,
        )
        pool.append({
            "id": "code_auto_02",
            "normalized_mp4": str(p2.resolve()),
            "role": "code_broll",
            "duration_sec": 3.5,
            "best_in_sec": 0.0,
            "sharpness_score": 80.0,
            "stability_score": 1.0,
            "semantic_tags": ["流程", "拆解", "细节"],
            "scene_group": "code_gfx",
        })
        deficit = (total_dur * 1.15 - (net_avail + 6.5)) if total_dur > 0.0 else 0.0
        extra_modes = ("chips", "versus", "kinetic_text", "flow_diagram")
        extra_idx = 0
        while deficit > 1.5 and extra_idx < 4 and len(sentences) > 0:
            s_pick = sentences[min(len(sentences) - 1, (extra_idx * 2 + 1) % len(sentences))]
            e_dur = 4.0
            e_path = gen_dir / f"auto_code_ext_{extra_idx + 1:02d}.mp4"
            e_mode = extra_modes[extra_idx % len(extra_modes)]
            e_kw = (s_pick.get("keywords") or [None])[0] or str(s_pick.get("text", "核心要点"))[:8]
            e_items = [
                    {"label": str((s.get("keywords") or [None])[0] or s["text"][:10]), "subtitle": s["text"][:18]}
                for s in sentences[extra_idx % len(sentences) : (extra_idx % len(sentences)) + 2]
            ] or [{"label": e_kw, "subtitle": str(s_pick.get("text", ""))[:18]}]
            render_broll_clip_pillow(
                e_path,
                mode=e_mode,
                title=str(e_kw)[:12],
                items=e_items,
                duration_sec=e_dur,
                accent_hex=pal["accent_hex"],
                bg_hex=pal["bg_hex"],
                style_id=style_id,
                track=track,
            )
            pool.append({
                "id": f"code_auto_ext_{extra_idx + 1:02d}",
                "normalized_mp4": str(e_path.resolve()),
                "role": "code_broll",
                "duration_sec": e_dur,
                "best_in_sec": 0.0,
                "sharpness_score": 80.0,
                "stability_score": 1.0,
                "semantic_tags": ["要点", "对比", *(s_pick.get("keywords") or [])],
                "scene_group": "code_gfx",
            })
            deficit -= e_dur
            extra_idx += 1
    return pool


def _build_eye_locked_vf(eye_anchor: Optional[Dict[str, Any]], zoom: float = 1.15) -> str:
    """商业剪辑标准锁眼微推拉（1.15x 确保景别明显拉近而不产生抽搐感）。"""
    cx = float((eye_anchor or {}).get("cx", (eye_anchor or {}).get("eye_cx", 0.50)))
    cy = float((eye_anchor or {}).get("cy", (eye_anchor or {}).get("eye_cy", 0.36)))
    zw = (int(round(CANVAS_WIDTH * zoom)) // 2) * 2
    zh = (int(round(CANVAS_HEIGHT * zoom)) // 2) * 2
    crop_x = max(0, min(zw - CANVAS_WIDTH, int(round(cx * (zw - CANVAS_WIDTH)))))
    crop_y = max(0, min(zh - CANVAS_HEIGHT, int(round(cy * (zh - CANVAS_HEIGHT)))))
    return f"scale={zw}:{zh}:flags=lanczos,crop={CANVAS_WIDTH}:{CANVAS_HEIGHT}:{crop_x}:{crop_y}"


def _apply_keyframe_policy(clips: List[Dict[str, Any]], kf_per_min: float, total_dur: float) -> int:
    """
    按风格实测关键帧频次给分镜挂载推/拉镜（每次推或拉记 2 个关键帧）：
    0 = 机位完全不动；>=80 = 几乎每镜都推拉（1.00↔1.14）；其余按预算挑最长的镜头做温和呼吸（1.00↔1.07）。
    code_broll（程序生成图卡）与带锁眼放大的镜头不参与。返回挂载数量。
    """
    for c in clips:
        c["ken_burns"] = None
    if kf_per_min <= 0.0:
        return 0
    cands = [c for c in clips if c.get("track_role") != "code_broll" and not c.get("vf_extra")]
    if not cands:
        return 0
    # 关键帧密度 ≠ 推镜密度。2026-11-24 实测（style_21 真草稿）：主轨 13 镜里只有 4 镜有推镜、
    # 共 8 个缩放关键帧点，主轨机位基本是**固定半身**（常驻 scale 1.2），推镜是点缀不是常态。
    # 旧代码把「92.2 关键帧/分」当成「92.2 次推镜/分」→ 51 镜里 49 镜全挂缓推，
    # 观感就是「每一镜都在呼吸」，这正是「看着乱」的头号来源。
    # 现在：只挑最长的镜头给预算，且比例上限 PUSH_SHOT_RATIO；主轨更少（推镜优先给 B-roll）。
    aroll = [c for c in cands if c.get("track_role") == "a_roll"]
    broll = [c for c in cands if c.get("track_role") != "a_roll"]
    budget_total = max(1, int(round(len(cands) * PUSH_SHOT_RATIO)))
    budget_a = int(round(len(aroll) * PUSH_SHOT_RATIO_AROLL))
    if aroll and budget_a == 0:
        budget_a = 1  # 全 A-roll 的风格（如 style_08）也要有推镜，否则整片机位死板
    if not broll:
        budget_a = max(budget_a, budget_total)
    budget_b = max(0, budget_total - budget_a)
    chosen = (
        sorted(aroll, key=lambda c: -float(c.get("duration_sec", 0.0)))[:budget_a]
        + sorted(broll, key=lambda c: -float(c.get("duration_sec", 0.0)))[:budget_b]
    )
    chosen_ids = {id(c) for c in chosen}
    peak = 1.08
    flip = 0
    for c in clips:
        if id(c) not in chosen_ids:
            continue
        push = (flip % 2 == 0)
        c["ken_burns"] = {
            "zoom_start": 1.0 if push else peak,
            "zoom_end": peak if push else 1.0,
            "reason": f"风格关键帧密度 {kf_per_min:g}/分钟 · {'推镜' if push else '拉镜'}",
        }
        flip += 1
    return len(chosen)


# 能当切点用的事件类型（与旧的事件间隔循环同口径）
_GRAMMAR_CUT_EVENT_TYPES = ("sentence_start", "pause_end", "pause_start",
                            "keyword_accent", "stress", "bgm_section", "bgm_downbeat", "bgm_beat")


def _event_cut_prio(ev: Dict[str, Any], prop_sent_ids: set) -> int:
    """候选事件的优先级（越小越优先）：台词点到道具的句界最该切，气口次之，重音最后。

    只在「离理想刀位同样近」时用于并列裁决，不会为了道具把节奏打乱。
    """
    t = str(ev.get("type") or "")
    if t == "sentence_start" and ev.get("sentence_id") in prop_sent_ids:
        return 0
    if t == "sentence_start":
        return 1
    if t in ("pause_end", "pause_start"):
        return 2
    if t == "keyword_accent":
        return 3
    return 4


def _pick_choreography_opening_events(
    events: List[Dict[str, Any]], fps: int, total_frames: int, count: int, prop_sent_ids: set,
) -> List[Dict[str, Any]]:
    """按帧选开场事件，优先留够能落地的切点，同帧再按语音事件优先级裁决。"""
    candidates = sorted(
        (ev for ev in events if ev.get("type") in _GRAMMAR_CUT_EVENT_TYPES
         and int(ev["frame"]) < 3 * fps and total_frames - int(ev["frame"]) >= 10),
        key=lambda ev: (int(ev["frame"]), _event_cut_prio(ev, prop_sent_ids)),
    )
    picked = []
    last_frame = 0
    min_gap_frames = max(12, math.ceil(0.40 * fps))
    for ev in candidates:
        if len(picked) >= count:
            break
        if int(ev["frame"]) - last_frame >= min_gap_frames:
            picked.append(ev)
            last_frame = int(ev["frame"])
    return picked


def _broll_tags(item: Dict[str, Any]) -> List[str]:
    return [
        str(tag).strip()
        for key in ("semantic_tags", "manual_tags", "scene_tags_zh", "tags")
        for tag in (item.get(key) or [])
        if str(tag).strip()
    ]


def _has_opening_broll_evidence(item: Dict[str, Any], text: str) -> bool:
    tags = _broll_tags(item)
    if any(tag in text for tag in tags):
        return True
    # 允许标签或描述用同一实物的近义名称，例如「虾饺」和「饺子」。
    spoken = broll_match.concepts_detail(text)
    visible = broll_match.concepts_detail(" ".join([str(item.get("description") or ""), *tags]))
    return any(strength >= 1.0 and concept in spoken and spoken[concept][1] >= 1.0
               for concept, (_, strength) in visible.items())


_SIGNBOARD_WALL_NOISE_CUES = ("宣传墙", "围挡", "广告墙", "广告牌", "海报墙")
_SIGNBOARD_SPOKEN_ALLOW_CUES = ("招牌", "广告", "宣传", "标语", "海报", "墙")
_DEICTIC_STUB_RE = re.compile(r"^(?:𠮶边|那边|呢边|这边|这里|呢度|那里)?(?:就系|就是)?(?:我哋|我们)?嘅?$")


def _is_signboard_wall_noise(item: Dict[str, Any], spoken_text: str) -> bool:
    """台词未提招牌/广告墙时，识别纯宣传围挡/标语墙镜头以免字面蹭词抢占真实空间画面。"""
    spoken = str(spoken_text or "")
    if any(tok in spoken for tok in _SIGNBOARD_SPOKEN_ALLOW_CUES):
        return False
    haystack = " ".join([str(item.get("description") or ""), *_broll_tags(item)])
    return any(tok in haystack for tok in _SIGNBOARD_WALL_NOISE_CUES)


def _is_deictic_pointing_stub(text: str, duration_sec: float) -> bool:
    """识别 <1.25s 且无具体实体的指向过渡短句（如「𠮶边就系我哋嘅」），锁留 A-roll 承接指代动作。"""
    if float(duration_sec or 0.0) >= 1.25:
        return False
    clean = re.sub(r"[\s，。！？；：、,.!?;:]+", "", str(text or ""))
    return bool(clean) and bool(_DEICTIC_STUB_RE.fullmatch(clean))


def _prune_cut_points_to_budget(cut_points: List[Dict[str, Any]], target_shots: int,
                               shot_max_sec: float, fps: int, total_dur: float,
                               can_merge=None) -> tuple:
    """刀的「数量」按风格真值收敛：超预算时合并相邻镜头（删掉影响力最小的那刀）。

    删除规则（从保守到激进都不越界）：
      - 不动开场（t<3s 的刀一个不删，开场刀数必须等于草稿真值）
      - 句界切点只允许在「同一个源、首尾相接」时合并（连续说完两句的长镜是合法 A-roll；
        跳剪源合并会口型对不上）——由 can_merge 回调判，不给回调就一律不动句界
      - 合并后超过目标最长镜上限的一刀不删（不许为了凑数量把镜头拉成超长镜）
      - 每次删「合并后最短」的那一刀，即先合并最短的相邻对，把真实分布里的碎镜收掉
      - 事件优先级低（bgm/pause）的优先于 keyword_accent
    被合并的镜头在 cut_points 上打 `budget_merged=True`，下游 A-roll 安全校验据此放行连续长镜。
    返回 (cut_points, note)。
    """
    if target_shots < 1 or len(cut_points) <= target_shots:
        return cut_points, ""
    prio_map = {"sentence_start": 0, "pause_end": 1, "pause_start": 2,
                "keyword_accent": 3, "stress": 4, "bgm_section": 5, "bgm_downbeat": 6}
    pruned = 0
    while len(cut_points) > target_shots and len(cut_points) > 2:
        best_i, best_score = None, None
        for i in range(1, len(cut_points)):
            t_cut = frames_to_sec(cut_points[i]["cut_frame"], fps)
            if t_cut < 3.0:
                continue  # 开场保护
            if str(cut_points[i]["event"].get("type")) == "sentence_start" and not (
                    can_merge and can_merge(i)):
                continue  # 句界保护（只有同源首尾相接才允许合并）
            prev_t = frames_to_sec(cut_points[i - 1]["cut_frame"], fps)
            nxt = (frames_to_sec(cut_points[i + 1]["cut_frame"], fps)
                   if i + 1 < len(cut_points) else total_dur)
            merged = nxt - prev_t
            if merged > shot_max_sec + 1e-6:
                continue
            prio = prio_map.get(str(cut_points[i]["event"].get("type")), 9)
            score = (round(merged, 3), -prio)
            if best_score is None or score < best_score:
                best_i, best_score = i, score
        if best_i is None:
            break
        cut_points.pop(best_i)
        cut_points[best_i - 1]["budget_merged"] = True
        pruned += 1
    note = (f"按风格目标 {target_shots} 镜收敛，合并掉 {pruned} 刀（优先合并最短的相邻镜，开场刀不动）"
            if pruned else "")
    return cut_points, note


def summarize_broll_semantic(clips: List[Dict[str, Any]]) -> Dict[str, Any]:
    """把逐镜的 `sem_match` 汇总成 EDL 的 `broll_semantic`（含全屏 B-roll 与分层底图 B-roll）。"""
    rows: List[Dict[str, Any]] = []
    for c in clips:
        is_layered_bg = bool(c.get("b_roll_overlay")) and bool((c.get("pip_layout") or {}).get("secondary_media_path"))
        if str(c.get("track_role")) not in ("b_roll", "code_broll") and not is_layered_bg:
            continue
        rel = c.get("sem_match") or {}
        bg_mp = (c.get("pip_layout") or {}).get("secondary_media_path") if is_layered_bg else c.get("media_path")
        rows.append({
            "clip_id": c.get("clip_id"),
            "role": "b_roll_bg" if is_layered_bg else c.get("track_role"),
            "timeline_in_sec": c.get("timeline_in_sec"),
            "sentence": c.get("sentence_text"),
            "media": Path(str(bg_mp or "")).name,
            "score": rel.get("score"),
            "tier": rel.get("tier") or rel.get("verdict") or "none",
            "hits": rel.get("hits") or rel.get("basis") or [],
            "why": rel.get("why") or "",
            "source": rel.get("source") or "rules",
        })
    return broll_match.summarize_choices(rows)


def run_step4(
    pipeline_dir: Path,
    track: str = "store",
    j_cut_frames: int = 3,
    fps: int = DEFAULT_FPS,
    prefer_broll_only: bool = False,
    style_id: Optional[str] = None,
    account_profile: Optional[Dict[str, Any]] = None,
    edit_profile: str = "talking",
    grammar: str = "style",
    allow_broll_mismatch: bool = False,
    enforce_broll_gate: bool = False,
) -> Dict[str, Any]:
    raw_jc = int(j_cut_frames)
    j_cut_frames = 0 if raw_jc <= 0 else max(2, min(4, raw_jc))
    if not style_id:
        style_id = load_style_applied(pipeline_dir)

    manifest = read_json(pipeline_dir / "01_manifest.json")
    audio_events = read_json(pipeline_dir / "03_audio_events.json")
    sentences = audio_events["sentences"]
    events = audio_events["events"]
    total_dur = float(audio_events["locked_duration_sec"])
    total_frames = int(audio_events["locked_duration_frames"])

    active_items = _apply_excluded_rules_to_items(pipeline_dir, manifest.get("items") or [])
    arolls = [m for m in active_items if m["role"] == "a_roll"]
    usable_arolls = [m for m in arolls if m.get("video_grade", "usable") == "usable"]
    usable_aroll_paths = {str(m["normalized_mp4"]) for m in usable_arolls}
    media_by_mp4 = {str(m.get("normalized_mp4")): m for m in manifest["items"]}
    vgrade_by_mp4 = {str(m.get("normalized_mp4")): str(m.get("video_grade", "usable")) for m in manifest["items"]}
    brolls_raw = [m for m in active_items if m["role"] in ("b_roll", "code_broll")]
    # 画面描述（抽帧判读结果）不在 manifest 里，必须从盘点/视觉索引补进来，否则语义选片无从谈起
    n_hydrated = _hydrate_vision_semantics(pipeline_dir, brolls_raw)
    if n_hydrated:
        print(f"[step4] 已接入抽帧判读画面描述 {n_hydrated}/{len(brolls_raw)} 条 B-roll（选片按台词语义排序）")
    else:
        print("[step4] [WARN] 没有可用的画面描述（00_inventory/00_visual_index 缺 description），"
              "B-roll 只能按质量轮转，对位会大面积记 none")
    broll_pool = ensure_code_broll_pool(pipeline_dir, sentences, brolls_raw, track, style_id=style_id, total_dur=total_dur)

    contract = None
    choreography_applied = None
    if style_id in ("style_01", "style_21"):
        try:
            from style_01_21_choreographer import (
                STYLE_RHYTHM_CONTRACTS,
                plan_style_01_rhythm,
                plan_style_21_rhythm,
            )

            contract = STYLE_RHYTHM_CONTRACTS[style_id]
        except Exception as exc:
            reason = "风格节奏契约加载失败，沿用原有剪辑规则"
            print(f"[step4] [WARN] {reason}（{type(exc).__name__}）")
            choreography_applied = {"style_id": style_id, "enabled": False, "planned_degrade_reason": reason}
        else:
            planner = plan_style_21_rhythm if style_id == "style_21" else plan_style_01_rhythm
            choreo_plan = planner(sentences, broll_pool, fps)
            choreography_applied = {
                "style_id": style_id,
                "enabled": True,
                "broll_target_ratio": choreo_plan["broll_target_ratio"],
                "punch_in_scale": contract["punch_in_scale"],
                "opening_cuts_3s_target": contract["opening_cuts_3s"],
                "aroll_speed_locked": True,
                "planned_degrade_reason": choreo_plan["degrade_reason"],
            }
    fast_choreography = bool(contract and style_id == "style_21")
    choreography_broll = bool(contract and not prefer_broll_only)
    live_brolls_for_choreography = [m for m in broll_pool if m["role"] == "b_roll"] if choreography_broll else []

    def _score_broll(
        text: str,
        item: Dict[str, Any],
        *,
        keywords: Optional[List[str]] = None,
        used_count: int = 0,
        need_sec: Optional[float] = None,
    ) -> Dict[str, Any]:
        score = broll_match.score_clip(text, item, keywords=keywords, used_count=used_count, need_sec=need_sec)
        if (
            fast_choreography and choreography_broll
            and item["role"] == "b_roll" and score["tier"] == broll_match.TIER_NONE
        ):
            # 台词明确讲到素材标签时，词表未收录的同场景依据也可以用于选镜。
            visible = broll_match.describe_clip(item)
            tags = [
                tag for tag in dict.fromkeys(_broll_tags(item))
                if len(tag) >= 2 and tag in text and tag in visible
                and tag not in ("实拍", "空镜", "近景", "特写", "全景", "中景", "远景")
            ][:6]
            if tags:
                weak = broll_match.WEAK_MIN
                score.update(
                    rank=round(score["rank"] + (weak - score["score"]) * 100, 2),
                    score=weak, tier=broll_match.TIER_WEAK,
                    hits=[f"素材标签「{tag}」" for tag in tags],
                    why=f"画面标签与台词相同（{'、'.join(tags)}）",
                )
        return score

    deictic_gaps = detect_script_media_gaps(sentences, broll_pool)
    if deictic_gaps:
        print(f"[step4] [WARN] 镜头与台词缺口预警 {len(deictic_gaps)} 处: {[g['matched_cue'] for g in deictic_gaps]}")

    raw_intel_p = pipeline_dir / "00_raw_intel.json"
    raw_intel = read_json(raw_intel_p) if raw_intel_p.is_file() else {}
    raw_pain_kws = [str(k) for k in (raw_intel.get("pain_keywords") or []) if str(k).strip()]
    raw_proof_names = [Path(str(a.get("filename") or "")).stem for a in (raw_intel.get("proof_assets") or []) if a.get("filename")]

    style_entry = load_style_spectrum_entry(style_id) if style_id else None
    # 风格「剪辑刀法」真值：style 模式读 forensic/style_edit_grammar.json；dynamic 是逃生门（旧行为）
    grammar_mode = grammar if grammar in ("style", "dynamic") else "style"
    gram = load_grammar(style_id) if (style_id and grammar_mode == "style") else None
    if gram is not None and fast_choreography:
        gram = {**gram, "shot": {**gram["shot"], "p50": contract["median_shot_sec"]}}
    # 镜头预算先算出来：挑切点时要用（目标刀数 / 单镜时长带 / 快切配额 / 开场刀数）
    shot_budget: Optional[Dict[str, Any]] = plan_shot_budget(total_dur, gram) if gram is not None else None
    grammar_notes: List[str] = []
    kinetic_stage_mode = bool(style_id and kinetic_choreographer.is_kinetic_stage_style(style_id))
    kinetic_stage_plan: Dict[str, Any] = (
        kinetic_choreographer.plan_kinetic_stage(
            audio_events, style_id=style_id, pipeline_dir=pipeline_dir, fps=fps,
        )
        if kinetic_stage_mode
        else {"enabled": False, "groups": [], "items": [], "matting_windows": [], "stats": {}}
    )
    kinetic_locked_spans: List[Tuple[float, float]] = [
        (float(g["start_sec"]), float(g["end_sec"]))
        for g in (kinetic_stage_plan.get("groups") or [])
        if g.get("a_roll_locked")
    ]
    kinetic_group_by_sentence = {
        sid: group for group in kinetic_stage_plan.get("groups", []) for sid in group["sentence_ids"]
    }

    def _overlaps_kinetic_locked(t_s: float, t_e: float) -> bool:
        return any(min(t_e, g_e) - max(t_s, g_s) >= 0.12 for g_s, g_e in kinetic_locked_spans)

    def _overlaps_matting_window(t_s: float, t_e: float) -> bool:
        return any(
            min(t_e, float(w["end_sec"])) - max(t_s, float(w["start_sec"])) >= 0.18
            for w in (kinetic_stage_plan.get("matting_windows") or [])
        )

    broll_prior_ratio = (choreography_applied["broll_target_ratio"] if contract
                         else float((style_entry or {}).get("broll_overlay_ratio_target", 0.53)))
    # 风格里的 B-roll 占比只是先验：实际配比按素材能力动态调整（用户明确要求，不可刻板执行模版比例）
    broll_target_hard = bool((style_entry or {}).get("broll_target_hard", False))
    contract_band = [r * 100 for r in contract["broll_ratio_band"]] if contract else None
    band_raw = (style_entry or {}).get("broll_target_band_pct") or contract_band or [
        max(0.0, (broll_prior_ratio - 0.15) * 100),
        min(100.0, (broll_prior_ratio + 0.15) * 100),
    ]
    broll_band = (max(0.0, float(band_raw[0]) / 100.0), min(1.0, float(band_raw[1]) / 100.0))
    if kinetic_stage_mode:
        _kdna = kinetic_choreographer.get_style_dna(style_id) or {}
        _kbp = _kdna.get("broll_policy") or {}
        if brolls_raw:
            broll_prior_ratio = float(_kbp.get("target_ratio", 0.22))
            _kband = _kbp.get("band") or (0.0, 0.30)
            broll_band = (max(0.0, float(_kband[0])), min(0.30, float(_kband[1])))
        else:
            broll_prior_ratio = 0.0
            broll_band = (0.0, 0.0)
        broll_target_hard = False
    broll_policy_note = ""
    ambient_policy = (style_entry or {}).get("broll_ambient_sound_policy") or {}
    ambient_enabled = bool(ambient_policy.get("enabled", True))
    ambient_gain_db = float(ambient_policy.get("gain_db", -10.0))
    ambient_words = tuple(ambient_policy.get("trigger_action_words") or ("敲", "响", "滋啦", "切", "倒", "沸", "烤", "炒", "砸", "摔"))

    eff_prof = account_profile if (account_profile and account_profile.get("sample_sufficient") is True) else {}
    min_shot_gap = max(0.95, min(2.4, float(eff_prof.get("target_median_shot_sec", 1.55)) * 0.85))
    pacing = dict(EDIT_PROFILES[edit_profile])
    max_shot = float(pacing["max_shot_sec"])
    micro_hook_max_sec = min(max_shot, float(eff_prof.get("micro_hook_max_sec", max_shot - 1.8)))
    # ── 动态配比：先验 → 按实际素材能力收敛，并记录原因 ──
    aroll_capacity = min(1.0, sum(float(m.get("duration_sec") or 0) for m in usable_arolls) / max(total_dur, 0.1))
    capacity_pool = live_brolls_for_choreography if choreography_broll else broll_pool
    broll_capacity = min(1.0, sum(float(m.get("duration_sec") or 0) for m in capacity_pool) / max(total_dur, 0.1))
    broll_target_ratio = broll_prior_ratio
    if kinetic_stage_mode and not brolls_raw:
        broll_target_ratio = 0.0
        broll_policy_note = f"{style_id} 动态花字舞台模式无外插实拍素材，保持纯口播 0% B-roll"
    elif usable_arolls and aroll_capacity < (1.0 - broll_prior_ratio) - 0.02:
        broll_target_ratio = min(1.0, 1.0 - aroll_capacity)
        broll_policy_note = f"A-roll 可用时长只够 {aroll_capacity*100:.0f}%，B-roll 上调到 {broll_target_ratio*100:.0f}%"
    elif broll_pool and broll_capacity < broll_prior_ratio - 0.02:
        broll_target_ratio = broll_capacity
        broll_policy_note = f"B-roll 素材只够覆盖 {broll_capacity*100:.0f}%，目标下调"
    if not broll_target_hard:
        broll_target_ratio = max(broll_band[0], min(broll_band[1], broll_target_ratio)) if broll_capacity >= broll_band[0] else broll_target_ratio
        if broll_target_ratio > broll_band[1]:
            broll_target_ratio = broll_capacity if broll_capacity < broll_target_ratio else broll_target_ratio
    broll_target_ratio = max(0.0, min(1.0, broll_target_ratio))
    if not broll_policy_note:
        broll_policy_note = f"落在风格动态带 {broll_band[0]*100:.0f}%–{broll_band[1]*100:.0f}% 内（先验 {broll_prior_ratio*100:.0f}%）"


    default_aroll_r = max(0.0, min(0.80, 1.0 - broll_target_ratio))
    target_aroll_ratio = max(0.0, min(0.85, float(eff_prof.get("target_aroll_ratio", default_aroll_r))))
    broll_only_mode = bool(prefer_broll_only or (broll_target_ratio >= 0.995) or (target_aroll_ratio <= 0.005))

    # 风格专属「A/B-roll 切换语法」：切镜频率 / 关键帧推拉密度 / 开场切数 / 切换样式，均来自白盒草稿实测值
    ab_spec = dict((style_entry or {}).get("ab_switch_spec") or {})
    ab_pattern = str(ab_spec.get("pattern") or "")
    # 账号画像不许吃掉风格刀法（用户明确要求：未经同意不许偏离风格标准）。
    # style 模式：刀法只认风格（grammar）与风格库，画像仅在风格值缺失时兜底；
    # dynamic 模式：保留旧行为（有足够画像就按画像走），留作逃生门。
    use_style_timing = bool(gram)
    use_ab_timing = use_style_timing or (bool(ab_spec) and not eff_prof)
    opening_cuts_conflict = ""
    zero_broll_mode = (bool(ab_spec) and broll_target_ratio <= 0.01 and bool(usable_arolls)
                       and not prefer_broll_only and not choreography_broll)
    if use_ab_timing:
        if use_style_timing:
            # 裁决开场刀数：以草稿切点直接数出来的 opening_structure.first_3s_cuts_count 为准
            #（grammar 已把它换算成「切点数」= 镜头数-1），ab_switch_spec.opening_cuts 降级为兜底。
            med_target = max(0.55, float(gram["shot"].get("p50") or 1.55))
            open_cuts = int(gram["opening"].get("cuts_in_first_3s") or 0)
            ab_open = ab_spec.get("opening_cuts")
            if ab_open is not None and int(ab_open) != open_cuts:
                opening_cuts_conflict = (
                    f"开场刀数按草稿切点取 {open_cuts} 刀（opening_structure 实测）；"
                    f"ab_switch_spec.opening_cuts={int(ab_open)} 与草稿不符，已降级为兜底"
                )
                grammar_notes.append(opening_cuts_conflict)
            # 刀法落到节奏参数：下沿用真值 p10（允许的最短镜），上沿用真值 p90×1.3（超长必切），
            # 中位用 p50。只拿中位数一个点去拧，等于把「分布」当「常数」，快切和长镜两头都丢了。
            p10_v = float(gram["shot"].get("p10") or med_target * 0.6)
            p90_v = float(gram["shot"].get("p90") or med_target * 1.6)
            min_shot_gap = max(0.50, p10_v * 0.85)
            sentence_gap = max(0.50, min(p10_v * 0.90, 6.0))
            short_cap = max(1.20, min(float(pacing["max_shot_sec"]), p90_v * 1.3))
        else:
            med_target = max(0.8, float(ab_spec.get("target_median_shot_sec", 1.55)))
            open_cuts = int(ab_spec.get("opening_cuts", pacing["opening_cuts"]))
            min_shot_gap = max(0.60, med_target * 0.78)
            sentence_gap = max(0.60, min(med_target * 0.62, 6.0))
            short_cap = max(float(pacing["max_shot_sec"]), med_target * 2.2)
        pacing["sentence_cut_gap"] = sentence_gap
        pacing["max_shot_sec"] = short_cap
        max_shot = float(pacing["max_shot_sec"])
        micro_hook_max_sec = min(max_shot, max(med_target * 2.0, 2.6))
        pacing["opening_cuts"] = open_cuts
        if zero_broll_mode:
            # 0% B-roll 长镜风：只在句界硬切（锁定音轨已切净气口，A-roll 不能跨句连续），不再人为插入中途切点
            min_shot_gap = 0.60
            pacing["sentence_cut_gap"] = 0.60
            micro_hook_max_sec = max_shot
    elif eff_prof and grammar_mode == "style":
        # style 模式下风格值完全缺失时，才轮到画像/档案兜底（并把这件事写进 notes）
        grammar_notes.append("风格刀法真值缺失，镜头节奏降级到账号画像兜底")
    kf_target = float(ab_spec["kf_per_min_target"]) if ("kf_per_min_target" in ab_spec) else None
    allow_punch = kf_target is None or kf_target > 0.0
    punch_scale = contract["punch_in_scale"] if contract else 1.15
    if fast_choreography:
        punch_scale = max(1.18, min(1.22, punch_scale))
    if contract:
        # 契约计镜头切片，选刀时减去从第 0 帧起的首镜。
        pacing["opening_cuts"] = max(int(pacing["opening_cuts"]), int(contract["opening_cuts_3s"]) - 1)

    sent_by_id = {s["id"]: s for s in sentences}
    prop_sent_ids = {s["id"] for s in sentences if any(cue in s.get("text", "") for cue in PROP_SHOW_CUES)}
    first_3s_cutoff_f = 3 * fps
    opening_cut_target = int(pacing["opening_cuts"])
    first_3s_evts = pick_first_3s_events(events, fps, opening_cut_target)
    if fast_choreography or (contract and len(first_3s_evts) < opening_cut_target):
        first_3s_evts = _pick_choreography_opening_events(events, fps, total_frames, opening_cut_target, prop_sent_ids)

    later_candidates = [
        e for e in events
        if e["time_sec"] > 2.85
        and (not contract or int(e["frame"]) >= first_3s_cutoff_f)
        and e["time_sec"] < total_dur - 0.80
        and e["type"] in ("pause_start", "sentence_start", "pause_end", "keyword_accent", "stress", "bgm_section", "bgm_downbeat")
    ]
    prio_map = {
        "sentence_start": 0, "pause_end": 1, "pause_start": 2,
        "keyword_accent": 3, "stress": 4, "bgm_section": 5, "bgm_downbeat": 6,
    }
    later_candidates.sort(key=lambda e: (e["frame"], 0 if (e.get("type") == "sentence_start" and e.get("sentence_id") in prop_sent_ids) else 1, prio_map.get(e["type"], 9)))

    if gram is not None and shot_budget:
        # ── style 模式：挑切点交给 edit_grammar.select_cuts（节奏式挑法）──
        # 目标镜头数、每镜时长带、快切配额、开场刀数全部来自该套草稿真值；这里只做两件事：
        # 把音频事件转成候选（带 prop_show 优先级），再把挑中的事件带回原流水线。
        cand_by_time: Dict[float, Dict[str, Any]] = {}
        for e in events:
            if str(e.get("type")) not in _GRAMMAR_CUT_EVENT_TYPES:
                continue
            t_c = round(float(e["time_sec"]), 3)
            if t_c < 0.15 or t_c > total_dur - 0.20:
                continue
            prio = _event_cut_prio(e, prop_sent_ids)
            prev_c = cand_by_time.get(t_c)
            if prev_c is None or prio < int(prev_c["prio"]):
                cand_by_time[t_c] = {"time_sec": t_c, "prio": prio, "event": e,
                                     "sentence_id": e.get("sentence_id")}
        selection_grammar = gram
        if (fast_choreography and not choreo_plan["degrade_reason"]
                and len(cand_by_time) >= shot_budget["target_shots"]):
            # 正文只借用参考镜长，select_cuts 仍从真实音频事件取切点。
            preferred_lengths = [
                seg["duration_sec"] for seg in choreo_plan["segments"][:-1]
                if seg["start_sec"] >= 3.0
            ]
            if preferred_lengths:
                selection_grammar = {**gram, "shot": {**gram["shot"], "durations_chrono": preferred_lengths}}
        plan = select_cuts(list(cand_by_time.values()), shot_budget, selection_grammar)
        chosen_cuts = [c["candidate"]["event"] for c in plan["cuts"] if c.get("candidate")]
        plan_notes = [str(note) for note in (plan.get("notes") or [])]
        if contract:
            grammar_opening_count = sum(int(e["frame"]) < first_3s_cutoff_f for e in chosen_cuts)
            if fast_choreography or len(first_3s_evts) > grammar_opening_count:
                chosen_cuts = first_3s_evts + [e for e in chosen_cuts if int(e["frame"]) >= first_3s_cutoff_f]
                plan_notes = [n for n in plan_notes if not n.startswith("前 3s 可用事件不足")]
                if len(first_3s_evts) < opening_cut_target:
                    plan_notes.append(f"前 3s 可用事件不足：开场只排到 {len(first_3s_evts)}/{opening_cut_target} 刀")
        grammar_notes.extend(plan_notes)
        grammar_notes.append(
            f"挑切点走风格节奏式挑法：目标 {shot_budget['target_shots']} 镜 / "
            f"快切配额 {shot_budget['fast_quota']} / 开场 {shot_budget['opening_cuts']} 刀 / "
            f"实际挑出 {len(chosen_cuts)} 刀"
        )
    else:
        chosen_cuts: List[Dict[str, Any]] = list(first_3s_evts)
        last_cut_sec = chosen_cuts[-1]["time_sec"] if chosen_cuts else 0.0
        n_cand = len(later_candidates)
        skip_until_idx = -1
        for ci, cand in enumerate(later_candidates):
            if ci < skip_until_idx:
                continue
            eff_cand = cand
            if cand.get("type") != "sentence_start":
                best_nxt: Optional[Dict[str, Any]] = None
                best_idx = -1
                for look_j in range(ci + 1, n_cand):
                    nxt = later_candidates[look_j]
                    if nxt["time_sec"] - cand["time_sec"] > 0.90:
                        break
                    if nxt.get("type") != "sentence_start":
                        continue
                    if nxt.get("sentence_id") in prop_sent_ids:
                        best_nxt = nxt
                        best_idx = look_j
                        break
                    if best_nxt is None and (nxt["time_sec"] - last_cut_sec <= min(micro_hook_max_sec, 4.5)):
                        best_nxt = nxt
                        best_idx = look_j
                if best_nxt is not None:
                    eff_cand = best_nxt
                    skip_until_idx = best_idx + 1

            is_sent_start = eff_cand.get("type") == "sentence_start"
            gap_needed = pacing["sentence_cut_gap"] if is_sent_start else max(min_shot_gap, pacing["sentence_cut_gap"])
            if eff_cand["time_sec"] - last_cut_sec >= gap_needed:
                prev_ev = chosen_cuts[-1] if chosen_cuts else None
                if prev_ev and prev_ev.get("sentence_id") in prop_sent_ids and eff_cand.get("sentence_id") == prev_ev.get("sentence_id"):
                    continue
                chosen_cuts.append(eff_cand)
                last_cut_sec = eff_cand["time_sec"]

    if chosen_cuts and gram is None:
        guarded_cuts: List[Dict[str, Any]] = []
        prev_t = 0.0
        for ev in chosen_cuts + [{"time_sec": total_dur, "frame": total_frames, "type": "_end"}]:
            while ev["time_sec"] - prev_t > micro_hook_max_sec:
                mid_ev = next(
                    (
                        c for c in events
                        if prev_t + 1.5 <= c["time_sec"] <= min(ev["time_sec"] - 1.2, prev_t + micro_hook_max_sec)
                        and c["type"] in ("keyword_accent", "stress", "pause_start", "sentence_start", "bgm_downbeat", "bgm_beat")
                    ),
                    None,
                )
                if mid_ev is None:
                    break
                guarded_cuts.append(mid_ev)
                prev_t = mid_ev["time_sec"]
            if ev["type"] != "_end":
                guarded_cuts.append(ev)
                prev_t = ev["time_sec"]
        chosen_cuts = guarded_cuts

    cut_points: List[Dict[str, Any]] = [
        {
            "cut_frame": 0,
            "event": events[0] if events else {"id": "evt_001", "type": "sentence_start", "time_sec": 0.0, "frame": 0},
            "raw_frame": 0,
        }
    ]
    for ev in chosen_cuts:
        raw_frame = int(ev["frame"])
        if raw_frame - cut_points[-1]["cut_frame"] >= 12 and total_frames - raw_frame >= 10:
            cut_points.append({"cut_frame": raw_frame, "raw_frame": raw_frame, "event": ev})

    # ── 镜头预算：刀数按风格真值的「总时长/中位镜长」收敛；素材/文案撑不到就如实封顶 ──
    shot_budget_capped = ""
    shot_budget_note = ""
    if gram is not None and shot_budget:
        target_shots = int(shot_budget["target_shots"] or 0)
        n_before = len(cut_points)
        if target_shots >= 1 and n_before > target_shots:
            sent_order = [str(s.get("id") or "") for s in sentences]

            def _merge_ok(idx: int) -> bool:
                """句界切点能不能删：本句与**台词顺序上的上一句**必须同源、且首尾相接。

                注意比的是台词序列里的上一句，不是 cut_points 里的上一个切点 —— 前面已经合并
                过的镜头会跨多句，拿切点回指去比会永远差一句，把长镜风卡死在半路。
                """
                sid = str(cut_points[idx]["event"].get("sentence_id") or "")
                if sid not in sent_order:
                    return False
                k = sent_order.index(sid)
                if k == 0:
                    return False
                cur, prev = sent_by_id.get(sid), sent_by_id.get(sent_order[k - 1])
                if not cur or not prev:
                    return False
                if str(cur.get("source_media") or "") != str(prev.get("source_media") or ""):
                    return False
                try:
                    return abs(float(cur.get("source_in_sec") or 0.0)
                               - float(prev.get("source_out_sec") or 0.0)) <= 0.35
                except (TypeError, ValueError):
                    return False

            cut_points, shot_budget_note = _prune_cut_points_to_budget(
                cut_points, target_shots, float(shot_budget["shot_max_sec"]), fps, total_dur,
                can_merge=_merge_ok,
            )
            if shot_budget_note:
                grammar_notes.append(shot_budget_note)
        if target_shots >= 2 and len(cut_points) < target_shots:
            # 不是「素材不够」：这套真值的均镜长明显大于中位镜长（长尾镜多），
            # 按真值节奏排出来的刀数自然比 total/中位 这个估算值少。
            shot_budget_capped = (
                f"按真值节奏排到 {len(cut_points)} 镜（中位数口径的估算目标是 {target_shots} 镜）："
                f"该套均镜长大于中位镜长（长尾长镜多），刀数天然少于估算值"
            )
            grammar_notes.append("shot_budget_capped: " + shot_budget_capped)

    # ── 尾段收口帧对齐：慢节拍切点若正好跨过尾镜口播 jump-cut（如 53.90s→57.93s 跨 54.86s 句首），对齐到句首以便 A-roll 完整收尾 ──
    if usable_arolls and not broll_only_mode and target_aroll_ratio >= 0.18 and sentences and len(cut_points) >= 2:
        _t_tail_floor = total_dur - max(3.5, min(10.0, total_dur * 0.45))

        def _tail_seg_aroll_viable(t0: float, t1: float) -> bool:
            if t1 < _t_tail_floor:
                return False
            ov_s = [s for s in sentences if min(t1, float(s["end_sec"])) - max(t0, float(s["start_sec"])) > 0.04]
            if not ov_s:
                return False
            if any(
                str(s.get("source_media") or "") not in usable_aroll_paths
                or str(s.get("source_video_grade") or vgrade_by_mp4.get(str(s.get("source_media") or ""), "usable")) in ("audio_only", "ng")
                for s in ov_s
            ):
                return False
            if len(ov_s) == 1:
                return True
            if len({str(s.get("source_media") or "") for s in ov_s}) != 1:
                return False
            return all(
                abs(float(ov_s[k + 1].get("source_in_sec", 0.0)) - float(ov_s[k].get("source_out_sec", 0.0))) <= 0.35
                for k in range(len(ov_s) - 1)
            )

        _has_viable_tail = any(
            _tail_seg_aroll_viable(
                frames_to_sec(cut_points[i]["raw_frame"], fps),
                frames_to_sec(cut_points[i + 1]["raw_frame"] if i + 1 < len(cut_points) else total_frames, fps),
            )
            for i in range(len(cut_points))
        )
        if not _has_viable_tail:
            _max_shot_lim = float((shot_budget or {}).get("shot_max_sec") or 12.0)
            for s_cand in reversed(sentences):
                sm_c = str(s_cand.get("source_media") or "")
                vg_c = str(s_cand.get("source_video_grade") or vgrade_by_mp4.get(sm_c, "usable"))
                s_t0 = float(s_cand.get("start_sec") or 0.0)
                if sm_c not in usable_aroll_paths or vg_c in ("audio_only", "ng"):
                    continue
                if s_t0 < _t_tail_floor or total_dur - s_t0 < 1.0:
                    continue
                snap_f = int(round(s_t0 * fps))
                snap_ev = next(
                    (e for e in events if e.get("type") == "sentence_start" and e.get("sentence_id") == s_cand.get("id")),
                    None,
                )
                if snap_ev is None:
                    snap_ev = next((e for e in events if abs(int(e["frame"]) - snap_f) <= 1), None)
                if snap_ev is None:
                    continue
                snap_f = int(snap_ev.get("frame", snap_f))
                snapped = False
                for k in range(len(cut_points) - 1, 0, -1):
                    cp_t = frames_to_sec(cut_points[k]["raw_frame"], fps)
                    prev_t = frames_to_sec(cut_points[k - 1]["raw_frame"], fps)
                    nxt_t = frames_to_sec(cut_points[k + 1]["raw_frame"] if k + 1 < len(cut_points) else total_frames, fps)
                    if (
                        str(cut_points[k]["event"].get("type") or "") != "sentence_start"
                        and (not fast_choreography or cp_t >= 3.0 or snap_f < first_3s_cutoff_f)
                        and abs(s_t0 - cp_t) <= 2.2
                        and s_t0 - prev_t >= 0.80
                        and s_t0 - prev_t <= max(_max_shot_lim, 6.0)
                        and nxt_t - s_t0 >= 0.80
                    ):
                        cut_points[k] = {"cut_frame": snap_f, "raw_frame": snap_f, "event": snap_ev}
                        snapped = True
                        break
                if snapped:
                    break
                # 若附近无非句首切点可吸附，直接在安全位置插入句首切点保住尾段 A-roll 收尾
                ins_idx = next((k for k, cp in enumerate(cut_points) if cp["raw_frame"] > snap_f), len(cut_points))
                left_t = frames_to_sec(cut_points[ins_idx - 1]["raw_frame"], fps)
                right_t = frames_to_sec(cut_points[ins_idx]["raw_frame"] if ins_idx < len(cut_points) else total_frames, fps)
                if s_t0 - left_t >= 0.80 and right_t - s_t0 >= 0.80:
                    cut_points.insert(ins_idx, {"cut_frame": snap_f, "raw_frame": snap_f, "event": snap_ev})
                    break

    # ── 换源机位句界硬切原则：只要相邻句子切换了不同视频源（如旧机位说完接新机位），新机位句首必须干脆硬切入 ──
    if usable_arolls and not broll_only_mode and len(sentences) >= 2 and len(cut_points) >= 1:
        source_switch_frames: set = set()
        for idx_s in range(1, len(sentences)):
            sm_prev = str(sentences[idx_s - 1].get("source_media") or "")
            sm_curr = str(sentences[idx_s].get("source_media") or "")
            if not sm_prev or not sm_curr or sm_prev == sm_curr:
                continue
            if sm_curr not in usable_aroll_paths:
                continue
            sid_c = str(sentences[idx_s].get("id") or "")
            t_c0 = float(sentences[idx_s].get("start_sec") or 0.0)
            ev_c = next(
                (e for e in events if e.get("type") == "sentence_start" and str(e.get("sentence_id") or "") == sid_c),
                None,
            ) or next((e for e in events if abs(float(e.get("time_sec") or 0.0) - t_c0) <= 0.05), None)
            if ev_c is None:
                continue
            snap_f = int(ev_c.get("frame", round(t_c0 * fps)))
            source_switch_frames.add(snap_f)
            if any(abs(int(cp["raw_frame"]) - snap_f) <= 2 for cp in cut_points):
                continue
            # 优先将 1.1s 内的邻近非换源普通切点直接吸附到新机位句首，避免旧机位说完后呆滞拖帧
            best_k = None
            best_dist = int(1.1 * fps) + 1
            for k in range(1, len(cut_points)):
                rf = int(cut_points[k]["raw_frame"])
                if fast_choreography and rf < first_3s_cutoff_f <= snap_f:
                    continue
                if any(abs(rf - sf) <= 2 for sf in source_switch_frames if sf != snap_f):
                    continue
                d_f = abs(rf - snap_f)
                if d_f < best_dist:
                    prev_rf = int(cut_points[k - 1]["raw_frame"])
                    nxt_rf = int(cut_points[k + 1]["raw_frame"]) if k + 1 < len(cut_points) else total_frames
                    if snap_f - prev_rf >= 14 and nxt_rf - snap_f >= 14:
                        best_dist = d_f
                        best_k = k
            if best_k is not None:
                cut_points[best_k] = {"cut_frame": snap_f, "raw_frame": snap_f, "event": ev_c}
            else:
                ins_i = next((k for k, cp in enumerate(cut_points) if int(cp["raw_frame"]) > snap_f), len(cut_points))
                left_rf = int(cut_points[ins_i - 1]["raw_frame"])
                right_rf = int(cut_points[ins_i]["raw_frame"]) if ins_i < len(cut_points) else total_frames
                if snap_f - left_rf >= 14 and right_rf - snap_f >= 14:
                    cut_points.insert(ins_i, {"cut_frame": snap_f, "raw_frame": snap_f, "event": ev_c})

    if choreography_broll:
        # 优先用锁定口播的真实句首界定窗口，避免把相邻的整段镜头一起锁住。
        lock_boundaries = set()
        was_locked = False
        for sentence in sentences:
            locked = bool(sentence.get("a_roll_locked"))
            if locked != was_locked:
                lock_boundaries.add(sentence["id"])
            was_locked = locked
        for event in events:
            if event["type"] != "sentence_start" or event.get("sentence_id") not in lock_boundaries:
                continue
            frame = int(event["frame"])
            if frame < 12:
                continue
            index = next((i for i, cp in enumerate(cut_points) if cp["raw_frame"] >= frame), len(cut_points))
            before = cut_points[index - 1]["raw_frame"]
            after = cut_points[index]["raw_frame"] if index < len(cut_points) else total_frames
            if frame - before >= 12 and after - frame >= 12:
                cut_points.insert(index, {"cut_frame": frame, "raw_frame": frame, "event": event})

    def find_sentence_for_time(
        t_sec: float,
        ev: Dict[str, Any] | None = None,
        t_end: Optional[float] = None,
    ) -> Dict[str, Any]:
        if t_end is not None and float(t_end) > t_sec + 0.04:
            ov = [
                s for s in sentences
                if min(float(t_end), float(s["end_sec"])) - max(t_sec, float(s["start_sec"])) > 0.04
            ]
            if ov:
                return ov[0]
        if ev and ev.get("sentence_id") in sent_by_id and ev.get("type") in ("sentence_start", "pause_end", "keyword_accent", "stress"):
            ev_s = sent_by_id[ev["sentence_id"]]
            if t_sec <= float(ev_s["end_sec"]) + 0.04:
                return ev_s
        for s in sentences:
            if s["start_sec"] <= t_sec <= s["end_sec"]:
                return s
        for s in sentences:
            if 0.0 < float(s["start_sec"]) - t_sec <= 0.65:
                return s
        for s in sentences:
            if s["start_sec"] <= t_sec <= s["end_sec"] + 0.40:
                return s
        return sentences[-1]

    def find_broll_topic_sentence(
        t_sec: float,
        t_end: Optional[float],
        fallback: Dict[str, Any],
    ) -> Dict[str, Any]:
        """为 B-roll 选片寻找占本镜主要时长的台词（避免结尾 0.2s 尾巴误导画面匹配），不影响 A-roll 口型句界。"""
        if t_end is not None and float(t_end) > t_sec + 0.04:
            ov = [
                s for s in sentences
                if min(float(t_end), float(s["end_sec"])) - max(t_sec, float(s["start_sec"])) > 0.04
            ]
            if ov:
                return max(
                    ov,
                    key=lambda s: (
                        min(float(t_end), float(s["end_sec"])) - max(t_sec, float(s["start_sec"])),
                        -abs(float(s["start_sec"]) - t_sec),
                    ),
                )
        return fallback

    opening_word_spans = [
        (float(w.get("start_sec", w.get("start", 0.0))), float(w.get("end_sec", w.get("end", 0.0))))
        for s in sentences for w in (s.get("words") or [])
    ] if fast_choreography else []
    aroll_locked_spans = [
        (float(s["start_sec"]), float(s["end_sec"])) for s in sentences if s.get("a_roll_locked")
    ] if choreography_broll else []

    def _choreography_aroll_locked(t_start: float, t_end: float) -> bool:
        if any(min(t_end, end) - max(t_start, start) > 1.0 / fps for start, end in aroll_locked_spans):
            return True
        if not fast_choreography:
            return t_start < 3.2
        return t_start < 3.0 and any(
            start + 1.0 / fps < cut < end - 1.0 / fps
            for start, end in opening_word_spans for cut in (t_start, t_end)
        )

    n_segs = len(cut_points)
    audio_only_flags: List[bool] = []
    source_aroll_valid: List[bool] = []
    # 被镜头预算合并出来的连续长镜（同源首尾相接、跨两句）：A-roll 校验要放行，
    # 否则一句连续说完的长镜会被判「跨句不安全」而降级成 B-roll，反而更偏离风格
    budget_merged_flags: List[bool] = [bool(cp.get("budget_merged")) for cp in cut_points]
    # 跨句的连续长镜（同源首尾相接）：允许当 A-roll，但逐镜留痕，导出侧能看出这一刀盖了几句
    multi_sentence_aroll_flags: List[bool] = [False] * len(cut_points)
    for ci, cp in enumerate(cut_points):
        ev = cp["event"]
        t_start = frames_to_sec(cp["cut_frame"], fps)
        t_end_cp = frames_to_sec(cut_points[ci + 1]["raw_frame"] if ci + 1 < n_segs else total_frames, fps)
        sent = find_sentence_for_time(t_start, ev, t_end=t_end_cp)
        sm_p = str(sent.get("source_media", "") or "")
        sent_vgrade = str(sent.get("source_video_grade") or vgrade_by_mp4.get(sm_p, "usable"))
        sm_item = media_by_mp4.get(sm_p) or {}
        sm_stab = float(sm_item.get("stability_score", 1.0) if sm_item.get("stability_score") is not None else 1.0)
        sm_sharp = float(sm_item.get("sharpness_score", 999.0) if sm_item.get("sharpness_score") is not None else 999.0)
        sm_jitter = float(sm_item.get("accel_jitter_px", 0.0) if sm_item.get("accel_jitter_px") is not None else 0.0)
        has_crisp_aroll = any(float(m.get("sharpness_score") or 0.0) >= 350.0 for m in usable_arolls)
        unusable_blur_shake = bool(broll_pool) and bool(sm_item) and (
            (has_crisp_aroll and sm_sharp < AROLL_UNUSABLE_SHARP_MAX and sm_stab < AROLL_UNUSABLE_STAB_WITH_SOFT_FOCUS)
            or (sm_stab <= AROLL_UNUSABLE_STAB_HARD and sm_jitter >= AROLL_UNUSABLE_JITTER_HARD)
        )
        forced_mask = (sent_vgrade in ("audio_only", "ng")) or unusable_blur_shake
        audio_only_flags.append(forced_mask)
        # 严格校验本句原声是否真正对应可用的实拍 A-roll，绝不把无源配音硬配给无关真人口型！
        source_aroll_valid.append(bool(sm_p and sm_p in usable_aroll_paths))

    def _can_be_safe_aroll(seg_idx: int, *, use_visual_frames: bool = False) -> bool:
        if audio_only_flags[seg_idx] or not usable_arolls or broll_only_mode or not source_aroll_valid[seg_idx]:
            return False
        frame_key = "cut_frame" if use_visual_frames else "raw_frame"
        t_in = frames_to_sec(cut_points[seg_idx][frame_key], fps)
        t_out = frames_to_sec(cut_points[seg_idx + 1][frame_key] if seg_idx + 1 < n_segs else total_frames, fps)
        overlapping = [s for s in sentences if min(t_out, float(s["end_sec"])) - max(t_in, float(s["start_sec"])) > 0.04]
        if not overlapping:
            overlapping = [find_sentence_for_time(t_in, cut_points[seg_idx]["event"])]
        if len(overlapping) != 1:
            # 若相邻多句来自同一段连续实拍 A-roll 源（如开场长镜或 0% B-roll 长镜），且中间无跳跃剪切，同样允许作为连续 A-roll
            same_src = len({str(s.get("source_media") or "") for s in overlapping}) == 1
            contiguous = all(
                abs(float(overlapping[k + 1].get("source_in_sec", 0.0)) - float(overlapping[k].get("source_out_sec", 0.0))) <= 0.35
                for k in range(len(overlapping) - 1)
            )
            # 同源、首尾相接的多句段 = 演员一口气连着说的同一段素材，剪成一刀是合法的连续长镜；
            # 真正会「口型穿帮」的是跳到另一条素材（same_src False）或中间挖掉大段（contiguous False）。
            if not (same_src and contiguous):
                return False
            if seg_idx != 0 and not zero_broll_mode:
                multi_sentence_aroll_flags[seg_idx] = True
        # A-roll 起点离句首太远会被判「切进半句嘴型」。但切口只要踩在语音事件上
        # （句界/气口/重音都是语言的天然停顿点），从这里进 A-roll 就是正常换镜；
        # 只有踩 BGM 拍点、与台词无关的切口才真的可能切进字中间。
        cut_ev_type = str(cut_points[seg_idx]["event"].get("type") or "")
        if (t_in - float(overlapping[0]["start_sec"]) > 1.10
                and cut_ev_type not in ("pause_start", "pause_end", "sentence_start",
                                        "keyword_accent", "stress")):
            return False
        if seg_idx < n_segs - 1:
            last_s = overlapping[-1]
            if t_out > float(last_s["end_sec"]) + float(last_s.get("pause_after_sec", 0.08)) + 0.04:
                return False
        return True

    roles: List[str] = []
    aroll_step = max(2, int(round(1.0 / max(0.04, target_aroll_ratio))))
    for idx, cp in enumerate(cut_points):
        ev = cp["event"]
        t_start = frames_to_sec(cp["cut_frame"], fps)
        t_end_cp = frames_to_sec(cut_points[idx + 1]["raw_frame"] if idx + 1 < n_segs else total_frames, fps)
        sent = find_sentence_for_time(t_start, ev, t_end=t_end_cp)
        offset_in_sent = t_start - float(sent["start_sec"])
        near_sent_start = offset_in_sent <= 0.85
        has_prop_show = any(cue in sent.get("text", "") for cue in PROP_SHOW_CUES)
        tail_start_sec = max(0.0, total_dur - min(8.5, max(2.2, total_dur * 0.28)))
        is_tail = (idx == n_segs - 1 and near_sent_start) or (t_start >= tail_start_sec and near_sent_start)

        if audio_only_flags[idx] or not usable_arolls or broll_only_mode or not source_aroll_valid[idx]:
            roles.append("b_roll")
        elif idx == 0 and target_aroll_ratio >= 0.25 and (_can_be_safe_aroll(0) or n_segs == 1):
            roles.append("a_roll")
        elif _can_be_safe_aroll(idx) and near_sent_start and (
            has_prop_show
            or (is_tail and target_aroll_ratio >= 0.18)
            or (idx % aroll_step == 1 if aroll_step > 2 else idx % 2 == 1)
        ):
            roles.append("a_roll")
        else:
            roles.append("b_roll")

    if usable_arolls and not broll_only_mode:
        seg_ends_est = [
            frames_to_sec(cut_points[i + 1]["raw_frame"] if i + 1 < n_segs else total_frames, fps)
            for i in range(n_segs)
        ]
        seg_durs_est = [
            frames_to_sec((cut_points[i + 1]["raw_frame"] if i + 1 < n_segs else total_frames) - cut_points[i]["raw_frame"], fps)
            for i in range(n_segs)
        ]
        cur_aroll_sec = sum(d for r, d in zip(roles, seg_durs_est) if r == "a_roll")
        tail_floor = total_dur - max(3.5, min(10.0, total_dur * 0.45))
        has_tail_aroll = any(roles[i] == "a_roll" and seg_ends_est[i] >= tail_floor for i in range(n_segs))
        if not has_tail_aroll and target_aroll_ratio >= 0.18:
            tail_cands = []
            for i in range(n_segs):
                if roles[i] != "b_roll" or not _can_be_safe_aroll(i) or seg_ends_est[i] < tail_floor:
                    continue
                t_s = frames_to_sec(cut_points[i]["raw_frame"], fps)
                s_obj = find_sentence_for_time(t_s, cut_points[i]["event"])
                tail_cands.append((-i, abs(t_s - float(s_obj["start_sec"])), -seg_durs_est[i], i))
            if tail_cands:
                _, _, _, best_tail_i = sorted(tail_cands)[0]
                roles[best_tail_i] = "a_roll"
                cur_aroll_sec += seg_durs_est[best_tail_i]

        min_aroll_sec = total_dur * max(0.0, min(0.35, target_aroll_ratio * 0.75))
        max_aroll_sec = total_dur * (1.0 if zero_broll_mode else min(0.85, max(0.02, target_aroll_ratio + 0.05)))
        if cur_aroll_sec < min_aroll_sec:
            cands = []
            for i in range(n_segs):
                if roles[i] != "b_roll" or not _can_be_safe_aroll(i):
                    continue
                t_s = frames_to_sec(cut_points[i]["raw_frame"], fps)
                s_obj = find_sentence_for_time(t_s, cut_points[i]["event"])
                cands.append((abs(t_s - float(s_obj["start_sec"])), -seg_durs_est[i], i))
            for _, _, i_cand in sorted(cands):
                roles[i_cand] = "a_roll"
                cur_aroll_sec += seg_durs_est[i_cand]
                if cur_aroll_sec >= min_aroll_sec:
                    break
        elif cur_aroll_sec > max_aroll_sec and len(broll_pool) > 0:
            # 精准控制目标 B-roll 覆盖率（覆盖 10%~95% 全风格区间）
            mid_indices = list(range(1, n_segs - 1)) if n_segs > 2 else []
            edge_indices = ([0, n_segs - 1] if n_segs > 1 else [0]) if target_aroll_ratio < 0.25 else []
            for idx_pool in (mid_indices, edge_indices):
                if cur_aroll_sec <= max_aroll_sec or not idx_pool:
                    break
                drop_cands = []
                for i in idx_pool:
                    if roles[i] != "a_roll":
                        continue
                    t_s = frames_to_sec(cut_points[i]["raw_frame"], fps)
                    s_obj = find_sentence_for_time(t_s, cut_points[i]["event"])
                    s_txt_c = str(s_obj.get("text", ""))
                    if any(cue in s_txt_c for cue in PROP_SHOW_CUES):
                        continue
                    sem_hit = any(
                        tg and (tg in s_txt_c)
                        for m in broll_pool if m.get("role") == "b_roll"
                        for tg in (m.get("semantic_tags", []) + m.get("manual_tags", []))
                    )
                    adj_broll = int((i + 1 < n_segs and roles[i + 1] != "a_roll") or (i > 0 and roles[i - 1] != "a_roll"))
                    drop_cands.append((-int(sem_hit), adj_broll, -seg_durs_est[i], i))
                for _, _, _, i_drop in sorted(drop_cands):
                    if cur_aroll_sec - seg_durs_est[i_drop] < min_aroll_sec and target_aroll_ratio >= 0.20:
                        continue
                    roles[i_drop] = "b_roll"
                    cur_aroll_sec -= seg_durs_est[i_drop]
                    if cur_aroll_sec <= max_aroll_sec:
                        break

    # 反向校正：初始排期天然偏多 B-roll（隔一镜一插），低覆盖率风格必须按目标把插入画面收回来
    if usable_arolls and not broll_only_mode and not zero_broll_mode:
        seg_durs_cap = [
            frames_to_sec(
                (cut_points[i + 1]["raw_frame"] if i + 1 < n_segs else total_frames) - cut_points[i]["raw_frame"], fps
            )
            for i in range(n_segs)
        ]
        broll_cap_ratio = min(1.0, broll_target_ratio + 0.03)
        cur_broll_sec = sum(d for r, d in zip(roles, seg_durs_cap) if r != "a_roll")
        if cur_broll_sec > total_dur * broll_cap_ratio:
            conv_cands = []
            for i_c in range(n_segs):
                if roles[i_c] == "a_roll" or not _can_be_safe_aroll(i_c):
                    continue
                t_s = frames_to_sec(cut_points[i_c]["raw_frame"], fps)
                s_obj = find_sentence_for_time(t_s, cut_points[i_c]["event"])
                s_txt = str(s_obj.get("text", ""))
                sem_hit = any(
                    tg and (tg in s_txt)
                    for m in broll_pool if m.get("role") == "b_roll"
                    for tg in (m.get("semantic_tags", []) + m.get("manual_tags", []))
                )
                has_prop = any(cue in s_txt for cue in PROP_SHOW_CUES)
                conv_cands.append((-int(sem_hit) + int(has_prop) * 3, i_c))
            for _, i_conv in sorted(conv_cands):
                if cur_broll_sec <= total_dur * broll_cap_ratio:
                    break
                roles[i_conv] = "a_roll"
                cur_broll_sec -= seg_durs_cap[i_conv]

    if zero_broll_mode:
        for i_z in range(n_segs):
            if not audio_only_flags[i_z] and source_aroll_valid[i_z]:
                roles[i_z] = "a_roll"

    # 按可用的实拍证据补足契约配额。其余风格沿用动态带下沿校正。
    choreography_has_unfilled_shot = False
    if choreography_broll and usable_arolls and not broll_only_mode:
        matched = []
        for i, cp in enumerate(cut_points):
            start = frames_to_sec(cp["raw_frame"], fps)
            end = seg_ends_est[i]
            sent = find_broll_topic_sentence(start, end, find_sentence_for_time(start, cp["event"], t_end=end))
            locked = _choreography_aroll_locked(start, end)
            duration = seg_durs_est[i]
            text = str(sent.get("text") or "")
            if not locked and any(
                float(m.get("duration_sec") or 0.0) + SOURCE_EPSILON_SEC >= duration
                and not (fast_choreography and start < 3.0 and not _has_opening_broll_evidence(m, text))
                and _score_broll(text, m, keywords=sent.get("keywords"))["tier"] in ("hit", "weak")
                for m in live_brolls_for_choreography
            ):
                matched.append(i)
            elif _can_be_safe_aroll(i):
                choreography_has_unfilled_shot |= roles[i] == "b_roll" and not locked
                roles[i] = "a_roll"

        target_b_sec = total_dur * broll_target_ratio
        max_b_sec = total_dur * max(broll_band[1], broll_target_ratio)
        cur_b_sec = sum(seg_durs_est[i] for i in matched if roles[i] == "b_roll")
        protected_tail = next(
            (i for i in reversed(range(n_segs)) if roles[i] == "a_roll" and seg_ends_est[i] >= tail_floor),
            None,
        ) if target_aroll_ratio >= 0.18 and not fast_choreography else None
        for i in sorted(matched, key=lambda idx: (-seg_durs_est[idx], idx)):
            if roles[i] != "a_roll" or i == protected_tail:
                continue
            if cur_b_sec + seg_durs_est[i] / 2.0 > target_b_sec:
                continue
            if fast_choreography and cur_b_sec + seg_durs_est[i] > max_b_sec + SOURCE_EPSILON_SEC:
                continue
            roles[i] = "b_roll"
            cur_b_sec += seg_durs_est[i]
    elif usable_arolls and not broll_only_mode and not zero_broll_mode and broll_pool and n_segs > 1:
        seg_durs_up = [
            frames_to_sec((cut_points[i + 1]["raw_frame"] if i + 1 < n_segs else total_frames)
                          - cut_points[i]["raw_frame"], fps)
            for i in range(n_segs)
        ]
        cur_b_sec = sum(d for r, d in zip(roles, seg_durs_up) if r != "a_roll")
        floor_sec = max(0.0, total_dur * broll_band[0] - total_dur * 0.02)
        if cur_b_sec < floor_sec:
            tail_keep_floor = total_dur - max(3.5, min(10.0, total_dur * 0.45))
            tail_aroll_indices = [
                k for k in range(n_segs)
                if roles[k] == "a_roll" and seg_ends_est[k] >= tail_keep_floor
            ]
            # 当目标 A-roll 占比 ≥ 18% 时，保留尾段最后 1 镜 A-roll 收口，防止 B-roll 补齐把末尾全吞成纯 B 镜导致质检挂科
            protected_tail_i = tail_aroll_indices[-1] if (target_aroll_ratio >= 0.18 and tail_aroll_indices) else None
            up_cands = []
            for i_u in range(n_segs):
                if roles[i_u] != "a_roll" or i_u == protected_tail_i:
                    continue
                t_s = frames_to_sec(cut_points[i_u]["raw_frame"], fps)
                s_obj = find_sentence_for_time(t_s, cut_points[i_u]["event"])
                s_txt = str(s_obj.get("text", ""))
                if any(cue in s_txt for cue in PROP_SHOW_CUES):
                    continue
                sem_hit = any(
                    tg and (tg in s_txt)
                    for m in broll_pool if m.get("role") == "b_roll"
                    for tg in (m.get("semantic_tags", []) + m.get("manual_tags", []))
                )
                up_cands.append((-int(sem_hit), -seg_durs_up[i_u], i_u))
            raised = 0
            for _, _, i_u in sorted(up_cands):
                if cur_b_sec >= floor_sec:
                    break
                roles[i_u] = "b_roll"
                cur_b_sec += seg_durs_up[i_u]
                raised += 1
            if raised:
                broll_policy_note += (f"；镜头数按风格收敛后 B 覆盖低于动态带下沿"
                                      f"（{broll_band[0]*100:.0f}%），按语义贴合补回 {raised} 镜 B-roll")
            else:
                broll_policy_note += (f"；镜头数收敛后 B 覆盖低于动态带下沿（{broll_band[0]*100:.0f}%），"
                                      f"素材侧没有可换的 B-roll，保持现状")

    if kinetic_stage_mode and usable_arolls:
        # 动态花字舞台锁死规则：
        # 1) 所有落入强动态花字意群的镜头强制锁死 A-roll 口播，绝不切去 B-roll 破坏舞台；
        # 2) 仅允许在呼吸过渡实体窗口内保留有强语义命中(hit/weak)的实拍 B-roll，总量 <= 30%（无实体则 0%）。
        live_b_only = [m for m in broll_pool if m.get("role") == "b_roll"]
        _b_cands_by_idx: List[Tuple[float, int, float]] = []
        for idx_k in range(n_segs):
            t_sk = frames_to_sec(cut_points[idx_k]["raw_frame"], fps)
            t_ek = frames_to_sec(cut_points[idx_k + 1]["raw_frame"] if idx_k + 1 < n_segs else total_frames, fps)
            dur_k = max(0.0, t_ek - t_sk)
            if _overlaps_kinetic_locked(t_sk, t_ek) or not live_b_only or t_sk < 3.0:
                if source_aroll_valid[idx_k] and not audio_only_flags[idx_k]:
                    roles[idx_k] = "a_roll"
                continue
            s_obj_k = find_broll_topic_sentence(t_sk, t_ek, find_sentence_for_time(t_sk, cut_points[idx_k]["event"]))
            s_txt_k = str(s_obj_k.get("text") or "")
            if _is_deictic_pointing_stub(s_txt_k, dur_k) and source_aroll_valid[idx_k] and not audio_only_flags[idx_k]:
                roles[idx_k] = "a_roll"
                continue
            s_kws_k = [str(x) for x in (s_obj_k.get("keywords") or [])]
            best_sc_k = 0.0
            best_v_k = "none"
            for bm in live_b_only:
                if _is_signboard_wall_noise(bm, s_txt_k):
                    continue
                sc_res = broll_match.score_clip(s_txt_k, bm, used_count=0, need_sec=dur_k, keywords=s_kws_k)
                if float(sc_res.get("score") or 0.0) > best_sc_k:
                    best_sc_k = float(sc_res.get("score") or 0.0)
                    best_v_k = str(sc_res.get("tier") or "none")
            if best_v_k in ("hit", "weak") and best_sc_k >= 0.30:
                # 若本镜当前被判为 a_roll 但其实处于可证据锚定的实体过渡窗内且语义贴合，可作为高光实拍候选
                _b_cands_by_idx.append((best_sc_k, idx_k, dur_k))
                roles[idx_k] = "b_roll"
            else:
                if source_aroll_valid[idx_k] and not audio_only_flags[idx_k]:
                    roles[idx_k] = "a_roll"

        # 上限控制：动态舞台模式下 B-roll 总覆盖严格收在 [0, 30%]，各过渡句组均衡保留最高语义贴合实证镜
        max_kinetic_b_sec = min(total_dur * 0.295, total_dur * max(0.285, broll_target_ratio + 0.07))
        cur_kb_sec = sum(
            frames_to_sec((cut_points[i + 1]["raw_frame"] if i + 1 < n_segs else total_frames) - cut_points[i]["raw_frame"], fps)
            for i in range(n_segs) if roles[i] != "a_roll"
        )
        if cur_kb_sec > max_kinetic_b_sec:
            score_map_k = {i_c: sc_c for sc_c, i_c, _ in _b_cands_by_idx}
            group_by_seg: Dict[int, str] = {}
            best_in_group: Dict[str, int] = {}
            for i_c in range(n_segs):
                if roles[i_c] != "a_roll":
                    ts_g = frames_to_sec(cut_points[i_c]["raw_frame"], fps)
                    s_g = find_sentence_for_time(ts_g, cut_points[i_c]["event"])
                    g_key = str((kinetic_group_by_sentence.get(s_g.get("id")) or {}).get("group_id") or s_g.get("id") or i_c)
                    group_by_seg[i_c] = g_key
                    if g_key not in best_in_group or score_map_k.get(i_c, 0.0) > score_map_k.get(best_in_group[g_key], 0.0):
                        best_in_group[g_key] = i_c
            protected_group_anchors = set(best_in_group.values())
            drop_b_order = sorted(
                (i for i in range(n_segs) if roles[i] != "a_roll" and source_aroll_valid[i] and not audio_only_flags[i]),
                key=lambda i_c: (
                    1 if i_c in protected_group_anchors else 0,
                    score_map_k.get(i_c, 0.0),
                    frames_to_sec((cut_points[i_c + 1]["raw_frame"] if i_c + 1 < n_segs else total_frames) - cut_points[i_c]["raw_frame"], fps),
                ),
            )
            for i_db in drop_b_order:
                if cur_kb_sec <= max_kinetic_b_sec:
                    break
                d_db = frames_to_sec((cut_points[i_db + 1]["raw_frame"] if i_db + 1 < n_segs else total_frames) - cut_points[i_db]["raw_frame"], fps)
                roles[i_db] = "a_roll"
                cur_kb_sec -= d_db
        broll_policy_note = (
            f"动态花字舞台模式：锁死 {sum(1 for g in (kinetic_stage_plan.get('groups') or []) if g.get('a_roll_locked'))} 组花字窗口为 A-roll，"
            "仅在实体过渡窗按语义证据切入 B-roll"
        )

        # 确保自抠像人后弹幕窗内的 A-roll 单镜不超过 2.35s（贴合跳剪节奏并合规过 treatment_policy 2.5s 上限）
        if kinetic_stage_plan.get("matting_windows"):
            new_cp, new_roles, new_ao, new_sv, new_bm, new_ms = [], [], [], [], [], []
            for idx_m in range(n_segs):
                f_s_m = cut_points[idx_m]["raw_frame"]
                f_e_m = cut_points[idx_m + 1]["raw_frame"] if idx_m + 1 < n_segs else total_frames
                t_s_m, t_e_m = frames_to_sec(f_s_m, fps), frames_to_sec(f_e_m, fps)
                new_cp.append(cut_points[idx_m])
                new_roles.append(roles[idx_m])
                new_ao.append(audio_only_flags[idx_m])
                new_sv.append(source_aroll_valid[idx_m])
                new_bm.append(budget_merged_flags[idx_m])
                new_ms.append(multi_sentence_aroll_flags[idx_m])
                if roles[idx_m] == "a_roll" and (t_e_m - t_s_m) > 2.35 and _overlaps_matting_window(t_s_m, t_e_m):
                    mid_ev_cands = [
                        e for e in events
                        if t_s_m + 0.70 <= float(e["time_sec"]) <= t_e_m - 0.65
                        and int(e["frame"]) - f_s_m >= 18 and f_e_m - int(e["frame"]) >= 18
                        and e["type"] in ("sentence_start", "keyword_accent", "stress", "pause_start", "bgm_downbeat", "bgm_beat")
                    ]
                    if mid_ev_cands:
                        target_mid = (t_s_m + t_e_m) * 0.5
                        best_mev = min(mid_ev_cands, key=lambda e: abs(float(e["time_sec"]) - target_mid))
                        mf = int(best_mev["frame"])
                        new_cp.append({"cut_frame": mf, "raw_frame": mf, "event": best_mev})
                        new_roles.append("a_roll")
                        new_ao.append(audio_only_flags[idx_m])
                        new_sv.append(source_aroll_valid[idx_m])
                        new_bm.append(False)
                        new_ms.append(False)
            if len(new_cp) != len(cut_points):
                cut_points = new_cp
                roles = new_roles
                audio_only_flags = new_ao
                source_aroll_valid = new_sv
                budget_merged_flags = new_bm
                multi_sentence_aroll_flags = new_ms
                n_segs = len(cut_points)

    # ── 同源 A-roll 句尾气口碎片合并：防止非句界节拍点在句尾静默/轻尾气口切出无语音覆盖的幽灵 A-roll 碎片 ──
    if n_segs >= 2:
        m_cp, m_roles, m_ao, m_sv, m_bm, m_ms = [], [], [], [], [], []
        for idx in range(n_segs):
            merged_into_prev = False
            if m_cp and m_roles[-1] == "a_roll" and roles[idx] == "a_roll":
                ev_type = str((cut_points[idx].get("event") or {}).get("type") or "")
                if ev_type != "sentence_start":
                    prev_t0 = frames_to_sec(m_cp[-1]["raw_frame"], fps)
                    t_s = frames_to_sec(cut_points[idx]["raw_frame"], fps)
                    t_e = frames_to_sec(cut_points[idx + 1]["raw_frame"] if idx + 1 < n_segs else total_frames, fps)
                    if (t_e - t_s) <= 0.85 and (t_e - prev_t0) <= 2.35:
                        s_prev = find_sentence_for_time(prev_t0, m_cp[-1]["event"], t_end=t_s)
                        s_curr = find_sentence_for_time(t_s, cut_points[idx]["event"], t_end=t_e)
                        if (
                            s_prev.get("id")
                            and s_prev.get("id") == s_curr.get("id")
                            and str(s_prev.get("source_media") or "") == str(s_curr.get("source_media") or "")
                        ):
                            other_ov = max(
                                (
                                    min(t_e, float(sx.get("end_sec", 0.0))) - max(t_s, float(sx.get("start_sec", 0.0)))
                                    for sx in sentences
                                    if sx.get("id") != s_curr.get("id")
                                ),
                                default=0.0,
                            )
                            if other_ov < 0.12:
                                s_words = s_curr.get("words") or []
                                if s_words:
                                    word_ov = sum(
                                        max(
                                            0.0,
                                            min(t_e, float(w.get("end", w.get("end_sec", 0.0))))
                                            - max(t_s, float(w.get("start", w.get("start_sec", 0.0)))),
                                        )
                                        for w in s_words
                                    )
                                    is_breath_tail = word_ov < 0.28
                                else:
                                    is_breath_tail = (float(s_curr.get("end_sec", 0.0)) - t_s) <= 0.50
                                if is_breath_tail:
                                    merged_into_prev = True
            if not merged_into_prev:
                m_cp.append(cut_points[idx])
                m_roles.append(roles[idx])
                m_ao.append(audio_only_flags[idx])
                m_sv.append(source_aroll_valid[idx])
                m_bm.append(budget_merged_flags[idx])
                m_ms.append(multi_sentence_aroll_flags[idx])
        if len(m_cp) != len(cut_points):
            cut_points = m_cp
            roles = m_roles
            audio_only_flags = m_ao
            source_aroll_valid = m_sv
            budget_merged_flags = m_bm
            multi_sentence_aroll_flags = m_ms
            n_segs = len(cut_points)

    def _cross_roll_needs_hard_cut(idx: int) -> bool:
        if kinetic_stage_mode:
            return True
        if roles[idx - 1] == roles[idx]:
            return False
        cut = cut_points[idx]
        at = frames_to_sec(cut["raw_frame"], fps)
        end = frames_to_sec(cut_points[idx + 1]["raw_frame"] if idx + 1 < n_segs else total_frames, fps)
        after = find_sentence_for_time(at, cut["event"], t_end=end)
        before = next((s for s in reversed(sentences) if float(s["start_sec"]) < at - 0.5 / fps), after)
        group_before = before.get("beat_group", before.get("beat_group_id"))
        group_after = after.get("beat_group", after.get("beat_group_id"))
        return (
            (group_before is not None and group_after is not None and group_before != group_after)
            or (before.get("a_roll_locked") and after.get("pattern") == "plain_bridge")
            or (roles[idx] == "a_roll" and (cut["event"]["type"] == "sentence_start" or before["id"] != after["id"]))
        )

    # 真·J-cut (正帧移：声先画后) 与 真·L-cut (负帧移：高语义物证画面先入)
    j_leads = [0] * n_segs
    if j_cut_frames > 0:
        for idx in range(1, n_segs):
            if fast_choreography and cut_points[idx]["raw_frame"] < first_3s_cutoff_f:
                continue
            if _cross_roll_needs_hard_cut(idx):
                continue
            ev = cut_points[idx]["event"]
            if roles[idx - 1] != "a_roll" and ev["type"] in ("sentence_start", "pause_end", "keyword_accent"):
                cand_f = cut_points[idx]["raw_frame"] + j_cut_frames
                next_f = cut_points[idx + 1]["raw_frame"] if idx + 1 < n_segs else total_frames
                if (idx == 1 or cut_points[idx]["raw_frame"] >= first_3s_cutoff_f or cand_f < first_3s_cutoff_f) and (next_f - cand_f >= 12):
                    cut_points[idx]["cut_frame"] = cand_f
                    j_leads[idx] = j_cut_frames
            elif (roles[idx - 1] == "a_roll" and roles[idx] != "a_roll"
                  and ev["type"] in ("sentence_start", "pause_end", "pause_start", "keyword_accent")):
                # L-cut：若前一镜为 A-roll 且本段为 B-roll，当前句或前一句尾部命中实拍特写标签时，
                # 画面提前 3 帧入镜（保持前段 >=14f）。切口踩在句界/气口/重音上都成立——
                # 只认句界会让风格节奏式挑点排出的 a→b 换镜几乎全落在气口上，L-cut 直接归零。
                sent = find_sentence_for_time(frames_to_sec(cut_points[idx]["raw_frame"], fps), ev)
                prev_sent = find_sentence_for_time(frames_to_sec(cut_points[idx - 1]["raw_frame"], fps), cut_points[idx - 1]["event"])
                s_txt = f"{prev_sent.get('text', '')} {sent.get('text', '')}"
                has_match = any(
                    tg and (tg in s_txt)
                    for m in broll_pool if m.get("role") == "b_roll"
                    for tg in (m.get("semantic_tags", []) + m.get("manual_tags", []))
                )
                l_lead = min(4, max(2, j_cut_frames))
                cand_f = cut_points[idx]["raw_frame"] - l_lead
                prev_f = cut_points[idx - 1]["cut_frame"]
                if choreography_broll and style_id == "style_01" and cand_f < round(3.2 * fps):
                    continue
                if has_match and (cand_f - prev_f >= 14) and (cut_points[idx]["raw_frame"] < first_3s_cutoff_f or cand_f >= first_3s_cutoff_f):
                    cut_points[idx]["cut_frame"] = cand_f
                    j_leads[idx] = -l_lead

        for idx in range(1, n_segs):
            if cut_points[idx]["cut_frame"] - cut_points[idx - 1]["cut_frame"] < 10:
                cut_points[idx]["cut_frame"] = cut_points[idx]["raw_frame"]
                j_leads[idx] = 0

        for idx in range(1, n_segs):
            if roles[idx] == "a_roll" and j_leads[idx] > 0:
                t_jc = frames_to_sec(cut_points[idx]["cut_frame"], fps)
                s_jc = find_sentence_for_time(t_jc, cut_points[idx]["event"])
                if t_jc - float(s_jc["start_sec"]) > 1.15:
                    cut_points[idx]["cut_frame"] = cut_points[idx]["raw_frame"]
                    j_leads[idx] = 0

    usage_counts: Dict[str, int] = {m["id"]: 0 for m in broll_pool}
    # 场景复用账本：原版一条素材反复取不同时间窗（真值段/文件 中位 4.3），不是一条用一次。
    # 每条素材只用一次 → 每秒换一个新场景 → 这就是「看着乱」的量化根因。
    used_media_counts: Dict[str, int] = {}
    scene_target = scene_budget.target_segs_per_file(style_id)
    # Agent 逐句指定过素材（04b_broll_choice.json）就优先采信，读一次即可
    agent_choices = broll_match.load_agent_choices(pipeline_dir)
    if agent_choices.get("items"):
        print(f"[step4] 采信 Agent 逐句选片 {len(agent_choices['items'])} 处（{broll_match.AGENT_CHOICE_FILE}）")
    n_live = max(1, sum(1 for m in broll_pool if m.get("role") == "b_roll"))
    budget_per_live = (total_dur * 0.92) / float(n_live)
    used_cursors: Dict[str, float] = {}
    for m in broll_pool:
        avail_m = float(m.get("duration_sec", 0.0))
        if m.get("role") == "b_roll" and avail_m > 2.0:
            h_est = 0.40 if avail_m > 2.8 else 0.0
            usable_span = max(0.0, avail_m - 2.0 * h_est)
            max_start = max(h_est, avail_m - h_est - min(usable_span, max(3.0, budget_per_live)))
            used_cursors[m["id"]] = round(min(max(h_est, float(m.get("best_in_sec", h_est))), max_start), 4)
        else:
            used_cursors[m["id"]] = float(m.get("best_in_sec", 0.0))
    max_out_by_mp: Dict[str, float] = {}
    taboo_blocked_count = 0

    OPPOSING_MOTION = {("pan_left", "pan_right"), ("pan_right", "pan_left"), ("tilt_up", "tilt_down"), ("tilt_down", "tilt_up")}

    def sem_of_choice(m: Dict[str, Any], s: Dict[str, Any], used_count: int = 0,
                       need_sec: Optional[float] = None) -> Dict[str, Any]:
        """写进剪辑表的语义依据（与选片同口径）；旧口径对照时另外记一笔字面 tag 命中数。"""
        sel = _score_broll(
            str(s.get("text", "")), m, used_count=used_count, need_sec=need_sec,
            keywords=s.get("keywords") or [],
        )
        rel = {
            "score": sel["score"],
            "tier": sel["tier"],
            # verdict/basis 是旧字段名：QA 与剪辑表都按这两个名字读，保持兼容
            "verdict": sel["tier"],
            "hits": sel["hits"],
            "basis": sel["hits"],
            "why": sel["why"],
            "source": "rules",
        }
        if LEGACY_SEM_MATCH:
            rel["legacy_tag_hits"] = legacy_score(str(s.get("text", "")), m, s.get("keywords") or [])
        return rel

    @lru_cache(maxsize=None)
    def _audit_broll(media_path: str) -> Dict[str, Any]:
        try:
            if Path(media_path).is_file():
                from clip_boundary_audit import analyze_clip_safe_window

                return analyze_clip_safe_window(media_path) or {}
        except Exception:
            # 检测模块可选，单条素材解码失败也可用默认边距继续排片。
            pass
        return {}

    def _broll_take(
        item: Dict[str, Any], need_sec: float, *, secondary_in: Optional[float] = None,
    ) -> Optional[BrollTake]:
        duration = float(item.get("duration_sec", 3.0))
        if duration <= 0.0:
            return None
        if item["role"] == "code_broll":
            return BrollTake(0.0, min(duration, need_sec), duration, "", True)

        media_path = str(item["normalized_mp4"])
        bounds = _declared_safe_window(item, duration)
        reason = "manifest"
        if bounds is None:
            bounds = _declared_safe_window(_audit_broll(media_path), duration)
            reason = "audit_safe_window"
        if bounds is None:
            stability = item.get("stability_score")
            head, tail = _default_broll_handles(duration, need_sec, 1.0 if stability is None else float(stability))
            bounds = (head, duration - tail)
            reason = "default_handle_margin"
        safe_in, safe_out = bounds

        # 人工标废窗优先于自动检测。选片与落窗共用这些连续可用区间。
        spans: List[Tuple[float, float]] = []
        cursor = safe_in
        for start, end in _excluded_source_spans(item.get("excluded_windows") or []):
            if end <= cursor or start >= safe_out:
                continue
            if start - 0.05 > cursor:
                spans.append((cursor, start - 0.05))
            cursor = min(safe_out, end + 0.05)
        if cursor < safe_out:
            spans.append((cursor, safe_out))
        if not spans:
            return None

        floor = max_out_by_mp.get(media_path, 0.0) if secondary_in is None else 0.0
        hint = used_cursors[item["id"]] if secondary_in is None else secondary_in
        forward = [(max(lo, floor), hi) for lo, hi in spans if hi > max(lo, floor) + SOURCE_EPSILON_SEC]
        for candidates, fresh in ((forward, True), (spans, False)):
            for lo, hi in candidates:
                if hi - lo + SOURCE_EPSILON_SEC >= need_sec:
                    start = max(lo, min(hint, hi - need_sec))
                    return BrollTake(start, min(hi, start + need_sec), hi, reason, fresh)
        # 素材不够覆盖整镜时，优先保留可顺播的最长一段。该源耗尽后只回用干净区间。
        lo, hi = max(forward or spans, key=lambda span: span[1] - span[0])
        return BrollTake(lo, hi, hi, reason, bool(forward))

    def _covers_shot(take: BrollTake, need_sec: float) -> bool:
        return take.end - take.start + SOURCE_EPSILON_SEC >= need_sec

    def _record_broll_use(item: Dict[str, Any], take: BrollTake) -> None:
        bid = item["id"]
        path = str(item["normalized_mp4"])
        if item["role"] == "b_roll":
            max_out_by_mp[path] = max(max_out_by_mp.get(path, 0.0), take.end)
            used_cursors[bid] = take.end + 0.10
        usage_counts[bid] = usage_counts.get(bid, 0) + 1
        used_media_counts[path] = used_media_counts.get(path, 0) + 1

    def pick_best_broll(
        sent: Dict[str, Any],
        seg_dur: float,
        last_group: str,
        t_start: float,
        prev_media_path: str,
        prev_src_out: float,
        prev_motion: str = "static",
        prev_shot_size: str = "medium",
        forbid_code: bool = False,
        matching_live_only: bool = False,
        require_full: bool = False,
        require_opening_evidence: bool = False,
        code_only: bool = False,
    ) -> Optional[Tuple[Dict[str, Any], str, BrollTake]]:
        """这镜配哪条 B-roll：先按「这句台词讲了什么画面」排，再拿质量/时长/复用次数同分裁决。"""
        nonlocal taboo_blocked_count
        s_txt = str(sent.get("text", ""))
        s_kws = [str(k) for k in sent.get("keywords", [])]
        is_praise = any(cue in s_txt for cue in POSITIVE_PRAISE_CUES)
        candidates = [m for m in broll_pool if not (forbid_code and m.get("role") == "code_broll")] or broll_pool
        if code_only:
            candidates = [m for m in candidates if m["role"] == "code_broll"]
        if matching_live_only:
            candidates = [
                m for m in candidates
                if m["role"] == "b_roll"
                and not (set(m.get("taboo_tags") or []) & HARD_TABOO_ALWAYS or (is_praise and m.get("taboo_tags")))
                and (not require_opening_evidence or _has_opening_broll_evidence(m, s_txt))
                and sem_of_choice(m, sent, need_sec=seg_dur)["tier"] in ("hit", "weak")
            ]
        takes = {m["id"]: _broll_take(m, seg_dur) for m in candidates}
        candidates = [
            m for m in candidates
            if (take := takes[m["id"]]) is not None
            and (not (require_full or matching_live_only) or _covers_shot(take, seg_dur))
            and (not matching_live_only or take.fresh)
        ]
        if not candidates:
            return None

        # Agent 已经逐句指定素材（04b_broll_choice.json）→ 直接采信，只在表里标明是谁选的
        forced = broll_match.agent_choice_for(agent_choices, t_start) if agent_choices else None
        if forced:
            mp_forced = str(forced.get("media_path") or "")
            for m in candidates:
                if broll_match.same_media(str(m.get("normalized_mp4") or m.get("media_path") or ""), mp_forced):
                    return m, "agent", takes[m["id"]]

        blocked_here = False
        cand_rows: List[Dict[str, Any]] = []
        for m in candidates:
            m_tags_tab = {str(t) for t in (m.get("taboo_tags") or [])}
            hard_taboo = bool(m_tags_tab & HARD_TABOO_ALWAYS)
            soft_taboo = bool(is_praise and m_tags_tab)
            taboo_pen = 1000.0 if (hard_taboo or soft_taboo) else 0.0
            if taboo_pen > 0:
                blocked_here = True

            tags = [str(t) for t in (m.get("semantic_tags", []) + (m.get("manual_tags") or []))]
            fname_m = Path(str(m.get("source_path") or m.get("normalized_mp4") or "")).stem
            if LEGACY_SEM_MATCH:
                # 旧口径（字面 tag 命中）：仅用于 A/B 对照复现，不参与正常出片
                sem_hits = sum(1 for tg in tags if tg and (tg in s_txt or any(tg in k or k in tg for k in s_kws)))
                sem_weight = sem_hits * 6.5
            else:
                # 语义分是主序（0~100）：台词概念 ↔ 画面描述命中越多越前，质量/复用/时长已在 rank 里扣完
                m_sel = _score_broll(
                    s_txt, m, used_count=usage_counts.get(m["id"], 0), need_sec=seg_dur, keywords=s_kws,
                )
                sem_weight = float(m_sel["rank"]) * BROLL_SEM_RANK_WEIGHT
                if _is_signboard_wall_noise(m, s_txt):
                    sem_weight -= 20.0
            # 一手原始痛点词/物证优先匹配加权
            intel_bonus = 0.0
            if raw_pain_kws and any(pk in s_txt and (pk in fname_m or any(pk in tg for tg in tags)) for pk in raw_pain_kws):
                intel_bonus += 4.5
            if raw_proof_names and any(pn and pn in fname_m for pn in raw_proof_names):
                intel_bonus += 3.0
            # 景别交替鼓励与反方向运镜防撞墙惩罚
            cur_motion = str(m.get("camera_motion") or "static")
            cur_size = str(m.get("shot_size") or "medium")
            motion_pen = 8.0 if (prev_motion, cur_motion) in OPPOSING_MOTION else 0.0
            size_bonus = 2.0 if (cur_size != prev_shot_size) else 0.0

            cont_bonus = 1.5 if m.get("scene_group") == last_group else 0.0
            stab_v = float(m.get("stability_score", 0.8))
            sharp_v = float(m.get("sharpness_score", 40.0))
            stab_bonus = stab_v * 1.2
            sharp_bonus = min(1.5, sharp_v / 40.0)
            pool_has_crisp = any(float(c_m.get("sharpness_score") or 0.0) >= 350.0 for c_m in candidates)
            blur_shake_pen = BROLL_QUALITY_FLOOR_PEN if (
                m.get("role") != "code_broll"
                and (stab_v < BROLL_SHAKY_STAB_MIN or (pool_has_crisp and sharp_v < BROLL_BLURRY_SHARP_MIN))
            ) else 0.0
            mp_str = str(m.get("normalized_mp4") or "")
            take = takes[m["id"]]
            full_fresh = take.fresh and _covers_shot(take, seg_dur)
            same_media_pen = 0.0
            if mp_str == prev_media_path:
                if m.get("role") == "code_broll" or not full_fresh or take.start < prev_src_out - SOURCE_EPSILON_SEC:
                    same_media_pen = 800.0
                else:
                    # 真值里同一文件会被反复取不同时间窗（段/文件 4.3），轻微扣分即可；
                    # 旧值 18.0 等于「禁止回用」，是「每镜换地方」的直接推手。
                    same_media_pen = 3.0
            exhausted_pen = 500.0 if (m.get("role") == "b_roll" and not full_fresh) else 0.0
            if m.get("role") == "b_roll" and (not take.fresh or take.end - take.start < min(0.5, seg_dur * 0.5)):
                exhausted_pen += 1500.0
            dur_bonus = 2.0 if full_fresh else (0.5 if _covers_shot(take, seg_dur) else -2.0)
            live_bonus = 3.5 if (t_start < 3.0 and m.get("role") == "b_roll") else 0.0
            reuse_adj = scene_budget.reuse_bonus(
                style_id, mp_str, used_media_counts, len(used_media_counts), t_start,
            )
            # 两段裁决（这是原版的做法，也是「乱」的根因所在）：
            #   第一段只问「画面对不对得上这句台词」——语义分进同档就算达标；
            #   第二段在同档里按场景连续性裁决：回用同一条素材的另一个时间窗、同场景组、景别交替、
            #   稳定性——而不是每切一次就换一条从没用过的新素材（那样每秒换一个场景，观感必然乱）。
            sem_part = sem_weight + intel_bonus
            cont_part = (
                size_bonus + cont_bonus + stab_bonus + sharp_bonus
                + dur_bonus + live_bonus + reuse_adj
                - taboo_pen - same_media_pen - exhausted_pen - motion_pen - blur_shake_pen
            )
            cand_rows.append({"m": m, "sem": sem_part, "cont": cont_part})
        top_sem = max(r["sem"] for r in cand_rows)
        band = [r for r in cand_rows if r["sem"] >= top_sem - BROLL_SEM_TIE_BAND]
        best_m = max(band, key=lambda r: (r["cont"], r["sem"]))["m"]
        if blocked_here and not best_m.get("taboo_tags"):
            taboo_blocked_count += 1
        return best_m, "rules", takes[best_m["id"]]

    clips: List[Dict[str, Any]] = []
    last_group = "shoot_default"
    last_motion = "static"
    last_shot_size = "medium"
    last_aroll_zoom = 1.0
    punch_in_count = 0
    lcut_real_count = 0
    broll_takes: Dict[str, BrollTake] = {}
    sentence_pos = {s["id"]: i for i, s in enumerate(sentences)}

    def _aroll_take(
        item: Dict[str, Any], sent: Dict[str, Any], start: float, end: float, need_sec: float,
    ) -> Tuple[float, float, float]:
        path = str(item["normalized_mp4"])
        duration = float(item["duration_sec"])
        first_in = float(sent.get("source_in_sec", 0.0))
        raw_offset = start - float(sent["start_sec"])
        if sent.get("source_media") == path and raw_offset < 0.0:
            # 切点前移至句前气口接盘上一镜时，回退取同源视频句首前的自然开口准备帧
            src_in = max(0.0, first_in + raw_offset)
        else:
            src_in = first_in + max(0.0, raw_offset)
        limit = duration
        if sent.get("source_media") == path:
            last_out = float(sent.get("source_out_sec", duration))
            next_pos = sentence_pos[sent["id"]] + 1
            # 同一句或同源连续台词才可延展。跨机位或跳过了废片时，不能借后句的源窗口。
            for following in sentences[next_pos:]:
                if (str(following.get("source_media") or "") != path
                        or float(following["start_sec"]) >= end - 0.04
                        or abs(float(following.get("source_in_sec", 0.0)) - last_out) > 0.35):
                    break
                last_out = max(last_out, float(following.get("source_out_sec", duration)))
                next_pos += 1
            next_source_in = min(
                (float(s["source_in_sec"]) for s in sentences[next_pos:]
                 if s.get("source_media") == path and float(s.get("source_in_sec", -1.0)) > first_in),
                default=duration,
            )
            # 句尾气口原速顺延，出点受后句入点、素材末尾和标废窗约束。
            limit = min(duration, last_out + MAX_AROLL_TAIL_EXTENSION_SEC, max(last_out, next_source_in))
            for excluded_in, excluded_out in _excluded_source_spans(item.get("excluded_windows") or []):
                if excluded_in < limit and excluded_out > last_out:
                    limit = min(limit, max(last_out, excluded_in))
                    break
        else:
            src_in = min(max(0.0, duration - need_sec), src_in)
            first_in = 0.0

        floor = max(0.0, max_out_by_mp.get(path, 0.0))
        if raw_offset >= 0.0:
            floor = max(floor, first_in)
        src_in = max(floor, src_in)
        shortfall = max(0.0, src_in + need_sec - limit)
        src_in = max(floor, src_in - min(shortfall, MAX_AROLL_SHORTFALL_SEC))
        return src_in, min(limit, src_in + need_sec), first_in

    # ── A-roll 源末尾跨气口防冻帧：若本镜源文件已物理耗尽且后镜换新源文件并有前置准备余量，提前将切点吸附到句末 pause_start ──
    if usable_arolls and not broll_only_mode:
        aroll_dur_by_mp = {str(a["normalized_mp4"]): float(a.get("duration_sec") or 0.0) for a in usable_arolls}
        for idx_f in range(n_segs - 1):
            if roles[idx_f] != "a_roll" or roles[idx_f + 1] != "a_roll" or j_leads[idx_f + 1] != 0:
                continue
            f_s = cut_points[idx_f]["cut_frame"]
            f_e = cut_points[idx_f + 1]["cut_frame"]
            t_s, t_e = frames_to_sec(f_s, fps), frames_to_sec(f_e, fps)
            s_cur = find_sentence_for_time(t_s, cut_points[idx_f]["event"], t_end=t_e)
            s_nxt = find_sentence_for_time(t_e, cut_points[idx_f + 1]["event"], t_end=frames_to_sec(cut_points[idx_f + 2]["cut_frame"] if idx_f + 2 < n_segs else total_frames, fps))
            sm_c = str(s_cur.get("source_media") or "")
            sm_n = str(s_nxt.get("source_media") or "")
            if not sm_c or not sm_n or sm_c == sm_n or sm_c not in aroll_dur_by_mp or sm_n not in aroll_dur_by_mp:
                continue
            src_dur_c = aroll_dur_by_mp[sm_c]
            est_src_in = float(s_cur.get("source_in_sec", 0.0)) + max(0.0, t_s - float(s_cur["start_sec"]))
            if est_src_in + (t_e - t_s) <= src_dur_c + 0.05:
                continue
            ev_p = next(
                (
                    e for e in events
                    if str(e.get("sentence_id") or "") == str(s_cur.get("id") or "")
                    and e.get("type") in ("pause_start", "sentence_end")
                    and f_s + 14 <= int(e.get("frame", 0)) < f_e
                ),
                None,
            )
            if ev_p is None:
                continue
            pf = int(ev_p["frame"])
            pt = frames_to_sec(pf, fps)
            lead_need = t_e - pt
            if 0.03 <= lead_need <= 0.35 and float(s_nxt.get("source_in_sec", 0.0)) >= lead_need + 0.04:
                cut_points[idx_f + 1] = {"cut_frame": pf, "raw_frame": pf, "event": ev_p}

    for idx, cp in enumerate(cut_points):
        f_start = cp["cut_frame"]
        f_end = cut_points[idx + 1]["cut_frame"] if idx + 1 < n_segs else total_frames
        t_start = frames_to_sec(f_start, fps)
        t_end = frames_to_sec(f_end, fps)
        seg_dur = frames_to_sec(f_end - f_start, fps)
        ev = cp["event"]
        clip_id = f"clip_{idx+1:03d}"
        sent = find_sentence_for_time(t_start, ev, t_end=t_end)
        broll_sent = find_broll_topic_sentence(t_start, t_end, sent)
        has_prop_show = any(cue in sent.get("text", "") for cue in PROP_SHOW_CUES)

        use_aroll = (roles[idx] == "a_roll")
        broll_pick = None
        prev_mp = str(clips[-1]["media_path"]) if clips else ""
        prev_so = float(clips[-1]["source_out_sec"]) if clips else 0.0
        if kinetic_stage_mode and not use_aroll and source_aroll_valid[idx] and not audio_only_flags[idx]:
            broll_pick = pick_best_broll(
                broll_sent, seg_dur, last_group, t_start, prev_mp, prev_so,
                prev_motion=last_motion, prev_shot_size=last_shot_size, matching_live_only=True,
            )
            if broll_pick is None:
                use_aroll = True
                roles[idx] = "a_roll"
                cur_kb_sec -= seg_dur
        if choreography_broll and not use_aroll:
            window_locked = _choreography_aroll_locked(t_start, t_end)
            if not window_locked:
                broll_pick = pick_best_broll(
                    broll_sent, seg_dur, last_group, t_start, prev_mp, prev_so,
                    prev_motion=last_motion, prev_shot_size=last_shot_size, matching_live_only=True,
                    require_opening_evidence=fast_choreography and t_start < 3.0,
                )
            if broll_pick is None:
                choreography_has_unfilled_shot |= not window_locked
                if (str(sent.get("source_media") or "") in usable_aroll_paths
                        and _can_be_safe_aroll(idx, use_visual_frames=True)):
                    use_aroll = True
                    roles[idx] = "a_roll"
        if use_aroll:
            ar = next((a for a in usable_arolls if a["normalized_mp4"] == sent.get("source_media")), usable_arolls[0])
            ar_mp = str(ar["normalized_mp4"])
            src_in, src_out, first_in = _aroll_take(ar, sent, t_start, t_end, seg_dur)
            shortfall = seg_dur - (src_out - src_in)
            aroll_locked = (
                sent.get("a_roll_locked") or (kinetic_group_by_sentence.get(sent["id"]) or {}).get("a_roll_locked")
                or _overlaps_kinetic_locked(t_start, t_end)
            )
            if (
                shortfall > MAX_AROLL_SHORTFALL_SEC + SOURCE_EPSILON_SEC
                and not aroll_locked and not zero_broll_mode
                and not (fast_choreography and t_start < 3.0 and _choreography_aroll_locked(t_start, t_end))
                and (not kinetic_stage_mode or (t_start >= 3.0 and cur_kb_sec + seg_dur <= max_kinetic_b_sec))
            ):
                broll_pick = pick_best_broll(
                    broll_sent, seg_dur, last_group, t_start, prev_mp, prev_so,
                    prev_motion=last_motion, prev_shot_size=last_shot_size, matching_live_only=True,
                    require_opening_evidence=fast_choreography and t_start < 3.0,
                )
                if broll_pick is not None:
                    use_aroll = False
                    roles[idx] = "b_roll"
                    if kinetic_stage_mode:
                        cur_kb_sec += seg_dur
            if use_aroll and src_in >= src_out:
                # 同源已经用尽而本镜必须保留真人，只能回用本句的有效源区间。
                src_in = max(0.0, min(first_in, src_out - min(seg_dur, 2.0 / fps)))
                src_out = min(src_out, src_in + seg_dur)
        if use_aroll:
            max_out_by_mp[ar_mp] = max(float(max_out_by_mp.get(ar_mp, 0.0)), src_out)

            vf_extra = ""
            cur_zoom = 1.0
            if idx > 0 and roles[idx - 1] == "a_roll" and not has_prop_show and allow_punch:
                cur_zoom = punch_scale if last_aroll_zoom == 1.0 else 1.0
                if cur_zoom > 1.01:
                    vf_extra = _build_eye_locked_vf(ar.get("eye_anchor"), zoom=cur_zoom)
                    punch_in_count += 1
            last_aroll_zoom = cur_zoom

            kb_cfg = {"zoom_start": 1.0, "zoom_end": 1.06, "reason": "防>3s静止微缓推"} if (seg_dur > 3.0 and not vf_extra) else None
            purpose = (
                "开篇第一眼定调抓人" if idx == 0
                else ("展示实物与凭据细节" if has_prop_show
                      else ("拉近画面强调重点" if cur_zoom > 1.01
                            else ("收尾强化真实感与互动" if idx == n_segs - 1 else "真人出镜说话建信任")))
            )
            last_motion = str(ar.get("camera_motion") or "static")
            last_shot_size = "closeup" if cur_zoom > 1.01 else str(ar.get("shot_size") or "medium")
            clips.append({
                "clip_id": clip_id,
                "track_role": "a_roll",
                "media_path": ar["normalized_mp4"],
                "source_in_sec": src_in,
                "source_out_sec": src_out,
                "tight": True,
                "timeline_in_sec": t_start,
                "timeline_out_sec": t_end,
                "timeline_in_frame": f_start,
                "timeline_out_frame": f_end,
                "duration_sec": seg_dur,
                "audio_locked": True,
                "b_roll_overlay": False,
                "underlying_audio_untouched": True,
                "j_cut_lead_frames": j_leads[idx],
                "punch_in_zoom": cur_zoom,
                "ken_burns": kb_cfg,
                "vf_extra": vf_extra,
                "cut_event_id": ev["id"],
                "cut_event_type": ev["type"],
                "cut_event_frame": ev["frame"],
                "sentence_id": sent["id"],
                "sentence_text": sent["text"],
                "purpose": purpose,
            })
        if not use_aroll:
            if broll_pick is None and choreography_broll:
                if live_brolls_for_choreography:
                    # 实拍片存在真实 B-roll 池时优先从实拍池补足，绝不强行生成伪 PPT 图卡打断实拍质感
                    broll_pick = pick_best_broll(
                        broll_sent, seg_dur, last_group, t_start, prev_mp, prev_so,
                        prev_motion=last_motion, prev_shot_size=last_shot_size, forbid_code=True,
                    )
                if broll_pick is None and not live_brolls_for_choreography:
                    # 仅在完全没有任何可用实拍画面时才允许回落图卡兜底
                    if not any(m["role"] == "code_broll" for m in broll_pool):
                        generated = ensure_code_broll_pool(
                            pipeline_dir, sentences, [], track, style_id=style_id, total_dur=total_dur,
                        )
                        broll_pool.extend(generated)
                        usage_counts.update({m["id"]: 0 for m in generated})
                    broll_pick = pick_best_broll(
                        broll_sent, seg_dur, last_group, t_start, prev_mp, prev_so,
                        prev_motion=last_motion, prev_shot_size=last_shot_size, code_only=True,
                    )
            if broll_pick is None and not choreography_broll:
                cur_code_dur = sum(float(c["duration_sec"]) for c in clips if c.get("track_role") == "code_broll")
                forbid_code = (cur_code_dur + seg_dur) / max(0.1, total_dur) > 0.45 and any(
                    m["role"] == "b_roll" and (take := _broll_take(m, seg_dur)) is not None
                    and take.fresh and _covers_shot(take, seg_dur)
                    for m in broll_pool
                )
                broll_pick = pick_best_broll(
                    broll_sent, seg_dur, last_group, t_start, prev_mp, prev_so,
                    prev_motion=last_motion, prev_shot_size=last_shot_size, forbid_code=forbid_code,
                )
            if broll_pick is None:
                raise ValueError(f"{clip_id} 没有可用的外插画面区间，请补充素材或检查标废范围")
            br, br_source, take = broll_pick
            _record_broll_use(br, take)
            src_in, src_out = take.start, take.end
            last_group = str(br.get("scene_group", last_group))
            last_motion = str(br.get("camera_motion") or "static")
            last_shot_size = str(br.get("shot_size") or "medium")
            eff_role = str(br["role"])  # 统一保留 b_roll/code_broll，不写入悬空 "freeze"
            if eff_role == "b_roll":
                broll_takes[clip_id] = take
            kb_cfg = {"zoom_start": 1.0, "zoom_end": 1.06, "reason": "长镜头微推"} if (seg_dur > pacing["static_sec"] and eff_role == "b_roll") else None

            if j_leads[idx] < 0:
                lcut_real_count += 1
            if audio_only_flags[idx]:
                b_purpose = "原声保留、用特写画面自然盖住瑕疵镜头"
            elif t_start < 3.0:
                b_purpose = "开头前3秒切入细节画面抓人眼"
            elif j_leads[idx] > 0:
                b_purpose = "声音先响半拍再切入特写，衔接更顺滑"
            elif j_leads[idx] < 0:
                b_purpose = "凭据特写画面先露面再听人解释"
            else:
                b_purpose = "顺着台词补入对应细节画面"

            amb_audio_src = br.get("normalized_wav") or (br.get("source_path") if br.get("has_audio") else None)
            keep_amb = bool(
                ambient_enabled
                and eff_role == "b_roll"
                and br.get("has_audio", False)
                and amb_audio_src
                and Path(str(amb_audio_src)).is_file()
                and not br.get("source_is_code_scene", False)
                and any(w in str(broll_sent.get("text", "")) for w in ambient_words)
            )
            # 语义对位可审计：这个画面凭什么被认为「对得上这句台词」（概念命中就写命中的字面，零依据就是 none）
            sem_here = sem_of_choice(br, broll_sent, need_sec=seg_dur)
            sem_here["source"] = br_source
            clips.append({
                "clip_id": clip_id,
                "track_role": eff_role,
                "media_path": br["normalized_mp4"],
                "source_in_sec": src_in,
                "source_out_sec": src_out,
                "tight": True,
                "audio_only_masked": audio_only_flags[idx],
                "timeline_in_sec": t_start,
                "timeline_out_sec": t_end,
                "timeline_in_frame": f_start,
                "timeline_out_frame": f_end,
                "duration_sec": seg_dur,
                "audio_locked": False,
                "b_roll_overlay": True,
                "underlying_audio_untouched": True,
                "keep_broll_ambient": keep_amb,
                "ambient_audio_path": str(amb_audio_src) if keep_amb else None,
                "ambient_gain_db": ambient_gain_db if keep_amb else None,
                "j_cut_lead_frames": j_leads[idx],
                "punch_in_zoom": 1.0,
                "ken_burns": kb_cfg,
                "vf_extra": "",
                "cut_event_id": ev["id"],
                "cut_event_type": ev["type"],
                "cut_event_frame": ev["frame"],
                "sentence_id": broll_sent["id"],
                "sentence_text": broll_sent["text"],
                "purpose": b_purpose,
                "sem_match": sem_here,
                "media_signal": describe_clip(br)[:160],
            })

    first_3s_changes = sum(1 for c in clips[1:] if c["timeline_in_frame"] < first_3s_cutoff_f)
    jcut_real_count = sum(1 for c in clips if c.get("j_cut_lead_frames", 0) > 0)
    durs = [float(c["duration_sec"]) for c in clips]
    shot_cv = round(statistics.stdev(durs) / max(1e-5, statistics.mean(durs)), 3) if len(durs) >= 2 else 0.0
    code_dur = sum(float(c["duration_sec"]) for c in clips if c.get("track_role") == "code_broll")
    code_ratio = round(code_dur / max(0.1, total_dur), 3)
    # 真实插入画面（b_roll）与自制图卡（code_broll）分开统计：源的覆盖率只由真实画面构成，
    # 图卡是我们自己的补位手段，不能算进「与源一致」的口径
    broll_actual_dur = sum(float(c["duration_sec"]) for c in clips if c.get("track_role") == "b_roll")
    code_card_dur = sum(float(c["duration_sec"]) for c in clips if c.get("track_role") == "code_broll")
    broll_ratio_actual = round(broll_actual_dur / max(0.1, total_dur), 3)
    broll_ratio_with_cards = round((broll_actual_dur + code_card_dur) / max(0.1, total_dur), 3)
    if kinetic_stage_mode and usable_arolls:
        broll_policy_note += f"（实拍占比 {broll_ratio_actual:.1%}）"

    # 按风格谱 pip_composition_spec 为分镜挂载真·画中画构图参数 (pip_layout)
    pip_spec = (style_entry or {}).get("pip_composition_spec") or {}
    sent_by_id = {str(s.get("id")): s for s in sentences}
    explainer_windows = detect_explainer_broll_windows(sentences)

    # 消费 explainer_broll_windows：口播讲解数字/流程/对比句时，若镜头为真人出镜且整体代码图解占比不超 30%，
    # 自动切入动态讲解图解并挂载平滑圆环人像画中画（smooth_orbit_pip），主讲人平滑缩至安全角继续同步说话
    # 仅在当前风格允许讲解圆窗画中画时才自动插入动态讲解图解 + 圆环人像（纯全屏原生模板如 style_01 绝不强塞画中画）
    allow_explainer_pip = bool(
        (style_entry or {}).get("allow_explainer_pip", False)
        or str(pip_spec.get("mode") or "") == "smooth_orbit_pip"
    )
    explainer_applied_count = 0
    if allow_explainer_pip and explainer_windows and clips and not prefer_broll_only and usable_arolls:
        win_by_sid = {str(w["sentence_id"]): w for w in explainer_windows}
        gen_exp_dir = pipeline_dir / "04_generated_broll"
        gen_exp_dir.mkdir(parents=True, exist_ok=True)
        pal_exp = resolve_style_palette(style_id, track=track)
        cur_code_dur = sum(float(c.get("duration_sec", 0.0)) for c in clips if c.get("track_role") == "code_broll")
        for c_obj in clips:
            if explainer_applied_count >= 2:
                break
            if c_obj.get("track_role") != "a_roll":
                continue
            sid_c = str(c_obj.get("sentence_id") or "")
            ew = win_by_sid.get(sid_c)
            if not ew:
                continue
            cdur = float(c_obj.get("duration_sec", 0.0))
            if cdur < 1.6 or (cur_code_dur + cdur) / max(0.1, total_dur) > 0.30:
                continue
            exp_mp4 = gen_exp_dir / f"explainer_{sid_c}.mp4"
            exp_mode = str(ew.get("suggested_mode") or "kinetic_text")
            exp_title = str(ew.get("trigger_cue") or "核心要点")[:10]
            exp_text = str(ew.get("sentence_text") or c_obj.get("sentence_text") or "")
            try:
                render_code_clip(
                    exp_mp4,
                    mode=exp_mode,
                    title=exp_title,
                    items=[{"label": exp_title, "subtitle": exp_text[:20]}],
                    duration_sec=max(1.8, cdur),
                    accent_hex=pal_exp["accent_hex"],
                    bg_hex=pal_exp["bg_hex"],
                    style_id=style_id,
                    track=track,
                    backend="pillow",
                )
            except Exception:
                continue
            orig_aroll = str(c_obj.get("media_path") or "")
            orig_in = float(c_obj.get("source_in_sec", 0.0))
            c_obj["media_path"] = str(exp_mp4.resolve())
            c_obj["source_in_sec"] = 0.0
            c_obj["source_out_sec"] = round(cdur, 4)
            c_obj["track_role"] = "code_broll"
            c_obj["purpose"] = f"动态讲解图解({exp_mode}) + 角落圆环人像"
            c_obj["pip_layout"] = {
                "mode": "smooth_orbit_pip",
                "corner": "tr",
                "radius_px": 146,
                "center_y": 360,
                "border_hex": pal_exp["accent_hex"],
                "border_width": 4,
                "tag_text": "主讲人",
                "secondary_media_path": orig_aroll,
                "secondary_in_sec": round(max(0.0, orig_in), 4),
                "secondary_out_sec": round(max(0.0, orig_in) + cdur, 4),
            }
            cur_code_dur += cdur
            explainer_applied_count += 1
        code_ratio = round(cur_code_dur / max(0.1, total_dur), 3)

    pip_mode = str(pip_spec.get("mode") or "none").strip()
    if pip_mode in ("", "none", "null"):
        pip_mode = ""
    pip_applied_count = explainer_applied_count

    # 分层剪辑模型（人层 + 底图层）：有高占比抠像叠 B-roll 语法的风格（如 style_21）
    # 将连续主体讲解区改造为「前景抠像人物（口型同步）+ 底图 B-roll（按镜切换）」，避免 A/B 互切频闪。
    layered_model_info: Dict[str, Any] = {"enabled": False, "clips": 0, "seconds": 0.0, "coverage": 0.0, "runs": 0}
    original_layer_shots: Dict[str, Dict[str, Any]] = {}
    cut_elem_rule = element_rules.get_element_rule(style_id, "cutout")
    v_cut_rule = treatment_policy.style_rules(style_id).get("cutout") or {}
    _cut_scope = v_cut_rule.get("scope") or {}
    _cut_cov = float(element_rules.get_limit(style_id, "cutout", "max_coverage_ratio", default=0.0) or 0.0)
    _pip_windows = [w for w in (pip_spec.get("windows") or []) if isinstance(w, dict)]
    _matting_win = next((w for w in _pip_windows if w.get("matting")), None)
    _is_corner_cutout = bool(
        _matting_win and 0.35 <= float(_matting_win.get("scale") or 1.0) <= 0.95
    )
    _is_full_frame_cutout = bool(
        str(_cut_scope.get("window_scale_sec") or "") == "full_frame"
        and _cut_cov >= 0.08
        and int(element_rules.get_limit(style_id, "cutout", "max_runs", default=0) or 0) >= 1
    )
    has_person_over_broll = bool(
        (not kinetic_stage_mode)
        and cut_elem_rule and cut_elem_rule.get("present") and v_cut_rule.get("enabled")
        and ab_pattern not in ("depth_hook_then_framed_broll", "dual_window_broll_switcher", "container_internal_switch")
        and (
            _is_full_frame_cutout
            or pip_mode in ("vision_portrait_cutout_pip", "data_news_cutout_pip")
            or _is_corner_cutout
        )
    )
    live_b_for_layer = [m for m in broll_pool if m.get("role") == "b_roll"] or list(broll_pool)
    if (
        has_person_over_broll and clips and usable_arolls and live_b_for_layer
        and edit_profile != "documentary"
        and not prefer_broll_only and not broll_only_mode and not zero_broll_mode
    ):
        max_cov = float(element_rules.get_limit(style_id, "cutout", "max_coverage_ratio", default=0.65) or 0.65)
        max_seg_lim = float(element_rules.get_limit(style_id, "cutout", "max_seg_sec", default=2.52) or 2.52)
        max_runs_lim = max(1, int(element_rules.get_limit(style_id, "cutout", "max_runs", default=1) or 1))
        if _is_corner_cutout:
            target_layer_sec = min(max_seg_lim, total_dur * min(0.18, max_cov) * 0.85)
            fg_scale = float(_matting_win.get("scale") or 0.699)
            fg_tx = float(_matting_win.get("x") or 0.38)
            fg_ty = float(_matting_win.get("y") or -0.301)
            open_w_scale = fg_scale
        else:
            target_layer_cov = min(max_cov * 0.85, max(0.35, broll_target_ratio * 0.82))
            target_layer_sec = total_dur * target_layer_cov
            # 解析因果表前景缩放与纵坐标（默认 1.2 / -0.1，贴底不悬空）
            fg_scale = 1.2
            raw_sc = str(element_rules.get_driver(style_id, "cutout", "clip.scale / uniform_scale.scale", default="") or "")
            m_sc = re.search(r"中位\s*([\d.]+)", raw_sc)
            if m_sc:
                try:
                    fg_scale = max(1.0, min(1.5, float(m_sc.group(1))))
                except ValueError:
                    fg_scale = 1.2
            fg_tx = float((_matting_win or {}).get("x") or 0.0)
            fg_ty = -0.10
            raw_ty = str(element_rules.get_driver(style_id, "cutout", "clip.transform.y", default="") or "")
            m_ty = re.findall(r"-?\d+\.\d+", raw_ty)
            if len(m_ty) >= 2:
                try:
                    fg_ty = max(-0.35, min(0.15, (float(m_ty[0]) + float(m_ty[1])) / 2.0))
                except ValueError:
                    fg_ty = -0.10
            open_w_scale = float((_matting_win or {}).get("scale") or fg_scale)
        stroke_drv = str(element_rules.get_driver(style_id, "cutout", "matting.strokes[0].adjust_params.effects_adjust_size", default="") or "")
        _has_mat_stroke = any(bool(w.get("matting") and w.get("stroke_hex")) for w in _pip_windows)
        if _is_corner_cutout:
            outline_px_layer = 10 if (_matting_win.get("stroke_hex") and "0 段" not in stroke_drv) else 0
            torn_paper_layer = False
        else:
            has_mat_stroke_fx = (
                pip_mode == "vision_portrait_cutout_pip"
                or (_has_mat_stroke and "0 段" not in stroke_drv)
            )
            m_sz = re.search(r"\[\s*(0\.\d+)\s*\]", stroke_drv)
            if not has_mat_stroke_fx:
                outline_px_layer = 0
            elif m_sz and float(m_sz.group(1)) <= 0.15 and "撕" not in stroke_drv:
                outline_px_layer = 6
            else:
                outline_px_layer = 12
            torn_paper_layer = bool(("撕" in stroke_drv) or (style_id == "style_21" and outline_px_layer > 0))
        outline_hex_layer = str((_matting_win or {}).get("stroke_hex") or "#FFFFFF")[:7]

        deictic_words = set(PROP_SHOW_CUES) | set(DEICTIC_CUES)
        for cond in v_cut_rule.get("conditions") or []:
            ck = cond.get("check") or {}
            if ck.get("key") == "no_deictic_terms":
                deictic_words.update(str(t) for t in (ck.get("terms") or []) if t)
        aroll_by_mp = {str(a["normalized_mp4"]): a for a in usable_arolls}

        def _aroll_window_for_clip(c_item: Dict[str, Any]) -> Optional[Tuple[str, float, float]]:
            # 若原镜就是安全同步 A-roll，直接沿用其严锁后的源窗口，绝不跨句错位或引起倒带
            if str(c_item.get("track_role")) == "a_roll" and str(c_item.get("media_path") or "") in aroll_by_mp:
                return (
                    str(c_item["media_path"]),
                    float(c_item.get("source_in_sec") or 0.0),
                    float(c_item.get("source_out_sec") or 0.0),
                )
            t_s = float(c_item.get("timeline_in_sec") or 0.0)
            dur_s = float(c_item.get("duration_sec") or 0.0)
            t_e = t_s + dur_s
            ov_s = [
                s for s in sentences
                if min(t_e, float(s["end_sec"])) - max(t_s, float(s["start_sec"])) > 0.04
            ]
            if not ov_s:
                return None
            # 跨多句时仅允许同源且口播首尾连续相接（<=0.35s 停顿；全屏分层开场流允许跨 <=0.85s 气口停顿贴合后句），且不致倒带
            _cross_breath_cut = False
            if len(ov_s) > 1:
                same_sm = len({str(s.get("source_media") or "") for s in ov_s}) == 1
                gaps = [
                    float(ov_s[k + 1].get("source_in_sec", 0.0)) - float(ov_s[k].get("source_out_sec", 0.0))
                    for k in range(len(ov_s) - 1)
                ]
                contig_strict = all(abs(g) <= 0.35 for g in gaps)
                contig_breath = (not _is_corner_cutout) and all(-0.08 <= g <= 0.85 for g in gaps)
                if not (same_sm and (contig_strict or contig_breath)):
                    return None
                _cross_breath_cut = bool(same_sm and not contig_strict and contig_breath)
            s_obj = ov_s[0]
            last_s = ov_s[-1]
            sm = str(s_obj.get("source_media") or "")
            if sm not in aroll_by_mp:
                return None
            ar_item = aroll_by_mp[sm]
            if t_s < float(s_obj.get("start_sec") or 0.0) - 0.04:
                return None
            if _cross_breath_cut:
                off_end = max(0.0, t_e - float(last_s.get("start_sec") or 0.0))
                s_e_lim = min(float(ar_item.get("duration_sec") or 0.0), float(last_s.get("source_out_sec") or ar_item.get("duration_sec") or 0.0))
                sout = round(min(s_e_lim, float(last_s.get("source_in_sec") or 0.0) + off_end), 4)
                sin = round(max(float(s_obj.get("source_in_sec") or 0.0), sout - dur_s), 4)
                if sout <= sin + 0.04:
                    return None
                return sm, sin, sout
            off = max(0.0, t_s - float(s_obj.get("start_sec") or 0.0))
            sin = round(float(s_obj.get("source_in_sec") or 0.0) + off, 4)
            s_end = min(float(ar_item.get("duration_sec") or 0.0), float(last_s.get("source_out_sec") or ar_item.get("duration_sec") or 0.0))
            if sin + dur_s > s_end + 0.05 or sin < 0.0:
                return None
            sout = round(min(s_end, sin + dur_s), 4)
            return sm, sin, sout

        # 按「连续段」筛选合格分层镜，并保证同一源片不发生时间倒带
        _cut_lims = v_cut_rule.get("limits") or {}
        _min_face_h = float(_cut_lims.get("require_face_min_h") or 0.12)
        _min_face_w = float(_cut_lims.get("require_face_min_w") or 0.07)
        _has_manifest_faces = any(isinstance(a.get("faces"), list) for a in usable_arolls)

        def _aroll_has_valid_face(mp_path: str) -> bool:
            if not _has_manifest_faces:
                return True
            fs = (aroll_by_mp.get(mp_path) or {}).get("faces") or []
            return any(
                float((f.get("bbox") or {}).get("h") or 0.0) >= _min_face_h
                and float((f.get("bbox") or {}).get("w") or 0.0) >= _min_face_w
                for f in fs
                if isinstance(f, dict)
            )

        eligible_flags: List[bool] = []
        for ci, c_item in enumerate(clips):
            role_c = str(c_item.get("track_role") or "")
            dur_c = float(c_item.get("duration_sec") or 0.0)
            stxt_c = str(c_item.get("sentence_text") or "")
            if (
                role_c == "code_broll"
                or c_item.get("pip_layout")
                or c_item.get("audio_only_masked")
                or dur_c > max_seg_lim + 1e-6
                or any(dw in stxt_c for dw in deictic_words)
                or (_is_corner_cutout and (ci == 0 or float(c_item.get("timeline_in_sec") or 0.0) > 3.5))
            ):
                eligible_flags.append(False)
                continue
            win = _aroll_window_for_clip(c_item)
            eligible_flags.append(bool(win is not None and _aroll_has_valid_face(win[0])))

        # 找连续合格块，优先挑最长或前部连续块，控制连续段数 <= max_runs_lim
        blocks: List[List[int]] = []
        cur_blk: List[int] = []
        for ci, ok_f in enumerate(eligible_flags):
            if ok_f:
                cur_blk.append(ci)
            elif cur_blk:
                blocks.append(cur_blk)
                cur_blk = []
        if cur_blk:
            blocks.append(cur_blk)
        # 优先将前 1.2s 开场块作为连续第一段（注意 timeline_in_sec=0.0 不可被 or 99.0 误判为假值）
        def _blk_start_sec(blk_idx: List[int]) -> float:
            raw_t = clips[blk_idx[0]].get("timeline_in_sec")
            return float(raw_t) if raw_t is not None else 99.0

        blocks_sorted = sorted(
            blocks,
            key=lambda b: (
                0 if _blk_start_sec(b) <= 1.2 else 1,
                -sum(float(clips[i]["duration_sec"]) for i in b),
                b[0],
            ),
        )
        picked_indices: List[int] = []
        acc_layer_sec = 0.0
        runs_used = 0
        for blk in blocks_sorted:
            if runs_used >= max_runs_lim or acc_layer_sec >= target_layer_sec:
                break
            if _is_corner_cutout and _blk_start_sec(blk) > 3.5:
                continue
            blk_picked: List[int] = []
            for ci in blk:
                if acc_layer_sec >= target_layer_sec:
                    break
                blk_picked.append(ci)
                acc_layer_sec += float(clips[ci]["duration_sec"])
            if blk_picked:
                picked_indices.extend(blk_picked)
                runs_used += 1

        picked_set = set(picked_indices)
        # 单调校验：按时间顺序推进并剔除与前后同源镜头倒带的候选（倒踢清理避免级联倒带）
        changed_mono = True
        final_picked: set = set(picked_indices)
        while changed_mono:
            changed_mono = False
            last_src_out_map: Dict[str, float] = {}
            for ci, c_item in enumerate(clips):
                is_pk = ci in final_picked
                if is_pk:
                    win = _aroll_window_for_clip(c_item)
                    if win is None:
                        final_picked.discard(ci)
                        changed_mono = True
                        continue
                    mp_w, sin_w, sout_w = win
                    if mp_w in last_src_out_map and sin_w < last_src_out_map[mp_w] - 0.04:
                        final_picked.discard(ci)
                        changed_mono = True
                        continue
                    conflict_later = False
                    for cj in range(ci + 1, len(clips)):
                        if cj not in final_picked and str(clips[cj].get("track_role")) == "a_roll" and str(clips[cj].get("media_path")) == mp_w:
                            if float(clips[cj].get("source_in_sec") or 0.0) < sout_w - 0.04:
                                conflict_later = True
                            break
                    if conflict_later:
                        final_picked.discard(ci)
                        changed_mono = True
                        continue
                    last_src_out_map[mp_w] = max(last_src_out_map.get(mp_w, 0.0), sout_w)
                elif str(c_item.get("track_role")) == "a_roll":
                    mp_a = str(c_item.get("media_path") or "")
                    last_src_out_map[mp_a] = max(last_src_out_map.get(mp_a, 0.0), float(c_item.get("source_out_sec") or 0.0))

        # 正式应用分层改造
        layered_clips_cnt = 0
        layered_sec = 0.0
        for rank_idx, ci in enumerate(sorted(final_picked)):
            c_obj = clips[ci]
            dur_c = float(c_obj["duration_sec"])
            win = _aroll_window_for_clip(c_obj)
            if win is None:
                continue
            fg_mp, fg_sin, fg_sout = win
            if str(c_obj.get("track_role")) == "b_roll":
                if not _covers_shot(broll_takes[c_obj["clip_id"]], dur_c):
                    continue
                bg_mp = str(c_obj.get("media_path") or "")
                bg_in = float(c_obj.get("source_in_sec") or 0.0)
                bg_out = float(c_obj.get("source_out_sec") or bg_in)
                bg_sem = c_obj.get("sem_match")
                bg_sig = c_obj.get("media_signal")
            else:
                t_layer = float(c_obj["timeline_in_sec"])
                if choreography_broll and (
                    _choreography_aroll_locked(t_layer, float(c_obj["timeline_out_sec"]))
                    or broll_actual_dur + dur_c / 2.0 > total_dur * broll_target_ratio
                ):
                    continue
                sent_c = sent_by_id.get(str(c_obj.get("sentence_id") or "")) or {"text": c_obj.get("sentence_text", ""), "keywords": []}
                prev_bg = str((clips[ci - 1].get("pip_layout") or {}).get("secondary_media_path") or clips[ci - 1].get("media_path") or "") if ci > 0 else ""
                prev_bg_out = float((clips[ci - 1].get("pip_layout") or {}).get("secondary_out_sec") or clips[ci - 1].get("source_out_sec") or 0.0) if ci > 0 else 0.0
                evidence_sent = sent_c
                bg_pick = pick_best_broll(
                    sent_c, dur_c, last_group, t_layer,
                    prev_bg, prev_bg_out, prev_motion=last_motion, prev_shot_size=last_shot_size,
                    forbid_code=True, require_full=True, matching_live_only=choreography_broll,
                    require_opening_evidence=fast_choreography and t_layer < 3.0,
                )
                if (
                    bg_pick is None and fast_choreography and ci == 0
                    and not broll_match.concepts_detail(str(sent_c.get("text") or ""))
                ):
                    # 自我介绍继续显示同步人像，底图可由这段录制接下来的台词提供依据。
                    for next_sent in sentences[sentence_pos[c_obj["sentence_id"]] + 1:]:
                        if next_sent.get("source_media") != fg_mp:
                            break
                        bg_pick = pick_best_broll(
                            next_sent, dur_c, last_group, t_layer,
                            prev_bg, prev_bg_out, prev_motion=last_motion, prev_shot_size=last_shot_size,
                            matching_live_only=True,
                        )
                        if bg_pick is not None:
                            evidence_sent = next_sent
                            break
                if bg_pick is None:
                    continue
                br_pick, br_src, bg_take = bg_pick
                bg_mp = str(br_pick["normalized_mp4"])
                bg_in, bg_out = bg_take.start, bg_take.end
                _record_broll_use(br_pick, bg_take)
                last_group = str(br_pick.get("scene_group", last_group))
                bg_sem = sem_of_choice(br_pick, evidence_sent, need_sec=dur_c)
                if evidence_sent is not sent_c:
                    bg_sem.update(
                        score=broll_match.WEAK_MIN, tier=broll_match.TIER_WEAK, verdict=broll_match.TIER_WEAK,
                        why="开场同步人像的底图与随后同源台词同场景", evidence_sentence_id=evidence_sent["id"],
                    )
                bg_sem["source"] = br_src
                bg_sig = describe_clip(br_pick)[:160]
                if choreography_broll:
                    broll_actual_dur += dur_c

            if choreography_broll:
                original_layer_shots[c_obj["clip_id"]] = dict(c_obj)
            c_obj["track_role"] = "a_roll"
            c_obj["media_path"] = fg_mp
            c_obj["source_in_sec"] = fg_sin
            c_obj["source_out_sec"] = fg_sout
            c_obj["audio_locked"] = True
            c_obj["b_roll_overlay"] = True
            c_obj["layered_role"] = "person_over_broll"
            c_obj["cutout"] = True
            c_obj["purpose"] = "前景真人持续说话 + 底图切入对应细节（分层剪辑）"
            c_obj["sem_match"] = bg_sem
            c_obj["media_signal"] = bg_sig
            seg_t_in = float(c_obj.get("timeline_in_sec") or 0.0)
            seg_fg_scale = fg_scale
            seg_fg_ty = fg_ty
            if (not _is_corner_cutout) and open_w_scale >= 1.35 and seg_t_in < 3.0:
                if rank_idx == 0:
                    seg_fg_scale = round(min(1.60, open_w_scale), 3)
                    seg_fg_ty = float((_matting_win or {}).get("y") or fg_ty)
                elif rank_idx == 1:
                    seg_fg_scale = round(max(fg_scale, (open_w_scale + fg_scale) * 0.5), 3)
                elif seg_t_in < 1.8:
                    seg_fg_scale = round(max(fg_scale, fg_scale + 0.12), 3)
            c_obj["pip_layout"] = {
                **pip_spec,
                "mode": "layered_cutout_over_broll",
                "cutout": True,
                "single_source": False,
                "secondary_media_path": bg_mp,
                "secondary_in_sec": round(max(0.0, bg_in), 4),
                "secondary_out_sec": round(max(0.0, bg_out), 4),
                "fg_scale": seg_fg_scale,
                "fg_transform_x": fg_tx,
                "fg_transform_y": seg_fg_ty,
                "outline_px": outline_px_layer,
                "outline_hex": outline_hex_layer,
                "torn_paper": bool(torn_paper_layer),
                "bg_blur_sigma": 0,
            }
            layered_clips_cnt += 1
            layered_sec += dur_c
            pip_applied_count += 1

        if layered_clips_cnt > 0:
            # 重新按「全屏 B-roll + 分层底图 B-roll」统一口径核算 broll_ratio_actual
            broll_actual_dur = sum(
                float(c["duration_sec"]) for c in clips
                if c.get("track_role") == "b_roll"
                or (bool(c.get("b_roll_overlay")) and bool((c.get("pip_layout") or {}).get("secondary_media_path")))
            )
            broll_ratio_actual = round(broll_actual_dur / max(0.1, total_dur), 3)
            broll_ratio_with_cards = round((broll_actual_dur + code_card_dur) / max(0.1, total_dur), 3)
            layered_model_info = {
                "enabled": True,
                "clips": layered_clips_cnt,
                "seconds": round(layered_sec, 2),
                "coverage": round(layered_sec / max(0.1, total_dur), 3),
                "runs": runs_used,
                "fg_scale": fg_scale,
                "fg_transform_y": fg_ty,
                "outline_px": outline_px_layer,
            }
            print(f"[step4] 分层剪辑启用（人叠 B-roll）：{layered_clips_cnt} 镜 / {layered_sec:.1f}s ({layered_model_info['coverage']*100:.1f}%) · {runs_used} 连续段")

    # 关键帧推拉频次：严格按风格实测值（0=机位完全不动；>=80=几乎每镜都推/拉）
    kf_applied = _apply_keyframe_policy(clips, kf_target, total_dur) if kf_target is not None else None

    # A/B-roll 切换样式：按风格范式决定「哪些镜头挂画框/抠像、哪些保持纯净全屏」
    known_patterns = {
        "fullscreen_intercut", "zero_broll_keyframe_flow", "container_internal_switch",
        "cutout_broll_alternating", "depth_hook_then_framed_broll", "dual_window_broll_switcher",
    }
    pattern_handled = ab_pattern in known_patterns
    if pattern_handled and pip_mode and clips and not kinetic_stage_mode:
        live_b = [m for m in broll_pool if m.get("role") == "b_roll"] or list(broll_pool)
        rot = {"n": 0}

        def _alt_broll(main_media: str) -> Tuple[str, float]:
            alts = [m for m in live_b if str(m.get("normalized_mp4") or "") not in ("", main_media)]
            if not alts:
                return "", 0.0
            m_pick = alts[rot["n"] % len(alts)]
            rot["n"] += 1
            return str(m_pick.get("normalized_mp4")), float(m_pick.get("best_in_sec", 0.0))

        def _speaker_for(c_obj: Dict[str, Any]) -> Tuple[str, float]:
            cur_s = sent_by_id.get(str(c_obj.get("sentence_id") or "")) or {}
            sm = str(cur_s.get("source_media") or "")
            if not sm or sm not in usable_aroll_paths or not Path(sm).is_file():
                return "", 0.0
            s_base = float(cur_s.get("start_sec", 0.0))
            off = max(0.0, float(c_obj.get("timeline_in_sec", s_base)) - s_base)
            return sm, round(max(0.0, float(cur_s.get("source_in_sec", 0.0)) + off), 4)

        def _dual_cfg(c_obj: Dict[str, Any], sec_media: str, sec_in: float) -> Dict[str, Any]:
            return {
                **pip_spec, "mode": pip_mode, "single_source": False,
                "secondary_media_path": sec_media,
                "secondary_in_sec": round(max(0.0, sec_in), 4),
                "secondary_out_sec": round(max(0.0, sec_in) + float(c_obj.get("duration_sec", 1.5)), 4),
            }

        hook_left = int(ab_spec.get("hook_clip_budget") or 0)
        a_seen = -1
        for c_obj in clips:
            role = c_obj.get("track_role")
            lay_mode_cur = str((c_obj.get("pip_layout") or {}).get("mode") or "")
            if (
                role == "code_broll"
                or c_obj.get("pip_layout")
                or c_obj.get("layered_role") == "person_over_broll"
                or lay_mode_cur == "layered_cutout_over_broll"
            ):
                continue
            # 分层模型已接管成段抠像时，块外 A-roll 保持全屏硬切，不散撒单镜抠像抢占 max_runs 名额
            if layered_model_info.get("enabled") and ab_pattern == "cutout_broll_alternating":
                continue
            main_media = str(c_obj.get("media_path") or "")
            cfg: Optional[Dict[str, Any]] = None
            if ab_pattern == "container_internal_switch":
                # 容器（拍立得/圆角框/聚光圆窗/羽化窗/顶栏）外壳恒定，A/B-roll 都在容器内部切换，绝不破框
                cfg = {**pip_spec, "mode": pip_mode, "single_source": True}
            elif ab_pattern == "depth_hook_then_framed_broll":
                if role == "a_roll":
                    a_seen += 1
                    cur_s = sent_by_id.get(str(c_obj.get("sentence_id") or "")) or {}
                    emo = str(cur_s.get("emotion_or_info") or "")
                    if hook_left > 0 and (a_seen == 0 or emo in ("hook", "twist") or a_seen % 2 == 0):
                        c_kws = [str(k).strip() for k in (cur_s.get("keywords") or []) if 2 <= len(str(k).strip()) <= 4]
                        clean = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", str(cur_s.get("text") or ""))[:4]
                        cfg = {**pip_spec, "mode": pip_mode, "single_source": True,
                               "giant_back_text": c_kws[0] if c_kws else (clean or "核心真相")}
                        hook_left -= 1
                else:
                    # B-roll 也用该风格的真值几何，不再硬套「背景虚化卡片」这张别人的版面
                    cfg = {**pip_spec, "mode": pip_mode, "single_source": True}
            elif ab_pattern == "cutout_broll_alternating":
                if role == "a_roll":
                    cfg = {**pip_spec, "mode": pip_mode, "single_source": True}
                    if not choreography_broll:
                        sec_m, sec_i = _alt_broll(main_media)
                        if sec_m:
                            cfg = _dual_cfg(c_obj, sec_m, sec_i)
            elif ab_pattern == "dual_window_broll_switcher":
                # 真草稿里主轨缩成小窗且叠加层是「大画面」时（如 style_09），B-roll 段必须把真人塞回窗口里
                if pip_mode in ("dark_doc_circle_avatar_pip", "geometry_pip_main_shrunk"):
                    if role == "a_roll":
                        sec_m, sec_i = _alt_broll(main_media)
                        cfg = _dual_cfg(c_obj, sec_m, sec_i) if sec_m else None
                    else:
                        spk_m, spk_i = _speaker_for(c_obj)
                        if spk_m:
                            cfg = _dual_cfg(c_obj, main_media, float(c_obj.get("source_in_sec", 0.0)))
                            cfg.update({"swap_main_secondary": True, "speaker_media_path": spk_m, "speaker_in_sec": spk_i})
                elif role == "a_roll":
                    sec_m, sec_i = _alt_broll(main_media)
                    cfg = _dual_cfg(c_obj, sec_m, sec_i) if sec_m else None
            if cfg:
                c_obj["pip_layout"] = cfg
                pip_applied_count += 1

    if pip_mode and clips and not pattern_handled and not kinetic_stage_mode:
        live_brolls = [m for m in broll_pool if m.get("role") == "b_roll"] or list(broll_pool)
        # 单源画框/景深类：只需当前主镜即可渲染（无需第二路副镜）
        single_source_frame_modes = {
            "black_polaroid_frame_pip",
            "blur_bg_center_card_pip",
            "paper_craft_card_pip",
            "minimal_white_note_card_pip",
            "top_banner_evidence_pip",
            "person_behind_giant_text_pip",
            "top_white_rounded_frame_pip",
            "black_spotlight_circle_pip",
            # 真草稿几何驱动：主轨推近/缩窗只需当前主镜
            "geometry_pip_main_punch_in",
            "geometry_pip_main_shrunk",
        }
        # 双源构图类（其中 vision_portrait_cutout_pip / data_news_cutout_pip / linear_split_feather_pip 无副镜时也可独立单源成立）
        full_dual_modes = {
            "dark_doc_circle_avatar_pip",
            "linear_split_feather_pip",
            "dual_gold_frame_pip",
            "vision_portrait_cutout_pip",
            "data_news_cutout_pip",
            # 真草稿几何驱动：窗口构图需要第二路副素材
            "geometry_pip_window",
        }
        self_sufficient_fallback_modes = {
            "vision_portrait_cutout_pip",
            "data_news_cutout_pip",
            "linear_split_feather_pip",
            "dark_doc_circle_avatar_pip",
            # 无副镜时至少把主轨真值几何（推近/缩窗）落地，不硬套别人的版面
            "geometry_pip_window",
            "geometry_pip_main_shrunk",
        }
        first_kw_back = ""
        if sentences:
            s0_kws = [str(k).strip() for k in (sentences[0].get("keywords") or []) if 2 <= len(str(k).strip()) <= 5]
            first_kw_back = s0_kws[0] if s0_kws else re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", str(sentences[0].get("text") or ""))[:4]
        for c_i, c_obj in enumerate(clips):
            lay_mode_cur = str((c_obj.get("pip_layout") or {}).get("mode") or "")
            if (
                c_obj.get("track_role") == "code_broll"
                or c_obj.get("pip_layout")
                or c_obj.get("layered_role") == "person_over_broll"
                or lay_mode_cur == "layered_cutout_over_broll"
            ):
                continue
            if layered_model_info.get("enabled") and ab_pattern == "cutout_broll_alternating":
                continue
            main_media = str(c_obj.get("media_path") or "")
            if pip_mode in single_source_frame_modes:
                extra_cfg: Dict[str, Any] = {}
                if pip_mode == "person_behind_giant_text_pip":
                    cur_s = sent_by_id.get(str(c_obj.get("sentence_id") or "")) or {}
                    c_kws = [str(k).strip() for k in (cur_s.get("keywords") or []) if 2 <= len(str(k).strip()) <= 4]
                    extra_cfg["giant_back_text"] = c_kws[0] if c_kws else (first_kw_back or "核心真相")
                c_obj["pip_layout"] = {
                    **pip_spec,
                    **extra_cfg,
                    "mode": pip_mode,
                    "single_source": True,
                }
                pip_applied_count += 1
                continue

            should_apply = (
                pip_mode in full_dual_modes
                or (c_obj.get("track_role") == "b_roll" and bool(usable_arolls))
                or (c_i % 2 == 1 and bool(live_brolls))
            )
            if not should_apply:
                continue

            sec_media = ""
            sec_in = 0.0
            if c_obj.get("track_role") == "a_roll":
                alt_b = next(
                    (m for m in live_brolls if str(m.get("normalized_mp4") or "") not in ("", main_media)),
                    None,
                )
                if alt_b:
                    sec_media = str(alt_b.get("normalized_mp4"))
                    sec_in = float(alt_b.get("best_in_sec", 0.0))
            else:
                # 优先绑定当前句的真实口播源与同步时间点（保持口型同步）
                cur_sent = sent_by_id.get(str(c_obj.get("sentence_id") or "")) or {}
                s_src_media = str(cur_sent.get("source_media") or "")
                if s_src_media and s_src_media != main_media and Path(s_src_media).is_file():
                    sec_media = s_src_media
                    s_base = float(cur_sent.get("start_sec", 0.0))
                    s_src_in = float(cur_sent.get("source_in_sec", 0.0))
                    sec_in = max(0.0, s_src_in + max(0.0, float(c_obj.get("timeline_in_sec", s_base)) - s_base))
                else:
                    alt_b = next(
                        (m for m in live_brolls if str(m.get("normalized_mp4") or "") not in ("", main_media)),
                        None,
                    )
                    if alt_b:
                        sec_media = str(alt_b.get("normalized_mp4"))
                        sec_in = float(alt_b.get("best_in_sec", 0.0))

            # 严禁同源套娃：无异源副镜时仅允许真抠像/羽化分屏/纯黑面板类单源成立，其余跳过
            if not sec_media or sec_media == main_media:
                if pip_mode in self_sufficient_fallback_modes:
                    c_obj["pip_layout"] = {
                        **pip_spec,
                        "mode": pip_mode,
                        "single_source": True,
                    }
                    pip_applied_count += 1
                continue

            c_obj["pip_layout"] = {
                **pip_spec,
                "mode": pip_mode,
                "single_source": False,
                "secondary_media_path": sec_media,
                "secondary_in_sec": round(max(0.0, sec_in), 4),
                "secondary_out_sec": round(max(0.0, sec_in) + float(c_obj.get("duration_sec", 1.5)), 4),
            }
            pip_applied_count += 1

    if kinetic_stage_mode and clips and usable_arolls:
        # 挂载「自抠像人后弹幕」切片（style_02 核心特性：仅对真正存在 behind_person 弹幕的痛点时段开启同源 1:1 自抠像）
        danmaku_spans = [
            (float(it["start_sec"]), float(it["end_sec"]))
            for it in (kinetic_stage_plan.get("items") or [])
            if str(it.get("z_layer") or "") == "behind_person"
        ]
        max_self_matte_sec = min(8.5, max(2.0, total_dur * 0.18))
        used_self_matte = 0.0
        for c_obj in clips:
            if c_obj.get("track_role") != "a_roll":
                continue
            cs, ce = float(c_obj.get("timeline_in_sec") or 0.0), float(c_obj.get("timeline_out_sec") or 0.0)
            cd = max(0.0, ce - cs)
            if cd > 2.48 or not _overlaps_matting_window(cs, ce):
                continue
            if danmaku_spans and not any(min(ce, de) - max(cs, ds) >= 0.18 for ds, de in danmaku_spans):
                continue
            if used_self_matte + cd > max_self_matte_sec:
                continue
            c_obj["pip_layout"] = {
                "mode": "self_matting_behind_text",
                "cutout": True,
                "self_matting": True,
                "single_source": True,
                "fg_scale": 1.0,
                "x": 0.0,
                "y": 0.0,
                "outline_px": 0,
                "secondary_media_path": str(c_obj.get("media_path") or ""),
            }
            used_self_matte += cd
            pip_applied_count += 1

    # 副镜渲染按整镜时长读源。安全区间如果撑不满，就保留原镜的单源构图。
    live_broll_by_path = {str(m["normalized_mp4"]): m for m in broll_pool if m["role"] == "b_roll"}
    for c_obj in clips:
        lay = c_obj.get("pip_layout") or {}
        secondary = live_broll_by_path.get(str(lay.get("secondary_media_path") or ""))
        if secondary is None:
            continue
        dur = float(c_obj["duration_sec"])
        take = _broll_take(secondary, dur, secondary_in=float(lay.get("secondary_in_sec") or 0.0))
        if take is not None and _covers_shot(take, dur):
            lay.update(secondary_in_sec=take.start, secondary_out_sec=take.end, boundary_trim_reason=take.reason)
        else:
            for key in ("secondary_media_path", "secondary_in_sec", "secondary_out_sec",
                        "swap_main_secondary", "speaker_media_path", "speaker_in_sec"):
                lay.pop(key, None)
            lay["single_source"] = True

    # 会裁切的构图（小窗/缩窗/推近/相框/圆窗）先在剪辑表里定好人脸锚点：
    # 剪辑表自己就能交代"这个窗口是按脸取的还是素材里根本没脸"，渲染与导出共用同一份依据
    CUT_MODES = {
        "geometry_pip_window", "geometry_pip_main_shrunk", "geometry_pip_main_punch_in",
        "blur_bg_center_card_pip", "smooth_orbit_pip", "black_polaroid_frame_pip",
        "person_behind_giant_text_pip", "vision_portrait_cutout_pip", "data_news_cutout_pip",
        "layered_cutout_over_broll", "self_matting_behind_text",
    }
    try:
        import face_framing

        face_framing.set_cache_path(pipeline_dir / "state" / "face_anchors.json")
        face_center_count = 0
        no_face_count = 0
        for c_obj in clips:
            lay = c_obj.get("pip_layout")
            if not isinstance(lay, dict) or str(lay.get("mode") or "") not in CUT_MODES:
                continue
            dur = float(c_obj.get("duration_sec", 1.5))
            mid = float(c_obj.get("source_in_sec", 0.0)) + dur / 2.0  # 锚点要取源片时间，不是时间线时间
            anchor = face_framing.anchor_for(str(c_obj.get("media_path") or ""), mid, dur)
            sec_a = None
            if lay.get("secondary_media_path"):
                sec_a = face_framing.anchor_for(str(lay["secondary_media_path"]),
                                                float(lay.get("secondary_in_sec", 0.0)) + dur / 2.0, dur)
            lay["face_anchor"] = anchor
            lay["secondary_face_anchor"] = sec_a
            lay["face_framing"] = "face_center" if anchor else "no_face_center_crop"
            face_center_count += 1 if anchor else 0
            no_face_count += 0 if anchor else 1
        if face_center_count or no_face_count:
            print(f"[step4] 小窗/推近取景：按人脸 {face_center_count} 处，素材里没脸退回居中 {no_face_count} 处")
    except Exception as exc:  # 没有 ffmpeg / 没有视觉检测时不许假装做过
        print(f"[step4] [WARN] 人脸锚点未计算（{exc}），小窗将按画面正中取景")

    # 抠像触发条件：风格会抠像 ≠ 每一镜都该抠（2026-11-24 用户实测「讲解效果图的地方，把讲解图给扣没了」）。
    # 决策口唯一是 `treatment_policy`（规则来自 forensic/style_trigger_rules_visual.json），
    # 降级写口唯一是 `pip_policy.apply_cutout_gate`（本模块通过 post_decide 把改判交给它，不另写一套）。
    try:
        cutout_gate = treatment_policy.apply_cutout_policy(clips, style_entry, pipeline_dir, style_id)
        print(treatment_policy.summarize_cutout(cutout_gate))
        for _r in cutout_gate.get("rows") or []:
            if not _r.get("allowed"):
                print(f"[step4]   不抠 @{float(_r.get('timeline_in_sec') or 0):6.2f}s "
                      f"{_r.get('media')}：{_r.get('reason')}")
    except Exception as _pe:
        # 门槛坏了就保守不抠：宁可少一个手法，也不能把观众要看的东西抠没
        for _c in clips:
            _lay = _c.get("pip_layout")
            if isinstance(_lay, dict) and _lay.get("cutout"):
                _lay["cutout"] = False
                _lay["cutout_gated_reason"] = f"抠像触发条件判定异常（{_pe}），保守不抠"
        cutout_gate = {"error": str(_pe), "candidates": 0, "allowed": 0, "dropped": 0, "rows": []}
        print(f"[step4] [WARN] 抠像触发条件未执行（{_pe}），本片一律不抠")

    if original_layer_shots:
        # 分层不可用时回到已经选好的原镜，保留合法实拍及其语义依据。
        kept_layers = set()
        for i, clip in enumerate(clips):
            original = original_layer_shots.get(clip["clip_id"])
            if original is None:
                continue
            layout = clip.get("pip_layout") or {}
            if layout.get("cutout") and layout.get("secondary_media_path"):
                kept_layers.add(i)
            else:
                clip.clear()
                clip.update(original)
                pip_applied_count -= 1
        if len(kept_layers) < len(original_layer_shots):
            layered_sec = sum(float(clips[i]["duration_sec"]) for i in kept_layers)
            layered_model_info.update(
                enabled=bool(kept_layers), clips=len(kept_layers), seconds=round(layered_sec, 2),
                coverage=round(layered_sec / max(0.1, total_dur), 3),
                runs=sum(i - 1 not in kept_layers for i in kept_layers),
            )
            broll_actual_dur = layered_sec + sum(
                float(c["duration_sec"]) for c in clips if c["track_role"] == "b_roll"
            )
            broll_ratio_actual = round(broll_actual_dur / max(0.1, total_dur), 3)
            broll_ratio_with_cards = round((broll_actual_dur + code_card_dur) / max(0.1, total_dur), 3)

    if choreography_broll and broll_ratio_actual < broll_band[0]:
        broll_policy_note += f"。可用且对得上台词的实拍覆盖 {broll_ratio_actual:.1%}，保留其余口播镜头"

    # 风格变速可多吃源帧，但仍要停在已选连续安全区间内。
    broll_speed_limits: Dict[str, float] = {}
    for c_obj in clips:
        if c_obj["track_role"] == "b_roll":
            path = str(c_obj["media_path"])
            limit = broll_takes[c_obj["clip_id"]].safe_out
            broll_speed_limits[path] = min(broll_speed_limits.get(path, limit), limit)
    speed_manifest = {
        **manifest,
        "items": [
            {**m, "duration_sec": broll_speed_limits[str(m["normalized_mp4"])]}
            if str(m.get("normalized_mp4")) in broll_speed_limits else m
            for m in manifest["items"]
        ],
    }
    try:
        speed_plan = treatment_policy.apply_speed_policy(clips, style_entry, pipeline_dir, style_id, speed_manifest)
        print(treatment_policy.summarize_speed(speed_plan))
        for _r in speed_plan.get("rows") or []:
            if _r.get("allowed"):
                print(f"[step4]   变速 {_r.get('speed')}× @{_r.get('clip_id')}：{_r.get('reason')}")
    except Exception as _se:
        for _c in clips:
            if _c.get("speed") not in (None, 1.0):
                _c["speed"] = 1.0
        speed_plan = {"error": str(_se), "applied": 0, "rows": []}
        print(f"[step4] [WARN] 变速触发条件未执行（{_se}），全片原速")

    # 分层和风格处理可能改过源窗口，按最终可播放时长复查。原声时间线始终锁定。
    for c_obj in clips:
        role = c_obj["track_role"]
        if role == "b_roll":
            take = broll_takes[c_obj["clip_id"]]
            c_obj["source_out_sec"] = min(c_obj["source_out_sec"], take.safe_out)
            c_obj["boundary_trim_reason"] = take.reason
            c_obj["handles_guarded"] = True
        elif role == "code_broll":
            c_obj["handles_guarded"] = True
        elif contract and role == "a_roll":
            c_obj["speed"] = 1.0
        dur = float(c_obj["duration_sec"])
        source_span = max(0.0, c_obj["source_out_sec"] - c_obj["source_in_sec"])
        speed = clamp_speed(c_obj.get("speed"))
        if (role == "b_roll" and source_span > 0.0
                and dur - source_span / speed > MAX_AROLL_SHORTFALL_SEC + SOURCE_EPSILON_SEC):
            # 补镜的安全区间放不满时可以放慢画面。
            speed = clamp_speed(source_span / dur)
            c_obj["speed"] = speed
            c_obj["speed_reason"] = "可用画面比镜头短，画面放慢顺播，原声保持不动"
            c_obj.update(keep_broll_ambient=False, ambient_audio_path=None, ambient_gain_db=None)
        shortfall = dur - source_span / speed
        c_obj["freeze_tail"] = shortfall > 1.0 / fps + SOURCE_EPSILON_SEC
        warn_threshold = 1.0 / fps if role == "a_roll" else MAX_AROLL_SHORTFALL_SEC
        if shortfall > warn_threshold + SOURCE_EPSILON_SEC:
            print(f"[step4] [WARN] {c_obj['clip_id']} 的可用画面仍短 {shortfall:.2f}s，请补充可用素材")

    # 转场 / 叠加层 / 画面特效：step4 不铺它们（铺在套用端），但要把「这一片一处都没套」和为什么记下来，
    # 否则报告里看不出到底是「没这个能力」还是「按条件拒绝了」。
    _vrules = treatment_policy.style_rules(style_id)
    _n_boundaries = max(0, len(clips) - 1)
    try:
        _rules_rel = str(treatment_policy.RULES_PATH.resolve().relative_to(
            Path(__file__).resolve().parents[2]))
    except Exception:
        _rules_rel = str(treatment_policy.RULES_PATH)
    treatment_plan = {
        "rules_source": _rules_rel,
        "style_id": style_id,
        "transition": {
            "applied": 0,
            "dropped": _n_boundaries,
            "default": "hard_cut",
            "reason": (_vrules["transition"].get("default_reason")
                       or "默认硬切（这套风格源草稿转场数 = 0）"),
            "allow_when": (_vrules["transition"].get("scope") or {}).get("allow"),
            "forbid": (_vrules["transition"].get("scope") or {}).get("forbid"),
            "scope": (_vrules["transition"].get("scope") or {}).get("forbid_note"),
        },
        "cutout": {
            "enabled": cutout_gate.get("enabled"),
            "candidates": cutout_gate.get("candidates"),
            "allowed": cutout_gate.get("allowed"),
            "dropped": cutout_gate.get("dropped"),
            "used_sec": cutout_gate.get("used_sec"),
            "policy": cutout_gate.get("policy"),
            "scope": cutout_gate.get("scope"),
            "rows_moved_to": "cutout_gate.rows",
            "note_cn": cutout_gate.get("note_cn"),
        },
        "speed": speed_plan,
        "overlay": {
            "applicable": False,
            "enabled": bool(_vrules["overlay"].get("enabled")),
            "reason": "step4 不铺贴纸/叠加层；贴纸在套用端（apply_replica_template）按同一份规则判",
            "limits": _vrules["overlay"].get("limits"),
        },
        "effect": {
            "applicable": False,
            "enabled": bool(_vrules["effect"].get("enabled")),
            "reason": "step4 不铺画面特效；特效在套用端（apply_replica_template）按同一份规则判",
            "limits": _vrules["effect"].get("limits"),
        },
        "note_cn": ("每一项视觉处理都过 forensic/style_trigger_rules_visual.json 的条件："
                    "没满足就不套，理由逐镜记在对应 rows 里。"),
    }

    # 真实测算首帧视觉密度：文件存在且非废画、且非低对比死黑
    first_c = clips[0] if clips else {}
    first_item = next((m for m in manifest.get("items", []) if m.get("normalized_mp4") == first_c.get("media_path")), {})
    first_clip_ok = bool(
        clips
        and Path(str(first_c.get("media_path", ""))).is_file()
        and first_item.get("video_grade", "usable") != "ng"
        and (first_c.get("track_role") in ("a_roll", "code_broll") or float(first_item.get("sharpness_score", 25.0)) >= 12.0)
    )

    # 语义对位汇总 + 门禁判定都在写 EDL 前算好：门禁不达标也要先把账写下来，便于事后追
    sem_summary = summarize_broll_semantic(clips)
    sem_gate = broll_match.check_semantic_gate(sem_summary, allow=allow_broll_mismatch)
    if choreography_has_unfilled_shot and live_brolls_for_choreography and not sem_summary["clips"]:
        # 实拍未能通过选用条件，即使剪辑表退回纯口播，显式语义门禁仍需如实给出判定。
        sem_gate.update(
            raw_pass=False, waived=allow_broll_mismatch,
            fails=["提供的实拍素材里没有可与台词对齐的可用镜头，本片保留口播画面"],
        )
        sem_gate["pass"] = allow_broll_mismatch
    sem_summary["gate"] = sem_gate

    edl = {
        "step": "04_build_edl",
        "fps": fps,
        "tight": True,
        "style_id": style_id,
        "choreography_applied": choreography_applied,
        "edit_profile": edit_profile,
        "canvas": {"width": CANVAS_WIDTH, "height": CANVAS_HEIGHT},
        "total_duration_sec": total_dur,
        "total_frames": total_frames,
        "j_cut_default_frames": j_cut_frames,
        "j_cut_applied_count": jcut_real_count,
        "l_cut_applied_count": lcut_real_count,
        "punch_in_count": punch_in_count,
        "pip_mode": pip_mode or ("smooth_orbit_pip" if explainer_applied_count > 0 else "none"),
        "ab_switch_pattern": ab_pattern or "legacy",
        "ab_target_median_shot_sec": ab_spec.get("target_median_shot_sec"),
        "kf_per_min_target": kf_target,
        "keyframe_moves_applied": kf_applied,
        "pip_applied_count": pip_applied_count,
        "layered_model": layered_model_info,
        "kinetic_stage_enabled": bool(kinetic_stage_mode),
        "kinetic_stage_stats": kinetic_stage_plan.get("stats") if kinetic_stage_mode else None,
        "cutout_gate": cutout_gate,
        "explainer_broll_windows": explainer_windows,
        "explainer_applied_count": explainer_applied_count,
        "shot_duration_cv": shot_cv,
        "code_broll_ratio": code_ratio,
        "broll_ratio_target": round(broll_target_ratio, 3),
        "broll_ratio_actual": broll_ratio_actual,
        "broll_ratio_with_cards": broll_ratio_with_cards,
        "broll_semantic": sem_summary,
        "broll_semantic_note": (
            "外来画面与它压着的那句台词的语义对位（逐镜看画面描述与台词的概念）："
            "hit=有概念命中，weak=同场景/近义沾边，none=素材侧确实没有可用描述或与台词没有公共概念。"
            "选片本身已按这个分数排序（高者先上），这里只是把账记清。"
        ),
        "code_card_ratio": round(code_card_dur / max(0.1, total_dur), 3),
        "broll_ratio_prior": round(broll_prior_ratio, 3),
        "broll_ratio_band": [round(broll_band[0], 3), round(broll_band[1], 3)],
        "broll_target_hard": broll_target_hard,
        "broll_policy_note": broll_policy_note,
        "broll_capacity": {"aroll": round(aroll_capacity, 3), "broll": round(broll_capacity, 3)},
        "ppt_warning": code_ratio > 0.30,
        "audio_only_masked_count": sum(1 for c in clips if c.get("audio_only_masked")),
        "taboo_blocked_count": taboo_blocked_count,
        "deictic_gaps": deictic_gaps,
        "first_frame_density_ok": first_clip_ok,
        "first_3s_visual_changes": first_3s_changes,
        "clip_count": len(clips),
        "treatment_policy": treatment_plan,
        "clips": clips,
    }
    # 把「实际用的预算 vs 最终跑出来的指标」写进 EDL，下游与门禁都能直接读
    if gram is not None:
        gate = check_edl(edl, gram)
        tgt = gate["targets"]
        act = gate["metrics"]
        edl["edit_grammar"] = {
            "mode": "style",
            "source_ref": gram.get("source_ref"),
            "target": {
                "shots": tgt.get("shots"),
                "p10": tgt.get("p10"), "p50": tgt.get("p50"), "p90": tgt.get("p90"),
                "fast_ratio": tgt.get("fast_ratio"),
                "b_ratio": tgt.get("b_ratio"), "b_band": tgt.get("b_band"),
                "opening_cuts": tgt.get("opening_cuts"),
                "longest_cap_sec": tgt.get("longest_cap"),
                "budget": ({k: shot_budget[k] for k in
                            ("target_shots", "shot_min_sec", "shot_max_sec", "median_sec",
                             "fast_quota", "opening_cuts", "b_ratio_target", "b_band")}
                           if shot_budget else None),
            },
            "actual": {
                "shots": act["n_shots"],
                "p10": act["p10"], "p50": act["p50"], "p90": act["p90"],
                "fast_ratio": act["fast_ratio"],
                "b_ratio": act["b_ratio"], "b_ratio_with_cards": act["b_ratio_with_cards"],
                "opening_cuts": act["opening_cuts"],
                "longest_sec": act["longest_sec"],
            },
            "notes": list(grammar_notes),
            "shot_budget_capped": shot_budget_capped or None,
            "degraded": list(gram.get("degraded") or []),
            "gate_pass": gate["pass"],
            "gate_fails": gate["fail_reasons"],
        }
    else:
        edl["edit_grammar"] = {
            "mode": grammar_mode,
            "target": None,
            "actual": {"shots": len(clips)},
            "notes": ["dynamic 模式：不读刀法真值，保留旧行为（逃生门）"] + grammar_notes,
        }
    write_json(pipeline_dir / "04_edl.json", edl)

    md_lines = [
        "# 画面剪辑排期表（大白话版）",
        "",
        f"- **成片总时长**：{total_dur:.2f} 秒（共 {len(clips)} 个画面段落）",
        f"- **开头 3 秒画面切换**：{first_3s_changes} 次（全部踩在说话节奏点上）",
        f"- **声音提前衔接 / 画面微拉近**：声画自然错位 {jcut_real_count + lcut_real_count} 处，重点画面拉近 {punch_in_count} 处",
        "",
        "| 序号 | 画面类型 | 截取起点 | 成片时间段 | 时长 | 衔接处理 | 安排用意 | 对应台词 | 凭什么对得上 |",
        "| :--- | :--- | :---: | :--- | :---: | :---: | :--- | :--- | :--- |",
    ]
    role_cn = {"a_roll": "真人出镜", "b_roll": "实拍细节", "code_broll": "动态图表"}

    def sem_cn(c: Dict[str, Any]) -> str:
        if c.get("track_role") == "a_roll":
            return "—（讲这句话的本人）"
        rel = c.get("sem_match") or {}
        v = str(rel.get("verdict") or "none")
        if v == "none":
            return "无依据：素材侧没有可对的内容线索"
        return f"{'贴合' if v == 'hit' else '同场景'}：" + "；".join(rel.get("basis") or [])[:40]

    for i_c, c in enumerate(clips, start=1):
        jl = c["j_cut_lead_frames"]
        j_p = "声音先入半拍" if jl > 0 else ("画面先露半拍" if jl < 0 else ("轻推拉近" if c["punch_in_zoom"] > 1.01 else "常规硬切"))
        md_lines.append(
            f"| 第 {i_c} 镜 | {role_cn.get(c['track_role'], '实拍画面')} | {c['source_in_sec']:.2f}s | "
            f"{c['timeline_in_sec']:.2f}s–{c['timeline_out_sec']:.2f}s | {c['duration_sec']:.2f}s | "
            f"{j_p} | {c['purpose']} | {c['sentence_text']} | {sem_cn(c)} |"
        )
    (pipeline_dir / "04_edl_table.md").write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    print(
        f"[step4] 剪辑排期完成：{len(clips)} 个镜头，前3秒切换 {first_3s_changes} 次，"
        f"声画错位 {jcut_real_count + lcut_real_count} 处，重点拉近 {punch_in_count} 处，"
        f"动态讲解画中画 {explainer_applied_count} 处 -> 04_edl.json"
    )
    _sem = edl.get("broll_semantic") or {}
    if _sem.get("clips"):
        print(
            f"[step4] 外来画面与台词对位：{_sem['clips']} 个里贴合 {_sem['hit']}、同场景 {_sem['weak']}、"
            f"无依据 {_sem['none']}（命中率 {_sem['hit_ratio']*100:.0f}%；"
            f"无依据=素材侧确实没描述或与这句没公共概念，不在剪辑里瞎配）"
        )
    if not sem_gate["raw_pass"]:
        for _reason in sem_gate["fails"]:
            print(f"[step4] [FAIL] B-roll 语义对位：{_reason}")
        if sem_gate["waived"]:
            print("[step4] [WARN] 已用 --allow-broll-mismatch 显式放行：账本仍记为不达标")
        elif enforce_broll_gate:
            raise BrollSemanticGateError("；".join(sem_gate["fails"]))
    return edl


def main() -> None:
    maybe_reexec_in_venv()
    parser = argparse.ArgumentParser(description="Step 4: 以音定画生成专业剪辑表")
    parser.add_argument("--pipeline-dir", required=True)
    parser.add_argument("--track", choices=["store", "creator"], default="store")
    parser.add_argument("--j-cut-frames", type=int, default=3)
    parser.add_argument("--style-id", help="风格谱ID")
    parser.add_argument("--edit-profile", choices=EDIT_PROFILES, default="talking")
    parser.add_argument("--account-profile", help="account_profile.json 路径")
    parser.add_argument("--prefer-broll-only", action="store_true", help="打断#3只改画面时可选切换纯B-roll混剪")
    parser.add_argument("--grammar", choices=["style", "dynamic"], default="style",
                        help="style=按风格刀法真值排切点（默认）；dynamic=不读刀法真值，保留旧行为")
    parser.add_argument("--allow-broll-mismatch", action="store_true",
                        help="显式放行 B-roll 语义对位不达标（默认 fail-closed：命中率低或无依据镜头超阈值即退出）")
    args = parser.parse_args()
    prof = read_json(Path(args.account_profile)) if args.account_profile and Path(args.account_profile).exists() else None
    try:
        run_step4(
            Path(args.pipeline_dir),
            track=args.track,
            j_cut_frames=args.j_cut_frames,
            prefer_broll_only=args.prefer_broll_only,
            style_id=args.style_id,
            account_profile=prof,
            edit_profile=args.edit_profile,
            grammar=args.grammar,
            allow_broll_mismatch=args.allow_broll_mismatch,
            enforce_broll_gate=True,
        )
    except BrollSemanticGateError as exc:
        # fail-closed：剪辑表已经写好了（带 gate 账本），但流程以非 0 退出码停下，
        # 不许“静默出片”；真要放行请显式加 --allow-broll-mismatch
        print(f"[step4] B-roll 与台词对不上，已中止（未被放行）：{exc}")
        print("[step4] 处理方式：补齐素材描述/重抽帧判读，或加 --allow-broll-mismatch 显式放行")
        raise SystemExit(2)


if __name__ == "__main__":
    main()
