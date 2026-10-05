import vo from './meta/vo.json';
import { FPS } from './theme';

type Placement = 'presenter' | 'bubble' | 'voice' | 'diagram';
export type SceneId = 'cold' | 'reveal' | 'm1' | 'm2' | 'm3' | 'how' | 'close';

export interface Word {
  text: string;
  start: number;
  end: number;
}

export interface VoLine {
  id: string;
  take: string;
  scene: SceneId;
  placement: Placement;
  text: string;
  file: string;
  duration: number;
  words: Word[];
  source?: 'presenter';
  presenter?: { plain: string; matte?: string };
}

const LEAD_SECONDS_BY_LINE = {
  cold1: 0.5, cold2: 0.5, cold3: 0.9,
  reveal1: 1.1, reveal2: 0.6,
  m1a: 0.6, m1b: 0.4,
  m2a: 0.6, m2b: 0.35,
  m3a: 0.6, m3b: 0.35, m3c: 0.45,
  how1: 0.8, how2: 0.5, how3: 0.5,
  close1: 0.8, close2: 0.5,
} as const;

const TAIL_SECONDS_BY_SCENE: Record<SceneId, number> = { cold: 1.2, reveal: 1.8, m1: 1.0, m2: 0.9, m3: 1.9, how: 1.0, close: 4.5 };

const SCENES: readonly SceneId[] = ['cold', 'reveal', 'm1', 'm2', 'm3', 'how', 'close'];

export interface TimedLine extends VoLine {
  from: number;
  frames: number;
}

export interface TimedScene {
  id: SceneId;
  from: number;
  frames: number;
  lines: TimedLine[];
}

const toFrames = (s: number) => Math.round(s * FPS);

function validLines(raw: unknown[]): VoLine[] {
  return raw.map((entry) => {
    const line = entry as VoLine;
    if (!(line.scene in TAIL_SECONDS_BY_SCENE)) throw new Error(`vo.json line ${line.id}: unknown scene "${line.scene}"`);
    if (!(line.id in LEAD_SECONDS_BY_LINE)) throw new Error(`vo.json line ${line.id}: no lead in timeline.ts`);
    if (!line.words?.length) throw new Error(`vo.json line ${line.id}: no word timings`);
    return line;
  });
}

function buildTimeline(): TimedScene[] {
  const lines = validLines(vo.lines);
  const scenes: TimedScene[] = [];
  let cursor = 0;
  for (const id of SCENES) {
    const sceneFrom = cursor;
    const timed: TimedLine[] = [];
    for (const line of lines.filter((l) => l.scene === id)) {
      cursor += toFrames(LEAD_SECONDS_BY_LINE[line.id as keyof typeof LEAD_SECONDS_BY_LINE]);
      timed.push({ ...line, from: cursor - sceneFrom, frames: toFrames(line.duration) });
      cursor += toFrames(line.duration);
    }
    if (!timed.length) throw new Error(`scene ${id} has no lines`);
    cursor += toFrames(TAIL_SECONDS_BY_SCENE[id]);
    scenes.push({ id, from: sceneFrom, frames: cursor - sceneFrom, lines: timed });
  }
  return scenes;
}

export const TIMELINE = buildTimeline();
export const TOTAL_FRAMES = TIMELINE.reduce((sum, scene) => sum + scene.frames, 0);

export function sceneOf(id: SceneId): TimedScene {
  const scene = TIMELINE.find((s) => s.id === id);
  if (!scene) throw new Error(`no scene ${id}`);
  return scene;
}

export function lineOf(scene: TimedScene, id: string): TimedLine {
  const line = scene.lines.find((l) => l.id === id);
  if (!line) throw new Error(`no line ${id} in ${scene.id}`);
  return line;
}

/** Scene-relative frame where `word` (nth occurrence, case-insensitive) starts being said. */
export function wordFrame(line: TimedLine, word: string, nth = 0): number {
  const matches = line.words.filter((w) => w.text.toLowerCase() === word.toLowerCase());
  const hit = matches[nth];
  if (!hit) throw new Error(`word "${word}" not in ${line.id}`);
  return line.from + toFrames(hit.start);
}
