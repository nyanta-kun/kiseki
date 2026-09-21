#!/usr/bin/env python3
"""多点へ広げる前に「並び違い」を先に買うか（2026-09-21・ユーザー提案）。

> 三連単で多点買う際に1点目の並び違いで確定し、その目を買っていないことが多い。
> 多点広げる前に、予想家として絶対に逆転がないというケースを除いて、
> 1点目の並び違いは買った方が良いのでは？
> （実例: 09-21 広島2R E_hit 決着 4-3-1 / 大宮2R A_ana 決着 7-2-1）

## 既に測ってあること（再検証しない）

- `apply_order_swap`（同じ3車の別の並びへ**入れ替える**・点数据え置き）は本番にある。
  対象は `B_hit` / `F_hit` のみで、`E_hit` は帯30倍が順序の選択肢を先に潰すため外した。
- 「1集合1点に制限」＝集合を最大限広げる側は **−2.85/−4.84pt で否定**。
  「1集合あたり上限2〜3点」は窓で符号反転。

## ここで新しく測ること

**集合あたりの下限（深さ優先）**と、**集合内でどの並びを買うかの基準**。
実例2件はどちらも決着がモデル確率で 7位 / 36位（最下位側）なのに、
予測オッズでは集合内最人気だった（モデル確率の人気-穴バイアスと整合）。

    ⓪ base        現行（順列を確率降順に k 点）
    ① d2          集合順に各集合2順列（集合内は確率順）
    ② d3          同 3順列
    ③ top1_full   最上位集合を6順列すべて＋残り枠は現行の確率降順
    ④ swap_odds   現行の集合構成のまま、集合内の並びを**予測オッズ昇順**で選び直す
    ⑤ d2_odds     ①＋集合内を予測オッズ昇順
    ⑥ ctl_d2      ①と同じ形で2順列目を無作為（対照・複数 seed）

点数・件数・投資は据え置き。本番の τ適応・帯・入稿ゲート・`apply_line_swap` /
`apply_order_swap` / `apply_osae` を全部通す（`build_with_gate_fallback` 経由）。

    PYTHONPATH=. .venv/bin/python scripts/exp_type_lab/order_depth.py [--limit N] [--out pkl]
"""
from __future__ import annotations

import argparse
import importlib.util
import itertools
import os
import pickle
import random
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
os.chdir(REPO)

import src.type_lab as TL                                          # noqa: E402
from src.strategy_wt import (rank_7t3_blend_probs,                 # noqa: E402
                             rank_7t3_order_swap_probs)

sys.path.insert(0, str(REPO / "scripts" / "exp_type_lab"))
import common as C                                                 # noqa: E402

# 🔴🔴 **本番の `apply_add_perm` を必ず切る**（2026-09-21・実際に踏んだ）。
#    このスクリプトが提案した操作が採用されて `ADD_PERM_PLANS` が埋まると、
#    **`base` 腕にもそれが掛かる**ので「1点足した状態からさらに足す/削る」を
#    測ることになり、腕の効果が半分近く小さく出る（実測: base の点数
#    8.59 → 8.78・⑨ の Δ が +0.66 → +0.37）。腕は下の `_add_perm` /
#    `_add_n` / `_swap_tail` が自前で作るので、本番側は常に無効化する。
TL.ADD_PERM_PLANS = frozenset()

_s = importlib.util.spec_from_file_location(
    "gate", REPO.parent / "backend/src/services/keirin_type_lab_gate.py")
_G = importlib.util.module_from_spec(_s)
_s.loader.exec_module(_G)                                          # type: ignore[union-attr]

PERMS, C3 = C.CANON, C.CANON3
MIN_MEAN_PAYOUT, MIN_POINT_ODDS = 20_000.0, 2.0

_z = np.load("/tmp/race_type_board.npz", allow_pickle=True)
Z = {k: _z[k] for k in _z.files}
_z.close()

# 対象の構造。三連複（順序が無い）と看板・段は触らない。
TARGET_STRUCT = ("prob_top", "bust_top")

ARM = "base"
SEED = 0
x_trio = None
_orig_build_legs = TL.build_legs


# ── 候補と集合 ────────────────────────────────────────────────────────────
def _cands(shape, plan, pred_odds, probs):
    """本番 `prob_top` / `bust_top` と同じ候補集合を確率降順で返す。"""
    lo = float(plan.min_odds or 0.0)
    pool = set(shape.order[1:]) if plan.structure == "bust_top" else None
    cand = [tuple(k) for k, v in pred_odds.items()
            if TL._pos(v) and len(set(k)) == 3 and float(v) >= lo
            and (pool is None or set(k) <= pool)]
    cand.sort(key=lambda k: -float(probs.get(k, 0.0)))
    return cand


def _groups(cand):
    """3車集合 → その集合の候補順列（確率降順）。集合の並びは最確順列の順。"""
    g: dict = {}
    order: list = []
    for k in cand:
        s = frozenset(k)
        if s not in g:
            g[s] = []
            order.append(s)
        g[s].append(k)
    return g, order


def _pack(order_sets, groups, plan, pred_odds, pick):
    """集合順に `pick` が返す順列を積む。`sigma_max` / `max_legs` は本番と同じ扱い。"""
    kmax = plan.max_legs or 10 ** 6
    out: list = []
    s = 0.0
    for S in order_sets:
        for k in pick(S, groups[S]):
            if k in out:
                continue
            o = float(pred_odds[k])
            if plan.sigma_max and s + 1.0 / o > plan.sigma_max:
                continue          # 本番 prob_top と同じ（break ではない）
            out.append(k)
            s += 1.0 / o
            if len(out) >= kmax:
                break
        if len(out) >= kmax:
            break
    return out


def _finish(out, plan, pred_odds):
    """本番 `build_legs` の末尾と同じ後処理。"""
    if not out:
        return None
    if plan.structure == "prob_top":
        if plan.sigma_max and len(out) < 2:
            return None
        out = TL._insert_underband(out, plan, pred_odds)
    elif plan.structure == "bust_top" and len(out) < 2:
        return None
    out = [tuple(c) for c in out
           if len(set(c)) == 3 and TL._pos(pred_odds.get(tuple(c)))]
    return out or None


def _arm_legs(shape, plan, pred_odds, probs):
    cand = _cands(shape, plan, pred_odds, probs)
    if not cand:
        return None
    groups, order_sets = _groups(cand)
    odds_key = lambda k: float(pred_odds[k])          # noqa: E731

    if ARM in ("d2", "d3"):
        d = 2 if ARM == "d2" else 3
        out = _pack(order_sets, groups, plan, pred_odds,
                    lambda S, ps, d=d: ps[:d])
    elif ARM == "d2_odds":
        out = _pack(order_sets, groups, plan, pred_odds,
                    lambda S, ps: sorted(ps, key=odds_key)[:2])
    elif ARM == "top1_full":
        # 最上位集合を全部（帯を満たす順列すべて）→ 残り枠は現行の確率降順
        first = order_sets[0]
        out = _pack([first], groups, plan, pred_odds, lambda S, ps: ps)
        kmax = plan.max_legs or 10 ** 6
        s = sum(1.0 / float(pred_odds[k]) for k in out)
        for k in cand:
            if len(out) >= kmax:
                break
            if k in out:
                continue
            o = float(pred_odds[k])
            if plan.sigma_max and s + 1.0 / o > plan.sigma_max:
                continue
            out.append(k)
            s += 1.0 / o
    elif ARM == "swap_odds":
        # 現行の集合構成（各集合から何点買うか）を保ち、集合内の並びを市場基準で選び直す
        base = _orig_build_legs(shape, plan, pred_odds, probs)
        if not base:
            return None
        cnt: dict = {}
        for k in base:
            cnt[frozenset(k)] = cnt.get(frozenset(k), 0) + 1
        out = []
        for S, m in cnt.items():
            ps = groups.get(S)
            if not ps:                       # 候補外（underband 由来など）はそのまま
                out.extend([tuple(k) for k in base if frozenset(k) == S])
                continue
            out.extend(sorted(ps, key=odds_key)[:m])
        return _finish(out, plan, pred_odds) if out else None
    elif ARM == "ctl_d2":
        rng = random.Random(SEED * 1_000_003 + 17)

        def pick(S, ps):
            if len(ps) <= 1:
                return ps[:1]
            return [ps[0], rng.choice(ps[1:])]
        out = _pack(order_sets, groups, plan, pred_odds, pick)
    else:
        return None
    return _finish(out, plan, pred_odds)


def _wrapped(shape, plan, pred_odds, probs):
    if (ARM == "base" or plan.bet_type != "trifecta"
            or plan.structure not in TARGET_STRUCT):
        return _orig_build_legs(shape, plan, pred_odds, probs)
    got = _arm_legs(shape, plan, pred_odds, probs)
    # 組めなければ現行へ落とす（在庫を減らさない）
    return got if got else _orig_build_legs(shape, plan, pred_odds, probs)


TL.build_legs = _wrapped


# ── 台 ────────────────────────────────────────────────────────────────────
class Ctx:
    __slots__ = ("shape", "po_tf", "pr_tf", "or_tf", "po_t3", "pr_t3",
                 "win_tf", "pay_tf", "win_t3", "odds_t3", "date", "rtype")


def ctx(i: int) -> Ctx | None:
    cars = list(range(1, 8))
    p3 = {c: float(Z["P3"][i][c - 1]) for c in cars}
    pw = {c: float(Z["PW"][i][c - 1]) for c in cars}
    lg = {c: str(Z["LG"][i][c - 1]) for c in cars}
    lp = {}
    for c in cars:
        v = Z["A_line_pos"][i][c - 1]
        lp[c] = int(v) if np.isfinite(v) else None
    pr = rank_7t3_blend_probs(cars, pw, p3, line_group=lg, line_pos=lp)
    orp = rank_7t3_order_swap_probs(cars, pw, p3, line_group=lg, line_pos=lp)
    po = {PERMS[t]: float(Z["PO"][i][t]) for t in range(210)
          if np.isfinite(Z["PO"][i][t]) and Z["PO"][i][t] > 0}
    if len(po) < 60:
        return None
    order = tuple(sorted(cars, key=lambda c: (-p3[c], c)))
    lines = TL._lines_of(lg, lp)
    x = Ctx()
    x.shape = TL.RaceShape(
        str(Z["TYPE"][i]), float(Z["AXIS_SUM"][i]), int(Z["ARARE"][i]),
        float(Z["GAP"][i]), float(Z["AXIS_SUM"][i]) >= TL.AXIS_SUM_FIRM,
        order, TL.win_entropy(pw), lines, TL._strongest_pair(lines, p3))
    x.po_tf, x.pr_tf, x.or_tf = po, pr, orp
    x.po_t3 = {frozenset(c): float(Z["TRIO_PO"][i][j]) for j, c in enumerate(C3)
               if np.isfinite(Z["TRIO_PO"][i][j]) and Z["TRIO_PO"][i][j] > 0}
    x.pr_t3 = {frozenset(c): sum(pr.get(p, 0.0) for p in itertools.permutations(c))
               for c in C3}
    x.win_tf = PERMS[int(Z["WIN"][i])]
    x.pay_tf = float(Z["PAY"][i]) / 100.0
    x.win_t3 = frozenset(C3[int(Z["TRIO_WIN"][i])])
    x.odds_t3 = float(Z["TRIO_PAY"][i])
    x.date = str(Z["DATE"][i])
    x.rtype = str(Z["RTYPE"][i])
    return x


def build(x: Ctx, plan) -> dict | None:
    global x_trio
    trio = plan.bet_type == "trio"
    x_trio = x.pr_t3
    pod, prb = (x.po_t3, x.pr_t3) if trio else (x.po_tf, x.pr_tf)
    got = TL.build_with_gate_fallback(x.shape, plan, pod, prb, 7,
                                      order_probs=None if trio else x.or_tf)
    if not got:
        return None
    legs, st, pl = got
    if not trio and pl.structure in TARGET_STRUCT:
        if ARM in ("add_band", "add_free"):
            legs, st = _add_perm(legs, st, pl, pod, prb, free=(ARM == "add_free"))
        elif ARM == "add_odds":
            legs, st = _add_perm(legs, st, pl, pod, prb, False, by_odds=True)
        elif ARM == "add_odds3":
            legs, st = _add_perm(legs, st, pl, pod, prb, False, by_odds=True, n_sets=3)
        elif ARM in ("add_set3", "add_set2"):
            legs, st = _add_perm(legs, st, pl, pod, prb, False, by_odds=True,
                                 n_sets=3 if ARM == "add_set3" else 2)
        elif ARM in ("ctl_set3", "ctl_set2"):
            legs, st = _add_perm(legs, st, pl, pod, prb, False, by_odds=True,
                                 n_sets=3 if ARM == "ctl_set3" else 2,
                                 pick_sets="random", seed=max(SEED, 1))
        elif ARM == "add_trio3":
            legs, st = _add_perm(legs, st, pl, pod, prb, False, by_odds=True,
                                 n_sets=3, pick_sets="trio", trio_prob=x_trio)
        elif ARM == "add_odds2":
            legs, st = _add_n(legs, st, pl, pod, prb, 2)
        elif ARM == "add_full":
            legs, st = _add_n(legs, st, pl, pod, prb, -1)
        elif ARM == "swap_tail1":
            legs, st = _swap_tail(legs, st, pl, pod, prb, 1)
        elif ARM == "swap_tail2":
            legs, st = _swap_tail(legs, st, pl, pod, prb, 2)
        elif ARM == "ctl_tail1":
            legs, st = _swap_tail(legs, st, pl, pod, prb, 1, seed=max(SEED, 1))
        elif ARM in ("add_rand", "add_rand_odds", "add_rand_odds3",
                     "add_rand_odds2"):
            legs, st = _add_rand(legs, st, pl, pod, prb, SEED,
                                 cheapest=ARM.startswith("add_rand_odds"),
                                 n_sets={"add_rand_odds3": 3,
                                         "add_rand_odds2": 2}.get(ARM, 1))
    mean = float(TL.mean_expected_payout(st, pod))
    gate = mean > MIN_MEAN_PAYOUT and min(float(pod[c]) for c in st) >= MIN_POINT_ODDS
    if trio:
        pay = float(st[x.win_t3] * x.odds_t3) if x.win_t3 in st else 0.0
        sethit = exact = x.win_t3 in st
        nsets = len(st)
        diag = dict(first_hit=False, n_first=0,
                    sigma=float(sum(1.0 / float(pod[c]) for c in st)),
                    win_stake=float(st.get(x.win_t3, 0.0)))
    else:
        pay = float(st[x.win_tf]) * x.pay_tf if x.win_tf in st else 0.0
        sets = {frozenset(c) for c in st}
        sethit = frozenset(x.win_tf) in sets
        exact = x.win_tf in st
        nsets = len(sets)
        # 🔴 診断: **先頭の目の集合**が決着したか、その集合を何点買っているか。
        #    「1点足すだけで先頭集合の並び違いを拾えるか」に答えるための量。
        first = frozenset(tuple(legs[0]))
        diag = dict(first_hit=bool(frozenset(x.win_tf) == first),
                    n_first=sum(1 for c in st if frozenset(c) == first),
                    sigma=float(sum(1.0 / float(pod[c]) for c in st)),
                    win_stake=float(st.get(x.win_tf, 0.0)))
    return dict(key=pl.key, k=len(st), inv=float(sum(st.values())), pay=pay,
                mean=mean, gate=bool(gate), sethit=bool(sethit), exact=bool(exact),
                nsets=nsets, struct=pl.structure, **diag)


def _add_rand(legs, st, plan, pred_odds, probs, seed: int, cheapest: bool = False,
              n_sets: int = 1):
    """**無作為対照**: 1点目の集合の並び違いではなく、帯を満たす未購入の目を無作為に1点。

    🔴 これが無いと「点を1つ足しただけ」の効果を「並び違いを選んだ効果」と誤読する
       （`apply_osae` の実測では効果の 1/3〜1/2 が足しただけだった）。
    """
    legs = [tuple(c) for c in legs]
    if not legs:
        return legs, st
    have = set(legs)
    lo = float(plan.min_odds or 0.0)
    hi = float(plan.max_odds or 0.0)
    pool = set(plan.structure == "bust_top" and () or ())
    cand = [tuple(k) for k, v in pred_odds.items()
            if tuple(k) not in have and TL._pos(v) and len(set(k)) == 3
            and float(v) >= max(lo, MIN_POINT_ODDS)
            and (not hi or float(v) <= hi)]
    if not cand:
        return legs, st
    cand.sort()
    rng = random.Random(seed * 7_919 + hash(legs[0]) % 100_003)
    if cheapest:
        # 無作為に集合を選び、その集合の未購入順列のうち最安を足す
        #（＝「安い目を n 点足した」効果と「買い目上位の集合を選んだ」効果を分ける）
        sets = sorted({frozenset(k) for k in cand})
        rng.shuffle(sets)
        picks = []
        for S in sets[:n_sets]:
            sub = [k for k in cand if frozenset(k) == S]
            if sub:
                picks.append(min(sub, key=lambda k: float(pred_odds[k])))
        if not picks:
            return legs, st
        new = legs + picks
    else:
        new = legs + [rng.choice(cand)]
    nst = TL.allocate(new, pred_odds, probs, plan)
    if not nst or len(nst) != len(new):
        return legs, st
    if TL.mean_expected_payout(nst, pred_odds) <= MIN_MEAN_PAYOUT:
        return legs, st
    if min(float(pred_odds[c]) for c in nst) < MIN_POINT_ODDS:
        return legs, st
    return new, nst


def _add_perm(legs, st, plan, pred_odds, probs, free: bool,
              by_odds: bool = False, n_sets: int = 1,
              pick_sets: str = "top", seed: int = 0, trio_prob=None):
    """**買い目上位 `n_sets` 個の集合**について、未購入順列を1点ずつ足す。

    by_odds=False … 足す1点は**確率最上位**（モデル基準）
    by_odds=True  … 足す1点は**予測オッズ最安**（市場基準・2026-09-21 大宮2R の型）
    

    🔴 点数は増えるが**投資は1万円固定**なので、既存の当たり目の配分は薄まる。
    🔴 入稿ゲート（平均想定払戻 2万円・全点2.0倍）を割るなら足さない
       （件数を1件も減らさないため。`apply_osae` と同じ作法）。
    free=True は帯（`min_odds`）を無視する＝商品の価格帯に触る腕。
    """
    legs = [tuple(c) for c in legs]
    if not legs:
        return legs, st
    have = set(legs)
    lo = 0.0 if free else float(plan.min_odds or 0.0)
    hi = float(plan.max_odds or 0.0)
    # 買い目に出てくる順に集合を拾う（先頭＝最確の目の集合）
    seen: list = []
    for c in legs:
        S = frozenset(c)
        if S not in seen:
            seen.append(S)
    # 🔴 **どの集合へ足すか**。`top` が実装（買い目上位）。`random` は**公平な対照**
    #    （買っている集合から無作為に選ぶ＝集合を増やさないので点数も同じ）。
    #    `trio` は三連複確率の上位（集合の選び方そのものを替える腕）。
    if pick_sets == "random":
        seen = list(seen)
        random.Random(seed * 6_151 + hash(tuple(sorted(legs[0]))) % 99_991).shuffle(seen)
    elif pick_sets == "trio" and trio_prob is not None:
        seen.sort(key=lambda S: -float(trio_prob.get(S, 0.0)))
    add: list = []
    for S in seen[:n_sets]:
        cand = []
        for k in itertools.permutations(sorted(S)):
            if k in have or k in add or not TL._pos(pred_odds.get(k)):
                continue
            o = float(pred_odds[k])
            if o < max(lo, MIN_POINT_ODDS) or (hi and not free and o > hi):
                continue
            cand.append(k)
        if not cand:
            continue
        if by_odds:
            cand.sort(key=lambda k: float(pred_odds[k]))
        else:
            cand.sort(key=lambda k: -float(probs.get(k, 0.0)))
        add.append(cand[0])
    if not add:
        return legs, st
    new = legs + add
    nst = TL.allocate(new, pred_odds, probs, plan)
    if not nst or len(nst) != len(new):
        return legs, st
    if TL.mean_expected_payout(nst, pred_odds) <= MIN_MEAN_PAYOUT:
        return legs, st
    if min(float(pred_odds[c]) for c in nst) < MIN_POINT_ODDS:
        return legs, st
    return new, nst


def _first_set_cands(legs, plan, pred_odds, free: bool = False):
    """先頭の目と同じ3車で、まだ買っていない並び（帯の中）を**予測オッズ昇順**で返す。"""
    have = {tuple(c) for c in legs}
    lo = 0.0 if free else float(plan.min_odds or 0.0)
    hi = float(plan.max_odds or 0.0)
    out = []
    for k in itertools.permutations(sorted(frozenset(tuple(legs[0])))):
        if k in have or not TL._pos(pred_odds.get(k)):
            continue
        o = float(pred_odds[k])
        if o < max(lo, MIN_POINT_ODDS) or (hi and o > hi):
            continue
        out.append(k)
    out.sort(key=lambda k: float(pred_odds[k]))
    return out


def _add_n(legs, st, plan, pred_odds, probs, n: int):
    """先頭集合の未購入の並びを**安い順に n 点足す**（n<0 なら全部）。"""
    legs = [tuple(c) for c in legs]
    if not legs:
        return legs, st
    cand = _first_set_cands(legs, plan, pred_odds)
    if not cand:
        return legs, st
    new = legs + (cand if n < 0 else cand[:n])
    nst = TL.allocate(new, pred_odds, probs, plan)
    if not nst or len(nst) != len(new):
        return legs, st
    if TL.mean_expected_payout(nst, pred_odds) <= MIN_MEAN_PAYOUT:
        return legs, st
    if min(float(pred_odds[c]) for c in nst) < MIN_POINT_ODDS:
        return legs, st
    return new, nst


def _swap_tail(legs, st, plan, pred_odds, probs, m: int, seed: int = 0):
    """**確率下位の末尾 m 点を落として**、先頭集合の未購入の並びを安い順に m 点入れる。

    ユーザー指摘（2026-09-21）:
    > 安いオッズの買い目を足すと掛け金が下がると思います。
    > 確率順位の下位を減らし、金額の再配分が必要ではないですか？

    点数据え置きなので `Σ(1/予測オッズ)` の増加は「足す」形より小さい
    （落とす末尾は確率最下位＝予測オッズが高く 1/オッズ が小さいので、
    差し引きでは増える。ゼロにはならない）。
    seed>0 は**無作為対照**（入れる m 点を帯の中の無作為な未購入目にする）。
    """
    legs = [tuple(c) for c in legs]
    if len(legs) - m < 3:                     # 残りが3点未満になるなら触らない
        return legs, st
    keep = legs[:-m]
    if seed:
        have = set(keep)
        lo = float(plan.min_odds or 0.0)
        hi = float(plan.max_odds or 0.0)
        pool = [tuple(k) for k, v in pred_odds.items()
                if tuple(k) not in have and TL._pos(v) and len(set(k)) == 3
                and float(v) >= max(lo, MIN_POINT_ODDS)
                and (not hi or float(v) <= hi)]
        if len(pool) < m:
            return legs, st
        pool.sort()
        add = random.Random(seed * 3_581 + hash(legs[0]) % 99_991).sample(pool, m)
    else:
        cand = _first_set_cands(keep, plan, pred_odds)
        if len(cand) < m:
            return legs, st
        add = cand[:m]
    new = keep + add
    nst = TL.allocate(new, pred_odds, probs, plan)
    if not nst or len(nst) != len(new):
        return legs, st
    if TL.mean_expected_payout(nst, pred_odds) <= MIN_MEAN_PAYOUT:
        return legs, st
    if min(float(pred_odds[c]) for c in nst) < MIN_POINT_ODDS:
        return legs, st
    return new, nst


ARMS = ["base", "d2", "d3", "top1_full", "swap_odds", "d2_odds", "ctl_d2",
        "add_band", "add_free", "add_rand",
        "add_odds", "add_odds3", "add_rand_odds", "add_rand_odds3",
        "add_odds2", "add_full", "swap_tail1", "swap_tail2", "ctl_tail1",
        "add_rand_odds2",
        # 🔴 上位3集合に各1点（両提示レースを拾える形）と**公平な対照**
        "add_set3", "ctl_set3", "add_trio3", "add_set2", "ctl_set2"]


def main() -> None:
    global ARM, SEED
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default="/tmp/order_depth.pkl")
    ap.add_argument("--ctl-seeds", type=int, default=1)
    ap.add_argument("--arms", default=None, help="カンマ区切りで腕を絞る")
    a = ap.parse_args()

    use = [k for k in ARMS if not a.arms or k in a.arms.split(",")]
    rows: dict[str, list] = {k: [] for k in use}
    for s in range(1, a.ctl_seeds):
        for base_arm in ("ctl_d2", "add_rand", "add_rand_odds", "add_rand_odds3",
                         "ctl_tail1", "add_rand_odds2", "ctl_set3", "ctl_set2"):
            if base_arm in use:
                rows[f"{base_arm}#{s}"] = []
    ndays: dict[str, int] = {}

    for win in ("explore", "confirm"):
        idx = [int(i) for i in C.select(None, win) if str(Z["TYPE"][int(i)]) in "ABCDEF"]
        if a.limit:
            idx = idx[:a.limit]
        ndays[win] = len({str(Z["DATE"][i]) for i in idx})
        for n, i in enumerate(idx):
            if n % 2000 == 0:
                print(f"  {win} {n:,}/{len(idx):,}", flush=True)
            x = ctx(i)
            if x is None:
                continue
            tl = x.shape.type_label
            trio_ok = None
            if tl == "A":
                ARM = "base"
                r = build(x, TL.PLANS["A_trio"])
                trio_ok = bool(r and r["gate"])
            sel = TL.sell_plans_for(tl, 7, x.rtype, pw_ent=x.shape.pw_ent,
                                    trio_ok=trio_ok)
            if not sel:
                continue
            plan = sel[0]
            axis_ok = bool(_G.passes_axis_gate(plan.key, x.shape.axis_sum, 7))
            base_meta = dict(i=i, win=win, date=x.date, type=tl, rtype=x.rtype,
                             axis=float(x.shape.axis_sum), axis_ok=axis_ok)
            for arm in rows:
                ARM = arm.split("#")[0]
                SEED = int(arm.split("#")[1]) if "#" in arm else 0
                r = build(x, plan)
                if r is None:
                    continue
                rows[arm].append(dict(**base_meta, **r))
    ARM = "base"
    for k, v in rows.items():
        print(f"  {k}: {len(v):,} 行")
    pickle.dump(dict(rows=rows, ndays=ndays), Path(a.out).open("wb"))
    print(f"→ {a.out}")


if __name__ == "__main__":
    main()
