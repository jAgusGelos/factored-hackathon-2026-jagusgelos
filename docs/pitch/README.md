# Pitch deck

A 6-slide, 16:9 investor-style pitch for the LATAM Bank dispute agent: Why → What → How →
Data & ML → Proof → What's next. About 60% product and 40% technical, and every technical slide
ends on the value it delivers.

| File | What it is |
|---|---|
| [`deck.pdf`](deck.pdf) | The deck as a PDF, one 1280x720 page per slide. Read this one. |
| [`deck/index.html`](deck/index.html) | Source: a single HTML file with inline CSS and no build step. Each slide is a `<section class="slide" id="slide-N">`. |
| [`deck/assets/`](deck/assets) | Real app screenshots (Spanish UI) and the architecture diagram (`architecture.svg`). |
| [`deck/png/`](deck/png) | One 2560x1440 PNG per slide, for the video. |
| [`deck-copy.md`](deck-copy.md) | The copy of every slide, with a `Source:` line per number. |
| [`scripts/`](scripts) | The screenshot and export scripts (Playwright). |

## Viewing it

Open `deck/index.html` in a browser: the slides stack vertically and scale to the window width.
Printing it from the browser gives one slide per page.

For the video: every slide is self-contained (no external fonts, scripts or network calls), so a
slide can be screenshotted from `deck/png/` or embedded from `deck/index.html#slide-N` as is.

## Rebuilding it

The scripts pin Playwright in [`package.json`](package.json) (1.63.0).

```bash
cd docs/pitch
npm ci && npx playwright install chromium

# 1. Screenshots (only when the app's UI changes). The app writes data/app.db; start from an
#    empty one, because the demo customer gets one automatic credit per 90 days.
uvicorn app.main:app --port 8765          # from the repo root
APP_URL=http://127.0.0.1:8765 npm run capture   # -> deck/assets/screen-*.png, handoff-case-file.png

# 2. PNGs and PDF from the HTML (the PDF page size comes from the deck's @page rule)
npm run export                            # -> deck/png/slide-1..6.png and deck.pdf
```

The `.shot` crops on slide 2 (`--iw`, `--s`, `--x`, `--y`) are in image pixels of these captures
(1280x800 at 2x, so 2560 wide). If the capture viewport or scale changes, re-tune them.

The screenshots run the README's scenarios against the real model (Claude Haiku 4.5), so the
agent's wording can change slightly from one capture to the next.

## Where every number comes from

Results carry an honesty label on the slide: MEASURED (computed from the dataset or timed on
real runs), SIMULATED (offline evaluation with a mocked model) or DESIGN ARGUMENT (a reasoned
choice, not a measurement). Plain counts of the repo itself (tests, contracts, fixture rows) and
the roadmap carry no label; the table says what they are. The dataset is the challenge's synthetic
LATAM Bank data, not a live bank's. Body text on the slides is 22px or larger; source footnotes,
labels and the top bar are smaller metadata.

| Slide | Number | Label | Source |
|---|---|---|---|
| 1 | 37 h median first response, p90 58 h, n = 7,567 of 12,297 "Cargo no reconocido" complaints | MEASURED | `docs/analysis/demand-report.md`, finding 3 |
| 1 | Why disputes: checkable in code against the customer's own ledger | DESIGN ARGUMENT | README "Demand analysis", closing paragraph |
| 2 | The three moments (resolve, ask, hand off); no numbers | Screenshots of the running app | README "The required scenarios, on one customer"; `scripts/capture-screens.cjs` |
| 3 | 128 combinations in the exhaustive sweep (the classifier never causes a credit) | Test result | README "Evaluation results"; `tests/test_policy_not_overridden.py` |
| 3 | 990 automated tests | Count | README "Running it end to end"; `pytest --collect-only` (990 collected, 2026-10-04) |
| 4 | 9 table contracts | Count | `etl/schema_contract.py` |
| 4 | 0 orphaned rows on both foreign keys checked from complaints (67,095 and 44,570 rows) | MEASURED | `data/lineage_manifest.json`, `quality_checks.foreign_keys` |
| 4 | 130,690 of 5,000,000 transactions, 30-day window 2026-05-18 to 2026-06-17; complaints 67,095 in full | MEASURED | `data/extraction_manifest.json`; `docs/challenge/challenge-brief.md` (5,000,000 rows); `etl/extract.py` (`DEFAULT_WINDOW_DAYS = 30`) |
| 4 | Fixture: 1 dataset customer and 8 labeled synthetic charges | Count | README "Demo data" and "Known limitations" |
| 4 | Why a subset: no S3 at runtime, reproducible, free-plan deploy, a dispute needs only the recent ledger | DESIGN ARGUMENT | `docs/architecture-decisions.md` AD-2 |
| 4 | Classifier split 11,543 / 2,037 (2026-01-06); macro-F1 baseline 0.1662, model 0.2448 (+0.0786) | MEASURED | `data/classifier_eval_report.json` |
| 5 | 0 of 40 unsafe outcomes | SIMULATED | `data/eval_report.json`, `unsafe_outcomes` |
| 5 | System comparison: hybrid 6 / 0 / 30, always escalate 0 / 0 / 30, no evidence check 6 / 4 / 26 | SIMULATED | `data/eval_report.json`, `system_comparison`; README "System-level comparison" |
| 5 | 3.2 s median resolving turn (1.3-6.6 s, 8 ES/PT runs); taps 0.05-0.14 s | MEASURED | README "Evaluation results", real Claude Haiku 4.5 latency (manual runs, 2026-09-30) |
| 6 | Roadmap: identity, managed database, monitoring, real traffic | Roadmap | README "Remaining production-deployment work" |

Slide 5 always shows its limit: the 40 cases are a constructed offline suite with a mocked model,
written by the policy's author, not a held-out workload. The only held-out evaluation is the
classifier's chronological split.
