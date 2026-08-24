"""レース単位の特徴量行列（入稿時点で使える情報のみ・オッズ不使用）。"""
import pickle, sys, math
import numpy as np, pandas as pd
sys.path.insert(0,'.')
from src.race_shape import _normalized

d = pickle.load(open("/tmp/keirin_upset_ds.pkl","rb"))
E = d["E"].merge(d["pred"], on=["race_key","frame_no"], how="inner").merge(pickle.load(open("/tmp/keirin_e2.pkl","rb")), on=["race_key","frame_no"], how="left")
R = d["R"].copy(); R["race_date"]=R.race_date.astype(str)
F0 = pickle.load(open("/tmp/keirin_upset_frame.pkl","rb"))
tf = pickle.load(open("/tmp/keirin_tf_out.pkl","rb"))
keep = set(F0.race_key)
E = E[E.race_key.isin(keep)]
num = lambda s: pd.to_numeric(s, errors="coerce")

def ent(v):
    v = np.array(v, float); v = v[v>0]; return float(-(v*np.log(v)).sum()) if len(v) else np.nan

rows=[]
for rk, g in E.groupby("race_key", sort=False):
    if len(g)!=7: continue
    pw = _normalized([max(float(x),1e-6) for x in g.ppw], 1.0)
    p3 = _normalized([max(float(x),1e-6) for x in g.pp3], 3.0)
    if pw is None or p3 is None: continue
    fr = g.frame_no.tolist()
    pb = [float(x) for x in g.pbad]
    W=dict(zip(fr,pw)); T=dict(zip(fr,p3)); B=dict(zip(fr,pb))
    ow = sorted(fr,key=lambda f:-W[f]); ot = sorted(fr,key=lambda f:-T[f])
    ws=[W[f] for f in ow]; ts=[T[f] for f in ot]; bs=sorted(pb, reverse=True)
    rp = num(g.race_point).values
    r1 = num(g.first_rate).values; r3 = num(g.third_rate).values
    lg = g.line_group.values; ls = num(g.line_size).values
    mark = num(g.prediction_mark).values
    mk = dict(zip(fr, mark))
    maru = [f for f in fr if mk.get(f)==1]; maru2=[f for f in fr if mk.get(f)==2]
    idx = {f:i for i,f in enumerate(fr)}
    lgmap = dict(zip(fr, lg)); lsmap = dict(zip(fr, ls))
    rank_of_maru = (ot.index(maru[0])+1) if maru and maru[0] in ot else np.nan
    d_ = dict(race_key=rk,
        p1_1=ws[0],p1_2=ws[1],p1_3=ws[2],p1_4=ws[3],
        p1_g12=ws[0]-ws[1],p1_g23=ws[1]-ws[2],p1_g34=ws[2]-ws[3],
        p1_sum2=ws[0]+ws[1],p1_sum3=sum(ws[:3]), p1_ent=ent(ws), p1_hhi=sum(x*x for x in ws),
        p3_1=ts[0],p3_2=ts[1],p3_3=ts[2],p3_4=ts[3],p3_5=ts[4],
        p3_g12=ts[0]-ts[1],p3_g23=ts[1]-ts[2],p3_g34=ts[2]-ts[3],p3_g45=ts[3]-ts[4],
        p3_sum2=ts[0]+ts[1],p3_sum3=sum(ts[:3]), p3_ent=ent([x/3 for x in ts]),
        pb_max=bs[0],pb_min=bs[-1],pb_mean=float(np.mean(pb)),pb_sd=float(np.std(pb)),
        pb_top1=B[ot[0]],pb_top2=B[ot[1]],pb_top3=B[ot[2]],
        pb_axis_sum=B[ot[0]]+B[ot[1]],
        # 1着率順位と3着内率順位の食い違い（逃げ型と追込型の分離度）
        rank_disagree=float(sum(abs(ot.index(f)-ow.index(f)) for f in fr)),
        win_is_top3=float(ow[0]==ot[0]),
        rp_mean=float(np.nanmean(rp)),rp_sd=float(np.nanstd(rp)),rp_rng=float(np.nanmax(rp)-np.nanmin(rp)),
        rp_top=float(rp[idx[ot[0]]]), rp_top_minus_mean=float(rp[idx[ot[0]]]-np.nanmean(rp)),
        r1_mean=float(np.nanmean(r1)),r1_sd=float(np.nanstd(r1)),r1_max=float(np.nanmax(r1)),
        r3_mean=float(np.nanmean(r3)),r3_sd=float(np.nanstd(r3)),
        n_nige=int((g.style=="逃").sum()),n_oi=int((g.style=="追").sum()),n_ryo=int((g.style=="両").sum()),
        n_lines=int(num(g.n_lines).iloc[0] or 0), max_line=float(np.nanmax(ls)),
        n_tanki=int((ls==1).sum()), line_of_top=float(lsmap.get(ot[0], np.nan)),
        top2_same_line=float(lgmap.get(ot[0]) is not None and lgmap.get(ot[0])==lgmap.get(ot[1])),
        s_sum=float(num(g.s_count).sum()), b_sum=float(num(g.b_count).sum()), h_sum=float(num(g.h_count).sum()),
        gear_mean=float(np.nanmean(num(g.gear_ratio))), gear_sd=float(np.nanstd(num(g.gear_ratio))),
        ex_spurt=float(np.nanmean(num(g.ex_spurt_pct))), ex_thrust=float(np.nanmean(num(g.ex_thrust_pct))),
        ex_left=float(np.nanmean(num(g.ex_left_behind_pct))), ex_split=float(np.nanmean(num(g.ex_split_line_pct))),
        ex_snatch=float(np.nanmean(num(g.ex_snatch_pct))),
        n_class=int(g.player_class.nunique()),
        mark_agree1=float(bool(maru) and ot[0]==maru[0]),
        mark_agree2=float(bool(maru) and bool(maru2) and set(ot[:2])=={maru[0],maru2[0]}),
        maru_model_rank=rank_of_maru,
    )
    rows.append(d_)
X = pd.DataFrame(rows)
X = X.merge(R[["race_key","race_date","venue_id","race_no","grade","race_type","cup_grade"]],
            on="race_key", how="left")
X = X.merge(F0[["race_key","payout","mkt_p_favset"]], on="race_key", how="left")
X = X.merge(tf, on="race_key", how="left")
X["month"]=X.race_date.str[5:7].astype(int)
X["dow"]=pd.to_datetime(X.race_date).dt.dayofweek
for c in ["venue_id","grade","race_type","cup_grade"]:
    X[c]=X[c].astype("category")
print(X.shape)
pickle.dump(X, open("/tmp/keirin_feat.pkl","wb"))
print(X.dtypes.value_counts().to_dict())
