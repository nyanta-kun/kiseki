"""単勝オッズ→PL価格 の較正で「予測払戻モデル」を作れるか（keirin odds_tf の代替）"""
import pandas as pd, numpy as np
SP="/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki-wt-chihou-upset-model/b8d3a508-4958-4d4b-a9f6-b7e18425c649/scratchpad/"
d=pd.read_pickle(SP+"d.pkl").dropna(subset=["q_trio","q_tri_ord","trio","tri"])
d=d[(d.q_trio>0)&(d.q_tri_ord>0)]
d["yr"]=d.date.str[:4]
tr=d[d.yr.isin(["2024","2025"])]; te=d[d.yr=="2026"]
for name,qcol,paycol in [("三連複","q_trio","trio"),("三連単","q_tri_ord","tri")]:
    X=np.log10(1/tr[qcol]); y=np.log10(tr[paycol]/100)
    # 2次多項式で較正
    co=np.polyfit(X,y,2)
    for lbl,dd in [("学習(24-25)",tr),("検証(2026)",te)]:
        xx=np.log10(1/dd[qcol]); yy=np.log10(dd[paycol]/100)
        pred=np.polyval(co,xx)
        raw=np.log10(0.745/dd[qcol]) if name=="三連複" else np.log10(0.72/dd[qcol])
        print(f"{name} {lbl}: n={len(dd)} 較正後 logMAE={np.abs(yy-pred).mean():.3f} "
              f"±2倍以内={((np.abs(yy-pred)<np.log10(2)).mean()*100):.1f}%  |  "
              f"素の含意価格 logMAE={np.abs(yy-raw).mean():.3f} ±2倍={(np.abs(yy-raw)<np.log10(2)).mean()*100:.1f}%")
    print("  係数(log10空間 2次):", np.round(co,4))
