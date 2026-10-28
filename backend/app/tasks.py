"""RQ jobs. fetch_player_games runs on the `lichess` queue (one worker, so concurrency 1);
analyse_game runs on the `analysis` queue (many workers)."""

from itertools import islice

import chess
import chess.engine
from redis import Redis
from rq import Callback, Queue
from rq.job import Job
from sqlalchemy import case, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from app import lichess
from app.analysis import classify, win_chances
from app.config import settings
from app.db import engine, games, jobs, moves, players, position_cache
from app.games import parse_game, read_pgns

redis = Redis.from_url(settings.redis_url)
lichess_queue = Queue("lichess", connection=redis)
analysis_queue = Queue("analysis", connection=redis)


def _on_failure(rq_job, connection, exc_type, exc_value, tb) -> None:
    """Crashes and timeouts mark our job failed; RQ keeps the job in its failed registry."""
    _set_job(rq_job.meta["job_id"], status="failed", error=f"{exc_type.__name__}: {exc_value}")


def enqueue(queue: Queue, func, job_id: str, *args, timeout: int) -> None:
    queue.enqueue(
        func,
        *args,
        job_timeout=timeout,
        meta={"job_id": job_id},
        on_failure=Callback(_on_failure),
    )


def queue_position(job_id: str, status: str) -> int | None:
    """How many queued RQ jobs (other players' fetches or games) are ahead of this job's first
    waiting one. None when nothing of this job is waiting (running, finished or failed)."""
    queue = {"fetching": lichess_queue, "analysing": analysis_queue}.get(status)
    if queue is None:
        return None
    for i, rq_job in enumerate(Job.fetch_many(queue.get_job_ids(), connection=redis)):
        if rq_job is not None and rq_job.meta.get("job_id") == job_id:
            return i
    return None


def player_games(username: str):
    return or_(games.c.white == username, games.c.black == username)


def save_games(username: str, pgn_texts: list[str], source: str) -> int:
    """Parse and store games; returns how many were new. Moves are stored at analysis time."""
    rows = []
    for text in pgn_texts:
        for game in read_pgns(text):
            parsed = parse_game(game, username)
            if parsed:
                rows.append({**parsed[0], "source": source})
    if not rows:
        return 0
    with engine.begin() as conn:
        res = conn.execute(
            insert(games).values(rows).on_conflict_do_nothing().returning(games.c.id)
        )
        return len(res.all())


def enqueue_analysis(job_id: str, username: str) -> int:
    """Queue every not-yet-analysed game of the player. Safe to call again after a crash."""
    with engine.begin() as conn:
        ids = conn.scalars(
            select(games.c.id).where(player_games(username), games.c.analysed_at.is_(None))
        ).all()
        conn.execute(
            update(jobs)
            .where(jobs.c.id == job_id)
            .values(status="analysing" if ids else "done", total=len(ids), done=0)
        )
    for gid in ids:
        enqueue(analysis_queue, analyse_game, job_id, gid, job_id, timeout=1800)
    return len(ids)


def fetch_player_games(job_id: str, player_id: int, username: str) -> None:
    with engine.connect() as conn:
        since = conn.scalar(select(players.c.latest_game_ms).where(players.c.id == player_id))
    try:
        # Lichess may return more than `max` when filters are applied, so cap here too.
        stream = lichess.stream_games(username, since + 1 if since else None, settings.games_cap)
        fetched = list(islice(stream, settings.games_cap))
    except lichess.UserNotFound:
        _set_job(job_id, status="failed", error="Lichess user not found")
        return
    save_games(username, [g["pgn"] for g in fetched], "lichess")
    if fetched:
        with engine.begin() as conn:
            conn.execute(
                update(players)
                .where(players.c.id == player_id)
                .values(latest_game_ms=max(g["createdAt"] for g in fetched))
            )
    enqueue_analysis(job_id, username)


def _set_job(job_id: str, **values) -> None:
    with engine.begin() as conn:
        conn.execute(
            update(jobs).where(jobs.c.id == job_id).values(**values, updated_at=func.now())
        )


def _epd(fen: str) -> str:
    return " ".join(fen.split()[:4])


def evaluate(fens: list[str], sf, engine_name: str) -> tuple[list[dict], int]:
    """Eval every position through the shared cache. Returns (evals, cache hits)."""
    keys = [_epd(f) for f in fens]
    with engine.connect() as conn:
        cached = {
            r.epd: r._asdict()
            for r in conn.execute(
                select(position_cache).where(
                    position_cache.c.epd.in_(set(keys)),
                    position_cache.c.engine == engine_name,
                    position_cache.c.nodes == settings.engine_nodes,
                )
            )
        }
    hits, new = 0, {}
    for fen, key in zip(fens, keys, strict=True):
        if key in cached:
            hits += 1
            continue
        if key in new:
            continue
        info = sf.analyse(chess.Board(fen), chess.engine.Limit(nodes=settings.engine_nodes))
        score = info["score"].white()
        pv = info.get("pv")
        new[key] = {
            "epd": key,
            "engine": engine_name,
            "nodes": settings.engine_nodes,
            "cp": score.score(),
            "mate": score.mate(),
            "best_uci": pv[0].uci() if pv else None,
        }
    if new:
        with engine.begin() as conn:
            conn.execute(insert(position_cache).values(list(new.values())).on_conflict_do_nothing())
    evals = {**cached, **new}
    return [evals[k] for k in keys], hits


def _score(ev: dict) -> chess.engine.PovScore:
    s = chess.engine.Mate(ev["mate"]) if ev["mate"] is not None else chess.engine.Cp(ev["cp"])
    return chess.engine.PovScore(s, chess.WHITE)


def analyse_game(game_id: str, job_id: str, sf=None) -> None:
    """Idempotent: a game already analysed (e.g. before a crash) is only counted."""
    with engine.connect() as conn:
        g = conn.execute(select(games).where(games.c.id == game_id)).one()
    if g.analysed_at is None:
        own = sf is None
        sf = sf or chess.engine.SimpleEngine.popen_uci(settings.engine_path)
        try:
            sf.configure({"Threads": 1, "Hash": 16})
            engine_name = sf.id["name"]
            _, rows = parse_game(next(read_pgns(g.pgn)), g.white)
            final = chess.Board(rows[-1]["fen_before"])
            final.push_uci(rows[-1]["uci"])
            fens = [r["fen_before"] for r in rows] + [final.fen()]
            evals, hits = evaluate(fens, sf, engine_name)
        finally:
            if own:
                sf.quit()
        scores = [_score(ev) for ev in evals]
        # The cache stores mate as white's mate count, and "mate in 0" loses who was mated;
        # in a checkmate the side to move is the one mated.
        if final.is_checkmate():
            scores[-1] = chess.engine.PovScore(chess.engine.Mate(0), final.turn)
        for i, r in enumerate(rows):
            pov = chess.WHITE if r["color"] == "white" else chess.BLACK
            r["wc_before"] = win_chances(scores[i], pov)
            r["wc_after"] = win_chances(scores[i + 1], pov)
            r["best_uci"] = evals[i]["best_uci"]
            r["label"] = classify(r["wc_before"], r["wc_after"])
        with engine.begin() as conn:
            conn.execute(insert(moves).values(rows).on_conflict_do_nothing())
            conn.execute(
                update(games)
                .where(games.c.id == game_id)
                .values(
                    analysed_at=func.now(), engine=engine_name, engine_nodes=settings.engine_nodes
                )
            )
            conn.execute(
                update(jobs)
                .where(jobs.c.id == job_id)
                .values(cache_hits=jobs.c.cache_hits + hits, positions=jobs.c.positions + len(fens))
            )
    _set_job(
        job_id,
        done=jobs.c.done + 1,
        status=case((jobs.c.done + 1 >= jobs.c.total, "done"), else_=jobs.c.status),
    )
