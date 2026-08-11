"""承認CLIと webhook の口を固定する（2026-08-11）。

## 守ること

1. 承認・取消は **同期実行**で結果を返す（背景起動だと「開始しました」しか返せず、
   確認画面が承認の成否を見せられない）
2. 場単位でも **1件ずつの結果**を返す（どれが失敗したか画面で示せないと困る）
3. 途中で失敗しても**残りは続行**する（1件の失敗で場全体が止まらない）
4. 取消は場単位を受け付けない（まとめて消す事故を避ける）
5. CLI は JSON を**標準出力の最終行**に出す（webhook が拾えること）
"""
from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path

import pytest

import scripts.netkeirin_approve_wt as cli

ROOT = Path(__file__).resolve().parent.parent
WEBHOOK = ROOT / "scripts" / "keirin_webhook.py"


def test_run_returns_per_target_results(monkeypatch):
    monkeypatch.setattr(cli, "approve_and_submit",
                        lambda rk, rank: (True, f"ok-{rk}-{rank}"))
    out = cli._run("approve", [("20260811_13_01", "7C"), ("20260811_13_02", "7A")])
    assert out["ok"] is True and out["n_ok"] == 2 and out["n_ng"] == 0
    assert [r["race_key"] for r in out["results"]] == ["20260811_13_01", "20260811_13_02"]


def test_run_continues_after_failure(monkeypatch):
    """1件失敗しても残りを続ける。まとめ承認が最初の1件で止まると使えない。"""
    def _fn(rk, rank):
        if rk.endswith("_01"):
            raise RuntimeError("boom")
        return True, "ok"

    monkeypatch.setattr(cli, "approve_and_submit", _fn)
    out = cli._run("approve", [("20260811_13_01", "7C"), ("20260811_13_02", "7A")])
    assert out["ok"] is False and out["n_ok"] == 1 and out["n_ng"] == 1
    assert out["results"][0]["ok"] is False and "boom" in out["results"][0]["message"]
    assert out["results"][1]["ok"] is True, "1件目の失敗で2件目が止まっています"


def test_cancel_uses_cancel_function(monkeypatch):
    seen: list[tuple[str, bool]] = []
    monkeypatch.setattr(cli, "cancel_submission",
                        lambda rk, rank, force=False: (seen.append((rk, force)),
                                                       (True, "deleted"))[1])
    monkeypatch.setattr(cli, "approve_and_submit",
                        lambda rk, rank: pytest.fail("cancel で承認関数が呼ばれました"))
    out = cli._run("cancel", [("20260811_13_01", "7C")])
    assert out["ok"] is True and seen == [("20260811_13_01", False)]


def test_cancel_passes_force_through(monkeypatch):
    """強制取消（netkeirin を触らず記録だけ合わせる）が CLI から素通しされること。

    ここが落ちると、画面の「強制取消」が**ただの取消として実行され**、
    netkeirin に見つからないまま再び失敗して記録が置き去りになる。
    """
    seen: list[tuple[str, bool]] = []
    monkeypatch.setattr(cli, "cancel_submission",
                        lambda rk, rank, force=False: (seen.append((rk, force)),
                                                       (True, "forced"))[1])
    out = cli._run("cancel", [("20260811_13_01", "7C")], force=True)
    assert out["ok"] is True and seen == [("20260811_13_01", True)]


def test_force_is_cancel_only():
    """--force は承認では受け付けないこと（承認に「強制」は無い）。"""
    src = inspect.getsource(cli.main)
    assert "--force は cancel 専用です" in src


def test_all_scope_requires_date():
    """🔴 日付の無い全件取消は絶対に通さない（過去分まで巻き込む）。

    2026-08-12 に取消の場単位・全件を解禁した（元は「まとめて消す事故を避ける」
    ため承認のみだった）。事故防止は画面の二段確認へ移したが、
    **範囲の縛りだけはコード側に残す**。
    """
    src = inspect.getsource(cli.main)
    assert '"--all には --date が必要です"' in src
    assert '"--all は cancel 専用です"' in src


def test_venue_query_filters_by_proposed_status():
    """場単位の対象が入稿案だけであること（送信済みを再送しない）。"""
    tree = ast.parse(inspect.getsource(cli._proposals_for_venue))
    sql = " ".join(n.value for n in ast.walk(tree)
                   if isinstance(n, ast.Constant) and isinstance(n.value, str))
    assert "status = ?" in sql and "venue_name = ?" in sql
    assert "ORDER BY race_no" in sql, "発走順に並べていません"


def test_cli_prints_json_on_last_line(monkeypatch, capsys):
    monkeypatch.setattr(cli, "approve_and_submit", lambda rk, rank: (True, "done"))
    monkeypatch.setattr("sys.argv",
                        ["x", "approve", "--race-key", "20260811_13_01",
                         "--rank-key", "7C"])
    rc = cli.main()
    assert rc == 0
    last = capsys.readouterr().out.strip().splitlines()[-1]
    assert json.loads(last)["ok"] is True


def _calls(fn: ast.FunctionDef) -> set[str]:
    """関数内で実際に呼んでいる名前を集める。

    ⚠️ ソースを素で grep すると **docstring に書いた説明文**が引っかかる。
       この検査は当初 `"_spawn" not in body` としており、
       「`_spawn` にすると…」という解説文で誤検知した。
       [[keirin_code_review_2026_08_08]] と同型なので構造で見る。
    """
    out: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name):
                out.add(f.id)
            elif isinstance(f, ast.Attribute):
                base = getattr(f.value, "id", "")
                out.add(f"{base}.{f.attr}" if base else f.attr)
    return out


def test_webhook_runs_approval_synchronously():
    """webhook が承認・取消を `_spawn`（背景起動）していないこと。"""
    src = WEBHOOK.read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "_handle_approval"), None)
    assert fn is not None, "_handle_approval がありません"
    called = _calls(fn)
    assert "_spawn" not in called, (
        "承認を背景起動しています。結果が返せず確認画面が成否を出せません"
    )
    assert "subprocess.run" in called, "同期実行していません"
    # タイムアウトを必ず付ける（netkeirin が無応答だと webhook が固まる）
    run_calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
                 and getattr(getattr(n.func, "value", None), "id", "") == "subprocess"]
    assert run_calls and any("timeout" in {k.arg for k in c.keywords} for c in run_calls), (
        "subprocess.run に timeout がありません（netkeirin 無応答で webhook が固まります）"
    )


def test_webhook_registers_both_paths():
    src = WEBHOOK.read_text(encoding="utf-8")
    assert '"/approve"' in src and '"/cancel"' in src


def test_webhook_validates_inputs():
    """race_key / date の検証を通していること。"""
    src = WEBHOOK.read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_handle_approval")
    body = ast.get_source_segment(src, fn) or ""
    assert "_RACE_KEY_RE" in body and "_DATE_RE" in body
