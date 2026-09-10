"""POG スコア集計の点数計算を固定する。

なぜ必要か（2026-09-10・統合 Phase 5）:
    🔴 **これは友人同士の実際の精算に使う数字。**間違えると人にお金を
    請求することになる。ゼロサムなので、1 箇所の符号ミスが全員に波及する。
    SQL から切り離した純関数にしてあるので、ここで機械的に固定する。
"""

from __future__ import annotations

from src.services.pog_score import (
    JRA_COURSES,
    OwnerInput,
    Wins,
    build_scores,
    classify,
)


def _owner(uid: int, *, prize=0, rank=4, horses=5, raced=0, won=0, wins=None) -> OwnerInput:
    return OwnerInput(
        user_id=uid,
        name=f"u{uid}",
        total_prize=prize,
        prize_rank=rank,
        win=0,
        place=0,
        show=0,
        out=0,
        horse_count=horses,
        horses_raced=raced,
        horses_won=won,
        wins=wins or Wins(),
    )


# --------------------------------------------------------------- 区分の振り分け


def test_中央のG1とダービーを分ける():
    assert classify("G1", "05", "東京優駿（日本ダービー）") == "derby"
    assert classify("G1", "05", "天皇賞（秋）") == "g1"
    assert classify("G2", "06", "セントライト記念") == "g2"
    assert classify("G3", "09", "シリウスステークス") == "g3"


def test_中央交流は地方重賞として数える():
    """🔴 JV-Link は中央交流を `G1` として持つ。格だけで見ると中央G1（1,000pt）
    になってしまうが、移設元の意図は地方重賞（500pt）。開催場で判定する。
    """
    # 東京大賞典（大井・course 44）は JV-Link 上 grade='G1'
    assert classify("G1", "44", "東京大賞典（中央交流）　Ｇ１") == "nar"
    assert classify("G2", "42", "浦和記念（中央交流）　Ｊｐｎ２") == "nar"
    assert classify("G3", "43", "クイーン賞（中央交流）　Ｊｐｎ３") == "nar"


def test_地方の格表記も地方重賞():
    assert classify("Jpn1", "44", "帝王賞") == "nar"
    assert classify("JpnIII", "45", "川崎マイラーズ") == "nar"


def test_中央10場だけが中央扱い():
    assert JRA_COURSES == {"01", "02", "03", "04", "05", "06", "07", "08", "09", "10"}
    for c in JRA_COURSES:
        assert classify("G1", c, "天皇賞") == "g1"


def test_重賞でないものは弾く():
    assert classify(None, "05", "3歳未勝利") is None
    assert classify("OP特別", "05", "オープン特別") is None
    assert classify("Listed", "05", "リステッド") is None


def test_海外は場コードで判定する():
    """現状データは無いが、入ったときに効くこと。"""
    assert classify("G1", "O1", "英ダービー Derby Stakes") == "overseas_derby"
    assert classify("G1", "O1", "凱旋門賞") == "overseas_other"


# --------------------------------------------------------------- 点数計算


def test_全員の合計は必ずゼロになる():
    """🔴 ゼロサム。ここが崩れたら誰かの金額が湧いている。"""
    owners = [
        _owner(1, rank=1, wins=Wins(g1=2, g3=1), horses=3, raced=3, won=2),
        _owner(2, rank=2, wins=Wins(derby=1)),
        _owner(3, rank=3, wins=Wins(nar=3)),
        _owner(4, rank=4),
        _owner(5, rank=5, horses=2, raced=2, won=2),
        _owner(6, rank=6),
        _owner(7, rank=7, wins=Wins(g2=1)),
    ]
    scores = build_scores(owners)
    assert sum(s.special_prize for s in scores) == 0
    assert sum(s.rank_prize for s in scores) == 0
    assert sum(s.total_points for s in scores) == 0


def test_勝った人が他全員から徴収する():
    """7 人で中央G1を1勝 → 本人 +6,000 / 他は各 -1,000。"""
    owners = [_owner(i) for i in range(1, 8)]
    owners[0] = _owner(1, wins=Wins(g1=1))
    scores = {s.user_id: s for s in build_scores(owners)}
    assert scores[1].special_prize == 1000 * 6
    for uid in range(2, 8):
        assert scores[uid].special_prize == -1000


def test_ダービーは倍額():
    owners = [_owner(i) for i in range(1, 8)]
    owners[0] = _owner(1, wins=Wins(derby=1))
    scores = {s.user_id: s for s in build_scores(owners)}
    assert scores[1].special_prize == 2000 * 6
    assert scores[2].special_prize == -2000


def test_全馬出走賞と全馬勝利賞():
    owners = [_owner(i, horses=3) for i in range(1, 8)]
    owners[0] = _owner(1, horses=3, raced=3, won=3)  # 全馬出走かつ全馬勝利
    scores = {s.user_id: s for s in build_scores(owners)}
    assert scores[1].all_raced and scores[1].all_won
    assert scores[1].special_prize == (500 + 1000) * 6
    assert scores[2].special_prize == -(500 + 1000)


def test_指名頭数がゼロなら賞は付かない():
    """0 頭を「全馬出走した」と数えない（0 >= 0 で真になってしまう）。"""
    o = _owner(1, horses=0, raced=0, won=0)
    assert not o.all_raced
    assert not o.all_won


def test_順位賞は賞金順位で決まる():
    owners = [_owner(i, rank=i) for i in range(1, 8)]
    scores = {s.user_id: s for s in build_scores(owners)}
    assert [scores[i].rank_prize for i in range(1, 8)] == [
        20000, 10000, 5000, 0, -5000, -10000, -20000
    ]


def test_基本ptは合計に入らない():
    """移設元と同じ。賞金は表示だけで、精算額には効かない。"""
    owners = [_owner(i, rank=i, prize=i * 1000) for i in range(1, 8)]
    scores = build_scores(owners)
    for s in scores:
        assert s.basic_points == s.total_prize
        assert s.total_points == s.rank_prize + s.special_prize


def test_同点は同順位():
    owners = [_owner(i, rank=4) for i in range(1, 5)]  # 全員 0pt
    scores = build_scores(owners)
    assert [s.rank for s in scores] == [1, 1, 1, 1]


def test_誰もいなければ空を返す():
    assert build_scores([]) == []
