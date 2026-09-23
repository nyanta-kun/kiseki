"""7車・honest WF予測 × 三連単確定オッズ全目 × 着順 のレース表を作る。"""
import glob, pickle
import numpy as np, pandas as pd
C = "/Users/ysuzuki/GitHub/kiseki/keirin/data/"
OUT = "/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki-dev-keirin/215ebb0d-e9b5-401f-a659-538e08cabeb6/scratchpad/ax/"
wf = pd.concat([pd.read_pickle(f) for f in sorted(glob.glob(C+"exp_cache/wf_preds_*_f60_*.pkl"))])
fe = pd.read_pickle(C+"feature_cache/wtfeat_20221201_20260831_f70_728566_20260831.pkl")
fe = fe[fe.race_date >= "2024-07-01"][["race_key","race_date","race_type","grade","venue_id","frame_no","finish_order",
     "prediction_mark","line_group","line_pos","line_size","n_lines","race_point"]]
d = wf.merge(fe, on=["race_key","frame_no"], how="inner")
n = d.groupby("race_key").frame_no.transform("size")
d = d[n == 7]
hp = pd.read_pickle(C+"exp_cache/highpay_trifecta_7_25_1e+06.pkl")
fb = pd.read_pickle(C+"exp_cache/favbust_payouts.pkl")
rows = []
for rk, g in d.groupby("race_key", sort=False):
    g = g.sort_values("pp3", ascending=False)
    fo = g.set_index("frame_no").finish_order
    top = fo[(fo >= 1) & (fo <= 3)].sort_values()
    if len(top) != 3 or top.duplicated().any():   # 同着・欠車で3着内が崩れるレースは除外
        continue
    res = "-".join(str(int(x)) for x in top.index)
    odds = hp.get(rk)
    if odds is None:
        continue
    ro = odds.get(res)
    if ro is None and rk in fb and fb[rk]["tf"] == res:
        ro = fb[rk]["tf_odds"]
    # 市場の1着確率（三連単全目から周辺化）
    mk = np.zeros(8)
    for k, v in odds.items():
        if v and v > 0:
            mk[int(k.split("-")[0])] += 1 / v
    mk = mk / mk.sum() if mk.sum() > 0 else mk
    order = g.frame_no.tolist()
    pwr = g.sort_values("ppw", ascending=False).frame_no.tolist()
    rows.append(dict(race_key=rk, race_date=g.race_date.iloc[0], race_type=g.race_type.iloc[0],
        grade=g.grade.iloc[0], venue_id=g.venue_id.iloc[0], idx=order, idxw=pwr,
        p3=g.pp3.tolist(), pw=g.ppw.tolist(), res=res, res_odds=ro,
        n_combos=len(odds), mkt=[mk[f] for f in order],
        mark=dict(zip(g.frame_no, g.prediction_mark)), line=dict(zip(g.frame_no, g.line_group)),
        rp=g.race_point.tolist()))
r = pd.DataFrame(rows)
print(len(r), r.res_odds.isna().sum(), r.race_date.min(), r.race_date.max())
r.to_pickle(OUT+"races.pkl")
# 保存しておく（買い目評価で全目オッズが要る）
pickle.dump({k: hp[k] for k in r.race_key}, open(OUT+"odds.pkl","wb"))
