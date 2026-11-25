#!/usr/bin/env python3
"""从锁定口播生成导演任务、校验写回的总谱，或离线生成总谱。"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from difflib import SequenceMatcher
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, List, Optional

from common import read_json, write_json

TASKS_FILENAME = "03b_director_tasks.json"
PLAN_FILENAME = "03b_director_plan.json"
DIRECTOR_PRIMITIVES = (
    "hero_intro_pop", "staged_3tier_build", "hero_anchor_plus_danmaku",
    "list_build_gather_dim_overlay", "entity_stack_plus_top", "num_anchor_dim_overlay",
    "inline_emphasis_cta", "plain_bridge", "negation_strike", "framework_pillar",
    "concept_split_card", "clean_breath",
)
THEME_PROFILES = ("store_vibrant", "business_editorial")

_AUDIO_FILENAME = "03_audio_events.json"
_COMPOSITION_FILENAME = "01b_composition_profile.json"
_INVENTORY_FILENAME = "00_inventory.json"
_CAMERAS = ("steady_hold", "slow_push_115", "side_shift_card")
_ROLES = ("hook", "setup", "twist", "framework", "proof", "cta")
_BROLL_POLICIES = ("lock_aroll", "allow_verified_broll")
_MUSIC_CUES = ("delay_entry", "silence_drop", "section_swell")
_QUIET_PRIMITIVES = ("plain_bridge", "clean_breath")
_TARGET_LOW, _TARGET_HIGH = 0.35, 0.55
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
_DIGIT_RUN = r"(?:[0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?)"
_DIGITS = f"0-9{_CN_DIGITS}"
_CN_POINT = rf"(?<=[{_DIGITS}])[點点](?=[{_DIGITS}])"
_NUMERAL = rf"(?:{_DIGIT_RUN}|[{_CN_DIGITS}十百千万亿萬億]|{_CN_POINT})+"
_FIGURATIVE_NUMBER = re.compile(r"(?:万一|萬一|千[万萬](?=不要|不能|不可以|别|別|要|记得|記得|莫|得|小心))")
_NUMBER = re.compile(
    rf"(?P<ordinal>第\s*)?(?P<negative>负|負|(?<![0-9])-)?"
    rf"(?P<percent>百分之)?(?P<bound>超过|超過|超|逾|至少)?"
    rf"(?P<value>{_NUMERAL}(?:\s*[多余餘]?\s*[十百千万亿萬億])?)"
    r"(?P<tail>\s*(?:多|余|餘|以上|\+))?"
    r"(?P<measure>\s*(?:%|平方米|平方|平米|平|小时|小時|分钟|分鐘|公斤|人民币|人民幣|"
    r"元|块|塊|蚊|年|月|天|秒|倍|成|折|人|家|间|間|个|個|斤|克|亩|畝))?"
)
_AMOUNT_PARTS = re.compile(r"[0-9]+(?:\.[0-9]+)?|[十百千万亿]")
_MEASURE_EQUIVALENTS = {
    "平方米": "平", "平方": "平", "平米": "平",
    "人民币": "元", "人民幣": "元", "块": "元", "塊": "元", "蚊": "元",
    "間": "间", "個": "个", "畝": "亩", "小時": "小时", "分鐘": "分钟",
}

_CLAUSE = re.compile(r"[,，。！？!?、；;：:\n]+")
_TRIM = " \t\r\n,，.。！？!?、；;：:「」『』“”"
_NEGATION = re.compile(r"(?<![可但要还还是總总])(?:也|更|都|仲|亦)?(?:不是|并非|並非|唔[系係])")
_CORE = r"核心|本质|本質|重点|重點|关键|關鍵|机制|機制|问题|問題"
_CONTRAST = re.compile(
    rf"(?P<core>{_CORE})?\s*(?:并?不是|并非|並非|唔[系係])"
    r"(?P<rejected>.+?)(?:而[是系係]|[,，]\s*(?:其实|其實)?[是系係])(?P<accepted>.+)"
)
_UNPUNCTUATED_CONTRAST = re.compile(
    rf"(?P<core>{_CORE})?(?:并?不是|并非|並非|唔[系係])"
    r"(?P<rejected>[^。！？!?，,]+?)(?<![不唔就还還也都又更仲])[是系係](?P<accepted>.+)"
)
_TURN = re.compile(r"(?:不是|并非|唔[系係])[^。！？!?]{0,30}(?:的问题|嘅問題)|关键是|關鍵是|真相")
_AMOUNT_LABEL = re.compile(r"(?:负债|負債|欠债|欠債|欠款|亏损|虧損|亏了|虧了|"
                           r"赔了|賠了|营收|營收|成本|利润|利潤)\s*(?:了|就|有)?\s*$")
_LIST_LEAD = re.compile(r"[一二两兩三四五六七八九十]大(?:天王|招牌|特色)|主要有|包括|分为|分為")
_LIST_JOIN = re.compile(r"[,，、；;：:]|同埋|以及|还有|還有|加埋")
_STORE_TERMS = re.compile(r"探店|实体店|實體店|门店|門店|茶楼|茶樓|点心|點心|"
                          r"招牌|后厨|後廚|餐厅|餐廳|菜单|菜單|到店|开店|開店|"
                          r"桑拿[鸡雞]|[虾蝦][饺餃]|四大天王|[百千0-9]平")
_DISH = re.compile(r"桑拿[鸡雞]|[虾蝦][饺餃]|叉[烧燒]包|凤爪|鳳爪|排骨")
_CTA = re.compile(r"评论区|評論區|留言|关注|關注|点赞|點讚|收藏")


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
            "description": str(evidence.get("description") or evidence.get("summary") or clip.get("description") or ""),
            "tags": tags,
            "entities": entities,
        })
    return pool


def build_director_tasks(
    pipeline_dir: Path, style_id: str = "style_02", out_path: Optional[Path] = None,
) -> Path:
    """把锁定句表与已有素材依据交给写总谱的 Agent。"""
    pipeline_dir = Path(pipeline_dir)
    sentences, total = _timeline(read_json(pipeline_dir / _AUDIO_FILENAME))
    profile = _optional_document(pipeline_dir, _COMPOSITION_FILENAME)
    pool = _broll_pool(
        _optional_document(pipeline_dir, _INVENTORY_FILENAME),
        _optional_document(pipeline_dir, "00_visual_index.json"),
    )
    return write_json(Path(out_path) if out_path is not None else pipeline_dir / TASKS_FILENAME, {
        "step": "03b_director_tasks",
        "style_id": style_id,
        "locked_duration_sec": total,
        "sentences": [{
            "id": sentence["id"], "text": sentence.get("text", ""),
            "start_sec": sentence["start_sec"], "end_sec": sentence["end_sec"],
            "duration_sec": round(sentence["end_sec"] - sentence["start_sec"], 4),
            "keywords": _string_list(sentence.get("keywords")),
        } for sentence in sentences],
        "broll_summary": {
            "has_real_broll": bool(pool), "count": len(pool), "clips": pool,
            "tags_preview": list(dict.fromkeys(tag for clip in pool for tag in clip["tags"]))[:12],
        },
        "composition_hint": {
            "primary_stage_mode": profile.get("primary_stage_mode", "pending"),
            "allow_behind_person": _allows_behind_person(profile, sentences),
            "theme_hint": profile.get("theme_hint"),
        } if profile is not None else "pending",
        "director_primitives": list(DIRECTOR_PRIMITIVES),
        "theme_profiles": list(THEME_PROFILES),
        "camera_intents": list(_CAMERAS),
        "dramatic_roles": list(_ROLES),
        "broll_policies": list(_BROLL_POLICIES),
        "music_cue_types": list(_MUSIC_CUES),
        "rules": [
            "按锁定时间顺序为每句分配且只分配一幕，保留原句 ID。",
            "每行大字移除装饰符号及空白后最多 7 字。词语和数字必须可从该幕原话核对。",
            "高光幕占锁定时长的目标范围为 0.35..0.55。用安静的过渡句给高光留出空间。",
            "禁止把人后弹幕放在不允许人后遮挡的机位上。实拍补镜需要有对应素材依据。",
            "反转或高潮考虑 silence_drop，开篇考虑 delay_entry。音乐指令最长 3.5 秒。",
        ],
        "how_to_fill": (
            f"将 theme_profile、acts、music_cues 写入 {PLAN_FILENAME}。"
            "每幕提供 act_id、sentence_ids、dramatic_role、emotion_intensity、primitive、"
            "visual_copy（headline、items、accent_token、footnote）、camera_intent、broll_policy、sfx_budget。"
            "每条音乐指令提供 type、start_sec、end_sec、depth_db、fade_ms、reason。"
            "用 director_plan.py apply-plan --pipeline-dir <pipeline> 校验并保存。"
        ),
    })


def _allows_behind_person(profile: Optional[Dict[str, Any]], sentences: List[Dict[str, Any]]) -> bool:
    profile = profile or {}
    if profile.get("allow_behind_person") is False or profile.get("primary_stage_mode") == "closeup_chest_stage":
        return False
    profiles = profile.get("aroll_profiles")
    if not isinstance(profiles, list):
        return True
    sources = {Path(_media_name(sentence.get("source_media"))).stem for sentence in sentences}
    for item in profiles:
        if isinstance(item, dict) and item.get("allow_behind_person") is False:
            name = Path(_media_name(item.get("media_path"))).stem
            if not name or "" in sources or name in sources:
                return False
    return True


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold().replace("−", "-")


def _display_length(text: str) -> int:
    return sum(not ch.isspace() and ch not in _DISPLAY_DECOR for ch in text)


def _amount(text: str) -> Optional[Fraction]:
    text = re.sub(r"[\s,多余餘]", "", text).translate(_CN_TO_DIGITS)
    if len(text) > 40:
        return None
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


def _trace_copy(
    text: str, sources: List[str], spoken_numbers: List[tuple], spoken_latin: set[str],
) -> List[str]:
    text = _normalize(text)
    missing = [
        match[0].strip() for match in _NUMBER.finditer(text)
        if not any(_number_matches(_number_key(match), spoken) for spoken in spoken_numbers)
    ]
    missing.extend(word for word in _LATIN.findall(text) if word not in spoken_latin)
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
) -> Dict[str, Any]:
    """校验总谱及同幕溯源，按锁定时间重算高光覆盖率。"""
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
        if primitive == "clean_breath" and (budget != 0 or any(visual.values())):
            errors.append(f"{path} clean_breath 需要留空前景大字及主动音效")
        if primitive == "hero_anchor_plus_danmaku" and not _allows_behind_person(composition_profile, anchored):
            errors.append(f"behind_person_not_allowed {path} 的构图不允许人后弹幕")
        if primitive in DIRECTOR_PRIMITIVES and primitive not in _QUIET_PRIMITIVES and anchored:
            spans.append(_span(anchored))

    missing = [sid for sid in by_id if sid not in covered]
    if missing:
        errors.append(f"missing_sentence_ids 未分配锁定句：{', '.join(missing)}")
    ratio = _coverage(spans, total)
    cleaned["highlight_coverage_ratio"] = round(ratio, 4)
    if sentences and not _TARGET_LOW <= ratio <= _TARGET_HIGH:
        if not 0.25 <= ratio <= 0.70:
            warnings.append(f"highlight_coverage_ratio {ratio:.2%} 超出 25%..70% 校验带")
        else:
            warnings.append(f"highlight_coverage_ratio {ratio:.2%} 偏离 35%..55% 目标带")
    cleaned["music_cues"] = _validate_music(plan.get("music_cues", []), total, errors, warnings)
    result["sanitized_plan"] = cleaned
    result["ok"] = not errors
    return result


def _visual_copy(headline: str = "", items: tuple[str, ...] = (), accent: str = "") -> Dict[str, Any]:
    return {"headline": headline, "items": list(items), "accent_token": accent, "footnote": ""}


def _phrase(text: str, sentences: List[Dict[str, Any]], limit: int = _DISPLAY_LIMIT) -> str:
    """优先用完整短分句，长句使用确实出现在句内的关键词或原始词。"""
    candidates = _CLAUSE.split(text)
    for sentence in sentences:
        candidates.extend(_string_list(sentence.get("display_keywords")) + _string_list(sentence.get("keywords")))
        candidates.extend(
            word.get("text") or word.get("word") or ""
            for word in (sentence.get("words") or []) if isinstance(word, dict)
        )
    for phrase in candidates:
        if isinstance(phrase, str):
            phrase = phrase.strip(_TRIM)
            if (2 <= _display_length(phrase) <= limit and phrase in text
                    and (_HAN.search(phrase) or _LATIN.search(_normalize(phrase)))):
                return phrase
    return ""


def _numeric_focus(text: str) -> tuple[str, str]:
    for match in _NUMBER.finditer(text):
        value, unit, ordinal, bound = _number_key(match)
        if ordinal or not isinstance(value, Fraction):
            continue
        if not (unit or abs(value) >= 10 or bound):
            continue
        label = _AMOUNT_LABEL.search(text[:match.start()])
        number = match[0].strip()
        if label and unit in ("", "元") and value >= 10000 and value % 10000 == 0 and bound != "minimum":
            number = f"{value // 10000}万" + ("+" if bound == "over" else "")
        elif re.search(r"[多余餘][十百千万亿萬億]$", number):
            number = re.sub(r"[多余餘]", "", number) + "+"
        if _display_length(number) > _DISPLAY_LIMIT:
            continue
        if label:
            headline = label[0].strip() + number
            if _display_length(headline) <= _DISPLAY_LIMIT:
                return headline, number
        return number, number
    return "", ""


def _contrast(text: str) -> Optional[re.Match[str]]:
    return _CONTRAST.search(text) or _UNPUNCTUATED_CONTRAST.search(text)


def _group_text(sentences: List[Dict[str, Any]]) -> str:
    return "，".join(unicodedata.normalize("NFKC", s.get("text", "")).strip(_TRIM) for s in sentences)


def _group_end(sentences: List[Dict[str, Any]], index: int, store: bool) -> int:
    """给分在相邻句里的否定排比或反转留出同一幕。"""
    first = _group_text(sentences[index:index + 1])
    lead = _LIST_LEAD.search(first) if store else None
    if lead and not first[lead.end():].strip(_TRIM) and index + 1 < len(sentences):
        following = sentences[index + 1]
        if following["start_sec"] - sentences[index]["end_sec"] <= 1.2 and _DISH.search(following.get("text", "")):
            return index + 2
    if _contrast(first) or "≠" in first or not (_NEGATION.search(first) or _numeric_focus(first)[0]):
        return index + 1
    end = index + 1
    for following in sentences[end:end + 2]:
        text = following.get("text", "").lstrip()
        if following["start_sec"] - sentences[end - 1]["end_sec"] > 1.2:
            break
        if _contrast(text) or "≠" in text:
            break
        after_negation = end > index + 1 or _NEGATION.search(first)
        positive = after_negation and text.startswith(("是", "而是", "而系", "而係"))
        if not (_NEGATION.match(text) or positive):
            break
        end += 1
    joined = _group_text(sentences[index:end])
    return end if len(_NEGATION.findall(joined)) >= 2 or _contrast(joined) else index + 1


def _negation_items(text: str, sentences: List[Dict[str, Any]]) -> tuple[str, ...]:
    matches = list(_NEGATION.finditer(text))
    items = []
    contrast = _contrast(text)
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        if contrast and match.end() == contrast.start("rejected"):
            end = min(end, contrast.end("rejected"))
        predicate = re.split(r"[,，。！？!?、；;]|而[是系係]", text[match.end():end], maxsplit=1)[0]
        phrase = _phrase(match[0].lstrip("也更都仲亦") + predicate, sentences)
        if phrase:
            items.append(f"✕ {phrase}")
    return tuple(dict.fromkeys(items))


def _ordinals(text: str) -> List[re.Match[str]]:
    return [
        match for match in _NUMBER.finditer(text)
        if match["ordinal"] and (match["measure"] or "").strip() not in ("天", "年", "月", "秒", "小时", "小時")
    ]


def _framework_copy(
    text: str, ordinals: List[re.Match[str]], sentences: List[Dict[str, Any]],
) -> Dict[str, Any]:
    rows = []
    for index, match in enumerate(ordinals):
        end = ordinals[index + 1].start() if index + 1 < len(ordinals) else len(text)
        value = _amount(match["value"])
        if value is None or value < 1 or value != int(value):
            continue
        number = f"{int(value):02d}"
        lead = re.sub(r"^[条條点點步项項]\s*", "", text[match.end():end].lstrip(_TRIM))
        title = _phrase(lead, sentences, limit=6)
        rows.append((number, title))
    if not rows:
        return _visual_copy()
    number, title = rows[0]
    items = tuple(
        f"{badge} · {label}" if _display_length(badge + label) <= _DISPLAY_LIMIT and label else badge
        for badge, label in rows[1:]
    )
    return _visual_copy(title, items, number)


def _propose(
    text: str, sentences: List[Dict[str, Any]], store: bool, opening: bool, behind: bool,
) -> tuple[str, Dict[str, Any]]:
    contrast = _contrast(text)
    if "≠" in text or (contrast and contrast["core"]):
        if "≠" in text:
            left, right = text.split("≠", 1)
            subject = ""
        else:
            left, right = contrast["rejected"], contrast["accepted"]
            subject = _phrase(text[:contrast.start()].rstrip("的嘅 "), sentences)
        rejected, accepted = _phrase(left, sentences), _phrase(right, sentences)
        items = tuple(f"{symbol} {word}" for symbol, word in (("✕", rejected), ("✓", accepted)) if word)
        return "concept_split_card", _visual_copy(subject or accepted or rejected, items, accepted)

    negations = _negation_items(text, sentences)
    if len(_NEGATION.findall(text)) >= 2 or contrast:
        headline, accent = _numeric_focus(text)
        if not headline:
            headline = _phrase(contrast["accepted"], sentences) if contrast else (negations[0][2:] if negations else "")
        return "negation_strike", _visual_copy(headline, negations, accent)

    ordinals = _ordinals(text)
    if ordinals:
        return "framework_pillar", _framework_copy(text, ordinals, sentences)

    if store:
        lead = _LIST_LEAD.search(text)
        if lead:
            tail = re.split(r"[。！？!?]", text[lead.end():], maxsplit=1)[0]
            items = []
            for part in _LIST_JOIN.split(tail):
                dishes = _DISH.findall(part)
                if len(dishes) > 1:
                    items.extend(dishes)
                elif word := _phrase(part, sentences):
                    items.append(word)
            items = list(dict.fromkeys(items))
            if len(items) >= 2:
                return "list_build_gather_dim_overlay", _visual_copy(lead[0], tuple(items[:4]))
        headline, accent = _numeric_focus(text)
        if headline:
            return "num_anchor_dim_overlay", _visual_copy(headline, accent=accent)
        if dish := _DISH.search(text):
            return "entity_stack_plus_top", _visual_copy(dish[0])
    if call := _CTA.search(text):
        return "inline_emphasis_cta", _visual_copy(call[0])
    turn = _TURN.search(text)
    title = _phrase(text[turn.end():] if turn else text, sentences)
    if not title and store and opening and (cue := _STORE_TERMS.search(text)):
        title = cue[0]
    if title:
        if opening:
            primitive = "hero_anchor_plus_danmaku" if store and behind else "hero_intro_pop"
            return primitive, _visual_copy(title)
        return "entity_stack_plus_top", _visual_copy(title)
    return "plain_bridge", _visual_copy()


_Candidate = tuple[List[Dict[str, Any]], str, Dict[str, Any]]


def _choose_highlights(candidates: List[_Candidate], total: float) -> set[int]:
    if not total:
        return set()
    priorities = {
        "negation_strike": 110, "concept_split_card": 105, "framework_pillar": 58,
        "list_build_gather_dim_overlay": 100, "num_anchor_dim_overlay": 90,
        "hero_anchor_plus_danmaku": 45, "hero_intro_pop": 20, "inline_emphasis_cta": 24,
    }

    # 每 0.1% 保留一组候选，比较时使用实测秒数，减少选镜次序对覆盖率的影响。
    def rank(state: tuple) -> tuple:
        seconds, _, importance, selected = state
        ratio = seconds / total
        return (max(_TARGET_LOW - ratio, ratio - _TARGET_HIGH, 0.0),
                -importance, abs(ratio - 0.45), len(selected))

    choices: Dict[int, tuple] = {0: (0.0, 0.0, 0, ())}
    seen: Dict[str, int] = {}
    for index, (sentences, primitive, visual) in enumerate(candidates):
        if primitive in _QUIET_PRIMITIVES or not (visual["headline"] or visual["items"] or visual["accent_token"]):
            continue
        start, end = _span(sentences)
        priority = priorities.get(primitive, 0)
        if priority:
            repetitions = seen.get(primitive, 0)
            seen[primitive] = repetitions + 1
            priority //= min(3, repetitions + 1)
        elif _DISH.fullmatch(visual["headline"]):
            priority = 35
        for seconds, previous_end, importance, selected in list(choices.values()):
            added = max(0.0, end - max(start, previous_end))
            state = (seconds + added, max(previous_end, end), importance + priority, selected + (index,))
            bucket = round(state[0] / total * 1000)
            if bucket not in choices or rank(state) < rank(choices[bucket]):
                choices[bucket] = state
    return set(min(choices.values(), key=rank)[3])


def _music_cues(candidates: List[_Candidate], total: float, store: bool) -> List[Dict[str, Any]]:
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
        if not (_TURN.search(text) or _contrast(text) or "≠" in text):
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
    return cues


def generate_deterministic_director_plan(
    audio_events: Dict[str, Any], style_id: str = "style_02",
    inventory: Optional[Dict[str, Any]] = None,
    composition_profile: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """用锁定原话、构图画像和实拍素材离线编排总谱。"""
    sentences, total = _timeline(audio_events)
    pool = _broll_pool(inventory)
    store_cues = set(_STORE_TERMS.findall("，".join(s.get("text", "") for s in sentences)))
    theme_hint = (composition_profile or {}).get("theme_hint")
    store = (
        len(store_cues) >= 3
        or bool(store_cues and (len(pool) >= 2 or theme_hint == "store_vibrant"))
        or bool(len(store_cues) >= 2 and pool)
    )

    candidates: List[_Candidate] = []
    index = 0
    while index < len(sentences):
        end = _group_end(sentences, index, store)
        group = sentences[index:end]
        text = _group_text(group)
        primitive, visual = _propose(text, group, store, not index, _allows_behind_person(composition_profile, group))
        if visual["headline"] or visual["items"] or visual["accent_token"]:
            # 摘词候选仍需按同一套溯源规则过一遍，避免数字在截取时失掉单位或词义。
            copy_errors: List[str] = []
            visual = _validate_copy(visual, "visual_copy", group, copy_errors)
            if copy_errors:
                primitive, visual = "plain_bridge", _visual_copy()
        candidates.append((group, primitive, visual))
        index = end

    highlights = _choose_highlights(candidates, total)
    roles = {
        "negation_strike": "twist", "concept_split_card": "twist", "framework_pillar": "framework",
        "list_build_gather_dim_overlay": "proof", "num_anchor_dim_overlay": "proof", "inline_emphasis_cta": "cta",
    }
    acts = []
    had_breath = False
    for index, (group, primitive, visual) in enumerate(candidates):
        highlight = index in highlights
        if highlight:
            role = "hook" if index == 0 else roles.get(primitive, "proof")
            intensity = 0.92 if primitive in ("negation_strike", "concept_split_card") else 0.73
            camera = "side_shift_card" if primitive == "concept_split_card" else "slow_push_115"
            budget = min(3, max(1, len(visual["items"]))) if primitive == "negation_strike" else 1
        else:
            breath = index - 1 in highlights or not had_breath
            had_breath = had_breath or breath
            primitive = "clean_breath" if breath else "plain_bridge"
            visual, role, budget = _visual_copy(), "setup", 0
            intensity = 0.18 if breath else 0.31
            camera = "slow_push_115" if breath else "steady_hold"
        allows_broll = store and bool(pool) and role != "hook" and primitive not in (
            "clean_breath", "hero_anchor_plus_danmaku", "negation_strike", "concept_split_card",
        )
        acts.append({
            "act_id": f"act_{index + 1:02d}",
            "sentence_ids": [sentence["id"] for sentence in group],
            "dramatic_role": role,
            "emotion_intensity": intensity,
            "primitive": primitive,
            "visual_copy": visual,
            "camera_intent": camera,
            "broll_policy": "allow_verified_broll" if allows_broll else "lock_aroll",
            "sfx_budget": budget,
        })
    result = validate_director_plan({
        "step": "03b_director_plan", "source": "fallback_deterministic", "style_id": style_id,
        "theme_profile": "store_vibrant" if store else "business_editorial",
        "acts": acts, "music_cues": _music_cues(candidates, total, store),
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
        result = validate_director_plan(plan, audio, profile)
        if result["ok"]:
            return result["sanitized_plan"]
        print(f"[director_plan] [WARN] {PLAN_FILENAME} 校验失败（{'；'.join(result['errors'])}）", file=sys.stderr)
    if generate_fallback:
        try:
            return generate_deterministic_director_plan(
                audio, style_id, _optional_document(pipeline_dir, _INVENTORY_FILENAME), profile,
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
        output = args.out if args.out is not None else pipeline_dir / PLAN_FILENAME
        if args.command == "apply-plan":
            plan = read_json(args.plan if args.plan is not None else pipeline_dir / PLAN_FILENAME)
        else:
            plan = generate_deterministic_director_plan(
                audio, args.style_id, _optional_document(pipeline_dir, _INVENTORY_FILENAME), profile,
            )
        result = validate_director_plan(plan, audio, profile)
        if not result["ok"]:
            raise ValueError("\n".join(result["errors"]))
        for warning in result["warnings"]:
            print(f"[director_plan] [WARN] {warning}", file=sys.stderr)
        print(write_json(output, result["sanitized_plan"]))
    except (OSError, ValueError) as exc:
        parser.exit(1, f"[director_plan] {exc}\n")


if __name__ == "__main__":
    main()
