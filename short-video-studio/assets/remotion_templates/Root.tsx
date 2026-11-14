import React from 'react';
import {Composition} from 'remotion';
import {NarrativeCard} from './NarrativeCard';

export const RemotionRoot: React.FC = () => {
  return (
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
  );
};
