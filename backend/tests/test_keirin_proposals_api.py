"""入稿案API（期待値・最低/最高払戻）の計算を固定する（2026-08-11）。

## 守ること

1. オッズが**1つでも欠けたら**最低/最高払戻は None
   （一部だけで計算すると、欠けた点が最安だった場合に最低払戻を実際より
     高く見せる＝確認の役に立たない）
2. 期待値は三連単（着順あり）では出さない（この確率モデルでは扱えない）
3. 三連複の確率はレース内で正規化される
"""
from __future__ import annotations

import pytest

from src.api.keirin_router import (
    _expected_value,
    _payout_range,
    _trio_probabilities,
)

_TOP3 = {1: 70.0, 2: 60.0, 3: 50.0, 4: 40.0, 5: 30.0, 6: 25.0, 7: 20.0}


def _lines(*specs):
    return [{"bet_type": "3連複", "combo": c, "stake": s, "odds": o} for c, s, o in specs]


def test_payout_range_basic():
    lo, hi = _payout_range(_lines(("1=2=3", 600, 5.0), ("1=2=4", 400, 9.0)))
    assert lo == pytest.approx(3000.0)
    assert hi == pytest.approx(3600.0)


@pytest.mark.parametrize("bad", [None, 0])
def test_payout_range_none_when_any_odds_missing(bad):
    """1点でも欠けたら None。部分計算は最低払戻を過大に見せる。"""
    lo, hi = _payout_range(_lines(("1=2=3", 600, 5.0), ("1=2=4", 400, bad)))
    assert lo is None and hi is None


def test_payout_range_empty():
    assert _payout_range([]) == (None, None)


def test_trio_probabilities_sum_to_one():
    probs = _trio_probabilities(_TOP3)
    assert len(probs) == 35  # 7C3
    assert sum(probs.values()) == pytest.approx(1.0)
    # 3着内率が高い3車の組が最大になる
    assert max(probs, key=probs.get) == frozenset({1, 2, 3})


def test_trio_probabilities_needs_three_cars():
    assert _trio_probabilities({1: 50.0, 2: 40.0}) == {}
    assert _trio_probabilities({}) == {}


def test_expected_value_is_ratio_of_stake():
    """期待値は投資に対する見込み回収率。1.0 で収支トントン。"""
    lines = _lines(("1=2=3", 600, 5.0), ("1=2=4", 400, 9.0))
    ev = _expected_value(lines, _TOP3)
    probs = _trio_probabilities(_TOP3)
    want = (probs[frozenset({1, 2, 3})] * 600 * 5.0
            + probs[frozenset({1, 2, 4})] * 400 * 9.0) / 1000
    assert ev == pytest.approx(want)


def test_expected_value_none_for_trifecta():
    """三連単（着順あり）は扱えないので None。無理に数字を出さない。"""
    lines = [{"bet_type": "3連単", "combo": "1-2-3", "stake": 1000, "odds": 30.0}]
    assert _expected_value(lines, _TOP3) is None


def test_expected_value_none_when_odds_missing():
    lines = _lines(("1=2=3", 600, 5.0), ("1=2=4", 400, None))
    assert _expected_value(lines, _TOP3) is None


def test_expected_value_none_without_probabilities():
    lines = _lines(("1=2=3", 1000, 5.0))
    assert _expected_value(lines, {1: 50.0, 2: 40.0}) is None


def test_expected_value_unknown_combo_is_none():
    """買い目に出走していない車が含まれていたら黙って0扱いにしない。"""
    lines = _lines(("1=2=9", 1000, 5.0))
    assert _expected_value(lines, _TOP3) is None


# ── 下振れ側の最低払戻（2026-08-16 追加）─────────────────────────────
#
# 🔴 これが要る理由（実測が起点）: `min_payout` の元になる `odds` は入稿時点の
#    板が最優先だが、**朝の板は買い目の帯で確定までに大きく下がる**。
#    実入稿 705点を確定オッズと突合すると 中央 確定/表示 = 0.860・
#    45.0% が 0.8倍未満（7C は 中央 0.651・64.3%）。
#    つまり従来の最低払戻は**当たったとき実際より高い額を約束していた**。
#    keirin 側が `odds_low`（予測の整合板 × 下側25%分位）を記録するので、
#    ガミ判定はそちらを優先する。

from src.api.keirin_router import _min_payout_low  # noqa: E402


def _lines_low(*specs):
    """(combo, stake, odds, odds_low) の並び。"""
    return [{"bet_type": "3連複", "combo": c, "stake": s, "odds": o, "odds_low": lo}
            for c, s, o, lo in specs]


def test_min_payout_low_uses_the_conservative_odds():
    lines = _lines_low(("1=2=3", 2500, 5.0, 4.2), ("1=2=4", 2500, 9.0, 7.6))
    assert _min_payout_low(lines) == pytest.approx(2500 * 4.2)
    # 板由来の最低払戻より必ず低い＝楽観側へ倒れない
    assert _min_payout_low(lines) < _payout_range(lines)[0]


@pytest.mark.parametrize("bad", [None, 0])
def test_min_payout_low_is_none_when_any_point_lacks_it(bad):
    """🔴 一部だけで計算しない。欠けた点が最安なら下限を高く見せることになる
    （`_payout_range` と同じ規約）。"""
    assert _min_payout_low(_lines_low(("1=2=3", 2500, 5.0, 4.2),
                                      ("1=2=4", 2500, 9.0, bad))) is None


def test_min_payout_low_is_none_for_old_records():
    """`odds_low` を持たない記録（三連単・2026-08-16 以前の入稿）は None。

    この場合、呼び出し側は従来どおり `min_payout` でガミ判定する。
    """
    assert _min_payout_low(_lines(("1=2=3", 2500, 5.0))) is None
    assert _min_payout_low([]) is None


# ── /review のボタン整理（2026-08-16・ユーザー指定）────────────────────
#
# 操作は **入稿 / 取消 / 公開** の3つだけにする:
#   入稿 … netkeirin への入稿のみ。公開はしない
#   取消 … 入稿の取消。**公開後・締切後は NOP**
#   公開 … 入稿データの公開。**入稿前なら入稿の上で公開**
#
# 🔴 「入稿して公開」という4つ目のボタンを復活させないこと。入稿済かどうかを
#    人に意識させないのがこの整理の目的で、判断は keirin 側 CLI が持つ。

import re  # noqa: E402
from pathlib import Path  # noqa: E402

_REVIEW = (Path(__file__).resolve().parents[2]
           / "frontend" / "src" / "app" / "keirin" / "review" / "ReviewClient.tsx")


def _review_src() -> str:
    if not _REVIEW.exists():
        pytest.skip(f"frontend が見つかりません: {_REVIEW}")
    return _REVIEW.read_text(encoding="utf-8")


def test_review_has_exactly_three_bulk_buttons():
    """一括操作は 入稿 / 公開 / 取消 の3つ。**日単位と場単位の両方**で揃える。

    2026-08-19: ラベルを「この日を全件入稿（N件）」から「入稿 N」へ簡略化し、
    同時に**場ごとの公開**を足した（それまで公開だけレース単位と日単位しか無く、
    場をまとめて売り出すのに1レースずつ押す必要があった）。
    ここは**件数付きの一括ボタンを数える**形にしてあるので、ラベルの言い回しを
    変えても壊れないが、3つ以外が増えたら落ちる。
    """
    src = _review_src()
    labels = re.findall(r"^\s+(入稿|公開|取消) \{n(\w+)\}$", src, re.MULTILINE)
    # 日単位（…All）と場単位で、それぞれ 入稿/公開/取消 が1つずつ
    day = sorted(a for a, v in labels if v.endswith("All"))
    venue = sorted(a for a, v in labels if not v.endswith("All"))
    assert day == ["入稿", "公開", "取消"], f"日単位の一括ボタンが {day} になっています"
    assert venue == ["入稿", "公開", "取消"], f"場単位の一括ボタンが {venue} になっています"


def test_review_has_no_approve_and_publish_button():
    """🔴 「入稿して公開」を別ボタンとして復活させない（公開が吸収する）。"""
    src = _review_src()
    assert "入稿して公開" not in src, "「入稿して公開」ボタンが復活しています"
    assert "approveAndPublish" not in src, "承認+公開の専用アクションが残っています"


def test_cancel_is_hidden_after_publish():
    """🔴 公開後の取消は NOP。押せると「押したのに消えていない」に見える。

    netkeirin の `delete` が効くのは公開待ちまで。
    """
    src = _review_src()
    assert 'p.status !== "deleted" && p.status !== "published"' in src, (
        "公開済みで取消ボタンを隠していません")


def test_publish_button_covers_proposed():
    """公開ボタンは未入稿にも出る（入稿の上で公開するため）。"""
    src = _review_src()
    assert '(p.status === "proposed" || p.status === "submitted")' in src, (
        "公開ボタンが公開待ちだけにしか出ていません")


# ── 当日サマリー（2026-08-16・netkeirin と数字を合わせる）──────────────


def test_review_summary_is_settled_only():
    """🔴 サマリーは**確定した分だけ**を数える（netkeirin と同じ）。

    未確定を購入へ混ぜると発走前の分だけ分母が膨らみ、回収率が 0% 近くに見えて
    「負けている」と誤読する。実測（2026-08-16 09:46）で netkeirin 画面が
    「予想数 1レース / 購入 10,000円 / 回収率 0.0%」のとき、確定分だけで数えた
    こちらも 1件・10,000円で一致した（全35件で数えると 350,000円になる）。
    """
    src = Path(__file__).resolve().parents[1] / "src" / "api" / "keirin_router.py"
    code = src.read_text(encoding="utf-8")
    assert 'settled_items = [x for x in sold if x["result"] is not None]' in code
    assert '"n_races": len(settled_items)' in code, "予想数が確定数になっていません"
    assert 'bet = sum(x["result"]["bet"] for x in settled_items)' in code, (
        "購入に未確定が混ざっています")


def test_review_summary_keeps_pending_visible():
    """未確定数を落とさない（分母から外すぶん、必ず画面へ出す）。"""
    code = (Path(__file__).resolve().parents[1] / "src" / "api" / "keirin_router.py"
            ).read_text(encoding="utf-8")
    assert '"n_pending": len(sold) - len(settled_items)' in code
    src = _review_src()
    assert "未確定" in src, "画面に未確定件数を出していません"


def test_review_summary_hit_rate_excludes_gami():
    """🔴 的中率はガミ（払戻<投資）を不的中と数える（netkeirin の表示と同じ）。"""
    code = (Path(__file__).resolve().parents[1] / "src" / "api" / "keirin_router.py"
            ).read_text(encoding="utf-8")
    assert 'x["result"]["net_hit"]' in code, "素の的中率で数えています"


def test_review_result_is_null_when_unsettled():
    """🔴 未確定は None。0円と区別する（発走前に「払戻0円」は外れに見える）。"""
    code = (Path(__file__).resolve().parents[1] / "src" / "api" / "keirin_router.py"
            ).read_text(encoding="utf-8")
    assert 'it["result"] = None if got is None else' in code
    assert "未確定" in _review_src()


def test_published_is_excluded_from_cancel_counts():
    """🔴 公開済みは取消できない（netkeirin の delete は公開待ちまで）。

    全件・場別の**両方**の件数から外すこと。片方だけだと押した後に
    「成功N件/失敗M件」で初めて分かる。
    """
    src = _review_src()
    assert src.count('r.status !== "deleted" && r.status !== "published"') >= 1, (
        "場別の取消件数から公開済みを外していません")
    assert src.count('p.status !== "deleted" && p.status !== "published"') >= 2, (
        "全件取消の件数か取消ボタンから公開済みを外していません")
