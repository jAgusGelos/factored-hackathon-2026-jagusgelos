import React from 'react';
import { AbsoluteFill, Sequence } from 'remotion';
import { SceneFade } from './components/Scene';
import { GridPaper, Post } from './components/Stage';
import { loadFonts } from './fonts';
import { Close } from './scenes/Close';
import { Cold } from './scenes/Cold';
import { How } from './scenes/How';
import { MomentAsk, MomentHandOff, MomentResolve } from './scenes/Moments';
import { Reveal } from './scenes/Reveal';
import { TIMELINE, type SceneId } from './timeline';

loadFonts();

const SCENE_COMPONENTS: Record<SceneId, React.FC> = {
  cold: Cold,
  reveal: Reveal,
  m1: MomentResolve,
  m2: MomentAsk,
  m3: MomentHandOff,
  how: How,
  close: Close,
};

export const Launch: React.FC = () => (
  <AbsoluteFill>
    <GridPaper glow={{ x: 960, y: 540 }} />
    {TIMELINE.map((scene) => {
      const Scene = SCENE_COMPONENTS[scene.id];
      return (
        <Sequence key={scene.id} from={scene.from} durationInFrames={scene.frames} name={scene.id}>
          <SceneFade frames={scene.frames}>
            <Scene />
          </SceneFade>
        </Sequence>
      );
    })}
    <Post />
  </AbsoluteFill>
);
