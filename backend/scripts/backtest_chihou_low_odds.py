"""地方競馬「単勝<2.0」帯の本命狙い条件 バックテスト

目的:
  「単勝オッズ<2.0」かつ「指数も支持」している本命を「信頼できる」、
  指数と人気が乖離しているものを「信頼できない」として 2 グループに分ける
  ためのスプリット条件を探索する。

評価指標:
  - 単勝ROI（of=単勝平均回収率） / 単勝的中率
  - 複勝的中率（参考、payout 集計は省略）

期間:
  train  : 〜 2025-09
  valid  : 2025-10〜2025-12
  test   : 2026-01〜

ROI陽性コースに限定する版／全コース版の両方を出力する。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv
load_dotenv(_root.parent / ".env")

import pandas as pd
import psycopg2

DSN = (
    f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
    f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
    f"password={os.getenv('DB_PASSWORD')}"
)

# 高オッズ版で陽性が確認されている 9 場（地方v10）
SWEET_SPOT_COURSES = {'浦和', '水沢', '笠松', '園田', '佐賀', '高知', '姫路', '盛岡', '門別'}

SQL = """
WITH base AS (
    SELECT
        r.date,
        r.id                              AS race_id,
        r.course_name,
        r.head_count,
        rr.horse_number,
        ci.composite_index,
        ci.win_probability,
        rr.win_odds::float                AS win_odds,
        rr.finish_position,
        rr.abnormality_code
    FROM chihou.calculated_indices ci
    JOIN chihou.races r ON r.id = ci.race_id
    JOIN chihou.race_entries re
        ON re.race_id = ci.race_id AND re.horse_id = ci.horse_id
    JOIN chihou.race_results rr
        ON rr.race_id = ci.race_id AND rr.horse_number = re.horse_number
    WHERE ci.version = 10
      AND r.course != '83'
      AND r.head_count >= 6
      AND rr.finish_position IS NOT NULL
      AND rr.win_odds IS NOT NULL
      AND rr.win_odds::float >= 1.0
      AND COALESCE(rr.abnormality_code, 0) = 0
),
ranked AS (
    SELECT *,
           RANK() OVER (PARTITION BY race_id ORDER BY composite_index DESC NULLS LAST) AS index_rank,
           RANK() OVER (PARTITION BY race_id ORDER BY win_odds ASC NULLS LAST)         AS pop_rank
    FROM base
)
SELECT * FROM ranked
ORDER BY date, race_id, horse_number
"""


def stats(df: pd.DataFrame) -> tuple[float, float, int]:
    """単勝ROI, 単勝的中率, 件数。"""
    if df.empty:
        return 0.0, 0.0, 0
    win_mask = df["finish_position"] == 1
    n = len(df)
    hits = int(win_mask.sum())
    returns = float(df.loc[win_mask, "win_odds"].sum())
    return returns / n, hits / n, n


def section(title: str) -> None:
    print(f"\n{'='*70}")
    print(f"  {title}")
    print('='*70)


def show(label: str, df: pd.DataFrame) -> None:
    r, hr, n = stats(df)
    print(f"  {label:<48} ROI {r:>6.3f}  hit {hr*100:>5.1f}%  n={n:>6,}")


def main() -> None:
    print("DB接続中...")
    conn = psycopg2.connect(DSN)
    df = pd.read_sql(SQL, conn)
    conn.close()

    df["date"] = df["date"].astype(str)
    df["ev"] = df["win_probability"] * df["win_odds"]

    train = df[df["date"] < "20251001"]
    valid = df[(df["date"] >= "20251001") & (df["date"] < "20260101")]
    test = df[df["date"] >= "20260101"]

    print(
        f"取得: {len(df):,}行 / "
        f"train {len(train):,} / valid {len(valid):,} / test {len(test):,}"
    )

    # =========================================================
    # 1. 単勝<2.0 単純抽出（人気1位かどうかを問わない）
    # =========================================================
    section("1. 単勝オッズ<2.0 単純抽出（人気・指数問わず）")
    fav = df[df["win_odds"] < 2.0]
    show("全コース", fav)
    show("ROI陽性9コースのみ", fav[fav["course_name"].isin(SWEET_SPOT_COURSES)])
    show("test (2026-01〜)", fav[fav["date"] >= "20260101"])

    # =========================================================
    # 2. オッズ帯別 ROI（<2.0 を細分化）
    # =========================================================
    section("2. 単勝オッズ帯別 ROI / 的中率（全コース・全期間）")
    bands = [(1.0, 1.2), (1.2, 1.4), (1.4, 1.6), (1.6, 1.8), (1.8, 2.0)]
    for lo, hi in bands:
        sub = df[(df["win_odds"] >= lo) & (df["win_odds"] < hi)]
        show(f"単勝 {lo:.1f}-{hi:.1f}", sub)

    # =========================================================
    # 3. 信頼/不信頼 分割案（v10 指数1位 ∧ EV しきい値）
    # =========================================================
    section("3. 信頼/不信頼 分割案 (単勝<2.0)")
    print("  分割軸:")
    print("    A: index_rank == 1 (v10で1位)")
    print("    B: index_rank in (1,2) (v10で1〜2位)")
    print("    EV しきい値: 0.85 / 1.0")

    fav = df[df["win_odds"] < 2.0]
    for axis_name, axis_mask in [
        ("index1位",        fav["index_rank"] == 1),
        ("index1〜2位",     fav["index_rank"] <= 2),
    ]:
        for ev_th in (0.85, 1.0):
            trust_mask = axis_mask & (fav["ev"] >= ev_th)
            untrust_mask = ~trust_mask
            show(
                f"信頼  ({axis_name} ∧ EV≥{ev_th})",
                fav[trust_mask],
            )
            show(
                f"不信頼 (上記以外)",
                fav[untrust_mask],
            )
            print()

    # =========================================================
    # 4. ROI陽性9コース限定での 3 と同じ分割
    # =========================================================
    section("4. 信頼/不信頼 分割案 (単勝<2.0 ∧ ROI陽性9場)")
    fav_pos = df[(df["win_odds"] < 2.0) & (df["course_name"].isin(SWEET_SPOT_COURSES))]
    for axis_name, axis_mask in [
        ("index1位",        fav_pos["index_rank"] == 1),
        ("index1〜2位",     fav_pos["index_rank"] <= 2),
    ]:
        for ev_th in (0.85, 1.0):
            trust_mask = axis_mask & (fav_pos["ev"] >= ev_th)
            untrust_mask = ~trust_mask
            show(
                f"信頼  ({axis_name} ∧ EV≥{ev_th})",
                fav_pos[trust_mask],
            )
            show(
                f"不信頼 (上記以外)",
                fav_pos[untrust_mask],
            )
            print()

    # =========================================================
    # 4b. index_rank 別に詳細（単勝<2.0 ∧ 全コース）
    # =========================================================
    section("4b. index_rank 別 (単勝<2.0、全コース・全期間)")
    fav_all = df[df["win_odds"] < 2.0]
    for rk in [1, 2, 3, 4, 5]:
        sub = fav_all[fav_all["index_rank"] == rk]
        show(f"index_rank == {rk}", sub)
    show("index_rank >= 4 (指数下位の本命)", fav_all[fav_all["index_rank"] >= 4])

    section("4c. オッズ × index_rank マトリクス (全期間)")
    for olo, ohi in [(1.0, 1.5), (1.5, 2.0)]:
        for rk_lo, rk_hi, label in [
            (1, 1, "indexで1位"),
            (2, 3, "indexで2-3位"),
            (4, 99, "indexで4位以下"),
        ]:
            sub = fav_all[
                (fav_all["win_odds"] >= olo)
                & (fav_all["win_odds"] < ohi)
                & (fav_all["index_rank"] >= rk_lo)
                & (fav_all["index_rank"] <= rk_hi)
            ]
            show(f"単勝 {olo:.1f}-{ohi:.1f} ∧ {label}", sub)
        print()

    # =========================================================
    # 5. test 期間の検証（単勝<2.0 ∧ 全コース ∧ index1位 ∧ EV≥0.9）
    # =========================================================
    section("5. test 期間サマリ")
    for axis_name, axis_mask in [
        ("index1位",        test["index_rank"] == 1),
        ("index1〜2位",     test["index_rank"] <= 2),
    ]:
        for ev_th in (0.85, 1.0):
            tf = test[test["win_odds"] < 2.0]
            ax = axis_mask.reindex(tf.index, fill_value=False)
            trust = tf[ax & (tf["ev"] >= ev_th)]
            untrust = tf[~(ax & (tf["ev"] >= ev_th))]
            show(f"信頼   ({axis_name} ∧ EV≥{ev_th}) test", trust)
            show(f"不信頼 (上記以外) test", untrust)
            print()


if __name__ == "__main__":
    main()
