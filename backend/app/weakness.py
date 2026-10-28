"""Rank a player's weaknesses: slices where they blunder more than the peer baseline expects.

Per slice: O = observed blunders, E = sum of peer-model blunder probabilities.
The rate ratio r = O/E gets a Gamma(PRIOR, PRIOR) prior centred on 1 ("like your peers"),
so thin slices are shrunk towards 1. Posterior: Gamma(PRIOR + O, PRIOR + E).
"""

import math

import pandas as pd

PRIOR = 5  # integer, so P(r > 1) has an exact Poisson form
MIN_BLUNDERS = 3
MIN_CONFIDENCE = 0.975  # from a null simulation: ~0.18 false findings per player
MAX_EVIDENCE = 5
MAX_SHARE = 0.6  # a slice with most of the player's moves is overall level, not a situation


def clock_bucket(seconds) -> str | None:
    if seconds is None or pd.isna(seconds):
        return None
    for limit, name in [(10, "under 10s"), (30, "10-30s"), (60, "30-60s")]:
        if seconds < limit:
            return name
    return "over 60s"


SLICES = {
    "clock": lambda d: d.clock_before.map(clock_bucket),
    "phase": lambda d: d.phase,
    "piece": lambda d: d.piece,
    "speed": lambda d: d.speed,
    "opening": lambda d: d.opening_family,
    "clock_phase": lambda d: d.clock_before.map(clock_bucket).str.cat(d.phase, sep=" / "),
}


def prob_ratio_above_one(o: float, e: float) -> float:
    """P(r > 1) for r ~ Gamma(shape=PRIOR+o, rate=PRIOR+e), o integer:
    equals P(Poisson(PRIOR+e) < PRIOR+o)."""
    shape, rate = PRIOR + int(o), PRIOR + e
    term = total = math.exp(-rate)
    for i in range(1, shape):
        term *= rate / i
        total += term
    return min(1.0, total)


def eligible_slices(moves: pd.DataFrame):
    """Yield (family, value, slice moves, O, E, P(r>1)) for every slice big enough to test."""
    for family, key in SLICES.items():
        for value, d in moves.groupby(key(moves), dropna=True):
            o, e = int(d.blunder.sum()), float(d.expected.sum())
            if o >= MIN_BLUNDERS and len(d) <= MAX_SHARE * len(moves):
                yield family, value, d, o, e, prob_ratio_above_one(o, e)


def find_weaknesses(moves: pd.DataFrame, top: int = 5) -> list[dict]:
    """moves: one row per move by the player, with feature columns, `opening_family`,
    `blunder` (0/1), `drop` (winning-chance loss), `expected` (peer probability),
    `game_id`, `ply`."""
    found = []
    for family, value, d, o, e, conf in eligible_slices(moves):
        if conf >= MIN_CONFIDENCE:
            ratio = (PRIOR + o) / (PRIOR + e)
            blunders = d[d.blunder == 1]
            # Win-% points lost to blunders beyond the peer expectation (shrunk).
            excess = e * (ratio - 1)
            cost = excess * float(blunders["drop"].mean()) * 50
            found.append(
                {
                    "slice": {"family": family, "value": value},
                    "moves": len(d),
                    "observed_blunders": o,
                    "expected_blunders": round(e, 2),
                    "observed_rate": o / len(d),
                    "expected_rate": e / len(d),
                    "ratio": round(ratio, 2),
                    "confidence": round(conf, 3),
                    "confidence_label": "high" if conf >= 0.99 else "medium",
                    "cost_win_pct": round(cost, 1),
                    "evidence": [
                        f"{r.game_id}:{r.ply}"
                        for r in blunders.nlargest(MAX_EVIDENCE, "drop").itertuples()
                    ],
                }
            )
    found.sort(key=lambda w: w["cost_win_pct"], reverse=True)
    # Overlapping slices (e.g. "under 10s" and "under 10s / endgame") often describe the
    # same blunders; keep the costlier one.
    picked, seen = [], set()
    for w in found:
        ev = set(w["evidence"])
        if len(ev & seen) > len(ev) / 2:
            continue
        picked.append(w)
        seen |= ev
        if len(picked) == top:
            break
    return picked


def player_moves(username: str) -> pd.DataFrame:
    """All analysed moves played by `username`, with features and peer expectations.
    Uses the same parser, feature function and model as training."""
    from sqlalchemy import select

    from app.db import engine, games, moves
    from app.features import move_features
    from app.games import parse_game, read_pgns
    from app.peer_model import expected_blunder
    from app.tasks import player_games

    with engine.connect() as conn:
        gs = conn.execute(
            select(games).where(player_games(username), games.c.analysed_at.is_not(None))
        ).all()
        stored = conn.execute(select(moves).where(moves.c.game_id.in_([g.id for g in gs]))).all()
    by_key = {(m.game_id, m.ply): m for m in stored}
    out = []
    for g in gs:
        meta, rows = parse_game(next(read_pgns(g.pgn)), username)
        color = "white" if g.white == username else "black"
        for r in rows:
            m = by_key[(g.id, r["ply"])]
            r["wc_before"], r["wc_after"], r["label"] = m.wc_before, m.wc_after, m.label
        family = (meta["opening"] or "").split(":")[0] or None
        for r, f in zip(rows, move_features(meta, rows), strict=True):
            if r["color"] != color:
                continue
            out.append(
                {
                    **f,
                    "game_id": g.id,
                    "ply": r["ply"],
                    "opening_family": family,
                    "blunder": int(r["label"] == "blunder"),
                    "drop": r["wc_before"] - r["wc_after"],
                }
            )
    df = pd.DataFrame(out)
    if len(df):
        df["expected"] = expected_blunder(df)
    return df
