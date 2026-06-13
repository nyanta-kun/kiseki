"""エキゾチックオッズパーサー ユニットテスト (T01)

JVDF v4.9 仕様書 (2024-08-07版) に準拠した固定長サンプルデータで
O2〜O6 のパースと OddsImporter の展開ロジックを DB 接続なしで検証する。

DataSpec と対応レコード種別 ID (確認済み):
  0B31 → O1: 単勝・複勝・枠連 (962バイト)
  0B32 → O2: 馬連 (2042バイト, 153組 × 13byte)
  0B33 → O3: ワイド (2654バイト, 153組 × 17byte)
  0B34 → O4: 馬単 (4031バイト, 306組 × 13byte)
  0B35 → O5: 三連複 (12293バイト, 816組 × 15byte)
  0B36 → O6: 三連単 (83285バイト, 4896組 × 17byte)
"""

from __future__ import annotations

from datetime import datetime

import pytest

from src.importers.jvlink_parser import parse_odds
from src.importers.odds_importer import (
    EXOTIC_HEADER_SIZE,
    OddsImporter,
    _parse_exotic_odds_value,
    _parse_horse_combo,
    _parse_odds_value,
)

# ---------------------------------------------------------------------------
# テスト用サンプルデータ生成ユーティリティ
# ---------------------------------------------------------------------------

# O2〜O6 の共通ヘッダー (51バイト):
# pos 1-2:   レコード種別ID
# pos 3:     データ区分 "2" (前日売最終)
# pos 4-27:  共通ヘッダー (開催年 + 月日 + 場コード等 24バイト)
#   pos 4-11:  データ作成年月日 "20260614"
#   pos 12-15: 開催年 "2026"
#   pos 16-19: 開催月日 "0614"
#   pos 20-21: 競馬場コード "05" (東京)
#   pos 22-23: 開催回 "01"
#   pos 24-25: 開催日目 "01"
#   pos 26-27: レース番号 "11"
# pos 28-35: 発表月日時分 "06141030"
# pos 36-37: 登録頭数 "18"
# pos 38-39: 出走頭数 "18"
# pos 40:    発売フラグ "1"
# pos 41-51: 予備 (11バイト空白)
# → jravan_race_id = "2026061405010111"

# ヘッダー本体 (レコード種別IDを除く 49バイト)
_HEADER_BODY = (
    "2"           # 1バイト: データ区分
    "20260614"    # 8バイト: データ作成年月日
    "2026"        # 4バイト: 開催年
    "0614"        # 4バイト: 開催月日
    "05"          # 2バイト: 競馬場コード (東京)
    "01"          # 2バイト: 開催回
    "01"          # 2バイト: 開催日目
    "11"          # 2バイト: レース番号
    "06141030"    # 8バイト: 発表月日時分
    "18"          # 2バイト: 登録頭数
    "18"          # 2バイト: 出走頭数
    "1"           # 1バイト: 発売フラグ
    "           "  # 11バイト: 予備
)  # 合計 1+8+4+4+2+2+2+2+8+2+2+1+11 = 49バイト

assert len(_HEADER_BODY) == 49, f"_HEADER_BODY サイズ不正: {len(_HEADER_BODY)}"


def _make_header(rec_id: str) -> str:
    """レコード種別IDを含む51バイトヘッダーを返す。"""
    assert len(rec_id) == 2
    return rec_id + _HEADER_BODY


EXPECTED_JRAVAN_ID = "2026061405010111"


def _make_o2_record(combos: list[tuple[int, int, float]]) -> str:
    """O2 馬連レコードを構築する。

    Args:
        combos: [(horse1, horse2, odds), ...] 人気順で並べること

    Returns:
        O2 固定長レコード文字列 (CRLF 末尾あり)
    """
    header = _make_header("O2")
    assert len(header) == 51

    # 各エントリ: 組番(4byte: 馬番2+馬番2) + オッズ(6byte: ÷10表記) + 人気順(3byte)
    # 最大 153 組 × 13byte = 1989byte
    entries = ""
    for i, (h1, h2, odds_val) in enumerate(combos):
        combo = f"{h1:02d}{h2:02d}"          # 4バイト
        odds_str = f"{int(odds_val * 10):06d}"  # 6バイト (×10整数)
        popularity = f"{i + 1:03d}"          # 3バイト
        entries += combo + odds_str + popularity

    # 残りを空白でパディング (153組まで)
    max_entries = 153
    entry_size = 13
    padding_count = max_entries - len(combos)
    padding = " " * (padding_count * entry_size)

    data = header + entries + padding + "\r\n"
    assert len(data) == 2042, f"O2 レコードサイズ不正: {len(data)} != 2042"
    return data


def _make_o3_record(combos: list[tuple[int, int, float, float]]) -> str:
    """O3 ワイドレコードを構築する。

    Args:
        combos: [(horse1, horse2, low_odds, high_odds), ...] 人気順

    Returns:
        O3 固定長レコード文字列 (CRLF 末尾あり)
    """
    header = _make_header("O3")

    # 各エントリ: 組番(4) + 最低オッズ(5) + 最高オッズ(5) + 人気順(3) = 17byte
    entries = ""
    for i, (h1, h2, low, high) in enumerate(combos):
        combo = f"{h1:02d}{h2:02d}"
        low_str = f"{int(low * 10):05d}"
        high_str = f"{int(high * 10):05d}"
        popularity = f"{i + 1:03d}"
        entries += combo + low_str + high_str + popularity

    max_entries = 153
    entry_size = 17
    padding_count = max_entries - len(combos)
    padding = " " * (padding_count * entry_size)

    data = header + entries + padding + "\r\n"
    assert len(data) == 2654, f"O3 レコードサイズ不正: {len(data)} != 2654"
    return data


def _make_o4_record(combos: list[tuple[int, int, float]]) -> str:
    """O4 馬単レコードを構築する。

    Args:
        combos: [(horse1, horse2, odds), ...] 人気順 (順番あり: 1着-2着)

    Returns:
        O4 固定長レコード文字列 (CRLF 末尾あり)
    """
    header = _make_header("O4")

    # 各エントリ: 組番(4) + オッズ(6) + 人気順(3) = 13byte
    entries = ""
    for i, (h1, h2, odds_val) in enumerate(combos):
        combo = f"{h1:02d}{h2:02d}"
        odds_str = f"{int(odds_val * 10):06d}"
        popularity = f"{i + 1:03d}"
        entries += combo + odds_str + popularity

    max_entries = 306
    entry_size = 13
    padding_count = max_entries - len(combos)
    padding = " " * (padding_count * entry_size)

    data = header + entries + padding + "\r\n"
    assert len(data) == 4031, f"O4 レコードサイズ不正: {len(data)} != 4031"
    return data


def _make_o5_record(combos: list[tuple[int, int, int, float]]) -> str:
    """O5 三連複レコードを構築する。

    Args:
        combos: [(h1, h2, h3, odds), ...] 人気順

    Returns:
        O5 固定長レコード文字列 (CRLF 末尾あり)
    """
    header = _make_header("O5")

    # 各エントリ: 組番(6: 馬番2×3) + オッズ(6) + 人気順(3) = 15byte
    entries = ""
    for i, (h1, h2, h3, odds_val) in enumerate(combos):
        combo = f"{h1:02d}{h2:02d}{h3:02d}"
        odds_str = f"{int(odds_val * 10):06d}"
        popularity = f"{i + 1:03d}"
        entries += combo + odds_str + popularity

    max_entries = 816
    entry_size = 15
    padding_count = max_entries - len(combos)
    padding = " " * (padding_count * entry_size)

    data = header + entries + padding + "\r\n"
    assert len(data) == 12293, f"O5 レコードサイズ不正: {len(data)} != 12293"
    return data


def _make_o6_record(combos: list[tuple[int, int, int, float]]) -> str:
    """O6 三連単レコードを構築する。

    Args:
        combos: [(h1, h2, h3, odds), ...] 人気順 (着順あり: 1-2-3着)

    Returns:
        O6 固定長レコード文字列 (CRLF 末尾あり)
    """
    header = _make_header("O6")

    # 各エントリ: 組番(6) + オッズ(7) + 人気順(4) = 17byte
    entries = ""
    for i, (h1, h2, h3, odds_val) in enumerate(combos):
        combo = f"{h1:02d}{h2:02d}{h3:02d}"
        odds_str = f"{int(odds_val * 10):07d}"
        popularity = f"{i + 1:04d}"
        entries += combo + odds_str + popularity

    max_entries = 4896
    entry_size = 17
    padding_count = max_entries - len(combos)
    padding = " " * (padding_count * entry_size)

    data = header + entries + padding + "\r\n"
    assert len(data) == 83285, f"O6 レコードサイズ不正: {len(data)} != 83285"
    return data


# ---------------------------------------------------------------------------
# _parse_odds_value テスト (既存機能の回帰テスト)
# ---------------------------------------------------------------------------


class TestParseOddsValue:
    """既存 O1 オッズ値変換テスト（回帰確認）。"""

    def test_normal_value(self) -> None:
        """"0022" → 2.2倍"""
        assert _parse_odds_value("0022") == pytest.approx(2.2)

    def test_zero_returns_none(self) -> None:
        """"0000"（無投票）は None"""
        assert _parse_odds_value("0000") is None

    def test_non_digit_returns_none(self) -> None:
        """"----"（発売前取消）は None"""
        assert _parse_odds_value("----") is None

    def test_9999_returns_max(self) -> None:
        """"9999" は 999.9"""
        assert _parse_odds_value("9999") == pytest.approx(999.9)


# ---------------------------------------------------------------------------
# _parse_exotic_odds_value テスト
# ---------------------------------------------------------------------------


class TestParseExoticOddsValue:
    """エキゾチックオッズ値変換テスト (6〜7桁形式)。"""

    def test_6digit_normal(self) -> None:
        """"000022" → 2.2倍 (O2〜O5 形式)"""
        assert _parse_exotic_odds_value("000022") == pytest.approx(2.2)

    def test_6digit_high(self) -> None:
        """"012345" → 1234.5倍"""
        assert _parse_exotic_odds_value("012345") == pytest.approx(1234.5)

    def test_7digit_normal(self) -> None:
        """"0000022" → 2.2倍 (O6 三連単形式)"""
        assert _parse_exotic_odds_value("0000022") == pytest.approx(2.2)

    def test_zero_returns_none(self) -> None:
        """"000000" は None（無投票）"""
        assert _parse_exotic_odds_value("000000") is None

    def test_non_digit_returns_none(self) -> None:
        """"------" は None（発売前取消）"""
        assert _parse_exotic_odds_value("------") is None

    def test_spaces_returns_none(self) -> None:
        """空白文字列は None"""
        assert _parse_exotic_odds_value("      ") is None


# ---------------------------------------------------------------------------
# _parse_horse_combo テスト
# ---------------------------------------------------------------------------


class TestParseHorseCombo:
    """馬番組合せフィールド変換テスト。"""

    def test_2horse_pair(self) -> None:
        """"0103" → "1-3" (馬連形式)"""
        assert _parse_horse_combo("0103", 2) == "1-3"

    def test_2horse_double_digit(self) -> None:
        """"1218" → "12-18" (高馬番)"""
        assert _parse_horse_combo("1218", 2) == "12-18"

    def test_3horse_trio(self) -> None:
        """"010305" → "1-3-5" (三連複形式)"""
        assert _parse_horse_combo("010305", 3) == "1-3-5"

    def test_3horse_high_numbers(self) -> None:
        """"101518" → "10-15-18" (大馬番)"""
        assert _parse_horse_combo("101518", 3) == "10-15-18"

    def test_invalid_zero_returns_none(self) -> None:
        """"0000" は None（馬番0は無効）"""
        assert _parse_horse_combo("0000", 2) is None

    def test_too_short_returns_none(self) -> None:
        """フィールド短すぎる場合は None"""
        assert _parse_horse_combo("01", 2) is None

    def test_non_digit_returns_none(self) -> None:
        """"XX03" は None"""
        assert _parse_horse_combo("XX03", 2) is None


# ---------------------------------------------------------------------------
# parse_odds テスト（jvlink_parser.py の JVDF 仕様対応確認）
# ---------------------------------------------------------------------------


class TestParseOddsRecordId:
    """JVDF v4.9 仕様の DataSpec↔レコード対応を検証する。

    旧コードのバグ修正確認:
    旧: O4=馬連, O5=ワイド, O6=馬単, O7=三連複, O8=三連単
    正: O2=馬連, O3=ワイド, O4=馬単, O5=三連複, O6=三連単
    """

    def test_o2_maps_to_quinella(self) -> None:
        """O2 レコード → bet_type = quinella (馬連)"""
        data = _make_o2_record([(1, 3, 5.5)])
        result = parse_odds(data)
        assert result is not None
        assert result["rec_id"] == "O2"
        assert result["bet_type"] == "quinella"

    def test_o3_maps_to_quinella_place(self) -> None:
        """O3 レコード → bet_type = quinella_place (ワイド)"""
        data = _make_o3_record([(1, 3, 2.5, 4.0)])
        result = parse_odds(data)
        assert result is not None
        assert result["rec_id"] == "O3"
        assert result["bet_type"] == "quinella_place"

    def test_o4_maps_to_exacta(self) -> None:
        """O4 レコード → bet_type = exacta (馬単)"""
        data = _make_o4_record([(1, 3, 8.0)])
        result = parse_odds(data)
        assert result is not None
        assert result["rec_id"] == "O4"
        assert result["bet_type"] == "exacta"

    def test_o5_maps_to_trio(self) -> None:
        """O5 レコード → bet_type = trio (三連複)"""
        data = _make_o5_record([(1, 3, 5, 50.0)])
        result = parse_odds(data)
        assert result is not None
        assert result["rec_id"] == "O5"
        assert result["bet_type"] == "trio"

    def test_o6_maps_to_trifecta(self) -> None:
        """O6 レコード → bet_type = trifecta (三連単)"""
        data = _make_o6_record([(1, 3, 5, 120.0)])
        result = parse_odds(data)
        assert result is not None
        assert result["rec_id"] == "O6"
        assert result["bet_type"] == "trifecta"

    def test_race_id_parsed_correctly(self) -> None:
        """O2 から jravan_race_id が正しく抽出される。"""
        data = _make_o2_record([(1, 3, 5.5)])
        result = parse_odds(data)
        assert result is not None
        assert result["jravan_race_id"] == EXPECTED_JRAVAN_ID

    def test_o7_not_recognized(self) -> None:
        """O7 はJVDF仕様上存在しないため None を返す。"""
        # O7 ヘッダーを持つ不正レコード
        fake_o7 = "O7" + " " * 100
        result = parse_odds(fake_o7)
        assert result is None


# ---------------------------------------------------------------------------
# OddsImporter._extract_pair_odds / _extract_wide_odds テスト
#   DB接続なし: _extract_odds_rows を直接呼び出す
# ---------------------------------------------------------------------------


class TestExtractExoticOdds:
    """OddsImporter の各券種オッズ展開テスト。"""

    def setup_method(self) -> None:
        """テスト用 fetched_at と importer インスタンスを初期化する。"""
        from unittest.mock import AsyncMock
        self.db = AsyncMock()
        self.importer = OddsImporter(db=self.db)
        self.fetched_at = datetime(2026, 6, 14, 10, 30, 0)
        self.race_id = 42

    def test_o2_quinella_extraction(self) -> None:
        """O2 馬連: 組番と人気順1位のオッズが正しく展開される。"""
        data = _make_o2_record([
            (1, 3, 5.5),   # 1番人気: 1-3, 5.5倍
            (2, 4, 8.0),   # 2番人気: 2-4, 8.0倍
            (1, 5, 15.0),  # 3番人気: 1-5, 15.0倍
        ])
        rows = self.importer._extract_odds_rows("O2", data, "quinella", self.race_id, self.fetched_at)

        assert len(rows) == 3
        # 1番人気の組合せ確認
        row0 = rows[0]
        assert row0["combination"] == "1-3"
        assert row0["odds"] == pytest.approx(5.5)
        assert row0["bet_type"] == "quinella"
        assert row0["race_id"] == self.race_id

    def test_o3_wide_extraction(self) -> None:
        """O3 ワイド: 最低オッズが格納される。"""
        data = _make_o3_record([
            (1, 3, 2.5, 4.0),   # 1-3: 最低2.5〜最高4.0
            (1, 5, 3.0, 5.5),   # 1-5: 最低3.0〜最高5.5
        ])
        rows = self.importer._extract_odds_rows("O3", data, "quinella_place", self.race_id, self.fetched_at)

        assert len(rows) == 2
        assert rows[0]["combination"] == "1-3"
        assert rows[0]["odds"] == pytest.approx(2.5)  # 最低オッズ
        assert rows[0]["bet_type"] == "quinella_place"

    def test_o4_exacta_extraction(self) -> None:
        """O4 馬単: 着順込みの組番が展開される。"""
        data = _make_o4_record([
            (1, 3, 8.0),   # 1着1-2着3: 8.0倍
            (3, 1, 9.5),   # 1着3-2着1: 9.5倍
        ])
        rows = self.importer._extract_odds_rows("O4", data, "exacta", self.race_id, self.fetched_at)

        assert len(rows) == 2
        assert rows[0]["combination"] == "1-3"
        assert rows[1]["combination"] == "3-1"

    def test_o5_trio_extraction(self) -> None:
        """O5 三連複: 3頭組番が "-" 区切りで展開される。"""
        data = _make_o5_record([
            (1, 3, 5, 50.0),    # 1-3-5: 50倍
            (2, 4, 6, 80.0),    # 2-4-6: 80倍
        ])
        rows = self.importer._extract_odds_rows("O5", data, "trio", self.race_id, self.fetched_at)

        assert len(rows) == 2
        assert rows[0]["combination"] == "1-3-5"
        assert rows[0]["odds"] == pytest.approx(50.0)
        assert rows[1]["combination"] == "2-4-6"

    def test_o6_trifecta_extraction(self) -> None:
        """O6 三連単: 7桁オッズ形式で正しくパースされる。"""
        data = _make_o6_record([
            (1, 3, 5, 120.0),   # 1-3-5: 120倍
            (1, 5, 3, 180.0),   # 1-5-3: 180倍
            (3, 1, 5, 250.0),   # 3-1-5: 250倍
        ])
        rows = self.importer._extract_odds_rows("O6", data, "trifecta", self.race_id, self.fetched_at)

        assert len(rows) == 3
        assert rows[0]["combination"] == "1-3-5"
        assert rows[0]["odds"] == pytest.approx(120.0)
        assert rows[1]["combination"] == "1-5-3"
        assert rows[2]["combination"] == "3-1-5"

    def test_o6_trifecta_max_combos_limit(self) -> None:
        """O6 三連単: TRIFECTA_MAX_COMBOS 上限が適用される。"""
        from src.importers.odds_importer import TRIFECTA_MAX_COMBOS

        if TRIFECTA_MAX_COMBOS is None:
            pytest.skip("TRIFECTA_MAX_COMBOS=None は全組格納のためスキップ")

        # TRIFECTA_MAX_COMBOS + 10 件のデータを生成（最大組数内に収める）
        n = min(TRIFECTA_MAX_COMBOS + 10, 4896)
        # 組番を一意に生成: (horse=i%18+1 パターン)
        combos = []
        for i in range(n):
            h1 = (i % 16) + 1
            h2 = (i % 15) + 2
            h3 = (i % 14) + 3
            if h1 == h2 or h2 == h3 or h1 == h3:
                h3 = 18
            combos.append((h1, h2, h3, float(i + 20)))
        data = _make_o6_record(combos)

        rows = self.importer._extract_odds_rows("O6", data, "trifecta", self.race_id, self.fetched_at)

        assert len(rows) <= TRIFECTA_MAX_COMBOS

    def test_zero_odds_skipped(self) -> None:
        """オッズ 0（無投票）のエントリはスキップされる。"""
        # 組番は有効だがオッズは 0（無投票相当）
        header = _make_header("O2")
        # 0倍のエントリ: 組番 "0103" + オッズ "000000" + 人気 "001"
        combo_entry = "0103" + "000000" + "001"
        padding = " " * ((153 - 1) * 13)
        data = header + combo_entry + padding + "\r\n"
        assert len(data) == 2042

        rows = self.importer._extract_odds_rows("O2", data, "quinella", self.race_id, self.fetched_at)
        assert len(rows) == 0

    def test_o2_header_size_correctness(self) -> None:
        """O2-O6 共通ヘッダーが 51 バイトであることを確認する。

        検証: total - data - CRLF = header
          O2: 2042 - (153×13) - 2 = 51
          O3: 2654 - (153×17) - 2 = 51
          O4: 4031 - (306×13) - 2 = 51
          O5: 12293 - (816×15) - 2 = 51
          O6: 83285 - (4896×17) - 2 = 51
        """
        assert 2042 - 153 * 13 - 2 == EXOTIC_HEADER_SIZE
        assert 2654 - 153 * 17 - 2 == EXOTIC_HEADER_SIZE
        assert 4031 - 306 * 13 - 2 == EXOTIC_HEADER_SIZE
        assert 12293 - 816 * 15 - 2 == EXOTIC_HEADER_SIZE
        assert 83285 - 4896 * 17 - 2 == EXOTIC_HEADER_SIZE


# ---------------------------------------------------------------------------
# データ量見積もりテスト（設計上限の確認）
# ---------------------------------------------------------------------------


class TestDataVolumeEstimate:
    """1日当たりのDB書き込み行数が設計上限 1000万行以内に収まることを確認する。"""

    def test_full_polling_exceeds_limit(self) -> None:
        """全36レース × 全組数 × 30秒間隔 → 三連単は上限超過する（設計確認）。"""
        polling_per_day = 24 * 60 * 60 // 30  # 2880回/日
        trifecta_all = 4896 * 36 * polling_per_day
        # 全組格納すると1日約50億行: 上限の500倍超で設計上 NG
        assert trifecta_all > 10_000_000

    def test_window_filtered_within_limit(self) -> None:
        """発走前30分以内・上位N人気絞りで1日1000万行以内に収まることを確認する。

        想定:
          - 同時対象レース数: 最大6レース (30分内)
          - 三連単格納上限: TRIFECTA_MAX_COMBOS = 200 組
          - ポーリング回数: 30分 / 30秒 = 60回/レース
        """
        from src.importers.odds_importer import TRIFECTA_MAX_COMBOS

        max_trifecta_per_combo_snapshot = TRIFECTA_MAX_COMBOS or 4896
        polls_per_race = 30 * 60 // 30  # 60回
        races_per_day = 36
        # 現実的には 200×60×36 = 432,000行/日 (三連単のみ)
        conservative_estimate = max_trifecta_per_combo_snapshot * polls_per_race * races_per_day
        assert conservative_estimate <= 10_000_000, (
            f"三連単の1日推定行数 {conservative_estimate:,} が 1000万を超えています。"
            "TRIFECTA_MAX_COMBOS を更に下げることを検討してください。"
        )
