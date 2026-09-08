"""型F の①代替への帯下差込 と ②本命の下限5倍 を固定する（2026-09-08）。

ユーザー質問「F_hit も同じ問題があるか／F は波乱想定なので対応しない方がいいか」から。

🔴 **型C と構造が逆**。`C_hit` は常に帯15倍で、差込を入れたときだけゲートに落ちる。
   `F_hit` は**本命に帯が無く**（76〜77%）、本命がゲートに 23〜24% 落ちて帯15倍の
   代替へ回る。だから「帯が順当な目を切っていた」のは型F 全体の 5.5 / 5.1% だけ
   （型C は 29.7 / 26.9%）＝**伸びしろは 1/5**。

固定するのは3点。どれが壊れても**エラーは出ず、静かに別の商品になる**:

1. 本命の下限は **5.0倍**（掃引で両窓とも CI が 0 を跨がないのはここだけ。
   6倍・8倍は確認窓で跨ぐ）。帯15倍への置換ではない
2. 代替は **「帯15倍＋差込」→「帯15倍」の2段**。2段目が無いと差込がゲートを
   割ったレースで商品が消える（実測 件/日 −1.3〜2.0%）
3. 差込は代替側だけ。**本命には差し込まない**（本命に帯が無く、差し込む対象が定義できない）

実測: `docs/type_lab/type_f.md` /
再現: `scripts/exp_type_lab/type_f_underband.py`（diag / arms / conc / floor / combo）
"""
from __future__ import annotations

import itertools

from src.stake_allocation import MIN_MEAN_PAYOUT
from src.type_lab import (
    GATE_FALLBACK, PLANS, RaceShape, build_legs, build_with_gate_fallback,
    mean_expected_payout,
)

PERMS = list(itertools.permutations(range(1, 8), 3))


def _shape() -> RaceShape:
    """型F（大混戦）の盤面。`build_legs` は order しか見ない。"""
    return RaceShape("F", 1.10, 3, 0.10, False, tuple(range(1, 8)), 1.9)


def _board(cheap: dict[tuple, float], rest: float = 40.0):
    po = {c: rest for c in PERMS} | cheap
    tot = sum(1.0 / v for v in po.values())
    return po, {c: (1.0 / v) / tot for c, v in po.items()}


# ───────────────────── ② 本命の下限 ─────────────────────

def test_primary_has_a_floor_of_five():
    """🔴 掃引で両窓とも有意だったのは 5.0倍だけ（3/4倍は確認窓で、6/8倍も同様に跨ぐ）。"""
    assert PLANS["F_hit"].min_odds == 5.0
    assert PLANS["F_hit"].underband_min == 0.0, "本命に差込は入れない"


def test_the_primary_stops_buying_the_very_cheap():
    """下限より安い目は買わない（帯が無かった頃は最安 3.5倍まで買っていた）。"""
    po, pr = _board({(1, 2, 3): 3.5, (1, 2, 4): 4.9, (1, 2, 5): 5.1})
    legs = build_legs(_shape(), PLANS["F_hit"], po, pr)
    assert legs
    assert all(po[c] >= 5.0 for c in legs)
    assert (1, 2, 3) not in legs and (1, 2, 4) not in legs


def test_the_floor_is_not_a_band():
    """🔴 5倍は「安すぎる目を落とす」下限であって帯ではない。帯15倍への丸ごと
    置換は 2026-09-03 に測って不採用（表示的中 −0.8pt）なので、5〜15倍の目は
    本命でそのまま買うこと。"""
    po, pr = _board({(1, 2, 3): 8.0, (1, 2, 4): 9.0})
    legs = build_legs(_shape(), PLANS["F_hit"], po, pr)
    assert (1, 2, 3) in legs and (1, 2, 4) in legs


# ───────────────────── ① 代替への差込と連鎖 ─────────────────────

def test_fallback_is_a_two_step_chain():
    """🔴 代替は2段。1段目に差込・2段目は受け皿（差込なし）。"""
    f0, f1 = GATE_FALLBACK["F_hit"]
    assert (f0.min_odds, f0.underband_min) == (15.0, 5.0)
    assert (f1.min_odds, f1.underband_min) == (15.0, 0.0)


def test_the_second_step_catches_what_the_insert_breaks():
    """🔴 差込がゲートを割ったら2段目で拾う＝**在庫を落とさない**。

    2段目が無いと、この盤面では商品が丸ごと消える（実測 件/日 −1.3〜2.0%）。
    """
    shape = _shape()
    # 本命（下限5倍）が落ち、代替1段目（帯15＋5.0倍の差込）も落ち、
    # 代替2段目（帯15のみ）だけが通る板を作る。
    po = {c: 28.0 for c in PERMS} | {(1, 2, 3): 5.0, (1, 2, 4): 5.2, (1, 2, 5): 5.4,
                                     (1, 2, 6): 5.6, (1, 2, 7): 5.8, (1, 3, 2): 6.0}
    tot = sum(1.0 / v for v in po.values())
    pr = {c: (1.0 / v) / tot for c, v in po.items()}

    got = build_with_gate_fallback(shape, PLANS["F_hit"], po, pr, n_entries=7)
    assert got is not None
    legs, stakes, used = got
    assert used is GATE_FALLBACK["F_hit"][1], "2段目で拾えていない"
    assert mean_expected_payout(stakes, po) > MIN_MEAN_PAYOUT
    assert all(po[c] >= 15.0 for c in stakes), "2段目は差込なし"


def test_without_the_second_step_the_product_disappears(monkeypatch):
    """🔴 上のテストの裏取り: 2段目を外すと**ゲートを通る商品が無くなる**。

    これが「在庫を落とさない」の実体。並びを1段に戻したら必ずここが落ちる。
    """
    import src.type_lab as T
    shape = _shape()
    po = {c: 28.0 for c in PERMS} | {(1, 2, 3): 5.0, (1, 2, 4): 5.2, (1, 2, 5): 5.4,
                                     (1, 2, 6): 5.6, (1, 2, 7): 5.8, (1, 3, 2): 6.0}
    tot = sum(1.0 / v for v in po.values())
    pr = {c: (1.0 / v) / tot for c, v in po.items()}
    monkeypatch.setitem(T.GATE_FALLBACK, "F_hit", (GATE_FALLBACK["F_hit"][0],))
    got = build_with_gate_fallback(shape, PLANS["F_hit"], po, pr, n_entries=7)
    assert not (got and mean_expected_payout(got[1], po) > MIN_MEAN_PAYOUT)


def test_the_chain_stops_at_the_first_step_that_passes():
    """1段目が通るなら2段目は使わない（既存の買い目を書き換えない）。"""
    shape = _shape()
    po, pr = _board({(1, 2, 3): 8.0}, rest=60.0)
    got = build_with_gate_fallback(shape, PLANS["F_hit"], po, pr, n_entries=7,
                                   min_mean_payout=10 ** 12)
    assert got is not None                      # 本命は必ずゲートに落ちる設定
    # 通常のゲートなら本命がそのまま通る（代替に降りない）
    _, stakes, used = build_with_gate_fallback(shape, PLANS["F_hit"], po, pr,
                                               n_entries=7)
    assert used is PLANS["F_hit"]


def test_the_insert_is_seven_car_only():
    """🔴 9車では `GATE_FALLBACK` を見ないので差込も掛からない（自動で7車限定）。"""
    shape = _shape()
    cheap, rich = PERMS[:2], PERMS[2:]
    po = {c: 8.0 for c in cheap} | {c: 40.0 for c in rich}
    w = {c: 100.0 for c in cheap} | {c: 1.0 for c in rich}
    tot = sum(w.values())
    pr = {c: v / tot for c, v in w.items()}
    _, _, used9 = build_with_gate_fallback(shape, PLANS["F_hit"], po, pr, 9)
    assert used9 is PLANS["F_hit"]
    assert used9.underband_min == 0.0


# ───────────────────── 文面 ─────────────────────

def test_the_copy_does_not_claim_to_avoid_cheap_points():
    """🔴 `F_hit` の文面は「安い目を買わない」と言っていないので、下限を入れても
    直す必要は無い。逆に**差込の一文を付けてはいけない**——本命の下限が 5倍なので
    5〜15倍の目は差込でなく通常の買い目であり、オッズからは見分けられない。"""
    from src.type_lab_submission import PLAN_BODIES, UNDERBAND_BANDS, underband_note
    assert "F_hit" not in UNDERBAND_BANDS
    body = PLAN_BODIES["F_hit"]
    for word in ("素直な決着は買わない", "安い", "人気"):
        assert word not in body
    legs = [{"combo": "1-2-3", "stake": 3000, "pred_odds": 6.0}]
    assert underband_note("F_hit", legs) == ""
