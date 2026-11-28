#!/usr/bin/env python3
"""
[V12 独立黑盒端到端验证套件 · dev_tools/v12_e2e_suite.py]

面向 R1-R6 需求与 F1-F20 功能清单构建的无侵入黑盒 E2E 测试套件（共 71 项检测）：
  - Tier 1 (30 项): 组件契约与黑盒语义/视觉/封面/EDL/合规检测器（每组 >=5）
  - Tier 2 (28 项): 边界条件、正负对照校准与 CLI 子进程集成（每组 >=5）
  - Tier 3 ( 7 项): 跨模块两两交互组合场景
  - Tier 4 ( 6 项): 48 条双赛道、第 3 条盲测、R6 安全清理与全量回归门禁

运行方式：
  "$HOME/.short-video-studio/venv/bin/python" dev_tools/v12_e2e_suite.py --mode smoke --tier all
  "$HOME/.short-video-studio/venv/bin/python" dev_tools/v12_e2e_suite.py --mode pipeline --tier all
  "$HOME/.short-video-studio/venv/bin/python" dev_tools/v12_e2e_suite.py --mode full --tier all --blind-dir <path>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
import wave
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
from unittest import mock

import av
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from scipy import ndimage

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = REPO_ROOT / "short-video-studio" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import director_plan  # noqa: E402
import independent_verifier  # noqa: E402
import motion_html_renderer  # noqa: E402
import qa_inspector  # noqa: E402
import step5_subtitles  # noqa: E402
import style_visual_truth  # noqa: E402
import vision_inspector  # noqa: E402

# ---------------------------------------------------------------------------
# 常量与动态路径/隐私规则（零个人家目录字面量、零草稿名称字面量）
# ---------------------------------------------------------------------------
PRIVACY_PATH_RE = re.compile(r"/(?:Users|home)/[A-Za-z0-9._-]+|C:\\Users\\[A-Za-z0-9._-]+")
TEXT_SCAN_EXTS = {
    ".py", ".md", ".json", ".cube", ".mjs", ".js", ".html",
    ".command", ".bat", ".sh", ".ps1", ".txt",
}
EXPECTED_BRANCH = "feat/v7-audio-first-polish"
CANVAS_W, CANVAS_H = 1080, 1920
SAFE_X_MAX = 880

# 动态拼接需废除的旧标识符，避免检测脚本自身命中扫描规则
ABOLISHED_G1_SYMBOLS = (
    "BIZ_" + "SEMANTIC_SUBTITLES",
    "STO_" + "SEMANTIC_SUBTITLES",
)
ABOLISHED_G2_SYMBOLS = (
    "BIZ_" + "SEMANTIC_BLOCKS",
    "STO_" + "SEMANTIC_BLOCKS",
    "_style_" + "cover_copy",
)
GENERIC_FRAME8_MARKERS = ("夫妻店到雇人断层", "夫妻店到雇人", "01/02/03")

DANGLING_HARD_PARTICLES = frozenset("的嘅地得之把被从向给和与在喺")
DANGLING_ASPECT_PARTICLES = frozenset("了咗着过")
AUX_HEAD_CHARS = frozenset("的地得了着过嘅啲")
SHORT_VALID_EXCLAMATIONS = frozenset({
    "好", "对", "是", "不", "行", "懂", "看", "来", "听", "走", "稳", "准",
    "第一", "第二", "第三", "第四", "首先", "其次", "最后", "所以", "但是",
    "然后", "而且", "其实", "本质", "关键", "核心", "注意", "记住", "千万",
    "没错", "对了", "好吧", "真的", "假的", "为什么",
})

GBRO_STYLE_IDS = [f"style-{i:02d}" for i in range(1, 11)]
BENCHMARK_12_STYLES = [
    "style_02", "style_03", "style_04", "style_06", "style_07", "style_09",
    "style_10", "style_12", "style_15", "style_16", "style_21", "style_24",
]
ALL_24_STYLES = [f"style_{i:02d}" for i in range(1, 25)]


@dataclass
class CheckResult:
    id: str
    tier: int
    group: str
    name: str
    status: str  # PASS | FAIL
    measured: str
    threshold: str
    detail: str


@dataclass
class CheckSpec:
    id: str
    tier: int
    group: str
    name: str
    fn: Callable[["SuiteContext"], CheckResult]


class SuiteContext:
    def __init__(
        self,
        mode: str,
        biz_root: Path,
        sto_root: Path,
        blind_dir: Optional[Path],
    ) -> None:
        self.mode = mode
        self.biz_root = biz_root
        self.sto_root = sto_root
        self.blind_dir = blind_dir
        self.py_bin = sys.executable
        self._font_path = self._find_font()

    @staticmethod
    def _find_font() -> Path:
        fonts_dir = REPO_ROOT / "short-video-studio" / "assets" / "fonts"
        for name in ("AlimamaShuHeiTi-Bold.otf", "SourceHanSerifCN-Heavy.otf", "SourceHanSerifCN-Bold.otf"):
            p = fonts_dir / name
            if p.is_file():
                return p
        cands = sorted(fonts_dir.glob("*.otf")) + sorted(fonts_dir.glob("*.ttf"))
        if not cands:
            raise FileNotFoundError(f"未找到可用开源字体目录: {fonts_dir}")
        return cands[0]

    def font(self, size: int) -> ImageFont.FreeTypeFont:
        return ImageFont.truetype(str(self._font_path), size=size)


# ---------------------------------------------------------------------------
# 通用辅助：文本规范化、色彩 WCAG、PyAV 帧解码/编码、dhash、JSON 读写
# ---------------------------------------------------------------------------
def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _clean_cjk(text: str) -> str:
    return re.sub(r"[\s，。！？、；：“”\"'（）()【】《》,.!?;:\-_/]+", "", str(text or ""))


def _srgb_lin(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def hex_to_rgb(hx: str) -> Tuple[int, int, int]:
    s = str(hx or "#000000").strip().lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    if len(s) < 6:
        return (0, 0, 0)
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))


def rel_luminance(rgb: Sequence[float]) -> float:
    r, g, b = (max(0.0, min(1.0, float(v) / 255.0)) for v in rgb[:3])
    return 0.2126 * _srgb_lin(r) + 0.7152 * _srgb_lin(g) + 0.0722 * _srgb_lin(b)


def wcag_ratio(l1: float, l2: float) -> float:
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


def bbox_iou(a: Dict[str, float], b: Dict[str, float]) -> float:
    ax1, ay1 = float(a.get("x", 0.0)), float(a.get("y", 0.0))
    ax2, ay2 = ax1 + float(a.get("w", 0.0)), ay1 + float(a.get("h", 0.0))
    bx1, by1 = float(b.get("x", 0.0)), float(b.get("y", 0.0))
    bx2, by2 = bx1 + float(b.get("w", 0.0)), by1 + float(b.get("h", 0.0))
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0.0:
        return 0.0
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union > 1e-9 else 0.0


def image_dhash(path: Path) -> int:
    with Image.open(path) as im:
        gray = im.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
        arr = np.asarray(gray, dtype=np.int16)
    diff = arr[:, 1:] > arr[:, :-1]
    h = 0
    for bit in diff.flatten():
        h = (h << 1) | int(bool(bit))
    return h


def hamming_distance(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def cluster_dhashes(hashes: Dict[str, int], *, max_dist: int = 6) -> List[List[str]]:
    """对感知哈希进行贪心汉明距离聚类（同一簇内与代表帧汉明距离 <= max_dist）。"""
    clusters: List[Tuple[int, List[str]]] = []
    for sid, h in sorted(hashes.items()):
        placed = False
        for rep_h, members in clusters:
            if hamming_distance(h, rep_h) <= max_dist:
                members.append(sid)
                placed = True
                break
        if not placed:
            clusters.append((h, [sid]))
    return [members for _, members in clusters]


def write_synthetic_mp4(mp4_path: Path, frames_rgb: List[np.ndarray], fps: int = 30) -> None:
    mp4_path.parent.mkdir(parents=True, exist_ok=True)
    h, w = frames_rgb[0].shape[:2]
    with av.open(str(mp4_path), mode="w") as container:
        stream = container.add_stream("libx264", rate=fps)
        stream.width = w
        stream.height = h
        stream.pix_fmt = "yuv420p"
        stream.options = {"preset": "ultrafast", "crf": "20"}
        for arr in frames_rgb:
            frame = av.VideoFrame.from_ndarray(np.ascontiguousarray(arr, dtype=np.uint8), format="rgb24")
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)


def decode_mp4_frames_at_times(mp4_path: Path, times_sec: Sequence[float]) -> List[Tuple[float, np.ndarray]]:
    """使用 PyAV 在精确时间点解码 RGB24 帧（多线程 + 按需转换）。"""
    targets = sorted(max(0.0, float(t)) for t in times_sec)
    if not targets:
        return []
    out: List[Tuple[float, np.ndarray]] = []
    with av.open(str(mp4_path)) as container:
        vstream = container.streams.video[0]
        vstream.thread_type = "AUTO"
        tb = float(vstream.time_base) if vstream.time_base else (1.0 / 30.0)
        idx = 0
        last_frame: Optional[av.VideoFrame] = None
        last_t = 0.0
        for frame in container.decode(video=0):
            ft = float(frame.pts * tb) if frame.pts is not None else last_t
            last_frame = frame
            last_t = ft
            if idx < len(targets) and ft + 0.017 >= targets[idx]:
                arr = frame.to_ndarray(format="rgb24")
                while idx < len(targets) and ft + 0.017 >= targets[idx]:
                    out.append((targets[idx], arr.copy()))
                    idx += 1
            if idx >= len(targets):
                break
        if idx < len(targets) and last_frame is not None:
            arr = last_frame.to_ndarray(format="rgb24")
            while idx < len(targets):
                out.append((targets[idx], arr.copy()))
                idx += 1
    return out


def probe_mp4_duration(mp4_path: Path) -> float:
    with av.open(str(mp4_path)) as container:
        vstream = container.streams.video[0]
        if container.duration is not None:
            return float(container.duration) / float(av.time_base)
        if vstream.duration is not None and vstream.time_base is not None:
            return float(vstream.duration * vstream.time_base)
    return 0.0


def _write_wav_tone(path: Path, seconds: float = 2.0, sr: int = 16000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = int(seconds * sr)
    t = np.arange(n, dtype=np.float32) / float(sr)
    sig = (0.15 * 32767.0 * np.sin(2.0 * math.pi * 220.0 * t)).astype(np.int16)
    with wave.open(str(path), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(sig.tobytes())
    return path


# ---------------------------------------------------------------------------
# G1 核心黑盒检测器：通用语义断句 & style_02 舞台
# ---------------------------------------------------------------------------
def detect_dangling_particles(
    chunks: Sequence[str],
    *,
    is_sentence_final: Optional[Sequence[bool]] = None,
    require_terminal_punct_on_final: bool = False,
) -> List[str]:
    issues: List[str] = []
    for idx, raw in enumerate(chunks):
        s = str(raw or "").strip()
        stripped = s.rstrip("，。！？、；：“”\"'…,.!?;: ")
        if not stripped:
            continue
        last_ch = stripped[-1]
        has_terminal_punct = len(s) > len(stripped) and any(p in s[len(stripped):] for p in "。！？!?")
        final_flag = bool(is_sentence_final[idx]) if is_sentence_final is not None else (idx == len(chunks) - 1)
        if last_ch in DANGLING_HARD_PARTICLES:
            issues.append(f"trailing_particle({idx}): '{s}'")
        elif last_ch in DANGLING_ASPECT_PARTICLES and (
            not final_flag or (require_terminal_punct_on_final and not has_terminal_punct)
        ):
            # 非句末切分，或显式要求句终符却缺失时，拦截 了/咗/着/过 挂尾
            issues.append(f"trailing_aspect({idx}): '{s}'")
    return issues


def _group_subtitle_rows_by_sentence(
    entries: Sequence[Dict[str, Any]],
) -> Tuple[List[str], List[bool], List[List[str]]]:
    rows = [
        e for e in entries
        if str(e.get("text") or "").strip()
        and (not e.get("suppressed") or e.get("suppress_reason") == "kinetic_typography_stage")
    ]
    groups: List[List[str]] = []
    last_sid: str = ""
    for idx, e in enumerate(rows):
        raw_sid = e.get("sentence_id")
        sid = str(raw_sid).strip() if raw_sid is not None else ""
        txt = str(e.get("text") or "")
        if idx == 0 or not sid or not last_sid or sid != last_sid:
            groups.append([txt])
        else:
            groups[-1].append(txt)
        last_sid = sid
    texts_flat: List[str] = []
    is_final_flat: List[bool] = []
    for grp in groups:
        for gi, txt in enumerate(grp):
            texts_flat.append(txt)
            is_final_flat.append(gi == len(grp) - 1)
    return texts_flat, is_final_flat, groups


def detect_aux_head_and_fragments(
    chunks: Sequence[str],
    *,
    single_sentence_mode: bool = False,
) -> List[str]:
    issues: List[str] = []
    for idx, raw in enumerate(chunks):
        s = _clean_cjk(str(raw or ""))
        if not s:
            continue
        if s[0] in AUX_HEAD_CHARS:
            issues.append(f"aux_head({idx}): '{raw}'")
        if not single_sentence_mode and len(chunks) > 1 and len(s) <= 2 and s not in SHORT_VALID_EXCLAMATIONS:
            # 数字/纯字母短词不判为碎词；中文<=2字碎片（如 '企业'/'厨房'/'花咗'）且非感叹词判为残句
            if not re.fullmatch(r"[A-Za-z0-9+%]+", s):
                issues.append(f"short_fragment({idx}): '{raw}'")
    return issues


def detect_split_numbers_compounds(chunks: Sequence[str]) -> List[str]:
    issues: List[str] = []
    num_tail = re.compile(r"[0-9零一二两三四五六七八九十百千万亿]+$")
    unit_head = re.compile(r"^[多几余万亿千百十平块元个次家店天年岁月日%分之]")
    frac_tail = re.compile(r"[0-9一二两三四五六七八九十百千]+分$")
    frac_head = re.compile(r"^之[0-9一二两三四五六七八九十百千]+")
    for idx in range(len(chunks) - 1):
        a = _clean_cjk(chunks[idx])
        b = _clean_cjk(chunks[idx + 1])
        if not a or not b:
            continue
        if (num_tail.search(a) and unit_head.search(b)) or (frac_tail.search(a) and frac_head.search(b)):
            issues.append(f"split_number({idx}->{idx+1}): '{a}' | '{b}'")
    return issues


def analyze_style02_kinetic_items(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    fg_items = [it for it in items if str(it.get("z_layer") or "fg") != "bg"]
    same_y_overlaps: List[str] = []
    prefix_dups: List[str] = []
    events: List[Tuple[float, int]] = []
    for it in fg_items:
        st = float(it.get("start_sec") or 0.0)
        en = float(it.get("end_sec") or 0.0)
        if en > st + 0.01:
            events.append((st, 1))
            events.append((en, -1))
    events.sort(key=lambda x: (x[0], x[1]))
    cur = 0
    max_rows = 0
    for _, delta in events:
        cur += delta
        max_rows = max(max_rows, cur)

    for i in range(len(fg_items)):
        a = fg_items[i]
        ta = str(a.get("text_or_asset") or "").strip()
        sa, ea = float(a.get("start_sec") or 0.0), float(a.get("end_sec") or 0.0)
        ya = float(a.get("y_norm") or 0.0)
        for j in range(i + 1, len(fg_items)):
            b = fg_items[j]
            tb = str(b.get("text_or_asset") or "").strip()
            sb, eb = float(b.get("start_sec") or 0.0), float(b.get("end_sec") or 0.0)
            yb = float(b.get("y_norm") or 0.0)
            ov = min(ea, eb) - max(sa, sb)
            if ov > 0.05 and abs(ya - yb) < 0.05:
                same_y_overlaps.append(f"{a.get('id')}('{ta}') vs {b.get('id')}('{tb}') @ y={ya:.3f} ov={ov:.2f}s")
            # 同一/相邻时间窗的尾部复读（如 '先答清楚你自己的' -> '自己的生意' 或 '我哋嘅四大天王虾饺' -> '虾饺'）
            if abs(sa - sb) <= 1.5 and abs(ya - yb) < 0.08:
                ca, cb = _clean_cjk(ta), _clean_cjk(tb)
                if len(ca) >= 2 and len(cb) >= 2 and (ca.endswith(cb) or cb.startswith(ca[-2:])):
                    if ca != cb:
                        prefix_dups.append(f"dup('{ta}' -> '{tb}')")

    texts = [str(it.get("text_or_asset") or "") for it in items]
    # 依据 group_id 判断是否为 group 内最后一项
    last_in_group: List[bool] = []
    for idx, it in enumerate(items):
        gid = it.get("group_id")
        is_last = idx == len(items) - 1 or items[idx + 1].get("group_id") != gid
        last_in_group.append(bool(is_last))
    dangling = detect_dangling_particles(texts, is_sentence_final=last_in_group)
    aux_frag = detect_aux_head_and_fragments(texts, single_sentence_mode=False)
    split_nums = detect_split_numbers_compounds(texts)
    ok = (
        not same_y_overlaps
        and max_rows <= 3
        and not prefix_dups
        and not dangling
        and not aux_frag
        and not split_nums
    )
    return {
        "ok": ok,
        "item_count": len(items),
        "max_simultaneous_rows": max_rows,
        "same_y_overlaps": same_y_overlaps,
        "prefix_dups": prefix_dups,
        "dangling_particles": dangling,
        "aux_and_fragments": aux_frag,
        "split_numbers": split_nums,
    }


def check_abolition_symbols_in_files(files: Sequence[Path], forbidden_symbols: Sequence[str]) -> List[str]:
    hits: List[str] = []
    for fp in files:
        if not fp.is_file() or fp.name == Path(__file__).name:
            continue
        txt = fp.read_text(encoding="utf-8", errors="ignore")
        for sym in forbidden_symbols:
            if re.search(rf"\b{re.escape(sym)}\b", txt):
                hits.append(f"{fp.name}: {sym}")
    return hits


# ---------------------------------------------------------------------------
# G2 核心黑盒检测器：导演规划协议 & gbro-cover-design 10 风格多图封面
# ---------------------------------------------------------------------------
def detect_frame8_generic_black_card_in_plan(plan: Dict[str, Any]) -> List[str]:
    hits: List[str] = []
    for idx, beat in enumerate(plan.get("style_scene_beats") or []):
        blob = json.dumps(beat, ensure_ascii=False)
        for marker in GENERIC_FRAME8_MARKERS:
            if marker in blob:
                hits.append(f"beat[{idx}] contains '{marker}'")
    for idx, callout in enumerate(plan.get("style_callouts") or []):
        blob = json.dumps(callout, ensure_ascii=False)
        for marker in GENERIC_FRAME8_MARKERS:
            if marker in blob:
                hits.append(f"callout[{idx}] contains '{marker}'")
    return hits


def validate_director_plan_universal(
    plan: Dict[str, Any],
    audio_events: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    errors: List[str] = []
    required_keys = ("acts", "music_cues", "cover_copy", "semantic_subtitles", "style_scene_beats", "style_callouts")
    for k in required_keys:
        if k not in plan:
            errors.append(f"missing_key:{k}")
    if not isinstance(plan.get("acts"), list) or not plan["acts"]:
        errors.append("empty_acts")
    cc = plan.get("cover_copy") or {}
    if not isinstance(cc, dict) or not str(cc.get("headline_top") or "").strip():
        errors.append("missing_cover_copy.headline_top")
    if not isinstance(plan.get("semantic_subtitles"), list) or not plan["semantic_subtitles"]:
        errors.append("empty_semantic_subtitles")
    if audio_events is not None:
        res = director_plan.validate_director_plan(plan, audio_events)
        if not res.get("ok"):
            errors.extend(res.get("errors") or ["director_plan.validate_director_plan_failed"])
    frame8_hits = detect_frame8_generic_black_card_in_plan(plan)
    errors.extend(frame8_hits)
    # 校验 style_callouts 与 style_scene_beats 时间窗不碰撞
    beats = plan.get("style_scene_beats") or []
    beat_wins: List[Tuple[float, float]] = []
    for b in beats:
        t0 = float(b.get("target_sec") or 0.0)
        dur = float(b.get("target_dur") or b.get("max_dur") or 3.0)
        beat_wins.append((t0, t0 + dur))
    for idx, c in enumerate(plan.get("style_callouts") or []):
        cs, ce = float(c.get("start_sec") or 0.0), float(c.get("end_sec") or 0.0)
        for bs, be in beat_wins:
            if min(ce, be) - max(cs, bs) > 0.25:
                errors.append(f"callout[{idx}] overlaps beat [{bs:.2f},{be:.2f}]")
    return {"ok": len(errors) == 0, "errors": errors}


def validate_gbro_prompt(
    prompt_text: str,
    gbro_style_id: str,
    *,
    has_fig1: bool,
    fig2_refs: Sequence[str],
) -> Dict[str, Any]:
    errors: List[str] = []
    lines = [ln.strip() for ln in str(prompt_text or "").splitlines() if ln.strip()]
    if not lines or not lines[0].startswith("3:4 竖版构图"):
        errors.append("line1_must_start_with_3_4_vertical")
    if not any(tok in prompt_text for tok in ("10%", "百分之十", "安全区")):
        errors.append("missing_10pct_safe_margin")
    if not re.search(r"[「“\"][^」”\"\n]{2,}[」”\"]", prompt_text):
        errors.append("missing_quoted_chinese_title")
    for banned in ("全息投影", "粒子效果", "发光特效"):
        if banned in prompt_text:
            errors.append(f"forbidden_effect:{banned}")
    if gbro_style_id not in GBRO_STYLE_IDS:
        errors.append(f"invalid_gbro_style:{gbro_style_id}")
    if has_fig1 and "图1" not in prompt_text:
        errors.append("missing_fig1_reference")
    if gbro_style_id == "style-03":
        if not fig2_refs or "图2" not in prompt_text:
            errors.append("style_03_requires_mandatory_fig2")
    if gbro_style_id == "style-08":
        if not any(tok in prompt_text for tok in ("背影", "背对镜头")):
            errors.append("style_08_requires_back_view")
        if any(tok in prompt_text for tok in ("正脸直视", "五官清晰正面")):
            errors.append("style_08_forbids_front_face")
    return {"ok": len(errors) == 0, "errors": errors}


def validate_gbro_handoff_and_covers(
    handoff: Dict[str, Any],
    cover_3x4: Path,
    cover_full: Path,
    *,
    require_nonempty_fig2: bool = True,
) -> Dict[str, Any]:
    errors: List[str] = []
    gstyle = str(handoff.get("recommended_style_id") or "")
    if gstyle not in GBRO_STYLE_IDS:
        errors.append(f"bad_style:{gstyle}")
    if str(handoff.get("aspect_ratio") or "") != "3:4":
        errors.append(f"bad_aspect_ratio:{handoff.get('aspect_ratio')!r}")
    fig1 = handoff.get("figure_1_face_ref") or {}
    if not isinstance(fig1, dict) or not str(fig1.get("path") or "").strip():
        errors.append("missing_figure_1_face_ref")
    fig2 = handoff.get("figure_2_content_refs") or []
    if require_nonempty_fig2 and (not isinstance(fig2, list) or len(fig2) == 0):
        errors.append("empty_figure_2_content_refs")
    p_res = validate_gbro_prompt(
        str(handoff.get("prompt_text") or ""),
        gstyle,
        has_fig1=bool(fig1),
        fig2_refs=[str(x.get("path") if isinstance(x, dict) else x) for x in fig2],
    )
    if not p_res["ok"]:
        errors.extend(p_res["errors"])

    if not cover_3x4.is_file():
        errors.append("missing_09_cover_3x4.jpg")
    else:
        with Image.open(cover_3x4) as im34:
            if im34.size != (1080, 1440):
                errors.append(f"cover_3x4_size:{im34.size}")
    if not cover_full.is_file():
        errors.append("missing_09_cover.jpg")
    else:
        with Image.open(cover_full) as imf:
            if imf.size != (1080, 1920):
                errors.append(f"cover_full_size:{imf.size}")
    return {"ok": len(errors) == 0, "errors": errors}


# ---------------------------------------------------------------------------
# G3 / G4 核心黑盒视觉 & 信号检测器（PyAV + NumPy + SciPy + Vision）
# ---------------------------------------------------------------------------
def analyze_ktv_orbs_pure_bg(
    frames_with_t: List[Tuple[float, np.ndarray]],
    stage_html_text: Optional[str] = None,
) -> Dict[str, Any]:
    """仅适用于纯 HTML 面板/合成纯背景帧的 KTV 光球 DOM + 色度/R-B 漂移检测（不直接用于含实拍人像/B-roll 的完整成片）。"""
    dom_hits: List[str] = []
    if stage_html_text is not None:
        for pat in ("amb-orb-1", "amb-orb-2", "amb-sweep"):
            if pat in stage_html_text:
                dom_hits.append(pat)

    chroma_p95_max = 0.0
    chroma_abs_max = 0.0
    temporal_rb_p95_max = 0.0
    for i in range(len(frames_with_t) - 1):
        a = frames_with_t[i][1].astype(np.float32)
        b = frames_with_t[i + 1][1].astype(np.float32)
        luma_a = 0.2126 * a[:, :, 0] + 0.7152 * a[:, :, 1] + 0.0722 * a[:, :, 2]
        luma_b = 0.2126 * b[:, :, 0] + 0.7152 * b[:, :, 1] + 0.0722 * b[:, :, 2]
        roi_slice = slice(360, 1400)
        sub_a = a[roi_slice]
        sub_b = b[roi_slice]
        la, lb = luma_a[roi_slice], luma_b[roi_slice]
        bg_mask = ((la < 120.0) & (lb < 120.0)) | ((sub_a.min(axis=2) > 190.0) & (sub_b.min(axis=2) > 190.0))
        if int(bg_mask.sum()) >= 500:
            ch_a = sub_a.max(axis=2) - sub_a.min(axis=2)
            ch_b = sub_b.max(axis=2) - sub_b.min(axis=2)
            ch_vals = np.concatenate([ch_a[bg_mask], ch_b[bg_mask]])
            chroma_p95_max = max(chroma_p95_max, float(np.percentile(ch_vals, 95)))
            chroma_abs_max = max(chroma_abs_max, float(np.max(ch_vals)))
            rb_diff = np.abs((sub_b[:, :, 0] - sub_b[:, :, 2]) - (sub_a[:, :, 0] - sub_a[:, :, 2]))
            temporal_rb_p95_max = max(temporal_rb_p95_max, float(np.percentile(rb_diff[bg_mask], 95)))

    orbs_ok = (
        not dom_hits
        and chroma_p95_max <= 16.0
        and chroma_abs_max <= 32.0
        and temporal_rb_p95_max <= 12.0
    )
    return {
        "orbs_ok": orbs_ok,
        "dom_hits": dom_hits,
        "chroma_p95": round(chroma_p95_max, 2),
        "chroma_max": round(chroma_abs_max, 2),
        "temporal_rb_p95": round(temporal_rb_p95_max, 2),
    }


def analyze_mp4_visual_freeze(frames_with_t: List[Tuple[float, np.ndarray]]) -> Dict[str, Any]:
    """全片 bb_no_visual_freeze 冻帧检测（270x480 掩掉顶栏与字幕带，支持任意全片时长采样）。"""
    thumbs: List[Tuple[float, np.ndarray]] = []
    for t, arr in frames_with_t:
        sm = np.asarray(Image.fromarray(arr).resize((270, 480), Image.Resampling.BILINEAR), dtype=np.float32)
        sm[46:88, :, :] = 0.0
        sm[285:380, :, :] = 0.0
        thumbs.append((t, sm))

    cur_static = 0.0
    max_static = 0.0
    step_metrics: List[Dict[str, float]] = []
    for i in range(len(thumbs) - 1):
        t0, s0 = thumbs[i]
        t1, s1 = thumbs[i + 1]
        dt = max(0.0, t1 - t0)
        diff = np.abs(s1 - s0)
        mad = float(np.mean(diff))
        p95 = float(np.percentile(diff, 95))
        step_metrics.append({"t0": t0, "t1": t1, "mad": round(mad, 3), "p95": round(p95, 3)})
        if mad < 1.2 and p95 < 5.0:
            cur_static += dt
            max_static = max(max_static, cur_static)
        else:
            cur_static = 0.0

    freeze_ok = (len(thumbs) < 2) or (max_static <= 3.0)
    return {
        "freeze_ok": freeze_ok,
        "max_static_sec": round(max_static, 3),
        "step_metrics": step_metrics,
    }


def analyze_ktv_orbs_and_freeze(
    frames_with_t: List[Tuple[float, np.ndarray]],
    stage_html_text: Optional[str] = None,
) -> Dict[str, Any]:
    """合成纯背景板场景下的 KTV 光球 + 冻帧兼容包装器（实拍成片请分调 analyze_mp4_visual_freeze）。"""
    orb_res = analyze_ktv_orbs_pure_bg(frames_with_t, stage_html_text=stage_html_text)
    frz_res = analyze_mp4_visual_freeze(frames_with_t)
    return {**orb_res, **frz_res}


def analyze_topbar_and_circle_sync(
    pkg_json: Dict[str, Any],
    edl_json: Dict[str, Any],
    *,
    panel_spec: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    errors: List[str] = []
    elems = pkg_json.get("visual_elements") or []
    top_bars = [e for e in elems if e.get("kind") == "persistent_top_bar"]
    top_bar_bottom = 0
    if top_bars:
        tb = top_bars[0]
        bb = tb.get("final_bbox") or tb.get("bbox") or {}
        top_bar_bottom = int(bb.get("y", 168)) + int(bb.get("h", 150))
        tb_sf, tb_ef = int(tb.get("start_frame") or 0), int(tb.get("end_frame") or 999999)
        for ov in elems:
            kind = str(ov.get("kind") or "")
            if kind in ("center_burst_title", "stamp_tag", "quote_citation_card", "end_card_cta", "kinetic_callout"):
                osf, oef = int(ov.get("start_frame") or 0), int(ov.get("end_frame") or 0)
                if min(tb_ef, oef) > max(tb_sf, osf):
                    obb = ov.get("final_bbox") or ov.get("bbox") or {}
                    oy = int(obb.get("y") or 0)
                    if oy < top_bar_bottom + 12:
                        errors.append(f"{kind}.y={oy} < top_bar_bottom+12 ({top_bar_bottom + 12})")

    for cl in edl_json.get("clips") or []:
        pip = cl.get("pip_layout") or {}
        if pip.get("mode") == "concept_diagram_pip" and pip.get("with_anchor_circle", True):
            cy = int(pip.get("circle_cy") or 220)
            cr = int(pip.get("circle_r") or 92)
            if top_bar_bottom > 0 and (cy - cr) < (top_bar_bottom + 12):
                errors.append(f"concept_diagram_pip circle_top={cy - cr} < top_bar_bottom+12 ({top_bar_bottom + 12})")
            if panel_spec is not None:
                spec_cy = int(panel_spec.get("circle_cy") or 0)
                if abs(spec_cy - cy) > 20:
                    errors.append(f"circle_cy desync: spec={spec_cy} vs pip={cy}")
    return {"ok": len(errors) == 0, "top_bar_bottom": top_bar_bottom, "errors": errors}


def analyze_style15_paper_frame(
    frame_t0: np.ndarray,
    frame_mid: np.ndarray,
    ocr_res_t0: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    def _luma(arr: np.ndarray) -> np.ndarray:
        a = arr.astype(np.float32)
        return 0.2126 * a[:, :, 0] + 0.7152 * a[:, :, 1] + 0.0722 * a[:, :, 2]

    l_t0 = _luma(frame_t0)
    l_mid = _luma(frame_mid)
    top_p75_mid = float(np.percentile(l_mid[200:600, 100:980], 75))
    bot_p75_mid = float(np.percentile(l_mid[1500:1900, 100:980], 75))
    bot_p75_t0 = float(np.percentile(l_t0[1500:1900, 100:980], 75))
    ink_roi = l_t0[360:1240, 80:1000]
    dark_ink_pct = float((ink_roi < 120.0).mean() * 100.0)
    ocr_lines = len((ocr_res_t0 or {}).get("texts") or [])
    ok = (
        bot_p75_mid >= 175.0
        and bot_p75_t0 >= 175.0
        and (top_p75_mid - bot_p75_mid) <= 45.0
        and dark_ink_pct >= 1.5
        and (ocr_res_t0 is None or ocr_lines >= 1)
    )
    return {
        "ok": ok,
        "top_p75_mid": round(top_p75_mid, 1),
        "bot_p75_mid": round(bot_p75_mid, 1),
        "bot_p75_t0": round(bot_p75_t0, 1),
        "dark_ink_pct_t0": round(dark_ink_pct, 2),
        "ocr_lines_t0": ocr_lines,
    }


def analyze_style04_giant_text(expected_text: str, ocr_res: Dict[str, Any]) -> Dict[str, Any]:
    clean_exp = _clean_cjk(expected_text)
    upper_lines = [
        str(t.get("text") or "")
        for t in (ocr_res.get("texts") or [])
        if float(t.get("y") or 0.0) < 0.55
    ]
    joined = _clean_cjk("".join(upper_lines))
    ratio = SequenceMatcher(None, clean_exp, joined).ratio() if clean_exp else 0.0
    min_len = min(len(clean_exp), 6) if len(clean_exp) >= 5 else len(clean_exp)
    ok = (ratio >= 0.85) and (len(joined) >= min_len)
    return {
        "ok": ok,
        "expected": clean_exp,
        "ocr_joined": joined,
        "match_ratio": round(ratio, 3),
        "upper_lines": upper_lines,
    }


def analyze_style10_16_layering(
    pkg_json: Dict[str, Any],
    edl_json: Dict[str, Any],
    frame_rgb: Optional[np.ndarray] = None,
    *,
    face_bbox_norm: Optional[Dict[str, float]] = None,
    fg_text_bboxes_norm: Optional[List[Dict[str, float]]] = None,
) -> Dict[str, Any]:
    errors: List[str] = []
    head_crown_bbox_norm: Optional[Dict[str, float]] = None
    if face_bbox_norm is not None:
        _fx = float(face_bbox_norm.get("x") or 0.0)
        _fy = float(face_bbox_norm.get("y") or 0.0)
        _fw = float(face_bbox_norm.get("w") or 0.0)
        _fh = float(face_bbox_norm.get("h") or 0.0)
        _crown_y = max(0.0, _fy - 0.45 * _fh)
        _crown_h = min(1.0 - _crown_y, _fh * 1.45)
        head_crown_bbox_norm = {"x": _fx, "y": _crown_y, "w": _fw, "h": _crown_h}
        head_top_y_px = min(0.15 * CANVAS_H, _crown_y * CANVAS_H)
    else:
        head_top_y_px = 0.15 * CANVAS_H

    if head_crown_bbox_norm is not None and fg_text_bboxes_norm is not None:
        for idx, tb in enumerate(fg_text_bboxes_norm):
            iou = bbox_iou(tb, head_crown_bbox_norm)
            if iou > 0.02:
                errors.append(f"fg_text[{idx}] iou_with_face={iou:.3f} > 0.02")

    # 逐元素严格检查大字置后路由：不允许用 EDL 中其它时段的一段 pip 掩盖未路由的顶部前景大字
    fps = float(edl_json.get("fps") or 30.0)
    clips = list(edl_json.get("clips") or [])
    behind_modes = {"self_matting_behind_text", "person_behind_giant_text_pip"}

    target_elems: List[Dict[str, Any]] = []
    for el in (pkg_json.get("visual_elements") or []):
        kind = str(el.get("kind") or "")
        bb = el.get("final_bbox") or el.get("bbox") or {}
        is_big_top = (
            kind in ("flower_text", "kinetic_callout")
            and bb
            and float(bb.get("y") or 0.0) < 0.5 * CANVAS_H
            and (float(bb.get("h") or 0.0) >= 90.0 or float(el.get("font_size_px") or 0.0) >= 72.0)
        )
        if kind in ("center_burst_title", "stamp_tag") or is_big_top:
            target_elems.append(el)

    if not target_elems:
        has_any_behind = any(str((cl.get("pip_layout") or {}).get("mode") or "") in behind_modes for cl in clips)
        if not has_any_behind:
            errors.append("missing_behind_person_matting_routing")
    else:
        for idx, el in enumerate(target_elems):
            eid = str(el.get("id") or f"el_{idx}")
            if str(el.get("z_layer") or "") == "behind_person":
                continue
            sf = int(el.get("start_frame") or 0)
            ef = int(el.get("end_frame") or 0)
            el_s, el_e = sf / fps, ef / fps
            if el_e > el_s and clips:
                overlapping_clips = []
                for cl in clips:
                    cs = float(cl.get("timeline_in_sec") if cl.get("timeline_in_sec") is not None else (cl.get("start_sec") or 0.0))
                    ce = float(cl.get("timeline_out_sec") if cl.get("timeline_out_sec") is not None else (cl.get("end_sec") or 0.0))
                    if min(ce, el_e) > max(cs, el_s) + 1e-4:
                        overlapping_clips.append(cl)
            else:
                overlapping_clips = clips
            if overlapping_clips and all(
                str((cl.get("pip_layout") or {}).get("mode") or "") in behind_modes
                for cl in overlapping_clips
            ):
                continue

            # 未走 behind_person/self_matting_behind_text 时，必须拥有明确 bbox 且严格避开主播头顶区
            bb = el.get("final_bbox") or el.get("bbox")
            if isinstance(bb, dict) and {"x", "y", "w", "h"}.issubset(bb.keys()):
                bx, by, bw, bh = float(bb["x"]), float(bb["y"]), float(bb["w"]), float(bb["h"])
                bb_norm = {"x": bx / CANVAS_W, "y": by / CANVAS_H, "w": bw / CANVAS_W, "h": bh / CANVAS_H}
                iou_ok = (head_crown_bbox_norm is None) or (bbox_iou(bb_norm, head_crown_bbox_norm) == 0.0)
                if iou_ok and (by + bh) <= head_top_y_px:
                    continue
                errors.append(
                    f"unrouted_top_text({eid}): z_layer={el.get('z_layer')!r}, "
                    f"bottom={by + bh:.0f} > head_top_y={head_top_y_px:.0f}"
                )
            else:
                errors.append(f"unrouted_top_text({eid}): z_layer={el.get('z_layer')!r}, missing_bbox")

    unoccluded_frac = 0.0
    if frame_rgb is not None and face_bbox_norm is not None:
        fh, fw = frame_rgb.shape[:2]
        _fx_n = float(face_bbox_norm.get("x") or 0.0)
        _fy_n = float(face_bbox_norm.get("y") or 0.0)
        _fw_n = float(face_bbox_norm.get("w") or 0.0)
        _fh_n = float(face_bbox_norm.get("h") or 0.0)
        _crown_y_n = max(0.0, _fy_n - 0.45 * _fh_n)
        fx = int((_fx_n + 0.25 * _fw_n) * fw)
        fy = int((_crown_y_n + 0.10 * _fh_n) * fh)
        fx2 = int((_fx_n + 0.75 * _fw_n) * fw)
        fy2 = int((_fy_n + 0.80 * _fh_n) * fh)
        core = frame_rgb[max(0, fy):min(fh, fy2), max(0, fx):min(fw, fx2)]
        if core.size > 0:
            # 检测人脸及额头/发冠核心区内高亮度白色/亮黄前景字覆盖比例
            bright_text = (core.min(axis=2) > 220) | ((core[:, :, 0] > 230) & (core[:, :, 1] > 200) & (core[:, :, 2] < 120))
            unoccluded_frac = float(bright_text.mean())
            if unoccluded_frac > 0.15:
                errors.append(f"head_core_text_occlusion={unoccluded_frac:.3f} > 0.15")

    return {"ok": len(errors) == 0, "unoccluded_frac": round(unoccluded_frac, 4), "errors": errors}


def analyze_style07_wcag_frame_and_hex(
    fg_hex: str,
    bg_hex: str,
    frame_rgb: Optional[np.ndarray] = None,
    roi_y: Tuple[int, int] = (800, 1250),
) -> Dict[str, Any]:
    hex_ratio = wcag_ratio(rel_luminance(hex_to_rgb(fg_hex)), rel_luminance(hex_to_rgb(bg_hex)))
    frame_ratio: Optional[float] = None
    if frame_rgb is not None:
        roi = frame_rgb[roi_y[0]:roi_y[1], 80:1000]
        bg_med = np.median(roi.reshape(-1, 3), axis=0)
        bg_lum = rel_luminance(bg_med)
        dist = np.linalg.norm(roi.astype(np.float32) - bg_med.astype(np.float32), axis=2)
        fg_px = roi[dist > 45.0]
        if len(fg_px) >= 20:
            fg_med = np.median(fg_px, axis=0)
            frame_ratio = wcag_ratio(rel_luminance(fg_med), bg_lum)
        else:
            frame_ratio = 1.0
    ok = hex_ratio >= 4.5 and (frame_ratio is None or frame_ratio >= 4.5)
    return {
        "ok": ok,
        "hex_wcag": round(hex_ratio, 2),
        "frame_wcag": round(frame_ratio, 2) if frame_ratio is not None else None,
    }


def analyze_style06_safe_zone(
    pkg_json: Dict[str, Any],
    frame_diff_or_text_rgb: Optional[np.ndarray] = None,
    *,
    use_pixel_roi: bool = True,
    ocr_res: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    errors: List[str] = []
    tb_bottom_norm = 0.0
    for el in pkg_json.get("visual_elements") or []:
        if str(el.get("kind") or "") == "persistent_top_bar":
            tbb = el.get("final_bbox") or el.get("bbox") or {}
            tb_bottom_norm = max(
                tb_bottom_norm,
                (float(tbb.get("y") or 168) + float(tbb.get("h") or 150)) / float(CANVAS_H),
            )
        if str(el.get("callout_variant") or "") == "s06_vertical_poem" or str(el.get("kind") or "") == "s06_vertical_poem":
            bb = el.get("final_bbox") or el.get("bbox") or {}
            x, w, h = int(bb.get("x") or 0), int(bb.get("w") or 0), int(bb.get("h") or 0)
            if w >= h:
                errors.append(f"vertical_bbox_not_portrait: w={w} h={h}")
            if x + w > SAFE_X_MAX:
                errors.append(f"bbox_exceeds_safe_x: {x + w} > {SAFE_X_MAX}")

    right_rail_frac = 0.0
    if use_pixel_roi and frame_diff_or_text_rgb is not None:
        roi = frame_diff_or_text_rgb[340:1100, :]
        # 仅用于纯暗底（#14151B）离屏叠层/合成帧，不用于含实拍画面成片
        fg_mask = roi.max(axis=2) > 95
        total_fg = int(fg_mask.sum())
        if total_fg > 0:
            rail_fg = int(fg_mask[:, SAFE_X_MAX:].sum())
            right_rail_frac = rail_fg / float(total_fg)
            if right_rail_frac > 0.01:
                errors.append(f"right_rail_text_frac={right_rail_frac:.4f} > 0.01")

    if ocr_res is not None:
        safe_x_norm = SAFE_X_MAX / float(CANVAS_W)
        for t in (ocr_res.get("texts") or []):
            ty = float(t.get("y") or 0.0)
            th = float(t.get("h") or 0.0)
            tx = float(t.get("x") or 0.0)
            tw = float(t.get("w") or 0.0)
            if tb_bottom_norm > 0.0 and (ty + 0.5 * th) <= (tb_bottom_norm + 0.005):
                continue
            if 0.18 <= ty <= 0.60 and (tx + tw) > safe_x_norm:
                errors.append(
                    f"ocr_exceeds_safe_x:{str(t.get('text') or '')!r}:x2={(tx + tw) * CANVAS_W:.1f}>{SAFE_X_MAX}"
                )
    return {"ok": len(errors) == 0, "right_rail_frac": round(right_rail_frac, 4), "errors": errors}


def analyze_portrait_matte_smoothness(alpha_u8: np.ndarray) -> Dict[str, Any]:
    mask_bin = alpha_u8 >= 128
    dil = ndimage.binary_dilation(mask_bin, iterations=4)
    ero = ndimage.binary_erosion(mask_bin, iterations=4)
    band = dil ^ ero
    band_cnt = int(band.sum())
    aa_cnt = int(((alpha_u8 > 15) & (alpha_u8 < 240) & band).sum())
    aa_frac = aa_cnt / float(max(1, band_cnt))

    # 左右轮廓二阶差分锯齿率
    left_edges: List[int] = []
    right_edges: List[int] = []
    for y in range(alpha_u8.shape[0]):
        xs = np.where(mask_bin[y])[0]
        if len(xs) >= 10:
            left_edges.append(int(xs[0]))
            right_edges.append(int(xs[-1]))
    jag_vals: List[bool] = []
    for arr_list in (left_edges, right_edges):
        if len(arr_list) >= 5:
            e = np.asarray(arr_list, dtype=np.int32)
            d2 = np.abs(e[2:] - 2 * e[1:-1] + e[:-2])
            jag_vals.extend(((d2 >= 2) & (d2 <= 12)).tolist())
    micro_jag = float(np.mean(jag_vals)) if jag_vals else 1.0
    ok = (aa_frac >= 0.20) and (micro_jag <= 0.08)
    return {
        "ok": ok,
        "boundary_aa_fraction": round(aa_frac, 3),
        "micro_jag": round(micro_jag, 4),
    }


def validate_edl_panel_groups(edl_json: Dict[str, Any]) -> List[str]:
    """校验 04_edl.json 中所有使用 v11_panel_group_id 的跨镜面板组：时长 >= 3.0s 且 offset 严格单调递增。"""
    groups: Dict[str, List[Tuple[float, float]]] = {}
    for cl in edl_json.get("clips") or []:
        pip = cl.get("pip_layout") or {}
        gid = str(pip.get("v11_panel_group_id") or "")
        if gid:
            dur = float(cl.get("duration_sec") or 0.0)
            off = float(pip.get("v11_panel_offset_sec") or 0.0)
            gdur = float(pip.get("v11_panel_group_dur") or 0.0)
            groups.setdefault(gid, []).append((dur, off, gdur))  # type: ignore[arg-type]
    group_errors: List[str] = []
    for gid, items in groups.items():
        if max(it[2] for it in items) < 3.0:  # type: ignore[index]
            group_errors.append(f"{gid}:group_dur<3.0")
        for i in range(len(items) - 1):
            prev_dur, prev_off, _ = items[i]  # type: ignore[misc]
            _, next_off, _ = items[i + 1]  # type: ignore[misc]
            if prev_dur > 0.0 and not (next_off > prev_off + 1e-4):
                group_errors.append(f"{gid}:non_monotonic_offset")
                break
    return group_errors


def analyze_style24_edl_and_frames(
    edl_json: Dict[str, Any],
    frames_rgb: Sequence[np.ndarray],
) -> Dict[str, Any]:
    total_dur = max(0.1, float(edl_json.get("total_duration_sec") or 0.0))
    wb_dur = 0.0
    groups: Dict[str, List[Tuple[float, float, float]]] = {}
    for cl in edl_json.get("clips") or []:
        pip = cl.get("pip_layout") or {}
        if pip.get("mode") == "review_whiteboard_pip":
            dur = float(cl.get("duration_sec") or 0.0)
            wb_dur += dur
            gid = str(pip.get("v11_panel_group_id") or "")
            if gid:
                groups.setdefault(gid, []).append((
                    dur,
                    float(pip.get("v11_panel_offset_sec") or 0.0),
                    float(pip.get("v11_panel_group_dur") or 0.0),
                ))
    edl_ratio = wb_dur / total_dur

    group_errors: List[str] = []
    if not groups:
        group_errors.append("missing_v11_panel_group_id")
    for gid, items in groups.items():
        if max(it[2] for it in items) < 3.0:
            group_errors.append(f"{gid}:group_dur<3.0")
        for i in range(len(items) - 1):
            prev_dur, prev_off, _ = items[i]
            _, next_off, _ = items[i + 1]
            if prev_dur > 0.0 and not (next_off > prev_off + 1e-4):
                group_errors.append(f"{gid}:non_monotonic_offset")
                break

    wb_frames = 0
    for arr in frames_rgb:
        a = arr.astype(np.float32)
        luma = 0.2126 * a[:, :, 0] + 0.7152 * a[:, :, 1] + 0.0722 * a[:, :, 2]
        if float((luma >= 195.0).mean()) >= 0.30:
            wb_frames += 1
    frame_ratio = wb_frames / float(max(1, len(frames_rgb)))
    ok = (edl_ratio >= 0.15) and (frame_ratio >= 0.15) and not group_errors
    return {
        "ok": ok,
        "edl_ratio": round(edl_ratio, 3),
        "frame_ratio": round(frame_ratio, 3),
        "group_errors": group_errors,
    }


def analyze_cross_style_frame8_uniqueness(
    style_frames: Dict[str, np.ndarray],
    ocr_map: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    sig_counts: Dict[str, List[str]] = {}
    marker_hits: List[str] = []
    for sid, arr in style_frames.items():
        luma = 0.2126 * arr[:, :, 0] + 0.7152 * arr[:, :, 1] + 0.0722 * arr[:, :, 2]
        is_dark = float((luma < 60.0).mean()) >= 0.55
        texts = [str(t.get("text") or "") for t in (ocr_map.get(sid, {}).get("texts") or [])]
        joined = _clean_cjk("".join(texts))
        for m in GENERIC_FRAME8_MARKERS:
            if _clean_cjk(m) in joined:
                marker_hits.append(f"{sid}:{m}")
        if is_dark and len(joined) >= 4:
            sig = joined[:12]
            sig_counts.setdefault(sig, []).append(sid)
    shared = {k: v for k, v in sig_counts.items() if len(v) > 2}
    return {"ok": (not marker_hits and not shared), "marker_hits": marker_hits, "shared_dark_cards": shared}


def check_dual_callout_overlap(pkg_json: Dict[str, Any]) -> List[str]:
    elems = pkg_json.get("visual_elements") or []
    callouts = [e for e in elems if str(e.get("kind") or "") in ("kinetic_callout", "stamp_tag")]
    bursts = [e for e in elems if str(e.get("kind") or "") == "center_burst_title"]
    collisions: List[str] = []
    for c in callouts:
        cs, ce = int(c.get("start_frame") or 0), int(c.get("end_frame") or 0)
        cb = c.get("final_bbox") or c.get("bbox") or {}
        for b in bursts:
            bs, be = int(b.get("start_frame") or 0), int(b.get("end_frame") or 0)
            if min(ce, be) > max(cs, bs):
                bb = b.get("final_bbox") or b.get("bbox") or {}
                if not cb or not bb or bbox_iou(cb, bb) > 0.05:
                    collisions.append(f"{c.get('id')} vs {b.get('id')}")
    return collisions


# ---------------------------------------------------------------------------
# G5 核心黑盒检测器：隐私路径、草稿名、分支、软链、必须保留资产
# ---------------------------------------------------------------------------
def load_draft_names_from_forensic() -> List[str]:
    src = REPO_ROOT / "forensic" / "style_sources.json"
    if not src.is_file():
        return []
    data = _read_json(src).get("styles") or {}
    return [str(v) for v in data.values() if str(v).strip()]


def scan_files_for_leaks(
    files: Sequence[Path],
    *,
    draft_names: Sequence[str],
    exclude_prefixes: Sequence[str] = ("forensic/",),
) -> Tuple[List[str], List[str]]:
    priv_hits: List[str] = []
    draft_hits: List[str] = []
    for fp in files:
        if not fp.is_file() or fp.suffix.lower() not in TEXT_SCAN_EXTS:
            continue
        try:
            rel = fp.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
        except ValueError:
            rel = fp.name
        if any(rel.startswith(px) for px in exclude_prefixes):
            continue
        txt = fp.read_text(encoding="utf-8", errors="ignore")
        m = PRIVACY_PATH_RE.search(txt)
        if m:
            priv_hits.append(f"{rel}:{m.group(0)}")
        for d in draft_names:
            if d and d in txt:
                draft_hits.append(f"{rel}:{d}")
    return priv_hits, draft_hits


def _repo_prod_and_changed_files() -> List[Path]:
    """返回生产域 (short-video-studio/) 全部 tracked 文件 + dev_tools/v12_e2e_suite.py + 相对基准或工作区的全部新增/变更文件。"""
    rel_set: set[str] = {"dev_tools/v12_e2e_suite.py"}
    prod_out = subprocess.check_output(
        ["git", "ls-files", "short-video-studio/"], cwd=str(REPO_ROOT), text=True
    )
    for ln in prod_out.splitlines():
        if ln.strip():
            rel_set.add(ln.strip())

    # 工作区修改 + 未跟踪文件
    for cmd in (
        ["git", "diff", "--name-only"],
        ["git", "diff", "--cached", "--name-only"],
        ["git", "ls-files", "--others", "--exclude-standard"],
    ):
        out = subprocess.check_output(cmd, cwd=str(REPO_ROOT), text=True)
        for ln in out.splitlines():
            if ln.strip():
                rel_set.add(ln.strip())

    # 相对基准 commit d83d0d9 的提交改动
    chk = subprocess.run(["git", "rev-parse", "--verify", "d83d0d9"], cwd=str(REPO_ROOT), capture_output=True)
    if chk.returncode == 0:
        out = subprocess.check_output(["git", "diff", "--name-only", "d83d0d9..HEAD"], cwd=str(REPO_ROOT), text=True)
        for ln in out.splitlines():
            if ln.strip():
                rel_set.add(ln.strip())
    return [REPO_ROOT / r for r in sorted(rel_set)]


def _repo_scan_files(mode: str) -> List[Path]:
    """smoke/pipeline 扫描生产域与 V12 新增改动；full 模式扫描全仓 git ls-files（仅排除 forensic/）。"""
    if mode == "full":
        rel_set: set[str] = set()
        for cmd in (
            ["git", "ls-files"],
            ["git", "ls-files", "--others", "--exclude-standard"],
        ):
            out = subprocess.check_output(cmd, cwd=str(REPO_ROOT), text=True)
            for ln in out.splitlines():
                if ln.strip():
                    rel_set.add(ln.strip())
        return [REPO_ROOT / r for r in sorted(rel_set)]
    return _repo_prod_and_changed_files()


def find_broken_symlinks(root_dirs: Sequence[Path]) -> List[str]:
    broken: List[str] = []
    for base in root_dirs:
        if not base.exists():
            continue
        for p in base.rglob("01_normalized"):
            if p.is_symlink() and not p.exists():
                broken.append(str(p))
    return broken


# ---------------------------------------------------------------------------
# 合成夹具生成器（仅在 TemporaryDirectory 内生成，供 smoke / Tier 1-3 校准）
# ---------------------------------------------------------------------------
def _make_audio_events_fixture(tmp_dir: Path, sentences_spec: List[Tuple[str, str, float, float]]) -> Path:
    wav_p = _write_wav_tone(tmp_dir / "voice_locked.wav", seconds=max(2.0, sentences_spec[-1][3] + 0.3))
    sents = []
    events = []
    for idx, (sid, text, st, en) in enumerate(sentences_spec):
        chars = [c for c in text if not c.isspace()]
        dur = max(0.2, en - st)
        words = []
        for wi, ch in enumerate(chars):
            ws = st + dur * wi / max(1, len(chars))
            we = st + dur * (wi + 1) / max(1, len(chars))
            words.append({"word": ch, "start_sec": round(ws, 3), "end_sec": round(we, 3)})
        sents.append({
            "id": sid,
            "text": text,
            "say_text": text,
            "source_media": str(tmp_dir / "aroll_01.mp4"),
            "voice_source": str(wav_p),
            "source_in_sec": st,
            "source_out_sec": en,
            "start_sec": st,
            "end_sec": en,
            "start_frame": int(round(st * 30)),
            "end_frame": int(round(en * 30)),
            "pause_after_sec": 0.15,
            "keywords": [text[:2]] if len(text) >= 2 else [text],
            "words": words,
        })
        events.append({
            "id": f"ev_{idx+1:03d}",
            "time_sec": st,
            "frame": int(round(st * 30)),
            "kind": "sentence_start",
        })
    ae = {
        "step": "03_audio_events",
        "locked": True,
        "fps": 30,
        "sample_rate": 16000,
        "locked_duration_sec": round(sentences_spec[-1][3] + 0.3, 3),
        "total_duration_sec": round(sentences_spec[-1][3] + 0.3, 3),
        "locked_duration_frames": int(round((sentences_spec[-1][3] + 0.3) * 30)),
        "locked_voice_wav": str(wav_p),
        "sentences": sents,
        "events": events,
    }
    pdir = tmp_dir / "pipeline"
    pdir.mkdir(parents=True, exist_ok=True)
    out = pdir / "03_audio_events.json"
    out.write_text(json.dumps(ae, ensure_ascii=False, indent=2), encoding="utf-8")
    return pdir


def _render_chinese_card(
    out_path: Path,
    text: str,
    *,
    bg: Tuple[int, int, int] = (20, 21, 27),
    fg: Tuple[int, int, int] = (255, 225, 77),
    pos: Tuple[int, int] = (120, 220),
    font_size: int = 130,
    font_loader: Optional[Callable[[int], ImageFont.FreeTypeFont]] = None,
) -> Path:
    im = Image.new("RGB", (CANVAS_W, CANVAS_H), bg)
    draw = ImageDraw.Draw(im)
    font = font_loader(font_size) if font_loader else ImageFont.load_default()
    draw.text(pos, text, font=font, fill=fg)
    im.save(out_path)
    return out_path


# ---------------------------------------------------------------------------
# Tier 1 (30 项)：G1(6) + G2(6) + G3(8) + G4(5) + G5(5)
# ---------------------------------------------------------------------------
def t1_g1_01_dangling_particles(ctx: SuiteContext) -> CheckResult:
    neg = ["删掉公司名改了", "是我们想事情的", "我系牛哥花咗", "岭南元素嘅", "先把店开在"]
    pos = ["删掉公司名", "想事情的方式", "我系牛哥", "体现岭南元素", "被彻底固化了", "先把店开起来。"]
    neg_issues = detect_dangling_particles(neg, is_sentence_final=[False, False, False, True, False])
    pos_issues = detect_dangling_particles(pos, is_sentence_final=[False, False, False, False, True, True])
    mid_aspect_issues = detect_dangling_particles(["被固化了", "之后"], is_sentence_final=[False, True])
    strict_punct_issues = detect_dangling_particles(
        ["被彻底固化了"], is_sentence_final=[True], require_terminal_punct_on_final=True
    )
    ok = (
        (len(neg_issues) == 5)
        and (len(pos_issues) == 0)
        and (len(mid_aspect_issues) == 1)
        and (len(strict_punct_issues) == 1)
    )
    if ctx.mode in ("pipeline", "full"):
        # 对 step5_subtitles.dp_segment_short_lines 跑真实语义分词验证（带句末边界标记）
        samples = [
            "我们做品牌定位最怕想事情的方式被彻底固化了",
            "我系牛哥花咗三百万装修呢间充满岭南元素嘅茶楼",
            "新邻舍涂装标准让老房翻新三天就能直接入住",
        ]
        live_issues: List[str] = []
        for s in samples:
            segs = [seg[2] for seg in step5_subtitles.dp_segment_short_lines(s, 608, font_size=62)]
            is_final = [i == len(segs) - 1 for i in range(len(segs))]
            live_issues.extend(detect_dangling_particles(segs, is_sentence_final=is_final))
        ok = ok and (len(live_issues) == 0)
        return CheckResult(
            "T1-G1-01", 1, "G1", "通用断句零挂尾助词检测",
            "PASS" if ok else "FAIL",
            f"neg_caught={len(neg_issues)}/5 mid_aspect={len(mid_aspect_issues)} pos_err={len(pos_issues)} live_err={len(live_issues)}",
            "neg=5, mid_aspect=1, pos=0, live=0",
            "; ".join(live_issues[:3]) or "普通话/粤语/未见域断句无挂尾助词（句末 了/咗 合法放行）",
        )
    return CheckResult(
        "T1-G1-01", 1, "G1", "通用断句零挂尾助词检测",
        "PASS" if ok else "FAIL",
        f"neg_caught={len(neg_issues)}/5 mid_aspect={len(mid_aspect_issues)} pos_err={len(pos_issues)}",
        "neg=5, mid_aspect=1, pos=0",
        "校准通过（负样本 5/5+句中了字命中，句末去标点了字与正样本 0 误报）" if ok else f"neg={neg_issues} mid={mid_aspect_issues} pos={pos_issues}",
    )


def t1_g1_02_aux_head_and_fragments(ctx: SuiteContext) -> CheckResult:
    neg = ["的思维方式", "嘅元素", "企业", "厨房"]
    pos = ["固化的思维方式", "岭南元素", "服务好每一家企业", "开放式透明厨房"]
    neg_issues = detect_aux_head_and_fragments(neg)
    pos_issues = detect_aux_head_and_fragments(pos)
    ok = (len(neg_issues) >= 4) and (len(pos_issues) == 0)
    return CheckResult(
        "T1-G1-02", 1, "G1", "零助词领起与零残句碎片检测",
        "PASS" if ok else "FAIL",
        f"neg_caught={len(neg_issues)} pos_err={len(pos_issues)}",
        "neg>=4, pos=0",
        "助词头与 <=2 字非感叹碎片检测准确" if ok else f"neg={neg_issues} pos={pos_issues}",
    )


def t1_g1_03_split_numbers_compounds(ctx: SuiteContext) -> CheckResult:
    neg = ["当时亏了200", "多万才醒悟", "面积足足有六百", "平米左右", "占比达到三分", "之一以上"]
    pos = ["当时亏了200多万", "才彻底醒悟过来", "面积足足有六百平米", "占比达到三分之一以上"]
    neg_issues = detect_split_numbers_compounds(neg)
    pos_issues = detect_split_numbers_compounds(pos)
    ok = (len(neg_issues) == 3) and (len(pos_issues) == 0)
    return CheckResult(
        "T1-G1-03", 1, "G1", "零劈裂数字量词与分数检测",
        "PASS" if ok else "FAIL",
        f"neg_caught={len(neg_issues)}/3 pos_err={len(pos_issues)}",
        "neg=3, pos=0",
        "数字量词与分数跨行保护校准通过" if ok else f"neg={neg_issues} pos={pos_issues}",
    )


def t1_g1_04_style02_ylane_and_stacking(ctx: SuiteContext) -> CheckResult:
    neg_items = [
        {"id": "1", "group_id": "g1", "text_or_asset": "先答清楚你自己的", "start_sec": 1.0, "end_sec": 2.4, "y_norm": -0.286, "z_layer": "fg"},
        {"id": "2", "group_id": "g1", "text_or_asset": "自己的生意", "start_sec": 1.5, "end_sec": 2.8, "y_norm": -0.286, "z_layer": "fg"},
        {"id": "3", "group_id": "g1", "text_or_asset": "第三行同时出现", "start_sec": 1.6, "end_sec": 2.7, "y_norm": -0.15, "z_layer": "fg"},
        {"id": "4", "group_id": "g1", "text_or_asset": "第四行超限堆叠", "start_sec": 1.7, "end_sec": 2.6, "y_norm": -0.45, "z_layer": "fg"},
    ]
    pos_items = [
        {"id": "1", "group_id": "g1", "text_or_asset": "先答清楚这个问题", "start_sec": 1.0, "end_sec": 2.0, "y_norm": -0.18, "z_layer": "fg"},
        {"id": "2", "group_id": "g1", "text_or_asset": "到底在做什么生意", "start_sec": 2.05, "end_sec": 3.2, "y_norm": -0.32, "z_layer": "fg"},
        {"id": "3", "group_id": "g1", "text_or_asset": "把单店盈利算清楚", "start_sec": 2.1, "end_sec": 3.2, "y_norm": -0.46, "z_layer": "fg"},
    ]
    r_neg = analyze_style02_kinetic_items(neg_items)
    r_pos = analyze_style02_kinetic_items(pos_items)
    ok = (not r_neg["ok"]) and bool(r_neg["same_y_overlaps"]) and (r_neg["max_simultaneous_rows"] > 3) and r_pos["ok"]
    return CheckResult(
        "T1-G1-04", 1, "G1", "style_02 同Y碰撞与多行堆叠检测",
        "PASS" if ok else "FAIL",
        f"neg_ok={r_neg['ok']} rows={r_neg['max_simultaneous_rows']} pos_ok={r_pos['ok']}",
        "neg=FAIL(>3 rows & same-Y), pos=PASS(<=3 rows)",
        f"neg_overlaps={len(r_neg['same_y_overlaps'])} neg_dups={len(r_neg['prefix_dups'])}",
    )


def _make_qa_sandbox_dir(
    root: Path,
    *,
    sentence_text: str,
    entries: List[Dict[str, Any]],
    protected_terms: Optional[List[str]] = None,
) -> Tuple[Path, Path]:
    pdir = _make_audio_events_fixture(root, [("sent_001", sentence_text, 0.0, 2.5)])
    vid = pdir / "out.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=c=0x181A20:s=1080x1920:d=2.6:r=30",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2.6",
        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
        str(vid),
    ], check=True)
    (pdir / "01_manifest.json").write_text(json.dumps({"items": []}), encoding="utf-8")
    (pdir / "02_transcript.json").write_text(json.dumps({"words": []}), encoding="utf-8")
    (pdir / "04_edl.json").write_text(json.dumps({
        "fps": 30, "total_duration_sec": 2.6,
        "clips": [{
            "clip_id": "c1", "track_role": "a_roll", "media_path": str(vid),
            "timeline_in_sec": 0.0, "timeline_out_sec": 2.6, "duration_sec": 2.6,
            "source_in_sec": 0.0, "source_out_sec": 2.6,
        }],
    }), encoding="utf-8")
    (pdir / "05_subtitles.json").write_text(json.dumps({"step": "05_subtitles", "entries": entries}, ensure_ascii=False), encoding="utf-8")
    (pdir / "06_packaging.json").write_text(json.dumps({"visual_elements": [], "sfx_cues": []}), encoding="utf-8")
    (pdir / "07_color_plan.json").write_text(json.dumps({}), encoding="utf-8")
    if protected_terms:
        (pdir / "terms.txt").write_text("\n".join(protected_terms) + "\n", encoding="utf-8")
    return pdir, vid


def _run_qa_seg_check(pdir: Path, vid: Path) -> Dict[str, Any]:
    fake_ctx = {"r128": {"integrated_lufs": -14.0, "true_peak_dbtp": -1.5}, "long_silences": []}
    with mock.patch.object(independent_verifier, "extract_audio_context_once", return_value=fake_ctx), \
         mock.patch.object(independent_verifier, "run_blackbox_verification", return_value={"checks": []}):
        report = qa_inspector.inspect_video(pdir, video_path=vid, export_mode="mp4")
    by_id = {c["id"]: c for c in (report.get("checks") or [])}
    return by_id["subtitle_segmentation_clean"]


def t1_g1_05_qa_inspector_seg_check(ctx: SuiteContext) -> CheckResult:
    # 真实调用 qa_inspector.inspect_video 校验 subtitle_segmentation_clean，严防桩测试
    with tempfile.TemporaryDirectory() as td:
        tdir = Path(td)
        pdir_neg, vid_neg = _make_qa_sandbox_dir(
            tdir / "neg_term",
            sentence_text="微晶纳米涂层施工非常稳定",
            entries=[
                {"id": "sub_1", "sentence_id": "sent_001", "text": "微晶纳米", "start_sec": 0.0, "end_sec": 1.1, "suppressed": False},
                {"id": "sub_2", "sentence_id": "sent_001", "text": "涂层施工非常稳定", "start_sec": 1.15, "end_sec": 2.4, "suppressed": False},
            ],
            protected_terms=["微晶纳米涂层"],
        )
        qa_neg_term = _run_qa_seg_check(pdir_neg, vid_neg)

        pdir_pos, vid_pos = _make_qa_sandbox_dir(
            tdir / "pos_term",
            sentence_text="微晶纳米涂层施工非常稳定",
            entries=[
                {"id": "sub_1", "sentence_id": "sent_001", "text": "微晶纳米涂层", "start_sec": 0.0, "end_sec": 1.2, "suppressed": False},
                {"id": "sub_2", "sentence_id": "sent_001", "text": "施工非常稳定", "start_sec": 1.25, "end_sec": 2.4, "suppressed": False},
            ],
            protected_terms=["微晶纳米涂层"],
        )
        qa_pos = _run_qa_seg_check(pdir_pos, vid_pos)

        # 通用检测器正负校准
        u_neg = detect_dangling_particles(["这是我们的", "方法"], is_sentence_final=[False, True]) + detect_aux_head_and_fragments(["这是我们的", "方法"])
        u_pos = detect_dangling_particles(["微晶纳米涂层", "施工非常稳定"], is_sentence_final=[False, True]) + detect_aux_head_and_fragments(["微晶纳米涂层", "施工非常稳定"])

        ok = (
            qa_neg_term.get("status") == "FAIL"
            and qa_pos.get("status") == "PASS"
            and len(u_neg) >= 2
            and len(u_pos) == 0
        )

        if ctx.mode in ("pipeline", "full"):
            qa_src = (SCRIPTS_ROOT / "qa_inspector.py").read_text(encoding="utf-8")
            no_hardcoded_pairs = ("_sub_protect_pairs" not in qa_src)
            pdir_unseen1, vid_u1 = _make_qa_sandbox_dir(
                tdir / "unseen_de",
                sentence_text="这是我们的方法",
                entries=[
                    {"id": "sub_1", "sentence_id": "sent_001", "text": "这是我们的", "start_sec": 0.0, "end_sec": 1.1, "suppressed": False},
                    {"id": "sub_2", "sentence_id": "sent_001", "text": "方法", "start_sec": 1.15, "end_sec": 2.4, "suppressed": False},
                ],
            )
            qa_u1 = _run_qa_seg_check(pdir_unseen1, vid_u1)
            pdir_unseen2, vid_u2 = _make_qa_sandbox_dir(
                tdir / "unseen_le",
                sentence_text="他昨天去了店里",
                entries=[
                    {"id": "sub_1", "sentence_id": "sent_001", "text": "他昨天去", "start_sec": 0.0, "end_sec": 1.1, "suppressed": False},
                    {"id": "sub_2", "sentence_id": "sent_001", "text": "了店里", "start_sec": 1.15, "end_sec": 2.4, "suppressed": False},
                ],
            )
            qa_u2 = _run_qa_seg_check(pdir_unseen2, vid_u2)
            ok = ok and no_hardcoded_pairs and (qa_u1.get("status") == "FAIL") and (qa_u2.get("status") == "FAIL")
            return CheckResult(
                "T1-G1-05", 1, "G1", "qa_inspector 通用字幕切分干净度验证",
                "PASS" if ok else "FAIL",
                f"term_neg={qa_neg_term.get('status')} pos={qa_pos.get('status')} unseen=({qa_u1.get('status')},{qa_u2.get('status')}) no_hardcode={no_hardcoded_pairs}",
                "neg=FAIL, pos=PASS, unseen=FAIL, no_hardcode=True",
                f"qa_inspector 实测：{qa_u1.get('measured')} | {qa_u2.get('measured')}",
            )

    return CheckResult(
        "T1-G1-05", 1, "G1", "qa_inspector 通用字幕切分干净度验证",
        "PASS" if ok else "FAIL",
        f"qa_neg={qa_neg_term.get('status')} qa_pos={qa_pos.get('status')} u_neg={len(u_neg)} u_pos={len(u_pos)}",
        "qa_neg=FAIL, qa_pos=PASS",
        f"真实调用 qa_inspector.inspect_video：{qa_neg_term.get('measured')}",
    )


def t1_g1_06_abolition_subtitles_tables(ctx: SuiteContext) -> CheckResult:
    with tempfile.TemporaryDirectory() as td:
        tdir = Path(td)
        bad_py = tdir / "bad.py"
        good_py = tdir / "good.py"
        bad_py.write_text(f"{ABOLISHED_G1_SYMBOLS[0]} = {{}}\n", encoding="utf-8")
        good_py.write_text("UNIVERSAL_SUBTITLES = []\n", encoding="utf-8")
        neg_hits = check_abolition_symbols_in_files([bad_py], ABOLISHED_G1_SYMBOLS)
        pos_hits = check_abolition_symbols_in_files([good_py], ABOLISHED_G1_SYMBOLS)
    self_ok = (len(neg_hits) == 1) and (len(pos_hits) == 0)
    if ctx.mode in ("pipeline", "full"):
        targets = list((REPO_ROOT / "short-video-studio" / "scripts").rglob("*.py")) + list((REPO_ROOT / "dev_tools").rglob("*.py"))
        live_hits = check_abolition_symbols_in_files(targets, ABOLISHED_G1_SYMBOLS)
        ok = self_ok and (len(live_hits) == 0)
        return CheckResult(
            "T1-G1-06", 1, "G1", "静态字幕硬编码表废除门禁",
            "PASS" if ok else "FAIL",
            f"self_ok={self_ok} live_hits={len(live_hits)}",
            "live_hits=0",
            ", ".join(live_hits) if live_hits else "scripts/ 与 dev_tools/ 已彻底清除静态字幕表",
        )
    return CheckResult(
        "T1-G1-06", 1, "G1", "静态字幕硬编码表废除门禁",
        "PASS" if self_ok else "FAIL",
        f"neg_hits={len(neg_hits)} pos_hits={len(pos_hits)}",
        "self_test=PASS",
        "烟雾自校准通过（真实仓库废除将在 pipeline/full 模式严格拦截）",
    )


def t1_g2_01_abolition_director_blocks(ctx: SuiteContext) -> CheckResult:
    with tempfile.TemporaryDirectory() as td:
        tdir = Path(td)
        bad_py = tdir / "bad_plan.py"
        good_py = tdir / "good_plan.py"
        bad_py.write_text(f"{ABOLISHED_G2_SYMBOLS[0]} = []\ndef {ABOLISHED_G2_SYMBOLS[2]}(): pass\n", encoding="utf-8")
        good_py.write_text("def build_universal_plan(): return {}\n", encoding="utf-8")
        neg_hits = check_abolition_symbols_in_files([bad_py], ABOLISHED_G2_SYMBOLS)
        pos_hits = check_abolition_symbols_in_files([good_py], ABOLISHED_G2_SYMBOLS)
    self_ok = (len(neg_hits) == 2) and (len(pos_hits) == 0)
    if ctx.mode in ("pipeline", "full"):
        targets = list((REPO_ROOT / "short-video-studio" / "scripts").rglob("*.py")) + list((REPO_ROOT / "dev_tools").rglob("*.py"))
        live_hits = check_abolition_symbols_in_files(targets, ABOLISHED_G2_SYMBOLS)
        # 同步检查硬编码 style_callouts 分支残留（如 if style_id == "style_05":\n callouts =）
        callout_re = re.compile(r'style_id\s*==\s*["\']style_0[568]["\'].{0,160}callouts\s*=', re.DOTALL)
        for fp in targets:
            if not fp.is_file() or fp.name == Path(__file__).name:
                continue
            txt = fp.read_text(encoding="utf-8", errors="ignore")
            if callout_re.search(txt):
                live_hits.append(f"{fp.name}: hardcoded_style_callouts")
        ok = self_ok and (len(live_hits) == 0)
        return CheckResult(
            "T1-G2-01", 1, "G2", "v11_director_planner 静态块废除门禁",
            "PASS" if ok else "FAIL",
            f"self_ok={self_ok} live_hits={len(live_hits)}",
            "live_hits=0",
            ", ".join(live_hits) if live_hits else "静态块、写死封面文案与硬编码 style_callouts 已废除",
        )
    return CheckResult(
        "T1-G2-01", 1, "G2", "v11_director_planner 静态块废除门禁",
        "PASS" if self_ok else "FAIL",
        f"neg_hits={len(neg_hits)}/2 pos_hits={len(pos_hits)}",
        "self_test=PASS",
        "烟雾自校准通过（真实仓库废除将在 pipeline/full 模式严格拦截）",
    )


def t1_g2_02_director_plan_schema(ctx: SuiteContext) -> CheckResult:
    with tempfile.TemporaryDirectory() as td:
        pdir = _make_audio_events_fixture(Path(td), [
            ("sent_001", "开店之前先把成本算清楚", 0.0, 2.5),
            ("sent_002", "毛利率低于六成千万别扩店", 2.7, 5.5),
        ])
        ae = _read_json(pdir / "03_audio_events.json")
        valid_plan = director_plan.generate_deterministic_director_plan(ae, style_id="style_09")
        # 补充通用字段检查（若当前未合并 M2，则构造符合 V12 契约的完整对象验证校验器）
        v12_plan = dict(valid_plan)
        v12_plan.setdefault("semantic_subtitles", [{"text": "开店之前", "keywords": ["开店"]}])
        v12_plan.setdefault("style_scene_beats", [{"target_sec": 0.5, "target_dur": 2.0, "layout": {"mode": "doc_dossier_pip", "panel_title": "成本拆解"}}])
        v12_plan.setdefault("style_callouts", [{"text": "先算清成本", "start_sec": 3.5, "end_sec": 5.0}])
        v12_plan["cover_copy"] = {"headline_top": "先算清成本", "highlight_badge": "别盲目扩店", "sub_line": "单店盈利模型", "gbro_style": "style-01"}
        broken_plan = {"acts": [], "cover_copy": {}}
        r_neg = validate_director_plan_universal(broken_plan)
        r_pos = validate_director_plan_universal(v12_plan, ae)
        ok = (not r_neg["ok"]) and r_pos["ok"]
    return CheckResult(
        "T1-G2-02", 1, "G2", "03b_director_plan 通用契约校验",
        "PASS" if ok else "FAIL",
        f"neg_ok={r_neg['ok']} pos_ok={r_pos['ok']}",
        "neg=FAIL, pos=PASS",
        "完整 Schema 覆盖校验通过" if ok else str(r_pos["errors"]),
    )


def t1_g2_03_zero_frame8_generic_black_card(ctx: SuiteContext) -> CheckResult:
    neg_plan = {
        "acts": [{"act_id": "act_1", "sentence_ids": ["sent_001"]}],
        "music_cues": [],
        "cover_copy": {"headline_top": "标题"},
        "semantic_subtitles": [{"text": "一句"}],
        "style_scene_beats": [
            {"target_sec": 66.5, "frac": 0.84, "layout": {"mode": "concept_diagram_pip", "panel_title": "夫妻店到雇人断层", "items": ["01/02/03"]}},
        ],
        "style_callouts": [],
    }
    pos_plan = {
        "acts": [{"act_id": "act_1", "sentence_ids": ["sent_001"]}],
        "music_cues": [],
        "cover_copy": {"headline_top": "旧墙翻新标准"},
        "semantic_subtitles": [{"text": "旧墙翻新"}],
        "style_scene_beats": [
            {"target_sec": 66.5, "frac": 0.84, "layout": {"mode": "paper_outline_pip", "panel_title": "三步交付验收"}},
        ],
        "style_callouts": [],
    }
    r_neg = validate_director_plan_universal(neg_plan)
    r_pos = validate_director_plan_universal(pos_plan)
    ok = (not r_neg["ok"]) and any("夫妻店" in e or "01/02/03" in e for e in r_neg["errors"]) and r_pos["ok"]
    return CheckResult(
        "T1-G2-03", 1, "G2", "零第8格通用黑卡注入检测",
        "PASS" if ok else "FAIL",
        f"neg_errs={len(r_neg['errors'])} pos_errs={len(r_pos['errors'])}",
        "neg>=1, pos=0",
        "成功拦截第8格黑卡注入并放行原生节拍",
    )


def t1_g2_04_gbro_10_style_prompt_contract(ctx: SuiteContext) -> CheckResult:
    refs_dir = REPO_ROOT / "short-video-studio" / "subskills" / "gbro-cover-design" / "references"
    ref_files = [list(refs_dir.glob(f"{sid}-*.md")) for sid in GBRO_STYLE_IDS]
    all_10_exist = all(len(matches) == 1 for matches in ref_files)

    bad_s03 = "深色背景，图1人物指向「今日招牌」标题，全息投影效果。"
    bad_s08 = "3:4 竖版构图\n四边保留 10% 安全区。\n图1正脸直视镜头，标题「门店升级」。"
    good_s03 = "3:4 竖版构图\n所有关键元素距四边保留约 10% 安全区。\n浅色干净背景，图2菜品主视觉占画面约 65%，图1人物做引导手势，超大粗体标题「地道虾饺皇」。"
    good_s08 = "3:4 竖版构图\n所有关键元素距四边保留约 10% 安全区。\n深色空间感背景，图1人物背影背对镜头望向远方亮光，白色大字标题「十年一店」。"

    ok = (
        all_10_exist
        and not validate_gbro_prompt(bad_s03, "style-03", has_fig1=True, fig2_refs=[])["ok"]
        and not validate_gbro_prompt(bad_s08, "style-08", has_fig1=True, fig2_refs=[])["ok"]
        and validate_gbro_prompt(good_s03, "style-03", has_fig1=True, fig2_refs=["dish.png"])["ok"]
        and validate_gbro_prompt(good_s08, "style-08", has_fig1=True, fig2_refs=[])["ok"]
    )
    return CheckResult(
        "T1-G2-04", 1, "G2", "gbro-cover-design 10套风格与提示词契约",
        "PASS" if ok else "FAIL",
        f"refs_10={sum(len(m)==1 for m in ref_files)}/10",
        "10/10 refs & prompt rules PASS",
        "10 套风格参考模板齐全，style-03 强制图2 与 style-08 背影规则校验准确",
    )


def t1_g2_05_gbro_handoff_multiref(ctx: SuiteContext) -> CheckResult:
    with tempfile.TemporaryDirectory() as td:
        tdir = Path(td)
        c34 = tdir / "09_cover_3x4.jpg"
        cf = tdir / "09_cover.jpg"
        Image.new("RGB", (1080, 1440), (30, 30, 35)).save(c34)
        Image.new("RGB", (1080, 1920), (20, 20, 25)).save(cf)
        neg_h = {"recommended_style_id": "style-01", "aspect_ratio": "9:16", "figure_1_face_ref": {}, "figure_2_content_refs": [], "prompt_text": "bad"}
        pos_h = {
            "recommended_style_id": "style-03",
            "aspect_ratio": "3:4",
            "figure_1_face_ref": {"path": "09_host_face_ref.png"},
            "figure_2_content_refs": [{"path": "09_content_ref_01.png"}],
            "prompt_text": "3:4 竖版构图\n保留 10% 安全区。\n图2烧鹅特写居中，图1人物在侧边引导，标题「老广古法烧鹅」。",
        }
        r_neg = validate_gbro_handoff_and_covers(neg_h, c34, cf)
        r_pos = validate_gbro_handoff_and_covers(pos_h, c34, cf)
        ok = (not r_neg["ok"]) and r_pos["ok"]
    return CheckResult(
        "T1-G2-05", 1, "G2", "09_gbro_cover_handoff 多图契约检测",
        "PASS" if ok else "FAIL",
        f"neg_ok={r_neg['ok']} pos_ok={r_pos['ok']}",
        "neg=FAIL, pos=PASS",
        "图1人脸+图2实拍内容+3:4画幅校验通过" if ok else str(r_pos["errors"]),
    )


def t1_g2_06_cover_geometry_and_uniqueness(ctx: SuiteContext) -> CheckResult:
    with tempfile.TemporaryDirectory() as td:
        tdir = Path(td)
        bad_sz = tdir / "bad_3x4.jpg"
        c_full = tdir / "09_cover.jpg"
        dish_a1 = tdir / "dish_a1.jpg"
        dish_a2 = tdir / "dish_a2.jpg"
        dish_b = tdir / "dish_b.jpg"

        Image.new("RGB", (720, 960), (40, 40, 40)).save(bad_sz)
        Image.new("RGB", (1080, 1920), (20, 20, 20)).save(c_full)

        # 生成两张明显不同菜品几何纹理的 1080x1440 封面图，以及一张重复图
        im_a = Image.new("RGB", (1080, 1440), (25, 25, 30))
        da = ImageDraw.Draw(im_a)
        da.ellipse((150, 450, 930, 1180), fill=(210, 110, 35))
        da.rectangle((100, 140, 980, 340), fill=(250, 220, 60))
        im_a.save(dish_a1)
        im_a.save(dish_a2)

        im_b = Image.new("RGB", (1080, 1440), (235, 238, 242))
        db = ImageDraw.Draw(im_b)
        db.rectangle((120, 560, 960, 1300), fill=(45, 130, 90))
        db.ellipse((680, 180, 960, 460), fill=(220, 60, 50))
        im_b.save(dish_b)

        sz_bad_ok = Image.open(bad_sz).size == (1080, 1440)
        dist_same = hamming_distance(image_dhash(dish_a1), image_dhash(dish_a2))
        dist_diff = hamming_distance(image_dhash(dish_a1), image_dhash(dish_b))
        ok = (not sz_bad_ok) and (dist_same <= 2) and (dist_diff >= 8)
    return CheckResult(
        "T1-G2-06", 1, "G2", "封面 1080x1440/1920 尺寸与感知哈希唯一性",
        "PASS" if ok else "FAIL",
        f"bad_sz={sz_bad_ok} same_dist={dist_same} diff_dist={dist_diff}",
        "bad_sz=False, same<=2, diff>=8",
        "感知哈希与尺寸门禁敏感度校准通过",
    )


def t1_g3_01_ktv_orbs_detector(ctx: SuiteContext) -> CheckResult:
    # 构造合成 KTV 光球负样本（移动橙红光球）vs 洁净微动效正样本
    neg_frames: List[np.ndarray] = []
    pos_frames: List[np.ndarray] = []
    yy, xx = np.mgrid[0:CANVAS_H, 0:CANVAS_W]
    for step, (ox, oy) in enumerate(((260, 720), (820, 1080))):
        base_neg = np.full((CANVAS_H, CANVAS_W, 3), (22, 24, 31), dtype=np.float32)
        dist2 = ((xx - ox) ** 2 + (yy - oy) ** 2) / (260.0 ** 2)
        glow = np.exp(-dist2)
        base_neg[:, :, 0] = np.clip(base_neg[:, :, 0] + glow * 130.0, 0, 255)
        base_neg[:, :, 1] = np.clip(base_neg[:, :, 1] + glow * 65.0, 0, 255)
        neg_frames.append(base_neg.astype(np.uint8))

        base_pos = np.full((CANVAS_H, CANVAS_W, 3), (22, 24, 31), dtype=np.float32)
        # 洁净局部进度条微动效（中性灰白，不污染背景色度）
        bar_w = 180 + step * 320
        base_pos[1340:1360, 120:120 + bar_w, :] = (235, 238, 242)
        pos_frames.append(base_pos.astype(np.uint8))

    r_neg = analyze_ktv_orbs_pure_bg([(0.5, neg_frames[0]), (1.5, neg_frames[1])], stage_html_text='<div id="amb-orb-1"></div>')
    r_pos = analyze_ktv_orbs_pure_bg([(0.5, pos_frames[0]), (1.5, pos_frames[1])], stage_html_text='<div id="clean-micro"></div>')
    ok = (not r_neg["orbs_ok"]) and r_pos["orbs_ok"]
    if ctx.mode in ("pipeline", "full"):
        stage_p = REPO_ROOT / "short-video-studio" / "assets" / "motion_templates" / "stage.html"
        stage_txt = stage_p.read_text(encoding="utf-8")
        dom_clean = not any(k in stage_txt for k in ("amb-orb-1", "amb-orb-2", "amb-sweep"))
        ok = ok and dom_clean
        return CheckResult(
            "T1-G3-01", 1, "G3", "KTV 光球清理与色度漂移检测",
            "PASS" if ok else "FAIL",
            f"neg_orbs={r_neg['orbs_ok']} pos_orbs={r_pos['orbs_ok']} stage_dom_clean={dom_clean}",
            "neg=FAIL, pos=PASS, dom_clean=True",
            f"neg_chroma_p95={r_neg['chroma_p95']} pos_chroma_p95={r_pos['chroma_p95']}",
        )
    return CheckResult(
        "T1-G3-01", 1, "G3", "KTV 光球清理与色度漂移检测",
        "PASS" if ok else "FAIL",
        f"neg_chroma_p95={r_neg['chroma_p95']} neg_rb={r_neg['temporal_rb_p95']} pos_chroma_p95={r_pos['chroma_p95']}",
        "neg=FAIL(>16), pos=PASS(<=16)",
        "像素色度/R-B 时域漂移检测与 DOM 规则校准通过",
    )


def t1_g3_02_no_visual_freeze_detector(ctx: SuiteContext) -> CheckResult:
    static_f = np.full((CANVAS_H, CANVAS_W, 3), (24, 26, 33), dtype=np.uint8)
    neg_seq = [(i * 0.5, static_f) for i in range(9)]  # 4.0s 全静止
    pos_seq = []
    for i in range(9):
        fr = static_f.copy()
        # 切换全屏中区卡片高亮位置 (>10% 像素变化，确保 270x480 p95>=5.0)
        y0 = 420 if (i % 2 == 0) else 760
        fr[y0:y0 + 280, 120:960, :] = (240, 240, 245)
        pos_seq.append((i * 0.5, fr))

    r_neg = analyze_mp4_visual_freeze(neg_seq)
    r_pos = analyze_mp4_visual_freeze(pos_seq)
    ok = (not r_neg["freeze_ok"]) and (r_neg["max_static_sec"] > 3.0) and r_pos["freeze_ok"] and (r_pos["max_static_sec"] <= 3.0)
    return CheckResult(
        "T1-G3-02", 1, "G3", "bb_no_visual_freeze 冻帧微动效检测",
        "PASS" if ok else "FAIL",
        f"neg_static={r_neg['max_static_sec']}s pos_static={r_pos['max_static_sec']}s",
        "neg>3.0s(FAIL), pos<=3.0s(PASS)",
        "270x480 掩膜冻帧检测与正负校准通过",
    )


def t1_g3_03_topbar_clearance_and_circle_sync(ctx: SuiteContext) -> CheckResult:
    neg_pkg = {
        "visual_elements": [
            {"kind": "persistent_top_bar", "start_frame": 0, "end_frame": 300, "final_bbox": {"x": 220, "y": 168, "w": 640, "h": 150}},
            {"kind": "stamp_tag", "start_frame": 30, "end_frame": 120, "final_bbox": {"x": 220, "y": 240, "w": 300, "h": 90}},
        ]
    }
    # 故意缺省 circle_cy：真实复现 build_panel_job_spec(style_06 -> 340) vs 默认 pip fallback(220) 的失步缺陷
    neg_edl = {"clips": [{"pip_layout": {"mode": "concept_diagram_pip", "circle_cx": 888, "circle_r": 92}}]}
    neg_spec = motion_html_renderer.build_panel_job_spec(neg_edl["clips"][0]["pip_layout"], style_id="style_06", dur_sec=3.0)

    pos_pkg = {
        "visual_elements": [
            {"kind": "persistent_top_bar", "start_frame": 0, "end_frame": 300, "final_bbox": {"x": 220, "y": 168, "w": 640, "h": 150}},
            {"kind": "stamp_tag", "start_frame": 30, "end_frame": 120, "final_bbox": {"x": 220, "y": 380, "w": 300, "h": 90}},
        ]
    }
    pos_edl = {"clips": [{"pip_layout": {"mode": "concept_diagram_pip", "circle_cx": 888, "circle_cy": 450, "circle_r": 92}}]}
    pos_spec = motion_html_renderer.build_panel_job_spec(pos_edl["clips"][0]["pip_layout"], style_id="style_06", dur_sec=3.0)

    r_neg = analyze_topbar_and_circle_sync(neg_pkg, neg_edl, panel_spec=neg_spec)
    r_pos = analyze_topbar_and_circle_sync(pos_pkg, pos_edl, panel_spec=pos_spec)
    has_desync_err = any("desync" in e for e in r_neg["errors"])
    has_topbar_err = any("top_bar_bottom" in e for e in r_neg["errors"])
    ok = (not r_neg["ok"]) and has_desync_err and has_topbar_err and r_pos["ok"]
    return CheckResult(
        "T1-G3-03", 1, "G3", "top_bar 安全边距与圆窗同步检测",
        "PASS" if ok else "FAIL",
        f"neg_errs={len(r_neg['errors'])} desync={has_desync_err} topbar={has_topbar_err} pos_errs={len(r_pos['errors'])}",
        "neg>=2(desync+topbar), pos=0",
        "顶栏碰撞与 missing-circle_cy 失步路径均被精准触发",
    )


def t1_g3_04_style15_paper_continuity_and_t0(ctx: SuiteContext) -> CheckResult:
    with tempfile.TemporaryDirectory() as td:
        tdir = Path(td)
        # 负样本：t0 发白无字 + 下半部黑断层 #10121A
        neg_t0 = np.full((CANVAS_H, CANVAS_W, 3), (242, 244, 247), dtype=np.uint8)
        neg_t0[1245:, :, :] = (16, 18, 26)
        neg_mid = neg_t0.copy()
        neg_t0_p = tdir / "neg_t0.png"
        Image.fromarray(neg_t0).save(neg_t0_p)

        # 正样本：全屏通体浅纸底 + t0 立即可见深墨水中文标题（真实 Apple Vision OCR）
        pos_t0_p = _render_chinese_card(
            tdir / "pos_t0.png",
            "核心交付三步法",
            bg=(242, 244, 247),
            fg=(24, 26, 32),
            pos=(120, 460),
            font_size=110,
            font_loader=ctx.font,
        )
        pos_t0 = np.asarray(Image.open(pos_t0_p).convert("RGB"))
        pos_mid = pos_t0.copy()
        vis = vision_inspector.inspect_images([neg_t0_p, pos_t0_p])
        r_neg = analyze_style15_paper_frame(neg_t0, neg_mid, vis.get(str(neg_t0_p)))
        r_pos = analyze_style15_paper_frame(pos_t0, pos_mid, vis.get(str(pos_t0_p)))
        ok = (not r_neg["ok"]) and r_pos["ok"]
    live_note = ""
    if ctx.mode in ("pipeline", "full"):
        stage_txt = (REPO_ROOT / "short-video-studio" / "assets" / "motion_templates" / "stage.html").read_text(encoding="utf-8")
        # 校验 paper_outline_whiteboard 与 magazine_review_whiteboard 模板段不再挂载 .bottom-sub-band 黑断层
        band_hits: List[str] = []
        for tpl_name in ("paper_outline_whiteboard", "magazine_review_whiteboard"):
            idx = stage_txt.find(f'kind === "{tpl_name}"')
            if idx != -1:
                seg = stage_txt[idx:idx + 3500]
                if "bottom-sub-band" in seg:
                    band_hits.append(tpl_name)
        ok = ok and (len(band_hits) == 0)
        live_note = f" | stage_paper_band_hits={band_hits}"
    return CheckResult(
        "T1-G3-04", 1, "G3", "style_15 通体纸底连续性与 t=0 首帧可见性",
        "PASS" if ok else "FAIL",
        f"neg_bot={r_neg['bot_p75_mid']}/ink={r_neg['dark_ink_pct_t0']}% pos_bot={r_pos['bot_p75_mid']}/ink={r_pos['dark_ink_pct_t0']}%/ocr={r_pos['ocr_lines_t0']}{live_note}",
        "neg=FAIL, pos=PASS(bot>=175, ink>=1.5%, ocr>=1)",
        "Apple Vision + 纸底亮度分布双检校准通过" if ok else f"纸底连续性或 stage.html .bottom-sub-band 未清零{live_note}",
    )


def t1_g3_05_style04_full_giant_text(ctx: SuiteContext) -> CheckResult:
    with tempfile.TemporaryDirectory() as td:
        tdir = Path(td)
        p_neg = _render_chinese_card(tdir / "s04_neg.png", "前提搬不", pos=(100, 220), font_size=140, font_loader=ctx.font)
        p_pos = _render_chinese_card(tdir / "s04_pos.png", "前提搬不过来", pos=(100, 220), font_size=140, font_loader=ctx.font)
        vis = vision_inspector.inspect_images([p_neg, p_pos])
        r_neg = analyze_style04_giant_text("前提搬不过来", vis[str(p_neg)])
        r_pos = analyze_style04_giant_text("前提搬不过来", vis[str(p_pos)])
        ok = (not r_neg["ok"]) and r_pos["ok"]
    return CheckResult(
        "T1-G3-05", 1, "G3", "style_04 巨字完整度 Apple Vision OCR 检测",
        "PASS" if ok else "FAIL",
        f"neg_ratio={r_neg['match_ratio']}('{r_neg['ocr_joined']}') pos_ratio={r_pos['match_ratio']}('{r_pos['ocr_joined']}')",
        "neg<0.85(FAIL), pos>=0.85(PASS)",
        "[:4] 截断缺陷被 Apple Vision 精确捕获",
    )


def t1_g3_06_style10_16_portrait_top_layering(ctx: SuiteContext) -> CheckResult:
    face_box = {"x": 0.30, "y": 0.28, "w": 0.40, "h": 0.25}
    neg_pkg = {"visual_elements": [{"kind": "center_burst_title", "z_layer": "fg"}]}
    neg_edl = {"clips": [{"pip_layout": {"mode": "default"}}]}
    neg_img = np.full((CANVAS_H, CANVAS_W, 3), (30, 30, 35), dtype=np.uint8)
    # 负样本：白字直接盖过人脸正中
    neg_img[660:780, 320:760, :] = (250, 250, 250)

    pos_pkg = {"visual_elements": [{"kind": "center_burst_title", "z_layer": "behind_person"}]}
    pos_edl = {"clips": [{"pip_layout": {"mode": "self_matting_behind_text"}}]}
    pos_img = np.full((CANVAS_H, CANVAS_W, 3), (30, 30, 35), dtype=np.uint8)
    # 正样本：大字在顶区（低于发冠线之上），头脸区域是完整抠像肤色（无亮白前景字穿帮）
    pos_img[200:310, 220:860, :] = (250, 250, 250)
    pos_img[540:1020, 324:756, :] = (195, 150, 125)

    r_neg = analyze_style10_16_layering(neg_pkg, neg_edl, neg_img, face_bbox_norm=face_box, fg_text_bboxes_norm=[{"x": 0.28, "y": 0.34, "w": 0.44, "h": 0.08}])
    r_pos = analyze_style10_16_layering(pos_pkg, pos_edl, pos_img, face_bbox_norm=face_box, fg_text_bboxes_norm=[{"x": 0.20, "y": 0.10, "w": 0.60, "h": 0.06}])
    ok = (not r_neg["ok"]) and r_pos["ok"]
    return CheckResult(
        "T1-G3-06", 1, "G3", "style_10/16 人像抠像置顶分层检测",
        "PASS" if ok else "FAIL",
        f"neg_occ={r_neg['unoccluded_frac']} pos_occ={r_pos['unoccluded_frac']}",
        "neg=FAIL, pos=PASS(iou<=0.02 & occ<=0.15)",
        "人像置顶与遮挡率检测准确",
    )


def t1_g3_07_style07_wcag_and_style06_safezone(ctx: SuiteContext) -> CheckResult:
    with tempfile.TemporaryDirectory() as td:
        tdir = Path(td)
        p_neg7 = _render_chinese_card(tdir / "s07_neg.png", "盲目扩张必亏", bg=(20, 21, 27), fg=(201, 42, 42), pos=(160, 900), font_size=90, font_loader=ctx.font)
        p_pos7 = _render_chinese_card(tdir / "s07_pos.png", "盲目扩张必亏", bg=(20, 21, 27), fg=(255, 135, 135), pos=(160, 900), font_size=90, font_loader=ctx.font)
        r_neg7 = analyze_style07_wcag_frame_and_hex("#C92A2A", "#14151B", np.asarray(Image.open(p_neg7).convert("RGB")))
        r_pos7 = analyze_style07_wcag_frame_and_hex("#FF8787", "#14151B", np.asarray(Image.open(p_pos7).convert("RGB")))

        neg6_pkg = {"visual_elements": [{"kind": "kinetic_callout", "callout_variant": "s06_vertical_poem", "final_bbox": {"x": 850, "y": 360, "w": 120, "h": 540}}]}
        pos6_pkg = {"visual_elements": [{"kind": "kinetic_callout", "callout_variant": "s06_vertical_poem", "final_bbox": {"x": 750, "y": 360, "w": 120, "h": 540}}]}
        neg6_img = np.full((CANVAS_H, CANVAS_W, 3), (20, 21, 27), dtype=np.uint8)
        neg6_img[380:920, 890:960, :] = (244, 226, 168)
        pos6_img = np.full((CANVAS_H, CANVAS_W, 3), (20, 21, 27), dtype=np.uint8)
        pos6_img[380:920, 770:840, :] = (244, 226, 168)
        r_neg6 = analyze_style06_safe_zone(neg6_pkg, neg6_img)
        r_pos6 = analyze_style06_safe_zone(pos6_pkg, pos6_img)

    ok = (not r_neg7["ok"]) and r_pos7["ok"] and (not r_neg6["ok"]) and r_pos6["ok"]
    live_detail = ""
    if ctx.mode in ("pipeline", "full"):
        s07_truth = style_visual_truth.get_visual_truth("style_07")
        s07_bad_hexes: List[str] = []
        for b in (s07_truth.get("scene_beats") or []):
            lay = b.get("layout") or {}
            bg_hx = str(lay.get("bg_hex") or "")
            if bg_hx:
                for k in ("accent_hex", "sub_hex"):
                    fg_hx = str(lay.get(k) or "")
                    if fg_hx and not analyze_style07_wcag_frame_and_hex(fg_hx, bg_hx)["ok"]:
                        s07_bad_hexes.append(f"{k}:{fg_hx}@{bg_hx}")
        stage_txt = (REPO_ROOT / "short-video-studio" / "assets" / "motion_templates" / "stage.html").read_text(encoding="utf-8")
        s06_idx = stage_txt.find('variant === "s06_vertical_poem"')
        s06_safe_ok = True
        if s06_idx != -1:
            s06_seg = stage_txt[s06_idx:s06_idx + 500]
            m_right = re.search(r"right\s*:\s*(\d+)px", s06_seg)
            if m_right and (CANVAS_W - int(m_right.group(1))) > SAFE_X_MAX:
                s06_safe_ok = False
        ok = ok and (not s07_bad_hexes) and s06_safe_ok
        live_detail = f" | live_s07_bad={s07_bad_hexes} live_s06_safe={s06_safe_ok}"
    return CheckResult(
        "T1-G3-07", 1, "G3", "style_07 sRGB WCAG>=4.5 与 style_06 右栏安全区",
        "PASS" if ok else "FAIL",
        f"s07_neg={r_neg7['frame_wcag']} s07_pos={r_pos7['frame_wcag']} s06_neg={r_neg6['right_rail_frac']} s06_pos={r_pos6['right_rail_frac']}{live_detail}",
        "s07>=4.5, s06_rail<=0.01",
        "弥补 common.py lum>=0.35 盲区并严查 x<=880" if ok else f"真仓 style_07 WCAG 或 style_06 右栏越界未过{live_detail}",
    )


def t1_g3_08_style03_21_smooth_matting(ctx: SuiteContext) -> CheckResult:
    # 负样本：二值锯齿掩膜（+-8px 随机高频锯齿 + 0/255 硬阈值）
    rng = np.random.default_rng(42)
    h, w = 480, 480
    yy, xx = np.mgrid[0:h, 0:w]
    jitter = rng.integers(-8, 9, size=h)[:, None]
    neg_alpha = np.where(((xx - (240 + jitter)) ** 2 / (120.0 ** 2) + (yy - 240) ** 2 / (170.0 ** 2)) <= 1.0, 255, 0).astype(np.uint8)

    # 正样本：平滑高斯羽化椭圆人像（4px 抗锯齿过渡）
    clean_bin = np.where(((xx - 240) ** 2 / (120.0 ** 2) + (yy - 240) ** 2 / (170.0 ** 2)) <= 1.0, 255.0, 0.0).astype(np.float32)
    pos_alpha = np.asarray(Image.fromarray(clean_bin.astype(np.uint8)).filter(ImageFilter.GaussianBlur(radius=2.2)))

    r_neg = analyze_portrait_matte_smoothness(neg_alpha)
    r_pos = analyze_portrait_matte_smoothness(pos_alpha)
    ok = (not r_neg["ok"]) and r_pos["ok"]
    live_note = ""
    if ctx.mode in ("pipeline", "full"):
        rmp4_txt = (SCRIPTS_ROOT / "render_mp4.py").read_text(encoding="utf-8")
        pm_txt = (SCRIPTS_ROOT / "person_matting.py").read_text(encoding="utf-8")
        rmp4_accurate = ('quality="accurate"' in rmp4_txt) or ('quality = "accurate"' in rmp4_txt)
        pm_accurate = ('quality: str = "accurate"' in pm_txt)
        no_forced_torn = ("torn_paper=True" not in rmp4_txt) and ("torn_paper: bool = True" not in pm_txt)
        ok = ok and rmp4_accurate and pm_accurate and no_forced_torn
        live_note = f" | rmp4_acc={rmp4_accurate} pm_acc={pm_accurate} no_torn={no_forced_torn}"
    return CheckResult(
        "T1-G3-08", 1, "G3", "style_03/21 抠像边缘羽化平滑度检测",
        "PASS" if ok else "FAIL",
        f"neg_aa={r_neg['boundary_aa_fraction']}/jag={r_neg['micro_jag']} pos_aa={r_pos['boundary_aa_fraction']}/jag={r_pos['micro_jag']}{live_note}",
        "aa>=0.20, micro_jag<=0.08",
        "高频锯齿与硬阈值切边被精准拦截" if ok else f"真仓抠像默认质量非 accurate 或未禁撕纸边{live_note}",
    )


def t1_g4_01_edl_multiclip_panel_span(ctx: SuiteContext) -> CheckResult:
    neg_edl = {
        "total_duration_sec": 20.0,
        "clips": [
            {"clip_id": "c1", "duration_sec": 2.0, "pip_layout": {"mode": "review_whiteboard_pip"}},
            {"clip_id": "c2", "duration_sec": 2.2, "pip_layout": {"mode": "review_whiteboard_pip"}},
        ],
    }
    neg_flat_off = {
        "total_duration_sec": 20.0,
        "clips": [
            {"clip_id": "c1", "duration_sec": 2.0, "pip_layout": {"mode": "review_whiteboard_pip", "v11_panel_group_id": "pg_1", "v11_panel_group_dur": 6.0, "v11_panel_offset_sec": 0.0}},
            {"clip_id": "c2", "duration_sec": 2.0, "pip_layout": {"mode": "review_whiteboard_pip", "v11_panel_group_id": "pg_1", "v11_panel_group_dur": 6.0, "v11_panel_offset_sec": 0.0}},
            {"clip_id": "c3", "duration_sec": 2.0, "pip_layout": {"mode": "review_whiteboard_pip", "v11_panel_group_id": "pg_1", "v11_panel_group_dur": 6.0, "v11_panel_offset_sec": 0.0}},
        ],
    }
    pos_edl = {
        "total_duration_sec": 20.0,
        "clips": [
            {"clip_id": "c1", "duration_sec": 2.0, "pip_layout": {"mode": "review_whiteboard_pip", "v11_panel_group_id": "pg_1", "v11_panel_group_dur": 4.2, "v11_panel_offset_sec": 0.0}},
            {"clip_id": "c2", "duration_sec": 2.2, "pip_layout": {"mode": "review_whiteboard_pip", "v11_panel_group_id": "pg_1", "v11_panel_group_dur": 4.2, "v11_panel_offset_sec": 2.0}},
        ],
    }
    white = [np.full((240, 180, 3), 235, dtype=np.uint8) for _ in range(4)] + [np.full((240, 180, 3), 25, dtype=np.uint8) for _ in range(16)]
    r_neg = analyze_style24_edl_and_frames(neg_edl, white)
    r_flat = analyze_style24_edl_and_frames(neg_flat_off, white)
    r_pos = analyze_style24_edl_and_frames(pos_edl, white)
    ok = (not r_neg["ok"]) and (not r_flat["ok"]) and any("non_monotonic_offset" in e for e in r_flat["group_errors"]) and r_pos["ok"]
    return CheckResult(
        "T1-G4-01", 1, "G4", "04_edl 跨镜头连续面板分组检测",
        "PASS" if ok else "FAIL",
        f"neg_errs={r_neg['group_errors']} flat_errs={r_flat['group_errors']} pos_ok={r_pos['ok']}",
        "neg=FAIL, flat_0=FAIL, pos=PASS(dur>=3.0 & strictly increasing offset)",
        "v11_panel_group_id / group_dur / 严格单调 offset 校验通过",
    )


def t1_g4_02_sto_style24_whiteboard_ratio(ctx: SuiteContext) -> CheckResult:
    neg_edl = {"total_duration_sec": 58.0, "clips": [{"duration_sec": 3.4, "pip_layout": {"mode": "review_whiteboard_pip", "v11_panel_group_id": "g1", "v11_panel_group_dur": 3.4, "v11_panel_offset_sec": 0.0}}]}
    pos_edl = {"total_duration_sec": 58.0, "clips": [{"duration_sec": 12.0, "pip_layout": {"mode": "review_whiteboard_pip", "v11_panel_group_id": "g1", "v11_panel_group_dur": 12.0, "v11_panel_offset_sec": 0.0}}]}
    dark = np.full((240, 180, 3), 24, dtype=np.uint8)
    paper = np.full((240, 180, 3), 236, dtype=np.uint8)
    r_neg = analyze_style24_edl_and_frames(neg_edl, [dark] * 20)
    r_pos = analyze_style24_edl_and_frames(pos_edl, [paper] * 4 + [dark] * 16)
    ok = (not r_neg["ok"]) and r_pos["ok"]
    return CheckResult(
        "T1-G4-02", 1, "G4", "STO style_24 杂志纸底白板占比检测",
        "PASS" if ok else "FAIL",
        f"neg_edl={r_neg['edl_ratio']}/fr={r_neg['frame_ratio']} pos_edl={r_pos['edl_ratio']}/fr={r_pos['frame_ratio']}",
        "edl_ratio>=0.15 & frame_ratio>=0.15",
        "白板镜头与成片帧占比双达标校验通过",
    )


def t1_g4_03_cross_style_frame8_uniqueness(ctx: SuiteContext) -> CheckResult:
    with tempfile.TemporaryDirectory() as td:
        tdir = Path(td)
        # 负样本：4 个不相关风格共享同一 01/02/03 黑底卡
        neg_imgs: Dict[str, np.ndarray] = {}
        neg_paths: List[Path] = []
        pos_imgs: Dict[str, np.ndarray] = {}
        pos_paths: List[Path] = []
        for idx, sid in enumerate(("style_03", "style_07", "style_15", "style_24")):
            pn = _render_chinese_card(tdir / f"n_{sid}.png", "01/02/03 夫妻店到雇人断层", font_size=70, font_loader=ctx.font)
            neg_paths.append(pn)
            neg_imgs[sid] = np.asarray(Image.open(pn).convert("RGB"))

            unique_txt = ("核心复盘金句", "高对比警示点", "纸底总结清单", "杂志白板复盘")[idx]
            bg_c = (242, 244, 247) if sid in ("style_15", "style_24") else (20, 21, 27)
            fg_c = (24, 26, 32) if sid in ("style_15", "style_24") else (255, 225, 77)
            pp = _render_chinese_card(tdir / f"p_{sid}.png", unique_txt, bg=bg_c, fg=fg_c, font_size=80, font_loader=ctx.font)
            pos_paths.append(pp)
            pos_imgs[sid] = np.asarray(Image.open(pp).convert("RGB"))

        vis = vision_inspector.inspect_images(neg_paths + pos_paths)
        neg_ocr = {sid: vis[str(tdir / f"n_{sid}.png")] for sid in neg_imgs}
        pos_ocr = {sid: vis[str(tdir / f"p_{sid}.png")] for sid in pos_imgs}
        r_neg = analyze_cross_style_frame8_uniqueness(neg_imgs, neg_ocr)
        r_pos = analyze_cross_style_frame8_uniqueness(pos_imgs, pos_ocr)
        ok = (not r_neg["ok"]) and r_pos["ok"]
    return CheckResult(
        "T1-G4-03", 1, "G4", "全24套第8格唯一性与零串味检测",
        "PASS" if ok else "FAIL",
        f"neg_hits={len(r_neg['marker_hits'])} shared={len(r_neg['shared_dark_cards'])} pos_ok={r_pos['ok']}",
        "neg=FAIL, pos=PASS",
        "跨风格共享黑底概念卡精准拦截",
    )


def t1_g4_04_native_style_dna_preservation(ctx: SuiteContext) -> CheckResult:
    # 黑盒检查 style_visual_truth 24 套风格 DNA 互异且保留特征模式
    truths = {sid: style_visual_truth.get_visual_truth(sid) for sid in ALL_24_STYLES}
    families = {sid: str(tr.get("family") or "") for sid, tr in truths.items()}
    s09_modes = {b["layout"]["mode"] for b in (truths["style_09"].get("scene_beats") or [])}
    s15_modes = {b["layout"]["mode"] for b in (truths["style_15"].get("scene_beats") or [])}
    s16_modes = {b["layout"]["mode"] for b in (truths["style_16"].get("scene_beats") or [])}
    s24_modes = {b["layout"]["mode"] for b in (truths["style_24"].get("scene_beats") or [])}
    ok = (
        len(truths) == 24
        and len(set(families.values())) >= 7
        and ("paper_outline_pip" in s15_modes)
        and ("dark_typewriter_card_pip" in s16_modes)
        and ("review_whiteboard_pip" in s24_modes)
        and bool(s09_modes)
    )
    return CheckResult(
        "T1-G4-04", 1, "G4", "原生风格基因保留与非同质化检测",
        "PASS" if ok else "FAIL",
        f"styles={len(truths)} families={len(set(families.values()))}",
        "24 styles, distinct DNA preserved",
        "style_09/15/16/24 等原生风格模式特征完整",
    )


def t1_g4_05_zero_dual_callout_collision(ctx: SuiteContext) -> CheckResult:
    neg_pkg = {
        "visual_elements": [
            {"id": "c1", "kind": "kinetic_callout", "start_frame": 90, "end_frame": 160, "final_bbox": {"x": 120, "y": 900, "w": 800, "h": 140}},
            {"id": "b1", "kind": "center_burst_title", "start_frame": 100, "end_frame": 170, "final_bbox": {"x": 140, "y": 910, "w": 780, "h": 140}},
        ]
    }
    pos_pkg = {
        "visual_elements": [
            {"id": "c1", "kind": "kinetic_callout", "start_frame": 90, "end_frame": 140, "final_bbox": {"x": 120, "y": 900, "w": 800, "h": 140}},
            {"id": "b1", "kind": "center_burst_title", "start_frame": 180, "end_frame": 250, "final_bbox": {"x": 140, "y": 910, "w": 780, "h": 140}},
        ]
    }
    c_neg = check_dual_callout_overlap(neg_pkg)
    c_pos = check_dual_callout_overlap(pos_pkg)
    ok = (len(c_neg) == 1) and (len(c_pos) == 0)
    return CheckResult(
        "T1-G4-05", 1, "G4", "零双花字时空重叠检测",
        "PASS" if ok else "FAIL",
        f"neg_collisions={len(c_neg)} pos_collisions={len(c_pos)}",
        "neg=1, pos=0",
        "kinetic_callout 与 center_burst_title 重叠检测通过",
    )


def t1_g5_01_repo_zero_personal_paths(ctx: SuiteContext) -> CheckResult:
    draft_names = load_draft_names_from_forensic()
    files = _repo_scan_files(ctx.mode)
    priv_hits, _ = scan_files_for_leaks(files, draft_names=draft_names)
    ok = len(priv_hits) == 0
    return CheckResult(
        "T1-G5-01", 1, "G5", "生产源码与新增文件零个人路径扫描",
        "PASS" if ok else "FAIL",
        f"mode={ctx.mode} scanned={len(files)} hits={len(priv_hits)}",
        "0 hits",
        ", ".join(priv_hits[:5]) if priv_hits else "未检出个人家目录绝对路径泄漏",
    )


def t1_g5_02_repo_zero_draft_names(ctx: SuiteContext) -> CheckResult:
    draft_names = load_draft_names_from_forensic()
    files = _repo_scan_files(ctx.mode)
    _, draft_hits = scan_files_for_leaks(files, draft_names=draft_names)
    ok = bool(draft_names) and (len(draft_hits) == 0)
    return CheckResult(
        "T1-G5-02", 1, "G5", "生产源码与新增文件零本地草稿名扫描",
        "PASS" if ok else "FAIL",
        f"mode={ctx.mode} draft_dict={len(draft_names)} scanned={len(files)} hits={len(draft_hits)}",
        "0 hits (excl. forensic/)",
        ", ".join(draft_hits[:5]) if draft_hits else "未检出本地参考草稿名泄漏",
    )


def t1_g5_03_git_branch_and_no_teamwork_in_repo(ctx: SuiteContext) -> CheckResult:
    cur_branch = subprocess.check_output(["git", "branch", "--show-current"], cwd=str(REPO_ROOT), text=True).strip()
    teamwork_dir = REPO_ROOT / ".agents" / "teamwork"
    no_tw = not teamwork_dir.exists()
    # 验证 main 分支存在且未被当前工作区污染
    main_exists = subprocess.run(["git", "rev-parse", "--verify", "main"], cwd=str(REPO_ROOT), capture_output=True).returncode == 0
    ok = (cur_branch == EXPECTED_BRANCH) and no_tw and main_exists
    return CheckResult(
        "T1-G5-03", 1, "G5", "Git 分支隔离与仓库内零 .agents/teamwork 守卫",
        "PASS" if ok else "FAIL",
        f"branch={cur_branch} no_teamwork_in_repo={no_tw} main_exists={main_exists}",
        f"branch=={EXPECTED_BRANCH}, no_teamwork=True",
        "分支与元数据目录隔离合规",
    )


def t1_g5_04_symlink_and_mustkeep_integrity(ctx: SuiteContext) -> CheckResult:
    must_keep = [
        REPO_ROOT / "short-video-studio" / "scripts",
        REPO_ROOT / "short-video-studio" / "assets",
        REPO_ROOT / "tests",
        REPO_ROOT / "dev_tools",
        REPO_ROOT / "forensic" / "style_sources.json",
    ]
    missing = [str(p.relative_to(REPO_ROOT)) for p in must_keep if not p.exists()]
    broken_links = find_broken_symlinks([ctx.biz_root, ctx.sto_root] if ctx.mode == "full" else [])
    ok = (not missing) and (not broken_links)
    return CheckResult(
        "T1-G5-04", 1, "G5", "01_normalized 软链与核心资产完整性守卫",
        "PASS" if ok else "FAIL",
        f"missing={len(missing)} broken_symlinks={len(broken_links)}",
        "missing=0, broken_symlinks=0",
        "核心目录与软链均完整" if ok else f"missing={missing} broken={broken_links[:3]}",
    )


def t1_g5_05_blind_material_zero_leakage(ctx: SuiteContext) -> CheckResult:
    leak_markers = ("夫妻店到雇人断层", "牛哥", "岭南元素", "虾饺", "200多万")
    with tempfile.TemporaryDirectory() as td:
        tdir = Path(td)
        neg_art = tdir / "neg_plan.json"
        pos_art = tdir / "pos_plan.json"
        neg_art.write_text(json.dumps({"panel_title": "夫妻店到雇人断层", "sub": "岭南元素"}, ensure_ascii=False), encoding="utf-8")
        pos_art.write_text(json.dumps({"panel_title": "旧墙翻新三层工艺", "sub": "净味环保涂装"}, ensure_ascii=False), encoding="utf-8")
        neg_hits = [m for m in leak_markers if m in neg_art.read_text(encoding="utf-8")]
        pos_hits = [m for m in leak_markers if m in pos_art.read_text(encoding="utf-8")]
    ok = (len(neg_hits) >= 2) and (len(pos_hits) == 0)
    if ctx.mode == "full" and ctx.blind_dir:
        bpipe = ctx.blind_dir / "pipeline" if (ctx.blind_dir / "pipeline").is_dir() else ctx.blind_dir
        live_hits: List[str] = []
        for fname in ("03b_director_plan.json", "05_subtitles.json", "06_packaging.json", "09_gbro_cover_handoff.json"):
            fp = bpipe / fname
            if fp.is_file():
                txt = fp.read_text(encoding="utf-8", errors="ignore")
                for m in leak_markers:
                    if m in txt:
                        live_hits.append(f"{fname}:{m}")
        ok = ok and (len(live_hits) == 0)
        return CheckResult(
            "T1-G5-05", 1, "G5", "第3条未知盲测素材零 BIZ/STO 串味泄漏",
            "PASS" if ok else "FAIL",
            f"live_hits={len(live_hits)}",
            "live_hits=0",
            ", ".join(live_hits) if live_hits else "盲测产线零旧语料泄漏",
        )
    return CheckResult(
        "T1-G5-05", 1, "G5", "第3条未知盲测素材零 BIZ/STO 串味泄漏",
        "PASS" if ok else "FAIL",
        f"neg_hits={len(neg_hits)} pos_hits={len(pos_hits)}",
        "neg>=2, pos=0",
        "盲测串味泄漏检测器自校准通过",
    )


# ---------------------------------------------------------------------------
# Tier 2 (28 项)：G1(6) + G2(6) + G3(6) + G4(5) + G5(5)
# ---------------------------------------------------------------------------
def t2_g1_01_long_clause_18plus_wrap(ctx: SuiteContext) -> CheckResult:
    long_txt = "我们在扩张新店面之前必须把所有隐性人工与房租成本全部算清楚"
    segs = [s[2] for s in step5_subtitles.dp_segment_short_lines(long_txt, 608, font_size=62)]
    is_final = [i == len(segs) - 1 for i in range(len(segs))]
    dangling = detect_dangling_particles(segs, is_sentence_final=is_final)
    max_len = max((len(_clean_cjk(x)) for x in segs), default=0)
    # 校验负样本对比
    bad_segs = ["我们在扩张新店面之前必须把", "所有隐性人工与房租成本全部算清楚"]
    bad_hits = detect_dangling_particles(bad_segs, is_sentence_final=[False, True])
    ok = (len(segs) >= 2) and (max_len <= 14) and (len(bad_hits) == 1) and (len(dangling) == 0)
    return CheckResult(
        "T2-G1-01", 2, "G1", "18+字长句自动折行边界",
        "PASS" if ok else "FAIL",
        f"segs={len(segs)} max_len={max_len} dangling={len(dangling)} bad_caught={len(bad_hits)}",
        "segs>=2, max_len<=14, dangling=0, bad_caught=1",
        f"segs={segs}",
    )


def t2_g1_02_short_and_empty_input(ctx: SuiteContext) -> CheckResult:
    empty_segs = step5_subtitles.dp_segment_short_lines("", 608, font_size=62)
    punct_segs = step5_subtitles.dp_segment_short_lines("，。！？", 608, font_size=62)
    short_segs = [s[2] for s in step5_subtitles.dp_segment_short_lines("真的吗", 608, font_size=62)]
    ok = (len(empty_segs) == 0) and (len(punct_segs) == 0) and (len(short_segs) == 1) and (short_segs[0] == "真的吗")
    return CheckResult(
        "T2-G1-02", 2, "G1", "空串/纯标点与<=3字短句边界",
        "PASS" if ok else "FAIL",
        f"empty={len(empty_segs)} punct={len(punct_segs)} short={short_segs}",
        "empty=0, punct=0, short=['真的吗']",
        "极短与空输入无崩溃无碎片",
    )


def t2_g1_03_heavy_cantonese_particles(ctx: SuiteContext) -> CheckResult:
    neg = ["佢哋喺呢度搞咗", "好多有意思嘅", "新菜式啲味道真系正"]
    pos = ["佢哋喺呢度", "搞咗好多有意思嘅菜", "啲味道真系好正。"]
    neg_hits = detect_dangling_particles(neg, is_sentence_final=[False, False, True])
    pos_hits = detect_dangling_particles(pos, is_sentence_final=[False, False, True])
    ok = (len(neg_hits) >= 2) and (len(pos_hits) == 0)
    return CheckResult(
        "T2-G1-03", 2, "G1", "高密度粤语助词(嘅/咗/喺/啲/哋/嚟)边界",
        "PASS" if ok else "FAIL",
        f"neg_caught={len(neg_hits)} pos_err={len(pos_hits)}",
        "neg>=2, pos=0",
        "粤语高频助词边界检测准确",
    )


def t2_g1_04_cross_sentence_asr_split(ctx: SuiteContext) -> CheckResult:
    # 模拟真实跨句断句边界："我系牛哥我" + "花咗三百万"
    neg = ["我系牛哥我", "花咗三百万装修"]
    pos = ["我系牛哥", "我花咗三百万装修。"]
    # 检查单个无意义挂尾代词/跨句残片与正样本区别
    neg_split = (neg[0].endswith("我") and not neg[0].endswith("是我")) and neg[1].startswith("花咗")
    pos_hits = detect_dangling_particles(pos, is_sentence_final=[False, True]) + detect_aux_head_and_fragments(pos)
    ok = neg_split and (len(pos_hits) == 0)
    return CheckResult(
        "T2-G1-04", 2, "G1", "跨句 ASR 截断重连边界",
        "PASS" if ok else "FAIL",
        f"neg_split={neg_split} pos_hits={len(pos_hits)}",
        "neg_split=True, pos_hits=0",
        "跨句截断边界校验通过",
    )


def t2_g1_05_style02_list_gather_depth_rapid(ctx: SuiteContext) -> CheckResult:
    neg_items = [
        {"id": "k1", "group_id": "g1", "text_or_asset": "第一点先把毛利算清", "start_sec": 0.0, "end_sec": 1.4, "y_norm": -0.20, "z_layer": "fg"},
        {"id": "k2", "group_id": "g1", "text_or_asset": "算清再谈扩店", "start_sec": 0.7, "end_sec": 1.6, "y_norm": -0.20, "z_layer": "fg"},
    ]
    pos_items = [
        {"id": "k1", "group_id": "g1", "text_or_asset": "第一先把毛利算清", "start_sec": 0.0, "end_sec": 1.1, "y_norm": -0.18, "z_layer": "fg"},
        {"id": "k2", "group_id": "g1", "text_or_asset": "第二再谈门店扩张", "start_sec": 1.15, "end_sec": 2.3, "y_norm": -0.34, "z_layer": "fg"},
        {"id": "k3", "group_id": "g1", "text_or_asset": "第三锁死回本周期", "start_sec": 1.2, "end_sec": 2.3, "y_norm": -0.50, "z_layer": "fg"},
    ]
    r_neg = analyze_style02_kinetic_items(neg_items)
    r_pos = analyze_style02_kinetic_items(pos_items)
    ok = (not r_neg["ok"]) and r_pos["ok"]
    return CheckResult(
        "T2-G1-05", 2, "G1", "style_02 list_gather_depth 急促连句边界",
        "PASS" if ok else "FAIL",
        f"neg_ok={r_neg['ok']} pos_ok={r_pos['ok']}",
        "neg=FAIL, pos=PASS",
        "连句前缀复读与通道冲突检测通过",
    )


def t2_g1_06_step5_and_style02_cli_calibration(ctx: SuiteContext) -> CheckResult:
    # 实跑 step5_subtitles.py CLI 子进程（独立沙箱目录）并验证输出 JSON 契约
    with tempfile.TemporaryDirectory() as td:
        pdir = _make_audio_events_fixture(Path(td), [
            ("sent_001", "新邻舍专注旧房墙面翻新二十年", 0.0, 2.8),
            ("sent_002", "三天即可完工并且全程净味环保", 3.0, 5.8),
        ])
        res = subprocess.run(
            [ctx.py_bin, str(SCRIPTS_ROOT / "step5_subtitles.py"), "--pipeline-dir", str(pdir), "--track", "store"],
            capture_output=True, text=True, timeout=60,
        )
        sub_p = pdir / "05_subtitles.json"
        ok = (res.returncode == 0) and sub_p.is_file()
        entries_cnt = 0
        if ok:
            entries_cnt = len(_read_json(sub_p).get("entries") or [])
            ok = entries_cnt >= 2
    return CheckResult(
        "T2-G1-06", 2, "G1", "step5_subtitles.py 黑盒 CLI 沙箱执行",
        "PASS" if ok else "FAIL",
        f"rc={res.returncode} entries={entries_cnt}",
        "rc=0, entries>=2",
        "step5_subtitles.py --pipeline-dir 黑盒运行成功" if ok else (res.stderr or res.stdout)[-200:],
    )


def t2_g2_01_director_minimal_2_sentences(ctx: SuiteContext) -> CheckResult:
    with tempfile.TemporaryDirectory() as td:
        pdir = _make_audio_events_fixture(Path(td), [
            ("sent_001", "做门店最核心的是复购率", 0.0, 2.2),
            ("sent_002", "回头客多了自然赚钱", 2.4, 4.6),
        ])
        out_p = pdir / "03b_director_plan.json"
        res = subprocess.run(
            [ctx.py_bin, str(SCRIPTS_ROOT / "director_plan.py"), "fallback", "--pipeline-dir", str(pdir), "--style-id", "style_09", "--out", str(out_p)],
            capture_output=True, text=True, timeout=60,
        )
        ok = (res.returncode == 0) and out_p.is_file()
        acts_n = 0
        if ok:
            doc = _read_json(out_p)
            acts_n = len(doc.get("acts") or [])
            ok = acts_n >= 1 and director_plan.validate_director_plan(doc, _read_json(pdir / "03_audio_events.json")).get("ok", False)
    return CheckResult(
        "T2-G2-01", 2, "G2", "director_plan.py 极短 2 句边界 CLI",
        "PASS" if ok else "FAIL",
        f"rc={res.returncode} acts={acts_n}",
        "rc=0, acts>=1",
        "2句极简脚本动态规划无报错" if ok else (res.stderr or res.stdout)[-200:],
    )


def t2_g2_02_director_long_120s_transcript(ctx: SuiteContext) -> CheckResult:
    with tempfile.TemporaryDirectory() as td:
        sents = [(f"sent_{i+1:03d}", f"第{i+1}步把门店标准执行到底", i * 4.0, i * 4.0 + 3.6) for i in range(30)]
        pdir = _make_audio_events_fixture(Path(td), sents)
        ae = _read_json(pdir / "03_audio_events.json")
        plan = director_plan.generate_deterministic_director_plan(ae, style_id="style_15")
        val = director_plan.validate_director_plan(plan, ae)
        ok = bool(val.get("ok")) and len(plan.get("acts") or []) >= 2
    return CheckResult(
        "T2-G2-02", 2, "G2", "120s+ 长口播动态导演规划边界",
        "PASS" if ok else "FAIL",
        f"dur={ae['locked_duration_sec']}s acts={len(plan.get('acts') or [])} ok={val.get('ok')}",
        "validate=True, acts>=2",
        "30句120s+ 长脚本动态映射与覆盖校验通过",
    )


def t2_g2_03_zero_broll_diagram_upgrade(ctx: SuiteContext) -> CheckResult:
    truth_s09 = style_visual_truth.get_visual_truth("style_09")
    truth_s15 = style_visual_truth.get_visual_truth("style_15")
    has_theme = isinstance(truth_s09.get("diagram_theme"), dict) and isinstance(truth_s15.get("diagram_theme"), dict)
    spec = motion_html_renderer.build_panel_job_spec(
        {"mode": "concept_diagram_pip", "diagram_mode": "compare", "panel_title": "无B-roll图解", "quote_text": "用动态图解替代空屏"},
        style_id="style_09", dur_sec=3.0,
    )
    ok = has_theme and (spec is not None) and spec.get("kind") == "concept_diagram_card"
    return CheckResult(
        "T2-G2-03", 2, "G2", "零外部 B-roll 退化动态图解面板升级",
        "PASS" if ok else "FAIL",
        f"has_theme={has_theme} spec_kind={spec.get('kind') if spec else None}",
        "kind==concept_diagram_card",
        "无 B-roll 时稳定构造动态图解规格",
    )


def t2_g2_04_gbro_fig2_optional_vs_mandatory(ctx: SuiteContext) -> CheckResult:
    p_s01 = "3:4 竖版构图\n保留 10% 安全区。\n深色渐变背景，图1人物居中，背后超粗白色大字「商业真相」。"
    p_s03 = "3:4 竖版构图\n保留 10% 安全区。\n浅色背景，图1人物居中指向标题「招牌茶点」。"
    r_s01_no_fig2 = validate_gbro_prompt(p_s01, "style-01", has_fig1=True, fig2_refs=[])
    r_s03_no_fig2 = validate_gbro_prompt(p_s03, "style-03", has_fig1=True, fig2_refs=[])
    ok = r_s01_no_fig2["ok"] and (not r_s03_no_fig2["ok"])
    return CheckResult(
        "T2-G2-04", 2, "G2", "gbro style-01 缺图2容错 vs style-03 强制图2",
        "PASS" if ok else "FAIL",
        f"s01_ok={r_s01_no_fig2['ok']} s03_ok={r_s03_no_fig2['ok']}",
        "s01=PASS, s03=FAIL",
        "风格差异化参考图约束校验准确",
    )


def t2_g2_05_director_apply_plan_rejects_corrupt(ctx: SuiteContext) -> CheckResult:
    with tempfile.TemporaryDirectory() as td:
        pdir = _make_audio_events_fixture(Path(td), [("sent_001", "测试内容一", 0.0, 2.0)])
        bad_p = pdir / "bad_plan.json"
        bad_p.write_text(json.dumps({"acts": [{"act_id": "a1", "sentence_ids": ["sent_999"]}]}), encoding="utf-8")
        res_bad = subprocess.run(
            [ctx.py_bin, str(SCRIPTS_ROOT / "director_plan.py"), "apply-plan", "--pipeline-dir", str(pdir), "--plan", str(bad_p)],
            capture_output=True, text=True, timeout=30,
        )
        ae = _read_json(pdir / "03_audio_events.json")
        good_plan = director_plan.generate_deterministic_director_plan(ae, style_id="style_01")
        good_p = pdir / "good_plan.json"
        good_p.write_text(json.dumps(good_plan, ensure_ascii=False), encoding="utf-8")
        res_good = subprocess.run(
            [ctx.py_bin, str(SCRIPTS_ROOT / "director_plan.py"), "apply-plan", "--pipeline-dir", str(pdir), "--plan", str(good_p)],
            capture_output=True, text=True, timeout=30,
        )
        ok = (res_bad.returncode != 0) and (res_good.returncode == 0)
    return CheckResult(
        "T2-G2-05", 2, "G2", "director_plan apply-plan 非法拒绝与合法放行",
        "PASS" if ok else "FAIL",
        f"bad_rc={res_bad.returncode} good_rc={res_good.returncode}",
        "bad_rc!=0, good_rc=0",
        "越界 sentence_id 被命令行严格拒绝",
    )


def t2_g2_06_sto_cross_video_dish_uniqueness(ctx: SuiteContext) -> CheckResult:
    with tempfile.TemporaryDirectory() as td:
        tdir = Path(td)
        c1 = tdir / "sto1.jpg"
        c2 = tdir / "sto2.jpg"
        im1 = Image.new("RGB", (1080, 1440), (20, 22, 28))
        ImageDraw.Draw(im1).ellipse((180, 500, 900, 1200), fill=(215, 110, 40))
        im1.save(c1)

        im2 = Image.new("RGB", (1080, 1440), (242, 240, 232))
        d2 = ImageDraw.Draw(im2)
        d2.rectangle((140, 480, 940, 1280), fill=(50, 140, 80))
        d2.ellipse((240, 200, 540, 420), fill=(220, 60, 60))
        im2.save(c2)

        p1 = "3:4 竖版构图\n保留 10% 安全区。\n图2玻璃皮脆肉乳鸽特写，图1人物推荐，标题「一鸽胜九鸡」。"
        p2 = "3:4 竖版构图\n保留 10% 安全区。\n图2现蒸水晶虾饺特写，图1人物指点，标题「手工现包虾饺」。"
        dist = hamming_distance(image_dhash(c1), image_dhash(c2))
        kw_match = ("乳鸽" in p1 and "乳鸽" not in p2) and ("虾饺" in p2 and "虾饺" not in p1)
        ok = (dist >= 8) and kw_match
    return CheckResult(
        "T2-G2-06", 2, "G2", "双 STO 视频封面菜品匹配与非复用边界",
        "PASS" if ok else "FAIL",
        f"hash_dist={dist} kw_match={kw_match}",
        "hash_dist>=8, kw_match=True",
        "不同菜品封面哈希与提示词关键词一一对应",
    )


def t2_g3_01_boundary_timestamps_pyav(ctx: SuiteContext) -> CheckResult:
    with tempfile.TemporaryDirectory() as td:
        mp4 = Path(td) / "clip.mp4"
        frames: List[np.ndarray] = []
        for idx in range(60):  # 2.0s @ 30fps
            c = min(250, 10 + idx * 4)
            arr = np.full((320, 180, 3), (c, 30, 250 - c), dtype=np.uint8)
            frames.append(arr)
        write_synthetic_mp4(mp4, frames, fps=30)
        dur = probe_mp4_duration(mp4)
        sampled = decode_mp4_frames_at_times(mp4, [0.0, 0.03, max(0.0, dur - 0.05)])
        r_start = int(sampled[0][1][160, 90, 0])
        r_end = int(sampled[2][1][160, 90, 0])
        ok = (len(sampled) == 3) and (dur >= 1.9) and (r_end - r_start >= 150)
    return CheckResult(
        "T2-G3-01", 2, "G3", "PyAV 边界帧时刻 (t=0, +0.03s, end-0.05s) 解码",
        "PASS" if ok else "FAIL",
        f"dur={dur:.2f}s r_start={r_start} r_end={r_end}",
        "3 frames decoded, end-start>=150",
        "PyAV 精确帧解码与时间轴一致性通过",
    )


def t2_g3_02_contrast_edge_449_vs_451(ctx: SuiteContext) -> CheckResult:
    # 精准查找在 #14151B (L≈0.008) 上达到 ~4.45:1 (FAIL) 与 ~4.55:1 (PASS) 的红珊瑚色
    bg_hex = "#14151B"
    bg_l = rel_luminance(hex_to_rgb(bg_hex))
    # 构造两个紧贴 4.5:1 边界的颜色
    neg_hex = "#E56363"  # ~4.30:1
    pos_hex = "#EE6A6A"  # ~4.70:1
    # 在 R=235, G=B 搜索紧贴 4.5:1 边界的临界色值
    best_neg, best_pos = None, None
    for g in range(40, 95):
        hx = f"#EB{g:02X}{g:02X}"
        r = wcag_ratio(rel_luminance(hex_to_rgb(hx)), bg_l)
        if 4.40 <= r < 4.50:
            best_neg = (hx, r)
        if 4.50 <= r <= 4.65 and best_pos is None:
            best_pos = (hx, r)
    neg_hx, neg_r = best_neg or (neg_hex, wcag_ratio(rel_luminance(hex_to_rgb(neg_hex)), bg_l))
    pos_hx, pos_r = best_pos or (pos_hex, wcag_ratio(rel_luminance(hex_to_rgb(pos_hex)), bg_l))
    r_neg = analyze_style07_wcag_frame_and_hex(neg_hx, bg_hex)
    r_pos = analyze_style07_wcag_frame_and_hex(pos_hx, bg_hex)
    ok = (not r_neg["ok"]) and r_pos["ok"]
    return CheckResult(
        "T2-G3-02", 2, "G3", "WCAG 4.5:1 对比度精确边界测试",
        "PASS" if ok else "FAIL",
        f"neg={neg_hx}({neg_r:.2f}:1) pos={pos_hx}({pos_r:.2f}:1)",
        "4.49<4.5(FAIL), >=4.50(PASS)",
        "WCAG 2.1 sRGB 临界阈值精准区分",
    )


def t2_g3_03_style06_x880_vs_x881_boundary(ctx: SuiteContext) -> CheckResult:
    p_pass = {"visual_elements": [{"callout_variant": "s06_vertical_poem", "final_bbox": {"x": 760, "y": 380, "w": 120, "h": 500}}]}  # x+w=880
    p_fail = {"visual_elements": [{"callout_variant": "s06_vertical_poem", "final_bbox": {"x": 761, "y": 380, "w": 120, "h": 500}}]}  # x+w=881
    r_pass = analyze_style06_safe_zone(p_pass)
    r_fail = analyze_style06_safe_zone(p_fail)
    ok = r_pass["ok"] and (not r_fail["ok"])
    return CheckResult(
        "T2-G3-03", 2, "G3", "style_06 右边界 x2=880 vs 881 临界测试",
        "PASS" if ok else "FAIL",
        f"x880_ok={r_pass['ok']} x881_ok={r_fail['ok']}",
        "880=PASS, 881=FAIL",
        "SAFE_ZONE x_max=880 单像素边界生效",
    )


def t2_g3_04_style04_char_length_boundary(ctx: SuiteContext) -> CheckResult:
    with tempfile.TemporaryDirectory() as td:
        tdir = Path(td)
        p4 = _render_chinese_card(tdir / "s4.png", "前提搬不", font_size=140, font_loader=ctx.font)
        p6 = _render_chinese_card(tdir / "s6.png", "前提搬不过来", font_size=130, font_loader=ctx.font)
        p10 = _render_chinese_card(tdir / "s10.png", "门店模型全链路拆解", pos=(60, 220), font_size=95, font_loader=ctx.font)
        vis = vision_inspector.inspect_images([p4, p6, p10])
        r4_on_6 = analyze_style04_giant_text("前提搬不过来", vis[str(p4)])
        r6 = analyze_style04_giant_text("前提搬不过来", vis[str(p6)])
        r10 = analyze_style04_giant_text("门店模型全链路拆解", vis[str(p10)])
        ok = (not r4_on_6["ok"]) and r6["ok"] and r10["ok"]
    return CheckResult(
        "T2-G3-04", 2, "G3", "style_04 4字/6字/10字 OCR 长度边界",
        "PASS" if ok else "FAIL",
        f"4vs6={r4_on_6['match_ratio']} 6={r6['match_ratio']} 10={r10['match_ratio']}",
        "4vs6=FAIL, 6=PASS, 10=PASS",
        "长短巨字 Apple Vision OCR 均准确匹配",
    )


def t2_g3_05_topbar_y318_vs_y340_boundary(ctx: SuiteContext) -> CheckResult:
    tb = {"kind": "persistent_top_bar", "start_frame": 0, "end_frame": 200, "final_bbox": {"x": 220, "y": 168, "w": 640, "h": 150}}  # bottom=318
    r_fail = analyze_topbar_and_circle_sync({"visual_elements": [tb, {"kind": "stamp_tag", "start_frame": 10, "end_frame": 60, "final_bbox": {"x": 200, "y": 318, "w": 300, "h": 80}}]}, {})
    r_pass = analyze_topbar_and_circle_sync({"visual_elements": [tb, {"kind": "stamp_tag", "start_frame": 10, "end_frame": 60, "final_bbox": {"x": 200, "y": 340, "w": 300, "h": 80}}]}, {})
    ok = (not r_fail["ok"]) and r_pass["ok"]
    return CheckResult(
        "T2-G3-05", 2, "G3", "top_bar 底缘 y=318 碰撞 vs y=340 安全边界",
        "PASS" if ok else "FAIL",
        f"y318_ok={r_fail['ok']} y340_ok={r_pass['ok']}",
        "y318=FAIL, y340=PASS",
        "顶栏 +12px 净空边界校验准确",
    )


def t2_g3_06_binary_jagged_vs_feathered_matte(ctx: SuiteContext) -> CheckResult:
    yy, xx = np.mgrid[0:360, 0:360]
    rng = np.random.default_rng(7)
    saw = rng.integers(-6, 7, size=360)[:, None]
    hard_mask = np.where(((xx - (180 + saw)) ** 2 + (yy - 180) ** 2) <= 100.0 ** 2, 255, 0).astype(np.uint8)
    clean_circle = np.where(((xx - 180) ** 2 + (yy - 180) ** 2) <= 100.0 ** 2, 255, 0).astype(np.uint8)
    feather_mask = np.asarray(Image.fromarray(clean_circle).filter(ImageFilter.GaussianBlur(radius=2.5)))
    r_hard = analyze_portrait_matte_smoothness(hard_mask)
    r_feat = analyze_portrait_matte_smoothness(feather_mask)
    ok = (not r_hard["ok"]) and r_feat["ok"]
    return CheckResult(
        "T2-G3-06", 2, "G3", "二值锯齿蒙版 vs 4px 羽化蒙版边界",
        "PASS" if ok else "FAIL",
        f"hard_aa={r_hard['boundary_aa_fraction']} feather_aa={r_feat['boundary_aa_fraction']}",
        "hard=FAIL, feather=PASS",
        "二值切边与抗锯齿羽化区分明确",
    )


def t2_g4_01_short_broll_segments_span(ctx: SuiteContext) -> CheckResult:
    edl = {
        "total_duration_sec": 12.0,
        "clips": [
            {"clip_id": "clip_001", "track_role": "a_roll", "duration_sec": 1.8, "pip_layout": {"mode": "review_whiteboard_pip", "v11_panel_group_id": "wb_1", "v11_panel_group_dur": 4.5, "v11_panel_offset_sec": 0.0}},
            {"clip_id": "clip_002", "track_role": "b_roll", "duration_sec": 1.03, "pip_layout": {"mode": "review_whiteboard_pip", "v11_panel_group_id": "wb_1", "v11_panel_group_dur": 4.5, "v11_panel_offset_sec": 1.8}},
            {"clip_id": "clip_003", "track_role": "b_roll", "duration_sec": 1.67, "pip_layout": {"mode": "review_whiteboard_pip", "v11_panel_group_id": "wb_1", "v11_panel_group_dur": 4.5, "v11_panel_offset_sec": 2.83}},
        ],
    }
    frames = [np.full((240, 180, 3), 235, dtype=np.uint8)] * 3 + [np.full((240, 180, 3), 25, dtype=np.uint8)] * 7
    res = analyze_style24_edl_and_frames(edl, frames)
    ok = res["ok"]
    return CheckResult(
        "T2-G4-01", 2, "G4", "短 B-roll 碎片(<3.2s) 跨镜聚合边界",
        "PASS" if ok else "FAIL",
        f"edl_ratio={res['edl_ratio']} errs={res['group_errors']}",
        "ok=True",
        "1.03s 短切镜正确纳入 4.5s 面板组",
    )


def t2_g4_02_mixed_aroll_broll_aroll_group_continuity(ctx: SuiteContext) -> CheckResult:
    edl_bad_offset = {
        "total_duration_sec": 15.0,
        "clips": [
            {"track_role": "a_roll", "duration_sec": 1.5, "pip_layout": {"mode": "review_whiteboard_pip", "v11_panel_group_id": "g1", "v11_panel_group_dur": 4.5, "v11_panel_offset_sec": 2.0}},
            {"track_role": "b_roll", "duration_sec": 1.5, "pip_layout": {"mode": "review_whiteboard_pip", "v11_panel_group_id": "g1", "v11_panel_group_dur": 4.5, "v11_panel_offset_sec": 1.5}},
            {"track_role": "a_roll", "duration_sec": 1.5, "pip_layout": {"mode": "review_whiteboard_pip", "v11_panel_group_id": "g1", "v11_panel_group_dur": 4.5, "v11_panel_offset_sec": 3.0}},
        ],
    }
    edl_good = {
        "total_duration_sec": 15.0,
        "clips": [
            {"track_role": "a_roll", "duration_sec": 1.5, "pip_layout": {"mode": "review_whiteboard_pip", "v11_panel_group_id": "g1", "v11_panel_group_dur": 4.5, "v11_panel_offset_sec": 0.0}},
            {"track_role": "b_roll", "duration_sec": 1.5, "pip_layout": {"mode": "review_whiteboard_pip", "v11_panel_group_id": "g1", "v11_panel_group_dur": 4.5, "v11_panel_offset_sec": 1.5}},
            {"track_role": "a_roll", "duration_sec": 1.5, "pip_layout": {"mode": "review_whiteboard_pip", "v11_panel_group_id": "g1", "v11_panel_group_dur": 4.5, "v11_panel_offset_sec": 3.0}},
        ],
    }
    frames = [np.full((240, 180, 3), 235, dtype=np.uint8)] * 3 + [np.full((240, 180, 3), 25, dtype=np.uint8)] * 7
    r_bad = analyze_style24_edl_and_frames(edl_bad_offset, frames)
    r_good = analyze_style24_edl_and_frames(edl_good, frames)
    ok = (not r_bad["ok"]) and r_good["ok"]
    return CheckResult(
        "T2-G4-02", 2, "G4", "A->B->A 混合切镜面板 offset 连续性边界",
        "PASS" if ok else "FAIL",
        f"bad_errs={r_bad['group_errors']} good_ok={r_good['ok']}",
        "bad=FAIL(non_monotonic), good=PASS",
        "跨 A/B/A 面板时间偏移单调性校验准确",
    )


def t2_g4_03_whiteboard_0_vs_141_vs_151_boundary(ctx: SuiteContext) -> CheckResult:
    dark = np.full((120, 90, 3), 24, dtype=np.uint8)
    paper = np.full((120, 90, 3), 235, dtype=np.uint8)
    edl_0 = {"total_duration_sec": 100.0, "clips": [{"duration_sec": 0.0, "pip_layout": {"mode": "review_whiteboard_pip", "v11_panel_group_id": "g1", "v11_panel_group_dur": 4.0, "v11_panel_offset_sec": 0.0}}]}
    edl_141 = {"total_duration_sec": 100.0, "clips": [{"duration_sec": 14.1, "pip_layout": {"mode": "review_whiteboard_pip", "v11_panel_group_id": "g1", "v11_panel_group_dur": 14.1, "v11_panel_offset_sec": 0.0}}]}
    edl_151 = {"total_duration_sec": 100.0, "clips": [{"duration_sec": 15.1, "pip_layout": {"mode": "review_whiteboard_pip", "v11_panel_group_id": "g1", "v11_panel_group_dur": 15.1, "v11_panel_offset_sec": 0.0}}]}
    r0 = analyze_style24_edl_and_frames(edl_0, [dark] * 20)
    r141 = analyze_style24_edl_and_frames(edl_141, [paper] * 2 + [dark] * 18)  # 10% frames -> fail
    r151 = analyze_style24_edl_and_frames(edl_151, [paper] * 4 + [dark] * 16)  # 20% frames -> pass
    ok = (not r0["ok"]) and (not r141["ok"]) and r151["ok"]
    return CheckResult(
        "T2-G4-03", 2, "G4", "style_24 白板占比 0% / 14.1% / 15.1% 阈值边界",
        "PASS" if ok else "FAIL",
        f"0%={r0['ok']} 14.1%={r141['ok']} 15.1%={r151['ok']}",
        "0%=FAIL, 14.1%=FAIL, 15.1%=PASS",
        "白板 15% 临界阈值准确区分",
    )


def t2_g4_04_blocks4_frame8_frac84_injection(ctx: SuiteContext) -> CheckResult:
    neg = {"style_scene_beats": [{"frac": 0.84, "target_sec": 66.5, "layout": {"mode": "concept_diagram_pip", "panel_title": "夫妻店到雇人断层"}}]}
    pos = {"style_scene_beats": [{"frac": 0.84, "target_sec": 66.5, "layout": {"mode": "paper_outline_pip", "panel_title": "门店实操步骤"}}]}
    ok = (len(detect_frame8_generic_black_card_in_plan(neg)) >= 1) and (len(detect_frame8_generic_black_card_in_plan(pos)) == 0)
    return CheckResult(
        "T2-G4-04", 2, "G4", "frac=0.84 第8格通用卡注入边界",
        "PASS" if ok else "FAIL",
        f"neg_detected={not ok is False}",
        "neg=caught, pos=clean",
        "0.84 处固定黑卡注入检测精准",
    )


def t2_g4_05_style15_native_ending_preserved(ctx: SuiteContext) -> CheckResult:
    tr15 = style_visual_truth.get_visual_truth("style_15")
    beats = tr15.get("scene_beats") or []
    modes = [str((b.get("layout") or {}).get("mode") or "") for b in beats]
    last_mode = modes[-1] if modes else ""
    no_black_card = not any("夫妻店" in json.dumps(b, ensure_ascii=False) for b in beats)
    ok = ("paper_outline_pip" in modes) and (last_mode != "concept_diagram_pip") and no_black_card
    return CheckResult(
        "T2-G4-05", 2, "G4", "style_15 结尾纸底保留不被第8格黑卡覆盖",
        "PASS" if ok else "FAIL",
        f"last_mode={last_mode} has_paper={'paper_outline_pip' in modes} no_black_card={no_black_card}",
        "paper_outline_pip present & last_mode!=concept_diagram_pip",
        "style_15 原生纸底结尾基因完好",
    )


def t2_g5_01_synthetic_personal_path_leak_selftest(ctx: SuiteContext) -> CheckResult:
    with tempfile.TemporaryDirectory() as td:
        tdir = Path(td)
        leak_unix = tdir / "leak_unix.py"
        leak_win = tdir / "leak_win.py"
        clean_p = tdir / "clean.py"
        # 动态拼接合成样本，避免在本文件中写入任何个人家目录前缀字面量
        synthetic_home = "/" + "Users" + "/" + "example_alice/workspace"
        synthetic_win = "C:\\" + "Users" + "\\example_alice\\proj"
        leak_unix.write_text(f"ROOT = '{synthetic_home}'\n", encoding="utf-8")
        leak_win.write_text(f"WIN = r'{synthetic_win}'\n", encoding="utf-8")
        clean_p.write_text("ROOT = str(Path.home() / 'workspace')\n", encoding="utf-8")
        priv_unix, _ = scan_files_for_leaks([leak_unix], draft_names=[], exclude_prefixes=())
        priv_win, _ = scan_files_for_leaks([leak_win], draft_names=[], exclude_prefixes=())
        priv_good, _ = scan_files_for_leaks([clean_p], draft_names=[], exclude_prefixes=())
        ok = (len(priv_unix) == 1) and (len(priv_win) == 1) and (len(priv_good) == 0)
    return CheckResult(
        "T2-G5-01", 2, "G5", "个人路径泄漏注入自检",
        "PASS" if ok else "FAIL",
        f"unix={len(priv_unix)} win={len(priv_win)} good={len(priv_good)}",
        "unix=1, win=1, good=0",
        "Unix 与 Windows 个人路径泄漏均被立即拦截",
    )


def t2_g5_02_synthetic_draft_name_leak_selftest(ctx: SuiteContext) -> CheckResult:
    draft_names = load_draft_names_from_forensic()
    with tempfile.TemporaryDirectory() as td:
        tdir = Path(td)
        leak_p = tdir / "leak.md"
        clean_p = tdir / "clean.md"
        sample_draft = draft_names[0] if draft_names else "MISSING"
        leak_p.write_text(f"参考工程：{sample_draft}\n", encoding="utf-8")
        clean_p.write_text("参考工程：style_01\n", encoding="utf-8")
        _, d_bad = scan_files_for_leaks([leak_p], draft_names=draft_names, exclude_prefixes=())
        _, d_good = scan_files_for_leaks([clean_p], draft_names=draft_names, exclude_prefixes=())
        ok = bool(draft_names) and (len(d_bad) >= 1) and (len(d_good) == 0)
    return CheckResult(
        "T2-G5-02", 2, "G5", "剪映草稿名泄漏注入自检",
        "PASS" if ok else "FAIL",
        f"bad={len(d_bad)} good={len(d_good)}",
        "bad>=1, good=0",
        "动态读取 forensic/style_sources.json 并成功拦截草稿名泄漏",
    )


def t2_g5_03_broken_symlink_selftest(ctx: SuiteContext) -> CheckResult:
    with tempfile.TemporaryDirectory() as td:
        tdir = Path(td)
        bad_proj = tdir / "bad_proj" / "pipeline"
        bad_proj.mkdir(parents=True)
        os.symlink(tdir / "nonexistent_target", bad_proj / "01_normalized")
        good_target = tdir / "real_target"
        good_target.mkdir()
        good_proj = tdir / "good_proj" / "pipeline"
        good_proj.mkdir(parents=True)
        os.symlink(good_target, good_proj / "01_normalized")
        broken_bad = find_broken_symlinks([bad_proj.parent])
        broken_good = find_broken_symlinks([good_proj.parent])
        ok = (len(broken_bad) == 1) and (len(broken_good) == 0)
    return CheckResult(
        "T2-G5-03", 2, "G5", "悬空 01_normalized 软链检出边界",
        "PASS" if ok else "FAIL",
        f"bad={len(broken_bad)} good={len(broken_good)}",
        "bad=1, good=0",
        "断链检测准确，未破坏任何真实目录",
    )


def t2_g5_04_baseline_devtools_and_cli_help(ctx: SuiteContext) -> CheckResult:
    cmds = [
        [ctx.py_bin, str(REPO_ROOT / "dev_tools" / "verify_style_parity.py"), "--check"],
        [ctx.py_bin, str(REPO_ROOT / "dev_tools" / "audit_consistency.py")],
        [ctx.py_bin, str(REPO_ROOT / "dev_tools" / "bench_talking_head.py"), "--smoke", "--format", "json"],
    ]
    failures: List[str] = []
    for c in cmds:
        r = subprocess.run(c, cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            failures.append(f"{Path(c[1]).name}:rc={r.returncode}")
    for script in ("step3_lock_audio.py", "director_plan.py", "step4_build_edl.py", "step5_subtitles.py", "step6_packaging.py", "kinetic_choreographer.py", "step9_title_cover.py", "qa_inspector.py", "independent_verifier.py", "run_pipeline.py"):
        r = subprocess.run([ctx.py_bin, str(SCRIPTS_ROOT / script), "--help"], capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            failures.append(f"{script} --help:rc={r.returncode}")

    # 真实沙箱 CLI 串联：director_plan fallback -> step5_subtitles -> step6_packaging
    with tempfile.TemporaryDirectory() as td:
        tdir = Path(td)
        pdir = _make_audio_events_fixture(tdir, [
            ("sent_001", "旧房墙面翻新最重要的是基层处理", 0.0, 2.8),
            ("sent_002", "三天完工并且净味环保可直接入住", 3.0, 5.8),
        ])
        (pdir / "01_manifest.json").write_text(json.dumps({"items": []}), encoding="utf-8")
        (pdir / "04_edl.json").write_text(json.dumps({
            "fps": 30, "total_duration_sec": 6.1, "total_frames": 183,
            "clips": [{
                "clip_id": "clip_001", "track_role": "a_roll", "media_path": str(tdir / "aroll_01.mp4"),
                "timeline_in_sec": 0.0, "timeline_out_sec": 6.1,
                "timeline_in_frame": 0, "timeline_out_frame": 183,
                "duration_sec": 6.1, "cut_event_frame": 0,
            }],
        }), encoding="utf-8")
        chain_steps = (
            ("director_plan", [ctx.py_bin, str(SCRIPTS_ROOT / "director_plan.py"), "fallback", "--pipeline-dir", str(pdir), "--style-id", "style_09"]),
            ("step5_subtitles", [ctx.py_bin, str(SCRIPTS_ROOT / "step5_subtitles.py"), "--pipeline-dir", str(pdir)]),
            ("step6_packaging", [ctx.py_bin, str(SCRIPTS_ROOT / "step6_packaging.py"), "--pipeline-dir", str(pdir), "--style-id", "style_09"]),
        )
        for cname, ccmd in chain_steps:
            rc = subprocess.run(ccmd, cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=60)
            if rc.returncode != 0:
                failures.append(f"chain_{cname}:rc={rc.returncode}")
                break
        for expected_art in ("03b_director_plan.json", "05_subtitles.json", "06_packaging.json"):
            if not (pdir / expected_art).is_file():
                failures.append(f"chain_missing:{expected_art}")

    ok = len(failures) == 0
    return CheckResult(
        "T2-G5-04", 2, "G5", "基线 dev_tools 门禁、10 CLI --help 与沙箱串联",
        "PASS" if ok else "FAIL",
        f"failures={len(failures)}",
        "0 failures",
        ", ".join(failures) if failures else "parity 24/24 + consistency + bench 3/3 + 10 CLIs + director->step5->step6 串联全部 PASS",
    )


def t2_g5_05_r6_cleanup_boundary(ctx: SuiteContext) -> CheckResult:
    def _eval_cleanup_state(repo_like: Path) -> Tuple[bool, List[str]]:
        errs: List[str] = []
        for obsolete in ("test_output/svs_koubo/ws", "test_output/svs_koubo/_chk", "test_output/svs_koubo/交付"):
            if (repo_like / obsolete).exists():
                errs.append(f"residual:{obsolete}")
        for must_keep in ("short-video-studio/scripts", "tests", "forensic/style_sources.json"):
            if not (repo_like / must_keep).exists():
                errs.append(f"missing_must_keep:{must_keep}")
        return (len(errs) == 0), errs

    with tempfile.TemporaryDirectory() as td:
        tdir = Path(td)
        dirty_root = tdir / "dirty"
        for d in ("test_output/svs_koubo/ws", "short-video-studio/scripts", "tests", "forensic"):
            (dirty_root / d).mkdir(parents=True, exist_ok=True)
        (dirty_root / "forensic/style_sources.json").write_text("{}", encoding="utf-8")

        clean_root = tdir / "clean"
        for d in ("short-video-studio/scripts", "tests", "forensic"):
            (clean_root / d).mkdir(parents=True, exist_ok=True)
        (clean_root / "forensic/style_sources.json").write_text("{}", encoding="utf-8")

        dirty_ok, _ = _eval_cleanup_state(dirty_root)
        clean_ok, _ = _eval_cleanup_state(clean_root)
    self_ok = (not dirty_ok) and clean_ok
    if ctx.mode == "full":
        live_ok, live_errs = _eval_cleanup_state(REPO_ROOT)
        # 校验工作区 V5-V10 旧目录已清理
        for acct_root in (ctx.biz_root, ctx.sto_root):
            if acct_root.is_dir():
                for proj in acct_root.iterdir():
                    if proj.is_dir() and re.match(r"^v(?:[5-9]|10)_", proj.name):
                        live_ok = False
                        live_errs.append(f"obsolete_ws:{proj.name}")
        return CheckResult(
            "T2-G5-05", 2, "G5", "R6 历史过程文件安全清理边界",
            "PASS" if (self_ok and live_ok) else "FAIL",
            f"live_ok={live_ok} errs={len(live_errs)}",
            "live_ok=True",
            ", ".join(live_errs[:5]) if live_errs else "旧过程目录已清除且必留资产完好",
        )
    return CheckResult(
        "T2-G5-05", 2, "G5", "R6 历史过程文件安全清理边界",
        "PASS" if self_ok else "FAIL",
        f"dirty_ok={dirty_ok} clean_ok={clean_ok}",
        "dirty=FAIL, clean=PASS",
        "R6 清理边界校验器自校准通过（实盘在 full 模式强制验收）",
    )


# ---------------------------------------------------------------------------
# Tier 3 (7 项)：跨特性两两组合交互场景
# ---------------------------------------------------------------------------
def t3_01_topbar_plus_concept_diagram_pip(ctx: SuiteContext) -> CheckResult:
    pkg = {"visual_elements": [{"kind": "persistent_top_bar", "start_frame": 0, "end_frame": 240, "final_bbox": {"x": 220, "y": 168, "w": 640, "h": 150}}]}
    pip_bad = {"mode": "concept_diagram_pip", "circle_cx": 888, "circle_cy": 220, "circle_r": 92}
    pip_good = {"mode": "concept_diagram_pip", "circle_cx": 888, "circle_cy": 430, "circle_r": 92}
    spec_good = motion_html_renderer.build_panel_job_spec(pip_good, style_id="style_03", dur_sec=3.0)
    r_neg = analyze_topbar_and_circle_sync(pkg, {"clips": [{"pip_layout": pip_bad}]}, panel_spec={"circle_cy": 340})
    r_pos = analyze_topbar_and_circle_sync(pkg, {"clips": [{"pip_layout": pip_good}]}, panel_spec=spec_good)
    ok = (not r_neg["ok"]) and r_pos["ok"]
    return CheckResult("T3-01", 3, "G3", "persistent_top_bar + concept_diagram_pip 交互", "PASS" if ok else "FAIL", f"neg_ok={r_neg['ok']} pos_ok={r_pos['ok']}", "neg=FAIL, pos=PASS", "顶栏下沿与圆窗/圆环坐标完全同步")


def t3_02_style02_dp_segmenter_plus_list_gather(ctx: SuiteContext) -> CheckResult:
    raw = "我们把开店最容易踩的三个坑拆开讲清楚第一先算房租第二看毛利第三盯复购"
    segs = [s[2] for s in step5_subtitles.dp_segment_short_lines(raw, 608, font_size=62)]
    items = []
    for i, txt in enumerate(segs[:6]):
        lane = i % 3
        t0 = (i // 3) * 2.2 + lane * 0.35
        items.append({"id": f"ks_{i}", "group_id": f"g_{i//3}", "text_or_asset": txt, "start_sec": t0, "end_sec": t0 + 1.4, "y_norm": -0.16 - lane * 0.14, "z_layer": "fg"})
    r_pos = analyze_style02_kinetic_items(items)
    # 注入同 Y 重叠作为反例验证交互检测灵敏度
    bad_items = list(items) + [{"id": "ks_bad", "group_id": "g_0", "text_or_asset": "复购率的", "start_sec": 0.2, "end_sec": 1.3, "y_norm": -0.16, "z_layer": "fg"}]
    r_neg = analyze_style02_kinetic_items(bad_items)
    ok = r_pos["ok"] and (not r_neg["ok"])
    return CheckResult("T3-02", 3, "G1", "style_02 + 通用DP分词 + list_gather_depth 交互", "PASS" if ok else "FAIL", f"segs={len(segs)} pos_ok={r_pos['ok']} neg_ok={r_neg['ok']}", "pos=PASS, neg=FAIL", "无挂尾、无复读、无同Y碰撞")


def t3_03_style10_16_callout_plus_accurate_matting(ctx: SuiteContext) -> CheckResult:
    # 校验人像抠像置顶 + 羽化平滑 + 顶部大字后置三者同时满足
    yy, xx = np.mgrid[0:360, 0:360]
    clean = np.where(((xx - 180) ** 2 + (yy - 180) ** 2) <= 100.0 ** 2, 255, 0).astype(np.uint8)
    alpha = np.asarray(Image.fromarray(clean).filter(ImageFilter.GaussianBlur(radius=2.2)))
    m_res = analyze_portrait_matte_smoothness(alpha)
    pkg = {"visual_elements": [{"kind": "center_burst_title", "z_layer": "behind_person"}]}
    edl = {"clips": [{"pip_layout": {"mode": "self_matting_behind_text"}}]}
    frame = np.full((CANVAS_H, CANVAS_W, 3), (25, 25, 30), dtype=np.uint8)
    frame[200:310, 200:880, :] = (255, 255, 255)
    frame[520:1000, 320:760, :] = (195, 150, 125)
    l_res = analyze_style10_16_layering(pkg, edl, frame, face_bbox_norm={"x": 0.30, "y": 0.28, "w": 0.40, "h": 0.25}, fg_text_bboxes_norm=[{"x": 0.20, "y": 0.10, "w": 0.60, "h": 0.06}])
    ok = m_res["ok"] and l_res["ok"]
    return CheckResult("T3-03", 3, "G3", "style_10/16 顶格大字 + 抠像置顶交互", "PASS" if ok else "FAIL", f"matte_ok={m_res['ok']} layer_ok={l_res['ok']}", "both=PASS", "真抠像平滑且稳居大字前方")


def t3_04_style24_broll_intercuts_plus_panel_group(ctx: SuiteContext) -> CheckResult:
    with tempfile.TemporaryDirectory() as td:
        mp4 = Path(td) / "s24_span.mp4"
        frames: List[np.ndarray] = []
        for i in range(90):  # 3.0s @ 30fps，前 1.5s 跨 A/B 切镜保持杂志白板，后 1.5s 实拍暗底
            c = 235 if i < 45 else 28
            fr = np.full((240, 180, 3), c, dtype=np.uint8)
            fr[20:40, 20:20 + (i % 40) * 3, :] = (80, 80, 90)
            frames.append(fr)
        write_synthetic_mp4(mp4, frames, fps=30)
        decoded = [fr for _, fr in decode_mp4_frames_at_times(mp4, [0.2, 0.7, 1.2, 1.7, 2.2, 2.7])]
        edl = {
            "total_duration_sec": 3.0,
            "clips": [
                {"track_role": "a_roll", "duration_sec": 0.8, "pip_layout": {"mode": "review_whiteboard_pip", "v11_panel_group_id": "wb_span", "v11_panel_group_dur": 3.2, "v11_panel_offset_sec": 0.0}},
                {"track_role": "b_roll", "duration_sec": 0.7, "pip_layout": {"mode": "review_whiteboard_pip", "v11_panel_group_id": "wb_span", "v11_panel_group_dur": 3.2, "v11_panel_offset_sec": 0.8}},
            ],
        }
        res = analyze_style24_edl_and_frames(edl, decoded)
        ok = res["ok"]
    return CheckResult("T3-04", 3, "G4", "style_24 白板 + B-roll 切镜 + v11_panel_group_id 交互", "PASS" if ok else "FAIL", f"edl={res['edl_ratio']} fr={res['frame_ratio']}", "edl>=0.15 & fr>=0.15", "PyAV 解码确认跨镜白板连续输出")


def t3_05_style15_paper_plus_bottom_subtitles(ctx: SuiteContext) -> CheckResult:
    with tempfile.TemporaryDirectory() as td:
        p = _render_chinese_card(Path(td) / "s15_combo.png", "一、立面基层标准化", bg=(242, 244, 247), fg=(24, 26, 32), pos=(120, 460), font_size=100, font_loader=ctx.font)
        im = Image.open(p).convert("RGB")
        # 在通体纸底下方叠加高对比度描边/深色字幕条，同时保持底部纸底 >= 175
        draw = ImageDraw.Draw(im)
        draw.text((220, 1520), "底层不打磨必定空鼓", font=ctx.font(60), fill=(20, 22, 28))
        im.save(p)
        arr = np.asarray(im)
        vis = vision_inspector.inspect_images([p])[str(p)]
        r15 = analyze_style15_paper_frame(arr, arr, vis)
        sub_wcag = wcag_ratio(rel_luminance((20, 22, 28)), rel_luminance((242, 244, 247)))
        ok = r15["ok"] and (sub_wcag >= 4.5)
    return CheckResult("T3-05", 3, "G3", "style_15 通体纸底 + 底部字幕对比度交互", "PASS" if ok else "FAIL", f"paper_ok={r15['ok']} sub_wcag={sub_wcag:.2f}:1", "paper=PASS, wcag>=4.5", "通体纸底无黑块且底部字幕高对比可读")


def t3_06_director_plan_plus_step9_gbro_handoff(ctx: SuiteContext) -> CheckResult:
    with tempfile.TemporaryDirectory() as td:
        tdir = Path(td)
        c34 = tdir / "09_cover_3x4.jpg"
        cf = tdir / "09_cover.jpg"
        Image.new("RGB", (1080, 1440), (240, 242, 245)).save(c34)
        Image.new("RGB", (1080, 1920), (20, 22, 28)).save(cf)
        plan = {"cover_copy": {"headline_top": "招牌老卤烧鹅", "gbro_style": "style-03"}}
        handoff = {
            "recommended_style_id": plan["cover_copy"]["gbro_style"],
            "aspect_ratio": "3:4",
            "figure_1_face_ref": {"path": "09_host_face_ref.png"},
            "figure_2_content_refs": [{"path": "09_content_ref_dish.png"}],
            "prompt_text": f"3:4 竖版构图\n保留 10% 安全区。\n浅色背景，图2烧鹅切盘特写占比65%，图1人物做推荐手势，粗体标题「{plan['cover_copy']['headline_top']}」。",
        }
        res = validate_gbro_handoff_and_covers(handoff, c34, cf)
        ok = res["ok"]
    return CheckResult("T3-06", 3, "G2", "director_plan + step9 gbro 多参考图联动", "PASS" if ok else "FAIL", f"ok={res['ok']}", "ok=True", "cover_copy.gbro_style 精准驱动封面生成契约")


def t3_07_micro_motion_plus_bb_no_visual_freeze(ctx: SuiteContext) -> CheckResult:
    # 真实合成 4.0s (120 帧) 1080x1920 H264 MP4，验证非 KTV 微动效通过 PyAV 解码 + bb_no_visual_freeze
    with tempfile.TemporaryDirectory() as td:
        mp4 = Path(td) / "micro_motion.mp4"
        frames: List[np.ndarray] = []
        for fi in range(120):
            arr = np.full((CANVAS_H, CANVAS_W, 3), (22, 24, 31), dtype=np.uint8)
            # 在中下部绘制平滑呼吸进度条（避免撞上顶栏与字幕掩膜区），色度中立
            bar_w = 120 + int((fi / 119.0) * 720)
            arr[1120:1156, 120:120 + bar_w, :] = (232, 236, 242)
            # 中性灰高亮卡片指示块随时间点亮（避免低饱和蓝偏色在 yuv420p 下放大 chroma）
            step_idx = min(3, fi // 30)
            y0 = 420 + step_idx * 150
            arr[y0:y0 + 110, 120:960, :] = (52, 54, 58)
            frames.append(arr)
        write_synthetic_mp4(mp4, frames, fps=30)
        sampled = decode_mp4_frames_at_times(mp4, [i * 0.5 for i in range(9)])
        res = analyze_ktv_orbs_and_freeze(sampled)
        ok = res["orbs_ok"] and res["freeze_ok"]
    return CheckResult(
        "T3-07", 3, "G3", "精致微动效 MP4 + bb_no_visual_freeze 联合校验",
        "PASS" if ok else "FAIL",
        f"orbs_ok={res['orbs_ok']} freeze_ok={res['freeze_ok']} static={res['max_static_sec']}s chroma={res['chroma_p95']}",
        "orbs_ok=True, static<=3.0s",
        "H264 真实解码实测：无 KTV 光球且冻帧检测 PASS",
    )


# ---------------------------------------------------------------------------
# Tier 4 (6 项)：真实全量验收场景（smoke 下跑结构化正负微缩验证，full 下实查 48 条+盲测+清理+单测）
# ---------------------------------------------------------------------------
def _validate_single_project_artifacts(
    proj_dir: Path,
    *,
    require_whiteboard: bool = False,
    require_gbro_covers: bool = True,
) -> Tuple[bool, List[str]]:
    errs: List[str] = []
    pdir = proj_dir / "pipeline" if (proj_dir / "pipeline").is_dir() else proj_dir
    out_dir = proj_dir / "out" if (proj_dir / "out").is_dir() else proj_dir
    mp4 = out_dir / "output_final.mp4"
    qa_p = pdir / "10_qa_report.json"
    edl_p = pdir / "04_edl.json"
    plan_p = pdir / "03b_director_plan.json"
    pkg_p = pdir / "06_packaging.json"
    sub_p = pdir / "05_subtitles.json"
    ae_p = pdir / "03_audio_events.json"
    hf_p = pdir / "09_gbro_cover_handoff.json"
    c34_p = pdir / "09_cover_3x4.jpg"
    cf_p = pdir / "09_cover.jpg"

    if not mp4.is_file() or mp4.stat().st_size < 10_000:
        errs.append("missing_output_final.mp4")

    if not qa_p.is_file():
        errs.append("missing_10_qa_report.json")
    else:
        qa = _read_json(qa_p)
        if not qa.get("all_passed"):
            errs.append("qa_all_passed=False")
        for bb in (qa.get("blackbox_details") or {}).get("checks") or []:
            if bb.get("id") == "bb_no_visual_freeze" and bb.get("status") != "PASS":
                errs.append("bb_no_visual_freeze!=PASS")

    if not plan_p.is_file():
        errs.append("missing_03b_director_plan.json")
    else:
        plan = _read_json(plan_p)
        ae_doc = _read_json(ae_p) if ae_p.is_file() else None
        dp_res = validate_director_plan_universal(plan, ae_doc)
        if not dp_res["ok"]:
            errs.extend(dp_res["errors"][:3])

    if not pkg_p.is_file():
        errs.append("missing_06_packaging.json")
    else:
        pkg = _read_json(pkg_p)
        errs.extend(check_dual_callout_overlap(pkg))
        if (pkg.get("kinetic_stage") or {}).get("enabled"):
            ks_res = analyze_style02_kinetic_items(pkg["kinetic_stage"].get("items") or [])
            if not ks_res["ok"]:
                errs.append(f"style02_ks_fail:{ks_res['max_simultaneous_rows']}")

    if not sub_p.is_file():
        errs.append("missing_05_subtitles.json")
    else:
        sub_entries = _read_json(sub_p).get("entries") or []
        texts_flat, is_final_flat, sent_groups = _group_subtitle_rows_by_sentence(sub_entries)
        if not texts_flat:
            errs.append("empty_05_subtitles")
        else:
            errs.extend(detect_dangling_particles(texts_flat, is_sentence_final=is_final_flat))
            for grp in sent_groups:
                errs.extend(detect_aux_head_and_fragments(grp, single_sentence_mode=(len(grp) == 1)))
                errs.extend(detect_split_numbers_compounds(grp))

    if not edl_p.is_file():
        errs.append("missing_04_edl.json")
    else:
        edl = _read_json(edl_p)
        errs.extend(validate_edl_panel_groups(edl))
        if require_whiteboard and mp4.is_file() and mp4.stat().st_size >= 10_000:
            dur = max(1.0, probe_mp4_duration(mp4))
            ts = [min(dur - 0.1, i * (dur / 24.0)) for i in range(24)]
            frames = [f for _, f in decode_mp4_frames_at_times(mp4, ts)]
            wb = analyze_style24_edl_and_frames(edl, frames)
            if not wb["ok"]:
                errs.append(f"style24_wb_fail:edl={wb['edl_ratio']},fr={wb['frame_ratio']}")

    if require_gbro_covers:
        if not hf_p.is_file():
            errs.append("missing_09_gbro_cover_handoff.json")
        else:
            cov_res = validate_gbro_handoff_and_covers(_read_json(hf_p), c34_p, cf_p)
            if not cov_res["ok"]:
                errs.extend([f"cover:{e}" for e in cov_res["errors"][:3]])

    return (len(errs) == 0), errs


def _smoke_mini_track_validator(require_whiteboard: bool = False) -> Tuple[bool, str]:
    with tempfile.TemporaryDirectory() as td:
        tdir = Path(td)
        # 负样本项目（qa 失败 + 缺失产物 + 第8格黑卡）
        neg = tdir / "neg_proj"
        (neg / "pipeline").mkdir(parents=True)
        (neg / "out").mkdir(parents=True)
        (neg / "out" / "output_final.mp4").write_bytes(b"\x00" * 100)  # 过小
        (neg / "pipeline" / "10_qa_report.json").write_text(json.dumps({"all_passed": False}), encoding="utf-8")
        (neg / "pipeline" / "03b_director_plan.json").write_text(json.dumps({"style_scene_beats": [{"layout": {"panel_title": "夫妻店到雇人断层"}}]}, ensure_ascii=False), encoding="utf-8")

        # 正样本项目（全套必选产物齐备：MP4 + QA + 03_audio + 03b + 04_edl + 05_subtitles + 06_pkg + gbro covers）
        pos = tdir / "pos_proj"
        pdir_ae = _make_audio_events_fixture(pos, [("sent_001", "单店盈利模型完全跑通了", 0.0, 1.8)])
        (pos / "out").mkdir(parents=True, exist_ok=True)
        frames = [np.full((240, 180, 3), 235 if i < 4 else 25, dtype=np.uint8) for i in range(16)]
        write_synthetic_mp4(pos / "out" / "output_final.mp4", frames, fps=8)
        if (pos / "out" / "output_final.mp4").stat().st_size < 10_500:
            rng = np.random.default_rng(1)
            noisy = [np.clip((235 if i < 8 else 30) + rng.integers(-12, 13, size=(480, 270, 3)), 0, 255).astype(np.uint8) for i in range(32)]
            write_synthetic_mp4(pos / "out" / "output_final.mp4", noisy, fps=16)

        ae = _read_json(pdir_ae / "03_audio_events.json")
        base_plan = director_plan.generate_deterministic_director_plan(ae, style_id="style_24")
        pos_plan = dict(base_plan)
        pos_plan["cover_copy"] = {"headline_top": "单店盈利模型", "highlight_badge": "跑通复购", "sub_line": "", "gbro_style": "style-03"}
        pos_plan["semantic_subtitles"] = [{"text": "单店盈利模型", "keywords": ["盈利"]}, {"text": "完全跑通了", "keywords": []}]
        pos_plan["style_scene_beats"] = [{"target_sec": 0.2, "target_dur": 1.2, "layout": {"mode": "review_whiteboard_pip", "panel_title": "实测拆解"}}]
        pos_plan["style_callouts"] = [{"text": "跑通模型", "start_sec": 1.7, "end_sec": 2.0}]

        (pos / "pipeline" / "10_qa_report.json").write_text(json.dumps({"all_passed": True, "blackbox_details": {"checks": [{"id": "bb_no_visual_freeze", "status": "PASS"}]}}), encoding="utf-8")
        (pos / "pipeline" / "03b_director_plan.json").write_text(json.dumps(pos_plan, ensure_ascii=False), encoding="utf-8")
        (pos / "pipeline" / "05_subtitles.json").write_text(json.dumps({
            "entries": [
                {"id": "sub_1", "sentence_id": "sent_001", "text": "单店盈利模型", "start_sec": 0.0, "end_sec": 0.9},
                {"id": "sub_2", "sentence_id": "sent_001", "text": "完全跑通了", "start_sec": 0.9, "end_sec": 1.8},
            ]
        }, ensure_ascii=False), encoding="utf-8")
        (pos / "pipeline" / "06_packaging.json").write_text(json.dumps({"visual_elements": [], "sfx_cues": []}), encoding="utf-8")
        (pos / "pipeline" / "04_edl.json").write_text(json.dumps({
            "total_duration_sec": 2.0,
            "clips": [{"duration_sec": 0.6, "pip_layout": {"mode": "review_whiteboard_pip", "v11_panel_group_id": "g1", "v11_panel_group_dur": 3.2, "v11_panel_offset_sec": 0.0}}],
        }), encoding="utf-8")
        Image.new("RGB", (1080, 1440), (30, 32, 38)).save(pos / "pipeline" / "09_cover_3x4.jpg")
        Image.new("RGB", (1080, 1920), (20, 22, 28)).save(pos / "pipeline" / "09_cover.jpg")
        (pos / "pipeline" / "09_gbro_cover_handoff.json").write_text(json.dumps({
            "recommended_style_id": "style-03",
            "aspect_ratio": "3:4",
            "figure_1_face_ref": {"path": "09_host_face_ref.png"},
            "figure_2_content_refs": [{"path": "09_content_ref_01.png"}],
            "prompt_text": "3:4 竖版构图\n保留 10% 安全区。\n图2门店实景特写，图1人物推荐，标题「单店盈利模型」。",
        }, ensure_ascii=False), encoding="utf-8")

        neg_ok, neg_errs = _validate_single_project_artifacts(neg, require_whiteboard=require_whiteboard)
        pos_ok, pos_errs = _validate_single_project_artifacts(pos, require_whiteboard=require_whiteboard)
        ok = (not neg_ok) and pos_ok
        return ok, f"neg_errs={len(neg_errs)} pos_errs={pos_errs}"


def _run_cross_style_frame8_check(root_dir: Path, prefix: str) -> List[str]:
    errs: List[str] = []
    frame_map: Dict[str, np.ndarray] = {}
    with tempfile.TemporaryDirectory() as td:
        tdir = Path(td)
        img_paths: List[Path] = []
        sid_by_path: Dict[str, str] = {}
        for sid in ALL_24_STYLES:
            mp4 = root_dir / f"{prefix}{sid}" / "out" / "output_final.mp4"
            if not mp4.is_file():
                continue
            dur = max(1.0, probe_mp4_duration(mp4))
            decoded = decode_mp4_frames_at_times(mp4, [min(dur - 0.1, dur * 0.84)])
            if not decoded:
                continue
            arr = decoded[0][1]
            frame_map[sid] = arr
            p_img = tdir / f"{sid}_f8.png"
            Image.fromarray(arr).save(p_img)
            img_paths.append(p_img)
            sid_by_path[str(p_img)] = sid
        if frame_map:
            vis = vision_inspector.inspect_images(img_paths)
            ocr_map = {sid_by_path[str(p)]: vis.get(str(p), {}) for p in img_paths}
            f8_res = analyze_cross_style_frame8_uniqueness(frame_map, ocr_map)
            if not f8_res["ok"]:
                if f8_res["marker_hits"]:
                    errs.append(f"frame8_markers:{','.join(f8_res['marker_hits'][:3])}")
                if f8_res["shared_dark_cards"]:
                    errs.append(f"frame8_shared_cards:{len(f8_res['shared_dark_cards'])}")
    return errs


def t4_01_biz_full_24_styles(ctx: SuiteContext) -> CheckResult:
    if ctx.mode != "full":
        ok, info = _smoke_mini_track_validator(require_whiteboard=False)
        return CheckResult("T4-01", 4, "G5", "BIZ 全24套成片与 QA 批量校验器", "PASS" if ok else "FAIL", info, "smoke validator PASS", f"[{ctx.mode}] 微缩双态验收器通过（实盘在 --mode full 核查 24 套）")

    failures: List[str] = []
    for sid in ALL_24_STYLES:
        pdir = ctx.biz_root / f"v12_biz_{sid}"
        if not pdir.is_dir():
            failures.append(f"missing:{pdir.name}")
            continue
        ok_p, errs = _validate_single_project_artifacts(
            pdir, require_whiteboard=(sid == "style_24"), require_gbro_covers=True
        )
        if not ok_p:
            failures.append(f"{sid}:{'/'.join(errs[:2])}")
    failures.extend(_run_cross_style_frame8_check(ctx.biz_root, "v12_biz_"))
    ok = len(failures) == 0
    return CheckResult("T4-01", 4, "G5", "BIZ 全24套成片与 QA 批量验收", "PASS" if ok else "FAIL", f"failures={len(failures)}", "0 failures (24/24 PASS & unique F8)", ", ".join(failures[:6]) or "BIZ 24 套全部出片、封面与第8格均达标")


def t4_02_sto_full_24_styles_and_gbro_covers(ctx: SuiteContext) -> CheckResult:
    if ctx.mode != "full":
        ok, info = _smoke_mini_track_validator(require_whiteboard=True)
        return CheckResult("T4-02", 4, "G5", "STO 全24套成片、style_24白板与一菜一封验收器", "PASS" if ok else "FAIL", info, "smoke validator PASS", f"[{ctx.mode}] 微缩双态验收器通过（实盘在 --mode full 核查 24 套）")

    failures: List[str] = []
    hashes: Dict[str, int] = {}
    fig2_hashes: Dict[str, int] = {}
    prompts: Dict[str, str] = {}
    for sid in ALL_24_STYLES:
        pdir = ctx.sto_root / f"v12_sto_{sid}"
        if not pdir.is_dir():
            failures.append(f"missing:{pdir.name}")
            continue
        ok_p, errs = _validate_single_project_artifacts(
            pdir, require_whiteboard=(sid == "style_24"), require_gbro_covers=True
        )
        if not ok_p:
            failures.append(f"{sid}:{'/'.join(errs[:2])}")
        c34 = pdir / "pipeline" / "09_cover_3x4.jpg"
        if c34.is_file():
            hashes[sid] = image_dhash(c34)
        hf_p = pdir / "pipeline" / "09_gbro_cover_handoff.json"
        if hf_p.is_file():
            hf = _read_json(hf_p)
            pt = str(hf.get("prompt_text") or "").strip()
            if pt:
                prompts[sid] = pt
            for ref in (hf.get("figure_2_content_refs") or []):
                ref_raw = str(ref.get("path") if isinstance(ref, dict) else ref or "").strip()
                if not ref_raw:
                    continue
                cand = Path(ref_raw)
                if not cand.is_absolute():
                    cand = (pdir / "pipeline" / cand) if (pdir / "pipeline" / cand).is_file() else (pdir / cand)
                if cand.is_file():
                    fig2_hashes[sid] = image_dhash(cand)
                    break
    failures.extend(_run_cross_style_frame8_check(ctx.sto_root, "v12_sto_"))
    # 核验 STO 封面未退化为全套复用同一盘菜（贪心汉明距离 <= 6 聚类，24 封面至少 6 簇）
    distinct_clusters = len(cluster_dhashes(hashes, max_dist=6)) if hashes else 0
    req_clusters = max(3, len(hashes) // 4) if hashes else 0
    if hashes and distinct_clusters < req_clusters:
        failures.append(f"low_cover_diversity:{distinct_clusters}<{req_clusters}")
    if len(fig2_hashes) >= 4:
        fig2_clusters = len(cluster_dhashes(fig2_hashes, max_dist=6))
        req_fig2 = max(3, len(fig2_hashes) // 4)
        if fig2_clusters < req_fig2:
            failures.append(f"low_fig2_diversity:{fig2_clusters}<{req_fig2}")
    if len(prompts) >= 4:
        norm_prompts = {re.sub(r"\d+", "#", pt) for pt in prompts.values()}
        req_prompts = max(3, len(prompts) // 4)
        if len(norm_prompts) < req_prompts:
            failures.append(f"low_prompt_diversity:{len(norm_prompts)}<{req_prompts}")
    ok = len(failures) == 0
    return CheckResult("T4-02", 4, "G5", "STO 全24套成片、白板与一菜一封验收", "PASS" if ok else "FAIL", f"failures={len(failures)} clusters={distinct_clusters}", "0 failures & unique covers", ", ".join(failures[:6]) or "STO 24 套出片、style_24 白板、第8格与一菜一封全部达标")


def t4_03_benchmark_12_styles_frame_inspection(ctx: SuiteContext) -> CheckResult:
    if ctx.mode != "full":
        # smoke/pipeline 模式：校验 12 个标杆风格的 style_visual_truth 配置全部完整无冲突
        missing_b = [s for s in BENCHMARK_12_STYLES if not style_visual_truth.get_visual_truth(s)]
        s07_tr = style_visual_truth.get_visual_truth("style_07")
        s07_beats = s07_tr.get("scene_beats") or []
        ok = (len(missing_b) == 0) and (len(s07_beats) >= 1)
        return CheckResult("T4-03", 4, "G3", "12套标杆风格关键帧缺陷清零校验器", "PASS" if ok else "FAIL", f"benchmark={len(BENCHMARK_12_STYLES)-len(missing_b)}/12", "12/12 specs ready", f"[{ctx.mode}] 12 套标杆真值就绪（实盘在 --mode full 抽帧实测 MP4）")

    failures: List[str] = []
    stage_txt = (REPO_ROOT / "short-video-studio" / "assets" / "motion_templates" / "stage.html").read_text(encoding="utf-8")
    for pat in ("amb-orb-1", "amb-orb-2", "amb-sweep"):
        if pat in stage_txt:
            failures.append(f"stage_html:{pat}")

    with tempfile.TemporaryDirectory() as td:
        tdir = Path(td)
        for root_dir, prefix in ((ctx.biz_root, "v12_biz_"), (ctx.sto_root, "v12_sto_")):
            for sid in BENCHMARK_12_STYLES:
                proj = root_dir / f"{prefix}{sid}"
                pipe = proj / "pipeline"
                mp4 = proj / "out" / "output_final.mp4"
                edl_p = pipe / "04_edl.json"
                pkg_p = pipe / "06_packaging.json"
                if not mp4.is_file() or not edl_p.is_file() or not pkg_p.is_file():
                    failures.append(f"{proj.name}:missing_artifacts")
                    continue
                edl = _read_json(edl_p)
                pkg = _read_json(pkg_p)
                dur = max(1.0, probe_mp4_duration(mp4))

                # 1) 全片时长 0.5s 步长冻帧检测（不截断前 12s，不对实拍混合画面套纯背景光球判据）
                n_steps = max(2, int(dur / 0.5) + 1)
                sample_t = [min(max(0.0, dur - 0.05), i * 0.5) for i in range(n_steps)]
                frames = decode_mp4_frames_at_times(mp4, sample_t)
                frz_res = analyze_mp4_visual_freeze(frames)
                if not frz_res["freeze_ok"]:
                    failures.append(f"{proj.name}:freeze={frz_res['max_static_sec']}s")

                # 2) style_02 同 Y / 行数 / 复读 / 分词检测
                if sid == "style_02":
                    ks = pkg.get("kinetic_stage") or {}
                    if not ks.get("enabled"):
                        failures.append(f"{proj.name}:style02_ks_disabled")
                    else:
                        ks_res = analyze_style02_kinetic_items(ks.get("items") or [])
                        if not ks_res["ok"]:
                            failures.append(f"{proj.name}:style02_ks_fail")

                # 3) style_03/06/12 顶栏安全边距与 concept_diagram_pip 圆窗同步
                if sid in ("style_03", "style_06", "style_12"):
                    diag_clips = [c for c in (edl.get("clips") or []) if (c.get("pip_layout") or {}).get("mode") == "concept_diagram_pip"]
                    if diag_clips:
                        for dc in diag_clips:
                            spec = motion_html_renderer.build_panel_job_spec(dc["pip_layout"], dur_sec=float(dc.get("duration_sec") or 3.0), style_id=sid)
                            tb_res = analyze_topbar_and_circle_sync(pkg, edl, panel_spec=spec)
                            if not tb_res["ok"]:
                                failures.append(f"{proj.name}:topbar={tb_res['errors'][0]}")
                                break
                    else:
                        tb_res = analyze_topbar_and_circle_sync(pkg, edl)
                        if not tb_res["ok"]:
                            failures.append(f"{proj.name}:topbar={tb_res['errors'][0]}")

                # 4) style_06 右栏 SAFE_ZONE x<=880（合成成片改用 bbox + stage.html right>=200 + Apple Vision OCR，避免实拍误报）
                if sid == "style_06":
                    s06_idx = stage_txt.find('variant === "s06_vertical_poem"')
                    if s06_idx == -1:
                        failures.append(f"{proj.name}:stage_missing_s06_vertical_poem")
                    else:
                        s06_seg = stage_txt[s06_idx:s06_idx + 500]
                        m_right = re.search(r"right\s*:\s*(\d+)px", s06_seg)
                        if not m_right or int(m_right.group(1)) < (CANVAS_W - SAFE_X_MAX):
                            r_val = m_right.group(1) if m_right else "missing"
                            failures.append(f"{proj.name}:stage_s06_right={r_val}<{CANVAS_W - SAFE_X_MAX}")

                    poem_els = [e for e in (pkg.get("visual_elements") or []) if e.get("callout_variant") == "s06_vertical_poem" or e.get("kind") == "s06_vertical_poem"]
                    if not poem_els:
                        failures.append(f"{proj.name}:missing_s06_vertical_poem")
                    else:
                        fps_e = float(edl.get("fps") or 30.0)
                        t_poem = 0.5 * (int(poem_els[0].get("start_frame") or 0) + int(poem_els[0].get("end_frame") or 0)) / fps_e
                        dec = decode_mp4_frames_at_times(mp4, [min(dur - 0.1, max(0.0, t_poem))])
                        if not dec:
                            failures.append(f"{proj.name}:s06_frame_decode_failed")
                        else:
                            poem_fr = dec[0][1]
                            p06 = tdir / f"{proj.name}_s06.png"
                            Image.fromarray(poem_fr).save(p06)
                            vis06 = vision_inspector.inspect_images([p06]).get(str(p06), {})
                            s06_res = analyze_style06_safe_zone(pkg, poem_fr, use_pixel_roi=False, ocr_res=vis06)
                            if not s06_res["ok"]:
                                failures.append(f"{proj.name}:s06_safe={s06_res['errors'][0]}")

                # 5) style_15 通体纸底连续性与 t0 首帧可见性
                if sid == "style_15":
                    p15_clips = [c for c in (edl.get("clips") or []) if (c.get("pip_layout") or {}).get("mode") == "paper_outline_pip"]
                    if not p15_clips:
                        failures.append(f"{proj.name}:missing_paper_outline_pip")
                    else:
                        c0 = p15_clips[0]
                        t_in = float(c0.get("timeline_in_sec") or 0.0)
                        t_out = float(c0.get("timeline_out_sec") or (t_in + float(c0.get("duration_sec") or 2.0)))
                        dec15 = decode_mp4_frames_at_times(mp4, [min(dur - 0.1, t_in + 0.03), min(dur - 0.1, 0.5 * (t_in + t_out))])
                        if len(dec15) == 2:
                            t0_png = tdir / f"{proj.name}_s15_t0.png"
                            Image.fromarray(dec15[0][1]).save(t0_png)
                            vis15 = vision_inspector.inspect_images([t0_png]).get(str(t0_png), {})
                            r15 = analyze_style15_paper_frame(dec15[0][1], dec15[1][1], vis15)
                            if not r15["ok"]:
                                failures.append(f"{proj.name}:s15_paper_bot={r15['bot_p75_mid']}/ink={r15['dark_ink_pct_t0']}")

                # 6) style_04 全量巨字无截断
                if sid == "style_04":
                    g_clips = [c for c in (edl.get("clips") or []) if (c.get("pip_layout") or {}).get("mode") == "person_behind_giant_text_pip"]
                    if not g_clips:
                        failures.append(f"{proj.name}:missing_person_behind_giant_text_pip")
                    else:
                        gc = g_clips[0]
                        exp_txt = str((gc.get("pip_layout") or {}).get("giant_back_text") or (gc.get("pip_layout") or {}).get("panel_title") or "").strip()
                        if not exp_txt:
                            failures.append(f"{proj.name}:empty_giant_back_text")
                        else:
                            t_mid = 0.5 * (float(gc.get("timeline_in_sec") or 0.0) + float(gc.get("timeline_out_sec") or 1.0))
                            dec04 = decode_mp4_frames_at_times(mp4, [min(dur - 0.1, t_mid)])
                            if not dec04:
                                failures.append(f"{proj.name}:s04_frame_decode_failed")
                            else:
                                p04 = tdir / f"{proj.name}_s04.png"
                                Image.fromarray(dec04[0][1]).save(p04)
                                vis04 = vision_inspector.inspect_images([p04]).get(str(p04), {})
                                r04 = analyze_style04_giant_text(exp_txt, vis04)
                                if not r04["ok"]:
                                    failures.append(f"{proj.name}:s04_giant={r04['match_ratio']}")

                # 7) style_10 / style_16 逐元素置后分层与头顶无遮挡
                if sid in ("style_10", "style_16"):
                    top_els = [e for e in (pkg.get("visual_elements") or []) if e.get("kind") in ("center_burst_title", "stamp_tag")]
                    t_probe = 0.8
                    if top_els:
                        fps_e = float(edl.get("fps") or 30.0)
                        t_probe = 0.5 * (int(top_els[0].get("start_frame") or 0) + int(top_els[0].get("end_frame") or 30)) / fps_e
                    dec10 = decode_mp4_frames_at_times(mp4, [min(dur - 0.1, max(0.0, t_probe))])
                    fr10 = dec10[0][1] if dec10 else None
                    face_bb = None
                    if fr10 is not None:
                        p10 = tdir / f"{proj.name}_layer.png"
                        Image.fromarray(fr10).save(p10)
                        faces = vision_inspector.inspect_images([p10]).get(str(p10), {}).get("faces") or []
                        if faces:
                            face_bb = max(faces, key=lambda f: float((f.get("bbox") or {}).get("w", 0)) * float((f.get("bbox") or {}).get("h", 0))).get("bbox")
                    r10 = analyze_style10_16_layering(pkg, edl, fr10, face_bbox_norm=face_bb)
                    if not r10["ok"]:
                        failures.append(f"{proj.name}:layer={r10['errors'][0]}")

                # 8) style_07 WCAG >= 4.5
                if sid == "style_07":
                    s07_tr = style_visual_truth.get_visual_truth("style_07")
                    for b in (s07_tr.get("scene_beats") or []):
                        lay = b.get("layout") or {}
                        bg_hx = str(lay.get("bg_hex") or "#14151B")
                        fg_hx = str(lay.get("accent_hex") or "")
                        if fg_hx and not analyze_style07_wcag_frame_and_hex(fg_hx, bg_hx)["ok"]:
                            failures.append(f"{proj.name}:s07_hex={fg_hx}")
                            break
                    pain_clips = [c for c in (edl.get("clips") or []) if (c.get("pip_layout") or {}).get("mode") == "dark_circle_panel_pip"]
                    if not pain_clips:
                        failures.append(f"{proj.name}:missing_dark_circle_panel_pip")
                    else:
                        pc = pain_clips[0]
                        play = pc.get("pip_layout") or {}
                        t_p = 0.5 * (float(pc.get("timeline_in_sec") or 0.0) + float(pc.get("timeline_out_sec") or 1.0))
                        dec07 = decode_mp4_frames_at_times(mp4, [min(dur - 0.1, t_p)])
                        if not dec07:
                            failures.append(f"{proj.name}:s07_frame_decode_failed")
                        else:
                            r07 = analyze_style07_wcag_frame_and_hex(str(play.get("accent_hex") or "#FF8787"), str(play.get("bg_hex") or "#14151B"), dec07[0][1])
                            if not r07["ok"]:
                                failures.append(f"{proj.name}:s07_wcag={r07['hex_wcag']}/{r07['frame_wcag']}")

                # 9) style_03 / style_21 抠像无锯齿撕纸边（要求平滑 matte PNG 或真实代码库 accurate 契约，严禁静默跳过）
                if sid in ("style_03", "style_21"):
                    cutout_modes = {
                        "vision_portrait_cutout_pip",
                        "layered_cutout_over_broll",
                        "cutout_overlay_pip",
                        "pure_black_cutout_pip",
                        "data_news_cutout_pip",
                        "self_matting_behind_text",
                        "person_behind_giant_text_pip",
                    }
                    cutout_clips: List[Dict[str, Any]] = []
                    saw_torn_paper = False
                    bad_explicit_mq: Optional[str] = None
                    for cl in (edl.get("clips") or []):
                        pip = cl.get("pip_layout") or {}
                        if pip.get("torn_paper") is True:
                            saw_torn_paper = True
                        mq = pip.get("matting_quality")
                        if mq and str(mq).strip() != "accurate" and bad_explicit_mq is None:
                            bad_explicit_mq = str(mq).strip()
                        pip_mode_s = str(pip.get("mode") or "")
                        is_geom_cutout = False
                        if pip_mode_s.startswith("geometry_pip"):
                            try:
                                is_geom_cutout = (
                                    bool(pip.get("cutout"))
                                    and float((pip.get("main") or {}).get("scale") or 1.0) >= 0.999
                                )
                            except (TypeError, ValueError):
                                is_geom_cutout = False
                        if (pip_mode_s in cutout_modes and bool(pip.get("cutout", True))) or is_geom_cutout:
                            cutout_clips.append(cl)
                    if saw_torn_paper:
                        failures.append(f"{proj.name}:torn_paper=True")
                    if bad_explicit_mq is not None:
                        failures.append(f"{proj.name}:matting_quality={bad_explicit_mq}")

                    matte_pngs = sorted(pipe.glob("*matte*.png"))
                    verified_smooth_matte = False
                    if matte_pngs:
                        all_mattes_ok = True
                        for matte_png in matte_pngs[:2]:
                            with Image.open(matte_png) as mim:
                                alpha_arr = np.asarray(mim.convert("RGBA"))[:, :, 3] if mim.mode == "RGBA" else np.asarray(mim.convert("L"))
                            m_res = analyze_portrait_matte_smoothness(alpha_arr)
                            if not m_res["ok"]:
                                failures.append(f"{proj.name}:matte_jag={m_res['micro_jag']}")
                                all_mattes_ok = False
                                break
                        verified_smooth_matte = all_mattes_ok
                    if not cutout_clips and not matte_pngs:
                        failures.append(f"{proj.name}:missing_cutout_clip_or_matte")
                    elif cutout_clips and not matte_pngs:
                        rmp4_txt = (SCRIPTS_ROOT / "render_mp4.py").read_text(encoding="utf-8")
                        pm_txt = (SCRIPTS_ROOT / "person_matting.py").read_text(encoding="utf-8")
                        rmp4_acc = bool(
                            re.search(
                                r'quality\s*=\s*(?:["\']accurate["\']|(?:str\s*\(\s*|\(\s*)?pip_cfg\.get\(\s*["\']matting_quality["\'][^)\n]*(?:,\s*|\)\s*or\s*)["\']accurate["\'])',
                                rmp4_txt,
                            )
                        ) and ('quality="balanced"' not in rmp4_txt) and ('quality = "balanced"' not in rmp4_txt)
                        pm_acc = bool(re.search(r'quality\s*:\s*str\s*=\s*["\']accurate["\']', pm_txt))
                        no_forced_torn = (
                            ("torn_paper=True" not in rmp4_txt)
                            and ("torn_paper: bool = True" not in pm_txt)
                            and ("setValue(4.2" not in pm_txt)
                            and ("contrast=4.2" not in pm_txt)
                        )
                        if not (rmp4_acc and pm_acc and no_forced_torn):
                            failures.append(
                                f"{proj.name}:codebase_matting_not_accurate(rmp4={rmp4_acc},pm={pm_acc},no_torn={no_forced_torn})"
                            )

                # 10) style_24 杂志纸底白板占比
                if sid == "style_24":
                    ts24 = [min(dur - 0.1, i * (dur / 24.0)) for i in range(24)]
                    fr24 = [f for _, f in decode_mp4_frames_at_times(mp4, ts24)]
                    r24 = analyze_style24_edl_and_frames(edl, fr24)
                    if not r24["ok"]:
                        failures.append(f"{proj.name}:s24_wb=edl={r24['edl_ratio']},fr={r24['frame_ratio']}")

    ok = len(failures) == 0
    return CheckResult("T4-03", 4, "G3", "12套标杆风格关键帧缺陷清零实测", "PASS" if ok else "FAIL", f"failures={len(failures)}", "0 failures", ", ".join(failures[:6]) or "24 条标杆成片 R3/R4 关键帧与冻帧全检通过")


def t4_04_blind_3rd_material_e2e(ctx: SuiteContext) -> CheckResult:
    if ctx.mode != "full":
        with tempfile.TemporaryDirectory() as td:
            pdir = _make_audio_events_fixture(Path(td), [("sent_001", "嘉宝莉墙面漆三天焕新净味安全", 0.0, 3.0)])
            ae = _read_json(pdir / "03_audio_events.json")
            plan = director_plan.generate_deterministic_director_plan(ae, style_id="style_15")
            ok = director_plan.validate_director_plan(plan, ae).get("ok", False) and not detect_frame8_generic_black_card_in_plan(plan)
        return CheckResult("T4-04", 4, "G5", "第3条未知新素材零硬编码盲测", "PASS" if ok else "FAIL", f"plan_ok={ok}", "plan_ok=True", f"[{ctx.mode}] 未见域沙箱规划通过（实盘在 --mode full 核查 --blind-dir）")

    if not ctx.blind_dir or not ctx.blind_dir.exists():
        return CheckResult("T4-04", 4, "G5", "第3条未知新素材零硬编码盲测", "FAIL", "blind_dir=missing", "valid --blind-dir", "full 模式必须提供有效 --blind-dir")
    ok_p, errs = _validate_single_project_artifacts(ctx.blind_dir, require_whiteboard=False, require_gbro_covers=True)
    t_leak = t1_g5_05_blind_material_zero_leakage(ctx)
    ok = ok_p and (t_leak.status == "PASS")
    return CheckResult("T4-04", 4, "G5", "第3条未知新素材零硬编码盲测", "PASS" if ok else "FAIL", f"art_ok={ok_p} leak_ok={t_leak.status}", "both=PASS", ", ".join(errs) or t_leak.detail)


def t4_05_r6_cleanup_and_symlinks_e2e(ctx: SuiteContext) -> CheckResult:
    r_sub = t2_g5_05_r6_cleanup_boundary(ctx)
    r_sym = t1_g5_04_symlink_and_mustkeep_integrity(ctx)
    ok = (r_sub.status == "PASS") and (r_sym.status == "PASS")
    return CheckResult("T4-05", 4, "G5", "R6 历史过程安全清理与软链零断链", "PASS" if ok else "FAIL", f"cleanup={r_sub.status} symlinks={r_sym.status}", "both=PASS", f"{r_sub.detail}; {r_sym.detail}")


def t4_06_final_regression_parity_and_hygiene_e2e(ctx: SuiteContext) -> CheckResult:
    r_base = t2_g5_04_baseline_devtools_and_cli_help(ctx)
    r_priv = t1_g5_01_repo_zero_personal_paths(ctx)
    r_draft = t1_g5_02_repo_zero_draft_names(ctx)
    r_git = t1_g5_03_git_branch_and_no_teamwork_in_repo(ctx)
    ut_info = "smoke/pipeline skipped full unittest"
    ut_ok = True
    if ctx.mode == "full":
        res = subprocess.run([ctx.py_bin, "-m", "unittest", "discover", "-s", "tests"], cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=600)
        m = re.search(r"Ran (\d+) tests", res.stderr + res.stdout)
        ran_n = int(m.group(1)) if m else 0
        ut_ok = (res.returncode == 0) and (ran_n >= 616)
        ut_info = f"unittest ran={ran_n} rc={res.returncode}"
    ok = all(r.status == "PASS" for r in (r_base, r_priv, r_draft, r_git)) and ut_ok
    return CheckResult(
        "T4-06", 4, "G5", "全量单测(>=616)、一致性与仓库红线总门禁",
        "PASS" if ok else "FAIL",
        f"devtools={r_base.status} priv={r_priv.status} draft={r_draft.status} git={r_git.status} {ut_info}",
        "all PASS & unittest>=616 in full",
        r_base.detail if ok else f"base={r_base.detail} priv={r_priv.detail} draft={r_draft.detail} git={r_git.detail} {ut_info}",
    )


# ---------------------------------------------------------------------------
# 注册全部 71 个测试项（30 Tier1 + 28 Tier2 + 7 Tier3 + 6 Tier4）
# ---------------------------------------------------------------------------
ALL_CHECKS: List[CheckSpec] = [
    # Tier 1 (30)
    CheckSpec("T1-G1-01", 1, "G1", "通用断句零挂尾助词检测", t1_g1_01_dangling_particles),
    CheckSpec("T1-G1-02", 1, "G1", "零助词领起与零残句碎片检测", t1_g1_02_aux_head_and_fragments),
    CheckSpec("T1-G1-03", 1, "G1", "零劈裂数字量词与分数检测", t1_g1_03_split_numbers_compounds),
    CheckSpec("T1-G1-04", 1, "G1", "style_02 同Y碰撞与多行堆叠检测", t1_g1_04_style02_ylane_and_stacking),
    CheckSpec("T1-G1-05", 1, "G1", "qa_inspector 通用字幕切分干净度验证", t1_g1_05_qa_inspector_seg_check),
    CheckSpec("T1-G1-06", 1, "G1", "静态字幕硬编码表废除门禁", t1_g1_06_abolition_subtitles_tables),
    CheckSpec("T1-G2-01", 1, "G2", "v11_director_planner 静态块废除门禁", t1_g2_01_abolition_director_blocks),
    CheckSpec("T1-G2-02", 1, "G2", "03b_director_plan 通用契约校验", t1_g2_02_director_plan_schema),
    CheckSpec("T1-G2-03", 1, "G2", "零第8格通用黑卡注入检测", t1_g2_03_zero_frame8_generic_black_card),
    CheckSpec("T1-G2-04", 1, "G2", "gbro-cover-design 10套风格与提示词契约", t1_g2_04_gbro_10_style_prompt_contract),
    CheckSpec("T1-G2-05", 1, "G2", "09_gbro_cover_handoff 多图契约检测", t1_g2_05_gbro_handoff_multiref),
    CheckSpec("T1-G2-06", 1, "G2", "封面 1080x1440/1920 尺寸与感知哈希唯一性", t1_g2_06_cover_geometry_and_uniqueness),
    CheckSpec("T1-G3-01", 1, "G3", "KTV 光球清理与色度漂移检测", t1_g3_01_ktv_orbs_detector),
    CheckSpec("T1-G3-02", 1, "G3", "bb_no_visual_freeze 冻帧微动效检测", t1_g3_02_no_visual_freeze_detector),
    CheckSpec("T1-G3-03", 1, "G3", "top_bar 安全边距与圆窗同步检测", t1_g3_03_topbar_clearance_and_circle_sync),
    CheckSpec("T1-G3-04", 1, "G3", "style_15 通体纸底连续性与 t=0 首帧可见性", t1_g3_04_style15_paper_continuity_and_t0),
    CheckSpec("T1-G3-05", 1, "G3", "style_04 巨字完整度 Apple Vision OCR 检测", t1_g3_05_style04_full_giant_text),
    CheckSpec("T1-G3-06", 1, "G3", "style_10/16 人像抠像置顶分层检测", t1_g3_06_style10_16_portrait_top_layering),
    CheckSpec("T1-G3-07", 1, "G3", "style_07 sRGB WCAG>=4.5 与 style_06 右栏安全区", t1_g3_07_style07_wcag_and_style06_safezone),
    CheckSpec("T1-G3-08", 1, "G3", "style_03/21 抠像边缘羽化平滑度检测", t1_g3_08_style03_21_smooth_matting),
    CheckSpec("T1-G4-01", 1, "G4", "04_edl 跨镜头连续面板分组检测", t1_g4_01_edl_multiclip_panel_span),
    CheckSpec("T1-G4-02", 1, "G4", "STO style_24 杂志纸底白板占比检测", t1_g4_02_sto_style24_whiteboard_ratio),
    CheckSpec("T1-G4-03", 1, "G4", "全24套第8格唯一性与零串味检测", t1_g4_03_cross_style_frame8_uniqueness),
    CheckSpec("T1-G4-04", 1, "G4", "原生风格基因保留与非同质化检测", t1_g4_04_native_style_dna_preservation),
    CheckSpec("T1-G4-05", 1, "G4", "零双花字时空重叠检测", t1_g4_05_zero_dual_callout_collision),
    CheckSpec("T1-G5-01", 1, "G5", "生产源码与新增文件零个人路径扫描", t1_g5_01_repo_zero_personal_paths),
    CheckSpec("T1-G5-02", 1, "G5", "生产源码与新增文件零本地草稿名扫描", t1_g5_02_repo_zero_draft_names),
    CheckSpec("T1-G5-03", 1, "G5", "Git 分支隔离与仓库内零 .agents/teamwork 守卫", t1_g5_03_git_branch_and_no_teamwork_in_repo),
    CheckSpec("T1-G5-04", 1, "G5", "01_normalized 软链与核心资产完整性守卫", t1_g5_04_symlink_and_mustkeep_integrity),
    CheckSpec("T1-G5-05", 1, "G5", "第3条未知盲测素材零 BIZ/STO 串味泄漏", t1_g5_05_blind_material_zero_leakage),
    # Tier 2 (28)
    CheckSpec("T2-G1-01", 2, "G1", "18+字长句自动折行边界", t2_g1_01_long_clause_18plus_wrap),
    CheckSpec("T2-G1-02", 2, "G1", "空串/纯标点与<=3字短句边界", t2_g1_02_short_and_empty_input),
    CheckSpec("T2-G1-03", 2, "G1", "高密度粤语助词(嘅/咗/喺/啲/哋/嚟)边界", t2_g1_03_heavy_cantonese_particles),
    CheckSpec("T2-G1-04", 2, "G1", "跨句 ASR 截断重连边界", t2_g1_04_cross_sentence_asr_split),
    CheckSpec("T2-G1-05", 2, "G1", "style_02 list_gather_depth 急促连句边界", t2_g1_05_style02_list_gather_depth_rapid),
    CheckSpec("T2-G1-06", 2, "G1", "step5_subtitles.py 黑盒 CLI 沙箱执行", t2_g1_06_step5_and_style02_cli_calibration),
    CheckSpec("T2-G2-01", 2, "G2", "director_plan.py 极短 2 句边界 CLI", t2_g2_01_director_minimal_2_sentences),
    CheckSpec("T2-G2-02", 2, "G2", "120s+ 长口播动态导演规划边界", t2_g2_02_director_long_120s_transcript),
    CheckSpec("T2-G2-03", 2, "G2", "零外部 B-roll 退化动态图解面板升级", t2_g2_03_zero_broll_diagram_upgrade),
    CheckSpec("T2-G2-04", 2, "G2", "gbro style-01 缺图2容错 vs style-03 强制图2", t2_g2_04_gbro_fig2_optional_vs_mandatory),
    CheckSpec("T2-G2-05", 2, "G2", "director_plan apply-plan 非法拒绝与合法放行", t2_g2_05_director_apply_plan_rejects_corrupt),
    CheckSpec("T2-G2-06", 2, "G2", "双 STO 视频封面菜品匹配与非复用边界", t2_g2_06_sto_cross_video_dish_uniqueness),
    CheckSpec("T2-G3-01", 2, "G3", "PyAV 边界帧时刻 (t=0, +0.03s, end-0.05s) 解码", t2_g3_01_boundary_timestamps_pyav),
    CheckSpec("T2-G3-02", 2, "G3", "WCAG 4.5:1 对比度精确边界测试", t2_g3_02_contrast_edge_449_vs_451),
    CheckSpec("T2-G3-03", 2, "G3", "style_06 右边界 x2=880 vs 881 临界测试", t2_g3_03_style06_x880_vs_x881_boundary),
    CheckSpec("T2-G3-04", 2, "G3", "style_04 4字/6字/10字 OCR 长度边界", t2_g3_04_style04_char_length_boundary),
    CheckSpec("T2-G3-05", 2, "G3", "top_bar 底缘 y=318 碰撞 vs y=340 安全边界", t2_g3_05_topbar_y318_vs_y340_boundary),
    CheckSpec("T2-G3-06", 2, "G3", "二值锯齿蒙版 vs 4px 羽化蒙版边界", t2_g3_06_binary_jagged_vs_feathered_matte),
    CheckSpec("T2-G4-01", 2, "G4", "短 B-roll 碎片(<3.2s) 跨镜聚合边界", t2_g4_01_short_broll_segments_span),
    CheckSpec("T2-G4-02", 2, "G4", "A->B->A 混合切镜面板 offset 连续性边界", t2_g4_02_mixed_aroll_broll_aroll_group_continuity),
    CheckSpec("T2-G4-03", 2, "G4", "style_24 白板占比 0% / 14.1% / 15.1% 阈值边界", t2_g4_03_whiteboard_0_vs_141_vs_151_boundary),
    CheckSpec("T2-G4-04", 2, "G4", "frac=0.84 第8格通用卡注入边界", t2_g4_04_blocks4_frame8_frac84_injection),
    CheckSpec("T2-G4-05", 2, "G4", "style_15 结尾纸底保留不被第8格黑卡覆盖", t2_g4_05_style15_native_ending_preserved),
    CheckSpec("T2-G5-01", 2, "G5", "个人路径泄漏注入自检", t2_g5_01_synthetic_personal_path_leak_selftest),
    CheckSpec("T2-G5-02", 2, "G5", "剪映草稿名泄漏注入自检", t2_g5_02_synthetic_draft_name_leak_selftest),
    CheckSpec("T2-G5-03", 2, "G5", "悬空 01_normalized 软链检出边界", t2_g5_03_broken_symlink_selftest),
    CheckSpec("T2-G5-04", 2, "G5", "基线 dev_tools 门禁与 10 大流水线 CLI --help", t2_g5_04_baseline_devtools_and_cli_help),
    CheckSpec("T2-G5-05", 2, "G5", "R6 历史过程文件安全清理边界", t2_g5_05_r6_cleanup_boundary),
    # Tier 3 (7)
    CheckSpec("T3-01", 3, "G3", "persistent_top_bar + concept_diagram_pip 交互", t3_01_topbar_plus_concept_diagram_pip),
    CheckSpec("T3-02", 3, "G1", "style_02 + 通用DP分词 + list_gather_depth 交互", t3_02_style02_dp_segmenter_plus_list_gather),
    CheckSpec("T3-03", 3, "G3", "style_10/16 顶格大字 + 抠像置顶交互", t3_03_style10_16_callout_plus_accurate_matting),
    CheckSpec("T3-04", 3, "G4", "style_24 白板 + B-roll 切镜 + v11_panel_group_id 交互", t3_04_style24_broll_intercuts_plus_panel_group),
    CheckSpec("T3-05", 3, "G3", "style_15 通体纸底 + 底部字幕对比度交互", t3_05_style15_paper_plus_bottom_subtitles),
    CheckSpec("T3-06", 3, "G2", "director_plan + step9 gbro 多参考图联动", t3_06_director_plan_plus_step9_gbro_handoff),
    CheckSpec("T3-07", 3, "G3", "精致微动效 MP4 + bb_no_visual_freeze 联合校验", t3_07_micro_motion_plus_bb_no_visual_freeze),
    # Tier 4 (6)
    CheckSpec("T4-01", 4, "G5", "BIZ 全24套成片与 QA 批量验收", t4_01_biz_full_24_styles),
    CheckSpec("T4-02", 4, "G5", "STO 全24套成片、白板与一菜一封验收", t4_02_sto_full_24_styles_and_gbro_covers),
    CheckSpec("T4-03", 4, "G3", "12套标杆风格关键帧缺陷清零实测", t4_03_benchmark_12_styles_frame_inspection),
    CheckSpec("T4-04", 4, "G5", "第3条未知新素材零硬编码盲测", t4_04_blind_3rd_material_e2e),
    CheckSpec("T4-05", 4, "G5", "R6 历史过程安全清理与软链零断链", t4_05_r6_cleanup_and_symlinks_e2e),
    CheckSpec("T4-06", 4, "G5", "全量单测(>=616)、一致性与仓库红线总门禁", t4_06_final_regression_parity_and_hygiene_e2e),
]


def _default_account_projects_root(preferred_names: Sequence[str]) -> Path:
    accounts_dir = Path.home() / ".short-video-studio" / "workspaces" / "accounts"
    for name in preferred_names:
        cand = accounts_dir / name / "projects"
        if cand.is_dir():
            return cand
    return accounts_dir / preferred_names[0] / "projects"


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="shortVideo-CCstudio V12 独立黑盒端到端测试套件")
    parser.add_argument("--mode", choices=("smoke", "pipeline", "full"), default="smoke")
    parser.add_argument("--tier", choices=("1", "2", "3", "4", "all"), default="all")
    parser.add_argument("--feature", choices=("all", "G1", "G2", "G3", "G4", "G5"), default="all")
    parser.add_argument("--biz-root", type=Path, default=None, help="BIZ 项目根目录（默认动态定位）")
    parser.add_argument("--sto-root", type=Path, default=None, help="STO 项目根目录（默认动态定位）")
    parser.add_argument("--blind-dir", type=Path, default=None, help="第 3 条未知盲测项目目录")
    parser.add_argument("--json-out", type=Path, default=None, help="输出结构化 JSON 报告路径")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    biz_root = args.biz_root or _default_account_projects_root(("demo-style02",))
    sto_root = args.sto_root or _default_account_projects_root(("ouyang-test",))
    ctx = SuiteContext(
        mode=args.mode,
        biz_root=biz_root,
        sto_root=sto_root,
        blind_dir=args.blind_dir,
    )

    selected = [
        spec for spec in ALL_CHECKS
        if (args.tier == "all" or spec.tier == int(args.tier))
        and (args.feature == "all" or spec.group == args.feature)
    ]

    t_start = time.monotonic()
    results: List[CheckResult] = []
    for spec in selected:
        try:
            res = spec.fn(ctx)
        except Exception as exc:
            res = CheckResult(
                id=spec.id,
                tier=spec.tier,
                group=spec.group,
                name=spec.name,
                status="FAIL",
                measured="exception",
                threshold="no_exception",
                detail=f"{type(exc).__name__}: {exc}",
            )
        results.append(res)
        mark = "PASS" if res.status == "PASS" else "FAIL"
        print(f"[{mark}] {res.id} (T{res.tier}/{res.group}) {res.name} | {res.measured} | {res.detail}")

    passed = sum(1 for r in results if r.status == "PASS")
    failed = sum(1 for r in results if r.status == "FAIL")
    skipped = 0
    dur = round(time.monotonic() - t_start, 3)

    report = {
        "mode": args.mode,
        "tier": args.tier,
        "feature": args.feature,
        "total": len(results),
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "duration_sec": dur,
        "results": [asdict(r) for r in results],
    }

    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        f"\n[V12 E2E SUMMARY] mode={args.mode} tier={args.tier} feature={args.feature} "
        f"total={len(results)} passed={passed} failed={failed} skipped={skipped} duration={dur}s"
    )
    return 0 if (failed == 0 and skipped == 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
