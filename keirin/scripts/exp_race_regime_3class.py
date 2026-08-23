#!/usr/bin/env python3
"""レースを3つの型に分ける — **オッズ公開前のデータだけ**で（2026-08-23）。

## ユーザー方針

> 商品を分ける前に、まずはレースのオッズ公開前に存在するデータのみでモデル精度が
> 重要。人気どころで硬く決まるレース／不人気選手が入着するケース／混戦でどちらとも
> 読めないレースを分ける精度を高くできるかで行うべき。分けた上でそれぞれのブロックで
> ROI 向上を図る。人気どころでは1日の的中体験を、不人気選手が入着しそうなレースで
> 高回収率を叩き出し、1日のROIを確保できるのが理想。

🟢 これは [[keirin_irregular_layer_screening_2026_08_20]] に **🟡未検証**として
   残っていた項目そのもの（「波乱スコアを収益でなく**商品の振り分け**に使う。
   ROI ではなく KPI を目的関数にするので壁の下でも成立しうる」）。

## 前提の監査（済み）

`FEATURE_COLS_WT`（66特徴）に**オッズ由来の列は無い**。市場に近いのは
`prediction_mark`（winticket AI印）だけで、これもオッズ公開前に出る。
→ `p3` およびここで使うレース構造量は**すべてオッズ公開前**に確定する。

## ラベルの操作的定義（事前登録）

🔴 直感で1つ選ぶと設計の些細な選択が効果量と同オーダーになる
   （[[keirin_verification_design_audit_2026_08_21]]）ので**2定義を並べて測る**。

| 定義 | 硬い | 中（混戦） | 荒れ |
|---|---|---|---|
| **P（配当）** | 三連複の確定配当が下位1/3 | 中位1/3 | 上位1/3 |
| **I（我々の指数）** | 実際のtop3が p3上位3車と完全一致 | 2車一致 | 0〜1車一致 |

**P が主**（経済的に直接意味がある）。**I は頑健性の確認**。
しきい値は**探索窓だけ**で決めて固定する。

## 窓（`docs/product_portfolio_redesign_2026_08.md` の事前登録に従う）

    探索 2024-01-01〜2025-12-31 / 確認 2026-01-01〜2026-06-30
    封印 2026-07-01〜2026-08-22（**本スクリプトは触らない**）
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.backfill_7t1_rank_wt import _load_finishes  # noqa: E402
from scripts.exp_axis1_bust_stratified import RACE_FEATS, build, load_rich  # noqa: E402
from scripts.exp_trio_joint_partner import fit, load_any, load_boards  # noqa: E402
from src.result_top3 import winning_trifectas  # noqa: E402
from src.strategy_wt import RANK_7C_LEG_P3_MIN, RANK_7C_LEGS_MIN, unit_stake  # noqa: E402

SEARCH_END = "2025-12-31"
CONFIRM_START, CONFIRM_END = "2026-01-01", "2026-06-30"
PAYOUT_RATE = 0.7485
NAMES = ["硬い", "中(混戦)", "荒れ"]


def label_P(pay, cuts):
    return 0 if pay < cuts[0] else (1 if pay < cuts[1] else 2)


def label_I(order, top3):
    n = sum(1 for c in order[:3] if c in top3)
    return 0 if n == 3 else (1 if n == 2 else 2)


def auc_ovr(y, p, k):
    yy = (np.asarray(y) == k).astype(int)
    pp = np.asarray(p)[:, k]
    pos, neg = yy.sum(), len(yy) - yy.sum()
    if not pos or not neg:
        return float("nan")
    r = np.argsort(np.argsort(pp)) + 1
    return (r[yy == 1].sum() - pos * (pos + 1) / 2) / (pos * neg)


def roi_ci(seg, B=4000, seed=13):
    by = defaultdict(lambda: [0.0, 0.0, 0, 0])
    for d, h, p, b in seg:
        z = by[d]; z[0] += b; z[1] += p; z[2] += h; z[3] += 1
    v = np.array([[z[0], z[1], z[2], z[3]] for z in by.values()], float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(v), size=(B, len(v)))
    r = np.sort(v[idx, 1].sum(1) / v[idx, 0].sum(1))
    return (v[:, 1].sum() / v[:, 0].sum(), r[int(B * .025)],
            v[:, 2].sum() / v[:, 3].sum())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache-a", default="data/exp/trio_rank_cache.jsonl")
    ap.add_argument("--cache-b", default="data/exp/tf_shape_cache4.jsonl")
    ap.add_argument("--rounds", type=int, default=500)
    args = ap.parse_args()

    allr = load_any(args.cache_a) + load_any(args.cache_b)
    S = [r for r in allr if r["date"] <= SEARCH_END]
    C = [r for r in allr if CONFIRM_START <= r["date"] <= CONFIRM_END]
    print(f"探索 {len(S):,}R（〜{SEARCH_END}） / 確認 {len(C):,}R"
          f"（{CONFIRM_START}〜{CONFIRM_END}）")
    print("🔒 封印 2026-07-01〜2026-08-22 は本スクリプトでは読まない\n")

    ks, kc = [r["key"] for r in S], [r["key"] for r in C]
    ent_s, ent_c = load_rich(ks), load_rich(kc)
    fin_s, fin_c = _load_finishes(ks), _load_finishes(kc)
    bd_s, bd_c = load_boards(ks), load_boards(kc)

    def pack(rows, ent, fin, bd):
        _, _, _, Xr, _, mr = build(rows, ent, fin)
        p3_of = {r["key"]: r["p3"] for r in rows}
        ord_of = {r["key"]: r["order"] for r in rows}
        out = []
        for x, m in zip(Xr, mr):
            key = m[0]
            o3 = fin.get(key); b = bd.get(key)
            if not o3 or not b:
                continue
            wins = {frozenset(w) for w in winning_trifectas(o3)}
            pays = [b[k] for k in wins if k in b]
            if not pays:
                continue
            top3 = {c for w in winning_trifectas(o3) for c in w}
            out.append(dict(key=key, date=m[1], x=x, pay=float(np.mean(pays)),
                            top3=top3, order=ord_of[key], p3=p3_of[key],
                            wins=wins, board=b))
        return out

    A, B = pack(S, ent_s, fin_s, bd_s), pack(C, ent_c, fin_c, bd_c)
    cuts = tuple(np.quantile([r["pay"] for r in A], [1 / 3, 2 / 3]))
    print(f"定義P のしきい値（探索窓で決定・以後固定）: "
          f"< {cuts[0]:.1f}倍 / < {cuts[1]:.1f}倍 / それ以上\n")

    for tag, fn in (("P（配当）", lambda r: label_P(r["pay"], cuts)),
                    ("I（我々の指数）", lambda r: label_I(r["order"], r["top3"]))):
        ya = np.array([fn(r) for r in A])
        yb = np.array([fn(r) for r in B])
        Xa = np.array([r["x"] for r in A], np.float32)
        Xb = np.array([r["x"] for r in B], np.float32)
        m = fit_multi(Xa, ya, args.rounds)
        pb = m.predict(Xb)
        print(f"===== 定義{tag} =====")
        print("  クラス比率 探索 " +
              " / ".join(f"{NAMES[k]} {(ya==k).mean():.1%}" for k in range(3)) +
              "   確認 " +
              " / ".join(f"{NAMES[k]} {(yb==k).mean():.1%}" for k in range(3)))
        print("  1対他AUC  " +
              " / ".join(f"{NAMES[k]} {auc_ovr(yb, pb, k):.4f}" for k in range(3)))
        pred = pb.argmax(1)
        hdr = "予測\\実際"
        print(f"  {hdr:>12}" + "".join(f"{n:>12}" for n in NAMES) +
              f"{'件数':>9}{'的中率':>9}")
        for k in range(3):
            msk = pred == k
            if not msk.sum():
                continue
            row = [(yb[msk] == j).mean() for j in range(3)]
            print(f"{NAMES[k]:>12}" + "".join(f"{v:>12.1%}" for v in row) +
                  f"{msk.sum():>9,}{row[k]:>9.1%}")
        # 予測ブロックごとの経済性（共通の参照買い＝7C風・軸2車＋p3足切り）
        print(f"  {'予測ブロック':>12}{'件数':>8}{'的中%':>9}{'ROI':>9}{'CI下限':>9}"
              f"{'中央配当':>10}")
        for k in range(3):
            seg = []
            for r, pk in zip(B, pred):
                if pk != k:
                    continue
                o, p3 = r["order"], r["p3"]
                legs = [c for c in o[2:] if p3[c] >= RANK_7C_LEG_P3_MIN]
                if len(legs) < RANK_7C_LEGS_MIN:
                    continue
                kk = [frozenset((o[0], o[1], c)) for c in legs]
                if any(x not in r["board"] for x in kk):
                    continue
                st = unit_stake(len(kk))
                hit = any(x in r["wins"] for x in kk)
                pay = sum(int(r["board"][x] * 100) * st // 100
                          for x in kk if x in r["wins"])
                seg.append((r["date"], int(hit), pay, len(kk) * st))
            if len(seg) < 100:
                continue
            roi, lo, hit = roi_ci(seg)
            mk = " 🟢" if lo > PAYOUT_RATE else ""
            pays = [r["pay"] for r, pk in zip(B, pred) if pk == k]
            print(f"{NAMES[k]:>12}{len(seg):>8,}{hit:>9.2%}{roi:>9.1%}{lo:>9.1%}"
                  f"{np.median(pays):>10.1f}{mk}")
        imp = sorted(zip(RACE_FEATS, m.feature_importance("gain")),
                     key=lambda x: -x[1])
        print("  寄与上位: " + " / ".join(k for k, _ in imp[:8]) + "\n")
    return 0


def fit_multi(X, y, rounds):
    import lightgbm as lgb
    return lgb.train(dict(objective="multiclass", num_class=3,
                          learning_rate=0.05, num_leaves=31,
                          min_data_in_leaf=200, feature_fraction=0.8,
                          bagging_fraction=0.8, bagging_freq=1,
                          verbose=-1, seed=7),
                     lgb.Dataset(X, label=y), num_boost_round=rounds)


if __name__ == "__main__":
    raise SystemExit(main())
