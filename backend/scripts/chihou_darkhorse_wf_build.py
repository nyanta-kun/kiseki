"""地方競馬 穴馬研究用 walk-forward honest 予測セットの生成。

`chihou_rebuild_walkforward.py` と同じ「四半期ごとに、その時点までのデータだけで
再学習して当該四半期を予測する」方式で model-vintage look-ahead を排除する。
違いは 3 点だけ:

  1. **対象期間を広げる**（既定 2024-07〜2026-07 / 9四半期）。
     穴馬は的中率が低く分散が大きいため、標本数が結論を左右する。
  2. **CSV ダンプを必須にする**。以後の探索・確認はこの1本の成果物だけを使い、
     「どの期間を何回見たか」を追跡可能にする。
  3. **ルール判定を一切しない**。sweet_spot 等の既存条件をここで適用すると、
     生成物が特定仮説に汚染される。判定は下流スクリプトの責務とする。

母集団は出走予定馬全体（LEFT JOIN）で idx_rank を確定してから確定結果に絞る。
本番 `chihou_recommender.rank_by_hn` と母集団を揃えるためで、
2026-07-23 監査で見つかった生存者バイアスの再発防止（`FULL_POP_QUERY` を流用）。

DB への書き込みは行わない（研究用途・標準出力と CSV のみ）。

使い方:
  cd backend
  .venv/bin/python scripts/chihou_darkhorse_wf_build.py --out /path/to/wf.csv
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

from scripts.chihou_rebuild_walkforward import (  # noqa: E402
    FULL_POP_QUERY,
    SEED,
    TRAIN_DATA_START,
    TRAIN_QUERY,
    _featurize_full,
    _fetch,
)
from scripts.train_chihou_market_lgb import (  # noqa: E402
    ALL_FEATURES,
    PROD_FEATURES,
    CHIHOU_V9_VERSION,
    build_ct_tables,
    compute_wet_apt_table,
    fetch_hist,
    fetch_hist_cond,
    train_binary_control,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("chihou_darkhorse_wf")

# (train_end, test_start, test_end)
# 2024-07 開始。それ以前はサブ指数(calculated_indices)が 2024-01 からしか無く、
# 学習に使える履歴が半年未満になるため対象外とする。
QUARTERS: list[tuple[str, str, str]] = [
    ("20240630", "20240701", "20240930"),
    ("20240930", "20241001", "20241231"),
    ("20241231", "20250101", "20250331"),
    ("20250331", "20250401", "20250630"),
    ("20250630", "20250701", "20250930"),
    ("20250930", "20251001", "20251231"),
    ("20251231", "20260101", "20260331"),
    ("20260331", "20260401", "20260630"),
    ("20260630", "20260701", "20260731"),
]

# ダンプする列（下流の探索で使う可能性があるものだけに絞る）。
# 特徴量そのものは 44 本あるが、条件探索でそれらを自由に使うと仮説空間が
# 爆発して多重比較が制御できなくなるため、意図的に落とす。
# 発走 N 分前の単勝オッズ スナップショット。
# 発走時刻(post_time)は JST の hhmm、odds_history.fetched_at は **UTC** 保存なので
# JST → UTC へ 9 時間戻して比較する（chihou_darkhorse_prerace.py と同一の扱い）。
# odds_history は 2026-04-07 以降しか無いため、それ以前の期間では空になる。
PRERACE_ODDS_QUERY = """
WITH r AS (
  SELECT id, (to_timestamp(date || post_time, 'YYYYMMDDHH24MI') - interval '9 hours') AS post_utc
  FROM chihou.races
  WHERE date BETWEEN %(start)s AND %(end)s AND course <> '83'
    AND post_time ~ '^[0-9]{4}$'
)
SELECT DISTINCT ON (o.race_id, o.combination)
       o.race_id, o.combination::int AS horse_number, o.odds AS pre_odds
FROM r
JOIN chihou.odds_history o
  ON o.race_id = r.id AND o.bet_type = 'win'
 AND o.combination ~ '^[0-9]+$'
 AND o.fetched_at <= r.post_utc - (%(lead)s || ' minutes')::interval
ORDER BY o.race_id, o.combination, o.fetched_at DESC
"""


def _apply_prerace_odds(df: pd.DataFrame, pre: pd.DataFrame) -> pd.DataFrame:
    """市場特徴の入力を確定オッズから発走前オッズへ差し替える。

    本番のライブ指数は odds_map を渡されないため市場特徴が中立値で動いているが、
    仮に「発走前オッズを渡す」よう直した場合に何が起きるかを測るための差し替え。

    確定オッズ(win_odds/win_popularity)は評価に使うので final_* へ退避する。
    **全出走馬にスナップショットがあるレースだけ**を残す。1頭でも欠けると
    レース内のオッズ順位が歪み、市場特徴が本番と別物になるため。
    """
    df = df.merge(pre, on=["race_id", "horse_number"], how="left")
    full = df.groupby("race_id")["pre_odds"].transform(lambda s: s.notna().all())
    dropped_races = df.loc[~full, "race_id"].nunique()
    df = df[full].copy()
    if dropped_races:
        logger.info("  発走前オッズが全馬揃わないレースを除外: %d", dropped_races)
    df["final_odds"] = df["win_odds"]
    df["final_popularity"] = df["win_popularity"]
    df["win_odds"] = df["pre_odds"]
    df["win_popularity"] = (
        df.groupby("race_id")["pre_odds"].rank(ascending=True, method="min").astype(int)
    )
    return df


KEEP_COLS = [
    "race_id", "date", "quarter", "course_name", "horse_id", "horse_number",
    "head_count", "distance", "is_turf", "condition", "horse_age",
    "win_odds", "place_odds", "win_popularity", "fav_odds", "finish_position",
    "composite_wf", "win_prob_wf", "idx_rank_wf", "win_prob_rank_wf",
    "nk_rank_n", "kc_rank_n", "ext_missing",
]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--out", required=True, help="walk-forward honest 予測結果の出力 CSV パス")
    p.add_argument("--quarters", type=int, default=len(QUARTERS), help="先頭から何四半期処理するか")
    p.add_argument(
        "--upset-weight",
        type=float,
        default=None,
        metavar="W",
        help=(
            "人気薄の行の学習重みを W 倍にして「穴馬専用指数」を作る。"
            "重み付けの基準は確定人気順位だが、**人気は特徴量に入れない**ので "
            "serve 時に人気を知る必要はなく look-ahead にならない"
            "（学習集合の層別であって入力ではない）。--no-market と併用すること"
        ),
    )
    p.add_argument(
        "--upset-from",
        type=int,
        default=4,
        metavar="P",
        help="--upset-weight の対象。確定 P 番人気以下を重くする（既定 4）",
    )
    p.add_argument(
        "--prerace-lead",
        type=int,
        default=None,
        metavar="N",
        help=(
            "市場特徴の入力を『発走N分前のオッズ』に差し替える。"
            "確定オッズ(=賭け時点で未知)を使った検証と、市場特徴なしで動いている本番との"
            "中間、すなわち『実現可能な上限』を測るためのモード。"
            "odds_history が 2026-04-07 以降しか無いためそれ以前は空になる"
        ),
    )
    p.add_argument(
        "--no-market",
        action="store_true",
        help=(
            "市場特徴5本を外して学習・予測する（PROD_FEATURES のみ）。"
            "本番のライブ指数は odds_map を渡されないため市場特徴が常に中立値になる。"
            "その条件を再現して条件の再現性を確かめるために使う"
        ),
    )
    args = p.parse_args()
    quarters = QUARTERS[: args.quarters]

    dsn = (
        f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
        f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
        f"password={os.getenv('DB_PASSWORD')}"
    )
    conn = psycopg2.connect(dsn)

    # 市場特徴を含めるか。本番のライブ指数は odds_map を渡されないため
    # odds_rank_n / is_heavy_fav / is_dark_horse が常に中立値で動いている。
    # --no-market はその条件を再現する（＝検証と本番の乖離を測る）。
    feature_set = PROD_FEATURES if args.no_market else ALL_FEATURES
    logger.info(
        "特徴量セット: %s (%d本)",
        "PROD_FEATURES(市場なし)" if args.no_market else "ALL_FEATURES(市場あり)",
        len(feature_set),
    )

    logger.info("補助テーブル読み込み中 (fetch_hist / apt_tbl / ct_tables)...")
    # いずれも「現走前の累積のみ」を参照する point-in-time 計算のため、
    # 全期間から作っても対象行の特徴量に未来は混入しない（実装確認済み）。
    df_hist_global = fetch_hist(conn)
    apt_tbl = compute_wet_apt_table(fetch_hist_cond(conn))
    ct_tables = build_ct_tables(conn)

    all_settled: list[pd.DataFrame] = []

    for train_end, test_start, test_end in quarters:
        logger.info("=== quarter train_end=%s test=%s〜%s ===", train_end, test_start, test_end)

        df_train_raw = _fetch(
            conn, TRAIN_QUERY,
            {"ver": CHIHOU_V9_VERSION, "start": TRAIN_DATA_START, "end": train_end},
        )
        n_train_races = df_train_raw["race_id"].nunique()
        if n_train_races < 200:
            logger.warning("  学習データ不足(%dレース)のためスキップ", n_train_races)
            continue
        df_train = _featurize_full(df_train_raw, df_hist_global, apt_tbl, ct_tables)
        df_train = df_train.sort_values("race_id").reset_index(drop=True)
        fp_tr = pd.to_numeric(df_train["finish_position"], errors="coerce")
        X_tr = df_train[feature_set].fillna(0.0).values.astype(np.float64)

        w_tr = None
        if args.upset_weight is not None:
            pop_tr = pd.to_numeric(df_train["win_popularity"], errors="coerce")
            w_tr = np.where(pop_tr >= args.upset_from, args.upset_weight, 1.0)
            logger.info(
                "  穴馬重み: %d番人気以下を x%.1f (対象 %.1f%%)",
                args.upset_from, args.upset_weight,
                100.0 * float((pop_tr >= args.upset_from).mean()),
            )

        m_top3 = train_binary_control(
            X_tr, (fp_tr <= 3).astype(int).values, SEED,
            feature_names=feature_set, sample_weight=w_tr,
        )
        m_win = train_binary_control(
            X_tr, (fp_tr == 1).astype(int).values, SEED,
            feature_names=feature_set, sample_weight=w_tr,
        )

        df_test_raw = _fetch(
            conn, FULL_POP_QUERY,
            {"ver": CHIHOU_V9_VERSION, "start": test_start, "end": test_end},
        )
        n_races_test = df_test_raw["race_id"].nunique()
        logger.info("  学習=%d レース / テスト=%d レース", n_train_races, n_races_test)
        if n_races_test == 0:
            continue

        if args.prerace_lead is not None:
            pre = _fetch(
                conn, PRERACE_ODDS_QUERY,
                {"start": test_start, "end": test_end, "lead": str(args.prerace_lead)},
            )
            if pre.empty:
                logger.warning("  発走前オッズなし（odds_history は 2026-04-07 以降）→ スキップ")
                continue
            df_test_raw = _apply_prerace_odds(df_test_raw, pre)
            if df_test_raw.empty:
                logger.warning("  発走前オッズが全馬揃うレースが無い → スキップ")
                continue

        df_test = _featurize_full(df_test_raw, df_hist_global, apt_tbl, ct_tables)
        X_te = df_test[feature_set].fillna(0.0).values.astype(np.float64)
        df_test = df_test.copy()
        df_test["composite_wf"] = m_top3.predict(X_te)
        df_test["win_prob_wf"] = m_win.predict(X_te)
        # 順位は出走予定馬全体で確定させる（本番 rank_by_hn と同じ母集団）
        df_test["idx_rank_wf"] = (
            df_test.groupby("race_id")["composite_wf"].rank(method="first", ascending=False).astype(int)
        )
        df_test["win_prob_rank_wf"] = (
            df_test.groupby("race_id")["win_prob_wf"].rank(method="first", ascending=False).astype(int)
        )
        if args.prerace_lead is not None:
            # 市場特徴の計算は済んだので、以降の評価は確定オッズに戻す
            df_test["win_odds"] = df_test["final_odds"]
            df_test["win_popularity"] = df_test["final_popularity"]
        df_test["fav_odds"] = df_test.groupby("race_id")["win_odds"].transform("min")
        df_test["quarter"] = f"{test_start}-{test_end}"

        settled = df_test[
            df_test["finish_position"].notna()
            & (df_test["abnormality_code"] == 0)
            & df_test["win_odds"].notna()
            & (df_test["win_odds"] >= 1.0)
        ].copy()
        cols = [c for c in KEEP_COLS if c in settled.columns]
        missing = [c for c in KEEP_COLS if c not in settled.columns]
        if missing:
            logger.warning("  ダンプ対象に無い列: %s", missing)
        all_settled.append(settled[cols])

    conn.close()

    if not all_settled:
        logger.error("有効な四半期データがありませんでした")
        sys.exit(1)

    full = pd.concat(all_settled, ignore_index=True)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    full.to_csv(out, index=False)
    logger.info(
        "保存: %s (%d行 / %dレース / %d四半期)",
        out, len(full), full["race_id"].nunique(), full["quarter"].nunique(),
    )

    # サニティチェック: vintage ごとの指数1位馬の勝率が安定しているか
    print(f"\n{'=' * 70}\n  四半期別 サニティチェック（指数1位馬）\n{'=' * 70}")
    top1 = full[full["idx_rank_wf"] == 1]
    for q, g in top1.groupby("quarter"):
        win = (g["finish_position"] == 1).mean()
        place = (g["finish_position"] <= 3).mean()
        print(f"  {q}  n={len(g):5,}  勝率={win:.4f}  複勝率={place:.4f}")


if __name__ == "__main__":
    main()
