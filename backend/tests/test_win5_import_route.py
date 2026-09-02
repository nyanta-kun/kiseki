"""WIN5（WF）の**取込経路**を固定する検査。

## なぜパーサ単体テストでは足りないのか

0B11（速報馬体重）は「パーサはあったのに rec_id の振り分け漏れで全件捨てられ、
**200 が返り続けていた**」という事故を起こした（CLAUDE.md 参照）。壊れていたのは
パーサではなく**経路**で、`test_parse_wh.py` が何本通っていても検出できなかった。

WF も同じ構造にある——`race_importer` は RA/SE しか見ないので、WF を汎用の
取込経路に混ぜると同じ形で無言の全件破棄が起きる。このテストは

1. `/api/import/win5` という**専用の口が存在すること**
2. **生の WF レコードを渡すとサーバ側でパースされること**
   （エージェントのパーサに依存しないこと）
3. **`unresolved_races` が戻り値に含まれること**（誰にも一致しないのに 200 が返るのを
   呼び出し側が検出できること）
4. 区分1（詳細発表）で区分7（成績）の確定値を潰さないこと

を機械的に守る。
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.api import import_router  # noqa: E402
from src.importers.jvlink_parser import parse_wf  # noqa: E402
from tests.test_parse_wf import _wf_record  # noqa: E402


def test_win5_endpoint_is_registered() -> None:
    """専用の口が存在すること。汎用経路に混ぜると 0B11 型の破棄が起きる。"""
    paths = {r.path for r in import_router.router.routes}
    assert "/api/import/win5" in paths, (
        "WIN5 の取込口が消えている。汎用の import 経路へ寄せてはいけない"
        "（race_importer は RA/SE しか見ないため WF は無言で捨てられる）"
    )


def test_request_takes_raw_records_not_parsed() -> None:
    """🔴 リクエストは**生の WF レコード**を受け取ること。

    エージェント側でパースする設計にすると、実機の jvlink_parser.py に
    依存する。それは git 管理外で 2026-05-04 付と4か月古く、更新手順も無い。
    さらに main のパーサは相対 import を持つため実機へそのまま置けず、
    置くと既存の HR 払戻経路まで壊れる（2026-09-02 実機確認）。
    /api/import/weights（0B11）と同じ「生を送ってサーバがパース」に揃える。
    """
    req = import_router.Win5ImportRequest(
        records=[{"rec_id": "WF", "data": _wf_record()}]
    )
    assert len(req.records) == 1
    assert req.records[0].rec_id == "WF"
    # パース済み dict を渡す設計に戻していないこと
    assert not hasattr(req.records[0], "held_date"), (
        "リクエストがパース済みの形になっている。実機のパーサ更新が必要になり、"
        "更新手段が無いので取り込めなくなる"
    )


def test_server_side_parse_produces_expected_fields() -> None:
    """サーバ側のパース結果が期待どおりであること。"""
    parsed = parse_wf(_wf_record())
    assert parsed is not None
    rec = import_router.Win5Record.model_validate(parsed)
    assert rec.held_date == "20260830"
    assert len(rec.legs) == 5
    assert rec.legs[0].jravan_race_id == "2026083006030510"
    assert len(rec.payouts) == 1
    assert rec.payouts[0].payout == 1234560


def test_response_reports_unresolved_races() -> None:
    """合成 ID が races に1件も無くても取込は落ちないが、件数を必ず返すこと。"""
    body = import_router.Win5ImportRequest(
        records=[{"rec_id": "WF", "data": _wf_record()}]
    )

    db = AsyncMock()
    # races の解決は0件、win5_events の id 取得は 1 を返す
    empty = MagicMock()
    empty.__iter__ = lambda self: iter([])
    scalar_one = MagicMock()
    scalar_one.scalar_one = MagicMock(return_value=1)
    db.execute = AsyncMock(side_effect=[empty, MagicMock(), scalar_one]
                           + [MagicMock() for _ in range(10)])

    import asyncio
    res = asyncio.run(import_router.import_win5(body, _=None, db=db))

    assert res["unresolved_races"] == 5, (
        "races に解決できなかった脚の数が返っていない。"
        "0 件一致でも 200 が返るのが 0B11 型の失敗形なので、"
        "呼び出し側が件数で気づけるようにしておくこと"
    )
    assert res["imported"] == 1
    assert res["legs"] == 5


def test_empty_request_is_safe() -> None:
    import asyncio
    body = import_router.Win5ImportRequest(records=[])
    res = asyncio.run(import_router.import_win5(body, _=None, db=AsyncMock()))
    assert res["imported"] == 0 and res["legs"] == 0 and res["unresolved_races"] == 0


def test_preliminary_kubun_is_recognised() -> None:
    """区分1（詳細発表）は確定値を持たないので上書き対象から外れること。

    後から区分1が届いて区分7（成績）の払戻・キャリーオーバー残高を
    NULL で潰すのを防ぐ。
    """
    assert "1" in import_router._PRELIMINARY_KUBUN
    assert "7" not in import_router._PRELIMINARY_KUBUN
    assert "3" not in import_router._PRELIMINARY_KUBUN


@pytest.mark.parametrize("kubun", ["7", "3", "9"])
def test_confirmed_kubun_not_treated_as_preliminary(kubun: str) -> None:
    assert kubun not in import_router._PRELIMINARY_KUBUN
