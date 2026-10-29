# Failure tests

Run on 2026-10-29 against the full compose stack on the dev laptop (8 analysis workers,
2 report workers). Each test was run, the problem found was fixed, and the test was run
again. Commands are in each section so they can be repeated.

## 1. Analysis worker killed mid-job

**How:** upload 25 games for one player (`scripts/load_test.py ... --players 1 --games 25`),
then `docker kill chessmindai-worker-analysis-N` while it is analysing a game.

**First run:** the job stopped at 22/23 games and stayed at "analysing" for good. The
killed worker's game was lost, and nothing queued it again. When RQ's cleanup noticed the
dead worker, its failure callback marked the **whole job failed**, although 22 of 23 games
were done.

**Fixes:**
- The failure callback no longer fails the job for one game: it retries the game once,
  then counts it as skipped (`jobs.skipped`, shown on the job page) so the job still
  finishes.
- RQ only finds a dead worker's job when its registry entry expires and maintenance runs.
  With RQ's defaults (420 s worker TTL, 600 s maintenance) the retried run still took
  **435 s** for 25 games (half done after 23 s). Workers now run with a 60 s TTL and 60 s
  maintenance (`app/worker.py`).

**Final run:** worker killed 6 s into the job; the abandoned game was found and retried.
The job finished at **22/22, 0 skipped, in 157 s**.

## 2. Deploy while jobs run (found by accident)

Rebuilding the stack (`docker compose up -d --build`) while a job ran cut off five
in-progress games: Docker stops containers after 10 s, and RQ's warm shutdown wants to
finish the current game first (5-40 s). **Fix:** `stop_grace_period: 60s` on all workers.
Any game still cut off is recovered as in test 1.

## 3. Lichess rate limit (HTTP 429)

**How:** a fake Lichess (`infra/fault/fake_lichess.py`) answers the first export request
with 429 and the second with 10 real games. A lichess worker was pointed at it with
`LICHESS_URL`.

**Result (no fix needed):** 429 at 09:28:26, the worker paused the whole Lichess queue and
retried at 09:29:26 (exactly 60 s, as Lichess asks), and the job finished 10/10 in 66 s.
`chessmind_lichess_429_total` went to 1, and the log line carried the job id.

## 4. Groq timeout

**How:** a report worker started with `LLM_TIMEOUT=0.001`, then a report requested through
the API.

**Result (one fix):** the report was written from the deterministic template
(`method=template`, 0 LLM attempts) in about 6 s; the user saw a correct report and no
error. Timeouts and network errors were retried without any log line; they are now logged
with the attempt number and counted in `chessmind_llm_requests_total{outcome="error"}`.

## 5. Redis down

**How:** `docker compose stop redis`, then requests to the API.

**First run:** `/health` correctly returned 503. But **every page took about 8 s**, because
each request still tried to write its metrics to Redis, and submitting a username returned
an **unhandled 500**.

**Fixes:** after a Redis error, metrics and the rate limit skip Redis for 30 s (fail open);
a Redis error while enqueueing returns 503 "The job queue is unavailable" (an HTML page for
the UI).

**Final run:** pages 5-10 ms during the outage, submit returns 503, `/health` 503 (about 8 s,
because it really checks Redis). Workers logged connection errors, kept retrying, and
resumed when Redis came back.

## Load test

`scripts/load_test.py` (3 players x 25 games uploaded at once, 10 readers polling pages,
rate limit off). The first run showed users were **not served fairly**: each upload queued
all its games, so the third user waited for the first two (half done after 31 / 68 / 93 s).
Now each job keeps only `ANALYSIS_WORKERS` games in the queue and each finished game queues
its job's next one, so jobs take turns: half done after **48 / 60 / 70 s**, all done after
94 / 100 / 108 s, at the same total throughput. Page latency under that load: p50 46 ms,
p95 107 ms (4,347 requests).
