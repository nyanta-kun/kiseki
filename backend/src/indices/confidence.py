"""レース信頼度算出モジュール

DBアクセスなし・純粋関数として実装。
`get_indices` APIで取得済みの composite_index リストから算出する。
"""

from __future__ import annotations

import statistics


def calculate_race_confidence(
    composite_indices: list[float],
    head_count: int | None,
) -> dict:
    """レース信頼度スコアを算出する（0〜100）。

    スコア構成:
        - 指数差スコア  (50点): 1位と2位・3位の差の大きさ
        - 頭数スコア    (20点): 少頭数ほど荒れにくい
        - 分散スコア    (30点): 全馬の指数分布が分離しているか

    Args:
        composite_indices: 全出走馬の総合指数リスト（順不同可）
        head_count: 出走頭数。None の場合はリスト長を使用

    Returns:
        score (int 0-100), label (HIGH/MID/LOW),
        gap_1_2 (float), gap_1_3 (float), head_count (int)
    """
    if not composite_indices:
        return {
            "score": 0,
            "label": "LOW",
            "gap_1_2": 0.0,
            "gap_1_3": 0.0,
            "head_count": head_count or 0,
        }

    n = head_count if head_count is not None else len(composite_indices)
    sorted_idx = sorted(composite_indices, reverse=True)

    # --- 指数差スコア (50点) ---
    gap_1_2 = sorted_idx[0] - sorted_idx[1] if len(sorted_idx) >= 2 else 0.0
    gap_1_3 = sorted_idx[0] - sorted_idx[2] if len(sorted_idx) >= 3 else gap_1_2
    weighted_gap = gap_1_2 * 0.7 + gap_1_3 * 0.3
    # 10点差で満点（指数の標準偏差≈10 を基準）
    gap_score = min(weighted_gap / 10.0, 1.0) * 50.0

    # --- 頭数スコア (20点) ---
    # 8頭以下=満点, 18頭=0点
    head_score = max(0.0, (18 - n) / 10.0) * 20.0

    # --- 分散スコア (30点) ---
    dispersion_score = 0.0
    if len(sorted_idx) >= 2:
        std_dev = statistics.stdev(sorted_idx)
        # 標準偏差8を満点閾値
        dispersion_score = min(std_dev / 8.0, 1.0) * 30.0

    total = round(gap_score + head_score + dispersion_score)
    total = max(0, min(100, total))

    if total >= 70:
        label = "HIGH"
    elif total >= 50:
        label = "MID"
    else:
        label = "LOW"

    return {
        "score": total,
        "label": label,
        "gap_1_2": round(gap_1_2, 1),
        "gap_1_3": round(gap_1_3, 1),
        "head_count": n,
    }
