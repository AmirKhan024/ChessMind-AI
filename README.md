# ChessMind AI

Analyses your recent Lichess games with Stockfish, compares your mistakes to peers at your rating, and has an LLM coach explain your recurring weaknesses, grounded in real positions from your games.

Work in progress. See `SOURCE_OF_TRUTH.md` for the design and `STATUS.md` for progress.

## Run

```
cp .env.example .env   # fill in keys later chunks need
docker compose up --build
curl localhost:8000/health
curl -X POST localhost:8000/players/<lichess-username>/analyse   # -> {"job_id": ...}
curl -F username=<you> -F file=@games.pgn localhost:8000/upload  # PGN fallback, no Lichess API call
curl localhost:8000/jobs/<job_id>                                 # progress
```

`GAMES_CAP` (default 300) and `ANALYSIS_WORKERS` (default 4) are read from the environment. Postgres is exposed on host port 5433.

## Develop

```
docker compose up -d db          # tests use a separate chessmind_test database
cd backend && uv sync && uv run pytest
```

Game data comes from [Lichess](https://lichess.org) (public API and CC0 database). This project is not affiliated with Lichess.
