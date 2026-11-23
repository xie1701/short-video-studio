#!/usr/bin/env python3
"""从解密剪映草稿直接驱动渲染器，复刻原版全片（盲审 P0-1a）。

与普通流水线的区别：不走 run_step4/5/6 的句子驱动重排，一切以草稿真值为准——
  - 主轨：段级 source/target 时程、变速、主轨 scale/transform 推近 → geometry_pip；
  - 复合片段（materials.drafts 内嵌子时间线）：递归复刻成 mp4，
    画面进叠加合成、声音按段落在时间线上进音频母带；
  - 叠加视频轨：先抽段 →（有 matting 就走 macOS Vision 人像抠像）→ 按草稿
    scale/transform/alpha/flip 合成回时间线（轨序即 z 序）；
  - 文本：按草稿原字体文件、原填充色、原字号（PX_PER_UNIT=4.0）、原阴影渲染 PNG，
    按草稿 transform 落位；不走模板近似；
  - 音频：草稿音频轨逐段裁切 → 定位 → amix 合成 08_mixed_master.wav；
  - 调色与画面特效：仍走 step7 校准链（其特效窗口本就来自源草稿 effect 轨真值）。

不假装：贴纸尺寸近似、关键帧动画、主轨 alpha、素材级 crop 复刻不了就计数上报
（report.unrealized），绝不静默跳过，也绝不拿 invented 内容顶替。

用法：
  python3 dev_tools/render_replica_from_draft.py --style-id style_21 --out /tmp/blind_review
  python3 dev_tools/render_replica_from_draft.py --all --out /tmp/blind_review [--preview]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "short-video-studio" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

PH_RE = re.compile(r"##_draftpath_placeholder_[0-9A-Fa-f\-]+_##")
US = 1_000_000

# overlay 叠层流的固定时移（单位：帧）。原因不是「时序写错了」，是 ffmpeg framesync 的口径：
#   overlay 给主帧 t 选副输入时取的是「PTS ≥ t 的第一帧」（向后取整），而叠层流第 k 帧的
#   PTS 恰好是 t_start + k/fps —— 于是图层第 k 帧落在主帧 k+1 上，整条叠层**领先一帧**。
# 实测（style_21 手机边框出场滑动 12.40–12.53s，用模板竖直位移 dy 逐帧量原版与复刻）：
#   原版 dy = 0/2/10/23/42/65/94/128/167（帧 744…752），复刻 dy = 3/10/23/42/65/94/128/168/212
#   → 复刻第 n 帧正是原版第 n+1 帧，整体领先 1 帧（不是曲线错，起点/斜率都对）。
# 黑底单叠实测：setpts 补 +1/(fps*TB) 后 dy 逐帧回到 0/3(≈2)/10/23/42/65/94。
# 只对「走 overlay 的贴纸/叠加层」生效（unit 上的 pts_lag 字段）；主轨与文字层不经过
# 这条 setpts（文字层另有自己的烘焙链），所以不受影响。
PTS_LAG_FRAMES = 1
_CODE_SIG = ""


def _cache_tag(*parts) -> str:
    """段文件缓存指纹：参数或脚本一改，旧段自动失效（不静默复用旧烘焙）。"""
    import hashlib as _h
    global _CODE_SIG
    if not _CODE_SIG:
        _CODE_SIG = _h.sha1(Path(__file__).read_bytes()).hexdigest()[:8]
    blob = ("|".join(str(p) for p in parts) + "|" + _CODE_SIG + "|" + _mask_code_sig()
            + "|" + _anim_code_sig() + "|" + _matte_code_sig())
    return _h.sha1(blob.encode()).hexdigest()[:8]


def _matte_code_sig() -> str:
    """person_matting.py（含内嵌的 Swift 源码）的指纹：抠像器一改，旧 matte/描边必须重烘。

    抠像器输出的是「逐帧 alpha」，改了首帧对齐方式后旧 matte 会被永久复用——
    这正是「改了不生效」的典型陷阱（与 STROKE_MATTE_DILATE 注释里那次同因）。
    """
    import hashlib as _h
    try:
        return _h.sha1((SCRIPTS / "person_matting.py").read_bytes()).hexdigest()[:8]
    except Exception:
        return "nomatte"


def _anim_code_sig() -> str:
    """animations/anim_render 的指纹：动画曲线或幅度一改，逐帧动画层必须重烘。

    这两个模块的标定（CTX_CALIBRATION）是逐帧反解出来的，改了不失效的话
    缓存里躺着的旧图层会一直盖在成片上，看着像「改了没生效」。
    """
    import hashlib as _h
    sig = ""
    for nm in ("animations.py", "anim_render.py"):
        try:
            sig += _h.sha1((SCRIPTS / nm).read_bytes()).hexdigest()[:8]
        except Exception:
            sig += "noanim"
    return sig


def _mask_code_sig() -> str:
    """mask_shapes.py 的指纹：shader 数学一改，遮罩 PNG/烘焙段全部失效重算。"""
    import hashlib as _h
    try:
        return _h.sha1((SCRIPTS / "mask_shapes.py").read_bytes()).hexdigest()[:8]
    except Exception:
        return "nomask"


# ---------- 剪映遮罩（materials.common_mask）真值解析 ----------
# 遮罩挂在 segment.extra_material_refs（**不是** clip.mask），配置见 materials.common_mask[*].config。
MASK_KF_PROPS = {
    "KFTypeCommonMaskRoundCorner": "roundCorner",
    "KFTypeCommonMaskFeather": "feather",
    "KFTypeCommonMaskPositionX": "centerX",
    "KFTypeCommonMaskPositionY": "centerY",
}


def mask_config(mat: dict) -> dict:
    """common_mask 素材 → mask_shapes.mask_alpha 的 cfg（resource_type 提到顶层）。"""
    cfg = dict(mat.get("config") or {})
    cfg["resource_type"] = str(mat.get("resource_type") or cfg.get("resource_type") or "")
    return cfg


def segment_mask_config(seg: dict, mask_by_id: dict, report: dict | None = None) -> dict | None:
    """段 → 遮罩 config（无遮罩 / 未复刻的遮罩类型返回 None，后者进 unrealized 不静默）。

    草稿里遮罩被「关掉」时 material.type == "none"（style_21 实测：线性遮罩 type=none，原版成片根本没遮），
    这种不是未实现，而是本来就不该生效 —— 不计数、不报 unrealized。
    """
    for ref in seg.get("extra_material_refs") or []:
        m = mask_by_id.get(ref)
        if not m:
            continue
        if str(m.get("type") or "mask").lower() == "none":
            return None
        cfg = mask_config(m)
        if not _mask_shapes_ok(cfg):
            if report is not None:
                key = f"mask_unsupported_type:{cfg.get('resource_type') or '?'}"
                report["unrealized"][key] = report["unrealized"].get(key, 0) + 1
            return None
        return cfg
    return None


def _mask_shapes_ok(cfg: dict) -> bool:
    """只上真复刻过的遮罩类型（矩形/圆形/线性）；其它类型宁可不遮也不乱遮。"""
    try:
        from mask_shapes import is_supported  # noqa: E402  (SCRIPTS 已在调用方入 sys.path)
        return is_supported(cfg.get("resource_type"))
    except Exception:
        return True


def mask_keyframe_series(seg: dict, src_in: float, report: dict | None = None) -> dict:
    """段上遮罩参数的关键帧序列 {prop: [(段内相对秒, 值)]}（keyframe 时间是源时间）。"""
    out: dict = {}
    for kfo in seg.get("common_keyframes") or []:
        ptype = kfo.get("property_type") or ""
        prop = MASK_KF_PROPS.get(ptype)
        if prop is None:
            if "Mask" in ptype and report is not None:
                report["unrealized"]["mask_keyframe_unsupported"] = \
                    report["unrealized"].get("mask_keyframe_unsupported", 0) + 1
            continue
        pts = []
        for k in kfo.get("keyframe_list") or []:
            v = k.get("values") or []
            if v:
                pts.append((max(0.0, float(k.get("time_offset") or 0) / US - float(src_in or 0.0)),
                            float(v[0])))
        if pts:
            pts.sort()
            out[prop] = pts
    return out


def mask_schedule(seg: dict, base_cfg: dict, dur: float, src_in: float, fps: int,
                  report: dict | None = None) -> list:
    """段内遮罩参数时间表 [(t0, t1, cfg)]，覆盖 [0, dur)，帧对齐。

    无遮罩关键帧 → 单段静态（config 即真值）；有关键帧 → 首帧前保持首值、末帧后保持末值、
    中间两点线性（与原版逐帧实测的顶边位置一致），**逐帧**烘焙后合并相同态，不按段近似。
    """
    ser = mask_keyframe_series(seg, src_in, report)
    if not ser:
        return [(0.0, float(dur), dict(base_cfg))]

    def val(prop: str, t: float):
        pts = ser.get(prop)
        if not pts:
            return base_cfg.get(prop)
        if t <= pts[0][0]:
            return pts[0][1]
        if t >= pts[-1][0]:
            return pts[-1][1]
        for (t0, v0), (t1, v1) in zip(pts, pts[1:]):
            if t0 <= t <= t1:
                return v0 if t1 - t0 <= 1e-9 else v0 + (v1 - v0) * (t - t0) / (t1 - t0)
        return pts[-1][1]

    n_frames = max(1, int(round(float(dur) * fps)))
    raw = []
    for i in range(n_frames):
        t0 = i / float(fps)
        t1 = min(float(dur), (i + 1) / float(fps))
        mid = 0.5 * (t0 + t1)
        cfg = dict(base_cfg)
        for prop in ser:
            cfg[prop] = round(float(val(prop, mid)), 6)
        raw.append((t0, t1, cfg))
    merged: list = []
    for t0, t1, cfg in raw:
        if merged and merged[-1][2] == cfg and abs(merged[-1][1] - t0) < 1e-9:
            merged[-1] = (merged[-1][0], t1, cfg)
        else:
            merged.append((t0, t1, cfg))
    return merged


def split_pieces_by_mask(pieces: list, sched: list, t_start: float) -> list:
    """把主轨几何 pieces 按遮罩时间表切开，attach 对应遮罩 cfg（无遮罩时 cfg=None）。
    注意两套时轴：pieces 用时间线秒，sched 用段内相对秒 —— 这里把 sched 平移 t_start。"""
    out: list = []
    for piece in pieces:
        ta, tb, sc, tx, ty = piece[:5]
        for m0, m1, cfg in sched:
            a, b = max(ta, t_start + m0), min(tb, t_start + m1)
            if b - a <= 1e-4:
                continue
            out.append((a, b, sc, tx, ty, cfg))
    return out
PX_PER_UNIT = 4.0  # 兜底值；实际按画布推导 canvas_w/192（实测 1080 宽草稿 = 5.625，与标题/字幕墨高吻合）
# 阴影位移的参考宽：剪映阴影 distance 走的是 **720 口径**，不是字号单位。
# 反解办法（style_21 三帧静态字幕 5.1/9.72/12.90s：把「近白的墨迹 alpha」和「变暗的阴影 alpha」
# 分开量，阴影 50% 边界相对墨迹 50% 边界就是位移）：实测每轴 +4~+6px；
# 按 0.6364×distance(5.0)×(1080/720=1.5) = 4.8px 落在实测中位，按旧的 5.625（字号单位）
# 算出来 17.9px、按更早的 PX_PER_UNIT=4.0 算 12.7px —— 都比实测大一倍以上（旧渲染里阴影糊出墨迹 1.5~2 倍远，
# 白字旁边多一圈灰，正是字幕带常态差的来源）。同一 720 参考宽在本仓库已由贴纸几何实证。
SHADOW_REF_WIDTH = 720.0
SHADOW_BLUR_EM = 0.13  # 阴影模糊：σ = smoothing × font_px × 0.13（由两档字号实测反解，见 parse_text_material）
# 字幕底框内边距（单位：em，即 font_px 的倍数）。剪映草稿里不带这个值，只能从原版成片反解：
# style_17 13.0/13.4s「如果你不清楚」底框 336×84、字迹墨框 295×44；15.5s「我帮你推荐一下」底框 386×84
# （383=336+50，即每字 50px 字距）→ 四边内边距都是 20.5px，而该处 font_px ≈ 50 → 0.41em。
# 底框按**墨迹框**外扩：多行/描边/阴影都能自然包住。
SUB_BG_PAD_EM = 0.41
CANVAS_W, CANVAS_H = 1080, 1920
MAX_COMPOUND_DEPTH = 3


def _subst(path: str, base_dir: Path) -> str:
    return PH_RE.sub(str(base_dir), str(path or ""))


def load_draft(draft_dir: Path, decrypted_root: Path) -> dict:
    # 优先解密实时草稿（快照可能落后于用户最近编辑，字体/素材路径会变）
    import importlib.util

    spec = importlib.util.spec_from_file_location("jy_draft_decrypt", ROOT / "forensic" / "jy_draft_decrypt.py")
    jd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(jd)
    live = draft_dir / "draft_content.json"
    if live.is_file():
        try:
            raw = live.read_text(encoding="utf-8", errors="ignore")
            return json.loads(jd.decrypt_text(raw))
        except Exception:
            pass
    dec = decrypted_root / draft_dir.name / "draft_content.json"
    if dec.is_file():
        return json.loads(dec.read_text(encoding="utf-8"))
    raise SystemExit(f"草稿既不能实时解密也没有快照: {draft_dir}")


def run_ffmpeg(cmd: list, timeout: int = 600) -> None:
    cmd = list(cmd)
    if "-nostdin" not in cmd:
        cmd.insert(1, "-nostdin")
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError("ffmpeg 失败: " + (proc.stderr or "")[-1200:])


def ffprobe_has_audio(path: str) -> bool:
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=60)
        return bool((proc.stdout or "").strip())
    except Exception:
        return False


def media_start_time(path: str) -> float:
    """素材**视频流**的 `start_time`（秒），即首帧 PTS。草稿源时间要减掉它才是 ffmpeg 的 `-ss`。

    证据链（style_17）：
      - `二手车.mp4` / `美业.mp4` 的 `format.start_time` = 0.000，但**视频流** start_time = 0.033008
        （= 首帧 PTS，30fps 一帧；旧实现读 format 所以永远得 0，等于没做补偿）；
      - ffmpeg 的输入 `-ss` 是**从首帧**开始计的（实测 `-ss 0` 与不加 -ss 取到同一帧，
        `-ss 0.0334` 才是第 2 帧）；
      - 剪映的 `source_timerange.start` 是**容器 PTS**口径：src=1.4667 在原版对应 PTS 1.4667 那一帧
        （首帧 0.033 + 43×0.0333），对应 `-ss 1.4334`；我们直接 `-ss 1.4667` 就晚了一帧。
    所以：s_pass = draft_src - stream_start（不是 +）。整片逐点复核：改前最优偏移 k=+1 帧
    （-1/0/+1 平均 9.08/8.05/7.28），改后 k=0。
    """
    if os.environ.get("SVS_IGNORE_START_TIME") == "1":
        return 0.0
    key = str(path)
    if key in _START_TIME_CACHE:
        return _START_TIME_CACHE[key]
    val = 0.0
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=start_time",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=60)
        raw = [ln for ln in (proc.stdout or "").strip().splitlines() if ln and ln != "N/A"]
        if raw:
            val = max(0.0, float(raw[0]))
    except Exception:
        val = 0.0
    _START_TIME_CACHE[key] = val
    return val


def src_seek_time(draft_src: float, src_path: str) -> float:
    """草稿源时间（容器 PTS 口径）→ ffmpeg `-ss` 值（减掉首帧 PTS，且不为负）。"""
    return max(0.0, float(draft_src) - media_start_time(src_path))


_START_TIME_CACHE: dict = {}


def make_black_segment(out_path: Path, dur: float, fps: int, w: int, h: int) -> Path:
    """主轨素材缺失时的诚实占位：黑场同长段，盲审里一眼可见，不冒充内容。"""
    if not out_path.is_file():
        run_ffmpeg(["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=black:s={w}x{h}:r={fps}:d={max(0.1, dur):.4f}",
                    "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p", str(out_path)], timeout=120)
    return out_path


def cover_crop_vf(w: int, h: int) -> str:
    return (
        f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=bicubic,"
        f"crop={w}:{h}"
    )


# ---------- 叠加图层的几何真值 ----------
# 剪映里一个图层（照片/视频）的屏幕尺寸**不是** canvas×clip.scale：
#   step1 material.crop 已经把素材按 crop_ratio 预裁（照片丢进画布时被自动裁成 16:9 等）；
#   step2 裁后画面 contain 适配画布（scale=1 时整幅可见，9:16 画布放 16:9 横图不裁内容）；
#   step3 再乘 clip.scale（1 个单位 = 画布宽/高的一半，即 1080 宽草稿里 0.85 → 918px）。
# style_17 实测（原版 8.0-10.3s 五张车图）：飞度 16:9 裁后 667×376 → contain 1080×607 →
# ×0.85017 = 918×516，落点 y=153..668，与草稿 transform.y=0.5722 推得的 cy=411 完全吻合；
# 旧代码按「画布高×scale」算成 918×1632（cover-crop 到整帧再缩），横图被纵向拉满整帧。
_MEDIA_SIZE_CACHE: dict = {}


def media_pixel_size(path: str, mat: dict | None = None) -> tuple:
    """素材真实像素尺寸：静图读文件、视频走 ffprobe（缓存），失败才退回草稿声明值。

    草稿声明值常比真实文件少 1px（style_17 飞度声明 666×480、文件 667×480），
    按声明值算 crop 会越界，故裁切必须用真实像素。
    """
    key = str(path)
    if key in _MEDIA_SIZE_CACHE:
        return _MEDIA_SIZE_CACHE[key]
    size = None
    if Path(key).is_file():
        if (mat or {}).get("type") == "photo" or Path(key).suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".bmp"):
            try:
                from PIL import Image as _Img
                with _Img.open(key) as im:
                    size = (int(im.size[0]), int(im.size[1]))
            except Exception:
                size = None
        if size is None:
            try:
                proc = subprocess.run(
                    ["ffprobe", "-v", "error", "-select_streams", "v:0",
                     "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", key],
                    capture_output=True, text=True, timeout=60)
                w, h = (proc.stdout or "").strip().split("x")[:2]
                size = (int(w), int(h))
            except Exception:
                size = None
    if not size:
        size = (int((mat or {}).get("width") or CANVAS_W), int((mat or {}).get("height") or CANVAS_H))
    _MEDIA_SIZE_CACHE[key] = size
    return size


def material_crop_px(mat: dict, sw: int, sh: int):
    """material.crop（uv 比例，缺省边 0/1）→ 源像素裁切框 (x, y, w, h)；无裁切返回 None。"""
    c = mat.get("crop") or {}
    if not c:
        return None

    def _v(key: str, default: float) -> float:
        v = c.get(key)
        return float(default if v is None else v)

    x0 = _v("upper_left_x", 0.0)
    x1 = _v("upper_right_x", 1.0)
    y0 = _v("upper_left_y", 0.0)
    y1 = _v("lower_left_y", 1.0)
    x0, x1 = max(0.0, min(x0, x1)), min(1.0, max(x0, x1))
    y0, y1 = max(0.0, min(y0, y1)), min(1.0, max(y0, y1))
    cx, cy = int(round(x0 * sw)), int(round(y0 * sh))
    cw, ch = int(round((x1 - x0) * sw)), int(round((y1 - y0) * sh))
    cw, ch = min(cw, sw - cx), min(ch, sh - cy)
    if cw < 4 or ch < 4:
        return None
    if (cx, cy, cw, ch) == (0, 0, sw, sh):
        return None  # 名义上有 crop 但没裁掉任何像素
    return (cx, cy, cw, ch)


def layer_geometry(mat: dict, src_path: str, canvas_w: int, canvas_h: int, scale: float) -> dict:
    """草稿图层 → 屏幕几何 {lw, lh, crop}：先裁 uv，再 contain 适配画布，最后 × clip.scale。"""
    sw, sh = media_pixel_size(src_path, mat)
    crop = material_crop_px(mat, sw, sh)
    if crop:
        cw, ch = crop[2], crop[3]
    else:
        cw, ch = sw, sh
    scale = float(scale) if float(scale or 0) > 0 else 1.0
    fit = min(canvas_w / float(cw), canvas_h / float(ch))
    lw = cw * fit * scale
    lh = ch * fit * scale
    return {"lw": max(2, int(round(lw / 2)) * 2), "lh": max(2, int(round(lh / 2)) * 2), "crop": crop}


def layer_geometry_xy(mat: dict, src_path: str, canvas_w: int, canvas_h: int,
                      sx: float, sy: float) -> dict:
    """非等比几何：剪映的 scale.x / scale.y 可以不同（KFTypeScaleX/KFTypeScaleY 分开插值）。

    layer_geometry 只吃一个标量（等比），这里先用 1.0 倍算出 contain 基准尺寸，再按轴各自缩放。
    """
    g = layer_geometry(mat, src_path, canvas_w, canvas_h, 1.0)
    return {"lw": max(2, int(round(g["lw"] * float(sx) / 2)) * 2),
            "lh": max(2, int(round(g["lh"] * float(sy) / 2)) * 2),
            "crop": g.get("crop")}


def layer_vf(geom: dict, canvas_w: int, canvas_h: int) -> str:
    """图层几何 → ffmpeg 滤镜串（crop → scale）；几何与整帧相同时返回空串（不白降画质）。"""
    parts = []
    crop = geom.get("crop")
    if crop:
        parts.append(f"crop={crop[2]}:{crop[3]}:{crop[0]}:{crop[1]}")
    if not crop and (geom["lw"], geom["lh"]) == (canvas_w, canvas_h):
        return ""
    parts.append(f"scale={geom['lw']}:{geom['lh']}:flags=bicubic")
    return ",".join(parts)


def _discover_root(kind: str) -> str:
    """剪映（mac 版）缓存/草稿根：环境变量优先，否则在 ~/Movies 下按结构自动发现。

    不写死路径也不写死应用名——仓库隐私门禁不许出现本机路径与应用名。
    kind: "cache" -> .../User Data/Cache；"drafts" -> .../User Data/Projects/<库里那层>
    """
    env = {"cache": "JY_CACHE_ROOT", "drafts": "JY_DRAFT_ROOT"}[kind]
    if os.environ.get(env):
        return os.environ[env]
    base = Path.home() / "Movies"
    if kind == "cache":
        for cand in sorted(base.glob("*/User Data/Cache")):
            if (cand / "artistEffect").is_dir() or (cand / "effect").is_dir():
                return str(cand)
        return str(base / "User Data" / "Cache")
    for cand in sorted(base.glob("*/User Data/Projects/*")):
        if cand.is_dir() and any(c.is_dir() for c in cand.iterdir()):
            return str(cand)
    return str(base / "User Data" / "Projects" / "drafts")


JY_CACHE_ROOT = _discover_root("cache")


def _font_candidates(mat: dict) -> list:
    """收集字体候选：绝对路径优先；相对路径按剪映 Cache 根解析；只留真实存在的文件。"""
    cands: list = []
    for f in mat.get("fonts") or []:
        if f.get("path"):
            cands.append(f["path"])
    content = mat.get("content")
    if isinstance(content, str) and content.lstrip().startswith("{"):
        try:
            fp = (json.loads(content).get("font") or {}).get("path")
            if fp:
                cands.append(fp)
        except Exception:
            pass
    out = []
    for p in cands:
        p = str(p)
        if not Path(p).is_file() and p.startswith("/"):
            alt = Path(JY_CACHE_ROOT) / p.lstrip("/")
            if alt.is_file():
                p = str(alt)
        if Path(p).is_file() and p not in out:
            out.append(p)
    return out


def parse_text_material(mat: dict, canvas_w: int = CANVAS_W) -> dict:
    """草稿文本素材 → {text, color, font_path, font_size, shadow, stroke, line_spacing}。

    canvas_w 只用于把草稿里的「单位」换算成像素（字号、阴影位移/模糊），默认 1080 画布。
    """
    out = {"text": "", "color": (255, 255, 255), "font_path": None,
           "font_candidates": _font_candidates(mat),
           "font_size": float(mat.get("font_size") or 0.0)}
    if out["font_candidates"]:
        out["font_path"] = out["font_candidates"][0]
    content = mat.get("content")
    txt = ""
    if isinstance(content, str) and content.lstrip().startswith("{"):
        try:
            cj = json.loads(content)
            txt = str(cj.get("text") or "")
            styles = cj.get("styles") or []
            if styles:
                st0 = styles[0]
                # 字号真源：识别字幕的 material 上 font_size 是 None，真值写在 content.styles[0].size
                # （实测 17-二手车 的字幕是 15，旧代码回退 24 会让字幕大 1.6 倍）
                if not out["font_size"] and st0.get("size"):
                    out["font_size"] = float(st0["size"])
                fill = ((st0.get("fill") or {}).get("content") or {}).get("solid") or {}
                rgb = fill.get("color") or []
                if len(rgb) >= 3:
                    out["color"] = tuple(max(0, min(255, round(c * 255))) for c in rgb[:3])
                strokes = st0.get("strokes") or []
                if strokes and float(strokes[0].get("width") or 0) > 0:
                    w_px = float(strokes[0].get("width") or 0) * out["font_size"] * PX_PER_UNIT
                    sc = ((strokes[0].get("content") or {}).get("solid") or {}).get("color") or []
                    if w_px > 0.5:
                        out["stroke"] = {
                            "px": round(w_px, 1),
                            "color": tuple(max(0, min(255, round(c * 255))) for c in sc[:3]) if len(sc) >= 3 else (0, 0, 0),
                        }
            for f in mat.get("fonts") or []:
                if f.get("path"):
                    out["font_path"] = f.get("path")
                    break
        except Exception:
            txt = content
    else:
        txt = str(content or "")
        for f in mat.get("fonts") or []:
            if f.get("path"):
                out["font_path"] = f.get("path")
                break
    out["text"] = txt
    if mat.get("has_shadow"):
        sp = mat.get("shadow_point") or {}
        # 位移：shadow_point 是 -45° 的单位向量分量（0.6364=1/√2），乘 distance 得「720 口径」像素，
        # 再乘 canvas_w/720 落到本画布。旧口径（×PX_PER_UNIT=4.0 或 ×字号单位 5.625）大 2.7~3.7 倍。
        _sh_k = float(canvas_w) / SHADOW_REF_WIDTH
        # 模糊：shadow_smoothing 是 0..1 的滑杆（style_21 字幕 0.45、大字 0.2925），实测阴影边缘半宽
        # 与 **字号** 成正比而与画布无关：75px 字号 σ≈4.4px、300px 大字 σ≈11.4px（两档相差 4 倍字号 →
        # 2.6 倍 σ，线性拟合残差 ≤1px，用 0.13×font_px 同时落在两档）。旧实现 σ=smoothing×4.0=1.8px
        # 阴影硬得像描边；truth 文件里 blur_px=diffuse×font×0.9 的算法更小（1.1px），也不是真口径。
        _sh_em = float(out["font_size"] or 0.0) * (float(canvas_w) / 172.8) * SHADOW_BLUR_EM
        out["shadow"] = {
            "dx": float(sp.get("x") or 0.0) * float(mat.get("shadow_distance") or 0.0) * _sh_k,
            "dy": float(sp.get("y") or 0.0) * float(mat.get("shadow_distance") or 0.0) * _sh_k,
            "blur": max(0.0, float(mat.get("shadow_smoothing") or 0.0) * _sh_em),
            "alpha": float(mat.get("shadow_alpha") if mat.get("shadow_alpha") is not None else 0.9),
            "color": mat.get("shadow_color") or (0, 0, 0),
        }
        sc = out["shadow"]["color"]
        if isinstance(sc, str):
            sc = sc.strip()
            if sc.startswith("#") and len(sc) >= 7:
                out["shadow"]["color"] = tuple(int(sc[i : i + 2], 16) for i in (1, 3, 5))
            else:
                out["shadow"]["color"] = (0, 0, 0)
        elif isinstance(sc, (list, tuple)) and len(sc) >= 3:
            vals = [float(v) for v in sc[:3]]
            out["shadow"]["color"] = tuple(
                max(0, min(255, round(v * 255))) if v <= 1.0 else int(v) for v in vals
            )
    out["line_spacing"] = float(mat.get("line_spacing") or 0.0)
    # 素材级不透明度：mat.global_alpha（style_17 的「千万/如果」= 0.3：原版黑底上笔画实测
    # 77/255 ≈ 0.30，我们渲成纯白 255 → 字幕带边缘密度差 61% 的主因）。
    # style_21 有两段 global_alpha=0.0（水印字）——原版根本不显示，不乘就会凭空多出两行大字。
    ga = mat.get("global_alpha")
    out["alpha"] = 1.0 if ga is None else max(0.0, min(1.0, float(ga)))
    # 字幕「背景」样式：草稿 material.background_style/background_color/background_alpha
    # （style_17 10.27/12.93/13.77/14.47/15.10 五段识别字幕都是 style=1、#000000、alpha=0.7；
    #   手打文本段这些字段为空 → 本来就没底框，不能硬加）
    if mat.get("background_style"):
        bg_alpha = float(mat.get("background_alpha") if mat.get("background_alpha") is not None else 1.0)
        bg_col = mat.get("background_color") or "#000000"
        rgb = (0, 0, 0)
        if isinstance(bg_col, str) and bg_col.strip().startswith("#") and len(bg_col.strip()) >= 7:
            s = bg_col.strip()
            rgb = tuple(int(s[i:i + 2], 16) for i in (1, 3, 5))
        elif isinstance(bg_col, (list, tuple)) and len(bg_col) >= 3:
            rgb = tuple(max(0, min(255, round(float(c) * (255 if float(c) <= 1.0 else 1)))) for c in bg_col[:3])
        if bg_alpha > 0.01:
            out["background"] = {"color": rgb, "alpha": min(1.0, bg_alpha)}
    return out


def render_text_png(spec: dict, scale: float, out_png: Path, fail_log: list | None = None,
                    pxu: float = PX_PER_UNIT) -> tuple:
    """按草稿字体/颜色/字号/阴影渲染透明 PNG，返回 (path, w, h)。字体加载失败逐候选重试并留痕。"""
    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    text = spec["text"]
    if not text.strip():
        raise ValueError("空文本段")
    font_px = max(12, int(round(spec["font_size"] * pxu * max(0.05, scale))))
    lines = text.split("\n")
    font = None
    for p in (spec.get("font_candidates") or ([spec.get("font_path")] if spec.get("font_path") else [])):
        try:
            font = ImageFont.truetype(p, font_px)
            break
        except Exception as e:
            if fail_log is not None:
                fail_log.append({"text": text[:12], "px": font_px, "path": str(p)[-60:], "err": f"{type(e).__name__}: {e}"[:120]})
    if font is None:
        font = ImageFont.load_default()
    ls = float(spec.get("line_spacing") or 0.0)
    line_h = int(font_px * 1.25 + font_px * ls)
    pad = int(font_px * 0.3) + 24
    tmp_img = Image.new("RGBA", (8, 8))
    tmp_draw = ImageDraw.Draw(tmp_img)
    widths = []
    ink_tops, ink_bots = [], []
    for i, ln in enumerate(lines):
        bbox = tmp_draw.textbbox((0, 0), ln, font=font)
        widths.append(int(bbox[2] - bbox[0]))
        ink_tops.append(bbox[1] + i * line_h)
        ink_bots.append(bbox[3] + i * line_h)
    w = max(8, max(widths) + pad * 2)
    h = max(8, line_h * len(lines) + pad * 2)
    # 垂直定位＝**墨迹包围盒居中**，不是行盒居中：剪映把文字块的中心钉在锚点上，而 PIL 的行盒
    # 上下留白按字体 ascent/descent 走，不同字体偏得还不一样（思源粗宋 36 号实测偏低 32px、
    # 抖音黑体 12 号偏高 8.5px）；不按墨迹居中，字幕带边缘密度差会一路涨到 61%。
    y_first = h / 2.0 - (min(ink_tops) + max(ink_bots)) / 2.0
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    def _paint(layer_draw, dx: float, dy: float, color, alpha=255):
        y_off = y_first
        for ln in lines:
            layer_draw.text((pad + dx, y_off + dy), ln, font=font, fill=(*color, int(alpha)))
            y_off += line_h

    sh = spec.get("shadow")
    if sh and (abs(sh["dx"]) > 0.5 or abs(sh["dy"]) > 0.5):
        sh_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        # 剪映 shadow_point y 负=向下（屏幕坐标），PNG 坐标 y 正=向下 → dy 取反
        _paint(ImageDraw.Draw(sh_layer), sh["dx"], -sh["dy"], tuple(sh["color"]), alpha=int(255 * sh["alpha"]))
        if sh["blur"] > 0.3:
            sh_layer = sh_layer.filter(ImageFilter.GaussianBlur(radius=sh["blur"]))
        img = Image.alpha_composite(img, sh_layer)
        draw = ImageDraw.Draw(img)
    stroke = spec.get("stroke")
    y_off = y_first
    for ln in lines:
        if stroke:
            draw.text((pad, y_off), ln, font=font, fill=(*spec["color"], 255),
                      stroke_width=max(1, int(round(stroke["px"]))), stroke_fill=(*stroke["color"], 255))
        else:
            draw.text((pad, y_off), ln, font=font, fill=(*spec["color"], 255))
        y_off += line_h
    # 字幕底框：把画布裁成「墨迹框 + 内边距」，先在底层铺矩形再叠字迹（剪映 style=1 是直角）
    bg = spec.get("background")
    if bg:
        ink = img.split()[3].getbbox()
        if ink:
            pad_bg = max(2, int(round(SUB_BG_PAD_EM * font_px)))
            bw = max(2, int(round((ink[2] - ink[0]) + 2 * pad_bg)))
            bh = max(2, int(round((ink[3] - ink[1]) + 2 * pad_bg)))
            layer = Image.new("RGBA", (bw, bh), (*bg["color"], int(round(255 * bg["alpha"]))))
            layer.alpha_composite(img.crop(ink), (pad_bg, pad_bg))
            img = layer
            w, h = img.size
    out_png.parent.mkdir(parents=True, exist_ok=True)
    _ga = float(spec.get("alpha") if spec.get("alpha") is not None else 1.0)
    if _ga < 0.999:
        # 素材级不透明度：叠底框前把整张字迹（含阴影）的 alpha 统一乘下去
        img.putalpha(img.split()[3].point(lambda v: int(round(v * _ga))))
    img.save(out_png)
    # 自证：墨迹宽度与预期差太多 = 字体回退了，留痕不假装
    try:
        ink = img.split()[3].getbbox()
        ink_w = (ink[2] - ink[0]) if ink else 0
        exp_w = sum(1 for ch in "".join(lines) if not ch.isspace()) * font_px * 0.5
        if ink_w < exp_w * 0.5 and fail_log is not None:
            fail_log.append({"text": text[:12], "px": font_px, "ink_w": ink_w, "exp_w": round(exp_w),
                             "err": "ink_too_small: 字体回退或测量异常"})
    except Exception:
        pass
    return out_png, w, h


def _kf_pts(seg: dict, ptype: str) -> list:
    """取段上某类 keyframe 的 (相对秒, 值) 序列；无则空表。"""
    out = []
    for kfo in seg.get("common_keyframes") or []:
        if kfo.get("property_type") != ptype:
            continue
        for k in kfo.get("keyframe_list") or []:
            v = k.get("values") or []
            if v:
                out.append((float(k.get("time_offset") or 0) / US, float(v[0])))
    out.sort()
    return out


def _kf_final(seg: dict, ptype: str):
    pts = _kf_pts(seg, ptype)
    return pts[-1][1] if pts else None


def _kf_rel(seg: dict, ptype: str, src_in: float) -> list:
    """keyframe 时间是源时间（= 段源入点起的偏移），统一归一到段内相对秒。"""
    return [(max(0.0, t - float(src_in or 0.0)), v) for t, v in _kf_pts(seg, ptype)]


def _kf_steady(pts: list, dur: float):
    """取“保持时间最长”的那个 keyframe 值作为静态态。
    剪映的 clip.* 静态值可能是动画起点也可能终点（推近 vs 飞出），靠这个分辨：
    段首→首keyframe、末keyframe→段尾 两段谁长就取谁。"""
    if not pts:
        return None
    hold_first = pts[0][0]
    hold_last = max(0.0, float(dur) - pts[-1][0])
    return pts[0][1] if hold_first >= hold_last else pts[-1][1]


def _kf_piecewise(pts: list, t_start: float, pix: float, offset: float) -> str:
    """两点 keyframe → overlay x/y 分段线性表达式（首 keyframe 前保持首值，末点后保持末值）。"""
    (t0, v0), (t1, v1) = pts[0], pts[1]
    a0 = t_start + t0
    a1 = t_start + t1
    p0 = v0 * pix + offset
    p1 = v1 * pix + offset
    span = max(1e-3, a1 - a0)
    return (f"if(lt(t,{a0:.4f}),{p0:.2f},"
            f"if(lt(t,{a1:.4f}),{p0:.2f}+({p1 - p0:.2f})*(t-{a0:.4f})/{span:.4f},{p1:.2f}))")


def _kv(pts: list, t: float) -> float:
    """keyframe 点列（相对秒, 值）→ t 时刻的值：段内线性插值，首点前/末点后保持。"""
    if not pts:
        return 0.0
    if t <= pts[0][0]:
        return pts[0][1]
    if t >= pts[-1][0]:
        return pts[-1][1]
    for i in range(1, len(pts)):
        t0, v0 = pts[i - 1]
        t1, v1 = pts[i]
        if t <= t1:
            span = max(1e-6, t1 - t0)
            return v0 + (v1 - v0) * (t - t0) / span
    return pts[-1][1]


KF_SCALE_MAX_SLICES = 120


def kf_scale_schedule(px: list, py: list, t_start: float, t_dur: float, fps: float):
    """缩放关键帧点列 → 逐帧几何时间表 [(段内起, 段内止, scale_x, scale_y)]，覆盖整段 [0, t_dur]。

    为什么必须逐帧（旧实现取「保持最久」的稳态值一处画完）：
      剪映把「连续缩小」写成两点 keyframe，播放时是**逐帧插值**——既不是常数、也不是两段拼接。
      style_21 人像叠层 KFTypeScaleX 1.5 → 1.2（源时间 2.4→2.5667，即段内 0→0.1667s），
      旧实现按常数 1.2 画，人像整层小一圈：1.683s 整帧差 14.77，同段稳定帧只有 6–8。

    采样口径与主轨缩窗那条（已验证）一致：**帧起点**采样，第 i 帧用「这一帧起点时刻」的曲线值
    （t = (f0+i)/fps），不做半帧中点补偿——中点采样会让整段领先原版半帧，缩窗那段实测差 83 px。
    掐点按帧号对齐（round(t·fps)）；按秒均分会取到非整帧的子段，拼接后帧网格漂移。

    px/py 的 t 都是**段内相对秒**（调用方已用容器 PTS 基归一）；py 缺省 = 跟 px 等比。
    返回 None = 这条曲线不值得/不能切帧（单点、零跨度、或帧数超上限）。
    """
    if not px or len(px) < 2:
        return None
    pts = sorted(px)
    t0, t1 = pts[0][0], pts[-1][0]
    if t1 - t0 <= 1e-6:
        return None
    t1 = min(t1, float(t_dur))
    f0 = int(round((t_start + t0) * fps))
    f1 = int(round((t_start + t1) * fps))
    n = f1 - f0
    if n <= 0 or n > KF_SCALE_MAX_SLICES:
        return None

    def _pair(t_rel: float):
        sx = float(_kv(pts, t_rel))
        sy = float(_kv(sorted(py), t_rel)) if py else sx
        return sx, sy

    out = []
    a0 = f0 / fps - t_start
    if a0 > 1e-6:
        out.append((0.0, a0) + _pair(a0))
    for i in range(n):
        ta = (f0 + i) / fps - t_start
        tb = (f0 + i + 1) / fps - t_start
        out.append((ta, tb) + _pair(ta))
    a1 = f1 / fps - t_start
    if t_dur - a1 > 1e-6:
        out.append((a1, float(t_dur)) + _pair(a1))
    return out


def _anim_base_png(src: str, mat: dict, geom: dict, out_png: Path, flip: dict | None = None) -> Path:
    """源素材 → geom 尺寸（crop→contain→×scale）的静态底图，作为逐帧动画层的模板。

    和静态路径的 ffmpeg 滤镜链同参数（crop 框、lw/lh），差别只是用 PIL 落图——
    这样动画层与静态层的几何完全一致，不会因为两套插值差半个像素。
    """
    from PIL import Image as _I
    if out_png.is_file():
        return out_png
    with _I.open(src) as im:
        im = im.convert("RGBA")
        crop = geom.get("crop")
        if crop:
            x, y, w, h = crop
            im = im.crop((x, y, x + w, y + h))
        if im.size != (geom["lw"], geom["lh"]):
            im = im.resize((geom["lw"], geom["lh"]), _I.LANCZOS)
        fl = flip or {}
        if fl.get("h"):
            im = im.transpose(_I.FLIP_LEFT_RIGHT)
        elif fl.get("v"):
            im = im.transpose(_I.FLIP_TOP_BOTTOM)
        out_png.parent.mkdir(parents=True, exist_ok=True)
        im.save(out_png)
    return out_png


def _anim_video_frames(src: str, geom: dict, s_in: float, dur: float, fps: int,
                       out_dir: Path, flip: dict | None = None) -> list:
    """视频素材 → 按输出帧率抽成 geom 尺寸的 PNG 序列（带 crop/scale/flip），供逐帧动画层用。

    为什么不能只抽一帧：出场淡出、滑出这类动画挂在真实视频层上，抽一帧等于把 4 秒底片
    冻成静图（style_21 的背景层就是这么来的）。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    pat = out_dir / "s%06d.png"
    have = sorted(out_dir.glob("s*.png"))
    if not have:
        vf = []
        if geom.get("crop"):
            x, y, w, h = geom["crop"]
            vf.append(f"crop={w}:{h}:{x}:{y}")
        vf.append(f"scale={geom['lw']}:{geom['lh']}:flags=bicubic")
        fl = flip or {}
        if fl.get("h"):
            vf.append("hflip")
        elif fl.get("v"):
            vf.append("vflip")
        run_ffmpeg(["ffmpeg", "-y", "-ss", f"{s_in:.4f}", "-t", f"{max(0.05, dur):.4f}", "-i", src,
                    "-vf", f"{','.join(vf)},fps={fps}", "-an", str(pat)], timeout=900)
        have = sorted(out_dir.glob("s*.png"))
    return have


def classify_tracks(draft: dict) -> dict:
    tracks = draft.get("tracks") or []
    vid_main, vid_over, txt_tracks, stk_tracks, fx_tracks, aud_tracks = [], [], [], [], [], []
    for idx, t in enumerate(tracks):
        tt = t.get("type")
        segs = t.get("segments") or []
        if tt == "video":
            (vid_over if t.get("flag") == 2 else vid_main).append((idx, segs))
        elif tt == "text":
            txt_tracks.append((idx, segs))
        elif tt == "sticker":
            stk_tracks.append((idx, segs))
        elif tt == "effect":
            fx_tracks.append((idx, segs))
        elif tt == "audio":
            aud_tracks.append((idx, segs))

    def _cover_len(item):
        return sum(int((s.get("target_timerange") or {}).get("duration") or 0) for s in item[1])

    vid_main.sort(key=_cover_len, reverse=True)
    return {
        "main": vid_main[0] if vid_main else (None, []),
        "overlays": vid_over,
        "texts": txt_tracks,
        "stickers": stk_tracks,
        "effects": fx_tracks,
        "audios": aud_tracks,
        "n_tracks": len(tracks),
    }


def _compound_is_visual(sub_draft: dict) -> bool:
    """复合片段有没有可见内容（视频轨上真有段）。纯音效叠层返回 False。"""
    for t in sub_draft.get("tracks") or []:
        if t.get("type") in ("video", "text", "sticker") and (t.get("segments") or []):
            return True
    return False


def _compound_has_personable_video(sub_draft: dict) -> bool:
    """复合片段里有没有真人视频素材（照片/文本拼的复合片段不抠像，直接上）。"""
    vids = (sub_draft.get("materials") or {}).get("videos") or []
    return any(v.get("type") == "video" and (v.get("path") or "") for v in vids)


# 剪映「模糊」特效（effect 634025 / resource_id 6739752823140913675）的真实 PSF。
# 不是拟合，是**读实现**得出（本机 effect 缓存里的 lua + GLSL）：
#   · GaussianBlurLayerScript:onEvent：`effects_adjust_blur` 值 v → intensity = v × 72；
#   · onUpdate：u_Sample = floor(intensity)，u_Steps = 1，u_ExpandFlag = 0；
#   · 权重 shader（GaussianBlur .frag）：w_i = normpdf(i/s·15, sigma=4)，s = u_Sample
#     → 高斯半径在 tap 索引空间恒为 4·N/15（N = floor(72v)），不是固定像素半径；
#   · 每 tap 的 uv 步长 = 1/ratio，ratio = 720·S/min(S_xy) → 每 tap = min(S_w,S_h)/720 图层像素；
#   · MidTex/OutputTex 缩到 RTRatio = 1−0.75v 再拉回，水平 / 垂直两趟可分离。
# 因此等效高斯 sigma（图层像素）= (4·floor(72v)/15) · min(S_w,S_h)/720。
# 1080×1920 图层 = 1.5 px/tap：v=0.36 → σ=10.0；v=0.5 → σ=14.4。
# 交叉验证（源草稿 A 卡片层 v=0.36，逐卡扫 σ 取 MAE 极小）：b3 18.33/17.10/16.67/16.45/16.35、
# b1 27.08/24.09/23.21/23.03/23.23（σ=3.6/6/8/10/12）→ 极小点就在 10–12，与公式一致。
# 旧常量 SEG_BLUR_GAIN=10（σ=3.6）把「tap 数」当成了「sigma」，差一个数量级。
# 同一 value 在不同显示尺寸下 σ 不同：σ 与 min(S) 成正比（按图层尺寸归一，不按画布）。
YES_BLUR_TAPS_GAIN = 72.0     # intensityRatio：tap 数 = floor(72·v)
YES_BLUR_SIGMA_TAPS = 4.0 / 15.0   # shader 里 sigma=4.0 作用在 i·15/N 上 → 索引空间 sigma = 4N/15
YES_BLUR_REF_WIDTH = 720.0    # shader 的 uv 步长口径：每 tap = min(S)/720 像素
SEG_BLUR_GAIN = float(os.environ.get("REPLICA_BLUR_GAIN") or 0.0)   # 非 0 时强制旧式线性口径（仅供 A/B 实验）


def yes_blur_sigma(value: float, layer_w: int = CANVAS_W, layer_h: int = CANVAS_H) -> float:
    """`effects_adjust_blur` 数值 → 等效高斯 sigma（图层像素）。"""
    import math as _m
    if SEG_BLUR_GAIN > 0:
        return max(1.0, SEG_BLUR_GAIN * float(value))
    taps = int(_m.floor(YES_BLUR_TAPS_GAIN * max(0.0, float(value))))
    px_per_tap = min(int(layer_w), int(layer_h)) / YES_BLUR_REF_WIDTH
    return max(1.0, YES_BLUR_SIGMA_TAPS * taps * px_per_tap)


def _animbake_media(src: str, s_in: float, dur: float, specs: list, seg_dur: float,
                    fps: int, out_dir: Path, zoom_amp_fn, flash_fn) -> Path:
    """主轨段级「横向模糊」入场：径向变焦模糊 + 白光过曝，逐帧用 PIL 烘成同尺寸底片。

    为什么不用 ffmpeg：径向/变焦模糊没有任何单链滤镜能表达（`-vf` 只吃一条链，`split`+`blend`
    要 filter_complex，而主轨段的几何/缩放由 render_mp4 的 `-vf` 链路负责，插不进去）。
    烘出来的底片仍是源尺寸，后续 scale/pad/pip 几何照旧由 EDL 驱动。
    """
    from PIL import Image, ImageFilter  # noqa: F401
    import numpy as np
    n = max(1, int(round(dur * fps)))
    seq = out_dir / ("animbake_" + _cache_tag("zbake", src, round(s_in, 4), round(dur, 4), fps,
                                              [round(zoom_amp_fn(i / fps, n), 5) for i in range(n)],
                                              [round(flash_fn(i / fps, n), 4) for i in range(n)]))
    out = seq.with_suffix(".mp4")
    if out.is_file():
        return out
    seq.mkdir(parents=True, exist_ok=True)
    for old in seq.glob("*.png"):
        old.unlink()
    run_ffmpeg(["ffmpeg", "-y", "-v", "error", "-ss", f"{s_in:.4f}", "-t", f"{dur + 0.2:.4f}", "-i", src,
                "-vf", f"fps={fps}", "-frames:v", str(n), str(seq / "s%05d.png")], timeout=900)
    paths = sorted(seq.glob("s*.png"))
    if not paths:
        raise RuntimeError(f"主轨动画烘焙取帧失败: {src}")
    for i, pp in enumerate(paths):
        amp = zoom_amp_fn(i / fps, n)
        flash = flash_fn(i / fps, n)
        if amp <= 1e-4 and flash <= 1e-4:
            continue
        with Image.open(pp) as im0:
            im = im0.convert("RGB")
        W0, H0 = im.size
        if amp > 1e-4:
            acc = np.zeros((H0, W0, 3), dtype=np.float64)
            taps = 9
            for j in range(taps):
                k = 1.0 + amp * ((j / (taps - 1)) - 0.5) * 2.0
                w2 = max(2, int(round(W0 * k / 2)) * 2)
                h2 = max(2, int(round(H0 * k / 2)) * 2)
                t = im.resize((w2, h2), Image.BICUBIC)
                l = (w2 - W0) // 2
                tp = (h2 - H0) // 2
                acc += np.asarray(t.crop((l, tp, l + W0, tp + H0))).astype(np.float64)
            acc /= taps
            im = Image.fromarray(np.clip(acc, 0, 255).astype(np.uint8))
        if flash > 1e-4:
            a = np.asarray(im).astype(np.float64)
            im = Image.fromarray(np.clip(a * (1.0 - flash) + 255.0 * flash, 0, 255).astype(np.uint8))
        im.save(pp)
    run_ffmpeg(["ffmpeg", "-y", "-v", "error", "-framerate", f"{fps}", "-i", str(seq / "s%05d.png"),
                "-t", f"{dur:.4f}", "-c:v", "libx264", "-preset", "fast", "-crf", "12",
                "-pix_fmt", "yuv420p", str(out)], timeout=1800)
    return out


def _effect_blur_steps(seg: dict, effect: dict, t_start: float, dur: float, fps: int,
                       layer_w: int = CANVAS_W, layer_h: int = CANVAS_H) -> list:
    """effect 轨「模糊」→ [(t0, t1, sigma)] 时间表，**按 effects_adjust_blur 关键帧逐帧算**。

    原来什么：把整段当成一个常数 sigma，窗口内一刀切。style_21 的 effect 段（1.667s，时长 0.167s）
    带关键帧 0.5 →（0.1667s）0.0，等于开帧满模糊、逐帧衰减到零；旧实现整段都按 0.5 的 σ=14.4，
    画面到窗口末尾该清晰了还糊着，第一帧的字幕也被一起糊掉。
    改成什么：把关键帧线性插值成逐帧值，每帧一个 σ（60fps 下 10 帧就 10 段），
    时间窗按帧切开（[t0+k/fps, t0+(k+1)/fps]），交给 gblur 的 enable 窗口。
    """
    base = 0.5
    for p in effect.get("adjust_params") or []:
        if p.get("name") == "effects_adjust_blur":
            v = p.get("value")
            base = float(v if v is not None else (p.get("default_value") or 0.5))
    kfs = _kf_pts(seg, "effects_adjust_blur")   # [(段内相对秒, 值)]
    if not kfs:
        return [(t_start, t_start + dur, yes_blur_sigma(base, layer_w, layer_h))]

    def _val(rel: float) -> float:
        if rel <= kfs[0][0]:
            return kfs[0][1]
        if rel >= kfs[-1][0]:
            return kfs[-1][1]
        for (a, va), (b, vb) in zip(kfs, kfs[1:]):
            if a <= rel <= b:
                u = 0.0 if b <= a else (rel - a) / (b - a)
                return va + (vb - va) * u
        return kfs[-1][1]

    n = max(1, int(round(dur * fps)))
    n = min(n, 24)   # 长窗口（如 5s 淡出）不能一帧一个滤镜：最多 24 段，σ 阶梯在 0.2s 内人眼看不出
    out = []
    for i in range(n):
        s = yes_blur_sigma(_val((i + 0.5) * dur / n), layer_w, layer_h)
        out.append((t_start + i * dur / n, t_start + (i + 1) * dur / n, s))
    return out


def _seg_blur_sigma_of(seg: dict, vfx: dict, layer_size: tuple | None = None):
    """段级画面特效真值：extra_material_refs 里的 video_effects（如「模糊」）。"""
    for _ref in seg.get("extra_material_refs") or []:
        _e = vfx.get(_ref)
        if not _e or "模糊" not in str(_e.get("name") or ""):
            continue
        _val = 0.5
        for _p in _e.get("adjust_params") or []:
            if _p.get("name") == "effects_adjust_blur":
                _v = _p.get("value")
                _val = float(_v if _v is not None else (_p.get("default_value") or 0.5))
        _lw, _lh = (layer_size or (CANVAS_W, CANVAS_H))
        return yes_blur_sigma(_val, _lw, _lh)
    return None


# ---------- 抠像「撕纸描边」（materials.videos[*].matting.strokes） ----------
# 草稿真值（style_21 0–1.667s 那条人像叠加层）：
#   enable_matting_stroke=true, color [1,1,1,1],
#   effects_adjust_size=0.35 / effects_adjust_distance=0.25 / effects_adjust_texture=0.6,
#   resource_name「撕纸描边」。旧实现完全没做 → 原版沿人像轮廓那圈白色纸边全丢。
#   **归因如实：它不是那三点的主因。** 按最优环宽画回去只降 0.75（1.0s：21.12→20.37），
#   因为原版纸带里只有约 1/3 面积是纸白（lum>200 的占比：环带 33% vs 环内/远景基线 19%/15%），
#   剩下的差量在一条 30–80 px 的宽环带上——那里我们的背景层整块暗 15–20 级、
#   且与原版相关性≈ 0（不同内容，不是调色/模糊/色域能拉的，见交付报告）。
#
# 像素换算常数全部由**原版帧反解**，不是拍脑袋：
#   · 取样：原版 style_21 的 0.5/1.0/1.5s 三帧；图层 scale=1.5（草稿 clip.scale 稳态值，
#     layer_geometry 得 1620×2880 贴回 1080×1920 画布）→ 图层像素 1:1 等于画布像素。
#   · 环宽：x=430..660 共 58 条扫描线，量「人像 alpha 边界之外」的连续白带垂直厚度，
#     中位 12–14 px；该段轮廓法向与竖直夹角 30–40° → 法向环宽 ≈ 10–11 px。
#     另取 x=540 单列逐像素复核：白带 y=226..236（11 px），内缘 225:139→226:236，
#     外缘 236:235→237:194→238:136 → 两侧各约 2 px 过渡。
#   · 与素材原生像素的关系：图层被 ×1.5 放大，故源像素环宽 = 11/1.5 ≈ 7.3 px，
#     羽化 sigma ≈ 1.0/1.5 ≈ 0.67 源像素。
#   · 反解常数：GAIN = 7.3 / size(0.35) ≈ 21 px/单位（1080 宽口径）；
#     羽化 = 0.67 源像素/单位。撕纸本身不规则，沿轮廓实测 7–13 px，单常数残差 ±2.5 px。
#   · distance=0.25 实测**没有可见间隙**（x=540 处白带内缘直接贴住头发 alpha 边界，
#     中间那 1 px 是头发自身的抗锯齿），所以不做成旋钮——留一个能调但不生效的参数
#     比不做更坏，这里如实记成「实测 0 间隙」。
STROKE_SIZE_GAIN = float(os.environ.get("REPLICA_STROKE_GAIN") or 21.0)      # px / size 单位（1080 宽口径）
STROKE_FEATHER_SRC = float(os.environ.get("REPLICA_STROKE_FEATHER") or 0.667)  # 高斯 sigma / 源像素
# texture：撕纸颗粒。0.6 档反解为「外缘半径抖动 + 环内 alpha 颗粒」。系数扫掠实测
# （同一段、只改这两个系数，三点差量 0.5/1.0/1.5s）：
#   texture 全关        14.51 / 20.57 / 19.91
#   1 八度 0.35/0.25    14.36 / 20.37 / 19.79
#   2 八度 0.50/0.50    14.11 / 20.13 / 19.54  ← 取这档（指标与视觉都最好）
# 所以 texture 不是「能调但不生效」的空捻钮；仍然是反解值域内的粗标定。
STROKE_TEX_JITTER = float(os.environ.get("REPLICA_STROKE_TEX_JITTER") or 0.5)
STROKE_TEX_GRAIN = float(os.environ.get("REPLICA_STROKE_TEX_GRAIN") or 0.5)
# 抠像「外扩余量」（画布像素，1080 宽口径）：剪映的快速画笔抠像不是贴着人像轮廓剪的，
# 而是人像外面**留一圈全亮原片**，纸边压在这圈的外沿。
#
# 反解过程（原版逐帧量，不是拟合）：
#   · 逐帧把原版与「主轨背景层（0.65 压黑）」相减，残差在紧轮廓外是一条宽 60 px 的亮带，
#     亮度 ≈ 原片本色（残差 /(1-0.65) 复原：5–20 px 带 169.2，原片 166.9；
#     30–60 px 带 144.4 对 160.8），且 **sigma=0 拟合最好**（锐利原片，加高斯模糊只会更差：
#     sigma 0/8/25/60 的 MAE 27.10/27.16/29.18/32.07）→ 是「没抠掉的画面」，不是光晕。
#   · 原版纯白像素（min>195 且饱和度<22）相对紧轮廓的距离直方图，0.5/0.75/1.0/1.25/1.5s
#     五个时刻都在 45–60 px 处出现同一个峰（40–45/45–50/50–55/55–60 px 计数
#     4964/5881/5803/4253 @1.0s），紧轮廓外 3–40 px 基本没有白像素 → 纸边确实在外沿。
#   · 环宽仍取反解值 11 px（见上），环带中心 ≈ 52 px → 外扩量 = 52 - 11/2 ≈ 46 px。
# 这不是「能调但不生效」的旋钮（都实测过）：
#   D=0（旧实现）1.0s 差量 20；D=46 → 17（成片实测，全片 MEAN 12.97 → 12.68）；
#   numpy 同帧扫 D=30/40/50/60/70 为 18.26/17.59/17.11/17.17/17.82 → 40–60 是平台。
STROKE_MATTE_DILATE = float(os.environ.get("REPLICA_STROKE_DILATE") or 46.0)


def matting_stroke_spec(mat: dict) -> dict | None:
    """素材 matting → 撕纸描边 cfg；没启用描边返回 None。

    只有 matting.path 非空（原版真的抠了像）且 enable_matting_stroke=true 才成立。
    描边是**从 alpha 派生**的：抠像失败时 alpha 全黑，环宽自然归零，不会凭空画白圈。
    """
    mt = mat.get("matting") or {}
    if not mt.get("path") or not mt.get("enable_matting_stroke"):
        return None
    strokes = mt.get("strokes") or []
    st = strokes[0] if strokes else {}
    vals = {}
    for p in st.get("adjust_params") or []:
        v = p.get("value")
        vals[str(p.get("name"))] = float(v if v is not None else (p.get("default_value") or 0.0))
    col = list(st.get("color") or [1.0, 1.0, 1.0, 1.0]) + [1.0, 1.0, 1.0]
    rgb = tuple(max(0, min(255, int(round(float(c) * 255)))) for c in col[:3])
    return {
        "color": rgb,
        "size": vals.get("effects_adjust_size", 0.35),
        "texture": vals.get("effects_adjust_texture", 0.0),
    }


def bake_stroke_layer(seg_video: Path, matte_video: Path, out_mov: Path, spec: dict,
                      size: tuple, fps: int, ffmpeg_bin: str = "ffmpeg") -> Path:
    """人像 RGBA + 撕纸描边 → 一条 ProRes4444 图层（描边压在人物**下方**）。

    为什么逐帧 numpy 而不是滤镜图：环宽要按 alpha 的距离场算（等距圆环，不能像可分离
    膨胀那样在 45° 方向胖 41%），纸纹还要一张稳定的噪声场；ffmpeg 的 morpho 只吃二值
    结构元、noise 又是逐帧随机，拼不出稳定纸纹。这段只有 1.5 秒，烘一次就缓存住。
    """
    import numpy as np
    from scipy import ndimage

    W, H = int(size[0]), int(size[1])
    scale = W / float(CANVAS_W)
    width = max(1.5, float(spec["size"]) * STROKE_SIZE_GAIN * scale)
    dilate = max(0.0, STROKE_MATTE_DILATE) * scale
    sigma = max(0.3, STROKE_FEATHER_SRC * scale)
    tex = max(0.0, min(1.0, float(spec.get("texture") or 0.0)))
    rgb = np.array(spec["color"], dtype=np.float32)

    # 纸纹：双八度平滑噪声场（粗 16px 块起伏 + 细 4px 纸纤维），整段共用一张——
    # 撕纸纹理是贴在纸上的，不逐帧闪。只用单一粗噪声时环宽是「平滑波浪」，
    # 拉不出原版那种锯齿+毛边（自评 4/10）；加一层细八度才有纤维感。
    rng = np.random.default_rng(20261123)

    def _octave(shrink: int) -> np.ndarray:
        s = rng.random((H // shrink + 2, W // shrink + 2)).astype(np.float32)
        return ndimage.zoom(s, shrink, order=3)[:H, :W]

    field = np.clip(0.55 * _octave(16) + 0.45 * _octave(4), 0.0, 1.0)
    field = (field - field.min()) / max(1e-6, field.max() - field.min())

    out_mov.parent.mkdir(parents=True, exist_ok=True)
    dec_v = subprocess.Popen([ffmpeg_bin, "-v", "error", "-i", str(seg_video),
                              "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    dec_m = subprocess.Popen([ffmpeg_bin, "-v", "error", "-i", str(matte_video),
                              "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    enc = subprocess.Popen([ffmpeg_bin, "-y", "-v", "error", "-f", "rawvideo",
                            "-pix_fmt", "rgba", "-s", f"{W}x{H}", "-r", f"{fps}", "-i", "-",
                            "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le",
                            str(out_mov)], stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
    fsz_v, fsz_m = W * H * 3, W * H
    n_done = 0
    try:
        while True:
            bv = dec_v.stdout.read(fsz_v)
            bm = dec_m.stdout.read(fsz_m)
            if not bv or not bm or len(bv) < fsz_v or len(bm) < fsz_m:
                break
            fg = np.frombuffer(bv, np.uint8).reshape(H, W, 3).astype(np.float32)
            a = np.frombuffer(bm, np.uint8).reshape(H, W).astype(np.float32) / 255.0
            if dilate > 0.5:
                # 快速画笔抠像的「外扩余量」：把 alpha 向外顶 dilate 像素，那圈留原片本色
                # （不是半透明白），纸边之后压在扩后的边界上。
                d_out = ndimage.distance_transform_edt(~(a > 0.5))
                a = np.maximum(a, np.clip(1.0 - (d_out - dilate) / 1.5, 0.0, 1.0))
            inside = a > 0.5
            dt = ndimage.distance_transform_edt(~inside)      # 0=人像内，>0=向外第几像素
            w_field = width * (1.0 + STROKE_TEX_JITTER * tex * (2.0 * field - 1.0))
            ring = np.clip(w_field - dt + 1.0, 0.0, 1.0)      # 环：alpha 边界向外 width px（半径不等距）
            ring *= (1.0 - a)                                 # 内缘跟着人像 alpha 走
            if STROKE_TEX_GRAIN > 0 and tex > 0:
                ring *= np.clip(1.0 - STROKE_TEX_GRAIN * tex * (1.0 - field), 0.0, 1.0)
            if sigma > 0.2:
                ring = ndimage.gaussian_filter(ring, sigma)   # 羽化（实测两侧各 ~2 px 过渡）
            ring = np.clip(ring, 0.0, 1.0)
            out_a = a + ring * (1.0 - a)                      # 描边压在人物下方
            denom = np.maximum(out_a, 1e-4)[:, :, None]
            out_rgb = (fg * a[:, :, None] + rgb[None, None, :] * (ring * (1.0 - a))[:, :, None]) / denom
            frame = np.concatenate([np.clip(out_rgb, 0, 255),
                                    (np.clip(out_a, 0, 1) * 255.0)[:, :, None]], axis=2)
            enc.stdin.write(frame.astype(np.uint8).tobytes())
            n_done += 1
    finally:
        for p in (dec_v, dec_m):
            try:
                p.stdout.close()
            except Exception:
                pass
            p.wait()
        try:
            enc.stdin.close()
        except Exception:
            pass
        enc.wait()
    if n_done == 0 or not out_mov.is_file():
        raise RuntimeError(f"撕纸描边烘焙失败（{seg_video} + {matte_video}）")
    return out_mov


def render_timeline(draft: dict, base_dir: Path, style_id: str, work: Path, fps_default: int,
                    preview: bool, report: dict, depth: int = 0, audio_only: bool = False,
                    alpha_out: bool = False) -> Path:
    """复刻一条时间线（含递归复合片段），返回最终 mp4。"""
    sys.path.insert(0, str(SCRIPTS))
    from common import read_json, write_json, find_bin, get_wav_duration_sec  # noqa: E402
    import render_mp4 as R  # noqa: E402
    import mask_shapes as MS  # noqa: E402
    from step7_color_grade import run_step7  # noqa: E402
    from person_matting import generate_person_matte_video  # noqa: E402
    from animations import (parse_animations, anim_state, alpha_ramp, fallback_names,  # noqa: E402
                            summarize, AnimState, blur_sigmas, blur_window, family_of,
                            zoom_amp, flash_alpha)
    from anim_render import render_animated_layer  # noqa: E402

    ffmpeg_bin = find_bin("ffmpeg")

    tag = "tl" if depth == 0 else f"c{depth}"
    pdir = work / "pipeline"
    media_dir = work / "media"
    matte_dir = work / "matte"
    text_dir = work / "text_png"
    comp_dir = work / "compounds"
    for d in (pdir, media_dir, matte_dir, text_dir, comp_dir):
        d.mkdir(parents=True, exist_ok=True)

    mats = draft.get("materials") or {}
    fps = int(round(float(draft.get("fps") or fps_default)))
    canvas = draft.get("canvas_config") or {}
    canvas_w = int(canvas.get("width") or CANVAS_W)
    canvas_h = int(canvas.get("height") or CANVAS_H)
    # 字号单位：实测 font_size × 画布宽/172.8 ≈ 真实像素。
    # 旧值 /192 偏小 11%——硬证据：style_17 6.0s 的黄字「不要想着买新车」(size 12) 原版墨高 69px、复刻 62px，
    # 宽 517 vs 468，两个方向都是 10.5%，与 192/172.8=1.111 严丝合缝。
    pxu = canvas_w / 172.8
    total_sec = float(draft.get("duration") or 0) / US
    if total_sec <= 0:
        segs_all = [s for t in draft.get("tracks") or [] for s in (t.get("segments") or [])]
        total_sec = max(
            ((s.get("target_timerange") or {}).get("start", 0) + (s.get("target_timerange") or {}).get("duration", 0))
            for s in segs_all
        ) / US
    W, H = CANVAS_W, CANVAS_H
    if depth == 0:
        report["fps"] = fps
        report["duration_sec"] = round(total_sec, 3)

    vid_by_id = {v["id"]: v for v in mats.get("videos") or []}
    aud_by_id = {a["id"]: a for a in mats.get("audios") or []}
    vfx_by_id = {e.get("id"): e for e in mats.get("video_effects") or []}

    def _seg_blur_sigma(seg: dict):
        """段级画面特效真值：extra_material_refs 里的 video_effects（如「模糊」）。"""
        return _seg_blur_sigma_of(seg, vfx_by_id)

    txt_by_id = {t["id"]: t for t in mats.get("texts") or []}
    stk_by_id = {s["id"]: s for s in mats.get("stickers") or []}
    mask_by_id = {m["id"]: m for m in mats.get("common_mask") or []}
    anim_by_id = {a["id"]: a for a in mats.get("material_animations") or []}
    drafts_entries = {dr["id"]: dr for dr in (mats.get("drafts") or []) if dr.get("draft")}

    # ---------- 1.5 剪映遮罩：alpha 场按 shader 数学算（mask_shapes），按 cfg+脚本指纹缓存 ----------
    def mask_alpha_png(cfg: dict, mw: int | None = None, mh: int | None = None) -> Path:
        """遮罩 alpha 场 PNG。mw/mh = 图层（渲染纹理）尺寸：剪映 shader 的 u_aspect 取的就是
        图层自身宽高比（圆角半径按短边归一），拿画布比算会把 16:9 卡片的圆角算错一倍。"""
        import hashlib as _hl
        mw = int(mw or W)
        mh = int(mh or H)
        key = _hl.sha1((json.dumps(cfg, sort_keys=True, ensure_ascii=False)
                        + f"|{mw}x{mh}|{MS.FEATHER_GAIN}|{_mask_code_sig()}").encode()).hexdigest()[:12]
        png = media_dir / f"mask_alpha_{key}.png"
        if not png.is_file():
            MS.save_alpha_png(MS.mask_alpha(mw, mh, cfg), png)
        return png

    def masked_media(src: str, s_in: float, dur: float, cfg: dict) -> Path:
        """主轨口径：遮罩区内显示画面、区外纯黑，**整帧不透明**（烘成因×alpha 叠在黑底上）。

        遮罩作用于图层（源画面）自身的 uv 空间，因此必须在 scale/位移/缩窗之前烘进画面。
        """
        dur = max(0.05, float(dur))
        nfr = max(1, int(round(dur * fps)))
        out = media_dir / ("masked_" + _cache_tag("mask_main", s_in, dur, json.dumps(cfg, sort_keys=True)) + ".mp4")
        if not out.is_file():
            png = mask_alpha_png(cfg)
            # setpts + shortest=1：不给两条输入对齐 pts 的话，overlay 的 framesync 在 t=0 取不到 mask 帧，
            # 会把首帧输出成纯黑（实测每个遮罩子段开头黑 1 帧）。
            run_ffmpeg([ffmpeg_bin, "-y",
                        "-f", "lavfi", "-i", f"color=c=black:s={W}x{H}:r={fps}:d={dur:.4f}",
                        "-ss", f"{s_in:.4f}", "-t", f"{dur:.4f}", "-i", src,
                        "-loop", "1", "-i", str(png),
                        "-filter_complex",
                        f"[1:v]fps={fps},setpts=PTS-STARTPTS,{cover_crop_vf(W, H)},format=rgba[fg];"
                        f"[2:v]fps={fps},setpts=PTS-STARTPTS,format=gray[mask];[fg][mask]alphamerge[ma];"
                        f"[0:v]setpts=PTS-STARTPTS[bg];"
                        f"[bg][ma]overlay=0:0:shortest=1:eof_action=repeat:format=auto,format=yuv420p[v]",
                        "-map", "[v]", "-frames:v", str(nfr), "-an",
                        "-c:v", "libx264", "-preset", "medium", "-crf", "12", str(out)], timeout=600)
        return out

    def resolve_compound(seg: dict) -> dict | None:
        """段 → 内嵌子时间线 dict（经 extra_material_refs 关联）。"""
        for ref in seg.get("extra_material_refs") or []:
            dr = drafts_entries.get(ref)
            if dr:
                return dr["draft"]
        return None

    def materialize_photo(mat: dict, dur: float, out_base: Path) -> Path:
        outp = out_base.with_suffix(".mp4")
        if not outp.is_file():
            run_ffmpeg(["ffmpeg", "-y", "-loop", "1", "-t", f"{dur:.4f}",
                        "-i", _subst(mat.get("path") or "", base_dir),
                        "-vf", cover_crop_vf(W, H) + f",fps={fps}", "-an",
                        "-c:v", "libx264", "-preset", "fast", "-crf", "16",
                        "-pix_fmt", "yuv420p", str(outp)])
        return outp

    def _compound_is_audio(sub: dict) -> bool:
        return any((t.get("segments") or []) for t in sub.get("tracks") or [] if t.get("type") == "audio")

    # ---------- 1. 复合片段预渲染（唯一化：同一子时间线只渲一次） ----------
    compound_media: dict = {}  # drafts entry id -> mp4 path
    compound_audio: dict = {}  # drafts entry id -> (mp4, has_audio)
    cls = classify_tracks(draft)

    def _build_master(cls_local: dict, comp_media: dict, comp_audio: dict, out_name: str) -> Path:
        """逐段裁切→定位→amix 合成母带；无音轨的源（照片/静图）探测后跳过并计数。"""
        segs = []
        for _idx, tl in cls_local["audios"]:
            segs += tl
        # 主轨视频**自带的声音**：剪映里口播常常就是主轨素材的原声（草稿里没有独立口播音轨），
        # 只拼音效轨/叠加轨会把整段人声丢掉。
        segs += list(cls_local["main"][1])
        # 复合片段音效叠层挂在视频轨上的：也按其段位置进母带
        for _idx, segs_v in cls_local["overlays"]:
            segs += segs_v
        inputs, chains, mix_ins = [], [], []
        n_in = 0
        no_stream_cache: dict = {}
        for s in segs:
            mat = aud_by_id.get(s.get("material_id")) or vid_by_id.get(s.get("material_id")) or {}
            if mat.get("type") == "photo":
                continue
            tgt = s.get("target_timerange") or {}
            srcr = s.get("source_timerange") or {}
            t_start = float(tgt.get("start") or 0) / US
            t_dur = float(tgt.get("duration") or 0) / US
            speed = float(s.get("speed") or 1.0)
            src = ""
            if not (mat.get("path") or ""):
                for ref in s.get("extra_material_refs") or []:
                    if ref in comp_media and comp_audio.get(ref, (None, False))[1]:
                        src = comp_media[ref]
                        break
                if not src:
                    if depth == 0:
                        report["unrealized"]["audio_segments_missing_source"] = report["unrealized"].get("audio_segments_missing_source", 0) + 1
                    continue
            else:
                src = _subst(mat.get("path") or "", base_dir)
                if not Path(src).is_file():
                    if depth == 0:
                        report["unrealized"]["audio_segments_missing_source"] = report["unrealized"].get("audio_segments_missing_source", 0) + 1
                    continue
            if src not in no_stream_cache:
                no_stream_cache[src] = ffprobe_has_audio(src)
            if not no_stream_cache[src]:
                if depth == 0:
                    report["unrealized"]["audio_segments_no_stream"] = report["unrealized"].get("audio_segments_no_stream", 0) + 1
                continue
            s_start = float(srcr.get("start") or 0) / US
            s_dur = float(srcr.get("duration") or (t_dur * speed)) / US
            vol = 1.0
            if isinstance(s.get("clip"), dict) and s["clip"].get("volume") is not None:
                vol = float(s["clip"]["volume"])
            elif s.get("volume") is not None:
                vol = float(s["volume"])
            inputs += ["-ss", f"{s_start:.4f}", "-t", f"{max(0.05, s_dur):.4f}", "-i", src]
            delay_ms = int(round(t_start * 1000))
            speed_fx = f"atempo={speed:.6f}," if abs(speed - 1.0) > 1e-4 else ""
            vol_fx = f"volume={vol:.4f}," if abs(vol - 1.0) > 1e-4 else ""
            chains.append(
                f"[{n_in}:a]aresample=48000,aformat=channel_layouts=stereo,{speed_fx}{vol_fx}adelay={delay_ms}|{delay_ms}[a{n_in}]"
            )
            mix_ins.append(f"[a{n_in}]")
            n_in += 1
        out = pdir / out_name
        if not mix_ins:
            return None  # 这条时间线根本没有可定位的音频（调用方决定：正常流程给静音母带，audio_only 才是错误）
        fc = (";".join(chains) + ";" + "".join(mix_ins)
              + f"amix=inputs={len(mix_ins)}:normalize=0:duration=longest,alimiter=limit=0.95,apad=whole_dur={total_sec:.4f},atrim=0:{total_sec:.4f}[af]")
        run_ffmpeg(["ffmpeg", "-y", *inputs, "-filter_complex", fc, "-map", "[af]",
                    "-t", f"{total_sec:.4f}",
                    "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(out)], timeout=300)
        return out
    all_segs = []
    for _i, segs in cls["overlays"]:
        all_segs += segs
    for _i, segs in cls["audios"]:
        all_segs += segs
    all_segs += cls["main"][1]
    for s in all_segs:
        mat = vid_by_id.get(s.get("material_id")) or aud_by_id.get(s.get("material_id")) or {}
        if mat and not (mat.get("path") or ""):
            sub = resolve_compound(s)
            if sub is None:
                continue
            key = None
            for ref in s.get("extra_material_refs") or []:
                if ref in drafts_entries:
                    key = ref
                    break
            if key in compound_media:
                continue
            if depth >= MAX_COMPOUND_DEPTH:
                report["unrealized"]["compound_depth_exceeded"] = report["unrealized"].get("compound_depth_exceeded", 0) + 1
                continue
            cdir = comp_dir / key[:8]
            cdir.mkdir(parents=True, exist_ok=True)
            sub_mp4 = cdir / "compound_alpha.mov"  # 带 alpha 的复合片段用 MOV（ProRes4444 进不了 mp4）
            import hashlib as _hl
            _ver = cdir / "compound_alpha.ver"
            _code_sig = _hl.sha1(Path(__file__).read_bytes()).hexdigest()[:12]
            if not (sub_mp4.is_file() and _ver.is_file() and _ver.read_text().strip() == _code_sig):
                if not _compound_is_visual(sub):
                    # 纯音效叠层：只出音频母带，不渲画面（画面本来就没有）
                    sub_wav = cdir / "compound.wav"
                    sub_out = render_timeline(sub, base_dir, style_id, cdir, fps, False, report,
                                              depth + 1, audio_only=True)
                    if sub_out != sub_wav:
                        import shutil
                        shutil.move(str(sub_out), str(sub_wav))
                    compound_media[key] = sub_wav
                    compound_audio[key] = (sub_wav, True)
                    _ver.write_text(_code_sig)
                    if depth == 0:
                        report.setdefault("compounds_rendered", []).append(
                            {"ref": key[:8], "visual": False, "has_audio": True, "audio_only": True})
                    continue
                sub_out = render_timeline(sub, base_dir, style_id, cdir, fps, False, report, depth + 1,
                                          alpha_out=True)
                if sub_out != sub_mp4:
                    import shutil
                    shutil.move(str(sub_out), str(sub_mp4))
                _ver.write_text(_code_sig)
            compound_media[key] = sub_mp4
            compound_audio[key] = (sub_mp4, _compound_is_audio(sub))
            if depth == 0:
                report.setdefault("compounds_rendered", []).append(
                    {"ref": key[:8], "visual": _compound_is_visual(sub), "has_audio": _compound_is_audio(sub)}
                )

    if audio_only:
        # 纯音频时间线：只出母带 wav（复合片段音频 Shortcut）
        mw = _build_master(cls, compound_media, compound_audio, "master_only.wav")
        if mw is None:
            raise RuntimeError("纯音频复合片段里没有任何可定位的音频段")
        return mw

    # ---------- 2. 主轨 EDL ----------
    main_idx, main_segs = cls["main"]
    clips = []
    for k, s in enumerate(main_segs):
        mat = vid_by_id.get(s.get("material_id")) or {}
        if not mat:
            raise RuntimeError(f"主轨段 {k} 找不到素材 {s.get('material_id')}")
        mtype = mat.get("type")
        tgt = s.get("target_timerange") or {}
        srcr = s.get("source_timerange") or {}
        t_start = float(tgt.get("start") or 0) / US
        t_dur = float(tgt.get("duration") or 0) / US
        speed = float(s.get("speed") or 1.0)
        ckey = None
        if not (mat.get("path") or ""):
            for ref in s.get("extra_material_refs") or []:
                if ref in compound_media:
                    ckey = ref
                    break
        if ckey:
            src = compound_media[ckey]
            s_in = src_seek_time(float(srcr.get("start") or 0) / US, src)
            s_out = s_in + t_dur * speed
        elif mtype == "photo":
            src = materialize_photo(mat, max(t_dur, 0.5), media_dir / f"photo_{mat['id'][:8]}")
            s_in, s_out = 0.0, t_dur
        else:
            src = _subst(mat.get("path") or "", base_dir)
            s_in = src_seek_time(float(srcr.get("start") or 0) / US, src)
            s_out = s_in + t_dur * speed
        if not Path(src).is_file():
            raise RuntimeError(f"主轨素材不存在: {src}")
        clip = {
            "clip_id": f"m{k:03d}",
            "track_role": "a_roll",
            "media_path": src,
            "source_in_sec": round(s_in, 4),
            "source_out_sec": round(s_out, 4),
            "duration_sec": round(t_dur, 4),
            "timeline_in_frame": int(round(t_start * fps)),
            "timeline_out_frame": int(round((t_start + t_dur) * fps)),
        }
        cl = s.get("clip") or {}
        sc0 = float((cl.get("scale") or {}).get("x") or 1.0)
        tx0 = float((cl.get("transform") or {}).get("x") or 0.0)
        ty0 = float((cl.get("transform") or {}).get("y") or 0.0)
        alpha = float(cl.get("alpha") if cl.get("alpha") is not None else 1.0)
        if abs(alpha - 1.0) > 1e-3:
            report["unrealized"]["main_alpha"] = report["unrealized"].get("main_alpha", 0) + 1
        # keyframe（时间为源时间）：scale 动画拆段；位移取终态（推近类动画的偏移通常归零）
        # 关键帧 time_offset 与 source_timerange.start **同口径**（素材容器 PTS），
        # 不是 ffmpeg 的 `-ss` 口径 —— 后者少了首帧 PTS。缩窗那段拿它逐帧解算（见下）。
        _kf_base = s_in + media_start_time(src)
        _kfs = _kf_rel(s, "KFTypeScaleX", s_in)
        _kfs_c = _kf_rel(s, "KFTypeScaleX", _kf_base)
        _fx = _kf_final(s, "KFTypePositionX")
        _fy = _kf_final(s, "KFTypePositionY")
        tx = _fx if _fx is not None else tx0
        ty = _fy if _fy is not None else ty0
        _t_end = t_start + t_dur
        pieces = []  # (t_a, t_b, scale|None=kenburns, tx, ty)
        if len(_kfs) == 2 and abs(_kfs[0][1] - _kfs[1][1]) > 1e-3 and _kfs[1][0] > _kfs[0][0]:
            _shrink = min(_kfs[0][1], _kfs[1][1]) < 0.999
            # 缩窗走容器口径（帧起点采样），推近保持原口径（不改已验证的那条路）
            _a0, _s0 = (_kfs_c if _shrink else _kfs)[0]
            _a1, _s1 = (_kfs_c if _shrink else _kfs)[1]
            _a0 = min(_a0, t_dur)
            _a1 = min(_a1, t_dur)
            if _a0 > 1e-3:
                pieces.append((t_start, t_start + _a0, _s0, tx, ty))
            if min(_s0, _s1) >= 0.999:
                # 纯推近/拉远（zoom≥1）：改成**逐帧静态子段**，每段用自己的 scale 走几何。
                # 原来交给渲染器的 ken_burns 逐帧插值，但 ken_burns 的 z 曲线与剪映的关键帧不等价：
                # 原版 13.0s（12.93 起 1.1→1.0 / 0.333s）实测 zoom=1.08，而 ken_burns 段首几帧
                # 仍停在 1.10，全帧边缘错位 → 该点副本差量 26。改成 1 帧一步后子段 zoom 与草稿插值对齐。
                # 切点必须落在整帧上（按帧号算而非按秒均分），否则子段长度不是整帧，拼接后帧网格漂移。
                _f0 = int(round((t_start + _a0) * fps))
                _f1 = int(round((t_start + _a1) * fps))
                _n = max(1, min(16, _f1 - _f0))
                for _i in range(_n):
                    _ta = (_f0 + _i) / fps
                    _tb = (_f0 + _i + 1) / fps
                    # 子段代表值取子段中点：实测取起点会让 9.0s 从 4 掉到 19，
                    # 取中点全片更优（0.0s 那 5 级差额属半帧偏差，等接入缓动曲线再收）
                    _ss = _s0 + (_s1 - _s0) * ((_i + 0.5) / _n)
                    pieces.append((_ta, _tb, _ss, tx, ty))
                report["unrealized"]["main_kf_zoom_sliced"] = report["unrealized"].get("main_kf_zoom_sliced", 0) + _n
            else:
                # 含缩窗（scale<1）：**逐帧一段（1 帧/段），且段内位移 keyframe 也跟着插值**。
                #
                # 原版真值（60fps 逐帧量「画面上方纯黑带的下界」= 缩窗顶边，12.4667s 段首后）：
                #   12.5000/5167/5333/5500/5667 → 217/166/115/64/13，每帧正好 -51 px、无台阶。
                # 与草稿 keyframe 对照可逐点复现（scale 0.63→1.2 与 pos.y 0.036979→0 在 0.1667s
                # 内**同时**线性插值，缩窗 top = H(1-scale)/2 - pos.y·H/2）：
                #   p=0.2/0.3/0.4/0.5/0.6 算得 217.6/166.1/115.2/63.7/12.8 vs 实测 217/166/115/64/13。
                # 所以曲线本身是直线，跳变全来自渲染侧，两条：
                #   (a) 段内位移 keyframe 以前用 _kf_final 取**终值** 0 → 12.5s 顶边直接差 28 px；
                #   (b) 2 帧一段 + 取段中点 → 每段整体领先原版半帧（scale 差 0.087 = 83 px）。
                # 旧实验「1 帧/段反而更差（12.5s 25.6→29.7/全片 12.97→13.20）」是拿**中点采样**
                # 的 1 帧/段比的：段变短后中点偏差一点没减（均匀 +半帧），却少了 2 帧一段那种
                # 「一半帧对、一半帧错」的相互抵消——于是 12.4–12.6 这段整段单调偏大，测点全中招。
                # 现在两错一起改：帧起点采样（p=i/n）+ 逐帧位移，才是量到的原版曲线。
                _f0 = int(round((t_start + _a0) * fps))
                _f1 = int(round((t_start + _a1) * fps))
                _n = max(1, _f1 - _f0)
                _kpx = _kf_rel(s, "KFTypePositionX", _kf_base)
                _kpy = _kf_rel(s, "KFTypePositionY", _kf_base)
                for _i in range(_n):
                    _ta = (_f0 + _i) / fps
                    _tb = (_f0 + _i + 1) / fps
                    _p = _i / float(_n)                      # 帧*起点*时刻的动画进度
                    _ss = _s0 + (_s1 - _s0) * _p
                    _tr = _ta - t_start
                    pieces.append((_ta, _tb, _ss,
                                   _kv(_kpx, _tr) if _kpx else tx,
                                   _kv(_kpy, _tr) if _kpy else ty))
                report["unrealized"]["main_kf_zoom_sliced"] = report["unrealized"].get("main_kf_zoom_sliced", 0) + _n
            if _t_end > t_start + _a1 + 1e-4:
                pieces.append((t_start + _a1, _t_end, _s1, tx, ty))
            report["unrealized"]["main_keyframe_animations"] = report["unrealized"].get("main_keyframe_animations", 0) + 1
        else:
            _fs = _kfs[-1][1] if _kfs else None
            if _fs is not None or _fx is not None or _fy is not None:
                report["unrealized"]["main_keyframe_static_end"] = report["unrealized"].get("main_keyframe_static_end", 0) + 1
            pieces.append((t_start, _t_end, _fs if _fs is not None else sc0, tx, ty))
        # 剪映遮罩：config/keyframe → 时间表（静态就是单段），再把几何 pieces 按表切开
        _mask_cfg = segment_mask_config(s, mask_by_id, report)
        if _mask_cfg is not None:
            _sched = mask_schedule(s, _mask_cfg, t_dur, s_in, fps, report)
            if len(_sched) > 1:
                report.setdefault("notes", []).append(
                    f"主轨 {k}: 遮罩参数逐帧（{len(_sched)} 态）烘焙")
            pieces = split_pieces_by_mask(pieces, _sched, t_start)
        else:
            pieces = [tuple(p[:5]) + (None,) for p in pieces]
        # 主轨段级入场/出场动画（material_animations）：
        #   模糊族（横向模糊）＝径向变焦模糊 + 白光过曝 → 逐帧 PIL 烘成底片（无单链滤镜可表达）；
        #   位移/缩放族在主轨没有逐帧几何通路，如实计入 unrealized（不假装做了）。
        _m_specs = parse_animations(s, anim_by_id)
        _m_bw = blur_window(_m_specs, t_dur) if _m_specs else None
        _anim_media = None
        if _m_specs:
            _unsup = [sp for sp in _m_specs if family_of(sp.name) != "blur"]
            for _sp in _unsup:
                key = f"main_anim_unsupported:{_sp.name}"
                report["unrealized"][key] = report["unrealized"].get(key, 0) + 1
            if len(_unsup) < len(_m_specs) and _mask_cfg is None and len(_kfs) <= 2:
                _anim_media = _animbake_media(
                    src, s_in, t_dur, _m_specs, t_dur, fps, media_dir,
                    lambda tl, _n: zoom_amp(_m_specs, tl, t_dur),
                    lambda tl, _n: flash_alpha(_m_specs, tl, t_dur))
                src = str(_anim_media)
                s_in = 0.0
                pieces = [(t_start, _t_end, sc0, tx, ty, None, None, None)]
                report.setdefault("notes", []).append(
                    f"主轨 {k}: 段级「横向模糊」逐帧烘（变焦模糊+白场）")
                if depth == 0:
                    report["main_anim_baked"] = report.get("main_anim_baked", 0) + 1
        if _m_bw and _anim_media is None:
            # 只把动画窗内的 part 拆到帧，窗内每帧一个子段（各自的 sigma 由曲线给出）
            _wa, _wb = t_start + _m_bw[0], t_start + _m_bw[1]
            _cut = []
            for _p in pieces:
                _pa, _pb = _p[0], _p[1]
                _tail8 = (_p[2], _p[3], _p[4], _p[5])
                if _pb <= _wa + 1e-6 or _pa >= _wb - 1e-6:
                    _cut.append((_pa, _pb) + _tail8 + (None, None))
                    continue
                _lo, _hi = max(_pa, _wa), min(_pb, _wb)
                if _pa < _lo - 1e-6:
                    _cut.append((_pa, _lo) + _tail8 + (None, None))
                _f0 = int(round(_lo * fps))
                _f1 = max(_f0 + 1, int(round(_hi * fps)))
                for _f in range(_f0, _f1):
                    _sx, _sy = blur_sigmas(_m_specs, (_f + 0.5) / fps - t_start, t_dur)
                    _cut.append((_f / fps, (_f + 1) / fps) + _tail8 + (_sx, _sy))
                if _pb > _hi + 1e-6:
                    _cut.append((_hi, _pb) + _tail8 + (None, None))
            pieces = _cut
            report.setdefault("notes", []).append(
                f"主轨 {k}: 段级模糊动画逐帧烘（{len(_cut)} 片）")
            if depth == 0:
                report["main_anim_blur_pieces"] = report.get("main_anim_blur_pieces", 0) + len(_cut)
        for _pi, (_ta, _tb, _sc_p, _ptx, _pty, _mcfg, _bsx, _bsy) in enumerate(
                [tuple(_p) if len(_p) == 8 else tuple(_p) + (None, None) for _p in pieces]):
            if _tb - _ta <= 1e-4:
                continue
            _f_in = int(round(_ta * fps))
            _f_out = int(round(_tb * fps))
            if _pi == 0:
                _f_in = int(round(t_start * fps))
            if _pi == len(pieces) - 1:
                _f_out = int(round(_t_end * fps))
            if _f_out <= _f_in:
                _f_out = _f_in + 1
            _s_in_p = s_in + (_f_in / fps - t_start) * speed
            if _anim_media is not None:
                # 烘焙底片的时间轴已归零（第 0 帧 = 段首）
                _s_in_p = max(0.0, (_f_in / fps - t_start) * speed)
            _s_out_p = _s_in_p + ((_f_out - _f_in) / fps) * speed
            _piece_src = src
            if _mcfg is not None:
                # 遮罩在图层空间：先烘成「画面×alpha 叠黑底」的不透明整帧，再走 scale/缩窗几何
                _piece_src = str(masked_media(src, _s_in_p, _s_out_p - _s_in_p, _mcfg))
                _s_in_p, _s_out_p = 0.0, _s_out_p - _s_in_p
                if depth == 0:
                    report["masked_main_segments"] = report.get("masked_main_segments", 0) + 1
            cid = f"m{k:03d}" if len(pieces) == 1 else f"m{k:03d}p{_pi}"
            _clip = {
                "clip_id": cid,
                "track_role": "a_roll",
                "media_path": _piece_src,
                "source_in_sec": round(_s_in_p, 4),
                "source_out_sec": round(_s_out_p, 4),
                "duration_sec": round((_f_out - _f_in) / fps, 4),
                "timeline_in_frame": _f_in,
                "timeline_out_frame": _f_out,
            }
            _vf_extra = []
            _seg_blur = _seg_blur_sigma(s)
            if _seg_blur:
                # 段级特效（模糊）要在缩放/缩窗前作用于源画面
                _vf_extra.append(f"gblur=sigma={_seg_blur:.2f}")
            if _bsx and _bsx > 0.2:
                # 段级「横向模糊」入场：椭圆高斯（x 重 y 轻），逐帧 sigma 由 animations.blur_sigmas 反解
                _vf_extra.append(f"gblur=sigma={_bsx:.2f}:sigmaV={_bsy:.2f}")
            if _sc_p < 0.999:
                # 缩窗：草稿真值是黑底居中缩窗（原版四角纯黑），不是渲染器默认的自身模糊铺底
                _clip["pip_layout"] = None
                w2 = int(round(W * _sc_p / 2)) * 2
                h2 = int(round(H * _sc_p / 2)) * 2
                px = max(0, min(W - w2, int(round((W - w2) / 2 + _ptx * W / 2))))
                py = max(0, min(H - h2, int(round((H - h2) / 2 - _pty * H / 2))))
                _vf_extra.append(f"scale={w2}:{h2}:flags=bicubic,pad={W}:{H}:{px}:{py}:black")
            elif _sc_p > 1.001 or abs(_ptx) > 1e-4 or abs(_pty) > 1e-4:
                # face_anchor 显式置 None（顶层）：复刻用草稿的居中缩放，禁止人脸锚定推近
                _clip["pip_layout"] = {"mode": "geometry_pip", "face_anchor": None,
                                       "main": {"scale": _sc_p, "x": _ptx, "y": -_pty}}
            else:
                _clip["pip_layout"] = None
            if abs(alpha - 1.0) > 1e-3:
                # 主轨半透明＝与黑底混合：把 RGB 乘 alpha 烘进段里
                _a = max(0.0, min(1.0, alpha))
                _vf_extra.append(f"lutrgb=r='val*{_a:.4f}':g='val*{_a:.4f}':b='val*{_a:.4f}'")
            if _vf_extra:
                _clip["vf_extra"] = ",".join(_vf_extra)
            if mat.get("crop"):
                # 主轨素材裁切真值（crop_ratio 预裁）尚未接入主轨链路，计数据实上报
                report["unrealized"]["material_crop"] = report["unrealized"].get("material_crop", 0) + 1
            clips.append(_clip)
    if depth == 0:
        report["main_segments"] = len(clips)

    edl = {
        "style_id": style_id,
        "fps": fps,
        "clips": clips,
        "duration_sec": round(total_sec, 3),
        "replica": True,
        "replica_note": "EDL 直接来自解密草稿主轨，不经 run_step4 重排",
    }
    write_json(pdir / "04_edl.json", edl)
    write_json(pdir / "01_manifest.json", {"items": []})
    write_json(pdir / "style_spectrum_applied.json", {"style_id": style_id})

    # ---------- 3. 音频母带（普通音频段 + 复合片段的声音） ----------
    master = _build_master(cls, compound_media, compound_audio, "08_mixed_master.wav")
    if master is None:
        # 有画面无音频的时间线：生成等长静音，不阻塞复刻
        run_ffmpeg(["ffmpeg", "-y", "-f", "lavfi", "-i",
                    f"anullsrc=channel_layout=stereo:sample_rate=48000:d={total_sec:.4f}",
                    "-c:a", "pcm_s16le", str(pdir / "08_mixed_master.wav")], timeout=120)
        master = pdir / "08_mixed_master.wav"
        report["unrealized"]["timeline_no_audio"] = report["unrealized"].get("timeline_no_audio", 0) + 1
    if depth == 0:
        drift = abs(get_wav_duration_sec(master) - total_sec)
        if drift >= 0.5:
            raise RuntimeError(f"音频母带时长与草稿不符（差 {drift:.2f}s）")

    # ---------- 4. 调色 plan（草稿真值优先） ----------
    # 草稿没有 filter 素材 / filter 轨 / clip.adjust 时，原版就是原素材本色，
    # 禁止把 step7 推断的风格调色强套进复刻（style_21 实测被压暗约 40 级）。
    _has_draft_grade = (
        bool((draft.get("materials") or {}).get("filters"))
        or any(t.get("type") == "filter" for t in (draft.get("tracks") or []))
        or any((s.get("clip") or {}).get("adjust")
               for t in (draft.get("tracks") or []) for s in (t.get("segments") or []))
    )
    if _has_draft_grade:
        run_step7(pdir, style_id=style_id)
        color_plan = read_json(pdir / "07_color_plan.json")
    else:
        report["notes"].append("draft_grade_truth=none -> skip step7 grade")
        color_plan = {"clip_plans": [], "lut_path": ""}

    # ---------- 5. 主轨渲染 ----------
    if clips:
        base_mp4 = R.build_base_video_track(pdir, edl, color_plan, master, manifest={"items": []}, preview=preview)
    elif alpha_out:
        # 复合片段：底是透明的，不是黑场（否则全幅盖掉主轨）
        base_mp4 = pdir / "_render_final" / "base_alpha.mov"
        base_mp4.parent.mkdir(parents=True, exist_ok=True)
        if not base_mp4.is_file():
            run_ffmpeg(["ffmpeg", "-y", "-f", "lavfi", "-i",
                        f"color=c=black@0.0:s={W}x{H}:r={fps}:d={max(0.1, total_sec):.4f},format=rgba",
                        "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le", str(base_mp4)], timeout=300)
    else:
        # 主轨 0 段（纯叠加/文本时间线，如照片翻页复合片段）：黑场底座，内容全在叠加层
        base_mp4 = pdir / "_render_final" / "base_video.mp4"
        base_mp4.parent.mkdir(parents=True, exist_ok=True)
        if not base_mp4.is_file():
            run_ffmpeg(["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=black:s={W}x{H}:r={fps}:d={max(0.1, total_sec):.4f}",
                        "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p", str(base_mp4)], timeout=300)
        report.setdefault("notes", []).append("主轨 0 段：黑场底座，画面全在叠加/文本层")

    # ---------- 6. 合成单元 ----------
    units = []

    for t_idx, segs in cls["overlays"]:
        for o, s in enumerate(segs):
            mat = vid_by_id.get(s.get("material_id")) or {}
            tgt = s.get("target_timerange") or {}
            srcr = s.get("source_timerange") or {}
            t_start = float(tgt.get("start") or 0) / US
            t_dur = float(tgt.get("duration") or 0) / US
            speed = float(s.get("speed") or 1.0)
            s_in = float(srcr.get("start") or 0) / US   # 容器 start_time 偏移在下面 src 定下来后补
            s_dur = t_dur * speed
            ckey = None
            if not (mat.get("path") or ""):
                for ref in s.get("extra_material_refs") or []:
                    if ref in compound_media:
                        ckey = ref
                        break
            if ckey and not _compound_is_visual(drafts_entries[ckey]["draft"]):
                continue  # 纯音效复合片段：画面透明，声音已进母带
            if ckey:
                src = compound_media[ckey]
            else:
                src = _subst(mat.get("path") or "", base_dir)
            if not src or not Path(src).is_file():
                report["unrealized"]["overlay_missing_source"] = report["unrealized"].get("overlay_missing_source", 0) + 1
                continue
            s_in = src_seek_time(s_in, src)
            cl = s.get("clip") or {}
            sc = float((cl.get("scale") or {}).get("x") or 1.0)
            tx = float((cl.get("transform") or {}).get("x") or 0.0)
            ty = float((cl.get("transform") or {}).get("y") or 0.0)
            alpha = float(cl.get("alpha") if cl.get("alpha") is not None else 1.0)
            flip = cl.get("flip") or {}
            if sc < 0.05 and s.get("common_keyframes"):
                # 打字机/缩放入场动画的起始态；终态按 1.0 铺开，动画本身计数不假装
                sc = 1.0
                report["unrealized"]["overlay_keyframe_animation"] = report["unrealized"].get("overlay_keyframe_animation", 0) + 1
            # keyframe 透明度/位置：两点线性直接表达（fade / overlay 表达式），不静默消失
            _kfa = _kf_rel(s, "KFTypeAlpha", s_in)
            _kfx = _kf_rel(s, "KFTypePositionX", s_in)
            _kfy = _kf_rel(s, "KFTypePositionY", s_in)
            fade = None
            if _kfa:
                if len(_kfa) == 2 and {_kfa[0][1], _kfa[1][1]} == {0.0, 1.0}:
                    fade = ("in" if _kfa[0][1] < _kfa[1][1] else "out", max(0.05, _kfa[1][0] - _kfa[0][0]), _kfa[0][0])
                    alpha = 1.0
                else:
                    alpha = (_kfa[0][1] + _kfa[-1][1]) / 2.0
                    report["unrealized"]["overlay_keyframe_alpha_approx"] = report["unrealized"].get("overlay_keyframe_alpha_approx", 0) + 1
                report["unrealized"]["overlay_keyframe_animations"] = report["unrealized"].get("overlay_keyframe_animations", 0) + 1
            elif alpha < 0.01 and s.get("common_keyframes"):
                alpha = 1.0
                report["unrealized"]["overlay_keyframe_animations"] = report["unrealized"].get("overlay_keyframe_animations", 0) + 1
            pos_kf = None
            # 叠加层缩放 keyframe：**逐帧**渲染（旧实现取「保持最久」的稳态值一处画完）。
            #
            # 旧行为 = 「取稳态常数」：style_21 人像叠层 KFTypeScaleX 1.5→1.2（段内 0→0.1667s），
            # 两点各保持 0s / 0.983s → 选终值 1.2 铺满全段。原版这 0.167s 内是连续缩小的，
            # 于是 1.683s 那人像整层小 22%（整帧差 14.77，同段稳定帧 6–8）。
            #
            # 时间口径：keyframe 的 time_offset 与 source_timerange.start 同为**容器 PTS**口径
            # （= 段源入点 + 素材首帧 PTS），所以基要加 media_start_time；用 ffmpeg 的 -ss 口径
            # 会整体偏一帧（主轨缩窗那条已验证）。
            _kf_base = s_in + media_start_time(src)
            _kfsx = _kf_rel(s, "KFTypeScaleX", _kf_base)
            _kfsy = _kf_rel(s, "KFTypeScaleY", _kf_base)
            if len(_kfsy) != len(_kfsx):
                _kfsy = []          # Y 轴点数对不上就当等比（宁可等比也不乱堆一条错曲线）
            _sc_series = _kfsx or _kfsy
            _sc_anim = None
            if len(_sc_series) >= 2:
                _vmin = min(v for _t, v in _sc_series)
                _vmax = max(v for _t, v in _sc_series)
                # 跨度太小 = 同一值写了两点（不是动画）；≤0.05 是「打字机/缩放入场起手态」，
                # 那种按下面的稳态分支处理（铺开画），不能拿它逐帧当 0 面积渲染。
                if abs(_vmax - _vmin) > 1e-3 and _vmin > 0.05:
                    _sc_anim = kf_scale_schedule(_kfsx or _kfsy, _kfsy, t_start, t_dur, float(fps))
            _sc_steady = _kf_steady(_sc_series, t_dur)
            if _sc_steady is not None and abs(_sc_steady - sc) > 1e-3 and not (sc < 0.05):
                # 抽段/抠像仍按稳态尺寸做（避免为了一段动画把整段重新抠像），
                # 逐帧几何在合成侧按比例缩放（见 composite 的 scale_kf 分支）。
                sc = _sc_steady
                if _sc_anim is None:
                    report["unrealized"]["overlay_keyframe_scale_end"] = report["unrealized"].get("overlay_keyframe_scale_end", 0) + 1
            if len(_kfx) == 2 and len(_kfy) == 2 and _kfx[1][0] > _kfx[0][0] and _kfy[1][0] > _kfy[0][0]:
                pos_kf = (_kfx, _kfy)
                report["unrealized"]["overlay_keyframe_position"] = report["unrealized"].get("overlay_keyframe_position", 0) + 1
            elif _kfx or _kfy:
                report["unrealized"]["overlay_keyframe_position_approx"] = report["unrealized"].get("overlay_keyframe_position_approx", 0) + 1
            # 注：不做「与主轨同素材同几何就跳过」——那是 z 序三明治手法（主轨上面盖卡片、
            # 再叠同素材抠像人物），删了会把 8s 这类层序弄丢；抠像错位问题已由 fps 对齐根治。
            alpha_src = bool(ckey and _compound_is_visual(drafts_entries[ckey]["draft"]))
            # 叠加轨遮罩：遮罩区外必须**透明**（透出下层），所以走 alpha 路径（ProRes4444 + alphamerge）
            ov_mask = segment_mask_config(s, mask_by_id, report)
            # 图层几何：material.crop → contain(画布) → ×clip.scale（非 canvas×scale，见 layer_geometry）
            geom = layer_geometry(mat, src, W, H, sc)
            lw, lh = geom["lw"], geom["lh"]
            # 入场/出场动画（material_animations）：纯透明度交给 ffmpeg 的 fade 链，
            # 带位移/缩放/模糊的必须 PIL 逐帧（scale 滤镜的输出尺寸不能逐帧变）。
            _specs = parse_animations(s, anim_by_id)
            # 宿主：照片图层单开一档（animations.CTX_CALIBRATION["layer_photo"]），
            # 同名动画在照片上是「黑场淡入」、在视频层上不是，不能共用一个幅度表。
            _anim_ctx = "layer_photo" if (mat.get("type") or "") == "photo" else "layer"
            _ramp = alpha_ramp(_specs, t_dur, ctx=_anim_ctx) if _specs else None
            if _ramp:
                fade = (_ramp[0], max(0.05, _ramp[2]), _ramp[1])
                if depth == 0:
                    report["overlay_anims"] = report.get("overlay_anims", 0) + 1
                    report.setdefault("overlay_anim_list", []).extend(summarize(_specs))
            _animated = bool(_specs) and not _ramp
            if _animated:
                # 整层按动画逐帧落图：几何/位移/透明度全部烘进画布尺寸的图层，
                # 叠加时只做 setpts 平移（坐标/缩放已在图层内部完成）。
                _cx = W / 2.0 + tx * W / 2.0
                _cy = H / 2.0 - ty * H / 2.0
                _atag = _cache_tag("an", s_in, s_dur, sc, tx, ty, alpha, fps, flip, json.dumps(geom, sort_keys=True),
                                   json.dumps([(x.kind, x.name, x.duration, x.start) for x in _specs]))
                _amov = media_dir / f"anim_{t_idx:02d}_{o:02d}_{_atag}.mov"
                if not _amov.is_file():
                    if (mat.get("type") or "") == "photo" or Path(src).suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
                        _frames = [_anim_base_png(src, mat, geom, media_dir / f"anim_{t_idx:02d}_{o:02d}_{_atag}.png", flip)]
                        _srcfps = None
                    else:
                        _frames = _anim_video_frames(src, geom, s_in, s_dur, fps,
                                                     media_dir / f"anim_{t_idx:02d}_{o:02d}_{_atag}_src", flip)
                        _srcfps = float(fps)
                    render_animated_layer(_frames, (W, H), (lw, lh), (_cx, _cy),
                                          t_start, t_dur, fps, _specs, _amov,
                                          src_fps=_srcfps, ctx=_anim_ctx)
                units.append((t_idx, o, {
                    "kind": "video", "seg": _amov, "matte": None, "t_start": t_start, "t_dur": t_dur,
                    "scale": 1.0, "lw": W, "lh": H, "tx": 0.0, "ty": 0.0, "alpha": alpha,
                    "fade": None, "pos_kf": None, "anim": True,
                }))
                if depth == 0:
                    report["overlay_segments"] += 1
                    report["animated_overlays"] = report.get("animated_overlays", 0) + 1
                    report.setdefault("overlay_anim_list", []).extend(summarize(_specs))
                    for _nm in fallback_names(_specs):
                        report["unrealized"][_nm] = report["unrealized"].get(_nm, 0) + 1
                continue
            # 缩放关键帧的逐帧几何表：合成时一路输入切 K 段、每段各自 scale。
            # 遮罩层 / 已走 PIL 逐帧动画的层不在这里切（两者的几何通路会打架），如实计数。
            scale_kf = None
            if _sc_anim:
                if ov_mask is None:
                    scale_kf = []
                    for (_ta, _tb, _sxa, _sya) in _sc_anim:
                        if abs(_sxa - sc) < 1e-9 and abs(_sya - sc) < 1e-9:
                            _g = geom          # 稳态帧：就是抽段本身的尺寸，不白多一次重采样
                        else:
                            _g = layer_geometry_xy(mat, src, W, H, _sxa, _sya)
                        scale_kf.append((t_start + _ta, t_start + _tb, _g["lw"], _g["lh"]))
                    if depth == 0:
                        report["unrealized"]["overlay_kf_scale_sliced"] = \
                            report["unrealized"].get("overlay_kf_scale_sliced", 0) + len(scale_kf)
                else:
                    report["unrealized"]["overlay_kf_scale_masked"] = \
                        report["unrealized"].get("overlay_kf_scale_masked", 0) + 1
            seg_ext = ".mov" if (alpha_src or ov_mask) else ".mp4"
            # 缓存指纹必须含模糊 sigma：否则改了标定系数旧段会被永久复用（“改了不生效”陷阱）
            seg_p = media_dir / (f"ov_{t_idx:02d}_{o:02d}_{_cache_tag('ov', s_in, s_dur, sc, tx, ty, alpha, fps, flip, json.dumps(geom, sort_keys=True), json.dumps(ov_mask, sort_keys=True) if ov_mask else '', _seg_blur_sigma(s))}" + seg_ext)
            if not seg_p.is_file():
                fl = "hflip," if flip.get("h") else ("vflip," if flip.get("v") else "")
                _ov_blur = _seg_blur_sigma(s)
                fl = (f"gblur=sigma={_ov_blur:.2f}," if _ov_blur else "") + fl
                _geom_vf = layer_vf(geom, W, H)
                # 拼接滤镜链：空段不能留逗号（`fps=30,` 这种尾部空滤镜 ffmpeg 会报 "No such filter: ''"）
                _pre = ",".join(x for x in (fl.rstrip(","), _geom_vf) if x)
                _pre = ("," + _pre) if _pre else ""
                if ov_mask:
                    # 遮罩作用于图层（裁切+缩放后）自身的 uv 空间：先裁/缩到图层尺寸，再按图层宽高比 alphamerge
                    _png = mask_alpha_png(ov_mask, lw, lh)
                    _nfr = max(1, int(round(max(0.05, s_dur) * fps)))
                    _in0 = (["-loop", "1", "-i", src] if (mat.get("type") or "") == "photo"
                            else ["-ss", f"{s_in:.4f}", "-t", f"{max(0.05, s_dur):.4f}", "-i", src])
                    run_ffmpeg([ffmpeg_bin, "-y", *_in0, "-loop", "1", "-i", str(_png),
                                "-filter_complex",
                                f"[0:v]fps={fps},setpts=PTS-STARTPTS,format=rgba{_pre}[fg];"
                                f"[1:v]fps={fps},setpts=PTS-STARTPTS,format=gray[mask];[fg][mask]alphamerge[v]",
                                "-map", "[v]", "-frames:v", str(_nfr), "-an",
                                "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le",
                                str(seg_p)], timeout=600)
                    if depth == 0:
                        report["masked_overlay_segments"] = report.get("masked_overlay_segments", 0) + 1
                elif alpha_src:
                    # 复合片段输出是 ProRes4444 带 alpha：抽段必须保 alpha
                    run_ffmpeg([ffmpeg_bin, "-y", "-ss", f"{s_in:.4f}", "-t", f"{max(0.05, s_dur):.4f}", "-i", src,
                                "-vf", f"fps={fps},setpts=PTS-STARTPTS,format=rgba{_pre}",
                                "-an", "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le", str(seg_p)], timeout=600)
                else:
                    if (mat.get("type") or "") == "photo":
                        # 静图：-t 放在 -i 前会限制输入读取（单帧图只能得 ~2 帧），必须 -loop 1 + 输出侧 -t
                        run_ffmpeg([ffmpeg_bin, "-y", "-loop", "1", "-i", src,
                                    "-vf", f"fps={fps}{_pre}",
                                    "-t", f"{max(0.05, s_dur):.4f}",
                                    "-an", "-c:v", "libx264", "-preset", "fast", "-crf", "14",
                                    "-pix_fmt", "yuv420p", str(seg_p)])
                    else:
                        # `setpts=PTS-STARTPTS` 不能省。真正机理是 mp4 的**空 edit（elst）**：
                        # 抽段首帧 PTS 不为 0（源视频流 start_time=0.033 带下来）时，muxer 会写
                        # 「empty edit [0,0.033) + real edit [0.033,…)」。ffmpeg 自己解码看不出异常，
                        # 但 AVFoundation 会把空 edit 补成一帧**全黑样本**（PTS=0、BGRA 均值 0.0/max=2，
                        # 下一帧才是真画面）；Vision 对纯黑帧返回空 mask → matte 前 1–2 帧全 0，
                        # 撕纸人像层前 2 帧整层缺失（成片 0.0s 的差就是它）。
                        # 实测：旧产物 stream start_time=0.033008、nb_frames=102、
                        # track.segments=[empty(0.033), real(1.7)]；加 setpts 后 start_time=0.000000、
                        # segments=[real(1.7)]、AVAssetReader 首帧均值 160（>=100），matte 首两帧
                        # 覆盖率正常（84.7/84.8，之前是 0.0/84.7 且多一帧）。
                        # 注意：改完后必须让 _cache_tag 失效重烘——上一轮就是复用了修复前的
                        # 旧抽段（hash 未含本脚本源码前生成），才看到「setpts 加了 start_time 没变」。
                        run_ffmpeg([ffmpeg_bin, "-y", "-ss", f"{s_in:.4f}", "-t", f"{max(0.05, s_dur):.4f}", "-i", src,
                                    "-vf", f"fps={fps},setpts=PTS-STARTPTS{_pre}",
                                    "-an", "-c:v", "libx264", "-preset", "fast", "-crf", "14",
                                    "-pix_fmt", "yuv420p", str(seg_p)])
            matte_p = None
            # matting.path 为空是草稿占位标记，原版并未抠像；只有非空才走人像抠像
            want_matte = bool((mat.get("matting") or {}).get("path"))
            if ckey is not None:
                # 照片/文本拼的复合片段没有可抠的真人，抠了反而丢图层
                sub_ref = drafts_entries.get(ckey, {}).get("draft") or {}
                if not _compound_has_personable_video(sub_ref):
                    want_matte = False
            if want_matte:
                matte_p = matte_dir / f"ov_{t_idx:02d}_{o:02d}_{_cache_tag('matte', s_in, s_dur, fps, sc)}_matte.mp4"
                if not matte_p.is_file():
                    meta = generate_person_matte_video(seg_p, matte_p, quality="balanced", fps=fps)
                    if not meta or not meta.get("has_person") or not matte_p.is_file():
                        matte_p = None
            if want_matte and matte_p is None:
                report["matte_failed"] += 1
                report["unrealized"]["overlay_matte_failed"] = report["unrealized"].get("overlay_matte_failed", 0) + 1
                continue  # 抠像失败不下假前景
            # 抠像描边（撕纸描边）：从同一份 matte 派生白色纸边，烘成 RGBA 图层压在人物下方。
            # 必须在 matte 成功之后做——描边跟着 alpha 走，抠像失败就没有描边，也绝不含糊。
            _stroke = matting_stroke_spec(mat) if matte_p else None
            _had_matte = matte_p is not None
            if _stroke:
                # 缓存指纹必须含像素换算常数（含环境变量覆盖）：否则改了 GAIN 旧烘片会被永久
                # 复用，看上去就是「改了不生效」——这个坑 hash 里已经吃过一次。
                _stroke_p = matte_dir / (
                    f"ov_{t_idx:02d}_{o:02d}_"
                    f"{_cache_tag('stroke', s_in, s_dur, fps, lw, lh, json.dumps(_stroke, sort_keys=True),
                                  STROKE_SIZE_GAIN, STROKE_FEATHER_SRC,
                                  STROKE_TEX_JITTER, STROKE_TEX_GRAIN, STROKE_MATTE_DILATE)}"
                    "_stroke.mov")
                if not _stroke_p.is_file():
                    bake_stroke_layer(seg_p, matte_p, _stroke_p, _stroke, (lw, lh), fps, ffmpeg_bin)
                seg_p, matte_p = _stroke_p, None   # 描边已烘进 RGBA 层，alpha 直接走 overlay
                if depth == 0:
                    report["matte_stroke_segments"] = report.get("matte_stroke_segments", 0) + 1
            units.append((t_idx, o, {
                "kind": "video", "seg": seg_p, "matte": matte_p, "t_start": t_start, "t_dur": t_dur,
                "scale": sc, "lw": lw, "lh": lh, "tx": tx, "ty": ty, "alpha": alpha,
                "fade": fade, "pos_kf": pos_kf, "scale_kf": scale_kf,
            }))
            if depth == 0:
                report["overlay_segments"] += 1
                if _had_matte:
                    report["matte_done"] += 1

    for t_idx, segs in cls["texts"]:
        for o, s in enumerate(segs):
            mat = txt_by_id.get(s.get("material_id")) or {}
            if mask_by_id and any(r in mask_by_id for r in (s.get("extra_material_refs") or [])):
                # 文本层遮罩没接（目前只做了视频轨）：计数不静默跳过
                report["unrealized"]["mask_on_text_or_sticker"] = report["unrealized"].get("mask_on_text_or_sticker", 0) + 1
            if not mat:
                report["unrealized"]["text_missing_material"] = report["unrealized"].get("text_missing_material", 0) + 1
                continue
            try:
                spec = parse_text_material(mat, canvas_w=canvas_w)
            except Exception:
                report["unrealized"]["text_parse_failed"] = report["unrealized"].get("text_parse_failed", 0) + 1
                continue
            if not spec["text"].strip():
                continue
            if float(spec.get("alpha", 1.0)) <= 0.005:
                # 素材级 global_alpha=0 有两层含义，必须靠段上的关键帧区分：
                #  · 没有 KFTypeGlobalAlpha → 剪映里这行字确实不显示，不偷画；
                #  · 有 KFTypeGlobalAlpha → material 里的 0 是「静态终态」，真值是曲线
                #    （style_21 的「创」「原」水印字：0.703 → 0，原版 6.8–8.2s 明明白白可见）。
                if not _kf_rel(s, "KFTypeGlobalAlpha", 0.0):
                    report.setdefault("notes", []).append(
                        f"文本 {spec['text'][:6]!r}: global_alpha=0，按原版不显示")
                    continue
                spec["alpha"] = 1.0
            tgt = s.get("target_timerange") or {}
            t_start = float(tgt.get("start") or 0) / US
            t_dur = float(tgt.get("duration") or 0) / US
            cl = s.get("clip") or {}
            sc = float((cl.get("scale") or {}).get("x") or 1.0)
            tx = float((cl.get("transform") or {}).get("x") or 0.0)
            ty = float((cl.get("transform") or {}).get("y") or 0.0)
            # 入场/出场动画（material_animations）与关键帧驱动的缩放/透明度/位置。
            # 草稿常把静态 clip.scale 写成动画终态（原创大字是 0.01），真尺寸在 KFTypeScaleX 里。
            _specs = parse_animations(s, anim_by_id)
            _ksx = _kf_rel(s, "KFTypeScaleX", 0.0)
            _ka = _kf_rel(s, "KFTypeAlpha", 0.0) + _kf_rel(s, "KFTypeGlobalAlpha", 0.0)
            _kpx = _kf_rel(s, "KFTypePositionX", 0.0)
            _kpy = _kf_rel(s, "KFTypePositionY", 0.0)
            _kf_driven = bool(_ksx or _ka or _kpx or _kpy)
            _sc_base = sc
            if _ksx:
                _sc_max = max(v for _t, v in _ksx)
                if _sc_max > 1e-3:
                    _sc_base = _sc_max
            elif sc < 0.05:
                _sc_base = 1.0
            # 缩放/透明度/位置 keyframe 现在真的按曲线渲染了（见 _tstate），不再计 unrealized
            if _specs or _kf_driven:
                # 逐帧图层：文字 PNG 作为模板，缩放/位移/透明度全部按动画曲线施加
                _png, _pw, _ph = render_text_png(spec, _sc_base,
                                                 text_dir / f"tx_{t_idx:02d}_{o:02d}_{_sc_base:.3f}_a{spec.get('alpha', 1.0):.2f}.png",
                                                 fail_log=report.setdefault("font_failures", []), pxu=pxu)
                _bcx = W / 2.0 + tx * W / 2.0
                _bcy = H / 2.0 - ty * H / 2.0

                def _tstate(t_local, _specs=_specs, _ksx=_ksx, _ka=_ka, _kpx=_kpx, _kpy=_kpy,
                            _sc=_sc_base, _tx=tx, _ty=ty, _dur=t_dur, _pw=_pw, _ph=_ph):
                    # 文字动画的位移量按「文本包围盒」归一（剪映文字入场是相对文本框的），
                    # 不是画布——拿画布宽算会把一行字直接甩出屏幕。
                    st = anim_state(_specs, t_local, _dur, _pw, _ph, ctx="layer") if _specs else AnimState()
                    if _ksx:
                        _v = _kv(_ksx, t_local)
                        if _v > 1e-4:
                            st.scale *= _v / _sc
                        else:
                            st.alpha = 0.0
                    if _ka:
                        st.alpha *= max(0.0, _kv(_ka, t_local))
                    if _kpx:
                        st.dx += (_kv(_kpx, t_local) - _tx) * W / 2.0
                    if _kpy:
                        st.dy -= (_kv(_kpy, t_local) - _ty) * H / 2.0
                    st.alpha = max(0.0, min(1.0, st.alpha))
                    return st

                _ttag = _cache_tag("tan", t_start, t_dur, _sc_base, tx, ty, fps, _pw, _ph,
                                   json.dumps([(x.kind, x.name, x.duration, x.start) for x in _specs]),
                                   json.dumps([_ksx, _ka, _kpx, _kpy], sort_keys=True))
                _tmov = media_dir / f"tanim_{t_idx:02d}_{o:02d}_{_ttag}.mov"
                if not _tmov.is_file():
                    render_animated_layer([_png], (W, H), (_pw, _ph), (_bcx, _bcy),
                                          t_start, t_dur, fps, _specs, _tmov,
                                          state_fn=_tstate)
                units.append((t_idx, o, {
                    "kind": "video", "seg": _tmov, "matte": None, "t_start": t_start, "t_dur": t_dur,
                    "scale": 1.0, "lw": W, "lh": H, "tx": 0.0, "ty": 0.0, "alpha": 1.0,
                    "fade": None, "pos_kf": None, "anim": True, "text_layer": True,
                }))
                if depth == 0:
                    report["text_segments"] += 1
                    report["animated_texts"] = report.get("animated_texts", 0) + 1
                    report.setdefault("text_anim_list", []).extend(summarize(_specs))
                    for _nm in fallback_names(_specs):
                        report["unrealized"][_nm] = report["unrealized"].get(_nm, 0) + 1
                continue
            png, pw, ph = render_text_png(spec, sc, text_dir / f"tx_{t_idx:02d}_{o:02d}_a{spec.get('alpha', 1.0):.2f}.png",
                                           fail_log=report.setdefault("font_failures", []), pxu=pxu)
            units.append((t_idx, o, {
                "kind": "text", "png": png, "pw": pw, "ph": ph, "t_start": t_start, "t_dur": t_dur,
                "tx": tx, "ty": ty, "alpha": 1.0,
            }))
            if depth == 0:
                report["text_segments"] += 1

    # ---------- 6.4 贴纸轨：走 sticker_render（真素材 + 真几何 + 出入场动画） ----------
    # 原来这里直接拿 materials.stickers[*].path 当图片叠。草稿是 Windows 机器的路径
    # （Windows 草稿里的 artistEffect/<id>/<hash> 绝对路径），本机根本不存在 → 每一段都落进
    # unrealized["stickers"]，等于**整条贴纸轨从来没画过**。style_21 的「手机屏幕边框」
    # 10.43–12.90s 因此全缺：12.0s 那一帧主轨/文字都对，画面却差 42 级（占全片最差帧）。
    # 现在改走短影工坊里已反解好的贴纸模块，三件事都按真值来：
    #   · 素材：按 resource_id 反查本机 Cache/artistEffect/<id>/（sticker_assets.resolve_sticker_dir）；
    #   · 几何：显示宽 = 原生宽 × clip.scale × 画布宽/720（720 是剪映贴纸的缩放参考宽）；
    #   · 出入场：animations 标定 + sticker_render.STICKER_OUT_SLIDE 的「向下滑动」真值窗口
    #     （start 缺省 = 段尾倒数 duration，缓动 p²、GAIN 1.229）。
    # 产出是**画布大小的 RGBA mov**（内容已摆好），所以当 scale=1 的视频单元叠即可；
    # z 序照旧由轨序决定（贴纸轨 11 > 文字轨 9/10，与草稿 render_index 14008/14009 > 14000~14007 一致）。
    if cls["stickers"] and not audio_only:
        from sticker_render import plan_sticker_layers  # noqa: E402
        _stk_tidx: dict = {}
        for _t_idx, _segs in cls["stickers"]:
            for _s in _segs:
                _tg = _s.get("target_timerange") or {}
                _stk_tidx[round(float(_tg.get("start") or 0) / US, 3)] = _t_idx
                if any(r in mask_by_id for r in (_s.get("extra_material_refs") or [])):
                    report["unrealized"]["mask_on_text_or_sticker"] = \
                        report["unrealized"].get("mask_on_text_or_sticker", 0) + 1
        _stk_layers = plan_sticker_layers(draft, base_dir, W, H, float(fps), total_sec,
                                         media_dir, report, depth=depth,
                                         animations_by_id=anim_by_id)
        for _k, _lay in enumerate(_stk_layers):
            _ti = _stk_tidx.get(round(_lay.t_start, 3), cls["stickers"][0][0])
            units.append((_ti, _k, {
                "kind": "video", "seg": _lay.path, "matte": None,
                "t_start": _lay.t_start, "t_dur": _lay.dur,
                "scale": 1.0, "lw": W, "lh": H, "tx": 0.0, "ty": 0.0,
                "alpha": 1.0, "fade": None, "pos_kf": None, "anim": True,
                # pts_lag_frames：见 PTS_LAG_FRAMES 注释（overlay 的 framesync 向后取整）。
                "pts_lag": PTS_LAG_FRAMES,
            }))
            if depth == 0:
                report["sticker_segments"] += 1
                report.setdefault("notes", []).append(
                    f"贴纸「{_lay.name}」{_lay.t_start:.2f}s+{_lay.dur:.2f}s（真素材/720 几何/出入场真值）")

    # ---------- 7. 一次合成 pass：z 序 = 轨序 ----------
    units.sort(key=lambda u: (u[0], u[1]))
    final_out = (work / "compound_alpha.mov") if alpha_out else (work / ("final.mp4" if depth == 0 else "compound.mp4"))
    if preview and depth == 0:
        run_ffmpeg([ffmpeg_bin, "-y", "-i", str(base_mp4), "-i", str(master),
                    "-map", "0:v", "-map", "1:a", "-shortest",
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", str(final_out)])
        report["output"] = str(final_out)
        report["notes"].append("preview：只主轨+音频，未合成叠加/文本")
        return final_out

    from PIL import Image as PILImage  # noqa: E402

    inputs = ["-i", str(base_mp4)]
    n_in = 1
    fc_parts = []
    # 主轨拼接后 PTS 网格会轻微漂（实测 41 段拼完 15.999s → 15.933s），不拉成 CFR 的话
    # 下游所有 enable(t,…) 都会早/晚一帧。强制重采样到目标帧率再叠。
    fc_parts.append(f"[0:v]fps={fps},setpts=PTS-STARTPTS[basecfr]")
    cur = "[basecfr]"
    # 草稿 effect 轨真值：整画面模糊只作用于**视频合成层**（主轨 + 叠加视频），不糊字幕/贴纸。
    # 剪映把画面特效挂在自己的 effect 轨（这里 render_index=7），字幕轨在它上面；实测原版
    # 1.683s 背景已重糊、字幕「粉丝认可度」依然清晰，而我们旧做法把 gblur 放在合成链最末端，
    # 等于连字幕一起糊——σ 从 5 放大到 14.4 后，这一帧的字幕直接不可读（0.0s 之外唯一变差的一帧）。
    # 所以在「视频单元结束、第一个文字/贴纸单元之前」插入模糊链；σ 按 effects_adjust_blur
    # 关键帧逐帧衰减（见 _effect_blur_steps）。
    _effect_blur = []
    for _ti, _esegs in cls.get("effects") or []:
        for _s in _esegs:
            _e = vfx_by_id.get(_s.get("material_id")) or {}
            if "模糊" not in str(_e.get("name") or ""):
                continue
            _trr = _s.get("target_timerange") or {}
            _st = float(_trr.get("start") or 0) / US
            _dur = float(_trr.get("duration") or 0) / US
            if _dur <= 0:
                continue
            for _a, _b, _sg in _effect_blur_steps(_s, _e, _st, _dur, fps, W, H):
                if _sg > 0.35:
                    _effect_blur.append(
                        f"gblur=sigma={_sg:.2f}:enable='between(t,{_a:.4f},{_b:.4f})'")
    _video_done = False
    for n, (_t, _o, u) in enumerate(units):
        # 动画文字也是 kind="video"（逐帧烘成图层），但它在剪映里仍然属于文字层，
        # 不能被 effect 轨的模糊盖住——所以接 text_layer 标记一起算作「视频层结束」。
        if (u["kind"] != "video" or u.get("text_layer")) and not _video_done:
            for _k, _f in enumerate(_effect_blur):
                fc_parts.append(f"{cur}{_f}[veff{_k}]")
                cur = f"[veff{_k}]"
            _video_done = True
        en = f"enable='between(t,{u['t_start']:.4f},{min(u['t_start'] + u['t_dur'], total_sec):.4f})'"
        # pts_lag：overlay 的第二路输入按「PTS ≥ 主帧 t」取帧（向后取整），所以图层第 k 帧
        # 会落在主帧 k+1 上——贴纸出场滑动实测逐帧比原版快一帧（12.40s 起，我们 dy=3 时原版 0，
        # 整体领先 1 帧）。把叠层流往后挪 1/fps 就还原成「第 k 帧 ↔ 主帧 k」。
        #
        # 尾部的「退场晚」不是这里的时间窗写错：素材帧数比段短时（序列贴纸 15 帧@16fps = 0.9375s，
        # 段长 1.05s），overlay 默认 eof_action=repeat 会把最后一帧拷贝到段尾。所以由
        # sticker_render.plan_sticker_layers 直接把 dur 截到「素材能铺满的长度」，窗口自己到点就收
        # —— 不加 eof_action=pass：实测 pass 会在最后一帧的整数时间边界上提前 EOF，吞掉帧 283。
        _lag = f"+{int(u.get('pts_lag') or 0)}/({fps}*TB)" if u.get("pts_lag") else ""
        if u["kind"] == "video":
            seg_i = n_in
            inputs += ["-i", str(u["seg"])]
            n_in += 1
            if u["matte"]:
                matte_i = n_in
                inputs += ["-i", str(u["matte"])]
                n_in += 1
            _skf = u.get("scale_kf") or []
            if not _skf:
                if u["matte"]:
                    dur_u = min(u["t_dur"], total_sec - u["t_start"])
                    t0_u = u["t_start"]
                    # 前景与 matte 强制同 fps 同长（Vision 抠像输出帧率会漂，不拉齐就是逐帧错位的鬼影）；
                    # 流必须平移到 t_start：enable 只管显隐，叠层流从 0 开始消费会让内容时间戳全错
                    fc_parts.append(
                        f"[{seg_i}:v]fps={fps},setpts=PTS-STARTPTS,trim=duration={dur_u:.4f},setpts=PTS+{t0_u:.4f}/TB{_lag},format=rgba[fg{n}];"
                        f"[{matte_i}:v]fps={fps},setpts=PTS-STARTPTS,trim=duration={dur_u:.4f},setpts=PTS+{t0_u:.4f}/TB{_lag},format=gray[mm{n}];"
                        f"[fg{n}][mm{n}]alphamerge[cut{n}]"
                    )
                else:
                    fc_parts.append(f"[{seg_i}:v]fps={fps},setpts=PTS+{u['t_start']:.4f}/TB{_lag},format=rgba[cut{n}]")
            if _skf:
                # 缩放关键帧逐帧：一路输入 split 成 K 支，每支 trim 出自己那几帧、各自 scale 到
                # 「该帧自己的图层像素尺寸」，再按它自己的时间窗 overlay。
                # 为什么不在 PIL 里烘：这层带抠像（人物 alpha 在单独的 matte 流里），
                # PIL 烘图层会绕开 alphamerge；切片方式让 alphamerge 照旧逐帧做，
                # 几何只换成「这一帧的尺寸」，其它（抠像/描边/遮罩外的 z 序）一点不动。
                # 渐隐/透明度必须留在 split 之前：fade 是按绝对时间算的，切片后每段起点不同会算错。
                _K = len(_skf)
                _t0u = u["t_start"]
                # 时间轴：整条流一次性平移到 t_start，**切片不再各自 setpts**。
                # 为什么不能逐片 setpts：`setpts=PTS+{ta:.4f}/TB` 的 ta/TB 会截断成整数帧号，
                # ta=0.0833 → 4.998 → 4（早了整整一帧），于是首片之后就整体领先原版 1 帧
                # （实测 0–1.67s 叠层内容错一帧，奇偶帧交替差 2.5–3.9）。一次性平移只有一个
                # 全局截断（≤1/60 帧、所有帧一起），切片只靠 trim 选帧 + enable 管显隐。
                _pre = f"fps={fps},setpts=PTS-STARTPTS+{_t0u:.6f}/TB{_lag},format=rgba"
                if u.get("fade"):
                    _d0, _dd, _ds = u["fade"]
                    _pre += f",fade=t={_d0}:st={_t0u + _ds:.4f}:d={_dd:.4f}:alpha=1"
                if abs(u["alpha"] - 1.0) > 1e-3:
                    _pre += f",colorchannelmixer=aa={u['alpha']:.3f}"
                fc_parts.append(f"[{seg_i}:v]{_pre},split={_K}"
                                + "".join(f"[kf{n}_{k}]" for k in range(_K)))
                if u["matte"]:
                    fc_parts.append(
                        f"[{matte_i}:v]fps={fps},setpts=PTS-STARTPTS+{_t0u:.6f}/TB{_lag},format=gray,split={_K}"
                        + "".join(f"[km{n}_{k}]" for k in range(_K)))
                _dbg = []
                # enable 窗口整体前移 2 ms（1/60 帧的 12%）：每个切片只覆盖「自己那几帧」。
                # 不能直接用 [ta, tb]：秒值只能写到 1e-4，而帧间隔是 1/60=0.0166667，
                # 四舍五入后边界会落在帧时间的一侧——t=0.0666667 对 0.0667 来说「不到」，
                # 上一段又已经在 0.0667 结束，那一帧就落到上一段的尾帧上（逐帧错位一帧，
                # 实测 0–1.67s 叠层逐帧后奇偶帧交替差 2.5–3.9，偶数帧与旧实现一致）。
                # 结论：边界必须带一个小于半帧的固定偏移，每一帧就恰好落在一个窗口里。
                _EPS = 0.002
                for k, (_ta, _tb, _kw, _kh) in enumerate(_skf):
                    _fa = max(0, int(round((_ta - _t0u) * fps)))
                    _fb = max(_fa + 1, int(round((_tb - _t0u) * fps)))
                    _tbe = min(_tb, total_sec)
                    fc_parts.append(
                        f"[kf{n}_{k}]trim=start_frame={_fa}:end_frame={_fb},"
                        f"scale={_kw}:{_kh}:flags=lanczos[kfg{n}_{k}]")
                    if u["matte"]:
                        fc_parts.append(
                            f"[km{n}_{k}]trim=start_frame={_fa}:end_frame={_fb},"
                            f"scale={_kw}:{_kh}:flags=bicubic[kmg{n}_{k}]")
                        fc_parts.append(f"[kfg{n}_{k}][kmg{n}_{k}]alphamerge[kfa{n}_{k}]")
                    _lbl = f"[kfa{n}_{k}]" if u["matte"] else f"[kfg{n}_{k}]"
                    _en = f"enable='between(t,{_ta - _EPS:.4f},{_tbe - _EPS:.4f})'"
                    if u.get("pos_kf"):
                        _pkx, _pky = u["pos_kf"]
                        _xs = _kf_piecewise(_pkx, _t0u, W / 2.0, W / 2.0 - _kw / 2)
                        _ys = _kf_piecewise(_pky, _t0u, -H / 2.0, H / 2.0 - _kh / 2)
                        _xs_s, _ys_s = f"'{_xs}'", f"'{_ys}'"
                    else:
                        _xs_s = f"{int(round((W - _kw) / 2 + u['tx'] * W / 2))}"
                        _ys_s = f"{int(round((H - _kh) / 2 - u['ty'] * H / 2))}"
                    fc_parts.append(f"{cur}{_lbl}overlay=x={_xs_s}:y={_ys_s}:{_en}[v{n}_{k}]")
                    cur = f"[v{n}_{k}]"
                    _dbg.append(f"{_ta:.3f}-{_tbe:.3f}:{_kw}x{_kh}")
                if depth == 0:
                    report.setdefault("notes", []).append(
                        f"叠层 {t_idx}/{o}: 缩放关键帧逐帧 {_K} 段（{_dbg[0]} … {_dbg[-1]}）")
                continue
            _cut_lbl = f"[cut{n}]"
            if u.get("fade"):
                _dir, _d, _st_off = u["fade"]
                fc_parts.append(f"{_cut_lbl}fade=t={_dir}:st={u['t_start'] + _st_off:.4f}:d={_d:.4f}:alpha=1[cutf{n}]")
                _cut_lbl = f"[cutf{n}]"
            if abs(u["alpha"] - 1.0) > 1e-3:
                fc_parts.append(f"{_cut_lbl}colorchannelmixer=aa={u['alpha']:.3f}[cutx{n}]")
                src_lbl = f"[cutx{n}]"
            else:
                src_lbl = _cut_lbl
            ov_w = int(u.get("lw") or (int(round(W * u["scale"])) if abs(u.get("scale", 1.0) - 1.0) > 1e-3 else W))
            ov_h = int(u.get("lh") or (int(round(H * u["scale"])) if abs(u.get("scale", 1.0) - 1.0) > 1e-3 else H))
            ox = int(round((W - ov_w) / 2 + u["tx"] * W / 2))
            oy = int(round((H - ov_h) / 2 - u["ty"] * H / 2))  # 剪映 y 正=向上
            if u.get("pos_kf"):
                (_pkx, _pky) = u["pos_kf"]
                # keyframe 位置与 clip.transform 同单位（-1..1，y 正=向上），换算到像素中心：
                #   px = W/2 + v*W/2 - ov_w/2 ；py = H/2 - v*H/2 - ov_h/2
                _xs = _kf_piecewise(_pkx, u["t_start"], W / 2.0, W / 2.0 - ov_w / 2)
                _ys = _kf_piecewise(_pky, u["t_start"], -H / 2.0, H / 2.0 - ov_h / 2)
                fc_parts.append(f"{cur}{src_lbl}overlay=x='{_xs}':y='{_ys}':{en}[v{n}]")
            else:
                fc_parts.append(f"{cur}{src_lbl}overlay=x={ox}:y={oy}:{en}[v{n}]")
        else:  # text / sticker PNG
            png_i = n_in
            inputs += ["-loop", "1", "-t", f"{max(0.05, u['t_dur']):.4f}", "-i", str(u["png"])]
            n_in += 1
            if u["kind"] == "sticker":
                with PILImage.open(u["png"]) as _im:
                    nat_w, nat_h = _im.size
                sc = float(u.get("scale") or 1.0)
                pw = max(2, int(round(nat_w * sc / 2)) * 2)
                ph = max(2, int(round(nat_h * sc / 2)) * 2)
                if (pw, ph) != (nat_w, nat_h):
                    fc_parts.append(f"[{png_i}:v]format=rgba,scale={pw}:{ph}[t{n}]")
                else:
                    fc_parts.append(f"[{png_i}:v]format=rgba[t{n}]")
                report.setdefault("notes", []).append(
                    f"sticker 尺寸按原生像素×{sc:.2f} 近似（草稿贴纸无绝对 px 真值）")
            else:
                pw, ph = u["pw"], u["ph"]
                fc_parts.append(f"[{png_i}:v]format=rgba[t{n}]")
            if abs(u["alpha"] - 1.0) > 1e-3:
                fc_parts.append(f"[t{n}]colorchannelmixer=aa={u['alpha']:.3f}[tx{n}]")
                lbl = f"[tx{n}]"
            else:
                lbl = f"[t{n}]"
            ox = int(round(W / 2 + u["tx"] * W / 2 - pw / 2))
            oy = int(round(H / 2 - u["ty"] * H / 2 - ph / 2))  # 剪映 y 正=向上
            fc_parts.append(f"{cur}{lbl}overlay=x={ox}:y={oy}:{en}[v{n}]")
        cur = f"[v{n}]"

    if not _video_done:
        for _k, _f in enumerate(_effect_blur):
            fc_parts.append(f"{cur}{_f}[veff{_k}]")
            cur = f"[veff{_k}]"
    tail = f"{cur}null"   # 无叠加单元时 cur 就是 [basecfr]，直接消费它
    # 模糊已在上面的「视频层」插入；这里只把未实现的其它特效计进 unrealized，不静默跳过。
    for _ti, _esegs in cls.get("effects") or []:
        for _s in _esegs:
            _e = vfx_by_id.get(_s.get("material_id")) or {}
            if "模糊" not in str(_e.get("name") or ""):
                report["unrealized"]["effect_not_implemented"] = \
                    report["unrealized"].get("effect_not_implemented", 0) + 1
    fc_parts.append(tail + "[vout]")
    fc = ";".join(fc_parts)
    fscript = work / "composite_filter.txt"
    fscript.write_text(fc, encoding="utf-8")
    help_txt = subprocess.run([ffmpeg_bin, "-hide_banner", "-h", "long"], capture_output=True, text=True).stdout or ""
    if "filter_complex_script" in help_txt:
        fc_args = ["-filter_complex_script", str(fscript)]
    elif "/filter_complex" in help_txt:
        fc_args = ["-/filter_complex", str(fscript)]
    else:
        fc_args = ["-filter_complex", fc]
    master_i = n_in
    inputs += ["-i", str(master)]
    final = work / ("composite_final.mov" if alpha_out else "composite_final.mp4")
    enc_args = (["-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le"]
                if alpha_out else
                ["-c:v", "libx264", "-preset", "medium", "-crf", "16", "-pix_fmt", "yuv420p"])
    run_ffmpeg([ffmpeg_bin, "-y", *inputs, *fc_args,
                "-map", "[vout]", "-map", f"{master_i}:a",
                "-t", f"{total_sec:.4f}",
                *enc_args,
                "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", str(final)], timeout=900)
    if alpha_out and units:
        # 自证：带图层的复合片段不允许交付全透明空壳（曾因静图 -t 放在 -i 前整段丢失）。
        # 多点采样：卡片/淡出类图层在采样点上可能合法为空，任一点非零即可。
        import io as _io
        from PIL import Image as _Img
        _amax_all = 0
        for _ts in (0.1, total_sec / 3, total_sec * 2 / 3):
            _ts = min(max(0.05, _ts), max(0.05, total_sec - 0.1))
            _probe = subprocess.run([ffmpeg_bin, "-v", "error", "-ss", f"{_ts:.3f}",
                                     "-i", str(final), "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"],
                                    capture_output=True)
            if _probe.stdout:
                _amax_all = max(_amax_all, _Img.open(_io.BytesIO(_probe.stdout)).convert("RGBA").getchannel("A").getextrema()[1])
            if _amax_all >= 8:
                break
        if _amax_all < 8:
            raise RuntimeError(f"复合片段合成输出全透明（{len(units)} 个图层全部丢失），拒绝交付空壳: {final}")
    import shutil

    shutil.move(str(final), str(final_out))
    if depth == 0:
        report["output"] = str(final_out)
    return final_out


def replicate_style(style_id: str, draft_name: str, out_dir: Path, draft_root: Path,
                    decrypted_root: Path, preview: bool = False) -> dict:
    draft_dir = draft_root / draft_name
    if not draft_dir.is_dir():
        raise SystemExit(f"草稿目录不存在: {draft_dir}")
    draft = load_draft(draft_dir, decrypted_root)
    work = out_dir / "replica_work" / style_id
    work.mkdir(parents=True, exist_ok=True)
    report: dict = {
        "style_id": style_id, "draft": draft_dir.name,
        "main_segments": 0, "overlay_segments": 0, "matte_done": 0, "matte_failed": 0,
        "text_segments": 0, "sticker_segments": 0,
        "masked_main_segments": 0, "masked_overlay_segments": 0,
        "compounds_rendered": [], "unrealized": {}, "notes": [],
    }
    final = render_timeline(draft, draft_dir, style_id, work, 30, preview, report, depth=0)
    out_mp4 = out_dir / f"{style_id}_replica.mp4"
    if final != out_mp4:
        import shutil
        shutil.copy2(str(final), str(out_mp4))
        report["output"] = str(out_mp4)
    import json as _json
    (work / "replica_report.json").write_text(
        _json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--style-id")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--out", default="/tmp/blind_review")
    ap.add_argument("--draft-root", default=_discover_root("drafts"))
    ap.add_argument("--decrypted-root", default=str(ROOT / "forensic" / "decrypted"))
    ap.add_argument("--preview", action="store_true")
    args = ap.parse_args()

    sources = json.loads((ROOT / "forensic" / "style_sources.json").read_text(encoding="utf-8"))["styles"]
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    targets = sorted(sources.items()) if args.all else [(args.style_id, sources[args.style_id])]
    failures = []
    for sid, dname in targets:
        print(f"[replica] {sid} <- {dname}")
        try:
            rep = replicate_style(sid, dname, out_dir, Path(args.draft_root), Path(args.decrypted_root), preview=args.preview)
            print(f"[replica] {sid} 完成 -> {rep.get('output')}  unrealized={rep['unrealized']}")
        except Exception as exc:
            failures.append(f"{sid}: {type(exc).__name__}: {exc}")
            print(f"[replica] {sid} 失败: {exc}")
    if failures:
        print("[replica] 失败清单：")
        for f in failures:
            print("  " + f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
