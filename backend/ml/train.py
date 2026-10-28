"""Train and evaluate the peer baseline P(blunder | features).

Player-level split: 20% of players are test players; every game they played is removed
from training, so no test player (or their games) is seen in training.

Usage: python -m ml.train DATA_DIR OUT_MODEL_DIR [--docs ../docs]
"""

import argparse
import json
from pathlib import Path

import lightgbm as lgb
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from sklearn.compose import ColumnTransformer  # noqa: E402
from sklearn.impute import SimpleImputer  # noqa: E402
from sklearn.linear_model import LogisticRegression  # noqa: E402
from sklearn.metrics import brier_score_loss, log_loss  # noqa: E402
from sklearn.pipeline import make_pipeline  # noqa: E402
from sklearn.preprocessing import OneHotEncoder, StandardScaler  # noqa: E402

from app.features import CATEGORICAL, FEATURES, NUMERIC  # noqa: E402
from app.peer_model import to_frame  # noqa: E402

SEED = 0
BANDS = [0, 1200, 1500, 1800, 2100, 4000]


def split(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(SEED)
    players = df.player.unique()
    test_players = set(rng.choice(players, size=len(players) // 5, replace=False))
    test_games = set(df.loc[df.player.isin(test_players), "game_id"])
    test = df[df.player.isin(test_players)]
    train = df[~df.game_id.isin(test_games)]
    return train, test


def ece(y, p, bins=10) -> float:
    """Expected calibration error over equal-count bins."""
    order = np.argsort(p)
    err = 0.0
    for chunk in np.array_split(order, bins):
        err += len(chunk) * abs(y[chunk].mean() - p[chunk].mean())
    return err / len(y)


def metrics(y, p) -> dict:
    return {
        "n": int(len(y)),
        "rate": float(y.mean()),
        "log_loss": float(log_loss(y, p, labels=[0, 1])),
        "brier": float(brier_score_loss(y, p)),
        "ece": float(ece(y, p)),
    }


def logistic():
    pre = ColumnTransformer(
        [
            (
                "num",
                make_pipeline(
                    SimpleImputer(strategy="median", add_indicator=True), StandardScaler()
                ),
                NUMERIC,
            ),
            ("cat", OneHotEncoder(handle_unknown="ignore"), list(CATEGORICAL)),
        ]
    )
    return make_pipeline(pre, LogisticRegression(max_iter=2000))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("data")
    ap.add_argument("out")
    ap.add_argument("--docs", default=None)
    args = ap.parse_args()

    df = pd.read_parquet(Path(args.data) / "moves.parquet")
    train, test = split(df)
    y_tr, y_te = train.blunder.to_numpy(), test.blunder.to_numpy()
    print(f"train moves={len(train)} test moves={len(test)} test players={test.player.nunique()}")

    results, preds = {}, {}
    preds["constant"] = np.full(len(test), y_tr.mean())

    lr = logistic().fit(train[FEATURES], y_tr)
    preds["logistic"] = lr.predict_proba(test[FEATURES])[:, 1]

    X_tr, X_te = to_frame(train), to_frame(test)
    gbm = lgb.LGBMClassifier(
        n_estimators=400,
        learning_rate=0.05,
        num_leaves=63,
        min_child_samples=200,
        subsample=0.8,
        subsample_freq=1,
        colsample_bytree=0.8,
        random_state=SEED,
        verbose=-1,
    )
    gbm.fit(X_tr, y_tr)
    preds["lightgbm"] = gbm.predict_proba(X_te)[:, 1]

    band = pd.cut(test.elo, BANDS).astype(str).to_numpy()
    speed = test.speed.to_numpy()
    for name, p in preds.items():
        r = {"overall": metrics(y_te, p), "by_band": {}, "by_speed": {}}
        for b in sorted(set(band)):
            m = band == b
            r["by_band"][b] = metrics(y_te[m], p[m])
        for s in sorted(set(speed)):
            m = speed == s
            r["by_speed"][s] = metrics(y_te[m], p[m])
        results[name] = r
        o = r["overall"]
        print(f"{name:9s} log_loss={o['log_loss']:.4f} brier={o['brier']:.5f} ece={o['ece']:.4f}")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    gbm.booster_.save_model(str(out / "model.txt"))
    (out / "metrics.json").write_text(
        json.dumps(
            {
                "data": str(args.data),
                "train_moves": len(train),
                "test_moves": len(test),
                "features": FEATURES,
                "results": results,
            },
            indent=2,
        )
    )

    if args.docs:
        fig, ax = plt.subplots(figsize=(5, 5))
        for name in ["logistic", "lightgbm"]:
            p = preds[name]
            order = np.argsort(p)
            xs, ys = zip(
                *[(p[c].mean(), y_te[c].mean()) for c in np.array_split(order, 20)], strict=True
            )
            ax.plot(xs, ys, marker="o", label=name)
        lim = max(ax.get_xlim()[1], ax.get_ylim()[1])
        ax.plot([0, lim], [0, lim], "k--", lw=1, label="perfect")
        ax.set_xlabel("predicted blunder probability")
        ax.set_ylabel("observed blunder rate")
        ax.set_title("Peer baseline calibration (held-out players)")
        ax.legend()
        fig.tight_layout()
        fig.savefig(Path(args.docs) / "calibration.png", dpi=120)


if __name__ == "__main__":
    main()
