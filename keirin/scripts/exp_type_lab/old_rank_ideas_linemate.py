"""7H1 の前提「本命が飛ぶと番手も飛ぶ」を、母集団を 7H1 と揃えて測り直す。"""
import os, sys, psycopg2
from collections import defaultdict
sys.path.insert(0,'/Users/ysuzuki/GitHub/kiseki/keirin')
from src.preprocessing.favbust_features import roles_of, ROLE_FAV_MATE, ROLE_FAV_THIRD
con=psycopg2.connect(os.environ["KEIRIN_DB_URL"]); cur=con.cursor()
cur.execute("""SELECT e.race_key, e.frame_no, e.pred_top3_pct, e.pred_win_pct,
                      e.line_group, e.line_pos, e.line_size, e.is_line_leader,
                      e.prediction_mark, e.finish_order, r.race_date
               FROM keirin.wt_entries e JOIN keirin.wt_races r ON r.race_key=e.race_key
               WHERE r.n_entries=7 AND r.cancel=0
                 AND r.race_date BETWEEN '2024-07-01' AND '2026-08-31'""")
E=defaultdict(list); D={}
for rk,fn,p3,pwv,lg,lp,ls,ill,mk,fo,d in cur.fetchall():
    E[rk].append(dict(frame_no=int(fn), p3=float(p3 or 0), pw=float(pwv or 0),
                      line_group=lg, line_pos=lp, line_size=ls, is_line_leader=ill,
                      prediction_mark=mk, fo=fo)); D[rk]=str(d)
res=defaultdict(lambda: defaultdict(lambda: [0,0]))
n=defaultdict(lambda:[0,0,0])
for rk,es in E.items():
    if len(es)!=7: continue
    top3={e["frame_no"] for e in es if e["fo"] in (1,2,3)}
    if len(top3)!=3: continue
    w="探索" if D[rk]<"2026-01-01" else "確認"
    fav=max(es,key=lambda e:e["pw"])["frame_no"]
    honmei=next((e["frame_no"] for e in es if e["prediction_mark"]==1), None)
    n[w][0]+=1
    if fav!=honmei: continue                      # 7H1 母集団外
    n[w][1]+=1
    pws=sorted((e["pw"] for e in es), reverse=True)
    if (pws[0]-pws[1])/100 < 0.20: continue       # 抜け度ゲート
    n[w][2]+=1
    if fav in top3: continue                      # バストしたレースだけ
    roles=roles_of(es, fav)
    for c,r in roles.items():
        res[w][r][0]+=1
        if c in top3: res[w][r][1]+=1
for w in ("探索","確認"):
    print(f"\n[{w}] 全{n[w][0]:,}R → 軸1==WT◎ {n[w][1]:,} → 抜け度>=0.20 {n[w][2]:,}")
    tot=sum(v[0] for v in res[w].values()); hit=sum(v[1] for v in res[w].values())
    print(f"  バストしたレースの役割別 3着内率（一様なら 3/6 = 50.0%）")
    for r,(a,b) in sorted(res[w].items(), key=lambda kv:-kv[1][1]/max(kv[1][0],1)):
        print(f"    {r:16s} n={a:5d}  3着内 {b/max(a,1)*100:5.2f}%")
