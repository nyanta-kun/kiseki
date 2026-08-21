"""検証プロトコルの経路を固定する（2026-08-21 新設）。

競輪は 2026-08-21 に「発見と検証に同じ窓を使う」誤りを1日で2回踏み、
そのたびに結論を撤回した。**気づける仕組みが無かった**のが原因なので、
仕組みのほうをテストで固める。
"""
from __future__ import annotations

import datetime
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import src.keirin_protocol as kp


def test_test_start_is_the_current_quarter():
    """TEST は**暦とともに前進する**こと（ベタ書きの窓に戻さない）。"""
    d = datetime.date.fromisoformat(kp.TEST_START)
    assert d.day == 1 and d.month in (1, 4, 7, 10)


def test_val_ends_the_day_before_test():
    assert (datetime.date.fromisoformat(kp.VAL_END) + datetime.timedelta(days=1)
            == datetime.date.fromisoformat(kp.TEST_START))


def test_burned_windows_are_rejected():
    """🔴 焼けた窓で結論を出そうとしたら**例外で止まる**こと。

    警告では足りない。2026-08-21 は「確認窓の79%が別母集団」に気づかず
    棄却を宣言してしまった。
    """
    with pytest.raises(SystemExit):
        kp.assert_not_burned("2025-04-01", "2025-06-30", who="test")   # w5
    with pytest.raises(SystemExit):
        kp.assert_not_burned("2026-05-01", "2026-05-31", who="test")   # w1 の内側


def test_fresh_window_passes():
    kp.assert_not_burned("2026-07-16", "2026-09-30", who="test")


def test_clean_test_start_skips_a_burned_overlap():
    """当四半期が焼けに食い込んでいたら、その翌日以降を返すこと。

    2026-08-21 時点で当四半期は 2026-07-01 始まりだが w1 の探索が
    2026-07-15 まで伸びている。
    """
    clean = kp.clean_test_start()
    assert clean >= kp.TEST_START
    assert kp.is_burned(clean, clean) is None


def test_min_test_windows_is_not_one():
    """🔴 1窓で採否を決めない。効果量(+0.3pt)とばらつき(±0.15pt)が同オーダー。"""
    assert kp.MIN_TEST_WINDOWS >= 4


def test_train_from_is_pinned():
    """`TRAIN_FROM` は S/B ラベルの都合で動かせない（古い窓は作れない）。"""
    assert kp.TRAIN_FROM == "2024-04-01"


def test_test_start_can_be_pinned_for_reproduction(monkeypatch):
    """過去分析の再現用に `KEIRIN_TEST_START` で固定できること。"""
    monkeypatch.setenv("KEIRIN_TEST_START", "2025-10-01")
    import importlib
    reloaded = importlib.reload(kp)
    try:
        assert reloaded.TEST_START == "2025-10-01"
    finally:
        monkeypatch.delenv("KEIRIN_TEST_START", raising=False)
        importlib.reload(kp)
