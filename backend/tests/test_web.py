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
    assert "3 of 10 done" in r.text and "7 tasks from other players ahead" in r.text
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
                    "weaknesses": [weakness | {"compared_with": "own level"}],
                    "breakdown": [
                        {
                            "slice": "game phase: endgame",
                            "moves": 60,
                            "blunders": 6,
                            "rate_pct": 10.0,
                            "peer_rate_pct": 5.0,
                            "ratio_vs_peers": 2.0,
                            "examples": [],
                        }
                    ],
                    "peer_data_note": "Very few players at your rating.",
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
    assert "relative weak spot" in r.text and "2.5x your usual rate" in r.text
    assert "Where your blunders happen" in r.text and "game phase: endgame" in r.text
    assert "Very few players at your rating." in r.text
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


def test_ui_errors_are_html_and_api_errors_stay_json():
    r = client.get("/ui/reports/nope")
    assert r.status_code == 404 and "report not found" in r.text and "<html" in r.text
    r = client.post("/ui/players/nobody/report")
    assert r.status_code == 404 and "<html" in r.text
    r = client.get("/reports/nope")
    assert r.status_code == 404 and r.json() == {"detail": "report not found"}


def test_report_is_queued_then_pending_page_until_written(monkeypatch):
    queued = []
    monkeypatch.setattr(tasks, "enqueue_report", lambda u, rid: queued.append((u, rid)))
    assert client.post("/players/alice/report").status_code == 404  # nothing analysed yet
    with engine.begin() as c:
        c.execute(
            games.insert().values(
                id="g9", source="upload", pgn="", white="alice", black="bob", analysed_at=None
            )
        )
        c.execute(games.update().values(analysed_at=text("now()")))
    r = client.post("/ui/players/alice/report", follow_redirects=False)
    assert r.status_code == 303 and queued[0][0] == "alice"
    rid = queued[0][1]
    assert r.headers["location"] == f"/ui/reports/{rid}"
    monkeypatch.setattr(tasks, "report_status", lambda i: "queued")
    monkeypatch.setattr(tasks, "queue_position", lambda i, s: 2)
    page = client.get(f"/ui/reports/{rid}")
    assert "Writing your report" in page.text and "2 reports from other players" in page.text
    assert client.get(f"/reports/{rid}").status_code == 202
    monkeypatch.setattr(tasks, "report_status", lambda i: "failed")
    assert client.get(f"/ui/reports/{rid}").status_code == 500


def test_delete_my_data_removes_games_reports_and_player():
    main._new_job("alice", "done")
    with engine.begin() as c:
        c.execute(games.insert().values(id="g1", source="upload", pgn="", white="alice", black="b"))
        c.execute(games.insert().values(id="g2", source="upload", pgn="", white="c", black="d"))
    r = client.delete("/players/Alice")
    assert r.json() == {"username": "alice", "games_deleted": 1, "reports_deleted": 0}
    with engine.connect() as c:
        assert c.execute(text("SELECT id FROM games")).scalars().all() == ["g2"]
        assert c.execute(text("SELECT count(*) FROM jobs")).scalar() == 0


def test_rate_limit_per_ip_returns_429(monkeypatch):
    monkeypatch.setitem(main.RATE_LIMITS, "analyse", 2)
    codes = [client.post("/ui/analyse", data={"username": "-bad-"}).status_code for _ in range(3)]
    assert codes == [422, 422, 429]
    r = client.post("/players/-bad-/analyse")
    assert r.status_code == 429 and r.json() == {
        "detail": "Too many requests. Please wait a minute."
    }
    assert client.get("/").status_code == 200  # reads are not limited


def test_metrics_endpoint_exposes_request_latency(monkeypatch):
    monkeypatch.setattr(main.metrics, "_queue_depths", lambda: {"analysis": 0})
    client.get("/")
    out = client.get("/metrics").text
    assert 'chessmind_http_request_seconds_count{method="GET",route="/"}' in out


def test_redis_outage_gives_503_not_500(monkeypatch):
    import redis

    def down(*a, **kw):
        raise redis.ConnectionError("redis down")

    monkeypatch.setattr(tasks, "enqueue", down)
    r = client.post("/players/alice/analyse")
    assert r.status_code == 503 and "queue is unavailable" in r.json()["detail"]
    r = client.post("/ui/analyse", data={"username": "alice"})
    assert r.status_code == 503 and "<html" in r.text
