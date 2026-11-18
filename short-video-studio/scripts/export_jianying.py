#!/usr/bin/env python3
"""
[出片 2：剪映专业版可编辑工程导出封装]
由于剪映专业版 6.0+（含 macOS 11.7.0）对内部 draft_info.json 强制启用私有加密，
直接复制第三方明文 draft_content.json 会被判定为草稿损坏无法打开。
本模块遵循剪映官方原生开放导入规范，导出标准可编辑工程包 (jianying_draft/)：
- <project_name>.fcpxml : Apple FCPXML 1.10 多轨工程（主视频精剪分镜 + 纯净人声/配乐/音效三轨独立 WAV）
- <project_name>.srt    : 毫秒级精准可编辑字幕（兼容剪映“本地字幕”一键拖拽生成文字轨）
- <project_name>.ass    : 高级样式字幕（若步骤 5 已生成则同步附带）
- overlays/             : 1080x1920 全画幅透明定位花字/卡片贴图（按起止时间命名）
- <lut_name>.cube       : 风格专属 3D LUT（若存在则同步附带，可在剪映中导入调色）
- README_剪映导入指南.txt
- legacy_v5_json/       : 留存旧版 5.9 协议精简 JSON 骨架（仅作归档，勿直接导入剪映 6.0+）
"""

from __future__ import annotations

import argparse
import shutil
import time
import uuid
import wave
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from xml.dom import minidom

from PIL import Image

from common import (
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    DEFAULT_FPS,
    ffprobe_media,
    maybe_reexec_in_venv,
    read_json,
    write_json,
)


def sec_to_us(sec: float) -> int:
    return int(round(sec * 1_000_000))


def _frames_to_rational(frames: int, fps: int) -> str:
    """将整型帧数转为 FCPXML 标准有理数时间（严格帧对齐，0 帧返回 '0s'）。"""
    if frames <= 0:
        return "0s"
    return f"{frames * 100}/{fps * 100}s"


def _measure_wav_frames(path_str: Optional[str], fps: int) -> Tuple[int, float]:
    """实测 WAV 真实采样时长与对应整数帧数。"""
    if not path_str:
        return 0, 0.0
    p = Path(path_str)
    if p.exists() and p.suffix.lower() == ".wav":
        try:
            with wave.open(str(p), "rb") as wf:
                dur = wf.getnframes() / float(wf.getframerate())
                return max(1, int(dur * fps)), dur
        except Exception:
            pass
    return 0, 0.0


def _measure_video_frames(path: Path, fps: int, fallback_frames: int) -> int:
    """实测 MP4 真实可播时长对应的最大整帧数，防止越界造成媒体损坏/黑帧。"""
    try:
        meta = ffprobe_media(path)
        fmt_dur = float(meta.get("format", {}).get("duration") or 0.0)
        if fmt_dur > 0.05:
            return max(1, int(fmt_dur * fps + 1e-4))
    except Exception:
        pass
    return max(1, fallback_frames)


def _format_srt_ts(sec: float) -> str:
    ms_total = max(0, int(round(sec * 1000.0)))
    h, rem = divmod(ms_total, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _build_srt_content(subs: Dict[str, Any]) -> str:
    lines: List[str] = []
    entries = sorted(subs.get("entries", []), key=lambda x: float(x.get("start_sec", 0.0)))
    prev_end = 0.0
    for idx, entry in enumerate(entries, start=1):
        txt = str(entry.get("text", "")).strip()
        if not txt:
            continue
        st = max(prev_end, float(entry.get("start_sec", 0.0)))
        en = max(st + 0.05, float(entry.get("end_sec", st + 0.5)))
        prev_end = en
        lines.extend([
            str(idx),
            f"{_format_srt_ts(st)} --> {_format_srt_ts(en)}",
            txt,
            "",
        ])
    return "\n".join(lines)


def _export_full_canvas_overlays(pipeline_dir: Path, overlays_dir: Path, fps: int) -> List[Path]:
    """将渲染阶段产出的局部花字与阶段卡 PNG 统一贴合到 1080x1920 透明全画幅底图导出。"""
    pkg_path = pipeline_dir / "06_packaging.json"
    if not pkg_path.exists():
        return []
    pkg = read_json(pkg_path)
    render_final = pipeline_dir / "_render_final"
    stage_assets = pipeline_dir / "_stage_assets"
    overlays_dir.mkdir(parents=True, exist_ok=True)
    for old in overlays_dir.glob("*.png"):
        old.unlink()

    exported: List[Path] = []
    items = list(pkg.get("stage_overlays", [])) + list(pkg.get("visual_elements", []))
    for idx, elem in enumerate(items, start=1):
        eid = str(elem.get("id", f"elem_{idx:03d}"))
        # 优先取 _render_final 中渲染好的透明 PNG，其次取 custom_png / _stage_assets
        candidates = [
            render_final / f"{eid}.png",
            Path(str(elem.get("custom_png", ""))) if elem.get("custom_png") else Path("__none__"),
            stage_assets / f"{eid}.png",
        ]
        src_png = next((c for c in candidates if c.is_file()), None)
        if not src_png:
            continue
        sf = int(elem.get("start_frame", 0))
        ef = max(sf + 1, int(elem.get("end_frame", sf + fps)))
        out_name = f"{idx:02d}_{sf / fps:06.2f}s-{ef / fps:06.2f}s_{eid}.png"
        out_p = overlays_dir / out_name

        with Image.open(src_png).convert("RGBA") as src_im:
            if src_im.size == (CANVAS_WIDTH, CANVAS_HEIGHT):
                src_im.save(out_p)
            else:
                bbox = elem.get("bbox", {})
                px = int(bbox.get("x", max(0, (CANVAS_WIDTH - src_im.width) // 2)))
                py = int(bbox.get("y", max(0, (CANVAS_HEIGHT - src_im.height) // 2)))
                canvas = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (0, 0, 0, 0))
                canvas.paste(src_im, (px, py), src_im)
                canvas.save(out_p)
        exported.append(out_p)
    return exported


def build_fcpxml_tree(pipeline_dir: Path, project_name: str) -> str:
    """
    构建严格符合 Apple FCPXML 1.10 DTD 标准的剪映可导入多轨工程：
    - Primary Storyline (<spine>): 全 46 段精剪/调色纯无声分镜 (<asset-clip>)
    - Connected Audio Lanes (-1 / -2 / -3): 独立纯净人声、风格 BGM、精准 SFX 音轨
    """
    edl = read_json(pipeline_dir / "04_edl.json")
    audio_events = read_json(pipeline_dir / "03_audio_events.json")
    mix_metrics = read_json(pipeline_dir / "08_mix_metrics.json")

    fps = int(edl.get("fps", DEFAULT_FPS))
    total_frames = int(edl.get("total_frames") or round(float(edl["total_duration_sec"]) * fps))
    render_final_dir = pipeline_dir / "_render_final"

    fcpxml = ET.Element("fcpxml", version="1.10")
    resources = ET.SubElement(fcpxml, "resources")

    fmt_id = "r_fmt_9x16"
    ET.SubElement(
        resources,
        "format",
        id=fmt_id,
        name="FFVideoFormatRateUndefined",
        frameDuration=_frames_to_rational(1, fps),
        width=str(CANVAS_WIDTH),
        height=str(CANVAS_HEIGHT),
        colorSpace="1-1-1 (Rec. 709)",
    )

    # 注册视频资产（优先使用无音频流的 _render_final/seg_xxx.mp4，已保留各风格色彩与边窗框）
    asset_cache: Dict[str, Tuple[str, int, bool]] = {}
    res_counter = 1

    def ensure_video_asset(media_path: Path, need_frames: int) -> Tuple[str, int, bool]:
        nonlocal res_counter
        key = str(media_path.resolve())
        if key in asset_cache:
            return asset_cache[key]
        res_counter += 1
        rid = f"r_v_{res_counter}"
        meta = ffprobe_media(media_path)
        has_audio = any(s.get("codec_type") == "audio" for s in meta.get("streams", []))
        fmt_dur = float(meta.get("format", {}).get("duration") or 0.0)
        avail_frames = max(need_frames, int(fmt_dur * fps + 1e-4)) if fmt_dur > 0.05 else need_frames
        attrs = {
            "id": rid,
            "name": media_path.name,
            "start": "0s",
            "duration": _frames_to_rational(avail_frames, fps),
            "hasVideo": "1",
            "hasAudio": "1" if has_audio else "0",
            "format": fmt_id,
        }
        if has_audio:
            attrs.update({"audioSources": "1", "audioChannels": "2", "audioRate": "48000"})
        asset_el = ET.SubElement(resources, "asset", **attrs)
        ET.SubElement(asset_el, "media-rep", kind="original-media", src=media_path.resolve().as_uri())
        asset_cache[key] = (rid, avail_frames, has_audio)
        return asset_cache[key]

    def register_audio_asset(wav_path: Optional[str], label: str) -> Optional[Tuple[str, int]]:
        nonlocal res_counter
        if not wav_path:
            return None
        p = Path(wav_path)
        if not p.is_file():
            return None
        n_frames, _ = _measure_wav_frames(str(p), fps)
        if n_frames <= 0:
            return None
        res_counter += 1
        rid = f"r_a_{res_counter}"
        asset_el = ET.SubElement(
            resources,
            "asset",
            id=rid,
            name=label,
            start="0s",
            duration=_frames_to_rational(n_frames, fps),
            hasVideo="0",
            hasAudio="1",
            audioSources="1",
            audioChannels="2",
            audioRate="48000",
        )
        ET.SubElement(asset_el, "media-rep", kind="original-media", src=p.resolve().as_uri())
        return rid, n_frames

    # 注册三条完全解耦的可编辑音轨：纯净降噪人声 + BGM + 音效轨
    voice_path = next(
        (cand for cand in [
            str(pipeline_dir / "08_voice_denoised.wav"),
            str(pipeline_dir / "03_locked_voice.wav"),
            mix_metrics.get("master_wav"),
        ] if cand and Path(cand).is_file()),
        None,
    )
    voice_res = register_audio_asset(voice_path, "01_纯净人声干音")
    bgm_res = register_audio_asset(audio_events.get("locked_bgm_wav"), "02_背景配乐BGM")
    sfx_res = register_audio_asset(str(pipeline_dir / "06_sfx_track.wav"), "03_切镜音效SFX")

    library = ET.SubElement(fcpxml, "library")
    event = ET.SubElement(library, "event", name=project_name)
    project = ET.SubElement(event, "project", name=project_name)
    sequence = ET.SubElement(
        project,
        "sequence",
        format=fmt_id,
        duration=_frames_to_rational(total_frames, fps),
        tcStart="0s",
        tcFormat="NDF",
        audioLayout="stereo",
        audioRate="48k",
    )
    spine = ET.SubElement(sequence, "spine")

    clips = edl.get("clips", [])
    accum_frames = 0
    first_clip_el: Optional[ET.Element] = None

    for idx, clip in enumerate(clips):
        seg_file = render_final_dir / f"seg_{idx+1:03d}.mp4"
        use_baked_seg = seg_file.is_file() and seg_file.stat().st_size > 1024
        if idx == len(clips) - 1:
            clip_frames = max(1, total_frames - accum_frames)
        else:
            clip_frames = max(1, int(clip["timeline_out_frame"]) - int(clip["timeline_in_frame"]))
        offset_frames = accum_frames
        accum_frames += clip_frames

        if use_baked_seg:
            media_p = seg_file
            src_start_frames = 0
            rid, avail_frames, has_audio = ensure_video_asset(media_p, clip_frames)
            dur_frames = min(clip_frames, avail_frames)
        else:
            media_p = Path(clip["media_path"])
            src_start_frames = max(0, int(round(float(clip.get("source_in_sec", 0.0)) * fps)))
            rid, avail_frames, has_audio = ensure_video_asset(media_p, src_start_frames + clip_frames)
            dur_frames = min(clip_frames, max(1, avail_frames - src_start_frames))

        clip_name = f"{idx+1:02d}_{clip.get('track_role', 'clip')}_{str(clip.get('sentence_text', ''))[:10]}"
        clip_el = ET.SubElement(
            spine,
            "asset-clip",
            name=clip_name,
            ref=rid,
            offset=_frames_to_rational(offset_frames, fps),
            start=_frames_to_rational(src_start_frames, fps),
            duration=_frames_to_rational(dur_frames, fps),
            format=fmt_id,
            tcFormat="NDF",
        )
        # 若回退引用了自带音频的源视频，将底片音轨静音，防止与独立人声轨重音
        if has_audio:
            ET.SubElement(clip_el, "adjust-volume", amount="-infinity")
        if first_clip_el is None:
            first_clip_el = clip_el

    # 挂载三条全片连续的独立 WAV 音轨至首个时间线起点 (lane -1 / -2 / -3)
    if first_clip_el is not None:
        audio_lanes = [
            (voice_res, -1, "dialogue", "0dB", "纯净人声干音"),
            (bgm_res, -2, "music", "-14dB", "专属BGM配乐"),
            (sfx_res, -3, "effects", "0dB", "切镜包装音效"),
        ]
        for res_info, lane_idx, role_name, vol_db, lane_title in audio_lanes:
            if not res_info:
                continue
            rid, a_frames = res_info
            use_frames = min(total_frames, a_frames)
            aud_el = ET.SubElement(
                first_clip_el,
                "asset-clip",
                name=lane_title,
                ref=rid,
                lane=str(lane_idx),
                offset="0s",
                start="0s",
                duration=_frames_to_rational(use_frames, fps),
                role=role_name,
            )
            if vol_db != "0dB":
                ET.SubElement(aud_el, "adjust-volume", amount=vol_db)

    rough = ET.tostring(fcpxml, encoding="utf-8")
    pretty = minidom.parseString(rough).toprettyxml(indent="  ", encoding="utf-8").decode("utf-8")
    # 注入标准 FCPXML DOCTYPE 声明，符合严格 XML/FCPXML DTD 校验
    header = '<?xml version="1.0" encoding="utf-8"?>\n'
    if pretty.startswith("<?xml"):
        body = pretty.split("?>", 1)[1].lstrip("\n")
    else:
        body = pretty
    return header + "<!DOCTYPE fcpxml>\n" + body


def build_jianying_draft_payload(pipeline_dir: Path) -> Dict[str, Any]:
    """构建向后兼容的旧版 5.9 精简 JSON 归档骨架。"""
    edl = read_json(pipeline_dir / "04_edl.json")
    subs = read_json(pipeline_dir / "05_subtitles.json")
    audio_events = read_json(pipeline_dir / "03_audio_events.json")
    mix_metrics = read_json(pipeline_dir / "08_mix_metrics.json")

    fps = float(edl.get("fps", DEFAULT_FPS))
    total_us = sec_to_us(float(edl["total_duration_sec"]))
    draft_id = str(uuid.uuid4()).upper()
    voice_wav = str(pipeline_dir / "08_voice_denoised.wav") if (pipeline_dir / "08_voice_denoised.wav").exists() else mix_metrics["master_wav"]

    draft_content = {
        "id": draft_id,
        "version": 360000,
        "fps": fps,
        "duration": total_us,
        "canvas_config": {"width": CANVAS_WIDTH, "height": CANVAS_HEIGHT, "ratio": "9:16"},
        "clips_count": len(edl.get("clips", [])),
        "subtitles_count": len(subs.get("entries", [])),
        "audio_stems": {
            "voice": voice_wav,
            "bgm": audio_events.get("locked_bgm_wav"),
            "sfx": str(pipeline_dir / "06_sfx_track.wav"),
        },
    }
    now_us = int(time.time() * 1_000_000)
    draft_meta = {
        "draft_id": draft_id,
        "draft_name": f"short_video_studio_{pipeline_dir.parent.name}",
        "tm_draft_create": now_us,
        "tm_draft_modified": now_us,
        "tm_duration": total_us,
    }
    return {"draft_content": draft_content, "draft_meta_info": draft_meta}


def export_jianying_draft(pipeline_dir: Path, simulate_failure: bool = False) -> Dict[str, Any]:
    draft_dir = pipeline_dir / "jianying_draft"
    proj_slug = pipeline_dir.parent.name if pipeline_dir.name == "pipeline" else pipeline_dir.name
    safe_name = f"short_video_studio_{proj_slug}"
    try:
        if simulate_failure:
            raise RuntimeError("模拟剪映工程导出版本不匹配或目标目录不可写。")

        draft_dir.mkdir(parents=True, exist_ok=True)
        # 清理旧版本直接写出的未加密 draft_*.json，避免用户误复制去 6.0+ 草稿目录报损坏
        legacy_dir = draft_dir / "legacy_v5_json"
        legacy_dir.mkdir(parents=True, exist_ok=True)
        for old_f in ("draft_content.json", "draft_meta_info.json"):
            old_p = draft_dir / old_f
            if old_p.exists():
                old_p.unlink()

        edl = read_json(pipeline_dir / "04_edl.json")
        subs = read_json(pipeline_dir / "05_subtitles.json")
        color_p = pipeline_dir / "07_color_plan.json"
        color_plan = read_json(color_p) if color_p.exists() else {}
        fps = int(edl.get("fps", DEFAULT_FPS))

        # 1. 生成 FCPXML 1.10 工程文件（剪映专业版 6.0~11.7+ 原生开放导入格式）
        fcpxml_text = build_fcpxml_tree(pipeline_dir, safe_name)
        fcpxml_path = draft_dir / f"{safe_name}.fcpxml"
        fcpxml_path.write_text(fcpxml_text, encoding="utf-8")

        # 2. 生成标准 SRT 字幕并拷贝 ASS 字幕
        srt_path = draft_dir / f"{safe_name}.srt"
        srt_path.write_text(_build_srt_content(subs), encoding="utf-8")
        ass_src = pipeline_dir / "05_subtitles.ass"
        if ass_src.is_file():
            shutil.copy2(ass_src, draft_dir / f"{safe_name}.ass")

        # 3. 导出 1080x1920 全画幅透明定位花字/卡片 PNG 与专属 3D LUT
        overlays_exported = _export_full_canvas_overlays(pipeline_dir, draft_dir / "overlays", fps)
        lut_exported: Optional[Path] = None
        if color_plan.get("lut_path") and Path(color_plan["lut_path"]).is_file():
            lut_src = Path(color_plan["lut_path"])
            lut_exported = draft_dir / lut_src.name
            shutil.copy2(lut_src, lut_exported)

        # 4. 写明清晰的中文导入操作说明
        readme = "\n".join([
            f"【{safe_name} 剪映专业版（含 11.7.0+）导入可编辑工程指南】",
            "",
            "由于剪映 6.0 及以上版本已对内部草稿文件夹启用私有加密，",
            "直接把旧版文件夹拷入草稿箱会提示损坏；请改用剪映官方原生开放工程导入：",
            "",
            f"第 1 步（导入分镜与三轨音频）：",
            f"  1) 打开剪映专业版 -> 右上角齿轮「设置」->「全局设置」-> 确认开启「导入工程」。",
            f"  2) 在首页「本地草稿」右上角点击「...」->「导入工程」（或新建项目后直接拖拽进来），",
            f"     选中本目录下的：{fcpxml_path.name}",
            f"  ✅ 导入后时间线即包含全部 {len(edl.get('clips', []))} 段切分镜头 + 独立纯净人声 + BGM + SFX 三条可任意静音/裁剪音轨！",
            "",
            f"第 2 步（导入可随意改字/改样式的原生字幕轨）：",
            f"  进入剪映编辑器 -> 顶部「文本」->「本地字幕」-> 拖入 {srt_path.name}（或 {safe_name}.ass）",
            f"  即可生成整条可任意更改字体、大小、颜色和错别字的原生字幕轨道。",
            "",
            f"第 3 步（可选：叠加独立花字/知识卡片与 LUT 微调）：",
            f"  - overlays/ 目录已导出 {len(overlays_exported)} 张 1080x1920 透明图，按文件名标注的时间段拖入画中画副轨即可自由开关；",
            f"  - 如需调整滤镜强度，可在「调节 -> 滤镜 -> 导入 LUT」加载 {lut_exported.name if lut_exported else '对应 .cube 文件'}。",
            "",
        ])
        (draft_dir / "README_剪映导入指南.txt").write_text(readme, encoding="utf-8")

        # 5. 归档精简元数据摘要
        payload = build_jianying_draft_payload(pipeline_dir)
        write_json(legacy_dir / "draft_summary.json", payload["draft_content"])
        write_json(legacy_dir / "draft_meta_info.json", payload["draft_meta_info"])

        status = {
            "status": "success",
            "degraded_to_mp4_only": False,
            "export_format": "fcpxml_1.10+srt",
            "draft_dir": str(draft_dir.resolve()),
            "fcpxml_path": str(fcpxml_path.resolve()),
            "srt_path": str(srt_path.resolve()),
            "overlays_count": len(overlays_exported),
            "message": f"剪映可编辑工程导出成功 ({fcpxml_path.name} + {srt_path.name})，可通过剪映「导入工程」打开编辑。",
        }
        write_json(pipeline_dir / "jianying_export_status.json", status)
        print(f"[jianying] 剪映原生可编辑导入包 (FCPXML + SRT) 导出成功 -> {draft_dir}")
        return status
    except Exception as exc:
        status = {
            "status": "degraded_to_mp4_only",
            "degraded_to_mp4_only": True,
            "draft_dir": None,
            "error": str(exc),
            "message": f"提示：剪映工程导出失败（{exc}），已自动降级为仅交付本地渲染 MP4，成片质量不受影响。",
        }
        write_json(pipeline_dir / "jianying_export_status.json", status)
        print(f"[jianying][降级提示] {status['message']}")
        return status


def main() -> None:
    maybe_reexec_in_venv()
    parser = argparse.ArgumentParser(description="出片2: 剪映原生可编辑工程包 (FCPXML + SRT) 独立导出")
    parser.add_argument("--pipeline-dir", required=True)
    parser.add_argument("--simulate-failure", action="store_true", help="模拟导出失败验证降级逻辑")
    args = parser.parse_args()
    export_jianying_draft(Path(args.pipeline_dir), simulate_failure=args.simulate_failure)


if __name__ == "__main__":
    main()
