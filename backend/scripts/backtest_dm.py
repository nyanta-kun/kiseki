"""DM 指数組み込み効果のバックテスト

JV-Next の DM (タイム型・対戦型) を総合指数に組み込んだ場合の効果を
既存 calculated_indices (v22) と race_entries.jvan_time_dm/battle_dm の
組み合わせから「再合成」する形で検証する。

検証内容:
  1. DM 単体の予測力 (DM1位の単勝/複勝ROI、スピアマン相関)
  2. 既存総合指数との順位相関 (補助情報か独立情報か)
  3. ウェイト感度: DM ウェイト 0% / 2% / 5% / 10% / 15% / 20% で再合成
  4. 期待値ベース (1位ROI / 上位3頭ROI / 高オッズ帯ROI)

DM カバレッジが不完全な期間も「DMが80%以上ある馬・レース」に絞ることで
十分な統計量を確保する。

使い方:
  .venv/bin/python scripts/backtest_dm.py --start 20230101 --end 20260426
  .venv/bin/python scripts/backtest_dm.py --start 20250701 --end 20250831 --min-coverage 1.0
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
from src.utils.constants import INDEX_WEIGHTS

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("backtest_dm")


# ============================================================================
# データ取得
# ============================================================================

QUERY = text("""
SELECT
    r.id              AS race_id,
    r.date            AS date,
    r.surface         AS surface,
    r.distance        AS distance,
    r.course          AS course,
    ci.horse_id,
    ci.speed_index,
    ci.last_3f_index,
    ci.course_aptitude,
    ci.position_advantage,
    ci.jockey_index,
    ci.pace_index,
    ci.rotation_index,
    ci.pedigree_index,
    ci.training_index,
    ci.anagusa_index,
    ci.paddock_index,
    ci.rebound_index,
    ci.rivals_growth_index,
    ci.career_phase_index,
    ci.distance_change_index,
    ci.jockey_trainer_combo_index,
    ci.going_pedigree_index,
    re.jvan_time_dm,
    re.jvan_battle_dm,
    rr.finish_position,
    rr.abnormality_code,
    rr.win_odds,
    rr.win_popularity
FROM keiba.calculated_indices ci
JOIN keiba.races r ON r.id = ci.race_id
JOIN keiba.race_entries re ON re.race_id = ci.race_id AND re.horse_id = ci.horse_id
JOIN keiba.race_results rr ON rr.race_id = ci.race_id AND rr.horse_id = ci.horse_id
WHERE r.date BETWEEN :start_date AND :end_date
  AND r.course IN ('01','02','03','04','05','06','07','08','09','10')
  AND ci.version = 22
ORDER BY r.date, r.id, ci.horse_id
""")


def load_data(start: str, end: str) -> pd.DataFrame:
    with Session(engine) as db:
        rows = db.execute(QUERY, {"start_date": start, "end_date": end}).fetchall()
        cols = [
            "race_id", "date", "surface", "distance", "course",
            "horse_id",
            "speed_index", "last_3f_index", "course_aptitude", "position_advantage",
            "jockey_index", "pace_index", "rotation_index", "pedigree_index",
            "training_index", "anagusa_index", "paddock_index", "rebound_index",
            "rivals_growth_index", "career_phase_index", "distance_change_index",
            "jockey_trainer_combo_index", "going_pedigree_index",
            "jvan_time_dm", "jvan_battle_dm",
            "finish_position", "abnormality_code", "win_odds", "win_popularity",
        ]
    df = pd.DataFrame(rows, columns=cols)
    if df.empty:
        return df

    num_cols = [c for c in df.columns if c not in ("race_id", "date", "surface", "course", "horse_id")]
    for c in num_cols:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["abnormality_code"] = df["abnormality_code"].fillna(0)

    logger.info(
        f"取得: {len(df):,} 行 / {df['race_id'].nunique():,} レース / "
        f"DM(time): {df['jvan_time_dm'].notna().sum():,} 馬, "
        f"DM(battle): {df['jvan_battle_dm'].notna().sum():,} 馬"
    )
    return df


# ============================================================================
# レースフィルタ
# ============================================================================

def filter_dm_races(df: pd.DataFrame, min_coverage: float = 0.8) -> pd.DataFrame:
    """DM が一定以上揃ったレースに絞る。さらに異常コード/着順なし馬を除外。"""
    bad = df[(df["abnormality_code"] > 0) | df["finish_position"].isna()]["race_id"].unique()
    df = df[~df["race_id"].isin(bad)].copy()

    cov = df.groupby("race_id").apply(
        lambda g: g["jvan_time_dm"].notna().mean(), include_groups=False
    )
    keep = cov[cov >= min_coverage].index
    df = df[df["race_id"].isin(keep)].copy()

    # 頭数フィルタ (4頭未満は除外)
    rc = df.groupby("race_id")["horse_id"].count()
    df = df[df["race_id"].isin(rc[rc >= 4].index)].copy()

    logger.info(
        f"DM coverage ≥ {min_coverage:.0%}: {df['race_id'].nunique():,} レース / "
        f"{len(df):,} 馬"
    )
    return df


# ============================================================================
# 指数再合成
# ============================================================================

# v22 重み (DM 抜き, 合計1.0)
BASE_WEIGHTS = {
    "speed_index":               INDEX_WEIGHTS["speed"],
    "last_3f_index":             INDEX_WEIGHTS["last_3f"],
    "course_aptitude":           INDEX_WEIGHTS["course_aptitude"],
    "pace_index":                INDEX_WEIGHTS["pace"],
    "jockey_index":              INDEX_WEIGHTS["jockey_trainer"],
    "pedigree_index":            INDEX_WEIGHTS["pedigree"],
    "rotation_index":            INDEX_WEIGHTS["rotation"],
    "training_index":            INDEX_WEIGHTS["training"],
    "position_advantage":        INDEX_WEIGHTS["position_advantage"],
    "anagusa_index":             INDEX_WEIGHTS["anagusa"],
    "paddock_index":             INDEX_WEIGHTS["paddock"],
    "rebound_index":             INDEX_WEIGHTS["disadvantage_bonus"],
    "rivals_growth_index":       INDEX_WEIGHTS["rivals_growth"],
    "career_phase_index":        INDEX_WEIGHTS["career_phase"],
    "distance_change_index":     INDEX_WEIGHTS["distance_change"],
    "jockey_trainer_combo_index": INDEX_WEIGHTS["jockey_trainer_combo"],
    "going_pedigree_index":      INDEX_WEIGHTS["going_pedigree"],
}


def fill_dm_with_race_mean(df: pd.DataFrame) -> pd.DataFrame:
    """DM が NULL の馬は同レース内の DM 平均で埋める (composite._fill_with_race_mean 相当)。"""
    df = df.copy()
    for col in ("jvan_time_dm", "jvan_battle_dm"):
        df[col] = df.groupby("race_id")[col].transform(lambda s: s.fillna(s.mean()))
        # レース内全員 NULL の場合は全体平均
        df[col] = df[col].fillna(df[col].mean())
    return df


def recompose(df: pd.DataFrame, w_time_dm: float, w_battle_dm: float,
              normalize: bool = True) -> pd.Series:
    """指数を再合成する。

    normalize=True のとき、ベース重みを (1 - w_time_dm - w_battle_dm) にスケールしてから
    DM を加える (合計1.0)。
    normalize=False のとき、ベース重みはそのままで DM を上乗せ (現行 v23 と同じ)。
    """
    if normalize:
        scale = 1.0 - w_time_dm - w_battle_dm
        if scale < 0:
            raise ValueError("DM ウェイトの合計が 1.0 を超えています")
        w = {k: v * scale for k, v in BASE_WEIGHTS.items()}
    else:
        w = dict(BASE_WEIGHTS)

    # 単純合成 (交互作用項は省略: 全モデルに共通の加算項なので比較に影響しない)
    # composite_index 列を作って返す
    score = pd.Series(0.0, index=df.index)
    for col, weight in w.items():
        if weight == 0:
            continue
        score = score + df[col].fillna(50.0) * weight
    score = score + df["jvan_time_dm"] * w_time_dm + df["jvan_battle_dm"] * w_battle_dm
    return score


# ============================================================================
# メトリクス
# ============================================================================

def spearman_per_race(df: pd.DataFrame, score_col: str) -> float:
    """レースごとのスピアマン相関の平均 (高指数ほど着順が良いはず: 負の相関期待)."""
    rhos = []
    for _, g in df.groupby("race_id"):
        if len(g) < 3:
            continue
        x = g[score_col].to_numpy(float)
        y = g["finish_position"].to_numpy(float)
        if np.any(np.isnan(x)) or np.any(np.isnan(y)):
            continue
        rx = x.argsort().argsort()
        ry = y.argsort().argsort()
        if rx.std() == 0 or ry.std() == 0:
            continue
        rhos.append(np.corrcoef(rx, ry)[0, 1])
    # 着順は小さいほど良い、指数は大きいほど良いので相関は負になる → 反転
    return float(-np.mean(rhos)) if rhos else float("nan")


def metrics_from_score(df: pd.DataFrame, score_col: str) -> dict:
    top1 = df.loc[df.groupby("race_id")[score_col].idxmax()].copy()
    valid = top1[top1["win_odds"].notna() & (top1["win_odds"] > 0)]
    bets = len(valid)
    wins = (valid["finish_position"] == 1).sum()
    payout = valid.loc[valid["finish_position"] == 1, "win_odds"].sum()
    roi = payout / bets * 100 if bets else float("nan")

    place_rate = (top1["finish_position"] <= 3).mean()

    rho = spearman_per_race(df, score_col)
    return {
        "races": int(top1["race_id"].nunique()),
        "win_rate_%": round(float(wins / bets * 100), 2) if bets else float("nan"),
        "place_rate_%": round(float(place_rate * 100), 2),
        "tansho_roi_%": round(float(roi), 2),
        "spearman_rho": round(float(rho), 4) if not np.isnan(rho) else float("nan"),
    }


def metrics_top_n(df: pd.DataFrame, score_col: str, n: int = 3) -> dict:
    """上位 n 頭流し買いの的中率/ROI."""
    rows = []
    for _, g in df.groupby("race_id"):
        top = g.nlargest(n, score_col)
        for _, r in top.iterrows():
            if pd.notna(r["win_odds"]) and r["win_odds"] > 0:
                rows.append({
                    "win": int(r["finish_position"] == 1),
                    "odds": r["win_odds"],
                })
    if not rows:
        return {}
    rdf = pd.DataFrame(rows)
    bets = len(rdf)
    wins = rdf["win"].sum()
    roi = (rdf.loc[rdf["win"] == 1, "odds"].sum()) / bets * 100
    return {
        "n_top": n, "bets": bets, "wins": int(wins),
        "win_rate_%": round(float(wins / bets * 100), 2),
        "tansho_roi_%": round(float(roi), 2),
    }


def metrics_high_odds(df: pd.DataFrame, score_col: str, min_odds: float = 10.0) -> dict:
    """高オッズ帯 (1位馬がオッズ ≥ min_odds) 限定 ROI."""
    top1 = df.loc[df.groupby("race_id")[score_col].idxmax()].copy()
    valid = top1[(top1["win_odds"].notna()) & (top1["win_odds"] >= min_odds)]
    if valid.empty:
        return {}
    bets = len(valid)
    wins = (valid["finish_position"] == 1).sum()
    payout = valid.loc[valid["finish_position"] == 1, "win_odds"].sum()
    return {
        "min_odds": min_odds, "bets": bets, "wins": int(wins),
        "win_rate_%": round(float(wins / bets * 100), 2),
        "tansho_roi_%": round(float(payout / bets * 100), 2),
    }


# ============================================================================
# メイン
# ============================================================================

def run(start: str, end: str, min_coverage: float, normalize: bool) -> None:
    df = load_data(start, end)
    if df.empty:
        logger.error("データがありません")
        return

    df = filter_dm_races(df, min_coverage=min_coverage)
    df = fill_dm_with_race_mean(df)

    # ─ DM 単体の評価 ─
    print("\n" + "=" * 80)
    print(f"DM 単体評価  期間: {start}〜{end}  カバレッジ≥{min_coverage:.0%}")
    print("=" * 80)
    print(f"対象レース数: {df['race_id'].nunique():,}")
    print(f"対象馬数:     {len(df):,}")
    print()

    # baseline (DM 抜き=既存総合)
    df["score_base"] = recompose(df, 0.0, 0.0, normalize=normalize)

    # DM 単体
    df["score_time_dm"] = df["jvan_time_dm"]
    df["score_battle_dm"] = df["jvan_battle_dm"]

    print("【単体指数の予測力】")
    print(f"{'指数':16s} {'勝率%':>8s} {'複勝率%':>8s} {'単勝ROI%':>10s} {'相関ρ':>8s}")
    for label, col in [
        ("既存総合 (DM抜)", "score_base"),
        ("DM time 単体", "score_time_dm"),
        ("DM battle 単体", "score_battle_dm"),
    ]:
        m = metrics_from_score(df, col)
        print(
            f"{label:16s} {m['win_rate_%']:>8.2f} {m['place_rate_%']:>8.2f} "
            f"{m['tansho_roi_%']:>10.2f} {m['spearman_rho']:>8.4f}"
        )

    # ─ DM ウェイト感度 (time/battle 同重で振る) ─
    print("\n【DM ウェイト感度 (time=battle, 残りはベース合計1.0で正規化={})】".format(
        "ON" if normalize else "OFF"
    ))
    print(
        f"{'w_time':>7s} {'w_battle':>9s} "
        f"{'勝率%':>7s} {'複勝率%':>8s} {'単勝ROI%':>10s} {'相関ρ':>8s} "
        f"{'top3ROI%':>10s} {'高odds≥10 ROI%':>16s}"
    )
    for w in [0.00, 0.02, 0.05, 0.10, 0.15, 0.20, 0.30, 0.40]:
        df["score"] = recompose(df, w, w, normalize=normalize)
        m = metrics_from_score(df, "score")
        m3 = metrics_top_n(df, "score", n=3)
        mh = metrics_high_odds(df, "score", min_odds=10.0)
        print(
            f"{w:>7.2f} {w:>9.2f} "
            f"{m['win_rate_%']:>7.2f} {m['place_rate_%']:>8.2f} "
            f"{m['tansho_roi_%']:>10.2f} {m['spearman_rho']:>8.4f} "
            f"{m3.get('tansho_roi_%', float('nan')):>10.2f} "
            f"{mh.get('tansho_roi_%', float('nan')):>16.2f}"
        )

    # ─ time / battle 別感度 ─
    print("\n【time DM 単独ウェイト感度 (battle=0)】")
    print(f"{'w_time':>7s} {'勝率%':>7s} {'単勝ROI%':>10s} {'相関ρ':>8s}")
    for w in [0.00, 0.05, 0.10, 0.20, 0.30]:
        df["score"] = recompose(df, w, 0.0, normalize=normalize)
        m = metrics_from_score(df, "score")
        print(f"{w:>7.2f} {m['win_rate_%']:>7.2f} {m['tansho_roi_%']:>10.2f} {m['spearman_rho']:>8.4f}")

    print("\n【battle DM 単独ウェイト感度 (time=0)】")
    print(f"{'w_battle':>9s} {'勝率%':>7s} {'単勝ROI%':>10s} {'相関ρ':>8s}")
    for w in [0.00, 0.05, 0.10, 0.20, 0.30]:
        df["score"] = recompose(df, 0.0, w, normalize=normalize)
        m = metrics_from_score(df, "score")
        print(f"{w:>9.2f} {m['win_rate_%']:>7.2f} {m['tansho_roi_%']:>10.2f} {m['spearman_rho']:>8.4f}")

    # ─ DM とベース指数の相関 ─
    print("\n【DM と既存指数の馬単位ピアソン相関】")
    df_corr = df[[
        "score_base", "speed_index", "last_3f_index", "course_aptitude",
        "pedigree_index", "jockey_index",
        "jvan_time_dm", "jvan_battle_dm",
    ]].dropna()
    cm = df_corr.corr().round(3)
    print(cm[["jvan_time_dm", "jvan_battle_dm"]].to_string())

    # ─ サーフェイス別 ─
    print("\n【サーフェイス別 ROI 比較 (DM抜き vs DM入り w=0.05/0.05)】")
    df["score_v22"] = recompose(df, 0.0, 0.0, normalize=normalize)
    df["score_v23"] = recompose(df, 0.05, 0.05, normalize=normalize)
    print(f"{'カテゴリ':28s} {'レース':>8s} {'v22 ROI':>9s} {'v23 ROI':>9s} {'差':>8s}")

    def _seg(s, d):
        if not isinstance(s, str):
            return "不明"
        is_turf = s.startswith("芝")
        is_dirt = s.startswith("ダ")
        if is_turf:
            base = "芝"
        elif is_dirt:
            base = "ダート"
        else:
            return s
        if pd.isna(d):
            return base
        if d <= 1400:
            return f"{base}スプリント(≤1400)"
        if d <= 1800:
            return f"{base}マイル(1401-1800)"
        if d <= 2400:
            return f"{base}中距離(1801-2400)"
        return f"{base}長距離(≥2401)"

    df["seg"] = df.apply(lambda r: _seg(r["surface"], r["distance"]), axis=1)
    for seg in sorted(df["seg"].unique()):
        sub = df[df["seg"] == seg]
        if sub["race_id"].nunique() < 30:
            continue
        m22 = metrics_from_score(sub, "score_v22")
        m23 = metrics_from_score(sub, "score_v23")
        print(
            f"{seg:28s} {sub['race_id'].nunique():>8d} "
            f"{m22['tansho_roi_%']:>9.2f} {m23['tansho_roi_%']:>9.2f} "
            f"{m23['tansho_roi_%']-m22['tansho_roi_%']:>+8.2f}"
        )


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--start", required=True, help="YYYYMMDD")
    p.add_argument("--end", required=True, help="YYYYMMDD")
    p.add_argument("--min-coverage", type=float, default=0.8, help="DM カバレッジ最小値 (default 0.8)")
    p.add_argument("--no-normalize", action="store_true",
                   help="DM を上乗せ加算 (合計1.10) 形式で評価")
    args = p.parse_args()
    run(args.start, args.end, args.min_coverage, normalize=not args.no_normalize)


if __name__ == "__main__":
    main()
