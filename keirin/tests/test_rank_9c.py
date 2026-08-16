"""RANK_9C（9車のベースモデル）の回帰テスト。

固定するのは「壊れても例外が出ない」不変条件だけ:

1. **閾値が9車向けであること**（7C の値を持ち込むと母集団が 21.2% に潰れる）
2. **軸と相手の選び方は 7C と同じ関数**を使うこと（二重管理の禁止）
3. **9S/9A が廃止台帳にあり現行から消えていること**
4. 🔴 **看板の穴埋めが 9C を名乗ること**（付け替え忘れると存在しないランク名で入稿する）
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.strategy_wt import (  # noqa: E402
    ABOLISHED_PAPER_RANK_NAMES, CURRENT_PAPER_RANKS, RANK_9C_BIG_GRADE_MIN,
    RANK_9C_LEG_P3_MIN, RANK_9C_LEGS_MIN, RANK_9C_NE, RANK_9C_P3_SUM_MIN,
    RANK_9C_P3_SUM_MIN_BIG, rank_7c_select_axis, rank_7c_select_legs,
    rank_9c_daily_select, rank_9c_p3_sum_min,
)


def _cand(p3_sum, legs, ne=9, cup_grade=None):
    return {"n_entries": ne, "p3_sum_top2": p3_sum, "legs_9c": legs,
            "cup_grade": cup_grade}


def test_thresholds_are_calibrated_for_nine_cars():
    """🔴 7C の値（1.44）を持ち込まないこと。

    `pred_top3_pct` はレース内合計が3.0に正規化されるので、車数が増えると
    上位2車の合計が構造的に下がる。7C の 1.44 は9車では **21.2% しか通らない**
    （7車は53.7%）。掃引して 1.30 を選んである。
    """
    assert RANK_9C_P3_SUM_MIN == 1.30
    assert RANK_9C_P3_SUM_MIN < 1.44, "7C の閾値を持ち込んでいます"
    assert RANK_9C_NE == 9


def test_leg_floor_and_minimum_points():
    assert RANK_9C_LEG_P3_MIN == 0.15
    # ⚠️ 9車では最低点数のゲートは実質効かない（相手が7車あるので3点を割らない）。
    #    買い目として成立する最低限を置くだけ。
    assert RANK_9C_LEGS_MIN == 3


def test_daily_select_applies_both_gates():
    assert len(rank_9c_daily_select([_cand(1.35, [1, 2, 3])])) == 1
    assert rank_9c_daily_select([_cand(1.20, [1, 2, 3])]) == []      # 合計不足
    assert rank_9c_daily_select([_cand(1.35, [1, 2])]) == []          # 相手不足
    assert rank_9c_daily_select([_cand(1.35, [1, 2, 3], ne=7)]) == []  # 7車は対象外
    assert rank_9c_daily_select([_cand(None, [1, 2, 3])]) == []       # p3 欠損


def test_gate_is_raised_only_for_gii_and_above():
    """GII以上（cup_grade>=4）だけ 1.40。GIII と不明は 1.30 のまま。

    🔴 `cup_grade=3` は **GIII（記念）** であって「一般開催」ではない。
       ここを 1.40 にすると母集団が半減するのに、増える二軸は全帯で成り立つ
       単調性の分（+6.4pt）でしかない。較正がずれているのは GII/GI の
       1.30-1.40 帯だけ（-13.5pt・3.3σ・2025/2026 両年で再現）。
    """
    assert RANK_9C_P3_SUM_MIN_BIG == 1.40
    assert RANK_9C_BIG_GRADE_MIN == 4
    assert rank_9c_p3_sum_min(5) == RANK_9C_P3_SUM_MIN_BIG   # GI
    assert rank_9c_p3_sum_min(4) == RANK_9C_P3_SUM_MIN_BIG   # GII
    assert rank_9c_p3_sum_min(6) == RANK_9C_P3_SUM_MIN_BIG   # GP
    assert rank_9c_p3_sum_min(3) == RANK_9C_P3_SUM_MIN       # GIII は据え置き
    assert rank_9c_p3_sum_min(1) == RANK_9C_P3_SUM_MIN       # FII


def test_unknown_grade_keeps_the_original_floor():
    """🔴 `cup_grade` が None なら 1.30（従来動作へのフォールバック）。

    この列は 2026-08-14 に保存を始めたので、それ以前のレースと取得に失敗した
    開催では NULL。上位グレード側へ倒すと過去分の再構築が**静かに減る**。
    """
    assert rank_9c_p3_sum_min(None) == RANK_9C_P3_SUM_MIN
    assert len(rank_9c_daily_select([_cand(1.35, [1, 2, 3], cup_grade=None)])) == 1
    # cup_grade キー自体が無い候補（旧形式のJSON）も同じ扱いにする。
    assert len(rank_9c_daily_select([
        {"n_entries": 9, "p3_sum_top2": 1.35, "legs_9c": [1, 2, 3]}])) == 1


def test_gate_selects_differently_by_grade_at_the_same_p3_sum():
    """同じ p3合計 1.35 が GIII では通り GI では落ちる（帯の境界そのもの）。"""
    assert len(rank_9c_daily_select([_cand(1.35, [1, 2, 3], cup_grade=3)])) == 1
    assert rank_9c_daily_select([_cand(1.35, [1, 2, 3], cup_grade=5)]) == []
    assert len(rank_9c_daily_select([_cand(1.45, [1, 2, 3], cup_grade=5)])) == 1


def test_sorted_by_confidence():
    got = rank_9c_daily_select([_cand(1.31, [1, 2, 3]), _cand(1.90, [4, 5, 6])])
    assert [c["p3_sum_top2"] for c in got] == [1.90, 1.31]


def test_axis_and_legs_reuse_the_7c_functions():
    """🔴 選び方を9車用に書き直していないこと（7C の関数は車数に依存しない）。

    写すと「7C だけ直して 9C が古い」を作れる。違うのは閾値だけ。
    """
    p3 = {i: 0.5 - i * 0.05 for i in range(1, 10)}
    a1, a2, s = rank_7c_select_axis(p3)
    assert (a1, a2) == (1, 2)
    others = sorted(set(p3) - {a1, a2})
    legs = rank_7c_select_legs(others, p3, p3_min=RANK_9C_LEG_P3_MIN)
    assert legs and all(p3[x] >= RANK_9C_LEG_P3_MIN for x in legs)


def test_9c_registered_and_9s_9a_abolished():
    spec = next(s for s in CURRENT_PAPER_RANKS if s.rank == "RANK_9C")
    assert (spec.suffix, spec.label) == ("#9C", "9C")
    for old in ("RANK_9S", "RANK_9A"):
        assert old in ABOLISHED_PAPER_RANK_NAMES, f"{old} が廃止台帳にありません"
        assert all(s.rank != old for s in CURRENT_PAPER_RANKS)


def test_marquee_fill_uses_9c_for_nine_cars():
    """🔴 穴埋めのランク名を付け替えていること。

    9A 入稿22件中12件が穴埋めだった主経路。9A のまま残すと**存在しない
    ランク名で入稿**し、Web にも成績にも出なくなる。
    """
    src = (REPO / "scripts" / "submit_marquee_wt.py").read_text(encoding="utf-8")
    line = next(l for l in src.splitlines() if l.startswith("RANK_BY_CARS"))
    assert '9: "9C"' in line, f"穴埋めが 9C を名乗っていません: {line}"
    assert "9A" not in line


def test_submit_config_has_no_trifecta_switch():
    """🔴 7C の三連単切替を持ち込まないこと（9車では未検証）。"""
    from scripts.netkeirin_submit_wt import RANK_CONFIGS
    cfg = RANK_CONFIGS["9C"]
    assert cfg["n_cars"] == 9
    assert "trifecta_switch_key" not in cfg, "9車で未検証の三連単切替が入っています"
    assert "9S" not in RANK_CONFIGS and "9A" not in RANK_CONFIGS


# ---------------------------------------------------------------------------
# ライブ判定・再構築の経路（2026-08-14 追加）
#
# 🔴 ランクを新設・全廃したときに漏れやすい2経路をここで固定する。
#    ②ライブ判定（notify_prerace_wt）と ①候補生成側の rebuild 登録。
#    9C 新設時、実際に「9C の live 判定が無い / 廃止した 9S・9A の live 判定が
#    残っている」状態で1日稼働してしまった。
# ---------------------------------------------------------------------------


def _prerace_source() -> str:
    return (REPO / "scripts" / "notify_prerace_wt.py").read_text(encoding="utf-8")


def test_prerace_runs_9c_and_not_the_abolished_ranks():
    """🔴 live 判定が 9C を呼び、9S/9A は呼ばないこと。

    判定関数の**定義**は過去日の再採点用に残すので、見るのは
    「main から呼ばれているか」＝呼び出し行だけ。
    """
    import re
    src = _prerace_source()
    called = set(re.findall(r"=\s*_process_rank_([0-9a-z]+)_candidates\(", src))
    assert "9c" in called, "9C のライブ判定が呼ばれていない（買い判定が一生走らない）"
    for old in ("9s", "9a"):
        assert old not in called, (
            f"廃止した {old.upper()} のライブ判定がまだ呼ばれている。"
            " Web・集計から消したランクの行が毎日書き込まれ続ける")


def test_judge_9c_is_trio_only():
    """🔴 7C の三連単切替を live 側にも持ち込まないこと（9車で未検証）。"""
    src = _prerace_source()
    start = src.index("def judge_rank_9c(")
    body = src[start:src.index("\ndef ", start + 10)]
    assert '"trio"' in body
    assert "trifecta" not in body, "9C に三連単の分岐が入っている（未検証）"


def test_rebuild_and_backfill_exist_and_are_registered():
    """🔴 rebuild が実在し tail reconcile に登録されていること。

    未登録だと当月だけ live 行が残り、過去期間の rebuild 行と条件が食い違う
    （7A/7B・7H1 で実際に起きた）。
    """
    from tests.reconcile_spec import reconcile_specs
    assert (REPO / "scripts" / "backfill_9c_rank_wt.py").exists()
    assert (REPO / "scripts" / "rebuild_9c_walkforward_pg.py").exists()
    assert reconcile_specs().get("9c") == "9C", "tail reconcile に 9c:9C が無い"


def test_backfill_uses_nine_car_thresholds():
    """🔴 バックフィルが 7C の閾値を使っていないこと。"""
    src = (REPO / "scripts" / "backfill_9c_rank_wt.py").read_text(encoding="utf-8")
    assert "RANK_9C_LEG_P3_MIN" in src and "rank_9c_daily_select" in src
    assert "RANK_7C_LEG_P3_MIN" not in src
    assert "rank_7c_daily_select" not in src, "7C の選別関数を呼んでいる"
    assert "N_CAR = 9" in src
