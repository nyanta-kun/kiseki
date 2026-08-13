"""開催グレード（GP/GI/GII/GIII/FI/FII）判定の回帰テスト。

対応表は winticket の `cup.grade` を実測15開催で確定させたもの（2026-08-13〜14）。
**推測で埋めた箇所は無い**が、サンプル数はグレードごとに偏っている
（GIII 6件 / GI 2件 / GII 1件 / GP 1件 / FI 2件 / FII 5件）ので、
未知の値が来たときの振る舞いを特に縛る。
"""
from __future__ import annotations

import pytest

from src.services.keirin_cup_grade import (
    BIG_EVENT_MIN_GRADE,
    GRADE_LABELS,
    grade_label,
    is_big_event_grade,
    is_known_grade,
)


@pytest.mark.parametrize("grade,label", [
    (6, "GP"), (5, "GI"), (4, "GII"), (3, "GIII"), (2, "FI"), (1, "FII"),
])
def test_grade_labels_match_measured_mapping(grade, label):
    """実測で確定した対応（docstring 参照）。ここを変えるなら再実測すること。"""
    assert grade_label(grade) == label


@pytest.mark.parametrize("grade", [6, 5, 4, 3])
def test_giii_and_above_are_big_events(grade):
    """GIII 以上を「大会」として FI/FII と分ける（2026-08-14 ユーザー判断）。"""
    assert is_big_event_grade(grade) is True


@pytest.mark.parametrize("grade", [2, 1])
def test_fi_fii_are_not_big_events(grade):
    assert is_big_event_grade(grade) is False


def test_threshold_is_giii():
    assert BIG_EVENT_MIN_GRADE == 3
    assert GRADE_LABELS[BIG_EVENT_MIN_GRADE] == "GIII"


@pytest.mark.parametrize("grade", [None, "", "あ", 0])
def test_unknown_or_missing_is_not_a_big_event(grade):
    """🔴 欠損・未知は通常開催として扱う（安全側）。

    誤って「大会」と判定して穴埋めを大量に出すより、出さないほうが戻せる。
    """
    assert is_big_event_grade(grade) is False
    assert grade_label(grade) is None


def test_unknown_grade_above_the_known_max_is_treated_as_big():
    """🔴 既知の最大より上の未知値は**格上**とみなす。

    新しいグレード体系（GP の上など）が入ったときに、最上位の開催だけ
    黙って商品が消えるのを避ける。ラベルは付けない（知らない名前を作らない）。
    """
    assert is_big_event_grade(max(GRADE_LABELS) + 1) is True
    assert grade_label(max(GRADE_LABELS) + 1) is None


def test_is_known_grade_flags_unmapped_values():
    """対応表の見直しが要ることを呼び出し側が検知できること。"""
    assert is_known_grade(5) is True
    assert is_known_grade(99) is False
    assert is_known_grade(None) is False


def test_canonical_imports_stdlib_only():
    """🔴 keirin は自分の venv からこのファイルを直接読む。

    依存を足すと **Web は無事なまま入稿だけが落ちる**（marquee 正本と同じ制約）。
    """
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "src" / "services"
           / "keirin_cup_grade.py").read_text(encoding="utf-8")
    for line in src.splitlines():
        line = line.strip()
        if line.startswith(("import ", "from ")) and "__future__" not in line:
            pytest.fail(f"正本が標準ライブラリ以外を import しています: {line}")


def test_race_grade_column_is_not_confused_with_cup_grade():
    """🔴 `wt_races.grade` は**級班**（A級/S級/L級）で開催グレードではない。

    取り違えると「S級だから GI」のような判定になる。SQL で cup_grade を
    見ていることを縛る。
    """
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "src" / "api"
           / "keirin_router.py").read_text(encoding="utf-8")
    assert "wr.cup_grade" in src, "API が cup_grade を読んでいません"
    assert "cup_grade_label" in src
