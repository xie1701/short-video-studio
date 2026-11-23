#!/usr/bin/env python3
"""
按用户的背景资料纠 ASR 的错（用户实测：很多转录没有针对他的背景资料纠错）。

为什么要它
──────────
Whisper 对「店名 / 人名 / 行话 / 品牌词 / 地名」几乎必错：同音字、方言音、专有名词都被
拉成常见词。以前只靠 `--store-anchor`（一句品牌提示）塞进 initial_prompt，等于给模型
一句口头提醒，错了也不会有人拉回来。

这里做两件事，都是本地的、零联网、零模型：
1. **喂给 ASR 的提示词**：把用户的背景资料压成一段「专有名词上下文」，拼进 initial_prompt，
   让模型先知道"这个词会出现在这段话里"。
2. **转写之后硬纠**：拿用户的术语表逐条比对，只做**可证明**的替换——长度相同、只差一个字、
   且差异位置在术语里（同音字错位的典型形态），或者用户自己写了 `错词 => 正词` 的映射。
   每一次替换都记进 `asr_corrections`，永远能反查"这句被我改过哪里"。

文件从哪读（按优先级，先命中先用，全部读也不冲突）
──────────────────────────────────────────────
  <pipeline_dir>/        ← 项目内（最高优先，改完立刻生效）
  <project_dir>/
  <原始素材目录>/
文件名认这些：
  背景资料.md / 背景资料.txt     自由文本，取前 400 字做 ASR 提示
  术语表.txt                    一行一个词；支持 `错词 => 正词`；`#` 开头是注释
  glossary.json                 {"terms": ["羊排", ...], "replacements": {"碳火": "炭火"}}
"""

from __future__ import annotations

import difflib
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

BACKGROUND_FILES = ("背景资料.md", "背景资料.txt", "background.md", "background.txt")
TERM_FILES = ("术语表.txt", "术语表.md", "terms.txt", "glossary.txt")
JSON_FILES = ("glossary.json", "背景资料.json")

MAX_PROMPT_CHARS = 400


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        try:
            return path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return ""


def _parse_term_lines(text: str) -> Tuple[List[str], Dict[str, str]]:
    """`术语表.txt`：一行一个词；`错词 => 正词` 是硬映射；`#` 注释。"""
    terms: List[str] = []
    repl: Dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = line.lstrip("-*· \t")
        if "=>" in line:
            wrong, right = [x.strip() for x in line.split("=>", 1)]
            if wrong and right:
                repl[wrong] = right
                continue
        for tok in re.split(r"[，,、;；\s]+", line):
            tok = tok.strip()
            if 2 <= len(tok) <= 12 and re.search(r"[\u4e00-\u9fffA-Za-z]", tok):
                terms.append(tok)
    return terms, repl


def load_glossary(dirs: Sequence[Optional[Path]]) -> Dict[str, Any]:
    """从若干目录读背景资料与术语表，返回 {terms, replacements, background, sources}。"""
    terms: List[str] = []
    repl: Dict[str, str] = {}
    background_parts: List[str] = []
    sources: List[str] = []
    seen: set = set()
    for d in dirs:
        if not d:
            continue
        d = Path(d)
        if not d.is_dir():
            continue
        for name in BACKGROUND_FILES:
            p = d / name
            if p.is_file():
                txt = _read_text(p).strip()
                if txt:
                    background_parts.append(txt)
                    sources.append(str(p))
                break
        for name in TERM_FILES:
            p = d / name
            if p.is_file():
                t, r = _parse_term_lines(_read_text(p))
                terms.extend(t)
                repl.update(r)
                sources.append(str(p))
                break
        for name in JSON_FILES:
            p = d / name
            if p.is_file():
                try:
                    doc = json.loads(_read_text(p))
                except Exception:
                    continue
                if isinstance(doc, dict):
                    terms.extend([str(x) for x in (doc.get("terms") or []) if str(x).strip()])
                    for k, v in (doc.get("replacements") or {}).items():
                        if str(k).strip() and str(v).strip():
                            repl[str(k)] = str(v)
                    if doc.get("background"):
                        background_parts.append(str(doc["background"]))
                sources.append(str(p))
                break

    # 去重保序
    uniq_terms: List[str] = []
    for t in terms:
        t = str(t).strip()
        if t and t not in seen:
            seen.add(t)
            uniq_terms.append(t)
    # 长词优先：先匹配「全屋定制」再匹配「定制」
    uniq_terms.sort(key=len, reverse=True)
    background = "\n".join(background_parts).strip()
    return {
        "terms": uniq_terms,
        "replacements": repl,
        "pinyin_available": has_pinyin(),
        "background": background[: MAX_PROMPT_CHARS * 3],
        "sources": sources,
    }


def asr_prompt_suffix(glossary: Dict[str, Any]) -> str:
    """拼进 Whisper `initial_prompt` 的一句上下文（有资料才加，没资料返回空串）。"""
    if not glossary:
        return ""
    terms = [t for t in (glossary.get("terms") or []) if len(t) >= 2][:24]
    bg = str(glossary.get("background") or "").strip()
    parts: List[str] = []
    if terms:
        parts.append("以下专有名词请按原样识别：" + "、".join(terms) + "。")
    if bg:
        parts.append(bg[:MAX_PROMPT_CHARS].replace("\n", " "))
    return (" " + " ".join(parts)) if parts else ""


try:  # 可选依赖：装了 pypinyin 才能判「同音」，没装就只做 3 字以上的近似纠错
    from pypinyin import lazy_pinyin as _lazy_pinyin  # type: ignore
except Exception:  # pragma: no cover
    _lazy_pinyin = None


def has_pinyin() -> bool:
    return _lazy_pinyin is not None


def _same_pronunciation(a: str, b: str) -> bool:
    """整词同音（忽略声调）。2 字词只差一个字太容易误伤，必须同音才敢改。"""
    if _lazy_pinyin is None or len(a) != len(b) or a == b:
        return False
    try:
        return _lazy_pinyin(a) == _lazy_pinyin(b)
    except Exception:
        return False


def _one_char_diff(a: str, b: str) -> bool:
    if len(a) != len(b) or a == b:
        return False
    return sum(1 for x, y in zip(a, b) if x != y) <= 1


def correction_pairs(glossary: Dict[str, Any], text: str) -> List[Tuple[str, str]]:
    """在 text 里找出「该改的」片段：硬映射优先，其次是同长度差一字的同音错位。"""
    out: List[Tuple[str, str]] = []
    if not text:
        return out
    for wrong, right in sorted((glossary.get("replacements") or {}).items(), key=lambda kv: -len(kv[0])):
        if wrong and wrong in text:
            out.append((wrong, right))
    for term in glossary.get("terms") or []:
        L = len(term)
        if L < 2 or L > 12:
            continue
        for i in range(0, max(0, len(text) - L + 1)):
            win = text[i : i + L]
            if win == term or not _one_char_diff(win, term):
                continue
            # 一字之差就可能误伤（"这家店"→"那家店"）：3 字以上要求共有的字 >= 2/3；
            # 2 字词只有「整词同音」才敢改（碳火→炭火），否则不动（需要 pypinyin）
            same = sum(1 for x, y in zip(win, term) if x == y)
            if L < 3:
                if not _same_pronunciation(win, term):
                    continue
            elif same < max(2, int(round(L * 0.66))):
                continue
            if not re.search(r"[\u4e00-\u9fff]", win):
                continue
            out.append((win, term))
    # 去重，长片段优先
    seen: set = set()
    uniq: List[Tuple[str, str]] = []
    for a, b in sorted(out, key=lambda ab: -len(ab[0])):
        if a in seen or a == b:
            continue
        seen.add(a)
        uniq.append((a, b))
    return uniq


def correct_text(text: str, glossary: Dict[str, Any]) -> Tuple[str, List[Tuple[str, str]]]:
    """返回 (纠错后文本, 实际发生的替换列表)。"""
    applied: List[Tuple[str, str]] = []
    out = str(text or "")
    for a, b in correction_pairs(glossary, out):
        if a and a in out:
            out = out.replace(a, b)
            applied.append((a, b))
    return out, applied


def apply_to_transcript(segments: Iterable[Dict[str, Any]], glossary: Dict[str, Any]) -> List[Dict[str, Any]]:
    """对整篇转写做纠错：改 text、改 words，逐句留痕（asr_corrections）。"""
    log: List[Dict[str, Any]] = []
    for seg in segments:
        if str(seg.get("drop_reason") or "").startswith("offcamera"):
            continue
        old = str(seg.get("text") or "")
        if not old:
            continue
        new, applied = correct_text(old, glossary)
        if not applied:
            continue
        seg["text"] = new
        seg["text_before_glossary"] = old
        # words 是字幕的落字与时间来源，必须同步改；同一替换通常落在单个词里
        for w in seg.get("words") or []:
            wt = str(w.get("word") or "")
            if not wt:
                continue
            for a, b in applied:
                if a in wt:
                    w["word"] = wt.replace(a, b)
                    wt = str(w["word"])
        seg["asr_corrections"] = [{"from": a, "to": b} for a, b in applied]
        log.append({"id": seg.get("id"), "from": old, "to": new, "corrections": seg["asr_corrections"]})
    return log


def summarize(glossary: Dict[str, Any], log: List[Dict[str, Any]]) -> Dict[str, Any]:
    n_pairs = sum(len(x.get("corrections") or []) for x in log)
    return {
        "terms": len(glossary.get("terms") or []),
        "hard_mappings": len(glossary.get("replacements") or {}),
        "has_background": bool(str(glossary.get("background") or "").strip()),
        "sources": list(glossary.get("sources") or []),
        "segments_changed": len(log),
        "replacements_applied": n_pairs,
    }
