"""races.race_class_label プロパティと parse_ra の競走条件コード抽出のテスト

競走条件コード（コード表2007）を優先して 新馬(メイクデビュー)/未勝利/各勝クラス
を区別し、コード未取得の旧データでは賞金推定にフォールバックすることを検証する。
"""

from __future__ import annotations

from src.db.models import Race
from src.importers.jvlink_parser import parse_ra


def _label(
    *,
    race_type_code: str | None = "11",
    race_condition_code: str | None = None,
    prize_1st: int | None = None,
    grade: str | None = None,
) -> str | None:
    r = Race(
        race_type_code=race_type_code,
        race_condition_code=race_condition_code,
        prize_1st=prize_1st,
        grade=grade,
    )
    return r.race_class_label


class TestRaceClassLabelByConditionCode:
    """競走条件コード（コード表2007）優先のラベル算出。"""

    def test_shinba_returns_make_debut(self) -> None:
        """701（新馬）は年齢に依らず 'メイクデビュー'。"""
        assert _label(race_type_code="11", race_condition_code="701") == "メイクデビュー"

    def test_maiden_with_age(self) -> None:
        """703（未勝利）は年齢を冠する。"""
        assert _label(race_type_code="11", race_condition_code="703") == "2歳未勝利"
        assert _label(race_type_code="12", race_condition_code="703") == "3歳未勝利"

    def test_win_classes(self) -> None:
        assert _label(race_type_code="13", race_condition_code="005") == "3歳以上1勝クラス"
        assert _label(race_type_code="13", race_condition_code="010") == "3歳以上2勝クラス"
        assert _label(race_type_code="14", race_condition_code="016") == "4歳以上3勝クラス"

    def test_open_no_age_prefix(self) -> None:
        assert _label(race_type_code="13", race_condition_code="999") == "オープン"

    def test_grade_takes_precedence(self) -> None:
        """grade があれば常に None（条件戦ラベルは出さない）。"""
        assert _label(race_condition_code="701", grade="G1") is None

    def test_condition_code_beats_prize(self) -> None:
        """賞金が 1勝クラス相当でも、条件コード 703 があれば未勝利。"""
        assert _label(race_type_code="11", race_condition_code="703", prize_1st=70000) == "2歳未勝利"


class TestRaceClassLabelFallback:
    """条件コード未取得（旧データ）の賞金推定フォールバック。"""

    def test_2yo_low_prize_maiden(self) -> None:
        assert _label(race_type_code="11", race_condition_code=None, prize_1st=50000) == "2歳未勝利"

    def test_2yo_high_prize_1win(self) -> None:
        """従来挙動を維持（コード無しは賞金しきい値で推定）。"""
        assert _label(race_type_code="11", race_condition_code=None, prize_1st=70000) == "2歳1勝クラス"

    def test_no_prize_age_only(self) -> None:
        assert _label(race_type_code="11", race_condition_code=None, prize_1st=None) == "2歳"


def _place(buf: list[str], pos: int, value: str) -> None:
    """1-indexed バイト位置に value を配置する。"""
    i = pos - 1
    buf[i : i + len(value)] = list(value)


def _build_ra(*, cond_2sai: str = "701", race_type: str = "11", prize: int = 70000) -> str:
    """有効な RA レコード（1272バイト）を最小限のフィールドで組み立てる。"""
    buf = [" "] * 1272
    _place(buf, 1, "RA")
    _place(buf, 3, "2")  # データ区分: 出馬表
    _place(buf, 4, "20260606")  # データ作成年月日
    _place(buf, 12, "2026")  # 開催年
    _place(buf, 16, "0606")  # 開催月日
    _place(buf, 20, "05")  # 競馬場（東京）
    _place(buf, 22, "03")  # 開催回
    _place(buf, 24, "01")  # 開催日目
    _place(buf, 26, "05")  # レース番号
    _place(buf, 617, race_type)  # 競走種別コード
    _place(buf, 623, cond_2sai)  # 競走条件コード 2歳条件
    _place(buf, 698, "1600")  # 距離
    _place(buf, 706, "10")  # トラックコード（芝）
    _place(buf, 714, f"{prize:08d}")  # 1着本賞金
    _place(buf, 884, "16")  # 出走頭数
    return "".join(buf)


class TestParseRaConditionCode:
    """parse_ra が競走条件コードを正しい位置（623-637）から抽出する。"""

    def test_extracts_shinba_code(self) -> None:
        rec = parse_ra(_build_ra(cond_2sai="701"))
        assert rec is not None
        assert rec["race_condition_code"] == "701"
        assert rec["race_type_code"] == "11"

    def test_extracts_maiden_code(self) -> None:
        rec = parse_ra(_build_ra(cond_2sai="703"))
        assert rec is not None
        assert rec["race_condition_code"] == "703"

    def test_empty_condition_is_none(self) -> None:
        """全スロット未設定（000/空白）なら None。"""
        rec = parse_ra(_build_ra(cond_2sai="000"))
        assert rec is not None
        assert rec["race_condition_code"] is None
