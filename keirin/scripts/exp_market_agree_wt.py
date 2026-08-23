#!/usr/bin/env python3
"""競輪版 `market_agree` の検証 — モデルと**市場**の一致で成績が割れるか。

## 背景（なぜ今これを測るのか）

中央（JRA）で、指数の信頼度（gap ベースの S/A/B/C）よりも
**「指数1位馬が単勝1番人気と一致するか（market_agree）」のほうが的中率の分離を
支配する**ことが分かり、tier の第一分岐がそちらへ置き換わった
（[[jra_axis_market_agree_redesign]]）。しかもその手法は
**もともと競輪の軸選定から中央へ移したもの**だった。

ところが競輪側のランク分岐は `wt_overlap_n`（モデル上位2車が **winticket の
公式予想印 ◎○** と一致するか）と `order_disagree` で切っており、
**市場（オッズ）との一致では一度も切っていない**。印は予想家の意見であって
市場ではないので、中央の market_agree とは別物。ここが空いている。

## 市場の車ごとの評価をどう作るか

🔴 **競輪には単勝の板が無い**（`wt_odds.bet_type` は trifecta / exacta / trio /
   quinella / quinellaPlace）。車ごとの市場評価は**周辺化**で作る:

    市場3着内率  p3_i = 3 × Σ_{c ∋ i} (1/o_c) / Σ_c (1/o_c)      … trio 板
    市場1着率    pw_i =     Σ_{j≠i} (1/o_{i-j}) / Σ (1/o)         … exacta 板

## ⚠️ この板は「確定オッズ」

`wt_odds` はレース後に1スナップショットだけ収集される。したがって本スクリプトが
測るのは **「信号が存在するか」の上界**であって、朝8:00 の入稿では使えない。
使えるかどうかは**予測オッズ版**（`--source predicted`）で別に測る。
両方出して差を見ること。

🔴 **確定オッズ版で効果が出ても、それだけで採用してはいけない。**
   入稿時点に無い情報なので、そのまま実装すると look-ahead になる。
"""
from __future__ import annotations

import argparse
import json
import os
import random
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

random.seed(11)


def load_cache(path: str) -> dict[str, dict]:
    out = {}
    with open(path) as f:
        for line in f:
            r = json.loads(line)
            out[r["race_key"]] = r
    return out


def market_boards(keys: list[str], morning: bool = False
                  ) -> dict[str, dict[str, dict[int, float]]]:
    """オッズ板を車ごとの市場評価へ周辺化する。

    `morning=True` なら `wt_odds_snapshot`（snapshot_type='morning'）を読む。
    🟢 **朝の板は 07:03 に収集され、入稿の開始は 07:09**（2026-08-22 実測）＝
       **入稿の6分前に実市場が手元にある**。予測オッズで代用する必要はない。
    ⚠️ ただし収集開始は **2026-06-08** から。それ以前は存在しない。
    ⚠️ 朝の時点では後半レースの板が薄い/欠けることがある。**欠けたレースは
       黙って一致扱いにせず、判定不能として母集団から外す**こと。
    """
    con = psycopg2.connect(os.environ["KEIRIN_DB_URL"])
    cur = con.cursor()
    table = ("keirin.wt_odds_snapshot" if morning else "keirin.wt_odds")
    extra = (" and snapshot_type='morning'" if morning else "")
    out: dict[str, dict[str, dict[int, float]]] = defaultdict(dict)
    for bet, kind in (("trio", "p3"), ("exacta", "pw")):
        acc: dict[str, dict[int, float]] = defaultdict(lambda: defaultdict(float))
        tot: dict[str, float] = defaultdict(float)
        for i in range(0, len(keys), 2000):
            chunk = keys[i:i + 2000]
            cur.execute(
                f"select race_key, combination, odds_value from {table} "
                f"where bet_type=%s and race_key = any(%s) and odds_value > 0{extra}",
                (bet, chunk))
            for rk, combo, o in cur.fetchall():
                inv = 1.0 / float(o)
                tot[rk] += inv
                cars = [int(x) for x in str(combo).replace("=", "-").split("-")]
                if kind == "pw":
                    acc[rk][cars[0]] += inv        # 二車単の1着列だけ足す
                else:
                    for c in cars:
                        acc[rk][c] += inv
        for rk, d in acc.items():
            t = tot[rk]
            if t <= 0:
                continue
            out[rk][kind] = {c: v / t for c, v in d.items()}
    return out


def predicted_boards(cache: dict[str, dict]) -> dict[str, dict[str, dict[int, float]]]:
    """予測三連単オッズ板を同じ式で周辺化する（**朝8:00 に使える版**）。"""
    out: dict[str, dict[str, dict[int, float]]] = {}
    for rk, row in cache.items():
        inv_w: dict[int, float] = defaultdict(float)
        inv_3: dict[int, float] = defaultdict(float)
        tot = 0.0
        for leg, o in row["odds"].items():
            if o <= 0:
                continue
            inv = 1.0 / float(o)
            tot += inv
            a, b, c = (int(x) for x in leg.split("-"))
            inv_w[a] += inv
            for x in (a, b, c):
                inv_3[x] += inv
        if tot <= 0:
            continue
        out[rk] = {"pw": {c: v / tot for c, v in inv_w.items()},
                   "p3": {c: v / tot for c, v in inv_3.items()}}
    return out


def agree_flags(row: dict, mk: dict) -> dict[str, bool] | None:
    p3 = {int(k): v for k, v in row["p3"].items()}
    pw = {int(k): v for k, v in row["pw"].items()}
    m3, mw = mk.get("p3"), mk.get("pw")
    if not m3 or not mw or len(m3) < 3 or len(mw) < 3:
        return None
    top = lambda d, n: [k for k, _ in sorted(d.items(), key=lambda kv: -kv[1])][:n]
    return {
        # モデルの1着率1位 == 市場の1着率1位（JRA market_agree に最も近い）
        "agree_win1": top(pw, 1)[0] == top(mw, 1)[0],
        # モデルの3着内率1位 == 市場の3着内率1位
        "agree_p3_1": top(p3, 1)[0] == top(m3, 1)[0],
        # 軸2車（モデル p3 上位2）が市場の p3 上位2と**集合として**一致
        "agree_p3_top2": set(top(p3, 2)) == set(top(m3, 2)),
    }


def boot_diff(a: list[tuple], b: list[tuple], B: int = 3000):
    """日単位でリサンプルして (ROI差, 的中率差) の CI。a/b は同じ日で対応しない
    ので**別々にリサンプル**する（層別ブートストラップ）。"""
    def draw(xs):
        s = [xs[random.randrange(len(xs))] for _ in xs]
        bet = sum(x[0] for x in s); pay = sum(x[1] for x in s)
        n = sum(x[2] for x in s); h = sum(x[3] for x in s)
        return (pay / bet if bet else 0.0), (h / n if n else 0.0)
    dr, dh = [], []
    for _ in range(B):
        ra, ha = draw(a); rb, hb = draw(b)
        dr.append(rb - ra); dh.append(hb - ha)
    dr.sort(); dh.sort()
    return (dr[int(B * .025)], dr[int(B * .975)],
            dh[int(B * .025)], dh[int(B * .975)])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="data/exp/tf_shape_cache.jsonl")
    ap.add_argument("--source", choices=("final", "predicted", "morning"),
                    default="final")
    ap.add_argument("--start", default="2026-01-01")
    ap.add_argument("--end", default="2026-08-21")
    args = ap.parse_args()

    cache = load_cache(args.cache)
    keys = sorted(cache)
    print(f"キャッシュ {len(keys)}R / 市場={args.source}")
    if args.source == "predicted":
        mk = predicted_boards(cache)
    else:
        mk = market_boards(keys, morning=(args.source == "morning"))

    flags: dict[str, dict[str, bool]] = {}
    for rk in keys:
        f = agree_flags(cache[rk], mk.get(rk, {}))
        if f:
            flags[rk] = f
    print(f"市場板を作れたレース: {len(flags)}R\n")

    con = psycopg2.connect(os.environ["KEIRIN_DB_URL"])
    cur = con.cursor()
    cur.execute("""
        select rank, race_date, race_key, bet_amount, payout, hit
        from keirin.picks_history
        where race_date between %s and %s and bet_amount > 0
    """, (args.start, args.end))
    rows = cur.fetchall()

    for key in ("agree_win1", "agree_p3_1", "agree_p3_top2"):
        print(f"===== {key} =====")
        print(f"{'ランク':10}{'一致 n':>8}{'的中%':>7}{'ROI':>8}"
              f"{'乖離 n':>8}{'的中%':>7}{'ROI':>8}"
              f"{'Δ的中[CI]':>22}{'ΔROI[CI]':>22}")
        by_rank: dict[str, dict[bool, dict[str, list]]] = defaultdict(
            lambda: {True: defaultdict(list), False: defaultdict(list)})
        for rank, d, rkey, bet, pay, hit in rows:
            base = str(rkey).split("#")[0]
            f = flags.get(base)
            if f is None:
                continue
            by_rank[rank][f[key]][d].append((int(bet), int(pay or 0), 1, int(hit)))
        for rank, sides in sorted(by_rank.items()):
            agg = {}
            for side in (True, False):
                days = [tuple(map(sum, zip(*v))) for v in sides[side].values() if v]
                agg[side] = days
            if len(agg[True]) < 20 or len(agg[False]) < 20:
                continue
            def tot(days):
                bet = sum(x[0] for x in days); pay = sum(x[1] for x in days)
                n = sum(x[2] for x in days); h = sum(x[3] for x in days)
                return n, h / n, (pay / bet if bet else 0.0)
            nA, hA, rA = tot(agg[True]); nD, hD, rD = tot(agg[False])
            rlo, rhi, hlo, hhi = boot_diff(agg[False], agg[True])
            mark = " 🟢" if (hlo > 0 or hhi < 0) else ""
            print(f"{rank:10}{nA:>8}{hA:>7.1%}{rA:>8.1%}{nD:>8}{hD:>7.1%}{rD:>8.1%}"
                  f"{f'{(hA-hD)*100:+.1f}pt[{hlo*100:+.1f},{hhi*100:+.1f}]':>22}"
                  f"{f'{(rA-rD)*100:+.1f}pt[{rlo*100:+.1f},{rhi*100:+.1f}]':>22}{mark}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
