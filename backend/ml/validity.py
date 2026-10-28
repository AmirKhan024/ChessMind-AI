"""Predictive validity: do weaknesses found in a player's EARLIER games still show excess
blunders in their LATER, held-out games?

For each player: games sorted by time, first half = discovery, second half = holdout.
Flagged = slices `find_weaknesses` reports on discovery. Control = slices that were
eligible on discovery (enough blunders, not most of the moves) but not flagged.
Compare pooled holdout O/E (observed / peer-expected blunders), CI by resampling players.
Usage: python -m ml.validity DATA_DIR MODEL_DIR"""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from app.peer_model import expected_blunder
from app.weakness import SLICES, eligible_slices, find_weaknesses


def holdout_oe(moves: pd.DataFrame, family: str, value) -> tuple[int, float]:
    d = moves[SLICES[family](moves) == value]
    return int(d.blunder.sum()), float(d.expected.sum())


def pooled(rows: list[dict], reps=2000, seed=0) -> dict:
    if not rows:
        return {"slices": 0}
    df = pd.DataFrame(rows)
    per = df.groupby("player")[["o", "e"]].sum().to_numpy()
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(per), size=(reps, len(per)))
    boot = per[idx, 0].sum(1) / per[idx, 1].sum(1)
    return {
        "slices": len(df),
        "players": int(df.player.nunique()),
        "holdout_o_over_e": float(df.o.sum() / df.e.sum()),
        "ci95": [float(x) for x in np.percentile(boot, [2.5, 97.5])],
        "discovery_o_over_e": float(df.o_disc.sum() / df.e_disc.sum()),
        "share_holdout_ratio_above_1": float((df.o > df.e).mean()),
    }


def main():
    data, model_dir = Path(sys.argv[1]), Path(sys.argv[2])
    rows = pd.read_json(data / "validity_ours.jsonl", lines=True)
    chosen = set(pd.read_json(data / "validity.jsonl", lines=True).player)
    rows = rows[rows.player.isin(chosen)].copy()  # the player's own moves only
    rows["expected"] = expected_blunder(rows, model_dir)

    flagged, control, overall, n_found = [], [], [], {}
    for player, mv in rows.groupby("player"):
        order = mv.groupby("game_id").played_at.first().sort_values().index.tolist()
        half = len(order) // 2
        disc = mv[mv.game_id.isin(order[:half])]
        hold = mv[mv.game_id.isin(order[half:])]
        overall.append(
            {
                "player": player,
                "o": hold.blunder.sum(),
                "e": hold.expected.sum(),
                "o_disc": disc.blunder.sum(),
                "e_disc": disc.expected.sum(),
            }
        )
        found = {(w["slice"]["family"], w["slice"]["value"]) for w in find_weaknesses(disc)}
        n_found[player] = len(found)
        for family, value, _, o_d, e_d, _ in eligible_slices(disc):
            o, e = holdout_oe(hold, family, value)
            if e == 0:
                continue
            row = {"player": player, "o": o, "e": e, "o_disc": o_d, "e_disc": e_d}
            (flagged if (family, value) in found else control).append(row)

    report = {
        "players": len(n_found),
        "games_per_player": int(rows.groupby("player").game_id.nunique().median()),
        "moves": len(rows),
        "players_with_findings": sum(v > 0 for v in n_found.values()),
        "findings_per_player": float(np.mean(list(n_found.values()))),
        "flagged": pooled(flagged),
        "control_eligible_not_flagged": pooled(control),
        "overall_player_level": pooled(overall),
        "speeds": rows.speed.value_counts(normalize=True).round(3).to_dict(),
    }
    (model_dir / "validity.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
