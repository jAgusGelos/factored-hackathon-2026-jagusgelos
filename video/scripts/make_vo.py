"""Generate the voiceover with MoneyPrinterTurbo's TTS (edge-tts voices) and word timings.

Run with MoneyPrinterTurbo's own interpreter so its services import cleanly:

    MPT=/home/agus/Escritorio/MoneyPrinterTurbo
    $MPT/.venv/bin/python video/scripts/make_vo.py --mpt $MPT

Writes one MP3 per narration line to video/public/vo/<id>.mp3, an SRT per line next to it
(MoneyPrinterTurbo's subtitle aligner), and video/src/data/vo.json with each line's duration
and word-level timings (seconds) for the captions in Remotion. No API key is needed: the
edge-tts voices are free.
"""

import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
VIDEO = os.path.dirname(HERE)


def duration(path: str) -> float:
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path]
    )
    return float(out.strip())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mpt", required=True, help="MoneyPrinterTurbo checkout")
    parser.add_argument("--only", nargs="*", help="regenerate only these line ids")
    args = parser.parse_args()

    os.chdir(args.mpt)  # its config loader reads ./config.toml
    sys.path.insert(0, args.mpt)
    from app.services import voice  # noqa: E402

    spec = json.load(open(os.path.join(VIDEO, "narration", "lines.json")))
    out_json = os.path.join(VIDEO, "src", "data", "vo.json")
    previous = {}
    if os.path.exists(out_json):
        previous = {line["id"]: line for line in json.load(open(out_json))["lines"]}

    say = spec.get("say", {})
    unsay = {said: written for written, said in say.items()}
    result = []
    for line in spec["lines"]:
        if args.only and line["id"] not in args.only and line["id"] in previous:
            result.append(previous[line["id"]])
            continue
        mp3 = os.path.join(VIDEO, "public", "vo", f"{line['id']}.mp3")
        # "say" respells words the voice mispronounces ("LATAM" came out as "Latin");
        # captions keep the written form.
        spoken = line["text"]
        for written, said in say.items():
            spoken = spoken.replace(written, said)
        sub_maker = voice.tts(spoken, spec["voice"], spec["rate"], mp3)
        if sub_maker is None:
            sys.exit(f"TTS failed for {line['id']}")
        srt = mp3.replace(".mp3", ".srt")
        voice.create_subtitle(sub_maker, spoken, srt)
        if os.path.exists(srt):
            text = open(srt).read()
            for said, written in unsay.items():
                text = text.replace(said, written)
            open(srt, "w").write(text)
        words = [
            {
                "text": unsay.get(cue.content, cue.content),
                "start": round(cue.start.total_seconds(), 3),
                "end": round(cue.end.total_seconds(), 3),
            }
            for cue in sub_maker.cues
        ]
        result.append(
            {**line, "file": f"vo/{line['id']}.mp3", "duration": round(duration(mp3), 3), "words": words}
        )
        print(f"{line['id']}: {result[-1]['duration']:.2f}s, {len(words)} words")

    os.makedirs(os.path.dirname(out_json), exist_ok=True)
    json.dump({"voice": spec["voice"], "rate": spec["rate"], "lines": result}, open(out_json, "w"), indent=1)
    print(f"total speech: {sum(line['duration'] for line in result):.1f}s")


if __name__ == "__main__":
    main()
