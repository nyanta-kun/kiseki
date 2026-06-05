"""JRA win_probability 較正 A/B 検証（is_win 較正ヘッド導入の事前検証）

jra_verify_signals.py で本番 win_probability(softmax) が未較正(ECE 0.033・最上位
decile +16pt 過信)と判明。地方 Phase2 (scripts/chihou_phase2_calibration.py) と同様に
「binary is_win の LGB 生出力が OOS でそのまま較正されているか」を確認してから
本番組込を判断する。

比較する確率（OOS test 期間、複数 seed 平均）:
  A) softmax(composite)  : 本番現行 ci.win_probability(version=26)
  B) is_win LGB 生出力    : v26 特徴量で is_win を binary 学習した生 predict
  C) B をレース内正規化   : Σ=1 に正規化（順位は変えず確率の絶対値を補正）
  D) B に isotonic 後段補正: train で isotonic fit → test 適用（地方では負の結果）

評価:
  - 信頼性曲線(decile): 予測 vs 実測 + ECE / MCE / Brier
  - sweet_spot EVゲート再評価: 較正確率で EV帯別ROIが単調化するか

使い方:
  cd backend
  .venv/bin/python scripts/jra_calibration_ab.py --seeds 5
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

import lightgbm as lgb  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import psycopg2  # noqa: E402
from sklearn.isotonic import IsotonicRegression  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("jra_calib")

V26_VERSION = 26

SUBINDEX_FEATURES = [
    "speed_index", "last_3f_index", "course_aptitude", "position_advantage",
    "rotation_index", "jockey_index", "pace_index", "pedigree_index",
    "training_index", "anagusa_index", "paddock_index", "rebound_index",
    "rivals_growth_index", "career_phase_index", "distance_change_index",
    "jockey_trainer_combo_index", "going_pedigree_index",
]
RACE_FEATURES = ["distance", "head_count", "is_turf", "is_dirt", "is_jump",
                 "is_good", "is_yaya", "is_heavy", "is_bad", "is_g1g2g3"]
HORSE_FEATURES = ["frame_number", "horse_age", "weight_carried", "horse_weight",
                  "weight_change", "jvan_time_dm", "jvan_battle_dm"]
ALL_FEATURES = SUBINDEX_FEATURES + RACE_FEATURES + HORSE_FEATURES

QUERY = """
SELECT
    ci.race_id, ci.horse_id,
    ci.speed_index, ci.last_3f_index, ci.course_aptitude, ci.position_advantage,
    ci.rotation_index, ci.jockey_index, ci.pace_index, ci.pedigree_index,
    ci.training_index, ci.anagusa_index, ci.paddock_index, ci.rebound_index,
    ci.rivals_growth_index, ci.career_phase_index, ci.distance_change_index,
    ci.jockey_trainer_combo_index, ci.going_pedigree_index,
    ci.win_probability AS softmax_win,
    r.date, r.distance, r.head_count, r.surface, r.condition, r.grade,
    re.frame_number, re.horse_age, re.weight_carried, re.horse_weight,
    rr.weight_change, re.jvan_time_dm, re.jvan_battle_dm,
    rr.finish_position, rr.win_odds
FROM keiba.calculated_indices ci
JOIN keiba.race_results rr ON rr.race_id = ci.race_id AND rr.horse_id = ci.horse_id
JOIN keiba.race_entries re ON re.race_id = ci.race_id AND re.horse_id = ci.horse_id
JOIN keiba.races r ON r.id = ci.race_id
WHERE ci.version = %(ver)s
  AND r.head_count >= 8
  AND r.date BETWEEN %(start)s AND %(end)s
  AND COALESCE(rr.abnormality_code, 0) = 0
  AND rr.finish_position IS NOT NULL
  AND rr.win_odds IS NOT NULL AND rr.win_odds > 0
  AND r.course IN ('01','02','03','04','05','06','07','08','09','10')
"""


def fetch(conn, start, end):
    cur = conn.cursor()
    cur.execute(QUERY, {"ver": V26_VERSION, "start": start, "end": end})
    cols = [d[0] for d in cur.description]
    df = pd.DataFrame(cur.fetchall(), columns=cols)
    cur.close()
    df["date"] = df["date"].astype(str)
    return df


def featurize(df):
    df = df.copy()
    s = df["surface"].fillna("").astype(str)
    df["is_turf"] = s.str.startswith("芝").astype(int)
    df["is_dirt"] = s.str.startswith("ダ").astype(int)
    df["is_jump"] = s.str.startswith("障").astype(int)
    c = df["condition"].fillna("").astype(str)
    df["is_good"] = (c == "良").astype(int)
    df["is_yaya"] = (c == "稍").astype(int)
    df["is_heavy"] = (c == "重").astype(int)
    df["is_bad"] = (c == "不").astype(int)
    g = df["grade"].fillna("").astype(str)
    df["is_g1g2g3"] = g.str.match(r"^G[1-3]$").astype(int)
    for col in SUBINDEX_FEATURES + HORSE_FEATURES + ["distance", "head_count",
                                                     "softmax_win", "finish_position", "win_odds"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def calib_metrics(prob: np.ndarray, y: np.ndarray, n_bins: int = 10) -> dict:
    """ECE / MCE / Brier + decile信頼性テーブル。"""
    df = pd.DataFrame({"p": prob, "y": y}).dropna()
    df = df.sort_values("p").reset_index(drop=True)
    df["bin"] = (np.arange(len(df)) * n_bins // len(df)).clip(0, n_bins - 1)
    tot = len(df)
    ece = mce = 0.0
    table = []
    for b, g in df.groupby("bin"):
        pred = g["p"].mean()
        act = g["y"].mean()
        gap = abs(pred - act)
        ece += gap * len(g) / tot
        mce = max(mce, gap)
        table.append((int(b) + 1, len(g), pred * 100, act * 100, (act - pred) * 100))
    brier = float(np.mean((df["p"] - df["y"]) ** 2))
    return {"ece": ece, "mce": mce, "brier": brier, "table": table}


def print_calib(name: str, m: dict) -> None:
    print(f"\n[{name}]  ECE={m['ece']:.4f}  MCE={m['mce']:.4f}  Brier={m['brier']:.4f}")
    print(f"  {'decile':<8}{'n':>7}{'予測%':>9}{'実測%':>9}{'乖離':>9}")
    for b, n, pred, act, gap in m["table"]:
        print(f"  {b:<8}{n:>7}{pred:>8.2f}%{act:>8.2f}%{gap:>+8.2f}%")


def train_iswin(df_tr, df_te, seeds):
    Xtr = df_tr[ALL_FEATURES].values.astype(float)
    ytr = (df_tr["finish_position"] == 1).astype(int).values
    Xte = df_te[ALL_FEATURES].values.astype(float)
    params = dict(objective="binary", metric="binary_logloss", num_leaves=31,
                  max_depth=6, min_data_in_leaf=100, lambda_l1=0.1, lambda_l2=0.1,
                  learning_rate=0.05, feature_fraction=0.7, bagging_fraction=0.7,
                  bagging_freq=5, verbose=-1)
    preds = []
    for sd in seeds:
        m = lgb.train(dict(params, seed=sd), lgb.Dataset(Xtr, ytr, feature_name=ALL_FEATURES),
                      num_boost_round=500)
        preds.append(m.predict(Xte))
        logger.info("  is_win seed=%d 学習完了", sd)
    return np.mean(preds, axis=0)


def race_normalize(prob, race_ids):
    df = pd.DataFrame({"r": np.asarray(race_ids), "p": prob})
    s = df.groupby("r")["p"].transform("sum")
    return (df["p"] / s.replace(0, np.nan)).fillna(0).values


def ev_monotonicity(df, prob_col, rng, label):
    print(f"\n--- EV帯別 単ROI (odds≥10馬, 確率={label}) sweet_spotゲート[1.2,5.0]単調性 ---")
    d = df[(df["win_odds"] >= 10) & df[prob_col].notna()].copy()
    d["ev"] = d[prob_col] * d["win_odds"]
    d["bin"] = pd.cut(d["ev"], [0, 1.2, 1.5, 2.0, 3.0, 5.0, 1e9],
                      labels=["<1.2", "1.2-1.5", "1.5-2.0", "2.0-3.0", "3.0-5.0", "≥5.0"])
    print(f"  {'EV帯':<10}{'n':>6}{'勝率':>7}{'単ROI':>8}")
    for b, g in d.groupby("bin", observed=True):
        if len(g) == 0:
            continue
        win = g["finish_position"] == 1
        roi = (win * g["win_odds"]).sum() / len(g)
        print(f"  {str(b):<10}{len(g):>6}{win.mean()*100:>6.1f}%{roi:>8.3f}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="20230501")
    p.add_argument("--train-end", default="20250630")
    p.add_argument("--test-start", default="20250701")
    p.add_argument("--end", default="20260605")
    p.add_argument("--seeds", type=int, default=5)
    args = p.parse_args()

    seeds = list(range(args.seeds))
    rng = np.random.default_rng(12345)
    dsn = (f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
           f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} password={os.getenv('DB_PASSWORD')}")
    conn = psycopg2.connect(dsn)
    df = featurize(fetch(conn, args.start, args.end))
    conn.close()
    df = df[df["finish_position"].notna()].reset_index(drop=True)

    tr = df[df["date"] <= args.train_end].copy()
    te = df[df["date"] >= args.test_start].copy().reset_index(drop=True)
    logger.info("train=%d test=%d (test %dレース)", len(tr), len(te), te["race_id"].nunique())

    y_te = (te["finish_position"] == 1).astype(int).values

    # B) is_win LGB 生出力
    raw = train_iswin(tr, te, seeds)
    # C) レース内正規化
    norm = race_normalize(raw, te["race_id"])
    # D) isotonic（train で fit）
    raw_tr = train_iswin(tr, tr, [0])  # train 上の生出力で isotonic fit（簡易・seed0）
    iso = IsotonicRegression(out_of_bounds="clip")
    iso.fit(raw_tr, (tr["finish_position"] == 1).astype(int).values)
    iso_te = iso.predict(raw)

    print("\n" + "=" * 78)
    print(f"JRA win_probability 較正 A/B  test {args.test_start}-{args.end}  "
          f"{te['race_id'].nunique()}R / {len(te)}馬 / is_win {len(seeds)}seed平均")
    print("=" * 78)
    print_calib("A) softmax(composite) 本番現行", calib_metrics(te["softmax_win"].values, y_te))
    print_calib("B) is_win LGB 生出力", calib_metrics(raw, y_te))
    print_calib("C) B レース内正規化(Σ=1)", calib_metrics(norm, y_te))
    print_calib("D) B isotonic後段補正", calib_metrics(iso_te, y_te))

    te["raw_iswin"] = raw
    ev_monotonicity(te, "softmax_win", rng, "A softmax(現行)")
    ev_monotonicity(te, "raw_iswin", rng, "B is_win較正")
    print("=" * 78)


if __name__ == "__main__":
    main()
