"""地方「激走 / 見送り」判定（`indices/chihou_gekisou.py`）の検査。

判定は「空き枠 room = 3 − 人気1〜3番の好走見込み」と「6番人気以下の最有力馬が
人気1〜3番の誰かをモデル上で押しのけるか」の2段。検証は
docs/chihou_rebuild_2026_08.md 18章。
"""

from __future__ import annotations

import pytest

from src.indices.chihou_gekisou import (
    GEKISOU_MIN_FIELD,
    GEKISOU_ROOM_MIN,
    NO_VERDICT,
    STATUS_GEKISOU,
    STATUS_MIOKURI,
    harville_top_k,
    judge_gekisou,
)

# 10頭立て。馬番 = 人気順（1番が1番人気）になるようオッズを並べる
ODDS_10 = [2.0, 4.0, 6.0, 9.0, 12.0, 18.0, 25.0, 35.0, 50.0, 80.0]


def _race(probs: list[float | None], odds: list[float | None] = ODDS_10) -> dict:
    return {i + 1: (p, o) for i, (p, o) in enumerate(zip(probs, odds, strict=True))}


class TestHarville:
    def test_1着以内は勝率そのもの(self) -> None:
        assert harville_top_k([0.5, 0.3, 0.2], 1) == pytest.approx([0.5, 0.3, 0.2])

    def test_3着以内の合計は3(self) -> None:
        out = harville_top_k([0.4, 0.2, 0.15, 0.1, 0.08, 0.07], 3)
        assert sum(out) == pytest.approx(3.0)

    def test_頭数が3なら全馬が3着以内(self) -> None:
        assert harville_top_k([0.6, 0.3, 0.1], 3) == pytest.approx([1.0, 1.0, 1.0])

    def test_正規化されていない入力も扱える(self) -> None:
        assert harville_top_k([2.0, 1.0, 1.0], 1) == pytest.approx([0.5, 0.25, 0.25])


class Test見送り:
    def test_人気馬が強ければ見送り(self) -> None:
        # 人気1〜3番にモデル確率が集中 → 空き枠が小さい
        v = judge_gekisou(_race([0.9, 0.8, 0.7, 0.2, 0.2, 0.15, 0.1, 0.1, 0.05, 0.05]))
        assert v.status == STATUS_MIOKURI
        assert v.room is not None and v.room < GEKISOU_ROOM_MIN
        assert v.pick is None


class Test激走:
    def test_空き枠があり人気薄が人気馬を押しのければ激走(self) -> None:
        # 3番人気のモデル評価が低く、7番人気が高い。オッズは平たく人気馬も割れている
        odds = [4.0, 4.5, 5.0, 8.0, 9.0, 10.0, 11.0, 14.0, 20.0, 30.0]
        v = judge_gekisou(_race([0.4, 0.35, 0.1, 0.3, 0.3, 0.2, 0.45, 0.2, 0.1, 0.1], odds))
        assert v.status == STATUS_GEKISOU
        assert v.pick == 7
        assert v.pick_pop == 7
        assert v.pick_q is not None and v.fav_min_q is not None
        assert v.pick_q > v.fav_min_q

    def test_人気薄が人気馬の誰も上回らなければ印なし(self) -> None:
        odds = [4.0, 4.5, 5.0, 8.0, 9.0, 10.0, 11.0, 14.0, 20.0, 30.0]
        v = judge_gekisou(_race([0.4, 0.35, 0.3, 0.3, 0.3, 0.2, 0.25, 0.2, 0.1, 0.1], odds))
        assert v.status is None
        assert v.room is not None and v.room >= GEKISOU_ROOM_MIN
        assert v.pick is None

    def test_激走は1レース1頭で6番人気以下から選ぶ(self) -> None:
        # 4番人気（人気薄ではない）が最も高くても対象外。6番人気以下の最大 q を取る
        odds = [4.0, 4.5, 5.0, 8.0, 9.0, 10.0, 11.0, 14.0, 20.0, 30.0]
        v = judge_gekisou(_race([0.4, 0.35, 0.1, 0.6, 0.3, 0.3, 0.2, 0.4, 0.1, 0.1], odds))
        assert v.status == STATUS_GEKISOU
        assert v.pick == 8


class Test判定しない:
    def test_7頭以下は判定しない(self) -> None:
        # 複勝が2着までになり「3枠」の前提が崩れる
        n = GEKISOU_MIN_FIELD - 1
        v = judge_gekisou(_race([0.3] * n, ODDS_10[:n]))
        assert v == NO_VERDICT

    def test_オッズの無い馬は出走馬に数えない(self) -> None:
        # 10頭中3頭が取消（オッズ無し）→ 7頭扱いで判定しない
        odds: list[float | None] = [*ODDS_10[:7], None, None, None]
        assert judge_gekisou(_race([0.3] * 10, odds)) == NO_VERDICT

    def test_指数の欠けた出走馬がいれば判定しない(self) -> None:
        """欠けたまま正規化すると他馬の q が膨らみ、空き枠も押しのけも歪む。"""
        probs: list[float | None] = [0.4, 0.35, 0.1, 0.3, 0.3, 0.2, 0.45, 0.2, 0.1, None]
        assert judge_gekisou(_race(probs)) == NO_VERDICT


class Test人気は発走前オッズの順:
    def test_馬番ではなくオッズで人気を決める(self) -> None:
        # 馬番10が1番人気、馬番1が最低人気になるよう逆順に並べる
        odds = list(reversed([4.0, 4.5, 5.0, 8.0, 9.0, 10.0, 11.0, 14.0, 20.0, 30.0]))
        probs = list(reversed([0.4, 0.35, 0.1, 0.3, 0.3, 0.2, 0.45, 0.2, 0.1, 0.1]))
        v = judge_gekisou(_race(probs, odds))
        assert v.status == STATUS_GEKISOU
        assert v.pick == 4  # 7番人気 = 馬番4
        assert v.pick_pop == 7
