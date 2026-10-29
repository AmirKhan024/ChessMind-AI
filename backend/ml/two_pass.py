"""Would a two-pass analysis keep our labels while using much less engine time?

Pass 1 evaluates every position with a small node limit. Pass 2 re-evaluates, at the
serving limit, only the two positions around moves whose pass-1 drop is at least MARGIN.
Moves never re-checked keep their pass-1 label, which cannot be a blunder (drop < MARGIN <
15 points). Reference = full serving-limit labels already computed by ml/relabel.py.

Usage: python -m ml.two_pass GAMES.jsonl OURS.jsonl [--nodes 5000 10000 20000] [--workers 8]
"""

import argparse
import json
import time
from multiprocessing import Pool

import chess
import chess.engine

from app.analysis import win_chances
from app.config import settings
from app.games import parse_game, read_pgns

MARGINS = [3, 5, 7.5, 10]  # win-% points; blunder threshold is 15
_sf = None


def _init():
    global _sf
    _sf = chess.engine.SimpleEngine.popen_uci(settings.engine_path)
    _sf.configure({"Threads": 1, "Hash": 16})


def _game_evals(args) -> tuple[str, list[float], list[bool]]:
    """White's winning chances per position, and whether each move gives checkmate."""
    line, nodes = args
    game = next(read_pgns(json.loads(line)["pgn"]))
    board = game.board()
    wcs, mates = [_eval(board, nodes)], []
    for move in game.mainline_moves():
        board.push(move)
        mates.append(board.is_checkmate())
        wcs.append(_eval(board, nodes))
    return parse_game(game, None)[0]["id"], wcs, mates


def _eval(board: chess.Board, nodes: int) -> float:
    if board.is_checkmate():  # the side to move is mated
        return -1.0 if board.turn == chess.WHITE else 1.0
    info = _sf.analyse(board, chess.engine.Limit(nodes=nodes))
    return win_chances(info["score"], chess.WHITE)


def run(lines: list[str], nodes: int, workers: int) -> tuple[dict, float]:
    t = time.perf_counter()
    with Pool(workers, initializer=_init) as pool:
        out = {g: (w, m) for g, w, m in pool.map(_game_evals, [(x, nodes) for x in lines])}
    return out, time.perf_counter() - t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("games")
    ap.add_argument("ours")
    ap.add_argument("--nodes", type=int, nargs="+", default=[5000, 10000, 20000])
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--timing-games", type=int, default=40)
    args = ap.parse_args()

    lines = open(args.games).read().splitlines()
    ref = {}
    for line in open(args.ours):
        r = json.loads(line)
        ref[(r["game_id"], r["ply"])] = r["blunder"]

    # Full-limit speed on a subset, same machine and worker count, for a fair cost ratio.
    sub = lines[: args.timing_games]
    full, t_full = run(sub, settings.engine_nodes, args.workers)
    full_pps = sum(len(w) for w, _ in full.values()) / t_full
    print(f"serving limit {settings.engine_nodes} nodes: {full_pps:.1f} positions/s")

    results = {"serving_nodes": settings.engine_nodes, "full_pps": full_pps, "runs": []}
    for nodes in args.nodes:
        quick, t_quick = run(lines, nodes, args.workers)
        positions = sum(len(w) for w, _ in quick.values())
        quick_pps = positions / t_quick
        for margin in MARGINS:
            ref_blunders = found = rechecked_positions = 0
            for gid, (wcs, mates) in quick.items():
                recheck = set()
                for i, mate in enumerate(mates):
                    key = (gid, i + 1)
                    if key not in ref:
                        continue  # game or move skipped by the labeller (e.g. speed filter)
                    truth = 0 if mate else ref[key]  # reference predates the mate-label fix
                    sign = 1 if i % 2 == 0 else -1
                    drop = sign * (wcs[i] - wcs[i + 1]) * 50
                    hit = drop >= margin
                    if hit:
                        recheck |= {i, i + 1}
                    ref_blunders += truth
                    found += truth and hit
                rechecked_positions += len(recheck)
            cost = full_pps / quick_pps + rechecked_positions / positions
            row = {
                "nodes": nodes,
                "margin_pts": margin,
                "quick_pps": round(quick_pps, 1),
                "recheck_share": round(rechecked_positions / positions, 3),
                "blunder_recall": round(found / ref_blunders, 4),
                "cost_vs_full": round(cost, 3),
                "speedup": round(1 / cost, 2),
            }
            results["runs"].append(row)
            print(row)
    results["reference_blunders"] = ref_blunders
    results["positions"] = positions
    print(json.dumps(results))


if __name__ == "__main__":
    main()
