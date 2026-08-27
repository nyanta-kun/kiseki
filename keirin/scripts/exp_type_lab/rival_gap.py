#!/usr/bin/env python3
"""競合（好調予想家10人）の高額的中の作られ方と、自社の買い方を同じ軸で並べる。

競合側の実測は `docs/rival_hot_highpay_2026_08_27.md`（30件の高額的中を1点ずつ展開）:
  1商品の総投資 中央 10,000円 / 点数 中央 8点 / 的中した1点の賭け金 中央 1,300円 /
  **的中倍率 中央 228倍**（範囲 12〜1,141）/ 10万+ 0.33〜1.5件/日

自社（`docs/rival_hot_highpay_2026_08_27.md` §3）: 的中倍率 中央 **5.6倍** / 最大 62.4倍 /
10万+ 0.10件/日。

🔴 払戻 = 的中倍率 × (予算 ÷ 点数)。**10万円を作る条件は「的中倍率 >= 10 × 点数」**
   （予算1万円のとき）。点数を決めた時点で必要倍率が決まる。
"""
from __future__ import annotations

import statistics as stx
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
from src.database import get_connection  # noqa: E402

BUDGET = 10_000


def main() -> None:
    q = ("SELECT plan_key, race_date, n_legs, budget, hit, payout, final_odds "
         "FROM type_lab_picks WHERE mode='paper' AND settled_at IS NOT NULL "
         "  AND race_date BETWEEN '2026-05-01' AND '2026-08-26'")
    cols = ("plan_key", "race_date", "n_legs", "budget", "hit", "payout", "final_odds")
    with get_connection() as c:
        rows = [dict(zip(cols, tuple(r))) for r in c.execute(q).fetchall()]
    days = len({str(r["race_date"]) for r in rows})
    g = defaultdict(list)
    for r in rows:
        g[r["plan_key"]].append(r)

    print(f"確認窓 2026-05-01〜08-26 / {days}日\n")
    print(f"{'plan':8}{'点数':>6}{'1点賭け金':>10}{'10万に要る倍率':>14}"
          f"{'的中倍率 中央':>13}{'最大':>9}{'100倍+':>8}{'払戻中央':>10}{'10万+/日':>9}")
    for p in sorted(g):
        v = g[p]
        legs = stx.median([int(x["n_legs"]) for x in v])
        stake = stx.median([int(x["budget"]) / int(x["n_legs"]) for x in v])
        need = 10 * legs                       # 予算1万円で10万円に届く倍率
        hits = [x for x in v if x["hit"] and x["final_odds"]]
        od = [float(x["final_odds"]) for x in hits]
        big = [x for x in v if (x["payout"] or 0) >= 100_000]
        o100 = sum(1 for o in od if o >= 100)
        med_pay = stx.median([x["payout"] for x in v if (x["payout"] or 0) > x["budget"]]
                             or [0])
        print(f"{p:8}{legs:6.1f}{stake:10,.0f}{need:14.0f}"
              f"{(stx.median(od) if od else 0):13.1f}{(max(od) if od else 0):9.1f}"
              f"{o100 / len(v) * 100:7.2f}%{med_pay:10,.0f}{len(big) / days:9.3f}")

    print("\n-- 参考: 競合10人（高額的中30件の分解・母集団が違うので件数は比べない）")
    print(f"{'競合':8}{'8.0':>6}{'1,300':>10}{'80':>14}{'228.0':>13}{'1141.0':>9}"
          f"{'—':>8}{'—':>10}{'0.33〜1.5':>9}")

    print("\n== 「10万円が出る頻度」を点数だけで説明できるか")
    print("   払戻 = 的中倍率 × (10,000 ÷ 点数)。点数を半分にすると必要倍率も半分。")
    for p in sorted(g):
        v = g[p]
        legs = stx.median([int(x["n_legs"]) for x in v])
        od = [float(x["final_odds"]) for x in v if x["hit"] and x["final_odds"]]
        if not od:
            continue
        for k in (1, 2, 3):
            need = 10 * k
            hit_rate = sum(1 for o in od if o >= need) / len(v) * 100
            if k == 1:
                print(f"   {p:8} 実点数 {legs:.0f}点 → 10万+ "
                      f"{sum(1 for o in od if o >= 10 * legs) / len(v) * 100:.2f}%", end="")
            print(f" | {k}点なら {hit_rate:.2f}%", end="")
        print()


if __name__ == "__main__":
    main()
