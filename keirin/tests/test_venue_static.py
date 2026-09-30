"""`VENUE_STATIC`（場のバンク周長・屋内）の正本を固定する（2026-09-30）。

KEIRIN.JP の場ガイドと `wt_races.distance` の両方で確かめた値。2026-09-30 まで稼働 42 場中
21 場の周長が誤っており、モデル入力 `bank_length_enc` に流れていた。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from fix_venue_info_wt import diff_rows  # noqa: E402
from src.database import VENUE_STATIC  # noqa: E402

#: 2026-09-30 に KEIRIN.JP で確認した稼働場の周長（前橋 335 は 333 の区分）。
OFFICIAL_BANK = {
    "11": 400, "12": 400, "13": 400, "21": 400, "22": 333, "23": 400, "24": 500, "25": 500,
    "26": 400, "27": 400, "28": 400, "31": 333, "34": 400, "35": 400, "36": 333, "37": 333,
    "38": 400, "42": 400, "43": 400, "44": 400, "45": 400, "46": 333, "47": 400, "48": 400,
    "51": 400, "53": 333, "54": 400, "55": 400, "56": 400, "61": 400, "62": 400, "63": 333,
    "71": 400, "73": 400, "74": 500, "75": 400, "81": 400, "83": 400, "84": 400, "85": 400,
    "86": 400, "87": 400,
}


def test_bank_length_matches_official():
    """稼働場の周長が公式値と一致する。"""
    wrong = {c: (VENUE_STATIC[c][1], b) for c, b in OFFICIAL_BANK.items() if VENUE_STATIC[c][1] != b}
    assert wrong == {}


def test_domes_are_indoor():
    """前橋（グリーンドーム）・小倉（メディアドーム）は屋内。"""
    assert VENUE_STATIC["22"][2] == 1
    assert VENUE_STATIC["81"][2] == 1


def test_bank_length_values_are_known_classes():
    """周長は 250/333/400/500 のいずれか（bank_length_enc = 周長/100 の前提）。"""
    assert {v[1] for v in VENUE_STATIC.values()} <= {250, 333, 400, 500}


def test_diff_rows_reports_only_mismatches():
    """食い違う場だけを返し、正本に無い場コードは触らない。"""
    rows = [
        {"venue_code": "24", "bank_length": 333, "is_indoor": 0},   # 誤り
        {"venue_code": "27", "bank_length": 400, "is_indoor": 0},   # 正しい
        {"venue_code": "22", "bank_length": 333, "is_indoor": None},  # 屋内が漏れ
        {"venue_code": "99", "bank_length": 123, "is_indoor": 0},   # 正本に無い
    ]
    got = {d[0]: d for d in diff_rows(rows)}
    assert set(got) == {"24", "22"}
    assert got["24"][3] == 500
    assert got["22"][5] == 1
