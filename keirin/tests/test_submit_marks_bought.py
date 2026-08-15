"""入稿の成立を `picks_history.bet_amount` へ反映する経路を固定する（2026-08-15）。

## なぜこれが要るのか

7T1 は**発走前判定の経路を持たない**（`notify_results_wt.py` に 7T1 の分岐が無い）。
そのため候補行は当日ずっと `bet_amount=0` のまま置かれ、実際に値が入るのは
翌朝 08:40 の `reconcile_walkforward_tail.sh` → `rebuild_7t1_walkforward_pg.py`。

結果として **netkeirin で実際に売っているのに** Web 側では

  - 投資・回収サマリーから丸ごと落ちる（SQL が `bet_amount > 0` で絞る）
  - ランクバッジの購入◯ が付かない（`isBuyConfirmed` が同じ条件）

という状態になっていた（2026-08-15 ユーザー指摘。当日 候補12 / 件数0 / 投資¥0）。

## 静的検査を混ぜている理由

「送信が成立する地点」は**3か所に散っている**（直接入稿 / 承認 / 取消）。
どれも本番の入稿バッチか Web の操作でしか通らず、CI では実行されない
（[[keirin_wave_picks_unbound_lg_2026_08_07]]「本番でしか実行されない関数は
CI で守れない」と同型）。1か所でも呼び忘れると、
**例外は出ないまま その経路の購入だけが記録されない**。そこで AST で
「status を submitted / deleted へ書く関数は必ず対の処理を呼ぶ」を固定する。
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

import scripts.netkeirin_submit_wt as m

SRC = Path(m.__file__).read_text(encoding="utf-8")
TREE = ast.parse(SRC)


class FakeConn:
    """execute された SQL とパラメータを溜めるだけの conn。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, sql: str, params=()) -> None:
        self.calls.append((" ".join(sql.split()), tuple(params)))


def _detail(total: int) -> str:
    return json.dumps({"total": total, "lines": [{"combo": "1-2-3", "stake": total}]})


# --- 投資額の取り出し -------------------------------------------------------

@pytest.mark.parametrize("raw", [None, "", "{}", "not json", '{"total": null}'])
def test_total_is_zero_when_it_cannot_be_read(raw):
    """読めないときは 0。**0 なら何も書かない**ので、金額が分からないまま
    購入済みに見せることはない。"""
    assert m._bet_detail_total(raw) == 0


def test_total_is_taken_from_the_submitted_original():
    assert m._bet_detail_total(_detail(10000)) == 10000


# --- 購入の記録 -------------------------------------------------------------

def test_submit_marks_the_pick_as_bought():
    conn = FakeConn()
    m._mark_bought(conn, "20260815_53_04", "7T1", 10000)
    assert len(conn.calls) == 1
    sql, params = conn.calls[0]
    assert sql.startswith("UPDATE picks_history SET bet_amount")
    # picks_history のキーは race_key に `#ランク` が付き、rank は RANK_ 接頭辞
    assert params == (10000, "20260815_53_04#7T1", "RANK_7T1")


def test_only_ranks_without_a_prerace_decision_are_marked():
    """🔴 発走前判定を持つランクをここに足すと、「入稿したが直前オッズで
    買わなかった」レースまで購入済みになる。"""
    conn = FakeConn()
    for rank in ("7S", "7C", "7A", "7B", "7H1", "7H2", "9C", "9H1", "7SS"):
        m._mark_bought(conn, "20260815_53_04", rank, 10000)
    assert conn.calls == []
    assert m.RANKS_BOUGHT_ON_SUBMIT == frozenset({"7T1"})


def test_zero_total_never_writes():
    conn = FakeConn()
    m._mark_bought(conn, "20260815_53_04", "7T1", 0)
    assert conn.calls == []


def test_marking_never_overwrites_an_already_scored_row():
    """既に採点で金額が入った行は触らない（翌朝の再構築が正本）。"""
    conn = FakeConn()
    m._mark_bought(conn, "20260815_53_04", "7T1", 10000)
    sql, _ = conn.calls[0]
    assert "COALESCE(bet_amount, 0) = 0" in sql


def test_marking_never_inserts_a_row():
    """候補行が無いレース（看板の穴埋め・手動入稿）は `submission_only` として
    別扱いされている。ここで行を作るとランクのペーパー成績に混ざる。"""
    fn = _func("_mark_bought")
    body = fn.body[1:] if ast.get_docstring(fn) else fn.body   # docstring は除く
    for stmt in body:
        for node in ast.walk(stmt):
            assert not (isinstance(node, ast.Constant) and isinstance(node.value, str)
                        and "INSERT" in node.value.upper()), "_mark_bought が INSERT している"


# --- 取消で戻す -------------------------------------------------------------

def test_cancel_restores_the_bet_amount():
    conn = FakeConn()
    m._unmark_bought(conn, "20260815_53_04", "7T1")
    sql, params = conn.calls[0]
    assert sql.startswith("UPDATE picks_history SET bet_amount = 0")
    assert params == ("20260815_53_04#7T1", "RANK_7T1")


def test_cancel_does_not_erase_a_settled_result():
    """発走後に取り消したとき、確定した成績まで 0 に戻さない。"""
    conn = FakeConn()
    m._unmark_bought(conn, "20260815_53_04", "7T1")
    sql, _ = conn.calls[0]
    assert "COALESCE(payout, 0) = 0" in sql


# --- 呼び忘れの防止（静的検査）---------------------------------------------

def _func(name: str) -> ast.FunctionDef:
    for node in ast.walk(TREE):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} が見つかりません")


def _calls_in(name: str) -> set[str]:
    return {n.func.id for n in ast.walk(_func(name))
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}


@pytest.mark.parametrize("fn", ["_record_submission", "approve_and_submit"])
def test_every_submit_path_marks_the_purchase(fn):
    """🔴 送信成立は3か所に散っている。1つでも呼び忘れると、その経路の購入だけが
    静かに記録されない（例外は出ない）。

    - `_record_submission`: 承認制OFF のときの直接入稿
    - `approve_and_submit`: 承認制ON のときの唯一の送信成立地点
    """
    assert "_mark_bought" in _calls_in(fn), f"{fn} が _mark_bought を呼んでいない"


def test_cancel_path_unmarks_the_purchase():
    assert "_unmark_bought" in _calls_in("cancel_submission"), \
        "cancel_submission が _unmark_bought を呼んでいない"


def test_every_writer_of_the_submission_status_keeps_bet_amount_in_sync():
    """`netkeirin_submissions` の status を**書き換える**関数は、必ず
    `_mark_bought` か `_unmark_bought` のどちらかを呼ぶこと。

    送信成立の地点が増えたときに、対の処理を入れ忘れると
    **例外は出ないままその経路の購入だけが記録されない**。
    🔴 **この検査が落ちたときにやるべきは、期待値を緩めることではなく、
       新しい経路に対の処理を入れること。**
    """
    offenders = []
    for node in ast.walk(TREE):
        if not isinstance(node, ast.FunctionDef):
            continue
        src = ast.unparse(node)
        writes = ("UPDATE netkeirin_submissions SET status" in src
                  or "INSERT OR REPLACE INTO netkeirin_submissions" in src)
        if not writes:
            continue
        if not ({"_mark_bought", "_unmark_bought"} & _calls_in(node.name)):
            offenders.append(node.name)
    assert offenders == [], f"投資額の同期を呼んでいない送信経路: {offenders}"
