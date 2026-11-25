---
task_id: T005
title: 将 style_01 与 style_21 节奏编舞契约接入 step4_build_edl
owner: codex
branch: agent/codex/T005
allowed_files:
  - short-video-studio/scripts/step4_build_edl.py
  - tests/test_sprint3_step4_rhythm.py
forbidden_files:
  - short-video-studio/scripts/step6_packaging.py
  - short-video-studio/scripts/render_template_overlays.py
  - short-video-studio/scripts/style_lock.py
  - short-video-studio/scripts/qa_inspector.py
  - short-video-studio/scripts/render_mp4.py
report_path: TEAM/reports/T005_codex.md
test_commands:
  - "{python} -m unittest tests.test_sprint3_step4_rhythm -v"
  - "{python} -m unittest tests.test_ab_cut_trim tests.test_broll_match tests.test_style01_21_dna tests.test_studio.ProductionPipelineV48Tests.test_v50_style02_kinetic_stage_and_broll_policy tests.test_studio.StudioTests.test_repo_privacy_and_zero_local_project_implants -v"
---

# T005：将 `style_01` 与 `style_21` 节奏编舞契约接入 `step4_build_edl.py`

> 开始前必查：`git branch --show-current` 必须为 `agent/codex/T005`。先通读根目录 `AGENTS.md`。

## 一、 背景与接入目标
Sprint 2 已在 [style_01_21_choreographer.py](../../short-video-studio/scripts/style_01_21_choreographer.py) 中沉淀了经过测试验证的 `style_01`（门店爆款 ~57% 语义 B-roll + 3 拍开场锚点）与 `style_21`（工艺快切 ~75.6% 工艺特写 + 首 3 秒 `>=4` 切 + `1.20x` 纯构图推镜）节奏编舞契约。

当前 [step4_build_edl.py](../../short-video-studio/scripts/step4_build_edl.py) 尚未显式消费该模块，存在两处实测差距：
1. `style_21` 的 `a_roll` 推镜仍硬编码 `cur_zoom = 1.15`（约 2053 行），未对齐契约实测 `punch_in_scale = 1.20`；同时前 3 秒与配额调控缺少编舞契约驱动的加密切点与 B-roll 覆写落地，容易出现 `broll_ratio_actual` 偏低。
2. `04_edl.json` 中尚未记录已接入的编舞契约可审计元数据，不便于下游与 QA 验证编舞契约生效与口播倍速 `1.0x` 锁死状态。

## 二、 必须实现的改造（严格控制在 `allowed_files` 内）

### 1. 升级 `short-video-studio/scripts/step4_build_edl.py`
仅当 `style_id in ("style_01", "style_21")` 时消费 `style_01_21_choreographer`（必须对 `style_02` 动态花字舞台及其他 22 套风格做到 **零逻辑改动、零回归**）：

1. **安全接入契约与节奏参考**：
   - 导入 `STYLE_RHYTHM_CONTRACTS`、`plan_style_01_rhythm`、`plan_style_21_rhythm`（若导入异常提供明确降级不崩溃）；
   - 当 `style_id in ("style_01", "style_21")` 时，以真实输入的 `sentences`、`broll_pool` 与 `fps` 计算一次参考节奏方案（`choreo_plan`）并读取 `contract = STYLE_RHYTHM_CONTRACTS[style_id]`：
     - 将 `broll_target_ratio`（在受素材产能与语义规则合法约束的前提下）与 `opening_cuts`（`style_01` `>= 3`、`style_21` `>= 4`）作为契约基准接入选刀与分配策略。
2. **切点音频事件吸附铁律（关键）**：
   - **严禁将编舞器的固定秒数切片边界直接强行写入 `cut_points`！**那样会破坏 `qa_inspector.py` 第 7 项“切点对齐音频事件 `<= 1 帧`”红线；
   - 正确做法：当 `style_id == "style_21"` 且音频事件充足时，优先确保 `0.0..3.0s` 内挑出 `>= 3` 个来自 `03_audio_events.json` 的有效事件作为 `first_3s_evts`（生成 `>= 4` 个首 3 秒镜头切片），并在整片候选事件足够密集、语义 B-roll 充足时让中位镜长与切镜节奏更贴近 `contract["median_shot_sec"]`；
   - 所有最终产生的 `clips[1:]` 必须严格保证 `timeline_in_frame - j_cut_lead_frames` 与 `cut_event_frame`（真实音频事件帧）误差 `<= 1` 帧。
3. **`punch_in_scale` 与 B-roll 配额落地**：
   - `style_21`：`allow_punch` 启用时，连续同向/衔接镜头的推镜倍率对齐至 `contract["punch_in_scale"]`（`1.20`，允许安全区间 `[1.18, 1.22]`），替代硬编码 `1.15`；同时允许在 `style_21`（非 `style_02` `kinetic_stage_mode` 且非纯开场锁定窗口时），若 B-roll 与当前前 3 秒台词**具备明确语义标签命中**且不截断词尾，可顺势分配语义匹配 B-roll（严禁无依据硬塞无关画面），使充分匹配素材下实测 B-roll 占比向 `0.756` 靠拢；
   - `style_01`：保留开场 `0..3.2s` 实拍/斜标窗口节奏稳定，B-roll 目标围绕 `0.572` 分配；
   - **绝对红线**：所有 `a_roll` 镜头严禁变速（严格保持 `1.0x` 原速），且不得放松或绕过已有的头尾 NG 裁剪、冻帧保护与语义门禁。
4. **写入可审计 EDL 元数据**：
   - 在输出 `04_edl.json` 顶层增加 `"choreography_applied"` 字段：
     - 当 `style_id in ("style_01", "style_21")` 时写入 `{"style_id": style_id, "enabled": True, "broll_target_ratio": ..., "punch_in_scale": ..., "opening_cuts_3s_target": ..., "aroll_speed_locked": True, "planned_degrade_reason": ...}`；
     - 其它风格（如 `style_02`）写 `None` 或 `{"style_id": style_id, "enabled": False}`，不得破坏既有 EDL 结构断言。

### 2. 新增单元测试 `tests/test_sprint3_step4_rhythm.py`
用临时目录构建最小完整 `pipeline` 输入（含真实 `03_audio_events.json` 事件、`01_manifest.json` 归一化视频元信息与带描述/标签的 B-roll），验证：
1. **`style_21` 全套接入断言**：
   - 首 3 秒产出 `>= 4` 个视觉切片（当事件充足时），且全部 `clips[1:]` 满足 `abs((timeline_in_frame - j_cut_lead_frames) - cut_event_frame) <= 1`；
   - B-roll 占比贴近高目标配额（语义匹配素材充足时 `>= 0.55`），推镜镜头 `punch_in_zoom` 对齐 `1.20`（`1.18..1.22`）；
   - `choreography_applied["enabled"] is True` 且 `aroll_speed_locked is True`，全部 `a_roll` 镜头无非 `1.0x` 倍速；
2. **`style_01` 全套接入断言**：
   - 消费 `style_01` 契约，B-roll 占比落在合理语义覆盖区间，无 `style_21` 的 `1.20x` 推镜溢出，`choreography_applied` 准确反映 `style_01`；
3. **无语义 B-roll 优雅降级**：
   - 当 B-roll 描述/标签均不匹配台词且 `--allow-broll-mismatch` 启用时，仍保持可用镜头并守住切点事件对齐，绝不硬塞；
4. **跨风格隔离**：
   - `style_02`（带动态花字舞台）的 EDL 行为与既有规则完全不受影响。

## 三、 交卷步骤
1. 运行上方全部 `test_commands`，确保全部 PASS；
2. 运行闸门检查：
   `"$HOME/.short-video-studio/venv/bin/python" dev_tools/team_ctl.py gate T005`
   确认看到 `=== GATE PASS: T005 ===`；
3. 按 `TEAM/REPORT_TEMPLATE.md` 写入 `TEAM/reports/T005_codex.md`；
4. 在 `agent/codex/T005` 分支本地执行 `git add` + `git commit`（严禁 `git push`）。
