"""開催種別の境界を固定する（2026-08-07）。

🔴 **keirin リポジトリの `src/meeting_wave.py` と同じ量を見ている。**
   あちらは netkeirin 入稿の「波」（朝7:00 / 昼13:00 / 夕18:00）を決めるために
   3分割し、こちらは表示のために4分割する。リポジトリが分かれていてコードを
   共有できないので、**境界がずれて「入稿の波」と「カードの色」が食い違わないよう
   ここで値を固定する**。片方だけ直したらこのテストが落ちる。

対応表（keirin `SESSION_WAVE` ↔ 本モジュール）:

    朝の波   (morning) = モーニング + デイ      … 第1R < 12時
    昼の波   (noon)    = ナイター               … 第1R 12〜17時台
    夕方の波 (night)   = ミッドナイト           … 第1R 18時〜
"""
from __future__ import annotations

import pytest

from src.api.keirin_meeting import (
    MEETING_DAY,
    MEETING_MIDNIGHT,
    MEETING_MORNING,
    MEETING_NIGHTER,
    first_hour_jst,
    meeting_type_of_first_hour,
)


# 実測（keirin wt_races・2026-07-16以降）で観測された第1R発走はこの6通りだけ。
@pytest.mark.parametrize("hour,expected", [
    (8, MEETING_MORNING),     # モーニング（最終10時）
    (10, MEETING_DAY),        # デイ（最終15-16時）
    (11, MEETING_DAY),        # デイ（遅め）
    (15, MEETING_NIGHTER),    # ナイター（最終20時）
    (16, MEETING_NIGHTER),    # ナイター（遅め）
    (20, MEETING_MIDNIGHT),   # ミッドナイト（最終23時）
])
def test_実測される第1R発走時刻が正しい種別になる(hour, expected):
    assert meeting_type_of_first_hour(hour) == expected


def test_境界():
    assert meeting_type_of_first_hour(8.99) == MEETING_MORNING
    assert meeting_type_of_first_hour(9) == MEETING_DAY
    assert meeting_type_of_first_hour(11.99) == MEETING_DAY
    assert meeting_type_of_first_hour(12) == MEETING_NIGHTER
    assert meeting_type_of_first_hour(17.99) == MEETING_NIGHTER
    assert meeting_type_of_first_hour(18) == MEETING_MIDNIGHT


def test_入稿の波と境界が食い違わない():
    """keirin `meeting_wave.py` の波（12時 / 18時）を跨がないこと。

    朝の波 = モーニング+デイ / 昼の波 = ナイター / 夕方の波 = ミッドナイト。
    したがって **12時未満は必ず morning か day**、**12〜18時未満は必ず nighter**、
    **18時以降は必ず midnight** でなければならない。
    """
    for h in range(0, 12):
        assert meeting_type_of_first_hour(h) in (MEETING_MORNING, MEETING_DAY), h
    for h in range(12, 18):
        assert meeting_type_of_first_hour(h) == MEETING_NIGHTER, h
    for h in range(18, 24):
        assert meeting_type_of_first_hour(h) == MEETING_MIDNIGHT, h


def test_発走時刻不明は色を付けない():
    """どれかの種別に倒すと実際とは違う色が付いて誤読の元になる。"""
    assert meeting_type_of_first_hour(None) is None
    assert first_hour_jst(None) is None
    assert first_hour_jst("") is None
    assert first_hour_jst("not-a-number") is None
    assert meeting_type_of_first_hour(first_hour_jst(None)) is None


def test_UNIX秒からJSTの時を取り出す():
    # 固定値の暗記ではなく「+1時間で1つ進む」ことで確かめる
    base = 1786000000
    h = first_hour_jst(base)
    assert h is not None and 0 <= h < 24
    # +1時間で「時」がちょうど1つ進む
    assert first_hour_jst(base + 3600) == pytest.approx((h + 1) % 24)
