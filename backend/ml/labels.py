"""Feature + label rows for bulk games, from any source of evals (Lichess %eval or our
engine). Shared by the sampler (training data) and the re-labeller (our-engine data)."""

import chess
import chess.pgn

from app.analysis import classify, win_chances
from app.features import move_features
from app.games import parse_game, read_pgns

SPEED_OK = {"bullet", "blitz", "rapid", "classical"}


def lichess_wc(game: chess.pgn.Game) -> list[float | None]:
    """White's winning chances for every position (start + after each ply) from %eval.
    The start position has no eval in Lichess PGNs."""
    out = [None]
    board = game.board()
    for node in game.mainline():
        board.push(node.move)
        ev = node.eval()
        if ev is not None:
            out.append(win_chances(ev, chess.WHITE))
        elif board.is_checkmate():
            out.append(1.0 if board.turn == chess.BLACK else -1.0)
        else:
            out.append(None)
    return out


def labelled_moves(pgn: str) -> list[dict]:
    """Moves of one evaluated game, labelled from Lichess's own %eval comments."""
    game = next(read_pgns(pgn))
    return labelled_rows(game, lichess_wc(game))


def labelled_rows(game: chess.pgn.Game, wc_white: list[float | None]) -> list[dict]:
    """Feature + label rows for one game, given white's winning chances per position."""
    parsed = parse_game(game, None)
    if parsed is None:
        return []
    g, rows = parsed
    if g["speed"] not in SPEED_OK or g["white_elo"] is None or g["black_elo"] is None:
        return []
    for i, r in enumerate(rows):
        sign = 1 if r["color"] == "white" else -1
        before, after = wc_white[i], wc_white[i + 1]
        r["wc_before"] = None if before is None else sign * before
        r["wc_after"] = None if after is None else sign * after
    feats = move_features(g, rows)
    family = (g["opening"] or "").split(":")[0] or None
    out = []
    for r, f in zip(rows, feats, strict=True):
        if r["wc_before"] is None or r["wc_after"] is None:
            continue
        label = classify(r["wc_before"], r["wc_after"])
        out.append(
            {
                "game_id": g["id"],
                "ply": r["ply"],
                "player": g[r["color"]],
                "played_at": g["played_at"],
                "opening_family": family,
                **f,
                "drop": r["wc_before"] - r["wc_after"],
                "label": label,
                "blunder": int(label == "blunder"),
            }
        )
    return out
