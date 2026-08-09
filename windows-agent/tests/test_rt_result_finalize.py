"""0B12（速報成績）の打ち切り判定を固定するテスト。

2026-08-08 の障害: realtime は HR（払戻）を見た瞬間にそのレースを
`finalized_race_keys` へ入れて以後の 0B12 問い合わせを止めていた。
0B12 は確定直後の応答に「RA + 上位3頭の SE + HR」しか載せず残りの SE は
遅れて届くため、全36レースが3頭分しか DB に入らなかった。
（蓄積系 JVOpen が生きていた頃は週次取込が埋めていたので表面化しなかった）

打ち切り条件は「HR を見た ∧ その巡回で新しい RA/SE が1件も無かった」であること。
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jvlink_agent import classify_rt_result_records  # noqa: E402


def _rec(rec_id: str, uniq: str) -> dict:
    """先頭30文字がユニーク識別に使われるため、そこまでを埋めたダミーを作る。"""
    head = (rec_id + uniq).ljust(30, "0")
    return {"rec_id": rec_id, "data": head + "payload"}


def _classify(records: list[dict], seen: set[str]):
    pending_result: set[str] = set()
    pending_payout: set[str] = set()
    ra_se, hr, found_new, payout_seen = classify_rt_result_records(
        records, seen, pending_result, pending_payout
    )
    # 呼び出し側は POST 成功後に seen へ移す。ここでは成功したものとして進める。
    seen.update(pending_result)
    seen.update(pending_payout)
    return ra_se, hr, found_new, payout_seen


def test_hr_alone_does_not_signal_completion() -> None:
    """HR と上位3頭の SE が同時に来た最初の応答では打ち切ってはいけない。"""
    seen: set[str] = set()
    first = [_rec("RA", "R1")] + [_rec("SE", f"R1H{i:02d}") for i in range(3)] + [_rec("HR", "R1")]

    ra_se, hr, found_new, payout_seen = _classify(first, seen)

    assert len(ra_se) == 4  # RA 1 + SE 3
    assert len(hr) == 1
    assert payout_seen is True
    # found_new が True である限り呼び出し側は finalize しない
    assert found_new is True


def test_late_arriving_se_is_picked_up_then_finalizes() -> None:
    """遅れて届いた残りの SE を拾い、出尽くした次の巡回で初めて打ち切れる。"""
    seen: set[str] = set()
    full_se = [_rec("SE", f"R1H{i:02d}") for i in range(14)]

    # 1巡目: RA + 上位3頭 + HR
    _classify([_rec("RA", "R1")] + full_se[:3] + [_rec("HR", "R1")], seen)

    # 2巡目: 全14頭が載った応答。未取得の11頭を拾うので、まだ打ち切らない。
    ra_se, _, found_new, payout_seen = _classify(
        [_rec("RA", "R1")] + full_se + [_rec("HR", "R1")], seen
    )
    assert len(ra_se) == 11
    assert found_new is True
    assert payout_seen is True

    # 3巡目: 新しい RA/SE は無い → 打ち切ってよい
    ra_se, hr, found_new, payout_seen = _classify(
        [_rec("RA", "R1")] + full_se + [_rec("HR", "R1")], seen
    )
    assert ra_se == []
    assert hr == []
    assert found_new is False
    assert payout_seen is True


def test_duplicate_keys_within_one_response_are_collapsed() -> None:
    """同一応答に同じレコードが重複しても1件しか送らない。"""
    seen: set[str] = set()
    dup = _rec("SE", "R1H01")

    ra_se, _, found_new, _ = _classify([dup, dup, dup], seen)

    assert len(ra_se) == 1
    assert found_new is True


def test_no_records_means_nothing_to_finalize() -> None:
    """未発走等で0件が返った場合、払戻も見ていないので打ち切り判断は起きない。"""
    seen: set[str] = set()

    ra_se, hr, found_new, payout_seen = _classify([], seen)

    assert ra_se == []
    assert hr == []
    assert found_new is False
    assert payout_seen is False


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
