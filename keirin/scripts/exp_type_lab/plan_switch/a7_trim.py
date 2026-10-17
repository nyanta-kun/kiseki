"""外れ値の頑健性: 当たりのうち払戻上位 p% を 0 にしても（賭け金は残す）結論が変わるか。日次上限あり・看板枠(_sign)は別掲。"""
import sys
sys.argv = ["x"]
from a4_grid import run, key_for, rows, np, G, paired
P = [0, 1, 2, 5, 10]

def trim(pay, inv, p):
    """当たり(pay>inv)のうち払戻上位 p% の払戻を除いた ROI%。"""
    pay = np.asarray(pay, float); inv = np.asarray(inv, float)
    h = np.sort(pay[pay > inv])[::-1]
    k = int(round(len(h) * p / 100))
    return (pay.sum() - h[:k].sum()) / inv.sum() * 100

def line(name, rs):
    inv = [x[2] for x in rs]; pay = [x[3] for x in rs]
    nh = sum(p > i for p, i in zip(pay, inv))
    return f"{name:<22}{len(rs):>6}{nh:>6}" + "".join(f"{trim(pay, inv, p):>7.1f}" for p in P)

HEAD = f"{'':<22}{'件':>6}{'当り':>6}" + "".join(f"{('全部' if p == 0 else f'除{p}%'):>6}" for p in P)
F2E = lambda r: key_for(r, lo=-1, hi=2) if r["arare"] == 1 and r["axis_sum"] < 1.44 else key_for(r)
nonsign = lambda rs: [x for x in rs if not x[1].endswith("_sign")]
if __name__ == "__main__":
  for w in ("exp", "conf"):
        R = [r for r in rows if r["win"] == w]
        base = run(R, key_for)
        print(f"\n== {w}  {len({r['date'] for r in R})}日  (ROI%)\n" + HEAD)
        for k in sorted({x[1] for x in base}):
            print(line(k, [x for x in base if x[1] == k]))
        print(line("看板以外 計", nonsign(base)))
        print(line("全体 計", base))
        cand = run(R, F2E)
        print("-- 候補 混戦∧arare=1→E_hit")
        print(line("案 看板以外", nonsign(cand))); print(line("案 全体", cand))
        for nm, a, b in (("Δ看板以外", nonsign(base), nonsign(cand)), ("Δ全体", base, cand)):
            d = [trim([x[3] for x in b], [x[2] for x in b], p) - trim([x[3] for x in a], [x[2] for x in a], p) for p in P]
            print(f"{nm:<34}" + "".join(f"{v:>+7.1f}" for v in d))
