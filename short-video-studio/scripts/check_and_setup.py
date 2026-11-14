#!/usr/bin/env python3
"""
跨平台环境自检与一键安装脚本 (支持 macOS / Windows / Linux)：
检查并配置：
- ffmpeg & ffprobe
- Node.js / npm (可选 Remotion / HyperFrames 支持)
- Python 独立虚拟环境 (~/.short-video-studio/venv) 及依赖 (pillow, numpy, edge-tts, soundfile, faster-whisper, librosa)
- 可商用中文字体
- 初始化生成内置 CC0 资产
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from common import VENV_DIR, find_bin, find_commercial_font, SCRIPTS_DIR


def check_command(cmd: str) -> str:
    found = find_bin(cmd)
    return found if found else "MISSING"


def try_install_ffmpeg(sys_name: str) -> None:
    if find_bin("ffmpeg") and find_bin("ffprobe"):
        return
    print("[setup] 未检测到 ffmpeg，尝试自动安装...")
    if sys_name == "Darwin" and find_bin("brew"):
        subprocess.run(["brew", "install", "ffmpeg"], check=False)
    elif sys_name == "Windows" and find_bin("winget"):
        subprocess.run(["winget", "install", "Gyan.FFmpeg", "--silent"], check=False)
    elif sys_name == "Linux" and find_bin("apt-get"):
        subprocess.run(["sudo", "apt-get", "update"], check=False)
        subprocess.run(["sudo", "apt-get", "install", "-y", "ffmpeg"], check=False)


def ensure_venv_and_deps(install_deps: bool, install_heavy_ml: bool) -> str:
    py_in_venv = VENV_DIR / ("Scripts/python.exe" if platform.system() == "Windows" else "bin/python")
    if install_deps:
        if not py_in_venv.exists():
            uv = find_bin("uv")
            VENV_DIR.parent.mkdir(parents=True, exist_ok=True)
            if uv:
                print(f"[setup] 使用 uv 创建隔离环境 ({VENV_DIR})...")
                # 优先尝试 Python 3.12 以获得最佳 faster-whisper / librosa 轮子兼容性
                res = subprocess.run([uv, "venv", "--python", "3.12", str(VENV_DIR)], check=False)
                if res.returncode != 0:
                    subprocess.run([uv, "venv", str(VENV_DIR)], check=True)
            else:
                print(f"[setup] 使用 python venv 创建隔离环境 ({VENV_DIR})...")
                subprocess.run([sys.executable, "-m", "venv", str(VENV_DIR)], check=True)

        core_pkgs = ["pillow", "numpy", "soundfile", "edge-tts"]
        heavy_pkgs = ["faster-whisper", "librosa"]
        uv = find_bin("uv")

        def pip_install(pkgs: list[str], allow_fail: bool = False) -> None:
            if uv:
                cmd = [uv, "pip", "install", "--python", str(py_in_venv), *pkgs]
            else:
                cmd = [str(py_in_venv), "-m", "pip", "install", "--upgrade", *pkgs]
            res = subprocess.run(cmd, check=False)
            if res.returncode != 0 and not allow_fail:
                print(f"[warn] 部分包安装未成功: {pkgs}")

        print(f"[setup] 安装核心音视频依赖: {', '.join(core_pkgs)} ...")
        pip_install(core_pkgs, allow_fail=True)
        if install_heavy_ml:
            print(f"[setup] 安装语音转写与节拍分析扩展包: {', '.join(heavy_pkgs)} ...")
            pip_install(heavy_pkgs, allow_fail=True)

    return str(py_in_venv) if py_in_venv.exists() else sys.executable


def main() -> None:
    parser = argparse.ArgumentParser(description="short-video-studio 环境检查与依赖安装")
    parser.add_argument("--check-only", action="store_true", help="仅检查当前环境，不执行安装")
    parser.add_argument("--init-assets", action="store_true", help="生成内置 CC0 BGM、音效、LUT 与贴纸")
    parser.add_argument("--install-deps", action="store_true", help="创建 venv 并安装 Python 音视频依赖")
    parser.add_argument("--install-ml", action="store_true", help="额外尝试安装 faster-whisper 与 librosa")
    parser.add_argument("--install-node-tools", action="store_true", help="检查并安装本地 Remotion/HyperFrames CLI")
    args = parser.parse_args()

    sys_name = platform.system()
    print("=== short-video-studio 环境检查 ===")
    print(f"操作系统: {sys_name} ({platform.machine()})")

    if not args.check_only and args.install_deps:
        try_install_ffmpeg(sys_name)

    ffmpeg_path = check_command("ffmpeg")
    ffprobe_path = check_command("ffprobe")
    node_path = check_command("node")
    npm_path = check_command("npm")
    uv_path = check_command("uv")
    remotion_path = check_command("remotion")
    hyperframes_path = check_command("hyperframes")
    font_path = find_commercial_font() or "MISSING"

    print(f"ffmpeg      : {ffmpeg_path}")
    print(f"ffprobe     : {ffprobe_path}")
    print(f"node / npm  : {node_path} / {npm_path}")
    print(f"uv          : {uv_path}")
    print(f"remotion    : {remotion_path} (未安装时自动回退本地代码动画引擎)")
    print(f"hyperframes : {hyperframes_path} (未安装时自动回退本地代码动画引擎)")
    print(f"可商用字体  : {font_path}")

    active_py = ensure_venv_and_deps(
        install_deps=(not args.check_only and args.install_deps),
        install_heavy_ml=(not args.check_only and args.install_ml),
    )
    print(f"Python 运行环境: {active_py}")

    # 检查关键 Python 模块状态
    check_code = (
        "import importlib.util; "
        "pkgs=['PIL','numpy','soundfile','edge_tts','faster_whisper','librosa']; "
        "print(', '.join(f'{p}:OK' if importlib.util.find_spec(p) else f'{p}:fallback' for p in pkgs))"
    )
    res = subprocess.run([active_py, "-c", check_code], stdout=subprocess.PIPE, text=True)
    print(f"Python 依赖状态: {res.stdout.strip()}")

    if not args.check_only and args.install_node_tools and npm_path != "MISSING":
        print("[setup] 检查并安装全局 hyperframes / @remotion/cli ...")
        subprocess.run([npm_path, "install", "-g", "hyperframes"], check=False)

    if args.init_assets or (not args.check_only):
        subprocess.run([sys.executable, str(SCRIPTS_DIR / "generate_builtin_assets.py")], check=True)

    if ffmpeg_path == "MISSING" or ffprobe_path == "MISSING":
        print("\n[ERROR] 缺少 ffmpeg/ffprobe，请手动安装后重试。")
        sys.exit(1)

    print("\n[OK] 环境检查与初始化完成。")


if __name__ == "__main__":
    main()
