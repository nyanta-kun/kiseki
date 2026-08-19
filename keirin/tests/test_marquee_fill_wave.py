"""看板穴埋めの「波」判定がランクの入稿と一致することの回帰テスト。

## 背景（2026-08-19 に是正した実バグ）

netkeirin は1レース1商品なので、穴埋めとランクは同じレースを取り合う。
波の中では**ランク → 穴埋め**の順に走るので競合は起きない設計だった
（`scripts/wave_submit_wt.sh`）。ところが穴埋めだけが独自の判定

    is_night = 開催の第1R発走 >= 18時

を持っており、**ナイター（第1R 12〜17時台）を朝7時の波で埋めていた**。
ランクがその開催を出すのは昼13:00 なので、穴埋めが5時間先回りして横取りする。

実測（2026-08-09〜08-19・穴埋め196件）。ランクが候補を持ちながら
**賭け金0＝商品を取れなかった25件は、波がずれたバケツにだけ現れた**:

    morning × morning  67件 / 候補24 / 取れず 0
    morning × noon     78件 / 候補53 / 取れず **25**
    noon    × noon      6件 / 候補 2 / 取れず 0
    evening × night    31件 / 候補10 / 取れず 0

板が育つ前に出すので傾斜配分も効かない（ナイターの三連複 未確定率は
朝8時台 30.8% → 12:00 5.3%）。

⚠️ 壊れても例外は出ない。「穴埋めが少し多い」「ランクの商品が少し少ない」に
   しか見えないので、テストでしか守れない。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.netkeirin_submit_wt import SESSION_WAVE  # noqa: E402
from scripts.submit_marquee_wt import (  # noqa: E402
    due_waves_for,
    session_of_hour,
    venue_waves,
)
from src.meeting_wave import (  # noqa: E402
    WAVE_MORNING,
    WAVE_NIGHT,
    WAVE_NOON,
    waves_due_by,
)


def _race(venue: str, hour: int, minute: int = 0) -> dict:
    """JST の指定時刻に発走するレース1件（`start_at` は epoch 秒）。"""
    # 1970-01-01 JST 基準で十分（`venue_waves` は時刻の「時」しか見ない）。
    return {"venue_id": venue, "start_at": (hour - 9) % 24 * 3600 + minute * 60}


def test_session_label_follows_execution_hour():
    assert session_of_hour(7) == "morning"
    assert session_of_hour(11) == "morning"
    assert session_of_hour(12) == "noon"
    assert session_of_hour(17) == "noon"
    assert session_of_hour(18) == "evening"
    assert session_of_hour(23) == "evening"


def test_due_waves_are_taken_from_the_rank_side_table():
    """🔴 対応表を書き写していないこと（写した瞬間に無言でずれる）。"""
    for session in ("morning", "noon", "evening"):
        assert due_waves_for(session) == set(waves_due_by(SESSION_WAVE[session]))


def test_night_meeting_is_not_filled_in_the_morning():
    """🔴 これが本体。ナイター（第1R 16時）を朝の波で埋めてはいけない。

    2026-08-19 以前は「第1R が18時未満なら朝に出してよい」だったため、
    ナイターが朝7時に埋められランクの13:00 が空振りしていた。
    """
    races = [_race("48", 16), _race("48", 16, 24), _race("48", 20, 30)]
    wave = venue_waves(races)["48"]
    assert wave == WAVE_NOON
    assert wave not in due_waves_for("morning")
    assert wave in due_waves_for("noon")
    assert wave in due_waves_for("evening")     # 取りこぼしは後の波が拾う


def test_midnight_meeting_is_only_filled_in_the_evening():
    races = [_race("34", 20), _race("34", 21)]
    wave = venue_waves(races)["34"]
    assert wave == WAVE_NIGHT
    assert wave not in due_waves_for("morning")
    assert wave not in due_waves_for("noon")
    assert wave in due_waves_for("evening")


def test_morning_meeting_is_filled_in_every_later_wave():
    """前倒し訂正で波を通り過ぎた開催を落とさない（`waves_due_by` の意図）。"""
    races = [_race("44", 10), _race("44", 15)]
    wave = venue_waves(races)["44"]
    assert wave == WAVE_MORNING
    for session in ("morning", "noon", "evening"):
        assert wave in due_waves_for(session)


def test_wave_is_decided_by_the_first_race_of_the_venue():
    """開催の波は**その会場の第1R**で決まる（レース個別の発走では分けない）。"""
    races = [_race("48", 16), _race("48", 20)]
    assert venue_waves(races)["48"] == WAVE_NOON     # 20時のレースも noon 扱い


def test_venues_are_separated():
    races = [_race("44", 10), _race("48", 16), _race("34", 20)]
    got = venue_waves(races)
    assert got == {"44": WAVE_MORNING, "48": WAVE_NOON, "34": WAVE_NIGHT}


def test_missing_start_time_falls_back_to_morning():
    """🔴 発走時刻が取れない開催は朝扱い（安全側）。

    分からないことを理由に入稿を落とすと、看板レースの商品が黙って消える。
    """
    races = [{"venue_id": "99", "start_at": None},
             {"venue_id": "99", "start_at": ""}]
    assert venue_waves(races)["99"] == WAVE_MORNING
