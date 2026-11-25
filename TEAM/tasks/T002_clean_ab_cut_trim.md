---
task_id: T002
title: 清理 step4_build_edl A/B 切换收尾拖沓与片段头尾 NG 边界修剪
owner: codex
branch: agent/codex/T002
allowed_files:
  - short-video-studio/scripts/step4_build_edl.py
  - tests/test_ab_cut_trim.py
forbidden_files:
  - short-video-studio/scripts/clip_boundary_audit.py
  - short-video-studio/scripts/qa_inspector.py
  - short-video-studio/scripts/kinetic_choreographer.py
report_path: TEAM/reports/T002_codex.md
test_commands:
  - "{python} -m unittest tests.test_ab_cut_trim tests.test_broll_match tests.test_studio.ProductionPipelineV48Tests.test_v50_style02_kinetic_stage_and_broll_policy -v"
---

# T002：清理 `step4_build_edl.py` A/B 切换收尾拖沓与片段头尾 NG 边界修剪

> 开始前必查：`git branch --show-current` 必须为 `agent/codex/T002`。先通读根目录 `AGENTS.md`。

## 一、 问题取证与根因定位
我们在端到端成片审核中定位到 **“A-roll 与 B-roll 切换收尾不干脆，片段头尾残留 NG 画面”** 的 4 处直接代码根因（均在 `short-video-studio/scripts/step4_build_edl.py`）：

1. **A-roll 源窗口不足时允许长达 1.2s 的硬定格冻帧**（约 1707–1711 行）：
   - 当前判断 `if (src_out - src_in) < max(0.25, seg_dur - 1.2) and len(broll_pool) > 0:` 过于宽松。当一条 1.23s 镜头源窗口仅剩 0.46s 时，`0.46 < max(0.25, 0.03)` 为假，导致结尾定格冻帧 0.77s 才切换镜头（例如人物已说完停顿或开始走动定格在尾帧）。
2. **B-roll 未对素材头尾充分切除晃动/举牌/收机 NG 帧**（约 1530–1545 与 1784–1803 行）：
   - `_broll_head_handle_sec()` 在素材 `stability_score >= 0.78` 时最多跳过 `0.40s`，且 `avail < seg_dur + 0.80` 时完全不避头不避尾直接用到 `0.0` 和 `avail` 终点；
   - 选择区间时未消费边界检测器结果，直接使用了片头开机对焦段与片尾摄影师收镜段。
3. **A↔B 切换跨句残留拖尾（`j_leads` 负/正帧移与花字冲突）**（约 1441–1482 行）：
   - A-roll → B-roll 时，L-cut 让 B-roll 提前 3 帧（`jcut=-3`）抢入前一句还没念完的时间；在 `kinetic_stage_mode`（如 `style_02`）下，前一句的动态花字还在舞台上，画面却已提前切进 B-roll 产生视觉残留；
   - B-roll → A-roll 时，部分片段拖延 `j_cut_frames` 滞后切回导致画面尾巴拖沓到下一句起拍。
4. **切后兜底缺口未做最终清零验证**：
   - 未对最终生成的 `clips` 做统一的源窗口充足率与头尾安全边距二次审查。

## 二、 必须实现的改造（严格只改动 `allowed_files`）

### 1. 改造 `short-video-studio/scripts/step4_build_edl.py`
1. **接入可选的真实边界安全窗（兼容 T001 并内置优雅回退）**：
   - 使用延迟/可选导入方式查询 `clip_boundary_audit.analyze_clip_safe_window`（若模块尚不在当前分支，不报错，优雅回退）：
     - 先查素材 manifest/item 中是否自带 `safe_in_sec` / `safe_out_sec`（若有，优先信任）；
     - 若物理视频文件存在且成功导入 `clip_boundary_audit`，可缓存/调用 `analyze_clip_safe_window(media_path)` 获取真实 `safe_in_sec / safe_out_sec`（注意不要对 `code_broll` 合成图卡调用视频扫描）；
     - 若以上均不可用，`_broll_head_handle_sec()` 与尾边距保护升级为：对于 `avail >= seg_dur + 1.2` 的实拍 B-roll，默认避开头 `min(0.65, avail * 0.12)` 与尾 `min(0.50, avail * 0.10)`（稳帧差 `< 0.78` 时提高至头 `<= 0.90s`）；对于较短素材也尽量居中取用中段而不是紧贴 `0.0` 或 `avail`。
   - 保证同一段视频反复使用时，其区间严格落在 `[safe_in, safe_out]` 之内，并在 clip 字段记录 `boundary_trim_reason`（字符串，简述是按 `manifest`、`audit_safe_window` 还是 `default_handle_margin` 裁剪）。
2. **杜绝 A-roll 源窗口不足导致的明显定格冻帧**：
   - 在 A-roll `src_in / src_out` 分配处（1705–1711 行附近）：将容忍缺口从 `seg_dur - 1.2` 严格收紧到 **最多 `0.25s`**（或 `need_freeze` 判断触发时）：
     - 当可用余量不足 `seg_dur - 0.25` 时：
       a. 先尝试检查是否允许回退（在满足单调递增、不撞前镜 `max_out_by_mp` 且该素材真实有效窗口够长的前提下）微调衔接，或仅在非 `a_roll_locked` 窗口由语义匹配的实拍 B-roll 干净接管；
       b. 即使处于 `a_roll_locked` 或无 B-roll 时也禁止放行 `source_out - source_in < seg_dur - 0.25` 的明显冻帧缺口若还能从相邻句连续源窗口延展，或清晰将 `freeze_tail=False` 保障正常连贯播放（注：`a_roll` 只要台词连续同一源视频，允许利用该段语音后的自然余韵缓冲 `<= 0.25s` 来补足 1–6 帧量化误差，避免硬冻帧）。
3. **收紧 `kinetic_stage_mode` 与 A↔B 跨角色切换干脆度**：
   - 当 `kinetic_stage_mode` 为真时（以及所有 `a_roll ↔ b_roll` 跨越 **不同 `beat_group` 或从 `a_roll_locked` 句切到 `plain_bridge`** 的边界处），禁止负值 L-cut 提前抢跑穿透前句花字（即 `j_leads[idx] = 0`，切点严格踩在 `raw_frame` 句界/气口零帧对齐）；
   - B-roll → A-roll 回切时，若目标 `a_roll` 属于新句子或新花字舞台句组起点（如 `kinetic_stage_mode` 的 `hero_anchor` / `entity_stack` 起拍），同样确保 `j_leads[idx] = 0` 一刀干脆切回主讲人（不得拖着 B-roll 尾巴进入新花字），同时仍保留非花字冲突场景下既有的 J/L-cut 合法能力以免破坏原有单测。
   - **重要兼容性守卫**：不得破坏 `qa_inspector.py` 的切点事件契约——每个 `clips[1:]` 的 `timeline_in_frame - j_cut_lead_frames` 必须仍与 `ev["frame"]` 误差 `<= 1` 帧，且不得打破 `tests/test_broll_match.py` 和 `tests/test_studio.py` 中已有的全部用例。

### 2. 新增独立单元测试 `tests/test_ab_cut_trim.py`
测试必须用临时目录合成最小完整输入，验证以下 4 个行为：
1. **B-roll 首尾保护与可选 `safe_in_sec / safe_out_sec` 消费**：若素材声明或返回 `safe_in_sec=0.8, safe_out_sec=5.2`（总长 6.0s），生成的 EDL 中所有用到该 B-roll 的 clip 严格满足 `0.8 - 1e-3 <= source_in_sec < source_out_sec <= 5.2 + 1e-3`；即使无检测器回退默认边距，也不会贴着 `0.0` 或 `avail` 末端取帧；
2. **杜绝大段源窗口不足冻帧**：构造某句时间线 1.5s 但原句 `source_out_sec - source_in_sec` 只有 0.45s（同时提供语义匹配 B-roll 与长源视频对照组），验证在有 B-roll 接管窗口下会自动避免硬定格 `>0.25s` 的 `freeze_tail`；
3. **动态花字风格下跨组切换干脆零抢跑**：对 `style_02` + 有匹配标签的 B-roll 构造 A→B 与 B→A 跨越句组边界测试，验证 `a_roll ↔ b_roll` 在句组/花字边界处 `j_cut_lead_frames == 0`，`timeline_in_frame == cut_event_frame`（0 帧拖尾，不提前抢入前句花字）；
4. **保持既有切点事件对齐契约与零回归**。

## 三、 交卷步骤
1. 运行 `"$HOME/.short-video-studio/venv/bin/python" dev_tools/team_ctl.py gate T002`，确认看到 `=== GATE PASS: T002 ===`；
2. 按 `TEAM/REPORT_TEMPLATE.md` 写入 `TEAM/reports/T002_codex.md`；
3. 在分支 `agent/codex/T002` 本地执行 `git add` + `git commit`（禁止 `git push`）。
