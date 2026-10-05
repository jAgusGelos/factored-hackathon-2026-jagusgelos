import { getLength, getPointAtLength } from '@remotion/paths';
import React from 'react';
import { Easing, interpolate, useCurrentFrame } from 'remotion';
import { COLOR } from '../theme';

/**
 * A stroke laid down by the plotter pen between `from` and `to` (frames), with the spark at the
 * pen's head while it draws. Pure function of the frame, like the kit's Plot.
 */
export const PenPath: React.FC<{
  d: string;
  from: number;
  to: number;
  color?: string;
  width?: number;
  dashed?: boolean;
  spark?: boolean;
}> = ({ d, from, to, color = COLOR.bone, width = 2, dashed = false, spark = true }) => {
  const frame = useCurrentFrame();
  const length = getLength(d);
  const progress = interpolate(frame, [from, to], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.inOut(Easing.cubic),
  });
  if (progress <= 0) return null;
  const head = getPointAtLength(d, length * progress) ?? { x: 0, y: 0 };
  const drawing = progress < 1;
  return (
    <g>
      {dashed ? (
        <>
          <defs>
            <mask id={`reveal-${from}-${to}-${length.toFixed(0)}`}>
              <path d={d} stroke="white" strokeWidth={width + 6} fill="none" strokeDasharray={`${length} ${length}`} strokeDashoffset={length * (1 - progress)} />
            </mask>
          </defs>
          <path d={d} stroke={color} strokeWidth={width} fill="none" strokeDasharray="8 8" mask={`url(#reveal-${from}-${to}-${length.toFixed(0)})`} />
        </>
      ) : (
        <path
          d={d}
          stroke={color}
          strokeWidth={width}
          fill="none"
          strokeLinecap="round"
          strokeDasharray={`${length} ${length}`}
          strokeDashoffset={length * (1 - progress)}
        />
      )}
      {spark && drawing ? <Spark x={head.x} y={head.y} /> : null}
    </g>
  );
};

/** The pen's head: a hot core in a soft halo. */
export const Spark: React.FC<{ x: number; y: number; scale?: number }> = ({ x, y, scale = 1 }) => (
  <g>
    <circle cx={x} cy={y} r={26 * scale} fill={COLOR.signal} opacity={0.18} />
    <circle cx={x} cy={y} r={11 * scale} fill={COLOR.signal} opacity={0.45} />
    <circle cx={x} cy={y} r={4.5 * scale} fill="#FFFFFF" />
  </g>
);

/** A hairline box drawn by the pen, corner to corner. */
export function boxPath(x: number, y: number, w: number, h: number): string {
  return `M ${x} ${y} L ${x + w} ${y} L ${x + w} ${y + h} L ${x} ${y + h} Z`;
}
