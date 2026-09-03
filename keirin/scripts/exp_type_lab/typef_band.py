#!/usr/bin/env python3
"""型F（F_hit）に帯の下限を入れる掃引（2026-09-03・ユーザー提案）。

発端: 「E,F は波乱で多点数＝低確率だから、合成オッズが高くなるように組む方が良いのでは」

🔴 前提の確認（実測済み）:
  - `E_hit` は既に `min_odds=30.0`＝全プラン最高の帯。**提案は E には実装済み**
  - `F_hit` は**帯なし**で計画倍 2.5倍＝鉄板 A_hit と同じ。荒れる型なのにここだけ素通し

そこで **帯だけ**を動かす（`max_legs=12` と `alloc="conf"` は現行のまま固定）。
⚠️ 既存 ARMS の「帯15倍+12点(C式)」は `alloc="dutch"` を同時に変えていて交絡している。

🔴 帯を上げると平均想定払戻が上がり **2万円ゲートを通りやすくなる**（F_hit は現状 24% 落ちる）
   ＝**件数が増える方向**。母集団が変わるので「両方が買えたレースだけ」の対応比較も併記する。
"""
from __future__ import annotations
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[0]))
import numpy as np
import common as C
from typef_racetype import ctx, run_arm, CAND, AXIS_GATE_MIN
from src.type_lab import Plan

BANDS = [0.0, 5.0, 10.0, 15.0, 20.0, 30.0]

# 帯だけを変えた腕を CAND へ足す（点数12・alloc conf は現行のまま）
NAMES = {}
for lo in BANDS:
    nm = "現行(帯なし)" if lo == 0 else f"帯{lo:.0f}倍+"
    NAMES[lo] = nm
    CAND[nm] = Plan("x", "F", "trifecta", "prob_top", 0,
                    min_odds=lo, max_legs=12, alloc="conf")


def main() -> int:
    z = C.board()
    for label, win in (("探索 2024-07〜2025-12", "explore"), ("確認 2026-01〜08", "confirm")):
        base = C.select("F", win)
        # 本番の母集団に揃える（軸信頼ゲート F_hit=1.230）
        base = base[np.array([float(z["AXIS_SUM"][i]) >= AXIS_GATE_MIN["F_hit"] for i in base])]
        nd = C.days_of(base)
        # 1レースぶんの ctx は1回だけ作る
        pre = {}
        for i in base:
            x = ctx(int(i))
            if x is None:
                continue
            pre[int(i)] = {lo: run_arm(x, NAMES[lo]) for lo in BANDS}
        print(f"\n===== {label}  対象 {len(pre)}R / {nd}日 =====")
        print(C.HEAD + "   10万+/日")
        for lo in BANDS:
            recs = [r for v in pre.values() if (r := v[lo])]
            s = C.summarize(recs, nd)
            big = s.get("big_per_day", 0.0) if s.get("n") else 0.0
            print(C.line(NAMES[lo], s) + f" {big:9.3f}")

        # ── 対応比較（現行と当該帯の両方が買えたレースだけ）──
        print("\n  ── 両方が買えたレースだけの対応比較（母集団ずれを外す）──")
        print(f"  {'腕':16s} {'R':>6s} {'表示的中% 現行→帯':>22s} {'ROI% 現行→帯':>18s}"
              f" {'払戻中央 現行→帯':>22s}")
        for lo in BANDS[1:]:
            both = [v for v in pre.values() if v[0.0] and v[lo]]
            if not both:
                print(f"  {NAMES[lo]:16s}  (該当なし)"); continue
            a = C.summarize([v[0.0] for v in both], nd)
            b = C.summarize([v[lo] for v in both], nd)
            n2a = sum(1 for v in both if v[0.0]['pay'] >= 2*v[0.0]['inv'])
            n2b = sum(1 for v in both if v[lo]['pay'] >= 2*v[lo]['inv'])
            print(f"  {NAMES[lo]:16s} {len(both):6d} "
                  f"{a['shown']:9.2f} → {b['shown']:6.2f} ({b['shown']-a['shown']:+5.2f}) "
                  f"{a['roi']:6.1f} → {b['roi']:5.1f} ({b['roi']-a['roi']:+5.1f}) "
                  f"{a['med_pay']:8,.0f} → {b['med_pay']:8,.0f}"
                  f"   2倍+ {n2a:4d} → {n2b:4d} ({100*(n2b-n2a)/max(n2a,1):+5.1f}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
