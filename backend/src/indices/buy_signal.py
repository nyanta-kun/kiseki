"""購入指針（Buy Signal）算出モジュール。

過去の実績データに基づき、各レースの購入推奨度を算出する。

JRA（v26 LightGBM ensemble 検証 2026-05-02, 3年/138,728 horse-races）:
    指数1位馬の単勝オッズ・絶対値・2位差で判定。

    レース全体 (jra_buy_signal):
      [buy]   1位馬の単勝オッズ ≥ 10 → 単勝ROI 1.237 (n=442/3年)
      [caution] 6 ≤ オッズ < 10 → 単勝ROI 約 1.0 (均衡)
      [pass]  オッズ < 6 → 単勝ROI 0.85-0.89 (鉄板買いはマイナス)

    馬個別 (jra_horse_purchase_signal):
      [super_buy] rank=1 ∧ 2位差≥5 ∧ オッズ≥10  → 単勝ROI 1.480
      [buy]       rank≤2 ∧ composite≥60 ∧ オッズ≥10  → 単勝ROI 1.129
      [watch]     rank≤3 ∧ オッズ≥10  → 単勝ROI 1.042
      [pass]      上記いずれにも該当しない

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

def jra_buy_signal(
    distance: int,  # noqa: ARG001 互換のため残置（v26 では距離フィルタを使わない）
    top_win_odds: float | None,
) -> str | None:
    """JRA レースの購入指針を算出する（レースレベル）。

    v26 ensemble 検証 (2026-05-02) ベース:
      - オッズ ≥ 10 → 単勝ROI 1.237 ⇒ "buy"
      - 6 ≤ オッズ < 10 → 単勝ROI ~1.0 ⇒ "caution"
      - オッズ < 6 → 単勝ROI 0.85-0.89 ⇒ "pass"

    Args:
        distance: レース距離（互換のため残置、未使用）
        top_win_odds: 指数1位馬の単勝オッズ（None = オッズ未取得）

    Returns:
        "buy" | "caution" | "pass" | None（オッズ未取得）
    """
    if top_win_odds is None:
        return None
    if top_win_odds >= 10.0:
        return "buy"
    if top_win_odds >= 6.0:
        return "caution"
    return "pass"


def jra_horse_purchase_signal(
    rank: int,
    top2_t3_gap: float | None,
    win_odds: float | None,
) -> str | None:
    """JRA レース内の個別馬の購入指針を算出する。

    v26 ensemble breakaway 検証 (2026-05-02, 3年138,728 horse-races) ベース:
      "上位2頭が3位以下から抜け出している" レースの上位2頭中穴馬が最強。

      - super_buy: rank≤2 ∧ top2_t3_gap≥7 ∧ オッズ≥10  → 単勝ROI 1.593 (年46R)
      - buy:       rank≤2 ∧ top2_t3_gap≥5 ∧ オッズ≥10  → 単勝ROI 1.290 (年79R)
      - watch:     rank≤3 ∧ オッズ≥10                → 単勝ROI 1.042 (年1786R)
      - pass:      上記いずれにも該当しない

    特に rank=2 (2位馬) の中穴オッズが最強で、top2_t3_gap≥7 のとき単勝ROI 1.694。
    1位馬は人気を集めやすく ROI が薄まるため、抜け出し予測下の2位馬が割安。

    Args:
        rank: レース内 composite_index ランク (1=1位)
        top2_t3_gap: 2位と3位の composite_index 差 (rank≤2 のときのみ意味あり)
        win_odds: 当該馬の単勝オッズ

    Returns:
        "super_buy" | "buy" | "watch" | None
    """
    if win_odds is None or win_odds < 10.0:
        return None
    # super_buy: 上位2頭抜け出し(差≥7)の中穴馬
    if rank <= 2 and top2_t3_gap is not None and top2_t3_gap >= 7.0:
        return "super_buy"
    # buy: 上位2頭抜け出し(差≥5)の中穴馬
    if rank <= 2 and top2_t3_gap is not None and top2_t3_gap >= 5.0:
        return "buy"
    # watch: 上位3頭の中穴ゾーン
    if rank <= 3:
        return "watch"
    return None


# ---------------------------------------------------------------------------
# スイートスポット判定（単勝≥10 ∧ 期待値 1.2-5.0 ∧ バッジあり）
# ---------------------------------------------------------------------------
# 3年バックテスト (2023-05〜2026-05, 4,983 馬) で実証:
#   勝率 5.66% / 単ROI 1.182 / 複ROI 0.836
#
# EV ≥ 4 はモデル予測勝率と実勝率の乖離が 4.8〜6.5 倍と大きく外れ値リスクが高い。
# EV ≤ 1.2 もモデル較正に難があり期待値プラスが取れない。
# EV 1.2〜5.0 のレンジ内 + 何らかのバッジで安定的にプラス収支。

SWEET_SPOT_MIN_ODDS: float = 10.0
SWEET_SPOT_MIN_EV: float = 1.2
SWEET_SPOT_MAX_EV: float = 5.0


def is_sweet_spot(
    win_odds: float | None,
    win_probability: float | None,
    composite_rank: int | None,
    dm_signals: list[str] | None,
    purchase_signal: str | None,
    anagusa_rank: str | None,
    nb_course_rank: int | None,
    nb_ave_rank: int | None,
    km_rank: int | None,
) -> bool:
    """スイートスポット該当判定。

    条件:
      1. 単勝オッズ ≥ 10.0
      2. 期待値 (win_probability × win_odds) ∈ [1.2, 5.0]
      3. 何らかのバッジあり:
         - DM signals 1個以上 / 購入シグナル (super_buy/buy/watch)
         - 穴ぐさ A/B/C ピック (composite 1位以外)
         - 外部指数穴馬 (composite 4位以下 ∧ (NB course=1 or (NB ave≤2 ∧ KM=1)))
    """
    if win_odds is None or win_odds < SWEET_SPOT_MIN_ODDS:
        return False
    if win_probability is None:
        return False
    ev = float(win_probability) * float(win_odds)
    if ev < SWEET_SPOT_MIN_EV or ev > SWEET_SPOT_MAX_EV:
        return False

    # バッジ判定
    if dm_signals:
        return True
    if purchase_signal in ("super_buy", "buy", "watch"):
        return True
    if (
        anagusa_rank in ("A", "B", "C")
        and composite_rank is not None
        and composite_rank >= 2
    ):
        return True
    if composite_rank is not None and composite_rank >= 4:
        if nb_course_rank == 1:
            return True
        if nb_ave_rank is not None and nb_ave_rank <= 2 and km_rank == 1:
            return True
    return False


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
