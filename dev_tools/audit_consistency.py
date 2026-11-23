#!/usr/bin/env python3
"""单源一致性审计：文档里的默认值、数量、路径、命令必须和代码/资产对得上。

跑法：python3 dev_tools/audit_consistency.py
退出码 0 表示全部一致；1 表示有漂移，逐条打印文件与行号。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "short-video-studio"
sys.path.insert(0, str(SKILL / "scripts"))

DOCS = [
    ROOT / "README.md",
    ROOT / "BRIEF.md",
    SKILL / "SKILL.md",
    *sorted((SKILL / "references").glob("*.md")),
]

# 已知会误导 agent/用户的漂移写法：v3→v4 把 style_01 与 style_14 的名字整体互换后留下的旧口径。
BAD_PATTERNS = [
    (r"门店默认[^\n]{0,12}style_14", "把 style_14 当门店默认"),
    (r"style_14[^\n]{0,10}门店默认", "把 style_14 当门店默认"),
    (r"默认旗舰风格谱\s*`?style_14`?", "把 style_14 当门店默认旗舰"),
    (r"\b14\s*号", "用 14 号指代门店默认款"),
    (r"store_bold_yellow[^\n]{0,12}style_14", "把基础字幕轨 store_bold_yellow 与 style_14 混写"),
    (r"23\s*项(白盒|质检|独立)", "沿用已过期的 23 项质检计数"),
    (r"七大执行模式", "沿用已过期的七大模式计数"),
    (r"(随仓)?内置\s*4\s*款[^\n]{0,8}字体", "字体数量写成 4（实际 5 款）"),
]


def load_library() -> dict:
    return json.loads((SKILL / "assets/style_spectrum/style_spectrum_library.json").read_text(encoding="utf-8"))


def load_templates() -> dict:
    return json.loads((SKILL / "assets/subtitle_templates/templates.json").read_text(encoding="utf-8"))["templates"]


def check_bad_patterns(errors: list[str]) -> None:
    for doc in DOCS:
        for i, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
            for pattern, why in BAD_PATTERNS:
                if re.search(pattern, line):
                    errors.append(f"{doc.relative_to(ROOT)}:{i}: {why} -> {line.strip()[:90]}")


def check_counts(errors: list[str], library: dict, templates: dict) -> None:
    style_count = len(library["styles"])
    template_count = len(templates)
    font_count = len(list((SKILL / "assets/fonts").glob("*")))
    archetype_count = len(library.get("archetypes") or [])
    rulebook_count = len(library.get("shared_rulebooks") or {})
    for doc in DOCS:
        for i, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
            for n in re.findall(r"(\d+)\s*套[^。\n]{0,12}风格", line):
                if int(n) != style_count:
                    errors.append(f"{doc.relative_to(ROOT)}:{i}: 风格数写 {n}，实际 {style_count}")
            for n in re.findall(r"(\d+)\s*套[^。\n]{0,10}字幕模板", line):
                if int(n) != template_count:
                    errors.append(f"{doc.relative_to(ROOT)}:{i}: 字幕模板数写 {n}，实际 {template_count}")
            for n in re.findall(r"(\d+)\s*款[^。\n]{0,12}字体", line):
                if int(n) != font_count:
                    errors.append(f"{doc.relative_to(ROOT)}:{i}: 字体数写 {n}，实际 {font_count}")
            for n in re.findall(r"(\d+)\s*大[^。\n]{0,10}(?:包装|视觉)流派", line):
                if int(n) != archetype_count:
                    errors.append(f"{doc.relative_to(ROOT)}:{i}: 流派数写 {n}，实际 {archetype_count}")
            for n in re.findall(r"(\d+)\s*大[^。\n]{0,10}剪辑规则字典", line):
                if int(n) != rulebook_count:
                    errors.append(f"{doc.relative_to(ROOT)}:{i}: 规则字典数写 {n}，实际 {rulebook_count}")


def check_paths(errors: list[str]) -> None:
    token = re.compile(r"`([^`\n]+)`")
    for doc in DOCS:
        for i, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
            for raw in token.findall(line):
                path = raw.split(" ")[0].rstrip(".,;:，。；：")
                if not path.startswith(("scripts/", "references/", "assets/", "subskills/")):
                    continue
                if any(ch in path for ch in "<>*{}|") or path.endswith("/"):
                    continue
                if not (SKILL / path).exists():
                    errors.append(f"{doc.relative_to(ROOT)}:{i}: 引用的文件不存在 -> {path}")


def check_cli(errors: list[str]) -> None:
    cmd = re.compile(r"python3?\s+(scripts/[\w_]+\.py)([^\n]*)")
    for doc in DOCS:
        for i, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
            for match in cmd.finditer(line):
                script, rest = match.group(1), match.group(2)
                target = SKILL / script
                if not target.is_file():
                    errors.append(f"{doc.relative_to(ROOT)}:{i}: 命令里的脚本不存在 -> {script}")
                    continue
                source = target.read_text(encoding="utf-8")
                for flag in set(re.findall(r"(--[a-z][\w-]+)", rest)):
                    if flag not in source:
                        errors.append(f"{doc.relative_to(ROOT)}:{i}: {script} 没有参数 {flag}")


def check_menu(errors: list[str]) -> None:
    from apply_style_spectrum import build_style_menu

    library = load_library()
    for track in ("store", "creator"):
        for has_aroll in (True, False):
            menu = build_style_menu(library, track, has_aroll=has_aroll)
            if not 3 <= len(menu) <= 4:
                errors.append(f"风格点单 {track}/aroll={has_aroll}: 只出 {len(menu)} 款，应为 3~4 款")
            for entry in menu:
                sid = entry["style_id"]
                if entry.get("recommended_track") != track:
                    errors.append(f"风格点单 {track}/{sid}: 轨道不符")
                if not has_aroll and str(entry.get("requires_aroll") or "optional") == "required":
                    errors.append(f"风格点单 {track}/无出镜: 混入必须出镜的 {sid}")


def check_hygiene(errors: list[str]) -> None:
    for path in (SKILL / "scripts").glob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "/Users/" in text or re.search(r"[A-Z]:\\\\Users", text):
            errors.append(f"{path.relative_to(ROOT)}: 含本机绝对路径")


def main() -> int:
    errors: list[str] = []
    library = load_library()
    templates = load_templates()

    default_id = library.get("default_style_id")
    creator_id = library.get("default_creator_style_id")
    if default_id != "style_01":
        errors.append(f"风格库默认门店款是 {default_id}，文档口径是 style_01")
    if creator_id != "style_11":
        errors.append(f"风格库默认博主款是 {creator_id}，文档口径是 style_11")
    tracks = {s["style_id"]: s.get("recommended_track") for s in library["styles"]}
    if tracks.get(default_id) != "store":
        errors.append(f"默认门店款 {default_id} 的轨道是 {tracks.get(default_id)}")
    if tracks.get(creator_id) != "creator":
        errors.append(f"默认博主款 {creator_id} 的轨道是 {tracks.get(creator_id)}")

    check_bad_patterns(errors)
    check_counts(errors, library, templates)
    check_paths(errors)
    check_cli(errors)
    check_menu(errors)
    check_hygiene(errors)

    print(f"[audit] 文档 {len(DOCS)} 篇 · 风格 {len(library['styles'])} 套 · 字幕模板 {len(templates)} 套")
    if errors:
        print(f"[audit] FAIL：{len(errors)} 处不一致")
        for e in errors:
            print(" -", e)
        return 1
    print("[audit] PASS：默认值、数量、路径、命令、点单菜单全部与单源一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
