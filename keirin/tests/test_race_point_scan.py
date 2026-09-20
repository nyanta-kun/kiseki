"""`race_point` の汚染を**過去へ遡って**探せること（2026-09-20 監査 item5）。

日次の健全性チェック（`check_race_point_sanity.py --date`）は
`daily_picks_wt.sh` から**その日ぶんにしか掛かっていない**。そのため
2026-06-12 の汚染（43・61 会場の 24 レース・210 行）は誰にも検知されず、
`docs/prediction-factors.md` には「汚染は 2026-06-18〜07-23 で解消済み」と
**窓の外の話**が書かれたまま残っていた。

再取得（`pipeline_wt.py::_get_collected_keys`）は結果の入った行をスキップするので
**自動経路では二度と直らない**。せめて「在ることが分かる」ようにする。

🔴 **遡及検査は止めるための道具ではない**（常に終了コード 0）。既知の汚染日が
   1つあるだけでバッチや CI が落ちるようにすると、そのうち外される。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location(
    "check_race_point_sanity", REPO / "scripts" / "check_race_point_sanity.py")
CRP = importlib.util.module_from_spec(_spec)
sys.modules["check_race_point_sanity"] = CRP
_spec.loader.exec_module(CRP)


def _days(avgs: list[float], n: int = 500) -> list[tuple[str, float, int]]:
    return [(f"2026-06-{i + 1:02d}", a, n) for i, a in enumerate(avgs)]


def test_平常運転なら何も出ない():
    assert CRP.anomalies(_days([85.0, 84.0, 86.0, 85.5, 84.8, 85.2, 86.1])) == []


def test_半分以下へ落ちた日を拾う():
    """2026-06-12 の実測（平均 41.83 ↔ 直近中央値 84.34 = 50%）と同じ形。"""
    got = CRP.anomalies(_days([85.0, 84.0, 86.0, 85.5, 41.8, 85.0]))
    assert [d for d, *_ in got] == ["2026-06-05"]


def test_基準日が足りない先頭は判定しない():
    """🔴 データの始まりを「異常」と言わない（比べる相手がいない）。"""
    assert CRP.anomalies(_days([40.0, 85.0, 84.0])) == []


def test_件数が少ない日は判定しない():
    days = _days([85.0, 84.0, 86.0, 85.5], n=500)
    days.append(("2026-06-05", 10.0, CRP.MIN_ENTRIES - 1))
    assert CRP.anomalies(days) == []


def test_規則は日次ゲートと同じ定数を使う():
    """🔴 別の閾値を持つと「日次は通ったのに遡ると異常」が起きる。"""
    src = (REPO / "scripts" / "check_race_point_sanity.py").read_text(encoding="utf-8")
    assert src.count("RATIO_THRESHOLD = ") == 1
    assert src.count("BASELINE_DAYS = ") == 1


def test_遡及検査は常に終了コードゼロ():
    src = (REPO / "scripts" / "check_race_point_sanity.py").read_text(encoding="utf-8")
    i = src.index("if args.scan:")
    j = src.index("if not args.date:")
    assert "sys.exit(0)" in src[i:j]
