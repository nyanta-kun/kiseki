"""実売の予測オッズ(bet_detail.odds)と確定オッズ(keirin.wt_odds)の比を
月別・オッズ帯別・車数別に集計する（2026-08-07〜09-20・全券種）。
B_live の subs.csv / odds_*.csv（既に取得済み）を再利用する。
"""
import csv, glob, json, sys
import numpy as np, pandas as pd

sys.path.insert(0, "/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/5a78b9ec-b8ca-47de-8015-b5191bfb9e70/scratchpad/audit/B_live")
from mklib import parse_bd, ORDERED, combo_label  # noqa: E402

BASE = "/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/5a78b9ec-b8ca-47de-8015-b5191bfb9e70/scratchpad/audit"
LIVE = f"{BASE}/B_live"

subs = list(csv.DictReader(open(f"{LIVE}/subs.csv")))
odds = {}
for p in sorted(glob.glob(f"{LIVE}/odds_*.csv")):
    for r in csv.DictReader(open(p, newline="")):
        odds[(r["rk"], r["bt"], r["cb"])] = float(r["odds_value"]) if r["odds_value"] else None

rows = []
for s in subs:
    if not s["bet_detail"]:
        continue
    rk = s["race_key"]
    if rk[:8] < "20260807":
        continue
    d = parse_bd(s["bet_detail"])
    if not d:
        continue
    for ln in d.get("lines") or []:
        o = ORDERED.get(str(ln.get("bet_type") or ""))
        lab = combo_label(ln.get("combo"), o)
        if lab is None:
            continue
        cars = lab.replace("=", "-")
        bt = "trifecta" if o else "trio"
        fo = odds.get((rk, bt, cars))
        po = ln.get("odds")
        if not fo or not po:
            continue
        rows.append(dict(rk=rk, ym=rk[:6], bt=bt, n_entries=s["n_entries"],
                          rank_key=s["rank_key"], po=float(po), fo=float(fo)))

df = pd.DataFrame(rows)
df["logratio"] = np.log10(df.po / df.fo)
df["ratio"] = df.po / df.fo
pd.set_option("display.width", 140)

print("n=", len(df), "races=", df.rk.nunique())
print("\n=== 月別 ===")
print(df.groupby("ym").agg(n=("ratio", "size"), median_ratio=("ratio", "median"),
                            mae_log=("logratio", lambda s: s.abs().mean()),
                            within2x=("ratio", lambda s: ((s >= .5) & (s <= 2)).mean())).round(4))

print("\n=== 月別×車数 ===")
print(df.groupby(["ym", "n_entries"]).agg(n=("ratio", "size"), median_ratio=("ratio", "median"),
                                           mae_log=("logratio", lambda s: s.abs().mean())).round(4))

print("\n=== 月別×券種 ===")
print(df.groupby(["ym", "bt"]).agg(n=("ratio", "size"), median_ratio=("ratio", "median")).round(4))

df["band"] = pd.cut(df.po, [0, 2, 5, 10, 20, 50, 100, 1e9],
                     labels=["0-2", "2-5", "5-10", "10-20", "20-50", "50-100", "100+"])
print("\n=== 月別×予測オッズ帯 ===")
print(df.groupby(["ym", "band"], observed=True).agg(
    n=("ratio", "size"), median_ratio=("ratio", "median")).round(4).to_string())

print("\n=== 週別（発走日の週）===")
df["week"] = pd.to_datetime(df.rk.str[:8]).dt.to_period("W")
wk = df.groupby("week").agg(n=("ratio", "size"), median_ratio=("ratio", "median"),
                             mae_log=("logratio", lambda s: s.abs().mean()))
print(wk.round(4))

df.to_pickle(f"{BASE}/P3_oddspred/real_ratio_rows.pkl")
