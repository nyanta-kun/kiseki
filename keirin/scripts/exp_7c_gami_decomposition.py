#!/usr/bin/env python3
"""7C のガミを「配分の誤差」と「構造的な壁」に分解する（2026-08-19）。

## なぜこの切り口か

7C のガミ対策は既に多数試して落としてある（`RANK_7C_P3_SUM_MIN` 節の
「✂️ 再提案しない」）。それらは**レースを選ぶ / 相手を削る**方向だった。
ここで測るのは**配分にどれだけ伸びしろが残っているか**。

三連複・軸2頭ながしで買う目 i の確定オッズを `o_i`、予算を B とする。
重み w_i ∝ 1/o_i で配分すると**どの目が来ても払戻は B / Σ(1/o_i) で一定**になる。
したがって

    Σ(1/o_i) > 1  →  どう配分してもガミ（**構造的な壁**）
    Σ(1/o_i) <= 1 →  確定オッズを知っていればガミを避けられた（**配分の誤差**）

`Σ(1/o_i)` は買う目の合成ブック。これが 1 を超えるレースの割合が
「買い方では絶対に取り返せない下限」で、残りが伸びしろになる。

## 比較する配分

| 名前 | 重み | 位置づけ |
|---|---|---|
| 均等 | 一定 | 配分導入前（2026-08-07 以前） |
| p3 のみ | p3^0.5 | 板が無いときの本番フォールバック |
| 確定オッズ（オラクル） | 1/o | 実装不能・**上限** |

⚠️ オラクルは発走後の情報なので運用不能。**到達可能な上限を測るためだけ**に使う。
⚠️ 朝の板は再現できないのでここには入れない（本番の傾斜配分は
   朝の板 × p3 × 予測オッズ。`src/stake_allocation.py`）。板の質の効果は
   別途 `keirin_netkeirin_gami_allocation` を参照。

使い方:
    PYTHONPATH=. .venv/bin/python scripts/exp_7c_gami_decomposition.py \
        --from 2025-01-01 --to 2026-08-18
"""
from __future__ import annotations

import argparse
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.database import get_connection  # noqa: E402
from src.p3_calibration import calibrated_p3_sum_top2  # noqa: E402
from src.strategy_wt import (  # noqa: E402
    RANK_7C_LEGS_MIN,
    RANK_7C_P3_SUM_MIN,
    rank_7c_select_axis,
    rank_7c_select_legs,
)

BUDGET = 10_000
UNIT = 100


def _parse(s: str) -> list[int]:
    return [int(x) for x in re.split(r"[-=>]+", str(s)) if x.strip().isdigit()]


def load(d1: str, d2: str):
    with get_connection() as conn:
        cur = conn.execute(
            "SELECT e.race_key, e.frame_no, e.pred_top3_pct, e.finish_order, "
            "       r.race_type, r.cup_grade "
            "FROM wt_entries e JOIN wt_races r USING(race_key) "
            "WHERE r.race_date BETWEEN ? AND ? AND r.n_entries = 7 "
            "  AND e.pred_top3_pct IS NOT NULL", (d1, d2))
        ent: dict[str, dict] = defaultdict(dict)
        meta: dict[str, tuple] = {}
        for rk, fn, p3, fo, rtype, grade in cur.fetchall():
            ent[rk][int(fn)] = dict(p3=float(p3) / 100.0, fo=fo)
            meta[rk] = (rtype, grade)
        cur = conn.execute(
            "SELECT o.race_key, o.combination, o.odds_value "
            "FROM wt_odds o JOIN wt_races r USING(race_key) "
            "WHERE r.race_date BETWEEN ? AND ? AND r.n_entries = 7 "
            "  AND o.bet_type = 'trio' AND o.odds_value > 0", (d1, d2))
        trio: dict[str, dict] = defaultdict(dict)
        for rk, cb, od in cur.fetchall():
            trio[rk][frozenset(_parse(cb))] = float(od)
    return ent, meta, trio


def _stakes(weights: dict, budget: int = BUDGET, unit: int = UNIT) -> dict:
    """重みを 100円単位へ落とす（切り捨て・余りは重み最大の目へ）。"""
    tot = sum(weights.values())
    if tot <= 0:
        n = len(weights)
        base = (budget // n) // unit * unit
        return {k: base for k in weights}
    out = {k: int(budget * w / tot) // unit * unit for k, w in weights.items()}
    rest = budget - sum(out.values())
    if rest > 0:
        out[max(weights, key=lambda k: weights[k])] += rest // unit * unit
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="d1", default="2025-01-01")
    ap.add_argument("--to", dest="d2", default="2026-08-18")
    args = ap.parse_args()

    ent, meta, trio = load(args.d1, args.d2)
    rows = []
    for rk, cars in ent.items():
        if len(cars) != 7 or rk not in trio:
            continue
        if sum(1 for v in cars.values() if v["fo"] in (1, 2, 3)) != 3:
            continue
        p3 = {f: v["p3"] for f, v in cars.items()}
        sel = rank_7c_select_axis(p3)
        if sel is None:
            continue
        a1, a2, _raw = sel
        if calibrated_p3_sum_top2(p3, *meta.get(rk, (None, None))) < RANK_7C_P3_SUM_MIN:
            continue
        legs = rank_7c_select_legs(sorted(set(p3) - {a1, a2}), p3)
        if len(legs) < RANK_7C_LEGS_MIN:
            continue
        combos = [frozenset({a1, a2, t}) for t in legs]
        if not all(c in trio[rk] for c in combos):
            continue
        rows.append(dict(rk=rk, date=rk[:8], p3=p3, legs=legs, combos=combos,
                         od={c: trio[rk][c] for c in combos},
                         win=frozenset(f for f, v in cars.items() if v["fo"] in (1, 2, 3))))

    days = len({r["date"] for r in rows})
    print(f"\n7C 再現: {len(rows)}R / {days}日 ({len(rows)/days:.1f}件per日) "
          f"[{args.d1}〜{args.d2}]")

    # ---- 構造的な壁: 買う目の合成ブック ----------------------------------
    books = [sum(1 / o for o in r["od"].values()) for r in rows]
    over = sum(1 for b in books if b > 1)
    print(f"\n===== 買う目の合成ブック Σ(1/確定オッズ) =====")
    print(f"  中央 {statistics.median(books):.3f} / "
          f"25%点 {sorted(books)[len(books)//4]:.3f} / "
          f"75%点 {sorted(books)[3*len(books)//4]:.3f}")
    print(f"  🔴 **1 を超える（＝どう配分してもガミ）レース: {over}R "
          f"({100*over/len(rows):.1f}%）**")
    hit_rows = [r for r in rows if r["win"] in r["od"]]
    hit_over = sum(1 for r in hit_rows if sum(1 / o for o in r["od"].values()) > 1)
    print(f"  的中したレースに限ると {hit_over}/{len(hit_rows)} "
          f"({100*hit_over/len(hit_rows):.1f}%) が構造的にガミ確定")

    # ---- 配分ごとの成績 --------------------------------------------------
    def run(name, wfn):
        bet = pay = hit = disp = 0
        ratios = []
        for r in rows:
            w = wfn(r)
            st = _stakes(w)
            b = sum(st.values())
            bet += b
            if r["win"] in st:
                hit += 1
                p = int(r["od"][r["win"]] * st[r["win"]])
                pay += p
                ratios.append(p / b)
                if p >= b:
                    disp += 1
        med = statistics.median(ratios) if ratios else 0
        print(f"  {name:<26}{100*hit/len(rows):>8.1f}{100*disp/len(rows):>9.1f}"
              f"{100*(hit-disp)/hit:>8.1f}{100*pay/bet:>8.1f}{med:>9.2f}")

    print(f"\n===== 配分ごと（同一レース・同一予算 {BUDGET:,}円）=====")
    print(f"  {'':26}{'素の的中%':>8}{'実質的中%':>9}{'ガミ%':>8}{'ROI%':>8}{'倍率中央':>9}")
    run("均等割り", lambda r: {c: 1.0 for c in r["combos"]})
    run("p3 のみ (^0.5)", lambda r: {c: (r["p3"][next(iter(c - {min(c)}))] ** 0.5)
                                     if False else
                                     (r["p3"][[t for t in r["legs"]
                                               if t in c][0]] ** 0.5)
                                     for c in r["combos"]})
    run("確定オッズ 1/o（オラクル）", lambda r: {c: 1.0 / r["od"][c] for c in r["combos"]})

    # ---- 点数別 ----------------------------------------------------------
    print(f"\n===== 点数別（オラクル配分での到達点）=====")
    print(f"  {'':26}{'R':>7}{'合成ブック中央':>14}{'ブック>1の割合':>15}")
    byn = defaultdict(list)
    for r, b in zip(rows, books):
        byn[len(r["legs"])].append(b)
    for n in sorted(byn):
        v = byn[n]
        print(f"  {'相手 ' + str(n) + '点':<26}{len(v):>7}{statistics.median(v):>14.3f}"
              f"{100*sum(1 for x in v if x > 1)/len(v):>14.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
