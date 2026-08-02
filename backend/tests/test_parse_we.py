"""WEレコード（天候馬場状態）パーサのユニットテスト

テストデータは 2026-08-02 に実機の `windows-agent/probe_track_condition.py` で
`JVRTOpen("0B14", "20260802")` から採取した本物のレコード。
同日の `keiba.races` 確定値（札幌 良/晴・新潟 良/曇・中京 良/晴）と突き合わせ済み。
DB 接続不要。
"""

from __future__ import annotations

from src.importers.jvlink_parser import parse_we

# --- 実機採取データ（末尾の \r\n は JVRead が返す形のまま） ---
# 札幌: 前日の初期状態（曇・稍重）→ 当日 06:55 に天候 曇→晴 → 同 06:55 に馬場 稍重→良
WE_SAPPORO_INITIAL = "WE12026080120260802010104000000001222000\r\n"
WE_SAPPORO_WEATHER = "WE12026080220260802010104080206552100200\r\n"
WE_SAPPORO_TRACK = "WE12026080220260802010104080206553011022\r\n"
# 新潟: 初期状態（曇・芝良・ダ稍重）→ 06:55 に馬場変更（芝良・ダ良）
WE_NIIGATA_INITIAL = "WE12026080120260802040204000000001212000\r\n"
WE_NIIGATA_TRACK = "WE12026080220260802040204080206553011012\r\n"
# 中京: 初期状態のみ（晴・芝良・ダ良）
WE_CHUKYO_INITIAL = "WE12026080120260802070204000000001111000\r\n"


class TestParseWe:
    """フィールド位置の同定結果を固定する。"""

    def test_returns_none_for_non_we(self) -> None:
        assert parse_we("RA6202608022026080201010401") is None

    def test_returns_none_for_short_record(self) -> None:
        assert parse_we("WE1202608") is None

    def test_initial_state(self) -> None:
        """初期状態（変更識別=1・発表時刻なし）"""
        p = parse_we(WE_SAPPORO_INITIAL)
        assert p is not None
        assert p["date"] == "20260802"
        assert p["course"] == "01"       # 札幌
        assert p["announced_at"] is None  # "00000000" は None 扱い
        assert p["change_type"] == "1"
        assert p["weather"] == "曇"
        assert p["turf_condition"] == "稍"
        assert p["dirt_condition"] == "稍"

    def test_weather_change(self) -> None:
        """天候変更（変更識別=2）。馬場側はコード0で「変更なし」→ None"""
        p = parse_we(WE_SAPPORO_WEATHER)
        assert p is not None
        assert p["announced_at"] == "08020655"  # 8/2 06:55
        assert p["change_type"] == "2"
        assert p["weather"] == "晴"
        assert p["turf_condition"] is None
        assert p["dirt_condition"] is None

    def test_track_change(self) -> None:
        """馬場状態変更（変更識別=3）。天候側はコード0で「変更なし」→ None"""
        p = parse_we(WE_SAPPORO_TRACK)
        assert p is not None
        assert p["announced_at"] == "08020655"
        assert p["change_type"] == "3"
        assert p["weather"] is None
        assert p["turf_condition"] == "良"
        assert p["dirt_condition"] == "良"

    def test_course_codes(self) -> None:
        assert parse_we(WE_NIIGATA_INITIAL)["course"] == "04"  # 新潟
        assert parse_we(WE_CHUKYO_INITIAL)["course"] == "07"   # 中京

    def test_surface_specific_conditions(self) -> None:
        """芝とダートで別々の馬場状態を持つ（新潟の初期状態は 芝良/ダ稍）"""
        p = parse_we(WE_NIIGATA_INITIAL)
        assert p is not None
        assert p["turf_condition"] == "良"
        assert p["dirt_condition"] == "稍"


class TestFoldToFinalState:
    """発表時刻順に非 None のみ上書き＝API 側の畳み込みロジックと同じ手順。

    実機の確定値（札幌 晴/良・新潟 曇/良・中京 晴/良）を再現できることを保証する。
    """

    @staticmethod
    def _fold(records: list[str]) -> dict[str, str | None]:
        parsed = [p for p in (parse_we(r) for r in records) if p is not None]
        state: dict[str, str | None] = {"weather": None, "turf": None, "dirt": None}
        for p in sorted(parsed, key=lambda x: (x["announced_at"] or "")):
            if p["weather"]:
                state["weather"] = p["weather"]
            if p["turf_condition"]:
                state["turf"] = p["turf_condition"]
            if p["dirt_condition"]:
                state["dirt"] = p["dirt_condition"]
        return state

    def test_sapporo_final(self) -> None:
        st = self._fold([WE_SAPPORO_INITIAL, WE_SAPPORO_WEATHER, WE_SAPPORO_TRACK])
        assert st == {"weather": "晴", "turf": "良", "dirt": "良"}

    def test_niigata_final(self) -> None:
        st = self._fold([WE_NIIGATA_INITIAL, WE_NIIGATA_TRACK])
        assert st == {"weather": "曇", "turf": "良", "dirt": "良"}

    def test_chukyo_final(self) -> None:
        st = self._fold([WE_CHUKYO_INITIAL])
        assert st == {"weather": "晴", "turf": "良", "dirt": "良"}

    def test_order_independence(self) -> None:
        """レコードの到着順が入れ替わっても発表時刻順に畳み込むので結果は同じ"""
        a = self._fold([WE_SAPPORO_INITIAL, WE_SAPPORO_WEATHER, WE_SAPPORO_TRACK])
        b = self._fold([WE_SAPPORO_TRACK, WE_SAPPORO_INITIAL, WE_SAPPORO_WEATHER])
        assert a == b
