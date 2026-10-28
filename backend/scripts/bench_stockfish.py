"""Benchmark Stockfish node limits on real-game positions.

Reports positions/sec (1 thread) and how often each limit's move labels
agree with a high-node reference. Usage: python scripts/bench_stockfish.py FIXTURE.ndjson N
"""

import io
import json
import sys
import time

import chess
import chess.engine
import chess.pgn

from app.analysis import classify, win_chances

LIMITS = [25_000, 50_000, 100_000, 200_000]
REFERENCE = 1_000_000


def games(path: str, n: int) -> list[list[chess.Board]]:
    """Positions per game (including the start), at most n positions in total."""
    out, total = [], 0
    for line in open(path):
        board = chess.pgn.read_game(io.StringIO(json.loads(line)["pgn"])).board()
        boards = [board.copy()]
        for move in chess.pgn.read_game(io.StringIO(json.loads(line)["pgn"])).mainline_moves():
            board.push(move)
            boards.append(board.copy())
        out.append(boards[: n - total])
        total += len(out[-1])
        if total >= n:
            break
    return out


def labels(engine, games, nodes):
    t, count, out = time.perf_counter(), 0, []
    for boards in games:
        wcs = []
        for b in boards:
            engine.configure({"Clear Hash": None})
            info = engine.analyse(b, chess.engine.Limit(nodes=nodes))
            wcs.append(win_chances(info["score"], chess.WHITE))
        count += len(boards)
        for i in range(1, len(boards)):
            sign = 1 if boards[i - 1].turn == chess.WHITE else -1
            out.append(classify(sign * wcs[i - 1], sign * wcs[i]))
    return out, count / (time.perf_counter() - t)


def main():
    gs = games(sys.argv[1], int(sys.argv[2]))
    engine = chess.engine.SimpleEngine.popen_uci("stockfish")
    engine.configure({"Threads": 1, "Hash": 16})
    ref, ref_pps = labels(engine, gs, REFERENCE)
    print(f"positions={sum(map(len, gs))} games={len(gs)} engine={engine.id['name']}")
    print(
        f"reference nodes={REFERENCE} pos/s={ref_pps:.2f} flagged={sum(x is not None for x in ref)}"
    )
    for nodes in LIMITS:
        got, pps = labels(engine, gs, nodes)
        agree = sum(a == b for a, b in zip(got, ref, strict=True)) / len(ref)
        ref_bl = [i for i, x in enumerate(ref) if x == "blunder"]
        bl_recall = sum(got[i] == "blunder" for i in ref_bl) / max(1, len(ref_bl))
        print(
            f"nodes={nodes} pos/s={pps:.2f} label_agreement={agree:.3f} "
            f"blunder_recall={bl_recall:.2f} (ref blunders={len(ref_bl)})"
        )
    engine.quit()


if __name__ == "__main__":
    main()
