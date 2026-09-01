#!/usr/bin/env python3
"""提案A ②: 軸が飛ぶ側を買う商品の採算（2026-08-31）。

軸1を除いた6車から三連複／三連単を買う。本番の入稿ゲートを腕ごとに掛け直し、
選別（pw_ent 上位）には**必ず同数の無作為対照**を並べる。
"""
import sys
from pathlib import Path, itertools, random, math
from statistics import median
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np
import type_a_upset2 as M

data = M.load()

def _trio_rank(d):
    """軸1を除いた6車の三連複20通りを、モデル確率の降順に並べる。"""
    cars = sorted(d["o"][1:])
    pr = {}
    for c in itertools.combinations(cars, 3):
        pr[frozenset(c)] = sum(float(d["PROB"][M.CIDX[p]]) for p in itertools.permutations(c))
    return sorted(pr, key=lambda k: -pr[k])

def _tf_rank(d):
    cars = set(d["o"][1:])
    idx = [i for i, t in enumerate(M.CANON) if set(t) <= cars]
    idx.sort(key=lambda i: -float(d["PROB"][i]))
    return [M.CANON[i] for i in idx]

for k in (3, 5, 8, 10):
    M.ARMS[f"飛び三連複 上位{k}点"] = (lambda k: lambda d: ("trio", _trio_rank(d)[:k]))(k)
for k in (5, 10, 15):
    M.ARMS[f"飛び三連単 上位{k}点"] = (lambda k: lambda d: ("tf", _tf_rank(d)[:k]))(k)
# 新軸2車（p3 2位・3位）流し = 残り4車 → 4点
M.ARMS["飛び三連複 新軸2車流し4点"] = lambda d: (
    "trio", [frozenset((d["o"][1], d["o"][2], d["o"][k])) for k in (3, 4, 5, 6)])

def pw_ent_of(d): return d["pw_ent"]

ex = [d for d in data if M.WINDOWS["探索 2025"][0] <= d["date"] <= M.WINDOWS["探索 2025"][1]]
pe33 = sorted(pw_ent_of(d) for d in ex)[len(ex) * 2 // 3]
pe10 = sorted(pw_ent_of(d) for d in ex)[int(len(ex) * .9)]
print(f"境界（探索窓の分位）: pw_ent 上位1/3 > {pe33:.4f} / 上位10% > {pe10:.4f}")

ARM_NAMES = [f"飛び三連複 上位{k}点" for k in (3, 5, 8, 10)] + \
            ["飛び三連複 新軸2車流し4点"] + [f"飛び三連単 上位{k}点" for k in (5, 10, 15)]

for win, (lo, hi) in M.WINDOWS.items():
    rs = [d for d in data if lo <= d["date"] <= hi]
    nd = len({d["date"] for d in rs})
    bust = sum(1 for d in rs if d["o"][0] not in d["f"])
    print(f"\n{'='*118}\n=== {win}  型A {len(rs):,}R / {nd}日  軸崩壊 {bust/len(rs):.1%} ===")
    for sname, sub in (("型A 全部", rs),
                       ("pw_ent 上位1/3", [d for d in rs if pw_ent_of(d) > pe33]),
                       ("pw_ent 上位10%", [d for d in rs if pw_ent_of(d) > pe10])):
        b = sum(1 for d in sub if d["o"][0] not in d["f"])
        print(f"\n  ── 選別「{sname}」{len(sub):,}R  軸崩壊 {b/len(sub):.1%} ──")
        print(M.HDR)
        for name in ARM_NAMES:
            recs = [r for r in (M.play(d, name) for d in sub) if r]
            print(M.row(name, M.summ(recs, nd)))
    # 無作為対照（上位1/3 と同数）
    sel = [d for d in rs if pw_ent_of(d) > pe33]
    print(f"\n  ── 無作為に同数（{len(sel):,}R）を取った対照 20本 ──")
    for name in ("飛び三連複 上位5点", "飛び三連単 上位10点"):
        rois = []
        for sd in range(20):
            pick = random.Random(sd).sample(rs, len(sel))
            s = M.summ([r for r in (M.play(d, name) for d in pick) if r], nd, ci=False)
            if s: rois.append(s["roi"])
        rois.sort()
        real = M.summ([r for r in (M.play(d, name) for d in sel) if r], nd, ci=False)
        print(f"    {name:<22} 対照 中央 {rois[10]:6.1f}% [{rois[0]:.1f},{rois[-1]:.1f}]"
              f"   選別 {real['roi']:6.1f}%  → 対照 {sum(1 for x in rois if real['roi']>x)}/20 に勝ち")
