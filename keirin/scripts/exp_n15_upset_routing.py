#!/usr/bin/env python3
"""N-15 波乱スコアを 7S ↔ 7M1 の**振り分け軸**に使えるか（2026-08-21）。

## 何を測るか

`RANK_ORDER` は 7S > 7M1 なので、両方が候補を持つレース（＝競合）は**常に 7S**が
取る。ここを **レース単位の波乱スコア（`lgbm_upset_screen`）で振り分ける**。

    波乱スコア >= thr  → 7M1 を出す（荒れる想定＝高配当が取れる側）
    それ未満           → 7S  を出す（堅い想定＝当たる側）

## なぜ収益ではなく KPI で測るのか

[[keirin_irregular_layer_screening_2026_08_20]] で「波乱スコアで穴を狙って儲ける」
経路は7車では閉じている（着地 ratio 0.946＝控除率の壁の下）。しかし
**分離そのものは7車でも有意**（Δ+0.142・月次一貫性94%）。
収益化には足りない大きさの信号でも、**どちらの商品を出すかの振り分け**になら使える。

[[keirin_n7_gami_cut_predicted_odds_2026_08_21]] の実測で 7S と 7M1 は逆を向く:

    2倍以上で的中  7S 12.98% > 7M1 10.89%   （7S が +2.10pt）
    5倍以上で的中  7S  1.58% < 7M1  5.92%   （7M1 が +4.34pt）

＝ 振り分けが当たれば**両方を同時に取れる**可能性がある。

## 🔴 母集団は「競合レース」だけ＝件数は動かない

振り分けは同じレース集合の中でどちらの商品を出すかを決める操作なので、
**比較は常に同件数**になる。[[keirin_race_selection_meta_2026_08_18]] の
「改善の正体は少なく賭けただけ」という型は構造的に起こらない。

## 🔴 事前登録した採用ライン（結果を見る前に確定。事後に動かさない）

掃引窓 2025-01〜2025-12 で閾値を選び、確認窓 2026-01〜2026-08 で判定する。
`always 7S`（＝現行）を基準に、**確認窓で以下をすべて満たしたときだけ採用**:

    (1) 「5倍以上で的中」  >= +1.0pt
    (2) 「2倍以上で的中」  >= -0.5pt （落とさない）
    (3) (1)(2) の符号が掃引窓と一致する

ROI は**監視のみ**（採否に使わない）。7M1 は的中が低く払戻の分散が大きいため
ROI を ±2.5pt に収めるのに約15.6年かかる（`RANK_7M1_P3_SUM_MAX` 定義部）。

## 🔴 波乱スコアは vintage で当てる

本番 `lgbm_upset_screen` は全期間学習なので過去へ当てると model-vintage
look-ahead になる。本検証は `lgbm_upset_screen_n15v2412`
（`--train-end 2024-12-31`）だけを使い、2025 年以降のみを評価する。

DB は読み取りのみ。

使い方:
    PYTHONPATH=. .venv/bin/python scripts/exp_n15_upset_routing.py \
        --model lgbm_upset_screen_n15v2412
"""
from __future__ import annotations

import argparse
import pickle
import random
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.database import get_connection  # noqa: E402
from src.preprocessing.upset_features import (  # noqa: E402
    build_upset_row, feature_vector,
)

SWEEP = ("2025-01-01", "2025-12-31")
CONFIRM = ("2026-01-01", "2026-08-20")


def load_picks(d1: str, d2: str) -> dict[str, dict[str, dict]]:
    """競合レース（7S と 7M1 の両方が実際に賭けている）だけを返す。"""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT split_part(race_key,'#',1) AS rk, race_date, rank, "
            "       bet_amount, payout "
            "FROM picks_history "
            "WHERE race_date BETWEEN ? AND ? AND bet_amount > 0 "
            "  AND rank IN ('RANK_7S','RANK_7M1')", (d1, d2))
        by: dict[str, dict[str, dict]] = defaultdict(dict)
        for r in cur:
            by[r["rk"]][r["rank"]] = dict(
                date=r["race_date"], bet=int(r["bet_amount"] or 0),
                pay=int(r["payout"] or 0))
    return {rk: v for rk, v in by.items()
            if "RANK_7S" in v and "RANK_7M1" in v and v["RANK_7S"]["bet"] > 0
            and v["RANK_7M1"]["bet"] > 0}


def load_scores(race_keys: set[str], model) -> dict[str, float]:
    """競合レースの波乱スコア。特徴は本番と同一の正本を通す。"""
    if not race_keys:
        return {}
    keys = list(race_keys)
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT e.race_key, r.race_date, r.n_entries, r.grade, r.race_type,
                   r.day_index, r.distance, r.start_at,
                   e.frame_no, e.race_point, e.line_group, e.line_size,
                   e.style, e.player_class, e.s_count, e.b_count,
                   e.first_rate, e.third_rate, e.prediction_mark, e.finish_order,
                   v.bank_length, v.is_indoor
            FROM wt_entries e
            JOIN wt_races r USING(race_key)
            LEFT JOIN venue_info v ON v.venue_code = r.venue_id
            WHERE r.cancel=0 AND e.race_key = ANY(?)
        """, (keys,))
        by_race: dict[str, list[dict]] = defaultdict(list)
        for e in cur:
            by_race[e["race_key"]].append(dict(e))

    out: dict[str, float] = {}
    for rk, ents in by_race.items():
        race = {k: ents[0].get(k) for k in
                ("n_entries", "grade", "race_type", "day_index", "distance",
                 "start_at", "bank_length", "is_indoor")}
        feat = build_upset_row(ents, race)
        if feat is None:
            continue
        out[rk] = float(model.predict(
            np.array([feature_vector(feat)], dtype=float))[0])
    return out


class Result:
    """振り分けた結果の KPI。母集団が同じなので率で直接比べてよい。"""

    def __init__(self, ratios: list[float], bets: list[int], pays: list[int]):
        self.n = len(ratios)
        self.ratios = ratios
        self.bet = sum(bets)
        self.pay = sum(pays)

    def kpi(self, x: float) -> float:
        if not self.n:
            return 0.0
        return 100.0 * sum(1 for r in self.ratios if r >= x) / self.n

    @property
    def roi(self) -> float:
        return 100.0 * self.pay / self.bet if self.bet else 0.0


def route(picks: dict, scores: dict, thr: float | None, force: str | None = None,
          reverse: bool = False):
    """thr 以上なら 7M1、未満なら 7S。`reverse=True` で向きを逆にする。

    🔴 **`reverse` は事後に足した向き**（2026-08-21）。当初の仮説は
    「荒れる想定のレースを 7M1 へ」だったが、四分位別に見ると
    **7S 自身が高スコア帯で強くなる**一方 7M1 の ≥5倍 優位は全帯で平坦だった。
    ＝ 低スコア帯（7S が弱い）を 7M1 へ回すほうが筋が通る。
    向きをデータから選んだので、**確認窓だけが正直な読み**になる。
    """
    ratios, bets, pays, n_m1 = [], [], [], 0
    for rk, v in picks.items():
        # 🔴 `force`（ベースライン）でも **スコアのあるレースだけ**に揃える。
        #    揃えないと、スコア欠損が出た日に母集団の違う2つを引き算して
        #    「効果」と読む構造になる（2026-08-21 の監査指摘。当時は
        #    カバレッジ 2,727/2,727 = 100% で実害ゼロだった）。
        if rk not in scores:
            continue
        if force:
            rank = force
        else:
            s = scores.get(rk)
            if s is None:
                continue
            hi = s >= thr
            rank = "RANK_7M1" if (hi != reverse) else "RANK_7S"
        if rank == "RANK_7M1":
            n_m1 += 1
        d = v[rank]
        bets.append(d["bet"])
        pays.append(d["pay"])
        ratios.append(d["pay"] / d["bet"] if d["bet"] else 0.0)
    return Result(ratios, bets, pays), n_m1


def boot_diff(picks: dict, scores: dict, thr: float, x: float,
              n_boot: int = 2000, seed: int = 0) -> tuple[float, float]:
    """`always 7S` との KPI 差の 95%CI（レース単位のペアで再標本化）。"""
    rng = random.Random(seed)
    keys = [rk for rk in picks if rk in scores]
    pairs = []
    for rk in keys:
        v = picks[rk]
        r_s = v["RANK_7S"]["pay"] / v["RANK_7S"]["bet"]
        r_m = v["RANK_7M1"]["pay"] / v["RANK_7M1"]["bet"]
        chosen = r_m if scores[rk] >= thr else r_s
        pairs.append((1.0 if chosen >= x else 0.0, 1.0 if r_s >= x else 0.0))
    diffs = []
    for _ in range(n_boot):
        sample = [pairs[rng.randrange(len(pairs))] for _ in range(len(pairs))]
        diffs.append(100.0 * (sum(a for a, _ in sample) - sum(b for _, b in sample))
                     / len(sample))
    diffs.sort()
    return diffs[int(0.025 * n_boot)], diffs[int(0.975 * n_boot)]



def null_same_fraction(picks: dict, scores: dict, thr: float, x: float,
                       n_draw: int = 4000, seed: int = 0,
                       reverse: bool = False) -> tuple[float, float, float]:
    """🔴 **正しい帰無仮説**: 同じ割合を**ランダムに** 7M1 へ振り分けた場合の KPI。

    `always 7S` との比較だけでは足りない。振り分けは 7S と 7M1 の混合比を
    動かす操作でもあるので、**スコアに情報が無くても** 7M1 側へ倒した割合に
    比例して「5倍以上で的中」は上がり「2倍以上で的中」は下がる。
    その分を引かないと、混合比を動かしただけの量を「振り分けが効いた」と
    読んでしまう（[[keirin_race_selection_meta_2026_08_18]] の
    「改善の正体は少なく賭けただけ」と同型）。

    返り値: (実測 KPI, 帰無の平均, 帰無分布での上側パーセンタイル)
    """
    keys = [rk for rk in picks if rk in scores]
    pair = []
    for rk in keys:
        v = picks[rk]
        pair.append((1.0 if v["RANK_7S"]["pay"] / v["RANK_7S"]["bet"] >= x else 0.0,
                     1.0 if v["RANK_7M1"]["pay"] / v["RANK_7M1"]["bet"] >= x else 0.0))
    pick_m1 = [(scores[rk] >= thr) != reverse for rk in keys]
    k = sum(pick_m1)
    n = len(keys)
    actual = 100.0 * sum(m if u else s for (s, m), u in zip(pair, pick_m1)) / n

    rng = random.Random(seed)
    idx = list(range(n))
    draws = []
    for _ in range(n_draw):
        rng.shuffle(idx)
        chosen = set(idx[:k])
        draws.append(100.0 * sum(m if i in chosen else s
                                 for i, (s, m) in enumerate(pair)) / n)
    mean = sum(draws) / len(draws)
    pct = 100.0 * sum(1 for d in draws if d < actual) / len(draws)
    return actual, mean, pct


def report(tag: str, picks: dict, scores: dict, thrs: list[float]) -> None:
    base, _ = route(picks, scores, None, force="RANK_7S")
    alt, _ = route(picks, scores, None, force="RANK_7M1")
    cov = sum(1 for rk in picks if rk in scores)
    print(f"\n=== {tag} 競合 {len(picks)}R（スコア取得 {cov}R）===")
    print(f"{'方式':<22}{'n':>6}{'2倍+':>9}{'5倍+':>9}{'10倍+':>9}{'ROI':>9}"
          f"{'7M1採用':>9}")
    for name, r, nm in (("always 7S（現行）", base, 0),
                        ("always 7M1", alt, alt.n)):
        print(f"{name:<22}{r.n:>6}{r.kpi(2):>8.2f}%{r.kpi(5):>8.2f}%"
              f"{r.kpi(10):>8.2f}%{r.roi:>8.1f}%{100*nm/max(r.n,1):>8.1f}%")
    for thr in thrs:
        r, nm = route(picks, scores, thr)
        d2 = r.kpi(2) - base.kpi(2)
        d5 = r.kpi(5) - base.kpi(5)
        print(f"{'thr ' + format(thr, '.2f'):<22}{r.n:>6}{r.kpi(2):>8.2f}%"
              f"{r.kpi(5):>8.2f}%{r.kpi(10):>8.2f}%{r.roi:>8.1f}%"
              f"{100*nm/max(r.n,1):>8.1f}%   Δ2倍{d2:+.2f} Δ5倍{d5:+.2f}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="lgbm_upset_screen_n15v2412")
    args = ap.parse_args()

    path = REPO / "data" / "models" / f"{args.model}.pkl"
    with open(path, "rb") as f:
        model = pickle.load(f)
    print(f"モデル: {args.model}（vintage・2024-12-31 まで学習）")

    picks_all = load_picks(SWEEP[0], CONFIRM[1])
    scores = load_scores(set(picks_all), model)
    print(f"競合 {len(picks_all)}R / スコア {len(scores)}R")

    vals = sorted(scores.values())
    qs = [0.50, 0.60, 0.70, 0.75, 0.80, 0.85, 0.90]
    thrs = [round(float(np.quantile(vals, q)), 4) for q in qs]
    print("閾値候補（全体分位）: "
          + " ".join(f"p{int(q*100)}={t:.4f}" for q, t in zip(qs, thrs)))

    for tag, (d1, d2) in (("掃引窓 2025", SWEEP), ("確認窓 2026", CONFIRM)):
        sub = {rk: v for rk, v in picks_all.items()
               if d1 <= v["RANK_7S"]["date"] <= d2}
        report(tag, sub, scores, thrs)

    print("\n--- 事前登録ラインの判定に使う CI（確認窓・always 7S との差）---")
    conf = {rk: v for rk, v in picks_all.items()
            if CONFIRM[0] <= v["RANK_7S"]["date"] <= CONFIRM[1]}
    for thr in thrs:
        lo2, hi2 = boot_diff(conf, scores, thr, 2.0)
        lo5, hi5 = boot_diff(conf, scores, thr, 5.0)
        print(f"thr {thr:.4f}  Δ2倍+ [{lo2:+.2f},{hi2:+.2f}]  "
              f"Δ5倍+ [{lo5:+.2f},{hi5:+.2f}]")

    print("\n--- 🔴 同じ割合をランダムに振り分けた帰無との比較（振り分けの正味）---")
    for tag, (d1, d2) in (("掃引窓 2025", SWEEP), ("確認窓 2026", CONFIRM)):
        sub = {rk: v for rk, v in picks_all.items()
               if d1 <= v["RANK_7S"]["date"] <= d2}
        print(f"\n[{tag}]  {'閾値':<9}{'7M1率':>7}"
              f"{'2倍+ 実測/帰無(pct)':>26}{'5倍+ 実測/帰無(pct)':>26}")
        for thr in thrs:
            _, nm = route(sub, scores, thr)
            frac = 100.0 * nm / max(len(sub), 1)
            a2, m2, p2 = null_same_fraction(sub, scores, thr, 2.0)
            a5, m5, p5 = null_same_fraction(sub, scores, thr, 5.0)
            print(f"{'':<9}{thr:<9.4f}{frac:>6.1f}%"
                  f"{a2:>12.2f}/{m2:.2f} ({p2:>4.1f}%)"
                  f"{a5:>12.2f}/{m5:.2f} ({p5:>4.1f}%)")

    print("\n--- 🔴 逆向き（低スコア帯を 7M1 へ）・帰無つき ---")
    for tag, (d1, d2) in (("掃引窓 2025", SWEEP), ("確認窓 2026", CONFIRM)):
        sub = {rk: v for rk, v in picks_all.items()
               if d1 <= v["RANK_7S"]["date"] <= d2}
        base, _ = route(sub, scores, None, force="RANK_7S")
        print(f"\n[{tag}] always 7S: 2倍+ {base.kpi(2):.2f}% / 5倍+ {base.kpi(5):.2f}%"
              f" / ROI {base.roi:.1f}%")
        for thr in thrs:
            r, nm = route(sub, scores, thr, reverse=True)
            frac = 100.0 * nm / max(r.n, 1)
            a2, m2, p2 = null_same_fraction(sub, scores, thr, 2.0, reverse=True)
            a5, m5, p5 = null_same_fraction(sub, scores, thr, 5.0, reverse=True)
            print(f"  thr {thr:.4f} 7M1率{frac:>5.1f}%  "
                  f"2倍+ {a2:>6.2f}/帰無{m2:.2f}({p2:>4.1f}%)  "
                  f"5倍+ {a5:>5.2f}/帰無{m5:.2f}({p5:>4.1f}%)  ROI {r.roi:>5.1f}%")

if __name__ == "__main__":
    main()
