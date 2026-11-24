"""把三条子线产出的元素因果分片合并成一张表，并渲染人读版。

输入：forensic/causality_text.json / causality_audio.json / causality_visual.json
输出：
  - forensic/style_element_causality.json            （合并后的单一真源）
  - short-video-studio/assets/style_spectrum/style_element_causality.json（运行期副本，供 Agent 查）
  - references/element-causality.md                  （人读版，由本脚本渲染，不手写）

合并规则：
  * 三个分片共用同一份 schema（references/element-causality-schema.md），字段名必须一致；
    缺字段就报错退出，不允许「差不多就行」——缺字段的规则没法给 Agent 用。
  * 隐私扫描：本机路径 / 草稿目录名 / 剪映草稿根，一律不许出现。
  * 每个风格合并后必须能回答 5 件事：为什么出现、为什么是这个值、作用域、影响谁、违反症状。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
FORENSIC = ROOT / "forensic"
RUNTIME = ROOT / "short-video-studio" / "assets" / "style_spectrum" / "style_element_causality.json"
DOC = ROOT / "references" / "element-causality.md"
OUT = FORENSIC / "style_element_causality.json"

SHARDS = {
    "text": FORENSIC / "causality_text.json",
    "audio": FORENSIC / "causality_audio.json",
    "visual": FORENSIC / "causality_visual.json",
}

REQUIRED = ("present", "why_appears", "value_drivers", "scope", "affects",
            "limits", "coupling", "violation_symptoms", "reuse_check", "sample_table")

ELEMENT_CN = {
    "subtitle": "字幕", "keyword_highlight": "行内高亮", "flower": "花字", "giant_text": "巨字",
    "chapter_card": "章节卡", "end_card": "尾卡",
    "sfx": "音效", "bgm": "BGM", "dual_bgm_switch": "双段 BGM 切换", "voice_ducking": "人声闪避",
    "cutout": "抠像", "speed": "变速", "transition": "转场", "effect": "特效", "sticker": "贴纸",
    "pip": "画中画/缩窗", "overlay_geometry": "叠加几何", "in_anim": "入场动画",
    "out_anim": "出场动画", "color_grade": "调色",
}

ELEMENT_WHY_I18N = {
    "subtitle": "字幕为什么出现", "keyword_highlight": "行内高亮为什么出现", "flower": "花字为什么出现",
    "giant_text": "巨字为什么出现", "chapter_card": "章节卡为什么出现", "end_card": "尾卡为什么出现",
    "sfx": "音效为什么出现", "bgm": "BGM 为什么出现", "dual_bgm_switch": "为什么切第二段 BGM",
    "voice_ducking": "什么时候压人声闪避",
    "cutout": "这一镜为什么要抠像", "speed": "这一段为什么要变速", "transition": "这里为什么没有转场",
    "effect": "这里为什么挂特效", "sticker": "这里为什么贴贴纸", "pip": "这里为什么开小窗",
    "overlay_geometry": "这一层为什么摆在这里", "in_anim": "元素为什么动进来",
    "out_anim": "元素为什么动出去", "color_grade": "这套为什么这样调色",
}

FORBIDDEN = ["/" + "Users/", "C:" + "/Users", "com." + "lveditor", "Movies/" + "Jianying" + "Pro"]


def load_json(p: Path) -> Any:
    return json.loads(p.read_text(encoding="utf-8"))


def _norm_why(el: Dict[str, Any]) -> Dict[str, Any]:
    w = el.get("why_appears")
    if isinstance(w, dict):
        return {"rule": w.get("rule"), "triggers": w.get("triggers") or [],
                "not_when": w.get("not_when") or [], "evidence": w.get("evidence")}
    return {"rule": str(w or ""), "triggers": [], "not_when": [], "evidence": None}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只校验不写盘")
    args = ap.parse_args()

    merged: Dict[str, Any] = {"version": "1.0", "domain": "all",
                              "generated_by": "dev_tools/merge_element_causality.py",
                              "schema": "references/element-causality-schema.md",
                              "domains": {}, "element_index": {}, "styles": {}}
    problems: List[str] = []
    normalized = 0

    for domain, path in SHARDS.items():
        if not path.is_file():
            problems.append(f"缺少分片：{path.name}")
            continue
        shard = load_json(path)
        style_ids = list((shard.get("styles") or {}).keys())
        merged["domains"][domain] = {
            "source": path.name,
            "styles": len(style_ids),
            "generated_from": shard.get("generated_from"),
        }
        for sid, node in (shard.get("styles") or {}).items():
            dst = merged["styles"].setdefault(sid, {"source_ref": node.get("source_ref"), "elements": {}})
            for key, el in (node.get("elements") or {}).items():
                miss = [f for f in REQUIRED if f not in el]
                if miss:
                    problems.append(f"{sid}.{key} 缺字段 {miss}")
                    continue
                if isinstance(el.get("why_appears"), str):
                    # 分片里「不出现」的元素允许用一句话交代原因；合并时统一成对象形态
                    normalized += 1
                elif not isinstance(el.get("why_appears"), (dict, type(None))):
                    problems.append(f"{sid}.{key}.why_appears 类型不对（{type(el.get('why_appears')).__name__}）")
                dst["elements"][key] = {
                    "present": bool(el.get("present")),
                    "absent_reason": el.get("absent_reason"),
                    "why_appears": _norm_why(el),
                    "value_drivers": el.get("value_drivers") or [],
                    "scope": el.get("scope") or {},
                    "affects": el.get("affects") or [],
                    "limits": el.get("limits") or [],
                    "coupling": el.get("coupling") or [],
                    "violation_symptoms": el.get("violation_symptoms") or [],
                    "reuse_check": el.get("reuse_check") or [],
                    "sample_table": el.get("sample_table") or [],
                    "domain": domain,
                }
                idx = merged["element_index"].setdefault(
                    key, {"cn": ELEMENT_CN.get(key, key), "domain": domain,
                          "why_question": ELEMENT_WHY_I18N.get(key, key), "styles": []})
                if el.get("present"):
                    idx["styles"].append(sid)

    # 每个风格必须三段齐全
    for sid, node in merged["styles"].items():
        got = {merged["styles"][sid]["elements"][k]["domain"] for k in node["elements"]}
        if got != set(SHARDS):
            problems.append(f"{sid} 缺域：有 {sorted(got)}，缺 {sorted(set(SHARDS) - got)}")

    text = json.dumps(merged, ensure_ascii=False, indent=1)
    leaked = [t for t in FORBIDDEN if t in text]
    if leaked:
        problems.append(f"隐私泄漏：{leaked}")

    n_el = sum(len(s["elements"]) for s in merged["styles"].values())
    print(f"[merge] 风格 {len(merged['styles'])} 套 · 元素实例 {n_el} 个 · 字段问题 {len(problems)}"
          + (f" · 一句话形态已归一 {normalized} 处" if normalized else ""))
    for p in problems[:12]:
        print("  !", p)
    if problems:
        sys.exit(1)
    if args.check:
        print("[merge] 仅校验，未写盘")
        return

    OUT.write_text(text, encoding="utf-8")
    RUNTIME.parent.mkdir(parents=True, exist_ok=True)
    RUNTIME.write_text(text, encoding="utf-8")
    DOC.write_text(render_doc(merged), encoding="utf-8")
    print(f"[merge] 写出 {OUT.relative_to(ROOT)}（{len(text)} B）")
    print(f"[merge] 写出 {RUNTIME.relative_to(ROOT)}")
    print(f"[merge] 写出 {DOC.relative_to(ROOT)}")


def _fmt_drivers(el: Dict[str, Any]) -> List[str]:
    out = []
    for d in el.get("value_drivers") or []:
        out.append(
            f"| `{d.get('param')}` | {str(d.get('value'))[:60]} | {str(d.get('derived_from') or '—')[:48]} "
            f"| {str(d.get('formula') or '—')[:52]} | {str(d.get('why_this_value') or '')[:90]} |"
        )
    return out


def render_doc(m: Dict[str, Any]) -> str:
    L: List[str] = []
    L.append("# 24 套风格 · 元素因果规则表（由脚本渲染，勿手改）\n")
    L.append("> 这张表回答的是四件事：**这个元素在什么条件下才会出现 → 为什么参数是这个值 → 它作用到哪些段/哪一层 → 它反过来约束了谁**。")
    L.append("> 元素清点（有几个、多密）在 `forensic/style_trigger_rules_*.json` 里；这里只放因果，因为只有因果能当复用时的判断依据。\n")
    L.append(f"生成：`dev_tools/merge_element_causality.py`｜分片：{[v['source'] for v in m['domains'].values()]}\n")
    L.append("## 元素索引\n")
    L.append("| 元素 | 中文 | 域 | 有这套风格的 | 它主要回答的问题 |")
    L.append("|---|---|---|---|---|")
    for key, idx in m["element_index"].items():
        L.append(f"| `{key}` | {idx['cn']} | {idx['domain']} | {len(idx['styles'])}/24 | {idx['why_question']} |")
    L.append("")
    for key, idx in m["element_index"].items():
        L.append(f"\n## {idx['cn']}（`{key}`）\n")
        L.append(f"**{idx['why_question']}**\n")
        rows = []
        for sid, node in sorted(m["styles"].items()):
            el = node["elements"].get(key)
            if not el:
                continue
            rows.append((sid, el))
        for sid, el in rows:
            if not el["present"]:
                L.append(f"- **{sid}**：不出现——{el.get('absent_reason') or '真值 0'}")
                if el.get("violation_symptoms"):
                    L.append(f"  - 硬加会怎样：{el['violation_symptoms'][0]}")
                continue
            w = el["why_appears"]
            L.append(f"- **{sid}**：{w.get('rule')}")
            if w.get("not_when"):
                L.append(f"  - 不该出现：{'；'.join(str(x) for x in w['not_when'][:3])}")
            if w.get("evidence"):
                L.append(f"  - 证据：{str(w['evidence'])[:200]}")
            sc = el.get("scope") or {}
            if sc:
                L.append(f"  - 作用域：{sc.get('unit')} / {str(sc.get('coverage'))[:80]}（不适用：{str(sc.get('excludes'))[:60]}）")
            drv = _fmt_drivers(el)
            if drv:
                L.append("  - 参数推导：\n")
                L.append("    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |")
                L.append("    |---|---|---|---|---|")
                L.extend("    " + d for d in drv)
            if el.get("affects"):
                L.append("  - 影响谁：" + "；".join(f"{a.get('target')}←{str(a.get('how'))[:60]}" for a in el["affects"][:3]))
            if el.get("coupling"):
                L.append("  - 耦合：" + "；".join(f"{c.get('pair') or c.get('with')}（{c.get('type')}）" for c in el["coupling"][:3]))
            if el.get("violation_symptoms"):
                L.append("  - 违反症状：" + "；".join(str(x)[:70] for x in el["violation_symptoms"][:3]))
        L.append("")
    L.append("\n## 怎么用\n")
    L.append("1. Agent 选处理前先查这张表：`scripts/element_rules.py explain <style_id> <element>` → 拿到「为什么出现/参数推导/作用域」。")
    L.append("2. 出片后跑 `scripts/element_rules.py check --pipeline-dir <管道>`：逐元素比对真值，偏离项会指到具体参数与秒数。")
    L.append("3. 表里 `present:false` 的元素仍然留着「硬加会怎样」——那正是复用时要防的错。")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    main()
