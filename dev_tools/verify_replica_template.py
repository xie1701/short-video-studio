#!/usr/bin/env python3
"""复刻模版端到端验证：真草稿 → 提模版 → 换新素材/新文案 → 出原生草稿 → 渲出片。

链路（全部真跑，不 mock）：

  1. 按 `--style-id` 在风格库里查 `source_hash`，在明文草稿快照目录里**按哈希**定位草稿
     （代码里不写草稿名，输出里也只打脱敏哈希）；
  2. `replica_template.extract_template()` 提结构模版；
  3. 现场用 ffmpeg 造几段 3~5s **不同内容**的新素材（testsrc2/分形/细胞自动机/色条/缩放…），
     另造一条 BGM，作为 `assets`；
  4. `apply_replica_template.apply_template()` 套模版 → 新草稿 JSON（可编辑原生草稿）；
  5. 用 `dev_tools/render_replica_from_draft.py` 的 `render_timeline()`（**只读调用，不改**）
     把新草稿渲成 mp4；
  6. 报告：新片时长/分段数/每段角色/`asset_reused`/`asset_wrapped`/`slot_shrunk`/`unknown` 素材数/渲染是否成功。

用法：
    python3 dev_tools/verify_replica_template.py                  # 默认 style_21
    python3 dev_tools/verify_replica_template.py --no-render      # 只到草稿，不渲
    python3 dev_tools/verify_replica_template.py --draft <草稿目录> --assets-json <素材.json>
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "short-video-studio" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT / "dev_tools"))

from common import maybe_reexec_in_venv  # noqa: E402

maybe_reexec_in_venv()

from replica_template import extract_template, resolve_draft_by_style, summarize  # noqa: E402
from apply_replica_template import apply_template  # noqa: E402

DEFAULT_TEXTS = [
    "别再一条一条瞎剪了",
    "一条片子其实只有三件事",
    "第一件 把节奏钉死",
    "第二件 让字站在正确的位置",
    "第三件 换素材也不跑版",
    "我们把这套结构抽成了模版",
    "镜头长度 关键帧 动画 全在里面",
    "换一批素材",
    "换一版文案",
    "直接套上去",
    "一分钟出一条结构一样的片子",
    "门店老板也能自己出片",
]

# 新素材生成配方：内容各不相同，时长都在 3~5s（用户要求真视频，不是纯色占位）
MEDIA_RECIPES = [
    ("a1_motion_bars", 3.6, "testsrc2=size=1080x1920:rate=30,noise=alls=8:allf=t", None),
    ("a2_cells", 3.2, "cellauto=size=540x960:rate=30:rule=110:random_fill_ratio=0.35",
     "scale=1080:1920,format=yuv420p"),
    ("a3_zoom_rgb", 4.0, "rgbtestsrc=size=1080x1920:rate=30",
     "zoompan=z='min(zoom+0.002,1.4)':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1080x1920"),
    ("b1_fractal", 4.2, "mandelbrot=size=540x960:rate=30", "scale=1080:1920,hue=h=40:s=1.6"),
    ("b2_life", 3.4, "life=size=540x960:rate=30:mold=10:ratio=0.25:death_color=#1b2b4b:life_color=#f3e7a2",
     "scale=1080:1920,format=yuv420p"),
    ("b3_bars", 3.0, "smptebars=size=1080x1920:rate=30", "rotate=0.02:fillcolor=black,noise=alls=12:allf=t"),
]


def _rel(p: Any) -> str:
    """输出路径统一相对当前目录（不把家目录写进报告）。"""
    try:
        return str(Path(p).resolve().relative_to(Path.cwd()))
    except Exception:
        return str(p)


def _run(cmd: List[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"命令失败: {' '.join(cmd[:3])}...\n{proc.stderr[-800:]}")


def build_placeholder_media(media_dir: Path) -> Dict[str, List[str]]:
    """现场造新素材（幂等：已存在就复用）。全是真视频，内容各不相同。"""
    media_dir.mkdir(parents=True, exist_ok=True)
    made: Dict[str, List[str]] = {"a_roll": [], "b_roll": [], "bgm": []}
    for name, dur, src, vf in MEDIA_RECIPES:
        out = media_dir / f"{name}.mp4"
        if not out.is_file():
            cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", src]
            if vf:
                cmd += ["-vf", vf]
            cmd += ["-t", f"{dur}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                    "-pix_fmt", "yuv420p", "-an", str(out)]
            _run(cmd)
        made["a_roll" if name.startswith("a") else "b_roll"].append(str(out))
    bgm = media_dir / "bgm_bed.m4a"
    if not bgm.is_file():
        _run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
              "-i", "sine=frequency=220:sample_rate=48000,volume=0.22",
              "-t", "20", "-c:a", "aac", "-b:a", "128k", str(bgm)])
    made["bgm"].append(str(bgm))
    return made


def probe_mp4(path: Path) -> dict:
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height,nb_frames,r_frame_rate",
             "-show_entries", "format=duration", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=120)
        data = json.loads(proc.stdout or "{}")
        st = (data.get("streams") or [{}])[0]
        return {
            "duration_sec": round(float((data.get("format") or {}).get("duration") or 0.0), 3),
            "width": st.get("width"), "height": st.get("height"),
            "fps": st.get("r_frame_rate"),
            "frames": int(st.get("nb_frames") or 0),
            "bytes": path.stat().st_size if path.is_file() else 0,
        }
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    ap = argparse.ArgumentParser(description="复刻模版端到端验证（提取→套用→渲出片）")
    ap.add_argument("--style-id", default="style_21")
    ap.add_argument("--draft", default=None, help="草稿目录或 draft_content.json（优先于 --style-id）")
    ap.add_argument("--out", default="/tmp/replica_template_run")
    ap.add_argument("--assets-json", default=None)
    ap.add_argument("--texts-json", default=None)
    ap.add_argument("--no-render", action="store_true")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    report: Dict[str, Any] = {"style_id": args.style_id, "steps": {}}

    # ---- 1. 提模版 ----
    t0 = time.time()
    draft_path = Path(args.draft) if args.draft else resolve_draft_by_style(args.style_id)
    tpl = extract_template(draft_path)
    tpl_path = out / "template.json"
    tpl_path.write_text(json.dumps(tpl, ensure_ascii=False, indent=2), encoding="utf-8")
    report["steps"]["extract"] = {
        "source_ref": tpl["source_ref"],
        "summary": summarize(tpl),
        "template_json": _rel(tpl_path),
        "duration_sec": tpl["duration_sec"],
        "fps": tpl["fps"],
        "tracks": len(tpl["tracks"]),
        "segments": len(tpl["segments"]),
        "segments_by_role": _seg_roles(tpl["segments"]),
        "text_slots": len(tpl["text_slots"]),
        "asset_roles": tpl["counts"],
        "unknown_assets": tpl["counts"].get("unknown", 0),
        "unknown_items": tpl["unknown_items"],
        "requires_review": tpl["requires_review"],
        "elapsed_sec": round(time.time() - t0, 2),
    }

    # ---- 2. 新素材 / 新文案 ----
    if args.assets_json:
        assets = json.loads(Path(args.assets_json).read_text(encoding="utf-8"))
        assets_src = "given"
    else:
        assets = build_placeholder_media(out / "media")
        assets_src = "generated"
    texts: List[Any] = DEFAULT_TEXTS
    if args.texts_json:
        texts = json.loads(Path(args.texts_json).read_text(encoding="utf-8"))
    report["steps"]["assets"] = {
        "source": assets_src,
        "counts": {k: len(v) for k, v in assets.items()},
        "paths": {k: [_rel(p) for p in v] for k, v in assets.items()},
        "texts": len(texts),
    }

    # ---- 3. 套模版 ----
    t0 = time.time()
    apply_rep: Dict[str, Any] = {}
    draft_json = apply_template(tpl, assets, texts, out / "draft", apply_rep)
    report["steps"]["apply"] = {
        "draft_json": _rel(draft_json),
        "duration_sec": apply_rep.get("duration_sec"),
        "template_duration_sec": apply_rep.get("template_duration_sec"),
        "duration_delta_sec": apply_rep.get("duration_delta_sec"),
        "segments_total": apply_rep.get("segments_total"),
        "segments_by_role": apply_rep.get("segments_by_role"),
        "tracks_out": apply_rep.get("tracks_out"),
        "asset_reused": apply_rep.get("asset_reused"),
        "content_linked": apply_rep.get("content_linked", 0),
        "asset_wrapped": apply_rep.get("asset_wrapped"),
        "asset_fallback": apply_rep.get("asset_fallback"),
        "slot_shrunk": apply_rep.get("slot_shrunk"),
        "anim_clamped": apply_rep.get("anim_clamped"),
        "texts_used": apply_rep.get("texts_used"),
        "texts_truncated": apply_rep.get("texts_truncated"),
        "text_reused": apply_rep.get("text_reused"),
        "seg_clipped": apply_rep.get("seg_clipped", 0),
        "skipped": apply_rep.get("skipped"),
        "anchors_sample": (apply_rep.get("anchors") or [])[:5],
        "elapsed_sec": round(time.time() - t0, 2),
    }

    # ---- 4. 渲出片（只读调用复刻渲染器） ----
    render_rep: Dict[str, Any] = {}
    if not args.no_render:
        t0 = time.time()
        import importlib
        R = importlib.import_module("render_replica_from_draft")
        draft = json.loads(Path(draft_json).read_text(encoding="utf-8"))
        rr = {"style_id": args.style_id + "_template", "draft": "generated",
              "main_segments": 0, "overlay_segments": 0, "matte_done": 0, "matte_failed": 0,
              "text_segments": 0, "sticker_segments": 0, "masked_main_segments": 0,
              "masked_overlay_segments": 0, "compounds_rendered": [], "unrealized": {},
              "notes": []}
        try:
            mp4 = R.render_timeline(draft, Path(draft_json).parent, args.style_id + "_template",
                                    out / "render", int(round(float(tpl["fps"]))), False, rr, depth=0)
            info = probe_mp4(Path(mp4))
            render_rep = {
                "ok": True,
                "mp4": _rel(mp4),
                "probe": info,
                "main_segments": rr.get("main_segments"),
                "overlay_segments": rr.get("overlay_segments"),
                "text_segments": rr.get("text_segments"),
                "sticker_segments": rr.get("sticker_segments"),
                "masked_segments": (rr.get("masked_main_segments") or 0) + (rr.get("masked_overlay_segments") or 0),
                "unrealized": rr.get("unrealized"),
                "notes": rr.get("notes"),
                "elapsed_sec": round(time.time() - t0, 1),
            }
            (out / "render" / "replica_report.json").write_text(
                json.dumps(rr, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            render_rep = {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                          "elapsed_sec": round(time.time() - t0, 1)}
    report["steps"]["render"] = render_rep

    # ---- 5. 结论 ----
    report["verdict"] = {
        "template_ok": bool(tpl["segments"]),
        "draft_ok": Path(draft_json).is_file(),
        "render_ok": bool(render_rep.get("ok")) if not args.no_render else None,
        "asset_reused": apply_rep.get("asset_reused"),
        "content_linked": apply_rep.get("content_linked", 0),
        "unknown_assets": tpl["counts"].get("unknown", 0),
    }
    (out / "verify_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                            encoding="utf-8")

    # 控制台摘要（不打印草稿名/家目录）
    a = report["steps"]["apply"]
    print(f"[verify] 模版: {report['steps']['extract']['summary']}")
    print(f"[verify] 新草稿: {a['segments_total']} 段 · {a['duration_sec']}s "
          f"(模版 {a['template_duration_sec']}s, Δ{a['duration_delta_sec']}s) · 角色 {a['segments_by_role']}")
    print(f"[verify] 复用/回卷/缩段: {a['asset_reused']} / {a['asset_wrapped']} / {a['slot_shrunk']} · "
          f"跟随主轨同素材 {a['content_linked']} 段 · "
          f"skipped {a['skipped']} · unknown 素材 {tpl['counts'].get('unknown', 0)}")
    if render_rep:
        if render_rep.get("ok"):
            pr = render_rep["probe"]
            print(f"[verify] 渲染成功 -> {render_rep['mp4']} · {pr.get('duration_sec')}s · "
                  f"{pr.get('width')}x{pr.get('height')} · {pr.get('frames')} 帧 · "
                  f"{render_rep['elapsed_sec']}s")
            print(f"[verify] 渲出图层: 主轨 {render_rep['main_segments']} / 叠加 {render_rep['overlay_segments']} / "
                  f"文字 {render_rep['text_segments']} / 遮罩 {render_rep['masked_segments']}")
            print(f"[verify] unrealized: {render_rep['unrealized']}")
        else:
            print(f"[verify] 渲染失败: {render_rep.get('error')}")
    return 0 if (report["verdict"]["draft_ok"] and report["verdict"]["render_ok"] is not False) else 1


def _seg_roles(segments: List[dict]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for s in segments:
        out[s["role"]] = out.get(s["role"], 0) + 1
    return out


if __name__ == "__main__":
    sys.exit(main())
