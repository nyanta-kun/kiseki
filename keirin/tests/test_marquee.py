"""看板レース検出の不変条件（2026-08-09 新設）。

## 背景

2026-08-08 は当日売上の 84% が「外れたレース」＝看板レース（決勝・特選クラス）に
集中し、当たった準決勝・予選は買い手0だった。ユーザー決定で
**看板レースとその前後には必ず推奨を出す**方針になった。

2026-08-09 時点では検出が無く、当日の看板11件を手作業で入稿していた。

## 守る不変条件

1. 判定は **race_type**。レース番号（最終R＝決勝）で判定しない
   — ガールズ決勝が 6R と 12R の両方に置かれる開催が実在する（08-09 佐世保）
2. 「前後」は看板の ±1R。**存在しないレース番号は返さない**
3. 🔴 判定の定義を**ここで持たない**（2026-08-11 一本化）
   — 正本は kiseki 側 `backend/src/services/keirin_marquee.py`
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.marquee import (  # noqa: E402
    MARQUEE_EXCLUDE,
    MARQUEE_KEYWORDS,
    is_marquee_type,
    marquee_race_nos,
)

_MARQUEE_PY = Path(__file__).resolve().parents[1] / "src" / "marquee.py"
_CANONICAL_PY = (Path(__file__).resolve().parents[2]
                 / "backend" / "src" / "services" / "keirin_marquee.py")


def test_marquee_keywords() -> None:
    for t in ("決勝", "ガールズ決勝", "チャレンジ決勝", "特選", "初特選",
              "選抜", "ガールズ選抜", "特秀", "男子新人アドバンス決勝"):
        assert is_marquee_type(t), t


def test_non_marquee_types() -> None:
    for t in ("予選", "チャレンジ予選", "一般", "ガールズ一般", "準決勝",
              "特予選", "Wガル", "", None):
        assert not is_marquee_type(t), t


def test_semifinal_is_not_marquee() -> None:
    """準決勝は看板ではない（「決勝」を含むが別物）。

    ⚠️ 部分一致で拾うと準決勝まで対象になり件数が跳ねる。
    """
    assert not is_marquee_type("準決勝")
    assert not is_marquee_type("チャレンジ準決勝")


def test_neighbours_are_included() -> None:
    races = [{"race_no": n, "race_type": "予選"} for n in range(1, 13)]
    races[6]["race_type"] = "決勝"          # 7R
    assert marquee_race_nos(races) == {6, 7, 8}


def test_multiple_marquee_races_in_one_meeting() -> None:
    """ガールズ決勝が6Rと12Rの両方にある開催（2026-08-09 佐世保）。"""
    races = [{"race_no": n, "race_type": "ガールズ一般"} for n in range(1, 13)]
    races[5]["race_type"] = "ガールズ決勝"    # 6R
    races[11]["race_type"] = "ガールズ決勝"   # 12R
    assert marquee_race_nos(races) == {5, 6, 7, 11, 12}


def test_does_not_return_missing_race_numbers() -> None:
    """存在しないレース番号（欠番・最終Rの次）を返さない。"""
    races = [{"race_no": n, "race_type": "予選"} for n in (1, 2, 3)]
    races[2]["race_type"] = "決勝"           # 3R が最終
    assert marquee_race_nos(races) == {2, 3}


def test_race_no_alone_does_not_qualify() -> None:
    """🔴 最終Rでも race_type が一般なら看板ではない。"""
    races = [{"race_no": n, "race_type": "一般"} for n in range(1, 13)]
    assert marquee_race_nos(races) == set()


# ---- 一本化の不変条件（2026-08-11）--------------------------------------
# keirin が別リポジトリだった間はキーワードを両方へ写していた。統合後に
# 写し戻ると「★は付くのに入稿されない」（またはその逆）を静かに作れる。


def test_keywords_are_not_redefined_here() -> None:
    """🔴 `src/marquee.py` がキーワードを自前で定義していないこと。

    ⚠️ 文字列 grep では docstring 中の「決勝 / 特選 …」という説明まで拾って
       しまうので **AST で代入の右辺を見る**（過去に grep 方式の検査が
       docstring を拾って偽陽性を出している）。
    """
    tree = ast.parse(_MARQUEE_PY.read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        names = {t.id for t in node.targets if isinstance(t, ast.Name)}
        if not names & {"MARQUEE_KEYWORDS", "MARQUEE_EXCLUDE"}:
            continue
        assert not isinstance(node.value, (ast.Tuple, ast.List, ast.Set)), (
            f"{sorted(names)} をここで定義し直しています。"
            f"正本は {_CANONICAL_PY} です（写すと二重管理が復活します）。"
        )


def test_judgement_is_not_reimplemented_here() -> None:
    """🔴 判定関数を書き直していないこと（正本の束縛であること）。"""
    tree = ast.parse(_MARQUEE_PY.read_text(encoding="utf-8"))
    defined = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    assert "is_marquee_type" not in defined, (
        "is_marquee_type をここで実装し直しています。"
        f"正本 {_CANONICAL_PY} の関数をそのまま束縛してください。"
    )


def test_values_match_the_canonical_source() -> None:
    """正本のファイルを直接読み、値が一致すること。"""
    src = _CANONICAL_PY.read_text(encoding="utf-8")
    ns: dict = {}
    exec(compile(src, str(_CANONICAL_PY), "exec"), ns)   # noqa: S102 - 自リポジトリ内
    assert MARQUEE_KEYWORDS == ns["MARQUEE_KEYWORDS"]
    assert MARQUEE_EXCLUDE == ns["MARQUEE_EXCLUDE"]
    for t in ("決勝", "特選", "準決勝", "一般", None):
        assert is_marquee_type(t) == ns["is_marquee_race"](t), t


def test_grade_covers_every_race_of_a_big_meeting():
    """🔴 GIII 以上の開催は**全レース**が穴埋め対象（2026-08-14・ユーザー判断）。

    2026-08-14 松山（オールスター競輪・GI・11R）の実データ。キーワード判定では
    「選抜(1)」とその前後しか拾えず 7R〜11R が無推奨だった。
    """
    from src.marquee import marquee_race_nos

    rs = [{"race_no": 1, "race_type": "一般", "cup_grade": 5},
          {"race_no": 3, "race_type": "選抜(１)", "cup_grade": 5},
          {"race_no": 7, "race_type": "準々Ｂ", "cup_grade": 5},
          {"race_no": 11, "race_type": "シャイニングスター賞", "cup_grade": 5}]
    assert marquee_race_nos(rs) == {1, 3, 7, 11}


def test_without_grade_the_keyword_behaviour_is_unchanged():
    """🔴 `cup_grade` が無い（NULL・古いレース）ときは従来どおり看板＋前後1R。"""
    from src.marquee import marquee_race_nos

    rs = [{"race_no": n, "race_type": t} for n, t in
          ((1, "一般"), (2, "一般"), (3, "決勝"), (4, "一般"), (5, "一般"))]
    assert marquee_race_nos(rs) == {2, 3, 4}


def test_low_grade_meeting_is_not_expanded():
    """FI/FII をグレードで拾わない（日常の開催が丸ごと対象になってしまう）。"""
    from src.marquee import marquee_race_nos

    rs = [{"race_no": n, "race_type": "一般", "cup_grade": 2} for n in range(1, 12)]
    assert marquee_race_nos(rs) == set()


# ---------------------------------------------------------------------------
# 看板レースのタイトル（2026-08-14・ユーザー要望「レースの特徴を入れる」）
# ---------------------------------------------------------------------------


def test_marquee_title_shows_the_race_shape():
    """🔴 他ランクと同じく `｜{shape}` でレース形を出すこと。

    2026-08-09〜08-14 は固定文字列「本日の二軸」で、看板だけが無個性だった。
    """
    from scripts.netkeirin_submit_wt import _MARQUEE_TITLE_TEMPLATE as t

    assert "{shape}" in t
    assert t != "本日の二軸"


def test_marquee_title_does_not_carry_grade_or_race_type():
    """🔴 種別・グレードはタイトルに入れない（通常ランクと方針を揃える）。"""
    from scripts.netkeirin_submit_wt import _MARQUEE_TITLE_TEMPLATE as t

    assert "{race_type}" not in t
    assert "{race_label}" not in t
    assert "GI" not in t


# ---------------------------------------------------------------------------
# 穴埋めのランク名（2026-08-16・7車の穴埋めが2日間全滅した実バグ）
# ---------------------------------------------------------------------------


def test_marquee_fill_rank_names_are_accepted_by_the_submitter():
    """🔴 `RANK_BY_CARS` の値は必ず `MANUAL_ALLOWED_RANKS` に載っていること。

    載っていないランク名を渡すと `--manual-rank-key` の argparse choices で
    **プロセスが即死**する。看板の穴埋めは1件も入稿されず、ログには
    `invalid choice` と「失敗N件」しか残らない。

    実害: PR#145（2026-08-14）が `MANUAL_ALLOWED_RANKS` から 7A を外したとき
    `RANK_BY_CARS` の 7車側を付け替え忘れ、2026-08-15 の7車穴埋め **9件が全滅**した。
    9車は 9C で成功していたため「穴埋めは動いている」ように見えていた。

    ⚠️ ランク集合の二重管理はこのリポジトリが繰り返し踏んでいる型（CLAUDE.md
       「変更時チェックリスト」）。片側だけ直しても落ちるように機械で縛る。
    """
    from scripts.netkeirin_submit_wt import MANUAL_ALLOWED_RANKS
    from scripts.submit_marquee_wt import RANK_BY_CARS

    for n_cars, rank in RANK_BY_CARS.items():
        assert rank in MANUAL_ALLOWED_RANKS, (
            f"{n_cars}車の穴埋めランク {rank} が MANUAL_ALLOWED_RANKS "
            f"{MANUAL_ALLOWED_RANKS} にありません（入稿が argparse で即死します）"
        )


def test_marquee_fill_rank_matches_the_car_count():
    """🔴 穴埋めランクの想定車数が対応表のキーと一致すること。

    `_process_manual` は `n_entries != cfg["n_cars"]` で弾くので、ここがずれると
    argparse は通っても**入稿0件のまま「車数不一致」で失敗し続ける**。
    """
    from scripts.netkeirin_submit_wt import RANK_CONFIGS
    from scripts.submit_marquee_wt import RANK_BY_CARS

    for n_cars, rank in RANK_BY_CARS.items():
        assert rank in RANK_CONFIGS, f"{n_cars}車の穴埋めランク {rank} が RANK_CONFIGS にありません"
        assert RANK_CONFIGS[rank]["n_cars"] == n_cars, (
            f"{n_cars}車の穴埋めに {rank}（{RANK_CONFIGS[rank]['n_cars']}車想定）を使っています"
        )


def test_marquee_fill_rank_has_no_gate_label():
    """看板は「必ず出す」ので、自信度を意味するゲート表示を付けない。"""
    from scripts.netkeirin_submit_wt import RANK_CONFIGS
    from scripts.submit_marquee_wt import RANK_BY_CARS

    for n_cars, rank in RANK_BY_CARS.items():
        assert rank in RANK_CONFIGS, f"{n_cars}車の穴埋めランク {rank} が RANK_CONFIGS にありません"
        assert RANK_CONFIGS[rank]["gate_filter"] is None, (
            f"{n_cars}車の穴埋め {rank} にゲート表示が付いています"
        )
