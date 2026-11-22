#!/usr/bin/env python3
"""
跨平台环境自检与一键安装脚本 (支持 macOS / Windows / Linux)：
1. 优先识别随离线分发包内置的 vendor/wheelhouse、vendor/bin、vendor/node_tools 与 vendor/whisper_models，零网络秒装。
2. 在线安装默认启用国内高速镜像源（清华/阿里 PyPI、淘宝 npmmirror、Homebrew 国内源、hf-mirror.com），失败自动回退官方源。
3. 一次性检查并安装：
   - ffmpeg & ffprobe
   - Python 隔离虚拟环境 (~/.short-video-studio/venv) 及核心/语音依赖 (pillow, numpy, scipy, edge-tts, fonttools, faster-whisper, librosa)
   - Node.js / playwright-core 无头渲染桥
   - 随仓开源免费可商用中文字体、CC0 音库、24 套剪映级视觉模板与 3D LUT 完整性
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

SCRIPTS_ROOT = Path(__file__).resolve().parent
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from common import (
    ASSETS_DIR,
    HF_CHINA_MIRROR,
    HYPERFRAMES_VERSION,
    NODE_TOOLS_DIR,
    NPM_CHINA_MIRROR,
    PYPI_CHINA_MIRRORS,
    VENDOR_DIR,
    VENV_DIR,
    detect_active_agent,
    detect_browser_renderer,
    find_bin,
    find_cached_whisper_model,
    find_commercial_font,
    is_verified_commercial_font,
    list_non_system_drives_windows,
    read_json,
    resolve_bundled_whisper_model,
    resolve_default_workspace_root,
)

CORE_PIP_PKGS = ["pillow", "numpy", "scipy", "edge-tts", "fonttools"]
HEAVY_ML_PKGS = ["faster-whisper", "librosa"]


def check_command(cmd: str) -> str:
    found = find_bin(cmd)
    return found if found else "MISSING"


def try_install_ffmpeg(sys_name: str) -> None:
    if find_bin("ffmpeg") and find_bin("ffprobe"):
        return
    print("[setup] 未检测到 ffmpeg，尝试通过国内高速源自动安装...")
    if sys_name == "Darwin" and find_bin("brew"):
        env = os.environ.copy()
        env.setdefault("HOMEBREW_API_DOMAIN", "https://mirrors.tuna.tsinghua.edu.cn/homebrew-bottles/api")
        env.setdefault("HOMEBREW_BOTTLE_DOMAIN", "https://mirrors.tuna.tsinghua.edu.cn/homebrew-bottles")
        subprocess.run(["brew", "install", "ffmpeg"], check=False, env=env)
    elif sys_name == "Windows" and find_bin("winget"):
        subprocess.run(
            ["winget", "install", "Gyan.FFmpeg", "--silent", "--accept-package-agreements", "--accept-source-agreements"],
            check=False,
        )
    elif sys_name == "Linux" and find_bin("apt-get"):
        subprocess.run(["sudo", "apt-get", "update"], check=False)
        subprocess.run(["sudo", "apt-get", "install", "-y", "ffmpeg"], check=False)


def _run_pip_install(
    py_in_venv: Path,
    pkgs: list[str],
    uv_bin: Optional[str],
    wheelhouse: Optional[Path],
    required: bool = True,
) -> bool:
    """优先使用离线 wheelhouse 秒装；无离线包或部分缺失时自动切清华/阿里国内 PyPI 镜像，最终回退官方源。"""
    if wheelhouse and wheelhouse.is_dir() and any(wheelhouse.glob("*.whl")):
        print(f"[setup] 检测到本地离线依赖包 ({wheelhouse})，优先离线极速安装: {', '.join(pkgs)} ...")
        cmd_off = (
            [uv_bin, "pip", "install", "--python", str(py_in_venv), "--no-index", "--find-links", str(wheelhouse), *pkgs]
            if uv_bin
            else [str(py_in_venv), "-m", "pip", "install", "--no-index", "--find-links", str(wheelhouse), *pkgs]
        )
        res_off = subprocess.run(cmd_off, check=False)
        if res_off.returncode == 0:
            return True
        print("[setup] 离线包未覆盖全部子依赖，自动切换国内镜像补齐...")

    index_candidates = [*PYPI_CHINA_MIRRORS, ""]  # 末尾空串代表官方 PyPI 兜底
    for idx_url in index_candidates:
        if uv_bin:
            cmd = [uv_bin, "pip", "install", "--python", str(py_in_venv)]
            if idx_url:
                cmd.extend(["--index-url", idx_url])
            cmd.extend(pkgs)
        else:
            cmd = [str(py_in_venv), "-m", "pip", "install", "--upgrade"]
            if idx_url:
                cmd.extend(["-i", idx_url])
            cmd.extend(pkgs)
        label = idx_url or "官方 PyPI"
        print(f"[setup] 正在从 {label} 安装: {', '.join(pkgs)} ...")
        res = subprocess.run(cmd, check=False)
        if res.returncode == 0:
            return True
    tag = "[ERROR]" if required else "[WARN]"
    print(f"{tag} 依赖安装失败: {pkgs}")
    return False


def ensure_venv_and_deps(install_deps: bool, install_heavy_ml: bool) -> tuple[str, bool]:
    py_in_venv = VENV_DIR / ("Scripts/python.exe" if platform.system() == "Windows" else "bin/python")
    install_ok = True
    wheelhouse = VENDOR_DIR / "wheelhouse"
    if install_deps or install_heavy_ml:
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
        if install_deps:
            if not _run_pip_install(py_in_venv, CORE_PIP_PKGS, uv, wheelhouse, required=True):
                install_ok = False
        if install_heavy_ml:
            if not _run_pip_install(py_in_venv, HEAVY_ML_PKGS, uv, wheelhouse, required=False):
                print("[WARN] 语音转写/节拍扩展包安装失败，将由内置静音切分兜底，不影响核心导出。")

    active_py = str(py_in_venv) if py_in_venv.exists() else sys.executable
    return active_py, install_ok


def ensure_whisper_model_ready(active_py: str, model_size: str = "small") -> bool:
    """确保至少有一份本地 Whisper 语音模型就绪（离线包内置 > 本机缓存 > hf-mirror 国内高速预拉取）。"""
    bundled = resolve_bundled_whisper_model(model_size)
    if bundled is not None:
        print(f"[setup] 已检测到离线内置 Whisper 模型 ({model_size}): {bundled}")
        return True
    cached = find_cached_whisper_model()
    if cached:
        print(f"[setup] 已检测到本机缓存 Whisper 模型: {cached}")
        return True
    print(f"[setup] 正在通过国内镜像 ({HF_CHINA_MIRROR}) 预下载语音识别模型 faster-whisper-{model_size} ...")
    env = os.environ.copy()
    env.setdefault("HF_ENDPOINT", HF_CHINA_MIRROR)
    dl_code = (
        f"from faster_whisper import WhisperModel; "
        f"WhisperModel({model_size!r}, device='cpu', compute_type='int8'); "
        f"print('WHISPER_READY')"
    )
    res = subprocess.run([active_py, "-c", dl_code], env=env, check=False)
    return res.returncode == 0


def check_and_sync_commercial_fonts(stage_into_assets: bool = False) -> tuple[Optional[str], bool]:
    """严格校验已核验的开源商用中文字体。"""
    verified = find_commercial_font(verified_only=True)
    if verified and stage_into_assets:
        fonts_dir = ASSETS_DIR / "fonts"
        fonts_dir.mkdir(parents=True, exist_ok=True)
        for cand_name in (
            "AlimamaShuHeiTi-Bold.otf",
            "SmileySans-Oblique.ttf",
            "SourceHanSerifCN-Bold.otf",
            "SourceHanSerifCN-Heavy.otf",
        ):
            cand = find_commercial_font([cand_name], verified_only=True, allow_fallback=False)
            if cand:
                dst = fonts_dir / Path(cand).name
                if not dst.exists():
                    try:
                        shutil.copy2(cand, dst)
                    except Exception:
                        pass
    return verified, bool(verified and is_verified_commercial_font(verified))


def main() -> None:
    parser = argparse.ArgumentParser(description="short-video-studio 环境检查与国内高速/离线依赖安装")
    parser.add_argument("--check-only", action="store_true", help="仅检查当前环境，不执行安装")
    parser.add_argument("--init-assets", action="store_true", help="校验随仓内置 CC0 真录音库、24 套视觉模板与 3D LUT 是否完整")
    parser.add_argument("--init-fonts", action="store_true", help="检测并归集已核验的免费商用中文字体到 assets/fonts/")
    parser.add_argument("--install-deps", action="store_true", help="创建 venv 并优先离线/国内镜像安装核心 Python 依赖")
    parser.add_argument("--install-ml", action="store_true", help="额外安装 faster-whisper 与 librosa")
    parser.add_argument("--download-whisper-model", default="", help="可选：通过 hf-mirror 预下载指定 Whisper 模型（如 small）")
    parser.add_argument("--install-node-tools", action="store_true", help="通过淘宝 npmmirror 检查并安装全局 HyperFrames CLI")
    parser.add_argument("--init-browser", action="store_true", help="准备轻量 playwright-core 无头渲染桥（优先离线包，否则走淘宝源）")
    args = parser.parse_args()

    sys_name = platform.system()
    ag_info = detect_active_agent()
    print("=== short-video-studio 环境检查 ===")
    print(f"操作系统    : {sys_name} ({platform.machine()})")
    print(f"当前智能体  : {ag_info['agent']} ({ag_info['signal']})")
    print(f"默认工作区  : {resolve_default_workspace_root()}")
    if sys_name == "Windows":
        non_c = list_non_system_drives_windows()
        if non_c and str(resolve_default_workspace_root()).upper().startswith("C:"):
            print(f"[提示] 检测到可用非 C 盘: {', '.join(d['drive'] + f'({d[\"free_gb\"]}GB)' for d in non_c)}，建议将工作区设在非 C 盘（如 {non_c[0]['recommended_workspace']}）。")

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
        print("可商用字体  : MISSING (请运行 --init-fonts 或安装随仓开源商用字体)")

    active_py, install_ok = ensure_venv_and_deps(
        install_deps=(not args.check_only and args.install_deps),
        install_heavy_ml=(not args.check_only and args.install_ml),
    )
    print(f"Python 环境 : {active_py}")

    check_code = (
        "import importlib.util, json; "
        "pkgs=['PIL','numpy','scipy','edge_tts','fontTools','faster_whisper','librosa']; "
        "res={p: bool(importlib.util.find_spec(p)) for p in pkgs}; "
        "print(json.dumps(res))"
    )
    res = subprocess.run([active_py, "-c", check_code], stdout=subprocess.PIPE, text=True)
    pkg_status = {}
    try:
        pkg_status = json.loads(res.stdout.strip() or "{}")
    except Exception:
        pkg_status = {}
    print(
        "Python 依赖 : "
        + ", ".join(f"{k}:{'OK' if v else 'MISSING'}" for k, v in pkg_status.items())
    )

    if not args.check_only and args.download_whisper_model and pkg_status.get("faster_whisper"):
        ensure_whisper_model_ready(active_py, model_size=args.download_whisper_model)

    cached_wm = find_cached_whisper_model()
    print(f"Whisper模型 : {cached_wm or '未预置（首次转写将走 hf-mirror 国内镜像自动拉取）'}")

    core_missing = [p for p in ("PIL", "numpy", "scipy", "edge_tts") if not pkg_status.get(p)]

    if not args.check_only and args.install_node_tools and npm_path != "MISSING":
        print(f"[setup] 通过淘宝镜像 ({NPM_CHINA_MIRROR}) 安装全局 hyperframes@{HYPERFRAMES_VERSION} ...")
        hf_res = subprocess.run(
            [npm_path, "install", "-g", f"--registry={NPM_CHINA_MIRROR}", f"hyperframes@{HYPERFRAMES_VERSION}"],
            check=False,
        )
        if hf_res.returncode != 0:
            print(f"[WARN] hyperframes@{HYPERFRAMES_VERSION} 安装失败（退出码 {hf_res.returncode}），稍后可用 npx 运行。")

    if not args.check_only and args.init_browser:
        vendor_pw = VENDOR_DIR / "node_tools" / "node_modules" / "playwright-core"
        if vendor_pw.is_dir():
            print(f"[setup] 已检测到离线内置 playwright-core: {vendor_pw}")
        elif npm_path == "MISSING" or node_path == "MISSING":
            print("[WARN] 缺少 node/npm，跳过 playwright-core 初始化（动态叠层将自动回退 Pillow）。")
        else:
            NODE_TOOLS_DIR.mkdir(parents=True, exist_ok=True)
            pkg_p = NODE_TOOLS_DIR / "package.json"
            if not pkg_p.exists():
                pkg_p.write_text('{"name":"svs-node-tools","private":true,"version":"1.0.0"}\n', encoding="utf-8")
            print(f"[setup] 正在通过淘宝镜像 ({NPM_CHINA_MIRROR}) 安装 playwright-core 到 {NODE_TOOLS_DIR} ...")
            res_pw = subprocess.run(
                [npm_path, "install", f"--registry={NPM_CHINA_MIRROR}", "--no-audit", "--no-fund", "playwright-core"],
                cwd=str(NODE_TOOLS_DIR),
                check=False,
            )
            if res_pw.returncode != 0:
                subprocess.run(
                    [npm_path, "install", "--no-audit", "--no-fund", "playwright-core"],
                    cwd=str(NODE_TOOLS_DIR),
                    check=False,
                )

    br_info = detect_browser_renderer()
    print(
        f"HTML 渲染桥 : {'OK (' + Path(str(br_info['chrome_bin'])).name + ')' if br_info['available'] else '未就绪'}"
        + ("" if br_info["available"] else f" (node={bool(br_info['node_bin'])}, pw={bool(br_info['playwright_core'])}, chrome={bool(br_info['chrome_bin'])})")
    )
    if args.init_browser and not br_info["chrome_bin"] and br_info["playwright_core"]:
        print("[WARN] 未在本机发现 Chrome/Edge/Chromium，可手动执行 `npx playwright-core install chromium` 补全浏览器。")

    if args.init_assets and not args.check_only:
        vibe_mf = ASSETS_DIR / "sound_vibe" / "vibe_sound_manifest.json"
        if not vibe_mf.is_file():
            print(f"[ERROR] 缺少 CC0 真录音库清单: {vibe_mf}")
            sys.exit(1)
        lib_path = ASSETS_DIR / "style_spectrum" / "style_spectrum_library.json"
        tpl_path = ASSETS_DIR / "subtitle_templates" / "templates.json"
        if not lib_path.is_file() or not tpl_path.is_file():
            print(f"[ERROR] 缺少核心样式库或字幕模板: {lib_path} / {tpl_path}")
            sys.exit(1)
        lib_data = read_json(lib_path)
        styles = lib_data.get("styles") or []
        missing_luts = [
            f"{s['style_id']}.cube"
            for s in styles
            if not (ASSETS_DIR / "style_spectrum" / "luts" / f"{s['style_id']}.cube").is_file()
        ]
        if missing_luts:
            print(f"[ERROR] 缺少风格 LUT 文件: {missing_luts}")
            sys.exit(1)
        lut_cnt = len(list((ASSETS_DIR / "style_spectrum" / "luts").glob("*.cube")))
        print(f"[setup] 已校验 CC0 真录音库 ({vibe_mf.name})、{len(styles)} 套视觉模板配置与 {lut_cnt} 套 3D LUT 资产完整可用。")

    if ffmpeg_path == "MISSING" or ffprobe_path == "MISSING":
        print("\n[ERROR] 缺少 ffmpeg/ffprobe，请手动安装后重试。")
        sys.exit(1)
    if not install_ok or core_missing:
        print(f"\n[ERROR] 核心 Python 依赖缺失 ({core_missing})，请运行 `--install-deps` 修复。")
        sys.exit(1)

    print("\n[OK] 环境检查与初始化完成。")


if __name__ == "__main__":
    main()
