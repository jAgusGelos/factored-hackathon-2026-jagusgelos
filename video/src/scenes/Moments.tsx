import React from 'react';
import { AbsoluteFill, Img, interpolate, staticFile, useCurrentFrame } from 'remotion';
import { asFootage, clipEventFrame } from '../components/Device';
import { BubblePresenter, Chapter, ClipOnLaptop, LEFT_COLUMN, MomentScene, RealFootageNote, tapCues, type CaptionedClip } from '../components/Moment';
import { Appear, SfxTrack, VoiceTrack } from '../components/Scene';
import { Glow } from '../components/Stage';
import { FactChip, MonoLabel } from '../components/Type';
import { DUPLICATE_OUTCOME, FACTS } from '../facts';
import m1Meta from '../meta/footage/m1.json';
import m2Meta from '../meta/footage/m2.json';
import m3Meta from '../meta/footage/m3.json';
import ptMeta from '../meta/footage/pt.json';
import { lineOf, sceneOf, wordFrame } from '../timeline';
import { COLOR, FONT, alpha } from '../theme';

const m1Scene = sceneOf('m1');
const m1Clip: CaptionedClip = {
  meta: asFootage(m1Meta),
  from: 15,
  rate: 1,
  startAt: 0,
  until: m1Scene.frames - 6,
  captions: [
    { from: 'report', original: 'No reconozco un cargo de 38.500 pesos del 14 de junio', english: 'I don’t recognize a 38,500 peso charge from June 14.' },
    { from: 'confirm_question', english: 'Uber, COP 38,500, June 14, 2026. Is this the charge you don’t recognize?' },
    { from: 'explanation', original: 'No uso Uber hace meses, tengo la tarjeta conmigo', english: 'I haven’t used Uber in months. I have my card with me.' },
    { from: 'resolved', english: 'Done: a provisional credit, the card blocked for safety, and a reference number.' },
  ],
};

export const MomentResolve: React.FC = () => (
  <MomentScene
    scene={m1Scene}
    chapter={{ number: '01', title: 'Resolves in seconds, with proof', subtitle: 'Typed report · verified charge' }}
    clip={m1Clip}
    resultEvent="resolved"
    result={
      <>
        <Glow radius={12} strength={0.5}>
          <div style={{ fontFamily: FONT.display, fontWeight: 900, fontSize: 64, color: COLOR.success }}>{FACTS.resolvingTurn.text}</div>
        </Glow>
        <MonoLabel style={{ margin: '6px 0 14px' }}>for the resolving turn</MonoLabel>
        <FactChip fact={FACTS.resolvingTurn} size={14} />
      </>
    }
  />
);

const m2Scene = sceneOf('m2');
const m2Clip: CaptionedClip = {
  meta: asFootage(m2Meta),
  from: 12,
  rate: 1.2,
  startAt: 0,
  until: m2Scene.frames - 6,
  captions: [
    { from: 'report', original: 'Me cobraron dos veces un taxi de 27 mil', english: 'I was charged twice for a 27k taxi.' },
    { from: 'two_charges', english: 'I see 2 charges that match. Tap the one you don’t recognize.' },
    { from: 'explanation', original: 'Tomé un solo taxi y me lo cobraron dos veces', english: 'I took one taxi and was charged twice.' },
    { from: 'resolved', english: 'Done: the charge was a duplicate, and one of the two was refunded.' },
  ],
};

export const MomentAsk: React.FC = () => (
  <MomentScene
    scene={m2Scene}
    chapter={{ number: '02', title: 'Asks when it is ambiguous', subtitle: 'Two matches · the customer picks' }}
    clip={m2Clip}
    resultEvent="resolved"
    result={
      <>
        <Glow radius={14} strength={0.6}>
          <div style={{ fontFamily: FONT.display, fontWeight: 900, fontStretch: '115%', fontSize: 96, lineHeight: 1, color: COLOR.signal }}>
            {DUPLICATE_OUTCOME.text}
          </div>
        </Glow>
        <MonoLabel style={{ marginTop: 10 }}>{DUPLICATE_OUTCOME.note}</MonoLabel>
      </>
    }
  />
);

const m3Scene = sceneOf('m3');
const m3a = lineOf(m3Scene, 'm3a');
const m3b = lineOf(m3Scene, 'm3b');
const m3c = lineOf(m3Scene, 'm3c');
const handoffAt = wordFrame(m3b, 'file');
const verifiedAt = wordFrame(m3b, 'verified');
const storyAt = wordFrame(m3b, 'story');
const openAt = wordFrame(m3b, 'open');

const m3Clip: CaptionedClip = {
  meta: asFootage(m3Meta),
  from: 12,
  rate: 1.7,
  startAt: 3.5,
  until: handoffAt + 8,
  captions: [
    { from: 'statement_question', english: 'Before handing your case over, tell me what happened. The person who reviews it will use this.' },
    { from: 'statement_1', english: 'I never bought there. I have my card. I saw it yesterday in an app alert.' },
    { from: 'statement_reply_1', english: 'A few short questions for the reviewer: other charges you don’t recognize?' },
    { from: 'statement_2', english: 'No, nothing else.' },
    { from: 'escalated', english: `Handed to a person. Case number, contact ${FACTS.contactDeadline.text}.` },
  ],
};
const escalatedAt = clipEventFrame(m3Clip, 'escalated');

const ptClip: CaptionedClip = {
  meta: asFootage(ptMeta),
  from: m3c.from - 6,
  rate: 1.2,
  startAt: 0.4,
  until: m3Scene.frames - 6,
  captions: [
    { from: 'report', original: 'Não reconheço uma compra na Tienda Online Global', english: 'The same report, in Portuguese.' },
    { from: 'statement_question', english: 'Same path: before forwarding the case, it asks what happened.' },
  ],
};

/** Rows (px in the 2x still) where each part of the advisor's case file starts. */
const HANDOFF_SCROLL = { top: 0, verified: 380, story: 1080, open: 2338 };
const HANDOFF_IMG_W = 542;
const HANDOFF_SCALE = 0.96;

const HANDOFF_SECTIONS = [
  { at: verifiedAt, title: 'Verified facts', note: 'from the charge record', color: COLOR.success },
  { at: storyAt, title: 'The customer’s story', note: 'unverified, model summary labelled', color: COLOR.warning },
  { at: openAt, title: 'What is still open', note: 'the decisions a person makes', color: COLOR.signal },
];

const HandoffFile: React.FC = () => {
  const frame = useCurrentFrame();
  const scroll = interpolate(
    frame,
    [handoffAt, verifiedAt, storyAt, openAt],
    [HANDOFF_SCROLL.top, HANDOFF_SCROLL.verified, HANDOFF_SCROLL.story, HANDOFF_SCROLL.open],
    { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' },
  );
  const visible = interpolate(frame, [handoffAt + 8, handoffAt + 18, m3c.from - 20, m3c.from - 8], [0, 1, 1, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  if (visible <= 0) return null;
  return (
    <AbsoluteFill style={{ opacity: visible }}>
      <MonoLabel size={20} color={COLOR.bone} style={{ position: 'absolute', left: 620, top: 150 }}>
        The advisor’s case file
      </MonoLabel>
      {HANDOFF_SECTIONS.map((section, i) => (
        <Appear key={section.title} at={section.at} style={{ left: 620, top: 260 + i * 190, width: 520 }}>
          <div style={{ borderLeft: `4px solid ${section.color}`, paddingLeft: 20 }}>
            <div style={{ fontFamily: FONT.display, fontWeight: 800, fontSize: 46, color: COLOR.bone }}>{section.title}</div>
            <MonoLabel size={16} color={section.color}>
              {section.note}
            </MonoLabel>
          </div>
        </Appear>
      ))}
      <div
        style={{
          position: 'absolute',
          left: 1220,
          top: 120,
          width: HANDOFF_IMG_W * HANDOFF_SCALE,
          height: 860,
          overflow: 'hidden',
          borderRadius: 14,
          border: `1px solid ${alpha(COLOR.grid, 0.3)}`,
          boxShadow: `0 30px 90px rgba(0,0,0,0.55), 0 0 40px ${alpha(COLOR.signal, 0.2)}`,
          background: COLOR.bone,
        }}
      >
        <Img src={staticFile('footage/m3-handoff.png')} style={{ width: HANDOFF_IMG_W * HANDOFF_SCALE, transform: `translateY(${-scroll * HANDOFF_SCALE}px)` }} />
      </div>
      <MonoLabel size={15} style={{ position: 'absolute', left: 1220, top: 1000 }}>
        Real handoff from the same case · Spanish UI
      </MonoLabel>
    </AbsoluteFill>
  );
};

export const MomentHandOff: React.FC = () => {
  const frame = useCurrentFrame();
  const inPortuguese = frame >= m3c.from - 12;
  const chapterOut = interpolate(frame, [handoffAt - 4, handoffAt + 6], [1, 0], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
  const chapterBack = interpolate(frame, [m3c.from - 12, m3c.from], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
  const deadlineChip = interpolate(frame, [escalatedAt, escalatedAt + 10, handoffAt - 4, handoffAt + 4], [0, 1, 1, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  return (
    <AbsoluteFill>
      <AbsoluteFill style={{ opacity: Math.max(chapterOut, chapterBack) }}>
        <Chapter number="03" title="Knows when not to act" subtitle={inPortuguese ? 'Same path · Portuguese' : 'Policy says no · a person decides'} />
      </AbsoluteFill>
      <div style={{ position: 'absolute', left: LEFT_COLUMN.x, top: LEFT_COLUMN.resultY, width: LEFT_COLUMN.width, opacity: deadlineChip }}>
        <FactChip fact={FACTS.contactDeadline} size={14} />
      </div>
      <ClipOnLaptop clip={m3Clip} />
      <HandoffFile />
      <ClipOnLaptop clip={ptClip} />
      {frame < handoffAt || inPortuguese ? <RealFootageNote /> : null}
      <BubblePresenter lines={[m3a]} until={m3b.from} />
      <VoiceTrack scene={m3Scene} />
      <SfxTrack cues={[...tapCues(ptClip), { at: handoffAt, sfx: 'card-slide-1', volume: 0.3 }]} />
    </AbsoluteFill>
  );
};
