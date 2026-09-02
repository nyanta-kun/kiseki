"""WF（重勝式 WIN5）パーサの検査。

## なぜ必要か

WIN5 のレイアウトは 7215 バイトのうち **7047 バイトが払戻情報の繰返（29バイト × 243）**
で、境界を1つ間違えても例外は出ない。読み違えたバイト位置から数字が取れてしまい、
「払戻金 1,234,560 円」のような**もっともらしい値**が静かに DB に入る。

さらに仕様には引っかかりやすい点が3つある:

1. **中止レコード（データ区分9）は 組番=0000000000 / 払戻金=000000100 /
   的中票数=0000000000 を返す。** 払戻金 100 は**返還**であって的中ではない。
   素直に取り込むと「払戻100円の的中」が入る
2. **`_i()` は 0 を None に潰す。** WIN5 はキャリーオーバー 0 円・的中票数 0 が
   意味を持つ値なので `_n()` を使わなければならない
3. **項番7（対象レース）には年月日が無い**（場・回・日目・R のみ）。16桁の
   race_id は項番4・5 の開催年月日と結合して作る

レイアウト出典: `docs/sources/JV-Data4901.pdf`「３０．重勝式(WIN5)」
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.importers.jvlink_parser import parse_wf  # noqa: E402

RECORD_LEN = 7215
PAY_FROM = 167
PAY_LEN = 29
PAY_MAX = 243


def _wf_record(
    *,
    data_kubun: str = "7",
    held_date: str = "20260830",
    legs: list[tuple[str, str, str, str]] | None = None,
    valid_votes: list[int] | None = None,
    sold_votes: int = 12345678,
    refund: str = "0",
    void: str = "0",
    no_hit: str = "0",
    carryover_start: int = 0,
    carryover_balance: int = 0,
    payouts: list[tuple[str, int, int]] | None = None,
) -> str:
    """WF レコードを仕様どおりのバイト位置で合成する。

    実機からレコードを採取できるようになったら、そちらを正本にしてこの合成は
    境界値テスト専用にすること（`test_parse_wh.py` が実レコードを使っているのと同じ方針）。
    """
    legs = legs or [("06", "03", "05", "10"), ("06", "03", "05", "11"),
                    ("05", "04", "06", "10"), ("05", "04", "06", "11"),
                    ("08", "02", "04", "11")]
    valid_votes = valid_votes if valid_votes is not None else [0] * 5
    payouts = payouts if payouts is not None else [("0511030814", 1234560, 7)]

    buf = ["\x00"] * RECORD_LEN

    def put(pos: int, text: str) -> None:  # pos は 1-indexed
        for i, ch in enumerate(text):
            buf[pos - 1 + i] = ch

    put(1, "WF")
    put(3, data_kubun)
    put(4, "20260831")                      # データ作成年月日（≠開催日）
    put(12, held_date[:4])                  # 開催年
    put(16, held_date[4:])                  # 開催月日
    put(20, "  ")                           # 予備
    for i, (course, kai, day, rno) in enumerate(legs):
        put(22 + i * 8, f"{course}{kai}{day}{rno}")
    put(62, " " * 6)                        # 予備
    put(68, f"{sold_votes:011d}")
    for i, v in enumerate(valid_votes):
        put(79 + i * 11, f"{v:011d}")
    put(134, refund)
    put(135, void)
    put(136, no_hit)
    put(137, f"{carryover_start:015d}")
    put(152, f"{carryover_balance:015d}")
    for i in range(PAY_MAX):
        b = PAY_FROM + i * PAY_LEN
        if i < len(payouts):
            combo, pay, votes = payouts[i]
            put(b, f"{combo}{pay:09d}{votes:010d}")
        else:
            put(b, " " * PAY_LEN)           # 未使用枠は空白（初期値 sp）
    put(7214, "\r\n")
    return "".join(buf)


def test_basic_fields() -> None:
    r = parse_wf(_wf_record())
    assert r is not None
    assert r["rec_id"] == "WF"
    assert r["data_kubun"] == "7"
    assert r["held_date"] == "20260830"
    assert r["created_date"] == "20260831"
    assert r["sold_votes"] == 12345678
    assert r["refund_flag"] is False and r["void_flag"] is False
    assert r["no_hit_flag"] is False


def test_jravan_race_id_is_16_chars() -> None:
    """項番7 に年月日が無いので、開催年月日と結合して16桁にする。"""
    r = parse_wf(_wf_record())
    assert r is not None
    assert len(r["legs"]) == 5
    first = r["legs"][0]
    assert first["leg_no"] == 1
    assert first["jravan_race_id"] == "20260830" + "06" + "03" + "05" + "10"
    assert len(first["jravan_race_id"]) == 16
    assert r["legs"][4]["jravan_race_id"].endswith("0802" + "04" + "11")


def test_valid_votes_follow_leg_order() -> None:
    """有効票数（項番10）は対象レース情報（項番7）と同じ順で入る。"""
    r = parse_wf(_wf_record(valid_votes=[0, 0, 4321, 0, 0]))
    assert r is not None
    assert [leg["valid_votes"] for leg in r["legs"]] == [0, 0, 4321, 0, 0]


def test_zero_is_preserved_not_none() -> None:
    """🔴 キャリーオーバー0円・的中票数0を None に潰してはいけない。

    `_i()` を使うとここが None になる（0 を欠損として扱う実装のため）。
    「繰越なし」と「取れなかった」が区別できなくなる。
    """
    r = parse_wf(_wf_record(carryover_start=0, carryover_balance=0,
                            payouts=[("0511030814", 1234560, 0)]))
    assert r is not None
    assert r["carryover_start"] == 0
    assert r["carryover_balance"] == 0
    assert r["payouts"][0]["hit_votes"] == 0


def test_carryover_is_read_from_correct_offsets() -> None:
    """初期(137-151)と残高(152-166)を取り違えていないこと。"""
    r = parse_wf(_wf_record(carryover_start=111_111_111,
                            carryover_balance=222_222_222))
    assert r is not None
    assert r["carryover_start"] == 111_111_111
    assert r["carryover_balance"] == 222_222_222


def test_cancelled_record_yields_no_payout() -> None:
    """🔴 中止（区分9）の払戻金100は返還であって的中ではない。

    仕様の特記事項: 組番=0000000000 / 払戻金=000000100 / 的中票数=0000000000。
    これを的中として取り込むと「払戻100円のWIN5的中」が DB に入る。
    """
    r = parse_wf(_wf_record(data_kubun="9",
                            payouts=[("0000000000", 100, 0)]))
    assert r is not None
    assert r["data_kubun"] == "9"
    assert r["payouts"] == []


def test_deleted_record_returns_none() -> None:
    """データ区分0（該当レコード削除）は取り込まない。"""
    assert parse_wf(_wf_record(data_kubun="0")) is None


def test_multiple_payouts_and_243rd_slot() -> None:
    """243枠目まで読み、244枠目（＝レコード区切）を読まないこと。

    末尾の払戻枠は 167 + 29*242 = 7185 から 7213。7214-7215 は CR/LF。
    """
    many = [(f"{i:010d}", 1000 + i, i) for i in range(1, PAY_MAX + 1)]
    r = parse_wf(_wf_record(payouts=many))
    assert r is not None
    assert len(r["payouts"]) == PAY_MAX
    assert r["payouts"][0]["combination"] == "0000000001"
    assert r["payouts"][-1]["combination"] == f"{PAY_MAX:010d}"
    assert r["payouts"][-1]["payout"] == 1000 + PAY_MAX


def test_unused_slots_are_skipped() -> None:
    """未使用枠（空白）と全ゼロ組番は落とす。"""
    r = parse_wf(_wf_record(payouts=[("0511030814", 1234560, 7)]))
    assert r is not None
    assert len(r["payouts"]) == 1
    assert r["payouts"][0] == {"combination": "0511030814",
                               "payout": 1234560, "hit_votes": 7}


def test_no_hit_flag_with_zero_payout() -> None:
    """的中無（項番13=1）のとき払戻金は 000000000 で提供される。

    組番は「的中無の場合も設定」と仕様にあるが、実際に全ゼロで来た場合は
    払戻として取り込まない（中止と同じ扱い）。
    """
    r = parse_wf(_wf_record(no_hit="1", payouts=[("0000000000", 0, 0)]))
    assert r is not None
    assert r["no_hit_flag"] is True
    assert r["payouts"] == []


def test_rejects_other_record_types() -> None:
    assert parse_wf("RA" + "0" * 100) is None
    assert parse_wf("SE" + "0" * 100) is None


def test_rejects_short_record() -> None:
    assert parse_wf("WF" + "0" * 50) is None
