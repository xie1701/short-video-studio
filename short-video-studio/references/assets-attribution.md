# 内置资产可商用授权与来源清单 (Assets Attribution)

本清单声明 `short-video-studio/assets/` 下所有内置字体、背景音乐、音效、贴纸、3D LUT 及代码补镜模板的版权归属与商业使用许可。

---

## 一、 可商用字体 (Fonts)

流水线与字幕模板优先加载以下开源免费可商用字体（全部采用 **SIL Open Font License 1.1** 或 **Apache 2.0** 协议，允许商业短视频传播、嵌入字幕与商用发布）：

| 字体名称 | 授权协议 | 来源项目 |
| :--- | :--- | :--- |
| **思源黑体 (Source Han Sans CN / Noto Sans CJK SC)** | SIL OFL 1.1 | Adobe & Google 开源发布 (`github.com/adobe-fonts/source-han-sans`) |
| **思源宋体 (Source Han Serif CN)** | SIL OFL 1.1 | Adobe & Google 开源发布 (`github.com/adobe-fonts/source-han-serif`) |
| **得意黑 (Smiley Sans)** | SIL OFL 1.1 | atelier-anchor 开源发布 (`github.com/atelier-anchor/smiley-sans`) |
| **阿里妈妈数黑体 (Alimama ShuHeiTi)** | 阿里巴巴免费可商用授权 | 阿里妈妈智造字官网授权全平台免费商用 |

> 注：仓库内 `assets/fonts/` 随仓预置轻量化免费商用字体 `AlimamaShuHeiTi-Bold.otf` (约 1.0 MB，可用 `check_and_setup.py --init-fonts` 初始化校验)，若本机安装有思源黑体/宋体、清松手写体等同样会优先选用；所有导出与 `#22` 质检均通过 `common.is_verified_commercial_font()` **严格拦截无商用授权的系统私有字体（如苹方/华文黑体/微软雅黑）**，防止版权风险。

---

## 二、 内置可商用背景音乐 (BGM)

| 文件名 | 节奏/风格 | 版权与来源 | 授权协议 |
| :--- | :--- | :--- | :--- |
| `assets/bgm/upbeat_store_120bpm.wav` | 120 BPM 轻快律动（适配门店探店/美食/快节奏干货） | 由 `scripts/generate_builtin_assets.py` 程序化数字音频合成（零第三方采样） | **CC0 1.0 公共领域（完全免费可商用）** |
| `assets/bgm/chill_story_100bpm.wav` | 100 BPM 温暖叙事（适配老板故事/原则初心/人设科普） | 由 `scripts/generate_builtin_assets.py` 程序化数字音频合成（零第三方采样） | **CC0 1.0 公共领域（完全免费可商用）** |

---

## 三、 内置可商用音效 (SFX)

所有音效均由 `scripts/generate_builtin_assets.py` 通过纯数学波形与物理包络合成（48kHz 16-bit PCM WAV），无任何第三方版权风险：

| 文件名 | 用途场景 | 来源 | 授权协议 |
| :--- | :--- | :--- | :--- |
| `assets/sfx/whoosh.wav` | 镜头切换 / J-cut 视觉过渡 | 本项目数字信号合成 | **CC0 1.0 公共领域可商用** |
| `assets/sfx/ding.wav` | 干货要点 / 价格或步骤数字出现 | 本项目数字信号合成 | **CC0 1.0 公共领域可商用** |
| `assets/sfx/pop.wav` | 气泡花字 / 贴纸弹出 | 本项目数字信号合成 | **CC0 1.0 公共领域可商用** |
| `assets/sfx/bass_drop.wav` | 钩子反转 / 情绪重音冲击 | 本项目数字信号合成 | **CC0 1.0 公共领域可商用** |
| `assets/sfx/shutter.wav` | 定格特写 / 证据截图定格 | 本项目数字信号合成 | **CC0 1.0 公共领域可商用** |

---

## 四、 调色预设 (LUTs) & 现场信息可视化渲染器

| 资产路径 | 说明 | 来源 | 授权协议 |
| :--- | :--- | :--- | :--- |
| `assets/luts/warm_store.cube` | 门店暖调提亮 LUT (17³) | 本项目三维色彩映射表生成 | **CC0 1.0 可商用** |
| `assets/luts/clean_creator.cube` | 博主清透自然 LUT (17³) | 本项目三维色彩映射表生成 | **CC0 1.0 可商用** |
| `assets/luts/high_contrast.cube` | 高反差纪实 LUT (17³) | 本项目三维色彩映射表生成 | **CC0 1.0 可商用** |

- 信息图表与 7 种修辞补镜（`kinetic_text / data_chart / flow_diagram / proportion / day_night / chips / versus`）均由 `scripts/generate_code_broll.py` 使用纯 Python (`Pillow` + `ffmpeg rawvideo` 管道) 现场原创绘制，无第三方 UI 素材版权负担。

---

## 五、 「风格谱 (Style Spectrum)」参考包装资产库

- `assets/style_spectrum/luts/style_01.cube` ~ `style_20.cube`（`33³` 3D LUT）：由 `scripts/analyze_style_spectrum.py` 基于参考样片色彩分布逆向生成的数学色彩映射矩阵，**CC0 1.0 可商用**。
- `assets/style_spectrum/hyperframes/`、`assets/style_spectrum/style_spectrum_library.json`、`assets/subtitle_templates/templates.json`：本项目原创风格谱 CSS/JSON 配置，**MIT / CC0 可商用**；全部字体候选链严格映射至 SIL OFL / 阿里妈妈免费商用字体。

---

## 六、 Vibe Sound CC0 真实物理音效与流派背景音乐 (`assets/sound_vibe/`)

由 [scripts/vibe_sound.py](../scripts/vibe_sound.py) 基于 [vibe-motion/sound](https://github.com/vibe-motion/sound) 检索框架筛选 **Creative Commons 0 (CC0)** 免版税真实录音素材并生成（现全库 BGM 元数据已完整覆盖 `beat_times_sec` / `downbeat_times_sec` 且支持全部 20 套 `style_01`–`style_20` 映射）：

| 流派样式 | 真实 BGM 来源 (Freesound ID · CC0) | 物理材质 SFX 来源 (Freesound ID · CC0) |
| :--- | :--- | :--- |
| `style_14` | `fs:680113` Pop Guitar Loop Thing | `fs:585257` Swoosh-1 / `fs:704935` Wooden Tap / `fs:411462` Bubble Pop |
| `style_02` | `fs:69195` Mix05 90BPM Hip-Hop Loop | `fs:585257` Swoosh-1 / `fs:512389` Bass Impact / `fs:878248` Micro Swish |
| `style_03` | `fs:713517` Perspire Guitar & Piano Loop | `fs:623709` Soft Ambience Swoosh / `fs:369710` Wood Knock / `fs:704935` Wooden Tap |
| `style_12` | `fs:629153` Dusty Lofi Melody Loop 75 BPM | `fs:878248` Micro Swish / `fs:411462` Bubble Pop / `fs:528561` Soft UI Click |
| `style_06` | `fs:668907` Cinematic Emotional Piano (92bpm) | `fs:623709` Soft Ambience Swoosh / `fs:676375` Minolta Film Camera Shutter |
| `style_05` | `fs:347815` Warm Breeze Loop | `fs:82379` Paper Flip (Olympus LS-10) / `fs:369710` Wood Knock |
| `style_15` | `fs:531463` Waterfall Synth Loop | `fs:378085` Mechanical Key / `fs:528561` Soft UI Click / `fs:217181` Till Receipt Printer |
