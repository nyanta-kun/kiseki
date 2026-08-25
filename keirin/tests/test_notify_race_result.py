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
    # 当たり目の表記（三連複 `1=2=8` / 三連単 `8-2-1`）をキーにする。
    # 🔴 Web（`backend/src/services/keirin_settlement.py`）と**同じ表記**でないと
    #    同じ商品が別々に採点される。
    assert got["1=2=8"] == 630
    assert got["8-2-1"] == 3490
    sql, _ = conn._last
    assert "wt_odds" in sql, "確定配当は wt_odds から引くこと"


def test_trio_keys_are_sorted_and_trifecta_keeps_the_order():
    """三連複は順不同・三連単は着順。畳み方を間違えると着順違いを的中に数える。"""
    conn = FakeConn([
        {"bet_type": "trio", "combination": "8-2-1", "odds_value": 6.3},
        {"bet_type": "trifecta", "combination": "8-2-1", "odds_value": 34.9},
    ])
    got = m._confirmed_payouts(conn, "rk")
    assert "1=2=8" in got            # 三連複は昇順へ畳む
    assert "8-2-1" in got            # 三連単は着順のまま
    assert "1-2-8" not in got


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
    src = ast.unparse(_func("_sold_lines"))
    assert "NOTIFY_STATUSES" in src, (
        "入稿の取得が実売の状態だけに戻っている＝取消が落ちる")
    assert m.STATUS_DELETED in m.NOTIFY_STATUSES
    assert "（取消）" in _src(), "取消であることを表示していない"


def test_published_status_value_matches_the_submit_script():
    """`STATUS_PUBLISHED` も値の一致を固定する。

    🔴 ずれると**通知から金額が丸ごと消える**（例外は出ない）。2026-08-16 に
       netkeirin の「公開」が入って以降、入稿は当日中に submitted → published へ
       進むため、submitted だけを見ていた本スクリプトは入稿原本の行も当日累計も
       出せなくなっていた（2026-08-20 の通知で実測）。
    """
    from scripts.netkeirin_submit_wt import STATUS_PUBLISHED as canonical
    assert m.STATUS_PUBLISHED == canonical


def test_sold_means_submitted_or_published():
    """「売った」＝ submitted ∪ published。Web（keirin_router）と同じ定義。

    🔴 published を落とすと、対象抽出・入稿原本の行・当日累計の**3箇所が同時に**
       欠ける。どれも例外を出さないので「今日は推奨が無かった」ようにしか見えない。
    """
    assert set(m.SOLD_STATUSES) == {m.STATUS_SUBMITTED, m.STATUS_PUBLISHED}
    for name in ("_targets", "_day_total"):
        src = ast.unparse(_func(name))
        assert "SOLD_STATUSES" in src, f"{name} が実売の定義を共有していない"


def test_day_total_counts_only_sold():
    """⚠️ 当日合計は**実売のみ**。取消を混ぜると売上が水増しされる。"""
    src = _src()
    body = src[src.index("def _day_total("):]
    body = body[:body.index("\ndef ", 10)]
    assert "SOLD_STATUSES" in body
    assert "STATUS_DELETED" not in body, "当日合計に取消が混ざっている"


def test_sold_line_shows_buy_and_payout():
    """買い目・的中・払戻の3点を必ず出す（ユーザー要件）。"""
    src = _src()
    body = src[src.index("def _sold_lines("):]
    body = body[:body.index("\ndef ", 10)]
    assert "format_bet_lines" in body, "買い目を出していない"
    assert "払戻" in body and "想定" in body, "払戻/想定の表記が無い"
    assert "的中" in body


def test_sold_line_prefers_the_bet_detail_over_the_paper_candidate():
    """🔴 買い目は**実際に入稿した bet_detail** が正本。

    picks_history は候補なので、看板の穴埋めで売ったレースには行が無く
    買い目が空欄のまま通知されていた。
    """
    src = ast.unparse(_func("_sold_lines"))
    assert src.index("format_bet_lines") < src.index("format_pred_combo"), \
        "picks_history を先に見ている（bet_detail が正本）"


# --- 表示（2026-08-21 ユーザー要望）------------------------------------------

def test_finishing_order_shows_car_and_mark_only():
    """🔴 着順に選手名を出さない。車番と印だけ（2026-08-21 ユーザー方針）。

    名前があると1行が折り返し、車番で書かれた買い目と突き合わせにくい。
    """
    src = ast.unparse(_func("_build_message"))
    assert "prediction_mark" in src and "MARK" in src, "印を出していない"
    # 出走表から名前を引いていないこと（venue_name は会場名なので別物）
    assert "'name'" not in src and '"name"' not in src, "着順に選手名が復活している"
    assert "wt_entries" in src
    entries_sql = next(x for x in src.split("'") if "FROM wt_entries" in x)
    assert " name" not in entries_sql, "出走表から選手名を取り続けている"


def test_race_payout_is_reported():
    """三連複・三連単の確定配当を出す（2026-08-21 ユーザー要望）。"""
    assert ast.unparse(_func("_build_message")).count("_race_payout_line") == 1
    payouts = {"1=3=7": 1640, "7-1-3": 4720}
    won = ["1=3=7", "7-1-3"]
    assert m._race_payout_line(payouts, won) == "確定配当: 3連複 ¥1,640 / 3連単 ¥4,720"


def test_race_payout_uses_the_finishing_order_for_trifecta():
    """三連複は順不同・三連単は着順。取り違えると別の目の配当を出す。"""
    payouts = {"7-1-3": 4720}
    # 着順が 1-7-3 なら当たり目は `1-7-3`。`7-1-3` の配当を出してはいけない。
    assert m._race_payout_line(payouts, ["1=3=7", "1-7-3"]) is None, \
        "着順違いの三連単配当を出している"


def test_race_payout_line_is_dropped_when_unavailable():
    """引けないときは行ごと落とす（`—` を並べない）。"""
    assert m._race_payout_line({}, ["1=2=3", "1-2-3"]) is None
    assert m._race_payout_line({"1=2=3": 630}, []) is None


def test_bet_kind_labels_are_not_printed():
    """🔴 券種は区切り文字が表す（三連複 `=` / 三連単 `-`）ので `三単:` は出さない。

    表記が統一されていればラベルは冗長というユーザー方針（2026-08-21）。
    """
    tree = ast.parse(_src())
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and getattr(n.func, "id", None) == "format_pred_combo"]
    assert calls, "買い目の整形が消えている"
    for c in calls:
        kw = {k.arg: k.value for k in c.keywords}
        assert isinstance(kw.get("labels"), ast.Constant) and kw["labels"].value is False, \
            "券種ラベルつきで買い目を出している箇所がある"


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
    """未確定レースを投資に数えない（回収率が不当に下がる）。

    🔴 `settled` を見ずに `got is None` だけで弾くと、**当たっているが確定配当が
       まだ引けていない**レースが払戻0円として回収率に入る。
    """
    src = ast.unparse(_func("_day_total"))
    assert "not got.settled" in src
