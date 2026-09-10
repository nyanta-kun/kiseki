#!/usr/bin/env python3
"""未使用の朝入力に価値があるか② — 節内成績（`MEETING_FORM_COLS_WT`）の A/B（2026-09-10）。

## なぜこれか（棚卸しの結果）

朝の推奨時点で使えて **`FEATURE_COLS_WT`（66特徴）に入っていない**ものを洗ったところ、
唯一「実装済みなのに配線されていない」ものが `add_meeting_form_features_wt`
（2026-08-20 追加・同一開催の前日までの自分の成績）だった。

    cup_n_so_far / cup_top3_rate / cup_win_rate / cup_mean_order_n

docstring の実測（2024-01〜・7車）では **節内全外 −0.17 / 一部的中 +1.14 /
全部3着内 +1.57pt（差 1.74pt・単調・10σ超）** と、未カバー残差としては最大。
しかし **A/B は取られていない**（`FEATURE_COLS_WT` に入らないまま1ヶ月放置）。

方法論は `exp_basic_elements_ab.py` / `exp_racetype_field_ab.py` と同一
（2窓 × 5seed・deterministic・AUC / 指数1位の勝率・3着内率 / 上位2車そろい率）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

from src.database import get_connection  # noqa: E402
from src.preprocessing.feature_wt import (  # noqa: E402
    FEATURE_COLS_WT, FORM_QUALITY_COLS_WT, MEETING_FORM_COLS_WT, TARGET_COL_WT,
    add_form_quality_features_wt, add_meeting_form_features_wt,
    build_features_wt, load_raw_data_wt,
)

TRAIN_FROM = "2024-04-01"
WINDOWS = {"w1": ("2026-04-13", "2026-07-15"), "w2": ("2026-01-01", "2026-04-12")}
SEEDS = [42, 101, 202, 303, 404]


def race_metrics(test: pd.DataFrame, prob: np.ndarray, ne_map: dict) -> tuple:
    """指数1位の勝率 / 3着内率 と、**上位2車そろい率**（軸2車が両方3着以内）。"""
    t = test.copy()
    t["p"] = prob
    win = top3 = pair = n = 0
    for rk, g in t.groupby("race_key"):
        if ne_map.get(rk) != 7 or len(g) != 7:
            continue
        fo = pd.to_numeric(g["finish_order"], errors="coerce")
        if (fo.notna() & (fo >= 1)).sum() < 3:
            continue
        g = g.assign(_fo=fo)
        o = g.sort_values("p", ascending=False)
        a1, a2 = o.iloc[0], o.iloc[1]
        f = a1["_fo"] if a1["_fo"] == a1["_fo"] else 99
        n += 1
        win += 1 if f == 1 else 0
        top3 += 1 if 1 <= f <= 3 else 0
        pair += 1 if (1 <= (a1["_fo"] or 99) <= 3 and 1 <= (a2["_fo"] or 99) <= 3) else 0
    return (win / n if n else 0, top3 / n if n else 0, pair / n if n else 0, n)


def main() -> None:
    print(f"データ読み込み ... baseline={len(FEATURE_COLS_WT)}特徴", flush=True)
    max_to = max(t for _, t in WINDOWS.values())
    df = build_features_wt(load_raw_data_wt(min_date=TRAIN_FROM, max_date=max_to))
    print("節内成績を付与 ...", flush=True)
    df = add_meeting_form_features_wt(df)
    print("走りの質を付与 ...", flush=True)
    df = add_form_quality_features_wt(df)
    NEW = list(MEETING_FORM_COLS_WT) + list(FORM_QUALITY_COLS_WT)
    miss = [c for c in NEW if c not in df.columns]
    if miss:
        raise SystemExit(f"列が作られていない: {miss}")
    print(df[NEW].describe().loc[["mean", "std", "min", "max"]]
          .round(4).to_string())

    arms = [("baseline", list(FEATURE_COLS_WT)),
            ("+節内成績", list(FEATURE_COLS_WT) + list(MEETING_FORM_COLS_WT)),
            ("+走りの質", list(FEATURE_COLS_WT) + list(FORM_QUALITY_COLS_WT)),
            ("+両方", list(FEATURE_COLS_WT) + NEW)]

    for wn, (tf, tt) in WINDOWS.items():
        with get_connection() as conn:
            ne_map = dict(conn.execute(
                "SELECT race_key, n_entries FROM wt_races WHERE race_date BETWEEN ? AND ?",
                (tf, tt)))
        train = df[(df["race_date"] >= TRAIN_FROM) & (df["race_date"] < tf)]
        test = df[(df["race_date"] >= tf) & (df["race_date"] <= tt)]
        print(f"\n######## {wn} test={tf}〜{tt}  train {len(train):,} / test {len(test):,}")
        from sklearn.metrics import roc_auc_score
        res = {}
        for arm, cols in arms:
            a, w, t3, pr = [], [], [], []
            m = None
            for seed in SEEDS:
                m = lgb.LGBMClassifier(
                    objective="binary", n_estimators=500, learning_rate=0.05,
                    num_leaves=31, min_child_samples=20, subsample=0.8,
                    colsample_bytree=0.8, random_state=seed,
                    deterministic=True, force_row_wise=True, verbose=-1)
                m.fit(train[cols], train[TARGET_COL_WT])
                p = m.predict_proba(test[cols])[:, 1]
                a.append(roc_auc_score(test[TARGET_COL_WT], p))
                x, y, z, n = race_metrics(test, p, ne_map)
                w.append(x); t3.append(y); pr.append(z)
            res[arm] = (a, w, t3, pr)
            print(f"== {arm} ({len(cols)}特徴) ==")
            print(f"   AUC        {np.mean(a):.5f} ± {np.std(a):.5f}")
            print(f"   1位勝率    {np.mean(w)*100:.2f}% ± {np.std(w)*100:.2f} (n={n:,})")
            print(f"   1位3着内   {np.mean(t3)*100:.2f}% ± {np.std(t3)*100:.2f}")
            print(f"   上位2車そろい {np.mean(pr)*100:.2f}% ± {np.std(pr)*100:.2f}")
            if arm != "baseline" and m is not None:
                imp = pd.Series(m.feature_importances_, index=cols)
                rk = imp.rank(ascending=False).astype(int)
                for c in [x for x in NEW if x in cols]:
                    print(f"     {c:<18} imp={imp[c]:5d}  順位 {rk[c]}/{len(cols)}")
        b = res["baseline"]
        for arm in ("+節内成績", "+走りの質", "+両方"):
            v = res[arm]
            print(f"== 差分（{arm} − baseline）==")
            print(f"   ΔAUC        {np.mean(v[0])-np.mean(b[0]):+.5f}"
                  f"   (baseline の seed 標準偏差 {np.std(b[0]):.5f})")
            print(f"   Δ1位勝率    {(np.mean(v[1])-np.mean(b[1]))*100:+.2f}pt")
            print(f"   Δ1位3着内   {(np.mean(v[2])-np.mean(b[2]))*100:+.2f}pt")
            print(f"   Δ上位2車そろい {(np.mean(v[3])-np.mean(b[3]))*100:+.2f}pt")


if __name__ == "__main__":
    main()
