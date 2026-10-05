import React from 'react';
import { AbsoluteFill, Freeze, OffthreadVideo, Sequence, interpolate, staticFile, useCurrentFrame } from 'remotion';
import type { TimedLine } from '../timeline';
import { COLOR, FONT, alpha } from '../theme';

type PresenterMode = 'full' | 'bubble';

interface Box {
  x: number;
  y: number;
  w: number;
  h: number;
}

export const PRESENTER_RIGHT: Box = { x: 1260, y: 150, w: 600, h: 930 };
export const PRESENTER_LEFT: Box = { x: 60, y: 180, w: 540, h: 900 };
export const PRESENTER_BUBBLE: Box = { x: 160, y: 790, w: 240, h: 240 };

const FADE = 6;
// The bubble frames the head, not the whole landscape take.
const BUBBLE_ZOOM = { transform: 'scale(1.4) translateY(-9%)', transformOrigin: '50% 50%' } as const;

// The cut-out is cropped from a landscape take into a portrait slot, so the body meets the slot's
// sides in a straight line; fading the sides and the bottom hides that cut.
const CUTOUT_EDGE_MASK =
  'linear-gradient(to right, transparent 0%, black 16%, black 84%, transparent 100%), linear-gradient(to top, transparent 0%, black 14%)';

/**
 * The builder on camera for a run of lines. Each line shows its own ingested take (cut out when
 * the ingest produced a matte, the plain take otherwise), held on its first frame before it speaks and on its
 * last frame until the next line, so the slot never pops empty between lines. A line with no take
 * yet shows a placeholder silhouette naming the take to record.
 */
export const PresenterTrack: React.FC<{ lines: TimedLine[]; mode: PresenterMode; box: Box; until: number; enterAt?: number }> = ({
  lines,
  mode,
  box,
  until,
  enterAt,
}) => {
  const frame = useCurrentFrame();
  const start = enterAt ?? lines[0].from - FADE * 2;
  const opacity = interpolate(frame, [start, start + FADE * 2, until - FADE * 2, until], [0, 1, 1, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  return (
    <div style={{ position: 'absolute', left: box.x, top: box.y, width: box.w, height: box.h, opacity }}>
      <Frame mode={mode}>
        {lines.map((line, i) => (
          <PresenterSlot key={line.id} line={line} mode={mode} slotStart={i === 0 ? start : line.from} slotEnd={lines[i + 1]?.from ?? until} />
        ))}
      </Frame>
    </div>
  );
};

const PresenterSlot: React.FC<{ line: TimedLine; mode: PresenterMode; slotStart: number; slotEnd: number }> = ({ line, mode, slotStart, slotEnd }) => (
  <Sequence from={slotStart} durationInFrames={Math.max(1, slotEnd - slotStart)} layout="none">
    {line.presenter ? <Take line={line} mode={mode} offset={line.from - slotStart} /> : <Placeholder take={line.take} mode={mode} />}
  </Sequence>
);

const Frame: React.FC<{ mode: PresenterMode; children: React.ReactNode }> = ({ mode, children }) =>
  mode === 'bubble' ? (
    <AbsoluteFill
      style={{
        borderRadius: '50%',
        overflow: 'hidden',
        border: `3px solid ${COLOR.signal}`,
        boxShadow: `0 0 0 8px ${alpha(COLOR.signal, 0.12)}, 0 0 40px ${alpha(COLOR.signal, 0.35)}`,
        background: COLOR.ink2,
      }}
    >
      {children}
    </AbsoluteFill>
  ) : (
    <AbsoluteFill>{children}</AbsoluteFill>
  );

const Take: React.FC<{ line: TimedLine; mode: PresenterMode; offset: number }> = ({ line, mode, offset }) => {
  const frame = useCurrentFrame();
  const presenter = line.presenter;
  if (!presenter) return null;
  const matte = presenter.matte;
  const fadeEdges = Boolean(matte) && mode === 'full';
  const takeFrame = Math.min(Math.max(frame - offset, 0), line.frames - 1);
  return (
    <AbsoluteFill
      style={
        fadeEdges
          ? { maskImage: CUTOUT_EDGE_MASK, WebkitMaskImage: CUTOUT_EDGE_MASK, maskComposite: 'intersect', WebkitMaskComposite: 'source-in' }
          : { borderRadius: mode === 'full' ? 18 : undefined, overflow: 'hidden' }
      }
    >
      <Freeze frame={takeFrame}>
        <OffthreadVideo
          src={staticFile(matte ?? presenter.plain)}
          transparent={Boolean(matte)}
          muted
          style={{ width: '100%', height: '100%', objectFit: 'cover', objectPosition: 'center top', ...(mode === 'bubble' ? BUBBLE_ZOOM : {}) }}
        />
      </Freeze>
    </AbsoluteFill>
  );
};

const Placeholder: React.FC<{ take: string; mode: PresenterMode }> = ({ take, mode }) => (
  <AbsoluteFill style={{ alignItems: 'center', justifyContent: 'flex-end' }}>
    <svg viewBox="0 0 400 520" style={{ width: '100%', height: '100%' }} preserveAspectRatio="xMidYMax meet">
      <circle cx={200} cy={170} r={86} fill={alpha(COLOR.grid, 0.06)} stroke={COLOR.boneDim} strokeWidth={2} strokeDasharray="6 6" />
      <path
        d="M 40 520 C 40 360 110 290 200 290 C 290 290 360 360 360 520"
        fill={alpha(COLOR.grid, 0.06)}
        stroke={COLOR.boneDim}
        strokeWidth={2}
        strokeDasharray="6 6"
      />
    </svg>
    <div
      style={{
        position: 'absolute',
        top: mode === 'bubble' ? '42%' : 18,
        fontFamily: FONT.mono,
        fontSize: 18,
        letterSpacing: '0.2em',
        color: COLOR.signal,
      }}
    >
      {`PRESENTER ${take}`}
    </div>
  </AbsoluteFill>
);
