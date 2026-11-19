#!/usr/bin/env python3
"""
short-video-studio 公共工具模块
集中管理唯一权威常量、多账号隔离工作区、FFmpeg/FFprobe 调用、商用字体合规校验、
抗混叠多速率音频读写、3D LUT/Style 解析与几何仿射映射。
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import re
import shutil
import subprocess
import sys
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SKILL_ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = SKILL_ROOT / "assets"
REFERENCES_DIR = SKILL_ROOT / "references"
SCRIPTS_DIR = SKILL_ROOT / "scripts"
VENV_DIR = Path.home() / ".short-video-studio" / "venv"
DEFAULT_WORKSPACE_ROOT = Path(
    os.environ.get("SVS_WORKSPACE_ROOT") or (Path.home() / ".short-video-studio" / "workspaces")
)

# 标准成片画布与安全区常量（1080x1920 @ 30fps，全仓唯一权威真相源）
CANVAS_WIDTH = 1080
CANVAS_HEIGHT = 1920
DEFAULT_FPS = 30
TARGET_LUFS = -14.0

# 平台 UI 遮挡区边界（任何关键文字/贴纸不得侵入）
SAFE_ZONE = {
    "x_min": 60,
    "x_max": 880,   # 避开右侧互动栏 (880, 1080]
    "y_min": 160,   # 避开顶部状态/搜索栏 [0, 160)
    "y_max": 1480,  # 避开底部文案/POI/进度条 (1480, 1920]
}

SUBTITLE_SAFE_ZONE = {
    "x_min": 80,
    "x_max": 860,
    "y_min": 1180,
    "y_max": 1468,
}

MEDIA_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm"}

BT709_FFMPEG_ARGS = [
    "-pix_fmt", "yuv420p",
    "-color_range", "tv",
    "-colorspace", "bt709",
    "-color_primaries", "bt709",
    "-color_trc", "bt709",
]

BT709_VF_SUFFIX = [
    "setsar=1",
    "scale=out_color_matrix=bt709:out_range=tv",
    "format=yuv420p",
    "setparams=range=tv:color_primaries=bt709:color_trc=bt709:colorspace=bt709",
]

# 全仓统一的 BT.709 健康肤色矢量扇区（用于 Step 7 掩膜校色与 #15 黑盒示波器校验）
SKIN_YCBCR_RANGE = {
    "cb_min": 86.0,
    "cb_max": 132.0,
    "cr_min": 128.0,
    "cr_max": 174.0,
}

# 全仓统一的制作元数据与开发词防露馅禁用正则集（#11 与 #17 共享）
LEAK_REGEX_PATTERNS = [
    r"\bDJI_\d+",
    r"\bIMG_\d+",
    r"\bMVI_\d+",
    r"\bDSC_\d+",
    r"202\d[-_/.]\d{2}",
    r"第\d+遍",
    r"口述稿?",
    r"提词器?",
    r"STYLE[_-]?\d+",
    r"style_[012]\d",
    r"\bNOTION\b",
    r"暗黑科技",
    r"强反差财经",
    r"高级感杂志",
    r"高密口播",
    r"电影感空间",
    r"纸张拼贴",
    r"流派模板",
    r"风格谱",
]

# 经核验的免费/OFL 可商用中文字体文件名白名单前缀
VERIFIED_COMMERCIAL_FONT_STEMS = (
    "alimamashuheiti",
    "sourcehansans",
    "sourcehanserif",
    "notosanssc",
    "notosanscjk",
    "notoserifcjk",
    "smileysans",
    "qingsongshouxieti",
    "lxgw",
    "opposans",
    "harmonyos",
)

_FONT_CACHE: Dict[Tuple[str, ...], Optional[str]] = {}


def get_preferred_python() -> str:
    """若当前不在专用 venv 且专用 venv 存在，返回其解释器路径。"""
    candidates = [
        VENV_DIR / "bin" / "python",
        VENV_DIR / "Scripts" / "python.exe",
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    return sys.executable


def maybe_reexec_in_venv() -> None:
    """若核心渲染/声学包(PIL/numpy/scipy)任一缺失且专用 venv 可用，自动切换至专用 venv 执行当前脚本。"""
    try:
        import PIL  # noqa: F401
        import numpy  # noqa: F401
        import scipy  # noqa: F401
        return
    except ImportError:
        pass

    preferred = get_preferred_python()
    if Path(preferred).resolve() != Path(sys.executable).resolve() and os.environ.get("SVS_IN_VENV") != "1":
        env = os.environ.copy()
        env["SVS_IN_VENV"] = "1"
        os.execvpe(preferred, [preferred, *sys.argv], env)


def find_bin(name: str) -> Optional[str]:
    """查找二进制可执行文件（ffmpeg / ffprobe / node / npm / uv 等）。"""
    return shutil.which(name)


def run_cmd(cmd: List[str], check: bool = True, capture: bool = True) -> subprocess.CompletedProcess:
    """统一 UTF-8 子进程执行封装，失败时提供清晰命令上下文。"""
    res = subprocess.run(
        cmd,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and res.returncode != 0:
        err_tail = (res.stderr or "")[-1200:]
        raise RuntimeError(f"命令执行失败 ({res.returncode}): {' '.join(cmd)}\n{err_tail}")
    return res


def ffmpeg_has_filter(filter_name: str) -> bool:
    """检测当前 ffmpeg 是否编译了指定滤镜（如 subtitles / drawtext / lut3d / zscale）。"""
    ffmpeg = find_bin("ffmpeg")
    if not ffmpeg:
        return False
    res = subprocess.run(
        [ffmpeg, "-hide_banner", "-filters"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    for line in (res.stdout or "").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == filter_name:
            return True
    return False


def ffprobe_media(media_path: Path) -> Dict[str, Any]:
    """调用 ffprobe 获取视频/音频流的详细元数据（含 side_data_list 旋转矩阵）。"""
    ffprobe = find_bin("ffprobe")
    if not ffprobe:
        raise RuntimeError("未找到 ffprobe，请先运行 check_and_setup.py 安装 ffmpeg。")
    cmd = [
        ffprobe, "-v", "quiet",
        "-print_format", "json",
        "-show_format", "-show_streams",
        str(media_path),
    ]
    res = run_cmd(cmd)
    return json.loads(res.stdout or "{}")


def parse_fps(rate_str: str) -> float:
    """把 '30000/1001' 或 '30/1' 转换为浮点数帧率。"""
    if not rate_str or rate_str == "0/0":
        return 0.0
    if "/" in rate_str:
        num, den = rate_str.split("/", 1)
        den_f = float(den)
        return float(num) / den_f if den_f else 0.0
    return float(rate_str)


def parse_video_stream_geometry(v_stream: Dict[str, Any]) -> Dict[str, Any]:
    """
    从 ffprobe 视频流读取 coded_w/coded_h 与 side_data_list/tags 中的旋转角度，
    输出旋转矫正后的真实展示宽高 (display_w, display_h) 与方向判定。
    """
    coded_w = int(v_stream.get("width") or 0)
    coded_h = int(v_stream.get("height") or 0)
    rotation = 0
    for sd in v_stream.get("side_data_list", []) or []:
        if isinstance(sd, dict) and sd.get("rotation") is not None:
            try:
                rotation = int(round(float(sd["rotation"])))
                break
            except Exception:
                pass
    if rotation == 0:
        tag_rot = (v_stream.get("tags") or {}).get("rotate")
        if tag_rot is not None:
            try:
                rotation = int(round(float(tag_rot)))
            except Exception:
                rotation = 0
    rot_norm = abs(rotation) % 360
    if rot_norm in (90, 270):
        display_w, display_h = coded_h, coded_w
    else:
        display_w, display_h = coded_w, coded_h
    orientation = "portrait" if display_h >= display_w else "landscape"
    return {
        "coded_width": coded_w,
        "coded_height": coded_h,
        "rotation_deg": rotation,
        "display_width": display_w,
        "display_height": display_h,
        "orientation": orientation,
    }


def compute_crop_transform(
    display_w: int,
    display_h: int,
    dst_w: int = CANVAS_WIDTH,
    dst_h: int = CANVAS_HEIGHT,
    crop_x_frac: float = 0.5,
    crop_y_frac: float = 0.5,
) -> Dict[str, Any]:
    """计算 scale(increase) + crop 到目标画布的确定性仿射变换参数。"""
    dw = max(1, int(display_w or dst_w))
    dh = max(1, int(display_h or dst_h))
    scale = max(float(dst_w) / float(dw), float(dst_h) / float(dh))
    # ffmpeg scale 偶对齐
    scaled_w = max(dst_w, int(round(dw * scale)))
    scaled_h = max(dst_h, int(round(dh * scale)))
    if scaled_w % 2 == 1:
        scaled_w += 1
    if scaled_h % 2 == 1:
        scaled_h += 1
    max_cx = max(0, scaled_w - dst_w)
    max_cy = max(0, scaled_h - dst_h)
    crop_x = int(round(max_cx * max(0.0, min(1.0, crop_x_frac))))
    crop_y = int(round(max_cy * max(0.0, min(1.0, crop_y_frac))))
    return {
        "src_w": dw,
        "src_h": dh,
        "scaled_w": scaled_w,
        "scaled_h": scaled_h,
        "crop_x": crop_x,
        "crop_y": crop_y,
        "dst_w": int(dst_w),
        "dst_h": int(dst_h),
    }


def map_norm_bbox_to_crop(
    bbox: Optional[Dict[str, float]],
    crop_tx: Dict[str, Any],
) -> Optional[Dict[str, float]]:
    """将裁切前显示画幅的归一化 {x, y, w, h} 精确仿射映射到目标 1080x1920 归一化坐标。"""
    if not isinstance(bbox, dict) or not crop_tx:
        return bbox
    sw = float(crop_tx["scaled_w"])
    sh = float(crop_tx["scaled_h"])
    cx = float(crop_tx["crop_x"])
    cy = float(crop_tx["crop_y"])
    dw = float(crop_tx["dst_w"])
    dh = float(crop_tx["dst_h"])
    x1 = (float(bbox.get("x", 0.0)) * sw - cx) / dw
    y1 = (float(bbox.get("y", 0.0)) * sh - cy) / dh
    x2 = ((float(bbox.get("x", 0.0)) + float(bbox.get("w", 0.0))) * sw - cx) / dw
    y2 = ((float(bbox.get("y", 0.0)) + float(bbox.get("h", 0.0))) * sh - cy) / dh
    cx1, cy1 = max(0.0, min(1.0, x1)), max(0.0, min(1.0, y1))
    cx2, cy2 = max(0.0, min(1.0, x2)), max(0.0, min(1.0, y2))
    if cx2 - cx1 < 0.015 or cy2 - cy1 < 0.015:
        return None
    return {
        "x": round(cx1, 4),
        "y": round(cy1, 4),
        "w": round(cx2 - cx1, 4),
        "h": round(cy2 - cy1, 4),
    }


def map_eye_anchor_to_crop(
    eye_anchor: Optional[Dict[str, Any]],
    crop_tx: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """将裁切前归一化双眼锚点映射至目标 1080x1920 画布，若被裁出画面外则返回 None。"""
    if not isinstance(eye_anchor, dict) or not crop_tx:
        return eye_anchor
    sw = float(crop_tx["scaled_w"])
    sh = float(crop_tx["scaled_h"])
    cx = float(crop_tx["crop_x"])
    cy = float(crop_tx["crop_y"])
    dw = float(crop_tx["dst_w"])
    dh = float(crop_tx["dst_h"])
    nx = (float(eye_anchor.get("eye_cx", 0.5)) * sw - cx) / dw
    ny = (float(eye_anchor.get("eye_cy", 0.35)) * sh - cy) / dh
    if not (0.02 <= nx <= 0.98 and 0.02 <= ny <= 0.98):
        return None
    out = dict(eye_anchor)
    out["eye_cx"] = round(nx, 4)
    out["eye_cy"] = round(ny, 4)
    return out


def is_verified_commercial_font(font_path: Optional[Path | str]) -> bool:
    """严格判断字体文件是否属于已验证的开源/免费商用白名单（绝不把 PingFang/微软雅黑标为可商用）。"""
    if not font_path:
        return False
    p = Path(font_path)
    low = p.stem.lower().replace("-", "").replace("_", "").replace(" ", "")
    return any(stem in low for stem in VERIFIED_COMMERCIAL_FONT_STEMS)


def _font_search_dirs() -> List[Path]:
    dirs: List[Path] = [ASSETS_DIR / "fonts"]
    sys_name = platform.system()
    if sys_name == "Darwin":
        dirs += [
            Path.home() / "Library" / "Fonts",
            Path("/Library/Fonts"),
            Path("/System/Library/Fonts"),
            Path("/System/Library/Fonts/Supplemental"),
        ]
    elif sys_name == "Windows":
        win_dir = Path(os.environ.get("WINDIR", "C:\\Windows"))
        dirs += [
            win_dir / "Fonts",
            Path.home() / "AppData" / "Local" / "Microsoft" / "Windows" / "Fonts",
        ]
    else:
        dirs += [
            Path.home() / ".local" / "share" / "fonts",
            Path("/usr/share/fonts"),
            Path("/usr/local/share/fonts"),
        ]
    return dirs


def find_commercial_font(candidates: Optional[List[str]] = None, verified_only: bool = False) -> Optional[str]:
    """
    跨平台查找中文字体（带结果缓存）：
    优先查找开源免费商用字体，verified_only=True 时拒绝非商用兜底字体。
    """
    default_verified = [
        "AlimamaShuHeiTi-Bold.otf",
        "SourceHanSansCN-Bold.otf",
        "SourceHanSansCN-Medium.otf",
        "SourceHanSansCN-Regular.otf",
        "SourceHanSerifCN-Bold.otf",
        "SmileySans-Oblique.ttf",
        "QingSongShouXieTi1.ttf",
        "QingSongShouXieTi2.ttf",
        "NotoSansCJKsc-Bold.otf",
        "NotoSansCJKsc-Regular.otf",
        "NotoSansSC-Bold.ttf",
        "NotoSansSC-Medium.ttf",
        "NotoSansSC-Regular.ttf",
    ]
    fallback_system = [
        "PingFang.ttc",
        "STHeiti Medium.ttc",
        "msyhbd.ttc",
        "msyh.ttc",
        "simhei.ttf",
    ]
    pool = list(default_verified) if verified_only else (list(default_verified) + fallback_system)
    names = list(candidates or []) + [c for c in pool if not candidates or c not in candidates]
    if verified_only:
        names = [n for n in names if is_verified_commercial_font(n)]

    cache_key = (str(verified_only), *names)
    if cache_key in _FONT_CACHE:
        return _FONT_CACHE[cache_key]

    search_dirs = _font_search_dirs()
    for font_name in names:
        for d in search_dirs:
            if not d.exists():
                continue
            direct = d / font_name
            if direct.is_file():
                _FONT_CACHE[cache_key] = str(direct)
                return _FONT_CACHE[cache_key]
            for sub in d.glob(f"**/{font_name}"):
                if sub.is_file():
                    _FONT_CACHE[cache_key] = str(sub)
                    return _FONT_CACHE[cache_key]
    _FONT_CACHE[cache_key] = None
    return None


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_subtitle_templates() -> Dict[str, Any]:
    tpl_path = ASSETS_DIR / "subtitle_templates" / "templates.json"
    return read_json(tpl_path)["templates"]


def load_style_applied(pipeline_dir: Path) -> Optional[str]:
    """统一读取已绑定的 style_spectrum_applied.json 中的 style_id。"""
    spec_p = pipeline_dir / "style_spectrum_applied.json"
    if spec_p.exists():
        try:
            sid = read_json(spec_p).get("style_id")
            return str(sid) if sid else None
        except Exception:
            return None
    return None


def load_style_spectrum_entry(style_id: str) -> Optional[Dict[str, Any]]:
    """统一查询 style_spectrum_library.json 中的风格条目。"""
    lib_p = ASSETS_DIR / "style_spectrum" / "style_spectrum_library.json"
    if not lib_p.exists():
        return None
    try:
        lib = read_json(lib_p)
        for s in lib.get("styles", []):
            if s.get("style_id") == style_id:
                return s
    except Exception:
        return None
    return None


def resolve_lut_path(lut_name: Optional[str], style_id: Optional[str] = None) -> Path:
    """单源 LUT 解析：优先从 style_spectrum/luts 解析 20 套风格 LUT，次选内置通用 LUT。"""
    if style_id:
        sp = ASSETS_DIR / "style_spectrum" / "luts" / f"{style_id}.cube"
        if sp.exists():
            return sp
    if lut_name:
        cand1 = ASSETS_DIR / "style_spectrum" / "luts" / lut_name
        if cand1.exists():
            return cand1
        cand2 = ASSETS_DIR / "luts" / lut_name
        if cand2.exists():
            return cand2
    return ASSETS_DIR / "luts" / "clean_creator.cube"


def write_cube_lut(out_path: Path, lut_arr: Any, size: int, title: str) -> Path:
    """统一写出 3D .cube LUT 文件。"""
    import numpy as np

    arr = np.clip(np.asarray(lut_arr, dtype=np.float32).reshape(-1, 3), 0.0, 1.0)
    header = [
        f'TITLE "{title}"',
        f"LUT_3D_SIZE {int(size)}",
        "DOMAIN_MIN 0.0 0.0 0.0",
        "DOMAIN_MAX 1.0 1.0 1.0",
    ]
    body = [f"{r:.6f} {g:.6f} {b:.6f}" for r, g, b in arr]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(header + body) + "\n", encoding="utf-8")
    return out_path


def link_or_copy(src: Path, dst: Path) -> Path:
    """跨文件统一的同卷硬链接、失败退化拷贝辅助函数。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() or dst.is_symlink():
        dst.unlink()
    try:
        os.link(src.resolve(), dst)
    except OSError:
        shutil.copy2(src.resolve(), dst)
    return dst


def get_wav_duration_sec(wav_path: Path) -> float:
    """准确获取 WAV 时长（异常头时自动交由 ffprobe 测量）。"""
    if not wav_path.exists():
        return 0.0
    try:
        with wave.open(str(wav_path), "rb") as wf:
            n_frames = wf.getnframes()
            sr = wf.getframerate()
            return n_frames / float(sr) if sr > 0 else 0.0
    except Exception:
        try:
            info = ffprobe_media(wav_path)
            return float(info.get("format", {}).get("duration") or 0.0)
        except Exception:
            return 0.0


def read_wav_as_48k_stereo(wav_path: Path, target_sr: int = 48000) -> Tuple[Any, int]:
    """
    通吃任意采样率(44.1k/48k/96k)、任意位深(8/16/24/32-bit PCM 或 float)与单/双声道 WAV，
    统一抗混叠重采样为 float32 [-1, 1] 的双声道 target_sr 数组 (N, 2)。
    """
    import numpy as np

    arr: Optional[np.ndarray] = None
    src_sr = 0
    try:
        with wave.open(str(wav_path), "rb") as wf:
            ch = wf.getnchannels()
            sw = wf.getsampwidth()
            src_sr = wf.getframerate()
            comptype = wf.getcomptype()
            raw = wf.readframes(wf.getnframes())
        if comptype == "NONE" and sw in (1, 2, 3, 4) and src_sr > 0:
            if sw == 1:
                a = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
            elif sw == 2:
                a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            elif sw == 3:
                b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3)
                # little-endian signed 24-bit
                i32 = (b[:, 0].astype(np.int32) | (b[:, 1].astype(np.int32) << 8) | (b[:, 2].astype(np.int32) << 16))
                i32 = np.where(i32 & 0x800000, i32 - 0x1000000, i32)
                a = i32.astype(np.float32) / 8388608.0
            else:
                a = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
            a = a.reshape(-1, ch)
            if ch == 1:
                arr = np.column_stack([a[:, 0], a[:, 0]])
            elif ch >= 2:
                arr = a[:, :2]
    except Exception:
        arr = None

    if arr is None:
        # 回退给 ffmpeg 统一解码为 48kHz stereo pcm_s16le
        ffmpeg = find_bin("ffmpeg")
        if not ffmpeg:
            raise RuntimeError(f"无法读取音频且缺少 ffmpeg: {wav_path}")
        res = subprocess.run(
            [
                ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(wav_path),
                "-f", "s16le", "-acodec", "pcm_s16le", "-ar", str(target_sr), "-ac", "2",
                "pipe:1",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        pcm = np.frombuffer(res.stdout, dtype=np.int16).astype(np.float32) / 32768.0
        return pcm.reshape(-1, 2), target_sr

    if src_sr != target_sr and len(arr) > 0:
        from scipy.signal import resample_poly
        g = math.gcd(int(src_sr), int(target_sr))
        up = int(target_sr) // g
        down = int(src_sr) // g
        arr = resample_poly(arr, up, down, axis=0).astype(np.float32)

    return np.ascontiguousarray(arr, dtype=np.float32), int(target_sr)


def measure_lufs_ebur128(media_path: Path, raise_on_error: bool = True) -> Dict[str, float]:
    """统一使用 ffmpeg ebur128 测量综合响度(I)、响度范围(LRA)与真峰值(TP)，绝不静默伪造假数值。"""
    ffmpeg = find_bin("ffmpeg")
    if not ffmpeg:
        if raise_on_error:
            raise RuntimeError("未找到 ffmpeg，无法测量 EBU R128 响度。")
        return {"integrated_lufs": -99.0, "lra_lu": 0.0, "true_peak_dbtp": -99.0, "valid": False}

    res = subprocess.run(
        [ffmpeg, "-hide_banner", "-nostats", "-i", str(media_path), "-af", "ebur128=peak=true", "-f", "null", "-"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    txt = res.stderr or ""
    summary_idx = txt.rfind("Summary:")
    scope = txt[summary_idx:] if summary_idx >= 0 else txt
    m_i = re.findall(r"I:\s*(-?[\d.]+)\s*LUFS", scope)
    m_lra = re.findall(r"LRA:\s*([\d.]+)\s*LU", scope)
    m_tp = re.findall(r"Peak:\s*(-?[\d.]+)\s*dBFS", scope)
    if not m_i:
        if raise_on_error:
            raise RuntimeError(f"ebur128 响度解析失败: {media_path}\n{txt[-400:]}")
        return {"integrated_lufs": -99.0, "lra_lu": 0.0, "true_peak_dbtp": -99.0, "valid": False}
    return {
        "integrated_lufs": float(m_i[-1]),
        "lra_lu": float(m_lra[-1]) if m_lra else 0.0,
        "true_peak_dbtp": float(m_tp[-1]) if m_tp else -1.5,
        "valid": True,
    }


def check_bbox_in_safe_zone(bbox: Dict[str, int]) -> Tuple[bool, str]:
    """按全仓唯一权威 SAFE_ZONE 校验元素包围盒是否进入平台 UI 遮挡区。"""
    x1 = int(bbox["x"])
    y1 = int(bbox["y"])
    x2 = x1 + int(bbox["w"])
    y2 = y1 + int(bbox["h"])

    if y1 < SAFE_ZONE["y_min"]:
        return False, f"顶部越界: y1={y1} < {SAFE_ZONE['y_min']}"
    if y2 > SAFE_ZONE["y_max"]:
        return False, f"底部越界进入文案/进度条区: y2={y2} > {SAFE_ZONE['y_max']}"
    if x1 < SAFE_ZONE["x_min"]:
        return False, f"左侧越界: x1={x1} < {SAFE_ZONE['x_min']}"
    if x2 > SAFE_ZONE["x_max"]:
        return False, f"右侧越界进入互动按钮区: x2={x2} > {SAFE_ZONE['x_max']}"
    return True, "OK"


def sec_to_frames(sec: float, fps: int = DEFAULT_FPS) -> int:
    return int(round(sec * fps))


def frames_to_sec(frames: int, fps: int = DEFAULT_FPS) -> float:
    return round(frames / float(fps), 4)


def format_tc(sec: float) -> str:
    """格式化为 MM:SS.ms 可读时间码。"""
    m = int(sec // 60)
    s = sec % 60
    return f"{m:02d}:{s:05.2f}"


def sanitize_slug(name: Optional[str], default: str = "default") -> str:
    """将账号名/项目名清洗为安全目录名，保留中文/字母/数字/下划线/短横线，禁止路径穿越。"""
    raw = str(name or "").strip()
    cleaned = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", raw).strip("._-")
    if not cleaned or cleaned in (".", ".."):
        return default
    return cleaned[:64]


def resolve_workspace_paths(
    account: Optional[str] = None,
    project_id: Optional[str] = None,
    project_dir: Optional[Path] = None,
    raw_dir: Optional[Path] = None,
    workspace_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    解析多账号物理隔离工作区与专属加工目录，并严格防止向用户原素材目录写入任何加工文件。
    目录结构：<workspace_root>/accounts/<account_slug>/{content-log/account_profile.json,compliance_whitelist.json,projects/<project_id>}
    """
    ws_root = (workspace_root or DEFAULT_WORKSPACE_ROOT).expanduser().resolve()
    acc_slug = sanitize_slug(account, default="default")
    account_dir = ws_root / "accounts" / acc_slug
    content_log_dir = account_dir / "content-log"

    raw_res = raw_dir.expanduser().resolve() if raw_dir else None
    if project_id:
        proj_slug = sanitize_slug(project_id, default="project")
    elif project_dir:
        proj_slug = sanitize_slug(project_dir.name, default="project")
    elif raw_res:
        proj_slug = sanitize_slug(raw_res.name, default="project")
    else:
        proj_slug = "default_project"

    redirected = False
    if project_dir is not None:
        pd_res = project_dir.expanduser().resolve()
        if raw_res is not None and (pd_res == raw_res or raw_res in pd_res.parents):
            redirected = True
            eff_proj_dir = account_dir / "projects" / proj_slug
        else:
            eff_proj_dir = pd_res
    else:
        eff_proj_dir = account_dir / "projects" / proj_slug

    eff_raw_dir = raw_res if raw_res is not None else (eff_proj_dir / "raw_media")
    pipeline_dir = eff_proj_dir / "pipeline"
    if raw_res is not None and (pipeline_dir == raw_res or raw_res in pipeline_dir.parents):
        redirected = True
        eff_proj_dir = account_dir / "projects" / proj_slug
        pipeline_dir = eff_proj_dir / "pipeline"

    out_dir = eff_proj_dir / "out"
    account_profile_path = content_log_dir / "account_profile.json"
    legacy_prof = eff_proj_dir / "content-log" / "account_profile.json"
    if not account and not account_profile_path.exists() and legacy_prof.exists():
        account_profile_path = legacy_prof

    return {
        "workspace_root": ws_root,
        "account_slug": acc_slug,
        "account_dir": account_dir,
        "content_log_dir": content_log_dir,
        "account_profile_path": account_profile_path,
        "compliance_whitelist_path": account_dir / "compliance_whitelist.json",
        "project_id": proj_slug,
        "project_dir": eff_proj_dir,
        "pipeline_dir": pipeline_dir,
        "out_dir": out_dir,
        "raw_dir": eff_raw_dir,
        "redirected_from_raw": redirected,
    }


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()
