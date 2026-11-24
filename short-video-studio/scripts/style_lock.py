#!/usr/bin/env python3
"""
风格锁：未经用户同意，管线必须 100% 套用所选风格的库参数。

背景（用户实测）：Agent 执行时会「顺手」改风格的标准——放大字号、换颜色、加转场、
调 B-roll 比例、改花字条数——而且不说明。用户的原话是：
「在没有经过用户同意的前提下，必须百分百套用风格执行。」

所以这里不靠承诺，靠比对：把「库里写的」和「管线里实际在用的」逐字段对撞，
不一致就 FAIL，并指出是哪一项、库是多少、实际是多少。要偏离，必须有一份
记录着用户同意的豁免文件，且只豁免被点名的那几个字段。

判定面（三处，都是真会影响出片的）：
  1. 字幕：pipeline/05_subtitles.json 里实际用的 template_config vs templates.json
  2. 蓝图：assets/style_spectrum/template_blueprints.json 里该风格的 subtitle_override / giant_count
  3. 应用配置：pipeline/style_spectrum_applied.json vs 风格库条目

用法：
  python3 scripts/style_lock.py --pipeline-dir <项目>/pipeline          # 只检查，PASS/FAIL
  python3 scripts/style_lock.py --style-id style_01                     # 只查库与蓝图自洽
  python3 scripts/style_lock.py --pipeline-dir <p> --json               # 机读
  python3 scripts/style_lock.py --pipeline-dir <p> \
      --waive subtitles.font_size --waive subtitles.keyword_color \
      --reason "用户说字太小，同意这两项放大"                            # 只在用户点头后运行
  python3 scripts/style_lock.py --pipeline-dir <p> --clear-waiver
退出码：0 = 完全套用（或偏离项都有用户豁免）；3 = 存在未经同意的偏离。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import ASSETS_DIR, read_json, write_json

LIB_PATH = ASSETS_DIR / "style_spectrum" / "style_spectrum_library.json"
TPL_PATH = ASSETS_DIR / "subtitle_templates" / "templates.json"
BP_PATH = ASSETS_DIR / "style_spectrum" / "template_blueprints.json"
WAIVER_NAME = "00_style_waiver.json"

# 字幕模板里必须 100% 一致的字段（这些直接决定屏幕上长什么样）
SUBTITLE_FIELDS: Tuple[Tuple[str, str], ...] = (
    ("font_size", "字号"),
    ("keyword_scale", "关键词放大倍数"),
    ("primary_color", "主色"),
    ("keyword_color", "关键词色"),
    ("stroke_mode", "描边方式（none=只有阴影）"),
    ("stroke_color", "描边色"),
    ("stroke_width", "描边宽"),
    ("shadow", "有无阴影"),
    ("shadow_color", "阴影色"),
    ("shadow_offset", "阴影偏移"),
    ("shadow_blur_px", "阴影虚化"),
    ("bg_box_color", "底框色"),
    ("bg_radius", "底框圆角"),
    ("bg_shape", "底框形状"),
    ("safe_y_center", "字幕纵向位置"),
    ("max_chars_per_line", "每行字数上限"),
    ("full_width_bar", "通栏底条"),
    ("source_font_family", "源字体"),
)

# 蓝图 subtitle_override 要跟字幕模板对齐的字段（蓝图是 Agent 最容易手改的地方）
BLUEPRINT_SUBTITLE_FIELDS: Tuple[Tuple[str, str], ...] = (
    ("name", "模板名"),
    ("font_size", "字号"),
    ("keyword_scale", "关键词放大倍数"),
    ("primary_color", "主色"),
    ("keyword_color", "关键词色"),
    ("stroke_mode", "描边方式（none=只有阴影）"),
    ("stroke_color", "描边色"),
    ("stroke_width", "描边宽"),
    ("shadow", "有无阴影"),
    ("shadow_color", "阴影色"),
    ("shadow_offset", "阴影偏移"),
    ("shadow_blur_px", "阴影虚化"),
    ("safe_y_center", "字幕纵向位置"),
    ("max_chars_per_line", "每行字数上限"),
    ("source_font_family", "源字体"),
)

# 应用配置里必须跟库一致的整块（转场/B-roll 比例/画中画/巨字/调色）
APPLIED_BLOCKS: Tuple[Tuple[str, str], ...] = (
    ("subtitle_template", "字幕模板"),
    ("color_and_lut", "调色"),
    ("transition_and_packaging", "转场与包装"),
    ("broll_overlay_ratio_target", "B-roll 占比"),
    ("packaging_techniques", "包装技法"),
    ("pip_composition_spec", "画中画构图"),
    ("giant_text_spec", "巨字规格"),
    ("source_jianying_font", "源字体名"),
)


def _load_lib() -> Dict[str, Any]:
    return read_json(LIB_PATH)


def _lib_entry(lib: Dict[str, Any], style_id: str) -> Optional[Dict[str, Any]]:
    for s in lib.get("styles") or []:
        if s.get("style_id") == style_id:
            return s
    return None


def _templates() -> Dict[str, Any]:
    return (read_json(TPL_PATH) or {}).get("templates") or {}


def _blueprints() -> Dict[str, Any]:
    return (read_json(BP_PATH) or {}).get("blueprints") or {}


def _same(a: Any, b: Any) -> bool:
    """宽松相等：数值容忍往返误差，dict/list 递归比，字符串严格。"""
    if isinstance(a, bool) or isinstance(b, bool):
        return bool(a) == bool(b)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) <= 1e-6
    if isinstance(a, dict) and isinstance(b, dict):
        return set(a) == set(b) and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    return a == b


def _fmt(v: Any) -> str:
    if isinstance(v, dict):
        return json.dumps(v, ensure_ascii=False, sort_keys=True)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_fmt(x) for x in v) + "]"
    return str(v)


class LockReport(dict):
    """极小包装，只为打印时少写点取键代码。"""

    @property
    def ok(self) -> bool:
        return bool(self.get("ok"))


def audit_pipeline(pipeline_dir: Path) -> Dict[str, Any]:
    lib = _load_lib()
    tpls = _templates()
    bps = _blueprints()
    deviations: List[Dict[str, Any]] = []
    notes: List[str] = []

    applied_p = pipeline_dir / "style_spectrum_applied.json"
    applied: Dict[str, Any] = read_json(applied_p) if applied_p.exists() else {}
    style_id = str(applied.get("style_id") or "")

    # 管线里没记风格时，退回 04_edl.json 里的 style_id（仍算"用哪套"的证据）
    if not style_id:
        edl_p = pipeline_dir / "04_edl.json"
        if edl_p.exists():
            try:
                style_id = str((read_json(edl_p) or {}).get("style_id") or "")
            except Exception:
                style_id = ""
        if style_id:
            notes.append(f"style_spectrum_applied.json 不存在，按 04_edl.json 的 style_id={style_id} 判定")

    if not style_id:
        return {
            "ok": False, "style_id": "", "deviations": [], "waived": [],
            "notes": notes + ["管线没有记录用的是哪套风格（style_spectrum_applied.json / 04_edl.json 都没有）"],
        }

    entry = _lib_entry(lib, style_id)
    if entry is None:
        return {
            "ok": False, "style_id": style_id, "deviations": [], "waived": [],
            "notes": notes + [f"风格库里没有 {style_id}"],
        }

    # ---- 1. 字幕：管线实际用的 template_config vs templates.json
    subs_p = pipeline_dir / "05_subtitles.json"
    if subs_p.exists():
        subs = read_json(subs_p) or {}
        tid = str(subs.get("template_id") or "")
        expect_tpl = tpls.get(tid) or tpls.get(style_id)
        actual_tpl = subs.get("template_config")
        if not expect_tpl:
            notes.append(f"05_subtitles.json 的模板 {tid} 在 templates.json 里不存在，跳过字幕比对")
        elif isinstance(actual_tpl, dict):
            for key, cn in SUBTITLE_FIELDS:
                if key not in expect_tpl and key not in actual_tpl:
                    continue
                if not _same(expect_tpl.get(key), actual_tpl.get(key)):
                    deviations.append({
                        "field": f"subtitles.{key}", "cn": f"字幕{cn}",
                        "expected": expect_tpl.get(key), "actual": actual_tpl.get(key),
                    })
        # 模板身份本身也要一致
        if tid and tid != style_id and tid != str(applied.get("subtitle_template") or style_id):
            deviations.append({
                "field": "subtitles.template_id", "cn": "字幕模板",
                "expected": applied.get("subtitle_template") or style_id, "actual": tid,
            })
    else:
        notes.append("还没有 05_subtitles.json（未跑字幕步），字幕项未比对")

    # ---- 2. 蓝图：subtitle_override / giant_count vs 单源
    bp = bps.get(style_id) or {}
    so = bp.get("subtitle_override") or {}
    if so:
        base = tpls.get(str(applied.get("subtitle_template") or style_id)) or tpls.get(style_id) or {}
        for key, cn in BLUEPRINT_SUBTITLE_FIELDS:
            if key == "name":
                exp_name = base.get("name") or so.get("name")
                got_name = so.get("name")
                if exp_name and got_name and not str(got_name).endswith(str(exp_name)):
                    deviations.append({
                        "field": "blueprint.subtitle_override.name", "cn": f"蓝图字幕{cn}",
                        "expected": exp_name, "actual": got_name,
                    })
                continue
            if key not in base and key not in so:
                continue
            if not _same(base.get(key), so.get(key)):
                deviations.append({
                    "field": f"blueprint.subtitle_override.{key}", "cn": f"蓝图字幕{cn}",
                    "expected": base.get(key), "actual": so.get(key),
                })
    ov = bp.get("overlay_style") or {}
    exp_giant = ((entry.get("packaging_techniques") or {}).get("giant_impact_count"))
    if ov.get("giant_count") is not None and exp_giant is not None:
        if not _same(ov.get("giant_count"), exp_giant):
            deviations.append({
                "field": "blueprint.overlay_style.giant_count", "cn": "蓝图巨字条数",
                "expected": exp_giant, "actual": ov.get("giant_count"),
            })

    # ---- 3. 应用配置：style_spectrum_applied.json vs 库
    for key, cn in APPLIED_BLOCKS:
        if key not in applied:
            notes.append(f"应用配置里没有 {key}（旧管线文件），该项未比对")
            continue
        # subtitle_template 在库里不单存一份：它就是 style_id（resolve_style_runtime_config 同一个口径）
        expect_v = style_id if key == "subtitle_template" else entry.get(key)
        if not _same(expect_v, applied.get(key)):
            deviations.append({
                "field": f"applied.{key}", "cn": cn,
                "expected": expect_v, "actual": applied.get(key),
            })

    # 源草稿零转场是硬约束：任何风格都必须是硬切
    tr = (applied.get("transition_and_packaging") or {}).get("transitions")
    if isinstance(tr, list) and tr and not all("硬切" in str(x) for x in tr):
        deviations.append({
            "field": "applied.transition_and_packaging.transitions", "cn": "转场",
            "expected": ["硬切（源 draft 转场数 = 0，全片零转场）"], "actual": tr,
        })

    # ---- 4. 调色 LUT 一致性：07_color_plan.json / applied vs 库 color_and_lut.lut_file ----
    exp_lut_name = Path(str((entry.get("color_and_lut") or {}).get("lut_file") or "")).name
    if exp_lut_name:
        app_lut_raw = (applied.get("color_and_lut") or {}).get("lut_file") if isinstance(applied, dict) else None
        if app_lut_raw:
            app_lut_name = Path(str(app_lut_raw)).name
            if app_lut_name and app_lut_name.lower() != exp_lut_name.lower():
                deviations.append({
                    "field": "color_and_lut.lut_file", "cn": "调色 LUT 文件",
                    "expected": exp_lut_name, "actual": app_lut_name,
                })
        cp_p = pipeline_dir / "07_color_plan.json"
        if cp_p.is_file():
            try:
                cp_doc = read_json(cp_p) or {}
            except Exception:
                cp_doc = {}
            act_lut_name = Path(str(cp_doc.get("lut_name") or cp_doc.get("source_lut_path") or "")).name
            if act_lut_name and act_lut_name.lower() != exp_lut_name.lower():
                if not any(d.get("field") == "color_and_lut.lut_file" for d in deviations):
                    deviations.append({
                        "field": "color_and_lut.lut_file", "cn": "调色 LUT 文件",
                        "expected": exp_lut_name, "actual": act_lut_name,
                    })

    # ---- 豁免：只认点名字段，且必须写着是用户同意的
    waiver_p = pipeline_dir / WAIVER_NAME
    waiver = read_json(waiver_p) if waiver_p.exists() else {}
    if waiver and str(waiver.get("style_id") or "") != style_id:
        notes.append(f"{WAIVER_NAME} 记的 style_id={waiver.get('style_id')} 与当前 {style_id} 不符，整份豁免作废")
        waiver = {}
    allowed = waiver.get("fields") or {}
    waived: List[str] = []
    blocking: List[Dict[str, Any]] = []
    for d in deviations:
        if d["field"] in allowed:
            waived.append(d["field"])
        else:
            blocking.append(d)

    return {
        "ok": not blocking,
        "style_id": style_id,
        "style_name_cn": entry.get("style_name_cn"),
        "deviations": blocking,
        "waived": waived,
        "notes": notes,
        "waiver_path": str(waiver_p) if waiver else "",
    }


def audit_assets(style_id: str) -> Dict[str, Any]:
    """不给管线目录时，只查「库 / 蓝图 / 字幕模板」三份资产自己自洽不自洽。"""
    lib = _load_lib()
    entry = _lib_entry(lib, style_id)
    if entry is None:
        return {"ok": False, "style_id": style_id, "deviations": [{"field": "style", "cn": "风格", "expected": "库里存在", "actual": "不存在"}], "waived": [], "notes": []}
    tpls = _templates()
    bp = _blueprints().get(style_id) or {}
    so = bp.get("subtitle_override") or {}
    base = tpls.get(style_id) or {}
    devs = []
    for key, cn in BLUEPRINT_SUBTITLE_FIELDS:
        if key in ("name",):
            continue
        if key in base and key in so and not _same(base.get(key), so.get(key)):
            devs.append({"field": f"blueprint.subtitle_override.{key}", "cn": f"蓝图字幕{cn}", "expected": base.get(key), "actual": so.get(key)})
    exp_giant = (entry.get("packaging_techniques") or {}).get("giant_impact_count")
    ov = bp.get("overlay_style") or {}
    if ov.get("giant_count") is not None and exp_giant is not None and not _same(ov.get("giant_count"), exp_giant):
        devs.append({"field": "blueprint.overlay_style.giant_count", "cn": "蓝图巨字条数", "expected": exp_giant, "actual": ov.get("giant_count")})
    return {"ok": not devs, "style_id": style_id, "deviations": devs, "waived": [], "notes": []}


def write_waiver(pipeline_dir: Path, style_id: str, fields: List[str], reason: str, approved_by: str = "用户") -> Path:
    """记录用户同意的偏离项。只能在对应用户点头之后运行——不点头就不该有这个文件。"""
    p = pipeline_dir / WAIVER_NAME
    cur: Dict[str, Any] = read_json(p) if p.exists() else {}
    if str(cur.get("style_id") or "") != style_id:
        cur = {"style_id": style_id, "fields": {}}
    cur.setdefault("fields", {})
    for f in fields:
        cur["fields"][f] = True
    cur["approved_by"] = approved_by
    cur["approved_at"] = datetime.now().isoformat(timespec="seconds")
    cur["reason"] = reason or cur.get("reason") or ""
    write_json(p, cur)
    return p


def render_report(rep: Dict[str, Any]) -> str:
    lines: List[str] = []
    sid = rep.get("style_id") or "（未记录）"
    name = rep.get("style_name_cn") or ""
    if rep.get("ok"):
        lines.append(f"[风格锁] PASS：{sid} {name} 完全按库里标准执行（未发现未经同意的改动）")
    else:
        lines.append(f"[风格锁] FAIL：{sid} {name} 有 {len(rep.get('deviations') or [])} 处偏离库标准，且没有用户同意")
    for d in rep.get("deviations") or []:
        lines.append(f"  ✗ {d['cn']}（{d['field']}）：库里是 {_fmt(d['expected'])}，实际是 {_fmt(d['actual'])}")
    if rep.get("waived"):
        lines.append(f"  ✓ 已由用户同意偏离：{', '.join(rep['waived'])}")
    for n in rep.get("notes") or []:
        lines.append(f"  · {n}")
    if not rep.get("ok"):
        lines.append("  下一步：要么改回库里的值，要么先问用户，用户同意后再运行")
        lines.append("  python3 scripts/style_lock.py --pipeline-dir <项目>/pipeline --waive <字段> --reason \"用户原话\"")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="风格锁：校验管线是否 100% 套用了所选风格的库标准")
    ap.add_argument("--pipeline-dir", type=Path, default=None)
    ap.add_argument("--style-id", default=None, help="不给管线目录时，只查该风格的库与蓝图是否自洽")
    ap.add_argument("--waive", action="append", default=[], help="记录一个用户已同意的偏离字段（可多次）")
    ap.add_argument("--reason", default="", help="用户同意偏离的原话")
    ap.add_argument("--approved-by", default="用户")
    ap.add_argument("--clear-waiver", action="store_true", help="删掉豁免文件（回到严格 100%% 套用）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.style_id and not args.pipeline_dir:
        rep = audit_assets(args.style_id)
        print(json.dumps(rep, ensure_ascii=False, indent=2) if args.json else render_report(rep))
        sys.exit(0 if rep["ok"] else 3)

    if not args.pipeline_dir:
        ap.error("需要 --pipeline-dir 或 --style-id")
    pdir = args.pipeline_dir
    if not pdir.is_dir():
        ap.error(f"目录不存在：{pdir}")

    if args.clear_waiver:
        (pdir / WAIVER_NAME).unlink(missing_ok=True)
        print(f"[风格锁] 已删除 {WAIVER_NAME}，回到严格 100% 套用")

    rep = audit_pipeline(pdir)
    if args.waive:
        if not rep.get("style_id"):
            ap.error("管线里没记录风格，无法登记豁免")
        known = {d["field"] for d in (rep.get("deviations") or [])} | set(rep.get("waived") or [])
        unknown = [f for f in args.waive if f not in known]
        if unknown:
            print(f"[风格锁] 注意：{', '.join(unknown)} 当前并不是偏离项（记下来也无害，但请确认字段名）")
        path = write_waiver(pdir, rep["style_id"], args.waive, args.reason, args.approved_by)
        print(f"[风格锁] 已记录用户同意的偏离 -> {path}")
        rep = audit_pipeline(pdir)

    print(json.dumps(rep, ensure_ascii=False, indent=2) if args.json else render_report(rep))
    sys.exit(0 if rep["ok"] else 3)


if __name__ == "__main__":
    main()
