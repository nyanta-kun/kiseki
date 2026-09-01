"""レース内 win_probability 合計は「ノイズ」か「レース難度の情報」かを判定する。

合計が 1.0 から外れるのを単なる較正ずれとみなして正規化すると、
「この面子には勝てそうな馬がいない」という情報を捨てることになる。
合計が指数の当たりやすさを予測するなら、正規化ではなく信頼度の材料にすべき。
"""
from __future__ import annotations
import os, sys
from pathlib import Path
_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_root))
from dotenv import load_dotenv
load_dotenv(_root.parent / ".env")
import numpy as np, pandas as pd, psycopg2  # noqa: E402

conn = psycopg2.connect(host=os.environ['DB_HOST'], port=os.environ['DB_PORT'],
                        dbname=os.environ['DB_NAME'], user=os.environ['DB_USER'],
                        password=os.environ['DB_PASSWORD'])
q = """
select ci.race_id, r.course_name, r.head_count, ci.win_probability wp,
       ci.composite_index comp, (rr.finish_position=1)::int won,
       (rr.finish_position<=3)::int top3
from chihou.calculated_indices ci
join chihou.races r on r.id=ci.race_id
join chihou.race_results rr on rr.race_id=ci.race_id and rr.horse_id=ci.horse_id
where ci.version=14 and r.course!='83' and r.date between '20260101' and '20260830'
  and ci.win_probability is not null and rr.finish_position is not null
"""
df = pd.read_sql(q, conn); conn.close()
df["wp"] = df["wp"].astype(float)

g = df.groupby("race_id")
race = pd.DataFrame({
    "wp_sum": g["wp"].sum(), "hc": g["wp"].size(), "course": g["course_name"].first(),
})
# 指数1位馬の結果
top = df.loc[g["comp"].idxmax()].set_index("race_id")
race["idx1_won"] = top["won"]; race["idx1_top3"] = top["top3"]
race["q"] = pd.qcut(race["wp_sum"], 5, labels=["1:最小","2","3","4","5:最大"])

print("=== 全場 2026年（合計の5分位ごと）===")
print(f"{'分位':>7s} {'R数':>6s} {'合計中央':>8s} {'頭数':>5s} {'指数1位勝率':>10s} {'指数1位複勝':>10s}")
for k, gg in race.groupby("q", observed=True):
    print(f"{str(k):>7s} {len(gg):>6,} {gg.wp_sum.median():>8.3f} {gg.hc.mean():>5.1f} "
          f"{100*gg.idx1_won.mean():>9.1f}% {100*gg.idx1_top3.mean():>9.1f}%")

o = race[race.course == "大井"].copy()
o["q"] = pd.qcut(o["wp_sum"], 5, labels=["1:最小","2","3","4","5:最大"])
print("\n=== 大井のみ ===")
print(f"{'分位':>7s} {'R数':>6s} {'合計中央':>8s} {'頭数':>5s} {'指数1位勝率':>10s} {'指数1位複勝':>10s}")
for k, gg in o.groupby("q", observed=True):
    print(f"{str(k):>7s} {len(gg):>6,} {gg.wp_sum.median():>8.3f} {gg.hc.mean():>5.1f} "
          f"{100*gg.idx1_won.mean():>9.1f}% {100*gg.idx1_top3.mean():>9.1f}%")

r = np.corrcoef(race.wp_sum, race.idx1_won)[0,1]
print(f"\n合計 vs 指数1位的中 の相関: {r:+.4f}  (頭数との相関 {np.corrcoef(race.wp_sum, race.hc)[0,1]:+.4f})")
