import json
from pathlib import Path

from app.games import USERNAME_RE, parse_game, read_pgns

FIXTURE = Path(__file__).parent / "fixtures" / "games.ndjson"


def fixture_games():
    return [json.loads(line) for line in FIXTURE.read_text().splitlines()]


def test_parses_real_lichess_game():
    g = fixture_games()[0]
    row, moves = parse_game(next(read_pgns(g["pgn"])), "Thibault")
    assert row["id"] == g["id"]
    assert row["speed"] == g["speed"]
    assert row["eco"] == g["opening"]["eco"]
    assert len(moves) == len(g["moves"].split())
    assert [m["ply"] for m in moves[:3]] == [1, 2, 3]
    assert all(m["clock_after"] is not None for m in moves)
    assert moves[0]["time_spent"] is None and moves[2]["time_spent"] >= 0


def test_skips_games_without_the_player():
    g = fixture_games()[0]
    assert parse_game(next(read_pgns(g["pgn"])), "someoneelse") is None


def test_upload_without_lichess_site_gets_stable_hash_id():
    pgn = '[White "a1"]\n[Black "b2"]\n[Result "1-0"]\n\n1. e4 e5 2. Qh5 Nc6 1-0\n'
    a = parse_game(next(read_pgns(pgn)), "a1")
    b = parse_game(next(read_pgns(pgn)), "A1")
    assert a[0]["id"] == b[0]["id"] and a[0]["id"].startswith("u")
    assert a[0]["white"] == "a1" and len(a[1]) == 4


def test_username_validation():
    assert USERNAME_RE.match("Thibault") and USERNAME_RE.match("a-b_c1")
    assert not USERNAME_RE.match("x") and not USERNAME_RE.match("bad name")
    assert not USERNAME_RE.match("../etc")
