import React from 'react';
import {Composition} from 'remotion';
import {NarrativeCard} from './NarrativeCard';
import {StyleSpectrumShowcase} from '../style_spectrum/remotion/StyleSpectrumPack';

export const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="NarrativeCard"
        component={NarrativeCard}
        durationInFrames={90}
        fps={30}
        width={1080}
        height={1920}
        defaultProps={{
          mode: 'kinetic_text',
          title: '核心真相',
          items: [{label: '先音后画，以音定画', subtitle: '声音锁定后再铺镜头'}],
          accentColor: '#FFD54F',
        }}
      />
      <Composition
        id="StyleSpectrumShowcase"
        component={StyleSpectrumShowcase}
        durationInFrames={180}
        fps={30}
        width={1080}
        height={1920}
        defaultProps={{
          styleId: 'style_02',
          coverTitle: '没上过大学不丢人，没文化才可怕',
          coverSubTitle: '风格谱复刻模板 · 1080×1920',
          subtitleText: '90%的人第一步就错了',
          subtitleEnglish: '90% of creators miss the first step',
          keywords: ['第一步', '错了'],
          chapters: ['① 钩子', '② 拆解', '③ 对比', '④ 收口'],
          bullets: ['镜头中位 1.17s', '事件对齐 79.4%', '描边 5px 无底框'],
        }}
      />
    </>
  );
};
