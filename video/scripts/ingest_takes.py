"""Turn the presenter's raw takes into the edit's voice, cut-out and word timings.

    MPT=/home/agus/Escritorio/MoneyPrinterTurbo
    $MPT/.venv/bin/python video/scripts/ingest_takes.py                 # every take found
    $MPT/.venv/bin/python video/scripts/ingest_takes.py --only T05 T06  # just these
    $MPT/.venv/bin/python video/scripts/ingest_takes.py --no-matte      # skip background removal

Any Python with numpy and onnxruntime works; MoneyPrinterTurbo's venv has both.

For each take id in video/narration/lines.json that has a file in video/takes/ (the last
<TAKE>_<n>.* unless video/takes/selection.json picks another n):

1. Aligns the line's words to the audio (CTC forced alignment with wav2vec2-base-960h, ported
   from align_vo.py of the pdoom-video "Motion as Code" kit, MIT) and trims the take to the
   words plus a short pad.
2. Cleans the voice (high-pass, FFT denoise) and brings it to TARGET_LUFS (measured gain, limiter):
   video/public/voice/<id>.m4a.
3. Cuts the presenter out of the background with Robust Video Matting (ONNX, MobileNetV3):
   video/public/presenter/<id>.webm (VP9 with alpha), plus the trimmed plain take <id>.mp4 for
   the framed-card fallback.
4. Rewrites the line in video/src/meta/vo.json with the real duration and word timings, so every
   animation keyed to a word re-times itself. Lines without a take keep the TTS placeholder.
"""

import argparse
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

import numpy as np
import onnxruntime as ort

VIDEO = Path(__file__).resolve().parent.parent
LINES_JSON = VIDEO / "narration" / "lines.json"
VO_JSON = VIDEO / "src" / "meta" / "vo.json"
TAKES_DIR = VIDEO / "takes"
VOICE_DIR = VIDEO / "public" / "voice"
PRESENTER_DIR = VIDEO / "public" / "presenter"
MODEL_DIR = Path.home() / ".cache" / "launch-video-models"
KIT_MODELS = Path("/home/agus/Escritorio/motion-as-code-kit/Motion_as_kit/analysis/models")
W2V2_MODEL = KIT_MODELS / "w2v2_base_960h_q.onnx"
W2V2_VOCAB = KIT_MODELS / "vocab.json"
RVM_URL = "https://github.com/PeterL1n/RobustVideoMatting/releases/download/v1.0.0/rvm_mobilenetv3_fp32.onnx"
RVM_MODEL = MODEL_DIR / "rvm_mobilenetv3_fp32.onnx"

SR, HOP = 16000, 320
FRAME_S = HOP / SR
FPS = 30
HEAD_PAD_S, TAIL_PAD_S = 0.25, 0.35
TARGET_LUFS, TRUE_PEAK = -15.0, -1.5
VOICED_BELOW_PEAK_DB = 34
TAKE_EXTENSIONS = (".mkv", ".mp4", ".mov", ".m4v", ".webm")



def take_number(path: Path) -> int:
    return int(path.stem.rsplit("_", 1)[1])


def find_take(take: str, selection: dict) -> Path | None:
    candidates = [p for p in TAKES_DIR.glob(f"{take}_*") if p.suffix.lower() in TAKE_EXTENSIONS]
    candidates = [p for p in candidates if re.fullmatch(rf"{take}_\d+", p.stem)]
    if not candidates:
        return None
    if take in selection:
        chosen = [p for p in candidates if take_number(p) == selection[take]]
        if not chosen:
            sys.exit(f"selection.json picks {take}_{selection[take]}, which is not in {TAKES_DIR}")
        return chosen[0]
    return max(candidates, key=take_number)



def load_audio(path: Path, sr: int = SR) -> np.ndarray:
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"],
        capture_output=True, check=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.float32).copy()


def voiced_mask(y: np.ndarray) -> np.ndarray:
    """10 ms frames whose energy is within VOICED_BELOW_PEAK_DB of the take's loudest frames."""
    n = len(y) // 160
    frames = y[: n * 160].reshape(n, 160)
    db = 20 * np.log10(np.sqrt((frames ** 2).mean(1) + 1e-12) + 1e-9)
    return db > np.percentile(db, 99) - VOICED_BELOW_PEAK_DB


def spoken_units(word: str) -> list[str]:
    cleaned = word.replace("’", "'").replace("-", " ").upper()
    cleaned = re.sub(r"[^A-Z' ]", "", cleaned)
    return [part.strip("'") for part in cleaned.split() if part.strip("'")]


def emissions(session: ort.InferenceSession, y: np.ndarray) -> np.ndarray:
    x = (y - y.mean()) / np.sqrt(y.var() + 1e-7)
    logits = session.run(None, {session.get_inputs()[0].name: x[None].astype(np.float32)})[0][0]
    return logits - np.logaddexp.reduce(logits, axis=1, keepdims=True)


def viterbi(log_probs: np.ndarray, targets: list[int]) -> np.ndarray:
    """CTC forced alignment: the CTC state of every frame (odd states are target tokens)."""
    frames, states = log_probs.shape[0], 2 * len(targets) + 1
    labels = np.zeros(states, np.int64)
    labels[1::2] = targets
    neg = -1e30
    can_skip = np.zeros(states, bool)
    can_skip[3::2] = np.array(targets[1:]) != np.array(targets[:-1])
    score = np.full(states, neg)
    score[0], score[1] = log_probs[0, 0], log_probs[0, labels[1]]
    back = np.zeros((frames, states), np.int8)
    for t in range(1, frames):
        one = np.concatenate([[neg], score[:-1]])
        two = np.where(can_skip, np.concatenate([[neg, neg], score[:-2]]), neg)
        best = np.maximum(score, np.maximum(one, two))
        back[t] = np.where(best == score, 0, np.where(best == one, 1, 2))
        score = best + log_probs[t, labels]
    state = states - 1 if score[states - 1] >= score[states - 2] else states - 2
    path = np.zeros(frames, np.int64)
    for t in range(frames - 1, -1, -1):
        path[t] = state
        state -= int(back[t, state])
    return path


def align_words(session, vocab: dict, y: np.ndarray, text: str) -> list[dict]:
    """Word spans in seconds from the start of `y`, one per display word of `text`."""
    separator = vocab["|"]
    words = text.split()
    targets, token_positions = [], []
    for word in words:
        positions = []
        for unit in spoken_units(word):
            if targets:
                targets.append(separator)
            for char in unit:
                positions.append(len(targets))
                targets.append(vocab[char])
        token_positions.append(positions)
    path = viterbi(emissions(session, y), targets)
    frames_by_token: dict[int, list[int]] = {}
    for frame, state in enumerate(path):
        if state % 2 == 1:
            frames_by_token.setdefault((state - 1) // 2, []).append(frame)

    spans = []
    for word, positions in zip(words, token_positions):
        frames = [f for p in positions for f in frames_by_token.get(p, [])]
        if frames:
            spans.append({"text": word, "start": min(frames) * FRAME_S, "end": (max(frames) + 1) * FRAME_S})
        else:
            previous_end = spans[-1]["end"] if spans else 0.0
            spans.append({"text": word, "start": previous_end, "end": previous_end})
    return extend_to_voicing(spans, voiced_mask(y), len(y) / SR)


def extend_to_voicing(spans: list[dict], voiced: np.ndarray, duration: float) -> list[dict]:
    """A word ends where the voice stops, not on its last CTC spike (never into the next word)."""
    for i, span in enumerate(spans):
        limit = spans[i + 1]["start"] if i + 1 < len(spans) else duration
        j = int(span["end"] * 100)
        while j < min(len(voiced), int(limit * 100)) and voiced[j]:
            j += 1
        span["end"] = min(max(span["end"], j / 100), limit)
    return spans


def speech_window(y: np.ndarray) -> tuple[float, float]:
    voiced = np.flatnonzero(voiced_mask(y))
    if not len(voiced):
        sys.exit("take has no audible speech")
    return voiced[0] / 100, (voiced[-1] + 1) / 100



def run(cmd: list[str]) -> str:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        sys.exit(f"command failed: {' '.join(cmd[:6])}...\n{result.stderr[-2000:]}")
    return result.stderr


def integrated_loudness(src: Path, start: float, end: float, filters: str) -> float:
    report = run(["ffmpeg", "-hide_banner", "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(src), "-vn", "-ac", "1",
                  "-af", f"{filters},ebur128", "-f", "null", "-"])
    return float(re.findall(r"I:\s+(-?[\d.]+) LUFS", report)[-1])


def clean_voice(src: Path, start: float, end: float, out: Path) -> None:
    """A measured gain plus a true-peak limiter: loudnorm does not converge on clips this short."""
    cleanup = "highpass=f=80,afftdn=nf=-25"
    gain = TARGET_LUFS - integrated_loudness(src, start, end, cleanup)
    limit = 10 ** (TRUE_PEAK / 20)
    run(["ffmpeg", "-hide_banner", "-y", "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(src), "-vn", "-ac", "1",
         "-af", f"{cleanup},volume={gain:.2f}dB,alimiter=limit={limit:.3f}:level=false", "-ar", "48000",
         "-c:a", "aac", "-b:a", "192k", str(out)])


def trim_plain(src: Path, start: float, end: float, out: Path) -> None:
    run(["ffmpeg", "-hide_banner", "-y", "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(src), "-an",
         "-vf", f"fps={FPS}", "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", str(out)])


def video_size(path: Path) -> tuple[int, int]:
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
         "-of", "csv=p=0:s=x", str(path)], text=True)
    width, height = out.strip().split("x")
    return int(width), int(height)


def rvm_session() -> ort.InferenceSession:
    if not RVM_MODEL.exists():
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        print(f"downloading {RVM_URL}")
        urllib.request.urlretrieve(RVM_URL, RVM_MODEL)
    return ort.InferenceSession(str(RVM_MODEL), providers=["CPUExecutionProvider"])


def matte(session: ort.InferenceSession, plain: Path, out: Path) -> None:
    """Robust Video Matting over the trimmed take; its recurrent state keeps hair edges stable."""
    width, height = video_size(plain)
    downsample = np.array([min(1.0, 512 / max(width, height))], np.float32)
    reader = subprocess.Popen(["ffmpeg", "-v", "error", "-i", str(plain), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                              stdout=subprocess.PIPE)
    writer = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{width}x{height}", "-r", str(FPS),
         "-i", "-", "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-b:v", "0", "-crf", "30", "-row-mt", "1",
         "-auto-alt-ref", "0", str(out)],
        stdin=subprocess.PIPE,
    )
    state = [np.zeros((1, 1, 1, 1), np.float32)] * 4
    frame_bytes = width * height * 3
    while chunk := reader.stdout.read(frame_bytes):
        if len(chunk) < frame_bytes:
            break
        rgb = np.frombuffer(chunk, np.uint8).reshape(height, width, 3)
        src = (rgb.astype(np.float32) / 255).transpose(2, 0, 1)[None]
        fgr, pha, *state = session.run(None, {"src": src, "r1i": state[0], "r2i": state[1], "r3i": state[2],
                                              "r4i": state[3], "downsample_ratio": downsample})
        rgba = np.concatenate([fgr[0], pha[0]], axis=0).transpose(1, 2, 0)
        writer.stdin.write((np.clip(rgba, 0, 1) * 255).astype(np.uint8).tobytes())
    writer.stdin.close()
    if reader.wait() != 0 or writer.wait() != 0:
        sys.exit(f"matting failed for {plain}")



def ingest(line: dict, take_path: Path, w2v2, vocab: dict, rvm) -> dict:
    y = load_audio(take_path)
    duration = len(y) / SR
    first, last = speech_window(y)
    window_start = max(0.0, first - 0.4)
    window_end = min(duration, last + 0.4)
    words = align_words(w2v2, vocab, y[int(window_start * SR): int(window_end * SR)], line["text"])
    start = max(0.0, window_start + words[0]["start"] - HEAD_PAD_S)
    end = min(duration, window_start + words[-1]["end"] + TAIL_PAD_S)

    VOICE_DIR.mkdir(parents=True, exist_ok=True)
    PRESENTER_DIR.mkdir(parents=True, exist_ok=True)
    voice = VOICE_DIR / f"{line['id']}.m4a"
    clean_voice(take_path, start, end, voice)
    plain = PRESENTER_DIR / f"{line['id']}.mp4"
    trim_plain(take_path, start, end, plain)
    presenter = {"plain": f"presenter/{plain.name}"}
    if rvm is not None:
        matted = PRESENTER_DIR / f"{line['id']}.webm"
        matte(rvm, plain, matted)
        presenter["matte"] = f"presenter/{matted.name}"

    offset = window_start - start
    return {
        **line,
        "source": "presenter",
        "take_file": take_path.name,
        "file": f"voice/{voice.name}",
        "duration": round(end - start, 3),
        "words": [{"text": w["text"], "start": round(w["start"] + offset, 3), "end": round(w["end"] + offset, 3)}
                  for w in words],
        "presenter": presenter,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", nargs="*", help="take ids to ingest (default: every take found)")
    parser.add_argument("--no-matte", action="store_true", help="skip background removal")
    parser.add_argument("--takes-dir", type=Path, help="read takes from another folder (tests)")
    args = parser.parse_args()

    global TAKES_DIR
    TAKES_DIR = args.takes_dir or TAKES_DIR
    selection_file = TAKES_DIR / "selection.json"
    selection = json.loads(selection_file.read_text()) if selection_file.exists() else {}
    narration = json.loads(LINES_JSON.read_text(encoding="utf-8"))
    vo = json.loads(VO_JSON.read_text(encoding="utf-8"))
    vo_by_id = {line["id"]: line for line in vo["lines"]}

    w2v2 = ort.InferenceSession(str(W2V2_MODEL), providers=["CPUExecutionProvider"])
    vocab = json.loads(W2V2_VOCAB.read_text())
    rvm = None if args.no_matte else rvm_session()

    for line in narration["lines"]:
        if args.only and line["take"] not in args.only:
            continue
        take_path = find_take(line["take"], selection)
        if take_path is None:
            print(f"{line['take']} {line['id']}: no take yet, keeping the placeholder")
            continue
        vo_by_id[line["id"]] = ingest(line, take_path, w2v2, vocab, rvm)
        ingested = vo_by_id[line["id"]]
        print(f"{line['take']} {line['id']}: {take_path.name} -> {ingested['duration']:.2f} s, "
              f"words {ingested['words'][0]['start']:.2f}-{ingested['words'][-1]['end']:.2f}")

    vo["lines"] = [vo_by_id[line["id"]] for line in narration["lines"]]
    VO_JSON.write_text(json.dumps(vo, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"updated {VO_JSON}")


if __name__ == "__main__":
    main()
