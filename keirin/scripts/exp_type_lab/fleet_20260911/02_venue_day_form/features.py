#!/usr/bin/env python3
"""会場×日の「決着傾向」を作り、板（/tmp/race_type_board.npz）へ結合する。

朝 7:15 時点で確定しているものだけ:
  prev_*   同一会場・前日（暦日 −1・結果のある日）の集計
  cup_*    同一開催（cup_id）の前日までの集計（初日は NaN）
  v365_*   その会場の直近365日（前日まで）の平均＝「普段」
  r_prev_* / r_cup_* = prev − v365 / cup − v365（乖離）
  g_prev_* 全場・前日（会場固有かどうかの対照）
出力: feat.pkl（板の行順・N=36,427）
"""
import os, sys
import numpy as np, pandas as pd

D = os.path.dirname(os.path.abspath(__file__))
df = pd.read_pickle(os.path.join(D, "races.pkl"))
df["date"] = pd.to_datetime(df["date"])
df["nige"] = (df.f1 == "逃").astype(float)
df["sashi"] = (df.f1 == "差").astype(float)
df["makuri"] = (df.f1 == "捲").astype(float)
df["ltf"] = np.log(df.tf_odds.astype(float))
df["same12"] = df.same12.astype(float)
df["mark1"] = df.mark1_in3.astype(float)
df["idx1"] = df.idx1_in3.astype(float)
COLS = ["nige", "sashi", "makuri", "ltf", "same12", "mark1", "idx1"]

# ── 会場×日 ──
vd = df.groupby(["venue", "date"]).agg(n=("race_key", "size"), **{c: (c, "mean") for c in COLS},
                                       ltf_med=("ltf", "median"), cup_id=("cup_id", "first"))
vd = vd.reset_index()

# 前日（暦日 −1）
prev = vd.copy(); prev["date"] = prev["date"] + pd.Timedelta(days=1)
prev = prev.rename(columns={c: f"prev_{c}" for c in COLS + ["ltf_med", "n", "cup_id"]})
vd = vd.merge(prev, on=["venue", "date"], how="left")
vd["prev_same_cup"] = (vd.cup_id == vd.prev_cup_id)

# 開催ここまで（同 cup_id・前日まで）— レース単位で累積
dc = df.sort_values("date").groupby(["cup_id", "date"]).agg(n=("race_key", "size"), **{c: (c, "sum") for c in COLS}).reset_index()
cs = dc.groupby("cup_id")[["n"] + COLS].cumsum()
cs.columns = ["cum_" + c for c in cs.columns]
dc = pd.concat([dc, cs], axis=1)
# 前日までの累積 = 当日累積 − 当日
for c in ["n"] + COLS:
    dc[f"cup_{c}"] = dc[f"cum_{c}"] - dc[c]
for c in COLS:
    dc[f"cup_{c}"] = dc[f"cup_{c}"] / dc["cup_n"].replace(0, np.nan)
vd = vd.merge(dc[["cup_id", "date", "cup_n"] + [f"cup_{c}" for c in COLS]], on=["cup_id", "date"], how="left")

# 会場の直近365日（前日まで）
rows = []
for v, g in df.groupby("venue"):
    d = g.groupby("date").agg(n=("race_key", "size"), **{c: (c, "sum") for c in COLS})
    idx = pd.date_range(d.index.min(), df.date.max() + pd.Timedelta(days=1))
    d = d.reindex(idx).fillna(0.0)
    cum = d.cumsum()
    w = cum.shift(1) - cum.shift(366)          # [date-365, date-1]
    w = w.fillna(cum.shift(1))                  # 期間の頭は手前ぶん全部
    out = pd.DataFrame({"venue": v, "date": w.index, "v365_n": w["n"].values})
    for c in COLS:
        out[f"v365_{c}"] = (w[c] / w["n"].replace(0, np.nan)).values
    rows.append(out)
v365 = pd.concat(rows)
vd = vd.merge(v365, on=["venue", "date"], how="left")

# 全場・前日（対照）
gd = df.groupby("date").agg(**{f"g_{c}": (c, "mean") for c in COLS}).reset_index()
gd["date"] = gd["date"] + pd.Timedelta(days=1)
gd = gd.rename(columns={f"g_{c}": f"g_prev_{c}" for c in COLS})
vd = vd.merge(gd, on="date", how="left")

for c in COLS:
    vd[f"r_prev_{c}"] = vd[f"prev_{c}"] - vd[f"v365_{c}"]
    vd[f"r_cup_{c}"] = vd[f"cup_{c}"] - vd[f"v365_{c}"]
vd["r_prev_ltf_med"] = vd["prev_ltf_med"] - vd["v365_ltf"]

# ── 板へ結合 ──
z = np.load("/tmp/race_type_board.npz", allow_pickle=True)
KEY, DATE, VENUE = z["KEY"], z["DATE"], z["VENUE"]
board = pd.DataFrame({"race_key": KEY, "date": pd.to_datetime(DATE), "venue": VENUE})
b = board.merge(vd.drop(columns=["n", "cup_id", "prev_cup_id"] + COLS + ["ltf_med"]), on=["venue", "date"], how="left")
b = b.merge(df[["race_key", "cup_id", "day_index", "race_no", "start_ts", "tf_odds"]], on="race_key", how="left")
assert len(b) == len(KEY) and (b.race_key.values == KEY).all()
b.to_pickle(os.path.join(D, "feat.pkl"))
print("board rows", len(b), file=sys.stderr)
print("coverage prev:", b.prev_nige.notna().mean().round(3), " same_cup:", b.prev_same_cup.fillna(False).mean().round(3),
      " cup_so_far:", b.cup_nige.notna().mean().round(3), " v365:", b.v365_nige.notna().mean().round(3), file=sys.stderr)
print(b[[c for c in b.columns if c.startswith("r_prev_") or c.startswith("r_cup_")]].describe().T, file=sys.stderr)
