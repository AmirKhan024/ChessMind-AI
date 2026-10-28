"""Do games with Lichess %eval differ from games without? Usage: python -m ml.bias DIR"""

import sys

import pandas as pd

m = pd.read_parquet(f"{sys.argv[1]}/meta.parquet")
m = m[m.usable & m.speed.isin(["bullet", "blitz", "rapid", "classical"])]
m["avg_elo"] = (m.white_elo + m.black_elo) / 2
m["decisive"] = m.result.isin(["1-0", "0-1"])
m["time_forfeit"] = m.termination == "Time forfeit"
g = m.groupby("has_eval")
print(g.size().rename("games"))
print(g[["avg_elo", "plies", "decisive", "time_forfeit"]].mean().round(3))
print(pd.crosstab(m.speed, m.has_eval, normalize="columns").round(3))
bands = pd.cut(m.avg_elo, [0, 1200, 1500, 1800, 2100, 3500])
print(pd.crosstab(bands, m.has_eval, normalize="columns").round(3))
