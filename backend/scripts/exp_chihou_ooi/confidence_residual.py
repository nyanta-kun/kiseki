"""現行の信頼度スコアに対して、追加候補2つが残差予測力を持つかを検定する。

候補1: レース内 win_probability 合計（レース難度）
候補2: 有効特徴カバレッジ（speed/last3f が中立50のままの馬の割合）

現行スコアの層内でこれらがまだ効くなら、独立した情報＝加える価値がある。
"""
from __future__ import annotations
import os, sys
from pathlib import Path
_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_root))
from dotenv import load_dotenv
load_dotenv(_root.parent / ".env")
import numpy as np, pandas as pd, psycopg2  # noqa: E402
from src.indices.confidence import (  # noqa: E402
    calculate_race_confidence, CHIHOU_GAP_FULL_SCORE, CHIHOU_DISPERSION_FULL_SCORE)

conn = psycopg2.connect(host=os.environ['DB_HOST'], port=os.environ['DB_PORT'],
                        dbname=os.environ['DB_NAME'], user=os.environ['DB_USER'],
                        password=os.environ['DB_PASSWORD'])
q = """
select ci.race_id, r.course_name, r.head_count, ci.composite_index comp,
       ci.win_probability wp, ci.speed_index sp, ci.last3f_index l3,
       (rr.finish_position=1)::int won
from chihou.calculated_indices ci
join chihou.races r on r.id=ci.race_id
join chihou.race_results rr on rr.race_id=ci.race_id and rr.horse_id=ci.horse_id
where ci.version=14 and r.course!='83' and r.date between '20260101' and '20260830'
  and ci.win_probability is not null and rr.finish_position is not null
"""
df = pd.read_sql(q, conn); conn.close()
for c in ("comp", "wp", "sp", "l3"):
    df[c] = df[c].astype(float)
df["dead"] = ((df.sp == 50.0) & (df.l3 == 50.0)).astype(int)

rows = []
for rid, g in df.groupby("race_id"):
    conf = calculate_race_confidence(
        list(g["comp"]), int(g["head_count"].iloc[0] or len(g)), list(g["wp"]),
        gap_full_score=CHIHOU_GAP_FULL_SCORE,
        dispersion_full_score=CHIHOU_DISPERSION_FULL_SCORE)
    top = g.loc[g["comp"].idxmax()]
    rows.append({"race_id": rid, "course": g["course_name"].iloc[0],
                 "score": conf["score"], "rank": conf["rank"],
                 "wp_sum": g["wp"].sum(), "coverage": 1.0 - g["dead"].mean(),
                 "idx1_won": int(top["won"])})
R = pd.DataFrame(rows)
print(f"母集団 {len(R):,}R\n")

print("=== 現行 tier の分離（参考）===")
for k, g in R.groupby("rank"):
    print(f"  {k}: {len(g):>5,}R  指数1位勝率 {100*g.idx1_won.mean():5.1f}%")

print("\n=== 現行スコア層の中で wp_sum がまだ効くか（残差予測力）===")
R["sband"] = pd.cut(R.score, [-1, 35, 50, 65, 101], labels=["~35", "36-50", "51-65", "66+"])
print(f"{'スコア帯':>8s} {'R数':>6s} {'wp_sum下位1/3':>13s} {'中1/3':>9s} {'上位1/3':>9s} {'差':>7s}")
for k, g in R.groupby("sband", observed=True):
    if len(g) < 100: continue
    t = pd.qcut(g.wp_sum, 3, labels=[0, 1, 2])
    v = [100*g[t == i].idx1_won.mean() for i in range(3)]
    print(f"{str(k):>8s} {len(g):>6,} {v[0]:>12.1f}% {v[1]:>8.1f}% {v[2]:>8.1f}% {v[2]-v[0]:>+6.1f}pt")

print("\n=== 特徴カバレッジの効果（全馬に指数あり=1.0）===")
R["cband"] = pd.cut(R["coverage"], [-0.01, 0.001, 0.5, 0.999, 1.001],
                    labels=["0%(全馬なし)", "1-50%", "51-99%", "100%"])
print(f"{'カバレッジ':>14s} {'R数':>6s} {'指数1位勝率':>10s} {'平均スコア':>10s}")
for k, g in R.groupby("cband", observed=True):
    print(f"{str(k):>14s} {len(g):>6,} {100*g.idx1_won.mean():>9.1f}% {g.score.mean():>10.1f}")

o = R[R.course == "大井"]
print(f"\n=== 大井のみ（{len(o):,}R）===")
for k, g in o.groupby("cband", observed=True):
    if len(g): print(f"{str(k):>14s} {len(g):>6,} {100*g.idx1_won.mean():>9.1f}% {g.score.mean():>10.1f}")
