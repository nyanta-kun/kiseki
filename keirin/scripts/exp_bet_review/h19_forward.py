#!/usr/bin/env python3
"""H19 前向きの参考（記述のみ）: type_lab_picks の段の紙上行（T_firm/T_mid/T_axis/T_upset・2026-09-15〜・採点済み）の成績。

    set -a; source ~/.config/kiseki/env >/dev/null 2>&1; set +a
    PYTHONPATH=. .venv/bin/python scripts/exp_bet_review/h19_forward.py
- DB は読み取り専用。URL は出力しない。段を売ったのは 2026-09-15 の1日だけ（以降の行は紙上）。
- 投資 = legs の stake 合計。払戻 = payout 列（最終オッズ×賭け金・void_refund は含めない）。表示的中 = 払戻 > 投資。
- 「全レースに当てた場合」= 各プランの全行。「段に振り分けた場合」= axis_sum で type_lab.tier_plan_key が指す段の行（T_axis は荒れ段の部分集合を別掲）。
"""
import sys, json
from pathlib import Path
import numpy as np
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parents[1]))
from _db import q
import src.type_lab as TL

cols, rows = q("select race_key, race_date, plan_key, legs, payout, hit, settled_at, rule_version, axis_sum, n_entries "
               "from keirin.type_lab_picks where plan_key in ('T_firm','T_mid','T_axis','T_upset')")
OUT = []
def P(s=""):
    print(s); OUT.append(s)

recs = []
n_unsettled = {}
for rk, rd, pk, legs, pay, hit, st, rv, ax, ne in rows:
    if st is None or pay is None:
        n_unsettled[pk] = n_unsettled.get(pk, 0) + 1
        continue
    legs = legs if isinstance(legs, list) else json.loads(legs)
    inv = float(sum(l["stake"] for l in legs))
    recs.append(dict(rk=rk, day=str(rd), plan=pk, inv=inv, pay=float(pay), ax=float(ax), rv=rv,
                     tier=TL.tier_plan_key(float(ax))))
def table(sel, title):
    P(f"\n{title}\n")
    P("| 段（プラン） | 採点済み R | 的中率 | 表示的中 | 回収率 | 10万+ | 的中時払戻 中央値 | 払戻最大 |")
    P("|---|---|---|---|---|---|---|---|")
    for pk in ("T_firm", "T_mid", "T_axis", "T_upset"):
        r = [x for x in recs if x["plan"] == pk and sel(x)]
        if not r:
            P(f"| {pk} | 0 | | | | | | |"); continue
        inv = np.array([x["inv"] for x in r]); pay = np.array([x["pay"] for x in r])
        h = pay > 0
        P(f"| {pk} | {len(r)} | {h.mean()*100:.1f}% | {(pay>inv).mean()*100:.1f}% | {pay.sum()/inv.sum()*100:.1f}% | {int((pay>=1e5).sum())} | {np.median(pay[h]) if h.any() else float('nan'):,.0f}円 | {pay.max():,.0f}円 |")
P(f"採点済みの段の行: " + str({pk: sum(1 for x in recs if x['plan']==pk) for pk in ('T_firm','T_mid','T_axis','T_upset')}) + f"  未採点（除外）: {n_unsettled}")
P("rule_version は期間中に複数ある（段の行の生成時期で版が割れる）: " + str(sorted({x['rv'] for x in recs})))
P(f"期間: {min(x['day'] for x in recs)} 〜 {max(x['day'] for x in recs)}（開催日 {len({x['day'] for x in recs})}）")
table(lambda x: True, "### A. 各プランを全レースに当てた場合（段の振り分けなし・全行）")
table(lambda x: x["tier"] == x["plan"] or (x["plan"] == "T_axis" and x["tier"] == "T_upset"),
      "### B. axis_sum で本来の段に振り分けた場合（本来売る段の行だけ。T_axis は荒れ段の部分集合）")
table(lambda x: x["day"] == "2026-09-15" and (x["tier"] == x["plan"] or (x["plan"] == "T_axis" and x["tier"] == "T_upset")),
      "### C. 販売した 2026-09-15 の1日だけ（B と同じ振り分け）")
table(lambda x: x["day"] > "2026-09-15" and (x["tier"] == x["plan"] or (x["plan"] == "T_axis" and x["tier"] == "T_upset")),
      "### D. 紙上のみ 2026-09-16〜（B と同じ振り分け）")
# 開催日×k など無し。2025 台の同じ段の成績との並置は報告側で。
open(HERE.parents[1] / "data/exp_bet_review/h19/h19_forward.md", "w").write("\n".join(OUT))
