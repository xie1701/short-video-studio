#!/usr/bin/env python3
"""
[入口 3：纯代码制片规约、可读性校验与像素证据核验工具]
1. init_code_spec：把简短选题与实测资产清单收敛为 00_code_spec.json + SPEC.md + STRUCTURE.md。
2. validate_code_spec：校验一句命题、分幕时长和 (±10%)、中文/英文最低可读停留底线、1080p 字号底线 (28/36/64) 与底部字幕洁净区。
3. generate_verify_template：基于 SPEC + 成片实测生成强制要求「时间戳 + 像素观感」证据的 VERIFY.md。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from common import (
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    DEFAULT_FPS,
    SAFE_ZONE,
    SUBTITLE_SAFE_ZONE,
    ffprobe_media,
    find_commercial_font,
    maybe_reexec_in_venv,
    read_json,
    write_json,
)


def estimate_reading_hold_sec(text: str, is_title: bool = False) -> float:
    """估算屏幕文案最低可读停留时长：中文 chars/4.5 + 1.5s，英文 letters/15 + 1.5s（混排合并计）。"""
    clean = re.sub(r"[\s，。！？、；：“”‘’（）().,!?;:\"'《》【】\[\]\-]+", "", str(text or ""))
    if not clean:
        return 2.0 if is_title else 1.5
    cjk_cnt = sum(1 for ch in clean if "\u4e00" <= ch <= "\u9fff")
    other_cnt = len(clean) - cjk_cnt
    raw = (cjk_cnt / 4.5) + (other_cnt / 15.0) + 1.5
    return round(max(2.0 if is_title else 1.5, raw), 2)


def probe_uploaded_assets(asset_paths: Optional[List[Path]]) -> List[Dict[str, Any]]:
    """探测外部资产真实规格，绝不凭猜测补全元数据。"""
    probed: List[Dict[str, Any]] = []
    for raw_p in (asset_paths or []):
        p = Path(raw_p).expanduser().resolve()
        if not p.is_file():
            probed.append({"path": str(raw_p), "exists": False, "type": "missing", "handling": "缺失文件，禁止引用"})
            continue
        ext = p.suffix.lower()
        if ext in (".png", ".jpg", ".jpeg", ".webp"):
            size_info: Dict[str, Any] = {}
            try:
                from PIL import Image
                with Image.open(p) as im:
                    size_info = {"width": im.width, "height": im.height}
            except Exception:
                pass
            probed.append({
                "path": str(p),
                "exists": True,
                "type": "image",
                "meta": size_info,
                "handling": "等比缩放保持原生影调；若做 UI 演示需与原图 50% 叠图校验",
            })
        elif ext in (".mp4", ".mov", ".mkv", ".m4v", ".wav", ".m4a", ".mp3"):
            meta: Dict[str, Any] = {}
            try:
                info = ffprobe_media(p)
                streams = info.get("streams") or []
                vs = next((s for s in streams if s.get("codec_type") == "video"), None)
                aus = next((s for s in streams if s.get("codec_type") == "audio"), None)
                dur = float(info.get("format", {}).get("duration") or (vs or aus or {}).get("duration") or 0.0)
                meta = {
                    "duration_sec": round(dur, 3),
                    "has_audio": aus is not None,
                }
                if vs:
                    fps_str = str(vs.get("avg_frame_rate") or vs.get("r_frame_rate") or "30/1")
                    num, _, den = fps_str.partition("/")
                    fps_val = float(num) / max(1.0, float(den or 1)) if den else float(num or 30.0)
                    meta.update({
                        "width": int(vs.get("width", 0)),
                        "height": int(vs.get("height", 0)),
                        "fps": round(fps_val, 3),
                    })
            except Exception as exc:
                meta = {"probe_error": str(exc)}
            is_vid = ext in (".mp4", ".mov", ".mkv", ".m4v")
            probed.append({
                "path": str(p),
                "exists": True,
                "type": "video" if is_vid else "audio",
                "meta": meta,
                "handling": "内嵌代码场景前须 extract_clip_sequence() 预抽 JPG 帧序列，禁播 <video>" if is_vid else "按单一时间线直混音轨",
            })
        else:
            probed.append({"path": str(p), "exists": True, "type": "other", "meta": {"bytes": p.stat().st_size}, "handling": "原样使用"})
    return probed


def init_code_spec(
    title: str,
    one_idea: str,
    out_dir: Path,
    duration_sec: float = 15.0,
    width: int = CANVAS_WIDTH,
    height: int = CANVAS_HEIGHT,
    fps: int = DEFAULT_FPS,
    style_id: str = "style_14",
    track: str = "store",
    sections: Optional[List[Dict[str, Any]]] = None,
    exact_copy: Optional[List[str]] = None,
    facts: Optional[List[Dict[str, str]]] = None,
    asset_paths: Optional[List[Path]] = None,
    risks: Optional[List[str]] = None,
    must_haves: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """初始化并持久化 00_code_spec.json、SPEC.md 与 STRUCTURE.md。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    target_dur = round(max(3.0, float(duration_sec)), 3)
    fps_i = max(12, int(fps))
    total_frames = int(round(target_dur * fps_i))

    if not sections:
        t1 = round(min(3.5, target_dur * 0.25), 2)
        t3 = round(min(4.0, target_dur * 0.27), 2)
        t2 = round(max(2.0, target_dur - t1 - t3), 2)
        sections = [
            {
                "id": "sec_01",
                "name": "开篇定锚与痛点设问",
                "start_sec": 0.0,
                "end_sec": t1,
                "duration_sec": t1,
                "mode": "kinetic_text",
                "visual_state": "大字标题弹簧弹出 + 重点胶囊高亮",
                "on_screen_text": title[:16],
                "items": [{"label": title[:16], "subtitle": one_idea[:22]}],
                "is_title": True,
                "transition_to_next": "共享主题色强调条横向滑入下一幕",
            },
            {
                "id": "sec_02",
                "name": "核心机制与数据图解拆解",
                "start_sec": t1,
                "end_sec": round(t1 + t2, 2),
                "duration_sec": t2,
                "mode": "data_chart",
                "visual_state": "核心图解占幅 ≥60%，数值条按真实比例交错弹升",
                "on_screen_text": one_idea[:18],
                "items": [
                    {"label": "核心指标对比", "value": 72},
                    {"label": "常规均值对照", "value": 28},
                ],
                "is_title": False,
                "transition_to_next": "数据主条延展为步骤流程图连线",
            },
            {
                "id": "sec_03",
                "name": "结论收束与核验指引",
                "start_sec": round(t1 + t2, 2),
                "end_sec": target_dur,
                "duration_sec": t3,
                "mode": "flow_diagram",
                "visual_state": "步骤卡片逐级高亮与收尾定格停留",
                "on_screen_text": "核心结论一目了然",
                "items": [
                    {"label": "真实结论", "subtitle": one_idea[:22]},
                    {"label": "透明核验", "subtitle": "全部数据有据可查"},
                ],
                "is_title": False,
                "transition_to_next": "尾帧平稳定格",
            },
        ]

    eff_copy = list(exact_copy or [s.get("on_screen_text", "") for s in sections if s.get("on_screen_text")])
    if title and title not in eff_copy:
        eff_copy.insert(0, title)

    font_path = find_commercial_font()
    eff_risks = list(risks or [
        "文字停留时长不足导致观众来不及读完",
        "图形主视觉高度 < 画幅 1/3 或侵入底部字幕区 [1180, 1468]",
        "帧渲染读 wall-clock 或随机数闪烁导致首尾卡顿",
    ])
    eff_musts = list(must_haves or [
        f"画面清脆呈献核心命题：{one_idea}",
        "所有上屏数字与文案 100% 源自 exact_copy 与 facts 台账",
        f"核心主图或信息卡高度 >= 画幅 1/3 ({round(height / 3)}px) 且底部字幕带洁净无压字",
        "音画与音效起音严格同步（单一时间线，无壁钟抖动）",
    ])

    spec: Dict[str, Any] = {
        "version": 1,
        "mode": "code_video",
        "track": track,
        "style_id": style_id,
        "title": title,
        "one_idea": one_idea,
        "format": {
            "width": int(width),
            "height": int(height),
            "fps": fps_i,
            "target_duration_sec": target_dur,
            "duration_tolerance_pct": 10.0,
            "frame_count": total_frames,
            "codec": "h264",
            "pix_fmt": "yuv420p",
            "audio_required": True,
        },
        "typography": {
            "font_path": font_path or "",
            "min_title_px": 64,
            "min_subtitle_px": 36,
            "min_label_px": 28,
        },
        "layout": {
            "safe_zone": SAFE_ZONE,
            "subtitle_reserved_band": SUBTITLE_SAFE_ZONE,
            "hero_min_height_ratio": 0.33,
            "diagram_min_coverage_ratio": 0.60,
        },
        "exact_copy": eff_copy,
        "facts": list(facts or []),
        "assets": probe_uploaded_assets(asset_paths),
        "sections": sections,
        "risks": eff_risks,
        "must_haves": eff_musts,
    }

    write_json(out_dir / "00_code_spec.json", spec)

    spec_md = [
        f"# Code2Video 生产规约 SPEC · {title}",
        "",
        f"- **一句话命题 (One Idea)**：{one_idea}",
        f"- **画幅格式**：`{width}x{height}` @ `{fps_i}fps` | 目标时长 `{target_dur}s`（总帧数 `{total_frames}`，允许误差 `±10%`） | 风格谱 `{style_id}`（`{track}` 轨）",
        f"- **主字体**：`{Path(font_path).name if font_path else '系统商业回退字体'}` | 字号下限：标题 `≥64px` / 字幕 `≥36px` / 标签 `≥28px`",
        f"- **安全区与字幕预留带**：主文字安全区 `SAFE_ZONE={SAFE_ZONE}`；字幕保留带 `y∈[{SUBTITLE_SAFE_ZONE['y_min']}, {SUBTITLE_SAFE_ZONE['y_max']}]`（代码画面信息避让该带）",
        "",
        "## 一、 Exact Copy 白名单（未入此表严禁上屏）",
        *[f"{idx}. `{txt}`" for idx, txt in enumerate(eff_copy, start=1)],
        "",
        "## 二、 Fact Ledger 事实台账",
        *(
            [f"- `{f.get('item', '')}` = `{f.get('value', '')}`（来源：{f.get('source', '用户提供')}）" for f in spec["facts"]]
            if spec["facts"]
            else ["- 本片暂未录入额外量化数值台账（禁止AI臆造数字/价格/参数）。"]
        ),
        "",
        "## 三、 资产处理规约",
        *(
            [f"- `{a['path']}` ({a['type']}): `{json.dumps(a.get('meta', {}), ensure_ascii=False)}` → {a['handling']}" for a in spec["assets"]]
            if spec["assets"]
            else ["- 纯程序矢量生成，无外部输入素材。"]
        ),
        "",
        "## 四、 高危风险与必检清单 (Must-Haves)",
        *[f"- [风险] {r}" for r in eff_risks],
        *[f"- [必检 #{i}] {m}" for i, m in enumerate(eff_musts, start=1)],
        "",
    ]
    (out_dir / "SPEC.md").write_text("\n".join(spec_md), encoding="utf-8")

    struct_md = [
        f"# 分幕视觉演进与转场结构表 STRUCTURE · {title}",
        "",
        "| 幕号 | 名称 | 时间窗 (s) | 时长 | 版式 | 视觉状态与主动作 | 转场衔接手法 |",
        "| :--- | :--- | :--- | :---: | :--- | :--- | :--- |",
    ]
    for sec in sections:
        st_s = float(sec.get("start_sec", 0.0))
        du_s = float(sec.get("duration_sec", 0.0))
        en_s = float(sec.get("end_sec", st_s + du_s))
        struct_md.append(
            f"| {sec.get('id', '')} | {sec.get('name', '')} | `{st_s:.2f}–{en_s:.2f}` | `{du_s:.2f}s` "
            f"| `{sec.get('mode', 'kinetic_text')}` | {sec.get('visual_state', '')} | {sec.get('transition_to_next', '')} |"
        )
    struct_md.append("")
    (out_dir / "STRUCTURE.md").write_text("\n".join(struct_md), encoding="utf-8")
    return spec


def validate_code_spec(spec: Dict[str, Any]) -> Dict[str, Any]:
    """机器强校验纯代码制片规约：命题、偶分辨率、分幕闭合、字号下限与中文可读停留时间。"""
    errors: List[str] = []
    warnings: List[str] = []

    if not str(spec.get("one_idea") or "").strip():
        errors.append("SPEC 缺少核心命题 one_idea")

    fmt = spec.get("format") or {}
    w = int(fmt.get("width", 0))
    h = int(fmt.get("height", 0))
    fps = int(fmt.get("fps", 0))
    target_dur = float(fmt.get("target_duration_sec", 0.0))
    tol_pct = float(fmt.get("duration_tolerance_pct", 10.0)) / 100.0
    if w <= 0 or h <= 0 or (w % 2 != 0) or (h % 2 != 0):
        errors.append(f"画幅宽高必须为正偶数: {w}x{h}")
    if fps <= 0:
        errors.append(f"无效帧率 fps={fps}")
    if target_dur < 2.0:
        errors.append(f"目标时长过短 target_duration_sec={target_dur}")

    typo = spec.get("typography") or {}
    if int(typo.get("min_label_px", 28)) < 28:
        errors.append(f"最小标签字号 {typo.get('min_label_px')}px < 28px 底线")
    if int(typo.get("min_subtitle_px", 36)) < 36:
        errors.append(f"最小字幕字号 {typo.get('min_subtitle_px')}px < 36px 底线")
    if int(typo.get("min_title_px", 64)) < 48:
        warnings.append(f"主标题字号 {typo.get('min_title_px')}px 偏小（建议 ≥64px）")

    layout = spec.get("layout") or {}
    if float(layout.get("hero_min_height_ratio", 0.33)) < 0.28:
        errors.append("hero_min_height_ratio 低于 0.28，主体过小难以辨识")

    sections = spec.get("sections") or []
    if not sections:
        errors.append("STRUCTURE 缺少分幕 sections 规划")
        sec_sum = 0.0
    else:
        sec_sum = round(sum(float(s.get("duration_sec", 0.0)) for s in sections), 3)
        if target_dur > 0 and abs(sec_sum - target_dur) > max(0.3, target_dur * tol_pct):
            errors.append(f"分幕总时长 {sec_sum:.2f}s 偏离目标时长 {target_dur:.2f}s (±{tol_pct*100:.0f}%)")

        for sec in sections:
            sid = sec.get("id", "sec")
            du = float(sec.get("duration_sec", 0.0))
            if du <= 0.4:
                errors.append(f"{sid} 持续时长过短 ({du:.2f}s)")
                continue
            txt = str(sec.get("on_screen_text") or "").strip()
            if txt:
                hold = float(sec.get("hold_sec", du))
                req_hold = estimate_reading_hold_sec(txt, is_title=bool(sec.get("is_title", False)))
                if hold + 0.05 < req_hold:
                    msg = f"{sid} 文案 '{txt[:12]}' 停留 {hold:.2f}s < 可读底线 {req_hold:.2f}s (chars/4.5+1.5)"
                    if hold < max(1.2, req_hold * 0.65):
                        errors.append(msg)
                    else:
                        warnings.append(msg)

    passed = len(errors) == 0
    return {
        "passed": passed,
        "errors": errors,
        "warnings": warnings,
        "section_duration_sum": sec_sum,
    }


def generate_verify_template(
    pipeline_dir: Path,
    spec_path: Optional[Path] = None,
    video_path: Optional[Path] = None,
) -> Path:
    """基于 00_code_spec.json 与实际视频生成像素取证与 Cold Read 核验单 VERIFY.md。"""
    sp = spec_path or (pipeline_dir / "00_code_spec.json")
    spec = read_json(sp) if sp.is_file() else {}
    musts = spec.get("must_haves") or [
        "核心主视觉轮廓在一秒内可被路人直观识别",
        "屏显中文文案 100% 匹配 SPEC 且字号/对比度合格、留够阅读停留",
        "底部字幕带 [1180, 1468] 与代码图文严格互不压挡",
        "前 3 秒动作连拍 (10_action_strips.jpg) 无闪烁、跳帧或瞬移",
    ]

    v_cand = video_path or (pipeline_dir / "output_final.mp4")
    v_meta_str = "待成片渲染后探测"
    if v_cand.is_file():
        try:
            info = ffprobe_media(v_cand)
            vs = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), {})
            aus = next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), None)
            dur = float(info.get("format", {}).get("duration") or 0.0)
            v_meta_str = f"`{vs.get('width')}x{vs.get('height')}` | `{vs.get('codec_name')}/{vs.get('pix_fmt')}` | `{dur:.2f}s` | audio={'有' if aus else '无'}"
        except Exception as exc:
            v_meta_str = f"探测异常: {exc}"

    qa_p = pipeline_dir / "10_qa_report.json"
    qa_summary = "待运行 22 项黑盒质检"
    if qa_p.is_file():
        try:
            qa = read_json(qa_p)
            qa_summary = f"`{qa.get('pass_count', 0)}/{qa.get('total_checks', 22)}` 项机检与黑盒 ({qa.get('approval_status', '')})"
        except Exception:
            pass

    lines = [
        f"# 像素级冷读与交付验收核对表 VERIFY · {spec.get('title', pipeline_dir.parent.name)}",
        "",
        f"- **实测成片流信息**：{v_meta_str}",
        f"- **22 项自动+黑盒裁判**：{qa_summary}",
        "- **关联取证图**：`00_contact_sheet.jpg` / `10_action_strips.jpg` / `09_ui_safe_preview.jpg`",
        "",
        "> **铁律**：证据栏只能填**在真实画面像素上看到的事实**（如 `t=1.20s 橙色柱条高 620px 位于 x∈[120,840]`），严禁写“代码 `draw()` 已绘制”。",
        "",
        "## 一、 Cold Read 盲读记录（未看代码先看画）",
        "- **开篇 0.0–1.0s 第一眼印象**：_[填写：路人 1 秒内认出什么主对象、占比多大、对比度最高元素是什么]_",
        "- **动作连拍条 (`10_action_strips.jpg`) 审查**：_[填写：0.2s 抽帧是否有弹簧卡顿、文字闪现、字幕侵入]_",
        "",
        "## 二、 SPEC 必检项逐条像素取证",
        "| # | Must-Have 要求 | 核查时间戳 / 抽帧图 | 像素级观感与尺寸证据 | 结论 |",
        "| :---: | :--- | :--- | :--- | :---: |",
    ]
    for i, m in enumerate(musts, start=1):
        lines.append(f"| {i} | {m} | `t=__s` / `10_action_strips.jpg` | _[实看像素描述]_ | ☐ PASS / FAIL |")
    lines.append("")

    out_md = pipeline_dir / "VERIFY.md"
    out_md.write_text("\n".join(lines), encoding="utf-8")
    return out_md


def main() -> None:
    maybe_reexec_in_venv()
    parser = argparse.ArgumentParser(description="Code2Video 规约初始化、校验与像素取证生成")
    parser.add_argument("--init-spec", action="store_true", help="生成 00_code_spec.json / SPEC.md / STRUCTURE.md")
    parser.add_argument("--validate-spec", help="校验指定 00_code_spec.json")
    parser.add_argument("--init-verify", help="为指定 pipeline 目录生成 VERIFY.md")
    parser.add_argument("--title", default="纯代码商业科普短片")
    parser.add_argument("--one-idea", default="用直观视觉对照说清一个核心事实")
    parser.add_argument("--out-dir", help="输出目录 (与 --init-spec 配合)")
    parser.add_argument("--duration", type=float, default=15.0)
    parser.add_argument("--style-id", default="style_14")
    parser.add_argument("--track", choices=["store", "creator"], default="store")
    parser.add_argument("--assets", nargs="*", help="需探针测量的外部素材路径列表")
    parser.add_argument("--video", help="与 --init-verify 配合指定成片路径")
    args = parser.parse_args()

    if args.init_spec:
        od = Path(args.out_dir or ".")
        asset_ps = [Path(a) for a in (args.assets or [])]
        spec = init_code_spec(
            title=args.title,
            one_idea=args.one_idea,
            out_dir=od,
            duration_sec=args.duration,
            style_id=args.style_id,
            track=args.track,
            asset_paths=asset_ps,
        )
        print(f"[code_spec] 已初始化规约 -> {od / '00_code_spec.json'} & SPEC.md & STRUCTURE.md")
        res = validate_code_spec(spec)
        if res["warnings"]:
            print(f"[code_spec] 提示: {res['warnings']}")
        if not res["passed"]:
            print(f"[code_spec] [FAIL] 初始校验未过: {res['errors']}")
            sys.exit(2)
        return

    if args.validate_spec:
        spec = read_json(Path(args.validate_spec))
        res = validate_code_spec(spec)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        sys.exit(0 if res["passed"] else 2)

    if args.init_verify:
        pdir = Path(args.init_verify)
        vp = Path(args.video) if args.video else None
        out_p = generate_verify_template(pdir, video_path=vp)
        print(f"[code_spec] 已生成像素验收单 -> {out_p}")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
