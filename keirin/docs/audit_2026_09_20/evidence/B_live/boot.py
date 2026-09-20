import json, random, collections, math, os, sys
os.chdir(os.path.dirname(os.path.abspath(__file__)))
rec = json.load(open("recomputed.json"))
live=[x for x in rec if not x["deleted"] and x["status"] in ("published","submitted")
      and x["recomp"]["settled"] and x["recomp"]["bet"]>0]
def stats(R):
    b=sum(x["recomp"]["bet"] for x in R); p=sum(x["recomp"]["payout"] for x in R)
    h=sum(1 for x in R if x["recomp"]["hit"])
    n=sum(1 for x in R if x["recomp"]["hit"] and x["recomp"]["payout"]>=x["recomp"]["bet"])
    big=sum(1 for x in R if x["recomp"]["payout"]>=100000)
    return dict(n=len(R), roi=100*p/b if b else 0, hit=100*h/len(R) if R else 0,
                net=100*n/len(R) if R else 0, big=big, bet=b, pay=p)
def boot(R, key, B=4000, seed=7):
    rnd=random.Random(seed)
    if key=="race":
        units=[[x] for x in R]
    else:
        g=collections.defaultdict(list)
        for x in R: g[x["race_key"][:8]].append(x)
        units=list(g.values())
    rois=[]; nets=[]; hits=[]
    N=len(units)
    for _ in range(B):
        s=[units[rnd.randrange(N)] for _ in range(N)]
        flat=[x for u in s for x in u]
        st=stats(flat); rois.append(st["roi"]); nets.append(st["net"]); hits.append(st["hit"])
    def ci(v): v=sorted(v); return (v[int(.025*len(v))], v[int(.975*len(v))])
    return ci(rois), ci(nets), ci(hits)
for label, R in [("通算(8/1-9/19)", live),
                 ("2026-08", [x for x in live if x["race_key"][:6]=="202608"]),
                 ("2026-09", [x for x in live if x["race_key"][:6]=="202609"]),
                 ("型ラボ期(8/29-)", [x for x in live if x["race_key"][:8]>="20260829"]),
                 ("旧ランク期(-8/28)", [x for x in live if x["race_key"][:8]<"20260829"])]:
    st=stats(R)
    cr,cn,ch=boot(R,"race"); dr,dn,dh=boot(R,"day")
    print(f"{label}: n={st['n']} bet={st['bet']:,} pay={st['pay']:,}")
    print(f"   ROI {st['roi']:.2f}%  race-CI[{cr[0]:.2f},{cr[1]:.2f}]  day-CI[{dr[0]:.2f},{dr[1]:.2f}]")
    print(f"   的中 {st['hit']:.2f}%  表示的中 {st['net']:.2f}% race-CI[{cn[0]:.2f},{cn[1]:.2f}] day-CI[{dn[0]:.2f},{dn[1]:.2f}]  10万+={st['big']}")
