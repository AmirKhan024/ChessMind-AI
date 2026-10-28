"""Server-rendered pages against a real Postgres; queue and LLM are stubbed."""

import uuid
from types import SimpleNamespace

import chess
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, text

from app import main, puzzles, tasks
from app.db import engine, games, jobs, moves, players, reports

try:
    with engine.connect() as c:
        c.execute(text("SELECT 1 FROM reports LIMIT 1"))
except Exception:  # noqa: BLE001
    pytest.skip("Postgres with migrated schema not available", allow_module_level=True)

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def clean():
    for t in (reports, moves, games, jobs, players):
        with engine.begin() as c:
            c.execute(delete(t))


def test_index_and_bad_username_rerenders_form_with_error():
    assert client.get("/").status_code == 200
    r = client.post("/ui/analyse", data={"username": "-bad-"})
    assert r.status_code == 422
    assert "invalid username" in r.text and 'action="/ui/analyse"' in r.text


def test_job_page_shows_progress_queue_position_and_refresh(monkeypatch):
    monkeypatch.setattr(tasks, "queue_position", lambda job_id, status: 7)
    job_id, _ = main._new_job("alice", "analysing")
    with engine.begin() as c:
        c.execute(jobs.update().values(total=10, done=3))
    r = client.get(f"/ui/jobs/{job_id}")
    assert r.status_code == 200
    assert "3 of 10 done" in r.text and "7 tasks ahead" in r.text
    assert 'http-equiv="refresh"' in r.text
    with engine.begin() as c:
        c.execute(jobs.update().values(status="done", done=10))
    r = client.get(f"/ui/jobs/{job_id}")
    assert "refresh" not in r.text and "/ui/players/alice/report" in r.text


def test_upload_with_no_games_for_that_player_is_rejected():
    pgn = '[White "bob"]\n[Black "carol"]\n[Result "1-0"]\n\n1. e4 e5 1-0\n'
    r = client.post(
        "/ui/upload", data={"username": "dave"}, files={"file": ("g.pgn", pgn.encode())}
    )
    assert r.status_code == 422 and "No new games by dave" in r.text


def test_report_page_draws_cited_positions_and_drills(monkeypatch):
    board = chess.Board()
    board.push_san("e4")
    fen = board.fen()
    with engine.begin() as c:
        c.execute(
            games.insert().values(id="g1", source="upload", pgn="", white="bob", black="alice")
        )
        c.execute(
            moves.insert().values(
                game_id="g1",
                ply=2,
                color="black",
                fen_before=fen,
                uci="f7f6",
                san="f6",
                wc_before=0.0,
                wc_after=-0.5,
                best_uci="e7e5",
                label="blunder",
            )
        )
    example = {
        "id": "g1:2",
        "move_number": 1,
        "played": "f6",
        "best": "e5",
        "win_before": 50,
        "win_after": 25,
        "clock_s": None,
    }
    weakness = {
        "id": "W1",
        "slice": "game phase: opening",
        "moves": 10,
        "observed_blunders": 4,
        "expected_blunders": 1.0,
        "ratio": 2.5,
        "confidence_pct": 99,
        "observed_rate_pct": 40.0,
        "expected_rate_pct": 10.0,
        "games_lost_equivalent": 0.5,
        "examples": [example],
    }
    rid = str(uuid.uuid4())
    with engine.begin() as c:
        c.execute(
            reports.insert().values(
                id=rid,
                username="alice",
                method="template",
                attempts=0,
                prompt_version="coach-v1",
                verification=[],
                facts={
                    "games": 1,
                    "moves": 10,
                    "overall_observed_blunders": 4,
                    "overall_expected_blunders": 1.0,
                    "weaknesses": [weakness],
                },
                report={
                    "summary": "Summary <b>text</b>.",
                    "claims": [{"weakness": "W1", "text": "Claim.", "evidence": ["g1:2"]}],
                },
            )
        )
    drill = {
        "id": "abc12",
        "fen": chess.STARTING_FEN,
        "moves": ["e2e4", "e7e5"],
        "rating": 1500,
        "popularity": 95,
        "themes": {"opening"},
        "openings": set(),
    }
    monkeypatch.setattr(puzzles, "load", lambda: [drill])
    r = client.get(f"/ui/reports/{rid}")
    assert r.status_code == 200
    assert r.text.count("<svg") == 2  # one example position, one drill puzzle
    assert "https://lichess.org/training/abc12" in r.text
    assert "Summary &lt;b&gt;text&lt;/b&gt;." in r.text  # LLM text is escaped
    assert "game phase: opening" in r.text and "engine preferred" in r.text
    assert client.get(f"/ui/reports/{uuid.uuid4()}").status_code == 404


def test_queue_position_counts_jobs_ahead(monkeypatch):
    queued = [{"job_id": "other"}, {"job_id": "other"}, {"job_id": "mine"}, {"job_id": "mine"}]
    monkeypatch.setattr(
        tasks.analysis_queue, "get_job_ids", lambda: [str(i) for i in range(len(queued))]
    )
    monkeypatch.setattr(
        tasks.Job,
        "fetch_many",
        lambda ids, connection: [SimpleNamespace(meta=queued[int(i)]) for i in ids],
    )
    assert tasks.queue_position("mine", "analysing") == 2
    assert tasks.queue_position("absent", "analysing") is None
    assert tasks.queue_position("mine", "done") is None
