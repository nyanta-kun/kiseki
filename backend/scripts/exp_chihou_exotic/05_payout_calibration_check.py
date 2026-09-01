"""的中組の市場含意価格(Harville/PL)と実払戻の対応 = 予測払戻モデルの素地"""
import pandas as pd, numpy as np, itertools
SP="/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki-wt-chihou-upset-model/b8d3a508-4958-4d4b-a9f6-b7e18425c649/scratchpad/"
race = pd.read_pickle(SP+"race.pkl")
ru = pd.read_pickle(SP+"runners.pkl").dropna(subset=["win_odds"])
ru["win_odds"]=ru.win_odds.astype(float); ru=ru[ru.win_odds>0]
ru["q"]=(1/ru.win_odds)/ (1/ru.win_odds).groupby(ru.race_id).transform("sum")
qm = {rid: dict(zip(g.horse_number, g.q)) for rid,g in ru.groupby("race_id")}
fin = ru[ru.finish_position.isin([1,2,3])].sort_values(["race_id","finish_position"])
top = fin.groupby("race_id")["horse_number"].apply(list)
rows=[]
for rid, order in top.items():
    if len(order)!=3 or rid not in qm: continue
    d=qm[rid]
    try: a,b,c=[d[h] for h in order]
    except KeyError: continue
    pl = a*(b/(1-a))*(c/(1-a-b))
    tri_q = 0.0
    for x,y,z in itertools.permutations([a,b,c]):
        tri_q += x*(y/(1-x))*(z/(1-x-y))
    rows.append((rid, pl, tri_q))
w=pd.DataFrame(rows, columns=["race_id","q_tri_ord","q_trio"]).set_index("race_id")
d=race.join(w, how="inner").dropna(subset=["q_trio","trio","tri"])
d["fair_trio"]=0.745/d.q_trio; d["fair_tri"]=0.745/d.q_tri_ord
d["r_trio"]=(d.trio/100)/d.fair_trio; d["r_tri"]=(d.tri/100)/d.fair_tri
print("n =", len(d))
print("\n=== 的中組: 実払戻 / 市場含意フェア価格 の比 (中央値) ===")
d["qb"]=pd.cut(d.q_trio,[0,1e-3,3e-3,1e-2,3e-2,1e-1,1])
t=d.groupby("qb", observed=True).agg(n=("trio","size"), 実trio中央=("trio","median"),
      含意trio中央=("fair_trio", lambda s: (s*100).median()), 比trio=("r_trio","median"),
      比trifecta=("r_tri","median"))
print(t.round(3).to_string())
print("\n対数相関 (log実払戻 vs log含意価格): trio %.3f / trifecta %.3f" % (
   np.corrcoef(np.log(d.trio), np.log(d.fair_trio))[0,1],
   np.corrcoef(np.log(d.tri), np.log(d.fair_tri))[0,1]))
print("logMAE(底10) trio %.3f  trifecta %.3f" % (
   np.abs(np.log10(d.trio/100/d.fair_trio)).mean(), np.abs(np.log10(d.tri/100/d.fair_tri)).mean()))
print("±2倍以内 trio %.1f%%  trifecta %.1f%%" % (
   (((d.trio/100/d.fair_trio)>0.5)&((d.trio/100/d.fair_trio)<2)).mean()*100,
   (((d.tri/100/d.fair_tri)>0.5)&((d.tri/100/d.fair_tri)<2)).mean()*100))
print("\n=== 構造別の比(中央値) ===")
d["ent_q"]=pd.qcut(d.ent_norm,4,labels=["Q1堅い","Q2","Q3","Q4荒れ"])
print(d.groupby("ent_q",observed=True).agg(n=("trio","size"),比trio=("r_trio","median"),比tri=("r_tri","median")).round(3).to_string())
print(d.groupby(d.head_count.clip(6,14),observed=True).agg(n=("trio","size"),比trio=("r_trio","median")).round(3).to_string())
d.to_pickle(SP+"d.pkl")
