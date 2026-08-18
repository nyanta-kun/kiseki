#!/usr/bin/env python3
"""朝／場中の**ワイド**オッズ（＝市場の「2車が3着以内」同時確率）で軸2車を選べるか（2026-08-18）

## なぜワイドなのか

二軸的中 = 「軸2車がともに3着以内」。**ワイド（quinellaPlace）の的中条件と完全に同じ**。
1/オッズ を全ペアで正規化すれば、市場が付けたペアの同時確率がそのまま得られる。
`keirin_layer2_pair_ceiling_2026_08_10` が「層2の天井 +2.62pt」と測ったのは
**最終オッズ**の同時確率だったので、入稿時刻に使える板で再測定する。

## 結果（2026-08-18・不採用）

n=785〜1,031R（モデル上位4車の6ペアが全て板にある7車レース）:

| 板 | 現行(p3上位2) | 市場最有力 | 混合 w=0.2 |
|---|---|---|---|
| morning | 55.39% | −3.06pt | +0.86pt ±0.86 |
| h12 | 54.61% | −1.45pt | +0.10pt ±0.72 |
| h14 | 53.46% | −2.81pt | +0.22pt ±0.80 |
| h18 | 52.61% | −0.13pt | +1.27pt ±0.76 |

🔴 **市場は現行モデルに勝てない**（4つの板すべてで負け）。混合しても差は 1SE 以内。
→ 「層2の天井 +2.62pt」は**現行モデルでは再現しない**。当時の R0（二軸 54.61%）と
   市場（57.23%）の差は最終オッズ由来で、入稿時刻の板にはその情報が無い。

⚠️ 朝の板は**発走の近いレースしか埋まらない**（`keirin_odds_availability_by_posttime_2026_08_07`）。
   6ペア全部そろう7車レースは morning で 816R（同期間の約25%）しかない。
   母集団が「朝一番に発走する開催」へ偏っている点も割り引くこと。

使い方:
    PYTHONPATH=. .venv/bin/python scripts/exp_market_pair_wt.py
"""
from __future__ import annotations

import itertools
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

SINCE, UNTIL = "2026-06-08", "2026-08-18"    # morning スナップショットの開始以降
TOPK = 4          # 候補は「モデル上位4車の6ペア」に絞る（全21ペア要求だと n が 1/4 になる）
SNAPS = ("morning", "h12", "h14", "h18")


def main() -> None:
    eng = create_engine(os.environ["KEIRIN_DB_URL"])

    def q(sql: str) -> pd.DataFrame:
        with eng.connect() as c:
            return pd.read_sql_query(text(sql), c)

    E = q(f"""SELECT e.race_key, e.frame_no, e.pred_top3_pct p3, e.finish_order
              FROM keirin.wt_entries e JOIN keirin.wt_races r USING(race_key)
              WHERE r.cancel=0 AND r.race_date BETWEEN '{SINCE}' AND '{UNTIL}'""")
    O = q("""SELECT race_key, snapshot_type, combination, odds_value
             FROM keirin.wt_odds_snapshot
             WHERE bet_type='quinellaPlace' AND odds_value < 9999""")

    E = E[E["finish_order"] >= 1].copy()
    E["n"] = E.groupby("race_key")["finish_order"].transform("max")
    E = E[E["n"] == 7]
    # 🔴 三連複の表記が 2026-06 途中で `1=2=3` → `1-2-3` に変わっている。必ず両方割る。
    ab = O["combination"].str.split(r"[-=]", regex=True)
    O["a"], O["b"] = ab.str[0].astype(int), ab.str[1].astype(int)
    O["key"] = list(zip(O[["a", "b"]].min(axis=1), O[["a", "b"]].max(axis=1)))
    O = O.sort_values("odds_value").drop_duplicates(
        ["race_key", "snapshot_type", "key"])

    for snap in SNAPS:
        om = {k: dict(zip(g["key"], g["odds_value"]))
              for k, g in O[O["snapshot_type"] == snap].groupby("race_key")}
        H, M, K, B = [], [], [], []
        for rk, g in E.groupby("race_key"):
            ov = om.get(rk)
            if len(g) != 7 or not ov:
                continue
            p3 = dict(zip(g["frame_no"], g["p3"]))
            fin = dict(zip(g["frame_no"], g["finish_order"]))
            srt = sorted(p3, key=lambda f: -p3[f])
            prs = list(itertools.combinations(sorted(srt[:TOPK]), 2))
            if any(pr not in ov for pr in prs):
                continue
            inv = np.array([1 / ov[pr] for pr in prs])
            t3 = {k for k, v in fin.items() if v <= 3}
            H.append([1.0 if (a in t3 and b in t3) else 0.0 for a, b in prs])
            mdl = np.array([p3[a] * p3[b] for a, b in prs])
            M.append(mdl / mdl.sum())
            K.append(inv / inv.sum())
            B.append(prs.index((min(srt[0], srt[1]), max(srt[0], srt[1]))))
        n = len(H)
        if n < 300:
            print(f"\n[{snap}] n={n} 少なすぎ")
            continue
        H, M, K, B = np.array(H), np.array(M), np.array(K), np.array(B)
        idx = np.arange(n)
        base = H[idx, B].mean()
        mk = H[idx, K.argmax(1)].mean()
        print(f"\n[{snap}] n={n:,}R  現行(p3上位2) {base*100:.2f}%  "
              f"市場最有力 {mk*100:.2f}% (差 {(mk-base)*100:+.2f}pt)")
        for w in (0.2, 0.35, 0.5, 0.65, 0.8):
            s = (1 - w) * np.log(M + 1e-9) + w * np.log(K + 1e-9)
            sel = s.argmax(1)
            d = H[idx, sel] - H[idx, B]
            print(f"   混合 w={w:.2f}  {H[idx, sel].mean()*100:.2f}%  "
                  f"差 {d.mean()*100:+.2f}pt ±{100*d.std()/np.sqrt(n):.2f}  "
                  f"(変更 {100*np.mean(sel != B):.1f}%)")


if __name__ == "__main__":
    main()
