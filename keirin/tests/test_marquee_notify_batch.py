"""看板レースの入稿通知が「まとめて1通・自動入稿」であることを固定する（2026-08-11）。

## 背景（実害）

`submit_marquee_wt.py` は看板レース1件につき `netkeirin_submit_wt.py` を
**子プロセスとして1回ずつ**起動する。子プロセス側は手動入稿経路
（`--manual-rank-key`）の通知を自前で送るので、2026-08-11 の朝は
**「netkeirin手動入稿 … 1件」が16通**届いた。

自動で埋めた分なのに「手動入稿」と書かれるため、人が出したものと誤読しかねない。

## 何を守るか

1. 子プロセスへ `--no-notify` を渡していること（渡さないと件数ぶん通知が飛ぶ）
2. まとめ通知が「自動入稿」であり「手動入稿」と書かないこと
3. 成功・失敗の両方が1通に含まれること
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARQUEE = ROOT / "scripts" / "submit_marquee_wt.py"
SUBMIT = ROOT / "scripts" / "netkeirin_submit_wt.py"


def test_marquee_passes_no_notify_to_child():
    """子プロセスの起動コマンドに --no-notify が入っていること。

    AST で「文字列リテラルとして現れるか」を見る。コメントや docstring では
    通らないようにするため、リテラルの集合で判定する。
    """
    tree = ast.parse(MARQUEE.read_text(encoding="utf-8"))
    literals = {n.value for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    assert "--no-notify" in literals, (
        "submit_marquee_wt.py が子プロセスへ --no-notify を渡していません。"
        "渡さないと看板レースの件数ぶん Discord 通知が飛びます"
    )
    assert "--manual-rank-key" in literals, "前提としていた起動方法が変わっています"


def test_submit_script_accepts_no_notify():
    """netkeirin_submit_wt.py 側が --no-notify を受け取れること。"""
    tree = ast.parse(SUBMIT.read_text(encoding="utf-8"))
    args = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if name == "add_argument" and node.args:
                a = node.args[0]
                if isinstance(a, ast.Constant) and isinstance(a.value, str):
                    args.add(a.value)
    assert "--no-notify" in args, "netkeirin_submit_wt.py が --no-notify を受け付けません"


def test_summary_notification_says_automatic_not_manual():
    """まとめ通知の文面が『自動入稿』で、『手動入稿』と書かないこと。"""
    import scripts.submit_marquee_wt as m  # noqa: PLC0415

    src = inspect.getsource(m._notify_summary)
    assert "自動入稿" in src, "まとめ通知が『自動入稿』と名乗っていません"
    # docstring の説明文には「手動入稿」が出てくるので、実際に送る文面だけを見る
    tree = ast.parse(src)
    sent = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            sent.add(node.value)
        elif isinstance(node, ast.JoinedStr):
            for v in node.values:
                if isinstance(v, ast.Constant) and isinstance(v.value, str):
                    sent.add(v.value)
    body = "".join(s for s in sent if "\n" not in s or "確認" in s)
    assert "手動入稿" not in body, (
        "まとめ通知の文面に『手動入稿』が残っています（自動で埋めた分です）"
    )


def test_summary_includes_success_and_failure(monkeypatch):
    """成功・失敗が1通にまとまること。送信は差し替えて実際には出さない。"""
    import scripts.submit_marquee_wt as m  # noqa: PLC0415

    sent: list[tuple[str, str]] = []

    import src.notify.discord as dc  # noqa: PLC0415

    monkeypatch.setattr(dc, "send", lambda msg, channel=None: sent.append((msg, channel)) or True)
    # 🔴 承認制フラグを固定する（2026-08-14）。`_notify_summary` は文言を
    #    `_approval_required()` で切り替えるようになったので、固定しないと
    #    **本番DBの設定でテストの成否が変わる**（実際に承認制ONで落ちた）。
    import scripts.netkeirin_submit_wt as ns  # noqa: PLC0415
    monkeypatch.setattr(ns, "_approval_required", lambda: False)
    m._notify_summary("2026-08-11", ["前橋11R(7A)", "小倉9R(7S)"], ["大宮5R(7A)"])
    assert len(sent) == 1, f"1通にまとまっていません: {len(sent)}通"
    msg, channel = sent[0]
    assert channel == "netkeirin"
    assert "自動入稿" in msg and "手動入稿" not in msg
    assert "成功2件" in msg and "失敗1件" in msg
    assert "前橋11R(7A)" in msg and "小倉9R(7S)" in msg and "大宮5R(7A)" in msg


def test_summary_not_sent_when_nothing_done(monkeypatch):
    """埋める対象が無い日は通知しない（毎朝の無意味な通知を増やさない）。

    ⚠️ 2026-08-24 に条件へ `skipped_cheap` を足した（平均払戻ゲートの見送り）。
       **意図は変えていない**——「報告すべきことが何も無ければ黙る」であって、
       見送りは報告すべきこと。ここを外すと、看板が全部ゲートで落ちた日に
       通知が1通も出ず、**ゲートが効きすぎていても気づけない**。
    """
    import scripts.submit_marquee_wt as m  # noqa: PLC0415

    src = inspect.getsource(m.main) if hasattr(m, "main") else ""
    assert "if not args.dry_run and (done or failed or skipped_cheap):" \
        in inspect.getsource(m), (
            "成功も失敗も見送りも無いときに通知しないガードが見当たりません"
        )
