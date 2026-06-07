"""地方モデルに kichiuma sp_score / netkeiba idx を入力特徴として統合する A/B (5seed OOS)。

確立した検証規律: cutoff再学習→held-out真OOS・複数seed平均(単一seedは当たりくじ)・
ブートCI。外部指数は発走前公表値(pre-race time index/score)=リークなし。
baseline(本番21特徴) vs augmented(+外部指数特徴5本)を is_win/is_top3 両ヘッドで比較。

外部特徴(レース内正規化・欠損は中立値):
  kc_sp_z   : kichiuma sp_score のレース内z (欠損→0)
  nk_idx_z  : netkeiba idx_ave のレース内z (欠損→0)
  kc_rank_n : kichiuma sp_score のレース内順位/頭数 (0=最良, 欠損→0.5)
  nk_rank_n : netkeiba idx_ave のレース内順位/頭数 (0=最良, 欠損→0.5)
  ext_missing: 両外部指数欠損フラグ

評価(test=2025-07〜): top1勝率(is_winヘッド rank1) / top1複勝率(is_top3 rank1) /
単勝ROI(rank1 by is_win・win_odds) / ECE(is_win)。5seed 平均±std で baseline と比較。

使い方: PYTHONPATH=. .venv/bin/python scripts/ab_chihou_external_features.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_root.parent / ".env")

import lightgbm as lgb  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import psycopg2  # noqa: E402

from scripts.train_chihou_prod_lgb import (  # noqa: E402
    FEATURES,
    NUM_ROUNDS,
    PARAMS,
    fetch_hist,
    prep,
)

CUTOFF = os.getenv("AB_CUTOFF","20250630")
TEST_START = os.getenv("AB_TEST_START","20250701")
SEEDS = [0, 1, 2, 3, 4]
EXT_FEATURES = ["kc_sp_z", "nk_idx_z", "kc_rank_n", "nk_rank_n", "ext_missing"]

# BASE_QUERY + horse_number + 外部指数(kichiuma/netkeiba)
MY_QUERY = """
SELECT
    ci.race_id, r.date, r.course_name, r.prize_1st AS curr_prize,
    re.horse_id, re.horse_number, r.surface, r.condition, r.distance, r.head_count,
    re.frame_number, re.horse_age, re.weight_carried,
    COALESCE(re.horse_weight, 500) AS horse_weight,
    COALESCE(re.weight_change, 0)  AS weight_change,
    COALESCE(ci.speed_index, 50.0)       AS speed_index,
    COALESCE(ci.last3f_index, 50.0)      AS last3f_index,
    COALESCE(ci.jockey_index, 50.0)      AS jockey_index,
    COALESCE(ci.rotation_index, 50.0)    AS rotation_index,
    COALESCE(ci.last_margin_index, 50.0) AS last_margin_index,
    rr.finish_position, rr.win_odds,
    CASE WHEN nk.idx_ave ~ '^-?[0-9]+\\*?$'
         THEN regexp_replace(nk.idx_ave, '\\*', '')::float ELSE NULL END AS nk_idx,
    kc.sp_score AS kc_sp
FROM chihou.calculated_indices ci
JOIN chihou.races r ON r.id = ci.race_id
JOIN chihou.race_entries re ON re.race_id = ci.race_id AND re.horse_id = ci.horse_id
JOIN chihou.race_results rr ON rr.race_id = ci.race_id AND rr.horse_number = re.horse_number
JOIN sekito.racecourse rc ON rc.netkeiba_id = r.course
LEFT JOIN sekito.netkeiba nk
  ON nk.course_code=rc.code AND nk.date=to_date(r.date,'YYYYMMDD')
     AND nk.race_no=r.race_number AND nk.horse_no=re.horse_number AND nk.is_time_index = true
LEFT JOIN sekito.kichiuma kc
  ON kc.course_code=rc.code AND kc.date=to_date(r.date,'YYYYMMDD')
     AND kc.race_no=r.race_number AND kc.horse_no=re.horse_number
WHERE ci.version = 9 AND r.course != '83' AND r.head_count >= 6
  AND r.date BETWEEN %(start)s AND %(end)s
  AND COALESCE(rr.abnormality_code, 0) = 0 AND rr.finish_position IS NOT NULL
ORDER BY r.date, ci.race_id
"""


def _conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT"), dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"), password=os.getenv("DB_PASSWORD"))


def _my_fetch(conn, start, end):
    cur = conn.cursor()
    cur.execute(MY_QUERY, {"start": start, "end": end})
    cols = [d[0] for d in cur.description]
    df = pd.DataFrame(cur.fetchall(), columns=cols)
    cur.close()
    return df


def _add_ext(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["nk_idx"] = pd.to_numeric(df["nk_idx"], errors="coerce")
    df["kc_sp"] = pd.to_numeric(df["kc_sp"], errors="coerce")

    def zscore(s):
        m, sd = s.mean(), s.std()
        return (s - m) / sd if sd and sd > 0 else s * 0.0

    g = df.groupby("race_id")
    df["kc_sp_z"] = g["kc_sp"].transform(zscore).fillna(0.0)
    df["nk_idx_z"] = g["nk_idx"].transform(zscore).fillna(0.0)
    # 順位/頭数 (0=最良)。欠損は0.5(中立)
    df["kc_rank_n"] = (g["kc_sp"].rank(ascending=False, method="min") - 1) / df["head_count"].clip(lower=1)
    df["nk_rank_n"] = (g["nk_idx"].rank(ascending=False, method="min") - 1) / df["head_count"].clip(lower=1)
    df["kc_rank_n"] = df["kc_rank_n"].fillna(0.5)
    df["nk_rank_n"] = df["nk_rank_n"].fillna(0.5)
    df["ext_missing"] = (df["kc_sp"].isna() & df["nk_idx"].isna()).astype(int)
    return df


def _ece(y, p, bins=10):
    df = pd.DataFrame({"y": y, "p": p})
    df["b"] = (df["p"] * bins).clip(0, bins - 1).astype(int)
    e = 0.0
    for _, g in df.groupby("b"):
        e += abs(g["y"].mean() - g["p"].mean()) * len(g) / len(df)
    return e


def _eval(te: pd.DataFrame, win_score, top3_score) -> dict:
    d = te.copy()
    d["sw"] = win_score
    d["s3"] = top3_score
    d["fp"] = pd.to_numeric(d["finish_position"], errors="coerce")
    d["wodds"] = pd.to_numeric(d["win_odds"], errors="coerce")
    r1w = d.loc[d.groupby("race_id")["sw"].idxmax()]
    r1p = d.loc[d.groupby("race_id")["s3"].idxmax()]
    win_pct = (r1w["fp"] == 1).mean() * 100
    place_pct = (r1p["fp"] <= 3).mean() * 100
    ret = np.where(r1w["fp"] == 1, r1w["wodds"], 0.0)
    win_roi = float(np.nanmean(ret))
    ece = _ece((d["fp"] == 1).astype(int).to_numpy(), np.clip(d["sw"].to_numpy(), 0, 1))
    return dict(win_pct=win_pct, place_pct=place_pct, win_roi=win_roi, ece=ece)


def main() -> None:
    conn = _conn()
    df_hist = fetch_hist(conn)
    print("データ取得中...")
    tr_raw = _add_ext(prep(conn, _my_fetch(conn, "20240101", CUTOFF), df_hist))
    te_raw = _add_ext(prep(conn, _my_fetch(conn, TEST_START, "20260607"), df_hist))
    conn.close()
    cov_tr = 1 - tr_raw["ext_missing"].mean()
    cov_te = 1 - te_raw["ext_missing"].mean()
    print(f"train {tr_raw['race_id'].nunique()}R / test {te_raw['race_id'].nunique()}R "
          f"| 外部指数カバレッジ train={cov_tr:.1%} test={cov_te:.1%}")

    fp_tr = pd.to_numeric(tr_raw["finish_position"], errors="coerce")
    y_win = (fp_tr == 1).astype(int).values
    y_top3 = (fp_tr <= 3).astype(int).values

    feat_sets = {"baseline": FEATURES, "augmented": FEATURES + EXT_FEATURES}
    results = {k: [] for k in feat_sets}

    for seed in SEEDS:
        params = dict(PARAMS, seed=seed, bagging_seed=seed, feature_fraction_seed=seed)
        for name, feats in feat_sets.items():
            Xtr = tr_raw[feats].values.astype(float)
            Xte = te_raw[feats].values.astype(float)
            mw = lgb.train(params, lgb.Dataset(Xtr, y_win, feature_name=feats), num_boost_round=NUM_ROUNDS)
            m3 = lgb.train(params, lgb.Dataset(Xtr, y_top3, feature_name=feats), num_boost_round=NUM_ROUNDS)
            results[name].append(_eval(te_raw, mw.predict(Xte), m3.predict(Xte)))
        print(f"  seed {seed} 完了")

    print(f"\n{'='*70}\n5seed平均 (test OOS {TEST_START}〜) baseline vs augmented\n{'='*70}")
    metrics = [("top1勝率%", "win_pct", "+"), ("top1複勝率%", "place_pct", "+"),
               ("単勝ROI", "win_roi", "+"), ("ECE(is_win)", "ece", "-")]
    for label, key, good in metrics:
        b = np.array([r[key] for r in results["baseline"]])
        a = np.array([r[key] for r in results["augmented"]])
        delta = a.mean() - b.mean()
        # 改善方向: +なら大きいほど良、-なら小さいほど良
        improved = (delta > 0) if good == "+" else (delta < 0)
        per_seed = [(a[i] - b[i]) if good == "+" else (b[i] - a[i]) for i in range(len(SEEDS))]
        n_better = sum(1 for x in per_seed if x > 0)
        sig = "★std超" if abs(delta) > max(b.std(), a.std()) and improved else ("◯" if improved else "✗")
        print(f"  {label:13} base {b.mean():.3f}±{b.std():.3f} → aug {a.mean():.3f}±{a.std():.3f} "
              f"| Δ={delta:+.3f} ({n_better}/{len(SEEDS)}seed改善) {sig}")
    print("\n判定基準: 主目標=top1勝率/複勝率が 全seed改善(5/5)かつΔ>std で本採用候補。")


if __name__ == "__main__":
    main()
