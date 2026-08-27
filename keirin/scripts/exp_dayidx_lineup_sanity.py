#!/usr/bin/env python3
"""開催日目と「並びの妥当性」（2026-08-27・ユーザー指示）。

レースの型の続き。①軸の堅さ ②相手の開き ③ライン構成 ④ラインの維持 に続いて
  ⑤ 開催日目（`wt_races.day_index`）
  ⑥ 並び予想そのものが妥当か
を測る。⑥は結果を使わず**朝に引ける内部整合性**で定義する:
  a) 指数1位のラインの**先頭の脚質**（逃/両/**追**）… 追が先頭は 16.8% あり構造的に不自然
  b) 先頭がライン内で**自力(ex_thrust_pct)最上位**か
  c) **先頭と番手の競走得点が逆転**していないか（番手のほうが強い）
  d) ライン内が**同県**でまとまっているか

⚠️ `pred_top3_pct` は backfill。構造の記述であって商品の採否は測っていない。
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

FROM, TO = "2025-01-01", "2026-08-26"


def load():
    con = psycopg2.connect(os.environ["KEIRIN_DB_URL"])
    cur = con.cursor()
    cur.execute("""
        SELECT e.race_key, e.frame_no, e.pred_top3_pct, e.finish_order, e.style,
               e.line_group, e.line_pos, e.factor, e.race_point, e.prefecture,
               e.ex_thrust_pct, e.ex_left_behind_pct, r.day_index, r.race_type
        FROM keirin.wt_entries e JOIN keirin.wt_races r USING (race_key)
        WHERE r.n_entries = 7 AND r.race_date BETWEEN %s AND %s""", (FROM, TO))
    races = defaultdict(dict)
    meta = {}
    for (rk, fn, p3, fo, st, lg, lp, fac, rp, pref, thr, beh, di, rt) in cur.fetchall():
        if p3 is None:
            continue
        races[rk][int(fn)] = dict(p3=float(p3) / 100.0, fo=int(fo) if fo else 0,
                                  style=st or "", lg=lg, lp=lp, factor=fac or "",
                                  rp=float(rp or 0), pref=pref or "",
                                  thr=float(thr or 0), beh=float(beh or 0))
        meta[rk] = dict(day=int(di or 0), rtype=rt or "")
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
    con.close()
    print(f"7車 {len(keys):,}R  {FROM}〜{TO}")
    return [(k, races[k], odds.get(k, {}), meta[k]) for k in keys]


def feat(cars, od, m):
    p3 = {f: v["p3"] for f, v in cars.items()}
    top3 = {f for f, v in cars.items() if 1 <= v["fo"] <= 3}
    order = sorted(p3, key=lambda f: -p3[f])
    g = cars[order[0]]["lg"]
    mem = [] if g in (None, "", "0") else [f for f in cars if cars[f]["lg"] == g]
    lead = next((f for f in mem if str(cars[f]["lp"]) == "1"), None)
    second = next((f for f in mem if str(cars[f]["lp"]) == "2"), None)
    win = [f for f, v in cars.items() if v["fo"] == 1]
    d = dict(day=m["day"],
             axis_sum=p3[order[0]] + p3[order[1]],
             both=set(order[:2]) <= top3,
             odds=od.get(frozenset(top3)),
             size=len(mem) if mem else 1,
             held=(lead is not None and second is not None and {lead, second} <= top3),
             has_pair=(lead is not None and second is not None),
             factor=cars[win[0]]["factor"] if win else "")
    others = order[2:]
    d["gap"] = (sum(p3[c] for c in others[:2]) / 2) - (sum(p3[c] for c in others[2:]) / 3)
    if lead is not None:
        d["lead_style"] = cars[lead]["style"]
        d["lead_is_top_thrust"] = (cars[lead]["thrust"] if False else
                                   cars[lead]["thr"]) >= max(cars[f]["thr"] for f in mem)
        d["pref_same"] = len({cars[f]["pref"] for f in mem}) == 1
    else:
        d["lead_style"] = None; d["lead_is_top_thrust"] = None; d["pref_same"] = None
    d["rp_inverted"] = (second is not None and lead is not None
                        and cars[second]["rp"] > cars[lead]["rp"])
    return d


def show(rows, keyf, title, order=None):
    g = defaultdict(list)
    for r in rows:
        k = keyf(r)
        if k is not None:
            g[k].append(r)
    print(f"\n[{title}]")
    print("  値              n      ライン維持  二軸そろい  オッズ中央  逃/捲/差")
    for k in (order or sorted(g, key=str)):
        sub = g.get(k)
        if not sub or len(sub) < 200:
            continue
        hp = [r for r in sub if r["has_pair"]]
        os_ = sorted(r["odds"] for r in sub if r["odds"])
        fc = Counter(r["factor"] for r in sub)
        n = sum(fc[x] for x in ("逃", "捲", "差")) or 1
        print(f"  {str(k):14s} {len(sub):6,}   "
              f"{(sum(1 for r in hp if r['held'])/len(hp)*100) if hp else 0:8.1f}%"
              f"  {sum(1 for r in sub if r['both'])/len(sub)*100:8.2f}%"
              f"  {median(os_) if os_ else 0:8.1f}倍  "
              f"{fc['逃']/n*100:.0f}/{fc['捲']/n*100:.0f}/{fc['差']/n*100:.0f}")


def main() -> None:
    rows = [feat(c, o, m) for _, c, o, m in load()]
    show(rows, lambda r: r["day"], "⑤ 開催日目", order=[1, 2, 3])
    show(rows, lambda r: r["lead_style"], "⑥a 指数1位のラインの先頭の脚質", order=["逃", "両", "追"])
    show(rows, lambda r: "先頭が自力最上位" if r["lead_is_top_thrust"] else "先頭より自力が上の車がいる",
         "⑥b 先頭がライン内で自力最上位か")
    show(rows, lambda r: "得点が逆転" if r["rp_inverted"] else "順当",
         "⑥c 先頭と番手の競走得点")
    show(rows, lambda r: "同県でまとまる" if r["pref_same"] else "混成",
         "⑥d ライン内の県")

    # 2×2 の中で開催日目が足すか
    gaps = sorted(r["gap"] for r in rows)
    gmid = gaps[len(gaps) // 2]
    print("\n[⑤が①②の上に足すか]")
    print("  軸    相手    日目   n      二軸そろい  オッズ中央")
    for aname, af in (("堅い", lambda r: r["axis_sum"] >= RANK_7C_P3_SUM_MIN),
                      ("混戦", lambda r: r["axis_sum"] < RANK_7C_P3_SUM_MIN)):
        for gname, gf in (("開く", lambda r: r["gap"] >= gmid),
                          ("狭い", lambda r: r["gap"] < gmid)):
            for d in (1, 2, 3):
                sub = [r for r in rows if af(r) and gf(r) and r["day"] == d]
                if len(sub) < 200:
                    continue
                os_ = sorted(r["odds"] for r in sub if r["odds"])
                print(f"  {aname:4s}  {gname:4s}  {d}日目 {len(sub):6,}   "
                      f"{sum(1 for r in sub if r['both'])/len(sub)*100:8.2f}%"
                      f"  {median(os_) if os_ else 0:8.1f}倍")


if __name__ == "__main__":
    main()
