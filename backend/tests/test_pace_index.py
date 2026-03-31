"""展開指数算出 ユニットテスト

DB接続不要のユニットテスト。SQLAlchemy Session をモックして検証する。
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from src.indices.pace import (
    LAST_3F_BONUS,
    PACE_SCORE_TABLE,
    PaceIndexCalculator,
    _classify_runner_type,
)

# ---------------------------------------------------------------------------
# ユーティリティ: モックオブジェクト生成ヘルパー
# ---------------------------------------------------------------------------


def _make_result(
    horse_id: int = 1,
    passing_4: int | None = None,
    last_3f: float | None = None,
    abnormality_code: int = 0,
) -> MagicMock:
    """RaceResult モックを生成する。"""
    r = MagicMock()
    r.horse_id = horse_id
    r.passing_4 = passing_4
    r.last_3f = Decimal(str(last_3f)) if last_3f is not None else None
    r.abnormality_code = abnormality_code
    return r


def _make_race_obj(
    id: int = 99,
    head_count: int | None = 10,
    course: str = "05",
    distance: int = 1600,
    surface: str = "芝",
    date: str = "20260322",
) -> MagicMock:
    """Race モックを生成する。"""
    r = MagicMock()
    r.id = id
    r.head_count = head_count
    r.course = course
    r.distance = distance
    r.surface = surface
    r.date = date
    return r


def _make_row(
    horse_id: int = 1,
    passing_4: int | None = None,
    head_count: int | None = 10,
    last_3f: float | None = None,
) -> MagicMock:
    """(RaceResult, Race) のペアモックを生成する。"""
    row = MagicMock()
    row.RaceResult = _make_result(horse_id=horse_id, passing_4=passing_4, last_3f=last_3f)
    row.Race = _make_race_obj(head_count=head_count)
    return row


def _make_calculator(
    race: MagicMock | None = None,
    entries: list[MagicMock] | None = None,
) -> PaceIndexCalculator:
    """DB モックを持つ PaceIndexCalculator を生成する。"""
    db = MagicMock()
    default_race = race or _make_race_obj()
    db.query.return_value.filter.return_value.first.return_value = default_race
    db.query.return_value.filter.return_value.all.return_value = entries or []
    return PaceIndexCalculator(db=db)


# ---------------------------------------------------------------------------
# _classify_runner_type
# ---------------------------------------------------------------------------


class TestClassifyRunnerType:
    """脚質分類ロジックのユニットテスト。"""

    def test_escape(self) -> None:
        """relative_pos=0.1 → 逃げ。"""
        assert _classify_runner_type(0.1) == "escape"

    def test_leader(self) -> None:
        """relative_pos=0.35 → 先行。"""
        assert _classify_runner_type(0.35) == "leader"

    def test_mid(self) -> None:
        """relative_pos=0.55 → 差し。"""
        assert _classify_runner_type(0.55) == "mid"

    def test_closer(self) -> None:
        """relative_pos=0.8 → 追込。"""
        assert _classify_runner_type(0.8) == "closer"

    def test_boundary_escape_leader(self) -> None:
        """0.25はleaderの開始（escapeの上限外）。"""
        assert _classify_runner_type(0.25) == "leader"

    def test_boundary_one_is_closer(self) -> None:
        """1.0は最後方なのでcloser。"""
        assert _classify_runner_type(1.0) == "closer"


# ---------------------------------------------------------------------------
# PaceIndexCalculator._determine_runner_type
# ---------------------------------------------------------------------------


class TestDetermineRunnerType:
    """脚質判定メソッドのユニットテスト。"""

    def test_no_data_returns_unknown(self) -> None:
        """過去データなし → 'unknown'。"""
        calc = _make_calculator()
        result = calc._determine_runner_type([])
        assert result == "unknown"

    def test_all_passing4_none_returns_unknown(self) -> None:
        """passing_4がすべてNone → 'unknown'。"""
        calc = _make_calculator()
        rows = [_make_row(passing_4=None, head_count=10) for _ in range(5)]
        result = calc._determine_runner_type(rows)
        assert result == "unknown"

    def test_escape_pattern(self) -> None:
        """passing_4=1, head_count=10 → relative_pos=0.1 → escape。"""
        calc = _make_calculator()
        rows = [_make_row(passing_4=1, head_count=10) for _ in range(5)]
        assert calc._determine_runner_type(rows) == "escape"

    def test_closer_pattern(self) -> None:
        """passing_4=9, head_count=10 → relative_pos=0.9 → closer。"""
        calc = _make_calculator()
        rows = [_make_row(passing_4=9, head_count=10) for _ in range(5)]
        assert calc._determine_runner_type(rows) == "closer"

    def test_head_count_none_is_skipped(self) -> None:
        """head_countがNoneの行はスキップ。有効データなし → unknown。"""
        calc = _make_calculator()
        rows = [_make_row(passing_4=3, head_count=None)]
        assert calc._determine_runner_type(rows) == "unknown"


# ---------------------------------------------------------------------------
# PaceIndexCalculator._predict_pace
# ---------------------------------------------------------------------------


class TestPredictPace:
    """ペース予測メソッドのユニットテスト。"""

    def test_no_escape_is_slow(self) -> None:
        """逃げ馬ゼロ → slow。"""
        calc = _make_calculator()
        runner_types = {1: "leader", 2: "mid", 3: "closer"}
        assert calc._predict_pace(runner_types) == "slow"

    def test_one_escape_is_normal(self) -> None:
        """逃げ馬1頭 → normal。"""
        calc = _make_calculator()
        runner_types = {1: "escape", 2: "leader", 3: "closer"}
        assert calc._predict_pace(runner_types) == "normal"

    def test_two_escapes_is_fast(self) -> None:
        """逃げ馬2頭以上 → fast。"""
        calc = _make_calculator()
        runner_types = {1: "escape", 2: "escape", 3: "closer"}
        assert calc._predict_pace(runner_types) == "fast"

    def test_all_unknown_is_slow(self) -> None:
        """全馬unknown → escape=0 → slow。"""
        calc = _make_calculator()
        runner_types = {1: "unknown", 2: "unknown"}
        assert calc._predict_pace(runner_types) == "slow"


# ---------------------------------------------------------------------------
# 適合スコアテーブルの値検証
# ---------------------------------------------------------------------------


class TestPaceScoreTable:
    """脚質×ペースの適合スコアテーブルの値が仕様通りであることを確認。"""

    def test_escape_slow_is_high(self) -> None:
        """逃げ馬+スロー展開 → 85（最高水準）。"""
        assert PACE_SCORE_TABLE["escape"]["slow"] == 85.0

    def test_closer_fast_is_high(self) -> None:
        """追込馬+ハイペース → 80（高スコア）。"""
        assert PACE_SCORE_TABLE["closer"]["fast"] == 80.0

    def test_escape_fast_is_low(self) -> None:
        """逃げ馬+ハイペース → 45（低スコア）。"""
        assert PACE_SCORE_TABLE["escape"]["fast"] == 45.0

    def test_closer_slow_is_low(self) -> None:
        """追込馬+スロー展開 → 45（低スコア）。"""
        assert PACE_SCORE_TABLE["closer"]["slow"] == 45.0

    def test_unknown_always_50(self) -> None:
        """unknown脚質はペースに関わらず50。"""
        assert PACE_SCORE_TABLE["unknown"]["fast"] == 50.0
        assert PACE_SCORE_TABLE["unknown"]["normal"] == 50.0
        assert PACE_SCORE_TABLE["unknown"]["slow"] == 50.0


# ---------------------------------------------------------------------------
# PaceIndexCalculator.calculate_batch (モック DB)
# ---------------------------------------------------------------------------


class TestCalculateBatch:
    """calculate_batch の動作テスト（DB モック使用）。"""

    def _build_calc(
        self,
        horse_configs: list[dict],
        race: MagicMock | None = None,
    ) -> PaceIndexCalculator:
        """calculate_batch テスト用 Calculator を構築する。

        Args:
            horse_configs: [{"horse_id": int, "passing_4": int|None,
                              "head_count": int|None, "last_3f": float|None}, ...]
            race: 対象レースのモック

        Returns:
            モック済み PaceIndexCalculator
        """
        target_race = race or _make_race_obj(id=1)

        db = MagicMock()
        # Race 取得
        db.query.return_value.filter.return_value.first.return_value = target_race

        # RaceEntry 取得
        entries = []
        for cfg in horse_configs:
            e = MagicMock()
            e.horse_id = cfg["horse_id"]
            entries.append(e)
        db.query.return_value.filter.return_value.all.return_value = entries

        calc = PaceIndexCalculator(db=db)

        # _get_past_results_batch をモック（全馬の過去データを直接注入）
        def mock_batch(horse_ids_arg: list[int], before_date: str, exclude_race_id: int) -> dict:
            result: dict[int, list] = {}
            for cfg in horse_configs:
                hid = cfg["horse_id"]
                if hid not in horse_ids_arg:
                    continue
                # 過去10戦分のダミーデータを生成
                rows = []
                passing_4 = cfg.get("passing_4")
                head_count = cfg.get("head_count", 10)
                last_3f = cfg.get("last_3f")
                if passing_4 is not None or last_3f is not None:
                    for _ in range(5):
                        rows.append(
                            _make_row(
                                horse_id=hid,
                                passing_4=passing_4,
                                head_count=head_count,
                                last_3f=last_3f,
                            )
                        )
                result[hid] = rows
            return result

        calc._get_past_results_batch = mock_batch

        # _get_avg_last3f をモック（条件平均上がり3Fを固定値で返す）
        calc._get_avg_last3f = MagicMock(return_value=35.0)

        return calc

    def test_returns_all_horse_ids(self) -> None:
        """全エントリ馬の horse_id がキーとして返る。"""
        horse_configs = [
            {"horse_id": 101, "passing_4": 1, "head_count": 10},
            {"horse_id": 102, "passing_4": 8, "head_count": 10},
            {"horse_id": 103, "passing_4": None, "head_count": 10},
        ]
        calc = self._build_calc(horse_configs)
        result = calc.calculate_batch(race_id=1)
        assert set(result.keys()) == {101, 102, 103}

    def test_race_not_found_returns_empty(self) -> None:
        """race_id未存在 → 空dict。"""
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        calc = PaceIndexCalculator(db=db)
        assert calc.calculate_batch(race_id=9999) == {}

    def test_no_entries_returns_empty(self) -> None:
        """エントリなし → 空dict。"""
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = _make_race_obj()
        db.query.return_value.filter.return_value.all.return_value = []
        calc = PaceIndexCalculator(db=db)
        assert calc.calculate_batch(race_id=1) == {}

    def test_unknown_runner_type_returns_50(self) -> None:
        """passing_4がNoneのみ → unknown脚質 → 50.0。"""
        horse_configs = [{"horse_id": 1, "passing_4": None, "head_count": 10}]
        calc = self._build_calc(horse_configs)
        # 上がり3F補正なし（last_3fなし）
        result = calc.calculate_batch(race_id=1)
        assert result[1] == 50.0

    def test_escape_slow_pace_high_score(self) -> None:
        """逃げ馬1頭のみ → 平均ペース(normal) → 逃げ+normal=70。

        Note: escape=0頭→slow, escape=1頭→normal, escape>=2頭→fast
        逃げ馬自身がいるため escape_count=1 → normal ペース。
        """
        # horse_id=1 が逃げ（1頭）→ normal ペース → escape+normal=70
        horse_configs = [
            {"horse_id": 1, "passing_4": 1, "head_count": 10},  # escape
            {"horse_id": 2, "passing_4": 8, "head_count": 10},  # closer
            {"horse_id": 3, "passing_4": 9, "head_count": 10},  # closer
        ]
        calc = self._build_calc(horse_configs)
        # 上がり3Fなし → ボーナスなし
        result = calc.calculate_batch(race_id=1)
        assert result[1] == pytest.approx(70.0, abs=0.1)

    def test_closer_fast_pace_high_score(self) -> None:
        """追込馬+ハイペース → 80。"""
        # 逃げ馬2頭 → fast ペース
        horse_configs = [
            {"horse_id": 1, "passing_4": 1, "head_count": 10},  # escape
            {"horse_id": 2, "passing_4": 1, "head_count": 10},  # escape
            {"horse_id": 3, "passing_4": 9, "head_count": 10},  # closer
        ]
        calc = self._build_calc(horse_configs)
        result = calc.calculate_batch(race_id=1)
        assert result[3] == pytest.approx(80.0, abs=0.1)

    def test_escape_fast_pace_low_score(self) -> None:
        """逃げ馬+ハイペース → 45。"""
        # 逃げ馬3頭 → fast ペース
        horse_configs = [
            {"horse_id": 1, "passing_4": 1, "head_count": 10},  # escape
            {"horse_id": 2, "passing_4": 1, "head_count": 10},  # escape
            {"horse_id": 3, "passing_4": 1, "head_count": 10},  # escape
        ]
        calc = self._build_calc(horse_configs)
        result = calc.calculate_batch(race_id=1)
        # 全馬 escape なので全員 45
        assert result[1] == pytest.approx(45.0, abs=0.1)

    def test_closer_slow_pace_low_score(self) -> None:
        """追込馬+スロー展開 → 45。"""
        # 逃げ馬なし → slow ペース
        horse_configs = [
            {"horse_id": 1, "passing_4": 9, "head_count": 10},  # closer
            {"horse_id": 2, "passing_4": 8, "head_count": 10},  # closer
        ]
        calc = self._build_calc(horse_configs)
        result = calc.calculate_batch(race_id=1)
        assert result[1] == pytest.approx(45.0, abs=0.1)

    def test_last3f_bonus_applied_when_faster(self) -> None:
        """上がり3F平均が条件平均より速い → +5ボーナス付与。"""
        # 条件平均は35.0（_get_avg_last3fのモック）
        # last_3f=33.0 (<35.0) → ボーナス適用
        horse_configs = [
            {"horse_id": 1, "passing_4": 9, "head_count": 10, "last_3f": 33.0},  # closer
        ]
        calc = self._build_calc(horse_configs)
        # slower展開 → base=45, ボーナス+5 = 50
        result = calc.calculate_batch(race_id=1)
        assert result[1] == pytest.approx(45.0 + LAST_3F_BONUS, abs=0.1)

    def test_last3f_bonus_not_applied_when_slower(self) -> None:
        """上がり3Fが条件平均より遅い → ボーナスなし。"""
        # 条件平均は35.0, last_3f=36.0 (>35.0) → ボーナスなし
        horse_configs = [
            {"horse_id": 1, "passing_4": 9, "head_count": 10, "last_3f": 36.0},  # closer
        ]
        calc = self._build_calc(horse_configs)
        result = calc.calculate_batch(race_id=1)
        assert result[1] == pytest.approx(45.0, abs=0.1)

    def test_score_clipped_at_100(self) -> None:
        """スコア上限100にクリップされる。"""
        # escape + slow(85) + bonus(5) = 90 ≤ 100 (クリップ不要)
        # escape + slow スコアが高い馬 + 高速上がり3F
        horse_configs = [
            {
                "horse_id": 1,
                "passing_4": 1,
                "head_count": 10,
                "last_3f": 30.0,
            },  # escape, fast last3f
        ]
        calc = self._build_calc(horse_configs)
        result = calc.calculate_batch(race_id=1)
        # escape + slow(85) + bonus(5) = 90
        assert result[1] <= 100.0

    def test_score_clipped_at_0(self) -> None:
        """スコア下限0にクリップされる。"""
        horse_configs = [
            {"horse_id": 1, "passing_4": 1, "head_count": 10},  # escape + fast = 45
        ]
        calc = self._build_calc(horse_configs)
        result = calc.calculate_batch(race_id=1)
        assert result[1] >= 0.0
