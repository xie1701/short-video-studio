---
task_id: T013
title: V6.5 顶配百万爆款精剪跃迁 · 一键V6闭环 + 元素级动效避重 + 审片精修 + 探店呼吸与微锚点
owner: deepseek
branch: agent/deepseek/T013
allowed_files:
  - short-video-studio/scripts/run_pipeline.py
  - short-video-studio/scripts/director_plan.py
  - short-video-studio/scripts/kinetic_choreographer.py
  - short-video-studio/scripts/step6_packaging.py
  - short-video-studio/scripts/render_template_overlays.py
  - short-video-studio/scripts/export_jianying_native.py
  - short-video-studio/scripts/aesthetic_critic.py
  - tests/test_v65_pipeline_and_critic.py
  - tests/test_v65_element_motion.py
forbidden_files:
  - short-video-studio/scripts/qa_inspector.py
  - short-video-studio/scripts/render_mp4.py
  - short-video-studio/scripts/composition_profile.py
  - short-video-studio/scripts/step4_build_edl.py
  - short-video-studio/scripts/step8_mix_audio.py
  - short-video-studio/scripts/capture_html_video.mjs
report_path: TEAM/reports/T013_deepseek.md
test_commands:
  - "{python} -m unittest tests.test_v65_pipeline_and_critic tests.test_v65_element_motion -v"
  - "{python} -m unittest tests.test_director_plan tests.test_composition_profile tests.test_v6_stage_and_camera tests.test_v6_motion_and_audio tests.test_aesthetic_critic tests.test_style02_polish tests.test_studio.ProductionPipelineV48Tests.test_v50_style02_kinetic_stage_and_broll_policy tests.test_studio.StudioTests.test_repo_privacy_and_zero_local_project_implants -v"
  - "{python} dev_tools/bench_talking_head.py --smoke"
  - "{python} -m unittest discover -s tests"
---

# T013：V6.5 顶配百万爆款精剪跃迁（一键闭环 + 元素级编舞 + 审片精修 + 探店呼吸）

> 开始前必查：`git branch --show-current` 必须为 `agent/deepseek/T013`。  
> 先完整阅读根目录 `AGENTS.md` 与 [TEAM/V6_MILLION_EDITOR_SPEC.md](../V6_MILLION_EDITOR_SPEC.md)。本任务在 V6.0 已合入 `main`（552 项单测全过）基础上向顶配人工精剪（9.5/10）推进，严格遵守 `allowed_files` 与 `forbidden_files`，**严禁**修改 `qa_inspector.py`、`render_mp4.py`、`composition_profile.py`、`step4_build_edl.py`、`step8_mix_audio.py`、`capture_html_video.mjs`。

---

## 一、 执行节奏（同一分支，分 A/B/C/D 四段提交）

1. **阶段 A（`run_pipeline.py` 一键 V6 全自动闭环）** → 跑通阶段 A 新增单测及 `test_studio` 相关用例 → 本地 `git commit -m "feat(T013-A): wire V6 composition, director, native draft, and critic into run_pipeline"`；
2. **阶段 B（元素级 HTML/SVG+Pillow 动效 + 短句底字幕自动避重 + 剪映兼容）** → 跑通 `tests.test_v65_element_motion`、`test_v6_motion_and_audio`、`test_v6_stage_and_camera` → 本地 `git commit -m "feat(T013-B): add element-level choreography and duplicate short-caption suppression"`；
3. **阶段 C（审片自动修订闭环 `--auto-critic-revise` + 反转主标题补全）** → 跑通 `tests.test_v65_pipeline_and_critic`、`test_aesthetic_critic`、`test_director_plan` → 本地 `git commit -m "feat(T013-C): support one-shot auto critic revision and contrast headline fallback"`；
4. **阶段 D（探店高潮克制音乐呼吸 + 商业长段安全微锚点 `editorial_ticker`）** → 运行 `dev_tools/team_ctl.py gate T013`（含全量 `unittest discover -s tests`）→ 看见 `=== GATE PASS: T013 ===` 后按 `TEAM/REPORT_TEMPLATE.md` 写回执并本地提交（**禁止 `git push`**）。

---

## 二、 必须实现的功能规格（逐阶段零歧义设计）

### 阶段 A · 一键 V6 流水线串联（`short-video-studio/scripts/run_pipeline.py`）
1. **新增 CLI 参数（均保持向后兼容默认，不影响非 `style_02` 任何风格）**：
   - `--no-v6-director`（默认 `False`）：显式关闭 V6 构图/导演阶段，强制退回 V5 既有规则路径；
   - `--director-plan`（`Optional[str]`）：可选外部 Agent 编写的 `03b_director_plan.json` 路径；
   - `--director-review-pause`（`action="store_true"`）：生成 `01b_composition_profile.json` 与 `03b_director_tasks.json` 后停止并打印提示，供大模型编写高级总谱；
   - `--auto-critic-revise`（`action="store_true"`）：初版出片审片后若需要修订且未达修订上限，自动回写总谱并重剪 1 轮。
2. **前置 V6 感知与导演计划自动调度**（在 `run_visual_to_delivery` 进入 `step4` 前、且 `style_id == "style_02"`、`not no_v6_director` 且 `03_audio_events.json` 存在时执行）：
   - 先调用 `composition_profile.build_composition_profile(pipeline_dir, overwrite=False)` 生成 `01b_composition_profile.json`（若缺 `01_manifest.json` 跳过，确保不破坏极简 mock 测试）；
   - 调用 `director_plan.build_director_tasks(pipeline_dir, style_id=style_id)` 写出 `03b_director_tasks.json`；
   - 若指定了 `director_plan` 文件：读取 JSON 并用 `director_plan.validate_director_plan` 执行防幻觉与约束校验，**校验失败必须直接报错停止**（不得静默忽略）；校验通过后将 `sanitized_plan` 写入 `pipeline_dir / "03b_director_plan.json"`；
   - 若开启 `director_review_pause` 且未传外部 `director_plan`：提前返回 `{"paused_for_director": True, "tasks_path": str(pipeline_dir / director_plan.TASKS_FILENAME)}`，供人工/Agent 介入后再续跑；
   - 否则：若目标目录已有合法 `03b_director_plan.json` 直接复用；若缺失或校验失败，调用 `director_plan.generate_deterministic_director_plan` 并校验写入 `03b_director_plan.json`（注意 `keep_edl=True` 模式下若已有 `03b_director_plan.json` 则不推翻重构）。
3. **导出与审片闭环自动接入 `_render_and_verify_only`**：
   - 在 V6 启用（或存在 `03b_director_plan.json`、`kinetic_stage` 已启用）且 `"jianying"` 允许时，同步安全调用 `export_jianying_native.export_native_draft(pipeline_dir)`，将返回值挂入返回字典 `"jianying_native"`（异常只告警记失败状态，不污染无音频/极简单元测试主返回）；
   - 在 `inspect_video` 完成且 `03b_director_plan.json` 或 `06_packaging.json` 存在时，自动运行 `aesthetic_critic.build_aesthetic_review_package(pipeline_dir)` 与 `aesthetic_critic.evaluate_review_heuristics(pipeline_dir)`，将摘要挂入返回值 `"aesthetic_review"`。
4. **函数签名兼容**：`run_visual_to_delivery` 与 `_render_and_verify_only` 仅追加具默认值的新关键词参数；非 `style_02` 风格的调用路径和输出保持 100% 不变。

---

### 阶段 B · 元素级物理编舞动效 + 短句底字幕自动避重
涉及文件：`render_template_overlays.py`、`kinetic_choreographer.py`、`step6_packaging.py`、`export_jianying_native.py`。

1. **短句底字幕自动避重（`kinetic_choreographer.py` + `step6_packaging.py`）**：
   - 更新 `business_caption_items(audio_events, subtitles, fps, highlight_items=None)`（默认 `highlight_items=None` 严格兼容旧调用）：
     * 对每条候选 `base_caption` 底字幕 `L`，检查是否存在前景非静帧高光项 `H`（不含 `bridge_bar` / `pillar_badge` / `editorial_ticker`），满足以下**全部条件**时抑制该条重复底字幕（不加入返回 `items`）：
       1. `L` 清洗空白与标点符号后显示长度 `<= 7` 字；
       2. `L` 与 `H` 时间重叠比例 `overlap_sec / (L_end - L_start) >= 0.50`；
       3. 对 `L` 与 `H` 的文字分别剔除 `✕✓≠→:：· `、标点及常见句首虚词/连词（`也/都/就/还/并/而/又/但/可`）后，短串与主串**精确相等**或字符重叠比例 `>= 0.85`（覆盖 `✕ 不是被骗`↔`不是被骗`、`✕ 不是行情差`↔`也不是行情差` 等重词场景）。
     * **安全保底红线**：若 `L` 长于 7 字（如 `看到做法先找他的前提` vs 大字 `先找他的前提`），必须保留 `L` 作为底字幕！且全片 `kinetic_stage` 保留的 `items` 合并后对口播总时长的覆盖率必须继续稳定 `>= 85%`（严守 `qa_inspector` 第 5b 项 `>= 80%` 门槛）。
   - `step6_packaging.py` 在 `business_stage` 分支中，先把 `kinetic_stage_plan["items"]` 作为 `highlight_items` 传入 `business_caption_items(...)`，记录 `suppressed_duplicate_captions` 计数进 `kinetic_stage_plan["stats"]`。

2. **四大元素级物理动效编舞（`kinetic_choreographer.py` + `render_template_overlays.py`）**：
   > **严格接口约束**：`render_mp4.py` 不可改动，它在第 2766 行依靠 `motion_mode` 非空触发 `render_animated_overlay_mov(elem=..., out_mov=..., style_id=..., duration_sec=..., fps=..., font_candidates=..., fallback_static_img=...)`（期望返回 `(mov_path, width, height)` 且 `.mov` 为偶数宽高、透明 `argb` / `qtrle`）。所有元素级编舞必须通过 `elem` 上的声明字段自动在 `render_animated_overlay_mov`（以及 Chrome `capture_html_video.mjs --alpha` + 无浏览器 Pillow 兜底两套实现）内部完成。

   - **元数据派发**（`kinetic_choreographer.py`）：
     * 在 `_director_items` 中为各组件写入 `"element_choreo"`：
       - `negation_line` → `"element_choreo": "strike_slash"`
       - `concept_card` → `"element_choreo": "stagger_card"`
       - 含 `>= 2` 位连续阿拉伯数字（如 `200` / `2000`）的 `hero_lime` → `"element_choreo": "number_roll"`
       - `framework_pillar` 的全部条陈提前统计总数 `pillar_total` 与当前顺序 `pillar_index`（1..N），写入 `pillar_badge` 与对应标题项上，`"element_choreo": "pillar_progress"`。
   - **四套元素级渲染器（`render_template_overlays.py`）**：Chrome SVG（走既有 `capture_html_video.mjs --alpha` + `.web-spring-*.mov` 哈希缓存）与无 Chrome 时的 Pillow 兜底**共享同一帧分拍时间与几何布局函数**，保证离线单测与线上有浏览器环境效果一致、且零外网依赖：
     1. **`strike_slash`（`negation_line` 动态红线划除）**：
        - `0.00 ~ 0.16s`：深色胶囊底框与白字弹簧弹入到位；
        - `0.16 ~ 0.34s`：鲜红 `✕` 两笔依次干脆划出，同时在否定文字（不含 `✕`）中部纵高位置从左向右线性+缓动绘出鲜红微透划除线（`alpha ≈ 0.85`、线宽 `2~4px`，保证白字依旧清楚可读），`>= 0.34s` 保持划除完成态直到淡出。
     2. **`stagger_card`（`concept_card` 三拍错峰揭示）**：
        - `0.00 ~ 0.16s`：深色胶囊底框 + 顶栏标题（如 `海底捞的师徒制`）弹簧入场；
        - `0.16 ~ 0.42s`：左栏反面项（如 `✕ 师徒两个字`）从左轻微滑入并显影（此时右栏与竖分割线尚未显示或 alpha ≈ 0）；
        - `0.42s+`：中间分隔竖线自上而下展开，同时右栏正面结论（如 `✓ 分到钱`）以亮金/微过冲弹性弹出，保持至退场。
     3. **`number_roll`（`hero_lime` 核心数字快滚砸屏）**：
        - 解析 `text_or_asset` 中首段核心整数 `N`（前缀 + `N` + 后缀，如 `负债` + `200` + `万+`）；
        - `0.00 ~ 0.26s` 分 5 拍缓动递进显示滚动数字（如从 `0.15*N → 0.45*N → 0.72*N → 0.91*N → N` 避免数字闪烁越界框，先用终值同字号预估槽位居中排版，防止整行左右抖动），`0.26s` 定格真实 `N` 并伴随微过冲停稳。
     4. **`pillar_progress`（`pillar_badge` 结构进度格）**：
        - 读取 `elem.get("pillar_index")` 与 `elem.get("pillar_total")`（仅当 `total >= 2` 时绘制），在序数胶囊内下缘或胶囊紧下方绘 `pillar_total` 个横排 `3px` 细格：第 `1..pillar_index` 格为实心琥珀金（或 `#BCFF00` 随赛道主题走），其余格为 `rgba(255,255,255,0.28)`；`render_kinetic_stage_item_png` 静帧兜底渲染时也一并绘出最终进度格（并保证顶左角 `(0,0)` alpha 仍为 0 满足圆角检查）。

3. **剪映原生草稿对齐（`export_jianying_native.py`）**：
   - 安全兼容 `editorial_ticker` 变体与 `pillar_index / pillar_total` 元数据（如 `editorial_ticker` 用小号胶囊底框落在其 `x_norm / y_norm` 写入 6 轨原生可编辑文本轨道），不得因新增字段报错或丢失文本。

---

### 阶段 C · 真导演自动审片精修闭环 & 反转主标题补全
涉及文件：`run_pipeline.py`、`aesthetic_critic.py`、`director_plan.py`。

1. **一次幂等自动审片重跑闭环（`run_pipeline.py`）**：
   - 当 `auto_critic_revise=True` 且存在初版 `10b_aesthetic_review.json` 与 `03b_director_plan.json`：
     * 若初版 `not review["passed"]` 或存在 `patch_suggestions` 且总谱尚无 `critic_revised=True`：
     * 调用 `aesthetic_critic.apply_critic_patches_to_director_plan(pipeline_dir, review)`；
     * 若返回 `applied=True`：记录 `critic_revision = {"applied": True, "before_score": review["mean_score"], ...}`，随后从 `step4_build_edl` 到 `step9 -> render_mp4 -> export -> qa_inspector -> aesthetic_critic` 自动完整重剪 **1 轮**，并把前后评分差写入返回字段 `"critic_revision"`；若二次尝试仍不通过，严格守住 `critic_revised=True` 锁不再循环重跑。
2. **审片能力增强（`aesthetic_critic.py`）**：
   - 在 `score_aesthetic_review` 中新增两项可测指标（写入 `metrics`）：
     * `duplicate_short_captions`：统计商业模式下与同时段前景高光（`<=7` 字）文本高度重合且长度 `<=7` 字的多余 `base_caption` 数量；若 `> 0`，在 `info_distillation` / `rhythm_breathing` 合理扣分并给出 `suppress_duplicate_captions` 建议标签；
     * `element_choreo_ratio`：统计高光项中包含 `element_choreo` 的比例，细化 `motion_craft` 打分区分度（普通整卡动效 vs 元素级物理编舞）；
   - 在 CLI 入口增加可选 `--review-file <path>` 参数用于 `apply` 命令消费外部多模态大模型手写的 `10b_aesthetic_review.json`，经 `validate_director_plan` 校验后原子落盘 `03b_director_plan.json`。
3. **反转大字主标题补全（`director_plan.py`）**：
   - 修复 `_propose` 中当命中 `negation_strike` 且存在 `contrast`（例如一句内完整对比：`不是人的问题，是我们想事情的方式被固化了`）但 `_short_conclusion(contrast["accepted"], sentences, allow_tail=False)` 返回空串时：先安全尝试 `_short_conclusion(contrast["accepted"], sentences, allow_tail=True)`，若通过 `_validate_copy` 校验则填入 `headline`（例如得到可溯源短结论 `方式被固化了`），消除商业片中段反转幕主标题留空问题；同时保证 `concept_split_card` 继续严格要求正反双边齐全、绝不出瘸腿单边卡。

---

### 阶段 D · 探店高潮音乐呼吸 + 商业长叙述克制微锚点
涉及文件：`director_plan.py`、`kinetic_choreographer.py`、`render_template_overlays.py`。

1. **探店高潮克制音乐呼吸（`director_plan.py`）**：
   - 当 `store is True`（`theme_profile == "store_vibrant"`）、全片 `total >= 20.0`、且 `_music_cues` 当前未产出任何 `silence_drop` 时：
     * 从非开篇（`index > 0`）高光候选幕中挑选核心揭晓幕（优先首个 `num_anchor_dim_overlay` 或 `list_build_gather_dim_overlay`），在其起点前插入 **1 条克制 `silence_drop`**（`start_sec = max(1.0, min(act_start - 0.08, total - 0.40))`，时长 `0.35s`，`depth_db = -20.0`，`fade_ms = 65`）；
     * 可选在随后的主体展开段（高光紧接着至少 `1.5s` 叙述句）补充 1 条温和 `section_swell`（`+2.2 dB`，时长 `<= 3.5s`，`fade_ms = 180`），不得与 `silence_drop` 重叠。确保所有生成 cue 严格通过 `validate_director_plan`。
2. **商业长叙述克制安全微锚点 `editorial_ticker`（`kinetic_choreographer.py` + `render_template_overlays.py`）**：
   - 在 `theme == "business_editorial"` 下扫描整条时间轴：若两个相邻激活锚点之间出现连续 `>= 10.0s` 的纯 `plain_bridge / clean_breath` 长叙事窗口（且避开前 `3.0s` 开场、避开紧随高光后 `1.5s` 内留白）：
     * 在长段中间挑 1 句信息实词最强的 `plain_bridge` 句，使用 `director_plan._short_conclusion`（`limit=5`）提炼 2~5 字真词（如 `百度文库` / `现金流` 等原句实词），不通过溯源校验则放弃；
     * **绝对避脸避 UI 护栏**：读取该句对应 `composition_for_sentence`（含 `face_bbox` 与 `safe_center_shift_x`），仅当在顶栏左下/左上/右上或侧边安全带能算出与人脸框及底部 `y_norm < -0.62` 字幕区 **完全不相交（`IoU == 0.00`）** 的中心位置 `(tx, ty)` 时，生成一个极其克制的小号静音胶囊 `variant="editorial_ticker"`（`font_size_units=5.2` / `30px`、`sfx_role_override=None`、`z_layer="foreground"`、`element_choreo="ticker_fade"`）；若无法保障 `IoU == 0.00` 避脸安全位置，**宁缺毋滥直接放弃生成**；
     * 在 `kinetic_choreographer` 统计中标记 `"ticker": True`，`aesthetic_critic` 不得把 `editorial_ticker` 计入主要高光覆盖率或 7 字警告惩罚（实际也严格 `<= 5` 字）。
   - `render_template_overlays.py` 中对 `variant == "editorial_ticker"` 渲染极简微暗灰磨砂小胶囊（半透黑底 `rgba(12,15,22,0.74)` + `1px` 微亮框 + `#E2E8F0` 小号精细字体），入出为柔和淡入淡出（无过冲弹跳、零抢戏）。

---

## 三、 必须新增的单元测试
1. **`tests/test_v65_element_motion.py`**：
   - 验证 `negation_line` (`strike_slash`) 在无 Chrome 兜底与（如可用）Chrome 路径下输出有效带 Alpha `qtrle` `.mov`：测量 `0.05s` vs `0.35s` 图像帧，红色通道高亮划除像素在 `0.35s` 显著高于 `0.05s`；
   - 验证 `concept_card` (`stagger_card`) 三拍时序：采样 `0.06s`（仅标题区非空）、`0.24s`（左侧 `✕` 列非空且右侧 `✓` 区域尚未点亮或极低 alpha）、`0.55s`（左右双栏均已清晰显影）；
   - 验证 `hero_lime` (`number_roll`) 在 `0.03s`、`0.14s`、`0.32s` 三个采样帧渲染像素存在明确演变（数字滚动而非静帧），终稳态清晰可读；
   - 验证 `pillar_badge` (`pillar_progress`) 的 `1/3` vs `3/3` PNG 底边进度格金色像素占比依次严格递增，且四角透明底保持完好；
   - 验证 `editorial_ticker` 渲染精致小胶囊，且商业长叙述仅在 `>=10s` 纯过场且避脸 `IoU==0.0` 时生成、静音且不触发音效。
2. **`tests/test_v65_pipeline_and_critic.py`**：
   - 验证 `run_visual_to_delivery(..., style_id="style_02")`（mock 媒体与 `render_mp4` 等重渲染环节或轻量合成端到端）自动产生 `01b_composition_profile.json`、`03b_director_plan.json`、`jianying_native` 与 `10b_aesthetic_review.json`；非 `style_02` 路径及 `--no-v6-director` 保持旧行为零变化；
   - 验证 `--director-plan` 遇到含 `hallucinated_token` 非法总谱直接报错退出不污染工程；`--director-review-pause` 准确停在出任务包；
   - 验证 `--auto-critic-revise` 遇过满/可优化总谱时自动修 1 轮并从 `step4` 重跑后达标，二次调用因 `critic_revised=True` 幂等跳过；
   - 验证 `business_caption_items` 遇到 `✕ 不是被骗` 时自动隐藏同名短底字幕 `不是被骗`，而长句 `看到做法先找他的前提` 完整保留；
   - 验证探店 `store_vibrant` `>=20s` 离线总谱自动输出合规 `silence_drop`（`0.35s`, `-20dB`）且过 `validate_director_plan`；商业长对比句 `不是人的问题，是我们想事情的方式被固化了` 自动带出非空可溯源主标题。

---

## 四、 交卷步骤
1. 严守 `AGENTS.md` 五条红线与本单 `allowed_files` / `forbidden_files`，零个人绝对路径、零内部项目代号；
2. 跑通全部 `test_commands` 并运行 `"$HOME/.short-video-studio/venv/bin/python" dev_tools/team_ctl.py gate T013`，确认看见 `=== GATE PASS: T013 ===`；
3. 按 `TEAM/REPORT_TEMPLATE.md` 分 A/B/C/D 四段写清改动脉络、实测结果与接口说明到 `TEAM/reports/T013_deepseek.md`，并在 `agent/deepseek/T013` 分支执行本地 `git add` + `git commit`（**禁止 `git push`**）。
