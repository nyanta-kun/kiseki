"""レース信頼度（0〜100%）。正本は同ディレクトリの `keirin_p3_calibration.py`。

🔴 **計算式や較正係数をここへ写さないこと。** 写した瞬間に、入稿のゲートが見る値と
   画面に出る値がずれる（keirin 側は同じ正本をファイル読み込みで束縛している）。

⚠️ 2026-08-25: 当初は正本を keirin 側に置き、こちらがファイル読み込みで束縛して
   いたが、**backend イメージのビルドコンテキストは `./backend` なので
   コンテナに `keirin/` が無く**、デプロイでコンテナが起動に失敗した。
   正本を kiseki 側へ移し、`marquee` / `cup_grade` と同じ向きに揃えた。
"""
from __future__ import annotations

from .keirin_p3_calibration import (
    CONFIDENCE_FULL_SUM,
    confidence_axes,
    confidence_pct,
)

__all__ = [
    "CONFIDENCE_FULL_SUM",
    "confidence_axes",
    "confidence_from_entries",
    "confidence_hit_count_from_entries",
    "confidence_pct",
]


def _probs_of(entries) -> dict[int, float]:
    probs: dict[int, float] = {}
    for e in entries or []:
        v = e.get("pred_top3_pct")
        fno = e.get("frame_no")
        if v is None or fno is None:
            continue
        probs[int(fno)] = float(v) / 100.0
    return probs


def confidence_from_entries(entries, race_type=None, cup_grade=None) -> int | None:
    """出走表の行（`pred_top3_pct` を持つ dict）からレース信頼度を出す。

    ⚠️ `pred_top3_pct` は **%スケール**で入っているので 0-1 へ直して渡す。
       ここを間違えると常に 100% になる（正本は 0-1 前提）。
    """
    probs = _probs_of(entries)
    if len(probs) < 2:
        return None
    return confidence_pct(probs, race_type, cup_grade)


def confidence_hit_count_from_entries(entries) -> int | None:
    """信頼度が見ている2車のうち**何車が3着以内に入ったか**（0 / 1 / 2）。

    表示は 2→○ / 1→△ / 0→× （2026-08-25 ユーザー指定）。
    **1軸だけ的中も情報**なので ○×の二値に潰さない。

    🔴 **信頼度は「軸2車がどちらも3着内に入る」確からしさ**なので、答え合わせも
       同じ2車で行う（`confidence_axes`）。買い目の的中とは別物で、
       買い目が外れていても二軸はそろっていることがある（相手が外れた場合）。

    ⚠️ **着順が1つでも欠けていたら None**（＝「まだ出さない」）。
       欠けたまま 0 を返すと、発走前や取消のレースに × が付いて
       「外れた」と読まれる。
    """
    probs = _probs_of(entries)
    axes = confidence_axes(probs)
    if axes is None:
        return None
    order: dict[int, int] = {}
    for e in entries or []:
        fno, fo = e.get("frame_no"), e.get("finish_order")
        if fno is not None and fo is not None:
            order[int(fno)] = int(fo)
    if any(a not in order for a in axes):
        return None
    return sum(1 for a in axes if 1 <= order[a] <= 3)
