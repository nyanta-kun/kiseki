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
    ABOLISHED_PAPER_RANK_NAMES, CURRENT_PAPER_RANKS, RANK_9C_LEG_P3_MIN,
    RANK_9C_LEGS_MIN, RANK_9C_NE, RANK_9C_P3_SUM_MIN, rank_7c_select_axis,
    rank_7c_select_legs, rank_9c_daily_select,
)


def _cand(p3_sum, legs, ne=9):
    return {"n_entries": ne, "p3_sum_top2": p3_sum, "legs_9c": legs}


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
