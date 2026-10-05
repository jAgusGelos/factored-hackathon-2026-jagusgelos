import React from 'react';
import { AbsoluteFill } from 'remotion';
import { PresenterTrack } from '../components/Presenter';
import { Appear, SfxTrack, VoiceTrack } from '../components/Scene';
import { MonoLabel } from '../components/Type';
import { ProductTitle, Tagline } from '../scenes/Reveal';
import { lineOf, wordFrame } from '../timeline';
import { BRAND, COLOR, FONT } from '../theme';
import { MARGIN, PRESENTER_LOWER, TEXT_WIDTH } from './layout';
import { teaserScene } from './timeline';

const scene = teaserScene('punch');
const close2 = lineOf(scene, 'close2');
const titleAt = close2.from + 2;
const tryAt = wordFrame(close2, 'Try');

export const Punch: React.FC = () => (
  <AbsoluteFill>
    <ProductTitle at={titleAt} x={MARGIN} y={200} size={150} />
    <Appear at={titleAt + 18} style={{ left: MARGIN, top: 560, width: TEXT_WIDTH }}>
      <Tagline size={56} />
    </Appear>
    <Appear at={tryAt} style={{ left: MARGIN, top: 790, width: TEXT_WIDTH }}>
      <MonoLabel size={24} color={COLOR.signal}>
        Try it live
      </MonoLabel>
      <div style={{ fontFamily: FONT.mono, fontSize: 38, color: COLOR.bone, marginTop: 10 }}>{BRAND.demo}</div>
    </Appear>
    <Appear at={tryAt + 12} style={{ left: MARGIN, top: 960 }}>
      <MonoLabel size={18}>{BRAND.event}</MonoLabel>
    </Appear>
    <PresenterTrack lines={scene.lines} mode="full" box={PRESENTER_LOWER} until={scene.frames} enterAt={0} />
    <VoiceTrack scene={scene} />
    <SfxTrack cues={[{ at: titleAt, sfx: 'impactSoft_heavy_003', volume: 0.45 }]} />
  </AbsoluteFill>
);
