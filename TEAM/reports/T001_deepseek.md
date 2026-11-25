# 任务交卷回执（T001）

- **负责人**：`deepseek`
- **分支**：`agent/deepseek/T001`
- **核心改动摘要（3–5 句大白话说明解决了什么问题、核心思路是什么）**：
  1. 新增 `clip_boundary_audit.py`：只抽素材首尾各 2 秒加中段参考窗，用 FFmpeg 解码出逐帧亮度与锐度，以「本镜中段」为参考判黑闪、糊帧和甩镜，直接给出能用的入点/出点；短素材、裁完过短的素材都有防误杀兜底，正常清晰镜头一帧都不多裁。
  2. 同一模块复核 `04_edl.json`：逐个镜头比对画面时长与源窗口长度，源窗口短缺超过 0.08 秒就点出具体 `clip_id` 和短缺秒数；再按成片时间线在入点 `+0.20s`、出点 `-0.20s` 内采样，抓黑闪（Y<14）与「清晰度腰斩 + 剧烈甩镜」。
  3. `qa_inspector.py` 新增一项 `cut_boundary_cleanliness`（第 29 项），正常片子 PASS，确有冻帧拖尾或 NG 边界才 FAIL；既有 28 项检查的 id、名称、阈值零改动。
  4. 新增 `tests/test_cut_boundary_ng.py`，在临时目录现场合成测试视频与伪 EDL，覆盖「首尾 NG 要裁掉」「正常镜头不许误伤」「冻帧短缺精准点名」「QA 拦截生效」四件事。

## 改动文件清单（对照 allowed_files）
- `short-video-studio/scripts/clip_boundary_audit.py` — 新增。导出 `analyze_clip_safe_window()` 与 `audit_edl_cut_boundaries()`；采样口径复用 `common.compute_bt709_luma`、`vision_inspector.compute_laplacian_sharpness`，不引新包（本机 venv 没有 cv2，所以走 FFmpeg + numpy）。
- `short-video-studio/scripts/qa_inspector.py` — 新增 `evaluate_cut_boundary_cleanliness()` 包装与三态映射，并在 `inspect_video()` 的切点检查后接入 `cut_boundary_cleanliness`；既有检查零改动。
- `tests/test_cut_boundary_ng.py` — 新增 4 个用例。

## 闸门与测试实测输出
- 执行命令：`"$HOME/.short-video-studio/venv/bin/python" dev_tools/team_ctl.py gate T001`
- 输出关键行（`[gate] running` 行里的解释器绝对路径按仓库隐私规范替换为 `$HOME`）：
  ```text
  test_1_head_black_flash_and_tail_blur_are_trimmed ... ok
  test_2_clean_stable_clip_is_not_over_trimmed ... ok
  test_3_freeze_tail_shortfall_names_the_clip ... ok
  test_4_qa_inspector_passes_clean_video_and_blocks_freeze_edl ... ok

  ----------------------------------------------------------------------
  Ran 4 tests in 10.481s

  OK
  [gate] running: $HOME/.short-video-studio/venv/bin/python -m unittest tests.test_cut_boundary_ng -v
  [gate] 分支校验通过: agent/deepseek/T001
  [gate] 变更白名单通过 (3 个文件): ['short-video-studio/scripts/clip_boundary_audit.py', 'short-video-studio/scripts/qa_inspector.py', 'tests/test_cut_boundary_ng.py']
  === GATE PASS: T001 ===
  ```

补充回归（只为确认新检查不误伤，未改任何既有断言）：
- `tests.test_studio.StudioTests.test_high_broll_edl_and_qa_styles_21_to_24` 与 `tests.test_studio.StudioTests.test_v25_tail_silence_and_residual_repeat_qa_gate`：`Ran 2 tests in 70.89s`，`OK`。
- `tests.test_qa_measurement`：`Ran 19 tests`，`OK`；`tests.test_team_ctl`：`Ran 1 test`，`OK`。
- 另用 `step4` 真实产出的 `style_24`（7 个镜头）复跑新检查：`cut_boundary_cleanliness` 判 `PASS`（0 处冻帧拖尾 / 0 处首尾 NG 帧），正常片子没有被误拦。

## 接口对齐与遗留风险说明
- 契约确认：两个公开函数签名、返回字段与任务单一致；`ng_reasons` 用 `head_dark_flash` / `head_pure_color_bad` / `head_blur` / `head_motion_shake` / `tail_dark_flash` / `tail_blur` / `tail_motion_shake` 这套标签；`audit_edl_cut_boundaries` 额外返回 `sampled` 布尔（成片是否成功采样），供质检三态映射用。T002 可直接把 `safe_in_sec` / `safe_out_sec` 写进源窗口。
- 采样精度：边界按 `sample_fps=8` 的帧网格判定，误差 ±1 帧（0.125s）。因此 0.8s 防误杀判据放宽了一个采样间隔再触发（代码注释已写明），否则 2.0s 素材首尾各 NG 0.6s 会被量化误差误判成「裁得太短」而整体回退。
- 成片解码不可用（比如文件损坏或缺 ffmpeg）时，边界采样这一半记 `SKIPPED_UNAVAILABLE`，不阻断交付；只要 EDL 侧查到冻帧拖尾，仍然判 `FAIL`，不会假装通过。
- 本任务未触碰 `forbidden_files` 里的 `step4_build_edl.py` / `render_mp4.py`；`qa_inspector` 的既有检查名称、id、阈值未动。需要总控协调的新接口：无。
