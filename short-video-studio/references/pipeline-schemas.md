# 剪辑与包装流水线中间文件契约 (Pipeline Schemas)

所有中间文件统一保存在 `<project_dir>/pipeline/` 下。每一步只读取前序已锁定文件并写出本步骤的确定性产物，严禁跳过中间文件直接拼参数。

---

## 0. 目录与文件流转总览

| 步骤 | 产出的标准文件 | 核心职责 |
| :--- | :--- | :--- |
| 一手原材（可选） | `00_raw_intel.json` + `00_script_lint.md` | 同行评论/客服原话痛点萃取、真实物证索引与剧本口语去AI味体检 |
| 入口2（可选） | `00_inventory.json` + `00_shot_log.json` | HDR/VFR诊断、物理锐度/抖动锁精华入点 `best_in_sec`、人脸双眼/显著性/OCR日志与选题反推 |
| Step 1 | `01_manifest.json` + `01_normalized/*.mp4/*.wav` | HDR→Rec.709 Hable 色调映射、统一转 CFR 30fps (`1080x1920`) 并提取 48kHz 16-bit WAV |
| Step 2 | `02_transcript.json` | ASR/VAD 能量包络强制对齐、F0/RMS 真实韵律重音、物理剥离画外声与多条 NG Take 择优 |
| Step 3 | `03_locked_voice.wav` + `03_locked_bgm.wav` + `03_audio_events.json` | 真实 Room Tone 抽样填缝 + 过零点等功率精剪 + 语义动态呼吸停顿 + 锁定音轨事件时间线 |
| Step 4 | `04_edl.json` + `04_edl_table.md` | 语义选镜与去重、真 J-cut、连续 A-roll 锁眼微推拉 (`1.00x↔1.09x`)、6.2s 模式中断守护 |
| Step 5 | `05_subtitles.json` + `05_subtitles.ass` | 逐字高亮字幕、权威避脸 `final_bbox` 与已验证可商用字体样式 |
| Step 6 | `06_packaging.json` + `06_sfx_track.wav` | 流派 CC0 真实物理音效先行 + ±1 帧对齐花字包装 + 权威 `final_bbox` + 预算控制 |
| Step 7 | `07_color_plan.json` (+ `07_blended_lut.cube`) | Hero 基准锚定 + YCbCr 皮肤矢量保护 + 中间调矫正 + 闭环回测混合强度 LUT |
| Step 8 | `08_mixed_master.wav` + `08_voice_master.wav` + `08_bgm_ducked.wav` + `08_sfx_audit_window.json` + `08_mix_metrics.json` | 广播人声链 + dB 域双频段闪避 + `-14.0 LUFS` 混音 + 同源分轨 + tidy 后 SFX 基线 |
| Step 9 | `09_title_cover.md` + `09_title_cover.json` + `09_cover.jpg` + `09_cover_3x4.jpg` | 黄金封面帧优选（锐度/睁眼）、风格调色同步、避脸双构图封面 (9:16 + 3:4) 与去套话标题 |
| 出片 | `output_preview.mp4` / `output_final.mp4` / `jianying_draft/` / `hyperframes_draft/` | 本地 MP4 渲染 + 全相对路径剪映 `timeline.fcpxml` + 全离线零 CDN HyperFrames |
| 质检 | `10_qa_report.json` + `10_qa_report.md` (+ `10_benchmark_comparison.md`) | **22 项 Fail-Closed 门禁**（三态 `PASS \| FAIL \| SKIPPED_UNAVAILABLE`，双态终审 `MACHINE_PASS_PENDING_HUMAN` → `APPROVED`） |
| 交付与清理 | `out/*` + `11_delivery_summary.md` + `review_player.html` + `tidy_report.json` | 交付物同步入 `out/` + 离线打点审片 + 自动清理 `01_normalized/`/`08_*.wav` 等大文件并保留 `08_sfx_audit_window.json` |
| 进化闭环 | `content-log/weekly_review.md` + `content-log/account_profile.json` | 按 `--account` 隔离的微观留存断崖反查 EDL，沉淀胜出组参数基因自动反哺下条流水线 |

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
    "file": "assets/bgm/upbeat_store_120bpm.wav",
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
      "track_role": "a_roll | b_roll | code_broll | freeze",
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
  - `jianying_draft/timeline.fcpxml`（Apple FCPXML 1.10：`frameDuration="100/3000s"` 严格帧对齐，主 `<spine>` 含全部分镜；上层不重叠 `lane 1..N` 按 `final_bbox` 挂载 1080×1920 静态透明 PNG 图层，零重编码损失、速度提升 >20x；`lane -1/-2/-3` 为同源 `voice_master.wav`、`bgm_ducked.wav`、`sfx_track.wav` 独立 WAV，0dB 定电平；全部 `<media-rep src="..."/>` 严格采用相对 `jianying_draft/` 路径，严禁 `file://` 或 `/Users/...` 绝对路径）
  - `jianying_draft/overlays/`（1080×1920 静态透明全画幅 PNG 图层）、`jianying_draft/media/{segments,audio}/`、`subtitles.srt` / `subtitles.ass`、`*.cube` 3D LUT、`manifest.json` 及 `README_剪映导入使用指南.txt`
- **容错降级原则**：任何异常时捕获并将 `status` 设为 `"degraded_to_mp4_only"`，明确提示用户。

---

## 3. HyperFrames Studio 全离线工程封装说明 (`export_hyperframes.py`)
- `run_pipeline.py` 每次完成剪辑包装后，同步生成**零外链 CDN、全相对路径离线 `pipeline/hyperframes_draft/`**（并通过 `common.link_or_copy` 硬链接/复制视频分镜、三轨 WAV、透明 PNG 与经 `is_verified_commercial_font` 校验的商用字体到 `media/`）：
  - `index.html` + `hyperframes.json`：根编排文件（内置轻量 `window.__timelines` 离线脚本，零 `http(s)://` 外链 CDN）
  - `compositions/video_track.html`（`video`）、`compositions/flower_track.html`（`graphics`，如有包装元素时生成）及 `compositions/captions_track.html`（`captions`：严格按权威 `final_bbox` 落入 `y <= 1468` 安全区、`stroke_width=0` 零强加描边、`@font-face` 双色字幕）
  - `启动HyperFrames工程.command`（`0o755` macOS 双击启动器）与 `启动HyperFrames工程.bat`（Windows 双击启动器）。

---

## 4. v4 原材、合规授权、闸门锁与 22 项 Fail-Closed 契约 Schema
- **专属隔离目录结构**：`<workspace_root>/accounts/<account_slug>/{account_profile.json, compliance_whitelist.json, content-log/, projects/<project_id>/{pipeline/, out/}}`（原素材 `--raw-dir` 全程只读零写入）。
- **`00_raw_intel.json`**：`verified_by_real_users`、`pain_keywords`、`raw_pain_phrases`、`customer_questions`、`proof_assets`、`script_lint` (`passed`, `error_count`, `warn_count`, `issues`)。
- **`00_shot_log.json` & `00_contact_sheet.jpg`**：每条素材的 `best_in_sec`、`sharpness_score`、`stability_score`（二阶加速度）、`motion_speed_px`、`accel_jitter_px`、`audio_grade` (`usable|noisy|silent`)、`video_grade` (`usable|audio_only|ng`)、`taboo_tags`、`color_meta`、`faces`、`eye_anchor`、`saliency_bbox`、`semantic_tags`、`scene_group`。
- **`00_compliance_report.json` & `00_asset_licenses.json` / `asset_license_manifest.json`**：广告法 `must`/`warn` 双级命中（实拍人声弃用 `>40%` 或清空即阻断 `passed=False`）；实际用到 BGM/SFX/字体/LUT 逐项 `sha256`、`commercial_verified`（全部字体强制校验 `common.is_verified_commercial_font`）、`unregistered_count`、`all_commercial_verified`。
- **`approval_lock.json` & `tidy_report.json` & `08_sfx_audit_window.json`**：`interruptions` (3 必停点审计)、`plan_sha256`、`human_approved`、`approved_at`（签字自动同步 `out/10_qa_report.*`）；完工清理保留 `08_sfx_audit_window.json` 供 `tidy` 后二次复验 `#20`。
- **`content-log/account_profile.json`**：`sample_sufficient`、`defaults_frozen`（样本 `< 3` 时冻结活跃默认参数、新估算写入 `proposed_defaults`）、`pause_sec`、`target_median_shot_sec`、`target_aroll_ratio`、`preferred_style_id`、`winning_hooks`。
- **`10_qa_report.json`（22 项 Fail-Closed）**：每项含 `status: PASS|FAIL|SKIPPED_UNAVAILABLE` 与 `passed: (status=="PASS")`；汇总 `pass_count`, `fail_count`, `skipped_count`, `approval_status` (`MACHINE_PASS_PENDING_HUMAN` | `APPROVED` | `REJECTED`)。
