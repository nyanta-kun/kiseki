"""看板レース判定（kiseki 側の正本）の不変条件（2026-08-10 新設）。

## 背景

2026-08-09 に「看板レースには必ず推奨を出す」方針になり、
自動入稿（keirin リポジトリ）と Web一覧の★表示で同じ判定が要るようになった。
初版はフロントに TypeScript でキーワードを写していたが、判定が3言語・
2リポジトリに散るのは事故の型なので、API が `is_marquee` を返す形へ移した。

## 守る不変条件

1. 判定は **race_type**。レース番号（最終R＝決勝）では判定しない
   — ガールズ決勝が 6R と 12R の両方に置かれる開催が実在（2026-08-09 佐世保）
2. 🔴 **「準決勝」を看板に含めない**。「決勝」を部分一致で拾うため、
   除外し忘れると全体の約14.5%が看板になり判定が意味を失う
3. フロントは自前でキーワードを持たない（API のフラグだけを見る）
4. 🔴 入稿側（`keirin/src/marquee.py`）も自前で持たない（2026-08-11 一本化）
5. 🔴 この正本は**標準ライブラリ以外を import しない**
   — 入稿側は keirin の venv（FastAPI も SQLAlchemy も無い）から
     このファイルを直接読み込むため
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.services.keirin_marquee import is_marquee_race

# 2026-08-09 に実データ（keirin.wt_races.race_type）で確認した実在の値。
MARQUEE = [
    "決勝", "ガールズ決勝", "チャレンジ決勝", "男子新人アドバンス決勝",
    "特選", "初特選", "選抜", "ガールズ選抜", "特秀",
]
NOT_MARQUEE = [
    "準決勝", "チャレンジ準決勝",
    "予選", "チャレンジ予選", "特予選",
    "一般", "ガールズ一般", "Wガル", "ガールズルーキー企画レース",
    "", None,
]


@pytest.mark.parametrize("race_type", MARQUEE)
def test_marquee_types(race_type: str) -> None:
    assert is_marquee_race(race_type), race_type


@pytest.mark.parametrize("race_type", NOT_MARQUEE)
def test_non_marquee_types(race_type: str | None) -> None:
    assert not is_marquee_race(race_type), race_type


def test_semifinal_is_excluded() -> None:
    """🔴 「準決勝」は「決勝」を部分一致で拾う。除外が外れたら落ちること。"""
    assert "決勝" in "準決勝"          # 前提の明示
    assert not is_marquee_race("準決勝")


def test_api_returns_the_flag() -> None:
    """keirin_router が `is_marquee` を返していること。"""
    src = (Path(__file__).resolve().parents[1] / "src" / "api"
           / "keirin_router.py").read_text(encoding="utf-8")
    assert '"is_marquee": is_marquee_race(' in src


def test_frontend_does_not_duplicate_the_keywords() -> None:
    """🔴 フロントがキーワードを自前で持たないこと。

    2026-08-09 の初版は TSX にキーワードを写しており、
    「★は付くのに入稿されない」（またはその逆）を作れる状態だった。
    API のフラグだけを見る形に戻したので、退行を機械的に禁じる。
    """
    tsx = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "app"
           / "keirin" / "page.tsx")
    if not tsx.exists():          # backend だけを取り出した環境では skip
        pytest.skip("frontend が無い")
    src = tsx.read_text(encoding="utf-8")
    assert "MARQUEE_KEYWORDS" not in src, "フロントにキーワードが復活している"
    assert "isMarqueeRace" not in src, "フロントに判定関数が復活している"
    assert "pick.is_marquee" in src, "API のフラグを使っていない"


def test_this_module_imports_only_stdlib() -> None:
    """🔴 正本が標準ライブラリ以外へ依存しないこと。

    入稿側（keirin）は自分の venv からこのファイルを `importlib` で直接
    読み込む。FastAPI / SQLAlchemy / 相対 import を足すと、Web は無事なまま
    **入稿だけが起動時に落ちる**。
    """
    path = (Path(__file__).resolve().parents[1] / "src" / "services"
            / "keirin_marquee.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.level == 0, f"相対 import は使えません: {ast.dump(node)}"
            assert (node.module or "").split(".")[0] in {"__future__"}, (
                f"標準ライブラリ以外を import しています: {node.module}"
            )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] in {"re", "unicodedata"}, (
                    f"標準ライブラリ以外を import しています: {alias.name}"
                )


def test_keirin_side_does_not_duplicate_the_keywords() -> None:
    """🔴 入稿側がキーワードを写し戻していないこと（2026-08-11 一本化）。

    keirin が別リポジトリだった間は両方に写していた。統合後に写し戻ると
    「★は付くのに入稿されない」（またはその逆）を静かに作れる。

    ⚠️ 文字列 grep では docstring の説明まで拾うので **AST で右辺を見る**。
    """
    path = (Path(__file__).resolve().parents[2] / "keirin" / "src" / "marquee.py")
    if not path.exists():          # backend だけを取り出した環境では skip
        pytest.skip("keirin が無い")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        names = {t.id for t in node.targets if isinstance(t, ast.Name)}
        if names & {"MARQUEE_KEYWORDS", "MARQUEE_EXCLUDE"}:
            assert not isinstance(node.value, (ast.Tuple, ast.List, ast.Set)), (
                f"keirin 側で {sorted(names)} を定義し直しています"
            )
    defined = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    assert "is_marquee_type" not in defined, "keirin 側で判定を実装し直しています"


# ---------------------------------------------------------------------------
# 開催グレードによる穴埋め（2026-08-14 追加）
# ---------------------------------------------------------------------------


def test_grade_takes_priority_over_keywords():
    """🔴 GIII 以上の開催は race_type を問わず穴埋め対象（ユーザー判断 2026-08-14）。

    2026-08-14 松山（オールスター競輪・GI・11R）で、キーワードが拾えたのは
    「選抜(1)」とその前後だけで **7R〜11R（準々Ａ/Ｂ・シャイニングスター賞）が
    丸ごと無推奨**だった。番組名は開催ごとに自由なのでキーワードでは追いつかない。
    """
    from src.services.keirin_marquee import FILL_ALL_MIN_GRADE, is_fill_target

    for rt in ("準々Ｂ", "シャイニングスター賞", "一般", None):
        assert is_fill_target(rt, FILL_ALL_MIN_GRADE) is True, rt
        assert is_fill_target(rt, 6) is True, rt          # GP


def test_lower_grades_fall_back_to_keywords():
    """FI/FII はグレードで拾わない（日常の開催まで全レース対象になってしまう）。"""
    from src.services.keirin_marquee import is_fill_target

    assert is_fill_target("準々Ｂ", 2) is False
    assert is_fill_target("一般", 1) is False
    assert is_fill_target("決勝", 1) is True              # 看板は従来どおり


def test_missing_grade_keeps_the_old_behaviour():
    """🔴 `cup_grade` は 2026-08-14 に保存を始めた列。NULL でも壊れないこと。

    NULL を「対象外」にも「対象」にも倒さず、**従来のキーワード判定へ落ちる**。
    """
    from src.services.keirin_marquee import is_fill_target

    assert is_fill_target("決勝", None) is True
    assert is_fill_target("準々Ｂ", None) is False
    assert is_fill_target("決勝") is True                 # 引数なしでも従来どおり


def test_threshold_matches_the_cup_grade_module():
    """🔴 しきい値が `keirin_cup_grade` と一致すること。

    ⚠️ `keirin_marquee` は keirin 側が自分の venv から直接読み込むため
       **標準ライブラリ以外を import できない**。だから定数を複製しており、
       ここでしか一致を保証できない。
    """
    from src.services.keirin_cup_grade import BIG_EVENT_MIN_GRADE
    from src.services.keirin_marquee import FILL_ALL_MIN_GRADE

    assert FILL_ALL_MIN_GRADE == BIG_EVENT_MIN_GRADE
