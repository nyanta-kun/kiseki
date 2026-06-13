"""ROI100 統一バックテスト CLI。

戦略を --strategy オプションで指定し、Bet リストを生成して settle() に渡す。

同梱リファレンス戦略:
  a) favorite_win    : 全レース1番人気単勝ベタ買い → ROI ≈ 0.78-0.80（控除率近傍）
  b) sweet_spot_win  : 単勝≥10×EV_proxy(1.2-5.0)×指数上位 → 既知ROI ≈ 1.19 と整合確認
  c) top3_trio_box   : 全レース指数上位3頭三連複BOX ベタ買い

使い方:
  .venv/bin/python scripts/roi100_backtest.py \\
      --strategy favorite_win --start 20250101 --end 20250630

  .venv/bin/python scripts/roi100_backtest.py \\
      --strategy sweet_spot_win --start 20230101 --end 20260331 \\
      --splits train:20230101-20250630,test:20250701-20260331

  .venv/bin/python scripts/roi100_backtest.py \\
      --strategy top3_trio_box --start 20250101 --end 20250630
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Generator

import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv

load_dotenv(_root.parent / ".env")

from src.betting.backtest import (
    Bet,
    SettleResult,
    normalize_combination,
    print_settle_report,
    settle,
)
from src.db.session import sync_engine as engine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("roi100_backtest")

# ---------------------------------------------------------------------------
# 標準データ取得クエリ（keiba スキーマ・JRA 中央競馬）
# ---------------------------------------------------------------------------

_BASE_QUERY = text(
    """
SELECT
    r.id             AS race_id,
    r.date           AS race_date,
    re.horse_id,
    re.horse_number,
    re.frame_number,
    ci.composite_index,
    ci.win_probability,
    rr.finish_position,
    rr.abnormality_code,
    rr.win_odds,
    rr.win_popularity,
    rr.dead_heat
FROM keiba.calculated_indices ci
JOIN keiba.races r       ON r.id = ci.race_id
JOIN keiba.race_entries re
    ON re.race_id = ci.race_id AND re.horse_id = ci.horse_id
JOIN keiba.race_results rr
    ON rr.race_id = ci.race_id AND rr.horse_id = ci.horse_id
WHERE r.date BETWEEN :start_date AND :end_date
  AND r.course IN ('01','02','03','04','05','06','07','08','09','10')
  AND ci.version = (
      SELECT MAX(version)
      FROM keiba.calculated_indices
      WHERE race_id = ci.race_id
  )
ORDER BY r.date, r.id, re.horse_number
"""
)

_CHUNK_DAYS = 90  # 3ヶ月チャンク


def _date_chunks(start: str, end: str, chunk_days: int = _CHUNK_DAYS) -> list[tuple[str, str]]:
    """YYYYMMDD 文字列を chunk_days 日ごとに分割して (start, end) ペアのリストを返す。"""
    from datetime import date, timedelta

    s = date(int(start[:4]), int(start[4:6]), int(start[6:]))
    e = date(int(end[:4]), int(end[4:6]), int(end[6:]))
    chunks: list[tuple[str, str]] = []
    cur = s
    while cur <= e:
        nxt = min(cur + timedelta(days=chunk_days - 1), e)
        chunks.append((cur.strftime("%Y-%m-%d"), nxt.strftime("%Y-%m-%d")))
        cur = nxt + timedelta(days=1)
    return chunks


def load_race_data(start: str, end: str) -> pd.DataFrame:
    """指定期間のレースデータを3ヶ月チャンクで取得する。

    Args:
        start: 開始日 YYYYMMDD
        end:   終了日 YYYYMMDD

    Returns:
        pandas DataFrame（race_id, horse_number, composite_index, win_popularity 等）
    """
    chunks = _date_chunks(start, end)
    dfs: list[pd.DataFrame] = []

    with Session(engine) as db:
        for chunk_start, chunk_end in chunks:
            result = db.execute(
                _BASE_QUERY,
                {"start_date": chunk_start, "end_date": chunk_end},
            )
            rows = result.fetchall()
            if rows:
                df_chunk = pd.DataFrame(rows, columns=list(result.keys()))
                dfs.append(df_chunk)
            logger.info(f"チャンク {chunk_start}〜{chunk_end}: {len(rows)} 行取得")

    if not dfs:
        return pd.DataFrame()

    df = pd.concat(dfs, ignore_index=True)

    # 型変換
    df["composite_index"] = pd.to_numeric(df["composite_index"], errors="coerce")
    df["win_probability"] = pd.to_numeric(df["win_probability"], errors="coerce")
    df["finish_position"] = pd.to_numeric(df["finish_position"], errors="coerce")
    df["abnormality_code"] = pd.to_numeric(df["abnormality_code"], errors="coerce").fillna(0)
    df["win_odds"] = pd.to_numeric(df["win_odds"], errors="coerce")
    df["win_popularity"] = pd.to_numeric(df["win_popularity"], errors="coerce")
    df["horse_number"] = pd.to_numeric(df["horse_number"], errors="coerce")
    df["frame_number"] = pd.to_numeric(df["frame_number"], errors="coerce")
    df["race_date"] = df["race_date"].astype(str)

    logger.info(
        f"合計 {len(df):,} 行 / {df['race_id'].nunique():,} レース取得完了"
    )
    return df


def filter_valid_races(df: pd.DataFrame) -> pd.DataFrame:
    """出走取消・着順なし・指数なし を除外する。

    異常コードが 0 以外の馬を含むレースは全除外（返還が発生したレース）。
    """
    bad_races = df[
        (df["abnormality_code"] > 0)
        | df["finish_position"].isna()
        | df["composite_index"].isna()
        | df["horse_number"].isna()
    ]["race_id"].unique()

    df = df[~df["race_id"].isin(bad_races)].copy()

    # 頭数フィルタ（3頭未満は三連複不成立）
    race_counts = df.groupby("race_id")["horse_id"].count()
    valid = race_counts[race_counts >= 3].index
    df = df[df["race_id"].isin(valid)].copy()

    logger.info(
        f"フィルタ後: {len(df):,} 行 / {df['race_id'].nunique():,} レース "
        f"（除外: {len(bad_races):,} レース）"
    )
    return df


# ---------------------------------------------------------------------------
# リファレンス戦略
# ---------------------------------------------------------------------------


def strategy_favorite_win(df: pd.DataFrame) -> list[Bet]:
    """戦略a: 全レース1番人気単勝ベタ買い。

    健全性チェック用。ROI ≈ 0.78-0.80（控除率近傍）が期待値。
    """
    bets: list[Bet] = []
    for race_id, grp in df.groupby("race_id"):
        fav = grp[grp["win_popularity"] == 1]
        if fav.empty:
            continue
        horse_no = int(fav.iloc[0]["horse_number"])
        bets.append(
            Bet(
                race_id=int(race_id),
                bet_type="win",
                combination=normalize_combination("win", [horse_no]),
                stake=100,
                tag="favorite_win",
            )
        )
    return bets


def strategy_sweet_spot_win(df: pd.DataFrame) -> list[Bet]:
    """戦略b: 単勝≥10 × EV_proxy(1.2〜5.0) × 指数1位。

    EV_proxy = win_probability × win_odds（win_probability が計算されている前提）。
    既知 ROI ≈ 1.19（3年バックテスト sweet_spot_recommendations.md）と整合を確認する。
    """
    bets: list[Bet] = []

    for race_id, grp in df.groupby("race_id"):
        # 指数1位を取得
        grp_sorted = grp.sort_values("composite_index", ascending=False)
        top1 = grp_sorted.iloc[0]

        horse_no = int(top1["horse_number"])
        win_odds = float(top1["win_odds"]) if pd.notna(top1["win_odds"]) else 0.0
        win_prob = float(top1["win_probability"]) if pd.notna(top1["win_probability"]) else 0.0

        # EV_proxy（win_probability が 0 の場合は 1/頭数で近似）
        if win_prob <= 0:
            head_count = len(grp)
            win_prob = 1.0 / head_count if head_count > 0 else 0.0

        ev = win_prob * win_odds

        # sweet_spot 条件
        if win_odds >= 10.0 and 1.2 <= ev <= 5.0:
            bets.append(
                Bet(
                    race_id=int(race_id),
                    bet_type="win",
                    combination=normalize_combination("win", [horse_no]),
                    stake=100,
                    tag="sweet_spot_win",
                )
            )

    return bets


def strategy_top3_trio_box(df: pd.DataFrame) -> list[Bet]:
    """戦略c: 全レース指数上位3頭三連複BOX ベタ買い。

    三連複の決済ロジック動作確認用。
    """
    bets: list[Bet] = []

    for race_id, grp in df.groupby("race_id"):
        if len(grp) < 3:
            continue
        top3 = grp.nlargest(3, "composite_index")
        horses = sorted(int(h) for h in top3["horse_number"].tolist())
        bets.append(
            Bet(
                race_id=int(race_id),
                bet_type="trio",
                combination=normalize_combination("trio", horses),
                stake=100,
                tag="top3_trio_box",
            )
        )

    return bets


# ---------------------------------------------------------------------------
# 戦略レジストリ
# ---------------------------------------------------------------------------

STRATEGIES: dict[str, callable] = {
    "favorite_win": strategy_favorite_win,
    "sweet_spot_win": strategy_sweet_spot_win,
    "top3_trio_box": strategy_top3_trio_box,
}


# ---------------------------------------------------------------------------
# 期間分割のパース
# ---------------------------------------------------------------------------


def parse_splits(splits_str: str) -> list[tuple[str, str, str]]:
    """'train:20230101-20250630,test:20250701-20260331' 形式をパースする。

    Returns:
        [(label, start_YYYYMMDD, end_YYYYMMDD), ...]
    """
    result: list[tuple[str, str, str]] = []
    for part in splits_str.split(","):
        label, dates = part.split(":")
        start_s, end_s = dates.split("-", 1)
        # YYYYMMDD → YYYYMMDD（そのまま）
        result.append((label.strip(), start_s.strip(), end_s.strip()))
    return result


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------


def main() -> None:
    """エントリーポイント。"""
    parser = argparse.ArgumentParser(
        description="ROI100 統一バックテスト CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--strategy",
        required=True,
        choices=list(STRATEGIES.keys()),
        help="実行する戦略名",
    )
    parser.add_argument("--start", required=True, help="開始日 YYYYMMDD")
    parser.add_argument("--end", required=True, help="終了日 YYYYMMDD")
    parser.add_argument(
        "--splits",
        default=None,
        help=(
            "期間分割 'label:YYYYMMDD-YYYYMMDD,...' 形式。"
            "例: 'train:20230101-20250630,test:20250701-20260331'"
        ),
    )
    parser.add_argument(
        "--no-filter",
        action="store_true",
        help="出走取消フィルタをスキップする（デバッグ用）",
    )
    parser.add_argument(
        "--bootstrap-n",
        type=int,
        default=10_000,
        help="ブートストラップ繰り返し回数（デフォルト: 10000）",
    )

    args = parser.parse_args()

    for d in (args.start, args.end):
        if len(d) != 8 or not d.isdigit():
            parser.error("日付は YYYYMMDD 形式で指定してください")

    logger.info(f"戦略: {args.strategy}  期間: {args.start}〜{args.end}")

    # データ取得
    df = load_race_data(args.start, args.end)
    if df.empty:
        logger.warning("データが取得できませんでした。日付・DBを確認してください。")
        sys.exit(1)

    if not args.no_filter:
        df = filter_valid_races(df)

    if df.empty:
        logger.warning("フィルタ後にデータがありません。")
        sys.exit(1)

    # Bet 生成
    strategy_fn = STRATEGIES[args.strategy]
    bets = strategy_fn(df)
    logger.info(f"生成ベット数: {len(bets):,}")

    if not bets:
        logger.warning("ベットが生成されませんでした。条件を確認してください。")
        sys.exit(1)

    # 期間分割のパース
    period_splits = None
    if args.splits:
        period_splits = parse_splits(args.splits)

    # 決済
    with Session(engine) as db:
        result = settle(
            bets,
            db.connection(),
            period_splits=period_splits,
            n_bootstrap=args.bootstrap_n,
        )

    # レポート出力
    print_settle_report(result, title=f"戦略: {args.strategy}  {args.start}〜{args.end}")

    # 健全性チェック（favorite_win は ROI が 0.75〜0.85 に収まるか）
    if args.strategy == "favorite_win":
        for ts in result.tag_summaries:
            if ts.bet_type == "win":
                roi = ts.roi
                if 0.75 <= roi <= 0.85:
                    print(f"✅ 健全性チェック合格: favorite_win ROI={roi:.3f} (0.75〜0.85)")
                else:
                    print(
                        f"⚠️  健全性チェック警告: favorite_win ROI={roi:.3f} "
                        f"(期待値 0.75〜0.85 外)"
                    )


if __name__ == "__main__":
    main()
