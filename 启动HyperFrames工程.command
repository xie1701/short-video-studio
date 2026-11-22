#!/usr/bin/env bash
# ==============================================================================
# HyperFrames 工程一键双击启动器 (macOS)
# - 放到任意 HyperFrames 工程目录内双击：直接打开浏览器 Studio 实时预览与渲染
# - 在仓库根目录双击：支持弹窗选工程文件夹 / 一键套用「风格谱」新建示例工程
# ==============================================================================

set -u

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
if [ -d "${HOME}/.nvm/versions/node" ]; then
  NVM_LATEST="$(ls -1 "${HOME}/.nvm/versions/node" 2>/dev/null | sort -V | tail -n 1 || true)"
  if [ -n "${NVM_LATEST}" ] && [ -d "${HOME}/.nvm/versions/node/${NVM_LATEST}/bin" ]; then
    export PATH="${HOME}/.nvm/versions/node/${NVM_LATEST}/bin:${PATH}"
  fi
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

pause_on_exit() {
  echo ""
  read -r -p "按回车键关闭窗口..." _ || true
}

resolve_apply_script() {
  local cand
  for cand in \
    "${SCRIPT_DIR}/short-video-studio/scripts/apply_style_spectrum.py" \
    "${HOME}/.claude/skills/short-video-studio/scripts/apply_style_spectrum.py" \
    "${HOME}/.gemini/config/skills/short-video-studio/scripts/apply_style_spectrum.py"
  do
    if [ -f "${cand}" ]; then
      printf '%s\n' "${cand}"
      return 0
    fi
  done
  return 1
}

resolve_python_bin() {
  if [ -x "${HOME}/.short-video-studio/venv/bin/python" ]; then
    printf '%s\n' "${HOME}/.short-video-studio/venv/bin/python"
  elif command -v python3 >/dev/null 2>&1; then
    command -v python3
  else
    return 1
  fi
}

clean_pasted_path() {
  local raw="$1"
  # 去除首尾空白、成对引号与 Finder 拖入终端时产生的反斜杠转义空格
  raw="$(printf '%s' "${raw}" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
  raw="${raw#\"}"
  raw="${raw%\"}"
  raw="${raw#\'}"
  raw="${raw%\'}"
  raw="$(printf '%s' "${raw}" | sed 's/\\ / /g')"
  raw="${raw%/}"
  printf '%s\n' "${raw}"
}

check_environment() {
  echo "=== 检查 HyperFrames 运行环境 ==="
  if ! command -v node >/dev/null 2>&1; then
    echo "[ERROR] 未检测到 Node.js（HyperFrames 需要 Node.js 22 或更高版本）。"
    echo "请先在终端执行安装命令：brew install node ffmpeg"
    return 1
  fi
  local node_major
  node_major="$(node -p "process.versions.node.split('.')[0]" 2>/dev/null || echo 0)"
  if [ "${node_major}" -lt 22 ] 2>/dev/null; then
    echo "[ERROR] 当前 Node.js 版本过高要求未达标（当前 v$(node -v)，需要 >= v22）。"
    echo "请先升级 Node.js：brew upgrade node"
    return 1
  fi
  if ! command -v ffmpeg >/dev/null 2>&1; then
    echo "[WARN] 未检测到 ffmpeg：预览可打开，但渲染导出 MP4 时需先执行 brew install ffmpeg。"
  fi
  if [ -n "${SVS_HYPERFRAMES_CLI:-}" ]; then
    HF_CMD=("${SVS_HYPERFRAMES_CLI}")
  elif command -v hyperframes >/dev/null 2>&1; then
    HF_CMD=(hyperframes)
  elif command -v npx >/dev/null 2>&1; then
    HF_CMD=(npx --yes --prefer-offline hyperframes@0.9.72)
  else
    echo "[ERROR] 未检测到 npx，请重新安装 Node.js >= 22。"
    return 1
  fi
  echo "[OK] Node $(node -v) | 启动命令: ${HF_CMD[*]}"
  return 0
}

resolve_export_script() {
  local cand
  for cand in \
    "${SCRIPT_DIR}/short-video-studio/scripts/export_hyperframes.py" \
    "${HOME}/.claude/skills/short-video-studio/scripts/export_hyperframes.py" \
    "${HOME}/.gemini/config/skills/short-video-studio/scripts/export_hyperframes.py"
  do
    if [ -f "${cand}" ]; then
      printf '%s\n' "${cand}"
      return 0
    fi
  done
  return 1
}

resolve_pipeline_to_hf() {
  local dir="$1"
  local p_dir=""
  if [ -f "${dir}/hyperframes.json" ] &&
     [[ "$(<"${dir}/hyperframes.json")" == *hyperframes-local-v1* ]] &&
     [ -f "${dir}/../04_edl.json" ]; then
    p_dir="$(cd "${dir}/.." && pwd)"
  elif [ -f "${dir}/index.html" ]; then
    printf '%s\n' "${dir}"
    return 0
  fi
  if [ -f "${dir}/04_edl.json" ]; then
    p_dir="${dir}"
  elif [ -f "${dir}/pipeline/04_edl.json" ]; then
    p_dir="${dir}/pipeline"
  fi
  if [ -n "${p_dir}" ]; then
    local hf_dir="${p_dir}/hyperframes_draft"
    if [ ! -f "${hf_dir}/studio_export.json" ]; then
      local exp_script py_bin
      exp_script="$(resolve_export_script || true)"
      py_bin="$(resolve_python_bin || true)"
      if [ -n "${exp_script}" ] && [ -n "${py_bin}" ]; then
        echo ">>> 检测到剪辑流水线目录，正在自动生成可编辑 HyperFrames 工程..." >&2
        "${py_bin}" "${exp_script}" --pipeline-dir "${p_dir}" >&2 || return 1
      fi
    fi
    if [ -f "${hf_dir}/index.html" ]; then
      printf '%s\n' "${hf_dir}"
      return 0
    fi
  fi
  printf '%s\n' "${dir}"
  return 0
}

launch_preview() {
  local raw_dir="$1"
  shift || true
  if [ ! -d "${raw_dir}" ]; then
    echo "[ERROR] 目录不存在: ${raw_dir}"
    return 1
  fi
  local proj_dir
  proj_dir="$(resolve_pipeline_to_hf "${raw_dir}")" || return 1
  if [ ! -f "${proj_dir}/hyperframes.json" ] && [ ! -f "${proj_dir}/index.html" ]; then
    echo "[ERROR] 该目录中未找到 hyperframes.json、index.html 或流水线 04_edl.json："
    echo "        ${raw_dir}"
    return 1
  fi

  cd "${proj_dir}" || return 1
  echo ""
  echo ">>> 正在启动 HyperFrames Studio 工程："
  echo "    ${proj_dir}"
  echo ">>> 浏览器即将自动打开实时时间线窗口（按 Ctrl+C 可停止预览服务）。"
  echo ""
  "${HF_CMD[@]}" preview . "$@"
}

choose_existing_project() {
  local picked=""
  if command -v osascript >/dev/null 2>&1; then
    echo "请在弹出的 Finder 窗口中选择 HyperFrames 工程文件夹（如取消可改用手动拖入）..."
    picked="$(osascript -e 'POSIX path of (choose folder with prompt "请选择包含 index.html 或 hyperframes.json 的 HyperFrames 工程文件夹：")' 2>/dev/null || true)"
    picked="$(clean_pasted_path "${picked}")"
  fi
  if [ -z "${picked}" ]; then
    echo ""
    read -r -p "请把工程文件夹直接拖入此窗口后按回车: " picked
    picked="$(clean_pasted_path "${picked}")"
  fi
  if [ -z "${picked}" ]; then
    echo "[取消] 未选择任何工程目录。"
    return 1
  fi
  launch_preview "${picked}"
}

create_style_spectrum_project() {
  local apply_script py_bin style_id target_dir
  apply_script="$(resolve_apply_script || true)"
  py_bin="$(resolve_python_bin || true)"
  if [ -z "${apply_script}" ] || [ -z "${py_bin}" ]; then
    echo "[ERROR] 未找到 style_spectrum 导出脚本（apply_style_spectrum.py）或 python3，请选择 [1] 或 [3]。"
    return 1
  fi

  echo ""
  echo "常用风格推荐：style_14 (门店实景默认) / style_11 (个人知识默认) / style_21 (85%门店特写) / style_24 (100%纯画面无出镜)"
  read -r -p "请输入风格编号 [直接回车默认 style_14]: " style_id
  style_id="${style_id:-style_14}"

  local default_dir="${SCRIPT_DIR}/hyperframes_${style_id}"
  read -r -p "新工程保存路径 [直接回车默认 ${default_dir}]: " target_dir
  target_dir="$(clean_pasted_path "${target_dir:-${default_dir}}")"

  "${py_bin}" "${apply_script}" --style-id "${style_id}" --export-hyperframes "${target_dir}" || return 1
  launch_preview "${target_dir}"
}

create_blank_project() {
  local default_dir="${SCRIPT_DIR}/my_hyperframes_project"
  local target_dir
  echo ""
  read -r -p "新建空白竖屏(1080x1920)工程目录 [默认 ${default_dir}]: " target_dir
  target_dir="$(clean_pasted_path "${target_dir:-${default_dir}}")"
  if [ ! -f "${target_dir}/hyperframes.json" ] && [ ! -f "${target_dir}/index.html" ]; then
    mkdir -p "$(dirname "${target_dir}")"
    "${HF_CMD[@]}" init "${target_dir}" --resolution portrait --non-interactive || return 1
  fi
  launch_preview "${target_dir}"
}

main() {
  if ! check_environment; then
    pause_on_exit
    exit 1
  fi

  # 情况 1：命令行传参目录，直接打开（支持附带 --background/--no-open/--port 等透传参数）
  if [ $# -ge 1 ] && [ -n "${1:-}" ]; then
    local arg_dir
    arg_dir="$(clean_pasted_path "$1")"
    shift
    if ! launch_preview "${arg_dir}" "$@"; then
      pause_on_exit
      exit 1
    fi
    exit 0
  fi

  # 情况 2：本脚本就在工程目录内部，双击即开不弹窗
  if [ -f "${SCRIPT_DIR}/hyperframes.json" ] || [ -f "${SCRIPT_DIR}/index.html" ]; then
    if ! launch_preview "${SCRIPT_DIR}"; then
      pause_on_exit
      exit 1
    fi
    exit 0
  fi

  # 情况 3：在仓库根目录或父目录双击，提供清晰中文菜单
  echo ""
  echo "请选择启动方式（直接回车默认为 1）："
  echo "  [1] 打开已有 HyperFrames 工程（系统弹窗选文件夹 / 拖入目录）"
  if resolve_apply_script >/dev/null 2>&1; then
    echo "  [2] 一键用内置「24套风格」新建示例 HyperFrames 工程并启动"
  fi
  echo "  [3] 新建空白竖屏 (1080x1920) HyperFrames 工程并启动"
  echo ""
  read -r -p "请输入选项 [1/2/3]: " choice
  choice="${choice:-1}"

  local rc=0
  case "${choice}" in
    1) choose_existing_project || rc=$? ;;
    2) create_style_spectrum_project || rc=$? ;;
    3) create_blank_project || rc=$? ;;
    *)
      echo "[ERROR] 无效选项: ${choice}"
      rc=1
      ;;
  esac

  if [ "${rc}" -ne 0 ]; then
    pause_on_exit
    exit "${rc}"
  fi
}

main "$@"
