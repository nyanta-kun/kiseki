#!/usr/bin/env python3
"""「点数を絞って1点を厚くする（集中型）」を正しく再現する（2026-08-27）。

競合の高額的中30件のうち **9件は 1〜2点に 3,000〜10,000円**を置いた集中型
（`docs/rival_hot_highpay_2026_08_27.md`）。自社の的中倍率は中央 5.6倍で
競合の 228倍と2桁違う。差は精度ではなく**点数**にある、というのが競合分析の主張。

🔴 **点数を減らすと的中率も落ちる。** 「今の的中を保ったまま倍率だけ上がる」計算は
   間違い（最初にそれをやりかけた）。保存してある `legs`（prob つき）と `win_combo`
   から、**上位k点だけ買っていたら当たったか**を1件ずつ判定して再現する。

   払戻 = 的中した点の確定オッズ × (予算 ÷ k)
"""
from __future__ import annotations

import json
import statistics as stx
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
from src.database import get_connection  # noqa: E402

WALL = 74.85
BUDGET = 10_000


def load() -> list[dict]:
    q = ("SELECT plan_key, race_date, bet_type, n_legs, budget, legs, win_combo, "
         "       final_odds, hit, payout "
         "FROM type_lab_picks WHERE mode='paper' AND settled_at IS NOT NULL "
         "  AND race_date BETWEEN '2026-05-01' AND '2026-08-26'")
    cols = ("plan_key", "race_date", "bet_type", "n_legs", "budget", "legs",
            "win_combo", "final_odds", "hit", "payout")
    with get_connection() as c:
        rows = [dict(zip(cols, tuple(r))) for r in c.execute(q).fetchall()]
    for r in rows:
        lg = r["legs"]
        r["legs"] = json.loads(lg) if isinstance(lg, str) else (lg or [])
        r["race_date"] = str(r["race_date"])
    return rows


def topk(r: dict, k: int, order: str) -> list[dict]:
    legs = list(r["legs"])
    if order == "prob":
        legs.sort(key=lambda x: -float(x.get("prob") or 0))
    else:                                   # ev = prob × 予測オッズ
        legs.sort(key=lambda x: -(float(x.get("prob") or 0)
                                  * float(x.get("pred_odds") or 0)))
    return legs[:k]


def simulate(rows: list[dict], k: int, order: str) -> dict:
    """上位k点だけ買った場合。**確定オッズが引けない的中は捨てない**（外れ扱いにしない）
    ため、`final_odds` が無い的中は母集団から除く。"""
    inv = ret = n = nhit = nshown = nbig = 0
    odds_hit: list[float] = []
    days = set()
    for r in rows:
        legs = topk(r, k, order)
        if not legs:
            continue
        stake = BUDGET // k // 100 * 100          # 100円単位に丸める
        if stake <= 0:
            continue
        spent = stake * len(legs)
        won = r["win_combo"] and any(str(x.get("combo")) == str(r["win_combo"])
                                     for x in legs)
        if won and not r["final_odds"]:
            continue                              # 倍率が引けない的中は測れない
        pay = int(float(r["final_odds"]) * stake) if won else 0
        n += 1
        days.add(r["race_date"])
        inv += spent
        ret += pay
        if won:
            nhit += 1
            odds_hit.append(float(r["final_odds"]))
            if pay > spent:
                nshown += 1
            if pay >= 100_000:
                nbig += 1
    nd = max(len(days), 1)
    return {"k": k, "order": order, "n": n, "per_day": n / nd,
            "hit": nhit / n * 100 if n else 0,
            "shown": nshown / n * 100 if n else 0,
            "roi": ret / inv * 100 if inv else 0,
            "med_odds": stx.median(odds_hit) if odds_hit else 0,
            "big_per_day": nbig / nd, "stake": BUDGET // k // 100 * 100}


def main() -> None:
    rows = load()
    by = defaultdict(list)
    for r in rows:
        by[r["plan_key"]].append(r)
    days = len({r["race_date"] for r in rows})
    print(f"確認窓 2026-05-01〜08-26 / {days}日\n")

    for plan in sorted(by):
        base = by[plan]
        actual = [r for r in base]
        n_legs = stx.median([int(r["n_legs"]) for r in actual])
        print(f"== {plan}（実際は {n_legs:.0f}点）")
        print(f"{'買い方':16}{'1点':>7}{'件/日':>7}{'的中':>8}{'表示的中':>9}"
              f"{'倍率中央':>9}{'10万+/日':>9}{'ROI':>8}")
        # 実際の形
        hits = [r for r in actual if r["hit"] and r["final_odds"]]
        big = [r for r in actual if (r["payout"] or 0) >= 100_000]
        inv = sum(int(r["budget"]) for r in actual)
        ret = sum(int(r["payout"] or 0) for r in actual)
        shown = sum(1 for r in actual if (r["payout"] or 0) > int(r["budget"]))
        print(f"{'実際':16}{int(actual[0]['budget']) // int(n_legs):7,}"
              f"{len(actual) / days:7.1f}{len(hits) / len(actual) * 100:7.2f}%"
              f"{shown / len(actual) * 100:8.2f}%"
              f"{(stx.median([float(r['final_odds']) for r in hits]) if hits else 0):9.1f}"
              f"{len(big) / days:9.3f}{ret / inv * 100:7.1f}%")
        for order in ("prob", "ev"):
            for k in (1, 2, 3):
                s = simulate(base, k, order)
                lab = f"上位{k}点({'確率' if order == 'prob' else 'EV'}順)"
                print(f"{lab:16}{s['stake']:7,}{s['per_day']:7.1f}{s['hit']:7.2f}%"
                      f"{s['shown']:8.2f}%{s['med_odds']:9.1f}{s['big_per_day']:9.3f}"
                      f"{s['roi']:7.1f}%{'🟢' if s['roi'] > WALL else ''}")
        print()


if __name__ == "__main__":
    main()
