#!/usr/bin/env python3
"""
[Step 6: 包装对齐与预算控制]
1. 修复限幅符号写反 Bug：采用上限 min(-3.0, ...) 确保真实流派音量梯度生效，06_packaging.json 与 WAV 实感完全一致。
2. Freesound CC0 真实物理材质音效排在数学合成兜底之前，同族轮换防疲劳。
3. 视觉包装严格遵守预算上限 max(3, ceil(duration_sec / 3.5))，并写入 style_id 与基于 01_manifest 裁切人脸的权威避脸 final_bbox。
输出：pipeline/06_packaging.json 与 pipeline/06_sfx_track.wav
"""

from __future__ import annotations

import argparse
import math
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from common import (
    ASSETS_DIR,
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    DEFAULT_FPS,
    SAFE_ZONE,
    SUBTITLE_CENTER_X,
    SUBTITLE_SYMMETRIC_MAX_W,
    bbox_iou,
    check_bbox_in_safe_zone,
    contrast_ratio,
    estimate_read_hold_sec,
    find_commercial_font,
    fit_text_line,
    frames_to_sec,
    hex_to_rgba,
    load_animation_sfx_pairs,
    load_sfx_intent_taxonomy,
    load_style_applied,
    load_style_spectrum_entry,
    local_assets_dir,
    maybe_reexec_in_venv,
    parse_manifest_faces,
    read_json,
    resolve_asset_path,
    write_json,
)

if __name__ == "__main__":
    maybe_reexec_in_venv()

import numpy as np
from step3_lock_audio import SR, load_stereo_wav_np, save_stereo_wav_np
from flower_policy import NUM_MEASURE_RE, FlowerPolicy, text_quality_issue
from flower_policy import format_table as format_flower_table
from sfx_policy import SfxPolicy
from sfx_policy import format_table as format_sfx_table


_SFX_ROLES = ("cut", "hook", "info", "accent", "receipt", "misc")
_SFX_EXTS = (".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg")


def style_sfx_dir(style_id: str) -> Path:
    sid = str(style_id or "").strip()
    primary = ASSETS_DIR / "sound_vibe" / "sfx" / sid
    if primary.is_dir() and any(primary.iterdir()):
        return primary
    loc = local_assets_dir(ASSETS_DIR)
    if loc is not None:
        loc_dir = loc / "sound_vibe" / "sfx" / sid
        if loc_dir.is_dir():
            return loc_dir
    return primary


def scan_style_sfx(style_id: str) -> Dict[str, List[Path]]:
    """本风格自己的真音效池：sound_vibe/sfx/<style_id>/<角色>_<原音效名>.<ext>。

    实测事故根因：旧版把兜底写死成 style_01_*/style_15_* 的真音效和其他风格的合成音，
    于是 style_21 的成片里每 1.5 秒塞一个外来「叮」。这里**只读本风格目录**
    （优先随仓，自动合并本机 ~/.short-video-studio/local_assets/sound_vibe/sfx/<style_id>/），
    本风格一条都没有就返回空字典（由调用方直接关掉音效），绝不做跨风格兜底。
    """
    sid = str(style_id or "").strip()
    pool: Dict[str, List[Path]] = {r: [] for r in _SFX_ROLES}
    seen_names: set = set()
    search_dirs: List[Path] = [ASSETS_DIR / "sound_vibe" / "sfx" / sid]
    loc = local_assets_dir(ASSETS_DIR)
    if loc is not None:
        search_dirs.append(loc / "sound_vibe" / "sfx" / sid)
    for d in search_dirs:
        if not d.is_dir():
            continue
        for p in sorted(d.iterdir()):
            if not p.is_file() or p.suffix.lower() not in _SFX_EXTS:
                continue
            if p.name in seen_names:
                continue
            seen_names.add(p.name)
            role = p.stem.split("_", 1)[0]
            if role not in pool:
                role = "misc"
            pool[role].append(p)
    # 用户自备原版音效按角色名覆盖：sound_vibe/user/<style_id>/cut.mp3
    user_dirs = [ASSETS_DIR / "sound_vibe" / "user" / sid]
    if loc is not None:
        user_dirs.append(loc / "sound_vibe" / "user" / sid)
    for user_dir in user_dirs:
        if not user_dir.is_dir():
            continue
        for r in _SFX_ROLES:
            hit = next((p for p in sorted(user_dir.iterdir())
                        if p.is_file() and p.suffix.lower() in _SFX_EXTS and p.stem.lower() == r), None)
            if hit is not None:
                pool[r] = [hit]
    return {r: v for r, v in pool.items() if v}


def _role_candidates(pool: Dict[str, List[Path]], role: str) -> List[Path]:
    """取本风格该角色的全部候选；本风格缺这个角色就用本风格其它角色（不跨风格）。"""
    if not pool:
        return []
    if pool.get(role):
        return list(pool[role])
    for r in _SFX_ROLES:
        if pool.get(r):
            return list(pool[r])
    return []


def _default_cc0_sfx(role: str, style_id: str) -> Optional[Path]:
    """本风格内的单条兜底音效；本风格整包为空则返回 None（不再借别的风格）。"""
    cands = _role_candidates(scan_style_sfx(style_id), role)
    return cands[0] if cands else None


def load_style_sfx_truth(style_id: str) -> Optional[Dict[str, Any]]:
    """源草稿音效实测表：assets/sound_vibe/sfx_truth/<style_id>.json。"""
    p = ASSETS_DIR / "sound_vibe" / "sfx_truth" / f"{style_id}.json"
    if not p.is_file():
        return None
    try:
        return read_json(p)
    except Exception:
        return None


def load_style_bgm_info(style_id: str) -> Dict[str, Any]:
    """本风格 BGM 归属（只读本风格自己的 manifest 节点），绝不指向别的风格。"""
    info: Dict[str, Any] = {
        "file": None,
        "status": None,
        "name": None,
        "dual_bgm": None,
        "switch_sec": None,
        "disabled_reason": None,
    }
    node: Dict[str, Any] = {}
    try:
        mf = read_json(ASSETS_DIR / "sound_vibe" / "vibe_sound_manifest.json")
        node = (mf.get("styles") or {}).get(str(style_id)) or {}
    except Exception:
        node = {}
    rel = node.get("bgm_wav")
    if rel and not node.get("bgm_is_silence_placeholder"):
        hit = resolve_asset_path(str(rel), ASSETS_DIR)
        if hit is not None and hit.is_file():
            info["file"] = str(rel)
        else:
            cc0_rel = f"sound_vibe/bgm/{style_id}_bgm.m4a"
            if (ASSETS_DIR / cc0_rel).is_file():
                info["file"] = cc0_rel
    info["status"] = node.get("bgm_status")
    info["name"] = node.get("bgm_name")
    info["dual_bgm"] = node.get("bgm_dual")
    info["switch_sec"] = node.get("bgm_switch_sec")
    if info["file"] is None:
        info["disabled_reason"] = (
            node.get("bgm_disabled_reason") or f"{style_id} 源草稿无 BGM，本片不放 BGM"
        )
    return info


_FLOWER_FILLER_PREFIXES = (
    "介绍一下", "给大家介绍", "我介绍一下", "我觉得", "其实", "一个", "因为", "所以",
    "可以", "就是", "然后", "那么", "这个", "那个", "大家看", "你看", "你们看",
    "今天", "现在", "咱们", "我们", "这里", "这边", "还有", "而且", "但是", "不过",
)
_FLOWER_BAN_EXACT = {"介绍", "一下", "一个", "这个", "那个", "觉得", "可以", "就是", "然后", "因为", "所以", "非常", "真的", "很"}


def _extract_clean_flower_phrase(
    sent: Dict[str, Any],
    raw_pain_kws: Optional[List[str]] = None,
    max_len: int = 8,
) -> str:
    """
    高质量花字短语提炼（根治实测 #4“介绍一下/我觉得”半词碎片）：
    1. 优先命中一手原始痛点词；
    2. 其次提取完整「数字+单位+核心名词」短语；
    3. 其次从句子 keywords 中跳过虚词/半词；
    4. 最后剥离口语虚词前缀，取完整语义短语，绝不硬截残词。
    """
    txt = str(sent.get("text") or "").strip()
    clean_txt = re.sub(r"[，。！？、；：“”\"'（）()【】\[\]\s]+", "", txt)
    for pk in (raw_pain_kws or []):
        pks = str(pk).strip()
        if 2 <= len(pks) <= max_len and pks in txt and pks not in _FLOWER_BAN_EXACT:
            return pks

    # 提取数字+量词/核心实体短语（如 25张卡座、8个包房、99元双人餐；避免「三百万喺金湾开」在「开/嘅/咗」处断句）
    num_m = re.search(r"([零一二两三四五六七八九十百千万\d]+(?:\.\d+)?[个张间家次倍元块万亿%％折平米天小时分钟秒](?:[喺在嘅的][^，。！？\s]{0,2}|[\u4e00-\u9fff]{0,3}))", clean_txt)
    if num_m:
        cand_nm = num_m.group(1)
        if len(cand_nm) > max_len or cand_nm.endswith(("开", "做", "去", "系", "是", "有", "摆")):
            cand_nm = cand_nm[:-1] if len(cand_nm) >= 3 else cand_nm
        if 2 <= len(cand_nm) <= max_len:
            return cand_nm

    for kw in _display_keywords(sent):
        ks = str(kw).strip()
        if len(ks) < 2 or ks in _FLOWER_BAN_EXACT:
            continue
        if any(ks.startswith(p) for p in _FLOWER_FILLER_PREFIXES):
            continue
        if len(ks) <= max_len:
            return ks

    # 剥离常见口语虚词开头后按自然子句提取
    clauses = [c.strip() for c in re.split(r"[，。！？、；：,\.!\?;:]+", txt) if c.strip()]
    best_clause = ""
    for cl in clauses:
        c = cl
        changed = True
        while changed:
            changed = False
            for pref in _FLOWER_FILLER_PREFIXES:
                if c.startswith(pref) and len(c) > len(pref) + 1:
                    c = c[len(pref):].lstrip("的了呢啊呀吧")
                    changed = True
        c = c.strip("的了呢啊呀吧")
        if 2 <= len(c) <= max_len:
            return c
        if len(c) > len(best_clause):
            best_clause = c
    if best_clause:
        return _cut_at_word_boundary(best_clause, max_len)
    return _cut_at_word_boundary(clean_txt, max_len) or "核心亮点"


def _cut_at_word_boundary(text: str, max_len: int) -> str:
    """按词边界截断，绝不把词劈成两半。

    2026-11-24 实测：`我哋嘅四大天王虾饺…` 硬截 8 字变成「我哋嘅四大天王虾」，
    上屏就是个残词。用 jieba 切词后往后累加，最后一个能装下的完整词收口。
    """
    t = str(text or "")
    if len(t) <= max_len:
        return t
    try:
        import jieba  # type: ignore
        toks = [w for w in jieba.lcut(t) if w]
    except Exception:
        toks = [t[i:i + 2] for i in range(0, len(t), 2)]
    out = ""
    for w in toks:
        if len(out) + len(w) > max_len:
            break
        out += w
    if len(out) >= 2:
        return out
    return t[:max_len]


# 花字不是「句子前几个字」。源草稿里 24 套的花字全部是**名词性短语或数字**（中位 4 字，1~5 字），
# 从来没有把半句话搬上屏的。以前按词边界截前 5 字，实测会打出「我系牛哥我」「上边摆好多」这种半句话，
# 观众一眼就看出是机器拼的——这也是「看着乱」的一部分。
_FLOWER_LEAD_STOP = (
    "我", "你", "佢", "他", "她", "我哋", "你哋", "佢哋", "呢", "嗰", "咁", "就", "都", "仲", "又",
    "係", "系", "嘅", "既", "有", "冇", "同", "跟", "同埋", "而", "但", "因为", "所以", "如果",
    "上边", "下边", "左边", "右边", "前边", "后边", "其实", "即系", "可以", "应该", "比较", "非常",
)
_FLOWER_BAD_TAIL = ("嘅", "系", "係", "同", "跟", "既", "嘅话", "啦", "喇", "嗰", "呢")
# 场记口令：这些字本身不是内容，绝不上屏（实测曾把「三二一组」打成花字）
CUE_WORDS = {
    "三二一", "一二三", "三二一组", "开始", "停", "嚟啦", "来啦", "预备", "起", "action",
    "唔知道大家", "大家好", "大家", "各位", "观众",
}


def _flower_label_for_style(label: str, chars_max: int) -> str:
    """花字文案收口：只留「数字核心」或「一个完整名词短语」，拼不出来就返回空串（这张不出）。

    返回空串是明确表态：这句里没有能当花字的词，宁可不打，也不要拿半句话糊上去。
    """
    t = str(label or "").strip()
    cap = max(2, int(chars_max or 10))
    # 口令/场记不上屏（三二一、嚟啦、开始、唔知道大家这种）
    if re.fullmatch(r"[一二三四五六七八九十0-9\s、，,。.！!？?~～]+", t or "") or t in CUE_WORDS:
        return ""
    m = NUM_MEASURE_RE.search(t)
    if m:
        core = re.sub(r"[嘅的]$", "", m.group(0))
        if 2 <= len(core) <= cap:
            return core
    if not t:
        return ""
    # 用词性挑「名词性短语」：源草稿里花字全部是名词/名词串（卡座、中空、岭南元素、招牌特色）
    # 或者数字（三百万、两百平），没有一条是动词短语或半句话。
    _NOUN_TAGS = ("n", "nr", "ns", "nt", "nz", "j", "l", "ng", "vn", "an")
    best = ""
    try:
        import jieba.posseg as pseg  # type: ignore

        pairs = [(str(w), str(f)) for w, f in pseg.cut(t)]
    except Exception:
        pairs = []
    if pairs:
        run: List[str] = []
        for w, f in pairs + [("", "x")]:
            # 数字不进名词串：数字单独走 NUM_MEASURE_RE（「三百万／两百平／十几年」），
            # 混进来会拼出「多卡座一个」这种不是人话的东西。
            is_noun = (f.startswith("n") or f in _NOUN_TAGS) and f != "m"
            if is_noun and w and len("".join(run)) + len(w) <= cap:
                run.append(w)
                continue
            cand = "".join(run).strip("我你佢他她哋嘅个咗啲嘅")
            run = [w] if (is_noun and w) else []
            if 2 <= len(cand) <= cap and len(cand) > len(best):
                best = cand
    if pairs:
        # 词性判断可用时只认名词串：没挑出来就是这句里没有能当花字的词，宁可不出。
        if best and not any(best.startswith(x) for x in _FLOWER_LEAD_STOP) and len(best) >= 2:
            return best
        return ""
    # 词性判断不可用时（没装 jieba.posseg）退回「按词边界取最长的干净词」
    try:
        import jieba  # type: ignore

        toks = [w.strip() for w in jieba.lcut(t) if w.strip()]
    except Exception:
        toks = [t[i:i + 2] for i in range(0, len(t), 2)]
    best2 = ""
    for w in toks:
        if not (2 <= len(w) <= cap):
            continue
        if any(w.startswith(x) for x in _FLOWER_LEAD_STOP):
            continue
        if any(w.endswith(x) for x in _FLOWER_BAD_TAIL):
            continue
        if not re.search(r"[\u4e00-\u9fffA-Za-z0-9]", w):
            continue
        if len(w) > len(best2):
            best2 = w
    if best2:
        return best2
    # 整句本身够短且干净（例如「干炒蚝」这种专名句），才允许整句上屏
    if 2 <= len(t) <= cap and not any(t.startswith(x) for x in _FLOWER_LEAD_STOP):
        return t
    return ""


def _display_keywords(sent: Dict[str, Any]) -> List[str]:
    """上屏用的关键词：优先取 step3 过滤好的 `display_keywords`（只在正文里出现过的词），并自动还原被上游截断的数字单位（「六百→六百平」）。"""
    txt = str(sent.get("text") or "")
    got = sent.get("display_keywords")
    raw_list = (
        [str(k) for k in got if str(k).strip()]
        if isinstance(got, list)
        else [str(k) for k in (sent.get("keywords") or []) if str(k).strip() and str(k) in txt]
    )
    out: List[str] = []
    for k in raw_list:
        ks = re.sub(r"\s+", "", k)
        if ks in ("一个", "一些", "这种", "那个", "这个"):
            continue
        if ks in txt and re.fullmatch(r"[零〇一二两三四五六七八九十百千万亿0-9]+分?", ks):
            m_u = re.search(re.escape(ks) + r"(?:分之[一二三四五六七八九十]+(?![百千万亿])|[平平米个间张条倍万亿块元]?)", txt)
            if m_u and len(m_u.group(0)) > len(ks):
                ks = m_u.group(0)
        if ks and ks not in out:
            out.append(ks)
    return out


def rotate_sfx(
    candidates: List[Path],
    last_used_name: Optional[str],
    recent_usage: Optional[Dict[str, List[float]]] = None,
    cur_time_sec: Optional[float] = None,
    max_same_per_60s: int = 2,
) -> Optional[Path]:
    """同族/候选音效自动轮换 + 60s 同文件防连响上限，绝不偷借跨风格音频。

    候选为空或 60s 滑窗内均已达 max_same_per_60s 上限时返回 None。
    """
    valid = [p for p in candidates if p and p.is_file()]
    if not valid:
        return None
    if recent_usage is None or cur_time_sec is None:
        if len(valid) == 1 or not last_used_name:
            return valid[0]
        for p in valid:
            if p.name != last_used_name:
                return p
        return valid[0]

    def _cnt60(name: str) -> int:
        return sum(1 for ts in (recent_usage.get(name) or []) if ts > cur_time_sec - 60.0 + 1e-6)

    def _last_t(name: str) -> float:
        hist = recent_usage.get(name) or []
        return hist[-1] if hist else -1e9

    under = [p for p in valid if _cnt60(p.name) < max(1, int(max_same_per_60s))]
    if not under:
        return None
    under.sort(key=lambda p: (
        1 if (last_used_name and p.name == last_used_name) else 0,
        _cnt60(p.name),
        _last_t(p.name),
        p.name,
    ))
    return under[0]


def _enhance_sfx_presence(arr: np.ndarray, sr: int = SR) -> np.ndarray:
    """保持 CC0 原生频谱波形不变，对 2–8kHz 过闷暖调 SFX 施加温和广播级自身带通临场补偿。"""
    if len(arr) < 128:
        return arr
    try:
        from scipy import signal as sp_sig
        mono = arr.mean(axis=1).astype(np.float64)
        full_rms = float(np.sqrt(np.mean(mono ** 2) + 1e-12))
        sos = sp_sig.butter(4, [2000.0, 8000.0], btype="bandpass", fs=float(sr), output="sos")
        hf_mono = sp_sig.sosfiltfilt(sos, mono)
        hf_rms = float(np.sqrt(np.mean(hf_mono ** 2) + 1e-12))
        hf_ratio = hf_rms / max(1e-5, full_rms)
        if hf_ratio >= 0.28 or hf_rms < 1e-5:
            return arr
        # 对超低频主导的敲锣/重音 SFX 先轻微清理 110Hz 以下次声轰鸣（防混音撞限幅器反压人声），
        # 再按比例混入原 SFX 自身的 2–8kHz 泛音清晰度并做柔性波峰收敛（绝不混入人工噪波）
        sos_hp = sp_sig.butter(2, min(115.0, float(sr) * 0.25), btype="highpass", fs=float(sr), output="sos")
        out = np.column_stack([sp_sig.sosfiltfilt(sos_hp, arr[:, ch].astype(np.float64)) for ch in range(arr.shape[1])])
        hp_hf_rms = float(np.sqrt(np.mean(sp_sig.sosfiltfilt(sos, out.mean(axis=1)) ** 2) + 1e-12))
        target_hf = max(hp_hf_rms, full_rms * 0.48)
        alpha = min(7.0, max(0.0, (target_hf - hp_hf_rms) / max(1e-5, hp_hf_rms)))
        if alpha > 0.0:
            out[:, 0] += alpha * sp_sig.sosfiltfilt(sos, out[:, 0])
            out[:, 1] += alpha * sp_sig.sosfiltfilt(sos, out[:, 1])
        pk = float(np.max(np.abs(out)))
        if pk > 0.72:
            out = np.tanh(out / 0.72) * 0.68
        return out.astype(np.float32)
    except Exception:
        return arr


def _fade_sfx_edges(arr: np.ndarray, sr: int, fade_in_sec: float = 0.002,
                    fade_out_sec: float = 0.010) -> np.ndarray:
    """给音效两端上淡入淡出，消掉文件硬截留下的电平台阶（不改音色、不改时长）。"""
    if arr.ndim != 2 or len(arr) < 16:
        return arr
    n_in = min(int(sr * fade_in_sec), len(arr) // 4)
    n_out = min(int(sr * fade_out_sec), len(arr) // 4)
    out = arr.copy()
    if n_in > 1:
        out[:n_in] *= np.linspace(0.0, 1.0, n_in, dtype=np.float32)[:, None]
    if n_out > 1:
        out[-n_out:] *= np.linspace(1.0, 0.0, n_out, dtype=np.float32)[:, None]
    return out


def _measure_sfx_peak_offset_sec(arr: np.ndarray, sr: int = SR) -> float:
    if len(arr) < 128:
        return 0.0
    mono = np.abs(arr.mean(axis=1))
    win = max(1, int(0.020 * sr))
    env = np.convolve(mono, np.ones(win, dtype=np.float32) / win, mode="same")
    peak_idx = int(np.argmax(env))
    return round(peak_idx / float(sr), 4)


def _avoid_faces_for_pkg(bbox: Dict[str, int], faces_px: List[Dict[str, Any]]) -> Dict[str, int]:
    """在 Step 6 源头一次性将花字胶囊避开裁切后人脸五官，保持安全区且不侵入字幕带。"""
    cand = dict(bbox)
    if not faces_px:
        return cand
    cur_iou = max(bbox_iou(cand, fb) for fb in faces_px)
    if cur_iou <= 0.01:
        return cand
    best = cand
    best_iou = cur_iou
    for try_y in (176, 224, 280, 340, 460, 780, 880, 960):
        if try_y + cand["h"] > 1120:
            continue
        alt = {"x": cand["x"], "y": int(try_y), "w": cand["w"], "h": cand["h"]}
        ok, _ = check_bbox_in_safe_zone(alt)
        if not ok:
            continue
        iou_v = max(bbox_iou(alt, fb) for fb in faces_px)
        if iou_v <= 0.01:
            return alt
        if iou_v < best_iou:
            best_iou = iou_v
            best = alt
    return best


def _measure_pkg_badge_width(text: str, font_path: Optional[str], max_pkg_w: int, target_fs: int = 40, min_w: int = 240) -> int:
    try:
        from PIL import Image, ImageDraw
        draw = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
        fnt, _ = fit_text_line(draw, text, font_path, max_width=max_pkg_w - 64, max_size=target_fs, min_size=24)
        if fnt is not None:
            bb = draw.textbbox((0, 0), text, font=fnt)
            return int(min(max_pkg_w, max(min_w, (bb[2] - bb[0]) + 72)))
    except Exception:
        pass
    return int(min(max_pkg_w, max(min_w, len(text) * target_fs + 68)))


def _bp_overlay_style(style_id: str) -> Dict[str, Any]:
    try:
        from render_template_overlays import get_style_blueprint

        return (get_style_blueprint(style_id) or {}).get("overlay_style") or {}
    except Exception:
        return {}


def flower_budget_from_source(style_id: str, total_dur: float, legacy: int) -> int:
    """花字条数预算：优先按源草稿实测的「每分钟花字条数」，没有实测值才用旧的经验公式。"""
    ppm = _bp_overlay_style(style_id).get("flower_per_min")
    try:
        ppm_v = float(ppm) if ppm is not None else 0.0
    except (TypeError, ValueError):
        ppm_v = 0.0
    if ppm_v <= 0:
        return int(legacy)
    return max(int(legacy), min(120, int(math.ceil(total_dur * ppm_v / 60.0))))


def _trim_giant_phrase(text: str, max_chars: int = 5) -> str:
    """将巨字候选裁剪到清晰短词（不含标点，剥离截断残留的尾虚字/孤立代词）。"""
    clean = re.sub(r"[\s，。！？、；：“”‘’\"'（）()【】\[\]—…·.!?,;:]+", "", str(text or ""))
    if not clean:
        return ""
    lim = max(1, int(max_chars or 5))
    if len(clean) > lim:
        cut = _cut_at_word_boundary(clean, lim)
        clean = cut or clean[:lim]
    _tail_particles = set("的得地了着过嘅咗啦呀啊呢嘛我你他她它佢喺在系是就到和与")
    while len(clean) >= 3 and clean[-1] in _tail_particles:
        clean = clean[:-1]
    return clean


def _pick_giant_tier_px(text: str, giant_spec: Dict[str, Any], fallback_fs: int = 140) -> int:
    """按有效字数映射 style_spectrum_library.giant_text_spec.size_tiers_px 分级字号。"""
    clean = re.sub(r"[\s，。！？、；：“”‘’\"'（）()【】\[\]—…·.!?,;:]+", "", str(text or ""))
    n = len(clean)
    tiers = giant_spec.get("size_tiers_px") or {}
    raw_v = None
    if isinstance(tiers, dict) and tiers:
        if n <= 2:
            raw_v = tiers.get("1-2")
        elif n == 3:
            raw_v = tiers.get("3")
        elif n == 4:
            raw_v = tiers.get("4")
        else:
            raw_v = tiers.get("5+")
    if raw_v is None:
        raw_v = giant_spec.get("font_size_px") or fallback_fs
    try:
        return max(72, int(float(raw_v)))
    except (TypeError, ValueError):
        return max(72, int(fallback_fs))


def giant_count_from_source(style_id: str, total_dur: float, pkg_tech: Dict[str, Any]) -> int:
    """巨字条数：优先蓝图里源草稿实测的 giant_count，再按片长做一次 sanity 上限。"""
    raw = (pkg_tech or {}).get("giant_impact_count")
    if raw is None:
        raw = _bp_overlay_style(style_id).get("giant_count")
    try:
        n = int(raw) if raw is not None else 2
    except (TypeError, ValueError):
        n = 2
    return max(0, min(n, int(math.ceil(max(1.0, float(total_dur)) / 4.0))))


def run_step6(
    pipeline_dir: Path,
    fps: int = DEFAULT_FPS,
    style_id: str | None = None,
    no_flower_text: bool = False,
    keep_manual_texts: bool = False,
) -> Dict[str, Any]:
    audio_events = read_json(pipeline_dir / "03_audio_events.json")
    edl = read_json(pipeline_dir / "04_edl.json")
    man_p = pipeline_dir / "01_manifest.json"
    manifest = read_json(man_p) if man_p.exists() else {"items": []}
    prev_pkg_p = pipeline_dir / "06_packaging.json"
    prev_visuals = (read_json(prev_pkg_p).get("visual_elements") or []) if (keep_manual_texts and prev_pkg_p.is_file()) else []

    faces_by_mp4: Dict[str, List[Dict[str, int]]] = {}
    for it in manifest.get("items", []):
        mp = str(it.get("normalized_mp4", ""))
        fl = parse_manifest_faces(it)
        if mp and fl:
            faces_by_mp4[mp] = fl

    if not style_id:
        style_id = edl.get("style_id") or load_style_applied(pipeline_dir)
    style_id = str(style_id or "style_01")

    vibe_prof = None
    try:
        from vibe_sound import get_style_sound_profile
        vibe_prof = get_style_sound_profile(style_id)
    except Exception:
        vibe_prof = None

    style_entry_pre = load_style_spectrum_entry(style_id) or {}
    broll_target = float(style_entry_pre.get("broll_overlay_ratio_target", 0.5))
    sfx_per_min_tgt = float((style_entry_pre.get("audio_and_sfx") or {}).get("sfx_per_min_target") or 24.0)
    # 密度以源草稿实测为准（sfx_truth/<style_id>.json 的 sfx_per_min，style_21≈46/min）
    sfx_truth = load_style_sfx_truth(style_id)
    if sfx_truth is not None:
        try:
            truth_ppm = float(sfx_truth.get("sfx_per_min") or 0.0)
        except (TypeError, ValueError):
            truth_ppm = 0.0
        # 真值为 0 就按 0 报（下面 sfx 池为空会真的一条都不出）
        sfx_per_min_tgt = max(0.0, truth_ppm)
    total_dur = float(audio_events["locked_duration_sec"])
    total_samples = int(round(total_dur * SR))
    _legacy_budget = max(2, math.ceil(total_dur / 5.5)) if broll_target >= 0.85 else max(3, math.ceil(total_dur / 3.5))
    max_visual_budget = flower_budget_from_source(style_id, total_dur, _legacy_budget)
    # 上限从 48 提到 72：源里 style_15 实测 64.8 次/分，旧的 48 上限会把它削平
    dyn_sfx_budget = max(5, int(math.ceil(total_dur * max(15.0, min(72.0, sfx_per_min_tgt)) / 60.0)))
    max_sfx_budget = max(8, dyn_sfx_budget) if style_id == "style_06" else dyn_sfx_budget

    sentences_map = {s["id"]: s for s in audio_events["sentences"]}
    events = audio_events["events"]
    code_ranges = [
        (int(c["timeline_in_frame"]), int(c["timeline_out_frame"]))
        for c in edl.get("clips", [])
        if c.get("track_role") == "code_broll"
    ]

    def overlaps_code(sf_f: int, ef_f: int) -> bool:
        return any(sf_f < ce and ef_f > cs for cs, ce in code_ranges)

    sfx_cues: List[Dict[str, Any]] = []
    dyn_gap_sec = max(1.5, min(4.8, (60.0 / max(12.0, sfx_per_min_tgt)) * 0.68))
    # 音效池：只认本风格目录下的真音效，整包为空就直接不出 sfx
    sfx_pool = scan_style_sfx(style_id)
    sfx_disabled_reason: Optional[str] = None
    if not sfx_pool:
        truth_count = int((sfx_truth or {}).get("sfx_count") or 0)
        sfx_disabled_reason = (
            f"{style_id} 源草稿 sfx_count=0，本风格无音效可放"
            if (sfx_truth is not None and truth_count == 0)
            else f"{style_id} 音效目录为空（assets/sound_vibe/sfx/{style_id}/ 无文件）"
        )
    native_prof = vibe_prof if (vibe_prof or {}).get("mapped_from_style_id") == style_id else None
    bgm_info = load_style_bgm_info(style_id)
    if native_prof:
        # 尊重流派自己的 sfx_gain_db（仅允许上限到 -3.5dB，避免风格全被拉爆成统一响度）
        base_gain_db = max(-14.0, min(-3.5, float(native_prof.get("sfx_gain_db", -9.5)) + 3.0))
        if sfx_per_min_tgt >= 25.0:
            base_gain_db = max(base_gain_db, -4.5)
        prof_gap = float(native_prof.get("sfx_min_gap_sec", dyn_gap_sec))
        min_sfx_gap = int(min(prof_gap, dyn_gap_sec) * fps)
    else:
        # 高频快切风格（真值 sfx_per_min >= 25）原声里音效显著跳在 BGM 上方 8–10dB，适当抬高基准电平
        base_gain_db = -5.0 if sfx_per_min_tgt >= 25.0 else -8.5
        min_sfx_gap = int(dyn_gap_sec * fps)

    sfx_files: Dict[str, List[Path]] = {
        "hook": _role_candidates(sfx_pool, "hook") or _role_candidates(sfx_pool, "accent"),
        "info": _role_candidates(sfx_pool, "info"),
        "accent": _role_candidates(sfx_pool, "accent"),
        "twist": _role_candidates(sfx_pool, "accent"),
        "detail": _role_candidates(sfx_pool, "info"),
        "persona": _role_candidates(sfx_pool, "cut"),
        "cta": _role_candidates(sfx_pool, "receipt"),
        "cut": _role_candidates(sfx_pool, "cut"),
    }

    # 接入动画×音效字典与叙事意图分类表（仅接受本风格目录内的音频，杜绝跨风格串味）
    anim_sfx_dict = load_animation_sfx_pairs()
    sfx_taxonomy = load_sfx_intent_taxonomy()
    emotion_routing = sfx_taxonomy.get("pipeline_emotion_routing") or {}
    style_sfx_root = style_sfx_dir(style_id).resolve()
    for emo_key, rel_list in emotion_routing.items():
        if emo_key not in sfx_files or not rel_list:
            continue
        for rel_p in rel_list:
            cand_p = ASSETS_DIR / rel_p
            if not cand_p.is_file():
                continue
            try:
                cand_p.resolve().relative_to(style_sfx_root)
            except Exception:
                continue  # 不是本风格目录内的音频，直接丢弃
            sfx_files[emo_key] = [cand_p]
            break

    # ─────────────── 触发条件闸门：花字先行 → 音效跟随 ───────────────
    # 用户实测原话：「音效过于密集，感觉没有任何缘由就出现……」。旧版只按密度在每个切点上撒，
    # 这一版每条 cue 都必须挂到一个可观测锚点上（结构性切点 / 花字出现 / 字幕换行 / BGM 拍点），
    # 挂不上就不出。规则来自 24 套源草稿的实测表：forensic/style_trigger_rules_sfx_flower.json。
    sentences_all = audio_events["sentences"]
    raw_intel_p = pipeline_dir / "00_raw_intel.json"
    raw_pain_kws = [str(k) for k in ((read_json(raw_intel_p).get("pain_keywords") or [])
                                    if raw_intel_p.is_file() else []) if str(k).strip()]

    def _visual_shot_role(clip_obj: Dict[str, Any]) -> str:
        r = str(clip_obj.get("track_role") or "a_roll")
        if r in ("b_roll", "code_broll") or bool(clip_obj.get("b_roll_overlay")):
            return "b_roll"
        lay = clip_obj.get("pip_layout")
        if isinstance(lay, dict) and lay.get("secondary_media_path") and float(lay.get("fg_scale") or 1.0) < 0.98:
            return "b_roll"
        return r

    clip_bounds: List[Tuple[int, int, str, float]] = []
    for c in edl.get("clips", []):
        role_c = _visual_shot_role(c)
        clip_bounds.append((int(c["timeline_in_frame"]), int(c["timeline_out_frame"]),
                            role_c, float(c.get("duration_sec") or 0.0)))

    def _shot_at(t: float) -> Tuple[str, float]:
        f_t = float(t) * fps
        for a_f, b_f, role_c, dur_c in clip_bounds:
            if a_f <= f_t < b_f:
                return role_c, dur_c
        return "a_roll", 0.0

    _shot_lens = sorted(d for _a, _b, _r, d in clip_bounds if d > 0)
    shot_median_sec = _shot_lens[len(_shot_lens) // 2] if _shot_lens else 0.0

    cut_events: List[Dict[str, Any]] = []
    clips_list = edl["clips"]
    for idx_c in range(1, len(clips_list)):
        prev_c = clips_list[idx_c - 1]
        c = clips_list[idx_c]
        prev_role = _visual_shot_role(prev_c)
        role_c = _visual_shot_role(c)
        t_cut = round(int(c["cut_event_frame"]) / float(fps), 4)
        ev_t = str(c.get("cut_event_type") or "")
        # A/B-roll 跨轨画面切换、或前 3s 开场黄金切镜均属结构性场景转折（shot_change），不应被误判为无故口播微切
        if prev_role != role_c or (t_cut <= 3.15 and ev_t in ("bgm_beat", "stress", "")):
            ev_t = "shot_change"
        cut_events.append({
            "time_sec": t_cut,
            "type": ev_t,
            "shot_type": role_c,
            "event_id": c.get("cut_event_id"),
            "role_switch": prev_role != role_c,
        })
    cut_times_list = [e["time_sec"] for e in cut_events]
    line_starts = [round(float(s.get("start_sec", 0.0)), 4) for s in sentences_all]
    beat_times = [float(b) for b in ((audio_events.get("bgm") or {}).get("beat_times_sec") or [])]
    sent_spans = [{"start_sec": float(s.get("start_sec", 0.0)),
                   "end_sec": float(s.get("end_sec", 0.0)),
                   "text": str(s.get("text") or "")} for s in sentences_all]

    import element_rules
    import kinetic_choreographer

    kinetic_stage_mode = kinetic_choreographer.is_kinetic_stage_style(style_id)
    kinetic_stage_plan: Optional[Dict[str, Any]] = (
        kinetic_choreographer.plan_kinetic_stage(
            audio_events, style_id=style_id, pipeline_dir=pipeline_dir, fps=fps,
        )
        if (kinetic_stage_mode and not no_flower_text)
        else None
    )

    _allow_flower = element_rules.is_present(style_id, "flower", default=True)
    _allow_giant = element_rules.is_present(style_id, "giant_text", default=True)
    _allow_end_card = element_rules.is_present(style_id, "end_card", default=False)
    is_pure_giant_style = bool(not _allow_flower and _allow_giant)

    # 「这一版会不会有花字/大字上屏」先定下来：不上屏的文字不能当音效的锚点，
    # 否则音效会以"花字出现"为理由响，而画面上根本没有那张字。
    suppress_top_dup = False
    framed_top_y = 210
    framed_x_min = SAFE_ZONE["x_min"]
    framed_x_max = SAFE_ZONE["x_max"]
    try:
        from render_template_overlays import get_style_blueprint
        bp_full = get_style_blueprint(style_id) or {}
        bp_ov = (bp_full.get("overlay_style") or {})
        suppress_top_dup = bool(bp_ov.get("suppress_top_duplicate_flower", False))
        bp_pip = bp_full.get("pip_spec") or {}
        if not bool(bp_pip.get("cutout")):
            win_scales = [
                float(w.get("scale") or 1.0)
                for w in (bp_pip.get("windows") or [])
                if isinstance(w, dict) and 0.65 <= float(w.get("scale") or 1.0) <= 0.94
            ]
            if win_scales:
                min_ws = min(win_scales)
                framed_top_y = max(210, int(round(1920 * (1.0 - min_ws) * 0.5)) + 106)
                pad_x_in = int(round(1080 * (1.0 - min_ws) * 0.5)) + 26
                framed_x_min = max(SAFE_ZONE["x_min"], pad_x_in)
                framed_x_max = min(SAFE_ZONE["x_max"], 1080 - pad_x_in)
    except Exception:
        suppress_top_dup = False
    _emit_flowers = not no_flower_text and (_allow_flower or _allow_giant)

    # ① 花字/重点词候选：一句一词，文案从这句人声里提炼（先定视觉上屏项，再对齐音效锚点）
    flower_pol = FlowerPolicy(style_id)
    _fchars_max = int(flower_pol.chars_max)
    flower_cands: List[Dict[str, Any]] = []
    for s in sentences_all:
        t0 = float(s.get("start_sec", 0.0))
        label = _extract_clean_flower_phrase(s, raw_pain_kws=raw_pain_kws, max_len=max(8, _fchars_max))
        label = _flower_label_for_style(label, _fchars_max)
        role_s, shot_len_s = _shot_at(t0)
        flower_cands.append({
            "text": label,
            "start_sec": t0,
            "sentence_id": s.get("id"),
            "sentence_text": str(s.get("text") or ""),
            "shot_type": role_s,
            "origin": "sentence",
            "long_turn": bool(shot_median_sec and shot_len_s >= 1.8 * shot_median_sec),
        })
    flower_res = flower_pol.evaluate(flower_cands, {
        "cut_times": cut_times_list,
        "line_starts": line_starts,
        "sentences": sent_spans,
        "duration_sec": total_dur,
    })
    flower_specs: List[Dict[str, Any]] = list(flower_res["accepted"]) if _emit_flowers else []

    style_entry = style_entry_pre or load_style_spectrum_entry(style_id) or {}
    style_font_cands = list((style_entry.get("subtitle_spec") or {}).get("font_candidates") or [])
    pkg_font_path = find_commercial_font(style_font_cands or None)
    flower_spec = style_entry.get("multi_zone_flower_spec") or (style_entry.get("transition_and_packaging") or {}).get("multi_zone_flower_spec") or {}
    pkg_tech = style_entry.get("packaging_techniques") or {}
    is_borderless_flower = bool(flower_spec.get("borderless", False))
    use_top_bar = bool(pkg_tech.get("persistent_top_bar") or flower_spec.get("persistent_top_bar"))
    use_quote_card = bool(pkg_tech.get("quote_citation_card") or flower_spec.get("quote_citation_card"))

    anim_lookup = anim_sfx_dict.get("animation_lookup") or {}
    anim_cn_alias = {
        "hard_cut": "干脆切换",
        "smooth_pop_or_fade": "轻快渐显",
        "渐显": "渐显",
        "渐隐": "渐隐",
        "水墨晕开": "晕开",
        "弹幕滚动": "向右集合",
        "日出": "随机上升",
        "向下滑动": "向下露出",
        "向右露出": "向右缓入",
    }
    raw_anim_str = str((style_entry.get("subtitle_spec") or {}).get("measured_animation_raw") or "in:渐显")
    raw_anim_tokens = [
        p.replace("in:", "").replace("loop:", "").replace("out:", "").strip()
        for p in raw_anim_str.split("/")
        if p.strip()
    ] or ["渐显"]
    default_jy_anim = ""
    matched_pair = None
    for tok in raw_anim_tokens:
        mapped = anim_cn_alias.get(tok, tok)
        if tok in anim_lookup:
            default_jy_anim = tok
            matched_pair = anim_lookup[tok]
            break
        if mapped in anim_lookup:
            default_jy_anim = mapped
            matched_pair = anim_lookup[mapped]
            break
    if not default_jy_anim:
        first_tok = raw_anim_tokens[0]
        default_jy_anim = anim_cn_alias.get(first_tok, "渐显" if first_tok.isascii() else first_tok)
    if matched_pair is None:
        matched_pair = next(iter(anim_lookup.values()), {
            "jianying_anim_name": "渐显",
            "anim_duration_ms": 400,
            "jianying_sfx_name": "“呼”的转场音效",
        })

    _anim_rot_names: List[str] = []
    for _it in ((style_entry.get("display_text_spec") or {}).get("anims_top") or []):
        _nm = str(_it[0] if isinstance(_it, (list, tuple)) else _it).strip()
        _nm = _nm.replace("in:", "").replace("out:", "").replace("loop:", "").strip()
        if not _nm:
            continue
        _mapped = anim_cn_alias.get(_nm, _nm)
        _canon = _nm if _nm in anim_lookup else (_mapped if _mapped in anim_lookup else "")
        if _canon and _canon not in _anim_rot_names:
            _anim_rot_names.append(_canon)
    _anim_rot_pairs = [anim_lookup[n] for n in _anim_rot_names]

    def _pair_for_slot(idx: int) -> Dict[str, Any]:
        if _anim_rot_pairs:
            return _anim_rot_pairs[idx % len(_anim_rot_pairs)]
        return matched_pair

    def _safe_readable_fg(cand_hex: Optional[str], fallback_hex: str = "#FFE28A") -> str:
        c_str = str(cand_hex or fallback_hex)
        rgb = hex_to_rgba(c_str, 255)[:3]
        if contrast_ratio(rgb, (16, 20, 28)) < 4.5:
            return fallback_hex
        return c_str

    visual_elements: List[Dict[str, Any]] = []
    giant_spec = style_entry.get("giant_text_spec") or {}
    giant_mode = str(giant_spec.get("mode") or "giant_smash_single")
    giant_fs = int(giant_spec.get("font_size_px") or 140)
    giant_max_chars = max(2, int(giant_spec.get("max_chars") or 5))
    giant_tag = str(giant_spec.get("tag_label") or "核心重点")
    giant_fg = _safe_readable_fg(giant_spec.get("fg_hex") or flower_spec.get("center_burst_color"), "#FFE28A")
    giant_accent = _safe_readable_fg(giant_spec.get("accent_hex") or flower_spec.get("flank_color"), "#FFF589")
    giant_stroke = str(giant_spec.get("stroke_hex") or flower_spec.get("stroke_color") or "#000000")
    giant_count_target = giant_count_from_source(style_id, total_dur, pkg_tech)
    use_multi_tag = bool(pkg_tech.get("multi_tag_burst") or flower_spec.get("multi_tag_burst"))
    if is_pure_giant_style:
        max_visual_budget = max(max_visual_budget, min(12, giant_count_target + (1 if _allow_end_card else 0) + (1 if use_top_bar else 0)))

    first_sent = audio_events["sentences"][0] if audio_events["sentences"] else {"text": "核心亮点", "keywords": []}
    hook_badge_text = _extract_clean_flower_phrase(first_sent, raw_pain_kws=raw_pain_kws, max_len=8)
    total_frames = int(round(total_dur * fps))
    top_ef = min(int(min(4.2, total_dur * 0.45) * fps), total_frames)
    hook_ef = min(max(int(math.ceil(1.4 * fps)), int(2.2 * fps)) if total_dur >= 1.5 else total_frames, total_frames)

    if no_flower_text:
        visual_elements = []
    elif kinetic_stage_mode and kinetic_stage_plan is not None:
        # 动态花字舞台已接管全片胸口/中区花字表现，关闭旧版顶部瞎贴与双行静态贴片
        visual_elements = []
    elif keep_manual_texts and prev_visuals:
        # --keep-edl 换风格时：完整保留已有花字文案与起止帧，只按新风格更新配色与视觉参数
        for idx_p, pe in enumerate(prev_visuals, start=1):
            ne = dict(pe)
            ne["id"] = f"pkg_{idx_p:03d}"
            ne["fg_hex"] = giant_fg if ne.get("kind") == "center_burst_title" else _safe_readable_fg(flower_spec.get("stamp_color"), "#FFF589")
            ne["accent_hex"] = giant_accent
            ne["stroke_hex"] = giant_stroke
            ne["jianying_anim_name"] = default_jy_anim
            fb = dict(ne.get("final_bbox") or ne.get("bbox") or {"x": 220, "y": 240, "w": 480, "h": 110})
            if fb["y"] + fb["h"] > 1120:
                fb["y"] = max(162, 1120 - fb["h"])
            ne["bbox"] = fb
            ne["final_bbox"] = fb
            visual_elements.append(ne)
    else:
        # 1. 若风格启用「顶部常驻标题栏」(如 style_05/06/08/09/12/14/22)，严格落在安全区 y=162；suppress_top_dup 严禁顶部常驻重复条
        if use_top_bar and not suppress_top_dup and total_dur >= 3.2 and not overlaps_code(0, top_ef):
            top_label = f"{hook_badge_text[:10]}"
            top_w = _measure_pkg_badge_width(top_label, pkg_font_path, SUBTITLE_SYMMETRIC_MAX_W, target_fs=34, min_w=280)
            top_bb = {"x": SUBTITLE_CENTER_X - top_w // 2, "y": 162, "w": top_w, "h": 58}
            ok_top, _ = check_bbox_in_safe_zone(top_bb)
            visual_elements.append({
                "id": f"pkg_{len(visual_elements)+1:03d}",
                "kind": "persistent_top_bar",
                "overlay_mode": "top_bar",
                "fg_hex": _safe_readable_fg(flower_spec.get("center_burst_color"), "#FFF589"),
                "stroke_hex": "#000000",
                "text_or_asset": top_label,
                "start_frame": 0,
                "end_frame": top_ef,
                "read_hold_sec": round(top_ef / float(fps), 2),
                "aligned_sfx_or_accent_frame": 0,
                "alignment_error_frames": 0,
                "bbox": top_bb,
                "final_bbox": top_bb,
                "safe_zone_ok": ok_top,
                "jianying_anim_name": str(_pair_for_slot(len(visual_elements)).get("jianying_anim_name") or default_jy_anim),
                "anim_duration_ms": int(_pair_for_slot(len(visual_elements)).get("anim_duration_ms", 400)),
                "paired_jianying_sfx": str(_pair_for_slot(len(visual_elements)).get("jianying_sfx_name", "提示音")),
                "trigger": "hook",
                "policy": f"{style_id} 顶部常驻标语（源草稿 packaging 结构件，按整条停留，不受花字密度约束）",
                "reason": f"{style_id} 顶部常驻主题标语栏",
            })

        # 2. 开场重点大字 / 门店三拍综艺斜标冲击
        is_opening_poster_burst = bool(
            str(bp_ov.get("opening_mode") or "") == "store_poster_burst"
            and _allow_flower
            and not _allow_giant
        )
        _open_has_num = any(
            re.search(r"\d|[两三四五六七八九十百千万]", str(s_op.get("text") or ""))
            for s_op in audio_events["sentences"]
            if float(s_op.get("start_sec") or 0.0) <= 2.6
        )
        _suppress_open_dup = bool(
            suppress_top_dup
            and (
                len(audio_events["sentences"]) <= 1
                or int(giant_count_target or 0) <= 0
                or not _open_has_num
            )
        )
        can_place_opening_giant = (
            len(visual_elements) < max_visual_budget
            and not _suppress_open_dup
            and (_allow_giant or is_opening_poster_burst)
            and (giant_count_target > 0 or is_pure_giant_style or is_borderless_flower or not use_top_bar or is_opening_poster_burst)
            and not overlaps_code(0, hook_ef)
            and (hook_ef / float(fps) >= 1.35)
        )
        if can_place_opening_giant:
            open_sents = [s for s in audio_events["sentences"] if float(s.get("start_sec") or 0.0) <= 2.6] or [first_sent]
            hook_cand = ""
            # 优先挑含真实数字/实体词（如三百万、六百平、点赞茶楼）的开场钩子，绝不拿「我系牛哥我」当大字主标
            for s_op in open_sents:
                phr_num = _extract_clean_flower_phrase(s_op, raw_pain_kws=raw_pain_kws, max_len=8)
                if re.search(r"\d|[两三四五六七八九十百千万]", phr_num):
                    hook_cand = phr_num
                    break
            if not hook_cand:
                for s_op in reversed(open_sents):
                    phr_op = _extract_clean_flower_phrase(s_op, raw_pain_kws=raw_pain_kws, max_len=8)
                    if len(phr_op) >= 3 and not phr_op.startswith(("我系", "我是")):
                        hook_cand = phr_op
                        break
            if not hook_cand:
                hook_cand = hook_badge_text
            if is_pure_giant_style:
                hook_label = _trim_giant_phrase(hook_cand, max_chars=giant_max_chars) or hook_cand[:giant_max_chars]
            else:
                hook_label = hook_cand[:8]
            tier_fs_open = 116 if is_opening_poster_burst else (
                _pick_giant_tier_px(hook_label, giant_spec, giant_fs) if (is_pure_giant_style or giant_spec.get("size_tiers_px")) else giant_fs
            )
            hook_w = min(SUBTITLE_SYMMETRIC_MAX_W, max(360, _measure_pkg_badge_width(hook_label, pkg_font_path, SUBTITLE_SYMMETRIC_MAX_W, target_fs=min(140, tier_fs_open), min_w=360)))
            hook_x = SUBTITLE_CENTER_X - hook_w // 2
            hook_h = 280 if is_opening_poster_burst else (min(480, max(142, int(round(tier_fs_open * 1.12)))) if is_pure_giant_style else (152 if giant_mode in ("giant_dual_clash", "giant_numeric_trio") else 142))
            init_giant_y = 780 if style_id == "style_01" else (max(framed_top_y, 224) if use_top_bar else framed_top_y)
            active_faces_0 = []
            for c in edl.get("clips", []):
                if int(c["timeline_in_frame"]) < hook_ef and int(c["timeline_out_frame"]) > 0:
                    active_faces_0.extend(faces_by_mp4.get(str(c.get("media_path", "")), []))
            hook_bbox = _avoid_faces_for_pkg({"x": hook_x, "y": init_giant_y, "w": hook_w, "h": hook_h}, active_faces_0)
            if use_top_bar and hook_bbox["y"] < 218:
                hook_bbox["y"] = 222
            if hook_bbox["y"] + hook_bbox["h"] > 1120:
                hook_bbox["y"] = max(162, 1120 - hook_bbox["h"])
            safe_ok, _ = check_bbox_in_safe_zone(hook_bbox)
            _fkw = _display_keywords(open_sents[-1]) or _display_keywords(first_sent)
            sec_kw = next((k for k in _fkw if k and k not in hook_label and hook_label not in k), "")
            if not sec_kw and is_opening_poster_burst:
                sec_kw = str((raw_pain_kws[0] if raw_pain_kws else "") or giant_tag or "实景探店")[:8]
            elif not sec_kw and style_id == "style_01":
                sec_kw = giant_tag if giant_tag and giant_tag != "核心重点" else "门店实拍"
            open_elem: Dict[str, Any] = {
                "id": f"pkg_{len(visual_elements)+1:03d}",
                "kind": "flower_text" if is_opening_poster_burst else "center_burst_title",
                "opening_mode": "store_poster_burst" if is_opening_poster_burst else str(bp_ov.get("opening_mode") or ""),
                "giant_mode": giant_mode,
                "font_size_px": tier_fs_open,
                "tag_label": "实景探店" if (is_opening_poster_burst and giant_tag == "门店招牌") else giant_tag,
                "accent_hex": giant_accent,
                "subtext": "" if is_pure_giant_style else str(sec_kw or ""),
                "overlay_mode": "borderless",
                "fg_hex": giant_fg,
                "stroke_hex": giant_stroke,
                "text_or_asset": hook_label,
                "start_frame": 0,
                "end_frame": hook_ef,
                "read_hold_sec": round(hook_ef / float(fps), 2),
                "aligned_sfx_or_accent_frame": 0,
                "alignment_error_frames": 0,
                "bbox": hook_bbox,
                "final_bbox": hook_bbox,
                "safe_zone_ok": safe_ok,
                "jianying_anim_name": "三拍砸屏" if is_opening_poster_burst else str(_pair_for_slot(len(visual_elements)).get("jianying_anim_name") or default_jy_anim),
                "anim_duration_ms": int(_pair_for_slot(len(visual_elements)).get("anim_duration_ms", 400)),
                "paired_jianying_sfx": str(_pair_for_slot(len(visual_elements)).get("jianying_sfx_name", "“呼”的转场音效")),
                "trigger": "chapter" if is_opening_poster_burst else "hook",
                "policy": f"{style_id} {'开场三拍综艺斜标' if is_opening_poster_burst else '开场巨字'}（片头 0~{round(hook_ef / float(fps), 2)}s 的结构件，不是撒花字）",
                "reason": f"{style_id} 开场 {tier_fs_open}px {'分拍斜标爆发' if is_opening_poster_burst else '砸屏巨字冲击'} ({giant_mode})",
            }
            if is_opening_poster_burst:
                open_elem["display_role"] = "flower"
                open_elem["anim_primitive"] = "三拍砸屏"
            elif is_pure_giant_style or (_allow_giant and giant_spec.get("size_tiers_px")):
                open_elem["display_role"] = "giant_text"
            visual_elements.append(open_elem)
        max_pkg_w = SUBTITLE_SYMMETRIC_MAX_W

        last_vis_end = (
            hook_ef + (int(1.2 * fps) if is_pure_giant_style else int(2.2 * fps))
            if any(str(v.get("kind") or "") != "persistent_top_bar" for v in visual_elements)
            else 0
        )
        last_sent = audio_events["sentences"][-1] if audio_events["sentences"] else None
        stamp_zone_ys = (max(framed_top_y, 280), 820, max(framed_top_y, 230), 890)
        giant_zone_ys = (max(framed_top_y, 230), max(framed_top_y + 40, 310), 400)
        stamp_seq = 0
        has_end_cta = False
        _tail_reserve_f = int(total_dur * 0.75 * fps) if (_allow_end_card and total_dur >= 4.0) else int((total_dur - 1.35) * fps)

        def _flower_skip(spec: Dict[str, Any], why: str) -> None:
            rec = {"start_sec": spec.get("start_sec"), "text": spec.get("text"),
                   "trigger": spec.get("trigger"), "dropped_because": "layout_skip", "detail": why}
            flower_res["dropped"].append(rec)
            flower_res["rows"].append({"start_sec": spec.get("start_sec"), "text": spec.get("text"),
                                       "decision": "drop", "trigger": spec.get("trigger"), "why": why})
            flower_res["n_dropped"] = len(flower_res["dropped"])

        if _allow_flower or is_pure_giant_style:
            _reserve_tail_slot = 1 if (is_pure_giant_style and _allow_end_card and total_dur >= 4.0) else 0
            for spec in flower_specs:
                if len(visual_elements) >= max(1, max_visual_budget - _reserve_tail_slot):
                    _flower_skip(spec, "视觉包装预算已满（保留片尾卡名额）" if _reserve_tail_slot else "视觉包装预算已满")
                    continue
                sf = int(round(float(spec["start_sec"]) * fps))
                ef = int(round(float(spec.get("end_sec") or 0.0) * fps))
                if ef <= sf:
                    ef = min(total_frames, sf + int(math.ceil(float(spec.get("hold_sec") or 2.0) * fps)))
                if is_pure_giant_style and (ef - sf) / float(fps) < 1.40:
                    ef = min(total_frames, sf + int(math.ceil(1.40 * fps)))
                sent = sentences_map.get(str(spec.get("sentence_id"))) or next(
                    (s for s in audio_events["sentences"] if s["start_frame"] <= sf <= s["end_frame"] + 15), first_sent)
                end_cap_f = _tail_reserve_f if is_pure_giant_style else int((total_dur - 1.35) * fps)
                if (
                    sf < last_vis_end
                    or sf >= end_cap_f
                    or (is_pure_giant_style and _allow_end_card and last_sent and sent.get("id") == last_sent.get("id"))
                ):
                    _flower_skip(spec, "与上一张展示字重叠，或落入片尾收口保留区")
                    continue
                if (ef - sf) / float(fps) < 1.35 or overlaps_code(sf, ef):
                    _flower_skip(spec, "停留不足 1.35s，或压在代码/图形 B-roll 上")
                    continue

                kw_label = str(spec.get("text") or "") or _extract_clean_flower_phrase(sent, raw_pain_kws=raw_pain_kws, max_len=8)
                if suppress_top_dup:
                    sent_plain = re.sub(r"[^\w\u4e00-\u9fff]+", "", str(sent.get("text") or ""))
                    kw_plain = re.sub(r"[^\w\u4e00-\u9fff]+", "", kw_label)
                    if len(audio_events["sentences"]) <= 1 or (kw_plain and kw_plain == sent_plain):
                        _flower_skip(spec, "suppress_top_duplicate_flower：抑制单句/与整句字幕纯复读花字")
                        continue
                active_faces = []
                for c in edl.get("clips", []):
                    if int(c["timeline_in_frame"]) < ef and int(c["timeline_out_frame"]) > sf:
                        active_faces.extend(faces_by_mp4.get(str(c.get("media_path", "")), []))

                if is_pure_giant_style:
                    g_label = _trim_giant_phrase(kw_label, max_chars=giant_max_chars)
                    if not g_label:
                        _flower_skip(spec, "分级大字短词为空")
                        continue
                    g_tier_fs = _pick_giant_tier_px(g_label, giant_spec, giant_fs)
                    box_w = min(SUBTITLE_SYMMETRIC_MAX_W, max(280, len(g_label) * min(g_tier_fs, 260) + 96))
                    box_h = min(480, max(142, int(round(g_tier_fs * 1.12))))
                    box_x = SUBTITLE_CENTER_X - box_w // 2
                    init_y = giant_zone_ys[stamp_seq % len(giant_zone_ys)]
                    final_bbox = _avoid_faces_for_pkg({"x": box_x, "y": init_y, "w": box_w, "h": box_h}, active_faces)
                    if final_bbox["y"] + final_bbox["h"] > 1120:
                        final_bbox["y"] = max(162, 1120 - final_bbox["h"])
                    ok, _ = check_bbox_in_safe_zone(final_bbox)
                    elem_obj = {
                        "id": f"pkg_{len(visual_elements)+1:03d}",
                        "kind": "center_burst_title",
                        "display_role": "giant_text",
                        "giant_mode": "giant_smash_single",
                        "font_size_px": g_tier_fs,
                        "tag_label": giant_tag,
                        "overlay_mode": "borderless",
                        "fg_hex": giant_fg,
                        "accent_hex": giant_accent,
                        "stroke_hex": giant_stroke,
                        "text_or_asset": g_label,
                        "subtext": "",
                        "burst_tags": [],
                        "start_frame": sf,
                        "end_frame": ef,
                        "read_hold_sec": round((ef - sf) / float(fps), 2),
                        "aligned_sfx_or_accent_frame": sf,
                        "alignment_error_frames": 0,
                        "bbox": final_bbox,
                        "final_bbox": final_bbox,
                        "safe_zone_ok": ok,
                        "jianying_anim_name": str(_pair_for_slot(len(visual_elements)).get("jianying_anim_name") or default_jy_anim),
                        "anim_duration_ms": int(_pair_for_slot(len(visual_elements)).get("anim_duration_ms", 400)),
                        "paired_jianying_sfx": str(_pair_for_slot(len(visual_elements)).get("jianying_sfx_name", "“呼”的转场音效")),
                        "trigger": spec.get("trigger") or "keyword",
                        "anchor": {"kind": spec.get("anchor_kind"), "time_sec": spec.get("anchor_time"),
                                   "offset_sec": spec.get("anchor_offset_sec")},
                        "anchor_time": spec.get("anchor_time"),
                        "evidence_text": spec.get("evidence_text"),
                        "policy": f"{style_id} 分级砸屏大字（{len(g_label)}字→{g_tier_fs}px，禁止普通花字）",
                        "reason": f"分级大字 {spec.get('trigger')}｜{g_tier_fs}px｜出处「{str(spec.get('evidence_text') or '')[:12]}」",
                    }
                    stamp_seq += 1
                    visual_elements.append(elem_obj)
                    last_vis_end = ef + int(1.0 * fps)
                    continue

                is_end_cta = bool(is_borderless_flower and last_sent and sent.get("id") == last_sent.get("id"))
                is_quote_item = bool(use_quote_card and not is_end_cta and stamp_seq == 0)
                box_max_w = SUBTITLE_SYMMETRIC_MAX_W
                target_fs = 64 if is_quote_item else (68 if is_borderless_flower else 48)
                box_w = min(box_max_w, max(320 if is_quote_item else 260, _measure_pkg_badge_width(kw_label, pkg_font_path, box_max_w, target_fs=target_fs, min_w=320 if is_quote_item else 260)))
                box_x = SUBTITLE_CENTER_X - box_w // 2
                box_h = 136 if is_end_cta else (132 if is_quote_item else (112 if is_borderless_flower else 96))
                init_y = 890 if is_end_cta else (820 if is_quote_item else stamp_zone_ys[stamp_seq % len(stamp_zone_ys)])
                final_bbox = _avoid_faces_for_pkg({"x": box_x, "y": init_y, "w": box_w, "h": box_h}, active_faces)
                if final_bbox["y"] + final_bbox["h"] > 1120:
                    final_bbox["y"] = max(162, 1120 - final_bbox["h"])
                ok, _ = check_bbox_in_safe_zone(final_bbox)
                elem_kind = (
                    "end_card_cta" if is_end_cta else
                    ("quote_citation_card" if is_quote_item else
                     ("stamp_tag" if is_borderless_flower else "flower_text"))
                )
                fg_key = "end_card_color" if is_end_cta else ("flank_color" if is_quote_item else "stamp_color")
                fg_default = "#FFE28A" if (is_end_cta or is_quote_item) else "#FFF589"
                burst_tags = [str(k)[:5] for k in _display_keywords(sent) if str(k).strip()][:3] if use_multi_tag else []
                elem_obj = {
                    "id": f"pkg_{len(visual_elements)+1:03d}",
                    "kind": elem_kind,
                    "overlay_mode": "borderless" if is_borderless_flower else ("focus_blur" if sent.get("emotion_or_info") == "twist" else "corner"),
                    "fg_hex": _safe_readable_fg(flower_spec.get(fg_key), fg_default),
                    "accent_hex": giant_accent,
                    "stroke_hex": str(flower_spec.get("stroke_color") or "#000000"),
                    "text_or_asset": f"“{kw_label}”" if is_quote_item and not kw_label.startswith("“") else kw_label,
                    "subtext": f"—— {giant_tag}" if is_quote_item else "",
                    "burst_tags": burst_tags,
                    "start_frame": sf,
                    "end_frame": ef,
                    "read_hold_sec": round((ef - sf) / float(fps), 2),
                    "aligned_sfx_or_accent_frame": sf,
                    "alignment_error_frames": 0,
                    "bbox": final_bbox,
                    "final_bbox": final_bbox,
                    "safe_zone_ok": ok,
                    "jianying_anim_name": str(_pair_for_slot(len(visual_elements)).get("jianying_anim_name") or default_jy_anim),
                    "anim_duration_ms": int(_pair_for_slot(len(visual_elements)).get("anim_duration_ms", 400)),
                    "paired_jianying_sfx": str(_pair_for_slot(len(visual_elements)).get("jianying_sfx_name", "“呼”的转场音效")),
                    "trigger": spec.get("trigger"),
                    "anchor": {"kind": spec.get("anchor_kind"), "time_sec": spec.get("anchor_time"),
                               "offset_sec": spec.get("anchor_offset_sec")},
                    "anchor_time": spec.get("anchor_time"),
                    "evidence_text": spec.get("evidence_text"),
                    "policy": spec.get("policy"),
                    "reason": f"花字 {spec.get('trigger')}｜出处「{str(spec.get('evidence_text') or '')[:12]}」",
                }
                if is_end_cta:
                    elem_obj["subtext"] = "评论区聊聊你的看法"
                    has_end_cta = True
                else:
                    stamp_seq += 1
                visual_elements.append(elem_obj)
                last_vis_end = ef + int(2.5 * fps)

        # 保障片尾收尾/互动卡在 end_card / 无框风格下按约落在最后 ~15%
        if (_allow_end_card or is_borderless_flower) and not has_end_cta and last_sent and len(visual_elements) < max_visual_budget:
            if is_pure_giant_style:
                tail_hold_sec = min(3.0, max(1.8, total_dur * 0.08))
                tail_target_sf = max(
                    int(last_sent.get("start_frame", 0)),
                    total_frames - int(round(tail_hold_sec * fps)),
                )
                ls_sf = max(last_vis_end, min(tail_target_sf, total_frames - int(round(1.45 * fps))))
                ls_ef = total_frames
            else:
                ls_sf = int(last_sent.get("start_frame", 0))
                ls_ef = min(total_frames, max(ls_sf + int(1.45 * fps), int(last_sent.get("end_frame", ls_sf))))
            if ls_sf >= last_vis_end and (ls_ef - ls_sf) / float(fps) >= 1.40 and not overlaps_code(ls_sf, ls_ef):
                _cta_raw = _flower_label_for_style(
                    _extract_clean_flower_phrase(last_sent, raw_pain_kws=raw_pain_kws, max_len=8),
                    _fchars_max)
                cta_label = _cta_raw if _cta_raw and not text_quality_issue(_cta_raw) else "评论区聊聊"
                if is_pure_giant_style:
                    cta_label = _trim_giant_phrase(cta_label, max_chars=max(4, giant_max_chars + 1)) or "评论区聊聊"
                bw = _measure_pkg_badge_width(cta_label, pkg_font_path, max_pkg_w, target_fs=64 if is_pure_giant_style else 54, min_w=240)
                bx = SUBTITLE_CENTER_X - bw // 2
                active_f = []
                for c in edl.get("clips", []):
                    if int(c["timeline_in_frame"]) < ls_ef and int(c["timeline_out_frame"]) > ls_sf:
                        active_f.extend(faces_by_mp4.get(str(c.get("media_path", "")), []))
                cta_init_y = 260 if is_pure_giant_style else 890
                cta_bb = _avoid_faces_for_pkg({"x": bx, "y": cta_init_y, "w": bw, "h": 128}, active_f)
                if cta_bb["y"] + cta_bb["h"] > 1120:
                    cta_bb["y"] = max(162, 1120 - cta_bb["h"])
                ok_cta, _ = check_bbox_in_safe_zone(cta_bb)
                cta_sub = "" if (cta_label == "评论区聊聊" or "评论区" in cta_label) else "评论区聊聊"
                visual_elements.append({
                    "id": f"pkg_{len(visual_elements)+1:03d}",
                    "kind": "end_card_cta",
                    "overlay_mode": "borderless",
                    "fg_hex": str(flower_spec.get("end_card_color") or giant_fg or "#FFE28A"),
                    "stroke_hex": str(flower_spec.get("stroke_color") or "#000000"),
                    "text_or_asset": cta_label,
                    "subtext": cta_sub,
                    "start_frame": ls_sf,
                    "end_frame": ls_ef,
                    "read_hold_sec": round((ls_ef - ls_sf) / float(fps), 2),
                    "aligned_sfx_or_accent_frame": ls_sf,
                    "alignment_error_frames": 0,
                    "bbox": cta_bb,
                    "final_bbox": cta_bb,
                    "safe_zone_ok": ok_cta,
                    "jianying_anim_name": str(_pair_for_slot(len(visual_elements)).get("jianying_anim_name") or default_jy_anim),
                    "anim_duration_ms": int(_pair_for_slot(len(visual_elements)).get("anim_duration_ms", 400)),
                    "paired_jianying_sfx": str(_pair_for_slot(len(visual_elements)).get("jianying_sfx_name", "“呼”的转场音效")),
                    "trigger": "ending",
                    "policy": f"{style_id} 片尾互动召唤（收束结构件，压到片尾帧，不侵入字幕带）",
                    "reason": "片尾收束互动召唤卡",
                })
                has_end_cta = True

    # ── 片尾收尾诊断：话没说完、没有行动指引的，补一张 CTA 尾卡（不改台词、不改原声）──
    ending = None
    try:
        import ending_plan as EP
        tr_p = pipeline_dir / "02_transcript.json"
        transcript = read_json(tr_p) if tr_p.is_file() else {}
        segs = transcript.get("segments") or [{"text": str((audio_events["sentences"][-1] or {}).get("text") or "")}]
        profile = {}
        for cand in (pipeline_dir / "00_account_profile.json", pipeline_dir.parent / "content-log" / "account_profile.json"):
            if cand.is_file():
                try:
                    profile = read_json(cand)
                except Exception:
                    profile = {}
                break
        topic_hint = ""
        if len(segs) >= 2:
            topic_hint = str((audio_events["sentences"][-2] or {}).get("text") or "")
        ending = EP.analyze_ending(segs, profile=profile, topic_hint=topic_hint)
        ending["framework_map"] = EP.map_framework(segs)
        if ending["framework_map"]["missing"]:
            print(f"[step6] 台词五格体检：缺「{'、'.join(ending['framework_map']['missing'])}」")
        has_end_card = any(str(e.get("kind")) == "end_card_cta" for e in visual_elements)
        # 用户显式关了花字（--no-flower-text）就不硬塞尾卡，只在报告里点名"片尾没收住"
        if (ending["verdict"] != "closed" and not has_end_card and not no_flower_text
                and len(visual_elements) < max_visual_budget):
            sf, ef = EP.card_frames(ending, total_frames, fps)
            if ef - sf >= int(1.2 * fps):
                text = EP.best_card_text(ending)
                _card_main, _card_sub = EP.split_card_text(text)
                fs_try = 46
                bw = _measure_pkg_badge_width(text[:16], pkg_font_path, SUBTITLE_SYMMETRIC_MAX_W, target_fs=fs_try, min_w=520)
                bh = 132
                bx = SUBTITLE_CENTER_X - bw // 2
                bb = {"x": bx, "y": 820, "w": bw, "h": bh}
                active_f = []
                for c in edl.get("clips", []):
                    if int(c["timeline_in_frame"]) < ef and int(c["timeline_out_frame"]) > sf:
                        active_f.extend(faces_by_mp4.get(str(c.get("media_path", "")), []))
                bb = _avoid_faces_for_pkg(bb, active_f)
                ok_card, _ = check_bbox_in_safe_zone(bb)
                visual_elements.append({
                    "id": f"pkg_{len(visual_elements)+1:03d}",
                    "kind": "end_card_cta",
                    "overlay_mode": "borderless",
                    "fg_hex": str(flower_spec.get("end_card_color") or "#FFE28A"),
                    "stroke_hex": str(flower_spec.get("stroke_color") or "#000000"),
                    "text_or_asset": _card_main,
                    "subtext": _card_sub,
                    "start_frame": sf,
                    "end_frame": ef,
                    "read_hold_sec": round((ef - sf) / float(fps), 2),
                    "aligned_sfx_or_accent_frame": sf,
                    "alignment_error_frames": 0,
                    "bbox": bb,
                    "final_bbox": bb,
                    "safe_zone_ok": ok_card,
                    "jianying_anim_name": str(_pair_for_slot(len(visual_elements)).get("jianying_anim_name") or default_jy_anim),
                    "anim_duration_ms": int(_pair_for_slot(len(visual_elements)).get("anim_duration_ms", 400)),
                    "paired_jianying_sfx": str(_pair_for_slot(len(visual_elements)).get("jianying_sfx_name", "“呼”的转场音效")),
                    "trigger": "ending",
                    "policy": "片尾收尾补位卡（话没说完时补一张，打 deviation_from_style_source 标）",
                    "reason": f"片尾收尾补位：{ending['reason']}",
                    "deviation_from_style_source": True,
                })
                ending["card_added"] = True
                print(f"[step6] 片尾补了一张收尾卡（{ending['verdict']}）：{text[:20]}")
        else:
            ending["card_added"] = has_end_card
        if ending.get("needs_operator_line") and not ending.get("card_added"):
            print(f"[step6] [提示] {ending['reason']}；下次拍的时候末句可以补一句行动指引")
    except Exception as exc:
        ending = {"verdict": "unknown", "reason": f"收尾诊断不可用：{type(exc).__name__}: {exc}"}

    # ③ 同框指示贴纸 & 出场退场动画（仅当因果规则明确 present=True；动态花字舞台模式在末句 CTA 自行统筹，不插旧式双挂卡片）
    _st_rule = element_rules.get_element_rule(style_id, "sticker")
    if _st_rule and _st_rule.get("present") and not no_flower_text and not kinetic_stage_mode:
        min_st_frames = int(math.ceil(1.36 * fps))
        text_anchors = [
            v for v in visual_elements
            if str(v.get("kind") or "") in ("center_burst_title", "flower_text", "stamp_tag")
            and int(v.get("end_frame") or 0) - int(v.get("start_frame") or 0) >= min_st_frames
        ]
        chosen_anchors: List[Dict[str, Any]] = []
        for cand_a in text_anchors:
            if not chosen_anchors:
                chosen_anchors.append(cand_a)
            elif int(cand_a.get("start_frame") or 0) - int(chosen_anchors[-1].get("start_frame") or 0) >= int(5.0 * fps):
                chosen_anchors.append(cand_a)
                break
        if len(chosen_anchors) < 2 and len(text_anchors) >= 2 and text_anchors[-1] is not chosen_anchors[0]:
            chosen_anchors.append(text_anchors[-1])
        _st_max_dur = float(element_rules.get_limit(style_id, "sticker", "max_dur_sec", default=2.4) or 2.4)
        for st_i, anc in enumerate(chosen_anchors[:2]):
            ab = anc.get("final_bbox") or anc.get("bbox") or {"x": 300, "y": 400, "w": 360, "h": 180}
            st_sf = min(int(anc["end_frame"]) - min_st_frames, int(anc["start_frame"]) + 3)
            st_sf = max(int(anc["start_frame"]), st_sf)
            st_ef = min(int(anc["end_frame"]), st_sf + int(min(_st_max_dur, 1.75) * fps))
            if st_ef - st_sf < min_st_frames:
                st_sf = int(anc["start_frame"])
                st_ef = min(int(anc["end_frame"]), st_sf + int(min(_st_max_dur, 1.75) * fps))
            if st_ef - st_sf < min_st_frames:
                continue
            # 贴靠字块左侧或右侧边缘（8px 紧凑间距），若字块宽大，优先贴上缘侧方，避免悬空或撞字；arrow 按位置指回字块
            ax0, ay0, aw, ah = int(ab["x"]), int(ab["y"]), int(ab["w"]), int(ab["h"])
            if ax0 + aw + 188 <= framed_x_max:
                sx = ax0 + aw + 8
                p_dir = "left"
            elif ax0 - 188 >= framed_x_min:
                sx = ax0 - 188
                p_dir = "right"
            else:
                sx = max(framed_x_min, min(framed_x_max - 180, ax0 - 140))
                p_dir = "right" if sx < ax0 + aw // 2 else "left"
            sy = max(max(framed_top_y, SAFE_ZONE["y_min"]), min(min(920, SAFE_ZONE["y_max"] - 180), ay0 + (ah // 2) - 90))
            st_bb = {"x": sx, "y": sy, "w": 180, "h": 180}
            st_kind = "thumbs_up" if st_i == 0 else "red_arrow"
            visual_elements.append({
                "id": f"pkg_{len(visual_elements)+1:03d}",
                "kind": "indicator_sticker",
                "sticker_kind": st_kind,
                "point_dir": p_dir,
                "paired_element_id": anc.get("id"),
                "overlay_mode": "borderless",
                "text_or_asset": "点赞手势" if st_kind == "thumbs_up" else "红箭头指引",
                "start_frame": st_sf,
                "end_frame": st_ef,
                "read_hold_sec": round((st_ef - st_sf) / float(fps), 2),
                "aligned_sfx_or_accent_frame": st_sf,
                "alignment_error_frames": 0,
                "bbox": st_bb,
                "final_bbox": st_bb,
                "safe_zone_ok": check_bbox_in_safe_zone(st_bb)[0],
                "anim_primitive": "弹入",
                "out_anim": "fade_out" if st_i == 0 else "slide_down",
                "out_anim_sec": 0.45,
                "trigger": "sticker_pair",
                "policy": f"{style_id} 同框指示贴纸（配「{anc.get('text_or_asset')}」，满足 text_pair_rate=100% 且同刻 ≤1 层）",
                "reason": f"同框指示贴纸（{st_kind}）强化当刻核心字块",
            })

    _out_rule = element_rules.get_element_rule(style_id, "out_anim")
    if _out_rule and _out_rule.get("present"):
        out_slot = 0
        for ve in visual_elements:
            if str(ve.get("kind") or "") in ("end_card_cta",) or ve.get("out_anim"):
                continue
            if int(ve.get("end_frame") or 0) >= total_frames - int(0.35 * fps):
                continue
            if float(ve.get("read_hold_sec") or 0.0) < 1.05:
                continue
            ve["out_anim"] = "fade_out" if (out_slot % 2 == 0) else "slide_down"
            # 文字退场缩为干脆 0.24s（7帧），保住主段 80%+ 实色清晰驻留，杜绝 1.4s 标题早早虚化溶底
            ve["out_anim_sec"] = 0.24
            out_slot += 1

    # 从最终真正上屏的 visual_elements 提取展示字锚点给音效策略（花字/大字/尾卡均有依据）
    _vis_anchor_kinds = {"flower_text", "stamp_tag", "quote_citation_card", "center_burst_title", "end_card_cta"}
    vis_anchor_specs: List[Dict[str, Any]] = [
        {
            "start_sec": round(int(v.get("start_frame") or 0) / float(fps), 4),
            "end_sec": round(int(v.get("end_frame") or 0) / float(fps), 4),
            "origin_role": str(v.get("display_role") or v.get("kind") or "flower"),
            "trigger": str(v.get("trigger") or ""),
        }
        for v in visual_elements
        if str(v.get("kind") or "") in _vis_anchor_kinds
        and int(v.get("end_frame") or 0) > int(v.get("start_frame") or 0)
        and float(int(v.get("start_frame") or 0) / float(fps)) <= total_dur - 0.5
    ]

    # ② 音效候选：结构性切点 + 已落盘上屏的展示字出现/退出 + 字幕换行 + BGM 拍点（去重后交给策略判定）
    sfx_cands: List[Dict[str, Any]] = []
    _seen_t: set = set()

    def _add_cand(t: float, **kw: Any) -> None:
        try:
            key = round(float(t), 2)
        except (TypeError, ValueError):
            return
        if key in _seen_t:
            return
        _seen_t.add(key)
        sfx_cands.append({"time_sec": round(float(t), 4), **kw})

    sfx_pol = SfxPolicy(style_id)
    # 若该风格在「切点 cut」下混用多种角色（如 style_21 真值同时用 info/accent/cut），按实测权重轮流派角，避免全挤进单一角色撞 role_min_gap
    _cut_role_cycle: List[str] = []
    _roles_tbl = (sfx_pol.rules or {}).get("roles") or {}
    for _r_name, _r_meta in sorted(
        _roles_tbl.items(),
        key=lambda kv: -float(((kv[1] or {}).get("triggers") or {}).get("cut") or 0.0),
    ):
        _cut_cnt = int((( _r_meta or {}).get("triggers") or {}).get("cut") or 0)
        if _cut_cnt > 0 and _r_name in sfx_files and sfx_files[_r_name]:
            _cut_role_cycle.extend([_r_name] * min(4, _cut_cnt))

    cut_role_idx = 0
    for ev in cut_events:
        cand_kw: Dict[str, Any] = {
            "origin": "cut",
            "event_type": ev["type"],
            "shot_type": ev["shot_type"],
            "event_id": ev["event_id"],
        }
        if _cut_role_cycle:
            cand_kw["role"] = _cut_role_cycle[cut_role_idx % len(_cut_role_cycle)]
            cut_role_idx += 1
        _add_cand(ev["time_sec"], **cand_kw)
    for fl in vis_anchor_specs:
        _orole = "number" if fl["trigger"] == "number" else fl["origin_role"]
        _add_cand(fl["start_sec"], origin=_orole, event_type="sentence_start")
        if fl.get("end_sec"):
            _erole = "end_card_cta" if fl["origin_role"] == "end_card_cta" else "flower_exit"
            _add_cand(fl["end_sec"], origin=_erole, event_type="sentence_start")
    if "line" in sfx_pol.allowed_triggers:
        for t_line in line_starts:
            _add_cand(t_line, origin="line", event_type="sentence_start")
    if "beat" in sfx_pol.allowed_triggers:
        for t_beat in beat_times:
            _add_cand(t_beat, origin="beat", event_type="bgm_beat")

    sfx_res = sfx_pol.evaluate(sfx_cands, {
        "cut_events": cut_events,
        "cut_times": cut_times_list,
        "flower_starts": [f["start_sec"] for f in vis_anchor_specs],
        "flower_ends": [f["end_sec"] for f in vis_anchor_specs if f.get("end_sec")],
        "line_starts": line_starts,
        "beats": beat_times,
        "sentences": sent_spans,
        "duration_sec": total_dur,
    })
    if not sfx_pool:
        sfx_res["accepted"] = []
    if len(sfx_res["accepted"]) > max_sfx_budget:
        for extra in sfx_res["accepted"][max_sfx_budget:]:
            sfx_res["dropped"].append({
                "time_sec": extra.get("time_sec"), "reason": extra.get("reason"),
                "anchor_time": extra.get("anchor_time"), "dropped_because": "budget_cap",
                "detail": f"超过该风格每分密度换算的预算 {max_sfx_budget} 条",
            })
            sfx_res["rows"].append({"time_sec": extra.get("time_sec"), "decision": "drop",
                                    "trigger": extra.get("reason"), "anchor_time": extra.get("anchor_time"),
                                    "offset_sec": extra.get("offset_sec"), "why": "budget_cap"})
        sfx_res["accepted"] = sfx_res["accepted"][:max_sfx_budget]
        sfx_res["n_accepted"] = len(sfx_res["accepted"])

    sfx_cues: List[Dict[str, Any]] = []
    has_style_sfx_truth = bool(sfx_pool) or (ASSETS_DIR / "sound_vibe" / "sfx" / f"{style_id}_accent.wav").is_file()
    if kinetic_stage_mode and kinetic_stage_plan is not None and has_style_sfx_truth:
        # ── 动态花字舞台 0ms 音效咬合通道：每个花字动效按角色直配真实专属音色，呼吸过渡句严格 0 音效 ──
        k_sfx_assets = kinetic_choreographer.ensure_kinetic_sfx_assets(
            pipeline_dir / "06_kinetic_sfx", style_id=style_id, assets_dir=ASSETS_DIR,
        )
        k_items_sorted = sorted(
            [it for it in (kinetic_stage_plan.get("items") or []) if it.get("sfx_role")],
            key=lambda it: (float(it.get("start_sec") or 0.0), int(it.get("track_lane") or 0)),
        )
        # 遵守 style_02 因果规则：min_gap_sec >= 0.60s，总数 <= max_sfx_budget
        last_k_sfx_t = -9.9
        k_rows: List[Dict[str, Any]] = []
        k_dropped: List[Dict[str, Any]] = []
        for it in k_items_sorted:
            t_it = round(float(it.get("start_sec") or 0.0), 4)
            role_k = str(it.get("sfx_role") or "swish")
            wav_p = k_sfx_assets.get(role_k) or next(iter(k_sfx_assets.values()), None)
            if wav_p is None or not Path(wav_p).is_file():
                continue
            if t_it - last_k_sfx_t < 0.58:
                k_dropped.append({"time_sec": t_it, "reason": "kinetic_min_gap", "dropped_because": "min_gap_0.58s", "detail": f"与前音间距<{0.58:.2f}s"})
                k_rows.append({"time_sec": t_it, "decision": "drop", "trigger": "kinetic_stage_item", "anchor_time": t_it, "offset_sec": 0.0, "why": f"{role_k}｜间距<0.58s跳过"})
                continue
            if len(sfx_cues) >= max_sfx_budget:
                k_dropped.append({"time_sec": t_it, "reason": "budget_cap", "dropped_because": "budget_cap", "detail": f"达风格音效预算上限 {max_sfx_budget}"})
                k_rows.append({"time_sec": t_it, "decision": "drop", "trigger": "kinetic_stage_item", "anchor_time": t_it, "offset_sec": 0.0, "why": f"{role_k}｜预算上限跳过"})
                continue
            frame_c = int(round(t_it * fps))
            cue_t = round(frame_c / float(fps), 4)
            gain_k = -4.2 if role_k in ("boom_heavy", "punch", "boom_hollow", "cash_register") else -4.8
            sfx_cues.append({
                "id": "",
                "file": str(Path(wav_p).resolve()),
                "sfx_name": Path(wav_p).name,
                "sfx_role": role_k,
                "time_sec": cue_t,
                "frame": frame_c,
                "gain_db": gain_k,
                "event_id": it.get("id"),
                "role": role_k,
                "reason": "kinetic_stage_item",
                "anchor": {"kind": "kinetic_stage_item", "time_sec": t_it, "offset_sec": round(cue_t - t_it, 4), "origin": str(it.get("jianying_anim_name") or it.get("variant") or "kinetic")},
                "anchor_time": t_it,
                "policy": f"{style_id} 花字动效↔音效 0ms 咬合 ({it.get('jianying_anim_name') or role_k} ↔ {role_k})",
            })
            k_rows.append({"time_sec": cue_t, "decision": "keep", "trigger": "kinetic_stage_item", "anchor_time": t_it, "offset_sec": round(cue_t - t_it, 4), "why": f"{role_k}｜{it.get('jianying_anim_name') or '动效'}咬合"})
            last_k_sfx_t = t_it
        sfx_res = {
            "style_id": style_id,
            "rules_used": f"kinetic_stage:{style_id}",
            "n_candidates": len(k_items_sorted),
            "n_accepted": len(sfx_cues),
            "n_dropped": len(k_dropped),
            "accepted": sfx_cues,
            "dropped": k_dropped,
            "rows": k_rows,
        }
        sfx_disabled_reason = None
    else:
        if not sfx_pool:
            sfx_res["accepted"] = []
        last_sfx_name: Optional[str] = None
        recent_sfx_usage: Dict[str, List[float]] = {}
        # 多个情绪/角色别名（twist=accent, detail=info, persona=cut）指向相同底层音效文件，
        # 按真实唯一文件路径去重统计池规模，避免 60s 滑窗额度被虚高分母折半而在片尾 48-61s 误杀整段音效；
        # 同时结合片段时长限制单文件最低约 11.5s 才允许重复一次（短片 <=16s 仍严控 <=2 次）。
        _unique_sfx_paths = {str(p.resolve()) for v in sfx_files.values() for p in (v or [])}
        _total_pool_files = max(1, len(_unique_sfx_paths))
        _budget_per_file = int(math.ceil(max(1, max_sfx_budget) / float(_total_pool_files)))
        _interval_per_file = max(2, int(math.ceil(max(1.0, total_dur) / 11.5)))
        max_per_file_60s = max(2, min(8, min(_budget_per_file, _interval_per_file)))
        _REASON_TRIM = {"cut": 0.5, "flower": 1.0, "line": -1.2, "beat": -1.6, "free": -1.6}
        for acc in sfx_res["accepted"]:
            t_cue = round(float(acc["time_sec"]), 4)
            reason_s = str(acc.get("reason") or "")
            origin_s = str(acc.get("origin") or "")
            role_pick = str(acc.get("role") or "misc")
            if origin_s in ("giant_text", "flower", "number", "center_burst_title") or reason_s == "flower":
                role_order = ["accent", "hook", "info", "cut"]
            elif origin_s in ("flower_exit", "end_card", "end_card_cta"):
                role_order = ["info", "hook", "accent", "cut"]
            elif reason_s == "cut":
                role_order = ["cut", "accent", "info", "hook"]
            elif reason_s == "line":
                role_order = ["info", "accent", "hook", "cut"]
            else:
                role_order = [role_pick, "cut", "accent", "info", "hook"]
            if role_pick in sfx_files and role_pick not in role_order:
                role_order = [role_pick] + role_order
            elif role_pick in sfx_files and role_order[0] != role_pick and reason_s not in ("flower", "cut"):
                role_order = [role_pick] + [r for r in role_order if r != role_pick]
            elif reason_s == "cut" and role_pick in sfx_files and sfx_files.get(role_pick):
                role_order = [role_pick] + [r for r in role_order if r != role_pick]

            sf: Optional[Path] = None
            picked_role = role_pick
            tried_paths: set = set()
            for cand_role in role_order:
                c_list = [p for p in (sfx_files.get(cand_role) or []) if str(p) not in tried_paths]
                if not c_list:
                    continue
                sf_try = rotate_sfx(
                    c_list, last_sfx_name,
                    recent_usage=recent_sfx_usage, cur_time_sec=t_cue, max_same_per_60s=max_per_file_60s,
                )
                if sf_try is not None:
                    sf = sf_try
                    picked_role = cand_role
                    break
                tried_paths.update(str(p) for p in c_list)
            if sf is None:
                fallback_cands = [p for _r in _SFX_ROLES for p in sfx_pool.get(_r, []) if str(p) not in tried_paths]
                sf = rotate_sfx(
                    fallback_cands, last_sfx_name,
                    recent_usage=recent_sfx_usage, cur_time_sec=t_cue, max_same_per_60s=max_per_file_60s,
                )
            if sf is None:
                sfx_res["dropped"].append({
                    "time_sec": t_cue, "reason": acc.get("reason"),
                    "anchor_time": acc.get("anchor_time"), "dropped_because": "same_sfx_60s_cap",
                    "detail": f"60s 滑窗内本风格可用音效均已达到单文件上限 {max_per_file_60s} 次",
                })
                sfx_res["n_dropped"] = len(sfx_res["dropped"])
                continue
            recent_sfx_usage.setdefault(sf.name, []).append(t_cue)
            last_sfx_name = sf.name
            frame_c = int(round(t_cue * fps))
            cue_gain = round(max(-14.0, min(-3.5, base_gain_db + _REASON_TRIM.get(reason_s, 0.0))), 1)
            sfx_cues.append({
                "id": "",
                "file": str(sf.resolve()),
                "sfx_name": sf.name,
                "time_sec": round(frame_c / float(fps), 4),
                "frame": frame_c,
                "gain_db": cue_gain,
                "event_id": acc.get("event_id"),
                "role": picked_role if role_pick == "misc" else role_pick,
                "reason": acc.get("reason"),
                "anchor": {"kind": acc.get("reason"), "time_sec": acc.get("anchor_time"),
                           "offset_sec": acc.get("offset_sec"), "origin": acc.get("origin")},
                "anchor_time": acc.get("anchor_time"),
                "policy": acc.get("policy"),
            })

    sfx_cues.sort(key=lambda x: x["frame"])
    for idx_cue, cue in enumerate(sfx_cues, start=1):
        cue["id"] = f"sfx_{idx_cue:03d}"

    sfx_track = np.zeros((total_samples, 2), dtype=np.float32)
    sfx_cache: Dict[str, np.ndarray] = {}
    for cue in sfx_cues:
        fp = cue["file"]
        if fp not in sfx_cache:
            arr, _ = load_stereo_wav_np(Path(fp))
            m_abs = np.abs(arr.mean(axis=1))
            active_m = m_abs[m_abs >= max(1e-4, float(np.max(m_abs)) * 0.15)]
            core_rms = float(np.sqrt(np.mean(active_m ** 2) + 1e-10)) if len(active_m) > 0 else 0.01
            norm_gain = min(10.0, 0.25 / max(1e-4, core_rms))
            peak_cap = 0.85 / max(1e-4, float(np.max(np.abs(arr))))
            arr = (arr * min(norm_gain, peak_cap)).astype(np.float32)
            arr = _enhance_sfx_presence(arr, SR)
            if sfx_per_min_tgt >= 25.0 and len(arr) > int(1.0 * SR):
                pk_sec = _measure_sfx_peak_offset_sec(arr, SR)
                max_len = min(len(arr), int((pk_sec + 0.85) * SR))
                if max_len >= int(0.45 * SR) and max_len < len(arr):
                    arr = _fade_sfx_edges(arr[:max_len], SR, fade_in_sec=0.002, fade_out_sec=0.08)
                else:
                    arr = _fade_sfx_edges(arr, SR)
            else:
                arr = _fade_sfx_edges(arr, SR)
            sfx_cache[fp] = arr
        arr = sfx_cache[fp]
        peak_off = _measure_sfx_peak_offset_sec(arr, SR)
        onset_sec = max(0.0, float(cue["time_sec"]) - peak_off)
        cue["onset_time_sec"] = round(onset_sec, 4)
        cue["peak_time_sec"] = round(onset_sec + peak_off, 4)
        gain = 10.0 ** (min(-3.0, float(cue["gain_db"])) / 20.0)
        start_idx = max(0, int(round(onset_sec * SR)))
        end_idx = min(total_samples, start_idx + len(arr))
        if end_idx > start_idx:
            piece = arr[: end_idx - start_idx]
            if end_idx - start_idx < len(arr):
                piece = _fade_sfx_edges(piece, SR, fade_in_sec=0.0)
            sfx_track[start_idx:end_idx] += piece * gain

    sfx_wav_path = pipeline_dir / "06_sfx_track.wav"
    save_stereo_wav_np(sfx_wav_path, sfx_track, SR)

    packaging = {
        "step": "06_packaging",
        "style_id": style_id,
        "no_flower_text": bool(no_flower_text),
        "sfx_track_wav": str(sfx_wav_path.resolve()),
        "sfx_disabled_reason": sfx_disabled_reason,
        "sfx_per_min_target": round(float(sfx_per_min_tgt), 1),
        "sfx_source_dir": f"sound_vibe/sfx/{style_id}",
        "bgm": bgm_info,
        "bgm_disabled_reason": bgm_info.get("disabled_reason"),
        "budget": {
            "max_visual_elements": max_visual_budget,
            "used_visual_elements": len(visual_elements),
            "max_sfx_cues": max_sfx_budget,
            "used_sfx_cues": len(sfx_cues),
        },
        "sfx_cues": sfx_cues,
        "sfx_dropped": sfx_res.get("dropped") or [],
        "sfx_justification": {
            "rules_used": sfx_res.get("rules_used"),
            "n_candidates": sfx_res.get("n_candidates"),
            "n_accepted": sfx_res.get("n_accepted"),
            "rows": sfx_res.get("rows"),
        },
        "flower_dropped": flower_res.get("dropped") or [],
        "flower_justification": {
            "rules_used": flower_res.get("rules_used"),
            "n_candidates": flower_res.get("n_candidates"),
            "n_accepted": flower_res.get("n_accepted"),
            "rows": flower_res.get("rows"),
        },
        "visual_elements": visual_elements,
        "kinetic_stage": kinetic_stage_plan,
        "ending_plan": ending,
    }
    write_json(pipeline_dir / "06_packaging.json", packaging)
    # 依据清单：每条音效/花字出或不出、凭什么、参照哪个锚点，全部打印出来备查
    if (not sfx_pool) and (not sfx_cues):
        print(f"[step6] 音效关闭：{sfx_disabled_reason}")
    elif sfx_cues:
        print(format_sfx_table(sfx_res, limit=400))
    print(format_flower_table(flower_res, limit=400))
    print(
        f"[step6] 包装完成：音效 {len(sfx_cues)}/{max_sfx_budget} 个（候选 {sfx_res.get('n_candidates')}，"
        f"每条都挂锚点），视觉包装 {len(visual_elements)}/{max_visual_budget} 个"
        f"（花字候选 {flower_res.get('n_candidates')}，每条都有出处）-> 06_packaging.json"
    )
    return packaging


def main() -> None:
    maybe_reexec_in_venv()
    parser = argparse.ArgumentParser(description="Step 6: 音效先行与±1帧对齐视觉包装")
    parser.add_argument("--pipeline-dir", required=True)
    parser.add_argument("--style-id", help="绑定风格谱 ID")
    parser.add_argument("--no-flower-text", action="store_true", help="关闭全部自动花字/巨字，仅保留字幕与音效")
    parser.add_argument("--keep-manual-texts", action="store_true", help="换风格时保留已有人工编辑的花字文案")
    args = parser.parse_args()
    run_step6(
        Path(args.pipeline_dir),
        style_id=args.style_id,
        no_flower_text=args.no_flower_text,
        keep_manual_texts=args.keep_manual_texts,
    )


if __name__ == "__main__":
    main()
