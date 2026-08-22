#!/usr/bin/env python3
"""8月の入稿を机上で再評価する（2026-08-22・ユーザー2件の依頼）。

## 何を測るか

**A. 「買い目の1点でも予想オッズが 2.0 倍未満のレースは推奨から除外」**
   （掛金の半分が入った点が2倍を割ると元返しにならない＝ガミ）

**B. 「レースごとの期待値の上位3レース／日のタイトルを『厳選の二軸』にする」**
   タイトルだけの変更なので売る商品は変わらない。確かめるべきは
   **『厳選』と名乗る3レースが実際に他より良いのか**。

## 🔴 保存されているオッズは使えない

`netkeirin_submissions.bet_detail` の `odds` は **8/21 までほぼ朝の板**
（`odds_source` 実測: 8/12〜8/21 は board 主体、8/07〜8/11 は欠損、
予測オッズに揃ったのは **8/22 から**）。保存値で遡ると「予想オッズの規則」を
別の量で評価することになる。

→ **予測オッズを引き直す**（`src.odds_prediction`）。本番モデルの
   `train_end = 2026-08-04` なので **8/05 以降は out-of-sample**。
   8/01〜8/04 は in-sample なので集計から外す。

## 採点

売った買い目（`bet_detail`）を確定オッズで採点する。同着は当たり目が複数に
なるので `src.result_top3` を通す（2026-08-22 の修正と同じ規則）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import psycopg2  # noqa: E402
import psycopg2.extras  # noqa: E402

from src.odds_prediction import (  # noqa: E402
    OddsPredictionUnavailable,
    _pl_trio,
    load_race_inputs,
    predict_board,
)
from src.result_top3 import winning_trifectas, winning_trios  # noqa: E402

#: 予測オッズモデルの学習終端。ここ以前は in-sample なので集計に入れない。
OOS_FROM = "20260805"


def _conn():
    return psycopg2.connect(os.environ["KEIRIN_DB_URL"])


def load(date_from: str, date_to: str) -> list[dict]:
    c = _conn()
    cur = c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        select s.race_key, s.rank_key, s.origin, s.status, s.venue_name, s.race_no,
               s.bet_detail, r.race_type, r.n_entries
        from keirin.netkeirin_submissions s
        join keirin.wt_races r on r.race_key = s.race_key
        where s.race_key between %s and %s and s.status = 'published'
        order by s.race_key
    """, (date_from, date_to + "_99_99"))
    subs = [dict(r) for r in cur.fetchall()]
    keys = sorted({s["race_key"] for s in subs})
    cur.execute("select race_key, finish_order, frame_no from keirin.wt_entries "
                "where race_key = any(%s) and finish_order between 1 and 3", (keys,))
    fin: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for r in cur.fetchall():
        fin[r["race_key"]].append((int(r["finish_order"]), int(r["frame_no"])))
    cur.execute("select race_key, bet_type, combination, odds_value from keirin.wt_odds "
                "where race_key = any(%s)", (keys,))
    od: dict[str, dict[tuple[str, str], float]] = defaultdict(dict)
    for r in cur.fetchall():
        od[r["race_key"]][(r["bet_type"], r["combination"])] = r["odds_value"]
    c.close()

    out = []
    for s in subs:
        try:
            bd = json.loads(s["bet_detail"] or "{}")
        except Exception:
            continue
        lines = bd.get("lines") or []
        total = int(bd.get("total") or 0)
        if not lines or not total:
            continue
        s.update(lines=lines, total=total, fin=fin.get(s["race_key"], []),
                 odds=od.get(s["race_key"], {}))
        out.append(s)
    return out


def predicted_odds(race_key: str, combos: list[tuple[str, list[int]]]):
    """買った各点の {(券種, 車番tuple): 予測オッズ} と PL 確率。三連複のみ対応。"""
    try:
        cars, p3, pw, meta = load_race_inputs(race_key)
        board = predict_board(cars, p3, pw, meta)
        pl = _pl_trio(pw, cars)
    except (OddsPredictionUnavailable, Exception):
        return None, None
    o, p = {}, {}
    for kind, nums in combos:
        if kind != "3連複":
            return None, None            # 三連単は予測盤面を持たない
        key = frozenset(nums)
        if key not in board or key not in pl:
            return None, None
        o[tuple(sorted(nums))] = float(board[key])
        p[tuple(sorted(nums))] = float(pl[key])
    return o, p


def score(sub) -> tuple[int, int, bool] | None:
    """(投資, 払戻, 的中) を確定オッズで。未確定・データ欠損は None。"""
    wins = {frozenset(x) for x in winning_trios(sub["fin"])}
    wins_tf = {tuple(x) for x in winning_trifectas(sub["fin"])}
    if not wins:
        return None
    bet = pay = 0
    for ln in sub["lines"]:
        st = int(ln.get("stake") or 0)
        bet += st
        nums = [int(x) for x in ln["combo"].replace("=", "-").split("-")]
        if ln["bet_type"] == "3連複":
            if frozenset(nums) in wins:
                o = sub["odds"].get(("trio", "-".join(str(x) for x in sorted(nums))))
                pay += int(st * (o or 0))
        else:
            if tuple(nums) in wins_tf:
                o = sub["odds"].get(("trifecta", "-".join(str(x) for x in nums)))
                pay += int(st * (o or 0))
    return bet, pay, pay > 0


def summarize(name: str, rows: list[dict]) -> str:
    n = len(rows)
    if not n:
        return f"  {name:22s} 0件"
    bet = sum(r["bet"] for r in rows)
    pay = sum(r["pay"] for r in rows)
    hit = sum(1 for r in rows if r["hit"])
    net = sum(1 for r in rows if r["pay"] >= r["bet"] and r["hit"])
    big = sum(1 for r in rows if r["hit"] and r["pay"] >= 2 * r["bet"])
    days = len({r["race_key"][:8] for r in rows})
    return (f"  {name:22s} {n:4d}件({n/days:4.1f}/日) 的中{hit:3d} ({100*hit/n:4.1f}%) "
            f"ガミなし{net:3d} 2倍+{big:3d} 投{bet:9,d} 回{pay:9,d} ROI {100*pay/bet:6.1f}%")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="d1", default="20260801")
    ap.add_argument("--to", dest="d2", default="20260821")
    ap.add_argument("--cut", type=float, default=2.0,
                    help="この倍率未満の点が1つでもあれば除外（A案）")
    ap.add_argument("--top", type=int, default=3, help="1日あたり『厳選』にする本数（B案）")
    args = ap.parse_args()

    subs = load(args.d1, args.d2)
    rows, skipped = [], defaultdict(int)
    for s in subs:
        sc = score(s)
        if sc is None:
            skipped["未確定/着順なし"] += 1
            continue
        bet, pay, hit = sc
        combos = [(ln["bet_type"],
                   [int(x) for x in ln["combo"].replace("=", "-").split("-")])
                  for ln in s["lines"]]
        o, p = predicted_odds(s["race_key"], combos)
        if o is None:
            skipped["予測オッズを作れない"] += 1
            continue
        # レースの期待値 = Σ p_i × 賭け金_i × 予測オッズ_i ÷ 投資
        ev = 0.0
        for ln in s["lines"]:
            key = tuple(sorted(int(x) for x in ln["combo"].replace("=", "-").split("-")))
            ev += p[key] * int(ln.get("stake") or 0) * o[key]
        rows.append(dict(race_key=s["race_key"], rank=s["rank_key"], origin=s["origin"],
                         venue=s["venue_name"], no=s["race_no"], bet=bet, pay=pay, hit=hit,
                         min_odds=min(o.values()), ev=ev / s["total"],
                         day=s["race_key"][:8]))
    print(f"母集団 {len(rows)}件（除外: {dict(skipped)}）")
    oos = [r for r in rows if r["day"] >= OOS_FROM]
    print(f"うち予測オッズが out-of-sample（{OOS_FROM}以降）: {len(oos)}件\n")

    print(f"=== A案: 買い目の1点でも予測オッズ < {args.cut} 倍なら除外 ===")
    keep = [r for r in oos if r["min_odds"] >= args.cut]
    drop = [r for r in oos if r["min_odds"] < args.cut]
    print(summarize("現行（全件）", oos))
    print(summarize(f"残す（>= {args.cut}倍）", keep))
    print(summarize(f"落とす（< {args.cut}倍）", drop))
    for cut in (1.6, 1.8, 2.0, 2.5, 3.0):
        k = [r for r in oos if r["min_odds"] >= cut]
        print(summarize(f"  参考 >= {cut}倍", k))

    print(f"\n=== B案: 期待値の上位{args.top}レース/日を『厳選の二軸』に ===")
    byday: dict[str, list[dict]] = defaultdict(list)
    for r in oos:
        byday[r["day"]].append(r)
    top, rest = [], []
    for d, rs in byday.items():
        rs.sort(key=lambda r: -r["ev"])
        top += rs[:args.top]
        rest += rs[args.top:]
    print(summarize(f"上位{args.top}（厳選）", top))
    print(summarize("それ以外", rest))
    print("\n  【対照】期待値でなく無作為に同数を選んだ場合")
    import random
    rng = random.Random(20260822)
    rt = []
    for d, rs in byday.items():
        rt += rng.sample(rs, min(args.top, len(rs)))
    print(summarize(f"  無作為{args.top}", rt))

    print("\n=== A案とB案を重ねた場合 ===")
    both_top, both_rest = [], []
    byday2: dict[str, list[dict]] = defaultdict(list)
    for r in keep:
        byday2[r["day"]].append(r)
    for d, rs in byday2.items():
        rs.sort(key=lambda r: -r["ev"])
        both_top += rs[:args.top]
        both_rest += rs[args.top:]
    print(summarize(f"A適用後の上位{args.top}", both_top))
    print(summarize("A適用後のそれ以外", both_rest))


if __name__ == "__main__":
    main()
