import React from 'react';
import { Composition } from 'remotion';
import { Launch } from './Launch';
import { TOTAL_FRAMES } from './timeline';
import { FPS, HEIGHT, WIDTH } from './theme';

export const Root: React.FC = () => (
  <Composition id="Launch" component={Launch} durationInFrames={TOTAL_FRAMES} fps={FPS} width={WIDTH} height={HEIGHT} />
);
