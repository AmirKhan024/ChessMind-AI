# ChessMind AI

Analyses your recent Lichess games with Stockfish, compares your mistakes to peers at your rating, and has an LLM coach explain your recurring weaknesses, grounded in real positions from your games.

Work in progress. See `SOURCE_OF_TRUTH.md` for the design and `STATUS.md` for progress.

## Run

```
cp .env.example .env   # GROQ_API_KEY for the coach; without it reports use the template
docker compose up -d --build --wait
```

- App: http://localhost:8000 (enter a Lichess username or upload a PGN)
- Grafana: http://localhost:3000 (dashboard "ChessMind AI"), Prometheus: http://localhost:9090

API:

```
curl -X POST localhost:8000/players/<username>/analyse          # -> {"job_id": ...}
curl -F username=<you> -F file=@games.pgn localhost:8000/upload  # PGN fallback, no Lichess call
curl localhost:8000/jobs/<job_id>                                 # progress
curl -X POST localhost:8000/players/<username>/report             # -> {"report_id": ...} (queued)
curl localhost:8000/reports/<report_id>                           # 202 while writing, then 200
curl -X DELETE localhost:8000/players/<username>                  # delete my data
```

Settings (environment): `GAMES_CAP` (300), `ANALYSIS_WORKERS` (8, one Stockfish per worker),
`REPORT_WORKERS` (1), `API_PORT`, `DB_PORT`, `GRAFANA_PORT`, `PROMETHEUS_PORT`, `RATE_LIMIT`.

## Develop

```
docker compose up -d db          # tests use a separate chessmind_test database
cd backend && uv sync && uv run pytest
```

Docs: `docs/results.md` (measured numbers), `docs/failure-tests.md`, `docs/runbook.md`
(operations and AWS deploy), `infra/terraform/` (AWS infrastructure).

Game data comes from [Lichess](https://lichess.org) (public API and CC0 database). This project is not affiliated with Lichess.
