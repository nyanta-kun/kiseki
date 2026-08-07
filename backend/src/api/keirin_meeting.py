"""開催（会場×日）の種別判定（2026-08-07 新設）。

競輪の開催は **その開催の第1レースの発走時刻**だけで4種に分かれる。
実測（keirin `wt_races`・2026-07-16以降）で観測された第1R発走は
**8 / 10 / 11 / 15 / 16 / 20 時の6通りだけ**で、9時台・12〜14時台・17〜19時台が
空いているため、境界に余裕がある。

| 種別 | 第1R発走 | 最終R |
|---|---|---|
| モーニング | 8時 | 10時 |
| デイ | 10-11時 | 15-16時 |
| ナイター | 15-16時 | 20時 |
| ミッドナイト | 20時 | 23時 |

🔴 **これは keirin リポジトリの `src/meeting_wave.py` と同じ量を見ている。**
   あちらは netkeirin 入稿の「波」（朝7:00 / 昼13:00 / 夕18:00）を決めるために
   3分割し、こちらは表示のために4分割する。**閾値がずれると
   「入稿の波」と「カードの色」が食い違って混乱する**ので、
   境界値は `tests/test_keirin_meeting.py` で固定してある。
   リポジトリが分かれている以上コード共有ができないため、
   検査で縛るのが唯一の手段。

⚠️ **発走時刻が取れない開催は None**（＝色を付けない）。分からないものを
   どれかの種別に倒すと、実際とは違う色が付いて誤読の元になる。
"""
from __future__ import annotations

MEETING_MORNING = "morning"      # モーニング（第1R < 9時）
MEETING_DAY = "day"              # デイ（9〜11時台）
MEETING_NIGHTER = "nighter"      # ナイター（12〜17時台）
MEETING_MIDNIGHT = "midnight"    # ミッドナイト（18時〜）

# 境界（第1R発走時刻の「時」）。実測の第1R発走は 8/10/11/15/16/20 時のみで、
# 9時台・12〜14時台・17〜19時台が空いている＝どの境界も実データから離れている。
DAY_FROM_HOUR = 9
# 🔴 12 と 18 は keirin `src/meeting_wave.py` の `NOON_FROM_HOUR` / `NIGHT_FROM_HOUR`
#    と**同じ値でなければならない**（入稿の波とカードの色が食い違うため）。
#    tests/test_keirin_meeting.py::test_入稿の波と境界が食い違わない が縛っている。
#    当初 13 にしていて実際にそのテストで捕まえた。
NIGHTER_FROM_HOUR = 12
MIDNIGHT_FROM_HOUR = 18


def meeting_type_of_first_hour(first_race_hour: float | int | None) -> str | None:
    """開催の第1R発走時刻（時・JST）から開催種別を返す。不明なら None。"""
    if first_race_hour is None:
        return None
    h = float(first_race_hour)
    if h >= MIDNIGHT_FROM_HOUR:
        return MEETING_MIDNIGHT
    if h >= NIGHTER_FROM_HOUR:
        return MEETING_NIGHTER
    if h >= DAY_FROM_HOUR:
        return MEETING_DAY
    return MEETING_MORNING


def first_hour_jst(start_at: str | int | None) -> float | None:
    """`wt_races.start_at`（UNIX秒の文字列）から JST の「時」を返す。"""
    # `start_at in (None, "")` だと mypy が None を絞り込めず int() で型エラーになる
    # （main に元からあった唯一の mypy エラー。2026-08-08 是正）。
    if start_at is None or start_at == "":
        return None
    try:
        return ((int(start_at) + 9 * 3600) % 86400) / 3600
    except (TypeError, ValueError):
        return None
