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
  python3 scripts/style_lock.py --style-id style_01 --check-dna         # 附加深层 DNA 自检
  python3 scripts/style_lock.py --pipeline-dir <p> --json               # 机读
  python3 scripts/style_lock.py --pipeline-dir <p> \
      --waive subtitles.font_size --waive subtitles.keyword_color \
      --reason "用户说字太小，同意这两项放大"                            # 只在用户点头后运行
  python3 scripts/style_lock.py --pipeline-dir <p> --clear-waiver
退出码：0 = 完全套用（或偏离项都有用户豁免）；3 = 存在未经同意的偏离。
传 --check-dna 时，原风格锁与 DNA 深层体检都通过才返回 0。
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


def check_style_dna_integrity(style_id: str) -> Dict[str, Any]:
    """只读深层体检：描边真值、B-roll 占比、画中画几何与编舞契约是否出现隐性漂移。

    与 `audit_assets()` 的分工：那边查「蓝图字幕覆盖 vs 字幕模板」的字面一致；
    这里查更底层的 DNA —— 实测无描边的风格不许被悄悄加粗黑边、B-roll 目标占比要落在
    实测区间且与转场包装/编舞器同源、画中画几何与 540px 对称居中不越界。
    """
    sid = str(style_id or "").strip()
    checks: List[Dict[str, Any]] = []
    issues: List[str] = []

    def _add(
        cid: str,
        name: str,
        ok: bool,
        expected: Any = None,
        actual: Any = None,
        detail: str = "",
    ) -> bool:
        row: Dict[str, Any] = {"id": cid, "name": name, "ok": bool(ok)}
        if expected is not None:
            row["expected"] = expected
        if actual is not None:
            row["actual"] = actual
        if detail:
            row["detail"] = detail
        checks.append(row)
        if not ok:
            issues.append(f"{name}：约定 {_fmt(expected)}，实际 {_fmt(actual)}")
        return bool(ok)

    def _int_or(value: Any, default: int = 0) -> int:
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default

    lib = _load_lib()
    tpls = _templates()
    bps = _blueprints()
    entry = _lib_entry(lib, sid) if sid else None
    if entry is None:
        _add("style_exists", "风格在库里存在", False, "库里能查到该风格", "没查到")
        return {"ok": False, "style_id": sid, "checks": checks, "issues": issues}

    tpl = tpls.get(sid) or {}
    bp = bps.get(sid) or {}
    _add("style_exists", "风格在库里存在", True, sid, sid)
    _add("subtitle_template_present", "字幕模板存在", bool(tpl), "templates.json 有该风格", "缺失")
    _add("blueprint_present", "风格蓝图存在", bool(bp), "template_blueprints.json 有该风格", "缺失")

    # 1) 描边真值：库里实测无描边，字幕/蓝图就不许悄悄改出粗黑描边
    flower = entry.get("flower_text_spec") or {}
    sub_spec = entry.get("subtitle_spec") or {}
    bp_sub = bp.get("subtitle_override") or {}
    stroke_modes = {
        "flower": str(flower.get("stroke_mode") or "none").strip().lower(),
        "subtitle": str(sub_spec.get("stroke_mode") or "").strip().lower(),
        "template": str(tpl.get("stroke_mode") or "").strip().lower(),
        "blueprint": str(bp_sub.get("stroke_mode") or "").strip().lower(),
    }
    stroke_widths = {
        "flower": flower.get("stroke_width"),
        "subtitle": sub_spec.get("stroke_width"),
        "template": tpl.get("stroke_width"),
        "blueprint": bp_sub.get("stroke_width"),
    }
    bad_widths = {}
    for key, value in stroke_widths.items():
        if value is None:
            continue
        try:
            if float(value) < 0:
                bad_widths[key] = value
        except (TypeError, ValueError):
            bad_widths[key] = value
    if stroke_modes["flower"] == "none":
        stroke_ok = (
            all(stroke_modes[k] in ("", "none") for k in ("subtitle", "template", "blueprint"))
            and not bad_widths
            and all(float(stroke_widths[k] or 0.0) == 0.0
                    for k in ("flower", "subtitle", "template", "blueprint"))
        )
    else:
        observed = {stroke_modes[k] for k in ("subtitle", "template", "blueprint") if stroke_modes[k]}
        stroke_ok = len(observed) <= 1 and (not observed or stroke_modes["flower"] in observed) and not bad_widths
    _add(
        "stroke_mode_consistency",
        "描边真值与蓝图/字幕一致（无描边不许悄悄加粗黑边）",
        stroke_ok,
        {"flower": stroke_modes["flower"], "stroke_width": 0},
        {**stroke_modes, **{f"{k}_width": v for k, v in stroke_widths.items()}},
    )

    # 2) B-roll 占比：目标值 0..1，落在实测区间，且与转场包装量级自洽
    target_raw = entry.get("broll_overlay_ratio_target")
    try:
        target = float(target_raw)
    except (TypeError, ValueError):
        target = None
    _add("broll_ratio_range", "B-roll 占比目标在 0..1", target is not None and 0.0 <= target <= 1.0, "0.0 ~ 1.0", target_raw)

    tp = entry.get("transition_and_packaging") or {}
    tp_target_raw = tp.get("broll_overlay_ratio_target")
    band = entry.get("broll_target_band_pct") or []
    band_ok = True
    if isinstance(band, (list, tuple)) and len(band) == 2 and target is not None:
        try:
            lo, hi = sorted((float(band[0]), float(band[1])))
        except (TypeError, ValueError):
            band_ok = False
        else:
            lo, hi = lo / 100.0, hi / 100.0
            band_ok = lo - 1e-9 <= target <= hi + 1e-9
            if tp_target_raw is not None:
                try:
                    band_ok = band_ok and lo - 1e-9 <= float(tp_target_raw) <= hi + 1e-9
                except (TypeError, ValueError):
                    band_ok = False
    if target is not None and tp_target_raw is not None:
        try:
            band_ok = band_ok and abs(target - float(tp_target_raw)) <= 0.15
        except (TypeError, ValueError):
            band_ok = False
    _add(
        "broll_ratio_band",
        "B-roll 占比与实测区间/转场包装自洽",
        band_ok,
        {"target": target, "band_pct": band},
        {"target": target_raw, "transition_target": tp_target_raw},
    )

    # 3) 编舞契约：style_01 / style_21 的节奏真值必须与库同源，口播倍速锁 1.0
    opening_min = {"style_01": 3, "style_21": 4}
    if sid in opening_min:
        try:
            from style_01_21_choreographer import STYLE_RHYTHM_CONTRACTS
            contract = STYLE_RHYTHM_CONTRACTS.get(sid) or {}
        except ImportError:
            contract = {}
        if not contract:
            _add("choreographer_contract", "编舞契约可用", True, "可导入", "未安装，跳过", "skip")
        else:
            try:
                c_target = float(contract.get("broll_target_ratio"))
            except (TypeError, ValueError):
                c_target = None
            try:
                c_speed = float(contract.get("aroll_speed_lock"))
            except (TypeError, ValueError):
                c_speed = None
            try:
                c_open = int(contract.get("opening_cuts_3s"))
            except (TypeError, ValueError):
                c_open = None
            try:
                c_scale = float(contract.get("punch_in_scale"))
            except (TypeError, ValueError):
                c_scale = None
            contract_ok = (
                target is not None
                and c_target is not None
                and abs(c_target - target) <= 1e-6
                and c_speed is not None
                and abs(c_speed - 1.0) <= 1e-9
                and c_open is not None
                and c_open >= opening_min[sid]
                and c_scale is not None
                and 1.0 <= c_scale <= 1.3
            )
            _add(
                "choreographer_contract",
                "编舞契约与库真值同源（口播倍速锁 1.0）",
                contract_ok,
                {"broll_target_ratio": target, "aroll_speed_lock": 1.0,
                 "opening_cuts_3s": f">={opening_min[sid]}", "punch_in_scale": "1.0~1.3"},
                {"broll_target_ratio": c_target, "aroll_speed_lock": c_speed,
                 "opening_cuts_3s": c_open, "punch_in_scale": c_scale},
            )

    # 4) 画中画：模式明确、蓝图与库一致、几何在可用范围
    pip = entry.get("pip_composition_spec") or {}
    pip_mode = str(pip.get("mode") or "").strip()
    _add("pip_mode_present", "画中画模式已明确", bool(pip_mode), "非空", pip_mode or "空")
    bp_pip_mode = str((bp.get("pip_spec") or {}).get("mode") or "").strip()
    if bp_pip_mode:
        _add("pip_mode_consistent", "画中画模式与蓝图一致", bp_pip_mode == pip_mode, pip_mode, bp_pip_mode)
    try:
        main_scale = float((pip.get("main") or {}).get("scale", 1.0))
    except (TypeError, ValueError):
        main_scale = -1.0
    bad_windows: List[Dict[str, Any]] = []
    for win in pip.get("windows") or []:
        try:
            wx = float(win.get("x", 0.0))
            wy = float(win.get("y", 0.0))
            ws = float(win.get("scale", 1.0))
        except (TypeError, ValueError):
            bad_windows.append({"raw": win})
            continue
        if not (-1.5 <= wx <= 1.5 and -1.5 <= wy <= 1.5 and 0.1 <= ws <= 3.0):
            bad_windows.append({"x": wx, "y": wy, "scale": ws})
    _add(
        "pip_geometry",
        "画中画几何在可用范围",
        0.5 <= main_scale <= 2.0 and not bad_windows,
        "主画面 scale 0.5~2.0；窗口 x/y ±1.5、scale 0.1~3.0",
        {"main_scale": main_scale, "bad_windows": bad_windows},
    )

    # 5) 画幅与字幕对称居中：1080x1920 9:16，字幕中心 540、最大宽度 <= 640
    meta = entry.get("meta") or {}
    canvas_ok = (
        _int_or(meta.get("width")) == 1080
        and _int_or(meta.get("height")) == 1920
        and str(meta.get("aspect") or "") == "9:16"
    )
    _add(
        "canvas_9_16",
        "画幅 1080x1920 9:16",
        canvas_ok,
        {"width": 1080, "height": 1920, "aspect": "9:16"},
        {"width": meta.get("width"), "height": meta.get("height"), "aspect": meta.get("aspect")},
    )
    center = sub_spec.get("symmetry_center_x")
    max_width = sub_spec.get("symmetry_max_width_px")
    tpl_center = tpl.get("symmetry_center_x")
    tpl_max_width = tpl.get("symmetry_max_width_px")

    def _width_ok(value: Any) -> bool:
        try:
            width = float(value)
        except (TypeError, ValueError):
            return False
        return 0 < width <= 640

    symmetry_ok = (
        _int_or(center) == 540
        and _width_ok(max_width)
        and (tpl_center is None or _int_or(tpl_center) == 540)
        and (tpl_max_width is None or _width_ok(tpl_max_width))
    )
    _add(
        "subtitle_symmetry",
        "字幕 540px 对称居中且不超宽",
        symmetry_ok,
        "center=540，max_width<=640",
        {"center": center, "max_width": max_width,
         "template_center": tpl_center, "template_max_width": tpl_max_width},
    )

    # 6) 源草稿零转场是硬约束，库里的转场说明也不许混进叠化/闪白
    transitions = tp.get("transitions") or []
    transitions_ok = bool(transitions) and all("硬切" in str(item) for item in transitions)
    _add("hard_cut_only", "零转场硬切", transitions_ok, "全部为硬切", transitions)

    return {
        "ok": all(row["ok"] for row in checks),
        "style_id": sid,
        "checks": checks,
        "issues": issues,
    }


def render_dna_report(dna: Dict[str, Any]) -> str:
    """把 DNA 体检结果写成普通人一眼能看懂的两三行。"""
    lines = [f"[风格DNA] {'PASS' if dna.get('ok') else 'FAIL'}：{dna.get('style_id') or '（未记录）'}"]
    for row in dna.get("checks") or []:
        lines.append(f"  {'✓' if row.get('ok') else '✗'} {row.get('name')}")
    if dna.get("issues"):
        lines.append("  需要修正：" + "；".join(str(x) for x in dna["issues"]))
    return "\n".join(lines)


def _merge_dna_report(rep: Dict[str, Any], dna: Dict[str, Any]) -> Dict[str, Any]:
    """在原风格锁报告上附加 DNA 结果；没传 --check-dna 时这条路径根本不会走。"""
    merged = dict(rep)
    merged["dna"] = dna
    merged["ok"] = bool(rep.get("ok")) and bool(dna.get("ok"))
    if not dna.get("ok"):
        merged["notes"] = list(merged.get("notes") or []) + [
            f"DNA 自检未通过：{item}" for item in (dna.get("issues") or [])
        ]
    return merged


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
    dna = rep.get("dna")
    if rep.get("ok"):
        lines.append(f"[风格锁] PASS：{sid} {name} 完全按库里标准执行（未发现未经同意的改动）")
    elif isinstance(dna, dict) and not rep.get("deviations"):
        lines.append(f"[风格锁] FAIL：{sid} {name} 的深层 DNA 自检没通过")
    else:
        lines.append(f"[风格锁] FAIL：{sid} {name} 有 {len(rep.get('deviations') or [])} 处偏离库标准，且没有用户同意")
    for d in rep.get("deviations") or []:
        lines.append(f"  ✗ {d['cn']}（{d['field']}）：库里是 {_fmt(d['expected'])}，实际是 {_fmt(d['actual'])}")
    if rep.get("waived"):
        lines.append(f"  ✓ 已由用户同意偏离：{', '.join(rep['waived'])}")
    for n in rep.get("notes") or []:
        lines.append(f"  · {n}")
    if isinstance(dna, dict):
        lines.extend(render_dna_report(dna).splitlines())
    if not rep.get("ok"):
        dna_failed = isinstance(dna, dict) and not dna.get("ok")
        if rep.get("deviations") or not dna_failed:
            lines.append("  下一步：要么改回库里的值，要么先问用户，用户同意后再运行")
            lines.append("  python3 scripts/style_lock.py --pipeline-dir <项目>/pipeline --waive <字段> --reason \"用户原话\"")
        if dna_failed:
            lines.append("  下一步：把上面点名漂移的字段改回风格库真值，再运行 --check-dna")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="风格锁：校验管线是否 100% 套用了所选风格的库标准")
    ap.add_argument("--pipeline-dir", type=Path, default=None)
    ap.add_argument("--style-id", default=None, help="不给管线目录时，只查该风格的库与蓝图是否自洽")
    ap.add_argument("--waive", action="append", default=[], help="记录一个用户已同意的偏离字段（可多次）")
    ap.add_argument("--reason", default="", help="用户同意偏离的原话")
    ap.add_argument("--approved-by", default="用户")
    ap.add_argument("--clear-waiver", action="store_true", help="删掉豁免文件（回到严格 100%% 套用）")
    ap.add_argument("--check-dna", action="store_true", help="附加只读深层 DNA 自检（描边/B-roll 占比/画中画/编舞契约）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.style_id and not args.pipeline_dir:
        rep = audit_assets(args.style_id)
        if args.check_dna:
            rep = _merge_dna_report(rep, check_style_dna_integrity(args.style_id))
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

    if args.check_dna:
        dna_style_id = str(rep.get("style_id") or args.style_id or "").strip()
        if dna_style_id:
            rep = _merge_dna_report(rep, check_style_dna_integrity(dna_style_id))
        else:
            rep = _merge_dna_report(rep, {
                "ok": False,
                "style_id": "",
                "checks": [],
                "issues": ["管线没有记录用的是哪套风格，无法做 DNA 自检"],
            })

    print(json.dumps(rep, ensure_ascii=False, indent=2) if args.json else render_report(rep))
    sys.exit(0 if rep["ok"] else 3)


if __name__ == "__main__":
    main()
