import React from 'react';
import { AbsoluteFill, OffthreadVideo, Sequence, interpolate, staticFile, useCurrentFrame } from 'remotion';
import type { TimedLine } from '../timeline';
import { COLOR, FONT } from '../theme';

export type PresenterMode = 'full' | 'bubble';

export interface Box {
  x: number;
  y: number;
  w: number;
  h: number;
}

const FADE = 6;

/**
 * The builder on camera for a run of lines. Each line plays its own ingested take (cut out, or
 * the plain take in a framed card); until the takes exist, a placeholder silhouette holds the
 * slot and names the take being said.
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
  const recorded = lines.some((line) => line.presenter);
  const current = [...lines].reverse().find((line) => frame >= line.from - FADE) ?? lines[0];
  return (
    <div style={{ position: 'absolute', left: box.x, top: box.y, width: box.w, height: box.h, opacity }}>
      <Frame mode={mode}>
        {recorded
          ? lines.map((line) => (
              <Sequence key={line.id} from={line.from} durationInFrames={line.frames} layout="none">
                <Take line={line} mode={mode} />
              </Sequence>
            ))
          : <Placeholder take={current.take} mode={mode} />}
      </Frame>
    </div>
  );
};

const Frame: React.FC<{ mode: PresenterMode; children: React.ReactNode }> = ({ mode, children }) =>
  mode === 'bubble' ? (
    <AbsoluteFill
      style={{
        borderRadius: '50%',
        overflow: 'hidden',
        border: `3px solid ${COLOR.signal}`,
        boxShadow: `0 0 0 8px rgba(76,141,255,0.12), 0 0 40px rgba(76,141,255,0.35)`,
        background: COLOR.ink2,
      }}
    >
      {children}
    </AbsoluteFill>
  ) : (
    <AbsoluteFill>{children}</AbsoluteFill>
  );

const Take: React.FC<{ line: TimedLine; mode: PresenterMode }> = ({ line, mode }) => {
  const presenter = line.presenter;
  if (!presenter) return null;
  const cutOut = mode === 'full' && presenter.matte;
  return (
    <AbsoluteFill style={cutOut ? undefined : { borderRadius: mode === 'full' ? 18 : undefined, overflow: 'hidden' }}>
      <OffthreadVideo
        src={staticFile(cutOut ? presenter.matte! : presenter.plain)}
        transparent={Boolean(cutOut)}
        muted
        style={{ width: '100%', height: '100%', objectFit: 'cover', objectPosition: 'center top' }}
      />
    </AbsoluteFill>
  );
};

const Placeholder: React.FC<{ take: string; mode: PresenterMode }> = ({ take, mode }) => (
  <AbsoluteFill style={{ alignItems: 'center', justifyContent: 'flex-end' }}>
    <svg viewBox="0 0 400 520" style={{ width: '100%', height: mode === 'bubble' ? '100%' : '100%' }} preserveAspectRatio="xMidYMax meet">
      <circle cx={200} cy={170} r={86} fill="rgba(232,238,246,0.06)" stroke={COLOR.boneDim} strokeWidth={2} strokeDasharray="6 6" />
      <path
        d="M 40 520 C 40 360 110 290 200 290 C 290 290 360 360 360 520"
        fill="rgba(232,238,246,0.06)"
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
        fontSize: mode === 'bubble' ? 20 : 18,
        letterSpacing: '0.2em',
        color: COLOR.signal,
      }}
    >
      {`PRESENTER ${take}`}
    </div>
  </AbsoluteFill>
);
