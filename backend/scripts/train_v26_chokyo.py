"""v26 + 調教(坂路 SLOP)特徴量の再学習 A/B 実験（拡張版・2026-07-26 再検証）。

train_v26_lightgbm.py の特徴量・データ・分割を流用し、坂路調教データ
(keiba.slope_training) から導出した特徴量を追加した場合としない場合で
test メトリクスを比較する。DB 書き込みは一切行わない研究用スクリプト。

評価軸（今回のユーザー要望・前回セッションから変更）: **複勝的中率**
(top1_place_pct = 指数1位馬が3着以内に入る率) を主指標とする。
単勝的中率・単勝ROIも併記する（前回セッションの主指標だった
top5捕捉率(place_in_top5)は参考値として残す）。

坂路特徴量（馬の直近35日の坂路追いから・レース日より前のみ=リークなし）:
  最速4F追いを「本追い」とみなし、トレセン別z-score化:
  - chokyo_4f_z      : 最速4F合計タイムの同トレセンz (負=速い=シャープ)
  - chokyo_last1f_z  : その追いの終い1F(200-0)の同トレセンz (負=終い速い)
  - chokyo_accel     : lap_400_200 - lap_200_0 (正=終い加速=伸びる脚)
  - chokyo_days_since: 直近追いからレースまでの日数 (詰め/間隔)
  - chokyo_count_35d : 直近35日の坂路追い本数 (調教量)
  上記5種を CHOKYO_FEATURES_SIMPLE とする。

  個体相対系（馬自身の過去との比較=上昇気配、2026-06-07セッションで追加）:
  - chokyo_self_4f_dev    : 本追い4F - 自己ベースライン(35〜180日前)中央値
  - chokyo_self_1f_dev    : 最終追い終い1F - 自己ベースライン中央値
  - chokyo_final_last1f_z : 最終追い(直近日)の終い1Fトレセンz
  上記3種を CHOKYO_FEATURES_SELF とする。CHOKYO_FEATURES_FULL = SIMPLE + SELF。

2026-07-26 拡張点（このファイル自体は元々 main 未マージの
feat/training-chokyo-backfill ブランチ上にのみ存在したため、実行のため
本ブランチの working tree に再構築した。ロジックは 2026-06-07 時点の
実装から変更していない。以下は今回追加した拡張のみ）:
  1. baseline / +simple5 / +full8 の **3アーム比較**に拡張
     （元は baseline vs +CHOKYO_FEATURES の2アームのみ）。
  2. calculated_indices の **version=24 は 2026-04-26 で backfill が
     止まっている**ことが判明したため（要調査で発覚）、同一スキーマで
     2026-07-26まで継続更新されている **version=26** を使うオプションを
     追加（--indices-version）。値は重複期間で厳密に一致することを
     行単位で確認済み（本スクリプト内では検証しない。別途確認済み）。
  3. test 期間を「旧窓（前回セッションと同一）」「拡張窓（今回のchokyo
     データ拡張を活かした窓）」の両方で評価できるよう --test-end に
     加えて --test-end-legacy を追加。

使い方:
  PYTHONPATH=. .venv/bin/python scripts/train_v26_chokyo.py --seeds 5
"""

from __future__ import annotations

import argparse
import json
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
    DATA_QUERY,
    featurize,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("v26_chokyo")

WINDOW_DAYS = 35
HISTORY_FLOOR = "20230101"
BASELINE_DAYS = 180  # 自己ベースライン参照窓（prep窓より前 35〜180日）
MIN_BASELINE = 3     # 自己ベースライン算出の最低本数

CHOKYO_FEATURES_SIMPLE = [
    "chokyo_4f_z", "chokyo_last1f_z", "chokyo_accel",
    "chokyo_days_since", "chokyo_count_35d",
]
CHOKYO_FEATURES_SELF = [
    "chokyo_self_4f_dev", "chokyo_self_1f_dev", "chokyo_final_last1f_z",
]
CHOKYO_FEATURES_FULL = CHOKYO_FEATURES_SIMPLE + CHOKYO_FEATURES_SELF

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
    """YYYYMMDD 文字列を date に変換する。不正値は None を返す。"""
    try:
        return datetime.strptime(str(yyyymmdd), "%Y%m%d").date()
    except (ValueError, TypeError):
        return None


def _valid(v) -> bool:
    """値が欠損(None/NaN)でないかを判定する。"""
    return v is not None and not pd.isna(v)


def load_slope(conn, end: str, floor: str = HISTORY_FLOOR) -> tuple[dict, dict]:
    """坂路履歴を取得し、トレセン別z統計と horse_id→works のマップを返す。"""
    cur = conn.cursor()
    cur.execute(SLOPE_HISTORY_QUERY, {"floor": floor, "end": end})
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    cur.close()
    df = pd.DataFrame(rows, columns=cols)
    logger.info(f"坂路履歴取得: {len(df):,}行 ({floor}〜{end})")
    if df.empty:
        return {}, {}

    for c in ("time_4f", "lap_400_200", "lap_200_0"):
        df[c] = pd.to_numeric(df[c], errors="coerce")

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


def _features_for(race_date, horse_id, works_by_horse, cstats) -> dict[str, float]:
    nan = {f: np.nan for f in CHOKYO_FEATURES_FULL}
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
    final = max(cand, key=lambda w: w["date"])        # 最終追い=直近

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
    self_4f = (
        best["time_4f"] - float(np.median(b4))
    ) if len(b4) >= MIN_BASELINE and _valid(best["time_4f"]) else np.nan
    self_1f = (
        final["lap_200_0"] - float(np.median(b1))
    ) if len(b1) >= MIN_BASELINE and _valid(final["lap_200_0"]) else np.nan

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
    """坂路特徴量(8種)を付与する。リークなし(レース日より前の追いのみ使用)。"""
    df = df.copy()
    feats = [
        _features_for(rd, h, works_by_horse, cstats)
        for rd, h in zip(df["race_date"], df["horse_id"])
    ]
    fdf = pd.DataFrame(feats, index=df.index)
    for c in CHOKYO_FEATURES_FULL:
        df[c] = fdf[c]
    return df


def fetch_dataset_ver(conn, start: str, end: str, version: int) -> pd.DataFrame:
    """train_v26_lightgbm.DATA_QUERY を再利用し、任意の calculated_indices.version で取得する。

    version=24 は 2026-04-26 で backfill が止まっているため、それ以降の
    期間を評価する場合は version=26(同一スキーマ・2026-07-26まで継続更新)
    を指定する。
    """
    cur = conn.cursor()
    cur.execute(DATA_QUERY, {"ver": version, "start": start, "end": end})
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    cur.close()
    df = pd.DataFrame(rows, columns=cols)
    logger.info(f"取得(v{version}): {len(df):,}行 ({start}〜{end})")
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


def _train(df_train, df_valid, features, args, seed):
    Xtr, Xva = df_train[features].values, df_valid[features].values
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
    return model


def _importance_ranks(model, features, chokyo_subset) -> dict[str, int]:
    imp = dict(zip(features, model.feature_importance(importance_type="gain")))
    order = sorted(features, key=lambda f: -imp[f])
    return {cf: order.index(cf) + 1 for cf in chokyo_subset}


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
    # データ窓（既定=2026-06-07セッションの recent 早期A/B窓を踏襲）
    p.add_argument("--train-start", default="20250701")
    p.add_argument("--train-end", default="20260131")
    p.add_argument("--valid-start", default="20260201")
    p.add_argument("--valid-end", default="20260315")
    p.add_argument("--test-start", default="20260316")
    p.add_argument("--test-end-legacy", default="20260510",
                   help="前回セッションと同一の test 終了日(比較用)")
    p.add_argument("--test-end", default="20260726",
                   help="今回拡張した chokyo データを活かした test 終了日")
    p.add_argument("--slope-floor", default="20250527",
                   help="坂路履歴取得の下限日(既定=現DB最古日 2025-05-27)")
    p.add_argument("--indices-version", type=int, default=26,
                   help="calculated_indices.version。24は2026-04-26でbackfill停止のため既定26")
    p.add_argument("--out", default=str(_root / "models" / "v26_phase4b_chokyo_extended_metrics.json"))
    args = p.parse_args()

    dsn = (
        f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
        f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
        f"password={os.getenv('DB_PASSWORD')}"
    )
    conn = psycopg2.connect(dsn)

    cstats, works = load_slope(conn, args.test_end, args.slope_floor)

    df_train = attach_chokyo(
        featurize(fetch_dataset_ver(conn, args.train_start, args.train_end, args.indices_version)),
        works, cstats,
    )
    df_valid = attach_chokyo(
        featurize(fetch_dataset_ver(conn, args.valid_start, args.valid_end, args.indices_version)),
        works, cstats,
    )
    df_test_legacy = attach_chokyo(
        featurize(fetch_dataset_ver(conn, args.test_start, args.test_end_legacy, args.indices_version)),
        works, cstats,
    )
    df_test_ext = attach_chokyo(
        featurize(fetch_dataset_ver(conn, args.test_start, args.test_end, args.indices_version)),
        works, cstats,
    )
    conn.close()

    for d in (df_train, df_valid, df_test_legacy, df_test_ext):
        d["y"] = (pd.to_numeric(d["finish_position"], errors="coerce") <= 3).astype(int)

    cov_legacy = df_test_legacy["chokyo_4f_z"].notna().mean() * 100
    cov_ext = df_test_ext["chokyo_4f_z"].notna().mean() * 100
    logger.info(
        f"坂路特徴カバレッジ: legacy窓({args.test_start}〜{args.test_end_legacy})={cov_legacy:.1f}% "
        f"/ 拡張窓({args.test_start}〜{args.test_end})={cov_ext:.1f}%"
    )
    logger.info(
        f"レース数: train={len(df_train['race_id'].unique()):,} "
        f"valid={len(df_valid['race_id'].unique()):,} "
        f"test_legacy={len(df_test_legacy['race_id'].unique()):,} "
        f"test_ext={len(df_test_ext['race_id'].unique()):,}"
    )

    keys = ["top1_win_pct", "top1_place_pct", "top1_win_roi", "place_in_top5", "place_in_top3"]
    arms = {
        "base": ALL_FEATURES,
        "simple5": ALL_FEATURES + CHOKYO_FEATURES_SIMPLE,
        "full8": ALL_FEATURES + CHOKYO_FEATURES_FULL,
    }
    test_sets = {"legacy": df_test_legacy, "ext": df_test_ext}

    # runs[testset][arm][key] = [seed毎の値]
    runs: dict[str, dict[str, dict[str, list[float]]]] = {
        ts: {arm: {k: [] for k in keys} for arm in arms} for ts in test_sets
    }
    rank_accum: dict[str, dict[str, list[int]]] = {
        "simple5": {f: [] for f in CHOKYO_FEATURES_SIMPLE},
        "full8": {f: [] for f in CHOKYO_FEATURES_FULL},
    }
    n_features = {arm: len(feats) for arm, feats in arms.items()}

    for seed in range(args.seeds):
        for arm, feats in arms.items():
            logger.info(f"=== seed {seed}: {arm} ({len(feats)}特徴) ===")
            model = _train(df_train, df_valid, feats, args, seed)
            for ts_name, df_test in test_sets.items():
                s = model.predict(df_test[feats].values, num_iteration=model.best_iteration)
                m = evaluate2(df_test, s, f"test[{ts_name}/{arm}#{seed}]")
                for k in keys:
                    runs[ts_name][arm][k].append(float(m[k]))
            if arm in rank_accum:
                ranks = _importance_ranks(model, feats, arms[arm][len(ALL_FEATURES):])
                for f, r in ranks.items():
                    rank_accum[arm][f].append(r)

    labels = {
        "top1_win_pct": "単勝的中率%", "top1_place_pct": "★複勝的中率%",
        "top1_win_roi": "単勝ROI",
        "place_in_top5": "top5内3着内頭数", "place_in_top3": "top3内3着内頭数",
    }

    summary: dict = {
        "generated_at": datetime.now().isoformat(),
        "seeds": args.seeds,
        "indices_version": args.indices_version,
        "windows": {
            "train": [args.train_start, args.train_end],
            "valid": [args.valid_start, args.valid_end],
            "test_legacy": [args.test_start, args.test_end_legacy],
            "test_ext": [args.test_start, args.test_end],
        },
        "n_races": {
            "train": int(len(df_train["race_id"].unique())),
            "valid": int(len(df_valid["race_id"].unique())),
            "test_legacy": int(len(df_test_legacy["race_id"].unique())),
            "test_ext": int(len(df_test_ext["race_id"].unique())),
        },
        "chokyo_coverage_pct": {"test_legacy": round(cov_legacy, 2), "test_ext": round(cov_ext, 2)},
        "n_features": n_features,
        "results": {},
        "feature_importance_rank_avg": {},
    }

    for ts_name, df_test in test_sets.items():
        print("\n" + "=" * 92)
        print(
            f"[{ts_name}窓 {args.test_start}〜{(args.test_end_legacy if ts_name == 'legacy' else args.test_end)}] "
            f"v26 test メトリクス比較（binary・{args.seeds} seed 平均±std）"
        )
        print("-" * 92)
        header = f"{'指標':<20}"
        for arm in arms:
            header += f"{arm:>18}"
        header += f"{'Δsimple5':>14}{'Δfull8':>14}"
        print(header)
        print("-" * 92)
        summary["results"][ts_name] = {}
        for key in keys:
            row = f"{labels[key]:<20}"
            vals = {}
            for arm in arms:
                arr = np.array(runs[ts_name][arm][key])
                vals[arm] = (float(arr.mean()), float(arr.std()))
                row += f"{arr.mean():>10.3f}±{arr.std():<6.3f}"
            d5 = vals["simple5"][0] - vals["base"][0]
            d8 = vals["full8"][0] - vals["base"][0]
            row += f"{d5:>+14.4f}{d8:>+14.4f}"
            print(row)
            summary["results"][ts_name][key] = {
                arm: {"mean": vals[arm][0], "std": vals[arm][1]} for arm in arms
            }
        print("=" * 92)

    print("\n坂路特徴量 feature importance 順位（gain基準・seed平均・分母=全特徴数）:")
    for arm in ("simple5", "full8"):
        print(f"  [{arm}] (全{n_features[arm]}特徴)")
        for f, ranks in rank_accum[arm].items():
            arr = np.array(ranks)
            print(f"    {f:<24} 平均{arr.mean():.1f}位 (min{arr.min()}/max{arr.max()})")
        summary["feature_importance_rank_avg"][arm] = {
            f: {"mean": float(np.mean(r)), "min": int(np.min(r)), "max": int(np.max(r))}
            for f, r in rank_accum[arm].items()
        }

    out_path = Path(args.out)
    if out_path.exists():
        logger.warning(f"{out_path} は既に存在するため保存をスキップします（上書き禁止）")
    else:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2))
        logger.info(f"サマリー保存: {out_path}")


if __name__ == "__main__":
    main()
