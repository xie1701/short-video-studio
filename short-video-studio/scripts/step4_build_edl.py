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
import re
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCRIPTS_ROOT = Path(__file__).resolve().parent
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from common import (
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    DEFAULT_FPS,
    EDIT_PROFILES,
    frames_to_sec,
    load_style_applied,
    load_style_spectrum_entry,
    maybe_reexec_in_venv,
    read_json,
    write_json,
)
import os

import broll_match
import pip_policy
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
# 对位门禁阈值：写在 broll_match 里（MAX_NONE_RATIO / MIN_HIT_RATIO），这里只做开关


class BrollSemanticGateError(RuntimeError):
    """B-roll 与台词对不上到越线（fail-closed）：默认只审计，显式开启后必须中断出片。"""


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


def _avoid_excluded_windows(
    src_in: float,
    seg_dur: float,
    avail: float,
    excluded_windows: List[Dict[str, Any]],
) -> float:
    """若目标切片 [src_in, src_in+seg_dur] 与任一人工标废时间窗重叠，自动前移跳过标废区间。"""
    if not excluded_windows:
        return src_in
    wins = sorted(
        [
            (float(w.get("start_sec", 0.0)), float(w.get("end_sec", 0.0)))
            for w in excluded_windows
            if float(w.get("end_sec", 0.0)) > float(w.get("start_sec", 0.0))
        ],
        key=lambda x: x[0],
    )
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
                {"label": str(s.get("keywords", [None])[0] or s["text"][:10]), "subtitle": s["text"][:18]}
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
    strong = kf_per_min >= 80.0
    if strong:
        chosen = cands
    else:
        budget = max(1, int(round(kf_per_min * total_dur / 60.0 / 2.0)))
        chosen = sorted(cands, key=lambda c: -float(c.get("duration_sec", 0.0)))[:budget]
    chosen_ids = {id(c) for c in chosen}
    peak = 1.14 if strong else 1.07
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
    """把逐镜的 `sem_match` 汇总成 EDL 的 `broll_semantic`（含逐条 score/tier/hits/why）。"""
    rows: List[Dict[str, Any]] = []
    for c in clips:
        if str(c.get("track_role")) not in ("b_roll", "code_broll"):
            continue
        rel = c.get("sem_match") or {}
        rows.append({
            "clip_id": c.get("clip_id"),
            "role": c.get("track_role"),
            "timeline_in_sec": c.get("timeline_in_sec"),
            "sentence": c.get("sentence_text"),
            "media": Path(str(c.get("media_path") or "")).name,
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
    # 镜头预算先算出来：挑切点时要用（目标刀数 / 单镜时长带 / 快切配额 / 开场刀数）
    shot_budget: Optional[Dict[str, Any]] = plan_shot_budget(total_dur, gram) if gram is not None else None
    grammar_notes: List[str] = []
    broll_prior_ratio = float((style_entry or {}).get("broll_overlay_ratio_target", 0.53))
    # 风格里的 B-roll 占比只是先验：实际配比按素材能力动态调整（用户明确要求，不可刻板执行模版比例）
    broll_target_hard = bool((style_entry or {}).get("broll_target_hard", False))
    band_raw = (style_entry or {}).get("broll_target_band_pct") or [
        max(0.0, (broll_prior_ratio - 0.15) * 100),
        min(100.0, (broll_prior_ratio + 0.15) * 100),
    ]
    broll_band = (max(0.0, float(band_raw[0]) / 100.0), min(1.0, float(band_raw[1]) / 100.0))
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
    broll_capacity = min(1.0, sum(float(m.get("duration_sec") or 0) for m in broll_pool) / max(total_dur, 0.1))
    broll_target_ratio = broll_prior_ratio
    if usable_arolls and aroll_capacity < (1.0 - broll_prior_ratio) - 0.02:
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
    zero_broll_mode = bool(ab_spec) and broll_target_ratio <= 0.01 and bool(usable_arolls) and not prefer_broll_only
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

    first_3s_evts = pick_first_3s_events(events, fps, int(pacing["opening_cuts"]))
    sent_by_id = {s["id"]: s for s in sentences}
    prop_sent_ids = {s["id"] for s in sentences if any(cue in s.get("text", "") for cue in PROP_SHOW_CUES)}

    later_candidates = [
        e for e in events
        if e["time_sec"] > 2.85
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
        plan = select_cuts(list(cand_by_time.values()), shot_budget, gram)
        chosen_cuts = [c["candidate"]["event"] for c in plan["cuts"] if c.get("candidate")]
        grammar_notes.extend(str(n) for n in (plan.get("notes") or []))
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
        if ev["time_sec"] <= 2.85:
            raw_frame = min(raw_frame, 3 * fps - 2)
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

    def find_sentence_for_time(t_sec: float, ev: Dict[str, Any] | None = None) -> Dict[str, Any]:
        if ev and ev.get("sentence_id") in sent_by_id and ev.get("type") in ("sentence_start", "pause_end", "keyword_accent", "stress"):
            return sent_by_id[ev["sentence_id"]]
        for s in sentences:
            if s["start_sec"] <= t_sec <= s["end_sec"]:
                return s
        for s in sentences:
            if s["start_sec"] <= t_sec <= s["end_sec"] + 0.40:
                return s
        return sentences[-1]

    n_segs = len(cut_points)
    audio_only_flags: List[bool] = []
    source_aroll_valid: List[bool] = []
    # 被镜头预算合并出来的连续长镜（同源首尾相接、跨两句）：A-roll 校验要放行，
    # 否则一句连续说完的长镜会被判「跨句不安全」而降级成 B-roll，反而更偏离风格
    budget_merged_flags: List[bool] = [bool(cp.get("budget_merged")) for cp in cut_points]
    # 跨句的连续长镜（同源首尾相接）：允许当 A-roll，但逐镜留痕，导出侧能看出这一刀盖了几句
    multi_sentence_aroll_flags: List[bool] = [False] * len(cut_points)
    for cp in cut_points:
        ev = cp["event"]
        t_start = frames_to_sec(cp["cut_frame"], fps)
        sent = find_sentence_for_time(t_start, ev)
        sm_p = str(sent.get("source_media", "") or "")
        sent_vgrade = str(sent.get("source_video_grade") or vgrade_by_mp4.get(sm_p, "usable"))
        forced_mask = sent_vgrade in ("audio_only", "ng")
        audio_only_flags.append(forced_mask)
        # 严格校验本句原声是否真正对应可用的实拍 A-roll，绝不把无源配音硬配给无关真人口型！
        source_aroll_valid.append(bool(sm_p and sm_p in usable_aroll_paths))

    def _can_be_safe_aroll(seg_idx: int) -> bool:
        if audio_only_flags[seg_idx] or not usable_arolls or broll_only_mode or not source_aroll_valid[seg_idx]:
            return False
        t_in = frames_to_sec(cut_points[seg_idx]["raw_frame"], fps)
        t_out = frames_to_sec(cut_points[seg_idx + 1]["raw_frame"] if seg_idx + 1 < n_segs else total_frames, fps)
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
        sent = find_sentence_for_time(t_start, ev)
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

    # 反向校正（下沿）：镜头数被风格预算收过之后，B 覆盖可能掉到风格动态带以下。
    # 素材够就按语义贴合度把 A 镜换成 B 镜（不硬套模版比例，换不动就如实记原因）。
    if usable_arolls and not broll_only_mode and not zero_broll_mode and broll_pool and n_segs > 1:
        seg_durs_up = [
            frames_to_sec((cut_points[i + 1]["raw_frame"] if i + 1 < n_segs else total_frames)
                          - cut_points[i]["raw_frame"], fps)
            for i in range(n_segs)
        ]
        cur_b_sec = sum(d for r, d in zip(roles, seg_durs_up) if r != "a_roll")
        floor_sec = max(0.0, total_dur * broll_band[0] - total_dur * 0.02)
        if cur_b_sec < floor_sec:
            up_cands = []
            for i_u in range(n_segs):
                if roles[i_u] != "a_roll":
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

    # 真·J-cut (正帧移：声先画后) 与 真·L-cut (负帧移：高语义物证画面先入)
    first_3s_cutoff_f = 3 * fps
    j_leads = [0] * n_segs
    if j_cut_frames > 0:
        for idx in range(1, n_segs):
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
        sel = broll_match.score_clip(
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
    ) -> Tuple[Dict[str, Any], str]:
        """这镜配哪条 B-roll：先按「这句台词讲了什么画面」排，再拿质量/时长/复用次数同分裁决。"""
        nonlocal taboo_blocked_count
        s_txt = str(sent.get("text", ""))
        s_kws = [str(k) for k in sent.get("keywords", [])]
        is_praise = any(cue in s_txt for cue in POSITIVE_PRAISE_CUES)
        candidates = [m for m in broll_pool if not (forbid_code and m.get("role") == "code_broll")] or broll_pool

        # Agent 已经逐句指定素材（04b_broll_choice.json）→ 直接采信，只在表里标明是谁选的
        forced = broll_match.agent_choice_for(agent_choices, t_start) if agent_choices else None
        if forced:
            mp_forced = str(forced.get("media_path") or "")
            for m in candidates:
                if broll_match.same_media(str(m.get("normalized_mp4") or m.get("media_path") or ""), mp_forced):
                    return m, "agent"

        best_m = candidates[0]
        best_score = -1e9
        blocked_here = False
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
                m_sel = broll_match.score_clip(
                    s_txt, m, used_count=usage_counts.get(m["id"], 0), need_sec=seg_dur, keywords=s_kws,
                )
                sem_weight = float(m_sel["rank"]) * BROLL_SEM_RANK_WEIGHT
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
            stab_bonus = float(m.get("stability_score", 0.8)) * 1.2
            sharp_bonus = min(1.5, float(m.get("sharpness_score", 40.0)) / 40.0)
            avail = float(m.get("duration_sec", 3.0))
            mp_str = str(m.get("normalized_mp4") or "")
            is_live_m = m.get("role") == "b_roll" and avail > 2.0
            h_m = 0.40 if (is_live_m and avail >= seg_dur + 0.80) else 0.0
            eff_cur = max(float(used_cursors.get(m["id"], 0.0)), float(max_out_by_mp.get(mp_str, 0.0)))
            same_media_pen = 0.0
            if mp_str == prev_media_path:
                if m.get("role") == "code_broll" or (avail - max(eff_cur, prev_src_out + 0.05) < seg_dur):
                    same_media_pen = 800.0
                else:
                    same_media_pen = 18.0
            rem = (avail - h_m) - eff_cur
            exhausted_pen = 500.0 if (m.get("role") != "code_broll" and (avail - eff_cur) < seg_dur) else 0.0
            if m.get("role") != "code_broll" and (avail - eff_cur) < min(0.5, seg_dur * 0.5):
                exhausted_pen += 1500.0
            dur_bonus = 2.0 if rem >= seg_dur else (0.5 if avail >= seg_dur else -2.0)
            live_bonus = 3.5 if (t_start < 3.0 and m.get("role") == "b_roll") else 0.0
            sc = (
                sem_weight + intel_bonus + size_bonus + cont_bonus + stab_bonus + sharp_bonus
                + dur_bonus + live_bonus - taboo_pen - same_media_pen - exhausted_pen - motion_pen
            )
            if sc > best_score:
                best_score = sc
                best_m = m
        if blocked_here and not best_m.get("taboo_tags"):
            taboo_blocked_count += 1
        return best_m, "rules"

    clips: List[Dict[str, Any]] = []
    last_group = "shoot_default"
    last_motion = "static"
    last_shot_size = "medium"
    last_aroll_zoom = 1.0
    punch_in_count = 0
    lcut_real_count = 0

    for idx, cp in enumerate(cut_points):
        f_start = cp["cut_frame"]
        f_end = cut_points[idx + 1]["cut_frame"] if idx + 1 < n_segs else total_frames
        t_start = frames_to_sec(f_start, fps)
        t_end = frames_to_sec(f_end, fps)
        seg_dur = round(t_end - t_start, 4)
        ev = cp["event"]
        sent = find_sentence_for_time(t_start, ev)
        has_prop_show = any(cue in sent.get("text", "") for cue in PROP_SHOW_CUES)

        if roles[idx] == "a_roll":
            ar = next((a for a in usable_arolls if a["normalized_mp4"] == sent.get("source_media")), usable_arolls[0])
            offset_in_sent = max(0.0, t_start - float(sent["start_sec"]))
            if sent.get("source_media") == ar["normalized_mp4"]:
                src_in = round(float(sent.get("source_in_sec", 0.0)) + offset_in_sent, 4)
                # 跨句连续长镜：源窗口要一直开到「最后一句的 source_out」，
                # 否则只取第一句的窗口会发生尾部冻帧（多句同源首尾相接时视频是连续表演）
                cover_sents = [
                    s for s in sentences
                    if min(t_end, float(s["end_sec"])) - max(t_start, float(s["start_sec"])) > 0.04
                    and str(s.get("source_media") or "") == str(sent.get("source_media") or "")
                ]
                last_cov = cover_sents[-1] if cover_sents else sent
                sent_src_end = float(last_cov.get("source_out_sec", ar["duration_sec"]))
                max_src_out = min(ar["duration_sec"], sent_src_end)
            else:
                src_in = round(min(max(0.0, ar["duration_sec"] - seg_dur), float(sent.get("source_in_sec", 0.0)) + offset_in_sent), 4)
                max_src_out = ar["duration_sec"]

            # 相邻同源单调推进校验（消除任何误差引起的轻微倒带）
            ar_mp = str(ar["normalized_mp4"])
            if ar_mp in max_out_by_mp:
                src_in = max(src_in, max_out_by_mp[ar_mp])
            src_in = min(src_in, max(0.0, max_src_out - 0.05))
            # 源窗口差在 1 帧以内是时间量化误差，不是真冻帧（渲不出来的一帧不算冻）
            need_freeze = src_in + seg_dur > max_src_out + 1.0 / max(1, fps)
            src_out = round(min(max_src_out, src_in + seg_dur), 4)
            max_out_by_mp[ar_mp] = max(float(max_out_by_mp.get(ar_mp, 0.0)), src_out)

            vf_extra = ""
            cur_zoom = 1.0
            if idx > 0 and roles[idx - 1] == "a_roll" and not has_prop_show and allow_punch:
                cur_zoom = 1.15 if last_aroll_zoom == 1.0 else 1.0
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
                "clip_id": f"clip_{idx+1:03d}",
                "track_role": "a_roll",
                "media_path": ar["normalized_mp4"],
                "source_in_sec": src_in,
                "source_out_sec": src_out,
                "tight": True,
                "freeze_tail": need_freeze,
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
        else:
            has_fresh_live = any(
                m.get("role") == "b_roll"
                and (
                    float(m.get("duration_sec", 0.0))
                    - max(
                        float(used_cursors.get(m["id"], 0.0)),
                        float(max_out_by_mp.get(str(m.get("normalized_mp4") or ""), 0.0)),
                    )
                ) >= min(0.6, seg_dur * 0.5)
                for m in broll_pool
            )
            cur_code_dur = sum(float(c["duration_sec"]) for c in clips if c.get("track_role") == "code_broll")
            forbid_code = has_fresh_live and ((cur_code_dur + seg_dur) / max(0.1, total_dur) > 0.45)
            prev_mp = str(clips[-1]["media_path"]) if clips else ""
            prev_so = float(clips[-1]["source_out_sec"]) if clips else 0.0

            br, br_source = pick_best_broll(
                sent, seg_dur, last_group, t_start, prev_mp, prev_so,
                prev_motion=last_motion, prev_shot_size=last_shot_size, forbid_code=forbid_code,
            )
            bid = br["id"]
            mp_str = str(br["normalized_mp4"])
            avail = float(br.get("duration_sec", 3.0))
            is_live_long = br.get("role") == "b_roll" and avail > 2.0
            handle_s = 0.40 if (is_live_long and avail >= seg_dur + 0.80) else 0.0
            max_usable_out = avail - handle_s

            if br["role"] == "code_broll":
                src_in = 0.0
                used_cursors[bid] = src_in + seg_dur + 0.10
                src_out = round(min(avail, src_in + seg_dur), 4)
            else:
                min_floor = max(handle_s, float(max_out_by_mp.get(mp_str, 0.0)))
                cur_pos = max(min_floor, float(used_cursors.get(bid, min_floor)))
                if cur_pos + seg_dur <= max_usable_out or (avail - cur_pos >= seg_dur):
                    src_in = round(cur_pos, 4)
                elif avail - min_floor >= 0.30:
                    src_in = round(min_floor, 4)
                else:
                    src_in = round(min(min_floor, max(0.0, avail - 0.05)), 4)
                if br.get("excluded_windows"):
                    src_in = _avoid_excluded_windows(src_in, seg_dur, avail, br["excluded_windows"])
                src_out = round(min(avail, src_in + seg_dur), 4)
                max_out_by_mp[mp_str] = max(float(max_out_by_mp.get(mp_str, 0.0)), src_out)
                used_cursors[bid] = round(src_out + 0.10, 4)

            usage_counts[bid] = usage_counts.get(bid, 0) + 1
            last_group = str(br.get("scene_group", last_group))
            last_motion = str(br.get("camera_motion") or "static")
            last_shot_size = str(br.get("shot_size") or "medium")
            need_freeze = src_in + seg_dur > avail + 0.01
            eff_role = str(br["role"])  # 统一保留 b_roll/code_broll，不写入悬空 "freeze"
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
                and any(w in str(sent.get("text", "")) for w in ambient_words)
            )
            # 语义对位可审计：这个画面凭什么被认为「对得上这句台词」（概念命中就写命中的字面，零依据就是 none）
            sem_here = sem_of_choice(br, sent, need_sec=seg_dur)
            sem_here["source"] = br_source
            clips.append({
                "clip_id": f"clip_{idx+1:03d}",
                "track_role": eff_role,
                "media_path": br["normalized_mp4"],
                "source_in_sec": src_in,
                "source_out_sec": src_out,
                "tight": True,
                "handles_guarded": bool(br["role"] == "code_broll" or (src_in >= 0.39 and (avail - src_out) >= 0.39)),
                "audio_only_masked": audio_only_flags[idx],
                "freeze_tail": need_freeze,
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
                "sentence_id": sent["id"],
                "sentence_text": sent["text"],
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

    # 关键帧推拉频次：严格按风格实测值（0=机位完全不动；>=80=几乎每镜都推/拉）
    kf_applied = _apply_keyframe_policy(clips, kf_target, total_dur) if kf_target is not None else None

    # A/B-roll 切换样式：按风格范式决定「哪些镜头挂画框/抠像、哪些保持纯净全屏」
    known_patterns = {
        "fullscreen_intercut", "zero_broll_keyframe_flow", "container_internal_switch",
        "cutout_broll_alternating", "depth_hook_then_framed_broll", "dual_window_broll_switcher",
    }
    pattern_handled = ab_pattern in known_patterns
    if pattern_handled and pip_mode and clips:
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
            if role == "code_broll" or c_obj.get("pip_layout"):
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
                    sec_m, sec_i = _alt_broll(main_media)
                    cfg = _dual_cfg(c_obj, sec_m, sec_i) if sec_m else {**pip_spec, "mode": pip_mode, "single_source": True}
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

    if pip_mode and clips and not pattern_handled:
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
            if c_obj.get("track_role") == "code_broll" or c_obj.get("pip_layout"):
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

    # 会裁切的构图（小窗/缩窗/推近/相框/圆窗）先在剪辑表里定好人脸锚点：
    # 剪辑表自己就能交代"这个窗口是按脸取的还是素材里根本没脸"，渲染与导出共用同一份依据
    CUT_MODES = {
        "geometry_pip_window", "geometry_pip_main_shrunk", "geometry_pip_main_punch_in",
        "blur_bg_center_card_pip", "smooth_orbit_pip", "black_polaroid_frame_pip",
        "person_behind_giant_text_pip", "vision_portrait_cutout_pip", "data_news_cutout_pip",
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

    # 抠像内容门槛：风格会抠像 ≠ 每一镜都该抠（2026-11-24 用户实测「讲解效果图的地方，把讲解图给扣没了」）
    try:
        cutout_gate = pip_policy.apply_cutout_gate(clips, style_entry, pipeline_dir)
        print(pip_policy.summarize_for_log(cutout_gate))
        for _r in cutout_gate.get("rows") or []:
            if not _r.get("allowed"):
                print(f"[step4]   抠像降级 @{_r['timeline_in_sec']:6.2f}s {_r['media']}：{_r['reason']}")
    except Exception as _pe:
        cutout_gate = {"error": str(_pe)}
        print(f"[step4] [WARN] 抠像门槛未执行（{_pe}），按风格原指令执行")

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
    sem_summary["gate"] = sem_gate

    edl = {
        "step": "04_build_edl",
        "fps": fps,
        "tight": True,
        "style_id": style_id,
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
        _gate = _sem.get("gate") or {}
        if not _gate.get("raw_pass", True):
            for _reason in (_gate.get("fails") or []):
                print(f"[step4] [FAIL] B-roll 语义对位：{_reason}")
            if _gate.get("waived"):
                print("[step4] [WARN] 已用 --allow-broll-mismatch 显式放行：账本仍记为不达标")
            elif enforce_broll_gate:
                raise BrollSemanticGateError("；".join(_gate.get("fails") or []))
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
