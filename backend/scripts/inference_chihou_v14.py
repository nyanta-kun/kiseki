"""地方競馬 v13 バッチ推論・全期間バックフィル

v13 とは（2026-08-02）:
  **v14(2026-08-14) で市場乖離5特徴を削除した**（`chihou_prod_lgb.v14_39feat.txt`）。
  本番は odds_map を渡されず市場特徴が常に中立値だったため、
  市場を使わずに学習し直した。詳細: docs/chihou_rebuild_2026_08.md 13章。
  変わるのは composite のスケールだけで、レース内 min-max 15〜85 を廃止し
  中心化線形（50 + CHIHOU_INDEX_SCALE * (p − レース内平均)）にした。
  詳細と根拠は `chihou_calculator._scale_to_index_local` の docstring 参照。

なぜ全期間バックフィルが要るのか:
  DB の履歴は **version=10（30特徴・市場特徴なし）で止まっており**、本番が serve して
  いる v12（44特徴）が過去に一度も適用されていなかった。honest 検証
  （train ≤2025-06 / test 2026-01〜06・6,418R）で両者の差は大きい:

    指標                v10相当(DB実測)   44特徴モデル    差
    1位馬 勝率            0.3967          0.4624       +6.6pt
    1位馬 複勝率          0.7172          0.7661       +4.9pt
    レース内 Spearman     0.5280          0.5822       +0.054

  差は paired bootstrap で全て有意（95%CI が 0 を跨がない）。原因は市場（オッズ）特徴で、
  market 5本を外すと 44特徴モデルも v10 と同水準（0.3995）まで落ちる。

母集団の注意（生存者バイアス対策）:
  `race_results` は **LEFT JOIN** で結合し、出走取消・失格馬も含む全出走馬に対して
  指数を算出する。本番 `chihou_recommender.rank_by_hn` が出走予定馬全体で順位を
  確定する設計と揃えるため（memory: chihou_survivor_bias_audit_2026_07_23）。
  学習用の `train_chihou_market_lgb.BASE_QUERY` は逆に完走馬のみに絞っているので、
  そのまま流用してはいけない。

⚠️ **バックフィルした過去分は in-sample**:
  本番モデルは全期間を1回で学習した単一モデルのため、過去レースに遡って適用すると
  model-vintage look-ahead が入る。**DB の composite_index / win_probability を使って
  過去の ROI・的中率を評価してはいけない**（JRA v27 と同じ但し書き）。
  honest 評価は `chihou_rebuild_walkforward.py` 等の walk-forward スクリプトで行うこと。

使い方:
    cd backend
    .venv/bin/python scripts/inference_chihou_v14.py --start 20240101 --end 20260802 --dry-run
    .venv/bin/python scripts/inference_chihou_v14.py --start 20240101 --end 20260802
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv

load_dotenv(_root.parent / ".env")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from psycopg2.extras import execute_values  # noqa: E402

from scripts.chihou_rank_quality_review import connect  # noqa: E402
from scripts.train_chihou_market_lgb import PROD_FEATURES, prep  # noqa: E402
from scripts.train_chihou_prod_lgb import CHIHOU_V9_VERSION  # noqa: E402
from scripts.train_chihou_v11_lightgbm import fetch_hist  # noqa: E402
from src.indices.chihou_calculator import (  # noqa: E402
    CHIHOU_COMPOSITE_VERSION,
    _scale_to_index_local,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("chihou_v13")

MODELS_DIR = _root / "models"
PROD_LGB_T3 = MODELS_DIR / "chihou_prod_lgb.v14_39feat.txt"
PROD_LGB_WIN = MODELS_DIR / "chihou_prod_lgb_win.v14_39feat.txt"

# 学習用 BASE_QUERY との違い: race_results を LEFT JOIN し、完走・正常決着の絞り込みを
# 外している（出走取消・失格馬も母集団に含める）。それ以外の列・結合は同一。
INFER_QUERY = """
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
    ci.last_margin_index AS src_last_margin_index,
    rr.finish_position, rr.win_odds, rr.win_popularity,
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
LEFT JOIN chihou.race_results rr
       ON rr.race_id = ci.race_id AND rr.horse_number = re.horse_number
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

UPSERT_SQL = """
INSERT INTO chihou.calculated_indices
    (race_id, horse_id, version, speed_index, last3f_index, jockey_index,
     rotation_index, last_margin_index, composite_index, win_probability,
     place_probability, calculated_at)
VALUES %s
ON CONFLICT ON CONSTRAINT uq_chihou_calc_idx_race_horse_ver DO UPDATE SET
    speed_index       = EXCLUDED.speed_index,
    last3f_index      = EXCLUDED.last3f_index,
    jockey_index      = EXCLUDED.jockey_index,
    rotation_index    = EXCLUDED.rotation_index,
    last_margin_index = EXCLUDED.last_margin_index,
    composite_index   = EXCLUDED.composite_index,
    win_probability   = EXCLUDED.win_probability,
    place_probability = EXCLUDED.place_probability,
    calculated_at     = EXCLUDED.calculated_at
"""


def fetch_all_entrants(conn, start: str, end: str) -> pd.DataFrame:
    cur = conn.cursor()
    cur.execute(INFER_QUERY, {"ver": CHIHOU_V9_VERSION, "start": start, "end": end})
    df = pd.DataFrame(cur.fetchall(), columns=[d[0] for d in cur.description])
    cur.close()
    for col in ["finish_position", "win_odds", "win_popularity", "head_count"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # `race_results` は (race_id, horse_id) が一意だが (race_id, horse_number) は
    # 一意でない（2024-01〜2026-08 で実測1件の異常データあり）。horse_number で
    # LEFT JOIN しているため、その1件が出走馬1頭を2行に増やし upsert が
    # CardinalityViolation で落ちる。1行の異常データで35万行のバックフィルを
    # 落とさないよう、ここで防御的に重複排除する。
    dup = int(df.duplicated(subset=["race_id", "horse_id"]).sum())
    if dup:
        logger.warning(f"(race_id, horse_id) の重複 {dup} 行を除去した"
                       f"（race_results の horse_number 重複由来）")
        df = df.drop_duplicates(subset=["race_id", "horse_id"], keep="first")
    return df.reset_index(drop=True)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--start", required=True, help="開始日 YYYYMMDD")
    p.add_argument("--end", required=True, help="終了日 YYYYMMDD")
    p.add_argument("--batch-size", type=int, default=5000,
                   help="1回の INSERT 行数。VPS 負荷を抑えるため分割する")
    p.add_argument("--sleep", type=float, default=0.2,
                   help="バッチ間のスリープ秒（VPS 負荷対策）")
    p.add_argument("--dry-run", action="store_true", help="DB へ書き込まない")
    args = p.parse_args()

    import lightgbm as lgb

    if not PROD_LGB_T3.exists() or not PROD_LGB_WIN.exists():
        logger.error(f"モデルが見つかりません: {PROD_LGB_T3} / {PROD_LGB_WIN}")
        sys.exit(1)
    m_t3 = lgb.Booster(model_file=str(PROD_LGB_T3))
    m_win = lgb.Booster(model_file=str(PROD_LGB_WIN))

    conn = connect()
    try:
        logger.info(f"出走馬取得 {args.start}〜{args.end}（取消・失格も含む）")
        df_raw = fetch_all_entrants(conn, args.start, args.end)
        if df_raw.empty:
            logger.error("対象行がありません")
            sys.exit(1)
        logger.info(f"  {len(df_raw):,} 行 / {df_raw['race_id'].nunique():,} レース")
        df_hist = fetch_hist(conn)
        logger.info("特徴量生成（学習と同一の prep）")
        df = prep(conn, df_raw, df_hist)
    finally:
        conn.close()

    df = df.sort_values(["race_id", "horse_id"]).reset_index(drop=True)
    X = df[list(PROD_FEATURES)].to_numpy(dtype=np.float64)
    logger.info(f"推論 {X.shape[0]:,}行 × {X.shape[1]}特徴")
    raw_t3 = m_t3.predict(X)
    raw_win = m_win.predict(X)

    df["place_probability"] = np.clip(raw_t3, 0.0, 1.0)
    df["win_probability"] = np.clip(raw_win, 0.0, 1.0)
    # composite は本番と同じ関数を通す（min-max ではなく中心化線形）
    df["composite_index"] = (
        df.assign(_p=raw_t3)
          .groupby("race_id")["_p"]
          .transform(lambda s: pd.Series(_scale_to_index_local(s.tolist()), index=s.index))
    )

    rng = df["composite_index"]
    per_race = df.groupby("race_id")["composite_index"]
    logger.info(
        f"composite: 全体 min={rng.min():.1f} max={rng.max():.1f} / "
        f"レース内幅 平均={(per_race.max() - per_race.min()).mean():.2f} "
        f"sd={(per_race.max() - per_race.min()).std():.2f}"
        f"（旧 min-max 方式なら幅は常に 70.00 / sd 0.00）"
    )

    if args.dry_run:
        logger.info("--dry-run のため書き込みなし")
        return

    now = pd.Timestamp.utcnow().tz_localize(None).to_pydatetime()
    rows = [
        (int(r.race_id), int(r.horse_id), CHIHOU_COMPOSITE_VERSION,
         float(r.speed_index), float(r.last3f_index), float(r.jockey_index),
         float(r.rotation_index),
         None if pd.isna(r.src_last_margin_index) else float(r.src_last_margin_index),
         round(float(r.composite_index), 1), round(float(r.win_probability), 4),
         round(float(r.place_probability), 4), now)
        for r in df.itertuples(index=False)
    ]

    conn = connect()
    conn.autocommit = False
    written = 0
    t0 = time.time()
    try:
        cur = conn.cursor()
        for i in range(0, len(rows), args.batch_size):
            chunk = rows[i:i + args.batch_size]
            execute_values(cur, UPSERT_SQL, chunk, page_size=1000)
            conn.commit()
            written += len(chunk)
            logger.info(f"  {written:,}/{len(rows):,} 行 ({time.time() - t0:.0f}s)")
            if args.sleep:
                time.sleep(args.sleep)
        cur.close()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    logger.info(f"完了: version={CHIHOU_COMPOSITE_VERSION} を {written:,} 行 upsert "
                f"({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
