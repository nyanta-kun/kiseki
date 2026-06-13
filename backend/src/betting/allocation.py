"""資金配分モジュール（分数Kelly + 制約レイヤ）。

## Kelly 基準と分数Kelly について

Kelly 基準は長期的な資産成長を最大化するベット比率 f* = (p*b - (1-p)) / b を与える
（b = net_odds, p = 的中確率）。ただし Kelly は対数効用の最大化であり、短期的には
分散が非常に大きく「破産リスク」が高い。

既定の fraction = 0.25（quarter-Kelly）の根拠:
  - シミュレーション研究では half-Kelly の分散が full-Kelly の 25%（分散はfraction^2に比例）
  - quarter-Kelly では分散を full-Kelly の 6.25% に抑えられ、長期成長率は full-Kelly の
    約 87% を維持（実証例: Thorpe 1997, Poundstone 2005）
  - 競馬では確率推定誤差が大きいため、over-betting による破産を防ぐ保守的設定として quarter が実用的

## 同一レース内の近似 Kelly について

同一レース内の複数ベットは排反でない（複勝+ワイドなど）。厳密な同時 Kelly は
凸最適化問題になり、馬体数×組合せ数でスケールする。本実装では以下の近似を採用:

1. 各ベット独立に Kelly ステーク s_i = kelly_fraction * f_i * bankroll を計算
2. レース内合計が BET_MAX_PER_RACE を超える場合、比例縮小（按分）
3. 近似誤差: 独立事象と仮定するため、排反ベット間の相関を無視する。
   最悪ケースはフルコリレート（三連単BOX全点）で合計ステークが過大になるが、
   BET_MAX_PER_RACE 制約が安全弁として機能する。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from src.config import settings

BetType = Literal[
    "win",        # 単勝
    "place",      # 複勝
    "quinella",   # 馬連
    "wide",       # ワイド
    "trio",       # 三連複
    "trifecta",   # 三連単
    "exacta",     # 馬単
    "frame",      # 枠連
]

# 券種別最大点数（超過時は EV 降順で切る）
MAX_TICKETS_PER_TYPE: dict[str, int] = {
    "win": 3,
    "place": 3,
    "quinella": 6,
    "wide": 6,
    "exacta": 12,
    "trio": 10,
    "trifecta": 12,
    "frame": 6,
}

MIN_STAKE = 100  # JRA 最小購入単位（円）
STAKE_UNIT = 100  # 丸め単位


@dataclass
class BetCandidate:
    """Kelly 配分への入力候補ベット。

    Attributes:
        bet_type: 券種識別子（BetType）
        combination: 買い目文字列（例: "01-02-03"）
        est_prob: モデル推定的中確率（0 < p < 1）
        odds: 対象オッズ（払戻倍率。1.0未満は無効）
        tag: 戦略タグ（集計・ログ用）
    """

    bet_type: str
    combination: str
    est_prob: float
    odds: float
    tag: str = ""


@dataclass
class AllocatedBet:
    """Kelly 配分後の確定ベット。

    Attributes:
        bet_type: 券種
        combination: 買い目
        stake: 配分額（円・100円単位）
        kelly_f: 計算された Kelly 比率（参考値）
        shrunk_prob: shrinkage 後の確率
        ev: 期待値（shrunk_prob * odds）
        tag: 戦略タグ
    """

    bet_type: str
    combination: str
    stake: int
    kelly_f: float
    shrunk_prob: float
    ev: float
    tag: str = ""


def _shrink_probability(
    est_prob: float,
    market_prob: float | None,
    alpha: float,
) -> float:
    """モデル確率を市場確率で縮小する（Shrinkage）。

    p' = alpha * est_prob + (1 - alpha) * market_prob

    alpha = 1.0 ならモデル確率をそのまま使用。
    alpha = 0.0 なら市場確率のみ使用。
    market_prob が None の場合は est_prob をそのまま返す。

    Args:
        est_prob: モデル推定確率
        market_prob: 市場確率（1 / odds で近似）。None なら縮小なし
        alpha: モデル確率のウェイト（0.0〜1.0）

    Returns:
        縮小後の確率（0.0〜1.0）
    """
    if market_prob is None or not (0.0 <= alpha <= 1.0):
        return est_prob
    if alpha == 1.0:
        return est_prob
    p = alpha * est_prob + (1.0 - alpha) * market_prob
    return max(1e-9, min(1.0 - 1e-9, p))


def _kelly_fraction_single(
    est_prob: float,
    odds: float,
    kelly_fraction: float,
) -> float:
    """単一ベットの Kelly 比率を計算する。

    f* = (p * b - (1 - p)) / b  ただし b = odds - 1（net_odds）
    fraction-Kelly: f = kelly_fraction * f*

    Args:
        est_prob: 的中確率
        odds: 払戻倍率
        kelly_fraction: 分数Kelly 係数（既定 0.25）

    Returns:
        推奨ベット比率（0.0〜kelly_fraction の範囲に clamp 済み）
    """
    net_odds = odds - 1.0
    if net_odds <= 0:
        return 0.0
    full_kelly = (est_prob * net_odds - (1.0 - est_prob)) / net_odds
    return max(0.0, kelly_fraction * full_kelly)


def allocate(
    candidates: list[BetCandidate],
    bankroll: int,
    *,
    kelly_fraction: float = 0.25,
    prob_alpha: float = 1.0,
    market_probs: dict[str, float] | None = None,
    max_per_race: int | None = None,
    min_ev: float | None = None,
    max_tickets_override: dict[str, int] | None = None,
) -> list[AllocatedBet]:
    """候補ベット列に対して分数Kelly 配分を行う。

    同一レース内の複数ベットは独立Kelly で計算後、レース合計上限で按分する
    （近似。詳細は docstring 参照）。

    Args:
        candidates: ベット候補列（同一レース内の候補を想定）
        bankroll: 現在のバンクロール（円）
        kelly_fraction: 分数Kelly 係数（既定 0.25 = quarter-Kelly）
        prob_alpha: Shrinkage 係数（1.0=縮小なし, 0.0=市場確率のみ）
        market_probs: combination -> 市場確率 のマッピング（省略時は縮小なし）
        max_per_race: レース内最大投資額（省略時は settings.bet_max_per_race）
        min_ev: EV フィルタ（省略時は settings.bet_min_expected_value）
        max_tickets_override: 券種別最大点数の上書き

    Returns:
        stake > 0 のAllocatedBet 列（MIN_STAKE 未満は除外）
    """
    _max_per_race = max_per_race if max_per_race is not None else settings.bet_max_per_race
    _max_per_ticket = settings.bet_max_per_ticket
    _min_ev = min_ev if min_ev is not None else settings.bet_min_expected_value
    _market_probs = market_probs or {}
    _max_tickets = {**MAX_TICKETS_PER_TYPE, **(max_tickets_override or {})}

    # Step 1: EV フィルタ + Shrinkage + Kelly 計算
    computed: list[tuple[BetCandidate, float, float, float]] = []
    # (candidate, shrunk_prob, ev, kelly_f)

    for cand in candidates:
        if cand.odds < 1.0:
            continue
        mprob = _market_probs.get(cand.combination)
        shrunk = _shrink_probability(cand.est_prob, mprob, prob_alpha)
        ev = shrunk * cand.odds
        if ev < _min_ev:
            continue
        kf = _kelly_fraction_single(shrunk, cand.odds, kelly_fraction)
        if kf <= 0:
            continue
        computed.append((cand, shrunk, ev, kf))

    if not computed:
        return []

    # Step 2: 券種別点数制約（EV 降順で上位 N 点を残す）
    from collections import defaultdict
    by_type: dict[str, list[tuple[BetCandidate, float, float, float]]] = defaultdict(list)
    for item in computed:
        by_type[item[0].bet_type].append(item)

    filtered: list[tuple[BetCandidate, float, float, float]] = []
    for btype, items in by_type.items():
        limit = _max_tickets.get(btype, 99)
        # EV 降順でソートして上位 N 点を残す
        items_sorted = sorted(items, key=lambda x: x[2], reverse=True)
        filtered.extend(items_sorted[:limit])

    if not filtered:
        return []

    # Step 3: 独立 Kelly でステーク計算
    raw_stakes: list[tuple[BetCandidate, float, float, float, int]] = []
    # (candidate, shrunk_prob, ev, kelly_f, raw_stake)
    for cand, shrunk, ev, kf in filtered:
        raw = kf * bankroll
        # max_per_ticket で clamp
        raw = min(raw, _max_per_ticket)
        raw_stakes.append((cand, shrunk, ev, kf, int(raw)))

    total_raw = sum(r[4] for r in raw_stakes)

    # Step 4: レース内予算で按分（近似）
    if total_raw > _max_per_race and total_raw > 0:
        scale = _max_per_race / total_raw
        raw_stakes = [
            (cand, shrunk, ev, kf, int(s * scale))
            for cand, shrunk, ev, kf, s in raw_stakes
        ]

    # Step 5: 100円単位に丸め + MIN_STAKE フィルタ
    result: list[AllocatedBet] = []
    for cand, shrunk, ev, kf, raw in raw_stakes:
        stake = _round_stake(raw)
        if stake < MIN_STAKE:
            continue
        result.append(
            AllocatedBet(
                bet_type=cand.bet_type,
                combination=cand.combination,
                stake=stake,
                kelly_f=kf,
                shrunk_prob=shrunk,
                ev=ev,
                tag=cand.tag,
            )
        )

    return result


def _round_stake(amount: float) -> int:
    """金額を STAKE_UNIT（100円）単位に切り捨て丸めする。

    Args:
        amount: 丸め前の金額

    Returns:
        100円単位に切り捨てた金額（int）
    """
    return int(math.floor(amount / STAKE_UNIT)) * STAKE_UNIT


@dataclass
class RaceConstraintState:
    """レース間をまたぐ制約状態（日次最大・連敗数）。

    T10 本番化時に FastAPI の状態管理層で利用することを想定。
    本モジュールでは状態管理のデータ構造のみ定義する（副作用なし）。
    """

    day_spent: int = 0       # 当日累計投資額（円）
    consecutive_losses: int = 0  # 連続不的中レース数

    def check_daily_budget(self, planned: int) -> int:
        """当日上限を考慮した実際の投資可能額を返す。

        Args:
            planned: 計画投資額

        Returns:
            実際に投資可能な額（day_spent + planned > bet_max_per_day の場合は削減）
        """
        remaining = settings.bet_max_per_day - self.day_spent
        return max(0, min(planned, remaining))

    def is_halted(self) -> bool:
        """連敗数が上限に達している場合 True を返す。"""
        return self.consecutive_losses >= settings.bet_max_consecutive_losses

    def record_race_result(self, *, spent: int, hit: bool) -> RaceConstraintState:
        """レース結果を記録し、新しい状態を返す（イミュータブル更新）。

        Args:
            spent: このレースの投資額
            hit: 的中したか

        Returns:
            更新後の RaceConstraintState
        """
        return RaceConstraintState(
            day_spent=self.day_spent + spent,
            consecutive_losses=0 if hit else self.consecutive_losses + 1,
        )
