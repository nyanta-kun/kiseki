#!/usr/bin/env python3
"""「軸が絡まず高配当になるレース」の事前検出（条件確率）— 全型（2026-09-03）。

ユーザー提案:
  上位を軸にすると市場と同じで堅い決着になる。軸が飛ぶ可能性が高いレースは
  上位以外から買えば配当が高い。その条件を事前検出できないか。

これまでは型A のみで測っていた（type_a_upset_2026_08_31）。ここでは
**全型 A〜F・両窓**で、目的を分けて測る:

  BUST   軸1（p3 1位）が3着内に入らない
  BUST2  軸1・軸2 とも3着内に入らない
  U30    確定三連単払戻 >= 30倍
  TGT    BUST ∧ U30      ← ユーザーの言う「軸が絡まず高配当」
  TGT100 BUST ∧ 100倍+

台: /tmp/race_type_board.npz（7車・vintage p3/pw）
"""
from __future__ import annotations
import sys, os
from pathlib import Path
import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO)); os.chdir(REPO)
sys.path.insert(0, str(REPO / "scripts" / "exp_type_lab"))
from common import board, select, CANON  # noqa: E402

z = board()
N = len(z["KEY"])
P3, PW = z["P3"], z["PW"]
WIN = z["WIN"].astype(int)
PAY = z["PAY"].astype(float)

# ── 決着（1,2,3着の車番）──
top3 = np.zeros((N, 3), int)
ok = WIN >= 0
for i in np.flatnonzero(ok):
    top3[i] = CANON[WIN[i]]

axis1 = np.argsort(-P3, axis=1)[:, 0] + 1
axis2 = np.argsort(-P3, axis=1)[:, 1] + 1
in3 = lambda car: (top3 == car[:, None]).any(axis=1)
BUST = ~in3(axis1)
BUST2 = BUST & ~in3(axis2)
U30 = PAY >= 3000.0
U100 = PAY >= 10000.0
TGT = BUST & U30
TGT100 = BUST & U100


def ent(M):
    p = M / np.clip(M.sum(axis=1, keepdims=True), 1e-12, None)
    p = np.clip(p, 1e-12, None)
    return -(p * np.log(p)).sum(axis=1)


rp = z["A_race_point"].astype(float)
P3s = np.sort(P3, axis=1)[:, ::-1]
ai = axis1 - 1
FEATS = {
    "pw_ent   1着率エントロピー": ent(PW),
    "p3_ent   3着内率エントロピー": ent(P3),
    "-axis_sum 軸2車の堅さ(逆)": -z["AXIS_SUM"].astype(float),
    "-pw_max  軸1の1着率(逆)": -PW.max(axis=1),
    "-p3_gap12 軸1と軸2の差(逆)": -(P3s[:, 0] - P3s[:, 1]),
    "-gap     相手の開き(逆)": -z["GAP"].astype(float),
    "arare    荒れ度": z["ARARE"].astype(float),
    "-rp_sd   実力伯仲(得点SD逆)": -rp.std(axis=1),
    "line_sz  軸1のライン人数": z["A_line_size"][np.arange(N), ai].astype(float),
    "-leader  軸1がライン先頭(逆)": -z["A_is_line_leader"][np.arange(N), ai].astype(float),
    "behind   軸1の遅れ率": z["BEHIND"][np.arange(N), ai].astype(float),
    "nlines   ライン本数": z["A_n_lines"][np.arange(N), ai].astype(float),
    "dayi     開催日目": z["DAYI"].astype(float),
    "-agree   印と不一致": -z["AGREE"].astype(float),
}


def auc(s, y):
    s = np.asarray(s, float); y = np.asarray(y, bool)
    if y.sum() == 0 or (~y).sum() == 0:
        return float("nan")
    r = np.argsort(np.argsort(s)) + 1.0
    n1, n0 = y.sum(), (~y).sum()
    return (r[y].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)


WINDOWS = ("explore", "confirm")
TARGETS = {"BUST 軸1が3着外": BUST, "TGT  軸1外∧30倍+": TGT, "TGT100 軸1外∧100倍+": TGT100}

print("=" * 108)
print("① 基準率（母集団はゲート前・7車・型判定と決着が揃うレース）")
print("=" * 108)
print(f"  {'型':4s} {'窓':8s} {'R':>7s} {'BUST%':>7s} {'BUST2%':>7s} {'30倍+%':>7s} "
      f"{'TGT%':>7s} {'TGT100%':>8s} {'TGT|BUST%':>10s}")
for t in [None, "A", "B", "C", "D", "E", "F"]:
    for w in WINDOWS:
        ix = select(t, w)
        if len(ix) == 0:
            continue
        print(f"  {t or 'ALL':4s} {w:8s} {len(ix):7,d} {BUST[ix].mean()*100:7.2f} "
              f"{BUST2[ix].mean()*100:7.2f} {U30[ix].mean()*100:7.2f} {TGT[ix].mean()*100:7.2f} "
              f"{TGT100[ix].mean()*100:8.2f} {TGT[ix].sum()/max(BUST[ix].sum(),1)*100:10.2f}")
    print()

print("=" * 108)
print("② 事前検出できるか（AUC・両窓）— 全型プール")
print("=" * 108)
for tname, y in TARGETS.items():
    print(f"\n  ▼ {tname}")
    ie, ic = select(None, "explore"), select(None, "confirm")
    rows = []
    for f, s in FEATS.items():
        rows.append((f, auc(s[ie], y[ie]), auc(s[ic], y[ic])))
    rows.sort(key=lambda r: -r[2])
    print(f"    {'量':30s} {'AUC探索':>8s} {'AUC確認':>8s}")
    for f, a, b in rows:
        print(f"    {f:30s} {a:8.3f} {b:8.3f}")

print()
print("=" * 108)
print("③ 型別 AUC（TGT = 軸1外∧30倍+）")
print("=" * 108)
print(f"  {'量':30s} " + " ".join(f"{t:>13s}" for t in "ABCDEF"))
for f, s in FEATS.items():
    cells = []
    for t in "ABCDEF":
        ie, ic = select(t, "explore"), select(t, "confirm")
        cells.append(f"{auc(s[ie],TGT[ie]):6.3f}/{auc(s[ic],TGT[ic]):5.3f}")
    print(f"  {f:30s} " + " ".join(f"{c:>13s}" for c in cells))

print()
print("=" * 108)
print("④ 上位10%のリフト（両窓・境界は探索窓の p90）")
print("=" * 108)
print(f"  {'型':4s} {'量':30s} {'基準TGT%':>9s} {'上位10%TGT%':>12s} {'リフト':>7s} "
      f"{'基準':>7s} {'上位10%':>8s} {'リフト':>7s}  (探索 | 確認)")
for t in ["A", "B", "C", "D", "E", "F"]:
    ie, ic = select(t, "explore"), select(t, "confirm")
    for f in ["pw_ent   1着率エントロピー", "-axis_sum 軸2車の堅さ(逆)",
              "-rp_sd   実力伯仲(得点SD逆)", "p3_ent   3着内率エントロピー"]:
        s = FEATS[f]
        thr = np.quantile(s[ie], 0.90)
        me, mc = s[ie] >= thr, s[ic] >= thr
        be, bc = TGT[ie].mean(), TGT[ic].mean()
        te = TGT[ie][me].mean() if me.sum() else float("nan")
        tc = TGT[ic][mc].mean() if mc.sum() else float("nan")
        print(f"  {t:4s} {f:30s} {be*100:9.2f} {te*100:12.2f} {te/be:7.2f} "
              f"{bc*100:7.2f} {tc*100:8.2f} {tc/bc:7.2f}")
    print()
