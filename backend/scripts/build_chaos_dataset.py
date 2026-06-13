"""荒れるレース事前分類器 — データセット構築スクリプト。

1レース1行のデータセットを構築し backend/data/roi100/chaos_dataset.parquet に出力する。

特徴量（point-in-time 厳守: 全て発走前に確定する情報のみ）:
  レース属性:
    head_count   — 出走頭数 (確定後・発走前確定)
    distance     — 距離 (m)
    is_turf      — 芝=1, ダート=0
    is_handicap  — weight_type_code='3'(ハンデ) =1
    race_num     — レース番号 (1-12)
    kai          — 開催回次 (jravan_race_id[10:12])
    day          — 開催日次 (jravan_race_id[12:14])
    grade_code   — グレードコード (1=G1, 2=G2, 3=G3, 4=特別, 5-=一般)

  市場構造（確定単勝オッズ: 締切前最終値 = race_results.win_odds で代用）:
    odds_top1    — 1番人気オッズ
    odds_top3_sum — 上位3頭オッズ合計
    odds_entropy  — オッズエントロピー（シャノン情報量）
    odds_gap12   — 1-2番人気オッズ差
    odds_gap23   — 2-3番人気オッズ差
    n_over10     — 単勝10倍超頭数

  モデル構造（v26 win_probability: 発走前算出・point-in-time OK）:
    wp_top1      — モデル1位 win_probability
    wp_top3_sum  — モデル上位3頭 win_probability 合計
    wp_entropy   — モデル win_probability エントロピー
    wp_mkt_gap   — モデルランク1位と市場1番人気のズレ（1=一致, 0=不一致）
    wp_mkt_corr  — モデル確率と市場確率（1/odds 正規化）のスピアマン相関

ターゲット定義（3種を全て出力: 採用定義は evaluate スクリプトで比較）:
  target_a: 三連単払戻 >= 100,000円
  target_b: 三連単払戻 >= 中央値×5 (集計期間全体で固定)
  target_c: 1-3番人気が3着内に1頭以下

欠損処理:
  - win_odds が全馬欠損のレースはオッズ特徴量 NaN（モデルは NaN 対応）
  - trifecta 払戻が存在しないレースは target_a/target_b を欠損 (NaN) として残す
  - win_probability が存在しないレースはモデル特徴量 NaN

使い方:
  cd backend
  .venv/bin/python scripts/build_chaos_dataset.py --start 20230101 --end 20260531
  # スモーク (3ヶ月窓)
  .venv/bin/python scripts/build_chaos_dataset.py --start 20250101 --end 20250331

出力: backend/data/roi100/chaos_dataset.parquet
"""

from __future__ import annotations

import argparse
import logging
import math
import os
import sys
from pathlib import Path

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv

load_dotenv(_root.parent / ".env")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import psycopg2  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("build_chaos_dataset")

OUTPUT_DIR = _root / "data" / "roi100"
OUTPUT_PATH = OUTPUT_DIR / "chaos_dataset.pkl"

# 対象コース（JRAのみ）
JRA_COURSES = ("01", "02", "03", "04", "05", "06", "07", "08", "09", "10")

# チャンク期間（一度に取得する月数）
CHUNK_MONTHS = 6

# ターゲット B の倍率
TARGET_B_MULTIPLIER = 5


def _conn() -> psycopg2.extensions.connection:
    """psycopg2 接続を返す。接続情報は環境変数から取得。"""
    dsn = (
        f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
        f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
        f"password={os.getenv('DB_PASSWORD')}"
    )
    return psycopg2.connect(dsn)


# ---------------------------------------------------------------------------
# クエリ
# ---------------------------------------------------------------------------

# 1レース1行: レース属性 + 人気情報取得
RACE_Q = """
SELECT
    r.id         AS race_id,
    r.date       AS date,
    r.course     AS course,
    r.race_number AS race_num,
    r.surface    AS surface,
    r.distance   AS distance,
    r.head_count AS head_count,
    r.weight_type_code AS weight_type_code,
    r.grade      AS grade,
    r.jravan_race_id AS jravan_race_id
FROM keiba.races r
WHERE r.date BETWEEN %(start)s AND %(end)s
  AND r.course IN %(courses)s
  AND r.head_count >= 4
ORDER BY r.date, r.id
"""

# 馬ごとの win_odds と人気・着順（market structure & target_c 用）
RESULTS_Q = """
SELECT
    rr.race_id,
    rr.horse_id,
    rr.win_odds,
    rr.win_popularity,
    rr.finish_position
FROM keiba.race_results rr
JOIN keiba.races r ON r.id = rr.race_id
WHERE r.date BETWEEN %(start)s AND %(end)s
  AND r.course IN %(courses)s
  AND COALESCE(rr.abnormality_code, 0) = 0
  AND rr.finish_position IS NOT NULL
ORDER BY rr.race_id
"""

# v26 win_probability（モデル構造特徴量）
WP_Q = """
SELECT
    ci.race_id,
    ci.horse_id,
    ci.win_probability
FROM keiba.calculated_indices ci
JOIN keiba.races r ON r.id = ci.race_id
WHERE r.date BETWEEN %(start)s AND %(end)s
  AND r.course IN %(courses)s
  AND ci.version = 26
  AND ci.win_probability IS NOT NULL
ORDER BY ci.race_id
"""

# 三連単払戻（MAX を取る: 複数着順で分割の場合があるため）
TRIFECTA_Q = """
SELECT
    rp.race_id,
    MAX(rp.payout) AS payout
FROM keiba.race_payouts rp
JOIN keiba.races r ON r.id = rp.race_id
WHERE r.date BETWEEN %(start)s AND %(end)s
  AND r.course IN %(courses)s
  AND rp.bet_type = 'trifecta'
GROUP BY rp.race_id
"""


# ---------------------------------------------------------------------------
# 特徴量計算ヘルパ
# ---------------------------------------------------------------------------


def _safe_entropy(probs: np.ndarray) -> float:
    """シャノンエントロピー（nats）を安全に計算。0確率はスキップ。"""
    p = probs[probs > 0]
    if len(p) == 0:
        return float("nan")
    return float(-np.sum(p * np.log(p)))


def _market_probs(odds: np.ndarray) -> np.ndarray:
    """単勝オッズ → 市場確率（1/odds で正規化）。"""
    inv = 1.0 / odds
    s = inv.sum()
    if s <= 0:
        return np.full_like(inv, float("nan"))
    return inv / s


def _spearman_corr(x: np.ndarray, y: np.ndarray) -> float:
    """スピアマン相関（pandas を使わず numpy で）。頭数不足は NaN。"""
    n = len(x)
    if n < 3:
        return float("nan")
    rx = np.argsort(np.argsort(x))
    ry = np.argsort(np.argsort(y))
    d = (rx.astype(float) - ry.astype(float)) ** 2
    return float(1 - 6 * d.sum() / (n * (n**2 - 1)))


def _build_market_features(grp: pd.DataFrame) -> dict:
    """market_features を1レース分計算して dict で返す。

    引数:
        grp: race_results のレースグループ (race_id 単位)。
             列: win_odds, win_popularity, finish_position

    point-in-time 根拠:
        win_odds は締切前最終オッズ（race_results に保存された確定値）。
        win_popularity も確定人気順。
        finish_position は target_c のラベル計算のみに使用し、特徴量には使わない。
    """
    result: dict = {}

    # オッズ列 (NaN 除外後で計算)
    odds = pd.to_numeric(grp["win_odds"], errors="coerce")
    pop = pd.to_numeric(grp["win_popularity"], errors="coerce")
    fp = pd.to_numeric(grp["finish_position"], errors="coerce")

    valid_odds = odds.dropna()
    has_odds = len(valid_odds) >= 3

    if has_odds:
        # 人気順にソート
        sorted_idx = pop.dropna().sort_values().index
        sorted_odds = odds.loc[sorted_idx].dropna()
        n = len(sorted_odds)
        oddsv = sorted_odds.values

        result["odds_top1"] = float(oddsv[0]) if n >= 1 else float("nan")
        result["odds_top3_sum"] = float(oddsv[:3].sum()) if n >= 3 else float("nan")
        mprobs = _market_probs(oddsv)
        result["odds_entropy"] = _safe_entropy(mprobs)
        result["odds_gap12"] = float(oddsv[1] - oddsv[0]) if n >= 2 else float("nan")
        result["odds_gap23"] = float(oddsv[2] - oddsv[1]) if n >= 3 else float("nan")
        result["n_over10"] = int((valid_odds >= 10.0).sum())
    else:
        result["odds_top1"] = float("nan")
        result["odds_top3_sum"] = float("nan")
        result["odds_entropy"] = float("nan")
        result["odds_gap12"] = float("nan")
        result["odds_gap23"] = float("nan")
        result["n_over10"] = int(len(valid_odds))

    # target_c: 1〜3番人気が3着内に1頭以下
    # 発走前情報は使っていない（ラベル計算用）
    favs_in_top3 = 0
    for _, row in grp.iterrows():
        p = row.get("win_popularity")
        f = row.get("finish_position")
        try:
            if int(p) <= 3 and int(f) <= 3:
                favs_in_top3 += 1
        except (TypeError, ValueError):
            pass
    result["target_c"] = 1 if favs_in_top3 <= 1 else 0

    return result


def _build_model_features(grp: pd.DataFrame, mkt_probs: np.ndarray | None) -> dict:
    """win_probability 特徴量を1レース分計算。

    引数:
        grp: calculated_indices のレースグループ (race_id 単位)。
             列: win_probability
        mkt_probs: 市場確率ベクトル (人気順整列済み)。None の場合は wp_mkt_corr=NaN。

    point-in-time 根拠:
        v26 win_probability は発走前に算出されたモデル確率。
        レース開始後の情報（着順・実走時間等）は含まない。
    """
    result: dict = {}

    wp = pd.to_numeric(grp["win_probability"], errors="coerce").dropna()
    n = len(wp)

    if n < 2:
        result["wp_top1"] = float("nan")
        result["wp_top3_sum"] = float("nan")
        result["wp_entropy"] = float("nan")
        result["wp_mkt_gap"] = float("nan")
        result["wp_mkt_corr"] = float("nan")
        return result

    sorted_wp = wp.sort_values(ascending=False).values
    result["wp_top1"] = float(sorted_wp[0])
    result["wp_top3_sum"] = float(sorted_wp[:3].sum())
    result["wp_entropy"] = _safe_entropy(sorted_wp)

    # wp_mkt_gap: モデル1位の horse_id と市場1番人気の horse_id が一致するか
    # (horse_id は特徴量に入らない: 一致フラグのみ)
    # 実装上: grp に horse_id と win_popularity を持たせて照合
    if "win_popularity" in grp.columns and "horse_id" in grp.columns:
        wp_df = grp[["horse_id", "win_probability", "win_popularity"]].copy()
        wp_df["win_probability"] = pd.to_numeric(wp_df["win_probability"], errors="coerce")
        wp_df["win_popularity"] = pd.to_numeric(wp_df["win_popularity"], errors="coerce")
        wp_df = wp_df.dropna(subset=["win_probability", "win_popularity"])
        if len(wp_df) >= 2:
            model_top1_horse = wp_df.loc[wp_df["win_probability"].idxmax(), "horse_id"]
            market_top1_horse = wp_df.loc[wp_df["win_popularity"].idxmin(), "horse_id"]
            result["wp_mkt_gap"] = 1 if model_top1_horse == market_top1_horse else 0
        else:
            result["wp_mkt_gap"] = float("nan")
    else:
        result["wp_mkt_gap"] = float("nan")

    # モデル確率と市場確率のスピアマン相関
    if mkt_probs is not None and len(mkt_probs) == n:
        result["wp_mkt_corr"] = _spearman_corr(sorted_wp, mkt_probs)
    else:
        result["wp_mkt_corr"] = float("nan")

    return result


# ---------------------------------------------------------------------------
# チャンク取得
# ---------------------------------------------------------------------------


def _date_chunks(start: str, end: str) -> list[tuple[str, str]]:
    """start〜end を CHUNK_MONTHS ヶ月ごとに分割したチャンクリストを返す。"""
    from datetime import date, timedelta

    def parse(s: str) -> date:
        return date(int(s[:4]), int(s[4:6]), int(s[6:8]))

    def fmt(d: date) -> str:
        return d.strftime("%Y%m%d")

    s = parse(start)
    e = parse(end)
    chunks = []
    cur = s
    while cur <= e:
        # next chunk start
        year = cur.year + (cur.month - 1 + CHUNK_MONTHS) // 12
        month = (cur.month - 1 + CHUNK_MONTHS) % 12 + 1
        nxt = date(year, month, 1)
        chunk_end = min(nxt - timedelta(days=1), e)
        chunks.append((fmt(cur), fmt(chunk_end)))
        cur = nxt
    return chunks


def _fetch_chunk(
    conn: psycopg2.extensions.connection, start: str, end: str
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """1チャンク分の4テーブルを取得して返す。"""
    params = {"start": start, "end": end, "courses": JRA_COURSES}
    cur = conn.cursor()

    cur.execute(RACE_Q, params)
    races = pd.DataFrame(cur.fetchall(), columns=[d[0] for d in cur.description])

    cur.execute(RESULTS_Q, params)
    results = pd.DataFrame(cur.fetchall(), columns=[d[0] for d in cur.description])

    cur.execute(WP_Q, params)
    wp = pd.DataFrame(cur.fetchall(), columns=[d[0] for d in cur.description])

    cur.execute(TRIFECTA_Q, params)
    tri = pd.DataFrame(cur.fetchall(), columns=[d[0] for d in cur.description])

    cur.close()
    return races, results, wp, tri


# ---------------------------------------------------------------------------
# メイン構築ロジック
# ---------------------------------------------------------------------------


def build_dataset(start: str, end: str) -> pd.DataFrame:
    """データセットを構築して DataFrame で返す。

    引数:
        start: 開始日 YYYYMMDD
        end: 終了日 YYYYMMDD

    戻り値:
        1レース1行の DataFrame。
    """
    chunks = _date_chunks(start, end)
    logger.info("チャンク数: %d (%s 〜 %s)", len(chunks), start, end)

    conn = _conn()
    all_dfs: list[pd.DataFrame] = []

    for chunk_start, chunk_end in chunks:
        logger.info("  チャンク取得中: %s 〜 %s", chunk_start, chunk_end)
        races, results, wp_df, tri = _fetch_chunk(conn, chunk_start, chunk_end)

        if races.empty:
            logger.info("    → レースなし。スキップ。")
            continue

        logger.info("    → %d レース取得", len(races))

        # results を race_id ごとにグループ化
        res_grp = results.groupby("race_id")
        wp_grp = wp_df.groupby("race_id")
        tri_map = dict(zip(tri["race_id"], tri["payout"]))

        rows: list[dict] = []
        for _, race_row in races.iterrows():
            race_id = int(race_row["race_id"])
            row: dict = {
                "race_id": race_id,
                "date": str(race_row["date"]),
                "course": str(race_row["course"]),
                "race_num": int(race_row["race_num"]),
                "distance": float(race_row["distance"]),
                "head_count": int(race_row["head_count"]),
                "is_turf": 1 if str(race_row["surface"]).strip() == "芝" else 0,
                "is_handicap": 1 if str(race_row["weight_type_code"]).strip() == "3" else 0,
            }

            # grade コード数値化 (G1=1, G2=2, G3=3, OP=4, 一般=5)
            grade = str(race_row.get("grade") or "").strip()
            grade_map = {"G1": 1, "G2": 2, "G3": 3, "OP": 4}
            row["grade_code"] = grade_map.get(grade, 5)

            # jravan_race_id から kai, day を抽出
            jrid = str(race_row.get("jravan_race_id") or "")
            if len(jrid) >= 14:
                try:
                    row["kai"] = int(jrid[10:12])
                    row["day"] = int(jrid[12:14])
                except ValueError:
                    row["kai"] = 0
                    row["day"] = 0
            else:
                row["kai"] = 0
                row["day"] = 0

            # 市場構造特徴量
            if race_id in res_grp.groups:
                rg = res_grp.get_group(race_id).copy()
                mkt_features = _build_market_features(rg)
                row.update(mkt_features)

                # 市場確率ベクトル（モデル相関計算用）
                odds_arr = pd.to_numeric(rg["win_odds"], errors="coerce").dropna().values
                mkt_probs = _market_probs(odds_arr) if len(odds_arr) >= 3 else None

                # モデル構造特徴量（wp_df と results の horse_id を照合）
                if race_id in wp_grp.groups:
                    wg = wp_grp.get_group(race_id).copy()
                    # win_popularity を results から付与
                    pop_map = rg.set_index("horse_id")["win_popularity"].to_dict()
                    wg["win_popularity"] = wg["horse_id"].map(pop_map)
                    model_features = _build_model_features(wg, mkt_probs)
                    row.update(model_features)
                else:
                    for k in ("wp_top1", "wp_top3_sum", "wp_entropy", "wp_mkt_gap", "wp_mkt_corr"):
                        row[k] = float("nan")
            else:
                for k in (
                    "odds_top1",
                    "odds_top3_sum",
                    "odds_entropy",
                    "odds_gap12",
                    "odds_gap23",
                    "n_over10",
                    "target_c",
                    "wp_top1",
                    "wp_top3_sum",
                    "wp_entropy",
                    "wp_mkt_gap",
                    "wp_mkt_corr",
                ):
                    row[k] = float("nan")

            # 三連単払戻（ターゲット A/B 用）
            payout = tri_map.get(race_id)
            row["trifecta_payout"] = float(payout) if payout is not None else float("nan")
            # target_a/target_b は evaluate スクリプトで使う（median は全期間で計算）
            row["target_a"] = int(payout >= 100_000) if payout is not None else float("nan")

            rows.append(row)

        if rows:
            all_dfs.append(pd.DataFrame(rows))

    conn.close()

    if not all_dfs:
        return pd.DataFrame()

    df = pd.concat(all_dfs, ignore_index=True)
    logger.info("全 %d レース取得完了", len(df))

    # target_b: 三連単払戻 >= 中央値×5 (取得期間全体の中央値)
    valid_pay = df["trifecta_payout"].dropna()
    if len(valid_pay) > 0:
        median_pay = float(valid_pay.median())
        threshold_b = median_pay * TARGET_B_MULTIPLIER
        logger.info(
            "三連単払戻 中央値=%.0f円, target_b 閾値(×5)=%.0f円",
            median_pay,
            threshold_b,
        )
        df["target_b"] = df["trifecta_payout"].apply(
            lambda x: int(x >= threshold_b) if not math.isnan(x) else float("nan")
        )
    else:
        df["target_b"] = float("nan")

    return df


def main() -> None:
    """エントリーポイント。"""
    parser = argparse.ArgumentParser(description="荒れるレース分類器 データセット構築")
    parser.add_argument("--start", default="20230101", help="開始日 YYYYMMDD (default: 20230101)")
    parser.add_argument("--end", default="20260531", help="終了日 YYYYMMDD (default: 20260531)")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    df = build_dataset(args.start, args.end)
    if df.empty:
        logger.error("データが取得できませんでした。DB 接続・期間を確認してください。")
        sys.exit(1)

    df.to_pickle(OUTPUT_PATH)
    logger.info("保存完了: %s (%d 行)", OUTPUT_PATH, len(df))

    # カバレッジサマリ
    na_odds = df["odds_top1"].isna().sum()
    na_wp = df["wp_top1"].isna().sum()
    na_tri = df["trifecta_payout"].isna().sum()
    total = len(df)
    logger.info(
        "カバレッジ: win_odds 欠損=%d (%.1f%%) / wp 欠損=%d (%.1f%%) / trifecta 欠損=%d (%.1f%%)",
        na_odds,
        100 * na_odds / total,
        na_wp,
        100 * na_wp / total,
        na_tri,
        100 * na_tri / total,
    )
    logger.info(
        "target_a 正例率=%.1f%% / target_c 正例率=%.1f%%",
        100 * df["target_a"].mean(),
        100 * df["target_c"].mean(),
    )


if __name__ == "__main__":
    main()
