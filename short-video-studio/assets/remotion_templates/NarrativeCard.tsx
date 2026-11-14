import React from 'react';
import {AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';

export interface NarrativeProps {
  mode: 'kinetic_text' | 'data_chart' | 'flow_diagram';
  title: string;
  items: {label: string; value?: number; subtitle?: string}[];
  accentColor?: string;
}

export const NarrativeCard: React.FC<NarrativeProps> = ({
  mode = 'kinetic_text',
  title = '核心真相',
  items = [],
  accentColor = '#FFD54F',
}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const titlePop = spring({frame, fps, config: {damping: 14, stiffness: 160}});

  return (
    <AbsoluteFill
      style={{
        backgroundColor: '#0F172A',
        color: '#F8FAFC',
        padding: '220px 100px 480px 100px',
        fontFamily: 'Source Han Sans CN, Noto Sans CJK SC, sans-serif',
        justifyContent: 'center',
      }}
    >
      <div
        style={{
          fontSize: 68,
          fontWeight: 800,
          color: accentColor,
          transform: `translateY(${interpolate(titlePop, [0, 1], [40, 0])}px)`,
          opacity: titlePop,
          marginBottom: 48,
        }}
      >
        {title}
      </div>
      <div style={{display: 'flex', flexDirection: 'column', gap: 28}}>
        {items.map((item, idx) => {
          const itemSpring = spring({
            frame: Math.max(0, frame - (idx + 1) * 8),
            fps,
            config: {damping: 16},
          });
          return (
            <div
              key={idx}
              style={{
                background: 'rgba(255,255,255,0.08)',
                borderLeft: `8px solid ${accentColor}`,
                borderRadius: 16,
                padding: '28px 36px',
                transform: `scale(${interpolate(itemSpring, [0, 1], [0.92, 1])})`,
                opacity: itemSpring,
              }}
            >
              <div style={{fontSize: 44, fontWeight: 700}}>{item.label}</div>
              {mode === 'data_chart' && typeof item.value === 'number' && (
                <div style={{marginTop: 14, background: '#1E293B', height: 26, borderRadius: 13}}>
                  <div
                    style={{
                      width: `${Math.min(100, item.value) * itemSpring}%`,
                      height: '100%',
                      background: accentColor,
                      borderRadius: 13,
                    }}
                  />
                </div>
              )}
              {item.subtitle && (
                <div style={{fontSize: 32, color: '#CBD5E1', marginTop: 10}}>{item.subtitle}</div>
              )}
            </div>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};
