"""POG 結果通知の組み立てを固定する。

なぜ必要か（2026-09-09・統合 Phase 5 の 5f）:
    通知は**送ってしまうと取り消せない**。しかも 10 分ごとに走るので、
    重複・欠落・表示崩れがそのまま利用者に届く。DB を要さない部分
    （embed の組み立て）だけでも機械的に固定しておく。
"""

from __future__ import annotations

from datetime import date

from src.services.pog_notify import build_embed


def _rows():
    return [
        {"course_name": "阪神", "course_code": "JHSN", "race_no": 2,
         "horse_name": "ブックオブケルズ", "finish_position": 2, "owner_name": "松"},
        {"course_name": "阪神", "course_code": "JHSN", "race_no": 5,
         "horse_name": "ハートノート", "finish_position": 1, "owner_name": "フクシゲ"},
        {"course_name": "中山", "course_code": "JNKY", "race_no": 5,
         "horse_name": "ヴェトロテンペスタ", "finish_position": 7, "owner_name": None},
    ]


def test_競馬場ごとにまとめる():
    e = build_embed("赤兎", date(2026, 9, 6), _rows())
    names = [f["name"] for f in e["fields"]]
    assert names == ["📍 阪神", "📍 中山"]
    assert e["fields"][0]["value"].count("\n") == 1  # 阪神は2件


def test_着順で絵文字が変わる():
    e = build_embed("赤兎", date(2026, 9, 6), _rows())
    hanshin = e["fields"][0]["value"]
    assert "🥈" in hanshin and "2着" in hanshin
    assert "🥇" in hanshin and "1着" in hanshin
    assert "📍" in e["fields"][1]["value"]  # 4着以下


def test_馬主名は括弧付きで出す():
    e = build_embed("赤兎", date(2026, 9, 6), _rows())
    assert "**(松)**" in e["fields"][0]["value"]


def test_馬主名が無ければ括弧を出さない():
    e = build_embed("赤兎", date(2026, 9, 6), _rows())
    assert "()" not in e["fields"][1]["value"]


def test_タイトルと日付():
    e = build_embed("赤兎", date(2026, 9, 6), _rows())
    assert e["title"] == "🏆 赤兎 POG馬結果速報"
    assert "2026年09月06日" in e["description"]


def test_タイムスタンプにタイムゾーンが付く():
    """🔴 offset 無しの ISO8601 は Discord に UTC と解釈され表示が +9h ずれる。

    移設元が一度踏んで直した箇所（コメントが残っている）。
    """
    e = build_embed("赤兎", date(2026, 9, 6), _rows())
    ts = e["timestamp"]
    assert ts.endswith("+00:00") or ts.endswith("Z"), ts


# --------------------------------------------------------------------------
# 出走想定・枠順確定
# --------------------------------------------------------------------------

from src.services.pog_notify import build_barrier_embed, build_entry_embed  # noqa: E402


def _entries():
    return [
        {"course_name": "阪神", "race_no": 2, "horse_name": "ショウナンバトレニ",
         "jockey": "武豊", "barrier": None, "horse_no": None, "weight": None,
         "owner_name": "友"},
        {"course_name": "中山", "race_no": 5, "horse_name": "ルーメンルーナエ",
         "jockey": None, "barrier": 3, "horse_no": 6, "weight": 55,
         "owner_name": None},
    ]


def test_出走想定は頭数を出す():
    e = build_entry_embed("赤兎", date(2026, 9, 12), _entries())
    assert e["title"] == "🏇 赤兎 POG馬出走想定"
    assert "計**2頭**" in e["description"]
    assert e["color"] == 0x3498DB


def test_出走想定は枠と騎手を持っている分だけ出す():
    e = build_entry_embed("赤兎", date(2026, 9, 12), _entries())
    hanshin = e["fields"][0]["value"]
    nakayama = e["fields"][1]["value"]
    assert "武豊" in hanshin and "枠)" not in hanshin      # 枠は未定
    assert "(3枠)" in nakayama                              # 騎手は未定
    assert "**(友)**" in hanshin and "()" not in nakayama


def test_枠順確定は2行で出す():
    """馬名の行と、枠・馬番・騎手・斤量の行。移設元と同じ形。"""
    e = build_barrier_embed("赤兎", date(2026, 9, 12), _entries())
    assert e["title"] == "🎯 赤兎 POG馬枠順確定"
    assert e["color"] == 0xE74C3C
    nakayama = e["fields"][1]["value"].split("\n")
    assert len(nakayama) == 2
    assert "3枠6番" in nakayama[1] and "55kg" in nakayama[1]


def test_枠順が無い項目は未定と出す():
    """🔴 None をそのまま埋めると 'None枠None番' になる。"""
    e = build_barrier_embed("赤兎", date(2026, 9, 12), _entries())
    hanshin = e["fields"][0]["value"]
    assert "未定枠未定番" in hanshin
    assert "None" not in hanshin


def test_日付をまたぐ通知もタイムゾーン付き():
    for build in (build_entry_embed, build_barrier_embed):
        ts = build("赤兎", date(2026, 9, 12), _entries())["timestamp"]
        assert ts.endswith("+00:00") or ts.endswith("Z"), ts
