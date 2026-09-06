"""地方 足切り（Web グレーアウト）ルールの正本。

## なぜこのモジュールがあるか

2026-09-06 まで、このルールは **3 か所に別々に書かれていた**:

  - `frontend/src/components/ChihouRaceDetailClient.tsx`（表示の実体）
  - `backend/scripts/chihou_cutoff_venue_review.py`（検証）
  - `backend/scripts/chihou_cutoff_review.py`（スイープ）

閾値を変えるには 3 か所を同時に直す必要があり、片方だけ直しても**エラーにならず
静かに食い違う**。実際 v14 デプロイ（2026-08-14）後に「除外率 31% の設計」と
「実測 14.5%」が乖離したまま1か月気づかれなかった。JRA 側は既に
「単一真実源はバックエンド（`is_cut_off`）」（`api/races.py:402`）に寄せてあるので、
地方も同じ形に揃える。

## ルール

    gap >= CUT_GAP_HARD  または  (gap >= CUT_GAP_SOFT かつ composite順位 >= CUT_RANK_MIN)

    gap  = レース内の最高 composite_index − その馬の composite_index
    順位 = composite_index の降順（同値は入力順。frontend の sort と同じ）

## ⚠️ 閾値は composite のスケールに依存する（構造的な弱点）

gap は**絶対量**なので、指数の版が変わってレース内のばらつきが変わると
**同じ数値が別の意味になる**。実際に 2 回連続で壊れている:

  - v12→v13: 旧 20/15/5位 が 82.1% の馬を足切りする状態になり再較正（30/24/7）
  - v13→v14: 市場特徴 5 本を外して gap 分布が縮み（p50 20.6→14.9）、
             除外率が設計 31% に対し実測 14.5% まで落ちた

⚠️ **`CHIHOU_INDEX_SCALE` や本番モデルの特徴量を変えたら必ず再較正すること。**
恒久対策としては JRA と同じ「較正済み確率で切る」（`place_probability` ベース）
への移行が候補。`place_probability` は既に DB・API・frontend まで通っている。
"""
from __future__ import annotations

from collections.abc import Sequence

# 足切り閾値。**v14 スケールで walk-forward 較正した値**
# （`scripts/chihou_cutoff_recalibrate.py` / 2026-09-06 / VAL 2025-07〜2026-08 の 2 窓）。
#
# ## 選定の軸（2026-09-06 ユーザー判断）: 1着取りこぼしの上限
#
# 「除外率を揃える」「着外率を揃える」ではなく、**1着取りこぼし <= 3%** を制約に置き
# その下で除外率を最大化した。理由は `chihou_cutoff_recalibrate.py` の docstring。
#
#   ルール      除外率(W1/W2)  着外率      1着落ち     3着内落ち
#   30/24/7     19.4 / 17.7%   93.0/93.0%  1.7/1.7%    4.6/4.2%   ← 旧（余力を使い切っていない）
#   28/22/7     25.1 / 23.3%   92.4/92.0%  2.6/2.7%    6.6/6.3%   ← 採用
#   26/22/7     26.2 / 24.4%   91.8/91.6%  2.8/3.0%    7.3/7.0%   制約の境界上（W1 の CI 上端 3.2%）
#   24/18/7     37.1 / 35.0%   90.5/90.0%  5.3/5.5%   12.0/11.9%  v13 の運用点。v14 では 1着落ちが倍
#
# 参考: v13 の設計値は 除外31.2% / 着外93.6% / 1着落ち2.9% / 3着内落ち6.9%。
# **28/22/7 は v13 とほぼ同じ質のプロファイルを、除外率 25%（v13 は 31%）で達成する。**
# 同じ除外率まで切ろうとすると v14 は 1着取りこぼしが倍になるので、
# 「v13 と同じだけ切る」は v14 では選べない。
#
# 🔴 hard はこの範囲ではほとんど効いていない（gap の p95 が 28.8〜30.3 のため
#    30 以上はまず発火しない）。効いているのは soft 24→22 の側。
CUT_GAP_HARD: float = 28.0
CUT_GAP_SOFT: float = 22.0
CUT_RANK_MIN: int = 7


def cut_flags(
    composite: Sequence[float | None],
    *,
    gap_hard: float = CUT_GAP_HARD,
    gap_soft: float = CUT_GAP_SOFT,
    rank_min: int = CUT_RANK_MIN,
) -> list[bool]:
    """レース1本ぶんの composite_index 列から足切りフラグを返す。

    Args:
        composite: 出走馬の composite_index（入力順を保持。None は指数なし）。
            **出走取消・失格を含む出走馬全体**を渡すこと。順位と gap を確定させる
            母集団を本番 `chihou_recommender.rank_by_hn` と揃えるため
            （memory: chihou_survivor_bias_audit_2026_07_23）。
        gap_hard: 無条件に足切りする最高指数との差。
        gap_soft: 順位条件と併用する差。
        rank_min: soft 側で足切りする最低順位（この順位以下）。

    Returns:
        composite と同じ長さ・同じ順のフラグ列。指数が None の馬は常に False。

    順位は降順・同値は入力順（frontend の sort と同じ挙動）。
    """
    known = [c for c in composite if c is not None]
    if not known:
        return [False] * len(composite)
    top = max(known)

    # 降順・同値は入力順（= pandas の method="first" / JS の安定ソート）
    order = sorted(
        (i for i, c in enumerate(composite) if c is not None),
        key=lambda i: -float(composite[i]),  # type: ignore[arg-type]
    )
    rank_of = {i: r for r, i in enumerate(order, start=1)}

    flags: list[bool] = []
    for i, c in enumerate(composite):
        if c is None:
            flags.append(False)
            continue
        gap = top - float(c)
        rank = rank_of[i]
        flags.append(gap >= gap_hard or (gap >= gap_soft and rank >= rank_min))
    return flags
