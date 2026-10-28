"""Agreement between our engine's labels and Lichess-%eval labels on the same moves, and
our-engine blunder rates in games with vs without %eval (selection bias).
Usage: python -m ml.consistency DIR"""

import sys

import pandas as pd

d = sys.argv[1]
c = pd.read_json(f"{d}/consistency_ours.jsonl", lines=True)
r = pd.read_json(f"{d}/random_ours.jsonl", lines=True)
both = c[c.has_lichess]
print(f"moves labelled both ways={len(both)} games={both.game_id.nunique()}")
print(pd.crosstab(both.label.fillna("ok"), both.lichess_label.fillna("ok"), margins=True))
ours, theirs = both.label == "blunder", both.lichess_label == "blunder"
print(
    f"blunder rate ours={ours.mean():.4f} lichess={theirs.mean():.4f} "
    f"ratio={ours.sum() / theirs.sum():.3f}"
)
print(
    f"lichess blunders we also flag={(ours & theirs).sum() / theirs.sum():.3f}; "
    f"our blunders lichess also flags={(ours & theirs).sum() / ours.sum():.3f}"
)
for name, x in [("with %eval", c), ("without %eval", r)]:
    print(
        f"{name}: games={x.game_id.nunique()} moves={len(x)} our blunder rate="
        f"{x.blunder.mean():.4f} by speed {x.groupby('speed').blunder.mean().round(4).to_dict()}"
    )
