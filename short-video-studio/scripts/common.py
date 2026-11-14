#!/usr/bin/env python3
"""
short-video-studio 公共工具模块
集中管理路径解析、环境 venv 检测、FFmpeg/FFprobe 调用、字体发现、安全区校验、JSON 读写。
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SKILL_ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = SKILL_ROOT / "assets"
REFERENCES_DIR = SKILL_ROOT / "references"
SCRIPTS_DIR = SKILL_ROOT / "scripts"
VENV_DIR = Path.home() / ".short-video-studio" / "venv"

# 标准成片画布与安全区常量（1080x1920 @ 30fps）
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
    """若核心渲染包(PIL/numpy)缺失且专用 venv 可用，自动切换至专用 venv 执行当前脚本。"""
    try:
        import PIL  # noqa: F401
        import numpy  # noqa: F401
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
    """统一子进程执行封装，失败时提供清晰命令上下文。"""
    res = subprocess.run(
        cmd,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        text=True,
    )
    if check and res.returncode != 0:
        err_tail = (res.stderr or "")[-1200:]
        raise RuntimeError(f"命令执行失败 ({res.returncode}): {' '.join(cmd)}\n{err_tail}")
    return res


def ffmpeg_has_filter(filter_name: str) -> bool:
    """检测当前 ffmpeg 是否编译了指定滤镜（如 subtitles / drawtext / lut3d）。"""
    ffmpeg = find_bin("ffmpeg")
    if not ffmpeg:
        return False
    res = subprocess.run([ffmpeg, "-hide_banner", "-filters"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    for line in res.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == filter_name:
            return True
    return False


def ffprobe_media(media_path: Path) -> Dict[str, Any]:
    """调用 ffprobe 获取视频/音频流的详细元数据。"""
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


def find_commercial_font(candidates: Optional[List[str]] = None) -> Optional[str]:
    """
    跨平台查找可商用中文字体：
    优先级：assets/fonts/ -> 用户字体目录 -> 系统字体目录。
    """
    default_candidates = [
        "AlimamaShuHeiTi-Bold.otf",
        "SourceHanSansCN-Bold.otf",
        "SourceHanSansCN-Medium.otf",
        "SourceHanSansCN-Regular.otf",
        "SourceHanSerifCN-Bold.otf",
        "SmileySans-Oblique.ttf",
        "NotoSansCJKsc-Bold.otf",
        "NotoSansCJKsc-Regular.otf",
        "NotoSansSC-Bold.ttf",
        "PingFang.ttc",
        "STHeiti Medium.ttc",
        "msyhbd.ttc",
        "msyh.ttc",
        "simhei.ttf",
    ]
    names = list(candidates or []) + [c for c in default_candidates if not candidates or c not in candidates]

    search_dirs: List[Path] = [ASSETS_DIR / "fonts"]
    sys_name = platform.system()
    if sys_name == "Darwin":
        search_dirs += [
            Path.home() / "Library" / "Fonts",
            Path("/Library/Fonts"),
            Path("/System/Library/Fonts"),
            Path("/System/Library/Fonts/Supplemental"),
        ]
    elif sys_name == "Windows":
        win_dir = Path(os.environ.get("WINDIR", "C:\\Windows"))
        search_dirs += [
            win_dir / "Fonts",
            Path.home() / "AppData" / "Local" / "Microsoft" / "Windows" / "Fonts",
        ]
    else:
        search_dirs += [
            Path.home() / ".local" / "share" / "fonts",
            Path("/usr/share/fonts"),
            Path("/usr/local/share/fonts"),
        ]

    for font_name in names:
        for d in search_dirs:
            if not d.exists():
                continue
            direct = d / font_name
            if direct.is_file():
                return str(direct)
            # 浅层子目录查找（如 Linux Noto 字体目录）
            for sub in d.glob(f"**/{font_name}"):
                if sub.is_file():
                    return str(sub)
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


def check_bbox_in_safe_zone(bbox: Dict[str, int]) -> Tuple[bool, str]:
    """校验元素包围盒是否在平台 UI 安全区内（不碰顶/底/右/左遮挡区）。"""
    x1 = bbox["x"]
    y1 = bbox["y"]
    x2 = x1 + bbox["w"]
    y2 = y1 + bbox["h"]

    if y1 < SAFE_ZONE["y_min"]:
        return False, f"顶部越界: y1={y1} < {SAFE_ZONE['y_min']}"
    if y2 > SAFE_ZONE["y_max"]:
        return False, f"底部越界进入文案/进度条区: y2={y2} > {SAFE_ZONE['y_max']}"
    if x1 < SAFE_ZONE["x_min"]:
        return False, f"左侧越界: x1={x1} < {SAFE_ZONE['x_min']}"
    # 右侧互动按钮区从 y=680 到底部，或者总安全右边界 880
    if x2 > SAFE_ZONE["x_max"] and y2 > 680:
        return False, f"右侧越界进入互动按钮区: x2={x2} > {SAFE_ZONE['x_max']}"
    if x2 > 1020:
        return False, f"右侧边缘越界: x2={x2} > 1020"
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
