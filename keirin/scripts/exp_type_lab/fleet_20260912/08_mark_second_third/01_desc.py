#!/usr/bin/env python3
"""実態 — 「1着が WT◎○以外で、◎○が2・3着に来る」事象の頻度と配当（2026-09-12）。

◎ = A_prediction_mark==1 / ○ ==2 / △ ==3（`wt_entries.prediction_mark`）。

🔴 本番コードは読まない純集計。買えているかは 02_build.py（本番replay）で測る。
"""
from __future__ import annotations

import itertools
import sys
from collections import Counter
from pathlib import Path
from statistics import median

import numpy as np

REPO = Path("/Users/ysuzuki/GitHub/kiseki/keirin")
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts" / "exp_type_lab"))
import common as C  # noqa: E402

PERMS = C.CANON


def marks(MK, i):
    """(◎, ○, △) の車番。欠測・重複なら None。"""
    m = MK[i]
    h = [c for c in range(1, 8) if int(m[c - 1]) == 1]
    o = [c for c in range(1, 8) if int(m[c - 1]) == 2]
    a = [c for c in range(1, 8) if int(m[c - 1]) == 3]
    if len(h) != 1 or len(o) != 1 or len(a) != 1:
        return None
    return h[0], o[0], a[0]


def main():
    z = C.board()
    MK, P3, PW, WIN, PAY = (z["A_prediction_mark"], z["P3"], z["PW"],
                            z["WIN"], z["PAY"])
    TYPE = np.array([str(v) for v in z["TYPE"]])
    for label, win in (("探索 2024-07〜2025-12", "explore"), ("確認 2026-01〜08", "confirm")):
        idx = C.select(None, win)
        idx = np.array([i for i in idx if TYPE[i] in "ABCDEF"])
        nd = C.days_of(idx)
        n = ok = 0
        cnt = Counter()
        pays = {k: [] for k in ("W_out_both", "W_out_one", "W_out_none",
                                "W_in", "all")}
        by_type = {t: Counter() for t in "ABCDEF"}
        hon_eq_a1 = 0
        for i in idx:
            mm = marks(MK, int(i))
            if mm is None:
                continue
            hon, tai, ana = mm
            w = PERMS[int(WIN[i])]
            p = float(PAY[i]) / 100.0
            order = list(np.argsort(-P3[i]) + 1)
            hon_eq_a1 += (order[0] == hon)
            n += 1
            pays["all"].append(p)
            first_out = w[0] not in (hon, tai)
            k23 = len({hon, tai} & {w[1], w[2]})
            if not first_out:
                key = "W_in"
            elif k23 == 2:
                key = "W_out_both"
            elif k23 == 1:
                key = "W_out_one"
            else:
                key = "W_out_none"
            cnt[key] += 1
            pays[key].append(p)
            by_type[TYPE[i]][key] += 1
            by_type[TYPE[i]]["n"] += 1
        print(f"\n=== {label}  n={n:,}R（印が◎○△そろう回のみ）日数={nd} ===")
        print(f"  ◎ == 指数1位: {hon_eq_a1/n*100:.2f}%")
        print(f"  {'事象':30s} {'件数':>7s} {'割合':>7s} {'件/日':>6s} "
              f"{'払戻中央':>9s} {'100倍+':>7s} {'1000倍+':>8s}")
        for key, lbl in (("W_in", "1着が◎か○（現行の本線）"),
                         ("W_out_both", "1着が◎○以外 ∧ ◎○が2・3着"),
                         ("W_out_one", "1着が◎○以外 ∧ ◎○の1車が2・3着"),
                         ("W_out_none", "1着が◎○以外 ∧ ◎○とも圏外"),
                         ("all", "全体")):
            v = pays[key]
            c = len(v)
            print(f"  {lbl:30s} {c:7,d} {c/n*100:6.2f}% {c/nd:6.2f} "
                  f"{median(v) if v else 0:8,.0f} "
                  f"{sum(1 for x in v if x >= 100)/max(c,1)*100:6.2f}% "
                  f"{sum(1 for x in v if x >= 1000)/max(c,1)*100:7.2f}%")
        print(f"\n  -- 型別（割合%）--")
        print(f"  {'型':4s} {'n':>6s} {'◎か○が1着':>10s} {'◎○以外1着∧◎○2・3着':>20s} "
              f"{'◎○の1車':>9s} {'◎○圏外':>8s}")
        for t in "ABCDEF":
            b = by_type[t]
            m = b["n"]
            if not m:
                continue
            print(f"  {t:4s} {m:6,d} {b['W_in']/m*100:9.2f}% "
                  f"{b['W_out_both']/m*100:19.2f}% {b['W_out_one']/m*100:8.2f}% "
                  f"{b['W_out_none']/m*100:7.2f}%")


if __name__ == "__main__":
    main()
