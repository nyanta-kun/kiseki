"""段（固め／広め／手広く／荒れ）の**販売スイッチ**（2026-09-16〜 OFF）。

2026-09-15 に 7車を段で売り始めたが、同日の確定57Rで段の実売が 表示的中 21.1%・回収率 37.3%、
同じ日の行を前日までの規則（e018af8 の入稿スクリプト）で dry-run した反実仮想が 31.2%・78.1% と
明確に悪く、ユーザーが「明朝から昨日までの規則に戻す。段商品は検証を続ける」と決定した。

ここで固定するのは次の2つ:

- `TIER_SELL_ENABLED=False` のとき、**入稿（販売）の判断が e018af8 と同じ**になること
  （段の分岐・日次上限の免除（段と `A_ana`）・一軸の判定・段の「自信あり」規則のすべて）
- **生成は止めない**こと（`plans_for` は段のプランを組み続け、`rule_version` はスイッチで割れない）

🔴 段の販売を復活させるときは `src/type_lab.py` の `TIER_SELL_ENABLED` を True にし、
   `test_販売スイッチの既定はOFF` を書き換える（ユーザー決定なしに戻さないための固定）。
"""

from __future__ import annotations

import datetime as _dt
import json

import pytest

import src.type_lab as tl

_H = 3600


def test_販売スイッチの既定はOFF():
    assert tl.TIER_SELL_ENABLED is False, (
        "段の販売は 2026-09-16 から停止中（2026-09-15 ユーザー決定）。戻すなら決定を確認してから")


# e018af8 の `sell_plans_for`（axis_sum を受け取らない版）で期待される結果。
@pytest.mark.parametrize("args,kw,expected", [
    (("A", 7), dict(pw_ent=1.20, trio_ok=True), ["A_trio"]),
    (("A", 7), dict(pw_ent=1.20, trio_ok=False), ["A_hit"]),
    (("A", 7), dict(pw_ent=1.50, trio_ok=True), ["A_ana"]),
    (("F", 9, "決勝"), {}, ["F_pay"]),
    (("F", 7, "予選"), {}, ["F_hit"]),
    (("F", 9, "準決勝"), {}, ["F_line"]),
    (("C", 7), {}, ["C_hit"]),
])
@pytest.mark.parametrize("axis_sum", [None, 1.20, 1.40, 1.60])
@pytest.mark.parametrize("one_axis_ok", [None, True, False])
def test_OFFなら軸信頼を渡しても型別の規則(args, kw, expected, axis_sum, one_axis_ok):
    got = [p.key for p in tl.sell_plans_for(*args, **kw, axis_sum=axis_sum,
                                             one_axis_ok=one_axis_ok)]
    base = [p.key for p in tl.sell_plans_for(*args, **kw)]
    assert got == base == expected
    assert all(k not in tl.TIER_PLAN_KEYS for k in got)


def test_ONなら段で売る(monkeypatch):
    monkeypatch.setattr(tl, "TIER_SELL_ENABLED", True)
    assert [p.key for p in tl.sell_plans_for("A", 7, pw_ent=1.20, axis_sum=1.60)] == ["T_firm"]
    assert [p.key for p in tl.sell_plans_for("F", 7, axis_sum=1.30, one_axis_ok=True)] == ["T_axis"]
    assert [p.key for p in tl.sell_plans_for("F", 9, "準決勝", axis_sum=1.30)] == ["F_line"]


def test_OFFでも段の行は組み続ける():
    """🔴 生成は止めない（段の検証を続けるため）。"""
    keys = [p.key for p in tl.plans_for("F", 7)]
    for k in tl.TIER_PLAN_ORDER:
        assert k in keys


def test_rule_versionはスイッチで割れない(monkeypatch):
    """行の中身はスイッチで変わらないので版を分けない（夜間レビューの世代を無意味に割らない）。"""
    base = tl.rule_version(7)
    monkeypatch.setattr(tl, "TIER_SELL_ENABLED", True)
    assert tl.rule_version(7) == base


# ── 入稿スクリプト ──────────────────────────────────────────────────────

def test_OFFなら日次上限の免除は無い(monkeypatch):
    """🔴 e018af8 には段も `A_ana` の上限免除も無かった。"""
    import scripts.netkeirin_submit_type_lab as m
    assert m.cap_free_plans() == frozenset()
    monkeypatch.setattr(tl, "TIER_SELL_ENABLED", True)
    assert m.cap_free_plans() == m.CAP_FREE_PLANS
    assert "A_ana" in m.cap_free_plans()


def test_上限の判定は定数でなく関数を通す():
    """定数 `CAP_FREE_PLANS` を直接見るとスイッチを切っても `A_ana` が上限外のまま残る。"""
    import inspect

    import scripts.netkeirin_submit_type_lab as m
    src = inspect.getsource(m.run)
    assert "in CAP_FREE_PLANS" not in src
    assert src.count("cap_free_plans()") >= 2


def _rows():
    g = _dt.datetime(2026, 9, 16, 7, 16)
    base = dict(race_date="2026-09-16", venue_name="川崎", race_no=1, race_type="予選",
                n_entries=7, day_index=1, axis1=1, axis2=2, p3_order=None, mode="live",
                bet_type="trifecta", n_legs=4, budget=10000, legs="[]",
                pred_mean_payout=30000, pred_min_payout=20000, rule_version="x",
                cup_grade=None, generated_at=g, pw_ent=1.0)
    rows = []
    for rk, t, ax, pw in (("R1", "C", 1.60, 1.0), ("R2", "E", 1.40, 1.0),
                          ("R3", "F", 1.30, 1.0), ("R4", "A", 1.60, tl.ANA_PW_ENT_MIN + 0.01)):
        for pk in ("C_hit", "E_hit", "F_hit", "A_ana", "T_firm", "T_mid", "T_axis", "T_upset"):
            legs = ('[{"combo": "2-7-1", "stake": 5000, "pred_odds": 6.0}]'
                    if pk == "T_axis" else "[]")
            rows.append(dict(base, race_key=rk, type_label=t, axis_sum=ax, pw_ent=pw,
                             plan_key=pk, legs=legs))
    return rows


def test_OFFなら読み出しは型別の商品だけ(monkeypatch):
    import scripts.netkeirin_submit_type_lab as m
    from tests.test_type_lab_submit import _FakeConn
    rows = _rows()
    monkeypatch.setattr(m, "get_connection", lambda: _FakeConn(rows))
    got = {(r["race_key"], r["plan_key"]) for r in m._load_rows("2026-09-16")}
    assert got == {("R1", "C_hit"), ("R2", "E_hit"), ("R3", "F_hit"), ("R4", "A_ana")}

    monkeypatch.setattr(tl, "TIER_SELL_ENABLED", True)
    got = {(r["race_key"], r["plan_key"]) for r in m._load_rows("2026-09-16")}
    assert got == {("R1", "T_firm"), ("R2", "T_mid"), ("R3", "T_axis"), ("R4", "A_ana")}


def test_OFFなら一軸の入稿ゲートを判定しない(monkeypatch):
    import scripts.netkeirin_submit_type_lab as m
    from tests.test_type_lab_submit import _FakeConn
    rows = _rows()
    monkeypatch.setattr(m, "get_connection", lambda: _FakeConn(rows))
    seen: list[str] = []
    real = m._gate_reason

    def spy(row):
        seen.append(str(row.get("plan_key")))
        return real(row)

    monkeypatch.setattr(m, "_gate_reason", spy)
    m._load_rows("2026-09-16")
    assert "T_axis" not in seen


def _legs(prob, odds, stake=5_000, n=2):
    return [{"combo": f"1-2-{3 + i}", "prob": prob, "stake": stake, "pred_odds": odds}
            for i in range(n)]


def test_OFFなら入稿側の自信ありは旧規則(monkeypatch):
    """段の行が混ざっていても `tier_confident_score` を使わない（e018af8 と同じ EV 規則）。"""
    import scripts.netkeirin_submit_type_lab as m
    called = {"tier": 0, "legacy": 0}

    def tier(*a, **k):
        called["tier"] += 1
        return 1.0

    def legacy(*a, **k):
        called["legacy"] += 1
        return 1.0

    monkeypatch.setattr(m, "tier_confident_score", tier)
    monkeypatch.setattr(m, "type_lab_confident_score", legacy)
    rows = [dict(race_key="R1", plan_key="T_firm", legs=_legs(0.2, 5.0), start_at=0,
                 race_type="決勝", venue_name="川崎", race_no=1),
            dict(race_key="R2", plan_key="D_hit", legs=_legs(0.2, 5.0), start_at=0,
                 race_type="予選", venue_name="川崎", race_no=2)]
    m._choose_confident(rows)
    assert called == {"tier": 0, "legacy": 2}

    called.update(tier=0, legacy=0)
    monkeypatch.setattr(tl, "TIER_SELL_ENABLED", True)
    m._choose_confident(rows)
    assert called == {"tier": 2, "legacy": 0}


def test_OFFなら朝の自信あり選定も旧規則で段の行を候補にしない(monkeypatch):
    from scripts import pick_confident_race_wt as m
    rows = [
        {"race_key": "20260916_11_10", "rank_key": "T_firm", "venue_name": "A", "race_no": 10,
         "legs": _legs(0.15, 7.0), "start_at": 11 * _H, "race_type": "準決勝"},
    ]
    called = {"tier": 0}

    def tier(*a, **k):
        called["tier"] += 1
        return 1.0

    monkeypatch.setattr(m, "tier_confident_score", tier)
    monkeypatch.setattr(m, "_load_type_lab", lambda date: rows)
    m.pick("2026-09-16", dry_run=True)
    assert called["tier"] == 0


def test_OFFなら朝の読み出しは段の行を落とす(monkeypatch):
    """`_load_type_lab` は e018af8 と同じく `SELL_PLANS` の行だけを通す。"""
    from scripts import pick_confident_race_wt as m

    fetched = [
        {"race_key": "a", "rank_key": "T_firm", "venue_name": "A", "race_no": 1,
         "legs": json.dumps(_legs(0.2, 5.0)), "start_at": 0, "race_type": "予選"},
        {"race_key": "b", "rank_key": "C_hit", "venue_name": "A", "race_no": 2,
         "legs": json.dumps(_legs(0.2, 5.0)), "start_at": 0, "race_type": "予選"},
    ]

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, *a, **k):
            class _C:
                def fetchall(self_inner):
                    return fetched
            return _C()

    monkeypatch.setattr(m, "get_connection", lambda: _Conn())
    assert [d["rank_key"] for d in m._load_type_lab("2026-09-16")] == ["C_hit"]
    monkeypatch.setattr(tl, "TIER_SELL_ENABLED", True)
    assert [d["rank_key"] for d in m._load_type_lab("2026-09-16")] == ["T_firm", "C_hit"]
