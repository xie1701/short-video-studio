#!/usr/bin/env python3
"""Validate prerequisites and deploy the skill with a recoverable previous installation."""

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


def main() -> None:
    parser = argparse.ArgumentParser(description="检查并安装 short-video-studio")
    parser.add_argument("--skills-dir", type=Path, action="append")
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    if sys.version_info < (3, 10):
        parser.error("需要 Python 3.10+")
    if not recognized_skill(SOURCE):
        parser.error(f"未找到正确的 Skill：{SOURCE}")

    doctor = [sys.executable, str(SOURCE / "scripts" / "check_and_setup.py")]
    if args.check_only:
        raise SystemExit(subprocess.run([*doctor, "--check-only"]).returncode)

    gemini = Path.home() / ".gemini" / "config" / "skills"
    raw_destinations = list(args.skills_dir or [Path.home() / ".claude" / "skills"])
    if not args.skills_dir and sys.platform != "win32" and gemini.is_dir():
        raw_destinations.append(gemini)
    destinations = list(dict.fromkeys(d.expanduser().resolve() for d in raw_destinations))
    for directory in destinations:
        target = directory / NAME
        broken = target.is_symlink() and not target.exists()
        if (target.exists() or target.is_symlink()) and not broken and not recognized_skill(target):
            parser.error(f"安装目标无法识别：{target}")

    result = subprocess.run([*doctor, "--check-only"])
    if result.returncode:
        subprocess.run([*doctor, "--install-deps", "--init-assets"], check=True)
        subprocess.run([*doctor, "--check-only"], check=True)

    for directory in destinations:
        backup = deploy(SOURCE, directory, copy=sys.platform == "win32")
        print(f"已安装 {directory / NAME}")
        if backup:
            print(f"可回滚版本 {backup}")


if __name__ == "__main__":
    main()
