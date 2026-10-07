#!/usr/bin/env python3
"""H15 添付3レース（2026-10-05 防府 1R・5R・6R）の再現。DB 読み取りのみ（type_lab_picks / netkeirin_submissions / 売上）。"""
from __future__ import annotations
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _db import q

CAP = 10_000 / 150_000


def main():
    keys = ["20261005_63_01", "20261005_63_05", "20261005_63_06"]
    for k in keys:
        c, rows = q("""select plan_key, race_type, n_entries, axis_sum, axis1, axis2, p3_order, win_combo, final_odds, win_tf_odds,
                       n_legs, legs, pred_mean_payout, hit, payout, void_refund from keirin.type_lab_picks where race_key=%s and rule_version='39bb6514621f' order by plan_key""", (k,))
        d = [dict(zip(c, r)) for r in rows]
        r0 = d[0]
        print(f"\n##### {k}  {r0['race_type']} 7車 axis_sum={r0['axis_sum']} 軸={r0['axis1']},{r0['axis2']} 着順(2-7-4形式)={r0['win_combo']} final_odds列={r0['final_odds']}(未格納) 確定三連単オッズ(win_tf_odds列・払戻÷賭け金と一致)={r0['win_tf_odds']} p3順={r0['p3_order']}")
        c2, sub = q("""select rank_key, origin, status, settled_hit, settled_payout, settled_bet, title from keirin.netkeirin_submissions where race_key=%s and deleted_at is null""", (k,))
        print("  入稿:", sub)
        c3, sa = q("select sold_paid_points, n_sold, n_hits_incl_garami, n_hits_excl_garami from keirin.netkeirin_sales_race where race_key=%s", (k,))
        print("  売上:", sa)
        for p in d:
            if p["plan_key"] not in ("F_sign", "F_hit", "F_big", "F_pay", "T_firm"):
                continue
            legs = p["legs"]
            s = 0.0; tr = []
            for l in legs:
                s += 1.0 / l["pred_odds"]
                tr.append(f"{l['combo']}(予測{l['pred_odds']:.1f}倍,{l['stake']}円,Σ1/o={s:.4f})")
            print(f"  [{p['plan_key']}] {p['n_legs']}点 計画平均払戻{float(p['pred_mean_payout']):,.0f} hit={p['hit']} payout={p['payout']} :: " + " ".join(tr))
        print(f"  F_sign の詰め上限 Σ(1/予測) ≤ 10000/150000 = {CAP:.4f}（＝予測オッズ15倍未満の目は1点も入らない）")


main()
