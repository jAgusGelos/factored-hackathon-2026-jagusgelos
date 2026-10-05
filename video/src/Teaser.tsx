import React from 'react';
import { Reel } from './components/Reel';
import { loadFonts } from './fonts';
import { Highlights } from './teaser/Highlights';
import { Hook } from './teaser/Hook';
import { Punch } from './teaser/Punch';
import { TeaserReveal } from './teaser/Reveal';
import { TEASER_TIMELINE, type TeaserSceneId } from './teaser/timeline';

loadFonts();

const SCENE_COMPONENTS: Record<TeaserSceneId, React.FC> = {
  hook: Hook,
  reveal: TeaserReveal,
  highlights: Highlights,
  punch: Punch,
};

/** The ~20 s vertical cut for social posts: hook, reveal, three real moments, the link. */
export const Teaser: React.FC = () => <Reel timeline={TEASER_TIMELINE} scenes={SCENE_COMPONENTS} glow={{ x: 540, y: 760 }} fadeInFirst={false} />;
