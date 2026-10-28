# STATUS

> Maintained by Claude Code. Read `SOURCE_OF_TRUTH.md` first, then this file.
> Update this file at the end of every session, before finishing.

**Last updated:** 2026-10-28
**Current chunk:** Chunk 0, Foundation
**Overall state:** Chunk 0 built and verified locally; CI not yet run (not pushed)

---

## Chunk tracker

| Chunk | Name | State | Tag |
|---|---|---|---|
| 0 | Foundation | in progress (local done, CI unverified) | |
| 1 | Ingest and analyse | not started | |
| 2 | Weakness engine | not started | |
| 3 | Coach | not started | |
| 4 | Product surface | not started | |
| 5 | Production and cloud | not started | |
| 6 | Evidence and polish | not started | |

States: not started / in progress / blocked / done

---

## Right now

- **Working on:** Chunk 0 wrap-up
- **Next step:** Owner pushes to GitHub so CI can run; then Chunk 1 (verify Lichess facts + Stockfish benchmark first)

## Done so far

- Chunk 0 plan: minimal FastAPI `/health` (checks Postgres+Redis), compose with api/worker/db/redis, ruff+pytest, CI (lint, test, compose smoke). Matches source of truth.
- `docker compose up --build --wait`: all services healthy, `/health` returns 200 (2026-10-28). 2 unit tests + ruff pass locally.

## Blockers and questions for the owner

(empty)

---

## Verified facts

Record the result of every check of an [unverified] item from `SOURCE_OF_TRUTH.md`. Include date and how you checked.

| Fact | Result | Date | How checked |
|---|---|---|---|
| Lichess user-games export supports `since` and `max` | | | |
| Public games need no token | | | |
| Lichess PGN export by the player themselves | | | |
| Lichess win-% formula and blunder thresholds | | | |
| PGN headers contain ECO/opening and `[%clk]` comments | | | |
| Monthly dump file sizes | | | |
| Selection bias in games that have evals | | | |
| Groq: current model name and free-tier limits | | | |

---

## Measured results

Only measured numbers go here. These later feed `docs/results.md` and the README.

| Metric | Value | Conditions | Date |
|---|---|---|---|
| Stockfish positions/sec per core | | | |
| Position cache hit rate | | | |
| Time to report per 100 games (p50/p95) | | | |
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
| 2026-10-28 | uv + `uv.lock` for pinned deps | Reproducible builds, fast CI | pip + requirements.txt |

---

## Session log

Short entry per session: what changed, what is broken, what to do next.

- 2026-10-28: Chunk 0 built and run locally. `.env` has `GROQ_API_KEY ` with a space before `=` (works with python-dotenv, fragile elsewhere; consider removing it). Docker Desktop was not running at start; I started it. Not tagged until CI is green.
