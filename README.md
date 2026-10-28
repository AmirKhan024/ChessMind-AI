# ChessMind AI

Analyses your recent Lichess games with Stockfish, compares your mistakes to peers at your rating, and has an LLM coach explain your recurring weaknesses, grounded in real positions from your games.

Work in progress. See `SOURCE_OF_TRUTH.md` for the design and `STATUS.md` for progress.

## Run

```
cp .env.example .env   # fill in keys later chunks need
docker compose up --build
curl localhost:8000/health
```

## Develop

```
cd backend && uv sync && uv run pytest
```
