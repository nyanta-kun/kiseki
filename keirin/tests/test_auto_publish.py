"""自動公開（入稿と同時に netkeirin で公開まで行う）の挙動を固定する（2026-08-29）。

## 守ること

1. 自動公開の判定は**承認制と同じ1つのスイッチ**（`_global.require_approval` の裏返し）
2. 読めなかったときは **公開しない側へ倒す**（fail-closed）。承認制は fail-open なので
   向きが逆になる。**揃えてはいけない**——公開は不可逆で、事故が戻せない
3. 公開するのは**この実行で netkeirin へ送ったものだけ**。日付で拾い直すと、
   人が意図して公開待ちに残した過去の下書きまで公開してしまう
4. 入稿案（proposed）は netkeirin に無いので対象に入らない
5. 公開に失敗しても入稿は成功のまま（例外を上げない）
"""
from __future__ import annotations

import pytest

import scripts.netkeirin_submit_wt as m


class _FakeConn:
    def __init__(self, row):
        self._row = row

    def execute(self, *_a, **_k):
        return self

    def fetchone(self):
        return self._row

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


def _patch_conn(monkeypatch, row=None, raises=False):
    def _get():
        if raises:
            raise RuntimeError("DB 落ちた")
        return _FakeConn(row)
    monkeypatch.setattr(m, "get_connection", _get)


@pytest.fixture(autouse=True)
def _clean_run_state():
    """実行内の送信済みリストはテストごとに空から始める。"""
    m._submitted_this_run.clear()
    yield
    m._submitted_this_run.clear()


# ---------------------------------------------------------------------------
# ① 自動公開の判定は承認制の裏返し
# ---------------------------------------------------------------------------

def test_承認制OFFなら自動公開ON(monkeypatch):
    _patch_conn(monkeypatch, row={"require_approval": 0})
    assert m._auto_publish_enabled() is True
    assert m._approval_required() is False


def test_承認制ONなら自動公開OFF(monkeypatch):
    _patch_conn(monkeypatch, row={"require_approval": 1})
    assert m._auto_publish_enabled() is False
    assert m._approval_required() is True


def test_行が無いときは公開しない(monkeypatch):
    """🔴 承認制は fail-open（False＝入稿する）だが、自動公開は fail-closed。

    同じ列を見ているのに既定が逆なのは意図的。**揃えてはいけない**。
    """
    _patch_conn(monkeypatch, row=None)
    assert m._auto_publish_enabled() is False
    assert m._approval_required() is False


def test_DBが読めないときは公開しない(monkeypatch):
    _patch_conn(monkeypatch, raises=True)
    assert m._auto_publish_enabled() is False
    # 承認制側は従来どおり fail-open（入稿は止めない）
    assert m._approval_required() is False


# ---------------------------------------------------------------------------
# ② 公開の対象は「この実行で送ったもの」だけ
# ---------------------------------------------------------------------------

def test_送ったものだけをまとめて公開する(monkeypatch):
    _patch_conn(monkeypatch, row={"require_approval": 0})
    called: list[list[tuple[str, str]]] = []

    def _publish(targets):
        called.append(list(targets))
        return [{"race_key": rk, "rank_key": rn, "ok": True, "message": "ok"}
                for rk, rn in targets]

    monkeypatch.setattr(m, "publish_submissions", _publish)
    m._submitted_this_run.extend([
        ("20260829_13_01", "A_hit"),
        ("20260829_13_02", "C_hit"),
        ("20260829_13_01", "A_hit"),      # 重複は1回だけ送る
    ])
    results = m.auto_publish_submitted()
    assert called == [[("20260829_13_01", "A_hit"), ("20260829_13_02", "C_hit")]]
    assert len(results) == 2
    # 一度公開したら次の呼び出しで二重に送らない
    assert m._submitted_this_run == []
    assert m.auto_publish_submitted() == []


def test_承認制のときは公開しない(monkeypatch):
    _patch_conn(monkeypatch, row={"require_approval": 1})
    monkeypatch.setattr(m, "publish_submissions",
                        lambda _t: pytest.fail("承認制なのに公開した"))
    m._submitted_this_run.append(("20260829_13_01", "A_hit"))
    assert m.auto_publish_submitted() == []


def test_dry_runでは公開しない(monkeypatch):
    _patch_conn(monkeypatch, row={"require_approval": 0})
    monkeypatch.setattr(m, "publish_submissions",
                        lambda _t: pytest.fail("dry-run なのに公開した"))
    m._submitted_this_run.append(("20260829_13_01", "A_hit"))
    assert m.auto_publish_submitted(dry_run=True) == []


def test_公開に失敗しても例外を上げない(monkeypatch):
    """🔴 ここへ来た時点で入稿は終わっている。例外を上げると呼び出し側が
    再実行を考え、二重入稿を招く。"""
    _patch_conn(monkeypatch, row={"require_approval": 0})

    def _boom(_targets):
        raise RuntimeError("netkeirin が落ちた")

    monkeypatch.setattr(m, "publish_submissions", _boom)
    m._submitted_this_run.append(("20260829_13_01", "A_hit"))
    assert m.auto_publish_submitted() == []


# ---------------------------------------------------------------------------
# ③ 入稿案は公開対象に入らない
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("race_id", "expected"),
    # 送信済み（netkeirin の race_id が付いている） → 公開対象
    [("202608291301", [("20260829_13_01", "A_hit")]),
     # 入稿案（PROPOSED_PREFIX 付き） → 公開対象に入らない
     (m.PROPOSED_PREFIX + "202608291301", [])],
)
def test_入稿案は公開対象に入らない(monkeypatch, race_id, expected):
    """`_record_submission` が status='submitted' のときだけ積むこと。

    🔴 入稿案（proposed）は netkeirin にまだ無いので `netkeirin_race_id` が無く、
       公開できない。ここへ混ぜると公開のたびに必ず失敗する行が並ぶ。
    """
    _patch_conn(monkeypatch, row=None)
    monkeypatch.setattr(m, "_mark_bought", lambda *_a, **_k: None)
    m._record_submission("20260829_13_01", "A_hit", "morning", "松戸", 1,
                         None, 1, 2, race_id)
    assert m._submitted_this_run == expected
