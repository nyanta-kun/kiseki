#!/usr/bin/env python3
"""H06 目視: 対象レース1件で ①現行 と ②H06 の買い目・配分・採点を表示する（集計の前に見る）。"""
from __future__ import annotations
import pickle, sys
import numpy as np
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import h06_run as H
S, R, TL, D, CANON = H.S, H.R, H.TL, H.D, H.CANON


def main():
    pop = pickle.load(open(D / "h06_pop.pkl", "rb")); FIN = pickle.load(open(D / "h06_final.pkl", "rb"))["FIN"]
    b = H.load_board(True, pop)
    KEY, DATE, WIN, PAY = b["KEY"], b["DATE"], b["WIN"], b["PAY"]
    ids = pop["pop"]
    cache = {i: S.ctx(i) for i in ids}
    ok = [i for i in ids if cache[i] is not None]
    recs = {r["race_key"]: r for r in R.run("current", {}, ok, cache)}
    tgt = [i for i in ids if pop["ent"][i] >= H.P80]
    # 例1: ① があり ② が当たった最初の対象 / 例2: ① があり ① が当たった
    ex = []
    for i in tgt:
        k = str(KEY[i])
        if k in recs and int(WIN[i]) in H.h06_stakes(pop["V"][i]):
            ex.append(i); break
    for i in tgt:
        k = str(KEY[i])
        if k in recs and recs[k]["pay"] > 0 and i not in ex:
            ex.append(i); break
    for i in ex:
        k = str(KEY[i]); x = cache[i]; r1 = recs[k]; V = pop["V"][i]
        print("=" * 100)
        print(f"{k} {DATE[i]} 型={x.shape.type_label} axis_sum={x.shape.axis_sum:.3f} mdl_ent={pop['ent'][i]:.4f}(p80={H.P80:.4f}) "
              f"race_type={x.rtype}")
        print(f"当たり目 {'-'.join(map(str, CANON[int(WIN[i])]))} 最終オッズ {PAY[i]/100:.1f}倍  (票側 vintage予測 {V[int(WIN[i])]:.1f}倍)")
        print(f"-- ① 現行: slot={r1['slot']} plan={r1['plan']} 券種={'三連複' if r1['trio'] else '三連単'} 点数={len(r1['stakes'])} "
              f"賭け金合計={r1['inv']:,.0f} 計画払戻(平均)={r1['mean']:,.0f} 払戻={r1['pay']:,.0f}")
        odds = x.po_t3 if r1["trio"] else x.po_tf
        for c, s in sorted(r1["stakes"].items(), key=lambda t: odds[t[0]]):
            lab = "=".join(map(str, sorted(c))) if r1["trio"] else "-".join(map(str, c))
            print(f"     {lab:8s} 予測{odds[c]:8.1f}倍 賭け{s:6,d}")
        st = H.h06_stakes(V)
        print(f"-- ② H06: 予測>=100 の目 {int((V>=100).sum())}/210 点, 昇順10点 賭け金合計={sum(st.values()):,}")
        f = FIN[k]
        for j, s in sorted(st.items(), key=lambda t: V[t[0]]):
            hit = "←的中" if j == int(WIN[i]) else ""
            print(f"     {'-'.join(map(str, CANON[j])):8s} 予測{V[j]:8.1f}倍 最終{f.get(j, float('nan')):8.1f}倍 賭け{s:6,d} {hit}")
        inv, pay = H.settle_idx(st, int(WIN[i]), float(PAY[i]))
        print(f"   ② 投資 {inv:,.0f} 払戻 {pay:,.0f}  (当たれば 払戻=賭け×最終オッズ)")
        print("   計画払戻(1/Σ(1/予測)×1万) =", f"{10000/sum(1/V[j] for j in st):,.0f}")


if __name__ == "__main__":
    main()
