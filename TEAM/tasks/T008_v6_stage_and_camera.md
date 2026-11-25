---
task_id: T008
title: V6.0 Wave 2 · 接入泛口播 10 原语双轨舞台、虚拟多机位与克制音效
owner: codex
branch: agent/codex/T008
allowed_files:
  - short-video-studio/scripts/kinetic_choreographer.py
  - short-video-studio/scripts/step4_build_edl.py
  - short-video-studio/scripts/step6_packaging.py
  - tests/test_v6_stage_and_camera.py
forbidden_files:
  - short-video-studio/scripts/render_template_overlays.py
  - short-video-studio/scripts/step8_mix_audio.py
  - short-video-studio/scripts/export_jianying_native.py
  - short-video-studio/scripts/qa_inspector.py
  - short-video-studio/scripts/render_mp4.py
report_path: TEAM/reports/T008_codex.md
test_commands:
  - "{python} -m unittest tests.test_v6_stage_and_camera -v"
  - "{python} -m unittest tests.test_director_plan tests.test_composition_profile tests.test_style02_polish tests.test_ab_cut_trim tests.test_sprint3_step4_rhythm tests.test_sprint3_step6_dna tests.test_studio.ProductionPipelineV48Tests.test_v50_style02_kinetic_stage_and_broll_policy tests.test_studio.StudioTests.test_repo_privacy_and_zero_local_project_implants -v"
---

# T008：接入泛口播 10 原语双轨舞台、虚拟多机位与克制音效

> 开始前必查：`git branch --show-current` 必须为 `agent/codex/T008`。前提是 T007（`director_plan.py`）和 T009（`composition_profile.py`）已合入 `main`。先通读 `AGENTS.md` 与 [TEAM/V6_MILLION_EDITOR_SPEC.md](../V6_MILLION_EDITOR_SPEC.md)。

## 一、 背景与核心升级目标
在保留既有 `style_02` 探店全部表现（包括 `test_v50` 与 `test_style02_polish` 零回归）的基础上，将 [kinetic_choreographer.py](../../short-video-studio/scripts/kinetic_choreographer.py)、[step4_build_edl.py](../../short-video-studio/scripts/step4_build_edl.py) 与 [step6_packaging.py](../../short-video-studio/scripts/step6_packaging.py) 接通 `03b_director_plan.json` 与 `01b_composition_profile.json`：
1. **双轨减法 & 4 个新原语落地**：高光幕不再强拆口播长句，而是优先采用导演总谱精炼的 2~6 字 `visual_copy`，并新增 `negation_strike`、`framework_pillar`、`concept_split_card`、`clean_breath`；
2. **构图自感知避遮挡**：当 `01b_composition_profile.json` 裁决 `allow_behind_person == False` 时，禁止在人物大头后放置 `behind_person` 弹幕，高光字改落 `safe_fg_y_norm` 胸口安全区；
3. **纯单口播虚拟 3 机位调度**：无实拍 B-roll 时，依据导演 `camera_intent`（`steady_hold` / `slow_push_115` / `side_shift_card`）在 `04_edl.json` 通过合法 `vf_extra` / `punch_in_zoom` / `ken_burns` 形成视觉呼吸，同时 `a_roll` 严守 `1.0x` 原速与 0 冻帧；
4. **克制高辨识音效**：`step6` 根据全片总时长与导演重点将 `kinetic_stage` 主动音效收敛在 **`6 ~ 14` 条**高辨识节点（普通过渡与 `clean_breath` 静音留白），绝不每 1~2 秒连响 `swish`。

## 二、 必须实现的改造（严格限于 `allowed_files`）

### 1. 升级 `short-video-studio/scripts/kinetic_choreographer.py`
保持现有 `plan_kinetic_stage(audio_events, style_id="style_02", pipeline_dir=None, fps=DEFAULT_FPS, broll_evidence_by_sent=None)` 签名与兼容返回值字段不变：
1. **接入总谱与构图画像**：
   - 当传入 `pipeline_dir`（或通过可选关键词参数传入 `director_plan` / `composition_profile`）时，安全加载 `director_plan.load_or_build_director_plan` 与 `01b_composition_profile.json`（若找不到则自动离线生成回退或无影响保持默认）；
   - 从 `director_plan["theme_profile"]` 与 `01b_composition_profile` 读取 `theme_profile` 与 `allow_behind_person` / `safe_fg_y_norm`，写入每个渲染项（如 `"theme_profile": theme_profile`）及返回顶层 `"theme_profile"`、`"director_source"`。
2. **实现新增 4 类泛口播高光原语渲染布局（均兼容 `render_template_overlays` 既有变体与 T010 扩展变体）**：
   - `negation_strike`：主钩词（`headline` 如 `负债 200万+`）走 `hero_lime` / `red_slam`，下方排比否定词（`✕ 不是被骗`、`✕ 不是行情差`）按词级时间依次亮出（`variant="negation_line"` 或自带合理样式字段 `fg_hex` / `stroke_hex` / `motion_mode="slide_in_out"`，并在每项上标注 `"anim_style_v6": "negation_strike"`），单条严控 `<= 7` 字；
   - `framework_pillar`：序数徽标（如 `01` / `02` / `03`，`variant="pillar_badge"` + 紧凑亮色徽标底）配右侧或上方核心短句（`variant="hero_white"`/`"hero_lime"`）；
   - `concept_split_card`：极简因果/对比结构卡（上/右区高亮结论或 `A ≠ B → C` 两段拆解，`variant="concept_card"`，配套在 `step4` `side_shift_card` 避让区同框，严格不撞人脸）；
   - `clean_breath`：只放轻柔单行 `bridge_bar` 或完全不加高光花字，**严格禁止生成任何 `sfx_role`**。
3. **构图护栏（向后兼容 `test_v50` 与 `test_style02_polish`）**：
   - 当 `01b_composition_profile` 明确给出 `allow_behind_person == False` 时，原应进 `hero_anchor_plus_danmaku` 的镜头自动把弹幕切换为前景安全带（`z_layer="foreground"`、`y_norm` 对齐 `safe_fg_y_norm` 周围不叠压红字 `red_slam`），彻底消除大头遮字；当没有 `closeup_chest_stage` 约束时（包括既有探店单测 `test_v50` 与 `test_style02_polish`），继续保留 `behind_person` 弹幕；
   - 每个返回 item 尽量携带不破坏原有架构的可选信息：`"render_backend": "web_spring"`（默认标注高光幕项可使用 Web 物理动效，`bridge_bar` 保持静帧）与 `"theme_profile"`。
4. **声效克制分配**：
   - 新增开关/权重控制，当处于 V6 导演模式（`director_plan` 生效）时，每组高光幕严格按 `sfx_budget`（全片总数控制约 `8~14` 条，相邻音效间隔 `>= 1.2s`，优先保留开篇钩子、否定打击、数字砸屏、框架切换与结尾重音），避免密集机械拟音。

### 2. 升级 `short-video-studio/scripts/step4_build_edl.py`
1. **消费导演 `broll_policy` 与 `camera_intent`**：
   - 读取 `director_plan` 与 `01b_composition_profile`：当某幕标注 `broll_policy == "lock_aroll"` 时锁死 `a_roll`；有实拍 B-roll 且允许语义切入时仍严格遵守 100% 实证匹配（不破坏 `v9_style02`）；无实拍 B-roll 时维持 `0` 伪图卡 `code_broll == 0`、`a_roll = 100%`；
   - **纯单口播虚拟 3 机位调度**（不改变 `a_roll` `1.0x` 原速，且 `test_studio` `allow_punch` 为假时尊重原契约）：
     * `steady_hold`：`punch_in_zoom = 1.0`；
     * `slow_push_115`：长镜微推或中景推近至 `1.14~1.16x`，利用 `face_framing` / 人脸锚点锁眼定位 (`_build_eye_locked_vf`) 不切头；
     * `side_shift_card`：若存在 `concept_split_card` 或侧边展示需要，结合 `01b_composition_profile` 仅对单口播做轻微平滑侧移缩放裁剪（`crop` 向左或向右保留半身构图不裁脸，`vf_extra` 合规输出标准 FFmpeg 缩放滤镜，`1080x1920` 偶数尺寸），绝不静态干坐整条片；
   - 保护已有的跨跳剪保护、前 3 秒至少 2 刀、0 冻帧与 `aroll_sentence_lock` 全部不退步。

### 3. 升级 `short-video-studio/scripts/step6_packaging.py`
1. 当 `kinetic_stage` 处于导演模式（`director_source` 存在或项带 `sfx_budget` 控制）时，尊重 `kinetic_choreographer` 标好的克制 `sfx_cues` 数量与间隔门槛（全片可控在 `6~14` 条，仍然 100% 挂 `0ms` 咬合锚点，严禁 `ding`/`chime`）；
2. 将 `03b_director_plan` 摘要及 `01b_composition_profile` 模式记录到 `06_packaging.json` 元信息中（例如 `"director_plan_applied": ...`），**严禁把任何风格代号或开发者术语写进面向成片字幕/花字的文本字段**（守住 QA 第 15/24 项零露馅红线）。

### 4. 新增单元测试 `tests/test_v6_stage_and_camera.py`
- 构造两套典型虚拟工程（商业大头单口播 vs 多素材探店）：
  1. 验证 `negation_strike`、`framework_pillar`、`concept_split_card`、`clean_breath` 4 个新原语正确排布，高光字均 `<= 7` 字；
  2. 验证大头近景 `closeup_chest_stage` 下自动关闭 `behind_person` 弹幕且安全落胸口区，`sfx` 总量收敛在 `<= 14` 条且 0 `ding`/`chime`；
  3. 验证单口播 `step4` 输出包含 `1.0x` 原速 `a_roll` 与 `slow_push_115` / `side_shift_card` 的合规 `vf_extra`，`code_broll == 0`、`freeze_tail == []`；
  4. 验证未提供 `03b_director_plan.json` 时的离线回退兼容性，且多素材探店场景行为零退步。

## 三、 交卷步骤
1. 严格仅编辑 `allowed_files`（含 `TEAM/reports/T008_codex.md`）；
2. 运行任务单全部 `test_commands` 确认 PASS；
3. 运行闸门检查：`"$HOME/.short-video-studio/venv/bin/python" dev_tools/team_ctl.py gate T008`，确认 `=== GATE PASS: T008 ===`；
4. 按 `TEAM/REPORT_TEMPLATE.md` 写入 `TEAM/reports/T008_codex.md`，在 `agent/codex/T008` 分支本地 `git commit`（严禁 `git push`）。
