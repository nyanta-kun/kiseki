"""確定着順から「3着以内の当たり目」を作る（Web 側・2026-08-22 新設）。

## なぜ要るのか

競輪には**同着**がある。3着が2車同着なら三連複の当たりは**2通り**、
1着/2着の同着なら三連単の当たりが着順の入れ替えぶん増える。
確認画面（`/keirin/review`）で「買い目のどの行が当たったか」を赤字にするには
この判定が要る。

🔴 **フロント（TypeScript）で判定しない。** 看板判定（`keirin_marquee.py`）で
   一度踏んだのと同じ型で、判定が3言語に散ると必ずどこかが古くなる。
   API が当たり目を文字列で返し、フロントは**一致を見るだけ**にする。

## ⚠️ 実装が2つある（統合待ち）

採点側の正本は **`keirin/src/result_top3.py`**（同日新設・PR #252）。
backend は Docker イメージに `backend/` しか入らないため keirin 側を
import できず、やむを得ずここに同じ規則を置いている。

**片方だけ直すと静かに食い違う**ので、
`backend/tests/test_keirin_result_top3.py` が両実装の出力一致を機械的に見る
（keirin 側のファイルをパスで読み込んで同じ入力を通す）。

将来は `keirin_marquee.py` と同じ形——**このファイルを正本にして
keirin 側がパスで読み込む**——へ寄せること。今日それをやらなかったのは、
毎朝 08:40 の再構築（`reconcile_walkforward_tail.sh`）が
`keirin/src/result_top3.py` を import する経路の直前で、
採点の import 形態を変えるのを避けたため。

## 判定

    3着以内に入った車を着順グループに分け、上から3つ分の枠を埋める。
    枠を跨ぐ同着グループは「どの車が入るか × その順」の両方が増える。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from itertools import permutations


def _groups(finishers: Iterable[Sequence[int]]) -> list[tuple[int, list[int]]]:
    """`(着順, 車番)` の並びを着順ごとの同着グループへ（車番昇順で決定的）。"""
    rows: list[tuple[int, int]] = []
    for row in finishers or []:
        try:
            fo, fno = int(row[0]), int(row[1])
        except (TypeError, ValueError, IndexError):
            continue
        if 1 <= fo <= 3:
            rows.append((fo, fno))
    out: list[tuple[int, list[int]]] = []
    for fo, fno in sorted(rows):
        if out and out[-1][0] == fo:
            out[-1][1].append(fno)
        else:
            out.append((fo, [fno]))
    return out


def winning_trifectas(finishers: Iterable[Sequence[int]]) -> list[tuple[int, ...]]:
    """三連単の当たり目をすべて返す（3着まで揃わなければ空）。"""
    groups = _groups(finishers)
    if sum(len(g) for _, g in groups) < 3:
        return []
    options: list[tuple[int, ...]] = [()]
    remaining = 3
    for _fo, frames in groups:
        if remaining <= 0:
            break
        take = min(len(frames), remaining)
        options = [p + q for p in options for q in permutations(frames, take)]
        remaining -= take
    return sorted(options)


def winning_trios(finishers: Iterable[Sequence[int]]) -> list[frozenset[int]]:
    """三連複の当たり目をすべて返す（同着なら複数）。"""
    seen: list[frozenset[int]] = []
    for t in winning_trifectas(finishers):
        key = frozenset(t)
        if key not in seen:
            seen.append(key)
    return sorted(seen, key=lambda s: sorted(s))


def winning_combo_labels(finishers: Iterable[Sequence[int]]) -> list[str]:
    """買い目の表記へ揃えた当たり目（`bet_detail.lines[].combo` と比較できる形）。

    三連複は車番昇順を `=` で、三連単は着順を `-` でつなぐ
    （`netkeirin_submit_wt` が組む表記に合わせてある）。

    >>> winning_combo_labels([(1, 4), (2, 3), (3, 1), (3, 7)])
    ['1=3=4', '3=4=7', '4-3-1', '4-3-7']
    """
    trios = ["=".join(str(x) for x in sorted(s)) for s in winning_trios(finishers)]
    tfs = ["-".join(str(x) for x in t) for t in winning_trifectas(finishers)]
    return trios + tfs
