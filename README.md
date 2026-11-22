# short-video-studio 短视频制片工作室

面向实体门店获客和个人账号增长的短视频制片 Agent Skill。支持从一手真实材料策划选题、按锁定音轨「先音后画」剪辑、代码生成图解补镜、高清 MP4 与剪映/HyperFrames 可编辑工程导出、独立黑盒质检与盲审取证，以及发布数据对照实验复盘。

---

## 一、 核心设计原则

1. **首次对话「先猜后确认」双轨制**
   - **门店轨（`store`）**：服务实体店老板 / 本地生活商家，以**评论区咨询、私信、到店核销**为核心目标（默认风格谱 `style_14`）。
   - **博主轨（`creator`）**：服务个人创作者 / 知识或泛娱乐账号，以**开头 3 秒留人、看完全片、新增关注**为核心目标（默认风格谱 `style_11`）。
2. **唯一选题与文案方法论（内部严格溯源，对外全程大白话）**
   - 以 [methodology-cheatsheet.md](short-video-studio/references/methodology-cheatsheet.md) 为日常速查手册、[建立可循环的内容系统.md](short-video-studio/references/%E5%BB%BA%E7%AB%8B%E5%8F%AF%E5%BE%AA%E7%8E%AF%E7%9A%84%E5%86%85%E5%AE%B9%E7%B3%BB%E7%BB%9F.md) 为完整总库。
   - **全程零黑话铁律**：内部按完整编导体系选型并过「事件一句话」自检；面向用户对话、4 周发片计划、口播稿、拍摄卡、剪辑表、《我替你做的决定》与周复盘全部转化为老百姓听得懂的大白话。
3. **三入口与「最多打断 3 次」交互契约**
   - **入口 1（从零策划实拍）**：主页定位 → 4 周发片计划 → 口播稿与带 SVG 构图示意的手机拍摄卡。
   - **入口 2（带素材进来）**：一键盘点零散素材（高动态/Log 探测、锐度/防抖最佳入点、景别运镜、人脸显著性与缩略接触表），自动反推选题与剪辑方案。
   - **入口 3（纯代码制片 `--mode code-video`）**：零实拍直出，按 `00_code_spec.json → 单 Chrome 批量渲染 → 像素取证 VERIFY.md` 全闭环出片。
   - **仅 3 次交互打断**：① 确认选题 → ② 确认拍摄卡或盲听音轨 → ③ 确认锁定音轨与低清粗剪预览（支持只改画面不动音轨或改词重锁音轨）。
4. **「先音后画」+ 真居中字幕 + 真实物理音景**
   - Step 3 锁定纯语音 + CC0 真实录制配乐节拍（支持开场/正文双段配乐切换、真实波形起拍校准、闭口帧切点微调），后续切镜、顺滑转场、特写动作声轻混与音效花字严格绑定音频事件帧。
   - 字幕四端（MP4 / 剪映 / HyperFrames / 质检）强锁 `X = 540px` 正中对称带 `[220, 860]`（宽 `640px`）与 `Y = 1310px` 基准，7 点自动避脸不撞点赞按钮。
5. **独立黑盒裁判与一页式盲审取证包**
   - 每次出片自动跑白盒契约 + 单遍解码黑盒校验（`-14.0 ±1.5 LUFS`、单帧黑屏、540px OCR 居中实测、单框 WCAG、BT.709 肤色、切点像素跳变、SFX 5ms 起音校准、本地 Whisper 盲听与商用授权清查），同时生成《独立盲审取证包》（`10_blind_review_pack.md` + `10_action_strips.jpg`）。

---

## 二、 仓库目录结构

- `short-video-studio/SKILL.md`：Agent Skill 入口与工作流规范。
- `short-video-studio/references/`：编导方法论、中间文件契约、安全区规范、代码制片工艺与商用授权清单。
- `short-video-studio/scripts/`：全套端到端制片、导出、质检与复盘脚本。
- `short-video-studio/assets/`：可商用字体、CC0 真实录制 BGM & 物理 SFX（`sound_vibe/`）、28 套字幕模板与 24 套风格谱 `17³` LUT。
- `dev_tools/`：离线风格谱分析与资产重建脚本（开发维护用，不随 Skill 分发包打包）。
- `tests/`：核心流水线契约单元测试。
- `install_skill.py` / `install.sh` / `install.ps1`：跨平台一键环境检查与安装脚本。

---

## 三、 安装与环境检查

前置要求：**Python 3.10+**、**FFmpeg / FFprobe**（macOS 未安装时脚本可自动调用 Homebrew 安装）、Git。

### macOS / Linux
```bash
git clone https://github.com/xie1701/short-video-studio.git
cd short-video-studio
bash install.sh
```

### Windows (PowerShell)
```powershell
git clone https://github.com/xie1701/short-video-studio.git
cd short-video-studio
.\install.ps1
```

安装入口先验证 Python 3.10+、FFmpeg/FFprobe 和核心 Python 依赖，再部署 Skill。支持 `--check-only` 只检环境和 `--skills-dir` 指定目录；已有安装自动保留备份以便回滚。Node 和本机 Chrome 用于 HTML 动效与 HyperFrames 官方运行时校验。

---

## 四、 使用指南

### 方式 1：在 Claude Code 中直接对话触发
安装后打开任意会话直接用自然语言描述即可：
- **带素材进场**：“帮我把 `./raw_media` 里的探店口播和空镜素材盘点一下，用门店轨剪一条宣传成片。”
- **从零起号策划**：“我想做一个餐饮门店短视频账号，帮我制定 4 周发片计划并先出第一条的手机拍摄卡。”
- **复盘迭代**：“这是这条视频发出去 48 小时后的播放、3 秒留存和咨询数，帮我记入周复盘。”

### 方式 2：命令行单独调用各工程工具

> 脚本内置 `maybe_reexec_in_venv()`，普通 `python3` 调用也会自动切换至 `~/.short-video-studio/venv` 隔离环境。

1. **一手客户原话接入与剧本口语体检**
   ```bash
   python3 short-video-studio/scripts/raw_intel_ingest.py \
     --comments-file ./comments.txt --proof-dir ./proofs --script-json ./script.json \
     --track store --output ./my_video/pipeline/00_raw_intel.json
   ```
2. **生成手机拍摄任务卡（入口 1）**
   ```bash
   python3 short-video-studio/scripts/generate_shot_card.py \
     --output ./my_video/shot_card.md --title "花300万砸200平纯手工厨房" --track store --industry catering
   ```
3. **素材全量盘点与反推选题（入口 2）**
   ```bash
   python3 short-video-studio/scripts/inventory_media.py \
     --input-dir ./my_video/raw_media --output ./my_video/pipeline/00_inventory.json --track store
   ```
4. **代码生成叙事补镜（缺少特写镜头时）**
   ```bash
   python3 short-video-studio/scripts/generate_code_broll.py \
     --output ./my_video/broll_data.mp4 --mode data_chart --title "200平纯手工明档厨房" \
     --items-json '[{"label":"纯手工现点现做","value":34,"unit":"%"},{"label":"拒绝预制菜占比","value":100,"unit":"%"}]'
   ```
5. **运行端到端流水线（六大执行模式）**
   ```bash
   # 粗剪预览（打断 #3）
   python3 short-video-studio/scripts/run_pipeline.py --account demo --raw-dir ./raw_media --track store --mode lock-preview

   # 只改画面（锁定音轨一比特不变）
   python3 short-video-studio/scripts/run_pipeline.py --account demo --raw-dir ./raw_media --track store --mode reedit-visuals --prefer-broll-only

   # 改词重锁音轨
   python3 short-video-studio/scripts/run_pipeline.py --account demo --raw-dir ./raw_media --track creator --mode relock-voice --script-json ./new_script.json

   # 一键正式出片（默认门店 style_14，可指定任意 --style-id / --raw-intel / --benchmark / --exports）
   python3 short-video-studio/scripts/run_pipeline.py --account demo --raw-dir ./raw_media --track store --mode full --style-id style_14 --benchmark ./benchmark.mp4
   ```
6. **独立校验与工程导出**
   - 自动质检：`python3 short-video-studio/scripts/qa_inspector.py --pipeline-dir ./my_video/pipeline`
   - 剪映工程：`python3 short-video-studio/scripts/export_jianying.py --pipeline-dir ./my_video/pipeline`
   - HyperFrames 工程：`python3 short-video-studio/scripts/export_hyperframes.py --pipeline-dir ./my_video/pipeline`
7. **发布数据沉淀与对照实验复盘**
   ```bash
   python3 short-video-studio/scripts/content_log_review.py \
     --account demo --track store \
     --add-json '{"track":"store","platform":"douyin","video_id":"v-01","title":"新店开门","published_at":"2026-11-08T10:00:00+08:00","captured_at":"2026-11-10T10:00:00+08:00","observation_hours":48,"duration_sec":42,"traffic_source":"local_feed","metrics":{"retention_3s":{"numerator":8500,"denominator":18500},"completion_rate":{"numerator":5180,"denominator":18500},"inquiry_rate":{"numerator":37,"denominator":18500}}}'
   ```

### 方式 3：导入剪映与 HyperFrames 双击编辑

#### 1) 剪映专业版导入（相对路径 FCPXML 多轨工程）
1. 首次使用打开剪映专业版 → 设置 → 全局设置 → 开启「导入工程」。
2. 在首页点击「导入工程」，选中 `<项目>/pipeline/jianying_draft/*.fcpxml`：
   - **主视频轨 (`V1`)**：精剪调色分镜 `media/segments/seg_xxx.mp4`；
   - **上层透明包装与双色字幕轨 (`overlays/*.png` + 备用 `<title>`)**：严格保持 540px 正中对称排版、商业字体、暖金关键词高亮与安全区避脸；
   - **三轨解耦音频 (`A1–A3`)**：`voice_master.wav`（纯净人声）、`bgm_ducked.wav`（含闪避配乐与特写现场声）、`sfx_master.wav`（点睛音效），防削波对齐可独立调音。
3. 需改文字或二次调色时，可直接拖入同目录 `subtitles.srt` / `subtitles.ass` 或导入 `*.cube` LUT。实际导入目标剪映版本后，在终审签字中记录版本与检查结果。

#### 2) HyperFrames Studio 原生时间线编辑
打开 `<项目>/pipeline/hyperframes_draft/`，双击 `启动HyperFrames工程.command`（macOS）或 `启动HyperFrames工程.bat`（Windows），或在目录内运行 `npx --yes hyperframes@0.9.72 preview .`。工程内所有视频、字幕、花字、人声、配乐与音效均以独立时间线条目暴露，完全离线自包含。

---

## 五、 成片与工程验收

- `--exports auto` 默认尝试导出两种工程，工程异常时保留通过验收的 MP4；`--exports mp4` 只做成片；`--exports jianying|hyperframes|both` 将所选工程列为必检。
- MP4 实测覆盖响度（`-14.0 ±1.5 LUFS`）、单帧黑屏、长静音、音视频起点与长度差（`≤1 帧`）、OCR 540px 居中与安全区、单框 WCAG、BT.709 肤色、切点像素跳变、SFX 5ms 起音校准及本地 Whisper 盲听复核（`--strict-asr` 时缺模型阻断）。
- 同步生成《独立盲审取证包》（`10_blind_review_pack.md` + `10_action_strips.jpg`）。完成人工核对后，使用 `workflow_guard.py approve-final --pipeline-dir <目录> --reviewed <ID> --note "<复核记录>"` 签字。

---

## 六、 可商用内置资产与风格谱复刻库

详尽授权溯源见 [assets-attribution.md](short-video-studio/references/assets-attribution.md)：
- **背景音乐 & 物理音效**：全部采用 `short-video-studio/assets/sound_vibe/` 下的 **CC0 1.0 真实录制 Freesound 音频**（均已完成波形起拍校准与 48kHz 统一处理），零低质电子合成音。
- **字幕字体**：优先加载 **思源黑体 / 思源宋体 / 得意黑（SIL OFL 1.1）** 及 **阿里妈妈数黑体（免费可商用）**，系统私有字体（如苹方/华文黑体/微软雅黑）全程拦截。
- **风格谱资产库**：内置 **6 大包装流派母版**与 **24 套量化「风格谱 (`style_01` ~ `style_24`)」**（含 `style_21` ~ `style_24` 四套 85%–100% 高空镜纯画面/少出镜风格，配套 24 套 `17³` CC0 `.cube` 3D LUT 与 28 套可商用字幕模板），详见 [STYLE_SPECTRUM_REPORT.md](short-video-studio/assets/style_spectrum/STYLE_SPECTRUM_REPORT.md)。

```bash
# 查看全库 24 套风格谱摘要
python3 short-video-studio/scripts/apply_style_spectrum.py --list

# 套用指定风格谱出片（门店默认 style_14，博主默认 style_11）
python3 short-video-studio/scripts/run_pipeline.py --account demo --raw-dir ./raw_media --track store --mode full --style-id style_14

# 导出某套风格谱的离线 HyperFrames 样板工程
python3 short-video-studio/scripts/apply_style_spectrum.py --style-id style_16 --export-hyperframes ./out_style_16_hyperframes

# （开发维护）基于外部视频样本提取风格特征预设
python3 dev_tools/analyze_style_spectrum.py --input-dir /path/to/reference_videos --workers 6
```
