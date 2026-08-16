"""看板穴埋めの軸決定（9車のライン組み替え）の回帰テスト。

## 背景

穴埋めはランクのゲートを通っていない帯を売る。そこで素の指数上位2車を軸にすると
実測（9車・GIII以上・2025-01〜2026-08 の穴埋め帯 n=1,742）で
二軸 30.4% / ROI 69.4% しか出ない。ラインで組み替えると
二軸 33.8% / 的中 30.9% / ROI 78.5% になり、paired bootstrap で3指標とも有意。

ここで守るのは**規則そのもの**であって数値ではない:

1. 9車で指数1位がライン先頭 → 軸2 はその同ライン最上位（番手）
2. 9車で指数1位が非先頭かつ指数2位がライン先頭 → その先頭＋番手のペアへ組み替える
3. **7車は組み替えない**（未検証。7車の定数・規則を9車へ持ち込む／その逆は
   このプロジェクトが繰り返し事故を起こしている型）
4. ライン情報が無ければ従来どおり指数上位2車へ落ちる

⚠️ 組み替えを外しても例外は出ず、二軸が数pt下がるだけで誰も気づかない。
   テストでしか守れない。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.submit_marquee_wt import _axes  # noqa: E402


def _entry(order: list[int]) -> dict:
    """指数順（`ai_rank` 昇順）に車番を並べた allindex 相当の entry を作る。"""
    return {"riders": [{"frame_no": n, "ai_rank": i + 1} for i, n in enumerate(order)]}


def _lines(spec: dict[int, tuple[int, bool]]) -> dict[int, dict]:
    """{車番: (line_group, is_line_leader)} から `_lines()` 相当の dict を作る。"""
    size: dict[int, int] = {}
    for _n, (g, _l) in spec.items():
        size[g] = size.get(g, 0) + 1
    return {
        n: {"frame_no": n, "line_group": g, "line_size": size[g], "is_line_leader": ld}
        for n, (g, ld) in spec.items()
    }


# 9車・3分戦。ライン A=(1,2,3) B=(4,5,6) C=(7,8,9)、各先頭は 1 / 4 / 7。
THREE_LINES = _lines({
    1: (1, True),  2: (1, False), 3: (1, False),
    4: (2, True),  5: (2, False), 6: (2, False),
    7: (3, True),  8: (3, False), 9: (3, False),
})


def test_rule1_axis1_is_line_leader_takes_its_partner():
    """指数1位=1(先頭) / 2位=5(別線の番手) → 軸2 は 1 の同ライン最上位 2 になる。"""
    order = [1, 5, 2, 4, 7, 3, 8, 6, 9]
    assert _axes(_entry(order), THREE_LINES) == (1, 2)


def test_rule1_partner_follows_index_order_within_the_line():
    """同ラインが複数いるときは**指数順で最上位**を採る（車番順ではない）。"""
    order = [1, 5, 3, 2, 4, 7, 8, 6, 9]   # 同ライン(1,2,3) のうち指数上位は 3
    assert _axes(_entry(order), THREE_LINES) == (1, 3)


def test_rule2_promotes_axis2_leader_and_drops_old_axis1():
    """指数1位=2(番手) / 2位=4(別線の先頭) → {4, その番手5} へ組み替える。

    🔴 旧軸1(2) は軸から外れる。相手側には残るのでここでは検査しない。
    """
    order = [2, 4, 5, 1, 7, 3, 8, 6, 9]
    assert _axes(_entry(order), THREE_LINES) == (4, 5)


def test_rule2_same_line_keeps_the_same_pair():
    """指数1位=2(番手) / 2位=1(同ラインの先頭) → ペアは変わらない。

    三連複は順序を問わないので、ここで軸1/軸2 が入れ替わっても買い目は動かない。
    """
    order = [2, 1, 5, 4, 7, 3, 8, 6, 9]
    assert set(_axes(_entry(order), THREE_LINES)) == {1, 2}


def test_no_change_when_neither_axis_is_a_leader():
    """指数1位も2位も番手 → 従来どおり上位2車。"""
    order = [2, 5, 1, 4, 7, 3, 8, 6, 9]
    assert _axes(_entry(order), THREE_LINES) == (2, 5)


def test_solo_rider_is_not_treated_as_a_leader():
    """単騎（line_size=1）は先頭に数えない。番手がおらず組み替えようがない。"""
    lines = _lines({
        1: (1, True), 2: (1, False), 3: (1, False),
        4: (2, True), 5: (2, False), 6: (2, False),
        7: (3, True),                                  # 7 は単騎
        8: (4, True), 9: (4, False),
    })
    order = [7, 5, 1, 4, 2, 3, 8, 6, 9]                # 指数1位が単騎の 7
    assert _axes(_entry(order), lines) == (7, 5)       # 素の上位2車のまま


def test_seven_car_race_is_never_reordered():
    """🔴 7車では組み替えない（9車でしか検証していない）。"""
    lines = _lines({
        1: (1, True), 2: (1, False), 3: (1, False),
        4: (2, True), 5: (2, False),
        6: (3, True), 7: (3, False),
    })
    order = [1, 5, 2, 4, 6, 3, 7]                      # 9車なら (1,2) になる並び
    assert _axes(_entry(order), lines) == (1, 5)


def test_falls_back_to_index_order_when_line_info_missing():
    """ライン情報が取れない／一部欠けるときは従来どおり上位2車。"""
    order = [1, 5, 2, 4, 7, 3, 8, 6, 9]
    assert _axes(_entry(order), None) == (1, 5)
    assert _axes(_entry(order), {}) == (1, 5)
    partial = {n: v for n, v in THREE_LINES.items() if n != 9}
    assert _axes(_entry(order), partial) == (1, 5)


def test_returns_none_when_fewer_than_two_riders():
    assert _axes({"riders": []}, THREE_LINES) is None
    assert _axes({"riders": [{"frame_no": 1, "ai_rank": 1}]}, THREE_LINES) is None
