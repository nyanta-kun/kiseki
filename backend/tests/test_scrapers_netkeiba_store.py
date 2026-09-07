"""`sekito.netkeiba` への書き込み規則を固定する。

なぜ必要か（2026-09-06）:
    1 行に**複数のジョブが別々の列を書く**。素直に全列 UPSERT すると、
    タイム指数を取り直しただけでパドックの列が NULL で潰れる。

    さらに深刻なのが 2026-09-06 に見つけた障害: `netkeiba-paddock` が
    文字コードを間違えたまま `horse_name` を上書きし、朝に正しく入った馬名を
    壊していた（[[paddock-mojibake]]・本番で 192/192 行が化けた）。
    **壊れた値を書かせない**ことをここで固定する。
"""

from __future__ import annotations

from datetime import date

import pytest

from src.scrapers.netkeiba.store import REPLACEMENT_CHAR, TARGET_COLUMNS, upsert


class _FakeSession:
    def __init__(self):
        self.executed: list[tuple[str, list[dict]]] = []
        self.committed = False

    def execute(self, stmt, params=None):
        self.executed.append((str(stmt), params))
        return None

    def commit(self):
        self.committed = True


def _run(target, records):
    s = _FakeSession()
    n = upsert(s, target, date(2026, 9, 6), "JHSN", 1, records)
    return s, n


def test_取得対象ごとに書く列が決まっている():
    """他のジョブが書いた列を巻き添えにしない。"""
    assert "p_rank" not in TARGET_COLUMNS["time_index"]
    assert "idx_max" not in TARGET_COLUMNS["paddock"]
    assert "training" not in TARGET_COLUMNS["time_index"]


def test_対象外の列は無視される():
    s, n = _run("time_index", [{"horse_no": 1, "idx_max": "81", "p_rank": "A"}])
    sql, params = s.executed[0]
    assert n == 1
    assert "idx_max" in sql
    assert "p_rank" not in sql, "パドックの列を巻き添えにしている"


def test_壊れた値は書かない():
    """🔴 2026-09-06 の障害の再発防止。

    置換文字 U+FFFD を含む値は復号に失敗している。それで既存の正しい値を
    上書きすると、朝に正しく入った馬名が夕方に壊れる（実際に起きた）。
    """
    s, n = _run("time_index", [{
        "horse_no": 1,
        "horse_name": f"ルース{REPLACEMENT_CHAR}ソラール",
        "idx_max": "81",
    }])
    sql, _ = s.executed[0]
    assert n == 1
    assert "horse_name" not in sql, "壊れた馬名を書こうとしている"
    assert "idx_max" in sql, "壊れていない列まで落としている"


def test_正しい値は書かれる():
    s, _ = _run("time_index", [{"horse_no": 1, "horse_name": "ルースソラール"}])
    sql, params = s.executed[0]
    assert "horse_name" in sql
    assert params[0]["horse_name"] == "ルースソラール"


def test_馬番が無い行は捨てる():
    """主キーが揃わない行は INSERT できない。"""
    s, n = _run("time_index", [{"horse_no": None, "idx_max": "81"}])
    assert n == 0
    assert s.executed == []


def test_未知の取得対象は例外にする():
    with pytest.raises(ValueError):
        _run("unknown_target", [{"horse_no": 1}])


def test_Noneの列は書かない():
    """🔴 NULL を書かない。

    理由は 2 つ。(1) `is_*` は NOT NULL（既定 false）なので、明示的な NULL は
    制約違反で落ちる（2026-09-07 に埋め戻しの初回実行で踏んだ）。
    (2) この表は複数のジョブが別々の列を書くので、持っていない値を NULL で
    上書きしてはいけない。
    """
    s, n = _run("time_index", [{"horse_no": 1, "idx_max": "81", "idx_ave": None}])
    sql, params = s.executed[0]
    assert n == 1
    assert "idx_max" in sql
    assert "idx_ave" not in sql, "None を NULL として書こうとしている"
    assert "idx_ave" not in params[0]


def test_埋め戻しは取得済みフラグを立てる():
    """立て忘れると NOT NULL の `is_*` に NULL を書こうとして落ちる。"""
    from src.scrapers.netkeiba.backfill import _IS_FLAG

    assert _IS_FLAG == {
        "blood": "is_blood", "training": "is_training", "paddock": "is_paddock",
    }
    for target, flag in _IS_FLAG.items():
        assert flag in TARGET_COLUMNS[target], f"{target} の列に {flag} が無い"
