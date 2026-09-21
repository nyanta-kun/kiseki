#!/usr/bin/env python3
"""出走車数（フィールドサイズ）と Σp3 正規化の検証（2026-09-21）。

発端: `docs/meeting_structure_unused_2026_09_21.md` §4。
`FEATURE_COLS_WT`（70特徴）に**車数が無い**ため、3着内率のレースごとの合計
Σp3 が理想の 3.0 から車数で系統的にずれている（5車 2.79 / 7車 3.03 / 9車 3.16）。

本稿はそれを2通りで検証する。**別物なので分けて測る**:

  A. モデルに車数を入れる（`field_size`）→ 順位もレース間較正も動きうる
  B. 出力を Σp3 = 3.0 へ正規化する（学習なし・レース内の順位は不変）
     → 動くのは `axis_sum >= AXIS_SUM_FIRM` のような**絶対閾値のゲートだけ**

方法論は `exp_meeting_form_ab.py` と同一（2窓 × 5seed・deterministic）。
⚠️ `build_features_wt` は既に `add_meeting_form_features_wt` を呼ぶ（70特徴）。
   旧 A/B のように自分で呼び足さないこと（二重付与で KeyError）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from src.preprocessing.feature_wt import (  # noqa: E402
    FEATURE_COLS_WT, TARGET_COL_WT, load_features_wt,
)
from src.type_lab import AXIS_SUM_FIRM  # noqa: E402

TRAIN_FROM = "2024-04-01"
WINDOWS = {"w1": ("2026-04-13", "2026-07-15"), "w2": ("2026-01-01", "2026-04-12")}
SEEDS = [42, 101, 202, 303, 404]
CARS = (7, 9)


def fit_predict(train, test, cols, seed):
    m = lgb.LGBMClassifier(
        objective="binary", n_estimators=500, learning_rate=0.05,
        num_leaves=31, min_child_samples=20, subsample=0.8,
        colsample_bytree=0.8, random_state=seed,
        deterministic=True, force_row_wise=True, verbose=-1)
    m.fit(train[cols], train[TARGET_COL_WT])
    return m.predict_proba(test[cols])[:, 1]


def race_metrics(t: pd.DataFrame, pcol: str, n_car: int) -> dict:
    """指数1位の勝率/3着内率と上位2車そろい率（車数を指定して評価）。"""
    g = t[t["field_size"] == n_car]
    win = top3 = pair = n = 0
    for _, r in g.groupby("race_key"):
        fo = r["_fo"]
        if (fo >= 1).sum() < 3:
            continue
        o = r.sort_values(pcol, ascending=False)
        f1, f2 = o["_fo"].iloc[0], o["_fo"].iloc[1]
        n += 1
        win += int(f1 == 1)
        top3 += int(1 <= f1 <= 3)
        pair += int(1 <= f1 <= 3 and 1 <= f2 <= 3)
    if not n:
        return {}
    return {"n": n, "win": win / n, "top3": top3 / n, "pair": pair / n}


def axis_table(t: pd.DataFrame, pcol: str, n_car: int) -> dict:
    """axis_sum（上位2車の合計）を AXIS_SUM_FIRM で切ったときの分離。"""
    g = t[t["field_size"] == n_car]
    rows = []
    for _, r in g.groupby("race_key"):
        if (r["_fo"] >= 1).sum() < 3:
            continue
        o = r.sort_values(pcol, ascending=False)
        pair = int(1 <= o["_fo"].iloc[0] <= 3 and 1 <= o["_fo"].iloc[1] <= 3)
        rows.append((o[pcol].iloc[:2].sum(), pair))
    if not rows:
        return {}
    a = pd.DataFrame(rows, columns=["axis", "pair"])
    firm = a[a.axis >= AXIS_SUM_FIRM]
    soft = a[a.axis < AXIS_SUM_FIRM]
    return {
        "R": len(a), "firm_rate": len(firm) / len(a),
        "firm_pair": firm.pair.mean() if len(firm) else np.nan,
        "soft_pair": soft.pair.mean() if len(soft) else np.nan,
        "sep": (firm.pair.mean() - soft.pair.mean()) if len(firm) and len(soft) else np.nan,
        "_df": a,
    }


def boot_sep(a: pd.DataFrame, b: pd.DataFrame, n_boot=2000, seed=0) -> tuple:
    """同一レース集合上での分離差（b − a）のブートストラップCI。"""
    rng = np.random.default_rng(seed)
    idx = np.arange(len(a))
    out = []
    av, ap = a.axis.values, a.pair.values
    bv, bp = b.axis.values, b.pair.values
    for _ in range(n_boot):
        s = rng.choice(idx, len(idx), replace=True)
        def sep(v, p):
            f = v[s] >= AXIS_SUM_FIRM
            if f.all() or (~f).all():
                return np.nan
            return p[s][f].mean() - p[s][~f].mean()
        out.append(sep(bv, bp) - sep(av, ap))
    out = np.array(out, dtype=float)
    out = out[~np.isnan(out)]
    return float(np.mean(out)), float(np.percentile(out, 2.5)), float(np.percentile(out, 97.5))


def main() -> None:
    print("データ読み込み ...", flush=True)
    df = load_features_wt("2022-12-01", "2026-08-31", use_cache=True)
    df = df[(df["race_date"] >= TRAIN_FROM) & (df["race_date"] <= max(t for _, t in WINDOWS.values()))]
    # 実際のフィールドサイズ（`wt_races.n_entries` ではなく出走表の行数。
    # 🔴 JRA v28 の `place_slots` と同じ理由＝申告値は欠車で実体とずれる。実測 1.08%）
    df["field_size"] = df.groupby("race_key")["frame_no"].transform("size").astype(int)
    df["_fo"] = pd.to_numeric(df["finish_order"], errors="coerce").fillna(99)
    print(f"  行 {len(df):,}  車数分布 {df.groupby('race_key')['field_size'].first().value_counts().to_dict()}")

    arms = [("base", list(FEATURE_COLS_WT)),
            ("+車数", list(FEATURE_COLS_WT) + ["field_size"])]

    for wn, (tf, tt) in WINDOWS.items():
        train = df[df["race_date"] < tf]
        test = df[(df["race_date"] >= tf) & (df["race_date"] <= tt)].copy()
        print(f"\n{'='*78}\n######## {wn}  test={tf}〜{tt}   train {len(train):,} / test {len(test):,}")
        for arm, cols in arms:
            ps = np.column_stack([fit_predict(train, test, cols, s) for s in SEEDS])
            test["p_raw"] = ps.mean(axis=1)          # seed 平均（順位はほぼseed不変）
            tot = test.groupby("race_key")["p_raw"].transform("sum")
            test["p_norm"] = test["p_raw"] * 3.0 / tot
            auc = [roc_auc_score(test[TARGET_COL_WT], ps[:, i]) for i in range(len(SEEDS))]
            print(f"\n── arm={arm} ({len(cols)}特徴)   AUC {np.mean(auc):.5f} ± {np.std(auc):.5f}")
            s = test.groupby("race_key").agg(k=("p_raw", "size"), sp=("p_raw", "sum"))
            print("   Σp3 平均: " + "  ".join(
                f"{int(n)}車 {v:.3f}" for n, v in s.groupby("k")["sp"].mean().items() if n in CARS))
            for nc in CARS:
                rm = race_metrics(test, "p_raw", nc)
                if not rm:
                    continue
                print(f"   [{nc}車 n={rm['n']:,}] 1位勝率 {rm['win']*100:.2f}%  "
                      f"1位3着内 {rm['top3']*100:.2f}%  上位2車そろい {rm['pair']*100:.2f}%")
                ar = axis_table(test, "p_raw", nc)
                an = axis_table(test, "p_norm", nc)
                for tag, a in (("生 ", ar), ("正規", an)):
                    print(f"      axis {tag}: 堅い {a['firm_rate']*100:5.1f}%  "
                          f"堅い側そろい {a['firm_pair']*100:5.2f}%  "
                          f"それ以外 {a['soft_pair']*100:5.2f}%  分離 {a['sep']*100:+5.2f}pt")
                m, lo, hi = boot_sep(ar["_df"], an["_df"])
                print(f"      分離差（正規 − 生）{m*100:+.2f}pt  95%CI [{lo*100:+.2f}, {hi*100:+.2f}]")


if __name__ == "__main__":
    main()
