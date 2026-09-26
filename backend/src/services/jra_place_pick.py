"""JRA 複勝ピック（1レース最大1頭）の判定 — 単一真実源。

レース詳細の「複勝」バッジと、推奨ページの当月一覧は**必ずこの関数を通す**。
条件を画面ごとに書き写すと、閾値を変えたときに片方だけ古い条件で残る。

## 条件（2026-09-26 探索・`RULE_VERSION` に埋まる）

1. フィールド（単勝オッズがあり、取消・除外でない馬）が **8頭以上**
   （7頭以下は複勝が2着までで、条件が悪い場面になりやすかった）
2. 複勝オッズ（下限）が **3.0 以上 4.0 未満**
3. v28 の複勝確率（`place_probability`）がフィールド内 **5位以内**
4. **単勝オッズ ÷ 複勝オッズ ≤ 3.5**
   （単勝の市場は強いと見ているのに、複勝の市場が買っておらず複勝が割高な馬）
5. 該当が複数なら複勝確率が最も高い1頭。該当が無ければ見送り

## 実測（発走10分前オッズ・2026-03-28〜09-22・8頭以上）

8頭以上の条件を加える前の全体（134点）: 的中 41.0%・複勝回収率 1.348
[1.07, 1.71]。探索期間 38.5% / 1.168、7/1 以降 46.5% / 1.728。

🔴 **7/1 以降（2026Q3）も見ながら条件を絞ったので、確認には使えない。**
採否は 2026Q4 以降の前向き記録（`keiba.hit_tier_picks` の発走前スナップショット）
だけで判断する。v28 は 10/1 の四半期再学習で入れ替わる点にも注意。

⚠️ 標準ライブラリ以外を import しない（DB にも FastAPI にも依存しない純関数）。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

MIN_FIELD = 8
PLACE_ODDS_MIN = 3.0
PLACE_ODDS_MAX = 4.0  # 未満
PLACE_PROB_RANK_MAX = 5
WIN_PLACE_RATIO_MAX = 3.5

RULE_VERSION = (
    f"place_pick,field>={MIN_FIELD},place[{PLACE_ODDS_MIN},{PLACE_ODDS_MAX}),"
    f"pp_rank<={PLACE_PROB_RANK_MAX},win/place<={WIN_PLACE_RATIO_MAX}"
)


@dataclass(frozen=True)
class PlacePickHorse:
    """判定の入力1頭分。取消・除外馬は呼び出し側で渡さないこと。"""

    horse_number: int
    win_odds: float | None
    place_odds: float | None
    place_probability: float | None


def _field(horses: Iterable[PlacePickHorse]) -> list[PlacePickHorse]:
    """単勝オッズのある馬＝市場が見ている出走馬。"""
    return [h for h in horses if h.win_odds is not None and h.win_odds > 0]


def place_probability_ranks(horses: Iterable[PlacePickHorse]) -> dict[int, int]:
    """フィールド内の複勝確率の順位（1=最高・同値は同順位＝min 方式）。"""
    probs = [(h.horse_number, h.place_probability) for h in _field(horses) if h.place_probability is not None]
    return {hn: 1 + sum(1 for _, q in probs if q > p) for hn, p in probs}


def select_place_pick(horses: Iterable[PlacePickHorse]) -> int | None:
    """対象馬の馬番を返す。該当なし（見送り）は None。"""
    field = _field(horses)
    if len(field) < MIN_FIELD:
        return None
    ranks = place_probability_ranks(field)
    candidates = [
        h
        for h in field
        if h.place_odds is not None
        and h.place_probability is not None
        and PLACE_ODDS_MIN <= h.place_odds < PLACE_ODDS_MAX
        and ranks.get(h.horse_number, 99) <= PLACE_PROB_RANK_MAX
        and h.win_odds / h.place_odds <= WIN_PLACE_RATIO_MAX  # type: ignore[operator]
    ]
    if not candidates:
        return None
    best = max(candidates, key=lambda h: (h.place_probability, -h.horse_number))
    return best.horse_number


def place_slots(field_size: int) -> int:
    """複勝の払戻対象着順（8頭以上=3・5〜7頭=2・4頭以下=0）。"""
    if field_size >= 8:
        return 3
    if field_size >= 5:
        return 2
    return 0
