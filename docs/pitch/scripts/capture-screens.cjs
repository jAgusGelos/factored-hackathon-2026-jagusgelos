// Captures the deck screenshots from a running local app: NODE_PATH=<dir with playwright> APP_URL=http://127.0.0.1:8765 node capture-screens.cjs
const path = require('path');
const { chromium, errors } = require('playwright');

const APP_URL = process.env.APP_URL ?? 'http://127.0.0.1:8000';
const OUT_DIR = path.join(__dirname, '..', 'deck', 'assets');
const VIEWPORT = { width: 1280, height: 800 };
const DEVICE_SCALE_FACTOR = 2;
const LOCALE = 'es-CO';
const SIGN_IN_TIMEOUT_MS = 15_000;
const BUSY_START_TIMEOUT_MS = 5_000;
const TURN_TIMEOUT_MS = 45_000;
const SETTLE_MS = 600;
const VIEW_SWITCH_MS = 800;
const MAX_STATEMENT_TURNS = 6;
const HANDOFF_BADGE_TEXT = 'Caso derivado';
// The case panel scrolls inside a fixed-height frame; unclip it so the whole case file fits one shot.
const UNCLIP_PANEL_CSS = '.app-frame, .app-body, .case-panel { height: auto !important; max-height: none !important; overflow: visible !important; }';

async function signIn(page) {
  await page.goto(APP_URL + '/');
  await page.getByRole('button', { name: 'Autocompletar' }).click();
  await page.getByRole('button', { name: 'Ingresar' }).click();
  await page.waitForURL(/\/chat\.html/, { timeout: SIGN_IN_TIMEOUT_MS });
  await page.waitForSelector('#chat-log');
}

async function waitForTurn(page) {
  await page.waitForFunction(
    () => document.querySelector('#chat-log').getAttribute('aria-busy') === 'false'
      && !document.querySelector('#send-btn').disabled,
    null,
    { timeout: TURN_TIMEOUT_MS },
  );
  await page.waitForTimeout(SETTLE_MS);
}

function ignoreMissedBusyFlag(error) {
  if (!(error instanceof errors.TimeoutError)) throw error;
}

async function send(page, text) {
  await page.fill('#message-input', text);
  await page.press('#message-input', 'Enter');
  await page.waitForFunction(
    () => document.querySelector('#chat-log').getAttribute('aria-busy') === 'true',
    null,
    { timeout: BUSY_START_TIMEOUT_MS },
  ).catch(ignoreMissedBusyFlag);
  await waitForTurn(page);
}

async function tap(page, name) {
  await page.getByRole('button', { name }).last().click();
  await waitForTurn(page);
}

async function shot(target, name) {
  await target.screenshot({ path: path.join(OUT_DIR, name) });
  console.log('saved', name);
}

async function withSignedInPage(browser, run) {
  const context = await browser.newContext({ viewport: VIEWPORT, deviceScaleFactor: DEVICE_SCALE_FACTOR, locale: LOCALE });
  const page = await context.newPage();
  try {
    await signIn(page);
    await run(page);
  } finally {
    await context.close();
  }
}

async function resolveScenario(page) {
  await send(page, 'No reconozco un cargo de 38.500 pesos del 14 de junio');
  await tap(page, 'Sí, es ese');
  await send(page, 'No uso Uber hace meses, tengo la tarjeta conmigo');
  await shot(page, 'screen-resolve.png');
}

async function askScenario(page) {
  await send(page, 'Me cobraron dos veces un taxi de 27 mil');
  await shot(page, 'screen-ask.png');
}

async function isHandedOff(page) {
  return (await page.getByText(HANDOFF_BADGE_TEXT).count()) > 0;
}

async function handoffScenario(page) {
  await send(page, 'No reconozco una compra en Tienda Online Global');
  await send(page, 'Nunca compré en Tienda Online Global y no conozco ese comercio. Tengo la tarjeta conmigo. Me di cuenta ayer por una alerta de la app.');
  for (let turn = 0; turn < MAX_STATEMENT_TURNS && !(await isHandedOff(page)); turn += 1) {
    await send(page, 'No, no veo otros cargos raros.');
  }
  if (!(await isHandedOff(page))) throw new Error(`handoff scenario did not reach "${HANDOFF_BADGE_TEXT}"`);
  await shot(page, 'screen-handoff-client.png');
  await page.locator('button[data-view="internal"]').click();
  await page.waitForTimeout(VIEW_SWITCH_MS);
  await shot(page, 'screen-handoff-internal.png');
  await page.addStyleTag({ content: UNCLIP_PANEL_CSS });
  await page.waitForTimeout(SETTLE_MS);
  await shot(page.locator('.handoff-card'), 'handoff-case-file.png');
}

async function main() {
  const browser = await chromium.launch();
  try {
    await withSignedInPage(browser, resolveScenario);
    await withSignedInPage(browser, askScenario);
    await withSignedInPage(browser, handoffScenario);
  } finally {
    await browser.close();
  }
}

function exitWithError(error) {
  console.error(error);
  process.exit(1);
}

main().catch(exitWithError);
