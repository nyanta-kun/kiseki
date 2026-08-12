"""承認制の配線を固定する（2026-08-11）。

## なぜ静的検査なのか

承認制の分岐が通るのは **本番の入稿バッチだけ**で、CI では実行されない。
実際、実装中に `propose_only` を `_process_rank` で定義して `main` で参照する
状態を作ってしまい、**本番でだけ NameError になる**ところだった
（[[keirin_wave_picks_unbound_lg_2026_08_07]]「本番でしか実行されない関数は
CIで守れない」と同型）。そこで AST で構造を検査する。

## 守ること

1. `propose_only` が使う関数すべてで**束縛されている**（未定義参照が無い）
2. 承認制の判定は **main で1回だけ**。`_process_rank` の中で引き直さない
3. `_approval_required()` は **fail-open**（例外時は False＝従来の自動入稿）
4. `_already_submitted()` が取消済みを除外する
5. `_record_submission()` が status を**戻り値の接頭辞から導出**する
   （呼び出し側に status を持たせると送信分岐3か所のどれかで渡し忘れる）
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

import scripts.netkeirin_submit_wt as m

SRC = Path(m.__file__).read_text(encoding="utf-8")
TREE = ast.parse(SRC)


def _func(name: str) -> ast.FunctionDef:
    for node in TREE.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} が見つかりません")


def _bound_names(fn: ast.FunctionDef) -> set[str]:
    names = {a.arg for a in fn.args.args} | {a.arg for a in fn.args.kwonlyargs}
    for node in ast.walk(fn):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
    return names


@pytest.mark.parametrize("fname", ["main", "_process_rank"])
def test_propose_only_is_bound_where_used(fname):
    """propose_only を参照する関数で必ず束縛されていること。

    これを外すと本番の入稿バッチだけが NameError で落ちる。
    """
    fn = _func(fname)
    used = any(isinstance(n, ast.Name) and n.id == "propose_only"
               and isinstance(n.ctx, ast.Load) for n in ast.walk(fn))
    if not used:
        pytest.skip(f"{fname} は propose_only を参照していない")
    assert "propose_only" in _bound_names(fn), (
        f"{fname} が propose_only を未定義のまま参照しています"
        "（本番の入稿バッチだけが落ちます）"
    )


def test_approval_decided_once_in_main_not_per_rank():
    """`_approval_required()` の呼び出しが `_process_rank` の中に無いこと。

    ランクごとに引き直すと、波の途中で設定が変わったときに
    「送ったもの」と「案のまま」が同じ波に混ざる。
    """
    fn = _func("_process_rank")
    calls = [n for n in ast.walk(fn)
             if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "_approval_required"]
    assert not calls, "_process_rank が承認制を引き直しています（main で1回だけにすること）"

    main_calls = [n for n in ast.walk(_func("main"))
                  if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "_approval_required"]
    assert len(main_calls) == 1, f"main での判定は1回であるべき: {len(main_calls)}回"


def test_approval_required_is_fail_open():
    """例外時に False（＝従来どおり自動入稿）を返すこと。

    承認制に倒すと入稿が全部止まったまま誰も気づかない。承認制は運用者が
    明示的にONにするもので、事故で有効になってはいけない。
    """
    fn = _func("_approval_required")
    handlers = [n for n in ast.walk(fn) if isinstance(n, ast.ExceptHandler)]
    assert handlers, "_approval_required に例外処理がありません"
    returns = [n for h in handlers for n in ast.walk(h) if isinstance(n, ast.Return)]
    assert returns, "except 節で return していません"
    for r in returns:
        assert isinstance(r.value, ast.Constant) and r.value.value is False, (
            "例外時に False（自動入稿）を返していません"
        )


def _sql_literals(fn_name: str) -> str:
    """関数内の文字列リテラルだけを連結して返す。

    ⚠️ `inspect.getsource` を素で grep すると**コメントや docstring に書いた
       説明文が引っかかる**。実際この検査は当初 `"deleted" in src` としており、
       SQL の除外句を消す変異を**素通しした**（偽陽性）。
       [[keirin_code_review_2026_08_08]]「安全網のテストが偽陽性で通っていた」と同型。
    """
    parts: list[str] = []
    for node in ast.walk(_func(fn_name)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            parts.append(node.value)
        elif isinstance(node, ast.JoinedStr):
            for v in node.values:
                if isinstance(v, ast.Constant) and isinstance(v.value, str):
                    parts.append(v.value)
    # docstring は除く（関数の先頭の文字列）
    fn = _func(fn_name)
    doc = ast.get_docstring(fn)
    if doc:
        parts = [p for p in parts if p != doc]
    return " ".join(parts)


def test_already_submitted_counts_deleted_as_handled():
    """🔴 **2026-08-13 に仕様を反転**（ユーザー判断）。

    取消した行も「その日は処理済み」に数える。race_key は日付を含むので
    ここに出る deleted 行は必ず**同じ日に人が取り消したもの**であり、
    朝の波で落とした商品を昼・夕の波が復活させると確認の意味が消える。

    取り消したレースを出し直したいときは**手動入稿**を使う
    （`--manual-rank-key`。あちらはこの判定を通らない）。

    コメントではなく **実際に発行する SQL** を見る。
    """
    sql = _sql_literals("_already_submitted")
    assert "deleted" not in sql, (
        "_already_submitted の SQL が取消済みを除外しています"
        "（取り消したレースが次の波で復活します）"
    )
    assert "status" not in sql, (
        "status で絞っています。取消済みも処理済みとして扱う仕様です")


def test_record_submission_derives_status_from_prefix():
    """status を引数で受け取らず、戻り値の接頭辞から導出すること。

    呼び出し側（送信分岐3か所）に status を持たせると、どれかで渡し忘れて
    「netkeirin に出ていないのに submitted」という行ができる。
    """
    fn = _func("_record_submission")
    arg_names = {a.arg for a in fn.args.args}
    assert "status" not in arg_names, (
        "_record_submission が status を引数で受け取っています"
        "（送信分岐のどれかで渡し忘れます）"
    )
    src = inspect.getsource(m._record_submission)
    assert "PROPOSED_PREFIX" in src and "startswith" in src


def test_record_submission_persists_title_and_comment():
    """確認画面が表示・編集するので文面を保存すること。"""
    fn = _func("_record_submission")
    arg_names = {a.arg for a in fn.args.args}
    assert {"title", "comment"} <= arg_names
    src = inspect.getsource(m._record_submission)
    assert "title" in src and "comment" in src


def test_status_constants_match_migration():
    """status の値がマイグレーションと一致すること（手書き二重管理の事故防止）。"""
    mig = (Path(m.__file__).resolve().parents[2] / "backend" / "alembic" / "versions"
           / "202608110900_keirin_submission_approval.py")
    if not mig.exists():
        pytest.skip("マイグレーションが見つからない（keirin 単独 clone）")
    text = mig.read_text(encoding="utf-8")
    for value in (m.STATUS_PROPOSED, m.STATUS_SUBMITTED, m.STATUS_DELETED):
        assert f'"{value}"' in text, f"status '{value}' がマイグレーションにありません"


def test_review_url_is_configurable():
    """確認画面のURLは環境変数で差し替えられること（本番以外で使うため）。"""
    src = inspect.getsource(m)
    assert "KEIRIN_REVIEW_URL" in src
    assert m.REVIEW_URL.startswith("http")
