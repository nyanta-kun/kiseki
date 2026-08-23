#!/usr/bin/env python3
"""三連複: 軸2車（モデルp3上位2）に対する**相手の順位別**の実現ROI。

🔴 既存ゲート（`RANK_7C_P3_SUM_MIN` / `RANK_7C_LEG_P3_MIN` / 印との一致）は
   **一切かけない**。先に全数で在り処を探す（ユーザー方針 2026-08-22）。
⚠️ `exp_trio_unconstrained_scan.py` の構造走査は 3〜80倍 に限っており、
   **妙味が見つかった 1〜3倍帯の中を見ていなかった**。その穴を埋める。

判定は事前登録: 日ブロック bootstrap の CI 下限 > 払戻率 74.85%。
探索窓（〜2026-04）/ 確認窓（2026-05〜）。
"""
import sys, numpy as np
sys.path.insert(0,'.')
from scripts.exp_trio_unconstrained_scan import build, roi_ci, PAYOUT_RATE
np.random.seed(101)
d = build("data/exp/tf_shape_cache.jsonl")
names=d["day_names"]; split=sum(1 for n in names if n<"2026-05-01"); sel=d["day"]<split
# 軸2車＝順位1と2 を含む目 ⇔ rt==1 かつ rm==2。そのとき rl が相手の順位
axis2 = (d["rt"]==1)&(d["rm"]==2)
print(f"軸2車(順位1-2)を含む目 {axis2.sum():,}（全 {len(d['rt']):,}）\n")
print("===== A. 相手の順位別（帯を問わない）=====")
print(f"{'相手':>8}{'探索:目数':>10}{'ROI':>8}{'CI下限':>8}{'確認:目数':>10}{'ROI':>8}{'CI下限':>8}"
      f"{'的中率':>8}{'予測ｵｯｽﾞ中央':>12}")
for r in range(3,8):
    m=axis2&(d["rl"]==r); A,B=m&sel,m&~sel
    if A.sum()<300: continue
    ra,la=roi_ci(d["pay"][A],d["day"][A],d["n_days"]); rb,lb=roi_ci(d["pay"][B],d["day"][B],d["n_days"])
    mk=" 🟢両窓で壁超" if la>PAYOUT_RATE and lb>PAYOUT_RATE else (" ⚠️探索のみ" if la>PAYOUT_RATE else "")
    print(f"{f'順位{r}':>8}{A.sum():>10,}{ra:>8.1%}{la:>8.1%}{B.sum():>10,}{rb:>8.1%}{lb:>8.1%}"
          f"{d['hit'][m].mean():>8.2%}{np.median(d['po'][m]):>12.1f}{mk}")

print("\n===== B. 帯 × 相手の順位（1〜3倍帯を含む）=====")
for lo,hi in ((1,3),(3,5),(5,10),(10,20),(20,50)):
    band=(d["po"]>=lo)&(d["po"]<hi)
    print(f"--- 予測オッズ {lo}〜{hi}倍 ---")
    for r in range(3,8):
        m=axis2&band&(d["rl"]==r); A,B=m&sel,m&~sel
        if A.sum()<200 or B.sum()<200: continue
        ra,la=roi_ci(d["pay"][A],d["day"][A],d["n_days"]); rb,lb=roi_ci(d["pay"][B],d["day"][B],d["n_days"])
        mk=" 🟢両窓" if la>PAYOUT_RATE and lb>PAYOUT_RATE else (" ⚠️探索のみ" if la>PAYOUT_RATE else "")
        print(f"  {f'相手順位{r}':>10}{A.sum():>9,}{ra:>8.1%}{la:>8.1%}"
              f"{B.sum():>9,}{rb:>8.1%}{lb:>8.1%}{d['hit'][m].mean():>8.2%}{mk}")
