"""調教レコードパーサー（parse_hc / parse_wc）のテスト

HC（坂路, 60バイト）・WC（ウッドチップ, 105バイト）の固定長レイアウトに対し、
1-indexed バイト位置での抽出・1/10秒タイム変換・測定不良 None 化を検証する。
"""

from __future__ import annotations

from decimal import Decimal

from src.importers.jvlink_parser import parse_hc, parse_record, parse_wc


def _build_hc() -> str:
    """有効な HC（坂路）レコードを 60 バイトで組み立てる。"""
    s = (
        "HC"          # 1-2  レコード種別
        "1"           # 3    データ区分
        "20260530"    # 4-11 作成年月日
        "1"           # 12   トレセン区分（栗東）
        "20260529"    # 13-20 調教年月日
        "0630"        # 21-24 調教時刻
        "2021104135"  # 25-34 血統登録番号
        "0532"        # 35-38 4F合計 → 53.2
        "138"         # 39-41 ラップ800-600 → 13.8
        "0394"        # 42-45 3F合計 → 39.4
        "128"         # 46-48 ラップ600-400 → 12.8
        "0266"        # 49-52 2F合計 → 26.6
        "131"         # 53-55 ラップ400-200 → 13.1
        "135"         # 56-58 ラップ200-0 → 13.5
        "\r\n"        # 59-60 レコード区切
    )
    assert len(s) == 60, len(s)
    return s


def _build_wc() -> str:
    """有効な WC（ウッドチップ）レコードを 105 バイトで組み立てる（6Fから計測）。"""
    s = (
        "WC"          # 1-2
        "1"           # 3
        "20260530"    # 4-11
        "0"           # 12 美浦
        "20260529"    # 13-20
        "0700"        # 21-24
        "2020102222"  # 25-34 血統登録番号
        "1"           # 35 コース B
        "1"           # 36 馬場周り 左
        "0"           # 37 予備
        "0000"        # 38-41 10F合計 → None
        "000"         # 42-44 → None
        "0000"        # 45-48 9F → None
        "000"         # 49-51 → None
        "0000"        # 52-55 8F → None
        "000"         # 56-58 → None
        "0000"        # 59-62 7F → None
        "000"         # 63-65 → None
        "0824"        # 66-69 6F合計 → 82.4
        "138"         # 70-72 ラップ1200-1000 → 13.8
        "0686"        # 73-76 5F合計 → 68.6
        "137"         # 77-79 ラップ1000-800 → 13.7
        "0549"        # 80-83 4F合計 → 54.9
        "135"         # 84-86 ラップ800-600 → 13.5
        "0414"        # 87-90 3F合計 → 41.4
        "133"         # 91-93 ラップ600-400 → 13.3
        "0281"        # 94-97 2F合計 → 28.1
        "138"         # 98-100 ラップ400-200 → 13.8
        "131"         # 101-103 ラップ200-0 → 13.1
        "\r\n"        # 104-105
    )
    assert len(s) == 105, len(s)
    return s


def test_parse_hc_basic() -> None:
    r = parse_hc(_build_hc())
    assert r is not None
    assert r["rec_id"] == "HC"
    assert r["center"] == "1"
    assert r["training_date"] == "20260529"
    assert r["training_time"] == "0630"
    assert r["blood_reg_no"] == "2021104135"
    assert r["time_4f"] == Decimal("53.2")
    assert r["lap_800_600"] == Decimal("13.8")
    assert r["time_3f"] == Decimal("39.4")
    assert r["lap_600_400"] == Decimal("12.8")
    assert r["time_2f"] == Decimal("26.6")
    assert r["lap_400_200"] == Decimal("13.1")
    assert r["lap_200_0"] == Decimal("13.5")


def test_parse_hc_deleted_record() -> None:
    """データ区分=0（削除レコード）は None。"""
    rec = "HC" + "0" + _build_hc()[3:]
    assert parse_hc(rec) is None


def test_parse_hc_measurement_failure() -> None:
    """測定不良（全ゼロ）の区間は None になる。"""
    rec = _build_hc()
    # 4F合計を "0000" に差し替え（pos 35-38）
    rec = rec[:34] + "0000" + rec[38:]
    r = parse_hc(rec)
    assert r is not None
    assert r["time_4f"] is None
    # 他の区間は健在
    assert r["time_3f"] == Decimal("39.4")


def test_parse_hc_too_short() -> None:
    assert parse_hc("HC123") is None


def test_parse_wc_basic() -> None:
    r = parse_wc(_build_wc())
    assert r is not None
    assert r["rec_id"] == "WC"
    assert r["center"] == "0"
    assert r["blood_reg_no"] == "2020102222"
    assert r["wood_course"] == "1"
    assert r["wood_direction"] == "1"
    # 長距離側は測定なし
    assert r["time_10f"] is None
    assert r["time_7f"] is None
    # 計測区間
    assert r["time_6f"] == Decimal("82.4")
    assert r["lap_1200_1000"] == Decimal("13.8")
    assert r["time_5f"] == Decimal("68.6")
    assert r["time_4f"] == Decimal("54.9")
    assert r["lap_800_600"] == Decimal("13.5")
    assert r["time_3f"] == Decimal("41.4")
    assert r["time_2f"] == Decimal("28.1")
    assert r["lap_200_0"] == Decimal("13.1")


def test_parse_record_dispatch() -> None:
    """parse_record が HC/WC を正しく振り分ける。"""
    hc = parse_record({"rec_id": "HC", "data": _build_hc()})
    assert hc is not None and hc["_rec_id"] == "HC"
    wc = parse_record({"rec_id": "WC", "data": _build_wc()})
    assert wc is not None and wc["_rec_id"] == "WC"
