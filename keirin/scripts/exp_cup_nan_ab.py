#!/usr/bin/env python3
"""節内成績の欠測の埋め方 A/B（2026-09-21・ユーザー指摘の残差から）。

## なぜこれか

`add_meeting_form_features_wt` は最後に **`fillna(0.0)`** しているので、
**初日（履歴なし）と「開催内で3着内なし」が同じ 0.0 で渡される**。
`cup_n_so_far` との交互作用で区別する設計だが、分割重要度は 53〜62位/73 で
実際には学習されていない。自社 p3 の残差を層別すると別物だった（予測1位・両窓）:

    初日（履歴なし）        -1.26 / -1.99pt  ★両窓有意
    2走+ 3着内なし         -2.82 / -3.26pt  ★両窓有意（該当は全選手の 27.4%）

JRA v28 の「欠損は NaN のまま LightGBM に渡す。50.0 で埋めない」と同型。

## 旧版（節内成績そのものの A/B）

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
    # 🔴 `build_features_wt` が 2026-09-10 から `add_meeting_form_features_wt` を
    #    呼ぶようになっている（`FEATURE_COLS_WT` が 66 → 70特徴）。ここで再度呼ぶと
    #    merge で列が重複して `cup_n_so_far` が消える（実際に踏んだ）。
    assert all(c in df.columns for c in MEETING_FORM_COLS_WT), "節内成績が付いていない"
    print(df[list(MEETING_FORM_COLS_WT)].describe()
          .loc[["mean", "std", "min", "max"]].round(4).to_string())

    # ── 腕 ──
    # baseline : 現行（初日を 0.0 で埋める）
    # nan      : 初日を NaN のまま渡す（cup_n_so_far は 0 のまま残す）
    # flag     : 明示フラグ 2本を足す（2走+3着内なし / 2走+全て3着内）
    # nan+flag : 両方
    FILL = ["cup_top3_rate", "cup_win_rate", "cup_mean_order_n"]
    df_nan = df.copy()
    m0 = df_nan["cup_n_so_far"] <= 0
    for c in FILL:
        df_nan.loc[m0, c] = np.nan
    print(f"  初日を NaN にした行: {int(m0.sum()):,} / {len(df):,} ({m0.mean()*100:.1f}%)")

    for d in (df, df_nan):
        d["cup_no_top3"] = ((d["cup_n_so_far"] >= 2)
                            & (d["cup_top3_rate"].fillna(1.0) <= 0)).astype(float)
        d["cup_all_top3"] = ((d["cup_n_so_far"] >= 2)
                             & (d["cup_top3_rate"].fillna(0.0) >= 0.999)).astype(float)
    FLAGS = ["cup_no_top3", "cup_all_top3"]
    print(f"  cup_no_top3 = {df['cup_no_top3'].mean()*100:.1f}% / "
          f"cup_all_top3 = {df['cup_all_top3'].mean()*100:.1f}%")

    BASE = list(FEATURE_COLS_WT)
    arms = [("baseline", BASE, df),
            ("nan（初日をNaN）", BASE, df_nan),
            ("flag（明示2本）", BASE + FLAGS, df),
            ("nan+flag", BASE + FLAGS, df_nan)]
    NEW = FLAGS

    for wn, (tf, tt) in WINDOWS.items():
        with get_connection() as conn:
            ne_map = dict(conn.execute(
                "SELECT race_key, n_entries FROM wt_races WHERE race_date BETWEEN ? AND ?",
                (tf, tt)))
        print(f"\n######## {wn} test={tf}〜{tt}")
        from sklearn.metrics import roc_auc_score
        res = {}
        for arm, cols, dd in arms:
            train = dd[(dd["race_date"] >= TRAIN_FROM) & (dd["race_date"] < tf)]
            test = dd[(dd["race_date"] >= tf) & (dd["race_date"] <= tt)]
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
        for arm in ("nan（初日をNaN）", "flag（明示2本）", "nan+flag"):
            v = res[arm]
            print(f"== 差分（{arm} − baseline）==")
            print(f"   ΔAUC        {np.mean(v[0])-np.mean(b[0]):+.5f}"
                  f"   (baseline の seed 標準偏差 {np.std(b[0]):.5f})")
            print(f"   Δ1位勝率    {(np.mean(v[1])-np.mean(b[1]))*100:+.2f}pt")
            print(f"   Δ1位3着内   {(np.mean(v[2])-np.mean(b[2]))*100:+.2f}pt")
            print(f"   Δ上位2車そろい {(np.mean(v[3])-np.mean(b[3]))*100:+.2f}pt")


if __name__ == "__main__":
    main()
