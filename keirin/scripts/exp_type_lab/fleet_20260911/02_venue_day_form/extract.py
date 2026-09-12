#!/usr/bin/env python3
"""DB からレース単位の「決着」記述を抜く（読み取りのみ）。

出力: races.pkl（1行=1レース・結果のあるもの）
  race_key venue date cup_id day_index race_no n_entries start_ts race_type grade
  f1 (勝者の決まり手) fr1 fr2 fr3 (着順の車番) same12 (1-2着が同ライン)
  mark1_in3 (◎が3着内) idx1_in3 (pred_top3_pct 1位が3着内・2024〜)
  tf_odds (的中三連単の確定オッズ・wt_odds の最終値)
"""
import os, sys
import pandas as pd, psycopg2

OUT = os.path.join(os.path.dirname(__file__), "races.pkl")
c = psycopg2.connect(os.environ["KEIRIN_DB_URL"])

races = pd.read_sql("""
select r.race_key, r.venue_id as venue, r.race_date as date, r.cup_id, r.day_index, r.race_no,
       r.n_entries, r.start_at, r.race_type, r.grade, r.cup_grade
from keirin.wt_races r
where r.race_date >= '2023-01-01' and coalesce(r.cancel,0)=0
""", c)
ent = pd.read_sql("""
select e.race_key, e.frame_no, e.finish_order, e.factor, e.line_group, e.prediction_mark, e.pred_top3_pct
from keirin.wt_entries e join keirin.wt_races r using(race_key)
where r.race_date >= '2023-01-01' and coalesce(r.cancel,0)=0
""", c)
print("races", len(races), "entries", len(ent), file=sys.stderr)

ent = ent[ent.finish_order.notna()]
ent["finish_order"] = ent.finish_order.astype(int)
g = ent[ent.finish_order.between(1, 3)].sort_values(["race_key", "finish_order"])
top = g.groupby("race_key").agg(fr=("frame_no", list), lg=("line_group", list), f=("factor", list))
top = top[top.fr.map(len) == 3]
rec = pd.DataFrame({
    "fr1": top.fr.map(lambda v: v[0]), "fr2": top.fr.map(lambda v: v[1]), "fr3": top.fr.map(lambda v: v[2]),
    "f1": top.f.map(lambda v: v[0]),
    "same12": top.lg.map(lambda v: (v[0] is not None) and v[0] == v[1] and v[0] not in (0,)),
}, index=top.index)
# ◎ / 指数1位 が 3着内か
m1 = ent[ent.prediction_mark == 1].groupby("race_key").finish_order.min()
rec["mark1_in3"] = (m1 <= 3).reindex(rec.index)
i1 = (ent[ent.pred_top3_pct.notna()].sort_values(["race_key", "pred_top3_pct"], ascending=[True, False])
      .groupby("race_key").head(1).set_index("race_key").finish_order)
rec["idx1_in3"] = (i1 <= 3).reindex(rec.index)
rec = rec.reset_index()
df = races.merge(rec, on="race_key", how="inner")
df["combo"] = df.fr1.astype(str) + "-" + df.fr2.astype(str) + "-" + df.fr3.astype(str)
print("with top3", len(df), file=sys.stderr)

# 的中三連単の確定オッズ（wt_odds 最終値）。レースごとに1行なので結合で引く
keys = df[["race_key", "combo"]]
keys.to_sql  # noqa
cur = c.cursor()
cur.execute("create temp table k(race_key varchar, combo varchar)")
from psycopg2.extras import execute_values
execute_values(cur, "insert into k values %s", list(keys.itertuples(index=False, name=None)), page_size=5000)
odds = pd.read_sql("""
select o.race_key, max(o.odds_value) as tf_odds
from keirin.wt_odds o join k on k.race_key=o.race_key and k.combo=o.combination
where o.bet_type='trifecta' group by 1""", c)
df = df.merge(odds, on="race_key", how="left")
df["start_ts"] = pd.to_datetime(pd.to_numeric(df.start_at, errors="coerce"), unit="s", utc=True).dt.tz_convert("Asia/Tokyo").dt.tz_localize(None)
df = df.drop(columns=["start_at"])
df.to_pickle(OUT)
print(df.describe(include="all").T.head(30), file=sys.stderr)
print("saved", OUT, len(df), file=sys.stderr)
