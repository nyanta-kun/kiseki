import json, os, random, collections
os.chdir(os.path.dirname(os.path.abspath(__file__)))
rec=json.load(open("recomputed.json"))
live=[x for x in rec if not x["deleted"] and x["status"] in ("published","submitted")
      and x["recomp"]["settled"] and x["recomp"]["bet"]>0]
def ci_roi(R,B=3000,seed=5):
    rnd=random.Random(seed); v=[]
    for _ in range(B):
        s=[R[rnd.randrange(len(R))] for _ in range(len(R))]
        b=sum(x["recomp"]["bet"] for x in s); p=sum(x["recomp"]["payout"] for x in s)
        v.append(100*p/b if b else 0)
    v.sort(); return v[int(.025*B)], v[int(.975*B)]
def table(R, title):
    print(f"\n### {title}")
    print(f"{'key':10s} {'n':>5} {'的中%':>7} {'表示%':>7} {'ROI%':>7} {'CI95':>18} {'10万+':>5} {'投資':>11}")
    g=collections.defaultdict(list)
    for x in R: g[x["rank_key"]].append(x)
    for k,v in sorted(g.items(), key=lambda t:-len(t[1])):
        b=sum(y["recomp"]["bet"] for y in v); p=sum(y["recomp"]["payout"] for y in v)
        h=sum(1 for y in v if y["recomp"]["hit"]); n=sum(1 for y in v if y["recomp"]["hit"] and y["recomp"]["payout"]>=y["recomp"]["bet"])
        big=sum(1 for y in v if y["recomp"]["payout"]>=100000)
        lo,hi=ci_roi(v) if len(v)>=8 else (float('nan'),float('nan'))
        print(f"{k:10s} {len(v):5d} {100*h/len(v):7.2f} {100*n/len(v):7.2f} {100*p/b:7.2f} [{lo:7.2f},{hi:7.2f}] {big:5d} {b:11,}")
    b=sum(y["recomp"]["bet"] for y in R); p=sum(y["recomp"]["payout"] for y in R)
    h=sum(1 for y in R if y["recomp"]["hit"]); n=sum(1 for y in R if y["recomp"]["hit"] and y["recomp"]["payout"]>=y["recomp"]["bet"])
    lo,hi=ci_roi(R)
    print(f"{'合計':10s} {len(R):5d} {100*h/len(R):7.2f} {100*n/len(R):7.2f} {100*p/b:7.2f} [{lo:7.2f},{hi:7.2f}] {sum(1 for y in R if y['recomp']['payout']>=100000):5d} {b:11,}")
table([x for x in live if x["race_key"][:8]>="20260829"], "型ラボ期（2026-08-29〜09-19）プラン別 実売")
table([x for x in live if x["race_key"][:8]<"20260829"], "旧ランク期（2026-08-07〜08-28）ランク別 実売")
