// Renders stills of the Launch composition at the given frames (one bundle, many frames),
// for checking the edit frame by frame.
//
//   node video/scripts/stills.mjs <out-dir> 100 470 800 ...

import { bundle } from '@remotion/bundler';
import { renderStill, selectComposition } from '@remotion/renderer';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const VIDEO_DIR = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const [outDir, ...frames] = process.argv.slice(2);
if (!outDir || !frames.length) throw new Error('usage: stills.mjs <out-dir> <frame> [frame...]');
fs.mkdirSync(outDir, { recursive: true });

const serveUrl = await bundle({ entryPoint: path.join(VIDEO_DIR, 'src', 'index.ts') });
const composition = await selectComposition({ serveUrl, id: 'Launch' });
for (const frame of frames.map(Number)) {
  const output = path.join(outDir, `f${String(frame).padStart(5, '0')}.jpg`);
  await renderStill({ serveUrl, composition, frame, output, imageFormat: 'jpeg', jpegQuality: 85 });
  console.log(output);
}
