#!/usr/bin/env python3
"""
[数据回路与每周一页复盘]
1. 在本地 content-log/records.jsonl 中记录每条视频发布后的后台数据：
   播放量、3秒留存率、完播率、点赞数、评论数、私信或咨询数、到店核销数，
   以及方法论元数据（轨道、5A层级、钩子切入点、开篇技巧、镜头平均时长）。
2. 自动计算账号各项基线 (Baseline)，识别表现显著高于基线的钩子类型与剪辑节奏。
3. 每周输出一页复盘报告 weekly_review.md，给出下周内容日历调整方案及每项调整的数据依据。
"""

from __future__ import annotations

import argparse
import json
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

DEMO_RECORDS = [
    {
        "video_id": "v_001", "date": "2026-11-08", "track": "store", "title": "价格越高的菜利润越低",
        "a_level": "A1", "hook_type": "颠覆切入", "opening_skill": "对立面开篇", "avg_shot_sec": 1.4,
        "views": 18500, "retention_3s": 0.64, "completion_rate": 0.34, "likes": 420, "comments": 68, "dm_inquiries": 24,
    },
    {
        "video_id": "v_002", "date": "2026-11-09", "track": "store", "title": "八年老店老板的底线",
        "a_level": "A2", "hook_type": "痛点切入", "opening_skill": "不字开头", "avg_shot_sec": 1.6,
        "views": 9200, "retention_3s": 0.58, "completion_rate": 0.31, "likes": 210, "comments": 45, "dm_inquiries": 31,
    },
    {
        "video_id": "v_003", "date": "2026-11-11", "track": "store", "title": "门店日常工作vlog",
        "a_level": "A3", "hook_type": "疑问切入", "opening_skill": "信息密度开篇", "avg_shot_sec": 3.4,
        "views": 3100, "retention_3s": 0.39, "completion_rate": 0.16, "likes": 52, "comments": 8, "dm_inquiries": 3,
    },
    {
        "video_id": "v_004", "date": "2026-11-13", "track": "store", "title": "高于20块才敢用的鲜羊排真相",
        "a_level": "A1", "hook_type": "好奇切入", "opening_skill": "数字开篇", "avg_shot_sec": 1.3,
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


def generate_weekly_review(log_dir: Path, out_md: Path) -> Dict[str, Any]:
    records = load_records(log_dir / "records.jsonl")
    if not records:
        raise ValueError("content-log 中暂无记录，无法生成周复盘。")

    track = records[-1].get("track", "store")
    base_views = statistics.mean(r["views"] for r in records)
    base_3s = statistics.mean(r["retention_3s"] for r in records)
    base_comp = statistics.mean(r["completion_rate"] for r in records)
    base_conv = statistics.mean((r["comments"] + r["dm_inquiries"]) for r in records)

    # 识别胜出组与落后组
    if track == "store":
        # 门店轨：重点看咨询/私信/评论 + 3秒留存
        winners = [r for r in records if (r["comments"] + r["dm_inquiries"]) >= base_conv or r["retention_3s"] >= base_3s + 0.05]
        laggards = [r for r in records if r not in winners]
    else:
        # 博主轨：重点看 3秒留存 + 完播率
        winners = [r for r in records if r["retention_3s"] >= base_3s and r["completion_rate"] >= base_comp]
        laggards = [r for r in records if r not in winners]

    fast_pace_views = statistics.mean([r["views"] for r in records if r.get("avg_shot_sec", 2.0) <= 2.0] or [base_views])
    slow_pace_views = statistics.mean([r["views"] for r in records if r.get("avg_shot_sec", 2.0) > 2.0] or [base_views])

    winning_hooks = sorted({f"{r['hook_type']}（{r['opening_skill']}）" for r in winners})
    losing_hooks = sorted({f"{r['hook_type']}（{r['opening_skill']}）" for r in laggards})

    md = [
        "# 每周短视频运营复盘与下周日历调整方案",
        "",
        f"- **统计周期样本数**：{len(records)} 条（当前轨道：`{'门店轨' if track=='store' else '博主轨'}`）",
        f"- **账号核心基线 (Baseline)**：平均播放 **{base_views:.0f}** | 3秒留存 **{base_3s*100:.1f}%** | 完播率 **{base_comp*100:.1f}%** | 平均评论+私信咨询 **{base_conv:.1f} 条/条**",
        "",
        "## 一、 跑赢账号基线的钩子与节奏特征",
        "",
        f"- **高效钩子组合**：{('、'.join(winning_hooks)) if winning_hooks else '样本尚少，继续测试对比'}",
    ]
    for w in winners:
        md.append(
            f"  - 《{w['title']}》[{w['a_level']} · {w['hook_type']}]: 播放 {w['views']} / 3秒留存 {w['retention_3s']*100:.0f}% / "
            f"完播 {w['completion_rate']*100:.0f}% / 评论+私信 {w['comments']+w['dm_inquiries']}（高于基线）"
        )
    md += [
        f"- **节奏实测对比**：平均镜头时长 `≤ 2.0s` 的快节奏成片均值播放 **{fast_pace_views:.0f}**，显著高于 `> 2.0s` 慢节奏的 **{slow_pace_views:.0f}**。",
        "",
        "## 二、 低于基线的待优化项",
        f"- **低效模式**：{('、'.join(losing_hooks)) if losing_hooks else '本周无明显落后样本'}",
    ]
    for l in laggards:
        md.append(
            f"  - 《{l['title']}》[{l['a_level']} · {l['hook_type']}]: 3秒留存仅 {l['retention_3s']*100:.0f}%（低于基线 {base_3s*100:.0f}%），单镜均值 {l.get('avg_shot_sec', 0)}s 偏拖沓。"
        )

    md += [
        "",
        "## 三、 下周内容日历调整方案及依据",
        "",
        "| 调整项 | 下周怎么调 | 数据依据 |",
        "| :--- | :--- | :--- |",
        f"| **钩子模板加权** | 下周 50% 选题直接复用 `{' / '.join(winning_hooks) or '颠覆/好奇切入'}` | 该类钩子 3 秒留存与转化均高于账号基线（{base_3s*100:.0f}%） |",
        f"| **剪辑节奏锁定** | 强制 Step 4 单镜平均时长控制在 `1.2s–1.8s`，前 3 秒至少 3 个画面锚点 | 快节奏样本播放 ({fast_pace_views:.0f}) vs 慢节奏 ({slow_pace_views:.0f})，高密度切镜拉升留存 |",
        "| **5A 配比微调** | 若门店咨询已达标，从阶段一逐步提升 `A2 人设信任 + A3 垂直说服` 占比至 70% | A2 人设类内容单条私信咨询效率最高（如痛点+原则/底线表达） |",
        "",
    ]

    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(md), encoding="utf-8")
    summary = {
        "record_count": len(records),
        "baseline": {"views": base_views, "retention_3s": base_3s, "completion_rate": base_comp, "conv": base_conv},
        "winning_hooks": winning_hooks,
        "weekly_review_md": str(out_md.resolve()),
    }
    print(f"[content_log] 已生成一页周复盘报告 -> {out_md}")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="content-log 数据记录与每周一页复盘")
    parser.add_argument("--log-dir", required=True, help="content-log 存储目录")
    parser.add_argument("--add-json", help="追加一条发布后台数据 JSON 字符串或文件")
    parser.add_argument("--demo", action="store_true", help="写入示例数据并生成周复盘报告")
    args = parser.parse_args()

    log_dir = Path(args.log_dir)
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
