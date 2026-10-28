"""Label bulk games with OUR engine settings (same evals as serving). Each output row has
the same schema as moves.parquet, plus `lichess_label` when the game has %eval.
Runs where Stockfish is installed (the worker image).
Usage: python -m ml.relabel IN.jsonl OUT.jsonl [--workers 8]"""

import argparse
import json
from multiprocessing import Pool

import chess
import chess.engine

from app.analysis import win_chances
from app.config import settings
from app.games import read_pgns
from ml.labels import labelled_rows, lichess_wc

_sf = None


def _init():
    global _sf
    _sf = chess.engine.SimpleEngine.popen_uci(settings.engine_path)
    _sf.configure({"Threads": 1, "Hash": 16})


def _wc(board: chess.Board) -> float:
    info = _sf.analyse(board, chess.engine.Limit(nodes=settings.engine_nodes))
    return win_chances(info["score"], chess.WHITE)


def label_game(line: str) -> list[dict]:
    game = next(read_pgns(json.loads(line)["pgn"]))
    board = game.board()
    ours = [_wc(board)]
    for move in game.mainline_moves():
        board.push(move)
        ours.append(_wc(board))
    rows = labelled_rows(game, ours)
    theirs = {r["ply"]: r["label"] for r in labelled_rows(game, lichess_wc(game))}
    for r in rows:
        r["has_lichess"] = r["ply"] in theirs
        r["lichess_label"] = theirs.get(r["ply"])
        r["played_at"] = r["played_at"].isoformat() if r["played_at"] else None
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("out")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()
    with open(args.src) as f, Pool(args.workers, initializer=_init) as pool:
        with open(args.out, "w") as out:
            for rows in pool.imap_unordered(label_game, f.readlines()):
                for r in rows:
                    out.write(json.dumps(r) + "\n")


if __name__ == "__main__":
    main()
