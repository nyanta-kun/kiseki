#!/usr/bin/env python3
"""A型（鉄板）× 印一致 の無商品レースに少点数の三連複を置いたら（2026-08-27・設計）。

背景: 7車の 44%（16,989R）にどのランクの候補も無く、その **98.8% は
「モデル上位2車 = 公式印◎○」**＝各ランクの**印不一致ゲート**が落としている。
その中で最も堅い A型（`axis_sum`>=1.44 ∧ 荒れ度<=-1）は二軸そろい **70.4%**・
確定三連複オッズ中央 4.3倍。**1点なら平均払戻が2万円ゲートを通る**はず、という検算。

買い方: 軸 = モデル3着内率の上位2車（＝この母集団では公式◎○と一致）。
相手 = 残り5車を3着内率降順に並べた上位から k 点。1レース10,000円を等分。
⚠️ 払戻は**確定三連複オッズ**。予測オッズが無いので入稿ゲート(2万円)は
   確定値で近似している（本番は予測オッズで判定するのでズレる）。
⚠️ `pred_top3_pct` は backfill。**設計の当たりを付けるための検算**であって
   採否の判断には walk-forward が要る。
"""
from __future__ import annotations

import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median

import psycopg2

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from src.strategy_wt import RANK_7C_P3_SUM_MIN  # noqa: E402

BUDGET, UNIT, MIN_MEAN = 10_000, 100, 20_000
BEHIND_MID = 11.0


def load():
    con = psycopg2.connect(os.environ["KEIRIN_DB_URL"])
    cur = con.cursor()
    cur.execute("""
        SELECT e.race_key, e.frame_no, e.pred_top3_pct, e.finish_order, e.style,
               e.line_group, e.line_pos, e.race_point, e.prediction_mark,
               e.ex_left_behind_pct, r.day_index, r.race_date::text
        FROM keirin.wt_entries e JOIN keirin.wt_races r USING (race_key)
        WHERE r.n_entries = 7 AND r.race_date BETWEEN '2025-01-01' AND '2026-08-26'""")
    races, meta = defaultdict(dict), {}
    for (rk, fn, p3, fo, st, lg, lp, rp, mk, beh, di, rd) in cur.fetchall():
        if p3 is None:
            continue
        races[rk][int(fn)] = dict(p3=float(p3) / 100.0, fo=int(fo) if fo else 0,
                                  style=st or "", lg=lg, lp=lp, rp=float(rp or 0),
                                  mark=int(mk or 0), beh=float(beh or 0))
        meta[rk] = dict(day=int(di or 0), date=rd)
    keys = [k for k, v in races.items() if len(v) == 7
            and len({f for f, x in v.items() if 1 <= x["fo"] <= 3}) == 3]
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
    cur.execute("SELECT DISTINCT split_part(race_key,'#',1) FROM keirin.picks_history "
                "WHERE race_key >= '2025'")
    cov = {r[0] for r in cur.fetchall()}
    con.close()
    return keys, races, odds, meta, cov


def main() -> None:
    keys, races, odds, meta, cov = load()
    rows = []
    for k in keys:
        cars = races[k]
        p3 = {f: v["p3"] for f, v in cars.items()}
        order = sorted(p3, key=lambda f: -p3[f])
        marks = {f for f, v in cars.items() if v["mark"] in (1, 2)}
        g = cars[order[0]]["lg"]
        mem = [] if g in (None, "", "0") else [f for f in cars if cars[f]["lg"] == g]
        lead = next((f for f in mem if str(cars[f]["lp"]) == "1"), None)
        second = next((f for f in mem if str(cars[f]["lp"]) == "2"), None)
        size = len(mem) if mem else 1
        s = 1 if size == 2 else (-1 if size >= 4 else 0)
        if lead is not None:
            s += -1 if cars[lead]["beh"] >= BEHIND_MID else 1
            s += 2 if cars[lead]["style"] == "追" else 0
        s += meta[k]["day"] - 2
        if lead is not None and second is not None and cars[second]["rp"] > cars[lead]["rp"]:
            s += 1
        rows.append(dict(key=k, date=meta[k]["date"], order=order, cars=cars,
                         od=odds.get(k, {}),
                         top3={f for f, v in cars.items() if 1 <= v["fo"] <= 3},
                         axis_sum=p3[order[0]] + p3[order[1]], arare=s,
                         agree=(set(order[:2]) == marks), covered=(k in cov)))

    target = [r for r in rows if r["axis_sum"] >= RANK_7C_P3_SUM_MIN and r["arare"] <= -1
              and r["agree"] and not r["covered"]]
    days = len({r["date"] for r in rows})
    print(f"対象＝A型(堅い×荒れ度<=-1) × 印一致 × 無商品: {len(target):,}R "
          f"= {len(target)/days:.2f}件/日  （全7車 {len(rows):,}R / {days}日）")

    print("\n  点数  ゲート通過  件/日   的中%   ガミ%  表示的中%  払戻中央   平均払戻中央  2倍+/日")
    for k_pts in (1, 2, 3, 4, 5):
        n = hit = gami = 0
        pays, means, ratios = [], [], []
        dset = set()
        for r in target:
            legs = [c for c in r["order"][2:2 + k_pts]]
            if len(legs) < k_pts:
                continue
            combos = [frozenset({r["order"][0], r["order"][1], c}) for c in legs]
            o = [r["od"].get(c) for c in combos]
            if any(x is None or x <= 0 for x in o):
                continue
            stake = BUDGET // k_pts // UNIT * UNIT
            mean = sum(stake * x for x in o) / k_pts
            if mean <= MIN_MEAN:          # 入稿ゲート（確定オッズで近似）
                continue
            n += 1
            dset.add(r["date"])
            inv = stake * k_pts
            win = frozenset(r["top3"])
            pay = stake * r["od"][win] if win in combos and win in r["od"] else 0
            means.append(mean)
            if pay > 0:
                hit += 1
                pays.append(pay)
                ratios.append(pay / inv)
                if pay < inv:
                    gami += 1
        if not n:
            continue
        nd = len(dset)
        print(f"  {k_pts}点  {n/len(target)*100:8.1f}% {n/days:6.2f} {hit/n*100:7.2f}"
              f" {gami/hit*100 if hit else 0:6.2f} {(hit-gami)/n*100:9.2f}"
              f"  {median(pays) if pays else 0:9,.0f}  {median(means):11,.0f}"
              f"  {sum(1 for x in ratios if x >= 2)/nd:7.2f}")


if __name__ == "__main__":
    main()
