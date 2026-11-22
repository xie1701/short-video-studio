#!/usr/bin/env python3
"""Validate prerequisites (with China mirrors / bundled offline vendor packages) and deploy the skill."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


NAME = "short-video-studio"
SOURCE = Path(__file__).resolve().parent / NAME
if str(SOURCE / "scripts") not in sys.path:
    sys.path.insert(0, str(SOURCE / "scripts"))


def recognized_skill(path: Path) -> bool:
    skill_file = path / "SKILL.md"
    return skill_file.is_file() and bool(
        re.search(r"(?m)^name:\s*short-video-studio\s*$", skill_file.read_text(encoding="utf-8"))
    )


def deploy(source: Path, skills_dir: Path, copy: bool = False) -> Path | None:
    target = skills_dir / NAME
    if target.is_symlink() and target.resolve() == source.resolve():
        return None
    previous = target.exists() or target.is_symlink()
    broken_symlink = target.is_symlink() and not target.exists()
    if previous and not broken_symlink and not recognized_skill(target):
        raise ValueError(f"安装目标不是可识别的 {NAME}，请检查 {target}")

    skills_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{NAME}-stage-", dir=skills_dir) as directory:
        staged = Path(directory) / NAME
        if copy:
            shutil.copytree(source, staged, ignore=shutil.ignore_patterns("__pycache__", ".DS_Store", "*.pyc"))
        else:
            staged.symlink_to(source.resolve(), target_is_directory=True)
        if not recognized_skill(staged):
            raise ValueError(f"待安装 Skill 校验失败：{staged}")
        backup = None
        if broken_symlink:
            target.unlink(missing_ok=True)
        elif previous:
            rollback_parent = skills_dir.parent if os.access(skills_dir.parent, os.W_OK) else skills_dir
            backup_dir = Path(tempfile.mkdtemp(prefix=f".{NAME}-rollback-", dir=rollback_parent))
            backup = backup_dir / NAME
            target.rename(backup)
        try:
            staged.rename(target)
        except OSError:
            if backup:
                backup.rename(target)
            raise
    return backup


def configure_windows_workspace_if_needed(explicit_workspace: str | None = None, non_interactive: bool = False) -> None:
    """Windows 下避免将视频工作区默认塞在 C 盘：交互询问用户希望存放的非 C 盘目录并持久化配置。"""
    from common import list_non_system_drives_windows, load_user_config, save_user_config

    if explicit_workspace:
        ws = Path(explicit_workspace).expanduser().resolve()
        ws.mkdir(parents=True, exist_ok=True)
        save_user_config({"workspace_root": str(ws)})
        print(f"[setup] 已设置视频工作区根目录 -> {ws}")
        return

    if sys.platform != "win32":
        return
    cfg = load_user_config()
    if cfg.get("workspace_root"):
        return
    drives = list_non_system_drives_windows()
    if not drives:
        return
    rec = drives[0]["recommended_workspace"]
    if non_interactive or not sys.stdin.isatty():
        save_user_config({"workspace_root": rec})
        print(f"[setup] Windows 环境自动选用非 C 盘工作区 -> {rec}")
        return
    print("\n=== 选择视频加工工作区位置（避免占用 C 盘空间） ===")
    for idx, d in enumerate(drives, 1):
        print(f"  [{idx}] {d['recommended_workspace']}  (剩余 {d['free_gb']} GB)")
    ans = input(f"请输入编号或自定义路径（直接回车默认选 [1] {rec}）：").strip()
    if not ans or ans == "1":
        chosen = Path(rec)
    elif ans.isdigit() and 1 <= int(ans) <= len(drives):
        chosen = Path(drives[int(ans) - 1]["recommended_workspace"])
    else:
        chosen = Path(ans).expanduser()
    chosen.mkdir(parents=True, exist_ok=True)
    save_user_config({"workspace_root": str(chosen.resolve())})
    print(f"[setup] 已保存工作区路径 -> {chosen.resolve()}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="检查并一站式安装 short-video-studio 及其全部依赖")
    parser.add_argument("--skills-dir", type=Path, action="append")
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--skip-ml", action="store_true", help="跳过语音转写/节拍扩展依赖安装")
    parser.add_argument("--download-whisper-model", default="", help="可选：顺手通过国内镜像预拉取 Whisper 模型（如 small）")
    parser.add_argument("--workspace-root", default="", help="可选：持久化设置视频工作区根目录（Windows 强烈建议放非 C 盘）")
    parser.add_argument("--non-interactive", action="store_true", help="非交互模式（不等待终端输入）")
    args = parser.parse_args()
    if sys.version_info < (3, 10):
        parser.error("需要 Python 3.10+")
    if not recognized_skill(SOURCE):
        parser.error(f"未找到正确的 Skill：{SOURCE}")

    doctor = [sys.executable, str(SOURCE / "scripts" / "check_and_setup.py")]
    if args.check_only:
        raise SystemExit(subprocess.run([*doctor, "--check-only"]).returncode)

    configure_windows_workspace_if_needed(
        explicit_workspace=args.workspace_root or None,
        non_interactive=args.non_interactive,
    )

    gemini = Path.home() / ".gemini" / "config" / "skills"
    cola_skills = Path.home() / ".cola" / "skills"
    raw_destinations = list(args.skills_dir or [Path.home() / ".claude" / "skills"])
    if not args.skills_dir:
        if sys.platform != "win32" and gemini.is_dir():
            raw_destinations.append(gemini)
        if cola_skills.is_dir():
            raw_destinations.append(cola_skills)
    destinations = list(dict.fromkeys(d.expanduser().resolve() for d in raw_destinations))
    for directory in destinations:
        target = directory / NAME
        broken = target.is_symlink() and not target.exists()
        if (target.exists() or target.is_symlink()) and not broken and not recognized_skill(target):
            parser.error(f"安装目标无法识别：{target}")

    # 一站式安装所有依赖（优先包内离线 vendor/wheelhouse，否则走清华/阿里/淘宝国内高速镜像）
    install_cmd = [*doctor, "--install-deps", "--init-fonts", "--init-assets", "--init-browser"]
    if not args.skip_ml:
        install_cmd.append("--install-ml")
    if args.download_whisper_model:
        install_cmd.extend(["--download-whisper-model", args.download_whisper_model])
    subprocess.run(install_cmd, check=True)
    subprocess.run([*doctor, "--check-only"], check=True)

    for directory in destinations:
        backup = deploy(SOURCE, directory, copy=sys.platform == "win32")
        print(f"已安装 {directory / NAME}")
        if backup:
            print(f"可回滚版本 {backup}")


if __name__ == "__main__":
    main()
