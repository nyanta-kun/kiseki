"""購入指針（Buy Signal）算出モジュール。

過去の実績データに基づき、各レースの購入推奨度を3段階で算出する。

JRA（2025-01以降, v17, 4,387レース実績）:
    距離帯 × 指数1位オッズで判定。
    短距離(〜1400m): ROI 84-85%（オッズ問わず） → "pass"
    4.0倍以上 + マイル以上(1401m+): ROI 105.7%+ → "buy"
    3.0-4.0倍 + マイル以上: ROI ~98% → "caution"
    4.0倍未満: ROI 85-94% → "pass"

地方（2025-01以降, v5, 16,824レース実績）:
    競馬場ベースで判定（オッズフィルターはROI改善効果が薄い）。
    高知・盛岡・園田: ROI 84-95% → "buy"
    佐賀〜川崎: ROI 65-80% → "caution"
    水沢・姫路・船橋・浦和: ROI 54-65% → "pass"
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

# 競馬場名 → 購入指針グレード（2025-01以降 v5 実績）
_CHIHOU_COURSE_GRADE: dict[str, str] = {
    # buy: ROI ≥ 80%
    "高知":   "buy",      # 95.2%
    "盛岡":   "buy",      # 84.6%
    "園田":   "buy",      # 84.0%
    # caution: 65% ≤ ROI < 80%
    "佐賀":   "caution",  # 79.4%
    "門別":   "caution",  # 79.1%
    "名古屋": "caution",  # 75.9%
    "金沢":   "caution",  # 72.9%
    "笠松":   "caution",  # 72.3%
    "大井":   "caution",  # 69.2%
    "川崎":   "caution",  # 65.9%
    # pass: ROI < 65%
    "水沢":   "pass",     # 63.3%
    "姫路":   "pass",     # 61.3%
    "船橋":   "pass",     # 56.7%
    "浦和":   "pass",     # 53.6%
}


def chihou_buy_signal(course_name: str) -> str:
    """地方競馬レースの購入指針を算出する。

    Args:
        course_name: 競馬場名（例: "高知", "園田"）

    Returns:
        "buy" | "caution" | "pass"
    """
    return _CHIHOU_COURSE_GRADE.get(course_name, "caution")
