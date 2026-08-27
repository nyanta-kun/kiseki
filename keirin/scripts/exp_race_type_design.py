#!/usr/bin/env python3
"""レースの型を定義して商品を割り当てる（2026-08-27・設計）。

6層の測定結果（`docs`/memory `keirin_race_shape_3layer_2026_08_27`）を踏まえ、

  ① 軸の堅さ  `axis_sum`（3着内率 上位2車の合計・境界 1.44）      → **的中率**
  ② 相手の開き 全体3,4番手の平均 − 全体5〜7番手の平均(p3)         → **3着の出どころ＝点数**
  ③〜⑥ ライン構成 / 先頭の自力 / 開催日目 / 並びの妥当性          → **配当（荒れ度）**

③〜⑥ は全部「荒れ」の同じ向きなので、**透明な加算スコア**へまとめる
（LightGBM で作らないのは、この層で学習器が単一変数に負けた前例があるため
 [[keirin_highpay_race_classifier_2026_08_24]]）。

⚠️ `pred_top3_pct` は backfill。型の**記述**であって商品の採否（ROI）は測っていない。
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
BEHIND_MID = 11.0          # 先頭の遅れ率の中央値（実測）


def load():
    con = psycopg2.connect(os.environ["KEIRIN_DB_URL"])
    cur = con.cursor()
    cur.execute("""
        SELECT e.race_key, e.frame_no, e.pred_top3_pct, e.finish_order, e.style,
               e.line_group, e.line_pos, e.n_lines, e.factor, e.race_point,
               e.ex_left_behind_pct, r.day_index, r.race_date::text
        FROM keirin.wt_entries e JOIN keirin.wt_races r USING (race_key)
        WHERE r.n_entries = 7 AND r.race_date BETWEEN %s AND %s""", (FROM, TO))
    races, meta = defaultdict(dict), {}
    for (rk, fn, p3, fo, st, lg, lp, nl, fac, rp, beh, di, rd) in cur.fetchall():
        if p3 is None:
            continue
        races[rk][int(fn)] = dict(p3=float(p3) / 100.0, fo=int(fo) if fo else 0,
                                  style=st or "", lg=lg, lp=lp, nl=nl, factor=fac or "",
                                  rp=float(rp or 0), beh=float(beh or 0))
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
    con.close()
    print(f"7車 {len(keys):,}R  {FROM}〜{TO}")
    return [(k, races[k], odds.get(k, {}), meta[k]) for k in keys]


def feat(cars, od, m):
    p3 = {f: v["p3"] for f, v in cars.items()}
    top3 = {f for f, v in cars.items() if 1 <= v["fo"] <= 3}
    order = sorted(p3, key=lambda f: -p3[f])
    others = order[2:]
    g = cars[order[0]]["lg"]
    mem = [] if g in (None, "", "0") else [f for f in cars if cars[f]["lg"] == g]
    lead = next((f for f in mem if str(cars[f]["lp"]) == "1"), None)
    second = next((f for f in mem if str(cars[f]["lp"]) == "2"), None)
    size = len(mem) if mem else 1
    win = [f for f, v in cars.items() if v["fo"] == 1]

    # ③〜⑥ を加算した荒れ度（大きいほど荒れる）
    s = 0
    s += 1 if size == 2 else (-1 if size >= 4 else 0)                  # ③ 2人ラインが最悪
    if lead is not None:
        s += -1 if cars[lead]["beh"] >= BEHIND_MID else 1              # ④ 先頭の自力
        s += 2 if cars[lead]["style"] == "追" else 0                    # ⑥a 追が先頭
    s += m["day"] - 2                                                   # ⑤ 開催日目
    if lead is not None and second is not None and cars[second]["rp"] > cars[lead]["rp"]:
        s += 1                                                          # ⑥c 得点の逆転
    return dict(
        date=m["date"],
        axis_sum=p3[order[0]] + p3[order[1]],
        gap=(sum(p3[c] for c in others[:2]) / 2) - (sum(p3[c] for c in others[2:]) / 3),
        both=set(order[:2]) <= top3,
        third_top=bool((top3 - set(order[:2])) <= set(others[:2])),
        odds=od.get(frozenset(top3)),
        arare=s,
        factor=cars[win[0]]["factor"] if win else "",
    )


def summarize(sub):
    al = [r for r in sub if r["both"]]
    os_ = sorted(r["odds"] for r in sub if r["odds"])
    fc = Counter(r["factor"] for r in sub)
    n = sum(fc[x] for x in ("逃", "捲", "差")) or 1
    return dict(
        n=len(sub),
        both=len(al) / len(sub) * 100,
        third=sum(1 for r in al if r["third_top"]) / len(al) * 100 if al else 0,
        med=median(os_) if os_ else 0,
        q25=os_[len(os_) // 4] if os_ else 0,
        q75=os_[len(os_) * 3 // 4] if os_ else 0,
        km=f"{fc['逃']/n*100:.0f}/{fc['捲']/n*100:.0f}/{fc['差']/n*100:.0f}")


def main() -> None:
    rows = [feat(c, o, m) for _, c, o, m in load()]
    print("\n[荒れ度スコアの分離力]")
    print("  score     n     二軸そろい  3着が全体3,4  オッズ q25/中央/q75   逃/捲/差")
    for s in sorted({r["arare"] for r in rows}):
        sub = [r for r in rows if r["arare"] == s]
        if len(sub) < 300:
            continue
        d = summarize(sub)
        print(f"  {s:+3d}   {d['n']:6,}    {d['both']:7.2f}%    {d['third']:8.1f}%"
              f"   {d['q25']:5.1f}/{d['med']:5.1f}/{d['q75']:6.1f}倍   {d['km']}")
    print("\n（参考）荒れ度は①②と独立か — ①の中で荒れ度を割る")
    for aname, af in (("堅い", lambda r: r["axis_sum"] >= RANK_7C_P3_SUM_MIN),
                      ("混戦", lambda r: r["axis_sum"] < RANK_7C_P3_SUM_MIN)):
        for lo, hi, lbl in ((-99, -1, "荒れ度 低"), (0, 0, "中"), (1, 99, "高")):
            sub = [r for r in rows if af(r) and lo <= r["arare"] <= hi]
            if len(sub) < 300:
                continue
            d = summarize(sub)
            print(f"  {aname:4s} {lbl:8s} {d['n']:6,}  二軸 {d['both']:6.2f}%  "
                  f"3着上位 {d['third']:5.1f}%  オッズ {d['med']:5.1f}倍  {d['km']}")




TYPES = [("A 鉄板", lambda r: r["axis_sum"] >= RANK_7C_P3_SUM_MIN and r["arare"] <= -1),
         ("B 堅い・中", lambda r: r["axis_sum"] >= RANK_7C_P3_SUM_MIN and r["arare"] == 0),
         ("C 堅いが崩れ筋", lambda r: r["axis_sum"] >= RANK_7C_P3_SUM_MIN and r["arare"] >= 1),
         ("D 混戦・軸あり", lambda r: r["axis_sum"] < RANK_7C_P3_SUM_MIN and r["arare"] <= -1),
         ("E 混戦・中", lambda r: r["axis_sum"] < RANK_7C_P3_SUM_MIN and r["arare"] == 0),
         ("F 大混戦", lambda r: r["axis_sum"] < RANK_7C_P3_SUM_MIN and r["arare"] >= 1)]


def coverage():
    """各型を現行どのランクが取っているか（picks_history の候補ベース）。"""
    con = psycopg2.connect(os.environ["KEIRIN_DB_URL"])
    cur = con.cursor()
    cur.execute("""SELECT split_part(race_key,'#',1), rank FROM keirin.picks_history
                   WHERE race_key >= '2025'""")
    d = defaultdict(set)
    for rk, rank in cur.fetchall():
        d[rk].add(rank)
    con.close()
    return d


ORDER = ["RANK_7H2", "RANK_9H1", "RANK_7T1", "RANK_7T3", "RANK_7S",
         "RANK_9C", "RANK_7B", "RANK_7C", "RANK_7H1", "RANK_7M1"]


def main2() -> None:
    data = load()
    rows = [(k, feat(c, o, m)) for k, c, o, m in data]
    cov = coverage()
    days = len({r["date"] for _, r in rows})
    print(f"\n=== 型ごとの姿（7車 {len(rows):,}R / {days}日 = {len(rows)/days:.1f}R/日）===")
    print("  型             件/日   二軸そろい  3着が全体3,4  オッズ q25/中央/q75    逃/捲/差")
    for nm, f in TYPES:
        sub = [r for _, r in rows if f(r)]
        if not sub:
            continue
        d = summarize(sub)
        print(f"  {nm:14s} {d['n']/days:6.1f}  {d['both']:8.2f}%    {d['third']:8.1f}%"
              f"   {d['q25']:5.1f}/{d['med']:5.1f}/{d['q75']:6.1f}倍   {d['km']}")

    print("\n=== 型ごとに、いまどのランクが取っているか（最優先ランクで代表させる）===")
    print("  型             件/日  " + "  ".join(f"{r.replace('RANK_',''):>5s}" for r in ORDER) + "   無商品")
    for nm, f in TYPES:
        sub = [(k, r) for k, r in rows if f(r)]
        if not sub:
            continue
        c = Counter()
        for k, r in sub:
            got = [x for x in ORDER if x in cov.get(k, ())]
            c[got[0] if got else "none"] += 1
        line = f"  {nm:14s} {len(sub)/days:6.1f}  "
        line += "  ".join(f"{c[r]/len(sub)*100:4.0f}%" for r in ORDER)
        line += f"   {c['none']/len(sub)*100:5.0f}%"
        print(line)




def main3() -> None:
    """無商品（どのランクの候補にもならない）レースは、型の中で質が違うのか。
    同じ型の中で「商品あり」と「無商品」を比べる。差が無ければ**取りこぼし**。"""
    data = load()
    rows = [(k, feat(c, o, m)) for k, c, o, m in data]
    cov = coverage()
    days = len({r["date"] for _, r in rows})
    print("\n=== 同じ型の中で 商品あり / 無商品 を比べる ===")
    print("  型             区分     件/日   二軸そろい  3着が全体3,4  オッズ中央  逃/捲/差")
    for nm, f in TYPES:
        sub = [(k, r) for k, r in rows if f(r)]
        if not sub:
            continue
        for lbl, has in (("商品あり", True), ("無商品", False)):
            g = [r for k, r in sub if bool(cov.get(k)) == has]
            if len(g) < 300:
                continue
            d = summarize(g)
            print(f"  {nm:14s} {lbl:8s} {d['n']/days:6.1f}  {d['both']:8.2f}%"
                  f"    {d['third']:8.1f}%   {d['med']:8.1f}倍   {d['km']}")


if __name__ == "__main__":
    main3()
