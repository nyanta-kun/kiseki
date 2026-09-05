"""地方競馬 walk-forward honest A/B: 未使用データ(kichiuma sueashi/senko・netkeiba training格付け)の効果検証。

2026-07-23 棚卸しで発見した「取得済み×未使用」フィールドを新特徴量化し、
chihou_rebuild_walkforward.py と同じ walk-forward honest 枠組み（model-vintage
look-ahead・生存者バイアスいずれも排除済み）でベースライン(44特徴)と比較する。

新特徴量（2本）:
  kc_sueashi_z  : kichiuma 末脚評価のレース内z-score（実測96.1%カバレッジ・chihouレースとJOIN済み確認）
  kc_senko_z    : kichiuma 先行評価のレース内z-score（同上）

⚠️ netkeiba.training/p_rank/p_comment は当初「16%カバレッジ」と見積もったが、実際に
   chihou.races と JOIN すると一致件数 0（sekito.netkeiba の該当行は別コース＝NAR以外の
   データであり chihou レースには一切紐付かない）。単純な非NULL件数カウントは
   実際のJOIN照合ではないため誤り。本スクリプトでは除外している。

使い方:
  cd backend
  .venv/bin/python scripts/chihou_walkforward_newfeat_ab.py --quarters 3   # 直近3四半期で高速確認
  .venv/bin/python scripts/chihou_walkforward_newfeat_ab.py                # 全8四半期
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

from dotenv import load_dotenv  # noqa: E402
load_dotenv(_root.parent / ".env")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import psycopg2  # noqa: E402

from scripts.train_chihou_market_lgb import (  # noqa: E402
    ALL_FEATURES,
    CHIHOU_V9_VERSION,
    add_corner_trainer_features,
    add_external_features,
    add_historical_features,
    add_market_features,
    add_track_features,
    build_ct_tables,
    compute_wet_apt_table,
    featurize,
    fetch_hist,
    fetch_hist_cond,
    train_binary_control,
)
from scripts.chihou_rebuild_walkforward import QUARTERS, TRAIN_DATA_START
from src.indices.buy_signal import chihou_is_place_bet, chihou_is_sweet_spot

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("chihou_newfeat_ab")

SEED = 0
NEW_FEATURES = ["kc_sueashi_z", "kc_senko_z"]
EXT_FEATURES = ALL_FEATURES + NEW_FEATURES


# TRAIN_QUERY/FULL_POP_QUERY に kc.sueashi/senko, nk.training を追加
TRAIN_QUERY = """
SELECT
    ci.race_id, r.date, r.course_name, r.prize_1st AS curr_prize,
    re.horse_id, r.surface, r.condition, r.distance, r.head_count,
    re.frame_number, re.horse_age, re.weight_carried,
    COALESCE(re.horse_weight, 500) AS horse_weight,
    COALESCE(re.weight_change, 0)  AS weight_change,
    COALESCE(ci.speed_index, 50.0)       AS speed_index,
    COALESCE(ci.last3f_index, 50.0)      AS last3f_index,
    COALESCE(ci.jockey_index, 50.0)      AS jockey_index,
    COALESCE(ci.rotation_index, 50.0)    AS rotation_index,
    COALESCE(ci.last_margin_index, 50.0) AS last_margin_index,
    rr.finish_position,
    rr.win_odds,
    rr.win_popularity,
    CASE WHEN nk.idx_ave ~ '^-?[0-9]+\\*?$'
         THEN regexp_replace(nk.idx_ave, '\\*', '')::float ELSE NULL END AS nk_idx,
    kc.sp_score AS kc_sp,
    kc.sueashi AS kc_sueashi,
    kc.senko AS kc_senko
FROM (
    SELECT DISTINCT ON (race_id, horse_id)
        race_id, horse_id, speed_index, last3f_index, jockey_index,
        rotation_index, last_margin_index
    FROM chihou.calculated_indices
    WHERE version >= %(ver)s
    ORDER BY race_id, horse_id, (version = %(ver)s) DESC, version DESC
) ci
JOIN chihou.races r ON r.id = ci.race_id
JOIN chihou.race_entries re ON re.race_id = ci.race_id AND re.horse_id = ci.horse_id
JOIN chihou.race_results rr ON rr.race_id = ci.race_id AND rr.horse_number = re.horse_number
LEFT JOIN keiba.racecourse_map rc ON rc.netkeiba_id = r.course
LEFT JOIN sekito.netkeiba nk
  ON nk.course_code = rc.code AND nk.date = to_date(r.date, 'YYYYMMDD')
     AND nk.race_no = r.race_number AND nk.horse_no = re.horse_number
     AND nk.is_time_index = true
LEFT JOIN sekito.kichiuma kc
  ON kc.course_code = rc.code AND kc.date = to_date(r.date, 'YYYYMMDD')
     AND kc.race_no = r.race_number AND kc.horse_no = re.horse_number
WHERE r.course != '83'
  AND r.head_count >= 6
  AND r.date BETWEEN %(start)s AND %(end)s
  AND COALESCE(rr.abnormality_code, 0) = 0
  AND rr.finish_position IS NOT NULL
ORDER BY r.date, ci.race_id
"""

FULL_POP_QUERY = """
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
    rr.finish_position,
    rr.win_odds,
    rr.place_odds,
    rr.win_popularity,
    COALESCE(rr.abnormality_code, 0) AS abnormality_code,
    CASE WHEN nk.idx_ave ~ '^-?[0-9]+\\*?$'
         THEN regexp_replace(nk.idx_ave, '\\*', '')::float ELSE NULL END AS nk_idx,
    kc.sp_score AS kc_sp,
    kc.sueashi AS kc_sueashi,
    kc.senko AS kc_senko
FROM (
    SELECT DISTINCT ON (race_id, horse_id)
        race_id, horse_id, speed_index, last3f_index, jockey_index,
        rotation_index, last_margin_index
    FROM chihou.calculated_indices
    WHERE version >= %(ver)s
    ORDER BY race_id, horse_id, (version = %(ver)s) DESC, version DESC
) ci
JOIN chihou.races r ON r.id = ci.race_id
JOIN chihou.race_entries re ON re.race_id = ci.race_id AND re.horse_id = ci.horse_id
LEFT JOIN chihou.race_results rr ON rr.race_id = ci.race_id AND rr.horse_number = re.horse_number
LEFT JOIN keiba.racecourse_map rc ON rc.netkeiba_id = r.course
LEFT JOIN sekito.netkeiba nk
  ON nk.course_code = rc.code AND nk.date = to_date(r.date, 'YYYYMMDD')
     AND nk.race_no = r.race_number AND nk.horse_no = re.horse_number
     AND nk.is_time_index = true
LEFT JOIN sekito.kichiuma kc
  ON kc.course_code = rc.code AND kc.date = to_date(r.date, 'YYYYMMDD')
     AND kc.race_no = r.race_number AND kc.horse_no = re.horse_number
WHERE r.course != '83'
  AND r.head_count >= 6
  AND r.date BETWEEN %(start)s AND %(end)s
ORDER BY r.date, ci.race_id
"""


def _fetch(conn, sql: str, params: dict) -> pd.DataFrame:
    cur = conn.cursor()
    cur.execute(sql, params)
    cols = [d[0] for d in cur.description]
    df = pd.DataFrame(cur.fetchall(), columns=cols)
    cur.close()
    for col in ["finish_position", "win_odds", "place_odds", "win_popularity", "head_count",
                "horse_number", "kc_sueashi", "kc_senko"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _add_new_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    g = df.groupby("race_id")

    def zscore(s: pd.Series) -> pd.Series:
        sd = s.std()
        return (s - s.mean()) / sd if sd and sd > 0 else s * 0.0

    df["kc_sueashi_z"] = g["kc_sueashi"].transform(zscore).fillna(0.0)
    df["kc_senko_z"] = g["kc_senko"].transform(zscore).fillna(0.0)
    return df


def _featurize_full(df_raw: pd.DataFrame, df_hist: pd.DataFrame, apt_tbl, ct_tables) -> pd.DataFrame:
    df = featurize(df_raw)
    df = add_historical_features(df, df_hist)
    for col in ("improving_form", "track_win_rate", "class_drop_ratio", "prev_pace_ratio"):
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(-1.0)
    df = add_external_features(df)
    df = add_track_features(df, apt_tbl)
    df = add_corner_trainer_features(df, *ct_tables)
    df = add_market_features(df)
    df = _add_new_features(df)
    return df


def _evaluate(df_test: pd.DataFrame, score_col: str, label: str) -> dict:
    """指数1位勝率・sweet_spot/place_bet相当のROIを honest 母集団で評価する。"""
    d = df_test.copy()
    d["idx_rank"] = d.groupby("race_id")[score_col].rank(method="first", ascending=False).astype("Int64")
    d["fav_odds"] = d.groupby("race_id")["win_odds"].transform("min")
    settled = d[
        d["finish_position"].notna() & (d["abnormality_code"] == 0)
        & d["win_odds"].notna() & (d["win_odds"] >= 1.0)
    ].copy()

    top1 = settled[settled["idx_rank"] == 1]
    n1 = len(top1)
    m1_win = int((top1["finish_position"] == 1).sum())
    m1_top3 = int((top1["finish_position"] <= 3).sum())

    settled["is_sweet_spot"] = settled.apply(
        lambda x: chihou_is_sweet_spot(int(x["idx_rank"]) if pd.notna(x["idx_rank"]) else None, x["win_odds"], x["course_name"]),
        axis=1,
    )
    settled["is_place_bet"] = settled.apply(
        lambda x: chihou_is_place_bet(
            int(x["idx_rank"]) if pd.notna(x["idx_rank"]) else None, x["win_odds"], x["fav_odds"],
            int(x["head_count"]) if pd.notna(x["head_count"]) else None,
        ),
        axis=1,
    )
    ss = settled[settled["is_sweet_spot"]]
    ss_k = ss.groupby("race_id").size()
    ss = ss[ss["race_id"].isin(ss_k[ss_k <= 2].index)]
    ss_n, ss_hits = len(ss), int((ss["finish_position"] == 1).sum())
    ss_roi = float(ss.loc[ss["finish_position"] == 1, "win_odds"].sum()) / ss_n if ss_n else 0.0

    return {
        "label": label, "n_top1": n1,
        "m1_win_rate": round(m1_win / n1 * 100, 2) if n1 else 0.0,
        "m1_top3_rate": round(m1_top3 / n1 * 100, 2) if n1 else 0.0,
        "sweet_spot_n": ss_n, "sweet_spot_roi": round(ss_roi, 3),
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--quarters", type=int, default=len(QUARTERS))
    args = p.parse_args()
    quarters = QUARTERS[-args.quarters:] if args.quarters < len(QUARTERS) else QUARTERS

    dsn = (
        f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
        f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
        f"password={os.getenv('DB_PASSWORD')}"
    )
    conn = psycopg2.connect(dsn)

    logger.info("補助テーブル読み込み中...")
    df_hist_global = fetch_hist(conn)
    apt_tbl = compute_wet_apt_table(fetch_hist_cond(conn))
    ct_tables = build_ct_tables(conn)

    baseline_results = []
    extended_results = []

    for train_end, test_start, test_end in quarters:
        logger.info("=== quarter train_end=%s test=%s〜%s ===", train_end, test_start, test_end)
        df_train_raw = _fetch(conn, TRAIN_QUERY, {"ver": CHIHOU_V9_VERSION, "start": TRAIN_DATA_START, "end": train_end})
        if df_train_raw["race_id"].nunique() < 200:
            logger.warning("  学習データ不足のためスキップ")
            continue
        df_train = _featurize_full(df_train_raw, df_hist_global, apt_tbl, ct_tables)
        df_train_s = df_train.sort_values("race_id").reset_index(drop=True)
        fp_tr = pd.to_numeric(df_train_s["finish_position"], errors="coerce")
        y_top3 = (fp_tr <= 3).astype(int).values

        cov = df_train_s["kc_sueashi"].notna().mean() * 100
        logger.info("  学習=%d レース  sueashi/senkoカバレッジ=%.1f%%", df_train_s["race_id"].nunique(), cov)

        X_tr_base = df_train_s[ALL_FEATURES].fillna(0.0).values.astype(np.float64)
        X_tr_ext = df_train_s[EXT_FEATURES].fillna(0.0).values.astype(np.float64)
        m_base = train_binary_control(X_tr_base, y_top3, SEED, feature_names=ALL_FEATURES)
        m_ext = train_binary_control(X_tr_ext, y_top3, SEED, feature_names=EXT_FEATURES)

        df_test_raw = _fetch(conn, FULL_POP_QUERY, {"ver": CHIHOU_V9_VERSION, "start": test_start, "end": test_end})
        if df_test_raw["race_id"].nunique() == 0:
            continue
        df_test = _featurize_full(df_test_raw, df_hist_global, apt_tbl, ct_tables)
        df_test["quarter"] = f"{test_start}-{test_end}"

        X_te_base = df_test[ALL_FEATURES].fillna(0.0).values.astype(np.float64)
        X_te_ext = df_test[EXT_FEATURES].fillna(0.0).values.astype(np.float64)
        df_test["score_base"] = m_base.predict(X_te_base)
        df_test["score_ext"] = m_ext.predict(X_te_ext)

        baseline_results.append(_evaluate(df_test, "score_base", f"baseline_{test_start}"))
        extended_results.append(_evaluate(df_test, "score_ext", f"extended_{test_start}"))

    conn.close()

    def _agg(results: list[dict]) -> dict:
        n1 = sum(r["n_top1"] for r in results)
        win_hits = sum(r["m1_win_rate"] / 100 * r["n_top1"] for r in results)
        top3_hits = sum(r["m1_top3_rate"] / 100 * r["n_top1"] for r in results)
        ss_n = sum(r["sweet_spot_n"] for r in results)
        return {
            "n_top1": n1,
            "m1_win_rate": round(win_hits / n1 * 100, 2) if n1 else 0.0,
            "m1_top3_rate": round(top3_hits / n1 * 100, 2) if n1 else 0.0,
            "sweet_spot_n": ss_n,
        }

    print(f"\n{'='*78}\n  walk-forward honest A/B: baseline(44特徴) vs extended(+sueashi/senko/training)\n{'='*78}")
    b = _agg(baseline_results)
    e = _agg(extended_results)
    print(f"  {'':<12}{'n(top1)':>10}{'M1単勝率%':>12}{'M1複勝率%':>12}{'sweet_spot_n':>14}")
    print(f"  {'baseline':<12}{b['n_top1']:>10,}{b['m1_win_rate']:>12.2f}{b['m1_top3_rate']:>12.2f}{b['sweet_spot_n']:>14,}")
    print(f"  {'extended':<12}{e['n_top1']:>10,}{e['m1_win_rate']:>12.2f}{e['m1_top3_rate']:>12.2f}{e['sweet_spot_n']:>14,}")
    print(f"  {'差':<12}{'':>10}{e['m1_win_rate']-b['m1_win_rate']:>+12.2f}{e['m1_top3_rate']-b['m1_top3_rate']:>+12.2f}{e['sweet_spot_n']-b['sweet_spot_n']:>+14,}")

    print(f"\n四半期別:")
    for br, er in zip(baseline_results, extended_results):
        print(f"  {br['label']:<28} base: n={br['n_top1']:>5} M1win={br['m1_win_rate']:>6.2f}%  "
              f"ext: n={er['n_top1']:>5} M1win={er['m1_win_rate']:>6.2f}%  差={er['m1_win_rate']-br['m1_win_rate']:>+5.2f}pt")


if __name__ == "__main__":
    main()
