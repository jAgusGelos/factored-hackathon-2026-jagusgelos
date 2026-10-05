import React, { useId } from 'react';
import { AbsoluteFill, random, useCurrentFrame, useVideoConfig } from 'remotion';
import { COLOR, alpha } from '../theme';

const MINOR = 24;
const MAJOR = 96;
const DRIFT_PX_PER_FRAME = 0.12;
const GRAIN_FRAMES = 12;

/** React ids carry colons, which break `url(#id)` references in some renderers. */
export const useSvgId = (): string => `s${useId().replace(/[^a-zA-Z0-9]/g, '')}`;

export const GridPaper: React.FC<{ glow: { x: number; y: number } }> = ({ glow }) => {
  const frame = useCurrentFrame();
  const { width, height } = useVideoConfig();
  const id = useSvgId();
  const shift = (frame * DRIFT_PX_PER_FRAME) % MAJOR;
  return (
    <AbsoluteFill style={{ backgroundColor: COLOR.ink }}>
      <svg width={width} height={height}>
        <defs>
          <pattern id={`${id}-minor`} width={MINOR} height={MINOR} patternUnits="userSpaceOnUse" x={-shift} y={-shift * 0.5}>
            <path d={`M ${MINOR} 0 L 0 0 0 ${MINOR}`} fill="none" stroke={COLOR.grid} strokeOpacity={0.05} strokeWidth={1} />
          </pattern>
          <pattern id={`${id}-major`} width={MAJOR} height={MAJOR} patternUnits="userSpaceOnUse" x={-shift} y={-shift * 0.5}>
            <path d={`M ${MAJOR} 0 L 0 0 0 ${MAJOR}`} fill="none" stroke={COLOR.grid} strokeOpacity={0.12} strokeWidth={1.2} />
          </pattern>
          <radialGradient id={`${id}-glow`} cx={glow.x / width} cy={glow.y / height} r={0.45}>
            <stop offset="0" stopColor={COLOR.signal} stopOpacity={0.16} />
            <stop offset="1" stopColor={COLOR.signal} stopOpacity={0} />
          </radialGradient>
        </defs>
        <rect width={width} height={height} fill={`url(#${id}-minor)`} />
        <rect width={width} height={height} fill={`url(#${id}-major)`} />
        <rect width={width} height={height} fill={`url(#${id}-glow)`} />
      </svg>
    </AbsoluteFill>
  );
};

export const Post: React.FC = () => {
  const frame = useCurrentFrame();
  const { width, height } = useVideoConfig();
  const id = useSvgId();
  const seed = Math.floor(random(`grain-${frame % GRAIN_FRAMES}`) * 1000);
  return (
    <AbsoluteFill style={{ pointerEvents: 'none' }}>
      <AbsoluteFill style={{ background: `radial-gradient(ellipse at center, transparent 55%, ${alpha('#02060E', 0.62)} 100%)` }} />
      <svg width={width} height={height} style={{ position: 'absolute', mixBlendMode: 'overlay', opacity: 0.22 }}>
        <filter id={`${id}-grain`}>
          <feTurbulence type="fractalNoise" baseFrequency="0.9" numOctaves={2} seed={seed} stitchTiles="stitch" />
          <feColorMatrix type="saturate" values="0" />
        </filter>
        <rect width={width} height={height} filter={`url(#${id}-grain)`} />
      </svg>
    </AbsoluteFill>
  );
};

/** Bloom: the children plus a blurred, brighter copy screened on top (text and SVG only, not video). */
export const Glow: React.FC<{ children: React.ReactNode; radius?: number; strength?: number }> = ({ children, radius = 14, strength = 0.7 }) => (
  <div style={{ position: 'relative' }}>
    {children}
    <div
      aria-hidden
      style={{
        position: 'absolute',
        inset: 0,
        filter: `blur(${radius}px) brightness(1.5)`,
        opacity: strength,
        mixBlendMode: 'screen',
        pointerEvents: 'none',
      }}
    >
      {children}
    </div>
  </div>
);
