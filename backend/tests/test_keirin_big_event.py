"""大会（6日制の特別開催）の予選を穴埋め対象に含める判定の検査。

## なぜ要るのか

2026-08-13、オールスター競輪（GI・松山）の **6R〜11R が丸ごと無推奨**になった。
原因は穴埋めの対象が「看板（決勝・特選クラス）」だけで、GI/GII の
**二次予選を拾わなかった**こと。実測でこの開催は1レースあたりの有償ptが
他会場の **5.0倍**で、最も売れる場所に商品が1つも無い状態だった。

## 縛るもの

1. 大会の予選が穴埋め対象になること
2. **通常の「予選」を巻き込まないこと**（282開催・2,264レースの日常レース）
3. 看板と大会を**別概念のまま**保つこと（Web の★と 7T1 の母集団を動かさない）
"""
from __future__ import annotations

import pytest

from src.services.keirin_marquee import (
    BIG_EVENT_KEYWORDS,
    is_big_event_race,
    is_fill_target,
    is_marquee_race,
)


@pytest.mark.parametrize("race_type", [
    "一予選", "二予選", "二次予選Ａ", "二次予選Ｂ", "一次予選１",
    "東予選(第１走)", "西予選(第２走)", "二次予選(東日本)",
])
def test_big_event_preliminaries_are_fill_targets(race_type):
    assert is_big_event_race(race_type) is True
    assert is_fill_target(race_type) is True


@pytest.mark.parametrize("race_type", ["予選", "チャレンジ予選", "特予選",
                                       "ガールズ予選(第１走)", "一般", "準決勝"])
def test_ordinary_races_are_not_big_events(race_type):
    """🔴 通常の「予選」を巻き込まないこと。

    接頭辞の無い「予選」は282開催・2,264レースに現れる日常のレース。
    含めると判定が意味を失う（「準決勝」を除外しないと14.5%が看板になるのと同型）。
    """
    assert is_big_event_race(race_type) is False


def test_big_event_and_marquee_stay_separate():
    """🔴 看板と大会は**別概念**。混ぜると Web の★の意味が変わり、
    `rank_7t1_is_target_race_type`（決勝系レース）の母集団も動く。
    """
    assert is_marquee_race("二予選") is False      # 大会の予選は看板ではない
    assert is_big_event_race("決勝") is False      # 決勝は看板であって大会予選ではない
    assert is_fill_target("二予選") is True
    assert is_fill_target("決勝") is True


def test_special_selection_prelim_is_already_marquee():
    """「特別選抜予選」は「選抜」を部分一致で拾うので既に看板。二重に足さない。"""
    assert is_marquee_race("特別選抜予選") is True
    assert "特別選抜予選" not in BIG_EVENT_KEYWORDS


def test_keyword_list_does_not_contain_bare_yosen():
    """裸の「予選」が紛れ込んでいないこと（全ての予選が対象になる）。"""
    assert "予選" not in BIG_EVENT_KEYWORDS


def test_seven_car_population_is_unaffected():
    """🔴 7T1 の母集団（決勝系レース）が動いていないこと。

    7T1 は `MARQUEE_KEYWORDS` の部分一致で母集団を決める。大会の予選を
    そちらへ足すと 7T1 まで巻き込むので、別関数に分けてある。
    実データ上も一予選・二予選は**全て9車**なので7車には現れない。
    """
    from src.services.keirin_marquee import MARQUEE_KEYWORDS
    for kw in BIG_EVENT_KEYWORDS:
        assert kw not in MARQUEE_KEYWORDS, (
            f"{kw} が MARQUEE_KEYWORDS に入っています（7T1 の母集団が変わります）")
