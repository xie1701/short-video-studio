#!/usr/bin/env bash
set -euo pipefail

# short-video-studio 一键环境检查、依赖安装与复刻脚本 (macOS / Linux)

REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
SKILL_SRC="${REPO_ROOT}/short-video-studio"
TARGET_ROOT="${HOME}/.claude/skills"
TARGET_LINK="${TARGET_ROOT}/short-video-studio"

echo "=== 安装 short-video-studio Agent Skill ==="
echo "源码目录: ${SKILL_SRC}"

if [ ! -f "${SKILL_SRC}/SKILL.md" ]; then
  echo "[ERROR] 未找到 ${SKILL_SRC}/SKILL.md"
  exit 1
fi

# 1. 检测 python3
if ! command -v python3 >/dev/null 2>&1; then
  echo "[ERROR] 未检测到 python3，请先安装 Python 3.10+"
  exit 1
fi

# 2. 检测 ffmpeg（macOS 可自动用 brew 补齐）
if ! command -v ffmpeg >/dev/null 2>&1; then
  if [ "$(uname -s)" = "Darwin" ] && command -v brew >/dev/null 2>&1; then
    echo "[setup] 未检测到 ffmpeg，正在通过 Homebrew 安装..."
    brew install ffmpeg
  else
    echo "[WARN] 未检测到 ffmpeg，后续将尝试通过 Python 检查脚本提示安装。"
  fi
fi

# 3. 运行一键环境检查、隔离依赖安装与内置 CC0 资产初始化
python3 "${SKILL_SRC}/scripts/check_and_setup.py" --init-assets --install-deps

# 4. 复刻/软链部署到 Claude Code skills 目录 (~/.claude/skills/short-video-studio)
mkdir -p "${TARGET_ROOT}"
if [ -L "${TARGET_LINK}" ] || [ -e "${TARGET_LINK}" ]; then
  rm -rf "${TARGET_LINK}"
fi
ln -s "${SKILL_SRC}" "${TARGET_LINK}"

echo ""
echo "[OK] short-video-studio 已安装至: ${TARGET_LINK}"
echo "提示：重启或打开任意 Claude Code 会话即可直接调用 short-video-studio。"
