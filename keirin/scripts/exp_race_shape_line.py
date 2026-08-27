#!/usr/bin/env python3
"""レースの型の3段目 — 決まり手とライン構成（2026-08-27・ユーザー指示）。

①軸の堅さ（`axis_sum` = 3着内率 上位2車の合計・境界 1.44）
②相手の開き（全体3,4番手の平均 − 全体5〜7番手の平均）
③**ライン構成・決まり手** ← ここを測る

前提: 決まり手そのもの（`wt_entries.factor`）は**結果**なので予想には使えない。
朝に引けるのは `style`(脚質) / `front_runner`(逃) `stalker`(捲) `deep_closer`(差)
`marker`(マーク) の回数 / `n_lines` / `line_size` / `line_pos` / `is_line_leader`。
決まり手は「その構成だと何が起きるか」の**説明**として並べる。

⚠️ `pred_top3_pct` は backfill 値。ここは商品の採否ではなく**構造の記述**なので使う。
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
        SELECT e.race_key, e.frame_no, e.pred_top3_pct, e.finish_order,
               e.style, e.line_group, e.line_size, e.line_pos, e.n_lines,
               e.front_runner, e.stalker, e.deep_closer, e.marker, e.factor
        FROM keirin.wt_entries e JOIN keirin.wt_races r USING (race_key)
        WHERE r.n_entries = 7 AND r.race_date BETWEEN %s AND %s""", (FROM, TO))
    races = defaultdict(dict)
    for (rk, fn, p3, fo, st, lg, lsz, lp, nl, fr, sk, dc, mk, fac) in cur.fetchall():
        if p3 is None:
            continue
        races[rk][int(fn)] = dict(p3=float(p3) / 100.0, fo=int(fo) if fo else 0,
                                  style=st or "", lg=lg, lsz=lsz, lp=lp, nl=nl,
                                  fr=fr or 0, sk=sk or 0, dc=dc or 0, mk=mk or 0,
                                  factor=fac or "")
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


def features(cars: dict, od: dict) -> dict:
    p3 = {f: v["p3"] for f, v in cars.items()}
    top3 = {f for f, v in cars.items() if 1 <= v["fo"] <= 3}
    order = sorted(p3, key=lambda f: -p3[f])
    others = order[2:]
    sizes = {}
    for f, v in cars.items():
        g = v["lg"]
        if g not in (None, "", "0"):
            sizes[g] = sizes.get(g, 0) + 1
    solo = sum(1 for f, v in cars.items()
               if v["lg"] in (None, "", "0") or sizes.get(v["lg"], 1) == 1)
    lead_g = cars[order[0]]["lg"]
    win = [f for f, v in cars.items() if v["fo"] == 1]
    return dict(
        axis_sum=p3[order[0]] + p3[order[1]],
        gap=(sum(p3[c] for c in others[:2]) / 2) - (sum(p3[c] for c in others[2:]) / 3),
        both=set(order[:2]) <= top3,
        third_top=bool((top3 - set(order[:2])) <= set(others[:2])),
        odds=od.get(frozenset(top3)),
        n_lines=int(cars[order[0]]["nl"] or 0),
        solo=solo,
        top_line_size=sizes.get(lead_g, 1),
        n_nige=sum(1 for v in cars.values() if v["style"] == "逃"),
        factor=cars[win[0]]["factor"] if win else "",
        same_line3=(len({cars[f]["lg"] for f in top3}) == 1
                    and cars[list(top3)[0]]["lg"] not in (None, "", "0")),
    )


def table(rows, keyf, title, keys_order=None):
    g = defaultdict(list)
    for r in rows:
        g[keyf(r)].append(r)
    print(f"\n[{title}]")
    print("  値        n       二軸そろい  3着が全体3,4  同ライン3車決着  オッズ中央   決まり手(逃/捲/差)")
    for k in (keys_order or sorted(g)):
        sub = g.get(k)
        if not sub or len(sub) < 200:
            continue
        al = [r for r in sub if r["both"]]
        t = sum(1 for r in al if r["third_top"]) / len(al) * 100 if al else 0
        os_ = sorted(r["odds"] for r in sub if r["odds"])
        fc = Counter(r["factor"] for r in sub)
        n = sum(fc[x] for x in ("逃", "捲", "差")) or 1
        print(f"  {str(k):8s} {len(sub):6,}     {len(al)/len(sub)*100:6.2f}%    {t:8.1f}%"
              f"      {sum(1 for r in sub if r['same_line3'])/len(sub)*100:8.1f}%"
              f"   {median(os_) if os_ else 0:8.1f}倍   "
              f"{fc['逃']/n*100:.0f}/{fc['捲']/n*100:.0f}/{fc['差']/n*100:.0f}")


def main() -> None:
    rows = [features(c, o) for _, c, o in load()]
    table(rows, lambda r: r["n_lines"], "ライン本数")
    table(rows, lambda r: r["top_line_size"], "指数1位が属するラインの人数")
    table(rows, lambda r: r["solo"], "単騎の人数")
    table(rows, lambda r: r["n_nige"], "逃げ型（style='逃'）の人数")

    # 2×2 の中でライン本数が追加情報を持つか
    gaps = sorted(r["gap"] for r in rows)
    gmid = gaps[len(gaps) // 2]
    print("\n[2×2 の中でライン本数を割る — ①②の上に情報を足すか]")
    print("  軸        相手      ライン   n      二軸そろい  3着が全体3,4  オッズ中央")
    for aname, af in (("堅い", lambda r: r["axis_sum"] >= RANK_7C_P3_SUM_MIN),
                      ("混戦", lambda r: r["axis_sum"] < RANK_7C_P3_SUM_MIN)):
        for gname, gf in (("開く", lambda r: r["gap"] >= gmid),
                          ("狭い", lambda r: r["gap"] < gmid)):
            for nl in (2, 3, 4):
                sub = [r for r in rows if af(r) and gf(r) and r["n_lines"] == nl]
                if len(sub) < 200:
                    continue
                al = [r for r in sub if r["both"]]
                t = sum(1 for r in al if r["third_top"]) / len(al) * 100 if al else 0
                os_ = sorted(r["odds"] for r in sub if r["odds"])
                print(f"  {aname:8s}  {gname:6s}  {nl}本  {len(sub):6,}     "
                      f"{len(al)/len(sub)*100:6.2f}%    {t:8.1f}%   {median(os_) if os_ else 0:8.1f}倍")




def main2() -> None:
    """①②の中で『指数1位が属するラインの人数』が何を動かすか（本数より効く）。"""
    rows = [features(c, o) for _, c, o in load()]
    gaps = sorted(r["gap"] for r in rows)
    gmid = gaps[len(gaps) // 2]
    print("\n[2×2 の中で『指数1位のライン人数』を割る]")
    print("  軸    相手    1位のライン   n      二軸そろい  3着が全体3,4  オッズ中央  逃/捲/差")
    for aname, af in (("堅い", lambda r: r["axis_sum"] >= RANK_7C_P3_SUM_MIN),
                      ("混戦", lambda r: r["axis_sum"] < RANK_7C_P3_SUM_MIN)):
        for gname, gf in (("開く", lambda r: r["gap"] >= gmid),
                          ("狭い", lambda r: r["gap"] < gmid)):
            for sz in (1, 2, 3, 4):
                sub = [r for r in rows if af(r) and gf(r) and r["top_line_size"] == sz]
                if len(sub) < 200:
                    continue
                al = [r for r in sub if r["both"]]
                t = sum(1 for r in al if r["third_top"]) / len(al) * 100 if al else 0
                os_ = sorted(r["odds"] for r in sub if r["odds"])
                fc = Counter(r["factor"] for r in sub)
                n = sum(fc[x] for x in ("逃", "捲", "差")) or 1
                lbl = "単騎" if sz == 1 else f"{sz}人"
                print(f"  {aname:4s}  {gname:4s}  {lbl:8s} {len(sub):6,}     "
                      f"{len(al)/len(sub)*100:6.2f}%    {t:8.1f}%   {median(os_) if os_ else 0:8.1f}倍"
                      f"   {fc['逃']/n*100:.0f}/{fc['捲']/n*100:.0f}/{fc['差']/n*100:.0f}")


if __name__ == "__main__":
    main2()
