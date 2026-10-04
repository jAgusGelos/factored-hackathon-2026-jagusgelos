"""Generate the voiceover with MoneyPrinterTurbo's TTS (edge-tts voices) and word timings.

Run with MoneyPrinterTurbo's own interpreter so its services import cleanly:

    MPT=/home/agus/Escritorio/MoneyPrinterTurbo
    $MPT/.venv/bin/python video/scripts/make_vo.py --mpt $MPT [--only how2 close1]

Writes one MP3 per narration line to video/public/vo/<id>.mp3, an SRT per line next to it
(MoneyPrinterTurbo's subtitle aligner), and video/src/meta/vo.json with each line's duration
and word-level timings (seconds) for the captions in Remotion. No API key is needed: the
edge-tts voices are free.
"""

import json
import os
import subprocess
import sys

from _mpt import VIDEO, attach, parser

LINES_JSON = os.path.join(VIDEO, "narration", "lines.json")
VO_JSON = os.path.join(VIDEO, "src", "meta", "vo.json")
VO_DIR = os.path.join(VIDEO, "public", "vo")


def read_json(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path: str, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)


def duration(path: str) -> float:
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path]
    )
    return float(out.strip())


def replace_all(text: str, replacements: dict) -> str:
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


def write_srt(voice, sub_maker, spoken: str, srt: str, unsay: dict) -> None:
    if os.path.exists(srt):
        os.remove(srt)
    voice.create_subtitle(sub_maker, spoken, srt)
    if not os.path.exists(srt):
        sys.exit(f"SRT alignment failed for {srt}")
    with open(srt, encoding="utf-8") as f:
        text = f.read()
    with open(srt, "w", encoding="utf-8") as f:
        f.write(replace_all(text, unsay))


def word_timings(sub_maker, unsay: dict) -> list:
    return [
        {
            "text": unsay.get(cue.content, cue.content),
            "start": round(cue.start.total_seconds(), 3),
            "end": round(cue.end.total_seconds(), 3),
        }
        for cue in sub_maker.cues
    ]


def render_line(voice, line: dict, spec: dict) -> dict:
    # "say" respells words the voice mispronounces ("LATAM" came out as "Latin");
    # the captions and the SRT keep the written form.
    say = spec.get("say", {})
    unsay = {said: written for written, said in say.items()}
    spoken = replace_all(line["text"], say)
    mp3 = os.path.join(VO_DIR, f"{line['id']}.mp3")
    sub_maker = voice.tts(spoken, spec["voice"], spec["rate"], mp3)
    if sub_maker is None:
        sys.exit(f"TTS failed for {line['id']}")
    write_srt(voice, sub_maker, spoken, mp3.replace(".mp3", ".srt"), unsay)
    return {
        **line,
        "file": f"vo/{line['id']}.mp3",
        "duration": round(duration(mp3), 3),
        "words": word_timings(sub_maker, unsay),
    }


def main() -> None:
    args_parser = parser(__doc__)
    args_parser.add_argument("--only", nargs="*", help="regenerate only these line ids")
    args = args_parser.parse_args()
    attach(args.mpt)
    from app.services import voice

    spec = read_json(LINES_JSON)
    previous = {}
    if os.path.exists(VO_JSON):
        previous = {line["id"]: line for line in read_json(VO_JSON)["lines"]}

    result = []
    for line in spec["lines"]:
        keep_previous = args.only and line["id"] not in args.only and line["id"] in previous
        rendered = previous[line["id"]] if keep_previous else render_line(voice, line, spec)
        result.append(rendered)
        print(f"{line['id']}: {rendered['duration']:.2f}s, {len(rendered['words'])} words")

    os.makedirs(os.path.dirname(VO_JSON), exist_ok=True)
    write_json(VO_JSON, {"voice": spec["voice"], "rate": spec["rate"], "lines": result})
    print(f"total speech: {sum(line['duration'] for line in result):.1f}s")


if __name__ == "__main__":
    main()
