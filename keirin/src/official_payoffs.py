"""公式払戻（100円あたり）の取り込みと読み出し — **採点の払戻はここだけから引く**（2026-10-13 新設）。

## なぜ要るか

採点はこれまで**発売締切時の最終オッズ（`wt_odds.odds_value`）× 賭け金**で払戻を
作っていた。同着でない決着ならこれは公式払戻と一致するが、**同着では一致しない**。
同着は売上を当たり目の間で分けて払うので、公式払戻はオッズの約半分になる:

    20261013_81_05（小倉5R・3着 2番・3番 同着）
      三連単 1-5-2  最終オッズ 52.1倍  → 公式払戻 2,620円
      三連単 1-5-3  最終オッズ 118.9倍 → 公式払戻 5,890円
      L_lead（2,500円×2点）  採点 427,500円 ↔ 実際 212,750円

ユーザー判断（2026-10-13）で、**同着に限らず全レースを公式払戻で採点する**。

## 出どころ

winticket のオッズページ（`WinticketScraper.fetch_payoffs` → `parse_payoffs`）。
券種ごとの `*PayoffStatus` が 1 になってから取り込む。結果が出た直後は 0 なので、
**着順の取り込み（`collect-wt`）とは別に、採点の直前に取りに行く**
（`collect-wt` は着順が入ったレースを二度と取りに行かないため、そこに相乗りすると
確定前の空振りが永久に残る）。

## 呼び出し側の約束

- **払戻が無ければ採点しない**（0 を書くと外れと区別できない）。
  オッズで代用しない——代用すると同着で2倍に化ける
- 表記は `wt_odds` と同じ（券種を問わず `-` 区切り・三連複は昇順）。
  `key()` で作ること
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Iterable, Sequence

from src.database import get_connection

logger = logging.getLogger(__name__)

#: 採点で使う券種。両方そろって初めて「そのレースは取り込み済み」とみなす。
MARKETS = ("trio", "trifecta")


def key(bet_type: str, cars: Sequence[int] | str) -> str:
    """当たり目 → `wt_payoffs.combination` の表記（`1-2-3`・三連複は昇順）。

    `"1=2=5"` / `"1-5-2"` のような文字列も受ける。
    """
    if isinstance(cars, str):
        sep = "=" if "=" in cars else "-"
        cars = [int(x) for x in cars.split(sep) if x.strip()]
    nums = [int(c) for c in cars]
    if bet_type == "trio":
        nums = sorted(nums)
    return "-".join(str(n) for n in nums)


def load(race_keys: Iterable[str]) -> dict[str, dict[tuple[str, str], int]]:
    """{race_key: {(券種, 表記): 100円あたり払戻}}。行が無いレースは含めない。"""
    ks = sorted({str(k) for k in race_keys if k})
    out: dict[str, dict[tuple[str, str], int]] = defaultdict(dict)
    if not ks:
        return {}
    with get_connection() as c:
        for i in range(0, len(ks), 900):
            ch = ks[i:i + 900]
            q = ("SELECT race_key, bet_type, combination, payoff FROM wt_payoffs "
                 f"WHERE race_key IN ({','.join('?' * len(ch))})")
            for rk, bt, comb, pay in c.execute(q, ch).fetchall():
                out[str(rk)][(str(bt), str(comb))] = int(pay)
    return dict(out)


def by_label(finishes: dict[str, list[tuple[int, int]]]) -> dict[str, dict[str, int]]:
    """{race_key: {当たり目の表記: 100円あたり公式払戻}}（`settle` に渡す形）。

    表記は三連複 `1=2=4`・三連単 `1-2-4`（`winning_combo_labels` が正本）。
    同着の当たり目はすべて引く。払戻がまだ無い目はキーを作らない（＝未確定）。
    """
    from src.sold_performance import winning_combo_labels   # 循環 import を避ける
    raw = load(finishes.keys())
    out: dict[str, dict[str, int]] = {}
    for rk, rows in finishes.items():
        pm = raw.get(rk, {})
        m: dict[str, int] = {}
        for label in winning_combo_labels(rows):
            bt = "trio" if "=" in label else "trifecta"
            got = pm.get((bt, key(bt, label)))
            if got:
                m[label] = got
        out[rk] = m
    return out


def missing(race_keys: Iterable[str]) -> list[str]:
    """3着までの着順が入っているのに、公式払戻が券種どちらか欠けているレース。"""
    ks = sorted({str(k) for k in race_keys if k})
    if not ks:
        return []
    finished: set[str] = set()
    have: dict[str, set[str]] = defaultdict(set)
    with get_connection() as c:
        for i in range(0, len(ks), 900):
            ch = ks[i:i + 900]
            ph = ",".join("?" * len(ch))
            for (rk,) in c.execute(
                    f"SELECT DISTINCT race_key FROM wt_entries WHERE race_key IN ({ph}) "
                    "AND finish_order BETWEEN 1 AND 3", ch).fetchall():
                finished.add(str(rk))
            for rk, bt in c.execute(
                    f"SELECT DISTINCT race_key, bet_type FROM wt_payoffs "
                    f"WHERE race_key IN ({ph})", ch).fetchall():
                have[str(rk)].add(str(bt))
    return [k for k in ks if k in finished and not set(MARKETS) <= have[k]]


def collect(race_keys: Iterable[str], scraper=None) -> dict[str, int]:
    """公式払戻を winticket から取り込む。取り込み済みのレースは取りに行かない。

    Returns: {"target", "saved", "unconfirmed", "failed", "empty"}
      - unconfirmed: まだ `PayoffStatus=0`（次の回で取り直す）
      - empty: 確定しているのに払戻のある目が無い（特払い・不成立など）。
        **採点は保留のまま**になるので件数とレースを必ずログに出す
    """
    todo = missing(race_keys)
    stats = {"target": len(todo), "saved": 0, "unconfirmed": 0, "failed": 0, "empty": 0}
    if not todo:
        return stats
    if scraper is None:
        from src.scraper.winticket import WinticketScraper
        scraper = WinticketScraper()

    with get_connection() as c:
        meta = {}
        for i in range(0, len(todo), 900):
            ch = todo[i:i + 900]
            for rk, vid, rd, rno, cup, day in c.execute(
                    "SELECT race_key, venue_id, race_date, race_no, cup_id, day_index "
                    f"FROM wt_races WHERE race_key IN ({','.join('?' * len(ch))})",
                    ch).fetchall():
                meta[str(rk)] = (str(vid), str(rd), int(rno), str(cup), int(day))

    for rk in todo:
        m = meta.get(rk)
        if m is None:
            stats["failed"] += 1
            logger.warning(f"[payoff] wt_races に無い {rk}")
            continue
        try:
            got = scraper.fetch_payoffs(*m)
        except Exception as e:                       # noqa: BLE001  1件の失敗で止めない
            got = None
            logger.warning(f"[payoff] 取得失敗 {rk}: {e}")
        if got is None:
            stats["failed"] += 1
            continue
        if not all(got["confirmed"].get(bt) for bt in MARKETS):
            stats["unconfirmed"] += 1
            continue
        items = got["items"]
        if not {it["bet_type"] for it in items} >= set(MARKETS):
            stats["empty"] += 1
            logger.warning(f"[payoff] 確定済みなのに払戻のある目が無い券種がある {rk} "
                           f"（特払い・不成立の可能性。採点は保留のまま）")
        if not items:
            continue
        with get_connection() as c:
            c.executemany(
                "INSERT OR REPLACE INTO wt_payoffs (race_key, bet_type, combination, payoff) "
                "VALUES (?, ?, ?, ?)",
                [(rk, it["bet_type"], it["combination"], it["payoff"]) for it in items])
            c.commit()
        stats["saved"] += 1
    return stats
