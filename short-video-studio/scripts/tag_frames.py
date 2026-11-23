#!/usr/bin/env python3
"""
给素材逐段抽密帧 + 多模态识别，直接产出一份能用的《素材说明》草稿。

为什么要有这个脚本
──────────────────
用户实测最痛的一条：「所有素材画面一定要抽密帧走多模态 AI 识别，要根治配错镜问题」。
`broll_semantics` 只能靠文字线索（文件名/文件夹名/OCR/手写说明），iPhone 原始命名下这些全空，
于是句子和画面永远对不上。这个脚本把「画面里是什么」变成可以检索的文字。

三层识别，能跑哪层跑哪层，每层都在结果里标明来源（不假装）
──────────────────────────────────────────────────────────
1. **本地视觉分类**（Apple Vision / ImageNet 级，零联网零 token）：沙发/椅子/后厨/汽车/工地…，
   经 `vision_scene_labels` 翻成中文。macOS 有，其它平台会自动跳过并说明。
2. **本地 OCR**（Apple Vision 文字识别）：门头、价目表、包装上的字——这类信息最值钱。
   非 macOS 上自动改用 ffmpeg 抽帧 + 不做 OCR，结果里标明 `ocr: unavailable`。
3. **视觉语言模型（可选）**：一句话说清"这段画面在讲什么"。两种接法都支持，用环境变量切换：
     - 本地：`SVS_VLM_ENDPOINT=http://127.0.0.1:11434/v1`（Ollama / LM Studio / vLLM 都行）
     - 云端：`SVS_VLM_ENDPOINT=https://...` + `SVS_VLM_API_KEY=...`
   模型名用 `SVS_VLM_MODEL`（默认 `qwen2.5vl:7b`）。**没配就不猜**，结果里写 `vlm: not_configured`。

产物
────
  <out>/00_frame_semantics.json          逐段逐帧的识别结果（含证据：标签 + 置信度 + 帧时间）
  <out>/素材说明_待填写.txt               直接可用的说明草稿：`文件名 + 一句画面内容`，人工改两个字就能用

用法
────
  python3 scripts/tag_frames.py --input-dir <素材目录> --out <工作目录>
  python3 scripts/tag_frames.py --input-dir <素材目录> --out <工作目录> --frames-per-clip 12 --vlm
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional

SCRIPTS_ROOT = Path(__file__).resolve().parent
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from common import (ALL_MEDIA_EXTS, IMAGE_EXTS, ensure_image_decodable, ffprobe_media, find_bin,
                    maybe_reexec_in_venv, write_json)

if __name__ == "__main__" or str(sys.argv[0]).endswith(".py"):
    maybe_reexec_in_venv()

def _collect(root: Path) -> List[Path]:
    out: List[Path] = []
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.suffix.lower() in ALL_MEDIA_EXTS:
            out.append(p)
    return out


def _frame_times(dur: float, n: int) -> List[float]:
    if dur <= 0:
        return [0.0]
    n = max(1, min(int(n), 24))
    return [round(min(dur - 0.05, max(0.0, dur * (i + 0.5) / n)), 3) for i in range(n)]


def _extract_frames(ffmpeg: str, src: Path, dur: float, n: int, td: Path) -> List[Path]:
    if src.suffix.lower() in IMAGE_EXTS:
        # iPhone 的 HEIC 先转成可读的 jpg 影子文件（源文件不动）
        dec = ensure_image_decodable(src, cache_dir=td / "decoded")
        return [dec] if dec else []
    outs: List[Path] = []
    for k, ts in enumerate(_frame_times(dur, n)):
        dst = td / f"{src.stem[:40]}_{k:02d}.jpg"
        cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{ts:.2f}", "-i", str(src),
               "-frames:v", "1", "-q:v", "3", str(dst)]
        try:
            subprocess.run(cmd, capture_output=True, timeout=120, check=False)
        except Exception:
            continue
        if dst.exists():
            outs.append(dst)
    return outs


def _vlm_config() -> Dict[str, str]:
    return {
        "endpoint": str(os.environ.get("SVS_VLM_ENDPOINT") or "").strip(),
        "model": str(os.environ.get("SVS_VLM_MODEL") or "qwen2.5vl:7b").strip(),
        "key": str(os.environ.get("SVS_VLM_API_KEY") or "").strip(),
    }


def _vlm_describe(cfg: Dict[str, str], images: List[Path], hint: str) -> Optional[str]:
    """用 OpenAI 兼容的 /chat/completions 让视觉模型说一句画面内容（本地 Ollama 也是这个协议）。"""
    if not cfg.get("endpoint") or not images:
        return None
    try:
        import base64
        import urllib.request
    except Exception:
        return None
    content: List[Dict[str, Any]] = [{
        "type": "text",
        "text": ("用一句不超过 25 字的中文，说清这几帧画面里有什么（人/物/场景/动作），"
                 "只描述看得见的，不要猜。素材文件名：" + hint),
    }]
    for p in images[:3]:
        try:
            b64 = base64.b64encode(Path(p).read_bytes()).decode("ascii")
        except Exception:
            continue
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
    body = json.dumps({
        "model": cfg["model"],
        "messages": [{"role": "user", "content": content}],
        "temperature": 0.2,
        "max_tokens": 80,
    }).encode("utf-8")
    url = cfg["endpoint"].rstrip("/") + "/chat/completions"
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    if cfg.get("key"):
        req.add_header("Authorization", f"Bearer {cfg['key']}")
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            doc = json.loads(resp.read().decode("utf-8"))
        msg = (doc.get("choices") or [{}])[0].get("message") or {}
        txt = str(msg.get("content") or "").strip()
        return re.sub(r"\s+", "", txt)[:60] or None
    except Exception as exc:
        print(f"[tag-frames] [WARN] VLM 调用失败（{type(exc).__name__}）：{exc}")
        return None


def main() -> None:
    ap = argparse.ArgumentParser(description="抽密帧 + 交大模型判断画面（默认不本地猜）")
    ap.add_argument("--input-dir", help="素材目录")
    ap.add_argument("--out", required=True, help="输出目录（建议放在 _SVS工作区/ 或项目目录下）")
    ap.add_argument("--engine", choices=["agent", "local"], default="agent",
                    help="agent（默认）=抽帧后交大模型/Agent 判定；local=本地分类兜底（需 --i-accept-local）")
    ap.add_argument("--i-accept-local", action="store_true",
                    help="明确同意用本地分类兜底（结果会打降级标记，且不知道画面在讲什么）")
    ap.add_argument("--apply", action="store_true",
                    help="把 00_vision_labels.json 里大模型的判定并进结果与《素材说明》草稿")
    ap.add_argument("--labels", default="", help="判定文件路径（默认 <out>/00_vision_labels.json）")
    ap.add_argument("--frames-per-clip", type=int, default=6, help="每段抽几帧（默认 6，长的建议 10–12）")
    ap.add_argument("--workers", type=int, default=0, help="并发线程数（默认按机器负载自适应）")
    ap.add_argument("--vlm", action="store_true", help="local 模式下额外用 VLM 描述（需 SVS_VLM_ENDPOINT）")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 段（试跑用）")
    args = ap.parse_args()

    out_dir = Path(args.out).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    import vision_agent as VA

    if args.apply:
        rep = VA.merge_labels(out_dir, VA.load_labels(out_dir, Path(args.labels) if args.labels else None) or None)
        print(f"[tag-frames] 大模型判定已并入：{rep['clips']}/{rep['total']} 段")
        if rep["missing"]:
            print(f"[tag-frames] 还有 {len(rep['missing'])} 段没判定（配镜时会被当成没依据）："
                  + "、".join(rep["missing"][:5]) + ("…" if len(rep["missing"]) > 5 else ""))
        print(f"[tag-frames] 结果 -> {out_dir / VA.SEMANTICS_NAME}")
        print(f"[tag-frames] 说明草稿 -> {out_dir / VA.NOTES_NAME}")
        return

    if args.engine == "local" and not args.i_accept_local:
        print(VA.LOCAL_VISION_REFUSAL, file=sys.stderr)
        raise SystemExit(3)
    if not args.input_dir:
        ap.error("--input-dir 必填（--apply 模式除外）")

    from vision_inspector import inspect_images

    src_dir = Path(args.input_dir).expanduser()
    files = _collect(src_dir)
    if args.limit:
        files = files[: args.limit]
    if not files:
        raise SystemExit(f"没找到素材：{src_dir}")
    ffmpeg = find_bin("ffmpeg")
    if not ffmpeg:
        raise SystemExit("没找到 ffmpeg")

    workers = args.workers or _adaptive_workers(len(files))
    print(f"[tag-frames] {len(files)} 段素材 · 每段 {args.frames_per_clip} 帧 · "
          f"并发 {workers}（按当前机器负载算的，不抢你正在用的程序）· 引擎 {args.engine}")

    frames_root = out_dir / "frames"

    def work(item: tuple) -> Dict[str, Any]:
        idx, src = item
        probe = ffprobe_media(src) or {}
        dur = float(probe.get("duration_sec") or 0.0)
        keep = frames_root / f"{idx:03d}_{VA.slug_of(src.name)}"
        keep.mkdir(parents=True, exist_ok=True)
        frames = _extract_frames(ffmpeg, src, dur, args.frames_per_clip, keep)
        if not frames:
            return {"file": src.name, "path": str(src), "duration_sec": round(dur, 2),
                    "frames": 0, "error": "抽帧失败"}
        base = {
            "file": src.name,
            "path": str(src),
            "duration_sec": round(dur, 2),
            "frames": len(frames),
            "frame_paths": [{"path": str(p), "t": 0.0} for p in frames],
            "frames_dir": str(keep),
        }
        return base

    results: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        for i, r in enumerate(ex.map(work, list(enumerate(files))), start=1):
            results.append(r)
            if i % 20 == 0 or i == len(files):
                print(f"[tag-frames] 抽帧 {i}/{len(files)}")

    # 帧的时间点用文件名里的序号反推（_extract_frames 按等分时间抽）
    for r in results:
        d = float(r.get("duration_sec") or 0.0)
        n = len(r.get("frame_paths") or [])
        if n:
            for k, fd in enumerate(r["frame_paths"]):
                fd["t"] = round(d * (k + 0.5) / n, 2)

    if args.engine == "agent":
        tasks_p = VA.write_tasks(out_dir, results)
        write_json(out_dir / VA.SEMANTICS_NAME, {
            "step": "frame_semantics",
            "engine": "agent_pending",
            "input_dir": str(src_dir.resolve()),
            "frames_per_clip": args.frames_per_clip,
            "clips": [{k: v for k, v in r.items() if k != "frame_paths"} for r in results],
        })
        notes = ["# 一行一条：文件名 + 一句画面内容（等大模型判定后自动填，别手改也行）"]
        notes += [f"{r['file']}  （待 Agent 判定）" for r in results]
        (out_dir / VA.NOTES_NAME).write_text("\n".join(notes) + "\n", encoding="utf-8")
        print(f"[tag-frames] 已抽 {sum(int(r.get('frames') or 0) for r in results)} 帧 -> {frames_root}")
        print(f"[tag-frames] 任务包 -> {tasks_p}")
        print("[tag-frames] 下一步：把帧交给 Agent/大模型判，把结论写进 "
              f"{out_dir / VA.LABELS_NAME}，然后跑 --apply --out {out_dir}")
        print("[tag-frames] 在你判定之前，配镜会明确标成『没有画面依据』，不会偷偷用文件名瞎配")
        return

    # 本地兜底：用户明确同意过，结果打标记
    from vision_scene_labels import labels_to_tags, tag_groups
    for r in results:
        frames = [Path(x["path"]) for x in (r.get("frame_paths") or [])]
        try:
            vis = inspect_images(frames) if frames else {}
        except Exception as exc:
            vis, r["note"] = {}, f"视觉识别不可用：{type(exc).__name__}"
        labels: List[Dict[str, Any]] = []
        ocr: List[str] = []
        for f in frames:
            v = vis.get(str(f)) or {}
            labels.extend(v.get("labels") or [])
            for t in v.get("texts") or []:
                s = str((t or {}).get("text") or "").strip()
                if s and s not in ocr:
                    ocr.append(s)
        tags, evidence = labels_to_tags(labels)
        desc = None
        if args.vlm:
            cfg = _vlm_config()
            desc = _vlm_describe(cfg, frames, r["file"])
            if desc is None and not cfg.get("endpoint"):
                r["note"] = (str(r.get("note") or "") + " vlm: not_configured").strip()
        r.update({
            "vision_engine": "user_accepted_local_fallback",
            "scene_tags_zh": tags,
            "scene_tag_groups": tag_groups(tags),
            "label_evidence": evidence[:10],
            "ocr_texts": ocr[:8],
            "vlm_description": desc,
            "suggested_note": "、".join([x for x in ([desc] if desc else []) + tags[:4] + ocr[:3] if x])[:60],
        })

    write_json(out_dir / VA.SEMANTICS_NAME, {
        "step": "frame_semantics",
        "engine": "local_fallback",
        "engine_ack": "user_accepted_local_fallback",
        "warning": "本地分类只给物件级标签，不知道画面在讲什么；这不是大模型理解结果。",
        "input_dir": str(src_dir.resolve()),
        "clips": [{k: v for k, v in r.items() if k != "frame_paths"} for r in results],
    })
    lines = ["# 一行一条：文件名 + 一句画面内容（本地兜底标签，只作临时参考）"]
    for r in results:
        lines.append(f"{r['file']} {r.get('suggested_note') or ''}".rstrip())
    (out_dir / VA.NOTES_NAME).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("[tag-frames] 已按你的要求走本地兜底（结果打了降级标记）")
    print(f"[tag-frames] 结果 -> {out_dir / VA.SEMANTICS_NAME}")


def _adaptive_workers(n_items: int) -> int:
    """并发数按机器现状算，别把用户正在用的程序一起拖垮。"""
    try:
        from common import adaptive_workers
        return adaptive_workers(n_items, cap=8)
    except Exception:
        return max(1, min(4, os.cpu_count() or 2))


if __name__ == "__main__":
    main()
