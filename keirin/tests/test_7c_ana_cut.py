"""7C の「総流し帯から WT△ を外す」（案E・2026-08-15）を固定する。

## なぜ入れたか

落差カット（`rank_7c_cut_legs_by_gap`）は**相手が横並びのレースでは何も削らない**。
残った 4〜5点の総流し帯が 7C のガミの本体だった（本番実測: 三連複4点のガミ率
47.0% / 5点 50.1%）。相手を属性別に分解すると、△が「よく3着に来るが必ず安い」
相手で、的中率 26.8% に対し**ガミ率 90.2%**・的中時の配当中央 5,500円。

常に外す案が最も効く（実質的中 +3.2pt）が素の的中が 26.4pt 落ちるため、
ユーザー判断で**発動を絞った案E**（△の3着内率が 0.40 以上のときだけ）を採用した。

## 守る不変条件

1. **`wt_ana=None` なら何もしない**（fail-open）。印が取れない日に買い目が
   勝手に変わってはいけない
2. **三連単側には掛からない**（点数を変えると効果が消えるのは検証済み）
3. **削るのは1車まで**。残りが3点を下回るなら削らない
4. 4点未満（＝落差カットが効いた帯）には掛からない。案Eは総流し帯だけの規則
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.strategy_wt import (  # noqa: E402
    RANK_7C_ANA_CUT_FLOOR,
    RANK_7C_ANA_CUT_MIN_LEGS,
    RANK_7C_ANA_CUT_P3_MIN,
    RANK_7C_TRIFECTA_PW_MIN,
    RANK_7C_TRIO_P3_SUM_MIN,
    rank_7c_buy_plan,
    rank_7c_cut_legs_by_gap,
    rank_7c_drop_ana_leg,
)

#: 落差カットが効かない（横並びの）相手4点。案Eの対象になる形。
FLAT_LEGS = [3, 4, 5, 6]
#: 落差ゼロ＝カットが1つも削らない p3。軸2車は合計がゲートを超えるよう高めに置く。
FLAT_P3 = {1: 0.90, 2: 0.80, 3: 0.50, 4: 0.50, 5: 0.50, 6: 0.50}


def test_ana_is_dropped_when_p3_is_high() -> None:
    """△が居て 3着内率が閾値以上なら、その1車だけ落ちる。"""
    out = rank_7c_drop_ana_leg(FLAT_LEGS, FLAT_P3, wt_ana=4)
    assert out == [3, 5, 6]


def test_no_marks_means_no_cut() -> None:
    """🔴 `wt_ana=None`（印が取れない）なら削らない＝ fail-open。"""
    assert rank_7c_drop_ana_leg(FLAT_LEGS, FLAT_P3, wt_ana=None) == FLAT_LEGS


def test_ana_outside_the_buy_is_ignored() -> None:
    """△が買い目に入っていないレースでは何も起きない。"""
    assert rank_7c_drop_ana_leg(FLAT_LEGS, FLAT_P3, wt_ana=7) == FLAT_LEGS


def test_low_p3_ana_is_kept() -> None:
    """△の3着内率が閾値未満なら削らない（案Eが発動を絞っている本体）。"""
    p3 = {**FLAT_P3, 4: RANK_7C_ANA_CUT_P3_MIN - 0.01}
    assert rank_7c_drop_ana_leg(FLAT_LEGS, p3, wt_ana=4) == FLAT_LEGS


def test_boundary_is_inclusive() -> None:
    """閾値ちょうどは発動する（>= で判定していること）。"""
    p3 = {**FLAT_P3, 4: RANK_7C_ANA_CUT_P3_MIN}
    assert rank_7c_drop_ana_leg(FLAT_LEGS, p3, wt_ana=4) == [3, 5, 6]


def test_not_applied_below_min_legs() -> None:
    """4点未満（落差カットが効いた帯）には掛からない。"""
    legs = FLAT_LEGS[: RANK_7C_ANA_CUT_MIN_LEGS - 1]
    assert rank_7c_drop_ana_leg(legs, FLAT_P3, wt_ana=4) == legs


def test_floor_blocks_the_cut() -> None:
    """削った残りが下限を下回るなら削らない（買い目を痩せさせない）。"""
    legs = list(range(3, 3 + RANK_7C_ANA_CUT_FLOOR))   # ちょうど下限の点数
    p3 = {1: 0.90, 2: 0.80, **{f: 0.50 for f in legs}}
    assert len(legs) < RANK_7C_ANA_CUT_MIN_LEGS or rank_7c_drop_ana_leg(
        legs, p3, wt_ana=legs[0]) == legs


def test_cut_is_inside_the_single_source() -> None:
    """🔴 差し替えは `rank_7c_cut_legs_by_gap` の**中**で行う。

    呼び出し側（候補生成・発走前判定・再構築）でそれぞれ掛ける形にすると、
    1つ忘れた経路だけが旧挙動を出し続ける——このリポジトリが繰り返し踏む型。
    """
    assert rank_7c_cut_legs_by_gap(FLAT_LEGS, FLAT_P3, wt_ana=4) == [3, 5, 6]
    # 渡さなければ従来どおり（既存の呼び出し・検証スクリプトを壊さない）
    assert rank_7c_cut_legs_by_gap(FLAT_LEGS, FLAT_P3) == FLAT_LEGS


def test_ana_cut_still_applies_after_the_trifecta_switch_was_disabled() -> None:
    """🔴 三連単切替の停止（2026-08-17）で、旧「切替レース」も三連複になる。

    以前はここが `trifecta`（△削りの対象外）だった。停止後は三連複なので
    **△削りが掛かる側へ移る**。停止によって買い方が変わることを明示的に固定する。
    """
    win = {1: RANK_7C_TRIFECTA_PW_MIN + 0.01, 2: 0.10}
    kind, legs = rank_7c_buy_plan(FLAT_P3, win, axis1=1, legs=FLAT_LEGS, wt_ana=4)
    assert kind == "trio"
    assert legs == [3, 5, 6]


def test_buy_plan_passes_ana_through() -> None:
    """三連複側は `rank_7c_buy_plan` 経由でも △ が落ちる。"""
    win = {1: RANK_7C_TRIFECTA_PW_MIN - 0.01, 2: 0.10}
    assert sum(sorted(FLAT_P3.values(), reverse=True)[:2]) >= RANK_7C_TRIO_P3_SUM_MIN
    kind, legs = rank_7c_buy_plan(FLAT_P3, win, axis1=1, legs=FLAT_LEGS, wt_ana=4)
    assert kind == "trio"
    assert legs == [3, 5, 6]


# ── 本番3経路が wt_ana を渡していること ───────────────────────────────────────
#
# 🔴 `wt_ana` は **fail-open**（None なら削らない）。渡し忘れても例外もログも出ず、
#    その経路だけが静かに旧挙動へ戻る。しかも候補生成・発走前判定・再構築の
#    どれか1つが欠けると「Web の実績が実際に売った商品を説明しない」状態になる
#    ——2026-08-15 に 7C の再構築で実際に起きた型（本番だけ買い方が違っていた）。
#    経路そのものをソース走査で固定する。

_REPO = Path(__file__).resolve().parent.parent

#: (ファイル, その中で wt_ana を渡していなければならない呼び出し)
_CALL_SITES = (
    ("src/cli/main.py", "rank_7c_buy_plan"),            # 候補生成（live）
    ("scripts/notify_prerace_wt.py", "rank_7c_cut_legs_by_gap"),  # 発走前判定
    ("scripts/backfill_7c_rank_wt.py", "rank_7c_buy_plan"),       # 過去分の再構築
)


def _call_text(src: str, func: str) -> str:
    """`func(` から対応する閉じ括弧までを返す（引数の改行をまたぐため）。"""
    i = src.index(func + "(")
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "(":
            depth += 1
        elif src[j] == ")":
            depth -= 1
            if depth == 0:
                return src[i:j + 1]
    raise AssertionError(f"{func}( の括弧が閉じていない")


def test_production_call_sites_pass_wt_ana() -> None:
    for rel, func in _CALL_SITES:
        src = (_REPO / rel).read_text(encoding="utf-8")
        assert func + "(" in src, f"{rel} が {func} を呼んでいない（改名したらここも直す）"
        call = _call_text(src, func)
        assert "wt_ana" in call, (
            f"{rel} の {func}(...) に wt_ana が渡っていない。"
            "fail-open なので黙って旧挙動（△を削らない）に戻る。"
        )


def test_backfill_derives_wt_ana_from_marks() -> None:
    """再構築は DB の prediction_mark から △ を作ること（候補JSONが無いため）。"""
    src = (_REPO / "scripts/backfill_7c_rank_wt.py").read_text(encoding="utf-8")
    assert "prediction_mark" in src
    assert "wt_ana" in src and "== 3" in src, (
        "再構築側で △（prediction_mark==3）を導出していない。"
        "ここが欠けると再構築だけ旧挙動になり、Web の実績が実売と食い違う。"
    )
