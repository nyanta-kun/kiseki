"""メカウチダの買い目 × 指数上位5位 / 穴ぐさ の一致を Discord に通知する。

中央の開催日、各レースの**発走 1 時間前から 10 分おき**にサイトを監視し、
**発走前の最後の監視**（発走まで 10 分以内）の時点で、サイトの買い目が

- 総合指数のレース内順位 5 位以内、または
- 穴ぐさ（A/B/C いずれか）

と一致した馬を通知する。

🔴 **発走後は絶対に通知しない。** サイトは発走後に判断を出すことがある
（2026-09-27 実測で 1R〜4R の判断が発走後 17〜111 分に出た。
``docs/jra_ext_virtualbet_feature_plan_2026_09_27.md`` §2.2）。

⚠️ サイトの正式判断は**発走 3 分前**に出る。10 分おきの最後の監視は発走 0〜10 分前なので、
多くのレースでは**見込み**（発走 30 分前からその時点のオッズで仮判断）を見ることになる。
通知文には必ず「判断済み / 見込み」を出す。

判定（窓・一致）は DB にも HTTP にも依存しない純関数。DB と送信は呼び出し側。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from src.scrapers.mekauchida import SitePick, SiteRace

JST = ZoneInfo("Asia/Tokyo")

MONITOR_LEAD = timedelta(minutes=60)
"""監視を始める発走前の時間。"""

POLL_INTERVAL = timedelta(minutes=10)
"""監視の間隔（cron と揃えること）。発走までこれ以下なら「最後の監視」。"""

FINAL_CAPTURE_WINDOW = timedelta(minutes=30)
"""発走後もサイトを読みに行く時間（判断済みの波乱度を取るため）。

サイトのページは約 10 分おきの更新で、発走直前の判断がページに載るのは発走後に
なることが多い。10 分おきの監視で 2〜3 回は読めるよう 30 分にしてある。
"""

INDEX_RANK_MAX = 5
"""指数順位がこれ以内なら一致。"""

KIND_LABEL = {"buy": "判断済み", "pv": "見込み"}


def parse_post_at(date: str, post_hhmm: str | None) -> datetime | None:
    """``YYYYMMDD`` + ``hhmm`` を JST の datetime にする（不正なら None）。"""
    if not post_hhmm or len(post_hhmm) != 4 or not post_hhmm.isdigit():
        return None
    try:
        return datetime.strptime(f"{date}{post_hhmm}", "%Y%m%d%H%M").replace(tzinfo=JST)
    except ValueError:
        return None


def is_monitoring(post_at: datetime | None, now: datetime) -> bool:
    """発走 1 時間前〜発走前なら True（発走時刻ちょうどは対象外）。"""
    if post_at is None:
        return False
    remain = post_at - now
    return timedelta(0) < remain <= MONITOR_LEAD


def is_final_capture(post_at: datetime | None, now: datetime) -> bool:
    """発走後 ``FINAL_CAPTURE_WINDOW`` 以内なら True（発走時刻ちょうどを含む）。

    この間はサイトを読み、判断済みの波乱度を取る（通知はしない）。
    """
    if post_at is None:
        return False
    return timedelta(0) <= now - post_at < FINAL_CAPTURE_WINDOW


def is_final_poll(post_at: datetime | None, now: datetime) -> bool:
    """発走前の最後の監視なら True。

    次の監視（``now + POLL_INTERVAL``）では発走を過ぎている＝発走まで
    ``POLL_INTERVAL`` 以内。発走時刻ちょうど以降は False（発走後に通知しない）。
    """
    if post_at is None:
        return False
    remain = post_at - now
    return timedelta(0) < remain <= POLL_INTERVAL


@dataclass(frozen=True)
class HorseContext:
    """DB 側の 1 頭（指数順位・穴ぐさ）。"""

    horse_number: int
    index_rank: int | None
    composite_index: float | None
    anagusa_rank: str | None  # "A" / "B" / "C"


@dataclass(frozen=True)
class MatchedPick:
    """通知する 1 頭。"""

    race: SiteRace
    pick: SitePick
    ctx: HorseContext
    reasons: tuple[str, ...]


def match_reasons(ctx: HorseContext | None) -> tuple[str, ...]:
    """一致した理由（空なら通知しない）。"""
    if ctx is None:
        return ()
    reasons: list[str] = []
    if ctx.index_rank is not None and ctx.index_rank <= INDEX_RANK_MAX:
        reasons.append(f"指数{ctx.index_rank}位")
    if ctx.anagusa_rank:
        reasons.append(f"穴ぐさ{ctx.anagusa_rank}")
    return tuple(reasons)


def match_race(race: SiteRace, horses: dict[int, HorseContext]) -> list[MatchedPick]:
    """1 レースの買い目のうち、一致したものを返す。"""
    out: list[MatchedPick] = []
    for pick in race.picks:
        ctx = horses.get(pick.horse_number)
        reasons = match_reasons(ctx)
        if reasons and ctx is not None:
            out.append(MatchedPick(race=race, pick=pick, ctx=ctx, reasons=reasons))
    return out


def _fmt_odds(v: float | None) -> str:
    return f"{v:.1f}" if v is not None else "—"


def build_message(matches: list[MatchedPick], now: datetime, title: str) -> str:
    """Discord に送る本文。"""
    lines = [f"🏇 **{title}**（{now:%m/%d %H:%M} 監視）"]
    for m in matches:
        r, p = m.race, m.pick
        post = f"{r.post_hhmm[:2]}:{r.post_hhmm[2:]}"
        flags = "".join(f"[{f}]" for f in p.flags)
        if p.place_odds_low is None:
            place = "—"
        elif p.place_odds_high is None:
            place = f"{p.place_odds_low:.1f}〜"
        else:
            place = f"{p.place_odds_low:.1f}–{p.place_odds_high:.1f}"
        ev = f"{p.expected_value:.2f}" if p.expected_value is not None else "—"
        pop = f"{p.popularity}人気" if p.popularity is not None else "—"
        lines.append(
            f"**{r.venue}{r.race_number}R** {post}発走　"
            f"{p.horse_number}番 {p.horse_name}{flags}（{KIND_LABEL.get(p.kind, p.kind)}）\n"
            f"　{' / '.join(m.reasons)}　{pop} 単{_fmt_odds(p.win_odds)} 複{place} 期待値{ev}"
        )
    lines.append("※ 見込み = サイトの正式判断（発走3分前）より前の仮判断。実際には買っていないサイトです")
    return "\n".join(lines)


@dataclass(frozen=True)
class UpsetRow:
    """保存する荒れやすさ 1 件（``keiba.mekauchida_race_upset`` の 1 行）。"""

    race_id: int
    level: str


def select_upset_rows(
    db_races: list[tuple[int, str, int, str | None]],
    site_races: list[SiteRace],
    date: str,
    now: datetime,
) -> list[UpsetRow]:
    """サイトの荒れやすさのうち、保存してよいものを選ぶ。

    Args:
        db_races: 今日の DB のレース ``(id, course_name, race_number, post_time)``
        site_races: サイトのレースカード
        date: ``YYYYMMDD``
        now: 現在時刻（JST）

    🔴 **発走前のレースだけを返す。** 発走後に読んだ札で上書きすると、保存値が
    「発走前の札」でなくなり前向きの記録として使えなくなる。
    発走時刻が読めないレースも返さない（前か後かが分からないため）。
    札の無いカード（障害・判断できなかったレース等）も返さない。
    """
    levels = {(r.venue, r.race_number): r.upset for r in site_races if r.upset}
    rows: list[UpsetRow] = []
    for race_id, venue, race_number, post_hhmm in db_races:
        level = levels.get((venue, race_number))
        post_at = parse_post_at(date, post_hhmm)
        if level is None or post_at is None or post_at <= now:
            continue
        rows.append(UpsetRow(race_id=race_id, level=level))
    return rows


def select_final_upset_rows(
    db_races: list[tuple[int, str, int, str | None]],
    site_races: list[SiteRace],
    date: str,
    now: datetime,
) -> list[UpsetRow]:
    """発走済みのレースのうち、サイトが判断済みとして札を出しているものを選ぶ。

    画面の波乱度は、発走後はこの札に差し替える（2026-10-25 ユーザー判断）。
    発走前に読んだ札は ``select_upset_rows`` が別の列に残す。

    - 発走時刻を過ぎたレースだけ（発走時刻ちょうどを含む）。時間で窓を切らないので、
      取り逃した回があっても次に読んだときにページ上の発走済みレースをまとめて拾う
    - カードが ``decided`` のものだけ。発走後でも ``wait`` のカードはページが
      まだ更新されておらず、札は判断前の仮のもの。``missed`` には札が無い
    """
    rows: list[UpsetRow] = []
    decided = {(r.venue, r.race_number): r.upset for r in site_races if r.state == "decided" and r.upset}
    for race_id, venue, race_number, post_hhmm in db_races:
        level = decided.get((venue, race_number))
        post_at = parse_post_at(date, post_hhmm)
        if level is None or post_at is None or post_at > now:
            continue
        rows.append(UpsetRow(race_id=race_id, level=level))
    return rows
