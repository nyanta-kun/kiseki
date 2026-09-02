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
                        lambda rk, rank, force=False, reason=None:
                            (seen.append((rk, force)), (True, "deleted"))[1])
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
                        lambda rk, rank, force=False, reason=None:
                            (seen.append((rk, force)), (True, "forced"))[1])
    out = cli._run("cancel", [("20260811_13_01", "7C")], force=True)
    assert out["ok"] is True and seen == [("20260811_13_01", True)]


def test_cancel_passes_reason_through(monkeypatch):
    """取消の理由が CLI から DB まで素通しされること（2026-08-25）。

    🔴 ここが落ちると一覧の「取消」バッジが理由なしになり、
       **売っていないことは分かっても「なぜ」が画面から消える**。
    """
    seen: list[str | None] = []
    monkeypatch.setattr(cli, "cancel_submission",
                        lambda rk, rank, force=False, reason=None:
                            (seen.append(reason), (True, "deleted"))[1])
    out = cli._run("cancel", [("20260811_13_01", "7C")], reason="平均払戻が安い")
    assert out["ok"] is True and seen == ["平均払戻が安い"]


def test_force_is_cancel_only():
    """--force は承認では受け付けないこと（承認に「強制」は無い）。"""
    src = inspect.getsource(cli.main)
    assert "--force は cancel 専用です" in src


def test_reason_is_cancel_only():
    """--reason も取消専用（承認・公開に「取り消した理由」は無い）。"""
    src = inspect.getsource(cli.main)
    assert "--reason は cancel 専用です" in src


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


def test_webhook_submit_race_uses_type_lab():
    """🔴🔴 **手動入稿は型ラボのスクリプトを叩くこと**（2026-09-03〜）。

    それまでは `netkeirin_submit_wt.py --manual-rank-key 7S/9C --axis1 --axis2` を
    起動していた。型ラボへ全面移行した後もここだけ旧ランク経路で、

      - 買い目が旧ランクのロジックで作られる（型の判定を通らない）
      - `type_lab_picks` に行が残らない → 採点・成績集計から漏れる
      - `bet_detail` の書式は共通なので**入稿自体は通り、気づけない**

    という状態だった。ここが戻ると同じことが再発するので構文で固定する。
    """
    import ast

    tree = ast.parse(WEBHOOK.read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_handle_submit_race")
    # 🔴 **docstring を外してから見る。** 「なぜ移したか」の説明に旧スクリプト名が
    #    出てくるので、素の文字列検索だと自分の注記で落ちる（2026-09-03 に実際に踏んだ）。
    stmts = fn.body[1:] if (fn.body and isinstance(fn.body[0], ast.Expr)
                            and isinstance(fn.body[0].value, ast.Constant)) else fn.body
    body = "\n".join(ast.unparse(x) for x in stmts)
    assert "netkeirin_submit_type_lab.py" in body, "型ラボのスクリプトを叩いていません"
    assert "netkeirin_submit_wt.py" not in body, "旧ランクのスクリプトが残っています"
    assert "--manual-rank-key" not in body, "旧ランクの手動入稿フラグが残っています"
    # 型ラボは3波。noon を弾くと昼の手動入稿ができない
    # ⚠️ `ast.unparse` は引用符を正規化する（"noon" → 'noon'）ので引用符を含めない
    assert "noon" in body, "session に noon が入っていません"
    # ランク・軸が来たら 400（黙って無視しない）
    assert "rank_key" in body and "400" in body, \
        "rank_key/axis1/axis2 を拒否する分岐がありません"


def test_webhook_routes_publish():
    """公開の口が生えていること（生えていないと画面のボタンが 404 になる）。"""
    src = WEBHOOK.read_text(encoding="utf-8")
    assert '"/approve", "/cancel", "/publish"' in src
    assert '"/publish-wait"' in src


def test_webhook_routes_publish_sync():
    """状態合わせの口が生えていること（2026-08-19）。

    🔴 **date 必須**であることも固定する。日付が無いと過去分の `submitted` まで
       まとめて `published` にしてしまう（承認・取消と同じ作法）。
    """
    src = WEBHOOK.read_text(encoding="utf-8")
    assert '"/publish-sync"' in src
    tree = ast.parse(src)
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "_handle_publish_sync"), None)
    assert fn is not None, "_handle_publish_sync がありません"
    body = ast.dump(fn)
    assert "_DATE_RE" in body, "date を検証していません"
    assert "netkeirin_sync_status.py" in ast.dump(fn), "同期スクリプトを呼んでいません"


def test_publish_flag_is_approve_only():
    """`--publish` は承認専用（単体の公開は publish アクションを使う）。"""
    src = inspect.getsource(cli.main)
    assert "--publish は approve 専用です" in src


def test_publish_targets_cover_proposed_and_submitted():
    """🔴 「公開」は**未入稿も対象**にする（2026-08-16・ユーザー指定）。

    画面の操作を 入稿 / 取消 / 公開 の3つに畳むため、「公開」を押したときに
    入稿済かどうかを人が意識しなくてよいようにする。未入稿は `_run()` が
    先に入稿してから公開する（入稿に失敗したものは公開しない）。
    """
    tree = ast.parse(inspect.getsource(cli._publishable))
    sql = " ".join(n.value for n in ast.walk(tree)
                   if isinstance(n, ast.Constant) and isinstance(n.value, str))
    assert "status IN (?, ?)" in sql, "未入稿と公開待ちの両方を対象にしていません"
    assert "race_key LIKE ?" in sql, "日付で絞っていません"


def test_publish_approves_before_publishing():
    """🔴 未入稿を混ぜたまま `publish_submissions` へ渡さないこと。

    渡すと「公開できる状態ではありません（proposed）」で必ず失敗する。
    """
    src = inspect.getsource(cli._run)
    assert '_run("approve"' in src and "publish=True" in src, (
        "未入稿を先に入稿する経路がありません")


def test_cancel_excludes_published():
    """🔴 公開済みは取消の対象に入れない。

    公開済みに netkeirin の `delete` が効くかは仕様に記載が無く未確認で、
    含めると一括取消のたびに必ず失敗する行が混ざり明細が読めなくなる。
    """
    tree = ast.parse(inspect.getsource(cli._cancelable))
    sql = " ".join(n.value for n in ast.walk(tree)
                   if isinstance(n, ast.Constant) and isinstance(n.value, str))
    assert "NOT IN (?, ?)" in sql, "deleted と published の2つを除外していません"


# ---------------------------------------------------------------------------
# 失敗の理由が画面まで届くこと（2026-08-16 の実障害）
# ---------------------------------------------------------------------------
#
# 🔴 `_summarize` に `message` が無かったため、Web の Server Action が
#    `json.message ?? "実行しました"` で埋め、**失敗しているのに
#    「成功0件 / 失敗1件: 実行しました」**という自己矛盾した表示になっていた。
#    理由は `results[]` にあるのに画面のどこにも出ず、「公開ボタンが無反応」に
#    見えた（京王閣12R・netkeirin 側に公開待ちが無く公開に失敗していた）。

def test_summarize_always_has_message():
    """空・全成功・失敗のどれでも `message` を返す（Web の既定文言に負けない）。"""
    for results in ([],
                    [{"race_key": "a", "rank_key": "7S", "ok": True, "message": "ok"}],
                    [{"race_key": "a", "rank_key": "7S", "ok": False, "message": "ng"}]):
        assert cli._summarize(results)["message"]


def test_summarize_message_carries_failure_reason():
    out = cli._summarize([
        {"race_key": "20260816_27_12", "rank_key": "7S", "ok": False,
         "message": "公開失敗: {'status': 'NG'}"},
    ])
    assert out["ok"] is False and out["n_ng"] == 1
    assert "公開失敗" in out["message"]


def test_summarize_folds_duplicate_reasons():
    """同じ理由が並んだときに1行へ畳む（一括操作でメッセージが溢れないため）。"""
    out = cli._summarize([
        {"race_key": f"20260816_27_{i:02d}", "rank_key": "7S", "ok": False,
         "message": "発走15分前を過ぎているため操作できません"} for i in (1, 2, 3)
    ])
    assert out["message"] == "発走15分前を過ぎているため操作できません（3件）"


def test_summarize_success_message_is_not_a_failure_reason():
    out = cli._summarize([
        {"race_key": "a", "rank_key": "7S", "ok": True, "message": "1件を公開しました"},
    ])
    assert out["ok"] is True and "件" in out["message"]
