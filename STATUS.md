# STATUS

> Maintained by Claude Code. Read `SOURCE_OF_TRUTH.md` first, then this file.
> Update this file at the end of every session, before finishing.

**Last updated:** 2026-10-28
**Current chunk:** Chunk 1, Ingest and analyse
**Overall state:** Chunk 0 done (CI green); Chunk 1 in progress

---

## Chunk tracker

| Chunk | Name | State | Tag |
|---|---|---|---|
| 0 | Foundation | done | chunk-0-done |
| 1 | Ingest and analyse | in progress | |
| 2 | Weakness engine | not started | |
| 3 | Coach | not started | |
| 4 | Product surface | not started | |
| 5 | Production and cloud | not started | |
| 6 | Evidence and polish | not started | |

States: not started / in progress / blocked / done

---

## Right now

- **Working on:** Chunk 1 essentially done; awaiting CI.
- **Risk for Chunk 2:** bulk-file evals come from Lichess server analysis, not our 100k-node limit. Labeling 100k+ bulk games ourselves would take days of CPU at 8.6 pos/s/core. Need to check label consistency between the two sources on a sample before building the baseline.
- **Chunk 1 plan:** Stockfish 19 (pinned binary) in the worker image; benchmark nodes limits on real-game positions. Tables via Alembic: games, moves, position_cache, jobs. Lichess fetch = one streamed NDJSON request on a dedicated RQ worker for the `lichess` queue (concurrency 1); on 429 the job sleeps 60s inside that single worker, which pauses the whole queue with no extra machinery. One analysis job per game on the `analysis` queue (N workers), writing per-game results so re-runs skip finished games. PGN upload endpoint uses the same parser. Same as source of truth except the 429 mechanism (simpler).

## Done so far

- Chunk 1: tables (players, games, moves, position_cache, jobs) via Alembic; Lichess NDJSON stream on a single-worker queue with 60 s 429 pause; PGN upload; shared PGN parser; per-game Stockfish analysis with FEN cache; idempotent re-runs (only new games fetched/analysed); RQ failure callback marks jobs failed with reason. Live run on real Lichess users works. Tests: 13 (labels, parser, 429/404, DB pipeline with fake engine); CI runs e2e upload with real Stockfish.

- Chunk 0 plan: minimal FastAPI `/health` (checks Postgres+Redis), compose with api/worker/db/redis, ruff+pytest, CI (lint, test, compose smoke). Matches source of truth.
- `docker compose up --build --wait`: all services healthy, `/health` returns 200 (2026-10-28). 2 unit tests + ruff pass locally.

## Blockers and questions for the owner

(empty)

---

## Verified facts

Record the result of every check of an [unverified] item from `SOURCE_OF_TRUTH.md`. Include date and how you checked.

| Fact | Result | Date | How checked |
|---|---|---|---|
| Lichess user-games export supports `since` and `max` | Yes, but **`max` is not a hard cap when `rated`/`perfType` filters are used** (max=30 returned 50); cap enforced client-side. NDJSON with `Accept: application/x-ndjson`; `clocks`, `opening`, `pgnInJson`, `evals` params work. **Requests without a descriptive User-Agent get HTTP 404** (curl default UA failed); must send a UA. | 2026-10-28 | Real requests + lichess-org/api OpenAPI spec |
| Public games need no token | True. Spec: stream throttled to 20 games/s anonymous, 30 authenticated. Token in `.env` is valid (`/api/account` 200) but not needed. | 2026-10-28 | Real request + spec |
| Lichess PGN export by the player themselves | Same endpoint returns PGN (default format) for any public user, so a user can download a PGN file and upload it. UI download page not separately checked. | 2026-10-28 | Spec |
| Lichess win-% formula and blunder thresholds | Formula correct: `wc = 2/(1+exp(-0.00368208*cp))-1`, cp clamped to ±1000; mate n → cp=(21-min(10,|n|))*100. **Correction:** thresholds are 0.1/0.2/0.3 on the wc scale (−1..1) = **5/10/15 win-% points**, not 10/20/30. Plan updated to use Lichess values. | 2026-10-28 | lila source: `ui/lib/src/ceval/winningChances.ts`, `modules/tree/src/main/Advice.scala` |
| PGN headers contain ECO/opening and `[%clk]` comments | True for API export (`[ECO`, `[Opening`, `[%clk` present). Bulk files still to check in Chunk 2. | 2026-10-28 | Real API response, 3 games |
| Monthly dump file sizes | | | |
| Selection bias in games that have evals | | | |
| Groq: current model name and free-tier limits | Key valid. Models listed include `openai/gpt-oss-120b`, `openai/gpt-oss-20b`, `qwen/qwen3.8-27b`. Limits: check in Chunk 3. | 2026-10-28 | `GET /openai/v1/models` |

---

## Measured results

Only measured numbers go here. These later feed `docs/results.md` and the README.

| Metric | Value | Conditions | Date |
|---|---|---|---|
| Stockfish positions/sec per core | 25k nodes: 30.5; 50k: 16.3; **100k: 8.6**; 200k: 4.5; 1M: 0.92. Label agreement vs 1M-node reference: 0.898 / 0.902 / **0.939** / 0.943; blunder recall 0.82 / 0.82 / 0.88 / 0.94 (only 17 ref blunders, small sample) | SF19, 1 thread, 16MB hash cleared per position, 250 positions from 5 real games, Docker on owner laptop (24 logical CPUs). `scripts/bench_stockfish.py` | 2026-10-28 |
| Position cache hit rate | 5.8% (thibault, 47 games, 3661 positions); 7.0% (german11, 20 games, 1294 positions, cache already warm from thibault) | 100k nodes, 4 analysis workers in parallel (concurrent games miss each other's opening positions) | 2026-10-28 |
| Time to report per 100 games (p50/p95) | Not yet p50/p95. Single runs, fetch+analysis only: 47 games in 114 s (~32 positions/s total); 20 games in 55 s | 4 analysis workers, owner laptop, Docker | 2026-10-28 |
| Baseline model calibration (Brier / log-loss) | | | |
| Predictive validity result | | | |
| Grounding pass rate | | | |
| Cost per report | | | |

---

## Decisions

Log every deviation from a "Recommended" item, and any important choice.

| Date | Decision | Why | Alternatives rejected |
|---|---|---|---|
| 2026-10-28 | Queue: RQ (one worker consuming lichess, analysis, report) | Simplest; split into per-queue workers when Chunk 1 needs it | Arq, Celery |
| 2026-10-28 | Alembic deferred to Chunk 1 | No tables yet; adding it now is unused code | Empty migrations now |
| 2026-10-28 | Blunder thresholds 5/10/15 win-% points | Matches Lichess source (the 10/20/30 in source of truth was wrong) | 10/20/30 |
| 2026-10-28 | Stockfish 19 official linux-x86-64-universal binary, pinned | Latest release (2026-09-05); Debian package lags | apt stockfish |
| 2026-10-28 | Default engine limit 100k nodes (configurable) | Best speed/agreement trade-off in benchmark; nodes (not time) for reproducibility | 50k (agreement 0.90), 200k (2x slower for +0.4pt) |
| 2026-10-28 | Games have `white`/`black` columns, no owner FK; a player's games = where they played | A game between two users of the app is stored and analysed once | games.player_id (breaks on shared games), link table (unneeded) |
| 2026-10-28 | 429: sleep 60 s inside the single lichess worker (max 3 retries, job timeout 600 s) | Pauses the whole queue with no extra state | Redis pause key + scheduler |
| 2026-10-28 | Tests use a separate `chessmind_test` DB, created and migrated by conftest | First version wiped dev data | Mocking the DB |
| 2026-10-28 | Moves of both colours stored | Same eval cost; opponent moves may help later | Player-only moves |
| 2026-10-28 | uv + `uv.lock` for pinned deps | Reproducible builds, fast CI | pip + requirements.txt |

---

## Session log

Short entry per session: what changed, what is broken, what to do next.

- 2026-10-28: Chunk 0 built and run locally. `.env` has `GROQ_API_KEY ` with a space before `=` (works with python-dotenv, fragile elsewhere; consider removing it). Docker Desktop was not running at start; I started it. Pushed; CI green; tagged `chunk-0-done`.
- 2026-10-28: `.env` values are wrapped in quotes; fine for python-dotenv/compose, but shell parsing must strip them.
- 2026-10-28: Chunk 1 built. Found Lichess needs a User-Agent, `max` is soft with filters, PGN `Site` is now `/game/<id>` (use `GameId` header). Cache hit rate is low (5-7%) for single users; worth re-checking with more users. Next: Chunk 2, starting with the bulk-eval vs our-label consistency check.
