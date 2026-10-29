"""How total Stockfish throughput scales with the number of parallel single-thread engines
(one per analysis worker). Usage: python scripts/bench_parallel.py FIXTURE.ndjson N W [W ...]
Runs N positions per engine at the default node limit with W engines at once."""

import sys
import time
from concurrent.futures import ThreadPoolExecutor

import chess.engine

from app.config import settings
from scripts.bench_stockfish import games


def run(boards: list[chess.Board]) -> int:
    engine = chess.engine.SimpleEngine.popen_uci(settings.engine_path)
    engine.configure({"Threads": 1, "Hash": 16})
    for b in boards:
        engine.configure({"Clear Hash": None})
        engine.analyse(b, chess.engine.Limit(nodes=settings.engine_nodes))
    engine.quit()
    return len(boards)


def main():
    boards = [b for g in games(sys.argv[1], int(sys.argv[2])) for b in g]
    for workers in map(int, sys.argv[3:]):
        t = time.perf_counter()
        with ThreadPoolExecutor(workers) as pool:  # each engine is its own process
            done = sum(pool.map(run, [boards] * workers))
        pps = done / (time.perf_counter() - t)
        print(f"workers={workers} nodes={settings.engine_nodes} total pos/s={pps:.1f}")


if __name__ == "__main__":
    main()
