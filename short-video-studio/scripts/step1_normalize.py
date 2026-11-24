#!/usr/bin/env python3
"""
[Step 1: 素材规整 + HDR 色彩空间还原 + 智能重构图 + 物理几何仿射映射]
v5 升级：
1. 基于 inventory 的 faces / saliency_bbox 智能计算横转竖重构图重心 (crop_x_frac / crop_y_frac)，彻底避免三分法人物裁出画面。
2. 音频流强制 `-af aresample=async=1:first_pts=0` 对齐 PTS 并自动拯救单边故障无线麦，杜绝 VFR/非零偏移引起的声画漂移。
3. 修复无 zscale 时 HDR 回退滤镜链路，准确统计 hdr_tonemapped_count，尊重 00_shot_log/00_visual_index 角色。
4. 并发调用 ffmpeg 规整，复用 common.detect_vfr_hdr。
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCRIPTS_ROOT = Path(__file__).resolve().parent
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from platform_probe import fps_mode_args
from common import (
    BT709_FFMPEG_ARGS,
    BT709_VF_SUFFIX,
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    DEFAULT_FPS,
    MEDIA_EXTS,
    compute_centered_crop_frac,
    compute_crop_transform,
    detect_vfr_hdr,
    estimate_task_eta,
    fast_media_fingerprint,
    ffmpeg_has_filter,
    ffprobe_media,
    find_bin,
    link_or_copy,
    map_eye_anchor_to_crop,
    map_norm_bbox_to_crop,
    maybe_reexec_in_venv,
    parse_video_stream_geometry,
    read_json,
    read_wav_as_48k_stereo,
    rescue_stereo_channels,
    run_cmd,
    write_json,
    write_wav_48k_stereo,
)

_AROLL_DIR_HINTS = ("口播", "主讲", "人声", "说话", "aroll", "a_roll", "talk", "host", "speech")
_BROLL_DIR_HINTS = ("素材", "空镜", "特写", "环境", "补拍", "画面", "产品", "broll", "b_roll", "sucai", "cutaway")


def _detect_folder_role_hint(src: Path, root_dir: Optional[Path] = None) -> Optional[str]:
    parts: List[str] = []
    if root_dir is not None:
        try:
            parts = list(src.resolve().relative_to(root_dir.resolve()).parts[:-1])
        except Exception:
            parts = [src.parent.name]
    else:
        parts = [src.parent.name]
    joined = "/".join(parts).lower()
    if any(k in joined for k in _BROLL_DIR_HINTS):
        return "b_roll"
    if any(k in joined for k in _AROLL_DIR_HINTS):
        return "a_roll"
    return None


def _hardlink_or_symlink(src: Path, dst: Path) -> None:
    """优先使用同盘硬链接零空间占用复用素材，跨盘降级软链接，最后才回退拷贝。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() or dst.is_symlink():
        try:
            if dst.resolve() == src.resolve():
                return
        except Exception:
            pass
        dst.unlink(missing_ok=True)
    try:
        os.link(src, dst)
        return
    except OSError:
        pass
    try:
        os.symlink(src.resolve(), dst)
        return
    except OSError:
        link_or_copy(src, dst)


def _build_hdr_prefix() -> str:
    if ffmpeg_has_filter("zscale"):
        return "zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,"
    return "colorspace=all=bt709:iall=bt2020:fast=1,"


def _smart_crop_fractions(shot_meta: Optional[Dict[str, Any]], disp_w: int, disp_h: int) -> Tuple[float, float]:
    """根据旋转前人脸/显著性中心计算智能重构图裁切比例，确保横转竖后主体真正落在 540px 正中。"""
    if not shot_meta or disp_w <= 0 or disp_h <= 0:
        return 0.5, 0.5
    faces = shot_meta.get("faces") or []
    cx, cy = 0.5, 0.5
    is_face = False
    found = False
    if faces and isinstance(faces[0], dict):
        bb = faces[0].get("bbox")
        if isinstance(bb, dict) and bb.get("w", 0) > 0:
            cx = float(bb.get("x", 0.5)) + float(bb.get("w", 0.0)) * 0.5
            cy = float(bb.get("y", 0.5)) + float(bb.get("h", 0.0)) * 0.5
            is_face = True
            found = True
    if not found:
        sal = shot_meta.get("saliency_bbox")
        if isinstance(sal, dict) and sal.get("w", 0) > 0:
            cx = float(sal.get("x", 0.5)) + float(sal.get("w", 0.0)) * 0.5
            cy = float(sal.get("y", 0.5)) + float(sal.get("h", 0.0)) * 0.5
            found = True
    if not found:
        return 0.5, 0.5
    base_tx = compute_crop_transform(disp_w, disp_h, CANVAS_WIDTH, CANVAS_HEIGHT, 0.5, 0.5)
    fx = compute_centered_crop_frac(cx, base_tx["scaled_w"], CANVAS_WIDTH, target_norm_pos=0.5)
    fy = compute_centered_crop_frac(cy, base_tx["scaled_h"], CANVAS_HEIGHT, target_norm_pos=0.40 if is_face else 0.5)
    return fx, fy


def normalize_single_media(
    src: Path,
    out_dir: Path,
    idx: int,
    role_hint: str = "auto",
    shot_meta: Optional[Dict[str, Any]] = None,
    shared_norm_cache: Optional[Path] = None,
    input_root: Optional[Path] = None,
) -> Dict[str, Any]:
    ffmpeg = find_bin("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("未找到 ffmpeg。")

    meta = ffprobe_media(src)
    streams = meta.get("streams", [])
    v_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    a_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if not v_stream:
        raise ValueError(f"文件不含视频流: {src}")

    geom = parse_video_stream_geometry(v_stream)
    disp_w = geom["display_width"] or CANVAS_WIDTH
    disp_h = geom["display_height"] or CANVAS_HEIGHT
    cx_frac, cy_frac = _smart_crop_fractions(shot_meta, disp_w, disp_h)
    crop_tx = compute_crop_transform(
        disp_w, disp_h, CANVAS_WIDTH, CANVAS_HEIGHT,
        crop_x_frac=cx_frac, crop_y_frac=cy_frac,
    )

    vh = detect_vfr_hdr(meta)
    r_fps, is_vfr, was_hdr = vh["r_fps"], vh["is_vfr"], vh["was_hdr"]
    log_profile = vh.get("log_profile")
    color_transfer = vh["color_transfer"] or "unknown"
    color_primaries = vh["color_primaries"] or "unknown"

    stem = f"norm_{idx:02d}_{src.stem}"
    out_mp4 = out_dir / f"{stem}.mp4"
    out_wav = out_dir / f"{stem}.wav"
    has_audio = a_stream is not None

    hdr_prefix = _build_hdr_prefix() if was_hdr else ("eq=contrast=1.18:saturation=1.22:gamma=0.92," if log_profile else "")
    bt709_tail = ",".join(BT709_VF_SUFFIX)
    vf_chain = (
        f"{hdr_prefix}"
        f"scale={crop_tx['scaled_w']}:{crop_tx['scaled_h']}:flags=lanczos,"
        f"crop={CANVAS_WIDTH}:{CANVAS_HEIGHT}:{crop_tx['crop_x']}:{crop_tx['crop_y']},"
        f"fps={DEFAULT_FPS},{bt709_tail}"
    )

    # 跨项目共享缓存硬链接复用：相同原素材+相同裁切/色彩方案零重复转码、零额外占盘
    cache_mp4: Optional[Path] = None
    cache_wav: Optional[Path] = None
    reused_from_cache = False
    if shared_norm_cache is not None:
        fp_sig = fast_media_fingerprint(src, salt=f"v3|{vf_chain}|{int(has_audio)}")
        cache_mp4 = shared_norm_cache / f"{fp_sig}.mp4"
        cache_wav = shared_norm_cache / f"{fp_sig}.wav"
        if cache_mp4.is_file() and (not has_audio or cache_wav.is_file()):
            _hardlink_or_symlink(cache_mp4, out_mp4)
            if has_audio and cache_wav.is_file():
                _hardlink_or_symlink(cache_wav, out_wav)
            reused_from_cache = True

    def _build_norm_cmd(vf_str: str, include_wav: bool) -> List[str]:
        cmd = [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(src),
            "-map", "0:v:0",
            "-vf", vf_str,
            "-an",
            *fps_mode_args("cfr"),
            "-r", str(DEFAULT_FPS),
            "-c:v", "libx264", *BT709_FFMPEG_ARGS, "-preset", "fast", "-crf", "18",
            str(out_mp4),
        ]
        if include_wav:
            cmd.extend([
                "-map", "0:a:0",
                "-vn", "-af", "aresample=async=1:first_pts=0",
                "-acodec", "pcm_s16le", "-ar", "48000", "-ac", "2",
                str(out_wav),
            ])
        return cmd

    hdr_tonemapped = bool(was_hdr and ffmpeg_has_filter("zscale"))
    stereo_diag = "ok"
    if not reused_from_cache:
        res = run_cmd(_build_norm_cmd(vf_chain, has_audio), check=False)
        if res.returncode != 0 and was_hdr:
            plain_vf = (
                f"scale={crop_tx['scaled_w']}:{crop_tx['scaled_h']}:flags=lanczos,"
                f"crop={CANVAS_WIDTH}:{CANVAS_HEIGHT}:{crop_tx['crop_x']}:{crop_tx['crop_y']},"
                f"fps={DEFAULT_FPS},{bt709_tail}"
            )
            hdr_tonemapped = False
            fb_res = None
            if ffmpeg_has_filter("zscale"):
                fallback_vf = (
                    f"colorspace=all=bt709:iall=bt2020:fast=1,"
                    f"{plain_vf}"
                )
                fb_res = run_cmd(_build_norm_cmd(fallback_vf, has_audio), check=False)
            if fb_res is None or fb_res.returncode != 0:
                run_cmd(_build_norm_cmd(plain_vf, has_audio), check=True)
        elif res.returncode != 0:
            raise RuntimeError(f"素材规整失败 ({src.name}): {(res.stderr or '')[-800:]}")

        if has_audio and out_wav.is_file():
            try:
                arr_w, _ = read_wav_as_48k_stereo(out_wav, target_sr=48000)
                arr_fixed, stereo_diag = rescue_stereo_channels(arr_w, imbalance_db=8.0)
                if stereo_diag != "ok":
                    write_wav_48k_stereo(out_wav, arr_fixed, sr=48000)
            except Exception:
                pass

        if cache_mp4 is not None and out_mp4.is_file():
            try:
                cache_mp4.parent.mkdir(parents=True, exist_ok=True)
                _hardlink_or_symlink(out_mp4, cache_mp4)
                if has_audio and cache_wav is not None and out_wav.is_file():
                    _hardlink_or_symlink(out_wav, cache_wav)
            except Exception:
                pass

    norm_meta = ffprobe_media(out_mp4)
    nv = next(s for s in norm_meta["streams"] if s["codec_type"] == "video")
    norm_dur = float(norm_meta.get("format", {}).get("duration") or nv.get("duration") or 0.0)

    # 判定素材角色（与 inventory_media 保持一致）：纯代码保护 > 显式传入 > 人工 visual_index > 无音频 > 音频缺陷/文件名 > 建议角色
    name_low = src.name.lower()
    is_code_scene = (src.parent.name == "01_code_scenes") or name_low.startswith("code_scene_")
    shot_role = (shot_meta or {}).get("role")
    shot_ag = (shot_meta or {}).get("audio_grade")
    vis_indexed = bool((shot_meta or {}).get("visual_indexed"))
    folder_hint = _detect_folder_role_hint(src, input_root)
    if role_hint != "auto":
        role = role_hint
    elif is_code_scene:
        role = "b_roll"
    elif "code_broll" in name_low:
        role = "code_broll"
    elif vis_indexed and shot_role in ("a_roll_candidate", "b_roll_candidate"):
        role = "a_roll" if (shot_role == "a_roll_candidate" and has_audio) else "b_roll"
    elif not has_audio or shot_ag in ("silent", "noisy"):
        role = "b_roll"
    elif "broll" in name_low or "b_roll" in name_low or "sucai" in name_low or "空镜" in name_low or "素材" in name_low:
        role = "b_roll"
    elif "aroll" in name_low or "a_roll" in name_low or "talk" in name_low or "main" in name_low or "口播" in name_low:
        role = "a_roll"
    elif folder_hint in ("a_roll", "b_roll"):
        role = folder_hint if (folder_hint != "a_roll" or has_audio) else "b_roll"
    elif shot_role == "b_roll_candidate":
        role = "b_roll"
    elif shot_role == "a_roll_candidate":
        role = "a_roll"
    else:
        role = "a_roll" if has_audio else "b_roll"

    item: Dict[str, Any] = {
        "id": f"media_{idx:02d}",
        "source_path": str(src.resolve()),
        "normalized_mp4": str(out_mp4.resolve()),
        "normalized_wav": str(out_wav.resolve()) if has_audio else None,
        "role": role,
        "source_is_code_scene": is_code_scene,
        "original_size": f"{disp_w}x{disp_h}",
        "coded_size": f"{geom['coded_width']}x{geom['coded_height']}",
        "rotation_deg": geom["rotation_deg"],
        "crop_transform": crop_tx,
        "was_vfr": is_vfr,
        "was_hdr": was_hdr,
        "hdr_tonemapped": hdr_tonemapped,
        "source_log_profile": log_profile,
        "stereo_diag": stereo_diag,
        "source_color_transfer": color_transfer,
        "source_color_primaries": color_primaries,
        "original_r_fps": round(r_fps, 2),
        "normalized_fps": DEFAULT_FPS,
        "duration_sec": round(norm_dur, 4),
        "has_audio": has_audio,
        "reused_from_shared_cache": reused_from_cache,
    }
    if shot_meta:
        for k in (
            "best_in_sec", "sharpness_score", "stability_score",
            "motion_speed_px", "accel_jitter_px", "camera_motion", "shot_size", "action_apex_sec",
            "audio_grade", "video_grade", "taboo_tags", "usable", "excluded_windows", "visual_indexed",
            "semantic_tags", "manual_tags", "scene_group", "description",
        ):
            if k in shot_meta:
                item[k] = shot_meta[k]
        if shot_meta.get("usable") is False and item.get("video_grade", "usable") == "usable":
            item["video_grade"] = "ng"

        mapped_faces: List[Dict[str, Any]] = []
        for f_obj in shot_meta.get("faces") or []:
            nb = map_norm_bbox_to_crop(f_obj.get("bbox"), crop_tx)
            if nb is None:
                continue
            nf = dict(f_obj)
            nf["bbox"] = nb
            ea_tmp = map_eye_anchor_to_crop({"eye_cx": f_obj.get("eye_cx", 0.5), "eye_cy": f_obj.get("eye_cy", 0.35)}, crop_tx)
            if ea_tmp:
                nf["eye_cx"] = ea_tmp["eye_cx"]
                nf["eye_cy"] = ea_tmp["eye_cy"]
            mapped_faces.append(nf)
        item["faces"] = mapped_faces

        ea = shot_meta.get("eye_anchor")
        if isinstance(ea, dict):
            mapped_ea = map_eye_anchor_to_crop(
                {"eye_cx": ea.get("cx", ea.get("eye_cx", 0.5)), "eye_cy": ea.get("cy", ea.get("eye_cy", 0.36)), **{k: v for k, v in ea.items()}},
                crop_tx,
            )
            if mapped_ea:
                mapped_ea["cx"] = mapped_ea["eye_cx"]
                mapped_ea["cy"] = mapped_ea["eye_cy"]
            item["eye_anchor"] = mapped_ea
        else:
            item["eye_anchor"] = None

        item["saliency_bbox"] = map_norm_bbox_to_crop(shot_meta.get("saliency_bbox"), crop_tx)

    # 盘点走大模型判定时本地不出人脸框，于是 a_roll 镜全都没有脸锚点 →
    # 小窗人脸取景、花字/贴纸避脸、竖屏重构重心全部没依据（实测 89 条素材里 31 条空，占满 12 个 A-roll 镜）。
    # 这里对「没有脸锚点」的素材补一次 Apple Vision 三点采样，写回归一化后的坐标。
    if not item.get("faces") and item.get("normalized_mp4"):
        norm_p = Path(str(item["normalized_mp4"]))
        if norm_p.is_file():
            try:
                import face_framing

                dur_s = float(item.get("duration_sec") or 3.0)
                anc = face_framing.anchor_for(str(norm_p), max(0.0, dur_s / 2.0), dur_s)
            except Exception:
                anc = None
            if isinstance(anc, dict) and float(anc.get("w") or 0.0) > 0.0:
                item["faces"] = [{
                    "bbox": {
                        "x": round(float(anc["cx"]) - float(anc["w"]) / 2.0, 4),
                        "y": round(float(anc["cy"]) - float(anc["h"]) / 2.0, 4),
                        "w": round(float(anc["w"]), 4),
                        "h": round(float(anc["h"]), 4),
                    },
                    "source": anc.get("source") or "apple_vision",
                    "frames_sampled": anc.get("frames_sampled"),
                    "backfilled": True,
                }]
    return item


def run_step1(
    input_paths: List[Path],
    pipeline_dir: Path,
    shared_cache_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    norm_dir = pipeline_dir / "01_normalized"
    norm_dir.mkdir(parents=True, exist_ok=True)
    # 若未显式传入 shared_cache_dir，自动向上探测所属账号的 shared_cache/normalized 复用池
    if shared_cache_dir is None:
        cand_acc = pipeline_dir.resolve().parents[2] if len(pipeline_dir.resolve().parents) >= 3 and pipeline_dir.resolve().parents[1].name == "projects" else None
        if cand_acc is not None:
            shared_cache_dir = cand_acc / "shared_cache"
    shared_norm_cache = (shared_cache_dir / "normalized") if shared_cache_dir else None

    shot_by_path: Dict[str, Dict[str, Any]] = {}
    shot_by_name: Dict[str, Dict[str, Any]] = {}
    shot_log_p = pipeline_dir / "00_shot_log.json"
    if shot_log_p.exists():
        sl = read_json(shot_log_p)
        for s in sl.get("shots", []):
            if s.get("path"):
                shot_by_path[str(Path(s["path"]).resolve())] = s
            if s.get("filename"):
                shot_by_name[str(s["filename"])] = s

    scene_meta_by_name: Dict[str, Dict[str, Any]] = {}
    media_entries: List[Tuple[Path, Optional[Path]]] = []
    seen_media: set[str] = set()
    for p in input_paths:
        if p.is_dir():
            sm_file = p / "scene_meta.json"
            if sm_file.is_file():
                try:
                    sm_raw = read_json(sm_file)
                    if isinstance(sm_raw, dict):
                        for fn, m_obj in sm_raw.items():
                            if isinstance(m_obj, dict):
                                scene_meta_by_name[str(fn)] = m_obj
                except Exception:
                    pass
            # 递归扫描多级子目录（如 口播/ + 素材/），忽略隐藏目录并去重
            for child in sorted(p.rglob("*")):
                if not child.is_file():
                    continue
                rel_parts = child.relative_to(p).parts
                if any(part.startswith(".") or part == "__pycache__" for part in rel_parts):
                    continue
                if child.suffix.lower() in MEDIA_EXTS and "benchmark" not in child.name.lower():
                    key = str(child.resolve())
                    if key not in seen_media:
                        seen_media.add(key)
                        media_entries.append((child, p))
        elif p.is_file() and p.suffix.lower() in MEDIA_EXTS and "benchmark" not in p.name.lower():
            key = str(p.resolve())
            if key not in seen_media:
                seen_media.add(key)
                media_entries.append((p, p.parent))

    if not media_entries:
        raise FileNotFoundError("未找到任何可用的原始视频素材文件。")

    eta = estimate_task_eta("normalize", clip_count=len(media_entries))
    print(f"[step1] 准备规整 {len(media_entries)} 条视频素材（优先硬链接复用账号缓存），预计耗时 {eta['label_cn']} ...")

    def _merge_shot_meta(f: Path) -> Optional[Dict[str, Any]]:
        base = dict(shot_by_path.get(str(f.resolve())) or shot_by_name.get(f.name) or {})
        extra = scene_meta_by_name.get(f.name)
        if extra:
            base.update(extra)
        return base or None

    tasks = [
        (f, idx, _merge_shot_meta(f), root_p)
        for idx, (f, root_p) in enumerate(media_entries, start=1)
    ]
    if len(tasks) > 1:
        with ThreadPoolExecutor(max_workers=min(4, len(tasks))) as pool:
            items = list(pool.map(
                lambda args: normalize_single_media(
                    args[0], norm_dir, args[1], shot_meta=args[2],
                    shared_norm_cache=shared_norm_cache, input_root=args[3],
                ),
                tasks,
            ))
    else:
        items = [normalize_single_media(
            tasks[0][0], norm_dir, tasks[0][1], shot_meta=tasks[0][2],
            shared_norm_cache=shared_norm_cache, input_root=tasks[0][3],
        )]

    hdr_cnt = sum(1 for it in items if it.get("hdr_tonemapped"))
    reused_cnt = sum(1 for it in items if it.get("reused_from_shared_cache"))
    manifest = {
        "step": "01_normalize",
        "fps": DEFAULT_FPS,
        "resolution": f"{CANVAS_WIDTH}x{CANVAS_HEIGHT}",
        "sample_rate": 48000,
        "media_count": len(items),
        "hdr_tonemapped_count": hdr_cnt,
        "reused_from_shared_cache_count": reused_cnt,
        "items": items,
    }
    write_json(pipeline_dir / "01_manifest.json", manifest)
    print(f"[step1] 素材规整完成：{len(items)} 条素材已统一为 {DEFAULT_FPS}fps CFR（共享缓存免转码复用 {reused_cnt} 条，HDR 还原 {hdr_cnt} 条）-> 01_manifest.json")
    return manifest


def main() -> None:
    maybe_reexec_in_venv()
    parser = argparse.ArgumentParser(description="Step 1: 素材规整 (旋转解算 + 智能重构图 + 仿射映射 + HDR + CFR)")
    parser.add_argument("--input", required=True, nargs="+", help="原始素材目录或视频文件路径列表")
    parser.add_argument("--pipeline-dir", required=True, help="pipeline 输出目录")
    args = parser.parse_args()
    run_step1([Path(x) for x in args.input], Path(args.pipeline_dir))


if __name__ == "__main__":
    main()
