"""RQ jobs. fetch_player_games runs on the `lichess` queue (one worker, so concurrency 1);
analyse_game runs on the `analysis` queue (many workers); write_report on the `report` queue
(LLM calls, kept off the web request)."""

import logging
from itertools import islice

import chess
import chess.engine
from redis import Redis
from rq import Callback, Queue
from rq.exceptions import NoSuchJobError
from rq.job import Job
from sqlalchemy import case, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from app import lichess, metrics
from app.analysis import classify, win_chances
from app.config import settings
from app.db import engine, games, jobs, moves, players, position_cache
from app.games import parse_game, read_pgns

log = logging.getLogger(__name__)

redis = Redis.from_url(settings.redis_url, socket_connect_timeout=2)
lichess_queue = Queue("lichess", connection=redis)
analysis_queue = Queue("analysis", connection=redis)
report_queue = Queue("report", connection=redis)


GAME_ATTEMPTS = 2


def _on_failure(rq_job, connection, exc_type, exc_value, tb) -> None:
    """Called by RQ for crashes and timeouts, and for jobs whose worker died (RQ notices
    within minutes and reports AbandonedJobError). RQ keeps the job in its failed registry.

    One game failing must not fail the user's whole job: retry it once, then skip it and
    carry on. A failed Lichess fetch does fail the job (nothing to analyse)."""
    job_id = rq_job.meta["job_id"]
    error = f"{exc_type.__name__}: {exc_value}"
    if rq_job.origin == "report":
        return  # report jobs have no jobs row; their RQ status is what the UI reads
    if rq_job.origin == "analysis":
        game_id, attempts = rq_job.args[0], rq_job.meta.get("attempts", 1)
        log.warning("game %s failed (attempt %d): %s", game_id, attempts, error)
        if attempts < GAME_ATTEMPTS:
            enqueue(
                analysis_queue,
                analyse_game,
                job_id,
                game_id,
                job_id,
                timeout=1800,
                attempts=attempts + 1,
            )
        else:
            metrics.inc("games_skipped")
            _finish_game(job_id, skipped=True)
        return
    _set_job(job_id, status="failed", error=error)
    metrics.inc("jobs_finished", status="failed")


def enqueue(queue: Queue, func, job_id: str, *args, timeout: int, attempts: int = 1) -> None:
    queue.enqueue(
        func,
        *args,
        job_timeout=timeout,
        meta={"job_id": job_id, "attempts": attempts},
        on_failure=Callback(_on_failure),
    )


def queue_position(job_id: str, status: str) -> int | None:
    """How many queued RQ jobs (other players' fetches, games or reports) are ahead of this
    job's first waiting one. None when nothing of this job is waiting."""
    queue = {"fetching": lichess_queue, "analysing": analysis_queue, "queued": report_queue}.get(
        status
    )
    if queue is None:
        return None
    for i, rq_job in enumerate(Job.fetch_many(queue.get_job_ids(), connection=redis)):
        if rq_job is not None and rq_job.meta.get("job_id") == job_id:
            return i
    return None


def enqueue_report(username: str, report_id: str) -> None:
    report_queue.enqueue(
        write_report,
        username,
        report_id,
        job_id=report_id,
        job_timeout=300,
        meta={"job_id": report_id},
        failure_ttl=7 * 24 * 3600,
        on_failure=Callback(_on_failure),
    )


def report_status(report_id: str) -> str | None:
    """RQ status of a report not yet stored (queued, started, failed...); None if unknown."""
    try:
        return Job.fetch(report_id, connection=redis).get_status()
    except NoSuchJobError:
        return None


def write_report(username: str, report_id: str) -> None:
    from app import coach

    coach.generate_report(username, report_id)


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


def _pending_key(job_id: str) -> str:
    return f"job:{job_id}:pending"


def enqueue_analysis(job_id: str, username: str) -> int:
    """Queue every not-yet-analysed game of the player, newest first, so an early report on
    the games done so far covers recent play. Safe to call again after a crash.

    Fairness: only `analysis_workers` games per job sit in the RQ queue at a time; the rest
    wait in a Redis list and each finished game queues its job's next one. With several
    users the FIFO queue then alternates between their jobs instead of finishing one user's
    300 games before starting the next (load test: 3 users waited 31/68/93 s for half)."""
    with engine.begin() as conn:
        ids = conn.scalars(
            select(games.c.id)
            .where(player_games(username), games.c.analysed_at.is_(None))
            .order_by(games.c.played_at.desc().nulls_last())
        ).all()
        conn.execute(
            update(jobs)
            .where(jobs.c.id == job_id)
            .values(status="analysing" if ids else "done", total=len(ids), done=0)
        )
    key = _pending_key(job_id)
    redis.delete(key)
    first, rest = ids[: settings.analysis_workers], ids[settings.analysis_workers :]
    if rest:
        redis.rpush(key, *rest)
        redis.expire(key, 7 * 24 * 3600)
    for gid in first:
        enqueue(analysis_queue, analyse_game, job_id, gid, job_id, timeout=1800)
    return len(ids)


def _enqueue_next(job_id: str) -> None:
    gid = redis.lpop(_pending_key(job_id))
    if gid is not None:
        enqueue(analysis_queue, analyse_game, job_id, gid.decode(), job_id, timeout=1800)


def fetch_player_games(job_id: str, player_id: int, username: str) -> None:
    with engine.connect() as conn:
        since = conn.scalar(select(players.c.latest_game_ms).where(players.c.id == player_id))
    try:
        # Lichess may return more than `max` when filters are applied, so cap here too.
        stream = lichess.stream_games(username, since + 1 if since else None, settings.games_cap)
        fetched = list(islice(stream, settings.games_cap))
    except lichess.UserNotFound:
        _set_job(job_id, status="failed", error="Lichess user not found")
        metrics.inc("jobs_finished", status="failed")
        return
    metrics.inc("lichess_games_fetched", len(fetched))
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


def evaluate(fens: list[str], sf, engine_name: str, nodes: int) -> tuple[list[dict], int]:
    """Eval every position through the shared cache. Returns (evals, cache hits)."""
    keys = [_epd(f) for f in fens]
    with engine.connect() as conn:
        cached = {
            r.epd: r._asdict()
            for r in conn.execute(
                select(position_cache).where(
                    position_cache.c.epd.in_(set(keys)),
                    position_cache.c.engine == engine_name,
                    position_cache.c.nodes == nodes,
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
        info = sf.analyse(chess.Board(fen), chess.engine.Limit(nodes=nodes))
        score = info["score"].white()
        pv = info.get("pv")
        new[key] = {
            "epd": key,
            "engine": engine_name,
            "nodes": nodes,
            "cp": score.score(),
            "mate": score.mate(),
            "best_uci": pv[0].uci() if pv else None,
        }
    if new:
        with engine.begin() as conn:
            conn.execute(insert(position_cache).values(list(new.values())).on_conflict_do_nothing())
    evals = {**cached, **new}
    return [evals[k] for k in keys], hits


def evaluate_two_pass(
    fens: list[str], colors: list[str], final: chess.Board, sf, engine_name: str
) -> tuple[list[dict], int, int]:
    """Pass 1: every position at `engine_quick_nodes`. Pass 2: re-evaluate at
    `engine_nodes` both positions around any move whose drop is >= `recheck_margin_pts`,
    repeating until every such move rests on full-limit evals at both ends. Moves below the
    margin keep pass-1 evals; they cannot be blunders (margin < 15 points). Measured on 295
    random games: 99.1% of full-limit blunders kept at 2.5x the speed (ml/two_pass.py).
    Returns (evals, cache hits, cache lookups)."""
    if not settings.engine_quick_nodes:
        return *evaluate(fens, sf, engine_name, settings.engine_nodes), len(fens)
    evals, hits = evaluate(fens, sf, engine_name, settings.engine_quick_nodes)
    lookups = len(fens)
    full: set[int] = set()
    while True:
        wc = [win_chances(s, chess.WHITE) for s in _scores(evals, final)]
        todo = set()
        for i, color in enumerate(colors):
            sign = 1 if color == "white" else -1
            if sign * (wc[i] - wc[i + 1]) * 50 >= settings.recheck_margin_pts:
                todo |= {i, i + 1} - full
        if not todo:
            return evals, hits, lookups
        idx = sorted(todo)
        again, more = evaluate([fens[i] for i in idx], sf, engine_name, settings.engine_nodes)
        for i, ev in zip(idx, again, strict=True):
            evals[i] = ev
        full |= todo
        hits += more
        lookups += len(idx)


def _score(ev: dict) -> chess.engine.PovScore:
    s = chess.engine.Mate(ev["mate"]) if ev["mate"] is not None else chess.engine.Cp(ev["cp"])
    return chess.engine.PovScore(s, chess.WHITE)


def _scores(evals: list[dict], final: chess.Board) -> list[chess.engine.PovScore]:
    scores = [_score(ev) for ev in evals]
    # The cache stores mate as white's mate count, and "mate in 0" loses who was mated;
    # in a checkmate the side to move is the one mated.
    if final.is_checkmate():
        scores[-1] = chess.engine.PovScore(chess.engine.Mate(0), final.turn)
    return scores


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
            colors = [r["color"] for r in rows]
            evals, hits, lookups = evaluate_two_pass(fens, colors, final, sf, engine_name)
        finally:
            if own:
                sf.quit()
        scores = _scores(evals, final)
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
                    analysed_at=func.now(),
                    engine=engine_name,
                    engine_nodes=settings.engine_nodes,
                    quick_nodes=settings.engine_quick_nodes or None,
                )
            )
            conn.execute(
                update(jobs)
                .where(jobs.c.id == job_id)
                .values(cache_hits=jobs.c.cache_hits + hits, positions=jobs.c.positions + lookups)
            )
        metrics.inc("positions_cached", hits)
        metrics.inc("positions_analysed", lookups - hits)
        metrics.inc("games_analysed")
    _finish_game(job_id)


def _finish_game(job_id: str, skipped: bool = False) -> None:
    """Count one game of the job as done (analysed, or skipped after repeated failures) and
    queue the job's next game."""
    values = {
        "done": jobs.c.done + 1,
        "status": case((jobs.c.done + 1 >= jobs.c.total, "done"), else_=jobs.c.status),
        "updated_at": func.now(),
    }
    if skipped:
        values["skipped"] = jobs.c.skipped + 1
    with engine.begin() as conn:
        row = conn.execute(
            update(jobs)
            .where(jobs.c.id == job_id)
            .values(**values)
            .returning(jobs.c.done, jobs.c.total, jobs.c.created_at, func.now())
        ).one()
    _enqueue_next(job_id)
    if row.done == row.total:
        metrics.inc("jobs_finished", status="done")
        seconds = (row[3] - row.created_at).total_seconds()
        metrics.observe("job_seconds_per_100_games", seconds * 100 / row.total)
