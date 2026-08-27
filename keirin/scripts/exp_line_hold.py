#!/usr/bin/env python3
"""ラインが維持されるか（分断・遅れ）を測る — レースの型の4段目（2026-08-27）。

列の意味（実測で確認済み・2025-01以降 321,910行）:
  `ex_spurt_pct`       先行率            逃 66.8 / 両 28.9 / 追 3.6
  `ex_thrust_pct`      自力（主導権）率   逃 83.9 / 両 39.0 / 追 2.7
  `ex_snatch_pct`      捲り率            逃 16.8 / 両 19.0 / 追 9.0
  `ex_left_behind_pct` **遅れ率**        line_pos1 が最大 8.5（先行して垂れる）
  `ex_split_line_pct`  **分断率**        line_pos が後ろほど高い 4.5→12.8→14.9

⚠️ どれも直近レースからの率で 0/100 に張り付く行がある（100% が 1,465行）。
   レース単位へ集約してから使う。
⚠️ `pred_top3_pct` は backfill。ここは構造の記述であって商品の採否は測っていない。
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
               e.line_group, e.line_pos, e.n_lines, e.factor,
               e.ex_split_line_pct, e.ex_left_behind_pct, e.ex_spurt_pct
        FROM keirin.wt_entries e JOIN keirin.wt_races r USING (race_key)
        WHERE r.n_entries = 7 AND r.race_date BETWEEN %s AND %s""", (FROM, TO))
    races = defaultdict(dict)
    for (rk, fn, p3, fo, st, lg, lp, nl, fac, split, behind, spurt) in cur.fetchall():
        if p3 is None:
            continue
        races[rk][int(fn)] = dict(p3=float(p3) / 100.0, fo=int(fo) if fo else 0,
                                  style=st or "", lg=lg, lp=lp, nl=nl, factor=fac or "",
                                  split=float(split or 0), behind=float(behind or 0),
                                  spurt=float(spurt or 0))
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
    return [(k, races[k], odds.get(k, {})) for k in keys]


def feat(cars, od):
    p3 = {f: v["p3"] for f, v in cars.items()}
    top3 = {f for f, v in cars.items() if 1 <= v["fo"] <= 3}
    order = sorted(p3, key=lambda f: -p3[f])
    others = order[2:]
    g = cars[order[0]]["lg"]
    solo = g in (None, "", "0")
    mem = [] if solo else [f for f in cars if cars[f]["lg"] == g]
    lead = next((f for f in mem if str(cars[f]["lp"]) == "1"), None)
    second = next((f for f in mem if str(cars[f]["lp"]) == "2"), None)
    win = [f for f, v in cars.items() if v["fo"] == 1]
    return dict(
        axis_sum=p3[order[0]] + p3[order[1]],
        gap=(sum(p3[c] for c in others[:2]) / 2) - (sum(p3[c] for c in others[2:]) / 3),
        both=set(order[:2]) <= top3,
        odds=od.get(frozenset(top3)),
        size=len(mem) if mem else 1,
        # 指数1位のラインが維持されたか＝先頭と番手がともに3着以内
        held=(lead is not None and second is not None
              and {lead, second} <= top3),
        has_pair=(lead is not None and second is not None),
        # 朝に引ける「維持されにくさ」
        split2=cars[second]["split"] if second else None,     # 番手の分断率
        behind1=cars[lead]["behind"] if lead else None,       # 先頭の遅れ率
        spurt1=cars[lead]["spurt"] if lead else None,         # 先頭の先行率
        split_field=sum(v["split"] for v in cars.values()) / 7,
        behind_field=sum(v["behind"] for v in cars.values()) / 7,
        factor=cars[win[0]]["factor"] if win else "",
        same3=(len({cars[f]["lg"] for f in top3}) == 1
               and cars[list(top3)[0]]["lg"] not in (None, "", "0")),
    )


def bucket(rows, key, edges, label):
    print(f"\n[{label}]")
    print("  帯            n      ライン維持  同ライン3車  二軸そろい  オッズ中央  逃/捲/差")
    for i, (lo, hi) in enumerate(zip([-1e9] + edges, edges + [1e9])):
        sub = [r for r in rows if r[key] is not None and lo <= r[key] < hi]
        if len(sub) < 200:
            continue
        hp = [r for r in sub if r["has_pair"]]
        os_ = sorted(r["odds"] for r in sub if r["odds"])
        fc = Counter(r["factor"] for r in sub)
        n = sum(fc[x] for x in ("逃", "捲", "差")) or 1
        rng = f"{lo:.0f}〜{hi:.0f}" if lo > -1e8 else f"〜{hi:.0f}"
        if hi > 1e8:
            rng = f"{lo:.0f}〜"
        print(f"  {rng:12s} {len(sub):6,}   "
              f"{(sum(1 for r in hp if r['held'])/len(hp)*100) if hp else 0:8.1f}%"
              f"   {sum(1 for r in sub if r['same3'])/len(sub)*100:8.1f}%"
              f"  {sum(1 for r in sub if r['both'])/len(sub)*100:8.2f}%"
              f"  {median(os_) if os_ else 0:8.1f}倍  "
              f"{fc['逃']/n*100:.0f}/{fc['捲']/n*100:.0f}/{fc['差']/n*100:.0f}")


def main() -> None:
    rows = [feat(c, o) for _, c, o in load()]
    paired = [r for r in rows if r["has_pair"]]
    print(f"指数1位に番手が居るレース {len(paired):,} / {len(rows):,}"
          f"（ライン維持率 {sum(1 for r in paired if r['held'])/len(paired)*100:.2f}%）")
    bucket(paired, "split2", [3, 8, 15, 30], "番手の分断率（ex_split_line_pct）")
    bucket(paired, "behind1", [3, 8, 15, 30], "先頭の遅れ率（ex_left_behind_pct）")
    bucket(rows, "split_field", [4, 6, 8, 12], "レース全体の平均分断率")
    bucket(rows, "behind_field", [3, 5, 8, 12], "レース全体の平均遅れ率")




def main2() -> None:
    """交絡の切り分け。
    ① `ex_left_behind_pct`（遅れ率）は 逃12.0/両6.0/追2.5 ＝ **先行型の代理**の疑い
       → `ex_spurt_pct`（先行率）と直接比べる。
    ② レース全体の平均分断率は「ラインがそもそも在るか」の代理の疑い
       → 全単騎（ガールズ等）を除いて測り直す。
    """
    rows = [feat(c, o) for _, c, o in load()]
    paired = [r for r in rows if r["has_pair"]]
    # 先頭の先行率を足す
    print("\n[指数1位のラインの先頭が『先行型』か — 遅れ率 vs 先行率]")
    for key, edges, label in (("behind1", [3, 8, 15, 30], "先頭の遅れ率"),
                              ("spurt1", [10, 35, 60, 80], "先頭の先行率(ex_spurt_pct)")):
        bucket(paired, key, edges, label)

    lines = [r for r in rows if r["size"] >= 2]
    solo = [r for r in rows if r["size"] == 1]
    print(f"\n[全単騎の切り分け] ライン有り {len(lines):,}R / 指数1位が単騎 {len(solo):,}R")
    for nm, sub in (("ライン有りのみ", lines),):
        print(f"  ({nm})")
        bucket(sub, "split_field", [4, 6, 8, 12], "レース全体の平均分断率（ライン有りのみ）")




def main3() -> None:
    """①軸の堅さ × ③指数1位のライン人数 の中で、④先頭の遅れ率がまだ効くか。"""
    rows = [r for r in [feat(c, o) for _, c, o in load()] if r["has_pair"]]
    bs = sorted(r["behind1"] for r in rows if r["behind1"] is not None)
    bmid = bs[len(bs) // 2]
    print(f"\n[④が①③の上に足すか]  遅れ率の中央 {bmid:.1f}% で二分")
    print("  軸    1位のライン  遅れ率   n      ライン維持  二軸そろい  オッズ中央  逃/捲/差")
    for aname, af in (("堅い", lambda r: r["axis_sum"] >= RANK_7C_P3_SUM_MIN),
                      ("混戦", lambda r: r["axis_sum"] < RANK_7C_P3_SUM_MIN)):
        for sz in (2, 3, 4):
            for bname, bf in (("高い", lambda r: r["behind1"] >= bmid),
                              ("低い", lambda r: r["behind1"] < bmid)):
                sub = [r for r in rows if af(r) and r["size"] == sz and bf(r)]
                if len(sub) < 200:
                    continue
                os_ = sorted(r["odds"] for r in sub if r["odds"])
                fc = Counter(r["factor"] for r in sub)
                n = sum(fc[x] for x in ("逃", "捲", "差")) or 1
                print(f"  {aname:4s}  {sz}人      {bname:4s}  {len(sub):6,}    "
                      f"{sum(1 for r in sub if r['held'])/len(sub)*100:8.1f}%"
                      f"  {sum(1 for r in sub if r['both'])/len(sub)*100:8.2f}%"
                      f"  {median(os_) if os_ else 0:8.1f}倍  "
                      f"{fc['逃']/n*100:.0f}/{fc['捲']/n*100:.0f}/{fc['差']/n*100:.0f}")
            print()


if __name__ == "__main__":
    main3()
