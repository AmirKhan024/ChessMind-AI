"""Build the bulk training sample from a (possibly truncated) Lichess monthly .pgn.zst.

Outputs in OUT_DIR:
  meta.parquet       one row per game (all games), for the eval selection-bias check
  moves.parquet      one row per move of games with Lichess %eval: features + labels
  consistency.jsonl  random evaluated games (PGN) for re-labelling with our own engine
  random.jsonl       random games WITHOUT %eval, labelled by our engine for the bias check
  validity.jsonl     all games (up to --validity-games) of random players with that many
                     games, for the predictive-validity experiment

Usage: python -m ml.sample IN.pgn.zst OUT_DIR [--consistency 200] [--random 300]
"""

import argparse
import io
import json
import random
import re
from multiprocessing import Pool
from pathlib import Path

import pandas as pd
import zstandard

from app.games import time_control
from ml.labels import labelled_moves

SPEEDS = {"bullet", "blitz", "rapid", "classical"}
HEADER = re.compile(r'^\[(\w+) "([^"]*)"\]', re.M)


def iter_pgns(path: str):
    """Yield raw PGN texts; stops cleanly at the end of a truncated download."""
    reader = zstandard.ZstdDecompressor().stream_reader(open(path, "rb"))
    text = io.TextIOWrapper(reader, encoding="utf-8", errors="replace")
    buf = []
    try:
        for line in text:
            if line.startswith("[Event ") and buf:
                yield "".join(buf)
                buf = []
            buf.append(line)
    except zstandard.ZstdError:
        return  # truncated tail: drop the partial game


def meta(pgn: str) -> dict:
    h = dict(HEADER.findall(pgn))
    initial, inc = time_control(h.get("TimeControl", ""))
    return {
        "id": h.get("Site", "")[-8:],
        "white": h.get("White", "").lower(),
        "black": h.get("Black", "").lower(),
        "white_elo": int(h["WhiteElo"]) if h.get("WhiteElo", "").isdigit() else None,
        "black_elo": int(h["BlackElo"]) if h.get("BlackElo", "").isdigit() else None,
        "time_control": h.get("TimeControl"),
        "speed": h.get("Event", "").split()[1].lower()
        if len(h.get("Event", "").split()) > 2
        else None,
        "result": h.get("Result"),
        "termination": h.get("Termination"),
        "plies": pgn.count("[%clk"),
        "has_eval": "[%eval" in pgn,
        "usable": initial is not None and "WhiteElo" in h and "BlackElo" in h,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("out")
    ap.add_argument("--consistency", type=int, default=200)
    ap.add_argument("--random", type=int, default=300)
    ap.add_argument("--validity-players", type=int, default=60)
    ap.add_argument("--validity-games", type=int, default=50)
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    metas, evaluated, plain, by_player = [], [], [], {}
    for pgn in iter_pgns(args.src):
        m = meta(pgn)
        metas.append(m)
        if m["usable"]:
            (evaluated if m["has_eval"] else plain).append(pgn)
            if m["speed"] in SPEEDS:
                for p in (m["white"], m["black"]):
                    by_player.setdefault(p, []).append(pgn)
    pd.DataFrame(metas).to_parquet(out / "meta.parquet")
    print(f"games={len(metas)} evaluated+usable={len(evaluated)}")

    rng = random.Random(0)
    samples = [("consistency", evaluated, args.consistency), ("random", plain, args.random)]
    for name, games, n in samples:
        with open(out / f"{name}.jsonl", "w") as f:
            for pgn in rng.sample(games, min(n, len(games))):
                f.write(json.dumps({"pgn": pgn}) + "\n")

    many = sorted(p for p, gs in by_player.items() if len(gs) >= args.validity_games)
    chosen = rng.sample(many, min(args.validity_players, len(many)))
    with open(out / "validity.jsonl", "w") as f:
        for p in chosen:
            for pgn in by_player[p][: args.validity_games]:  # file order = chronological
                f.write(json.dumps({"player": p, "pgn": pgn}) + "\n")
    print(f"validity: {len(chosen)} of {len(many)} players with >= {args.validity_games} games")

    with Pool(args.workers) as pool:
        rows = [r for game in pool.imap(labelled_moves, evaluated, chunksize=50) for r in game]
    df = pd.DataFrame(rows)
    df.to_parquet(out / "moves.parquet")
    print(f"moves={len(df)} blunder_rate={df.blunder.mean():.4f} players={df.player.nunique()}")


if __name__ == "__main__":
    main()
