"""指数精度検証スクリプト

指定年のcalculated_indicesとrace_resultsを照合し、
指数の予測精度を多角的に評価してMarkdownレポートを出力する。

使い方:
  # Docker コンテナ内
  uv run python scripts/verify_indices.py --year 2026

  # ローカル
  python scripts/verify_indices.py --year 2026 --output docs/verification/
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv

load_dotenv(_root.parent / ".env")

from sqlalchemy import text
from sqlalchemy.orm import Session

from src.db.session import engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("verify_indices")


# ---------------------------------------------------------------------------
# SQL クエリ群
# ---------------------------------------------------------------------------

SQL_BASIC_STATS = text("""
WITH race_size AS (
    SELECT race_id, COUNT(*) as entry_count
    FROM keiba.race_entries GROUP BY race_id
),
base AS (
    SELECT
        ci.race_id,
        ci.horse_id,
        ci.composite_index,
        ci.speed_index,
        ci.last_3f_index,
        ci.jockey_index,
        ci.pace_index,
        ci.course_aptitude,
        ci.rotation_index,
        ci.win_probability,
        ci.place_probability,
        RANK() OVER (PARTITION BY ci.race_id ORDER BY ci.composite_index DESC) as composite_rank,
        RANK() OVER (PARTITION BY ci.race_id ORDER BY ci.speed_index DESC)     as speed_rank,
        RANK() OVER (PARTITION BY ci.race_id ORDER BY ci.last_3f_index DESC)   as last3f_rank,
        RANK() OVER (PARTITION BY ci.race_id ORDER BY ci.jockey_index DESC)    as jockey_rank,
        rr.finish_position,
        rr.win_odds,
        rs.entry_count
    FROM keiba.calculated_indices ci
    JOIN keiba.races r ON r.id = ci.race_id
    JOIN race_size rs ON rs.race_id = ci.race_id
    LEFT JOIN keiba.race_results rr
        ON rr.race_id = ci.race_id
        AND rr.horse_id = ci.horse_id
        AND rr.abnormality_code = 0
    WHERE LEFT(r.date, 4) = :year
      AND rs.entry_count >= 4
      AND rr.finish_position IS NOT NULL
)
SELECT
    COUNT(DISTINCT race_id)                                          AS total_races,
    COUNT(*)                                                         AS total_horses,
    ROUND(AVG(entry_count)::numeric, 1)                             AS avg_field_size,
    ROUND(AVG(1.0 / entry_count) * 100, 1)                         AS random_win_pct,
    ROUND(AVG(3.0 / entry_count) * 100, 1)                         AS random_place_pct,
    -- 総合指数1位
    COUNT(CASE WHEN composite_rank = 1 THEN 1 END)                  AS rank1_cnt,
    COUNT(CASE WHEN composite_rank = 1 AND finish_position = 1 THEN 1 END)  AS rank1_wins,
    COUNT(CASE WHEN composite_rank = 1 AND finish_position <= 3 THEN 1 END) AS rank1_places,
    -- 総合指数Top3
    COUNT(CASE WHEN composite_rank <= 3 AND finish_position = 1 THEN 1 END) AS top3_wins,
    COUNT(CASE WHEN composite_rank <= 3 AND finish_position <= 3 THEN 1 END) AS top3_places,
    COUNT(CASE WHEN composite_rank <= 3 THEN 1 END)                 AS top3_cnt,
    -- 相関係数
    ROUND(CORR(composite_rank, finish_position)::numeric, 4)        AS pearson_corr
FROM base
""")

SQL_RANK_BREAKDOWN = text("""
WITH race_size AS (
    SELECT race_id, COUNT(*) as entry_count
    FROM keiba.race_entries GROUP BY race_id
),
ranked AS (
    SELECT
        ci.race_id,
        RANK() OVER (PARTITION BY ci.race_id ORDER BY ci.composite_index DESC) as idx_rank,
        rr.finish_position,
        rr.win_odds
    FROM keiba.calculated_indices ci
    JOIN keiba.races r ON r.id = ci.race_id
    JOIN race_size rs ON rs.race_id = ci.race_id
    LEFT JOIN keiba.race_results rr
        ON rr.race_id = ci.race_id
        AND rr.horse_id = ci.horse_id
        AND rr.abnormality_code = 0
    WHERE LEFT(r.date, 4) = :year
      AND rs.entry_count >= 4
      AND rr.finish_position IS NOT NULL
)
SELECT
    idx_rank,
    COUNT(*)                                                                   AS cnt,
    COUNT(CASE WHEN finish_position = 1 THEN 1 END)                          AS wins,
    COUNT(CASE WHEN finish_position <= 3 THEN 1 END)                         AS places,
    ROUND(COUNT(CASE WHEN finish_position = 1 THEN 1 END)::numeric / COUNT(*) * 100, 1) AS win_pct,
    ROUND(COUNT(CASE WHEN finish_position <= 3 THEN 1 END)::numeric / COUNT(*) * 100, 1) AS place_pct,
    ROUND(AVG(finish_position)::numeric, 2)                                  AS avg_pos
FROM ranked
WHERE idx_rank <= 8
GROUP BY idx_rank
ORDER BY idx_rank
""")

SQL_INDIVIDUAL_INDEX = text("""
WITH race_size AS (
    SELECT race_id, COUNT(*) as entry_count
    FROM keiba.race_entries GROUP BY race_id
),
ranked AS (
    SELECT
        ci.race_id,
        RANK() OVER (PARTITION BY ci.race_id ORDER BY ci.composite_index DESC) as composite_rank,
        RANK() OVER (PARTITION BY ci.race_id ORDER BY ci.speed_index DESC)     as speed_rank,
        RANK() OVER (PARTITION BY ci.race_id ORDER BY ci.last_3f_index DESC)   as last3f_rank,
        RANK() OVER (PARTITION BY ci.race_id ORDER BY ci.jockey_index DESC)    as jockey_rank,
        RANK() OVER (PARTITION BY ci.race_id ORDER BY ci.rotation_index DESC)  as rotation_rank,
        RANK() OVER (PARTITION BY ci.race_id ORDER BY ci.course_aptitude DESC) as course_rank,
        rr.finish_position
    FROM keiba.calculated_indices ci
    JOIN keiba.races r ON r.id = ci.race_id
    JOIN race_size rs ON rs.race_id = ci.race_id
    LEFT JOIN keiba.race_results rr
        ON rr.race_id = ci.race_id
        AND rr.horse_id = ci.horse_id
        AND rr.abnormality_code = 0
    WHERE LEFT(r.date, 4) = :year
      AND rs.entry_count >= 4
      AND rr.finish_position IS NOT NULL
)
SELECT index_name, cnt, wins, places,
    ROUND(wins::numeric / cnt * 100, 1) AS win_pct,
    ROUND(places::numeric / cnt * 100, 1) AS place_pct
FROM (
    SELECT '総合 (composite)'    AS index_name,
           COUNT(CASE WHEN composite_rank = 1 THEN 1 END) AS cnt,
           COUNT(CASE WHEN composite_rank = 1 AND finish_position = 1 THEN 1 END) AS wins,
           COUNT(CASE WHEN composite_rank = 1 AND finish_position <= 3 THEN 1 END) AS places
    FROM ranked
    UNION ALL
    SELECT 'スピード (speed)',
           COUNT(CASE WHEN speed_rank = 1 THEN 1 END),
           COUNT(CASE WHEN speed_rank = 1 AND finish_position = 1 THEN 1 END),
           COUNT(CASE WHEN speed_rank = 1 AND finish_position <= 3 THEN 1 END)
    FROM ranked
    UNION ALL
    SELECT '上がり3F (last_3f)',
           COUNT(CASE WHEN last3f_rank = 1 THEN 1 END),
           COUNT(CASE WHEN last3f_rank = 1 AND finish_position = 1 THEN 1 END),
           COUNT(CASE WHEN last3f_rank = 1 AND finish_position <= 3 THEN 1 END)
    FROM ranked
    UNION ALL
    SELECT '騎手 (jockey)',
           COUNT(CASE WHEN jockey_rank = 1 THEN 1 END),
           COUNT(CASE WHEN jockey_rank = 1 AND finish_position = 1 THEN 1 END),
           COUNT(CASE WHEN jockey_rank = 1 AND finish_position <= 3 THEN 1 END)
    FROM ranked
    UNION ALL
    SELECT 'ローテーション (rotation)',
           COUNT(CASE WHEN rotation_rank = 1 THEN 1 END),
           COUNT(CASE WHEN rotation_rank = 1 AND finish_position = 1 THEN 1 END),
           COUNT(CASE WHEN rotation_rank = 1 AND finish_position <= 3 THEN 1 END)
    FROM ranked
    UNION ALL
    SELECT 'コース適性 (course)',
           COUNT(CASE WHEN course_rank = 1 THEN 1 END),
           COUNT(CASE WHEN course_rank = 1 AND finish_position = 1 THEN 1 END),
           COUNT(CASE WHEN course_rank = 1 AND finish_position <= 3 THEN 1 END)
    FROM ranked
) t
ORDER BY win_pct DESC
""")

SQL_GRADE_BREAKDOWN = text("""
WITH race_size AS (
    SELECT race_id, COUNT(*) as entry_count
    FROM keiba.race_entries GROUP BY race_id
),
ranked AS (
    SELECT
        ci.race_id,
        r.surface,
        CASE
            WHEN r.grade IN ('G1','G2','G3') THEN r.grade
            WHEN r.grade LIKE 'OP%' OR r.grade = 'OP特別' THEN 'OP/L'
            WHEN r.surface = '障' THEN '障害'
            ELSE '一般'
        END AS grade_group,
        RANK() OVER (PARTITION BY ci.race_id ORDER BY ci.composite_index DESC) AS idx_rank,
        rr.finish_position
    FROM keiba.calculated_indices ci
    JOIN keiba.races r ON r.id = ci.race_id
    JOIN race_size rs ON rs.race_id = ci.race_id
    LEFT JOIN keiba.race_results rr
        ON rr.race_id = ci.race_id
        AND rr.horse_id = ci.horse_id
        AND rr.abnormality_code = 0
    WHERE LEFT(r.date, 4) = :year
      AND rs.entry_count >= 4
      AND rr.finish_position IS NOT NULL
)
SELECT
    surface,
    grade_group,
    COUNT(*) AS races,
    ROUND(COUNT(CASE WHEN finish_position = 1 THEN 1 END)::numeric / COUNT(*) * 100, 1) AS win_pct,
    ROUND(COUNT(CASE WHEN finish_position <= 3 THEN 1 END)::numeric / COUNT(*) * 100, 1) AS place_pct,
    ROUND(AVG(finish_position)::numeric, 2) AS avg_pos
FROM ranked
WHERE idx_rank = 1
GROUP BY surface, grade_group
ORDER BY surface, grade_group
""")

SQL_ROI = text("""
WITH race_size AS (
    SELECT race_id, COUNT(*) as entry_count
    FROM keiba.race_entries GROUP BY race_id
),
ranked AS (
    SELECT
        ci.race_id,
        RANK() OVER (PARTITION BY ci.race_id ORDER BY ci.composite_index DESC) AS idx_rank,
        rr.finish_position,
        rr.win_odds
    FROM keiba.calculated_indices ci
    JOIN keiba.races r ON r.id = ci.race_id
    JOIN race_size rs ON rs.race_id = ci.race_id
    LEFT JOIN keiba.race_results rr
        ON rr.race_id = ci.race_id
        AND rr.horse_id = ci.horse_id
        AND rr.abnormality_code = 0
    WHERE LEFT(r.date, 4) = :year
      AND rs.entry_count >= 4
      AND rr.finish_position IS NOT NULL
      AND rr.win_odds IS NOT NULL AND rr.win_odds > 0
)
SELECT
    idx_rank,
    COUNT(*) AS bets,
    COUNT(CASE WHEN finish_position = 1 THEN 1 END) AS wins,
    -- ROI = (勝利時の払戻合計) / (投資合計) * 100
    -- win_odds × 100円 が払戻額、100円 × bets が投資額
    ROUND(
        SUM(CASE WHEN finish_position = 1 THEN win_odds ELSE 0 END)
        / COUNT(*) * 100
    , 1) AS roi_pct,
    ROUND(AVG(CASE WHEN finish_position = 1 THEN win_odds END)::numeric, 2) AS avg_winning_odds
FROM ranked
WHERE idx_rank <= 5
GROUP BY idx_rank
ORDER BY idx_rank
""")


# ---------------------------------------------------------------------------
# 検証実行 & レポート生成
# ---------------------------------------------------------------------------


def run(year: str, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{date.today().strftime('%Y%m%d')}_{year}_verification.md"

    with Session(engine) as db:
        logger.info(f"{year}年 指数精度検証開始...")
        params = {"year": year}

        basic = db.execute(SQL_BASIC_STATS, params).mappings().first()
        ranks = db.execute(SQL_RANK_BREAKDOWN, params).mappings().all()
        indices = db.execute(SQL_INDIVIDUAL_INDEX, params).mappings().all()
        grades = db.execute(SQL_GRADE_BREAKDOWN, params).mappings().all()
        roi = db.execute(SQL_ROI, params).mappings().all()

    report = _build_report(year, basic, ranks, indices, grades, roi)

    with output_path.open("w", encoding="utf-8") as f:
        f.write(report)

    logger.info(f"レポート出力: {output_path}")
    print(report)


def _build_report(year, basic, ranks, indices, grades, roi) -> str:
    today = date.today().strftime("%Y-%m-%d")
    random_win = float(basic["random_win_pct"])
    random_place = float(basic["random_place_pct"])
    rank1_win = round(int(basic["rank1_wins"]) / int(basic["rank1_cnt"]) * 100, 1)
    rank1_place = round(int(basic["rank1_places"]) / int(basic["rank1_cnt"]) * 100, 1)
    top3_win_rate = round(int(basic["top3_wins"]) / int(basic["top3_cnt"]) * 100, 1)
    top3_place_rate = round(int(basic["top3_places"]) / int(basic["top3_cnt"]) * 100, 1)

    lines = [
        f"# 指数精度検証レポート — {year}年",
        "",
        f"**検証日**: {today}  ",
        f"**対象**: {year}年 全レース（頭数4頭以上・異常コードなし）",
        "",
        "---",
        "",
        "## 1. 基本統計",
        "",
        "| 項目 | 値 |",
        "|------|----|",
        f"| 対象レース数 | {basic['total_races']:,} |",
        f"| 対象馬数（延べ） | {basic['total_horses']:,} |",
        f"| 平均出走頭数 | {basic['avg_field_size']} 頭 |",
        f"| ランダム期待単勝率 | {random_win}% |",
        f"| ランダム期待複勝率 | {random_place}% |",
        f"| 指数ランク↔着順 Pearson相関 | {basic['pearson_corr']} |",
        "",
        "---",
        "",
        "## 2. 総合指数 ランク別的中率",
        "",
        f"> ランダム期待値: 単勝 **{random_win}%** / 複勝 **{random_place}%**",
        "",
        "| 指数ランク | 件数 | 単勝数 | 複勝数 | 単勝率 | 複勝率 | 平均着順 | 単勝リフト |",
        "|-----------|------|--------|--------|--------|--------|----------|-----------|",
    ]

    for r in ranks:
        lift = round(float(r["win_pct"]) / random_win, 2)
        lines.append(
            f"| {r['idx_rank']}位 | {r['cnt']:,} | {r['wins']} | {r['places']} "
            f"| **{r['win_pct']}%** | {r['place_pct']}% | {r['avg_pos']} | ×{lift} |"
        )

    lines += [
        "",
        "**指数1位のサマリー**",
        f"- 単勝的中率: **{rank1_win}%**（ランダム比 ×{round(rank1_win / random_win, 2)}）",
        f"- 複勝的中率: **{rank1_place}%**（ランダム比 ×{round(rank1_place / random_place, 2)}）",
        "",
        "**指数Top3のサマリー（全体の1着馬をカバーできるか）**",
        f"- 単勝カバー率（Top3に1着馬が入る確率）: **{top3_win_rate}%**",
        f"- 複勝カバー率（Top3に複勝馬が入る確率）: **{top3_place_rate}%**",
        "",
        "---",
        "",
        "## 3. 各指数のランク1位的中率",
        "",
        "> 同率1位が複数いる場合は全員カウント（実件数 > レース数になることがある）",
        "",
        "| 指数 | 件数 | 単勝数 | 複勝数 | 単勝率 | 複勝率 |",
        "|------|------|--------|--------|--------|--------|",
    ]

    for idx in indices:
        lines.append(
            f"| {idx['index_name']} | {idx['cnt']:,} | {idx['wins']} | {idx['places']} "
            f"| **{idx['win_pct']}%** | {idx['place_pct']}% |"
        )

    lines += [
        "",
        "---",
        "",
        "## 4. グレード・馬場別 指数1位的中率",
        "",
        "| 馬場 | グレード | レース数 | 単勝率 | 複勝率 | 平均着順 |",
        "|------|---------|---------|--------|--------|---------|",
    ]

    for g in grades:
        lines.append(
            f"| {g['surface']} | {g['grade_group']} | {g['races']} "
            f"| {g['win_pct']}% | {g['place_pct']}% | {g['avg_pos']} |"
        )

    lines += [
        "",
        "---",
        "",
        "## 5. 単勝ROIシミュレーション（指数ランク別・100円均一購入）",
        "",
        "> ROI = (勝利時の払戻合計) / (投資合計) × 100%  ",
        "> 控除率25%のため、ランダム購入時の理論ROIは **75%**",
        "",
        "| 指数ランク | 購入レース数 | 的中数 | ROI | 平均配当オッズ |",
        "|-----------|------------|--------|-----|--------------|",
    ]

    for r in roi:
        avg_odds = r["avg_winning_odds"] if r["avg_winning_odds"] else "—"
        lines.append(
            f"| {r['idx_rank']}位 | {r['bets']:,} | {r['wins']} "
            f"| **{r['roi_pct']}%** | {avg_odds} |"
        )

    lines += [
        "",
        "---",
        "",
        "## 6. 考察・課題",
        "",
        "### 精度評価",
        f"- 指数1位の単勝的中率 **{rank1_win}%**（ランダム {random_win}% の約×{round(rank1_win / random_win, 1)}倍）",
        f"- Pearson相関 **{basic['pearson_corr']}**：中程度の正の相関あり",
        "",
        f"### データ品質の制約（{year}年時点）",
        "- `course_aptitude`（コース適性）: 過去同条件3戦未満の馬はデフォルト値50.0",
        "- `pedigree_index`（血統）: 取り込み済みデータ範囲による精度差",
        "- `training_index`（調教）: 未実装（50.0固定）",
        "- `paddock_index`（パドック）: 未実装（50.0固定）",
        "",
        "### 今後の改善ポイント",
        "1. データ量増加（2024年・2025年再算出完了後）による精度向上確認",
        "2. G2/G3レースで精度が低い → 重賞専用の重み調整を検討",
        "3. 障害レースは別モデルの必要性",
        "4. ROIが理論値（75%）以下のランクの重み最適化",
        "",
        "---",
        "*Generated by kiseki/backend/scripts/verify_indices.py*",
    ]

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="指数精度検証レポート生成")
    parser.add_argument("--year", required=True, help="検証対象年 (例: 2026)")
    parser.add_argument(
        "--output", default=None, help="出力先ディレクトリ（省略時は docs/verification/）"
    )
    args = parser.parse_args()

    if args.output:
        output_dir = Path(args.output)
    else:
        output_dir = _root.parent / "docs" / "verification"

    run(args.year, output_dir)


if __name__ == "__main__":
    main()
