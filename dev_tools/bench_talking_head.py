#!/usr/bin/env python3
"""Compare completed talking-head editing projects across topics."""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).resolve().parents[1] / "short-video-studio" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from aesthetic_critic import REVIEW_RESULT_FILE, score_aesthetic_review
from common import read_json, write_json

COLUMNS = (
    "project", "qa_fail_count", "max_headline_chars", "highlight_ratio",
    "sfx_count", "has_music_breathing", "critic_mean_score", "passed",
)


def _pipeline(project: Path) -> Path:
    return project / "pipeline" if (project / "pipeline").is_dir() else project


def score_project(project: Path) -> dict[str, Any]:
    pipeline = _pipeline(project)
    offline = score_aesthetic_review(pipeline)
    try:
        saved = read_json(pipeline / REVIEW_RESULT_FILE)
    except (OSError, ValueError):
        saved = {}
    if not isinstance(saved, dict) or not isinstance(saved.get("mean_score"), (int, float)):
        saved = offline
    scores = offline["metrics"]
    name = pipeline.parent.name if pipeline.name == "pipeline" else pipeline.name
    ratio = scores["highlight_ratio"]
    passes = (
        scores["qa_fail_count"] == 0 and 0 < scores["max_headline_chars"] <= 7
        and ratio is not None and 0.35 <= ratio <= 0.55
        and 6 <= scores["sfx_count"] <= 14 and scores["has_music_breathing"]
        and saved["mean_score"] >= 8.5 and saved.get("min_score", 0) >= 8.0
    )
    return {"project": name, **{key: scores[key] for key in COLUMNS[1:6]},
            "critic_mean_score": saved["mean_score"], "passed": passes}


def format_markdown(rows: list[dict[str, Any]]) -> str:
    def cell(value: Any) -> str:
        if value is None:
            return "—"
        if isinstance(value, bool):
            return "是" if value else "否"
        return str(value).replace("|", r"\|").replace("\n", " ")

    lines = ["| " + " | ".join(COLUMNS) + " |", "| " + " | ".join("---" for _ in COLUMNS) + " |"]
    lines.extend("| " + " | ".join(cell(row[key]) for key in COLUMNS) + " |" for row in rows)
    return "\n".join(lines)


def _workspace_projects(root: Path) -> list[Path]:
    pools = sorted(root.glob("accounts/*/projects/*"))
    if (root / "pipeline").is_dir() or (root / "03_audio_events.json").is_file():
        pools.append(root)
    pools.extend(sorted(p for p in root.glob("*") if (p / "pipeline").is_dir()))
    return list(dict.fromkeys(p for p in pools if p.is_dir()))


def _smoke_project(directory: Path, theme: str, label: str) -> None:
    pipeline = directory / "pipeline"
    texts = (label, "这里先停一停", "看真实需求", "接着解释", "比较两种做法", "中间留一口气", "利润分开算", "说到这里")
    sentences = [{"id": f"s{i}", "text": text, "start_sec": i * 4, "end_sec": (i + 1) * 4}
                 for i, text in enumerate(texts)]
    acts = [
        {"act_id": f"a{i}", "sentence_ids": [s["id"]],
         "primitive": "clean_breath" if i % 2 else "hero_intro_pop",
         "visual_copy": {"headline": "" if i % 2 else s["text"]}, "sfx_budget": 0 if i % 2 else 2}
        for i, s in enumerate(sentences)
    ]
    items = [
        {"act_id": acts[i]["act_id"], "variant": "hero_lime", "text_or_asset": s["text"],
         "z_layer": "foreground", "theme_profile": theme, "render_backend": "web_spring"}
        for i, s in enumerate(sentences) if i % 2 == 0
    ]
    cue = {"type": "delay_entry", "start_sec": 0, "end_sec": 2.0, "depth_db": -36.0, "fade_ms": 100}
    docs = {
        "03_audio_events.json": {"sentences": sentences, "locked_duration_sec": 32},
        "03b_director_plan.json": {"acts": acts, "theme_profile": theme, "music_cues": [cue]},
        "01b_composition_profile.json": {"allow_behind_person": theme == "store_vibrant"},
        "06_packaging.json": {"kinetic_stage": {"items": items, "theme_profile": theme},
                              "sfx_cues": [{"time_sec": t, "sfx_role": "pop"} for t in (0, 1.5, 8, 9.5, 16, 24)]},
        "08_mix_metrics.json": {"music_cue_metrics": {"applied_count": 1, "applied_cues": [cue]}},
        "10_qa_report.json": {"fail_count": 0},
    }
    for filename, data in docs.items():
        write_json(pipeline / filename, data)


def _smoke_rows() -> list[dict[str, Any]]:
    with tempfile.TemporaryDirectory(prefix="talking-head-bench-") as directory:
        root = Path(directory)
        samples = (
            ("商业复盘", "business_editorial", "现金流要算清"),
            ("粤语探店", "store_vibrant", "招牌叉烧"),
            ("方法清单", "business_editorial", "先问生意前提"),
        )
        for name, theme, label in samples:
            _smoke_project(root / name, theme, label)
        return [score_project(root / name) for name, _, _ in samples]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--projects", nargs="+", type=Path)
    target.add_argument("--workspace-root", type=Path)
    target.add_argument("--smoke", action="store_true")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    args = parser.parse_args()
    if args.smoke:
        rows = _smoke_rows()
    else:
        projects = args.projects or _workspace_projects(args.workspace_root.expanduser())
        if not projects:
            parser.error("没有找到可评测的项目目录")
        rows = [score_project(project.expanduser()) for project in projects]
    if args.format == "json":
        print(json.dumps({"mode": "smoke" if args.smoke else "projects", "projects": rows},
                         ensure_ascii=False, indent=2))
    else:
        print(format_markdown(rows))
    return 0 if all(row["passed"] for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
