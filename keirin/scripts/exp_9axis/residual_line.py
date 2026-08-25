import numpy as np, pandas as pd
D = pd.read_pickle("/tmp/diag9.pkl")
D["res"] = D.top3 - D.p3          # 残差（正 = モデルが過小評価）

def tab(d, keys, lab, minn=300):
    t = d.groupby(keys).agg(n=("res","size"), pred=("p3","mean"), act=("top3","mean"))
    t["残差pt"] = 100*(t.act - t.pred)
    t = t[t.n>=minn].sort_values("残差pt")
    print(f"-- {lab} --"); print(t.round(4)); print()

for nc in (9,7):
    d = D[D.nc==nc].copy()
    print(f"########## {nc}車 ##########")
    tab(d, ["nl"], "ライン数 n_lines")
    tab(d, ["lsize","lpos"], "ライン規模 × 位置")
    tab(d, ["style"], "脚質")

print("########## 9車: ライン構成パターン ##########")
d = D[D.nc==9].copy()
comp = (d.drop_duplicates(["rk","lsize","lpos"]) if False else None)
# レースごとのライン規模の並び
sizes = (D[D.nc==9].groupby(["rk","lsize"]).size().reset_index(name="cnt"))
pat = {}
for rk,g in D[D.nc==9].groupby("rk"):
    # 各ラインの規模を数える（line_pos==1 の車がライン数ぶんある想定）
    s = sorted(g[g.lpos==1].lsize.dropna().astype(int).tolist(), reverse=True)
    pat[rk] = "-".join(map(str,s)) if s else "?"
d["pat"] = d.rk.map(pat)
tab(d, ["pat"], "ライン構成", minn=800)
