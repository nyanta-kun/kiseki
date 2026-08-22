#!/usr/bin/env python3
"""三連複35通りを条件なしで全数走査する（三連単版 `exp_tf_unconstrained_scan.py` の対）。

## なぜやるか

三連単では 313万目を条件なしで走査して **妙味のポケットはゼロ**だった
（[[keirin_tf_unconstrained_scan_2026_08_22]]）。三連複は
**粒度が粗い（35通り）が、7C/7S/7B が実際に的中を稼いでいる帯**（的中時中央 1.5〜2.3倍）。
「三連単で無かったから三連複でも無い」は**推測にすぎない**ので実測する。

🔴 **先に全数で在り処を探し、見つかってから条件を書く**（ユーザー方針 2026-08-22）。
既存ランクのゲート（`RANK_7C_P3_SUM_MIN` / `RANK_7C_LEG_P3_MIN` / 印との一致など）は
**一切かけない**。

## 入力（モデルの再実行は不要）

- `p_model` … キャッシュ済みの三連単 PL（位置別合成 `(1,.5,0)`）を
  **6順列ぶん合算**して三連複の確率にする
- `予測オッズ` … 同じく三連単の予測板から `Σ(1/o)` を取って三連複へ畳む
  （**朝8:00 に手に入る情報だけ**で作れる）
- `確定オッズ` / `払戻` … `keirin.wt_odds`(bet_type='trio') と確定配当

## 判定（三連単版と同じ・事前登録）

**日ブロック bootstrap の CI 下限 > 払戻率 74.85%**。
探索窓（〜2026-04）と確認窓（2026-05〜）を**時間で**分ける。
⚠️ **EV 分位は必ずオッズ帯の中で切る**（跨ぐと favorite–longshot bias が
   EV の効果に化ける。三連単で実際に踏んだ）。
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.exp_leg_prob_heads import strengths  # noqa: E402
from src.strategy_wt import (  # noqa: E402
    rank_7t1_is_cross_line, rank_7t1_is_target_race_type,
)

PAYOUT_RATE = 0.7485
BANDS = [(1, 3), (3, 5), (5, 10), (10, 20), (20, 40), (40, 80),
         (80, 200), (200, 10 ** 9)]


def _trio_boards(keys: list[str]) -> tuple[dict, dict]:
    """確定の三連複オッズ板と、確定配当（100円あたり）。"""
    con = psycopg2.connect(os.environ["KEIRIN_DB_URL"])
    cur = con.cursor()
    board: dict[str, dict[frozenset, float]] = defaultdict(dict)
    for i in range(0, len(keys), 2000):
        chunk = keys[i:i + 2000]
        cur.execute(
            "select race_key, combination, odds_value from keirin.wt_odds "
            "where bet_type='trio' and race_key = any(%s) and odds_value > 0",
            (chunk,))
        for rk, combo, o in cur.fetchall():
            cars = frozenset(int(x) for x in str(combo).replace("=", "-").split("-"))
            if len(cars) == 3:
                board[rk][cars] = float(o)
    return board, {}


def build(cache: str) -> dict[str, np.ndarray]:
    rows = []
    with open(cache) as f:
        for line in f:
            r = json.loads(line)
            if r.get("win"):
                rows.append(r)
    board, _ = _trio_boards([r["race_key"] for r in rows])
    print(f"確定の三連複板が取れたレース: {len(board):,} / {len(rows):,}")

    pm, po, fo, pay, day, hit = [], [], [], [], [], []
    r_top, r_mid, r_low, fin, crs = [], [], [], [], []
    days: dict[str, int] = {}
    for r in rows:
        bd = board.get(r["race_key"])
        if not bd:
            continue
        pw = {int(k): v for k, v in r["pw"].items()}
        p3 = {int(k): v for k, v in r["p3"].items()}
        s = [strengths(pw, p3, a) for a in (1.0, 0.5, 0.0)]
        s1 = sum(s[0].values())
        cars = list(pw)
        rank = {c: i + 1 for i, c in enumerate(sorted(p3, key=lambda k: (-p3[k], k)))}
        tf_pred = r["odds"]
        # 三連単 → 三連複へ畳む
        prob: dict[frozenset, float] = defaultdict(float)
        inv: dict[frozenset, float] = defaultdict(float)
        tot_p = tot_i = 0.0
        for x, y, z in itertools.permutations(cars, 3):
            d2 = sum(s[1][c] for c in cars if c != x)
            d3 = sum(s[2][c] for c in cars if c not in (x, y))
            if d2 <= 0 or d3 <= 0:
                continue
            v = (s[0][x] / s1) * (s[1][y] / d2) * (s[2][z] / d3)
            key = frozenset((x, y, z))
            prob[key] += v
            tot_p += v
            o = tf_pred.get(f"{x}-{y}-{z}")
            if o and o > 0:
                inv[key] += 1.0 / o
                tot_i += 1.0 / o
        if tot_p <= 0 or tot_i <= 0:
            continue
        # 🔴 予測三連複オッズは板として整合させる（Σ(1/o) を確定板の水準へ合わせる）
        tgt = sum(1.0 / o for o in bd.values())
        wins = {frozenset(int(x) for x in w.split("-")) for w in r["win"]}
        di = days.setdefault(r["race_date"], len(days))
        is_fin = bool(rank_7t1_is_target_race_type(r.get("race_type")))
        lg = {int(k): v for k, v in r["line_group"].items()}
        lp = {int(k): v for k, v in r["line_pos"].items()}
        is_crs = bool(rank_7t1_is_cross_line(p3, lg, lp))
        for key, o_fin in bd.items():
            p = prob.get(key, 0.0) / tot_p
            iv = inv.get(key, 0.0)
            if iv <= 0:
                continue
            o_pred = (tot_i / tgt) / iv          # 予測オッズ（板として再スケール）
            rs = sorted(rank[c] for c in key)
            pm.append(p); po.append(o_pred); fo.append(o_fin)
            pay.append(o_fin * 100 if key in wins else 0.0)
            hit.append(1 if key in wins else 0)
            day.append(di)
            r_top.append(rs[0]); r_mid.append(rs[1]); r_low.append(rs[2])
            fin.append(is_fin); crs.append(is_crs)
    return dict(p=np.array(pm, dtype=np.float32), po=np.array(po, dtype=np.float32),
                fo=np.array(fo, dtype=np.float32), pay=np.array(pay, dtype=np.float64),
                hit=np.array(hit, dtype=np.int8), day=np.array(day, dtype=np.int32),
                rt=np.array(r_top, dtype=np.int8), rm=np.array(r_mid, dtype=np.int8),
                rl=np.array(r_low, dtype=np.int8),
                fin=np.array(fin), crs=np.array(crs),
                n_days=len(days), day_names=sorted(days, key=days.get))


def roi_ci(pay, day, n_days, B=1500):
    if len(pay) == 0:
        return 0.0, 0.0
    cnt = np.bincount(day, minlength=n_days).astype(np.float64)
    tot = np.bincount(day, weights=pay, minlength=n_days)
    keep = cnt > 0
    cnt, tot = cnt[keep], tot[keep]
    roi = tot.sum() / (100 * cnt.sum())
    idx = np.random.randint(0, len(cnt), size=(B, len(cnt)))
    b = np.sort(tot[idx].sum(1) / (100 * cnt[idx].sum(1)))
    return roi, b[int(B * .025)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="data/exp/tf_shape_cache.jsonl")
    ap.add_argument("--split", default="2026-05-01")
    ap.add_argument("--min-legs", type=int, default=1500)
    ap.add_argument("--price", choices=("pred", "final"), default="pred",
                    help="バケット分けに使うオッズ。final は 8:00 に無いので"
                         "『情報が在るか』の上界を見る用")
    args = ap.parse_args()

    np.random.seed(61)
    d = build(args.cache)
    price = d["po"] if args.price == "pred" else d["fo"]
    ev = d["p"] * price
    names = d["day_names"]
    split_idx = sum(1 for n in names if n < args.split)
    sel = d["day"] < split_idx
    print(f"総目数 {len(d['p']):,} / {d['n_days']}日 "
          f"（探索 {sel.sum():,} / 確認 {(~sel).sum():,}）  価格={args.price}")
    print(f"壁（払戻率）= {PAYOUT_RATE:.2%}\n")

    print(f"{'オッズ帯':>12}{'EV分位':>7}{'探索:目数':>10}{'ROI':>8}{'CI下限':>8}"
          f"{'確認:目数':>10}{'ROI':>8}{'CI下限':>8}")
    n_pass = 0
    for lo, hi in BANDS:
        band = (price >= lo) & (price < hi)
        if (band & sel).sum() < args.min_legs:
            continue
        qs = np.quantile(ev[band & sel], [.2, .4, .6, .8])
        for qi in range(5):
            a_lo = -np.inf if qi == 0 else qs[qi - 1]
            a_hi = np.inf if qi == 4 else qs[qi]
            cell = band & (ev >= a_lo) & (ev < a_hi)
            A, B_ = cell & sel, cell & ~sel
            if A.sum() < args.min_legs or B_.sum() < args.min_legs:
                continue
            ra, la = roi_ci(d["pay"][A], d["day"][A], d["n_days"])
            rb, lb = roi_ci(d["pay"][B_], d["day"][B_], d["n_days"])
            mark = ""
            if la > PAYOUT_RATE:
                mark = " 🟢確認窓も超" if lb > PAYOUT_RATE else " ⚠️探索のみ"
                n_pass += 1
            print(f"{f'{lo}〜{hi}':>12}{f'Q{qi+1}':>7}{A.sum():>10,}{ra:>8.1%}{la:>8.1%}"
                  f"{B_.sum():>10,}{rb:>8.1%}{lb:>8.1%}{mark}")

    print(f"\n===== 構造（オッズ 3〜80倍）=====")
    core = (price >= 3) & (price < 80)
    print(f"{'条件':>26}{'探索:目数':>10}{'ROI':>8}{'CI下限':>8}"
          f"{'確認:目数':>10}{'ROI':>8}{'CI下限':>8}")
    conds = [(f"最上位車=順位{i}", d["rt"] == i) for i in range(1, 6)] + \
            [(f"最下位車=順位{i}", d["rl"] == i) for i in range(3, 8)] + \
            [("上位2車を含む", (d["rt"] == 1) & (d["rm"] == 2)),
             ("順位1を含む", d["rt"] == 1), ("順位1を含まない", d["rt"] > 1),
             ("3車とも上位4", d["rl"] <= 4), ("下位3車のみ", d["rt"] >= 5),
             ("決勝系", d["fin"]), ("決勝系以外", ~d["fin"]),
             ("上位2車が別ライン", d["crs"]), ("同ライン", ~d["crs"])]
    for lbl, m in conds:
        cell = core & m
        A, B_ = cell & sel, cell & ~sel
        if A.sum() < args.min_legs or B_.sum() < args.min_legs:
            continue
        ra, la = roi_ci(d["pay"][A], d["day"][A], d["n_days"])
        rb, lb = roi_ci(d["pay"][B_], d["day"][B_], d["n_days"])
        mark = ""
        if la > PAYOUT_RATE:
            mark = " 🟢確認窓も超" if lb > PAYOUT_RATE else " ⚠️探索のみ"
            n_pass += 1
        print(f"{lbl:>26}{A.sum():>10,}{ra:>8.1%}{la:>8.1%}"
              f"{B_.sum():>10,}{rb:>8.1%}{lb:>8.1%}{mark}")
    print()
    if n_pass == 0:
        print("🔴 探索窓で CI 下限が払戻率を超えたセルは **1つも無い**。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
