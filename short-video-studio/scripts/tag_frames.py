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
    ap = argparse.ArgumentParser(description="抽密帧 + 多模态识别，产出《素材说明》草稿")
    ap.add_argument("--input-dir", required=True, help="素材目录")
    ap.add_argument("--out", required=True, help="输出目录（建议放在 _SVS工作区/ 或项目目录下）")
    ap.add_argument("--frames-per-clip", type=int, default=6, help="每段抽几帧（默认 6，长的建议 10–12）")
    ap.add_argument("--workers", type=int, default=4, help="并发线程数（默认 4）")
    ap.add_argument("--vlm", action="store_true",
                    help="额外用视觉语言模型描述画面（需要 SVS_VLM_ENDPOINT，本地 Ollama 也行）")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 段（试跑用）")
    args = ap.parse_args()

    from vision_scene_labels import labels_to_tags, tag_groups
    from vision_inspector import inspect_images

    src_dir = Path(args.input_dir).expanduser()
    out_dir = Path(args.out).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    files = _collect(src_dir)
    if args.limit:
        files = files[: args.limit]
    if not files:
        raise SystemExit(f"没找到素材：{src_dir}")
    ffmpeg = find_bin("ffmpeg")
    if not ffmpeg:
        raise SystemExit("没找到 ffmpeg")

    print(f"[tag-frames] {len(files)} 段素材 · 每段 {args.frames_per_clip} 帧 · 并发 {args.workers}")

    def work(item: tuple) -> Dict[str, Any]:
        idx, src = item
        probe = ffprobe_media(src) or {}
        dur = float(probe.get("duration_sec") or 0.0)
        with tempfile.TemporaryDirectory(prefix="svs_frames_") as td:
            frames = _extract_frames(ffmpeg, src, dur, args.frames_per_clip, Path(td))
            if not frames:
                return {"file": src.name, "path": str(src), "frames": 0, "error": "抽帧失败"}
            try:
                vis = inspect_images(frames)
            except Exception as exc:
                vis = {}
                note = f"视觉识别不可用：{type(exc).__name__}"
            else:
                note = ""
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
        desc: Optional[str] = None
        if args.vlm:
            cfg = _vlm_config()
            desc = _vlm_describe(cfg, frames, src.name)
            if desc is None and not cfg.get("endpoint"):
                note = (note + " " if note else "") + "vlm: not_configured"
        sentence_parts = [desc] if desc else []
        sentence_parts += tags[:4]
        sentence_parts += [t[:12] for t in ocr[:3]]
        sentence = "、".join([x for x in sentence_parts if x])[:60]
        return {
            "file": src.name,
            "path": str(src),
            "duration_sec": round(dur, 2),
            "frames": len(frames),
            "scene_tags_zh": tags,
            "scene_tag_groups": tag_groups(tags),
            "label_evidence": evidence[:10],
            "ocr_texts": ocr[:8],
            "vlm_description": desc,
            "suggested_note": sentence,
            "note": note,
        }

    results: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        for i, r in enumerate(ex.map(work, list(enumerate(files))), start=1):
            results.append(r)
            if i % 20 == 0 or i == len(files):
                print(f"[tag-frames] {i}/{len(files)}")

    write_json(out_dir / "00_frame_semantics.json", {
        "step": "frame_semantics",
        "input_dir": str(src_dir.resolve()),
        "frames_per_clip": args.frames_per_clip,
        "backends": {
            "vision_classify": "apple_vision" if sys.platform == "darwin" else "unavailable",
            "ocr": "apple_vision" if sys.platform == "darwin" else "unavailable",
            "vlm": (_vlm_config()["model"] if (_vlm_config()["endpoint"] and args.vlm) else "not_configured"),
        },
        "clips": results,
    })

    # 直接产出一份能用的《素材说明》草稿：文件名 + 一句画面内容
    lines = ["# 一行一条：文件名 + 一句画面内容（下面是自动识别出来的草稿，改两个字就准了）"]
    filled = 0
    for r in results:
        note = r.get("suggested_note") or ""
        if note:
            filled += 1
        lines.append(f"{r['file']} {note}".rstrip())
    note_path = out_dir / "素材说明_待填写.txt"
    note_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with_tags = sum(1 for r in results if r.get("scene_tags_zh"))
    with_ocr = sum(1 for r in results if r.get("ocr_texts"))
    with_vlm = sum(1 for r in results if r.get("vlm_description"))
    print(f"[tag-frames] 识别出画面内容 {with_tags}/{len(results)} 段 · 有画面文字 {with_ocr} 段"
          f" · 视觉模型描述 {with_vlm} 段")
    print(f"[tag-frames] 识别结果 -> {(out_dir / '00_frame_semantics.json')}")
    print(f"[tag-frames] 说明草稿 -> {note_path}（{filled}/{len(results)} 段已有初稿）")


if __name__ == "__main__":
    main()
