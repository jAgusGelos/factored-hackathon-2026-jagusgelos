import React from 'react';
import { AbsoluteFill } from 'remotion';
import { PenPath } from '../components/Pen';
import { PRESENTER_RIGHT, PresenterTrack } from '../components/Presenter';
import { Appear, SfxTrack, VoiceTrack, WhileLine } from '../components/Scene';
import { Glow } from '../components/Stage';
import { Karaoke, MonoLabel } from '../components/Type';
import { FACTS } from '../facts';
import { lineOf, sceneOf, wordFrame } from '../timeline';
import { BRAND, COLOR, FONT, HEIGHT, WIDTH } from '../theme';
import { ProductTitle, Tagline } from './Reveal';

const scene = sceneOf('close');
const close1 = lineOf(scene, 'close1');
const close2 = lineOf(scene, 'close2');
const secondsAt = wordFrame(close1, 'seconds');
const personAt = wordFrame(close1, 'person');
const liveAt = wordFrame(close2, 'live');
const endCardAt = close2.from - 4;

const BigWord: React.FC<{ children: React.ReactNode; color: string }> = ({ children, color }) => (
  <Glow radius={18} strength={0.6}>
    <div style={{ fontFamily: FONT.display, fontWeight: 900, fontStretch: '115%', fontSize: 128, lineHeight: 1, color }}>{children}</div>
  </Glow>
);

const PersonMark: React.FC = () => (
  <svg width={110} height={110} viewBox="0 0 110 110">
    <circle cx={55} cy={36} r={22} fill="none" stroke={COLOR.success} strokeWidth={4} />
    <path d="M 14 106 C 14 74 32 62 55 62 C 78 62 96 74 96 106" fill="none" stroke={COLOR.success} strokeWidth={4} />
  </svg>
);

const PromiseBeat: React.FC = () => (
  <WhileLine line={close1} until={endCardAt}>
    <div style={{ position: 'absolute', left: 120, top: 120 }}>
      <Karaoke line={close1} size={60} width={1060} emphasis={['seconds', 'person']} />
    </div>
    <Appear at={close1.from} style={{ left: 120, top: 420 }}>
      <BigWord color={COLOR.boneDim}>{`${FACTS.firstResponse.hours} h`}</BigWord>
    </Appear>
    <svg width={WIDTH} height={HEIGHT} style={{ position: 'absolute' }}>
      <PenPath d="M 110 490 L 430 465" from={secondsAt - 8} to={secondsAt + 4} color={COLOR.critical} width={6} />
      <PenPath d="M 470 485 L 540 485" from={secondsAt} to={secondsAt + 8} color={COLOR.boneDim} spark={false} />
    </svg>
    <Appear at={secondsAt} style={{ left: 570, top: 420 }}>
      <BigWord color={COLOR.signal}>seconds</BigWord>
    </Appear>
    <Appear at={personAt} style={{ left: 120, top: 640 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 28 }}>
        <PersonMark />
        <div>
          <div style={{ fontFamily: FONT.display, fontWeight: 800, fontSize: 64, color: COLOR.bone }}>a person</div>
          <MonoLabel>with the full case file</MonoLabel>
        </div>
      </div>
    </Appear>
  </WhileLine>
);

const EndCard: React.FC = () => (
  <>
    <ProductTitle at={endCardAt + 6} x={120} y={250} size={140} />
    <Appear at={endCardAt + 20} style={{ left: 120, top: 520 }}>
      <Tagline size={50} />
    </Appear>
    <Appear at={liveAt} style={{ left: 120, top: 650 }}>
      <MonoLabel size={20} color={COLOR.signal}>
        Try it live
      </MonoLabel>
      <div style={{ fontFamily: FONT.mono, fontSize: 34, color: COLOR.bone, marginTop: 8 }}>{BRAND.demo}</div>
      <MonoLabel size={20} color={COLOR.signal} style={{ marginTop: 30 }}>
        Code, data and evidence
      </MonoLabel>
      <div style={{ fontFamily: FONT.mono, fontSize: 30, color: COLOR.bone, marginTop: 8 }}>{BRAND.repo}</div>
    </Appear>
    <Appear at={endCardAt + 30} style={{ left: 120, top: 990 }}>
      <MonoLabel size={16}>{BRAND.event}</MonoLabel>
    </Appear>
  </>
);

export const Close: React.FC = () => (
  <AbsoluteFill>
    <PromiseBeat />
    <EndCard />
    <PresenterTrack lines={scene.lines} mode="full" box={PRESENTER_RIGHT} until={scene.frames - 20} />
    <VoiceTrack scene={scene} />
    <SfxTrack
      cues={[
        { at: secondsAt, sfx: 'impactSoft_medium_001', volume: 0.3 },
        { at: endCardAt + 6, sfx: 'impactSoft_heavy_003', volume: 0.45 },
      ]}
    />
  </AbsoluteFill>
);
