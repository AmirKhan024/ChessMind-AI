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
