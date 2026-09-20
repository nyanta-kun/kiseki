import csv, json, os, collections
os.chdir(os.path.dirname(os.path.abspath(__file__)))
csv.field_size_limit(10**9)
rec = json.load(open("recomputed.json"))
sales = {}
with open("sales_race.csv", newline="") as f:
    for r in csv.DictReader(f):
        sales[r["race_key"]] = r
# 実売 = deleted でない かつ status in (published, submitted)
live = [x for x in rec if not x["deleted"] and x["status"] in ("published","submitted")]
byrace = collections.defaultdict(list)
for x in live: byrace[x["race_key"]].append(x)
print("live rows", len(live), "races", len(byrace),
      "races with >1 product", sum(1 for v in byrace.values() if len(v)>1))
print("sales races", len(sales))

matched = mism_stake = mism_pay = 0
diffs = []
only_ours = []; only_sales = []
for rk, xs in byrace.items():
    s = sales.get(rk)
    ours_bet = sum((x["cached"]["bet"] or x["recomp"]["bet"]) for x in xs)
    ours_pay = sum((x["cached"]["payout"] if x["cached"]["payout"] is not None else x["recomp"]["payout"]) for x in xs)
    settled_all = all(x["recomp"]["settled"] and x["recomp"]["bet"]>0 for x in xs)
    if s is None:
        only_ours.append((rk, [x["rank_key"] for x in xs], ours_bet, settled_all)); continue
    matched += 1
    if int(s["stake_amount"]) != ours_bet: mism_stake += 1
    if settled_all and int(s["payout_amount"]) != ours_pay:
        mism_pay += 1
        diffs.append((rk, [x["rank_key"] for x in xs], ours_bet, int(s["stake_amount"]), ours_pay, int(s["payout_amount"])))
for rk in sales:
    if rk not in byrace: only_sales.append(rk)
print(f"matched races {matched} / stake mismatch {mism_stake} / payout mismatch {mism_pay}")
print("only in ours:", len(only_ours), "only in netkeirin sales:", len(only_sales))
print("--- payout 差 上位20 (|差| 降順) ---")
for d in sorted(diffs, key=lambda y: -abs(y[4]-y[5]))[:20]: print(d)
print("--- only in ours 先頭10 ---")
for x in only_ours[:10]: print(x)
print("--- only in sales 先頭10 ---")
for x in sorted(only_sales)[:10]: print(x, sales[x]["race_label"], sales[x]["stake_amount"], sales[x]["payout_amount"])
