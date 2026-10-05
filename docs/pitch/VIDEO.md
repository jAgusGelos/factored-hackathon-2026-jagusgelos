# Launch video

A 2:11 launch-style pitch for the LATAM Bank Dispute Agent: Why (the 37 h wait), What (three
moments of the real product), How (one trust beat), and the promise. Narration and shot list:
[`video-script.md`](video-script.md). Recording guide for the presenter: [`RECORDING.md`](RECORDING.md).

## Where the video is

The render is `video/out/launch.mp4` (1920x1080, 30 fps, H.264, not committed), with
`video/out/poster.jpg` as the thumbnail and `video/out/share-copy.txt` for the post. Upload the MP4
(YouTube unlisted or Drive) and attach it to the release.

## How to render

```bash
cd video
npm ci                                   # Remotion 4.0.532, pinned
npx remotion studio src/index.ts         # live preview
npm run render                           # -> out/launch.mp4
node scripts/stills.mjs /tmp/stills 120 900 2400   # stills of chosen frames, for checking
```

Rebuilding the inputs (only when they change):

| Input | Command | Needs |
|---|---|---|
| Narration text | edit `video/narration/lines.json` | |
| Placeholder voice + word timings | `video/scripts/make_vo.py --mpt <MoneyPrinterTurbo>` | MoneyPrinterTurbo checkout (edge-tts, no key) |
| Presenter takes | `video/scripts/record_take.sh T05`, then `video/scripts/ingest_takes.py` | webcam or phone files, numpy + onnxruntime |
| App footage | `node video/scripts/record.mjs m1 m2 m3 pt` | the app's `.env` (Anthropic key) and `data/` fixture |
| Teleprompter | `node video/scripts/build_teleprompter.mjs` | |

Every scene keys its animations to words of the narration (`src/timeline.ts`), so when the
presenter's takes replace the placeholder voice, the whole edit re-times itself.

## What is real and what is illustrative

| Element | Status |
|---|---|
| App footage in the laptop (Uber resolution, duplicated taxi, policy hand-off, Portuguese) | **Real**: the local app with the real model (Claude Haiku 4.5), recorded with Playwright. The time spent waiting for the model is shortened in the edit (the footage says so on screen); replies are never faked. |
| The advisor's case file in moment 03 | **Real**: a screenshot of the hand-off of the same recorded case. |
| English captions over the chat | Translations of what the Spanish/Portuguese UI shows. |
| The phone notification in the cold open | **Illustrative** (labelled on screen). The merchant and amount are the demo fixture's. |
| The 37 h clock | Motion graphic of a **MEASURED** number (demand report). |
| The trust diagram, the data slice | Motion graphics of the architecture (AD-2, AD-3, AD-11/13) and of the extraction manifest. |
| 0 / 48 unsafe outcomes, 18 / 18 resolved | **MEASURED** on a held-out set written before the first run, against the real Claude Haiku 4.5, 3 runs (`docs/eval/measured-eval.md`). Small set: the 95% upper bound on the unsafe rate is about 7%. |
| Presenter | The builder on camera. Until the takes are recorded, a placeholder silhouette holds the slot. |

Every number on screen comes from `video/src/facts.ts`, which names its source and label.

## Tools and credits

- **Remotion** 4.0.532 (React video): the whole edit.
- **MoneyPrinterTurbo**: the placeholder voiceover (its edge-tts voice `en-US-AndrewNeural`) and
  subtitle alignment, plus the Pexels search used to explore stock b-roll (not used in the final cut).
- **Playwright** + Chromium screencast: the app footage.
- **Visual language**: ported from **pdoom-video by mexicat** (MIT, see `video/LICENSE.pdoom-engine`)
  as used in the "Motion as Code" kit: graph paper, plotter pen and spark, karaoke words, glow and
  grain. Recoloured with the app's own tokens. The word aligner in `ingest_takes.py` is ported from
  the same kit's `align_vo.py` (wav2vec2-base-960h, CTC).
- **Fonts**: Archivo, IBM Plex Mono, Cormorant Garamond (SIL Open Font License, `video/public/fonts/OFL.txt`).
- **Sound effects**: Kenney (CC0), `video/public/sfx/`.
- **Background removal**: Robust Video Matting (MobileNetV3, ONNX).
