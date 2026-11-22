#!/usr/bin/env python3
"""
[外来画面 ↔ 口播文案 语义对位审计]

用法：
  1) 复现实测（默认）：造一套"真实手机素材"（文件名是 IMG_xxxx，标签走 inventory_media 的真实推断），
     跑一遍剪辑表，逐镜量「这个 B-roll 和它压着的那句话到底有没有关系」。
       python dev_tools/audit_broll_semantics.py --demo
       python dev_tools/audit_broll_semantics.py --demo --legacy     # 旧口径对照
  2) 审自己的成片：给一个跑过 step4 的 pipeline 目录
       python dev_tools/audit_broll_semantics.py --pipeline-dir <dir>
  --check 时命中率低于门槛直接非零退出（进 CI）。
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "short-video-studio" / "scripts"
for p in (str(SCRIPTS), str(REPO)):
    if p not in sys.path:
        sys.path.insert(0, p)

from common import maybe_reexec_in_venv  # noqa: E402

if __name__ == "__main__":
    maybe_reexec_in_venv()

from broll_semantics import evaluate_edl, legacy_score, relate, summarize  # noqa: E402

REPORT_CSV = REPO / "reports" / "broll_semantics_audit.csv"

# 一套"真实手机素材"：文件名毫无语义，只有拍摄时长/稳定性差异
_REAL_POOL = [
    ("IMG_4821.MOV", 8.0, 0.92, 46.0, "gray"),
    ("IMG_4822.MOV", 6.0, 0.90, 44.0, "navy"),
    ("IMG_4830.MOV", 9.0, 0.88, 47.0, "teal"),
    ("VID_20240312_104522.mp4", 7.0, 0.86, 43.0, "purple"),
    ("DSC09931.MOV", 5.5, 0.84, 41.0, "olive"),
]

# 一段真实口播（烧烤门店），keywords 走 step2 的真实口径
_SENTENCES = [
    ("价格越高的菜，利润反而越低。", ["价格越高", "利润"]),
    ("清晨市场带回的鲜羊排，成本花了三千块。", ["鲜羊排", "三千块"]),
    ("看起来卖四百能赚一百多，", ["卖四百"]),
    ("其实要腌两小时，炭火再烤三小时。", ["两小时", "三小时"]),
    ("开了八年烧烤店，绝不用冻肉糊弄人。", ["八年", "绝不用冻肉"]),
    ("后厨每道工序我都站在旁边看着，", ["后厨", "工序"]),
    ("想吃鲜嫩烤羊排，评论区发你门店地址。", ["鲜嫩", "门店地址"]),
]


def _make_video(path: Path, dur: float, color: str) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c={color}:s=1080x1920:r=30:d={dur}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
        check=True,
    )


# 用户手写一行说明时的样子（真实门店老板能写出来的话）
_NOTES = {
    "IMG_4821.MOV": "后厨现切鲜羊排，刀工特写",
    "IMG_4822.MOV": "炭火烤羊排，后厨火候",
    "IMG_4830.MOV": "门店门头与大厅，客人到店",
    "VID_20240312_104522.mp4": "账本与进货单据，成本明细",
    "DSC09931.MOV": "员工腌肉备料，工序流程",
}


def build_demo(root: Path, with_notes: bool = False) -> Path:
    from inventory_media import _infer_semantic_tags, load_media_notes  # 真实推断口径

    aroll = root / "aroll.mp4"
    _make_video(aroll, 20.0, "gray")
    items: List[Dict[str, Any]] = [{
        "id": "ar1", "role": "a_roll", "normalized_mp4": str(aroll), "duration_sec": 20.0,
        "video_grade": "usable", "sharpness_score": 45.0, "stability_score": 0.9,
        "semantic_tags": ["真人出镜"], "manual_tags": [],
    }]
    tags_by_name: Dict[str, List[str]] = {}
    for i, (name, dur, stab, sharp, color) in enumerate(_REAL_POOL, start=1):
        mp4 = root / name
        _make_video(mp4, dur, color)
        # 手机素材真实推断：无中文文件名、无 OCR → 只剩画面描述
        tags = _infer_semantic_tags(name, {"faces": [], "texts": []}, 120.0)
        tags_by_name[name] = tags
        items.append({
            "id": f"br{i}", "role": "b_roll", "normalized_mp4": str(mp4), "duration_sec": dur,
            "video_grade": "usable", "sharpness_score": sharp, "stability_score": stab,
            "semantic_tags": tags, "manual_tags": [], "description": "",
            "best_in_sec": 0.4, "shot_size": "medium", "camera_motion": "static",
        })

    sents = []
    evts = []
    t = 0.0
    for i, (text, kws) in enumerate(_SENTENCES, start=1):
        dur = round(1.8 + 0.2 * (i % 3), 2)
        sents.append({
            "id": f"s{i}", "text": text, "keywords": kws, "start_sec": round(t, 2),
            "end_sec": round(t + dur, 2), "source_media": str(aroll),
            "source_in_sec": round(t, 2), "source_out_sec": round(t + dur, 2),
        })
        evts.append({"id": f"evt_{i:03d}", "type": "sentence_start", "sentence_id": f"s{i}",
                     "time_sec": round(t, 2), "frame": int(round(t * 30))})
        t += dur
    if with_notes:
        (root / "素材说明.txt").write_text(
            "\n".join(f"{k} {v}" for k, v in _NOTES.items()) + "\n", encoding="utf-8")
        notes = load_media_notes(root)
        for it in items:
            note = notes.get(Path(str(it["normalized_mp4"])).name) or ""
            if note:
                it["description"] = note
                it["manual_tags"] = [note]
        print("[demo] 已提供素材说明.txt（一行一条），素材侧这才有了主题词：")
        for it in items[1:]:
            print(f"   {Path(it['normalized_mp4']).name:<28} -> {it['description']}")

    pipe = root / "demo_pipeline"
    pipe.mkdir(parents=True, exist_ok=True)
    (pipe / "01_manifest.json").write_text(json.dumps({"items": items}, ensure_ascii=False), encoding="utf-8")
    (pipe / "03_audio_events.json").write_text(
        json.dumps({"locked_duration_sec": round(t, 2), "locked_duration_frames": int(round(t * 30)),
                    "sentences": sents, "events": evts}, ensure_ascii=False), encoding="utf-8")
    print("[demo] 素材侧真实推断标签（这就是过去\"对不上\"的根源）：")
    for name, tags in tags_by_name.items():
        print(f"   {name:<28} -> {tags}")
    return pipe


def audit(pipe: Path, style_id: str, track: str) -> List[Dict[str, Any]]:
    from step4_build_edl import run_step4

    edl = run_step4(pipe, track=track, style_id=style_id, edit_profile="documentary")
    sents = {}
    ev = json.loads((pipe / "03_audio_events.json").read_text(encoding="utf-8"))
    for s in ev.get("sentences") or []:
        sents[str(s.get("id"))] = s
    # 旧口径对照：同一批镜头用字面 tag 命中数评估
    rows = evaluate_edl(edl, sents)
    manifest = {str(it.get("id")): it for it in (json.loads((pipe / "01_manifest.json").read_text(encoding="utf-8")).get("items") or [])}
    clips = {str(c.get("id")): c for c in edl.get("clips") or []}
    for r in rows:
        c = clips.get(r["clip_id"]) or {}
        sent = sents.get(str(c.get("sentence_id")) or "") or {}
        item = manifest.get(str(c.get("manifest_id") or c.get("media_id") or "")) or c
        r["legacy_tag_hits"] = legacy_score(str(r["sentence"]), {**item, **c}, sent.get("keywords"))
        r["has_manual_signal"] = bool(str(c.get("sem_match_basis") or ""))
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description="B-roll ↔ 口播文案语义对位审计")
    ap.add_argument("--demo", action="store_true", help="用真实手机素材口径复现实测")
    ap.add_argument("--pipeline-dir", default="", help="已跑过 step4 的 pipeline 目录")
    ap.add_argument("--style", default="style_21")
    ap.add_argument("--track", default="store")
    ap.add_argument("--legacy", action="store_true", help="强制旧口径（字面 tag 命中）选片，用于对照")
    ap.add_argument("--with-notes", action="store_true", help="demo 里模拟用户给了「素材说明.txt」")
    ap.add_argument("--check", action="store_true", help="命中率不达标则非零退出")
    ap.add_argument("--min-hit", type=float, default=0.60)
    args = ap.parse_args()

    if args.legacy:
        os.environ["SVS_LEGACY_SEM_MATCH"] = "1"

    tmp_holder: List[tempfile.TemporaryDirectory] = []
    if args.pipeline_dir:
        pipe = Path(args.pipeline_dir).expanduser().resolve()
    else:
        td = tempfile.TemporaryDirectory(prefix="svs_broll_audit_")
        tmp_holder.append(td)
        pipe = build_demo(Path(td.name), with_notes=args.with_notes)

    rows = audit(pipe, args.style, args.track)
    stat = summarize(rows)

    print(f"\n[审计] {pipe}  style={args.style}  {'旧口径' if args.legacy else '概念桥'}{'  +素材说明' if args.with_notes else ''}")
    print(f"{'镜头':<10}{'判定':<7}{'分':<7}{'台词':<34}{'素材':<26}依据")
    for r in rows:
        sent = (r["sentence"] or "")[:16]
        print(f"{str(r['clip_id'])[:9]:<10}{r['verdict']:<7}{r['score']:<7}{sent:<34}{str(r['media'])[:24]:<26}{r['basis']}")

    print(f"\n[结果] 外来画面 {stat['clips']} 个：命中 {stat['hit']} / 沾边 {stat['weak']} / 无对应 {stat['none']}"
          f"  命中率 {stat['hit_ratio']*100:.1f}%  含沾边 {stat['aligned_ratio']*100:.1f}%")
    if rows:
        legacy_hit = sum(1 for r in rows if r["legacy_tag_hits"] > 0)
        print(f"[对照] 旧口径（字面 tag 命中>0）在这批镜头上：{legacy_hit}/{len(rows)} = {legacy_hit/len(rows)*100:.1f}%")

    REPORT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with REPORT_CSV.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["clip_id", "role", "sentence", "media", "verdict", "score", "basis", "legacy_tag_hits", "has_manual_signal"])
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in w.fieldnames})
    print(f"[CSV] {REPORT_CSV.relative_to(REPO)}")

    ok = stat["hit_ratio"] >= args.min_hit
    print(f"[门槛] 命中率 ≥ {args.min_hit*100:.0f}% → {'PASS' if ok else 'FAIL'}")
    if args.check and not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
