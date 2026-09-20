import json, math
import numpy as np, pandas as pd

def load():
    d = pd.read_csv("main.csv", dtype={"race_key":str,"race_date":str,"venue_code":str,
                                       "race_id":str,"rank_key":str,"grade":str})
    d["date"] = d["race_date"].astype(str)
    # planned payout from the ACTUAL sold bet_detail
    def parse(bd):
        if not isinstance(bd,str) or not bd.strip(): return None
        try: return json.loads(bd)
        except Exception: return None
    d["_bd"] = d["bet_detail"].map(parse)
    def legs(r):
        o = r["_bd"]
        if not o: return []
        return [l for l in (o.get("lines") or []) if (l.get("role") or "base")=="base"]
    d["_legs"] = d.apply(legs, axis=1)
    def pay(ls):
        v=[float(l.get("stake") or 0)*float(l.get("odds") or 0) for l in ls if l.get("odds")]
        return float(np.mean(v)) if v else np.nan
    def paymin(ls):
        v=[float(l.get("stake") or 0)*float(l.get("odds") or 0) for l in ls if l.get("odds")]
        return float(min(v)) if v else np.nan
    d["plan_pay"]  = d["_legs"].map(pay)      # 計画払戻（実売 bet_detail 由来）
    d["plan_paymin"]= d["_legs"].map(paymin)
    d["n_legs"]    = d["_legs"].map(len)
    d["total_bet"] = d["_bd"].map(lambda o: float(o.get("total") or 0) if o else np.nan)
    d["has_band"]  = d["_bd"].map(lambda o: bool(o) and any((l.get("role") or "base")!="base" for l in (o.get("lines") or [])))
    d["paid"]      = d["sold_paid_points"].fillna(0).astype(float)
    d["free"]      = d["sold_points"].fillna(0).astype(float) - d["paid"]
    d["lpaid"]     = np.log1p(d["paid"])
    d["plan"]      = d["rank_key"].fillna("?")
    d["is_hp"]     = d["plan"].str.match(r"^[A-F]_(sign|big)$").fillna(False)
    d["conf"]      = d["is_confident"].astype(str).str.lower().isin(["t","true","1"])
    rt = d["race_type"].fillna("")
    d["final"]   = rt.str.contains("決勝") & ~rt.str.contains("準決")
    d["semi"]    = rt.str.contains("準決")
    d["yosen"]   = rt.str.contains("予選")
    d["tokusen"] = rt.str.contains("特選|選抜|特秀")
    d["seg"] = np.where(d["final"],"final",np.where(d["semi"],"semi",
               np.where(d["yosen"],"yosen",np.where(d["tokusen"],"tokusen","other"))))
    return d

def ols(y, X, names):
    X = np.asarray(X,float); y=np.asarray(y,float)
    b,*_ = np.linalg.lstsq(X,y,rcond=None)
    r = y - X@b
    XtXi = np.linalg.pinv(X.T@X)
    # HC1
    n,k = X.shape
    S = (X*r[:,None]).T@(X*r[:,None])
    V = XtXi@S@XtXi * n/max(n-k,1)
    se = np.sqrt(np.clip(np.diag(V),0,None))
    return {nm:(b[i], b[i]-1.96*se[i], b[i]+1.96*se[i]) for i,nm in enumerate(names)}, b, se

def dmat(df, cols, fe=None):
    X=[np.ones(len(df))]; names=["const"]
    for c in cols:
        X.append(np.asarray(df[c],float)); names.append(c)
    if fe:
        for f in fe:
            vals = sorted(df[f].dropna().unique())[1:]
            for v in vals:
                X.append((df[f]==v).astype(float).values); names.append(f"{f}={v}")
    return np.column_stack(X), names
