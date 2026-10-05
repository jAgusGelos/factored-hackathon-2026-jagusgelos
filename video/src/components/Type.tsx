import React from 'react';
import { interpolate, useCurrentFrame } from 'remotion';
import type { Fact, Honesty } from '../facts';
import type { TimedLine, Word } from '../timeline';
import { COLOR, FONT, FPS, alpha } from '../theme';
import { WhileLine } from './Scene';

const WIPE_S = 0.12;

type WordState = 'unsaid' | 'saying' | 'said';

const normalize = (word: string): string => word.replace(/[^\w'-]/g, '').toLowerCase();

/**
 * Timing for each display word. TTS cues and the presenter's alignment do not always split
 * words the same way ("thirty-seven", "It's"), so words are matched by character position.
 */
function timingsForDisplay(line: TimedLine): Word[] {
  const timedText = line.words.map((w) => w.text);
  const totalTimed = timedText.join(' ').length || 1;
  const totalDisplay = line.text.length || 1;
  const timedOffsets = timedText.map((_, i) => timedText.slice(0, i).join(' ').length + (i ? 1 : 0));
  let offset = 0;
  return line.text.split(' ').map((word) => {
    const position = (offset / totalDisplay) * totalTimed;
    offset += word.length + 1;
    let index = 0;
    while (index + 1 < timedOffsets.length && timedOffsets[index + 1] <= position + 0.5) index += 1;
    return line.words[index];
  });
}

function wordState(t: number, word: Word): { state: WordState; wipe: number } {
  if (t < word.start) return { state: 'unsaid', wipe: 0 };
  if (t < word.end + WIPE_S) return { state: 'saying', wipe: Math.min(1, (t - word.start) / Math.max(WIPE_S, word.end - word.start)) };
  return { state: 'said', wipe: 1 };
}

const KaraokeWord: React.FC<{ display: string; t: number; word: Word; emphasis: boolean }> = ({ display, t, word, emphasis }) => {
  const { state, wipe } = wordState(t, word);
  const settled = emphasis ? COLOR.signal : COLOR.bone;
  const color = state === 'said' ? settled : state === 'saying' ? COLOR.signalHot : 'transparent';
  return (
    <span
      style={{
        color,
        WebkitTextStroke: state === 'unsaid' ? `1.2px ${COLOR.boneDim}` : '0px transparent',
        textShadow: state === 'saying' || (emphasis && state === 'said') ? `0 0 ${18 * wipe}px ${COLOR.signal}` : 'none',
        marginRight: '0.26em',
        display: 'inline-block',
      }}
    >
      {display}
    </span>
  );
};

/**
 * The line as karaoke: unsaid words are hairline outlines, the word being said wipes in the
 * signal colour with a glow, said words settle to bone. `emphasis` words stay signal once said.
 */
export const Karaoke: React.FC<{ line: TimedLine; size: number; width: number; emphasis?: string[] }> = ({ line, size, width, emphasis = [] }) => {
  const frame = useCurrentFrame();
  const t = (frame - line.from) / FPS;
  const appear = interpolate(frame, [line.from - 8, line.from], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
  const emphasized = new Set(emphasis.map(normalize));
  const timings = timingsForDisplay(line);
  return (
    <div
      style={{
        width,
        fontFamily: FONT.display,
        fontWeight: 800,
        fontStretch: '112%',
        fontSize: size,
        lineHeight: 1.08,
        letterSpacing: '-0.01em',
        opacity: appear,
        transform: `translateY(${(1 - appear) * 14}px)`,
      }}
    >
      {line.text.split(' ').map((display, i) => (
        <KaraokeWord key={`${line.id}-${i}`} display={display} t={t} word={timings[i]} emphasis={emphasized.has(normalize(display))} />
      ))}
    </div>
  );
};

/** A narration line as karaoke at a fixed spot, shown while it is the current line. */
export const LineKaraoke: React.FC<{
  line: TimedLine;
  until: number;
  at: { x: number; y: number };
  size: number;
  width: number;
  emphasis?: string[];
  lead?: number;
}> = ({ line, until, at, size, width, emphasis, lead }) => (
  <WhileLine line={line} until={until} lead={lead}>
    <div style={{ position: 'absolute', left: at.x, top: at.y }}>
      <Karaoke line={line} size={size} width={width} emphasis={emphasis} />
    </div>
  </WhileLine>
);

export const MonoLabel: React.FC<{ children: React.ReactNode; size?: number; color?: string; style?: React.CSSProperties }> = ({
  children,
  size = 18,
  color = COLOR.boneDim,
  style,
}) => (
  <div
    style={{
      fontFamily: FONT.mono,
      fontWeight: 500,
      fontSize: size,
      letterSpacing: '0.22em',
      textTransform: 'uppercase',
      color,
      ...style,
    }}
  >
    {children}
  </div>
);

const HONESTY_COLOR: Record<Honesty, string> = {
  MEASURED: COLOR.success,
  SIMULATED: COLOR.warning,
  ASSUMED: COLOR.warning,
  'DESIGN ARGUMENT': COLOR.signal,
  PLACEHOLDER: COLOR.critical,
};

const Chip: React.FC<{ kind: Honesty; text: string; size: number }> = ({ kind, text, size }) => (
  <div style={{ display: 'flex', alignItems: 'center', gap: 12, fontFamily: FONT.mono, fontSize: size, color: COLOR.boneDim }}>
    <span style={{ border: `1.5px solid ${HONESTY_COLOR[kind]}`, color: HONESTY_COLOR[kind], padding: '3px 10px', letterSpacing: '0.18em', fontWeight: 500, whiteSpace: 'nowrap' }}>
      {kind}
    </span>
    <span>{text}</span>
  </div>
);

export const FactChip: React.FC<{ fact: Fact; long?: boolean; size?: number }> = ({ fact, long = false, size = 16 }) => (
  <Chip kind={fact.label} text={long ? fact.source : fact.short} size={size} />
);

/** English translation of what the Spanish/Portuguese UI shows, timed to the footage. */
export const Caption: React.FC<{ original?: string; english: string; style?: React.CSSProperties }> = ({ original, english, style }) => (
  <div
    style={{
      background: alpha('#060E1C', 0.88),
      border: `1px solid ${COLOR.signal}`,
      boxShadow: `0 0 24px ${alpha(COLOR.signal, 0.35)}`,
      padding: '14px 22px',
      maxWidth: 760,
      ...style,
    }}
  >
    {original ? <div style={{ fontFamily: FONT.mono, fontSize: 18, color: COLOR.boneDim, marginBottom: 6 }}>{original}</div> : null}
    <div style={{ fontFamily: FONT.display, fontWeight: 600, fontSize: 30, color: COLOR.bone, lineHeight: 1.25 }}>{english}</div>
  </div>
);
