"""入稿設定の「自動公開」の契約を固定する（2026-08-29）。

ユーザー要望「入稿確認を ON/OFF 切り替えに。入稿設定の自動入稿の下に『自動公開』を
追加し、ON のときは入稿データ作成と共に自動入稿。ON 時は自動入稿を ON 固定」。

守るのは3点:

1. **専用の列を作らない**。自動公開は承認制（`require_approval`）の裏返し。
   2つのフラグに分けると「承認待ちなのに公開する」という状態が作れてしまい、
   **公開は不可逆**なので事故が戻せない。
2. **送られてこなかった `auto_publish` で上書きしない**。既定 False で書くと、
   テンプレートを直しただけの保存が承認制を勝手に ON にする。
3. **自動公開 ON なら自動入稿も ON**。入稿しないものは公開できないので、
   この2つが独立に動けると「公開する設定なのに何も出ない」状態になる。
"""
from __future__ import annotations

from src.api.keirin_router import (
    NetkeirinSettingIn,
    auto_publish_of,
    global_mode_updates,
)


def test_自動公開は承認制の裏返し():
    assert auto_publish_of("_global", require_approval=False) is True
    assert auto_publish_of("_global", require_approval=True) is False


def test_ランク行は自動公開を持たない():
    """`_global` 以外の行の `require_approval` は意味を持たない。

    ここを True にすると、ランクごとに自動公開があるかのように画面へ出る。
    """
    assert auto_publish_of("7C", require_approval=False) is False
    assert auto_publish_of("A_hit", require_approval=False) is False


def test_未指定なら承認制を触らない():
    """🔴 既定値で承認制を書き換えないこと。"""
    assert global_mode_updates(None) == {}


def test_自動公開ONで承認制OFFと自動入稿ONを同時に書く():
    assert global_mode_updates(True) == {"require_approval": False, "enabled": True}


def test_自動公開OFFは承認制ONだけを書く():
    """OFF は「承認制へ戻す」。自動入稿の ON/OFF は画面の値をそのまま使う
    （OFF に戻したときまで自動入稿を強制すると、入稿を止められなくなる）。"""
    assert global_mode_updates(False) == {"require_approval": True}


def test_auto_publishの既定はNone():
    """🔴 False にしてはいけない。この項目を知らないクライアントの保存が
    「自動公開 OFF ＝ 承認制 ON」を黙って書き込む。"""
    item = NetkeirinSettingIn(rank_key="_global", enabled=True,
                              title_template="", comment_template="")
    assert item.auto_publish is None
