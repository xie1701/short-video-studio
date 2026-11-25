---
task_id: T004
title: 沉淀 style_01 与 style_21 节奏编舞器与风格锁 DNA 校验
owner: deepseek
branch: agent/deepseek/T004
allowed_files:
  - short-video-studio/scripts/style_01_21_choreographer.py
  - short-video-studio/scripts/style_lock.py
  - tests/test_style01_21_dna.py
forbidden_files:
  - short-video-studio/scripts/style_dna_profiles.py
  - short-video-studio/scripts/render_template_overlays.py
  - short-video-studio/assets/style_spectrum/style_spectrum_library.json
  - short-video-studio/scripts/step4_build_edl.py
report_path: TEAM/reports/T004_deepseek.md
test_commands:
  - "{python} -m unittest tests.test_style01_21_dna -v"
  - "{python} -m unittest tests.test_studio.StudioTests.test_v2_whitebox_style_spectrum_and_rulebooks_contract tests.test_studio.StudioTests.test_repo_privacy_and_zero_local_project_implants -v"
  - "{python} short-video-studio/scripts/style_lock.py --style-id style_01"
  - "{python} short-video-studio/scripts/style_lock.py --style-id style_21"
  - "{python} short-video-studio/scripts/style_lock.py --style-id style_01 --check-dna"
  - "{python} short-video-studio/scripts/style_lock.py --style-id style_21 --check-dna"
---

# T004：沉淀 `style_01` 与 `style_21` 节奏编舞器与风格锁 DNA 校验

> 开始前必查：`git branch --show-current` 必须为 `agent/deepseek/T004`。先通读根目录 `AGENTS.md`。

## 一、 问题背景与真值依据
[style_spectrum_library.json](../../short-video-studio/assets/style_spectrum/style_spectrum_library.json) 与 [template_blueprints.json](../../short-video-studio/assets/style_spectrum/template_blueprints.json) 中已经记录了 **`style_01`** 与 **`style_21`** 的实测剪辑节拍真值，但目前缺少独立的编舞契约模块与风格锁深层 DNA 自检护栏：

1. **`style_01`（门店爆款默认首选 · 3 拍斜标开场 + ~57% 语义 B-roll）**：
   - 库实测特征：`broll_overlay_ratio_target=0.572`、`pip_composition_spec.mode="geometry_pip_window"`、主轨口播硬切零缝紧拼（中位 `1.87s`）、无任何转场（`transitions: ["无转场..."]`）；
   - 蓝图特征：`overlay_style.opening_mode="store_poster_burst"`、`anim_mode="store_stagger_burst"`、`tilt_deg=-5`、强调 3 拍错落砸屏斜标开场与居中无框白字 + 暖黄高亮。
2. **`style_21`（门店工艺快切 · 首 3s 4 切高能快切 + ~75.6% 工艺 B-roll + 推镜关键帧）**：
   - 库实测特征：`broll_overlay_ratio_target=0.756`、`pip_composition_spec.mode="geometry_pip_main_punch_in"`（`main.scale=1.2`）、极高快切密度（`13` 主轨口播中位 `1.15s`、`12` 段覆盖层视频、`24` 个关键帧）、工艺与细节特写占主导；
   - 铁律红线：无论推镜还是 B-roll 高占比，**所有 `a_roll` 人声段落播放倍速必须严格锁定 `1.0x`**（严禁靠加速口播凑快剪感）。
3. **`style_lock.py` 风格锁缺口**：
   - 现有 `style_lock.py` 只核对了字幕模板、蓝图字号颜色和几个整块对象是否一致，但缺乏可选的 `--check-dna` 深层体检入口，无法在提交或出片前主动核查某套风格的「描边真值（`stroke_mode == "none"`）」、「B-roll 占比合理区间」、「画中画模式」、「关键帧/快切契约」与编舞器规格之间是否出现隐性漂移。

## 二、 你必须实现的交付物（严格控制在 `allowed_files` 内）

### 1. 新增模块 `short-video-studio/scripts/style_01_21_choreographer.py`
仅使用 Python 标准库（及可选读取 `common.ASSETS_DIR` 下既有 JSON 真值），构建纯函数、确定性的剪辑节奏与花字窗口编舞器（供总控在后续冲刺无缝接入 `step4_build_edl.py` / `step6_packaging.py`）：

1. 导出常量 `STYLE_RHYTHM_CONTRACTS`：从 `style_spectrum_library.json` 与 `template_blueprints.json` 读取 `style_01` 与 `style_21` 实测真值（目标 `broll_target_ratio`、`opening_cuts_3s`、`median_shot_sec`、`opening_mode`、`punch_in_scale`、`aroll_speed_lock = 1.0`）。
2. 实现 **`plan_style_01_rhythm(sentences, broll_candidates=None, fps=30)`**：
   - 输入：`sentences` 列表（每项含 `id`/`sentence_id`、`start_sec`、`end_sec`、`text` 等）与可选 `broll_candidates`（含 `id`、`duration_sec`、`tags`/`semantic_tags`、`description`）；
   - 输出字典需清晰包含：
     - `style_id`: `"style_01"`
     - `opening_beats`: 开场 3 拍错落综艺斜标节奏窗列表（长度严格为 `3`，起拍分布在开场 `0.0..3.2s` 内，含 `beat_index`, `start_sec`, `end_sec`, `tilt_deg=-5`, `anim_mode="store_stagger_burst"`, `text`）；
     - `segments`: 整片分镜角色与配额建议（每段必须明确 `track_role` 为 `"a_roll"` 或 `"b_roll"`，**所有 `"a_roll"` 段强制 `playback_speed: 1.0`**，`broll_target_ratio` 围绕 `0.572`、在有充足候选时落入合理语义 B-roll 覆盖区间 `[0.45, 0.68]`）；
     - `broll_ratio_planned`: 计算后的实测计划 B-roll 时长占比；
     - `aroll_speed_locked`: `True`（且内部自检不存在任何非 `1.0` 的 `a_roll` 倍速）。
3. 实现 **`plan_style_21_rhythm(sentences, broll_candidates=None, fps=30)`**：
   - 输出字典需清晰包含：
     - `style_id`: `"style_21"`
     - `opening_3s_cuts`: 前 3.0 秒内计划的画面切分点列表（当总片长 `>= 3.0s` 时，保证前 3.0s 内至少产生 **`>= 4` 个独立画面切片 / `>= 3` 次硬切跳转**，单切点间隔合理 >= 0.45s）；
     - `segments`: 整片高密工艺快切序列：
       - **所有 `"a_roll"` 段强制 `playback_speed: 1.0`**，**任何口播句严禁变速**；
       - 在有候选工艺/特写 B-roll 时，高占比覆写窗口目标围绕 `0.756`（当候选覆盖足时规划 B-roll 占比 `>= 0.60`，并在无对应 B-roll 时优雅降级保持可用镜头并标注原因）；
       - 为 `punch_in` 触发镜生成标准缩放关键帧轨迹（`punch_in_zoom` / `keyframes`: e.g. `1.0 -> 1.18~1.22`，纯构图推镜不动声音）；
     - `fast_cuts_under_1s_ratio`: 统计 `< 1.05s`（或 `<= 1.0s`）快切占比；
     - `aroll_speed_locked`: `True`。

### 2. 增强 `short-video-studio/scripts/style_lock.py`
**向后兼容性要求（关键）**：
- 不得破坏 `style_lock.py --style-id <id>`、`--pipeline-dir <dir>`、`--json`、`--waive`、`--clear-waiver` 的任何既有行为、JSON 字段结构或退出码（`0` / `3`）；
- 新增只读深层 DNA 自检能力：
  1. 新增函数 **`check_style_dna_integrity(style_id: str) -> Dict[str, Any]`**：
     - 读取 `style_spectrum_library.json`、`template_blueprints.json`、`templates.json`，对 `style_id` 做扩展 DNA 一致性核查：
       - 核查 `flower_text_spec.stroke_mode` 与蓝图/字幕描边约定是否一致（实测无描边风格不可悄悄改出粗黑描边）；
       - 核查 `broll_overlay_ratio_target` 处于 `[0.0, 1.0]` 且与 `transition_and_packaging` / 编舞契约量级自洽（如若为 `style_01` / `style_21` 且可导入 `style_01_21_choreographer`，顺带比对 `STYLE_RHYTHM_CONTRACTS` 中的目标占比与 `aroll_speed_lock == 1.0` 不得冲突）；
       - 核查 `pip_composition_spec.mode` 非空、画幅与 540px 对称居中规则无越界；
     - 返回 `{"ok": bool, "style_id": style_id, "checks": [...], "issues": [...]}`。
  2. 为 `main()` 的 `argparse` 增加可选标志 `--check-dna`（`action="store_true"`）：
     - 未传 `--check-dna` 时：行为与原先 100% 相同；
     - 传入 `--style-id <id> --check-dna` 时：同时执行原 `audit_style_definition` 与 `check_style_dna_integrity`，合并结果输出并按两者同时 `ok` 返回退出码 `0` 否则 `3`；若与 `--pipeline-dir` 连用也同样兼容附加检查。

### 3. 新增单元测试 `tests/test_style01_21_dna.py`
测试需覆盖：
1. `style_01`：模拟 6–8 句门店口播 + 若干带标签 B-roll，验证 `opening_beats` 恰有 3 拍、`tilt_deg == -5`、`broll_ratio_planned` 合理贴近 ~0.57、`a_roll` `playback_speed` 全为 `1.0`；
2. `style_21`：模拟 6–8 句工艺/探店口播 + B-roll，验证前 3 秒切片数 `>= 4`、`broll_ratio_planned` 高占比贴近 ~0.75、存在 `punch_in` 关键帧且 `a_roll` `playback_speed` 100% 为 `1.0`；
3. `style_lock.py` CLI 兼容性 + `--check-dna`：用真实库验证 `style_01`、`style_21`、`style_02`、`style_03`、`style_17` 均返回 0；同时通过 mock/构造异常字典验证若出现非 1.0 A-roll 倍速锁或越界 B-roll 目标占比时 `check_style_dna_integrity` 能精准检出并判 `ok=False`；
4. 跨风格隔离与隐私无黑话自查。

## 三、 交卷步骤
1. 在 Worktree 根目录运行列出的全部 `test_commands`，确保全部 PASS；
2. 运行闸门检查：
   `"$HOME/.short-video-studio/venv/bin/python" dev_tools/team_ctl.py gate T004`
   确认看见 `=== GATE PASS: T004 ===`；
3. 按 `TEAM/REPORT_TEMPLATE.md` 写入 `TEAM/reports/T004_deepseek.md`；
4. 在 `agent/deepseek/T004` 分支执行本地 `git add` + `git commit`（严禁 `git push`）。
