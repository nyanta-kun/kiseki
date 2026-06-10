"""人気薄リランカー軸（A2/A3）の運用精度モニタ.

直近 N 日の確定レースに対しアーティファクトで軸判定を再現し、
複勝圏精度を帯ベースラインと比較する。撤退基準の監視が目的:
「A2 精度が帯ベース(~27%)を下回り続けたら停止」(memory: upset_place_extraction)。

※確定オッズで判定するため、ライブ判定(発走前オッズ)より +3pt 程度楽観
  （発走前-10分判定 34.8% vs 確定判定 37.6%, 2026-03〜06 検証）。

使い方:
  cd backend
  .venv/bin/python scripts/monitor_upset_picks.py [--days 30]
終了コード: A2 精度 < 帯ベースのとき 1（アラート連携用）。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

_root = Path(__file__).resolve().parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import numpy as np

from scripts.train_upset_reranker import ARTIFACT_PATH, load_dataset, score_with_artifact
from src.indices.upset_reranker import UPSET_BAND_MAX, UPSET_BAND_MIN


def main() -> None:
    """エントリポイント."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--artifact", default=str(ARTIFACT_PATH))
    args = parser.parse_args()

    artifact = json.loads(Path(args.artifact).read_text())
    start = (date.today() - timedelta(days=args.days)).strftime("%Y%m%d")
    uni = load_dataset(start, artifact.get("indices_version", 26))
    uni = uni[uni.fp.notna()]
    if uni.empty:
        print("対象データなし")
        return

    band = uni[(uni.win_odds >= UPSET_BAND_MIN) & (uni.win_odds < UPSET_BAND_MAX)].copy()
    band["ns"] = score_with_artifact(artifact, band)
    th = artifact["threshold"]
    a2 = band[(band.ns >= th) & (band.badge_cnt >= 1)]
    a3 = band[(band.ns >= th) & (band.badge_cnt >= 2)]

    base = float(band.top3.mean())
    print(f"=== 人気薄リランカー軸 精度モニタ ({start}〜, {band.race_id.nunique()}R) ===")
    print(f"帯[10,15)ベース: n={len(band)} 複勝圏率={base:.3f}")
    for label, s in [("A2(バッジ1+)", a2), ("A3(バッジ2+)", a3)]:
        if len(s) == 0:
            print(f"{label}: picks=0")
            continue
        print(f"{label}: picks={len(s)} 精度={float(s.top3.mean()):.3f} "
              f"平均オッズ={float(s.win_odds.mean()):.1f}")
    # 週次推移
    band["week"] = (band.date // 100).astype(str) + "-w" + (
        (np.minimum(band.date % 100, 28) - 1) // 7 + 1
    ).astype(int).astype(str)
    a2w = band[(band.ns >= th) & (band.badge_cnt >= 1)]
    if len(a2w):
        print("\n-- A2 週次 --")
        for wk, g in a2w.groupby("week"):
            print(f"  {wk}: {int(g.top3.sum())}/{len(g)} ({float(g.top3.mean()):.3f})")

    if len(a2) >= 30 and float(a2.top3.mean()) < base:
        print("\n⚠️ ALERT: A2 精度が帯ベースを下回っています（撤退基準に抵触）")
        sys.exit(1)


if __name__ == "__main__":
    main()
