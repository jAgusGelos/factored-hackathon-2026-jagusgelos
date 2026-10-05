import React from 'react';
import { AbsoluteFill, random, useCurrentFrame } from 'remotion';
import { COLOR, HEIGHT, WIDTH } from '../theme';

const MINOR = 24;
const MAJOR = 96;
const DRIFT_PX_PER_FRAME = 0.12;
const GRAIN_FRAMES = 12;

/** Graph paper on deep navy: minor and major lines, a slow drift, a glow where the action is. */
export const GridPaper: React.FC<{ glow?: { x: number; y: number; strength: number }; dim?: number }> = ({ glow, dim = 1 }) => {
  const frame = useCurrentFrame();
  const shift = (frame * DRIFT_PX_PER_FRAME) % MAJOR;
  return (
    <AbsoluteFill style={{ backgroundColor: COLOR.ink }}>
      <svg width={WIDTH} height={HEIGHT} style={{ opacity: dim }}>
        <defs>
          <pattern id="minor" width={MINOR} height={MINOR} patternUnits="userSpaceOnUse" x={-shift} y={-shift * 0.5}>
            <path d={`M ${MINOR} 0 L 0 0 0 ${MINOR}`} fill="none" stroke={COLOR.grid} strokeOpacity={0.05} strokeWidth={1} />
          </pattern>
          <pattern id="major" width={MAJOR} height={MAJOR} patternUnits="userSpaceOnUse" x={-shift} y={-shift * 0.5}>
            <path d={`M ${MAJOR} 0 L 0 0 0 ${MAJOR}`} fill="none" stroke={COLOR.grid} strokeOpacity={0.12} strokeWidth={1.2} />
          </pattern>
          {glow ? (
            <radialGradient id="glow" cx={glow.x / WIDTH} cy={glow.y / HEIGHT} r={0.45}>
              <stop offset="0" stopColor={COLOR.signal} stopOpacity={0.16 * glow.strength} />
              <stop offset="1" stopColor={COLOR.signal} stopOpacity={0} />
            </radialGradient>
          ) : null}
        </defs>
        <rect width={WIDTH} height={HEIGHT} fill="url(#minor)" />
        <rect width={WIDTH} height={HEIGHT} fill="url(#major)" />
        {glow ? <rect width={WIDTH} height={HEIGHT} fill="url(#glow)" /> : null}
      </svg>
    </AbsoluteFill>
  );
};

/** The post layer over everything: vignette and animated film grain. */
export const Post: React.FC = () => {
  const frame = useCurrentFrame();
  const seed = Math.floor(random(`grain-${frame % GRAIN_FRAMES}`) * 1000);
  return (
    <AbsoluteFill style={{ pointerEvents: 'none' }}>
      <AbsoluteFill
        style={{ background: 'radial-gradient(ellipse at center, rgba(0,0,0,0) 55%, rgba(2,6,14,0.62) 100%)' }}
      />
      <svg width={WIDTH} height={HEIGHT} style={{ position: 'absolute', mixBlendMode: 'overlay', opacity: 0.22 }}>
        <filter id={`grain-${seed}`}>
          <feTurbulence type="fractalNoise" baseFrequency="0.9" numOctaves={2} seed={seed} stitchTiles="stitch" />
          <feColorMatrix type="saturate" values="0" />
        </filter>
        <rect width={WIDTH} height={HEIGHT} filter={`url(#grain-${seed})`} />
      </svg>
    </AbsoluteFill>
  );
};

/** Bloom: the children plus a blurred, brighter copy blended on top. */
export const Glow: React.FC<{ children: React.ReactNode; radius?: number; strength?: number; style?: React.CSSProperties }> = ({
  children,
  radius = 14,
  strength = 0.7,
  style,
}) => (
  <div style={{ position: 'relative', ...style }}>
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
