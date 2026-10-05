import React from 'react';
import { OffthreadVideo, Sequence, interpolate, staticFile, useCurrentFrame } from 'remotion';
import { COLOR, FPS, alpha } from '../theme';

interface FootageEvent {
  name: string;
  t: number;
  kind?: string;
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

/** A recorded clip placed in a scene: `rate` speeds it up to fit its beat, `startAt` skips its head. */
export interface Clip {
  meta: FootageMeta;
  from: number;
  rate: number;
  startAt: number;
  until: number;
}

export function asFootage(json: unknown): FootageMeta {
  const meta = json as FootageMeta;
  if (!meta.clip || !(meta.width > 0) || !Array.isArray(meta.events)) throw new Error('footage metadata is malformed');
  return meta;
}

export function clipEventFrame(clip: Clip, name: string): number {
  const event = clip.meta.events.find((e) => e.name === name);
  if (!event) throw new Error(`no event ${name} in ${clip.meta.clip}`);
  return clip.from + Math.round(((event.t - clip.startAt) / clip.rate) * FPS);
}

export function clipTaps(clip: Clip): FootageEvent[] {
  return clip.meta.events.filter((e) => e.kind === 'tap');
}

const BEZEL = 18;

export const Laptop: React.FC<{ clip: Clip; screenWidth: number }> = ({ clip, screenWidth }) => {
  const { meta } = clip;
  const screenHeight = (screenWidth * meta.height) / meta.width;
  const scale = screenWidth / meta.width;
  return (
    <div style={{ position: 'relative', width: screenWidth + BEZEL * 2 }}>
      <div
        style={{
          background: COLOR.screen,
          border: `1.5px solid ${alpha(COLOR.grid, 0.28)}`,
          borderRadius: 22,
          padding: BEZEL,
          boxShadow: `0 40px 120px rgba(0,0,0,0.6), 0 0 0 1px ${alpha(COLOR.signal, 0.12)}`,
        }}
      >
        <div style={{ position: 'relative', width: screenWidth, height: screenHeight, overflow: 'hidden', borderRadius: 6, background: COLOR.bone }}>
          <Sequence from={clip.from} layout="none">
            <OffthreadVideo
              src={staticFile(`footage/${meta.clip}.mp4`)}
              playbackRate={clip.rate}
              trimBefore={Math.round(clip.startAt * FPS)}
              muted
              style={{ width: screenWidth, height: screenHeight }}
            />
          </Sequence>
          {clipTaps(clip).map((tap) => (
            <TapRipple key={tap.name} at={clipEventFrame(clip, tap.name)} x={(tap.x ?? 0) * scale} y={(tap.y ?? 0) * scale} />
          ))}
        </div>
      </div>
      <div
        style={{
          height: 22,
          margin: '0 -70px',
          background: 'linear-gradient(#1B2433, #0B111C)',
          borderRadius: '0 0 26px 26px',
          border: `1px solid ${alpha(COLOR.grid, 0.18)}`,
          borderTop: 'none',
        }}
      />
    </div>
  );
};

const RIPPLE_FRAMES = 18;
const RIPPLE_SIZE = 56;

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
        left: x - RIPPLE_SIZE / 2,
        top: y - RIPPLE_SIZE / 2,
        width: RIPPLE_SIZE,
        height: RIPPLE_SIZE,
        borderRadius: '50%',
        border: `3px solid ${COLOR.signal}`,
        background: alpha(COLOR.signal, 0.18),
        transform: `scale(${grow})`,
        opacity: fade,
      }}
    />
  );
};
