"""地方競馬 Phase2 実験: 確率較正(OOF isotonic) + 単複ヘッド分離

現状 win_probability は top3 モデルの softmax(T=10) で、実勝率に較正されていない。
sweet_spot / place_bet の EV ゲートはこの未較正確率に依存している。

本スクリプトは:
  - 単勝ヘッド is_win(1着) と 複勝ヘッド is_top3(3着以内) を分離学習(複数seed平均)
  - 時系列較正ホールドアウトで IsotonicRegression を学習(リークなし)
  - test(OOS)で 較正品質(ECE/信頼度曲線) と EVゲートのROI を比較

評価軸:
  1. ECE: 現production相当(top3 softmax) vs 生is_win vs 較正is_win
  2. ランキング: is_win head の top1勝率(vs top3 head)
  3. EVゲート(sweet_spot相当): 較正P(win)×odds∈[1.0,2.0]∧odds≥10 の ROI/的中/CI/drop1

使い方:
  cd backend
  .venv/bin/python scripts/chihou_phase2_calibration.py --seeds 5
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv

load_dotenv(_root.parent / ".env")

import lightgbm as lgb
import numpy as np
import pandas as pd
import psycopg2
from sklearn.isotonic import IsotonicRegression

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("chihou_phase2")

CHIHOU_V9_VERSION = 9
FAVORABLE_COURSES = {"浦和", "金沢", "高知", "笠松", "盛岡"}

FEATURES = [
    "speed_index", "last3f_index", "jockey_index", "rotation_index", "last_margin_index",
    "distance", "head_count", "is_turf", "is_dirt", "is_good", "is_heavy", "is_bad",
    "frame_number", "horse_age", "weight_carried", "horse_weight", "weight_change",
]

BASE_QUERY = """
SELECT
    ci.race_id, r.date, r.course_name, r.surface, r.condition, r.distance, r.head_count,
    re.frame_number, re.horse_age, re.weight_carried,
    COALESCE(re.horse_weight, 500) AS horse_weight,
    COALESCE(re.weight_change, 0)  AS weight_change,
    COALESCE(ci.speed_index, 50.0)       AS speed_index,
    COALESCE(ci.last3f_index, 50.0)      AS last3f_index,
    COALESCE(ci.jockey_index, 50.0)      AS jockey_index,
    COALESCE(ci.rotation_index, 50.0)    AS rotation_index,
    COALESCE(ci.last_margin_index, 50.0) AS last_margin_index,
    rr.finish_position, rr.win_odds
FROM chihou.calculated_indices ci
JOIN chihou.races r ON r.id = ci.race_id
JOIN chihou.race_entries re ON re.race_id = ci.race_id AND re.horse_id = ci.horse_id
JOIN chihou.race_results rr ON rr.race_id = ci.race_id AND rr.horse_number = re.horse_number
WHERE ci.version = %(ver)s AND r.course != '83' AND r.head_count >= 6
  AND r.date BETWEEN %(start)s AND %(end)s
  AND COALESCE(rr.abnormality_code, 0) = 0
  AND rr.finish_position IS NOT NULL AND rr.win_odds IS NOT NULL
ORDER BY r.date, ci.race_id
"""

PARAMS = {
    "objective": "binary", "metric": "binary_logloss",
    "num_leaves": 31, "max_depth": 5, "min_data_in_leaf": 50,
    "lambda_l1": 0.1, "lambda_l2": 1.0, "learning_rate": 0.05,
    "feature_fraction": 0.8, "bagging_fraction": 0.8, "bagging_freq": 5, "verbose": -1,
}


def fetch(conn, start, end):
    cur = conn.cursor()
    cur.execute(BASE_QUERY, {"ver": CHIHOU_V9_VERSION, "start": start, "end": end})
    cols = [d[0] for d in cur.description]
    df = pd.DataFrame(cur.fetchall(), columns=cols)
    cur.close()
    return df


def featurize(df):
    df = df.copy()
    s = df["surface"].fillna("").astype(str)
    df["is_turf"] = s.str.contains("芝").astype(int)
    df["is_dirt"] = s.str.contains("ダ").astype(int)
    c = df["condition"].fillna("").astype(str)
    df["is_good"] = (c == "良").astype(int)
    df["is_heavy"] = (c == "重").astype(int)
    df["is_bad"] = (c == "不").astype(int)
    for col in FEATURES:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
    df["fp"] = pd.to_numeric(df["finish_position"], errors="coerce")
    df["win_odds"] = pd.to_numeric(df["win_odds"], errors="coerce")
    return df


def train_seeds(X, y, seeds):
    preds_fn = []
    for sd in seeds:
        p = dict(PARAMS, seed=sd)
        m = lgb.train(p, lgb.Dataset(X, y, feature_name=FEATURES), num_boost_round=400)
        preds_fn.append(m)
    return preds_fn


def predict_mean(models, X):
    return np.mean([m.predict(X) for m in models], axis=0)


def softmax_index(scores, race_ids):
    """production相当: min-max(15-85)→softmax(T=10)。レース内和=1。"""
    out = np.zeros(len(scores))
    tmp = pd.DataFrame({"r": np.asarray(race_ids), "s": scores})
    for _, idx in tmp.groupby("r").indices.items():
        s = tmp["s"].values[idx]
        lo, hi = s.min(), s.max()
        sc = np.full(len(s), 50.0) if hi - lo < 1e-9 else 15.0 + (s - lo) / (hi - lo) * 70.0
        st = sc / 10.0
        ex = np.exp(st - st.max())
        out[idx] = ex / ex.sum()
    return out


def ece(prob, y, n_bins=10):
    """Expected Calibration Error（等頻度ビン）。"""
    prob = np.asarray(prob)
    y = np.asarray(y)
    order = np.argsort(prob)
    bins = np.array_split(order, n_bins)
    e = 0.0
    rows = []
    for b in bins:
        if len(b) == 0:
            continue
        pm, ym = prob[b].mean(), y[b].mean()
        e += len(b) / len(prob) * abs(pm - ym)
        rows.append((pm, ym, len(b)))
    return e, rows


def roi_ci(picks, rng, n_boot=2000):
    n = len(picks)
    if n == 0:
        return dict(n=0, hit=0.0, roi=0.0, drop1=0.0, lo=0.0, hi=0.0)
    wins = (picks["fp"] == 1).values
    odds = picks["win_odds"].values
    pay = np.where(wins, odds, 0.0)
    roi = pay.sum() / n
    drop1 = (pay.sum() - pay.max()) / max(n - 1, 1) if pay.max() > 0 else roi
    boot = [rng.choice(pay, size=n, replace=True).mean() for _ in range(n_boot)]
    lo, hi = np.percentile(boot, [2.5, 97.5])
    return dict(n=n, hit=float(wins.mean()), roi=float(roi), drop1=float(drop1), lo=float(lo), hi=float(hi))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--train-start", default="20230101")
    p.add_argument("--fit-end", default="20250331")     # モデル学習
    p.add_argument("--cal-start", default="20250401")   # isotonic 較正用
    p.add_argument("--cal-end", default="20250630")
    p.add_argument("--test-start", default="20250701")
    p.add_argument("--test-end", default="20260605")
    p.add_argument("--seeds", type=int, default=5)
    args = p.parse_args()
    seeds = list(range(args.seeds))
    rng = np.random.default_rng(12345)

    dsn = (f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} dbname={os.getenv('DB_NAME')} "
           f"user={os.getenv('DB_USER')} password={os.getenv('DB_PASSWORD')}")
    conn = psycopg2.connect(dsn)
    fit = featurize(fetch(conn, args.train_start, args.fit_end))
    cal = featurize(fetch(conn, args.cal_start, args.cal_end))
    test = featurize(fetch(conn, args.test_start, args.test_end))
    conn.close()
    logger.info("fit=%d cal=%d test=%d", len(fit), len(cal), len(test))

    Xfit, Xcal, Xtest = (d[FEATURES].values.astype(float) for d in (fit, cal, test))
    y_win_fit = (fit["fp"] == 1).astype(int).values
    y_t3_fit = (fit["fp"] <= 3).astype(int).values

    logger.info("単勝ヘッド is_win 学習...")
    win_models = train_seeds(Xfit, y_win_fit, seeds)
    logger.info("複勝ヘッド is_top3 学習...")
    t3_models = train_seeds(Xfit, y_t3_fit, seeds)

    # ── isotonic 較正（cal期間で学習） ──
    iso_win = IsotonicRegression(out_of_bounds="clip").fit(predict_mean(win_models, Xcal), (cal["fp"] == 1).astype(int).values)
    iso_t3 = IsotonicRegression(out_of_bounds="clip").fit(predict_mean(t3_models, Xcal), (cal["fp"] <= 3).astype(int).values)

    # ── test 予測 ──
    raw_win = predict_mean(win_models, Xtest)
    raw_t3 = predict_mean(t3_models, Xtest)
    cal_win = iso_win.predict(raw_win)
    cal_t3 = iso_t3.predict(raw_t3)
    # production相当 win_probability = top3スコアの softmax
    prod_winprob = softmax_index(raw_t3, test["race_id"])

    yw = (test["fp"] == 1).astype(int).values
    yt3 = (test["fp"] <= 3).astype(int).values

    # ── ① 較正品質 (ECE) ──
    print("\n" + "=" * 76)
    print(f"Phase2 較正実験  test {args.test_start}-{args.test_end}  {test['race_id'].nunique()}R {len(test)}馬 / {len(seeds)}seed")
    print("=" * 76)
    print("--- ① 較正品質 ECE (低いほど良) ---")
    e1, _ = ece(prod_winprob, yw)
    e2, _ = ece(raw_win, yw)
    e3, rows3 = ece(cal_win, yw)
    e4, _ = ece(raw_t3, yt3)
    e5, _ = ece(cal_t3, yt3)
    print(f"  win:  production(top3 softmax) ECE={e1:.4f} / 生is_win ECE={e2:.4f} / 較正is_win ECE={e3:.4f}")
    print(f"  top3: 生is_top3 ECE={e4:.4f} / 較正is_top3 ECE={e5:.4f}")
    print("  較正is_win 信頼度曲線(予測 / 実績 / n):")
    for pm, ym, nb in rows3:
        print(f"    {pm:.3f} / {ym:.3f} / {nb}")

    # ── ② ランキング: is_win head top1勝率 ──
    test = test.copy()
    test["raw_win"] = raw_win
    test["raw_t3"] = raw_t3
    test["cal_win"] = cal_win
    print()
    for col, lab in [("raw_t3", "top3 head(現行相当)"), ("raw_win", "win head")]:
        t1 = test.loc[test.groupby("race_id")[col].idxmax()]
        print(f"--- ② {lab}: top1勝率 {(t1['fp']==1).mean()*100:.1f}% / 複勝 {(t1['fp']<=3).mean()*100:.1f}% ---")

    # ── ③ EVゲート (sweet_spot相当) ──
    print("\n--- ③ EVゲート: P(win)×odds∈[1.0,2.0] ∧ odds≥10 の単勝ROI ---")
    print(f"  {'確率源':<26}{'n':>6}{'的中':>8}{'ROI':>7}{'drop1':>7}{'95%CI':>16}")
    for prob, lab in [(prod_winprob, "production(top3 softmax)"), (cal_win, "較正is_win")]:
        test["_ev"] = prob * test["win_odds"].values
        cand = test[(test["win_odds"] >= 10) & (test["_ev"] >= 1.0) & (test["_ev"] <= 2.0)]
        # レース内で1頭(最良EV)に絞る
        if len(cand):
            picks = cand.loc[cand.groupby("race_id")["_ev"].idxmax()]
        else:
            picks = cand
        s = roi_ci(picks, rng)
        print(f"  {lab:<24}{s['n']:>6}{s['hit']*100:>7.1f}%{s['roi']:>7.3f}{s['drop1']:>7.3f}   [{s['lo']:.2f},{s['hi']:.2f}]")
        # 割安場限定も
        if len(cand):
            favp = picks[picks["course_name"].isin(FAVORABLE_COURSES)]
            sf = roi_ci(favp, rng)
            print(f"    └ 割安場限定        {sf['n']:>6}{sf['hit']*100:>7.1f}%{sf['roi']:>7.3f}{sf['drop1']:>7.3f}   [{sf['lo']:.2f},{sf['hi']:.2f}]")
    print("=" * 76)


if __name__ == "__main__":
    main()
