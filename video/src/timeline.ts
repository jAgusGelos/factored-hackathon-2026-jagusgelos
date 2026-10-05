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

/** Silence before each line (s). Cuts sit in these pauses; longer ones are the story's beats. */
const LEAD_S = {
  cold1: 0.6, cold2: 1.0, cold3: 1.4,
  reveal1: 1.6, reveal2: 1.2,
  m1a: 1.0, m1b: 0.9,
  m2a: 1.0, m2b: 0.8,
  m3a: 1.0, m3b: 0.7, m3c: 0.9,
  how1: 1.2, how2: 1.0, how3: 1.0,
  close1: 1.2, close2: 1.0,
} as const;

/** Silence after a scene's last line before the next scene starts (s). */
const TAIL_S: Record<SceneId, number> = { cold: 1.6, reveal: 2.2, m1: 1.4, m2: 1.2, m3: 2.4, how: 1.4, close: 4.5 };

const SCENES = Object.keys(TAIL_S) as SceneId[];

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
    if (!(line.scene in TAIL_S)) throw new Error(`vo.json line ${line.id}: unknown scene "${line.scene}"`);
    if (!(line.id in LEAD_S)) throw new Error(`vo.json line ${line.id}: no lead in timeline.ts`);
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
      cursor += toFrames(LEAD_S[line.id as keyof typeof LEAD_S]);
      timed.push({ ...line, from: cursor - sceneFrom, frames: toFrames(line.duration) });
      cursor += toFrames(line.duration);
    }
    if (!timed.length) throw new Error(`scene ${id} has no lines`);
    cursor += toFrames(TAIL_S[id]);
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
