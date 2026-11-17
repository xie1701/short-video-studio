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

> 注：若目标主机尚未安装上述字体，脚本会自动检测系统可用开源字体，或提示通过 `check_and_setup.py` 下载/定位对应开源字体；Windows/macOS 自带回退仅用于本地测试预览，正式导出默认优先绑定本清单内的 OFL 开源商用字体路径。

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

## 四、 内置贴纸 (Stickers) & 调色预设 (LUTs)

| 资产路径 | 说明 | 来源 | 授权协议 |
| :--- | :--- | :--- | :--- |
| `assets/stickers/arrow_highlight.svg` | 重点指引箭头贴纸 | 本项目原创矢量绘制 | **CC0 1.0 可商用** |
| `assets/stickers/badge_must_see.svg` | 高信息密度角标徽章 | 本项目原创矢量绘制 | **CC0 1.0 可商用** |
| `assets/stickers/check_mark.svg` | 正确/推荐对勾标识 | 本项目原创矢量绘制 | **CC0 1.0 可商用** |
| `assets/stickers/warning_tag.svg` | 避坑/警示标签 | 本项目原创矢量绘制 | **CC0 1.0 可商用** |
| `assets/luts/warm_store.cube` | 门店暖调提亮 LUT (17³) | 本项目三维色彩映射表生成 | **CC0 1.0 可商用** |
| `assets/luts/clean_creator.cube` | 博主清透自然 LUT (17³) | 本项目三维色彩映射表生成 | **CC0 1.0 可商用** |
| `assets/luts/high_contrast.cube` | 高反差纪实 LUT (17³) | 本项目三维色彩映射表生成 | **CC0 1.0 可商用** |

---

## 五、 代码生成补镜模板

- `assets/remotion_templates/`：本项目原创 MIT/CC0 模板，支持在 Remotion / HyperFrames 或本地 Pillow+FFmpeg 引擎中无限制商用渲染。

---

## 六、 「风格谱 (Style Spectrum)」参考包装资产库

- `assets/style_spectrum/luts/style_01.cube` ~ `style_20.cube`（`33³` 3D LUT）：由 `scripts/analyze_style_spectrum.py` 基于公开参考样片色彩统计分布程序化逆向生成的数学色彩映射矩阵，**CC0 1.0 可商用**。
- `assets/style_spectrum/remotion/`、`assets/style_spectrum/hyperframes/`、`assets/style_spectrum/jianying/`：本项目原创跨平台代码模板与 JSON 参数配置，**MIT / CC0 可商用**；所有字幕样式优先绑定「思源黑体 / 思源宋体 / 阿里妈妈数黑体 / 清松手写体」等开源/免费可商用字体链。
- `assets/style_spectrum/keyframes/style_01_contact.jpg` ~ `style_20_contact.jpg`：用于本地视觉复核与参数比对的关键帧采样图谱（仅作教学与参考研究用途，请勿直接截取原片人物肖像用于商业投放）。

---

## 七、 Vibe Sound CC0 真实物理音效与流派背景音乐 (`assets/sound_vibe/`)

由 [scripts/vibe_sound.py](file:///Users/jerry/Downloads/AIworks/shortVideo-CCstudio/short-video-studio/scripts/vibe_sound.py) 基于 [vibe-motion/sound](https://github.com/vibe-motion/sound) 检索框架从 Freesound 筛选 **Creative Commons 0 (CC0)** 商业免版税真实录音素材并执行静音裁切与 LUFS 归一化清洗生成：

| 流派样式 | 真实 BGM 来源 (Freesound ID · CC0) | 物理材质 SFX 来源 (Freesound ID · CC0) |
| :--- | :--- | :--- |
| `style_14` | `fs:680113` Pop Guitar Loop Thing | `fs:585257` Swoosh-1 / `fs:704935` Wooden Tap / `fs:411462` Bubble Pop |
| `style_02` | `fs:69195` Mix05 90BPM Hip-Hop Loop | `fs:585257` Swoosh-1 / `fs:512389` Bass Impact / `fs:878248` Micro Swish |
| `style_03` | `fs:713517` Perspire Guitar & Piano Loop | `fs:623709` Soft Ambience Swoosh / `fs:369710` Wood Knock / `fs:704935` Wooden Tap |
| `style_12` | `fs:629153` Dusty Lofi Melody Loop 75 BPM | `fs:878248` Micro Swish / `fs:411462` Bubble Pop / `fs:528561` Soft UI Click |
| `style_06` | `fs:668907` Cinematic Emotional Piano (92bpm) | `fs:623709` Soft Ambience Swoosh / `fs:676375` Minolta Film Camera Shutter |
| `style_05` | `fs:347815` Warm Breeze Loop | `fs:82379` Paper Flip (Olympus LS-10) / `fs:369710` Wood Knock |
| `style_15` | `fs:531463` Waterfall Synth Loop | `fs:378085` Mechanical Key / `fs:528561` Soft UI Click / `fs:217181` Till Receipt Printer |
