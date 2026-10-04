"""Fetch the cold open's stock b-roll through MoneyPrinterTurbo's Pexels search.

    MPT=/home/agus/Escritorio/MoneyPrinterTurbo
    $MPT/.venv/bin/python video/scripts/fetch_broll.py --mpt $MPT            # download the chosen clips
    $MPT/.venv/bin/python video/scripts/fetch_broll.py --mpt $MPT --search "phone at night"

The Pexels key lives in MoneyPrinterTurbo's config.toml (never in this repo). The chosen clips
are listed in video/broll.json by URL, so a fresh checkout downloads the same footage into
video/public/broll/ (gitignored: stock video is large and licensed by Pexels, not by us).
"""

import argparse
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
VIDEO = os.path.dirname(HERE)
OUT = os.path.join(VIDEO, "public", "broll")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mpt", required=True)
    parser.add_argument("--search", help="list candidate clips for a query instead")
    args = parser.parse_args()

    os.chdir(args.mpt)
    sys.path.insert(0, args.mpt)
    from app.models.schema import VideoAspect  # noqa: E402
    from app.services import material  # noqa: E402

    os.makedirs(OUT, exist_ok=True)
    if args.search:
        items = material.search_videos_pexels(args.search, 5, VideoAspect.landscape)
        for i, item in enumerate(items[:8]):
            path = material.save_video(item.url, os.path.join(OUT, "candidates"))
            print(i, item.duration, item.url, path)
        return

    for clip in json.load(open(os.path.join(VIDEO, "broll.json")))["clips"]:
        target = os.path.join(OUT, clip["file"])
        if os.path.exists(target):
            continue
        path = material.save_video(clip["url"], os.path.join(OUT, "cache"))
        shutil.copy(path, target)
        print("saved", target)


if __name__ == "__main__":
    main()
