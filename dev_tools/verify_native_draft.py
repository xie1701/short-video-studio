#!/usr/bin/env python3
"""原生剪映草稿结构自证器。

拿 `export_jianying_native.py` 产出的 `draft_content.json` 与一份解密后的
真剪映草稿逐键比对，证明它不是「长得像」而是 schema 同构，并检查：

  1. 明文可读（不是密文，剪映可直接加载）
  2. 顶层键 / 素材表键 / 各轨 segment 键 / 文字素材键 / 视频素材键 —— 相对真草稿 **零缺失**
  3. 时间线自洽：画面段首尾相接、累计时长 == duration、无重叠
  4. 无悬空引用：每个 segment.material_id 都能在 materials 里找到
  5. 文字可编辑：字幕与花字是 text 素材（不是烘焙 PNG 图层），字体族写入源模版字体

用法:
  python3 dev_tools/verify_native_draft.py <draft_dir|draft_content.json> [--ref <真草稿路径>]
"""

from __future__ import annotations

import argparse
import os
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "forensic"))

DEFAULT_REF = Path(os.environ.get("JY_DRAFT_REF") or "")  # 真草稿参考由环境变量给，仓库里不留本机草稿名
US = 1_000_000


def _load(path: Path) -> Dict[str, Any]:
    if path.is_dir():
        path = path / "draft_content.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _missing(ref: Dict[str, Any], got: Dict[str, Any]) -> List[str]:
    return sorted(set(ref) - set(got))


def verify(draft_dir: Path, ref_path: Path) -> int:
    content = _load(draft_dir)
    ref = _load(ref_path)
    fails: List[str] = []
    checks = 0

    def check(ok: bool, label: str) -> None:
        nonlocal checks
        checks += 1
        if not ok:
            fails.append(label)

    # 1 明文
    raw = json.dumps(content, ensure_ascii=False)
    check(not raw.lstrip().startswith("4eGa0"), "明文可读（base64 密文形态）")

    # 2 schema 同构
    check(not _missing(ref, content), f"顶层键缺失: {_missing(ref, content)}")
    for key in ("videos", "texts", "speeds", "audios"):
        got = (content.get("materials") or {}).get(key) or []
        refm = (ref.get("materials") or {}).get(key) or []
        if got and refm:
            diff = _missing(refm[0], got[0])
            check(not diff, f"materials.{key} 键缺失: {diff}")
    got_tracks = content.get("tracks") or []
    ref_tracks = ref.get("tracks") or []
    check(bool(got_tracks), "存在轨道")
    for t in got_tracks:
        same = next((rt for rt in ref_tracks if rt.get("type") == t.get("type")), None)
        if same and same.get("segments") and t.get("segments"):
            diff = _missing(same["segments"][0], t["segments"][0])
            check(not diff, f"{t.get('type')} 轨 segment 键缺失: {diff}")

    # 3 时间线自洽
    vsegs = [s for t in got_tracks if t.get("type") == "video" for s in (t.get("segments") or [])]
    check(bool(vsegs), "至少一段画面")
    cursor = 0
    ordered = sorted(vsegs, key=lambda s: s["target_timerange"]["start"])
    for s in ordered:
        start, dur = s["target_timerange"]["start"], s["target_timerange"]["duration"]
        check(start >= cursor - 1, f"画面段重叠 @{start / US:.3f}s")
        check(dur > 0, f"零长画面段 @{start / US:.3f}s")
        cursor = max(cursor, start + dur)
    check(abs(content.get("duration", 0) - cursor) <= 40_000, f"总时长 {content.get('duration', 0) / US:.3f}s 与画面累计 {cursor / US:.3f}s 不一致")

    # 4 无悬空引用
    ids = {
        m.get("id")
        for key in ("videos", "texts", "audios", "speeds")
        for m in ((content.get("materials") or {}).get(key) or [])
    }
    dangling = [s.get("material_id") for t in got_tracks for s in (t.get("segments") or []) if s.get("material_id") not in ids]
    check(not dangling, f"悬空素材引用: {dangling[:3]}")

    # 5 文字可编辑
    tsegs = [s for t in got_tracks if t.get("type") == "text" for s in (t.get("segments") or [])]
    check(bool(tsegs), "存在文字轨")
    texts = (content.get("materials") or {}).get("texts") or []
    check(all(str(m.get("type")) == "text" for m in texts), "文字素材类型统一为 text")
    editable = 0
    for m in texts:
        try:
            body = json.loads(m.get("content") or "{}")
        except Exception:
            body = {}
        if str(body.get("text") or "").strip() and m.get("font_name"):
            editable += 1
    check(editable == len(texts) and editable > 0, f"可编辑文字素材 {editable}/{len(texts)}")
    check("overlays" not in json.dumps((content.get("materials") or {}).get("texts") or [], ensure_ascii=False), "文字未烘焙成 PNG 图层")

    sentinel_missing = _missing(ref, content)
    print(f"native draft: {draft_dir}")
    print(f"  tracks: {[(t.get('type'), len(t.get('segments') or [])) for t in got_tracks]}")
    print(f"  duration: {content.get('duration', 0) / US:.2f}s  canvas: {content.get('canvas_config')}")
    print(f"  texts: {len(texts)} 字体族: {sorted({m.get('font_name') for m in texts if m.get('font_name')})}")
    if fails:
        print(f"  FAIL ({len(fails)}/{checks})")
        for f in fails:
            print(f"   - {f}")
        return 1
    print(f"  PASS ({checks} checks, schema delta 0 vs {ref_path.name})")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="原生剪映草稿结构自证")
    ap.add_argument("draft", help="草稿目录或 draft_content.json 路径")
    ap.add_argument("--ref", default=str(DEFAULT_REF), help="参考真草稿路径")
    args = ap.parse_args()
    ref = Path(args.ref)
    if not ref.is_file():
        print(f"[skip] 参考真草稿不存在（{ref}），需先跑 forensic/jy_draft_decrypt.py 解密本机草稿")
        sys.exit(0)
    sys.exit(verify(Path(args.draft), ref))


if __name__ == "__main__":
    main()
