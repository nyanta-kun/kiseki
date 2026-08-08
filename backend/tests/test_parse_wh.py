"""WH（速報馬体重・0B11）パーサの検査。

## 背景（実際に起きた抜け）

2026-08-08 まで 0B11 は本番で一度も取り込まれていなかった。
`/api/import/weights` は受け取ったレコードを RaceImporter へ渡すだけで、
RaceImporter が見るのは rec_id が RA/SE のものだけ。0B11 が返すのは全て WH
なので、23件/回が毎回まるごと捨てられて 200 が返っていた。

`race_entries.horse_weight` は 0B12（確定成績）経由で **1〜3着馬にしか**
入らず、総合指数 v27 の特徴量 `horse_weight` / `weight_change` は当日の算出で
常に欠損していた（レース内 sd が実測で約半分に潰れる）。

レイアウトは本番の実レコードから確認した（2026-08-08 札幌5R）:
    "...010105030000000001ホウオウチャールズ…456-00602ジングアップ…448+002"
"""

from __future__ import annotations

from src.importers.jvlink_parser import parse_wh

_NAME_BYTES = 36  # 馬名は全角18文字固定


def _pad_name(name: str) -> str:
    """馬名を 36 バイト（全角18文字）へ全角空白で右詰めする。"""
    filler = (_NAME_BYTES - len(name.encode("cp932"))) // 2
    return name + "　" * filler


def _wh_record(entries: list[tuple[int, str, str, str, str]], race_num: str = "03") -> str:
    """WH レコードを組み立てて JVRead と同じ「SJIS を Latin-1 として持つ」形で返す。

    entries: [(馬番, 馬名, 体重3桁, 符号1, 増減3桁), ...]
    """
    body = "".join(
        f"{num:02d}{_pad_name(name)}{weight}{sign}{diff}"
        for num, name, weight, sign, diff in entries
    )
    rec = (
        "WH1" + "20260808" + "2026" + "0808" + "01" + "01" + "05" + race_num
        + "00000000" + body
    )
    return rec.encode("cp932").decode("latin-1")


def test_parses_real_production_record():
    """本番から採取した実レコードと同じ並びを正しく読む。"""
    raw = _wh_record([
        (1, "ホウオウチャールズ", "456", "-", "006"),
        (2, "ジングアップ", "448", "+", "002"),
    ])
    out = parse_wh(raw)

    assert out is not None
    assert out["jravan_race_id"] == "2026080801010503"
    assert out["race_date"] == "20260808"
    assert out["entries"] == [
        {"horse_number": 1, "horse_name": "ホウオウチャールズ",
         "horse_weight": 456, "weight_change": -6},
        {"horse_number": 2, "horse_name": "ジングアップ",
         "horse_weight": 448, "weight_change": 2},
    ]


def test_blank_sign_means_no_change():
    """符号が空白なら増減0として扱う（parse_se と同じ規約）。"""
    out = parse_wh(_wh_record([(1, "ルージュエピック", "486", " ", "000")]))
    assert out is not None
    assert out["entries"][0]["horse_weight"] == 486
    assert out["entries"][0]["weight_change"] == 0


def test_unmeasurable_and_scratched_become_none():
    """999=計量不能 / 000=出走取消 は体重 None（既存値を潰さないため）。"""
    out = parse_wh(_wh_record([
        (1, "ケイリョウフノウ", "999", " ", "000"),
        (2, "シュッソウトリケシ", "000", " ", "000"),
        (3, "セイジョウ", "470", "+", "004"),
    ]))
    assert out is not None
    weights = [(e["horse_number"], e["horse_weight"]) for e in out["entries"]]
    assert weights == [(1, None), (2, None), (3, 470)]


def test_stops_at_first_empty_slot():
    """出走頭数に満たない残り枠で打ち切る（18頭ぶん読み進めない）。"""
    out = parse_wh(_wh_record([(1, "イットウ", "500", "+", "000")]))
    assert out is not None
    assert len(out["entries"]) == 1


def test_rejects_non_wh_record():
    """SE など別レコードを渡しても取り違えない。"""
    se_like = ("SE1" + "20260808" + "2026" + "0808" + "01" + "01" + "05" + "03"
               + "0" * 100)
    assert parse_wh(se_like) is None


def test_rejects_truncated_record():
    """ヘッダーに満たない短いレコードは None。"""
    assert parse_wh("WH1202608") is None
