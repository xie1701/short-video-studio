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
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

DEMO_RECORDS = [
    {
        "video_id": "v_001", "date": "2026-11-08", "track": "store", "title": "价格越高的菜利润越低",
        "a_level": "A1", "hook_type": "颠覆切入", "opening_skill": "对立面开篇", "avg_shot_sec": 1.4,
        "style_id": "style_14", "pause_sec": 0.16, "bounce_2s": 0.28, "retention_5s": 0.52,
        "views": 18500, "retention_3s": 0.64, "completion_rate": 0.34, "likes": 420, "comments": 68, "dm_inquiries": 24,
    },
    {
        "video_id": "v_002", "date": "2026-11-09", "track": "store", "title": "八年老店老板的底线",
        "a_level": "A2", "hook_type": "痛点切入", "opening_skill": "不字开头", "avg_shot_sec": 1.6,
        "style_id": "style_14", "pause_sec": 0.18, "bounce_2s": 0.31, "retention_5s": 0.49,
        "views": 9200, "retention_3s": 0.58, "completion_rate": 0.31, "likes": 210, "comments": 45, "dm_inquiries": 31,
    },
    {
        "video_id": "v_003", "date": "2026-11-11", "track": "store", "title": "门店日常工作vlog",
        "a_level": "A3", "hook_type": "疑问切入", "opening_skill": "信息密度开篇", "avg_shot_sec": 3.4,
        "style_id": "style_06", "pause_sec": 0.26, "bounce_2s": 0.54, "retention_5s": 0.26, "drop_off_sec": 4.2,
        "views": 3100, "retention_3s": 0.39, "completion_rate": 0.16, "likes": 52, "comments": 8, "dm_inquiries": 3,
    },
    {
        "video_id": "v_004", "date": "2026-11-13", "track": "store", "title": "高于20块才敢用的鲜羊排真相",
        "a_level": "A1", "hook_type": "好奇切入", "opening_skill": "数字开篇", "avg_shot_sec": 1.3,
        "style_id": "style_02", "pause_sec": 0.15, "bounce_2s": 0.24, "retention_5s": 0.56,
        "views": 24600, "retention_3s": 0.69, "completion_rate": 0.38, "likes": 610, "comments": 95, "dm_inquiries": 29,
    },
]


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
    """将验证跑赢基线的节奏/停顿/风格谱/钩子参数沉淀为账号基因库 account_profile.json。"""
    pool = winners or records
    shot_secs = [float(r["avg_shot_sec"]) for r in pool if r.get("avg_shot_sec")]
    pause_secs = [float(r["pause_sec"]) for r in pool if r.get("pause_sec")]
    styles = [str(r["style_id"]) for r in pool if r.get("style_id")]

    target_shot = round(max(1.0, min(3.2, statistics.median(shot_secs))), 2) if shot_secs else 1.55
    base_pause = round(max(0.14, min(0.30, statistics.median(pause_secs))), 3) if pause_secs else 0.18
    pref_style = Counter(styles).most_common(1)[0][0] if styles else ("style_14" if track == "store" else "style_02")

    # 结合 2 秒跳出率判断前 3 秒是否需要更紧凑
    bounces = [float(r["bounce_2s"]) for r in records if r.get("bounce_2s") is not None]
    high_bounce = bool(bounces and statistics.mean(bounces) > 0.40)

    winning_hooks = sorted({f"{r.get('hook_type', '')}（{r.get('opening_skill', '')}）" for r in winners if r.get("hook_type")})
    sample_sufficient = len(records) >= 3
    out_p = log_dir / "account_profile.json"

    proposed = {
        "pause_sec": base_pause,
        "narrative_pause_sec": round(max(0.10, base_pause * 0.72), 3),
        "twist_pause_sec": round(min(0.38, max(0.26, base_pause * 1.55)), 3),
        "target_median_shot_sec": round(min(target_shot, 1.40) if high_bounce else target_shot, 2),
        "target_aroll_ratio": 0.35 if track == "store" else 0.30,
        "micro_hook_max_sec": 5.5 if high_bounce else 6.2,
        "preferred_style_id": pref_style,
        "winning_hooks": winning_hooks,
    }

    # 样本不足 3 条时冻结活跃默认参数：不覆盖已有 account_profile.json 的默认生效字段
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
            "target_median_shot_sec": prev_prof.get("target_median_shot_sec", 1.55),
            "target_aroll_ratio": prev_prof.get("target_aroll_ratio", 0.35 if track == "store" else 0.30),
            "micro_hook_max_sec": prev_prof.get("micro_hook_max_sec", 6.2),
            "preferred_style_id": prev_prof.get("preferred_style_id", "style_14" if track == "store" else "style_02"),
            "winning_hooks": prev_prof.get("winning_hooks", []),
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
    return out_p


def generate_weekly_review(log_dir: Path, out_md: Path) -> Dict[str, Any]:
    records = load_records(log_dir / "records.jsonl")
    if not records:
        raise ValueError("content-log 中暂无记录，无法生成周复盘。")

    track_counts = Counter(str(r.get("track", "store")) for r in records if r.get("track"))
    track = track_counts.most_common(1)[0][0] if track_counts else "store"
    base_views = statistics.mean(r.get("views", 0) for r in records)
    base_3s = statistics.mean(r.get("retention_3s", 0.0) for r in records)
    base_comp = statistics.mean(r.get("completion_rate", 0.0) for r in records)
    base_conv = statistics.mean((r.get("comments", 0) + r.get("dm_inquiries", 0)) for r in records)

    if track == "store":
        winners = [r for r in records if (r.get("comments", 0) + r.get("dm_inquiries", 0)) >= base_conv or r.get("retention_3s", 0.0) >= base_3s + 0.05]
    else:
        winners = [r for r in records if r.get("retention_3s", 0.0) >= base_3s and r.get("completion_rate", 0.0) >= base_comp]
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

    winning_hooks = sorted({f"{r.get('hook_type', '')}（{r.get('opening_skill', '')}）" for r in winners if r.get("hook_type")})
    losing_hooks = sorted({f"{r.get('hook_type', '')}（{r.get('opening_skill', '')}）" for r in laggards if r.get("hook_type")})

    # 微观留存断崖 EDL 反查
    drop_diagnoses = [d for d in (diagnose_edl_drop_off(r) for r in records) if d is not None]
    profile_path = build_account_profile(log_dir, records, winners, track)
    profile = json.loads(profile_path.read_text(encoding="utf-8"))

    # 微观漏斗汇总
    bounces = [float(r["bounce_2s"]) for r in records if r.get("bounce_2s") is not None]
    ret5s = [float(r["retention_5s"]) for r in records if r.get("retention_5s") is not None]
    micro_line = ""
    if bounces or ret5s:
        b_str = f"2秒跳出率 **{statistics.mean(bounces)*100:.1f}%**" if bounces else ""
        r5_str = f"5秒留存 **{statistics.mean(ret5s)*100:.1f}%**" if ret5s else ""
        micro_line = f"- **微观漏斗均值**：{' | '.join(x for x in (b_str, r5_str) if x)}"

    honest_note = (
        f"- **统计周期样本数**：{len(records)} 条（当前轨道：`{'门店轨' if track=='store' else '博主轨'}`）"
        if len(records) >= 3
        else f"- **统计周期样本数**：{len(records)} 条（⚠️ **样本不足 3 条，暂不调整账号核心策略与节奏参数**，当前只记录不归因）"
    )
    md = [
        "# 每周短视频运营诊断与账号参数进化报告",
        "",
        honest_note,
        f"- **账号核心基线 (Baseline)**：平均播放 **{base_views:.0f}** | 3秒留存 **{base_3s*100:.1f}%** | 完播率 **{base_comp*100:.1f}%** | 平均评论+私信 **{base_conv:.1f} 条/条**",
    ]
    if micro_line:
        md.append(micro_line)
    md += [
        f"- **账号参数基因输出**：已写入 `{profile_path.name}`（下条视频自动继承推荐节奏）",
        "",
        "## 一、 跑赢账号基线的钩子与节奏实测",
        "",
        f"- **高效钩子组合**：{('、'.join(winning_hooks)) if winning_hooks else '样本尚少，继续测试对比'}",
    ]
    for w in winners:
        md.append(
            f"  - 《{w.get('title','')}》[{w.get('a_level','-')} · {w.get('hook_type','-')}]: 播放 {w.get('views',0)} / 3秒留存 {w.get('retention_3s',0)*100:.0f}% / "
            f"完播 {w.get('completion_rate',0)*100:.0f}% / 评论+私信 {w.get('comments',0)+w.get('dm_inquiries',0)}"
        )
    md += [
        f"- **节奏实测对比**：{pace_conclusion}",
        "",
        "## 二、 低于基线项与微观留存断崖反查 (EDL 掉点病历)",
        f"- **低效模式**：{('、'.join(losing_hooks)) if losing_hooks else '本周无明显落后样本'}",
    ]
    for l in laggards:
        extra_drop = f"（留存断崖 @{l['drop_off_sec']}s）" if l.get("drop_off_sec") is not None else ""
        md.append(
            f"  - 《{l.get('title','')}》[{l.get('a_level','-')} · {l.get('hook_type','-')}]: 3秒留存 {l.get('retention_3s',0)*100:.0f}%（基线 {base_3s*100:.0f}%），单镜均值 {l.get('avg_shot_sec', 0)}s {extra_drop}"
        )
    if drop_diagnoses:
        md += [
            "",
            "### 留存断崖精确镜头定位",
            "",
            "| 视频标题 | 断崖时刻 | 对应剪辑表镜头 | 轨道角色 | 时段/时长 | 当时口播句 | 诊断处方 |",
            "| :--- | :---: | :--- | :--- | :---: | :--- | :--- |",
        ]
        for dd in drop_diagnoses:
            rx = "同期声单镜偏长或无模式中断，建议切语义 B-roll 或锁眼推拉" if dd["track_role"] == "a_roll" else "B-roll 画面与当前台词强关联不足，建议更换高冲击物证镜头"
            md.append(
                f"| 《{dd['title']}》 | {dd['drop_off_sec']}s | `{dd['clip_id']}` | `{dd['track_role']}` | "
                f"{dd['clip_span']} ({dd['duration_sec']}s) | {dd['sentence_text'][:18]} | {rx} |"
            )

    md += [
        "",
        "## 三、 下周参数自动进化 (`account_profile.json`) 与日历调整",
        "",
        "| 进化参数 | 推荐设定值 | 数据依据 |",
        "| :--- | :--- | :--- |",
        f"| **目标中位镜长** | `{profile['target_median_shot_sec']}s`（模式中断上限 `{profile['micro_hook_max_sec']}s`） | 基于胜出组单镜时长与 2 秒跳出率实测计算 |",
        f"| **动态呼吸停顿** | 基准 `{profile['pause_sec']}s`（陈述 `{profile['narrative_pause_sec']}s` / 反转 `{profile['twist_pause_sec']}s`） | 继承胜出组语流呼吸感并保护反转悬念 |",
        f"| **首选包装风格谱** | `{profile['preferred_style_id']}` | 胜出样本最高频风格谱映射 |",
        f"| **高效钩子加权** | `{' / '.join(winning_hooks) or '继续探索'}` | 3秒留存与有效询单领先基线 |",
        "",
    ]

    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(md), encoding="utf-8")
    summary = {
        "record_count": len(records),
        "baseline": {"views": base_views, "retention_3s": base_3s, "completion_rate": base_comp, "conv": base_conv},
        "winning_hooks": winning_hooks,
        "drop_diagnoses": drop_diagnoses,
        "account_profile_json": str(profile_path.resolve()),
        "weekly_review_md": str(out_md.resolve()),
    }
    print(f"[content_log] 已生成微观诊断复盘 -> {out_md} & 账号参数基因 -> {profile_path}")
    return summary


def main() -> None:
    from common import resolve_workspace_paths

    parser = argparse.ArgumentParser(description="content-log 微观留存诊断与账号参数基因库复盘")
    parser.add_argument("--log-dir", help="content-log 存储目录（未传时可配 --account 自动隔离）")
    parser.add_argument("--account", help="账号标识，自动定位该账号专属 content-log/")
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

    generate_weekly_review(log_dir, log_dir / "weekly_review.md")


if __name__ == "__main__":
    main()
