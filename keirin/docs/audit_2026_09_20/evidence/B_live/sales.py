import csv, json, os, statistics, collections, math
os.chdir(os.path.dirname(os.path.abspath(__file__)))
csv.field_size_limit(10**9)
S=list(csv.DictReader(open("sales_race.csv", newline="")))
def f(x): return float(x) if x not in ("",None) else 0.0
def spear(a,b):
    def rank(v):
        o=sorted(range(len(v)), key=lambda i:v[i]); r=[0]*len(v); i=0
        while i<len(o):
            j=i
            while j+1<len(o) and v[o[j+1]]==v[o[i]]: j+=1
            m=(i+j)/2+1
            for k in range(i,j+1): r[o[k]]=m
            i=j+1
        return r
    ra,rb=rank(a),rank(b); n=len(a)
    ma,mb=statistics.mean(ra),statistics.mean(rb)
    num=sum((x-ma)*(y-mb) for x,y in zip(ra,rb))
    den=math.sqrt(sum((x-ma)**2 for x in ra)*sum((y-mb)**2 for y in rb))
    return num/den if den else 0
# 日別：その日の的中・高額と、翌日以降の売上
byday=collections.defaultdict(list)
for r in S: byday[r["race_date"]].append(r)
days=sorted(byday)
rows=[]
for d in days:
    R=byday[d]
    stake=sum(f(r["stake_amount"]) for r in R); pay=sum(f(r["payout_amount"]) for r in R)
    hits=sum(f(r["n_hits_excl_garami"]) for r in R)
    big=sum(1 for r in R if f(r["payout_amount"])>=100000)
    sold=sum(f(r["sold_paid_points"]) for r in R)
    nsold=sum(f(r["n_sold"]) for r in R)
    rows.append(dict(d=d,n=len(R),roi=100*pay/stake if stake else 0,net=100*hits/len(R),big=big,sold=sold,nsold=nsold))
print(f"{'日':10s} {'R':>4} {'表示%':>6} {'ROI%':>7} {'10万+':>5} {'有償pt':>8} {'販売数':>6}")
for r in rows: print(f"{r['d']:10s} {r['n']:4d} {r['net']:6.1f} {r['roi']:7.1f} {r['big']:5d} {r['sold']:8.0f} {r['nsold']:6.0f}")
# 同日の相関
print()
for lag in (0,1,2):
    a=[];b=[];c=[];e=[]
    for i in range(len(rows)-lag):
        j=i+lag
        a.append(rows[i]["net"]); b.append(rows[j]["sold"]); c.append(rows[i]["roi"]); e.append(rows[i]["big"])
    print(f"lag={lag}日: 表示的中 vs 有償pt Spearman={spear(a,b):+.3f} / ROI vs 有償pt={spear(c,b):+.3f} / 10万+本数 vs 有償pt={spear(e,b):+.3f}  (n={len(a)})")
# レース単位
a=[f(r["sold_paid_points"]) for r in S]
h=[f(r["n_hits_excl_garami"]) for r in S]
p=[f(r["payout_amount"]) for r in S]
print(f"\nレース単位（同レース）: 有償pt vs 的中 Spearman={spear(h,a):+.3f} / 有償pt vs 払戻={spear(p,a):+.3f}  (n={len(a)})")
