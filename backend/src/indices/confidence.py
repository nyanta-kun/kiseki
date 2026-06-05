"""レース信頼度・推奨度算出モジュール

DBアクセスなし・純粋関数として実装。
`get_indices` APIで取得済みの composite_index リストから算出する。
"""

from __future__ import annotations

import statistics


def score_to_rank(score: int) -> str:
    """信頼度スコア (0-100) → ランク (S/A/B/C)"""
    if score >= 80:
        return "S"
    if score >= 65:
        return "A"
    if score >= 50:
        return "B"
    return "C"


def calculate_recommend_rank(
    confidence_score: int,
    win_prob_top: float | None = None,
    win_odds_top: float | None = None,
) -> str:
    """推奨度ランク（=本命の堅さ・信頼度tier）を算出する (S/A/B/C)。

    ⚠️ 再定義 (2026-06-05): 旧ロジックは EV=win_prob×odds の閾値(EV>2.0等)で
    判定していたが、Phase2 で win_probability を較正(is_win)に変えた結果、
    OOS検証(2025.7-2026.6, 10,883R)で **完全に逆転**した
    (旧S=勝率14.4% / 旧C=勝率43.6%・ROIもS優位なし)。EVベース価値選別は
    回収率に直結しないことも判明済みのため EVゲートを廃止し、検証で1位馬勝率が
    単調になる「信頼度tier」へ再定義する。

    検証済み tier（1位=composite最上位馬の勝率 / 複勝率）:
      S 鉄板 : 指数1位が断然人気(単勝<1.5)      → 70.5% / 92.2%
      A 信頼 : confidence_score >= 80 (conf S)   → 41.0% / 72.7%
      B      : confidence_score >= 65 (conf A)   → 24.4% / 57.1%
      C 混戦 : 上記以外                          → ~20%

    ※ 高オッズの「妙味穴」は別軸（is_sweet_spot・回収率重視）。recommend_rank は
       「的中重視の本命の堅さ」。統一取捨: sweet_spot馬がいれば妙味穴(単勝) >
       recommend S 鉄板 > A 信頼軸 > 見送り。

    Args:
        confidence_score: 信頼度スコア (0-100)
        win_prob_top:     予測1位馬の勝率（互換のため残置・未使用）
        win_odds_top:     予測1位馬（composite最上位）の単勝オッズ。None=未取得

    Returns:
        "S" | "A" | "B" | "C"
    """
    # S: 指数1位が断然人気（単勝 < 1.5）= 鉄板本命
    if win_odds_top is not None and win_odds_top < 1.5:
        return "S"
    # 以降は信頼度スコアのみ（オッズ未取得でも評価可能）
    if confidence_score >= 80:
        return "A"
    if confidence_score >= 65:
        return "B"
    return "C"


def calculate_race_confidence(
    composite_indices: list[float],
    head_count: int | None,
    win_probabilities: list[float] | None = None,
) -> dict:
    """レース信頼度スコアを算出する（0〜100）。

    スコア構成:
        - 指数差スコア  (40点): 1位と2位・3位の差の大きさ
        - 頭数スコア    (20点): 少頭数ほど荒れにくい
        - 分散スコア    (25点): 全馬の指数分布が分離しているか
        - 勝率集中スコア(15点): 1位の勝率が突出しているか、2番人気以降が拮抗していないか

    Args:
        composite_indices: 全出走馬の総合指数リスト（順不同可）
        head_count:        出走頭数。None の場合はリスト長を使用
        win_probabilities: 全出走馬の勝率リスト（composite_indices と対応順）。
                           None の場合は勝率集中スコアをスキップ

    Returns:
        score (int 0-100), label (HIGH/MID/LOW), rank (S/A/B/C),
        gap_1_2 (float), gap_1_3 (float), head_count (int),
        win_prob_top (float | None)
    """
    if not composite_indices:
        return {
            "score": 0,
            "label": "LOW",
            "rank": "C",
            "gap_1_2": 0.0,
            "gap_1_3": 0.0,
            "head_count": head_count or 0,
            "win_prob_top": None,
        }

    n = head_count if head_count is not None else len(composite_indices)
    sorted_idx = sorted(composite_indices, reverse=True)

    # --- 指数差スコア (40点) ---
    gap_1_2 = sorted_idx[0] - sorted_idx[1] if len(sorted_idx) >= 2 else 0.0
    gap_1_3 = sorted_idx[0] - sorted_idx[2] if len(sorted_idx) >= 3 else gap_1_2
    weighted_gap = gap_1_2 * 0.7 + gap_1_3 * 0.3
    # 10点差で満点（指数の標準偏差≈10 を基準）
    gap_score = min(weighted_gap / 10.0, 1.0) * 40.0

    # --- 頭数スコア (20点) ---
    # 8頭以下=満点, 18頭=0点
    head_score = max(0.0, (18 - n) / 10.0) * 20.0

    # --- 分散スコア (25点) ---
    dispersion_score = 0.0
    if len(sorted_idx) >= 2:
        std_dev = statistics.stdev(sorted_idx)
        # 標準偏差8を満点閾値
        dispersion_score = min(std_dev / 8.0, 1.0) * 25.0

    # --- 勝率集中スコア (15点) ---
    win_prob_concentration_score = 0.0
    win_prob_top: float | None = None
    if win_probabilities and len(win_probabilities) >= 2:
        sorted_probs = sorted(win_probabilities, reverse=True)
        win_prob_top = sorted_probs[0]
        # 1位が50%超なら高スコア、2位以降が拮抗しているほど低スコア
        # 1位の優位性: prob[0] - prob[1]
        prob_gap = sorted_probs[0] - sorted_probs[1]
        # 勝率差20%で満点
        win_prob_concentration_score = min(prob_gap / 0.20, 1.0) * 15.0
        # 1位の絶対値ボーナス（40%超で追加5点、上限内）
        if sorted_probs[0] >= 0.40:
            win_prob_concentration_score = min(win_prob_concentration_score + 5.0, 15.0)

    total = round(gap_score + head_score + dispersion_score + win_prob_concentration_score)
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
        "rank": score_to_rank(total),
        "gap_1_2": round(gap_1_2, 1),
        "gap_1_3": round(gap_1_3, 1),
        "head_count": n,
        "win_prob_top": round(win_prob_top, 4) if win_prob_top is not None else None,
    }
