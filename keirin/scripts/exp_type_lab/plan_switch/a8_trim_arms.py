"""a2(プラン切替)・a4(境界)の各腕で、払戻上位 p% 除外後の ΔROI（現行比）。看板(_sign)を含む全体と看板以外。"""
import sys, io, contextlib
sys.argv = ["x"]
with contextlib.redirect_stdout(io.StringIO()):
    import a2_switch as A2
from a4_grid import run, key_for, np
from a7_trim import trim, P
rows = A2.rows
def d(a, b, p):
    return trim([x[3] for x in b], [x[2] for x in b], p) - trim([x[3] for x in a], [x[2] for x in a], p)
ns = lambda rs: [x for x in rs if not x[1].endswith("_sign")]
arms2 = {k: v for k, v in A2.pols.items() if k != "現行"}
arms4 = {f"FIRM {f}": (lambda r, f=f: key_for(r, firm=f)) for f in (1.40, 1.50, 1.58)}
for lo, hi in ((-2, 1), (-1, 2), (-2, 2), (0, 1), (-1, 0)):
    arms4[f"列 lo{lo} hi{hi}"] = (lambda r, lo=lo, hi=hi: key_for(r, lo=lo, hi=hi))
H = f"{'腕 ΔROI(全体 | 看板以外)':<26}" + "".join(f"{('全部' if p == 0 else f'除{p}%'):>6}" for p in P) + "  |" + "".join(f"{('全部' if p == 0 else f'除{p}%'):>6}" for p in P)
for w in ("exp", "conf"):
    R = [r for r in rows if r["win"] == w]
    print(f"\n== {w}\n" + H)
    b2 = A2.recs(R, A2.baseline)
    for nm, pol in arms2.items():
        rs = A2.recs(R, pol)
        print(f"{nm:<26}" + "".join(f"{d(b2, rs, p):>+7.1f}" for p in P) + "  |" + "".join(f"{d(ns(b2), ns(rs), p):>+7.1f}" for p in P))
    b4 = run(R, key_for)
    for nm, pol in arms4.items():
        rs = run(R, pol)
        print(f"{nm:<26}" + "".join(f"{d(b4, rs, p):>+7.1f}" for p in P) + "  |" + "".join(f"{d(ns(b4), ns(rs), p):>+7.1f}" for p in P))
