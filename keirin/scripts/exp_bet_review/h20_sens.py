#!/usr/bin/env python3
"""H20 判定外の感度: 版B で候補基準に 0.05pt 届かなかった wt_overlap_n=1 だけを除外集合にして下期を測る。
事前登録の候補ではない（採否の判定に使わない）。"""
import pickle, sys
from pathlib import Path
import numpy as np
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import alloc_common as C, h20_common as H, h20_run as R

recs, start = C.load_base()
R.REC = {r["key"]: r for r in recs.values()}
morning = C.morning_set(start)
rec_lab, _ = H.race_labels(recs, start)
rows_by_day = C.by_day(recs)
days = sorted(rows_by_day); days2 = [d for d in days if d > H.H1_END]
L_full = C.lineup(recs, morning, legacy=False, with_lead=False)
cuts = H.prod_cuts(recs, L_full)
cands = [dict(dim="wt_overlap_n", val="1")]
res = R.step2(recs, morning, rows_by_day, rec_lab, cands, False, days2, L_full, cuts)
e = R.evaluate(res, days2)
m0, m2 = e["m0"], e["m2"]
print(f"件/日 {m0['n_day']:.1f}->{m2['n_day']:.1f}  ROI {m0['roi']:.2f}->{m2['roi']:.2f}  対照中央 {np.median([m['roi'] for m in e['m3']]):.2f} "
      f"ΔvsCtrl {e['d_ctrl_pt']:+.2f} [{e['d_ctrl_ci'][0]:+.2f},{e['d_ctrl_ci'][1]:+.2f}] 勝ち {e['ctrl_wins']}/20")
print(f"②-① {e['d_base_pt']:+.2f} [{e['d_base_ci'][0]:+.2f},{e['d_base_ci'][1]:+.2f}] 表示的中 {m0['shown']:.2f}->{m2['shown']:.2f} ({e['d_shown_pt']:+.2f}) 10万+/日 {m0['big']:.3f}->{m2['big']:.3f} ({e['big_rel']:+.1f}%) 高額枠 {e['hp0']:.2f}->{e['hp2']:.2f}")
print(f"除外 売った {e['nsold_day']:.1f}/日 売らず {e['nunsold_day']:.1f}/日  pass={e['passed']}")
