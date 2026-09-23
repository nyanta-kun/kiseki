"""逃げ先頭ライン（`L_lead`）の検証集計（`services/keirin_line_lead_verify`）。

固定するのは「もし売っていたら」の会計が正しいこと:

- 新ランクのレースでは、売った商品を**外して**新ランクを入れる（置き換え）
- 売っていなかったレースには新ランクを**足す**
- 未採点は ROI に混ぜない（0円払戻として数えると回収率が下がって見える）
- 欠車の返還（`void_refund`）は投資から引く
"""

from __future__ import annotations

from src.services.keirin_line_lead_verify import (
    LeadRow,
    SoldRow,
    Tally,
    build_line_lead_report,
)


def _lead(
    rk: str, payout: int = 0, *, settled: bool = True, n: int = 4, stake: int = 2500, void: int = 0, start: int = 0
) -> LeadRow:
    return LeadRow(
        rk,
        f"{rk[:4]}-{rk[4:6]}-{rk[6:8]}",
        "場",
        int(rk[-2:]),
        "一般",
        start,
        tuple(f"1-2-{c}" for c in range(3, 3 + n)),
        (stake,) * n,
        settled,
        payout > 0,
        payout,
        void,
    )


def test_置き換えは売った商品を外して新ランクを入れる():
    leads = [_lead("20260901_31_03", 1_000_000)]
    sold = [
        SoldRow("20260901_31_03", "C_hit", 10_000, 20_000, True),
        SoldRow("20260901_31_05", "B_hit", 10_000, 0, True),
    ]
    s = build_line_lead_report(leads, sold)["summary"]
    assert s["current"]["invest"] == 20_000 and s["current"]["payout"] == 20_000
    # 置き換え後 = 新ランク(1万円) + 31_05 の B_hit(1万円)。31_03 の C_hit は外れる
    assert s["combined"]["invest"] == 20_000 and s["combined"]["payout"] == 1_000_000
    assert s["displaced"]["n"] == 1 and s["displaced"]["payout"] == 20_000


def test_売っていなかったレースには足す():
    s = build_line_lead_report([_lead("20260901_31_03", 0)], [SoldRow("20260901_31_05", "B_hit", 10_000, 0, True)])[
        "summary"
    ]
    assert s["combined"]["n"] == 2 and s["current"]["n"] == 1
    assert s["displaced"]["n"] == 0


def test_未採点はROIに混ぜない():
    s = build_line_lead_report([_lead("20260901_31_03", 0, settled=False), _lead("20260901_31_04", 50_000)], [])[
        "summary"
    ]
    assert s["lead"]["n"] == 1 and s["lead"]["pending"] == 1
    assert s["lead"]["roi"] == 500.0


def test_欠車の返還は投資から引く():
    assert _lead("20260901_31_03", n=4, stake=2500, void=2500).invest == 7_500


def test_日別の収支差():
    leads = [_lead("20260901_31_03", 300_000)]
    sold = [SoldRow("20260901_31_03", "C_hit", 10_000, 0, True)]
    day = build_line_lead_report(leads, sold)["days"][0]
    assert day["date"] == "2026-09-01"
    assert day["diff"] == (300_000 - 10_000) - (0 - 10_000)


def test_大当たりを除いた回収率():
    t = Tally()
    for p in (1_000_000, 0, 0, 0):
        t.add(10_000, p, True)
    assert t.roi == 2500.0 and t.roi_without_top(1) == 0.0


def test_レースは日付の新しい順で同日は発走の早い順():
    leads = [_lead("20260901_31_03", start=200), _lead("20260901_31_01", start=100), _lead("20260902_31_01", start=300)]
    keys = [r["race_key"] for r in build_line_lead_report(leads, [])["races"]]
    assert keys == ["20260902_31_01", "20260901_31_01", "20260901_31_03"]


def test_1日の画面用に買い目の明細と決着の配当を返す():
    from dataclasses import replace

    r = replace(
        _lead("20260901_31_03", 60_000, n=3, stake=3300),
        combos=("1-2-3", "1-2-4", "1-2-5"),
        pred_odds=(12.5, 30.0, 45.0),
        win_combo="1-2-4",
        win_tf_odds=18.2,
    )
    race = build_line_lead_report(
        [r], [SoldRow("20260901_31_03", "C_hit", 10_000, 0, True, ("3連単 1-3-2 ×5,700円",), "題")]
    )["races"][0]
    assert [leg["won"] for leg in race["legs"]] == [False, True, False]
    assert race["legs"][0]["pred_odds"] == 12.5 and race["legs"][0]["stake"] == 3300
    assert race["win_tf_odds"] == 18.2
    assert race["sold"][0]["lines"] == ["3連単 1-3-2 ×5,700円"] and race["sold"][0]["title"] == "題"


def test_売った商品の買い目の表示():
    from src.services.keirin_line_lead_verify import sold_lines

    d = {
        "lines": [
            {"bet_type": "3連単", "combo": "1-3-2", "stake": 5700},
            {"bet_type": "3連複", "combo": "1-2-3", "stake": 300},
        ]
    }
    assert sold_lines(d) == ("3連単 1-3-2 ×5,700円", "3連複 1-2-3 ×300円")
    assert sold_lines(None) == () and sold_lines("壊れた") == () and sold_lines({"lines": [{}]}) == ()
