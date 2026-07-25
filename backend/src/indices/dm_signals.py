"""DM (JV-Next タイム型・対戦型) × 穴ぐさ × 既存指数 「穴」シグナル算出モジュール

⚠️ 2026-07-25 全面簡素化 [[jra_upset_badge_redesign]]:
軸信頼度は`recommend_rank`（market_agreeベースtier、confidence.py）に一本化済み
のため、本モジュールが個別に付与していた軸シグナル(三冠一致/高得点鉄板)・
警戒シグナル(人気下振れ)はユーザー指示により廃止。「穴として期待できそうな馬」
を示す単一マーク`SIGNAL_UPSET_CANDIDATE`のみを残す。

沿革: 旧ANAGUSA_DM/DM_BIG_DARK/DM_HIGH_ODDS/ANAGUSA_DM_TIMEの4タグ(狭いAND条件、
n=10-184の小標本でOOS不安定)→badge_cnt(穴ぐさ/netkeiba/kichiuma/DM-battleの
一致数)ベースの複数指数一致穴/指数一致穴2タグ(3年+完全OOSで両窓一貫した
単調分離を確認、badge0=7-10%→badge1=14-16%→badge2+=17-21%複勝的中)→
1レース1頭のみに絞るK=1化(複勝圏頭数キャップは合算的中率100%基準未達で不採用)
→本改修で「穴」1タグに統合(2つの強度区分もユーザー指示で撤廃)。

SIGNAL_UPSET_CANDIDATE (穴):
  単勝オッズ≥10 ∧ 穴ぐさ/netkeiba/kichiuma/DM-battleのうち1つ以上が上位評価、
  の該当馬のうちレース内でbadge_cnt最大の1頭のみ（同点はcomposite_index降順）。
  複勝的中率は両窓で約20%(min_badge=1/2とも大差なし)。複勝ROIは母集団全体で
  <1(控除率の壁)であり、「回収率」でなく「的中率の頑健な分離」が目的。
"""

from __future__ import annotations

from typing import Any, Protocol

# シグナル文字列定数 (UI 表示用にラベル付き、API では key を返す)
SIGNAL_UPSET_CANDIDATE = "穴"

# 穴badge対象の単勝オッズ下限（is_sweet_spot/upset_reranker と同一閾値）
UPSET_BADGE_MIN_ODDS = 10.0


class _Horse(Protocol):
    """compute_dm_signals が必要とする最小インターフェース。

    HorseIndexOut (api/races.py) を想定するが、テストや他用途で
    同じプロパティを持つオブジェクトなら何でも渡せる。
    """

    horse_number: int
    composite_index: float
    jvan_time_dm: float | None
    jvan_battle_dm: float | None
    anagusa_rank: str | None
    nb_ave_rank: int | None
    km_rank: int | None
    dm_signals: list[str] | None


def _ranks_descending(values: list[float | None]) -> list[int | None]:
    """降順ランクを付ける (最大=1)。NULL は None を返す。同値は同一ランク。

    例: [50, 80, 80, 30] → [3, 1, 1, 4]
    """
    n = len(values)
    indexed = [(i, v) for i, v in enumerate(values) if v is not None]
    indexed.sort(key=lambda x: x[1], reverse=True)
    out: list[int | None] = [None] * n
    rank = 0
    last_v: float | None = None
    seen = 0
    for i, v in indexed:
        seen += 1
        if last_v is None or v != last_v:
            rank = seen
            last_v = v
        out[i] = rank
    return out


def compute_dm_signals(
    horses: list[Any],
    popularity_map: dict[int, int] | None = None,
    win_odds_map: dict[int, float] | None = None,
    course_name: str | None = None,
    surface: str | None = None,
    distance: float | int | None = None,
    exclude_horse_numbers: set[int] | None = None,
) -> None:
    """レース内で最も有力な「穴」候補1頭に SIGNAL_UPSET_CANDIDATE を付与する (in-place)。

    Args:
        horses: HorseIndexOut のリスト (composite_index, anagusa_rank,
                nb_ave_rank, km_rank, jvan_battle_dm を持つこと)
        popularity_map: 未使用（後方互換のため引数のみ残置）。
        win_odds_map: {horse_number: 単勝オッズ} のマップ。
                      渡されない場合は穴判定不能で誰にも付与されない。
        course_name: 未使用（後方互換のため引数のみ残置）。
        surface: 未使用（後方互換のため引数のみ残置）。
        distance: 未使用（後方互換のため引数のみ残置）。
        exclude_horse_numbers: 出走取消・発走除外馬の馬番セット。
                               判定の母集団から除外する。
    """
    del popularity_map, course_name, surface, distance  # 後方互換のため引数のみ残置
    if not horses:
        return

    # 全馬の dm_signals を [] に初期化 (None だと未計算と区別できない)
    for h in horses:
        h.dm_signals = []

    # 取消・除外馬を母集団から外す（dm_signals は [] のまま）
    excluded = exclude_horse_numbers or set()
    horses = [h for h in horses if h.horse_number not in excluded]
    if not horses:
        return

    odds = win_odds_map or {}

    # DM battle は「そのレースで2頭以上値がある」場合のみランクを算出し badge_cnt に含める
    # (全頭DM必須にするとカバレッジが落ちるため部分カバレッジで計算する設計を維持)
    partial_battle_ranks = _ranks_descending([h.jvan_battle_dm for h in horses])
    best_h: _Horse | None = None
    best_badge_cnt = 0
    for i, h in enumerate(horses):
        win_odds = odds.get(h.horse_number)
        if win_odds is None or win_odds < UPSET_BADGE_MIN_ODDS:
            continue
        # nb_ave_rank/km_rank は getattr で防御的に読む（Protocol非準拠の
        # 呼び出し元(旧集計スクリプト等)でも badge_cnt 計算が落ちないように）
        nb_ave_rank = getattr(h, "nb_ave_rank", None)
        km_rank = getattr(h, "km_rank", None)
        badge_cnt = 0
        if h.anagusa_rank in ("A", "B", "C"):
            badge_cnt += 1
        if nb_ave_rank is not None and nb_ave_rank <= 3:
            badge_cnt += 1
        if km_rank is not None and km_rank <= 3:
            badge_cnt += 1
        battle_rank_partial = partial_battle_ranks[i]
        if battle_rank_partial is not None and battle_rank_partial <= 2:
            badge_cnt += 1
        if badge_cnt < 1:
            continue

        if best_h is None or badge_cnt > best_badge_cnt or (
            badge_cnt == best_badge_cnt and h.composite_index > best_h.composite_index
        ):
            best_h, best_badge_cnt = h, badge_cnt

    if best_h is not None:
        # 関数冒頭で全馬 dm_signals=[] 初期化済みのため実際は None にはならないが、
        # 型上は list[str] | None のため防御的に narrow する
        signals = best_h.dm_signals
        if signals is None:
            signals = []
            best_h.dm_signals = signals
        signals.append(SIGNAL_UPSET_CANDIDATE)


def popularity_from_odds(
    horse_numbers: list[int], win_odds_map: dict[int, float | None]
) -> dict[int, int]:
    """単勝オッズから人気を導出する。

    オッズが低い馬 = 人気上位 (1 が最人気)。同オッズは同人気。
    オッズが NULL の馬は最下位扱いせず、マップから除外する (シグナル発動回避)。
    """
    valid = [(hn, win_odds_map.get(hn)) for hn in horse_numbers]
    sortable = [(hn, o) for hn, o in valid if o is not None and o > 0]
    sortable.sort(key=lambda x: x[1])
    out: dict[int, int] = {}
    rank = 0
    last_o: float | None = None
    seen = 0
    for hn, o in sortable:
        seen += 1
        if last_o is None or o != last_o:
            rank = seen
            last_o = o
        out[hn] = rank
    return out
