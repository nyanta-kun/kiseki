"""レース確定通知の内容と対象範囲を固定する（2026-08-15）。

## 直した3つの症状（ユーザー報告「通知内容がバラバラ・通知がないレースもある」）

2026-08-15 松山（9車・全11R を 9C の看板穴埋めで販売）の実測:

| 症状 | 原因 | 該当 |
|---|---|---|
| 通知が来ない | 対象が `picks_history` のみ＝**売っているのに候補行が無いレースは対象外** | 2R/3R/10R |
| 通知が来ない | 確定が `CHECK_MINUTES`(6/10/15/25分) を過ぎると**永久に諦める** | 8R |
| 着順だけで推奨行が無い | 推奨行を `picks_history` から作るので、入稿OFFの 9H1 しか行が無いと消える | 1R/5R/6R/7R |

いずれも**例外が出ない**。通知が減るだけなので気づけない。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

import scripts.notify_race_result_wt as m


class FakeConn:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, sql, params=()):
        self._last = (sql, params)
        return self

    def fetchall(self):
        return self._rows


# --- 確定配当の取り出し -----------------------------------------------------

def test_payouts_come_from_the_final_odds_board():
    """🔴 入稿時オッズではなく確定オッズを使うこと。

    実測 2026-08-15 松山9R は入稿時 9.0倍 → 確定 6.3倍。入稿時のまま計算すると
    払戻が 33,300円（正しくは 23,310円）と **43% 過大**になる。
    """
    conn = FakeConn([
        {"bet_type": "trio", "combination": "1-2-8", "odds_value": 6.3},
        {"bet_type": "trifecta", "combination": "8-2-1", "odds_value": 34.9},
    ])
    got = m._confirmed_payouts(conn, "20260815_75_09")
    assert got[("3連複", (1, 2, 8))] == 630
    assert got[("3連単", (8, 2, 1))] == 3490
    sql, _ = conn._last
    assert "wt_odds" in sql, "確定配当は wt_odds から引くこと"


def test_trio_keys_are_sorted_and_trifecta_keeps_the_order():
    """三連複は順不同・三連単は着順。畳み方を間違えると着順違いを的中に数える。"""
    conn = FakeConn([
        {"bet_type": "trio", "combination": "8-2-1", "odds_value": 6.3},
        {"bet_type": "trifecta", "combination": "8-2-1", "odds_value": 34.9},
    ])
    got = m._confirmed_payouts(conn, "rk")
    assert ("3連複", (1, 2, 8)) in got
    assert ("3連単", (8, 2, 1)) in got
    assert ("3連単", (1, 2, 8)) not in got


def test_rows_without_odds_are_skipped():
    conn = FakeConn([{"bet_type": "trio", "combination": "1-2-3", "odds_value": None}])
    assert m._confirmed_payouts(conn, "rk") == {}


# --- 定数の食い違い防止 -----------------------------------------------------

def test_status_value_matches_the_submit_script():
    """`STATUS_SUBMITTED` を2箇所に書いているので値の一致を固定する。

    ⚠️ 入稿一式を import すると毎分 cron が重くなるため、あえて文字列を持っている。
       ずれると**対象が1件も取れなくなる**（例外は出ない）。
    """
    from scripts.netkeirin_submit_wt import STATUS_SUBMITTED as canonical
    assert m.STATUS_SUBMITTED == canonical


def test_deleted_status_value_matches_the_submit_script():
    """`STATUS_DELETED` も同じ理由で一致を固定する。

    ずれると**却下した推奨が的中通知に混ざる**（値が違うだけで例外は出ない）。
    """
    from scripts.netkeirin_submit_wt import STATUS_DELETED as canonical
    assert m.STATUS_DELETED == canonical


def test_cancelled_submission_is_still_notified():
    """🔴 取消した推奨も通知する（2026-08-18 ユーザー方針）。

    「全推奨（取消も含む）を通知し、買い目・払戻・的中を出す。取消なら
    取り消したことを含める」。**黙って落とすと出した推奨の結果が追えなくなる。**

    経緯: 2026-08-18 高知8R の 7S は 07:08 提案 → 07:15 取消（意図的）。
    当初これを「通知から外す」方向で直しかけたが、方針は逆だった。
    """
    src = _src()
    assert "STATUS_DELETED" in src, "取消ステータスを扱っていない"
    # 入稿の取得が submitted だけに戻っていないこと
    assert "status IN (?, ?)" in src, (
        "入稿の取得が status='submitted' だけに戻っている＝取消が落ちる")
    assert "（取消）" in src, "取消であることを表示していない"


def test_day_total_counts_only_sold():
    """⚠️ 当日合計は**実売のみ**。取消を混ぜると売上が水増しされる。"""
    src = _src()
    body = src[src.index("def _day_total("):]
    body = body[:body.index("\ndef ", 10)]
    assert "STATUS_SUBMITTED" in body
    assert "STATUS_DELETED" not in body, "当日合計に取消が混ざっている"


def test_sold_line_shows_buy_and_payout():
    """買い目・的中・払戻の3点を必ず出す（ユーザー要件）。"""
    src = _src()
    body = src[src.index("def _sold_lines("):]
    body = body[:body.index("\ndef ", 10)]
    assert "format_pred_combo" in body, "買い目を出していない"
    assert "払戻" in body and "想定" in body, "払戻/想定の表記が無い"
    assert "的中" in body


# --- 対象範囲（静的検査）----------------------------------------------------

def _src() -> str:
    return Path(m.__file__).read_text(encoding="utf-8")


def _func(name: str) -> ast.FunctionDef:
    tree = ast.parse(_src())
    return next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == name)


def test_targets_include_submitted_races():
    """🔴 対象は picks_history だけではない。売ったレースは全部含めること。

    これを戻すと**看板の穴埋めだけで売っているレースが通知されなくなる**。
    """
    src = ast.unparse(_func("_targets"))
    assert "netkeirin_submissions" in src
    assert "picks_history" in src


def test_targets_do_not_drop_races_that_already_have_results():
    """🔴 結果が既にあるレースを対象から外さないこと。

    外すと (1) 15分毎の intraday に先を越されたレース (2) 確定が
    CHECK_MINUTES を過ぎたレース が永久に通知されない。
    """
    src = ast.unparse(_func("_targets"))
    assert 'fetch' in src, "取得の要否と対象の可否を分けること"
    # 「結果があれば continue」という早期打ち切りが復活していないこと
    assert "if n > 0:" not in src


def test_daily_total_is_reported():
    """ユーザー要望「推奨した結果として払い戻し総額も出して」（2026-08-15）。"""
    src = ast.unparse(_func("_build_message"))
    assert "_day_total" in src
    assert "払戻" in src


def test_sold_lines_win_over_the_paper_lines():
    """同じランクで入稿原本とペーパー候補の両方を出さないこと（重複表示の防止）。"""
    src = ast.unparse(_func("_build_message"))
    assert "sold_ranks" in src


def test_day_total_counts_only_settled_races():
    """未確定レースを投資に数えない（回収率が不当に下がる）。"""
    src = ast.unparse(_func("_day_total"))
    assert "len(order3) < 3" in src
