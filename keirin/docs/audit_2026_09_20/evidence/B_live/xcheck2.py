import csv, json, os, collections
os.chdir(os.path.dirname(os.path.abspath(__file__)))
csv.field_size_limit(10**9)
rec = json.load(open("recomputed.json"))
sales = {}
with open("sales_race.csv", newline="") as f:
    for r in csv.DictReader(f): sales[r["race_key"]] = r
live = {x["race_key"]: x for x in rec if not x["deleted"] and x["status"] in ("published","submitted")}

rows=[]
for rk, x in live.items():
    s = sales.get(rk)
    if s is None: continue
    if not (x["recomp"]["settled"] and x["recomp"]["bet"]>0): continue
    rows.append(dict(rk=rk, ym=rk[:6], plan=x["rank_key"],
        ob=x["recomp"]["bet"], op=x["recomp"]["payout"], oh=x["recomp"]["hit"],
        sb=int(s["stake_amount"] or 0), sp=int(s["payout_amount"] or 0),
        sh=int(s["n_hits_incl_garami"] or 0), snet=int(s["n_hits_excl_garami"] or 0),
        npred=int(s["n_predictions"] or 0)))
print("採点済み かつ sales あり:", len(rows))
for ym in sorted({r["ym"] for r in rows}):
    R=[r for r in rows if r["ym"]==ym]
    ob=sum(r["ob"] for r in R); op=sum(r["op"] for r in R)
    sb=sum(r["sb"] for r in R); sp=sum(r["sp"] for r in R)
    oh=sum(r["oh"] for r in R); sh=sum(r["sh"] for r in R)
    onet=sum(1 for r in R if r["oh"] and r["op"]>=r["ob"]); snet=sum(r["snet"] for r in R)
    print(f"{ym} n={len(R)}  自前 bet={ob:,} pay={op:,} ROI={100*op/ob:.2f}% hit={oh}({100*oh/len(R):.2f}%) net={onet}({100*onet/len(R):.2f}%)")
    print(f"        netkeirin bet={sb:,} pay={sp:,} ROI={100*sp/sb:.2f}% hit={sh}({100*sh/len(R):.2f}%) net={snet}({100*snet/len(R):.2f}%)")
# 差の内訳
ds = collections.Counter()
for r in rows:
    if r["sb"] != r["ob"]: ds[(r["ym"], "stake")] += 1
    if r["sp"] != r["op"]: ds[(r["ym"], "payout")] += 1
    if r["oh"] != (r["sh"]>0): ds[(r["ym"], "hit")] += 1
print(ds)
# 日別の差
bad = [r for r in rows if r["sp"]!=r["op"] or r["sb"]!=r["ob"]]
c = collections.Counter(r["rk"][:8] for r in bad)
print("不一致の日別:", sorted(c.items())[:40])
json.dump(rows, open("xrows.json","w"))
