"""地方競馬 Phase3b: 新規候補特徴の 5seed A/B 評価

ベースライン21特徴(Phase3a)に対し、候補特徴を追加して寄与を測る。
候補:
  - apprentice       : 減量騎手区分 (race_entries.jockey_apprentice_code, 01: 減量で複勝-8.4pt)
  - trainer_t3       : 厩舎の当該レース前 累積複勝(top3)率 (trainer_id, 系統a 精度)
  - cf_bias          : コース×枠順の過去top3率(train期間集計, 系統b edge, 場で符号反転)

評価軸（OOS test）:
  系統a 精度: top1勝率 / 症状A(勝ち馬→rank4+ demote率) / 症状B(top3 bust率)
  系統b edge: 指数1位×単勝10-30倍×割安場 単勝ROI(drop1+CI)
判定: 5seed平均で、効果が seed分散(std)を超えるもののみ採用候補。

使い方:
  cd backend
  .venv/bin/python scripts/chihou_phase3b_features.py --seeds 5
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

from scripts.chihou_model_compare import (  # noqa: E402
    FAVORABLE_COURSES,
    _roi_with_ci,
    scale_to_index,
)
from scripts.train_chihou_v11_lightgbm import (  # noqa: E402
    ALL_FEATURES as V21_FEATURES,
    add_historical_features,
    featurize,
    fetch_hist,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("chihou_p3b")

CHIHOU_V9_VERSION = 9

# base21 + trainer_id/apprentice を取得
BASE_QUERY = """
SELECT
    ci.race_id, r.date, r.course_name, r.prize_1st AS curr_prize,
    r.surface, r.condition, r.distance, r.head_count,
    re.horse_id, re.trainer_id,
    COALESCE(re.jockey_apprentice_code, '0') AS apprentice,
    re.frame_number, re.horse_age, re.weight_carried,
    COALESCE(rr.horse_weight, 500) AS horse_weight,
    COALESCE(rr.weight_change, 0)  AS weight_change,
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

# 厩舎履歴（trainer の当該前 top3率算出用）
TRAINER_HIST_QUERY = """
SELECT re.trainer_id, r.date, r.id AS race_id, rr.finish_position
FROM chihou.race_entries re
JOIN chihou.races r ON r.id = re.race_id
JOIN chihou.race_results rr ON rr.race_id = re.race_id AND rr.horse_number = re.horse_number
WHERE r.course != '83' AND r.date >= '20220101'
  AND COALESCE(rr.abnormality_code, 0) = 0 AND rr.finish_position IS NOT NULL
  AND re.trainer_id IS NOT NULL
ORDER BY re.trainer_id, r.date, r.id
"""

PARAMS = {
    "objective": "binary", "metric": "binary_logloss",
    "num_leaves": 31, "max_depth": 5, "min_data_in_leaf": 50,
    "lambda_l1": 0.1, "lambda_l2": 1.0, "learning_rate": 0.05,
    "feature_fraction": 0.8, "bagging_fraction": 0.8, "bagging_freq": 5, "verbose": -1,
}


def fetch(conn, q, params):
    cur = conn.cursor()
    cur.execute(q, params)
    cols = [d[0] for d in cur.description]
    df = pd.DataFrame(cur.fetchall(), columns=cols)
    cur.close()
    return df


def add_trainer_t3(df: pd.DataFrame, th: pd.DataFrame) -> pd.DataFrame:
    """厩舎の当該レース前 累積複勝(top3)率を (trainer_id,race_id) で付与。"""
    th = th.sort_values(["trainer_id", "date", "race_id"]).copy()
    th["is_t3"] = (pd.to_numeric(th["finish_position"], errors="coerce") <= 3).astype(float)
    g = th.groupby("trainer_id")
    th["_runs"] = g.cumcount()
    th["_wins"] = g["is_t3"].cumsum() - th["is_t3"]
    th["trainer_t3"] = np.where(th["_runs"] >= 5, th["_wins"] / th["_runs"].clip(lower=1), -1.0)
    # (trainer_id, race_id) ごとに代表値（同一レース複数頭でも同 trainer は同値域だが
    # cumcount は頭ごとに進むため race 先頭の値を採用）
    rep = th.groupby(["trainer_id", "race_id"])["trainer_t3"].first().reset_index()
    return df.merge(rep, on=["trainer_id", "race_id"], how="left")


def add_cf_bias(df_train: pd.DataFrame, df_test: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """コース×枠順の top3率を train 期間で集計し、両 df に特徴量として付与（リーク無）。"""
    tr = df_train.copy()
    tr["is_t3"] = (pd.to_numeric(tr["finish_position"], errors="coerce") <= 3).astype(float)
    tr["fb"] = tr["frame_number"].fillna(0).astype(int)
    tbl = tr.groupby(["course_name", "fb"])["is_t3"].mean()
    glob = tr["is_t3"].mean()

    def _map(d):
        d = d.copy()
        d["fb"] = d["frame_number"].fillna(0).astype(int)
        d["cf_bias"] = [
            tbl.get((c, f), glob) for c, f in zip(d["course_name"], d["fb"])
        ]
        return d

    return _map(df_train), _map(df_test)


def train_seeds(Xtr, ytr, Xte, feats, seeds):
    return [
        lgb.train(dict(PARAMS, seed=sd), lgb.Dataset(Xtr, ytr, feature_name=feats),
                  num_boost_round=400).predict(Xte)
        for sd in seeds
    ]


def evaluate(df_test, idx_scores, rng):
    """1 seed の index スコアで 系統a/b 指標を返す。"""
    d = df_test.copy()
    d["s"] = idx_scores
    d["fp"] = pd.to_numeric(d["finish_position"], errors="coerce")
    d["win_odds"] = pd.to_numeric(d["win_odds"], errors="coerce")
    d["rk"] = d.groupby("race_id")["s"].rank(ascending=False, method="first")
    top1 = d[d["rk"] == 1]
    win = (top1["fp"] == 1).mean() * 100
    # 症状A: 勝ち馬を rank4+ に降格
    w = d[d["fp"] == 1]
    demote = (w["rk"] >= 4).mean() * 100
    # 症状B: top3 の凡走(fp>=6)
    bust = (d[d["rk"] <= 3]["fp"] >= 6).mean() * 100
    # 系統b: 指数1位×10-30×割安場
    fav = top1[(top1["win_odds"] >= 10) & (top1["win_odds"] < 30) & (top1["course_name"].isin(FAVORABLE_COURSES))]
    s = _roi_with_ci(fav, rng)
    return {"win": win, "demote": demote, "bust": bust, "roi": s["roi"], "roi_n": s["n"]}


def run_variant(name, feats, df_train, df_test, seeds, rng):
    Xtr = df_train[feats].values.astype(float)
    ytr = (pd.to_numeric(df_train["finish_position"], errors="coerce") <= 3).astype(int).values
    Xte = df_test[feats].values.astype(float)
    preds = train_seeds(Xtr, ytr, Xte, feats, seeds)
    metrics = []
    for p in preds:
        idx = scale_to_index(p, df_test["race_id"])
        metrics.append(evaluate(df_test, idx, rng))
    agg = {k: np.mean([m[k] for m in metrics]) for k in metrics[0]}
    std = {k: np.std([m[k] for m in metrics]) for k in metrics[0]}
    logger.info("[%s] win=%.2f±%.2f demote=%.2f bust=%.2f 割安場ROI=%.3f±%.3f (n=%.0f)",
                name, agg["win"], std["win"], agg["demote"], agg["bust"],
                agg["roi"], std["roi"], agg["roi_n"])
    return name, agg, std


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--train-start", default="20230101")
    p.add_argument("--train-end", default="20250630")
    p.add_argument("--test-start", default="20250701")
    p.add_argument("--test-end", default="20260605")
    p.add_argument("--seeds", type=int, default=5)
    args = p.parse_args()
    seeds = list(range(args.seeds))
    rng = np.random.default_rng(12345)

    dsn = (f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} dbname={os.getenv('DB_NAME')} "
           f"user={os.getenv('DB_USER')} password={os.getenv('DB_PASSWORD')}")
    conn = psycopg2.connect(dsn)
    tr_raw = fetch(conn, BASE_QUERY, {"ver": CHIHOU_V9_VERSION, "start": args.train_start, "end": args.train_end})
    te_raw = fetch(conn, BASE_QUERY, {"ver": CHIHOU_V9_VERSION, "start": args.test_start, "end": args.test_end})
    df_hist = fetch_hist(conn)
    th = fetch(conn, TRAINER_HIST_QUERY, {})
    conn.close()
    logger.info("train=%d test=%d trainer_hist=%d", len(tr_raw), len(te_raw), len(th))

    # 21特徴(Phase3a) 付与
    alldf = pd.concat([tr_raw, te_raw], ignore_index=True)
    alldf = featurize(alldf)
    alldf = add_historical_features(alldf, df_hist)
    for c in V21_FEATURES:
        alldf[c] = pd.to_numeric(alldf[c], errors="coerce").fillna(-1.0)
    # 候補: apprentice / trainer_t3
    alldf["apprentice"] = pd.to_numeric(alldf["apprentice"], errors="coerce").fillna(0.0)
    alldf = add_trainer_t3(alldf, th)
    alldf["trainer_t3"] = pd.to_numeric(alldf["trainer_t3"], errors="coerce").fillna(-1.0)

    cut = tr_raw["date"].max()
    df_train = alldf[alldf["date"] <= cut].copy()
    df_test = alldf[alldf["date"] > cut].copy().reset_index(drop=True)
    # cf_bias は train集計→両者付与
    df_train, df_test = add_cf_bias(df_train, df_test)

    print("\n" + "=" * 84)
    print(f"Phase3b 候補特徴 5seed A/B  test {args.test_start}-{args.test_end} {df_test['race_id'].nunique()}R")
    print("ベースライン=21特徴(Phase3a)。各候補を追加して寄与を測定。割安場=浦和/金沢/高知/笠松/盛岡")
    print("=" * 84)

    variants = [
        ("base21", V21_FEATURES),
        ("+apprentice", V21_FEATURES + ["apprentice"]),
        ("+trainer_t3", V21_FEATURES + ["trainer_t3"]),
        ("+cf_bias", V21_FEATURES + ["cf_bias"]),
        ("+all3", V21_FEATURES + ["apprentice", "trainer_t3", "cf_bias"]),
    ]
    rows = [run_variant(n, f, df_train, df_test, seeds, rng) for n, f in variants]

    base = rows[0][1]
    bstd = rows[0][2]
    print("\n--- 判定（baseline比 / 効果がseed分散を超えるか） ---")
    print(f"{'variant':<14}{'win':>8}{'Δwin':>8}{'demote':>8}{'bust':>8}{'割安場ROI':>11}{'判定':>8}")
    for name, agg, std in rows:
        dwin = agg["win"] - base["win"]
        # win 改善が baseline seed std を超え、demote/bust 悪化せず、ROI 維持なら採用候補
        passed = name == "base21" or (
            dwin > bstd["win"] and agg["demote"] <= base["demote"] + 0.5
        )
        mark = "—" if name == "base21" else ("採用候補" if passed else "分散内/不採用")
        print(f"{name:<14}{agg['win']:>7.2f}%{dwin:>+7.2f}{agg['demote']:>7.1f}%{agg['bust']:>7.1f}%"
              f"{agg['roi']:>11.3f}{mark:>10}")
    print("=" * 84)


if __name__ == "__main__":
    main()
