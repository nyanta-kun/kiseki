"""注目馬の表示元（前向き記録 / 再計算）の選択ロジックのユニットテスト。

背景: 2026-08-26 に「画面の遡り表示」と「その日に実際に出ていた推奨」が
食い違うことが分かった。再計算はオッズを引き直すので、終わったレースでは
発走 0〜1 分前の値を使い、記録（発走 5〜6 分前で凍結）と別物になる。

実例（船橋）: 記録は 3R・4R に計 4 頭。再計算では 12R の 1 頭のみ返り、
その 12R も 12 分後にはシェアが 0.628 → 0.661 へ動いて条件から外れた。
"""
from __future__ import annotations

from src.api.chihou_races_router import resolve_place_picks


class TestResolvePlacePicks:
    def test_記録が無ければ再計算の結果を使う(self) -> None:
        assert resolve_place_picks(None, [3, 7]) == [3, 7]

    def test_記録があればそちらを使い再計算は捨てる(self) -> None:
        assert resolve_place_picks([1, 12], [7]) == [1, 12]

    def test_記録が見送りなら再計算で拾い直さない(self) -> None:
        """🔴 これが本丸。`[]`（見送りの記録）を `None`（記録なし）と同一視すると、
        その日に出していなかった馬が後から画面に現れる。"""
        assert resolve_place_picks([], [7]) == []

    def test_記録なしかつ再計算も該当なしなら空(self) -> None:
        assert resolve_place_picks(None, []) == []

    def test_記録の順序を保つ(self) -> None:
        """pick_order（指数の良い順）をそのまま表示に流す。"""
        assert resolve_place_picks([12, 2], [2, 12]) == [12, 2]

    def test_再計算側を書き換えない(self) -> None:
        recomputed = [7]
        resolve_place_picks([1], recomputed)
        assert recomputed == [7]
