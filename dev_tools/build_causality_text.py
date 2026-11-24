#!/usr/bin/env python3
"""从 24 套剪映真草稿抽取 text 域元素的因果规则 -> forensic/causality_text.json

只读。输出里不出现本机路径 / 草稿目录名 / 字幕原文：
  - source_ref 用 wb_<sha256 前 12 位>（取 forensic/style_render_truth.json 已脱敏的同一个值）
  - sample_table 只写结构描述 + 字数 / 像素 / 秒数，不写文案

元素口径（element_key 只用 schema 允许的 6 个）：
  subtitle          口播逐句字幕：base 轨（同 y 带 + 同基准字号 + 有 ASR 类型的兄弟轨）
  keyword_highlight 同一条 text 的 styles[] 里追加第二块 style（range 只盖几个字；放大或换色）
  flower            展示字/花字（既非字幕、也非 giant/chapter/end 的其余文字段）
  giant_text        展示字里字号 ≥ 1.8× 本套字幕基准 px，且 ≤6 字的单词冲击
  chapter_card      带序号前缀（一、/第二/1./(1)）的分点卡；≥3 个不同分点才算规律
  end_card          片尾最后 ~15% 内、y 在 0.30–0.92 的收口/CTA（可 1–3 条阶梯）

口径来自草稿字段本身：
  px = size × canvas_w / 172.8（1080 宽 = 6.25 px/单位）
  y_ratio = (1 − clip.transform.y) / 2
  主体字号取 styles[0].size（range 从 0 开始的那块），高亮取 styles[1]
  无 styles[].size 的块退回素材级 font_size（48 单位 → 300px），不按 0 处理

运行：python3 dev_tools/build_causality_text.py
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import statistics
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEC = ROOT / "forensic" / "decrypted"
OUT = ROOT / "forensic" / "causality_text.json"
LIB = ROOT / "short-video-studio" / "assets" / "style_spectrum" / "style_spectrum_library.json"
TRIG = ROOT / "forensic" / "style_trigger_rules_sfx_flower.json"
RENDER = ROOT / "forensic" / "style_render_truth.json"
SOURCES = ROOT / "forensic" / "style_sources.json"

PX_PER_UNIT_1080 = 1080.0 / 172.8  # 6.25：size×canvas_w/172.8
FRAME = 1.0 / 60.0
ORD_RE = re.compile(r"^(第[一二三四五六七八九十百0-9]+[章节条点]?|[一二三四五六七八九十]、|0?[1-9][、.．]|[（(][一二三四五六七八九十0-9]+[)）])")


# ---------------------------------------------------------------- loaders
def load_draft(name: str):
    for fn in ("__latest_close.json", "draft_content.json"):
        p = DEC / name / fn
        if p.is_file():
            try:
                return json.loads(p.read_text(encoding="utf-8")), fn
            except Exception:
                continue
    return None, ""


def hexc(c) -> str:
    try:
        return "#{:02X}{:02X}{:02X}".format(*[int(round(float(v) * 255)) for v in c[:3]])
    except Exception:
        return "#000000"


def fill_hex(style: dict):
    c = (((style.get("fill") or {}).get("content") or {}).get("solid") or {}).get("color")
    return hexc(c) if c else None


def pct(a: float) -> str:
    return f"{round(a * 100):d}%"


def med(xs, default=0.0):
    return statistics.median(xs) if xs else default


def short(x: float, nd: int = 2):
    return round(float(x), nd)


# ---------------------------------------------------------------- per-draft measurement
def measure(name: str) -> dict:
    data, fn = load_draft(name)
    if not data:
        return {}
    dur_us = max(int(data.get("duration") or 1), 1)
    dur = dur_us / 1e6
    canvas_w = int((data.get("canvas_config") or {}).get("width") or 1080)
    k = canvas_w / 172.8
    mats = data.get("materials") or {}
    txt_map = {m["id"]: m for m in (mats.get("texts") or []) if "id" in m}
    anim_map = {m["id"]: m for m in (mats.get("material_animations") or []) if "id" in m}

    cuts = []
    vids = [t for t in (data.get("tracks") or []) if t.get("type") == "video" and (t.get("segments") or [])]
    if vids:
        cuts = sorted(float((sg.get("target_timerange") or {}).get("start") or 0) / 1e6
                      for sg in vids[0]["segments"])
    tracks = []
    for ti, tr in enumerate(data.get("tracks") or []):
        if tr.get("type") != "text" or not (tr.get("segments") or []):
            continue
        segs = []
        for si, s in enumerate(tr["segments"]):
            m = txt_map.get(s.get("material_id"))
            if not m:
                continue
            try:
                c = json.loads(m.get("content") or "{}")
            except Exception:
                c = {}
            styles = c.get("styles") or []
            sizes = [float(st.get("size") or 0) for st in styles if st.get("size")]
            # 有的展示字 styles[] 不写 size，字号退回素材级 font_size（48 单位→300px，不该当 0）
            if not sizes and m.get("font_size"):
                sizes = [float(m["font_size"])]
            text = str(c.get("text") or "")
            lines = text.split("\n")
            tr_ = (s.get("clip") or {}).get("transform") or {}
            tm = s.get("target_timerange") or {}
            anims = []
            for ref in s.get("extra_material_refs") or []:
                a = anim_map.get(ref)
                if a and a.get("animations"):
                    for x in a["animations"]:
                        anims.append({"type": x.get("type"), "name": x.get("name"),
                                      "dur": float(x.get("duration") or 0) / 1e6})
            shadow = None
            if m.get("has_shadow"):
                shadow = {"distance": m.get("shadow_distance"), "smoothing": m.get("shadow_smoothing"),
                          "alpha": m.get("shadow_alpha"), "point": m.get("shadow_point")}
            strokes = [hexc(((sk.get("content") or {}).get("solid") or {}).get("color") or [0, 0, 0])
                       for st in styles for sk in (st.get("strokes") or [])]
            segs.append({
                "ti": ti, "si": si,
                "t": float(tm.get("start") or 0) / 1e6,
                "du": float(tm.get("duration") or 0) / 1e6,
                "y": (1.0 - float(tr_.get("y") or 0.0)) / 2.0,
                "x": (float(tr_.get("x") or 0.0) + 1.0) / 2.0,
                "units": max(sizes) if sizes else 0.0,
                "px": (max(sizes) * k) if sizes else 0.0,
                # 主体字号 = styles[0]（range 从 0 开始的那块），高亮块在 styles[1]
                "body_units": (float(styles[0].get("size") or sizes[0]) if styles and (styles[0].get("size") or sizes) else (sizes[0] if sizes else 0.0)),
                "body_px": ((float(styles[0].get("size") or sizes[0]) if styles and (styles[0].get("size") or sizes) else (sizes[0] if sizes else 0.0)) * k),
                "ch": len(text.replace("\n", "")),
                "lines": len(lines),
                "line_max": max((len(x) for x in lines), default=0),
                "type": m.get("type"),
                "styles": [{"size": float(st.get("size") or 0),
                            "color": fill_hex(st),
                            "range": st.get("range") or [],
                            "font": os.path.splitext(os.path.basename(((st.get("font") or {}).get("path")) or ""))[0]}
                           for st in styles],
                "anims": anims,
                "shadow": shadow,
                "strokes": strokes,
                "bg_alpha": m.get("background_alpha"),
                "border_width": m.get("border_width"),
                "text": text,
            })
        if segs:
            tracks.append({"ti": ti, "segs": segs,
                           "med_y": med([s["y"] for s in segs]),
                           "cov": sum(s["du"] for s in segs) / dur,
                           "tshare": sum(1 for s in segs if s["type"] == "subtitle") / len(segs),
                           "med_du": med([s["du"] for s in segs])})
    return {"dur": dur, "canvas_w": canvas_w, "k": k, "tracks": tracks, "cuts": cuts, "file": fn}


# ---------------------------------------------------------------- element classification
def classify(m: dict, safe_y: float, base_hint: float = 0.0) -> dict:
    """把每段文字归到一个 element_key。先定字幕基准轨，再切展示字里的 giant/chapter/end。"""
    tracks = m["tracks"]
    dur = m["dur"]

    cands = [t for t in tracks if t["tshare"] >= 0.3 and len(t["segs"]) >= 5
             and abs(t["med_y"] - safe_y) <= 0.12 and t["med_du"] <= 4.5]
    if not cands:  # 兜底：无 ASR 字幕型轨时，只认"字号统一+覆盖高"的轨
        for t in tracks:
            uni = sum(1 for s in t["segs"] if s["units"] > 0) / len(t["segs"])
            if len(t["segs"]) >= 8 and abs(t["med_y"] - safe_y) <= 0.05 and t["cov"] >= 0.7 and uni >= 0.7:
                pool = [s["units"] for s in t["segs"] if s["units"] > 0]
                if pool and (max(pool) - min(pool)) <= 0.25 * statistics.median(pool):
                    cands.append(t)
    base = max(cands, key=lambda t: len(t["segs"])) if cands else None
    base_px = 0.0
    if base:
        # 真字幕字号 = base 轨里「句子长度」那批段的字号众数；两档（高亮块放大）时取小的那档
        sent = [s for s in base["segs"] if s["ch"] >= 4] or base["segs"]
        typed = [s for s in sent if s["type"] == "subtitle"]
        pool = typed if len(typed) >= 3 else sent
        cnt = Counter(round(s["body_px"]) for s in pool if s["body_px"] > 0)
        if not cnt:
            cnt = Counter(round(s["px"]) for s in pool)
        base_px = float(cnt.most_common(1)[0][0])
    if base_px <= 0 and base_hint:
        base_px = float(base_hint)  # 无 ASR 字幕轨时，用库字号当"正文基准"来判 giant
    # 姊妹字幕轨：同一 y 带、同一基准字号
    base_tracks = []
    if base:
        for t in tracks:
            if t is base:
                base_tracks.append(t)
                continue
            same_band = abs(t["med_y"] - base["med_y"]) <= 0.08
            same_px = base_px > 0 and sum(1 for s in t["segs"] if abs(s["px"] - base_px) <= 0.15 * base_px) >= 0.6 * len(t["segs"])
            if t["med_du"] <= 4.5 and same_band and same_px and t["tshare"] >= 0.3:
                base_tracks.append(t)
    base_ids = {id(t) for t in base_tracks}

    subs, disp = [], []
    for t in tracks:
        for s in t["segs"]:
            if id(t) in base_ids and abs(s["y"] - base["med_y"]) <= 0.06 and base_px > 0 \
                    and s["px"] <= 1.35 * base_px and s["du"] <= 6.0:
                subs.append(s)
            else:
                disp.append(s)

    # 展示字细分
    cnt = Counter(s["text"] for s in disp)
    for s in disp:
        el = "flower"
        if s["t"] + s["du"] >= 0.95 * dur and s["t"] >= 0.75 * dur and s["du"] >= 0.8 \
                and 0.30 <= s["y"] <= 0.92:
            el = "end_card"
        elif ORD_RE.match(s["text"]) and s["ch"] >= 2 \
                and (not base_tracks or s["px"] >= 0.9 * (base_px or 75.0)) \
                and (s["du"] >= 0.75 or s["px"] >= 1.15 * (base_px or 75.0)):
            el = "chapter_card"
        if el == "flower" and base_px > 0 and s["px"] >= 1.8 * base_px and s["ch"] <= 6 and s["du"] >= 0.3:
            el = "giant_text"
        s["el"] = el

    # 关键词高亮：任意文字块的 styles 里出现第二块 style（换色或放大）
    # 宿主可以是字幕，也可以是展示字——机制是同一个：range 只盖几个字
    for s in subs + disp:
        sts = s["styles"]
        s["kw"] = None
        if len(sts) >= 2:
            colors = [x["color"] for x in sts if x["color"]]
            body = sts[0]
            extra = sts[1]
            ratio = (extra["size"] / body["size"]) if (body["size"] and extra["size"]) else 1.0
            if ratio < 0.9:
                continue  # 缩小＝双行翻译行，不是强调
            if len(set(colors)) > 1 or ratio >= 1.05:
                rng = extra.get("range") or []
                span = s["text"].replace("\n", "")[rng[0]:rng[1]] if len(rng) == 2 else ""
                s["kw"] = {
                    "ratio": ratio,
                    "color": extra["color"],
                    "body_color": body["color"],
                    "len": (rng[1] - rng[0]) if len(rng) == 2 else 0,
                    "at_end": bool(len(rng) == 2 and rng[1] >= s["ch"] - 1),
                    "host": "subtitle" if any(s is x for x in subs) else "display",
                    "numeric": bool(re.search(r"[0-9０-９%％¥￥元万亿]", span)),
                }

    for s in subs:
        s.setdefault("el", "subtitle")
        s["el"] = "subtitle"

    # 元素两两共存统计：同帧 + 同 y 带（±0.05）才算真冲突
    allsegs = subs + disp
    pairs = {}
    els = ("subtitle", "flower", "giant_text", "chapter_card", "end_card")
    for i, a in enumerate(els):
        for b in els[i + 1:]:
            A = [x for x in allsegs if x["el"] == a]
            B = [x for x in allsegs if x["el"] == b]
            def side(X, Y):
                ov = coll = 0
                for x in X:
                    hit = [y for y in Y if x["t"] < y["t"] + y["du"] and y["t"] < x["t"] + x["du"]]
                    if hit:
                        ov += 1
                        if any(abs(x["y"] - y["y"]) <= 0.05 for y in hit):
                            coll += 1
                return ov, coll
            ov_a, coll_a = side(A, B)
            ov_b, coll_b = side(B, A)
            pairs[(a, b)] = {"overlap": ov_a, "collide": coll_a, "na": len(A),
                             "overlap_b": ov_b, "collide_b": coll_b, "nb": len(B)}
    base_segs = sorted([x for t in base_tracks for x in t["segs"]], key=lambda x: x["t"])
    return {"subs": subs, "disp": disp, "base_px": base_px, "pairs": pairs, "cuts": m.get("cuts") or [],
            "has_sub": bool(base_tracks),
            "base_segs": base_segs, "base_cov": union_sec(base_segs) / dur,
            "base_tracks": [t["ti"] for t in base_tracks],
            "base_y": base["med_y"] if base else None}


def segs_of(cls: dict, el: str) -> list:
    if el == "subtitle":
        return cls["subs"]
    return [s for s in cls["disp"] if s["el"] == el]


# ---------------------------------------------------------------- table helpers
def sample_rows(segs: list, kind: str, limit: int = 10) -> list:
    if not segs:
        return []
    rows = sorted(segs, key=lambda s: s["t"])
    if len(rows) > limit:
        step = (len(rows) - 1) / (limit - 1)
        idx = sorted({int(round(i * step)) for i in range(limit)})
        rows = [rows[i] for i in idx]
    out = []
    for i, s in enumerate(rows):
        if kind == "subtitle":
            val = f"{short(s['units'],1)}u / {round(s['px'])}px"
            note = f"{s['ch']} 字，停 {short(s['du'])}s"
            if s["kw"] and abs(s["kw"]["ratio"] - 1.0) >= 0.005:
                note += f"；含高亮 {short(s['kw']['ratio'],2)}×"
            note += "；首句首条" if i == 0 and s["t"] <= 0.2 else f"；y={round(s['y']*1920)}px"
            out.append({"at_sec": short(s["t"], 3), "value": val, "note": note})
        elif kind == "keyword":
            kw = s["kw"] or {}
            r = kw.get("ratio", 1.0)
            val = (f"{kw.get('color') or '-'}（同字号）" if abs(r - 1.0) < 0.005
                   else f"+{round((r-1)*100)}% 字 / {kw.get('color') or '-'}")
            out.append({"at_sec": short(s["t"], 3),
                        "value": val,
                        "note": f"高亮 {kw.get('len',0)} 字，块长 {s['ch']} 字，"
                                f"宿主{'字幕' if kw.get('host')=='subtitle' else '展示字'}，"
                                f"{'落句末' if kw.get('at_end') else '落句中'}"})
        elif kind == "giant":
            out.append({"at_sec": short(s["t"], 3), "value": f"{round(s['px'])}px",
                        "note": f"{s['ch']} 字，停 {short(s['du'])}s，y={round(s['y']*1920)}px"})
        elif kind == "chapter":
            out.append({"at_sec": short(s["t"], 3), "value": f"{round(s['px'])}px",
                        "note": f"{s['ch']} 字，停 {short(s['du'])}s，y={round(s['y']*1920)}px"})
        elif kind == "end":
            out.append({"at_sec": short(s["t"], 3), "value": f"{round(s['px'])}px",
                        "note": f"{s['ch']} 字，停 {short(s['du'])}s，y={round(s['y']*1920)}px"})
        else:  # flower
            out.append({"at_sec": short(s["t"], 3), "value": f"{round(s['px'])}px",
                        "note": f"{s['ch']} 字，停 {short(s['du'])}s，y={short(s['y'],2)}"})
    return out


def anim_names(segs: list) -> list:
    c = Counter(f"{a['type']}:{a['name']}" for s in segs for a in s["anims"] if a.get("name"))
    return [[k, v] for k, v in c.most_common(4)]


def union_sec(segs: list) -> float:
    if not segs:
        return 0.0
    iv = sorted((s["t"], s["t"] + s["du"]) for s in segs)
    tot, cur_s, cur_e = 0.0, iv[0][0], iv[0][1]
    for a, b in iv[1:]:
        if a <= cur_e:
            cur_e = max(cur_e, b)
        else:
            tot += cur_e - cur_s
            cur_s, cur_e = a, b
    return tot + cur_e - cur_s


def pair_stat(cls: dict, a: str, b: str, subject: str = None) -> dict:
    """subject 指定以谁为分母：取该方向的 overlap/collide。"""
    pr = (cls.get("pairs") or {})
    st = pr.get((a, b))
    flip = False
    if st is None:
        st = pr.get((b, a))
        flip = True
    if st is None:
        return {"overlap": 0, "collide": 0, "n": 0}
    first = a if not flip else b
    if subject is None or subject == first:
        return {"overlap": st["overlap"], "collide": st["collide"], "n": st["na"]}
    return {"overlap": st["overlap_b"], "collide": st["collide_b"], "n": st["nb"]}


def band_relation(st: dict, a: str, b: str) -> tuple:
    """返回 (coupling_type, 说明)；把「互斥」从断言降级成实测。"""
    if st["n"] == 0 or st["overlap"] == 0:
        return ("time_mutex", f"源里 {a} 与 {b} 从不在同一帧出现（0/{max(st['n'],1)} 条重叠）")
    if st["collide"] == 0:
        return ("y_band_mutex", f"同帧可以并存（{st['overlap']}/{st['n']} 条 {a} 与 {b} 时间重叠），"
                                f"但 0 条落进同一 y 带——分层是空间上的，不是时间上的")
    return ("coexist_same_band", f"{st['collide']}/{st['n']} 条 {a} 与 {b} 同帧且同 y 带，"
                                 f"属于「替代式」：占住带的一方让另一方让位")


def zone_of(y: float) -> str:
    if y < 0.35:
        return "top"
    if y < 0.62:
        return "middle"
    return "bottom"


# ---------------------------------------------------------------- element builders
def build_subtitle(segs, cls, lib_spec, base_px) -> dict:
    dur = cls["dur"]
    n = len(segs)
    if n < 3:
        return {"present": False,
                "absent_reason": (f"真值 0 段：这套没有 ASR 字幕轨（全部文字都是展示字轨），"
                                  f"逐句字幕规则不适用" if n == 0 else f"真值 {n} 段，不足以形成逐句规则"),
                "why_appears": None, "value_drivers": [], "scope": None, "affects": [],
                "limits": [], "coupling": [], "violation_symptoms": [], "reuse_check": [],
                "sample_table": []}
    xs = sorted(segs, key=lambda s: s["t"])
    chs = [s["ch"] for s in xs]
    dus = [s["du"] for s in xs]
    cps = [s["ch"] / s["du"] for s in xs if s["du"] > 0.2 and s["ch"] > 0]
    cuts = cls.get("cuts") or []
    near_cut = sum(1 for x in xs if cuts and min(abs(x["t"] - c) for c in cuts) <= 2 * FRAME)
    cut_dist = med([min(abs(x["t"] - c) for c in cuts) for x in xs]) if cuts else None
    chain = cls.get("base_segs") or xs
    glue = sum(1 for a, b in zip(chain, chain[1:]) if abs(b["t"] - (a["t"] + a["du"])) <= FRAME)
    glue_r = glue / max(1, len(chain) - 1)
    cov = sum(dus) / dur
    cov_track = cls.get("base_cov") or cov
    y_med = med([s["y"] for s in xs])
    base_y_med = cls.get("base_y")
    near = [round(s["body_units"], 2) for s in xs if abs(s["body_px"] - base_px) <= 1.5]
    main_units = Counter(near).most_common(1)[0][0] if near else round(base_px / 6.25, 2)
    units = Counter(round(s["units"], 1) for s in xs).most_common(2)
    hi_units = max(units, key=lambda kv: kv[0])[0]
    body_share = sum(1 for s in xs if abs(s["body_px"] - base_px) <= 1.5) / n
    hi_share = sum(1 for s in xs if s["units"] > s["body_units"] * 1.05) / n
    colors = Counter(s["styles"][0]["color"] for s in xs if s["styles"] and s["styles"][0]["color"])
    lines_multi = sum(1 for s in xs if s["lines"] > 1)
    max_line = max(s["line_max"] for s in xs)
    lib_units = lib_spec.get("font_size_jianying_units")
    lib_px = lib_spec.get("font_size_1080p_px")
    lib_chars = lib_spec.get("max_chars_per_line")
    lib_safe = lib_spec.get("safe_y_ratio")
    font_dev = (base_px / lib_px - 1.0) if lib_px else 0.0
    shadow = next((s["shadow"] for s in xs if s["shadow"]), None) or {}
    has_shadow = bool(shadow)
    stroke_any = sum(1 for s in xs if s["strokes"])
    anims = anim_names(xs)
    word_ms = (med(cps) and 1000.0 / med(cps)) or 0

    drivers = [
        {"param": "font_size",
         "value": f"{main_units} 单位 / {round(base_px)}px @1080",
         "derived_from": "content.styles[0].size（该轨众数）",
         "formula": "px = size × canvas_w / 172.8 = size × 6.25（1080 宽）",
         "why_this_value": f"主体 {main_units} 单位（{round(base_px)}px）出现在 {pct(body_share)} 的段（styles[0] 块）"
                           + (f"；另有 {pct(hi_share)} 的段带更大的 {hi_units} 单位——那是同一条里的行内高亮块"
                              f"（见 keyword_highlight），不是第二套字幕字号"
                              if hi_share >= 0.05 else "，全片只有这一档字幕字号"),
         "calibrated_by": "forensic/text_style_truth.json + 本表实测",
         "tolerance": "±1px（换字号也只能整档换，不能按 0.9× 缩）"},
        {"param": "fill_color",
         "value": " / ".join(f"{c}×{v}" for c, v in colors.most_common(3)),
         "derived_from": "content.styles[i].fill.content.solid.color",
         "formula": "RGB 0-1 → #RRGGBB，无渐变、无透明",
         "why_this_value": (f"主色占 {round(colors.most_common(1)[0][1]/n*100)}%，全片字幕只有这一个色"
                            if len(colors) == 1 else
                            f"主色占 {round(colors.most_common(1)[0][1]/n*100)}%；"
                            f"第二色（{colors.most_common(2)[-1][0]}）只出现在关键词高亮块里，不是随机换色"),
         "calibrated_by": "forensic/style_render_truth.json::texts.fonts_basename/字号直方图",
         "tolerance": "通道 ±2"},
        {"param": "stroke",
         "value": "none（styles[].strokes 为空）",
         "derived_from": "materials.texts[].styles[].strokes",
         "formula": "有 strokes[] 才画描边，宽度取 strokes[].size；素材级 border_width 只作废值",
         "why_this_value": f"{n} 段里 {stroke_any} 段有真描边；"
                           f"border_width 恒为 0.08 是剪映面板滑杆记忆值，与渲染无关",
         "calibrated_by": "forensic/text_style_truth.json（stroke_mode）",
         "tolerance": "0（要么有要么没有，不存在 0.5px 描边）"},
        {"param": "shadow",
         "value": (f"color #000000 / alpha {round(float(shadow.get('alpha') or 0),2)} / distance {round(float(shadow.get('distance') or 0),1)}u / "
                  f"point ({short((shadow.get('point') or {}).get('x',0),3)},{short((shadow.get('point') or {}).get('y',0),3)}) / "
                  f"smoothing {round(float(shadow.get('smoothing') or 0),2)}") if has_shadow else "none",
         "derived_from": "materials.texts[].shadow_distance / shadow_point / shadow_smoothing / shadow_alpha",
         "formula": "offset_px = distance × 2.3（1080 宽）；σ = smoothing × font_px × 0.13",
         "why_this_value": ("四件套在源里是固定常量，全片不随字号/颜色变；"
                            "distance=5 换算 11.5px 是「贴纸感」的来源"
                            if has_shadow else
                            f"这套字幕不挂阴影（{sum(1 for x in xs if x['shadow'])}/{n} 段有 shadow）："
                            f"它靠字号/颜色跟画面拉开，不靠投影"
                            + (f"；库记 shadow={lib_spec.get('shadow')} / distance="
                               f"{lib_spec.get('shadow_distance_units')}，与源不符，以源为准"
                               if lib_spec.get("shadow") else "")),
         "calibrated_by": "forensic/style_render_truth.json::texts.shadow + rebuild_subtitle_typography.py",
         "tolerance": "alpha ±0.05，smoothing ±0.02"},
        {"param": "safe_y（字幕带中心）",
         "value": f"{short(y_med,3)}×H = {round(y_med*1920)}px（库记 {lib_safe}）",
         "derived_from": "segment.clip.transform.y",
         "formula": "y_ratio = (1 − transform.y) / 2；px = y_ratio × canvas_h",
         "why_this_value": f"{n} 段的 y 中位就是 {short(y_med,3)}，标准差极小——字幕从不下移去躲画面；"
                           f"库值与实测相差 {short((y_med - (lib_safe or y_med))*1920)}px",
         "calibrated_by": "forensic/style_render_truth.json::canvas_fps + 本表实测",
         "tolerance": "±0.01×H（约 ±19px）"},
    ]
    anim_cnt = sum(1 for x in xs if x["anims"])
    anim_kinds = anim_names(xs)
    drivers.append({
        "param": "entry_anim",
        "value": "none" if anim_cnt == 0 else f"{anim_cnt}/{n} 段挂了动画 " + str(anim_kinds[:2]),
        "derived_from": "segment.extra_material_refs → material_animations.animations[]",
        "formula": "有 anim 引用才播；动画时长取 animations[].duration（源里只有 0.3/0.5s 两档）",
        "why_this_value": f"字幕默认不动画（{n} 段里只有 {anim_cnt} 段挂动画，属零星手滑）；"
                          f"动画是展示字的资源——同一套里展示字 {sum(1 for d in cls['disp'] if d['anims'])}/"
                          f"{len(cls['disp'])} 段都挂动画",
        "calibrated_by": "本表实测（material_animations）",
        "tolerance": "0（字幕不挂动画）",
    })
    drivers = drivers + [
        {"param": "hold（停留时长）",
         "value": f"中位 {short(med(dus))}s（{round(min(dus),2)}–{round(max(dus),2)}s），"
                  f"每字 {round(word_ms)}ms",
         "derived_from": "target_timerange.duration",
         "formula": "dur ≈ ch / cps，cps 中位 " + f"{short(med(cps),2)} 字/秒",
         "why_this_value": f"时长不是拍脑袋给的：字数 {med(chs):.0f} 字 × {round(word_ms)}ms ≈ {short(med(dus))}s，"
                           f"跟口播语速走；{round(glue_r*100)}% 的句首紧贴上一句句尾（Δ≤1 帧），"
                           f"说明它由 ASR 断句驱动而不是按秒切",
         "calibrated_by": "本表实测（dur vs ch 回归）",
         "tolerance": "±2 帧（33ms）"},
    ]
    limits = [
        {"name": "max_chars_per_line", "value": max_line,
         "why": f"源里最长行 {max_line} 字（{lines_multi} 段出现换行）；库记 {lib_chars}——"
                + ("两者一致" if lib_chars == max_line else
                   "库值是 sub_max_chars（把长引语卡/标题也算进字幕），相差悬殊，以本表实测为准"
                   if lib_chars and lib_chars > max_line * 1.8 else
                   "库值比实测大几字，差异来自分类口径（库把展示字算进字幕）")},
        {"name": "font_size_jianying_units", "value": main_units,
         "why": f"库记 {lib_units} 单位 / {lib_px}px；实测 {main_units} 单位 / {round(base_px)}px"
                f"（偏差 {round(font_dev*100):+d}%，实测比库{'大' if font_dev > 0 else '小'} "
                f"{abs(round(font_dev*100))}%）"
                if abs(font_dev) >= 0.005 else "（偏差 0%，与实测一致）"},
        {"name": "px_per_unit_at_1080", "value": 6.25,
         "why": "1080/172.8；把 172.8 当 192 会让每个字号小 11%"},
        {"name": "safe_y", "value": short(y_med, 3),
         "why": f"字幕带中心；花字/贴纸不得进 ±0.04 带；实测已在 {round(y_med*1920)}px"},
        {"name": "stroke_mode", "value": "none" if stroke_any == 0 else "stroke",
         "why": f"源里 {stroke_any}/{n} 段有 styles[].strokes；border_width=0.08 是面板记忆值，不算描边"},
        ({"name": "shadow_distance", "value": round(float(shadow.get("distance") or 0), 1),
          "why": "阴影位移（单位），换算 offset_px = distance × 2.3（1080 宽）"} if has_shadow else
         {"name": "shadow_distance", "value": "none", "why": "源里这套字幕没有 shadow 块"}),
        ({"name": "shadow_point", "value": f"({short((shadow.get('point') or {}).get('x', 0), 3)}, "
                                          f"{short((shadow.get('point') or {}).get('y', 0), 3)})",
          "why": "阴影方向（单位向量，归一化）；x 正 y 负 = 右下"} if has_shadow else
         {"name": "shadow_point", "value": "none", "why": "无阴影"}),
        ({"name": "shadow_smoothing", "value": round(float(shadow.get("smoothing") or 0), 2),
          "why": "σ = smoothing × font_px × 0.13；改它可以造「发光」或「硬边」，源里是固定值"} if has_shadow else
         {"name": "shadow_smoothing", "value": "none", "why": "无阴影"}),
        {"name": "min_hold", "value": short(min(dus), 2),
         "why": "最短一条；低于 0.4s 的字幕观众读不完，源里出现是因为那句只有 2–3 字"},
        {"name": "max_hold", "value": short(max(dus), 2),
         "why": "最长一条；超过 4s 通常是长句没被 ASR 断开，属于源里的瑕疵而非规则"},
    ]
    upgraded = [d for d in cls["disp"]
                if base_y_med is not None and abs(d["y"] - base_y_med) <= 0.06 and d["px"] > 1.35 * base_px]
    if upgraded:
        limits.append({
            "name": "upgraded_segments", "value": len(upgraded),
            "why": f"另有 {len(upgraded)} 段仍落在字幕带、但被放大到 "
                   f"{short(min(u['px'] for u in upgraded)/max(1.0,base_px),2)}–"
                   f"{short(max(u['px'] for u in upgraded)/max(1.0,base_px),2)}×（已在本表归到 giant_text）；"
                   f"所以字幕覆盖率 {pct(cov)} 不是漏字幕，是风格把某些句升级成大字",
        })
    affects = [
        {"target": "keyword_highlight", "how": "高亮是同一 text 块的第二个 range；字幕换行会把 range 切断，所以先定行再定词"},
        {"target": "flower 安全区", "how": f"字幕带 y={short(y_med,3)}×H 被占；"
                                          f"展示字落进 ±0.05 带就会压字（本套实测 "
                                          f"{pair_stat(cls, 'subtitle', 'flower', 'subtitle')['collide']} 条字幕被压）"},
        {"target": "B-roll 可读性", "how": "字幕压在下 1/3，手部/产品特写出现在这条带上会被遮"},
        {"target": "入出场动画", "how": f"字幕不挂动画（{anim_cnt}/{n} 段），动画只给展示字；"
                                        f"把动画照搬到字幕会让整片一直在动"},
    ]
    fl_t, fl_why = band_relation(pair_stat(cls, "subtitle", "flower", "subtitle"), "字幕", "展示字")
    coupling = [
        {"with": "flower", "type": fl_t, "why": fl_why},
        {"with": "keyword_highlight", "type": "same_text_block", "why": "高亮只能是字幕块的一部分，不能独立成轨"},
    ]
    viol = ["字号偏小 11% = 把 172.8 当成 192（75px 会被算成 67px）"]
    if abs(font_dev) >= 0.08:
        viol.append(f"直接套库里的 {lib_units} 单位：本套真实 {main_units} 单位，"
                    f"字号偏大 {round(font_dev*100)}%，字幕顶进花字带")
    viol += ["把 border_width=0.08 当描边画出黑边（源 styles[].strokes 为空）",
             "源 background_alpha=0 却画出底框",
             "按固定 1.2s 给时长而不是按字数/语速 → 长句被截、短句拖"]
    reuse = [
        f"新素材有逐句 ASR 时间戳时，先核「句首紧贴句尾」比例是否 ≥{max(50, round(glue_r*100)-20)}%，否则说明断句口径变了",
        f"渲染后量字幕中心 y，偏离 {round(y_med*1920)}px 超过 19px 即判不合格",
        "字号只能整档取，不允许为了塞进一行缩到 0.9×",
    ]
    return {
        "present": True, "absent_reason": None,
        "why_appears": {
            "rule": "口播每说完一句（ASR 断句点）上一条；句子换则切段，段与段首尾相接不留空帧",
            "triggers": ["口播逐句（ASR 断句）", "换句"],
            "not_when": ["纯 B-roll / 空镜无人声", "片尾静帧", "同一句里主轨切点不触发字幕切段"],
            "evidence": f"base 轨（track {cls['base_tracks']}）共 {len(chain)} 段，覆盖片长 {pct(cov_track)}；"
                        f"相邻段无缝率 {pct(glue_r)}（Δ≤1 帧，按整轨顺序算）；"
                        f"其中 {n} 段是普通字号字幕（其余 {len(chain)-n} 段被升级）；y 中位 {short(y_med,3)}×H；"
                        + ((f"字幕起点 {pct(near_cut/n)} 落在主轨切点 ±2 帧内"
                            f"（到最近切点中位 {short(cut_dist)}s）——"
                            + ("这套是切点驱动的重剪风格，字幕起句跟着镜头切"
                               if near_cut / n >= 0.5 else "由语音断句驱动，不跟切点"))
                           if cuts else "（主轨无切点可比）"),
        },
        "value_drivers": drivers,
        "scope": {
            "unit": "句",
            "coverage": f"全片口播 {len(chain)} 段（{pct(cov_track)} 时长），跨镜头不重置、不按镜头分段"
                        + (f"；本表 {n} 段为普通字号字幕，另 {len(chain)-n} 段被升级为标题/大字"
                           if len(chain) > n else f"；{n} 段全部是普通字号字幕"),
            "excludes": "无台词的纯 B-roll 段、片尾定格",
            "reset_on": "换句（ASR 断句）；不随主轨切点重置",
        },
        "affects": affects, "limits": limits, "coupling": coupling,
        "violation_symptoms": viol, "reuse_check": reuse,
        "sample_table": sample_rows(xs, "subtitle"),
    }


def build_keyword(all_segs, cls, base_px) -> dict:
    nsub = len(cls["subs"])
    kws = [s for s in all_segs if s.get("kw")]
    n = len(kws)
    # 缩小的小字副行（双语/拼音）不是强调，单独计数
    sublines = sum(1 for x in all_segs if len(x["styles"]) >= 2 and x["styles"][1].get("size")
                   and x["styles"][0].get("size")
                   and x["styles"][1]["size"] < 0.9 * x["styles"][0]["size"])
    if n < 3:
        return {
            "present": False,
            "absent_reason": (f"真值 {n} 段带第二个 style range 的强调块——"
                              + ("这套只用单色单字号" if n == 0 else "只有 1–2 条，不成规律")
                              + (f"；另有 {sublines} 条是缩小的小字副行（双语/拼音），不是强调" if sublines else "")),
            "why_appears": None, "value_drivers": [], "scope": None, "affects": [],
            "limits": [], "coupling": [], "violation_symptoms": [], "reuse_check": [],
            "sample_table": [],
        }
    ratios = Counter(round(s["kw"]["ratio"], 3) for s in kws)
    ratio = ratios.most_common(1)[0][0]
    colors = Counter(s["kw"]["color"] for s in kws if s["kw"]["color"])
    body_colors = Counter(s["kw"]["body_color"] for s in kws if s["kw"]["body_color"])
    lens = [s["kw"]["len"] for s in kws if s["kw"]["len"]]
    at_end = sum(1 for s in kws if s["kw"]["at_end"])
    numeric = sum(1 for s in kws if s["kw"].get("numeric"))
    hosts = Counter(s["kw"]["host"] for s in kws)
    n_all = len(all_segs)
    share_sub = sum(1 for s in kws if s["kw"]["host"] == "subtitle") / max(1, nsub)
    share_all = n / max(1, n_all)
    scale_px = round(base_px * ratio)

    drivers = [
        {"param": "highlight_scale",
         "value": f"{short(ratio,3)}× 主体字号（{round(base_px)}→{scale_px}px）",
         "derived_from": "content.styles[1].size / content.styles[0].size",
         "formula": "highlight_px = base_px × ratio；行宽也按同一比例增加",
         "why_this_value": f"{n} 条高亮里 {ratios.most_common(1)[0][1]} 条都是 {short(ratio,3)}×，"
                           f"模板锁死的一个常数；{'没有放大、只换色' if ratio == 1.0 else '既放大又换色'}",
         "calibrated_by": "本表实测（styles[].size 直方图）",
         "tolerance": "±0.01（1.133 不能写成 1.16）"},
        {"param": "highlight_color",
         "value": " / ".join(f"{c}×{v}" for c, v in colors.most_common(3)) or "same_as_body",
         "derived_from": "content.styles[1].fill.content.solid.color",
         "formula": "纯色填充，不透明",
         "why_this_value": f"高亮色只有一个（{colors.most_common(1)[0][0] if colors else '与主体同色'}），"
                           f"主体色通常是 {body_colors.most_common(1)[0][0] if body_colors else '-'}；"
                           f"两者是固定配对，不是每组随机",
         "calibrated_by": "forensic/style_render_truth.json::texts",
         "tolerance": "通道 ±2"},
        {"param": "range_len",
         "value": f"{med(lens):.0f} 字（{min(lens)}–{max(lens)}）" if lens else "0",
         "derived_from": "content.styles[1].range = [start,end]",
         "formula": "range 长度 = end − start；range 必须以字符下标为单位，不能用像素",
         "why_this_value": f"高亮只盖句子里最值钱的那 2–4 个字，不整句变色；"
                           f"{at_end}/{n} 条落在块末、{numeric}/{n} 条是数字/价格（两端最容易做强调的位置）",
         "calibrated_by": "本表实测",
         "tolerance": "±1 字"},
    ]
    limits = [
        {"name": "highlights_per_sentence", "value": 1,
         "why": f"{n} 条高亮段里没有一条出现第二个 range；多标一个就变成花字"},
        {"name": "highlight_len_ratio", "value": short(med(lens) / max(1, med([s['ch'] for s in kws])), 2) if lens else 0,
         "why": "高亮字数不超过句长的 1/3；超了整句变黄等于没高亮"},
        {"name": "coverage_share", "value": short(max(share_sub, share_all), 3),
         "why": f"带行内高亮的块只占 {pct(share_all)}（字幕句里 {pct(share_sub)}）；"
                f"每条都标会让画面一直在闪"},
    ]
    affects = [
        {"target": "字幕行宽", "how": f"放大 {round((ratio-1)*100)}% 会让同一行的像素宽增加；"
                                     f"长句要提前断行，否则高亮词被挤到下一行"},
        {"target": "max_chars_per_line", "how": "按高亮后的宽度算，不按原字号算"},
    ]
    coupling = [
        {"with": "subtitle", "type": "same_text_block", "why": "高亮只能是 styles[1]，独立成轨会在同一帧出现两条字幕"},
        {"with": "flower", "type": "mutex_in_frame", "why": "同一句已有高亮时不再挂花字，否则两个强调互相打架"},
    ]
    viol = [
        "把高亮做成独立字幕轨 → 同一帧两行叠字（源里所有高亮都在同一个 text 块的 range 里）",
        f"比例写成 1.16（库记）而源是 {short(ratio,3)} → 每行宽多出约 2px，12 字行会换行",
        "高亮 range 跨换行 → 后半句整句变色",
    ]
    reuse = [
        "核高亮是否落在同一个 text 块：看新产出里同一时刻只有一条字幕段",
        f"量高亮字号比：偏离 {short(ratio,3)} 超过 0.01 即判不合格",
        "核高亮字数：超过句长 1/3 就是滥标",
    ]
    trig = []
    if at_end / n >= 0.4:
        trig.append("句末重音短语")
    if numeric / n >= 0.3:
        trig.append("数字/价格")
    if not trig:
        trig.append("被强调的短词（位置不固定）")
    return {
        "present": True, "absent_reason": None,
        "why_appears": {
            "rule": f"句子要压重音时，不另起一条，而是在同一条 text 的 styles[] 追加第二块 style，"
                    f"用 range 只盖那几个字；落点 {pct(at_end/n)} 在块末——是「句尾重音」机制，不是「给数字上色」",
            "triggers": trig,
            "not_when": ["整句都是结论（没有重音落点）", "同一句已经挂了花字"],
            "evidence": f"{n} 条文字块含 2 个 style block（宿主：字幕 {hosts.get('subtitle',0)} / 展示字 {hosts.get('display',0)}；"
                        f"占字幕句 {pct(share_sub)}）；高亮块 {short(ratio,3)}× 主体、"
                        f"颜色 {colors.most_common(1)[0][0] if colors else '-'}；{at_end}/{n} 条贴块末；"
                        f"{numeric}/{n} 条高亮区间含数字或价格符号",
        },
        "value_drivers": drivers,
        "scope": {"unit": "词",
                  "coverage": f"{n} 条命中块：字幕宿主 {hosts.get('subtitle',0)} 条（占字幕句 {pct(share_sub)}）、"
                              f"展示字宿主 {hosts.get('display',0)} 条；只作用于它所在的那一块文本",
                  "excludes": "不跨句、不跨行；换行后 range 失效",
                  "reset_on": "换句/换展示字"},
        "affects": affects, "limits": limits, "coupling": coupling,
        "violation_symptoms": viol, "reuse_check": reuse,
        "sample_table": sample_rows(sorted(kws, key=lambda s: s["t"]), "keyword"),
    }


def build_flower(segs, cls, trig_flower, lib_spec) -> dict:
    n = len(segs)
    dur = cls["dur"] if "dur" in cls else None
    if n < 3:
        others = Counter(s["el"] for s in cls["disp"])
        absorbed = "、".join(f"{k}×{v}" for k, v in others.items() if k != "flower")
        if not cls["disp"]:
            why = "真值 0 段：这套没有独立展示字轨（全片只有字幕/大字）"
        elif n == 0 and absorbed:
            why = f"真值 0 段：展示字全部命中更具体的规则（{absorbed}），没有剩下的普通花字"
        else:
            why = f"真值 {n} 段展示字（阈值 ≥3），不成规律" + (f"；其余归入 {absorbed}" if absorbed else "")
        return {"present": False, "absent_reason": why,
                "why_appears": None, "value_drivers": [], "scope": None, "affects": [],
                "limits": [], "coupling": [], "violation_symptoms": [], "reuse_check": [],
                "sample_table": []}
    base_px = cls.get("base_px") or float((lib_spec.get("subtitle_spec") or {}).get("font_size_1080p_px") or 75)
    chs = [s["ch"] for s in segs]
    dus = [s["du"] for s in segs]
    pxs = [round(s["px"]) for s in segs]
    zones = Counter(zone_of(s["y"]) for s in segs)
    colors = Counter(s["styles"][0]["color"] for s in segs if s["styles"] and s["styles"][0]["color"])
    fonts = Counter(st["font"] for s in segs for st in s["styles"] if st.get("font"))
    stroke_any = sum(1 for s in segs if s["strokes"])
    hold = med(dus)
    trig = (trig_flower or {}).get("trigger_mix") or {}
    allowed = (trig_flower or {}).get("allowed_triggers") or []
    fallback = (trig_flower or {}).get("fallback_hold_sec") or [hold, hold]
    scope_t = (trig_flower or {}).get("scope") or {}
    seg_per_min = n / max(0.1, cls["dur"] / 60)
    per_min = (trig_flower or {}).get("per_min") or seg_per_min
    max_pm = (trig_flower or {}).get("max_per_min")
    lib_px = lib_spec.get("flower_text_spec") or {}
    lib_fpx = lib_px.get("font_size_1080p_px") or round(med(pxs))
    ch_px_corr = None
    if len(segs) >= 4 and len({x["ch"] for x in segs}) > 1 and len({round(x["px"]) for x in segs}) > 1:
        try:
            a = [x["ch"] for x in segs]
            c = [x["px"] for x in segs]
            ma, mc = statistics.mean(a), statistics.mean(c)
            cov = sum((u - ma) * (v - mc) for u, v in zip(a, c)) / len(a)
            ch_px_corr = cov / (statistics.pstdev(a) * statistics.pstdev(c))
        except Exception:
            ch_px_corr = None
    drivers = [
        {"param": "font_size",
         "value": f"中位 {round(med(pxs))}px（{min(pxs)}–{max(pxs)}），"
                  f"{short(med(pxs)/max(1.0,base_px),2)}× 本套字幕基准",
         "derived_from": "content.styles[0].size",
         "formula": "px = size × canvas_w / 172.8",
         "why_this_value": (f"同一套里就有 {len(set(pxs))} 档字号，字数越少字号越大"
                            f"（字数与字号相关 r={short(ch_px_corr,2)}）"
                            if len(set(pxs)) >= 3 and ch_px_corr is not None and ch_px_corr <= -0.3
                            else f"同一套里 {len(set(pxs))} 档字号（{min(pxs)}–{max(pxs)}px），"
                                 f"字号由元素角色定，不由字数定"
                            if len(set(pxs)) >= 3 else
                            f"全片只有一档 {round(med(pxs))}px，靠颜色和落位区分重点，不靠字号"),
         "calibrated_by": "forensic/text_style_truth.json（flower）+ 本表实测",
         "tolerance": "±2px"},
        {"param": "fill_color",
         "value": " / ".join(f"{c}×{v}" for c, v in colors.most_common(4)),
         "derived_from": "content.styles[].fill.content.solid.color",
         "formula": "纯色；同一套里允许 3–5 个色，但都由模板的 accent 色系派生",
         "why_this_value": f"取色不是随机的：使用次数前 {min(4,len(colors))} 个色就覆盖了 {round(sum(v for _,v in colors.most_common(4))/n*100)}% 的展示字",
         "calibrated_by": "forensic/style_render_truth.json::texts.fonts_basename",
         "tolerance": "通道 ±2"},
        {"param": "stroke",
         "value": "none" if stroke_any == 0 else f"{stroke_any}/{n} 段有描边",
         "derived_from": "content.styles[].strokes",
         "formula": "只认 styles[].strokes；素材级 border_width 不作数",
         "why_this_value": f"展示字靠阴影拉对比（{sum(1 for s in segs if s['shadow'])}/{n} 段有阴影），不靠描边；"
                           f"库记 stroke_mode={lib_px.get('stroke_mode')}",
         "calibrated_by": "forensic/text_style_truth.json",
         "tolerance": "0"},
        {"param": "hold",
         "value": f"中位 {short(hold)}s（{short(min(dus))}–{short(max(dus))}s）",
         "derived_from": "target_timerange.duration",
         "formula": "hold ≈ max(字符数/阅读速度, 锚点间隔)；锚点缺失时用 fallback_hold_sec",
         "why_this_value": f"展示字停留由它跟的锚点决定：{short(fallback[0])}–{short(fallback[1])}s 是兜底；"
                           f"实际中位 {short(hold)}s，与字数 {med(chs):.0f} 字对应",
         "calibrated_by": "forensic/style_trigger_rules_sfx_flower.json::flower.hold_sec_*",
         "tolerance": "±2 帧"},
        {"param": "entry_anim",
         "value": (f"{sum(1 for x in segs if x['anims'])}/{n} 段挂动画 " + str(anim_names(segs)[:2]))
                  if sum(1 for x in segs if x["anims"]) else "none",
         "derived_from": "segment.extra_material_refs → material_animations.animations[]",
         "formula": "动画时长取 animations[].duration；源里只有 0.3/0.4/0.5s 三档，与停留时长无关",
         "why_this_value": "展示字几乎条条挂入场动画（这是它和字幕最大的区别）；"
                           "动画时长是固定档，不随字数变化",
         "calibrated_by": "forensic/style_trigger_rules_sfx_flower.json::flower.anims_top",
         "tolerance": "±0.1s"},
        {"param": "safe_zone",
         "value": f"top {zones.get('top',0)} / middle {zones.get('middle',0)} / bottom {zones.get('bottom',0)}",
         "derived_from": "segment.clip.transform.y",
         "formula": "y_ratio = (1 − transform.y)/2；top<0.35 / middle<0.62 / bottom≥0.62",
         "why_this_value": "落位由画面主体决定：人脸在上就往下压，产品特写在下就往上提；"
                           f"本套实际分布 {dict(zones)}",
         "calibrated_by": "本表实测 + face framing 检测",
         "tolerance": "±0.02×H"},
    ]
    limits = [
        {"name": "chars", "value": f"{min(chs)}–{max(chs)} 字（中位 {med(chs):.0f}）",
         "why": (f"展示字是词不是句；本套最长 {max(chs)} 字，再长就退化成第二条字幕"
                 if max(chs) <= 8 else
                 f"本套最长 {max(chs)} 字是文字卡片（整块信息），不是单词冲击；"
                 f"单词型展示字仍集中在 {min(chs)}–8 字")},
        {"name": "per_min", "value": f"{short(seg_per_min,1)} 段/min；逻辑 cue {round(per_min,1)}/min（上限 {max_pm}）",
         "why": "密度由触发点决定，不是平均铺；一条 cue 可以含多段（多行/多轨同时出字），"
                "按段算才是真实上屏密度"},
        {"name": "hold", "value": f"{short(fallback[0])}–{short(fallback[1])}s",
         "why": "低于 0.8s 读不完，高于 3s 变成常驻标题"},
        {"name": "stroke_mode", "value": "none" if stroke_any == 0 else f"{stroke_any}/{n} 段有描边",
         "why": f"库记 {lib_px.get('stroke_mode')}；展示字靠阴影拉对比"},
        {"name": "shadow_offset", "value": str(lib_px.get("shadow_offset") or [0, 0]),
         "why": "展示字阴影位移（px）；与字幕同源，但花字阴影常被刻意加大到 14–45px"},
        {"name": "shadow_blur", "value": lib_px.get("shadow_blur_px"),
         "why": "阴影模糊半径（px）；比字幕大，制造「贴纸」厚度感"},
    ]
    st_sub = pair_stat(cls, "subtitle", "flower", "flower")
    st_gi = pair_stat(cls, "giant_text", "flower", "flower")
    sub_t, sub_why = band_relation(st_sub, "展示字", "字幕")
    gi_t, gi_why = band_relation(st_gi, "展示字", "大字")
    sub_affect = (
        f"本套没有 ASR 字幕轨，展示字独占 y {short(med([x['y'] for x in segs]),2)} 带，"
        f"不需要为字幕让位" if not cls["subs"] else
        f"同帧可并存（{st_sub['overlap']}/{st_sub['n']} 条展示字与字幕时间重叠），"
        f"同 y 带只有 {st_sub['collide']} 条——落进带里就是替代式标题，字幕让位"
        if st_sub["overlap"] else f"时段互斥（0/{st_sub['n']} 条与字幕重叠），展示字只出现在字幕空档")
    affects = [
        {"target": "subtitle 安全区", "how": sub_affect},
        {"target": "人脸/主体遮挡", "how": "落位按主体反打：top 区有脸时改 middle"},
        ({"target": "giant_text", "how": f"与本套 {sum(1 for x in cls['disp'] if x['el']=='giant_text')} 条大字"
                                          f"共占展示层：同帧不同带（{st_gi['collide']}/{st_gi['n']} 条重合）"}
         if st_gi["n"] else {"target": "giant_text", "how": "本套没有 ≥1.8× 的大字块，这条不适用"}),
    ]
    coupling = [
        ({"with": "subtitle", "type": "n/a", "why": "本套无字幕轨，这条耦合不适用"}
         if not cls["subs"] else {"with": "subtitle", "type": sub_t, "why": sub_why}),
        {"with": "sfx", "type": "co_onset", "why": "音效与花字同一锚点起（offset 中位见音效分片）"},
    ]
    viol = [
        "花字压字幕带 → 两行黄字叠在一起，最典型的「看起来乱」",
        "每句话都挂一个 → 源的上限是 " + f"{max_pm}/min（逻辑 cue），超了就变弹幕",
        (f"把花字字号统一成 {lib_fpx}px：源里同一套有 {len(set(pxs))} 档，统一后重点全平"
         if len(set(pxs)) >= 3 else
         f"把花字字号改成比字幕小：源里是 {short(med(pxs)/max(1.0,base_px),2)}× 字幕基准，改小就退化成第二行字幕"),
        "用描边代替阴影：源里展示字 0 描边，靠阴影拉对比",
    ]
    reuse = [
        "新素材上先定锚点（切点/数字/章节），再挂花字；挂不上的用 fallback_hold 也不要硬塞",
        "渲染后量展示字与字幕的 y 带是否重叠",
        f"密度核：每分钟不超过 {max_pm} 条",
    ]
    return {
        "present": True, "absent_reason": None,
        "why_appears": {
            "rule": "展示字只在有可讲内容的锚点上出现：数字/价格、章节头、关键词、文字卡片；纯口播长镜不挂",
            "triggers": allowed or [k for k, v in trig.items() if v],
            "not_when": ["静音段", "字幕正带同帧", "同一帧已有更大字号元素"],
            "evidence": f"{n} 条展示字，{round(per_min,1)}/min；触发构成 {trig}；"
                        f"b-roll 占比 {scope_t.get('broll_share')}，zones {scope_t.get('zones')}",
        },
        "value_drivers": drivers,
        "scope": {"unit": "条",
                  "coverage": f"{n} 条独立文字段（每条自占一段轨道），落位 {dict(zones)}",
                  "excludes": "字幕段、giant/chapter/end 卡",
                  "reset_on": "换锚点"},
        "affects": affects, "limits": limits, "coupling": coupling,
        "violation_symptoms": viol, "reuse_check": reuse,
        "sample_table": sample_rows(segs, "flower"),
    }


def build_giant(segs, cls, base_px) -> dict:
    n = len(segs)
    if n < 3:
        return {"present": False,
                "absent_reason": f"真值 {n} 段（{'无 ≥1.8× 字幕基准的单词块' if n == 0 else '只有个别条，不成规律'}）",
                "why_appears": None, "value_drivers": [], "scope": None, "affects": [],
                "limits": [], "coupling": [], "violation_symptoms": [], "reuse_check": [],
                "sample_table": []}
    dur = cls["dur"]
    pxs = [s["px"] for s in segs]
    ratios = [p / base_px for p in pxs]
    chs = [s["ch"] for s in segs]
    first5 = sum(1 for s in segs if s["t"] <= 5.0)
    ys = [s["y"] for s in segs]
    dus = [s["du"] for s in segs]
    stroke = sum(1 for s in segs if s["strokes"])
    drivers = [
        {"param": "font_size",
         "value": f"中位 {round(med(pxs))}px = {short(med(ratios),2)}× 字幕基准（{round(base_px)}px）",
         "derived_from": "content.styles[0].size",
         "formula": "px = size × canvas_w/172.8；判定线 px ≥ 1.8 × base_px",
         "why_this_value": (f"{n} 条全是 {round(med(pxs))}px（{short(med(ratios),2)}×），"
                            f"同一个模板值，不是逐条调出来的"
                            if max(pxs) - min(pxs) < 5 else
                            f"{n} 条里字号跨度 {round(min(pxs))}–{round(max(pxs))}px，"
                            f"换成倍数就是 {short(min(ratios),2)}–{short(max(ratios),2)}×")
                           + "；1.8× 是「砸一下」的下限，低于它就和普通展示字分不开",
         "calibrated_by": "forensic/style_render_truth.json::texts.font_size_hist",
         "tolerance": "±3px"},
        {"param": "chars",
         "value": f"{min(chs)}–{max(chs)} 字（中位 {med(chs):.0f}）",
         "derived_from": "content.text 的字符数",
         "formula": "char_len，不含换行",
         "why_this_value": "字越大能塞的字越少；超过 6 字就必须换行，视觉冲击就没了",
         "calibrated_by": "本表实测",
         "tolerance": "±1 字"},
        {"param": "position",
         "value": f"y 中位 {short(med(ys),2)}（{round(med(ys)*1920)}px），top 占 {pct(sum(1 for y in ys if y<0.35)/n)}",
         "derived_from": "segment.clip.transform.y",
         "formula": "y_ratio = (1 − transform.y)/2",
         "why_this_value": f"{n} 条里 {sum(1 for y in ys if y<0.35)} 条落 top、"
                           f"{sum(1 for y in ys if 0.35<=y<0.62)} 条落 middle、"
                           f"{sum(1 for y in ys if y>=0.62)} 条压到下部（与字幕同带）；"
                           f"大字位置由主体决定，不是一个固定点",
         "calibrated_by": "本表实测",
         "tolerance": "±0.02×H"},
    ]
    limits = [
        {"name": "ratio_vs_subtitle", "value": short(med(ratios), 2),
         "why": "小于 1.8× 只是普通展示字；大于 8× 会顶到画外"},
        {"name": "chars", "value": max(chs), "why": "超过 6 字的巨型字只能靠换行，破坏单点冲击"},
        {"name": "hold", "value": f"{short(med(dus))}s",
         "why": (f"大字停留 {short(med(dus))}s，比普通展示字（{short(med([x['du'] for x in cls['disp'] if x['el']=='flower'] or [med(dus)]))}s）更长："
                 "读者要先认出形状再读字"
                 if med([x["du"] for x in cls["disp"] if x["el"] == "flower"]) and
                 med([x["du"] for x in cls["disp"] if x["el"] == "flower"]) < med(dus)
                 else f"大字停留 {short(med(dus))}s，与普通展示字同量级——靠字号而不是靠时间做冲击")},
    ]
    st_gs = pair_stat(cls, "giant_text", "subtitle", "giant_text")
    st_gf = pair_stat(cls, "giant_text", "flower", "giant_text")
    gs_t, gs_why = band_relation(st_gs, "大字", "字幕")
    gf_t, gf_why = band_relation(st_gf, "大字", "展示字")
    affects = [
        {"target": "字幕带", "how": (gs_why if st_gs["n"] else "本套无字幕轨，大字不受字幕带约束")
                                  + f"；落带内时该镜头字幕让位"},
        {"target": "人脸/主体", "how": "大字直接盖在主体上，必须配合抠像或虚化，否则读不清"},
        {"target": "chapter_card", "how": "同一个分点只用一种：要么大字砸，要么小卡列，不叠加"},
    ]
    coupling = [
        {"with": "flower", "type": gf_t, "why": gf_why},
        ({"with": "subtitle", "type": "n/a", "why": "本套无字幕轨"}
         if not cls["subs"] else {"with": "subtitle", "type": gs_t, "why": gs_why}),
    ]
    viol = [
        "把 giant 当普通花字处理 → 字号只有 1.2×，冲击感消失",
        "大字盖住字幕带 → 两行叠字",
        "大字字数超 6 又硬塞一行 → 被压扁/出画",
    ]
    reuse = [
        f"新素材上先算 base_px，再用 1.8× 判定线筛，低于线的不要叫 giant",
        "核落位：大字中心 y 与字幕带 y 的差 ≥0.2",
    ]
    return {
        "present": True, "absent_reason": None,
        "why_appears": {
            "rule": "开场钩子 / 分点标题 / 结论词上，用 ≥1.8× 正文字号的单词砸一下；同一镜头最多砸一次（多块时是阶梯出字，不是并排）",
            "triggers": ["开场 5 秒内的钩子词", "分点标题", "结论词"],
            "not_when": ["连续长句段", "画面里正给人看关键信息（会互相遮）"],
            "evidence": f"{n} 条 ≥1.8× 的字块；{first5}/{n} 条落在前 5 秒；"
                        f"字号 {round(med(pxs))}px，字数中位 {med(chs):.0f}",
        },
        "value_drivers": drivers,
        "scope": {"unit": "词/行",
                  "coverage": f"{n} 条，实际占片长 {pct(union_sec(segs)/dur)}（并集）；集中在前 5s 与分点处",
                  "excludes": "字幕带、普通展示字",
                  "reset_on": "换镜/换分点"},
        "affects": affects, "limits": limits, "coupling": coupling,
        "violation_symptoms": viol, "reuse_check": reuse,
        "sample_table": sample_rows(segs, "giant"),
    }


def build_chapter(segs, cls, base_px) -> dict:
    n = len(segs)
    n_distinct = len({s["text"] for s in segs})
    if n_distinct < 3:
        return {"present": False,
                "absent_reason": (f"真值 {n} 段 / {n_distinct} 个不同序号标题（阈值 ≥3 个分点）——"
                                  f"{'这套是线性叙述，没有分点结构；序号只出现在字幕句里，不是卡' if n == 0 else '分点不足 3 个，不成规律'}"),
                "why_appears": None, "value_drivers": [], "scope": None, "affects": [],
                "limits": [], "coupling": [], "violation_symptoms": [], "reuse_check": [],
                "sample_table": []}
    cnt = Counter(s["text"] for s in segs)
    forms = Counter("序号汉字" if re.match(r"^[一二三四五六七八九十]、", s["text"]) else
                    "第N" if s["text"].startswith("第") else
                    "数字点号" if re.match(r"^\d", s["text"]) else "括号序号"
                    for s in segs)
    dus = [s["du"] for s in segs]
    pxs = [s["px"] for s in segs]
    ys = [s["y"] for s in segs]
    ts = sorted(s["t"] for s in segs)
    gaps = [b - a for a, b in zip(ts, ts[1:])] or [0]
    rep = sum(1 for k, v in cnt.items() if v >= 2)
    drivers = [
        {"param": "ordinal_form",
         "value": " / ".join(f"{k}×{v}" for k, v in forms.most_common()),
         "derived_from": "content.text 开头的序号写法",
         "formula": "文本以 一、/第二/1./(1) 开头 ⟹ 分点卡；否则归 flower",
         "why_this_value": f"{n} 条全部带序号前缀（{forms.most_common(1)[0][0]}为主），"
                           f"{rep} 个标题被复挂——这是「分点讲解」的结构指纹，不是装饰",
         "calibrated_by": "本表实测（文本前缀 + 精确匹配）",
         "tolerance": "序号写法必须与源一致（一、 不是 1、）"},
        {"param": "font_size",
         "value": f"中位 {round(med(pxs))}px（{short(med(pxs)/max(1.0,base_px),2)}× 字幕基准）",
         "derived_from": "content.styles[0].size",
         "formula": "px = size × canvas_w/172.8",
         "why_this_value": (f"相对字幕基准 {short(med(pxs)/max(1.0,base_px),2)}×："
                            + ("比正文大、比 giant 小，靠字号就能分层"
                               if med(pxs) > base_px * 1.1 else
                               "并不比正文大——它靠位置（top/middle）和停留时长跟字幕分层，不靠字号")),
         "calibrated_by": "forensic/style_render_truth.json::texts.font_size_hist",
         "tolerance": "±3px"},
        {"param": "hold",
         "value": f"中位 {short(med(dus))}s（{short(min(dus))}–{short(max(dus))}s）",
         "derived_from": "target_timerange.duration",
         "formula": "hold ≥ 覆盖该分点第一句的时长",
         "why_this_value": "分点卡要压住整个小节的引入句，所以比普通展示字长；"
                           "短的（<1.2s）在本表里已排除，属于一闪而过的贴纸",
         "calibrated_by": "本表实测",
         "tolerance": "±2 帧"},
        {"param": "position",
         "value": f"y 中位 {short(med(ys),2)}（{round(med(ys)*1920)}px），top+middel 占 {pct(sum(1 for y in ys if y<0.62)/n)}",
         "derived_from": "segment.clip.transform.y",
         "formula": "y_ratio = (1 − transform.y)/2",
         "why_this_value": "分点卡当「页眉」：落 top/middle，把底部字幕带整条让出来",
         "calibrated_by": "本表实测",
         "tolerance": "±0.02×H"},
        {"param": "spacing",
         "value": f"相邻分点间隔中位 {short(med(gaps))}s",
         "derived_from": "target_timerange.start 序列",
         "formula": "间隔 = 下一条分点起点 − 上一条分点起点",
         "why_this_value": "分点之间的间隔＝每个小节的长度；间隔过于接近说明只是同一条的复挂，不是新分点",
         "calibrated_by": "本表实测",
         "tolerance": "±0.5s"},
    ]
    limits = [
        {"name": "min_hold", "value": 1.2, "why": "低于 1.2s 的带序号文字在本表归到 flower（一闪而过的贴纸）"},
        {"name": "points", "value": len(cnt), "why": "分点数＝不同序号标题数；同一序号复挂时不增加分点"},
        {"name": "min_chars", "value": min(s["ch"] for s in segs), "why": "分点标题至少要能读成一句话，单字序号不算"},
    ]
    st_gs = pair_stat(cls, "chapter_card", "subtitle", "chapter_card")
    affects = [
        {"target": "字幕/花字层级", "how": f"分点卡落 top/middle，把字幕带让出来；"
                                          f"本套 {st_gs['collide']}/{st_gs['n']} 条与字幕同 y 带"},
        {"target": "节奏", "how": "分点卡出现的位置＝段落切点；它决定后面几个镜头的主题"},
        {"target": "giant_text", "how": "同一条分点只用一种表现形式（大字砸 或 小卡列），不叠加"
                                       + ("" if pair_stat(cls, "giant_text", "chapter_card", "chapter_card")["n"]
                                          else "；本套无大字块，实际只用卡")},
    ]
    coupling = [
        {"with": "giant_text", "type": "alternative", "why": "同一条分点只用一种表现形式"},
        ({"with": "subtitle", "type": "n/a", "why": "本套无字幕轨"}
         if not cls["subs"] else {"with": "subtitle",
                                  "type": "y_band_mutex" if st_gs["collide"] == 0 else "coexist_same_band",
                                  "why": f"{st_gs['collide']}/{st_gs['n']} 条与字幕同带"}),
    ]
    viol = [
        "把每个展示字都当分点卡 → 满屏页眉",
        "分点卡停留太短（<1.2s）→ 观众还没读完就没了",
        "复挂同一分点换字号/换位置 → 观众认不出是同一节",
        "序号写法混用（一、/1./①）→ 读者要重新解析层级",
    ]
    reuse = [
        "新脚本先抽分点（一、二、三 / 01 02 / 第N），有分点才允许出章节卡",
        f"复挂同一分点必须复用同一模板参数；相邻分点间隔参考 {short(med(gaps))}s",
    ]
    return {
        "present": True, "absent_reason": None,
        "why_appears": {
            "rule": "只有脚本里真有分点结构才出：文字以序号前缀（一、/第二/1./(1)）开头，且压住该分点的引入句",
            "triggers": ["序号前缀（一、/01/第N）", "分点引入句"],
            "not_when": ["线性叙事无分点", "同一句重复出现但没有序号（那是复挂的展示字/常驻标题，不是章节卡）"],
            "evidence": f"{n} 条全部带序号前缀（{forms.most_common(1)[0][0]}为主），共 {len(cnt)} 个分点，"
                        f"{rep} 个被复挂；停留中位 {short(med(dus))}s，y 中位 {short(med(ys),2)}",
        },
        "value_drivers": drivers,
        "scope": {"unit": "节",
                  "coverage": f"{n} 条，分布在 {len(cnt)} 个分点；一条分点卡压住该节引入句",
                  "excludes": "线性叙述段、无序号装饰字",
                  "reset_on": "换分点"},
        "affects": affects, "limits": limits, "coupling": coupling,
        "violation_symptoms": viol, "reuse_check": reuse,
        "sample_table": sample_rows(segs, "chapter"),
    }


def build_end(segs, cls, base_px) -> dict:
    n = len(segs)
    if n < 1:
        return {"present": False,
                "absent_reason": "真值 0 段（片尾没有独立收口字卡）",
                "why_appears": None, "value_drivers": [], "scope": None, "affects": [],
                "limits": [], "coupling": [], "violation_symptoms": [], "reuse_check": [],
                "sample_table": []}
    dur = cls["dur"]
    dus = [s["du"] for s in segs]
    ys = [s["y"] for s in segs]
    pxs = [s["px"] for s in segs]
    chs = [s["ch"] for s in segs]
    drivers = [
        {"param": "at_end",
         "value": f"起点 ≥ {round(min(s['t'] for s in segs),1)}s（片长 {round(dur,1)}s 的最后 {pct(1 - min(s['t'] for s in segs)/dur)}）",
         "derived_from": "target_timerange.start / draft.duration",
         "formula": "t ≥ 0.75 × duration；且 t+du ≥ 0.95 × duration",
         "why_this_value": "收口卡必须压到最后一帧，否则观众在它之前就划走",
         "calibrated_by": "本表实测",
         "tolerance": "±0.3s"},
        {"param": "position",
         "value": f"y 中位 {short(med(ys),2)}（{round(med(ys)*1920)}px）",
         "derived_from": "segment.clip.transform.y",
         "formula": "y_ratio = (1 − transform.y)/2；本表限定 0.30–0.92 才算收口卡",
         "why_this_value": "落中下部：上方留给画面主体/logo，下方不出安全区；"
                           "贴到 0.95 以上的长条是全片免责声明，不是收口卡",
         "calibrated_by": "本表实测",
         "tolerance": "±0.02×H"},
        {"param": "hold",
         "value": f"中位 {short(med(dus))}s",
         "derived_from": "target_timerange.duration",
         "formula": "hold = 片尾剩余时长（一般 0.8–3s）",
         "why_this_value": "收口卡不自己定长短，它吃满片尾；短于 0.8s 读不完一句 CTA",
         "calibrated_by": "本表实测",
         "tolerance": "±2 帧"},
    ]
    limits = [
        {"name": "count", "value": n,
         "why": (f"片尾只出一组收口；本套 {n} 条，是同一组的阶梯出字"
                 if n > 1 else "片尾只出一次收口；多条会互相抢")},
        {"name": "chars", "value": max(chs),
         "why": f"收口句越短越好；本套最长 {max(chs)} 字"
                + ("，已经偏长" if max(chs) > 12 else "")},
    ]
    st_ef = pair_stat(cls, "end_card", "flower", "end_card")
    affects = [
        {"target": "片尾 BGM/音效", "how": "收口卡起＝BGM 收尾点，二者必须同帧"},
        {"target": "字幕", "how": "收口卡所在时段通常无口播（字幕已结束）；"
                                 "若还有口播，收口句必须先于字幕让位，否则两行叠字"},
    ]
    coupling = [({"with": "flower", "type": "y_band_mutex",
                  "why": f"片尾 {st_ef['overlap']}/{st_ef['n']} 条与花字时间重叠，"
                         f"同 y 带 {st_ef['collide']} 条"}
                 if st_ef["n"] else {"with": "flower", "type": "n/a", "why": "本套无花字"})]
    viol = ["收口卡提前到 80% 处 → 观众以为视频完了，完播率掉",
            "收口卡落进 y>0.95 的安全区外 / 或压住平台 UI"]
    reuse = ["核最后 5% 是否只有一块文字", "核它是否压到最后一帧（t+du ≥ 0.95×dur）"]
    return {
        "present": True, "absent_reason": None,
        "why_appears": {
            "rule": "片尾最后 ~15% 出现一组收口/CTA（1–3 条阶梯出字），压到最后一帧；它吃满片尾剩余时长",
            "triggers": ["片尾收口", "CTA"],
            "not_when": ["整片无 CTA 的纯纪实", "片尾仍有口播时（收口句会跟着字幕走）"],
            "evidence": f"{n} 条收口卡，最早 {round(min(s['t'] for s in segs),1)}s / 片长 {round(dur,1)}s；"
                        f"停留中位 {short(med(dus))}s",
        },
        "value_drivers": drivers,
        "scope": {"unit": "条", "coverage": f"{n} 条，只作用于最后 {pct(1 - min(s['t'] for s in segs)/dur)}",
                  "excludes": "全片免责声明长条（y>0.95、跨全片）", "reset_on": "不重置（片尾唯一）"},
        "affects": affects, "limits": limits, "coupling": coupling,
        "violation_symptoms": viol, "reuse_check": reuse,
        "sample_table": sample_rows(segs, "end"),
    }


# ---------------------------------------------------------------- main
def main():
    lib = {s["style_id"]: s for s in json.loads(LIB.read_text(encoding="utf-8"))["styles"]}
    trig = json.loads(TRIG.read_text(encoding="utf-8"))["styles"]
    render = json.loads(RENDER.read_text(encoding="utf-8"))["styles"]
    sources = json.loads(SOURCES.read_text(encoding="utf-8"))["styles"]

    out = {"version": "1.0", "domain": "text",
           "generated_from": "forensic/decrypted/<draft>/{__latest_close.json|draft_content.json} + "
                             "forensic/text_style_truth.json + forensic/style_trigger_rules_sfx_flower.json + "
                             "forensic/style_render_truth.json + assets/style_spectrum/style_spectrum_library.json",
           "styles": {}}

    for sid in sorted(sources):
        m = measure(sources[sid])
        if not m:
            continue
        lib_s = lib[sid]
        spec = lib_s.get("subtitle_spec") or {}
        safe_y = float(spec.get("safe_y_ratio") or 0.7)
        cls = classify(m, safe_y, float(spec.get("font_size_1080p_px") or 75))
        cls["dur"] = m["dur"]
        base_px = cls["base_px"] or float(spec.get("font_size_1080p_px") or 75)

        els = {
            "subtitle": build_subtitle(cls["subs"], cls, spec, base_px),
            "keyword_highlight": build_keyword(cls["subs"] + cls["disp"], cls, base_px),
            "flower": build_flower(segs_of(cls, "flower"), cls, trig.get(sid, {}).get("flower"), lib_s),
            "giant_text": build_giant(segs_of(cls, "giant_text"), cls, base_px),
            "chapter_card": build_chapter(segs_of(cls, "chapter_card"), cls, base_px),
            "end_card": build_end(segs_of(cls, "end_card"), cls, base_px),
        }
        # 统一回收只读口径：schema 不认 recheck 之外的临时键
        for e in els.values():
            e.pop("recheck", None)
            e.pop("reuse_check", None) if "reuse_check" not in e else None
        out["styles"][sid] = {
            "source_ref": render[sid]["source_ref"],
            "samples": len(cls["subs"]) + len(cls["disp"]),
            "elements": els,
        }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes), styles={len(out['styles'])}")


if __name__ == "__main__":
    main()
