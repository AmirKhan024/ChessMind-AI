"""Per-move features. The ONLY feature code: used to train the peer baseline on bulk
games and to score a user's games at serving time (no train/serve skew).

Input: a game row and its move rows from `games.parse_game`, where every move row also
carries `wc_before` (winning chances -1..1 from the mover's view, before the move)."""

import chess

from app.games import time_control

PIECE_VALUES = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9}
SPEEDS = ["bullet", "blitz", "rapid", "classical"]
PIECES = ["P", "N", "B", "R", "Q", "K"]
PHASES = ["opening", "middlegame", "endgame"]
CATEGORICAL = {"speed": SPEEDS, "piece": PIECES, "phase": PHASES}
NUMERIC = [
    "elo",
    "elo_diff",
    "move_number",
    "initial_secs",
    "increment",
    "clock_before",
    "clock_frac",
    "time_spent",
    "material_diff",
    "is_capture",
    "gives_check",
    "in_check",
    "n_legal",
    "wc_before",
]
FEATURES = NUMERIC + list(CATEGORICAL)


def phase(board: chess.Board, ply: int) -> str:
    """Endgame: at most 13 points of non-pawn material left (e.g. R+B v R).
    Opening: first 10 moves each, unless already an endgame."""
    material = sum(
        PIECE_VALUES[p.piece_type]
        for p in board.piece_map().values()
        if p.piece_type not in (chess.PAWN, chess.KING)
    )
    if material <= 13:
        return "endgame"
    return "opening" if ply <= 20 else "middlegame"


def _material(board: chess.Board, color: chess.Color) -> int:
    return sum(
        PIECE_VALUES.get(p.piece_type, 0) * (1 if p.color == color else -1)
        for p in board.piece_map().values()
    )


def move_features(game: dict, rows: list[dict]) -> list[dict]:
    initial, inc = time_control(game.get("time_control") or "")
    clock = {"white": initial, "black": initial}
    out = []
    for r in rows:
        board = chess.Board(r["fen_before"])
        move = chess.Move.from_uci(r["uci"])
        me = board.turn
        elo = game["white_elo"] if me == chess.WHITE else game["black_elo"]
        opp = game["black_elo"] if me == chess.WHITE else game["white_elo"]
        before = clock[r["color"]]
        out.append(
            {
                "speed": game.get("speed"),
                "elo": elo,
                "elo_diff": (elo - opp) if elo is not None and opp is not None else None,
                "move_number": (r["ply"] + 1) // 2,
                "initial_secs": initial,
                "increment": inc,
                "clock_before": before,
                "clock_frac": before / initial if before is not None and initial else None,
                "time_spent": r["time_spent"],
                "material_diff": _material(board, me),
                "piece": board.piece_at(move.from_square).symbol().upper(),
                "is_capture": int(board.is_capture(move)),
                "gives_check": int(board.gives_check(move)),
                "in_check": int(board.is_check()),
                "n_legal": board.legal_moves.count(),
                "phase": phase(board, r["ply"]),
                "wc_before": r["wc_before"],
            }
        )
        if r["clock_after"] is not None:
            clock[r["color"]] = r["clock_after"]
    return out
