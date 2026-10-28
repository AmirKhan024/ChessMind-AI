"""Build the puzzle subset shipped with the app from the Lichess puzzle database (CC0).

    curl -A "ChessMindAI" -o ../data/puzzles/lichess_db_puzzle.csv.zst \
        https://database.lichess.org/lichess_db_puzzle.csv.zst
    uv run --group ml python -m ml.puzzles ../data/puzzles/lichess_db_puzzle.csv.zst

Keeps well-tested, popular puzzles: up to PER_BUCKET per (theme or opening, 200-point
rating band), for the themes `app.puzzles` maps weaknesses to and every opening family."""

import csv
import gzip
import io
import sys
from collections import Counter

import zstandard

from app.puzzles import ALL_THEMES, PUZZLE_FILE, opening_key

PER_BUCKET = 25
MIN_PLAYS = 500
MIN_POPULARITY = 80
MAX_RATING_DEV = 100
COLUMNS = ["PuzzleId", "FEN", "Moves", "Rating", "Popularity", "Themes", "OpeningTags"]


def main(path: str) -> None:
    wanted = set(ALL_THEMES)
    buckets: Counter = Counter()
    kept, seen = [], 0
    with open(path, "rb") as raw:
        text = io.TextIOWrapper(zstandard.ZstdDecompressor().stream_reader(raw), "utf-8")
        for r in csv.DictReader(text):
            seen += 1
            if (
                int(r["NbPlays"]) < MIN_PLAYS
                or int(r["Popularity"]) < MIN_POPULARITY
                or int(r["RatingDeviation"]) > MAX_RATING_DEV
            ):
                continue
            band = int(r["Rating"]) // 200
            # Opening tags go from family to variation; the first one is the family.
            tags = r["OpeningTags"].split()
            keys = [t for t in r["Themes"].split() if t in wanted]
            if tags:
                keys.append("opening:" + opening_key(tags[0]))
            open_keys = [k for k in keys if buckets[(k, band)] < PER_BUCKET]
            if not open_keys:
                continue
            for k in keys:
                buckets[(k, band)] += 1
            kept.append({c: r[c] for c in COLUMNS})
    PUZZLE_FILE.parent.mkdir(exist_ok=True)
    with gzip.open(PUZZLE_FILE, "wt", newline="") as f:
        w = csv.DictWriter(f, COLUMNS)
        w.writeheader()
        w.writerows(kept)
    print(f"read {seen} puzzles, kept {len(kept)} -> {PUZZLE_FILE}")


if __name__ == "__main__":
    main(sys.argv[1])
