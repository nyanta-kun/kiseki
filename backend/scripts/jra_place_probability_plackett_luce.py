"""JRA 複勝確率の Plackett-Luce 理論値検証（研究用・DB書き込みなし）

現行の本番 place_probability（`scripts/inference_v26.py` 258-273行目）は
「win_probability(softmax) × 3」を [0,1] にクリップするだけの簡易ヒューリスティックで、
理論的根拠がない。

本スクリプトは Plackett-Luce モデル（各馬の強さ w_i = exp(composite_index_i / 10.0) を
用いた逐次選択過程モデル）に基づき「3着以内に入る確率」を厳密に導出し、
現行ヒューリスティックとキャリブレーション（ECE）を比較する。

Plackett-Luce による P(3着以内) の導出（全順列を数え上げず O(n^3) で計算）:
    P(i=1着)        = w_i / W                                          (W = Σw)
    P(i=2着)        = Σ_{j≠i} P(j=1着) × w_i / (W - w_j)
    P(i=3着)        = Σ_{j≠i} Σ_{k≠i,j} P(j=1着) × P(k=2着|j=1着) × w_i / (W - w_j - w_k)
    P(i 3着以内)     = P(i=1着) + P(i=2着) + P(i=3着)

worths として win_probability（softmax出力・レース内 Σ=1）をそのまま使う。
Plackett-Luce は worths のスケールに対して不変（定数倍しても各確率は変化しない）なので、
生の composite_index から exp(score/10) を計算しても、正規化済みの win_probability を
そのまま渡しても数学的に同じ結果になる。本スクリプトでは後者を採用する。

なお `src/indices/composite.py` の `CompositeIndexCalculator._harville_place_probs`
（v24 系で既に実装済みの Harville 公式）は本質的に同一の数式であり、本スクリプトの
独立実装が正しいかどうかをクロスチェックする対照実装として利用する
（`tests/test_plackett_luce.py` 参照）。

使い方:
    cd backend
    .venv/bin/python scripts/jra_place_probability_plackett_luce.py
    .venv/bin/python scripts/jra_place_probability_plackett_luce.py --start 20230501 --end 20260726

出力:
    標準出力にキャリブレーション比較・健全性チェック結果を表示
    backend/models/v26_place_probability_pl_calibration.json にサマリー保存
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

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
logger = logging.getLogger("pl_place_prob")

V26_VERSION = 26
SOFTMAX_TEMPERATURE = 10.0
JRA_COURSES = ("01", "02", "03", "04", "05", "06", "07", "08", "09", "10")
MODELS_DIR = _root / "models"

DATA_QUERY = """
SELECT
    ci.race_id, ci.horse_id, ci.composite_index,
    r.date, r.head_count, r.course,
    rr.finish_position, rr.win_odds, rr.win_popularity
FROM keiba.calculated_indices ci
JOIN keiba.race_results rr ON rr.race_id = ci.race_id AND rr.horse_id = ci.horse_id
JOIN keiba.races r ON r.id = ci.race_id
WHERE ci.version = %(ver)s
  AND r.head_count >= 8
  AND r.date BETWEEN %(start)s AND %(end)s
  AND COALESCE(rr.abnormality_code, 0) = 0
  AND r.course IN ('01','02','03','04','05','06','07','08','09','10')
"""


# ---------------------------------------------------------------------------
# 確率算出関数（DB非依存・ユニットテスト対象）
# ---------------------------------------------------------------------------


def softmax(scores: np.ndarray, temperature: float = SOFTMAX_TEMPERATURE) -> np.ndarray:
    """レース内 softmax で勝率(win_probability)を算出する。数値安定のため max を引く。

    Args:
        scores: 1レース分の composite_index 配列。
        temperature: softmax 温度（本番 v26 と同じ 10.0 をデフォルトとする）。

    Returns:
        各馬の勝率配列（合計 1.0）。
    """
    s = np.asarray(scores, dtype=float) / temperature
    ex = np.exp(s - s.max())
    return ex / ex.sum()


def heuristic_place_probs(win_p: np.ndarray) -> np.ndarray:
    """現行本番の簡易ヒューリスティック: place_p = clip(win_p × 3, 0, 1)。

    `scripts/inference_v26.py` 258-273行目と同一のロジック。

    Args:
        win_p: 1レース分の勝率配列。

    Returns:
        各馬の複勝確率(ヒューリスティック)配列。
    """
    return np.clip(np.asarray(win_p, dtype=float) * 3.0, 0.0, 1.0)


def plackett_luce_place_probs(worths: np.ndarray) -> np.ndarray:
    """Plackett-Luce モデルにおける「3着以内」確率を厳密に導出する（全順列非列挙）。

    Plackett-Luce は逐次選択過程モデル: 1着馬を強さに比例した確率で選び、残った馬から
    2着馬を選び、さらに残った馬から3着馬を選ぶ、という条件付き確率の積で順位確率を表す。
    worths は正の値であれば何でもよく（定数倍しても結果は不変）、通常は
    `exp(composite_index / temperature)` あるいは既に正規化済みの win_probability を渡す。

    理論式（n=1レースの頭数、i,j,k は馬のインデックス）:
        W = Σ w
        P(i=1着) = w_i / W
        P(i=2着) = Σ_{j≠i} P(j=1着) × w_i / (W - w_j)
        P(i=3着) = Σ_{j≠i} Σ_{k≠i,j} P(j=1着) × [w_k/(W-w_j)] × w_i / (W - w_j - w_k)
        P(i 3着以内) = P(i=1着) + P(i=2着) + P(i=3着)

    計算量は O(n^3) だが JRA最大頭数 n=18 でも十分高速。
    n<=3 の場合は全馬が必ず3着以内になるため自明に 1.0 を返す。

    Args:
        worths: 1レース分の強さパラメータ配列（正の値）。

    Returns:
        各馬が3着以内に入る確率の配列（worths と同じ順序）。全馬の合計は理論上 min(3, n) に
        一致する（n>=3 なら 3.0 に一致するのが Plackett-Luce 実装の健全性チェック）。

    Raises:
        ValueError: worths の合計が 0 以下の場合。
    """
    w = np.asarray(worths, dtype=float)
    n = len(w)
    if n == 0:
        return np.array([])
    if n <= 3:
        # 3着払いの対象頭数以下なら全馬が必ず3着以内
        return np.ones(n)

    total = w.sum()
    if total <= 0:
        raise ValueError("worths の合計は正である必要があります")

    p1 = w / total

    # P(2着): M2[j, i] = P(j=1着) × w_i / (W - w_j)  (i != j)
    denom1 = total - w
    with np.errstate(divide="ignore", invalid="ignore"):
        row_factor = np.where(denom1 > 1e-12, p1 / denom1, 0.0)
    m2 = np.outer(row_factor, w)
    np.fill_diagonal(m2, 0.0)
    p2 = m2.sum(axis=0)

    # P(3着): j(1着) × k(2着) の全ペアを列挙し、残り馬全員への寄与をベクトルで加算
    p3 = np.zeros(n)
    idx_all = np.arange(n)
    for j in range(n):
        wj = w[j]
        denom_j = total - wj
        if denom_j <= 1e-12:
            continue
        pk_given_j = w / denom_j  # P(k=2着 | j=1着)。k=j は下で mask
        denom_jk = denom_j - w  # W - w_j - w_k (k でインデックス)
        valid_k = (idx_all != j) & (denom_jk > 1e-12)
        for k in idx_all[valid_k]:
            factor = p1[j] * pk_given_j[k] / denom_jk[k]
            contrib = factor * w
            contrib[j] = 0.0
            contrib[k] = 0.0
            p3 += contrib

    place = p1 + p2 + p3
    return np.clip(place, 0.0, 1.0)


def calib_metrics(prob: np.ndarray, y: np.ndarray, n_bins: int = 10) -> dict[str, Any]:
    """ECE(Expected Calibration Error) / MCE / Brier + decile信頼性テーブルを算出する。

    各 bin は等サンプル数（分位）で区切り、bin内の「予測確率平均」と「実測的中率」の
    絶対差をサンプル数加重平均したものが ECE。

    Args:
        prob: 予測確率配列。
        y: 実測 0/1 ラベル配列（同じ長さ）。
        n_bins: 分位数（デフォルト10 = decile）。

    Returns:
        ece / mce / brier / table（decile毎の n・予測%・実測%・乖離%）を含む dict。
    """
    df = pd.DataFrame({"p": prob, "y": y}).dropna()
    df = df.sort_values("p").reset_index(drop=True)
    tot = len(df)
    if tot == 0:
        return {"ece": float("nan"), "mce": float("nan"), "brier": float("nan"), "table": [], "n": 0}
    df["bin"] = (np.arange(tot) * n_bins // tot).clip(0, n_bins - 1)
    ece = 0.0
    mce = 0.0
    table = []
    for b, g in df.groupby("bin"):
        pred = g["p"].mean()
        act = g["y"].mean()
        gap = abs(pred - act)
        ece += gap * len(g) / tot
        mce = max(mce, gap)
        table.append(
            {
                "decile": int(b) + 1,
                "n": int(len(g)),
                "pred_pct": round(pred * 100, 2),
                "actual_pct": round(act * 100, 2),
                "gap_pct": round((act - pred) * 100, 2),
            }
        )
    brier = float(np.mean((df["p"] - df["y"]) ** 2))
    return {"ece": float(ece), "mce": float(mce), "brier": brier, "table": table, "n": tot}


def print_calib(name: str, m: dict[str, Any]) -> None:
    """calib_metrics の結果を整形して標準出力に表示する。"""
    print(f"\n[{name}]  n={m['n']:,}  ECE={m['ece']:.4f}  MCE={m['mce']:.4f}  Brier={m['brier']:.4f}")
    print(f"  {'decile':<8}{'n':>8}{'予測%':>10}{'実測%':>10}{'乖離':>10}")
    for row in m["table"]:
        print(
            f"  {row['decile']:<8}{row['n']:>8}{row['pred_pct']:>9.2f}%"
            f"{row['actual_pct']:>9.2f}%{row['gap_pct']:>+9.2f}%"
        )


# ---------------------------------------------------------------------------
# DB 取得・レース単位計算
# ---------------------------------------------------------------------------


def fetch_dataset(conn: Any, start: str, end: str) -> pd.DataFrame:
    """calculated_indices(version=26) + race_results + races を取得する。

    `scripts/train_v26_lightgbm.py` の DATA_QUERY と同一の JOIN 構造
    （head_count>=8 AND course IN JRA10場 AND abnormality_code=0）を用いる。
    DB への書き込みは一切行わない（SELECT のみ）。
    """
    cur = conn.cursor()
    cur.execute(DATA_QUERY, {"ver": V26_VERSION, "start": start, "end": end})
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    cur.close()
    df = pd.DataFrame(rows, columns=cols)
    logger.info(f"取得: {len(df):,}行 / {df['race_id'].nunique():,}レース ({start}〜{end})")
    return df


def compute_race_level_probs(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """レース単位で win_p / heuristic place_p / Plackett-Luce place_p を算出する。

    Args:
        df: fetch_dataset の戻り値（race_id, horse_id, composite_index 等を含む）。

    Returns:
        (horse_df, race_sum_df) のタプル。
        horse_df: 馬単位の行に win_p / place_p_heuristic / place_p_pl / hit(0/1) を付与したもの。
        race_sum_df: レース単位の P(3着以内)合計（健全性チェック用）。
    """
    df = df.copy()
    df["composite_index"] = pd.to_numeric(df["composite_index"], errors="coerce")
    df["finish_position"] = pd.to_numeric(df["finish_position"], errors="coerce")
    df["win_odds"] = pd.to_numeric(df["win_odds"], errors="coerce")
    df["win_popularity"] = pd.to_numeric(df["win_popularity"], errors="coerce")
    df = df.dropna(subset=["composite_index", "finish_position"]).reset_index(drop=True)

    out_rows: list[dict[str, Any]] = []
    race_sums: list[dict[str, Any]] = []

    for race_id, idx in df.groupby("race_id").indices.items():
        sub = df.loc[idx]
        scores = sub["composite_index"].values
        n = len(scores)
        win_p = softmax(scores)
        place_heur = heuristic_place_probs(win_p)
        place_pl = plackett_luce_place_probs(win_p)

        race_sums.append(
            {
                "race_id": int(race_id),
                "head_count": int(n),
                "sum_place_pl": float(place_pl.sum()),
                "sum_win_p": float(win_p.sum()),
            }
        )

        for j, (_, row) in enumerate(sub.iterrows()):
            out_rows.append(
                {
                    "race_id": int(race_id),
                    "horse_id": int(row["horse_id"]),
                    "head_count": int(n),
                    "date": row["date"],
                    "win_odds": row["win_odds"],
                    "win_popularity": row["win_popularity"],
                    "win_p": float(win_p[j]),
                    "place_p_heuristic": float(place_heur[j]),
                    "place_p_pl": float(place_pl[j]),
                    "hit": 1 if row["finish_position"] <= 3 else 0,
                }
            )

    horse_df = pd.DataFrame(out_rows)
    race_sum_df = pd.DataFrame(race_sums)
    return horse_df, race_sum_df


# ---------------------------------------------------------------------------
# レポート
# ---------------------------------------------------------------------------


def sanity_check(race_sum_df: pd.DataFrame) -> dict[str, Any]:
    """全馬の P(3着以内) 合計が 3.0 に近いか（Plackett-Luce実装の健全性チェック）。"""
    eligible = race_sum_df[race_sum_df["head_count"] >= 3]
    diffs = (eligible["sum_place_pl"] - 3.0).abs()
    result = {
        "n_races": int(len(eligible)),
        "mean_sum": float(eligible["sum_place_pl"].mean()),
        "max_abs_diff_from_3": float(diffs.max()) if len(diffs) else float("nan"),
        "mean_abs_diff_from_3": float(diffs.mean()) if len(diffs) else float("nan"),
        "n_races_diff_gt_1e-6": int((diffs > 1e-6).sum()),
    }
    print("\n=== Plackett-Luce 健全性チェック: 全馬のP(3着以内)合計が3.0に近いか ===")
    print(f"  対象レース数: {result['n_races']:,}")
    print(f"  合計値の平均: {result['mean_sum']:.6f} (理論値 3.0)")
    print(f"  |合計-3.0| の最大: {result['max_abs_diff_from_3']:.2e}")
    print(f"  |合計-3.0| の平均: {result['mean_abs_diff_from_3']:.2e}")
    print(f"  |合計-3.0| > 1e-6 のレース数: {result['n_races_diff_gt_1e-6']:,} / {result['n_races']:,}")
    return result


def deviation_analysis(horse_df: pd.DataFrame) -> dict[str, Any]:
    """両手法の乖離が大きいケースの特徴（頭数・人気帯）を確認する。"""
    d = horse_df.copy()
    d["diff"] = d["place_p_heuristic"] - d["place_p_pl"]
    d["abs_diff"] = d["diff"].abs()

    print("\n=== 頭数別: ヒューリスティック - PL の平均乖離 ===")
    print(f"  {'頭数帯':<10}{'n':>8}{'平均乖離':>12}{'平均|乖離|':>12}")
    head_bins = [(8, 9), (10, 11), (12, 13), (14, 15), (16, 18)]
    by_head: list[dict[str, Any]] = []
    for lo, hi in head_bins:
        g = d[(d["head_count"] >= lo) & (d["head_count"] <= hi)]
        if len(g) == 0:
            continue
        row = {
            "head_count_range": f"{lo}-{hi}",
            "n": int(len(g)),
            "mean_diff": float(g["diff"].mean()),
            "mean_abs_diff": float(g["abs_diff"].mean()),
        }
        by_head.append(row)
        print(f"  {row['head_count_range']:<10}{row['n']:>8}{row['mean_diff']:>+12.4f}{row['mean_abs_diff']:>12.4f}")

    print("\n=== 人気帯別（win_popularity）: ヒューリスティック - PL の平均乖離 ===")
    print(f"  {'人気帯':<10}{'n':>8}{'平均乖離':>12}{'平均|乖離|':>12}")
    pop_bins = [(1, 3), (4, 6), (7, 9), (10, 12), (13, 18)]
    by_pop: list[dict[str, Any]] = []
    dp = d.dropna(subset=["win_popularity"])
    for lo, hi in pop_bins:
        g = dp[(dp["win_popularity"] >= lo) & (dp["win_popularity"] <= hi)]
        if len(g) == 0:
            continue
        row = {
            "popularity_range": f"{lo}-{hi}",
            "n": int(len(g)),
            "mean_diff": float(g["diff"].mean()),
            "mean_abs_diff": float(g["abs_diff"].mean()),
        }
        by_pop.append(row)
        print(f"  {row['popularity_range']:<10}{row['n']:>8}{row['mean_diff']:>+12.4f}{row['mean_abs_diff']:>12.4f}")

    corr_head = float(d["abs_diff"].corr(d["head_count"]))
    corr_pop = float(dp["abs_diff"].corr(dp["win_popularity"])) if len(dp) else float("nan")
    print(f"\n  |乖離| と 頭数 の相関: {corr_head:+.4f}")
    print(f"  |乖離| と 人気 の相関: {corr_pop:+.4f}")

    return {
        "by_head_count": by_head,
        "by_popularity": by_pop,
        "corr_abs_diff_head_count": corr_head,
        "corr_abs_diff_popularity": corr_pop,
        "overall_mean_diff": float(d["diff"].mean()),
        "overall_mean_abs_diff": float(d["abs_diff"].mean()),
        "corr_heuristic_pl": float(d["place_p_heuristic"].corr(d["place_p_pl"])),
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--start", default="20230501")
    p.add_argument("--end", default="20260726")
    p.add_argument("--train-end", default="20250630", help="訓練期間相当の終端")
    p.add_argument("--test-start", default="20260101", help="直近テスト期間の開始")
    args = p.parse_args()

    dsn = (
        f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
        f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} password={os.getenv('DB_PASSWORD')}"
    )
    conn = psycopg2.connect(dsn)
    df = fetch_dataset(conn, args.start, args.end)
    conn.close()

    if df.empty:
        logger.warning("対象データなし")
        return

    horse_df, race_sum_df = compute_race_level_probs(df)
    logger.info(f"算出完了: {len(horse_df):,}頭 / {race_sum_df['race_id'].nunique():,}レース")

    # 1) 健全性チェック
    sanity = sanity_check(race_sum_df)

    # 2) キャリブレーション比較（全期間・訓練期間・テスト期間）
    periods = {
        "全期間": (args.start, args.end),
        "訓練期間相当": (args.start, args.train_end),
        "直近テスト期間": (args.test_start, args.end),
    }
    calib_results: dict[str, dict[str, Any]] = {}
    for label, (s, e) in periods.items():
        sub = horse_df[(horse_df["date"] >= s) & (horse_df["date"] <= e)]
        if sub.empty:
            logger.warning(f"[{label}] 対象データなし ({s}〜{e})")
            continue
        m_heur = calib_metrics(sub["place_p_heuristic"].values, sub["hit"].values)
        m_pl = calib_metrics(sub["place_p_pl"].values, sub["hit"].values)
        print(f"\n\n########## {label} ({s}〜{e}) ##########")
        print_calib("現行ヒューリスティック (win_p×3)", m_heur)
        print_calib("Plackett-Luce", m_pl)
        print(f"\n  ECE比較: ヒューリスティック={m_heur['ece']:.4f}  Plackett-Luce={m_pl['ece']:.4f}"
              f"  (差={m_heur['ece'] - m_pl['ece']:+.4f}, 正ならPLが優れる)")
        print(f"  Brier比較: ヒューリスティック={m_heur['brier']:.4f}  Plackett-Luce={m_pl['brier']:.4f}")
        calib_results[label] = {
            "period": [s, e],
            "n": len(sub),
            "heuristic": {k: v for k, v in m_heur.items() if k != "table"},
            "plackett_luce": {k: v for k, v in m_pl.items() if k != "table"},
            "heuristic_table": m_heur["table"],
            "plackett_luce_table": m_pl["table"],
        }

    # 3) 乖離分析
    deviation = deviation_analysis(horse_df)

    # 4) サマリー保存
    MODELS_DIR.mkdir(exist_ok=True)
    out_path = MODELS_DIR / "v26_place_probability_pl_calibration.json"
    summary = {
        "generated_at": pd.Timestamp.now().isoformat(),
        "data_range": [args.start, args.end],
        "n_horses": len(horse_df),
        "n_races": int(race_sum_df["race_id"].nunique()),
        "sanity_check": sanity,
        "calibration_by_period": calib_results,
        "deviation_analysis": deviation,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    logger.info(f"サマリー保存: {out_path}")


if __name__ == "__main__":
    main()
