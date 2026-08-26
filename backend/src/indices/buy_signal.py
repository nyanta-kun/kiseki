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

from collections.abc import Iterable, Mapping
from typing import TypedDict

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


def is_external_dark_horse(
    composite_rank: int | None,
    nb_course_rank: int | None,
    nb_ave_rank: int | None,
    km_rank: int | None,
) -> bool:
    """外部指数穴馬判定（単一の真実源。フロントは API のフラグを表示するだけ）。

    シミュレーション実績:
      - CI4位以下 ∧ netkeibaコース指数1位: 単勝ROI +105〜355%（平場芝）
      - CI4位以下 ∧ NB上位2 ∧ KM1位: 単勝ROI +126%（芝）
    """
    if composite_rank is None or composite_rank < 4:
        return False
    if nb_course_rank == 1:
        return True
    return nb_ave_rank is not None and nb_ave_rank <= 2 and km_rank == 1


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
    if is_external_dark_horse(composite_rank, nb_course_rank, nb_ave_rank, km_rank):
        return True
    return False


# ---------------------------------------------------------------------------
# JRA 高オッズ穴 複勝＋ワイド軸 推奨（2026-06-09・単勝EVゲートsweet_spotを置換）
# ---------------------------------------------------------------------------
# バックテスト検証 (memory: highodds_place_wide_recommendation / ハーネス
# scripts/jra_badge_lift.py・wide_axis_bt.py・oos_ci.py):
#   軸 = 単勝≥10 × composite上位4 × バッジ × place_probability上位2(レース内)
#     複勝(軸)          : 的中~27% / 複ROI 0.89 (2025 OOS分割一貫, CI[0.76,1.02])
#     ワイド軸×モデル1位 : 的中~17% / ROI 1.05 (前後半一貫, CI[0.80,1.33])
#   ※相手は「人気1番」でなく「モデルcomposite1位」が最良(モデルと市場の乖離が価値)。
#   ※モデル絞り(comp上位4)で複勝圏的中率は無条件比で倍増(10.6→24-27%)。
#   ※edgeは薄くCIは1を跨ぐ・v26 in-sample 楽観含み。複勝は高的中アンカー、
#     ワイド軸が数少ない+EV。地方は同構造でも黒字化しないため非適用(JRA限定)。

HIGHODDS_MIN_ODDS: float = 10.0
HIGHODDS_MAX_COMP_RANK: int = 4   # モデルcomposite レース内順位の上限
HIGHODDS_MAX_PP_RANK: int = 2     # place_probability レース内順位の上限(k≤2絞り)


def jra_highodds_has_badge(
    anagusa_rank: str | None,
    nb_ave_rank: int | None,
    km_rank: int | None,
    dm_signals: list[str] | None,
) -> bool:
    """高オッズ穴 軸の「バッジ(直交シグナル)」判定。

    バックテストの badge_any 定義に対応:
      穴ぐさ A/B/C ∪ netkeiba(idx_ave)順位≤3 ∪ kichiuma(sp_score)順位≤3 ∪ DMシグナルあり
    (バックテストの DM battle 順位≤2 は本番では dm_signals 有無で近似する)。
    """
    if anagusa_rank in ("A", "B", "C"):
        return True
    if nb_ave_rank is not None and nb_ave_rank <= 3:
        return True
    if km_rank is not None and km_rank <= 3:
        return True
    if dm_signals:
        return True
    return False


def jra_is_place_axis(
    win_odds: float | None,
    composite_rank: int | None,
    place_prob_rank: int | None,
    anagusa_rank: str | None,
    nb_ave_rank: int | None,
    km_rank: int | None,
    dm_signals: list[str] | None,
) -> bool:
    """JRA 高オッズ穴の複勝＋ワイド軸「軸馬」該当判定。

    条件(検証済):
      1. 単勝オッズ ≥ 10.0
      2. composite レース内順位 ≤ 4（モデル絞り）
      3. place_probability レース内順位 ≤ 2（k≤2 絞り）
      4. バッジあり（jra_highodds_has_badge）
    """
    if win_odds is None or float(win_odds) < HIGHODDS_MIN_ODDS:
        return False
    if composite_rank is None or composite_rank > HIGHODDS_MAX_COMP_RANK:
        return False
    if place_prob_rank is None or place_prob_rank > HIGHODDS_MAX_PP_RANK:
        return False
    return jra_highodds_has_badge(anagusa_rank, nb_ave_rank, km_rank, dm_signals)


# ---------------------------------------------------------------------------
# JRA 人気薄リランカー軸（2026-06-11・上記 jra_is_place_axis を置換する本番条件）
# ---------------------------------------------------------------------------
# 検証 (memory: upset_place_extraction.md):
#   軸 = 単勝[10,15) × 非オッズリランカー上位1/4(学習期閾値・検証期間で選定) × バッジ
#     A2(バッジ>=1): test(3分割凍結評価) 精度 33.1% CI[0.283,0.381] / 約7頭/日
#     A3(バッジ>=2): 精度 32.7-39.6% / 約3頭/日 → tier="strong"
#   発走前オッズ(-10分)判定 35.3%(市場同数26.1%)。15倍超は精度が構造的に落ちるため帯外。
#   リランカー実体は src/indices/upset_reranker.py（オッズ非使用 logistic）。

UPSET_AXIS_BAND_MIN: float = 10.0
UPSET_AXIS_BAND_MAX: float = 15.0


def jra_upset_axis_tier(
    win_odds: float | None,
    ns_score: float | None,
    ns_threshold: float | None,
    badge_cnt: int | None,
) -> str | None:
    """JRA 人気薄複勝圏リランカー軸の判定。

    Args:
        win_odds: 単勝オッズ。
        ns_score: リランカー非オッズスコア（UpsetReranker.score_race の ns）。
        ns_threshold: 学習期帯内 2/3 分位の採用閾値（アーティファクト保持値）。
        badge_cnt: バッジ数（穴ぐさ + netkeiba≤3 + kichiuma≤3 + DM battle≤2）。

    Returns:
        "strong"(バッジ>=2) / "standard"(バッジ>=1) / None(非該当)。
    """
    if win_odds is None or not (UPSET_AXIS_BAND_MIN <= float(win_odds) < UPSET_AXIS_BAND_MAX):
        return None
    if ns_score is None or ns_threshold is None or ns_score < ns_threshold:
        return None
    if badge_cnt is None or badge_cnt < 1:
        return None
    return "strong" if badge_cnt >= 2 else "standard"


class JraHighOddsPick(TypedDict):
    """JRA 高オッズ穴 複勝＋ワイド軸 推奨（軸1頭につき1枚）。"""

    axis_horse_number: int
    """軸馬番（高オッズ穴）。"""

    axis_win_odds: float
    """軸の単勝オッズ。"""

    place_bet: bool
    """複勝(軸)を推奨するか（常に True）。"""

    wide_partner_horse_number: int | None
    """ワイド相手＝モデルcomposite1位の馬番（軸自身が1位なら None）。"""

    rationale: str
    """バッジ等の説明。"""


def jra_build_highodds_pick(
    axis_horse: dict,
    comp_rank1_horse_number: int | None,
) -> JraHighOddsPick:
    """軸馬 dict から複勝＋ワイド軸の推奨を構築する。

    Args:
        axis_horse: 軸馬 dict（horse_number / win_odds / anagusa_rank / dm_signals 等）
        comp_rank1_horse_number: レースの composite 1位馬番（ワイド相手）

    Returns:
        JraHighOddsPick
    """
    axis_no = int(axis_horse["horse_number"])
    partner = (
        comp_rank1_horse_number
        if comp_rank1_horse_number is not None and int(comp_rank1_horse_number) != axis_no
        else None
    )
    tags: list[str] = []
    if axis_horse.get("anagusa_rank") in ("A", "B", "C"):
        tags.append(f"穴{axis_horse['anagusa_rank']}")
    dm = axis_horse.get("dm_signals")
    if dm:
        tags.extend(list(dm)[:2])
    if axis_horse.get("nb_ave_rank") is not None and int(axis_horse["nb_ave_rank"]) <= 3:
        tags.append("netkeiba上位")
    if axis_horse.get("km_rank") is not None and int(axis_horse["km_rank"]) <= 3:
        tags.append("kichiuma上位")
    rationale = (
        f"{axis_no}番 単勝{float(axis_horse.get('win_odds') or 0):.1f}倍 高オッズ穴"
        + (f"[{','.join(tags)}]" if tags else "")
        + (f" → 複勝＋ワイド軸(相手{partner}番=モデル1位)" if partner else " → 複勝")
    )
    return JraHighOddsPick(
        axis_horse_number=axis_no,
        axis_win_odds=float(axis_horse.get("win_odds") or 0.0),
        place_bet=True,
        wide_partner_horse_number=partner,
        rationale=rationale,
    )


# ---------------------------------------------------------------------------
# 地方競馬 スイートスポット（v10 LightGBM win_probability ベース）
# ---------------------------------------------------------------------------
# v10 バックテスト（3年・南関4場 2023-05〜2026-05）:
#   浦和 EV 1.0-1.5: ROI 1.375 (n=395, tr:1.183 va:0.962 te:3.098)
#   コース別 EV 1.0-2.0 × 単勝≥10 でROI陽性:
#     浦和 1.375 / 水沢 1.634 / 笠松 1.430 / 園田 1.460 / 佐賀 1.379 / 高知 1.118
#   ROI陰性コース（除外）: 名古屋/大井/船橋/川崎/金沢

# Phase2 (2026-06-05): sweet_spot を「EVゲート」から検証済みのランキング規則へ移行。
# 較正済 win_probability(is_win生確率, ECE 0.0024) では高オッズ馬の honest EV は
# 概ね <1 となり、旧 EV∈[1.0,2.0] ゲートは機能しない。Phase1 のクリーンOOS検証で
# 黒字だったのは EVゲートでなく「指数1位 × 単勝10-30倍 × 割安場」(5seed ROI 1.17)
# というランキング規則だったため、これを sweet_spot の定義とする。
CHIHOU_SWEET_SPOT_MIN_ODDS: float = 10.0
CHIHOU_SWEET_SPOT_MAX_ODDS: float = 30.0  # 30倍超は分散大・ROI崩壊(03 検証)

# Phase1 検証で 単勝10-30倍 ROI陽性だった割安場（浦和/金沢/高知が牽引、笠松/盛岡含む）
_CHIHOU_SWEET_SPOT_COURSES: frozenset[str] = frozenset({
    "浦和", "金沢", "高知", "笠松", "盛岡",
})

# 断然人気複勝推奨の1番人気オッズ閾値（断然人気レースの value 穴）
CHIHOU_PLACE_BET_FAV_ODDS_MAX: float = 2.0
# place_bet も EV ゲートから「指数上位の穴」ランキング規則へ移行（複勝は妙味薄=参考用途）
CHIHOU_PLACE_BET_MAX_INDEX_RANK: int = 3
# 複勝は出走7頭以下だと2着までしか払い戻されない(JRA/NAR共通ルール)。
# 3着入着でも複勝は不的中となるため、複勝関連の推奨は8頭以上に限定する。
# 2026-07-23 発見: is_place_bet に頭数ゲートがなく、6-7頭立てで3着入着馬を
# 誤って複勝的中として扱いうる状態だった(backtest honest ROIも同様の混入あり)。
CHIHOU_PLACE_MIN_HEAD_COUNT: int = 8


def chihou_is_sweet_spot(
    index_rank: int | None,
    win_odds: float | None,
    course_name: str | None,
) -> bool:
    """地方競馬スイートスポット判定（Phase2: ランキング規則）。

    条件（Phase1 クリーンOOS検証, 5seed 単勝ROI 1.17）:
      1. 指数(composite)1位
      2. 単勝オッズ ∈ [10.0, 30.0)
      3. 割安場（浦和/金沢/高知/笠松/盛岡）
    """
    if index_rank != 1:
        return False
    if win_odds is None or not (CHIHOU_SWEET_SPOT_MIN_ODDS <= float(win_odds) < CHIHOU_SWEET_SPOT_MAX_ODDS):
        return False
    return course_name in _CHIHOU_SWEET_SPOT_COURSES


def chihou_is_place_bet(
    index_rank: int | None,
    win_odds: float | None,
    fav_odds: float | None,
    head_count: int | None = None,
) -> bool:
    """地方競馬 断然人気レース穴馬 複勝推奨判定（Phase2: ランキング規則）。

    1頭断然人気がいるレースで、指数上位の単勝高オッズ馬を複勝推奨（複圏の value 穴）。
    地方競馬では断然人気馬が1着固定でも 2〜3着に人気薄が入りやすい構造がある。
    複勝は控除率分の赤字帯（03 検証）のため「予想の参考」用途。

    条件:
      1. 1番人気単勝オッズ < 2.0（断然人気レース）
      2. 対象馬 単勝オッズ ≥ 10.0
      3. 指数(composite)上位（rank ≤ 3）
      4. 出走頭数 ≥ 8（7頭以下は複勝が2着までしか払い戻されないため対象外。
         head_count=None の場合も安全側でFalse）
    """
    if head_count is None or head_count < CHIHOU_PLACE_MIN_HEAD_COUNT:
        return False
    if fav_odds is None or float(fav_odds) >= CHIHOU_PLACE_BET_FAV_ODDS_MAX:
        return False
    if win_odds is None or float(win_odds) < CHIHOU_SWEET_SPOT_MIN_ODDS:
        return False
    return index_rank is not None and index_rank <= CHIHOU_PLACE_BET_MAX_INDEX_RANK


# ---------------------------------------------------------------------------
# 地方競馬 低オッズ本命（単勝<2.0）信頼度分割
# ---------------------------------------------------------------------------
# バックテスト（3年・全地方・約16,000サンプル）:
#   単勝 < 1.5         : hit 69.7% / 単勝ROI 0.85 (n=6,715) → 信頼できる
#   1.5 ≤ 単勝 < 2.0   : hit 47.7% / 単勝ROI 0.81 (n=9,606) → 信頼できない
#   index_rank の影響は小さく（オッズと相関）、オッズ単一軸の方がデータ的に明瞭。
#   ROI は構造的に常に 1.0 未満（控除率分の損失帯）= 推奨は「予想の参考」として扱う。

CHIHOU_LOW_ODDS_MAX: float = 2.0
CHIHOU_LOW_ODDS_TRUST_THRESHOLD: float = 1.5


def chihou_low_odds_trust_level(win_odds: float | None) -> str | None:
    """地方競馬 単勝<2.0 帯の本命を信頼度で分類する。

    Returns:
        "trusted"   — 単勝 < 1.5（バックテスト的中率 約 70%）
        "untrusted" — 1.5 ≤ 単勝 < 2.0（同 約 48%）
        None        — 範囲外
    """
    if win_odds is None:
        return None
    if win_odds < CHIHOU_LOW_ODDS_TRUST_THRESHOLD:
        return "trusted"
    if win_odds < CHIHOU_LOW_ODDS_MAX:
        return "untrusted"
    return None


# ---------------------------------------------------------------------------
# 地方競馬 注目馬 — 人気薄なのに複勝圏に来る馬（的中率特化）
# ---------------------------------------------------------------------------
# 検証: docs/chihou_darkhorse_feasibility_2026_08_05.md
#
# ## この条件が答えているもの
#   「発走前6番人気以下の馬が3着以内に来る」条件を絞り込めるか。
#   **収支ではなく的中率の指標**。ROI は 0.9〜1.1 で黒字は確立していない。
#
# ## 実測（2026-08-25 更新・これが現行の値）
#   四半期 walk-forward（市場特徴なし・2024-10〜2026-07）で指数を作り直し、
#   **選別も決済も発走6分前オッズ**で行った、本番と同条件の測定:
#
#     母集団（発走前6番人気以下）      : 複勝圏率 12.2%  ROI 0.674  (n=20,341)
#     本条件（指数5位内 × シェア<0.63）: 複勝圏率 26.9%  ROI 0.923  (n=840)
#                                        = 母集団の約 2.2 倍 / ROI 95%CI [0.804, 1.052]
#
#   ⚠️ **黒字は確認できていない。** CI がまだ 1.0 を跨いでおり、
#      点推定は 1.0 未満。的中率の指標として使うこと。
#
#   参考: 同じ期間を**確定オッズで選別**すると複勝率 19.5% / ROI 0.784 に落ちる。
#      確定オッズでの選別は保守側にズレるので、バックテストの数字をそのまま
#      運用の期待値にしないこと（下記「前身の条件をなぜ捨てたか」と同根）。
#
# ## 【破棄】旧実測（2026-04-07〜07-31・複勝圏率 51.5% / ROI 1.110）
#   指数側が市場特徴を含み、その入力が確定オッズだったため look-ahead していた。
#   「発走5分前は6番人気以下だが締切までに買われた馬」を拾っていた（該当186頭の
#   87% が確定1〜3番人気）。**この数字を引用しないこと。**
#
# ## 前身の条件をなぜ捨てたか（重要な教訓）
#   旧 `chihou_is_open_place()`（単勝30-50倍 × シェア<0.63）は **確定オッズで
#   条件を判定していた**。賭け時に確定オッズは分からないため look-ahead であり、
#   発走前オッズで測り直すと複勝ROI 1.22 → **0.85** に崩壊した。
#   選ばれた馬のオッズは発走までに中央値 ×1.48 に流れ、58.8% が最終50倍超になる。
#   「確定で30-50倍に収まる馬」＝「流れなかった馬」を選ぶ条件付けになっていた
#   （drift<1.0 の馬だけなら複勝率 0.225 / ROI 1.337、drift>=1.3 なら 0.056 / 0.585）。
#
#   → **この条件は人気・シェアとも発走前スナップショットだけで判定する。**
#      確定オッズを使ってはいけない。
#
# ## シェア≥0.63（開いていないレース）を採らない理由
#   探索 37.7% → 確認 25.7% と落ちる。母集団の3倍はあるが再現性が弱い。

# 「6番人気以下」の下限（発走前オッズ昇順の順位）
CHIHOU_PICK_MIN_POP_RANK: int = 6
# 指数がこの順位以内であること。
#
# 2026-08-13: 3 → 5 に緩和した。目的が「1レースに1〜2頭ピックアップ」であり、
# 3位以内では網羅率が 6.4%（レースの約1/16）で選択的すぎたため。
# DISCOVERY(2026-04-07〜06-30 / 2,894R・市場を見ない walk-forward 指数)実測:
#
#   指数3位内 × 最大2頭  網羅  6.4%  1.15頭/R  複勝率 29.4%(×2.46)  R的中 32.1%
#   指数5位内 × 最大2頭  網羅 14.0%  1.40頭/R  複勝率 28.2%(×2.36)  R的中 36.8%
#
# 網羅率が 2.2 倍になり複勝率の低下は 1.2pt に留まる。
# ⚠️ 探索段階の選択（約30セルを比較）であり、隣接セルの差は有意ではない。
# 確認は前向き記録でのみ行うこと（台帳 11 章）。
CHIHOU_PICK_MAX_INDEX_RANK: int = 5

# 1レースから採る上限頭数。
#
# 「最大2頭」は「最大1頭」より一貫して複勝率が高い（指数5位内で 24.9% → 28.2%）。
# 2頭目の質が良いのではなく、**2頭が条件を満たすレース自体が当たりやすい**
# （開いていて指数が複数頭を推せるレース）。頭数そのものがレース選別に効いている。
CHIHOU_PICK_MAX_PER_RACE: int = 2
# 市場含意確率（1/オッズをレース内で正規化）の上位3頭合計がこの値未満なら「開いたレース」
CHIHOU_OPEN_RACE_MAX_TOP3_SHARE: float = 0.63


def chihou_market_top3_share(win_odds: Iterable[float | None]) -> float | None:
    """レースの「市場上位3頭シェア」を算出する。

    1/オッズをレース内で正規化した市場含意確率の、上位3頭の合計。
    小さいほど「誰も抜けていない開いたレース」。

    最終オッズでなくても安定している（締切5分前の判定が最終と 98.6% 一致）ため、
    発走前のスナップショットで判定してよい。

    Args:
        win_odds: レース内全馬の単勝オッズ（None・1.0未満は無効値として捨てる）

    Returns:
        上位3頭シェア（0〜1）。有効オッズが3頭未満なら None。
    """
    valid = [float(o) for o in win_odds if o is not None and float(o) >= 1.0]
    if len(valid) < 3:
        return None
    inv = [1.0 / o for o in valid]
    total = sum(inv)
    if total <= 0:
        return None
    probs = sorted((v / total for v in inv), reverse=True)
    return sum(probs[:3])


def chihou_popularity_ranks(pre_odds_by_hn: Mapping[int, float | None]) -> dict[int, int]:
    """発走前単勝オッズから人気順位を作る（1 = 最も買われている）。

    ⚠️ **確定オッズを渡してはいけない。** 賭け時に得られる情報だけで判定するのが
    この条件の前提（前身の条件はここを誤って崩壊した）。

    Args:
        pre_odds_by_hn: 馬番 → 発走前単勝オッズ（None・1.0未満は順位を付けない）

    Returns:
        馬番 → 人気順位。同着オッズは馬番の小さい方を上位とする。
    """
    valid = [
        (hn, float(o)) for hn, o in pre_odds_by_hn.items()
        if o is not None and float(o) >= 1.0
    ]
    valid.sort(key=lambda x: (x[1], x[0]))
    return {hn: i + 1 for i, (hn, _o) in enumerate(valid)}


def chihou_effective_head_count(
    head_count: int | None,
    registered_count: int | None,
) -> int | None:
    """発走前でも使える出走頭数を返す。

    🔴 **`chihou.races.head_count` はレース後にしか入らない。**
    実測（2026-08-13）:

        8/12（過去） 57レース中 57 で充足
        8/13（当日） 45レース中  2 で充足
        8/14（翌日） 34レース中  0 で充足

    一方 `registered_count`（登録頭数）は**発走前から100%入っている**。

    `chihou_is_place_pick()` は `head_count is None` で False を返すため、
    このままでは**当日は一頭も注目馬が出ない**（推奨が事実上死んでいた）。
    過去データでは head_count が埋まっているので検証だけが通っていた。

    代替の安全性（2026-01〜08-12・8,130レース実測）:

        registered_count>=8 かつ head_count>=8   7,538
        registered_count>=8 だが head_count<8       71（0.93%）
        registered_count<8  だが head_count>=8       0  ← 取りこぼしゼロ

    取りこぼしは無く、通しすぎが 0.93%。その 0.93% は出走取消で7頭以下になり
    複勝が2着までしか払い戻されないレースだが、許容する。

    Args:
        head_count: 確定出走頭数（レース後のみ）
        registered_count: 登録頭数（発走前から入る）

    Returns:
        判定に使う頭数。どちらも無ければ None。
    """
    if head_count is not None:
        return head_count
    return registered_count


def chihou_is_place_pick(
    pop_rank: int | None,
    index_rank: int | None,
    top3_share: float | None,
    head_count: int | None,
) -> bool:
    """地方競馬 注目馬（人気薄の複勝圏候補）の判定。

    条件:
      1. 出走 8 頭以上（7頭以下は複勝が2着までしか払い戻されないため対象外）
      2. **発走前** 6番人気以下
      3. 指数（composite_index）5位以内
      4. 市場上位3頭シェア < 0.63（上位が抜けていない「開いたレース」）

    さらに1レースから採るのは `chihou_select_place_picks()` で**最大2頭**に絞る。

    ⚠️ **期待値の訂正（2026-08-13）**: 先行検証の「複勝圏率 51.5%」は
    **指数側の look-ahead** による過大評価だった。検証に使った walk-forward 指数は
    市場特徴を含み、その入力が `race_results` の**確定オッズ**だったため、
    「発走5分前は6番人気以下だが締切までに2〜3番人気まで買われた馬」を拾っていた
    （該当186頭の 87% が確定1〜3番人気・pre/final 比の中央値 2.30）。

    **市場を見ない指数（＝本番と同じ条件）で測り直した honest な値**
    （2026-08-25 更新・walk-forward 2024-10〜2026-07 / 発走6分前オッズで選別）:

        複勝圏率 26.9%（母集団 12.2% の約2.2倍・n=840）

    **的中率の指標であって収支の保証ではない**
    （複勝ROI 0.923・95%CI [0.804, 1.052] で**黒字は確認できていない**）。
    根拠は台帳 `docs/chihou_rebuild_2026_08.md` 10〜11章と、モジュール冒頭の実測ブロック。

    Args:
        pop_rank: 発走前オッズによる人気順位（`chihou_popularity_ranks()`）
        index_rank: composite_index のレース内順位（1 = 最上位）
        top3_share: `chihou_market_top3_share()` の戻り値（発走前オッズから）
        head_count: 出走頭数

    Returns:
        注目馬（複勝）に該当するか
    """
    if head_count is None or head_count < CHIHOU_PLACE_MIN_HEAD_COUNT:
        return False
    if pop_rank is None or pop_rank < CHIHOU_PICK_MIN_POP_RANK:
        return False
    if index_rank is None or index_rank > CHIHOU_PICK_MAX_INDEX_RANK:
        return False
    if top3_share is None:
        return False
    return float(top3_share) < CHIHOU_OPEN_RACE_MAX_TOP3_SHARE


def chihou_select_place_picks(
    candidates: list[tuple[int, int]],
    max_picks: int = CHIHOU_PICK_MAX_PER_RACE,
) -> list[int]:
    """1レースの注目馬候補から実際に推奨する馬を選ぶ。

    `chihou_is_place_pick()` は馬ごとの適格判定しかしないので、
    指数5位以内まで緩めると1レースに最大5頭が該当しうる。
    「1レースに1〜2頭」に絞るのはここの責務。

    Args:
        candidates: (馬番, 指数順位) の組。適格判定を通ったものだけを渡すこと。
        max_picks: 採用上限。

    Returns:
        推奨する馬番（指数の良い順）。
    """
    ordered = sorted(candidates, key=lambda x: (x[1], x[0]))
    return [hn for hn, _rank in ordered[:max_picks]]


# ---------------------------------------------------------------------------
# JRA 統合買い目推奨（ランク体系）
# ---------------------------------------------------------------------------
# bet-structure-guide.md の思想を競馬に適用:
#   gap12（指数1位〜2位の確率差）で軸の確度を評価し、券種と点数まで決定する。
#
# Tier 定義（単勝系はバックテスト実証済み、3連複系は仮説・暫定閾値）:
#   SS   : super_buy ∧ sweet_spot               → 単勝   ROI 1.48 実証
#   S    : buy       ∧ sweet_spot               → 単勝   ROI 1.29 実証
#   A    : sweet_spot                           → 単勝   ROI 1.19 実証
#   3F-2軸: gap_1_2≥8 ∧ top2_t3_gap≥5 ∧ DM穴 → 3連複2軸×3頭(3点) 仮説
#   3F-BOX: 混戦(prob<0.25) ∧ DM有             → 3連複BOX3(1点)   仮説


class JraRaceTicket(TypedDict):
    """JRAレースの統合買い目推奨。1レースにつき1枚。"""

    tier: str
    """ランク: "SS" / "S" / "A" / "3F-2軸" / "3F-BOX"。"""

    bet_type: str
    """馬券種別: "win" / "trifecta"。"""

    target_horse_numbers: list[int]
    """対象馬番リスト（単勝: 対象馬、3連複: 軸＋ひも全馬番）。"""

    ticket_combos: list[list[int]]
    """実際の組み合わせ（単勝: [[馬番]]×N / 3連複: [[1,2,3],[1,2,4],...]）。"""

    points: int
    """合計点数。"""

    rationale: str
    """主要シグナルの説明。"""

    roi_basis: float | None
    """バックテスト実証ROI（None=未実証の仮説）。"""

    is_verified: bool
    """バックテスト実証済みか。False の場合は仮説として扱う。"""


# 3連複2軸戦略の閾値（バックテスト確定 2026-05-29）
# grid search: gap≥8 t3≥3 → n=297, hit=57(19.2%), ROI=3.606
# win_prob 条件は影響なし（DM穴条件で自然にフィルタ済み）
_3F_2AX_GAP_1_2_MIN: float = 8.0
_3F_2AX_TOP2_T3_GAP_MIN: float = 3.0

# 3連複BOX3の閾値（バックテスト確定 2026-05-29）
# prob<0.25 gap12<0.06 福島除外 → n=496, hit=27(5.4%), ROI=4.660
_3F_BOX_WIN_PROB_MAX: float = 0.25
_3F_BOX_GAP12_PROB_MAX: float = 0.06

# 3F-BOX 除外コース（全消しコース: 福島=ROI 0.0, 新潟は borderline）
_3F_BOX_DENY_COURSES: frozenset[str] = frozenset({"福島"})

# DM穴系シグナル（3連複戦略の発動条件）
_DM_DARK_SIGNALS: frozenset[str] = frozenset({
    "穴ぐさDM", "DM大穴", "DM高オッズ", "穴ぐさ+DMtime",
})


def jra_race_ticket(
    gap_1_2: float | None,
    gap12_prob: float | None,
    top2_t3_gap: float | None,
    win_prob_rank1: float | None,
    ranked_horses: list[dict],
    sweet_horses: list[dict],
    head_count: int | None,
    course_name: str | None = None,
) -> JraRaceTicket | None:
    """JRAレースの統合買い目推奨を決定する。

    複数シグナルを1つの Tier に統合し「何を何点買うか」まで出力する。
    優先順位: SS > S > A > 3F-2軸 > 3F-BOX。

    Args:
        gap_1_2: composite_index の1位〜2位差（recommender.pyで計算済み）
        gap12_prob: win_probability の1位〜2位差
        top2_t3_gap: composite_index の2位〜3位差
        win_prob_rank1: 指数1位馬の win_probability
        ranked_horses: composite_index降順のレース馬リスト（各馬は dict）
        sweet_horses: sweet_spot=True の馬リスト（purchase_signal 含む）
        head_count: 出走頭数
        course_name: 競馬場名（3F-BOX除外コース判定に使用）

    Returns:
        JraRaceTicket（推奨あり）/ None（条件不一致）
    """
    if not ranked_horses:
        return None

    # --- 単勝系: SS / S / A ---
    if sweet_horses:
        ss_horses = [h for h in sweet_horses if h.get("purchase_signal") == "super_buy"]
        s_horses = [h for h in sweet_horses if h.get("purchase_signal") == "buy"]

        def _win_ticket(tier: str, targets: list[dict], roi: float) -> JraRaceTicket:
            numbers = [h["horse_number"] for h in targets]
            return JraRaceTicket(
                tier=tier,
                bet_type="win",
                target_horse_numbers=numbers,
                ticket_combos=[[n] for n in numbers],
                points=len(numbers),
                rationale=_win_rationale(targets),
                roi_basis=roi,
                is_verified=True,
            )

        if ss_horses:
            return _win_ticket("SS", ss_horses, 1.48)
        if s_horses:
            return _win_ticket("S", s_horses, 1.29)
        return _win_ticket("A", sweet_horses, 1.19)

    # --- 3連複系: 3F-2軸 ---
    # ⚠️ OOS再検証で破綻 (2026-06-05, scripts/jra_trifecta_backtest.py):
    #   旧主張 gap≥8 t3≥3 → n=297/ROI 3.606 は再現せず。本番条件(SS/S/A非該当 ∧ DM穴 ∧
    #   gap_1_2≥8 ∧ top2_t3_gap≥3)では3年でわずか2レースしか発火せず的中0。
    #   → is_verified=False(仮説)へ降格。発火が極小のため実質非運用。
    has_dm_dark = any(
        bool(set(h.get("dm_signals") or []) & _DM_DARK_SIGNALS)
        for h in ranked_horses
    )
    has_dm_any = any(h.get("dm_signals") for h in ranked_horses)

    if (
        len(ranked_horses) >= 5
        and gap_1_2 is not None and gap_1_2 >= _3F_2AX_GAP_1_2_MIN
        and top2_t3_gap is not None and top2_t3_gap >= _3F_2AX_TOP2_T3_GAP_MIN
        and has_dm_dark
    ):
        ax1 = ranked_horses[0]["horse_number"]
        ax2 = ranked_horses[1]["horse_number"]
        # ひも: 3〜5位（最大3頭、出走頭数-2を超えない）
        himo_max = min(3, len(ranked_horses) - 2)
        himo = [ranked_horses[i]["horse_number"] for i in range(2, 2 + himo_max)]
        combos = [[ax1, ax2, h] for h in himo]
        return JraRaceTicket(
            tier="3F-2軸",
            bet_type="trifecta",
            target_horse_numbers=[ax1, ax2, *himo],
            ticket_combos=combos,
            points=len(combos),
            rationale=(
                f"2頭抜け出し(gap={gap_1_2:.1f} t3={top2_t3_gap:.1f}) × DM穴シグナル"
                f" → {ax1}-{ax2}軸×3着{himo}流し"
            ),
            roi_basis=None,
            is_verified=False,
        )

    # --- 3連複系: 3F-BOX ---
    # ⚠️ OOS再検証で未確証 (2026-06-05, scripts/jra_trifecta_backtest.py):
    #   旧主張 prob<0.25 gap12<0.06 福島除外 → n=496/ROI 4.660 は再現せず。
    #   FULL n=139 ROI 1.535(CI[0.62,2.65]跨ぎ) → OOS test n=72 ROI 0.803(drop1 0.396)。
    #   高配当1本依存で黒字確証なし → is_verified=False(仮説)へ降格。
    if (
        len(ranked_horses) >= 3
        and course_name not in _3F_BOX_DENY_COURSES
        and win_prob_rank1 is not None and win_prob_rank1 < _3F_BOX_WIN_PROB_MAX
        and (gap12_prob is None or gap12_prob < _3F_BOX_GAP12_PROB_MAX)
        and has_dm_any
    ):
        top3 = [ranked_horses[i]["horse_number"] for i in range(3)]
        return JraRaceTicket(
            tier="3F-BOX",
            bet_type="trifecta",
            target_horse_numbers=top3,
            ticket_combos=[top3],
            points=1,
            rationale=(
                f"混戦(1位prob={win_prob_rank1:.2f} gap12={gap12_prob:.3f}) × DMシグナル"
                f" → {top3[0]}-{top3[1]}-{top3[2]}BOX"
            ),
            roi_basis=None,
            is_verified=False,
        )

    return None


def _win_rationale(targets: list[dict]) -> str:
    parts = []
    for h in targets:
        tags: list[str] = []
        sig = h.get("purchase_signal")
        if sig == "super_buy":
            tags.append("2頭抜け出し+中穴")
        elif sig == "buy":
            tags.append("抜け出し+中穴")
        dm = h.get("dm_signals")
        if dm:
            tags.extend(dm[:2])
        if h.get("anagusa_rank") in ("A", "B", "C"):
            tags.append(f"穴{h['anagusa_rank']}")
        parts.append(
            f"{h['horse_number']}番 EV{(h.get('ev_win') or 0):.2f}"
            + (f"[{','.join(tags)}]" if tags else "")
        )
    return "単勝: " + " / ".join(parts)


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
