#!/usr/bin/env python3
"""事前登録した 7S 波乱スコア選別を 2024 年で一度きり確認する（2026-08-21）。

規則・閾値・採用ラインは `docs/PREREG_7S_UPSET_GATE_2026_08_21.md` で
**2024 を見る前に**凍結済み。ここは実行するだけで、閾値を探索しない。

DB は読み取りのみ。
"""
from __future__ import annotations

import pickle
import random
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.database import get_connection  # noqa: E402
from src.preprocessing.upset_features import (  # noqa: E402
    build_upset_row, feature_vector,
)

MODEL = "lgbm_upset_screen_n15v2312"   # train-end 2023-12-31
THRESHOLD = 0.311346                   # 7車・〜2023-12-31 の p75（凍結）
D1, D2 = "2024-01-01", "2024-12-31"
LINE_DELTA = 1.5                       # 全件比 +1.5pt 以上
LINE_PCT = 90.0                        # 同件数ランダム帰無の90%点以上


def main() -> None:
    with open(REPO / "data" / "models" / f"{MODEL}.pkl", "rb") as f:
        model = pickle.load(f)

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT split_part(race_key,'#',1) rk, bet_amount, payout "
            "FROM picks_history WHERE rank='RANK_7S' AND bet_amount>0 "
            "  AND race_date BETWEEN ? AND ?", (D1, D2))
        picks = {r["rk"]: (int(r["bet_amount"]), int(r["payout"] or 0)) for r in cur}
        keys = list(picks)
        cur.execute("""
            SELECT e.race_key, r.n_entries, r.grade, r.race_type, r.day_index,
                   r.distance, r.start_at, e.frame_no, e.race_point, e.line_group,
                   e.line_size, e.style, e.player_class, e.s_count, e.b_count,
                   e.first_rate, e.third_rate, e.prediction_mark, e.finish_order,
                   v.bank_length, v.is_indoor
            FROM wt_entries e JOIN wt_races r USING(race_key)
            LEFT JOIN venue_info v ON v.venue_code=r.venue_id
            WHERE r.cancel=0 AND e.race_key = ANY(?)""", (keys,))
        ents: dict[str, list[dict]] = defaultdict(list)
        for e in cur:
            ents[e["race_key"]].append(dict(e))

    rows = []
    for rk, es in ents.items():
        race = {k: es[0].get(k) for k in
                ("n_entries", "grade", "race_type", "day_index", "distance",
                 "start_at", "bank_length", "is_indoor")}
        f = build_upset_row(es, race)
        if f is None:
            continue
        s = float(model.predict(np.array([feature_vector(f)], dtype=float))[0])
        bet, pay = picks[rk]
        rows.append((s, pay / bet))

    n = len(rows)
    keep = [r for s, r in rows if s >= THRESHOLD]
    allr = [r for _, r in rows]
    kpi = lambda v, x: 100.0 * sum(1 for r in v if r >= x) / len(v)
    roi = lambda v: 100.0 * sum(v) / len(v)

    print(f"モデル {MODEL} / 閾値 {THRESHOLD}（凍結）")
    print(f"2024 の 7S {n}R → 残す {len(keep)}R（{100*len(keep)/n:.1f}%）\n")
    print(f"{'':<10}{'n':>6}{'2倍+':>9}{'5倍+':>9}{'ROI':>9}")
    print(f"{'全件':<10}{n:>6}{kpi(allr,2):>8.2f}%{kpi(allr,5):>8.2f}%{roi(allr):>8.1f}%")
    print(f"{'残す側':<10}{len(keep):>6}{kpi(keep,2):>8.2f}%{kpi(keep,5):>8.2f}%"
          f"{roi(keep):>8.1f}%")

    rng = random.Random(0)
    flags = [1.0 if r >= 2.0 else 0.0 for r in allr]
    draws = sorted(100.0 * sum(rng.sample(flags, len(keep))) / len(keep)
                   for _ in range(4000))
    act = kpi(keep, 2.0)
    pct = 100.0 * sum(1 for d in draws if d < act) / len(draws)
    delta = act - kpi(allr, 2.0)
    print(f"\n同件数ランダム帰無: 平均 {sum(draws)/len(draws):.2f}% / "
          f"90%点 {draws[3600]:.2f}%")
    print(f"実測 {act:.2f}% → 帰無分布の {pct:.1f}%点 / 全件比 {delta:+.2f}pt")

    ok1, ok2 = delta >= LINE_DELTA, pct >= LINE_PCT
    print(f"\n採用ライン (1) 全件比 >= +{LINE_DELTA}pt : "
          f"{'✅' if ok1 else '❌'} {delta:+.2f}pt")
    print(f"採用ライン (2) 帰無の >= {LINE_PCT}%点 : "
          f"{'✅' if ok2 else '❌'} {pct:.1f}%点")
    print(f"\n判定: {'🟢 2024 で再現した' if (ok1 and ok2) else '❌ 再現しない → 採用しない'}")


if __name__ == "__main__":
    main()
