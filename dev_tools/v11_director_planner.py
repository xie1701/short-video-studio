#!/usr/bin/env python3
"""
v11_director_planner.py · 通用导演规划包装器与 CLI

已彻底废除早期的 BIZ/STO 静态字幕表、静态卡片块、写死封面文案与硬编码花字分支。
所有项目（含未知新素材）均直接委托给 `director_plan.py` 通用协议与 `style_visual_truth.py`
动态生成 `03b_director_plan.json`。
"""

from __future__ import annotations

import argparse
import copy
import sys
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "short-video-studio" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import director_plan  # noqa: E402
from common import read_json, write_json  # noqa: E402


def build_director_plan_for_audio(
    audio_events: Dict[str, Any],
    style_id: str = "style_02",
    inventory: Optional[Dict[str, Any]] = None,
    composition_profile: Optional[Dict[str, Any]] = None,
    visual_index: Optional[Dict[str, Any]] = None,
    base_plan: Optional[Dict[str, Any]] = None,
    pipeline_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """基于锁定语音轴与可选实拍索引，委托通用导演引擎产出并校验 03b_director_plan。"""
    plan = director_plan.generate_deterministic_director_plan(
        audio_events=audio_events,
        style_id=style_id,
        inventory=inventory,
        composition_profile=composition_profile,
        visual_index=visual_index,
        pipeline_dir=pipeline_dir,
    )
    if isinstance(base_plan, dict) and base_plan:
        merged = copy.deepcopy(base_plan)
        merged.update(plan)
        chk = director_plan.validate_director_plan(merged, audio_events, composition_profile)
        if chk["ok"]:
            return chk["sanitized_plan"]
    return plan


def build_director_plan_for_pipeline(
    pipeline_dir: Path,
    style_id: str = "style_02",
    write_plan: bool = False,
) -> Dict[str, Any]:
    """从 pipeline 目录读取锁音、盘点与视觉索引，动态产出通用导演总谱。"""
    pdir = Path(pipeline_dir)
    audio_events = read_json(pdir / director_plan._AUDIO_FILENAME)
    inventory = director_plan._optional_document(pdir, director_plan._INVENTORY_FILENAME)
    profile = director_plan._optional_document(pdir, director_plan._COMPOSITION_FILENAME)
    visual_index = director_plan._optional_document(pdir, "00_visual_index.json")
    plan = build_director_plan_for_audio(
        audio_events=audio_events,
        style_id=style_id,
        inventory=inventory,
        composition_profile=profile,
        visual_index=visual_index,
        pipeline_dir=pdir,
    )
    if write_plan:
        write_json(pdir / director_plan.PLAN_FILENAME, plan)
    return plan


def main() -> None:
    parser = argparse.ArgumentParser(description="通用导演总谱生成器（委托 director_plan.py）")
    parser.add_argument("--pipeline-dir", type=Path, required=True, help="项目 pipeline 目录")
    parser.add_argument("--style-id", default="style_02", help="目标视觉风格 ID（style_01..style_24）")
    parser.add_argument("--out", type=Path, default=None, help="可选输出 JSON 路径，默认写入 03b_director_plan.json")
    args = parser.parse_args()

    pdir = args.pipeline_dir.expanduser()
    plan = build_director_plan_for_pipeline(pdir, style_id=args.style_id, write_plan=False)
    out_path = args.out.expanduser() if args.out is not None else (pdir / director_plan.PLAN_FILENAME)
    write_json(out_path, plan)
    print(f"[v11_director_planner] 已生成通用导演总谱 ({args.style_id}) -> {out_path}")


if __name__ == "__main__":
    main()
