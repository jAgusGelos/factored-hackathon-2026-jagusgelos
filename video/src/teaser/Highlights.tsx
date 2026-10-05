import React from 'react';
import { AbsoluteFill, interpolate, useCurrentFrame } from 'remotion';
import { asFootage, type Clip } from '../components/Device';
import { PresenterTrack } from '../components/Presenter';
import { SfxTrack, VoiceTrack } from '../components/Scene';
import { Caption, LineKaraoke, MonoLabel } from '../components/Type';
import { DUPLICATE_OUTCOME } from '../facts';
import m1Meta from '../meta/footage/m1.json';
import m2Meta from '../meta/footage/m2.json';
import m3Meta from '../meta/footage/m3.json';
import { lineOf, wordFrame } from '../timeline';
import { CLAMP, FPS } from '../theme';
import { MARGIN, PRESENTER_BUBBLE_LOWER, TEXT_WIDTH } from './layout';
import { BEZEL, Screen, type Crop } from './Screen';
import { linePart, teaserScene } from './timeline';

const scene = teaserScene('highlights');
const close1 = lineOf(scene, 'close1');
const PERSON_WORD = close1.text.split(' ').indexOf('A');
const resolvePhrase = linePart(close1, 0, PERSON_WORD);
const personPhrase = linePart(close1, PERSON_WORD);

const CUT_LEAD = 4;
const CAPTION_RISE = 5;
const m2At = wordFrame(close1, 'the') - CUT_LEAD;
const m3At = wordFrame(close1, 'A') - CUT_LEAD;

// Every clip plays at the same speed and starts just before its outcome reply lands.
const RATE = 1.5;
const OUTCOME_IN_FRAMES = 10;

interface Highlight {
  clip: Clip;
  caption: string;
}

function highlight(json: unknown, outcomeEvent: string, from: number, until: number, caption: string): Highlight {
  const meta = asFootage(json);
  const outcome = meta.events.find((e) => e.name === outcomeEvent);
  if (!outcome) throw new Error(`no event ${outcomeEvent} in ${meta.clip}`);
  const startAt = outcome.t - (RATE * OUTCOME_IN_FRAMES) / FPS;
  return { clip: { meta, from, rate: RATE, startAt, until }, caption };
}

const HIGHLIGHTS: Highlight[] = [
  highlight(m1Meta, 'resolved', 0, m2At, 'Provisional credit, card blocked.'),
  highlight(m2Meta, 'resolved', m2At, m3At, `Duplicate: ${DUPLICATE_OUTCOME.text} reversed.`),
  highlight(m3Meta, 'escalated', m3At, scene.frames, 'Card blocked, handed to a person.'),
];

/** The chat column of the app, without its input bar. */
const CHAT_CROP: Crop = { x: 90, y: 150, w: 1230, h: 890 };
const SCREEN = { x: 24, y: 470, width: 1000 } as const;
const BELOW_SCREEN = SCREEN.y + BEZEL * 2 + (CHAT_CROP.h * SCREEN.width) / CHAT_CROP.w;

const ActiveCaption: React.FC = () => {
  const frame = useCurrentFrame();
  const active = HIGHLIGHTS.find((h) => frame >= h.clip.from && frame < h.clip.until);
  if (!active) return null;
  const p = interpolate(frame, [active.clip.from, active.clip.from + CAPTION_RISE], [0, 1], CLAMP);
  return (
    <div style={{ position: 'absolute', left: MARGIN - 40, top: BELOW_SCREEN + 70, opacity: p, transform: `translateY(${(1 - p) * 14}px)` }}>
      <Caption english={active.caption} size={44} style={{ maxWidth: 640 }} />
    </div>
  );
};

export const Highlights: React.FC = () => (
  <AbsoluteFill>
    <LineKaraoke line={resolvePhrase} until={m3At - 2} at={{ x: MARGIN, y: 180 }} size={64} width={TEXT_WIDTH} emphasis={['seconds']} />
    <LineKaraoke line={personPhrase} until={scene.frames} at={{ x: MARGIN, y: 180 }} size={64} width={TEXT_WIDTH} emphasis={['person']} lead={4} />
    <div style={{ position: 'absolute', left: SCREEN.x, top: SCREEN.y }}>
      <Screen clips={HIGHLIGHTS.map((h) => h.clip)} crop={CHAT_CROP} width={SCREEN.width} />
    </div>
    <MonoLabel size={17} style={{ position: 'absolute', left: SCREEN.x + 16, top: BELOW_SCREEN + 18 }}>
      Real app · real model · wait shortened, clips sped up
    </MonoLabel>
    <ActiveCaption />
    <PresenterTrack lines={scene.lines} mode="bubble" box={PRESENTER_BUBBLE_LOWER} until={scene.frames} enterAt={0} />
    <VoiceTrack scene={scene} />
    <SfxTrack cues={HIGHLIGHTS.slice(1).map((h) => ({ at: h.clip.from, sfx: 'switch_007', volume: 0.25 }))} />
  </AbsoluteFill>
);
