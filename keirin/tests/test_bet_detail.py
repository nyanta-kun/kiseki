"""入稿した買い目・金額配分の記録（`build_bet_detail`）のテスト。

Web は**この記録を読むだけ**で買い目を表示する。傾斜配分の金額は入稿時点の
想定オッズから決まるため**あとから再現できない**ので、ここが唯一の正本になる。
守るのは3点:

  1. 買い目が**展開済み**であること（表示側が展開ロジックを再実装しないため）
  2. 金額の合計が実際の入稿額と一致すること
  3. 均等配分ランク（`submit_pick` 経路）でも同じ形になること
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.netkeirin_submit_wt import (  # noqa: E402
    RANK_CONFIGS,
    _legs_for_record,
    build_bet_detail,
)
from src.netkeirin_client import (  # noqa: E402
    BET_KIND_TRIFECTA_FORMATION,
    BET_KIND_TRIO_AXIS2,
    BET_KIND_TRIO_BOX,
    BetLeg,
)


def _detail(legs, source=None, odds=None):
    return json.loads(build_bet_detail(legs, source, odds))


def test_三連複軸2車が展開されて金額とともに並ぶ():
    legs = [BetLeg(BET_KIND_TRIO_AXIS2, [[1], [2], [3, 4]], 2500)]
    d = _detail(legs, "blend")
    assert d["source"] == "blend"
    assert d["total"] == 5000
    assert sorted(x["combo"] for x in d["lines"]) == ["1=2=3", "1=2=4"]
    assert all(x["stake"] == 2500 for x in d["lines"])
    assert all(x["bet_type"] == "3連複" for x in d["lines"])


def test_点ごとに金額が違う傾斜配分を表現できる():
    """同額どうしをまとめた複数行から、点ごとの金額へ戻せること。"""
    legs = [
        BetLeg(BET_KIND_TRIO_AXIS2, [[1], [2], [5]], 4100),
        BetLeg(BET_KIND_TRIO_AXIS2, [[1], [2], [3, 4]], 2000),
        BetLeg(BET_KIND_TRIO_AXIS2, [[1], [2], [6]], 1900),
    ]
    d = _detail(legs, "blend")
    got = {x["combo"]: x["stake"] for x in d["lines"]}
    assert got == {"1=2=5": 4100, "1=2=3": 2000, "1=2=4": 2000, "1=2=6": 1900}
    assert d["total"] == 10000


def test_三連単は着順つきで表記が変わる():
    legs = [BetLeg(BET_KIND_TRIFECTA_FORMATION, [[3], [4], [1, 2]], 900)]
    d = _detail(legs)
    assert sorted(x["combo"] for x in d["lines"]) == ["3-4-1", "3-4-2"]
    assert all(x["bet_type"] == "3連単" for x in d["lines"])
    # 3連複は "=", 3連単は "-"（着順の有無が読み取れること）
    assert all("-" in x["combo"] for x in d["lines"])


def test_2券種併買は両方が1つの記録に入る():
    legs = [
        BetLeg(BET_KIND_TRIFECTA_FORMATION, [[3], [4], [1, 2]], 900),
        BetLeg(BET_KIND_TRIO_BOX, [[1, 2, 3, 4]], 200),
    ]
    d = _detail(legs)
    assert {x["bet_type"] for x in d["lines"]} == {"3連単", "3連複"}
    assert d["total"] == 900 * 2 + 200 * 4     # 三連単2点 + BOX4点


def test_均等配分ランクも同じ形になる():
    """`submit_pick` 経路（7B 等）でも表示側の扱いが変わらないこと。"""
    cfg = RANK_CONFIGS["7B"]
    legs = _legs_for_record(cfg, 1, 3, [4, 5, 6], 3300)
    d = _detail(legs)
    assert d["source"] is None
    assert sorted(x["combo"] for x in d["lines"]) == ["1=3=4", "1=3=5", "1=3=6"]
    assert d["total"] == 9900


def test_合成した買い目は本番の入稿と同じ点数になる():
    """`_legs_for_record` が組む groups は `build_bet_id` と同じでなければならない。"""
    cfg = RANK_CONFIGS["7S"]
    partners = [3, 4, 5, 6, 7]
    legs = _legs_for_record(cfg, 1, 2, partners, 2000)
    d = _detail(legs)
    assert len(d["lines"]) == len(partners)


@pytest.mark.parametrize("source", ["blend", "odds", "model", "equal", None])
def test_配分の出どころをそのまま持つ(source):
    legs = [BetLeg(BET_KIND_TRIO_AXIS2, [[1], [2], [3]], 10000)]
    assert _detail(legs, source)["source"] == source


def test_JSONは日本語をエスケープしない():
    """DB を直接読んだときに券種が読めること。"""
    raw = build_bet_detail([BetLeg(BET_KIND_TRIO_AXIS2, [[1], [2], [3]], 100)])
    assert "3連複" in raw


# ── 入稿時点のオッズ（2026-08-07 追加）────────────────────────────────

def test_オッズを渡すと買い目に添えられる():
    """**配分の根拠そのものなので一緒に保存する。** あとから引くと発走時の値に
    なってしまい「なぜこの金額なのか」が読めなくなる。"""
    legs = [BetLeg(BET_KIND_TRIO_AXIS2, [[1], [2], [3, 4]], 2500)]
    odds = {frozenset({1, 2, 3}): 8.34, frozenset({1, 2, 4}): 21.0}
    d = _detail(legs, "blend", odds)
    got = {x["combo"]: x["odds"] for x in d["lines"]}
    assert got == {"1=2=3": 8.3, "1=2=4": 21.0}      # 小数第1位へ丸める


def test_三連単のオッズはtupleキーで引く():
    legs = [BetLeg(BET_KIND_TRIFECTA_FORMATION, [[3], [4], [1]], 500)]
    d = _detail(legs, None, {(3, 4, 1): 128.5})
    # `odds_source` は板/予測の区別（2026-08-12 追加）。板由来なので "board"。
    # `odds_low` は下限包絡（2026-08-16 追加）。三連単は予測モデルが無いので None。
    assert d["lines"][0] == {"bet_type": "3連単", "combo": "3-4-1",
                             "stake": 500, "odds": 128.5, "odds_source": "board",
                             "odds_low": None}


def test_オッズが取れなければNoneで残す():
    """欠損を 0 や省略にすると表示側で「オッズ0倍」と読めてしまう。"""
    legs = [BetLeg(BET_KIND_TRIO_AXIS2, [[1], [2], [3]], 10000)]
    assert _detail(legs)["lines"][0]["odds"] is None
    assert _detail(legs, None, {frozenset({1, 2, 3}): 0})["lines"][0]["odds"] is None


# ── 下限包絡 odds_low（2026-08-16 追加）──────────────────────────────
#
# 🔴 これが要る理由（実測が起点）: 入稿時に記録している「表示オッズ」は板が
#    最優先だが、**朝の板は買い目の帯で確定までに大きく下がる**。
#    実入稿 705点を確定オッズと突合すると 中央 確定/表示 = 0.860・
#    45.0% が 0.8倍未満（7C は 中央 0.651・64.3%）。一方で予測の整合板は
#    中央 1.081・<0.8倍 17.5%（honest 検証窓 5,456点）。
#    そこで**金額の水準を使う判断だけ**を予測側の下限へ寄せる。


def test_odds_low_は板の有無によらず全点に入る():
    """🔴 `odds_low` は「板に無い点を埋める」ものではない。

    板があってもその点の下限を出す。板の値こそが下振れの発生源なので、
    埋め合わせ扱いにすると**一番直したい点だけ下限が付かない**。
    """
    legs = [BetLeg(BET_KIND_TRIO_AXIS2, [[1], [2], [3, 4]], 2500)]
    board = {frozenset({1, 2, 3}): 8.34}                 # 1=2=4 は板に無い
    pred = {frozenset({1, 2, 3}): 9.0, frozenset({1, 2, 4}): 30.0}
    low = {frozenset({1, 2, 3}): 7.6, frozenset({1, 2, 4}): 25.3}
    d = json.loads(build_bet_detail(legs, "predicted", board,
                                    predicted_odds=pred, predicted_low=low))
    got = {x["combo"]: (x["odds"], x["odds_source"], x["odds_low"]) for x in d["lines"]}
    assert got == {"1=2=3": (8.3, "board", 7.6),      # 板は上書きしない
                   "1=2=4": (30.0, "predicted", 25.3)}


def test_odds_low_は表示オッズを超えない():
    """🔴 「下振れ時」が表示オッズより高いのは意味を成さない。

    板が既にモデルの下限より低いなら、その板の値のほうが厳しい見積もり。
    min を取るので下側分位の較正は**安全側へしか動かない**。
    実レース 20260815_22_01 で実際に起きた（板 5.6 に対し下限 5.8）。
    """
    legs = [BetLeg(BET_KIND_TRIO_AXIS2, [[1], [2], [3]], 2500)]
    d = json.loads(build_bet_detail(
        legs, "predicted", {frozenset({1, 2, 3}): 5.6},
        predicted_low={frozenset({1, 2, 3}): 5.8}))
    assert d["lines"][0]["odds_low"] == 5.6


def test_odds_low_が無ければNoneのままで落ちない():
    """モデル未配備・保守倍率欠損でも入稿は止めない（従来どおり動く）。"""
    legs = [BetLeg(BET_KIND_TRIO_AXIS2, [[1], [2], [3]], 10000)]
    d = json.loads(build_bet_detail(legs, None, {frozenset({1, 2, 3}): 5.0}))
    assert d["lines"][0]["odds_low"] is None
    assert d["lines"][0]["odds"] == 5.0


def test_保守板は点予測より必ず低くレース内で一定倍(monkeypatch):
    """`_conservative_trio_board` は下側分位なので 1 未満の倍率が掛かる。

    ⚠️ **倍率を meta から実際に読ませない。** `data/models/` は git 管理外で
       CI には存在しないため、実モデルに依存させるとここだけ落ちる（実際に落とした）。
       検査したいのは「一定倍で必ず下がる」という規則のほう。
    """
    import scripts.netkeirin_submit_wt as m

    monkeypatch.setattr(m, "conservative_multiplier", lambda n, q: 0.8453)
    board = {frozenset({1, 2, 3}): 10.0, frozenset({1, 2, 4}): 40.0}
    low = m._conservative_trio_board(board, 7)
    for k, v in board.items():
        assert 0 < low[k] < v, f"{k}: 下限 {low[k]} が点予測 {v} を下回っていません"
    # レース内で一定倍＝順序は保つ（配分の比率を壊さないことの確認）
    assert len({low[k] / v for k, v in board.items()}) == 1


def test_保守板は空盤面で例外を出さない():
    from scripts.netkeirin_submit_wt import _conservative_trio_board

    assert _conservative_trio_board({}, 7) == {}


def test_保守倍率が取れなければ下限を出さない(monkeypatch):
    """🔴 モデル・meta 未配備でも入稿は止めない（下限が出ないだけ）。

    ⚠️ 代わりに 1.0 を掛けて「下限」として出してはいけない。それは点予測
       そのもので、下限だと思って読まれると**楽観側へ黙って倒れる**。
    """
    import scripts.netkeirin_submit_wt as m
    from src.odds_prediction import OddsPredictionUnavailable

    def _boom(n, q):
        raise OddsPredictionUnavailable("meta がありません")

    monkeypatch.setattr(m, "conservative_multiplier", _boom)
    assert m._conservative_trio_board({frozenset({1, 2, 3}): 10.0}, 7) == {}


# ── netkeirin の公開（2026-08-16 追加）─────────────────────────────────
#
# 仕様は `race_auth.html` の実機 JS から確定:
#   個別 param.action='change_status'; param.race_id = race_id      （スカラー）
#   一括 param.action='change_status'; param.race_id = [race_id...]  （配列 → race_id[]）
# 🔴 **公開は不可逆**（netkeirin の確認文言「公開後は修正できなくなります」）。


def _client(monkeypatch, captured):
    from src.netkeirin_client import NetkeirinClient

    cl = NetkeirinClient.__new__(NetkeirinClient)
    cl.propose_only = False

    class _Sess:
        def post(self, url, data=None, timeout=None):
            captured["url"] = url
            captured["data"] = data
            return type("R", (), {"raise_for_status": lambda s: None,
                                  "json": lambda s: {"status": "OK"}})()
    cl.session = _Sess()
    return cl


def test_公開は1件ならスカラーで送る(monkeypatch):
    cap = {}
    ok, msg = _client(monkeypatch, cap).publish_picks(["202608160101"])
    assert ok
    assert cap["data"]["action"] == "change_status"
    assert cap["data"]["race_id"] == "202608160101"
    assert "race_id[]" not in cap["data"]


def test_公開は複数なら配列で送る(monkeypatch):
    cap = {}
    ok, _ = _client(monkeypatch, cap).publish_picks(["202608160101", "202608160102"])
    assert ok
    assert cap["data"]["race_id[]"] == ["202608160101", "202608160102"]
    assert "race_id" not in cap["data"], "本家 JS と同じくスカラーとは併用しない"


def test_未送信の入稿案は公開対象から外す(monkeypatch):
    """🔴 `PROPOSED:` は netkeirin にまだ存在しない＝公開する相手がいない。"""
    from src.netkeirin_client import PROPOSED_PREFIX

    cap = {}
    ok, msg = _client(monkeypatch, cap).publish_picks([f"{PROPOSED_PREFIX}202608160101"])
    assert not ok and "race_id がありません" in msg
    assert "data" not in cap, "netkeirin へリクエストを送っています"


def test_propose_onlyでは公開しない():
    """承認制の下でも公開は実操作。素通しすると記録だけ進んで実態と食い違う。"""
    from src.netkeirin_client import NetkeirinClient

    cl = NetkeirinClient.__new__(NetkeirinClient)
    cl.propose_only = True
    ok, msg = cl.publish_picks(["202608160101"])
    assert not ok and "propose_only" in msg
