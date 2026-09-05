"""地方競馬 walk-forward honest再構築（keirinの rebuild_*_walkforward.py 相当）。

## 背景
現行の本番モデル(chihou_prod_lgb.v12_44feat.txt)は 2023-01〜直近まで全期間を
1回だけ学習した「単一モデル」であり、backfill(chihou_backfill_indices.py)は
この単一モデルを2024-01以降の全historical raceにretroactivelyに適用している。
これは「モデルの学習パラメータ自体が対象レースより未来のデータを反映している」
という model-vintage look-ahead を意味する（keirinで見つかった「モデルが賢くなる
たびに過去分析がリークする」問題と同型。memory: chihou_survivor_bias_audit_2026_07_23）。

なお特徴量自体(speed_index等のサブ指数・historical/track/corner/trainer系)は
train_chihou_prod_lgb.py 内で確認済みの通りいずれも「現走前の累積のみ」を使う
per-row point-in-time計算のため、学習に使う行を日付で絞るだけで walk-forward化できる
（ct_tables/apt_tbl 等の補助テーブル自体は全期間から作られるが、各行の特徴値は
その馬/調教師の「自分より過去の行」の累積のみを参照するため、未来の行が
テーブルに含まれていても対象行の特徴量には影響しない）。

## やること
四半期ごとに「その時点までのデータだけ」で is_top3 / is_win の2モデルを学習しなおし、
その四半期のレースをそのvintageのモデルで honest に予測する。予測後、
出走予定馬全体（LEFT JOIN、Phase0の生存者バイアス修正と同一方針）で idx_rank を
確定し、sweet_spot/place_bet の真の walk-forward ROI を計算する。

DBへの書き込みは一切行わない（研究用途。結果は標準出力のみ）。

使い方:
  cd backend
  .venv/bin/python scripts/chihou_rebuild_walkforward.py
  .venv/bin/python scripts/chihou_rebuild_walkforward.py --quarters 2  # 動作確認用に軽量実行
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
from src.indices.buy_signal import chihou_is_place_bet, chihou_is_sweet_spot
from src.services.chihou_place_odds_guard import filter_races_with_full_place_odds  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("chihou_walkforward")

TRAIN_DATA_START = "20230101"
SEED = 0

# 四半期境界（train_end, test_start, test_end）。2024-10 以前は事前学習データが
# 薄いため対象外とする（keirinも初期期間はwalk-forward対象外とした前例に倣う）。
QUARTERS = [
    ("20240930", "20241001", "20241231"),
    ("20241231", "20250101", "20250331"),
    ("20250331", "20250401", "20250630"),
    ("20250630", "20250701", "20250930"),
    ("20250930", "20251001", "20251231"),
    ("20251231", "20260101", "20260331"),
    ("20260331", "20260401", "20260630"),
    ("20260630", "20260701", "20260723"),
]

# 出走予定馬全体（LEFT JOIN）を母集団にする Phase0 修正済みクエリ
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
    kc.sp_score AS kc_sp
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

# 学習用（settled のみ・ラベルが要るため）
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
    kc.sp_score AS kc_sp
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


def _fetch(conn, sql: str, params: dict) -> pd.DataFrame:
    cur = conn.cursor()
    cur.execute(sql, params)
    cols = [d[0] for d in cur.description]
    df = pd.DataFrame(cur.fetchall(), columns=cols)
    cur.close()
    for col in ["finish_position", "win_odds", "place_odds", "win_popularity", "head_count", "horse_number"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _featurize_full(df_raw: pd.DataFrame, df_hist: pd.DataFrame, apt_tbl, ct_tables) -> pd.DataFrame:
    """train_chihou_market_lgb.prep と同一パイプライン（conn再フェッチを避けるため引数化）。"""
    df = featurize(df_raw)
    df = add_historical_features(df, df_hist)
    for col in ("improving_form", "track_win_rate", "class_drop_ratio", "prev_pace_ratio"):
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(-1.0)
    df = add_external_features(df)
    df = add_track_features(df, apt_tbl)
    df = add_corner_trainer_features(df, *ct_tables)
    df = add_market_features(df)
    return df


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--quarters", type=int, default=len(QUARTERS), help="先頭から何四半期処理するか(動作確認用)")
    p.add_argument("--dump-csv", type=str, default=None, help="walk-forward honest 予測結果(全列)をCSVに保存するパス")
    args = p.parse_args()
    quarters = QUARTERS[: args.quarters]

    dsn = (
        f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
        f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
        f"password={os.getenv('DB_PASSWORD')}"
    )
    conn = psycopg2.connect(dsn)

    logger.info("補助テーブル読み込み中 (fetch_hist / apt_tbl / ct_tables)...")
    df_hist_global = fetch_hist(conn)
    apt_tbl = compute_wet_apt_table(fetch_hist_cond(conn))
    ct_tables = build_ct_tables(conn)

    all_settled: list[pd.DataFrame] = []

    for train_end, test_start, test_end in quarters:
        logger.info("=== quarter train_end=%s test=%s〜%s ===", train_end, test_start, test_end)

        # ── 学習: train_end までの settled データのみ ──
        df_train_raw = _fetch(conn, TRAIN_QUERY, {"ver": CHIHOU_V9_VERSION, "start": TRAIN_DATA_START, "end": train_end})
        if df_train_raw["race_id"].nunique() < 200:
            logger.warning("  学習データ不足(%dレース)のためスキップ", df_train_raw["race_id"].nunique())
            continue
        df_train = _featurize_full(df_train_raw, df_hist_global, apt_tbl, ct_tables)
        df_train_s = df_train.sort_values("race_id").reset_index(drop=True)
        fp_tr = pd.to_numeric(df_train_s["finish_position"], errors="coerce")
        y_top3 = (fp_tr <= 3).astype(int).values
        y_win = (fp_tr == 1).astype(int).values
        X_tr = df_train_s[ALL_FEATURES].fillna(0.0).values.astype(np.float64)

        m_top3 = train_binary_control(X_tr, y_top3, SEED, feature_names=ALL_FEATURES)
        m_win = train_binary_control(X_tr, y_win, SEED, feature_names=ALL_FEATURES)

        # ── テスト: 出走予定馬全体(LEFT JOIN)でこのvintageモデルを適用 ──
        df_test_raw = _fetch(conn, FULL_POP_QUERY, {"ver": CHIHOU_V9_VERSION, "start": test_start, "end": test_end})
        n_races_test = df_test_raw["race_id"].nunique()
        logger.info("  学習=%d レース(%s〜%s)  テスト=%d レース", df_train_s["race_id"].nunique(), TRAIN_DATA_START, train_end, n_races_test)
        if n_races_test == 0:
            continue
        df_test = _featurize_full(df_test_raw, df_hist_global, apt_tbl, ct_tables)
        X_te = df_test[ALL_FEATURES].fillna(0.0).values.astype(np.float64)

        df_test = df_test.copy()
        df_test["composite_wf"] = m_top3.predict(X_te)
        df_test["win_prob_wf"] = m_win.predict(X_te)
        # 出走予定馬全体で idx_rank 確定（Phase0修正と同一の母集団定義）
        df_test["idx_rank_wf"] = (
            df_test.groupby("race_id")["composite_wf"].rank(method="first", ascending=False).astype("Int64")
        )
        df_test["fav_odds"] = df_test.groupby("race_id")["win_odds"].transform("min")
        df_test["quarter"] = f"{test_start}-{test_end}"

        settled = df_test[
            df_test["finish_position"].notna()
            & (df_test["abnormality_code"] == 0)
            & df_test["win_odds"].notna()
            & (df_test["win_odds"] >= 1.0)
        ].copy()
        all_settled.append(settled)

    conn.close()

    if not all_settled:
        logger.error("有効な四半期データがありませんでした")
        return

    full = pd.concat(all_settled, ignore_index=True)
    logger.info("walk-forward honest 予測 完了: %d行 / %dレース", len(full), full["race_id"].nunique())

    full["is_sweet_spot"] = full.apply(
        lambda x: chihou_is_sweet_spot(
            int(x["idx_rank_wf"]) if pd.notna(x["idx_rank_wf"]) else None, x["win_odds"], x["course_name"]
        ),
        axis=1,
    )
    full["is_place_bet"] = full.apply(
        lambda x: chihou_is_place_bet(
            int(x["idx_rank_wf"]) if pd.notna(x["idx_rank_wf"]) else None, x["win_odds"], x["fav_odds"],
            int(x["head_count"]) if pd.notna(x["head_count"]) else None,
        ),
        axis=1,
    )

    if args.dump_csv:
        full.to_csv(args.dump_csv, index=False)
        logger.info("walk-forward honest 予測結果を保存: %s (%d行)", args.dump_csv, len(full))

    def _show(label: str, sub: pd.DataFrame, bet: str) -> None:
        if sub.empty:
            print(f"  [{label}] 該当なし")
            return
        if bet == "win":
            mask = sub["finish_position"] == 1
            n = len(sub)
            hits = int(mask.sum())
            roi = float(sub.loc[mask, "win_odds"].sum()) / n if n else 0.0
            print(f"  [{label}] n={n:,}  hits={hits}  hit_rate={hits/n*100:.1f}%  単勝ROI={roi:.3f}")
        else:
            # place_odds の欠損は着順と相関している（HR払戻は1〜3着しか埋めない）。
            # notna() で落とすだけだと母集団が「当たり馬だけ」になり ROI が壊れる。
            # 全馬揃っているレースだけを母集団にする。
            valid, audit = filter_races_with_full_place_odds(sub)
            n = len(valid)
            hits = int(valid["finish_position"].between(1, 3, inclusive="both").sum())
            roi = float(valid.loc[valid["finish_position"].between(1, 3, inclusive="both"), "place_odds"].sum()) / n if n else 0.0
            print(f"  [{label}] n={n:,}  hits={hits}  hit_rate={hits/n*100 if n else 0:.1f}%  複勝ROI={roi:.3f}")
            print(audit.format())

    print(f"\n{'='*74}\n  walk-forward honest 集計（全{len(quarters)}四半期・model-vintage leak なし）\n{'='*74}")

    ss = full[full["is_sweet_spot"]].copy()
    ss_k = ss.groupby("race_id").size()
    ss = ss[ss["race_id"].isin(ss_k[ss_k <= 2].index)]
    _show("sweet_spot", ss, "win")

    pb = full[full["is_place_bet"]].copy()
    pb_k = pb.groupby("race_id").size()
    pb = pb[pb["race_id"].isin(pb_k[pb_k <= 2].index)]
    _show("place_bet", pb, "place")

    print(f"\n{'='*74}\n  四半期別 sweet_spot 内訳\n{'='*74}")
    for q, g in ss.groupby("quarter"):
        _show(q, g, "win")


if __name__ == "__main__":
    main()
