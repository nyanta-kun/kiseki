"""逃げ先頭ライン（`L_lead`）の販売（2026-09-24 ユーザー決定）。

「1日上限5本・穴狙いとして・現在売っていないレースに追加」
「モーニングを除外として、早い未販売の5レース」
→ 同日改訂「本数の上限を外し、除外も提示条件としてください」（準決勝系・型E を外す）

ここで固定するのは:

- 型ラボの本体が**全部終わった後**に回り、どの商品も出ていないレースにだけ出す（既存を減らさない）
- **モーニング開催・準決勝系・型E を外し**、発走の早い順に**本数の上限なし**で出す
  （上限を掛けたときは波をまたいで数える）
- 出どころ `origin='line_lead'`・勝負アイコンは**穴狙い**
- 手動入稿（`--race-key`）では回さない・入稿設定で無効にできる・失敗しても型ラボの入稿を止めない
- `L_lead` で出したレースを昼・夕に**組み直さない**（売った買い目が書き換わる）
- 監査の見張りで `L_lead` を**本線に混ぜない**
"""
from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_JST = timezone(timedelta(hours=9))


def _ts(h: int, m: int = 0) -> str:
    return str(int(datetime(2026, 9, 25, h, m, tzinfo=_JST).timestamp()))


def _row(rk: str, plan: str, start: str, *, type_label: str = "B", axis_sum: float = 1.9,
         race_type: str = "予選"):
    legs = ([{"combo": "5-6-1", "stake": 3300, "pred_odds": 80.0},
             {"combo": "5-6-2", "stake": 3300, "pred_odds": 90.0},
             {"combo": "5-6-3", "stake": 3300, "pred_odds": 120.0}]
            if plan == "L_lead" else
            [{"combo": "1-4-5", "stake": 6400, "pred_odds": 2.6},
             {"combo": "1-4-7", "stake": 2100, "pred_odds": 9.9},
             {"combo": "1-4-6", "stake": 1500, "pred_odds": 16.2}])
    return {"race_key": rk, "race_date": "2026-09-25", "venue_name": "場",
            "race_no": int(rk[-2:]), "race_type": race_type, "n_entries": 7, "cup_grade": None,
            "type_label": type_label, "axis_sum": axis_sum, "pw_ent": 1.2,
            "axis1": 1, "axis2": 4, "p3_order": "1-4-5-6-7-2-3", "mode": "live",
            "plan_key": plan, "bet_type": "trifecta", "n_legs": 3, "budget": 10_000,
            "pred_mean_payout": 300_000.0 if plan == "L_lead" else 30_000.0,
            "pred_min_payout": 250_000.0 if plan == "L_lead" else 25_000.0,
            "start_at": start, "legs": legs}


def _env(monkeypatch, rows, lead_rows, day_races, *, already=frozenset(), slots="unset"):
    from scripts import netkeirin_submit_type_lab as m

    sent: list[tuple] = []
    monkeypatch.setattr(m, "_load_settings", dict)
    monkeypatch.setattr(m, "_approval_required", lambda: False)
    monkeypatch.setattr(m, "_load_closed_races", lambda day: set())
    monkeypatch.setattr(m, "_already_submitted", lambda keys: set(already))
    monkeypatch.setattr(m, "_missing_market_inputs", lambda rk: None)
    monkeypatch.setattr(m, "_build_entry_table", lambda rk, marks: None)
    monkeypatch.setattr(m, "_race_point_sd", lambda keys: {})
    monkeypatch.setattr(m, "_load_rows", lambda day: rows)
    monkeypatch.setattr(m, "_load_highpay_rows", lambda day: {})
    monkeypatch.setattr(m, "_load_line_lead_rows", lambda day: lead_rows)
    monkeypatch.setattr(m, "_load_day_races", lambda day: day_races)
    monkeypatch.setattr(m, "auto_publish_submitted", lambda dry: [])
    monkeypatch.setattr(m, "_write_confident", lambda *a, **k: None)
    monkeypatch.setattr(m, "send", lambda *a, **k: None)
    if slots != "unset":
        monkeypatch.setattr(m, "LINE_LEAD_SLOTS_PER_DAY", slots)

    def _fake_submit(row, session, client, dry_run, show_detail=False,
                     skip=None, confident=False, origin=m.ORIGIN_RANK):
        sent.append((str(row["race_key"]), str(row["plan_key"]), origin))
        return True, "ok"

    monkeypatch.setattr(m, "submit_row", _fake_submit)
    return m, sent


# 10:50 開始のデイ（28）と 08:30 開始のモーニング（87）
DAY = [("20260925_87_01", 87, _ts(8, 30)), ("20260925_87_05", 87, _ts(10, 40)),
       ("20260925_28_01", 28, _ts(10, 50)), ("20260925_28_02", 28, _ts(11, 20)),
       ("20260925_28_03", 28, _ts(11, 50)), ("20260925_28_04", 28, _ts(12, 20)),
       ("20260925_28_05", 28, _ts(12, 50)), ("20260925_28_06", 28, _ts(13, 20)),
       ("20260925_28_07", 28, _ts(13, 50))]
START = {rk: st for rk, _v, st in DAY}


#: 型ラボの行（モーニング開催のレース）。🔴 `run` は型ラボの売り物の行が1件も無い日は
#: 冒頭で終わる。`L_lead` の行は型ラボの行と同じ生成で作られるので、`L_lead` の候補があって
#: 型ラボの行が無い日は実際には起きない——現実に合わせて1件置く。
_TL = [_row("20260925_87_05", "B_hit", START["20260925_87_05"])]


def _leads(*keys):
    return [_row(k, "L_lead", START[k]) for k in keys]


def test_売っていないレースに上限なしで早い順(monkeypatch):
    # 型ラボは 28_01 だけ売る（1レースなので上限 max(1, 0) = 1件）
    rows = [_row("20260925_28_01", "B_hit", START["20260925_28_01"])]
    leads = _leads("20260925_28_01", "20260925_28_02", "20260925_28_03", "20260925_28_04",
                   "20260925_28_05", "20260925_28_06", "20260925_28_07")
    m, sent = _env(monkeypatch, rows, leads, DAY)
    m.run("2026-09-25", "morning", dry_run=False, only_key=None, do_rebuild=False)
    lead = [rk for rk, p, o in sent if p == "L_lead"]
    assert lead == ["20260925_28_02", "20260925_28_03", "20260925_28_04",
                    "20260925_28_05", "20260925_28_06", "20260925_28_07"], sent
    assert all(o == m.ORIGIN_LINE_LEAD for _rk, p, o in sent if p == "L_lead")
    # 1レース1商品（型ラボが売った 28_01 には出ない）
    assert len({rk for rk, _, _ in sent}) == len(sent), sent


def test_モーニング開催は外す(monkeypatch):
    leads = _leads("20260925_87_01", "20260925_87_05", "20260925_28_02")
    m, sent = _env(monkeypatch, _TL, leads, DAY)
    m.run("2026-09-25", "morning", dry_run=False, only_key=None, do_rebuild=False)
    assert [rk for rk, p, _ in sent if p == "L_lead"] == ["20260925_28_02"], sent


def test_上限を掛けたときは波をまたいで数える(monkeypatch):
    leads = _leads("20260925_28_02", "20260925_28_03", "20260925_28_04")
    already = {("20260925_28_05", "L_lead"), ("20260925_28_06", "L_lead"),
               ("20260925_28_07", "L_lead"), ("20260925_28_01", "L_lead")}
    m, sent = _env(monkeypatch, _TL, leads, DAY, already=already, slots=5)
    m.run("2026-09-25", "noon", dry_run=False, only_key=None, do_rebuild=False)
    assert [rk for rk, p, _ in sent if p == "L_lead"] == ["20260925_28_02"], sent


def test_上限の既定は無し():
    from scripts import netkeirin_submit_type_lab as m
    assert m.LINE_LEAD_SLOTS_PER_DAY is None


def test_準決勝系と型Eは出さない(monkeypatch):
    leads = [_row("20260925_28_02", "L_lead", START["20260925_28_02"], race_type="準決勝"),
             _row("20260925_28_03", "L_lead", START["20260925_28_03"], type_label="E"),
             _row("20260925_28_04", "L_lead", START["20260925_28_04"], race_type="決勝"),
             _row("20260925_28_05", "L_lead", START["20260925_28_05"], type_label="F")]
    m, sent = _env(monkeypatch, _TL, leads, DAY)
    m.run("2026-09-25", "morning", dry_run=False, only_key=None, do_rebuild=False)
    # 決勝（準決勝ではない）と型F は対象のまま
    assert [rk for rk, p, _ in sent if p == "L_lead"] == ["20260925_28_04", "20260925_28_05"], sent


def test_型の除外はそのレースの最新の型で見る(monkeypatch):
    """`L_lead` の行の型が古くても、型ラボの最新の型が E なら出さない。"""
    from scripts import netkeirin_submit_type_lab as m

    lead = dict(_row("20260925_28_02", "L_lead", START["20260925_28_02"], type_label="F"),
                legs="[]")
    other = dict(_row("20260925_28_03", "L_lead", START["20260925_28_03"], type_label="E"),
                 legs="[]")
    current = {("20260925_28_02", "live"): (2, "E"), ("20260925_28_03", "live"): (2, "B")}
    monkeypatch.setattr(m, "_fetch_rows", lambda day: ([lead, other], current))
    rows = m._load_line_lead_rows("2026-09-25")
    assert {r["race_key"]: r["type_label"] for r in rows} == {
        "20260925_28_02": "E", "20260925_28_03": "B"}
    assert [r["race_key"] for r in m.line_lead_candidates(rows, set())] == ["20260925_28_03"]


def test_既に何かを出したレースと締切後には出さない(monkeypatch):
    leads = _leads("20260925_28_02", "20260925_28_03", "20260925_28_04")
    m, sent = _env(monkeypatch, _TL, leads, DAY, already={("20260925_28_02", "RANK_7S")})
    monkeypatch.setattr(m, "_load_closed_races", lambda day: {"20260925_28_03"})
    m.run("2026-09-25", "noon", dry_run=False, only_key=None, do_rebuild=False)
    assert [rk for rk, p, _ in sent if p == "L_lead"] == ["20260925_28_04"], sent


def test_手動入稿では出さない(monkeypatch):
    rows = [_row("20260925_28_01", "B_hit", START["20260925_28_01"])]
    m, sent = _env(monkeypatch, rows, _leads("20260925_28_02"), DAY)
    m.run("2026-09-25", "morning", dry_run=False, only_key="20260925_28_01", do_rebuild=False)
    assert not [x for x in sent if x[1] == "L_lead"], sent


def test_入稿設定で止められる(monkeypatch):
    m, sent = _env(monkeypatch, _TL, _leads("20260925_28_02"), DAY)
    monkeypatch.setattr(m, "_load_settings", lambda: {"L_lead": {"enabled": False}})
    m.run("2026-09-25", "morning", dry_run=False, only_key=None, do_rebuild=False)
    assert sent and not [x for x in sent if x[1] == "L_lead"], sent


def test_上限0で止まる(monkeypatch):
    m, sent = _env(monkeypatch, _TL, _leads("20260925_28_02"), DAY, slots=0)
    m.run("2026-09-25", "morning", dry_run=False, only_key=None, do_rebuild=False)
    assert sent and not [x for x in sent if x[1] == "L_lead"], sent


def test_失敗しても型ラボの入稿は止まらない(monkeypatch):
    rows = [_row("20260925_28_01", "B_hit", START["20260925_28_01"])]
    m, sent = _env(monkeypatch, rows, [], DAY)

    def _boom(day):
        raise RuntimeError("DB が落ちた")

    monkeypatch.setattr(m, "_load_line_lead_rows", _boom)
    m.run("2026-09-25", "morning", dry_run=False, only_key=None, do_rebuild=False)
    assert sent == [("20260925_28_01", "B_hit", m.ORIGIN_RANK)]


def test_穴狙いのアイコン():
    from scripts import netkeirin_submit_type_lab as m
    assert m.ACT_TYPE_BY_PLAN["L_lead"] == m.ACT_TYPE_LONGSHOT


def test_型ラボの本体より後に回る():
    from scripts import netkeirin_submit_type_lab as m
    src = inspect.getsource(m.run)
    assert src.index("confident=is_conf)") < src.index("_run_line_lead()"), (
        "逃げ先頭ラインが型ラボの本体より先に回っている（既存の商品を奪う）")
    assert src.index("_run_line_lead()") < src.index("auto_publish_submitted("), (
        "公開・通知より後に回っていて、出した商品が公開されない")


def test_L_leadで出したレースは組み直さない():
    from scripts import netkeirin_submit_type_lab as m
    src = inspect.getsource(m.run)
    i = src.index("todo = {")
    assert "rk in taken" in src[i:i + 900], "別ランク（L_lead）が取ったレースを組み直している"


def test_最新の型の判定にL_leadの行を使わない(monkeypatch):
    """`--only-plans L_lead` で後から足した行の型で、型ラボの行が古い扱いにならない。"""
    from scripts import netkeirin_submit_type_lab as m

    base = {"race_key": "r", "mode": "live"}
    rows = [dict(base, plan_key="B_hit", type_label="B", generated_at=1),
            dict(base, plan_key="L_lead", type_label="F", generated_at=2)]

    class _C:
        def execute(self, *a, **k):
            class _R:
                def fetchall(self):
                    return rows
            return _R()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(m, "get_connection", lambda: _C())
    _rows, current = m._fetch_rows("2026-09-25")
    assert current[("r", "live")][1] == "B"


def test_監査の本線に混ぜない():
    import importlib.util
    from types import SimpleNamespace

    spec = importlib.util.spec_from_file_location("audit_watch", REPO / "scripts" / "audit_watch.py")
    aw = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(aw)
    races = [SimpleNamespace(origin="line_lead", rank_key="L_lead"),
             SimpleNamespace(origin="rank", rank_key="B_hit")]
    assert [r.rank_key for r in aw._layer(races, "base")] == ["B_hit"]
    assert [r.rank_key for r in aw._layer(races, "line_lead")] == ["L_lead"]


def test_入稿通知に逃げ先頭のレース名を出す(monkeypatch):
    """「ランク別 … L_lead 4」だけでは、どのレースへ足したのかが読めない（2026-09-25 指摘）。"""
    rows = [_row("20260925_28_01", "B_hit", START["20260925_28_01"])]
    m, sent = _env(monkeypatch, rows, _leads("20260925_28_02", "20260925_28_03"), DAY)
    msgs: list[str] = []
    monkeypatch.setattr(m, "send", lambda text, channel=None, **k: msgs.append(text))
    monkeypatch.setattr(m, "auto_publish_submitted", lambda dry: [{"ok": True}] * 3)
    monkeypatch.setattr(m, "_confident_line", lambda day: "🎯 自信あり: なし")
    m.run("2026-09-25", "morning", dry_run=False, only_key=None, do_rebuild=False)
    body = "\n".join(msgs)
    assert "🏃 逃げ先頭（穴狙い）2件: 場2R（11:20）・場3R（11:50）" in body, body


def test_逃げ先頭を出さなかった回は行を書かない(monkeypatch):
    rows = [_row("20260925_28_01", "B_hit", START["20260925_28_01"])]
    m, sent = _env(monkeypatch, rows, [], DAY)
    msgs: list[str] = []
    monkeypatch.setattr(m, "send", lambda text, channel=None, **k: msgs.append(text))
    monkeypatch.setattr(m, "auto_publish_submitted", lambda dry: [{"ok": True}])
    monkeypatch.setattr(m, "_confident_line", lambda day: "🎯 自信あり: なし")
    m.run("2026-09-25", "morning", dry_run=False, only_key=None, do_rebuild=False)
    assert msgs and "逃げ先頭" not in "\n".join(msgs), msgs
