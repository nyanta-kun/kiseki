import csv, json, os, random, collections, statistics
os.chdir(os.path.dirname(os.path.abspath(__file__)))
csv.field_size_limit(10**9)
live=[r for r in csv.DictReader(open("tlp_live.csv", newline="")) if "2026-08-29"<=r["race_date"]<="2026-09-19" and r["hit"] not in ("",None)]
rec=json.load(open("recomputed.json"))
sold={(x["race_key"],x["rank_key"]) for x in rec if not x["deleted"] and x["status"] in ("published","submitted")
      and x["recomp"]["settled"] and x["recomp"]["bet"]>0 and x["race_key"][:8]>="20260829"}
SP={k for _,k in sold}; SR={rk for rk,_ in sold}
def agg(R,label):
    b=sum(int(r["budget"]) for r in R); p=sum(int(r["payout"] or 0) for r in R)
    n=sum(1 for r in R if r["hit"]=="t" and int(r["payout"] or 0)>=int(r["budget"]))
    print(f"{label}: n={len(R)} ROI={100*p/b:.2f}% 表示={100*n/len(R):.2f}%")
agg([r for r in live if r["plan_key"] in SP and r["race_key"] in SR], "売ったレースの売れる候補")
agg([r for r in live if r["plan_key"] in SP and r["race_key"] not in SR], "売らなかったレースの売れる候補")
# 自信あり
subs=list(csv.DictReader(open("subs.csv", newline="")))
conf={(s["race_key"],s["rank_key"]): s["is_confident"]=="t" for s in subs}
R=[x for x in rec if not x["deleted"] and x["status"] in ("published","submitted") and x["recomp"]["settled"] and x["recomp"]["bet"]>0]
for tag,f in [("自信あり",True),("自信なし",False)]:
    V=[x for x in R if conf.get((x["race_key"],x["rank_key"]),False)==f]
    b=sum(x["recomp"]["bet"] for x in V); p=sum(x["recomp"]["payout"] for x in V)
    n=sum(1 for x in V if x["recomp"]["hit"] and x["recomp"]["payout"]>=x["recomp"]["bet"])
    print(f"{tag}: n={len(V)} ROI={100*p/b:.2f}% 表示={100*n/len(V):.2f}%")
# 片側検定
def roi(R): 
    b=sum(x["recomp"]["bet"] for x in R); p=sum(x["recomp"]["payout"] for x in R); return 100*p/b
rnd=random.Random(2); v=[]
for _ in range(8000):
    s=[R[rnd.randrange(len(R))] for _ in range(len(R))]; v.append(roi(s))
for t in (75,78,80,82,85):
    print(f"P(bootstrap ROI >= {t}%) = {sum(1 for x in v if x>=t)/len(v):.4f}")
print("実測 ROI", f"{roi(R):.2f}%")
