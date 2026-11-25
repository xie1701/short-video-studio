---
task_id: T007
title: V6.0 Wave 1 · 实现 AI 导演总谱协议与防幻觉溯源校验 (director_plan.py)
owner: codex
branch: agent/codex/T007
allowed_files:
  - short-video-studio/scripts/director_plan.py
  - tests/test_director_plan.py
forbidden_files:
  - short-video-studio/scripts/step4_build_edl.py
  - short-video-studio/scripts/step6_packaging.py
  - short-video-studio/scripts/kinetic_choreographer.py
  - short-video-studio/scripts/render_template_overlays.py
  - short-video-studio/scripts/step8_mix_audio.py
  - short-video-studio/scripts/qa_inspector.py
  - short-video-studio/scripts/render_mp4.py
report_path: TEAM/reports/T007_codex.md
test_commands:
  - "{python} -m unittest tests.test_director_plan -v"
  - "{python} -m unittest tests.test_style02_polish tests.test_studio.StudioTests.test_repo_privacy_and_zero_local_project_implants -v"
---

# T007：实现 AI 导演总谱协议与防幻觉溯源校验 (`director_plan.py`)

> 开始前必查：`git branch --show-current` 必须为 `agent/codex/T007`。先通读根目录 `AGENTS.md` 与 [TEAM/V6_MILLION_EDITOR_SPEC.md](../V6_MILLION_EDITOR_SPEC.md)。

## 一、 背景与架构定位
当前 `style_02` 只能机械地把整句 ASR 口播拆块放大上屏，缺少通篇“情绪曲线 + 视觉信息减法 + 音乐呼吸 + 机位叙事”导演控制。
本任务参照仓内 [script_polish.py](../../short-video-studio/scripts/script_polish.py) 的解耦范式：
- 脚本不硬编码或直连外部商业大模型 API Key，而是产出给总控/Agent 审阅决策的任务包 `03b_director_tasks.json`；
- 强校验并落地总控/Agent 写回的 `03b_director_plan.json`（含防幻觉溯源、字数上限与高光占比校验）；
- 当不存在外部 Agent 写入时，内置零网络依赖的**确定性导演回退生成器**，同样产出高水准的双轨信息减法与情绪呼吸总谱（确保自动化流水线随时离线全通）。

## 二、 必须实现的功能（严格限于 `allowed_files`）

### 1. 新建 `short-video-studio/scripts/director_plan.py`
定义规范文件名常量：
- `TASKS_FILENAME = "03b_director_tasks.json"`
- `PLAN_FILENAME = "03b_director_plan.json"`
- 支持的 10 个原语枚举 `DIRECTOR_PRIMITIVES`：
  `hero_intro_pop`, `staged_3tier_build`, `hero_anchor_plus_danmaku`, `list_build_gather_dim_overlay`, `entity_stack_plus_top`, `num_anchor_dim_overlay`, `inline_emphasis_cta`, `plain_bridge`, `negation_strike`, `framework_pillar`, `concept_split_card`, `clean_breath`
- 支持的双赛道主题 `THEME_PROFILES = ("store_vibrant", "business_editorial")`

必须实现并导出以下简洁函数（附带 CLI 支持 `build-tasks` / `apply-plan` / `fallback`）：

1. **`build_director_tasks(pipeline_dir: Path, style_id: str = "style_02", out_path: Optional[Path] = None) -> Path`**
   - 读取 `03_audio_events.json`（必需）及可选的 `00_inventory.json` / `00_visual_index.json`（B-roll 描述与实体能力）与 `01b_composition_profile.json`（若存在）；
   - 生成结构化 `03b_director_tasks.json`，包含：
     * 完整时间轴锁定句子列表（`id`, `text`, `start_sec`, `end_sec`, `duration_sec`, `keywords`）；
     * B-roll 素材池摘要（是否存在真实实拍 B-roll、数量、标签预览）；
     * 构图模式提示（若已有 `01b_composition_profile.json` 则带入，否则标记 `"pending"`）；
     * 导演编写硬约束清单（如每行视觉大字 `<= 7` 字、高光覆盖率 `0.35..0.55`、仅提炼减法不编造数字/专名、反转或高潮句必须考虑 `silence_drop` / 开篇 `delay_entry`）。

2. **`validate_director_plan(plan: Dict[str, Any], audio_events: Dict[str, Any], composition_profile: Optional[Dict[str, Any]] = None) -> Dict[str, Any]`**
   - 返回 `{"ok": bool, "errors": List[str], "warnings": List[str], "sanitized_plan": Dict[str, Any]}`。
   - 必须执行严格确定性校验：
     * **句子覆写完整度**：所有 `acts` 的 `sentence_ids` 必须合法存在于 `03_audio_events.json`，且按时间有序、不漏关键句、不重复冲突归属；
     * **原语与枚举合法性**：`primitive` 必须属于 `DIRECTOR_PRIMITIVES`；`theme_profile` 必须属于 `THEME_PROFILES`；`camera_intent` 须在 `("steady_hold", "slow_push_115", "side_shift_card")` 内；
     * **信息减法长度控制**：对 `visual_copy.headline` 与每条 `visual_copy.items[i]`，去除修饰符号 `✕✓≠→:：· ` 后字符数严禁超过 `7` 字（超长或越界应在 `errors` 中明确指出并/或在 `sanitized_plan` 提供明确报错，不可静默吞掉假过）；
     * **零幻觉实词数字溯源**：提取 `visual_copy` 中 `headline`、`items`、`accent_token` 的连续数字串（含 `200万+` 等等价阿拉伯/中文数字）与 2 字以上中文核心实词，验证它们是否可从该幕对应 `sentence_ids` 拼接文本（允许数字跨汉字与阿拉伯数字等价，如“两百万”≈“200万”，或同一句精确子串/核心字集重叠 `>= 0.6`）溯源；若出现原文毫无依据的新数字（如原句说“两百万”，大字编成“五千万”）或虚构专名，**必须拦截并计入 `errors` (`hallucinated_token`)**；
     * **高光覆盖率与构图兼容**：计算高光幕（非 `plain_bridge` 且非 `clean_breath`）占锁定总时长比 `highlight_coverage_ratio`，写入报告并校验若显著偏离 `[0.25, 0.70]`（目标带 `[0.35, 0.55]`）给出告警/校验失败；若 `composition_profile` 存在且 `allow_behind_person is False`，不得对 `hero_anchor_plus_danmaku` 放行人后遮挡；
     * **音乐指令合法性**：校验每条 `music_cues`（`delay_entry` / `silence_drop` / `section_swell`）时间段落在 `[0, total_dur]`，`0.0 < duration <= 3.5`（其中 `silence_drop` 时长建议 `0.25..0.90s`，`depth_db <= -12.0`）。

3. **`generate_deterministic_director_plan(audio_events: Dict[str, Any], style_id: str = "style_02", inventory: Optional[Dict[str, Any]] = None, composition_profile: Optional[Dict[str, Any]] = None) -> Dict[str, Any]`**
   - 离线/无外部 Agent 介入时的确定性高质量回退编导引擎（绝对零外网请求）：
     * 根据是否有实体门店/探店高频词及实拍 B-roll 数量自动判断 `theme_profile`（`store_vibrant` vs `business_editorial`）；
     * 识别**否定排比连击**（如含 `不是...也不是...`、`不是...是...` 连续短句）自动派发 `negation_strike`，并提炼核心数字+否定项（如把 `公司破产那年我负债200多万，不是被骗，也不是行情差` 减法成 `headline="负债200万+"` / `items=["✕ 不是被骗", "✕ 不是行情差"]`，严格只取原文真词！）；
     * 识别**序数条陈**（`第一` / `第二` / `第三` 等）自动派发 `framework_pillar`，提炼 `01 / 02 / 03` + <=6字结论；
     * 识别**核心机制对比句**（如含 `核心不是...是...`、`≠`）派发 `concept_split_card` + `camera_intent="side_shift_card"`；
     * 识别**高张力反转词**（如 `不是...的问题`、`关键是`、`真相`）产出 `music_cues` 中的 `silence_drop`（句前或句初 `0.45s`、`-28dB`）及高潮前 `delay_entry`（当总时长 >= 25s 且商业复盘主题时）；
     * 强制控频疏密节奏：把其余常规叙述标为 `plain_bridge` 或 `clean_breath`，保证全片 `highlight_coverage_ratio` 落在 `0.35 ~ 0.55` 区间，且产出默认可通过 `validate_director_plan` 校验的完整 JSON。

4. **`load_or_build_director_plan(pipeline_dir: Path, style_id: str = "style_02", generate_fallback: bool = True) -> Optional[Dict[str, Any]]`**
   - 优先加载并校验 `pipeline_dir / PLAN_FILENAME`；若存在且合法返回校验清洗后的计划；若存在但不合法则打印明确 `[director_plan] [WARN]` 并按需回退生成确定性方案；若缺失且 `generate_fallback=True`，读取 `03_audio_events.json` / `00_inventory.json` / `01b_composition_profile.json` 现场生成返回（不污染只读上游测试）。

### 2. 新增完整单元测试 `tests/test_director_plan.py`
- 测试覆盖：
  1. `build_director_tasks` 生成完整结构且含待办句表与构图提示；
  2. `validate_director_plan`：合法计划通过；单行大字 `> 7` 字拦截；原句说“两百万”而大字篡改成“五千万”或无关词触发 `hallucinated_token` 拦截；`silence_drop` 越界时长/非法原语拦截；
  3. `generate_deterministic_director_plan` 在两类典型场景上的真实出力：
     - 纯单人商业复盘台词（含负债 200 多万、不是被骗、也不是行情差、第一/第二/第三、师徒制核心不是师徒两个字而是分利润），验证自动生成 `negation_strike`、`framework_pillar`、`concept_split_card`、`clean_breath` 与至少 1 处 `silence_drop`，且 `validate_director_plan(... )["ok"] is True`、`highlight_coverage_ratio` 在 `0.30..0.60`、每条视觉大字 `<= 7` 字且 100% 溯源原文；
     - 多素材探店台词（含六百平、四大天王列表、桑拿鸡），验证 `theme_profile == "store_vibrant"` 并保持 `list_build_gather_dim_overlay`、`num_anchor_dim_overlay` 等探店原语与 `validate_director_plan["ok"] is True`。
  4. `load_or_build_director_plan` 的优先读取、非法 JSON 降级保护。

## 三、 交卷步骤
1. 确认只在 `allowed_files`（含 `TEAM/reports/T007_codex.md`）内改动，严禁引入任何本机家目录绝对路径或参考工程敏感词；
2. 运行任务单 `test_commands` 全部 PASS；
3. 运行闸门检查：`"$HOME/.short-video-studio/venv/bin/python" dev_tools/team_ctl.py gate T007`，确认看见 `=== GATE PASS: T007 ===`；
4. 按 `TEAM/REPORT_TEMPLATE.md` 写入 `TEAM/reports/T007_codex.md`，并在 `agent/codex/T007` 分支执行本地 `git add` + `git commit`（严禁 `git push`）。
