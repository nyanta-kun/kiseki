"""7A の市場合意枠（overlap==2・2026-08-11 追加）のテスト。

守るのは3点:
  1. **7B のスライスを絶対に含めない**（netkeirin の優先順位が 7A > 7B なので、
     含めると 7A が 7B のレースを奪う）
  2. 形は 7A と同じ（axis_sum だけ不合格・entropy は合格）
  3. `RANK_7A_MARKET_AGREE = False` で完全に止まる
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import src.strategy_wt as sw  # noqa: E402
from src.strategy_wt import (  # noqa: E402
    RANK_7A_MARKET_AGREE_FROM,
    RANK_7B_RACE_TYPES,
    rank_7a_daily_select,
    rank_7a_is_7b_slice,
    rank_7a_market_agree_pool,
    rank_7a_race_date,
)

AX_NG = sw.RANK_7S_AXIS_SUM_MAX + 0.05      # axis_sum 不合格
AX_OK = sw.RANK_7S_AXIS_SUM_MAX - 0.05
ENT_OK = sw.RANK_7S_ENTROPY_MAX - 0.05
ENT_NG = sw.RANK_7S_ENTROPY_MAX + 0.05


#: 有効化日以降の race_key（日付は race_key の先頭8桁から導かれる）
_ON = RANK_7A_MARKET_AGREE_FROM.replace("-", "") + "_34_04"


def c(**kw) -> dict:
    base = dict(race_key=_ON, axis_sum=AX_NG, entropy=ENT_OK, wt_overlap_n=2,
                order_disagree=False, race_type="一般")
    base.update(kw)
    return base


# ── 1. 7B との排他 ───────────────────────────────────────────────────────


def test_excludes_7b_slice():
    """🔴 7B が取るスライス（順序一致 ∧ 準決勝）は含めない。

    優先順位が 7A > 7B なので、含めると 7A が 7B のレースを奪う。
    7B は3窓で ROI 82〜83% を保った唯一の切り口なので食わせてはいけない。
    """
    seven_b = c(order_disagree=False, race_type=RANK_7B_RACE_TYPES[0])
    assert rank_7a_is_7b_slice(seven_b) is True
    assert rank_7a_market_agree_pool([seven_b]) == []


def test_includes_same_race_type_when_order_disagrees():
    """準決勝でも**順序不一致**なら 7B は取らないので、こちらが拾う。"""
    x = c(order_disagree=True, race_type=RANK_7B_RACE_TYPES[0])
    assert rank_7a_is_7b_slice(x) is False
    assert rank_7a_market_agree_pool([x]) == [x]


def test_includes_other_race_types():
    """順序一致でも race_type が 7B 指定外なら拾う（川崎4R=一般・6R=選抜の型）。"""
    for rt in ("一般", "選抜", "特選", "チャレンジ準決勝"):
        x = c(race_type=rt)
        assert rank_7a_market_agree_pool([x]) == [x], rt


def test_7b_race_types_matched_exactly_not_substring():
    """🔴 部分一致にしない。"チャレンジ準決勝" は 7B の母集団ではない。

    部分一致にすると未検証の母集団が約30%混入する（7B 定義部のコメント）。
    """
    assert rank_7a_is_7b_slice(c(race_type="チャレンジ準決勝")) is False


# ── 2. 形は 7A と同じ ────────────────────────────────────────────────────


def test_requires_axis_sum_fail_and_entropy_ok():
    assert rank_7a_market_agree_pool([c(axis_sum=AX_OK)]) == []          # 形が違う
    assert rank_7a_market_agree_pool([c(entropy=ENT_NG)]) == []          # 両方不合格
    assert len(rank_7a_market_agree_pool([c()])) == 1


@pytest.mark.parametrize("ov", [0, 1, None])
def test_only_overlap2(ov):
    """overlap∈{0,1} は既存 7A の担当。None（印欠損）は対象外。"""
    assert rank_7a_market_agree_pool([c(wt_overlap_n=ov)]) == []


def test_does_not_leak_into_existing_7a_pool():
    """🔴 既存 `rank_7a_daily_select` は overlap==2 を拾わないまま。

    ここが混ざると 7A の低配当見送りゲートの閾値（直近プールの q20）が
    動いて、**既存 7A で選ばれるレースが変わる**。
    """
    assert rank_7a_daily_select([c()]) == []


def test_sorted_by_axis_sum():
    """axis_sum 昇順（既存 7A と同じ並び）。race_key は日付を持つ形のまま変える。"""
    lo = c(race_key=_ON.replace("_34_04", "_34_01"), axis_sum=AX_NG)
    hi = c(race_key=_ON.replace("_34_04", "_34_02"), axis_sum=AX_NG + 0.2)
    got = [x["race_key"] for x in rank_7a_market_agree_pool([hi, lo])]
    assert got == [lo["race_key"], hi["race_key"]]


# ── 3. 停止スイッチ ──────────────────────────────────────────────────────


def test_switch_off_disables_everything(monkeypatch):
    monkeypatch.setattr(sw, "RANK_7A_MARKET_AGREE", False)
    assert sw.rank_7a_market_agree_pool([c()]) == []


# ── 4. 適用開始日（過去を書き換えないための境界）──────────────────────────


def test_race_date_derived_from_race_key():
    """live の生候補は `race_date` を持たず `race_key` しかない。

    ここが片方だけ日付を取れないと live と rebuild で選ばれるレースが変わり、
    毎晩 picks_history が書き換わる。
    """
    assert rank_7a_race_date({"race_key": "20260810_34_04"}) == "2026-08-10"
    assert rank_7a_race_date({"race_date": "2026-08-09",
                              "race_key": "20260810_34_04"}) == "2026-08-09"
    assert rank_7a_race_date({"race_key": "こわれた"}) == ""


def test_not_applied_before_start_date():
    """🔴 開始日より前は拾わない。7A の過去実績（ROI 80.1%）を書き換えないため。"""
    y, m, d = (int(x) for x in RANK_7A_MARKET_AGREE_FROM.split("-"))
    before = f"{y:04d}{m:02d}{d:02d}"
    # 前日の race_key を作る（月初でも壊れないよう datetime で引く）
    from datetime import date as _d, timedelta as _t
    prev = (_d(y, m, d) - _t(days=1)).strftime("%Y%m%d")
    assert rank_7a_market_agree_pool([c(race_key=f"{prev}_34_04")]) == []
    assert len(rank_7a_market_agree_pool([c(race_key=f"{before}_34_04")])) == 1


def test_unknown_date_is_excluded():
    """日付が取れない候補は**拾わない**（安全側）。"""
    assert rank_7a_market_agree_pool([c(race_key="こわれた")]) == []
