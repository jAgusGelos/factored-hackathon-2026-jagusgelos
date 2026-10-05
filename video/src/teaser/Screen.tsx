import React from 'react';
import { OffthreadVideo, Sequence, staticFile } from 'remotion';
import type { Clip } from '../components/Device';
import { COLOR, FPS, alpha } from '../theme';

/** The part of the recorded 1920x1200 app frame to show, in source pixels. */
export interface Crop {
  x: number;
  y: number;
  w: number;
  h: number;
}

export const BEZEL = 16;

/**
 * A monitor-style frame around a window onto the recorded app: the same crop for every clip,
 * cut hard from one clip to the next so the frame itself never moves.
 */
export const Screen: React.FC<{ clips: Clip[]; crop: Crop; width: number }> = ({ clips, crop, width }) => {
  const scale = width / crop.w;
  return (
    <div
      style={{
        background: COLOR.screen,
        border: `1.5px solid ${alpha(COLOR.grid, 0.28)}`,
        borderRadius: 22,
        padding: BEZEL,
        boxShadow: `0 40px 120px rgba(0,0,0,0.6), 0 0 0 1px ${alpha(COLOR.signal, 0.12)}`,
      }}
    >
      <div style={{ position: 'relative', width, height: crop.h * scale, overflow: 'hidden', borderRadius: 6, background: COLOR.bone }}>
        {clips.map((clip) => (
          <Sequence key={clip.meta.clip} from={clip.from} durationInFrames={clip.until - clip.from} layout="none">
            <OffthreadVideo
              src={staticFile(`footage/${clip.meta.clip}.mp4`)}
              playbackRate={clip.rate}
              trimBefore={Math.round(clip.startAt * FPS)}
              muted
              style={{
                position: 'absolute',
                left: -crop.x * scale,
                top: -crop.y * scale,
                width: clip.meta.width * scale,
                height: clip.meta.height * scale,
              }}
            />
          </Sequence>
        ))}
      </div>
    </div>
  );
};
