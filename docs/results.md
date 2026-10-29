# Results (Chunk 2)

Every number here was measured by a script in `backend/ml/` on the data described. Samples are small and come from one month, so treat them as indicative.

**Data.** First 50 MB of Lichess `2026-09` standard rated (160,305 games, from the start of the month). 10.4% of games carry `%eval`. Evaluated games are slower (rapid 32% vs 15%, bullet 21% vs 38%) and about 59 Elo lower on average (`ml/bias.py`). Peer baseline covers Lichess players only.

## Peer baseline (P(blunder | move features))
Blunder = win-chance drop >= 0.3 on Lichess's scale. Labels from Lichess `%eval`. 16,609 evaluated games, 897k moves; test = 20% of players held out entirely (210,471 moves, 4,334 players). `ml/train.py`.

| Model | log-loss | Brier | ECE |
|---|---|---|---|
| Constant rate | 0.2555 | 0.06573 | 0.0017 |
| Logistic regression | 0.2383 | 0.06328 | 0.0033 |
| LightGBM (chosen) | **0.2001** | **0.05689** | 0.0018 |

LightGBM ECE by rating band: 0.0027 (<=1200), 0.0025, 0.0026, 0.0019, 0.0048 (>2100). By speed: bullet 0.0019, blitz 0.0033, rapid 0.0010, classical 0.0123 (only 4,092 moves). Plot: `docs/calibration.png`.

## Our labels vs Lichess's (`ml/consistency.py`)
Same 198 games, 13,409 moves, labelled both ways (Stockfish 19 at 100k nodes vs Lichess server evals): our blunder rate 8.03% vs 6.94% (1.16x). We flag 93.6% of Lichess blunders; 80.9% of ours are also Lichess blunders. So the baseline is shifted (`ml/calibrate.py`, logit shift fitted on these games) to predict OUR labels.

Test on 295 random games without `%eval`, labelled by our engine (19,766 moves): observed/expected blunders 1.23 (95% CI 1.14-1.32) before the shift, **0.98 (0.91-1.05)** after. By speed after the shift: bullet 0.93 (0.82-1.03), blitz 1.06 (0.95-1.17), rapid 0.84 (0.71-1.00). Our blunder rate with vs without `%eval`: 7.92% vs 7.82%, no visible selection effect on blunder rate (speed mix differs).

## Predictive validity (`ml/validity.py`)
60 players with >= 50 games in the sample; first 25 games = discovery, last 25 = holdout (111k moves). **98% of these moves are bullet** (heavy bullet players dominate players with many games), so this says little about slower games. Pooled observed/expected blunders in the holdout, 95% CI from resampling players:

| Slices | Count | Holdout O/E | Discovery O/E |
|---|---|---|---|
| Flagged as weaknesses | 34 (20 players) | **1.43 (1.18-1.62)** | 1.62 |
| Eligible but not flagged (control) | 1,189 | 1.05 (1.00-1.11) | 1.00 |
| Whole player | 60 | 1.07 (1.01-1.13) | 1.02 |

Flagged slices keep excess blunders in held-out games (79% of them above 1), but shrink from 1.62 to 1.43, as expected from selection on noise. Only 20 of 60 players got any finding (0.58 per player). Limits: one month, bullet-dominated, slices not independent, 34 flagged slices is a small count.

## Weakness engine
Gamma(5,5) prior on the observed/expected ratio, report slices with P(ratio>1) >= 0.975, >= 3 blunders, <= 60% of the player's moves. Null simulation (`tests`-style, planted effects): ~0.18 false findings per player, 96% power for a 2.4x effect at that threshold. On two real accounts analysed end to end (50 and 20 games) both were at peer level overall (135 vs 138 and 38 vs 52 expected blunders) and no weakness was reported.

# Results (Chunk 3, coach)

Groq `openai/gpt-oss-120b`, 3 real Lichess players (60 games each, analysed by this app), `ml/eval_coach.py`. Small samples.

- **Reports** (strict verifier: every number copied from the cited facts, every move from cited examples, each claim must quote its weakness's numbers): 3/6 grounded on the first try, 5/6 after up to 2 regenerations with error feedback, 1/6 replaced by the deterministic template. No ungrounded report was shown. Median latency 4.8 s (max 38 s), about 5.4k tokens per report. An earlier, looser verifier passed 12/12, but those reports quoted no numbers, so the rule was tightened.
- **Chat**: in-scope questions 4/4 grounded after the overview and weakness list were put in the prompt (2/6 before, due to Groq tool-loop errors). Out-of-scope questions (best move in an unseen position, opening advice): 4/6 answered with a refusal to analyse, 2/6 were blocked by the verifier and replaced by a fixed refusal.

# Results (after Chunk 4: speed, report quality; Chunk 5: production)

Measured 2026-10-29 on the dev laptop (Docker Desktop, 24 logical CPUs, 8 GB for Docker).

## Engine throughput and two-pass analysis
- **Parallel Stockfish scaling** (`scripts/bench_parallel.py`, 100k nodes): total 4.1 positions/s
  with 1 engine, 19.0 with 4, 26.2 with 8, 31.1 with 16, 32.4 with 20. Beyond 8 engines the
  laptop adds little, so more workers alone cannot fix a 15-minute job (25k positions / 30 per s).
- **Two-pass analysis** (`ml/two_pass.py`): every position at a low node limit, then both
  positions around any move whose drop is >= a margin re-evaluated at 100k. Reference: the
  same 295 random games fully labelled at 100k (1,511 blunders, 20,364 positions).

| Pass-1 nodes | Margin (win-% pts) | Blunders kept | Speed vs 100k everywhere |
|---|---|---|---|
| 5k | 5 | 98.4% | 2.77x |
| **10k** | **5** | **99.1%** | **2.48x** (chosen) |
| 20k | 5 | 99.7% | 1.97x |
| 10k | 10 | 97.4% | 3.56x |

  Moves below the margin keep pass-1 evals and cannot be blunders (threshold 15 points), so
  every blunder shown still rests on 100k-node evaluations at both ends.
- **End to end, real Lichess user** (penguingm1, 299 games, live API, 8 workers, empty
  cache): **260 s** to fully analysed (was about 15 minutes for drnykterstein, 300 games,
  4 workers, one pass); the first 50 games, enough for an early report, after 60 s. Single
  uploaded player, 50 games: 37 s.

## Report content
drnykterstein (300 games): before, the template said "about what peers would be expected"
for 430 vs 764.3 expected blunders and showed nothing else. Now: "fewer than the 764.3 ...
(0.56x the peer rate)", a relative weak spot (30-60 s left in the middlegame, 183 vs 153.0
expected at his own level, 1.2x, confidence 99%), a breakdown by phase, time left and time
control, and a note that few training players are rated as high (3,829 of 1.07M training
moves are by players rated 2700+, 366 by 2900+).

## Coach (live Groq, prompt coach-v3, 4 players x 2 reports + 20 chat questions)
- Reports: 8/8 ended as grounded LLM text (0 template), but only 1/8 on the first try.
  53 of 61 first-attempt errors were field names (e.g. "observed_blunders") copied into the
  text, which a new verifier rule now blocks; median latency 37 s, 8.7k tokens per report.
  Prompt coach-v4 shows the model readable keys to fix this; **not yet measured** (Groq
  daily quota used up, see below).
- Chat: in-scope 11/12 grounded (including "why do I blunder in the endgame" for all 4
  players, now answered from the breakdown), out-of-scope 8/8 handled.
- Re-scoring the Chunk 3 recordings with the field-name rule: 9 of 22 verdicts change
  (outputs that read like "observed_blunders 183" are now blocked).
- **Groq free tier also limits tokens per day: 200,000 for gpt-oss-120b** (seen in a 429
  body). At ~5-9k tokens per report that is roughly 25-40 LLM reports a day; after that,
  reports use the template. The client now falls back at once on a daily-limit 429.

## Load and failure tests
See `docs/failure-tests.md`. Load test (3 users x 25 games at once): pages p50 46 ms /
p95 107 ms over 4,347 requests; users served in turn (half done after 48 / 60 / 70 s).

## Model quality gate
Shipped LightGBM on the committed held-out sample (30,000 moves from test-split players,
`models/eval/holdout.csv.gz`): log-loss 0.2032, Brier 0.0580, ECE 0.0035 (`ml/gate.py`).
