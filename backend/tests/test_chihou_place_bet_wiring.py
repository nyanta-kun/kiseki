"""複穴（place_bet）カテゴリの配線と、個別馬バッジの版ガードを固定する。

## なぜ必要か（2026-09-01 の実測）

**① 推奨カテゴリ `place_bet` が別ルールを配信していた**

`/api/chihou/recommendations/sweet-spot` が返す `category="place_bet"` は
`chihou_is_place_pick`（注目馬 = 6番人気以下 × 指数5位内 × 開いたレース）で
作られていた。一方でカテゴリ名・画面のタイトル・注記はいずれも
`chihou_is_place_bet`（断然人気R × 単勝10倍以上 × 指数3位内）を説明しており、
さらに注目馬は同じページの `ChihouFeaturedPlacePanel` が既に配信していた。
つまり「別ルールの札を掛けた注目馬の二重配信」だった。

前向き記録（`chihou.place_picks`・採点済 572R・2026-08-14〜09-01）で
同一母集団に載せて比べた実測:

    注目馬      105R(18.4%) 152点  複勝的中 22.37% CI95[16.47, 29.63]
                同条件の母集団 11.91% → リフト ×1.88   複勝ROI 0.868 [0.557, 1.233]
    断然人気穴  176R(30.8%) 241点  複勝的中 36.93% CI95[31.09, 43.18]
                同条件の母集団 17.78% → リフト ×2.08   複勝ROI 0.773 [0.619, 0.951]

的中率・リフト・カバー率のいずれも断然人気穴が上。ROI は両者とも 1.0 に
届かず、CI が大きく重なるので差は判別できない。よって `place_bet` は
断然人気穴を配信する。

**② 個別馬バッジが v11 以降ずっと点いていなかった**

`chihou_races_router` の `is_sweet_spot` / `is_place_bet` 付与が
`if CHIHOU_COMPOSITE_VERSION == 10:` で囲まれたまま v14 まで来ており、
ブロックごと不到達だった。エラーもログも出ない。

**③ 頭数は effective を使う**

`races.head_count` はレース後にしか入らない。生の値を渡すと
`chihou_is_place_bet` が発走前は必ず False を返し、
「バッジが発走後だけ点く」不整合になる。
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from src.api import chihou_races_router
from src.indices.buy_signal import (
    CHIHOU_PLACE_MIN_HEAD_COUNT,
    chihou_effective_head_count,
    chihou_is_place_bet,
    chihou_is_place_pick,
)
from src.services import chihou_recommender

RECOMMENDER_SRC = Path(inspect.getfile(chihou_recommender)).read_text(encoding="utf-8")
ROUTER_SRC = Path(inspect.getfile(chihou_races_router)).read_text(encoding="utf-8")


def _called_names(source: str) -> set[str]:
    """ソース中で実際に「呼び出されている」名前を集める（文字列やコメントは拾わない）。"""
    out: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name):
                out.add(f.id)
            elif isinstance(f, ast.Attribute):
                out.add(f.attr)
    return out


class Test推奨カテゴリの配線:
    def test_place_bet_は断然人気穴の関数で作る(self) -> None:
        assert "chihou_is_place_bet" in _called_names(RECOMMENDER_SRC)

    def test_注目馬の関数は推奨カテゴリでは呼ばない(self) -> None:
        """注目馬は `/featured-place`（ChihouFeaturedPlacePanel）の担当。

        ここで呼ぶと同じページに同じ推奨が2度出る（過去の実害そのもの）。
        """
        called = _called_names(RECOMMENDER_SRC)
        assert "chihou_is_place_pick" not in called
        assert "chihou_select_place_picks" not in called

    def test_廃棄済みの数値を画面に出さない(self) -> None:
        """51.5% は指数側の look-ahead による過大評価と判明済み。

        それを「過大評価だ」と書いたコメントのすぐ下で、同じ数値が
        ユーザー向け reason に埋め込まれ DB にも永続化されていた。

        ⚠️ 検査対象は **実行される文字列リテラル**だけに絞る。経緯を残す
           コメントまで禁止すると、なぜ禁止したのかを書けなくなる。
        """
        literals: list[str] = []
        for node in ast.walk(ast.parse(RECOMMENDER_SRC)):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                literals.append(node.value)
        offenders = [t for t in literals if "51.5" in t]
        assert not offenders, f"廃棄済みの数値が文字列に残っています: {offenders}"

    def test_信頼度は前向き記録の実測値(self) -> None:
        v = chihou_recommender.CHIHOU_PLACE_BET_MEASURED_HIT_RATE
        # 実測 36.93% CI95[31.09, 43.18]。CI の外へ出たら測り直しの合図。
        assert 0.31 <= v <= 0.44, f"実測 CI から外れています: {v}"


class Test個別馬バッジの版ガード:
    def test_指数バージョンで分岐しない(self) -> None:
        """判定は composite_index の**レース内順位**しか使わず版に依存しない。

        `CHIHOU_COMPOSITE_VERSION == 10` の一時ガードが v11〜v14 の4回の昇格で
        外し忘れられ、バッジが丸ごと死んでいた。
        """
        for node in ast.walk(ast.parse(ROUTER_SRC)):
            if not isinstance(node, ast.Compare):
                continue
            left = node.left
            if isinstance(left, ast.Name) and left.id == "CHIHOU_COMPOSITE_VERSION":
                raise AssertionError(
                    "CHIHOU_COMPOSITE_VERSION での分岐が復活しています。"
                    " バッジ判定は指数の版に依存しません。"
                )

    def test_バッジ判定は実際に呼ばれている(self) -> None:
        called = _called_names(ROUTER_SRC)
        assert "chihou_is_sweet_spot" in called
        assert "chihou_is_place_bet" in called

    def test_頭数はeffectiveを使う(self) -> None:
        assert "chihou_effective_head_count" in _called_names(ROUTER_SRC)


class Test頭数の扱い:
    """発走前でも判定が成立することを、本番関数の組み合わせで確認する。"""

    def _fire(self, head_count: int | None, registered: int | None) -> bool:
        return chihou_is_place_bet(
            index_rank=2,
            win_odds=12.0,
            fav_odds=1.6,
            head_count=chihou_effective_head_count(head_count, registered),
        )

    def test_発走前は登録頭数で判定できる(self) -> None:
        """head_count はレース後にしか入らない。ここが None でも点く必要がある。"""
        assert self._fire(head_count=None, registered=10) is True

    def test_確定頭数があればそちらを優先(self) -> None:
        assert self._fire(head_count=10, registered=12) is True

    def test_生のNoneを渡すと落ちることを記録しておく(self) -> None:
        """effective を通さないと発走前は必ず False になる、という罠の再現。"""
        assert (
            chihou_is_place_bet(
                index_rank=2, win_odds=12.0, fav_odds=1.6, head_count=None
            )
            is False
        )

    def test_7頭以下は複勝が2着までなので対象外(self) -> None:
        assert self._fire(head_count=None, registered=CHIHOU_PLACE_MIN_HEAD_COUNT - 1) is False


class Test二つのルールは別物:
    """同じ馬で両者の判定が食い違うことを示す（片方をもう片方の代用にできない）。"""

    def test_断然人気穴だけが拾う馬(self) -> None:
        # 指数2位・単勝12倍・1番人気1.6倍の断然人気レース。
        # 人気順は3番人気なので注目馬（6番人気以下）の条件は満たさない。
        assert chihou_is_place_bet(2, 12.0, 1.6, 10) is True
        assert chihou_is_place_pick(pop_rank=3, index_rank=2, top3_share=0.55, head_count=10) is False

    def test_注目馬だけが拾う馬(self) -> None:
        # 8番人気・指数4位・開いたレース（上位3頭シェア 0.55）。
        # 1番人気が 3.0 倍なので断然人気レースではない。
        assert chihou_is_place_pick(pop_rank=8, index_rank=4, top3_share=0.55, head_count=10) is True
        assert chihou_is_place_bet(4, 25.0, 3.0, 10) is False
