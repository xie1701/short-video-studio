#!/usr/bin/env python3
"""
画面理解必须走大模型 —— 这个模块负责把「等大模型判断」这件事变成流程，而不是一句口号。

用户 2026-11-23 11:27 的原话
──────────────────────────
「抽密帧 + 多模态识别要走大模型，本身就是用 Agent 处理，让 Agent 判断，严禁走本地，
  不得已的话要把问题告知用户！」

为什么不能拿本地 ImageNet 分类冒充
──────────────────────────────────
本地 Vision 分类能给的是「沙发 / 厨房 / 汽车」这种物件级标签，置信度还经常 0.2 上下。
它能救急（比如判「牌子写着沙发区、画面是办公桌」），但它**不知道这段画面在讲什么**，
更给不出「这张图能不能用来说明成本高」这种判断。用它冒充足量的大模型理解，
就是让用户以为自己有语义能力，实际配镜还是瞎配——这比不做更坏。

所以现在的分工是硬的：
* **默认 `engine=agent`**：脚本只负责把帧抽出来、落成可交给 Agent 的任务包
  （每段 6–12 帧、保留到磁盘上、带时间点），然后**停下来**等大模型/Agent 写判定；
  没有判定就明确标记 `pending_agent`，配镜环节知道自己没有依据。
* **`engine=local`** 必须显式 `--i-accept-local`，而且会在结果里写
  `engine_ack: "user_accepted_local_fallback"`，谁打开文件都能看见这是降级结果。
* **押不到大模型时**：`_VLM 未配置` 要打印出来，不许静默当成功。

任务包与判定包的格式
────────────────────
`00_vision_tasks.json`:
    {"clips": [{"file": "IMG_1938.MOV", "duration_sec": 12.4,
                "frames": [{"path": "/abs/frame_000.jpg", "t": 1.2}, ...],
                "ask": "这段画面在拍什么？一句话，说清主体+场景+动作"}]}

`00_vision_labels.json`（由 Agent / 大模型写入）:
    {"clips": [{"file": "IMG_1938.MOV",
                "summary": "后厨不锈钢台面，师傅在穿羊肉串",
                "tags": ["后厨", "烧烤", "人物出镜"],
                "evidence": ["frame_000.jpg: 不锈钢台面与串好的肉串"]}]}
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

TASKS_NAME = "00_vision_tasks.json"
LABELS_NAME = "00_vision_labels.json"
SEMANTICS_NAME = "00_frame_semantics.json"
NOTES_NAME = "素材说明_待填写.txt"

LOCAL_VISION_REFUSAL = (
    "拒绝用本地分类冒充大模型理解。\n"
    "本地 Vision 只能给物件级标签（沙发/厨房/汽车）且置信度常低于 0.3，"
    "它不知道这段画面在讲什么，拿来配镜还是瞎配。\n"
    "做法二选一：\n"
    "  1) 默认流程：跑 --engine agent，把 00_vision_tasks.json 交给 Agent（大模型）判，"
    "结果写进 00_vision_labels.json，再 --apply；\n"
    "  2) 你确实只要一个临时标签：加 --i-accept-local，结果会打上 "
    "user_accepted_local_fallback 降级标记。"
)


def slug_of(filename: str) -> str:
    s = re.sub(r"[^\w.\-]+", "_", str(filename or "clip"))
    return s[:80] or "clip"


def build_tasks(clips: List[Dict[str, Any]]) -> Dict[str, Any]:
    """把「抽好的帧」整理成交给大模型的任务包。"""
    tasks: List[Dict[str, Any]] = []
    for c in clips:
        frames = []
        for f in c.get("frame_paths") or []:
            if isinstance(f, dict):
                frames.append({"path": str(f.get("path")), "t": round(float(f.get("t") or 0.0), 2)})
            else:
                frames.append({"path": str(f), "t": 0.0})
        tasks.append({
            "file": str(c.get("file") or ""),
            "path": str(c.get("path") or ""),
            "duration_sec": round(float(c.get("duration_sec") or 0.0), 2),
            "frames": frames,
            "ask": "这段画面在拍什么？一句话说清「主体 + 场景 + 动作」，能看出行业就带上；"
                   "画面里的门头/价目/包装上的字一并抄下来。不要编看不清的东西。",
        })
    return {
        "step": "vision_tasks",
        "engine": "agent",
        "how_to_fill": (
            f"把每段的帧交给大模型（或 Agent 的视觉能力）看，结论写进 {LABELS_NAME}："
            '{"clips":[{"file","summary","tags","evidence"}]}，然后运行 '
            "tag_frames.py --apply --out <同一目录>"
        ),
        "clips": tasks,
    }


def write_tasks(out_dir: Path, clips: List[Dict[str, Any]]) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / TASKS_NAME
    p.write_text(json.dumps(build_tasks(clips), ensure_ascii=False, indent=1), encoding="utf-8")
    return p


def load_labels(out_dir: Path, path: Optional[Path] = None) -> Dict[str, Any]:
    p = path or (out_dir / LABELS_NAME)
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if isinstance(data, list):
        return {"clips": data}
    return data if isinstance(data, dict) else {}


def _labels_to_notes(labels: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for c in labels.get("clips") or []:
        if not isinstance(c, dict):
            continue
        name = str(c.get("file") or "").strip()
        if not name:
            continue
        out[name] = {
            "summary": str(c.get("summary") or "").strip(),
            "tags": [str(t).strip() for t in (c.get("tags") or []) if str(t).strip()],
            "evidence": [str(e).strip() for e in (c.get("evidence") or []) if str(e).strip()],
        }
    return out


def merge_labels(out_dir: Path, labels: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """把大模型的判定并进 00_frame_semantics.json 与《素材说明》草稿。

    返回 {"clips": 命中段数, "total": 总段数, "missing": [没判定的文件名]}——
    没判定的要响亮地说出来，不许当成"已经理解过了"。
    """
    labels = labels if labels is not None else load_labels(out_dir)
    by_file = _labels_to_notes(labels)
    sem_p = out_dir / SEMANTICS_NAME
    sem: Dict[str, Any] = {}
    if sem_p.is_file():
        try:
            sem = json.loads(sem_p.read_text(encoding="utf-8"))
        except Exception:
            sem = {}
    clips = list(sem.get("clips") or [])
    hit, missing = 0, []
    for c in clips:
        name = str(c.get("file") or "")
        lab = by_file.get(name)
        if not lab:
            c["vision_engine"] = "pending_agent"
            missing.append(name)
            continue
        hit += 1
        c["vision_engine"] = "agent"
        c["vlm_description"] = lab["summary"]
        if lab["tags"]:
            c["scene_tags_zh"] = lab["tags"]
            try:
                from vision_scene_labels import tag_groups
                c["scene_tag_groups"] = tag_groups(lab["tags"])
            except Exception:
                pass
        c["agent_evidence"] = lab["evidence"]
        c["suggested_note"] = "、".join([lab["summary"]] + lab["tags"][:3])[:80] if lab["summary"] else ""
    sem["engine"] = "agent"
    sem["clips"] = clips
    sem["agent_judged"] = hit
    out_dir.mkdir(parents=True, exist_ok=True)
    sem_p.write_text(json.dumps(sem, ensure_ascii=False, indent=1), encoding="utf-8")

    # 《素材说明》草稿：判过的写判定，没判的留空并注明待判
    lines = ["# 一行一条：文件名 + 一句画面内容（大模型判定结果，改两个字就准了）"]
    for c in clips:
        name = str(c.get("file") or "")
        note = str(c.get("suggested_note") or "")
        lines.append(f"{name} {note}".rstrip() if note else f"{name}  （待 Agent 判定）")
    (out_dir / NOTES_NAME).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"clips": hit, "total": len(clips), "missing": missing}
