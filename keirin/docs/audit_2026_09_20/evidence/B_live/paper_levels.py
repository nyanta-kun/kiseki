import csv, os, collections, random, statistics
os.chdir(os.path.dirname(os.path.abspath(__file__)))
csv.field_size_limit(10**9)
P=[r for r in csv.DictReader(open("tlp_paper.csv", newline="")) if r["hit"] not in ("",None)]
SOLD={'C_hit','F_hit','B_hit','A_hit','E_hit','F_sign','D_hit','A_ana','F_pay','A_trio','F_line','C_sign','B_sign','D_sign'}
def agg(R,label):
    if not R: print(label,"n=0"); return
    b=sum(int(r["budget"]) for r in R); p=sum(int(r["payout"] or 0) for r in R)
    n=sum(1 for r in R if r["hit"]=="t" and int(r["payout"] or 0)>=int(r["budget"]))
    h=sum(1 for r in R if r["hit"]=="t")
    print(f"{label}: n={len(R)} ROI={100*p/b:.2f}% 的中={100*h/len(R):.2f}% 表示={100*n/len(R):.2f}%")
for lo,hi,tag in [("2025-01-01","2025-12-31","2025"),("2026-01-01","2026-06-30","2026H1"),("2026-07-01","2026-08-26","2026-07/08")]:
    R=[r for r in P if lo<=r["race_date"]<=hi]
    agg(R, f"paper {tag} 全候補")
    agg([r for r in R if r["plan_key"] in SOLD], f"paper {tag} 売るプランのみ")
    byrace=collections.defaultdict(list)
    for r in R:
        if r["plan_key"] in SOLD: byrace[r["race_key"]].append(r)
    rnd=random.Random(3); rois=[]; nets=[]
    for _ in range(300):
        pick=[rnd.choice(v) for v in byrace.values()]
        b=sum(int(r["budget"]) for r in pick); p=sum(int(r["payout"] or 0) for r in pick)
        n=sum(1 for r in pick if r["hit"]=="t" and int(r["payout"] or 0)>=int(r["budget"]))
        rois.append(100*p/b); nets.append(100*n/len(pick))
    rois.sort(); nets.sort()
    print(f"  → 1レース1商品を無作為に: ROI 中央 {statistics.median(rois):.2f}% [{rois[7]:.2f},{rois[-8]:.2f}] 表示 {statistics.median(nets):.2f}%  (レース {len(byrace)})")
    print()
