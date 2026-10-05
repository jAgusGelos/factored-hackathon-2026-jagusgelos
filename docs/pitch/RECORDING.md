# Recording the presenter takes

You record 17 short takes (T01 to T17) plus 10 seconds of room tone. Each take is one line of the
narration. The edit re-times every animation to your real delivery, so you do not need to match the
placeholder voice's timing: speak naturally.

Total recording time: about 45 minutes for 2-3 takes of each line.

## Before you start (5 minutes)

1. **Camera.** Open the camera's privacy shutter (a test capture at night came out black).
   Prefer a phone on a tripod at 1080p or 4K, 30 fps, landscape: the laptop webcam tops out at
   1280x720, which is soft for the full-presenter shots. The laptop webcam is fine as a fallback.
2. **Background.** Plain and uncluttered: a bare wall at least 1 m behind you, no window behind
   you, no shelves or posters. A wall that contrasts with your hair and clothes makes the cut-out
   cleaner (light wall if you wear dark clothes, and the other way round).
3. **Light.** Face a window or put a lamp at 45 degrees to one side, slightly above eye level.
   Never a light behind you. If one side of your face is dark, put a white sheet or paper on that
   side as a bounce. Daylight in the morning is ideal; switch off mixed-colour room lights.
4. **Sound.** The closest microphone wins: a headset or lapel mic beats the laptop mic. Quiet
   room, windows closed, fan and AC off, phone on silent. Soft furnishings (curtains, a bed, a sofa)
   reduce echo.
5. **Wardrobe.** A solid colour, no fine stripes or small checks (they shimmer on video), nothing
   the same colour as the wall. Avoid pure white and pure black. A navy or mid-grey top suits the
   video's colours.
6. **Teleprompter.** Open `video/teleprompter/index.html` (double-click; it works offline).
   Put it on the screen right next to or behind the camera so your eyes stay near the lens.

## Framing per placement

The teleprompter header shows each take's placement (from `video/narration/lines.json`); the table
below is a summary of it.

| Placement | Takes | Framing |
|---|---|---|
| Full presenter (cut out) | T01-T05, T16-T17 | Waist-up, camera at eye level, eyes about one third from the top of the frame, a hand's width of headroom. Stand or sit centred; leave room for your hands. |
| Picture-in-picture bubble | T06-T10 | Head and shoulders, centred. |
| Voice only | T11-T12 | Record on camera anyway (same framing as the bubble); the app fills the screen. |
| Beside the diagram | T13-T15 | Waist-up like the full presenter. You may gesture or point to your left (screen right), where the diagram appears. |

Keep the same position, framing and light for all takes of one placement.

## How to record each take

With the laptop webcam:

```bash
video/scripts/record_take.sh --check     # once: lists cameras, sizes and microphones
video/scripts/record_take.sh ROOMTONE    # 10 s of silence in the room, stops by itself
video/scripts/record_take.sh T01         # one take; press q in the terminal to stop
```

`AUDIO_SRC=<name>` picks another microphone and `VIDEO_DEV=/dev/videoN` another camera (`--check`
lists them; a node with no MJPEG mode is the camera's metadata or IR node, not a picture). Each run
saves `video/takes/<TAKE>_<n>.mkv` and never overwrites an earlier take. Press q in the terminal,
not in the preview window: closing the preview ends the take early.

For every take:

1. Press space in the teleprompter for the 3-2-1, or just read from the top.
2. **Stay silent for 1 second**, look into the lens, then say the line.
3. **Stay silent for 1 second** at the end, then stop.
4. Do 2-3 takes of each line. Vary them slightly (one calmer, one with more energy).
5. If you stumble, stop and start a new take; do not fix it mid-take.

No clap or spoken take id is needed: the file name carries the id and the sound is recorded in the
same file. Delivery tips: smile with the eyes on the cold open, slow down on the numbers
("thirty-seven hours"), pause on the commas, and land the last word of each line.

### With a phone

Record each take as its own clip, then copy the files into `video/takes/` named like the script
would: `T01_1.mp4`, `T01_2.mp4`, `T02_1.mov`, and so on, plus `ROOMTONE_1.mp4`. Keep the phone in
landscape and lock exposure and focus on your face (long-press on the face in the camera app).

## Choosing takes and handing over

By default the most recently modified file of each id is used, which is the newest take for
the script's recordings. A phone clip's file time is when it was copied, so after copying several
phone takes of one line, pick the one you want. To choose another one, write
`video/takes/selection.json`:

```json
{ "T01": 2, "T05": 1 }
```

Then tell Claude "the takes are in". The ingest step (`video/scripts/ingest_takes.py`, or
`--takes T15` for one re-recorded line) trims each take to its words, cleans the voice using the
room tone as the noise profile, levels it to about -15 LUFS, cuts you out of the background,
and re-times the whole video to your delivery.

## If a line needs a redo

Every line is final. If one take comes out badly, re-record only that line as a new take
(for example `T15_<n>`) and run the ingest for it alone (`--takes T15`).
