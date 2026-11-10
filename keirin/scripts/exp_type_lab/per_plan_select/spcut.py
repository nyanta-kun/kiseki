import pandas as pd, numpy as np
import os
D=os.environ.get('PER_PLAN_DIR','./')
d=pd.read_pickle(D+'live.pkl')
d=d[~((d.plan_key=='C_hit')&(d.race_date>='2026-10-25'))]  # C_hit: 旧構造だけで見る
rng=np.random.default_rng(1)
roi=lambda y:y.ret.sum()/y.budget.sum()
print('plan  | H2 all hit/roi | H2 keep(Σp上位2/3) hit/roi | Δhit CI | Δroi CI | 平均払戻(的中時) all→keep')
for p in ['B_hit','C_hit','D_hit','E_hit','F_hit','A_trio','F_line']:
    x=d[d.plan_key==p]; cut=x[x.half=='H1'].sp.quantile(1/3); y=x[x.half=='H2']; k=y[y.sp>cut]
    bs=[];bh=[]
    for _ in range(3000):
        i=rng.integers(0,len(y),len(y)); yy=y.iloc[i]; kk=yy[yy.sp>cut]
        bs.append(roi(kk)-roi(yy)); bh.append(kk.dhit.mean()-yy.dhit.mean())
    print(f"{p:6s}| {y.dhit.mean():.3f}/{roi(y):.3f} (n={len(y)}) | {k.dhit.mean():.3f}/{roi(k):.3f} (n={len(k)}) | {np.percentile(bh,[2.5,97.5]).round(3)} | {np.percentile(bs,[2.5,97.5]).round(3)} | {y[y.rhit==1].payout.mean():.0f}→{k[k.rhit==1].payout.mean():.0f}")
