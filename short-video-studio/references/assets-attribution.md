# 内置资产可商用授权与来源清单 (Assets Attribution)

本清单声明 `short-video-studio/assets/` 下所有随仓分发的可商用字体、CC0 真实录制背景音乐、CC0 真实物理音效、3D LUT 及代码渲染器的版权归属与商业使用许可。

---

## 一、 可商用字体 (Fonts)

流水线与 24 套字幕模板优先加载以下开源免费可商用字体（全部采用 **SIL Open Font License 1.1**、**Apache 2.0** 或官方全平台免费商用授权）：

| 字体名称 | 授权协议 | 来源项目 |
| :--- | :--- | :--- |
| **阿里妈妈数黑体 (Alimama ShuHeiTi)** | 阿里巴巴免费可商用授权 | 阿里妈妈智造字官网授权全平台免费商用（随仓内置 `assets/fonts/AlimamaShuHeiTi-Bold.otf`，约 1.0 MB） |
| **思源黑体 (Source Han Sans CN / Noto Sans CJK SC)** | SIL OFL 1.1 | Adobe & Google 开源发布 (`github.com/adobe-fonts/source-han-sans`) |
| **思源宋体 (Source Han Serif CN)** | SIL OFL 1.1 | Adobe & Google 开源发布 (`github.com/adobe-fonts/source-han-serif`) |
| **得意黑 (Smiley Sans)** | SIL OFL 1.1 | atelier-anchor 开源发布 (`github.com/atelier-anchor/smiley-sans`) |

> 注：所有导出与 `#22` 质检均通过 `common.is_verified_commercial_font()` **严格拦截无商用授权的系统私有字体（如苹方/华文黑体/微软雅黑）**，防止商业发布版权风险。本机合法安装上述免费商用开源字体时同样会被识别放行。

---

## 二、 CC0 真实录制背景音乐与物理材质音效 (`assets/sound_vibe/`)

全仓已完全淘汰低质电子合成音频，统一采用由 [scripts/vibe_sound.py](../scripts/vibe_sound.py) 基于 [vibe-motion/sound](https://github.com/vibe-motion/sound) 筛选的 **Creative Commons 0 (CC0 1.0 公共领域)** 免版税真实录音素材（BGM 统一转为 48kHz AAC `.m4a` 并经真实波形起拍校准 `onset_offset_sec` / `beat_times_sec` / `downbeat_times_sec`；物理音效为 48kHz 16-bit PCM WAV，支持全部 20 套 `style_01`–`style_20` 映射与门店/博主双轨默认配置）：

| 代表流派样式 | 真实 BGM 来源 (Freesound ID · CC0) | 物理材质 SFX 来源 (Freesound ID · CC0) |
| :--- | :--- | :--- |
| `style_14`（默认门店轨） | `fs:680113` Pop Guitar Loop Thing | `fs:585257` Swoosh-1 / `fs:704935` Wooden Tap / `fs:411462` Bubble Pop |
| `style_02` | `fs:69195` Mix05 90BPM Hip-Hop Loop | `fs:585257` Swoosh-1 / `fs:512389` Bass Impact / `fs:878248` Micro Swish |
| `style_03` | `fs:713517` Perspire Guitar & Piano Loop | `fs:623709` Soft Ambience Swoosh / `fs:369710` Wood Knock / `fs:704935` Wooden Tap |
| `style_12`（含博主轨） | `fs:629153` Dusty Lofi Melody Loop 75 BPM | `fs:878248` Micro Swish / `fs:411462` Bubble Pop / `fs:528561` Soft UI Click |
| `style_06` | `fs:668907` Cinematic Emotional Piano (92bpm) | `fs:623709` Soft Ambience Swoosh / `fs:676375` Minolta Film Camera Shutter |
| `style_05` | `fs:347815` Warm Breeze Loop | `fs:82379` Paper Flip (Olympus LS-10) / `fs:369710` Wood Knock |
| `style_15` | `fs:531463` Waterfall Synth Loop | `fs:378085` Mechanical Key / `fs:528561` Soft UI Click / `fs:217181` Till Receipt Printer |

> 注：如果文案流程使用在线 `edge-tts` 生成配音，仅面向演示与内部测试；`compliance_guard.py` 会在 `asset_license_manifest.json` 显式输出 `tts_commercial_notice` 告警，正式商业投放前建议替换为真人实拍口播或已采购商用授权语音。

---

## 三、 调色预设 (`17³` 3D LUTs) 与代码叙事渲染器

| 资产路径 | 说明 | 来源 | 授权协议 |
| :--- | :--- | :--- | :--- |
| `assets/luts/warm_store.cube` | 门店暖调提亮 LUT (`17³`) | 仓库离线构建脚本 `dev_tools/generate_builtin_assets.py` 生成 | **CC0 1.0 可商用** |
| `assets/luts/clean_creator.cube` | 博主清透自然 LUT (`17³`) | 仓库离线构建脚本 `dev_tools/generate_builtin_assets.py` 生成 | **CC0 1.0 可商用** |
| `assets/style_spectrum/luts/style_01.cube` ~ `style_20.cube` | 20 套风格谱色彩映射矩阵 (`17³`) | 仓库离线分析脚本 `dev_tools/analyze_style_spectrum.py` 生成 | **CC0 1.0 可商用** |
| `assets/style_spectrum/style_spectrum_library.json`、`assets/subtitle_templates/templates.json` | 风格谱配置与 24 套模板 | 本项目原创结构化配置（零私有字体） | **MIT / CC0 可商用** |

- 信息图表与 7 种修辞补镜（`kinetic_text / data_chart / flow_diagram / proportion / day_night / chips / versus`）均由 `scripts/generate_code_broll.py`（无头 Chrome HTML + Pillow 兜底）现场原创绘制，无第三方 UI 素材版权负担。
