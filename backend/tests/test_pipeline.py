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
