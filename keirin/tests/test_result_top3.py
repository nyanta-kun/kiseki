"""同着（デッドヒート）の採点が壊れないことを固定する（2026-08-22 新設）。

## なぜ要るか

競輪には同着がある。3着が2車同着なら三連複の当たりは**2通り**になる。
2026-08-22 の監査時点で、採点は全経路が

    SELECT frame_no ... WHERE finish_order BETWEEN 1 AND 3 ORDER BY finish_order
    order_list[:3]

と書かれており当たりを1通りしか作っていなかった。もう一方を買っていた場合は
`hit=0` で記録され、**例外もログも出ない**。実データで 8件が的中なのに外れとして
残っていた（RANK_7B 3 / 7S 2 / 7C 1 / 7M1 1 / 9C 1・2024-01〜2026-08）。

さらに `ORDER BY finish_order` にタイブレークが無く、**再構築のたびに
どちらの目が正解になるか変わりうる**（台帳の再現性が壊れる）。

ここは「壊れても静かなので、経路そのものを固定する」タイプの検査。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.result_top3 import (
    TOP3_SQL,
    hit_trifecta,
    hit_trio,
    is_dead_heat,
    representative,
    winning_trifectas,
    winning_trios,
)

ROOT = Path(__file__).resolve().parent.parent

# 実データ（20260614_23_06）: 1着4番・2着3番・3着は1番と7番の同着。
# RANK_7B は `7=3-5,4,2`（軸 7,3 / 相手 5,4,2）を買っており {3,4,7} で的中だが、
# 旧実装は {1,3,4} 側だけを正解にしていたため hit=0 で記録されていた。
REAL_DEAD_HEAT = [(1, 4), (2, 3), (3, 1), (3, 7)]


def test_通常のレースは当たり目がひとつ():
    assert winning_trios([(1, 5), (2, 2), (3, 7)]) == [frozenset({2, 5, 7})]
    assert winning_trifectas([(1, 5), (2, 2), (3, 7)]) == [(5, 2, 7)]
    assert is_dead_heat([(1, 5), (2, 2), (3, 7)]) is False


def test_3着同着は三連複も三連単も2通り():
    assert winning_trios(REAL_DEAD_HEAT) == [frozenset({1, 3, 4}), frozenset({3, 4, 7})]
    assert winning_trifectas(REAL_DEAD_HEAT) == [(4, 3, 1), (4, 3, 7)]
    assert is_dead_heat(REAL_DEAD_HEAT) is True


def test_2着同着は三連複1通り_三連単2通り():
    fin = [(1, 4), (2, 3), (2, 5)]
    assert winning_trios(fin) == [frozenset({3, 4, 5})]
    assert winning_trifectas(fin) == [(4, 3, 5), (4, 5, 3)]


def test_1着同着は三連複1通り_三連単2通り():
    fin = [(1, 1), (1, 2), (3, 5)]
    assert winning_trios(fin) == [frozenset({1, 2, 5})]
    assert winning_trifectas(fin) == [(1, 2, 5), (2, 1, 5)]


def test_3車そろわなければ未確定として空を返す():
    assert winning_trios([(1, 1), (2, 2)]) == []
    assert winning_trifectas([(1, 1), (2, 2)]) == []
    assert winning_trios([]) == []
    # 4着以下は3着以内ではない
    assert winning_trios([(1, 1), (2, 2), (4, 3)]) == []


def test_行の並び順に依らず結果が同じ():
    """🔴 `ORDER BY finish_order` はタイブレークが無い。入力順で結果が変わってはならない。"""
    a = winning_trios(REAL_DEAD_HEAT)
    b = winning_trios(list(reversed(REAL_DEAD_HEAT)))
    c = winning_trios([(3, 7), (1, 4), (3, 1), (2, 3)])
    assert a == b == c
    assert (winning_trifectas(REAL_DEAD_HEAT)
            == winning_trifectas(list(reversed(REAL_DEAD_HEAT))))


def test_同着で買った目のほうが当たりとして返る():
    """払戻は当たり目ごとに違うので、**買った目**が返らなければ金額を間違える。"""
    wins = winning_trios(REAL_DEAD_HEAT)
    bought = [frozenset({3, 4, 7}), frozenset({3, 4, 5}), frozenset({2, 3, 4})]
    assert hit_trio(bought, wins) == frozenset({3, 4, 7})
    # もう一方の目を買っていた場合はそちらが返る
    assert hit_trio([frozenset({1, 3, 4})], wins) == frozenset({1, 3, 4})
    # どちらも買っていなければ None
    assert hit_trio([frozenset({2, 5, 6})], wins) is None


def test_三連単も買った目が返る():
    wins = winning_trifectas(REAL_DEAD_HEAT)
    assert hit_trifecta([(4, 3, 7), (4, 3, 2)], wins) == (4, 3, 7)
    assert hit_trifecta([(3, 4, 7)], wins) is None      # 着順違いは当たりではない
    assert hit_trifecta([], wins) is None


def test_representative_は決定的():
    assert representative(winning_trios(REAL_DEAD_HEAT)) == frozenset({1, 3, 4})
    assert representative([]) is None


# ─────────────────────────────────────────────────────────────────────────
# 経路の固定 — 採点スクリプトが「先頭3件だけ」に戻らないこと
# ─────────────────────────────────────────────────────────────────────────

#: 採点して picks_history へ書く経路。ここが単一正解に戻ると静かに的中を落とす。
SCORING_FILES = [
    "scripts/notify_results_wt.py",
    *[f"scripts/backfill_{r}_rank_wt.py"
      for r in ("7a", "7b", "7c", "7s", "7ss", "7m1", "7h1", "7t1",
                "9a", "9s", "9c", "s1w")],
]

#: 旧実装の形。`ORDER BY finish_order` を直書きすると同着でタイブレークが無い。
_OLD_SQL = re.compile(r'"ORDER BY finish_order"|ORDER BY finish_order"')


@pytest.mark.parametrize("rel", SCORING_FILES)
def test_採点経路が単一正解の旧実装に戻っていない(rel):
    src = (ROOT / rel).read_text(encoding="utf-8")
    assert "from src.result_top3 import" in src, (
        f"{rel}: 同着対応（src/result_top3）を通していない")
    assert not _OLD_SQL.search(src), (
        f"{rel}: `ORDER BY finish_order` の直書きが残っている。"
        f"タイブレークが無く同着で正解が入れ替わる。TOP3_SQL を使うこと")


def test_TOP3_SQL_は車番でタイブレークする():
    assert "ORDER BY finish_order, frame_no" in TOP3_SQL
    assert "finish_order, frame_no" in TOP3_SQL.split("SELECT", 1)[1].split("FROM", 1)[0]
