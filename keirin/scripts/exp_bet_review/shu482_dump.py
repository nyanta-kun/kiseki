"""シュウのAI指数極(482) 検証用 — DB から読み取り専用で抜き出す（2026-10-25）。

使い方（KEIRIN_DB_URL が要る）:
    PYTHONPATH=scripts/exp_bet_review .venv/bin/python scripts/exp_bet_review/shu482_dump.py OUTDIR

出力:
    OUTDIR/races.pkl    — 2026-08-01〜10-24 の全レース（場名・種別・車数・三連単の最低オッズと当たり目オッズ）
    OUTDIR/entries.pkl  — 同期間の出走表（予測3着内率・1着率・公式印・ライン・着順）
    OUTDIR/tl.pkl       — 型ラボ live 行（09-06〜10-24・全プラン・採点済み）
"""
import sys

import pandas as pd
from _db import q

D0, D1 = "2026-08-01", "2026-10-24"


def main(out: str) -> None:
    """3つの表を pickle へ書く。"""
    c, r = q(f"""
        with tf as (
            select race_key, min(odds_value) as tf_min,
                   sum(1.0/odds_value) as tf_inv_sum,
                   count(*) as tf_n
            from keirin.wt_odds
            where bet_type='trifecta' and odds_value > 0
              and race_key >= '20260801' and race_key < '20261025'
            group by race_key)
        select w.race_key, w.race_date, w.race_no, w.race_type, w.grade, w.n_entries,
               v.name as venue_name, tf.tf_min, tf.tf_inv_sum, tf.tf_n
        from keirin.wt_races w
        left join keirin.venue_info v on v.venue_code = w.venue_id
        left join tf on tf.race_key = w.race_key
        where w.race_date between '{D0}' and '{D1}'""")
    pd.DataFrame(r, columns=c).to_pickle(f"{out}/races.pkl")

    c, r = q(f"""
        select race_key, frame_no, player_class, style, race_point, prediction_mark,
               line_group, line_size, line_pos, finish_order,
               pred_win_pct, pred_top3_pct, pred_top2_pct
        from keirin.wt_entries
        where race_key >= '20260801' and race_key < '20261025'""")
    pd.DataFrame(r, columns=c).to_pickle(f"{out}/entries.pkl")

    c, r = q("""
        select race_key, combination, odds_value
        from keirin.wt_odds
        where bet_type='trifecta' and race_key >= '20260801' and race_key < '20261025'""")
    pd.DataFrame(r, columns=c).to_pickle(f"{out}/tf_odds.pkl")

    c, r = q("""
        select race_key, race_date, venue_name, race_no, race_type, n_entries, type_label,
               axis_sum, pw_ent, plan_key, mode, bet_type, n_legs, budget, legs,
               settled_at is not null as settled, hit, payout, void_refund, win_combo, win_tf_odds
        from keirin.type_lab_picks
        where mode in ('live','live9') and race_date between '2026-09-06' and '2026-10-24'""")
    pd.DataFrame(r, columns=c).to_pickle(f"{out}/tl.pkl")


if __name__ == "__main__":
    main(sys.argv[1])
