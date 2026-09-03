#!/usr/bin/env python3
"""型F の帯をラインナップ全体で測る（2026-09-03）。

`typef_band.py` は型F 単体で「帯15倍が最適点」と出した（2倍+/日 +35〜46%・
表示的中 −4.6pt・ROI は窓で符号反転）。**単体で良く見えても全体で消えることがある**
（型E で実際に踏んだ）ので、全8プランを組んだ状態で測り直す。

腕:
  A 現行              … F_hit は帯なし
  B 置換(帯15倍)       … F_hit を `min_odds=15` へ置き換える（点数12・alloc conf は据置）
  C ハイブリッド        … F_hit がゲートに落ちたときだけ帯15倍で拾う（既存案・置換しない）

🔴 **確認窓(2026)が本番相当**。予測オッズモデルの train_end が 2025-12-31 なので、
   帯フィルタを扱う本実験では**探索窓は in-sample**。
"""
from __future__ import annotations
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[0]))
from collections import defaultdict
import numpy as np
import common as C
from typef_racetype import (ctx, _run_named, _plan_for, AXIS_GATE_MIN, CAND)
from src.type_lab import PLANS, Plan, SIGNBOARD_RACE_TYPES

#: 置換用: F_hit の帯だけ 15倍にした版（点数・配分は現行のまま）
PLANS["F_b15"] = Plan("F_b15", "F", "trifecta", "prob_top", 0,
                      min_odds=15.0, max_legs=12, alloc="conf")

ARMS = ("A 現行", "B 置換(帯15倍)", "C ハイブリッド")


def main() -> int:
    z = C.board()
    rt = np.array([str(v) for v in z["RTYPE"]])
    tp = np.array([str(v) for v in z["TYPE"]])
    axs = np.array([float(v) for v in z["AXIS_SUM"]])
    sign_rt = tuple(SIGNBOARD_RACE_TYPES)

    for label, win in (("探索 2024-07〜2025-12 (in-sample)", "explore"),
                       ("確認 2026-01〜08 (本番相当)", "confirm")):
        base = [int(i) for i in C.select(None, win) if tp[int(i)] in "ABCDEF"]
        nd = C.days_of(C.select(None, win))
        pre = {}
        for i in base:
            x = ctx(i)
            if x is None:
                continue
            tl = tp[i]
            if tl == "F":
                keys = ["F_hit", "F_pay", "F_sign", "F_b15", "_C15"]
            elif tl == "A":
                keys = ["A_ana", "A_trio", "A_hit"]
            else:
                keys = [{"B": "B_hit", "C": "C_hit", "D": "D_hit", "E": "E_hit"}[tl]]
            pre[i] = {k: _run_named(x, k) for k in keys}
            pre[i]["_pw"] = x.shape.pw_ent
        print("\n" + "=" * 96)
        print(f"=== {label}   n={len(pre):,}R / {nd}日 ===")
        print(f"  {'腕':16s} {'件/日':>6s} {'表示的中%':>9s} {'2倍+/日':>8s} "
              f"{'10万+/日':>8s} {'払戻中央':>9s} {'ROI%':>7s}")
        out = {}
        for arm in ARMS:
            recs = []
            for i, d in pre.items():
                key = _plan_for(tp[i], rt[i], d["_pw"], d.get("A_trio") is not None, sign_rt)
                if key == "F_hit":
                    if arm == ARMS[1]:
                        key = "F_b15"
                    elif arm == ARMS[2]:
                        r = d["F_hit"] or d["_C15"]
                        if axs[i] >= AXIS_GATE_MIN.get("F_hit", 0.0) and r:
                            recs.append(r)
                        continue
                # 軸信頼ゲート（置換版も F_hit の閾値で判定＝母集団を現行と揃える）
                if axs[i] < AXIS_GATE_MIN.get("F_hit" if key == "F_b15" else key, 0.0):
                    continue
                if (r := d.get(key)):
                    recs.append(r)
            s = C.summarize(recs, nd)
            out[arm] = s
            print(f"  {arm:16s} {s['perday']:6.2f} {s['shown']:8.2f}% {s['two_per_day']:8.2f} "
                  f"{s['big_per_day']:8.3f} {s['med_pay']:9,.0f} {s['roi']:7.1f}")
        a = out[ARMS[0]]
        for arm in ARMS[1:]:
            b = out[arm]
            print(f"    Δ {arm:14s} 件/日 {b['perday']-a['perday']:+5.2f} "
                  f"表示的中 {b['shown']-a['shown']:+5.2f}pt "
                  f"2倍+/日 {b['two_per_day']-a['two_per_day']:+5.2f} "
                  f"({100*(b['two_per_day']-a['two_per_day'])/a['two_per_day']:+5.1f}%) "
                  f"10万+ {b['big_per_day']-a['big_per_day']:+6.3f} "
                  f"ROI {b['roi']-a['roi']:+5.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
