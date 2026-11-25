---
task_id: T010
title: V6.0 Wave 3 · 升级 Web/SVG 物理弹簧动效、BGM 情绪呼吸混音与剪映多轨同步
owner: codex
branch: agent/codex/T010
allowed_files:
  - short-video-studio/scripts/capture_html_video.mjs
  - short-video-studio/scripts/render_template_overlays.py
  - short-video-studio/scripts/step8_mix_audio.py
  - short-video-studio/scripts/export_jianying_native.py
  - tests/test_v6_motion_and_audio.py
forbidden_files:
  - short-video-studio/scripts/step4_build_edl.py
  - short-video-studio/scripts/step6_packaging.py
  - short-video-studio/scripts/kinetic_choreographer.py
  - short-video-studio/scripts/qa_inspector.py
  - short-video-studio/scripts/render_mp4.py
report_path: TEAM/reports/T010_codex.md
test_commands:
  - "{python} -m unittest tests.test_v6_motion_and_audio -v"
  - "{python} -m unittest tests.test_v6_stage_and_camera tests.test_style02_polish tests.test_style03_17_dna tests.test_studio.ProductionPipelineV48Tests.test_v50_style02_kinetic_stage_and_broll_policy tests.test_studio.StudioTests.test_repo_privacy_and_zero_local_project_implants -v"
---

# T010：Web/SVG 物理弹簧动效、BGM 情绪呼吸混音与剪映多轨同步

> 开始前必查：`git branch --show-current` 必须为 `agent/codex/T010`。前提是 T008 已合入 `main`。先通读 `AGENTS.md` 与 [TEAM/V6_MILLION_EDITOR_SPEC.md](../V6_MILLION_EDITOR_SPEC.md)。

## 一、 背景与核心升级目标
1. **视觉升维（Web/SVG 物理动效引擎）**：突破 Pillow 线性位移的“程序贴图感”，在有本地 Chrome 时通过扩展 `capture_html_video.mjs` + `render_template_overlays.py` 渲染真正具备 **Spring 阻尼过冲、轻度动态透明度过渡、商业字体层级排布** 的透明 Alpha `.mov` 图层，并在缺浏览器环境下无损透明回落 Pillow；同时落地双赛道美术主题（`store_vibrant` 与 `business_editorial`）；
2. **声音导演（情绪呼吸混音）**：升级 `step8_mix_audio.py` 消费 `03b_director_plan.json` 的 `music_cues`，实现**开篇延迟进歌 (`delay_entry`)**、**反转爆点 0.4~0.6s 音乐骤空 (`silence_drop`)**、与段落推进 (`section_swell`)，零爆音且严格维持 EBU R128 `-14 LUFS` 总响度契约；
3. **剪映原生草稿对齐**：让 `export_jianying_native.py` 完整映射新增 4 原语变体（`negation_line` / `pillar_badge` / `concept_card`）进 6 轨可编辑真文字轨。

## 二、 必须实现的改造（严格限于 `allowed_files`）

### 1. 扩展 `short-video-studio/scripts/capture_html_video.mjs`
- 为现有 `capture_html_video.mjs` 增加安全透明 Alpha 视频直出能力（不影响现有 `--out out.mp4` H.264/BT.709 任何行为）：
  - 新增 `--alpha`（或 `--out` 后缀为 `.mov` 时默认输出 `ProRes 4444` / `qtrle` 透明 RGBA `.mov` 或可选 `libvpx-vp9` 兼容方案；优先采用 FFmpeg `qtrle` 或 `prores_ks -pix_fmt yuva444p10le`，确保本仓 `render_mp4.py` 可直接以带 Alpha `.mov` 无损叠加！）并设置浏览器截图 `omitBackground: true`；
  - 兼容 `--batch-json` 模式传递 `"alpha": true`，保证批处理多条花字只拉起 1 次 Chrome 实例，控制渲染速度在合理耗时内。

### 2. 升级 `short-video-studio/scripts/render_template_overlays.py`
**绝对红线**：保持 `render_kinetic_stage_item_png` 与 `render_animated_overlay_mov` 原有接口兼容，绝不破坏 `render_mp4.py` 调用契约及 `test_style02_polish` / `test_v50` / `test_style03_17_dna`！
1. **双赛道视觉主题 + 新原语样式支持**：
   - 在 `render_kinetic_stage_item_png` 内部根据 `elem.get("theme_profile")`（`store_vibrant` 默认保持既有风格不改；`business_editorial` 采用杂志黑金高对比：深磨砂胶囊底 `rgba(12,15,22,0.80)` + `1px` 细描边 `#FFFFFF30`、主白 `#F8FAFC`、重点金/红 `#EAB308` / `#DC2626`）；
   - 原生支持 `negation_line`（带红色/亮色斜杠或 `✕` 强标记质感）、`pillar_badge`（圆角亮色序数胶囊 `01/02/03`）、`concept_card`（结构分栏对比卡），保证中文字体与商业回退库 `100%` 无豆腐块。
2. **物理弹簧帧同步渲染器 (`web_spring` + Pillow Spring 兜底)**：
   - 封装一套自包含的确定性 HTML/SVG 动效模版（内联本地字体路径 `file://` 或按安全静态路由提供，严禁访问外网字体），实现真正弹簧/Cubic-Bezier 缓动函数 $f(t)$（`in` 入场具备快起微过冲与阻尼停稳、`dim_after_sec` 平滑降暗、`out` 干净收敛）；
   - 当检测到 `detect_browser_renderer().get("available")` 且素材适合浏览器直出时，通过 `capture_html_video.mjs --alpha`（支持多条打包批处理或缓存复用）产出透明 Alpha `.mov`；
   - **重要**：当无 Chrome 或超时或单测模式快速渲染时，自动无缝回落至**同等物理弹簧缓动数学曲线的 Pillow `.mov` 渲染器**（把 `render_template_overlays.py` 默认线性插值升级为同曲线物理阻尼插值！），确保“任何环境必出可播 Alpha `.mov`，且都有非线性弹簧速度曲线”。

### 3. 升级 `short-video-studio/scripts/step8_mix_audio.py`
1. **消费 `03b_director_plan.json` 的 `music_cues`**：
   - 在生成 `ducked_bgm`（即 BGM 双频段闪避包络阶段）前读取可选 `pipeline_dir / "03b_director_plan.json"`；
   - 实现精准平滑增益曲线 `apply_music_cues_envelope(...)`（作为纯函数导出便于单测）：
     * `delay_entry`：在 `[start_sec, end_sec]` 压低至接近静音或氛围微底（如 `-36dB`），随后在 `fade_ms`（如 `120ms`）内顺滑抬入正轨；
     * `silence_drop`：在指定反转窗口 `[start_sec, end_sec]` 前后采用 `50~80ms` 余弦平滑斜坡（`0` 阶跃爆音！），将 BGM 稳态衰减至少 `18~28 dB`（使人声乾音瞬间凸显张力），出点同步抬回；
     * `section_swell`：段落过渡温和抬升 `+2.0 ~ +3.0 dB`（受总响度限幅安全保护）。
   - 记录实际生效的 cues 数量与最深压低分贝到 `08_mix_metrics.json`，全片仍严格通过一次校准 EBU R128 达成 `-14 ± 0.8 LUFS` 与单声道相位安全。

### 4. 更新 `short-video-studio/scripts/export_jianying_native.py`
- 适配 `negation_line` / `pillar_badge` / `concept_card` 在 `kinetic_stage` 导出的颜色、底框圆角与 `track_lane` 映射，保持导出 **6 轨可编辑真文字草稿**，文字、字号、底色均可在剪映二改。

### 5. 新增单元测试 `tests/test_v6_motion_and_audio.py`
- 测试覆盖：
  1. `negation_line` / `pillar_badge` / `concept_card` 在 `business_editorial` 与 `store_vibrant` 双主题下渲染 PNG 均非空、高对比且零豆腐块；
  2. 验证物理弹簧缓动位移轨迹严格呈非线性（前段快速位移 > 末段阻尼收敛速度），`render_animated_overlay_mov` 降级回落 Pillow 与可选 Chrome Alpha 均可输出带透明通道的有效 `.mov`；
  3. 实测 `apply_music_cues_envelope` 对 stereo BGM 数组施加 `delay_entry` 与 `silence_drop`（持续 `0.5s`, `-26dB`），精确测量：窗口内 RMS 实测跌落 `>= 18 dB`、窗口首尾跃变幅度无阶跃爆音 (`max_sample_diff < 0.05`)；
  4. `export_jianying_native` 遇到包含上述新变体的 `kinetic_stage` 时可完整写入 `draft_content.json` 并保留所有段落可编辑文本。

## 三、 交卷步骤
1. 严格仅在 `allowed_files`（含 `TEAM/reports/T010_codex.md`）内改动；
2. 运行任务单全部 `test_commands` 确认 PASS；
3. 运行闸门检查：`"$HOME/.short-video-studio/venv/bin/python" dev_tools/team_ctl.py gate T010`，确认 `=== GATE PASS: T010 ===`；
4. 按 `TEAM/REPORT_TEMPLATE.md` 写入 `TEAM/reports/T010_codex.md`，在 `agent/codex/T010` 分支本地 `git commit`（严禁 `git push`）。
