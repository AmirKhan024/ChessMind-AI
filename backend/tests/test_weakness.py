import numpy as np
import pandas as pd

from app.weakness import find_weaknesses, prob_ratio_above_one


def moves(n=600, seed=0, low_clock_rate=0.05):
    rng = np.random.default_rng(seed)
    clock = rng.choice([5.0, 45.0, 120.0], size=n)
    expected = np.full(n, 0.05)
    rate = np.where(clock < 10, low_clock_rate, 0.05)
    return pd.DataFrame(
        {
            "clock_before": clock,
            "phase": rng.choice(["opening", "middlegame", "endgame"], size=n),
            "piece": rng.choice(list("PNBRQK"), size=n),
            "speed": "blitz",
            "opening_family": "Sicilian Defense",
            "blunder": (rng.random(n) < rate).astype(int),
            "drop": rng.uniform(0.3, 0.8, size=n),
            "expected": expected,
            "game_id": [f"g{i // 40}" for i in range(n)],
            "ply": np.arange(n) % 40 + 1,
        }
    )


def test_finds_planted_time_pressure_weakness():
    found = find_weaknesses(moves(low_clock_rate=0.3))
    assert found
    top = found[0]
    assert "under 10s" in top["slice"]["value"]
    assert top["ratio"] > 2 and top["confidence"] >= 0.975
    assert all(e.count(":") == 1 for e in top["evidence"])


def test_few_findings_when_player_matches_peers():
    assert sum(len(find_weaknesses(moves(seed=s))) for s in range(20)) <= 8


def test_confidence_shrinks_thin_slices():
    assert prob_ratio_above_one(3, 1.0) < prob_ratio_above_one(30, 10.0)
    assert prob_ratio_above_one(0, 5.0) < 0.5


def test_shipped_model_predicts_sane_probabilities():
    from app.features import FEATURES
    from app.peer_model import expected_blunder

    row = dict.fromkeys(FEATURES, 1.0) | {"speed": "blitz", "piece": "N", "phase": "middlegame"}
    p = expected_blunder([row, row | {"clock_before": 3.0, "clock_frac": 0.01}])
    assert ((p > 0) & (p < 1)).all()
