import React from 'react';
import { interpolate, useCurrentFrame } from 'remotion';
import type { TimedLine, Word } from '../timeline';
import { COLOR, FONT, FPS } from '../theme';

const WIPE_S = 0.12;

type WordState = 'unsaid' | 'saying' | 'said';

/**
 * Timing for each display word. TTS cues and the presenter's alignment do not always split
 * words the same way ("thirty-seven", "It's"), so words are matched by character position.
 */
export function timingsForDisplay(line: TimedLine): Word[] {
  const display = line.text.split(' ');
  const timedText = line.words.map((w) => w.text);
  const totalTimed = timedText.join(' ').length || 1;
  const totalDisplay = line.text.length || 1;
  const timedOffsets = timedText.map((_, i) => timedText.slice(0, i).join(' ').length + (i ? 1 : 0));
  let offset = 0;
  return display.map((word) => {
    const position = (offset / totalDisplay) * totalTimed;
    offset += word.length + 1;
    let index = 0;
    while (index + 1 < timedOffsets.length && timedOffsets[index + 1] <= position + 0.5) index += 1;
    return line.words[index];
  });
}

function wordState(t: number, start: number, end: number): { state: WordState; wipe: number } {
  if (t < start) return { state: 'unsaid', wipe: 0 };
  if (t < end + WIPE_S) return { state: 'saying', wipe: Math.min(1, (t - start) / Math.max(WIPE_S, end - start)) };
  return { state: 'said', wipe: 1 };
}

/**
 * The line as karaoke: unsaid words are hairline outlines, the word being said wipes in the
 * signal colour with a glow, said words settle to bone. `emphasis` words stay signal once said.
 */
export const Karaoke: React.FC<{
  line: TimedLine;
  size?: number;
  width?: number;
  emphasis?: string[];
  align?: 'left' | 'center';
  appearFrames?: number;
}> = ({ line, size = 76, width = 1100, emphasis = [], align = 'left', appearFrames = 8 }) => {
  const frame = useCurrentFrame();
  const t = (frame - line.from) / FPS;
  const appear = interpolate(frame, [line.from - appearFrames, line.from], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
  const emphasized = new Set(emphasis.map((w) => w.toLowerCase()));
  const shown = line.text.split(' ');
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
        textAlign: align,
        opacity: appear,
        transform: `translateY(${(1 - appear) * 14}px)`,
      }}
    >
      {shown.map((display, i) => {
        const word = timings[i];
        const { state, wipe } = wordState(t, word.start, word.end);
        const isEmphasis = emphasized.has(display.replace(/[^\w']/g, '').toLowerCase());
        const settled = isEmphasis ? COLOR.signal : COLOR.bone;
        const color = state === 'said' ? settled : state === 'saying' ? COLOR.signalHot : 'transparent';
        return (
          <span
            key={`${line.id}-${i}`}
            style={{
              color,
              WebkitTextStroke: state === 'unsaid' ? `1.2px ${COLOR.boneDim}` : '0px transparent',
              textShadow: state === 'saying' || isEmphasis ? `0 0 ${18 * wipe}px ${COLOR.signal}` : 'none',
              marginRight: '0.26em',
              display: 'inline-block',
            }}
          >
            {display}
          </span>
        );
      })}
    </div>
  );
};

/** Spaced mono caps: the plates' UI voice. */
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

export type Honesty = 'MEASURED' | 'SIMULATED' | 'ASSUMED' | 'DESIGN ARGUMENT' | 'PLACEHOLDER';

const HONESTY_COLOR: Record<Honesty, string> = {
  MEASURED: COLOR.success,
  SIMULATED: COLOR.warning,
  ASSUMED: COLOR.warning,
  'DESIGN ARGUMENT': COLOR.signal,
  PLACEHOLDER: COLOR.critical,
};

/** A number's honesty label with its source, as in the deck and the README. */
export const HonestyChip: React.FC<{ kind: Honesty; children: React.ReactNode; size?: number }> = ({ kind, children, size = 17 }) => (
  <div style={{ display: 'flex', alignItems: 'center', gap: 12, fontFamily: FONT.mono, fontSize: size, color: COLOR.boneDim }}>
    <span
      style={{
        border: `1.5px solid ${HONESTY_COLOR[kind]}`,
        color: HONESTY_COLOR[kind],
        padding: '3px 10px',
        letterSpacing: '0.18em',
        fontWeight: 500,
      }}
    >
      {kind}
    </span>
    <span>{children}</span>
  </div>
);

/** English translation of what the Spanish/Portuguese UI shows, timed to the footage. */
export const Caption: React.FC<{ original?: string; english: string; style?: React.CSSProperties }> = ({ original, english, style }) => (
  <div
    style={{
      background: 'rgba(6, 14, 28, 0.88)',
      border: `1px solid ${COLOR.signal}`,
      boxShadow: `0 0 24px rgba(76, 141, 255, 0.35)`,
      padding: '14px 22px',
      maxWidth: 760,
      ...style,
    }}
  >
    {original ? (
      <div style={{ fontFamily: FONT.mono, fontSize: 18, color: COLOR.boneDim, marginBottom: 6 }}>{original}</div>
    ) : null}
    <div style={{ fontFamily: FONT.display, fontWeight: 600, fontSize: 30, color: COLOR.bone, lineHeight: 1.25 }}>{english}</div>
  </div>
);
