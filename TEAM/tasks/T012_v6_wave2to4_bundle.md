---
task_id: T012
title: V6.0 Wave 2~4 一次性打包交付 · 双轨舞台与虚拟机位 + 物理动效与情绪混音 + 审美审片闭环（合并 T008 + T010 + T011）
owner: codex
branch: agent/codex/T012
allowed_files:
  - short-video-studio/scripts/kinetic_choreographer.py
  - short-video-studio/scripts/step4_build_edl.py
  - short-video-studio/scripts/step6_packaging.py
  - tests/test_v6_stage_and_camera.py
  - short-video-studio/scripts/capture_html_video.mjs
  - short-video-studio/scripts/render_template_overlays.py
  - short-video-studio/scripts/step8_mix_audio.py
  - short-video-studio/scripts/export_jianying_native.py
  - tests/test_v6_motion_and_audio.py
  - short-video-studio/scripts/aesthetic_critic.py
  - dev_tools/bench_talking_head.py
  - tests/test_aesthetic_critic.py
forbidden_files:
  - short-video-studio/scripts/qa_inspector.py
  - short-video-studio/scripts/render_mp4.py
  - short-video-studio/scripts/director_plan.py
  - short-video-studio/scripts/composition_profile.py
report_path: TEAM/reports/T012_codex.md
test_commands:
  - "{python} -m unittest tests.test_v6_stage_and_camera -v"
  - "{python} -m unittest tests.test_v6_motion_and_audio -v"
  - "{python} -m unittest tests.test_aesthetic_critic -v"
  - "{python} -m unittest tests.test_director_plan tests.test_composition_profile tests.test_style02_polish tests.test_ab_cut_trim tests.test_sprint3_step4_rhythm tests.test_sprint3_step6_dna tests.test_style03_17_dna tests.test_studio.ProductionPipelineV48Tests.test_v50_style02_kinetic_stage_and_broll_policy tests.test_studio.StudioTests.test_repo_privacy_and_zero_local_project_implants -v"
  - "{python} dev_tools/bench_talking_head.py --smoke"
  - "{python} -m unittest discover -s tests"
---

# T012：V6.0 Wave 2~4 一次性打包交付（合并 T008 + T010 + T011）

> 开始前必查：`git branch --show-current` 必须为 `agent/codex/T012`。Wave 1（T007 `director_plan.py` + T009 `composition_profile.py`）已合入 `main`，两者接口**冻结**，本任务只消费、不修改。
> 先通读根目录 `AGENTS.md`、[TEAM/V6_MILLION_EDITOR_SPEC.md](../V6_MILLION_EDITOR_SPEC.md)，再按顺序通读三份子任务规格：
> 1. [T008_v6_stage_and_camera.md](T008_v6_stage_and_camera.md)
> 2. [T010_v6_motion_and_audio.md](T010_v6_motion_and_audio.md)
> 3. [T011_v6_aesthetic_critic.md](T011_v6_aesthetic_critic.md)

## 一、 本单与三份子任务的关系（优先级：本单 > 子任务单）

- 三份子任务单的**功能需求、接口签名与单测要求全部有效**，按其正文实现。
- 子任务单里关于 `branch`（`agent/codex/T008|T010|T011`）、“前提 Txxx 已合入 main”、各自 `gate` 与回执路径的条款**全部作废**，统一以本单 Frontmatter 为准：同一分支 `agent/codex/T012`、同一白名单、同一闸门 `gate T012`、同一回执 `TEAM/reports/T012_codex.md`。
- 白名单外的文件（尤其 `director_plan.py`、`composition_profile.py`、`qa_inspector.py`、`render_mp4.py`）一律不得修改。确需扩权时立即停手并在回复中申请。

## 二、 执行顺序与提交节奏（一个分支，三段本地提交）

1. **阶段 A · T008**（舞台、虚拟机位、克制音效）→ 跑通 `tests.test_v6_stage_and_camera` 与回归组 → 本地 `git commit -m "feat(T012-A): ..."`；
2. **阶段 B · T010**（Web/Pillow 弹簧动效、双赛道主题、`music_cues` 混音、剪映映射）→ 跑通 `tests.test_v6_motion_and_audio` 与回归组 → 本地 `git commit -m "feat(T012-B): ..."`；
3. **阶段 C · T011**（`aesthetic_critic.py` 审片闭环 + `bench_talking_head.py` 记分卡）→ 跑通 `tests.test_aesthetic_critic` 与 `bench_talking_head.py --smoke` → 本地 `git commit -m "feat(T012-C): ..."`；
4. 最后运行 `gate T012`（含全量 `unittest discover`），看到 `=== GATE PASS: T012 ===` 后写回执并提交。**禁止 `git push`。**

每个阶段结束都必须保证当时的全部既有测试通过，不允许把问题拖到下一阶段一起修。

## 三、 Wave 1 终审带来的补充硬约束（子任务单未写，必须遵守）

1. **探店题材零退步（最高优先级）**：当 `director_plan["theme_profile"] == "store_vibrant"` 时，`kinetic_choreographer.py` 以**现有 `style_02` 花字编排结果为底**，导演总谱只对它标为高光的幕做**覆盖或增强**，不得因总谱把某幕标为 `plain_bridge` / `clean_breath` 就删掉原有的探店花字、人后弹幕或实体堆叠。实测背景：离线总谱在探店标杆上高光覆盖率仅约 `28.75%`，若直接替换会让 `ouyangTestCut_v9_style02` 画面明显变空。`test_v50_style02_kinetic_stage_and_broll_policy` 与 `test_style02_polish` 必须零改动通过。
2. **商业题材以总谱为准**：`theme_profile == "business_editorial"` 时，前景高光严格按总谱 `visual_copy` 排布，`plain_bridge` / `clean_breath` 幕不得再由旧逻辑自行生成大字。
3. **否定连击允许无主标题**：`negation_strike` 的 `headline` 可能为空、只有 `items`（如 `✕ 不是人的问题`）。舞台与渲染需正常排出逐条否定行，不得崩溃或生成空白大字框。
4. **构图阈值**：近景判定以 `face_area_ratio >= 0.085` 为准（总纲已校准）。消费逐镜画像时，按句子 `source_media` 匹配 `aroll_profiles[].allow_behind_person`，未匹配回退顶层 `allow_behind_person`（与 `director_plan._allows_behind_person` 同一口径）。
5. **音效数量**：导演模式下全片主动音效收敛在 `6 ~ 14` 条，相邻间隔 `>= 1.2s`，`0` 条 `ding` / `chime`，`clean_breath` 幕 `0` 音效。
6. **零隐私**：代码、测试、回执、`bench_talking_head.py` 默认值中不得出现任何本机家目录绝对路径；真实工程目录只能通过 `--projects` / `--workspace-root` 参数传入。

## 四、 交卷验收（总控合并前会逐条复核）

| 项目 | 硬指标 |
| :--- | :--- |
| 闸门 | `gate T012` 输出 `=== GATE PASS: T012 ===`，含全量 `unittest discover -s tests` 通过，既有断言 0 删减 0 放宽 |
| 白名单 | `review T012` 显示 `unauthorized=[]` |
| 商业单口播 | 否定连击、`01/02/03` 支柱、对比卡、`clean_breath` 留白可从 `06_packaging.json` 的 `kinetic_stage` 中读出；近景下 `behind_person` 项为 `0`；音效 `6~14` 条 |
| 探店多机位 | 与合并前 `main` 相比，`kinetic_stage` 高光项数不减少，人后弹幕保留，实拍 B-roll 贴合度不下降 |
| 混音 | `apply_music_cues_envelope` 单测实测 `silence_drop` 窗口 RMS 跌落 `>= 18 dB`，窗口边界无阶跃爆音 |
| 动效 | 无 Chrome 环境下也能输出带透明通道的 `.mov`，位移曲线非线性 |
| 审片 | `evaluate_review_heuristics` 正反样本区分正确；补丁回写后仍通过 `validate_director_plan`，且带 `critic_revised=True` |
| 回执 | `TEAM/reports/T012_codex.md` 按 `TEAM/REPORT_TEMPLATE.md` 分 A/B/C 三段写明改动、测试实测输出与遗留风险 |

端到端双标杆渲染出片、抽帧视觉终验与审片打分由总控在合并后执行，不属于本单交付范围。
