"""Load test: several players upload games at once while readers poll pages.

Measures, per player: time until the first 50%/all of their games are analysed (fairness
between concurrent users), total engine throughput, and page latency under load.
Run against a stack with RATE_LIMIT=false (all requests come from one IP).

Usage: python scripts/load_test.py GAMES.jsonl [--api URL] [--players 3] [--games 30]
       [--readers 10] [--skip 0]
GAMES.jsonl: one {"pgn": ...} per line (e.g. data/bulk/s50/validity.jsonl)."""

import argparse
import asyncio
import json
import re
import statistics
import time
from collections import defaultdict

import httpx

WHITE = re.compile(r'\[White "([^"]+)"\]')
BLACK = re.compile(r'\[Black "([^"]+)"\]')


def pick_players(path: str, players: int, games: int, skip: int) -> dict[str, list[str]]:
    by_player = defaultdict(list)
    for line in open(path):
        pgn = json.loads(line)["pgn"]
        for rx in (WHITE, BLACK):
            by_player[rx.search(pgn).group(1)].append(pgn)
    chosen = [p for p, g in by_player.items() if len(g) >= games][skip : skip + players]
    return {p: by_player[p][:games] for p in chosen}


def pct(xs: list[float], q: float) -> float:
    return statistics.quantiles(xs, n=100)[int(q) - 1] if len(xs) > 1 else xs[0]


async def upload(client, player: str, pgns: list[str]) -> str:
    files = {"file": ("games.pgn", "\n\n".join(pgns).encode())}
    r = await client.post("/upload", data={"username": player}, files=files)
    r.raise_for_status()
    return r.json()["job_id"]


async def watch(client, job_id: str, t0: float, out: dict) -> None:
    while True:
        s = (await client.get(f"/jobs/{job_id}")).json()
        if s["games_total"] and "half" not in out and s["games_done"] >= s["games_total"] / 2:
            out["half"] = time.perf_counter() - t0
        if s["status"] in ("done", "failed"):
            out.update(all=time.perf_counter() - t0, status=s["status"], positions=s["positions"])
            return
        await asyncio.sleep(2)


async def reader(client, paths: list[str], stop: asyncio.Event, lat: list[float]) -> None:
    i = 0
    while not stop.is_set():
        t = time.perf_counter()
        r = await client.get(paths[i % len(paths)])
        if r.status_code < 500:
            lat.append(time.perf_counter() - t)
        i += 1
        await asyncio.sleep(0.2)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--players", type=int, default=3)
    ap.add_argument("--games", type=int, default=30)
    ap.add_argument("--readers", type=int, default=10)
    ap.add_argument("--skip", type=int, default=0, help="skip players used by earlier runs")
    args = ap.parse_args()

    players = pick_players(args.file, args.players, args.games, args.skip)
    async with httpx.AsyncClient(base_url=args.api, timeout=60) as client:
        t0 = time.perf_counter()
        ids = await asyncio.gather(*(upload(client, p, g) for p, g in players.items()))
        results = {p: {} for p in players}
        stop, lat = asyncio.Event(), []
        paths = ["/", "/health"] + [f"/ui/jobs/{j}" for j in ids]
        readers = [
            asyncio.create_task(reader(client, paths, stop, lat)) for _ in range(args.readers)
        ]
        await asyncio.gather(
            *(watch(client, j, t0, results[p]) for p, j in zip(players, ids, strict=True))
        )
        stop.set()
        await asyncio.gather(*readers)
    wall = time.perf_counter() - t0
    positions = sum(r["positions"] for r in results.values())
    summary = {
        "players": len(players),
        "games_per_player": args.games,
        "wall_s": round(wall, 1),
        "positions": positions,
        "positions_per_s": round(positions / wall, 1),
        "per_player": {
            p: {"half_done_s": round(r["half"], 1), "all_done_s": round(r["all"], 1)}
            for p, r in results.items()
        },
        "page_requests": len(lat),
        "page_p50_ms": round(pct(lat, 50) * 1000, 1),
        "page_p95_ms": round(pct(lat, 95) * 1000, 1),
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
