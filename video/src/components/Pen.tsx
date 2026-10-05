import { getLength, getPointAtLength } from '@remotion/paths';
import React from 'react';
import { Easing, interpolate, useCurrentFrame } from 'remotion';
import { CLAMP, COLOR, alpha } from '../theme';
import { useSvgId } from './Stage';

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
  const maskId = useSvgId();
  const length = getLength(d);
  const progress = interpolate(frame, [from, to], [0, 1], {
    ...CLAMP,
    easing: Easing.inOut(Easing.cubic),
  });
  if (progress <= 0) return null;
  const head = getPointAtLength(d, length * progress) ?? { x: 0, y: 0 };
  const reveal = { strokeDasharray: `${length} ${length}`, strokeDashoffset: length * (1 - progress) };
  return (
    <g>
      {dashed ? (
        <>
          <defs>
            <mask id={maskId}>
              <path d={d} stroke="white" strokeWidth={width + 6} fill="none" {...reveal} />
            </mask>
          </defs>
          <path d={d} stroke={color} strokeWidth={width} fill="none" strokeDasharray="8 8" mask={`url(#${maskId})`} />
        </>
      ) : (
        <path d={d} stroke={color} strokeWidth={width} fill="none" strokeLinecap="round" {...reveal} />
      )}
      {spark && progress < 1 ? <Spark x={head.x} y={head.y} /> : null}
    </g>
  );
};

export const Spark: React.FC<{ x: number; y: number }> = ({ x, y }) => (
  <g>
    <circle cx={x} cy={y} r={26} fill={alpha(COLOR.signal, 0.18)} />
    <circle cx={x} cy={y} r={11} fill={alpha(COLOR.signal, 0.45)} />
    <circle cx={x} cy={y} r={4.5} fill="#FFFFFF" />
  </g>
);

export function boxPath(x: number, y: number, w: number, h: number): string {
  return `M ${x} ${y} L ${x + w} ${y} L ${x + w} ${y + h} L ${x} ${y + h} Z`;
}
