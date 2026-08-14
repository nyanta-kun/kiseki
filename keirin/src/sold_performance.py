"""**実際に売った商品**の成績集計（2026-08-15 新設）。

## なぜ picks_history と別に要るのか

picks_history は**ペーパー成績**（各ランクが条件を満たした全レース）で、
netkeirin で実際に売れるのは **1レース1商品**だけ。両者は母集団が違う:

  - 売っていないのに picks_history にはある（他ランクに商品を譲ったレース）
  - 売ったのに picks_history には無い（**看板の穴埋め** 233件・全入稿の49%）

そのため picks_history をいくら足しても「いくら売って、いくら返ってきたか」は出ない。
`/stats` の「全入稿」トグル（`_manual_submission_buckets`）は
**picks_history + 穴埋め**の混成で、これはこれで
「1レースに1商品」を守るために *売った 7A の穴埋めではなく、売っていない 7T1 の
ペーパー行を計上する*ことがある（8月の穴埋め172件中83件がこの経路で落ちる）。

ここは情報源を **`netkeirin_submissions` + `bet_detail` だけ**に固定する。
1レース1商品なので二重計上が構造的に起こらない。

## 🔴 「的中」は2種類ある

netkeirin の**表示的中率は `n_hits_excl_garami`**（払戻＞賭け金）で、ガミ
（当たったのに損）を不的中として数える。素の的中率だけを見ると、
点数を増やしたときに「改善した」と誤読する（2026-08-15 に実際にやった）。
両方返すこと。

## ⚠️ 使えない期間がある

`bet_detail` の保存は **2026-08-07 開始**。それ以前の入稿は「売った事実」しか
残っておらず買い目も金額も復元できない。0円として足すと投資額を過小に見せるので
**集計から外し、件数だけ返す**（黙って落とすと完全な数字に見える）。

DB にも FastAPI にも依存しない純関数（`keirin_sales_analysis.py` と同じ方針）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

#: `bet_detail.lines[].bet_type` → 着順を見るか（三連単だけ順序が要る）
_ORDERED = {"3連単": True, "3連複": False}


@dataclass
class SoldRace:
    """売った1レース分（＝1商品）。"""

    race_key: str
    race_date: str
    rank_key: str
    origin: str | None
    bet: int
    payout: int
    hit: bool
    #: 払戻 >= 賭け金。**netkeirin の表示的中はこちら**
    net_hit: bool
    n_points: int


@dataclass
class SoldSummary:
    n_races: int = 0
    n_hits: int = 0
    n_net_hits: int = 0
    bet: int = 0
    payout: int = 0
    #: `bet_detail` が無く集計できなかった件数（2026-08-07 以前）
    n_no_detail: int = 0
    payouts: list[int] = field(default_factory=list)

    @property
    def hit_rate(self) -> float | None:
        return self.n_hits / self.n_races if self.n_races else None

    @property
    def net_hit_rate(self) -> float | None:
        """netkeirin の表示的中率（ガミを不的中として数える）。"""
        return self.n_net_hits / self.n_races if self.n_races else None

    @property
    def roi(self) -> float | None:
        return self.payout / self.bet if self.bet else None

    @property
    def gami_rate(self) -> float | None:
        """当たったうちガミだった割合。"""
        return (self.n_hits - self.n_net_hits) / self.n_hits if self.n_hits else None

    @property
    def median_payout(self) -> int | None:
        """的中したレースの払戻の中央値（外れは含めない）。"""
        if not self.payouts:
            return None
        p = sorted(self.payouts)
        return p[len(p) // 2]


def _as_dict(bet_detail: Any) -> dict | None:
    if bet_detail is None:
        return None
    if isinstance(bet_detail, str):
        try:
            return json.loads(bet_detail)
        except (TypeError, ValueError):
            return None
    return dict(bet_detail) if isinstance(bet_detail, Mapping) else None


def _combo_key(combo: str, ordered: bool) -> tuple[int, ...]:
    """'5-6-1' / '1=2=3' を比較可能な形にする。三連複は順不同へ畳む。"""
    parts = [int(x) for x in str(combo).replace("=", "-").split("-")]
    return tuple(parts) if ordered else tuple(sorted(parts))


def settle_submission(
    bet_detail: Any,
    order3: Sequence[int] | None,
    payout_per_100: Mapping[tuple[str, tuple[int, ...]], int] | None = None,
) -> tuple[int, int, bool] | None:
    """1入稿を採点して (投資, 払戻, 的中) を返す。採点できないなら None。

    Args:
        bet_detail: `netkeirin_submissions.bet_detail`
        order3: 確定した1〜3着の車番（着順）。確定していなければ None
        payout_per_100: `{(券種, 目): 100円あたりの確定配当}`。
            **確定配当が正**で、`bet_detail.odds`（入稿時点のオッズ）は
            発走までに動くので払戻計算には使わない。渡されない・欠けている
            ときだけ `odds` へフォールバックする。

    🔴 券種は行ごとに違いうる（7H2 は三連単と三連複を1商品で売る）。
       `bet_type` を無視して一律で畳むと、三連単の着順違いを的中に数える。
    """
    d = _as_dict(bet_detail)
    if not d:
        return None
    lines = d.get("lines") or []
    if not lines:
        return None
    if not order3 or len(order3) < 3:
        return None

    bet = payout = 0
    hit = False
    for line in lines:
        stake = int(line.get("stake") or 0)
        bet += stake
        kind = str(line.get("bet_type") or "")
        ordered = _ORDERED.get(kind)
        if ordered is None:            # 未知の券種は採点しない（黙って外れにしない）
            return None
        want = tuple(order3[:3]) if ordered else tuple(sorted(order3[:3]))
        if _combo_key(line.get("combo"), ordered) != want:
            continue
        hit = True
        per100 = (payout_per_100 or {}).get((kind, want))
        if per100:
            payout += int(per100) * stake // 100
        else:                          # 確定配当が引けないときだけ入稿時オッズで代用
            payout += int(round(float(line.get("odds") or 0) * stake))
    if bet <= 0:
        return None
    return bet, payout, hit


def build_sold_races(
    submissions: Iterable[Mapping[str, Any]],
    finishes: Mapping[str, Sequence[int]],
    payouts: Mapping[str, Mapping[tuple[str, tuple[int, ...]], int]] | None = None,
) -> tuple[list[SoldRace], int]:
    """入稿の一覧から `SoldRace` を組み立てる。

    submissions の各要素に必要なキー:
      `race_key` / `race_date` / `rank_key` / `bet_detail`（`origin` は任意）
      **取消済み（deleted）は呼び出し側で除いておくこと**——商品ではないため。

    returns (売れたレース, 採点できなかった件数)
    """
    out: list[SoldRace] = []
    skipped = 0
    for s in submissions:
        rk = str(s.get("race_key") or "")
        got = settle_submission(
            s.get("bet_detail"), finishes.get(rk), (payouts or {}).get(rk))
        if got is None:
            skipped += 1
            continue
        bet, pay, hit = got
        d = _as_dict(s.get("bet_detail")) or {}
        out.append(SoldRace(
            race_key=rk, race_date=str(s.get("race_date") or ""),
            rank_key=str(s.get("rank_key") or ""), origin=s.get("origin"),
            bet=bet, payout=pay, hit=hit, net_hit=hit and pay >= bet,
            n_points=len(d.get("lines") or []),
        ))
    return out, skipped


def summarize(races: Iterable[SoldRace], n_no_detail: int = 0) -> SoldSummary:
    s = SoldSummary(n_no_detail=n_no_detail)
    for r in races:
        s.n_races += 1
        s.bet += r.bet
        s.payout += r.payout
        s.n_hits += int(r.hit)
        s.n_net_hits += int(r.net_hit)
        if r.hit:
            s.payouts.append(r.payout)
    return s


def group_by(races: Iterable[SoldRace], key: str) -> dict[str, SoldSummary]:
    """`rank_key` / `race_date` / `origin` 等で束ねる。"""
    buckets: dict[str, list[SoldRace]] = {}
    for r in races:
        buckets.setdefault(str(getattr(r, key, "") or "—"), []).append(r)
    return {k: summarize(v) for k, v in sorted(buckets.items())}
