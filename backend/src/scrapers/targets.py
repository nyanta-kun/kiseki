"""スクレイプ対象レースの供給。

移設前は `sekito.races` を SELECT していた。その表は sekito の日次ジョブが
`keiba.races`（中央・JV-Link 由来）と `chihou.races`（地方・UmaConn 由来）から
同期して作っていたので、ここでは**元の 2 表を直接読む**。

2026-09-06 の実測（本番 DB）:
    中央 直近14日   sekito.races 180 / keiba.races 180 / 差分 0
    地方 直近15日   日付ごとに全て一致（sekito のみ 0 / chihou のみ 0）
    → `sekito.races` は 2 表の射影にすぎず、経由する理由が無い。

🔴 地方の場コード体系:
    `chihou.races.course` は **netkeiba の場コード**（"30"=門別 / "44"=大井）で、
    JRA-VAN の課コードではない。`keiba.races.course` は JRA 2 桁課コード。
    どちらも `racecourse.py` の対応表で sekito 4 文字コードへ寄せる。
    対応表に無い場（実測: `chihou.races.course='83'`）は移設前も取得対象外
    だったので、ここでも黙って落とす。
"""

from __future__ import annotations

from datetime import date
from typing import NamedTuple

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..utils.racecourse import BY_NETKEIBA_ID, JRA_TO_SEKITO


class TargetRace(NamedTuple):
    """スクレイプ対象の 1 レース。

    Attributes:
        date: レース日。
        course_code: sekito 4 文字コード（`sekito.*` テーブルの course_code）。
        race_no: レース番号。

    `is_jra` / `group_label` は course_code から導く（取得先ホストの出し分けと
    ログ表記に使う）。
    """

    date: date
    course_code: str
    race_no: int

    @property
    def is_jra(self) -> bool:
        return self.course_code.startswith("J")

    @property
    def group_label(self) -> str:
        """ログ用の「中央 / 地方」。"""
        return "中央" if self.is_jra else "地方"


_JRA_SQL = text(
    """
    SELECT r.course, r.race_number
    FROM keiba.races r
    WHERE r.date = :ymd
    ORDER BY r.course, r.race_number
    """
)

_NAR_SQL = text(
    """
    SELECT r.course, r.race_number
    FROM chihou.races r
    WHERE r.date = :ymd
    ORDER BY r.course, r.race_number
    """
)


def target_races(
    session: Session,
    target_date: date,
    *,
    include_jra: bool = True,
    include_nar: bool = True,
    course_codes: list[str] | None = None,
    race_nos: list[int] | None = None,
) -> list[TargetRace]:
    """対象日のレースを sekito 4 文字コードで返す。

    Args:
        session: 同期セッション（`SyncSessionLocal`）。
        target_date: 対象日。
        include_jra: 中央を含めるか。
        include_nar: 地方を含めるか。
        course_codes: sekito 4 文字コードでの絞り込み（省略時は全場）。
        race_nos: レース番号での絞り込み（省略時は全レース）。

    Returns:
        `course_code`, `race_no` の昇順に並んだ対象レース。
    """
    ymd = target_date.strftime("%Y%m%d")
    wanted = set(course_codes) if course_codes else None
    wanted_nos = set(race_nos) if race_nos else None

    out: list[TargetRace] = []
    if include_jra:
        for course, race_no in session.execute(_JRA_SQL, {"ymd": ymd}):
            code = JRA_TO_SEKITO.get(str(course).zfill(2))
            if code:
                out.append(TargetRace(target_date, code, int(race_no)))
    if include_nar:
        for course, race_no in session.execute(_NAR_SQL, {"ymd": ymd}):
            rc = BY_NETKEIBA_ID.get(str(course).zfill(2))
            # 中央の netkeiba_id と地方の場コードは値域が重ならないが、
            # 念のため「地方の表から中央コードが出てきたら捨てる」を明示しておく。
            if rc and rc.jra_code is None:
                out.append(TargetRace(target_date, rc.code, int(race_no)))

    if wanted is not None:
        out = [t for t in out if t.course_code in wanted]
    if wanted_nos is not None:
        out = [t for t in out if t.race_no in wanted_nos]

    # 並び順は移設前（`ORDER BY course_code, race_no`）に合わせる。時間打ち切りの
    # あるジョブでは「どこまで進んだか」が順序に依存するため、比較のあいだは
    # 変えない（中央 J* → 地方 N* の順になる）。
    out.sort(key=lambda t: (t.course_code, t.race_no))
    return out
