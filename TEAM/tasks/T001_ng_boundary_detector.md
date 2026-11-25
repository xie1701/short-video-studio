---
task_id: T001
title: 素材头尾 NG 画面检测与 QA 切点干净度审计
owner: deepseek
branch: agent/deepseek/T001
allowed_files:
  - short-video-studio/scripts/clip_boundary_audit.py
  - short-video-studio/scripts/qa_inspector.py
  - tests/test_cut_boundary_ng.py
forbidden_files:
  - short-video-studio/scripts/step4_build_edl.py
  - short-video-studio/scripts/render_mp4.py
report_path: TEAM/reports/T001_deepseek.md
test_commands:
  - "{python} -m unittest tests.test_cut_boundary_ng -v"
---

# T001：素材头尾 NG 画面检测与 QA 切点干净度审计

> 开始前必查：`git branch --show-current` 必须为 `agent/deepseek/T001`。先通读根目录 `AGENTS.md`。

## 一、 背景与实测痛点
用户在审阅 `style_02` 成片后反馈：**A-roll 与 B-roll 片段的开头和结尾常残留 NG 画面，切换收尾不够干脆**。
具体症状有四类：
1. 素材最前 `0.0..0.8s` 常含开机起拍晃动、摄影师手入画或对焦模糊；
2. 素材末尾常含收机甩镜、人物未站稳转身或暗闪；
3. 若单条镜头 `source_out_sec - source_in_sec < duration_sec - 0.08`（实测例如 1.23s 镜头源窗口仅剩 0.46s），会导致结尾冻帧硬拖 0.7s+ 才硬切；
4. 现有 `qa_inspector.py` 只检验了切点帧是否与上一镜有亮度跳变，没有验证 **切点前后 ±0.20s 内本身是否糊帧/黑闪/定格冻帧**。

## 二、 你必须实现的交付物（严格控制在 `allowed_files` 内）

### 1. 新增模块 `short-video-studio/scripts/clip_boundary_audit.py`
仅使用仓库现有环境已装依赖（优先复用 `cv2` / `numpy` / `common.ffmpeg_binary`、`common.ffprobe_binary`），禁止引入新第三方包。必须导出以下两个稳定公开函数，供 `T002`（`step4_build_edl.py`）和 `qa_inspector.py` 直接调用：

```python
def analyze_clip_safe_window(
    media_path: str,
    sample_fps: float = 8.0,
    head_scan_sec: float = 2.0,
    tail_scan_sec: float = 2.0,
) -> Dict[str, Any]:
    """
    扫描视频素材首部与尾部的可用边界，切除开机甩镜、过暗黑闪与严重失焦糊帧。
    必须返回字段：
      - safe_in_sec: float       # 建议安全入点（>= 0.0）
      - safe_out_sec: float      # 建议安全出点（<= duration_sec）
      - duration_sec: float      # 素材总时长
      - head_ng_sec: float       # 首部被裁掉的 NG 时长（== safe_in_sec）
      - tail_ng_sec: float       # 尾部被裁掉的 NG 时长（== duration_sec - safe_out_sec）
      - usable_span_sec: float   # safe_out_sec - safe_in_sec
      - ng_reasons: List[str]    # 命中原因标签，如 ["head_motion_shake", "tail_dark_flash", "tail_blur"]
    防误杀兜底：
      - 若素材很短（duration_sec <= 1.2s），只剪明显黑帧/纯色坏帧，不强行裁没画面；
      - 若计算后 usable_span_sec < 0.8s 且素材总长 >= 1.2s，适度保守回退至少保留 >= min(duration_sec * 0.6, duration_sec - 0.2)。
      - 任何单帧全清晰全稳态的正常视频：head_ng_sec 与 tail_ng_sec 必须 <= 0.15s（绝不误伤正常镜头）。
    """
```

```python
def audit_edl_cut_boundaries(
    video_path: str,
    edl: Dict[str, Any],
    window_sec: float = 0.20,
) -> Dict[str, Any]:
    """
    结合成片 MP4 与 04_edl.json 逐镜检查入出点干脆度：
      1. 源窗口冻帧拖尾检查：每个 clip 是否满足 source_out_sec - source_in_sec >= duration_sec - 0.08；
      2. 入点前后 0..+0.20s、出点前 -0.20..0s 采样检查是否存在黑帧闪帧（均值亮度 Y<14）或相对该镜中段清晰度腰斩且伴有剧烈全帧瞬移甩镜。
    必须返回字段：
      - ok: bool
      - checked_clips: int
      - freeze_tail_clips: List[Dict[str, Any]]   # {"clip_id", "timeline_in_sec", "shortfall_sec"}
      - ng_boundary_clips: List[Dict[str, Any]]   # {"clip_id", "edge": "head|tail", "time_sec", "reason"}
      - summary: str
    """
```

### 2. 接入 `short-video-studio/scripts/qa_inspector.py`
- 阅读 `qa_inspector.py` 中 `checks.append(_mk_check(...))` 的既有格式，在合适位置加入 1 条白盒/黑盒边界检测项：
  - check id: `"cut_boundary_cleanliness"`
  - title: `"片段首尾无NG帧与切点无冻帧拖尾"`
- **注意隔离兼容性**：如果成片/EDL 中未检出冻帧拖尾及 NG 边界则标 `PASS`；仅当确有违例时标 `FAIL`（不得误伤合成极简测试中的正常视频，合成视频如 1 秒纯色帧无剧烈甩镜/无冻帧短缺应判 `PASS`）。
- 不允许修改既有 28 项检查的名称、id 或阈值，以免破坏 `tests/test_studio.py` 现有断言。

### 3. 编写独立单元测试 `tests/test_cut_boundary_ng.py`
使用仓库现有 `$HOME/.short-video-studio/venv/bin/python` + `ffmpeg` / `PIL` / `numpy` 在 `tempfile.TemporaryDirectory()` 内现场生成测试视频，覆盖以下 4 个场景：
1. **首部甩镜/黑闪 + 尾部失焦/黑帧**：生成前 0.6s 黑闪/大幅偏移 + 中段清晰 + 末 0.6s 高斯强模糊的视频，断言 `safe_in_sec >= 0.45` 且 `safe_out_sec <= dur - 0.45`，`ng_reasons` 非空；
2. **全程稳定清晰镜头不误杀**：生成清晰稳定、有适度正常运动前景的视频，断言 `head_ng_sec <= 0.15` 且 `tail_ng_sec <= 0.15`；
3. **冻帧短缺检出**：构造伪 EDL 含一条 `duration_sec=2.0` 但 `source_out_sec - source_in_sec = 1.1` 的 clip，`audit_edl_cut_boundaries` 必须返回 `ok=False` 并精准点出该 `clip_id` 在 `freeze_tail_clips`；
4. **`qa_inspector` 回归与拦截**：验证正常合成视频上 `"cut_boundary_cleanliness"` 返回 `PASS`，违例 EDL 返回 `FAIL`。

## 三、 交卷步骤
1. 运行 `"$HOME/.short-video-studio/venv/bin/python" dev_tools/team_ctl.py gate T001`，确认看到 `=== GATE PASS: T001 ===`；
2. 按 `TEAM/REPORT_TEMPLATE.md` 写入 `TEAM/reports/T001_deepseek.md`；
3. 在分支 `agent/deepseek/T001` 本地执行 `git add` + `git commit`（禁止 `git push`）。
