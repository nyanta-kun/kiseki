"""7H1 の三連単一本化が経路のどこかで戻っていないこと（2026-08-15）。

## なぜ経路ごと固定するのか

7H1 は **三連複BOX を併せ買いする唯一のランク**だった。一本化で外したが、
このリポジトリで繰り返し起きているのは「1経路だけ直し忘れる」型の事故で、
7H1 の場合は特に静かに壊れる:

    judge_rank_7h1 / 採点の注入は **`legs_trio` の有無で購入を決めていた**。
    `legs_tf` へ替え損ねると **1件も買わずに正常終了**する（例外もログも出ない）。

したがって「trio を見ていないこと」を経路ごとに機械的に固定する。

⚠️ 7H2 は**2券種のまま**。ここで 7H2 まで巻き込んで禁止しないこと。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

#: 7H1 を扱う本番経路と、その中で trio を見てはいけない関数
_GUARDED: dict[str, tuple[str, ...]] = {
    "scripts/notify_prerace_wt.py": (
        "judge_rank_7h1", "_insert_rank_7h1_pick", "_build_rank_7h1_message",
        "_process_rank_7h1_candidates",
    ),
    "scripts/backfill_7h1_rank_wt.py": ("build_rows",),
    "scripts/build_7h1_candidates.py": ("build",),
}


def _code_of(path: str, name: str) -> str:
    """関数の**コードだけ**を文字列で返す（docstring は落とす）。

    ⚠️ docstring を残すと「一本化前はこうだった」という説明文に反応して落ちる。
       検査したいのは実行される式であって、経緯の記述ではない。
    """
    tree = ast.parse((REPO / path).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            body = node.body
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                body = body[1:]
            return "\n".join(ast.unparse(stmt) for stmt in body)
    raise AssertionError(f"{path} に {name}() が無い（改名したらこのテストも直すこと）")


@pytest.mark.parametrize(
    ("path", "name"),
    [(p, n) for p, names in _GUARDED.items() for n in names],
)
def test_no_trio_key_in_7h1_paths(path, name):
    """🔴 7H1 の本番経路が三連複のキーを読み書きしていないこと。

    `trio_payout` は検査しない——picks_history の**列名**として残っており
    （常に 0 を入れる）、列の存在自体は一本化と無関係だから。禁じるのは
    買い目・賭け金・欠車の各キー＝**挙動を決める**ものだけ。
    """
    src = _code_of(path, name)
    for key in ("legs_trio", "stake_trio", "dropped_trio"):
        assert key not in src, (
            f"{path}::{name}() に三連複のキー {key!r} が残っている。"
            " 一本化前の形へ戻ると、購入判定が legs_trio に依存して"
            "**1件も買わずに静かに終わる**")


def test_build_legs_is_single_valued():
    """`rank_7h1_build_legs` が (trio, tf) の2値タプルへ戻っていないこと。"""
    from src.preprocessing.favbust_features import (
        ROLE_FAV_MATE, ROLE_LEAD_TOP, ROLE_OTHER_MATE,
    )
    from src.strategy_wt import rank_7h1_build_legs

    got = rank_7h1_build_legs(
        [3, 4, 5, 1, 2, 6],
        {3: ROLE_LEAD_TOP, 4: ROLE_OTHER_MATE, 5: ROLE_OTHER_MATE,
         1: ROLE_OTHER_MATE, 2: ROLE_OTHER_MATE, 6: ROLE_FAV_MATE})
    assert isinstance(got, list) and got and isinstance(got[0], str)


def test_daily_select_does_not_require_trio():
    """選別が `legs_trio` を要求していないこと（要求すると全件落ちる）。"""
    from src import strategy_wt as sw

    cand = {"n_entries": 7, "gap12": sw.RANK_7H1_GAP_MIN + 0.05,
            "bust_prob": sw.RANK_7H1_BUST_PROB_MIN + 0.05,
            "legs_tf": ["4-2-3"]}          # legs_trio を**持たない**
    assert sw.rank_7h1_daily_select([cand]) == [cand]


def test_submit_config_uses_the_formation_route():
    """入稿が 9H1 と同じ単一券種経路であること。"""
    from scripts.netkeirin_submit_wt import RANK_CONFIGS
    assert RANK_CONFIGS["7H1"].get("formation_bet") is True
    assert "multi_bet" not in RANK_CONFIGS["7H1"]
    # 2券種経路そのものが 7H2 専用に縮んでいること
    assert RANK_CONFIGS["7H2"].get("multi_bet_7h2") is True


def test_scoring_injection_reads_legs_tf():
    """採点側の注入（seven_7h1）が `legs_tf` を見ていること。

    🔴 ここが `legs_trio` のままだと **picks に1件も載らない**＝その日の 7H1 が
       まるごと採点されない。例外は出ないので気づけない。
    """
    src = (REPO / "scripts" / "notify_results_wt.py").read_text(encoding="utf-8")
    i = src.index('_key.endswith("#7H1")')
    block = src[i:i + 400]
    assert '_dec.get("legs_tf")' in block
    assert '_dec.get("legs_trio")' not in block
