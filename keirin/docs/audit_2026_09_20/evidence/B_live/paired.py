import csv, json, os, random, collections, statistics
os.chdir(os.path.dirname(os.path.abspath(__file__)))
csv.field_size_limit(10**9)
live=[r for r in csv.DictReader(open("tlp_live.csv", newline="")) if "2026-08-29"<=r["race_date"]<="2026-09-19" and r["hit"] not in ("",None)]
rec=json.load(open("recomputed.json"))
sold={(x["race_key"],x["rank_key"]) for x in rec if not x["deleted"] and x["status"] in ("published","submitted")
      and x["recomp"]["settled"] and x["recomp"]["bet"]>0 and x["race_key"][:8]>="20260829"}
SOLDPLANS={k for _,k in sold}
byrace=collections.defaultdict(list)
for r in live:
    if r["plan_key"] in SOLDPLANS: byrace[r["race_key"]].append(r)
pairs=[]
for rk,kk in sold:
    cand=byrace.get(rk,[])
    chosen=[r for r in cand if r["plan_key"]==kk]
    if not chosen or len(cand)<2: continue
    c=chosen[0]
    alt=[r for r in cand]
    pairs.append((int(c["payout"] or 0), int(c["budget"]),
                  statistics.mean(int(r["payout"] or 0) for r in alt),
                  statistics.mean(int(r["budget"]) for r in alt),
                  1 if (c["hit"]=="t" and int(c["payout"] or 0)>=int(c["budget"])) else 0,
                  statistics.mean(1 if (r["hit"]=="t" and int(r["payout"] or 0)>=int(r["budget"])) else 0 for r in alt),
                  len(alt), rk))
print("対象レース（候補2つ以上）:", len(pairs))
def stat(S):
    cp=sum(x[0] for x in S); cb=sum(x[1] for x in S)
    ap=sum(x[2] for x in S); ab=sum(x[3] for x in S)
    cn=sum(x[4] for x in S)/len(S); an=sum(x[5] for x in S)/len(S)
    return 100*cp/cb, 100*ap/ab, 100*cn, 100*an
c0,a0,n0,m0=stat(pairs)
print(f"選んだ商品: ROI {c0:.2f}% 表示 {n0:.2f}%")
print(f"同レースの売れる候補平均: ROI {a0:.2f}% 表示 {m0:.2f}%")
rnd=random.Random(1); dr=[]; dn=[]
for _ in range(4000):
    S=[pairs[rnd.randrange(len(pairs))] for _ in range(len(pairs))]
    c,a,n,m=stat(S); dr.append(c-a); dn.append(n-m)
dr.sort(); dn.sort()
print(f"Δ ROI (選択 − 候補平均) = {c0-a0:+.2f}pt  CI95 [{dr[100]:+.2f},{dr[-101]:+.2f}]")
print(f"Δ 表示的中 = {n0-m0:+.2f}pt  CI95 [{dn[100]:+.2f},{dn[-101]:+.2f}]")
print("1レースあたり候補数 中央:", statistics.median(x[6] for x in pairs))
