# STATUS

> Maintained by Claude Code. Read `SOURCE_OF_TRUTH.md` first, then this file.
> Update this file at the end of every session, before finishing.

**Last updated:** 2026-10-29
**Current chunk:** Chunk 5, Production and cloud (in progress; deploy blocked on owner approval)
**Overall state:** Chunks 0-4 done. Post-Chunk-4 fixes (speed, report content) done. Chunk 5 built and tested locally; AWS not provisioned. Open: re-fit calibration after the mate-label fix; measure coach-v4 prompt when the Groq daily quota resets

---

## Chunk tracker

| Chunk | Name | State | Tag |
|---|---|---|---|
| 0 | Foundation | done | chunk-0-done |
| 1 | Ingest and analyse | done | chunk-1-done |
| 2 | Weakness engine | done | chunk-2-done |
| 3 | Coach | done | chunk-3-done |
| 4 | Product surface | done | chunk-4-done |
| 5 | Production and cloud | in progress (blocked: AWS approval) | |
| 6 | Evidence and polish | not started | |

States: not started / in progress / blocked / done

---

## Right now

- **Working on:** Chunk 5. Done locally: Prometheus metrics (Redis-backed so all RQ worker processes add up; `app/metrics.py`), Grafana dashboard provisioned from `infra/monitoring/`, JSON logs with job_id (`app/logs.py`; workers run `python -m app.worker`), report queue (reports no longer run inside a request), per-IP rate limit, `DELETE /players/{u}`, CI model quality gate (`ml/gate.py`, committed 30k-move holdout) and a named grounding-gate step, load test (`scripts/load_test.py`), failure tests (`docs/failure-tests.md`: 4 real bugs found and fixed), runbook (`docs/runbook.md`), Terraform for one EC2 VM + Caddy HTTPS + SSM secret + budget alarm (`infra/terraform/`, passes `terraform validate`, **not applied**).
- **Blocked on owner:** approve AWS spend and provide credentials, then `terraform apply` (see Blockers). Also decide whether Groq's 200k tokens/day is enough for a public demo.
- **Post-Chunk-4 product fixes (owner feedback 2026-10-29: Carlsen's 300 games took ~15 min and the report said nothing useful):** (a) the template said "about what peers expect" for 430 vs 764 blunders; now it says fewer/more with the ratio. (b) Relative weak spots: the same Gamma-Poisson test with the expectation scaled by the player's own overall O/E, labelled "own level", fill places the peer test leaves empty. (c) A breakdown by phase / time left / time control is always shown and given to chat (chat could not answer "why do I blunder in the endgame"). (d) Peer-data note at 2700+. (e) Two-pass analysis (10k nodes, re-check moves dropping >= 5 pts at 100k): 99.1% of blunders kept, 2.5x faster. (f) 8 analysis workers by default, newest games first, early report after 50 games, fair interleaving between users. Result: 299 games from live Lichess in 260 s (was ~15 min).
- **Coach prompt coach-v4** (readable keys in the prompt, trimmed report input) is untested live: Groq's daily token quota was used up by the evals. **TODO:** run `python -m ml.eval_coach beny627 goodplayscap husnu1995 drnykterstein --reports 2 --pause 45` inside the stack (command in results.md), then commit the re-recorded fixtures. Current `recorded.jsonl` = the Chunk 3 recordings re-scored with the field-name rule.
- **Report rows do not record two-pass settings** (`games.quick_nodes` has them per game; the report page prints only the full node limit). Small follow-up.
- **Bug found and fixed in Chunk 4 (affects Chunk 1-2 numbers):** checkmating moves were labelled blunders ("mate in 0" scored as a loss for the mover; the cache also lost the sign). All 67 mating moves in the dev DB were blunders. Fixed in `win_chances` and `analyse_game`; migration `d1e7a3c5b902` fixes stored moves. Training labels (Lichess %eval) were not affected. Our-engine label files were: white's mating moves = **2.3% (random), 3.2% (consistency), 2.5% (validity) of our blunders**. **TODO next session:** correct the `*_ours.jsonl` rows (mating move: blunder=0) and re-run `ml/consistency.py`, `ml/calibrate.py`, `ml/validity.py`; update calibration.json and the measured results below (the 1.16x label gap should drop to roughly 1.12x). Reports stored before the fix keep their old facts; generate a new one.
- **Chunk 3 open items:** Open items for Chunk 3: re-record `tests/fixtures/coach/recorded.jsonl` after prompt changes (`python -m ml.eval_coach beny627 goodplayscap husnu1995 --reports 2`); report first-try pass rate is only 50%, try a clearer prompt or gpt-oss-20b comparison.
- **Chunk 3 plan:** No LangGraph: the report is one structured call and chat is a short tool loop, plain code is simpler (decision logged). `llm.py` Groq client (timeout, bounded retries, backoff). `grounding.py` pure-code verifier: LLM must copy numbers verbatim from facts; every number and chess move in a claim must exist in the cited weakness/evidence facts; claims must cite evidence ids of their own weakness. Up to 2 regenerations with error feedback, then deterministic template. Reports table stores method, versions, verification log. Chat = tool loop over the same facts, answer verified against that turn's tool outputs. Eval: recorded fixtures (good + deliberately bad outputs) in CI; live script measures grounding pass rate.
- **Chunk 2 plan:** (1) Stream-decompress only the head of the newest monthly file (2026-09 is 29.2 GB; a few hundred MB holds roughly 1M games), keep standard-time-control rated games. (2) Measure eval bias: games with `%eval` vs without (rating, speed, result, length). (3) Consistency: re-label ~200 evaluated games with our 100k-node Stockfish, compare with labels from Lichess evals. (4) Choose the training labels from (2)+(3): Lichess evals if consistent and unbiased; otherwise our own engine on a random sample (tens of minutes of CPU, not days, if a few thousand games are enough). (5) One feature function over `parse_game` output, used for training and serving. (6) Logistic regression vs LightGBM, player-level split, log-loss/Brier/calibration by rating band and speed. (7) Weakness ranking: observed vs expected blunders per slice with Gamma-Poisson shrinkage and P(ratio>1). (8) Predictive validity on players with many games (early vs later games).

## Done so far

- Chunk 1: tables (players, games, moves, position_cache, jobs) via Alembic; Lichess NDJSON stream on a single-worker queue with 60 s 429 pause; PGN upload; shared PGN parser; per-game Stockfish analysis with FEN cache; idempotent re-runs (only new games fetched/analysed); RQ failure callback marks jobs failed with reason. Live run on real Lichess users works. Tests: 13 (labels, parser, 429/404, DB pipeline with fake engine); CI runs e2e upload with real Stockfish.

- Chunk 0 plan: minimal FastAPI `/health` (checks Postgres+Redis), compose with api/worker/db/redis, ruff+pytest, CI (lint, test, compose smoke). Matches source of truth.
- `docker compose up --build --wait`: all services healthy, `/health` returns 200 (2026-10-28). 2 unit tests + ruff pass locally.

## Blockers and questions for the owner

- **AWS deploy needs approval (money).** Plan: one `c7i-flex.large` (2 vCPU, 4 GB; free-tier eligible for AWS accounts created after 2025-07-15 per AWS docs, paid from credits) + Elastic IP + 30 GB gp3, budget alarm at $15/month. A bigger box analyses faster: c7i.2xlarge is about $0.357/h (~$260/month if always on; third-party price pages, check the console). Needs: AWS credentials on this machine, an alert email, and the Groq key stored in SSM (`docs/runbook.md`).
- **Groq free tier: 200k tokens/day for gpt-oss-120b** (about 25-40 LLM reports a day; after that, template reports). Options: accept; add `openai/gpt-oss-20b` as a fallback model (grounding pass rate not measured); or Groq's paid Dev tier.

---

## Verified facts

Record the result of every check of an [unverified] item from `SOURCE_OF_TRUTH.md`. Include date and how you checked.

| Fact | Result | Date | How checked |
|---|---|---|---|
| Lichess user-games export supports `since` and `max` | Yes, but **`max` is not a hard cap when `rated`/`perfType` filters are used** (max=30 returned 50); cap enforced client-side. NDJSON with `Accept: application/x-ndjson`; `clocks`, `opening`, `pgnInJson`, `evals` params work. **Requests without a descriptive User-Agent get HTTP 404** (curl default UA failed); must send a UA. | 2026-10-28 | Real requests + lichess-org/api OpenAPI spec |
| Public games need no token | True. Spec: stream throttled to 20 games/s anonymous, 30 authenticated. Token in `.env` is valid (`/api/account` 200) but not needed. | 2026-10-28 | Real request + spec |
| Lichess PGN export by the player themselves | Same endpoint returns PGN (default format) for any public user, so a user can download a PGN file and upload it. UI download page not separately checked. | 2026-10-28 | Spec |
| Lichess win-% formula and blunder thresholds | Formula correct: `wc = 2/(1+exp(-0.00368208*cp))-1`, cp clamped to ±1000; mate n → cp=(21-min(10,|n|))*100. **Correction:** thresholds are 0.1/0.2/0.3 on the wc scale (−1..1) = **5/10/15 win-% points**, not 10/20/30. Plan updated to use Lichess values. | 2026-10-28 | lila source: `ui/lib/src/ceval/winningChances.ts`, `modules/tree/src/main/Advice.scala` |
| PGN headers contain ECO/opening and `[%clk]` comments | True for API export and bulk file (bulk: 99.7% of games have `%clk`; ECO/Opening headers present; Lichess `?!/?/??` NAGs present; no `GameId` header, id is end of `Site`). API Site format is `/game/<id>`. | 2026-10-28 | Real API response, 3 games |
| Monthly dump file sizes | 2026-09: 29.2 GB, 89.6M games (~327 B/game). 2018-09: 6.8 GB. Server supports range requests; a truncated head decompresses fine. Download ~4.8 MB/s here. | 2026-10-28 | HEAD requests, `counts.txt`, 50 MB range download |
| Selection bias in games that have evals | **10.4%** of games have `%eval` (not ~6%). Evaluated games are slower (rapid 32% vs 15%, bullet 21% vs 38%), ~59 Elo lower, fewer time forfeits (25% vs 34%). Same-labeller (our engine) blunder rate with vs without %eval: 7.92% vs 7.82%, no visible effect (198 vs 295 games). | 2026-10-28 | First 50 MB of 2026-09 (160k games), `ml/bias.py` |
| Groq: daily token limit | **200,000 tokens per day** for gpt-oss-120b on the free tier (429 body: "tokens per day (TPD): Limit 200000"), separate from the 8k tokens/minute limit | 2026-10-29 | Real 429 response |
| Groq: current model name and free-tier limits | Key valid. Models include `openai/gpt-oss-120b` (chosen), `openai/gpt-oss-20b`, `qwen/qwen3.8-27b`. Chat call works; rate-limit headers for gpt-oss-120b on this key: **1000 requests, 8000 tokens per minute** (so prompts must stay small, eval runs paced). | 2026-10-28 | `GET /openai/v1/models` |

---

## Measured results

Only measured numbers go here. These later feed `docs/results.md` and the README.

| Metric | Value | Conditions | Date |
|---|---|---|---|
| Stockfish positions/sec per core | 25k nodes: 30.5; 50k: 16.3; **100k: 8.6**; 200k: 4.5; 1M: 0.92. Label agreement vs 1M-node reference: 0.898 / 0.902 / **0.939** / 0.943; blunder recall 0.82 / 0.82 / 0.88 / 0.94 (only 17 ref blunders, small sample) | SF19, 1 thread, 16MB hash cleared per position, 250 positions from 5 real games, Docker on owner laptop (24 logical CPUs). `scripts/bench_stockfish.py` | 2026-10-28 |
| Position cache hit rate | 5.8% (thibault, 47 games, 3661 positions); 7.0% (german11, 20 games, 1294 positions, cache already warm from thibault) | 100k nodes, 4 analysis workers in parallel (concurrent games miss each other's opening positions) | 2026-10-28 |
| Time to report per 100 games (p50/p95) | Not yet p50/p95. Single runs, fetch+analysis only: 47 games in 114 s (~32 positions/s total); 20 games in 55 s | 4 analysis workers, owner laptop, Docker | 2026-10-28 |
| Baseline model calibration (Brier / log-loss) | Held-out players (210k moves, 4,334 players), Lichess-%eval labels, blunder rate ~6.9%. Constant: log-loss 0.2555, Brier 0.06573. Logistic: 0.2383 / 0.06328, ECE 0.0033. **LightGBM: 0.2001 / 0.05689, ECE 0.0018** | 16.6k evaluated games from first 50 MB of 2026-09; player-level split; `ml/train.py` | 2026-10-28 |
| Our labels vs Lichess-%eval labels | **(pre mate-fix; 3.2% of our blunders here were mating moves, re-measure)** Our 100k-node labels flag 1.16x more blunders (8.03% vs 6.94%, 13.4k moves, 198 games); we catch 93.6% of Lichess blunders; 80.9% of ours are Lichess blunders. Fixed with logit shift | 200 random evaluated games, SF19 100k nodes | 2026-10-28 |
| Predictive validity result | **(pre mate-fix, re-measure)** Flagged slices holdout O/E 1.43 (95% CI 1.18-1.62) vs 1.05 (1.00-1.11) for unflagged eligible slices; discovery was 1.62. 34 slices, 20 of 60 players. **98% bullet games**, one month. | 60 players x 50 games, first half discovery/second half holdout, `ml/validity.py` | 2026-10-28 |
| Calibration on serving labels | **(pre mate-fix, re-measure)** After logit shift: O/E 0.98 (0.91-1.05) on 295 random non-%eval games (19.8k moves); raw model was 1.23 | `ml/calibrate.py` | 2026-10-28 |
| Grounding pass rate (reports) | Strict rules (claims must quote their numbers): 3/6 grounded on first try, 5/6 after regeneration, 1/6 fell back to template; 0 ungrounded reports shown. Median latency 4.8 s, max 38 s; ~5.4k tokens/report. (An earlier, looser verifier passed 12/12, but the reports were vague and quoted no numbers, so it was tightened.) | 3 real players (60 games each), gpt-oss-120b, `ml/eval_coach.py` | 2026-10-28 |
| Grounding pass rate (chat) | After giving overview+weaknesses up front: 4/4 in-scope answers grounded (spot check). Before: 2/6 (tool-loop HTTP 400s). Out-of-scope: 4/6 answered with a refusal; 2/6 blocked (echoed the user's own number) and replaced by the fixed refusal | same | 2026-10-28 |
| Cost per report | $0 on the Groq free tier; 5-9k tokens per report (8.7k mean with regenerations, coach-v3) | gpt-oss-120b | 2026-10-29 |
| Parallel Stockfish throughput | 1 engine 4.1 pos/s; 4: 19.0; 8: 26.2; 16: 31.1; 20: 32.4 (100k nodes) | `scripts/bench_parallel.py`, dev laptop | 2026-10-29 |
| Two-pass analysis | 10k first pass, re-check >= 5 pts at 100k: **99.1% of 1,511 reference blunders kept, 2.48x faster** (5k: 98.4% / 2.77x; 20k: 99.7% / 1.97x) | 295 random games, `ml/two_pass.py` | 2026-10-29 |
| Time to analyse 300 games | **260 s** for penguingm1 (299 games, live Lichess, 8 workers, cache hit 8%); first 50 games after 60 s. Before: ~15 min (drnykterstein, 4 workers, one pass) | dev laptop | 2026-10-29 |
| Load test | 3 users x 25 games at once: half done after 48/60/70 s (before the fairness fix: 31/68/93 s); pages p50 46 ms, p95 107 ms | `scripts/load_test.py` | 2026-10-29 |
| Coach live eval (coach-v3) | reports 8/8 grounded LLM after regeneration, 1/8 first try (53/61 errors were field names); chat in-scope 11/12, out-of-scope 8/8; p50 37 s | 4 players, `ml/eval_coach.py` | 2026-10-29 |
| Model gate holdout | log-loss 0.2032, Brier 0.0580, ECE 0.0035 | 30k held-out moves, `ml/gate.py` | 2026-10-29 |

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
| 2026-10-28 | Bulk data = streamed head of newest month (50 MB = 160k games) | Whole month is 29 GB; 1M labelled moves already enough for a stable model | Full month download; older smaller months (different player pool) |
| 2026-10-28 | Train on Lichess-%eval labels, then logit-shift recalibration to our labels; test on random non-evaluated games labelled by our engine | Our engine labelling 1M moves would take ~days; consistency check showed a systematic 13.5% label-rate gap that a shift can absorb | Train on own labels only (too little data); ignore gap (every user would look ~1.13x worse than peers) |
| 2026-10-28 | LightGBM chosen over logistic regression | Log-loss 0.200 vs 0.238, both calibrated | Logistic (kept as reported baseline) |
| 2026-10-28 | File model registry (`backend/models/peer_baseline/` with metrics.json) instead of MLflow | One model, metrics in JSON are enough for the CI quality gate; no tracking server | MLflow (revisit if many experiments) |
| 2026-10-28 | Weakness confidence threshold 0.975, Gamma(5,5) prior, slices >60% of moves excluded | Null simulation: 0.9 gave 0.8 false findings/player; 0.975 gives 0.18 with 96% power for a 2.4x effect | 0.9, 0.99 (power 0.89) |
| 2026-10-28 | No LangGraph: plain Python for the report loop and the chat tool loop | One structured call + a 3-round tool loop; a graph framework adds a dependency and indirection without solving a problem here | LangGraph |
| 2026-10-28 | Grounding = exact-number + move check: LLM must copy numbers verbatim from the cited facts; SAN moves only from cited examples; claims must cite their own weakness's evidence | Deterministic, explainable, easy to test; any arithmetic or invented move fails | Fuzzy number tolerance (lets wrong numbers through), LLM-as-judge (an LLM checking an LLM) |
| 2026-10-28 | Report: 1 try + 2 regenerations with error feedback, then template. Chat: 1 regeneration, then a fixed refusal | Bounded latency and tokens under the 8k TPM limit | Unbounded retries |
| 2026-10-28 | Groq model `openai/gpt-oss-120b`, `reasoning_effort=low`, temperature 0.2, JSON mode for reports | Largest listed model; low reasoning keeps tokens within limit | gpt-oss-20b, qwen3.8-27b (not compared yet) |
| 2026-10-28 | UI: server-rendered Jinja2 pages in the API, boards as SVG from python-chess | No JS build, no CDN, one container; the source of truth allows templates for a solo build | React + Vite + chessboard component |
| 2026-10-28 | Puzzle drills from a shipped 1.8 MB subset (popular, well-tested puzzles; 25 per theme/opening x 200-point band), held in memory; no puzzles table | Clean `docker compose up` works offline; whole file is 307 MB / 6.2M rows | Load full dump into Postgres at startup |
| 2026-10-28 | Weakness -> puzzle themes by a fixed table (phase -> phase theme, clock/speed -> short/long, piece -> piece endgame + typical tactic, opening -> opening tag) | The puzzle file has no clock or piece-moved data; a transparent table is honest about that | Learned mapping (no labels to learn from) |
| 2026-10-29 | Relative weak spots (expected x player's own O/E) fill report places when the peer test finds fewer than 5 | Strong players (and most others) got an empty report; the peer test alone can only say "worse than peers" | Lower confidence threshold (more false findings) |
| 2026-10-29 | Two-pass analysis: 10k nodes, then 100k for moves dropping >= 5 pts | Measured 99.1% blunder recall at 2.5x speed; more than 8 workers barely helps on the laptop | Lower node limit everywhere (worse labels) |
| 2026-10-29 | Metrics kept in Redis hashes, exposed by the API's /metrics via a custom collector | RQ forks a process per job; in-process counters would be lost | Pushgateway (extra service), prometheus_client multiprocess mode (shared dir across containers) |
| 2026-10-29 | Fair scheduling: each job keeps ANALYSIS_WORKERS games in RQ, the rest chained from a Redis list | Load test showed FIFO starvation between users | One RQ queue per user |
| 2026-10-29 | Failed or abandoned game: retry once, then skip it (jobs.skipped) | Kill test: one lost game froze or failed the whole job | Fail the job (old behaviour) |
| 2026-10-29 | Deploy target: one EC2 VM running compose + Caddy (sslip.io hostname), Terraform, Groq key in SSM, no SSH | Cheapest defensible option; same stack as local | ECS Fargate (more cost for steady CPU), Kubernetes (non-goal) |
| 2026-10-29 | Model gate on a committed 30k-move holdout sample | Training data is not in the repo, so CI needs a fixed evaluation set | MLflow registry (still one model) |
| 2026-10-29 | One report worker by default | Groq's tokens/minute, not CPU, limits reports; a second worker only competes for it | 2 workers |
| 2026-10-28 | uv + `uv.lock` for pinned deps | Reproducible builds, fast CI | pip + requirements.txt |

---

## Session log

Short entry per session: what changed, what is broken, what to do next.

- 2026-10-28: Chunk 0 built and run locally. `.env` has `GROQ_API_KEY ` with a space before `=` (works with python-dotenv, fragile elsewhere; consider removing it). Docker Desktop was not running at start; I started it. Pushed; CI green; tagged `chunk-0-done`.
- 2026-10-28: `.env` values are wrapped in quotes; fine for python-dotenv/compose, but shell parsing must strip them.
- 2026-10-28: Chunk 1 built. Found Lichess needs a User-Agent, `max` is soft with filters, PGN `Site` is now `/game/<id>` (use `GameId` header). Cache hit rate is low (5-7%) for single users; worth re-checking with more users. Next: Chunk 2, starting with the bulk-eval vs our-label consistency check.
- 2026-10-28: Chunk 2 built and measured; results in `docs/results.md`. Caveats: validity sample is 98% bullet (players with many games in a 50 MB head); only 1 month of data. If time allows, a larger/other-month sample would firm this up. `GET /players/{u}/weaknesses` works on real analysed players. Labelling 3,000 games took ~2.5 h of laptop CPU.
- 2026-10-28: Chunk 3 built. Coach report + chat with code-only grounding verifier, regeneration, template/refusal fallback, reports table with versions. Live eval on 3 real players. Found and fixed: vague reports passed a loose verifier; chat tool loop hit Groq 400 (`tool_choice none` but model called a tool). 43 tests incl. replay of real recorded outputs.
- 2026-10-28: Chunk 4 first slice (short session). Server-rendered UI: submit/upload, progress with queue position, report with boards. Tested live: real report for beny627 (gpt-oss-120b, grounded, 3.6 s) renders with boards; upload, re-upload and error paths work. 47 tests pass. Next: chat UI, puzzle drill list, clean-compose browser check.
- 2026-10-29: Owner tested drnykterstein: ~15 min and an empty, misleading report. Fixed (see Right now), then built Chunk 5 locally. Failure tests found 4 real bugs (a lost game froze or failed a job; 7-10 minute recovery; deploys cut games off; a Redis outage made every page ~8 s and submit a 500), all fixed and re-tested. Groq's daily quota (200k tokens) ran out during evals, so the coach-v4 prompt is still to be measured. A live eval run I stopped had overwritten recorded.jsonl; restored from git and re-scored, and eval_coach now writes atomically. Port 8000 was taken by another local project, hence `API_PORT`. 80 tests pass. Next: owner approves AWS -> `terraform apply`, check the public URL and dashboards with real traffic, write the cloud part of the failure report, tag chunk-5-done.
- 2026-10-28: Chunk 4 finished: chat box, puzzle drills, HTML error pages, smoke test checks UI. Clean-stack check in a separate compose project (empty volumes, dev data kept): real Lichess user german11 (18 games) submit -> progress -> template report (no weakness) -> grounded chat; upload; error pages; smoke script passes. Headless Chrome: report page screenshot looks right; chat box click-through returns a grounded answer. Screenshot showed `Ra3#` labelled a blunder -> found the mate-label bug (see Right now). 61 tests pass. Not checked: phone-width layout (headless Chrome clamps the window width).
