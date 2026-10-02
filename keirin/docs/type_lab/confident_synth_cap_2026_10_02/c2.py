import pickle, statistics as st, random
from collections import defaultdict, Counter
C = pickle.load(open("c1.pkl", "rb"))
W = (("探索", "2025-01-01", "2025-12-31", "paper"), ("確認", "2026-01-01", "2026-08-26", "paper"),
     ("実地", "2026-08-27", "2026-10-01", "live"), ("実地9/22〜", "2026-09-22", "2026-10-01", "live"))
ARMS = (("現行 2.5以上", 2.5, 1e9), ("2.5〜3.5（提案）", 2.5, 3.5), ("2.5〜3.0", 2.5, 3.0), ("3.0〜3.5", 3.0, 3.5),
        ("2.5〜4.0", 2.5, 4.0), ("3.0〜4.0", 3.0, 4.0))
def pick(cs, lo, hi):
    by = defaultdict(list)
    for c in cs:
        if lo <= c["syn"] <= hi: by[c["date"]].append(c)
    return [max(v, key=lambda c: (c["p"], c["rk"])) for v in by.values()]
for wl, d1, d2, mode in W:
    cs = [c for c in C if c["mode"] == mode and d1 <= c["date"] <= d2]
    days = len({c["date"] for c in cs})
    print(f"\n== {wl} {d1}〜{d2}（候補のある日 {days}）")
    print(f"  {'腕':14s} 選べた日 表示的中  的中(払戻>0) ROI   当たり払戻中央 合成中央 予測Σp  外れのうち10倍未満決着  主なプラン")
    for name, lo, hi in ARMS:
        ps = pick(cs, lo, hi); n = len(ps)
        if not n: print(f"  {name:14s} 0"); continue
        shown = [c for c in ps if c["ret"] > c["inv"]]
        anyhit = sum(c["ret"] > 0 for c in ps)
        roi = sum(c["ret"] for c in ps) / sum(c["inv"] for c in ps) * 100
        miss = [c for c in ps if c["ret"] <= c["inv"]]
        cheap = sum(1 for c in miss if c["win_odds"] and c["win_odds"] < 10)
        print(f"  {name:14s} {n:5d}  {len(shown)/n*100:6.1f}%  {anyhit/n*100:6.1f}%  {roi:5.1f}  {st.median([c['ret'] for c in shown]) if shown else 0:9,.0f}"
              f"  {st.median(c['syn'] for c in ps):5.2f}  {st.mean(c['p'] for c in ps)*100:5.1f}%  {cheap/max(len(miss),1)*100:5.1f}%"
              f"  {Counter(c['plan'] for c in ps).most_common(3)}")
