#!/usr/bin/env python3
"""30万円超の一撃をどう作るか — 高額枠の買い方を掃引する（2026-09-06）。

母集団 = 高額枠が実際に置かれるレース
        （日次上限の下位50%［`axis_sum` 降順・決勝系は枠外］ ∧ 型B/C/D ∧ 7車）
窓     = 探索 2024-07〜2025-12（予測オッズは in-sample）/ 確認 2026-01〜08（本番相当）
配分   = ダッチ（∝1/予測オッズ）・1レース 10,000円・100円単位
ゲート = ①1点でも予測 < 2.0倍 ②想定払戻の平均 <= 20,000円 → そのレースは見送り

    python3 scripts/exp_type_lab/big30.py [sweep|pools|menu]

記録: `docs/highpay_5slots_2026_09_06.md` 9章
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from scripts.exp_type_lab.common import (BUDGET, CANON, CIDX, MIN_MEAN_PAYOUT,   # noqa: E402
                                         MIN_POINT_ODDS, UNIT, board)

Z = board()
DATE, TYPE, PO, PROB = Z["DATE"], Z["TYPE"], Z["PO"], Z["PROB"]
WIN, PAY, RT = Z["WIN"], Z["PAY"], Z["RTYPE"]
AXIS, P3 = Z["AXIS_SUM"].astype(float), Z["P3"].astype(float)

BASE = (TYPE != "") & (WIN >= 0) & np.isfinite(PAY) & Z["OKPRED"]

# ── 日次上限（`DAILY_CAP_RACE_FRACTION=0.5`）の並びを再現して「捨てる側」を取る ──
# ⚠️ 本番の並びは `(2×軸信頼順位 + 実力伯仲順位)/3` で波ごとに掛かる。ここは
#    `axis_sum` 降順・日単位の**近似**。買い方どうしの比較が目的なので許容する。
_byday: dict[str, list[int]] = defaultdict(list)
for _i in np.flatnonzero(BASE):
    _byday[str(DATE[_i])].append(int(_i))
DROP = np.zeros(len(DATE), bool)
for _d, _ix in _byday.items():
    _t = [i for i in _ix if "決勝" not in str(RT[i])]
    _t.sort(key=lambda i: -AXIS[i])
    for _j, _i in enumerate(_t):
        DROP[_i] = _j >= max(1, int(len(_t) * 0.5))

POOL = BASE & DROP & np.isin(TYPE, ["B", "C", "D"])
W = {"探索 2024-07〜2025-12": (DATE >= "2024-07-01") & (DATE <= "2025-12-31"),
     "確認 2026-01〜08": (DATE >= "2026-01-01")}


def dutch(i: int, combos):
    """∝1/予測オッズ の100円単位配分。組めなければ None（本番 `allocate` と同型）。"""
    po = [float(PO[i][CIDX[c]]) for c in combos]
    if any((not np.isfinite(o)) or o <= 0 for o in po):
        return None
    n_units = BUDGET // UNIT
    if n_units < len(combos):
        return None
    w = [1.0 / o for o in po]
    tot, units = sum(w), [1] * len(combos)
    rest = n_units - len(combos)
    for j, x in enumerate(w):
        units[j] += int(rest * x / tot)
    while sum(units) < n_units:
        j = min(range(len(units)), key=lambda k: units[k] / max(w[k], 1e-12))
        units[j] += 1
    return {c: u * UNIT for c, u in zip(combos, units)}


def prob_order(i: int, lo=0.0, hi=1e9, exclude_first=False):
    """確率降順の買い目。`exclude_first` で **軸1（p3 最上位）を含む目を全部落とす**。"""
    a1 = int(np.argmax(P3[i])) + 1
    out = []
    for t in np.argsort(-PROB[i]):
        c = CANON[t]
        if exclude_first and a1 in c:
            continue
        v = float(PO[i][t])
        if np.isfinite(v) and v > 0 and lo <= v <= hi:
            out.append(c)
    return out


def sign_from(i: int, target: int, cands):
    """確率順に `Σ(1/予測オッズ) <= 予算/target` まで積む（本番 `signboard` と同じ）。"""
    cap, s, out = BUDGET / float(target), 0.0, []
    for c in cands:
        o = float(PO[i][CIDX[c]])
        if s + 1.0 / o > cap:
            continue
        out.append(c)
        s += 1.0 / o
    return out or None


ARMS = {
    "計画15万（現行）": lambda i: sign_from(i, 150_000, prob_order(i, hi=600.0)),
    "計画25万": lambda i: sign_from(i, 250_000, prob_order(i, hi=600.0)),
    "計画40万": lambda i: sign_from(i, 400_000, prob_order(i, hi=600.0)),
    "計画50万": lambda i: sign_from(i, 500_000, prob_order(i, hi=600.0)),
    "軸1外し 上位3点": lambda i: (prob_order(i, exclude_first=True)[:3] or None),
    "軸1外し 上位5点": lambda i: (prob_order(i, exclude_first=True)[:5] or None),
    "軸1外し 上位8点": lambda i: (prob_order(i, exclude_first=True)[:8] or None),
    "軸1外し×計画25万": lambda i: sign_from(i, 250_000, prob_order(i, hi=600.0, exclude_first=True)),
    "軸1外し×計画40万": lambda i: sign_from(i, 400_000, prob_order(i, hi=600.0, exclude_first=True)),
    "軸1外し×計画60万": lambda i: sign_from(i, 600_000, prob_order(i, hi=600.0, exclude_first=True)),
    "予測300倍+ 上位4点": lambda i: (prob_order(i, lo=300.0)[:4] or None),
}


def play(i: int, arm):
    """1レース1商品を組んで採点する。ゲートに掛かれば None。"""
    combos = arm(i)
    if not combos:
        return None
    st = dutch(i, combos)
    if st is None:
        return None
    po = {c: float(PO[i][CIDX[c]]) for c in combos}
    if min(po.values()) < MIN_POINT_ODDS:
        return None
    if sum(st[c] * po[c] for c in combos) / len(combos) <= MIN_MEAN_PAYOUT:
        return None
    w = CANON[int(WIN[i])]
    return dict(date=str(DATE[i]), k=len(combos), inv=float(sum(st.values())),
                pay=float(st[w] / 100.0 * PAY[i]) if w in st else 0.0)


def _stat(rs):
    f = lambda t: sum(1 for x in rs if x["pay"] >= t) / len(rs)
    hits = [x["pay"] for x in rs if x["pay"] > 0]
    return dict(k=float(np.mean([x["k"] for x in rs])),
                shown=sum(1 for x in rs if x["pay"] > x["inv"]) / len(rs),
                roi=sum(x["pay"] for x in rs) / sum(x["inv"] for x in rs),
                med=float(np.median(hits)) if hits else 0.0,
                b1=f(100_000), b3=f(300_000), b5=f(500_000))


def sweep(pool=POOL, arms=ARMS):
    for wn, m in W.items():
        idx = np.flatnonzero(pool & m)
        print("\n" + "=" * 104)
        print(f"■ {wn}   母集団 {len(idx):,}R   *** 5本/日 に換算した月次回数 ***")
        print("=" * 104)
        print(f"{'買い方':20s} {'点数':>5s} {'表示的中%':>8s} {'ROI%':>6s} {'払戻中央':>10s} "
              f"{'10万/月':>7s} {'30万/月':>7s} {'50万/月':>7s}")
        for an, arm in arms.items():
            rs = [x for x in (play(int(i), arm) for i in idx) if x]
            if len(rs) < 200:
                continue
            s = _stat(rs)
            print(f"{an:20s} {s['k']:5.2f} {s['shown']*100:7.2f}% {s['roi']*100:5.1f}% "
                  f"{s['med']:10,.0f} {s['b1']*150:7.2f} {s['b3']*150:7.2f} {s['b5']*150:7.2f}")


def pools():
    """母集団を替えても向きが変わらないかを見る（`軸1外し` は変わる・計画払戻は変わらない）。"""
    sub = {k: ARMS[k] for k in ("計画15万（現行）", "計画40万", "軸1外し 上位5点", "軸1外し×計画40万")}
    for pn, pm in {"型BCD・捨てる側": POOL,
                   "型BCD・残す側": BASE & ~DROP & np.isin(TYPE, ["B", "C", "D"]),
                   "型A・捨てる側": BASE & DROP & (TYPE == "A"),
                   "型E・捨てる側": BASE & DROP & (TYPE == "E"),
                   "型F・捨てる側": BASE & DROP & (TYPE == "F")}.items():
        print(f"\n\n#### 母集団: {pn}")
        sweep(pm, sub)


#: 実売の断面（2026-08-20〜09-05・806件）。高額枠を足したときの見え方を出すため。
LIVE = dict(n=47.4, shown=0.2531, roi=0.696, b1=0.118)
MENUS = {
    "① 計画15万 ×5（PR#477）": [("計画15万（現行）", 5)],
    "② 15万×3 ＋ 軸1外し×40万×2": [("計画15万（現行）", 3), ("軸1外し×計画40万", 2)],
    "③ 軸1外し×40万 ×5": [("軸1外し×計画40万", 5)],
    "④ 軸1外し8点×3 ＋ 軸1外し×60万×2": [("軸1外し 上位8点", 3), ("軸1外し×計画60万", 2)],
    "⑤ 軸1外し8点 ×5": [("軸1外し 上位8点", 5)],
}


def menu():
    for wn, m in W.items():
        idx = np.flatnonzero(POOL & m)
        S = {a: _stat([x for x in (play(int(i), ARMS[a]) for i in idx) if x])
             for a in {a for mix in MENUS.values() for a, _ in mix}}
        print(f"\n■ {wn} の1商品あたり実測を、実売のラインナップ"
              f"（{LIVE['n']}件/日・表示的中 {LIVE['shown']*100:.2f}%・ROI {LIVE['roi']*100:.1f}%）へ足す")
        print(f"{'メニュー':30s} {'件/日':>5s} {'表示的中':>7s} {'ROI':>6s} "
              f"{'10万+/月':>8s} {'30万+/月':>8s} {'50万+/月':>8s}")
        print(f"{'（高額枠なし）':30s} {LIVE['n']:5.1f} {LIVE['shown']*100:6.2f}% "
              f"{LIVE['roi']*100:5.1f}% {LIVE['b1']*30:8.2f} {0.0:8.2f} {0.0:8.2f}")
        for name, mix in MENUS.items():
            n = LIVE["n"] + sum(k for _, k in mix)
            sh = (LIVE["n"] * LIVE["shown"] + sum(S[a]["shown"] * k for a, k in mix)) / n
            ro = (LIVE["n"] * LIVE["roi"] + sum(S[a]["roi"] * k for a, k in mix)) / n
            b1 = LIVE["b1"] + sum(S[a]["b1"] * k for a, k in mix)
            b3 = sum(S[a]["b3"] * k for a, k in mix)
            b5 = sum(S[a]["b5"] * k for a, k in mix)
            print(f"{name:30s} {n:5.1f} {sh*100:6.2f}% {ro*100:5.1f}% "
                  f"{b1*30:8.2f} {b3*30:8.2f} {b5*30:8.2f}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "sweep"
    print(f"母集団（型B/C/D ∧ 捨てる側 ∧ 7車）: "
          + " / ".join(f"{k} {int((POOL & m).sum()):,}R" for k, m in W.items()))
    {"sweep": sweep, "pools": pools, "menu": menu}[cmd]()
