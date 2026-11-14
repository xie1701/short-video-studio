# short-video-studio 一键环境检查、依赖安装与复刻脚本 (Windows PowerShell)
$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$SkillSrc = Join-Path $RepoRoot "short-video-studio"
$TargetRoot = Join-Path $env:USERPROFILE ".claude\skills"
$TargetDir = Join-Path $TargetRoot "short-video-studio"

Write-Host "=== 安装 short-video-studio Agent Skill ==="
if (-not (Test-Path (Join-Path $SkillSrc "SKILL.md"))) {
    Write-Error "未找到 $SkillSrc\SKILL.md"
    exit 1
}

# 1. 检测 Python
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { $py = Get-Command python3 -ErrorAction SilentlyContinue }
if (-not $py) {
    Write-Error "未检测到 Python，请先安装 Python 3.10+ (winget install Python.Python.3.12)"
    exit 1
}

# 2. 运行环境检查、依赖安装与内置资产初始化
& $py.Source (Join-Path $SkillSrc "scripts\check_and_setup.py") --init-assets --install-deps

# 3. 复制部署到 %USERPROFILE%\.claude\skills\short-video-studio
New-Item -ItemType Directory -Force -Path $TargetRoot | Out-Null
if (Test-Path $TargetDir) {
    Remove-Item -Recurse -Force $TargetDir
}
Copy-Item -Recurse -Force $SkillSrc $TargetDir

Write-Host ""
Write-Host "[OK] short-video-studio 已安装至: $TargetDir"
Write-Host "提示：重启或打开任意 Claude Code 会话即可直接调用 short-video-studio。"
