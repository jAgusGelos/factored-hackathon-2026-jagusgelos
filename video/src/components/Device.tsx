import React from 'react';
import { OffthreadVideo, Sequence, interpolate, staticFile, useCurrentFrame } from 'remotion';
import { COLOR, FPS } from '../theme';

export interface FootageEvent {
  name: string;
  t: number;
  kind?: 'type' | 'reply' | 'tap';
  text?: string;
  x?: number;
  y?: number;
}

export interface FootageMeta {
  clip: string;
  width: number;
  height: number;
  duration: number;
  events: FootageEvent[];
}

const BEZEL = 18;

/**
 * A laptop around real app footage. `rate` speeds the footage up to fit its beat; taps in the
 * footage's metadata get a ripple at the tapped spot.
 */
export const Laptop: React.FC<{
  meta: FootageMeta;
  from: number;
  rate?: number;
  startAt?: number;
  screenWidth: number;
  children?: React.ReactNode;
}> = ({ meta, from, rate = 1, startAt = 0, screenWidth, children }) => {
  const screenHeight = (screenWidth * meta.height) / meta.width;
  const scale = screenWidth / meta.width;
  return (
    <div style={{ position: 'relative', width: screenWidth + BEZEL * 2 }}>
      <div
        style={{
          background: '#05080F',
          border: `1.5px solid rgba(232, 238, 246, 0.28)`,
          borderRadius: 22,
          padding: BEZEL,
          boxShadow: '0 40px 120px rgba(0,0,0,0.6), 0 0 0 1px rgba(76,141,255,0.12)',
        }}
      >
        <div style={{ position: 'relative', width: screenWidth, height: screenHeight, overflow: 'hidden', borderRadius: 6, background: '#F6F7F9' }}>
          <Sequence from={from} layout="none">
            <OffthreadVideo
              src={staticFile(`footage/${meta.clip}.mp4`)}
              playbackRate={rate}
              startFrom={Math.round(startAt * FPS)}
              muted
              style={{ width: screenWidth, height: screenHeight }}
            />
          </Sequence>
          {meta.events
            .filter((e) => e.kind === 'tap')
            .map((e) => (
              <TapRipple
                key={e.name}
                at={from + Math.round(((e.t - startAt) / rate) * FPS)}
                x={(e.x ?? 0) * scale}
                y={(e.y ?? 0) * scale}
              />
            ))}
          {children}
        </div>
      </div>
      <div
        style={{
          height: 22,
          margin: '0 -70px',
          background: 'linear-gradient(#1B2433, #0B111C)',
          borderRadius: '0 0 26px 26px',
          border: '1px solid rgba(232, 238, 246, 0.18)',
          borderTop: 'none',
        }}
      />
    </div>
  );
};

const RIPPLE_FRAMES = 18;

const TapRipple: React.FC<{ at: number; x: number; y: number }> = ({ at, x, y }) => {
  const frame = useCurrentFrame();
  const p = (frame - at) / RIPPLE_FRAMES;
  if (p < -0.4 || p > 1) return null;
  const grow = interpolate(p, [-0.4, 0, 1], [0.6, 1, 2.4], { extrapolateRight: 'clamp' });
  const fade = interpolate(p, [-0.4, 0, 1], [0, 0.9, 0], { extrapolateRight: 'clamp' });
  return (
    <div
      style={{
        position: 'absolute',
        left: x - 28,
        top: y - 28,
        width: 56,
        height: 56,
        borderRadius: '50%',
        border: `3px solid ${COLOR.signal}`,
        background: 'rgba(76, 141, 255, 0.18)',
        transform: `scale(${grow})`,
        opacity: fade,
      }}
    />
  );
};

/** Frame (relative to the clip's start in the scene) when a footage event lands on screen. */
export function eventFrame(meta: FootageMeta, name: string, from: number, rate = 1, startAt = 0): number {
  const event = meta.events.find((e) => e.name === name);
  if (!event) throw new Error(`no event ${name} in ${meta.clip}`);
  return from + Math.round(((event.t - startAt) / rate) * FPS);
}
