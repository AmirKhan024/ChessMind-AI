"""Fit the logit shift that maps the Lichess-label model to OUR engine's labels, on evaluated
games labelled both ways; then test calibration on random games WITHOUT %eval labelled by
our engine (the serving condition, and a selection-bias check).
Usage: python -m ml.calibrate DATA_DIR MODEL_DIR"""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from app.peer_model import _booster, shifted, to_frame
from ml.train import metrics


def observed_over_expected(y, p, groups, reps=2000, seed=0):
    """O/E with a 95% bootstrap CI that resamples whole groups (games or players),
    since moves within a game are not independent."""
    g = pd.DataFrame({"y": y, "p": p, "g": groups}).groupby("g")[["y", "p"]].sum().to_numpy()
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(g), size=(reps, len(g)))
    boot = g[idx, 0].sum(1) / g[idx, 1].sum(1)
    ci = [float(x) for x in np.percentile(boot, [2.5, 97.5])]
    return {"o_over_e": float(g[:, 0].sum() / g[:, 1].sum()), "ci95": ci}


def main():
    data, model_dir = Path(sys.argv[1]), Path(sys.argv[2])
    booster = _booster(model_dir)
    fit = pd.read_json(data / "consistency_ours.jsonl", lines=True)
    test = pd.read_json(data / "random_ours.jsonl", lines=True)
    p_fit = booster.predict(to_frame(fit))
    y_fit = fit.blunder.to_numpy()

    lo, hi = -2.0, 2.0  # bisection: total expected == total observed
    for _ in range(60):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if shifted(p_fit, mid).sum() < y_fit.sum() else (lo, mid)
    shift = (lo + hi) / 2

    y, games = test.blunder.to_numpy(), test.game_id.to_numpy()
    p_raw = booster.predict(to_frame(test))
    p_cal = shifted(p_raw, shift)
    report = {
        "logit_shift": shift,
        "fit_set": {
            "games": int(fit.game_id.nunique()),
            "moves": len(fit),
            "our_blunders": int(y_fit.sum()),
            "lichess_label_blunders": int((fit.lichess_label == "blunder").sum()),
        },
        "test_set": {"games": int(test.game_id.nunique()), "moves": len(test)},
        "test_raw": {**metrics(y, p_raw), **observed_over_expected(y, p_raw, games)},
        "test_calibrated": {**metrics(y, p_cal), **observed_over_expected(y, p_cal, games)},
        "test_calibrated_by_speed": {
            s: {"moves": int(m.sum()), **observed_over_expected(y[m], p_cal[m], games[m])}
            for s in sorted(test.speed.unique())
            if (m := (test.speed == s).to_numpy()).sum() > 500
        },
    }
    (model_dir / "calibration.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
