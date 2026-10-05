import React from 'react';
import { AbsoluteFill } from 'remotion';
import { PenPath } from '../components/Pen';
import { PresenterTrack } from '../components/Presenter';
import { SfxTrack, VoiceTrack } from '../components/Scene';
import { LineKaraoke } from '../components/Type';
import { ProductTitle } from '../scenes/Reveal';
import { lineOf, wordFrame } from '../timeline';
import { COLOR } from '../theme';
import { MARGIN, PRESENTER_LOWER, TEASER_SIZE, TEXT_WIDTH } from './layout';
import { teaserScene } from './timeline';

const scene = teaserScene('reveal');
const reveal2 = lineOf(scene, 'reveal2');
const titleAt = wordFrame(reveal2, 'Dispute');
const TITLE_Y = 440;
const UNDERLINE_Y = TITLE_Y + 420;

export const TeaserReveal: React.FC = () => (
  <AbsoluteFill>
    <LineKaraoke line={reveal2} until={titleAt - 2} at={{ x: MARGIN, y: TITLE_Y + 30 }} size={84} width={TEXT_WIDTH} lead={6} />
    <ProductTitle at={titleAt} x={MARGIN} y={TITLE_Y} size={170} />
    <svg width={TEASER_SIZE.width} height={TEASER_SIZE.height} style={{ position: 'absolute' }}>
      <PenPath d={`M ${MARGIN} ${UNDERLINE_Y} L ${MARGIN + TEXT_WIDTH} ${UNDERLINE_Y}`} from={titleAt + 6} to={titleAt + 26} color={COLOR.signal} width={3} />
    </svg>
    <PresenterTrack lines={scene.lines} mode="full" box={PRESENTER_LOWER} until={scene.frames} enterAt={0} />
    <VoiceTrack scene={scene} />
    <SfxTrack cues={[{ at: titleAt, sfx: 'impactSoft_heavy_003', volume: 0.45 }]} />
  </AbsoluteFill>
);
