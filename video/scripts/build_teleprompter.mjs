// Builds video/teleprompter/index.html (self-contained, opens with a double-click) from the
// narration in video/narration/lines.json and the target durations in video/src/meta/vo.json.
//
//   node video/scripts/build_teleprompter.mjs

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const VIDEO_DIR = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const readJson = (file) => JSON.parse(fs.readFileSync(path.join(VIDEO_DIR, file), 'utf8'));

const narration = readJson('narration/lines.json');
const durationsById = new Map(readJson('src/meta/vo.json').lines.map((line) => [line.id, line.duration]));
const takes = narration.lines.map((line) => ({ ...line, duration: durationsById.get(line.id) ?? 0 }));
const payload = JSON.stringify({ placements: narration.placements, lines: takes }).replaceAll('<', '\\u003c');

const template = fs.readFileSync(path.join(VIDEO_DIR, 'teleprompter', 'template.html'), 'utf8');
const out = path.join(VIDEO_DIR, 'teleprompter', 'index.html');
fs.writeFileSync(out, template.replace('/*TAKES*/', () => payload));
console.log(`${takes.length} takes -> ${out}`);
