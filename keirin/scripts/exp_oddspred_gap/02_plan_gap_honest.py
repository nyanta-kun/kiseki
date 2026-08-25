"""honest な長い窓（2026-01〜08）で、計画（予測オッズ）と確定オッズの差を分解する。

    KEIRIN_ODDS_MODEL_DIR=data/backup/odds_model_20260816 \
      PYTHONPATH=. .venv/bin/python scripts/exp_oddspred_gap/02_plan_gap_honest.py

母集団は学習データセット（`data/exp_cache/odds_trio_dataset_n7_db.pkl`）の全7車レース。
プランは **軸=p3上位2車の総流し5点**（行の `rk1==0 and rk2==1` がそれ）。
本番の 7C/7S と完全同一ではないが、**同じ買い方を全レースへ当てて水準を測る台**として使う。
実入稿での数字は 01 を見ること（両者は中央比 1.10 で一致する）。

🔴 モデルは必ず学習終端 2025-12-31 の vintage を指すこと（本番モデルだと in-sample）。
"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd

from _common import CACHE  # noqa: E402
from src.odds_prediction import FEATURE_NAMES, load_meta, target_sum  # noqa: E402
from src.stake_allocation import allocate_budget  # noqa: E402

DS = CACHE / "odds_trio_dataset_n7_db.pkl"
OUT = CACHE / "oddspred_gap_plan5.pkl"


def build() -> pd.DataFrame:
    import lightgbm as lgb
    meta = load_meta()
    end = meta["per_n_car"]["7"]["train_end"]
    if end >= "2026-01-01":
        raise SystemExit(f"学習終端 {end} のモデルで 2026 を採点すると in-sample です。"
                         "KEIRIN_ODDS_MODEL_DIR=data/backup/odds_model_20260816 を指してください")
    booster = lgb.Booster(model_file=str(
        (os.environ.get("KEIRIN_ODDS_MODEL_DIR") or "") + "/odds_trio_n7.txt"))
    d = pd.read_pickle(DS)
    d = d[d.date > end].reset_index(drop=True)
    raw = 10 ** booster.predict(d[list(FEATURE_NAMES)])
    d["pred"] = raw * (pd.Series(1 / raw).groupby(d.rk.values).transform("sum").to_numpy()
                       / target_sum(7))
    plan = d[(d.rk1 == 0) & (d.rk2 == 1)]
    rows = []
    for rk, g in plan.groupby("rk", sort=False):
        if len(g) != 5:
            continue
        g = g.sort_values("pred")
        p, f = g.pred.to_numpy(), g.odds.to_numpy()
        s_ = allocate_budget({i: 1.0 / v for i, v in enumerate(p)}, 10000, 100)
        s = np.array([s_[i] for i in range(5)], float)
        wp, wf = (1 / p) / (1 / p).sum(), (1 / f) / (1 / f).sum()
        rows.append(dict(rk=rk, date=g.date.iloc[0], mp=(p * s).mean(), mf=(f * s).mean(),
                         flp=(p * s).min() / 1e4, flf=(f * s).min() / 1e4,
                         spp=(p * s).max() / (p * s).min(), spf=(f * s).max() / (f * s).min(),
                         l1=float(np.abs(wp - wf).sum()),
                         **{f"pay_f{i+1}": float((f * s)[i]) for i in range(5)},
                         **{f"r{i+1}": float(f[i] / p[i]) for i in range(5)}))
    P = pd.DataFrame(rows)
    P["month"] = P.date.str[:7]
    P.to_pickle(OUT)
    return P


def main() -> None:
    P = build()
    print(f"n={len(P)}R  {P.date.min()}〜{P.date.max()}")
    print(f"\n配分: 払戻の最大/最小 中央  計画 {P.spp.median():.2f} → 確定 {P.spf.median():.2f}"
          f"   重みのL1 中央 {P.l1.median():.3f}")
    print(f"平均払戻: 確定/予測 中央 {(P.mf / P.mp).median():.3f}"
          f"（sd(log10) {np.log10(P.mf / P.mp).std():.4f} ＝ ±{100 * (10 ** np.log10(P.mf / P.mp).std() - 1):.0f}%）")
    print(f"最低払戻: 確定/予測 中央 {(P.flf / P.flp).median():.3f}"
          f"（sd(log10) {np.log10(P.flf / P.flp).std():.4f}）")
    print("\n月別（この2つの偏りが安定しているか）")
    for m, g in P.groupby("month"):
        print(f"  {m} n={len(g):5d}  平均払戻比 {(g.mf / g.mp).median():.3f}"
              f"  最低払戻比 {(g.flf / g.flp).median():.3f}  配分L1 {g.l1.median():.3f}")
    print("\n脚別（1=最人気）: 確定/予測 中央 と 確定払戻の中央")
    for i in range(1, 6):
        print(f"  脚{i}  {P[f'r{i}'].median():.3f}   {P[f'pay_f{i}'].median():,.0f}円")
    print("\n【足切りの較正表】判定値 = 予測 × c  （c は単調変換なので閾値の付け替えと同じ）")
    print("  最低払戻 1.5倍ゲート")
    print("   c     通す率   通した中で実際に1.5倍以上   切った中で実は1.5倍以上")
    for c in (1.00, 0.95, 0.90, 0.85, 0.80, 0.78, 0.75, 0.70):
        pa = P.flp * c >= 1.5
        if pa.sum() == 0:
            continue
        print(f"  {c:.2f}   {100 * pa.mean():5.1f}%   {100 * (P.flf[pa] >= 1.5).mean():21.1f}%"
              f"   {100 * (P.flf[~pa] >= 1.5).mean():20.1f}%")
    print("  平均払戻 20,000円ゲート")
    print("   c     通す率   通した中で実際に2万円超   切った中で実は2万円超")
    for c in (1.00, 1.05, 1.10, 1.15, 1.20):
        pa = P.mp * c > 20000
        print(f"  {c:.2f}   {100 * pa.mean():5.1f}%   {100 * (P.mf[pa] > 20000).mean():19.1f}%"
              f"   {100 * (P.mf[~pa] > 20000).mean():18.1f}%")
    print(f"\n参考: 確定オッズでの実際の分布  下限1.5倍以上 {100 * (P.flf >= 1.5).mean():.1f}%"
          f" / 平均払戻2万円超 {100 * (P.mf > 20000).mean():.1f}%"
          f" / 下限中央 {P.flf.median():.2f} / 平均払戻中央 {P.mf.median():,.0f}円")


if __name__ == "__main__":
    main()
