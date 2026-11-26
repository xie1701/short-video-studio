# V6.0 · `style_02` 百万剪辑师级通用口播引擎架构总纲

> 状态：**APPROVED FOR CODEX EXECUTION**  
> 总控：Antigravity ｜ 唯一主力执行：Codex ｜ 协同看板：[BOARD.md](BOARD.md)

---

## 一、 终极目标（North Star）

将 `style_02` 从“依赖固定探店句型与宽中景机位的局部花字模板”，升级为**可稳定复刻到任意口播类视频（多素材探店 / 纯单人大头商业口播 / 干货方法论清单 / 故事复盘，兼容普通话与粤语）的百万剪辑师级通用编导与动效引擎**：

- **效果标尺**：在不知晓制作工具的前提下，成片在情绪节奏、视觉信息减法、空间构图、音乐呼吸与动效物理质感五个维度达到成熟商业剪辑师手工精剪水准。
- **工程底线**：全链路兼容**零外部 API 的离线确定性回退**与**无 Chrome 浏览器回退**；现有 29 项白盒+黑盒 QA 保持 `0 FAIL`；同步产出 **6 轨可编辑剪映原生草稿**。
- **架构铁律**：
  - 严守 KISS 原则与 `AGENTS.md` 五条绝对红线；
  - 优先复用既有 `script_polish.py` 任务单/Agent 判定分离模式、`face_framing.py` 人脸锚点、`capture_html_video.mjs` 浏览器帧同步渲染桥，以及 `step8_mix_audio.py` 双频段分轨；
  - **严格保持 `render_mp4.py` 与 `qa_inspector.py` 现有核心消费契约不变**（Web 物理动效封装在 `render_template_overlays.py` 内部，虚拟多机位封装在 `step4_build_edl.py` 内部 `vf_extra` / `punch_in_zoom` / 偏移裁切内）。

---

## 二、 架构跃迁四步数据流

```text
01_manifest.json + 02_transcript.json + 03_audio_events.json
       │
       ├──► [T009] composition_profile.py ──► 01b_composition_profile.json
       │      (人脸占比 / 头顶留白 / 胸口暗区 / 背景纹理杂乱度 → 自动裁决舞台布局模式)
       │
       ▼
[T007] director_plan.py ──► 03b_director_tasks.json ──(Agent)──► 03b_director_plan.json
       │  (情绪幕划分 + 听觉原话/视觉大字双轨减法 + 10原语指派 + 机位 + 音乐呼吸 + 防幻觉溯源校验)
       │
       ├──► [T008] step4_build_edl.py 消费 camera_plan + 01b_composition_profile → 虚拟3机位叙事调度（A-roll 严锁 1.0x）
       ├──► [T008] kinetic_choreographer.py + step6_packaging.py → 10原语双轨舞台 + 克制高辨识音效(8~14条)
       ├──► [T010] capture_html_video.mjs + render_template_overlays.py → Web/SVG 物理弹簧透明动效层 + 双赛道主题
       ├──► [T010] step8_mix_audio.py → 消费 music_cues（延迟进歌 / 反转 Silence Drop / 段落滤波推升）
       ├──► [T010] export_jianying_native.py → 同步映射新 4 原语进 6 轨可编辑剪映原生草稿
       │
       ▼
[T011] 双闸门闭环：29项工程 QA (0 FAIL) + aesthetic_critic.py 6维多模态审片 → 自动回写 03b_director_plan.json 修订1轮
```

---

## 三、 跨模块统一核心契约（接口冻结）

### 1. 构图画像：`01b_composition_profile.json`（T009 产出，T008/T010 消费）
```json
{
  "step": "01b_composition_profile",
  "primary_stage_mode": "wide_behind_person | closeup_chest_stage",
  "theme_hint": "store_vibrant | business_editorial",
  "aroll_profiles": [
    {
      "media_path": "...",
      "face_area_ratio": 0.19,
      "headroom_ratio": 0.14,
      "chest_dark_ratio": 0.62,
      "bg_complexity": 0.48,
      "allow_behind_person": false,
      "safe_fg_y_norm": -0.46,
      "recommended_stage_mode": "closeup_chest_stage"
    }
  ]
}
```
- **核心裁决规则**（阈值经 Wave 1 双标杆实测校准：单人大头商业片实测 `face_area_ratio = 0.0864`）：当 `face_area_ratio >= 0.085` 或 `headroom_ratio < 0.22` 或 `(bg_complexity >= 0.42 and headroom_ratio < 0.28)` 时，`allow_behind_person = false`，`recommended_stage_mode = "closeup_chest_stage"`，由前端暗区（胸口位置 `y_norm` 约 `-0.52 ~ -0.30`）承载舞台大字，严禁在头顶/杂乱背景后塞入半遮挡弹幕；宽中景纯净/低遮挡头顶则保持 `wide_behind_person` 与人后弹幕。

### 2. AI 导演总谱：`03b_director_plan.json`（T007 定义与校验，T008/T010 消费）
```json
{
  "step": "03b_director_plan",
  "source": "agent | fallback_deterministic",
  "theme_profile": "store_vibrant | business_editorial",
  "highlight_coverage_ratio": 0.44,
  "acts": [
    {
      "act_id": "act_01",
      "sentence_ids": ["sent_001", "sent_002", "sent_003"],
      "dramatic_role": "hook | setup | twist | framework | proof | cta",
      "emotion_intensity": 0.92,
      "primitive": "negation_strike",
      "visual_copy": {
        "headline": "负债 200万+",
        "items": ["✕ 不是被骗", "✕ 不是行情差"],
        "accent_token": "200万+",
        "footnote": ""
      },
      "camera_intent": "steady_hold | slow_push_115 | side_shift_card",
      "broll_policy": "lock_aroll | allow_verified_broll",
      "sfx_budget": 2
    }
  ],
  "music_cues": [
    {
      "type": "delay_entry | silence_drop | section_swell",
      "start_sec": 0.0,
      "end_sec": 2.9,
      "depth_db": -28.0,
      "fade_ms": 80,
      "reason": "开篇纯干声重锤立钩子"
    }
  ]
}
```
- **防幻觉与信息减法铁律**：
  1. `visual_copy.headline` 与每条 `visual_copy.items[i]` 清洗符号（`✕✓≠→:`）与空白后长度必须 `<= 7` 字（主锤词建议 `2~6` 字）；
  2. 所有非符号中文实词/数字必须能在所属 `sentence_ids` 拼合原句的同源词汇或数字等价表达中溯源命中，禁止模型无中生有编造新数字或专名；
  3. 非 `clean_breath` / `plain_bridge` 的高光幕时长占比（`highlight_coverage_ratio`）必须收敛在 `[0.30, 0.58]`（目标带 `0.35~0.55`），避免全片爆满显吵或全片寡淡。

### 3. 泛口播 10 原语清单（T008 实现，T010 渲染并映射剪映）
- **保留既有 6 原语（向后兼容 0 退步）**：
  `hero_intro_pop`、`staged_3tier_build`、`hero_anchor_plus_danmaku`、`list_build_gather_dim_overlay`、`entity_stack_plus_top`、`num_anchor_dim_overlay`、`inline_emphasis_cta`、`plain_bridge`
- **新增 4 类泛口播高光原语**：
  1. `negation_strike`：否定/反转排比连击（如 `负债 200万+` 配合 `✕ 不是被骗`、`✕ 不是行情差`），支持红线划掉/强对比符号与清脆打击节奏；
  2. `framework_pillar`：结构进度锚点（如左下或居中紧凑胶囊标出 `01` / `02` / `03` 配 2~6 字短结论，如 `01 先问生意前提`），消灭单调数字流水账；
  3. `concept_split_card`：极简因果/对比结构卡（如 `师徒制 ≠ 喊师傅` / `核心 → 利润分钱`），与 `side_shift_card` 虚拟侧移构图协同同框展示且不挡脸；
  4. `clean_breath`：显式导演留白幕（强制禁止前景花字与主动音效，只留稳健干净底字幕与轻推镜头，制造「爆 → 收 → 爆」节奏张力）。

### 4. 双赛道视觉美术主题（T010）
- `store_vibrant`（默认兼顾既有 `style_02` 实体门店/探店风）：`#BCFF00` 荧光绿 + `#B71C1C` 警示红 + 醒目粗描边 + 纸张/气泡轻拟态，彻底保持既有风味；
- `business_editorial`（商业/认知/复盘口播默认适配）：杂志黑金高对比（主白 `#F8FAFC` / 琥珀金或冷青高亮 `#EAB308` / 克制暗红徽标 `#DC2626` / 深色半透磨砂胶囊 `rgba(12,15,22,0.78)` 配合 `1px` 微弱亮边框与细腻字距），消除大头商业口播的廉价花字感。

### 5. 审美审片协议：`10b_aesthetic_review_tasks.json` / `10b_aesthetic_review.json`（T011）
- 抽取首 3 秒 + 每高光幕中点 + 过渡幕代表帧拼图，配套导演规划/QA 指标摘要；
- 按 **6 维评分（信息减法、节奏呼吸、空间避脸、音乐张力、动效质感、赛道气质，每项 0~10）** 评审，输出可机读的 `patch_suggestions` 自动微调 `03b_director_plan.json` 后重跑 1 轮（仅一次迭代闭环，防止死循环）。

---

## 四、 终极验收标准（三道硬闸门，全满足方可交卷）

| 闸门 | 验收项目 | 硬指标 |
| :--- | :--- | :--- |
| **A. 工程硬闸门** | 1. 全量自动化单测 | `unittest discover -s tests -v` 全部 PASS（不得删减或放宽既有 `487` 项任何断言） |
| | 2. 29 项白/黑盒 QA | 标杆探店片（多素材粤语）、单人大头商业片（普通话）及新回归片均达 **`0 FAIL`**（`0` 冻帧、`0` 越界、`0` 伪 PPT、`A-roll 1.0x`、`0` `ding/chime`、`-14±1.5 LUFS`、盲听 `CER <= 0.15`） |
| | 3. 完整交付与降级 | MP4 + **6 轨可编辑剪映原生草稿** + HyperFrames + 4K 封面齐全；无外部 Agent / 无 Chrome 离线可完整跑通；0 隐私路径 |
| **B. 百万剪辑师量化标尺** | 1. 信息减法与溯源 | 前景核心大字每行严格 `<= 7` 字，实词数字 `100%` 可溯源原句，`0` 语义腰斩破词 |
| | 2. 疏密节奏曲线 | 高光舞台时长占比 `35% ~ 55%`（离线/Agent 模式均生效）；`A-roll` 纯静态不连续 `> 12s` |
| | 3. 空间避让与可读 | 动态大字与人脸框 `IoU == 0.00`；大头近景自动禁用人后弹幕；字体反差满足清晰可读 |
| | 4. 声学导演控制 | 音效数量克制在 `6 ~ 14` 条（`0ms` 误差 `<= 1` 帧）；具备 `delay_entry` 或反转 `silence_drop`（BGM 衰减 `>= 18dB`、持续 `0.35~0.75s`、零爆音） |
| | 5. 物理缓动动效 | `web_spring` 渲染帧呈现明确非线性加减速（首末段速度与中段峰值比非均匀），边缘无黑晕锯齿 |
| **C. 双标杆视觉终验** | 1. 商业口播新高度 | 开篇呈现否定连击（`负债 200万+` → `✕ 不是被骗` → `✕ 不是行情差`）；反转处 BGM 清晰抽空重起；方法论自带 `01/02/03` 进度与不挡脸结构卡片；有自然景别推拉与侧移呼吸 |
| | 2. 多素材探店零退步 | 保持粤语烟火气息、100% 实拍 B-roll 贴合度与宽中景人后穿行特效 |
| | 3. Critic 与用户拍板 | `aesthetic_critic` 6 维均分 `>= 8.5 / 10`（单项 `>= 8.0`），并经【脉动】看片终审通过 |
