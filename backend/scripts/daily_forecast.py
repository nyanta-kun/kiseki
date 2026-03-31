"""当日レース予想出力スクリプト

指定日（デフォルト: 本日）の全レースについて、算出済み指数ランキングと
期待値（EV）を競馬新聞スタイルでターミナルへ出力する。

機能:
  - 指数1位馬を本命（◎）として表示
  - anagusa指数が高い馬を穴ぐさ候補（☆）としてハイライト
  - 期待値 EV = (単勝オッズ × 指数ベース勝率推定) を表示
  - 過去レース（結果あり）は実際の着順も併記
  - --min-ev でEV閾値以上のみを絞り込み表示

使い方:
  python scripts/daily_forecast.py --date 20260212
  python scripts/daily_forecast.py --date 20260212 --min-ev 1.20
  python scripts/daily_forecast.py --date 20260212 --race 11
  python scripts/daily_forecast.py --date 20260212 --top-only
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv

load_dotenv(_root.parent / ".env")

logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("daily_forecast")

from src.db.session import engine
from src.indices.composite import COMPOSITE_VERSION
from src.utils.constants import EV_BUY_THRESHOLD, JRA_TAKEOUT_RATE

# 穴ぐさ候補の閾値（指数差）
ANAGUSA_HIGHLIGHT_SCORE = 58.0
# EV 表示記号の閾値
EV_BUY = EV_BUY_THRESHOLD  # 1.20
EV_HOLD = 1.00

_QUERY = text("""
SELECT
    r.id              AS race_id,
    r.race_number     AS race_number,
    r.race_name       AS race_name,
    r.course_name     AS course_name,
    r.surface         AS surface,
    r.distance        AS distance,
    r.grade           AS grade,
    r.head_count      AS head_count,
    r.condition       AS condition,
    re.horse_number   AS horse_number,
    h.name            AS horse_name,
    j.name            AS jockey_name,
    re.weight_carried AS weight_carried,
    ci.composite_index    AS composite_index,
    ci.speed_index        AS speed_index,
    ci.course_aptitude    AS course_aptitude,
    ci.pace_index         AS pace_index,
    ci.anagusa_index      AS anagusa_index,
    ci.paddock_index      AS paddock_index,
    rr.finish_position    AS finish_position,
    rr.win_odds           AS win_odds,
    rr.win_popularity     AS win_popularity
FROM keiba.races r
JOIN keiba.race_entries re ON re.race_id = r.id
JOIN keiba.horses h ON h.id = re.horse_id
JOIN keiba.jockeys j ON j.id = re.jockey_id
JOIN keiba.calculated_indices ci
    ON ci.race_id = r.id AND ci.horse_id = re.horse_id AND ci.version = :version
LEFT JOIN keiba.race_results rr
    ON rr.race_id = r.id AND rr.horse_id = re.horse_id
WHERE r.date = :race_date
  AND (:race_number = 0 OR r.race_number = :race_number)
ORDER BY r.course_name, r.race_number, ci.composite_index DESC
""")


def load_forecast(
    race_date: str,
    race_number: int = 0,
    version: int = COMPOSITE_VERSION,
) -> pd.DataFrame:
    """指定日のレースデータを取得する。"""
    with Session(engine) as db:
        result = db.execute(
            _QUERY,
            {"race_date": race_date, "race_number": race_number, "version": version},
        )
        rows = result.fetchall()
        cols = list(result.keys())

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=cols)
    for col in [
        "composite_index",
        "speed_index",
        "course_aptitude",
        "pace_index",
        "anagusa_index",
        "paddock_index",
        "finish_position",
        "win_odds",
        "win_popularity",
    ]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _estimate_win_prob(df_race: pd.DataFrame) -> pd.Series:
    """指数から勝率を推定する（softmax 近似）。

    composite_index を指数関数でスコア化し、全馬で正規化。
    スケールパラメータ τ=10 はキャリブレーション前の暫定値。
    """
    tau = 10.0
    scores = df_race["composite_index"].fillna(50.0)
    exp_s = (scores / tau).apply(lambda x: 2.718281828**x)
    return exp_s / exp_s.sum()


def _ev(odds: float, prob: float) -> float:
    """期待値 = オッズ × (1 - 控除率) × 勝率推定。"""
    return odds * (1.0 - JRA_TAKEOUT_RATE) * prob


def _surface_label(surface: str | None) -> str:
    if not surface:
        return "?"
    if surface.startswith("芝"):
        return "芝"
    if surface.startswith("ダ"):
        return "ダ"
    return surface[:2]


def _grade_label(grade: str | None) -> str:
    if not grade:
        return "   "
    g = str(grade).strip()
    if g in ("G1", "G2", "G3"):
        return f"[{g}]"
    return "   "


def _finish_label(pos: float | None) -> str:
    if pos is None or pd.isna(pos):
        return "  "
    p = int(pos)
    if p == 1:
        return "1着"
    if p == 2:
        return "2着"
    if p == 3:
        return "3着"
    return f"{p}着"


def _ev_label(ev: float, has_odds: bool) -> str:
    if not has_odds:
        return "   N/A"
    if ev >= EV_BUY:
        return f"★{ev:4.2f}"
    if ev >= EV_HOLD:
        return f" {ev:4.2f}"
    return f" {ev:4.2f}"


def print_race(df_race: pd.DataFrame, top_only: bool = False, min_ev: float = 0.0) -> None:
    """1レース分の予想を出力する。"""
    row0 = df_race.iloc[0]
    course = str(row0["course_name"]) if pd.notna(row0["course_name"]) else "?"
    rno = int(row0["race_number"])
    rname = str(row0["race_name"]) if pd.notna(row0["race_name"]) else ""
    surf = _surface_label(row0["surface"])
    dist = int(row0["distance"]) if pd.notna(row0["distance"]) else 0
    grade = _grade_label(row0["grade"])
    hcount = int(row0["head_count"]) if pd.notna(row0["head_count"]) else len(df_race)
    cond = str(row0["condition"]) if pd.notna(row0["condition"]) else ""
    has_result = df_race["finish_position"].notna().any()
    has_odds = df_race["win_odds"].notna().any()

    # 勝率推定 → EV 算出
    probs = _estimate_win_prob(df_race)
    df_race = df_race.copy()
    df_race["_prob"] = probs.values
    df_race["_ev"] = df_race.apply(
        lambda r: (
            _ev(float(r["win_odds"]), r["_prob"]) if pd.notna(r["win_odds"]) else float("nan")
        ),
        axis=1,
    )

    # min_ev フィルタ適用時: EV条件を満たす馬がいなければスキップ
    if min_ev > 0:
        has_ev_candidate = (df_race["_ev"] >= min_ev).any()
        if not has_ev_candidate:
            return

    print(f"\n{'━' * 70}")
    print(f"  {course} R{rno:02d}  {rname}{grade}  {surf} {dist}m  {hcount}頭  {cond}")
    print(f"{'━' * 70}")

    header = (
        f"  {'印':2}  {'馬番':3}  {'馬名':<16}  {'騎手':<8}  "
        f"{'斤量':4}  {'総合':5}  {'SP':5}  {'適性':5}  "
        f"{'展開':5}  {'穴':5}  {'EV':6}"
    )
    if has_result:
        header += f"  {'着順':4}"
    if has_odds:
        header += f"  {'人気':3}"
    print(header)
    print(f"  {'─' * 67}")

    top_idx = df_race["composite_index"].idxmax()

    for rank, (idx, r) in enumerate(df_race.iterrows(), 1):
        if top_only and rank > 3:
            break

        # 印
        is_top = idx == top_idx
        is_anagusa = (
            pd.notna(r["anagusa_index"])
            and r["anagusa_index"] >= ANAGUSA_HIGHLIGHT_SCORE
            and not is_top
        )
        mark = "◎" if is_top else ("☆" if is_anagusa else "  ")

        comp = f"{r['composite_index']:5.1f}" if pd.notna(r["composite_index"]) else "  N/A"
        sp = f"{r['speed_index']:5.1f}" if pd.notna(r["speed_index"]) else "  N/A"
        apt = f"{r['course_aptitude']:5.1f}" if pd.notna(r["course_aptitude"]) else "  N/A"
        pace = f"{r['pace_index']:5.1f}" if pd.notna(r["pace_index"]) else "  N/A"
        ana = f"{r['anagusa_index']:5.1f}" if pd.notna(r["anagusa_index"]) else "  N/A"
        wc = f"{float(r['weight_carried']):4.1f}" if pd.notna(r["weight_carried"]) else " N/A"
        ev_s = _ev_label(r["_ev"], has_odds and pd.notna(r["win_odds"]))

        # min_ev フィルタ
        if min_ev > 0 and not is_top and r["_ev"] < min_ev:
            continue

        line = (
            f"  {mark:2}  {int(r['horse_number']):3}  "
            f"{str(r['horse_name']):<16}  {str(r['jockey_name']):<8}  "
            f"{wc}  {comp}  {sp}  {apt}  {pace}  {ana}  {ev_s}"
        )
        if has_result:
            line += f"  {_finish_label(r['finish_position']):4}"
        if has_odds and pd.notna(r["win_odds"]):
            line += (
                f"  {int(r['win_popularity']):3}人" if pd.notna(r["win_popularity"]) else "     "
            )
        print(line)

    # EV購入候補サマリー
    candidates = df_race[df_race["_ev"] >= EV_BUY]
    if not candidates.empty and has_odds:
        names = "・".join(
            f"{int(r['horse_number'])}番{r['horse_name']}(EV{r['_ev']:.2f})"
            for _, r in candidates.iterrows()
        )
        print(f"\n  【購入候補 EV≥{EV_BUY}】{names}")


def run(
    race_date: str,
    race_number: int = 0,
    top_only: bool = False,
    min_ev: float = 0.0,
    version: int = COMPOSITE_VERSION,
) -> None:
    """指定日の全レース予想を出力する。"""
    df = load_forecast(race_date, race_number, version)
    if df.empty:
        print(f"[{race_date}] 算出済みデータがありません（version={version}）。")
        return

    n_races = df["race_id"].nunique()
    n_horses = len(df)
    print(f"\n{'=' * 70}")
    print(f"  {race_date}  指数予想レポート  ({n_races}レース / {n_horses}頭)  v{version}")
    print(f"{'=' * 70}")
    print(
        f"  凡例: ◎本命  ☆穴ぐさ候補(指数{ANAGUSA_HIGHLIGHT_SCORE:.0f}+)  "
        f"★EV≥{EV_BUY}(購入候補)  EV=期待値"
    )

    for race_id, df_race in df.groupby("race_id", sort=False):
        print_race(df_race, top_only=top_only, min_ev=min_ev)

    print(f"\n{'=' * 70}")


def main() -> None:
    parser = argparse.ArgumentParser(description="当日レース指数予想出力")
    parser.add_argument("--date", required=True, help="対象日 YYYYMMDD")
    parser.add_argument("--race", type=int, default=0, help="レース番号（0=全レース）")
    parser.add_argument("--top-only", action="store_true", help="各レースTop3のみ表示")
    parser.add_argument(
        "--min-ev",
        type=float,
        default=0.0,
        help=f"この期待値以上の馬がいるレースのみ表示（例: {EV_BUY_THRESHOLD}）",
    )
    parser.add_argument(
        "--version",
        type=int,
        default=COMPOSITE_VERSION,
        help=f"算出バージョン (default: {COMPOSITE_VERSION})",
    )
    args = parser.parse_args()

    if len(args.date) != 8 or not args.date.isdigit():
        parser.error("日付は YYYYMMDD 形式で指定してください")

    run(args.date, args.race, args.top_only, args.min_ev, args.version)


if __name__ == "__main__":
    main()
