import chess
import chess.engine

from app.analysis import classify, win_chances


def pov(cp=None, mate=None):
    s = chess.engine.Mate(mate) if mate is not None else chess.engine.Cp(cp)
    return chess.engine.PovScore(s, chess.WHITE)


def test_win_chances_symmetric_and_clamped():
    assert win_chances(pov(0), chess.WHITE) == 0
    assert win_chances(pov(300), chess.WHITE) == -win_chances(pov(300), chess.BLACK)
    assert win_chances(pov(5000), chess.WHITE) == win_chances(pov(1000), chess.WHITE)


def test_mate_scores_are_extreme_but_finite():
    assert win_chances(pov(mate=1), chess.WHITE) > win_chances(pov(1000), chess.WHITE)
    assert win_chances(pov(mate=-3), chess.WHITE) < -0.99


def test_classify_uses_lichess_thresholds():
    assert classify(0.0, -0.05) is None
    assert classify(0.0, -0.1) == "inaccuracy"
    assert classify(0.0, -0.2) == "mistake"
    assert classify(0.5, 0.2) == "blunder"


def test_mate_in_zero_keeps_who_gave_mate():
    # Black to move and checkmated: white gave mate.
    mated_black = chess.engine.PovScore(chess.engine.Mate(0), chess.BLACK)
    assert win_chances(mated_black, chess.WHITE) > 0.99
    assert win_chances(mated_black, chess.BLACK) < -0.99
