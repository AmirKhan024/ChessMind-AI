"""Fetch -> store -> analyse against a real Postgres, with a fake engine and Lichess stub."""

import json
from pathlib import Path

import chess
import chess.engine
import pytest
from sqlalchemy import delete, func, select, text

from app import tasks
from app.db import engine, games, jobs, moves, players, position_cache
from app.main import _new_job

FIXTURE = Path(__file__).parent / "fixtures" / "games.ndjson"

try:
    with engine.connect() as c:
        c.execute(text("SELECT 1 FROM games LIMIT 1"))
except Exception:  # noqa: BLE001
    pytest.skip("Postgres with migrated schema not available", allow_module_level=True)


class FakeEngine:
    id = {"name": "FakeFish"}

    def __init__(self):
        self.calls = 0

    def configure(self, _):
        pass

    def analyse(self, board, limit):
        self.calls += 1
        # White "loses" 400cp whenever it is black to move: makes white moves blunders.
        cp = -400 if board.turn == chess.BLACK else 0
        pv = [next(iter(board.legal_moves))] if not board.is_game_over() else []
        return {"score": chess.engine.PovScore(chess.engine.Cp(cp), chess.WHITE), "pv": pv}


@pytest.fixture
def run(monkeypatch):
    for t in (moves, games, jobs, players, position_cache):
        with engine.begin() as c:
            c.execute(delete(t))
    sf = FakeEngine()
    lines = [json.loads(x) for x in FIXTURE.read_text().splitlines()]
    served = {"games": lines[:6]}
    # Like real Lichess, the stub ignores `max`; the task must enforce the cap.
    monkeypatch.setattr(tasks.lichess, "stream_games", lambda u, since, n: iter(served["games"]))
    monkeypatch.setattr(tasks.settings, "games_cap", 4)
    monkeypatch.setattr(tasks.settings, "engine_quick_nodes", 0)  # single pass here
    monkeypatch.setattr(tasks.settings, "analysis_workers", 2)  # 2 queued, rest chained
    monkeypatch.setattr(tasks, "enqueue", lambda q, f, _, gid, jid, **kw: f(gid, jid, sf=sf))

    def go():
        job_id, pid = _new_job("Thibault", "fetching")
        tasks.fetch_player_games(job_id, pid, "thibault")
        with engine.connect() as c:
            return c.execute(select(jobs).where(jobs.c.id == job_id)).one()

    return go, sf, served, lines


def test_fetch_and_analyse_then_rerun_only_new(run):
    go, sf, served, lines = run
    job = go()
    assert (job.status, job.done, job.total) == ("done", 4, 4)
    with engine.connect() as c:
        n_moves = c.scalar(select(func.count()).select_from(moves))
        white_blunders = c.scalar(
            select(func.count()).where(moves.c.color == "white", moves.c.label == "blunder")
        )
        assert c.scalar(select(func.count()).where(games.c.engine == "FakeFish")) == 4
    assert n_moves == sum(len(g["moves"].split()) for g in lines[:4])
    assert white_blunders > 0
    assert job.positions == n_moves + 4

    served["games"] = lines[4:6] + lines[:4]  # newest first; 4 already stored
    calls_before = sf.calls
    job2 = go()
    assert (job2.status, job2.total) == ("done", 2)
    assert sf.calls > calls_before
    # Start position and early opening positions repeat across games -> cache hits.
    assert job2.cache_hits > 0


def test_analyse_is_idempotent(run):
    go, sf, served, _ = run
    job = go()
    calls = sf.calls
    tasks.analyse_game(served["games"][0]["id"], job.id, sf=sf)
    assert sf.calls == calls


def test_player_moves_uses_shared_features_and_model(run):
    from app.weakness import player_moves

    go, *_ = run
    go()
    df = player_moves("thibault")
    with engine.connect() as c:
        own = c.scalar(
            select(func.count())
            .select_from(moves.join(games, moves.c.game_id == games.c.id))
            .where(
                ((games.c.white == "thibault") & (moves.c.color == "white"))
                | ((games.c.black == "thibault") & (moves.c.color == "black"))
            )
        )
    assert len(df) == own
    assert df.expected.between(0, 1).all()
    assert df.blunder.sum() > 0


class MateEngine(FakeEngine):
    """Like Stockfish: a checkmated position scores mate 0 for the side to move."""

    def analyse(self, board, limit):
        if board.is_checkmate():
            return {"score": chess.engine.PovScore(chess.engine.Mate(0), board.turn), "pv": []}
        return {"score": chess.engine.PovScore(chess.engine.Cp(0), chess.WHITE), "pv": []}


def test_checkmating_move_is_not_a_blunder():
    for t in (moves, games, jobs, players, position_cache):
        with engine.begin() as c:
            c.execute(delete(t))
    pgn = '[White "alice"]\n[Black "bob"]\n[Result "0-1"]\n\n1. f3 e5 2. g4 Qh4# 0-1\n'
    tasks.save_games("bob", [pgn], "upload")
    job_id, _ = _new_job("bob", "analysing")
    with engine.connect() as c:
        gid = c.scalar(select(games.c.id))
    tasks.analyse_game(gid, job_id, sf=MateEngine())
    with engine.connect() as c:
        last = c.execute(select(moves).where(moves.c.san == "Qh4#")).one()
    assert last.label is None and last.wc_after > 0.99


class ScriptedEngine(FakeEngine):
    """Quick pass sees nothing; the full limit sees white's 4th move (ply 7) losing 400 cp."""

    def __init__(self, target_fen):
        super().__init__()
        self.target, self.limits = target_fen, []

    def analyse(self, board, limit):
        self.limits.append(limit.nodes)
        cp = 0
        if limit.nodes == tasks.settings.engine_nodes and board.fen() == self.target:
            cp = -400
        return {"score": chess.engine.PovScore(chess.engine.Cp(cp), chess.WHITE), "pv": []}


def test_two_pass_rechecks_only_moves_with_a_quick_drop(monkeypatch):
    for t in (moves, games, jobs, players, position_cache):
        with engine.begin() as c:
            c.execute(delete(t))
    monkeypatch.setattr(tasks.settings, "engine_quick_nodes", 1000)
    pgn = (
        '[White "alice"]\n[Black "bob"]\n[Result "*"]\n\n1. e4 e5 2. Nf3 Nc6 3. Bc4 Nf6 4. Ng5 *\n'
    )
    tasks.save_games("alice", [pgn], "upload")
    job_id, _ = _new_job("alice", "analysing")
    board = chess.Board()
    for san in "e4 e5 Nf3 Nc6 Bc4 Nf6 Ng5".split():
        board.push_san(san)
    with engine.connect() as c:
        gid = c.scalar(select(games.c.id))

    # Quick evals are flat: nothing is re-checked, so the full-limit drop stays invisible.
    sf = ScriptedEngine(board.fen())
    tasks.analyse_game(gid, job_id, sf=sf)
    assert set(sf.limits) == {1000}
    with engine.connect() as c:
        assert c.scalar(select(func.count()).where(moves.c.label.is_not(None))) == 0
        assert c.scalar(select(games.c.quick_nodes)) == 1000

    # A quick drop on ply 7 re-checks exactly its two positions at the full limit.
    class QuickDrop(ScriptedEngine):
        def analyse(self, b, limit):
            out = super().analyse(b, limit)
            if limit.nodes == 1000 and b.fen() == self.target:
                out["score"] = chess.engine.PovScore(chess.engine.Cp(-200), chess.WHITE)
            return out

    with engine.begin() as c:
        c.execute(delete(moves))
        c.execute(delete(position_cache))
        c.execute(games.update().values(analysed_at=None))
    sf = QuickDrop(board.fen())
    tasks.analyse_game(gid, job_id, sf=sf)
    assert sf.limits.count(tasks.settings.engine_nodes) == 2
    with engine.connect() as c:
        label = c.scalar(select(moves.c.label).where(moves.c.ply == 7))
    assert label == "blunder"


def test_jobs_take_turns_in_the_analysis_queue(monkeypatch, fake_redis):
    queued = []
    monkeypatch.setattr(
        tasks, "enqueue", lambda q, f, jid, gid, *a, **kw: queued.append((jid, gid))
    )
    monkeypatch.setattr(tasks.settings, "analysis_workers", 2)
    for t in (moves, games, jobs, players):
        with engine.begin() as c:
            c.execute(delete(t))
    for user in ("ann", "ben"):
        pgns = [f'[White "{user}"]\n[Black "x{i}"]\n[Result "*"]\n\n1. e4 *\n' for i in range(5)]
        tasks.save_games(user, pgns, "upload")
    a, _ = _new_job("ann", "analysing")
    b, _ = _new_job("ben", "analysing")
    tasks.enqueue_analysis(a, "ann")
    tasks.enqueue_analysis(b, "ben")
    assert [j for j, _ in queued] == [a, a, b, b]  # only a window per job, not all 5
    tasks._enqueue_next(a)  # one of ann's games finished
    assert queued[-1][0] == a and len(fake_redis.data[tasks._pending_key(a)]) == 2


def test_failed_game_is_retried_once_then_skipped_without_failing_the_job(monkeypatch):
    from types import SimpleNamespace

    queued = []
    monkeypatch.setattr(tasks, "enqueue", lambda *a, **kw: queued.append(kw))
    for t in (moves, games, jobs, players):
        with engine.begin() as c:
            c.execute(delete(t))
    job_id, _ = _new_job("ann", "analysing")
    with engine.begin() as c:
        c.execute(jobs.update().values(total=2, done=1))

    def fail(attempts):
        rq_job = SimpleNamespace(
            origin="analysis", args=("g1", job_id), meta={"job_id": job_id, "attempts": attempts}
        )
        tasks._on_failure(rq_job, None, RuntimeError, RuntimeError("worker died"), None)

    fail(1)
    assert queued == [{"timeout": 1800, "attempts": 2}]
    fail(2)
    with engine.connect() as c:
        job = c.execute(select(jobs)).one()
    assert (job.status, job.done, job.skipped) == ("done", 2, 1)
