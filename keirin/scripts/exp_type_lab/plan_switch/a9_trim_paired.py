"""同じレースを両腕から除く外れ値検査: レースを max(払戻_現行, 払戻_腕) で並べ、上位 k 件を両腕から賭け金ごと除く。
k は現行の当たり件数の p%。a8 の「各腕の自分の上位 p%」は尾の太い腕ほど多く削れるので比較に偏る。"""
import sys, io, contextlib
sys.argv = ["x"]
with contextlib.redirect_stdout(io.StringIO()):
    import a2_switch as A2
from a4_grid import run, key_for, np
rows = A2.rows
P = [0, 1, 2, 5]

def droi(a, b, p, only=None):
    """a,b: recs。only: 看板以外に限る（どちらかの腕が _sign のレースを除く）。"""
    A = {id(x[4]): x for x in a}; B = {id(x[4]): x for x in b}
    ids = set(A) | set(B)
    if only:
        ids = {i for i in ids if not any(i in M and M[i][1].endswith("_sign") for M in (A, B))}
    nh = sum(1 for i in ids if i in A and A[i][3] > A[i][2])
    k = int(round(nh * p / 100))
    top = set(sorted(ids, key=lambda i: -max(A[i][3] if i in A else 0, B[i][3] if i in B else 0))[:k])
    keep = ids - top
    def roi(M):
        inv = sum(M[i][2] for i in keep if i in M); pay = sum(M[i][3] for i in keep if i in M)
        return pay / inv * 100
    return roi(B) - roi(A), roi(A)

F2E = lambda r: key_for(r, lo=-1, hi=2) if r["arare"] == 1 and r["axis_sum"] < 1.44 else key_for(r)
arms = [("候補 混戦∧arare1→E(上限あり)", "cap", F2E),
        ("列 lo-2 hi2(上限あり)", "cap", lambda r: key_for(r, lo=-2, hi=2)),
        ("FIRM 1.58(上限あり)", "cap", lambda r: key_for(r, firm=1.58))]
for nm in ("Σp最大(的中系)", "セル最良(探索ROI除10)", "セル最良(探索表示的中)", "型F非看板→E_hit"):
    arms.append((nm, "nocap", A2.pols[nm]))
H = f"{'ΔROI 同レース除外 (全体 | 看板以外)':<30}" + "".join(f"{('全部' if p == 0 else f'除{p}%'):>6}" for p in P) + "  |" + "".join(f"{('全部' if p == 0 else f'除{p}%'):>6}" for p in P)
for w in ("exp", "conf"):
    R = [r for r in rows if r["win"] == w]
    bc, bn = run(R, key_for), A2.recs(R, A2.baseline)
    print(f"\n== {w}\n" + H)
    for nm, kind, pol in arms:
        base = bc if kind == "cap" else bn
        rs = run(R, pol) if kind == "cap" else A2.recs(R, pol)
        print(f"{nm:<30}" + "".join(f"{droi(base, rs, p)[0]:>+7.1f}" for p in P) + "  |" + "".join(f"{droi(base, rs, p, 1)[0]:>+7.1f}" for p in P))
    print(f"{'(参考)現行ROI 上限あり':<30}" + "".join(f"{droi(bc, bc, p)[1]:>7.1f}" for p in P) + "  |" + "".join(f"{droi(bc, bc, p, 1)[1]:>7.1f}" for p in P))
