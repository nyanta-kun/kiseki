"""テンプレートのプレースホルダ反映ガード（2026-08-14・実害あり）。

## 背景（本番で起きたこと）

`{wide_note}` を含む新テンプレートを **DB へ先に反映**し、それを置換するコードの
デプロイが後になった。その間に走った 18:00 の波が、本文に `{wide_note}` を
**素で残したまま入稿案を12件**作った（幸い `proposed` で netkeirin へは未送信）。

DB とコードは別々に反映されるので順序を守るしかない。「入稿側が置換できる
キーか」を機械的に確かめて、逆順の反映を止める。
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import scripts.update_netkeirin_templates as m  # noqa: E402


def test_current_templates_use_only_known_placeholders():
    """🔴 いまのテンプレートは全部置換できること。"""
    assert m._unknown_placeholders() == set()


def test_unknown_placeholder_is_detected(monkeypatch):
    """🔴 入稿側が知らないキーを混ぜたら検出すること。"""
    monkeypatch.setitem(m.COMMENT_TEMPLATES, "7S",
                        m.COMMENT_TEMPLATES["7S"] + "{brand_new_key}")
    assert "{brand_new_key}" in m._unknown_placeholders()


def test_apply_is_blocked_when_a_placeholder_is_unknown(monkeypatch, capsys):
    """🔴 `--apply` が**書き込む前に**止まること（順序を守らせる本体）。"""
    monkeypatch.setitem(m.COMMENT_TEMPLATES, "7S",
                        m.COMMENT_TEMPLATES["7S"] + "{brand_new_key}")
    monkeypatch.setattr(sys, "argv", ["x", "--apply"])
    called = []
    monkeypatch.setattr(m, "get_connection",
                        lambda *a, **k: called.append(1))   # 呼ばれたら失敗
    assert m.main() == 1
    assert not called, "プレースホルダ未対応なのに DB へ触っている"
    assert "デプロイ" in capsys.readouterr().err
