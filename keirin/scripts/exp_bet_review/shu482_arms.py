"""シュウ極(482) の形・ユーザー提案「三連単100倍超×2点」を自社モデルで測る（2026-10-25）。

台: /tmp/race_type_board.npz（7車・2024-07〜2026-08・vintage walk-forward の確率 PROB と
    予測オッズ PO・当たり目 WIN とその確定払戻 PAY(100円あたり)）。
窓: 探索 2024-07〜2025-12 / 確認 2026-01〜08。予測オッズ odds_tf_n7 の train_end は 2025-12-31
    なので、**予測オッズで目を選ぶ腕は確認窓だけが本番相当**。
予算: 1レース 10,000円。均等は 10,000÷点数を100円単位で切り捨て。ダッチは ∝1/予測オッズ。

指標（482 と同じ物差し）: 表示的中（払戻>購入）・回収率（開催日ブートストラップ95%CI）・
最大3本除く回収率・10万+/50万+/100万+ の頻度。

    .venv/bin/python scripts/exp_bet_review/shu482_arms.py
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

Z = np.load("/tmp/race_type_board.npz", allow_pickle=True)
DATE, PO, PROB, WIN, PAY = Z["DATE"], Z["PO"], Z["PROB"], Z["WIN"], Z["PAY"]
P3 = Z["P3"].astype(float)
CANON = __import__("itertools").permutations(range(1, 8), 3)
CANON = list(CANON)
CAR = np.array(CANON)                                   # (210, 3) 車番
BASE = (WIN >= 0) & np.isfinite(PAY) & Z["OKPRED"] & np.isfinite(PO).all(1) & (PO > 0).all(1)
IDX = np.flatnonzero(BASE)
RNG = np.random.default_rng(20261025)
BUDGET, UNIT = 10_000, 100


def equal(combos: list[int]) -> dict[int, int]:
    """均等配分。"""
    s = (BUDGET // len(combos)) // UNIT * UNIT
    return {c: s for c in combos}


def dutch(i: int, combos: list[int]) -> dict[int, int]:
    """∝1/予測オッズ の100円単位配分（端数は確率の高い順に足す）。"""
    w = 1.0 / PO[i, combos].astype(float)
    units = np.floor(w / w.sum() * (BUDGET // UNIT)).astype(int)
    rest = BUDGET // UNIT - units.sum()
    for j in np.argsort(-w)[:rest]:
        units[j] += 1
    return {c: int(u) * UNIT for c, u in zip(combos, units) if u > 0}


def arm_top_prob(k, min_odds=0.0, bust=False, order="prob", alloc="equal"):
    """予測オッズ >= min_odds の目から、確率順（または EV 順）に k 点。"""
    def f(i):
        ok = PO[i] >= min_odds
        if bust:
            a1 = int(np.argmax(P3[i])) + 1
            ok &= ~(CAR == a1).any(1)
        cand = np.flatnonzero(ok)
        if len(cand) < k:
            return None
        key = PROB[i, cand] if order == "prob" else PROB[i, cand] * PO[i, cand]
        pick = list(cand[np.argsort(-key)[:k]])
        return equal(pick) if alloc == "equal" else dutch(i, pick)
    return f


def arm_plan(target, bust=False):
    """看板枠と同じ: 確率順に Σ(1/予測) <= 予算/T まで積んでダッチ（本番 signboard 分岐の写し）。"""
    cap = BUDGET / target

    def f(i):
        ok = np.ones(210, bool)
        if bust:
            a1 = int(np.argmax(P3[i])) + 1
            ok &= ~(CAR == a1).any(1)
        cand = np.flatnonzero(ok)
        cand = cand[np.argsort(-PROB[i, cand])]
        out, s = [], 0.0
        for c in cand:
            inv = 1.0 / float(PO[i, c])
            if s + inv > cap:
                continue
            out.append(int(c))
            s += inv
        return dutch(i, out) if out else None
    return f


ARMS = {
    "482形: 確率上位3点・均等": arm_top_prob(3),
    "482形: 確率上位2点・均等": arm_top_prob(2),
    "提案: 予測100倍+ 確率上位2点・5,000円": arm_top_prob(2, 100),
    "提案: 予測100倍+ 確率上位3点・3,300円": arm_top_prob(3, 100),
    "提案: 予測100倍+ EV上位2点": arm_top_prob(2, 100, order="ev"),
    "提案: 予測100倍+ 軸1外し上位2点": arm_top_prob(2, 100, bust=True),
    "予測50倍+ 確率上位2点": arm_top_prob(2, 50),
    "予測200倍+ 確率上位2点（100万狙い）": arm_top_prob(2, 200),
    # ユーザーの「より高額で点数を増やす」: 倍率を上げて点数を増やす
    "予測200倍+ 確率上位3点・3,300円": arm_top_prob(3, 200),
    "予測300倍+ 確率上位3点・3,300円": arm_top_prob(3, 300),
    "予測300倍+ 確率上位4点・2,500円": arm_top_prob(4, 300),
    "予測500倍+ 確率上位5点・2,000円": arm_top_prob(5, 500),
    "計画50万ダッチ": arm_plan(500_000),
    "計画50万ダッチ・軸1外し": arm_plan(500_000, bust=True),
    "計画100万ダッチ・軸1外し": arm_plan(1_000_000, bust=True),
}


def run(f):
    """全レースを1商品ずつ組んで (day, bet, payout, n_legs) を返す。"""
    rows = []
    for i in IDX:
        st = f(i)
        if not st:
            continue
        bet = sum(st.values())
        pay = st.get(int(WIN[i]), 0) * PAY[i] / 100.0
        rows.append((DATE[i], bet, pay, len(st), i))
    return pd.DataFrame(rows, columns=["day", "bet", "payout", "n_legs", "i"])


def summarize(df: pd.DataFrame) -> dict:
    """482 と同じ指標。"""
    n, days = len(df), df.day.nunique()
    bet, pay = df.bet.sum(), df.payout.sum()
    g = df.groupby("day")[["bet", "payout"]].sum()
    ix = RNG.integers(0, len(g), size=(2000, len(g)))
    bs = g.payout.values[ix].sum(1) / g.bet.values[ix].sum(1)
    hit = df.payout > df.bet
    top3 = np.sort(df.payout.values)[-3:].sum()
    return {"件/日": round(n / days, 1), "点数": df.n_legs.median(),
            "表示的中%": round(100 * hit.mean(), 2), "回収率%": round(100 * pay / bet, 1),
            "CI": f"[{100*np.percentile(bs, 2.5):.0f},{100*np.percentile(bs, 97.5):.0f}]",
            "最大3本除く%": round(100 * (pay - top3) / bet, 1),
            "的中時中央": int(df.payout[hit].median()) if hit.any() else 0,
            "10万+%": round(100 * (df.payout >= 1e5).mean(), 2),
            "50万+%": round(100 * (df.payout >= 5e5).mean(), 3),
            "100万+%": round(100 * (df.payout >= 1e6).mean(), 3),
            "0円の日%": round(100 * (g.payout == 0).mean(), 1)}


def main() -> None:
    """腕ごと・窓ごとに表を出す。レース選別（人気決着しそうなレースを避ける）も見る。"""
    pd.set_option("display.width", 250)
    wins = {"探索": DATE <= "2025-12-31", "確認(本番相当)": DATE >= "2026-01-01"}
    res = []
    cache = {}
    for name, f in ARMS.items():
        df = run(f)
        cache[name] = df
        for w, m in wins.items():
            x = df[m[df.i.values]]
            res.append({"腕": name, "窓": w, **summarize(x)})
    out = pd.DataFrame(res)
    print(out.to_string(index=False))

    # 人気決着しそうなレースを避ける — 発走前に分かる「予測オッズ上位5目の市場シェア」で5分位
    inv = 1.0 / PO.astype(float)
    srt = -np.sort(-inv, axis=1)
    s5 = srt[:, :5].sum(1) / srt.sum(1)
    for name in ("提案: 予測100倍+ 確率上位2点・5,000円", "計画50万ダッチ・軸1外し"):
        df = cache[name].copy()
        df["s5"] = s5[df.i.values]
        df["q"] = pd.qcut(df.s5, 5, labels=["Q1混戦", "Q2", "Q3", "Q4", "Q5人気集中"])
        df["win"] = np.where(df.day >= "2026-01-01", "確認", "探索")
        print(f"\n## {name} — 予測人気の集中度5分位（Q5=人気決着しそう）")
        rows = []
        for (w, q), x in df.groupby(["win", "q"], observed=True):
            rows.append({"窓": w, "分位": q, "件": len(x), **{k: v for k, v in summarize(x).items()
                                                            if k in ("表示的中%", "回収率%", "CI", "50万+%", "10万+%")}})
        print(pd.DataFrame(rows).to_string(index=False))

    # 482 は「安く当てる商品」と「大穴の商品」を1アカウントで混ぜている（的中の62%が1〜5番人気・
    # 10万+ の61%が21〜100番人気）。自社も混ぜたら3指標で並べるか。
    # 割り当ては**無作為**（レースごとに固定の乱数）。選別で良く見せないため。
    u = np.random.default_rng(7).random(len(DATE))
    base = cache["482形: 確率上位2点・均等"].set_index("i")
    print("\n## 混ぜた場合（大穴の割合 w をレースへ無作為に割り当て・残りは確率上位2点）")
    rows = []
    for ls in ("予測200倍+ 確率上位2点（100万狙い）", "提案: 予測100倍+ 確率上位2点・5,000円"):
        lsd = cache[ls].set_index("i")
        common = base.index.intersection(lsd.index)
        for w in (0.3, 0.45, 0.6):
            pick = u[common] < w
            mix = pd.concat([lsd.loc[common[pick]], base.loc[common[~pick]]]).reset_index()
            for wn, m in wins.items():
                x = mix[m[mix.i.values]]
                rows.append({"大穴": ls[:14], "w": w, "窓": wn, **summarize(x)})
    print(pd.DataFrame(rows).to_string(index=False))
    out.to_csv(sys.argv[1] if len(sys.argv) > 1 else "shu482_arms.csv", index=False)


if __name__ == "__main__":
    main()
