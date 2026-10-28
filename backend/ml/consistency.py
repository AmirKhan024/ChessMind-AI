"""Compare our engine's labels with labels from Lichess %eval on the same moves,
and our-engine blunder rates in games with vs without %eval (selection bias).
Usage: python -m ml.consistency DIR"""

import sys

import pandas as pd

d = sys.argv[1]
c = pd.read_json(f"{d}/consistency_labels.jsonl", lines=True)
c = c[c.has_lichess]
c["ours_b"] = c.ours == "blunder"
c["li_b"] = c.lichess == "blunder"
print(f"moves={len(c)} games={c.game_id.nunique()}")
print(pd.crosstab(c.ours.fillna("ok"), c.lichess.fillna("ok"), margins=True))
both = (c.ours_b & c.li_b).sum()
print(f"blunder rate ours={c.ours_b.mean():.4f} lichess={c.li_b.mean():.4f}")
print(
    f"of lichess blunders, ours agree={both / c.li_b.sum():.3f}; "
    f"of our blunders, lichess agree={both / c.ours_b.sum():.3f}"
)
print("by speed:", c.groupby("speed")[["ours_b", "li_b"]].mean().round(4).to_dict("index"))

r = pd.read_json(f"{d}/random_labels.jsonl", lines=True)
r["ours_b"] = r.ours == "blunder"
all_c = pd.read_json(f"{d}/consistency_labels.jsonl", lines=True)
all_c["ours_b"] = all_c.ours == "blunder"
print("\nselection bias, our labels, all moves:")
for name, x in [("with %eval", all_c), ("without %eval", r)]:
    se = (x.ours_b.mean() * (1 - x.ours_b.mean()) / len(x)) ** 0.5
    by = x.groupby("speed").ours_b.mean().round(4).to_dict()
    print(
        f"  {name}: games={x.game_id.nunique()} moves={len(x)} "
        f"blunder_rate={x.ours_b.mean():.4f} (se {se:.4f}) by speed {by}"
    )
