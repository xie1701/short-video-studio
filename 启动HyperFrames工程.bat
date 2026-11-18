@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion

REM ==============================================================================
REM HyperFrames 工程一键双击启动器 (Windows)
REM - 放到任意 HyperFrames 工程目录内双击：直接启动浏览器 Studio 预览
REM - 在仓库根目录双击：支持选择工程目录 / 用风格谱新建工程 / 新建空白工程
REM ==============================================================================

set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

echo === 检查 HyperFrames 运行环境 ===
where node >nul 2>nul
if errorlevel 1 (
  echo [ERROR] 未检测到 Node.js（需要 Node.js 22 或更高版本）。
  echo 请先前往 https://nodejs.org/ 安装 Node.js 22+ 与 FFmpeg。
  pause
  exit /b 1
)

where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo [WARN] 未检测到 ffmpeg：预览可开启，导出 MP4 时建议先安装 ffmpeg（winget install Gyan.FFmpeg）。
)

set "HF_RUNNER=npx --yes hyperframes"
where hyperframes >nul 2>nul
if not errorlevel 1 set "HF_RUNNER=hyperframes"

if not "%~1"=="" (
  set "TARGET=%~1"
  goto :launch_preview
)

if exist "%SCRIPT_DIR%\hyperframes.json" (
  set "TARGET=%SCRIPT_DIR%"
  goto :launch_preview
)
if exist "%SCRIPT_DIR%\index.html" (
  set "TARGET=%SCRIPT_DIR%"
  goto :launch_preview
)

echo.
echo 请选择启动方式（直接回车默认为 1）：
echo   [1] 打开已有 HyperFrames 工程（直接输入或把文件夹拖进本窗口）
echo   [2] 一键用内置「20套风格谱」新建示例 HyperFrames 工程并启动
echo   [3] 新建空白竖屏 (1080x1920) HyperFrames 工程并启动
echo.
set "CHOICE="
set /p "CHOICE=请输入选项 [1/2/3]: "
if "%CHOICE%"=="" set "CHOICE=1"

if "%CHOICE%"=="1" (
  set "TARGET="
  set /p "TARGET=请把工程文件夹拖入本窗口并按回车: "
  set "TARGET=!TARGET:"=!"
  goto :launch_preview
)

if "%CHOICE%"=="2" (
  set "STYLE_ID="
  set /p "STYLE_ID=请输入风格编号 [直接回车默认 style_14]: "
  if "!STYLE_ID!"=="" set "STYLE_ID=style_14"
  set "TARGET=%SCRIPT_DIR%\hyperframes_!STYLE_ID!"
  set "APPLY_PY=%SCRIPT_DIR%\short-video-studio\scripts\apply_style_spectrum.py"
  if not exist "!APPLY_PY!" set "APPLY_PY=%USERPROFILE%\.claude\skills\short-video-studio\scripts\apply_style_spectrum.py"
  if not exist "!APPLY_PY!" (
    echo [ERROR] 未找到 apply_style_spectrum.py，请选择 [1] 或 [3]。
    pause
    exit /b 1
  )
  python "!APPLY_PY!" --style-id "!STYLE_ID!" --export-hyperframes "!TARGET!"
  if errorlevel 1 (
    pause
    exit /b 1
  )
  goto :launch_preview
)

if "%CHOICE%"=="3" (
  set "TARGET=%SCRIPT_DIR%\my_hyperframes_project"
  set /p "TARGET_IN=新工程保存路径 [回车默认 %SCRIPT_DIR%\my_hyperframes_project]: "
  if not "!TARGET_IN!"=="" set "TARGET=!TARGET_IN:"=!"
  if not exist "!TARGET!\hyperframes.json" if not exist "!TARGET!\index.html" (
    call %HF_RUNNER% init "!TARGET!" --resolution portrait --non-interactive
    if errorlevel 1 (
      pause
      exit /b 1
    )
  )
  goto :launch_preview
)

echo [ERROR] 无效选项: %CHOICE%
pause
exit /b 1

:launch_preview
if not exist "%TARGET%" (
  echo [ERROR] 目录不存在: %TARGET%
  pause
  exit /b 1
)
if not exist "%TARGET%\hyperframes.json" if not exist "%TARGET%\index.html" (
  echo [ERROR] 目录内缺少 hyperframes.json 或 index.html: %TARGET%
  pause
  exit /b 1
)

cd /d "%TARGET%"
echo.
echo ^>^>^> 正在启动 HyperFrames Studio 工程：%TARGET%
echo ^>^>^> 浏览器即将自动打开实时预览界面（关闭窗口或 Ctrl+C 可停止预览）。
echo.
call %HF_RUNNER% preview
if errorlevel 1 pause
