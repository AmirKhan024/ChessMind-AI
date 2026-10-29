"""Model quality gate: a new peer model may not be worse than the registered one.

The training data is not in the repo, so a fixed sample of held-out moves (players never
seen in training, same split as ml/train.py) is committed as models/eval/holdout.csv.gz.
`gate.json` holds the registered model's scores on it. CI re-scores the shipped model and
fails if it does not match gate.json (a model swapped in without going through the gate)
or breaks the limits below.

  python -m ml.gate build DATA_DIR          # once: write the holdout sample (needs ml deps)
  python -m ml.gate check CANDIDATE_DIR     # score a candidate against the registered model
  python -m ml.gate register CANDIDATE_DIR  # check, then copy it in and record its scores
"""

import json
import shutil
import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from app.features import FEATURES
from app.peer_model import MODEL_DIR, to_frame

EVAL_FILE = MODEL_DIR.parent / "eval" / "holdout.csv.gz"
GATE_FILE = MODEL_DIR / "gate.json"
SAMPLE_MOVES = 30_000
# A candidate may be at most this much worse than the registered model on each score.
TOLERANCE = {"log_loss": 0.002, "brier": 0.0005, "ece": 0.003}
MAX_ECE = 0.01  # calibration matters most: expected rates are used directly


def ece(y: np.ndarray, p: np.ndarray, bins: int = 10) -> float:
    """Expected calibration error over equal-count bins (same as ml/train.py)."""
    chunks = np.array_split(np.argsort(p), bins)
    return sum(len(c) * abs(y[c].mean() - p[c].mean()) for c in chunks) / len(y)


def score(model_dir: Path) -> dict:
    df = pd.read_csv(EVAL_FILE)
    y = df.blunder.to_numpy()
    # Raw model output: holdout labels come from Lichess %eval, like the training labels.
    p = np.clip(
        lgb.Booster(model_file=str(model_dir / "model.txt")).predict(to_frame(df)), 1e-9, 1 - 1e-9
    )
    return {
        "n": int(len(y)),
        "log_loss": float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))),
        "brier": float(np.mean((p - y) ** 2)),
        "ece": float(ece(y, p)),
    }


def failures(candidate: dict, registered: dict) -> list[str]:
    out = [
        f"{k} {candidate[k]:.5f} is worse than registered {registered[k]:.5f} (+{tol})"
        for k, tol in TOLERANCE.items()
        if candidate[k] > registered[k] + tol
    ]
    if candidate["ece"] > MAX_ECE:
        out.append(f"ece {candidate['ece']:.5f} is above the limit {MAX_ECE}")
    return out


def build(data_dir: str) -> None:
    from ml.train import split

    _, test = split(pd.read_parquet(Path(data_dir) / "moves.parquet"))
    sample = test.sample(n=min(SAMPLE_MOVES, len(test)), random_state=0)
    EVAL_FILE.parent.mkdir(exist_ok=True)
    sample[FEATURES + ["blunder"]].to_csv(EVAL_FILE, index=False)
    print(f"wrote {len(sample)} held-out moves to {EVAL_FILE}")


def main() -> None:
    cmd, arg = sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None
    if cmd == "build":
        build(arg)
        return
    candidate = score(Path(arg))
    registered = json.loads(GATE_FILE.read_text()) if GATE_FILE.exists() else None
    problems = failures(candidate, registered) if registered else []
    print(json.dumps({"candidate": candidate, "registered": registered}, indent=2))
    if problems:
        print("GATE FAILED:\n- " + "\n- ".join(problems))
        sys.exit(1)
    print("gate passed")
    if cmd == "register":
        if Path(arg).resolve() != MODEL_DIR.resolve():
            shutil.copy(Path(arg) / "model.txt", MODEL_DIR / "model.txt")
            shutil.copy(Path(arg) / "metrics.json", MODEL_DIR / "metrics.json")
        GATE_FILE.write_text(json.dumps(candidate, indent=2) + "\n")
        print(f"registered; scores written to {GATE_FILE}")


if __name__ == "__main__":
    main()
