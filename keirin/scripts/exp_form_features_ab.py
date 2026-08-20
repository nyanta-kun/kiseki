"""節内成績・結果条件つきローリング・単独先行の A/B 検証（2026-08-20 新設）。

背景 — なぜこの3群なのか
------------------------
[[keirin_verification_audit_2026_08_20]] で、過去の棄却に系統的な誤りが2件見つかった。
うち誤り②「ROI 1.333 基準を特徴量候補に誤適用」により、**独立2手法で確認済みの
信号が実装されないまま約1年放置**されていた。本スクリプトはその回収と、
今セッションで新たに測った最大残差（節内成績 1.74pt）の検証を行う。

| アーム | 特徴 | 根拠（実測残差 = 実測3着内率 − 予測p3） |
|---|---|---|
| `+meeting` | cup_n_so_far / cup_top3_rate / cup_win_rate / cup_mean_order_n | 節内 全外 −0.17 ↔ 全的中 +1.57 = **1.74pt**（10σ超） |
| `+quality` | b_sink_rate_90 / b_hold_rate_90 / fh_lost_rate_90 | 上がり上位×着外 +1.10 ↔ 上がり下位×着外 +0.22 = **0.88pt**（4σ） |
| `+lone` | is_lone_senko | 逃1人の逃げ選手 **+2.7pt**（逃2人以上は +0.2〜0.6pt） |
| `+all` | 上記すべて | |

検証設計は `exp_sb_dyn_ab.py` と同一（2独立窓 × 5seed・deterministic=True）。

🔴 **記録する指標を4つに増やしてある。** 監査で「ΔAUC しか記録が無く判定根拠が
   追えない」事例（`exp_b_pred_ab.py`）が出たため、ΔAUC / Δ1位勝率 / Δ1位3着内 /
   Δ二軸 を必ず全部出す。

採用ライン（先に固定・事後に動かさない）:
  **両窓で符号が一致** かつ **Δ1位3着内 ≥ +0.3pt**
  （前回採用した line_leader 群は Δ1位勝率 +0.24pt だったので、それと同等以上）

Usage:
    PYTHONPATH=. .venv/bin/python scripts/exp_form_features_ab.py [--windows w1,w2]
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import lightgbm as lgb

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from src.preprocessing.feature_wt import (
    FEATURE_COLS_WT, TARGET_COL_WT, build_features_wt, load_raw_data_wt,
    add_form_quality_features_wt, add_meeting_form_features_wt,
    FORM_QUALITY_COLS_WT, MEETING_FORM_COLS_WT,
)
from src.database import get_connection

TRAIN_FROM = "2024-04-01"          # S/B ラベル(2024-01〜)の90D窓が充足する時点
WINDOWS = {
    "w1": ("2026-04-13", "2026-07-15"),
    "w2": ("2026-01-01", "2026-04-12"),
}
SEEDS = [42, 101, 202, 303, 404]
LONE_COLS = ["is_lone_senko"]

ARMS = {
    "base":     [],
    "+meeting": MEETING_FORM_COLS_WT,
    "+quality": FORM_QUALITY_COLS_WT,
    "+lone":    LONE_COLS,
    "+all":     MEETING_FORM_COLS_WT + FORM_QUALITY_COLS_WT + LONE_COLS,
}


def race_metrics(test: pd.DataFrame, prob: np.ndarray, ne_map: dict) -> dict:
    """7車レースの 指数1位の勝率・3着内率、および二軸（上位2車とも3着内）。"""
    t = test.copy()
    t["p"] = prob
    win = top3 = two = n = 0
    for rk, g in t.groupby("race_key"):
        if ne_map.get(rk) != 7 or len(g) != 7:
            continue
        fo = pd.to_numeric(g["finish_order"], errors="coerce")
        if (fo >= 1).sum() < 3:
            continue
        order = g["p"].values.argsort()[::-1]
        f1 = fo.iloc[order[0]]
        f2 = fo.iloc[order[1]]
        f1 = 99 if pd.isna(f1) or f1 < 1 else f1
        f2 = 99 if pd.isna(f2) or f2 < 1 else f2
        n += 1
        win += 1 if f1 == 1 else 0
        top3 += 1 if f1 <= 3 else 0
        two += 1 if (f1 <= 3 and f2 <= 3) else 0
    if not n:
        return {"win": 0.0, "top3": 0.0, "two": 0.0, "n": 0}
    return {"win": win / n, "top3": top3 / n, "two": two / n, "n": n}


def run_window(df: pd.DataFrame, test_from: str, test_to: str) -> dict:
    with get_connection() as conn:
        ne_map = dict(conn.execute(
            "SELECT race_key, n_entries FROM wt_races WHERE race_date BETWEEN ? AND ?",
            (test_from, test_to)))
    train = df[(df["race_date"] >= TRAIN_FROM) & (df["race_date"] < test_from)]
    test = df[(df["race_date"] >= test_from) & (df["race_date"] <= test_to)]
    print(f"\n######## 窓 test={test_from}〜{test_to}  "
          f"train {len(train):,}行 / test {len(test):,}行 ########", flush=True)

    from sklearn.metrics import roc_auc_score
    res = {}
    for arm, extra in ARMS.items():
        cols = FEATURE_COLS_WT + extra
        acc = {"auc": [], "win": [], "top3": [], "two": []}
        n = 0
        for seed in SEEDS:
            m = lgb.LGBMClassifier(
                objective="binary", n_estimators=500, learning_rate=0.05,
                num_leaves=31, min_child_samples=20, subsample=0.8,
                colsample_bytree=0.8, random_state=seed,
                deterministic=True, force_row_wise=True, verbose=-1)
            m.fit(train[cols], train[TARGET_COL_WT])
            p = m.predict_proba(test[cols])[:, 1]
            acc["auc"].append(roc_auc_score(test[TARGET_COL_WT], p))
            r = race_metrics(test, p, ne_map)
            for k in ("win", "top3", "two"):
                acc[k].append(r[k])
            n = r["n"]
        res[arm] = {k: float(np.mean(v)) for k, v in acc.items()}
        res[arm]["auc_sd"] = float(np.std(acc["auc"]))
        print(f"== {arm} ({len(cols)}特徴) ==  n={n}")
        print(f"   AUC {res[arm]['auc']:.5f} ±{res[arm]['auc_sd']:.5f} / "
              f"1位勝率 {res[arm]['win']*100:.2f}% / "
              f"1位3着内 {res[arm]['top3']*100:.2f}% / 二軸 {res[arm]['two']*100:.2f}%",
              flush=True)
        if arm == "+all":
            imp = pd.Series(m.feature_importances_, index=cols)
            rk = imp.rank(ascending=False).astype(int)
            for c in ARMS["+all"]:
                print(f"     {c:<18} imp={imp[c]:5d}  順位 {rk[c]}/{len(cols)}")
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--windows", default="w1,w2")
    args = ap.parse_args()

    print("データ読み込み ...", flush=True)
    max_to = max(t for _, t in WINDOWS.values())
    raw = load_raw_data_wt(min_date="2024-01-01", max_date=max_to)
    df = build_features_wt(raw)
    print(f"  特徴量構築 {len(df):,}行", flush=True)
    df = add_meeting_form_features_wt(df)
    df = add_form_quality_features_wt(df)
    print("  追加特徴 付与完了", flush=True)

    out = {}
    for w in args.windows.split(","):
        out[w] = run_window(df, *WINDOWS[w])

    print("\n" + "=" * 72)
    print("== 差分（各アーム − base）==")
    hdr = f"{'アーム':<10}" + "".join(f"{w:>28}" for w in out)
    print(hdr)
    for arm in ARMS:
        if arm == "base":
            continue
        line = f"{arm:<10}"
        for w, r in out.items():
            d_auc = r[arm]["auc"] - r["base"]["auc"]
            d_t3 = (r[arm]["top3"] - r["base"]["top3"]) * 100
            d_w = (r[arm]["win"] - r["base"]["win"]) * 100
            d_2 = (r[arm]["two"] - r["base"]["two"]) * 100
            line += f"  AUC{d_auc:+.5f} 勝{d_w:+.2f} 3着{d_t3:+.2f} 二軸{d_2:+.2f}"
        print(line)
    print("\n採用ライン: 両窓で符号一致 かつ Δ1位3着内 ≥ +0.30pt")


if __name__ == "__main__":
    main()
