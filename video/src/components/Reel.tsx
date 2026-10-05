import React from 'react';
import { AbsoluteFill, Sequence } from 'remotion';
import type { TimedScene } from '../timeline';
import { SceneFade } from './Scene';
import { GridPaper, Post } from './Stage';

/** A whole video: graph paper, its scenes back to back (each dipping through the paper), then glow and grain. */
export function Reel<Id extends string>({
  timeline,
  scenes,
  glow,
  fadeInFirst = true,
}: {
  timeline: TimedScene<Id>[];
  scenes: Record<Id, React.FC>;
  glow: { x: number; y: number };
  fadeInFirst?: boolean;
}): React.ReactElement {
  return (
    <AbsoluteFill>
      <GridPaper glow={glow} />
      {timeline.map((scene, i) => {
        const Scene: React.FC = scenes[scene.id];
        return (
          <Sequence key={scene.id} from={scene.from} durationInFrames={scene.frames} name={scene.id}>
            <SceneFade frames={scene.frames} fadeIn={fadeInFirst || i > 0}>
              <Scene />
            </SceneFade>
          </Sequence>
        );
      })}
      <Post />
    </AbsoluteFill>
  );
}
