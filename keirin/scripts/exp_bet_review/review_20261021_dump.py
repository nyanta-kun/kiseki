"""2026-10-21 型ラボの紙上と実売の食い違いの検証 — DB から読み取り専用で抜き出す。

使い方（KEIRIN_DB_URL が要る）:
    PYTHONPATH=scripts/exp_bet_review .venv/bin/python scripts/exp_bet_review/review_20261021_dump.py OUTDIR

出力: OUTDIR/live.pkl（live 行・全車数）/ subs.pkl（入稿）/ live7_legs.pkl（7車の live 行と買い目）
"""
import sys

import pandas as pd
from _db import q


def main(out: str) -> None:
    """3つの表を pickle へ書く。"""
    c, r = q("""select race_key, race_date, venue_name, race_no, race_type, n_entries, type_label,
                axis_sum, arare, plan_key, mode, budget, settled_at is not null as settled, hit,
                payout, pred_mean_payout, void_refund
                from keirin.type_lab_picks where mode in ('live','live9')
                and race_date between '2026-09-01' and '2026-10-21'""")
    pd.DataFrame(r, columns=c).to_pickle(f"{out}/live.pkl")
    c, r = q("""select race_key, rank_key, origin, status, submitted_at, is_confident,
                settled_bet, settled_payout, settled_hit, settled_at
                from keirin.netkeirin_submissions where race_key >= '20260901'""")
    pd.DataFrame(r, columns=c).to_pickle(f"{out}/subs.pkl")
    c, r = q("""select race_key, race_date, race_type, n_entries, type_label, axis_sum, pw_ent,
                plan_key, legs, pred_mean_payout, settled_at is not null as settled, payout,
                budget, generated_at
                from keirin.type_lab_picks where mode='live'
                and race_date between '2026-09-15' and '2026-10-21' and n_entries=7""")
    pd.DataFrame(r, columns=c).to_pickle(f"{out}/live7_legs.pkl")


if __name__ == "__main__":
    main(sys.argv[1])
