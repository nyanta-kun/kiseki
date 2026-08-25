"""9車の軸モデル診断（読み取りのみ・vintage walk-forward 予測を使う）。"""
import glob, pickle, sys, numpy as np, pandas as pd
sys.path.insert(0, '.')
from src.database import get_connection

def load(pat, n_car):
    fr = []
    for f in sorted(glob.glob(f"data/exp_cache/{pat}")):
        d = pickle.load(open(f, "rb"))
        fr.append(d[["race_key", "frame_no", "pp3", "ppw"]])
    P = pd.concat(fr, ignore_index=True).drop_duplicates(["race_key", "frame_no"])
    return P

P9 = load("wf_preds9_*.pkl", 9)
P7 = load("wf_preds_*.pkl", 7)
print(f"9車 vintage {P9.race_key.nunique():,}R / 7車 {P7.race_key.nunique():,}R")

keys = sorted(set(P9.race_key) | set(P7.race_key))
ent, meta = {}, {}
with get_connection() as c:
    for i in range(0, len(keys), 700):
        ch = keys[i:i+700]; ph = ",".join("?"*len(ch))
        for r in c.execute(f"SELECT race_key,race_date,race_type,n_entries,cup_grade FROM wt_races WHERE race_key IN ({ph})", ch):
            meta[r["race_key"]] = dict(date=str(r["race_date"]), rtype=r["race_type"],
                                       ne=int(r["n_entries"] or 0), cg=r["cup_grade"])
        for r in c.execute(f"SELECT race_key,frame_no,finish_order,line_group,line_size,line_pos,"
                           f"is_line_leader,n_lines,style,factor,race_point,prediction_mark "
                           f"FROM wt_entries WHERE race_key IN ({ph})", ch):
            ent.setdefault(r["race_key"], []).append(dict(r))
rows = []
for P, nc in ((P9, 9), (P7, 7)):
    for rk, g in P.groupby("race_key"):
        m = meta.get(rk); es = ent.get(rk)
        if not m or not es or m["ne"] != nc: continue
        fo = {e["frame_no"]: e["finish_order"] for e in es}
        if sum(1 for v in fo.values() if v and 1 <= int(v) <= 3) != 3: continue
        p3 = dict(zip(g.frame_no.astype(int), g.pp3.astype(float)))
        pw = dict(zip(g.frame_no.astype(int), g.ppw.astype(float)))
        if len(p3) != nc: continue
        for e in es:
            f = e["frame_no"]
            if f not in p3: continue
            o = fo.get(f)
            rows.append(dict(nc=nc, rk=rk, date=m["date"], rtype=m["rtype"], cg=m["cg"],
                             f=f, p3=p3[f], pw=pw[f], top3=int(bool(o and 1 <= int(o) <= 3)),
                             win=int(bool(o and int(o) == 1)),
                             lpos=e["line_pos"], lsize=e["line_size"], nl=e["n_lines"], lg=e["line_group"],
                             leader=e["is_line_leader"], style=e["style"],
                             rp=e["race_point"], mark=e["prediction_mark"],
                             p3rank=None))
D = pd.DataFrame(rows)
D["p3rank"] = D.groupby("rk")["p3"].rank(ascending=False, method="first").astype(int)
D.to_pickle("/tmp/diag9.pkl")
print(f"rows {len(D):,}  9車 {D[D.nc==9].rk.nunique():,}R  7車 {D[D.nc==7].rk.nunique():,}R")
