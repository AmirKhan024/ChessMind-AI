import pytest

from app import puzzles
from app.grounding import parse_slice_label, slice_label


def puzzle(pid, rating, themes, openings=(), popularity=90):
    return {
        "id": pid,
        "fen": "",
        "moves": [],
        "rating": rating,
        "popularity": popularity,
        "themes": set(themes),
        "openings": {puzzles.opening_key(o) for o in openings},
    }


@pytest.fixture
def pool(monkeypatch):
    items = [
        puzzle("end1", 1500, ["endgame", "short"], popularity=80),
        puzzle("end2", 1520, ["endgame", "long"], popularity=99),
        puzzle("end3", 2400, ["endgame"]),
        puzzle("mid1", 1500, ["middlegame"]),
        puzzle("sic1", 1450, ["middlegame"], ["Sicilian_Defense", "Sicilian_Defense_Najdorf"]),
    ]
    monkeypatch.setattr(puzzles, "load", lambda: items)
    return items


@pytest.mark.parametrize(
    "s",
    [
        {"family": "clock_phase", "value": "under 10s / endgame"},
        {"family": "opening", "value": "Sicilian Defense"},
        {"family": "piece", "value": "Q"},
    ],
)
def test_slice_label_round_trip(s):
    assert parse_slice_label(slice_label(s)) == s


def test_opening_key_matches_puzzle_tags():
    assert puzzles.opening_key("King's Pawn Game") == puzzles.opening_key("Kings_Pawn_Game")


def test_drills_near_rating_by_popularity(pool):
    d = puzzles.drills("phase", "endgame", 1500, n=2)
    assert [p["id"] for p in d["puzzles"]] == ["end2", "end1"]
    assert d["themes"] == ["endgame"]


def test_clock_phase_prefers_matching_clock_theme(pool):
    d = puzzles.drills("clock_phase", "under 10s / endgame", 1500, n=1)
    assert [p["id"] for p in d["puzzles"]] == ["end1"]


def test_opening_slice_uses_opening_tags(pool):
    d = puzzles.drills("opening", "Sicilian Defense", 1500)
    assert [p["id"] for p in d["puzzles"]] == ["sic1"]


def test_widens_rating_window_when_too_few(pool):
    d = puzzles.drills("phase", "endgame", 2000, n=3)
    assert {p["id"] for p in d["puzzles"]} == {"end1", "end2", "end3"}


def test_every_mapped_theme_is_kept_by_the_subset_builder():
    for table in (puzzles.PHASE_THEMES, puzzles.CLOCK_THEMES, puzzles.PIECE_THEMES):
        for themes in table.values():
            assert set(themes) <= set(puzzles.ALL_THEMES)


def test_excluded_puzzles_are_not_repeated(pool):
    d = puzzles.drills("phase", "endgame", 1500, n=2, exclude={"end2"})
    assert [p["id"] for p in d["puzzles"]] == ["end1"]


def test_display_label_spells_out_pieces():
    assert puzzles.display_label("piece", "N", "piece moved: N") == "piece moved: knight"
    assert puzzles.display_label("phase", "endgame", "game phase: endgame") == (
        "game phase: endgame"
    )
