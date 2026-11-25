---
task_id: T006
title: 将 style_03/17 视觉 DNA 与 style_01 三拍开场接入 step6_packaging
owner: deepseek
branch: agent/deepseek/T006
allowed_files:
  - short-video-studio/scripts/step6_packaging.py
  - tests/test_sprint3_step6_dna.py
forbidden_files:
  - short-video-studio/scripts/step4_build_edl.py
  - short-video-studio/scripts/render_template_overlays.py
  - short-video-studio/scripts/style_dna_profiles.py
  - short-video-studio/scripts/style_01_21_choreographer.py
  - short-video-studio/scripts/style_lock.py
report_path: TEAM/reports/T006_deepseek.md
test_commands:
  - "{python} -m unittest tests.test_sprint3_step6_dna -v"
  - "{python} -m unittest tests.test_style03_17_dna tests.test_style01_21_dna tests.test_studio.StudioTests.test_v30_flower_text_clean_no_flower_and_keep_edl tests.test_studio.StudioTests.test_repo_privacy_and_zero_local_project_implants -v"
---

# T006：将 `style_03/17` 视觉 DNA 与 `style_01` 三拍开场接入 `step6_packaging.py`

> 开始前必查：`git branch --show-current` 必须为 `agent/deepseek/T006`。先通读根目录 `AGENTS.md`。

## 一、 背景与接入目标
Sprint 2 已在以下两个模块就位了风格白盒决策与渲染契约：
- [style_dna_profiles.py](../../short-video-studio/scripts/style_dna_profiles.py)：提供 `resolve_style03_bar_layout`（区分通宽深红底条 `full_width_crimson_bar` vs 顶部引语卡 `quote_citation_card`、`anchor="top"`）与 `resolve_style17_frame_layout`（仅在对比语义触发 `dual_gold_hairline_grid`，单镜保持 `clean_fullscreen_gold`）；
- [style_01_21_choreographer.py](../../short-video-studio/scripts/style_01_21_choreographer.py)：提供 `plan_style_01_rhythm`（产出开场 3 拍错落斜标 `opening_beats`，含 `-5°` 倾角与 3 条互不重复短文本）。

但当前 [step6_packaging.py](../../short-video-studio/scripts/step6_packaging.py) 尚未消费它们，存在 3 处脱节：
1. **`style_03` 顶部引语卡错落中下屏**：第 1208–1215 行生成 `quote_citation_card` 时写死了 `init_y = 820`，未对齐顶部锚点 `anchor="top"`；通宽红底条宽度基准也仍按旧小卡片测算；
2. **`style_17` 缺少 `compare_mode` 驱动与双格对比拆分**：未根据台词/标题对比语义显式传入 `compare_mode: True/False` 及双格上下行文案，导致对比镜头没法稳定激活 2px 暖金双格框；
3. **`style_01` 开场 3 拍缺结构化节拍**：`store_poster_burst` 仅挂了单句标题与 `subtext`，未注入来自 `plan_style_01_rhythm` 的 3 拍节奏元数据 `opening_beats`。

## 二、 必须实现的改造（严格控制在 `allowed_files` 内）

### 1. 升级 `short-video-studio/scripts/step6_packaging.py`
仅针对 `style_01`、`style_03`、`style_17` 接入上述决策函数（严禁影响 `style_02` 动态花字舞台分支及禁用 `ding`/`chime` 音效红线）：

1. **`style_03` 接入 `resolve_style03_bar_layout`**：
   - 在生成 `style_03` 视觉元素（如开场 `center_burst_title`、中段 `quote_citation_card` / `flower_text`）时调用 `resolve_style03_bar_layout`：
     - 当 `layout["bar_mode"] == "quote_citation_card"` 时：强制使用顶部锚点策略（`anchor: "top"`、`bar_mode: "quote_citation_card"`），将初始 Y 坐标放在顶部避脸安全区内（如避开 `persistent_top_bar` 之后的顶部带 `SAFE_ZONE["top"] + 16 .. 340` 区间并执行 `_avoid_faces_for_pkg`，且不可落回中下屏 `>= 700`）；元素宽高同步参考 `layout["width_px"]`；
     - 当 `layout["bar_mode"] == "full_width_crimson_bar"` 时：写入 `bar_mode: "full_width_crimson_bar"`、`anchor: "center"`，宽度基准匹配画布安全居中（`>= 900` 且在 1080 安全区内、水平居中 540）。
2. **`style_17` 接入 `resolve_style17_frame_layout`**：
   - 判断对比语义（例如当前句原文/标题/副标中包含对比词或分隔符：`vs`/`VS`/`对比`/`改造前`/`升级后`/`升级前`/`老店`/`新店`/`前与后`/`｜`/`/`）：
     - 先用 `resolve_style17_frame_layout(..., compare_mode=...)` 决策 `layout_mode`；
     - 若命中对比语义（或 `layout["layout_mode"] == "dual_gold_hairline_grid"`），在产物元素上显式写入 `"compare_mode": True`、`"layout_mode": "dual_gold_hairline_grid"`，并将对比前/后项拆入 `"text_or_asset"` 与 `"subtext"`，且保证 `bbox` / `final_bbox` 高度足够双格对比框渲染（如 `h >= 200`）并通过 `check_bbox_in_safe_zone`；
     - 普通单镜显式写入 `"compare_mode": False`、`"layout_mode": "clean_fullscreen_gold"`，保持极净单镜排版。
3. **`style_01` 注入开场 3 拍斜标节拍**：
   - 在 `style_01`（尤其 `is_opening_poster_burst` 开场元素分支，第 1061–1095 行附近）可选调用 `plan_style_01_rhythm(audio_events.get("sentences") or [], fps=fps)`：
     - 提取其 `opening_beats`（3 拍错落时间窗、不重复文案、`tilt_deg=-5.0`、`anim_mode="store_stagger_burst"`），写入 `open_elem["opening_beats"]` 并设置 `open_elem["tilt_deg"] = -5.0`；
     - 保留 `open_elem` 原有全部必选字段（`id`/`kind`/`bbox`/`final_bbox`/`safe_zone_ok`/`aligned_sfx_or_accent_frame` 等），保证 100% 向后兼容；
4. **严格红线守卫**：
   - **绝对禁止修改 `style_02` 动态花字舞台路径**（`kinetic_choreographer` 分支）及其音效逻辑，严禁引入 `ding`/`chime` 音效；
   - 所有新增/调整的元素 `bbox`/`final_bbox` 必须通过 `check_bbox_in_safe_zone` 且满足 `read_hold_sec >= 1.35`（除非受总片长限制的原有例外）。

### 2. 新增单元测试 `tests/test_sprint3_step6_dna.py`
使用临时目录合成最小完整 `pipeline` 输入，真实运行 `step6_packaging.run_step6` 并联动调用 `render_template_overlays.render_template_packaging_png` 验证整条链贯通：
1. **`style_03` 联调验证**：验证 `quote_citation_card` 元素 `anchor == "top"` 且 `final_bbox["y"]` 处于顶部区（`< 450`）而非中下屏，主花字通宽安全居中，传给 `render_template_packaging_png` 可正确渲染深墨顶栏引语卡与深红底条；
2. **`style_17` 对比 vs 单镜联动验证**：造一组含“老店改造前 VS 升级后”的台词与一组普通单句“现炒锅气实测”，验证前者写入 `compare_mode=True` 且经 `render_template_packaging_png` 渲染出暖金细线框，后者 `compare_mode=False` 且渲染极净无框底板；
3. **`style_01` 3 拍开场注入验证**：验证开场 `store_poster_burst` 带有 3 拍 `opening_beats`（文案不重样、`tilt_deg == -5.0`），原有必选字段齐备；
4. **跨风格零污染**：验证 `style_02` 启用动能舞台时产物不含 `compare_mode` / `opening_beats` 污染、且 `sfx_cues` 无 `ding` / `chime` 音效；验证本任务修改源码零本机路径、面向用户文字无开发黑话。

## 三、 交卷步骤
1. 运行上方全部 `test_commands`，确保全部 PASS；
2. 运行闸门检查：
   `"$HOME/.short-video-studio/venv/bin/python" dev_tools/team_ctl.py gate T006`
   确认看到 `=== GATE PASS: T006 ===`；
3. 按 `TEAM/REPORT_TEMPLATE.md` 写入 `TEAM/reports/T006_deepseek.md`；
4. 在 `agent/deepseek/T006` 分支本地执行 `git add` + `git commit`（严禁 `git push`）。
