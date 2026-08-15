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
    """🔴 日付の無い全件操作は絶対に通さない（過去分まで巻き込む）。

    2026-08-12 に取消の場単位・全件を、2026-08-16 に承認の全件を解禁した
    （元は「まとめて操作する事故を避ける」ため承認は場単位までだった）。
    事故防止は画面の確認ダイアログと件数表示へ移したが、
    **範囲の縛りだけはコード側に残す**。
    """
    src = inspect.getsource(cli.main)
    assert '"--all には --date が必要です"' in src


def test_venue_query_filters_by_proposed_status():
    """場単位・全件の対象が入稿案だけであること（送信済みを再送しない）。

    🔴 `submitted` を混ぜると二重入稿になる。
    """
    tree = ast.parse(inspect.getsource(cli._proposals_for_venue))
    sql = " ".join(n.value for n in ast.walk(tree)
                   if isinstance(n, ast.Constant) and isinstance(n.value, str))
    assert "status = ?" in sql and "venue_name = ?" in sql
    assert "race_no" in sql, "発走順に並べていません"


def test_approve_all_venues_covers_the_whole_day(monkeypatch):
    """🔴 `--all` の承認が場を絞らないこと（2026-08-16 追加）。

    venue_name を渡すと1場しか承認されない。全件承認のつもりで
    1場だけ入稿される事故を防ぐ。
    """
    seen = {}

    class _Conn:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, sql, params):
            seen["sql"] = sql
            seen["params"] = list(params)
            return type("R", (), {"fetchall": staticmethod(lambda: [])})()

    monkeypatch.setattr(cli, "get_connection", lambda: _Conn())
    cli._proposals_for_venue("2026-08-16", None)
    assert "venue_name = ?" not in seen["sql"], "全件なのに場で絞っています"
    assert seen["params"] == ["20260816%", cli.STATUS_PROPOSED]

    cli._proposals_for_venue("2026-08-16", "前橋")
    assert "venue_name = ?" in seen["sql"], "場指定が効いていません"
    assert seen["params"] == ["20260816%", cli.STATUS_PROPOSED, "前橋"]


def test_approve_all_is_accepted_by_cli():
    """承認でも `--all` が通ること（取消専用に戻していないこと）。"""
    src = inspect.getsource(cli.main)
    assert "--all は cancel 専用です" not in src, (
        "承認の --all を塞ぐガードが復活しています")


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


def test_webhook_passes_all_venues_for_both_actions():
    """🔴 webhook が `all_venues` を承認にも渡すこと（2026-08-16）。

    元は `action == "cancel"` のときだけ `--all` を付けていた。承認で
    `all_venues` を送っても黙って**場単位として扱われる**（venue が無いので
    「date+venue_name が必要」で 400）ため、画面のボタンが無反応に見える。
    """
    src = WEBHOOK.read_text(encoding="utf-8")
    assert 'if body.get("all_venues"):' in src, (
        "all_venues の判定に action の条件が残っています（承認で --all が付きません）")
    assert 'action == "cancel" and body.get("all_venues")' not in src


def test_webhook_manual_ranks_match_the_submitter():
    """🔴 webhook の `_MANUAL_ALLOWED_RANKS` が submit 側と一致すること。

    ランク集合のコピーは3箇所（submit / kiseki backend `_MANUAL_RANK_KEYS` /
    webhook）。webhook だけ取り残されると **Web のランク選択から選んだのに 400**
    になる（画面には「入稿に失敗しました」しか出ない）。

    実害: 2026-08-14 の 9A→9C・7A廃止に追随しておらず、2026-08-16 まで
    **9C を選ぶと必ず 400** だった。2026-08-02 にも同じ場所で同型の取り残しがある。
    """
    import re

    from scripts.netkeirin_submit_wt import MANUAL_ALLOWED_RANKS

    src = WEBHOOK.read_text(encoding="utf-8")
    m = re.search(r"_MANUAL_ALLOWED_RANKS = \(([^)]*)\)", src)
    assert m, "webhook の _MANUAL_ALLOWED_RANKS 宣言を見つけられません"
    webhook_ranks = tuple(re.findall(r'"([^"]+)"', m.group(1)))
    assert webhook_ranks == MANUAL_ALLOWED_RANKS, (
        "webhook と submit でランク集合が食い違います。\n"
        f"  webhook: {webhook_ranks}\n  submit : {MANUAL_ALLOWED_RANKS}")


def test_webhook_routes_publish():
    """公開の口が生えていること（生えていないと画面のボタンが 404 になる）。"""
    src = WEBHOOK.read_text(encoding="utf-8")
    assert '"/approve", "/cancel", "/publish"' in src
    assert '"/publish-wait"' in src


def test_publish_flag_is_approve_only():
    """`--publish` は承認専用（単体の公開は publish アクションを使う）。"""
    src = inspect.getsource(cli.main)
    assert "--publish は approve 専用です" in src


def test_publish_targets_are_submitted_only():
    """🔴 公開できるのは netkeirin へ送信済み（submitted）だけ。

    入稿案（proposed）は netkeirin にまだ無いので race_id が引けない。
    """
    tree = ast.parse(inspect.getsource(cli._publishable))
    sql = " ".join(n.value for n in ast.walk(tree)
                   if isinstance(n, ast.Constant) and isinstance(n.value, str))
    assert "status = ?" in sql
    assert "race_key LIKE ?" in sql, "日付で絞っていません"


def test_cancel_excludes_published():
    """🔴 公開済みは取消の対象に入れない。

    公開済みに netkeirin の `delete` が効くかは仕様に記載が無く未確認で、
    含めると一括取消のたびに必ず失敗する行が混ざり明細が読めなくなる。
    """
    tree = ast.parse(inspect.getsource(cli._cancelable))
    sql = " ".join(n.value for n in ast.walk(tree)
                   if isinstance(n, ast.Constant) and isinstance(n.value, str))
    assert "NOT IN (?, ?)" in sql, "deleted と published の2つを除外していません"
