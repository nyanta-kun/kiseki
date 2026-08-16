"""RANK_7T1（三連単・高配当枠）の回帰テスト。

固定するのは「壊れても例外が出ない」不変条件だけを選んである
（このリポジトリは入稿・採点経路が黙って壊れる事故を繰り返している）:

1. **母集団**: 決勝系レース（決勝・**準決勝**・特選・選抜）だけを通すこと。
   キーワードを写していないこと。看板判定（準決勝を除外する）と**混同しない**こと
2. **母集団**: 3着内率の上位2車が別ラインのレースだけを通すこと
3. **自己整合**: 買った点が全て「払戻 >= 目標額」に届く予測オッズであること
   ＝ この枠の設計そのもの。ここが崩れると商品の意味が消える
4. **軸1は指数上位2車から選ぶ**こと（ブランド制約）
5. **軸1が必ず1着・軸2が必ず2着**であること（前身 7H3 とは逆向き）
6. **賭け金は均等**で、合計が予算ちょうど
7. **入稿**: 候補JSON → 入稿行の変換で点数・金額・印が保たれること
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.strategy_wt import (  # noqa: E402
    RANK_7T1_AXIS1_TOP_N, RANK_7T1_BUDGET, RANK_7T1_KMAX, RANK_7T1_NE,
    RANK_7T1_TARGET_PAYOUT, RANK_7T1_UNIT, ABOLISHED_PAPER_RANK_NAMES,
    CURRENT_PAPER_RANKS, RANK_7T1_SKIP_MARQUEE, rank_7t1_daily_select,
    rank_7t1_is_cross_line, rank_7t1_is_marquee_race_type,
    rank_7t1_is_target_race_type, rank_7t1_pl_prob, rank_7t1_select, rank_7t1_stakes,
)


def _probs(**kw) -> dict[int, float]:
    """{車番: 3着内率}。7車ぶん埋める。"""
    base = {i: 0.05 for i in range(1, RANK_7T1_NE + 1)}
    base.update({int(k[1:]): v for k, v in kw.items()})
    return base


def _win(**kw) -> dict[int, float]:
    base = {i: 0.05 for i in range(1, RANK_7T1_NE + 1)}
    base.update({int(k[1:]): v for k, v in kw.items()})
    return base


# 1位=1番 / 2位=2番 の標準形
STD = _probs(c1=0.90, c2=0.80, c3=0.40, c4=0.30, c5=0.25, c6=0.20, c7=0.15)
STD_W = _win(c1=0.40, c2=0.25, c3=0.12, c4=0.08, c5=0.06, c6=0.05, c7=0.04)

# 上位2車（1番・2番）が別ライン
CROSS_LG = {1: 1, 2: 2, 3: 1, 4: 2, 5: 3, 6: 3, 7: 1}
CROSS_LP = {1: 1, 2: 1, 3: 2, 4: 2, 5: 1, 6: 2, 7: 3}
# 上位2車が同一ラインの隣接
SAME_LG = {1: 1, 2: 1, 3: 2, 4: 2, 5: 3, 6: 3, 7: 1}
SAME_LP = {1: 1, 2: 2, 3: 1, 4: 2, 5: 1, 6: 2, 7: 3}


def _board(odds: float = 400.0) -> dict[tuple[int, int, int], float]:
    """全順列が一律 `odds` 倍の板。足切りの効き方だけを見たいときに使う。"""
    import itertools
    return {t: odds for t in itertools.permutations(range(1, RANK_7T1_NE + 1), 3)}


# ---------------------------------------------------------------- 母集団


@pytest.mark.parametrize("race_type", ["決勝", "S級決勝", "特選", "選抜", "特秀"])
def test_final_series_race_types_are_targets(race_type):
    """前身 7H3 とは**母集団が真逆**。決勝系レースを通すこと。"""
    assert rank_7t1_is_target_race_type(race_type) is True


@pytest.mark.parametrize("race_type", ["予選", "一般", "初日特別", "ガールズ", None])
def test_non_final_series_race_types_are_excluded(race_type):
    assert rank_7t1_is_target_race_type(race_type) is False


def test_semifinal_is_a_target():
    """🔴 **準決勝は対象に含む**（看板判定とはここが違う）。

    検証をこの定義で行っている。準決勝は母集団の33%を占め、成績は本体と
    区別できない（目標20万での検証時 20万超 2.17% vs 2.02%）。
    `marquee.is_marquee_type()` をそのまま使うと準決勝が落ち、13.4→8.9本/日で
    **頻度だけが落ちる**（実装時に実際に踏んだ）。
    """
    assert rank_7t1_is_target_race_type("準決勝") is True
    assert rank_7t1_is_target_race_type("S級準決勝") is True


def test_does_not_use_the_marquee_predicate():
    """看板判定（準決勝を除外する）を**呼んで**いないこと。

    🔴 ソースの文字列一致で見てはいけない。docstring が「使ってはいけない」と
       説明のために名前を書くだけで落ちる（実際に落ちた）。AST で
       **実際の import と呼び出しだけ**を見る。
    """
    import ast

    src = (REPO / "src" / "strategy_wt.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef)
              and n.name == "rank_7t1_is_target_race_type")
    imported: set[str] = set()
    called: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.ImportFrom):
            imported |= {a.name for a in node.names}
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            called.add(node.func.id)
    assert "is_marquee_type" not in imported | called, (
        "7T1 が看板判定を使っている。準決勝が落ちて母集団が33%減る")
    assert "MARQUEE_KEYWORDS" in imported, "正本からキーワードを束縛すること"


def test_race_type_keywords_are_not_redefined_here():
    """レース種別キーワードを 7T1 側で定義していないこと（二重管理の禁止）。

    写した瞬間に「★は付くのに入稿されない」またはその逆を作れる。
    """
    src = (REPO / "src" / "strategy_wt.py").read_text(encoding="utf-8")
    section = src[src.index("RANK_7T1_NE ="):src.index("def rank_7t1_is_cross_line(")]
    for kw in ("決勝", "特選", "選抜", "特秀"):
        assert f'"{kw}"' not in section, (
            f"看板キーワード {kw} を 7T1 側で定義している。正本から束縛すること")


def test_cross_line_uses_top2_of_p3():
    assert rank_7t1_is_cross_line(STD, CROSS_LG, CROSS_LP) is True
    assert rank_7t1_is_cross_line(STD, SAME_LG, SAME_LP) is False


def test_same_line_but_not_adjacent_counts_as_cross():
    """同一ラインでも**隣接していなければ**別ライン扱い（検証時の定義）。"""
    lg = {**SAME_LG}
    lp = {**SAME_LP, 2: 3}          # 1番=先頭 / 2番=3番手（間に1車いる）
    assert rank_7t1_is_cross_line(STD, lg, lp) is True


def test_missing_line_info_is_treated_as_cross():
    """ライン欠損で**静かに母集団が減らない**こと。"""
    assert rank_7t1_is_cross_line(STD, {1: None, 2: None}, {}) is True


# ---------------------------------------------- 自己整合（この枠の設計そのもの）


def test_every_bought_point_reaches_the_target_payout():
    """🔴 買った全ての点が「払戻 >= 目標額」に届くこと。

    N点等分なら 払戻 >= T ⟺ オッズ >= T×N/予算。ここが崩れたら、
    届かない点に賭け金を配って残りを薄めているだけで商品の意味が消える。
    """
    for odds in (60.0, 120.0, 400.0, 1200.0):
        got = rank_7t1_select(STD, STD_W, _board(odds))
        if got is None:
            continue
        _a1, _a2, legs = got
        stakes = rank_7t1_stakes(legs)
        for leg in legs:
            assert stakes[leg] * odds >= RANK_7T1_TARGET_PAYOUT, (
                f"オッズ{odds}倍・{len(legs)}点で {leg} が目標額に届かない")


def test_cut_uses_rounded_min_stake_not_ideal_split():
    """🔴 足切りを「理想の等分」で計算していないこと（実装時に踏んだバグ）。

    10,000円を3点は 3,333円ずつではなく **3,300 / 3,300 / 3,400**（最低単位100円）。
    `T×N/予算` で切ると 60.0倍が通ってしまい、3,300×60 = 198,000円 で
    **最小の点だけ目標に届かない**。
    """
    from src.strategy_wt import rank_7t1_min_stake

    assert rank_7t1_min_stake(3) == 3300          # 3,333 ではない
    assert rank_7t1_min_stake(1) == RANK_7T1_BUDGET
    # 理想の等分（1万÷3点＝3,333円）から逆算した足切り。実際の最小額は
    # 3,300円なので、この倍率で切ると最小の点だけ目標に届かない。
    ideal_bar = RANK_7T1_TARGET_PAYOUT * 3 / RANK_7T1_BUDGET
    got = rank_7t1_select(STD, STD_W, _board(ideal_bar))
    if got is not None:
        stakes = rank_7t1_stakes(got[2])
        assert min(stakes.values()) * ideal_bar >= RANK_7T1_TARGET_PAYOUT


def test_point_count_grows_with_odds():
    """オッズが高いほど多く買える（足切り閾値が点数に比例するため）。"""
    n_low = rank_7t1_select(STD, STD_W, _board(60.0))
    n_high = rank_7t1_select(STD, STD_W, _board(1200.0))
    assert n_low is not None and n_high is not None
    assert len(n_low[2]) < len(n_high[2])


def test_no_points_when_board_is_too_cheap():
    """1点買っても目標額に届かない板では**何も買わない**こと。"""
    too_cheap = RANK_7T1_TARGET_PAYOUT / RANK_7T1_BUDGET - 1.0
    assert rank_7t1_select(STD, STD_W, _board(too_cheap)) is None


def test_never_exceeds_kmax():
    got = rank_7t1_select(STD, STD_W, _board(100000.0))
    assert got is not None
    assert len(got[2]) <= RANK_7T1_KMAX


def test_axis1_comes_from_top_n_of_p3():
    """軸1は指数上位 N 車から選ぶこと（ブランド制約）。"""
    order = [f for f, _ in sorted(STD.items(), key=lambda kv: (-kv[1], kv[0]))]
    got = rank_7t1_select(STD, STD_W, _board(400.0))
    assert got is not None
    assert got[0] in order[:RANK_7T1_AXIS1_TOP_N]


def test_axis1_is_first_and_axis2_is_second_in_every_leg():
    """🔴 軸1が1着・軸2が2着。前身 7H3（軸を2〜3着に置く）とは**逆向き**。"""
    got = rank_7t1_select(STD, STD_W, _board(400.0))
    assert got is not None
    a1, a2, legs = got
    for leg in legs:
        cars = [int(x) for x in leg.split("-")]
        assert cars[0] == a1 and cars[1] == a2
        assert cars[2] not in (a1, a2)


def test_no_absolute_probability_threshold_in_selection():
    """🔴 確率の**絶対閾値**でレースを切っていないこと。

    前身 7H3 が壊れた原因そのもの（軸積 >= 0.70 が確率の出どころ次第で
    母集団を1.4倍にした）。確率を一律にスケールしても、板が同じなら
    買い目の**点数**は変わってはいけない。
    """
    scaled = {k: v * 0.5 for k, v in STD.items()}
    a = rank_7t1_select(STD, STD_W, _board(400.0))
    b = rank_7t1_select(scaled, STD_W, _board(400.0))
    assert a is not None and b is not None
    assert len(a[2]) == len(b[2])


def test_select_returns_none_without_predicted_odds():
    """板が無ければ**黙って買わない**（別の足切りへフォールバックしない）。"""
    assert rank_7t1_select(STD, STD_W, {}) is None


# ---------------------------------------------------------------- 賭け金


def test_stakes_use_whole_budget_and_min_unit():
    got = rank_7t1_select(STD, STD_W, _board(400.0))
    assert got is not None
    stakes = rank_7t1_stakes(got[2])
    assert sum(stakes.values()) == RANK_7T1_BUDGET
    assert all(v >= RANK_7T1_UNIT and v % RANK_7T1_UNIT == 0 for v in stakes.values())


def test_stakes_are_equal():
    """🔴 均等であること。確率で重み付けすると軽い点が目標額に届かなくなる。"""
    legs = ["1-2-3", "1-2-4", "1-2-5"]
    stakes = rank_7t1_stakes(legs)
    assert max(stakes.values()) - min(stakes.values()) <= RANK_7T1_UNIT


def test_pl_prob_is_a_probability_and_order_sensitive():
    p = rank_7t1_pl_prob(STD_W, "1-2-3")
    assert p is not None and 0.0 < p < 1.0
    assert rank_7t1_pl_prob(STD_W, "3-2-1") != p
    assert rank_7t1_pl_prob({}, "1-2-3") is None


# ---------------------------------------------------------------- 日次選出


def _cand(**kw) -> dict:
    # ⚠️ 既定を「準決勝」にしてあるのは、2026-08-17 に**看板レースを母集団から
    #    外した**ため（`RANK_7T1_SKIP_MARQUEE`）。決勝/特選/選抜は看板なので
    #    既定にすると全ケースが0件になる。
    base = {"n_entries": RANK_7T1_NE, "race_type": "準決勝", "is_cross_line": True,
            "legs": ["1-2-3"], "start_time": "10:00", "race_key": "20260813_11_01"}
    base.update(kw)
    return base


def test_daily_select_requires_final_series_and_cross_line():
    assert len(rank_7t1_daily_select([_cand()])) == 1
    assert rank_7t1_daily_select([_cand(race_type="予選")]) == []
    assert rank_7t1_daily_select([_cand(is_cross_line=False)]) == []
    assert rank_7t1_daily_select([_cand(n_entries=9)]) == []
    assert rank_7t1_daily_select([_cand(legs=[])]) == []


def test_daily_select_excludes_marquee_races():
    """🔴 看板レース（決勝/特選/選抜）は出さない（2026-08-17・ユーザー判断）。

    看板は当日売上の84%が集中するレースで、そこでの表示的中は
    7S 32.5% / 7C 26.1% に対し 7T1 は 4.2% しかない。的中体験を優先する。

    ⚠️ 入稿順は既に 7S > 7C > 7T1 なので、**優先順位を下げても効かない**
       （看板で 7T1 が出るのは両方がゲートに落ちたレースだから）。
       母集団から外すことでしか変わらない。
    ⚠️ 外しても看板に穴は空かない（`submit_marquee_wt` が 7S で埋める）。
    """
    assert RANK_7T1_SKIP_MARQUEE is True
    for rt in ("決勝", "特選", "初特選", "選抜", "チャレンジ決勝", "ガールズ決勝"):
        assert rank_7t1_daily_select([_cand(race_type=rt)]) == [], rt
    # 準決勝は看板ではないので**残る**（7T1 の母集団はここが本体になる）
    for rt in ("準決勝", "S級準決勝", "チャレンジ準決勝"):
        assert len(rank_7t1_daily_select([_cand(race_type=rt)])) == 1, rt


def test_marquee_and_target_race_type_are_different_concepts():
    """🔴 2つの判定を混同しない。違いは「準決勝」ただ1点。"""
    assert rank_7t1_is_target_race_type("準決勝") is True
    assert rank_7t1_is_marquee_race_type("準決勝") is False
    for rt in ("決勝", "特選", "選抜"):
        assert rank_7t1_is_target_race_type(rt) is True
        assert rank_7t1_is_marquee_race_type(rt) is True
    assert rank_7t1_is_marquee_race_type("予選") is False
    assert rank_7t1_is_marquee_race_type(None) is False


def test_daily_select_is_not_a_daily_rank_cut():
    """件数を日ごとの相対順位で切らないこと（切り捨てが件数を系統的に減らす）。"""
    cands = [_cand(race_key=f"20260813_11_{i:02d}") for i in range(1, 21)]
    assert len(rank_7t1_daily_select(cands)) == 20


# ---------------------------------------------------------------- 登録・入稿


def test_registered_in_paper_rank_registry():
    spec = next(s for s in CURRENT_PAPER_RANKS if s.rank == "RANK_7T1")
    assert (spec.suffix, spec.label) == ("#7T1", "7T1")
    assert spec.in_header_total is False      # 穴推奨系はヘッダー合計に混ぜない


def test_predecessor_7h3_is_abolished_and_gone():
    """🔴 旧 RANK_7H3 が現行から消え、廃止台帳に載っていること。

    枠を作り替えたのに旧名が現行に残ると、集計に**逆のセマンティクス**が混ざる。
    """
    assert "RANK_7H3" in ABOLISHED_PAPER_RANK_NAMES
    assert all(s.rank != "RANK_7H3" for s in CURRENT_PAPER_RANKS)


def test_netkeirin_priority_is_below_7c():
    """入稿の優先順位で 7T1 が 7C より後ろにあること。

    7C（実質的中率39.0%）が的中体験を担い、7T1 は高配当担当。**看板を取り合う**
    ので、7T1 が先に取ると表示的中3%の商品が的中体験を奪う。
    """
    from scripts.netkeirin_submit_wt import RANK_ORDER
    assert RANK_ORDER.index("7T1") > RANK_ORDER.index("7C")
    assert RANK_ORDER.index("7T1") < RANK_ORDER.index("7B")


def test_netkeirin_normalize_preserves_points_and_stakes():
    """候補JSON → 入稿行で点数・合計金額・印が保たれること。"""
    from scripts.netkeirin_submit_wt import RANK_CONFIGS, _normalize_7t1_candidate

    legs = ["1-2-3", "1-2-5"]
    stakes = rank_7t1_stakes(legs)
    cand = {"race_key": "20260813_11_01", "axis1": 1, "axis2": 2,
            "partners": [3, 5], "legs": legs, "stakes": stakes}
    rows, marks, axis1, axis2 = _normalize_7t1_candidate(cand, RANK_CONFIGS["7T1"])
    assert len(rows) == len(legs)                       # 1点=1行
    assert sum(r.stake_per_line for r in rows) == RANK_7T1_BUDGET
    assert (axis1, axis2) == (1, 2)
    assert marks[1] == "◎" and marks[2] == "○"
    assert marks[3] == "△" and marks[5] == "△"


def test_stake_note_says_equal_even_with_rounding_remainder():
    """🔴 端数だけの差で「オッズに応じて配分」と**嘘の説明**を出さないこと。

    10,000円を3点は 3,400 / 3,300 / 3,300 になる（最小単位100円）。
    「単価が1種類か」で判定すると傾斜扱いになり、均等に置いているのに
    顧客向け本文に「オッズに応じて配分しています」と出る（実際に出た）。
    """
    from scripts.netkeirin_submit_wt import (
        RANK_CONFIGS, _normalize_7t1_candidate, _stake_note_for)

    for n in (1, 2, 3, 4, 5):
        legs_raw = [f"1-2-{c}" for c in range(3, 3 + n)]
        cand = {"race_key": "20260813_11_01", "axis1": 1, "axis2": 2,
                "partners": [int(x.split("-")[2]) for x in legs_raw],
                "legs": legs_raw, "stakes": rank_7t1_stakes(legs_raw)}
        rows, _m, _a1, _a2 = _normalize_7t1_candidate(cand, RANK_CONFIGS["7T1"])
        note = _stake_note_for("7T1", rows)
        assert "均等" in note, f"{n}点で均等と説明されない: {note}"
        assert "オッズに応じて" not in note, f"{n}点で傾斜配分と誤説明: {note}"


def test_netkeirin_normalize_rebuilds_stakes_when_json_is_stale():
    """候補JSONの stakes が買い目と食い違ったら**同じ関数で組み直す**こと。

    別式で埋めると記録側と入稿側が静かに食い違う（7H1 で実際に起きた型）。
    """
    from scripts.netkeirin_submit_wt import RANK_CONFIGS, _normalize_7t1_candidate

    cand = {"race_key": "20260813_11_01", "axis1": 1, "axis2": 2,
            "partners": [3, 5], "legs": ["1-2-3", "1-2-5"],
            "stakes": {"9-9-9": 10000}}                 # 別レースの残骸を想定
    rows, _marks, _a1, _a2 = _normalize_7t1_candidate(cand, RANK_CONFIGS["7T1"])
    assert sum(r.stake_per_line for r in rows) == RANK_7T1_BUDGET


def test_netkeirin_normalize_rejects_empty_candidate():
    from scripts.netkeirin_submit_wt import RANK_CONFIGS, _normalize_7t1_candidate

    with pytest.raises(ValueError):
        _normalize_7t1_candidate(
            {"axis1": 1, "axis2": 2, "legs": []}, RANK_CONFIGS["7T1"])


def test_single_point_is_allowed():
    """1点買いが約半数を占める設計。2点未満を弾いてはいけない。"""
    from scripts.netkeirin_submit_wt import RANK_CONFIGS, _normalize_7t1_candidate

    cand = {"race_key": "20260813_11_01", "axis1": 1, "axis2": 2,
            "partners": [3], "legs": ["1-2-3"], "stakes": rank_7t1_stakes(["1-2-3"])}
    rows, _marks, _a1, _a2 = _normalize_7t1_candidate(cand, RANK_CONFIGS["7T1"])
    assert len(rows) == 1
    assert rows[0].stake_per_line == RANK_7T1_BUDGET
