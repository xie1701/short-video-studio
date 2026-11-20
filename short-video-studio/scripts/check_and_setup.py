#!/usr/bin/env python3
"""
跨平台环境自检与一键安装脚本 (支持 macOS / Windows / Linux)：
检查并配置：
- ffmpeg & ffprobe
- Node.js / npm (可选 HyperFrames Studio 支持)
- Python 独立虚拟环境 (~/.short-video-studio/venv) 及依赖 (pillow, numpy, scipy, edge-tts, faster-whisper, librosa)
- 开源免费可商用中文字体校验 (--init-fonts)
- 初始化生成内置 CC0 资产 (--init-assets)
"""

from __future__ import annotations

import argparse
import platform
import subprocess
import sys
from pathlib import Path
from typing import Optional

SCRIPTS_ROOT = Path(__file__).resolve().parent
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from common import (
    ASSETS_DIR,
    SCRIPTS_DIR,
    VENV_DIR,
    find_bin,
    find_commercial_font,
    is_verified_commercial_font,
)

CORE_PIP_PKGS = ["pillow", "numpy", "scipy", "edge-tts"]
HEAVY_ML_PKGS = ["faster-whisper", "librosa"]


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


def ensure_venv_and_deps(install_deps: bool, install_heavy_ml: bool) -> tuple[str, bool]:
    py_in_venv = VENV_DIR / ("Scripts/python.exe" if platform.system() == "Windows" else "bin/python")
    install_ok = True
    if install_deps:
        if not py_in_venv.exists():
            uv = find_bin("uv")
            VENV_DIR.parent.mkdir(parents=True, exist_ok=True)
            if uv:
                print(f"[setup] 使用 uv 创建隔离环境 ({VENV_DIR})...")
                res = subprocess.run([uv, "venv", "--python", "3.12", str(VENV_DIR)], check=False)
                if res.returncode != 0:
                    subprocess.run([uv, "venv", str(VENV_DIR)], check=True)
            else:
                print(f"[setup] 使用 python venv 创建隔离环境 ({VENV_DIR})...")
                subprocess.run([sys.executable, "-m", "venv", str(VENV_DIR)], check=True)

        uv = find_bin("uv")

        def pip_install(pkgs: list[str], required: bool = True) -> bool:
            cmd = (
                [uv, "pip", "install", "--python", str(py_in_venv), *pkgs]
                if uv
                else [str(py_in_venv), "-m", "pip", "install", "--upgrade", *pkgs]
            )
            res = subprocess.run(cmd, check=False)
            if res.returncode != 0:
                tag = "[ERROR]" if required else "[WARN]"
                print(f"{tag} 依赖安装失败: {pkgs}")
                return False
            return True

        print(f"[setup] 安装核心音视频与 DSP 依赖: {', '.join(CORE_PIP_PKGS)} ...")
        if not pip_install(CORE_PIP_PKGS, required=True):
            install_ok = False
        if install_heavy_ml:
            print(f"[setup] 安装语音转写与节拍分析扩展包: {', '.join(HEAVY_ML_PKGS)} ...")
            if not pip_install(HEAVY_ML_PKGS, required=False):
                install_ok = False

    active_py = str(py_in_venv) if py_in_venv.exists() else sys.executable
    return active_py, install_ok


def check_and_sync_commercial_fonts(stage_into_assets: bool = False) -> tuple[Optional[str], bool]:
    """
    严格校验已核验的开源商用中文字体；
    当 stage_into_assets=True 时仅将最轻量的阿里妈妈数黑体 (1.0MB) 同步进 assets/fonts/ 作为跨平台随身商用兜底。
    """
    verified = find_commercial_font(verified_only=True)
    if verified and stage_into_assets:
        import shutil
        fonts_dir = ASSETS_DIR / "fonts"
        fonts_dir.mkdir(parents=True, exist_ok=True)
        light_cand = find_commercial_font(["AlimamaShuHeiTi-Bold.otf"], verified_only=True)
        if light_cand:
            dst = fonts_dir / Path(light_cand).name
            if not dst.exists():
                try:
                    shutil.copy2(light_cand, dst)
                except Exception:
                    pass
    return verified, bool(verified and is_verified_commercial_font(verified))


def main() -> None:
    parser = argparse.ArgumentParser(description="short-video-studio 环境检查与依赖安装")
    parser.add_argument("--check-only", action="store_true", help="仅检查当前环境，不执行安装")
    parser.add_argument("--init-assets", action="store_true", help="生成内置 CC0 BGM、音效与 3D LUT")
    parser.add_argument("--init-fonts", action="store_true", help="检测并归集已核验的免费商用中文字体到 assets/fonts/")
    parser.add_argument("--install-deps", action="store_true", help="创建 venv 并安装 Python 音视频依赖 (含 scipy)")
    parser.add_argument("--install-ml", action="store_true", help="额外安装 faster-whisper 与 librosa")
    parser.add_argument("--install-node-tools", action="store_true", help="检查并安装全局 HyperFrames CLI")
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
    hyperframes_path = check_command("hyperframes")
    font_path, font_verified = check_and_sync_commercial_fonts(
        stage_into_assets=(not args.check_only and args.init_fonts)
    )

    print(f"ffmpeg      : {ffmpeg_path}")
    print(f"ffprobe     : {ffprobe_path}")
    print(f"node / npm  : {node_path} / {npm_path}")
    print(f"uv          : {uv_path}")
    print(f"hyperframes : {hyperframes_path} (未安装时可导出静态 HTML Studio 工程)")
    if font_path and font_verified:
        print(f"可商用字体  : {font_path} [已验证免费/OFL商用]")
    else:
        print("可商用字体  : MISSING (请运行 --init-fonts 或安装思源黑体 / 阿里妈妈数黑体)")

    active_py, install_ok = ensure_venv_and_deps(
        install_deps=(not args.check_only and args.install_deps),
        install_heavy_ml=(not args.check_only and args.install_ml),
    )
    print(f"Python 运行环境: {active_py}")

    check_code = (
        "import importlib.util, json; "
        "pkgs=['PIL','numpy','scipy','edge_tts','faster_whisper','librosa']; "
        "res={p: bool(importlib.util.find_spec(p)) for p in pkgs}; "
        "print(json.dumps(res))"
    )
    res = subprocess.run([active_py, "-c", check_code], stdout=subprocess.PIPE, text=True)
    import json
    pkg_status = {}
    try:
        pkg_status = json.loads(res.stdout.strip() or "{}")
    except Exception:
        pkg_status = {}
    print(
        "Python 依赖状态: "
        + ", ".join(f"{k}:{'OK' if v else 'MISSING'}" for k, v in pkg_status.items())
    )

    core_missing = [p for p in ("PIL", "numpy", "scipy", "edge_tts") if not pkg_status.get(p)]

    if not args.check_only and args.install_node_tools and npm_path != "MISSING":
        print("[setup] 检查并安装全局 hyperframes ...")
        subprocess.run([npm_path, "install", "-g", "hyperframes"], check=False)

    if args.init_assets and not args.check_only:
        subprocess.run([active_py, str(SCRIPTS_DIR / "generate_builtin_assets.py")], check=True)

    if ffmpeg_path == "MISSING" or ffprobe_path == "MISSING":
        print("\n[ERROR] 缺少 ffmpeg/ffprobe，请手动安装后重试。")
        sys.exit(1)
    if not install_ok or core_missing:
        print(f"\n[ERROR] 核心 Python 依赖缺失 ({core_missing})，请运行 `--install-deps` 修复。")
        sys.exit(1)

    print("\n[OK] 环境检查与初始化完成。")


if __name__ == "__main__":
    main()
