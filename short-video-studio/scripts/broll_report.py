#!/usr/bin/env python3
"""把 `04_edl.json` 的 B-roll 对位账本摊成一张能直接看的表（Markdown 或终端）。

用户要的不是「命中率 84%」这一个数字，而是「每一个外来画面凭什么配在这句台词上」，
以及「哪几个确实没依据」。所以这张表逐镜给：时间点、压着的那句台词、用了哪条素材、
命中层级、命中的概念（或没命中的原因）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List


def load_rows(pipeline_dir: Path) -> Dict[str, Any]:
    p = Path(pipeline_dir) / "04_edl.json"
    edl = json.loads(p.read_text(encoding="utf-8"))
    sem = edl.get("broll_semantic") or {}
    return {"edl": edl, "sem": sem, "rows": sem.get("rows") or []}


def render_md(rows: List[Dict[str, Any]], sem: Dict[str, Any]) -> str:
    lines: List[str] = []
    total = len(rows)
    hit = sum(1 for r in rows if r.get("tier") == "hit")
    weak = sum(1 for r in rows if r.get("tier") == "weak")
    none = sum(1 for r in rows if r.get("tier") not in ("hit", "weak"))
    ratio = (hit / total) if total else 0.0
    lines.append(f"# B-roll 配镜对位账本（{total} 个外来画面）\n")
    lines.append(f"- 贴合 **{hit}** ｜ 同场景/沾边 **{weak}** ｜ 无依据 **{none}** ｜ 命中率 **{ratio:.0%}**")
    gate = sem.get("gate") or {}
    if gate:
        lines.append(f"- 门禁：{'PASS' if gate.get('pass') else 'FAIL'}"
                     f"（hit≥{gate.get('min_hit_ratio', '')}，none≤{gate.get('max_none_ratio', '')}）"
                     f"{' · 已豁免' if gate.get('waived') else ''}")
    lines.append("")
    lines.append("| # | 时间(s) | 压着的台词 | 用的素材 | 层级 | 凭什么对得上 / 为什么没有 |")
    lines.append("|---:|---:|---|---|---|---|")
    for i, r in enumerate(rows, 1):
        tier = str(r.get("tier") or "none")
        mark = {"hit": "✅ 贴合", "weak": "🟡 沾边"}.get(tier, "❌ 无依据")
        why = str(r.get("why") or "").replace("|", "／")
        sent = str(r.get("sentence") or "").replace("|", "／")
        lines.append(
            f"| {i} | {float(r.get('timeline_in_sec') or 0):.2f} | {sent[:26]} | "
            f"{str(r.get('media') or '').replace('|', '／')} | {mark} | {why[:80]} |"
        )
    bad = [r for r in rows if r.get("tier") not in ("hit", "weak")]
    if bad:
        lines.append("")
        lines.append("## 无依据清单（素材池里确实没有能对上这条台词的东西）")
        for i, r in enumerate(bad, 1):
            lines.append(
                f"{i}. `{float(r.get('timeline_in_sec') or 0):.2f}s` "
                f"「{str(r.get('sentence') or '')[:30]}」→ `{r.get('media')}`：{r.get('why')}"
            )
    return "\n".join(lines) + "\n"


def render_plain(rows: List[Dict[str, Any]]) -> str:
    out = []
    for r in rows:
        out.append(f"{float(r.get('timeline_in_sec') or 0):6.2f}s [{r.get('tier'):4s}] "
                   f"{str(r.get('media') or '')[:44]:44s} {str(r.get('why') or '')[:60]}")
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="摊开 B-roll 配镜账本")
    ap.add_argument("--pipeline-dir", required=True)
    ap.add_argument("--md", help="写成 Markdown 到这个路径")
    args = ap.parse_args(argv)
    data = load_rows(Path(args.pipeline_dir))
    md = render_md(data["rows"], data["sem"])
    if args.md:
        Path(args.md).write_text(md, encoding="utf-8")
        print(f"[broll-report] -> {args.md}")
    else:
        print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
