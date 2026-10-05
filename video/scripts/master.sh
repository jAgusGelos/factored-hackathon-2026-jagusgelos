#!/usr/bin/env bash
# Masters the Remotion render into the deliverable:
#   video/scripts/master.sh [raw.mp4] [out.mp4] [poster frame] [poster.jpg]
# - audio to -14 LUFS integrated, -1.5 dBTP (two-pass loudnorm, linear)
# - the poster frame saved as poster.jpg next to the output (or the 4th argument) and baked
#   over frame 0 (replaced, not added, so the audio stays in sync)
set -euo pipefail

VIDEO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RAW="${1:-$VIDEO_DIR/out/launch-raw.mp4}"
OUT="${2:-$VIDEO_DIR/out/launch.mp4}"
POSTER_FRAME="${3:-840}"
POSTER="${4:-$(dirname "$OUT")/poster.jpg}"
TARGET_LUFS=-14
TARGET_TP=-1.5
TARGET_LRA=11

[[ -f "$RAW" ]] || { echo "no render at $RAW (npm run render first)" >&2; exit 1; }

ffmpeg -loglevel error -y -i "$RAW" -vf "select=eq(n\\,$POSTER_FRAME)" -frames:v 1 -q:v 2 "$POSTER"

measured=$(ffmpeg -hide_banner -nostats -i "$RAW" -vn \
  -af "loudnorm=I=$TARGET_LUFS:TP=$TARGET_TP:LRA=$TARGET_LRA:print_format=json" -f null - 2>&1 \
  | sed -n '/^{/,/^}/p')
field() { echo "$measured" | sed -n "s/.*\"$1\" : \"\\([^\"]*\\)\".*/\\1/p"; }

ffmpeg -loglevel error -y -i "$RAW" -i "$POSTER" \
  -filter_complex "[0:v][1:v]overlay=enable='eq(n,0)'[v]" -map "[v]" -map 0:a \
  -c:v libx264 -preset slow -crf 18 -pix_fmt yuv420p -movflags +faststart \
  -af "loudnorm=I=$TARGET_LUFS:TP=$TARGET_TP:LRA=$TARGET_LRA:measured_I=$(field input_i):measured_TP=$(field input_tp):measured_LRA=$(field input_lra):measured_thresh=$(field input_thresh):offset=$(field target_offset):linear=true" \
  -ar 48000 -c:a aac -b:a 192k "$OUT"

echo "$OUT ($(du -h "$OUT" | cut -f1)), poster $POSTER"
