# 剪辑与包装流水线中间文件契约 (Pipeline Schemas)

所有中间文件统一保存在 `<project_dir>/pipeline/` 下。每一步只读取前序已锁定文件并写出本步骤的确定性产物，严禁跳过中间文件直接拼参数。

---

## 0. 目录与文件流转总览

| 步骤 | 产出的标准文件 | 核心职责 |
| :--- | :--- | :--- |
| 一手原材（可选） | `00_raw_intel.json` + `00_script_lint.md` | 同行评论/客服原话痛点萃取、真实物证索引与剧本口语去AI味体检 |
| 入口2（可选） | `00_inventory.json` + `00_shot_log.json` | HDR/VFR诊断、物理锐度/抖动锁精华入点 `best_in_sec`、人脸双眼/显著性/OCR日志与选题反推 |
| 入口3（可选） | `00_code_spec.json` + `SPEC.md` + `STRUCTURE.md` | 纯代码制片规约（One Idea、Exact Copy 白名单、Fact Ledger、分幕时间表、安全区与可读停留校验） |
| Step 1 | `01_manifest.json` + `01_normalized/*.mp4/*.wav` | HDR→Rec.709 Hable 色调映射、统一转 CFR 30fps (`1080x1920`) 并提取 48kHz 16-bit WAV |
| Step 2 | `02_transcript.json` | ASR/VAD 能量包络强制对齐、F0/RMS 真实韵律重音、物理剥离画外声与多条 NG Take 择优 |
| Step 3 | `03_locked_voice.wav` + `03_locked_bgm.wav` + `03_audio_events.json` | 真实 Room Tone 抽样填缝 + 过零点等功率精剪 + 语义动态呼吸停顿 + 锁定音轨事件时间线 |
| Step 4 | `04_edl.json` + `04_edl_table.md` | 语义选镜与单向不回退 B-roll 游标、真 J-cut + 真 L-cut、连续 A-roll `1.15x` 锁眼微推拉、6.2s 模式中断守护 |
| Step 5 | `05_subtitles.json` + `05_subtitles.ass` | 逐字高亮字幕（含 `keywords` 与清洗后 `words`）、7点避脸 `final_bbox` 与已验证可商用字体样式 |
| Step 6 | `06_packaging.json` + `06_sfx_track.wav` | 流派 CC0 真实物理音效先行（峰值对齐切点 `onset_time_sec`/`peak_time_sec`）+ 字体实测宽胶囊 + 预算控制 |
| Step 7 | `07_color_plan.json` (+ `07_blended_lut.cube`) | Hero 基准锚定 + 全人脸镜头 BT.709 皮肤矢量保护 + 中点测光矫正 + 单向不升强度 LUT 闭环 |
| Step 8 | `08_mixed_master.wav` + `08_stem_nosfx.wav` + `08_voice_master.wav` + `08_bgm_ducked.wav` + `08_sfx_master.wav` + `08_sfx_audit_window.json` + `08_mix_metrics.json` | 广播人声链(自适应压缩) + dB 域双频段闪避 + 锁定一致 `-14.0 LUFS` 混音 + 三端四轨同源分轨 +全量 SFX 基线 |
| Step 9 | `09_title_cover.md` + `09_title_cover.json` + `09_cover.jpg` + `09_cover_3x4.jpg` + `09_ui_safe_preview.jpg` | 黄金封面帧优选（锐度/睁眼）、风格调色同步、避脸双构图封面 + UI遮罩预览 + 180px缩略图可读校验与真实3s `--hook-variants` |
| 出片 | `output_preview.mp4` / `output_final.mp4` / `jianying_draft/` / `hyperframes_draft/` | 本地 MP4 渲染 (`focus_blur` 柔焦与锁眼 Ken Burns 修复) + 相对 URL FCPXML + `<template>` 可播放离线 HyperFrames |
| 质检 | `10_qa_report.json` + `10_qa_report.md` (+ `10_benchmark_comparison.md`) | **22 项 Fail-Closed 门禁**（三态 `PASS \| FAIL \| SKIPPED_UNAVAILABLE`，双态终审 `MACHINE_PASS_PENDING_HUMAN` → `APPROVED`） |
| 交付与清理 | `out/*` + `11_delivery_summary.md` + `review_player.html` + `10_action_strips.jpg` (+ `VERIFY.md`) + `tidy_report.json` | 交付物同步入 `out/` + 离线打点审片 + 前3秒及切点动作连拍条 + 默认清理高体积中间帧/过程流并保留锁轨 ASSETS 以支撑 `reedit-visuals` (`--deep-clean` 才全清) |
| 进化闭环 | `content-log/weekly_review.md` + `content-log/account_profile.json` | 按 `--account` 与 `--track` 双重隔离复盘，纳入复播率/同城占比/询单率并反查 EDL 断崖 |

---

## 1. 关键 JSON 结构规范

### `02_transcript.json`
```json
{
  "source_type": "asr | tts",
  "language": "zh",
  "duration_sec": 24.5,
  "segments": [
    {
      "id": "seg_001",
      "text": "做了八年烧烤店，今天说句大实话。",
      "start_sec": 0.0,
      "end_sec": 2.4,
      "source_media": "01_normalized/a_roll_01.mp4",
      "is_filler": false,
      "words": [
        {"word": "做", "start_sec": 0.0, "end_sec": 0.18, "energy": 0.72, "is_keyword": false},
        {"word": "大实话", "start_sec": 1.65, "end_sec": 2.35, "energy": 0.94, "is_keyword": true}
      ]
    }
  ]
}
```

### `03_audio_events.json`（全流水线唯一时间骨架）
```json
{
  "fps": 30,
  "sample_rate": 48000,
  "locked_duration_sec": 21.6,
  "locked_duration_frames": 648,
  "bgm": {
    "file": "assets/bgm/upbeat_store_120bpm.m4a",
    "bpm": 120.0,
    "beat_times_sec": [0.0, 0.5, 1.0, 1.5],
    "downbeat_times_sec": [0.0, 2.0, 4.0],
    "hook_first_word_sec": 0.0,
    "hook_aligned_downbeat_sec": 0.0
  },
  "sentences": [
    {
      "id": "sent_001",
      "text": "做了八年烧烤店",
      "start_sec": 0.0,
      "end_sec": 1.15,
      "start_frame": 0,
      "end_frame": 35,
      "pause_after_sec": 0.20,
      "emotion_or_info": "hook",
      "keywords": ["八年", "烧烤店"],
      "words": []
    }
  ],
  "events": [
    {"id": "evt_001", "type": "sentence_start", "time_sec": 0.0, "frame": 0, "sentence_id": "sent_001", "label": "钩子首字对齐强拍"},
    {"id": "evt_002", "type": "keyword_accent", "time_sec": 0.85, "frame": 26, "sentence_id": "sent_001", "label": "核心词: 烧烤店"},
    {"id": "evt_003", "type": "bgm_downbeat", "time_sec": 2.0, "frame": 60, "label": "BGM第2小节强拍"},
    {"id": "evt_004", "type": "pause_end", "time_sec": 1.35, "frame": 41, "sentence_id": "sent_002", "label": "句间停顿结束"}
  ]
}
```
> **锁定约束**：`events` 的 `type` 枚举为：`sentence_start` | `sentence_end` | `keyword_accent` | `stress` | `pause_start` | `pause_end` | `bgm_downbeat` | `bgm_section` | `sfx_cue`。

### `04_edl.json`（以音定画剪辑表）
```json
{
  "fps": 30,
  "canvas": {"width": 1080, "height": 1920},
  "total_duration_sec": 21.6,
  "total_frames": 648,
  "first_frame_density_ok": true,
  "first_3s_visual_changes": 3,
  "j_cut_default_frames": 3,
  "clips": [
    {
      "clip_id": "clip_001",
      "track_role": "a_roll | b_roll | code_broll",
      "media_path": "01_normalized/a_roll_01.mp4",
      "source_in_sec": 0.0,
      "source_out_sec": 0.90,
      "timeline_in_sec": 0.0,
      "timeline_out_sec": 0.90,
      "timeline_in_frame": 0,
      "timeline_out_frame": 27,
      "audio_locked": true,
      "b_roll_overlay": false,
      "underlying_audio_untouched": true,
      "j_cut_lead_frames": 0,
      "cut_event_id": "evt_001",
      "cut_event_time_sec": 0.0,
      "sentence_id": "sent_001",
      "sentence_text": "做了八年烧烤店",
      "purpose": "首帧高密度开篇钩子",
      "visual_description": "老板直视镜头切羊排特写",
      "frame_canvas": {"x": 64, "y": 725, "w": 810, "h": 530, "bg_hex": "0xF1F3F5", "crop_y_frac": 0.32},
      "vf_extra": ""
    }
  ]
}
```
> **画布与边界可选约束**：
> - 分屏/边框流派可通过 `frame_canvas` 指定嵌套视窗坐标与背景色，并通过 `crop_y_frac`（默认 `0.32`）调整竖屏裁剪的垂直锚点（手持道具或特写画面建议设为 `0.44~0.48` 以居中防裁切）。
> - 手工锁定 `a_roll` 切片边界时须严格隔离句前后摄影师/导演画外提词音与环境句首余响（建议设置 `lock_edges` 禁止自动向右/向左跨过外部停顿吸帧）。

### `06_packaging.json`（包装与预算控制）
```json
{
  "budget": {
    "max_visual_elements": 7,
    "used_visual_elements": 4,
    "max_sfx_cues": 8,
    "used_sfx_cues": 5
  },
  "sfx_cues": [
    {"id": "sfx_001", "file": "assets/sfx/whoosh.wav", "time_sec": 0.90, "frame": 27, "gain_db": -6.0, "event_id": "evt_002"}
  ],
  "visual_elements": [
    {
      "id": "pkg_001",
      "kind": "flower_text | sticker | badge",
      "text_or_asset": "8年老店底线",
      "start_frame": 27,
      "end_frame": 72,
      "aligned_sfx_or_accent_frame": 27,
      "alignment_error_frames": 0,
      "bbox": {"x": 140, "y": 320, "w": 520, "h": 110},
      "final_bbox": {"x": 140, "y": 320, "w": 520, "h": 110},
      "safe_zone_ok": true,
      "reason": "情绪/信息点：核心数字背书"
    }
  ]
}
```

---

## 2. 剪映可编辑工程独立封装说明 (`export_jianying.py`)
- 鉴于剪映专业版 6.0+ 对 `draft_info.json` 启用加密，直接写明文 JSON 无法导入；`export_jianying.py` 默认导出**全相对路径可编辑多轨 FCPXML 工程包**：
  - `jianying_draft/timeline.fcpxml`（Apple FCPXML 1.10：`frameDuration="100/3000s"` 严格帧对齐，主 `<spine>` 含经按需调色/Ken Burns 的独立分镜 `seg_XXX.mp4`；上层不重叠 `lane 1..N` 按所属宿主 `<asset-clip>` 局部 offset 精准挂载静态透明 PNG 及可编辑备用 `<title>` 字幕和 `<marker>`；`lane -1/-2/-3` 通过 `common.resolve_audio_stems` 挂载同源 `voice_master.wav`、`bgm_ducked.wav`、`sfx_master.wav`；全部 `<media-rep src="..."/>` 严格采用 `urllib.parse.quote` URL 编码的相对 `jianying_draft/` 路径）
  - `jianying_draft/overlays/`（1080×1920 静态透明全画幅 PNG 图层）、`jianying_draft/media/{segments,audio}/`、`subtitles.srt` / `subtitles.ass`、`*.cube` 3D LUT、`manifest.json` 及 `README_剪映导入使用指南.txt`
- **容错降级原则**：任何异常时捕获并将 `status` 设为 `"degraded_to_mp4_only"`，明确提示用户。

---

## 3. HyperFrames Studio 全离线工程封装说明 (`export_hyperframes.py`)
- `run_pipeline.py` 每次完成剪辑包装后，同步生成**零外链 CDN、符合 `hyperframes-core` `<template>` 契约、可直接在浏览器播放拖拽 Seek 的 `pipeline/hyperframes_draft/`**：
  - `index.html` + `hyperframes.json`：根编排文件（内嵌支持 `<template>` 注水与逐帧同步显隐的离线时间线驱动引擎，零 `http(s)://` 外链 CDN）
  - `compositions/video_track.html`（`video`）、`compositions/flower_track.html`（`graphics`，如有包装元素时生成）及 `compositions/captions_track.html`（`captions`：样式与 `@font-face` 全置于 `<template>` 内、唯一 ID 根、遵循 `bg_box_color`/`bg_radius`、严控 `y <= 1468`）
  - `启动HyperFrames工程.command`（`0o755` macOS 双击启动器）与 `启动HyperFrames工程.bat`（Windows 双击启动器）。

---

## 4. 原材、合规授权、闸门锁与 22 项 Fail-Closed 契约 Schema
- **专属隔离目录结构**：`<workspace_root>/accounts/<account_slug>/{account_profile.json, compliance_whitelist.json, content-log/, projects/<project_id>/{pipeline/, out/}}`（原素材 `--raw-dir` 全程只读零写入）。
- **`00_raw_intel.json`**：`verified_by_real_users`、`pain_keywords`、`raw_pain_phrases`、`customer_questions`、`proof_assets`、`script_lint` (`passed`, `error_count`, `warn_count`, `issues`)。
- **`00_shot_log.json` & `00_contact_sheet.jpg`**：每条素材的 `best_in_sec`、`sharpness_score`、`stability_score`（二阶加速度）、`motion_speed_px`、`accel_jitter_px`、`audio_grade` (`usable|noisy|silent`)、`video_grade` (`usable|audio_only|ng`)、`taboo_tags`、`color_meta`、`faces`、`eye_anchor`、`saliency_bbox`、`semantic_tags`、`scene_group`。
- **`00_compliance_report.json` & `00_asset_licenses.json` / `asset_license_manifest.json`**：广告法 `must`/`warn` 双级命中（实拍人声弃用 `>40%` 或清空即阻断 `passed=False`，`#22` 黑盒复核严格只读不覆写）；实际用到 BGM/SFX/字体/LUT/code_broll 逐项 `sha256`、`commercial_verified`、`tts_commercial_notice`。
- **`approval_lock.json` & `tidy_report.json` & `08_sfx_audit_window.json`**：`interruptions` (3 必停点审计)、`plan_sha256`（锁 03~08 核心工件及存在的 `00_code_spec.json`）、`output_final_sha256`、`human_approved`、`approved_at`（签字自动同步 `out/10_qa_report.*`）；`blind_test_answer_key.json` 独立真随机盲测。
- **`content-log/account_profile.json`**：按 `--account` 与 `--track` 双重隔离，`sample_sufficient`、`defaults_frozen`、`pause_sec`、`persona_pause_sec`、`target_median_shot_sec`、`target_aroll_ratio`、`preferred_style_id`、`winning_hooks`。
- **`00_code_spec.json` & `SPEC.md` & `STRUCTURE.md` & `VERIFY.md` & `10_code_verify_evidence.json`**：入口 3 `--mode code-video` 契约，含 `title`、`one_idea`、`format` (`width`/`height`/`fps`/`target_duration_sec`/`frame_count`)、`exact_copy`、`facts`、`assets` (真实 `ffprobe_media` 元数据)、`typography` (字号下限 ≥28/36/64px)、`subtitle_reserved_band`、`sections` (含 `hold_sec` 可读性验证)、自动抽帧像素几何+OCR实测取证 `10_code_verify_evidence.json` 及填满真实证据的 `VERIFY.md`；`--script-json` 同时兼容每条对象附带独立 `say`（TTS 读法）与 `show`（上屏显示）字段，并通过跨通道单调投射保留 `WordBoundary` 词级时间戳。
- **`10_qa_report.json`（22 项 Fail-Closed）**：每项含 `status: PASS|FAIL|SKIPPED_UNAVAILABLE` 与 `passed: (status=="PASS")`；汇总 `pass_count`, `fail_count`, `skipped_count`, `approval_status` (`MACHINE_PASS_PENDING_HUMAN` | `APPROVED` | `REJECTED`)；其中 `#16` 支持 HyperFrames 3 帧真实无头浏览器烟测、`#20` 同时核验 2–8kHz 抬升与 5ms Spectral Flux 毫秒起音同步。
