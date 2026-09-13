#!/usr/bin/env python3
"""p3 モデル本体の余地 — 3腕の A/B（2026-09-14）。

方法論は `exp_meeting_form_ab.py` と同一（2窓 × 5seed・deterministic）。
指標は AUC / 指数1位の勝率・3着内率 / **上位2車そろい率**（＝軸崩壊の裏返し）。

## なぜこの3腕か（先に本番と棚卸しを読んだ結果）

- 未使用入力の棚卸しは `unused_morning_inputs_2026_09_10.md` で尽きている
  （節内成績は採用済み・走りの質は窓で符号割れ・コメントは増分ゼロ・天候は無情報・
   `ex_*` はリーク・`pred_*_pct` は自社出力）。**DB 内の「使っていない列」は残っていない。**
- したがって残るのは「**使い方**」の側:

  ① **`n_entries`（車数）をモデルに教える。** `FEATURE_COLS_WT` に車数が無い。
     学習データの **15% は7車以外**（9車 8.1% / 6車 5.2% / 5車 1.2% / 8車 0.5%）で、
     `score_rank` / `wr_rank` / `top3r_rank` / `frame_no` / `line_*` は車数で意味が変わる。
     モデルは1行ずつ見るので、車数を直接には知りようがない。
  ② **H2H の再検証。** 実装済みだが 2026-07-28 に「S1/S9 の ROI 悪化」で撤回された。
     🔴 その判定根拠は**いまは使っていない旧ランクの ROI** で、現在の作法は
     「ROI で採否を決めない」（`DESIGN.md` 不変条件6）。**同じ物差しで測り直す。**
  ③ **7車のみで学習。** 商品は7車が主力。15% の他車数を落とすとデータは減るが
     ランク系特徴の意味が揃う。

⚠️ 窓 w1/w2 は `keirin_protocol.BURNED_WINDOWS` の焼けた窓＝**VAL 扱い**。
   ここで出るのは「探索の結果」であって採否ではない。採るなら TEST を4窓。

    PYTHONPATH=. .venv/bin/python scripts/exp_model_headroom_ab.py
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
    FEATURE_COLS_WT, H2H_COLS_WT, TARGET_COL_WT,
    add_h2h_features_wt, build_features_wt, load_raw_data_wt,
)

TRAIN_FROM = "2024-04-01"
WINDOWS = {"w1": ("2026-04-13", "2026-07-15"), "w2": ("2026-01-01", "2026-04-12")}
SEEDS = [42, 101, 202, 303, 404]


def race_metrics(test: pd.DataFrame, prob: np.ndarray, ne_map: dict) -> tuple:
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
    print(f"読み込み ... baseline={len(FEATURE_COLS_WT)}特徴", flush=True)
    max_to = max(t for _, t in WINDOWS.values())
    df = build_features_wt(load_raw_data_wt(min_date=TRAIN_FROM, max_date=max_to))
    print("H2H を付与 ...", flush=True)
    df = add_h2h_features_wt(df)
    miss = [c for c in H2H_COLS_WT if c not in df.columns]
    if miss:
        raise SystemExit(f"H2H 列が作られていない: {miss}")
    # 車数
    with get_connection() as conn:
        ne_all = dict(conn.execute(
            "SELECT race_key, n_entries FROM wt_races WHERE race_date >= ?", (TRAIN_FROM,)))
    df["n_entries"] = df["race_key"].map(ne_all).fillna(7).astype(float)
    print(df[list(H2H_COLS_WT) + ["n_entries"]].describe()
          .loc[["mean", "std", "min", "max"]].round(4).to_string(), flush=True)

    BASE = list(FEATURE_COLS_WT)
    arms = [
        ("baseline", BASE, False),
        ("+車数", BASE + ["n_entries"], False),
        ("+H2H", BASE + list(H2H_COLS_WT), False),
        ("7車のみ学習", BASE, True),
        ("+車数+H2H", BASE + ["n_entries"] + list(H2H_COLS_WT), False),
    ]

    from sklearn.metrics import roc_auc_score
    for wn, (tf, tt) in WINDOWS.items():
        ne_map = {k: v for k, v in ne_all.items()}
        train_all = df[(df["race_date"] >= TRAIN_FROM) & (df["race_date"] < tf)]
        test = df[(df["race_date"] >= tf) & (df["race_date"] <= tt)]
        print(f"\n######## {wn} test={tf}〜{tt}  train {len(train_all):,} / test {len(test):,}",
              flush=True)
        res = {}
        for arm, cols, only7 in arms:
            train = train_all[train_all["n_entries"] == 7] if only7 else train_all
            a, w, t3, pr = [], [], [], []
            n = 0
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
            print(f"== {arm} ({len(cols)}特徴 / 学習 {len(train):,}行) ==", flush=True)
            print(f"   AUC        {np.mean(a):.5f} ± {np.std(a):.5f}")
            print(f"   1位勝率    {np.mean(w)*100:.2f}% ± {np.std(w)*100:.2f} (n={n:,})")
            print(f"   1位3着内   {np.mean(t3)*100:.2f}% ± {np.std(t3)*100:.2f}")
            print(f"   上位2車そろい {np.mean(pr)*100:.2f}% ± {np.std(pr)*100:.2f}", flush=True)
        b = res["baseline"]
        for arm, _, _ in arms[1:]:
            v = res[arm]
            print(f"== 差分（{arm} − baseline）==")
            print(f"   ΔAUC        {np.mean(v[0])-np.mean(b[0]):+.5f}"
                  f"   (baseline の seed 標準偏差 {np.std(b[0]):.5f})")
            print(f"   Δ1位勝率    {(np.mean(v[1])-np.mean(b[1]))*100:+.2f}pt")
            print(f"   Δ1位3着内   {(np.mean(v[2])-np.mean(b[2]))*100:+.2f}pt")
            print(f"   Δ上位2車そろい {(np.mean(v[3])-np.mean(b[3]))*100:+.2f}pt", flush=True)


if __name__ == "__main__":
    main()
