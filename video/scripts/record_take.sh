#!/usr/bin/env bash
# Records one presenter take (webcam + microphone) for the launch video.
#
#   video/scripts/record_take.sh T05          # records video/takes/T05_<n>.mkv, n = highest existing + 1
#   video/scripts/record_take.sh ROOMTONE     # 10 s of the quiet room, for the noise profile
#   video/scripts/record_take.sh --check      # lists cameras, formats and microphones, records nothing
#
# A preview window opens; stop the take with q in this terminal (or Ctrl+C; both close the file
# cleanly). Settings come from the environment:
#   VIDEO_DEV   camera device         (default /dev/video0)
#   VIDEO_SIZE  capture size          (default: the largest MJPEG size the camera offers)
#   VIDEO_FPS   frames per second     (default 30)
#   AUDIO_SRC   PulseAudio/PipeWire source name (default "default"; see --check)
# Phone takes need no script: copy them into video/takes/ named <TAKE>_<n>.mp4 (or .mov).
set -euo pipefail

VIDEO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TAKES_DIR="$VIDEO_DIR/takes"
VIDEO_DEV="${VIDEO_DEV:-/dev/video0}"
VIDEO_FPS="${VIDEO_FPS:-30}"
AUDIO_SRC="${AUDIO_SRC:-default}"
ROOMTONE_SECONDS=10

largest_mjpeg_size() {
  { ffmpeg -hide_banner -f v4l2 -list_formats compressed -i "$VIDEO_DEV" 2>&1 \
    | grep -i mjpeg | grep -oE '[0-9]+x[0-9]+' \
    | awk -Fx '{ print $1 * $2, $0 }' | sort -n | tail -1 | cut -d' ' -f2; } || true
}

check_devices() {
  echo "Cameras:"
  for dev in /sys/class/video4linux/video*; do
    echo "  /dev/$(basename "$dev"): $(cat "$dev/name")"
  done
  echo
  echo "Formats of $VIDEO_DEV:"
  ffmpeg -hide_banner -f v4l2 -list_formats all -i "$VIDEO_DEV" 2>&1 | grep -E 'Compressed|Raw' | sed 's/^\[[^]]*\] */  /'
  echo
  echo "Microphones (use the name as AUDIO_SRC):"
  if command -v wpctl >/dev/null; then
    wpctl status | sed -n '/Sources:/,/Source endpoints:/p' | sed '1d;$d'
  fi
  echo
  echo "Default capture size: $(largest_mjpeg_size) @ ${VIDEO_FPS} fps"
}

# One above the highest existing number, never a deleted gap: the newest take keeps the top number.
next_take_path() {
  local take="$1" n=0 f k
  for f in "$TAKES_DIR/${take}"_*.*; do
    [[ -e "$f" ]] || continue
    k="${f##*/${take}_}"; k="${k%%.*}"
    [[ "$k" =~ ^[0-9]+$ ]] && (( 10#$k > n )) && n=$((10#$k))
  done
  echo "$TAKES_DIR/${take}_$((n + 1)).mkv"
}

if [[ "${1:-}" == "--check" ]]; then
  check_devices
  exit 0
fi
TAKE="${1:?usage: record_take.sh <TAKE_ID, e.g. T05 or ROOMTONE> | --check}"
if [[ ! "$TAKE" =~ ^(T[0-9]{2}|ROOMTONE)$ ]]; then
  echo "take id must look like T05 or ROOMTONE, got '$TAKE'" >&2
  exit 1
fi

VIDEO_SIZE="${VIDEO_SIZE:-$(largest_mjpeg_size)}"
if [[ -z "$VIDEO_SIZE" ]]; then
  echo "$VIDEO_DEV offers no MJPEG mode (it may be the camera's metadata or IR node)." >&2
  echo "Run 'record_take.sh --check' and pick another VIDEO_DEV." >&2
  exit 1
fi
mkdir -p "$TAKES_DIR"
OUT="$(next_take_path "$TAKE")"
LIMIT=()
[[ "$TAKE" == "ROOMTONE" ]] && LIMIT=(-t "$ROOMTONE_SECONDS")

echo "Recording $OUT ($VIDEO_SIZE @ ${VIDEO_FPS} fps, mic: $AUDIO_SRC)"
echo "Stay silent 1 s, say the line, stay silent 1 s, then press q HERE (not in the preview window)."

# Two outputs: the take (H.264 near-lossless + FLAC) and a small raw preview piped to ffplay.
ffmpeg -hide_banner -loglevel warning -stats \
  -f v4l2 -input_format mjpeg -video_size "$VIDEO_SIZE" -framerate "$VIDEO_FPS" -thread_queue_size 1024 -i "$VIDEO_DEV" \
  -f pulse -thread_queue_size 1024 -i "$AUDIO_SRC" \
  "${LIMIT[@]}" -map 0:v -map 1:a -c:v libx264 -preset veryfast -crf 16 -pix_fmt yuv420p -c:a flac -ac 1 "$OUT" \
  "${LIMIT[@]}" -map 0:v -vf "scale=640:-2,hflip" -c:v rawvideo -pix_fmt yuv420p -f nut pipe:1 \
  | ffplay -hide_banner -loglevel error -autoexit -window_title "take $TAKE (mirror preview)" -fflags nobuffer -f nut -i pipe:0 \
  || true

if [[ -s "$OUT" ]]; then
  echo "Saved $OUT ($(ffprobe -v error -show_entries format=duration -of csv=p=0 "$OUT" | cut -d. -f1) s)"
else
  echo "Nothing was recorded" >&2
  exit 1
fi
