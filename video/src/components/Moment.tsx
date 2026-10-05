import React from 'react';
import { interpolate, useCurrentFrame } from 'remotion';
import type { TimedLine } from '../timeline';
import { COLOR, FONT } from '../theme';
import { Laptop, eventFrame, type FootageMeta } from './Device';
import { PresenterTrack } from './Presenter';
import { Caption, MonoLabel } from './Type';

export interface CaptionCue {
  /** Footage event that brings the caption in. */
  from: string;
  /** Footage event that takes it out (default: the next cue). */
  to?: string;
  original?: string;
  english: string;
}

export interface Clip {
  meta: FootageMeta;
  from: number;
  rate?: number;
  startAt?: number;
  until: number;
  captions: CaptionCue[];
}

export const LAPTOP_X = 600;
export const LAPTOP_Y = 120;
export const SCREEN_W = 1180;
const CAPTION_LEAD = 4;

/** Chapter number and claim in the left column, like a plate's title block. */
export const Chapter: React.FC<{ number: string; title: string; subtitle: string }> = ({ number, title, subtitle }) => {
  const frame = useCurrentFrame();
  const p = interpolate(frame, [4, 18], [0, 1], { extrapolateRight: 'clamp' });
  return (
    <div style={{ position: 'absolute', left: 110, top: 150, width: 420, opacity: p }}>
      <div
        style={{
          fontFamily: FONT.display,
          fontWeight: 900,
          fontStretch: '125%',
          fontSize: 120,
          lineHeight: 1,
          color: COLOR.signal,
          textShadow: `0 0 28px rgba(76,141,255,0.6)`,
        }}
      >
        {number}
      </div>
      <div style={{ fontFamily: FONT.display, fontWeight: 800, fontSize: 52, lineHeight: 1.05, color: COLOR.bone, marginTop: 18 }}>{title}</div>
      <MonoLabel style={{ marginTop: 22 }}>{subtitle}</MonoLabel>
    </div>
  );
};

/** One footage clip in the laptop with its English captions, visible from `from` to `until`. */
export const ClipOnLaptop: React.FC<{ clip: Clip; screenWidth?: number }> = ({ clip, screenWidth = SCREEN_W }) => {
  const frame = useCurrentFrame();
  const { meta, from, rate = 1, startAt = 0, until } = clip;
  const opacity = interpolate(frame, [from - 8, from, until - 8, until], [0, 1, 1, 0], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
  if (opacity <= 0) return null;
  const at = (name: string) => eventFrame(meta, name, from, rate, startAt);
  const active = clip.captions.find((cue, i) => {
    const start = at(cue.from) - CAPTION_LEAD;
    const next = clip.captions[i + 1];
    const end = cue.to ? at(cue.to) : next ? at(next.from) - CAPTION_LEAD : until;
    return frame >= start && frame < end;
  });
  return (
    <div style={{ position: 'absolute', left: LAPTOP_X, top: LAPTOP_Y, opacity }}>
      <Laptop meta={meta} from={from} rate={rate} startAt={startAt} screenWidth={screenWidth} />
      {active ? (
        <div style={{ position: 'absolute', left: 60, bottom: 70 }}>
          <Caption original={active.original} english={active.english} />
        </div>
      ) : null}
    </div>
  );
};

/** The footer note under every product moment: what is real in the footage. */
export const RealFootageNote: React.FC = () => (
  <MonoLabel size={15} style={{ position: 'absolute', left: LAPTOP_X + 18, top: 1010 }}>
    Real app · real model (Claude Haiku 4.5) · model wait shortened in the edit
  </MonoLabel>
);

export const BubblePresenter: React.FC<{ lines: TimedLine[]; until: number }> = ({ lines, until }) => (
  <PresenterTrack lines={lines} mode="bubble" box={{ x: 160, y: 790, w: 240, h: 240 }} until={until} enterAt={lines[0].from - 12} />
);
