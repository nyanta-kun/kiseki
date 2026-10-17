from collections import Counter
from common import *
rows = load()
for w in ("exp", "conf"):
    R = [r for r in rows if r["win"] == w]
    nd = len({r["date"] for r in R})
    print(f"== {w} {len(R)}R {nd}日"); print(HEAD)
    b = recs(R, baseline)
    print(fmt("現行(上限なし)", summ(b, nd)))
    for k, c in Counter(x[1] for x in b).most_common():
        print(fmt("  " + k, summ([x for x in b if x[1] == k], nd)))
    print("sell分布", Counter(r["sell"] for r in R).most_common())
    print("ゲート落ち", Counter(r["sell"] for r in R if r["sell"] and not ok(r, r["sell"])).most_common())
