#!/usr/bin/env python3
"""波ごとの可用性。前日分は type_lab_picks.settled_at で実測済（別クエリ）。ここは
「同一会場・当日ここまで」が各波の組立時刻に何レースあるか（構造上の上限）を start_ts から出す。"""
import os, numpy as np, pandas as pd
D = os.path.dirname(os.path.abspath(__file__))
df = pd.read_pickle(os.path.join(D, "races.pkl"))
df["date"] = pd.to_datetime(df["date"])
df = df[df.start_ts.notna() & (df.date >= "2026-01-01")].copy()
SETTLE = pd.Timedelta(minutes=30)      # 発走→採点済の中央 24分・p90 30分
first = df.groupby(["venue", "date"]).start_ts.min().rename("first_ts")
df = df.merge(first, on=["venue", "date"])
h = df.first_ts.dt.hour
df["wave"] = np.where(h >= 18, "evening(18:05)", np.where(h >= 12, "noon(13:05)", "morning(07:15)"))
build = {"morning(07:15)": pd.Timedelta(hours=7, minutes=15), "noon(13:05)": pd.Timedelta(hours=13, minutes=5), "evening(18:05)": pd.Timedelta(hours=18, minutes=5)}
df["build_ts"] = df.date + df.wave.map(build)
# 同一会場・当日・組立時刻までに採点済のレース数
done = df.assign(done_ts=df.start_ts + SETTLE)[["venue", "date", "done_ts"]]
cnt = []
for (v, d), g in df.groupby(["venue", "date"]):
    dts = done[(done.venue == v) & (done.date == d)].done_ts.values
    bt = g.build_ts.iloc[0]
    cnt.append((v, d, int((dts <= np.datetime64(bt)).sum()), len(g)))
c = pd.DataFrame(cnt, columns=["venue", "date", "n_done_at_build", "n"])
c = c.merge(df[["venue", "date", "wave"]].drop_duplicates(), on=["venue", "date"])
print("会場×日 (2026):", len(c))
print(c.groupby("wave").agg(days=("n", "size"), races=("n", "sum"), done_at_build_mean=("n_done_at_build", "mean"), any_done=("n_done_at_build", lambda s: (s > 0).mean())))
# 前日（暦日−1・同一会場）が存在する割合、同一開催の割合
vd = df.groupby(["venue", "date"]).agg(cup=("cup_id", "first"), day_index=("day_index", "max"), n=("race_key", "size")).reset_index()
vd["prev_date"] = vd.date - pd.Timedelta(days=1)
p = vd[["venue", "date", "cup"]].rename(columns={"date": "prev_date", "cup": "prev_cup"})
vd = vd.merge(p, on=["venue", "prev_date"], how="left")
print("\n前日（同一会場・暦日−1）が存在するレースの割合:", round((vd.prev_cup.notna() * vd.n).sum() / vd.n.sum(), 3),
      " うち同一開催:", round(((vd.prev_cup == vd.cup) * vd.n).sum() / vd.n.sum(), 3))
print("day_index 別のレース割合:", (vd.groupby("day_index").n.sum() / vd.n.sum()).round(3).to_dict())
