"""`scripts/scrape_netkeirin_sales.py` のパーサ検査。

日別（集計ID 8桁）とレース別（12桁）は**同じテーブル・同じ列構成**で返ってくる。
違うのは集計IDの桁数だけなので、振り分けを間違えても列は全部埋まり、
値も自然に見えてしまう。ここで振り分け自体を固定する。
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

# このスクリプトは VPS 上の **keirin の venv** で動くため、`requests` は backend の
# 依存に入っていない（CI の backend ジョブでは import できない）。検査したいのは
# HTTP を伴わないパース部分だけなので、未導入なら最小のスタブを挿してから読み込む。
# ⚠️ ここで「未導入なら skip」にしてはいけない。CI で一度も走らない検査は
#    通っているように見えるだけで何も守らない。
try:  # pragma: no cover - 実行環境依存
    import requests  # noqa: F401
except ModuleNotFoundError:  # pragma: no cover - CI の backend ジョブがこちら
    _stub = types.ModuleType("requests")
    # スクリプトが module レベルで参照するのはこの2つだけ（型注釈と except 節）。
    _stub.Session = object  # type: ignore[attr-defined]
    _stub.RequestException = Exception  # type: ignore[attr-defined]
    sys.modules["requests"] = _stub

_SPEC = importlib.util.spec_from_file_location(
    "scrape_netkeirin_sales",
    Path(__file__).resolve().parents[1] / "scripts" / "scrape_netkeirin_sales.py",
)
assert _SPEC and _SPEC.loader
scraper = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(scraper)


# 実ページの列順（_COLUMNS）に合わせた 31 個の指標セル。値は識別しやすい連番にする。
def _metric_cells() -> list[str]:
    return [
        "1",        # n_predictions
        "1",        # n_predictions_staked
        "1",        # n_hits_incl_garami
        "0",        # n_hits_excl_garami
        "0",        # n_miss
        "10,000",   # stake_amount
        "6,700",    # payout_amount
        "100.0%",   # hit_rate_pct
        "67.0%",    # recovery_rate_pct
        "3",        # n_sold
        "900",      # sold_points
        "600",      # sold_paid_points
        "300",      # avg_sold_points
        "152",      # avg_sold_minutes
        "3",        # avg_sold_hour
        *["0%"] * 3,       # ◎1/2/3着率
        "1", *["0%"] * 3,  # 〇件数 + 1/2/3着率
        "0", *["0%"] * 3,  # ▲件数 + 1/2/3着率
        "0",               # ◎〇▲件数
        *["0%"] * 4,       # 遷移率4本
    ]


def _table(rows: list[list[str]]) -> str:
    header = "<tr>" + "".join(f"<th>{h}</th>" for h in ["集計ID", "集計名", *["x"] * 31]) + "</tr>"
    body = "".join(
        "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>" for cells in rows
    )
    return f"<html><body><table>{header}{body}</table></body></html>"


def test_指標セルの数が実ページの列定義と一致する():
    """このテストが落ちたら、サイトの列が増減したか _metric_cells がずれている。"""
    assert len(_metric_cells()) == len(scraper._COLUMNS)


def test_レース別は集計IDを場コードとレース番号へ分解する():
    html = _table([["202608104808", "08/10 四日市 Ａ級 準決勝", *_metric_cells()]])
    # fetch_race_rows は HTTP を伴うので、パース部分だけを直接検証する
    parsed = scraper._parse_table(html, r"\d{12}")
    assert len(parsed) == 1
    agg_id, agg_name, metrics = parsed[0]
    assert agg_id == "202608104808"
    assert agg_name == "08/10 四日市 Ａ級 準決勝"
    assert metrics["sold_paid_points"] == 600
    assert metrics["stake_amount"] == 10000
    assert metrics["recovery_rate_pct"] == pytest.approx(67.0)


def test_レース別の行は日別のパーサに拾われない():
    """8桁パターンが12桁IDに部分一致すると、レース別行が日別テーブルへ流れ込む。
    `re.fullmatch` をやめた瞬間に起きるので、ここで固定する。"""
    html = _table([["202608104808", "08/10 四日市 Ａ級 準決勝", *_metric_cells()]])
    assert scraper._parse_table(html, r"\d{8}") == []


def test_日別の行はレース別のパーサに拾われない():
    html = _table([["20260810", "日別", *_metric_cells()]])
    assert scraper._parse_table(html, r"\d{12}") == []
    assert len(scraper._parse_table(html, r"\d{8}")) == 1


def test_列数が足りない行は取り込まない():
    html = _table([["202608104808", "08/10 四日市", "1", "2", "3"]])
    assert scraper._parse_table(html, r"\d{12}") == []


def test_race_keyはpicks_historyと同じゼロ埋め形式():
    """`202608100801` → `20260810_08_01`。ここがずれると join が全滅して
    ランクも開催時間帯も付かない（が、売上は出るので気づきにくい）。"""
    assert scraper.race_fields("202608100801") == {
        "race_id": "202608100801",
        "race_date": "20260810",
        "venue_code": "08",
        "race_no": 1,
        "race_key": "20260810_08_01",
    }
    # 2桁レース番号もゼロ埋めのまま（11R が `_11`、`_1` にならない）
    assert scraper.race_fields("202608104611")["race_key"] == "20260810_46_11"


def test_UPSERT文は日別とレース別で別テーブルを指す():
    assert "keirin.netkeirin_sales_daily" in scraper.UPSERT_SQL
    assert "keirin.netkeirin_sales_race" in scraper.UPSERT_RACE_SQL
    # レース別は race_id が競合キー（sale_date ではない）
    assert "ON CONFLICT (race_id)" in scraper.UPSERT_RACE_SQL
