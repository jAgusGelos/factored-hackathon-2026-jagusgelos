import React from 'react';
import { AbsoluteFill, Audio, Sequence, interpolate, staticFile, useCurrentFrame } from 'remotion';
import type { TimedLine, TimedScene } from '../timeline';

const DIP_FRAMES = 9;
const fadeClamp = { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' } as const;

/** Scene content dips through the background at both ends: old out, then new in, never a muddy mix. */
export const SceneFade: React.FC<{ frames: number; children: React.ReactNode }> = ({ frames, children }) => {
  const frame = useCurrentFrame();
  const opacity = interpolate(frame, [0, DIP_FRAMES, frames - DIP_FRAMES, frames], [0, 1, 1, 0], fadeClamp);
  return <AbsoluteFill style={{ opacity }}>{children}</AbsoluteFill>;
};

export const VoiceTrack: React.FC<{ scene: TimedScene }> = ({ scene }) => (
  <>
    {scene.lines.map((line) => (
      <Sequence key={line.id} from={line.from} durationInFrames={line.frames + 2} layout="none">
        <Audio src={staticFile(line.file)} />
      </Sequence>
    ))}
  </>
);

export interface Cue {
  at: number;
  sfx: string;
  volume?: number;
}

export const SfxTrack: React.FC<{ cues: Cue[] }> = ({ cues }) => (
  <>
    {cues.map((cue) => (
      <Sequence key={`${cue.sfx}-${cue.at}`} from={cue.at} layout="none">
        <Audio src={staticFile(`sfx/${cue.sfx}.ogg`)} volume={cue.volume ?? 0.3} />
      </Sequence>
    ))}
  </>
);

export const WhileLine: React.FC<{ line: TimedLine; until: number; lead?: number; children: React.ReactNode }> = ({
  line,
  until,
  lead = 8,
  children,
}) => {
  const frame = useCurrentFrame();
  const opacity = interpolate(frame, [line.from - lead, line.from, until - 6, until], [0, 1, 1, 0], fadeClamp);
  if (opacity <= 0) return null;
  return <AbsoluteFill style={{ opacity }}>{children}</AbsoluteFill>;
};

export const Appear: React.FC<{ at: number; frames?: number; rise?: number; style?: React.CSSProperties; children: React.ReactNode }> = ({
  at,
  frames = 10,
  rise = 16,
  style,
  children,
}) => {
  const frame = useCurrentFrame();
  const p = interpolate(frame, [at, at + frames], [0, 1], fadeClamp);
  if (p <= 0) return null;
  return <div style={{ position: 'absolute', opacity: p, transform: `translateY(${(1 - p) * rise}px)`, ...style }}>{children}</div>;
};
