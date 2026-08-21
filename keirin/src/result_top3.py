"""確定着順から「3着以内の当たり目」を作る唯一の正本（2026-08-22 新設）。

## なぜ要るか

競輪には**同着**がある。3着が2車同着なら、その1レースの三連複の当たりは
**2通り**（1着・2着・同着のどちらか）、三連単も**2通り**になる。

それまでの採点は全経路で

    SELECT frame_no FROM wt_entries WHERE finish_order BETWEEN 1 AND 3 ORDER BY finish_order
    order_list[:3]

と書かれており、**当たりを1通りしか作らない**。もう一方を買っていた場合は
`hit=0` で記録される。**例外もログも出ない。**

- 同着レースは実測 237 / 102,221 = **0.232%**
- 掛かった picks 64件のうち **8件が的中なのに hit=0** だった
  （RANK_7B 3 / 7S 2 / 7C 1 / 7M1 1 / 9C 1・2024-01〜2026-08）

🔴 **さらに `ORDER BY finish_order` にタイブレークが無い。** 同着の2車のうち
   どちらが `[:3]` に残るかは行順まかせなので、**同じレースを再構築するたびに
   正解が入れ替わりうる**。台帳の再現性そのものが壊れる。ここでは必ず
   `(finish_order, frame_no)` で並べ、当たり目も車番昇順で生成する。

## 使い方

    fin = conn.execute(TOP3_SQL, (race_key,)).fetchall()   # (finish_order, frame_no)
    wins = winning_trios(fin)                # [frozenset, ...]（同着なら複数）
    key  = hit_trio(combos, wins)            # 買った目のうち当たったもの / None
    pay  = pm[rk].get(("trio", key or representative(wins)), 0)

⚠️ **払戻は当たり目ごとに違う。** 的中したときは必ず「**自分が買った当たり目**」の
   払戻を引くこと（`key`）。外れたときの記録用には `representative()` を使う
   （決定的に1つ選ぶだけで、「その値がレースの払戻だ」という意味ではない）。
"""
from __future__ import annotations

from itertools import permutations
from typing import Iterable, Sequence

#: 3着以内の確定着順を引く SQL。**タイブレークまで含めてここが正本**。
TOP3_SQL = (
    "SELECT finish_order, frame_no FROM wt_entries "
    "WHERE race_key=? AND finish_order BETWEEN 1 AND 3 "
    "ORDER BY finish_order, frame_no"
)


def normalize(finishers: Iterable[Sequence[int]]) -> list[tuple[int, int]]:
    """`(finish_order, frame_no)` の並びへ揃える（1〜3着のみ・決定的な順）。"""
    out: list[tuple[int, int]] = []
    for row in finishers or []:
        try:
            fo, fno = int(row[0]), int(row[1])
        except (TypeError, ValueError, IndexError):
            continue
        if 1 <= fo <= 3:
            out.append((fo, fno))
    return sorted(out)


def _groups(finishers: Iterable[Sequence[int]]) -> list[tuple[int, list[int]]]:
    """着順ごとの同着グループ（着順昇順・グループ内は車番昇順）。"""
    fin = normalize(finishers)
    out: list[tuple[int, list[int]]] = []
    for fo, fno in fin:
        if out and out[-1][0] == fo:
            out[-1][1].append(fno)
        else:
            out.append((fo, [fno]))
    return out


def winning_trifectas(finishers: Iterable[Sequence[int]]) -> list[tuple[int, ...]]:
    """三連単の当たり目を**すべて**返す（同着は着順の入れ替えぶん増える）。

    同着グループは**その中の並び順すべてが的中**になる。3着の境界を跨ぐ
    グループは「どの車が入るか × その順」の両方が増える。

    >>> winning_trifectas([(1, 4), (2, 3), (3, 1), (3, 7)])   # 3着同着
    [(4, 3, 1), (4, 3, 7)]
    >>> winning_trifectas([(1, 1), (1, 2), (3, 5)])           # 1着同着
    [(1, 2, 5), (2, 1, 5)]
    >>> winning_trifectas([(1, 4), (2, 3), (2, 5)])           # 2着同着
    [(4, 3, 5), (4, 5, 3)]
    >>> winning_trifectas([(1, 1), (2, 2)])                   # 3車未満は未確定
    []
    """
    groups = _groups(finishers)
    if sum(len(g) for _, g in groups) < 3:
        return []
    options: list[tuple[int, ...]] = [()]
    remaining = 3
    for _fo, frames in groups:
        if remaining <= 0:
            break
        take = min(len(frames), remaining)
        options = [p + q for p in options
                   for q in permutations(frames, take)]
        remaining -= take
    return sorted(options)


def winning_trios(finishers: Iterable[Sequence[int]]) -> list[frozenset[int]]:
    """三連複の当たり目を**すべて**返す（同着なら複数・決定的な順）。

    >>> winning_trios([(1, 4), (2, 3), (3, 1), (3, 7)])
    [frozenset({1, 3, 4}), frozenset({3, 4, 7})]
    >>> winning_trios([(1, 1), (2, 2), (3, 5)])
    [frozenset({1, 2, 5})]
    >>> winning_trios([(1, 1), (1, 2), (3, 5)])   # 1着同着は1通り
    [frozenset({1, 2, 5})]
    >>> winning_trios([(1, 1), (2, 2)])           # 3車未満は未確定
    []
    """
    seen: list[frozenset[int]] = []
    for t in winning_trifectas(finishers):
        key = frozenset(t)
        if key not in seen:
            seen.append(key)
    return sorted(seen, key=lambda s: sorted(s))


def is_dead_heat(finishers: Iterable[Sequence[int]]) -> bool:
    """3着以内の当たりが2通り以上になる同着か。"""
    return len(winning_trios(finishers)) > 1


def hit_trio(combos: Iterable[frozenset[int]],
             wins: Sequence[frozenset[int]]) -> frozenset[int] | None:
    """買った三連複のうち当たったものを返す（無ければ None）。

    🔴 返すのは**買った目**。払戻はこの目で引くこと（同着では目ごとに払戻が違う）。
    """
    bought = list(combos or [])
    for w in wins or []:
        if w in bought:
            return w
    return None


def hit_trifecta(combos: Iterable[Sequence[int]],
                 wins: Sequence[tuple[int, ...]]) -> tuple[int, ...] | None:
    """買った三連単のうち当たったものを返す（無ければ None）。"""
    bought = {tuple(int(x) for x in c) for c in (combos or [])}
    for w in wins or []:
        if w in bought:
            return w
    return None


def representative(wins: Sequence):
    """外れたときに記録へ残す代表の当たり目（決定的に先頭を選ぶだけ）。

    ⚠️ 同着では当たり目が複数ありそれぞれ払戻が違う。この値を
       「このレースの払戻」として集計に使わないこと。
    """
    return wins[0] if wins else None
