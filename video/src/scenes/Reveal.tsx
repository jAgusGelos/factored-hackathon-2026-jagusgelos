import React from 'react';
import { AbsoluteFill, interpolate, useCurrentFrame } from 'remotion';
import { PenPath } from '../components/Pen';
import { PRESENTER_RIGHT, PresenterTrack } from '../components/Presenter';
import { Appear, SfxTrack, VoiceTrack } from '../components/Scene';
import { Glow } from '../components/Stage';
import { LineKaraoke, MonoLabel } from '../components/Type';
import { lineOf, sceneOf, wordFrame } from '../timeline';
import { BRAND, CLAMP, COLOR, FONT, HEIGHT, WIDTH } from '../theme';

const scene = sceneOf('reveal');
const reveal1 = lineOf(scene, 'reveal1');
const reveal2 = lineOf(scene, 'reveal2');
const titleAt = wordFrame(reveal2, 'Dispute');
export const ProductTitle: React.FC<{ at: number; x: number; y: number; size?: number }> = ({ at, x, y, size = 150 }) => {
  const frame = useCurrentFrame();
  const sweep = interpolate(frame, [at, at + 36], [-30, 130], CLAMP);
  const rise = interpolate(frame, [at - 4, at + 12], [0, 1], CLAMP);
  return (
    <div style={{ position: 'absolute', left: x, top: y, opacity: rise, transform: `translateY(${(1 - rise) * 24}px)` }}>
      <MonoLabel size={24} color={COLOR.signal} style={{ marginBottom: 14 }}>
        {BRAND.bank}
      </MonoLabel>
      <Glow radius={24} strength={0.55}>
        <div
          style={{
            fontFamily: FONT.display,
            fontWeight: 900,
            fontStretch: '118%',
            fontSize: size,
            lineHeight: 0.95,
            letterSpacing: '-0.015em',
            backgroundImage: `linear-gradient(100deg, ${COLOR.bone} ${sweep - 12}%, #FFFFFF ${sweep}%, ${COLOR.signalHot} ${sweep + 4}%, ${COLOR.bone} ${sweep + 14}%)`,
            WebkitBackgroundClip: 'text',
            color: 'transparent',
          }}
        >
          {BRAND.product}
        </div>
      </Glow>
    </div>
  );
};

export const Tagline: React.FC<{ size: number }> = ({ size }) => (
  <div style={{ fontFamily: FONT.serif, fontStyle: 'italic', fontSize: size, color: COLOR.bone }}>{BRAND.tagline}</div>
);

export const Reveal: React.FC = () => (
  <AbsoluteFill>
    <LineKaraoke line={reveal1} until={reveal2.from - 4} at={{ x: 120, y: 330 }} size={96} width={1100} emphasis={['seconds', 'right']} />
    <LineKaraoke line={reveal2} until={titleAt - 2} at={{ x: 120, y: 380 }} size={84} width={1100} lead={6} />
    <ProductTitle at={titleAt} x={120} y={330} />
    <svg width={WIDTH} height={HEIGHT} style={{ position: 'absolute' }}>
      <PenPath d="M 120 650 L 1140 650" from={titleAt + 10} to={titleAt + 40} color={COLOR.signal} width={3} />
    </svg>
    <Appear at={titleAt + 30} style={{ left: 120, top: 690 }}>
      <Tagline size={54} />
    </Appear>
    <PresenterTrack lines={scene.lines} mode="full" box={PRESENTER_RIGHT} until={scene.frames} enterAt={0} />
    <VoiceTrack scene={scene} />
    <SfxTrack cues={[{ at: titleAt, sfx: 'impactSoft_heavy_003', volume: 0.45 }]} />
  </AbsoluteFill>
);
