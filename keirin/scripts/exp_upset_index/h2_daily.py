#!/usr/bin/env python3
"""H2 の最良型を指定期間で日次集計する（7車・9車・合計）。

型: 三連単 / レース選定 上位20%（閾値は 2025 の分位で固定）/ 100倍以上の安い順 10点 / ダッチ。
選定量は `mkt_p100`（直前オッズ）と参考の `mdl_ent`（オッズ不使用）。

使い方:
    PYTHONPATH=. .venv/bin/python scripts/exp_upset_index/h2_daily.py 2026-09-26 2026-10-02
"""

from __future__ import annotations

import sys

import pandas as pd

import scripts.exp_upset_index.h2_analyze as h

Q = 0.20
RULE, K, STAKING = "cheap", 10, "dutch"


def main() -> None:
    """日次表を出す。"""
    lo, hi = sys.argv[1], sys.argv[2]
    e = h.load_entries()
    res, pw = h.race_table(e)
    t = h.combos("trifecta", res, pw)
    sig = h.race_signals(t, pw)
    rr = h.race_results(t)
    rr = rr[(rr.rule == RULE) & (rr.k == K) & (rr.staking == STAKING)]
    rr = rr.merge(res[["race_key", "race_date", "n"]], on="race_key").merge(sig, left_on="race_key", right_index=True)
    # 確定済みのレース数（母数）
    pd.set_option("display.width", 200)
    for metric in ("mkt_p100", "mdl_ent"):
        ex = rr[rr.race_date.between("2025-01-01", "2025-12-31")]
        thr = {n: ex[ex.n == n][metric].quantile(1 - Q) for n in (7, 9)}
        win = rr[rr.race_date.between(lo, hi)]
        sel = win[win.apply(lambda r: r[metric] >= thr[r.n], axis=1)]
        rows = []
        for d, g in sel.groupby("race_date"):
            allr = win[win.race_date == d]
            for label, s, a in (("7車", g[g.n == 7], allr[allr.n == 7]), ("9車", g[g.n == 9], allr[allr.n == 9]),
                                ("合計", g, allr)):
                rows.append(dict(日付=d, 区分=label, 全レース=len(a), 対象=len(s), 的中=int(s.hit.sum()),
                                 的中率=s.hit.mean() if len(s) else 0.0, 投資=int(s.bet.sum()),
                                 払戻=int(s.pay.sum()),
                                 回収率=s.pay.sum() / s.bet.sum() if len(s) else 0.0))
        df = pd.DataFrame(rows)
        tot = []
        for label in ("7車", "9車", "合計"):
            s = df[df.区分 == label]
            tot.append(dict(日付="期間計", 区分=label, 全レース=s.全レース.sum(), 対象=s.対象.sum(), 的中=s.的中.sum(),
                            的中率=s.的中.sum() / max(s.対象.sum(), 1), 投資=s.投資.sum(), 払戻=s.払戻.sum(),
                            回収率=s.払戻.sum() / max(s.投資.sum(), 1)))
        df = pd.concat([df, pd.DataFrame(tot)], ignore_index=True)
        print(f"\n===== 選定 {metric}（閾値 7車 {thr[7]:.3f} / 9車 {thr[9]:.3f}）=====")
        print(df.to_string(index=False, formatters={"的中率": "{:.1%}".format, "回収率": "{:.1%}".format}))


if __name__ == "__main__":
    main()
