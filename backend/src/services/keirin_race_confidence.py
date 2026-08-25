"""レース信頼度（0〜100%）。正本は同ディレクトリの `keirin_p3_calibration.py`。

🔴 **計算式や較正係数をここへ写さないこと。** 写した瞬間に、入稿のゲートが見る値と
   画面に出る値がずれる（keirin 側は同じ正本をファイル読み込みで束縛している）。

⚠️ 2026-08-25: 当初は正本を keirin 側に置き、こちらがファイル読み込みで束縛して
   いたが、**backend イメージのビルドコンテキストは `./backend` なので
   コンテナに `keirin/` が無く**、デプロイでコンテナが起動に失敗した。
   正本を kiseki 側へ移し、`marquee` / `cup_grade` と同じ向きに揃えた。
"""
from __future__ import annotations

from .keirin_p3_calibration import CONFIDENCE_FULL_SUM, confidence_pct

__all__ = ["CONFIDENCE_FULL_SUM", "confidence_pct", "confidence_from_entries"]


def confidence_from_entries(entries, race_type=None, cup_grade=None) -> int | None:
    """出走表の行（`pred_top3_pct` を持つ dict）からレース信頼度を出す。

    ⚠️ `pred_top3_pct` は **%スケール**で入っているので 0-1 へ直して渡す。
       ここを間違えると常に 100% になる（正本は 0-1 前提）。
    """
    probs = {}
    for e in entries or []:
        v = e.get("pred_top3_pct")
        fno = e.get("frame_no")
        if v is None or fno is None:
            continue
        probs[int(fno)] = float(v) / 100.0
    if len(probs) < 2:
        return None
    return confidence_pct(probs, race_type, cup_grade)
