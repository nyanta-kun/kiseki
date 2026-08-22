"""券種名（bet_type）が全モジュールで一致していることを検査する。

`keiba.race_payouts` / `keiba.odds_history` / `keiba.latest_odds` は
`bet_type` で join される。表記が割れても SQL は例外を出さず 0 件を返すだけなので、
テストで縛らないと気付けない。

実際 2026-08-23 まで、同じ `jvlink_parser.py` の中で

    HR（払戻）→ race_payouts.bet_type = 'wide'
    O3（オッズ）→ odds_history.bet_type = 'quinella_place'

と別名を書いており、確定オッズの検証でワイドだけ結果が出なかった。
枠連も `allocation.py` だけ 'frame'、DB は 'bracket' だった。
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path
from typing import get_args

import pytest

from src.bet_types import (
    BET_TYPE_JA,
    BET_TYPES,
    LEGACY_BET_TYPE_ALIASES,
    BetType,
    canonical_bet_type,
)
from src.betting import allocation, backtest, odds_model
from src.importers import jvlink_parser

_SRC = Path(jvlink_parser.__file__).resolve().parents[1]


class TestCanonicalVocabulary:
    """src/bet_types.py の中身そのもの。"""

    def test_eight_bet_types(self) -> None:
        assert BET_TYPES == {
            "win",
            "place",
            "bracket",
            "quinella",
            "wide",
            "exacta",
            "trio",
            "trifecta",
        }

    def test_wide_is_wide_not_quinella_place(self) -> None:
        """ワイドは 'wide'。JRA 公式英語名の 'quinella_place' は使わない。"""
        assert "wide" in BET_TYPES
        assert "quinella_place" not in BET_TYPES

    def test_bracket_is_bracket_not_frame(self) -> None:
        """枠連は 'bracket'。'frame' は使わない。"""
        assert "bracket" in BET_TYPES
        assert "frame" not in BET_TYPES

    def test_literal_matches_frozenset(self) -> None:
        assert set(get_args(BetType)) == BET_TYPES

    def test_japanese_labels_cover_all(self) -> None:
        assert set(BET_TYPE_JA) == BET_TYPES

    @pytest.mark.parametrize(
        ("legacy", "canonical"),
        [("quinella_place", "wide"), ("frame", "bracket")],
    )
    def test_legacy_alias_maps_to_canonical(self, legacy: str, canonical: str) -> None:
        assert canonical_bet_type(legacy) == canonical
        assert canonical in BET_TYPES

    def test_aliases_never_shadow_canonical_names(self) -> None:
        assert not (set(LEGACY_BET_TYPE_ALIASES) & BET_TYPES)

    def test_unknown_name_passes_through(self) -> None:
        assert canonical_bet_type("trifecta") == "trifecta"


class TestWritersAgree:
    """DB に bet_type を書く側が同じ語彙を使っているか。"""

    def test_payout_writer_uses_canonical_names(self) -> None:
        """HR レコード（race_payouts）が書く bet_type がすべて正準表記か。

        `parse_hr` は各券種を `bet_type="..."` リテラルで渡しているので
        ソースから拾う。8 券種すべてが揃っていることも同時に確認する。
        """
        src = inspect.getsource(jvlink_parser.parse_hr)
        written = set(re.findall(r'bet_type="([a-z_]+)"', src))
        assert written == BET_TYPES, f"race_payouts 側の券種名がずれている: {written}"

    def test_odds_writer_uses_canonical_names(self) -> None:
        """O1-O6 レコード（odds_history）が書く bet_type がすべて正準表記か。"""
        written = set(jvlink_parser.ODDS_RECORD_BET_TYPES.values())
        assert written <= BET_TYPES, f"odds_history 側の券種名がずれている: {written}"

    def test_o3_maps_to_wide(self) -> None:
        """回帰: O3（ワイド）は race_payouts と同じ 'wide' を書く。"""
        assert jvlink_parser.ODDS_RECORD_BET_TYPES["O3"] == "wide"

    def test_payouts_and_odds_share_the_join_key(self) -> None:
        """join に使う券種が両側に存在するか（ワイドが 0 件になった件の回帰）。"""
        payout_src = inspect.getsource(jvlink_parser.parse_hr)
        payout_types = set(re.findall(r'bet_type="([a-z_]+)"', payout_src))
        odds_types = set(jvlink_parser.ODDS_RECORD_BET_TYPES.values())
        # O1 は win/place/枠連 が混在し、複勝は odds_importer が別途展開する
        odds_types |= {"place"}
        missing = odds_types - payout_types
        assert not missing, f"odds 側にしかない券種名: {missing}"


class TestConsumersAgree:
    """bet_type を読む側（betting モジュール）が同じ語彙を使っているか。"""

    def test_backtest_bet_types_match(self) -> None:
        assert backtest.BET_TYPES == BET_TYPES

    def test_allocation_bet_type_literal_matches(self) -> None:
        assert set(get_args(allocation.BetType)) == BET_TYPES

    def test_allocation_max_tickets_covers_all(self) -> None:
        assert set(allocation.MAX_TICKETS_PER_TYPE) == BET_TYPES

    def test_odds_model_takeout_rate_covers_all(self) -> None:
        assert set(odds_model.TAKEOUT_RATE) == BET_TYPES

    def test_normalize_combination_accepts_every_bet_type(self) -> None:
        """全券種が normalize_combination を通ること（未対応で落ちないこと）。"""
        horses = {
            "win": [3],
            "place": [3],
            "bracket": [7, 8],
            "quinella": [3, 7],
            "wide": [3, 7],
            "exacta": [3, 7],
            "trio": [3, 7, 11],
            "trifecta": [3, 7, 11],
        }
        for bt in sorted(BET_TYPES):
            assert backtest.normalize_combination(bt, horses[bt])


class TestNoLegacyNamesLeftInSource:
    """旧表記がソースに残っていないか（DB 移行スクリプトと docs は除く）。"""

    ALLOWED = {
        "bet_types.py",                        # 別名テーブルの定義そのもの
        "rename_quinella_place_to_wide.py",    # 移行スクリプト
        "purge_corrupt_exotic_odds.py",        # 改名前後の両方を掃除する
        "test_bet_type_naming.py",             # このファイル
    }

    @pytest.mark.parametrize("legacy", sorted(LEGACY_BET_TYPE_ALIASES))
    def test_legacy_name_not_used_as_bet_type(self, legacy: str) -> None:
        """券種名として旧表記を書いている行が無いか。

        'frame' のように別の意味でも使う語があるため、同じ行に 'bet_type' が
        出てくる箇所だけを対象にする（composite.py の枠順特徴 "frame" 等は無関係）。
        """
        pattern = re.compile(rf'["\']{re.escape(legacy)}["\']')
        offenders = []
        for path in _SRC.rglob("*.py"):
            if path.name in self.ALLOWED:
                continue
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if pattern.search(line) and "bet_type" in line.lower():
                    offenders.append(f"{path.relative_to(_SRC)}:{lineno}")
        assert not offenders, f"旧表記 {legacy!r} が券種名として残っています: {offenders}"
