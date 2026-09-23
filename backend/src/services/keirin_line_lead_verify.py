"""逃げ先頭ライン（`L_lead`）の検証集計（2026-09-24 新設・**DB にも FastAPI にも依存しない純関数**）。

`L_lead` は型ラボの**検証中・入稿しない**ランク（keirin `src/type_lab.py` の
`line_lead_legs`）。ここでは「もし売っていたら」を見るために、次の3つを並べる:

| 区分 | 中身 |
|---|---|
| 新ランク | `type_lab_picks`（`plan_key='L_lead'`・7車の実地）。1レース1万円の均等買い |
| 買わなくなるランク | **同じレースで実際に netkeirin へ出した商品**（送信済み・公開済み） |
| ポートフォリオ | 実際に売った全商品（現行） ↔ 新ランクのレースだけ置き換えた場合 |

🔴 **置き換えの定義**: 新ランクの行があるレースでは、そのレースで売った商品を**外して**
   新ランクを入れる。売っていなかったレースには新ランクを**足す**。それ以外は現行のまま。
🔴 **成績は採点済みだけで数える。** 未採点は `pending` に数え、ROI に混ぜない
   （発走前の行を 0 円払戻として数えると回収率が下がって見える）。
🔴 **的中は2種類**。`hit`＝当たり目を買っていた ／ `shown_hit`＝払戻 > 投資
   （netkeirin の表示的中）。売った商品の側は `shown_hit` しか比べられない。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field


@dataclass(frozen=True)
class LeadRow:
    """`type_lab_picks` の `L_lead` 1行。"""

    race_key: str
    race_date: str  # YYYY-MM-DD
    venue_name: str | None
    race_no: int | None
    race_type: str | None
    start_at: int | None  # UNIX 秒（並べ替え用）
    combos: tuple[str, ...]
    stakes: tuple[int, ...]
    settled: bool
    hit: bool
    payout: int
    void_refund: int = 0
    win_combo: str | None = None

    @property
    def invest(self) -> int:
        """実際の投資額（欠車の返還を引く）。"""
        return sum(self.stakes) - int(self.void_refund or 0)


@dataclass(frozen=True)
class SoldRow:
    """`netkeirin_submissions` の実際に出した商品 1件。"""

    race_key: str
    rank_key: str
    bet: int
    payout: int
    settled: bool


@dataclass
class Tally:
    """件数・投資・払戻の合計。"""

    n: int = 0
    pending: int = 0
    invest: int = 0
    payout: int = 0
    hits: int = 0
    shown_hits: int = 0
    max_payout: int = 0
    payouts: list[int] = field(default_factory=list)

    def add(self, invest: int, payout: int, settled: bool, hit: bool | None = None) -> None:
        if not settled:
            self.pending += 1
            return
        self.n += 1
        self.invest += int(invest)
        self.payout += int(payout)
        self.hits += int(bool(hit if hit is not None else payout > 0))
        self.shown_hits += int(payout > invest)
        self.max_payout = max(self.max_payout, int(payout))
        self.payouts.append(int(payout))

    @property
    def roi(self) -> float | None:
        return round(self.payout / self.invest * 100, 1) if self.invest else None

    def roi_without_top(self, k: int = 3) -> float | None:
        """払戻の大きい上位 k 本を除いた回収率（**大当たりへの依存を見る**）。"""
        if not self.invest:
            return None
        top = sum(sorted(self.payouts, reverse=True)[:k])
        return round((self.payout - top) / self.invest * 100, 1)

    def as_dict(self) -> dict:
        return {
            "n": self.n,
            "pending": self.pending,
            "invest": self.invest,
            "payout": self.payout,
            "hits": self.hits,
            "shown_hits": self.shown_hits,
            "max_payout": self.max_payout,
            "roi": self.roi,
        }


def _day(race_key: str) -> str:
    return f"{race_key[:4]}-{race_key[4:6]}-{race_key[6:8]}"


def build_line_lead_report(leads: Iterable[LeadRow], sold: Iterable[SoldRow]) -> dict:
    """日別・レース別・合計の集計を返す。

    戻り値のキー:
      summary … lead / displaced / current / combined（各 `Tally.as_dict()`）と、
                lead_roi_wo_top3・lead_days_over_100・n_days
      days    … 日付の降順。date と lead / displaced / current / combined、diff（収支差）
      races   … 新ランクのレース（発走の早い順・日付の降順）。売っていた商品を並べる

    >>> L = [LeadRow("20260901_31_03", "2026-09-01", "場", 3, "一般", 1, ("1-5-7", "1-5-2"),
    ...              (5000, 5000), True, True, 1113000)]
    >>> S = [SoldRow("20260901_31_03", "C_hit", 10000, 0, True),
    ...      SoldRow("20260901_31_05", "B_hit", 10000, 25000, True)]
    >>> r = build_line_lead_report(L, S)
    >>> r["summary"]["current"]["payout"], r["summary"]["combined"]["payout"]
    (25000, 1138000)
    >>> r["summary"]["displaced"]["n"], r["races"][0]["sold"][0]["rank_key"]
    (1, 'C_hit')
    """
    leads = list(leads)
    sold = list(sold)
    lead_keys = {r.race_key for r in leads}
    sold_by_race: dict[str, list[SoldRow]] = {}
    for s in sold:
        sold_by_race.setdefault(s.race_key, []).append(s)

    days: dict[str, dict[str, Tally]] = {}

    def _t(date: str, name: str) -> Tally:
        return days.setdefault(date, {}).setdefault(name, Tally())

    tot = {k: Tally() for k in ("lead", "displaced", "current", "combined")}

    for r in leads:
        for t in (_t(r.race_date, "lead"), tot["lead"], _t(r.race_date, "combined"), tot["combined"]):
            t.add(r.invest, r.payout, r.settled, r.hit)
    for s in sold:
        d = _day(s.race_key)
        for t in (_t(d, "current"), tot["current"]):
            t.add(s.bet, s.payout, s.settled)
        if s.race_key in lead_keys:
            for t in (_t(d, "displaced"), tot["displaced"]):
                t.add(s.bet, s.payout, s.settled)
        else:
            for t in (_t(d, "combined"), tot["combined"]):
                t.add(s.bet, s.payout, s.settled)

    day_rows = []
    lead_over = 0
    lead_days = 0
    for d in sorted(days, reverse=True):
        v = {k: days[d].get(k, Tally()) for k in ("lead", "displaced", "current", "combined")}
        if v["lead"].invest:
            lead_days += 1
            lead_over += int(v["lead"].payout >= v["lead"].invest)
        diff = (v["combined"].payout - v["combined"].invest) - (v["current"].payout - v["current"].invest)
        day_rows.append({"date": d, **{k: t.as_dict() for k, t in v.items()}, "diff": diff})

    races = []
    for r in sorted(leads, key=lambda x: (x.race_date, -(x.start_at or 0)), reverse=True):
        races.append(
            {
                "race_key": r.race_key,
                "race_date": r.race_date,
                "venue_name": r.venue_name,
                "race_no": r.race_no,
                "race_type": r.race_type,
                "start_at": r.start_at,
                "combos": list(r.combos),
                "stake": (r.stakes[0] if r.stakes else 0),
                "invest": r.invest,
                "settled": r.settled,
                "hit": r.hit,
                "payout": r.payout,
                "win_combo": r.win_combo,
                "sold": [
                    {"rank_key": s.rank_key, "bet": s.bet, "payout": s.payout, "settled": s.settled}
                    for s in sold_by_race.get(r.race_key, [])
                ],
            }
        )

    return {
        "summary": {
            **{k: t.as_dict() for k, t in tot.items()},
            "lead_roi_wo_top3": tot["lead"].roi_without_top(3),
            "lead_days_over_100": lead_over,
            "n_days": lead_days,
        },
        "days": day_rows,
        "races": races,
    }
