"""スクレイプ対象レースの供給を固定する。

なぜ必要か（2026-09-06・統合 Phase 2）:
    移設前は `sekito.races` を読んでいた。その表は sekito の日次ジョブが
    `keiba.races` / `chihou.races` から同期して作っていたので、移設後は
    元の 2 表を直接読む（同期ジョブ 2 本が不要になる）。

    🔴 **2 表は場コードの体系が違う**。`keiba.races.course` は JRA 2 桁課コード、
    `chihou.races.course` は netkeiba の場コード。取り違えると
    「対象レース 0 件」になり、ジョブは success を返して指数だけが欠ける。
    そこを机上で間違えないよう、変換規則をここで固定する。

    本番との一致は 2026-09-06 に実測済み（中央 直近14日 180/180・差分 0、
    地方 15 日ぶんすべて差分 0）。
"""

from __future__ import annotations

from datetime import date

from src.scrapers.targets import TargetRace, target_races
from src.utils.racecourse import BY_NETKEIBA_ID, JRA_TO_SEKITO


class _FakeSession:
    """`keiba.races` / `chihou.races` の SELECT を順に返すスタブ。"""

    def __init__(self, jra_rows, nar_rows):
        self.jra_rows = jra_rows
        self.nar_rows = nar_rows

    def execute(self, stmt, _params=None):
        sql = str(stmt)
        return list(self.nar_rows if "chihou.races" in sql else self.jra_rows)


def _session():
    # keiba.races は JRA 課コード、chihou.races は netkeiba 場コード
    return _FakeSession(
        jra_rows=[("05", 11), ("05", 12), ("09", 1)],
        nar_rows=[("44", 11), ("30", 1), ("83", 5)],
    )


def test_中央は課コードから地方はnetkeibaコードから解決する():
    got = target_races(_session(), date(2026, 9, 6))
    assert got == [
        TargetRace(date(2026, 9, 6), "JHSN", 1),   # 09 = 阪神
        TargetRace(date(2026, 9, 6), "JTOK", 11),  # 05 = 東京
        TargetRace(date(2026, 9, 6), "JTOK", 12),
        TargetRace(date(2026, 9, 6), "NMNB", 1),   # 30 = 門別
        TargetRace(date(2026, 9, 6), "NOOI", 11),  # 44 = 大井
    ]


def test_対応表に無い場は黙って落とす():
    """`chihou.races.course='83'` が実在する（2026-09-06 実測）。

    移設前も `sekito.racecourse` に無いので対象外だった。ここで拾うと
    吉馬・netkeiba に存在しない場コードで叩きに行くことになる。
    """
    got = target_races(_session(), date(2026, 9, 6))
    assert "83" not in BY_NETKEIBA_ID
    assert all(t.course_code.startswith(("J", "N")) for t in got)


def test_中央だけ_地方だけに絞れる():
    jra = target_races(_session(), date(2026, 9, 6), include_nar=False)
    nar = target_races(_session(), date(2026, 9, 6), include_jra=False)
    assert all(t.is_jra for t in jra) and len(jra) == 3
    assert not any(t.is_jra for t in nar) and len(nar) == 2


def test_場とレース番号で絞れる():
    got = target_races(_session(), date(2026, 9, 6), course_codes=["JTOK"], race_nos=[11])
    assert got == [TargetRace(date(2026, 9, 6), "JTOK", 11)]


def test_並び順は移設前と同じ_course_code_race_no():
    """時間打ち切りのあるジョブは「どこまで進んだか」が順序に依存する。"""
    got = target_races(_session(), date(2026, 9, 6))
    assert got == sorted(got, key=lambda t: (t.course_code, t.race_no))


def test_中央地方の判定は先頭文字():
    assert TargetRace(date(2026, 9, 6), "JTOK", 1).group_label == "中央"
    assert TargetRace(date(2026, 9, 6), "NOOI", 1).group_label == "地方"


def test_中央10場すべてに課コードがある():
    """`keiba.races.course` で来る値が 1 つでも欠けると、その場が丸ごと落ちる。"""
    assert len(JRA_TO_SEKITO) == 10
