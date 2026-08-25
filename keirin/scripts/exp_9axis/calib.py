import numpy as np, pandas as pd
D = pd.read_pickle("/tmp/diag9.pkl")
pd.set_option("display.width", 200)

print("=== ① レース内 Σp3（定義上 3.00）===")
s = D.groupby(["nc","rk"]).p3.sum().reset_index()
print(s.groupby("nc").p3.describe()[["count","mean","50%"]])

print("\n=== ② 較正: 予測 p3 平均 vs 実測 3着内率（車数別）===")
for nc in (7,9):
    d = D[D.nc==nc]
    print(f"  {nc}車  pred {d.p3.mean():.4f}  actual {d.top3.mean():.4f}  乖離 {100*(d.p3.mean()-d.top3.mean()):+.2f}pt")

print("\n=== ③ ライン内位置別の較正（9車 vs 7車）===")
for nc in (7,9):
    d = D[D.nc==nc]
    t = d.groupby("lpos").agg(n=("top3","size"), pred=("p3","mean"), act=("top3","mean"),
                              win_pred=("pw","mean"), win_act=("win","mean"))
    t["p3乖離pt"] = 100*(t.pred-t.act); t["win乖離pt"]=100*(t.win_pred-t.win_act)
    print(f"-- {nc}車 --"); print(t.round(4).head(6))

print("\n=== ④ ライン先頭（＝先行役）の勝率・3着内率 ===")
for nc in (7,9):
    d = D[(D.nc==nc)]
    for lead in (1,0):
        x = d[d.leader==lead]
        if not len(x): continue
        print(f"  {nc}車 leader={lead}: n={len(x):6d} pred_win {x.pw.mean():.4f} act_win {x.win.mean():.4f}"
              f"  pred_p3 {x.p3.mean():.4f} act_p3 {x.top3.mean():.4f}")

print("\n=== ⑤ p3順位別の較正（9車）===")
for nc in (7,9):
    d = D[D.nc==nc]
    t = d.groupby("p3rank").agg(n=("top3","size"), pred=("p3","mean"), act=("top3","mean"))
    t["乖離pt"]=100*(t.pred-t.act)
    print(f"-- {nc}車 --"); print(t.round(4))
