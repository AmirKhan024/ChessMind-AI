from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    SmallInteger,
    String,
    Table,
    Text,
    create_engine,
    func,
)

from app.config import settings

metadata = MetaData()

players = Table(
    "players",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("username", String(30), nullable=False, unique=True),  # lower-cased
    Column("latest_game_ms", BigInteger),  # createdAt of newest fetched Lichess game
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
)

games = Table(
    "games",
    metadata,
    Column("id", String(40), primary_key=True),  # Lichess id, or hash for uploads
    Column("source", String(10), nullable=False),  # lichess | upload
    Column("pgn", Text, nullable=False),
    Column("white", String(30), nullable=False, index=True),  # lower-cased usernames
    Column("black", String(30), nullable=False, index=True),
    Column("white_elo", SmallInteger),
    Column("black_elo", SmallInteger),
    Column("time_control", String(20)),
    Column("speed", String(15)),
    Column("eco", String(3)),
    Column("opening", Text),
    Column("result", String(7)),
    Column("played_at", DateTime(timezone=True)),
    Column("engine", String(40)),
    Column("engine_nodes", Integer),
    Column("analysed_at", DateTime(timezone=True)),
)

# One row per half-move. Evidence ids are "<game_id>:<ply>".
moves = Table(
    "moves",
    metadata,
    Column("game_id", ForeignKey("games.id", ondelete="CASCADE"), primary_key=True),
    Column("ply", SmallInteger, primary_key=True),  # 1 = white's first move
    Column("color", String(5), nullable=False),
    Column("fen_before", Text, nullable=False),
    Column("uci", String(5), nullable=False),
    Column("san", String(10), nullable=False),
    Column("clock_after", Float),  # seconds left after the move
    Column("time_spent", Float),  # seconds, increment accounted for
    Column("wc_before", Float, nullable=False),  # winning chances -1..1, mover's view
    Column("wc_after", Float, nullable=False),
    Column("best_uci", String(5)),  # engine's choice in fen_before
    Column("label", String(10)),  # inaccuracy | mistake | blunder | null
)

# Keyed by position (EPD, no move counters) + engine + limit, so results are reproducible.
position_cache = Table(
    "position_cache",
    metadata,
    Column("epd", Text, primary_key=True),
    Column("engine", String(40), primary_key=True),
    Column("nodes", Integer, primary_key=True),
    Column("cp", Integer),  # white's view
    Column("mate", SmallInteger),
    Column("best_uci", String(5)),
)

jobs = Table(
    "jobs",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("player_id", ForeignKey("players.id", ondelete="CASCADE"), nullable=False),
    Column("status", String(10), nullable=False),  # fetching | analysing | done | failed
    Column("total", Integer, nullable=False, server_default="0"),
    Column("done", Integer, nullable=False, server_default="0"),
    Column("cache_hits", Integer, nullable=False, server_default="0"),
    Column("positions", Integer, nullable=False, server_default="0"),
    Column("error", Text),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    Column("updated_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
)

engine = create_engine(settings.sqlalchemy_url, pool_pre_ping=True)
