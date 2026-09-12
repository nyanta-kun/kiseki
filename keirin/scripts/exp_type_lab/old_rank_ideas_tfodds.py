import os, pickle, psycopg2
from collections import defaultdict
con=psycopg2.connect(os.environ["KEIRIN_DB_URL"]); cur=con.cursor()
cur.execute("""SELECT r.race_key, e.frame_no, e.finish_order
               FROM keirin.wt_races r JOIN keirin.wt_entries e ON e.race_key=r.race_key
               WHERE r.n_entries=7 AND r.cancel=0
                 AND r.race_date BETWEEN '2024-07-01' AND '2026-08-31'
                 AND e.finish_order BETWEEN 1 AND 3""")
fin=defaultdict(list)
for rk,fn,fo in cur.fetchall(): fin[rk].append((int(fo),int(fn)))
combo={rk:"-".join(str(f) for _,f in sorted(v)) for rk,v in fin.items() if len(v)==3}
print("決着あり", len(combo), flush=True)
keys=sorted(combo); out={}
for i in range(0,len(keys),5000):
    ch=keys[i:i+5000]
    cur.execute("""SELECT o.race_key, o.odds_value FROM keirin.wt_odds o
                   JOIN unnest(%s::text[], %s::text[]) AS t(rk, cb)
                     ON o.race_key=t.rk AND o.combination=t.cb
                   WHERE o.bet_type='trifecta'""", (ch, [combo[k] for k in ch]))
    for rk,od in cur.fetchall():
        try: out[rk]=float(od)
        except Exception: pass
    print(f"  {min(i+5000,len(keys))}/{len(keys)}  取得{len(out)}", flush=True)
print("確定三連単オッズ", len(out), f"{len(out)/len(combo)*100:.1f}%")
pickle.dump(out, open("/tmp/tf_odds_win.pkl","wb"))
