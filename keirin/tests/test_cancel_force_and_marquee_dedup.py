"""取消の一貫性まわりの検査（2026-08-11）。

実運用で詰まった2件を固定する。

## ① 自動穴埋めの重複判定が status を見ていなかった

`submit_marquee_wt.py` は「既に入稿済みのレース」を除外するが、その集合を
**status を見ずに**作っていた。取消は論理削除なので行が残る。結果、
**一度取り消したレースを自動穴埋めで出し直せない**（毎回その場で手作業になる）。
`netkeirin_submit_wt.py::_already_submitted` は正しく除外しており、
**2箇所で判定が食い違っていた**のが問題の本体。

## ② netkeirin 側を先に消すと記録が置き去りになる

`cancel_submission` は item_id を引き直してから削除する。netkeirin 側で先に
消していると引けず、そこで return してしまい **DB も更新されない**。
取消したはずの行が生きているので ① の重複判定にも引っかかる。
`force=True` で「netkeirin は触らず記録だけ実態へ合わせる」経路を用意した。
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import scripts.netkeirin_submit_wt as m

ROOT = Path(__file__).resolve().parent.parent
MARQUEE = ROOT / "scripts" / "submit_marquee_wt.py"


# ---------------------------------------------------------------------------
# ① 重複判定
# ---------------------------------------------------------------------------

def _sql_literals(path: Path, needle: str) -> list[str]:
    """ファイル中の文字列リテラルのうち needle を含むものを返す（AST・docstring混入なし）。

    ⚠️ grep だとコメントや docstring を拾って**偽陽性で通る**。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and needle in n.value:
            # docstring は Expr の直下に現れる。SQL は Call の引数なので除ける。
            out.append(n.value)
    return out


def test_穴埋めの重複判定が取消済みを除外する():
    """🔴 これが無いと、取り消したレースを自動穴埋めで出し直せない。"""
    sqls = _sql_literals(MARQUEE, "FROM netkeirin_submissions")
    assert sqls, "submit_marquee_wt.py が netkeirin_submissions を読む SQL を見つけられません"
    joined = " ".join(sqls)
    assert "deleted" in joined, (
        "自動穴埋めの重複判定が status を見ていません。取消は論理削除なので行が残り、"
        "除外しないと取り消したレースを出し直せません"
    )


def test_重複判定の条件が入稿側と揃っている():
    """🔴 2箇所で条件が食い違っていたのが今回の原因。同じ式であることを縛る。"""
    expected = "COALESCE(status, 'submitted') <> 'deleted'"
    marquee = " ".join(_sql_literals(MARQUEE, "netkeirin_submissions"))
    submit = " ".join(
        n.value for n in ast.walk(ast.parse(inspect.getsource(m._already_submitted)))
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    )
    assert expected in marquee, f"submit_marquee_wt.py の条件が {expected} ではありません"
    assert expected in submit, f"_already_submitted の条件が {expected} ではありません"


# ---------------------------------------------------------------------------
# ② 強制取消
# ---------------------------------------------------------------------------

class _Conn:
    """cancel_submission が使う最小の接続スタブ。"""

    def __init__(self, row):
        self.row = row
        self.executed: list[tuple] = []

    def execute(self, sql, params=()):
        self.executed.append((sql, params))
        row = self.row

        class _C:
            @staticmethod
            def fetchone():
                return row if "SELECT" in sql else None
        return _C()

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _ClientNotFound:
    """netkeirin 側に該当が無い状態（先に消されている）。"""

    def __init__(self, *a, **k):
        pass

    def fetch_item_ids(self):
        return {}

    def delete_pick(self, item_id):  # pragma: no cover - 呼ばれてはいけない
        raise AssertionError("見つかっていないのに削除を呼びました")


def _setup(monkeypatch, status):
    conn = _Conn({"netkeirin_race_id": "202608114804", "status": status})
    monkeypatch.setattr(m, "NetkeirinClient", _ClientNotFound)
    monkeypatch.setattr(m, "get_connection", lambda: conn)
    return conn


def test_通常の取消はnetkeirinに無ければ失敗しDBも触らない(monkeypatch):
    """従来どおりの安全側。記録だけ勝手に消さない。"""
    conn = _setup(monkeypatch, m.STATUS_SUBMITTED)
    ok, msg = m.cancel_submission("20260811_48_04", "7A")
    assert ok is False
    assert "見つかりません" in msg
    assert not any("UPDATE" in s for s, _ in conn.executed), (
        "netkeirin で消せていないのに記録を取消にしています"
    )


def test_通常の取消の失敗メッセージが強制取消へ誘導する(monkeypatch):
    """画面はこの文言を見て強制取消のボタンを出す。文言を変えると導線が消える。"""
    _setup(monkeypatch, m.STATUS_SUBMITTED)
    _, msg = m.cancel_submission("20260811_48_04", "7A")
    assert "強制取消" in msg


def test_強制取消はnetkeirinを触らずDBだけ更新する(monkeypatch):
    """🔴 netkeirin 側で先に消したときに記録を実態へ合わせるための経路。"""
    conn = _setup(monkeypatch, m.STATUS_SUBMITTED)
    ok, msg = m.cancel_submission("20260811_48_04", "7A", force=True)
    assert ok is True
    assert "強制取消" in msg
    updates = [s for s, _ in conn.executed if "UPDATE" in s]
    assert len(updates) == 1
    assert "deleted_at" in updates[0]
    assert "DELETE FROM" not in updates[0].upper(), "物理削除してはいけません"


def test_強制取消でも未送信なら従来どおり(monkeypatch):
    """proposed は netkeirin へ出ていないので force の有無に関係なく素通し。"""
    conn = _setup(monkeypatch, m.STATUS_PROPOSED)
    ok, _ = m.cancel_submission("20260811_48_04", "7A", force=True)
    assert ok is True
    assert any("UPDATE" in s for s, _ in conn.executed)


def test_既に取消済みならforceでも何もしない(monkeypatch):
    conn = _setup(monkeypatch, m.STATUS_DELETED)
    ok, msg = m.cancel_submission("20260811_48_04", "7A", force=True)
    assert ok is True
    assert "既に取消済み" in msg
    assert not any("UPDATE" in s for s, _ in conn.executed)


# ---------------------------------------------------------------------------
# ③ 場単位・全件の取消（2026-08-12 解禁）
# ---------------------------------------------------------------------------

class _CaptureConn:
    """実行された SQL とパラメータを捕まえるだけの接続スタブ。"""

    def __init__(self):
        self.sql = ""
        self.params: list = []

    def execute(self, sql, params=()):
        self.sql, self.params = sql, list(params)

        class _C:
            @staticmethod
            def fetchall():
                return []
        return _C()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _capture_cancelable(monkeypatch, venue):
    import scripts.netkeirin_approve_wt as cli
    conn = _CaptureConn()
    monkeypatch.setattr(cli, "get_connection", lambda: conn)
    cli._cancelable("2026-08-12", venue)
    return conn


def test_取消対象は生きている下書きだけ(monkeypatch):
    """🔴 取消済み（deleted）を含めると「N件取消しました」の N が実態より多く出る。
    論理削除なので行は残っている。"""
    conn = _capture_cancelable(monkeypatch, "平塚")
    assert "COALESCE(status, 'submitted') <> ?" in conn.sql, "取消済みを除外していません"
    assert "deleted" in conn.params


def test_全件取消でも日付で必ず絞る(monkeypatch):
    """🔴 venue を省いた（＝全場）ときも日付の条件が残ること。
    ここが外れると**全期間の下書きが対象**になる。"""
    conn = _capture_cancelable(monkeypatch, None)
    assert "race_key LIKE ?" in conn.sql, "日付で絞っていません（過去分まで巻き込みます）"
    assert "20260812%" in conn.params
    # ⚠️ 単なる部分一致にしない。`ORDER BY venue_name` にも当たってしまう
    #    （実際にこれで一度誤検知した）。**WHERE 句の形**で見る。
    assert "venue_name = ?" not in conn.sql, "全場のはずが場で絞っています"


def test_場単位では場でも絞る(monkeypatch):
    conn = _capture_cancelable(monkeypatch, "平塚")
    assert "venue_name = ?" in conn.sql
    assert "平塚" in conn.params
    assert "20260812%" in conn.params, "場を指定しても日付の絞り込みは必要です"


def test_一括取消はforceを既定で渡さない():
    """🔴 まとめて『記録だけ消す』のは事故。失敗は明細で返し、
    強制取消は画面から1件ずつ行う。"""
    import scripts.netkeirin_approve_wt as cli
    tree = ast.parse(inspect.getsource(cli.main))
    consts = {n.value for n in ast.walk(tree)
              if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    assert "--force は cancel 専用です" in consts
    assert "--all は cancel 専用です" in consts
    assert "--all には --date が必要です" in consts
