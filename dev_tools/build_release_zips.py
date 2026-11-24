#!/usr/bin/env python3
"""
构建区分 macOS (arm64) 与 Windows (x64) 的离线全依赖 Skill 分发包：
  - dist/short-video-studio-macos-arm64.zip
  - dist/short-video-studio-win-x64.zip

包内自包含结构：
  short-video-studio/
    ├── SKILL.md / scripts / references / assets / subskills
    └── vendor/
        ├── wheelhouse/       # 对应目标平台的全部 Python .whl（经清华/阿里国内源下载）
        ├── bin/              # 对应目标平台的 ffmpeg & ffprobe 静态二进制（若提供或本机可用）
        ├── whisper_models/   # 离线内置 faster-whisper-small 模型（可直接零网络语音转写）
        └── node_tools/       # 离线 playwright-core（可选）
  install_skill.py / install.sh / install.ps1
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_SRC = REPO_ROOT / "short-video-studio"
DIST_DIR = REPO_ROOT / "dist"

sys.path.insert(0, str(SKILL_SRC / "scripts"))
from common import HF_CHINA_MIRROR, NODE_TOOLS_DIR, PYPI_CHINA_MIRRORS, find_bin, get_huggingface_hub_dir

ALL_OFFLINE_PKGS = [
    "pillow",
    "numpy",
    "scipy",
    "edge-tts",
    "fonttools",
    # sherpa-onnx = 主转写引擎 SenseVoice 的运行时（快 20 倍以上、粤语自判）。
    # 它有平台专用 wheel（sherpa_onnx_core），所以必须按目标平台下载，不能通用。
    "sherpa-onnx",
    "faster-whisper",
    "librosa",
    "pypinyin",
]

PLATFORM_SPECS = {
    "macos-arm64": {
        "pip_platforms": ["macosx_11_0_arm64", "macosx_12_0_arm64", "macosx_13_0_arm64", "macosx_14_0_arm64"],
        "python_version": "3.12",
        "zip_name": "short-video-studio-macos-arm64.zip",
    },
    "win-x64": {
        "pip_platforms": ["win_amd64"],
        "python_version": "3.12",
        "zip_name": "short-video-studio-win-x64.zip",
    },
}


def _find_pip_cmd() -> List[str]:
    candidates: List[List[str]] = [[sys.executable, "-m", "pip"]]
    for b in ("pip3", "pip"):
        p = shutil.which(b)
        if p:
            candidates.append([p])
    for py in ("python3", "/opt/homebrew/bin/python3", "/usr/local/bin/python3"):
        p = shutil.which(py) if not py.startswith("/") else (py if Path(py).is_file() else None)
        if p:
            candidates.append([p, "-m", "pip"])
    for cand in candidates:
        res = subprocess.run([*cand, "--version"], capture_output=True, text=True, check=False)
        if res.returncode == 0:
            return cand
    return [sys.executable, "-m", "pip"]


def _download_wheels(target_key: str, wheel_dir: Path, include_ml: bool = True) -> int:
    spec = PLATFORM_SPECS[target_key]
    wheel_dir.mkdir(parents=True, exist_ok=True)
    pkgs = ALL_OFFLINE_PKGS if include_ml else ALL_OFFLINE_PKGS[:5]
    pip_cmd = _find_pip_cmd()

    for mirror in (*PYPI_CHINA_MIRRORS, ""):
        cmd: List[str] = [*pip_cmd, "download", "-d", str(wheel_dir)]
        if mirror:
            cmd.extend(["-i", mirror])
        # 始终显式锁定目标平台 + Python 3.12 预编译轮子，避免打包机本机 Python 版本过高/缺编译链导致回退源码编译失败
        cmd.extend(["--only-binary=:all:", "--python-version", spec["python_version"]])
        for plat in spec["pip_platforms"]:
            cmd.extend(["--platform", plat])
        cmd.extend(pkgs)
        print(f"[build-zip:{target_key}] 下载离线 wheels ({mirror or 'official PyPI'}) ...")
        res = subprocess.run(cmd, check=False)
        if res.returncode == 0:
            break
    whls = list(wheel_dir.glob("*.whl")) + list(wheel_dir.glob("*.tar.gz"))
    print(f"[build-zip:{target_key}] 已收集 {len(whls)} 个离线 Python 包 -> {wheel_dir}")
    return len(whls)


def _stage_whisper_model(dst_models_dir: Path, model_size: str = "small", allow_download: bool = True) -> bool:
    """将本地已缓存或国内 hf-mirror 下载的 faster-whisper 模型打进 vendor/whisper_models/<size>/。"""
    target_dir = dst_models_dir / model_size
    hub = get_huggingface_hub_dir()
    src_snap: Optional[Path] = None
    repo_dir = hub / f"models--Systran--faster-whisper-{model_size}"
    if (repo_dir / "snapshots").is_dir():
        snaps = sorted((repo_dir / "snapshots").iterdir(), reverse=True)
        for sp in snaps:
            if (sp / "model.bin").is_file():
                src_snap = sp
                break

    if src_snap is None and allow_download:
        print(f"[build-zip] 本地未缓存 faster-whisper-{model_size}，正从 {HF_CHINA_MIRROR} 拉取 ...")
        env = os.environ.copy()
        env["HF_ENDPOINT"] = HF_CHINA_MIRROR
        dl_code = (
            f"from faster_whisper import WhisperModel; "
            f"WhisperModel({model_size!r}, device='cpu', compute_type='int8')"
        )
        subprocess.run([sys.executable, "-c", dl_code], env=env, check=False)
        if (repo_dir / "snapshots").is_dir():
            for sp in sorted((repo_dir / "snapshots").iterdir(), reverse=True):
                if (sp / "model.bin").is_file():
                    src_snap = sp
                    break

    if src_snap is None:
        print(f"[WARN] 未找到 faster-whisper-{model_size} 本地快照，离线包将不含预置模型（首次运行时走 hf-mirror 下载）。")
        return False

    target_dir.mkdir(parents=True, exist_ok=True)
    for item in src_snap.iterdir():
        real_item = item.resolve()
        if real_item.is_file():
            shutil.copy2(real_item, target_dir / item.name)
    print(f"[build-zip] 已内置离线 Whisper 模型 ({model_size}) -> {target_dir}")
    return True


def _stage_sensevoice_model(dst_models_dir: Path) -> bool:
    """把 SenseVoice 模型（约 240 MB）打进离线包：model.int8.onnx + tokens.txt 就够跑。

    来源优先级：用户级目录 → Cola 自带（~/.coli/models）。
    找不到就明说，别让装包的人以为到手就能转写。
    """
    cands: List[Path] = []
    for root in (Path.home() / ".short-video-studio" / "models", Path.home() / ".coli" / "models"):
        if not root.is_dir():
            continue
        if (root / "model.int8.onnx").is_file() or (root / "model.onnx").is_file():
            cands.append(root)
        for sub in sorted(root.iterdir()):
            if sub.is_dir() and ((sub / "model.int8.onnx").is_file() or (sub / "model.onnx").is_file()):
                cands.append(sub)
    if not cands:
        print("[WARN] 本机没找到 SenseVoice 模型（~/.short-video-studio/models 与 ~/.coli/models 都没有），"
              "离线包将不含它 —— 转写会回落到 faster-whisper。")
        return False
    src = cands[0]
    dst = dst_models_dir / "sensevoice"
    dst.mkdir(parents=True, exist_ok=True)
    keep = ("model.int8.onnx", "model.onnx", "tokens.txt", "LICENSE", "README.md")
    total = 0
    for name in keep:
        f = src / name
        if f.is_file():
            shutil.copy2(f, dst / name)
            total += f.stat().st_size
    print(f"[build-zip] 已内置 SenseVoice 模型 -> {dst}（{total / 1e6:.0f} MB，来源 {src.name}）")
    return (dst / "tokens.txt").is_file() and any((dst / n).is_file() for n in ("model.int8.onnx", "model.onnx"))


def _stage_ffmpeg_bins(target_key: str, bin_dir: Path, custom_bin_dir: Optional[Path] = None) -> int:
    bin_dir.mkdir(parents=True, exist_ok=True)
    copied = 0
    ext = ".exe" if target_key.startswith("win") else ""
    if custom_bin_dir and custom_bin_dir.is_dir():
        for name in ("ffmpeg", "ffprobe"):
            src = custom_bin_dir / f"{name}{ext}"
            if src.is_file():
                shutil.copy2(src, bin_dir / src.name)
                copied += 1
        return copied

    if target_key == "macos-arm64" and platform.system() == "Darwin" and platform.machine() == "arm64":
        for name in ("ffmpeg", "ffprobe"):
            fb = find_bin(name)
            if fb and Path(fb).is_file():
                # 只收「真能脱离这台机器跑」的二进制。实测：Homebrew 的 ffmpeg 只有 426 KB，
                # otool -L 全是 /opt/homebrew/Cellar/... —— 打进包里等于只在这台机器上能跑。
                if _is_dynamic_homebrew_bin(Path(fb).resolve()):
                    print(f"[build-zip] 跳过 {name}：这是 Homebrew 动态链接版，"
                          f"换台机器会 dyld 失败。要内置请用静态构建（--ffmpeg-bin-dir 指定）")
                    continue
                shutil.copy2(Path(fb).resolve(), bin_dir / name)
                copied += 1
    return copied


def _is_dynamic_homebrew_bin(path: Path) -> bool:
    """判断一个 mac 二进制是否依赖 /opt/homebrew 或 /usr/local 的 dylib。

    非 Darwin 或无 otool 时按「小体积 + 无内置依赖」的保守规则返回 False（交给上层决定）。
    """
    if platform.system() != "Darwin":
        return False
    try:
        out = subprocess.run(["otool", "-L", str(path)], capture_output=True, text=True, timeout=30).stdout
    except Exception:
        return False
    return any(("/opt/homebrew/" in ln) or ("/usr/local/Cellar/" in ln) for ln in out.splitlines())


def build_target_zip(
    target_key: str,
    out_dir: Path,
    include_wheels: bool = True,
    include_ml: bool = True,
    whisper_model: str = "small",
    ffmpeg_bin_dir: Optional[Path] = None,
    include_node_tools: bool = True,
) -> Path:
    spec = PLATFORM_SPECS[target_key]
    out_dir.mkdir(parents=True, exist_ok=True)
    out_zip = out_dir / spec["zip_name"]

    with tempfile.TemporaryDirectory(prefix=f"svs-pkg-{target_key}-") as td:
        stage_root = Path(td)
        staged_skill = stage_root / "short-video-studio"
        shutil.copytree(
            SKILL_SRC,
            staged_skill,
            ignore=shutil.ignore_patterns("__pycache__", ".DS_Store", "*.pyc", "vendor"),
        )
        for root_file in ("install_skill.py", "install.sh", "install.ps1", "README.md"):
            rf = REPO_ROOT / root_file
            if rf.is_file():
                shutil.copy2(rf, stage_root / root_file)

        vendor_dir = staged_skill / "vendor"
        vendor_dir.mkdir(parents=True, exist_ok=True)

        if include_wheels:
            _download_wheels(target_key, vendor_dir / "wheelhouse", include_ml=include_ml)
        if whisper_model:
            _stage_whisper_model(vendor_dir / "whisper_models", model_size=whisper_model, allow_download=True)
        _stage_sensevoice_model(vendor_dir / "models")
        _stage_ffmpeg_bins(target_key, vendor_dir / "bin", custom_bin_dir=ffmpeg_bin_dir)
        if include_node_tools and (NODE_TOOLS_DIR / "node_modules" / "playwright-core").is_dir():
            shutil.copytree(
                NODE_TOOLS_DIR,
                vendor_dir / "node_tools",
                ignore=shutil.ignore_patterns(".DS_Store"),
            )

        if out_zip.exists():
            out_zip.unlink()
        print(f"[build-zip:{target_key}] 正在压缩打包 -> {out_zip} ...")
        with zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for file_path in sorted(stage_root.rglob("*")):
                if file_path.is_file():
                    zf.write(file_path, arcname=str(file_path.relative_to(stage_root)))

    mb = out_zip.stat().st_size / (1024 * 1024)
    print(f"[build-zip:{target_key}] 完成 -> {out_zip} ({mb:.1f} MB)")
    return out_zip


def main() -> None:
    parser = argparse.ArgumentParser(description="构建区分 macOS 与 Windows 的离线全依赖 Skill ZIP 包")
    parser.add_argument("--targets", nargs="+", choices=list(PLATFORM_SPECS.keys()), default=list(PLATFORM_SPECS.keys()))
    parser.add_argument("--out-dir", type=Path, default=DIST_DIR)
    parser.add_argument("--whisper-model", default="small", help="内置离线 Whisper 模型（传空字符串跳过）")
    parser.add_argument("--mac-ffmpeg-dir", type=Path, default=None, help="可选：指定静态 macOS ffmpeg/ffprobe 目录")
    parser.add_argument("--win-ffmpeg-dir", type=Path, default=None, help="可选：指定 Windows ffmpeg.exe/ffprobe.exe 目录")
    parser.add_argument("--no-wheels", action="store_true", help="跳过下载 Python wheels（仅测试打包结构）")
    parser.add_argument("--no-ml", action="store_true", help="仅打包核心 Python wheels（不含 faster-whisper/librosa）")
    args = parser.parse_args()

    for tk in args.targets:
        ff_dir = args.mac_ffmpeg_dir if tk == "macos-arm64" else args.win_ffmpeg_dir
        build_target_zip(
            tk,
            out_dir=args.out_dir,
            include_wheels=not args.no_wheels,
            include_ml=not args.no_ml,
            whisper_model=args.whisper_model,
            ffmpeg_bin_dir=ff_dir,
        )


if __name__ == "__main__":
    main()
