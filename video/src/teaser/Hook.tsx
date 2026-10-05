import React from 'react';
import { AbsoluteFill } from 'remotion';
import { PresenterTrack } from '../components/Presenter';
import { Appear, SfxTrack, VoiceTrack } from '../components/Scene';
import { FactChip, LineKaraoke } from '../components/Type';
import { FACTS } from '../facts';
import { WaitClock, type ClockLayout } from '../scenes/Cold';
import { lineOf, wordFrame } from '../timeline';
import { MARGIN, PRESENTER_LOWER, TEXT_WIDTH } from './layout';
import { teaserScene } from './timeline';

const scene = teaserScene('hook');
const cold3 = lineOf(scene, 'cold3');
const CLOCK: ClockLayout = { x: 540, y: 810, r: 250 };
// The dial is already drawn on frame 0 and lands on 37 h by "wait", inside the first two seconds.
const CLOCK_DRAWN_BEFORE = -16;
const countTo = wordFrame(cold3, 'wait');

export const Hook: React.FC = () => (
  <AbsoluteFill>
    <LineKaraoke line={cold3} until={scene.frames} at={{ x: MARGIN, y: 180 }} size={70} width={TEXT_WIDTH} emphasis={['thirty-seven', 'hours']} />
    <WaitClock clock={CLOCK} appearAt={CLOCK_DRAWN_BEFORE} countFrom={0} countTo={countTo}>
      <Appear at={countTo + 4} style={{ left: MARGIN, top: CLOCK.y + CLOCK.r + 36, width: TEXT_WIDTH, display: 'flex', justifyContent: 'center' }}>
        <FactChip fact={FACTS.firstResponse} size={26} />
      </Appear>
    </WaitClock>
    <PresenterTrack lines={scene.lines} mode="full" box={PRESENTER_LOWER} until={scene.frames} enterAt={CLOCK_DRAWN_BEFORE} />
    <VoiceTrack scene={scene} />
    <SfxTrack cues={[{ at: countTo, sfx: 'impactSoft_heavy_003', volume: 0.4 }]} />
  </AbsoluteFill>
);
