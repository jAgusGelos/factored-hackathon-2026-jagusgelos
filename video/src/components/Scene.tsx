import React from 'react';
import { AbsoluteFill, Audio, Sequence, interpolate, staticFile, useCurrentFrame } from 'remotion';
import type { TimedLine } from '../timeline';
import { CLAMP } from '../theme';

const DIP_FRAMES = 9;
/**
 * Scene content dips through the background at both ends: old out, then new in, never a muddy mix.
 * `fadeIn={false}` opens on the content itself (a video's first scene, where frame 0 is the hook).
 */
export const SceneFade: React.FC<{ frames: number; fadeIn?: boolean; children: React.ReactNode }> = ({ frames, fadeIn = true, children }) => {
  const frame = useCurrentFrame();
  const opacity = interpolate(frame, [0, DIP_FRAMES, frames - DIP_FRAMES, frames], [fadeIn ? 0 : 1, 1, 1, 0], CLAMP);
  return <AbsoluteFill style={{ opacity }}>{children}</AbsoluteFill>;
};

export const VoiceTrack: React.FC<{ scene: { lines: TimedLine[] } }> = ({ scene }) => (
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
  const opacity = interpolate(frame, [line.from - lead, line.from, until - 6, until], [0, 1, 1, 0], CLAMP);
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
  const p = interpolate(frame, [at, at + frames], [0, 1], CLAMP);
  if (p <= 0) return null;
  return <div style={{ position: 'absolute', opacity: p, transform: `translateY(${(1 - p) * rise}px)`, ...style }}>{children}</div>;
};
