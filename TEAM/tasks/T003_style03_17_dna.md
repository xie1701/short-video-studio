---
task_id: T003
title: 复刻 style_03 与 style_17 视觉与包装 DNA
owner: codex
branch: agent/codex/T003
allowed_files:
  - short-video-studio/scripts/style_dna_profiles.py
  - short-video-studio/scripts/render_template_overlays.py
  - short-video-studio/assets/style_spectrum/style_spectrum_library.json
  - tests/test_style03_17_dna.py
forbidden_files:
  - short-video-studio/scripts/style_01_21_choreographer.py
  - short-video-studio/scripts/style_lock.py
  - short-video-studio/scripts/step4_build_edl.py
  - short-video-studio/scripts/render_mp4.py
report_path: TEAM/reports/T003_codex.md
test_commands:
  - "{python} -m unittest tests.test_style03_17_dna -v"
  - "{python} -m unittest tests.test_studio.StudioTests.test_v2_whitebox_style_spectrum_and_rulebooks_contract tests.test_studio.StudioTests.test_repo_privacy_and_zero_local_project_implants -v"
  - "{python} short-video-studio/scripts/style_lock.py --style-id style_03"
  - "{python} short-video-studio/scripts/style_lock.py --style-id style_17"
---

# T003：复刻 `style_03` 与 `style_17` 视觉与包装 DNA

> 开始前必查：`git branch --show-current` 必须为 `agent/codex/T003`。先通读根目录 `AGENTS.md`。

## 一、 问题背景与真值依据
当前 [render_template_overlays.py](../../short-video-studio/scripts/render_template_overlays.py) 中 `style_03`（`red_bar_dual_line`）与 `style_17`（`warm_gold_highlight`）的叠加视觉渲染仍停留在早期近似实现，与 [style_spectrum_library.json](../../short-video-studio/assets/style_spectrum/style_spectrum_library.json) 及 [template_blueprints.json](../../short-video-studio/assets/style_spectrum/template_blueprints.json) 记录的实测规格存在明显差距：

1. **`style_03`（暗调深度口播 · 深红实心底条双行大字 + 顶置引语卡）**：
   - 库实测特征：暗调影调（`y_mean_255=39.5`、`dark_ratio_pct=70.8%`）、低 B-roll 覆盖（`0.097`）、`persistent_top_bar=true`、`quote_citation_card=true`、`flower_text_spec.stroke_mode="none"`（无黑描边，使用阴影 `shadow=true`、`shadow_color="#000000E6"`）、蓝图 `font_candidates` 为 `SourceHanSerifCN-Heavy.otf`。
   - 现状不足：`render_template_overlays.py` 中 `red_bar_dual_line`（约第 976–987 行）只画了 780px 小圆角框并使用 `_sw(2)`/`sans_font`，未落实通宽深红底条排版、重点金黄字高对比与顶置引语卡布局，也缺少独立可读的 DNA 规格封装。
2. **`style_17`（暖金高对比强切 · 条件双格金线对比框 + 极净全屏）**：
   - 库实测特征：高动态暖金影调（`y_mean_255=96.2`、`dynamic_range_255=209.7`）、B-roll 覆盖 `0.158`、`flower_text_spec.stroke_mode="none"`、`giant_text_spec.mode="giant_dual_clash"`（主色 `#FFBF17`、辅色 `#FFFFFF`）、蓝图 `flower_layout="gold_hairline_plus_square_plate"`、`font_candidates` 为 `SourceHanSerifCN-Heavy.otf`。
   - 现状不足：`render_template_overlays.py` 中 `warm_gold_highlight`（约第 1329–1355 行）仍在底排文字和副排硬编码了 `stroke_w=2` 黑描边，且未区分「双镜对比语义（绘制上下暖金细线双格框）」与「单镜极净全屏硬切（保持清爽质感、不滥加边框）」。

## 二、 你必须实现的交付物（严格控制在 `allowed_files` 内）

### 1. 新增模块 `short-video-studio/scripts/style_dna_profiles.py`
设计轻量、纯数据驱动的视觉 DNA 规格与版式决策模块（零副作用，可供渲染器与后续质检复用）：
- 读取 `style_spectrum_library.json` 与 `template_blueprints.json`，导出稳定公开结构与函数：
  - `STYLE_DNA_PROFILES`：覆盖 `style_03` 与 `style_17` 的白盒实测规格字典（色彩亮度基线、零黑描边约束、字体候选、花字/引语卡/对比框触发规则）；
  - `get_style_dna_profile(style_id: str) -> Dict[str, Any]`：返回指定风格的不可变拷贝/视图，未知风格返回基础安全结构而非崩溃；
  - `resolve_style03_bar_layout(main_text: str, sub_text: str = "", elem_kind: str = "", display_role: str = "") -> Dict[str, Any]`：
    - 判定是「通宽深红实心底条双行主字（`bar_mode="full_width_crimson_bar"`）」还是「顶部金句引语卡（`bar_mode="quote_citation_card"`）」，并输出宽度（通宽模式下 `>= 960px` 适配 1080 画布安全区）、深红底色 `#B91C1C` / `(185, 28, 28, 238)`、双行主/次配色（纯白 `#FFFFFF` + 暖黄 `#FFF589`）、零描边（`stroke_px=0`）与阴影参数；
  - `resolve_style17_frame_layout(main_text: str, sub_text: str = "", elem_kind: str = "", compare_mode: Optional[bool] = None) -> Dict[str, Any]`：
    - 当 `compare_mode=True`、或 `elem_kind == "center_burst_title"` 且含对比分隔符（如 `vs` / `VS` / `对比` / `｜` / `/`）时，输出上下双格暖金细线对比框参数（`layout_mode="dual_gold_hairline_grid"`，金线色 `#FFBF17`/`#FFDE00`、线宽 `<= 3px`、方角深色衬底 `#000000B2`）；
    - 普通单镜情况下输出极净单镜模式（`layout_mode="clean_fullscreen_gold"`，不绘冗余外框，严格执行 `stroke_px=0` + 投影）。

### 2. 精准升级 `short-video-studio/scripts/render_template_overlays.py`
在 `render_template_packaging_png` 中，仅改造以下两处风格渲染逻辑（严禁改动其它 22 套风格分支与全局绘制基础规则）：
1. **`red_bar_dual_line`（`style_03`）**：
   - 调用 `style_dna_profiles.resolve_style03_bar_layout`：
   - 默认主视觉绘制贴合 1080p 安全宽度的通宽深红实心底条（无夸张圆角、深红底纯正饱满），主字体优先采用 `serif_font`（`SourceHanSerifCN-Heavy.otf`）或调用方传入 `font_path`，严格遵守 `flower_text_spec.stroke_mode == "none"`（不强行加黑描边，保持纯投影清晰度）；
   - 当决策为顶部引语卡模式（如 `quote_citation` / `persistent_top_bar` 相关角色或要素类型）时，渲染顶部精简深红/深墨引语条；
2. **`warm_gold_highlight`（`style_17`）**：
   - 调用 `style_dna_profiles.resolve_style17_frame_layout`：
   - 清理硬编码 `stroke_w=2` 黑描边，文本层遵守 `_sw(...)`（在 `style_17` 下应为 `0` 描边，靠暖金微弱外发光/柔和暗影保证对比度）；
   - 对比模式下绘制清爽的上下暖金细线（1~3px）双格对比框 + 方角深黑半透字幕底板（`#000000B2`，`radius=0`）；普通单镜模式下仅保留干净暖金主字与方角底板，绝不强行套上大框。

### 3. `short-video-studio/assets/style_spectrum/style_spectrum_library.json`（仅在需要补充只读描述或附加字段时）
- **铁律与兼容性红线（极其重要）**：
  - `template_blueprints.json` 与 `style_lock.py` **不在你的 `allowed_files` 内**！因此**绝对禁止**修改 `style_spectrum_library.json` 中 `style_lock.py` 比对的核心同步字段（如 `subtitle_spec`、`color_and_lut`、`broll_overlay_ratio_target`、`transition_and_packaging`、`packaging_techniques`、`pip_composition_spec`、`giant_text_spec`、`source_jianying_font`、`version`、`plain_cn_pitch`）；
  - 若需要补充细粒度花字/对比框规则，仅允许在 `style_03`、`style_17` 下安全新增独立字段（如 `packaging_dna_rules`），且不得引入任何技术黑话或隐私字符串。若现有字段已足够驱动 `style_dna_profiles.py`，甚至可以不改动 JSON 文件！

### 4. 新增单元测试 `tests/test_style03_17_dna.py`
用代码直接验收（含像素级校验）：
1. `style_dna_profiles` 返回值完整、字段自洽且不破坏未知 `style_id` 回退；
2. 渲染 `style_03` PNG：校验透明 RGBA 图层尺寸适配 1080 安全宽度、通宽深红实心底条中心像素 RGB 落入深红通道且左右对称、字体字形非空、顶部引语卡变体能正确生成且尺寸比例区分明显；
3. 渲染 `style_17` PNG：对比模式生成上下双格暖金细线结构（能检出上下分割金线非零像素），普通单镜模式无大面积封闭对比框（边缘透明干净），且文字描边尊重零黑描边约束；
4. 验证 `style_lock.py --style-id style_03` 与 `--style-id style_17` 退出码均为 `0`；
5. 验证其它对照风格（如 `style_01`、`style_02`）渲染结果完全不受影响。

## 三、 交卷步骤
1. 在 Worktree 根目录运行列出的全部 `test_commands`，确保全部 PASS；
2. 运行闸门检查：
   `"$HOME/.short-video-studio/venv/bin/python" dev_tools/team_ctl.py gate T003`
   确认看见 `=== GATE PASS: T003 ===`；
3. 按 `TEAM/REPORT_TEMPLATE.md` 写入 `TEAM/reports/T003_codex.md`；
4. 在 `agent/codex/T003` 分支执行本地 `git add` + `git commit`（严禁 `git push`）。
