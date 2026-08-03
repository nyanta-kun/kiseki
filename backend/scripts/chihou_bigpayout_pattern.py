"""地方競馬 大穴決着レースの事前特徴パターン検証（2026-07-23、ユーザー提案）。

「不人気馬の条件を当てる」のではなく、逆方向から: 実際に大穴決着（複勝4倍以上・
三連複30/50/100倍以上）が発生したレースを payout テーブルから特定し、それらのレースが
gap12/top2_share/fav_odds/頭数等の事前特徴（chihou_race_upset_index.py で計算した
レース単位指標）でどう違うかを比較する。

「条件から当てにいく」→「結果から逆引きして事前に判別可能な特徴を探す」というケース
コントロール型の検証。chihou_rebuild_walkforward.py --dump-csv の walk-forward honest
予測結果と chihou.race_payouts を race_id で突き合わせる。

使い方:
  cd backend
  .venv/bin/python scripts/chihou_bigpayout_pattern.py --csv /path/to/chihou_wf_full.csv
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
from dotenv import load_dotenv

_root = Path(__file__).resolve().parents[1]
load_dotenv(_root.parent / ".env")

DSN = (
    f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
    f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
    f"password={os.getenv('DB_PASSWORD')}"
)


def build_race_features(df: pd.DataFrame) -> pd.DataFrame:
    """chihou_race_upset_index.py と同一ロジックでレース単位特徴量を作る。"""
    rows = []
    for race_id, g in df.groupby("race_id"):
        g = g.sort_values("composite_wf", ascending=False)
        if len(g) < 2:
            continue
        scores = g["composite_wf"].to_numpy()
        odds = pd.to_numeric(g["win_odds"], errors="coerce").dropna().sort_values().to_numpy()
        gap12 = float(scores[0] - scores[1])
        total_score = float(scores.sum())
        top2_share = float((scores[0] + scores[1]) / total_score) if total_score > 0 else np.nan
        fav_odds = float(odds[0]) if len(odds) >= 1 else np.nan
        odds_gap12 = float(odds[1] - odds[0]) if len(odds) >= 2 else np.nan
        rows.append({
            "race_id": race_id,
            "head_count": g["head_count"].iloc[0],
            "course_name": g["course_name"].iloc[0],
            "quarter": g["quarter"].iloc[0],
            "gap12": gap12,
            "top2_share": top2_share,
            "fav_odds": fav_odds,
            "odds_gap12": odds_gap12,
        })
    return pd.DataFrame(rows)


def fetch_payouts(race_ids: list[int]) -> pd.DataFrame:
    conn = psycopg2.connect(DSN)
    cur = conn.cursor()
    cur.execute(
        "SELECT race_id, bet_type, payout FROM chihou.race_payouts "
        "WHERE bet_type IN ('place','trio') AND race_id = ANY(%s)",
        (race_ids,),
    )
    cols = [d[0] for d in cur.description]
    df = pd.DataFrame(cur.fetchall(), columns=cols)
    cur.close()
    conn.close()
    return df


def _describe(label: str, sub: pd.DataFrame, base: pd.DataFrame) -> None:
    print(f"\n  [{label}]  n={len(sub):,}  (全体比 {len(sub)/len(base)*100:.1f}%)")
    for col in ["gap12", "top2_share", "fav_odds", "odds_gap12", "head_count"]:
        print(f"    {col:<12} 該当レース平均={sub[col].mean():.3f}  全体平均={base[col].mean():.3f}  "
              f"差={sub[col].mean()-base[col].mean():+.3f}")
    print(f"    場別内訳(上位5): {sub['course_name'].value_counts().head(5).to_dict()}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--csv", required=True)
    args = p.parse_args()

    df = pd.read_csv(args.csv)
    print(f"読み込み: {len(df):,}行 / {df['race_id'].nunique():,}レース")
    race_feat = build_race_features(df)
    print(f"レース特徴量: {len(race_feat):,}レース")

    payouts = fetch_payouts(race_feat["race_id"].unique().tolist())
    print(f"payout取得: {len(payouts):,}件")

    place_max = payouts[payouts["bet_type"] == "place"].groupby("race_id")["payout"].max()
    trio_max = payouts[payouts["bet_type"] == "trio"].groupby("race_id")["payout"].max()
    race_feat["place_max_payout"] = race_feat["race_id"].map(place_max)
    race_feat["trio_max_payout"] = race_feat["race_id"].map(trio_max)

    base = race_feat.dropna(subset=["place_max_payout"])
    print(f"\n{'='*78}\n  複勝: 大穴決着レースの事前特徴（vs 全体平均）\n{'='*78}")
    print(f"  [全体]  n={len(base):,}  gap12平均={base['gap12'].mean():.3f}  "
          f"top2_share平均={base['top2_share'].mean():.3f}  fav_odds平均={base['fav_odds'].mean():.2f}")
    for th in [400, 600, 1000]:
        sub = base[base["place_max_payout"] >= th]
        _describe(f"複勝最高配当 >= {th/100:.0f}倍", sub, base)

    base_t = race_feat.dropna(subset=["trio_max_payout"])
    print(f"\n{'='*78}\n  三連複: 大穴決着レースの事前特徴（vs 全体平均）\n{'='*78}")
    print(f"  [全体]  n={len(base_t):,}  gap12平均={base_t['gap12'].mean():.3f}  "
          f"top2_share平均={base_t['top2_share'].mean():.3f}  fav_odds平均={base_t['fav_odds'].mean():.2f}")
    for th in [3000, 5000, 10000]:
        sub = base_t[base_t["trio_max_payout"] >= th]
        _describe(f"三連複 >= {th/100:.0f}倍", sub, base_t)

    print(f"\n{'='*78}\n注意: これは「結果から見た事前特徴の平均差」の記述統計。"
          f"差が大きく見えても、実際に閾値化して事前フィルタとして使う場合は\n"
          f"別途 hit_rate/ROI で検証すること（多重比較・過学習に注意）。\n{'='*78}")


if __name__ == "__main__":
    main()
