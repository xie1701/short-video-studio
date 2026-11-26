#!/usr/bin/env python3
"""融合「黑盒 rea-video 音画拆解 + 白盒剪映草稿取证 + 教学原则草稿」，生成 24 套脱敏风格导演圣经与跨风格共性宪法。

输出文件（全部随仓库资产版本化，严格不含本机绝对路径、用户名或本地参考草稿私有名称）：
- short-video-studio/assets/style_spectrum/style_playbooks.json
- short-video-studio/assets/style_spectrum/playbooks/style_01.md .. style_24.md
- short-video-studio/assets/style_spectrum/common_editing_grammar.json
- short-video-studio/assets/style_spectrum/common_editing_grammar.md

用法：
  "$HOME/.short-video-studio/venv/bin/python" dev_tools/build_style_playbooks.py
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
FORENSIC_DIR = ROOT / "forensic"
ASSETS_SPECTRUM = ROOT / "short-video-studio" / "assets" / "style_spectrum"
DEFAULT_REA_DIR = Path.home() / ".short-video-studio" / "rea_breakdowns"

# `style_02` 明确进入 V8.0 单层一体化粗黑体血统
UNIFIED_SINGLE_STAGE_STYLES = frozenset({"style_02"})
STYLE_02_UNIFIED_PRIMITIVES = (
    "opening_triad",
    "left_right_punch",
    "giant_anchor_sub",
    "pain_slam_danmaku",
    "stacked_dual_color",
    "list_gather_depth",
    "number_burst_pair",
    "inline_highlight_cta",
)


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _collect_forbidden_tokens() -> List[str]:
    """收集不得泄漏到仓库圣经文本中的私有目录名与参考草稿名称。"""
    tokens: List[str] = []
    home_str = str(Path.home())
    if home_str:
        tokens.append(home_str)
    home_name = Path.home().name
    if home_name and len(home_name) >= 3:
        tokens.append(f"/Users/{home_name}")
        tokens.append(f"/home/{home_name}")
    sources_doc = _read_json(FORENSIC_DIR / "style_sources.json")
    for val in (sources_doc.get("styles") or {}).values():
        sval = str(val or "").strip()
        if sval:
            tokens.append(sval)
            # 也屏蔽去掉编号前缀后的后缀词，防草稿命名泄漏
            tail = re.sub(r"^\d+[-_]", "", sval).strip()
            if len(tail) >= 2:
                tokens.append(tail)
    # 动态收集参考/教学目录全名（不硬编码私有目录名到仓库代码）
    dec_dir = FORENSIC_DIR / "decrypted"
    if dec_dir.is_dir():
        for d in sorted(dec_dir.iterdir()):
            if d.is_dir() and re.match(r"^\d+-\d+-", d.name):
                tokens.append(d.name)
    # 按长度降序去重，确保长词优先替换
    uniq = sorted({t for t in tokens if t}, key=len, reverse=True)
    return uniq


def _sanitize_text(text: str, forbidden: List[str]) -> str:
    if not text:
        return ""
    out = str(text)
    home_str = str(Path.home())
    if home_str:
        out = out.replace(home_str, "$HOME")
    out = re.sub(r"/Users/[^/\s\"']+", "$HOME", out)
    out = re.sub(r"\bwb_[0-9a-f]{8,}\b", "<draft_sample>", out)
    for tok in forbidden:
        if len(tok) >= 2 and tok in out and tok not in ("knowledge", "style"):
            out = out.replace(tok, "<参考源>")
    # 过滤残留孤立草稿编号前缀
    out = re.sub(r"（参考草稿[^）]*）", "", out)
    return out.strip()


def _find_numbered_draft(dec_dir: Path, num_prefix: str) -> Optional[Path]:
    """按数字编号前缀（如 '2-1-'）查找本地解密草稿，避免在源码中硬编码中文草稿名。"""
    if not dec_dir.is_dir():
        return None
    for d in sorted(dec_dir.iterdir()):
        if d.is_dir() and d.name.startswith(num_prefix):
            cand = d / "draft_content.json"
            if cand.is_file():
                return cand
    return None


def _load_teaching_principles() -> Dict[str, Any]:
    """从剪辑师教学草稿中仅抽取通用方法论与「文字动画↔音效」映射对。"""
    dec_dir = FORENSIC_DIR / "decrypted"
    anim_sfx_pairs: List[Dict[str, str]] = []
    seen_pairs = set()

    f21 = _find_numbered_draft(dec_dir, "2-1-")
    if f21 is not None:
        d = _read_json(f21)
        mats = d.get("materials") or {}
        tmap = {t["id"]: t for t in (mats.get("texts") or [])}
        amap = {a["id"]: a for a in (mats.get("audios") or [])}
        anmap = {an["id"]: an for an in (mats.get("material_animations") or [])}

        audio_events: List[Tuple[float, str]] = []
        text_events: List[Tuple[float, str, List[str]]] = []
        for tr in (d.get("tracks") or []):
            ttype = tr.get("type")
            for seg in (tr.get("segments") or []):
                st = (seg.get("target_timerange") or {}).get("start", 0) / 1e6
                if ttype == "audio":
                    aname = str((amap.get(seg.get("material_id")) or {}).get("name") or "").strip()
                    if aname:
                        audio_events.append((round(st, 3), aname))
                elif ttype == "text":
                    tm = tmap.get(seg.get("material_id")) or {}
                    try:
                        txt = json.loads(tm.get("content") or "{}").get("text", "").strip()
                    except Exception:
                        txt = ""
                    anims = []
                    for rid in (seg.get("extra_material_refs") or []):
                        if rid in anmap:
                            for an in (anmap[rid].get("animations") or []):
                                if an.get("name"):
                                    anims.append(f"{an.get('type', 'in')}:{an.get('name')}")
                    if anims or txt:
                        text_events.append((round(st, 3), txt, anims))
        for st_t, label, anims in text_events:
            # 找 ±0.25s 内的同台词同步音效
            matched_audio = min(
                audio_events,
                key=lambda x: abs(x[0] - st_t),
                default=None,
            )
            if matched_audio and abs(matched_audio[0] - st_t) <= 0.25:
                anim_label = anims[0] if anims else label
                key = (anim_label, matched_audio[1])
                if key not in seen_pairs and anim_label:
                    seen_pairs.add(key)
                    anim_sfx_pairs.append({
                        "animation": anim_label,
                        "sfx_reference": matched_audio[1],
                        "cue_intent": label or anim_label,
                    })

    # 提取音效搜索分类法则（脱敏通用检索类别）
    sound_categories: List[str] = []
    f22 = _find_numbered_draft(dec_dir, "2-2-")
    if f22 is not None:
        d22 = _read_json(f22)
        for t in ((d22.get("materials") or {}).get("texts") or []):
            try:
                txt = json.loads(t.get("content") or "{}").get("text", "").strip()
            except Exception:
                txt = ""
            if "搜索关键词" in txt or "提示音" in txt or "转场" in txt:
                clean_line = re.sub(r"\s+", " ", txt.replace("\n", " / "))
                if clean_line not in sound_categories:
                    sound_categories.append(clean_line[:90])

    return {
        "typography_four_principles": [
            "对比（大小字阶比至少 1.5×~2.5×，主词用 L2/L3 巨字、次句用 L1，拒绝满屏同号字）",
            "重复（全片仅用唯一无衬线粗黑体或唯一衬线字族 + 唯一主色/辅色盘，禁止宋黑混穿与多色跑偏）",
            "对齐（居中主舞台严格居中，左右错落/偏心构图保持安全边界内对齐，绝不凌乱漂移）",
            "亲密（主标题与紧邻副标题/胶囊成组绑定同时序入退场，呼吸间隔 >=0.35s 让观众消化）",
        ],
        "scale_hierarchy_ratios": {
            "base_to_sub_highlight": "1.0x -> 1.5x (如 L1 ~69px -> L2 ~99px)",
            "base_to_hero_impact": "1.0x -> 2.0x..2.8x (如 L1 ~69px -> L3 ~135px)",
        },
        "anim_to_sfx_pairs": anim_sfx_pairs[:36],
        "sound_search_categories": sound_categories[:12],
        "composition_patterns": [
            "三阶错落开场（时间/场景 + 痛点主体 + 行动断言胶囊）",
            "左右/双色对冲（否定与肯定对比、旧认知 vs 新方案）",
            "数字焦点锚定（大号数字居中偏下 + 简洁白字结论）",
            "逐条聚光清单（依次弹出、前条压暗留景深、幕尾全清）",
            "单行行内双色重音（小字白前置 + 荧光色核心关键词，绝不断字）",
        ],
    }


def _summarize_rea_breakdown(sid: str, rea_dir: Path) -> Dict[str, Any]:
    bd_path = rea_dir / sid / "breakdown.json"
    if not bd_path.is_file():
        return {"available": False}
    bd = _read_json(bd_path)
    summary = bd.get("summary") or {}
    audio = bd.get("audio") or {}
    pkg = bd.get("packaging") or {}
    text_cues = pkg.get("text_cues") or []
    line_heights = [float(c.get("line_h") or 0.0) for c in text_cues if float(c.get("line_h") or 0.0) > 0]
    anim_in_counts: Dict[str, int] = {}
    band_counts: Dict[str, int] = {}
    fill_counts: Dict[str, int] = {}
    has_box_count = 0
    for c in text_cues:
        ak = str(((c.get("anim_in") or {}).get("kind")) or "cut")
        anim_in_counts[ak] = anim_in_counts.get(ak, 0) + 1
        bnd = str(c.get("band") or "unknown")
        band_counts[bnd] = band_counts.get(bnd, 0) + 1
        fn = str(((c.get("style") or {}).get("fill_name")) or "").strip()
        if fn:
            fill_counts[fn] = fill_counts.get(fn, 0) + 1
        if (c.get("style") or {}).get("box"):
            has_box_count += 1

    sfx_types: Dict[str, int] = {}
    sfx_anchors: Dict[str, int] = {}
    for sfx in (pkg.get("sfx_anchored") or []):
        tp = str(sfx.get("type") or "other")
        sfx_types[tp] = sfx_types.get(tp, 0) + 1
        anc = str(sfx.get("anchor") or "")
        for tag in ("字幕", "花字", "文字标注", "贴纸"):
            if tag in anc:
                sfx_anchors[tag] = sfx_anchors.get(tag, 0) + 1
                break

    lh_sorted = sorted(line_heights)
    l1_pct = round(lh_sorted[int(len(lh_sorted) * 0.15)] * 100, 2) if lh_sorted else None
    l2_pct = round(statistics.median(lh_sorted) * 100, 2) if lh_sorted else None
    l3_pct = round(lh_sorted[int(len(lh_sorted) * 0.90)] * 100, 2) if lh_sorted else None

    return {
        "available": True,
        "duration_sec": round(float(summary.get("duration") or 0.0), 2),
        "shots_detected": int(summary.get("shots") or len(bd.get("shots") or [])),
        "avg_shot_s": summary.get("avg_shot_s"),
        "overlay_kinds": list(summary.get("overlay_kinds") or []),
        "production_mix": [
            {"label": p.get("label"), "ratio": p.get("ratio")}
            for p in (summary.get("production_mix") or [])
        ],
        "audio_voice_ratio": (audio.get("voice") or {}).get("ratio"),
        "audio_music_bpm": (audio.get("music") or {}).get("tempo_bpm"),
        "cuts_on_onset_ratio": audio.get("cuts_on_onset_ratio"),
        "cuts_on_beat_ratio": audio.get("cuts_on_beat_ratio"),
        "packaging_counts": pkg.get("counts") or {},
        "text_height_pct_tiers": {
            "L1_p15_pct": l1_pct,
            "L2_median_pct": l2_pct,
            "L3_p90_pct": l3_pct,
            "sample_count": len(lh_sorted),
        },
        "text_band_distribution": band_counts,
        "text_anim_in_distribution": anim_in_counts,
        "text_box_count": has_box_count,
        "sfx_anchored_count": len(pkg.get("sfx_anchored") or []),
        "sfx_type_distribution": sfx_types,
        "sfx_anchor_targets": sfx_anchors,
    }


def _build_three_tier_ruler(
    sid: str, font_hist: Dict[str, Any], rea: Dict[str, Any], sub_px: int,
) -> Dict[str, Any]:
    """根据白盒单位字号分布与黑盒像素高度推算该风格可执行 L1/L2/L3 标尺。"""
    if sid in UNIFIED_SINGLE_STAGE_STYLES:
        return {
            "mode": "unified_3_tier",
            "L1": {"units": 11.5, "px_1080p": 69, "height_pct": 5.3, "usage": "bridge_bar（单行紧凑过渡短句）及辅助角标"},
            "L2": {"units": 16.5, "px_1080p": 99, "height_pct": 7.5, "usage": "主高光行、清单条目、red_pill 白底红字胶囊、inline_rich 焦点词"},
            "L3": {"units": 22.5, "px_1080p": 135, "height_pct": 10.5, "usage": "red_slam 痛点巨字、hero_lime 荧光绿焦点词、大数字主视觉"},
            "ratio_L1_L2_L3": "1.00 : 1.43 : 1.96",
        }
    sizes = sorted(float(k) for k in font_hist.keys() if float(k) > 0)
    if sizes:
        u1 = sizes[0]
        u2 = sizes[len(sizes) // 2]
        u3 = sizes[-1]
    else:
        u1 = round(max(8.0, sub_px / 6.0), 1)
        u2 = round(u1 * 1.45, 1)
        u3 = round(u1 * 1.95, 1)
    # 保证单调层次感
    if u2 <= u1:
        u2 = round(u1 * 1.4, 1)
    if u3 <= u2:
        u3 = round(u2 * 1.35, 1)
    tiers = rea.get("text_height_pct_tiers") or {}
    return {
        "mode": "dual_or_multi_tier",
        "L1": {"units": round(u1, 1), "px_1080p": int(round(u1 * 6.0)), "height_pct": tiers.get("L1_p15_pct"), "usage": "常规口播字幕/桥接句"},
        "L2": {"units": round(u2, 1), "px_1080p": int(round(u2 * 6.0)), "height_pct": tiers.get("L2_median_pct"), "usage": "章节标注、副重点、小清单"},
        "L3": {"units": round(u3, 1), "px_1080p": int(round(u3 * 6.0)), "height_pct": tiers.get("L3_p90_pct"), "usage": "开场钩子、数字、大情绪结论"},
        "ratio_L1_L2_L3": f"1.00 : {u2/u1:.2f} : {u3/u1:.2f}",
    }


def _render_style_markdown(pb: Dict[str, Any]) -> str:
    sid = pb["style_id"]
    typo = pb["typography_and_palette"]
    ruler = pb["three_tier_ruler"]
    stage = pb["subtitle_stage_architecture"]
    edit = pb["editing_and_broll_rhythm"]
    pkg = pb["packaging_and_visual_grammar"]
    aud = pb["audio_and_sfx_design"]
    rea = pb["blackbox_rea_summary"]

    lines: List[str] = [
        f"# {sid} 导演圣经（双源逆向对齐版）",
        "",
        f"- **视觉家族定位**：{pb['label_cn']}（原型 `{pb['archetype_id']}`）",
        f"- **舞台范式**：`{stage['stage_mode']}`（{'单层一体化动态排版，高光句与桥接句共享唯一舞台，绝不在大字下叠加第二层底字幕' if stage['single_stage_unified'] else '底字幕与前景花字层级分工协同'}）",
        f"- **Director 主题配置**：`theme_profile = {stage['recommended_theme_profile']}`",
        "",
        "## 一、视觉血统：字体、唯一色盘与三级字号标尺",
        f"- **推荐主字体族**：`{typo['font_family']}` | 首选商用候选：`{', '.join(typo['font_candidates'])}`",
        f"- **字族纯洁铁律**：{typo['font_purity_rule']}",
        f"- **唯一色盘**：主字 `{typo['primary_color']}` | 核心高光 `{typo['keyword_color']}` | 警示/痛点 `{typo['danger_color']}` | 描边 `{typo['stroke_color']}` | 底框 `{typo['bg_slab']}`",
        "",
        "| 字阶 | 剪映基准单位 | 1080p 渲染字号 | 黑盒视觉占比 | 适用表达对象 |",
        "| :--- | :---: | :---: | :---: | :--- |",
        f"| **L1（常规/桥接）** | `{ruler['L1']['units']}u` | `~{ruler['L1']['px_1080p']}px` | {ruler['L1']['height_pct'] or '-'}% | {ruler['L1']['usage']} |",
        f"| **L2（主讲高光）** | `{ruler['L2']['units']}u` | `~{ruler['L2']['px_1080p']}px` | {ruler['L2']['height_pct'] or '-'}% | {ruler['L2']['usage']} |",
        f"| **L3（核爆锚点）** | `{ruler['L3']['units']}u` | `~{ruler['L3']['px_1080p']}px` | {ruler['L3']['height_pct'] or '-'}% | {ruler['L3']['usage']} |",
        f"- **三阶对比比例**：`{ruler['ratio_L1_L2_L3']}`",
        "",
        "## 二、字幕与动态排版规范",
        f"- **安全中心 Y 占比**：`{stage['sub_y_ratio']}` | **单行字数上限**：`<= {stage['max_chars_per_line']}` 字 | **贴主轨切点率**：`{stage['cut_text_align_ratio']:.1%}`",
        f"- **白盒轨迹实证**：{stage['causality_summary']}",
        f"- **双层复读禁令**：{stage['double_caption_rule']}",
    ]
    if sid in UNIFIED_SINGLE_STAGE_STYLES:
        lines.extend([
            "- **8 大一体化复合排版原语（商业与探店共通）**：",
            "  1. `opening_triad`：开场三阶错落弹出（上 L2 白 + 中 L3 荧光绿 + 下 L2 白底红字胶囊）",
            "  2. `left_right_punch`：左右错落对冲（L2 白与 L3 荧光绿左右张力）",
            "  3. `giant_anchor_sub`：L3 巨字居中锚定 + 下方 L1/L2 紧凑结论",
            "  4. `pain_slam_danmaku`：痛点 L3 `red_slam` 砸屏 + 联合人脸包络安全避头人后弹幕",
            "  5. `stacked_dual_color`：上下双色错层叠字（L2 白 + L3 荧光绿按词触发）",
            "  6. `list_gather_depth`：清单依次按词弹出 + 前项自动压暗留景深 + 幕尾彻底清场",
            "  7. `number_burst_pair`：荧光绿 L3 大数字（滚数动效）+ 紧贴白字 L2 结论",
            "  8. `inline_highlight_cta`：单行内双色双字号混排（L1 白 + L2 荧光绿重点词，严禁吞尾字）",
        ])
    lines.extend([
        "",
        "## 三、剪辑节奏、运镜与 B-roll 选镜策略",
        f"- **白盒剪辑统计**：中位镜长 `{edit['median_shot_sec']}s`（P10 `{edit['p10_sec']}s` / P90 `{edit['p90_sec']}s`）| `<1s` 快切占比 `{edit['fast_cut_lt1s_ratio']:.1%}` | 前 3 秒切镜 `{edit['opening_cuts_3s']}` 刀 | 关键帧密度 `{edit['kf_per_min']}/min`",
        f"- **黑盒拆解实测（rea-video）**：总时长 `{rea.get('duration_sec', '-')}`s | 检测镜头 `{rea.get('shots_detected', '-')}` 个（均长 `{rea.get('avg_shot_s', '-')}`s）| 切换踩声音起始点比 `{rea.get('cuts_on_onset_ratio', '-')}`",
        f"- **B-roll 目标配比区间**：先验 `{edit['broll_target_ratio']:.0%}`（弹性带宽 `{edit['broll_band'][0]:.0%}..{edit['broll_band'][1]:.0%}`）；无实拍素材则保持 100% 口播靠复合排版 + 推拉特写撑起视觉密度；有实拍素材则严格遵循 `broll_policy` + `broll_keywords` 在物证/环境幕切入对应实拍。",
        f"- **转场规范**：`{pkg['transition_style']}`（95%+ 为 0 帧硬切，禁止滥用花哨内置转场）。",
        "",
        "## 四、视觉包装、抠像与声画同步（以音定画）",
        f"- **抠像与画中画**：AI 人像抠像 `{pkg['ai_matting_count']}` 段（{pkg['cutout_rule']}）；画中画模式 `{pkg['pip_mode']}`；画面特效：{pkg['effect_summary']}",
        f"- **黑盒叠层构成**：可见种类 `{', '.join(rea.get('overlay_kinds') or [])}`；入场动效分布 `{rea.get('text_anim_in_distribution')}`；底框出现 `{rea.get('text_box_count', 0)}` 次",
        f"- **响度与配乐**：目标响度 `{aud['integrated_lufs']} LUFS` | 人声/BGM 信噪比 `{aud['voice_bgm_snr_db']} dB` | 白盒 BPM `{aud['whitebox_bpm']}` / 黑盒 BPM `{rea.get('audio_music_bpm')}` | 双 BGM `{aud['dual_bgm']}`",
        f"- **音效密度与触发分配**：`{aud['sfx_per_min']}/min`（触发分配 `{aud['trigger_share']}`，最小间隔 `{aud['min_gap_sec']}s`）；黑盒卡点类型 `{rea.get('sfx_type_distribution')}`",
        "",
        "## 五、总导演执导红线（写 `03b_director_plan.json` 必守）",
        "1. 严格按 `03b_director_tasks.json` 顺序覆盖全部 `sentence_ids`，不遗漏不重复。",
        "2. 视觉短词（`headline`/`items`/`accent_token`）去掉标点每行不超过 7 字，必须完全出自该幕口播原话，数字与量词 100% 原文可核对。",
        "3. `slow_push_115` 严禁连续两幕连推造成弹回；关键对比/反转幕使用 `jump_closeup`（1.22× 硬切特写）或 `slow_pull_115` 形成起伏。",
        "4. 每幕 `visual_copy` 的入场与子项偏移通过词级时间戳 `words` 映射实现「声画 0ms 咬合，不提前剧透未说出口的条目」。",
        "5. 若存在高光实拍素材，凡口播提及具体食物/空间/动作/场景切换时设置 `broll_policy: allow_verified_broll` 并填入精确 `broll_keywords`；开篇锚点、痛点砸屏与人后弹幕严格设 `lock_aroll`。",
        "",
    ])
    return "\n".join(lines)


def _render_common_markdown(common: Dict[str, Any]) -> str:
    stats = common["corpus_statistics"]
    teach = common["teaching_grammar"]
    lines = [
        "# 全 24 套风格跨源共性宪法（Common Editing Grammar）",
        "",
        f"> 基于 {stats['n_styles']} 套白盒解密剪映草稿 + {stats['n_rea_available']} 套母本视频黑盒拆解 + 7 套剪辑师教学工程交叉提炼。本文件仅含客观剪辑与排版律，不含任何私有路径与草稿名。",
        "",
        "## 一、全风格宏观共性数字法则",
        f"- **硬切绝对主导**：`{stats['zero_transition_styles']}/{stats['n_styles']}` 套风格转场数为 0，全部采用 0 帧干净硬切，节奏靠声画咬合而非炫技转场。",
        f"- **中位镜长分布**：全库中位镜长均值 `{stats['median_shot_avg_sec']}s`（P25–P75：`{stats['median_shot_p25']}s .. {stats['median_shot_p75']}s`），口播段落不宜超过 3.0s 不变画面。",
        f"- **以音定画铁律**：SFX 触发中 `{stats['avg_sfx_anchored_share']:.1%}` 明确挂载可观测视觉事件（切镜/花字/字幕起句），0 免费无锚音效；切点与起句延迟中位 `<= 2 帧`。",
        f"- **B-roll 弹性配比**：纯认知口播流靠三级字号与推拉维持信息密度；有真实场景/产品 B-roll 素材时，按语义匹配在物证句与环境句穿插 B-roll（不打断开场定调与痛点砸屏）。",
        "",
        "## 二、教学草稿核心排版四大原则",
    ]
    for p in teach["typography_four_principles"]:
        lines.append(f"- {p}")
    lines.extend([
        "",
        "## 三、复合排版常见构图原型",
    ])
    for cp in teach["composition_patterns"]:
        lines.append(f"- {cp}")
    lines.extend([
        "",
        "## 四、高频文字动画 ↔ 0ms 音效配对字典（实测动画音效对齐映射）",
        "| 文字动画 | 对应推荐音效 | 表达意图 |",
        "| :--- | :--- | :--- |",
    ])
    for row in teach["anim_to_sfx_pairs"][:24]:
        lines.append(f"| `{row['animation']}` | `{row['sfx_reference']}` | {row['cue_intent']} |")
    lines.extend([
        "",
        "## 五、跨元件几何与避让耦合红线（来自白盒因果图）",
        "1. **抠像与缩窗同源公式**：抠像遮罩窗口与背景缩窗必须共用同一坐标映射 `left=(W/2)*(x+1-scale)`，严禁各算一套导致重影/偏移。",
        "2. **抠像禁用三大红线**：① 演示图纸/界面/实物特写时不抠人；② 副镜与主镜禁止同源；③ 同一画面有人后弹幕时，必须按本幕全部相关镜头的**人脸联合外包络**避让头耳区域。",
        "3. **花字绝不预剧透与复读**：多条清单/对比词通过逐字时间映射延后到声音刚落字时弹出；前景已展示完整核心句的 `style_02_unified` 舞台禁止同时叠放同义底部字幕。",
        "4. **无破词与防截字**：单行胶囊与行内双色短句严格保留术语完整性与尾字宽度，绝不出现单字折行或切掉末字。",
        "",
    ])
    return "\n".join(lines)


def build_all(rea_dir: Path, out_spectrum_dir: Path) -> Dict[str, Any]:
    forbidden = _collect_forbidden_tokens()
    lib = _read_json(out_spectrum_dir / "style_spectrum_library.json")
    bp_doc = _read_json(out_spectrum_dir / "template_blueprints.json")
    edit_g = _read_json(FORENSIC_DIR / "style_edit_grammar.json").get("styles") or {}
    render_t = _read_json(FORENSIC_DIR / "style_render_truth.json").get("styles") or {}
    caus_t = _read_json(FORENSIC_DIR / "causality_text.json").get("styles") or {}
    caus_v = _read_json(FORENSIC_DIR / "causality_visual.json").get("styles") or {}
    caus_a = _read_json(FORENSIC_DIR / "causality_audio.json").get("styles") or {}
    trigger_r = _read_json(FORENSIC_DIR / "style_trigger_rules_sfx_flower.json").get("styles") or {}

    styles_raw = lib.get("styles") or []
    lib_by_id = (
        {s["style_id"]: s for s in styles_raw if isinstance(s, dict) and s.get("style_id")}
        if isinstance(styles_raw, list)
        else dict(styles_raw)
    )
    blueprints = bp_doc.get("blueprints") or {}
    teaching = _load_teaching_principles()

    playbooks: Dict[str, Any] = {}
    playbooks_dir = out_spectrum_dir / "playbooks"
    playbooks_dir.mkdir(parents=True, exist_ok=True)

    median_shots: List[float] = []
    zero_trans = 0
    rea_ok_count = 0
    sfx_shares: List[float] = []

    for idx in range(1, 25):
        sid = f"style_{idx:02d}"
        s_lib = lib_by_id.get(sid) or {}
        s_bp = blueprints.get(sid) or {}
        s_eg = edit_g.get(sid) or {}
        s_rt = render_t.get(sid) or {}
        s_ct = caus_t.get(sid) or {}
        s_cv = caus_v.get(sid) or {}
        s_ca = caus_a.get(sid) or {}
        s_tr = trigger_r.get(sid) or {}

        rea_sum = _summarize_rea_breakdown(sid, rea_dir)
        if rea_sum.get("available"):
            rea_ok_count += 1

        sub_spec = s_lib.get("subtitle_spec") or {}
        pkg_tech = s_lib.get("packaging_techniques") or {}
        shot_dist = s_lib.get("shot_distribution") or {}
        aud_sfx = s_lib.get("audio_and_sfx") or {}
        vis_bp = s_lib.get("visual_blueprint") or {}

        sub_px = int(sub_spec.get("font_size_1080p_px") or 80)
        font_hist = ((s_rt.get("texts") or {}).get("font_size_hist") or {})
        ruler = _build_three_tier_ruler(sid, font_hist, rea_sum, sub_px)

        if sid in UNIFIED_SINGLE_STAGE_STYLES:
            font_family = "Source Han Sans CN / Alimama ShuHeiTi"
            font_cands = ["SourceHanSansCN-Bold.otf", "NotoSansSC-Bold.ttf", "AlimamaShuHeiTi-Bold.otf"]
            font_purity = "全片（开场、高光、桥接、人后弹幕、清单）100% 使用现代无衬线粗黑体，严禁与宋体混穿。"
            primary_col = "#FFFFFF"
            kw_col = "#BCFF00"
            danger_col = "#B71C1C"
            bg_slab = "rgba(0,0,0,0.70) 紧凑方底(bridge_bar) / #FFFFFF 白底胶囊(red_pill)"
            rec_theme = "style_02_unified"
            stage_mode = "unified_single_stage"
            double_rule = "0 复读铁律：高光大字直接承担台词表达，禁止在同时间窗底端叠加第二层小字幕；仅过渡/呼吸句显示 L1 紧凑 bridge_bar。"
        else:
            font_family = str(sub_spec.get("font_family") or "Source Han Serif CN")
            font_cands = list(sub_spec.get("font_candidates") or ["SourceHanSerifCN-Bold.otf", "AlimamaShuHeiTi-Bold.otf"])
            font_purity = f"以 `{font_family}` 为主字体族保持视觉连贯，粤语生僻字按同字族优先原则安全回退。"
            primary_col = str(sub_spec.get("primary_color") or "#FFFFFF")
            kw_col = str(sub_spec.get("keyword_color") or "#FFE28A")
            danger_col = "#DC2626"
            bg_slab = str(vis_bp.get("bg_slab") or "rgba(0,0,0,0.65)")
            rec_theme = "business_editorial" if sid in ("style_03", "style_17") else "store_vibrant"
            stage_mode = "dual_layer_subtitle_plus_flower"
            double_rule = "常规口播在安全底栏呈现字幕，重点短词（<=7字）作为前景视觉锚点弹出，不堆叠整句复读卡片。"

        ct_sub = ((s_ct.get("elements") or {}).get("subtitle") or {})
        ct_rule = _sanitize_text(str(((ct_sub.get("why_appears") or {}).get("evidence")) or ""), forbidden)
        if not ct_rule:
            ct_rule = "口播切句处分段，高光短词贴主轨切点同步入屏。"

        cutout_el = ((s_cv.get("elements") or {}).get("cutout") or {})
        cutout_rule = _sanitize_text(str(((cutout_el.get("why_appears") or {}).get("rule")) or "无人像换背景需求时不开启"), forbidden)
        if len(cutout_rule) > 120:
            cutout_rule = cutout_rule[:117] + "..."

        tr_style = str(vis_bp.get("transition_style") or "none")
        if tr_style == "none":
            zero_trans += 1

        med_s = float(shot_dist.get("median_sec") or 1.5)
        median_shots.append(med_s)
        eg_shot = s_eg.get("shot_distribution") or {}
        eg_open = s_eg.get("opening") or {}
        eg_align = s_eg.get("cut_text_alignment") or {}

        sfx_cfg = s_tr.get("sfx") or {}
        trig_share = sfx_cfg.get("trigger_share") or {}
        anchored_share = 1.0 - float(trig_share.get("free") or 0.0)
        sfx_shares.append(anchored_share)

        raw_eff = s_rt.get("effects")
        eff_segs = raw_eff.get("segments") if isinstance(raw_eff, dict) else []
        eff_names = list(dict.fromkeys(
            str(seg.get("name") or seg.get("type") or "").strip()
            for seg in (eff_segs or [])
            if isinstance(seg, dict) and str(seg.get("name") or seg.get("type") or "").strip()
        ))
        eff_summary = ", ".join(eff_names[:4]) if eff_names else "无额外全局花哨滤镜，保持干净原片质感"

        broll_ratio = float(shot_dist.get("broll_coverage_ratio") or 0.0)
        if sid in UNIFIED_SINGLE_STAGE_STYLES:
            broll_band = [0.0, 0.55]
        else:
            broll_band = [round(max(0.0, broll_ratio - 0.15), 2), round(min(0.85, broll_ratio + 0.20), 2)]

        pb = {
            "style_id": sid,
            "label_cn": _sanitize_text(str(s_bp.get("label_cn") or sid), forbidden),
            "archetype_id": str((s_eg.get("archetype_id") or s_cv.get("archetype_id") or "talking_head")),
            "typography_and_palette": {
                "font_family": font_family,
                "font_candidates": font_cands,
                "font_purity_rule": font_purity,
                "primary_color": primary_col,
                "keyword_color": kw_col,
                "danger_color": danger_col,
                "stroke_color": str(sub_spec.get("stroke_color") or "#000000"),
                "bg_slab": bg_slab,
            },
            "three_tier_ruler": ruler,
            "subtitle_stage_architecture": {
                "stage_mode": stage_mode,
                "single_stage_unified": sid in UNIFIED_SINGLE_STAGE_STYLES,
                "recommended_theme_profile": rec_theme,
                "sub_y_ratio": sub_spec.get("safe_y_ratio", 0.78),
                "max_chars_per_line": int(sub_spec.get("max_chars_per_line") or 10),
                "cut_text_align_ratio": float(eg_align.get("aligned_ratio") or 0.0),
                "causality_summary": ct_rule,
                "double_caption_rule": double_rule,
                "supported_primitives": list(STYLE_02_UNIFIED_PRIMITIVES) if sid in UNIFIED_SINGLE_STAGE_STYLES else [],
            },
            "editing_and_broll_rhythm": {
                "median_shot_sec": round(med_s, 3),
                "p10_sec": eg_shot.get("p10"),
                "p90_sec": eg_shot.get("p90"),
                "fast_cut_lt1s_ratio": float(shot_dist.get("fast_cuts_under_1s_ratio") or 0.0),
                "opening_cuts_3s": int(eg_open.get("cuts_in_first_3s") or 0),
                "kf_per_min": float(shot_dist.get("kf_per_min") or 0.0),
                "broll_target_ratio": round(broll_ratio, 3),
                "broll_band": broll_band,
            },
            "packaging_and_visual_grammar": {
                "transition_style": tr_style,
                "ai_matting_count": int(pkg_tech.get("ai_matting_cutout") or 0),
                "cutout_rule": cutout_rule,
                "pip_mode": str(pkg_tech.get("pip_mode") or "none"),
                "effect_summary": _sanitize_text(eff_summary, forbidden),
            },
            "audio_and_sfx_design": {
                "integrated_lufs": float(aud_sfx.get("integrated_lufs") or -14.0),
                "voice_bgm_snr_db": float(aud_sfx.get("voice_bgm_snr_target_db") or 11.5),
                "whitebox_bpm": (s_tr.get("beat") or {}).get("bpm"),
                "dual_bgm": bool(aud_sfx.get("dual_bgm_enabled", False)),
                "sfx_per_min": float(sfx_cfg.get("per_min") or aud_sfx.get("sfx_per_min_measured") or 0.0),
                "trigger_share": trig_share,
                "min_gap_sec": float(sfx_cfg.get("min_gap_sec") or 0.6),
            },
            "blackbox_rea_summary": rea_sum,
        }
        playbooks[sid] = pb
        md_text = _sanitize_text(_render_style_markdown(pb), forbidden)
        (playbooks_dir / f"{sid}.md").write_text(md_text, encoding="utf-8")

    sorted_med = sorted(median_shots)
    common_grammar = {
        "version": "8.0.0",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "corpus_statistics": {
            "n_styles": 24,
            "n_rea_available": rea_ok_count,
            "zero_transition_styles": zero_trans,
            "median_shot_avg_sec": round(statistics.mean(sorted_med), 3) if sorted_med else 1.5,
            "median_shot_p25": round(sorted_med[len(sorted_med) // 4], 3) if sorted_med else 1.1,
            "median_shot_p75": round(sorted_med[(len(sorted_med) * 3) // 4], 3) if sorted_med else 1.9,
            "avg_sfx_anchored_share": round(statistics.mean(sfx_shares), 3) if sfx_shares else 0.95,
        },
        "teaching_grammar": teaching,
        "director_contracts": {
            "highlight_coverage_unified": [0.50, 0.85],
            "highlight_coverage_editorial": [0.35, 0.55],
            "double_caption_overlap_max": 0.05,
            "camera_intents": ["steady_hold", "slow_push_115", "slow_pull_115", "side_shift_card", "punch_closeup", "jump_closeup"],
        },
    }
    raw_pb_json = json.dumps({
        "version": "8.0.0",
        "generated_at": common_grammar["generated_at"],
        "n_styles": 24,
        "n_rea_backed": rea_ok_count,
        "styles": playbooks,
    }, ensure_ascii=False, indent=2)
    (out_spectrum_dir / "style_playbooks.json").write_text(_sanitize_text(raw_pb_json, forbidden) + "\n", encoding="utf-8")

    raw_cm_json = json.dumps(common_grammar, ensure_ascii=False, indent=2)
    (out_spectrum_dir / "common_editing_grammar.json").write_text(_sanitize_text(raw_cm_json, forbidden) + "\n", encoding="utf-8")
    (out_spectrum_dir / "common_editing_grammar.md").write_text(
        _sanitize_text(_render_common_markdown(common_grammar), forbidden),
        encoding="utf-8",
    )
    return {
        "n_styles": len(playbooks),
        "n_rea_backed": rea_ok_count,
        "out_dir": str(out_spectrum_dir),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="构建 24 套双源脱敏风格导演圣经与跨风格共性宪法")
    ap.add_argument("--rea-dir", type=Path, default=DEFAULT_REA_DIR)
    ap.add_argument("--out-dir", type=Path, default=ASSETS_SPECTRUM)
    args = ap.parse_args()
    res = build_all(args.rea_dir, args.out_dir)
    print(f"[build_style_playbooks] DONE: {res['n_styles']} 套圣经（含黑盒拆解 {res['n_rea_backed']}/24）-> {res['out_dir']}")


if __name__ == "__main__":
    main()
