"""PGN parsing shared by the Lichess fetch and the PGN upload paths."""

import hashlib
import io
import re
from collections.abc import Iterator
from datetime import UTC, datetime

import chess
import chess.pgn

USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,28}[A-Za-z0-9]$")
_LICHESS_SITE = re.compile(r"lichess\.org/(?:game/)?([A-Za-z0-9]{8})")


def read_pgns(text: str) -> Iterator[chess.pgn.Game]:
    stream = io.StringIO(text)
    while (game := chess.pgn.read_game(stream)) is not None:
        yield game


def parse_game(game: chess.pgn.Game, username: str | None) -> tuple[dict, list[dict]] | None:
    """Return (game row, move rows) or None if the game is unusable for this player.
    username=None keeps any standard game (bulk data)."""
    h = game.headers
    if h.get("Variant", "Standard") != "Standard" or "FEN" in h:
        return None
    white, black = h.get("White", "").lower(), h.get("Black", "").lower()
    if username is not None and username.lower() not in (white, black):
        return None
    pgn = str(game)
    site = _LICHESS_SITE.search(h.get("Site", ""))
    game_id = h.get("GameId") or (site and site.group(1))
    game_id = game_id or "u" + hashlib.sha1(pgn.encode()).hexdigest()[:20]

    initial, inc = time_control(h.get("TimeControl", ""))
    rows, last_clock = [], {}
    board = game.board()
    for ply, node in enumerate(game.mainline(), start=1):
        mover = "white" if board.turn == chess.WHITE else "black"
        clock = node.clock()
        spent = None
        if clock is not None and mover in last_clock:
            spent = round(last_clock[mover] - clock + (inc or 0), 2)
        if clock is not None:
            last_clock[mover] = clock
        rows.append(
            {
                "game_id": game_id,
                "ply": ply,
                "color": mover,
                "fen_before": board.fen(),
                "uci": node.move.uci(),
                "san": board.san(node.move),
                "clock_after": clock,
                "time_spent": spent,
            }
        )
        board.push(node.move)
    if not rows:
        return None

    row = {
        "id": game_id,
        "pgn": pgn,
        "white": white,
        "black": black,
        "white_elo": _int(h.get("WhiteElo")),
        "black_elo": _int(h.get("BlackElo")),
        "time_control": h.get("TimeControl"),
        "speed": _speed(initial, inc),
        "eco": h.get("ECO") if h.get("ECO", "?") != "?" else None,
        "opening": h.get("Opening"),
        "result": h.get("Result"),
        "played_at": _played_at(h),
    }
    return row, rows


def _int(v: str | None) -> int | None:
    return int(v) if v and v.isdigit() else None


def time_control(tc: str) -> tuple[int | None, int | None]:
    m = re.fullmatch(r"(\d+)\+(\d+)", tc)
    return (int(m.group(1)), int(m.group(2))) if m else (None, None)


def _speed(initial: int | None, inc: int | None) -> str | None:
    """Lichess speed categories from estimated game duration (initial + 40 * increment)."""
    if initial is None:
        return None
    est = initial + 40 * inc
    for limit, name in [(29, "ultraBullet"), (179, "bullet"), (479, "blitz"), (1499, "rapid")]:
        if est <= limit:
            return name
    return "classical"


def _played_at(h) -> datetime | None:
    try:
        return datetime.strptime(f"{h['UTCDate']} {h['UTCTime']}", "%Y.%m.%d %H:%M:%S").replace(
            tzinfo=UTC
        )
    except (KeyError, ValueError):
        return None
