"""シュウ極(482) との比較用 — 型ラボの高額系 paper 行を長い窓で抜き出す（2026-10-25）。

使い方（KEIRIN_DB_URL が要る）:
    PYTHONPATH=scripts/exp_bet_review .venv/bin/python scripts/exp_bet_review/shu482_dump_paper.py OUTDIR

出力: OUTDIR/paper.pkl — mode in (paper, paper9)・2025-01-01〜2026-08-26・
      高額系（*_big / *_sign / A_ana）と比較用の hit 系の採点済み行。
      当たり目の市場人気順位 win_pop（確定三連単オッズで数えた順位）と最低三連単 tf_min を付ける。
"""
import sys

import pandas as pd
from _db import q


def main(out: str) -> None:
    """paper 行を pickle へ書く。"""
    c, r = q("""
        with p as (
            select race_key, race_date, venue_name, race_no, race_type, n_entries, type_label,
                   axis_sum, pw_ent, plan_key, mode, n_legs, budget, hit, payout, void_refund,
                   win_tf_odds, rule_version
            from keirin.type_lab_picks
            where mode in ('paper','paper9') and settled_at is not null
              and race_date between '2025-01-01' and '2026-08-26'
              and (plan_key like '%%\\_big' or plan_key like '%%\\_sign' or plan_key like '%%\\_hit'
                   or plan_key in ('A_ana','F_pay','A_trio'))),
        rk as (select distinct race_key, win_tf_odds from p where win_tf_odds is not null),
        o as (
            select rk.race_key, min(w.odds_value) as tf_min,
                   1 + sum(case when w.odds_value < rk.win_tf_odds then 1 else 0 end) as win_pop
            from rk join keirin.wt_odds w on w.race_key = rk.race_key and w.bet_type='trifecta'
                                          and w.odds_value > 0
            group by rk.race_key)
        select p.*, o.tf_min, o.win_pop from p left join o using (race_key)""")
    pd.DataFrame(r, columns=c).to_pickle(f"{out}/paper.pkl")


if __name__ == "__main__":
    main(sys.argv[1])
