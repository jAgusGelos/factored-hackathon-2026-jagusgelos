// Renders docs/pitch/deck/index.html to one PNG per slide and to docs/pitch/deck.pdf: NODE_PATH=<dir with playwright> node export-deck.cjs
const path = require('path');
const { pathToFileURL } = require('url');
const { chromium } = require('playwright');

const PITCH_DIR = path.join(__dirname, '..');
const DECK_URL = pathToFileURL(path.join(PITCH_DIR, 'deck', 'index.html')).href;
const PNG_DIR = path.join(PITCH_DIR, 'deck', 'png');
const PDF_PATH = path.join(PITCH_DIR, 'deck.pdf');
const SLIDE = { width: 1280, height: 720 };
const DEVICE_SCALE_FACTOR = 2;

async function exportPngs(browser) {
  const page = await browser.newPage({ viewport: { width: 1400, height: 900 }, deviceScaleFactor: DEVICE_SCALE_FACTOR });
  await page.goto(DECK_URL, { waitUntil: 'networkidle' });
  const slides = page.locator('.slide');
  const count = await slides.count();
  for (let index = 0; index < count; index += 1) {
    const name = `slide-${index + 1}.png`;
    await slides.nth(index).screenshot({ path: path.join(PNG_DIR, name) });
    console.log('saved', name);
  }
  await page.close();
}

async function exportPdf(browser) {
  const page = await browser.newPage({ viewport: SLIDE });
  await page.goto(DECK_URL, { waitUntil: 'networkidle' });
  await page.emulateMedia({ media: 'print' });
  await page.pdf({
    path: PDF_PATH,
    width: `${SLIDE.width}px`,
    height: `${SLIDE.height}px`,
    printBackground: true,
    preferCSSPageSize: true,
  });
  console.log('saved', path.relative(PITCH_DIR, PDF_PATH));
  await page.close();
}

async function main() {
  const browser = await chromium.launch();
  try {
    await exportPngs(browser);
    await exportPdf(browser);
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
