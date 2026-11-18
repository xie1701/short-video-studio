# 剪辑与包装流水线中间文件契约 (Pipeline Schemas)

所有中间文件统一保存在 `<project_dir>/pipeline/` 下。每一步只读取前序已锁定文件并写出本步骤的确定性产物，严禁跳过中间文件直接拼参数。

---

## 0. 目录与文件流转总览

| 步骤 | 产出的标准文件 | 核心职责 |
| :--- | :--- | :--- |
| 入口2（可选） | `00_inventory.json` | 带素材进来时的素材盘点、VFR检测、画质诊断与选题反推 |
| Step 1 | `01_manifest.json` + `01_normalized/*.mp4/*.wav` | 统一转 CFR 30fps (1080x1920) 并提取 48kHz 16-bit WAV |
| Step 2 | `02_transcript.json` | 逐字时间戳（口播转写或 TTS 生成） |
| Step 3 | `03_locked_voice.wav` + `03_locked_bgm.wav` + `03_audio_events.json` | **锁定音轨**与音频事件时间线（此后声音彻底锁定，不再修改） |
| Step 4 | `04_edl.json` + `04_edl_table.md` | **以音定画**剪辑表（J-cut、前3秒≥2切、A/B轨绑定、缺画面补镜） |
| Step 5 | `05_subtitles.json` + `05_subtitles.ass` | 逐字高亮字幕与可商用字体样式 |
| Step 6 | `06_packaging.json` + `06_sfx_track.wav` | 音效先行落点 + ±1帧对齐的花字/贴纸/特效 + 预算控制 |
| Step 7 | `07_color_plan.json` | 基础白平衡/曝光参数 + 3D `.cube` LUT 配置 |
| Step 8 | `08_mixed_master.wav` + `08_mix_metrics.json` | 降噪 + BGM 自动闪避 + `-14.0 LUFS` 母带混音 |
| Step 9 | `09_title_cover.md` + `09_cover.jpg` | 标题、话题标签、封面文案与高密度封面图 |
| 出片 | `output_preview.mp4` / `output_final.mp4` / `jianying_draft/` | 本地 MP4 渲染与可选剪映草稿（失效自动降级） |
| 质检 | `10_qa_report.json` + `10_qa_report.md` (+ `10_benchmark_comparison.md`) | 11 项自动质检（全部 PASS 且通过多模态声画实审才交付）+ 对标逐秒结构对比 |

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
      "safe_zone_ok": true,
      "reason": "情绪/信息点：核心数字背书"
    }
  ]
}
```

---

## 2. 剪映可编辑工程独立封装说明 (`export_jianying.py`)
- 鉴于剪映专业版 6.0+（含 11.7.0）对 `draft_info.json` 启用私有加密，直接写明文 JSON 拷贝至草稿箱无法打开；`export_jianying.py` 默认导出**剪映官方原生支持导入的多轨可编辑包**：
  - `jianying_draft/<project>.fcpxml`（Apple FCPXML 1.10：`frameDuration="100/3000s"` 严格帧对齐，主 `<spine>` 含全部切片，`lane -1/-2/-3` 为纯净人声、BGM、SFX 三条独立 WAV 音轨）
  - `jianying_draft/<project>.srt`（及 `05_subtitles.ass`，可直接在剪映「文本 → 本地字幕」拖拽生成可编辑文字轨）
  - `jianying_draft/overlays/`（1080×1920 透明定位花字/卡片 PNG）与 `*.cube` 3D LUT，附 `README_剪映导入指南.txt` 及 `legacy_v5_json/` 归档元数据
- **容错降级原则**：任何格式转换异常或目标路径不可写时，捕获异常并将 `status` 设为 `"degraded_to_mp4_only"`，明确提示用户“剪映工程导出失败已自动降级为仅输出 MP4，不影响成片交付”。
