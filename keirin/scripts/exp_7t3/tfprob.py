"""三連単の買い目確率: 位置別合成 Plackett-Luce（1着=pw / 2着=中間 / 3着=p3）。"""
import itertools, numpy as np

def blend_pl(cars, pw, p3, w=(1.0, 0.5, 0.0)):
    a = np.array([max(pw[c],1e-9) for c in cars], float); a/=a.sum()
    b = np.array([max(p3[c],1e-9) for c in cars], float); b/=b.sum()
    S = [a**wi * b**(1-wi) for wi in w]
    S = [s/s.sum() for s in S]
    idx = {c:i for i,c in enumerate(cars)}
    out={}
    for x,y,z in itertools.permutations(cars,3):
        ix,iy,iz = idx[x],idx[y],idx[z]
        d1 = S[1].sum()-S[1][ix]
        d2 = S[2].sum()-S[2][ix]-S[2][iy]
        if d1<=0 or d2<=0: continue
        out[(x,y,z)] = S[0][ix]*(S[1][iy]/d1)*(S[2][iz]/d2)
    t=sum(out.values())
    return {k:v/t for k,v in out.items()}
