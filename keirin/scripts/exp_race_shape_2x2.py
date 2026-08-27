#!/usr/bin/env python3
"""レースの型を2軸で切る — 「軸が堅いか」×「相手（3着目）まで堅いか」（2026-08-27）。

ユーザーの予想手順:
  ① 予想するレースがどんなメンバ構成か（混戦か、人気選手が強く硬めか）
  ② その上で相手含めて堅いか

現行実装で①に当たるもの:
  `axis_sum` = モデル3着内率の上位2車の合計。**7C と 7M1 はこの1.44で隙間なく分かれる**
    （`RANK_7C_P3_SUM_MIN = 1.44` / `RANK_7M1_P3_SUM_MAX` は同じ定数を共有）
  `RANK_7S_AXIS_SUM_MAX = 1.40` / `rank_7s_field_entropy` <= 1.8329 / WT印との重なり
②に当たるレース単位の判定は **存在しない**（相手側は車ごとの絶対値
  `RANK_7C_LEG_P3_MIN=0.15` / `RANK_7C_ANA_CUT_P3_MIN=0.40` と 7M1 の位置規則だけ）。

ここでは②を「全体3・4番手の平均 − 全体5〜7番手の平均（3着内率）」＝**相手の開き**で
定義し、①×②の2×2で決着の質を出す。
"""
from __future__ import annotations

import os
import re
import sys
from collections import defaultdict
from pathlib import Path
from statistics import median

import psycopg2

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from src.strategy_wt import RANK_7C_P3_SUM_MIN  # noqa: E402

FROM, TO = "2025-01-01", "2026-08-26"


def load():
    con = psycopg2.connect(os.environ["KEIRIN_DB_URL"])
    cur = con.cursor()
    cur.execute("""
        SELECT e.race_key, e.frame_no, e.pred_top3_pct, e.finish_order
        FROM keirin.wt_entries e JOIN keirin.wt_races r USING (race_key)
        WHERE r.n_entries = 7 AND r.race_date BETWEEN %s AND %s""", (FROM, TO))
    races = defaultdict(dict)
    for rk, fn, p3, fo in cur.fetchall():
        if p3 is None:
            continue
        races[rk][int(fn)] = (float(p3) / 100.0, int(fo) if fo else 0)
    keys = [k for k, v in races.items() if len(v) == 7
            and len({f for f, x in v.items() if 1 <= x[1] <= 3}) == 3]
    print(f"7車 {len(keys):,}R  {FROM}〜{TO}")
    odds = defaultdict(dict)
    for i in range(0, len(keys), 2000):
        cur.execute("""SELECT race_key, combination, odds_value FROM keirin.wt_odds
                       WHERE bet_type='trio' AND race_key = ANY(%s)""", (keys[i:i + 2000],))
        for rk, comb, od in cur.fetchall():
            try:
                v = float(od)
            except (TypeError, ValueError):
                continue
            if 0 < v < 9999:
                odds[rk][frozenset(int(x) for x in re.split(r"[-=→]", str(comb)))] = v
    con.close()
    return [(k, races[k], odds.get(k, {})) for k in keys]


def main() -> None:
    rows = load()
    out = []
    for rk, cars, od in rows:
        p3 = {f: v[0] for f, v in cars.items()}
        top3 = {f for f, v in cars.items() if 1 <= v[1] <= 3}
        order = sorted(p3, key=lambda f: -p3[f])
        axis_sum = p3[order[0]] + p3[order[1]]
        others = order[2:]                       # 全体3〜7番手
        gap = (sum(p3[c] for c in others[:2]) / 2) - (sum(p3[c] for c in others[2:]) / 3)
        third = (top3 - {order[0], order[1]})
        o = od.get(frozenset(top3))
        out.append(dict(axis_sum=axis_sum, gap=gap, both=set(order[:2]) <= top3,
                        third_top=bool(third and third <= set(others[:2])),
                        odds=o))
    gaps = sorted(r["gap"] for r in out)
    gmid = gaps[len(gaps) // 2]
    print(f"\n軸の堅さ = 3着内率 上位2車の合計（境界 {RANK_7C_P3_SUM_MIN}／7C と 7M1 の分かれ目）")
    print(f"相手の開き = 全体3,4番手の平均 − 全体5〜7番手の平均（中央 {gmid:.3f} で二分）\n")
    print("  軸        相手      n      二軸そろい  3着が全体3,4から  的中三連複オッズ中央")
    for aname, af in (("堅い(>=1.44)", lambda r: r["axis_sum"] >= RANK_7C_P3_SUM_MIN),
                      ("混戦(<1.44) ", lambda r: r["axis_sum"] < RANK_7C_P3_SUM_MIN)):
        for gname, gf in (("開く(堅い)", lambda r: r["gap"] >= gmid),
                          ("狭い(荒れ)", lambda r: r["gap"] < gmid)):
            sub = [r for r in out if af(r) and gf(r)]
            if not sub:
                continue
            al = [r for r in sub if r["both"]]
            t = sum(1 for r in al if r["third_top"]) / len(al) * 100 if al else 0
            os_ = sorted(r["odds"] for r in sub if r["odds"])
            print(f"  {aname}  {gname}  {len(sub):6,}   {len(al)/len(sub)*100:8.2f}%"
                  f"      {t:8.1f}%        {median(os_) if os_ else 0:8.1f}倍")


if __name__ == "__main__":
    main()
