#!/usr/bin/env python3
"""字形覆盖门禁（fail-closed）：逐字核对 05_subtitles.json 的字幕文本能否被「主字体 + 回退链」画出来。

为什么要这条门禁：PIL/FreeType 不做字体回退，缺字就画成空白/豆腐块，而片子上屏前没人会逐字看。
用户实测（2026-11-24）：「阿里妈妈字体应该是不支持粤语文字的，很多字都显示不出来」——
fontTools 实测 `AlimamaShuHeiTi-Bold.otf` 的 cmap 缺 10 个粤语常用字
`佢 冇 咗 哋 啲 喺 嘅 嘢 嚟 𠮶`。

口径与 step5 完全一致（同一个 `glyph_fallback.coverage_report`）：
  主字体 = 05_subtitles.json 的 `font_path`（渲染端真正用的那个），
  回退链 = assets/fonts/font_aliases.json → glyph_fallback_chain。

用法：
  python3 dev_tools/verify_glyph_coverage.py --pipeline-dir <项目>/pipeline        # 只读校验
  python3 dev_tools/verify_glyph_coverage.py --subs 05_subtitles.json --json      # 机读输出
  python3 dev_tools/verify_glyph_coverage.py --pipeline-dir ... --primary <字体>   # 换主字体预估

缺一个字就 exit 1（列出缺字与所在行）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "short-video-studio"
sys.path.insert(0, str(SKILL / "scripts"))

import glyph_fallback as G  # noqa: E402


def load_rows(subs_path: Path) -> Tuple[Dict[str, Any], List[Tuple[str, str]]]:
    doc = json.loads(subs_path.read_text(encoding="utf-8"))
    rows: List[Tuple[str, str]] = []
    for i, ent in enumerate(doc.get("entries") or [], start=1):
        if not isinstance(ent, dict):
            continue
        # 口径与 step5 门禁一致：全部条目都查（包括被让位 suppressed 的，它们也是这份字幕文本的一部分）
        rows.append((str(ent.get("id") or f"#{i}"), str(ent.get("text") or "")))
    return doc, rows


def main() -> int:
    ap = argparse.ArgumentParser(description="字形覆盖门禁：主字体 + 回退链能否画出全部字幕字")
    ap.add_argument("--pipeline-dir", type=Path, default=None, help="项目 pipeline 目录（读 05_subtitles.json）")
    ap.add_argument("--subs", type=Path, default=None, help="直接指定 05_subtitles.json")
    ap.add_argument("--primary", default=None, help="覆盖主字体（默认取 05_subtitles.json 的 font_path）")
    ap.add_argument("--json", action="store_true", help="机读输出")
    args = ap.parse_args()

    subs_path = args.subs or (args.pipeline_dir / "05_subtitles.json" if args.pipeline_dir else None)
    if not subs_path or not Path(subs_path).is_file():
        print(f"找不到 05_subtitles.json：{subs_path}", file=sys.stderr)
        return 2

    doc, rows = load_rows(Path(subs_path))
    primary = args.primary or doc.get("font_path")
    try:
        report = G.coverage_report(rows, primary, strict_chain=True)
    except G.GlyphCoverageError as exc:
        print(f"[glyph] 配置错误：{exc}", file=sys.stderr)
        return 2
    report["template_id"] = doc.get("template_id")
    report["stored"] = doc.get("glyph_coverage")
    report["subs_path"] = str(Path(subs_path).resolve())
    report["lines"] = len(rows)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"[glyph] {Path(subs_path).resolve()}  模板 {doc.get('template_id')}  行数 {len(rows)}")
        print(G.format_report(report))
        stored = doc.get("glyph_coverage")
        if isinstance(stored, dict) and stored.get("missing") != report["missing"]:
            print(f"[glyph] 注意：文件里存的 glyph_coverage.missing={stored.get('missing')} 与本次实算不一致（渲染端以本次实算为准）")

    if report["missing"]:
        print(
            f"[glyph] FAIL：{len(report['missing'])} 个字无字体可画（{''.join(report['missing'])}），"
            "上屏会变成空白/豆腐块",
            file=sys.stderr,
        )
        return 1
    print(f"[glyph] PASS：{report['chars']} 个去重字全部有字形（回退 {sum(report['fallback_used'].values())} 字次）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
