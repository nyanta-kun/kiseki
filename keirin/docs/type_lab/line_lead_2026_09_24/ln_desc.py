import pandas as pd, numpy as np
d=pd.read_pickle("LN/d.pkl")
def T(by,minn=150):
    g=d.groupby(by+["win"],observed=True).agg(R=("big","size"),五千円超=("big","mean"),一万円超=("huge","mean"),中央払戻=("res_odds","median"),得点1位3着内=("rp1_in3","mean"),得点12位そろい=("rp12_in3","mean"))
    for c in ["五千円超","一万円超","得点1位3着内","得点12位そろい"]: g[c]=(g[c]*100).round(1)
    g["中央払戻"]=(g.中央払戻*100).round(-1).astype(int)
    u=g.unstack("win"); u=u[(u[("R","探索")]>=minn)&(u[("R","確認")]>=minn*0.4)]
    return u[[("R","探索"),("R","確認"),("五千円超","探索"),("五千円超","確認"),("一万円超","探索"),("一万円超","確認"),("中央払戻","探索"),("中央払戻","確認"),("得点12位そろい","探索"),("得点12位そろい","確認")]]
pd.set_option("display.width",250)
print("全体 五千円超",round(d.big.mean()*100,1),"一万円超",round(d.huge.mean()*100,1))
print("\n== ライン構成 ==");print(T(["comp"]).to_string())
print("\n== 得点1位・2位の位置（同ライン/別ライン）==");print(T(["rp12_same","rp1_pos","rp2_pos"]).to_string())
d["g12"]=pd.cut(d.rp_gap12,[-1,0.5,1.5,3,5,100],labels=["〜0.5","0.5-1.5","1.5-3","3-5","5〜"])
d["g13"]=pd.cut(d.rp_gap13,[-1,1,3,6,10,100],labels=["〜1","1-3","3-6","6-10","10〜"])
print("\n== 得点差 1位-2位 ==");print(T(["g12"]).to_string())
print("\n== 得点差 1位-3位 ==");print(T(["g13"]).to_string())
d.to_pickle("LN/d.pkl")
