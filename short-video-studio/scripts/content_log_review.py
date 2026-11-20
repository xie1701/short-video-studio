#!/usr/bin/env python3
"""
[阶段 6：微观留存曲线诊断与账号参数真闭环 (content-log)]
1. 在本地 content-log/records.jsonl 中记录发布后后台微观数据：
   播放、3秒留存、完播、2秒跳出率 (bounce_2s)、5秒留存 (retention_5s)、复播率 (rewatch_rate)、
   同城占比 (local_traffic_pct)、有效询单率 (inquiry_rate)、留存断崖秒数 (drop_off_sec)、
   关联剪辑表 (edl_path)、style_id 与停顿基线 (pause_sec)。
2. 留存断崖精准反查 EDL：把平台后台掉粉秒数定位回对应 clip_id、轨道角色与台词句，生成微观掉点病历。
3. 严守实事求是统计原则（样本不足绝不瞎写“显著高于”），按真实胜出组统计推荐参数并输出
   content-log/account_profile.json（账号参数基因库），供下一条视频自动加载调优！
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from common import resolve_workspace_paths

DEMO_RECORDS = [
    {
        "video_id": "v_001", "date": "2026-11-08", "track": "store", "title": "价格越高的菜利润越低",
        "a_level": "A1", "script_type": "聊行业看法", "viral_element": "算钱对比",
        "hook_type": "颠覆切入", "opening_skill": "对立面开篇", "avg_shot_sec": 1.4,
        "style_id": "style_14", "pause_sec": 0.16, "persona_pause_sec": 0.28, "bounce_2s": 0.28, "retention_5s": 0.52,
        "rewatch_rate": 0.14, "local_traffic_pct": 0.68, "inquiry_rate": 0.0013,
        "views": 18500, "retention_3s": 0.64, "completion_rate": 0.34, "likes": 420, "comments": 68, "dm_inquiries": 24,
    },
    {
        "video_id": "v_002", "date": "2026-11-09", "track": "store", "title": "八年老店老板的底线",
        "a_level": "A2", "script_type": "给进店理由", "viral_element": "揭秘内幕",
        "hook_type": "痛点切入", "opening_skill": "不字开头", "avg_shot_sec": 1.6,
        "style_id": "style_14", "pause_sec": 0.18, "persona_pause_sec": 0.32, "bounce_2s": 0.31, "retention_5s": 0.49,
        "rewatch_rate": 0.12, "local_traffic_pct": 0.74, "inquiry_rate": 0.0034,
        "views": 9200, "retention_3s": 0.58, "completion_rate": 0.31, "likes": 210, "comments": 45, "dm_inquiries": 31,
    },
    {
        "video_id": "v_003", "date": "2026-11-11", "track": "store", "title": "门店日常工作vlog",
        "a_level": "A3", "script_type": "晒干活过程", "viral_element": "前后反差",
        "hook_type": "疑问切入", "opening_skill": "信息密度开篇", "avg_shot_sec": 3.4,
        "style_id": "style_06", "pause_sec": 0.26, "persona_pause_sec": 0.35, "bounce_2s": 0.54, "retention_5s": 0.26, "drop_off_sec": 4.2,
        "rewatch_rate": 0.05, "local_traffic_pct": 0.41, "inquiry_rate": 0.0010,
        "views": 3100, "retention_3s": 0.39, "completion_rate": 0.16, "likes": 52, "comments": 8, "dm_inquiries": 3,
    },
    {
        "video_id": "v_004", "date": "2026-11-13", "track": "store", "title": "高于20块才敢用的鲜羊排真相",
        "a_level": "A1", "script_type": "教实用知识", "viral_element": "算钱对比",
        "hook_type": "好奇切入", "opening_skill": "数字开篇", "avg_shot_sec": 1.3,
        "style_id": "style_02", "pause_sec": 0.15, "persona_pause_sec": 0.26, "bounce_2s": 0.24, "retention_5s": 0.56,
        "rewatch_rate": 0.17, "local_traffic_pct": 0.65, "inquiry_rate": 0.0012,
        "views": 24600, "retention_3s": 0.69, "completion_rate": 0.38, "likes": 610, "comments": 95, "dm_inquiries": 29,
    },
]

_STAGE_PLAIN = {
    "A1": "拉新视频",
    "A2": "建信任视频",
    "A3": "打消顾虑视频",
    "A4": "照顾老客视频",
    "A5": "宠粉互动视频",
}

_HOOK_PLAIN = {
    "颠覆切入": "反常识开头",
    "痛点切入": "戳烦心事开头",
    "疑问切入": "提问开头",
    "好奇切入": "留悬念开头",
    "人群切入": "点名一类人开头",
    "效果切入": "先亮结果开头",
    "情绪切入": "情绪共鸣开头",
    "身份推荐切入": "资深身份/老客见证开头",
    "对立面开篇": "反着说",
    "不字开头": "用“别/不要”开头",
    "信息密度开篇": "一上来塞满现场细节",
    "数字开篇": "最狠数字放开头",
    "制造敌人": "先回应质疑",
    "做嘴替": "替观众说心里话",
    "信息缺口": "先讲吃亏换来的真相",
    "对赌开篇": "用“你信不信”开场",
    "稀缺性开篇": "强调全城少见",
    "冲突开篇": "身份和动作反差开场",
}


def _plain_stage(val: Any) -> str:
    s = str(val or "").strip()
    return _STAGE_PLAIN.get(s.upper(), s or "未分类")


def _plain_hook_label(r: Dict[str, Any]) -> str:
    ht = str(r.get("hook_type") or "").strip()
    sk = str(r.get("opening_skill") or "").strip()
    p_ht = _HOOK_PLAIN.get(ht, ht)
    p_sk = _HOOK_PLAIN.get(sk, sk)
    if p_ht and p_sk:
        return f"{p_ht}（{p_sk}）"
    return p_ht or p_sk or "未标注开头"


def _plain_script_combo(r: Dict[str, Any]) -> Optional[str]:
    st = str(r.get("script_type") or "").strip()
    ve = str(r.get("viral_element") or "").strip()
    if st and ve:
        return f"{st} × {ve}"
    return st or ve or None


def load_records(jsonl_path: Path) -> List[Dict[str, Any]]:
    if not jsonl_path.exists():
        return []
    rows = []
    for line in jsonl_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def append_record(log_dir: Path, record: Dict[str, Any]) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = log_dir / "records.jsonl"
    if "date" not in record:
        record["date"] = datetime.now().strftime("%Y-%m-%d")
    with jsonl_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return jsonl_path


def diagnose_edl_drop_off(record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """根据后台留存曲线最大断崖秒数 drop_off_sec 反查当时 04_edl.json 对应的镜头与台词。"""
    drop_t = record.get("drop_off_sec")
    edl_p = record.get("edl_path")
    if drop_t is None or not edl_p:
        return None
    p = Path(str(edl_p))
    if not p.is_file():
        return None
    try:
        edl = json.loads(p.read_text(encoding="utf-8"))
        t = float(drop_t)
        clips = edl.get("clips", [])
        hit = next((c for c in clips if float(c["timeline_in_sec"]) <= t <= float(c["timeline_out_sec"])), None)
        if not hit and clips:
            hit = min(clips, key=lambda c: abs(float(c["timeline_in_sec"]) - t))
        if not hit:
            return None
        return {
            "title": record.get("title", "未命名"),
            "drop_off_sec": round(t, 2),
            "clip_id": hit.get("clip_id"),
            "track_role": hit.get("track_role"),
            "clip_span": f"{hit.get('timeline_in_sec', 0):.2f}s–{hit.get('timeline_out_sec', 0):.2f}s",
            "duration_sec": hit.get("duration_sec"),
            "sentence_text": hit.get("sentence_text", ""),
            "purpose": hit.get("purpose", ""),
        }
    except Exception:
        return None


def build_account_profile(
    log_dir: Path,
    records: List[Dict[str, Any]],
    winners: List[Dict[str, Any]],
    track: str,
) -> Path:
    """将验证跑赢基线的节奏/停顿/风格谱/钩子参数沉淀为分轨隔离的账号基因库 account_profile.json。"""
    pool = winners or records
    shot_secs = [float(r["avg_shot_sec"]) for r in pool if r.get("avg_shot_sec")]
    pause_secs = [float(r["pause_sec"]) for r in pool if r.get("pause_sec")]
    persona_pauses = [float(r["persona_pause_sec"]) for r in pool if r.get("persona_pause_sec")]
    styles = [str(r["style_id"]) for r in pool if r.get("style_id")]

    target_shot = round(max(1.0, min(3.2, statistics.median(shot_secs))), 2) if shot_secs else 1.55
    base_pause = round(max(0.14, min(0.30, statistics.median(pause_secs))), 3) if pause_secs else 0.18
    persona_pause = round(max(0.18, min(0.42, statistics.median(persona_pauses))), 3) if persona_pauses else round(min(0.40, base_pause * 1.5), 3)
    pref_style = Counter(styles).most_common(1)[0][0] if styles else ("style_14" if track == "store" else "style_02")

    bounces = [float(r["bounce_2s"]) for r in records if r.get("bounce_2s") is not None]
    high_bounce = bool(bounces and statistics.mean(bounces) > 0.40)

    winning_hooks = sorted({_plain_hook_label(r) for r in winners if r.get("hook_type") or r.get("opening_skill")})
    winning_script_types = sorted({str(r["script_type"]).strip() for r in winners if r.get("script_type")})
    winning_viral_elements = sorted({str(r["viral_element"]).strip() for r in winners if r.get("viral_element")})
    winning_script_combos = sorted({c for c in (_plain_script_combo(r) for r in winners) if c})
    sample_sufficient = len(records) >= 3
    out_p = log_dir / "account_profile.json"

    proposed = {
        "pause_sec": base_pause,
        "narrative_pause_sec": round(max(0.10, base_pause * 0.72), 3),
        "twist_pause_sec": round(min(0.38, max(0.26, base_pause * 1.55)), 3),
        "persona_pause_sec": persona_pause,
        "target_median_shot_sec": round(min(target_shot, 1.40) if high_bounce else target_shot, 2),
        "target_aroll_ratio": 0.35 if track == "store" else 0.30,
        "micro_hook_max_sec": 5.5 if high_bounce else 6.2,
        "preferred_style_id": pref_style,
        "winning_hooks": winning_hooks,
        "winning_script_types": winning_script_types,
        "winning_viral_elements": winning_viral_elements,
        "winning_script_combos": winning_script_combos,
    }

    if not sample_sufficient:
        prev_prof: Dict[str, Any] = {}
        if out_p.is_file():
            try:
                prev_prof = json.loads(out_p.read_text(encoding="utf-8"))
            except Exception:
                prev_prof = {}
        active_defaults = {
            "pause_sec": prev_prof.get("pause_sec", 0.18),
            "narrative_pause_sec": prev_prof.get("narrative_pause_sec", 0.13),
            "twist_pause_sec": prev_prof.get("twist_pause_sec", 0.28),
            "persona_pause_sec": prev_prof.get("persona_pause_sec", 0.28),
            "target_median_shot_sec": prev_prof.get("target_median_shot_sec", 1.55),
            "target_aroll_ratio": prev_prof.get("target_aroll_ratio", 0.35 if track == "store" else 0.30),
            "micro_hook_max_sec": prev_prof.get("micro_hook_max_sec", 6.2),
            "preferred_style_id": prev_prof.get("preferred_style_id", "style_14" if track == "store" else "style_02"),
            "winning_hooks": prev_prof.get("winning_hooks", []),
            "winning_script_types": prev_prof.get("winning_script_types", []),
            "winning_viral_elements": prev_prof.get("winning_viral_elements", []),
            "winning_script_combos": prev_prof.get("winning_script_combos", []),
        }
    else:
        active_defaults = proposed

    profile = {
        "track": track,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "sample_count": len(records),
        "sample_sufficient": sample_sufficient,
        "winner_count": len(winners),
        "defaults_frozen": not sample_sufficient,
        "proposed_defaults": proposed,
        **active_defaults,
    }
    out_p.parent.mkdir(parents=True, exist_ok=True)
    out_p.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    # 同步保存轨道隔离档案，防止 store / creator 双轨混用
    track_p = log_dir / f"account_profile_{track}.json"
    track_p.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_p


def _extract_inquiry_rate(r: Dict[str, Any]) -> Optional[float]:
    if r.get("inquiry_rate") is not None:
        return float(r["inquiry_rate"])
    views = float(r.get("views") or 0.0)
    if views > 0 and r.get("dm_inquiries") is not None:
        return float(r["dm_inquiries"]) / views
    return None


def generate_weekly_review(log_dir: Path, out_md: Path, track: Optional[str] = None) -> Dict[str, Any]:
    all_records = load_records(log_dir / "records.jsonl")
    if not all_records:
        raise ValueError("content-log 中暂无记录，无法生成周复盘。")

    if track is None:
        track_counts = Counter(str(r.get("track", "store")) for r in all_records if r.get("track"))
        track = track_counts.most_common(1)[0][0] if track_counts else "store"

    # 分轨隔离：优先只聚合当前轨道样本，防止门店轨与博主轨数据相互污染
    records = [r for r in all_records if str(r.get("track", track)) == track] or all_records

    base_views = statistics.mean(r.get("views", 0) for r in records)
    base_3s = statistics.mean(r.get("retention_3s", 0.0) for r in records)
    base_comp = statistics.mean(r.get("completion_rate", 0.0) for r in records)
    base_conv = statistics.mean((r.get("comments", 0) + r.get("dm_inquiries", 0)) for r in records)

    if track == "store":
        winners = [
            r for r in records
            if (r.get("comments", 0) + r.get("dm_inquiries", 0)) >= base_conv
            or (r.get("local_traffic_pct") is not None and float(r["local_traffic_pct"]) >= 0.60 and r.get("retention_3s", 0.0) >= base_3s)
            or r.get("retention_3s", 0.0) >= base_3s + 0.05
        ]
    else:
        winners = [
            r for r in records
            if r.get("retention_3s", 0.0) >= base_3s
            and (r.get("completion_rate", 0.0) >= base_comp or (r.get("rewatch_rate") is not None and float(r["rewatch_rate"]) >= 0.12))
        ]
    laggards = [r for r in records if r not in winners]

    fast_views = [r.get("views", 0) for r in records if float(r.get("avg_shot_sec", 2.0)) <= 2.0]
    slow_views = [r.get("views", 0) for r in records if float(r.get("avg_shot_sec", 2.0)) > 2.0]

    if len(records) < 4 or not fast_views or not slow_views:
        pace_conclusion = (
            f"样本不足（快节奏 `≤2.0s` 样本 {len(fast_views)} 条，慢节奏 `>2.0s` 样本 {len(slow_views)} 条），暂不做节奏显著性归因。"
        )
    else:
        f_mean = statistics.mean(fast_views)
        s_mean = statistics.mean(slow_views)
        if f_mean > s_mean * 1.15:
            pace_conclusion = f"平均镜头时长 `≤ 2.0s` 快节奏成片均值播放 **{f_mean:.0f}**，高于 `> 2.0s` 慢节奏的 **{s_mean:.0f}**。"
        elif s_mean > f_mean * 1.15:
            pace_conclusion = f"本周期 `> 2.0s` 舒缓节奏均值播放 **{s_mean:.0f}** 高于快节奏的 **{f_mean:.0f}**，建议适当放宽停顿与镜头停留。"
        else:
            pace_conclusion = f"快节奏均值播放 **{f_mean:.0f}** 与慢节奏 **{s_mean:.0f}** 基本持平，节奏差异尚未形成决定性分化。"

    winning_hooks = sorted({_plain_hook_label(r) for r in winners if r.get("hook_type") or r.get("opening_skill")})
    losing_hooks = sorted({_plain_hook_label(r) for r in laggards if r.get("hook_type") or r.get("opening_skill")})
    winning_combos = sorted({c for c in (_plain_script_combo(r) for r in winners) if c})
    losing_combos = sorted({c for c in (_plain_script_combo(r) for r in laggards) if c})

    drop_diagnoses = [d for d in (diagnose_edl_drop_off(r) for r in records) if d is not None]
    profile_path = build_account_profile(log_dir, records, winners, track)
    profile = json.loads(profile_path.read_text(encoding="utf-8"))

    # 观众行为细项汇总（刚刷到2秒就划走 / 5秒留住多少人 / 看多遍的比例 / 本地观众占比 / 来问的比例）
    bounces = [float(r["bounce_2s"]) for r in records if r.get("bounce_2s") is not None]
    ret5s = [float(r["retention_5s"]) for r in records if r.get("retention_5s") is not None]
    rewatches = [float(r["rewatch_rate"]) for r in records if r.get("rewatch_rate") is not None]
    local_pcts = [float(r["local_traffic_pct"]) for r in records if r.get("local_traffic_pct") is not None]
    inq_rates = [v for v in (_extract_inquiry_rate(r) for r in records) if v is not None]
    funnel_parts: List[str] = []
    if bounces:
        funnel_parts.append(f"刚看2秒就划走 **{statistics.mean(bounces)*100:.1f}%**")
    if ret5s:
        funnel_parts.append(f"前5秒留住 **{statistics.mean(ret5s)*100:.1f}%**")
    if rewatches:
        funnel_parts.append(f"看了不止一遍 **{statistics.mean(rewatches)*100:.1f}%**")
    if local_pcts:
        funnel_parts.append(f"本地观众占比 **{statistics.mean(local_pcts)*100:.1f}%**")
    if inq_rates:
        funnel_parts.append(f"每千人播放来咨询 **{statistics.mean(inq_rates)*1000:.2f} 人**")
    micro_line = f"- **观众行为细项平均**：{' | '.join(funnel_parts)}" if funnel_parts else ""

    honest_note = (
        f"- **本次统计条数**：{len(records)} 条（当前方向：{'实体店同城获客' if track=='store' else '个人账号涨粉引流'}）"
        if len(records) >= 3
        else f"- **本次统计条数**：{len(records)} 条（⚠️ **还不到 3 条，样本太少暂不贸然改策略和节奏**，先继续记录）"
    )
    md = [
        "# 每周发片数据复盘与下周调整建议（大白话版）",
        "",
        honest_note,
        f"- **账号平均水平**：平均播放 **{base_views:.0f}** | 开头3秒留住 **{base_3s*100:.1f}%** | 看完整条 **{base_comp*100:.1f}%** | 平均每条评论+私信 **{base_conv:.1f} 条**",
    ]
    if micro_line:
        md.append(micro_line)
    md += [
        f"- **自动记住最佳节奏**：已更新到 `{profile_path.name}`（下条剪辑自动沿用跑得最好的节奏）",
        "",
        "## 一、 跑得比平均线好的片子（值得继续多拍）",
        "",
        f"- **最管用的拍法 × 吸引角度**：{('、'.join(winning_combos)) if winning_combos else '暂未填写拍法标签，后续补齐即可自动统计'}",
        f"- **最抓人的开头写法**：{('、'.join(winning_hooks)) if winning_hooks else '样本还少，继续多试几种对比'}",
    ]
    for w in winners:
        combo_s = _plain_script_combo(w)
        tag_parts = [_plain_stage(w.get("a_level"))]
        if combo_s:
            tag_parts.append(combo_s)
        tag_parts.append(_plain_hook_label(w))
        md.append(
            f"  - 《{w.get('title','')}》[{' · '.join(tag_parts)}]: 播放 {w.get('views',0)} / 3秒留住 {w.get('retention_3s',0)*100:.0f}% / "
            f"看完 {w.get('completion_rate',0)*100:.0f}% / 评论+私信 {w.get('comments',0)+w.get('dm_inquiries',0)}"
        )
    md += [
        f"- **画面切快还是切慢更好**：{pace_conclusion}",
        "",
        "## 二、 跑得偏弱的片子与掉人位置排查",
        f"- **效果偏弱的拍法 × 吸引角度**：{('、'.join(losing_combos)) if losing_combos else '本周没有明显拉垮的拍法'}",
        f"- **效果偏弱的开头写法**：{('、'.join(losing_hooks)) if losing_hooks else '本周没有明显拉垮的开头'}",
    ]
    for l in laggards:
        extra_drop = f"（第 {l['drop_off_sec']} 秒掉人最狠）" if l.get("drop_off_sec") is not None else ""
        combo_s = _plain_script_combo(l)
        tag_parts = [_plain_stage(l.get("a_level"))]
        if combo_s:
            tag_parts.append(combo_s)
        tag_parts.append(_plain_hook_label(l))
        md.append(
            f"  - 《{l.get('title','')}》[{' · '.join(tag_parts)}]: 3秒留住 {l.get('retention_3s',0)*100:.0f}%（平均 {base_3s*100:.0f}%），平均每个画面停 {l.get('avg_shot_sec', 0)} 秒 {extra_drop}"
        )
    if drop_diagnoses:
        md += [
            "",
            "### 观众划走那一秒到底在看什么",
            "",
            "| 视频标题 | 掉人秒数 | 对应画面 | 画面类型 | 时间段（时长） | 当时说的话 | 怎么改 |",
            "| :--- | :---: | :--- | :--- | :---: | :--- | :--- |",
        ]
        for dd in drop_diagnoses:
            role_plain = "真人出镜说话" if dd["track_role"] == "a_roll" else "特写/环境补拍"
            rx = "真人出镜镜头停太久画面没变化，建议中间切入细节特写或微微拉近镜头" if dd["track_role"] == "a_roll" else "补拍画面和当时说的话贴得不够紧，建议换更有冲击力的实物特写"
            md.append(
                f"| 《{dd['title']}》 | {dd['drop_off_sec']}s | `{dd['clip_id']}` | {role_plain} | "
                f"{dd['clip_span']} ({dd['duration_sec']}s) | {dd['sentence_text'][:18]} | {rx} |"
            )

    md += [
        "",
        "## 三、 下周自动调整的剪辑节奏与选题侧重",
        "",
        "| 调整项 | 下周怎么用 | 为什么要这么调 |",
        "| :--- | :--- | :--- |",
        f"| **画面切换节奏** | 平均每 `{profile['target_median_shot_sec']}` 秒切一个画面（最长不超 `{profile['micro_hook_max_sec']}` 秒没变化） | 按本周跑得最好的片子和开头 2 秒划走情况算出来的 |",
        f"| **说话停顿节奏** | 日常停 `{profile['pause_sec']}s`（正常讲 `{profile['narrative_pause_sec']}s` / 反转前 `{profile['twist_pause_sec']}s` / 亮老板底线前 `{profile.get('persona_pause_sec', 0.28)}s`） | 保留自然呼吸感，重点句子前留足气口 |",
        f"| **优先画面包装风格** | `{profile['preferred_style_id']}` | 本周表现最好的片子里出现最多的风格 |",
        f"| **下周多拍的组合** | `{' / '.join(winning_combos) or ' / '.join(winning_hooks) or '继续多试几种对比'}` | 这几类开头留人或评论私信明显高于平均线 |",
        "",
    ]

    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(md), encoding="utf-8")
    summary = {
        "track": track,
        "record_count": len(records),
        "baseline": {
            "views": base_views,
            "retention_3s": base_3s,
            "completion_rate": base_comp,
            "conv": base_conv,
            "rewatch_rate": round(statistics.mean(rewatches), 4) if rewatches else None,
            "local_traffic_pct": round(statistics.mean(local_pcts), 4) if local_pcts else None,
            "inquiry_rate": round(statistics.mean(inq_rates), 5) if inq_rates else None,
        },
        "winning_hooks": winning_hooks,
        "winning_script_combos": winning_combos,
        "drop_diagnoses": drop_diagnoses,
        "account_profile_json": str(profile_path.resolve()),
        "weekly_review_md": str(out_md.resolve()),
    }
    print(f"[content_log] 已生成微观诊断复盘 -> {out_md} & 账号参数基因 -> {profile_path}")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="content-log 微观留存诊断与账号参数基因库复盘")
    parser.add_argument("--log-dir", help="content-log 存储目录（未传时可配 --account 自动隔离）")
    parser.add_argument("--account", help="账号标识，自动定位该账号专属 content-log/")
    parser.add_argument("--track", choices=["store", "creator"], help="按赛道轨道隔离计算基线与账号参数档案")
    parser.add_argument("--workspace-root", help="可选：自定义专属工作区根目录")
    parser.add_argument("--add-json", help="追加一条发布后台数据 JSON 字符串或文件")
    parser.add_argument("--demo", action="store_true", help="写入示例数据并生成周复盘报告")
    args = parser.parse_args()

    if args.log_dir:
        log_dir = Path(args.log_dir)
    else:
        ws = resolve_workspace_paths(
            account=args.account or "default",
            workspace_root=Path(args.workspace_root) if args.workspace_root else None,
        )
        log_dir = ws["content_log_dir"]
    if args.demo:
        jsonl = log_dir / "records.jsonl"
        if jsonl.exists():
            jsonl.unlink()
        for r in DEMO_RECORDS:
            append_record(log_dir, dict(r))
    elif args.add_json:
        p = Path(args.add_json)
        rec = json.loads(p.read_text(encoding="utf-8")) if p.exists() else json.loads(args.add_json)
        append_record(log_dir, rec)

    generate_weekly_review(log_dir, log_dir / "weekly_review.md", track=args.track)


if __name__ == "__main__":
    main()
