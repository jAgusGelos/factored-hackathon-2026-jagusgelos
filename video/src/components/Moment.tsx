import React from 'react';
import { AbsoluteFill, interpolate, useCurrentFrame } from 'remotion';
import { MODEL_NAME } from '../facts';
import type { TimedLine, TimedScene } from '../timeline';
import { COLOR, FONT, alpha } from '../theme';
import { Laptop, clipEventFrame, clipTaps, type Clip } from './Device';
import { PRESENTER_BUBBLE, PresenterTrack } from './Presenter';
import { SfxTrack, VoiceTrack, type Cue } from './Scene';
import { Caption, MonoLabel } from './Type';

interface CaptionCue {
  /** Footage event that brings the caption in; it stays until the next cue. */
  from: string;
  original?: string;
  english: string;
}

export interface CaptionedClip extends Clip {
  captions: CaptionCue[];
}

const LAPTOP_X = 600;
const LAPTOP_Y = 120;
const SCREEN_W = 1180;
const CAPTION_LEAD = 4;
export const LEFT_COLUMN = { x: 110, resultY: 530, width: 440 } as const;

export const Chapter: React.FC<{ number: string; title: string; subtitle: string }> = ({ number, title, subtitle }) => {
  const frame = useCurrentFrame();
  const p = interpolate(frame, [4, 18], [0, 1], { extrapolateRight: 'clamp' });
  return (
    <div style={{ position: 'absolute', left: LEFT_COLUMN.x, top: 150, width: LEFT_COLUMN.width - 20, opacity: p }}>
      <div
        style={{
          fontFamily: FONT.display,
          fontWeight: 900,
          fontStretch: '125%',
          fontSize: 120,
          lineHeight: 1,
          color: COLOR.signal,
          textShadow: `0 0 28px ${alpha(COLOR.signal, 0.6)}`,
        }}
      >
        {number}
      </div>
      <div style={{ fontFamily: FONT.display, fontWeight: 800, fontSize: 52, lineHeight: 1.05, color: COLOR.bone, marginTop: 18 }}>{title}</div>
      <MonoLabel style={{ marginTop: 22 }}>{subtitle}</MonoLabel>
    </div>
  );
};

function activeCaption(clip: CaptionedClip, frame: number): CaptionCue | undefined {
  for (let i = 0; i < clip.captions.length; i += 1) {
    const start = clipEventFrame(clip, clip.captions[i].from) - CAPTION_LEAD;
    const next = clip.captions[i + 1];
    const end = next ? clipEventFrame(clip, next.from) - CAPTION_LEAD : clip.until;
    if (frame >= start && frame < end) return clip.captions[i];
  }
  return undefined;
}

export const ClipOnLaptop: React.FC<{ clip: CaptionedClip }> = ({ clip }) => {
  const frame = useCurrentFrame();
  const opacity = interpolate(frame, [clip.from, clip.from + 8, clip.until - 8, clip.until], [0, 1, 1, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  if (opacity <= 0) return null;
  const caption = activeCaption(clip, frame);
  return (
    <div style={{ position: 'absolute', left: LAPTOP_X, top: LAPTOP_Y, opacity }}>
      <Laptop clip={clip} screenWidth={SCREEN_W} />
      {caption ? (
        <div style={{ position: 'absolute', left: 60, bottom: 70 }}>
          <Caption original={caption.original} english={caption.english} />
        </div>
      ) : null}
    </div>
  );
};

export const RealFootageNote: React.FC = () => (
  <MonoLabel size={15} style={{ position: 'absolute', left: LAPTOP_X + 18, top: 1010 }}>
    {`Real app · real model (${MODEL_NAME}) · model wait shortened in the edit`}
  </MonoLabel>
);

export const BubblePresenter: React.FC<{ lines: TimedLine[]; until: number }> = ({ lines, until }) => (
  <PresenterTrack lines={lines} mode="bubble" box={PRESENTER_BUBBLE} until={until} enterAt={lines[0].from - 12} />
);

export const tapCues = (clip: Clip): Cue[] => clipTaps(clip).map((tap) => ({ at: clipEventFrame(clip, tap.name), sfx: 'click_003', volume: 0.5 }));

export const MomentScene: React.FC<{
  scene: TimedScene;
  chapter: { number: string; title: string; subtitle: string };
  clip: CaptionedClip;
  resultEvent: string;
  result: React.ReactNode;
}> = ({ scene, chapter, clip, resultEvent, result }) => {
  const frame = useCurrentFrame();
  const resultAt = clipEventFrame(clip, resultEvent);
  const p = interpolate(frame, [resultAt + 4, resultAt + 14], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
  return (
    <AbsoluteFill>
      <Chapter {...chapter} />
      <ClipOnLaptop clip={clip} />
      <div style={{ position: 'absolute', left: LEFT_COLUMN.x, top: LEFT_COLUMN.resultY, width: LEFT_COLUMN.width, opacity: p, transform: `translateY(${(1 - p) * 16}px)` }}>
        {result}
      </div>
      <RealFootageNote />
      <BubblePresenter lines={scene.lines} until={scene.frames - 4} />
      <VoiceTrack scene={scene} />
      <SfxTrack cues={[...tapCues(clip), { at: resultAt, sfx: 'impactBell_heavy_000', volume: 0.18 }]} />
    </AbsoluteFill>
  );
};
