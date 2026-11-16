import React from 'react';
import {
  AbsoluteFill,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';
import {
  getStylePreset,
  StyleSpectrumPreset,
} from './StyleSpectrumPresets';

export interface StyleCoverCardProps {
  preset: StyleSpectrumPreset;
  mainTitle: string;
  subTitle?: string;
  badgeText?: string;
}

export const StyleCoverCard: React.FC<StyleCoverCardProps> = ({
  preset,
  mainTitle,
  subTitle = '底层逻辑拆解',
  badgeText = '重点',
}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const enter = spring({frame, fps, config: {damping: 14, stiffness: 180}});
  const kwColor = preset.subtitle.keywordColor;
  const priColor = preset.subtitle.primaryColor;
  const layoutId = preset.cover.layoutId;

  const isLightLayout =
    layoutId === 'apple_notion_light_top' ||
    layoutId === 'paper_cutout_hero' ||
    layoutId === 'whiteboard_matte_sticky';

  const cardBg = isLightLayout
    ? 'rgba(248, 250, 252, 0.92)'
    : 'rgba(15, 23, 42, 0.78)';
  const titleColor = isLightLayout ? '#111827' : priColor;

  return (
    <div
      style={{
        position: 'absolute',
        left: 72,
        right: 140,
        top: layoutId === 'calligraphy_contrast_bottom' ? 980 : 280,
        padding: '28px 34px',
        borderRadius: layoutId === 'film_reel_console' ? 12 : 24,
        backgroundColor:
          layoutId === 'depth_matte_behind_subject' ? 'transparent' : cardBg,
        borderLeft:
          layoutId === 'apple_notion_light_top'
            ? '10px solid #D9383A'
            : `8px solid ${kwColor}`,
        transform: `translateY(${interpolate(enter, [0, 1], [32, 0])}px) scale(${interpolate(enter, [0, 1], [0.94, 1])})`,
        opacity: enter,
        fontFamily: preset.subtitle.fontFamily,
      }}
    >
      <div
        style={{
          display: 'inline-block',
          padding: '6px 18px',
          borderRadius: 999,
          backgroundColor: kwColor,
          color: '#111827',
          fontSize: 28,
          fontWeight: 800,
          marginBottom: 16,
        }}
      >
        {badgeText} · {preset.cover.layoutName}
      </div>
      <div
        style={{
          fontSize: Math.round(preset.subtitle.fontSizePx * 1.45),
          fontWeight: 900,
          lineHeight: 1.18,
          color: titleColor,
          textShadow: isLightLayout
            ? 'none'
            : `0 4px 16px ${preset.subtitle.strokeColor}`,
        }}
      >
        {mainTitle}
      </div>
      {subTitle ? (
        <div
          style={{
            marginTop: 12,
            fontSize: Math.round(preset.subtitle.fontSizePx * 0.75),
            fontWeight: 700,
            color: kwColor,
          }}
        >
          {subTitle}
        </div>
      ) : null}
    </div>
  );
};

export interface StyleSubtitleBarProps {
  preset: StyleSpectrumPreset;
  text: string;
  englishText?: string;
  keywords?: string[];
}

export const StyleSubtitleBar: React.FC<StyleSubtitleBarProps> = ({
  preset,
  text,
  englishText,
  keywords = [],
}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const sub = preset.subtitle;

  let scale = 1;
  let opacity = 1;
  let translateY = 0;

  if (sub.animation === 'pop_spring') {
    const p = spring({frame, fps, config: {damping: 13, stiffness: 220}});
    scale = interpolate(p, [0, 1], [0.84, 1]);
    opacity = p;
  } else if (sub.animation === 'fade_slide_up') {
    const p = interpolate(frame, [0, 7], [0, 1], {extrapolateRight: 'clamp'});
    translateY = interpolate(p, [0, 1], [16, 0]);
    opacity = p;
  }

  // 按关键词高亮切分文本
  const segments: {part: string; isKw: boolean}[] = [];
  if (keywords.length === 0) {
    segments.push({part: text, isKw: false});
  } else {
    let cursor = 0;
    const sorted = [...keywords].filter(Boolean);
    while (cursor < text.length) {
      let hitKw = '';
      let hitIdx = text.length;
      for (const kw of sorted) {
        const idx = text.indexOf(kw, cursor);
        if (idx !== -1 && idx < hitIdx) {
          hitIdx = idx;
          hitKw = kw;
        }
      }
      if (hitKw) {
        if (hitIdx > cursor) {
          segments.push({part: text.slice(cursor, hitIdx), isKw: false});
        }
        segments.push({part: hitKw, isKw: true});
        cursor = hitIdx + hitKw.length;
      } else {
        segments.push({part: text.slice(cursor), isKw: false});
        break;
      }
    }
  }

  const isBottomPercent = Math.round(sub.safeYRatio * 100);
  const hasVisibleBox =
    sub.bgBoxColor && sub.bgBoxColor !== '#00000000' && sub.bgBoxColor !== 'transparent';

  return (
    <div
      style={{
        position: 'absolute',
        left: 80,
        right: 140,
        top: `${isBottomPercent}%`,
        transform: `translateY(${translateY}px) scale(${scale})`,
        opacity,
        textAlign: 'center',
        fontFamily: sub.fontFamily,
      }}
    >
      <div
        style={{
          display: 'inline-block',
          padding: hasVisibleBox ? '12px 26px' : '4px 10px',
          borderRadius: sub.bgRadius,
          backgroundColor: hasVisibleBox ? sub.bgBoxColor : 'transparent',
          WebkitTextStroke:
            sub.strokeWidth > 0 ? `${sub.strokeWidth}px ${sub.strokeColor}` : undefined,
          paintOrder: 'stroke fill',
          textShadow: `0 3px 10px ${sub.strokeColor}`,
        }}
      >
        <div
          style={{
            fontSize: sub.fontSizePx,
            fontWeight: 800,
            lineHeight: 1.25,
            color: sub.primaryColor,
          }}
        >
          {segments.map((seg, i) => (
            <span
              key={i}
              style={{
                color: seg.isKw ? sub.keywordColor : sub.primaryColor,
                fontSize: seg.isKw
                  ? Math.round(sub.fontSizePx * sub.keywordScale)
                  : sub.fontSizePx,
                fontWeight: seg.isKw ? 900 : 800,
              }}
            >
              {seg.part}
            </span>
          ))}
        </div>
        {sub.bilingual && englishText ? (
          <div
            style={{
              marginTop: 6,
              fontSize: sub.englishFontSizePx,
              fontWeight: 500,
              color: sub.keywordColor,
              opacity: 0.9,
            }}
          >
            {englishText}
          </div>
        ) : null}
      </div>
    </div>
  );
};

export interface StyleChapterBarProps {
  preset: StyleSpectrumPreset;
  chapters: string[];
  activeIndex: number;
}

export const StyleChapterBar: React.FC<StyleChapterBarProps> = ({
  preset,
  chapters,
  activeIndex,
}) => {
  return (
    <div
      style={{
        position: 'absolute',
        top: 64,
        left: 64,
        right: 64,
        display: 'flex',
        gap: 10,
        padding: '10px 14px',
        borderRadius: 12,
        backgroundColor: 'rgba(15, 23, 42, 0.72)',
        fontFamily: preset.subtitle.fontFamily,
      }}
    >
      {chapters.map((ch, idx) => {
        const active = idx === activeIndex;
        return (
          <div
            key={idx}
            style={{
              flex: 1,
              textAlign: 'center',
              padding: '8px 6px',
              borderRadius: 8,
              fontSize: 22,
              fontWeight: active ? 800 : 500,
              backgroundColor: active ? preset.subtitle.keywordColor : 'transparent',
              color: active ? '#111827' : '#CBD5E1',
            }}
          >
            {ch}
          </div>
        );
      })}
    </div>
  );
};

export interface StyleGraphicCardProps {
  preset: StyleSpectrumPreset;
  title: string;
  bullets: string[];
}

export const StyleGraphicCard: React.FC<StyleGraphicCardProps> = ({
  preset,
  title,
  bullets,
}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const pop = spring({frame, fps, config: {damping: 15, stiffness: 170}});
  const palette = preset.color.paletteHex;

  return (
    <div
      style={{
        position: 'absolute',
        left: 72,
        right: 160,
        top: 560,
        padding: '28px 32px',
        borderRadius: 20,
        backgroundColor: 'rgba(17, 24, 39, 0.84)',
        border: `2px solid ${preset.subtitle.keywordColor}`,
        transform: `scale(${interpolate(pop, [0, 1], [0.9, 1])})`,
        opacity: pop,
        fontFamily: preset.subtitle.fontFamily,
        color: '#F8FAFC',
      }}
    >
      <div
        style={{
          fontSize: 38,
          fontWeight: 800,
          color: preset.subtitle.keywordColor,
          marginBottom: 18,
        }}
      >
        {title}
      </div>
      <div style={{display: 'flex', flexDirection: 'column', gap: 14}}>
        {bullets.map((b, idx) => (
          <div
            key={idx}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 14,
              fontSize: 30,
              fontWeight: 600,
              padding: '10px 14px',
              borderRadius: 10,
              backgroundColor: 'rgba(255,255,255,0.06)',
              borderLeft: `6px solid ${palette[idx % palette.length] || preset.subtitle.keywordColor}`,
            }}
          >
            <span>0{idx + 1}</span>
            <span>{b}</span>
          </div>
        ))}
      </div>
    </div>
  );
};

export interface StyleSpectrumShowcaseProps {
  styleId: string;
  coverTitle: string;
  coverSubTitle?: string;
  subtitleText: string;
  subtitleEnglish?: string;
  keywords?: string[];
  chapters?: string[];
  bullets?: string[];
}

export const StyleSpectrumShowcase: React.FC<StyleSpectrumShowcaseProps> = ({
  styleId = 'style_02',
  coverTitle = '先把音轨锁死再剪画面',
  coverSubTitle = '先音后画 · ±1帧精准对齐',
  subtitleText = '90%的人第一步就错了',
  subtitleEnglish = '90% of creators miss the first step',
  keywords = ['第一步', '错了'],
  chapters = ['① 钩子', '② 拆解', '③ 对比', '④ 收口'],
  bullets = ['切点对齐音频事件 ±1帧', '前3秒 ≥2次画面切换', '安全区 X[100,860]'],
}) => {
  const frame = useCurrentFrame();
  const preset = getStylePreset(styleId);
  const bgHex = preset.color.paletteHex[0] || '#0F172A';

  return (
    <AbsoluteFill
      style={{
        backgroundColor: bgHex,
        filter: preset.color.cssFilter,
      }}
    >
      <StyleChapterBar
        preset={preset}
        chapters={chapters}
        activeIndex={Math.min(chapters.length - 1, Math.floor(frame / 45))}
      />
      {frame < 55 ? (
        <StyleCoverCard
          preset={preset}
          mainTitle={coverTitle}
          subTitle={coverSubTitle}
          badgeText={preset.styleId.toUpperCase()}
        />
      ) : (
        <StyleGraphicCard
          preset={preset}
          title={`${preset.styleName} · 核心参数`}
          bullets={bullets}
        />
      )}
      <StyleSubtitleBar
        preset={preset}
        text={subtitleText}
        englishText={subtitleEnglish}
        keywords={keywords}
      />
    </AbsoluteFill>
  );
};
