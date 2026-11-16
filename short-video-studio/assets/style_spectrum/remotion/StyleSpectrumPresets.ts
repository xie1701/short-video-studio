// 自动生成的 20 套参考视频「风格谱」Remotion 预设库
export interface StyleSubtitlePreset {
  fontFamily: string;
  fontSizePx: number;
  englishFontSizePx: number;
  bilingual: boolean;
  keywordScale: number;
  primaryColor: string;
  keywordColor: string;
  strokeColor: string;
  strokeWidth: number;
  bgBoxColor: string;
  bgRadius: number;
  safeYRatio: number;
  animation: 'hard_cut' | 'pop_spring' | 'fade_slide_up' | 'karaoke_highlight';
}

export interface StyleSpectrumPreset {
  styleId: string;
  styleName: string;
  archetypeId: string;
  orientation: 'portrait_9_16' | 'landscape_16_9';
  fps: number;
  bpm: number;
  shotMedianSec: number;
  first3sCutsTarget: number;
  jCutFrames: number;
  subtitle: StyleSubtitlePreset;
  cover: {
    layoutId: string;
    layoutName: string;
    mainSubRatio: number;
    highlightStyle: string;
  };
  color: {
    yMean255: number;
    satMeanPct: number;
    warmthIndex: number;
    toneMood: string;
    paletteHex: string[];
    lutFile: string;
    cssFilter: string;
  };
  packaging: {
    transitions: string[];
    elements: string[];
    sfxPerMinTarget: number;
    visualPerMinTarget: number;
  };
}

export const STYLE_SPECTRUM_ARCHETYPES = [
  {
    "archetype_id": "arch_01_tech_ui_console",
    "name": "暗黑科技 / AI 工作流控制台风",
    "members": [
      "style_01",
      "style_09",
      "style_10",
      "style_15",
      "style_17"
    ],
    "best_for": "AI 工具测评、Agent 工作流演示、SaaS 产品教程、极客硬核拆解",
    "color_signature": "暗黑低亮度基底 (Y=36~84) + 琥珀金/科技蓝强调色 + 高对比卡片边框",
    "rhythm_signature": "顶部章节步骤条常驻 + 画面内部 UI/卡片高频切换 + 强节拍/事件对齐 (60%~90%)",
    "subtitle_signature": "克制小号居中底字 (36~46px)，重在让渡主视觉区给界面与卡片演示"
  },
  {
    "archetype_id": "arch_02_high_contrast_story",
    "name": "强反差财经 / 深度人物叙事拼贴风",
    "members": [
      "style_03",
      "style_16",
      "style_19"
    ],
    "best_for": "商业案例揭秘、创始人深度访谈、剪辑思维干货、高信息量长视频",
    "color_signature": "中性电影对比调或暖人物肤色 + 亮黄/翠绿/报刊米白强视觉冲击块",
    "rhythm_signature": "高速切镜 (med 1.25s~2.20s，全片 118~290 镜) + 高频音效推动 (63~90 SFX/min)",
    "subtitle_signature": "常规底部小字兜底 + 情绪/数字爆点处全屏或半屏巨型花字/色块炸裂"
  },
  {
    "archetype_id": "arch_03_editorial_matte_depth",
    "name": "高级感杂志排版 / 人物前景遮罩风",
    "members": [
      "style_11",
      "style_12",
      "style_18",
      "style_20"
    ],
    "best_for": "审美/设计评论、个人IP方法论教学、Notion/极简白底知识拆解",
    "color_signature": "极净极简白底或深红/书墙反差色 + 少而精的无衬线黑体层级",
    "rhythm_signature": "前 3 秒用人物遮罩大字或实物定格瞬间抓眼，中段结构化清单与快切案例交替",
    "subtitle_signature": "干净双语或粗黑体无底框排版，字距行距克制，严控花哨字体数量 ≤2 种"
  },
  {
    "archetype_id": "arch_04_punch_talking_head",
    "name": "高密口播卡点 / 干货大字冲击风",
    "members": [
      "style_02",
      "style_04",
      "style_07",
      "style_14"
    ],
    "best_for": "创始人认知口播、实体商家/建材家居老板IP、认知颠覆与痛点科普",
    "color_signature": "暖肤色高透或暖琥珀书桌调 + 亮黄(#FFD54F)/珊瑚红强反差重点高亮",
    "rhythm_signature": "口播句间紧剪 + 景别推拉跳剪 + 卡片弹窗音效卡点 (对齐率 79%~92%)",
    "subtitle_signature": "大号粗黑体 (52~76px) + 整句/半句亮黄变色放大，首帧高信息密度大字封面"
  },
  {
    "archetype_id": "arch_05_cinema_slow_space",
    "name": "电影感空间漫游 / 人文宋体金句风",
    "members": [
      "style_06",
      "style_08",
      "style_13"
    ],
    "best_for": "高端室内设计/建筑展厅实拍、心理学/哲学金句、深度真实生活随拍访谈",
    "color_signature": "暖木质感色温或冷青电影灰调，拒绝廉价高饱和滤镜",
    "rhythm_signature": "尊重呼吸感与空间尺度（空镜缓推或随拍微跳剪），少音效或零花字干扰",
    "subtitle_signature": "思源宋体 (SourceHanSerifCN) 或极简细黑投影，呈现杂志书卷气与高级克制感"
  },
  {
    "archetype_id": "arch_06_paper_stopmotion",
    "name": "纸张拼贴定格 / 工程图解科普风",
    "members": [
      "style_05"
    ],
    "best_for": "收纳改造、家居尺寸科普、产品结构拆解、强动手感实物教程",
    "color_signature": "低饱和褶皱白纸底纹 (sat=9.1%) + 暖木纹理 + 红线尺寸标尺",
    "rhythm_signature": "前 3 秒手部模型定格 3 连切，全片依靠纸片元件位移与定格帧节奏驱动",
    "subtitle_signature": "底部极简白字黑描边，画面主体留给纸雕模型与红色尺寸标注线"
  }
] as const;

export const STYLE_SPECTRUM_PRESETS: Record<string, StyleSpectrumPreset> = {
  "style_01": {
    "styleId": "style_01",
    "styleName": "复古胶片控制台 · AI 模板展示风",
    "archetypeId": "arch_01_tech_ui_console",
    "orientation": "portrait_9_16",
    "fps": 30,
    "bpm": 103.4,
    "shotMedianSec": 6.55,
    "first3sCutsTarget": 2,
    "jCutFrames": 0,
    "subtitle": {
      "fontFamily": "'Source Han Sans CN', 'Noto Sans SC', sans-serif",
      "fontSizePx": 40,
      "englishFontSizePx": 22,
      "bilingual": true,
      "keywordScale": 1.1,
      "primaryColor": "#FBFAEE",
      "keywordColor": "#D2BB8C",
      "strokeColor": "#0D0C0D",
      "strokeWidth": 2,
      "bgBoxColor": "#00000000",
      "bgRadius": 0,
      "safeYRatio": 0.8,
      "animation": "fade_slide_up"
    },
    "cover": {
      "layoutId": "film_reel_console",
      "layoutName": "复古胶片齿孔放映控制台",
      "mainSubRatio": 1.85,
      "highlightStyle": "金黄金属渐变大字 + 胶片黄黑胶囊状态标签"
    },
    "color": {
      "yMean255": 43.7,
      "satMeanPct": 20.5,
      "warmthIndex": 6.86,
      "toneMood": "深沉低饱和质感调",
      "paletteHex": [
        "#0D0C0D",
        "#F3F2EF",
        "#2D2B2C",
        "#504E4D",
        "#73726E"
      ],
      "lutFile": "style_01.cube",
      "cssFilter": "brightness(0.934) contrast(1.24) saturate(0.76) sepia(0.057)"
    },
    "packaging": {
      "transitions": [
        "暗场淡入",
        "胶片滚动切场",
        "弹窗意见卡滑入"
      ],
      "elements": [
        "胶片齿孔边框",
        "胶囊场次进度条",
        "圆环倒计时速度表",
        "白底映后意见卡"
      ],
      "sfxPerMinTarget": 60,
      "visualPerMinTarget": 12
    }
  },
  "style_02": {
    "styleId": "style_02",
    "styleName": "极简硬核口播 · 书法炸裂封面风",
    "archetypeId": "arch_04_punch_talking_head",
    "orientation": "portrait_9_16",
    "fps": 30,
    "bpm": 157.9,
    "shotMedianSec": 1.167,
    "first3sCutsTarget": 3,
    "jCutFrames": 0,
    "subtitle": {
      "fontFamily": "'Alimama ShuHeiTi', 'Noto Sans SC', sans-serif",
      "fontSizePx": 74,
      "englishFontSizePx": 22,
      "bilingual": false,
      "keywordScale": 1.24,
      "primaryColor": "#FFFFFF",
      "keywordColor": "#EFD45F",
      "strokeColor": "#111111",
      "strokeWidth": 5,
      "bgBoxColor": "#00000000",
      "bgRadius": 0,
      "safeYRatio": 0.78,
      "animation": "hard_cut"
    },
    "cover": {
      "layoutId": "calligraphy_contrast_bottom",
      "layoutName": "下方双行毛笔撞色金句封面",
      "mainSubRatio": 1.05,
      "highlightStyle": "全句/半句整段变亮黄并放大 1.24x + 硬切零拖沓"
    },
    "color": {
      "yMean255": 109.9,
      "satMeanPct": 20.0,
      "warmthIndex": 9.69,
      "toneMood": "均衡自然高通透调",
      "paletteHex": [
        "#0B0C0D",
        "#2D2D32",
        "#4A4C55",
        "#91928B",
        "#6D6F73"
      ],
      "lutFile": "style_02.cube",
      "cssFilter": "brightness(0.99) contrast(0.972) saturate(0.76) sepia(0.081)"
    },
    "packaging": {
      "transitions": [
        "硬切",
        "同机位推拉跳剪"
      ],
      "elements": [
        "毛笔手写封面大字",
        "全句变黄重音高亮"
      ],
      "sfxPerMinTarget": 70,
      "visualPerMinTarget": 6
    }
  },
  "style_03": {
    "styleId": "style_03",
    "styleName": "十亿访谈长片 · 顶部时间节点进度条风",
    "archetypeId": "arch_02_high_contrast_story",
    "orientation": "landscape_16_9",
    "fps": 24,
    "bpm": 111.1,
    "shotMedianSec": 1.583,
    "first3sCutsTarget": 2,
    "jCutFrames": 0,
    "subtitle": {
      "fontFamily": "'Source Han Sans CN', 'Noto Sans SC', sans-serif",
      "fontSizePx": 46,
      "englishFontSizePx": 22,
      "bilingual": false,
      "keywordScale": 1.35,
      "primaryColor": "#FFFFFF",
      "keywordColor": "#F8E731",
      "strokeColor": "#141414",
      "strokeWidth": 4,
      "bgBoxColor": "#00000000",
      "bgRadius": 0,
      "safeYRatio": 0.89,
      "animation": "hard_cut"
    },
    "cover": {
      "layoutId": "top_milestone_bar_interview",
      "layoutName": "顶部金线里程碑进度条 + 双机位花字冲击",
      "mainSubRatio": 2.1,
      "highlightStyle": "巨型亮黄#F8E731重黑描边花字 + 顶部里程碑节点亮起"
    },
    "color": {
      "yMean255": 125.1,
      "satMeanPct": 21.1,
      "warmthIndex": 16.37,
      "toneMood": "暖色高饱和门店/人物调",
      "paletteHex": [
        "#070707",
        "#F8F7F3",
        "#2F302E",
        "#ADB2AF",
        "#D1D2CD"
      ],
      "lutFile": "style_03.cube",
      "cssFilter": "brightness(1.023) contrast(0.975) saturate(0.76) sepia(0.136)"
    },
    "packaging": {
      "transitions": [
        "双机位正反打硬切",
        "单人特写切入"
      ],
      "elements": [
        "顶部里程碑进度条与头像游标",
        "全屏大白字重音花字",
        "语气词悬浮花字(嗯~)"
      ],
      "sfxPerMinTarget": 80,
      "visualPerMinTarget": 14
    }
  },
  "style_04": {
    "styleId": "style_04",
    "styleName": "质感拟物纸片 · AI 框架科普风",
    "archetypeId": "arch_04_punch_talking_head",
    "orientation": "portrait_9_16",
    "fps": 24,
    "bpm": 84.5,
    "shotMedianSec": 4.083,
    "first3sCutsTarget": 2,
    "jCutFrames": 3,
    "subtitle": {
      "fontFamily": "'Source Han Sans CN', 'Noto Sans SC', sans-serif",
      "fontSizePx": 48,
      "englishFontSizePx": 22,
      "bilingual": false,
      "keywordScale": 1.12,
      "primaryColor": "#FFFFFF",
      "keywordColor": "#FFFA87",
      "strokeColor": "#262B24",
      "strokeWidth": 3,
      "bgBoxColor": "#00000000",
      "bgRadius": 0,
      "safeYRatio": 0.76,
      "animation": "hard_cut"
    },
    "cover": {
      "layoutId": "center_dual_stroke_badge",
      "layoutName": "居中红黄撞色圆角双行封面 + 拟物纸片卡",
      "mainSubRatio": 1.25,
      "highlightStyle": "红底黄字胶囊 + 米白纸张折角卡片 + 打勾清单贴纸"
    },
    "color": {
      "yMean255": 153.7,
      "satMeanPct": 19.4,
      "warmthIndex": 21.39,
      "toneMood": "暖色高饱和门店/人物调",
      "paletteHex": [
        "#E2DFD2",
        "#140F0D",
        "#F6F5F2",
        "#332A2A",
        "#B4B0AC"
      ],
      "lutFile": "style_04.cube",
      "cssFilter": "brightness(1.057) contrast(1.219) saturate(0.76) sepia(0.178)"
    },
    "packaging": {
      "transitions": [
        "硬切",
        "纸片弹出切入",
        "全屏对话UI切屏"
      ],
      "elements": [
        "倾斜手绘黄标签",
        "折角纸片引文卡",
        "AI聊天模拟手机框",
        "方框勾选清单"
      ],
      "sfxPerMinTarget": 75,
      "visualPerMinTarget": 18
    }
  },
  "style_05": {
    "styleId": "style_05",
    "styleName": "皱纹纸拼贴定格 · 工程图解科普风",
    "archetypeId": "arch_06_paper_stopmotion",
    "orientation": "portrait_9_16",
    "fps": 24,
    "bpm": 96.8,
    "shotMedianSec": 1.5,
    "first3sCutsTarget": 3,
    "jCutFrames": 3,
    "subtitle": {
      "fontFamily": "'Source Han Sans CN', 'Noto Sans SC', sans-serif",
      "fontSizePx": 48,
      "englishFontSizePx": 22,
      "bilingual": false,
      "keywordScale": 1.15,
      "primaryColor": "#FFFFFF",
      "keywordColor": "#FFD54F",
      "strokeColor": "#1E1E1E",
      "strokeWidth": 4,
      "bgBoxColor": "#00000000",
      "bgRadius": 0,
      "safeYRatio": 0.88,
      "animation": "fade_slide_up"
    },
    "cover": {
      "layoutId": "paper_cutout_hero",
      "layoutName": "褶皱纸面立体模型 + 红色工程标注线",
      "mainSubRatio": 1.45,
      "highlightStyle": "红字工程尺寸标注线 + 立体纸模抽拉动效"
    },
    "color": {
      "yMean255": 185.0,
      "satMeanPct": 9.1,
      "warmthIndex": 12.51,
      "toneMood": "暖色高饱和门店/人物调",
      "paletteHex": [
        "#D2D2D1",
        "#030302",
        "#4E3A28",
        "#F6F6F6",
        "#A87950"
      ],
      "lutFile": "style_05.cube",
      "cssFilter": "brightness(1.066) contrast(1.24) saturate(0.76) sepia(0.104)"
    },
    "packaging": {
      "transitions": [
        "实物手势定格切",
        "纸片滑动位移"
      ],
      "elements": [
        "皱纹纸全局底纹",
        "红色CAD尺寸标尺",
        "3D等轴测柜体图",
        "卡通小人交互"
      ],
      "sfxPerMinTarget": 78,
      "visualPerMinTarget": 15
    }
  },
  "style_06": {
    "styleId": "style_06",
    "styleName": "10秒情感金句 · 衬线宋体剪影大片风",
    "archetypeId": "arch_05_cinema_slow_space",
    "orientation": "portrait_9_16",
    "fps": 30,
    "bpm": 146.3,
    "shotMedianSec": 2.317,
    "first3sCutsTarget": 2,
    "jCutFrames": 0,
    "subtitle": {
      "fontFamily": "'Source Han Serif CN', 'Noto Sans SC', sans-serif",
      "fontSizePx": 78,
      "englishFontSizePx": 22,
      "bilingual": false,
      "keywordScale": 1.38,
      "primaryColor": "#FFFFFF",
      "keywordColor": "#F7CE77",
      "strokeColor": "#1A1A1A",
      "strokeWidth": 4,
      "bgBoxColor": "#00000000",
      "bgRadius": 0,
      "safeYRatio": 0.69,
      "animation": "hard_cut"
    },
    "cover": {
      "layoutId": "minimal_serif_title_top",
      "layoutName": "置顶红白衬线主题 + 剪影高光句中大小字错落",
      "mainSubRatio": 1.38,
      "highlightStyle": "行内关键词字号突增 1.38x + 朱红渐变「第N次」角标"
    },
    "color": {
      "yMean255": 192.3,
      "satMeanPct": 9.0,
      "warmthIndex": 10.63,
      "toneMood": "均衡自然高通透调",
      "paletteHex": [
        "#EEEFF0",
        "#D0D0D2",
        "#AFADAD",
        "#909093",
        "#080809"
      ],
      "lutFile": "style_06.cube",
      "cssFilter": "brightness(1.066) contrast(1.24) saturate(0.76) sepia(0.089)"
    },
    "packaging": {
      "transitions": [
        "叠化转黑白剪影",
        "人物渐隐消散特效"
      ],
      "elements": [
        "置顶常驻双行宋体标题",
        "名牌小标签",
        "逆光家庭剪影",
        "行内大小字混排金句"
      ],
      "sfxPerMinTarget": 72,
      "visualPerMinTarget": 18
    }
  },
  "style_07": {
    "styleId": "style_07",
    "styleName": "商业导师书桌 · 暗金波点全屏花字风",
    "archetypeId": "arch_04_punch_talking_head",
    "orientation": "portrait_9_16",
    "fps": 24,
    "bpm": 153.8,
    "shotMedianSec": 4.083,
    "first3sCutsTarget": 2,
    "jCutFrames": 0,
    "subtitle": {
      "fontFamily": "'Source Han Sans CN', 'Noto Sans SC', sans-serif",
      "fontSizePx": 64,
      "englishFontSizePx": 22,
      "bilingual": false,
      "keywordScale": 1.32,
      "primaryColor": "#FFFFFF",
      "keywordColor": "#FBDE51",
      "strokeColor": "#1F1B18",
      "strokeWidth": 5,
      "bgBoxColor": "#00000000",
      "bgRadius": 0,
      "safeYRatio": 0.83,
      "animation": "pop_spring"
    },
    "cover": {
      "layoutId": "warm_desk_keynote_pop",
      "layoutName": "暖琥珀书桌中景 + 全屏波点黑板关键词卡",
      "mainSubRatio": 1.7,
      "highlightStyle": "屏中巨型双行花字 + 暗金波点剪影全屏转场 + 结构化白底表格"
    },
    "color": {
      "yMean255": 97.1,
      "satMeanPct": 51.1,
      "warmthIndex": 63.9,
      "toneMood": "深沉低饱和质感调",
      "paletteHex": [
        "#0B0804",
        "#534E4D",
        "#29180B",
        "#8E6E4F",
        "#6F5135"
      ],
      "lutFile": "style_07.cube",
      "cssFilter": "brightness(0.973) contrast(0.978) saturate(1.28) sepia(0.25)"
    },
    "packaging": {
      "transitions": [
        "硬切",
        "全屏暗金波点图形切",
        "表格弹窗切入"
      ],
      "elements": [
        "透明导师履历名牌",
        "屏中巨型双行重音字",
        "波点剪影隐喻插画",
        "4x4方法论工具表格"
      ],
      "sfxPerMinTarget": 76,
      "visualPerMinTarget": 16
    }
  },
  "style_08": {
    "styleId": "style_08",
    "styleName": "真实生活随拍 · 文艺双语宋体微跳剪风",
    "archetypeId": "arch_05_cinema_slow_space",
    "orientation": "portrait_9_16",
    "fps": 24,
    "bpm": 107.1,
    "shotMedianSec": 5.333,
    "first3sCutsTarget": 2,
    "jCutFrames": 0,
    "subtitle": {
      "fontFamily": "'Source Han Serif CN', 'Noto Sans SC', sans-serif",
      "fontSizePx": 50,
      "englishFontSizePx": 24,
      "bilingual": true,
      "keywordScale": 1.08,
      "primaryColor": "#F6F9F7",
      "keywordColor": "#FFD54F",
      "strokeColor": "#1E2221",
      "strokeWidth": 2,
      "bgBoxColor": "#00000000",
      "bgRadius": 0,
      "safeYRatio": 0.67,
      "animation": "fade_slide_up"
    },
    "cover": {
      "layoutId": "candid_serif_no_cover",
      "layoutName": "无包装封面直入 · 双语宋体电影感版式",
      "mainSubRatio": 1.1,
      "highlightStyle": "中英双语思源宋体 + 零特效音克制呼吸感"
    },
    "color": {
      "yMean255": 127.3,
      "satMeanPct": 24.3,
      "warmthIndex": -4.52,
      "toneMood": "清透冷中性/极简白调",
      "paletteHex": [
        "#FBFDFD",
        "#080D11",
        "#6F918D",
        "#4E3831",
        "#51504D"
      ],
      "lutFile": "style_08.cube",
      "cssFilter": "brightness(1.008) contrast(1.099) saturate(0.868) sepia(0.0)"
    },
    "packaging": {
      "transitions": [
        "去气口硬跳剪 (Jump-cut)"
      ],
      "elements": [
        "双语思源宋体字幕",
        "左中竖排无边框宋体词条"
      ],
      "sfxPerMinTarget": 15,
      "visualPerMinTarget": 4
    }
  },
  "style_09": {
    "styleId": "style_09",
    "styleName": "电影剪辑工作站 · 3D 胶片墙极客风",
    "archetypeId": "arch_01_tech_ui_console",
    "orientation": "landscape_16_9",
    "fps": 30,
    "bpm": 120.0,
    "shotMedianSec": 2.117,
    "first3sCutsTarget": 2,
    "jCutFrames": 3,
    "subtitle": {
      "fontFamily": "'Source Han Sans CN', 'Noto Sans SC', sans-serif",
      "fontSizePx": 36,
      "englishFontSizePx": 20,
      "bilingual": true,
      "keywordScale": 1.05,
      "primaryColor": "#FCFCF6",
      "keywordColor": "#FFD54F",
      "strokeColor": "#050505",
      "strokeWidth": 2,
      "bgBoxColor": "#00000000",
      "bgRadius": 0,
      "safeYRatio": 0.9,
      "animation": "hard_cut"
    },
    "cover": {
      "layoutId": "cyber_film_workstation",
      "layoutName": "暗黑极客剪辑室 · 2.39:1 宽幅画中画版式",
      "mainSubRatio": 2.2,
      "highlightStyle": "取景器 HUD 细线角标 + 等宽数字计时 + 衬线主标题"
    },
    "color": {
      "yMean255": 36.8,
      "satMeanPct": 43.4,
      "warmthIndex": 10.79,
      "toneMood": "深沉低饱和质感调",
      "paletteHex": [
        "#0F0C09",
        "#2F2E2B",
        "#51504D",
        "#70706D",
        "#EEF1EF"
      ],
      "lutFile": "style_09.cube",
      "cssFilter": "brightness(0.934) contrast(1.24) saturate(1.28) sepia(0.09)"
    },
    "packaging": {
      "transitions": [
        "3D镜头穿梭",
        "暗场UI界面切屏",
        "VHS复古录像带叠加"
      ],
      "elements": [
        "3D胶片墙",
        "取景器REC角标",
        "2.39:1遮幅画中画",
        "终端CHANGELOG卡片"
      ],
      "sfxPerMinTarget": 69,
      "visualPerMinTarget": 16
    }
  },
  "style_10": {
    "styleId": "style_10",
    "styleName": "暗黑幕后工作室 · 右下固定画中画教程风",
    "archetypeId": "arch_01_tech_ui_console",
    "orientation": "landscape_16_9",
    "fps": 25,
    "bpm": 93.8,
    "shotMedianSec": 17.76,
    "first3sCutsTarget": 2,
    "jCutFrames": 0,
    "subtitle": {
      "fontFamily": "'Source Han Sans CN', 'Noto Sans SC', sans-serif",
      "fontSizePx": 44,
      "englishFontSizePx": 22,
      "bilingual": false,
      "keywordScale": 1.12,
      "primaryColor": "#FFFEFB",
      "keywordColor": "#FFD54F",
      "strokeColor": "#080406",
      "strokeWidth": 3,
      "bgBoxColor": "#00000000",
      "bgRadius": 0,
      "safeYRatio": 0.91,
      "animation": "hard_cut"
    },
    "cover": {
      "layoutId": "dark_studio_pip_bottom_right",
      "layoutName": "暗黑工作台 + 右下角常驻主播画中画",
      "mainSubRatio": 2.4,
      "highlightStyle": "背后巨型白字「幕后」+ 三栏蓝色卡片对照"
    },
    "color": {
      "yMean255": 47.4,
      "satMeanPct": 26.1,
      "warmthIndex": 5.18,
      "toneMood": "深沉低饱和质感调",
      "paletteHex": [
        "#0E0D0E",
        "#2E2B2C",
        "#746E6E",
        "#6B574E",
        "#50332D"
      ],
      "lutFile": "style_10.cube",
      "cssFilter": "brightness(0.934) contrast(1.24) saturate(0.932) sepia(0.043)"
    },
    "packaging": {
      "transitions": [
        "中心样片放大推入",
        "暗黑工作台UI交叉叠化"
      ],
      "elements": [
        "右下圆角主播PIP",
        "黑胶旋转角标",
        "声波时间轴图示",
        "三栏方案对比卡"
      ],
      "sfxPerMinTarget": 78,
      "visualPerMinTarget": 12
    }
  },
  "style_11": {
    "styleId": "style_11",
    "styleName": "人物前景遮罩巨字 · 高级感杂志教程风",
    "archetypeId": "arch_03_editorial_matte_depth",
    "orientation": "landscape_16_9",
    "fps": 24,
    "bpm": 130.4,
    "shotMedianSec": 1.354,
    "first3sCutsTarget": 2,
    "jCutFrames": 3,
    "subtitle": {
      "fontFamily": "'Source Han Sans CN', 'Noto Sans SC', sans-serif",
      "fontSizePx": 38,
      "englishFontSizePx": 22,
      "bilingual": false,
      "keywordScale": 1.12,
      "primaryColor": "#FFFFFF",
      "keywordColor": "#F4AB09",
      "strokeColor": "#1E1E1E",
      "strokeWidth": 2,
      "bgBoxColor": "#00000000",
      "bgRadius": 0,
      "safeYRatio": 0.9,
      "animation": "hard_cut"
    },
    "cover": {
      "layoutId": "depth_matte_behind_subject",
      "layoutName": "人物前景遮罩穿插巨字 + 极简高反差全屏卡",
      "mainSubRatio": 3.0,
      "highlightStyle": "珊瑚红#E57373与琥珀黄#F4AB09巨型无描边净字 + 极简负空间"
    },
    "color": {
      "yMean255": 112.9,
      "satMeanPct": 22.8,
      "warmthIndex": 5.02,
      "toneMood": "均衡自然高通透调",
      "paletteHex": [
        "#090909",
        "#F7F7F7",
        "#2D2C2B",
        "#95928D",
        "#CBCFD2"
      ],
      "lutFile": "style_11.cube",
      "cssFilter": "brightness(1.008) contrast(1.24) saturate(0.814) sepia(0.042)"
    },
    "packaging": {
      "transitions": [
        "硬切",
        "J-cut声音先行切案例",
        "全屏渐变色卡切入"
      ],
      "elements": [
        "人物前景抠像遮罩字",
        "深红渐变留白大字卡",
        "电影2.39:1拉片画框"
      ],
      "sfxPerMinTarget": 70,
      "visualPerMinTarget": 15
    }
  },
  "style_12": {
    "styleId": "style_12",
    "styleName": "Apple/Notion 极简白底 · 人像抠像卡片风",
    "archetypeId": "arch_03_editorial_matte_depth",
    "orientation": "portrait_9_16",
    "fps": 24,
    "bpm": 146.3,
    "shotMedianSec": 65.812,
    "first3sCutsTarget": 2,
    "jCutFrames": 0,
    "subtitle": {
      "fontFamily": "'Source Han Sans CN', 'Noto Sans SC', sans-serif",
      "fontSizePx": 54,
      "englishFontSizePx": 22,
      "bilingual": false,
      "keywordScale": 1.12,
      "primaryColor": "#FFFFFF",
      "keywordColor": "#FFD54F",
      "strokeColor": "#2A2A2A",
      "strokeWidth": 4,
      "bgBoxColor": "#00000000",
      "bgRadius": 0,
      "safeYRatio": 0.77,
      "animation": "hard_cut"
    },
    "cover": {
      "layoutId": "apple_notion_light_top",
      "layoutName": "极净浅灰白底 · 上方结构化 Notion 卡片 + 下方人像抠像",
      "mainSubRatio": 1.6,
      "highlightStyle": "朱红竖线眉标(#D9383A) + 红色胶囊状态徽章 + 白底微阴影方卡"
    },
    "color": {
      "yMean255": 224.7,
      "satMeanPct": 5.1,
      "warmthIndex": 0.97,
      "toneMood": "均衡自然高通透调",
      "paletteHex": [
        "#F1F3F5",
        "#D0D6CE",
        "#EEC8B2",
        "#D3AC94",
        "#2F2D2A"
      ],
      "lutFile": "style_12.cube",
      "cssFilter": "brightness(1.066) contrast(1.24) saturate(0.76) sepia(0.008)"
    },
    "packaging": {
      "transitions": [
        "卡片内容平滑替换",
        "人像微缩放"
      ],
      "elements": [
        "红竖线眉标",
        "Notion风白底要点列表",
        "过去vs现在左右对比卡",
        "流程图箭头"
      ],
      "sfxPerMinTarget": 40,
      "visualPerMinTarget": 16
    }
  },
  "style_13": {
    "styleId": "style_13",
    "styleName": "建筑空间漫游 · 电影感慢调奢享风",
    "archetypeId": "arch_05_cinema_slow_space",
    "orientation": "portrait_9_16",
    "fps": 30,
    "bpm": 95.2,
    "shotMedianSec": 1.567,
    "first3sCutsTarget": 2,
    "jCutFrames": 0,
    "subtitle": {
      "fontFamily": "'Source Han Sans CN', 'Noto Sans SC', sans-serif",
      "fontSizePx": 46,
      "englishFontSizePx": 22,
      "bilingual": false,
      "keywordScale": 1.08,
      "primaryColor": "#FAFFFA",
      "keywordColor": "#FFD54F",
      "strokeColor": "#26251C",
      "strokeWidth": 2,
      "bgBoxColor": "#00000000",
      "bgRadius": 0,
      "safeYRatio": 0.72,
      "animation": "hard_cut"
    },
    "cover": {
      "layoutId": "architectural_no_overlay",
      "layoutName": "零遮挡实景长镜 · 电影字幕极简版式",
      "mainSubRatio": 1.0,
      "highlightStyle": "暖木温润调色(warmth=26.5) + 极简无框细投影字幕"
    },
    "color": {
      "yMean255": 95.9,
      "satMeanPct": 33.8,
      "warmthIndex": 26.52,
      "toneMood": "深沉低饱和质感调",
      "paletteHex": [
        "#0E0D0B",
        "#704E33",
        "#8E6D50",
        "#91948D",
        "#31302B"
      ],
      "lutFile": "style_13.cube",
      "cssFilter": "brightness(0.972) contrast(1.194) saturate(1.207) sepia(0.221)"
    },
    "packaging": {
      "transitions": [
        "缓慢推拉长镜",
        "柔和叠化"
      ],
      "elements": [
        "极简细字投影字幕"
      ],
      "sfxPerMinTarget": 20,
      "visualPerMinTarget": 2
    }
  },
  "style_14": {
    "styleId": "style_14",
    "styleName": "毛玻璃卡片叠映 · 居家创始人信任风",
    "archetypeId": "arch_04_punch_talking_head",
    "orientation": "portrait_9_16",
    "fps": 24,
    "bpm": 120.0,
    "shotMedianSec": 8.708,
    "first3sCutsTarget": 2,
    "jCutFrames": 0,
    "subtitle": {
      "fontFamily": "'Source Han Sans CN', 'Noto Sans SC', sans-serif",
      "fontSizePx": 50,
      "englishFontSizePx": 22,
      "bilingual": false,
      "keywordScale": 1.15,
      "primaryColor": "#FFFFFF",
      "keywordColor": "#FFD54F",
      "strokeColor": "#2B3242",
      "strokeWidth": 3,
      "bgBoxColor": "#00000000",
      "bgRadius": 0,
      "safeYRatio": 0.74,
      "animation": "fade_slide_up"
    },
    "cover": {
      "layoutId": "frosted_top_cards_founder",
      "layoutName": "顶部渐显宋体主题 + 半屏毛玻璃实拍对比图卡",
      "mainSubRatio": 1.65,
      "highlightStyle": "圆角横向对比图条 + 毛玻璃叠层大数字章节卡"
    },
    "color": {
      "yMean255": 142.8,
      "satMeanPct": 20.5,
      "warmthIndex": 0.9,
      "toneMood": "均衡自然高通透调",
      "paletteHex": [
        "#E8ECED",
        "#35476E",
        "#D1D3D3",
        "#2B3551",
        "#514E51"
      ],
      "lutFile": "style_14.cube",
      "cssFilter": "brightness(1.046) contrast(1.135) saturate(0.76) sepia(0.008)"
    },
    "packaging": {
      "transitions": [
        "背景高斯虚化过渡",
        "圆角图条连续弹窗"
      ],
      "elements": [
        "宋体顶部渐显标题",
        "细字创始人名牌",
        "4连横条实拍图卡",
        "毛玻璃02大数字卡"
      ],
      "sfxPerMinTarget": 92,
      "visualPerMinTarget": 18
    }
  },
  "style_15": {
    "styleId": "style_15",
    "styleName": "AI Agent 流程导航 · 工牌/账单小票风",
    "archetypeId": "arch_01_tech_ui_console",
    "orientation": "landscape_16_9",
    "fps": 24,
    "bpm": 162.2,
    "shotMedianSec": 11.708,
    "first3sCutsTarget": 2,
    "jCutFrames": 0,
    "subtitle": {
      "fontFamily": "'Source Han Sans CN', 'Noto Sans SC', sans-serif",
      "fontSizePx": 40,
      "englishFontSizePx": 22,
      "bilingual": false,
      "keywordScale": 1.15,
      "primaryColor": "#FBFFFE",
      "keywordColor": "#FBE088",
      "strokeColor": "#1E221D",
      "strokeWidth": 3,
      "bgBoxColor": "#00000000",
      "bgRadius": 0,
      "safeYRatio": 0.9,
      "animation": "pop_spring"
    },
    "cover": {
      "layoutId": "agent_workflow_step_bar",
      "layoutName": "顶部步骤条进度导航 + 拟物工牌与制片小票 UI",
      "mainSubRatio": 2.6,
      "highlightStyle": "亮黄#FBE088粗字黑描边 + 挂绳工牌卡 + 制片账单小票"
    },
    "color": {
      "yMean255": 84.3,
      "satMeanPct": 29.7,
      "warmthIndex": 6.84,
      "toneMood": "暗黑高对比科技调",
      "paletteHex": [
        "#0D1013",
        "#8F8E8F",
        "#F5F5EE",
        "#71706F",
        "#302D2E"
      ],
      "lutFile": "style_15.cube",
      "cssFilter": "brightness(0.934) contrast(0.966) saturate(1.061) sepia(0.057)"
    },
    "packaging": {
      "transitions": [
        "工作流步骤高亮跳转",
        "人物居中缩入左下PIP"
      ],
      "elements": [
        "顶部步骤条导航",
        "黄色工牌吊绳",
        "深色终端清单",
        "制片小票账单UI"
      ],
      "sfxPerMinTarget": 72,
      "visualPerMinTarget": 18
    }
  },
  "style_16": {
    "styleId": "style_16",
    "styleName": "商业悬疑纪实 · 撕纸拍立得拼贴风",
    "archetypeId": "arch_02_high_contrast_story",
    "orientation": "portrait_9_16",
    "fps": 30,
    "bpm": 109.1,
    "shotMedianSec": 2.2,
    "first3sCutsTarget": 2,
    "jCutFrames": 3,
    "subtitle": {
      "fontFamily": "'Source Han Sans CN', 'Noto Sans SC', sans-serif",
      "fontSizePx": 48,
      "englishFontSizePx": 22,
      "bilingual": false,
      "keywordScale": 1.25,
      "primaryColor": "#FFF8F3",
      "keywordColor": "#E1A187",
      "strokeColor": "#26150E",
      "strokeWidth": 3,
      "bgBoxColor": "#00000000",
      "bgRadius": 0,
      "safeYRatio": 0.84,
      "animation": "hard_cut"
    },
    "cover": {
      "layoutId": "polaroid_torn_paper_finance",
      "layoutName": "拍立得画中画 + 报刊撕纸拼贴",
      "mainSubRatio": 2.4,
      "highlightStyle": "超大白色数字`360亿` + 朱红报刊衬线标题 + 锯齿撕纸分割线"
    },
    "color": {
      "yMean255": 100.6,
      "satMeanPct": 25.2,
      "warmthIndex": 7.45,
      "toneMood": "深沉低饱和质感调",
      "paletteHex": [
        "#0C0B0B",
        "#302E2F",
        "#514F4D",
        "#B4B2B0",
        "#D0CECC"
      ],
      "lutFile": "style_16.cube",
      "cssFilter": "brightness(0.978) contrast(1.138) saturate(0.9) sepia(0.062)"
    },
    "packaging": {
      "transitions": [
        "航拍转拍立得画中画",
        "锯齿撕纸分屏",
        "报刊全屏章节卡"
      ],
      "elements": [
        "复古拍立得边框",
        "锯齿撕纸上下分屏",
        "报刊衬线章节卡",
        "红条数据标"
      ],
      "sfxPerMinTarget": 88,
      "visualPerMinTarget": 20
    }
  },
  "style_17": {
    "styleId": "style_17",
    "styleName": "深蓝 SaaS 演示 · 右下圆角画中画风",
    "archetypeId": "arch_01_tech_ui_console",
    "orientation": "portrait_9_16",
    "fps": 24,
    "bpm": 93.8,
    "shotMedianSec": 1.75,
    "first3sCutsTarget": 2,
    "jCutFrames": 3,
    "subtitle": {
      "fontFamily": "'Source Han Sans CN', 'Noto Sans SC', sans-serif",
      "fontSizePx": 46,
      "englishFontSizePx": 22,
      "bilingual": false,
      "keywordScale": 1.15,
      "primaryColor": "#F9FFFF",
      "keywordColor": "#F1E1B3",
      "strokeColor": "#0E172B",
      "strokeWidth": 2,
      "bgBoxColor": "#00000000",
      "bgRadius": 0,
      "safeYRatio": 0.7,
      "animation": "hard_cut"
    },
    "cover": {
      "layoutId": "dark_navy_saas_pip",
      "layoutName": "深海蓝科技面板 · 上2/3演示区 + 右下真人PIP",
      "mainSubRatio": 1.8,
      "highlightStyle": "深蓝微边框卡片 + 暖金胶囊标(`#F1E1B3`) + 衬线混排步骤卡"
    },
    "color": {
      "yMean255": 63.5,
      "satMeanPct": 50.9,
      "warmthIndex": -13.98,
      "toneMood": "暗黑高对比科技调",
      "paletteHex": [
        "#0E172B",
        "#17264B",
        "#D0B292",
        "#F7F8F9",
        "#2C2C30"
      ],
      "lutFile": "style_17.cube",
      "cssFilter": "brightness(0.934) contrast(1.226) saturate(1.28) sepia(0.0)"
    },
    "packaging": {
      "transitions": [
        "演示卡片无缝切换",
        "流程图节点点亮"
      ],
      "elements": [
        "深蓝科技边框卡片",
        "右下圆角真人PIP",
        "机器人节点架构图",
        "STEP混排标卡"
      ],
      "sfxPerMinTarget": 75,
      "visualPerMinTarget": 16
    }
  },
  "style_18": {
    "styleId": "style_18",
    "styleName": "白板方法论 · 手写字纸片反差条风",
    "archetypeId": "arch_03_editorial_matte_depth",
    "orientation": "portrait_9_16",
    "fps": 24,
    "bpm": 111.1,
    "shotMedianSec": 2.708,
    "first3sCutsTarget": 2,
    "jCutFrames": 0,
    "subtitle": {
      "fontFamily": "'QingSongShouXieTi / Source Han Sans CN', 'Noto Sans SC', sans-serif",
      "fontSizePx": 64,
      "englishFontSizePx": 22,
      "bilingual": false,
      "keywordScale": 1.18,
      "primaryColor": "#1E1E1E",
      "keywordColor": "#FFD54F",
      "strokeColor": "#FFFFFF",
      "strokeWidth": 0,
      "bgBoxColor": "#00000000",
      "bgRadius": 0,
      "safeYRatio": 0.75,
      "animation": "hard_cut"
    },
    "cover": {
      "layoutId": "whiteboard_matte_sticky",
      "layoutName": "浅灰手写水印底板 + 黑白反差条 + 暗调白板清单",
      "mainSubRatio": 1.5,
      "highlightStyle": "纯黑底白字矩形反差块 + 手写字体亲和力 + 暗调亮黄关键句"
    },
    "color": {
      "yMean255": 75.2,
      "satMeanPct": 17.1,
      "warmthIndex": 7.62,
      "toneMood": "深沉低饱和质感调",
      "paletteHex": [
        "#0F0E0F",
        "#2A2828",
        "#94918B",
        "#F4F2F2",
        "#D3D1CD"
      ],
      "lutFile": "style_18.cube",
      "cssFilter": "brightness(0.945) contrast(1.24) saturate(0.76) sepia(0.064)"
    },
    "packaging": {
      "transitions": [
        "硬切进花絮窗",
        "暗调蒙版清单展开"
      ],
      "elements": [
        "前景抠像白板透字",
        "背景水印手写字",
        "纯黑反差矩形条",
        "暗调结构化清单"
      ],
      "sfxPerMinTarget": 50,
      "visualPerMinTarget": 12
    }
  },
  "style_19": {
    "styleId": "style_19",
    "styleName": "专业剪辑大师课 · 顶部细条章节 + 绿块冲击风",
    "archetypeId": "arch_02_high_contrast_story",
    "orientation": "landscape_16_9",
    "fps": 24,
    "bpm": 146.3,
    "shotMedianSec": 1.25,
    "first3sCutsTarget": 2,
    "jCutFrames": 3,
    "subtitle": {
      "fontFamily": "'Source Han Sans CN', 'Noto Sans SC', sans-serif",
      "fontSizePx": 46,
      "englishFontSizePx": 22,
      "bilingual": false,
      "keywordScale": 1.2,
      "primaryColor": "#FFFFFF",
      "keywordColor": "#2ECC71",
      "strokeColor": "#111111",
      "strokeWidth": 3,
      "bgBoxColor": "#00000000",
      "bgRadius": 0,
      "safeYRatio": 0.9,
      "animation": "hard_cut"
    },
    "cover": {
      "layoutId": "pro_editor_chapter_nav",
      "layoutName": "顶部极细章节导航条 + 亮绿圆角方块与左右竖排巨字",
      "mainSubRatio": 2.5,
      "highlightStyle": "高饱和亮绿渐变方框单字块(#10B981) + 双侧竖排巨型白字"
    },
    "color": {
      "yMean255": 77.2,
      "satMeanPct": 27.2,
      "warmthIndex": 0.56,
      "toneMood": "暗黑高对比科技调",
      "paletteHex": [
        "#0F0F0F",
        "#2D2D2F",
        "#515252",
        "#6F7070",
        "#90908E"
      ],
      "lutFile": "style_19.cube",
      "cssFilter": "brightness(0.934) contrast(1.204) saturate(0.971) sepia(0.005)"
    },
    "packaging": {
      "transitions": [
        "高速B-roll素材硬切",
        "双侧竖排巨字黑底切"
      ],
      "elements": [
        "顶部4栏极细章节导航",
        "绿底方框单字花字",
        "双侧竖排白字夹心画中画",
        "达芬奇示波器录屏"
      ],
      "sfxPerMinTarget": 64,
      "visualPerMinTarget": 18
    }
  },
  "style_20": {
    "styleId": "style_20",
    "styleName": "模型盒悬念开篇 · 书墙双语毛玻璃金句风",
    "archetypeId": "arch_03_editorial_matte_depth",
    "orientation": "landscape_16_9",
    "fps": 24,
    "bpm": 90.9,
    "shotMedianSec": 1.271,
    "first3sCutsTarget": 2,
    "jCutFrames": 3,
    "subtitle": {
      "fontFamily": "'Source Han Sans CN', 'Noto Sans SC', sans-serif",
      "fontSizePx": 42,
      "englishFontSizePx": 24,
      "bilingual": true,
      "keywordScale": 1.1,
      "primaryColor": "#FFFFFF",
      "keywordColor": "#E6D267",
      "strokeColor": "#120B01",
      "strokeWidth": 3,
      "bgBoxColor": "#00000000",
      "bgRadius": 0,
      "safeYRatio": 0.87,
      "animation": "fade_slide_up"
    },
    "cover": {
      "layoutId": "physical_prop_bookshelf_highlight",
      "layoutName": "实物道具开篇 + 毛玻璃编号清单 + 荧光黄绿底块金句",
      "mainSubRatio": 1.8,
      "highlightStyle": "中英同步关键词高亮 + 荧光黄绿底块黑字金句条"
    },
    "color": {
      "yMean255": 98.3,
      "satMeanPct": 30.1,
      "warmthIndex": 22.32,
      "toneMood": "深沉低饱和质感调",
      "paletteHex": [
        "#0F0C0C",
        "#342E2B",
        "#534E4A",
        "#F6F7F7",
        "#736F6D"
      ],
      "lutFile": "style_20.cube",
      "cssFilter": "brightness(0.965) contrast(0.963) saturate(1.075) sepia(0.186)"
    },
    "packaging": {
      "transitions": [
        "实物盒子跳切人物",
        "背景虚化+毛玻璃清单浮现"
      ],
      "elements": [
        "实物微缩开篇模型",
        "卡通描边贴纸(AI)",
        "暗色毛玻璃编号清单",
        "荧光黄绿金句底块"
      ],
      "sfxPerMinTarget": 88,
      "visualPerMinTarget": 16
    }
  }
};

export function getStylePreset(styleId: string): StyleSpectrumPreset {
  return STYLE_SPECTRUM_PRESETS[styleId] ?? STYLE_SPECTRUM_PRESETS['style_02'];
}
