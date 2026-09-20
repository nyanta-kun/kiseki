import numpy as np, pandas as pd

def ols(y, X, names):
    X = np.asarray(X, float); y=np.asarray(y,float)
    XtX = X.T@X
    XtXi = np.linalg.pinv(XtX)
    b = XtXi@(X.T@y)
    r = y - X@b
    n,k = X.shape
    # HC1 robust
    S = (X * r[:,None])
    meat = S.T@S * n/(n-k)
    V = XtXi@meat@XtXi
    se = np.sqrt(np.diag(V))
    t = b/np.where(se==0,np.nan,se)
    r2 = 1 - (r@r)/(((y-y.mean())**2).sum())
    return pd.DataFrame({'coef':b,'se':se,'t':t,'lo':b-1.96*se,'hi':b+1.96*se}, index=names), r2, n

def design(df, cols_num=(), cats=(), fe=None, const=True):
    parts=[]; names=[]
    if const:
        parts.append(np.ones((len(df),1))); names.append('const')
    for c in cols_num:
        parts.append(df[[c]].astype(float).values); names.append(c)
    for c,base in cats:
        s=df[c].astype(str)
        levels=[v for v in sorted(s.unique()) if v!=base]
        for v in levels:
            parts.append((s==v).astype(float).values.reshape(-1,1)); names.append(f'{c}={v}')
    if fe is not None:
        s=df[fe].astype(str); levels=sorted(s.unique())[1:]
        for v in levels:
            parts.append((s==v).astype(float).values.reshape(-1,1)); names.append(f'FE_{fe}={v}')
    return np.hstack(parts), names
