"""JRA 着外率（6着以下確率）ヘッドの本番モデル学習。

検証結果（memory: jra_out_rate_3head_verification_2026_08_02）:
  - 着外率は ROI を作らない（全帯 0.54〜0.84）が、**足切り判定としては極めて有効**。
  - p_out >= 0.80 で 除外30% / 1着取りこぼし5.0% / 除外馬の実着外率 88.7%。
    独立2年（2025 / 2026）で除外率・取りこぼし率がほぼ同一＝較正が安定。
  - 従来の Web 足切り（指数差 20 以上 等）は 除外55% で 1着を 16.8% 取りこぼしており、
    本モデルはこれを全面的に置き換える。

特徴量は v26 と同一の 34 列（`composite.py::_V26_FEATURE_NAMES` と同順）。
オッズ・人気は使わない（発走前に確定している情報のみ）。

サブ指数の取得元は `composite.SUBINDEX_SOURCE_SQL`（`version >= SUBINDEX_MIN_VERSION`
のうち各馬の最大版）。**特定の版に固定してはいけない** — 本番が版を上げた瞬間に
学習データが静かに凍結する（docs/jra_rebuild_2026_08.md 4.7）。

出力:
  models/jra_out_rate_lgb.txt        - 本番モデル（全期間 refit）
  models/jra_out_rate_metrics.json   - honest test メトリクス + 閾値別の足切り性能

使い方:
    cd backend
    .venv/bin/python scripts/train_jra_out_rate.py
"""

from __future__ import annotations

import argparse
import json
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

from src.indices.composite import OUT_PROB_FEATURE_NAMES, SUBINDEX_SOURCE_SQL  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("train_jra_out_rate")

MODELS_DIR = _root / "models"
MODELS_DIR.mkdir(exist_ok=True)
MODEL_PATH = MODELS_DIR / "jra_out_rate_lgb.txt"
METRICS_PATH = MODELS_DIR / "jra_out_rate_metrics.json"

FETCH_SQL = f"""
WITH ci AS ({SUBINDEX_SOURCE_SQL})
SELECT
    r.date, ci.race_id, ci.horse_id,
    ci.speed_index, ci.last_3f_index, ci.course_aptitude, ci.position_advantage,
    ci.rotation_index, ci.jockey_index, ci.pace_index, ci.pedigree_index,
    ci.training_index, ci.anagusa_index, ci.paddock_index, ci.rebound_index,
    ci.rivals_growth_index, ci.career_phase_index, ci.distance_change_index,
    ci.jockey_trainer_combo_index, ci.going_pedigree_index,
    r.distance, r.head_count, r.surface, r.condition, r.grade,
    re.frame_number, re.horse_age, re.weight_carried, re.horse_weight,
    re.jvan_time_dm, re.jvan_battle_dm,
    rr.weight_change, rr.abnormality_code, rr.finish_position
FROM ci
JOIN keiba.races r         ON r.id = ci.race_id
JOIN keiba.race_entries re ON re.race_id = ci.race_id AND re.horse_id = ci.horse_id
LEFT JOIN keiba.race_results rr ON rr.race_id = ci.race_id AND rr.horse_id = ci.horse_id
WHERE r.date >= %(start)s AND r.date <= %(end)s
  AND r.course IN ('01','02','03','04','05','06','07','08','09','10')
"""


def load_df(start: str, end: str) -> pd.DataFrame:
    dsn = (
        f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
        f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
        f"password={os.getenv('DB_PASSWORD')}"
    )
    conn = psycopg2.connect(dsn)
    cur = conn.cursor()
    cur.execute(FETCH_SQL, {"start": start, "end": end})
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return pd.DataFrame(rows, columns=cols)


def featurize(df: pd.DataFrame) -> pd.DataFrame:
    """`composite.py::_build_v26_features` と同一の変換を行う（train/serve skew 防止）。"""
    surface = df["surface"].fillna("").astype(str)
    cond = df["condition"].fillna("").astype(str)
    grade = df["grade"].fillna("").astype(str)
    df["is_turf"] = surface.str.startswith("芝").astype(int)
    df["is_dirt"] = surface.str.startswith("ダ").astype(int)
    df["is_jump"] = surface.str.startswith("障").astype(int)
    df["is_good"] = (cond == "良").astype(int)
    df["is_yaya"] = (cond == "稍").astype(int)
    df["is_heavy"] = (cond == "重").astype(int)
    df["is_bad"] = (cond == "不").astype(int)
    df["is_g1g2g3"] = grade.isin(["G1", "G2", "G3"]).astype(int)
    for c in OUT_PROB_FEATURE_NAMES + ["finish_position", "abnormality_code"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    # 推論側は sub-indices 欠損を 50.0、jvan 欠損を 50.0 で埋めるため学習側も揃える
    subidx = OUT_PROB_FEATURE_NAMES[:17]
    df[subidx] = df[subidx].fillna(50.0)
    df["jvan_time_dm"] = df["jvan_time_dm"].fillna(50.0)
    df["jvan_battle_dm"] = df["jvan_battle_dm"].fillna(50.0)
    return df


def _params(seed: int) -> dict:
    return dict(
        objective="binary", metric="binary_logloss",
        learning_rate=0.05, num_leaves=63, min_data_in_leaf=100,
        feature_fraction=0.8, bagging_fraction=0.8, bagging_freq=1,
        lambda_l2=1.0, verbose=-1, seed=seed, deterministic=True,
        num_threads=os.cpu_count() or 4,
    )


def cutoff_stats(y_out: np.ndarray, y_fin: np.ndarray, p: np.ndarray) -> list[dict]:
    """閾値別の足切り性能（除外率・取りこぼし率・除外馬の実着外率）。"""
    n = len(p)
    n3 = int((y_fin <= 3).sum())
    n1 = int((y_fin == 1).sum())
    rows = []
    for th in [0.65, 0.70, 0.75, 0.78, 0.80, 0.82, 0.85, 0.90]:
        m = p >= th
        ex = int(m.sum())
        if ex == 0:
            continue
        rows.append({
            "threshold": th,
            "excluded_pct": round(ex / n, 4),
            "excluded_actual_out_rate": round(float(y_out[m].mean()), 4),
            "missed_top3_pct": round(float((y_fin[m] <= 3).sum() / n3), 4) if n3 else None,
            "missed_win_pct": round(float((y_fin[m] == 1).sum() / n1), 4) if n1 else None,
        })
    return rows


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="20230506")
    p.add_argument("--end", default="20991231")
    p.add_argument("--train-end", default="20250630")
    p.add_argument("--valid-end", default="20251231")
    p.add_argument("--seeds", default="42,123,456")
    args = p.parse_args()
    seeds = [int(s) for s in args.seeds.split(",")]

    df = featurize(load_df(args.start, args.end))
    ab = df["abnormality_code"].fillna(0)
    df = df[~ab.isin([1, 2])]
    df = df[df["finish_position"].notna() & (df["finish_position"] > 0)].reset_index(drop=True)
    df["y_out"] = (df["finish_position"] >= 6).astype(int)
    logger.info(f"学習データ: {len(df):,}行 / {df['race_id'].nunique():,}レース "
                f"({df['date'].min()}〜{df['date'].max()}) 着外率={df['y_out'].mean():.3f}")

    tr = df[df["date"] <= args.train_end]
    va = df[(df["date"] > args.train_end) & (df["date"] <= args.valid_end)]
    te = df[df["date"] > args.valid_end]
    logger.info(f"train={len(tr):,} valid={len(va):,} test={len(te):,}")

    Xtr, Xva = tr[OUT_PROB_FEATURE_NAMES].values, va[OUT_PROB_FEATURE_NAMES].values
    best_iters, te_preds = [], []
    for seed in seeds:
        dtr = lgb.Dataset(Xtr, label=tr["y_out"].values, feature_name=OUT_PROB_FEATURE_NAMES)
        dva = lgb.Dataset(Xva, label=va["y_out"].values, reference=dtr)
        m = lgb.train(_params(seed), dtr, num_boost_round=2000, valid_sets=[dva],
                      callbacks=[lgb.early_stopping(100, verbose=False)])
        best_iters.append(m.best_iteration)
        if len(te):
            te_preds.append(m.predict(te[OUT_PROB_FEATURE_NAMES].values,
                                      num_iteration=m.best_iteration))
    n_rounds = int(np.median(best_iters))
    logger.info(f"best_iter={best_iters} → refit rounds={n_rounds}")

    metrics: dict = {
        "train_period": [df["date"].min(), args.train_end],
        "valid_period": [args.train_end, args.valid_end],
        "seeds": seeds, "best_iters": best_iters, "refit_rounds": n_rounds,
        "base_out_rate": round(float(df["y_out"].mean()), 4),
        "features": OUT_PROB_FEATURE_NAMES,
    }
    if len(te):
        pte = np.mean(te_preds, axis=0)
        ll = float(-np.mean(te["y_out"] * np.log(np.clip(pte, 1e-9, 1))
                            + (1 - te["y_out"]) * np.log(np.clip(1 - pte, 1e-9, 1))))
        metrics["test"] = {
            "period": [te["date"].min(), te["date"].max()],
            "n": len(te), "n_races": int(te["race_id"].nunique()),
            "logloss": round(ll, 5),
            "cutoff_stats": cutoff_stats(te["y_out"].values, te["finish_position"].values, pte),
        }
        logger.info(f"honest test logloss={ll:.5f}")
        for r in metrics["test"]["cutoff_stats"]:
            logger.info(f"  th={r['threshold']:.2f} 除外{r['excluded_pct']:.1%} "
                        f"実着外率{r['excluded_actual_out_rate']:.3f} "
                        f"取りこぼし 3着内{r['missed_top3_pct']:.1%} 1着{r['missed_win_pct']:.1%}")

    # 本番モデル: 全期間 refit（seed 平均は取れないため先頭 seed で固定ラウンド学習）
    dall = lgb.Dataset(df[OUT_PROB_FEATURE_NAMES].values, label=df["y_out"].values,
                       feature_name=OUT_PROB_FEATURE_NAMES)
    final = lgb.train(_params(seeds[0]), dall, num_boost_round=n_rounds)
    final.save_model(str(MODEL_PATH))
    metrics["model_path"] = str(MODEL_PATH)
    metrics["refit_period"] = [df["date"].min(), df["date"].max()]
    METRICS_PATH.write_text(json.dumps(metrics, ensure_ascii=False, indent=2, default=str))
    logger.info(f"保存: {MODEL_PATH} / {METRICS_PATH}")


if __name__ == "__main__":
    main()
