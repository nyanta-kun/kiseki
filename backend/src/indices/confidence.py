"""レース信頼度・推奨度算出モジュール

DBアクセスなし・純粋関数として実装。
`get_indices` APIで取得済みの composite_index リストから算出する。
"""

from __future__ import annotations

import math
import statistics

# 指数差スコアが満点になる「1位と2〜3位の加重差」。
# 総合指数のスケール（レース内の広がり）に依存する較正値。
#   - DEFAULT (10.0): 地方競馬など従来スケール向け。「指数の標準偏差≈10」を基準にした値
#   - JRA_V27 (6.0) : JRA v27（順位回帰+着外率合成）向け。
#     v27 は指数分布が対称的になり 1位-2位の差が v26 比で約 0.75 倍に縮む。
#     10.0 のままだと confidence_score が下振れし tier S が 19.0%→8.4% に半減する。
#     6.0 にすると v26 の分布（平均 63.2 / S 19.0% / A以上 46.7%）をほぼ再現する
#     （実測: 平均 63.5 / S 20.0% / A以上 46.7%、test 2026-01〜08 2,046R）。
#     ※ tier の閾値（S>=80, A>=65）は OOS 検証済みのため動かさず、
#        スケール差だけをこの係数で吸収する。
#   - CHIHOU (12.0): 地方 v13（min-max 廃止・中心化線形スケール C=40）向け。下記参照。
DEFAULT_GAP_FULL_SCORE: float = 10.0
JRA_GAP_FULL_SCORE: float = 6.0
CHIHOU_GAP_FULL_SCORE: float = 12.0

# 分散スコアが満点になる「レース内 composite の標準偏差」。
# 2026-08-02 の実測で、この閾値が地方では**完全に飽和して定数化**していたことが判明した:
#   地方 v10/v12（min-max 15-85）は全レースで幅が 70.00 固定 → 平均 sd 23.6 で
#   **100% のレースが満点**。指数差スコアも 63% が満点になり、100点中65点が機能せず、
#   結果として **97% のレースが tier S** という状態だった（memory:
#   chihou_logic_review_2026_08_02）。JRA v27 でも 77% が満点で飽和寄り。
# 地方 v13 では composite を中心化線形スケール（50 + 40*(p − レース内平均)）に変更し、
# 本閾値を 16.0 にすることで分散スコアを非飽和にする。
DEFAULT_DISPERSION_FULL_SCORE: float = 8.0
CHIHOU_DISPERSION_FULL_SCORE: float = 16.0

# tier別 entropy_norm 中央値閾値（Phase3 市場混戦度分析で検証済み、
# memory: jra_new_index_web_research_2026_07_26 / docs: jra_new_index_results.md）。
# 診断期間(2023-07〜2025-12)の中央値をそのまま固定し、確認期間(2026-01〜07)で
# 再現確認済み（tier=Cを本閾値で分割すると複勝的中率が11〜13pt分離）。
ENTROPY_THRESHOLDS: dict[str, float] = {
    "S": 0.6951,
    "A": 0.7414,
    "B": 0.7564,
    "C": 0.7757,
}


def calculate_market_chaos(win_odds: list[float]) -> dict[str, float | None]:
    """単勝オッズから市場混戦度（HHI・正規化Shannon entropy）を算出する。

    全馬の単勝オッズから implied probability（控除率補正込み）を求め、
    HHI（1に近いほど本命一強）と entropy_norm（0〜1、1に近いほど大混戦）を返す。
    有効オッズが3頭未満の場合は算出不能として None を返す。

    Args:
        win_odds: 出走馬の単勝オッズリスト（None・1.0未満の異常値は無視）

    Returns:
        {"hhi": float | None, "entropy_norm": float | None}
    """
    valid = [o for o in win_odds if o is not None and o >= 1.0]
    if len(valid) < 3:
        return {"hhi": None, "entropy_norm": None}

    implied = [1.0 / o for o in valid]
    total = sum(implied)
    if total <= 0:
        return {"hhi": None, "entropy_norm": None}

    probs = [q / total for q in implied]
    hhi = sum(p * p for p in probs)
    shannon = -sum(p * math.log(p) for p in probs if p > 0)
    entropy_norm = shannon / math.log(len(probs))

    return {"hhi": round(hhi, 4), "entropy_norm": round(entropy_norm, 4)}


def score_to_rank(score: int) -> str:
    """信頼度スコア (0-100) → ランク (S/A/B/C)"""
    if score >= 80:
        return "S"
    if score >= 65:
        return "A"
    if score >= 50:
        return "B"
    return "C"


def is_market_favorite(win_odds_top: float | None, all_win_odds: list[float] | None) -> bool | None:
    """指数1位馬が単勝オッズでもレース内最低オッズ（1番人気）と一致するか。

    market_agree（[[jra_axis_market_agree_redesign]]）の入力。全馬オッズが
    揃っていない場合（発走直前以外・取得不能時）は None を返し、呼び出し側で
    `calculate_recommend_rank` の market_agree=None フォールバックに委ねる。
    """
    if win_odds_top is None or not all_win_odds:
        return None
    return win_odds_top <= min(all_win_odds)


def calculate_recommend_rank(
    confidence_score: int,
    win_prob_top: float | None = None,
    win_odds_top: float | None = None,
    market_agree: bool | None = None,
    entropy_norm: float | None = None,
) -> str:
    """推奨度ランク（=本命の堅さ・信頼度tier）を算出する (S/A/B/C/C+)。

    ⚠️ 再定義 (2026-07-25, [[jra_axis_market_agree_redesign]]): 3年+完全OOS
    (2025.7-2026.7) のセグメント異質性分析で、confidence_score（指数gapベース）
    単独より「指数1位馬が単勝オッズでも1番人気と一致するか(market_agree)」の方が
    的中率の分離が支配的と判明。市場乖離グループ(的中15〜26%)と市場一致グループ
    (的中27〜51%)がほぼ重ならず、confidence_score>=80の最上位ですら市場が
    支持していなければ最下位の市場一致グループより弱い
    （train+val: S×乖離21.4% < C×一致27.0%、testでも同様の逆転）。
    market_agree を第一分岐、confidence_score を第二分岐に再構成する。

    検証済み tier（1位=composite最上位馬の勝率、train+val / testOOS）:
      S 最強軸: 断然人気(単勝<1.5) または (市場一致 ∧ confidence_score>=80) → 45〜51% / 70%+(断然人気時)
      A 信頼軸: 市場一致 ∧ confidence_score>=65                              → 33〜40%
      B 準軸  : 市場一致 ∧ confidence_score<65                               → 27〜35%
      C 混戦  : 市場乖離（confidence問わず）                                  → 15〜26%
      market_agree=None（全馬オッズ未取得）時は旧ロジック（confidence_scoreのみ）にフォールバック。

    ※ 高オッズの「妙味穴」は別軸（is_sweet_spot・回収率重視）。recommend_rank は
       「的中重視の本命の堅さ」。統一取捨: sweet_spot馬がいれば妙味穴(単勝) >
       recommend S 最強軸 > A 信頼軸 > 見送り。

    ⚠️ 追加 (2026-07-26, Phase3 市場混戦度分析 [[jra_new_index_web_research_2026_07_26]]):
    tier=C（市場乖離）はさらに entropy_norm（単勝オッズの正規化Shannon entropy、
    `calculate_market_chaos()`参照）で分割できることを診断期間・確認期間の両方で
    確認済み。entropy_norm < ENTROPY_THRESHOLDS["C"]（まだ市場内で本命寄りに
    拮抗している）の場合は "C+"（準見送り、複勝的中率約55%）として区別し、
    それ以外（真の大混戦、複勝的中率約43%）は従来通り "C"（見送り）とする。

    Args:
        confidence_score: 信頼度スコア (0-100)
        win_prob_top:     予測1位馬の勝率（互換のため残置・未使用）
        win_odds_top:     予測1位馬（composite最上位）の単勝オッズ。None=未取得
        market_agree:     指数1位馬が単勝1番人気と一致するか。`is_market_favorite()`で算出。
                           None=全馬オッズ未取得で計算不能
        entropy_norm:     レースの市場混戦度（`calculate_market_chaos()`で算出）。
                           None=算出不能（tier=C内のC+分割は行わずCのまま）

    Returns:
        "S" | "A" | "B" | "C" | "C+"
    """
    # S: 指数1位が断然人気（単勝 < 1.5）= 鉄板本命（market_agree を問わず優先）
    if win_odds_top is not None and win_odds_top < 1.5:
        return "S"

    if market_agree is None:
        # 全馬オッズ未取得等で market_agree 計算不能 → 旧ロジック（confidence_score のみ）
        if confidence_score >= 80:
            return "A"
        if confidence_score >= 65:
            return "B"
        tier = "C"
    elif not market_agree:
        tier = "C"
    elif confidence_score >= 80:
        return "S"
    elif confidence_score >= 65:
        return "A"
    else:
        return "B"

    if entropy_norm is not None and entropy_norm < ENTROPY_THRESHOLDS["C"]:
        return "C+"
    return tier


def calculate_race_confidence(
    composite_indices: list[float],
    head_count: int | None,
    win_probabilities: list[float] | None = None,
    gap_full_score: float = DEFAULT_GAP_FULL_SCORE,
    dispersion_full_score: float = DEFAULT_DISPERSION_FULL_SCORE,
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
        gap_full_score:    指数差スコアが満点になる加重差。総合指数のスケールに合わせる。
                           JRA v27 は `JRA_GAP_FULL_SCORE`、地方 v13 は `CHIHOU_GAP_FULL_SCORE`
        dispersion_full_score:
                           分散スコアが満点になるレース内標準偏差。同じくスケール依存。
                           地方 v13 は `CHIHOU_DISPERSION_FULL_SCORE`

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
    # gap_full_score 点差で満点（指数スケールに依存する較正値。上部の定数コメント参照）
    gap_score = min(weighted_gap / gap_full_score, 1.0) * 40.0

    # --- 頭数スコア (20点) ---
    # 8頭以下=満点, 18頭=0点
    head_score = max(0.0, (18 - n) / 10.0) * 20.0

    # --- 分散スコア (25点) ---
    dispersion_score = 0.0
    if len(sorted_idx) >= 2:
        std_dev = statistics.stdev(sorted_idx)
        # dispersion_full_score で満点（指数スケールに依存する較正値。上部の定数コメント参照）
        dispersion_score = min(std_dev / dispersion_full_score, 1.0) * 25.0

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
