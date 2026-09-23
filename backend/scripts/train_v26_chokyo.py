"""v26 + 調教(坂路 SLOP)特徴量の再学習 A/B 実験。

train_v26_lightgbm.py の特徴量・データ・分割を流用し、坂路調教データ
(keiba.slope_training) から導出した特徴量を追加した場合としない場合で
test メトリクスを比較する。

評価軸（ユーザー要望）: ROIでなく **的中精度** = 指数上位5位以内に
3着内馬（馬券圏内）が入る捕捉率。evaluate2() で以下を測定:
  - place_in_top5: 各レースで予測top5に入った実3着内馬の平均頭数 (0-3)
  - place_in_top3: 同 予測top3 (0-3)
  - top1_place_pct / top1_win_roi (従来指標も併記)

坂路特徴量（馬の直近35日の坂路追いから・レース日より前のみ=リークなし）:
  最速4F追いを「本追い」とみなし、トレセン別z-score化:
  - chokyo_4f_z      : 最速4F合計タイムの同トレセンz (負=速い=シャープ)
  - chokyo_last1f_z  : その追いの終い1F(200-0)の同トレセンz (負=終い速い)
  - chokyo_accel     : lap_400_200 - lap_200_0 (正=終い加速=伸びる脚)
  - chokyo_days_since: 直近追いからレースまでの日数 (詰め/間隔)
  - chokyo_count_35d : 直近35日の坂路追い本数 (調教量)

使い方:
  PYTHONPATH=. .venv/bin/python scripts/train_v26_chokyo.py --seeds 5
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime
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

from scripts.train_v26_lightgbm import (  # noqa: E402
    ALL_FEATURES,
    fetch_dataset,
    featurize,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("v26_chokyo")

WINDOW_DAYS = 35
HISTORY_FLOOR = "20230101"

CHOKYO_FEATURES = [
    # 絶対系（同トレセン比）
    "chokyo_4f_z", "chokyo_last1f_z", "chokyo_accel",
    "chokyo_days_since", "chokyo_count_35d",
    # 個体相対系（馬自身の過去norm比 = 上昇気配）+ 最終追い特定
    "chokyo_self_4f_dev", "chokyo_self_1f_dev", "chokyo_final_last1f_z",
]
BASELINE_DAYS = 180  # 自己ベースライン参照窓（prep窓より前 35〜180日）
MIN_BASELINE = 3     # 自己ベースライン算出の最低本数

SLOPE_HISTORY_QUERY = """
SELECT h.id AS horse_id, st.training_date, st.center,
       st.time_4f, st.lap_400_200, st.lap_200_0
FROM keiba.slope_training st
JOIN keiba.horses h ON h.jravan_code = st.blood_reg_no
WHERE st.training_date BETWEEN %(floor)s AND %(end)s
  AND st.time_4f IS NOT NULL
ORDER BY h.id, st.training_date
"""


def _to_date(yyyymmdd: str):
    try:
        return datetime.strptime(str(yyyymmdd), "%Y%m%d").date()
    except (ValueError, TypeError):
        return None


def load_slope(conn, end: str, floor: str = HISTORY_FLOOR):
    """坂路履歴を取得し、トレセン別z統計と horse_id→works のマップを返す。"""
    cur = conn.cursor()
    cur.execute(SLOPE_HISTORY_QUERY, {"floor": floor, "end": end})
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    cur.close()
    df = pd.DataFrame(rows, columns=cols)
    logger.info(f"坂路履歴取得: {len(df):,}行 ({HISTORY_FLOOR}〜{end})")
    if df.empty:
        return {}, {}

    for c in ("time_4f", "lap_400_200", "lap_200_0"):
        df[c] = pd.to_numeric(df[c], errors="coerce")

    # トレセン別 z 統計（time_4f / lap_200_0）
    cstats: dict[str, dict[str, float]] = {}
    for center, grp in df.groupby("center"):
        cstats[center] = {
            "m4f": float(grp["time_4f"].mean()),
            "s4f": float(grp["time_4f"].std()) or 1.0,
            "m1f": float(grp["lap_200_0"].mean()),
            "s1f": float(grp["lap_200_0"].std()) or 1.0,
        }

    works_by_horse: dict[int, list[dict]] = {}
    for hid, grp in df.groupby("horse_id", sort=False):
        recs = []
        for r in grp.to_dict("records"):
            d = _to_date(r["training_date"])
            if d is None:
                continue
            recs.append({
                "date": d, "center": r["center"],
                "time_4f": r["time_4f"], "lap_400_200": r["lap_400_200"],
                "lap_200_0": r["lap_200_0"],
            })
        recs.sort(key=lambda x: x["date"])
        works_by_horse[int(hid)] = recs
    logger.info(f"坂路: {len(works_by_horse):,}頭分の追い切り履歴を構築")
    return cstats, works_by_horse


def _valid(v) -> bool:
    return v is not None and not pd.isna(v)


def _features_for(race_date, horse_id, works_by_horse, cstats) -> dict[str, float]:
    nan = {f: np.nan for f in CHOKYO_FEATURES}
    works = works_by_horse.get(int(horse_id))
    if not works:
        return nan
    rd = _to_date(race_date)
    if rd is None:
        return nan
    cand = [w for w in works if 0 < (rd - w["date"]).days <= WINDOW_DAYS]
    if not cand:
        return nan
    best = min(cand, key=lambda w: w["time_4f"])      # 本追い=最速4F
    final = max(cand, key=lambda w: w["date"])         # 最終追い=直近
    cs = cstats.get(best["center"])

    def _z(v, mkey, skey):
        if cs is None or not _valid(v):
            return np.nan
        return (v - cs[mkey]) / cs[skey]

    f4z = _z(best["time_4f"], "m4f", "s4f")
    f1z = _z(best["lap_200_0"], "m1f", "s1f")
    final_1fz = _z(final["lap_200_0"], "m1f", "s1f")
    accel = (
        best["lap_400_200"] - best["lap_200_0"]
        if _valid(best["lap_400_200"]) and _valid(best["lap_200_0"]) else np.nan
    )

    # 個体相対: prep窓より前(35〜180日)の自己norm比。負=今回の追いが自分の平常より速い=上昇
    base = [w for w in works if WINDOW_DAYS < (rd - w["date"]).days <= BASELINE_DAYS]
    b4 = [w["time_4f"] for w in base if _valid(w["time_4f"])]
    b1 = [w["lap_200_0"] for w in base if _valid(w["lap_200_0"])]
    self_4f = (best["time_4f"] - float(np.median(b4))) if len(b4) >= MIN_BASELINE and _valid(best["time_4f"]) else np.nan
    self_1f = (final["lap_200_0"] - float(np.median(b1))) if len(b1) >= MIN_BASELINE and _valid(final["lap_200_0"]) else np.nan

    return {
        "chokyo_4f_z": f4z,
        "chokyo_last1f_z": f1z,
        "chokyo_accel": accel,
        "chokyo_days_since": float((rd - final["date"]).days),
        "chokyo_count_35d": float(len(cand)),
        "chokyo_self_4f_dev": self_4f,
        "chokyo_self_1f_dev": self_1f,
        "chokyo_final_last1f_z": final_1fz,
    }


def attach_chokyo(df: pd.DataFrame, works_by_horse, cstats) -> pd.DataFrame:
    df = df.copy()
    feats = [
        _features_for(rd, h, works_by_horse, cstats)
        for rd, h in zip(df["race_date"], df["horse_id"])
    ]
    fdf = pd.DataFrame(feats, index=df.index)
    for c in CHOKYO_FEATURES:
        df[c] = fdf[c]
    return df


def evaluate2(df_test: pd.DataFrame, scores: np.ndarray, label: str) -> dict:
    """top1 指標に加え、上位5位/3位への3着内馬 捕捉頭数を測定する。"""
    df = df_test.copy()
    df["score"] = scores
    df["finish_position"] = pd.to_numeric(df["finish_position"], errors="coerce")
    df["win_odds"] = pd.to_numeric(df["win_odds"], errors="coerce")

    top1 = df.loc[df.groupby("race_id")["score"].idxmax()]
    win_pct = (top1["finish_position"] == 1).mean() * 100
    place_pct = (top1["finish_position"] <= 3).mean() * 100
    win_roi = ((top1["finish_position"] == 1) * top1["win_odds"]).fillna(0).sum() / len(top1)

    cap5, cap3 = [], []
    for _rid, grp in df.groupby("race_id"):
        g = grp.sort_values("score", ascending=False)
        placers = set(g.index[g["finish_position"] <= 3])
        if not placers:
            continue
        top5_idx = set(g.head(5).index)
        top3_idx = set(g.head(3).index)
        cap5.append(len(placers & top5_idx))
        cap3.append(len(placers & top3_idx))

    metrics = {
        "label": label,
        "n_races": len(top1),
        "top1_win_pct": round(win_pct, 2),
        "top1_place_pct": round(place_pct, 2),
        "top1_win_roi": round(win_roi, 3),
        "place_in_top5": round(float(np.mean(cap5)), 4),  # 0-3
        "place_in_top3": round(float(np.mean(cap3)), 4),  # 0-3
    }
    logger.info(f"[{label}] {metrics}")
    return metrics


def _train_eval(df_train, df_valid, df_test, features, args, tag, seed):
    Xtr, Xva, Xte = df_train[features].values, df_valid[features].values, df_test[features].values
    ytr, yva = df_train["y"].values, df_valid["y"].values
    params = {
        "objective": "binary", "metric": "binary_logloss",
        "num_leaves": args.num_leaves, "max_depth": args.max_depth,
        "min_data_in_leaf": args.min_data_in_leaf,
        "lambda_l1": args.lambda_l1, "lambda_l2": args.lambda_l2,
        "learning_rate": args.learning_rate,
        "feature_fraction": args.feature_fraction,
        "bagging_fraction": args.bagging_fraction, "bagging_freq": 5,
        "seed": seed, "verbose": -1,
    }
    tr = lgb.Dataset(Xtr, ytr, feature_name=features)
    va = lgb.Dataset(Xva, yva, feature_name=features, reference=tr)
    model = lgb.train(
        params, tr, num_boost_round=args.num_iterations, valid_sets=[va],
        callbacks=[lgb.early_stopping(30), lgb.log_evaluation(0)],
    )
    s_test = model.predict(Xte, num_iteration=model.best_iteration)
    m = evaluate2(df_test, s_test, f"test[{tag}#{seed}]")
    if set(CHOKYO_FEATURES) <= set(features):
        imp = dict(zip(features, model.feature_importance(importance_type="gain")))
        for cf in CHOKYO_FEATURES:
            rank = sorted(features, key=lambda f: -imp[f]).index(cf) + 1
            logger.info(f"  {cf} gain={int(imp[cf])} ({rank}/{len(features)}位)")
    return m


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--num-leaves", type=int, default=31)
    p.add_argument("--max-depth", type=int, default=6)
    p.add_argument("--min-data-in-leaf", type=int, default=100)
    p.add_argument("--lambda-l1", type=float, default=0.1)
    p.add_argument("--lambda-l2", type=float, default=0.1)
    p.add_argument("--feature-fraction", type=float, default=0.7)
    p.add_argument("--bagging-fraction", type=float, default=0.7)
    p.add_argument("--learning-rate", type=float, default=0.05)
    p.add_argument("--num-iterations", type=int, default=500)
    p.add_argument("--seeds", type=int, default=5)
    # データ窓（既定=v26標準。option=1の1年データで早期A/Bする場合は recent 窓を指定）
    p.add_argument("--train-start", default="20230501")
    p.add_argument("--train-end", default="20250630")
    p.add_argument("--valid-start", default="20250701")
    p.add_argument("--valid-end", default="20251231")
    p.add_argument("--test-start", default="20260101")
    p.add_argument("--test-end", default="20260430")
    p.add_argument("--slope-floor", default=HISTORY_FLOOR,
                   help="坂路履歴取得の下限日(既定20230101)。option=1の1年データなら20250527等")
    args = p.parse_args()

    dsn = (
        f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
        f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
        f"password={os.getenv('DB_PASSWORD')}"
    )
    conn = psycopg2.connect(dsn)

    cstats, works = load_slope(conn, args.test_end, args.slope_floor)

    df_train = attach_chokyo(featurize(fetch_dataset(conn, args.train_start, args.train_end)), works, cstats)
    df_valid = attach_chokyo(featurize(fetch_dataset(conn, args.valid_start, args.valid_end)), works, cstats)
    df_test = attach_chokyo(featurize(fetch_dataset(conn, args.test_start, args.test_end)), works, cstats)
    conn.close()

    for d in (df_train, df_valid, df_test):
        d["y"] = (pd.to_numeric(d["finish_position"], errors="coerce") <= 3).astype(int)

    cov = df_test["chokyo_4f_z"].notna().mean() * 100
    logger.info(f"test 坂路特徴カバレッジ: {cov:.1f}%")

    keys = ["top1_win_pct", "top1_place_pct", "top1_win_roi", "place_in_top5", "place_in_top3"]
    base_runs = {k: [] for k in keys}
    ck_runs = {k: [] for k in keys}
    for seed in range(args.seeds):
        logger.info(f"=== seed {seed}: baseline ===")
        b = _train_eval(df_train, df_valid, df_test, ALL_FEATURES, args, "base", seed)
        logger.info(f"=== seed {seed}: +chokyo ===")
        w = _train_eval(df_train, df_valid, df_test, ALL_FEATURES + CHOKYO_FEATURES, args, "chokyo", seed)
        for k in keys:
            base_runs[k].append(float(b[k]))
            ck_runs[k].append(float(w[k]))

    print("\n" + "=" * 80)
    print(f"v26 test メトリクス比較（binary・{args.seeds} seed 平均±std）/ 坂路カバレッジ {cov:.1f}%")
    print("-" * 80)
    print(f"{'指標':<22}{'baseline':>20}{'+chokyo':>20}{'平均Δ':>16}")
    print("-" * 80)
    labels = {
        "top1_win_pct": "単勝的中率%", "top1_place_pct": "複勝的中率%",
        "top1_win_roi": "単勝ROI",
        "place_in_top5": "★top5内3着内頭数", "place_in_top3": "top3内3着内頭数",
    }
    for key in keys:
        ba, ck = np.array(base_runs[key]), np.array(ck_runs[key])
        d = ck - ba
        print(
            f"{labels[key]:<22}{ba.mean():>10.4f}±{ba.std():<8.4f}"
            f"{ck.mean():>10.4f}±{ck.std():<8.4f}{d.mean():>+10.4f} (全seed正:{int((d > 0).all())})"
        )
    print("=" * 80)
    print("判定: ★top5内3着内頭数 が全seedで↑(seed間stdを超える) なら採用価値あり")


if __name__ == "__main__":
    main()
