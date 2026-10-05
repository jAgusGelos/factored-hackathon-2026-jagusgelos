import React from 'react';
import { Composition } from 'remotion';
import { Launch } from './Launch';
import { Teaser } from './Teaser';
import { TEASER_SIZE } from './teaser/layout';
import { TEASER_FRAMES } from './teaser/timeline';
import { TOTAL_FRAMES } from './timeline';
import { FPS, HEIGHT, WIDTH } from './theme';

export const Root: React.FC = () => (
  <>
    <Composition id="Launch" component={Launch} durationInFrames={TOTAL_FRAMES} fps={FPS} width={WIDTH} height={HEIGHT} />
    <Composition id="Teaser" component={Teaser} durationInFrames={TEASER_FRAMES} fps={FPS} width={TEASER_SIZE.width} height={TEASER_SIZE.height} />
  </>
);
