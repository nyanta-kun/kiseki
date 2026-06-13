"""ticket_builder.py のユニットテスト。

DoD 要件:
- フォーメーション展開が JRA 公式の点数公式と一致
  例: 三連単フォーメーション 2×3×4 の点数
- 買い目の重複なし・昇順ソート・着順固定の確認
"""

from __future__ import annotations

from src.betting.ticket_builder import (
    build_exacta_axis,
    build_exacta_box,
    build_frame_box,
    build_place,
    build_quinella_axis,
    build_quinella_box,
    build_trifecta_axis,
    build_trifecta_box,
    build_trio_axis,
    build_trio_box,
    build_trio_formation,
    build_wide_box,
    build_win,
    count_formation_points,
    top_n_formation,
)

# ---------------------------------------------------------------------------
# 単勝 / 複勝
# ---------------------------------------------------------------------------


class TestWinPlace:
    def test_win_basic(self) -> None:
        result = build_win([1, 3, 5])
        assert result == ["01", "03", "05"]

    def test_place_basic(self) -> None:
        result = build_place([2, 4])
        assert result == ["02", "04"]

    def test_win_single_horse(self) -> None:
        result = build_win([7])
        assert result == ["07"]


# ---------------------------------------------------------------------------
# 馬連 BOX
# ---------------------------------------------------------------------------


class TestQuinellaBox:
    def test_two_horse_box(self) -> None:
        """2頭 BOX = C(2,2) = 1 点。"""
        result = build_quinella_box([1, 2])
        assert result == ["01-02"]

    def test_three_horse_box(self) -> None:
        """3頭 BOX = C(3,2) = 3 点。"""
        result = build_quinella_box([1, 2, 3])
        assert len(result) == 3
        assert "01-02" in result
        assert "01-03" in result
        assert "02-03" in result

    def test_four_horse_box(self) -> None:
        """4頭 BOX = C(4,2) = 6 点。"""
        result = build_quinella_box([1, 2, 3, 4])
        assert len(result) == 6

    def test_sorted_order(self) -> None:
        """馬番は昇順に並ぶ（逆順指定でも同じ）。"""
        result = build_quinella_box([3, 1])
        assert result == ["01-03"]

    def test_no_duplicates(self) -> None:
        """重複が含まれても重複なしで返る。"""
        result = build_quinella_box([1, 1, 2])
        assert result == ["01-02"]


# ---------------------------------------------------------------------------
# 馬連 軸流し
# ---------------------------------------------------------------------------


class TestQuinellaAxis:
    def test_one_axis_two_partners(self) -> None:
        """軸1頭×相手2頭 = 2点。"""
        result = build_quinella_axis([1], [2, 3])
        assert len(result) == 2
        assert "01-02" in result
        assert "01-03" in result

    def test_axis_not_paired_with_itself(self) -> None:
        """軸馬は自分自身と組まない。"""
        result = build_quinella_axis([1], [1, 2, 3])
        # 01-01 は存在しない
        assert all("-" in c for c in result)
        pairs = [c.split("-") for c in result]
        for a, b in pairs:
            assert a != b

    def test_two_axis_formation(self) -> None:
        """軸2頭流しで軸間の組合せも含まれる。"""
        result = build_quinella_axis([1, 2], [3, 4])
        # 01-02 / 01-03 / 01-04 / 02-03 / 02-04 の 5 点
        assert "01-02" in result  # 軸間
        assert "01-03" in result
        assert "02-04" in result


# ---------------------------------------------------------------------------
# ワイド（組合せ形式は馬連と同じ）
# ---------------------------------------------------------------------------


class TestWideBox:
    def test_wide_box_same_as_quinella(self) -> None:
        q = build_quinella_box([1, 2, 3])
        w = build_wide_box([1, 2, 3])
        assert q == w


# ---------------------------------------------------------------------------
# 馬単 BOX
# ---------------------------------------------------------------------------


class TestExactaBox:
    def test_two_horse_box(self) -> None:
        """2頭 BOX = P(2,2) = 2 点。"""
        result = build_exacta_box([1, 2])
        assert len(result) == 2
        assert "01-02" in result
        assert "02-01" in result

    def test_three_horse_box(self) -> None:
        """3頭 BOX = P(3,2) = 6 点。"""
        result = build_exacta_box([1, 2, 3])
        assert len(result) == 6

    def test_order_matters(self) -> None:
        """着順が固定されている（01-02 と 02-01 は別々）。"""
        result = build_exacta_box([1, 2])
        assert "01-02" in result
        assert "02-01" in result


# ---------------------------------------------------------------------------
# 馬単 軸流し
# ---------------------------------------------------------------------------


class TestExactaAxis:
    def test_basic_axis(self) -> None:
        """1着軸1頭×2着相手2頭 = 2点。"""
        result = build_exacta_axis([1], [2, 3])
        assert "01-02" in result
        assert "01-03" in result
        assert len(result) == 2

    def test_same_horse_excluded(self) -> None:
        """同一馬は除外（01-01 は存在しない）。"""
        result = build_exacta_axis([1], [1, 2])
        assert "01-01" not in result
        assert "01-02" in result

    def test_no_duplicates(self) -> None:
        """重複なし。"""
        result = build_exacta_axis([1, 1], [2])
        assert len(set(result)) == len(result)


# ---------------------------------------------------------------------------
# 三連複 BOX
# ---------------------------------------------------------------------------


class TestTrioBox:
    def test_three_horse_box(self) -> None:
        """3頭 BOX = C(3,3) = 1 点。"""
        result = build_trio_box([1, 2, 3])
        assert result == ["01-02-03"]

    def test_four_horse_box(self) -> None:
        """4頭 BOX = C(4,3) = 4 点。"""
        result = build_trio_box([1, 2, 3, 4])
        assert len(result) == 4

    def test_six_horse_box(self) -> None:
        """6頭 BOX = C(6,3) = 20 点。"""
        result = build_trio_box(list(range(1, 7)))
        assert len(result) == 20

    def test_sorted_within_combination(self) -> None:
        """各組合せ内の馬番が昇順。"""
        result = build_trio_box([3, 1, 2])
        assert result == ["01-02-03"]


# ---------------------------------------------------------------------------
# 三連複 軸流し
# ---------------------------------------------------------------------------


class TestTrioAxis:
    def test_one_axis_four_partners(self) -> None:
        """軸1頭×相手4頭 = C(4,2) = 6 点。"""
        result = build_trio_axis([1], [2, 3, 4, 5])
        assert len(result) == 6

    def test_axis_included_in_all(self) -> None:
        """全組合せに軸馬が含まれる。"""
        result = build_trio_axis([1], [2, 3, 4])
        for combo in result:
            assert "01" in combo.split("-")


# ---------------------------------------------------------------------------
# 三連複 フォーメーション
# ---------------------------------------------------------------------------


class TestTrioFormation:
    def test_dod_example(self) -> None:
        """DoD 要件: col1=[1,2], col2=[1,2,3], col3=[1,2,3,4]。

        JRA 公式の点数計算: 3列から各1頭を選んで全て異なる組合せ。
        手計算で数えると: 全部で以下のユニーク組合せ:
        {1,2,3} {1,2,4} {1,3,4} {2,3,4} = 4点
        ただし col1 が {1,2} で固定なので:
        - a=1: (1,b,c) b∈{1,2,3}, c∈{1,2,3,4}, b≠1,c≠1,b≠c
            b=2,c=3 → {1,2,3}
            b=2,c=4 → {1,2,4}
            b=3,c=2 → {1,2,3} (重複)
            b=3,c=4 → {1,3,4}
        - a=2: (2,b,c) b∈{1,2,3}, c∈{1,2,3,4}, b≠2,c≠2,b≠c
            b=1,c=3 → {1,2,3} (重複)
            b=1,c=4 → {1,2,4} (重複)
            b=3,c=1 → {1,2,3} (重複)
            b=3,c=4 → {2,3,4}
        ユニーク = {1,2,3} {1,2,4} {1,3,4} {2,3,4} = 4点
        """
        result = build_trio_formation([1, 2], [1, 2, 3], [1, 2, 3, 4])
        assert len(result) == 4
        assert "01-02-03" in result
        assert "01-02-04" in result
        assert "01-03-04" in result
        assert "02-03-04" in result

    def test_no_duplicates(self) -> None:
        """重複なし。"""
        result = build_trio_formation([1, 2, 3], [1, 2, 3], [1, 2, 3])
        assert len(set(result)) == len(result)

    def test_all_same_horse_excluded(self) -> None:
        """全列が同一馬だと組合せは 0 点。"""
        result = build_trio_formation([1], [1], [1])
        assert len(result) == 0


# ---------------------------------------------------------------------------
# 三連単 BOX
# ---------------------------------------------------------------------------


class TestTrifectaBox:
    def test_three_horse_box(self) -> None:
        """3頭 BOX = P(3,3) = 6 点。"""
        result = build_trifecta_box([1, 2, 3])
        assert len(result) == 6

    def test_four_horse_box(self) -> None:
        """4頭 BOX = P(4,3) = 24 点。"""
        result = build_trifecta_box([1, 2, 3, 4])
        assert len(result) == 24

    def test_order_matters(self) -> None:
        """01-02-03 と 02-01-03 は別々の組合せ。"""
        result = build_trifecta_box([1, 2, 3])
        assert "01-02-03" in result
        assert "02-01-03" in result


# ---------------------------------------------------------------------------
# 三連単 フォーメーション（JRA 公式との一致 — DoD 必須）
# ---------------------------------------------------------------------------


class TestTrifectaAxis:
    def test_dod_formation_2x3x4(self) -> None:
        """DoD 要件: 三連単フォーメーション 2×3×4 の点数確認。

        JRA 公式: 1列目2頭 × 2列目3頭 × 3列目4頭
        axis_first=[1,2], axis_second=[1,2,3], axis_third=[1,2,3,4]
        全て異なる馬番が1着-2着-3着に入る順列。

        手計算（JRA公式掲載例）:
        1着=1, 2着=2 → 3着: {3,4} = 2点
        1着=1, 2着=3 → 3着: {2,4} = 2点
        1着=2, 2着=1 → 3着: {3,4} = 2点
        1着=2, 2着=3 → 3着: {1,4} = 2点
        合計 = 8点
        """
        result = build_trifecta_axis(
            [1, 2], [1, 2, 3], [1, 2, 3, 4]
        )
        assert len(result) == 8

    def test_no_same_horse_in_combination(self) -> None:
        """同一馬が複数着に入らない。"""
        result = build_trifecta_axis([1, 2], [2, 3], [3, 4])
        for combo in result:
            parts = combo.split("-")
            assert len(set(parts)) == 3  # 全て異なる

    def test_no_duplicates(self) -> None:
        """重複なし。"""
        result = build_trifecta_axis([1, 2], [1, 2, 3], [1, 2, 3, 4])
        assert len(set(result)) == len(result)

    def test_order_preserved(self) -> None:
        """1着-2着-3着の順が保たれる。"""
        # 1着=1 のみ候補
        result = build_trifecta_axis([1], [2], [3])
        assert result == ["01-02-03"]


# ---------------------------------------------------------------------------
# 枠連
# ---------------------------------------------------------------------------


class TestFrameBox:
    def test_two_frames(self) -> None:
        """2枠 BOX = 3点（同枠含む）: 1-1, 1-2, 2-2。"""
        result = build_frame_box([1, 2])
        assert "1-1" in result
        assert "1-2" in result
        assert "2-2" in result
        assert len(result) == 3

    def test_unique_frames(self) -> None:
        """重複枠番は1つとして扱う。"""
        result = build_frame_box([1, 1, 2])
        assert len(result) == 3


# ---------------------------------------------------------------------------
# top_n_formation / count_formation_points
# ---------------------------------------------------------------------------


class TestTopNFormation:
    def test_returns_top_n(self) -> None:
        """確率上位 N 点を返す。"""
        combo_probs = {
            "01-02-03": 0.05,
            "01-02-04": 0.03,
            "01-03-04": 0.02,
            "02-03-04": 0.01,
        }
        result = top_n_formation(combo_probs, bet_type="trio", n=2)
        assert result == ["01-02-03", "01-02-04"]

    def test_returns_all_when_n_large(self) -> None:
        """N が全点数より大きい場合は全部返す。"""
        combo_probs = {"01": 0.5, "02": 0.3}
        result = top_n_formation(combo_probs, bet_type="win", n=100)
        assert len(result) == 2


class TestCountFormationPoints:
    def test_basic_count(self) -> None:
        assert count_formation_points(["01-02-03", "01-02-04"]) == 2

    def test_deduplicates(self) -> None:
        assert count_formation_points(["01-02", "01-02", "01-03"]) == 2

    def test_empty(self) -> None:
        assert count_formation_points([]) == 0
