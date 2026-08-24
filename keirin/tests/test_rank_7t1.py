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
    CURRENT_PAPER_RANKS, rank_7t1_daily_select, rank_7t1_is_cross_line,
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


@pytest.mark.parametrize("race_type", ["決勝", "チャレンジ決勝"])
def test_final_race_types_are_targets(race_type):
    """🔴 **2026-08-24 に「決勝のみ」へ絞った**（旧: 決勝系＝決勝/準決勝/特選/選抜）。

    旧テストは `["決勝", "S級決勝", "特選", "選抜", "特秀"]` を通していた。
    日次上限5が ROI を1ミリも改善していなかった（件数 1/3 で ROI 同じ）一方、
    **種別で絞ると質が上がる**と分かったため（決勝のみ×別ラインで
    2.20件/日・ROI 106.3% ↔ 旧 4.96件/日・81.8%）。
    設計と実測: `docs/rank_7t3_design.md` §5。
    """
    assert rank_7t1_is_target_race_type(race_type) is True


@pytest.mark.parametrize("race_type", ["予選", "一般", "初日特別", "ガールズ", None])
def test_non_final_series_race_types_are_excluded(race_type):
    assert rank_7t1_is_target_race_type(race_type) is False


@pytest.mark.parametrize("race_type", ["準決勝", "S級準決勝", "特選", "選抜", "特秀",
                                       "S級決勝"])
def test_semifinal_and_other_marquee_types_are_no_longer_targets(race_type):
    """🔴 **反転**（旧 `test_semifinal_is_a_target`・2026-08-24）。

    旧テストは「準決勝は対象に含む（検証をこの定義で行っている）」を守っていた。
    決勝のみへ絞る判断で不要になった。**部分一致にしていないこと**もここで固定する
    ——「決勝」で部分一致すると準決勝と S級決勝 を拾う。

    ⚠️ `S級決勝` が False なのは完全一致だから。実データの `race_type` は
       `決勝` / `チャレンジ決勝` の2値で、級班は別列にある。
    """
    assert rank_7t1_is_target_race_type(race_type) is False


def test_does_not_bind_the_marquee_keywords():
    """🔴 **反転**（旧 `test_does_not_use_the_marquee_predicate`・2026-08-24）。

    旧テストは `MARQUEE_KEYWORDS` を**正本から束縛していること**を要求していた。
    その構造だと「7T1 を絞ろう」としてキーワードを触ると**看板判定の正本まで動く**。
    7T1 は `RANK_7T1_RACE_TYPES` を自前で持ち、`marquee` へ依存しない。

    🔴 ソースの文字列一致で見てはいけない。docstring が説明のために名前を
       書くだけで落ちる（過去に実際に落ちた）。AST で**実際の import と
       呼び出しだけ**を見る。
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
    assert not ({"is_marquee_type", "MARQUEE_KEYWORDS"} & (imported | called)), (
        "7T1 が marquee へ依存している。看板判定の正本と結合してはいけない")


def test_race_type_is_defined_by_7t1_own_constant():
    """🔴 **反転**（旧 `test_race_type_keywords_are_not_redefined_here`・2026-08-24）。

    旧テストは「キーワードを 7T1 側で定義しない（正本＝marquee から束縛する）」を
    守っていた。二重管理を避ける意図は正しいが、**束ねる相手が間違っていた**
    ——7T1 の母集団と看板の母集団は別物（看板は準決勝を除外し、7T1 は決勝のみ）
    なので、同じ定数を共有すると片方を動かしたとき他方が黙って動く。

    7T1 は `RANK_7T1_RACE_TYPES` を自分で持つ。**看板のキーワードは写さない**
    （特選・選抜・特秀が入ると母集団が別物になる）。
    """
    import src.strategy_wt as sw

    assert sw.RANK_7T1_RACE_TYPES == ("決勝", "チャレンジ決勝")
    # 看板のキーワードを写していないこと
    for kw in ("特選", "選抜", "特秀"):
        assert kw not in sw.RANK_7T1_RACE_TYPES


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
    base = {"n_entries": RANK_7T1_NE, "race_type": "決勝", "is_cross_line": True,
            "legs": ["1-2-3"], "start_time": "10:00", "race_key": "20260813_11_01"}
    base.update(kw)
    return base


def test_daily_select_requires_final_and_cross_line():
    """⚠️ 準決勝は 2026-08-24 に母集団から外れた（決勝のみ）。"""
    assert len(rank_7t1_daily_select([_cand()])) == 1
    assert rank_7t1_daily_select([_cand(race_type="準決勝")]) == []
    assert rank_7t1_daily_select([_cand(race_type="予選")]) == []
    assert rank_7t1_daily_select([_cand(is_cross_line=False)]) == []
    assert rank_7t1_daily_select([_cand(n_entries=9)]) == []
    assert rank_7t1_daily_select([_cand(legs=[])]) == []


def test_daily_cap_is_disabled():
    """🔴 **反転**（旧 `test_daily_select_caps_at_five_per_day`・2026-08-24）。

    旧テストは「1日5本まで」（2026-08-18 ユーザー判断・7T1 が入稿全体の40%を
    占め表示的中率を押し下げていたため）を固定していた。

    **上限は ROI を1ミリも改善していなかった**——件数を 13.60 → 4.96件/日 と
    1/3 に削って ROI は 81.8% で同じ（CI [73,91] ↔ [66,98]）＝ ev による選別は
    無価値。代わりに母集団を決勝のみへ絞り 2.20件/日・ROI 106.3% にした。
    比率の問題は母集団が薄くなったこと自体で解決している。

    ⚠️ 上限の**機構自体は残す**（`daily_cap` 引数）。値を 0＝無効にしただけで、
       戻したくなったら定数1つで戻せる。
    """
    import src.strategy_wt as sw

    assert sw.RANK_7T1_DAILY_CAP == 0
    cands = [_cand(race_key=f"20260813_11_{i:02d}") for i in range(1, 21)]
    assert len(rank_7t1_daily_select(cands)) == 20
    # 機構は生きている（明示的に渡せば効く）
    assert len(rank_7t1_daily_select(cands, daily_cap=5)) == 5


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


def test_netkeirin_priority_is_above_7s():
    """🔴 **反転**（旧 `test_netkeirin_priority_is_below_7c`・2026-08-24 ユーザー判断）。

    旧テストは「7T1 は 7C より後ろ」を守っていた。理由は「7C が的中体験を担い、
    7T1 が先に取ると表示的中3%の商品が的中体験を奪う」。

    絞り込み後の 7T1（決勝のみ×別ライン）は **2.20件/日**しか無く、下に置くと
    7S に取られてほぼ出ない（実測: 7T1 は決勝の16%しか取れていなかった）。
    決勝では ROI が 7S より 22〜31pt 高い。受け入れたトレードは
    **7S の表示的中 −0.22pt**・ROI 不変（決勝は 7S 母集団の 5.7%）。

    🔴 **7T1 は 7T3 の直上**であること。この順序が「別ラインは 7T1・
       同ラインは 7T3」の棲み分けを作っている（7T3 はライン条件を持たない）。
    設計と実測: `docs/rank_7t3_design.md` §9
    """
    from scripts.netkeirin_submit_wt import RANK_ORDER

    assert RANK_ORDER.index("7T1") < RANK_ORDER.index("7S")
    assert RANK_ORDER.index("7T1") < RANK_ORDER.index("7C")
    # 7T3 は 7T1 の**直後**（間に他ランクを挟まない）
    assert RANK_ORDER.index("7T3") == RANK_ORDER.index("7T1") + 1


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


# ── 日次上限（2026-08-18 新設）────────────────────────────────────────

def _cap_cand(day: str, key: str, ev: float | None) -> dict:
    return {"n_entries": 7, "race_type": "決勝", "is_cross_line": True,
            "legs": ["1-2-3"], "race_date": day, "race_key": key,
            "start_time": key, "ev": ev}


def test_daily_cap_keeps_the_top_n_by_expected_value() -> None:
    """上限を掛けたときは**期待値の高い順**に残ること（機構の検査）。

    ⚠️ 2026-08-24 に既定は 0（無効）になった。上限そのものは ROI を改善して
       いなかったため（`test_daily_cap_is_disabled`）。**機構は残す**ので、
       ここでは明示的に `daily_cap=5` を渡して順位づけだけを固定する。
    """
    import src.strategy_wt as sw
    cands = [_cap_cand("2026-08-01", f"a{i}", 1.0 + i * 0.1) for i in range(8)]
    got = sw.rank_7t1_daily_select(cands, daily_cap=5)
    assert len(got) == 5
    assert sorted(round(c["ev"], 2) for c in got) == [1.3, 1.4, 1.5, 1.6, 1.7]


def test_daily_cap_is_applied_per_race_date() -> None:
    """🔴 上限は**日付ごと**に掛ける。

    本関数は日次の候補生成（1日分）だけでなく、過去分バックフィルからも
    **月単位**で呼ばれる（`build_7t1_candidates.build` に date_from/date_to を渡す）。
    全体へ一括で掛けると、その月ぜんぶで5本しか残らず再構築が本番と別物になる。
    """
    import src.strategy_wt as sw
    cands = ([_cap_cand("2026-08-01", f"a{i}", 1.0 + i * 0.1) for i in range(8)]
             + [_cap_cand("2026-08-02", f"b{i}", 1.0 + i * 0.1) for i in range(8)])
    got = sw.rank_7t1_daily_select(cands, daily_cap=5)
    from collections import Counter
    assert Counter(c["race_date"] for c in got) == {"2026-08-01": 5, "2026-08-02": 5}


def test_daily_cap_does_not_drop_candidates_without_ev() -> None:
    """`ev` を持たない旧形式の候補は落とさない（上限に余裕があれば残る）。"""
    import src.strategy_wt as sw
    cands = [_cap_cand("2026-08-01", "a", None), _cap_cand("2026-08-01", "b", 1.5)]
    got = sw.rank_7t1_daily_select(cands)
    assert {c["race_key"] for c in got} == {"a", "b"}


def test_daily_cap_ranks_ev_above_missing_ev() -> None:
    """上限に達したら `ev` のある候補が優先される（欠損は最下位）。"""
    import src.strategy_wt as sw
    cands = ([_cap_cand("2026-08-01", f"x{i}", None) for i in range(3)]
             + [_cap_cand("2026-08-01", f"y{i}", 2.0 + i) for i in range(5)])
    got = sw.rank_7t1_daily_select(cands, daily_cap=5)
    assert {c["race_key"] for c in got} == {"y0", "y1", "y2", "y3", "y4"}
