import { layoutScenes, toFrames, voLine, type TimedLine, type TimedScene } from '../timeline';

export type TeaserSceneId = 'hook' | 'reveal' | 'highlights' | 'punch';

// The same recorded lines as the launch video, packed tight: the hook speaks from frame 0.
export const TEASER_TIMELINE = layoutScenes<TeaserSceneId>([
  { id: 'hook', lines: [{ line: voLine('cold3'), lead: 0 }], tail: 0.3 },
  { id: 'reveal', lines: [{ line: voLine('reveal2'), lead: 0.1 }], tail: 0.45 },
  { id: 'highlights', lines: [{ line: voLine('close1'), lead: 0.4 }], tail: 0.4 },
  { id: 'punch', lines: [{ line: voLine('close2'), lead: 0.3 }], tail: 1.3 },
]);

export const TEASER_FRAMES = TEASER_TIMELINE.reduce((sum, scene) => sum + scene.frames, 0);

export function teaserScene(id: TeaserSceneId): TimedScene<TeaserSceneId> {
  const scene = TEASER_TIMELINE.find((s) => s.id === id);
  if (!scene) throw new Error(`no teaser scene ${id}`);
  return scene;
}

const PHRASE_LEAD_S = 0.2;

/**
 * Words `from` to `to` (exclusive) of a line as a line of their own, starting just before its first
 * word, so one recorded sentence can be shown as two kinetic phrases.
 */
export function linePart(line: TimedLine, from: number, to?: number): TimedLine {
  const display = line.text.split(' ');
  if (display.length !== line.words.length) throw new Error(`${line.id}: display and timed words differ, cannot split`);
  const words = line.words.slice(from, to);
  const offset = Math.max(0, words[0].start - PHRASE_LEAD_S);
  return {
    ...line,
    id: `${line.id}-${from}`,
    text: display.slice(from, to).join(' '),
    words: words.map((w) => ({ ...w, start: w.start - offset, end: w.end - offset })),
    from: line.from + toFrames(offset),
    frames: line.frames - toFrames(offset),
  };
}
