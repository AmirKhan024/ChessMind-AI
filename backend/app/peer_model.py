"""Peer baseline model: expected blunder probability for a move, given its features.
`to_frame` is shared by training and serving so categorical encodings always match.

The model is trained on labels from Lichess's %eval; our serving labels come from our own
engine limit, which flags more blunders. `calibration.json` holds a logit shift fitted on
games labelled both ways, so expected rates match OUR labels (see ml/calibrate.py)."""

import json
from functools import cache
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from app.features import CATEGORICAL, FEATURES

MODEL_DIR = Path(__file__).parent.parent / "models" / "peer_baseline"


def to_frame(rows) -> pd.DataFrame:
    df = pd.DataFrame(rows)[FEATURES].copy()
    for col, cats in CATEGORICAL.items():
        df[col] = pd.Categorical(df[col], categories=cats)
    for col in FEATURES:
        if col not in CATEGORICAL:
            df[col] = pd.to_numeric(df[col], errors="coerce").astype(float)
    return df


@cache
def _booster(model_dir: Path = MODEL_DIR) -> lgb.Booster:
    return lgb.Booster(model_file=str(model_dir / "model.txt"))


@cache
def _shift(model_dir: Path = MODEL_DIR) -> float:
    path = model_dir / "calibration.json"
    return json.loads(path.read_text())["logit_shift"] if path.exists() else 0.0


def shifted(p: np.ndarray, shift: float) -> np.ndarray:
    p = np.clip(p, 1e-9, 1 - 1e-9)
    return 1 / (1 + np.exp(-(np.log(p / (1 - p)) + shift)))


def expected_blunder(rows, model_dir: Path = MODEL_DIR) -> np.ndarray:
    raw = _booster(model_dir).predict(to_frame(rows))
    return shifted(raw, _shift(model_dir))
