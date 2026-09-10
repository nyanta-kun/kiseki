"""POG スコア集計の点数計算（sekito `/api/pog/score-summary` の移設先）。

統合 Phase 5。**これは友人同士の実際の精算に使う数字**なので、SQL から切り離した
純関数にして単体テストで固定する。DB にも FastAPI にも依存しない。

## 仕組み

ゼロサムの賭け。誰かが重賞を勝つと、**その人が他の全員から徴収し、他の全員が
支払う**。だから全員の合計は常に 0 になる。

    自分の取り分   勝った回数 × 単価 × (人数 - 1)
    自分の支払い   (全員の勝った回数 - 自分の回数) × 単価

順位賞は賞金順位で決まる固定額（1位 +20,000 … 7位 -20,000）。

    合計pt = 順位賞 + 特別賞
    ⚠️ 基本pt（賞金 万円）は**合計に入らない**（表示だけ）。移設元と同じ。

## 単価

| 区分 | 単価 |
|---|---:|
| 日本ダービー | 2,000 |
| 中央 G1（ダービー除く） | 1,000 |
| 中央 G2 / G3 | 500 |
| **地方重賞** | 500 |
| 海外重賞（ダービー） | 1,000 |
| 海外重賞（その他） | 500 |
| 全馬出走賞 | 500 |
| 全馬勝利賞 | 1,000 |

## 🔴 地方重賞の判定は「格」ではなく「開催場」で行う

移設元は `grade IN ('Jpn1','JpnI',...)` で地方重賞を判定していた。この表記は
netkeiba 由来（`sekito.races`）で、**JV-Link は同じ中央交流競走を `G1/G2/G3`
として持っている**（実測: 東京大賞典 → grade `G1`・race_name に `Ｇ１`）。

kiseki のデータでそのまま `grade` を見ると、**中央交流が「中央 G1（1,000pt）」
として数えられる**。移設元の意図は明らかに「地方の格付け＝地方重賞（500pt）」
なので、**開催場コードが中央 10 場（01〜10）でなければ地方重賞**とする。

⚠️ これは金額が変わる変更。移設元は `sekito.entries` の凍結もあって中央交流を
大きく取りこぼしていた（旧 mv では Jpn 系が 54 件しか無い）ため、
**過去年度のスコアも動く**。

## ⚠️ 海外重賞は現状かならず 0 になる

移設元は `course_code LIKE 'O%'` で海外を拾っていたが、実測で
`sekito.mv_graded_wins` にも `sekito.races` にも **O 始まりの行は 1 件も無い**。
つまりこのカテゴリは一度も発火していない。JV-Link / UmaConn は海外開催を
持たないので kiseki でも 0 のまま。**枠は残す**（将来データが入れば効くように）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: 中央 10 場の場コード。ここに無ければ地方開催として扱う。
JRA_COURSES = frozenset({"01", "02", "03", "04", "05", "06", "07", "08", "09", "10"})

#: 日本ダービーのレース名。`race_name` は JV-Link だと
#: 「東京優駿（日本ダービー）」のように括弧付きなので部分一致で見る。
DERBY_KEYWORD = "日本ダービー"

#: 区分ごとの単価（pt）。
UNIT_DERBY = 2000
UNIT_G1 = 1000
UNIT_G2 = 500
UNIT_G3 = 500
UNIT_NAR = 500
UNIT_OVERSEAS_DERBY = 1000
UNIT_OVERSEAS_OTHER = 500
UNIT_ALL_RACED = 500
UNIT_ALL_WON = 1000

#: 賞金順位ごとの順位賞。移設元の switch と同じ。
#: 8 位以下は 0（実際 POG は 7 人だが、人数が増えても落ちないようにする）。
RANK_PRIZE = {1: 20000, 2: 10000, 3: 5000, 4: 0, 5: -5000, 6: -10000, 7: -20000}


@dataclass(frozen=True)
class Wins:
    """1 人ぶんの重賞勝ちの内訳。"""

    derby: int = 0
    g1: int = 0
    g2: int = 0
    g3: int = 0
    nar: int = 0
    overseas_derby: int = 0
    overseas_other: int = 0

    def paired(self) -> list[tuple[int, int]]:
        """`(回数, 単価)` の並び。徴収と支払いの両方でこの順に使う。"""
        return [
            (self.derby, UNIT_DERBY),
            (self.g1, UNIT_G1),
            (self.g2, UNIT_G2),
            (self.g3, UNIT_G3),
            (self.nar, UNIT_NAR),
            (self.overseas_derby, UNIT_OVERSEAS_DERBY),
            (self.overseas_other, UNIT_OVERSEAS_OTHER),
        ]


@dataclass(frozen=True)
class OwnerInput:
    """1 人ぶんの入力。"""

    user_id: int
    name: str | None
    #: 賞金（万円）。基本ptとしてそのまま表示する。
    total_prize: int
    #: 賞金順位（同額は同順位）。
    prize_rank: int
    win: int
    place: int
    show: int
    out: int
    #: 指名頭数（`pick_order <> 0` の数）。
    horse_count: int
    #: 1 回でも出走した馬の数。
    horses_raced: int
    #: 1 回でも勝った馬の数。
    horses_won: int
    wins: Wins = field(default_factory=Wins)

    @property
    def all_raced(self) -> bool:
        """全馬出走賞の条件を満たすか。"""
        return self.horse_count > 0 and self.horses_raced >= self.horse_count

    @property
    def all_won(self) -> bool:
        """全馬勝利賞の条件を満たすか。"""
        return self.horse_count > 0 and self.horses_won >= self.horse_count


@dataclass(frozen=True)
class OwnerScore:
    """1 人ぶんの結果。"""

    rank: int
    prize_rank: int
    user_id: int
    name: str | None
    total_prize: int
    basic_points: int
    rank_prize: int
    special_prize: int
    total_points: int
    win: int
    place: int
    show: int
    out: int
    horse_count: int
    horses_raced: int
    horses_won: int
    all_raced: bool
    all_won: bool
    wins: Wins


def classify(grade: str | None, course_code: str | None, race_name: str | None) -> str | None:
    """重賞 1 勝を区分に振り分ける。

    Args:
        grade: `races.grade`。
        course_code: `races.course`。中央 10 場でなければ地方開催とみなす。
        race_name: レース名。日本ダービーの判定に使う。

    Returns:
        `Wins` のフィールド名。重賞でなければ None。

    🔴 **地方重賞は格ではなく開催場で決める。** JV-Link は中央交流競走を
    `G1/G2/G3` として持つため、格だけで見ると中央 G1 として数えてしまう。
    """
    if not grade:
        return None
    # 海外は場コードが `O` 始まり（現状データは無いが、入ったときに効くように）。
    if course_code and course_code.startswith("O"):
        if race_name and ("ダービー" in race_name or "Derby" in race_name):
            return "overseas_derby"
        return "overseas_other" if grade in {"G1", "G2", "G3"} else None
    # 中央 10 場以外＝地方開催。中央交流もここに入る。
    if course_code not in JRA_COURSES:
        return "nar"
    if grade in {"JpnI", "Jpn1", "JpnII", "Jpn2", "JpnIII", "Jpn3"}:
        return "nar"
    if grade == "G1":
        return "derby" if race_name and DERBY_KEYWORD in race_name else "g1"
    if grade == "G2":
        return "g2"
    if grade == "G3":
        return "g3"
    return None


def build_scores(owners: list[OwnerInput]) -> list[OwnerScore]:
    """全員ぶんのスコアを算出し、合計pt の降順で順位を付けて返す。

    Args:
        owners: 全参加者の入力。**一部だけ渡してはいけない**（徴収と支払いが
            全員の合計に依存するため、欠けると全員の金額が狂う）。

    Returns:
        合計pt の降順。同点は同順位。
    """
    member_count = len(owners)
    if member_count == 0:
        return []

    # 全員ぶんの合計。支払い側の計算に使う。
    totals = {
        name: sum(getattr(o.wins, name) for o in owners)
        for name in ("derby", "g1", "g2", "g3", "nar", "overseas_derby", "overseas_other")
    }
    all_raced_count = sum(1 for o in owners if o.all_raced)
    all_won_count = sum(1 for o in owners if o.all_won)

    scored: list[OwnerScore] = []
    for o in owners:
        special = 0
        for name, unit in (
            ("derby", UNIT_DERBY),
            ("g1", UNIT_G1),
            ("g2", UNIT_G2),
            ("g3", UNIT_G3),
            ("nar", UNIT_NAR),
            ("overseas_derby", UNIT_OVERSEAS_DERBY),
            ("overseas_other", UNIT_OVERSEAS_OTHER),
        ):
            mine = getattr(o.wins, name)
            special += mine * unit * (member_count - 1)
            special -= (totals[name] - mine) * unit

        if o.all_raced:
            special += UNIT_ALL_RACED * (member_count - 1)
        special -= (all_raced_count - (1 if o.all_raced else 0)) * UNIT_ALL_RACED

        if o.all_won:
            special += UNIT_ALL_WON * (member_count - 1)
        special -= (all_won_count - (1 if o.all_won else 0)) * UNIT_ALL_WON

        rank_prize = RANK_PRIZE.get(o.prize_rank, 0)
        scored.append(
            OwnerScore(
                rank=0,  # 下で付け直す
                prize_rank=o.prize_rank,
                user_id=o.user_id,
                name=o.name,
                total_prize=o.total_prize,
                # 基本pt は賞金（万円）そのまま。⚠️ 合計には入らない。
                basic_points=o.total_prize,
                rank_prize=rank_prize,
                special_prize=special,
                total_points=rank_prize + special,
                win=o.win,
                place=o.place,
                show=o.show,
                out=o.out,
                horse_count=o.horse_count,
                horses_raced=o.horses_raced,
                horses_won=o.horses_won,
                all_raced=o.all_raced,
                all_won=o.all_won,
                wins=o.wins,
            )
        )

    scored.sort(key=lambda s: s.total_points, reverse=True)
    out: list[OwnerScore] = []
    prev_points: int | None = None
    current_rank = 0
    for i, s in enumerate(scored, start=1):
        if prev_points is not None and s.total_points == prev_points:
            pass  # 同点は同順位（current_rank を据え置く）
        else:
            current_rank = i
            prev_points = s.total_points
        out.append(OwnerScore(**{**s.__dict__, "rank": current_rank}))
    return out
