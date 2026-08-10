"""承認制（propose_only）と下書き削除の挙動を固定する（2026-08-11）。

## 守りたいこと

1. 承認制のとき **netkeirin へ一切 POST しない**（出さないつもりで出てしまうのが最悪）
2. 承認制の戻り値が本物の race_id と**必ず区別できる**こと
   （区別できないと未送信の行を送信済みと誤認し、二重入稿防止が壊れる）
3. ゲートが唯一の POST 地点（`_post_goods`）にあること
   — 送信の分岐は submit_pick / submit_pick_multi / 手動経路の3か所にあり、
     個別に止めると片方だけ抜ける（2026-08-08 の 9H1 と同型の事故）
4. 削除が `action=delete` + `item_id` で送られること
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from src import netkeirin_client as nc

ROOT = Path(__file__).resolve().parent.parent


class _Boom:
    """呼ばれたら失敗させるためのダミー。承認制で POST が起きたら分かる。"""

    def __init__(self):
        self.calls: list = []

    def post(self, *a, **k):
        self.calls.append(("post", a, k))
        raise AssertionError("承認制なのに netkeirin へ POST しました")

    def get(self, *a, **k):
        self.calls.append(("get", a, k))
        raise AssertionError("承認制なのに netkeirin へ GET しました")


def _client(propose_only: bool) -> nc.NetkeirinClient:
    c = nc.NetkeirinClient.__new__(nc.NetkeirinClient)
    c.propose_only = propose_only
    c.session = _Boom()
    return c


def test_propose_mode_never_posts():
    c = _client(True)
    ok, msg = c._post_goods(
        race_id="202608110101", n_cars=7, mark={"1": "1"},
        title="t", comment="c", kaime=[{"bet_id": "x", "bet_money": 100}],
        act_type="0",
    )
    assert ok is True
    assert msg.startswith(nc.PROPOSED_PREFIX), "入稿案の戻り値が race_id と区別できません"
    assert c.session.calls == [], "承認制なのに通信しています"


def test_proposed_marker_is_distinguishable_from_race_id():
    """本物の race_id は数字だけ。接頭辞が付いていれば取り違えない。"""
    assert nc.PROPOSED_PREFIX
    assert not nc.PROPOSED_PREFIX.isdigit()
    marker = f"{nc.PROPOSED_PREFIX}202608110101"
    assert not marker.isdigit()
    assert marker.removeprefix(nc.PROPOSED_PREFIX) == "202608110101"


def test_delete_is_noop_in_propose_mode():
    c = _client(True)
    ok, msg = c.delete_pick("b1723163_732")
    assert ok is True and msg.startswith(nc.PROPOSED_PREFIX)
    assert c.session.calls == []


def test_delete_rejects_empty_item_id():
    c = _client(False)
    ok, msg = c.delete_pick("")
    assert ok is False and "item_id" in msg
    assert c.session.calls == [], "item_id が空なのに通信しています"


def test_delete_sends_action_delete_with_item_id():
    """POST の中身が action=delete + item_id であること。"""
    sent: dict = {}

    class _S:
        def post(self, url, data=None, timeout=None):
            sent.update({"url": url, "data": data})

            class _R:
                @staticmethod
                def raise_for_status():
                    return None

                @staticmethod
                def json():
                    return {"status": "OK", "item_id": data["item_id"]}

            return _R()

    c = nc.NetkeirinClient.__new__(nc.NetkeirinClient)
    c.propose_only = False
    c.session = _S()
    ok, msg = c.delete_pick("b1723163_732")
    assert ok is True and msg == "b1723163_732"
    assert sent["data"]["action"] == "delete"
    assert sent["data"]["item_id"] == "b1723163_732"
    assert "race_id" not in sent["data"], "delete に race_id は不要（仕様2.1）"


def test_delete_reports_failure_status():
    class _S:
        def post(self, url, data=None, timeout=None):
            class _R:
                @staticmethod
                def raise_for_status():
                    return None

                @staticmethod
                def json():
                    return {"status": "NG", "message": "not found"}

            return _R()

    c = nc.NetkeirinClient.__new__(nc.NetkeirinClient)
    c.propose_only = False
    c.session = _S()
    ok, msg = c.delete_pick("x_1")
    assert ok is False and "削除失敗" in msg


def test_gate_lives_in_the_single_post_choke_point():
    """propose_only の判定が `_post_goods` にあること。

    送信分岐ごとに書くと片方が抜ける。唯一の POST 地点で止めているかを
    構造で確かめる（コメントではなく実際の分岐を見る）。
    """
    src = inspect.getsource(nc.NetkeirinClient._post_goods)
    tree = ast.parse(inspect.cleandoc(src))
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            for sub in ast.walk(node.test):
                if isinstance(sub, ast.Attribute) and sub.attr == "propose_only":
                    found = True
    assert found, "_post_goods に propose_only の分岐がありません"


def test_fetch_item_ids_parses_delete_buttons():
    html = """
    <table>
      <tr><td><a href="/bet/race.html?race_id=202608110101">前橋1R</a></td>
          <td><button id="act-yoso_delete_b1723163_732">削除</button></td></tr>
      <tr><td><a href="/bet/race.html?race_id=202608110102">前橋2R</a></td>
          <td><button id="act-yoso_delete_b1723164_732">削除</button></td></tr>
    </table>
    """

    class _S:
        def get(self, url, timeout=None):
            class _R:
                text = html

                @staticmethod
                def raise_for_status():
                    return None

            return _R()

    c = nc.NetkeirinClient.__new__(nc.NetkeirinClient)
    c.propose_only = False
    c.session = _S()
    got = c.fetch_item_ids()
    assert got == {"202608110101": "b1723163_732", "202608110102": "b1723164_732"}


@pytest.mark.parametrize("prop", [True, False])
def test_constructor_sets_flag(prop, monkeypatch):
    monkeypatch.setattr(nc.NetkeirinClient, "_load_cookies", lambda self: None)
    c = nc.NetkeirinClient(propose_only=prop)
    assert c.propose_only is prop
