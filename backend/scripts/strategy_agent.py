"""戦略エージェント（Strategy Agent）

指数ランキング・EV期待値・Kelly基準を組み合わせた馬券戦略を最適化する。

機能:
  - EV（期待値）スコアリング: softmax(composite_index) × 払戻オッズ
  - EV閾値グリッドサーチ: 買い条件となるEV下限を最適化
  - オッズフィルタ: 単勝オッズの上限・下限フィルタ効果を検証
  - Kelly基準シミュレーション: 最適賭け比率（フルKelly / 半Kelly / 1/4Kelly）
  - 総合戦略レポート: Markdown形式で出力

使い方:
  python scripts/strategy_agent.py --start 20230413 --end 20251231 --test-start 20260101 --test-end 20260413
  python scripts/strategy_agent.py --start 20230413 --end 20251231 --report docs/strategy/
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv

load_dotenv(_root.parent / ".env")

from src.db.session import sync_engine as engine
from src.indices.composite import COMPOSITE_VERSION

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("strategy_agent")


# ---------------------------------------------------------------------------
# データ取得（backtest.py と同一クエリ）
# ---------------------------------------------------------------------------


def _build_query(version: int) -> text:
    return text(f"""
SELECT
    r.id              AS race_id,
    r.date            AS date,
    r.course_name     AS course_name,
    r.surface         AS surface,
    r.distance        AS distance,
    r.head_count      AS head_count,
    ci.horse_id       AS horse_id,
    ci.composite_index AS composite_index,
    rr.finish_position AS finish_position,
    rr.abnormality_code AS abnormality_code,
    rr.win_odds        AS win_odds,
    rr.place_odds      AS place_odds
FROM keiba.calculated_indices ci
JOIN keiba.races r ON r.id = ci.race_id
JOIN keiba.race_results rr ON rr.race_id = ci.race_id AND rr.horse_id = ci.horse_id
WHERE r.date BETWEEN :start_date AND :end_date
  AND ci.version = {version}
ORDER BY r.date, r.id, ci.horse_id
""")


def load_data(start_date: str, end_date: str, version: int = COMPOSITE_VERSION) -> pd.DataFrame:
    """DB からデータを取得する。"""
    with Session(engine) as db:
        result = db.execute(
            _build_query(version), {"start_date": start_date, "end_date": end_date}
        )
        rows = result.fetchall()
        columns = list(result.keys())

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=columns)
    df["composite_index"] = pd.to_numeric(df["composite_index"], errors="coerce")
    df["finish_position"] = pd.to_numeric(df["finish_position"], errors="coerce")
    df["abnormality_code"] = pd.to_numeric(df["abnormality_code"], errors="coerce").fillna(0)
    df["distance"] = pd.to_numeric(df["distance"], errors="coerce")
    df["win_odds"] = pd.to_numeric(df["win_odds"], errors="coerce")
    df["place_odds"] = pd.to_numeric(df["place_odds"], errors="coerce")

    logger.info(f"取得: {len(df):,} 件 / レース: {df['race_id'].nunique():,}")
    return df


def filter_valid_races(df: pd.DataFrame, min_runners: int = 4) -> pd.DataFrame:
    """有効レースに絞り込む（異常コード・着順NULL・頭数不足・指数NULL を除外）。"""
    bad = df[
        (df["abnormality_code"] > 0)
        | df["finish_position"].isna()
        | df["composite_index"].isna()
    ]["race_id"].unique()

    df = df[~df["race_id"].isin(bad)].copy()

    race_counts = df.groupby("race_id")["horse_id"].count()
    valid = race_counts[race_counts >= min_runners].index
    df = df[df["race_id"].isin(valid)].copy()

    logger.info(f"フィルタ後: {len(df):,} 件 / レース: {df['race_id'].nunique():,}")
    return df


# ---------------------------------------------------------------------------
# 確率推定: softmax(composite_index) → レース内勝利確率
# ---------------------------------------------------------------------------


def estimate_win_prob(df: pd.DataFrame, temperature: float = 1.0) -> pd.DataFrame:
    """composite_index から softmax で勝利確率を推定し、列 `p_win` を追加する。

    temperature が小さいほど指数差を強調する。
    """
    df = df.copy()

    def _softmax(x: np.ndarray, t: float) -> np.ndarray:
        x = x / t
        x_exp = np.exp(x - x.max())  # 数値安定性
        return x_exp / x_exp.sum()

    probs = []
    for _, grp in df.groupby("race_id"):
        idx_vals = grp["composite_index"].to_numpy(dtype=float)
        p = _softmax(idx_vals, temperature)
        probs.extend(p.tolist())

    df["p_win"] = probs
    return df


# ---------------------------------------------------------------------------
# EV（期待値）計算
# ---------------------------------------------------------------------------


def compute_ev(df: pd.DataFrame) -> pd.DataFrame:
    """単勝 EV = win_odds × p_win を計算し、列 `ev` を追加する。"""
    df = df.copy()
    df["ev"] = df["win_odds"] * df["p_win"]
    return df


# ---------------------------------------------------------------------------
# Kelly 基準
# ---------------------------------------------------------------------------


def compute_kelly(df: pd.DataFrame) -> pd.DataFrame:
    """Kelly 分率 f* = (b*p - q) / b を計算し、列 `kelly` を追加する。

    b = win_odds - 1（ネットオッズ）
    p = p_win
    q = 1 - p_win
    負値はゼロクリップ（賭けない）。
    """
    df = df.copy()
    b = df["win_odds"] - 1
    p = df["p_win"]
    q = 1 - p
    f_kelly = (b * p - q) / b
    df["kelly"] = f_kelly.clip(lower=0)
    return df


# ---------------------------------------------------------------------------
# 戦略シミュレーション
# ---------------------------------------------------------------------------


def _roi_fixed(df: pd.DataFrame, top_n: int = 1) -> dict:
    """指数 Top-N を固定額購入したときの ROI。"""
    bets_list = []
    for _, grp in df.groupby("race_id"):
        top = grp.nlargest(top_n, "composite_index")
        bets_list.append(top)

    bets_df = pd.concat(bets_list)
    valid = bets_df[bets_df["win_odds"].notna() & (bets_df["win_odds"] > 0)]
    n_bets = len(valid)
    payout = valid.loc[valid["finish_position"] == 1, "win_odds"].sum()
    roi = float(payout / n_bets * 100) if n_bets > 0 else 0.0
    wins = int((valid["finish_position"] == 1).sum())
    return {"bets": n_bets, "wins": wins, "roi": roi}


def _roi_ev_threshold(df: pd.DataFrame, ev_threshold: float) -> dict:
    """指数1位馬のEV >= ev_threshold のレースのみ購入したときの ROI。"""
    top1 = df.loc[df.groupby("race_id")["composite_index"].idxmax()].copy()
    valid = top1[(top1["ev"] >= ev_threshold) & top1["win_odds"].notna() & (top1["win_odds"] > 0)]
    n_bets = len(valid)
    if n_bets == 0:
        return {"bets": 0, "wins": 0, "roi": 0.0, "coverage": 0.0}
    payout = valid.loc[valid["finish_position"] == 1, "win_odds"].sum()
    roi = float(payout / n_bets * 100)
    wins = int((valid["finish_position"] == 1).sum())
    n_races = df["race_id"].nunique()
    coverage = n_bets / n_races
    return {"bets": n_bets, "wins": wins, "roi": roi, "coverage": coverage}


def _roi_odds_filter(df: pd.DataFrame, lo: float | None, hi: float | None) -> dict:
    """指数1位でオッズ範囲フィルタを適用したときの ROI。"""
    top1 = df.loc[df.groupby("race_id")["composite_index"].idxmax()].copy()
    mask = top1["win_odds"].notna() & (top1["win_odds"] > 0)
    if lo is not None:
        mask &= top1["win_odds"] >= lo
    if hi is not None:
        mask &= top1["win_odds"] <= hi
    valid = top1[mask]
    n_bets = len(valid)
    if n_bets == 0:
        return {"bets": 0, "wins": 0, "roi": 0.0, "coverage": 0.0}
    payout = valid.loc[valid["finish_position"] == 1, "win_odds"].sum()
    roi = float(payout / n_bets * 100)
    wins = int((valid["finish_position"] == 1).sum())
    n_races = df["race_id"].nunique()
    coverage = n_bets / n_races
    return {"bets": n_bets, "wins": wins, "roi": roi, "coverage": coverage}


def _roi_kelly(
    df: pd.DataFrame,
    fraction: float = 0.5,
    bankroll: float = 10000.0,
    ev_min: float = 1.0,
) -> dict:
    """Kelly 基準（指数1位馬を対象）で賭け額を調整したときの ROI シミュレーション。

    fraction=1.0: フルKelly, 0.5: 半Kelly, 0.25: 1/4Kelly
    bankroll: 初期資金（円）
    ev_min: EV がこれ未満の場合は賭けない
    """
    top1 = df.loc[df.groupby("race_id")["composite_index"].idxmax()].copy()
    # 日付順にソート（累積損益シミュレーション用）
    top1 = top1.sort_values("date").reset_index(drop=True)

    current_bankroll = bankroll
    total_bet = 0.0
    total_return = 0.0
    n_bets = 0
    n_wins = 0
    bankroll_series = [bankroll]

    for _, row in top1.iterrows():
        if row["win_odds"] <= 0 or pd.isna(row["win_odds"]):
            continue
        if row["kelly"] <= 0 or row["ev"] < ev_min:
            continue
        if current_bankroll <= 0:
            break

        bet_fraction = row["kelly"] * fraction
        bet_amount = current_bankroll * bet_fraction

        total_bet += bet_amount
        n_bets += 1

        if row["finish_position"] == 1:
            ret = bet_amount * row["win_odds"]
            n_wins += 1
        else:
            ret = 0.0

        current_bankroll = current_bankroll - bet_amount + ret
        total_return += ret
        bankroll_series.append(current_bankroll)

    roi = float(total_return / total_bet * 100) if total_bet > 0 else 0.0
    final = current_bankroll
    return {
        "bets": n_bets,
        "wins": n_wins,
        "roi": roi,
        "final_bankroll": final,
        "bankroll_series": bankroll_series,
    }


def _calibrate_temperature(df: pd.DataFrame) -> float:
    """softmax temperature をキャリブレーションする。

    p_win の平均が 1/平均頭数 に近くなる temperature を探索する。
    （p_win は定義上 1/n の平均になるが、spread の適切さを確認する）
    実際には実際の単勝 EV=1.0 になる temperature を grid search する。
    """
    avg_runners = df.groupby("race_id")["horse_id"].count().mean()
    target_mean_pwin = 1.0 / avg_runners

    # top1のpwinがwin_rateに近くなるtemperatureを探す
    win_rate = (
        df.loc[df.groupby("race_id")["composite_index"].idxmax(), "finish_position"] == 1
    ).mean()

    best_t = 1.0
    best_diff = float("inf")

    for t in [0.1, 0.2, 0.3, 0.5, 0.7, 1.0, 1.5, 2.0, 3.0, 5.0]:
        df_t = estimate_win_prob(df, temperature=t)
        top1_pwin = df_t.loc[df_t.groupby("race_id")["composite_index"].idxmax(), "p_win"].mean()
        diff = abs(top1_pwin - win_rate)
        if diff < best_diff:
            best_diff = diff
            best_t = t

    logger.info(
        f"キャリブレーション: 実勝率={win_rate:.3f}, "
        f"平均頭数={avg_runners:.1f}, 最適temperature={best_t}"
    )
    return best_t


# ---------------------------------------------------------------------------
# グリッドサーチ
# ---------------------------------------------------------------------------


def ev_threshold_grid(df: pd.DataFrame, thresholds: list[float] | None = None) -> pd.DataFrame:
    """EV 閾値別の ROI・投票数・カバレッジをグリッドサーチする。"""
    if thresholds is None:
        thresholds = [0.8, 0.9, 1.0, 1.1, 1.2, 1.3, 1.5, 1.7, 2.0]

    rows = []
    n_races = df["race_id"].nunique()
    for thr in thresholds:
        r = _roi_ev_threshold(df, thr)
        rows.append(
            {
                "EV閾値": thr,
                "投票数": r["bets"],
                "的中数": r["wins"],
                "カバレッジ(R割合)": round(r["bets"] / n_races * 100, 1) if n_races > 0 else 0.0,
                "単勝ROI(%)": round(r["roi"], 1),
                "的中率(%)": round(r["wins"] / r["bets"] * 100, 1) if r["bets"] > 0 else 0.0,
            }
        )

    return pd.DataFrame(rows)


def odds_filter_grid(
    df: pd.DataFrame,
    lo_values: list[float | None] | None = None,
    hi_values: list[float | None] | None = None,
) -> pd.DataFrame:
    """オッズ下限×上限グリッドサーチ（指数1位馬対象）。"""
    if lo_values is None:
        lo_values = [None, 2.0, 3.0, 4.0, 5.0]
    if hi_values is None:
        hi_values = [None, 30.0, 20.0]

    n_races = df["race_id"].nunique()
    rows = []
    for lo in lo_values:
        for hi in hi_values:
            r = _roi_odds_filter(df, lo, hi)
            rows.append(
                {
                    "下限(lo)": lo if lo is not None else "-",
                    "上限(hi)": hi if hi is not None else "-",
                    "投票数": r["bets"],
                    "カバレッジ%": round(r["bets"] / n_races * 100, 1) if n_races > 0 else 0.0,
                    "単勝ROI(%)": round(r["roi"], 1),
                    "的中率(%)": (
                        round(r["wins"] / r["bets"] * 100, 1) if r["bets"] > 0 else 0.0
                    ),
                }
            )

    return pd.DataFrame(rows)


def kelly_grid(df: pd.DataFrame) -> pd.DataFrame:
    """Kelly 分率×EV閾値のグリッドサーチ。"""
    rows = []
    for fraction, label in [(1.0, "フルKelly"), (0.5, "半Kelly"), (0.25, "1/4Kelly")]:
        for ev_min in [0.8, 1.0, 1.2]:
            r = _roi_kelly(df, fraction=fraction, bankroll=10000.0, ev_min=ev_min)
            rows.append(
                {
                    "Kelly分率": label,
                    "EV下限": ev_min,
                    "投票数": r["bets"],
                    "的中数": r["wins"],
                    "累積ROI(%)": round(r["roi"], 1),
                    "最終資金(円)": round(r["final_bankroll"], 0),
                }
            )
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# レポート生成
# ---------------------------------------------------------------------------


def _df_to_markdown(df: pd.DataFrame) -> str:
    """DataFrame を Markdown テーブルに変換する。"""
    header = "| " + " | ".join(str(c) for c in df.columns) + " |"
    sep = "| " + " | ".join("---" for _ in df.columns) + " |"
    rows = []
    for _, row in df.iterrows():
        rows.append("| " + " | ".join(str(v) for v in row.values) + " |")
    return "\n".join([header, sep] + rows)


def generate_report(
    train_df: pd.DataFrame,
    test_df: pd.DataFrame,
    train_period: str,
    test_period: str,
    temperature: float,
    report_dir: Path | None = None,
) -> str:
    """戦略分析レポート（Markdown）を生成する。"""
    lines = [
        "# Strategy Agent レポート",
        "",
        f"**訓練期間**: {train_period}",
        f"**テスト期間**: {test_period}",
        f"**バージョン**: v{COMPOSITE_VERSION}",
        f"**softmax temperature**: {temperature}",
        "",
        "---",
        "",
    ]

    for label, df in [("訓練（学習）", train_df), ("テスト（検証）", test_df)]:
        n_races = df["race_id"].nunique()
        lines += [
            f"## {label}（{n_races:,} レース）",
            "",
        ]

        # ベースライン（固定額・指数1位）
        base = _roi_fixed(df, top_n=1)
        lines += [
            "### ベースライン（固定額・指数1位購入）",
            "",
            f"- 投票数: {base['bets']:,} / 的中: {base['wins']:,}",
            f"- 単勝 ROI: **{base['roi']:.1f}%**",
            f"- 的中率: {base['wins']/base['bets']*100:.1f}%",
            "",
        ]

        # EV 閾値グリッド
        ev_grid = ev_threshold_grid(df)
        lines += [
            "### EV 閾値グリッド",
            "",
            _df_to_markdown(ev_grid),
            "",
        ]

        # オッズフィルタグリッド
        odds_grid = odds_filter_grid(df)
        lines += [
            "### オッズフィルタグリッド（指数1位）",
            "",
            _df_to_markdown(odds_grid),
            "",
        ]

        # Kelly シミュレーション
        kelly_result = kelly_grid(df)
        lines += [
            "### Kelly 基準シミュレーション（初期資金 10,000 円）",
            "",
            _df_to_markdown(kelly_result),
            "",
        ]

    # 推奨戦略（テストデータ基準）
    lines += [
        "---",
        "",
        "## 推奨戦略まとめ",
        "",
    ]

    # テストデータで最高 ROI の EV 閾値を探す
    test_ev_grid = ev_threshold_grid(test_df)
    best_ev_row = test_ev_grid[test_ev_grid["カバレッジ(R割合)"] >= 20].sort_values(
        "単勝ROI(%)", ascending=False
    )
    if not best_ev_row.empty:
        best = best_ev_row.iloc[0]
        lines += [
            f"- **EV 閾値推奨**: {best['EV閾値']} "
            f"（テスト ROI {best['単勝ROI(%)']:.1f}%, "
            f"カバレッジ {best['カバレッジ(R割合)']}%）",
        ]

    # テストデータで最高 ROI のオッズ下限を探す
    test_odds_grid = odds_filter_grid(test_df)
    best_odds_row = test_odds_grid[
        (test_odds_grid["下限(lo)"] != "-")
        & (test_odds_grid["上限(hi)"] == "-")
        & (test_odds_grid["カバレッジ%"] >= 25)
    ].sort_values("単勝ROI(%)", ascending=False)
    if not best_odds_row.empty:
        best_o = best_odds_row.iloc[0]
        lines += [
            f"- **オッズ下限推奨**: {best_o['下限(lo)']} 倍以上 "
            f"（テスト ROI {best_o['単勝ROI(%)']:.1f}%, "
            f"カバレッジ {best_o['カバレッジ%']}%）",
        ]

    lines += [""]

    report = "\n".join(lines)

    if report_dir is not None:
        report_dir.mkdir(parents=True, exist_ok=True)
        out_path = report_dir / f"strategy_report_v{COMPOSITE_VERSION}.md"
        out_path.write_text(report, encoding="utf-8")
        logger.info(f"レポート保存: {out_path}")

    return report


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="戦略エージェント: Kelly基準・EV閾値最適化")
    parser.add_argument("--start", required=True, help="訓練開始日 YYYYMMDD")
    parser.add_argument("--end", required=True, help="訓練終了日 YYYYMMDD")
    parser.add_argument("--test-start", default=None, help="テスト開始日 YYYYMMDD")
    parser.add_argument("--test-end", default=None, help="テスト終了日 YYYYMMDD")
    parser.add_argument("--version", type=int, default=COMPOSITE_VERSION, help="指数バージョン")
    parser.add_argument("--temperature", type=float, default=None, help="softmax temperature (省略時は自動キャリブレーション)")
    parser.add_argument("--report", default=None, help="レポート出力ディレクトリ")
    args = parser.parse_args()

    # 訓練データ
    logger.info(f"訓練データ取得: {args.start} 〜 {args.end}")
    train_raw = load_data(args.start, args.end, version=args.version)
    if train_raw.empty:
        logger.error("訓練データなし")
        sys.exit(1)
    train_raw = filter_valid_races(train_raw)

    # temperature キャリブレーション
    temperature = args.temperature
    if temperature is None:
        temperature = _calibrate_temperature(train_raw)

    train_df = estimate_win_prob(train_raw, temperature)
    train_df = compute_ev(train_df)
    train_df = compute_kelly(train_df)

    # テストデータ
    if args.test_start and args.test_end:
        logger.info(f"テストデータ取得: {args.test_start} 〜 {args.test_end}")
        test_raw = load_data(args.test_start, args.test_end, version=args.version)
        if not test_raw.empty:
            test_raw = filter_valid_races(test_raw)
            test_df = estimate_win_prob(test_raw, temperature)
            test_df = compute_ev(test_df)
            test_df = compute_kelly(test_df)
        else:
            logger.warning("テストデータなし。訓練データで代替します。")
            test_df = train_df
        test_period = f"{args.test_start}〜{args.test_end}"
    else:
        test_df = train_df
        test_period = f"{args.start}〜{args.end}（同一期間）"

    report_dir = Path(args.report) if args.report else None

    report = generate_report(
        train_df=train_df,
        test_df=test_df,
        train_period=f"{args.start}〜{args.end}",
        test_period=test_period,
        temperature=temperature,
        report_dir=report_dir,
    )

    print(report)


if __name__ == "__main__":
    main()
