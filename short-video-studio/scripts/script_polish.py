#!/usr/bin/env python3
"""文案整理：把 ASR 的逐字稿变成能上屏、能核对的一行一句话。

为什么必须单独一道
──────────────────
SenseVoice 给的是**逐字原话**：专名准（牛哥/金湾/正门/厨房/卡座），但带口癖、半截句、
重拍的多版本、现场口令。CLAUDE 2026-11-24 实测同一批素材：73 句里真正能用的只有 20 出头，
其余是同一句的四个重拍版本、`用钱`、`点讲`、`系咪` 这类碎片。

分工是硬的：
* **脚本**（本文件）只做确定的事：给 Agent 出任务清单、按 Agent 的判定落盘、算时长与缺口。
* **Agent（大模型）**做判断：哪一版是最好的那条、哪几句该合并、口癖去掉后原句是什么。
* 谁都不许「凭时长猜台词」——没有判定就明确标 pending，不编。

产物
────
`02b_polish_tasks.json`  给 Agent 的任务清单（含每段的原字、时间、时长、来源文件）
`02b_polish_labels.json` Agent 的判定：{"lines":[{"text","segs":[id...],"keep":true,"note"}]}
`02c_script_final.json`  落地后的成稿（确认单、字幕、剪辑都读这个）

整理三原则（写进任务清单，Agent 必须照做）：
1. **不改事实、不换词**：只去口癖、合并被停顿切碎的半句、补标点；粤语就保持粤语字面。
2. **一句话一个意思**，超过 20 秒的合并要拆开。
3. **听不准的词标出来**，别猜：写在 `note` 里，确认单上会显示成「你核一下」。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

TASKS_NAME = "02b_polish_tasks.json"
LABELS_NAME = "02b_polish_labels.json"
FINAL_NAME = "02c_script_final.json"

# 单独成句就说明「这句没说完」的东西：纯语气词、口令、口头禅
STANDALONE_NOISE = {
    "系咪", "点讲", "用钱", "大家都坐", "最想要", "k我记得到", "三二一组", "三二一走",
    "再一次唔好卡o", "冇冇冇所以我占我", "所以", "然后", "跟住", "诶", "啊", "嗯", "哦",
}

RULES = [
    "只去口癖、合并被停顿切碎的同义半句、补标点；**不改事实、不换词**。",
    "粤语就保持粤语字面（咗/嘅/喺/系/嚟），不要改写成普通话。",
    "同一句话重拍了好几版时，留最完整、最顺的一版，其余 keep=false 并写 note=重拍。",
    "听不准的地方不要猜，把可疑词写在 note 里（确认单会显示「你核一下」）。",
    "一行控制在 20 秒以内，超了就按意思拆成两行。",
]


def _seg_text(s: Dict[str, Any]) -> str:
    return str(s.get("text") or "").strip()


def kept_segments(transcript: Dict[str, Any]) -> List[Dict[str, Any]]:
    segs = transcript.get("segments") or transcript.get("kept_segments") or []
    return [
        s for s in segs
        if s.get("keep") is not False and not str(s.get("drop_reason") or "").strip() and _seg_text(s)
    ]


def build_tasks(pipeline_dir: Path) -> Path:
    tr_p = pipeline_dir / "02_transcript.json"
    if not tr_p.is_file():
        raise FileNotFoundError(f"缺少 {tr_p}")
    tr = json.loads(tr_p.read_text("utf-8"))
    segs = kept_segments(tr)
    lines = []
    for s in segs:
        dur = max(0.0, float(s.get("end_sec", 0)) - float(s.get("start_sec", 0)))
        lines.append({
            "id": s.get("id"),
            "text": _seg_text(s),
            "start_sec": s.get("start_sec"),
            "end_sec": s.get("end_sec"),
            "dur": round(dur, 2),
            "source": Path(str(s.get("source_media") or "")).name,
            "asr_lang": s.get("asr_lang"),
            "auto_suspect": _suspects(_seg_text(s), dur),
        })
    doc = {
        "step": "script_polish",
        "rules": RULES,
        "how_to_fill": (
            f"把结论写进 {LABELS_NAME}：{{\"lines\":[{{\"text\":\"\",\"segs\":[\"seg_007\"],"
            "\"keep\":true,\"note\":\"\"}]}}；segs 可以合并多段（按时间顺序），"
            "keep=false 表示这句不要；然后跑 script_polish.py --apply --pipeline-dir <pipeline>"
        ),
        "total_lines": len(lines),
        "total_dur": round(sum(x["dur"] for x in lines), 2),
        "lines": lines,
    }
    out = pipeline_dir / TASKS_NAME
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[polish] 任务清单 -> {out}（{len(lines)} 句，{doc['total_dur']} 秒）")
    return out


def _suspects(text: str, dur: float) -> List[str]:
    """给 Agent 的提示：哪些地方值得怀疑。只是线索，不是结论。"""
    out: List[str] = []
    if text in STANDALONE_NOISE or len(text) <= 3:
        out.append("过短/像口令或口癖，优先考虑丢弃")
    if dur > 18:
        out.append("这一句超过 18 秒，可能该拆开")
    if any(t in text for t in ("okk", "ok k", " o k", "嚟ok", "三二一", "二一组", "第三一组")):
        out.append("含现场口令残留")
    if any(t in text for t in ("诶", "咧", "嘅嘅", "系系", "你你", "大家大家", "俾俾")):
        out.append("含重复口癖")
    return out


def apply_labels(pipeline_dir: Path, labels_path: Optional[Path] = None) -> Path:
    lp = labels_path or (pipeline_dir / LABELS_NAME)
    if not lp.is_file():
        raise FileNotFoundError(f"没有 {lp.name}：先把 Agent 的判定写进去")
    labels = json.loads(lp.read_text("utf-8"))
    tr = json.loads((pipeline_dir / "02_transcript.json").read_text("utf-8"))
    by_id = {str(s.get("id")): s for s in (tr.get("segments") or [])}
    out_lines: List[Dict[str, Any]] = []
    for item in labels.get("lines") or []:
        if item.get("keep") is False:
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        ids = [str(x) for x in (item.get("segs") or [])]
        segs = [by_id[i] for i in ids if i in by_id]
        if not segs:
            print(f"[polish] [WARN] 这一行找不到对应转写段，跳过：{text[:24]}")
            continue
        srcs = {str(s.get("source_media") or "") for s in segs}
        if len(srcs) > 1:
            # 一行只能来自同一段原声：跨文件合并会把两段不相干的画面剪在一起（时间跨度还会翻倍）
            print(f"[polish] [WARN] 这一行跨了 {len(srcs)} 个素材来源，已拆分保留最长的一段：{text[:20]}")
            segs = [max(segs, key=lambda x: float(x.get("end_sec", 0)) - float(x.get("start_sec", 0)))]
        start = min(float(s.get("start_sec", 0)) for s in segs)
        end = max(float(s.get("end_sec", 0)) for s in segs)
        # 两个时长分开记：`dur` = 真正说了多久（各段相加），`span` = 从哪到哪（含中间停顿）。
        # 只给 span 会让人以为一句话 36 秒，其实中间有大段静音/走位。
        speech = sum(max(0.0, float(x.get("end_sec", 0)) - float(x.get("start_sec", 0))) for x in segs)
        out_lines.append({
            "text": text,
            "start_sec": round(start, 3),
            "end_sec": round(end, 3),
            "dur": round(speech, 3),
            "span": round(end - start, 3),
            "segs": [str(x.get("id")) for x in segs],
            "note": str(item.get("note") or "").strip(),
            "source": Path(str(segs[0].get("source_media") or "")).name,
        })
    out_lines.sort(key=lambda x: (x["source"], x["start_sec"]))
    doc = {
        "step": "script_final",
        "engine": "agent_polished",
        "lines": out_lines,
        "total_lines": len(out_lines),
        "total_dur": round(sum(x["dur"] for x in out_lines), 2),
        "total_span": round(sum(x["span"] for x in out_lines), 2),
        "notes": [{"text": x["text"], "note": x["note"]} for x in out_lines if x["note"]],
    }
    dst = pipeline_dir / FINAL_NAME
    dst.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[polish] 成稿 -> {dst}（{len(out_lines)} 句 / {doc['total_dur']} 秒）")
    if doc["notes"]:
        print(f"[polish] 有 {len(doc['notes'])} 处需要你核一下（见确认单「待核对」）")
    return dst


def main() -> None:
    ap = argparse.ArgumentParser(description="文案整理：ASR 逐字稿 → 能上屏的一行一句")
    ap.add_argument("--pipeline-dir", required=True)
    ap.add_argument("--tasks", action="store_true", help="生成 Agent 任务清单")
    ap.add_argument("--apply", action="store_true", help="把 Agent 判定落成 02c_script_final.json")
    ap.add_argument("--labels", default="", help="判定文件路径（默认 <pipeline>/02b_polish_labels.json）")
    a = ap.parse_args()
    pd = Path(a.pipeline_dir).expanduser()
    if a.apply:
        apply_labels(pd, Path(a.labels) if a.labels else None)
        return
    build_tasks(pd)


if __name__ == "__main__":
    main()

def plan_lines(pipeline_dir: Path) -> List[Dict[str, Any]]:
    """读 02c 成稿，返回「一行 = 一句要上屏的话」的顺序清单。

    下剪刀的地方（step3）必须认这份，否则砍掉的句子还会被剪进去。
    每项：{"text": 上屏文字, "segs": [原始转写段 id, ...]}
    """
    p = Path(pipeline_dir) / FINAL_NAME
    if not p.is_file():
        return []
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []
    out: List[Dict[str, Any]] = []
    for line in doc.get("lines") or []:
        text = str((line or {}).get("text") or "").strip()
        segs = [str(x) for x in ((line or {}).get("segs") or [])]
        if text and segs:
            out.append({"text": text, "segs": segs})
    return out
