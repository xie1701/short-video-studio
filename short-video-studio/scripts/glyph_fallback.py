#!/usr/bin/env python3
"""逐字字形回退（PIL/FreeType 不做字体回退，缺字直接画成空白/豆腐块）。

用户实测（2026-11-24）：「阿里妈妈字体应该是不支持粤语文字的，很多字都显示不出来」。
fontTools 实测 `AlimamaShuHeiTi-Bold.otf` 的 cmap 缺 10 个粤语常用字：
`佢 冇 咗 哋 啲 喺 嘅 嘢 嚟 𠮶`（其中 `𠮶` 是 CJK 扩展 B 区 U+2CA36）；
思源宋体 Heavy 只缺 `𠮶`；全机只有 `STHeiti Medium.ttc`（Heiti TC Medium）这一档覆盖全量。

本模块是这条链的唯一真源：
- 回退链写在 `assets/fonts/font_aliases.json → glyph_fallback_chain`（有序，随包优先、系统垫底）；
  `.ttc` 用 `index` 显式指定字面（index=0 = Heiti TC Medium，繁体字形，覆盖扩展 B 区）。
- `font_has_glyph()` 优先查 cmap（fontTools），环境里没有 fontTools 时退到像素探针（渲染对比 U+FFFE）。
- `coverage_report()` 是 step5 与 dev_tools/verify_glyph_coverage.py 共用的门禁口径：
  主字体 + 回退链都覆盖不了的字一律报出来（fail-closed：宁可不发片，也不发豆腐块）。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, NamedTuple, Optional, Sequence, Tuple

_SCRIPTS_DIR = Path(__file__).resolve().parent
ASSETS_DIR = _SCRIPTS_DIR.parent / "assets"
ALIAS_PATH = ASSETS_DIR / "fonts" / "font_aliases.json"
CHAIN_KEY = "glyph_fallback_chain"
_TOFU_PROBE = "\ufffe"


class GlyphCoverageError(RuntimeError):
    """主字体 + 回退链都覆盖不到某个字，或回退链配置本身缺失/为空。"""


class FontRef(NamedTuple):
    path: str
    index: int
    label: str

    @property
    def filename(self) -> str:
        return Path(self.path).name


def _aliases_doc() -> Dict[str, Any]:
    try:
        return json.loads(ALIAS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _search_dirs() -> List[Path]:
    # 与 common.find_commercial_font 同一套搜索目录（随包开源 fonts/ + 本机私有 local_assets/ + 系统字体）
    from common import _font_search_dirs  # noqa: PLC0415

    return _font_search_dirs()


def resolve_font_ref(entry: Any) -> Optional[FontRef]:
    """把回退链里的一项（字符串文件名，或 {"file","index"}）解析成可用的绝对路径。

    解析不到返回 None（例如 Windows 上没有 STHeiti）——由调用方跳过，最终由门禁 fail-closed 兜住。
    """
    if isinstance(entry, str):
        name, index = entry, 0
    elif isinstance(entry, dict):
        name = str(entry.get("file") or "").strip()
        try:
            index = int(entry.get("index") or 0)
        except (TypeError, ValueError):
            index = 0
    else:
        return None
    if not name:
        return None
    cand = Path(name).expanduser()
    if cand.is_file():
        return FontRef(str(cand), index, cand.name)
    for d in _search_dirs():
        if not d.exists():
            continue
        direct = d / name
        if direct.is_file():
            return FontRef(str(direct), index, direct.name)
    for d in _search_dirs():
        if not d.exists():
            continue
        try:
            hit = next((p for p in d.glob(f"**/{name}") if p.is_file()), None)
        except OSError:
            hit = None
        if hit is not None:
            return FontRef(str(hit), index, hit.name)
    return None


@lru_cache(maxsize=1)
def glyph_fallback_chain(strict: bool = False) -> Tuple[FontRef, ...]:
    """有序回退链（随包优先、系统垫底）。strict=True 时配置缺失/为空直接抛错（fail-closed）。"""
    raw = _aliases_doc().get(CHAIN_KEY)
    if not isinstance(raw, list) or not raw:
        if strict:
            raise GlyphCoverageError(
                "assets/fonts/font_aliases.json 缺少有效的 glyph_fallback_chain（有序回退链），"
                "缺字将无处回落，按 fail-closed 拒绝继续"
            )
        return ()
    refs: List[FontRef] = []
    seen: set = set()
    for item in raw:
        ref = resolve_font_ref(item)
        if ref is None:
            continue
        key = (ref.path, ref.index)
        if key in seen:
            continue
        seen.add(key)
        refs.append(ref)
    return tuple(refs)


@lru_cache(maxsize=64)
def _cmap_codepoints(path: str, index: int) -> Optional[frozenset]:
    """字体 cmap 码点集合；没有 fontTools 或解析失败返回 None（调用方退到像素探针）。"""
    try:
        from fontTools.ttLib import TTFont  # noqa: PLC0415
    except Exception:
        return None
    font = None
    try:
        font = TTFont(path, fontNumber=index, lazy=True)
        cmap = font.getBestCmap() or {}
        return frozenset(int(cp) for cp in cmap.keys())
    except Exception:
        return None
    finally:
        if font is not None:
            try:
                font.close()
            except Exception:
                pass


@lru_cache(maxsize=4096)
def _glyph_probe_bytes(path: str, index: int, ch: str, size: int = 48) -> bytes:
    from PIL import Image, ImageDraw, ImageFont  # noqa: PLC0415

    font = ImageFont.truetype(path, size, index=index) if index else ImageFont.truetype(path, size)
    img = Image.new("L", (size * 2, size * 2), 0)
    ImageDraw.Draw(img).text((size // 4, size // 4), ch, font=font, fill=255)
    return img.tobytes()


def _probe_has_glyph(path: str, index: int, ch: str) -> bool:
    try:
        sig = _glyph_probe_bytes(path, index, ch)
        if not any(sig):
            return False
        return sig != _glyph_probe_bytes(path, index, _TOFU_PROBE)
    except Exception:
        return False


@lru_cache(maxsize=512)
def load_font_ref(path: str, index: int, size: int) -> Any:
    """按（字体文件, 字面 index, 字号）载入 Pillow 字体：`.ttc` 必须给 index 才能取到目标字面（如 Heiti TC）。"""
    from PIL import ImageFont  # noqa: PLC0415

    from common import get_cached_font  # noqa: PLC0415

    sz = max(12, int(size))
    if index:
        return ImageFont.truetype(str(path), sz, index=int(index))
    return get_cached_font(str(path), sz)


@lru_cache(maxsize=8192)
def glyph_font_for(ch: str, primary_font_path: Optional[str], size: int) -> Any:
    """逐字字形回退：主字体缺这个字就按 glyph_fallback_chain 逐级找，返回已按 size 载入的字体对象。

    只换字体、**不改字号**；整条链都覆盖不了时返回主字体（由覆盖门禁 fail-closed 拦下）。
    """
    sz = max(12, int(size))
    primary = primary_ref(primary_font_path)
    if primary is None:
        from common import get_cached_font  # noqa: PLC0415

        return get_cached_font(None, sz)
    if font_has_glyph(primary.path, primary.index, ch):
        return load_font_ref(primary.path, primary.index, sz)
    ref, _via = font_for_char(ch, primary.path, glyph_fallback_chain())
    if ref is None:
        return load_font_ref(primary.path, primary.index, sz)
    try:
        return load_font_ref(ref.path, ref.index, sz)
    except Exception:
        return load_font_ref(primary.path, primary.index, sz)


@lru_cache(maxsize=65536)
def font_has_glyph(path: str, index: int, ch: str) -> bool:
    """字体是否真有这个字形：先查 cmap，没有 fontTools 时退到像素探针。"""
    if not ch or ch.isspace() or ch == "\n":
        return True
    if ord(ch) < 32:
        return True
    cps = _cmap_codepoints(str(path), int(index))
    if cps is not None:
        return ord(ch) in cps
    return _probe_has_glyph(str(path), int(index), ch)


def primary_ref(primary_font_path: Optional[str]) -> Optional[FontRef]:
    """主字体的 FontRef（拿不到文件路径就返回 None）。"""
    if not primary_font_path:
        return None
    p = Path(str(primary_font_path)).expanduser()
    if not p.is_file():
        return None
    return FontRef(str(p), 0, p.name)


def font_for_char(
    ch: str,
    primary_font_path: Optional[str],
    chain: Optional[Sequence[FontRef]] = None,
) -> Tuple[Optional[FontRef], Optional[str]]:
    """逐字找字体：主字体命中返回 (FontRef, None)；回落返回 (FontRef, 回退字体名)；都覆盖不了 (None, None)。"""
    primary = primary_ref(primary_font_path)
    if primary is not None and font_has_glyph(primary.path, primary.index, ch):
        return primary, None
    refs = tuple(chain) if chain is not None else glyph_fallback_chain()
    for ref in refs:
        if primary is not None and (Path(ref.path) == Path(primary.path)) and ref.index == primary.index:
            continue
        if font_has_glyph(ref.path, ref.index, ch):
            return ref, ref.filename
    return None, None


def coverage_report(
    rows: Iterable[Tuple[str, str]],
    primary_font_path: Optional[str],
    chain: Optional[Sequence[FontRef]] = None,
    strict_chain: bool = True,
) -> Dict[str, Any]:
    """门禁口径：逐字核对「主字体 + 回退链」覆盖情况。

    rows: [(行 id, 文本), ...]（15_subtitles 里就是 (entry["id"], entry["text"])）
    返回 {"chars": 去重字数, "total_chars": 总字数, "missing": [缺字...],
          "fallback_used": {回退字体名: 出现次数}, "fallback_chars": {回退字体名: [字...]},
          "missing_lines": [{"id","text","chars"}], "primary": 主字体名, "chain": [回退链]}
    """
    refs = tuple(chain) if chain is not None else glyph_fallback_chain(strict=strict_chain)
    primary = primary_ref(primary_font_path)
    uniq: List[str] = []
    seen: set = set()
    total = 0
    fallback_used: Dict[str, int] = {}
    fallback_chars: Dict[str, List[str]] = {}
    missing: Dict[str, int] = {}
    missing_lines: List[Dict[str, Any]] = []

    for row_id, text in rows:
        line_missing: List[str] = []
        for ch in str(text or ""):
            if not ch or ch.isspace():
                continue
            total += 1
            if ch not in seen:
                seen.add(ch)
                uniq.append(ch)
            ref, via = font_for_char(ch, primary_font_path, refs)
            if ref is None:
                missing[ch] = missing.get(ch, 0) + 1
                if ch not in line_missing:
                    line_missing.append(ch)
                continue
            if via:
                fallback_used[via] = fallback_used.get(via, 0) + 1
                chars = fallback_chars.setdefault(via, [])
                if ch not in chars:
                    chars.append(ch)
        if line_missing:
            missing_lines.append({"id": str(row_id), "text": str(text or ""), "chars": line_missing})

    return {
        "chars": len(uniq),
        "total_chars": total,
        "missing": sorted(missing),
        "missing_counts": dict(sorted(missing.items())),
        "missing_lines": missing_lines,
        "fallback_used": dict(sorted(fallback_used.items())),
        "fallback_chars": {k: sorted(v) for k, v in sorted(fallback_chars.items())},
        "primary": primary.filename if primary else (Path(primary_font_path).name if primary_font_path else "default"),
        "chain": [f"{r.filename}" + (f"#{r.index}" if r.index else "") for r in refs],
    }


def require_coverage(
    rows: Iterable[Tuple[str, str]],
    primary_font_path: Optional[str],
    chain: Optional[Sequence[FontRef]] = None,
) -> Dict[str, Any]:
    """coverage_report + fail-closed：有缺字就抛 GlyphCoverageError（消息里带缺字与所在行）。"""
    report = coverage_report(rows, primary_font_path, chain=chain)
    if report["missing"]:
        detail = "；".join(
            f"{ln['id']}「{ln['text']}」缺 {''.join(ln['chars'])}" for ln in report["missing_lines"]
        )
        raise GlyphCoverageError(
            f"字体覆盖不足：主字体 {report['primary']} + 回退链 {report['chain']} 仍缺 "
            f"{len(report['missing'])} 个字（{''.join(report['missing'])}）——{detail}"
        )
    return report


def format_report(report: Dict[str, Any]) -> str:
    """人读报告（step5 打印 / verify 脚本共用）。"""
    lines = [
        f"字形覆盖：去重 {report['chars']} 字 / 共 {report['total_chars']} 字，"
        f"主字体 {report['primary']}，回退链 {' → '.join(report['chain']) or '（空）'}",
        f"缺字：{len(report['missing'])}" + (f"（{''.join(report['missing'])}）" if report["missing"] else ""),
    ]
    for font, n in (report.get("fallback_used") or {}).items():
        lines.append(f"回退 {font}：{n} 字次，字面 {''.join((report.get('fallback_chars') or {}).get(font) or [])}")
    for ln in report.get("missing_lines") or []:
        lines.append(f"  ✗ {ln['id']}「{ln['text']}」缺 {''.join(ln['chars'])}")
    return "\n".join(lines)
