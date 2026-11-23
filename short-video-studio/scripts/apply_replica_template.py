#!/usr/bin/env python3
"""复刻模版套用器：把「与素材无关的结构模版」套到**新素材 + 新文案**上，产出原生草稿。

产出的是**可编辑的原生草稿 JSON**（真文字素材、真视频素材、真音频素材，分轨独立），
不是烘好的 mp4；用剪辑软件打开就能改字/改时长/换素材。

时间轴重映射（核心，别改坏）
---------------------------
模版里每段带 `start_ratio` / `duration_ratio`（占总时长比例）与 `slot`（第几段），
关键帧/动画带的是**段内相对比例**。新素材总时长与模版不同时：

1. **驱动轨**（`is_driver` 的主轨）按「段边界锚点」重排：逐段取
   `新段时长 = min(模版段时长, 该素材剩余可用时长)`，从 0 顺次铺开。
   这**不是**把每段等比缩放——等比会让静止段变长、动段变短，节奏就散了。
   素材放完就回到素材头（记 `asset_wrapped`），不肯把一段冻成定格画面。
2. 其余轨（叠加/文字/贴纸/音频）的起点按驱动轨的**边界锚点**做分段线性映射：
   `t' = map(t)`，即「模版第 k 个切点 → 新片第 k 个切点」。时长按该锚点所在
   驱动段的局部伸缩比缩放（叠加视频再被素材剩余时长钳一次）。
3. 关键帧 / 出场动画的时间 = 段内比例 × 新段时长；动画超出段长就钳到段长并记
   `anim_clamped`（不静默丢动画，也不让它溢出到下一段）。
4. 文字先换行、超限才截断：容量是**版式算出来的**（模版 text_slots[].char_cap =
   每行字数 × 可容行数），按可用宽度断行（中文按字、西文按词），行数超上限才截并计 `texts_truncated`；
   每个文字位的 capacity/new_text_len/lines_used/truncated 明细进报告 `text_layout`。
5. **字体按名字就近解析**：模版只带字体名（`text_slots[].font.name`），套用时按
   ① 风格字体资产 → ② 系统字体目录 → ③ 剪映缓存字体目录 逐级找本机文件，
   真找不到才回落系统字体；结果进 `font_resolved` / `font_fallback_slots`。

6. 素材声明宽高取**显示尺寸**（旋转元数据已换过来）；模版带过来的 uv 裁切只在
   「裁切框宽高比 == 新素材宽高比（±3%）」时搬，否则丢掉并计 `crop_uv_dropped`
   （旋转素材必丢：uv 是对未旋转宽高算的，硬搬会把 crop 弄越界直接崩渲染）。

7. **时间上重叠的叠层绝不同源**（硬约束）。原版主轨是同一条真片切成多段，叠加轨再把同一
   素材的另一个入点/抠像叠上来；换成新素材池后池子会轮转复用，一旦复用落在时间重叠的两层，
   画面上就是「两个不同瞬间的同源画面不透明地叠着」= 肉眼可见重影（实测 3.0s 一帧里同一个
   人出现两次）。所以叠层分配时按时间窗登记已占用的素材：冲突就换一条，换不到就**留空**
   并计 `overlay_left_blank`，绝不拿同源素材糊过去。
8. **`same_material_as` 是强绑定**：模版里标了它的段和主轨是同一素材的推近/缩窗重构，
   必须跟主轨同素材、同入点（按原段偏移）——否则推近/缩窗重构图会漂。但若它既无遮罩
   又满幅不透明，渲染出来就是同源第二份画面 → 跳过并计数（不自欺）。
9. **人像抠像层按原结构重建，不再跳过**：原版 `matting.path` 非空的叠加层是「从主轨同一条素材里
   抠出人物压在背景上」。抠像遮罩本身不随模版迁移，但**结构**迁移得了：模版记
   `same_material_as`（同素材同入点）+ 描边颜色/强度，套用时把抠像结构写进新草稿的素材
   `matting`，渲染时在新素材上真跑 Vision 抠像 + 撕纸描边（alpha 合成）。只有绑不到主轨素材
   的抠像层才跳过并计 `cutout_layers_skipped`；渲染时抠像失败（新素材里没人）也不下假前景，
   渲染器会计 `overlay_matte_failed`。**绝不**给它另接一条新素材满幅盖住主轨。
   背景板（原版三明治的中层、不透明且时间窗被抠像层盖住）跟着一起重建（`backdrop_rebuilt`）。
   跳过多少、为什么跳过、重建了几层，逐条进报告；素材池不够就报「素材不足，N 个位留空」。

素材不够时按角色顺序回落（`overlay_card → b_roll → a_roll`、`a_roll → b_roll` 等），
每次回落与复用都进报告；绝不假装「素材刚好够」。

用法：
    python3 scripts/apply_replica_template.py --template tpl.json \
        --assets assets.json --texts texts.json --out /tmp/new_draft --style-id style_21
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

US = 1_000_000
CANVAS_W, CANVAS_H = 1080, 1920

# 角色回落链：写出来的素材角色 → 可以顶替的角色（按顺序）
# 音频刻意不互通：音乐床（bgm）与音效（sfx）不是同一种东西 —— 把 15s 音乐床
# 切成十来个音效槽会把声音弄成断续噪声，比「没声音效」更不诚实，所以 sfx 缺素材
# 一律跳过并计数（报告里看得到），只有 bgm 缺素材时才允许拿 sfx 顶主床。
ROLE_FALLBACK = {
    "a_roll": ("a_roll", "b_roll", "photo"),
    "b_roll": ("b_roll", "a_roll", "photo"),
    "overlay_card": ("overlay_card", "b_roll", "a_roll", "photo"),
    # photo **只回落 photo**：复合片段里的「照片翻页/拼贴底板」层（原版是 5 张 scale 0.8 的静图）
    # 在换素材后必须还是图片。拿视频顶 = 把「照片翻页」变成「视频墙」，结构没重建、只是換了张皮；
    # 池里真没图就留空并计 photo_layer_no_image，再写到报告里——角色不匹配就如实报缺口。
    "photo": ("photo",),
    "sticker": ("sticker",),
    "bgm": ("bgm", "sfx"),
    "sfx": ("sfx",),
}
TRACK_TYPE_OF_ROLE = {
    "a_roll": "video", "b_roll": "video", "overlay_card": "video", "photo": "video",
    "text": "text", "sticker": "sticker", "effect": "effect", "audio": "audio",
}


def _uid() -> str:
    return str(uuid.uuid4()).upper()


def _asset_ref(path: str) -> str:
    """素材 → 脱敏引用（报告里只出现这个，绝不写文件路径/文件名）。"""
    return "as" + hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:10]


# 满幅阀值：显示面积占画布 ≥85% 且不透明，就算「满幅覆层」——它盖住下层，同源就是重影。
FULL_FRAME_COVER = 0.85
# 静态图片扩展名：静图在素材池里的「时长」是无限的（循环显示），探不到 duration 不能当废素材丢掉。
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".heic")


# 字体回落候选：模版不带商用字体文件（版权也不允许带），本机按名字也找不到时才回到系统字体。
# 不回落的话 PIL 会退到 10px 位图字体，字号样式全白写，字会小到看不清。
# 这些是**系统字体目录**，不含任何用户目录/应用私有目录；Windows/Linux 也列了常见路径。
FONT_CANDIDATES = (
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Songti.ttc",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
)


def resolve_font_path() -> Optional[str]:
    """系统 CJK 字体：环境变量 SVS_REPLICA_FONT 优先，否则按固定候选表探活。

    只在「模版字体名在本机怎么都找不到」时用——这是 last resort，不是默认路径。
    """
    env = os.environ.get("SVS_REPLICA_FONT")
    if env and Path(env).is_file():
        return env
    for cand in FONT_CANDIDATES:
        if Path(cand).is_file():
            return cand
    return None


# ---------------------------------------------------------------- 字体按名字解析
#
# 原来什么：模版不带字体文件 → 24 个文字位全落一种系统黑体（Hiragino Sans GB）。观感与
#   原版的俊雅体/思源粗宋完全对不上，而这两个字体本机其实都存（剪映缓存 + ~/Library/Fonts）。
# “不可移植”这个结论是错的：**字体名可以提，文件可以按名在本机就近解析**。
#
# 改成什么：模版只记字体名/扩展名（`text_slots[].font`），套用时按名字逐级找：
#   ① 风格字体资产 assets/style_spectrum/fonts[/<style_id>]（common.find_source_font）
#   ② 系统字体目录（common._font_search_dirs，mac/Windows/Linux 均覆盖）
#   ③ 剪映缓存字体目录（effect/<id>/<hash>/<字体文件>；根由 _discover_root('cache') 发现）
#   ④ 都没有才回落（resolve_font_path）并在报告里逐位写 fallback。
# 全程只按**文件名**模糊匹配，不写死路径与应用名，Windows/Linux 的缓存布局不同也能跑。

FONT_EXTS = (".ttf", ".otf", ".ttc")
_RESOLVED_FONTS: Dict[Tuple[str, str], Optional[dict]] = {}
_DIR_INDEX_CACHE: Dict[str, Dict[str, str]] = {}


def _norm_font_name(name: Any) -> str:
    """字体名归一化：去空格/下划线/连字符/括号并转小写，用来做模糊比对。"""
    return re.sub(r"[\s_\-()（）\[\]]+", "", str(name or "")).casefold()


def _index_fonts_in(dirs: List[Path], *, recursive_one: bool = True) -> Dict[str, str]:
    """目录列表 → {归一化文件名: 绝对路径}（同名先到先得，扫描根不递归太深）。"""
    idx: Dict[str, str] = {}
    for d in dirs:
        try:
            if not Path(d).is_dir():
                continue
        except OSError:
            continue
        patterns = ("*", "*/*") if recursive_one else ("*",)
        for pat in patterns:
            try:
                cands = sorted(Path(d).glob(pat))
            except OSError:
                continue
            for p in cands:
                try:
                    if not p.is_file() or p.suffix.casefold() not in FONT_EXTS:
                        continue
                except OSError:
                    continue
                idx.setdefault(_norm_font_name(p.stem), str(p))
    return idx


def _style_font_dirs(style_id: Optional[str]) -> List[Path]:
    try:
        from common import style_font_dirs  # 与渲染器同一条资产口径
        return list(style_font_dirs(style_id))
    except Exception:
        return []


def _system_font_dirs() -> List[Path]:
    try:
        from common import _font_search_dirs
        return list(_font_search_dirs())
    except Exception:
        return []


def _cache_font_index() -> Dict[str, str]:
    """剪映缓存字体索引：effect/<id>/<hash>/<字体文件>。

    根目录不写死：走渲染器那条就绪的 `_discover_root("cache")`（环境变量 JY_CACHE_ROOT 优先，
    否则在 ~/Movies 下按结构自动发现）——只读复用，不重复实现一份发现逻辑。
    """
    key = "cache:effect"
    if key in _DIR_INDEX_CACHE:
        return _DIR_INDEX_CACHE[key]
    idx: Dict[str, str] = {}
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "dev_tools"))
        from replica_template import _replica_module
        root = Path(_replica_module()._discover_root("cache"))
    except Exception:
        root = None
    if root is not None:
        for eff_root in (root / "effect", root / "artistEffect"):
            try:
                if not eff_root.is_dir():
                    continue
                cands = sorted(eff_root.glob("*/*/*"))
            except OSError:
                continue
            for p in cands:
                try:
                    if not p.is_file() or p.suffix.casefold() not in FONT_EXTS:
                        continue
                except OSError:
                    continue
                idx.setdefault(_norm_font_name(p.stem), str(p))
    _DIR_INDEX_CACHE[key] = idx
    return idx


def _fuzzy_lookup(idx: Dict[str, str], name: str) -> Optional[str]:
    """精确优先，其次子串模糊（字体名常带字重/厂商前后缀）。"""
    n = _norm_font_name(name)
    if not n:
        return None
    if n in idx:
        return idx[n]
    best = None
    for key_, path in idx.items():
        if not key_:
            continue
        if n in key_ or (len(key_) >= 4 and key_ in n):
            # 取匹配长度最长的那个，避开「朝代体」误配到「雅体」这类短名泛滥
            if best is None or len(key_) > len(best[0]):
                best = (key_, path)
    return best[1] if best else None


def _via_of(path: str) -> str:
    """来源标签：落在随仓 assets 里算 assets，其余算 system。"""
    try:
        from common import ASSETS_DIR
        if Path(path).resolve().is_relative_to(Path(ASSETS_DIR).resolve()):
            return "assets"
    except Exception:
        pass
    return "system"


FONT_ALIAS_PATH = Path(__file__).resolve().parents[1] / "assets" / "fonts" / "font_aliases.json"
_FONT_ALIAS: Optional[dict] = None


def font_alias_table() -> dict:
    """随包字体别名表：剪映字体名（含扩展名）→ {kind, file, ...}。

    为什么要有这张表：24 套模版一共用到 **30 种**字体，其中 21 种只在剪映缓存里有、
    5 种有开源对应名（得意黑=Smiley Sans、思源宋体=Source Han Serif…），另 4 种本机没有。
    换到别的电脑（没有剪映、没有这些字体文件）时，光靠「扫本机目录」会 29/30 全部回落成
    系统字体——字号/字宽全变，模版就不成立了。所以把字体文件随包带一份，按剪映原名索引，
    这样「另一台机器上也能照原样排字」。
    """
    global _FONT_ALIAS
    if _FONT_ALIAS is None:
        try:
            _FONT_ALIAS = (json.loads(FONT_ALIAS_PATH.read_text(encoding="utf-8")) or {}).get("entries") or {}
        except Exception:
            _FONT_ALIAS = {}
    return _FONT_ALIAS


def _bundled_font(name: str) -> Optional[dict]:
    """查随包字体表。name 可带扩展名也可不带（模版里存的是「名+扩展名」，实际调用常见只给名）。"""
    tbl = font_alias_table()
    if not tbl:
        return None
    cands = [str(name or "")]
    cands += [k for k in tbl if Path(k).stem == str(name or "")]
    for k in cands:
        ent = tbl.get(k)
        if not ent or ent.get("kind") == "missing":
            continue
        rel = ent.get("file")
        if not rel:
            continue
        path = (Path(__file__).resolve().parents[1] / rel) if not Path(rel).is_absolute() else Path(rel)
        if path.is_file():
            return {"name": str(name), "path": str(path), "via": "bundled"}
    return None


def resolve_template_font(name: str, style_id: Optional[str] = None) -> Optional[dict]:
    """模版字体名 → 本机字体文件。返回 {name, path, via}；真找不到返回 None。

    via: bundled（随包字体表命中）/ assets（随仓风格字体）/ system（系统字体目录）/
         cache（剪辑软件缓存字体）。
    顺序：先查随包表（换电脑后仍然成立），再查本机风格资产 / 系统 / 缓存。
    结果带进程内缓存（同一字体名不会反复扫目录）。
    """
    norm = _norm_font_name(name)
    if not norm:
        return None
    ck = (str(style_id or ""), norm)
    if ck in _RESOLVED_FONTS:
        return _RESOLVED_FONTS[ck]
    found: Optional[dict] = _bundled_font(name)
    # ① 风格字体资产（assets/style_spectrum/fonts[/<style_id>]）
    try:
        from common import find_source_font
        p = find_source_font(name, style_id)
        if p:
            found = {"name": str(name), "path": str(p), "via": "assets"}
    except Exception:
        pass
    # ② 系统字体目录（common._font_search_dirs 的第一项是随仓的 assets/fonts，
    #    落在那儿的一律记成 assets，不把「自带资产」混算成「系统字体」）
    if found is None:
        p = _fuzzy_lookup(_index_fonts_in(_system_font_dirs()), name)
        if p:
            found = {"name": str(name), "path": p, "via": _via_of(p)}
    # ③ 剪映缓存字体目录
    if found is None:
        p = _fuzzy_lookup(_cache_font_index(), name)
        if p:
            found = {"name": str(name), "path": p, "via": "cache"}
    _RESOLVED_FONTS[ck] = found
    return found


# ---------------------------------------------------------------- 文字换行 / 容量

def _is_wide(ch: str) -> bool:
    """中文/全角标点算 1 个字宽，西文/数字算半个（按 0.5 字宽累加）。"""
    return ord(ch) >= 0x2E80


def _tokens(line: str) -> List[Tuple[str, bool]]:
    """断行单元：中文按字切，西文按词切（`performance` 不能被拆成两半）。

    返回 [(token, 前有没有空白)]——西文词之间要保留一个空格，否则拼回去就是
    `performancemarketing` 这种断词。
    """
    out: List[Tuple[str, bool]] = []
    buf = ""
    gap = False
    for ch in line:
        if ch.isspace():
            if buf:
                out.append((buf, gap))
                buf, gap = "", True
            else:
                gap = True
        elif _is_wide(ch):
            if buf:
                out.append((buf, gap))
                buf = ""
                gap = True
            out.append((ch, gap))
            gap = False
        else:
            buf += ch
    if buf:
        out.append((buf, gap))
    if out:
        out[0] = (out[0][0], False)  # 行首不挂空格
    return out


def _tok_w(tok: str) -> float:
    if not tok:
        return 0.0
    return 1.0 if _is_wide(tok[0]) else max(0.5, 0.5 * len(tok))


def _line_w(line: str) -> float:
    """一行占几个字宽：西文词间的空格也算半个字。"""
    total = 0.0
    for i, (tok, gap) in enumerate(_tokens(line)):
        if i and gap and not _is_wide(tok[0]):
            total += 0.5
        total += _tok_w(tok)
    return total


def layout_text(text: str, per_line: int, max_lines: int) -> dict:
    """先把文案按可用宽度**换行**，行数超上限才**截断**（并打上省略号）。

    原来什么：直接 `txt.split("\n")[:max_lines]` 再拼回单行、按字数硬截 → 新文案略长就整句后面
    被砍成「…」，读者看到的是一句读不通的半句话。

    改成什么：按版式容量逐行填充；能装下就一行不少，装不下才截末行加「…」。
    返回 {text, lines, lines_used, truncated, new_text_len, per_line, max_lines}。
    """
    per_line = max(1, int(per_line or 1))
    max_lines = max(1, int(max_lines or 1))
    raw = str(text or "")
    wrapped: List[str] = []
    for hard_line in raw.split("\n"):
        hard_line = hard_line.strip()
        if not hard_line:
            continue
        cur, cur_w = "", 0.0
        for i, (tok, gap) in enumerate(_tokens(hard_line)):
            sep = 0.5 if (cur and gap and not _is_wide(tok[0])) else 0.0
            w = _tok_w(tok) + sep
            if cur and cur_w + w > per_line + 1e-9:
                wrapped.append(cur)
                cur, cur_w = tok, _tok_w(tok)
            else:
                cur += (" " if sep else "") + tok
                cur_w += w
        if cur:
            wrapped.append(cur)
    truncated = len(wrapped) > max_lines
    if truncated:
        wrapped = wrapped[:max_lines]
        last = wrapped[-1] if wrapped else ""
        if per_line >= 2:
            # 末行留一个省略号的位置；单字宽的大字位（每行只放得下 1 个字）放不下“…”
            while last and _line_w(last) + 1.0 > per_line + 1e-9:
                last = last[:-1]
            wrapped[-1] = (last or "") + "…"
        else:
            wrapped[-1] = last
    return {
        "text": "\n".join(wrapped),
        "lines": wrapped,
        "lines_used": len(wrapped),
        "truncated": truncated,
        "new_text_len": len(raw.replace("\n", "").strip()),
        "per_line": per_line,
        "max_lines": max_lines,
    }


def _us(sec: float) -> int:
    return int(round(float(sec) * US))


def _hex(rgb: List[int] | Tuple[int, ...]) -> str:
    r, g, b = (max(0, min(255, int(round(v)))) for v in tuple(rgb)[:3])
    return f"#{r:02X}{g:02X}{b:02X}"


def _rgb01(rgb: List[int] | Tuple[int, ...]) -> List[float]:
    return [round(max(0, min(255, float(v))) / 255.0, 6) for v in tuple(rgb)[:3]]


def probe_media(path: Path) -> dict:
    """素材探针：时长 + 像素尺寸（失败给保守默认值，并在报告里留痕）。

    宽高取**显示尺寸**：手机横拍存成 1920x1080 + rotation=-90 的文件，ffmpeg 解码时
    会自动转成 1080x1920，而 ffprobe 不转。不跟 rotation 换过来的话，草稿里声明的
    宽高与解码帧不一致，uv 裁切会越界（crop=1920:966 在 1080 宽的帧上直接报错）。
    """
    info = {"duration": 0.0, "width": 0, "height": 0, "rotation": 0}
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries",
             "stream=width,height:stream_side_data=rotation:stream_tags=rotate:format=duration",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=60)
        data = json.loads(proc.stdout or "{}")
        st = (data.get("streams") or [{}])[0]
        w = int(st.get("width") or 0)
        h = int(st.get("height") or 0)
        rot = 0
        for sd in st.get("side_data_list") or []:
            if sd.get("rotation") not in (None, ""):
                try:
                    rot = int(round(float(sd["rotation"])))
                except (TypeError, ValueError):
                    rot = 0
        if not rot:
            try:
                rot = int(round(float((st.get("tags") or {}).get("rotate") or 0)))
            except (TypeError, ValueError):
                rot = 0
        if abs(rot) % 180 == 90:
            w, h = h, w
        info["width"], info["height"], info["rotation"] = w, h, rot
        info["duration"] = float((data.get("format") or {}).get("duration") or 0.0)
    except Exception:
        pass
    if info["duration"] <= 0:
        try:
            proc = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                   "-of", "csv=p=0", str(path)], capture_output=True, text=True, timeout=60)
            info["duration"] = max(0.0, float((proc.stdout or "").strip() or 0.0))
        except Exception:
            info["duration"] = 0.0
    return info


# ---------------------------------------------------------------- 时间轴规划


class _Pool:
    """一个角色的素材池：轮转取用 + 每条素材自己的读头（cursor）。"""

    def __init__(self, role: str, paths: List[Path], report: dict):
        self.role = role
        self.items: List[dict] = []
        for p in paths:
            if not Path(p).is_file():
                report.setdefault("missing_assets", []).append(Path(p).name)
                continue
            is_image = Path(p).suffix.casefold() in IMAGE_EXTS
            info = probe_media(Path(p))
            if is_image:
                # 静图没有「时长」概念：当成无限长底片（fill() 只会取 want 那么长）。
                # 探不到 duration 的图片不能进 unprobeable 被丢掉——那正是照片拼贴层的素材。
                info = {"duration": 3600.0, **{k: v for k, v in info.items() if k != "duration"}}
            if info["duration"] <= 0.05:
                report.setdefault("unprobeable_assets", []).append(Path(p).name)
                continue
            self.items.append({"path": str(Path(p)), "dur": info["duration"],
                               "w": info["width"], "h": info["height"],
                               "rotation": int(info.get("rotation") or 0),
                               "is_image": bool(is_image),
                               "cursor": 0.0, "uses": 0})
        self.rot = 0

    def _take(self, item: dict, want: float, min_take: float) -> dict:
        """从一条素材上切一段（推进它的读头）。`fill()` 与 `fill_path()` 共用。"""
        avail = item["dur"] - item["cursor"]
        wrapped = False
        if avail < min(want, min_take):
            item["cursor"] = 0.0
            avail = item["dur"]
            wrapped = True
        take = max(0.05, min(want, avail))
        src_in = item["cursor"]
        item["cursor"] = min(item["dur"], src_in + take)
        reused = item["uses"] > 0
        item["uses"] += 1
        return {"path": item["path"], "src_in": src_in, "take": take,
                "reused": reused, "wrapped": wrapped, "conflict_skipped": 0,
                "w": item["w"], "h": item["h"],
                "is_image": bool(item.get("is_image")),
                "asset_dur": float(item["dur"]),
                "rotation": int(item.get("rotation") or 0)}

    def fill(self, want: float, min_take: float = 0.2,
             avoid: Optional[set] = None) -> Optional[dict]:
        """取一段：返回 {path, src_in, take, reused, wrapped}；池空返回 None。

        `avoid` 是**硬约束**：这些素材文件不许再取（用于「时间上重叠的叠层绝不同源」和
        「同一张图全片只用一次」）。整池都在 avoid 里就返回 None——宁可让该段留空并计数，
        也不拿同源素材叠出重影。
        """
        if not self.items:
            return None
        n = len(self.items)
        item = None
        skipped_conflict = 0
        for step in range(n):
            cand = self.items[(self.rot + step) % n]
            if avoid and cand["path"] in avoid:
                skipped_conflict += 1
                continue
            item = cand
            self.rot = (self.rot + step + 1) % n
            break
        if item is None:
            return None
        fill = self._take(item, want, min_take)
        fill["conflict_skipped"] = skipped_conflict
        return fill

    def peek(self, avoid: Optional[set] = None) -> Optional[dict]:
        """看一眼「下一次 fill 会取到哪一条」但**不推进**读头（只用于记账）。

        用途：照片层全局去重时回答「这次本来会分到的那张图，是不是已经在时间线别处用过」，
        从而把「挡住了一次重复分配」计成 `duplicate_photo_blocked`。
        """
        n = len(self.items)
        for step in range(n):
            cand = self.items[(self.rot + step) % n]
            if avoid and cand["path"] in avoid:
                continue
            return cand
        return None

    def fill_path(self, path: str, want: float, min_take: float = 0.2) -> Optional[dict]:
        """取**指定的**那一条（照片层「原版这两层是同一张图」→ 复用同一张新图走这里）。"""
        for item in self.items:
            if item["path"] == path:
                return self._take(item, want, min_take)
        return None


def _anchor_map_builder(old_b: List[Tuple[float, float]], new_b: List[Tuple[float, float]], new_total: float):
    def map_start(t: float) -> float:
        for (o0, o1), (n0, n1) in zip(old_b, new_b):
            if t < o1 - 1e-6:
                if o1 - o0 <= 1e-6:
                    return n0
                frac = max(0.0, (t - o0) / (o1 - o0))
                return n0 + frac * (n1 - n0)
        if not old_b:
            return t
        o0, o1 = old_b[-1]
        n0, n1 = new_b[-1]
        rate = (n1 - n0) / max(1e-6, o1 - o0)
        return min(new_total, n1 + (t - o1) * rate)

    def scale_at(t: float) -> float:
        for (o0, o1), (n0, n1) in zip(old_b, new_b):
            if t < o1 - 1e-6:
                return (n1 - n0) / max(1e-6, o1 - o0)
        if not old_b:
            return 1.0
        o0, o1 = old_b[-1]
        n0, n1 = new_b[-1]
        return (n1 - n0) / max(1e-6, o1 - o0)

    return map_start, scale_at


def _text_style_material(text: str, style: dict, font_path: Optional[str] = None) -> dict:
    """按模版文字样式造一条**真文字**素材（字号/颜色/描边/阴影/底框），文案是新的。

    `font_path` 是套用端按**字体名**在本机解析出的实际文件（见 resolve_template_font）；
    解析不到才给 None（此时字体名留空，渲染端会退到系统字体并留痕）。
    """
    size = float(style.get("font_size_units") or 15.0)
    color = style.get("color") or [255, 255, 255]
    lmw = style.get("line_max_width")
    line_max_width = float(lmw) if (lmw and 0.0 < float(lmw) <= 1.0) else 0.82
    st0: Dict[str, Any] = {
        "fill": {"content": {"solid": {"color": _rgb01(color)}}},
        "font": {"path": font_path or "", "id": ""},
        "size": size,
        "useLetterColor": True,
        "range": [0, len(text)],
    }
    stroke = style.get("stroke")
    if stroke and float(stroke.get("width_units") or 0) > 0:
        st0["strokes"] = [{
            "width": float(stroke["width_units"]),
            "content": {"solid": {"color": _rgb01(stroke.get("color") or [0, 0, 0])}},
        }]
    content = json.dumps({"text": text, "styles": [st0]}, ensure_ascii=False)
    shadow = style.get("shadow")
    bg = style.get("background")
    mat: Dict[str, Any] = {
        "add_type": 0,
        "alignment": style.get("alignment"),
        "background_alpha": float(bg["alpha"]) if bg else 0.0,
        "background_color": (_hex(bg.get("rgb") or [0, 0, 0]) if bg else ""),
        "background_height": 0.0,
        "background_horizontal_offset": 0.0,
        "background_round_radius": 0.0,
        "background_style": int(bg["style"]) if bg else 0,
        "background_vertical_offset": 0.0,
        "background_width": 0.0,
        "base_content": "",
        "beats": [],
        "bold": bool(style.get("bold")),
        "border_alpha": 1.0 if stroke else 0.0,
        "border_color": _hex((stroke or {}).get("color") or [0, 0, 0]),
        "border_width": float((stroke or {}).get("width_units") or 0.0),
        "caption_template_info": {"path": "", "resource_id": ""},
        "check_flag": 39,
        "combo_info": {},
        "content": content,
        "current_words": {},
        "fixed_height": -1,
        "fixed_width": -1,
        "font_category_id": "",
        "font_category_name": "",
        "font_id": "",
        "font_name": Path(font_path).stem if font_path else "",
        "font_path": font_path or "",
        "font_resource_id": "",
        "font_size": size,
        "font_source_platform": "",
        "font_team_id": "",
        "font_title": "",
        "font_url": "",
        "fonts": ([{"category_id": "system", "category_name": "系统字体", "effect_id": "",
                    "id": _uid(), "path": font_path, "resource_id": "", "title": Path(font_path).stem}]
                  if font_path else []),
        "force_apply_line_max_width": False,
        "group_id": "",
        "has_shadow": bool(shadow),
        "id": _uid(),
        "initial_scale": 1.0,
        "inner_padding": -1.0,
        "is_rich_text": True,
        "italic_degree": 0,
        "language": "zh",
        "layer_weight": 1,
        "letter_spacing": 0.0,
        "line_feed": 1,
        "line_max_width": line_max_width,
        "line_spacing": float(style.get("line_spacing") or 0.0),
        "lyrics_template": {"path": "", "resource_id": ""},
        "name": "",
        "original_size": [],
        "preset_category": "",
        "preset_category_id": "",
        "preset_has_set_alignment": False,
        "preset_id": "",
        "preset_index": 0,
        "preset_name": "",
        "recognize_type": 0,
        "relevance_segment": [],
        "shadow_alpha": float(shadow["alpha"]) if shadow else 0.0,
        "shadow_angle": -45,
        "shadow_color": _hex((shadow or {}).get("color") or [0, 0, 0]),
        "shadow_distance": float((shadow or {}).get("distance") or 0.0),
        "shadow_point": {"x": float((shadow or {}).get("point", [0.0, 0.0])[0]),
                         "y": float((shadow or {}).get("point", [0.0, 0.0])[1])},
        "shadow_smoothing": float((shadow or {}).get("smoothing") or 0.0),
        "shape_clip_x": False,
        "shape_clip_y": False,
        "style_name": "",
        "sub_type": 0,
        "text_alpha": 1.0,
        "text_color": _hex(color),
        "text_effect_style": "",
        "text_effect_strength": 0.5,
        "text_size": size,
        "text_to_audio_ids": [],
        "typesetting": 0,
        "underline": False,
        "underline_offset": 0.22,
        "underline_width": 0.05,
        "type": "subtitle",
        "use_effect_default_color": True,
        "words": {},
    }
    ga = style.get("global_alpha")
    if ga is not None:
        mat["global_alpha"] = float(ga)
    return mat


def _segment_has_blur(seg: dict) -> bool:
    """模版这一层原版是否**真带模糊**（`模糊` 特效里有 `effects_adjust_blur` 且值 > 0）。

    D5 的判定依据：verify 给背景板的豁免是「同源 + 同入点 + 实测 sigma≥8」，所以套用端
    只该把**真有模糊特效**的视频层当「模糊背景板」重建；没模糊证据的层（尤其是照片竖条）
    当普通层走自己的素材池——否则丢蒙版 + 不加模糊 = 一张全屏同源副本。
    """
    for fx in seg.get("effects") or []:
        if "模糊" not in str(fx.get("name") or ""):
            continue
        for p in fx.get("adjust_params") or []:
            if str(p.get("name")) != "effects_adjust_blur":
                continue
            try:
                val = float(p.get("value") if p.get("value") is not None
                            else (p.get("default_value") or 0.0))
            except (TypeError, ValueError):
                val = 0.0
            if val > 0:
                return True
    return False


def _matting_block(meta: Optional[dict], root_uuid: str) -> dict:
    """模版里的「人像抠像 + 撕纸描边」结构 → 新草稿素材的 `matting` 字段。

    原来（重建前）：套用端把 `matting.path` 非空的叠加层一律跳过（`matting_layer_not_reconstructible`），
    成片只剩主轨 + 文字，原版的撕纸抠像边与人像压背景全丢。
    改成：把抠像层按 `same_material_as` 强绑回主轨同素材同入点，并把抠像结构写进新草稿的素材，
    由渲染器（`render_replica_from_draft`，只读调用）在新素材上真跑 Vision 抠像 + 撕纸描边，
    再用 alphamerge 合成。

    **为何选「草稿标 meta、渲染时执行」而不是套用阶段烘 RGBA 层**：
      ① 描边像素换算常数（`STROKE_SIZE_GAIN`/`STROKE_FEATHER_SRC`/`STROKE_TEX_*`/`STROKE_MATTE_DILATE`）
         只在渲染器里，且是逐帧从原版反解出来的；套用端复制一份必然漂成第二个真值源。
      ② 烘好的 RGBA 层若当普通素材引用，渲染器抽段走 h264（`.mp4`）会丢 alpha——alpha 只从
         `matting`/遮罩派生，等于白烘（要另改渲染器才能接）。
      ③ matte 必须与成片用**同一份抽段文件、同一 fps**生成，否则逐帧错位、人物边缘鬼影。
    `matting.path` 写成草稿内相对占位（与原版草稿同一形状）：本复刻链路由渲染器在渲染时生成
    matte；编辑器打开时按需重算抠像。
    """
    meta = meta or {}
    mt: Dict[str, Any] = {
        "custom_matting_id": _uid(),
        "path": f"##_draftpath_placeholder_{root_uuid}_##/matting/person_cutout",
        "flag": 3,
        "has_use_quick_brush": bool(meta.get("quick_brush")),
    }
    if meta.get("stroke"):
        params = dict(meta.get("stroke_params") or {})
        if not params:
            # 模版是旧格式（只有 stroke=True，没记强度参数）：按原版反解值补，并如实留痕
            params = {"effects_adjust_size": 0.35, "effects_adjust_distance": 0.25,
                      "effects_adjust_texture": 0.6}
        col = meta.get("stroke_color") or [1.0, 1.0, 1.0, 1.0]
        mt["enable_matting_stroke"] = True
        mt["strokes"] = [{
            "resource_name": meta.get("stroke_name") or "",
            "color": list(col),
            "adjust_params": [{"name": k, "value": float(v), "default_value": float(v)}
                              for k, v in params.items()],
            "path": "",
            "resource_id": "",
        }]
    return mt


def _video_material(path_name: str, root_uuid: str, rel_dir: str, w: int, h: int, dur: float,
                    crop: Optional[dict] = None, *, is_photo: bool = False,
                    matting: Optional[dict] = None) -> dict:
    crop_dict = {}
    if crop:
        crop_dict = {
            "lower_left_x": crop["x0"], "lower_left_y": crop["y1"],
            "lower_right_x": crop["x1"], "lower_right_y": crop["y1"],
            "upper_left_x": crop["x0"], "upper_left_y": crop["y0"],
            "upper_right_x": crop["x1"], "upper_right_y": crop["y0"],
        }
    return {
        "beauty_face_auto_preset": {},
        "category_name": "local",
        "check_flag": 62978047,
        "crop": crop_dict,
        "crop_ratio": "free",
        "crop_scale": 1.0,
        "duration": _us(dur),
        "height": int(h or CANVAS_H),
        "id": _uid(),
        "local_material_id": _uid(),
        "material_id": "",
        "material_name": path_name,
        "matting": matting or {"path": ""},
        "path": f"##_draftpath_placeholder_{root_uuid}_##/{rel_dir}/{path_name}",
        "stable": {"time_range": {}},
        "type": "photo" if is_photo else "video",
        "video_algorithm": {"path": "", "story_video_modify_video_config": {}},
        "width": int(w or CANVAS_W),
    }


def _audio_material(path_name: str, root_uuid: str, rel_dir: str, dur: float, category: str) -> dict:
    return {
        "app_id": 0,
        "category_id": category,
        "category_name": category,
        "check_flag": 1,
        "duration": _us(dur),
        "effect_id": "",
        "id": _uid(),
        "intensifies_path": "",
        "local_material_id": _uid(),
        "music_id": _uid(),
        "name": Path(path_name).stem,
        "path": f"##_draftpath_placeholder_{root_uuid}_##/{rel_dir}/{path_name}",
        "request_id": "",
        "resource_id": "",
        "source_platform": 0,
        "team_id": "",
        "type": "extract_music" if category == "music" else "sound",
    }


def _speed_material() -> dict:
    return {"curve_speed": None, "id": _uid(), "mode": 0, "speed": 1.0, "type": "speed"}


def _compound_placeholder_material(name: str, root_uuid: str, w: int, h: int, dur: float,
                                   *, audio: bool = False) -> dict:
    """复合片段在父时间线上的**占位素材**：path 空，真身在 materials.drafts。

    必须是「有 id、但没有 path」的素材——渲染器就是靠 `not mat.path` + 
    `extra_material_refs` 里的 drafts 条目来识别复合片段的（与剪映草稿同一形状）。
    """
    if audio:
        mat = _audio_material(name, root_uuid, "materials/audio", float(dur), "sound")
        mat["path"] = ""      # 真身是子时间线，不是文件
        mat["type"] = "music"
        mat["name"] = name
        return mat
    mat = _video_material(name, root_uuid, "materials/video", w, h, float(dur))
    mat["path"] = ""          # 真身是子时间线，不是文件
    mat["material_name"] = name
    mat["matting"] = {"path": ""}
    return mat


# uv 裁切能不能掁过来：只看「裁切框宽高比」与「新素材宽高比」是否一致（±3%）
CROP_ASPECT_TOLERANCE = 0.0296  # ln(1.03)


def usable_crop(seg: dict, fill: dict, canvas_w: float, canvas_h: float,
                rep: Optional[dict] = None) -> Optional[dict]:
    """模版带过来的 uv 裁切（源素材的预裁框）能不能直接套到新素材上。

    原来什么：无脑把 `geometry.crop`（归一化 uv）写进新素材的 material.crop。
    问题：uv 是对**源素材那块画面的宽高比**成立的。新素材换成 16:9 横拍后，同一组 uv
    会裁出完全不同的取景；素材带旋转元数据（1920x1080 + rotation=-90）时更糟——
    渲染器按 ffprobe 的未旋转宽高算 crop，实际解码帧是 1080x1920，
    `crop=1920:966` 在 1080 宽的帧上直接报 Invalid size，整条渲染挂掉。

    改成什么：先用模版的 `geometry.size_ratio` 反推出原裁切框的宽高比
        （size_ratio 是 contain 后的显示比例，cb_aspect = sr_w*W /(sr_h*H)），
    与新素材宽高比比对，偏差在 3% 以内才搬；否则丢掉裁切并计数（crop_uv_dropped），
    让图层按整幅 contain —— 宁可少裁一刀，也不能裁错地方或把渲染弄崩。
    带 90/270 旋转的素材一律不搬 uv 裁切（渲染器那条裁切算式不看旋转）。
    """
    geom = seg.get("geometry") or {}
    crop = geom.get("crop")
    if not crop:
        return None
    w, h = float(fill.get("w") or 0), float(fill.get("h") or 0)
    rotated = abs(int(fill.get("rotation") or 0)) % 180 == 90
    sr = geom.get("size_ratio")
    want = None
    if sr and len(sr) == 2 and float(sr[1] or 0) > 0:
        want = (float(sr[0]) * canvas_w) / max(1e-6, float(sr[1]) * canvas_h)
    if not rotated and want and w > 0 and h > 0:
        got = w / max(1e-6, h)
        if abs(math.log(max(1e-6, got) / max(1e-6, want))) <= CROP_ASPECT_TOLERANCE:
            return crop
        reason = "aspect_mismatch"
    elif rotated:
        reason = "rotation_metadata"
    else:
        # 判不了（模版没记 size_ratio 或素材尺寸缺失）→ 照搬，模版已标 crop_uv_inherited_from_source_material
        return crop
    if rep is not None:
        rep["crop_uv_dropped"] = rep.get("crop_uv_dropped", 0) + 1
        rep.setdefault("crop_uv_dropped_detail", []).append({
            "key": seg.get("key"), "reason": reason,
            "template_crop_box_aspect": round(want, 3) if want else None,
            "asset_aspect": round(w / max(1e-6, h), 3),
        })
    return None


def _aspect_crop(fill: dict, want_aspect: Optional[float]) -> Optional[dict]:
    """把新图片裁成模版图层的**展示宽高比**（居中）。

    为什么要这一步：模版里的 `geometry.size_ratio` 是那一层**设计好的显示矩形**（例如
    0.45~1.667s 拼贴底板每张是 1242×344 = 3.6:1 的长条）。如果直接把 3:4 的竖图放进
    scale 0.8 的 contain 里，方屏上会是五个大竖图叠在一起——不是「照片条拼贴」了。
    按模板宽高比居中裁一刀，新图仍然是真的照片内容（未拉伸、未变形），只是保留了
    原版的**长条取景**；同时在报告里计 photo_layer_aspect_cropped。
    """
    w, h = float(fill.get("w") or 0), float(fill.get("h") or 0)
    if w <= 0 or h <= 0 or not want_aspect or want_aspect <= 0:
        return None
    got = w / h
    if abs(math.log(max(1e-6, got) / max(1e-6, want_aspect))) <= CROP_ASPECT_TOLERANCE:
        return None
    if got > want_aspect:      # 太宽 → 收窄宽度
        new_w = h * want_aspect
        x0 = (w - new_w) / (2.0 * w)
        return {"x0": round(x0, 4), "y0": 0.0, "x1": round(1.0 - x0, 4), "y1": 1.0}
    new_h = w / want_aspect    # 太高 → 收窄高度
    y0 = (h - new_h) / (2.0 * h)
    return {"x0": 0.0, "y0": round(y0, 4), "x1": 1.0, "y1": round(1.0 - y0, 4)}


def _anim_holder(animations: List[dict], seg_dur: float, report: dict) -> Tuple[dict, List[dict]]:
    """段内比例动画 → material_animations 素材（时长/起点按新段长还原，溢出钳住）。"""
    items = []
    for an in animations:
        start = float(an.get("start_ratio") or 0.0) * seg_dur
        dur = float(an.get("duration_ratio") or 0.0) * seg_dur
        if dur <= 1e-3:
            continue
        if start + dur > seg_dur + 1e-4:
            new_dur = max(0.05, seg_dur - start)
            if new_dur < dur - 1e-3:
                report["anim_clamped"] = report.get("anim_clamped", 0) + 1
            dur = new_dur
            if start >= seg_dur - 1e-3:
                start = max(0.0, seg_dur - dur)
        item = {"category_id": "", "category_name": "", "duration": _us(dur), "id": _uid(),
                "material_type": "video", "name": an.get("name"), "panel": "video",
                "path": "", "resource_id": "", "third_resource_id": "0", "type": an.get("kind")}
        if start > 1e-4 and an.get("kind") == "out":
            item["start"] = _us(start)
        items.append(item)
    if not items:
        return {}, []
    return {"animations": items, "id": _uid(), "type": "sticker_animation"}, items


def _keyframes_of(seg: dict, seg_dur: float, src_in: float) -> List[dict]:
    """段内比例关键帧 → 草稿口径（time_offset 是源时间/容器 PTS 绝对值）。"""
    out = []
    for kf in seg.get("keyframes") or []:
        pts = []
        for p in kf.get("points") or []:
            t = float(p.get("t") or 0.0) * seg_dur
            pts.append({
                "id": _uid(),
                "left_control": {"x": 0.0, "y": 0.0},
                "right_control": {"x": 0.0, "y": 0.0},
                "time_offset": _us(max(0.0, src_in + t)),
                "values": [float(p.get("v"))],
            })
        if pts:
            out.append({"id": _uid(), "keyframe_list": pts, "material_id": "",
                        "property_type": kf.get("property")})
    return out


def _segment(*, mat_id: str, tl_start: float, dur: float, src_in: float, src_dur: float,
             geometry: dict, keyframes: List[dict], extra_refs: List[str],
             track_index: int, visible: bool = True, volume: float = 1.0,
             render_index: Optional[float] = None) -> dict:
    scale = geometry.get("scale") or [1.0, 1.0]
    transform = geometry.get("transform") or [0.0, 0.0]
    return {
        "cartoon": False,
        "clip": {
            "alpha": float(geometry["alpha"]) if geometry.get("alpha") is not None else 1.0,
            "flip": geometry.get("flip") or {},
            "rotation": float(geometry.get("rotation") or 0.0),
            "scale": {"x": float(scale[0]), "y": float(scale[1])},
            "transform": {"x": float(transform[0]), "y": float(transform[1])},
        },
        "common_keyframes": keyframes,
        "enable_adjust": False,
        "enable_adjust_mask": False,
        "enable_color_adjust": False,
        "enable_hsl": False,
        "extra_material_refs": list(extra_refs),
        "group_id": "",
        "hdr_settings": {"intensity": 1.0, "mode": 1, "nits": 1000},
        "id": _uid(),
        "intensifies_audio": False,
        "is_placeholder": False,
        "is_tone_modify": False,
        "keyframe_refs": [],
        "last_nonzero_volume": float(volume),
        "material_id": mat_id,
        # 原来什么：`"render_index": int(track_index)` —— 把**轨数组序**写成了 z 序。剪映草稿里这是
        # 两个字段（`track_render_index` 才是数组序），原版同一时刻拼贴轨数组序=2 而 render_index=3
        # （在上）。套用端这样写，拼贴就被同源缩图层盖住了。
        # 改成什么：段上写**原版 render_index**（模版已按原版语义记好）；原版本来就缺省的那种
        # 段（主轨 / 音频轨）退回到模版轨序号——它们在剪映里本来就没有 z 值，写什么都只看轨序，
        # 这里不自作主张给它们编一个 z 层。
        "render_index": int(render_index) if render_index is not None else int(track_index),
        "render_timerange": {},
        "responsive_layout": {"enable": False, "horizontal_pos_layout": 0,
                              "target_follow": False, "vertical_pos_layout": 0},
        "reverse": False,
        "source": "segmentsourcenormal",
        "source_timerange": {"duration": _us(src_dur), "start": _us(src_in)},
        "speed": 1.0,
        "target_timerange": {"duration": _us(dur), "start": _us(tl_start)},
        "template_id": "",
        "template_scene": "default",
        "track_attribute": 0,
        "track_render_index": int(track_index),
        "uniform_scale": {"on": True, "value": 1.0},
        "visible": bool(visible),
        "volume": float(volume),
    }


# ---------------------------------------------------------------- z 序（轨序即 z 序）
# 本项目渲染器是「**轨序即 z 序**」（render_replica_from_draft 里 units.sort 用轨下标，数组越靠后
# 越在上层）。而模版来自剪映草稿，它的 z 序是**段上的 render_index**（越大越上）。两者方向一致，
# 所以迁移 z 序 = **按 render_index 升序写出 tracks**，而不是把 render_index 当轨下标用。
#
# 为什么这是真因（不再重挖）：原版 0.45~1.667s 的拼贴父段 render_index=3、同源缩图层 render_index=2，
# 而它们在数组里是 2 号轨/6 号轨；旧实现按数组序写，拼贴落到缩图层**下面**，条带被整块盖住。

AUDIO_TRACK_TYPES = ("audio",)


def _track_z_key(track: dict) -> float:
    """轨的 z 序键 = 原版段级 `render_index` 的聚合值（模版轨级 render_index）。

    一条轨里段的 render_index 可能不统一（原版这块拼贴轨：0.45~1.667s 是 3、5.88~8.22s 是 14），
    取 **max**：让这条轨站在它曾到达的最高层。取 min 会让它在 5.88~8.22s 反被 render_index=13
    的那条轨盖住（原版那一段拼贴在**上**）——用 max 才与所有重叠窗口的先后关系一致。
    缺字段（v1 老模版）退回轨数组序：行为与旧版一模一样，宁可不变也不凭猜测重排。
    """
    ri = track.get("render_index")
    if isinstance(ri, (int, float)):
        return float(ri)
    return float(track.get("index") or 0)


def _z_sort_tracks(tracks: List[dict]) -> List[dict]:
    """按 render_index 升序排轨（同值保持原有相对顺序 = 稳定排序）；音频轨留到最后。

    音频轨不参入排序：音频没有画面 z 序（原版音频轨段的 render_index 就是缺省），渲染器也是
    另起一路做混音、不看轨序；原版自己也把音频轨放在数组末尾。文字/贴纸/特效轨**参入**排序：
    原版 render_index 三就分档——文字 14000+ / 特效 11000 / 贴纸 14008，必须排在视频层（1~15）之上。
    """
    visual = [t for t in tracks if t.get("track_type") not in AUDIO_TRACK_TYPES
              and t.get("role") != "audio"]
    vis_ids = {id(t) for t in visual}
    audio = [t for t in tracks if id(t) not in vis_ids]
    visual.sort(key=lambda t: (_track_z_key(t), int(t.get("index") or 0)))
    return visual + audio


def _z_order_stats(scope: str, all_tracks: List[dict], emitted: List[dict],
                   segments: List[dict]) -> dict:
    """一层时间线排完 z 序后的对账（顶层与每个复合片段子稿各一份）。

    `all_tracks` = 模版里的**原数组序**（含被跳过没写出去的 effect/空轨），`emitted` = 真正
    写进草稿的轨（已按 render_index 排好）。`before` 只取 emitted 里的那些、仍按原数组序，
    这样「动了多少」只算真写出去的轨，不会被跳过的 effect 轨干扰。

    三个量各管一事，别混着当同一个看：
      · `tracks_reordered`：**位置变了**的轨数（= 这次重排的幅度）；
      · `z_order_mismatch`：排序后仍有残留错位的**轨数**（涉及反转对的轨去重），正确产物 = 0；
      · `z_order_inverted_pairs`：时间重叠的两层里，轨序先后与原版 render_index 先后**相反**的
        对数，正确产物 = 0。
    判反转只看两边都记了 render_index 的段（缺值无法比较，不瞎判）。
    """
    emitted_ids = {id(t) for t in emitted}
    before_list = [t for t in all_tracks if id(t) in emitted_ids]
    order_before = [int(t.get("index") or 0) for t in before_list]
    order_after = [int(t.get("index") or 0) for t in emitted]
    moved = sum(1 for pos, idx in enumerate(order_after) if order_before[pos] != idx)
    pos_of = {idx: pos for pos, idx in enumerate(order_after)}
    segs = [{"track": int(s.get("track_index") or 0),
             "key": s.get("key"),
             "ri": float(s["render_index"]),
             "a": float(s.get("start_sec") or 0.0),
             "b": float(s.get("start_sec") or 0.0) + float(s.get("duration_sec") or 0.0)}
            for s in segments
            if s.get("render_index") is not None and int(s.get("track_index") or 0) in pos_of]
    inverted: List[dict] = []
    n_inv = 0
    bad_tracks: set = set()
    for i, x in enumerate(segs):
        for y in segs[i + 1:]:
            if x["track"] == y["track"]:
                continue
            ov = min(x["b"], y["b"]) - max(x["a"], y["a"])
            if ov <= 0.02:
                continue
            by_z = x["ri"] - y["ri"]
            by_out = pos_of[x["track"]] - pos_of[y["track"]]
            if by_z * by_out < 0:
                n_inv += 1
                bad_tracks.update((x["track"], y["track"]))
                if len(inverted) < 8:
                    inverted.append({"overlap": [round(max(x["a"], y["a"]), 3),
                                                 round(min(x["b"], y["b"]), 3)],
                                     "a": x["key"], "b": y["key"],
                                     "render_index": [x["ri"], y["ri"]],
                                     "track_pos": [pos_of[x["track"]], pos_of[y["track"]]]})
    return {
        "scope": scope,
        "tracks_total": len(emitted),
        "tracks_reordered": moved,
        "z_order_mismatch": len(bad_tracks),
        "z_order_inverted_pairs": n_inv,
        "audio_tracks_kept_last": sum(1 for t in emitted
                                      if t.get("track_type") in AUDIO_TRACK_TYPES
                                      or t.get("role") == "audio"),
        "order_before": order_before,
        "order_after": order_after,
        "inverted_pairs": inverted,
        "basis": ("轨序即 z 序：按原版 render_index 升序写 tracks；渲染器/剪映两个口径都自洽"),
    }


def _z_apply_render_index(out_tracks: List[dict]) -> List[dict]:
    """排完序后回填 `track_render_index`，让草稿自洽（`track_render_index` = 轨数组序 = 输出位置）。

    `render_index` 不必改写：它已按**原版 z 序**写好（见 _segment），而输出轨序又是按它升序排的，
    所以「轨序 → 段上 render_index」本来就单调一致；这里只给真缺值（None）的段补一个位置，
    不做多余改写（改了反而会让剪映侧看到的层号与轨序不符）。
    """
    for pos, tr in enumerate(out_tracks):
        for seg in tr.get("segments") or []:
            seg["track_render_index"] = pos
            if seg.get("render_index") is None:
                seg["render_index"] = pos
    return out_tracks


# ---------------------------------------------------------------- 主入口


def apply_template(template: dict, assets: Dict[str, List[str]], texts: List[Any],
                   out_draft_path: Path | str, report: Optional[dict] = None,
                   *, style_id: Optional[str] = None) -> Path:
    """模版 + 新素材 + 新文案 → 原生草稿 `draft_content.json`（返回该文件路径）。"""
    rep: dict = report if report is not None else {}
    for key in ("skipped", "asset_fallback", "notes"):
        rep.setdefault(key, {} if key != "notes" else [])
    rep.setdefault("asset_reused", 0)
    rep.setdefault("asset_wrapped", 0)
    rep.setdefault("slot_shrunk", 0)
    rep.setdefault("anim_clamped", 0)
    rep.setdefault("texts_truncated", 0)
    rep.setdefault("text_reused", 0)
    # 叠层同源硬约束的账目（见「2b. 视频叠层」）：
    #   overlay_left_blank  因换不到「与重叠层不同源」的素材而留空的段数（含抠像层）
    #   ghost_rejected      因「再叠就会同源重影」而拒接的视频段数（overlay_left_blank 的子集）
    #   asset_reuse_by_slot 逐段：分到哪条素材（脱敏 ref）+ 角色 + 入点，不含路径
    rep.setdefault("overlay_left_blank", 0)
    rep.setdefault("overlay_left_blank_detail", [])
    rep.setdefault("ghost_rejected", 0)
    rep.setdefault("asset_reuse_by_slot", [])
    rep.setdefault("overlap_conflicts_avoided", 0)
    out_p = Path(out_draft_path)
    draft_dir = out_p.parent if out_p.suffix.lower() == ".json" else out_p
    draft_dir.mkdir(parents=True, exist_ok=True)
    for sub in ("materials/video", "materials/audio", "materials/sticker"):
        (draft_dir / sub).mkdir(parents=True, exist_ok=True)

    canvas = template.get("canvas") or {}
    canvas_w = int(canvas.get("width") or CANVAS_W)
    canvas_h = int(canvas.get("height") or CANVAS_H)
    fps = float(template.get("fps") or 30.0)
    total_old = float(template.get("duration_sec") or 0.0)
    root_uuid = _uid()
    # 模版不带字体文件（商用字体不能随模版走）：先按**字体名**在本机就近解析，
    # 真的一个都找不到才回落系统 CJK 字体（字号/样式照旧）。
    fallback_font = resolve_font_path()
    rep["font_fallback"] = Path(fallback_font).name if fallback_font else None
    if not fallback_font and template.get("text_slots"):
        rep["notes"].append("找不到系统 CJK 字体：文字会回落成位图字体，字号将不生效")

    segments: List[dict] = template.get("segments") or []
    tracks: List[dict] = template.get("tracks") or []
    text_slots: List[dict] = template.get("text_slots") or []

    # ---- 字体：模版字体名 → 本机文件（逐位解析，逐位记账）----
    slot_font: Dict[int, Optional[str]] = {}
    font_resolved: Dict[str, dict] = {}
    font_fallback_slots: List[dict] = []
    for si, slot in enumerate(text_slots):
        fname = str(((slot.get("font") or {}).get("name")) or "").strip()
        hit = resolve_template_font(fname, style_id) if fname else None
        if hit:
            slot_font[si] = hit["path"]
            entry = font_resolved.setdefault(fname, {"path": hit["path"], "via": hit["via"],
                                                      "slots": []})
            entry["slots"].append(si)
        else:
            slot_font[si] = fallback_font
            font_fallback_slots.append({"slot": si, "template_font": fname or None,
                                        "fallback": Path(fallback_font).name if fallback_font else None})
    rep["font_resolved"] = font_resolved
    rep["font_fallback_slots"] = font_fallback_slots
    driver_idx = next((t["index"] for t in tracks if t.get("is_driver")), None)
    if driver_idx is None:
        cand = [t for t in tracks if t.get("role") == "a_roll"]
        driver_idx = max(cand, key=lambda t: t.get("total_sec") or 0)["index"] if cand else None
    driver_segs = sorted((s for s in segments if s["track_index"] == driver_idx),
                         key=lambda s: s.get("start_sec") or 0.0)
    if not driver_segs:
        raise RuntimeError("模版没有驱动轨（a_roll）段，无法建立时间轴")

    # ---- 素材池 ----
    pools = {role: _Pool(role, [str(p) for p in paths], rep) for role, paths in (assets or {}).items()}
    for role in ROLE_FALLBACK:
        pools.setdefault(role, _Pool(role, [], rep))

    def take_for(role: str, want: float, avoid: Optional[set] = None) -> Optional[dict]:
        for cand_role in ROLE_FALLBACK.get(role, (role,)):
            pool = pools.get(cand_role)
            if pool and pool.items:
                fill = pool.fill(want, avoid=avoid)
                if fill is None:
                    continue
                fill["used_role"] = cand_role
                if cand_role != role:
                    key = f"{role}->{cand_role}"
                    rep["asset_fallback"][key] = rep["asset_fallback"].get(key, 0) + 1
                if fill["reused"]:
                    rep["asset_reused"] += 1
                if fill["wrapped"]:
                    rep["asset_wrapped"] += 1
                return fill
        return None

    # ---- 照片层的全局去重账本（跨复合片段、跨层级、跨时间窗）----
    #
    # 原来什么：照片层只用了 `_taken_near()`（“这段时间窗里已经被占住的图”）当硬约束。
    # 复合片段是按父段顺序逐个解析的，后面的片段在前一个**分完之后**才登记窗口，而且两个
    # 片段的窗口根本不需要重叠——于是 2.9s 那个 7 图拼贴跟 0.45s 那个 5 图拼贴分到了两张
    # 同一张图（实测 `绘画.png` / `汽车.jpg` 各出现两次，重影检查看不见：它们不同时上屏）。
    # 观众看到的是“同一张图又来了”，这就是真 bug。
    #
    # 改成什么：全片一本账。同一张**新图**在整条时间线上只能用一次；只有原版本来就是同一张图
    # （`material_fingerprint` 相同）才显式放行并计 `duplicate_photo_allowed`。池子真不够就
    # 留空（调用方计 `photo_layer_no_image`），绝不拿已经在别处出现过的图去填。
    photo_used: Dict[str, str] = {}    # 新图路径 → 第一次分给它的段 key
    photo_by_fp: Dict[str, str] = {}   # 原版素材指纹 → 分给它的新图路径
    photo_alloc: List[dict] = []       # 逐层台账（脱敏 ref + 指纹 ref），供报告对账
    rep.setdefault("duplicate_photo_blocked", 0)
    rep.setdefault("duplicate_photo_allowed", 0)
    rep.setdefault("duplicate_photo_blocked_detail", [])
    rep.setdefault("photo_layers_unfilled", 0)

    def take_unique_photo(want: float, overlap_avoid: Optional[set] = None, *,
                         fingerprint: Optional[str] = None,
                         key: str = "") -> Optional[dict]:
        """照片层取图：**全片唯一**（跨复合片段/跨层级/跨时间窗）。

        · 原版这两层是同一张图（`material_fingerprint` 相同）→ 复用同一张新图，
          计 `duplicate_photo_allowed`（这是原版设计，不是重复）；
        · 否则只从「没被别处用过」的图里取；本来轮到的那张若已经用过，计
          `duplicate_photo_blocked` 后改取下一张；整池都用过 → 返回 None（留空）。
        """
        pool = pools.get("photo")
        if pool is None or not pool.items:
            return None
        if fingerprint and fingerprint in photo_by_fp:
            fill = pool.fill_path(photo_by_fp[fingerprint], want)
            if fill is not None:
                fill["used_role"] = "photo"
                rep["asset_reused"] += 1
                rep["duplicate_photo_allowed"] += 1
                photo_alloc.append({"key": key, "asset_ref": _asset_ref(fill["path"]),
                                    "fingerprint_ref": fingerprint, "reused_original_same": True})
                return fill
        overlap = set(overlap_avoid or ())
        naive = pool.peek(avoid=overlap)
        if naive is not None and naive["path"] in photo_used:
            rep["duplicate_photo_blocked"] += 1
            rep["duplicate_photo_blocked_detail"].append({
                "key": key, "blocked_ref": _asset_ref(naive["path"]),
                "first_used_by": photo_used[naive["path"]]})
        fill = pool.fill(want, avoid=overlap | set(photo_used))
        if fill is None:
            rep["photo_layers_unfilled"] += 1
            return None
        fill["used_role"] = "photo"
        if fill["reused"]:
            rep["asset_reused"] += 1
        photo_used.setdefault(fill["path"], key or "?")
        if fingerprint:
            photo_by_fp.setdefault(fingerprint, fill["path"])
        photo_alloc.append({"key": key, "asset_ref": _asset_ref(fill["path"]),
                            "fingerprint_ref": fingerprint, "reused_original_same": False})
        return fill

    # 媒体落盘：同源文件只拷一次
    copied: Dict[str, tuple] = {}  # 源绝对路径 -> (文件名, 子目录)

    def stage(path: str, sub: str) -> str:
        src = Path(path)
        if path not in copied:
            name = src.name
            dst = draft_dir / "materials" / sub / name
            n = 1
            while dst.exists() and dst.stat().st_size != src.stat().st_size:
                name = f"{src.stem}_{n}{src.suffix}"
                dst = draft_dir / "materials" / sub / name
                n += 1
            if not dst.exists():
                shutil.copy2(src, dst)
            copied[path] = (name, f"materials/{sub}")
        return copied[path][0]

    # ---- 1. 驱动轨：段边界锚点重排 ----
    plan: Dict[str, dict] = {}
    old_b: List[Tuple[float, float]] = []
    new_b: List[Tuple[float, float]] = []
    cursor = 0.0
    for seg in driver_segs:
        want = float(seg.get("duration_sec") or 0.0)
        role = seg.get("role") or "a_roll"
        asset_role = seg.get("asset_role") or role
        if seg.get("kind") == "compound":
            # 驱动轨上的**复合片段**（内嵌子时间线，例如 style_15 的 16 段照片翻页块）：
            # 它占的是一段时间轴，内容是子时间线自己，**不是一条可以换掉的 B-roll**。
            # 原来这里照样 take_for(a_roll) 分一条新素材，等于把整块复合片段换成了普通视频，
            # 子层结构（照片拼贴/多图层/音效）连同 z 序一起丢。现在只推进时间槽位、
            # 记锚点，素材与子草稿交给下面 2b 的 compound 通道。
            s0 = float(seg.get("start_sec") or 0.0)
            old_b.append((s0, s0 + want))
            new_b.append((cursor, cursor + want))
            cursor += want
            continue
        if asset_role not in ("a_roll", "b_roll", "photo"):
            asset_role = "a_roll"
        if asset_role == "photo":
            # 驱动轨本身就是静图（照片翻页类模版）：同样走「全片唯一」这条账
            fill = take_unique_photo(want, fingerprint=seg.get("material_fingerprint"),
                                     key=seg["key"])
        else:
            fill = take_for(asset_role, want)
        if fill is None:
            rep["skipped"]["a_roll_no_asset"] = rep["skipped"].get("a_roll_no_asset", 0) + 1
            continue
        new_dur = min(want, fill["take"])
        if new_dur < want - 1e-3:
            rep["slot_shrunk"] += 1
        s0 = float(seg.get("start_sec") or 0.0)
        old_b.append((s0, s0 + want))
        new_b.append((cursor, cursor + new_dur))
        plan[seg["key"]] = {"kind": seg.get("kind"), "role": role, "start": cursor,
                            "dur": new_dur, "src_in": fill["src_in"],
                            "asset": fill, "reused_asset": fill["reused"],
                            "old_start": s0, "old_dur": want,
                            "track_index": seg["track_index"]}
        cursor += new_dur
    if not new_b:
        raise RuntimeError("驱动轨一段素材都没填上（assets['a_roll'] 为空或全不可读）")
    new_total = cursor
    map_start, scale_at = _anchor_map_builder(old_b, new_b, new_total)

    # ---- 2b. 视频叠层：硬约束「时间上重叠的两层绝不同源」 ----
    #
    # 为什么要有这一遍：原版主轨是**同一条真片**切成 13 段，叠加轨再把同一条素材的
    # 另一个入点/抠像叠上来（推近、人像抠像压背景）。换成新素材池后，池子会轮转复用：
    # 一旦复用发生在时间上重叠的两层，画面上就是「两个不同瞬间的同源画面不透明地
    # 叠在一起」——实测 3.0s 一帧里同一个人出现两次、头顶错位。
    #
    # 处理顺序（按优先级，与模版提取端的同一套语义对齐）：
    #   ① 原版是**人像抠像层**（matting.path 非空）→ 必须有 alpha 才能成立，而抠像遮罩
    #      不随模版迁移。给它另接一条新素材满幅盖住主轨 = 把内容盖掉，所以只能跳过并计
    #      overlay_left_blank（不假装做了抠像，也不拿新素材糊上去）。
    #   ② `same_material_as` 是**强绑定**：它和主轨是同一素材的推近/缩窗重构，必须跟主轨
    #      同素材、同入点（相对段偏移），不另接 B-roll。但若它既无遮罩、又满幅不透明，
    #      渲染出来就是同源第二份画面（重影）→ 跳过并计数。小窗/有遮罩（有 alpha）才保留。
    #   ③ 普通 B-roll / 叠加卡片：按时间窗登记已占用的素材，冲突就换一条；换不到留空。
    placed_windows: List[dict] = []  # {start, dur, path, key}

    def _overlaps(a0: float, a1: float, b0: float, b1: float, tol: float = 0.02) -> bool:
        return min(a1, b1) - max(a0, b0) > tol

    def _placed(start: float, dur: float, path: str, key: str) -> None:
        placed_windows.append({"start": start, "dur": dur, "path": path, "key": key})

    def _taken_near(start: float, dur: float) -> set:
        """这些素材已经被**时间上重叠**的图层占着，不许再分给新层。"""
        return {w["path"] for w in placed_windows
                if _overlaps(start, start + dur, w["start"], w["start"] + w["dur"])}

    def _blank_overlay(seg: dict, reason: str, start: float, dur: float,
                       *, ghost: bool, **extra: Any) -> None:
        rep["overlay_left_blank"] += 1
        if ghost:
            rep["ghost_rejected"] += 1
        row = {"why": reason, "key": seg["key"], "track_index": seg["track_index"],
               "role": seg.get("role"), "start": round(start, 3), "duration": round(dur, 3)}
        row.update({k: v for k, v in extra.items() if v is not None})
        rep["overlay_left_blank_detail"].append(row)

    for key, p in plan.items():
        if p.get("asset") and p.get("kind") in ("video", "photo"):
            _placed(p["start"], p["dur"], p["asset"]["path"], key)

    overlay_infos: List[dict] = []
    for seg in segments:
        if seg["track_index"] == driver_idx or seg.get("kind") not in ("video", "photo"):
            continue
        # 复合片段（内嵌子时间线）不参与「抠像/背景板/普通 B-roll」这一遍的素材分配：
        # 它的素材由子时间线自己按层分配，几何也由父段的占位素材承载。放进来会被当成
        # 一张普通 B-roll 分素材，或当背景板切成 pieces——两条路都丢结构，且 plan 里
        # 的 compound 字段（mat_id / placeholder / compound_ref）会被覆盖掉。
        # 交给 2b 的 compound 通道（下面 `if kind == "compound"` 那段）统一建子草稿。
        if seg.get("kind") == "compound":
            continue
        old_start = float(seg.get("start_sec") or 0.0)
        old_dur = float(seg.get("duration_sec") or 0.0)
        new_start = map_start(old_start)
        new_dur = max(0.05, old_dur * scale_at(old_start))
        if new_start + new_dur > new_total + 1e-4:
            new_dur = max(0.05, new_total - new_start)
        geom = seg.get("geometry") or {}
        cover = float(geom.get("cover_ratio") or 0.0)
        clip_alpha = geom.get("alpha")
        opaque = clip_alpha is None or float(clip_alpha) >= 0.99
        role = seg.get("asset_role") or seg.get("role") or "b_roll"
        linked_key = seg.get("same_material_as")
        linked = plan.get(linked_key) if linked_key else None
        is_cutout = bool((seg.get("matting") or {}).get("person_cutout"))
        overlay_infos.append({"seg": seg, "start": new_start, "dur": new_dur,
                              "cover": cover, "opaque": opaque, "role": role,
                              "linked": linked, "is_cutout": is_cutout})

    # 先处理“必须跟随主轨”的和“必须跳过”的（它们不参与抢素材），再按时间窗分配 B-roll。
    #
    # 人像抠像层（原版 `matting.path` 非空）：**原来一律跳过**（`matting_layer_not_reconstructible`），
    # 结果成片只剩主轨 + 文字，原版三格拼贴/撕纸抠像边的设计全丢。现在改成**重建**：
    #   ① 仍按 `same_material_as` 强绑主轨同素材同入点（原版就是主轨那条片子的抠像版，不是另一段内容）；
    #   ② 把模版里的抠像结构（描边颜色/强度）写进新草稿素材的 `matting`，渲染时真跑 Vision 抠像
    #      + `bake_stroke_layer` 撕纸描边，alpha 由 alphamerge 合成（见 `_matting_block`）；
    #   ③ 几何按原版（contain + clip.scale）叠回去。
    # 只有“绑不到主轨素材”的抠像层才继续跳过并计 `cutout_layers_skipped`，不假装重建成功。
    rebuilt_cutouts: List[dict] = []   # 已重建的抠像层（供背景板判定）
    dropped_cutout_keys: List[str] = []  # 彻底丢弃（不分配素材）的抠像层，供写段后对账

    def _bind_cutout_to_main(seg: dict, start: float, dur: float) -> Optional[dict]:
        """抠像层没有 `same_material_as`（或锚段在新片里没上）时，按**原版素材指纹**重绑。

        原版抠像层就是「主轨那条片子的另一条重构」，`matting` 只是从它身上抠人像。
        只要模版主轨有一段与它 `material_fingerprint` 相同、且在新时间轴上真的与它
        同时出现在画面上（窗口有交集），那段就能当锚——不必留空，也不必另接 B-roll。
        找不到就返回 None：调用方按「彻底不分配素材」处理（绝不留同源不透明副本）。
        """
        fp = seg.get("material_fingerprint")
        if not fp:
            return None
        best: Optional[tuple] = None
        for cand in driver_segs:
            if cand.get("kind") in ("compound", "photo"):
                continue
            if cand.get("material_fingerprint") != fp:
                continue
            p = plan.get(cand["key"])
            if not p or not p.get("asset"):
                continue
            ov = min(start + dur, p["start"] + p["dur"]) - max(start, p["start"])
            if best is None or ov > best[0]:
                best = (ov, cand["key"], p)
        if best is None or best[0] <= 0.02:
            return None
        return {"asset": best[2]["asset"], "src_in": best[2].get("src_in"),
                "old_start": best[2].get("old_start"), "key": best[1]}

    for info in sorted(overlay_infos, key=lambda i: (i["start"], i["seg"]["track_index"])):
        seg = info["seg"]
        new_start, new_dur = info["start"], info["dur"]
        old_start = float(seg.get("start_sec") or 0.0)
        old_dur = float(seg.get("duration_sec") or 0.0)
        linked = info["linked"]
        has_mask = bool(seg.get("mask"))
        if info["is_cutout"]:
            binding = linked if (linked and linked.get("asset")) else _bind_cutout_to_main(
                seg, new_start, new_dur)
            if binding is None:
                # 两条路二选一，选「彻底不分配素材」：这一层当缺失，成片不写它。
                # 为什么不选「留空但画面里还在」：原版那层是「主轨同一条片子抠出来压在背景上」，
                # 少了抠像、只剩同源不透明副本，就是同一张脸被画两遍（重影）。
                # 所以先把它从 plan 里摘掉（防御：任何早先分配都作废），再如实计
                # `cutout_layer_dropped_no_binding`；写段循环只写 plan，它不可能上屏。
                plan.pop(seg["key"], None)
                _blank_overlay(seg, "cutout_layer_dropped_no_binding", new_start, new_dur,
                               ghost=False, linked_to=seg.get("same_material_as"),
                               cover_ratio=round(info["cover"], 3))
                rep.setdefault("cutout_layers_skipped", {})
                rep["cutout_layers_skipped"]["no_main_binding"] = \
                    rep["cutout_layers_skipped"].get("no_main_binding", 0) + 1
                rep["cutout_layer_dropped_no_binding"] = \
                    rep.get("cutout_layer_dropped_no_binding", 0) + 1
                rep.setdefault("cutout_dropped_detail", []).append({
                    "key": seg["key"], "track_index": seg["track_index"],
                    "start": round(new_start, 3), "duration": round(new_dur, 3),
                    "cover_ratio": round(info["cover"], 3),
                    "material_fingerprint": seg.get("material_fingerprint"),
                    "linked_to": seg.get("same_material_as"),
                })
                dropped_cutout_keys.append(seg["key"])
                continue
            la = binding["asset"]
            binding_key = seg.get("same_material_as") or binding.get("key")
            offset = old_start - float(binding.get("old_start") or 0.0)
            # 可抠的余量按**整条素材**算（不是按锚段 take）：原版抠像层常比它锚定的主轨段长
            # （seg:6:0 盖 0–1.667s，锚段 0:1 只有 0.45–1.667s）。按锚段 take 钳会把人物层截短。
            asset_room = float(la.get("asset_dur") or la["take"])
            src_in = min(max(0.0, float(binding.get("src_in") or 0.0) + offset),
                         max(0.0, asset_room - 0.05))
            new_dur = min(new_dur, max(0.05, asset_room - src_in))
            fill = {"path": la["path"], "src_in": src_in, "take": new_dur, "w": la["w"],
                    "h": la["h"], "reused": True, "wrapped": False,
                    "asset_dur": asset_room, "used_role": la.get("used_role")}
            plan[seg["key"]] = {"kind": seg["kind"], "role": info["role"], "start": new_start,
                                "dur": new_dur, "src_in": src_in, "asset": fill,
                                "reused_asset": True, "old_start": old_start,
                                "old_dur": old_dur, "track_index": seg["track_index"],
                                "same_material_as": binding_key,
                                "cutout": True,
                                "matting": seg.get("matting") or {"person_cutout": True}}
            rep["cutout_layers_rebuilt"] = rep.get("cutout_layers_rebuilt", 0) + 1
            rep["content_linked"] = rep.get("content_linked", 0) + 1
            if (seg.get("matting") or {}).get("stroke"):
                rep["stroke_applied"] = rep.get("stroke_applied", 0) + 1
            rep.setdefault("cutout_rebuilt_detail", []).append({
                "key": seg["key"], "track_index": seg["track_index"],
                "start": round(new_start, 3), "duration": round(new_dur, 3),
                "src_in": round(src_in, 3),
                "linked_to": binding_key,
                "bound_by": ("same_material_as" if (linked and linked.get("asset"))
                             else "material_fingerprint"),
                "asset_ref": _asset_ref(fill["path"]),
                "stroke": bool((seg.get("matting") or {}).get("stroke")),
                "backdrop_asset_ref": None,
            })
            rebuilt_cutouts.append({"start": new_start, "dur": new_dur,
                                    "track_index": seg["track_index"],
                                    "key": seg["key"]})
            _placed(new_start, new_dur, fill["path"], seg["key"])
            continue
        if linked and linked.get("asset"):
            if not has_mask and info["opaque"] and info["cover"] >= FULL_FRAME_COVER:
                _blank_overlay(seg, "same_source_fullframe_duplicate", new_start, new_dur,
                               ghost=True, linked_to=seg.get("same_material_as"),
                               cover_ratio=round(info["cover"], 3))
                continue
            la = linked["asset"]
            offset = old_start - float(linked.get("old_start") or 0.0)
            src_in = min(max(0.0, float(linked["src_in"]) + offset), max(0.0, la["take"] - 0.05))
            new_dur = min(new_dur, max(0.05, la["take"] - src_in))
            fill = {"path": la["path"], "src_in": src_in, "take": new_dur, "w": la["w"],
                    "h": la["h"], "reused": True, "wrapped": False,
                    "used_role": la.get("used_role")}
            rep["content_linked"] = rep.get("content_linked", 0) + 1
            plan[seg["key"]] = {"kind": seg["kind"], "role": info["role"], "start": new_start,
                                "dur": new_dur, "src_in": src_in, "asset": fill,
                                "reused_asset": True, "old_start": old_start,
                                "old_dur": old_dur, "track_index": seg["track_index"],
                                "same_material_as": seg.get("same_material_as")}
            _placed(new_start, new_dur, fill["path"], seg["key"])
    # 再分配普通叠层：**不许**拿与重叠层相同的素材
    def _backdrop_of_cutout(info: dict) -> bool:
        """这层是「人像抠像层的背景板」吗（原版三明治：主轨 → 模糊背景板 → 人像抠像层）。

        回去（重建前）：抠像层被跳过后，不透明背景板留在原位就是一块盖在主轨上、没人的空镜，
        所以一并跳过并计 `orphaned_backdrop_of_skipped_cutout`。
        现在：抠像层已经重建，背景板就不再是孤儿——它是「人物压在这层模糊板上」这幅构图的底板，
        必须一起重建（否则人物悬在另一个背景上，设计还是丢了）。

        **三条前置条件（缺一不可）**：① 不透明；② 时间窗大半被更高轨上的抠像层盖住；
        ③ **原版这层真带模糊**且是视频——原来只凭①②就把「kind=photo + 线性蒙版 + 无模糊」
        的照片竖条当背景板：重建时换素材、丢蒙版、不加模糊，结果是一张全屏同源副本，
        把主轨那张脸盖掉（style_07 seg:4:1，3.5s 判 2 对 FAIL）。
        verify 的「模糊背景板」豁免口径是「同源 + 同入点 + 实测 sigma≥8」，两边必须一致：
        没模糊证据就不当板，照片一律不当板。

        用哪条素材见下面重建分支：跟着**每层抠像人像自己的素材/入点**切块，不再另接 B-roll。
        （原版背景板 seg:1:0 的 file_slot=1、与主轨 file_slot=0 不是同一个文件；但新素材池里
        每一条片子都有人，照抄“另一条独立素材”会得到「清晰的人 + 背后模糊的另一个人」。）
        """
        if not info["opaque"] or not rebuilt_cutouts:
            return False
        a0, a1 = info["start"], info["start"] + info["dur"]
        hidden = 0.0
        for c in rebuilt_cutouts:
            if int(c["track_index"] or 0) <= int(info["seg"]["track_index"] or 0):
                continue  # 只看“更高轨”上的抠像层（数组序即 z 序）
            hidden += max(0.0, min(a1, c["start"] + c["dur"]) - max(a0, c["start"]))
        if hidden < 0.5 * max(1e-6, a1 - a0):
            return False
        # 到这里才是「本来会被当成背景板」的候选层；下面两条门槛不许它当板（D5）。
        if info["seg"].get("kind") != "video":
            rep["backdrop_rejected_photo"] = rep.get("backdrop_rejected_photo", 0) + 1
            rep.setdefault("backdrop_rejected_detail", []).append({
                "key": info["seg"]["key"], "why": "not_a_video_layer",
                "kind": info["seg"].get("kind"),
                "has_mask": bool(info["seg"].get("mask")),
                "cover_ratio": round(info["cover"], 3)})
            return False
        if not _segment_has_blur(info["seg"]):
            rep["backdrop_rejected_not_blurred"] = \
                rep.get("backdrop_rejected_not_blurred", 0) + 1
            rep.setdefault("backdrop_rejected_detail", []).append({
                "key": info["seg"]["key"], "why": "no_blur_effect",
                "kind": info["seg"].get("kind"),
                "has_mask": bool(info["seg"].get("mask")),
                "cover_ratio": round(info["cover"], 3)})
            return False
        return True

    for info in sorted(overlay_infos, key=lambda i: (i["start"], i["seg"]["track_index"])):
        seg = info["seg"]
        if seg["key"] in plan or info["is_cutout"]:
            continue
        new_start, new_dur = info["start"], info["dur"]
        old_start = float(seg.get("start_sec") or 0.0)
        old_dur = float(seg.get("duration_sec") or 0.0)
        role = info["role"]
        if _backdrop_of_cutout(info):
            # 背景板重建。原版是一层「模糊背景视频」横跨数个抠像层，人物压在这层模糊板上。
            #
            # 原来：抠像层被跳过时，这块板也跟着跳过（orphaned_backdrop_of_skipped_cutout）。
            # 现在：抠像层回来了，板必须回来，否则人物悬在别的东西上，设计还是丢。
            #
            # 用哪条素材：**跟它上面每层抠像人像同素材同入点**，并**按那段抠像层切成几块**。
            # 为什么不照原版留一块连续长板、也不另接一条 B-roll：
            #   · 另接一条 B-roll：新素材池里每一条片子都有人（A-roll 有人脸；实测 B-roll 候选
            #     里也有出镜人物），成片就是「清晰的人 + 背后一个模糊的另一个人」——5.9s 帧
            #     Vision 直接认出两个人，比原版「同一人被虚化在自己身后」更假。
            #   · 一块连续长板固定在起始那段素材上：新素材池把相邻的主轨段分给了不同片子
            #     （原版是一条连续拍摄），长板播到后面就变成「同一个文件里另一个时刻的画面
            #     当别人的背景」，还是跨素材/跨时刻的错误匹配。
            #   · 切成与抠像层对齐的几块：每块的素材/入点都与它上面的抠像人像、下面的主轨
            #     完全同帧，只是模糊——原版设计（人像抠像 + 模糊底）一比一保留。
            # 重影检查端因此给这类层一条**带证据的**例外：同源 + 同入点（≤0.06s）+ 真带模糊
            # 特效（sigma≥8），见 verify 的 check_overlay_ghosting。
            cuts_above = [c for c in rebuilt_cutouts
                          if int(c["track_index"] or 0) > int(seg["track_index"] or 0)
                          and min(new_start + new_dur, c["start"] + c["dur"])
                          - max(new_start, c["start"]) > 0.02]
            pieces: List[dict] = []
            for c in sorted(cuts_above, key=lambda x: x["start"]):
                cp = plan.get(c["key"]) or {}
                ca = cp.get("asset")
                if not ca:
                    continue
                s0 = max(new_start, c["start"])
                s1 = min(new_start + new_dur, c["start"] + c["dur"])
                if s1 - s0 <= 0.05:
                    continue
                p_in = float(cp.get("src_in") or 0.0) + (s0 - c["start"])
                p_room = float(ca.get("asset_dur") or ca["take"]) - p_in
                p_dur = min(s1 - s0, max(0.05, p_room))
                if s1 - s0 - p_dur > 0.05:
                    rep["backdrop_clipped"] = rep.get("backdrop_clipped", 0) + 1
                pieces.append({"start": s0, "dur": p_dur, "src_in": max(0.0, p_in),
                               "asset": ca, "above": c["key"]})
            if not pieces:
                _blank_overlay(seg, "backdrop_no_main_footage", new_start, new_dur,
                               ghost=False, cover_ratio=round(info["cover"], 3))
                rep.setdefault("backdrop_skipped", {})
                rep["backdrop_skipped"]["no_aligned_footage"] = \
                    rep["backdrop_skipped"].get("no_aligned_footage", 0) + 1
                continue
            plan[seg["key"]] = {"kind": seg["kind"], "role": info["role"],
                                "start": pieces[0]["start"],
                                "dur": sum(pc["dur"] for pc in pieces),
                                "src_in": pieces[0]["src_in"],
                                "asset": pieces[0]["asset"], "reused_asset": True,
                                "old_start": old_start, "old_dur": old_dur,
                                "track_index": seg["track_index"], "backdrop": True,
                                "pieces": pieces,
                                "backdrop_of": [pc["above"] for pc in pieces]}
            rep["backdrop_rebuilt"] = rep.get("backdrop_rebuilt", 0) + 1
            rep.setdefault("backdrop_rebuilt_detail", []).append({
                "key": seg["key"], "track_index": seg["track_index"],
                "start": round(pieces[0]["start"], 3),
                "duration": round(sum(pc["dur"] for pc in pieces), 3),
                "pieces": [{"start": round(pc["start"], 3), "duration": round(pc["dur"], 3),
                            "src_in": round(pc["src_in"], 3), "above": pc["above"],
                            "asset_ref": _asset_ref(pc["asset"]["path"])} for pc in pieces],
                "same_material_as_cutout": True, "same_in_point_as_cutout": True,
                "effects": [e.get("name") for e in (seg.get("effects") or [])],
                "cover_ratio": round(info["cover"], 3),
            })
            for row in rep.get("cutout_rebuilt_detail") or []:
                if row.get("key") in (plan[seg["key"]]["backdrop_of"] or []):
                    row["backdrop_asset_ref"] = row.get("asset_ref")
            continue
        avoid = _taken_near(new_start, new_dur)
        if role == "photo":
            fill = take_unique_photo(new_dur, avoid,
                                     fingerprint=seg.get("material_fingerprint"),
                                     key=seg["key"])
        else:
            fill = take_for(role if role in ROLE_FALLBACK else "b_roll", new_dur, avoid=avoid)
        if fill is None:
            _blank_overlay(seg, "asset_pool_exhausted_avoid_same_source", new_start, new_dur,
                           ghost=True, cover_ratio=round(info["cover"], 3),
                           blocked_assets=len(avoid))
            continue
        if fill.get("conflict_skipped"):
            rep["overlap_conflicts_avoided"] += 1
        new_dur = min(new_dur, fill["take"])
        if new_start + new_dur > new_total + 1e-4:
            new_dur = max(0.05, new_total - new_start)
            rep["seg_clipped"] = rep.get("seg_clipped", 0) + 1
        plan[seg["key"]] = {"kind": seg["kind"], "role": role, "start": new_start,
                            "dur": new_dur, "src_in": fill.get("src_in", 0.0),
                            "asset": fill, "reused_asset": bool(fill.get("reused")),
                            "old_start": old_start, "old_dur": old_dur,
                            "track_index": seg["track_index"],
                            "conflict_skipped": int(fill.get("conflict_skipped") or 0)}
        _placed(new_start, new_dur, fill["path"], seg["key"])

    # ---- 2c. 复合片段：按子结构重建内嵌子时间线（materials.drafts + subdraft/） ----
    #
    # 上一轮这里是 `skipped["compound_unrealized"]`：整块跳过。结果 0.45~1.667s 那块
    # 「三格拼贴底板」直接没了（成片只剩人像压在压暗的主轨上）。
    #
    # 现在：模版已经把子时间线的轨/段/几何/关键帧/动画/文字位递归拆出来了（与顶层同一
    # schema，见 replica_template._extract_level），这里就**按同一套规则换素材**，再写回
    # 剪映的真实结构：`materials.drafts[*].draft`（内嵌完整子时间线）+ `subdraft/<uuid>/`
    # 目录落盘，父段用 `extra_material_refs` 引用那个 drafts 条目。
    #
    # 角色跨层规则（硬约束，别改）：
    #   · 子时间线的 `photo` 层（原版 = 5 张 scale 0.8 的拼贴静图）**只能**取 photo 池的
    #     **图片**；池里没图就留空并计 `photo_layer_no_image`，**绝不**拿视频顶
    #     （视频顶进去就不是「照片翻页」而是「视频墙」，结构丢了）；
    #   · 子层的 `a_roll/b_roll/overlay_card` 跟顶层一样走叠层同源硬约束（时间窗内有重叠就换一条）；
    #   · 子层的 sticker/音效同样只取同角色，缺就计缺口。
    subdraft_root = draft_dir / "subdraft"
    drafts_mats: List[dict] = []
    # 每个复合片段子稿的 z 序对账（写入报告：tracks_reordered / z_order_mismatch / 排序结果）
    sub_order_stats: List[dict] = []
    # 子时间线文字层的文案池：只取「无 slot 的通用文案」。带 slot 的文案是给顶层文字位
    # 显式对位的，不能被内嵌子时间线抢去（否则顶层的字幕会整条错位）。
    loose_texts: List[Any] = [t for t in (texts or [])
                              if not (isinstance(t, dict) and isinstance(t.get("slot"), int))]
    rep.setdefault("compounds_applied", 0)
    rep.setdefault("compound_applied_detail", [])
    rep.setdefault("compound_skipped", {})
    rep.setdefault("sub_segments_total", 0)
    rep.setdefault("photo_layers_mapped", 0)
    rep.setdefault("compound_anim_clamped", 0)
    rep.setdefault("compound_time_scaled", 0)
    rep.setdefault("compound_length_mismatch", 0)
    rep.setdefault("compound_canvas_refs_dropped", 0)

    def _skip_compound(key: str, why: str) -> None:
        rep["compound_skipped"][why] = rep["compound_skipped"].get(why, 0) + 1
        rep["compound_applied_detail"].append({"key": key, "applied": False, "why": why})

    def build_sub_draft(seg: dict, *, parent_start: float, parent_dur: float) -> Optional[dict]:
        """模版段 → 一条可落盘的子时间线（与顶层草稿同一套素材/段写法）。

        子段的时间反从「相对子时间线总长」按 `parent_dur/sub_dur` 比例映射到**父段新时长**：
        父段可能因锚点重映射/素材不够而变短变长，子时间线必须跟着缩，否则父段取不满
        （渲染器是按父段时长去抽子片段的）。
        """
        comp = seg.get("compound") or {}
        sub_segs = comp.get("segments") or []
        sub_tracks = comp.get("tracks") or []
        sub_slots = comp.get("sub_slots") or []
        sub_dur = float(comp.get("duration_sec") or 0.0)
        if not sub_segs:
            return None
        scale = (parent_dur / sub_dur) if sub_dur > 1e-6 else 1.0
        if abs(scale - 1.0) > 1e-3:
            rep["compound_time_scaled"] += 1
        if not comp.get("length_matches_parent", True):
            rep["compound_length_mismatch"] += 1

        sub_videos: Dict[str, dict] = {}
        sub_audios: Dict[str, dict] = {}
        sub_texts: Dict[str, dict] = {}
        sub_anims: List[dict] = []
        sub_speeds: List[dict] = []
        sub_masks: List[dict] = []
        sub_vfx: List[dict] = []
        sub_track_segs: Dict[int, List[dict]] = {t["index"]: [] for t in sub_tracks}
        local_taken: List[dict] = []
        stats = {"segments": 0, "photo_layers": 0, "skipped": {}}

        def _skip(why: str) -> None:
            stats["skipped"][why] = stats["skipped"].get(why, 0) + 1

        def _sub_taken(t0: float, dur: float) -> set:
            """子层避让只看**这一层自己的时间窗**（子时间轴内），不是父段整窗。

            原来什么：`_taken_near(parent_start, parent_dur)` —— 父段窗口里用过的素材对
            同父段的**所有**子层都禁用，于是一个复合片段最多只能填满「池子条数」个子层。
            原版长复合片段常常是**顺序播放**（同一时刻只上 1 层，style_23 的 25 段一拍一层），
            本该复用同一批素材；按父段整窗挡会把 45 层留空、子时间线拼成空片。
            改成什么：子层自己的 `[t0, t0+dur)` 与已放子层窗口求交集，时间上不重叠的放行复用。
            """
            return {x["path"] for x in local_taken
                    if _overlaps(t0, t0 + dur, x["start"], x["start"] + x["dur"])}

        for s in sorted(sub_segs, key=lambda x: (int(x.get("track_index") or 0),
                                                    float(x.get("start_sec") or 0.0))):
            kind = s.get("kind")
            role = str(s.get("asset_role") or s.get("role") or "")
            t0 = max(0.0, float(s.get("start_sec") or 0.0) * scale)
            dur = max(0.05, float(s.get("duration_sec") or 0.0) * scale)
            if t0 + dur > parent_dur + 1e-3:
                dur = max(0.05, parent_dur - t0)
            t_i = int(s.get("track_index") or 0)
            sub_track_segs.setdefault(t_i, [])
            geom = dict(s.get("geometry") or {})
            extra: List[str] = []
            mat_id = None
            src_in = 0.0
            src_dur = dur
            is_audio = False

            if kind == "effect" or role == "effect":
                _skip("effect_track_in_compound")
                continue
            if kind == "photo" or role == "photo":
                # 照片层：只取图片（角色规则见上面注释）+ **全片唯一**（见 take_unique_photo）：
                # 原来只避开「时间窗重叠」的层，于是两个不同复合片段会拿到同一张图；
                # 现在全片一本账，同一张图只能用一次（原版同比图例外）。
                avoid = _taken_near(parent_start + t0, dur) | _sub_taken(t0, dur)
                fill = take_unique_photo(dur, avoid,
                                         fingerprint=s.get("material_fingerprint"),
                                         key=f"{seg['key']}#sub{t_i}")
                if fill is None:
                    _skip("photo_layer_no_image")
                    continue
                name = stage(fill["path"], "video")
                crop_use = usable_crop(s, fill, canvas_w, canvas_h, rep)
                if not crop_use:
                    # 模版没带 crop 时，按原版图层的**展示宽高比**居中裁一刀（详见 _aspect_crop）
                    sr = geom.get("size_ratio")
                    want = None
                    if sr and len(sr) == 2 and float(sr[1] or 0) > 0:
                        want = (float(sr[0]) * canvas_w) / max(1e-6, float(sr[1]) * canvas_h)
                    crop_use = _aspect_crop(fill, want)
                    if crop_use:
                        rep["photo_layer_aspect_cropped"] = \
                            rep.get("photo_layer_aspect_cropped", 0) + 1
                mk = f"photo|{fill['path']}|{json.dumps(crop_use or {}, sort_keys=True)}"
                if mk not in sub_videos:
                    sub_videos[mk] = _video_material(name, root_uuid, "materials/video",
                                                     fill["w"], fill["h"], float(fill["take"]),
                                                     crop_use, is_photo=True)
                    sub_videos[mk]["_path"] = fill["path"]
                mat_id = sub_videos[mk]["id"]
                stats["photo_layers"] += 1
                local_taken.append({"start": t0, "dur": dur, "path": fill["path"]})
                # 跨复合片段登记：两个复合片段时间窗重叠时（原版 5.3~8.2s 与 5.88~8.22s 就是），
                # 它们的照片层不许同源叠两居——靠 _placed/_taken_near 这同一本账。
                _placed(parent_start + t0, dur, fill["path"], f"{seg['key']}#sub{int(s.get('track_index') or 0)}")
            elif kind in ("video",) or role in ("a_roll", "b_roll", "overlay_card"):
                avoid = _taken_near(parent_start + t0, dur) | _sub_taken(t0, dur)
                fill = take_for(role if role in ROLE_FALLBACK else "b_roll", dur, avoid=avoid)
                if fill is None:
                    _skip("video_layer_no_asset")
                    continue
                name = stage(fill["path"], "video")
                crop_use = usable_crop(s, fill, canvas_w, canvas_h, rep)
                mk = f"{fill['path']}|{json.dumps(crop_use or {}, sort_keys=True)}"
                if mk not in sub_videos:
                    sub_videos[mk] = _video_material(name, root_uuid, "materials/video",
                                                     fill["w"], fill["h"], float(fill["take"]),
                                                     crop_use, is_photo=False)
                    sub_videos[mk]["_path"] = fill["path"]
                mat_id = sub_videos[mk]["id"]
                src_in = float(fill.get("src_in") or 0.0)
                dur = min(dur, float(fill["take"]))
                local_taken.append({"start": t0, "dur": dur, "path": fill["path"]})
                _placed(parent_start + t0, dur, fill["path"],
                        f"{seg['key']}#sub{int(s.get('track_index') or 0)}")
            elif kind == "sticker" or role == "sticker":
                fill = take_for("sticker", dur)
                if fill is None:
                    _skip("sticker_layer_no_asset")
                    continue
                name = stage(fill["path"], "sticker")
                mk = f"sticker|{fill['path']}"
                if mk not in sub_videos:
                    sub_videos[mk] = {
                        "id": _uid(),
                        "path": f"##_draftpath_placeholder_{root_uuid}_##/materials/sticker/{name}",
                        "type": "sticker", "material_name": name, "_path": fill["path"],
                    }
                mat_id = sub_videos[mk]["id"]
            elif kind == "audio" or role in ("bgm", "sfx"):
                fill = take_for(role if role in ROLE_FALLBACK else "sfx", dur)
                if fill is None:
                    _skip("audio_layer_no_asset")
                    continue
                name = stage(fill["path"], "audio")
                mk = f"{fill['path']}|{role}"
                if mk not in sub_audios:
                    sub_audios[mk] = _audio_material(
                        name, root_uuid, "materials/audio", float(fill["take"]),
                        "music" if role == "bgm" else "sound")
                    sub_audios[mk]["_path"] = fill["path"]
                mat_id = sub_audios[mk]["id"]
                src_in = float(fill.get("src_in") or 0.0)
                dur = min(dur, float(fill["take"]))
                is_audio = True
            elif kind == "text":
                si = s.get("text_slot")
                if si is None or si >= len(sub_slots):
                    _skip("text_layer_without_slot")
                    continue
                slot = sub_slots[si]
                raw = loose_texts[(int(si) % len(loose_texts))] if loose_texts else None
                if isinstance(raw, dict):
                    raw = raw.get("text")
                txt = str(raw or "").strip()
                if not txt:
                    _skip("text_layer_no_copy")
                    continue
                per_line = int(slot.get("capacity_per_line") or 0)
                max_lines = int(slot.get("max_lines") or slot.get("capacity_lines") or 1)
                if per_line <= 0:
                    per_line = max(1, int(slot.get("char_cap") or 12) // max(1, max_lines))
                laid = layout_text(txt, per_line, max_lines)
                if laid["truncated"]:
                    rep["texts_truncated"] += 1
                fname = str(((slot.get("font") or {}).get("name")) or "").strip()
                hit_font = resolve_template_font(fname, style_id) if fname else None
                fpath = hit_font["path"] if hit_font else fallback_font
                mk = f"txt|{si}"
                if mk not in sub_texts:
                    sub_texts[mk] = _text_style_material(laid["text"], slot.get("style") or {}, fpath)
                mat_id = sub_texts[mk]["id"]
                src_in = 0.0
                src_dur = 0.0
            else:
                _skip("unknown_layer_kind")
                continue

            holder, _items = _anim_holder(s.get("animations") or [], dur, rep)
            if holder:
                sub_anims.append(holder)
                extra.append(holder["id"])
                for an in (s.get("animations") or []):
                    if float(an.get("start_ratio") or 0) * dur + float(an.get("duration_ratio") or 0) * dur \
                            > dur + 1e-3:
                        rep["compound_anim_clamped"] += 1
                        break
            if not is_audio:
                spd = _speed_material()
                sub_speeds.append(spd)
                extra.append(spd["id"])
            if s.get("mask"):
                mask_mat = {"category": "video", "config": s["mask"].get("config") or {},
                            "id": _uid(), "name": s["mask"].get("name"),
                            "path": "", "resource_type": str(s["mask"].get("resource_type") or ""),
                            "text_config": {"align_type": 15}, "type": "mask"}
                sub_masks.append(mask_mat)
                extra.append(mask_mat["id"])
            for fx in s.get("effects") or []:
                fx_mat = {"adjust_params": fx.get("adjust_params") or [],
                          "category_id": "100000", "category_name": "画面特效",
                          "effect_id": fx.get("effect_id"), "id": _uid(),
                          "name": fx.get("name"), "path": "",
                          "render_index": fx.get("render_index") or 11000,
                          "resource_id": "", "type": "video_effect", "value": 1.0}
                sub_vfx.append(fx_mat)
                extra.append(fx_mat["id"])

            sub_track_segs[t_i].append(_segment(
                mat_id=mat_id, tl_start=t0, dur=dur, src_in=src_in, src_dur=src_dur,
                geometry=geom, keyframes=_keyframes_of(s, dur, src_in),
                extra_refs=extra, track_index=t_i, render_index=s.get("render_index")))
            stats["segments"] += 1

        if not any(segs for segs in sub_track_segs.values()):
            return None

        sub_out_tracks: List[dict] = []
        # 子时间线也必须按原版 render_index 升序写（渲染器进复合片段后同样「轨序即 z 序」，
        # 不排序就会把子层里本该在上面的那层写到下面）。原来什么：直接按模版数组序写。
        sub_tracks_ordered = _z_sort_tracks(sub_tracks)
        for tr in sub_tracks_ordered:
            segs = sub_track_segs.get(tr["index"]) or []
            if not segs or tr.get("role") == "effect":
                continue
            segs.sort(key=lambda x: x["target_timerange"]["start"])
            sub_out_tracks.append({
                "attribute": 0,
                "flag": int(tr.get("flag") or 0),
                "id": _uid(),
                "is_default_name": True,
                "name": "",
                "segments": segs,
                "type": TRACK_TYPE_OF_ROLE.get(tr.get("role"), tr.get("track_type") or "video"),
            })
        # 子稿的 z 序对账：只统计真正写出去的轨（effect 轨被跳过不计入）
        _z_apply_render_index(sub_out_tracks)
        sub_order_stats.append(_z_order_stats(
            f"compound:{seg['key']}", sub_tracks,
            [t for t in sub_tracks_ordered
             if sub_track_segs.get(t["index"]) and t.get("role") != "effect"],
            sub_segs))

        videos = [v for v in sub_videos.values() if v.get("type") != "sticker"]
        stickers = [{"category_id": "", "category_name": "", "id": v["id"],
                     "material_name": v["material_name"], "path": v["path"],
                     "resource_id": "", "type": "sticker", "source_platform": 0,
                     "preview_cover": "", "extra_info": ""}
                    for v in sub_videos.values() if v.get("type") == "sticker"]
        sub_draft = {
            "canvas_config": {"height": canvas_h, "ratio": "original", "width": canvas_w},
            "color_space": 0,
            "duration": _us(parent_dur),
            "fps": fps,
            "id": _uid(),
            "is_minimal_draft": True,
            "keyframes": {"adjusts": [], "audios": [], "effects": [], "filters": [],
                          "handwrites": [], "stickers": [], "texts": [], "videos": []},
            "materials": {
                "audios": list(sub_audios.values()),
                "beats": [],
                "canvases": [],
                "common_mask": sub_masks,
                "drafts": [],
                "material_animations": sub_anims,
                "material_colors": [],
                "placeholder_infos": [],
                "sound_channel_mappings": [],
                "speeds": sub_speeds,
                "stickers": stickers,
                "texts": list(sub_texts.values()),
                "transitions": [],
                "video_effects": sub_vfx,
                "videos": videos,
                "vocal_separations": [],
            },
            "name": "sub_compound",
            "new_version": "125.0.0",
            "path": "",
            "platform": {"app_id": 3704, "app_source": "lv", "app_version": "11.8.0",
                         "os": "mac"},
            "relationships": [],
            "render_index_track_mode_on": True,
            "source": "default",
            "static_cover_image_path": "",
            "tracks": sub_out_tracks,
            "uneven_animation_template_info": {"composition": "none", "content": "",
                                              "order": "none", "sub_template_id": ""},
            "version": 360000,
        }
        stats["has_visual"] = any(t["type"] in ("video", "text", "sticker")
                                  for t in sub_out_tracks)
        stats["sub_draft"] = sub_draft
        return stats

    # ---- 2. 其余轨：锚点映射 + 段内比例 ----
    skipped_text_no_slot = 0
    for seg in segments:
        on_driver = seg["track_index"] == driver_idx
        if seg["key"] in plan:
            continue
        # 驱动轨上的复合片段必须放行到 compound 通道（见驱动轨那段注释），
        # 其余驱动轨段已经在 plan 里，不会走到这里。
        if on_driver and seg.get("kind") != "compound":
            continue
        role = seg.get("asset_role") or seg.get("role") or ""
        kind = seg.get("kind")
        old_start = float(seg.get("start_sec") or 0.0)
        old_dur = float(seg.get("duration_sec") or 0.0)
        new_start = map_start(old_start)
        new_dur = max(0.05, old_dur * scale_at(old_start))
        fill = None
        if kind == "compound":
            reasons = seg.get("review_reasons") or []
            comp = seg.get("compound") or {}
            if not comp:
                why = ("depth_exceeded" if "compound_depth_exceeded" in reasons else
                       "sub_timeline_missing" if "compound_sub_timeline_missing" in reasons else
                       "template_without_sub_structure")
                _skip_compound(seg["key"], why)
                continue
            built = build_sub_draft(seg, parent_start=new_start, parent_dur=new_dur)
            if built is None:
                # 子层一个图层都没换到素材（例如只有贴纸/音效而池子空）：不写空壳子草稿，
                # 但把逐层缺口原因带进报告（不静默丢）。
                _skip_compound(seg["key"], "no_sub_layers_materialized")
                continue
            # 子层「有画面」才值得当成视频叠加层写入；纯音频复合片段走音频占位素材
            has_visual = bool(built.get("has_visual"))
            sub_uuid = str(uuid.uuid4())
            sub_dir = subdraft_root / sub_uuid
            sub_dir.mkdir(parents=True, exist_ok=True)
            (sub_dir / "draft_content.json").write_text(
                json.dumps(built["sub_draft"], ensure_ascii=False, indent=2), encoding="utf-8")
            (sub_dir / "sub_draft_config.json").write_text(
                json.dumps({"draft_id": built["sub_draft"]["id"],
                            "draft_name": "sub_compound",
                            "draft_fold_path": str(sub_dir).replace("\\", "/")},
                           ensure_ascii=False, indent=2), encoding="utf-8")
            entry_id = _uid()
            placehold_name = ("compound_av" if has_visual else "compound_audio")
            ph = _compound_placeholder_material(placehold_name, root_uuid, canvas_w, canvas_h,
                                                new_dur, audio=not has_visual)
            drafts_mats.append({
                "combination_id": _uid(),
                "combination_type": "none",
                "draft": built["sub_draft"],
                "draft_config_path": f"##_draftpath_placeholder_{root_uuid}_##/subdraft/{sub_uuid}/sub_draft_config.json",
                "draft_cover_path": f"##_draftpath_placeholder_{root_uuid}_##/subdraft/{sub_uuid}/draft_cover.jpg",
                "draft_file_path": f"##_draftpath_placeholder_{root_uuid}_##/subdraft/{sub_uuid}/draft_content.json",
                "id": entry_id,
                "type": "combination",
            })
            plan[seg["key"]] = {"kind": "compound", "role": role, "start": new_start,
                                "dur": new_dur, "src_in": 0.0, "asset": None,
                                "reused_asset": False, "old_start": old_start,
                                "old_dur": old_dur, "track_index": seg["track_index"],
                                "mat_id": ph["id"], "placeholder": ph,
                                "compound_ref": entry_id, "is_audio": not has_visual,
                                "sub_draft": built["sub_draft"]}
            rep["compounds_applied"] += 1
            rep["sub_segments_total"] += int(built.get("segments") or 0)
            rep["photo_layers_mapped"] += int(built.get("photo_layers") or 0)
            for k, v in (built.get("skipped") or {}).items():
                rep["compound_skipped"][f"sub_{k}"] = rep["compound_skipped"].get(f"sub_{k}", 0) + v
            rep["compound_applied_detail"].append({
                "key": seg["key"], "applied": True,
                "layout": (comp.get("asset_profile") or {}).get("layout"),
                "depth": comp.get("depth"),
                "start": round(new_start, 3), "duration": round(new_dur, 3),
                "sub_segments": int(built.get("segments") or 0),
                "photo_layers_mapped": int(built.get("photo_layers") or 0),
                "visual": has_visual,
                "time_scale": round((new_dur / float(comp.get("duration_sec") or new_dur)), 4),
                "sub_draft_dir": str(sub_dir.relative_to(draft_dir)),
            })
            continue
        if kind in ("video", "photo"):
            # 视频/静图叠层已在「2b. 视频叠层」里统一分配（含同源硬约束与跳过记账）：
            # 这里绝不能再分一次素材——二次分配会把刚跳过的抠像层又接上新素材。
            continue
        linked_key = seg.get("same_material_as")
        linked = plan.get(linked_key) if linked_key else None
        if linked and linked.get("asset") and kind in ("video", "photo"):
            # 原版这里是「同一素材推近/缩窗」：跟主轨同一素材、同一入点（+段内偏移），不另接 B-roll
            la = linked["asset"]
            offset = old_start - float(linked.get("old_start") or 0.0)
            src_in = min(max(0.0, float(linked["src_in"]) + offset), max(0.0, la["take"] - 0.05))
            new_dur = min(new_dur, max(0.05, la["take"] - src_in))
            fill = {"path": la["path"], "src_in": src_in, "take": new_dur, "w": la["w"],
                    "h": la["h"], "reused": True, "wrapped": False, "used_role": la.get("used_role")}
            rep["content_linked"] = rep.get("content_linked", 0) + 1
            if new_start + new_dur > new_total + 1e-4:
                new_dur = max(0.05, new_total - new_start)
                rep["seg_clipped"] = rep.get("seg_clipped", 0) + 1
            plan[seg["key"]] = {"kind": kind, "role": role, "start": new_start, "dur": new_dur,
                                "src_in": src_in, "asset": fill, "reused_asset": True,
                                "old_start": old_start, "old_dur": old_dur}
            continue
        if kind in ("video", "photo") or role in ("b_roll", "overlay_card", "photo"):
            if kind == "photo" or role == "photo":
                fill = take_unique_photo(new_dur, fingerprint=seg.get("material_fingerprint"),
                                         key=seg["key"])
            else:
                fill = take_for(role if role in ROLE_FALLBACK else "b_roll", new_dur)
            if fill is None:
                rep["skipped"]["b_roll_no_asset"] = rep["skipped"].get("b_roll_no_asset", 0) + 1
                continue
            new_dur = min(new_dur, fill["take"])
        elif kind == "sticker":
            fill = take_for("sticker", new_dur)
            if fill is None:
                rep["skipped"]["sticker_no_asset"] = rep["skipped"].get("sticker_no_asset", 0) + 1
                continue
        elif kind == "audio":
            fill = take_for(role, new_dur)
            if fill is None:
                rep["skipped"]["audio_no_asset"] = rep["skipped"].get("audio_no_asset", 0) + 1
                continue
            new_dur = min(new_dur, fill["take"])
        elif kind == "text":
            if seg.get("text_slot") is None or seg["text_slot"] >= len(text_slots):
                skipped_text_no_slot += 1
                continue
        elif kind == "effect":
            rep["skipped"]["effect_track_not_rendered"] = rep["skipped"].get("effect_track_not_rendered", 0) + 1
            continue
        if new_start + new_dur > new_total + 1e-4:
            new_dur = max(0.05, new_total - new_start)
            rep.setdefault("seg_clipped", 0)
            rep["seg_clipped"] += 1
        plan[seg["key"]] = {"kind": kind, "role": role, "start": new_start, "dur": new_dur,
                            "src_in": (fill or {}).get("src_in", 0.0), "asset": fill,
                            "reused_asset": bool((fill or {}).get("reused")),
                            "old_start": old_start, "old_dur": old_dur}
    if skipped_text_no_slot:
        rep["skipped"]["text_without_slot"] = skipped_text_no_slot

    # ---- 3. 组装草稿 ----
    video_mats: Dict[str, dict] = {}
    audio_mats: Dict[str, dict] = {}
    text_mats: Dict[str, dict] = {}
    speeds: List[dict] = []
    anim_mats: List[dict] = []
    mask_mats: List[dict] = []
    vfx_mats: List[dict] = []
    out_tracks: List[dict] = []
    texts_used = 0
    # 复合片段的占位素材（path 空、真身在 materials.drafts）：按视频/音频分两桶
    ph_video_mats: List[dict] = []
    ph_audio_mats: List[dict] = []

    # 文字位 → 新文案
    #   ① 显式对位优先：文案项写成 {"slot": 4, "text": "..."} 就精确落到第 4 个文字位
    #      （24 个文字位跨 4 条文字轨、时间上交错，纯靠顺序铺很容易把大字贴到字幕位上）
    #   ② 其余按文字位顺序铺，不够就循环复用并计 text_reused（与旧行为一致）
    #   ③ 每一位都过 layout_text()：先按版式容量换行，行数超上限才截断
    slot_text: Dict[int, str] = {}
    text_layout_report: List[dict] = []
    explicit: Dict[int, Any] = {}
    loose: List[Any] = []
    for item in texts or []:
        if isinstance(item, dict) and isinstance(item.get("slot"), int):
            explicit[int(item["slot"])] = item
        else:
            loose.append(item)
    for si in range(len(text_slots)):
        slot = text_slots[si]
        raw = explicit.get(si)
        if raw is None and loose:
            if si < len(loose):
                raw = loose[si]
            else:
                raw = loose[si % len(loose)]
                rep["text_reused"] += 1
        if isinstance(raw, dict):
            raw = raw.get("text")
        txt = str(raw or "").strip()
        if not txt:
            continue
        per_line = int(slot.get("capacity_per_line") or 0)
        max_lines = int(slot.get("max_lines") or slot.get("capacity_lines") or 1)
        capacity = int(slot.get("char_cap") or 0)
        if per_line <= 0:
            # 旧模版（没有 capacity_per_line）：按总容量除行数反推，不直接当单行限制
            per_line = max(1, capacity // max(1, max_lines)) if capacity else 12
        laid = layout_text(txt, per_line, max_lines)
        if laid["truncated"]:
            rep["texts_truncated"] += 1
        slot_text[si] = laid["text"]
        text_layout_report.append({
            "slot": si,
            "track_index": slot.get("track_index"),
            "font": (slot.get("font") or {}).get("name"),
            "font_path_source": (next((v.get("via") for v in font_resolved.values()
                                       if si in (v.get("slots") or [])), None) or "fallback"),
            "capacity": capacity,
            "per_line": per_line,
            "max_lines": max_lines,
            "capacity_basis": slot.get("char_cap_basis") or "legacy",
            "new_text_len": laid["new_text_len"],
            "lines_used": laid["lines_used"],
            "truncated": bool(laid["truncated"]),
            "text": laid["text"],
        })
    rep["text_layout"] = text_layout_report

    track_segs: Dict[int, List[dict]] = {t["index"]: [] for t in tracks}
    # 草稿段 id → 模版段 key：verify 端要拿它回到 `template.json` 里核「原版同刻是否本来就有
    # 同指纹的两层」（第三条例外必须拿证据换，不能凭“看起来同源”放行）。
    segment_map: List[dict] = []

    # 背景板按抠像层切块：一个模版段 → 多个草稿段（与抠像人像/主轨同素材同入点，只差“模糊”）。
    # 写段循环仍按“一模版段一段”组织，所以这里先把带 `pieces` 的段展开成“克隆段”，
    # 每个克隆段在 plan 里有自己的 start/dur/src_in/asset；几何/特效/关键帧沿用原段。
    segments_for_write: List[dict] = []
    for seg in segments:
        p0 = plan.get(seg["key"])
        pieces = (p0 or {}).get("pieces")
        if not pieces:
            segments_for_write.append(seg)
            continue
        for i, pc in enumerate(pieces):
            key_i = seg["key"] if i == 0 else f"{seg['key']}#piece{i}"
            clone = dict(seg)
            clone["key"] = key_i
            plan[key_i] = {**p0, "start": pc["start"], "dur": pc["dur"],
                           "src_in": pc["src_in"], "asset": pc["asset"], "pieces": None}
            segments_for_write.append(clone)

    for seg in sorted(segments_for_write, key=lambda s: (s["track_index"], s.get("start_sec") or 0.0)):
        p = plan.get(seg["key"])
        if not p:
            continue
        t_i = seg["track_index"]
        t_role = next((t.get("role") for t in tracks if t["index"] == t_i), None)
        if t_role == "effect":
            continue
        geometry = dict(seg.get("geometry") or {})
        extra_refs: List[str] = []
        src_in = float(p.get("src_in") or 0.0)
        src_dur = p["dur"]
        mat_id = None
        is_audio = False

        if p["kind"] in ("video", "photo") and p["asset"]:
            fill = p["asset"]
            key = fill["path"]
            sub = "video"
            name = stage(key, sub)
            crop_use = usable_crop(seg, fill, canvas_w, canvas_h, rep)
            if not crop_use and p["kind"] == "photo":
                # 模版没带 crop：照片原版是按**图片自身像素**定尺寸的，新图的宽高比不一样，
                # 换成画布 contain 后覆盖面积会差好几倍（style_16 seg:1:1 原版 cover 1.006，
                # 新图未裁 3.17 = 三倍画面；那张「照片竖条」于是变成盖满全屏的图）。
                # 按原版图层的展示宽高比居中裁一刀（与复合片段子层的同一口径）。
                sr = (geometry or {}).get("size_ratio")
                want = None
                if sr and len(sr) == 2 and float(sr[1] or 0) > 0:
                    want = (float(sr[0]) * canvas_w) / max(1e-6, float(sr[1]) * canvas_h)
                crop_use = _aspect_crop(fill, want)
                if crop_use:
                    rep["photo_layer_aspect_cropped"] = \
                        rep.get("photo_layer_aspect_cropped", 0) + 1
            # 抠像层要一份**自己的**素材条目（带 matting 结构）：和主轨用同一个文件，但素材
            # 的 matting 是逐素材的，共用一份会把主轨也变成抠像层。
            mat_block = _matting_block(p.get("matting"), root_uuid) if p.get("cutout") else None
            mk = f"{key}|{json.dumps(crop_use or {}, sort_keys=True)}"
            if mat_block:
                mk += "|cutout:" + json.dumps(mat_block, sort_keys=True)
            if mk not in video_mats:
                video_mats[mk] = _video_material(
                    name, root_uuid, f"materials/{sub}", fill["w"], fill["h"],
                    float(max(fill["take"], 0.1)), crop_use,
                    is_photo=(p["kind"] == "photo"), matting=mat_block)
                video_mats[mk]["_path"] = key
            mat_id = video_mats[mk]["id"]
        elif p["kind"] == "sticker" and p["asset"]:
            fill = p["asset"]
            name = stage(fill["path"], "sticker")
            key = fill["path"]
            if key not in video_mats:
                video_mats[key] = {
                    "id": _uid(), "path": f"##_draftpath_placeholder_{root_uuid}_##/materials/sticker/{name}",
                    "type": "sticker", "material_name": name, "_path": key,
                }
            mat_id = video_mats[key]["id"]
        elif p["kind"] == "audio" and p["asset"]:
            fill = p["asset"]
            name = stage(fill["path"], "audio")
            key = fill["path"]
            if key not in audio_mats:
                audio_mats[key] = _audio_material(
                    name, root_uuid, "materials/audio", float(fill["take"]),
                    "music" if p["role"] == "bgm" else "sound")
                audio_mats[key]["_path"] = key
            mat_id = audio_mats[key]["id"]
            is_audio = True
        elif p["kind"] == "compound":
            # 复合片段（内嵌子时间线）：父段引用一个**无 path 的占位素材**，
            # 真身是同一条段 `extra_material_refs` 里的 drafts 条目（剪映的真实结构）。
            # source_timerange 从 0 起、长度 = 父段新时长：子时间线已按父段时长重映射过。
            mat_id = p.get("mat_id")
            if not mat_id:
                # 走到这里说明这条复合片段被别的通道先占了 plan（例如被判成背景板切块），
                # 结构已经丢了：如实计一次 skip，不要用无主素材写出坏段。
                _skip_compound(seg["key"], "compound_plan_overwritten")
                continue
            extra_refs.append(p["compound_ref"])
            src_in = 0.0
            src_dur = p["dur"]
            is_audio = bool(p.get("is_audio"))
            ph = p.get("placeholder") or {}
            if ph:
                (ph_audio_mats if is_audio else ph_video_mats).append(ph)
        elif p["kind"] == "text":
            si = seg["text_slot"]
            if si not in slot_text:
                continue
            plan_txt = slot_text[si]
            style = (text_slots[si].get("style") or {})
            if si not in text_mats:
                text_mats[si] = _text_style_material(plan_txt, style, slot_font.get(si))
            mat_id = text_mats[si]["id"]
            texts_used += 1
            src_dur = 0.0
            src_in = 0.0
        else:
            continue

        holder, _items = _anim_holder(seg.get("animations") or [], p["dur"], rep)
        if holder:
            anim_mats.append(holder)
            extra_refs.append(holder["id"])
        if not is_audio:
            spd = _speed_material()
            speeds.append(spd)
            extra_refs.append(spd["id"])
        if seg.get("mask"):
            mcfg = dict(seg["mask"].get("config") or {})
            mcfg["resource_type"] = str(seg["mask"].get("resource_type") or "")
            mask_mat = {"category": "video", "config": seg["mask"].get("config") or {},
                        "id": _uid(), "name": seg["mask"].get("name"),
                        "path": "", "resource_type": mcfg["resource_type"],
                        "text_config": {"align_type": 15}, "type": "mask"}
            mask_mats.append(mask_mat)
            extra_refs.append(mask_mat["id"])
        if seg.get("effects"):
            for fx in seg["effects"]:
                fx_mat = {"adjust_params": fx.get("adjust_params") or [],
                          "category_id": "100000", "category_name": "画面特效",
                          "effect_id": fx.get("effect_id"), "id": _uid(),
                          "name": fx.get("name"), "path": "",
                          "render_index": fx.get("render_index") or 11000,
                          "resource_id": "", "type": "video_effect", "value": 1.0}
                vfx_mats.append(fx_mat)
                extra_refs.append(fx_mat["id"])

        kfs = _keyframes_of(seg, p["dur"], src_in)
        new_seg = _segment(
            mat_id=mat_id, tl_start=p["start"], dur=p["dur"], src_in=src_in,
            src_dur=src_dur if p["kind"] not in ("text",) else 0.0,
            geometry=geometry, keyframes=kfs, extra_refs=extra_refs,
            track_index=t_i, volume=1.0, render_index=seg.get("render_index"))
        track_segs[t_i].append(new_seg)
        segment_map.append({"seg_id": new_seg["id"], "key": seg["key"],
                            "track_index": t_i, "start": round(p["start"], 3),
                            "dur": round(p["dur"], 3)})

    rep["segment_map"] = segment_map
    # 彻底丢弃的抠像层必须真的不在成片上：写段循环只写 plan，这里如实对账（应为 0）。
    written_keys = {row["key"] for row in segment_map}
    rep["cutout_dropped_still_rendered"] = sum(1 for k in dropped_cutout_keys if k in written_keys)

    # ---- 写 tracks：**按原版 render_index 升序**（不是模版数组序）----
    # 原来什么：`for tr in tracks:` —— 照模版数组序写，于是拼贴（数组第 2 条）写在缩图层（数组第 6 条）
    # **前面**，而本项目渲染器「轨序即 z 序」→ 拼贴被盖住（条带只看得见几条的真因）。
    # 改成什么：先按原版 render_index 升序排序（同值稳定；音频轨留末尾），再按这个顺序写 tracks，
    # 写完后把每条轨的 `track_render_index` 回填成输出位置（轨序 / render_index 两口径自洽）。
    tracks_ordered = _z_sort_tracks(tracks)
    emitted_tracks: List[dict] = []
    for tr in tracks_ordered:
        segs = track_segs.get(tr["index"]) or []
        if not segs or tr.get("role") == "effect":
            continue
        out_tracks.append({
            "attribute": 0,
            "flag": int(tr.get("flag") or 0),
            "id": _uid(),
            "is_default_name": True,
            "name": "",
            "segments": segs,
            "type": TRACK_TYPE_OF_ROLE.get(tr.get("role"), tr.get("track_type") or "video"),
        })
        emitted_tracks.append(tr)
    _z_apply_render_index(out_tracks)
    top_order_stats = _z_order_stats("top", tracks, emitted_tracks, segments)

    videos = [v for v in video_mats.values() if v.get("type") != "sticker"] + ph_video_mats
    stickers = [{"category_id": "", "category_name": "", "id": v["id"], "material_name": v["material_name"],
                 "path": v["path"], "resource_id": "", "type": "sticker", "source_platform": 0,
                 "preview_cover": "", "extra_info": ""}
                for v in video_mats.values() if v.get("type") == "sticker"]

    now_us = int(time.time() * US)
    draft_id = _uid()
    content = {
        "canvas_config": {"height": canvas_h, "ratio": "original", "width": canvas_w},
        "color_space": 0,
        "config": {
            "adjust_max_index": 1,
            "attachment_info": [],
            "combination_max_index": 1,
            "export_range": None,
            "extract_audio_last_index": len(audio_mats),
            "lyrics_recognition_id": "",
            "lyrics_sync": True,
            "lyrics_taskinfo": [],
            "maintrack_adsorb": True,
            "material_save_mode": 0,
            "multi_language_current": "none",
            "multi_language_list": [],
            "multi_language_main": "none",
            "multi_language_mode": "none",
            "original_sound_last_index": 1,
            "record_audio_last_index": 1,
            "sticker_max_index": max(1, len(stickers)),
            "subtitle_keywords_config": None,
            "subtitle_recognition_id": "",
            "subtitle_sync": True,
            "subtitle_taskinfo": [],
            "system_font_list": [],
            "video_mute": False,
            "zoom_info_params": None,
        },
        "duration": _us(new_total),
        "extra_info": None,
        "fps": fps,
        "function_assistant_info": {"fps": {}},
        "free_render_index_mode_on": False,
        "group_container": None,
        "id": draft_id,
        "is_minimal_draft": True,
        "keyframe_graph_list": [],
        "keyframes": {"adjusts": [], "audios": [], "effects": [], "filters": [], "handwrites": [],
                      "stickers": [], "texts": [], "videos": []},
        "last_modified_platform": {"app_id": 3704, "app_source": "lv", "app_version": "11.8.0",
                                   "os": "mac"},
        "materials": {
            "audios": list(audio_mats.values()) + ph_audio_mats,
            "beats": [],
            "canvases": [],
            "common_mask": mask_mats,
            "drafts": drafts_mats,
            "material_animations": anim_mats,
            "material_colors": [],
            "placeholder_infos": [],
            "sound_channel_mappings": [],
            "speeds": speeds,
            "stickers": stickers,
            "texts": list(text_mats.values()),
            "transitions": [],
            "video_effects": vfx_mats,
            "videos": videos,
            "vocal_separations": [],
        },
        "mutable_config": None,
        "name": "replica_from_template",
        "new_version": "125.0.0",
        "path": "",
        "platform": {"app_id": 3704, "app_source": "lv", "app_version": "11.8.0", "os": "mac"},
        "relationships": [],
        "render_index_track_mode_on": True,
        "retouch_cover": None,
        "smart_ads_info": {},
        "source": "default",
        "static_cover_image_path": "",
        "time_marks": None,
        "tracks": out_tracks,
        "uneven_animation_template_info": {"composition": "none", "content": "",
                                          "order": "none", "sub_template_id": ""},
        "update_time": now_us,
        "version": 360000,
    }

    draft_json = draft_dir / "draft_content.json"
    draft_json.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
    meta = {
        "cloud_draft_cover": False,
        "cloud_draft_sync": False,
        "draft_cover": "draft_cover.jpg",
        "draft_deeplink_url": "",
        "draft_fold_path": str(draft_dir).replace("\\", "/"),
        "draft_id": draft_id,
        "draft_is_ae_produce": False,
        "draft_is_invisible": False,
        "draft_materials": [{"type": 0, "value": [
            {"duration": m["duration"], "extra_info": m.get("material_name"),
             "file_Path": "./" + str(m.get("path", "")).split("_##/")[-1],
             "height": m.get("height", canvas_h), "id": m.get("local_material_id"), "metetype": "video",
             "roughcut_time_range": {"duration": -1, "start": -1},
             "sub_time_range": {"duration": -1, "start": -1}, "type": 0,
             "width": m.get("width", canvas_w)}
            for m in videos]}],
        "draft_name": "replica_from_template",
        "draft_removable_storage_device": "",
        "draft_root_path": str(draft_dir.parent).replace("\\", "/"),
        "draft_timeline_materials_size_": 0,
        "draft_type": "",
        "tm_draft_create": now_us,
        "tm_draft_modified": now_us,
        "tm_duration": int(content["duration"]),
    }
    (draft_dir / "draft_meta_info.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---- 视频叠层对账：最终草稿里时间重叠的每一对，素材是否同源 ----
    # 模版原版就允许「同一素材推近/缩窗」这种叠层（same_material_as），所以这里不要求
    # 同源对数 = 0，而是要求「同源对」全部是**模版明说的重构**（有小窗/遮罩 = 有 alpha），
    # 或者数量为 0。叠层同源的自然结果另行在报告里逐对罗列。
    vid_items = [(k, p) for k, p in plan.items()
                 if p.get("kind") in ("video", "photo") and p.get("asset")]
    vid_items.sort(key=lambda kv: (kv[1]["start"], kv[1].get("track_index") or 0))
    overlap_pairs: List[dict] = []
    for i in range(len(vid_items)):
        ka, pa = vid_items[i]
        for j in range(i + 1, len(vid_items)):
            kb, pb = vid_items[j]
            a0, a1 = pa["start"], pa["start"] + pa["dur"]
            b0, b1 = pb["start"], pb["start"] + pb["dur"]
            ov = min(a1, b1) - max(a0, b0)
            if ov <= 0.02:
                continue
            overlap_pairs.append({
                "a": ka, "b": kb,
                "a_track": pa.get("track_index"), "b_track": pb.get("track_index"),
                "overlap_start": round(max(a0, b0), 3),
                "overlap_end": round(min(a1, b1), 3),
                "overlap_mid": round((max(a0, b0) + min(a1, b1)) / 2.0, 3),
                "overlap_sec": round(ov, 3),
                "same_asset": pa["asset"]["path"] == pb["asset"]["path"],
                "designed_same_source": bool(pa.get("same_material_as") == kb
                                             or pb.get("same_material_as") == ka),
            })
    rep["video_overlap_pairs"] = overlap_pairs
    rep["video_overlap_same_asset"] = sum(1 for p in overlap_pairs if p["same_asset"])

    # ---- 模版层逐对记账：模版里每一对时间重叠的视频段，在新片里到底怎么了 ----
    # 为什么还要这一份：最终草稿里可能一对都不剩（叠层全被拒接），那时只看「最终重叠对」
    # 会变成 0 对，读报告的人无法知道「每一对到底去哪了」。这里按**模版结构**逐对列，
    # 给出映射到新时间轴后的区间、处置与原因。
    blank_by_key = {row["key"]: row["why"] for row in rep["overlay_left_blank_detail"]}
    tpl_vid: List[dict] = []
    for seg in segments:
        if seg.get("kind") not in ("video", "photo"):
            continue
        o0 = float(seg.get("start_sec") or 0.0)
        od = float(seg.get("duration_sec") or 0.0)
        p = plan.get(seg["key"])
        n0 = p["start"] if p else map_start(o0)
        nd = p["dur"] if p else max(0.05, od * scale_at(o0))
        tpl_vid.append({"key": seg["key"], "track": seg["track_index"],
                        "start": n0, "dur": nd, "planned": bool(p),
                        "asset_ref": _asset_ref(p["asset"]["path"]) if p and p.get("asset") else None})
    resolved: List[dict] = []
    for i in range(len(tpl_vid)):
        a = tpl_vid[i]
        for j in range(i + 1, len(tpl_vid)):
            b = tpl_vid[j]
            ov = min(a["start"] + a["dur"], b["start"] + b["dur"]) - max(a["start"], b["start"])
            if ov <= 0.02:
                continue
            skipped = [x["key"] for x in (a, b) if not x["planned"]]
            if skipped:
                res = "skipped:" + (blank_by_key.get(skipped[0]) or "unknown")
                if len(skipped) == 2 and blank_by_key.get(skipped[0]) != blank_by_key.get(skipped[1]):
                    res = "skipped:" + "+".join(blank_by_key.get(k) or "unknown" for k in skipped)
            elif a["asset_ref"] == b["asset_ref"]:
                res = "kept_same_source"
            else:
                res = "kept_distinct_assets"
            resolved.append({"a": a["key"], "b": b["key"],
                             "a_track": a["track"], "b_track": b["track"],
                             "overlap_start": round(max(a["start"], b["start"]), 3),
                             "overlap_end": round(min(a["start"] + a["dur"], b["start"] + b["dur"]), 3),
                             "overlap_mid": round((max(a["start"], b["start"])
                                                  + min(a["start"] + a["dur"], b["start"] + b["dur"])) / 2.0, 3),
                             "resolution": res,
                             "a_asset_ref": a["asset_ref"], "b_asset_ref": b["asset_ref"]})
    rep["overlay_pairs_resolved"] = resolved
    rep["asset_reuse_by_slot"] = [
        {"key": k, "track_index": p.get("track_index"), "role": p.get("role"),
         "start": round(p["start"], 3), "duration": round(p["dur"], 3),
         "src_in": round(float(p.get("src_in") or 0.0), 3),
         "asset_ref": _asset_ref(p["asset"]["path"]),
         "used_role": p["asset"].get("used_role"),
         "reused": bool(p.get("reused_asset")),
         "conflict_skipped": int(p.get("conflict_skipped") or 0),
         "linked_to": p.get("same_material_as")}
        for k, p in vid_items]

    # ---- 4. 报告 ----
    by_role: Dict[str, int] = {}
    for tr in tracks:
        segs = track_segs.get(tr["index"]) or []
        if not segs:
            continue
        by_role[tr.get("role")] = by_role.get(tr.get("role"), 0) + len(segs)
    rep.update({
        "out_draft": str(draft_json),
        "duration_sec": round(new_total, 4),
        "template_duration_sec": round(total_old, 4),
        "duration_delta_sec": round(new_total - total_old, 4),
        "segments_total": sum(len(t["segments"]) for t in out_tracks),
        "segments_by_role": by_role,
        "tracks_out": [{"role": next((t.get("role") for t in tracks if t["index"] == idx),
                                    None), "segments": len(s)} for idx, s in
                       ((tr["index"], track_segs[tr["index"]]) for tr in tracks_ordered
                        if track_segs.get(tr["index"]) and tr.get("role") != "effect")],
        # z 序迁移台账（这一轮的核心）：重排幅度 / 残留错位 / 每个复合片段子稿的排序结果
        "tracks_reordered": top_order_stats["tracks_reordered"],
        "z_order_mismatch": top_order_stats["z_order_mismatch"] + sum(
            s["z_order_mismatch"] for s in sub_order_stats),
        "z_order_inverted_pairs": top_order_stats["z_order_inverted_pairs"] + sum(
            s["z_order_inverted_pairs"] for s in sub_order_stats),
        "track_order": {
            "top": top_order_stats,
            "compounds": sub_order_stats,
            "tracks_reordered_total": top_order_stats["tracks_reordered"] + sum(
                s["tracks_reordered"] for s in sub_order_stats),
            "note": ("轨序即 z 序：按原版 render_index 升序写 tracks（同值稳定、音频轨留末尾）；"
                     "tracks_reordered = 位置变了的轨数（重排幅度），z_order_mismatch = 排序后仍有残留"
                     "错位的轨数，z_order_inverted_pairs = 重叠层对里先后仍与原版相反的对数，后两个正确时应为 0"),
        },
        "texts_used": texts_used,
        "texts_truncated": rep.get("texts_truncated", 0),
        "crop_uv_dropped": rep.get("crop_uv_dropped", 0),
        "crop_uv_dropped_detail": rep.get("crop_uv_dropped_detail", []),
        "overlay_left_blank": rep.get("overlay_left_blank", 0),
        "overlay_left_blank_detail": rep.get("overlay_left_blank_detail", []),
        # 抠像三明治重建台账（这一轮的核心）：重建了几层 / 跳过了几层（分原因）/ 背景板 / 描边
        "cutout_layers_rebuilt": rep.get("cutout_layers_rebuilt", 0),
        "cutout_rebuilt_detail": rep.get("cutout_rebuilt_detail", []),
        "cutout_layers_skipped": rep.get("cutout_layers_skipped", {}),
        "backdrop_rebuilt": rep.get("backdrop_rebuilt", 0),
        "backdrop_rebuilt_detail": rep.get("backdrop_rebuilt_detail", []),
        "backdrop_skipped": rep.get("backdrop_skipped", {}),
        "stroke_applied": rep.get("stroke_applied", 0),
        "ghost_rejected": rep.get("ghost_rejected", 0),
        "overlap_conflicts_avoided": rep.get("overlap_conflicts_avoided", 0),
        "asset_reuse_by_slot": rep.get("asset_reuse_by_slot", []),
        "video_overlap_pairs": rep.get("video_overlap_pairs", []),
        "video_overlap_same_asset": rep.get("video_overlap_same_asset", 0),
        "overlay_pairs_resolved": rep.get("overlay_pairs_resolved", []),
        "content_linked": rep.get("content_linked", 0),
        # 复合片段重建台账（这一轮的核心）：
        #   compounds_extracted：模版里拆出子结构的复合片段数（提取端口径，随模版传入）
        #   compounds_applied  ：真写回 materials.drafts[*].draft + subdraft/ 的几个
        #   compound_skipped   ：按原因分桶（含子层逐层缺素材的细分）
        #   sub_segments_total ：真正落到子时间线里的段数
        #   photo_layers_mapped：子时间线的照片层里换到**图片**的层数
        "compounds_extracted": (template.get("counts") or {}).get("compounds_extracted", 0),
        "compounds_applied": rep.get("compounds_applied", 0),
        "compounds_skipped": rep.get("compound_skipped", {}),
        "compound_applied_detail": rep.get("compound_applied_detail", []),
        "sub_segments_total": rep.get("sub_segments_total", 0),
        "photo_layers_mapped": rep.get("photo_layers_mapped", 0),
        # 照片全局唯一台账（跨复合片段/跨层级的同一本账）：
        #   duplicate_photo_blocked：本来要分到、但那张图已经在时间线别处用过 → 改派/留空的次数
        #   duplicate_photo_allowed：原版这两层就是同一张图（material_fingerprint 相同）→ 显式复用
        #   photo_layers_unfilled  ：池子里的图全用过了 → 只能留空的照片层数
        #   photo_assets_distinct  ：实际用到几张不同的图
        "duplicate_photo_blocked": rep.get("duplicate_photo_blocked", 0),
        "duplicate_photo_blocked_detail": rep.get("duplicate_photo_blocked_detail", []),
        "duplicate_photo_allowed": rep.get("duplicate_photo_allowed", 0),
        "photo_allocations": photo_alloc,
        "photo_layers_unfilled": rep.get("photo_layers_unfilled", 0),
        "photo_assets_distinct": len(photo_used),
        "photo_pool_size": len((pools.get("photo").items if pools.get("photo") else [])),
        "compound_photo_layers_in_template": (template.get("counts") or {}).get(
            "compound_photo_layers", 0),
        "compound_anim_clamped": rep.get("compound_anim_clamped", 0),
        "compound_time_scaled": rep.get("compound_time_scaled", 0),
        "compound_length_mismatch": rep.get("compound_length_mismatch", 0),
        "compound_depth_exceeded": (template.get("counts") or {}).get(
            "compound_depth_exceeded", 0),
        "compound_canvas_refs_dropped": rep.get("compound_canvas_refs_dropped", 0),
        "text_layout": text_layout_report,
        "font_resolved": font_resolved,
        "font_fallback_slots": font_fallback_slots,
        "anchors": [{"old_start": round(o0, 4), "old_end": round(o1, 4),
                     "new_start": round(n0, 4), "new_end": round(n1, 4)}
                    for (o0, o1), (n0, n1) in zip(old_b, new_b)],
    })
    return draft_json


def main() -> int:
    ap = argparse.ArgumentParser(description="把复刻模版套到新素材/新文案上，产出原生草稿")
    ap.add_argument("--template", required=True, help="模版 JSON")
    ap.add_argument("--assets", help="素材 JSON：{角色: [路径...]}")
    ap.add_argument("--texts", help="文案 JSON（字符串数组 / {\"slot\":i,\"text\":\"...\"} 对象数组）或 UTF-8 文本（每行一条）")
    ap.add_argument("--out", required=True, help="输出草稿目录（或 draft_content.json 路径）")
    ap.add_argument("--style-id", default=None, help="风格 id（字体优先在该风格的字体资产里找）")
    args = ap.parse_args()

    tpl = json.loads(Path(args.template).read_text(encoding="utf-8"))
    assets = json.loads(Path(args.assets).read_text(encoding="utf-8")) if args.assets else {}
    texts: List[Any] = []
    if args.texts:
        raw = Path(args.texts).read_text(encoding="utf-8")
        texts = json.loads(raw) if raw.lstrip().startswith("[") else [x for x in raw.splitlines() if x.strip()]
    rep: dict = {}
    out = apply_template(tpl, assets, texts, Path(args.out), rep, style_id=args.style_id)
    print(json.dumps({k: rep.get(k) for k in ("out_draft", "duration_sec", "segments_total",
                                              "segments_by_role", "asset_reused", "asset_wrapped",
                                              "slot_shrunk", "anim_clamped", "skipped",
                                              "texts_truncated", "font_resolved",
                                              "font_fallback_slots", "overlay_left_blank",
                                              "ghost_rejected", "overlap_conflicts_avoided",
                                              "video_overlap_same_asset")},
                     ensure_ascii=False, indent=2))
    print(f"[apply-template] -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
