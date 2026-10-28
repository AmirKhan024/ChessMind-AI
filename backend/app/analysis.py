"""Move labels from Stockfish evals, using Lichess's win-chance model (lila Advice.scala)."""

import math

import chess
import chess.engine

# Thresholds on the winning-chances scale (-1..1), as in lila CpAdvice.
JUDGEMENTS = [(0.3, "blunder"), (0.2, "mistake"), (0.1, "inaccuracy")]


def win_chances(score: chess.engine.PovScore, pov: chess.Color) -> float:
    s = score.pov(pov)
    mate = s.mate()
    if mate is not None:
        cp = (21 - min(10, abs(mate))) * 100 * (1 if mate > 0 else -1)
    else:
        cp = max(-1000, min(1000, s.score()))
    return 2 / (1 + math.exp(-0.00368208 * cp)) - 1


def classify(wc_before: float, wc_after: float) -> str | None:
    """Both values from the mover's point of view."""
    drop = wc_before - wc_after
    return next((label for t, label in JUDGEMENTS if drop >= t), None)
