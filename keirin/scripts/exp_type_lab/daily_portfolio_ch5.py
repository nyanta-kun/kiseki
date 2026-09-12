"""§5 実入稿（2026-08-29〜09-09）を同じ分解に掛ける。"""
import sys, csv; sys.path.insert(0,'scripts/exp_type_lab')
import numpy as np, subprocess
from collections import defaultdict, Counter
import daily_portfolio as D

# 確認窓の paper からプラン別の表示的中を作る（実入稿の期待値の元）
base = {}
CF = D.CF
for p in {r['plan'] for r in CF}:
    s = [r for r in CF if r['plan'] == p]
    base[p] = sum(1 for r in s if r['pay'] > r['inv']) / len(s)
print("プラン別ベース（確認窓 paper・入稿ゲート後の全母集団）:")
print("  " + "  ".join(f"{k}={v*100:.1f}%" for k, v in sorted(base.items())))

q = """SELECT substring(race_key from 1 for 8) d, rank_key,
  settled_bet, settled_payout FROM keirin.netkeirin_submissions
 WHERE deleted_at IS NULL AND settled_bet IS NOT NULL
   AND substring(race_key from 1 for 8) >= '20260829'
   AND rank_key IN (SELECT DISTINCT plan_key FROM keirin.type_lab_picks)"""
import os
out = subprocess.run(["psql", os.environ["KEIRIN_DB_URL"], "-t", "-A", "-F,", "-c", q],
                     capture_output=True, text=True,
                     env={**__import__('os').environ}).stdout
rows = []
for ln in out.strip().split("\n"):
    if not ln:
        continue
    d, rk, bet, pay = ln.split(",")
    rows.append(dict(d=d, plan=rk, bet=float(bet), pay=float(pay)))
FALL = {"F_line": base.get("F_hit"), "F_pay": base.get("F_sign"),
        "A_big": base.get("A_ana"), "B_sign": base.get("F_sign"),
        "C_sign": base.get("F_sign"), "D_sign": base.get("F_sign"),
        "B_big": base.get("A_ana"), "C_big": base.get("A_ana"),
        "D_big": base.get("A_ana")}
byd = defaultdict(lambda: [0, 0, 0.0, 0.0, 0.0])
for r in rows:
    p = base.get(r["plan"]) or FALL.get(r["plan"])
    if p is None:
        continue
    a = byd[r["d"]]
    a[0] += 1; a[1] += 1 if r["pay"] > r["bet"] else 0
    a[2] += p; a[3] += r["bet"]; a[4] += r["pay"]
print(f"\n{'日付':>9s} {'件':>4s} {'実測%':>7s} {'構成期待%':>9s} {'差pt':>7s} "
      f"{'期待SD':>7s} {'z':>6s} {'ROI%':>7s}")
zs = []
for d in sorted(byd):
    n, h, e, bet, pay = byd[d]
    obs = h / n * 100; exp = e / n * 100
    sd = np.sqrt(e / n * (1 - e / n) / n) * 100
    z = (obs - exp) / sd
    zs.append(z)
    print(f"{d:>9s} {n:4d} {obs:7.2f} {exp:9.2f} {obs-exp:+7.2f} {sd:7.2f} {z:+6.2f} "
          f"{pay/bet*100:7.1f}")
zs = np.array(zs)
print(f"\n  z の平均 {zs.mean():+.3f} / SD {zs.std(ddof=1):.3f}"
      f"（過分散が無ければ SD≈1）  χ²/df = {np.mean(zs**2):.3f}  n={len(zs)}日")
