"""P1: 高額狙い商品の解剖と再設計 — 共通ライブラリ。

本番コード（src/type_lab.py, src/strategy_wt.py）を**そのまま import** して使う。
板は /tmp/race_type_board.npz（vintage walk-forward の P3/PW・予測オッズ odds_tf_n7）。
⚠️ 予測オッズ PO の train_end は 2025-12-31 → 探索窓(2024-07〜2025-12)の PO は in-sample。
"""
from __future__ import annotations
import itertools, os, sys, pickle
import numpy as np

REPO = "/Users/ysuzuki/GitHub/kiseki/keirin"
sys.path.insert(0, REPO)
os.chdir(REPO)

from src import type_lab as TL                      # noqa: E402
from src.strategy_wt import rank_7t3_blend_probs    # noqa: E402

PERMS = list(itertools.permutations(range(1, 8), 3))
COMB3 = list(itertools.combinations(range(1, 8), 3))
BOARD = "/tmp/race_type_board.npz"
CACHE = "/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/5a78b9ec-b8ca-47de-8015-b5191bfb9e70/scratchpad/audit/P1_highpay/races.pkl"

EXPLORE = ("2024-07-01", "2025-12-31")
CONFIRM = ("2026-01-01", "2026-07-15")


def _load_board():
    return np.load(BOARD, allow_pickle=True)


def build_races(limit=None):
    """板から1レース1dictへ。probs は本番 rank_7t3_blend_probs（ライン情報込み）。"""
    z = _load_board()
    KEY = [str(k) for k in z["KEY"]]
    DATE = [str(d) for d in z["DATE"]]
    RT = [str(r) for r in z["RTYPE"]]
    GR = [str(g) for g in z["GRADE"]]
    VN = [str(v) for v in z["VENUE"]]
    P3, PW, PO, PROB = z["P3"], z["PW"], z["PO"], z["PROB"]
    WIN, PAY = z["WIN"], z["PAY"]
    TRIO_ODDS, TRIO_PO, TRIO_WIN, TRIO_PAY = (z["TRIO_ODDS"], z["TRIO_PO"],
                                              z["TRIO_WIN"], z["TRIO_PAY"])
    LG, ST = z["LG"], z["ST"]
    LPOS, RPT, BH = z["A_line_pos"], z["A_race_point"], z["BEHIND"]
    DAYI = z["DAYI"]
    TYPE, AXS, GAP, ARARE = z["TYPE"], z["AXIS_SUM"], z["GAP"], z["ARARE"]
    n = len(KEY) if limit is None else min(limit, len(KEY))
    out = []
    for i in range(n):
        cars = list(range(1, 8))
        p3 = {c: float(P3[i, c - 1]) for c in cars}
        pw = {c: float(PW[i, c - 1]) for c in cars}
        lg = {c: str(LG[i, c - 1]) for c in cars}
        lp = {c: int(LPOS[i, c - 1]) if np.isfinite(LPOS[i, c - 1]) else 0 for c in cars}
        st = {c: str(ST[i, c - 1]) for c in cars}
        rp = {c: float(RPT[i, c - 1]) for c in cars}
        bh = {c: float(BH[i, c - 1]) for c in cars}
        shape = TL.race_shape(p3, lg, lp, st, rp, bh, int(DAYI[i]), win_probs=pw)
        if shape is None:
            continue
        probs = rank_7t3_blend_probs(cars, pw, p3, line_group=lg, line_pos=lp)
        po = {PERMS[t]: float(PO[i, t]) for t in range(210)
              if np.isfinite(PO[i, t]) and PO[i, t] > 0}
        # 三連複
        tpo = {frozenset(COMB3[j]): float(TRIO_PO[i, j]) for j in range(35)
               if np.isfinite(TRIO_PO[i, j]) and TRIO_PO[i, j] > 0}
        tod = {frozenset(COMB3[j]): float(TRIO_ODDS[i, j]) for j in range(35)
               if np.isfinite(TRIO_ODDS[i, j]) and TRIO_ODDS[i, j] > 0}
        tprob = {}
        for k, v in probs.items():
            f = frozenset(k)
            tprob[f] = tprob.get(f, 0.0) + v
        out.append(dict(
            key=KEY[i], date=DATE[i], rtype=RT[i], grade=GR[i], venue=VN[i],
            dayi=int(DAYI[i]), type=str(TYPE[i]), axis_sum=float(AXS[i]),
            gap=float(GAP[i]), arare=int(ARARE[i]), shape=shape,
            probs=probs, po=po, win=PERMS[int(WIN[i])] if WIN[i] >= 0 else None,
            pay=float(PAY[i]),
            board_prob={PERMS[t]: float(PROB[i, t]) for t in range(210)},
            tprob=tprob, tpo=tpo, tod=tod,
            twin=frozenset(COMB3[int(TRIO_WIN[i])]) if TRIO_WIN[i] >= 0 else None,
            tpay=float(TRIO_PAY[i]),
        ))
    return out


def get_races():
    if os.path.exists(CACHE):
        with open(CACHE, "rb") as f:
            return pickle.load(f)
    r = build_races()
    with open(CACHE, "wb") as f:
        pickle.dump(r, f, protocol=4)
    return r


def window(races, w):
    lo, hi = w
    return [r for r in races if lo <= r["date"] <= hi]
