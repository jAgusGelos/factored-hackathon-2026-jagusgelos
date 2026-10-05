# Launch video

A 2:11 launch-style pitch for the LATAM Bank Dispute Agent: Why (the 37 h wait), What (three
moments of the real product), How (one trust beat), and the promise. Narration and shot list:
[`video-script.md`](video-script.md). Recording guide for the presenter: [`RECORDING.md`](RECORDING.md).

## Where the video is

The render is `video/out/launch.mp4` (1920x1080, 30 fps, H.264, audio mastered to -14 LUFS, about
65 MB, not committed), with `video/out/poster.jpg` as the thumbnail (the reveal frame, also baked
in as frame 0) and `video/out/share-copy.txt` for the post. Upload the MP4
(YouTube unlisted or Drive) and attach it to the release.

The committed copies of the finished renders live in [`docs/pitch/video/`](video/): `launch.mp4` with
`poster.jpg`, and the vertical teaser `teaser.mp4` with `teaser-poster.jpg`.

## Teaser

A 22 s vertical cut (composition `Teaser`, 1080x1920, 30 fps) for social posts, built from the same
recorded lines and components as the main video: the 37 h hook from frame 0 (`cold3`, MEASURED chip),
the product reveal (`reveal2`), three real app outcomes cut hard inside one screen frame (`close1`:
resolved with a provisional credit, the duplicate reversed, the hand-off to a person), and the end
card with the demo link and tagline (`close2`). Text stays out of the top 150 px and the bottom 250 px
that social apps cover. The footage window crops the app's chat column and plays the clips at 1.5x
from just before each outcome; the on-screen note says so.

```bash
cd video
npm run render:teaser                    # -> out/teaser-raw.mp4
npm run master:teaser                    # -> out/teaser.mp4 (-14 LUFS, frame 150 baked as frame 0) + out/teaser-poster.jpg
node scripts/stills.mjs --composition Teaser /tmp/teaser 15 60 330   # stills of the teaser
```

`master.sh` takes the poster path as an optional 4th argument; without it the poster still lands at
`out/poster.jpg`, so the launch video's poster is unchanged. Copy `out/teaser.mp4` and
`out/teaser-poster.jpg` into `docs/pitch/video/` after a re-render.

## How to render

```bash
cd video
npm ci                                   # Remotion 4.0.532, pinned
npx remotion studio src/index.ts         # live preview
npm run render                           # -> out/launch-raw.mp4
npm run master                           # -> out/launch.mp4 (-14 LUFS, poster as frame 0) + out/poster.jpg
                                         #    poster = frame 840 (the reveal); pass another frame as the 3rd argument if the timeline moves
node scripts/stills.mjs /tmp/stills 120 900 2400   # stills of chosen frames, for checking
```

The presenter cut-outs (`video/public/presenter/`, about 100 MB) and the raw takes (`video/takes/`)
are not committed, so a fresh clone cannot render the presenter until the takes are recorded and
ingested again (table below). Everything else the render needs is in git.

Rebuilding the inputs (only when they change), from the repo root:

| Input | Command | Needs |
|---|---|---|
| Narration text | edit `video/narration/lines.json` | |
| Placeholder voice + word timings | `<MoneyPrinterTurbo>/.venv/bin/python video/scripts/make_vo.py --mpt <MoneyPrinterTurbo>` | MoneyPrinterTurbo checkout (edge-tts, no key) |
| Presenter takes | `bash video/scripts/record_take.sh T05`, then `python3 video/scripts/ingest_takes.py` (voice cleanup with the room tone, word alignment, background removal) | webcam or phone files, numpy + onnxruntime; `video/takes/selection.json` pins a take |
| App footage | `node video/scripts/record.mjs m1 m2 m3 pt` | the app's `.env` (Anthropic key) and `data/` fixture |
| Teleprompter | `node video/scripts/build_teleprompter.mjs` | |

Every scene keys its animations to words of the narration (`src/timeline.ts`), so the edit
re-times itself to the presenter's real delivery whenever a take is re-ingested.

## What is real and what is illustrative

| Element | Status |
|---|---|
| App footage in the laptop (Uber resolution, duplicated taxi, policy hand-off, Portuguese) | **Real**: the local app with the real model (Claude Haiku 4.5), recorded with Playwright. The time spent waiting for the model is shortened in the edit, and to fit their beats the clips play at 1x (moment 01), 1.2x (moment 02) and 1.7x (moment 03 and the Portuguese clip); the footage says both on screen, and replies are never faked. |
| The advisor's case file in moment 03 | **Real**: a screenshot of the hand-off of the same recorded case. |
| English captions over the chat | Translations of what the Spanish/Portuguese UI shows. |
| The phone notification in the cold open | **Illustrative** (labelled on screen). The merchant and amount are the demo fixture's. |
| The 37 h clock | Motion graphic of a **MEASURED** number (demand report). |
| The trust diagram, the data slice | Motion graphics of the architecture (AD-2, AD-3, AD-11/13) and of the extraction manifest. |
| 0 / 48 unsafe outcomes, 18 / 18 resolved | **MEASURED** on a held-out set written before the first run, against the real Claude Haiku 4.5, 3 runs (`docs/eval/measured-eval.md`). Small set: the 95% upper bound on the unsafe rate is about 7%. |
| Presenter and voice | **Real**: the builder on camera, speaking every line, 17 takes recorded on 2026-10-05. Cut out of the room's background by a matting model; the voice is denoised and levelled, never altered. |

Every eval, demand and data number on screen comes from `video/src/facts.ts`, which names its source and
label. The amounts and dates in the chat captions and the cold-open notification are the demo fixture's.

## Tools and credits

- **Remotion** 4.0.532 (React video): the whole edit.
- **MoneyPrinterTurbo**: the placeholder voiceover used to time the edit before the takes existed
  (its edge-tts voice `en-US-AndrewNeural`), plus the Pexels search used to explore stock b-roll.
  Neither is in the final cut: the voice is the builder's.
- **Playwright** + Chromium screencast: the app footage.
- **Visual language**: ported from **pdoom-video by mexicat** (MIT, see `video/LICENSE.pdoom-engine`)
  as used in the "Motion as Code" kit: graph paper, plotter pen and spark, karaoke words, glow and
  grain. Recoloured with the app's own tokens. The word aligner in `ingest_takes.py` is ported from
  the same kit's `align_vo.py` (wav2vec2-base-960h, CTC). That script is the kit author's own and
  carries no licence of its own (the kit's MIT notice covers the pdoom-video engine); ours is a
  reimplementation of its method, not a copy.
- **Fonts**: Archivo, IBM Plex Mono, Cormorant Garamond (SIL Open Font License, `video/public/fonts/OFL.txt`).
- **Sound effects**: Kenney (CC0), `video/public/sfx/`.
- **Background removal**: Robust Video Matting (MobileNetV3, ONNX, GPL-3.0), downloaded at run time
  by `ingest_takes.py` and not redistributed.
- **Mastering**: ffmpeg two-pass `loudnorm` to -14 LUFS integrated, -1.5 dBTP (`video/scripts/master.sh`).
- **Music**: none.
