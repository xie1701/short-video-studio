# 任务交卷回执（T009）

- **负责人** `codex-b`
- **分支** `agent/codex-b/T009`
- **核心改动摘要**
  1. 根据脸占画面的大小、头顶留白和背景杂乱度，判断花字适合放在人物后方还是胸口前方。
  2. 从每条口播的 20% 和 60% 位置抽帧，取中位数评估胸口暗区和背景纹理。视频缺失或解码失败时，用已有脸框继续判断。
  3. 汇总逐镜摆字位置和整体主题建议，并提供命令行入口生成 `01b_composition_profile.json`。

## 改动文件清单（对照 allowed_files）

- `short-video-studio/scripts/composition_profile.py` 提供逐帧分析、素材抽帧、流水线汇总与命令行入口。
- `tests/test_composition_profile.py` 用合成画面、离线素材和临时清单验证构图、回退和主题裁决。

## 闸门与测试实测输出

- 执行命令 `"$HOME/.short-video-studio/venv/bin/python" dev_tools/team_ctl.py gate T009`
- 输出关键行
  ```text
  Ran 13 tests in 0.030s
  OK
  Ran 9 tests in 3.194s
  OK
  [gate] 分支校验通过: agent/codex-b/T009
  [gate] 变更白名单通过 (2 个文件): ['short-video-studio/scripts/composition_profile.py', 'tests/test_composition_profile.py']
  === GATE PASS: T009 ===
  ```
- 用实际 FFmpeg 和命令行处理了两秒合成视频与一份损坏视频。合成视频成功抽取两帧，胸口暗区比例中位数为 `0.50`。损坏视频通过脸框元数据得到近景模式，`sampled_frames` 为 `0`。

## 接口对齐与遗留风险说明

- 近景以 T009 任务单的 `face_area_ratio >= 0.085` 和 `0.0 < headroom_ratio < 0.22` 为准，拥挤背景条件为 `bg_complexity >= 0.42` 且 `headroom_ratio < 0.28`。V6 总纲中的人脸占比另记为 `0.12`，供总控核对。
- 顶层提供整体模式、人物后方是否可用及主题提示，具体高度和横移量位于 `aroll_profiles`。任一选中镜头需要前景时，整体关闭人物后方舞台。`safe_center_shift_x` 以画面宽度为单位，正值表示人物右移，负值表示左移，幅度上限为画宽的 12%。
- 无补拍画面时，单个口播机位或两个各满 12 秒的近景口播机位使用 `business_editorial` 建议。`sampled_frames` 为 `0` 时，胸口暗区和背景纹理两个像素指标以 `0` 占位。
- 当脸框下缘达到画面高度的 78%，约定的胸口高度带已无法提供避脸中心。`safe_fg_y_norm` 此时仍为 `-0.56`，排版消费方需结合脸框做逐镜避让。
