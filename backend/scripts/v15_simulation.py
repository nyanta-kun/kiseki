"""v15 セグメント別ウェイト シミュレーション

v14 の指数データ（各単体指数）を再利用して v15 の composite を再計算し、
バックフィルなしで v14 vs v15 のROI比較を行う。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv
load_dotenv(_root.parent / ".env")

sys.path.insert(0, str(_here.parent))
import backtest as bt
from src.indices.composite import _segment_weights, DEFAULT_INDEX, INDEX_MIN, INDEX_MAX
from src.utils.constants import INDEX_WEIGHTS

_INTER_W = 0.013333


def _recalc_composite(row: pd.Series, w: dict) -> float:
    """v15 の composite を再計算する（交互作用項込み）。"""
    def g(col: str) -> float:
        v = row.get(col)
        return float(v) if v is not None and not (isinstance(v, float) and np.isnan(v)) else DEFAULT_INDEX

    speed = g("speed_index")
    last3f = g("last3f_index")
    course = g("course_aptitude")
    pos = g("position_advantage")
    rotation = g("rotation_index")
    jockey = g("jockey_index")
    pace = g("pace_index")
    pedigree = g("pedigree_index")
    training = g("training_index")
    anagusa = g("anagusa_index")
    paddock = g("paddock_index")
    rebound = g("rebound_index")
    rivals = g("rivals_growth_index")
    career = g("career_phase_index")
    dist_chg = g("distance_change_index")
    jt_combo = g("jockey_trainer_combo_index")
    going_ped = g("going_pedigree_index")

    base = (
        speed * w["speed"]
        + last3f * w["last_3f"]
        + course * w["course_aptitude"]
        + pace * w["pace"]
        + jockey * w["jockey_trainer"]
        + pedigree * w["pedigree"]
        + rotation * w["rotation"]
        + training * w["training"]
        + pos * w["position_advantage"]
        + anagusa * w["anagusa"]
        + paddock * w["paddock"]
        + rebound * w["disadvantage_bonus"]
        + rivals * w["rivals_growth"]
        + career * w["career_phase"]
        + dist_chg * w["distance_change"]
        + jt_combo * w["jockey_trainer_combo"]
        + going_ped * w["going_pedigree"]
    )
    upside = (
        last3f * pedigree / 100.0 * _INTER_W
        + pos * pedigree / 100.0 * _INTER_W
        + jockey * pedigree / 100.0 * _INTER_W
        + speed * pedigree / 100.0 * _INTER_W
        + rotation * pedigree / 100.0 * _INTER_W
    )
    return round(max(INDEX_MIN, min(INDEX_MAX, base + upside)), 1)


def _classify_segment(surface: str | None, distance: float | None) -> str:
    if not isinstance(surface, str):
        surf = "その他"
    elif surface.startswith("芝"):
        surf = "芝"
    elif surface.startswith("ダ"):
        surf = "ダート"
    else:
        surf = "その他"

    if pd.isna(distance):
        dist = "不明"
    else:
        d = int(distance)
        if d <= 1400:
            dist = "スプリント(～1400)"
        elif d <= 1800:
            dist = "マイル(1401-1800)"
        elif d <= 2400:
            dist = "中距離(1801-2400)"
        else:
            dist = "長距離(2401+)"
    return f"{surf}×{dist}"


def simulate(df: pd.DataFrame) -> None:
    """v14 vs v15 ROI比較を実行する。"""
    # セグメント列追加
    df = df.copy()
    df["segment"] = df.apply(
        lambda r: _classify_segment(r["surface"], r["distance"]), axis=1
    )

    # レースごとのセグメントウェイトを取得してv15 composite を計算
    seg_weights_cache: dict[str, dict] = {}

    def get_w(surface: str | None, distance: float | None) -> dict:
        key = f"{surface}_{distance}"
        if key not in seg_weights_cache:
            dist_int = int(distance) if not pd.isna(distance) else None
            seg_weights_cache[key] = _segment_weights(surface, dist_int)
        return seg_weights_cache[key]

    df["composite_v15"] = df.apply(
        lambda r: _recalc_composite(r, get_w(r["surface"], r["distance"])),
        axis=1,
    )

    # ─ セグメント別 ROI 比較 ────────────────────────────────────────
    print("\n" + "=" * 76)
    print("  v14 vs v15 セグメント別 単勝ROI シミュレーション")
    print("=" * 76)
    print(f"  {'セグメント':<30} {'レース数':>8} {'v14 ROI':>9} {'v15 ROI':>9} {'差分':>8}")
    print(f"  {'─'*68}")

    total_v14 = {"bets": 0, "payout": 0.0}
    total_v15 = {"bets": 0, "payout": 0.0}

    segment_rows = []
    for seg, gdf in df.groupby("segment"):
        n = gdf["race_id"].nunique()
        if n < 30:
            continue

        # v14: composite_index で1位を選ぶ
        top1_v14 = gdf.loc[gdf.groupby("race_id")["composite_index"].idxmax()]
        valid_v14 = top1_v14[top1_v14["win_odds"].notna() & (top1_v14["win_odds"] > 0)]

        # v15: composite_v15 で1位を選ぶ
        top1_v15 = gdf.loc[gdf.groupby("race_id")["composite_v15"].idxmax()]
        valid_v15 = top1_v15[top1_v15["win_odds"].notna() & (top1_v15["win_odds"] > 0)]

        def _roi(valid: pd.DataFrame) -> float:
            if len(valid) == 0:
                return 0.0
            payout = valid.loc[valid["finish_position"] == 1, "win_odds"].sum()
            return float(payout / len(valid) * 100)

        roi14 = _roi(valid_v14)
        roi15 = _roi(valid_v15)
        diff = roi15 - roi14

        total_v14["bets"] += len(valid_v14)
        total_v14["payout"] += valid_v14.loc[valid_v14["finish_position"] == 1, "win_odds"].sum()
        total_v15["bets"] += len(valid_v15)
        total_v15["payout"] += valid_v15.loc[valid_v15["finish_position"] == 1, "win_odds"].sum()

        marker = " ✅" if diff > 2 else (" ❌" if diff < -2 else "")
        segment_rows.append((seg, n, roi14, roi15, diff))
        print(
            f"  {seg:<30} {n:>8,} {roi14:>8.1f}% {roi15:>8.1f}% {diff:>+7.1f}%{marker}"
        )

    # 合計
    total_roi14 = total_v14["payout"] / total_v14["bets"] * 100 if total_v14["bets"] > 0 else 0
    total_roi15 = total_v15["payout"] / total_v15["bets"] * 100 if total_v15["bets"] > 0 else 0
    print(f"  {'─'*68}")
    print(
        f"  {'【全セグメント合計】':<30} {total_v14['bets']:>8,} "
        f"{total_roi14:>8.1f}% {total_roi15:>8.1f}% {total_roi15 - total_roi14:>+7.1f}%"
    )

    # ─ 的中馬が変わったレースの分析 ─────────────────────────────────
    changed = 0
    improved = 0
    worsened = 0
    for race_id, grp in df.groupby("race_id"):
        top_v14 = grp.loc[grp["composite_index"].idxmax(), "horse_id"]
        top_v15 = grp.loc[grp["composite_v15"].idxmax(), "horse_id"]
        if top_v14 != top_v15:
            changed += 1
            actual_winner = grp[grp["finish_position"] == 1]["horse_id"].values
            if len(actual_winner) > 0:
                w14_won = top_v14 in actual_winner
                w15_won = top_v15 in actual_winner
                if w15_won and not w14_won:
                    improved += 1
                elif w14_won and not w15_won:
                    worsened += 1

    n_races = df["race_id"].nunique()
    print(f"\n  【予測変化分析】")
    print(f"  1位馬が変わったレース: {changed:,} / {n_races:,} ({changed/n_races*100:.1f}%)")
    print(f"  v15で改善（v14外れ→v15当たり）: {improved:,}レース")
    print(f"  v15で悪化（v14当たり→v15外れ）: {worsened:,}レース")
    print(f"  純改善: {improved - worsened:+,}レース")
    print("=" * 76 + "\n")


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="v15 セグメントウェイト シミュレーション")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--version", type=int, default=14)
    args = parser.parse_args()

    df = bt.load_data(args.start, args.end, version=args.version)
    if df.empty:
        print("データなし")
        return
    df = bt.filter_valid_races(df)
    if df.empty:
        print("有効レースなし")
        return
    simulate(df)


if __name__ == "__main__":
    main()
