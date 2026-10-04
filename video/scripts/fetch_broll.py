"""Fetch the cold open's stock b-roll through MoneyPrinterTurbo's Pexels search.

    MPT=/home/agus/Escritorio/MoneyPrinterTurbo
    $MPT/.venv/bin/python video/scripts/fetch_broll.py --mpt $MPT            # download the chosen clips
    $MPT/.venv/bin/python video/scripts/fetch_broll.py --mpt $MPT --search "phone at night"

The Pexels key lives in MoneyPrinterTurbo's config.toml (never in this repo). The chosen clips
are listed in video/broll.json by URL, so a fresh checkout downloads the same footage into
video/public/broll/ (gitignored: stock video is large and licensed by Pexels, not by us).
Search candidates and the download cache go to video/recordings/broll/, outside public/.
"""

import json
import os
import shutil
import sys

from _mpt import VIDEO, attach, parser

BROLL_JSON = os.path.join(VIDEO, "broll.json")
OUT_DIR = os.path.join(VIDEO, "public", "broll")
CACHE_DIR = os.path.join(VIDEO, "recordings", "broll")
MIN_CLIP_SECONDS = 5
MAX_CANDIDATES = 8


def search(material, query: str) -> None:
    from app.models.schema import VideoAspect

    items = material.search_videos_pexels(query, minimum_duration=MIN_CLIP_SECONDS, video_aspect=VideoAspect.landscape)
    for i, item in enumerate(items[:MAX_CANDIDATES]):
        path = material.save_video(item.url, os.path.join(CACHE_DIR, "candidates"))
        print(i, item.duration, item.url, path or "DOWNLOAD FAILED")


def fetch_chosen(material) -> None:
    with open(BROLL_JSON, encoding="utf-8") as f:
        clips = json.load(f)["clips"]
    os.makedirs(OUT_DIR, exist_ok=True)
    for clip in clips:
        target = os.path.join(OUT_DIR, clip["file"])
        if os.path.exists(target):
            continue
        path = material.save_video(clip["url"], os.path.join(CACHE_DIR, "cache"))
        if not path:
            sys.exit(f"download failed: {clip['file']} from {clip['url']}")
        shutil.move(path, target)
        print("saved", target)


def main() -> None:
    args_parser = parser(__doc__)
    args_parser.add_argument("--search", help="list candidate clips for a query instead")
    args = args_parser.parse_args()
    attach(args.mpt)
    from app.services import material

    if args.search:
        search(material, args.search)
    else:
        fetch_chosen(material)


if __name__ == "__main__":
    main()
