import csv, json, os, collections
os.chdir(os.path.dirname(os.path.abspath(__file__)))
csv.field_size_limit(10**9)
rec=json.load(open("recomputed.json"))
live=[]
with open("tlp_live.csv", newline="") as f: live=list(csv.DictReader(f))
L={(r["race_key"], r["plan_key"]): r for r in live}
sold=[x for x in rec if not x["deleted"] and x["status"] in ("published","submitted")
      and x["recomp"]["settled"] and x["recomp"]["bet"]>0 and x["race_key"][:8]>="20260829"]
print("採点済み実売 8/29-:", len(sold))
m=0; nomatch=[]; diff_pay=0; diff_n=0
rows=[]
for x in sold:
    r=L.get((x["race_key"], x["rank_key"]))
    if r is None: nomatch.append((x["race_key"], x["rank_key"])); continue
    m+=1
    if r["hit"] in ("",None): continue
    sp=x["recomp"]["payout"]; pp=int(r["payout"] or 0)
    if sp!=pp: diff_pay+=1
    rows.append((x, r))
print("type_lab_picks と突き合わせ:", m, "不一致 payout:", diff_pay, "未突合:", len(nomatch))
print("未突合の rank_key:", collections.Counter(k for _,k in nomatch).most_common(10))
b1=sum(x["recomp"]["bet"] for x,_ in rows); p1=sum(x["recomp"]["payout"] for x,_ in rows)
b2=sum(int(r["budget"]) for _,r in rows); p2=sum(int(r["payout"] or 0) for _,r in rows)
print(f"実売(bet_detail 採点): bet={b1:,} pay={p1:,} ROI={100*p1/b1:.2f}%")
print(f"同じ行の type_lab_picks: bet={b2:,} pay={p2:,} ROI={100*p2/b2:.2f}%")
# 差の大きいもの
d=sorted(rows, key=lambda t: -abs(t[0]["recomp"]["payout"]-int(t[1]["payout"] or 0)))[:15]
for x,r in d:
    print(x["race_key"], x["rank_key"], "sold_pay", x["recomp"]["payout"], "tlp_pay", r["payout"], "n_legs", r["n_legs"], "recomp_n", x["recomp"]["n"], "rv", r["rule_version"])
json.dump([[x["race_key"],x["rank_key"],x["recomp"],{k:r[k] for k in ("plan_key","type_label","n_entries","budget","payout","hit","n_legs","pred_mean_payout","final_odds","rule_version","race_type","day_index")}] for x,r in rows], open("matched.json","w"))
