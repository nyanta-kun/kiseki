#!/usr/bin/env python3
"""軸崩壊の天井 — 相手(+4.8pt)・順序(+15.3pt)と同じ台で並べる（2026-09-14）。

`partner_ceiling_2026_09_14.md` が相手側と順序側の天井を出した。残るのは
外れの 41.38%（外れの55%）を占める**軸崩壊**。同じ母集団・同じ指標で並べないと
「どこに資源を回すか」の判断ができない。

腕:
  ① 現行
  ② **軸崩壊レースを見送る**（オラクル選別）— 日次上限を無視した上限値
  ③ **軸崩壊レースだけ買い方を変える**（軸を使わない総流し的な腕のオラクル）
  ④ 相手オラクル / 順序オラクル（`partner_ceiling` の再掲・同じ台で出し直す）

🔴 ②は**日次上限を無視している**。本番は `DAILY_CAP_RACE_FRACTION=0.5` で
   上限に当たっており（`synth_odds_thin` 実測: 日単位 9/9 日・朝の波 8/8 で binding）、
   見送った枠は**次順位のレースが埋める**。したがって②は「上限」であって
   実現可能な値ではない。

    PYTHONPATH=. .venv/bin/python scripts/exp_type_lab/bust_ceiling.py map|who
"""
from __future__ import annotations

import itertools
import pickle
import sys

import numpy as np

_R = None


def load():
    global _R
    if _R is None:
        with open("/tmp/ratebranch/rows.pkl", "rb") as f:
            _R = pickle.load(f)
    return _R


def wins():
    r = load()
    return {w: [x for x in r if x["win"] == w] for w in ("explore", "confirm")}


def _shown(rs):
    return sum(1 for r in rs if r["pay"] >= r["inv"]) / len(rs) * 100


def mapping():
    W = wins()
    print("§1 外れの3成分と、それぞれを消したときの表示的中（同じ台・同じ母集団）\n")
    print(f"{'腕':34s}{'確認 2026':>12s}{'探索':>11s}")
    out = {}
    for w in ("confirm", "explore"):
        rs = W[w]
        n = len(rs)
        base = _shown(rs)
        bust = [r for r in rs if not r["both_in3"]]
        # ② 軸崩壊レースを見送る（その商品を母集団から外す）
        keep = [r for r in rs if r["both_in3"]]
        a2 = _shown(keep)
        # ③ 軸崩壊レースだけ「必ず当たる」に置き換える（買い方を変えるオラクルの上限）
        a3 = (sum(1 for r in rs if r["pay"] >= r["inv"]) + len(bust)) / n * 100
        out[w] = dict(base=base, n=n, nb=len(bust), bust_rate=len(bust) / n * 100,
                      skip=a2, fix=a3)
    for lab, k in (("① 現行", "base"),
                   ("② 軸崩壊レースを見送る（上限）", "skip"),
                   ("③ 軸崩壊レースを必ず当てる（上限）", "fix")):
        print(f"{lab:34s}{out['confirm'][k]:11.2f}%{out['explore'][k]:10.2f}%")
    print(f"{'  Δ②（見送り）':34s}{out['confirm']['skip']-out['confirm']['base']:+10.2f}pt"
          f"{out['explore']['skip']-out['explore']['base']:+10.2f}pt")
    print(f"{'  Δ③（買い方で救う）':34s}{out['confirm']['fix']-out['confirm']['base']:+10.2f}pt"
          f"{out['explore']['fix']-out['explore']['base']:+10.2f}pt")
    print(f"\n  軸崩壊の割合 確認 {out['confirm']['bust_rate']:.2f}% "
          f"(n={out['confirm']['nb']:,}/{out['confirm']['n']:,}) / "
          f"探索 {out['explore']['bust_rate']:.2f}% (n={out['explore']['nb']:,}/{out['explore']['n']:,})")
    print("\n  参考（partner_ceiling_2026_09_14 §1・同じ台）:")
    print("    相手オラクル +4.83 / +5.31pt   順序オラクル +15.30 / +15.69pt")
    print("\n🔴 ②は日次上限を無視した上限値。本番は上限に当たっており（朝の波 8/8 日）")
    print("   見送った枠は次順位のレースが埋めるので、実現値はこれより小さい。")


def who():
    """軸崩壊の中身 — どちらの軸が飛んでいるか。"""
    W = wins()
    print("\n§2 軸崩壊の中身（軸2車のどちらが3着を外したか）\n")
    print(f"{'':28s}{'確認':>10s}{'探索':>10s}")
    rowsn = {}
    for w in ("confirm", "explore"):
        rs = [r for r in W[w] if not r["both_in3"]]
        both = sum(1 for r in rs if r["a1"] not in r["fin"] and r["a2"] not in r["fin"])
        only1 = sum(1 for r in rs if r["a1"] not in r["fin"] and r["a2"] in r["fin"])
        only2 = sum(1 for r in rs if r["a1"] in r["fin"] and r["a2"] not in r["fin"])
        rowsn[w] = (len(rs), both, only1, only2)
    lab = ["軸1だけ3着外", "軸2だけ3着外", "両方3着外"]
    for j, l in enumerate(lab):
        i = {0: 2, 1: 3, 2: 1}[j]
        print(f"{l:28s}{rowsn['confirm'][i]/rowsn['confirm'][0]*100:9.2f}%"
              f"{rowsn['explore'][i]/rowsn['explore'][0]*100:9.2f}%")
    print(f"{'（軸崩壊の件数）':28s}{rowsn['confirm'][0]:10,d}{rowsn['explore'][0]:10,d}")
    # 軸崩壊レースの配当
    print(f"\n{'':28s}{'確認':>10s}{'探索':>10s}")
    for w in ("confirm", "explore"):
        rs = [r for r in W[w] if not r["both_in3"]]
        pass
    for lab2, f in (("三連単 確定配当の中央値",
                     lambda rs: np.median([r["pay_tf"] for r in rs])),):
        v = []
        for w in ("confirm", "explore"):
            v.append(f([r for r in W[w] if not r["both_in3"]]))
        print(f"{lab2:28s}{v[0]:9,.0f}円{v[1]:9,.0f}円")
        v = []
        for w in ("confirm", "explore"):
            v.append(f([r for r in W[w] if r["both_in3"]]))
        print(f"{'（参考）軸2車そろい時':28s}{v[0]:9,.0f}円{v[1]:9,.0f}円")


if __name__ == "__main__":
    {"map": mapping, "who": who}[sys.argv[1]]()
