import csv, json, os, collections, random, statistics
os.chdir(os.path.dirname(os.path.abspath(__file__)))
csv.field_size_limit(10**9)
live=list(csv.DictReader(open("tlp_live.csv", newline="")))
live=[r for r in live if "2026-08-29"<=r["race_date"]<="2026-09-19" and r["hit"] not in ("",None)]
rec=json.load(open("recomputed.json"))
soldset={(x["race_key"], x["rank_key"]) for x in rec if not x["deleted"]
         and x["status"] in ("published","submitted") and x["recomp"]["settled"]
         and x["recomp"]["bet"]>0 and x["race_key"][:8]>="20260829"}
SOLDPLANS=collections.Counter(k for _,k in soldset)
print("売ったプラン:", SOLDPLANS.most_common())
def agg(R,label):
    if not R: print(label,"n=0"); return
    b=sum(int(r["budget"]) for r in R); p=sum(int(r["payout"] or 0) for r in R)
    n=sum(1 for r in R if r["hit"]=="t" and int(r["payout"] or 0)>=int(r["budget"]))
    h=sum(1 for r in R if r["hit"]=="t")
    big=sum(1 for r in R if int(r["payout"] or 0)>=100000)
    print(f"{label}: n={len(R)} ROI={100*p/b:.2f}% 的中={100*h/len(R):.2f}% 表示={100*n/len(R):.2f}% 10万+={big}")
agg(live, "① 全 live 候補")
sp=set(SOLDPLANS)
agg([r for r in live if r["plan_key"] in sp], "② 売ったことのあるプランの候補")
agg([r for r in live if (r["race_key"],r["plan_key"]) in soldset], "④ 実際に売った行")
byrace=collections.defaultdict(list)
for r in live: byrace[r["race_key"]].append(r)
soldraces={rk for rk,_ in soldset}
agg([r for rk in soldraces for r in byrace.get(rk,[]) if r["plan_key"] in sp], "③ 売ったレース × 売ったことのあるプラン")
print(f"\n候補のあるレース {len(byrace)} / 売ったレース {len(soldraces)} = カバレッジ {100*len(soldraces)/len(byrace):.1f}%")
# 無作為対照: 売ったレースの中から、そのレースの sellable 候補を1つ無作為に選ぶ
rnd=random.Random(11); rois=[]; nets=[]
for _ in range(2000):
    pick=[]
    for rk in soldraces:
        cand=[r for r in byrace.get(rk,[]) if r["plan_key"] in sp]
        if cand: pick.append(rnd.choice(cand))
    b=sum(int(r["budget"]) for r in pick); p=sum(int(r["payout"] or 0) for r in pick)
    n=sum(1 for r in pick if r["hit"]=="t" and int(r["payout"] or 0)>=int(r["budget"]))
    rois.append(100*p/b); nets.append(100*n/len(pick))
rois.sort(); nets.sort()
print(f"無作為に1商品を選ぶ対照（同じレース集合）: ROI 中央 {statistics.median(rois):.2f}% [{rois[50]:.2f},{rois[-50]:.2f}]  表示 中央 {statistics.median(nets):.2f}% [{nets[50]:.2f},{nets[-50]:.2f}]")
