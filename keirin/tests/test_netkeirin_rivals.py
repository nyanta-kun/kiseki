"""netkeirin 全予想家の商品成績の取り込み（`src/netkeirin_rivals.py`）の回帰テスト。

フィクスチャは 2026-10-13 の実物の応答から li を間引いたもの:
  settled.html   … いわき平1R。払戻>購入 / ガミ（0<払戻<購入）/ 外れ の3商品
  unsettled.html … いわき平7R（ミッドナイト・取得時点で未確定）の2商品

🔴 いちばん守りたいのは「未確定を 0 円（外れ）にしない」こと。
   class だけ見ると未確定も `NoHit` なので、払戻欄の有無で判定している。
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src import netkeirin_rivals as NR  # noqa: E402
from src.database import get_connection  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures" / "netkeirin_rivals"
SETTLED = (FIX / "settled.html").read_text(encoding="utf-8")
UNSETTLED = (FIX / "unsettled.html").read_text(encoding="utf-8")

#: 本物と衝突しない日付（テストは共有の SQLite を使う）
DAY = "2999-10-13"
RK1, RK2 = "29991013_13_01", "29991013_13_07"


def test_race_id_of():
    assert NR.race_id_of("20261013_81_05") == "202610138105"


def test_parse_settled_reads_visible_amounts():
    got = NR.parse(RK1, SETTLED)
    assert got["n_declared"] == 3
    rows = {g["yosoka_id"]: g for g in got["goods"]}
    assert set(rows) == {410, 345, 583}
    assert (rows[410]["bet"], rows[410]["payout"], rows[410]["profit"]) == (9600, 15860, 6260)
    # ガミ: 払戻はあるが購入を下回る
    assert (rows[345]["bet"], rows[345]["payout"], rows[345]["profit"]) == (10000, 8500, -1500)
    # 外れは 0（NULL ではない）
    assert (rows[583]["bet"], rows[583]["payout"], rows[583]["profit"]) == (6000, 0, -6000)
    assert rows[410]["yosoka_name"] == "倉本匠馬"
    assert rows[410]["goods_id"] == "b1824998_410"
    assert rows[410]["published_at"] == "2026-10-13 15:17:00"


def test_parse_unsettled_is_null_not_zero():
    """🔴 払戻欄がまだ無い商品は None（＝未確定）。0 にすると外れに化ける。"""
    got = NR.parse(RK2, UNSETTLED)
    assert got["n_declared"] == 2
    assert [g["payout"] for g in got["goods"]] == [None, None]
    assert [g["profit"] for g in got["goods"]] == [None, None]
    assert all(g["bet"] == 10000 for g in got["goods"])


def _clear():
    with get_connection() as c:
        c.execute("DELETE FROM netkeirin_rival_goods WHERE race_date = ?", (DAY,))
        c.commit()


def test_collect_saves_and_refetches_only_unsettled():
    """確定しきったレースは2回目に取りに行かない。未確定が残るレースは取り直す。"""
    _clear()
    pages = {RK1: SETTLED, RK2: UNSETTLED}
    calls: list[str] = []

    def fetcher(rk: str) -> str:
        calls.append(rk)
        return pages[rk]

    races = [(RK1, DAY), (RK2, DAY)]
    st = NR.collect(races, fetcher=fetcher, interval=0)
    assert st["saved"] == 2 and st["goods"] == 5 and st["unsettled"] == 1
    assert st["mismatch"] == 0 and st["failed"] == 0

    with get_connection() as c:
        n_null = c.execute("SELECT COUNT(*) FROM netkeirin_rival_goods WHERE race_date = ? "
                           "AND payout IS NULL", (DAY,)).fetchone()[0]
    assert n_null == 2

    calls.clear()
    st2 = NR.collect(races, fetcher=fetcher, interval=0)
    assert calls == [RK2]                       # 確定済みの RK1 は飛ばす
    assert st2["skipped"] == 1
    _clear()


def test_collect_counts_mismatch():
    """「N人が予想中」と商品数が合わなければ数える（ページ分割の検知）。"""
    _clear()
    broken = SETTLED.replace("<strong>3</strong>人", "<strong>5</strong>人")
    st = NR.collect([(RK1, DAY)], fetcher=lambda rk: broken, interval=0)
    assert st["mismatch"] == 1
    _clear()


def test_collect_survives_fetch_error():
    _clear()

    def boom(rk: str) -> str:
        raise OSError("timeout")

    st = NR.collect([(RK1, DAY)], fetcher=boom, interval=0)
    assert st["failed"] == 1 and st["saved"] == 0
    _clear()


def test_fetch_retries_non_json_page(tmp_path, monkeypatch):
    """🔴 API は約4回に1回 HTML ページ（JSON でない）を返す。取り直して拾うこと。"""
    monkeypatch.setattr(NR, "RAW_DIR", tmp_path)
    answers = [ValueError("Expecting value"), ValueError("Expecting value"), SETTLED]
    waits: list[float] = []

    def once(rk: str) -> str:
        a = answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a

    assert NR.fetch(RK1, _once=once, _sleep=waits.append) == SETTLED
    assert waits == [2.0, 4.0]
    assert (tmp_path / RK1[:8] / f"{RK1}.html.gz").exists()


def test_fetch_gives_up_after_retries(tmp_path, monkeypatch):
    monkeypatch.setattr(NR, "RAW_DIR", tmp_path)

    def once(rk: str) -> str:
        raise ValueError("Expecting value")

    import pytest
    with pytest.raises(ValueError):
        NR.fetch(RK1, retries=2, _once=once, _sleep=lambda s: None)
