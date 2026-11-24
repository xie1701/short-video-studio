# 跨平台可运行性审计（intel mac / windows x86+arm / linux）

> 2026-11-23 全量只读审计。问题原话：「这套 skill 是否能运行在 intel 版的 mac、x86 或 arm 版的 windows？
> 还有 linux 下是否可以？目标是可以通用嘛，看看还缺什么。」
>
> 结论一句话：**渲染链路（Pillow 逐帧 PNG + `eq/curves/lut3d/xfade`）全平台可移植**，这是最大的资产；
> 会直接崩的只有四件事，其余是静默降级。脚本里没有任何架构分支，能不能跑由**外部二进制**决定。
>
> 本文件是原始审计证据；`scripts/platform_probe.py` 是它的可执行版（`python3 scripts/platform_probe.py`）。

# short-video-studio 跨平台可运行性审计

根目录：`/Users/jerry/Downloads/AIworks/shortVideo-CCstudio/`。下文路径均相对该根（`short-video-studio/scripts/...`）。
只读核查；在本机跑了 `check_and_setup.py --check-only`（未安装、未写文件），确认基线为 Darwin arm64 + Python 3.12 venv + Homebrew ffmpeg 9.0.2 + Xcode CLT。

先给三条会改变结论的硬事实：

1. **分发包 `dist/short-video-studio-macos-arm64.zip` 里的 ffmpeg/ffprobe 不是静态二进制**：`file` = `Mach-O 64-bit executable arm64`（426 KB），`otool -L` 全是 `/opt/homebrew/Cellar/ffmpeg/9.0.2_1/lib/libav*.dylib`。也就是说这个"离线包"只在这台装了 Homebrew ffmpeg 9.0.2_1 的 arm Mac 上能跑。打包脚本的注释自己写着"仅复制真实静态或独立二进制；Homebrew 动态链接版做提醒"，但代码没有任何检查，直接复制（`dev_tools/build_release_zips.py:156-162`）。
2. **Windows 分发包里根本没有 ffmpeg**：`dist/short-video-studio-win-x64.zip` 的 `vendor/` 只有 `wheelhouse/`、`whisper_models/`、`node_tools/`，没有 `vendor/bin/`。README 第 14 行却宣称"双系统离线全依赖包"。
3. **`vendor/` 被 .gitignore 排除**（`.gitignore:47`），仓库源码树里没有 vendor。任何"clone 源码 + install.sh"的安装（Intel Mac、Linux 必然走这条路）都拿不到 wheelhouse / whisper 模型 / node_tools / ffmpeg，只能联网。

---

## 1. macOS arm64（现状基线）

结论：**本机可以跑通全链路**；但"分发给别人"的 arm64 离线包不可移植，属于打包缺陷而非平台缺陷。

### 能跑

| 功能 | 依赖 | 结论 |
|---|---|---|
| 环境自检 | Python 3.12 venv + Homebrew ffmpeg | 实测 `--check-only` 全绿：ffmpeg/ffprobe/node/uv/PIL/numpy/scipy/edge_tts/fontTools/faster_whisper/librosa/Chrome 均 OK |
| 渲染 | `libx264` 软编（`render_mp4.py:1050,1083` 等） | 不依赖 VideoToolbox，纯 CPU，可跑 |
| 人像抠像 | Swift + Vision + CoreImage/Metal（`person_matting.py:287-318`，`platform.system()!="Darwin"` 直接 None） | 首选路径可跑；`swiftc` 由 Xcode CLT 提供（`vision_inspector.py:223`） |
| OCR/人脸/显著性 | Swift Vision CLI 编译缓存（`vision_inspector.py:219-263`） | 可跑，带 `fcntl` 文件锁 |
| 前景人像分割（封面） | `VNGeneratePersonSegmentationRequest`（`vision_inspector.py:343-377`） | 可跑（需 macOS 12+） |
| TTS | edge-tts；失败降级 `say`（`step2_transcribe.py:1026,1091`） | 两条路都有 |
| 字体 | `assets/fonts/*.otf|.ttf`（5 个） | 实测 Pillow 13 全部 `truetype()` 加载成功 |
| 浏览器桥 | node + playwright-core + Chrome（`common.py:1715-1750`） | 可跑 |

### 跑不了 / 缺什么

| 功能 | 卡在哪（文件:行） | 需要什么 / 量级 |
|---|---|---|
| 离线包内 ffmpeg 在"干净的 arm Mac"上启动即 dyld 失败 | `build_release_zips.py:156-162`（复制 Homebrew 动态二进制）；`find_bin` 无任何可执行性校验且优先 vendor：`common.py:415-423`；`check_command` 只 `which` 不 `-version`：`check_and_setup.py:54-57,221` | 换成静态/自包含 ffmpeg（或干脆不打包、只查 PATH）。改代码约 10 行，但必须重新产出二进制——**必须替换打包物料** |
| 无 `swiftc` 时 OCR/人脸/分割静默降级 | `vision_inspector.py:221-226,344` | 属预期降级，非崩溃 |
| 源码安装无 Whisper 模型（483 MB） | `common.py:29`（`VENDOR_DIR`）、`.gitignore:47` | 联网走 hf-mirror；或补进包 |

---

## 2. macOS x86_64（Intel Mac）

结论：**源码安装可用，视觉能力可用，性能最差；没有任何为 x86_64 准备的分发包。**

### 能跑

| 功能 | 依赖 | 结论 |
|---|---|---|
| 全链路 | 源码 + Homebrew（Intel 前缀 `/usr/local`）+ Xcode CLT swiftc | 结构与 arm64 相同；`export_hyperframes.py:77` 的 `PATH` 已含 `/usr/local/bin`，启动器可用 |
| Apple Vision（人脸/OCR/分割/抠像） | Intel Mac 同样有 Vision 框架；`VNGeneratePersonSegmentationRequest` 需 macOS 12+ | 可跑（`vision_inspector.py:221` 只判 Darwin，不判架构） |
| 字体/字幕/调色/混音 | PIL + ffmpeg 滤镜 | 可跑 |
| 渲染 | libx264 CPU | 可跑，但比 M 系列慢（软编，无 VideoToolbox，见上表） |

### 跑不了 / 缺什么

| 功能 | 卡在哪 | 需要什么 / 量级 |
|---|---|---|
| 用 arm64 包会直接崩 | `vendor/bin/ffmpeg` 是 thin arm64（`file` 证据）+ `/opt/homebrew` dylib；`find_bin` 优先返回它且不校验（`common.py:415-423`） | 不要发错架构包；`find_bin` 应校验可执行/架构，约 15 行 |
| 无 x86_64 离线包 | `build_release_zips.py:48-59` 只定义 `macos-arm64` 与 `win-x64` | 增加 `pip_platforms: macosx_*_x86_64` + 静态 ffmpeg，纯打包工作 |
| faster-whisper / librosa 依赖的 ctranslate2、onnxruntime、numba 是否有 macOS x86_64 轮子 | 无离线 wheelhouse；源码安装只能现场 pip | **无法判断**（未联网核验 PyPI 是否仍提供这些 x86_64 轮子）；若缺，ASR 不可用，只能走 `--script-json` 的 VAD 对齐（`step2_transcribe.py:1627-1635`） |
| 性能 | 全 CPU 软编 + CPU int8 Whisper（`common.py:1679`，`step2:371`） | 可用但慢；不涉及 macOS 专属加速 |

---

## 3. Windows x86_64

结论：**能跑核心链路，但开箱不自包含；Apple Vision 全家桶缺失后是"静默降级 + 质检转人工"，不是崩溃。真正会崩的是 TTS（无 `say` 兜底）和无 ffmpeg 时的安装自检。**

### 能跑

| 功能 | 依赖 | 结论 |
|---|---|---|
| Python 依赖（离线） | `vendor/wheelhouse` 全套 `cp312-win_amd64`（57 个 whl，含 ctranslate2/onnxruntime/av/numpy/scipy/pillow…） | Python 3.12 x64 可零网装（`check_and_setup.py:123-143`） |
| Whisper 转写 | `vendor/whisper_models/small/model.bin`（483 MB，win 包内） | 可离线跑 |
| 字体/字幕/调色/混音/渲染 | PIL + ffmpeg libx264 | 可跑 |
| 浏览器桥 | `common.py:1725-1738` 有 Chrome/Edge 的 Program Files 路径；`capture_html_video.mjs:83-92` 也有 Windows 路径 | 有 Chrome/Edge 即可 |
| HyperFrames 启动器 | 生成 `.bat`（`export_hyperframes.py:97-108`） | 可跑 |
| `install.ps1` | 找 `python`/`python3`（`install.ps1:4-7`） | 可跑 |
| Windows 非 C 盘工作区 | `common.py:69-86`、`install_skill.py:64-105` | 可跑 |

### 跑不了 / 缺什么

| 功能 | 卡在哪（文件:行） | 需要什么 / 量级 |
|---|---|---|
| ffmpeg 不在包里 | win zip 无 `vendor/bin/`；`check_and_setup.py:68-71` 用 winget 装，但装完当前进程 PATH 未刷新，紧接着 `find_bin`（`:221`）仍 MISSING，`:336-341` 直接 `exit(1)`，`install_skill.py:147` `check=True` 让安装整体失败 | 打包 ffmpeg.exe/ffprobe.exe，或装后刷新 PATH/重探。约 20 行或纯打包 |
| OCR / 人脸 / 眼睛锚点 / 显著性 | Vision 分支在 `vision_inspector.py:221` 直接 None；回退只做 numpy 梯度显著性（`:380-421`），`faces=[]`、`texts=[]` | 影响：人脸构图退居中（`step1_normalize.py:110-140`）、封面选帧无脸（`step9:155`）、代码视频 `ocr_available=False`（`code_video_spec.py:467-468`）。**换技术选型**（见末节） |
| 黑盒质检 3 项 | `independent_verifier.py:619-656`：非 apple_vision 一律 `SKIPPED_UNAVAILABLE`；`common.py:236-247` 归入 `manual`，不 block 但 `machine_passed=false`，必须人工签字 | 不崩；交付前多一步人工放行 |
| 人像抠像（style_05/07/19） | `person_matting.py:288` 非 mac 返回 None；`render_mp4.py:1438-1442` 失败即 `return False`，`:1960` 回退普通画面 | 静默降级。想恢复：装 mediapipe+cv2（当前两包**不在**任何安装列表：`check_and_setup.py:50-51`） |
| TTS（纯配音/无实拍路线） | edge-tts 需联网；失败后只有 `say`（`step2_transcribe.py:1091-1096`），Windows 无 `say` → `:1110` 直接 RuntimeError | 必须替换 TTS 后端（piper/本地模型）或把 edge-tts 设为硬依赖 |
| 实拍口播转写 | faster-whisper 有包；但若装失败，无 `--script-json` 时 `step2:1631-1635` 直接报错 | 离线包已覆盖 x64，问题较小 |
| 剪映原生草稿 | `export_jianying_native.py:553,572` 硬编码 `"os": "mac"` | Windows 剪映是否接受未知；**无法判断**（无 Windows 剪映可测）。改为按平台写值，2 行 |
| 文档命令 | `SKILL.md`/README 全用 `python3 scripts/...`；Windows 常只有 `python` | 文档替换，或提供 `py` 入口 |
| 路径/编码 | 全部 `pathlib` + UTF-8；`/tmp` 无使用；`os.link`/`os.symlink` 都有 try/except 回退拷贝（`step1_normalize.py:74-93`）；`chmod` 仅 mac 用途 | 结构化风险低。`ffmpeg` 在中文路径/长路径（>260）下的 argv 编码与 WIN32 长路径策略**无法判断**（未在 Windows 实测） |
| `os.execvpe` 切 venv | `common.py:430` | Windows 下是否可用**无法判断**；仅在缺包触发，若抛异常会启动即失败 |

---

## 4. Windows arm64

结论：**没有为它准备任何离线物料；FFmpeg 可跑（x64 模拟），但语音/节拍依赖大概率装不上，等于"只能用 `--script-json` 的剪辑，不能用自动转写"。**

### 能跑

| 功能 | 依赖 | 结论 |
|---|---|---|
| 纯渲染/字幕/调色/混音 | x64 ffmpeg（Windows ARM64 可模拟 x64）+ PIL/numpy | 前提是这些 Windows 包有 `win_arm64` 轮子：**无法判断**（未联网核验 numpy/scipy/pillow 的 arm64 覆盖） |
| 字体 | 同上，纯 Pillow FreeType | 可跑（若 Pillow 能装） |
| Chrome 桥 | Edge/Chrome ARM64 或 x64 | 可跑 |

### 跑不了 / 缺什么

| 功能 | 卡在哪 | 需要什么 / 量级 |
|---|---|---|
| 离线安装 | `build_release_zips.py:54-57` 只有 `win_amd64`；没有 `win-arm64` target | 需要新增平台轮子集；**缺 wheel** |
| faster-whisper / librosa | wheelhouse 无 arm64；依赖链 ctranslate2 / onnxruntime / numba / llvmlite 的 `win_arm64` 轮子覆盖**无法判断**，按公开生态通常缺失 | 若确实缺失：**必须换技术选型**（whisper.cpp / sherpa-onnx 等自带 arm64 二进制），不是"缺个 wheel"能解决 |
| 无 ASR 时的行为 | `step2_transcribe.py:1627-1635` 无脚本时直接 RuntimeError | 至少应给出"仅 VAD 对齐"的自动降级（改约 10 行） |
| Apple Vision 全线 | 同 Windows x64（`vision_inspector.py:221`） | 静默降级 |
| 人像抠像 | `person_matting.py:288` + mediapipe 无 arm64 轮子 | 该功能不可用 |
| TTS | 无 `say`（`step2:1091`） | 同 x64 |

---

## 5. Linux x86_64（Ubuntu 22.04 / Debian 12）

结论：**依赖齐、路径/编码没问题；但有两个硬坑：Ubuntu 22.04 的 ffmpeg 4.4 不认 `-fps_mode`（第一步就失败），以及 `python3-venv` 缺失时安装直接失败。视觉能力全线降级，TTS 无兜底。**

### 能跑

| 功能 | 依赖 | 结论 |
|---|---|---|
| 渲染/字幕/调色/混音 | 发行版 ffmpeg（含 libx264）+ PIL + numpy/scipy manylinux | Debian 12（ffmpeg 5.1）可跑 |
| 字体 | 5 个 OTF/TTF；Pillow 自带 FreeType | 可直接加载；`common.py:940-944` 还搜 `/usr/share/fonts` |
| 无头浏览器 | `/usr/bin/google-chrome|chromium`（`common.py:1698-1700`、`capture_html_video.mjs:87-89`） | 有浏览器即可 |
| 路径/编码 | `pathlib`、UTF-8、`tempfile`、硬链接失败回退拷贝（`step1_normalize.py:74-93`） | 中文路径/大小写无硬假设；LUT 路径已做转义（`step7_color_grade.py:454`） |
| 安装入口 | `install.sh` → `install_skill.py`（`install.sh:5`） | 可跑 |

### 跑不了 / 缺什么

| 功能 | 卡在哪（文件:行） | 需要什么 / 量级 |
|---|---|---|
| **Ubuntu 22.04 ffmpeg 4.4 第一步即失败** | `-fps_mode` 出现 4 处：`step1_normalize.py:201`、`generate_code_broll.py:391`、`workflow_guard.py:1007`、`independent_verifier.py:768` | `-fps_mode` 是 5.1 才有的选项；改为 `-vsync cfr/passthrough` 或强制 ffmpeg>=5.1。**改 4 行**（建议目标机跑 `ffmpeg -h full | grep fps_mode` 确认） |
| venv 创建 | `check_and_setup.py:133-135` 用 `python -m venv`；Debian/Ubuntu 未装 `python3-venv` 时 ensurepip 失败 | 预检并提示 `apt install python3-venv`；约 10 行 |
| 无离线物料 | 无 Linux 分发包；`VENDOR_DIR` 不存在（`common.py:29`、`.gitignore:47`） | 只能联网：PyPI 镜像（`check_and_setup.py:98-117`）+ hf-mirror 下载 483 MB Whisper。**缺 wheelhouse 目标平台** |
| 自动装 ffmpeg | `check_and_setup.py:73-75` 直接 `sudo apt-get`，非交互环境会卡密码；apt 版本即 4.4 | 应给出"请先装 ffmpeg≥5.1"的显式检查；约 10 行 |
| OCR/人脸/分割 | `vision_inspector.py:221` 非 Darwin 返回 None | 静默降级 + 质检转人工（`independent_verifier.py:619-656`） |
| 人像抠像 style_05/07/19 | `person_matting.py:288`；`render_mp4.py:1438-1442,1960` | mediapipe+cv2 未安装（不在 `check_and_setup.py:50-51`），静默降级 |
| TTS | 无 `say`；`step2_transcribe.py:1091-1110` | edge-tts 必须联网，否则硬失败 |
| 无 `swiftc` | `vision_inspector.py:223` | 预期降级 |
| 性能 | 全 CPU libx264 + CPU int8 Whisper | 能跑，不比 Windows 更差；没有 macOS 专属加速可失去 |

---

## 必须做的最小改动清单（按"让 Linux 与 Windows 真正可用"排序）

| # | 改动 | 类型 | 量级 |
|---|---|---|---|
| 1 | `vendor/bin/ffmpeg` 换成**静态/自包含**二进制；`find_bin` 增加"能否 `-version` 执行"校验并回退 PATH（`common.py:415-423`；`build_release_zips.py:156-162`） | **必须替换物料** | 约 15 行代码 + 重新打包；否则连 arm64 离线包都不可移植 |
| 2 | win-x64 包补 `ffmpeg.exe`/`ffprobe.exe`；apt/winget 装完刷新 PATH 后再探测（`check_and_setup.py:59-75,221`） | 缺二进制 / 约 20 行 | 小 |
| 3 | `-fps_mode` → `-vsync` 或声明 ffmpeg≥5.1（4 处，`step1:201` 等） | 兼容性 | 4 行 |
| 4 | 增加 `linux-x86_64`（manylinux）+ `macos-x86_64` 的 wheelhouse/ffmpeg 打包目标（`build_release_zips.py:48-59`） | **缺 wheel** | 纯打包 |
| 5 | Linux/Windows arm64 的 faster-whisper：要么补 `win_arm64` 轮子，要么替换为自带二进制的 ASR（whisper.cpp / sherpa-onnx） | **必须替换技术选型**（若轮子确实不存在） | 中大 |
| 6 | TTS 去掉对 macOS `say` 的唯一兜底：内置离线 TTS（piper 等）或把 edge-tts 设为硬依赖+重试（`step2:1091-1110`） | **必须替换技术选型** | 中 |
| 7 | Apple Vision 三件套跨平台替代：OCR→RapidOCR/PaddleOCR(onnxruntime)；人脸→OpenCV YuNet / mediapipe；前景分割→mediapipe selfie / u2net onnx（`vision_inspector.py:219-377`、`person_matting.py:287-318`） | **必须替换技术选型** | 大（3 个模块 + 模型文件） |
| 8 | 把 `mediapipe`/`cv2` 加入可选安装并处理轮子覆盖（`check_and_setup.py:50-51`） | 缺 wheel | 小 |
| 9 | `export_jianying_native.py:553,572` 的 `"os":"mac"` 按平台生成 | 2 行 | 小 |
| 10 | 文档/提示里的 `python3` 在 Windows 改为 `python`/`py`；`install.ps1` 的 Python 3.10+ 校验 | 文案 | 小 |

---

## 全局观察（跨 5 平台都一样）

- **没有任何 `sys.platform`/`platform.machine()` 的架构分支**，只有 `platform.system()` 的三处 Darwin 判断（`vision_inspector.py:221,344`、`person_matting.py:288`）和 Windows 专属工具函数（`common.py:69-86,418`）。所以"是否能跑"由外部二进制决定，不由 Python 分支决定。
- **渲染路径完全可移植**：字幕是 Pillow 逐帧 PNG 合成（`render_mp4.py:2172`），不走 libass/`drawtext`；调色/转场是 `eq/curves/lut3d/xfade` 等旧滤镜；LUT 路径已做 Windows 转义（`step7_color_grade.py:454`）；concat 用 `as_posix()`（`render_mp4.py:1981`）。这是这套 skill 最大的跨平台资产。
- **"直接崩"清单**（非 mac）：无 edge-tts 又无 `say`（`step2:1110`）；无 faster-whisper 又无 `--script-json`（`step2:1631-1635`）；knowledge-animation / `--code-html` 无浏览器（`run_pipeline.py:545-547,1376-1379`）；ffmpeg 缺失或版本过低（`check_and_setup.py:336-341`、`step1:201`）。
- **"静默降级"清单**：人脸/眼睛锚点/显著性地标（`vision_inspector.py:380-421`）、OCR 质检（`independent_verifier.py:619-656`，转人工）、封面人像分割（`step9:791-800`）、人像抠像（`render_mp4.py:1438-1442,1960`）、HTML 动效补镜（`generate_code_broll.py:1229-1247` 回退 Pillow）、4K 代码封面（`step9:435-437` 直接跳过）。
- 性能上没有任何 macOS 专属加速被依赖：ffmpeg 只用 `libx264`，Whisper 固定 `device='cpu', compute_type='int8'`（`common.py:1679`、`step2:371`）。所以 Windows/Linux 不会"慢到不可用"，最慢的反而是 Intel Mac。

## Open Questions

- Windows/Linux 上 `ffmpeg` 对中文路径与 >260 字符路径的真实行为：我只有 macOS 可测，**无法判断**；这决定了是否需要给工作区路径做长度保护。
- `ctranslate2` / `onnxruntime` / `numba` / `llvmlite` / `pillow` 是否提供 `win_arm64` 与 `macosx_x86_64` 轮子：离线无法核验，**无法判断**；这直接决定 Windows arm64 与 Intel Mac 是"缺 wheel"还是"必须换 ASR 技术选型"。若这一条能确认，第 5 项的量级会从"中大"变成"纯打包"或反过来。
- Windows 剪映是否接受 `"os":"mac"` 的原生草稿：**无法判断**（无 Windows 剪映）；只影响 `export_jianying_native` 这一条交付路线。

---

## 本轮按此清单已经改掉的（v4.2）

| 项 | 改法 | 证据 |
|---|---|---|
| 离线包里的 ffmpeg 是 Homebrew 动态链接版 | 打包时 `otool -L` 检查，检出 `/opt/homebrew` / `/usr/local/Cellar` 依赖就**拒绝内置**并提示用静态构建 | `dev_tools/build_release_zips.py:_is_dynamic_homebrew_bin` |
| `find_bin` 只判断"文件在"，不判断"跑得起来" | 对 ffmpeg/ffprobe 真跑一次 `-version` 验活并缓存，跑不动回落 PATH | `common.py:_vendor_bin_usable` |
| Ubuntu 22.04 的 ffmpeg 4.4 不认 `-fps_mode` | 新增 `platform_probe.fps_mode_args()`，<5.1 自动换 `-vsync`；4 处调用点全改 | `step1_normalize` / `generate_code_broll` / `independent_verifier` / `workflow_guard` |
| winget 装完 PATH 不刷新导致安装失败 | 装完扫 winget 包目录把 ffmpeg 所在目录补进 PATH 再探 | `check_and_setup.py:try_install_ffmpeg` |
| Linux 装到 ffmpeg 4.4 不说一声 | 装完明确提示"会自动用 -vsync；要新版请装 ffmpeg>=5.1" | 同上 |
| 原生剪映草稿 `"os": "mac"` 写死 | 按平台写 `mac` / `windows` | `export_jianying_native.py:_jy_os_value` |
| 用户不知道自己机器哪几步会降级 | `python3 scripts/platform_probe.py` 一张体检单 | `platform_probe.py` |

## 还没解决（要说清楚的话）

1. **Windows arm64 / Intel Mac 的自动转写**：取决于 `ctranslate2` / `onnxruntime` / `numba` 是否提供对应轮子。
   没有的话不是"缺个 wheel"，而是**必须换技术选型**（whisper.cpp / sherpa-onnx 这类自带二进制的 ASR）。
2. **非 mac 的 OCR / 人脸 / 抠像**：Apple Vision 三件套没有等价的现成实现，替代方案是
   RapidOCR/PaddleOCR(onnxruntime) + OpenCV YuNet/mediapipe + u2net onnx —— 三个模块 + 模型文件，工作量大。
   目前非 mac 是**静默降级**：人脸构图退回居中、封面不选脸、黑盒质检转人工签字。
3. **非 mac 的 TTS**：edge-tts 要联网，失败只有 macOS 的 `say` 兜底 → Windows/Linux 会直接报错。
4. **离线 wheelhouse 只有 `macos-arm64` 与 `win-x64` 两套**：Intel Mac / Linux / win-arm64 目前只能源码联网安装。
5. **Windows 中文路径 / >260 字符路径**：未在 Windows 实测，无法判断，可能需要给工作区做路径长度保护。
