"""Puzzle drills for a weakness, from a subset of the Lichess puzzle database (CC0) built by
`ml/puzzles.py`. A weakness slice maps to puzzle themes by a fixed, hand-written table: the
puzzle file tags themes and openings, not the clock or the piece the player moved, so the
mapping is a practice suggestion, not a measured link."""

import csv
import gzip
import re
from functools import cache
from pathlib import Path

PUZZLE_FILE = Path(__file__).parent.parent / "puzzles" / "puzzles.csv.gz"
PER_WEAKNESS = 5

PHASE_THEMES = {"opening": ["opening"], "middlegame": ["middlegame"], "endgame": ["endgame"]}
# Fast clocks: quick pattern spotting. Lots of time: longer calculation.
CLOCK_THEMES = {
    "under 10s": ["short", "hangingPiece"],
    "10-30s": ["short", "hangingPiece"],
    "30-60s": ["long"],
    "over 60s": ["long"],
}
SPEED_THEMES = {
    "bullet": ["short", "hangingPiece"],
    "blitz": ["short"],
    "rapid": ["long"],
    "classical": ["long", "veryLong"],
}
PIECE_THEMES = {
    "P": ["pawnEndgame", "advancedPawn"],
    "N": ["knightEndgame", "fork"],
    "B": ["bishopEndgame", "pin"],
    "R": ["rookEndgame"],
    "Q": ["queenEndgame", "trappedPiece"],
    "K": ["exposedKing"],
}
# Every theme the subset keeps puzzles for.
ALL_THEMES = sorted(
    {
        t
        for m in (PHASE_THEMES, CLOCK_THEMES, SPEED_THEMES, PIECE_THEMES)
        for v in m.values()
        for t in v
    }
)


def opening_key(name: str) -> str:
    """'King's Pawn Game' and the puzzle tag 'Kings_Pawn_Game' both become 'kingspawngame'."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def themes_for(family: str, value: str) -> tuple[list[str], str | None]:
    """(acceptable themes, opening key). A puzzle matches if it has any of the themes; for
    time-left/phase slices it must match the phase and preferably the clock."""
    if family == "phase":
        return PHASE_THEMES.get(value, []), None
    if family == "clock":
        return CLOCK_THEMES.get(value, []), None
    if family == "speed":
        return SPEED_THEMES.get(value, []), None
    if family == "piece":
        return PIECE_THEMES.get(value, []), None
    if family == "opening":
        return [], opening_key(value)
    if family == "clock_phase":
        return PHASE_THEMES.get(value.split(" / ")[1], []), None
    return [], None


PIECE_NAMES = {"P": "pawn", "N": "knight", "B": "bishop", "R": "rook", "Q": "queen", "K": "king"}


def display_label(family: str, value: str, label: str) -> str:
    """Stored slice label with piece letters spelled out, for headings."""
    return f"piece moved: {PIECE_NAMES[value]}" if family == "piece" else label


@cache
def load() -> list[dict]:
    if not PUZZLE_FILE.exists():
        return []
    with gzip.open(PUZZLE_FILE, "rt", newline="") as f:
        return [
            {
                "id": r["PuzzleId"],
                "fen": r["FEN"],
                "moves": r["Moves"].split(),
                "rating": int(r["Rating"]),
                "popularity": int(r["Popularity"]),
                "themes": set(r["Themes"].split()),
                "openings": {opening_key(t) for t in r["OpeningTags"].split()},
            }
            for r in csv.DictReader(f)
        ]


def drills(
    family: str, value: str, rating: int | None, n: int = PER_WEAKNESS, exclude=frozenset()
) -> dict:
    """Puzzles for one weakness slice near the player's rating, most popular first.
    `exclude`: puzzle ids already shown for another weakness."""
    themes, opening = themes_for(family, value)
    preferred = set()
    if family == "clock_phase":
        preferred = set(CLOCK_THEMES.get(value.split(" / ")[0], []))
    if opening:
        pool = [p for p in load() if opening in p["openings"]]
    else:
        pool = [p for p in load() if p["themes"] & set(themes)]
    pool = [p for p in pool if p["id"] not in exclude]
    target = rating or 1500
    picked = []
    for width in (200, 400, 800):
        near = [p for p in pool if abs(p["rating"] - target) <= width]
        near.sort(key=lambda p: (not (p["themes"] & preferred), -p["popularity"], p["id"]))
        picked = near[:n]
        if len(picked) == n:
            break
    label_themes = [f"opening {value}"] if opening else themes + sorted(preferred - set(themes))
    return {"themes": label_themes, "puzzles": picked}
