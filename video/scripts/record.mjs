// Records the app moments the launch video shows, against the real local app and model.
//
//   node video/scripts/record.mjs [m1 m2 m3 pt]
//
// Needs the repo's .env (ANTHROPIC_API_KEY) and data/ (fixture, demo users) in the worktree.
// Each clip gets a fresh app server with an empty data/app.db, because the policy
// remembers credits across cases (a second take of m1 would otherwise go to a person).
//
// Frames come from Chromium's screencast (sharp JPEGs at 2x scale, not Playwright's
// low-bitrate video). The model's waiting time is cut down to WAIT_KEEP_S in the output:
// the footage shows real replies, only the dead time is shorter, and VIDEO.md says so.
// Output: video/public/footage/<clip>.mp4 plus video/src/meta/footage/<clip>.json with
// the tap positions and the time each reply landed, on the cut timeline.

import { spawn, spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const VIDEO_DIR = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const REPO = path.dirname(VIDEO_DIR);
const PYTHON = process.env.APP_PYTHON ?? '/home/agus/Escritorio/factored-hackathon-2026-jagusgelos/.venv/bin/python';
const PORT = Number(process.env.RECORD_PORT ?? 8812);
const BASE = `http://127.0.0.1:${PORT}`;
const VIEWPORT = { width: 1200, height: 750 };
const HANDOFF_VIEWPORT = { width: 1200, height: 2200 };
const SCALE = 2;
const FPS = 30;
const WAIT_KEEP_S = 0.9;
const TYPE_DELAY_MS = 38;
const RAW_DIR = path.join(VIDEO_DIR, 'recordings');
const OUT_DIR = path.join(VIDEO_DIR, 'public', 'footage');
const META_DIR = path.join(VIDEO_DIR, 'src', 'meta', 'footage');

const STATEMENT_ANSWERS = [
  'Nunca compré en esa tienda y no la conozco. Tengo la tarjeta conmigo. Lo vi ayer por una alerta de la app.',
  'No, no veo otros cargos raros.',
  'No.',
];

async function startServer() {
  for (const suffix of ['', '-wal', '-shm']) fs.rmSync(path.join(REPO, 'data', `app.db${suffix}`), { force: true });
  const server = spawn(PYTHON, ['-m', 'uvicorn', 'app.main:app', '--port', String(PORT)], {
    cwd: REPO,
    stdio: ['ignore', 'ignore', 'pipe'],
  });
  for (let i = 0; i < 60; i += 1) {
    try {
      if ((await fetch(BASE + '/')).ok) return server;
    } catch {
      // not listening yet
    }
    await new Promise((r) => setTimeout(r, 500));
  }
  server.kill();
  throw new Error(`app server did not start on ${BASE}`);
}

/** The next clip's server must not find this one still answering on the same port. */
async function stopServer(server) {
  const exited = new Promise((resolve) => server.once('exit', resolve));
  server.kill();
  await exited;
}

class Recorder {
  constructor(page, clip, viewport) {
    this.page = page;
    this.clip = clip;
    this.viewport = viewport;
    this.frames = [];
    this.waits = [];
    this.events = [];
    this.dir = path.join(RAW_DIR, clip);
    fs.rmSync(this.dir, { recursive: true, force: true });
    fs.mkdirSync(this.dir, { recursive: true });
  }

  async start() {
    this.cdp = await this.page.context().newCDPSession(this.page);
    this.cdp.on('Page.screencastFrame', async ({ data, metadata, sessionId }) => {
      const file = path.join(this.dir, `${String(this.frames.length).padStart(5, '0')}.jpg`);
      fs.writeFileSync(file, Buffer.from(data, 'base64'));
      this.frames.push({ t: metadata.timestamp, file });
      await this.cdp.send('Page.screencastFrameAck', { sessionId }).catch(() => {});
    });
    await this.cdp.send('Page.startScreencast', {
      format: 'jpeg',
      quality: 92,
      maxWidth: this.viewport.width * SCALE,
      maxHeight: this.viewport.height * SCALE,
    });
    this.t0 = Date.now() / 1000;
  }

  now() {
    return Date.now() / 1000;
  }

  mark(name, extra = {}) {
    this.events.push({ name, t: this.now(), ...extra });
  }

  async hold(ms) {
    await this.page.waitForTimeout(ms);
  }

  async tap(locator, name) {
    await locator.scrollIntoViewIfNeeded();
    const box = await locator.boundingBox();
    this.mark(name, { kind: 'tap', x: box.x + box.width / 2, y: box.y + box.height / 2 });
    await this.hold(350);
    await locator.click();
  }

  async type(text, name) {
    const input = this.page.locator('#message-input');
    await input.click();
    this.mark(name, { kind: 'type', text });
    await input.pressSequentially(text, { delay: TYPE_DELAY_MS });
    await this.hold(300);
    await this.page.keyboard.press('Enter');
  }

  /** Waits for the agent's reply; the wait itself is cut down in the output. */
  async reply(name) {
    const waitStart = this.now();
    await this.page.locator('.msg-bubble--typing').first().waitFor({ state: 'attached', timeout: 5_000 }).catch(() => {});
    await this.page.locator('.msg-bubble--typing').waitFor({ state: 'detached', timeout: 60_000 });
    const waitEnd = this.now();
    this.waits.push([waitStart + 0.25, waitEnd]);
    this.mark(name, { kind: 'reply' });
    await this.hold(1_800);
  }

  async stop() {
    this.stoppedAt = this.now();
    await this.cdp.send('Page.stopScreencast');
    await this.hold(200);
  }

  /** Maps a wall-clock time to the cut timeline (waits shortened to WAIT_KEEP_S). */
  cutTime(t) {
    let removed = 0;
    for (const [a, b] of this.waits) {
      const removable = Math.max(0, b - a - WAIT_KEEP_S);
      if (t >= b) removed += removable;
      else if (t > a + WAIT_KEEP_S) removed += t - (a + WAIT_KEEP_S);
    }
    return t - this.frames[0].t - removed;
  }

  encode() {
    const kept = this.frames.filter((f) => !this.waits.some(([a, b]) => f.t > a + WAIT_KEEP_S && f.t < b));
    const lines = [];
    kept.forEach((f, i) => {
      const next = kept[i + 1];
      const until = next ? next.t : this.stoppedAt;
      const duration = this.cutTime(until) - this.cutTime(f.t);
      lines.push(`file '${f.file}'`, `duration ${Math.max(duration, 0.001).toFixed(4)}`);
    });
    lines.push(`file '${kept.at(-1).file}'`);
    const list = path.join(this.dir, 'frames.txt');
    fs.writeFileSync(list, lines.join('\n'));
    fs.mkdirSync(OUT_DIR, { recursive: true });
    const out = path.join(OUT_DIR, `${this.clip}.mp4`);
    const outWidth = Math.min(1920, this.viewport.width * SCALE);
    const ffmpeg = spawnSync('ffmpeg', [
      '-loglevel', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', list,
      '-vf', `fps=${FPS},scale=${outWidth}:-2:flags=lanczos,format=yuv420p`,
      '-c:v', 'libx264', '-preset', 'slow', '-crf', '22', '-movflags', '+faststart', out,
    ]);
    if (ffmpeg.status !== 0) throw new Error(`ffmpeg failed for ${this.clip}: ${ffmpeg.stderr}`);

    const toVideoScale = outWidth / this.viewport.width;
    const events = this.events.map((e) => ({
      ...e,
      t: Number(this.cutTime(e.t).toFixed(3)),
      ...(e.x === undefined ? {} : { x: Math.round(e.x * toVideoScale), y: Math.round(e.y * toVideoScale) }),
    }));
    const duration = this.cutTime(this.stoppedAt);
    fs.mkdirSync(META_DIR, { recursive: true });
    fs.writeFileSync(
      path.join(META_DIR, `${this.clip}.json`),
      JSON.stringify({ clip: this.clip, width: outWidth, height: Math.round(this.viewport.height * toVideoScale), duration: Number(duration.toFixed(3)), events }, null, 1),
    );
    console.log(`${this.clip}: ${duration.toFixed(1)} s, ${kept.length} frames, ${this.waits.length} waits cut -> ${out}`);
  }
}

async function signIn(page) {
  await page.goto(BASE + '/');
  await page.getByRole('button', { name: 'Autocompletar' }).click();
  await page.getByRole('button', { name: 'Ingresar' }).click();
  await page.waitForURL(/\/chat\.html/, { timeout: 15_000 });
  await page.locator('.msg-bubble--agent').first().waitFor();
}

async function giveStatement(rec) {
  for (const [i, answer] of STATEMENT_ANSWERS.entries()) {
    if (await rec.page.getByText('Caso derivado').first().isVisible()) return;
    await rec.type(answer, `statement_${i + 1}`);
    await rec.reply(`statement_reply_${i + 1}`);
  }
}

async function showHandoff(rec) {
  await rec.tap(rec.page.locator('.persona-toggle button[data-view="internal"]'), 'internal_view');
  rec.mark('handoff_shown');
  await rec.hold(3_000);
}

/** The advisor's whole case file as one tall still, which the edit animates card by card. */
async function captureHandoff(page) {
  await page.setViewportSize(HANDOFF_VIEWPORT);
  await page.addStyleTag({
    content: '.app-frame { height: auto !important; max-height: none !important; } .case-panel { overflow: visible !important; }',
  });
  await page.waitForTimeout(500);
  const file = path.join(OUT_DIR, 'm3-handoff.png');
  await page.locator('.handoff-card').screenshot({ path: file });
  console.log(`m3: handoff still -> ${file}`);
}

const AFTER_CLIP = { m3: captureHandoff };

const CLIPS = {
  async m1(rec) {
    await rec.hold(800);
    await rec.type('No reconozco un cargo de 38.500 pesos del 14 de junio', 'report');
    await rec.reply('confirm_question');
    await rec.tap(rec.page.getByRole('button', { name: 'Sí, es ese' }), 'tap_yes');
    await rec.reply('explain_question');
    await rec.type('No uso Uber hace meses, tengo la tarjeta conmigo', 'explanation');
    await rec.reply('resolved');
    await rec.hold(2_500);
  },
  async m2(rec) {
    await rec.hold(800);
    await rec.type('Me cobraron dos veces un taxi de 27 mil', 'report');
    await rec.reply('two_charges');
    await rec.tap(rec.page.locator('.charge-option').first(), 'tap_charge');
    await rec.reply('explain_question');
    await rec.type('Tomé un solo taxi y me lo cobraron dos veces', 'explanation');
    await rec.reply('resolved');
    await rec.hold(2_500);
  },
  async m3(rec) {
    await rec.hold(800);
    await rec.type('No reconozco una compra en Tienda Online Global', 'report');
    await rec.reply('statement_question');
    await giveStatement(rec);
    rec.mark('escalated');
    await rec.hold(2_500);
    await showHandoff(rec);
  },
  async pt(rec) {
    await rec.hold(600);
    await rec.tap(rec.page.locator('.lang-toggle button[data-lang="pt"]'), 'tap_pt');
    await rec.hold(1_200);
    await rec.type('Não reconheço uma compra na Tienda Online Global', 'report');
    await rec.reply('statement_question');
    await rec.hold(2_500);
  },
};

async function recordClip(browser, clip) {
  const server = await startServer();
  try {
    const context = await browser.newContext({ viewport: VIEWPORT, deviceScaleFactor: SCALE, locale: 'es-CO' });
    const page = await context.newPage();
    await signIn(page);
    const rec = new Recorder(page, clip, VIEWPORT);
    await rec.start();
    await CLIPS[clip](rec);
    await rec.stop();
    rec.encode();
    await AFTER_CLIP[clip]?.(page);
    await context.close();
  } finally {
    await stopServer(server);
  }
}

const requested = process.argv.slice(2);
const clips = requested.length ? requested : Object.keys(CLIPS);
const browser = await chromium.launch();
try {
  for (const clip of clips) {
    if (!CLIPS[clip]) throw new Error(`unknown clip ${clip}; known: ${Object.keys(CLIPS).join(', ')}`);
    await recordClip(browser, clip);
  }
} finally {
  await browser.close();
}
