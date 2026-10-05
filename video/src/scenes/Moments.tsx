import React from 'react';
import { AbsoluteFill, Img, interpolate, staticFile, useCurrentFrame } from 'remotion';
import type { FootageMeta } from '../components/Device';
import { eventFrame } from '../components/Device';
import { BubblePresenter, Chapter, ClipOnLaptop, RealFootageNote, type Clip } from '../components/Moment';
import { Appear, SfxTrack, VoiceTrack } from '../components/Scene';
import { Glow } from '../components/Stage';
import { HonestyChip, MonoLabel } from '../components/Type';
import { FACTS } from '../facts';
import m1Meta from '../meta/footage/m1.json';
import m2Meta from '../meta/footage/m2.json';
import m3Meta from '../meta/footage/m3.json';
import ptMeta from '../meta/footage/pt.json';
import { lineOf, sceneOf, wordFrame } from '../timeline';
import { COLOR, FONT } from '../theme';

const tapCues = (clip: Clip) =>
  clip.meta.events
    .filter((e) => e.kind === 'tap')
    .map((e) => ({ at: eventFrame(clip.meta, e.name, clip.from, clip.rate, clip.startAt), sfx: 'click_003', volume: 0.5 }));

const m1Scene = sceneOf('m1');
const m1Clip: Clip = {
  meta: m1Meta as FootageMeta,
  from: 15,
  rate: 1,
  until: m1Scene.frames - 6,
  captions: [
    { from: 'report', original: 'No reconozco un cargo de 38.500 pesos del 14 de junio', english: 'I don’t recognize a 38,500 peso charge from June 14.' },
    { from: 'confirm_question', english: 'Uber, COP 38,500, June 14, 2026. Is this the charge you don’t recognize?' },
    { from: 'explanation', original: 'No uso Uber hace meses, tengo la tarjeta conmigo', english: 'I haven’t used Uber in months. I have my card with me.' },
    { from: 'resolved', english: 'Done: a provisional credit, the card blocked for safety, and a reference number.' },
  ],
};

export const MomentResolve: React.FC = () => {
  const resolvedAt = eventFrame(m1Clip.meta, 'resolved', m1Clip.from, m1Clip.rate);
  return (
    <AbsoluteFill>
      <Chapter number="01" title="Resolves in seconds, with proof" subtitle="Typed report · verified charge" />
      <ClipOnLaptop clip={m1Clip} />
      <Appear at={resolvedAt + 6} style={{ left: 110, top: 530, width: 440 }}>
        <Glow radius={12} strength={0.5}>
          <div style={{ fontFamily: FONT.display, fontWeight: 900, fontSize: 64, color: COLOR.success }}>{FACTS.resolvingTurn.text}</div>
        </Glow>
        <MonoLabel style={{ margin: '6px 0 14px' }}>for the resolving turn</MonoLabel>
        <HonestyChip kind={FACTS.resolvingTurn.label} size={14}>
          Claude Haiku 4.5, manual runs
        </HonestyChip>
      </Appear>
      <RealFootageNote />
      <BubblePresenter lines={m1Scene.lines} until={m1Scene.frames - 4} />
      <VoiceTrack scene={m1Scene} />
      <SfxTrack cues={[...tapCues(m1Clip), { at: resolvedAt, sfx: 'impactBell_heavy_000', volume: 0.18 }]} />
    </AbsoluteFill>
  );
};

const m2Scene = sceneOf('m2');
const m2Clip: Clip = {
  meta: m2Meta as FootageMeta,
  from: 12,
  rate: 1.2,
  until: m2Scene.frames - 6,
  captions: [
    { from: 'report', original: 'Me cobraron dos veces un taxi de 27 mil', english: 'I was charged twice for a 27k taxi.' },
    { from: 'two_charges', english: 'I see 2 charges that match. Tap the one you don’t recognize.' },
    { from: 'explanation', original: 'Tomé un solo taxi y me lo cobraron dos veces', english: 'I took one taxi and was charged twice.' },
    { from: 'resolved', english: 'Done: the charge was a duplicate, and one of the two was refunded.' },
  ],
};

export const MomentAsk: React.FC = () => {
  const resolvedAt = eventFrame(m2Clip.meta, 'resolved', m2Clip.from, m2Clip.rate);
  return (
    <AbsoluteFill>
      <Chapter number="02" title="Asks when it is ambiguous" subtitle="Two matches · the customer picks" />
      <ClipOnLaptop clip={m2Clip} />
      <Appear at={resolvedAt + 4} style={{ left: 110, top: 530, width: 440 }}>
        <Glow radius={14} strength={0.6}>
          <div style={{ fontFamily: FONT.display, fontWeight: 900, fontStretch: '115%', fontSize: 96, lineHeight: 1, color: COLOR.signal }}>1 of 2</div>
        </Glow>
        <MonoLabel style={{ marginTop: 10 }}>reversed · twin verified</MonoLabel>
      </Appear>
      <RealFootageNote />
      <BubblePresenter lines={m2Scene.lines} until={m2Scene.frames - 4} />
      <VoiceTrack scene={m2Scene} />
      <SfxTrack cues={[...tapCues(m2Clip), { at: resolvedAt, sfx: 'impactBell_heavy_000', volume: 0.18 }]} />
    </AbsoluteFill>
  );
};

const m3Scene = sceneOf('m3');
const m3a = lineOf(m3Scene, 'm3a');
const m3b = lineOf(m3Scene, 'm3b');
const m3c = lineOf(m3Scene, 'm3c');
const handoffAt = wordFrame(m3b, 'person');

const m3Clip: Clip = {
  meta: m3Meta as FootageMeta,
  from: 12,
  rate: 1.3,
  until: handoffAt + 8,
  captions: [
    { from: 'report', original: 'No reconozco una compra en Tienda Online Global', english: 'I don’t recognize a purchase at Tienda Online Global.' },
    { from: 'statement_question', english: 'Before handing your case over, tell me what happened. The person who reviews it will use this.' },
    { from: 'statement_1', english: 'I never bought there. I have my card. I saw it yesterday in an app alert.' },
    { from: 'statement_reply_1', english: 'A few short questions for the reviewer: other charges you don’t recognize?' },
    { from: 'statement_2', english: 'No, nothing else.' },
    { from: 'escalated', english: `Handed to a person. Case number, contact ${FACTS.contactDeadline.text}.` },
  ],
};

const ptClip: Clip = {
  meta: ptMeta as FootageMeta,
  from: m3c.from - 12,
  rate: 1.2,
  startAt: 0.4,
  until: m3Scene.frames - 6,
  captions: [
    { from: 'report', original: 'Não reconheço uma compra na Tienda Online Global', english: 'The same report, in Portuguese.' },
    { from: 'statement_question', english: 'Same path: before forwarding the case, it asks what happened.' },
  ],
};

/** Image rows (px in the 2x still) where each part of the advisor's case file starts. */
const HANDOFF_SCROLL = { top: 0, verified: 380, story: 1080, open: 2338 };
const HANDOFF_SCALE = 0.96;

const HandoffFile: React.FC = () => {
  const frame = useCurrentFrame();
  const verifiedAt = wordFrame(m3b, 'verified');
  const storyAt = wordFrame(m3b, 'story');
  const openAt = wordFrame(m3b, 'open');
  const scroll = interpolate(
    frame,
    [handoffAt, verifiedAt, storyAt, openAt],
    [HANDOFF_SCROLL.top, HANDOFF_SCROLL.verified, HANDOFF_SCROLL.story, HANDOFF_SCROLL.open],
    { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' },
  );
  const visible = interpolate(frame, [handoffAt, handoffAt + 12, m3c.from - 16, m3c.from - 6], [0, 1, 1, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  if (visible <= 0) return null;
  const labels = [
    { at: verifiedAt, title: 'Verified facts', note: 'from the charge record', color: COLOR.success },
    { at: storyAt, title: 'The customer’s story', note: 'unverified, model summary labelled', color: COLOR.warning },
    { at: openAt, title: 'What is still open', note: 'the decisions a person makes', color: COLOR.signal },
  ];
  return (
    <AbsoluteFill style={{ opacity: visible }}>
      <MonoLabel size={20} color={COLOR.bone} style={{ position: 'absolute', left: 620, top: 150 }}>
        The advisor’s case file
      </MonoLabel>
      {labels.map((label, i) => (
        <Appear key={label.title} at={label.at} style={{ left: 620, top: 260 + i * 190, width: 520 }}>
          <div style={{ borderLeft: `4px solid ${label.color}`, paddingLeft: 20 }}>
            <div style={{ fontFamily: FONT.display, fontWeight: 800, fontSize: 46, color: COLOR.bone }}>{label.title}</div>
            <MonoLabel size={16} color={label.color}>
              {label.note}
            </MonoLabel>
          </div>
        </Appear>
      ))}
      <div
        style={{
          position: 'absolute',
          left: 1220,
          top: 120,
          width: 542 * HANDOFF_SCALE,
          height: 860,
          overflow: 'hidden',
          borderRadius: 14,
          border: `1px solid rgba(232,238,246,0.3)`,
          boxShadow: '0 30px 90px rgba(0,0,0,0.55), 0 0 40px rgba(76,141,255,0.2)',
          background: '#fff',
        }}
      >
        <Img
          src={staticFile('footage/m3-handoff.png')}
          style={{ width: 542 * HANDOFF_SCALE, transform: `translateY(${-scroll * HANDOFF_SCALE}px)` }}
        />
      </div>
      <MonoLabel size={15} style={{ position: 'absolute', left: 1220, top: 1000 }}>
        Real handoff from the same case · Spanish UI
      </MonoLabel>
    </AbsoluteFill>
  );
};

export const MomentHandOff: React.FC = () => {
  const frame = useCurrentFrame();
  const chapterOut = interpolate(frame, [handoffAt - 4, handoffAt + 6], [1, 0], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
  const chapterBack = interpolate(frame, [m3c.from - 12, m3c.from], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
  return (
    <AbsoluteFill>
      <AbsoluteFill style={{ opacity: Math.max(chapterOut, chapterBack) }}>
        <Chapter number="03" title="Knows when not to act" subtitle={frame >= m3c.from - 12 ? 'Same path · Portuguese' : 'Policy says no · a person decides'} />
      </AbsoluteFill>
      <ClipOnLaptop clip={m3Clip} />
      <HandoffFile />
      <ClipOnLaptop clip={ptClip} />
      {frame < handoffAt || frame >= m3c.from - 12 ? <RealFootageNote /> : null}
      <BubblePresenter lines={[m3a]} until={m3b.from} />
      <VoiceTrack scene={m3Scene} />
      <SfxTrack cues={[...tapCues(ptClip), { at: handoffAt, sfx: 'card-slide-1', volume: 0.3 }]} />
    </AbsoluteFill>
  );
};
