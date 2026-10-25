import pickle, numpy as np, sys
from collections import Counter
P = sys.argv[1] if len(sys.argv) > 1 else "data/exp_bet_review/h22/h22_arms.pkl"  # keirin/ で実行
z=pickle.load(open(P,"rb")); arms,cur,races=z["arms"],z["cur"],z["races"]
keys=sorted(arms); N=len(keys)
days=sorted({races[k]["day"] for k in keys}); dpos={d:i for i,d in enumerate(days)}; ND=len(days)
DAY=np.array([dpos[races[k]["day"]] for k in keys])
rng=np.random.default_rng(1)
BW=np.stack([np.bincount(rng.choice(ND,ND),minlength=ND) for _ in range(2000)]).astype(float)
def dm(mask,inv,pay):
    M=np.zeros((ND,2)); np.add.at(M[:,0],DAY[mask],inv[mask]); np.add.at(M[:,1],DAY[mask],pay[mask]); return M
def roi(mask,inv,pay):
    M=dm(mask,inv,pay); t=M.sum(0); b=BW@M; r=b[:,1]/b[:,0]*100
    return t[1]/t[0]*100, np.percentile(r,2.5), np.percentile(r,97.5), int(mask.sum())
def droi(m1,i1,p1,m2,i2,p2):
    A=dm(m1,i1,p1);B=dm(m2,i2,p2); a=BW@A; b=BW@B; d=a[:,1]/a[:,0]*100-b[:,1]/b[:,0]*100
    return A.sum(0)[1]/A.sum(0)[0]*100-B.sum(0)[1]/B.sum(0)[0]*100, np.percentile(d,2.5), np.percentile(d,97.5)
def arr(pk,fk):
    ex=np.zeros(N,bool);g=np.zeros(N,bool);c=np.zeros(N);inv=np.zeros(N);pay=np.zeros(N);n=np.zeros(N,int)
    for j,k in enumerate(keys):
        r=arms[k]["rows"][pk][fk]
        if r: ex[j],g[j],c[j],inv[j],pay[j],n[j]=True,r["gate"],r["comp"],r["inv"],r["pay"],r["n"]
    return ex,g,c,inv,pay,n
pk="Pline"
ex,g,c,inv,pay,n=arr(pk,"F6@3")
base=ex&g; main=base&(c>=3-1e-9); skip=base&~main
print("F6@3 Pline base",base.sum(),"main",main.sum(),"skip",skip.sum())
print("skip n-points dist",Counter(n[skip]).most_common(5), "comp range", c[skip].min(), c[skip].max())
# 1. realized payout of main hits
h=main&(pay>0)
print("main hits",h.sum(),"realized pay<30k frac %.3f  <20k %.3f  <15k %.3f  median %.0f"%((pay[h]<30000).mean(),(pay[h]<20000).mean(),(pay[h]<15000).mean(),np.median(pay[h])))
# realized/predicted for hit leg
# 2. fixed forms within skip vs main races (race property test with fixed bet structure)
for fk in ("F1","F2","F4","F5"):
    e2,g2,c2,i2,p2,n2=arr(pk,fk)
    for nm,m in (("skip",skip),("main",main)):
        mm=m&e2
        print(f"  {fk} on F6-{nm} races (no gate/T): ROI %.2f [%.1f,%.1f] n=%d"%roi(mm,i2,p2))
    print("   diff skip-main", "%.2f [%.2f,%.2f]"%droi(skip&e2,i2,p2,main&e2,i2,p2))
# 3. ctrl n=1 (top prob leg >=2x) ROI by predicted odds band, all races
ci_=np.zeros(N);cp=np.zeros(N);cc=np.zeros(N);ce=np.zeros(N,bool)
for j,k in enumerate(keys):
    r=arms[k]["ctrl"].get(1)
    if r: ce[j]=True;ci_[j]=r["inv"];cp[j]=r["pay"];cc[j]=r["comp"]
print("single top-prob leg (>=2x) ROI by predicted odds band:")
for lo,hi in ((2,3),(3,4),(4,6),(6,10),(10,1e9)):
    m=ce&(cc>=lo)&(cc<hi)
    print("  [%g,%g): ROI %.2f [%.1f,%.1f] n=%d"%((lo,hi)+roi(m,ci_,cp)))
    for nm,mm in (("in skip",m&skip),("not skip",m&~skip)):
        if mm.sum()>50: print("     %s ROI %.2f [%.1f,%.1f] n=%d"%((nm,)+roi(mm,ci_,cp)))
# 4. vs ①
A1b=np.zeros(N,bool);A1i=np.zeros(N);A1p=np.zeros(N)
for j,k in enumerate(keys):
    r=cur.get(k)
    if r: A1b[j]=True;A1i[j]=r["inv"];A1p[j]=r["pay"]
print("② on races ① sells: %.2f [%.1f,%.1f] n=%d"%roi(main&A1b,inv,pay))
print("② on races ① doesn't sell: %.2f [%.1f,%.1f] n=%d"%roi(main&~A1b,inv,pay))
print("① on races ② doesn't buy: %.2f [%.1f,%.1f] n=%d"%roi(A1b&~main,A1i,A1p))
print("① on races ② buys: %.2f [%.1f,%.1f] n=%d"%roi(A1b&main,A1i,A1p))
print("① races that ② skipped(T): %d ; ① ROI there %.2f"%((A1b&skip).sum(), roi(A1b&skip,A1i,A1p)[0]))
# ① main vs highpay
sl=np.array([cur[k]["slot"] if k in cur else "" for k in keys])
for s in ("main","highpay"):
    m=A1b&(sl==s); print(" ① slot",s,"ROI %.2f [%.1f,%.1f] n=%d"%roi(m,A1i,A1p), " paired with ②:",(m&main).sum())
mm=A1b&(sl=="main")&main
print(" pair excluding ① highpay: ② %.2f  ① %.2f  diff %.2f [%.2f,%.2f]"%(roi(mm,inv,pay)[0],roi(mm,A1i,A1p)[0],*droi(mm,inv,pay,mm,A1i,A1p)))
print("\n=== fixed forms: composite-based skip (T=3万/5万) among gate-passing ===")
for pk in ("Pp3","Pline"):
  for fk in ("F1","F2","F4","F5"):
    e2,g2,c2,i2,p2,n2=arr(pk,fk)
    b=e2&g2
    for T in (3.0,5.0):
        k=b&(c2>=T-1e-9); s=b&~k
        rk=roi(k,i2,p2); rs=roi(s,i2,p2); d=droi(k,i2,p2,s,i2,p2)
        print(f" {pk} {fk} T={T:.0f}: base {b.sum()} skip {s.sum()} ({s.sum()/b.sum()*100:.0f}pct)  kept %.2f  skipped %.2f  kept-skipped %.2f [%.2f,%.2f]"%(rk[0],rs[0],*d))
# realized payout fractions ①
h1=A1b&(A1p>0)
print("\n① hits",h1.sum(),"pay<30k %.3f <20k %.3f median %.0f"%((A1p[h1]<30000).mean(),(A1p[h1]<20000).mean(),np.median(A1p[h1])))
# per-day count of hits >=30k
print("②(F6 Pline 3万) hits>=30k per day %.2f ; ① %.2f"%(((main&(pay>=30000)).sum())/ND, ((A1b&(A1p>=30000)).sum())/ND))
# realized/predicted for winning leg in ② main hits
rat=[]
for j in np.flatnonzero(h):
    k=keys[j]; st=arms[k]["rows"][pk]["F6@3"]["stakes"]; w=arms[k]["info"]["win_tf"]
    pr=10000/ (st[w]) # not predicted odds; skip
print("half split skip-vs-kept F6:")
H1=np.array([int(races[k]["day"][5:7])<=6 for k in keys])
for nm,hm in (("上期",H1),("下期",~H1)):
    print("  ",nm,"skip %.2f [%.1f,%.1f] n=%d"%roi(skip&hm,inv,pay)," kept %.2f"%roi(main&hm,inv,pay)[0])
