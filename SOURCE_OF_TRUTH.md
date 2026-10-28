# ChessMind-AI: Source of Truth

> Read this file and `STATUS.md` at the start of every session. Update `STATUS.md` at the end of every session.
> Repo: https://github.com/AmirKhan024/ChessMind-AI.git

---

## 0. How to use this document (for Claude Code)

**Who is working:** one human owner, working alone with you (Claude Code, Opus 5.5 or Sonnet 5.5, possibly switching between sessions). There is no team and no merge-conflict problem. Your context resets between sessions, so this file and `STATUS.md` are your memory.

**You are trusted to use your own judgement.** This document gives the goal, the principles, the constraints and a recommended path. It does not micro-manage. If you believe a different approach is better, take it, under the rules in section 12. What matters is that the result is correct, simple, measured and explainable.

**Three kinds of statements appear here. Treat them differently:**
- **MUST / NEVER** are hard rules. Do not break them without asking the human.
- **Recommended** means a sensible default. Change it if you have a better reason, and record the reason in `STATUS.md` under Decisions.
- **[verified]** and **[unverified]** tags mark facts about external services. Verified facts were checked by web search on 2026-10-28. Unverified facts are the author's recollection or assumption. Check them early, with a real request or command, and record the result in `STATUS.md` under Verified facts. Do not build on an unverified fact without checking it.

**Why this project exists:** it is a portfolio project for a job search (ML engineering, LLMOps, MLOps, DevOps flavoured). The owner must be able to explain, in an interview, why each technology was chosen. Every component must solve a named problem. Nothing is added to look impressive.

---

## 1. Product in one paragraph

A user enters their Lichess username. The system downloads their recent public games, analyses every move with Stockfish, and compares the player's mistakes against what is typical for players of the same rating and time control. It finds the 3 to 5 recurring weaknesses that cost them the most, and an LLM coach explains them in plain language, backed by real positions from their own games. It then suggests practice puzzles that target those weaknesses. The user can ask follow-up questions in chat.

**Example finding:** "When you have under 20 seconds left, you blunder pieces 2.1x as often as players at your rating. This cost you about N% win probability across 14 games. Here are three positions."

---

## 2. The core design rule: three layers, three jobs

1. **Stockfish decides what is true about a position.** Move quality comes only from the engine.
2. **Machine learning decides what is unusual about this player.** It compares the player to peers and ranks weaknesses with honest confidence.
3. **The LLM explains and converses.** It turns structured findings into coaching language.

**NEVER let the LLM evaluate a chess position, suggest a move as "best", or state a chess fact that did not come from a tool result.** LLMs are unreliable at chess. Every number, move, and position in a report or chat answer must trace back to stored evidence (engine output, a database row, or a model result). This is the most important rule in the project and the core of its credibility.

---

## 3. Goals, non-goals, and honest claims

**Goals**
- Reports that are correct, grounded, and useful.
- A real pipeline with background jobs, caching, a trained model with proper evaluation, and an LLM component that is tested.
- Production practices that solve real problems here: queueing, rate-limit handling, retries, observability, CI quality gates, reproducible deployment.
- A public demo that anyone can try with their own Lichess username.

**Non-goals (do not build)**
- Kubernetes, Kafka, service mesh, multiple vector databases, microservices beyond what section 7 needs.
- Training a chess engine or a chess-playing model.
- Playing games, bots, or anything that interacts with Lichess accounts beyond reading public game data.
- User accounts, passwords, payments.
- Any paid service other than possible small cloud costs the owner approves.

**Honest-claims rule.** README, docs, and resume bullets may contain only numbers that were measured and are reproducible from this repo. Do not claim that the tool "improves players". The defensible claim is that it finds consistent, predictive weaknesses (see the predictive-validity experiment, section 6.5). If a result is weak, say so plainly in the docs.

---

## 4. Data sources

### 4.1 Lichess public database (bulk files). The source for the peer baseline.
- [verified] Lichess publishes complete public exports under **CC0**: all rated games (PGN), all puzzles with themes and ratings, and all evaluated positions. Site: database.lichess.org.
- [verified] Games include clock times from 2017 onward. Only a minority of games (about 6%) carry Stockfish evaluations.
- [unverified] Exact file sizes. Monthly game files are very large. Check size before downloading anything. Start with a **sample** (one or two months, filtered by rating band and time control, a few hundred thousand games). Scale up only if model metrics demand it.
- [unverified] Games that have evaluations probably exist because someone requested analysis, which may bias them (for example towards lost games or more engaged players). Investigate this bias and document it. If it is large, compute your own Stockfish evals on a random sample at modest depth and compare.
- [unverified] PGN headers include ECO/opening names, ratings, time control, and result, and move comments include `[%clk]` clock values where available. Confirm on real files.
- These files are used offline. No API and no rate limit is involved.

### 4.2 Lichess API. Used only for one user's games at request time.
- [verified] Official rules (lichess.org/page/api-tips): only make **one request at a time**; on HTTP **429**, wait a **full minute** before using the API again. Lichess says exact per-endpoint limits vary and cannot be listed, and that following those two rules should keep you safe. They prefer being asked to add an endpoint over scraping.
- [unverified] That the user-games export supports a `since` filter and a `max` count. Test early.
- [unverified] Whether public games need a token. Tokens exist and are said to raise limits. Test without one first.
- [unverified] Third-party summaries claim specific per-endpoint numbers (for example, one concurrent download per user for game export). Do not rely on them. Use only the two official rules.
- **Design consequences (MUST):**
  - Fetch a user's games as **one streamed request** (NDJSON), never one request per game.
  - All Lichess API calls go through **one global queue with concurrency 1**. A 429 pauses that whole queue for 60 seconds.
  - Cache fetched games in the database. A re-run fetches only new games.
  - Cap games per user (recommended default 300, configurable).
  - Provide a **PGN upload fallback** so the product works with no API call. [unverified] Lichess lets players export their own games as PGN; confirm.
  - During development and tests, use fixtures and the bulk files, not the live API.

### 4.3 Puzzles
- [verified] Lichess publishes puzzles with themes and ratings (CC0). Inspect the actual theme names in the file and build the weakness-to-theme mapping from what exists, not from memory.

### 4.4 Groq (LLM)
- The owner will provide `GROQ_API_KEY` through a local `.env` file. **NEVER commit it, print it, or paste it into docs, tests, or logs.** Keep `.env` in `.gitignore` and provide `.env.example` with placeholders.
- Model names and free-tier rate limits change. Check Groq's current documentation at build time. Make the model name an environment variable and do not hard-code limits from memory.
- Every LLM call must have a timeout, bounded retries, and a fallback (section 8).

---

## 5. Architecture (recommended)

```
Browser UI
   |
FastAPI  --- Postgres (users, games, moves, jobs, reports, evidence, models)
   |
Redis queues
   |-- lichess queue  -> 1 worker, concurrency 1  (fetch games, respect 429)
   |-- analysis queue -> N workers (Stockfish, CPU heavy, cached by position)
   |-- report queue   -> workers (weakness engine + LLM coach)
   |
Model artifacts (MLflow or file registry)      Groq API (LLM)
Prometheus + Grafana (metrics)                 Docker Compose (local), cloud later
```

**Why these pieces (each has a problem it solves, so you can defend it in an interview):**
- **Queue + workers:** Stockfish analysis takes minutes per user, which cannot block a web request. Separate queues let the Lichess queue stay at concurrency 1 while analysis scales with CPU.
- **Postgres:** games, moves, jobs, and evidence are relational, and caching and resumability need transactions.
- **Redis:** simple, mature job broker.
- **Position cache (keyed by FEN + engine version + depth/nodes):** opening positions repeat constantly, so this saves large amounts of CPU. Cache hit rate is a measurable result.
- **Model registry/tracking:** lets a new model be compared to the current one before it ships.
- **Prometheus/Grafana:** queue depth, latency, 429 counts, cache hits, LLM grounding failures and cost are the numbers that tell you what is actually wrong.

---

## 6. The ML component

### 6.1 Move labels (from Stockfish)
- For each move, compute the evaluation before and after, from the mover's perspective. Convert centipawns to win probability. As recalled by the author, Lichess uses `win% = 50 + 50 * (2 / (1 + exp(-0.00368208 * cp)) - 1)`. [unverified] Verify against Lichess documentation or source and record the exact formula.
- Classify inaccuracy, mistake, blunder by win-probability drop. Recommended starting thresholds of roughly 10, 20, and 30 percentage points. [unverified] Make them configurable and document them.
- Handle mate scores explicitly. Record Stockfish version and search limit with every analysis row.
- Use a fixed node or depth limit for reproducibility. Choose the default by benchmarking (section 9, first task of Chunk 1).

### 6.2 Features (keep them explainable)
Game phase, move number, clock remaining (seconds and fraction of total), time spent on the move, time control category, player rating and opponent rating, material balance, side to move, piece moved, capture/check flags, number of legal moves, evaluation sharpness, and opening (ECO). Add more only if they help and can be explained to a user.

**MUST:** use one shared feature-extraction code path for training and for serving. Duplicated feature logic is the classic source of silent train/serve skew.

### 6.3 Peer baseline model
- Learn `P(blunder | features, rating band, time control)` from the sampled bulk data. Recommended: gradient-boosted trees (LightGBM or XGBoost) after a simple logistic-regression baseline. Report both. Keep the simpler one if the gain is small.
- Evaluate with a **player-level split** (no player appears in both train and test). Report log-loss, Brier score, and a calibration/reliability plot, by rating band and time control. Calibration matters more than accuracy here, because the model's output is used as an expected rate.
- Track experiments and register the chosen model (MLflow recommended).

### 6.4 Weakness ranking
- Group the user's moves into slices (for example: clock bucket x phase, opening family, piece type, time control).
- For each slice compute observed blunder rate versus expected rate from the baseline model. Use a statistical method that handles small samples honestly (shrinkage or confidence intervals). Show a confidence level; mark thin slices as uncertain; never present a noisy slice as a finding.
- Rank by expected win-probability cost = excess blunders x average loss per blunder.
- Return the top 3 to 5 weaknesses, each with: slice definition, observed vs expected rate, confidence, estimated cost, and evidence IDs pointing to specific stored positions.

### 6.5 The key experiment: predictive validity
This is what separates a real finding from noise.
- Using players from the bulk data who have many games, find weaknesses from their earlier games and test whether those slices also show excess blunders in their later, held-out games.
- Report the result honestly, including a null or weak result. Document the setup so it is reproducible.
- Optional supporting evidence: have a few friends rate the usefulness of 20 to 30 reports.

---

## 7. The LLM coach (Groq, LangGraph recommended)

**Inputs the LLM may use:** only structured tool outputs (weaknesses, evidence positions, comparisons, puzzle lists).

**Tools (names indicative):** `get_weaknesses`, `get_examples(slice)`, `get_game_summary(game_id)`, `compare_to_peers(slice)`, `get_drills(weakness)`.

**Report generation**
- The LLM returns structured output: coaching text plus a list of **claims**, each citing evidence IDs.
- A **grounding verifier** (plain code, not an LLM) checks that every number, move, and position in the text matches the cited evidence. If verification fails, regenerate up to a small fixed number of times, then fall back to a **deterministic template report** built from the same data. A user must never see an unverified claim.
- Log every verification result. Grounding pass rate is a tracked metric.

**Chat**
- Answers only from the user's analysed data via tools. If asked something outside the data (for example, "what is the best move here?"), say that it can show the engine's stored evaluation or that the question is out of scope. Do not improvise chess analysis.

**Evaluation**
- Build a fixed test set of recorded tool outputs plus expected properties (no fabricated numbers, claims cite evidence, refusal on out-of-scope). Run it in CI against recorded responses (deterministic) and, occasionally, against live Groq (manual).

---

## 8. Production practices (each solves a real problem here)

- **Idempotent, resumable jobs.** Store per-game analysis as it completes. After a crash or restart, skip finished games.
- **Retries and backoff** on Lichess (429 handling as above), Groq (timeouts, 429, 5xx), and worker failures. Dead-letter failed jobs with a visible reason.
- **Graceful degradation.** If Groq is down or grounding keeps failing: template report. If the Lichess queue is paused: show queue position and estimated wait. Never fail silently.
- **Metrics (Prometheus):** queue depth per queue, job latency (p50/p95) per 100 games, positions analysed per second, cache hit rate, Lichess 429 count, LLM latency and token usage, grounding pass rate, estimated cost per report. Dashboards in Grafana.
- **Structured JSON logs** with a job ID on every line.
- **CI (GitHub Actions):** lint, unit tests, integration tests using docker compose, **model quality gate** (a new model may not be worse than the registered one on calibration), **grounding eval gate**, image build.
- **Load and failure tests:** measure throughput; kill a worker mid-job; simulate a Lichess 429 and a Groq timeout; record what happened. The incident notes become interview stories.
- **Security and privacy:** use only public game data; validate the username input; rate-limit your own API per IP; provide a delete-my-data endpoint; keep secrets out of the repo and logs; attribute Lichess and do not imply affiliation.
- **Reproducibility:** pin dependency and image versions; record Stockfish version, model version, prompt version, and LLM model with every report.

---

## 9. Chunks

Seven chunks, each large enough to be meaningful. Work in order unless you have a good reason not to. After each chunk: all tests pass, `docker compose up` works from a clean clone, `STATUS.md` is updated, and the work is committed (tag the chunk, for example `chunk-1-done`).

### Chunk 0: Foundation
Repo layout, `docker-compose` (API, worker, Postgres, Redis), health endpoint, config via environment, linting, test setup, CI skeleton, `.env.example`, README stub.
**Done when:** a clean clone runs `docker compose up` and the health check passes; CI is green.

### Chunk 1: Ingest and analyse
- **First task:** check all [unverified] Lichess API facts with real requests, and benchmark Stockfish (positions per second per core at candidate depth/node limits). Record results in `STATUS.md`. Pick the default engine limit from the benchmark.
- Lichess client with the single-concurrency queue, 429 backoff, NDJSON streaming, caching, game cap, and PGN upload fallback.
- Parse games, clocks, openings. Stockfish worker, position cache, move-level analysis table, job status with progress, resumability.
**Done when:** a username (or uploaded PGN) produces a fully analysed set of games in the database; a re-run reuses cache; benchmark and cache-hit numbers are recorded.

### Chunk 2: Weakness engine
Sample builder for bulk data (download, filter, parse, labels), shared feature pipeline, baseline models with tracking, calibration evaluation, the selection-bias check from section 4.1, weakness ranking with confidence, and the predictive-validity experiment.
**Done when:** a user's analysed games yield ranked weaknesses with evidence IDs, and a metrics table (calibration, predictive validity) is saved in `docs/results.md`.

### Chunk 3: Coach
Groq client with timeout, retry and model config; LangGraph agent and tools; report generator with claims and evidence; grounding verifier; template fallback; chat endpoint; recorded-fixture eval suite.
**Done when:** reports pass the grounding verifier on the fixture set, failures fall back correctly, and the grounding pass rate is measured.

### Chunk 4: Product surface
Simple web UI: submit username or upload PGN, see progress and queue position, view the report with chessboards for example positions, chat, and a puzzle drill list mapped from weaknesses (themes taken from the real puzzle file).
**Done when:** the full flow works in a browser from a clean `docker compose up`.

### Chunk 5: Production and cloud
Metrics and dashboards, structured logs, CI quality gates, load test, failure-injection tests, deployment to the cloud with infrastructure as code, secrets handling, a billing alarm, and a short runbook.
- **Deployment guidance (recommended):** the owner wants to learn AWS. Prefer infrastructure as code (Terraform) and containers. Choose the simplest option that is defensible. A single VM running compose is acceptable if cost is a concern; ECS Fargate is a good learning target. Avoid Kubernetes. Cost-check before provisioning, because prices were not verified here. Ask the owner before creating any paid resource.
**Done when:** a public URL works, dashboards show real traffic, and a failure-test report exists.

### Chunk 6: Evidence and polish
README with architecture diagram and a "why this tech" table, short design notes for key decisions, `docs/results.md` with measured numbers only, a two-minute demo script, and resume bullets drawn strictly from measured results.
**Done when:** a stranger can run it from the README and the claims match the numbers.

---

## 10. Technology defaults (all changeable with a recorded reason)

| Area | Default | Notes |
|---|---|---|
| Language | Python 3.12+ | |
| API | FastAPI | async, typed, auto docs |
| DB | PostgreSQL + migrations (Alembic) | |
| Queue | Redis with RQ or Arq (Celery acceptable) | pick the simplest that supports separate queues and concurrency control |
| Chess | python-chess, Stockfish (pinned version) | record version per row |
| Data processing | pandas or polars; DuckDB optional for large files | |
| ML | scikit-learn, LightGBM or XGBoost, MLflow | |
| LLM | Groq via official SDK or compatible endpoint, LangGraph | model name in env |
| Frontend | React + Vite + TypeScript with a chessboard component, or server-rendered templates | choose the simpler for a solo build |
| Observability | Prometheus, Grafana, structured logs | OpenTelemetry optional |
| CI | GitHub Actions | |
| Infra | Docker Compose locally; Terraform + AWS later | see Chunk 5 |
| Secrets | `.env` locally; cloud secret manager in deployment | never commit |

---

## 11. Suggested repo layout (adjust freely)

```
backend/      API, workers, DB models, migrations
ml/           data sampling, features, training, evaluation
frontend/     web UI
infra/        compose files, Terraform, monitoring config
docs/         results.md, design notes, runbook, demo script
tests/        unit, integration, fixtures (recorded LLM/tool outputs)
SOURCE_OF_TRUTH.md
STATUS.md
```

---

## 12. Rules for using your own judgement

**You may change** any "Recommended" item: libraries, folder layout, chunk order, queue technology, frontend approach, thresholds, even the deployment target.

**When you change something:**
1. Prefer the simpler option unless there is a concrete reason.
2. Record the change and the reason in `STATUS.md` under **Decisions** (date, what, why, alternatives rejected).
3. If it affects the owner's money, security, or the core rules, ask first.

**You MUST NOT, without asking the owner:**
- Break the three-layer rule (section 2) or let the LLM judge chess.
- Commit, log, or print secrets.
- Add a component that does not solve a named problem in this project.
- Remove evaluation or quality gates to make something pass.
- Provision paid cloud resources.
- Publish unmeasured numbers.

**When something is unclear or a fact is unverified:** test it with a real command or request, record the result, and proceed. Ask the owner only about money, secrets, or product decisions.

**Working style:** small commits, tests with each feature, no speculative abstractions, no placeholder code left behind. Prefer boring technology that works.

---

## 13. What the owner provides

- `GROQ_API_KEY` in a local `.env` (never committed). If the key is ever exposed, regenerate it.
- Docker Desktop running on the machine.
- Approval for any paid cloud resource.
- Optional: a Lichess API token, if testing shows one helps.

---

## 14. Known risks to watch (decide by evidence)

1. **Stockfish compute cost per user.** The author's rough estimate is about 24,000 positions for 300 games at about 80 moves each, but this is unmeasured. Benchmark first. Levers: fewer games, lower depth or node limit, position cache, more workers.
2. **Selection bias in evaluated games** (section 4.1).
3. **Small-sample noise** in per-user slices. Honest confidence is part of the product.
4. **Peer baseline covers Lichess players only.** Say so.
5. **LLM drift or model changes on Groq.** Pin the model name; keep the grounding gate.
6. **Lichess API limits unknown in detail.** Respect the two official rules and the single queue.
7. **Laptop resources.** Docker Desktop plus Stockfish workers can saturate a laptop; set worker counts conservatively.

---

## 15. Plain-language glossary

- **Blunder:** a move that drops a large share of your winning chances, as judged by the engine.
- **Peer baseline:** how often players of your rating blunder in the same kind of situation.
- **Calibration:** whether a predicted 20% really happens about 20% of the time.
- **Grounding:** every statement the coach makes can be traced to stored evidence.
- **Shadow testing:** running a new model quietly beside the current one to compare before switching.
