#!/usr/bin/env python3
"""从锁定口播生成导演任务、校验写回的总谱，或离线生成总谱。"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import unicodedata
from difflib import SequenceMatcher
from fractions import Fraction
from functools import lru_cache, partial
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional

from common import read_json, write_json

TASKS_FILENAME = "03b_director_tasks.json"
PLAN_FILENAME = "03b_director_plan.json"
STYLE_02_UNIFIED_PRIMITIVES = (
    "opening_triad", "left_right_punch", "giant_anchor_sub",
    "pain_slam_danmaku", "stacked_dual_color", "list_gather_depth",
    "number_burst_pair", "inline_highlight_cta",
)
DIRECTOR_PRIMITIVES = (
    "hero_intro_pop", "staged_3tier_build", "hero_anchor_plus_danmaku",
    "list_build_gather_dim_overlay", "entity_stack_plus_top", "num_anchor_dim_overlay",
    "inline_emphasis_cta", "plain_bridge", "negation_strike", "framework_pillar",
    "concept_split_card", "semantic_diagram_card", "clean_breath",
    *STYLE_02_UNIFIED_PRIMITIVES,
)
THEME_PROFILES = ("style_02_unified", "store_vibrant", "business_editorial")
GBRO_STYLES = tuple(f"style-{i:02d}" for i in range(1, 11))

_AUDIO_FILENAME = "03_audio_events.json"
_COMPOSITION_FILENAME = "01b_composition_profile.json"
_INVENTORY_FILENAME = "00_inventory.json"
_GENERIC_FRAME8_MARKERS = ("夫妻店到" + "雇人断层", "夫妻店到" + "雇人", "01/02" + "/03")
_BOUND_NOUN_SUFFIX_BEFORE_COPULA = frozenset("感性度率力化者员师家型制法")
_HIGH_DENSITY_BEAT_STYLES = frozenset({"style_09", "style_15", "style_16", "style_19", "style_22", "style_24"})
_DANGLING_HARD_PARTICLES = frozenset("的嘅地得之把被从向给和与在喺")
_DANGLING_ASPECT_PARTICLES = frozenset("了咗着过")
_CAMERAS = ("steady_hold", "slow_push_115", "side_shift_card", "punch_closeup", "jump_closeup", "slow_pull_115")
_ROLES = ("hook", "setup", "twist", "framework", "proof", "cta")
_BROLL_POLICIES = ("lock_aroll", "allow_verified_broll")
_MUSIC_CUES = ("delay_entry", "silence_drop", "section_swell")
_QUIET_PRIMITIVES = ("plain_bridge", "clean_breath")
_TARGET_LOW, _TARGET_HIGH = 0.35, 0.55
_UNIFIED_TARGET_LOW, _UNIFIED_TARGET_HIGH = 0.50, 0.85
_DISPLAY_LIMIT = 7
_DISPLAY_DECOR = "✕✓≠→:：·"
_EMPTY_COPY = {"headline": "", "accent_token": "", "footnote": ""}

_HAN = re.compile(r"[\u3400-\u9fff\U00020000-\U000323af]+")
_LATIN = re.compile(r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*")
_CONNECTIVES = "的地得和与與及以并並跟也都再在就了着著嘅"
_ENTITY_END = re.compile(r"(?:集团|集團|公司|银行|銀行|资本|資本|科技|基金|证券|證券|大学|大學|"
                         r"学院|學院|医院|醫院|茶楼|茶樓|餐厅|餐廳|酒店|品牌|先生|女士)$")

_CN_DIGITS = "零〇一二两兩三四五六七八九"
_CN_TO_DIGITS = str.maketrans(_CN_DIGITS + "點点萬億", "0012223456789..万亿")
_UNITS = {"十": 10, "百": 100, "千": 1000, "万": 10000, "亿": 100000000}
_CN_NONZERO = "一二两兩三四五六七八九"
_SCALES = "十百千万亿萬億"
_DIGITS = f"0-9{_CN_DIGITS}"
_NUMERAL = (
    rf"(?:[{_DIGITS}{_SCALES}]|(?<=[0-9]),(?=[0-9]{{3}}(?![0-9]))"
    rf"|(?<=[{_DIGITS}])[.點点](?=[{_DIGITS}]))+"
)
# 汉字分数后紧接另一组有量级的数字时，分子在前一个数词结束。
_FRACTION = (
    rf"{_NUMERAL}分之(?:[{_CN_DIGITS}](?=[0-9])|[{_DIGITS}](?=[{_CN_NONZERO}][{_SCALES}])|{_NUMERAL})"
)
_FIGURATIVE_NUMBER = re.compile(r"(?:万一|萬一|千[万萬](?=不要|不能|不可以|别|別|要|记得|記得|莫|得|小心))")
_NUMBER = re.compile(
    rf"(?P<ordinal>第\s*)?(?P<negative>负|負|(?<![0-9])-)?"
    rf"(?P<percent>百分之)?(?P<bound>超过|超過|超|逾|至少)?"
    rf"(?P<value>{_FRACTION}|{_NUMERAL}(?:\s*[多余餘]?\s*[{_SCALES}])?)"
    r"(?P<tail>\s*(?:多|余|餘|以上|\+))?"
    r"(?P<measure>\s*(?:%|平方米|平方|平米|平|小时|小時|分钟|分鐘|公斤|人民币|人民幣|"
    r"分贝|分貝|赫兹|赫茲|千赫|兆赫|毫米|厘米|分米|微米|纳米|毫秒|微秒|纳秒|流明|尼特|帧|幀|"
    r"元|块|塊|蚊|年|月|天|秒|倍|成|折|人|家|间|間|个|個|次|句|斤|克|亩|畝))?"
)
_AMOUNT_PARTS = re.compile(r"[0-9]+(?:\.[0-9]+)?|[十百千万亿]")
_MEASURE_EQUIVALENTS = {
    "平方米": "平", "平方": "平", "平米": "平",
    "人民币": "元", "人民幣": "元", "块": "元", "塊": "元", "蚊": "元",
    "間": "间", "個": "个", "畝": "亩", "小時": "小时", "分鐘": "分钟",
    "分貝": "分贝", "赫茲": "赫兹", "幀": "帧",
}

_CLAUSE = re.compile(r"[,，。！？!?、；;：:\n]+")
_TRIM = " \t\r\n,，.。！？!?、；;：:「」『』“”"
_NEGATION_WORD = r"并?不是|并非|並非|唔[系係]"
_NEGATION = re.compile(rf"(?<![可但要还还是總总])(?:也|更|都|仲|亦)?(?:{_NEGATION_WORD})")
_CORE = r"核心|本质|本質|重点|重點|关键|關鍵|机制|機制|问题|問題"
_CONTRAST = re.compile(
    rf"(?P<core>{_CORE})?\s*(?:{_NEGATION_WORD})(?P<rejected>[^。！？!?，,]+?)"
    r"(?:(?:[，,]\s*)?而[是系係]|[,，]\s*(?:其实|其實)?[是系係]"
    r"|(?<![不唔就还還也都又更仲])[是系係])(?P<accepted>.+)"
)
_TURN = re.compile(rf"(?:{_NEGATION_WORD})[^。！？!?]{{0,30}}(?:的问题|嘅問題)|关键是|關鍵是|真相")
_AMOUNT_LABEL = re.compile(r"(?:负债|負債|欠债|欠債|欠款|亏损|虧損|亏了|虧了|"
                           r"赔了|賠了|营收|營收|成本|利润|利潤)\s*(?:了|就|有)?\s*$")
_NOT_ENUMERATION = (
    "反应", "反應", "时间", "時間", "眼", "次", "名", "位", "批",
    "天", "年", "月", "周", "週", "小时", "小時", "分钟", "分鐘", "秒",
    "个小时", "個小時", "个月", "個月",
)
_ENUMERATION_SUFFIXES = "点點条條步项項"
_CONCLUSION_BOUNDARIES = frozenset((
    "里", "裡", "裏", "里面", "裡面", "裏面", "内", "內", "中", "当中", "當中", "之中",
    "之后", "之後", "以后", "以後", "才能", "才会", "才會", "从而", "從而", "就能", "能够", "能夠",
    "可以", "需要", "必须", "必須", "逐步", "最终", "最終",
))
_CONCLUSION_WEAK_START = frozenset("的地得了是在把他你我她它您咱着著过過和与與及向将將嘅")
_LIST_LEAD = re.compile(r"(?P<count>[二两兩三四五六七八九2-9])大(?:天王|招牌|特色|单品|單品|品类|品類|款式)"
                        r"|主要有|包括|分为|分為")
_LIST_JOIN = re.compile(r"[,，、；;：:]|同埋|以及|还有|還有|加埋")
_STORE_TERMS = re.compile(r"探店|实体店|實體店|门店|門店|店铺|店舖|门市|門市|展厅|展廳|茶楼|茶樓|点心|點心|"
                          r"招牌|主打|后厨|後廚|餐厅|餐廳|菜单|菜單|到店|开店|開店|"
                          r"四大天王|[百千0-9]平")
_STORE_INTRO = re.compile(r"探店|(?:带|帶)大家(?:逛|睇|看)|走[进進]")
_SHOWCASE = re.compile(r"(?:招牌|主打|特色)(?:菜|产品|產品|项目|項目)?(?:\s*(?:就)?[是系係]|[：:])")
_CTA = re.compile(
    r"评论区|評論區|留言|关注|關注|(?:点赞|點讚)(?!\s*(?:茶[楼樓]|餐[厅廳]|[门門]店|店))"
    r"|收藏|私信|主页|主頁|预约|預約|领取|領取|点击|點擊"
)


def _finite_number(value: Any) -> bool:
    return type(value) in (int, float) and -sys.float_info.max <= value <= sys.float_info.max


def _string_list(value: Any) -> List[str]:
    return [s.strip() for s in value if isinstance(s, str) and s.strip()] if isinstance(value, list) else []


def _timeline(audio_events: Dict[str, Any]) -> tuple[List[Dict[str, Any]], float]:
    if not isinstance(audio_events, dict) or not isinstance(audio_events.get("sentences"), list):
        raise ValueError(f"{_AUDIO_FILENAME} 缺少 sentences 列表")
    by_id: Dict[str, Dict[str, Any]] = {}
    for index, sentence in enumerate(audio_events["sentences"]):
        if not isinstance(sentence, dict):
            raise ValueError(f"sentences[{index}] 必须是对象")
        sid = sentence.get("id")
        start, end = sentence.get("start_sec"), sentence.get("end_sec")
        if not isinstance(sid, str) or not sid.strip() or sid in by_id:
            raise ValueError(f"sentences[{index}] 的 id 缺失或重复")
        if not (_finite_number(start) and _finite_number(end) and 0 <= start < end):
            raise ValueError(f"{sid} 的锁定时间无效")
        if not isinstance(sentence.get("text", ""), str):
            raise ValueError(f"{sid} 的 text 必须是字符串")
        by_id[sid] = sentence

    last_end = max((s["end_sec"] for s in by_id.values()), default=0.0)
    total = audio_events.get("locked_duration_sec", audio_events.get("total_duration_sec", last_end))
    if not _finite_number(total) or total < last_end:
        raise ValueError("锁定总时长无效或短于最后一句")
    return sorted(by_id.values(), key=lambda sentence: sentence["start_sec"]), float(total)


def _optional_document(pipeline_dir: Path, filename: str) -> Optional[Dict[str, Any]]:
    try:
        data = read_json(pipeline_dir / filename)
        if not isinstance(data, dict):
            raise ValueError("应为 JSON 对象")
        return data
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        print(f"[director_plan] [WARN] {filename} 读取失败（{exc}）", file=sys.stderr)
        return None


def _clips(document: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    doc = document or {}
    rows = doc.get("clips") or doc.get("items") or doc.get("shots") or []
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _media_name(value: Any) -> str:
    name = Path(str(value or "").replace("\\", "/")).name
    return re.sub(r"^norm_[0-9]+_", "", name)


def _clip_name(clip: Dict[str, Any]) -> str:
    return _media_name(clip.get("filename") or clip.get("file") or clip.get("source_path") or clip.get("path"))


def _broll_pool(inventory: Optional[Dict[str, Any]], visual_index: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    inventory_rows = _clips(inventory)
    inventory_names = {_clip_name(clip) for clip in inventory_rows} - {""}
    indexed_rows = _clips(visual_index)
    indexed = {_clip_name(clip): clip for clip in indexed_rows if _clip_name(clip)}
    pool = []
    for clip in inventory_rows + [row for row in indexed_rows if _clip_name(row) not in inventory_names]:
        role = clip.get("suggested_role") or clip.get("role")
        name = _clip_name(clip)
        evidence = indexed.get(name, {})
        if role not in ("b_roll_candidate", "b_roll") or any(
            row.get("usable") is False or row.get("video_grade") == "ng"
            or row.get("generated") is True
            or row.get("source") in ("generated", "code", "code_broll")
            or row.get("source_kind") in ("generated", "code", "code_broll")
            for row in (clip, evidence)
        ):
            continue
        tags = list(dict.fromkeys(
            tag for row in (evidence, clip)
            for key in ("scene_tags_zh", "semantic_tags", "tags", "manual_tags")
            for tag in _string_list(row.get(key))
        ))
        entities = list(dict.fromkeys(
            entity for row in (evidence, clip) for entity in _string_list(row.get("entities"))
        ))
        pool.append({
            "filename": name,
            "duration_sec": round(float(clip.get("duration_sec") or evidence.get("duration_sec") or 0.0), 2),
            "description": str(evidence.get("description") or evidence.get("summary") or clip.get("description") or ""),
            "tags": tags,
            "entities": entities,
        })
    return pool


_UNIFIED_THEME_TARGETS = (0.50, 0.85)


def _playbook_paths(style_id: str) -> Dict[str, Optional[Path]]:
    scripts_dir = Path(__file__).resolve().parent
    spectrum_dir = scripts_dir.parent / "assets" / "style_spectrum"
    pb = spectrum_dir / "playbooks" / f"{style_id}.md"
    cg = spectrum_dir / "common_editing_grammar.md"
    return {
        "playbook": pb if pb.is_file() else None,
        "common_grammar": cg if cg.is_file() else None,
    }


def _style_truth_summary(style_id: str) -> Dict[str, Any]:
    try:
        import style_visual_truth as svt
        truth = svt.get_visual_truth(style_id)
        if not truth:
            return {}
        return {
            "family": truth.get("family"),
            "label": truth.get("label"),
            "top_bar": svt.get_persistent_top_bar_spec(style_id),
            "diagram_theme": svt.get_diagram_theme(style_id),
            "scene_beats_spec": [
                {
                    "frac": b.get("frac"),
                    "max_dur": b.get("max_dur"),
                    "mode": (b.get("layout") or {}).get("mode"),
                    "variant": (b.get("layout") or {}).get("variant"),
                }
                for b in svt.get_scene_beat_specs(style_id)
            ],
            "callout_templates": svt.get_callout_templates(style_id),
        }
    except Exception:
        return {}


def _render_director_prompt_md(
    style_id: str,
    total_dur: float,
    sentences: List[Dict[str, Any]],
    prosody: Dict[str, Dict[str, Any]],
    pool: List[Dict[str, Any]],
    profile: Optional[Dict[str, Any]],
    pb_paths: Dict[str, Optional[Path]],
) -> str:
    pb_text = pb_paths["playbook"].read_text(encoding="utf-8") if pb_paths["playbook"] else ""
    cg_text = pb_paths["common_grammar"].read_text(encoding="utf-8") if pb_paths["common_grammar"] else ""
    pb_excerpt = "\n".join(pb_text.splitlines()[:55]) if pb_text else f"（未找到 {style_id} 独立圣经文件）"
    cg_excerpt = "\n".join(cg_text.splitlines()[:28]) if cg_text else ""
    truth_summary = _style_truth_summary(style_id)

    lines = [
        f"# 总导演编排任务包（style_id={style_id}，总时长={total_dur:.2f}s）",
        "",
        "请通读下方风格圣经、风格视觉真值摘要、构图约束、逐字声学口播句表与实拍素材清单，亲自产出高质量 `03b_director_plan.json`。",
        "",
        "## 1. 风格圣经摘要",
        pb_excerpt,
        "",
        "## 1b. 风格视觉真值摘要 (`style_visual_truth.py`)",
        f"- `native_style_mode`: `{style_id != 'style_02'}`",
        f"- `family`: `{truth_summary.get('family', 'default')}`",
        f"- `top_bar`: `{truth_summary.get('top_bar')}`",
        f"- `diagram_theme`: `{truth_summary.get('diagram_theme')}`",
        f"- `scene_beats_spec`: `{truth_summary.get('scene_beats_spec', [])}`",
        f"- `callout_templates`: `{truth_summary.get('callout_templates', [])}`",
        "",
        "## 2. 跨风格共性红线摘要",
        cg_excerpt,
        "",
        "## 3. 构图与人脸安全区 (`01b_composition_profile.json`)",
        f"- `primary_stage_mode`: `{(profile or {}).get('primary_stage_mode', 'pending')}`",
        f"- `allow_behind_person`: `{_allows_behind_person(profile, sentences)}`",
        f"- `theme_hint`: `{(profile or {}).get('theme_hint', 'auto')}`",
        "",
        "## 4. 锁音逐字台词与声学韵律表（总计 " + str(len(sentences)) + " 句）",
        "| sentence_id | 起止秒 | 时长 | 句后停顿 | 语速(字/秒) | 声学重音 | 台词原话 |",
        "| :--- | :---: | :---: | :---: | :---: | :---: | :--- |",
    ]
    for s in sentences:
        sid = str(s["id"])
        pr = prosody.get(sid) or {}
        st, en = float(s["start_sec"]), float(s["end_sec"])
        lines.append(
            f"| `{sid}` | {st:.2f}..{en:.2f} | {en-st:.2f}s | {pr.get('pause_after', 0.0):.2f}s | "
            f"{pr.get('cps', 0.0):.1f} | {'★' if pr.get('acoustic_emphasis') else '-'} | {s.get('text', '')} |"
        )
    lines.extend([
        "",
        f"## 5. 可用实拍 B-roll 索引（共 {len(pool)} 条）",
    ])
    if pool:
        for item in pool[:120]:
            tags_str = ", ".join((item.get("tags") or [])[:8])
            desc = str(item.get("description") or "").replace("\n", " ")[:90]
            dur_v = float(item.get("duration_sec") or 0.0)
            dur_lbl = f" ({dur_v:.1f}s)" if dur_v > 0 else ""
            lines.append(
                f"- `{item.get('filename')}`{dur_lbl} tags=[{tags_str}] {desc}"
            )
    else:
        lines.append("- （无外插实拍 B-roll，全部镜头使用口播 A-roll + 复合排版舞台 + 推拉特写）")
    lines.extend([
        "",
        "## 6. 目标输出 JSON 规范 (`03b_director_plan.json` V12 通用协议)",
        "- 必填顶层字段：`step='03b_director_plan'`, `source='agent'`, `style_id`（`style_01..style_24` 全部支持）, `native_style_mode`（`style_02` 为 `false`，其余为 `true`）, `theme_profile`（`style_02` 统一使用 `'style_02_unified'`；其余快节奏探店可选 `'store_vibrant'`，商业口播可选 `'business_editorial'`）, `acts`, `music_cues`。",
        "- 推荐 V12 通用顶层字段：",
        "  - `semantic_subtitles`: `[{\"text\": \"4-10字完整语义短句（零挂尾助词、零劈词）\", \"keywords\": [\"关键词\"]}, ...]`",
        "  - `style_scene_beats`: 严格依据第 1b 节 `scene_beats_spec`（`style_01/02/06/08` 保持 `[]`，严禁跨风格强塞通用黑卡）与本片台词提炼 `panel_title`/`quote_text`/`panel_lines`/`panel_items`/`diagram_mode`。",
        "  - `style_callouts`: 依据 `callout_templates` 在非 `style_scene_beats` 时段编排透明前景花字 `[{\"text\": \"...\", \"start_sec\": ..., \"end_sec\": ..., \"variant\": \"...\", \"callout_y\": ..., \"font_role\": \"...\"}]`。",
        "  - `cover_copy`: `{\"headline_top\": \"主标题4-9字整句\", \"highlight_badge\": \"黄标黑字重点4-8字\", \"sub_line\": \"可选副标\", \"gbro_style\": \"style-01..style-10\", \"dish_or_subject\": \"贴合本片实拍的菜品/核心主体\"}`",
        "  - `gbro_cover_spec`: `{\"gbro_style\": \"style-01..style-10\", \"dish_or_subject\": \"本片核心实体\", \"expression\": \"推荐神态\", \"headline_top\": \"...\", \"highlight_badge\": \"...\", \"sub_line\": \"...\"}`",
        "- 每幕 `act` 必填：`act_id`, `sentence_ids`, `dramatic_role`, `emotion_intensity`, `primitive`, `visual_copy` (`headline`/`items`/`accent_token`/`footnote`), `camera_intent`, `broll_policy`, `broll_keywords`（可选）, `broll_clips`（可选实拍文件名白名单列表——强烈建议依据上方第 5 节实拍画面逐条描述精确点名；凡素材库没拍到的菜品或细节，必须设 `broll_policy='lock_aroll'` 留真人出镜，宁缺毋滥绝不强行切无关外景或冲突食材）, `sfx_budget`。",
        "- 写完后运行 `director_plan.py apply-plan --pipeline-dir <dir>` 校验溯源、实拍点名白名单、零第8格通用卡污染与覆盖率。",
        "",
    ])
    return "\n".join(lines)


def build_director_tasks(
    pipeline_dir: Path, style_id: str = "style_02", out_path: Optional[Path] = None,
) -> Path:
    """把锁定句表、风格圣经与已有素材依据交给写总谱的大模型总导演。"""
    pipeline_dir = Path(pipeline_dir)
    audio_events = read_json(pipeline_dir / _AUDIO_FILENAME)
    sentences, total = _timeline(audio_events)
    prosody = _sentence_prosody_metrics(sentences, audio_events)
    profile = _optional_document(pipeline_dir, _COMPOSITION_FILENAME)
    pool = _broll_pool(
        _optional_document(pipeline_dir, _INVENTORY_FILENAME),
        _optional_document(pipeline_dir, "00_visual_index.json"),
    )
    pb_paths = _playbook_paths(style_id)
    prompt_path = pipeline_dir / "03b_director_prompt.md"
    prompt_path.write_text(
        _render_director_prompt_md(style_id, total, sentences, prosody, pool, profile, pb_paths),
        encoding="utf-8",
    )
    target_band = _UNIFIED_THEME_TARGETS if style_id == "style_02" else (_TARGET_LOW, _TARGET_HIGH)
    return write_json(Path(out_path) if out_path is not None else pipeline_dir / TASKS_FILENAME, {
        "step": "03b_director_tasks",
        "style_id": style_id,
        "native_style_mode": style_id != "style_02",
        "recommended_theme_profile": "style_02_unified" if style_id == "style_02" else None,
        "locked_duration_sec": total,
        "director_prompt_md": str(prompt_path.name),
        "playbook_file": str(pb_paths["playbook"].relative_to(Path(__file__).resolve().parents[1])) if pb_paths["playbook"] else None,
        "common_grammar_file": str(pb_paths["common_grammar"].relative_to(Path(__file__).resolve().parents[1])) if pb_paths["common_grammar"] else None,
        "style_visual_truth": _style_truth_summary(style_id),
        "gbro_styles": list(GBRO_STYLES),
        "sentences": [{
            "id": sentence["id"], "text": sentence.get("text", ""),
            "start_sec": sentence["start_sec"], "end_sec": sentence["end_sec"],
            "duration_sec": round(sentence["end_sec"] - sentence["start_sec"], 4),
            "pause_after_sec": round(float((prosody.get(sentence["id"]) or {}).get("pause_after") or 0.0), 4),
            "speech_rate_cps": round(float((prosody.get(sentence["id"]) or {}).get("cps") or 0.0), 2),
            "acoustic_emphasis": bool((prosody.get(sentence["id"]) or {}).get("acoustic_emphasis")),
            "keywords": _string_list(sentence.get("keywords")),
        } for sentence in sentences],
        "broll_summary": {
            "has_real_broll": bool(pool), "count": len(pool), "clips": pool,
            "tags_preview": list(dict.fromkeys(tag for clip in pool for tag in clip["tags"]))[:16],
        },
        "composition_hint": {
            "primary_stage_mode": profile.get("primary_stage_mode", "pending"),
            "allow_behind_person": _allows_behind_person(profile, sentences),
            "theme_hint": profile.get("theme_hint"),
        } if profile is not None else "pending",
        "director_primitives": list(DIRECTOR_PRIMITIVES),
        "style_02_unified_primitives": list(STYLE_02_UNIFIED_PRIMITIVES),
        "theme_profiles": list(THEME_PROFILES),
        "camera_intents": list(_CAMERAS),
        "dramatic_roles": list(_ROLES),
        "broll_policies": list(_BROLL_POLICIES),
        "music_cue_types": list(_MUSIC_CUES),
        "rules": [
            "按锁定时间顺序为每句分配且只分配一幕，保留原句 ID。",
            "每行大字移除装饰符号及空白后最多 7 字。词语和数字必须可从该幕原话核对。",
            f"高光幕占锁定时长的目标范围为 {target_band[0]:.2f}..{target_band[1]:.2f}。用安静的过渡句给高光留出呼吸空间。",
            "禁止把人后弹幕放在不允许人后遮挡的机位上。实拍补镜建议附上 broll_keywords。",
            "反转或高潮考虑 silence_drop，开篇考虑 delay_entry。音乐指令最长 3.5 秒。",
            "semantic_subtitles、style_scene_beats、style_callouts 与 gbro_cover_spec 必须完全基于本片原话与风格真值动态产出，严禁跨风格抄用通用黑卡。",
        ],
        "how_to_fill": (
            f"阅读 {prompt_path.name} 与对应风格圣经，将 theme_profile、acts、music_cues、"
            "semantic_subtitles、style_scene_beats、style_callouts、cover_copy、gbro_cover_spec 写入 "
            f"{PLAN_FILENAME}。每幕提供 act_id、sentence_ids、dramatic_role、emotion_intensity、primitive、"
            "visual_copy（headline、items、accent_token、footnote）、camera_intent、broll_policy、broll_keywords、sfx_budget。"
            "用 director_plan.py apply-plan --pipeline-dir <pipeline> 校验并保存。"
        ),
    })


def _allows_behind_person(profile: Optional[Dict[str, Any]], sentences: List[Dict[str, Any]]) -> bool:
    profile = profile or {}
    fallback = profile.get("allow_behind_person")
    if type(fallback) is not bool:
        fallback = profile.get("primary_stage_mode") != "closeup_chest_stage"

    by_path: Dict[str, bool] = {}
    by_stem: Dict[str, bool] = {}
    profiles = profile.get("aroll_profiles")
    for item in profiles if isinstance(profiles, list) else ():
        if not isinstance(item, dict) or type(item.get("allow_behind_person")) is not bool:
            continue
        media = str(item.get("media_path") or "").replace("\\", "/")
        stem = Path(_media_name(media)).stem
        if stem:
            allowed = item["allow_behind_person"]
            by_path[media] = by_path.get(media, True) and allowed
            by_stem[stem] = by_stem.get(stem, True) and allowed
    sources = {str(sentence.get("source_media") or "").replace("\\", "/") for sentence in sentences}
    return all(
        by_path.get(source, by_stem.get(Path(_media_name(source)).stem, fallback))
        for source in sources or {""}
    )


def _resolve_side_shift_x(
    profile: Optional[Dict[str, Any]],
    sentences: List[Dict[str, Any]],
    default: float = 0.08,
) -> float:
    profile = profile if isinstance(profile, dict) else {}
    top_shift = profile.get("safe_center_shift_x")
    fallback = float(top_shift) if _finite_number(top_shift) else float(default)

    by_path: Dict[str, float] = {}
    by_stem: Dict[str, float] = {}
    profiles = profile.get("aroll_profiles")
    for item in profiles if isinstance(profiles, list) else ():
        if not isinstance(item, dict) or not _finite_number(item.get("safe_center_shift_x")):
            continue
        val = float(item["safe_center_shift_x"])
        media = str(item.get("media_path") or "").replace("\\", "/")
        if media:
            by_path[media] = val
        stem = Path(_media_name(media)).stem
        if stem:
            by_stem[stem] = val

    for sentence in sentences:
        source = str(sentence.get("source_media") or "").replace("\\", "/")
        if source in by_path:
            return round(max(-0.12, min(0.12, by_path[source])), 4)
        stem = Path(_media_name(source)).stem
        if stem and stem in by_stem:
            return round(max(-0.12, min(0.12, by_stem[stem])), 4)
    return round(max(-0.12, min(0.12, fallback)), 4)


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold().replace("−", "-")


def _display_length(text: str) -> int:
    return sum(not ch.isspace() and ch not in _DISPLAY_DECOR for ch in text)


def _amount(text: str) -> Optional[Fraction]:
    text = re.sub(r"[\s,多余餘]", "", text)
    if len(text) > 40:
        return None
    denominator, marker, numerator = text.partition("分之")
    if marker:
        whole, share = _amount(denominator), _amount(numerator)
        return share / whole if whole and share is not None else None

    # 连读的「一两百」「三四万」是概数。保留原样，避免换算为单一金额。
    coefficients = re.split(rf"[{_SCALES}.點点]", text)
    if any(re.fullmatch(rf"[0-9{_CN_NONZERO}][{_CN_NONZERO}]+", part)
           for part in coefficients):
        return None
    text = text.translate(_CN_TO_DIGITS)
    parts = _AMOUNT_PARTS.findall(text)
    if not parts or "".join(parts) != text:
        return None
    total = section = Fraction(0)
    value = None
    for part in parts:
        if part not in _UNITS:
            if value is not None:
                return None
            value = Fraction(part)
            continue
        unit = _UNITS[part]
        if unit < 10000:
            section += (value if value is not None else 1) * unit
        else:
            section += value if value is not None else 0
            if unit == 10000:
                total += (section if section or value is not None else 1) * unit
            else:
                subtotal = total + section
                total = (subtotal if subtotal or value is not None else 1) * unit
            section = Fraction(0)
        value = None
    return total + section + (value if value is not None else 0)


def _number_key(match: re.Match[str]) -> tuple:
    raw_value = match["value"]
    value = _amount(raw_value)
    figurative = _FIGURATIVE_NUMBER.match(match.string, match.start("value"))
    if value is None or (figurative and figurative.end() == match.end("value")):
        return (match[0].strip(), "", False, "")
    unit = "%" if match["percent"] else (match["measure"] or "").strip()
    tail = (match["tail"] or "").strip()
    bound = "minimum" if match["bound"] == "至少" or tail == "以上" else ""
    if not bound and (match["bound"] or tail or any(ch in raw_value for ch in "多余餘")):
        bound = "over"
    return (
        -value if match["negative"] else value,
        _MEASURE_EQUIVALENTS.get(unit, unit), bool(match["ordinal"]), bound,
    )


def _number_matches(visual: tuple, spoken: tuple) -> bool:
    value, unit, ordinal, bound = visual
    sv, su, so, sb = spoken
    return (
        value == sv and (unit == su or (not unit and su != "%"))
        and (not ordinal or so) and (not bound or bound == sb)
    )


def _grounded_han(token: str, sources: List[str]) -> bool:
    if any(token in sentence for sentence in sources):
        return True
    if len(token) < 4 or _ENTITY_END.search(token):
        return False
    # 允许同一句删除或补上连接词。核心词要成段、按原顺序保留。
    for source in sources:
        blocks = [m for m in SequenceMatcher(None, token, source, autojunk=False).get_matching_blocks() if m.size >= 2]
        if sum(m.size for m in blocks) < len(token) * 0.6:
            continue
        gaps = [token[:blocks[0].a], token[blocks[-1].a + blocks[-1].size:]]
        for before, after in zip(blocks, blocks[1:]):
            gaps.extend((token[before.a + before.size:after.a], source[before.b + before.size:after.b]))
        if all(not gap.strip(_CONNECTIVES) for gap in gaps):
            return True
    return False


_CJK_MEASURE_GUARD = (
    r"(?:分钟|分鐘|小时|小時|秒|天|周|月|年|倍|折|成|平|平方米|平米|"
    r"分贝|分貝|赫兹|赫茲|千赫|兆赫|毫米|厘米|分米|微米|纳米|毫秒|微秒|纳秒|流明|尼特|像素|帧|幀|"
    r"元|块|塊|万|萬|亿|億|个|個|条|條|件|家|间|間|次|位|种|種|款|套|步|项|項|组|組|层|層|%|％|度|瓦|伏|安|斤|克|升|吨|噸|"
    r"寸|吋|档|檔|转|轉|磅|帕)"
)
_DP_CJK_TECH_UNITS = r"(?:分贝|分貝|赫兹|赫茲|千赫|兆赫|毫米|厘米|分米|微米|纳米|毫秒|微秒|纳秒|流明|尼特|帧|幀)"
_DP_ARABIC_TECH_SINGLE_UNITS = r"(?:寸|吋|档|檔|转|轉|磅|帕)"


def _atomic_alnum_spans(text: str) -> List[tuple[int, int]]:
    """收集「数字+字母单位/混合型号/连字符英文/空格版本号/技术规格」原子跨度，防止 2.4G、Wi-Fi 7 或 4K 60帧被劈碎。"""
    s = str(text or "")
    if not s:
        return []
    spans: List[tuple[int, int]] = []
    try:
        import step5_subtitles as s5
        for ms, me in s5.iter_num_unit_spans(s):
            if me - ms >= 2:
                spans.append((ms, me))
        for rx in (s5._ALNUM_MIXED_TOKEN_RE, s5._HYPHEN_ASCII_TOKEN_RE):
            for m in rx.finditer(s):
                spans.append(m.span())
    except Exception:
        pass
    for m in re.finditer(r"(?<![A-Za-z0-9.])[0-9]+(?:\.[0-9]+)?[A-Za-z][A-Za-z0-9]*(?![A-Za-z0-9])", s):
        spans.append(m.span())
    for m in re.finditer(
        rf"(?<![0-9A-Za-z.])[0-9零〇一二两兩三四五六七八九十百千万萬亿億]+(?:\.[0-9]+)?{_DP_CJK_TECH_UNITS}",
        s,
    ):
        spans.append(m.span())
    for m in re.finditer(rf"(?<![0-9A-Za-z.])[0-9]+(?:\.[0-9]+)?{_DP_ARABIC_TECH_SINGLE_UNITS}", s):
        spans.append(m.span())
    for m in re.finditer(
        rf"(?<![A-Za-z0-9._-])[0-9]+(?:\.[0-9]+)?(?:[KkPp]|[GMgm]?[Hh][Zz]|[Hh][Zz]|[VvWwAa])[ \t]+"
        rf"[0-9]+(?:\.[0-9]+)?(?:[A-Za-z][A-Za-z0-9]*|{_DP_CJK_TECH_UNITS}|{_DP_ARABIC_TECH_SINGLE_UNITS})?",
        s,
    ):
        spans.append(m.span())
    for m in re.finditer(
        rf"(?<![A-Za-z0-9._-])[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*[ \t]+[0-9]+(?:\.[0-9]+)?[A-Za-z]?(?![A-Za-z0-9]|{_CJK_MEASURE_GUARD})",
        s,
    ):
        spans.append(m.span())
    if not spans:
        return []
    spans.sort(key=lambda x: (x[0], -(x[1] - x[0])))
    merged: List[tuple[int, int]] = []
    for a, b in spans:
        if merged and a < merged[-1][1]:
            if b > merged[-1][1]:
                merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    return merged


def _contains_frame8_marker(obj: Any) -> Optional[str]:
    try:
        blob = json.dumps(obj, ensure_ascii=False)
    except Exception:
        blob = str(obj)
    for marker in _GENERIC_FRAME8_MARKERS:
        if marker in blob:
            return marker
    return None


def _trace_copy(
    text: str, sources: List[str], spoken_numbers: List[tuple], spoken_latin: set[str],
) -> List[str]:
    text = _normalize(text)
    missing = [
        match[0].strip() for match in _NUMBER.finditer(text)
        if not any(_number_matches(_number_key(match), spoken) for spoken in spoken_numbers)
    ]
    for match in _LATIN.finditer(text):
        word = match[0]
        if word not in spoken_latin:
            missing.append(word)
            continue
        pos = match.start()
        if pos == 0 or not (text[pos - 1].isdigit() or text[pos - 1] in "._-"):
            standalone_re = re.compile(rf"(?<![0-9a-z._-]){re.escape(word)}(?![a-z0-9._-])")
            if not any(standalone_re.search(src) for src in sources):
                missing.append(word)
        else:
            start_a = pos
            while start_a > 0 and (text[start_a - 1].isalnum() or text[start_a - 1] in "._-"):
                start_a -= 1
            full_tok = text[start_a:match.end()]
            if not any(full_tok in src for src in sources):
                missing.append(full_tok)
    for token in _HAN.findall(_NUMBER.sub(" ", text)):
        if not _grounded_han(token, sources):
            missing.append(token)
    return list(dict.fromkeys(missing))


def _validate_copy(
    visual: Any, location: str, sentences: List[Dict[str, Any]], errors: List[str],
) -> Dict[str, Any]:
    cleaned = dict(_EMPTY_COPY, items=[])
    if not isinstance(visual, dict):
        errors.append(f"{location} 必须是对象")
        return cleaned

    sources = [_normalize(sentence.get("text", "")) for sentence in sentences]
    numbers = [_number_key(match) for source in sources for match in _NUMBER.finditer(source)]
    latin = {word for source in sources for word in _LATIN.findall(source)}

    def checked(text: Any, path: str, *, limit: bool = False) -> str:
        if not isinstance(text, str):
            errors.append(f"{path} 必须是字符串")
            return ""
        text = text.strip()
        length = _display_length(text)
        if limit and length > _DISPLAY_LIMIT:
            errors.append(f"{path} 长度 {length} 超过 {_DISPLAY_LIMIT} 字")
        for token in _trace_copy(text, sources, numbers, latin):
            errors.append(f"hallucinated_token {path} 的 {token!r} 无法从本幕原句溯源")
        return text

    for key in _EMPTY_COPY:
        cleaned[key] = checked(visual.get(key, ""), f"{location}.{key}", limit=key == "headline")
    items = visual.get("items", [])
    if isinstance(items, list):
        cleaned["items"] = [checked(item, f"{location}.items[{i}]", limit=True) for i, item in enumerate(items)]
    else:
        errors.append(f"{location}.items 必须是列表")
    return cleaned


def _validate_music(
    cues: Any, total: float, errors: List[str], warnings: List[str],
) -> List[Dict[str, Any]]:
    if not isinstance(cues, list):
        errors.append("music_cues 必须是列表")
        return []
    cleaned = []
    for i, cue in enumerate(cues):
        location = f"music_cues[{i}]"
        if not isinstance(cue, dict):
            errors.append(f"{location} 必须是对象")
            continue
        kind = cue.get("type")
        if kind not in _MUSIC_CUES:
            errors.append(f"{location}.type 不支持 {kind!r}")
        start, end = cue.get("start_sec"), cue.get("end_sec")
        if not (_finite_number(start) and _finite_number(end) and 0 <= start < end <= total):
            errors.append(f"{location} 时间段必须落在 [0, {total:g}] 秒")
        else:
            duration = end - start
            if duration > 3.5 + 1e-9:
                errors.append(f"{location} 时长超过 3.5 秒")
            elif kind == "silence_drop" and not 0.25 - 1e-9 <= duration <= 0.90 + 1e-9:
                warnings.append(f"{location} silence_drop 时长建议为 0.25..0.90 秒")
        depth = cue.get("depth_db", -28.0 if kind == "delay_entry" else 0.0)
        if not _finite_number(depth) or (kind == "silence_drop" and depth > -12.0):
            errors.append(f"{location}.depth_db 无效，silence_drop 需要小于等于 -12 dB")
        fade = cue.get("fade_ms", 80)
        if type(fade) is not int or fade < 0:
            errors.append(f"{location}.fade_ms 必须是非负整数")
        reason = cue.get("reason", "")
        if not isinstance(reason, str):
            errors.append(f"{location}.reason 必须是字符串")
        cleaned.append(dict(cue, depth_db=depth, fade_ms=fade, reason=reason))
    return cleaned


def _span(sentences: List[Dict[str, Any]]) -> tuple[float, float]:
    return sentences[0]["start_sec"], max(s["end_sec"] for s in sentences)


def _coverage(spans: List[tuple[float, float]], total: float) -> float:
    duration = previous_end = 0.0
    for start, end in sorted(spans):
        duration += max(0.0, end - max(start, previous_end))
        previous_end = max(previous_end, end)
    return duration / total if total else 0.0


def validate_director_plan(
    plan: Dict[str, Any], audio_events: Dict[str, Any],
    composition_profile: Optional[Dict[str, Any]] = None,
    valid_broll_names: Optional[set[str]] = None,
) -> Dict[str, Any]:
    """校验总谱、封面文案、broll_clips 实拍白名单及同幕溯源，按锁定时间重算高光覆盖率。"""
    errors: List[str] = []
    warnings: List[str] = []
    result = {"ok": False, "errors": errors, "warnings": warnings, "sanitized_plan": {}}
    if not isinstance(plan, dict):
        errors.append("导演总谱必须是 JSON 对象")
        return result
    try:
        sentences, total = _timeline(audio_events)
    except ValueError as exc:
        errors.append(str(exc))
        return result

    cleaned = dict(plan)
    for key, default, choices in (
        ("step", "03b_director_plan", ("03b_director_plan",)),
        ("source", "agent", ("agent", "fallback_deterministic")),
        ("theme_profile", None, THEME_PROFILES),
    ):
        value = cleaned.setdefault(key, default)
        if value not in choices:
            errors.append(f"{key} 不支持 {value!r}")

    if "native_style_mode" in plan and type(plan.get("native_style_mode")) is not bool:
        errors.append("native_style_mode 必须是布尔值")

    cov_raw = plan.get("cover_copy")
    if cov_raw is not None:
        if not isinstance(cov_raw, dict):
            errors.append("cover_copy 必须是对象")
        else:
            cov_clean: Dict[str, str] = {}
            for ck in ("headline_top", "highlight_badge", "sub_line", "gbro_style", "dish_or_subject"):
                val = cov_raw.get(ck)
                if val is not None:
                    if not isinstance(val, str):
                        errors.append(f"cover_copy.{ck} 必须是字符串")
                    else:
                        cov_clean[ck] = val.strip()
            gstyle = cov_clean.get("gbro_style", "")
            if gstyle and gstyle not in GBRO_STYLES:
                errors.append(f"cover_copy.gbro_style 不支持 {gstyle!r}")
            cleaned["cover_copy"] = cov_clean

    gbro_raw = plan.get("gbro_cover_spec")
    if gbro_raw is not None:
        if not isinstance(gbro_raw, dict):
            errors.append("gbro_cover_spec 必须是对象")
        else:
            gbro_clean: Dict[str, str] = {}
            for gk in ("gbro_style", "dish_or_subject", "expression", "headline_top", "highlight_badge", "sub_line"):
                val = gbro_raw.get(gk)
                if val is not None:
                    if not isinstance(val, str):
                        errors.append(f"gbro_cover_spec.{gk} 必须是字符串")
                    else:
                        gbro_clean[gk] = val.strip()
            gstyle2 = gbro_clean.get("gbro_style", "")
            if gstyle2 and gstyle2 not in GBRO_STYLES:
                errors.append(f"gbro_cover_spec.gbro_style 不支持 {gstyle2!r}")
            cleaned["gbro_cover_spec"] = gbro_clean

    if "gbro_cover_3x4_path" in plan and plan.get("gbro_cover_3x4_path") is not None:
        if not isinstance(plan.get("gbro_cover_3x4_path"), str):
            errors.append("gbro_cover_3x4_path 必须是字符串")

    by_id = {sentence["id"]: sentence for sentence in sentences}
    positions = {sid: i for i, sid in enumerate(by_id)}
    covered: set[str] = set()
    act_ids: set[str] = set()
    previous = -1
    spans: List[tuple[float, float]] = []
    acts = plan.get("acts")
    cleaned["acts"] = []
    if not isinstance(acts, list):
        errors.append("acts 必须是列表")
        acts = []
    for index, act in enumerate(acts):
        path = f"acts[{index}]"
        if not isinstance(act, dict):
            errors.append(f"{path} 必须是对象")
            continue
        act = dict(act)
        cleaned["acts"].append(act)
        aid = act.setdefault("act_id", f"act_{index + 1:02d}")
        if not isinstance(aid, str) or not aid.strip() or aid in act_ids:
            errors.append(f"{path}.act_id 缺失或重复")
        else:
            act_ids.add(aid)
        for key, default, choices in (
            ("primitive", None, DIRECTOR_PRIMITIVES),
            ("camera_intent", None, _CAMERAS),
            ("dramatic_role", "setup", _ROLES),
            ("broll_policy", "lock_aroll", _BROLL_POLICIES),
        ):
            value = act.setdefault(key, default)
            if value not in choices:
                errors.append(f"{path}.{key} 不支持 {value!r}")
        intensity = act.setdefault("emotion_intensity", 0.0)
        if not _finite_number(intensity) or not 0 <= intensity <= 1:
            errors.append(f"{path}.emotion_intensity 必须在 [0, 1] 内")
        budget = act.setdefault("sfx_budget", 0)
        if type(budget) is not int or budget < 0:
            errors.append(f"{path}.sfx_budget 必须是非负整数")

        ids = act.get("sentence_ids")
        anchored = []
        if not isinstance(ids, list) or not ids:
            errors.append(f"{path}.sentence_ids 必须是非空列表")
            ids = []
        for sid in ids:
            if not isinstance(sid, str) or sid not in by_id:
                errors.append(f"unknown_sentence_id {path} 的 {sid!r} 不在锁定句表内")
            elif sid in covered:
                errors.append(f"duplicate_sentence_id {path} 重复分配 {sid!r}")
            else:
                if positions[sid] <= previous:
                    errors.append(f"sentence_order {path} 的 {sid!r} 未按锁定时间排序")
                previous = max(previous, positions[sid])
                covered.add(sid)
                anchored.append(by_id[sid])
        primitive = act["primitive"]
        visual = _validate_copy(act.get("visual_copy", {}), f"{path}.visual_copy", anchored, errors)
        act["visual_copy"] = visual
        raw_kws = act.get("broll_keywords")
        if raw_kws is not None:
            if not isinstance(raw_kws, list) or not all(isinstance(x, str) for x in raw_kws):
                errors.append(f"{path}.broll_keywords 必须是字符串列表")
            else:
                act["broll_keywords"] = list(dict.fromkeys(x.strip() for x in raw_kws if x.strip()))
        raw_clips = act.get("broll_clips")
        if raw_clips is not None:
            if not isinstance(raw_clips, list) or not all(isinstance(x, str) for x in raw_clips):
                errors.append(f"{path}.broll_clips 必须是字符串列表")
            else:
                pin_names = list(dict.fromkeys(x.strip() for x in raw_clips if x.strip()))
                if valid_broll_names is not None:
                    bad_pins = [n for n in pin_names if _media_name(n) not in valid_broll_names and n not in valid_broll_names]
                    if bad_pins:
                        errors.append(f"{path}.broll_clips 指定了不存在或不可用的素材：{bad_pins}")
                act["broll_clips"] = pin_names
        if primitive == "clean_breath" and (budget != 0 or any(visual.values())):
            errors.append(f"{path} clean_breath 需要留空前景大字及主动音效")
        if primitive == "hero_anchor_plus_danmaku" and not _allows_behind_person(composition_profile, anchored):
            errors.append(f"behind_person_not_allowed {path} 的构图不允许人后弹幕")
        if "side_shift_x" in act and act.get("side_shift_x") is not None:
            raw_shift = act.get("side_shift_x")
            if not _finite_number(raw_shift):
                errors.append(f"{path}.side_shift_x 必须是有限数字")
            else:
                act["side_shift_x"] = round(max(-0.12, min(0.12, float(raw_shift))), 4)
        elif act.get("camera_intent") == "side_shift_card":
            act["side_shift_x"] = _resolve_side_shift_x(composition_profile, anchored)
        if primitive in DIRECTOR_PRIMITIVES and primitive not in _QUIET_PRIMITIVES and anchored:
            spans.append(_span(anchored))

    missing = [sid for sid in by_id if sid not in covered]
    if missing:
        errors.append(f"missing_sentence_ids 未分配锁定句：{', '.join(missing)}")
    ratio = _coverage(spans, total)
    cleaned["highlight_coverage_ratio"] = round(ratio, 4)
    is_unified = cleaned.get("theme_profile") == "style_02_unified"
    target_lo, target_hi = (_UNIFIED_TARGET_LOW, _UNIFIED_TARGET_HIGH) if is_unified else (_TARGET_LOW, _TARGET_HIGH)
    warn_lo, warn_hi = (0.35, 0.92) if is_unified else (0.25, 0.70)
    if sentences and not target_lo <= ratio <= target_hi:
        if not warn_lo <= ratio <= warn_hi:
            warnings.append(f"highlight_coverage_ratio {ratio:.2%} 超出 {warn_lo:.0%}..{warn_hi:.0%} 校验带")
        else:
            warnings.append(f"highlight_coverage_ratio {ratio:.2%} 偏离 {target_lo:.0%}..{target_hi:.0%} 目标带")
    cleaned["music_cues"] = _validate_music(plan.get("music_cues", []), total, errors, warnings)

    if "semantic_subtitles" in plan and plan.get("semantic_subtitles") is not None:
        sem_raw = plan.get("semantic_subtitles")
        if not isinstance(sem_raw, list):
            errors.append("semantic_subtitles 必须是列表")
        else:
            sem_clean: List[Dict[str, Any]] = []
            for idx_s, item in enumerate(sem_raw):
                if isinstance(item, str):
                    txt_s = item.strip()
                    kws_s: List[str] = []
                elif isinstance(item, dict):
                    txt_s = str(item.get("text") or "").strip()
                    kws_raw = item.get("keywords", [])
                    if not isinstance(kws_raw, list) or not all(isinstance(k, str) for k in kws_raw):
                        errors.append(f"semantic_subtitles[{idx_s}].keywords 必须是字符串列表")
                        kws_s = []
                    else:
                        kws_s = [k.strip() for k in kws_raw if k.strip() and k.strip() in txt_s]
                else:
                    errors.append(f"semantic_subtitles[{idx_s}] 必须是对象或字符串")
                    continue
                if not txt_s:
                    errors.append(f"semantic_subtitles[{idx_s}].text 不能为空")
                    continue
                sem_clean.append({"text": txt_s, "keywords": kws_s})
            cleaned["semantic_subtitles"] = sem_clean

    beat_wins: List[tuple[float, float]] = []
    if "style_scene_beats" in plan and plan.get("style_scene_beats") is not None:
        beats_raw = plan.get("style_scene_beats")
        if not isinstance(beats_raw, list):
            errors.append("style_scene_beats 必须是列表")
        else:
            beats_clean: List[Dict[str, Any]] = []
            for idx_b, beat in enumerate(beats_raw):
                if not isinstance(beat, dict):
                    errors.append(f"style_scene_beats[{idx_b}] 必须是对象")
                    continue
                marker = _contains_frame8_marker(beat)
                if marker:
                    errors.append(f"style_scene_beats[{idx_b}] 含禁止通用黑卡标记 {marker!r}")
                lay = beat.get("layout")
                if not isinstance(lay, dict) or not str(lay.get("mode") or "").strip():
                    errors.append(f"style_scene_beats[{idx_b}].layout.mode 缺失")
                t0 = beat.get("target_sec")
                if t0 is not None and (not _finite_number(t0) or float(t0) < 0.0):
                    errors.append(f"style_scene_beats[{idx_b}].target_sec 无效")
                tdur = beat.get("target_dur")
                if tdur is not None and (not _finite_number(tdur) or float(tdur) <= 0.0):
                    errors.append(f"style_scene_beats[{idx_b}].target_dur 无效")
                if _finite_number(t0):
                    dur_v = float(tdur) if _finite_number(tdur) else float(beat.get("max_dur") or 3.0)
                    beat_wins.append((float(t0), float(t0) + max(0.2, dur_v)))
                beats_clean.append(dict(beat))
            cleaned["style_scene_beats"] = beats_clean

    if "style_callouts" in plan and plan.get("style_callouts") is not None:
        co_raw = plan.get("style_callouts")
        if not isinstance(co_raw, list):
            errors.append("style_callouts 必须是列表")
        else:
            co_clean: List[Dict[str, Any]] = []
            for idx_c, co in enumerate(co_raw):
                if not isinstance(co, dict):
                    errors.append(f"style_callouts[{idx_c}] 必须是对象")
                    continue
                marker = _contains_frame8_marker(co)
                if marker:
                    errors.append(f"style_callouts[{idx_c}] 含禁止通用黑卡标记 {marker!r}")
                ctxt = str(co.get("text") or "").strip()
                if not ctxt:
                    errors.append(f"style_callouts[{idx_c}].text 不能为空")
                cs, ce = co.get("start_sec"), co.get("end_sec")
                if not (_finite_number(cs) and _finite_number(ce) and 0.0 <= float(cs) < float(ce) <= total + 0.05):
                    errors.append(f"style_callouts[{idx_c}] 时间段必须落在 [0, {total:g}] 秒")
                else:
                    for bs, be in beat_wins:
                        if min(float(ce), be) - max(float(cs), bs) > 0.25:
                            errors.append(f"style_callouts[{idx_c}] 与 style_scene_beats [{bs:.2f}, {be:.2f}] 重叠")
                            break
                co_clean.append(dict(co))
            cleaned["style_callouts"] = co_clean

    result["sanitized_plan"] = cleaned
    result["ok"] = not errors
    return result


def _visual_copy(headline: str = "", items: tuple[str, ...] = (), accent: str = "") -> Dict[str, Any]:
    return {"headline": headline, "items": list(items), "accent_token": accent, "footnote": ""}


def _is_short_phrase(phrase: str, limit: int = _DISPLAY_LIMIT, minimum: int = 2) -> bool:
    return (
        minimum <= _display_length(phrase) <= limit
        and bool(_HAN.search(phrase) or _LATIN.search(_normalize(phrase)))
    )


def _phrase(text: str, limit: int = _DISPLAY_LIMIT, extra_terms: Optional[List[str]] = None) -> str:
    for clause in _CLAUSE.split(text):
        phrase = clause.strip(_TRIM)
        if _is_short_phrase(phrase, limit) and (
            not extra_terms or not _breaks_atomic_or_word_boundary(phrase, text, extra_terms=extra_terms)
        ):
            return phrase
    return ""


def _coalesce_protected_words(words: List[str], extra_terms: Optional[List[str]]) -> List[str]:
    """合并被分词器拆散的受保护词，确保后续词界扫描把整词视为不可拆原子。"""
    if len(words) < 2 or not extra_terms:
        return words
    terms = sorted({str(t).strip() for t in extra_terms if len(str(t).strip()) >= 2}, key=len, reverse=True)
    if not terms:
        return words
    joined = "".join(words)
    spans: List[tuple[int, int]] = []
    for tm in terms:
        pos = 0
        while True:
            idx = joined.find(tm, pos)
            if idx < 0:
                break
            spans.append((idx, idx + len(tm)))
            pos = idx + 1
    if not spans:
        return words
    spans.sort(key=lambda x: (x[0], -(x[1] - x[0])))
    merged_spans: List[tuple[int, int]] = []
    for sa, sb in spans:
        if not merged_spans or sa >= merged_spans[-1][1]:
            merged_spans.append((sa, sb))
    out: List[str] = []
    offset = 0
    buf = ""
    active_end = -1
    for w in words:
        w_start = offset
        w_end = offset + len(w)
        offset = w_end
        if active_end > w_start:
            buf += w
            if w_end >= active_end:
                out.append(buf)
                buf = ""
                active_end = -1
            continue
        hit = next(((sa, sb) for sa, sb in merged_spans if sa <= w_start < sb and w_end < sb), None)
        if hit is not None:
            buf = w
            active_end = hit[1]
            continue
        out.append(w)
    if buf:
        out.append(buf)
    return out


@lru_cache(maxsize=1)
def _offline_tokenizer() -> Callable[[str], List[str]]:
    """结合 M1 原子数字/字母单位跨度保护与独立 jieba(HMM=False) 分词，既不断裂 2.4G/600平，又保持既有词界契约。"""
    try:
        from jieba import Tokenizer
        tok = Tokenizer()
        tok.initialize()
    except ImportError:
        from step5_subtitles import tokenize_semantic_words

        terms = {word for word in _CONCLUSION_BOUNDARIES if len(word) > 1}
        terms.update(("自己", "我们", "我們", "你们", "你們", "他们", "他們", "她们", "她們", "咱们", "咱們"))
        return partial(tokenize_semantic_words, terms=sorted(terms))

    def _cut_with_atomic_spans(text: str) -> List[str]:
        s = str(text or "")
        if not s:
            return []
        spans = _atomic_alnum_spans(s)
        if not spans:
            return list(tok.lcut(s, HMM=False))
        out: List[str] = []
        pos = 0
        for sa, sb in spans:
            if sa > pos:
                out.extend(tok.lcut(s[pos:sa], HMM=False))
            out.append(s[sa:sb])
            pos = sb
        if pos < len(s):
            out.extend(tok.lcut(s[pos:], HMM=False))
        return [w for w in out if w]

    return _cut_with_atomic_spans


def _coalesce_atomic_words(words: List[str]) -> List[str]:
    """合并被 ASR 拆碎的数字/字母单位原子词（如 '2.4' + 'G' -> '2.4G'）。"""
    if len(words) < 2:
        return words
    joined = "".join(words)
    spans = _atomic_alnum_spans(joined)
    if not spans:
        return words
    out: List[str] = []
    offset = 0
    buf = ""
    buf_start = 0
    active_end = -1
    for w in words:
        w_start = offset
        w_end = offset + len(w)
        offset = w_end
        if active_end > w_start:
            buf += w
            if w_end >= active_end:
                out.append(buf)
                buf = ""
                active_end = -1
            continue
        hit = next(((sa, sb) for sa, sb in spans if sa <= w_start < sb and w_end < sb), None)
        if hit is not None:
            buf = w
            buf_start = w_start
            active_end = hit[1]
            continue
        out.append(w)
    if buf:
        out.append(buf)
    return out


def _word_sequences(
    text: str, sentences: List[Dict[str, Any]], extra_terms: Optional[List[str]] = None,
) -> Iterator[tuple[str, ...]]:
    """只在完整原句分词的边界上截取词组。"""
    for sentence in sentences:
        rows = sentence.get("words")
        if not isinstance(rows, list):
            continue
        words = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            value = row.get("text") or row.get("word")
            if isinstance(value, str):
                word = unicodedata.normalize("NFKC", value).strip(_TRIM)
                if word:
                    words.append(word)
        words = _coalesce_protected_words(_coalesce_atomic_words(words), extra_terms)
        start = "".join(words).find(text)
        if start < 0:
            continue
        stop = start + len(text)
        chunk, offset = [], 0
        for word in words:
            end = offset + len(word)
            if start <= offset and end <= stop:
                chunk.append(word)
            if end >= stop:
                break
            offset = end
        if len(chunk) > 1 and any(len(word) > 1 for word in chunk) and "".join(chunk) == text:
            yield tuple(chunk)

    raw_toks = _atomic_semantic_tokens(text, extra_terms=extra_terms) if extra_terms else _offline_tokenizer()(text)
    words = tuple(_coalesce_protected_words(list(raw_toks), extra_terms))
    if len(words) > 1 and "".join(words) == text:
        yield words


def _conclusion_tail(
    words: tuple[str, ...],
    sentences: List[Dict[str, Any]],
    limit: int,
    *,
    full_text: str = "",
    extra_terms: Optional[List[str]] = None,
) -> str:
    starts = []
    length = 0
    for i in range(len(words) - 1, -1, -1):
        word = words[i]
        length += _display_length(word)
        if length > limit:
            break
        if length >= 2 and word.strip(_TRIM) and word not in _CONCLUSION_WEAK_START:
            if len(word) == 1 and (
                word in _BOUND_NOUN_SUFFIX_BEFORE_COPULA
                or (
                    i + 1 < len(words)
                    and words[i + 1] in ("是", "系", "係")
                    and word not in ("这", "那", "谁", "哪", "不", "也", "都", "先", "再")
                )
            ):
                continue
            starts.append(i)

    ref_src = full_text or "".join(words)
    for i in reversed(starts):
        suffix = "".join(words[i:])
        if _is_short_phrase(suffix, limit) and (
            not extra_terms or not _breaks_atomic_or_word_boundary(suffix, ref_src, extra_terms=extra_terms)
        ):
            errors: List[str] = []
            _validate_copy({"headline": suffix}, "visual_copy", sentences, errors)
            if not errors:
                return suffix
    return ""


def _short_conclusion(
    text: str,
    sentences: List[Dict[str, Any]],
    limit: int = _DISPLAY_LIMIT,
    *,
    allow_tail: bool = True,
    extra_terms: Optional[List[str]] = None,
) -> str:
    if phrase := _phrase(text, limit, extra_terms=extra_terms):
        return phrase
    clauses = [part.strip(_TRIM) for part in _CLAUSE.split(text) if part.strip(_TRIM)]
    tail_words = []
    for index, clause in enumerate(clauses):
        for words in _word_sequences(clause, sentences, extra_terms=extra_terms):
            for i in range(len(words) - 2, -1, -1):
                if words[i] in _CONCLUSION_BOUNDARIES:
                    suffix = "".join(words[i + 1:])
                    if (
                        _is_short_phrase(suffix, limit)
                        and suffix not in _CONCLUSION_BOUNDARIES
                        and (not extra_terms or not _breaks_atomic_or_word_boundary(suffix, clause, extra_terms=extra_terms))
                    ):
                        return suffix
            if allow_tail and index == len(clauses) - 1:
                tail_words.append(words)

    for words in tail_words:
        if suffix := _conclusion_tail(
            words, sentences or [{"text": text}], limit, full_text=text, extra_terms=extra_terms,
        ):
            return suffix
    return ""


def _numeric_focus(text: str) -> tuple[str, str]:
    best_score = None
    best = ("", "")
    atomic_spans = _atomic_alnum_spans(text)
    for match in _NUMBER.finditer(text):
        value, unit, ordinal, bound = _number_key(match)
        if ordinal or not isinstance(value, Fraction):
            continue
        magnitude = abs(value)
        if magnitude == 1 and unit in ("", "个", "间", "次", "句", "家"):
            continue
        if not (unit or magnitude >= 10 or bound):
            continue
        ms, me = match.span()
        enclosing = next(
            ((sa, sb) for sa, sb in atomic_spans if sa <= ms and me <= sb and (sa, sb) != (ms, me)),
            None,
        )
        if enclosing is not None:
            sa, sb = enclosing
            atom_txt = text[sa:sb]
            if atom_txt[:1].isascii() and atom_txt[:1].isalpha():
                continue
            compact_atom = _compact_zh_spaces(atom_txt)
            if _display_length(compact_atom) > _DISPLAY_LIMIT:
                continue
            label = _AMOUNT_LABEL.search(text[:sa])
            number = compact_atom
            strong = any(ch in atom_txt for ch in "万萬亿億") or bool(re.search(r"(?:Hz|hz|fps|帧|分贝|平|元|%|％)", atom_txt))
            score = (strong, magnitude, label is not None)
            if best_score is None or score > best_score:
                headline = label[0].strip() + number if label else number
                best = (headline if _display_length(headline) <= _DISPLAY_LIMIT else number, number)
                best_score = score
            continue
        label = _AMOUNT_LABEL.search(text[:match.start()])
        raw_value = match["value"]
        number = match[0].strip()
        if label and unit in ("", "元") and value >= 10000 and value % 10000 == 0 and bound != "minimum":
            number = f"{value // 10000}万" + ("+" if bound == "over" else "")
        elif number[-1] in _SCALES and any(ch in raw_value for ch in "多余餘"):
            number = number.translate(str.maketrans("", "", "多余餘")) + "+"
        if _display_length(number) > _DISPLAY_LIMIT:
            continue
        strong = unit in ("平", "元", "%", "亩") or any(ch in raw_value for ch in "万萬亿億")
        score = (strong, magnitude, label is not None)
        if best_score is None or score > best_score:
            headline = label[0].strip() + number if label else number
            best = (headline if _display_length(headline) <= _DISPLAY_LIMIT else number, number)
            best_score = score
    return best


def _group_text(sentences: List[Dict[str, Any]]) -> str:
    return "，".join(unicodedata.normalize("NFKC", s.get("text", "")).strip(_TRIM) for s in sentences)


def _ordinals(text: str) -> List[re.Match[str]]:
    return [
        match for match in _NUMBER.finditer(text)
        if match["ordinal"] and not text[match.end("value"):].lstrip().startswith(_NOT_ENUMERATION)
    ]


def _ordinal_body(text: str) -> str:
    text = text.lstrip()
    if text and text[0] in _ENUMERATION_SUFFIXES:
        text = text[1:]
    return text.strip(_TRIM)


def _list_word_options(
    part: str,
    sentences: List[Dict[str, Any]],
    expected: Optional[int],
    extra_terms: Optional[List[str]] = None,
) -> Iterator[tuple[str, ...]]:
    for words in _word_sequences(part, sentences, extra_terms=extra_terms):
        if all(_is_short_phrase(word) for word in words):
            yield words
        elif expected:
            # 明确报出总项数时，允许字级分词的相邻单字组成双字词。
            items = []
            single = ""
            for word in words:
                if len(word) == 1:
                    if single:
                        items.append(single + word)
                        single = ""
                    else:
                        single = word
                elif single:
                    break
                else:
                    items.append(word)
            else:
                if not single and len(items) > 1 and all(_is_short_phrase(item) for item in items):
                    yield tuple(items)


def _list_items(
    tail: str,
    sentences: List[Dict[str, Any]],
    lead: re.Match[str],
    extra_terms: Optional[List[str]] = None,
) -> tuple[str, ...]:
    count = lead["count"]
    expected = int(count.translate(_CN_TO_DIGITS)) if count else None
    body = re.split(r"[。！？!?]", tail, maxsplit=1)[0]
    parts = [part.strip(_TRIM) for part in _LIST_JOIN.split(body) if part.strip(_TRIM)]
    if (len(parts) == expected or (expected is None and len(parts) >= 2)) and all(
        _is_short_phrase(part, minimum=1) for part in parts
    ):
        return tuple(parts[:4])
    if expected is None:
        if len(parts) == 1:
            return next(_list_word_options(parts[0], sentences, None, extra_terms=extra_terms), ())[:4]
        return ()

    by_count: Dict[int, tuple[str, ...]] = {0: ()}
    for part in parts:
        options = [(part,)] if _is_short_phrase(part, minimum=1) else []
        options.extend(_list_word_options(part, sentences, expected, extra_terms=extra_terms))
        splits: Dict[int, tuple[str, ...]] = {}
        for previous in by_count.values():
            for option in options:
                items = previous + option
                if len(items) <= expected:
                    splits.setdefault(len(items), items)
        by_count = splits
    return by_count.get(expected, ())[:4]


def _group_end(sentences: List[Dict[str, Any]], index: int, store: bool) -> int:
    """把相邻的序数结论、并列项或反转分句留在同一幕。"""
    end = index + 1
    first = _group_text(sentences[index:end])
    max_gap = 1.2 + 1e-9
    if end == len(sentences) or sentences[end]["start_sec"] - sentences[index]["end_sec"] > max_gap:
        return end

    following = _group_text(sentences[end:end + 1])
    ordinals = _ordinals(first)
    if len(ordinals) == 1:
        marker = ordinals[0]
        if not first[:marker.start()].strip(_TRIM) and not _ordinal_body(first[marker.end():]):
            return end + 1 if following and not _ordinals(following) else end

    lead = _LIST_LEAD.search(first) if store else None
    if lead and not first[lead.end():].strip(_TRIM) and _list_items(following, sentences[end:end + 1], lead):
        return end + 1

    if _CONTRAST.search(first) or "≠" in first or not (_NEGATION.search(first) or _numeric_focus(first)[0]):
        return end
    for sentence in sentences[end:end + 2]:
        text = _group_text([sentence])
        if (sentence["start_sec"] - sentences[end - 1]["end_sec"] > max_gap
                or _CONTRAST.search(text) or "≠" in text):
            break
        after_negation = end > index + 1 or _NEGATION.search(first)
        positive = after_negation and text.startswith(("是", "而是", "而系", "而係"))
        if not (_NEGATION.match(text) or positive):
            break
        end += 1
    joined = _group_text(sentences[index:end])
    return end if len(_NEGATION.findall(joined)) >= 2 or _CONTRAST.search(joined) else index + 1


def _negation_items(
    text: str, contrast: Optional[re.Match[str]], extra_terms: Optional[List[str]] = None,
) -> tuple[str, ...]:
    matches = list(_NEGATION.finditer(text))
    items = []
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        if contrast and match.end() == contrast.start("rejected"):
            end = min(end, contrast.end("rejected"))
        predicate = _CLAUSE.split(text[match.end():end], maxsplit=1)[0].strip(_TRIM)
        if predicate and (phrase := _phrase(match[0].lstrip("也更都仲亦") + predicate, extra_terms=extra_terms)):
            if not extra_terms or not _breaks_atomic_or_word_boundary(phrase, text, extra_terms=extra_terms):
                items.append(f"✕ {phrase}")
    return tuple(dict.fromkeys(items))


def _framework_copy(
    text: str,
    ordinals: List[re.Match[str]],
    sentences: List[Dict[str, Any]],
    extra_terms: Optional[List[str]] = None,
) -> Dict[str, Any]:
    rows: List[tuple[int, str, str]] = []
    for i, match in enumerate(ordinals):
        end = ordinals[i + 1].start() if i + 1 < len(ordinals) else len(text)
        value = _amount(match["value"])
        if value is None or value < 1 or value != int(value):
            continue
        body_raw = _ordinal_body(text[match.end():end])
        title = _short_conclusion(body_raw, sentences, limit=6, extra_terms=extra_terms)
        if title:
            rows.append((i, f"{int(value):02d}", title))
    if not rows:
        return _visual_copy()
    _, number, title = rows[0]
    items_out: List[str] = []
    for orig_i, badge, label in rows[1:]:
        if _display_length(badge + label) <= _DISPLAY_LIMIT:
            items_out.append(f"{badge} · {label}")
            continue
        end = ordinals[orig_i + 1].start() if orig_i + 1 < len(ordinals) else len(text)
        body_raw = _ordinal_body(text[ordinals[orig_i].end():end])
        short_label = _short_conclusion(
            body_raw, sentences, limit=max(2, _DISPLAY_LIMIT - len(badge)), extra_terms=extra_terms,
        )
        if short_label and _display_length(badge + short_label) <= _DISPLAY_LIMIT:
            items_out.append(f"{badge} · {short_label}")
        else:
            items_out.append(label)
    return _visual_copy(title, tuple(items_out), number)


def _grounded_short_copy(
    text: str, sentences: List[Dict[str, Any]], extra_terms: Optional[List[str]] = None,
) -> str:
    """先按完整短句取词；取不到再放开句尾截取，但仍必须能溯源，取不到就返回空串。"""
    if not text:
        return ""
    if phrase := _short_conclusion(text, sentences, allow_tail=False, extra_terms=extra_terms):
        return phrase
    phrase = _short_conclusion(text, sentences, allow_tail=True, extra_terms=extra_terms)
    if not phrase:
        return ""
    if extra_terms and _breaks_atomic_or_word_boundary(phrase, text, extra_terms=extra_terms):
        return ""
    errors: List[str] = []
    _validate_copy({"headline": phrase}, "visual_copy", sentences, errors)
    return "" if errors else phrase


def _sentence_prosody_metrics(
    sentences: List[Dict[str, Any]], audio_events: Optional[Dict[str, Any]] = None,
) -> Dict[str, Dict[str, Any]]:
    """计算每句相对语速减速比、句内声学大停顿与能量/重音事件密度，实现真·以音定画。"""
    cps_list: List[float] = []
    raw_info: Dict[str, Dict[str, Any]] = {}
    events = (audio_events or {}).get("events") or []
    events_by_sid: Dict[str, int] = {}
    for ev in events:
        if not isinstance(ev, dict):
            continue
        if ev.get("type") in ("stress", "energy_peak", "emphasis"):
            sid_e = str(ev.get("sentence_id") or "")
            if sid_e:
                events_by_sid[sid_e] = events_by_sid.get(sid_e, 0) + 1

    for s in sentences:
        sid = str(s.get("id") or "")
        plain = re.sub(r"[\s,，。！？!?、；;：:「」『』“”]+", "", str(s.get("text") or ""))
        dur = max(0.12, float(s.get("end_sec", 0.0)) - float(s.get("start_sec", 0.0)))
        cps = len(plain) / dur if plain else 0.0
        if len(plain) >= 5:
            cps_list.append(cps)
        ws = [w for w in (s.get("words") or []) if isinstance(w, dict)]
        max_gap = max(
            (float(ws[i + 1].get("start_sec", 0.0)) - float(ws[i].get("end_sec", 0.0)) for i in range(len(ws) - 1)),
            default=0.0,
        )
        raw_info[sid] = {
            "clean_len": len(plain),
            "dur": dur,
            "cps": cps,
            "max_gap": max(0.0, max_gap),
            "pause_after": max(0.0, float(s.get("pause_after_sec") or 0.0)),
            "stress_count": events_by_sid.get(sid, 0),
        }

    median_cps = statistics.median(cps_list) if cps_list else 5.0
    for info in raw_info.values():
        slow_ratio = info["cps"] / max(1.0, median_cps) if info["clean_len"] >= 5 else 1.0
        info["slowdown_ratio"] = round(slow_ratio, 3)
        info["acoustic_emphasis"] = bool(
            (slow_ratio <= 0.82 and info["clean_len"] >= 6)
            or info["max_gap"] >= 0.28
            or (info["stress_count"] >= 3 and slow_ratio <= 0.90)
        )
    return raw_info


def _group_prosody(group: List[Dict[str, Any]], prosody: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    rows = [prosody.get(str(s.get("id") or ""), {}) for s in group]
    return {
        "slowdown_ratio": min((r.get("slowdown_ratio", 1.0) for r in rows), default=1.0),
        "max_gap": max((r.get("max_gap", 0.0) for r in rows), default=0.0),
        "stress_count": sum(int(r.get("stress_count", 0)) for r in rows),
        "acoustic_emphasis": any(r.get("acoustic_emphasis", False) for r in rows),
    }


def _extract_trio_diagram(
    text: str,
    sentences: List[Dict[str, Any]],
    gp: Dict[str, Any],
    extra_terms: Optional[List[str]] = None,
) -> Optional[Dict[str, Any]]:
    """捕获「三元并列 + 反转结果」结构（如 `分工·计酬·监督` -> `一雇人 / ✕ 三样全碎`）。"""
    clean = re.sub(r"[，。！？、；：,.!?;:\s]+", "", text)
    if len(clean) < 10 or _NEGATION.search(text) or _ordinals(text):
        return None
    has_collapse = bool(re.search(r"(?:全碎|全无|全乱|崩|失灵|没了|瓦解|全毁|乱套)", clean))
    if not (has_collapse or (gp.get("max_gap", 0.0) >= 0.30 and gp.get("slowdown_ratio", 1.0) <= 0.85)):
        return None

    trio: Optional[tuple[str, str, str]] = None
    trio_end_in_clean = -1
    m_punct = re.search(r"([\u4e00-\u9fff]{2})[、,，]\s*([\u4e00-\u9fff]{2})[、,，]\s*([\u4e00-\u9fff]{2})", text)
    if m_punct:
        trio = (m_punct.group(1), m_punct.group(2), m_punct.group(3))
        trio_end_in_clean = clean.find("".join(trio)) + 6
    else:
        raw_toks = _atomic_semantic_tokens(clean, extra_terms=extra_terms) if extra_terms else _offline_tokenizer()(clean)
        toks = [tk for tk in raw_toks if tk.strip()]
        bad_toks = {"我们", "你们", "他们", "自己", "大家", "什么", "这样", "那样", "天然", "就有", "就是", "因为", "所以", "两个", "三个"}
        for i in range(len(toks) - 2):
            a, b, c = toks[i], toks[i + 1], toks[i + 2]
            if (len(a) == 2 and len(b) == 2 and len(c) == 2
                    and all(_HAN.fullmatch(x) and x not in bad_toks for x in (a, b, c))
                    and not any(ch in _CONNECTIVES for x in (a, b, c) for ch in x)):
                idx_pos = clean.find(a + b + c)
                if idx_pos >= 0:
                    trio = (a, b, c)
                    trio_end_in_clean = idx_pos + 6
                    break
    if not trio or trio_end_in_clean < 0:
        return None

    headline = f"{trio[0]}·{trio[1]}·{trio[2]}"
    tail = clean[trio_end_in_clean:]
    m_break = re.search(r"(一[\u4e00-\u9fff]{1,3}?)(三样[\u4e00-\u9fff]{1,3}|全[\u4e00-\u9fff]{1,3}|都[\u4e00-\u9fff]{1,3}|全碎|没了|乱套)", tail)
    items: tuple[str, ...]
    if m_break:
        act_part = m_break.group(1).strip(_TRIM)
        res_part = m_break.group(2).rstrip("了的嘅").strip(_TRIM)
        items = (act_part, f"✕ {res_part}")
    else:
        short_res = _grounded_short_copy(tail, sentences, extra_terms=extra_terms)
        items = (f"✕ {short_res}",) if short_res else ()
    if not items or any(_display_length(x) > _DISPLAY_LIMIT for x in (headline, *items)):
        return None
    errs: List[str] = []
    vis = _validate_copy(_visual_copy(headline, items, ""), "visual_copy", sentences, errs)
    return None if errs else vis


def _extract_couplet_diagram(
    text: str,
    sentences: List[Dict[str, Any]],
    gp: Dict[str, Any],
    extra_terms: Optional[List[str]] = None,
) -> Optional[Dict[str, Any]]:
    """捕获对称节奏双短句金句（如 `招人找能人 / 管人抄制度`），在无标点或有停顿口播中自动拆成双栏图卡。"""
    if _NEGATION.search(text) or _CONTRAST.search(text) or _ordinals(text) or _CTA.search(text):
        return None
    clauses = [c.strip(_TRIM) for c in _CLAUSE.split(text) if c.strip(_TRIM)]
    if len(clauses) == 2:
        a, b = clauses
        if (4 <= _display_length(a) <= 6 and 4 <= _display_length(b) <= 6
                and abs(_display_length(a) - _display_length(b)) <= 1
                and _HAN.fullmatch(a) and _HAN.fullmatch(b)):
            anchor_match = any(
                k < len(a) and k < len(b) and a[k] == b[k] and a[k] not in _CONNECTIVES
                for k in range(min(3, len(a), len(b)))
            )
            if anchor_match or gp.get("acoustic_emphasis"):
                errs: List[str] = []
                vis = _validate_copy(_visual_copy("", (a, b), ""), "visual_copy", sentences, errs)
                if not errs:
                    return vis
    elif len(clauses) == 1:
        plain = clauses[0]
        if 8 <= len(plain) <= 12 and _HAN.fullmatch(plain) and (gp.get("slowdown_ratio", 1.0) <= 0.84 or gp.get("acoustic_emphasis")):
            raw_toks = _atomic_semantic_tokens(plain, extra_terms=extra_terms) if extra_terms else _offline_tokenizer()(plain)
            toks = [tk for tk in raw_toks if tk.strip()]
            bounds = []
            acc = 0
            for tk in toks[:-1]:
                acc += len(tk)
                bounds.append(acc)
            mid = len(plain) // 2
            cands = sorted(
                (k for k in bounds if 4 <= k <= 6 and 4 <= len(plain) - k <= 6),
                key=lambda k: abs(k - mid),
            )
            for k in cands:
                a, b = plain[:k], plain[k:]
                if extra_terms and (
                    _breaks_atomic_or_word_boundary(a, plain, extra_terms=extra_terms)
                    or _breaks_atomic_or_word_boundary(b, plain, extra_terms=extra_terms)
                ):
                    continue
                anchor_match = any(
                    idx_c < len(a) and idx_c < len(b) and a[idx_c] == b[idx_c] and a[idx_c] not in _CONNECTIVES
                    for idx_c in range(min(3, len(a), len(b)))
                )
                if anchor_match:
                    errs = []
                    vis = _validate_copy(_visual_copy("", (a, b), ""), "visual_copy", sentences, errs)
                    if not errs:
                        return vis
    return None


def _extract_cta_copy(
    text: str,
    call: re.Match[str],
    sentences: List[Dict[str, Any]],
    extra_terms: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """提取带动作感与针对性的 CTA 文案（如 `私信我` + `答不上来`，告别孤零零二字 `私信`）。"""
    raw = text[call.start():].split("，", 1)[0].split("。", 1)[0].strip(_TRIM)
    cta_head = call[0]
    m_ext = re.match(rf"{re.escape(cta_head)}(?:我|你|见|吧|领|聊|讲下?|扣\d+)?", raw)
    if m_ext and 2 <= _display_length(m_ext.group(0)) <= _DISPLAY_LIMIT:
        cta_head = m_ext.group(0)
    pre_clause = text[:call.start()].strip(_TRIM)
    sub = ""
    m_trigger = re.search(r"(答不上来|搞不定|看不懂|记不住|不会做|没思路|拿不准)", pre_clause)
    if m_trigger:
        sub = m_trigger.group(1)
    elif pre_clause and not re.search(r"(?:唔知道|不知道|如果|只要)", pre_clause[-6:]):
        cand_sub = _short_conclusion(pre_clause, sentences, limit=_DISPLAY_LIMIT, allow_tail=True, extra_terms=extra_terms)
        if cand_sub and cand_sub != cta_head and 2 <= _display_length(cand_sub) <= 5 and cand_sub[-1:] not in "的嘅了我":
            sub = cand_sub
    items = (sub,) if sub else ()
    errs: List[str] = []
    vis = _validate_copy(_visual_copy(cta_head, items, ""), "visual_copy", sentences, errs)
    return _visual_copy(call[0]) if errs else vis


def _guard_proposed_visual(
    primitive: str,
    visual: Dict[str, Any],
    text: str,
    extra_terms: Optional[List[str]],
) -> tuple[str, Dict[str, Any]]:
    if not extra_terms:
        return primitive, visual
    out = dict(visual)
    hl = str(out.get("headline") or "").strip()
    if hl and _breaks_atomic_or_word_boundary(hl.lstrip("✕✓ "), text, extra_terms=extra_terms):
        out["headline"] = ""
    clean_items: List[str] = []
    for it in (out.get("items") or []):
        its = str(it or "").strip()
        chk_it = its.split(" · ", 1)[-1].lstrip("✕✓ ")
        if chk_it and _breaks_atomic_or_word_boundary(chk_it, text, extra_terms=extra_terms):
            continue
        clean_items.append(its)
    out["items"] = clean_items
    if primitive == "framework_pillar" and not out["headline"]:
        return "plain_bridge", _visual_copy()
    if not (out["headline"] or out["items"] or out.get("accent_token")):
        return "plain_bridge", _visual_copy()
    return primitive, out


def _propose(
    text: str, sentences: List[Dict[str, Any]], store: bool, opening: bool, behind: bool,
    gp: Optional[Dict[str, Any]] = None,
    extra_terms: Optional[List[str]] = None,
) -> tuple[str, Dict[str, Any]]:
    gp_info = gp or {}
    contrast = _CONTRAST.search(text)
    if "≠" in text or (contrast and contrast["core"]):
        if "≠" in text:
            left, right = text.split("≠", 1)
            subject = ""
        else:
            left, right = contrast["rejected"], contrast["accepted"]
            subject = _phrase(text[:contrast.start()].rstrip("的嘅 "), extra_terms=extra_terms)
        rejected = _short_conclusion(left, sentences, allow_tail=False, extra_terms=extra_terms)
        accepted = _short_conclusion(right, sentences, allow_tail=False, extra_terms=extra_terms)
        if rejected and accepted:
            sides = (f"✕ {rejected}", f"✓ {accepted}")
            return _guard_proposed_visual("concept_split_card", _visual_copy(subject or accepted, sides, accepted), text, extra_terms)
        return "plain_bridge", _visual_copy()

    if contrast or len(_NEGATION.findall(text)) >= 2:
        negations = _negation_items(text, contrast, extra_terms=extra_terms)
        if negations:
            headline, accent = _numeric_focus(text)
            if not headline:
                # 反转幕主标题：完整短句取不到时放开句尾截取，但过了溯源才算数。
                headline = (
                    _grounded_short_copy(contrast["accepted"], sentences, extra_terms=extra_terms)
                    if contrast else negations[0][2:]
                )
            return _guard_proposed_visual("negation_strike", _visual_copy(headline, negations, accent), text, extra_terms)

    ordinals = _ordinals(text)
    if ordinals:
        visual = _framework_copy(text, ordinals, sentences, extra_terms=extra_terms)
        if visual["headline"]:
            return _guard_proposed_visual("framework_pillar", visual, text, extra_terms)

    if not store:
        if trio_vis := _extract_trio_diagram(text, sentences, gp_info, extra_terms=extra_terms):
            return _guard_proposed_visual("semantic_diagram_card", trio_vis, text, extra_terms)
        if couplet_vis := _extract_couplet_diagram(text, sentences, gp_info, extra_terms=extra_terms):
            return _guard_proposed_visual("semantic_diagram_card", couplet_vis, text, extra_terms)

    if store:
        lead = _LIST_LEAD.search(text)
        if lead and (items := _list_items(text[lead.end():], sentences, lead, extra_terms=extra_terms)):
            return _guard_proposed_visual("list_build_gather_dim_overlay", _visual_copy(lead[0], items), text, extra_terms)
        headline, accent = _numeric_focus(text)
        if headline:
            return _guard_proposed_visual("num_anchor_dim_overlay", _visual_copy(headline, accent=accent), text, extra_terms)
        showcase = _SHOWCASE.search(text)
        if showcase and (title := _phrase(text[showcase.end():], extra_terms=extra_terms)):
            return _guard_proposed_visual("entity_stack_plus_top", _visual_copy(title), text, extra_terms)
    if call := _CTA.search(text):
        return _guard_proposed_visual("inline_emphasis_cta", _extract_cta_copy(text, call, sentences, extra_terms=extra_terms), text, extra_terms)
    if opening and store and (cue := _STORE_INTRO.search(text)):
        primitive = "hero_anchor_plus_danmaku" if behind else "hero_intro_pop"
        return _guard_proposed_visual(primitive, _visual_copy(cue[0]), text, extra_terms)
    if opening and (turn := _TURN.search(text)):
        if title := _short_conclusion(text[turn.end():], sentences, allow_tail=False, extra_terms=extra_terms):
            return _guard_proposed_visual("hero_intro_pop", _visual_copy(title), text, extra_terms)
    return "plain_bridge", _visual_copy()


_Candidate = tuple[List[Dict[str, Any]], str, Dict[str, Any]]
_HighlightState = tuple[float, float, int, int, tuple[int, ...]]
_BREATH_GAP_SEC = 0.35
_MAX_HIGHLIGHT_STREAK = 2


_LEGACY_TO_UNIFIED_PRIMITIVE = {
    "hero_intro_pop": "opening_triad",
    "staged_3tier_build": "opening_triad",
    "hero_anchor_plus_danmaku": "pain_slam_danmaku",
    "negation_strike": "pain_slam_danmaku",
    "concept_split_card": "left_right_punch",
    "framework_pillar": "stacked_dual_color",
    "semantic_diagram_card": "giant_anchor_sub",
    "list_build_gather_dim_overlay": "list_gather_depth",
    "num_anchor_dim_overlay": "number_burst_pair",
    "entity_stack_plus_top": "stacked_dual_color",
    "inline_emphasis_cta": "inline_highlight_cta",
}


_BROLL_STOPWORDS = frozenset({
    "一个", "一些", "呢个", "𠮶个", "嗰个", "这种", "那个", "这个", "什么", "乜嘢", "所以",
    "因为", "觉得", "如果", "但是", "现在", "时候", "跟住", "大家", "不是", "是我", "两个",
    "一次", "下来", "出来", "还有", "包括", "就是", "就系", "可以", "非常", "绝对", "好多",
    "之后", "自己", "一间", "呢一个", "都会", "是的", "实拍", "空镜", "近景", "特写",
    "全景", "中景", "远景",
})


def _match_broll_keywords(group: List[Dict[str, Any]], pool: List[Dict[str, Any]]) -> List[str]:
    if not pool:
        return []
    text = _group_text(group)
    s_kws = [
        kw for s in group for kw in _string_list(s.get("keywords"))
        if len(kw) >= 2 and kw not in _BROLL_STOPWORDS and not _is_trivial_lead_or_ordinal(kw, text)
    ]
    hits: List[str] = []
    for clip in pool:
        tags = _string_list(clip.get("tags")) + _string_list(clip.get("semantic_tags"))
        desc = str(clip.get("description") or "")
        for tag in tags:
            if (
                len(tag) >= 2
                and tag not in _BROLL_STOPWORDS
                and not _is_trivial_lead_or_ordinal(tag, text)
                and (tag in text or any(tag in k or k in tag for k in s_kws))
            ):
                hits.append(tag)
        for kw in s_kws:
            if len(kw) >= 2 and (kw in desc or any(kw in t for t in tags)):
                hits.append(kw)
    return list(dict.fromkeys(hits))[:8]


def _choose_highlights(
    candidates: List[_Candidate], total: float, prosody: Optional[Dict[str, Dict[str, Any]]] = None,
    target_band: tuple[float, float] = (_TARGET_LOW, _TARGET_HIGH),
) -> set[int]:
    if not total:
        return set()
    t_lo, t_hi = target_band
    t_mid = (t_lo + t_hi) / 2.0
    priorities = {
        "negation_strike": 110, "semantic_diagram_card": 108, "concept_split_card": 105,
        "framework_pillar": 58, "list_build_gather_dim_overlay": 100, "num_anchor_dim_overlay": 90,
        "entity_stack_plus_top": 35, "hero_anchor_plus_danmaku": 45, "hero_intro_pop": 20,
        "inline_emphasis_cta": 40,
        "pain_slam_danmaku": 110, "left_right_punch": 105, "list_gather_depth": 100,
        "number_burst_pair": 95, "giant_anchor_sub": 90, "stacked_dual_color": 65,
        "opening_triad": 85, "inline_highlight_cta": 50,
    }

    def rank(state: _HighlightState) -> tuple:
        seconds, _, _, importance, selected = state
        ratio = seconds / total
        return (max(t_lo - ratio, ratio - t_hi, 0.0),
                -importance, abs(ratio - t_mid), len(selected))

    choices: Dict[tuple[int, float, int], _HighlightState] = {(0, 0.0, 0): (0.0, 0.0, 0, 0, ())}

    def keep(state: _HighlightState) -> None:
        seconds, previous_end, streak, _, _ = state
        # 同一覆盖率下保留不同的高光结束时间与连击数，绝不挤掉还能接下一幕的排期。
        bucket = (round(seconds / total * 1000), previous_end, streak)
        if bucket not in choices or rank(state) < rank(choices[bucket]):
            choices[bucket] = state

    seen: Dict[str, int] = {}
    p_map = prosody or {}
    for index, (sentences, primitive, visual) in enumerate(candidates):
        if primitive in _QUIET_PRIMITIVES or not (visual["headline"] or visual["items"] or visual["accent_token"]):
            continue
        start, end = _span(sentences)
        repetitions = seen.get(primitive, 0)
        seen[primitive] = repetitions + 1
        gp = _group_prosody(sentences, p_map) if p_map else {}
        acoustic_bonus = 18 if gp.get("acoustic_emphasis") else 0
        priority = priorities.get(primitive, 0) // min(3, repetitions + 1) + acoustic_bonus
        previous_choices = choices.values()
        choices = {}
        for seconds, previous_end, streak, importance, selected in previous_choices:
            if start - previous_end >= _BREATH_GAP_SEC - 1e-9:
                previous_end, streak = 0.0, 0
            keep((seconds, previous_end, streak, importance, selected))
            if streak >= _MAX_HIGHLIGHT_STREAK:
                continue
            added = max(0.0, end - max(start, previous_end))
            keep((seconds + added, max(previous_end, end), streak + 1, importance + priority, selected + (index,)))
    return set(min(choices.values(), key=rank)[4])


_STORE_BREATH_MIN_TOTAL = 20.0
_STORE_REVEAL_PRIMITIVES = ("num_anchor_dim_overlay", "list_build_gather_dim_overlay")
_STORE_REVEAL_MIN_OPENING_SEC = 4.0
_STORE_REVEAL_MIN_TOTAL_RATIO = 0.15


def _store_breath_cues(
    candidates: List[_Candidate], total: float, highlights: Optional[set[int]],
) -> List[Dict[str, Any]]:
    """探店长片：只在核心揭晓幕前留一次克制呼吸，可选补一条温和推升，绝不与呼吸重叠。"""
    picked = sorted(highlights) if highlights is not None else list(range(len(candidates)))
    # 开场前 4 秒（且不早于全片 15%）是钩子区，绝不在这里抽掉配乐。
    floor = max(_STORE_REVEAL_MIN_OPENING_SEC, total * _STORE_REVEAL_MIN_TOTAL_RATIO)
    scored: List[tuple] = []
    for index in picked:
        if index <= 0 or index >= len(candidates):
            continue
        primitive = candidates[index][1]
        if primitive not in _STORE_REVEAL_PRIMITIVES:
            continue
        act_start, _ = _span(candidates[index][0])
        if act_start < floor:
            continue
        # 招牌菜清单最能撑住揭晓重音；同类里再挑最靠近全片中段的那一幕。
        priority = 0 if primitive == "list_build_gather_dim_overlay" else 1
        scored.append((priority, abs(act_start - total * 0.5), index))
    reveal = min(scored)[2] if scored else None
    if reveal is None:
        return []
    act_start, _ = _span(candidates[reveal][0])
    start = round(max(1.0, min(act_start - 0.08, total - 0.40)), 4)
    drop_end = round(min(total, start + 0.35), 4)
    cues: List[Dict[str, Any]] = [{
        "type": "silence_drop", "start_sec": start, "end_sec": drop_end,
        "depth_db": -20.0, "fade_ms": 65, "reason": "核心揭晓前留一口气",
    }]
    for index in (i for i in picked if i > reveal):
        span_start, span_end = _span(candidates[index][0])
        if span_end - span_start < 1.5:
            continue
        swell_start = round(max(drop_end + 0.05, min(span_start - 0.08, total - 0.50)), 4)
        swell_end = round(min(total, swell_start + 3.5), 4)
        if swell_end - swell_start < 0.5:
            continue
        cues.append({
            "type": "section_swell", "start_sec": swell_start, "end_sec": swell_end,
            "depth_db": 2.2, "fade_ms": 180, "reason": "主体展开段配乐托一把",
        })
        break
    return cues


def _music_cues(
    candidates: List[_Candidate], total: float, store: bool, highlights: Optional[set[int]] = None,
) -> List[Dict[str, Any]]:
    cues: List[Dict[str, Any]] = []
    occupied_until = 0.0
    if candidates and total >= 25.0 and not store:
        occupied_until = min(2.9, candidates[0][0][0]["end_sec"])
        cues.append({
            "type": "delay_entry", "start_sec": 0.0, "end_sec": occupied_until,
            "depth_db": -28.0, "fade_ms": 80, "reason": "开篇先留出干声",
        })
    for group, _, _ in candidates:
        text = _group_text(group)
        if not (_TURN.search(text) or _CONTRAST.search(text) or "≠" in text):
            continue
        act_start, act_end = _span(group)
        start = max(0.0, occupied_until, min(act_start - 0.08, total - 0.45))
        end = min(total, start + 0.45)
        if start >= act_end or end - start < 0.25:
            continue
        cues.append({
            "type": "silence_drop", "start_sec": start, "end_sec": end,
            "depth_db": -28.0, "fade_ms": 80, "reason": "反转句前压低配乐",
        })
        occupied_until = end
    if store and total >= _STORE_BREATH_MIN_TOTAL and not any(cue["type"] == "silence_drop" for cue in cues):
        cues.extend(_store_breath_cues(candidates, total, highlights))
    return cues


_DANGLING_TAIL_CHARS = _DANGLING_HARD_PARTICLES | _DANGLING_ASPECT_PARTICLES
_DANGLING_1CHAR_TAIL_POS = frozenset({
    "v", "vg", "a", "ag", "b", "d", "dg", "p", "c", "u", "uj", "ug", "ud", "ul", "uz", "y", "zg", "r", "q", "tg", "ng",
})
_NON_ENTITY_POS = frozenset({
    "u", "uj", "ug", "ud", "ul", "uz", "p", "c", "y", "e", "o", "x", "zg", "d", "r", "f", "s", "t",
})
_ORDINAL_ONLY_RE = re.compile(r"^第[零〇一二两三四五六七八九十百千万亿0-9]+[点条步项个位次]?$")
_NOUN_ENTITY_POS = frozenset({"n", "nr", "ns", "nt", "nz", "nrt", "vn", "eng"})
_COPULA_DE_END_RE = re.compile(r"(?:出来|下来|起来|进去|过来|回来|出去)的$")
_SHOWCASE_VERBS = frozenset({"做", "卖", "有", "主打", "包括", "供应", "提供", "推出"})
_PERSONAL_PRONOUNS = frozenset({
    "我", "你", "他", "她", "它", "佢", "咱", "咱们", "咱們",
    "我们", "我們", "你们", "你們", "他们", "他們", "她们", "她們", "它们", "它們",
    "我哋", "你哋", "佢哋", "大家", "自己", "别人", "別人",
})
_DISCOURSE_LEADIN_TOKENS = frozenset({
    "第一", "第二", "第三", "第四", "首先", "其次", "再次", "最后",
    "其实", "记住", "另外", "而且", "但是", "可是", "不过", "所以",
    "因此", "因为", "然后", "跟住", "到时", "聊聊", "为什么", "为啥", "点解",
})
_UNIT_END_ALNUM_CHARS = (
    "万", "亿", "元", "块", "平", "年", "天", "秒", "分", "分钟", "小时", "倍", "%", "％", "度",
    "帧", "幀", "分贝", "分貝", "赫兹", "赫茲", "千赫", "兆赫", "毫米", "厘米", "流明", "尼特",
    "hz", "Hz", "HZ", "fps", "FPS", "db", "dB", "DB",
)
_PRE_NOUN_BOUND_PREFIXES = frozenset("双单全半零纯伪准超微主副")
_VERB_COMPLEMENT_CHARS = frozenset("完掉住好到破碎光满错对清成")
_CONTINUATION_COMP_HEAD = frozenset("完掉住好到破碎光满错对清成出进回起开着")
_PRONOUN_DE_PREFIXES = ("我的", "你的", "他的", "她的", "它的", "咱的", "佢的", "我嘅", "你嘅", "佢嘅")
_R_MOD_HEAD_RE = re.compile(r"^[这那哪每各某此该呢嗰𠮶][一几两些种样类个位家环步条件点项次批套款份层]?$")
_MID_CLAUSE_OK_ADVERBS = frozenset({"之间", "以上", "以下", "以内", "以外", "之外", "之后", "以后", "左右", "上下", "前后"})
_MID_CLAUSE_TAIL_POS = frozenset({"d", "dg", "c", "p", "u", "uj", "ug", "ud", "ul", "uz"})
_POST_UNIT_RANGE_TOKENS = ("以下", "以上", "以内", "以外", "左右", "上下", "前后")
_DANGLING_MODIFIER_WORDS = frozenset({
    "有啲", "好多", "呢啲", "𠮶啲", "嗰啲", "呢个", "嗰个", "𠮶个",
    "这个", "那个", "哪个", "两个", "几个", "一个", "一些", "这种", "那种",
})
_GENERIC_QUANTIFIER_HEADS = frozenset({
    "一间", "一个", "一批", "一种", "一款", "一套", "一份", "一家", "一条", "一张", "一点",
})
_QUOTE_DANGLING_TAILS = _DANGLING_HARD_PARTICLES | _DANGLING_ASPECT_PARTICLES | frozenset("就")


def _compact_zh_spaces(text: str) -> str:
    """规范化空白：保留字母/数字词之间的单空格（如 'Wi-Fi 7'），剔除中文旁与多余空白。"""
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    return re.sub(r"(?<![A-Za-z0-9])\s+|\s+(?![A-Za-z0-9])", "", s)


@lru_cache(maxsize=1)
def _raw_pseg_tokenizer() -> Optional[Any]:
    try:
        from jieba.posseg import POSTokenizer
        from jieba import Tokenizer
        dt = Tokenizer()
        dt.initialize()
        return POSTokenizer(tokenizer=dt)
    except Exception:
        return None


def _raw_pseg_tokens(text: str) -> List[tuple[str, str]]:
    s = str(text or "")
    if not s:
        return []
    pt = _raw_pseg_tokenizer()
    if pt is None:
        return []
    return [(str(w), str(f)) for w, f in pt.cut(s) if w]


def _pseg_tokens(text: str, prot_terms: Optional[List[str]] = None) -> List[tuple[str, str]]:
    s = str(text or "")
    if not s:
        return []
    try:
        import step5_subtitles as s5
        return list(s5._pseg_cut_clean(s, prot_terms=prot_terms))
    except Exception:
        return [(w, "x") for w in _offline_tokenizer()(s) if w]


def _dynamic_protected_terms(text: str, extra_terms: Optional[List[str]] = None) -> List[str]:
    """动态提取混合字母数字原子串、词性复合词（如 1字形+2字名、2字简称+2字名、2字专名+1字名）作为保护词。"""
    s = str(text or "")
    terms: List[str] = []
    seen: set[str] = set()

    def _add(t: str) -> None:
        ts = str(t or "").strip()
        if 2 <= len(ts) <= 14 and ts not in seen:
            seen.add(ts)
            terms.append(ts)

    for et in (extra_terms or []):
        _add(et)
    for sa, sb in _atomic_alnum_spans(s):
        span_tok = s[sa:sb]
        if span_tok not in _GENERIC_QUANTIFIER_HEADS:
            _add(span_tok)

    pairs = _pseg_tokens(s, prot_terms=terms)
    for idx_p in range(len(pairs) - 1):
        w0, f0 = pairs[idx_p]
        w1, f1 = pairs[idx_p + 1]
        # 1) 通用 AA 重叠动词/形容词/名词保护（如「说说」「看看」「试试」）
        if (
            len(w0) == 1
            and w0 == w1
            and _HAN.fullmatch(w0)
            and (f0 in ("v", "vg", "a", "ag", "n", "ng") or f1 in ("v", "vg", "a", "ag", "n", "ng"))
            and w0 not in "的嘅地得了咗着过在不没唔是系係都也就才还又再"
            and not (idx_p > 0 and pairs[idx_p - 1][0] == w0)
            and not (idx_p + 2 < len(pairs) and pairs[idx_p + 2][0] == w0)
        ):
            _add(w0 + w1)
        # 2) 数字/字母单位 + 紧邻 2..4 字名物词根（如「800V高压平台」「5G网络」「Type-C接口」）
        elif (
            len(w0) <= 8
            and (
                (bool(_atomic_alnum_spans(w0)) and _atomic_alnum_spans(w0)[0] == (0, len(w0)))
                or (2 <= len(w0) <= 5 and re.fullmatch(r"[A-Z][A-Z0-9-]*", w0))
            )
            and w0 not in _GENERIC_QUANTIFIER_HEADS
            and not _ORDINAL_ONLY_RE.match(w0)
            and not w0.endswith(_UNIT_END_ALNUM_CHARS)
            and len(w1) >= 2
            and f1 in ("n", "vn", "nz", "an")
            and w1 not in _PERSONAL_PRONOUNS
            and w1 not in _SHOWCASE_VERBS
            and w1 not in _DANGLING_MODIFIER_WORDS
            and not re.match(r"^[0-9零〇一二两兩三四五六七八九十百千万萬亿億]", w1)
            and _HAN.fullmatch(w1)
        ):
            nom_suffix = w1
            if idx_p + 2 < len(pairs):
                w2, f2 = pairs[idx_p + 2]
                if (
                    len(nom_suffix) + len(w2) <= 4
                    and f2 in ("n", "vn", "nz", "an")
                    and w2 not in _PERSONAL_PRONOUNS
                    and w2 not in _SHOWCASE_VERBS
                    and _HAN.fullmatch(w2)
                ):
                    nom_suffix = nom_suffix + w2
            if 2 <= len(nom_suffix) <= 4 and len(w0 + nom_suffix) <= 10:
                _add(w0 + nom_suffix)
        # 3a) 2字动词 + 1字结果补语（如「升级完」「整理好」「打磨清」）
        elif (
            len(w0) == 2
            and f0 in ("v", "vn")
            and w0 not in _SHOWCASE_VERBS
            and w0 not in _PERSONAL_PRONOUNS
            and len(w1) == 1
            and w1 in _VERB_COMPLEMENT_CHARS
            and _HAN.fullmatch(w0 + w1)
        ):
            _add(w0 + w1)
        # 3b) 1字黏着前置定语 + 2字名词（如「双机位」「全消声」「单通道」「纯手工」）
        elif (
            len(w0) == 1
            and w0 in _PRE_NOUN_BOUND_PREFIXES
            and len(w1) == 2
            and f1 in ("n", "vn", "nz", "an")
            and w1 not in _PERSONAL_PRONOUNS
            and w1 not in _SHOWCASE_VERBS
            and _HAN.fullmatch(w0 + w1)
        ):
            _add(w0 + w1)
        elif (
            len(w0) == 1
            and f0 in ("a", "b", "ng", "vg")
            and w0 not in "很太最更挺蛮真极超级不没唔好这那哪每各"
            and w0 not in _DANGLING_HARD_PARTICLES
            and len(w1) == 2
            and f1 in ("n", "vn", "v", "nz", "an", "ns")
            and _HAN.fullmatch(w0 + w1)
        ):
            _add(w0 + w1)
        elif (
            len(w0) == 2
            and f0 in ("j", "b")
            and not _ORDINAL_ONLY_RE.match(w0)
            and w0[0] not in "几多少每各某这那哪"
            and not re.search(r"[0-9]", w0)
            and len(w1) == 2
            and f1 in ("n", "nr", "nrt", "nz", "vn")
            and _HAN.fullmatch(w0 + w1)
        ):
            _add(w0 + w1)
        elif (
            len(w0) == 2
            and f0 in ("nr", "ns", "b", "a")
            and w0 not in _DANGLING_MODIFIER_WORDS
            and len(w1) == 1
            and f1 == "n"
            and w1 not in _DANGLING_TAIL_CHARS
            and w1 not in "人个条只件家店"
            and _HAN.fullmatch(w0 + w1)
        ):
            _add(w0 + w1)
    terms.sort(key=len, reverse=True)
    return terms


def _atomic_semantic_tokens(text: str, extra_terms: Optional[List[str]] = None) -> List[str]:
    s = str(text or "")
    if not s:
        return []
    prot = _dynamic_protected_terms(s, extra_terms=extra_terms)
    try:
        import step5_subtitles as s5
        toks = [t for t in s5.tokenize_semantic_words(s, terms=prot) if t]
    except Exception:
        toks = [t for t in _offline_tokenizer()(s) if t]
    return _coalesce_atomic_words(toks)


def _strip_clause_punct(text: str) -> str:
    cleaned = re.sub(r"[，。！？、；：,!?;:「」『』“”\"'()（）]+|(?<!\d)\.(?!\d)", "", str(text or ""))
    return _compact_zh_spaces(cleaned)


def _has_quant_or_ascii_atomic(token: str) -> bool:
    t = str(token or "").strip()
    if not t or not _atomic_alnum_spans(t):
        return False
    return any(ch.isascii() and ch.isalnum() for ch in t) or bool(_numeric_focus(t)[0])


def _is_dangling_head(txt: str) -> bool:
    t = str(txt or "").strip()
    if not t or t[0] not in _DANGLING_HARD_PARTICLES:
        return False
    if t[0] in ("的", "嘅"):
        return not t.startswith(("的确", "的士", "德国"))
    pairs = _raw_pseg_tokens(t) or _pseg_tokens(t)
    if pairs and len(pairs[0][0]) >= 2:
        return False
    return True


def _is_trivial_lead_or_ordinal(phrase: str, full_text: str = "") -> bool:
    p = _strip_clause_punct(phrase)
    if not p or len(p) < 2:
        return True
    if _ORDINAL_ONLY_RE.match(p) or p in _DANGLING_MODIFIER_WORDS:
        return True
    pairs = _pseg_tokens(p, prot_terms=_dynamic_protected_terms(p))
    if not pairs:
        return True
    has_alnum_atomic = _has_quant_or_ascii_atomic(p)
    if not has_alnum_atomic:
        if all(f in (_NON_ENTITY_POS | {"m", "q", "mq"}) for _, f in pairs):
            return True
        # 检查原生词性，避免粤语助词/方位/口语连接词（如「𠮶边」「就系」「跟住」「朝早」）被标为 nz
        raw_pairs = _raw_pseg_tokens(p)
        if raw_pairs and all(f in (_NON_ENTITY_POS | {"m", "q", "mq", "v", "vg", "a"}) for _, f in raw_pairs):
            if not any(f in _NOUN_ENTITY_POS for _, f in raw_pairs):
                # 保留真正包含双字实词的短语（如「点赞茶楼」含「茶楼」n），剔除纯虚词/方位/动词碎片
                if len(p) <= 3 or all(f in (_NON_ENTITY_POS | {"v", "vg", "a"}) for _, f in raw_pairs):
                    return True
        if len(p) <= 3 and p[-1] in _DANGLING_ASPECT_PARTICLES and not any(f in _NOUN_ENTITY_POS for _, f in pairs):
            return True
    if full_text and len(p) <= 2 and all(f in ("v", "d", "c") for _, f in pairs):
        ft = _compact_zh_spaces(full_text)
        if re.match(rf"^{re.escape(p)}[,，。！？!?：:、]", ft):
            return True
    return False


def _breaks_atomic_or_word_boundary(
    cand: str, full_text: str, extra_terms: Optional[List[str]] = None,
) -> bool:
    c = _strip_clause_punct(cand)
    if not c:
        return True
    if _is_dangling_head(c):
        return True
    full_compact = _compact_zh_spaces(full_text)
    plain_full = _strip_clause_punct(full_compact)
    if not plain_full:
        return False

    # 1) 检查是否切破任意数字字母混合原子跨度（含带连字符/小数点/空格版本的 Type-C / USB3.2 / H.265 / 2.4G / Wi-Fi 7 / 4K 60帧）
    for src in (full_compact, plain_full):
        spans = _atomic_alnum_spans(src)
        if not spans:
            continue
        start = 0
        while True:
            idx = src.find(c, start)
            if idx < 0:
                break
            end = idx + len(c)
            if any(sa < idx < sb or sa < end < sb for sa, sb in spans):
                return True
            if end < len(src) and src[end] in "-." and any(sa <= idx and end < sb for sa, sb in spans):
                return True
            start = idx + 1

    # 1b) 无空白投影校验：拦截被上游去空格截断的原子关键词（如 "USB 3.2" -> "USB3"、"4K 60帧" -> "4K60"）
    c_ns = re.sub(r"\s+", "", c)
    if c_ns:
        for src in (full_compact, plain_full):
            spans = _atomic_alnum_spans(src)
            if not spans:
                continue
            ns_chars: List[str] = []
            orig_to_ns = [0] * (len(src) + 1)
            ns_len = 0
            for i_ch, ch in enumerate(src):
                orig_to_ns[i_ch] = ns_len
                if not ch.isspace():
                    ns_chars.append(ch)
                    ns_len += 1
            orig_to_ns[len(src)] = ns_len
            ns_src = "".join(ns_chars)
            ns_spans = [(orig_to_ns[sa], orig_to_ns[sb]) for sa, sb in spans if orig_to_ns[sb] > orig_to_ns[sa]]
            if not ns_spans:
                continue
            start_ns = 0
            while True:
                idx_ns = ns_src.find(c_ns, start_ns)
                if idx_ns < 0:
                    break
                end_ns = idx_ns + len(c_ns)
                if any(sa < idx_ns < sb or sa < end_ns < sb for sa, sb in ns_spans):
                    return True
                if end_ns < len(ns_src) and ns_src[end_ns] in "-." and any(sa <= idx_ns and end_ns < sb for sa, sb in ns_spans):
                    return True
                start_ns = idx_ns + 1

    # 2) 按分句分别校验词界与后继结构助词「的/嘅/地/得」，防止跨逗号把「第一，颗粒度」粘成「第一颗」
    clause_plains = [
        _strip_clause_punct(cl)
        for cl in _CLAUSE.split(full_compact)
        if _strip_clause_punct(cl)
    ]
    targets = [cl_p for cl_p in clause_plains if c in cl_p]
    if not targets and c in plain_full:
        targets = [plain_full]
    if not targets:
        return False

    for src_p in targets:
        prot_src = _dynamic_protected_terms(src_p, extra_terms=extra_terms)
        toks = _atomic_semantic_tokens(src_p, extra_terms=prot_src)
        bounds: set[int] = {0}
        acc = 0
        end_tok_map: Dict[int, str] = {}
        start_tok_map: Dict[int, str] = {}
        for tk in toks:
            start_tok_map[acc] = tk
            acc += len(tk)
            bounds.add(acc)
            end_tok_map[acc] = tk

        pos_pairs = _pseg_tokens(src_p, prot_terms=prot_src)
        pos_at_end: Dict[int, tuple[str, str]] = {}
        pos_at_start: Dict[int, tuple[str, str]] = {}
        cur_p = 0
        for w_p, f_p in pos_pairs:
            pos_at_start[cur_p] = (w_p, f_p)
            start_tok_map.setdefault(cur_p, w_p)
            cur_p += len(w_p)
            bounds.add(cur_p)
            pos_at_end[cur_p] = (w_p, f_p)
            end_tok_map.setdefault(cur_p, w_p)

        raw_pos_pairs = _pseg_tokens(src_p, prot_terms=None)
        raw_pos_at_end: Dict[int, tuple[str, str]] = {}
        raw_pos_at_start: Dict[int, tuple[str, str]] = {}
        cur_rp = 0
        for w_rp, f_rp in raw_pos_pairs:
            raw_pos_at_start[cur_rp] = (w_rp, f_rp)
            cur_rp += len(w_rp)
            raw_pos_at_end[cur_rp] = (w_rp, f_rp)

        search_pos = 0
        while True:
            idx = src_p.find(c, search_pos)
            if idx < 0:
                break
            search_pos = idx + 1
            end = idx + len(c)
            if idx not in bounds or end not in bounds:
                continue
            if idx > 0 and c[0] in _DANGLING_HARD_PARTICLES:
                start_w, start_f = pos_at_start.get(idx, ("", ""))
                _, raw_start_f = raw_pos_at_start.get(idx, ("", ""))
                if (
                    start_w == c[0]
                    or start_f in ("u", "uj", "ug", "ud", "ul", "uz", "p", "c")
                    or raw_start_f in ("u", "uj", "ug", "ud", "ul", "uz", "p", "c")
                ):
                    continue
            if end < len(src_p):
                last_tk = end_tok_map.get(end, "")
                w_p, f_p = pos_at_end.get(end, ("", ""))
                raw_w_p, raw_f_p = raw_pos_at_end.get(end, ("", ""))
                if (
                    len(last_tk) == 1
                    and not _atomic_alnum_spans(last_tk)
                    and (
                        last_tk in _DANGLING_HARD_PARTICLES
                        or last_tk in _PRE_NOUN_BOUND_PREFIXES
                        or f_p in _DANGLING_1CHAR_TAIL_POS
                        or raw_f_p in _DANGLING_1CHAR_TAIL_POS
                    )
                ):
                    continue
                if last_tk in ("是", "系", "係", "就是", "就系", "就係") or w_p in ("是", "系", "係", "就是", "就系", "就係"):
                    continue
                is_dangling_m = (
                    f_p in ("m", "mq")
                    and not re.search(r"[A-Za-z0-9]", w_p)
                    and not w_p.endswith(_UNIT_END_ALNUM_CHARS)
                ) or (
                    raw_f_p in ("m", "mq")
                    and not re.search(r"[A-Za-z0-9]", raw_w_p)
                    and not raw_w_p.endswith(_UNIT_END_ALNUM_CHARS)
                )
                is_dangling_r = (
                    (f_p == "r" or raw_f_p == "r")
                    and (
                        w_p.endswith(("一", "几", "两"))
                        or raw_w_p.endswith(("一", "几", "两"))
                        or bool(_R_MOD_HEAD_RE.match(w_p))
                        or bool(_R_MOD_HEAD_RE.match(raw_w_p))
                    )
                )
                is_mid_clause_tail_pos = (
                    (f_p in _MID_CLAUSE_TAIL_POS and not re.search(r"[A-Za-z0-9]", w_p) and w_p not in _MID_CLAUSE_OK_ADVERBS)
                    or (raw_f_p in _MID_CLAUSE_TAIL_POS and not re.search(r"[A-Za-z0-9]", raw_w_p) and raw_w_p not in _MID_CLAUSE_OK_ADVERBS)
                )
                if w_p in _DANGLING_MODIFIER_WORDS or raw_w_p in _DANGLING_MODIFIER_WORDS or is_dangling_m or is_dangling_r or is_mid_clause_tail_pos:
                    continue
                # 无论 jieba 词性误标为何，严禁在非句末完整谓词处直接截断于「的/嘅/地/得」之前
                next_ch = src_p[end]
                next_tk = start_tok_map.get(end, "")
                if not _COPULA_DE_END_RE.search(c):
                    if next_ch in ("的", "嘅") and next_tk not in ("的确", "的士"):
                        continue
                    if next_tk in ("地", "得"):
                        continue
            return False
    return True


def _clean_sent_keywords(
    sent: Dict[str, Any], min_len: int = 2, max_len: int = 8,
    extra_terms: Optional[List[str]] = None,
) -> List[str]:
    """通用清洗单句 keywords：经 step5_subtitles 边界清洗 + POS/原子跨度校验，剔除 ASR 残片与虚词。"""
    if not isinstance(sent, dict):
        return []
    sent_text = str(sent.get("text") or "").strip()
    raw_kws: List[str] = []
    for field in ("display_keywords", "keywords"):
        for k in (sent.get(field) or []):
            ks = str(k or "").strip()
            if ks and ks not in raw_kws:
                raw_kws.append(ks)
    if not raw_kws:
        return []
    try:
        import step5_subtitles as s5
        cleaned = s5._clean_display_keywords(raw_kws, sent_text) if sent_text else raw_kws
    except Exception:
        cleaned = raw_kws
    if sent_text:
        compact_spans = {
            re.sub(r"\s+", "", sent_text[sa:sb]): _compact_zh_spaces(sent_text[sa:sb])
            for sa, sb in _atomic_alnum_spans(sent_text)
        }
        cleaned = [compact_spans.get(re.sub(r"\s+", "", str(kw)), str(kw)) for kw in cleaned]

    out: List[str] = []
    for kw in cleaned:
        k = str(kw or "").strip()
        if not (min_len <= len(k) <= max_len):
            continue
        if k[-1] in _DANGLING_TAIL_CHARS or k[0] in _DANGLING_TAIL_CHARS:
            continue
        if sent_text and _breaks_atomic_or_word_boundary(k, sent_text, extra_terms=extra_terms):
            continue
        if _is_trivial_lead_or_ordinal(k, sent_text):
            continue
        pairs = _pseg_tokens(k, prot_terms=_dynamic_protected_terms(k, extra_terms=extra_terms))
        if pairs:
            first_w, first_f = pairs[0]
            last_w, last_f = pairs[-1]
            if (
                first_f in ("m", "mq")
                and not re.search(r"[0-9零〇一二两三四五六七八九十百千万亿]", first_w)
            ):
                continue
            if len(last_w) == 1 and last_f in _DANGLING_1CHAR_TAIL_POS and not _atomic_alnum_spans(last_w):
                continue
            # 剔除代词领起的动词短语（含 jieba 将「大家」标为 n 的情况），除非包含独立非代词实体名词且非动词结尾
            if len(pairs) >= 2 and (first_f == "r" or first_w in _PERSONAL_PRONOUNS):
                has_non_pronoun_noun = any(
                    f in _NOUN_ENTITY_POS and w not in _PERSONAL_PRONOUNS and w not in _SHOWCASE_VERBS
                    for w, f in pairs[1:]
                )
                if (
                    pairs[1][1] in ("v", "vn", "vg")
                    or pairs[1][0] in _SHOWCASE_VERBS
                    or last_f in ("v", "vn", "vg")
                    or not has_non_pronoun_noun
                ):
                    continue
        if k in _PERSONAL_PRONOUNS or k in _SHOWCASE_VERBS:
            continue
        raw_pairs = _raw_pseg_tokens(k)
        if raw_pairs and len(raw_pairs) >= 2 and (raw_pairs[0][1] == "r" or raw_pairs[0][0] in _PERSONAL_PRONOUNS):
            if raw_pairs[1][1] in ("v", "vn", "vg") or raw_pairs[-1][1] in ("v", "vn", "vg"):
                continue
        if k not in out:
            out.append(k)
    return out


def _extract_noun_entities(
    text: str, max_chars: int = 8, extra_terms: Optional[List[str]] = None,
) -> List[str]:
    """从文本中按词性与原子跨度提取真实名词/实体短语（拒绝纯动词/方位/时间/虚词子句）。"""
    s = str(text or "").strip()
    if not s:
        return []
    prot = _dynamic_protected_terms(s, extra_terms=extra_terms)
    pairs = _pseg_tokens(s, prot_terms=prot)
    cands: List[str] = []

    def _has_real_raw_noun(cand: str) -> bool:
        if _has_quant_or_ascii_atomic(cand):
            return True
        if extra_terms and any(cand == et for et in extra_terms):
            return True
        rp = _raw_pseg_tokens(cand)
        if not rp:
            return True
        return any(f in _NOUN_ENTITY_POS and w not in _PERSONAL_PRONOUNS and w not in _SHOWCASE_VERBS for w, f in rp)

    def _push(cand: str) -> None:
        c = _strip_clause_punct(cand)
        if not (2 <= len(c) <= max_chars):
            return
        if c in _PERSONAL_PRONOUNS or c in _SHOWCASE_VERBS:
            return
        if c[-1] in _DANGLING_TAIL_CHARS or c[0] in _DANGLING_TAIL_CHARS:
            return
        if _is_trivial_lead_or_ordinal(c, s):
            return
        if _breaks_atomic_or_word_boundary(c, s, extra_terms=extra_terms):
            return
        if not _has_real_raw_noun(c):
            return
        cpairs = _pseg_tokens(c, prot_terms=prot)
        if not cpairs:
            return
        if cpairs[0][0] in _PERSONAL_PRONOUNS or cpairs[0][0] in _SHOWCASE_VERBS:
            return
        if cpairs[0][1] in ("p", "c", "u", "uj", "ul", "ug", "y", "r", "f", "s", "t", "zg"):
            return
        if cpairs[-1][1] in ("p", "c", "u", "uj", "ul", "ug", "y", "r", "zg", "d", "v", "vg"):
            if not _has_quant_or_ascii_atomic(cpairs[-1][0]):
                return
        if not any(
            (f in _NOUN_ENTITY_POS and w not in _PERSONAL_PRONOUNS and w not in _SHOWCASE_VERBS)
            or bool(_has_quant_or_ascii_atomic(w))
            for w, f in cpairs
        ):
            return
        if c not in cands:
            cands.append(c)

    # 0) 若句中存在展示动词（做/卖/有/主打/包括/供应），优先提取动词之后的宾语名词
    after_verb_idx = -1
    for i, (w, f) in enumerate(pairs):
        if w in _SHOWCASE_VERBS or (f == "v" and w in ("做", "卖", "有", "系", "是")):
            after_verb_idx = i + 1
            break

    def _scan_pairs(sub_pairs: List[tuple[str, str]]) -> None:
        for i, (w, f) in enumerate(sub_pairs):
            if _has_quant_or_ascii_atomic(w):
                if (
                    i + 1 < len(sub_pairs)
                    and sub_pairs[i + 1][1] in _NOUN_ENTITY_POS
                    and sub_pairs[i + 1][0] not in _SHOWCASE_VERBS
                    and len(w + sub_pairs[i + 1][0]) <= max_chars
                ):
                    _push(w + sub_pairs[i + 1][0])
                _push(w)
        i = 0
        while i < len(sub_pairs):
            w, f = sub_pairs[i]
            before_de = i + 1 < len(sub_pairs) and sub_pairs[i + 1][0] in ("的", "嘅")
            if w in _SHOWCASE_VERBS or w in _PERSONAL_PRONOUNS:
                i += 1
                continue
            if f in _NOUN_ENTITY_POS and len(w) >= 2 and not before_de:
                if (
                    i + 1 < len(sub_pairs)
                    and len(sub_pairs[i + 1][0]) >= 2
                    and sub_pairs[i + 1][1] in _NOUN_ENTITY_POS
                    and sub_pairs[i + 1][0] not in _SHOWCASE_VERBS
                    and sub_pairs[i + 1][0] not in _PERSONAL_PRONOUNS
                    and len(w + sub_pairs[i + 1][0]) <= max_chars
                ):
                    _push(w + sub_pairs[i + 1][0])
                _push(w)
            elif (
                (f in ("a", "b", "nr", "ns") or (len(w) == 1 and w in "双单新老全大小微"))
                and i + 1 < len(sub_pairs)
                and len(sub_pairs[i + 1][0]) >= 2
                and sub_pairs[i + 1][1] in _NOUN_ENTITY_POS
                and sub_pairs[i + 1][0] not in _SHOWCASE_VERBS
                and not before_de
            ):
                if len(w + sub_pairs[i + 1][0]) <= max_chars:
                    _push(w + sub_pairs[i + 1][0])
            i += 1

    if after_verb_idx > 0 and after_verb_idx < len(pairs):
        _scan_pairs(pairs[after_verb_idx:])
    _scan_pairs(pairs)
    return cands


def _extract_safe_punch(
    sent: Dict[str, Any], vc_hl: str = "", max_chars: int = 7, fallback: str = "核心真相",
    extra_terms: Optional[List[str]] = None,
) -> str:
    """校验/清洗 svt.extract_sentence_punch，确保不切碎 Type-C/2.4G/USB3.2 且不落虚词/引语残片。"""
    sent_text = str(sent.get("text") or "").strip() if isinstance(sent, dict) else ""
    plain = _strip_clause_punct(sent_text)

    def _valid(cand: str) -> bool:
        c = str(cand or "").strip().lstrip("✕✓ ")
        if not (2 <= len(c) <= max_chars):
            return False
        if c[-1] in _DANGLING_TAIL_CHARS or c[0] in _DANGLING_TAIL_CHARS:
            return False
        if sent_text:
            if _breaks_atomic_or_word_boundary(c, sent_text, extra_terms=extra_terms):
                return False
            if _is_trivial_lead_or_ordinal(c, sent_text):
                return False
        return True

    # 1) 优先用经过校验的 visual_copy.headline
    if _valid(vc_hl):
        return str(vc_hl).strip().lstrip("✕✓ ")

    # 2) 清洗后的本句关键词（优先选择名词/原子词实体，而非动词短语）
    clean_kws = _clean_sent_keywords(sent, min_len=2, max_len=max_chars, extra_terms=extra_terms)
    noun_ents = [ent for ent in _extract_noun_entities(sent_text, max_chars=max_chars, extra_terms=extra_terms) if _valid(ent)]
    for kw in clean_kws:
        if _valid(kw):
            kw_pairs = _pseg_tokens(kw, prot_terms=_dynamic_protected_terms(kw, extra_terms=extra_terms))
            kw_is_noun = bool(
                _atomic_alnum_spans(kw)
                or (
                    kw_pairs
                    and kw_pairs[-1][1] not in ("v", "vg")
                    and any(f in _NOUN_ENTITY_POS and w not in _PERSONAL_PRONOUNS for w, f in kw_pairs)
                )
            )
            if kw_is_noun or not noun_ents:
                return kw
    if noun_ents:
        return noun_ents[0]

    # 3) 校验 style_visual_truth.extract_sentence_punch 结果是否切碎原子词或取到引语虚词（传入清洗后的 keywords）
    svt_punch = ""
    try:
        import style_visual_truth as svt
        sent_cleaned = dict(sent, keywords=clean_kws, display_keywords=clean_kws) if isinstance(sent, dict) else sent
        svt_punch = str(svt.extract_sentence_punch(sent_cleaned, max_chars=4, fallback="") or "").strip()
    except Exception:
        svt_punch = ""

    sent_alnum = [
        plain[sa:sb] for sa, sb in _atomic_alnum_spans(plain)
        if 2 <= (sb - sa) <= max_chars and not _ORDINAL_ONLY_RE.match(plain[sa:sb])
    ]
    if svt_punch and _valid(svt_punch):
        svt_in_plain = svt_punch in plain
        svt_pairs = _pseg_tokens(svt_punch, prot_terms=_dynamic_protected_terms(svt_punch, extra_terms=extra_terms))
        svt_has_noun = any(f in _NOUN_ENTITY_POS or bool(_atomic_alnum_spans(w)) for w, f in svt_pairs)
        if svt_in_plain and (svt_has_noun or not sent_alnum):
            return svt_punch

    # 4) 从原句提取原子数字字母串或实词短语
    for tok in sent_alnum:
        if _valid(tok):
            return tok
    for ent in _extract_noun_entities(sent_text, max_chars=max_chars, extra_terms=extra_terms):
        if _valid(ent):
            return ent
    return _snap_phrase_to_boundary(
        sent_text, max_chars=max_chars, min_chars=2, fallback=fallback, allow_plus_two=False, prefer_content=True,
        extra_terms=extra_terms,
    )


def _snap_phrase_to_boundary(
    text: str,
    max_chars: int = 9,
    min_chars: int = 2,
    fallback: str = "",
    *,
    allow_plus_two: bool = True,
    prefer_content: bool = False,
    extra_terms: Optional[List[str]] = None,
) -> str:
    """按通用 DP 语义词边界截取短语，禁止劈碎数字字母原子词、复合词、否定前缀或留挂尾虚词。"""
    raw = _compact_zh_spaces(text).strip(_TRIM)
    if not raw:
        return fallback
    raw_clean = _strip_clause_punct(raw)
    if not raw_clean:
        return fallback

    prot_terms = _dynamic_protected_terms(raw, extra_terms=extra_terms)
    eff_cap = max_chars + (2 if allow_plus_two else 0)
    clauses = [c.strip(_TRIM) for c in _CLAUSE.split(raw) if _strip_clause_punct(c)]

    _LEADING_STRIP_PARTICLES = _DANGLING_HARD_PARTICLES - frozenset("的嘅地得")

    def _trim_edges(s: str) -> str:
        cs = _strip_clause_punct(s)
        while len(cs) > min_chars and cs[0] in _LEADING_STRIP_PARTICLES:
            cs = cs[1:]
        while (
            len(cs) > min_chars
            and cs[-1] in _DANGLING_HARD_PARTICLES
            and not _COPULA_DE_END_RE.search(cs)
        ):
            cs = cs[:-1]
        return cs

    has_trivial_lead_clause = len(clauses) >= 2 and _is_trivial_lead_or_ordinal(_strip_clause_punct(clauses[0]), raw)
    intact_cap = eff_cap if (allow_plus_two and len(clauses) <= 1) else max_chars
    if not has_trivial_lead_clause and len(raw_clean) <= intact_cap:
        out = _trim_edges(raw_clean)
        if (
            len(out) >= min_chars
            and not _is_dangling_head(out)
            and not (out[-1] in _DANGLING_TAIL_CHARS and not _COPULA_DE_END_RE.search(out))
            and not _breaks_atomic_or_word_boundary(out, raw, extra_terms=extra_terms)
        ):
            return out

    cands: List[str] = []

    def _drops_clause_negation(cand_clean: str) -> bool:
        for cl in clauses:
            cl_p = _strip_clause_punct(cl)
            pos = cl_p.find(cand_clean)
            if pos > 0 and _NEGATION.search(cl_p[:pos]):
                return True
        return False

    def _starts_with_copula_or_particle(cand_clean: str) -> bool:
        return bool(cand_clean) and cand_clean[0] in ("是", "系", "係", "的", "嘅", "地", "得", "了", "咗", "着", "过")

    def _add_cand(s: str, *, allow_neg_drop: bool = True, allow_copula_head: bool = True) -> None:
        cs = _trim_edges(s)
        if not (min_chars <= len(cs) <= eff_cap):
            return
        if _is_dangling_head(cs):
            return
        if cs[-1] in _DANGLING_HARD_PARTICLES and not _COPULA_DE_END_RE.search(cs):
            return
        if not allow_neg_drop and _drops_clause_negation(cs):
            return
        if not allow_copula_head and _starts_with_copula_or_particle(cs):
            return
        if _breaks_atomic_or_word_boundary(cs, raw, extra_terms=extra_terms):
            return
        if cs not in cands:
            cands.append(cs)

    try:
        import step5_subtitles as s5
        segs = s5.dp_segment_short_lines(
            raw,
            max_width_px=max(480, max_chars * 84),
            font_path=None,
            font_size=54,
            terms=prot_terms,
            max_chars=max_chars,
            strip_punct=True,
        )
        for seg_idx, seg in enumerate(segs):
            seg_clean = _strip_clause_punct(seg[2])
            if not seg_clean:
                continue
            # 禁止 DP 续行切片丢掉同分句开头的否定词（「不是...」）或在非分句首落到系词「是」/复合词续尾（「完的...」）
            is_mid_clause = _drops_clause_negation(seg_clean)
            if is_mid_clause:
                continue
            starts_clause = any(_strip_clause_punct(cl).startswith(seg_clean) for cl in clauses)
            if not starts_clause and _starts_with_copula_or_particle(seg_clean):
                continue
            seg_first_tok = (_atomic_semantic_tokens(seg_clean, extra_terms=prot_terms) or [""])[0]
            if not starts_clause and (
                (len(seg_clean) >= 2 and seg_clean[1] in ("的", "嘅", "地", "得") and not seg_clean.startswith(_PRONOUN_DE_PREFIXES))
                or (len(seg_first_tok) == 1 and seg_first_tok in _CONTINUATION_COMP_HEAD and not _atomic_alnum_spans(seg_clean[:2]))
                or seg_first_tok in _POST_UNIT_RANGE_TOKENS
                or seg_first_tok in _CONCLUSION_BOUNDARIES
            ):
                continue
            _add_cand(seg[2], allow_neg_drop=False, allow_copula_head=starts_clause)
            if len(seg_clean) > eff_cap:
                seg_toks = _atomic_semantic_tokens(seg_clean, extra_terms=prot_terms)
                for cap in ((max_chars, eff_cap) if allow_plus_two else (max_chars,)):
                    acc = ""
                    for tk in seg_toks:
                        if len(acc) + len(tk) <= cap:
                            acc += tk
                        else:
                            break
                    if acc:
                        _add_cand(acc, allow_neg_drop=False, allow_copula_head=starts_clause)
    except Exception:
        pass

    for cl in clauses:
        cl_plain = _strip_clause_punct(cl)
        if not cl_plain:
            continue
        if len(cl_plain) <= eff_cap:
            _add_cand(cl_plain)
        toks = _atomic_semantic_tokens(cl_plain, extra_terms=prot_terms)
        for cap in ((max_chars, eff_cap) if allow_plus_two else (max_chars,)):
            acc = ""
            for tk in toks:
                if len(acc) + len(tk) <= cap:
                    acc += tk
                    if len(acc) >= min_chars:
                        _add_cand(acc)
                else:
                    break
        # 仅允许跳过明确的句首话语/序数引导词（绝不剥离否定前缀、绝不落到系词「是」或词中）
        lead_idx = 0
        while lead_idx < len(toks) - 1:
            tk_l = toks[lead_idx]
            if (tk_l in _DISCOURSE_LEADIN_TOKENS or _ORDINAL_ONLY_RE.match(tk_l)) and not _NEGATION.search(tk_l):
                lead_idx += 1
            else:
                break
        if lead_idx > 0 and not _starts_with_copula_or_particle(toks[lead_idx]):
            for cap in ((max_chars, eff_cap) if allow_plus_two else (max_chars,)):
                tail = ""
                for tk in toks[lead_idx:]:
                    if len(tail) + len(tk) <= cap:
                        tail += tk
                        if len(tail) >= min_chars:
                            _add_cand(tail, allow_neg_drop=False, allow_copula_head=False)
                    else:
                        break
        # 补入「的/嘅」之后的干净名物性中心语短语（如「播客录音棚」「低频驻波处理」，仅保留 >= 4 字的实词短语）
        for tk_i, tk_v in enumerate(toks[:-1]):
            if tk_v in ("的", "嘅"):
                for cap in ((max_chars, eff_cap) if allow_plus_two else (max_chars,)):
                    np_tail = ""
                    for tk_sub in toks[tk_i + 1:]:
                        if len(np_tail) + len(tk_sub) <= cap:
                            np_tail += tk_sub
                            if len(np_tail) >= 4:
                                _add_cand(np_tail, allow_neg_drop=False, allow_copula_head=False)
                        else:
                            break

    for ent in _extract_noun_entities(raw, max_chars=max_chars, extra_terms=extra_terms):
        _add_cand(ent, allow_neg_drop=False, allow_copula_head=False)

    if cands:
        def _score(cand: str) -> tuple:
            pairs = _pseg_tokens(cand, prot_terms=prot_terms)
            cand_toks = _atomic_semantic_tokens(cand, extra_terms=prot_terms)
            has_atomic = bool(_atomic_alnum_spans(cand))
            has_noun = any(f in _NOUN_ENTITY_POS for _, f in pairs)
            ends_aspect = cand[-1] in _DANGLING_ASPECT_PARTICLES
            is_triv = _is_trivial_lead_or_ordinal(cand, raw)
            starts_leadin = 1 if (cand_toks and (cand_toks[0] in _DISCOURSE_LEADIN_TOKENS or _ORDINAL_ONLY_RE.match(cand_toks[0]))) else 0
            severs_post_unit = 0
            if has_atomic:
                cand_spans = _atomic_alnum_spans(cand)
                if cand_spans and cand_spans[-1][1] == len(cand):
                    pos_c = raw_clean.find(cand)
                    if pos_c >= 0:
                        after_c = raw_clean[pos_c + len(cand):]
                        for rng_tok in _POST_UNIT_RANGE_TOKENS:
                            if after_c.startswith(rng_tok) and len(cand) + len(rng_tok) <= eff_cap:
                                severs_post_unit = 1
                                break
            over = max(0, len(cand) - max_chars)
            short_pen = 1 if len(cand) < 4 else 0
            return (
                1 if is_triv else 0,
                1 if ends_aspect else 0,
                starts_leadin,
                severs_post_unit,
                1 if (prefer_content and not (has_atomic or has_noun)) else 0,
                1 if over > 0 else 0,
                short_pen,
                -(1 if has_atomic else 0),
                -(1 if has_noun else 0),
                over,
                -min(len(cand), max_chars),
            )

        best = min(cands, key=_score)
        if prefer_content and fallback == "" and _is_trivial_lead_or_ordinal(best, raw):
            return fallback
        return best

    # 最终兜底：严格校验原子词界与非悬空虚词，绝不返回未过边界校验的残片
    toks = _atomic_semantic_tokens(raw_clean, extra_terms=prot_terms)
    prefixes: List[str] = []
    acc = ""
    for tk in toks:
        if len(acc) + len(tk) <= max_chars:
            acc += tk
            trimmed = _trim_edges(acc)
            if len(trimmed) >= min_chars:
                prefixes.append(trimmed)
        else:
            break
    for cand_fb in reversed(prefixes):
        if (
            not _is_dangling_head(cand_fb)
            and not (cand_fb[-1] in _DANGLING_TAIL_CHARS and not _COPULA_DE_END_RE.search(cand_fb))
            and not _breaks_atomic_or_word_boundary(cand_fb, raw, extra_terms=extra_terms)
            and not (prefer_content and _is_trivial_lead_or_ordinal(cand_fb, raw))
        ):
            return cand_fb
    for ent in _extract_noun_entities(raw, max_chars=max_chars, extra_terms=extra_terms):
        if (
            len(ent) >= min_chars
            and not _drops_clause_negation(ent)
            and not _breaks_atomic_or_word_boundary(ent, raw, extra_terms=extra_terms)
        ):
            return ent
    return fallback


def _load_pipeline_protected_terms(
    audio_events: Dict[str, Any],
    pipeline_dir: Optional[Path] = None,
    inventory: Optional[Dict[str, Any]] = None,
) -> List[str]:
    terms: List[str] = []
    seen: set[str] = set()

    def _push(t: Any) -> None:
        ts = str(t or "").strip()
        if "=>" in ts:
            ts = ts.split("=>", 1)[1].strip()
        if 2 <= len(ts) <= 12 and ts not in _BROLL_STOPWORDS and ts not in seen:
            seen.add(ts)
            terms.append(ts)

    for t in (audio_events.get("protected_terms") or []):
        _push(t)

    try:
        import step5_subtitles as s5
        raw_extra: List[str] = []
        if pipeline_dir is not None:
            pdir = Path(pipeline_dir)
            raw_intel_p = pdir / "00_raw_intel.json"
            if raw_intel_p.is_file():
                try:
                    intel = read_json(raw_intel_p)
                    for key in ("pain_keywords", "terms", "protected_terms", "entities"):
                        for k in (intel.get(key) or []):
                            ks = str(
                                k.get("name") or k.get("text") or k.get("term") or ""
                                if isinstance(k, dict) else (k or "")
                            ).strip()
                            if ks:
                                raw_extra.append(ks)
                except Exception:
                    pass
            tf = s5._terms_file_for(pdir)
            for t in s5.load_protected_terms(tf, extra_terms=raw_extra):
                _push(t)
        elif isinstance(inventory, dict):
            for key in ("raw_dir", "source_dir", "input_dir"):
                v = str(inventory.get(key) or "").strip()
                if not v:
                    continue
                for cand in (Path(v) / "_SVS工作区" / "术语表.txt", Path(v) / "术语表.txt"):
                    if cand.is_file():
                        for t in s5.load_protected_terms(cand):
                            _push(t)
                        break
    except Exception:
        pass
    terms.sort(key=len, reverse=True)
    return terms


def _resolve_subtitle_font_path(style_id: str, tpl: Optional[Dict[str, Any]] = None) -> Optional[str]:
    try:
        import common
        tpl = tpl or {}
        source_font_family = tpl.get("source_font_family") or (tpl.get("font_substitution") or {}).get("source_font")
        source_font = common.find_source_font(source_font_family, style_id, tpl.get("font_candidates"))
        return (
            source_font
            or common.find_commercial_font(tpl.get("font_candidates"), verified_only=True)
            or common.find_commercial_font()
        )
    except Exception:
        return None


def _build_semantic_subtitles(
    audio_events: Dict[str, Any],
    style_id: str,
    pipeline_dir: Optional[Path] = None,
    font_path: Optional[str] = None,
    inventory: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """懒导入 M1 step5_subtitles 通用语义分词引擎，产出 0 挂尾、0 残句、0 劈词的 semantic_subtitles。"""
    sentences = list(audio_events.get("sentences") or [])
    if not sentences:
        return []
    try:
        import step5_subtitles as s5
        import style_visual_truth as svt
    except Exception:
        return [
            {"text": str(s.get("text") or "").strip(), "keywords": _string_list(s.get("keywords"))}
            for s in sentences if str(s.get("text") or "").strip()
        ]

    tpl = s5.load_subtitle_templates().get(style_id, {})
    if font_path is None:
        font_path = _resolve_subtitle_font_path(style_id, tpl)

    if style_id == "style_02":
        chunk_fs = 54
        chunk_px = s5.SUBTITLE_SYMMETRIC_MAX_W - 8
        chunk_max_chars = 9
        tpl_max_chars = 9
        strip_punct = True
    else:
        truth_sub = dict(svt.get_visual_truth(style_id).get("subtitle_patch") or {})
        tpl_char_cap = max(8, int(tpl.get("max_chars_hard", 96)))
        base_fs = int(truth_sub.get("font_size") or tpl.get("font_size", 62))
        line_px = s5.SUBTITLE_SYMMETRIC_MAX_W - 8
        chars_by_font = max(4, int(s5.SUBTITLE_SYMMETRIC_MAX_W // max(1, base_fs)))
        cfg_max_chars = int(truth_sub.get("max_chars_per_line") or tpl.get("max_chars_per_line", 10))
        tpl_max_chars = max(4, min(tpl_char_cap, cfg_max_chars, chars_by_font))
        dual_banner = bool(tpl.get("dual_line_banner"))
        chunk_fs = min(84, max(62, base_fs)) if dual_banner else min(base_fs, max(54, int((line_px - 36) // 10)))
        chunk_px = int(line_px * 1.85) if dual_banner else line_px
        chunk_max_chars = max(8, tpl_max_chars, min(tpl_char_cap, max(8, cfg_max_chars))) if dual_banner else max(9, tpl_max_chars)
        strip_punct = bool(tpl.get("punct_stripped", True))

    prot_terms = _load_pipeline_protected_terms(audio_events, pipeline_dir=pipeline_dir, inventory=inventory)
    merged = s5.rebalance_asr_sentences(sentences, max_chars=tpl_max_chars)
    raw_entries: List[Dict[str, Any]] = []
    for sent in merged:
        sent_eff = dict(sent)
        raw_words = sent_eff.get("words")
        if isinstance(raw_words, list) and any(
            not isinstance(w, dict) or "start_sec" not in w or "end_sec" not in w
            for w in raw_words
        ):
            sent_eff.pop("words", None)
        s_txt_full = str(sent_eff.get("text") or "")
        cleaned_kws = _clean_sent_keywords(sent_eff, min_len=2, max_len=8, extra_terms=prot_terms)
        sent_eff["keywords"] = cleaned_kws
        if isinstance(sent_eff.get("display_keywords"), list):
            sent_eff["display_keywords"] = _clean_sent_keywords(
                {"text": s_txt_full, "keywords": sent_eff["display_keywords"]},
                min_len=2, max_len=8, extra_terms=prot_terms,
            ) or cleaned_kws
        sent_prot = _dynamic_protected_terms(s_txt_full, extra_terms=prot_terms)
        chunks = s5._merge_short_chunks(
            list(s5.split_sentence_into_chunks(
                sent_eff,
                max_chars=chunk_max_chars,
                font_path=font_path,
                font_size=chunk_fs,
                max_safe_px=chunk_px,
                strip_punct=strip_punct,
                protected_terms=sent_prot,
            )),
            max_px=chunk_px,
            font_path=font_path,
            font_size=chunk_fs,
            protected_terms=sent_prot,
        )
        for ch in chunks:
            txt = str(ch.get("text") or "").strip()
            if not txt:
                continue
            kws = [str(k).strip() for k in (ch.get("keywords") or cleaned_kws) if str(k).strip() and str(k).strip() in txt]
            raw_entries.append({
                "sentence_id": str(sent_eff.get("id") or ""),
                "text": txt,
                "start_sec": float(ch.get("start_sec") or sent_eff.get("start_sec") or 0.0),
                "end_sec": float(ch.get("end_sec") or sent_eff.get("end_sec") or 0.0),
                "keywords": kws,
            })

    # 跨句首助词护栏（与 step5_subtitles 一致）
    cross_aux = frozenset("的地得嘅啲嘛呢吧啊呀")
    k = 0
    while k < len(raw_entries) - 1:
        cur_e = raw_entries[k]
        nxt_e = raw_entries[k + 1]
        t0 = str(cur_e["text"])
        t1 = str(nxt_e["text"])
        same_sent = cur_e["sentence_id"] == nxt_e["sentence_id"]
        aux_set = s5._BAD_SUB_HEAD_AUX if same_sent else cross_aux
        if (
            t0 and t1 and t1[0] in aux_set
            and not (t1[0] == "呢" and s5._is_cantonese_ne_demo_head(t1))
            and (same_sent or (len(t0.replace(" ", "")) >= 3 and not s5._CLAUSE_START_PROTECT_RE.match(t0.replace(" ", ""))))
            and (nxt_e["start_sec"] - cur_e["end_sec"]) <= 0.35
            and len(t0) <= 15
        ):
            ch_mv = t1[0]
            cand_t0 = t0 + ch_mv
            cand_t1 = t1[1:]
            if not cand_t1.replace(" ", ""):
                cur_e["text"] = cand_t0
                cur_e["end_sec"] = max(cur_e["end_sec"], nxt_e["end_sec"])
                cur_e["keywords"] = list(dict.fromkeys(cur_e["keywords"] + [kw for kw in nxt_e["keywords"] if kw in cand_t0]))
                raw_entries.pop(k + 1)
                continue
            if len(cand_t1.replace(" ", "")) < s5.MIN_CHUNK_CHARS:
                if len((t0 + t1).replace(" ", "")) <= 12:
                    cur_e["text"] = t0 + t1
                    cur_e["end_sec"] = max(cur_e["end_sec"], nxt_e["end_sec"])
                    cur_e["keywords"] = list(dict.fromkeys(cur_e["keywords"] + nxt_e["keywords"]))
                    raw_entries.pop(k + 1)
                    continue
            else:
                cur_e["text"] = cand_t0
                nxt_e["text"] = cand_t1
                nxt_e["keywords"] = [kw for kw in nxt_e["keywords"] if kw in cand_t1]
                k += 1
                continue
        k += 1

    out = [
        {"text": e["text"], "keywords": [kw for kw in e["keywords"] if kw in e["text"]]}
        for e in raw_entries if str(e["text"]).strip()
    ]
    if not out:
        return [
            {"text": str(s.get("text") or "").strip(), "keywords": _string_list(s.get("keywords"))}
            for s in sentences if str(s.get("text") or "").strip()
        ]
    return out


def _rebalance_sentences_for_planning(sentences: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """复用 M1 step5_subtitles 通用跨句重平衡，消除切碎的 '的/嘅' 跨句残片对封面/面板/花字的污染。"""
    if len(sentences) < 2:
        return list(sentences)
    try:
        import step5_subtitles as s5
        merged = s5.rebalance_asr_sentences(sentences, max_chars=10)
        return merged if merged else list(sentences)
    except Exception:
        return list(sentences)


def _is_clean_panel_item(txt: str, src: str = "", extra_terms: Optional[List[str]] = None) -> bool:
    t = _strip_clause_punct(txt)
    if len(t) < 2:
        return False
    if _is_dangling_head(t):
        return False
    if t[-1] in _DANGLING_TAIL_CHARS and not _COPULA_DE_END_RE.search(t):
        return False
    ref_src = _compact_zh_spaces(src) if src else t
    if (
        len(t) >= 2
        and t[1] in ("的", "嘅", "地", "得")
        and not t.startswith(_PRONOUN_DE_PREFIXES)
        and not _atomic_alnum_spans(t[:2])
        and not any(_strip_clause_punct(cl).startswith(t) for cl in _CLAUSE.split(ref_src))
    ):
        return False
    if _is_trivial_lead_or_ordinal(t, ref_src):
        return False
    if src and _breaks_atomic_or_word_boundary(t, src, extra_terms=extra_terms):
        return False
    return True


def _build_style_scene_beats(
    style_id: str,
    sentences: List[Dict[str, Any]],
    total: float,
    acts: List[Dict[str, Any]],
    prosody: Dict[str, Dict[str, Any]],
    prot_terms: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """直接消费 style_visual_truth.py 风格骨架，动态填充本片真实语料内容（绝无跨风格第8格黑卡）。"""
    if style_id == "style_02" or not sentences:
        return []
    import style_visual_truth as svt

    raw_beats = svt.get_scene_beat_specs(style_id)
    if not raw_beats:
        return []
    theme = svt.get_diagram_theme(style_id)
    act_by_sid = {sid: act for act in acts for sid in (act.get("sentence_ids") or [])}

    def _act_for_sent(s_obj: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        sid_v = str(s_obj.get("id") or "")
        if sid_v in act_by_sid:
            return act_by_sid[sid_v]
        for m_sid in (s_obj.get("merged_sentence_ids") or ()):
            if str(m_sid) in act_by_sid:
                return act_by_sid[str(m_sid)]
        return None

    is_high_density = style_id in _HIGH_DENSITY_BEAT_STYLES

    # 按严格递增顺序将每个 beat 对齐到最接近 frac*total 的句索引
    eff_raw_beats = list(raw_beats)
    if len(sentences) < len(eff_raw_beats):
        if len(sentences) == 1:
            eff_raw_beats = [eff_raw_beats[0]]
        else:
            pick_idxs = [
                round(k * (len(eff_raw_beats) - 1) / float(len(sentences) - 1))
                for k in range(len(sentences))
            ]
            eff_raw_beats = [eff_raw_beats[idx_b] for idx_b in pick_idxs]

    chosen_indices: List[int] = []
    min_idx = 0
    for b_i, rb in enumerate(eff_raw_beats):
        rem_beats = len(eff_raw_beats) - b_i - 1
        max_idx = len(sentences) - 1 - rem_beats
        if min_idx > max_idx:
            break
        target_t = float(rb.get("frac") or (0.15 + 0.18 * b_i)) * total
        best_i = min(
            range(min_idx, max_idx + 1),
            key=lambda idx: abs(float(sentences[idx]["start_sec"]) - target_t),
        )
        chosen_indices.append(best_i)
        min_idx = best_i + 1

    out_beats: List[Dict[str, Any]] = []
    for pos_i, s_idx in enumerate(chosen_indices):
        rb = eff_raw_beats[pos_i]
        sent = sentences[s_idx]
        t0 = max(0.0, float(sent["start_sec"]))
        if t0 >= total - 0.5:
            continue
        next_t0 = (
            float(sentences[chosen_indices[pos_i + 1]]["start_sec"]) - 0.12
            if pos_i + 1 < len(chosen_indices)
            else total - 0.05
        )
        avail = next_t0 - t0
        if avail < 0.55:
            continue
        base_dur = (
            (5.4 if is_high_density else 4.5)
            if total >= 25.0
            else min(float(rb.get("max_dur") or 4.2), max(1.2, total / max(1.0, len(chosen_indices) * 1.4)))
        )
        target_dur = round(min(avail, max(0.6, base_dur)), 3)

        base_lay = dict(rb.get("layout") or {})
        mode = str(base_lay.get("mode") or "")
        act = _act_for_sent(sent)
        vc = (act or {}).get("visual_copy") or {}
        vc_hl = str(vc.get("headline") or "").strip().lstrip("✕✓ ")
        quote_ex = svt.extract_quote_excerpt(sentences, s_idx, max_chars=44)
        win_txt = "，".join(
            str(sentences[k].get("text") or "").strip()
            for k in range(s_idx, min(len(sentences), s_idx + 3))
            if str(sentences[k].get("text") or "").strip()
        )
        win_prot = _dynamic_protected_terms(win_txt or quote_ex, extra_terms=prot_terms)
        if len(quote_ex) >= 44 and win_txt and len(win_txt) > 44:
            q_toks = _atomic_semantic_tokens(win_txt, extra_terms=win_prot)
            acc_q = ""
            for q_tk in q_toks:
                if len(acc_q) + len(q_tk) <= 44:
                    acc_q += q_tk
                else:
                    break
            if len(acc_q) >= 8:
                quote_ex = acc_q
        q_clauses = [c.strip(_TRIM) for c in _CLAUSE.split(quote_ex) if c.strip(_TRIM)]
        while len(q_clauses) >= 2 and (
            (q_clauses[-1][-1] in _QUOTE_DANGLING_TAILS and not _COPULA_DE_END_RE.search(q_clauses[-1]))
            or (len(quote_ex) >= 44 and win_txt and _breaks_atomic_or_word_boundary(q_clauses[-1], win_txt, extra_terms=prot_terms))
        ):
            q_clauses.pop()
        if q_clauses:
            quote_ex = "，".join(q_clauses)
        while (
            len(quote_ex) > 4
            and quote_ex[-1] in _QUOTE_DANGLING_TAILS
            and not _COPULA_DE_END_RE.search(quote_ex)
        ):
            quote_ex = quote_ex[:-1].rstrip(_TRIM)

        sent_is_trivial = _is_trivial_lead_or_ordinal(str(sent.get("text") or ""), str(sent.get("text") or ""))
        punch_src = (
            {"text": quote_ex, "keywords": [kw for off in range(3) if s_idx + off < len(sentences) for kw in (sentences[s_idx + off].get("keywords") or [])]}
            if sent_is_trivial and quote_ex
            else sent
        )
        punch = _extract_safe_punch(punch_src, vc_hl=vc_hl, max_chars=7, fallback="", extra_terms=prot_terms)
        quote_prot = _dynamic_protected_terms(quote_ex, extra_terms=prot_terms)
        panel_lines_from_dp = False

        def _panel_line_ok(ln_val: str) -> bool:
            lv = _compact_zh_spaces(ln_val)
            if not lv:
                return False
            if lv[0] in ("的", "嘅", "地", "得") and not lv.startswith(("的确", "的士", "德国", "地方", "地区", "地址", "地铁", "地面", "地球", "得到", "得意", "得分", "得力")):
                return False
            if lv[-1] in _QUOTE_DANGLING_TAILS and not _COPULA_DE_END_RE.search(lv):
                return False
            return True

        try:
            import step5_subtitles as s5
            segs = s5.dp_segment_short_lines(
                quote_ex,
                max_width_px=960,
                font_path=None,
                font_size=50,
                terms=quote_prot,
                max_chars=12,
                strip_punct=True,
            )
            raw_pl = [_compact_zh_spaces(seg[2]) for seg in segs if _compact_zh_spaces(seg[2])]
            if len(raw_pl) >= 2:
                for pi in range(len(raw_pl) - 1):
                    cur_pl, nxt_pl = raw_pl[pi], raw_pl[pi + 1]
                    needs_shift = (
                        _breaks_atomic_or_word_boundary(cur_pl, quote_ex, extra_terms=quote_prot)
                        or (cur_pl[-1] in _QUOTE_DANGLING_TAILS and not _COPULA_DE_END_RE.search(cur_pl))
                    )
                    if needs_shift:
                        pl_toks = _atomic_semantic_tokens(cur_pl, extra_terms=quote_prot)
                        tail_tk = pl_toks[-1] if len(pl_toks) >= 2 else (cur_pl[-1:] if len(cur_pl) >= 4 else "")
                        if tail_tk:
                            cand_cur = "".join(pl_toks[:-1]) if len(pl_toks) >= 2 else cur_pl[:-1]
                            cand_nxt = tail_tk + nxt_pl
                            if (
                                len(cand_cur) >= 3
                                and len(cand_nxt) <= 13
                                and not _is_dangling_head(tail_tk)
                                and not _is_dangling_head(cand_cur)
                                and cand_cur[-1] not in _QUOTE_DANGLING_TAILS
                                and not _atomic_alnum_spans(cand_cur[-1:] + tail_tk[:1])
                                and _panel_line_ok(cand_cur)
                            ):
                                raw_pl[pi] = cand_cur
                                raw_pl[pi + 1] = cand_nxt
            panel_lines = [ln for ln in raw_pl if _panel_line_ok(ln)][:4]
            panel_lines_from_dp = bool(panel_lines)
        except Exception:
            panel_lines = []
        if not panel_lines:
            snapped_pl = [
                _snap_phrase_to_boundary(p, max_chars=12, fallback="", allow_plus_two=True, extra_terms=quote_prot)
                for p in _CLAUSE.split(quote_ex) if _compact_zh_spaces(p)
            ]
            panel_lines = [x for x in snapped_pl if x and _panel_line_ok(x)][:4]
            if not panel_lines and punch:
                panel_lines = [punch]

        def _fit_panel_item(ln: str, max_chars: int = 12, src: str = quote_ex) -> str:
            t_ln = _compact_zh_spaces(ln)
            if not t_ln:
                return ""
            ascii_n = sum(1 for ch in t_ln if ch.isascii() and not ch.isspace())
            cjk_n = len(t_ln.replace(" ", "")) - ascii_n
            eff_n = (cjk_n + (ascii_n * 55 + 99) // 100) if ascii_n >= 4 else len(t_ln.replace(" ", ""))
            if panel_lines_from_dp and eff_n <= max_chars + 2 and len(t_ln) <= max_chars + 4 and _is_clean_panel_item(t_ln, src or t_ln, extra_terms=prot_terms):
                return t_ln
            try:
                import step5_subtitles as s5
                sub_segs = s5.dp_segment_short_lines(
                    t_ln,
                    max_width_px=max(480, max_chars * 80),
                    font_path=None,
                    font_size=50,
                    terms=_dynamic_protected_terms(t_ln, extra_terms=prot_terms),
                    max_chars=max_chars,
                    strip_punct=True,
                )
                if sub_segs:
                    cand_sub = _compact_zh_spaces(sub_segs[0][2])
                    if cand_sub and _is_clean_panel_item(cand_sub, src or t_ln, extra_terms=prot_terms):
                        return cand_sub
            except Exception:
                pass
            snapped = _snap_phrase_to_boundary(t_ln, max_chars=max_chars, fallback="", allow_plus_two=True, extra_terms=prot_terms)
            return snapped if (snapped and _is_clean_panel_item(snapped, src or t_ln, extra_terms=prot_terms)) else ""

        title_src = quote_ex if sent_is_trivial and quote_ex else str(sent.get("text") or "")
        panel_title = (
            _snap_phrase_to_boundary(
                title_src,
                max_chars=8,
                fallback=punch,
                allow_plus_two=False,
                prefer_content=True,
                extra_terms=prot_terms,
            )
            if mode in ("paper_outline_pip", "concept_diagram_pip")
            else (_snap_phrase_to_boundary(punch, max_chars=7, fallback=punch, allow_plus_two=False, extra_terms=prot_terms) if punch else "")
        )
        if panel_title and title_src and _breaks_atomic_or_word_boundary(panel_title, title_src, extra_terms=prot_terms):
            ent_fb = _extract_noun_entities(title_src, max_chars=8, extra_terms=prot_terms)
            panel_title = ent_fb[0] if ent_fb else punch
        role_sub_map = {
            "hook": "核心开篇", "twist": "关键转折", "framework": "方法拆解",
            "proof": "一线实证", "cta": "行动建议", "setup": "背景拆解",
        }
        panel_sub = role_sub_map.get(str((act or {}).get("dramatic_role") or ""), "重点提炼")

        lay = dict(base_lay)
        lay["panel_title"] = panel_title
        lay["giant_back_text"] = punch
        lay["panel_subtitle"] = panel_sub
        lay["quote_text"] = quote_ex
        lay["panel_lines"] = panel_lines
        lay["citation"] = f"—— {punch}" if punch else ""

        if mode == "paper_outline_pip":
            raw_items: List[str] = []
            for x in (vc.get("items") or []):
                xs = str(x).lstrip("✕✓· ").strip()
                if not xs:
                    continue
                if " · " in xs and len(xs) <= 14:
                    raw_items.append(xs)
                elif _is_clean_panel_item(xs, str(sent.get("text") or quote_ex), extra_terms=prot_terms):
                    raw_items.append(xs)
            if len(raw_items) < 3:
                for offset in range(0, 5):
                    if s_idx + offset < len(sentences):
                        s_cand_txt = str(sentences[s_idx + offset].get("text") or "")
                        cand = _snap_phrase_to_boundary(
                            s_cand_txt, max_chars=11, fallback="", allow_plus_two=True, prefer_content=True,
                            extra_terms=prot_terms,
                        )
                        if (
                            cand
                            and _is_clean_panel_item(cand, s_cand_txt, extra_terms=prot_terms)
                            and cand not in raw_items
                        ):
                            raw_items.append(cand)
                    if len(raw_items) >= 3:
                        break
            for ln in panel_lines:
                if len(raw_items) >= 3:
                    break
                snapped_ln = _fit_panel_item(ln, max_chars=11, src=quote_ex)
                if snapped_ln and _is_clean_panel_item(snapped_ln, quote_ex, extra_terms=prot_terms) and snapped_ln not in raw_items:
                    raw_items.append(snapped_ln)
            if len(raw_items) < 3:
                for ln in panel_lines:
                    if len(raw_items) >= 3:
                        break
                    snapped_ln = _fit_panel_item(ln, max_chars=11, src=quote_ex)
                    if (
                        snapped_ln
                        and not _is_dangling_head(snapped_ln)
                        and snapped_ln[-1] not in _DANGLING_TAIL_CHARS
                        and not _breaks_atomic_or_word_boundary(snapped_ln, quote_ex, extra_terms=prot_terms)
                        and snapped_ln not in raw_items
                    ):
                        raw_items.append(snapped_ln)
            clean_items: List[str] = []
            for txt in raw_items[:3]:
                if " · " in txt and len(txt) <= 14:
                    item_txt = txt
                else:
                    item_txt = _fit_panel_item(txt, max_chars=12, src=win_txt or quote_ex)
                if item_txt:
                    clean_items.append(item_txt)
            lay["panel_items"] = [
                {"ord": ord_zh, "text": txt}
                for ord_zh, txt in zip(("一、", "二、", "三、"), clean_items)
            ]
        elif mode == "review_whiteboard_pip":
            wb_cands: List[str] = []
            for ln in panel_lines:
                t_ln = _compact_zh_spaces(ln)
                if not t_ln:
                    continue
                ascii_n = sum(1 for ch in t_ln if ch.isascii() and not ch.isspace())
                cjk_n = len(t_ln.replace(" ", "")) - ascii_n
                eff_n = (cjk_n + (ascii_n * 55 + 99) // 100) if ascii_n >= 4 else len(t_ln.replace(" ", ""))
                if eff_n > 13 or len(t_ln) > 16:
                    try:
                        import step5_subtitles as s5
                        sub_segs = s5.dp_segment_short_lines(
                            t_ln,
                            max_width_px=800,
                            font_path=None,
                            font_size=50,
                            terms=_dynamic_protected_terms(t_ln, extra_terms=prot_terms),
                            max_chars=10,
                            strip_punct=True,
                        )
                        parts = [_compact_zh_spaces(sg[2]) for sg in sub_segs if _compact_zh_spaces(sg[2])]
                        for pt_wb in (parts or []):
                            if _panel_line_ok(pt_wb):
                                wb_cands.append(pt_wb)
                    except Exception:
                        fitted_wb = _fit_panel_item(t_ln, max_chars=10, src=quote_ex)
                        if fitted_wb:
                            wb_cands.append(fitted_wb)
                elif _panel_line_ok(t_ln):
                    wb_cands.append(t_ln)
            wb_filtered = [ln for ln in wb_cands if _is_clean_panel_item(ln, quote_ex, extra_terms=prot_terms)]
            if len(wb_filtered) < 2:
                for ln in wb_cands:
                    if ln not in wb_filtered and _panel_line_ok(ln):
                        wb_filtered.append(ln)
            lay["panel_items"] = [{"text": ln} for ln in wb_filtered[:2]]
        elif mode == "concept_diagram_pip":
            s_txt = str(sent.get("text") or "")
            vc_items = [str(x).strip() for x in (vc.get("items") or []) if str(x).strip()]
            src_cmp = win_txt or quote_ex or s_txt
            clean_vc_items: List[str] = []
            for raw_vc_it in vc_items:
                lbl = raw_vc_it.lstrip("✕✓ ").strip()
                if not lbl:
                    continue
                if _breaks_atomic_or_word_boundary(lbl, src_cmp, extra_terms=prot_terms) or not _is_clean_panel_item(lbl, src_cmp, extra_terms=prot_terms):
                    lbl = _snap_phrase_to_boundary(
                        lbl, max_chars=8, fallback="", allow_plus_two=True, extra_terms=prot_terms,
                    )
                if lbl and not _breaks_atomic_or_word_boundary(lbl, src_cmp, extra_terms=prot_terms) and _is_clean_panel_item(lbl, src_cmp, extra_terms=prot_terms):
                    clean_vc_items.append(lbl)
            if len(clean_vc_items) >= 2 and (any(x.startswith(("✕", "✓")) for x in vc_items) or "不是" in s_txt or "而是" in s_txt):
                dmode = "compare"
                lay["panel_items"] = [
                    {"label": clean_vc_items[0], "points": panel_lines[:2]},
                    {"label": clean_vc_items[1], "points": panel_lines[1:3] or panel_lines[:2]},
                ]
            elif any(k in s_txt for k in ("所以", "因此", "从而", "才能", "就会")):
                dmode = "chain"
                pts = (clean_vc_items or panel_lines)[:3]
                cd_labels = [
                    (_fit_panel_item(pt, max_chars=10, src=quote_ex) if pt in panel_lines else _snap_phrase_to_boundary(pt, max_chars=10, fallback="", allow_plus_two=True, extra_terms=prot_terms))
                    for pt in pts
                ]
                cd_labels = [lb for lb in cd_labels if lb]
                lay["panel_items"] = [
                    {"ord": "①②③"[idx_p], "label": lb, "desc": ""}
                    for idx_p, lb in enumerate(cd_labels[:3])
                ]
            else:
                dmode = "trio"
                pts = (clean_vc_items or panel_lines)[:3]
                cd_labels = [
                    (_fit_panel_item(pt, max_chars=10, src=quote_ex) if pt in panel_lines else _snap_phrase_to_boundary(pt, max_chars=10, fallback="", allow_plus_two=True, extra_terms=prot_terms))
                    for pt in pts
                ]
                cd_labels = [lb for lb in cd_labels if lb]
                lay["panel_items"] = [
                    {"ord": "①②③"[idx_p], "label": lb, "desc": ""}
                    for idx_p, lb in enumerate(cd_labels[:3])
                ]
            lay["diagram_mode"] = dmode
            lay["light_theme"] = bool(theme.get("light_theme", False))
            lay["accent_hex"] = str(theme.get("accent") or "#FFE14D")
            lay["accent2_hex"] = str(theme.get("accent2") or "#DC2626")
            lay["font_role"] = str(theme.get("font_role") or "sans")

        out_beats.append({
            "sentence_id": sent["id"],
            "target_sec": round(t0, 3),
            "frac": round(float(rb.get("frac") or (t0 / max(1.0, total))), 4),
            "max_dur": float(rb.get("max_dur") or 4.2),
            "target_dur": target_dur,
            "layout": lay,
        })
    return out_beats


def _build_style_callouts(
    style_id: str,
    sentences: List[Dict[str, Any]],
    total: float,
    acts: List[Dict[str, Any]],
    prosody: Dict[str, Dict[str, Any]],
    style_scene_beats: List[Dict[str, Any]],
    prot_terms: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """消费 style_visual_truth.get_callout_templates 动态生成非面板时段前景花字。"""
    if style_id == "style_02" or not sentences:
        return []
    import style_visual_truth as svt

    slots = svt.get_callout_templates(style_id)
    if not slots:
        return []

    beat_wins = [
        (
            float(b.get("target_sec") or 0.0) - 0.30,
            float(b.get("target_sec") or 0.0) + float(b.get("target_dur") or b.get("max_dur") or 3.0) + 0.30,
        )
        for b in style_scene_beats
    ]
    act_by_sid = {sid: act for act in acts for sid in (act.get("sentence_ids") or [])}

    def _act_for_sent(s_obj: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        sid_v = str(s_obj.get("id") or "")
        if sid_v in act_by_sid:
            return act_by_sid[sid_v]
        for m_sid in (s_obj.get("merged_sentence_ids") or ()):
            if str(m_sid) in act_by_sid:
                return act_by_sid[str(m_sid)]
        return None

    def _free_spans(st: float, en: float) -> List[tuple[float, float]]:
        spans = [(max(0.0, st), min(total, en))]
        for bs, be in sorted(beat_wins):
            nxt: List[tuple[float, float]] = []
            for a, b in spans:
                if b <= bs or a >= be:
                    nxt.append((a, b))
                else:
                    if a < bs:
                        nxt.append((a, bs))
                    if be < b:
                        nxt.append((be, b))
            spans = nxt
        return [(a, b) for a, b in spans if (b - a) >= 1.55]

    ranked: List[tuple[float, int, float, float, Dict[str, Any]]] = []
    for idx_s, sent in enumerate(sentences):
        free = _free_spans(float(sent["start_sec"]), float(sent["end_sec"]))
        if not free:
            continue
        seg_s, seg_e = max(free, key=lambda x: x[1] - x[0])
        sid = str(sent.get("id") or "")
        act = _act_for_sent(sent) or {}
        pr = prosody.get(sid) or {}
        score = 0.0
        if pr.get("acoustic_emphasis"):
            score += 3.0
        if act.get("dramatic_role") in ("twist", "proof", "cta", "hook"):
            score += 2.5
        if _numeric_focus(str(sent.get("text") or ""))[0]:
            score += 2.0
        if _clean_sent_keywords(sent, min_len=2, max_len=8, extra_terms=prot_terms):
            score += 1.0
        score += min(1.5, (seg_e - seg_s) * 0.3)
        ranked.append((score, idx_s, seg_s, seg_e, sent))

    ranked.sort(key=lambda x: (-x[0], x[1]))
    chosen_wins: List[tuple[float, float]] = []
    out_callouts: List[Dict[str, Any]] = []
    for slot in slots:
        pick = None
        for cand in ranked:
            _, idx_s, seg_s, seg_e, sent = cand
            cs = round(seg_s, 3)
            ce = round(min(seg_e, cs + 2.8), 3)
            if ce - cs < 1.55:
                continue
            if any(min(ce, ow_e + 0.5) - max(cs, ow_s - 0.5) > 0.0 for ow_s, ow_e in chosen_wins):
                continue
            s_txt_co = str(sent.get("text") or "")
            act = _act_for_sent(sent) or {}
            vc = act.get("visual_copy") or {}
            raw_hl = str(vc.get("headline") or "").strip().lstrip("✕✓ ")
            if (
                2 <= len(raw_hl) <= 8
                and not _is_dangling_head(raw_hl)
                and raw_hl[-1:] not in _DANGLING_TAIL_CHARS
                and not _breaks_atomic_or_word_boundary(raw_hl, s_txt_co, extra_terms=prot_terms)
                and not _is_trivial_lead_or_ordinal(raw_hl, s_txt_co)
            ):
                co_text = raw_hl
            else:
                safe_fb = _extract_safe_punch(sent, max_chars=7, fallback="", extra_terms=prot_terms)
                co_text = _snap_phrase_to_boundary(
                    s_txt_co,
                    max_chars=8,
                    fallback=safe_fb,
                    allow_plus_two=True,
                    prefer_content=True,
                    extra_terms=prot_terms,
                )
            if (
                not co_text
                or len(co_text) < 2
                or _is_dangling_head(co_text)
                or (co_text[-1] in _DANGLING_TAIL_CHARS and not _COPULA_DE_END_RE.search(co_text))
                or _breaks_atomic_or_word_boundary(co_text, s_txt_co, extra_terms=prot_terms)
                or _is_trivial_lead_or_ordinal(co_text, s_txt_co)
            ):
                continue
            pick = (cs, ce, sent, co_text, s_txt_co)
            break
        if pick is None:
            continue
        cs, ce, sent, co_text, s_txt_co = pick
        chosen_wins.append((cs, ce))
        co_obj: Dict[str, Any] = {
            "text": co_text,
            "start_sec": cs,
            "end_sec": ce,
            "variant": str(slot.get("variant") or "s08_giant_white"),
            "callout_y": int(slot.get("callout_y") or 920),
            "font_role": str(slot.get("font_role") or "sans"),
            "accent_hex": str(slot.get("accent_hex") or "#FFE14D"),
        }
        if slot.get("z_layer"):
            co_obj["z_layer"] = str(slot["z_layer"])
        if co_obj["variant"] in ("s12_pill_bubbles", "s22_floating_tags"):
            kws = _clean_sent_keywords(sent, min_len=2, max_len=6, extra_terms=prot_terms)
            if len(kws) < 2:
                try:
                    import step5_subtitles as s5
                    segs = s5.dp_segment_short_lines(
                        s_txt_co,
                        max_width_px=640,
                        font_path=None,
                        font_size=50,
                        terms=_dynamic_protected_terms(s_txt_co, extra_terms=prot_terms),
                        max_chars=6,
                        strip_punct=True,
                    )
                    for seg in segs:
                        seg_t = _snap_phrase_to_boundary(
                            seg[2], max_chars=6, fallback="", allow_plus_two=True, extra_terms=prot_terms,
                        )
                        if (
                            2 <= len(seg_t) <= 7
                            and not _is_dangling_head(seg_t)
                            and not _breaks_atomic_or_word_boundary(seg_t, s_txt_co, extra_terms=prot_terms)
                            and not _is_trivial_lead_or_ordinal(seg_t, s_txt_co)
                            and seg_t not in kws
                        ):
                            kws.append(seg_t)
                except Exception:
                    pass
            co_obj["items"] = kws[:3] if kws else [co_text]
        out_callouts.append(co_obj)
    return out_callouts


def _build_cover_and_gbro_spec(
    style_id: str,
    sentences: List[Dict[str, Any]],
    acts: List[Dict[str, Any]],
    pool: List[Dict[str, Any]],
    store: bool,
    prot_terms: Optional[List[str]] = None,
) -> tuple[Dict[str, str], Dict[str, str]]:
    """通用提取贴合本片实拍内容的封面文案与 gbro-cover-design 规格（零 BIZ/STO 硬编码）。"""
    style_m = re.search(r"(\d+)", str(style_id or "style_01"))
    style_num = int(style_m.group(1)) if style_m else 1

    # 跳过纯自我介绍短开场（如“我系牛哥”），取实质性吸睛句
    hook_cand_sents = list(sentences[:3])
    if (
        len(hook_cand_sents) >= 2
        and len(re.sub(r"[，。！？、,.!? ]+", "", str(hook_cand_sents[0].get("text") or ""))) <= 5
        and re.match(r"^(?:我[系是]|大家好|哈喽)", str(hook_cand_sents[0].get("text") or "").strip())
    ):
        hook_text = str(hook_cand_sents[1].get("text") or "")
    elif hook_cand_sents:
        hook_text = str(hook_cand_sents[0].get("text") or "")
    else:
        hook_text = "核心实拍拆解"

    head_t = _snap_phrase_to_boundary(
        hook_text, max_chars=9, fallback="核心真相", allow_plus_two=False, prefer_content=True, extra_terms=prot_terms,
    )
    act_sent_map: Dict[str, str] = {}
    for ps in sentences:
        ps_txt = str(ps.get("text") or "").strip()
        ps_id = str(ps.get("id") or "")
        if ps_id:
            act_sent_map[ps_id] = ps_txt
        for m_sid in (ps.get("merged_sentence_ids") or ()):
            if str(m_sid):
                act_sent_map[str(m_sid)] = ps_txt

    act_highlights: List[tuple[str, str]] = []
    for a in acts:
        if a.get("primitive") in _QUIET_PRIMITIVES:
            continue
        h_raw = str(a["visual_copy"].get("headline") or (a["visual_copy"].get("items") or [""])[0]).lstrip("✕✓ ").strip()
        if len(h_raw) < 2 or h_raw == head_t:
            continue
        act_src = "，".join(
            dict.fromkeys(act_sent_map.get(str(sid), "") for sid in (a.get("sentence_ids") or []) if act_sent_map.get(str(sid), ""))
        )
        if act_src and (
            _breaks_atomic_or_word_boundary(h_raw, act_src, extra_terms=prot_terms)
            or _is_trivial_lead_or_ordinal(h_raw, act_src)
            or _is_dangling_head(h_raw)
        ):
            continue
        act_highlights.append((h_raw, act_src or h_raw))

    if act_highlights:
        badge_raw, badge_src = act_highlights[(style_num - 1) % len(act_highlights)]
        badge_t = (
            badge_raw
            if (
                2 <= len(badge_raw) <= 8
                and not _is_dangling_head(badge_raw)
                and badge_raw[-1] not in _DANGLING_TAIL_CHARS
                and not _breaks_atomic_or_word_boundary(badge_raw, badge_src, extra_terms=prot_terms)
                and not _is_trivial_lead_or_ordinal(badge_raw, badge_src)
            )
            else _snap_phrase_to_boundary(
                badge_src, max_chars=8, fallback="一线全拆解", allow_plus_two=False, prefer_content=True,
                extra_terms=prot_terms,
            )
        )
    elif len(sentences) > 1:
        badge_t = _snap_phrase_to_boundary(
            str(sentences[1].get("text") or ""), max_chars=8, fallback="一线全拆解", allow_plus_two=False, prefer_content=True,
            extra_terms=prot_terms,
        )
    else:
        badge_t = "一线全拆解"
    if badge_t == head_t:
        badge_t = _snap_phrase_to_boundary(
            str(sentences[-1].get("text") or "") if len(sentences) > 1 else "",
            max_chars=8,
            fallback="实战全复盘",
            allow_plus_two=False,
            prefer_content=True,
            extra_terms=prot_terms,
        )
        if badge_t == head_t:
            badge_t = "实拍全拆解" if store else "核心全复盘"

    # 从实拍 B-roll 索引或口播实体提炼贴合本片的主体/菜品 (dish_or_subject)
    full_blob = " ".join(str(s.get("text") or "") for s in sentences)
    prot_set = set(prot_terms or ())

    def _safe_broll_tag(tag: str) -> bool:
        if any(tag != pt and tag in pt for pt in prot_set):
            return False
        if desc and tag in desc and _breaks_atomic_or_word_boundary(tag, desc, extra_terms=prot_terms):
            return False
        if full_blob and tag in full_blob and _breaks_atomic_or_word_boundary(tag, full_blob, extra_terms=prot_terms):
            return False
        return True

    dish_cands: List[str] = []
    if pool:
        scored_pool: List[tuple[int, str]] = []
        for clip in pool:
            raw_tags = [
                str(t).strip()
                for t in (clip.get("tags") or []) + (clip.get("entities") or [])
                if 2 <= len(str(t).strip()) <= 12 and (len(str(t).strip()) <= 8 or str(t).strip() in prot_set)
            ]
            desc = str(clip.get("description") or "")
            tags = [t for t in raw_tags if _safe_broll_tag(t)]
            hits = sum(1 for t in tags if t in full_blob)
            label = tags[0] if tags else ""
            if not label and prot_terms:
                label = next((pt for pt in prot_terms if pt in desc or (any(rt in pt for rt in raw_tags) and pt in full_blob)), "")
            if not label:
                cand_desc = _snap_phrase_to_boundary(desc, max_chars=8, fallback="", allow_plus_two=False, extra_terms=prot_terms)
                if cand_desc and cand_desc not in _BROLL_STOPWORDS:
                    label = cand_desc
            if label and label not in [x[1] for x in scored_pool]:
                scored_pool.append((hits, label))
        scored_pool.sort(key=lambda x: -x[0])
        dish_cands = [lbl for _, lbl in scored_pool if lbl]

    if dish_cands:
        dish_or_subject = dish_cands[(style_num - 1) % len(dish_cands)]
    else:
        entity_cands: List[str] = []
        for idx_cov, s_cov in enumerate(sentences):
            s_txt_cov = str(s_cov.get("text") or "").strip()
            for m_show in _SHOWCASE.finditer(s_txt_cov):
                # 仅取展示词紧邻的当前分句片段（含中英文逗号截断）做名词实体抽取，绝不跨句/跨逗号抓取方位子句
                local_tail = re.split(r"[，,。！？!?\n]+", s_txt_cov[m_show.end():], maxsplit=1)[0].strip()
                if not local_tail and idx_cov + 1 < len(sentences):
                    nxt_txt = str(sentences[idx_cov + 1].get("text") or "").strip()
                    local_tail = re.split(r"[，,。！？!?\n]+", nxt_txt, maxsplit=1)[0].strip()
                for ent in _extract_noun_entities(local_tail, max_chars=8, extra_terms=prot_terms):
                    if ent not in entity_cands:
                        entity_cands.append(ent)
        if not entity_cands:
            for s in sentences:
                for kw in _clean_sent_keywords(s, min_len=2, max_len=8, extra_terms=prot_terms):
                    # 仅保留真实名词/专名实体作为封面主体
                    kw_pairs = _pseg_tokens(kw, prot_terms=_dynamic_protected_terms(kw, extra_terms=prot_terms))
                    if kw_pairs and any(f in _NOUN_ENTITY_POS for _, f in kw_pairs) and kw not in entity_cands:
                        entity_cands.append(kw)
        if not entity_cands:
            for s in sentences:
                for ent in _extract_noun_entities(str(s.get("text") or ""), max_chars=8, extra_terms=prot_terms):
                    if ent not in entity_cands:
                        entity_cands.append(ent)
        if not entity_cands and prot_terms:
            entity_cands = [pt for pt in prot_terms if pt in full_blob]
        dish_or_subject = entity_cands[0] if entity_cands else (badge_t if len(badge_t) >= 2 else head_t)

    # 覆盖全部 10 套 gbro 风格按题材与素材池确定性择优
    if any(k in (head_t + badge_t + full_blob[:40]) for k in ("对比", "VS", "算账", "破产", "差价", "坏", "踩坑")):
        gbro_style = "style-04"
    elif pool:
        pool_styles = ("style-03", "style-06", "style-09", "style-01")
        gbro_style = pool_styles[(style_num - 1) % len(pool_styles)]
    else:
        talking_styles = ("style-01", "style-02", "style-05", "style-07", "style-08", "style-10")
        gbro_style = talking_styles[(style_num - 1) % len(talking_styles)]

    sub_s1_ents = _extract_noun_entities(str(sentences[1].get("text") or ""), max_chars=10, extra_terms=prot_terms) if len(sentences) > 1 else []
    sub_source = (
        dish_or_subject
        if dish_or_subject not in (head_t, badge_t)
        else (
            sub_s1_ents[0]
            if sub_s1_ents
            else (sentences[1].get("text", "") if len(sentences) > 1 else "")
        )
    )
    sub_line = _snap_phrase_to_boundary(
        sub_source,
        max_chars=10,
        fallback="",
        allow_plus_two=False,
        prefer_content=True,
        extra_terms=prot_terms,
    )
    expression = (
        "" if gbro_style == "style-08"
        else ("对比震惊" if gbro_style == "style-04" else ("推荐种草感" if store or pool else "自信笃定"))
    )

    cov_copy = {
        "headline_top": head_t,
        "highlight_badge": badge_t,
        "sub_line": sub_line,
        "gbro_style": gbro_style,
        "dish_or_subject": dish_or_subject,
    }
    gbro_spec = {
        "gbro_style": gbro_style,
        "dish_or_subject": dish_or_subject,
        "expression": expression,
        "headline_top": head_t,
        "highlight_badge": badge_t,
        "sub_line": sub_line,
    }
    return cov_copy, gbro_spec


def generate_deterministic_director_plan(
    audio_events: Dict[str, Any], style_id: str = "style_02",
    inventory: Optional[Dict[str, Any]] = None,
    composition_profile: Optional[Dict[str, Any]] = None,
    visual_index: Optional[Dict[str, Any]] = None,
    pipeline_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """用锁定原话、声学韵律特征、构图画像和实拍素材离线编排总谱。"""
    sentences, total = _timeline(audio_events)
    pool = _broll_pool(inventory, visual_index)
    store_cues = set(_STORE_TERMS.findall("，".join(s.get("text", "") for s in sentences)))
    theme_hint = (composition_profile or {}).get("theme_hint")
    store = (
        len(store_cues) >= 3
        or bool(store_cues and (len(pool) >= 2 or theme_hint == "store_vibrant"))
        or bool(len(store_cues) >= 2 and pool)
    )
    is_auto_profile = bool(
        (composition_profile or {}).get("step") == "01b_composition_profile"
        or "aroll_profiles" in (composition_profile or {})
    )
    use_unified = (style_id == "style_02") and (
        is_auto_profile or (theme_hint not in ("business_editorial", "store_vibrant"))
    )
    prosody = _sentence_prosody_metrics(sentences, audio_events)
    prot_terms = _load_pipeline_protected_terms(audio_events, pipeline_dir=pipeline_dir, inventory=inventory)

    candidates: List[_Candidate] = []
    index = 0
    while index < len(sentences):
        end = _group_end(sentences, index, store)
        group = sentences[index:end]
        text = _group_text(group)
        gp = _group_prosody(group, prosody)
        primitive, visual = _propose(
            text, group, store, not index, _allows_behind_person(composition_profile, group),
            gp=gp, extra_terms=prot_terms,
        )
        if visual["headline"] or visual["items"] or visual["accent_token"]:
            # 摘词候选仍需按同一套溯源规则过一遍，避免数字在截取时失掉单位或词义。
            copy_errors: List[str] = []
            visual = _validate_copy(visual, "visual_copy", group, copy_errors)
            if copy_errors:
                primitive, visual = "plain_bridge", _visual_copy()
        if use_unified and primitive not in _QUIET_PRIMITIVES:
            primitive = _LEGACY_TO_UNIFIED_PRIMITIVE.get(primitive, primitive)
        candidates.append((group, primitive, visual))
        index = end

    target_band = _UNIFIED_THEME_TARGETS if use_unified else (_TARGET_LOW, _TARGET_HIGH)
    highlights = _choose_highlights(candidates, total, prosody=prosody, target_band=target_band)
    roles = {
        "negation_strike": "twist", "concept_split_card": "twist", "semantic_diagram_card": "twist",
        "pain_slam_danmaku": "twist", "left_right_punch": "twist", "giant_anchor_sub": "twist",
        "framework_pillar": "framework", "stacked_dual_color": "framework",
        "list_build_gather_dim_overlay": "proof", "list_gather_depth": "proof",
        "num_anchor_dim_overlay": "proof", "number_burst_pair": "proof",
        "inline_emphasis_cta": "cta", "inline_highlight_cta": "cta",
    }
    acts = []
    had_breath = False
    end_zoom = 1.0
    prev_cam = "steady_hold"
    for index, (group, primitive, visual) in enumerate(candidates):
        highlight = index in highlights
        dur_act = max(0.1, _span(group)[1] - _span(group)[0])
        if highlight:
            role = "hook" if index == 0 else roles.get(primitive, "proof")
            intensity = 0.92 if primitive in (
                "negation_strike", "concept_split_card", "semantic_diagram_card",
                "pain_slam_danmaku", "left_right_punch", "giant_anchor_sub",
            ) else 0.75
            if primitive == "concept_split_card":
                camera = "side_shift_card"
                end_zoom = 1.15
            elif (role == "twist" and prev_cam not in ("punch_closeup", "jump_closeup")
                  and (index > 0 or dur_act >= 2.0)
                  and primitive in ("negation_strike", "semantic_diagram_card", "pain_slam_danmaku", "left_right_punch")):
                camera = "jump_closeup" if use_unified else ("punch_closeup" if not store else "slow_push_115")
                end_zoom = 1.22 if camera == "jump_closeup" else (1.16 if camera == "punch_closeup" else 1.15)
            elif end_zoom <= 1.02 and prev_cam != "slow_push_115":
                camera = "slow_push_115"
                end_zoom = 1.15
            elif dur_act >= 1.5 and prev_cam != "slow_pull_115":
                camera = "slow_pull_115"
                end_zoom = 1.00
            else:
                camera = "steady_hold"
                end_zoom = 1.00
            budget = min(3, max(1, len(visual["items"]))) if primitive in ("negation_strike", "pain_slam_danmaku", "opening_triad") else 1
        else:
            breath = index - 1 in highlights or not had_breath
            had_breath = had_breath or breath
            primitive = "clean_breath" if breath else "plain_bridge"
            visual, role, budget = _visual_copy(), "setup", 0
            intensity = 0.18 if breath else 0.31
            if breath:
                if end_zoom > 1.02:
                    camera = "slow_pull_115"
                    end_zoom = 1.00
                elif index + 1 < len(candidates) and (index + 1) in highlights:
                    # 下一幕马上要推近或侧移，呼吸幕先稳住等上一拍完全收稳，绝不连续连推弹回
                    camera = "steady_hold"
                    end_zoom = 1.00
                else:
                    camera = "slow_push_115"
                    end_zoom = 1.15
            else:
                camera = "steady_hold"
                end_zoom = 1.00
        prev_cam = camera
        kw_hits = _match_broll_keywords(group, pool)
        lock_prims = {
            "clean_breath", "hero_anchor_plus_danmaku", "pain_slam_danmaku",
            "negation_strike", "concept_split_card", "semantic_diagram_card",
            "opening_triad", "left_right_punch",
        }
        allows_broll = (
            bool(pool)
            and role != "hook"
            and (role != "cta" or bool(kw_hits))
            and primitive not in lock_prims
            and (store or bool(kw_hits))
        )
        act_obj: Dict[str, Any] = {
            "act_id": f"act_{index + 1:02d}",
            "sentence_ids": [sentence["id"] for sentence in group],
            "dramatic_role": role,
            "emotion_intensity": intensity,
            "primitive": primitive,
            "visual_copy": visual,
            "camera_intent": camera,
            "broll_policy": "allow_verified_broll" if allows_broll else "lock_aroll",
            "sfx_budget": budget,
        }
        if camera == "side_shift_card":
            act_obj["side_shift_x"] = _resolve_side_shift_x(composition_profile, group)
        if allows_broll and kw_hits:
            act_obj["broll_keywords"] = kw_hits
        acts.append(act_obj)
    out_theme = "style_02_unified" if use_unified else ("store_vibrant" if store else "business_editorial")
    plan_sentences = _rebalance_sentences_for_planning(sentences)
    plan_prosody = _sentence_prosody_metrics(plan_sentences, audio_events)
    cov_copy, gbro_spec = _build_cover_and_gbro_spec(
        style_id, plan_sentences, acts, pool, store, prot_terms=prot_terms,
    )
    native_mode = (style_id != "style_02")
    sem_subtitles = _build_semantic_subtitles(audio_events, style_id, pipeline_dir=pipeline_dir, inventory=inventory)
    style_beats = _build_style_scene_beats(style_id, plan_sentences, total, acts, plan_prosody, prot_terms=prot_terms)
    style_callouts = _build_style_callouts(
        style_id, plan_sentences, total, acts, plan_prosody, style_beats, prot_terms=prot_terms,
    )

    result = validate_director_plan({
        "step": "03b_director_plan",
        "source": "fallback_deterministic",
        "style_id": style_id,
        "native_style_mode": native_mode,
        "theme_profile": out_theme,
        "cover_copy": cov_copy,
        "gbro_cover_spec": gbro_spec,
        "semantic_subtitles": sem_subtitles,
        "style_scene_beats": style_beats,
        "style_callouts": style_callouts,
        "acts": acts,
        "music_cues": _music_cues(candidates, total, store, highlights),
    }, audio_events, composition_profile)
    if not result["ok"]:
        raise ValueError("；".join(result["errors"]))
    return result["sanitized_plan"]


def load_or_build_director_plan(
    pipeline_dir: Path, style_id: str = "style_02", generate_fallback: bool = True,
) -> Optional[Dict[str, Any]]:
    """优先读取 Agent 总谱，必要时当场回退，供只读流水线步骤调用。"""
    pipeline_dir = Path(pipeline_dir)
    plan_path = pipeline_dir / PLAN_FILENAME
    if not plan_path.is_file() and not generate_fallback:
        return None
    audio = _optional_document(pipeline_dir, _AUDIO_FILENAME)
    if audio is None:
        if plan_path.is_file():
            print(f"[director_plan] [WARN] 缺少有效的 {_AUDIO_FILENAME}，无法校验总谱", file=sys.stderr)
        return None
    profile = _optional_document(pipeline_dir, _COMPOSITION_FILENAME)
    plan = _optional_document(pipeline_dir, PLAN_FILENAME)
    if plan is not None:
        plan_style = plan.get("style_id")
        stale_style = bool(plan_style and plan_style != style_id)
        stale_style02 = (
            style_id == "style_02"
            and plan.get("source") == "fallback_deterministic"
            and plan.get("theme_profile") != "style_02_unified"
        )
        if stale_style or stale_style02:
            print(
                f"[director_plan] [WARN] {PLAN_FILENAME} 与当前风格/统一主题不匹配"
                f"（plan_style={plan_style!r}, style_id={style_id!r}, theme_profile={plan.get('theme_profile')!r}），将重新生成",
                file=sys.stderr,
            )
            plan = None
    if plan is not None:
        result = validate_director_plan(plan, audio, profile)
        if result["ok"]:
            return result["sanitized_plan"]
        print(f"[director_plan] [WARN] {PLAN_FILENAME} 校验失败（{'；'.join(result['errors'])}）", file=sys.stderr)
    if generate_fallback:
        try:
            return generate_deterministic_director_plan(
                audio, style_id,
                _optional_document(pipeline_dir, _INVENTORY_FILENAME),
                profile,
                _optional_document(pipeline_dir, "00_visual_index.json"),
                pipeline_dir=pipeline_dir,
            )
        except ValueError as exc:
            print(f"[director_plan] [WARN] 无法生成离线总谱（{exc}）", file=sys.stderr)
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    tasks = commands.add_parser("build-tasks", help="生成供 Agent 审阅的任务清单")
    apply = commands.add_parser("apply-plan", help="校验 Agent 编写的总谱并保存")
    fallback = commands.add_parser("fallback", help="用锁定原话离线生成并保存总谱")
    for command in (tasks, apply, fallback):
        command.add_argument("--pipeline-dir", type=Path, required=True)
        command.add_argument("--out", type=Path)
    tasks.add_argument("--style-id", default="style_02")
    fallback.add_argument("--style-id", default="style_02")
    apply.add_argument("--plan", "--plan-path", type=Path, help=f"默认从 {PLAN_FILENAME} 读取")
    args = parser.parse_args()
    pipeline_dir = args.pipeline_dir.expanduser()

    try:
        if args.command == "build-tasks":
            print(build_director_tasks(pipeline_dir, args.style_id, args.out))
            return
        audio = read_json(pipeline_dir / _AUDIO_FILENAME)
        profile = _optional_document(pipeline_dir, _COMPOSITION_FILENAME)
        inv_doc = _optional_document(pipeline_dir, _INVENTORY_FILENAME)
        vi_doc = _optional_document(pipeline_dir, "00_visual_index.json")
        bpool = _broll_pool(inv_doc, vi_doc)
        valid_broll_names = (
            {n for item in bpool for n in (item.get("filename"), _media_name(item.get("filename"))) if n}
            if bpool else None
        )
        output = args.out if args.out is not None else pipeline_dir / PLAN_FILENAME
        if args.command == "apply-plan":
            plan = read_json(args.plan if args.plan is not None else pipeline_dir / PLAN_FILENAME)
        else:
            plan = generate_deterministic_director_plan(
                audio, args.style_id,
                inv_doc,
                profile,
                vi_doc,
                pipeline_dir=pipeline_dir,
            )
        result = validate_director_plan(plan, audio, profile, valid_broll_names=valid_broll_names)
        if not result["ok"]:
            raise ValueError("\n".join(result["errors"]))
        for warning in result["warnings"]:
            print(f"[director_plan] [WARN] {warning}", file=sys.stderr)
        print(write_json(output, result["sanitized_plan"]))
    except (OSError, ValueError) as exc:
        parser.exit(1, f"[director_plan] {exc}\n")


if __name__ == "__main__":
    main()
