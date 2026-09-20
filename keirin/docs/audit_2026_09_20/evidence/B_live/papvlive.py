import csv, collections, json, os, random
os.chdir(os.path.dirname(os.path.abspath(__file__)))
csv.field_size_limit(10**9)
def rd(p):
    with open(p, newline="") as f: return list(csv.DictReader(f))
live=rd("tlp_live.csv"); paper=rd("tlp_paper.csv")
def num(x): return None if x in ("",None) else float(x)
def st(R, label):
    R=[r for r in R if r["settled_at"] not in ("",None)] if "settled_at" in (R[0] if R else {}) else R
    R=[r for r in R if r["hit"] not in ("",None)]
    b=sum(int(r["budget"]) for r in R); p=sum(int(r["payout"] or 0) for r in R)
    h=sum(1 for r in R if r["hit"]=="t"); n=sum(1 for r in R if r["hit"]=="t" and int(r["payout"] or 0)>=int(r["budget"]))
    print(f"{label}: n={len(R)} bet={b:,} pay={p:,} ROI={100*p/b if b else 0:.2f}% 的中={100*h/len(R) if R else 0:.2f}% 表示={100*n/len(R) if R else 0:.2f}%")
    return R
print("=== live 候補（type_lab_picks, mode=live/live9） 期間別 ===")
for d0,d1 in [("2026-08-27","2026-08-28"),("2026-08-29","2026-09-19")]:
    st([r for r in live if d0<=r["race_date"]<=d1], f"live {d0}〜{d1}")
print()
print("=== paper（同じプラン集合） ===")
for d0,d1 in [("2025-01-01","2025-12-31"),("2026-01-01","2026-08-26")]:
    st([r for r in paper if d0<=r["race_date"]<=d1], f"paper {d0}〜{d1}")
print()
print("=== プラン別 live 候補 (8/29-) ===")
L=[r for r in live if r["race_date"]>="2026-08-29" and r["hit"] not in ("",None)]
P=[r for r in paper if r["hit"] not in ("",None)]
plans=sorted({r["plan_key"] for r in L})
print(f"{'plan':10s} {'live n':>7} {'ROI':>7} {'表示':>7} | {'paper n':>8} {'ROI':>7} {'表示':>7}")
for pk in plans:
    a=[r for r in L if r["plan_key"]==pk]; b=[r for r in P if r["plan_key"]==pk]
    def f(R):
        if not R: return (0,0,0)
        bb=sum(int(r["budget"]) for r in R); pp=sum(int(r["payout"] or 0) for r in R)
        nn=sum(1 for r in R if r["hit"]=="t" and int(r["payout"] or 0)>=int(r["budget"]))
        return (len(R), 100*pp/bb, 100*nn/len(R))
    x=f(a); y=f(b)
    print(f"{pk:10s} {x[0]:7d} {x[1]:7.2f} {x[2]:7.2f} | {y[0]:8d} {y[1]:7.2f} {y[2]:7.2f}")
