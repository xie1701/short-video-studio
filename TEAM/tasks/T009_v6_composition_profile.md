---
task_id: T009
title: V6.0 Wave 1 · 实现画面构图自感知模块 (composition_profile.py)
owner: codex-b
branch: agent/codex-b/T009
allowed_files:
  - short-video-studio/scripts/composition_profile.py
  - tests/test_composition_profile.py
forbidden_files:
  - short-video-studio/scripts/director_plan.py
  - short-video-studio/scripts/step4_build_edl.py
  - short-video-studio/scripts/step6_packaging.py
  - short-video-studio/scripts/kinetic_choreographer.py
  - short-video-studio/scripts/render_template_overlays.py
  - short-video-studio/scripts/qa_inspector.py
report_path: TEAM/reports/T009_codex-b.md
test_commands:
  - "{python} -m unittest tests.test_composition_profile -v"
  - "{python} -m unittest tests.test_style02_polish tests.test_studio.StudioTests.test_repo_privacy_and_zero_local_project_implants -v"
---

# T009：实现画面构图自感知模块 (`composition_profile.py`)

> 开始前必查：`git branch --show-current` 必须为 `agent/codex-b/T009`。先通读根目录 `AGENTS.md` 与 [TEAM/V6_MILLION_EDITOR_SPEC.md](../V6_MILLION_EDITOR_SPEC.md)。本任务单与 T007 完全并行、文件零交集。

## 一、 背景与痛点
既有 `style_02` 默认假设主讲人是“宽中景、头顶留白充足”的站姿取景，因此把人后弹幕硬放在画面上方。遇到中近景大头坐姿口播（人物占半屏以上、头顶贴边、背后存在复杂图表或墙绘，而胸口衣物为干净暗区）时，人后弹幕会被脑袋挡半截或混进背景。
需要建立轻量、确定性、复用 `face_framing` 与 Pillow/NumPy 像素统计的**画面构图自感知模块**，在排期与舞台编排前给出 `01b_composition_profile.json` 空间画像。

## 二、 必须实现的功能（严格限于 `allowed_files`）

### 1. 新建 `short-video-studio/scripts/composition_profile.py`
定义常量：`PROFILE_FILENAME = "01b_composition_profile.json"`。

必须实现并导出以下核心接口（含 CLI 支持 `--pipeline-dir <pdir>`）：

1. **`analyze_frame_composition(frame_img: Image.Image, face_bbox: Optional[Dict[str, float]] = None) -> Dict[str, Any]`**
   - `face_bbox` 遵循仓库归一化格式 `{"x": float, "y": float, "w": float, "h": float}`（可能为 `None`，此时安全降级按无脸处理）；
   - 基于图像像素与人脸区域计算 4 个归一化实测指标（均在 `[0.0, 1.0]`）：
     * `face_area_ratio`: 人脸外接框面积占比（无脸为 `0.0`）；
     * `headroom_ratio`: `face_bbox["y"]` 向上到顶边留白比例（无脸按顶部 `0.30` 估算或参考亮暗均匀度）；
     * `chest_dark_ratio`: 根据 `face_bbox` 下方胸口前景带（无脸时取竖屏 `[y=0.58..0.82, x=0.22..0.78]`）亮度 `Y <= 95` 且纹理低方差像素占比（识别深色衣物形成的天然高对比舞台区）；
     * `bg_complexity`: 人脸上方/左右背景区域（避开脸框本体）的 Sobel / 梯度均值或局部高频标准差归一化分值（区分干净白墙 vs 花哨地图/货架）。
   - **空间舞台模式自动裁决**：
     * 触发大头前景模式 `closeup_chest_stage` 条件（任一成立）：
       1. `face_area_ratio >= 0.085`（等效中近景大头，如人脸宽高均占 `~0.28..0.40`）；
       2. `headroom_ratio > 0.0` 且 `headroom_ratio < 0.22`；
       3. `bg_complexity >= 0.42` 且 `headroom_ratio < 0.28`；
     * 否则判定为 `wide_behind_person`（允许开启人后弹幕与上方立体字）。
   - 计算推荐前景安全中心纵坐标 `safe_fg_y_norm`（遵循本仓 `y_norm` 约定：`0.0` 为中心、`+1.0` 顶边、`-1.0` 底边，`y_norm` 越负越靠下；注意避开底部 `y_norm < -0.62` 平台 UI 字幕防遮挡带与人脸正框区 `[face_bottom]`）：
     * 确保大字中心落在人脸底缘下方 `max(-0.56, min(-0.34, chest_mid_ynorm))` 与安全带内，避免撞脸和撞底边按钮。
   - 返回字典包含：`face_area_ratio`, `headroom_ratio`, `chest_dark_ratio`, `bg_complexity`, `allow_behind_person`, `safe_fg_y_norm`, `recommended_stage_mode`, `safe_center_shift_x`（供 `side_shift_card` 计算往左或往右挪人物的安全空间）。

2. **`analyze_aroll_item(item: Dict[str, Any], ffmpeg_bin: str = "ffmpeg") -> Dict[str, Any]`**
   - 读取 `item` 的 `normalized_mp4`（或 `source_path`）、`duration_sec` 以及已有 `faces` 字段；
   - 若无本地人脸框，则优先安全尝试调用 `face_framing.anchor_for(...)` 获取人脸锚点（若 `vision_inspector` 不可用不抛异常，退回 `faces[0]["bbox"]` 或 `None`）；
   - 用 `ffmpeg` 从片长 20% 与 60% 位置抽 1~2 帧（若视频不存在或无法解码，**绝不崩溃**，直接基于 `faces` 元数据保守推算后返回有效结果并标注 `"sampled_frames": 0`），聚合中位数给出一条完整 `aroll_profiles` 条目。

3. **`build_composition_profile(pipeline_dir: Path, overwrite: bool = True) -> Dict[str, Any]`**
   - 读取 `pipeline_dir / "01_manifest.json"` 与可选 `00_inventory.json`；
   - 扫描全部可用的 `a_roll` 素材（优先 `usable`，否则全部有视频的项），生成汇总：
     * `"step": "01b_composition_profile"`
     * `"primary_stage_mode"`: `"wide_behind_person" | "closeup_chest_stage"`
     * `"allow_behind_person"`: `bool`
     * `"theme_hint"`: 结合构图与多镜头特征提示 `"store_vibrant"` 或 `"business_editorial"`（当纯单机位或只有 1~2 个长机位近景大头口播时偏向 `"business_editorial"`，多机位多场景探店偏向 `"store_vibrant"`）
     * `"aroll_profiles"`: 逐条画像列表（含 `media_path`、`face_area_ratio` 等字段）
   - 写入 `pipeline_dir / PROFILE_FILENAME` 并返回字典。

### 2. 新增完整单元测试 `tests/test_composition_profile.py`
- 使用纯内存 Pillow 生成清晰可测的合成帧 + `face_bbox` 校验算法：
  1. **中近景大头 + 高频杂乱背景 + 纯黑胸口衣区**：构造上部棋盘/高频噪点地图背景 + 中大脸框（`w=0.34, h=0.30`, `y=0.14`）+ 下部纯黑胸口矩形，断言 `recommended_stage_mode == "closeup_chest_stage"`、`allow_behind_person is False`、`chest_dark_ratio >= 0.70`、`bg_complexity >= 0.35`、`safe_fg_y_norm` 处于合理胸口安全带 `[-0.58, -0.32]`；
  2. **宽中景 + 低纹理干净背景**：构造小脸框（`w=0.16, h=0.16`, `y=0.32`）+ 顶部柔和均匀背景，断言 `recommended_stage_mode == "wide_behind_person"`、`allow_behind_person is True`；
  3. **真实离线流水线目录构建**：只写 `01_manifest.json`（含合成 `faces` 元数据、甚至无需真实 mp4 解码），调用 `build_composition_profile`，断言正确写出 `01b_composition_profile.json` 并按多机位探店 vs 单机位大头给出合理 `primary_stage_mode` 与 `theme_hint`。

## 三、 交卷步骤
1. 严格控制修改文件在 `allowed_files`（含 `TEAM/reports/T009_codex-b.md`）内，零个人家目录绝对路径；
2. 运行任务单 `test_commands` 全部 PASS；
3. 运行闸门检查：`"$HOME/.short-video-studio/venv/bin/python" dev_tools/team_ctl.py gate T009`，确认看见 `=== GATE PASS: T009 ===`；
4. 按 `TEAM/REPORT_TEMPLATE.md` 写入 `TEAM/reports/T009_codex-b.md`，并在 `agent/codex-b/T009` 分支执行本地 `git add` + `git commit`（严禁 `git push`）。
