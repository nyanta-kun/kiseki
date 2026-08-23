#!/usr/bin/env python3
"""条件を一切かけずに三連単313万目を走査し、妙味の在り処を探す。

## なぜ順序を変えたか（ユーザー指摘 2026-08-22）

> 7T1/7T2 に絞って議論しているが、まず何か既存条件に絞る前に検証し、
> 条件に合う絞り込みがあれば追加とするのが良くないか

これまでの検証は 7T1 の枠（軸1は3着内率上位2車・`a1-a2-c` の形・オッズ足切り・
自己整合の点数）を**前提にしたまま**測っていた。それでは「既存条件に合うか」しか
分からない。**先に全数で在り処を探し、見つかってから条件を書く。**

## 設計

対象は 14,922R × 210通 ＝ 約313万目。各目について朝8:00 に手に入るものだけを使う:

  - `pred_odds`   予測三連単オッズ（`odds_prediction_tf.predict_board`）
  - `p_model`     位置別合成 PL（`(1,.5,0)`・§12 で確認窓まで確認済み）
  - `ev`          p_model × pred_odds
  - 着ごとのモデル順位（1着車が3着内率で何番手か 等）
  - レース属性（決勝系か・上位2車が別ラインか）

採点は**確定オッズ**（`win` の配当）。100円ずつ買った想定の実現 ROI で見る。

## 🔴 多重比較への備え（これが無いと313万目からノイズを拾うだけ）

- **時間で探索窓と確認窓を分ける**（既定 2026-05-01）。探索窓でしか閾値を選ばない
- 判定は **日ブロック bootstrap の CI 下限が払戻率 74.85% を超えるか**
- 🔴 **オッズ帯を揃えずに EV 分位を見てはいけない。** 揃えないと
  favorite–longshot bias（極端な大穴の値付けの悪さ）が EV の効果に化ける
  （実測: 全母集団だと Q1 の平均予測オッズが 2068倍で ROI 55.6%、
  帯を 15〜75倍に揃えると全分位が 75% に張り付いた）
"""
from __future__ import annotations

import argparse
import itertools
import json
import random
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.exp_leg_prob_heads import strengths  # noqa: E402
from src.strategy_wt import (  # noqa: E402
    rank_7t1_is_cross_line, rank_7t1_is_target_race_type,
)

random.seed(51)
PAYOUT_RATE = 0.7485          # 競輪の払戻率（＝控除率25.15%）。壁の位置

#: オッズ帯。**必ず帯を揃えてから他の軸を見る**
BANDS = [(1, 10), (10, 20), (20, 30), (30, 50), (50, 75),
         (75, 150), (150, 300), (300, 1000), (1000, 10 ** 9)]


def build(cache: str) -> dict[str, np.ndarray]:
    odds, ev, pay, day, r1, r2, r3, final, cross = [], [], [], [], [], [], [], [], []
    days: dict[str, int] = {}
    with open(cache) as f:
        for line in f:
            r = json.loads(line)
            if not r.get("win"):
                continue
            pw = {int(k): v for k, v in r["pw"].items()}
            p3 = {int(k): v for k, v in r["p3"].items()}
            s = [strengths(pw, p3, a) for a in (1.0, 0.5, 0.0)]
            s1sum = sum(s[0].values())
            cars = list(pw)
            # 3着内率でのレース内順位（1が最上位）
            rank = {c: i + 1 for i, c in enumerate(
                sorted(p3, key=lambda k: (-p3[k], k)))}
            lg = {int(k): v for k, v in r["line_group"].items()}
            lp = {int(k): v for k, v in r["line_pos"].items()}
            is_cross = bool(rank_7t1_is_cross_line(p3, lg, lp))
            is_final = bool(rank_7t1_is_target_race_type(r.get("race_type")))
            di = days.setdefault(r["race_date"], len(days))
            board = set(r["board"])
            win = r["win"]
            for x, y, z in itertools.permutations(cars, 3):
                leg = f"{x}-{y}-{z}"
                if leg not in board:
                    continue
                o = r["odds"].get(leg)
                if not o or o <= 0:
                    continue
                d2 = sum(s[1][c] for c in cars if c != x)
                d3 = sum(s[2][c] for c in cars if c not in (x, y))
                if d2 <= 0 or d3 <= 0:
                    continue
                p = (s[0][x] / s1sum) * (s[1][y] / d2) * (s[2][z] / d3)
                odds.append(o); ev.append(p * o)
                pay.append(win.get(leg, 0)); day.append(di)
                r1.append(rank[x]); r2.append(rank[y]); r3.append(rank[z])
                final.append(is_final); cross.append(is_cross)
    return dict(odds=np.array(odds, dtype=np.float32),
                ev=np.array(ev, dtype=np.float32),
                pay=np.array(pay, dtype=np.float64),
                day=np.array(day, dtype=np.int32),
                r1=np.array(r1, dtype=np.int8), r2=np.array(r2, dtype=np.int8),
                r3=np.array(r3, dtype=np.int8),
                final=np.array(final), cross=np.array(cross),
                n_days=len(days), day_names=sorted(days, key=days.get))


def roi_ci(pay: np.ndarray, day: np.ndarray, n_days: int, B: int = 1500):
    """日ブロック bootstrap。100円ずつ買った想定の ROI とその CI。"""
    if len(pay) == 0:
        return 0.0, 0.0, 0.0
    cnt = np.bincount(day, minlength=n_days).astype(np.float64)
    tot = np.bincount(day, weights=pay, minlength=n_days)
    keep = cnt > 0
    cnt, tot = cnt[keep], tot[keep]
    roi = tot.sum() / (100 * cnt.sum())
    idx = np.random.randint(0, len(cnt), size=(B, len(cnt)))
    b = tot[idx].sum(1) / (100 * cnt[idx].sum(1))
    b.sort()
    return roi, b[int(B * .025)], b[int(B * .975)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="data/exp/tf_shape_cache.jsonl")
    ap.add_argument("--split", default="2026-05-01")
    ap.add_argument("--min-legs", type=int, default=3000)
    args = ap.parse_args()

    np.random.seed(51)
    d = build(args.cache)
    names = d["day_names"]
    split_idx = sum(1 for n in names if n < args.split)
    is_sel = d["day"] < split_idx
    print(f"総目数 {len(d['odds']):,} / {d['n_days']}日 "
          f"（探索窓 〜{args.split}: {is_sel.sum():,}目 / 確認窓: {(~is_sel).sum():,}目）")
    print(f"壁（払戻率）= {PAYOUT_RATE:.2%}\n")

    print(f"{'オッズ帯':>14}{'EV分位':>7}{'探索:目数':>10}{'ROI':>8}{'CI下限':>8}"
          f"{'確認:目数':>10}{'ROI':>8}{'CI下限':>8}")
    hits = []
    for lo, hi in BANDS:
        band = (d["odds"] >= lo) & (d["odds"] < hi)
        if band.sum() < args.min_legs:
            continue
        # 🔴 EV 分位は**帯の中で**切る（帯をまたぐと longshot bias が混ざる）
        evb = d["ev"][band & is_sel]
        if len(evb) < args.min_legs:
            continue
        qs = np.quantile(evb, [.2, .4, .6, .8])
        for qi in range(5):
            lo_q = -np.inf if qi == 0 else qs[qi - 1]
            hi_q = np.inf if qi == 4 else qs[qi]
            cell = band & (d["ev"] >= lo_q) & (d["ev"] < hi_q)
            a, b = cell & is_sel, cell & ~is_sel
            if a.sum() < args.min_legs or b.sum() < args.min_legs:
                continue
            ra, la, _ = roi_ci(d["pay"][a], d["day"][a], d["n_days"])
            rb, lb, _ = roi_ci(d["pay"][b], d["day"][b], d["n_days"])
            mark = ""
            if la > PAYOUT_RATE:
                mark = " 🟢確認窓も超" if lb > PAYOUT_RATE else " ⚠️探索のみ"
                hits.append(((lo, hi), qi + 1, ra, la, rb, lb))
            print(f"{f'{lo}〜{hi}':>14}{f'Q{qi+1}':>7}{a.sum():>10,}{ra:>8.1%}{la:>8.1%}"
                  f"{b.sum():>10,}{rb:>8.1%}{lb:>8.1%}{mark}")
    print()
    if not hits:
        print("🔴 探索窓で CI 下限が払戻率を超えたセルは **1つも無い**。")
    else:
        print(f"探索窓で壁を超えたセル: {len(hits)}")

    # ── 第2段: 買い目の**構造**で走査する ──────────────────────────────
    # 🔴 EV 軸だけでは「どういう形の目が割安か」は分からない。
    #    各着に置く車のモデル順位（3着内率でのレース内順位）で切る。
    #    ⚠️ 極端な大穴帯（1000倍超）は値付けが構造的に悪く、混ぜると全ての
    #       セルがそれに引きずられるので除外する。
    core = (d["odds"] >= 10) & (d["odds"] < 300)
    print(f"\n===== 第2段: 買い目の構造（予測オッズ 10〜300倍・"
          f"{core.sum():,}目）=====")
    print(f"{'条件':>26}{'探索:目数':>10}{'ROI':>8}{'CI下限':>8}"
          f"{'確認:目数':>10}{'ROI':>8}{'CI下限':>8}")
    struct = []
    for lbl, m in [(f"1着=順位{i}", d["r1"] == i) for i in range(1, 8)] + \
                  [(f"2着=順位{i}", d["r2"] == i) for i in range(1, 8)] + \
                  [(f"3着=順位{i}", d["r3"] == i) for i in range(1, 8)] + \
                  [("決勝系", d["final"]), ("決勝系以外", ~d["final"]),
                   ("上位2車が別ライン", d["cross"]), ("同ライン", ~d["cross"]),
                   ("1着=順位1 ∧ 2着=順位2", (d["r1"] == 1) & (d["r2"] == 2)),
                   ("1・2着が順位1-2のどちらか",
                    (d["r1"] <= 2) & (d["r2"] <= 2)),
                   ("3着が下位3車（順位5-7）", d["r3"] >= 5),
                   ("3着が上位（順位1-2）", d["r3"] <= 2)]:
        cell = core & m
        a, b = cell & is_sel, cell & ~is_sel
        if a.sum() < args.min_legs or b.sum() < args.min_legs:
            continue
        ra, la, _ = roi_ci(d["pay"][a], d["day"][a], d["n_days"])
        rb, lb, _ = roi_ci(d["pay"][b], d["day"][b], d["n_days"])
        mark = ""
        if la > PAYOUT_RATE:
            mark = " 🟢確認窓も超" if lb > PAYOUT_RATE else " ⚠️探索のみ"
            struct.append(lbl)
        print(f"{lbl:>26}{a.sum():>10,}{ra:>8.1%}{la:>8.1%}"
              f"{b.sum():>10,}{rb:>8.1%}{lb:>8.1%}{mark}")
    print()
    if not struct:
        print("🔴 構造でも、探索窓で壁を超えた条件は **1つも無い**。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
