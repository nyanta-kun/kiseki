#!/usr/bin/env python3
"""型別の商品設計ラボ 共通ライブラリ（2026-08-27）。

台: /tmp/race_type_board.npz（`scripts/build_race_type_board.py` が作る）
  7車 36,427R・2024-07-01〜2026-08-25・**vintage walk-forward の p3/pw**

🔴 検証の作法（CLAUDE.md「測る前に本番コードを読む」）
  - 予算は 1レース 10,000円。既定の配分は**ダッチング（∝1/予測オッズ）**＝本番の
    `RANK_CONFIGS[...]["tilt_stakes"]`。均等にしたい場合は明示すること。
  - 入稿ゲートは2つ。**通してから比べる**（通す前の倍率中央は全部ずれる）:
      ① `MIN_POINT_ODDS = 2.0`  買い目の1点でも**予測**オッズ<2.0 ならレースごと見送り
      ② `MIN_MEAN_PAYOUT = 20,000`  入稿する買い目の**想定払戻の平均**<=2万円なら見送り
    （`MIN_EXPECTED_PAYOUT_BY_RANK` は 7C/7S だけなのでここでは使わない）
  - 判断指標は **件/日・表示的中(ガミ除く)・払戻中央・2倍+/日・ガミ率**。
    🔴 **ROI で採否を決めない**（この層は ±2.5pt に収めるのに約15.6年かかる）。
  - 窓は 探索 2024-07〜2025-12 / 確認 2026-01〜2026-08。
    ⚠️ 予測オッズモデル `odds_tf_n7` の train_end は 2025-12-31 なので
       **オッズを使う数字は確認窓(2026)が本番相当**。探索窓は in-sample。
"""
from __future__ import annotations

import itertools
from statistics import median

import numpy as np

BUDGET, UNIT = 10_000, 100
MIN_POINT_ODDS = 2.0
MIN_MEAN_PAYOUT = 20_000

CANON = list(itertools.permutations(range(1, 8), 3))          # 三連単 210
CANON3 = list(itertools.combinations(range(1, 8), 3))         # 三連複 35
C3IDX = {frozenset(c): i for i, c in enumerate(CANON3)}
CIDX = {c: i for i, c in enumerate(CANON)}

_Z = None


def board():
    global _Z
    if _Z is None:
        _Z = np.load("/tmp/race_type_board.npz", allow_pickle=True)
    return _Z


def select(type_label: str | None = None, window: str = "all",
           agree: bool | None = None) -> np.ndarray:
    """条件に合うレースの index 配列。

    type_label: "A".."F" / None=全部    window: "explore"|"confirm"|"all"
    agree: True=モデル上位2車が公式印◎○と一致 / False=不一致 / None=問わない
    """
    z = board()
    m = (z["TYPE"] != "") & (z["TRIO_WIN"] >= 0) & np.isfinite(z["TRIO_PAY"]) & z["OKPRED"]
    if type_label:
        m &= z["TYPE"] == type_label
    if agree is not None:
        m &= z["AGREE"] == agree
    d = z["DATE"]
    if window == "explore":
        m &= (d >= "2024-07-01") & (d <= "2025-12-31")
    elif window == "confirm":
        m &= (d >= "2026-01-01")
    return np.flatnonzero(m)


def p3_order(i: int) -> list[int]:
    """3着内率の降順（車番 1..7）。"""
    return list(np.argsort(-board()["P3"][i]) + 1)


def pw_order(i: int) -> list[int]:
    return list(np.argsort(-board()["PW"][i]) + 1)


# ───────────────────────── 三連複 ─────────────────────────

def trio_stakes(i: int, combos: list[frozenset], tilt: bool = True) -> dict | None:
    """買い目 -> 賭け金。tilt=True なら 1/予測オッズ に比例（本番と同じ）。
    予測オッズが1点でも欠けたら None（本番は「出す側へ倒す」が、検証では除外する）。"""
    z = board()
    po = [z["TRIO_PO"][i][C3IDX[c]] for c in combos]
    if any((not np.isfinite(x)) or x <= 0 for x in po):
        return None
    w = [1.0 / x for x in po] if tilt else [1.0] * len(combos)
    n_units = BUDGET // UNIT
    if n_units < len(combos):
        return None
    tot = sum(w)
    units = [1] * len(combos)
    rest = n_units - len(combos)
    for j, x in enumerate(w):
        units[j] += int(rest * x / tot)
    while sum(units) < n_units:
        j = min(range(len(units)), key=lambda k: units[k] / max(w[k], 1e-12))
        units[j] += 1
    return {c: u * UNIT for c, u in zip(combos, units)}


def trio_gate(i: int, stakes: dict) -> bool:
    """入稿ゲートを通るか（①1点でも予測<2.0倍 ②平均想定払戻<=2万円）。"""
    z = board()
    po = {c: float(z["TRIO_PO"][i][C3IDX[c]]) for c in stakes}
    if any((not np.isfinite(v)) or v <= 0 for v in po.values()):
        return False
    if min(po.values()) < MIN_POINT_ODDS:
        return False
    mean = sum(stakes[c] * po[c] for c in stakes) / len(stakes)
    return mean > MIN_MEAN_PAYOUT


def trio_result(i: int, stakes: dict) -> tuple[float, float, float]:
    """(投資, 払戻, 平均想定払戻)。払戻は**確定**三連複オッズ。"""
    z = board()
    inv = float(sum(stakes.values()))
    w = frozenset(CANON3[int(z["TRIO_WIN"][i])])
    pay = float(stakes[w] * z["TRIO_ODDS"][i][C3IDX[w]]) if w in stakes else 0.0
    mean = sum(stakes[c] * float(z["TRIO_PO"][i][C3IDX[c]]) for c in stakes) / len(stakes)
    return inv, pay, mean


# ───────────────────────── 三連単 ─────────────────────────

def tf_stakes(i: int, combos: list[tuple], equal: bool = True) -> dict:
    """三連単の賭け金。既定は均等（7T1/7T3 と同じ）。"""
    k = len(combos)
    s = BUDGET // k // UNIT * UNIT
    return {c: s for c in combos}


def tf_gate(i: int, stakes: dict) -> bool:
    """三連単は平均払戻ゲートの対象外（本番も三連単経路は除外）。
    ただし予測オッズが揃わない点があれば除外する。"""
    z = board()
    return all(np.isfinite(z["PO"][i][CIDX[c]]) and z["PO"][i][CIDX[c]] > 0 for c in stakes)


def tf_result(i: int, stakes: dict) -> tuple[float, float]:
    z = board()
    inv = float(sum(stakes.values()))
    w = CANON[int(z["WIN"][i])]
    pay = float(stakes[w] / 100.0 * z["PAY"][i]) if w in stakes else 0.0
    return inv, pay


# ───────────────────────── 集計 ─────────────────────────

def summarize(recs: list[dict], n_days_all: int | None = None) -> dict:
    """recs: [{date, inv, pay, mean(optional), k}] を判断指標へ。"""
    if not recs:
        return dict(n=0)
    days = {r["date"] for r in recs}
    nd = len(days)
    inv = sum(r["inv"] for r in recs)
    pay = sum(r["pay"] for r in recs)
    hits = [r for r in recs if r["pay"] > 0]
    gami = [r for r in hits if r["pay"] < r["inv"]]
    ratios = sorted(r["pay"] / r["inv"] for r in hits)
    pays = sorted(r["pay"] for r in hits)
    means = sorted(r["mean"] for r in recs if r.get("mean"))
    return dict(
        n=len(recs), perday=len(recs) / (n_days_all or nd),
        k=sum(r["k"] for r in recs) / len(recs),
        hit=len(hits) / len(recs) * 100,
        gami=len(gami) / len(hits) * 100 if hits else 0.0,
        shown=(len(hits) - len(gami)) / len(recs) * 100,          # 表示的中（ガミ除く）
        med_pay=median(pays) if pays else 0.0,
        med_ratio=median(ratios) if ratios else 0.0,
        med_mean=median(means) if means else 0.0,
        two_per_day=sum(1 for x in ratios if x >= 2) / (n_days_all or nd),
        big_per_day=sum(1 for p in pays if p >= 100_000) / (n_days_all or nd),
        roi=pay / inv * 100 if inv else 0.0,
    )


def days_of(idx: np.ndarray) -> int:
    return len(set(board()["DATE"][idx]))


HEAD = ("  {:26s} {:>6s} {:>5s} {:>7s} {:>6s} {:>8s} {:>9s} {:>10s} {:>8s} {:>7s}"
        .format("腕", "件/日", "点数", "的中%", "ガミ%", "表示的中%", "払戻中央", "平均払戻中央",
                "2倍+/日", "ROI%"))


def line(name: str, s: dict) -> str:
    if not s.get("n"):
        return f"  {name:26s}  (該当なし)"
    return (f"  {name:26s} {s['perday']:6.2f} {s['k']:5.2f} {s['hit']:7.2f} {s['gami']:6.2f}"
            f" {s['shown']:8.2f} {s['med_pay']:9,.0f} {s['med_mean']:10,.0f}"
            f" {s['two_per_day']:8.2f} {s['roi']:7.1f}")


# ═══════════════════════════════════════════════════════════════════════════
# 信頼度傾斜配分（2026-08-27・ユーザー提案）
#
# > ダッチングは的中率が高ければ良さがあるが、今の的中率だとじわじわ負ける形になる。
# > 推奨買い目における信頼度で傾斜をつけ、**一番期待していないところはガミにならない
# > 程度**を配分し、残りは自信度で傾斜配分にする。
#
# 実装:
#   ① 各点に「当たっても投資を下回らない最低額」floor_i = ceil(予算/予測オッズ_i) を置く
#      → 予測どおりなら**ガミが構造的に起きない**（ダッチと同じ性質）
#   ② 残り (予算 − Σfloor) を**確率に比例**して配る
#      → 期待していない点（高オッズ・低確率）は floor のまま＝元返し近辺、
#        自信のある点ほど厚くなり**当たったときの払戻が伸びる**
#   組めない条件は Σ(1/予測オッズ) > 1（ダッチの Σ<0.5 よりずっと緩い）
#
# 🔴 ダッチとの違いは「払戻を揃える」か「確率へ寄せる」か。ダッチは
#    払戻 = 予算/Σ(1/オッズ) で全点同額、こちらは**当たりやすい点ほど大きい**。
# ⚠️ 平均想定払戻ゲートは**算術平均**なので、傾斜すると高オッズ側の裾が伸びて
#    ダッチより通りやすくなる。通過率が変わることを必ず併記すること。
# ═══════════════════════════════════════════════════════════════════════════

def confidence_stakes(odds: dict, probs: dict, budget: int = BUDGET,
                      unit: int = UNIT, floor_mult: float = 1.0) -> dict | None:
    """信頼度傾斜配分。odds/probs は {買い目: 値}。組めなければ None。

    floor_mult: 床の余裕。1.0 = ちょうど元返し / 1.2 = 2割増しを保証。
    """
    keys = list(odds)
    if not keys:
        return None
    floor = {}
    for c in keys:
        o = odds[c]
        if not o or o <= 0:
            return None
        f = int(np.ceil(budget * floor_mult / o / unit)) * unit
        floor[c] = max(f, unit)
    if sum(floor.values()) > budget:
        return None
    rest_units = (budget - sum(floor.values())) // unit
    tot = sum(max(probs.get(c, 0.0), 0.0) for c in keys)
    add = {c: 0 for c in keys}
    if rest_units > 0 and tot > 0:
        share = {c: rest_units * max(probs.get(c, 0.0), 0.0) / tot for c in keys}
        for c in keys:
            add[c] = int(share[c])
        while sum(add.values()) < rest_units:
            c = max(keys, key=lambda x: share[x] - add[x])
            add[c] += 1
    elif rest_units > 0:
        add[keys[0]] = rest_units
    return {c: floor[c] + add[c] * unit for c in keys}


def trio_probs(i: int, combos: list[frozenset]) -> dict:
    """三連複の買い目確率（三連単板の PROB を6順列ぶん足す）。"""
    z = board()
    out = {}
    for c in combos:
        s = 0.0
        for perm in itertools.permutations(sorted(c)):
            s += float(z["PROB"][i][CIDX[perm]])
        out[c] = s
    return out


def tf_probs(i: int, combos: list[tuple]) -> dict:
    z = board()
    return {c: float(z["PROB"][i][CIDX[c]]) for c in combos}
