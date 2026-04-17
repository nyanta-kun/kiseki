"""購入指針（Buy Signal）算出モジュール。

過去の実績データに基づき、各レースの購入推奨度を3段階で算出する。

JRA（2025-01以降, v17, 4,387レース実績）:
    距離帯 × 指数1位オッズで判定。
    短距離(〜1400m): ROI 84-85%（オッズ問わず） → "pass"
    4.0倍以上 + マイル以上(1401m+): ROI 105.7%+ → "buy"
    3.0-4.0倍 + マイル以上: ROI ~98% → "caution"
    4.0倍未満: ROI 85-94% → "pass"

地方（v8 P1実績 2023-04-16〜2024-04-16, 3,373R）:
    競馬場 × 期待値EV（推定勝率×単勝オッズ）の組み合わせで判定。
    EV最適帯 = 1.0〜2.0（ROI 82-85%）。EV>2.0は大穴不安定（ROI 72%台）。

    [buy]  高ROIコース（高知94.7%/園田91.0%/盛岡） × EV rank S/A
    [caution] 上記コース × EV rank B/C、または中ROIコース × EV rank S/A
    [pass] 中ROIコース × EV rank B/C、または低ROIコース
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# JRA 購入指針
# ---------------------------------------------------------------------------

def jra_buy_signal(distance: int, top_win_odds: float | None) -> str | None:
    """JRA レースの購入指針を算出する。

    Args:
        distance: レース距離（メートル）
        top_win_odds: 指数1位馬の単勝オッズ（None = オッズ未取得）

    Returns:
        "buy" | "caution" | "pass" | None（オッズ未取得）
    """
    if top_win_odds is None:
        return None
    # 短距離はオッズ問わず回収率が改善しない
    if distance <= 1400:
        return "pass"
    # 低オッズは期待値マイナス
    if top_win_odds < 3.0:
        return "pass"
    # 4倍以上 + マイル以上: ROI 105.7%+ で購入圏
    if top_win_odds >= 4.0:
        return "buy"
    # 3.0-4.0倍 + マイル以上: ROI ~98% で損益ほぼ±0
    return "caution"


# ---------------------------------------------------------------------------
# 地方競馬 購入指針
# ---------------------------------------------------------------------------

# 競馬場名 → コースグレード（v8 P1実績 2023-04-16〜2024-04-16）
_CHIHOU_COURSE_GRADE: dict[str, str] = {
    # buy: ROI ≥ 85%
    "高知":   "buy",      # 94.7%
    "園田":   "buy",      # 91.0%
    "盛岡":   "buy",      # ※ P1データ不足、過去実績から維持
    # caution: 65% ≤ ROI < 85%
    "佐賀":   "caution",  # 83.7%
    "名古屋": "caution",  # 78.6%
    "水沢":   "caution",  # 77.9%（旧pass→更新）
    "大井":   "caution",  # 77.5%
    "姫路":   "caution",  # 71.4%（旧pass→更新）
    "船橋":   "caution",  # 71.1%（旧pass→更新）
    "川崎":   "caution",  # 64.9%
    "笠松":   "caution",  # 64.1%
    "浦和":   "caution",  # 61.6%
    "門別":   "caution",  # 未集計（暫定）
    # pass: ROI < 60%
    "金沢":   "pass",     # 48.3%（旧caution→更新）
}


def chihou_buy_signal(course_name: str, recommend_rank: str | None = None) -> str:
    """地方競馬レースの購入指針を算出する。

    コースグレード × 期待値ランク（recommend_rank）で判定する。
    recommend_rank が None（オッズ未取得）の場合はコースグレードのみで暫定判定。

    Args:
        course_name:    競馬場名（例: "高知", "園田"）
        recommend_rank: 期待値ランク S/A/B/C（None = オッズ未取得）

    Returns:
        "buy" | "caution" | "pass"
    """
    course_grade = _CHIHOU_COURSE_GRADE.get(course_name, "caution")

    if recommend_rank is None:
        # オッズ未取得: コースグレードをそのまま返す（暫定）
        return course_grade

    if course_grade == "buy":
        # 高ROIコース: EV良好(S/A)なら買い、不良(B/C)なら要注意
        if recommend_rank in ("S", "A"):
            return "buy"
        return "caution"  # コース◎ × EV不利（過剰人気 or 大穴）

    if course_grade == "caution":
        # 中ROIコース: EV良好でも買いには格上げしない
        if recommend_rank in ("S", "A"):
            return "caution"  # EV良好、詳細確認推奨
        return "pass"  # EV不利なら見送り

    # pass コース
    return "pass"
