import numpy as np, pandas as pd
D = pd.read_pickle("/tmp/diag9.pkl")
d9 = D[D.nc==9].copy()
G = d9.top3.mean() - d9.p3.mean()      # 全体バイアス
print(f"9車 全体バイアス {G*100:+.2f}pt（これを引いた差分で読む）\n")

print("=== 9車: ライン数 × 役割 の相対残差（全体バイアス控除後）===")
d9["role"] = np.where(d9.lsize==1, "単騎",
             np.where(d9.lpos==1, "ライン先頭(先行)",
             np.where(d9.lpos==2, "番手", "3番手以降")))
t = d9.groupby(["nl","role"]).agg(n=("top3","size"), pred=("p3","mean"), act=("top3","mean"))
t["相対残差pt"] = 100*((t.act-t.pred) - G)
print(t[t.n>=250].round(4))

print("\n=== 参考: 7車 同表 ===")
d7 = D[D.nc==7].copy(); G7 = d7.top3.mean()-d7.p3.mean()
d7["role"] = np.where(d7.lsize==1,"単騎", np.where(d7.lpos==1,"ライン先頭(先行)",
             np.where(d7.lpos==2,"番手","3番手以降")))
t7 = d7.groupby(["nl","role"]).agg(n=("top3","size"), pred=("p3","mean"), act=("top3","mean"))
t7["相対残差pt"] = 100*((t7.act-t7.pred)-G7)
print(t7[t7.n>=2000].round(4))
