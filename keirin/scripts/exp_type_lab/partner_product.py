#!/usr/bin/env python3
"""相手（3着候補）の選び方を替えて、ラインナップ全体の KPI を測る（2026-09-10）。

## 先に本番を読んだ結果

- 「相手」が**明示的に存在するのは三連複2プランだけ**:
    `A_trio` = `axis2_flow`     … 相手は `shape.order[2:]`（p3 降順）の先頭 n 車。**確率は見ない**
    `D_hit`  = `axis2_drop_fav` … 相手5点から最人気1点を落とし、**三連複確率**上位3点
  三連単の主力は `prob_top`（軸の概念が無く 210点を確率降順に積む）。
  `F_pay`/`A_pay` は `axis1_second2`（3着 pool は p3 降順）。
- 既存の相手・並び操作: `apply_line_swap` / `_insert_underband` / `axis2_drop_fav`。
- ゲートは3段（軸信頼 → 組む → 入稿ゲート）。**本稿は全部通してから比べる。**

🔴 **本番関数で組む。** 選抜用の確率 `prb_sel` と配分用の確率 `prb` を分けるためだけに
   `build_with_gate_fallback` の制御フローを写した `_build`（下）を使い、
   **`prb_sel is prb` のとき本番と1レースも食い違わないことを毎回 assert する**。
"""
from __future__ import annotations

import itertools
import random
import sys
from collections import defaultdict
from dataclasses import replace
from pathlib import Path
from statistics import median

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts" / "exp_type_lab"))

import common as C  # noqa: E402
from src.type_lab import (  # noqa: E402
    GATE_FALLBACK, MIN_MEAN_PAYOUT, PLANS, Plan, allocate, alloc_fallback,
    apply_line_swap, build_legs, build_with_gate_fallback, mean_expected_payout,
    race_shape, sell_plans_for)
from src.strategy_wt import rank_7t3_blend_probs  # noqa: E402

import importlib.util  # noqa: E402
_s = importlib.util.spec_from_file_location(
    "gate", REPO.parent / "backend/src/services/keirin_type_lab_gate.py")
_G = importlib.util.module_from_spec(_s)
_s.loader.exec_module(_G)  # type: ignore[union-attr]
passes_axis_gate = _G.passes_axis_gate

PERMS, C3 = C.CANON, C.CANON3
C3IDX = C.C3IDX
MIN_POINT_ODDS = 2.0
CARS = list(range(1, 8))


# ───────────────────────────── 台 ─────────────────────────────

class X:
    __slots__ = ("shape", "po_tf", "pr_tf", "po_t3", "pr_t3", "win_tf", "pay_tf",
                 "win_t3", "odds_t3", "date", "rtype", "axis", "i")


_A = None


def ctx(i: int):
    global _A
    if _A is None:
        z = C.board()
        _A = {k: z[k] for k in ("P3", "PW", "LG", "A_line_pos", "ST", "A_race_point",
                                "BEHIND", "DAYI", "PO", "WIN", "PAY", "TRIO_PO",
                                "TRIO_ODDS", "TRIO_WIN", "TRIO_PAY", "DATE", "RTYPE",
                                "TYPE", "AXIS_SUM")}
    a = _A
    p3 = {c: float(a["P3"][i][c - 1]) for c in CARS}
    pw = {c: float(a["PW"][i][c - 1]) for c in CARS}
    lg = {c: a["LG"][i][c - 1] for c in CARS}
    # 🔴🔴 **`line_pos` は int で渡すこと。** 板は float32 なので、そのまま渡すと
    #    `race_shape` の `str(line_pos.get(c)) == "1"` が `"1.0"` と比較されて
    #    **先頭・番手が永久に None**（＝`arare` が BEHIND/脚質の項を落として型がずれる）、
    #    `_lines_of` の `int(str(v))` も ValueError で全員 99 へ落ち、
    #    **ラインが「隊列順」ではなく車番順**になる（`line_legs` の先頭×番手が壊れる）。
    #    ⚠️ `rank_7t3_blend_probs` の `_line_next` は `int(pb)` なので float でも動く。
    #       つまり**確率は正しいのに型とラインだけが静かにずれる**——エラーは出ない。
    lp = {c: (int(a["A_line_pos"][i][c - 1])
              if np.isfinite(a["A_line_pos"][i][c - 1]) else 0) for c in CARS}
    sh = race_shape(p3, lg, lp, {c: str(a["ST"][i][c - 1]) for c in CARS},
                    {c: float(a["A_race_point"][i][c - 1]) for c in CARS},
                    {c: float(a["BEHIND"][i][c - 1]) for c in CARS},
                    int(a["DAYI"][i]), pw)
    if sh is None or sh.type_label != str(a["TYPE"][i]):
        return None
    po = {PERMS[t]: float(a["PO"][i][t]) for t in range(210)
          if np.isfinite(a["PO"][i][t]) and a["PO"][i][t] > 0}
    if len(po) < 60:
        return None
    pr = rank_7t3_blend_probs(CARS, pw, p3, line_group=lg, line_pos=lp)
    x = X()
    x.i = i
    x.shape = sh
    x.po_tf, x.pr_tf = po, pr
    x.po_t3 = {frozenset(c): float(a["TRIO_PO"][i][j]) for j, c in enumerate(C3)
               if np.isfinite(a["TRIO_PO"][i][j]) and a["TRIO_PO"][i][j] > 0}
    x.pr_t3 = {frozenset(c): sum(pr.get(q, 0.0) for q in itertools.permutations(c))
               for c in C3}
    x.win_tf = PERMS[int(a["WIN"][i])]
    x.pay_tf = float(a["PAY"][i]) / 100.0
    x.win_t3 = frozenset(C3[int(a["TRIO_WIN"][i])])
    x.odds_t3 = float(a["TRIO_PAY"][i])
    x.date = str(a["DATE"][i])
    x.rtype = str(a["RTYPE"][i])
    x.axis = float(a["AXIS_SUM"][i])
    return x


def plan_key_of(x) -> str:
    trio_ok = _trio_ok(x)
    pl = sell_plans_for(x.shape.type_label, 7, x.rtype,
                        pw_ent=x.shape.pw_ent, trio_ok=trio_ok)
    return pl[0].key if pl else ""


def _trio_ok(x) -> bool:
    got = build_with_gate_fallback(x.shape, PLANS["A_trio"], x.po_t3, x.pr_t3, 7)
    if not got:
        return False
    legs, st, _ = got
    return (mean_expected_payout(st, x.po_t3) > MIN_MEAN_PAYOUT
            and min(float(x.po_t3[c]) for c in st) >= MIN_POINT_ODDS)


# ───────── 本番 `build_with_gate_fallback` の写し（選抜用確率を分けるだけ）─────────

def _bp(shape, plan, pod, prb, prb_sel):
    legs = build_legs(shape, plan, pod, prb_sel)
    if not legs:
        return None
    st = allocate(legs, pod, prb, plan)
    if not st:
        fb = alloc_fallback(plan)
        if fb is not None:
            st = allocate(legs, pod, prb, fb)
    if not st:
        return None
    return [c for c in legs if c in st], st


def _build(shape, plan, pod, prb, prb_sel=None):
    prb_sel = prb if prb_sel is None else prb_sel

    def _done(got, pl):
        legs, st = apply_line_swap(shape, pl, got[0], got[1], pod, prb, MIN_MEAN_PAYOUT)
        return legs, st, pl

    got = _bp(shape, plan, pod, prb, prb_sel)
    fbs = GATE_FALLBACK.get(plan.key) or ()
    if not fbs:
        return _done(got, plan) if got else None
    if got and mean_expected_payout(got[1], pod) > MIN_MEAN_PAYOUT:
        return _done(got, plan)
    for fb in fbs:
        alt = _bp(shape, fb, pod, prb, prb_sel)
        if alt and mean_expected_payout(alt[1], pod) > MIN_MEAN_PAYOUT:
            return _done(alt, fb)
    return _done(got, plan) if got else None


def _score(x, key, got):
    legs, st, pl = got
    trio = pl.bet_type == "trio"
    pod = x.po_t3 if trio else x.po_tf
    if mean_expected_payout(st, pod) <= MIN_MEAN_PAYOUT:
        return None
    if min(float(pod[c]) for c in st) < MIN_POINT_ODDS:
        return None
    if trio:
        pay = float(st[x.win_t3] * x.odds_t3) if x.win_t3 in st else 0.0
    else:
        pay = float(st[x.win_tf] / 100.0 * x.pay_tf * 100.0) if x.win_tf in st else 0.0
    return dict(date=x.date, inv=float(sum(st.values())), pay=pay, k=len(st),
                mean=mean_expected_payout(st, pod), plan=key, i=x.i)


# ───────────────────────────── 腕 ─────────────────────────────

def _mkt_t3(x) -> dict:
    """三連複の「市場確率」＝ 1/予測オッズ（正規化しない・順位だけ使う）。"""
    return {k: 1.0 / v for k, v in x.po_t3.items()}


def _line_t3(x) -> dict:
    """軸2車と同ラインの相手を上へ。同値は三連複確率で割る。"""
    a1, a2 = x.shape.order[0], x.shape.order[1]
    lg = {c: g for line in x.shape.lines for i, c in enumerate(line)
          for g in (id(line),)}
    out = {}
    for k, v in x.pr_t3.items():
        third = [c for c in k if c not in (a1, a2)]
        bonus = 0.0
        if len(third) == 1:
            c = third[0]
            same = lg.get(c) is not None and (lg.get(c) == lg.get(a1)
                                              or lg.get(c) == lg.get(a2))
            bonus = 1.0 if same else 0.0
        out[k] = bonus + v * 1e-3
    return out


def _reorder_partners(x, key: dict):
    """`shape.order` の3位以下を `key` の降順に並べ替えた shape。"""
    a1, a2 = x.shape.order[0], x.shape.order[1]
    rest = [c for c in x.shape.order[2:]]
    rest.sort(key=lambda c: -key.get(frozenset({a1, a2, c}), 0.0))
    return replace(x.shape, order=(a1, a2) + tuple(rest))


def _set_prob(x, w: float) -> dict:
    """三連単の選抜用確率: **集合は市場×モデルの混合順位・並びはモデル**。

    集合の重み q(set) を「モデル順位と市場順位の加重平均」で作り、
    集合内の 6 順列へはモデルの構成比で配る（順序の情報は一切変えない）。
    """
    mdl = {k: v for k, v in x.pr_t3.items() if k in x.po_t3}
    if not mdl:
        return x.pr_tf
    mkt = {k: 1.0 / x.po_t3[k] for k in mdl}
    rm = {c: j for j, c in enumerate(sorted(mdl, key=lambda z: -mdl[z]))}
    rk = {c: j for j, c in enumerate(sorted(mkt, key=lambda z: -mkt[z]))}
    q = {c: 1.0 / (1.0 + (1 - w) * rm[c] + w * rk[c]) for c in mdl}
    out = {}
    for perm, p in x.pr_tf.items():
        s = frozenset(perm)
        tot = mdl.get(s, 0.0)
        out[perm] = (q[s] * p / tot) if (s in q and tot > 0) else 0.0
    return out


def _fill_partners(x, got, m: int, key: str):
    """相手を上位 m 車まで**必ず1点持つ**ようにする（先頭へ挿入・末尾を落とす）。

    key: 'p3'（本番の相手順）/ 'mkt'（予測オッズ昇順）
    """
    legs, st, pl = got
    # 🔴 **本線プラン（`prob_top`）だけ。** `A_ana`（軸1を買わない）・`F_sign`（看板枠）へ
    #    軸2車の目を差し込むのは商品を別物へ作り替える操作で、
    #    `miss_anatomy_2026_09_10.md` §5 が「効きの大半はそれ」と分解済み。
    if pl.bet_type != "trifecta" or pl.structure != "prob_top":
        return None
    a1, a2 = x.shape.order[0], x.shape.order[1]
    cand = list(x.shape.order[2:])
    if key == "mkt":
        cand.sort(key=lambda c: x.po_t3.get(frozenset({a1, a2, c}), 9e9))
    want = cand[:m]
    have = {frozenset(c) for c in legs}
    add = []
    for c in want:
        s = frozenset({a1, a2, c})
        if s in have:
            continue
        best = max((p for p in itertools.permutations((a1, a2, c))
                    if p in x.po_tf and x.po_tf[p] >= MIN_POINT_ODDS),
                   key=lambda p: x.pr_tf.get(p, 0.0), default=None)
        if best is not None:
            add.append(best)
    if not add or len(add) >= len(legs):
        return None
    new = add + [c for c in legs if c not in set(add)][:len(legs) - len(add)]
    st2 = allocate(new, x.po_tf, x.pr_tf, pl)
    if not st2 or len(st2) != len(new):
        return None
    if mean_expected_payout(st2, x.po_tf) <= MIN_MEAN_PAYOUT:
        return None
    return [c for c in new if c in st2], st2, pl


ARMS = ("base", "trio_mkt", "trio_line", "set_w25", "set_w50", "set_mkt",
        "fill3_p3", "fill4_p3", "fill5_p3", "fill3_mkt", "fill4_mkt")


def _build_arm(x, key: str, arm: str):
    plan = PLANS[key]
    trio = plan.bet_type == "trio"
    pod = x.po_t3 if trio else x.po_tf
    prb = x.pr_t3 if trio else x.pr_tf
    shape, sel = x.shape, None
    if arm.startswith("trio_") and trio:
        k = _mkt_t3(x) if arm == "trio_mkt" else _line_t3(x)
        if plan.structure == "axis2_flow":
            shape = _reorder_partners(x, k)
        else:
            sel = k
    elif arm.startswith("set_") and not trio and plan.structure == "prob_top":
        w = {"set_w25": 0.25, "set_w50": 0.5, "set_mkt": 1.0}[arm]
        sel = _set_prob(x, w)
    got = _build(shape, plan, pod, prb, sel)
    if got is None:
        return None
    if arm.startswith("fill"):
        m = int(arm[4])
        alt = _fill_partners(x, got, m, arm.split("_")[1])
        if alt is not None:
            got = alt
    return got


# ───────────────────────────── 実行 ─────────────────────────────

def _boot(pairs, seed=0, n=2000):
    """レース単位の対応ブートストラップ（numpy）。pairs=[(base_rec, arm_rec)]。"""
    m = len(pairs)
    bs = np.array([1.0 if (b["pay"] >= b["inv"] and b["pay"] > 0) else 0.0
                   for b, _ in pairs])
    as_ = np.array([1.0 if (a["pay"] >= a["inv"] and a["pay"] > 0) else 0.0
                    for _, a in pairs])
    bi = np.array([b["inv"] for b, _ in pairs]); bp = np.array([b["pay"] for b, _ in pairs])
    ai = np.array([a["inv"] for _, a in pairs]); ap = np.array([a["pay"] for _, a in pairs])
    rng = np.random.default_rng(seed)
    ds, dr = [], []
    for _ in range(n):
        k = rng.integers(0, m, m)
        ds.append((as_[k].sum() - bs[k].sum()) / m * 100)
        x, y = ai[k].sum(), bi[k].sum()
        dr.append((ap[k].sum() / x - bp[k].sum() / y) * 100 if x and y else 0.0)
    ds = np.sort(np.array(ds)); dr = np.sort(np.array(dr))
    q = lambda v: (float(v[int(.025 * len(v))]), float(v[int(.975 * len(v))]))
    return q(ds), q(dr)


def _rand_control(x, key, base_got, arm_got, seed):
    """腕が差し替えた点数と同数を、**同じ候補プールから無作為に**差し替えた対照。"""
    legs, st, pl = base_got
    if pl.structure != "prob_top" or pl.bet_type != "trifecta":
        return None
    d = len(set(arm_got[0]) - set(legs))
    if d <= 0 or d >= len(legs):
        return None
    rng = random.Random(seed * 1_000_003 + x.i)
    have = set(legs)
    pool = [p for p, o in x.po_tf.items()
            if p not in have and o >= max(pl.min_odds, MIN_POINT_ODDS)]
    if len(pool) < d:
        return None
    add = rng.sample(pool, d)
    keep = list(legs)
    rng.shuffle(keep)
    new = add + keep[:len(legs) - d]
    st2 = allocate(new, x.po_tf, x.pr_tf, pl)
    if not st2 or len(st2) != len(new):
        return None
    if mean_expected_payout(st2, x.po_tf) <= MIN_MEAN_PAYOUT:
        return None
    return [c for c in new if c in st2], st2, pl


def main() -> None:
    arms = list(ARMS)
    lim = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    n_ctrl = int(sys.argv[3]) if len(sys.argv) > 3 else 0
    ctrl_of = sys.argv[4] if len(sys.argv) > 4 else "set_w50"
    for label, win in (("探索 2024-07〜2025-12", "explore"),
                       ("確認 2026-01〜08", "confirm")):
        idx = C.select(None, win)
        if lim:
            idx = idx[:lim]
        nd = C.days_of(C.select(None, win))
        recs = {a: [] for a in arms}
        ctrl = {s: [] for s in range(n_ctrl)}
        pairs = {a: [] for a in arms}
        per_plan = {a: defaultdict(list) for a in arms}
        n_chk = n_bad = 0
        for t, i in enumerate(idx):
            if t % 5000 == 0:
                print(f"  {win} {t:,}/{len(idx):,}", flush=True)
            x = ctx(int(i))
            if x is None:
                continue
            key = plan_key_of(x)
            if not key or not passes_axis_gate(key, x.axis, 7):
                continue
            pl0 = PLANS[key]
            pod0 = x.po_t3 if pl0.bet_type == "trio" else x.po_tf
            prb0 = x.pr_t3 if pl0.bet_type == "trio" else x.pr_tf
            got0 = build_with_gate_fallback(x.shape, pl0, pod0, prb0, 7)
            mine = _build(x.shape, pl0, pod0, prb0)
            n_chk += 1
            if (got0 is None) != (mine is None) or (
                    got0 is not None and got0[1] != mine[1]):
                n_bad += 1
            base_got = _build_arm(x, key, "base")
            base = _score(x, key, base_got) if base_got else None
            if base is None:
                continue
            for a in arms:
                if a == "base":
                    r, g = base, base_got
                else:
                    g = _build_arm(x, key, a)
                    r = _score(x, key, g) if g else None
                    if r is None:
                        r, g = base, base_got
                recs[a].append(r)
                pairs[a].append((base, r))
                per_plan[a][key].append(r)
                if a == ctrl_of and n_ctrl:
                    for sd in range(n_ctrl):
                        cg = _rand_control(x, key, base_got, g, sd)
                        cr = _score(x, key, cg) if cg else None
                        ctrl[sd].append(cr or base)
        print(f"\n{'=' * 118}")
        print(f"=== {label}  写しの一致 {n_chk - n_bad:,}/{n_chk:,}"
              f"（不一致 {n_bad}）===")
        print(C.HEAD)
        for a in arms:
            print(C.line(a, C.summarize(recs[a], nd)))
        print("  -- Δ vs base（レース単位 対応ブートストラップ 95%CI）--")
        for a in arms[1:]:
            b = C.summarize(recs["base"], nd)
            v = C.summarize(recs[a], nd)
            (s1, s2), (r1, r2) = _boot(pairs[a])
            print(f"  {a:12s} Δ表示的中 {v['shown'] - b['shown']:+6.2f}pt "
                  f"[{s1:+.2f},{s2:+.2f}]   ΔROI {v['roi'] - b['roi']:+6.2f}pt "
                  f"[{r1:+.2f},{r2:+.2f}]")
        if n_ctrl:
            base_sh = C.summarize(recs["base"], nd)["shown"]
            arm_sh = C.summarize(recs[ctrl_of], nd)["shown"]
            cs = sorted(C.summarize(ctrl[sd], nd)["shown"] for sd in range(n_ctrl))
            cr = sorted(C.summarize(ctrl[sd], nd)["roi"] for sd in range(n_ctrl))
            win_n = sum(1 for v in cs if arm_sh > v)
            print(f"  -- 無作為対照 {n_ctrl}本（{ctrl_of} と同数・同プールを無作為差し替え）--")
            print(f"     base {base_sh:.2f}% / 腕 {arm_sh:.2f}% / 対照 中央 "
                  f"{cs[len(cs)//2]:.2f}%（{cs[0]:.2f}〜{cs[-1]:.2f}）→ **{win_n}/{n_ctrl}**")
            print(f"     ROI 対照 中央 {cr[len(cr)//2]:.1f}%（{cr[0]:.1f}〜{cr[-1]:.1f}）"
                  f" / 腕 {C.summarize(recs[ctrl_of], nd)['roi']:.1f}%")
        print("  -- プラン別 Δ表示的中(pt) vs base --")
        keys = sorted(per_plan["base"])
        print("  {:12s}".format("") + "".join(f"{k:>9s}" for k in keys))
        for a in arms[1:]:
            row = []
            for k in keys:
                b = C.summarize(per_plan["base"][k], nd)
                v = C.summarize(per_plan[a][k], nd)
                row.append(f"{(v.get('shown', 0) - b.get('shown', 0)):9.2f}")
            print(f"  {a:12s}" + "".join(row))



def anatomy() -> None:
    """商品ごとに「相手をどこまで広げているか」と「その仕事に対する失敗」を出す。

    🔴 フラットな表示的中ではなく **設計意図に対する失敗** で見る:
       `_hit` は当てにいく商品なので相手外しは失敗、`D_hit` の最人気落としと
       `A_ana`/`_sign` の軸外しは**設計どおりの代償**。
    """
    for label, win in (("探索 2024-07〜2025-12", "explore"),
                       ("確認 2026-01〜08", "confirm")):
        idx = C.select(None, win)
        agg = defaultdict(lambda: dict(n=0, both=0, cov=0, ncov=0.0, npt=0.0,
                                       setok=0, hit=0, need3=0, cov3=0))
        for t, i in enumerate(idx):
            if t % 5000 == 0:
                print(f"  {win} {t:,}/{len(idx):,}", flush=True)
            x = ctx(int(i))
            if x is None:
                continue
            key = plan_key_of(x)
            if not key or not passes_axis_gate(key, x.axis, 7):
                continue
            got = _build_arm(x, key, "base")
            if got is None or _score(x, key, got) is None:
                continue
            legs, st, pl = got
            a1, a2 = x.shape.order[0], x.shape.order[1]
            sets = {frozenset(c) if not isinstance(c, frozenset) else c for c in legs}
            partners = {next(iter(s - {a1, a2})) for s in sets
                        if len(s - {a1, a2}) == 1}
            a = agg[key]
            a["n"] += 1
            a["ncov"] += len(partners)
            a["npt"] += len(legs)
            fin = set(x.win_tf)
            if a1 in fin and a2 in fin:
                a["both"] += 1
                third = next(c for c in fin if c not in (a1, a2))
                if third in partners:
                    a["cov"] += 1
                # 🔴 **同じ広さの基準線**: いま実際に覆えている相手の車数 m と同じだけ
                #    ①本番の相手順（p3 降順）②市場（三連複予測オッズ昇順）で採ったら
                #    覆えたか。広さを揃えないと「点数が多いから当たる」と混ざる。
                m = len(partners)
                if m:
                    rest = list(x.shape.order[2:])
                    if third in rest[:m]:
                        a["cov3"] += 1
                    mk = sorted(rest, key=lambda c: x.po_t3.get(
                        frozenset({a1, a2, c}), 9e9))
                    if third in mk[:m]:
                        a["need3"] += 1
                if frozenset(fin) in sets:
                    a["setok"] += 1
            if pl.bet_type == "trio":
                a["hit"] += x.win_t3 in set(legs)
            else:
                a["hit"] += x.win_tf in set(legs)
        print(f"\n=== {label} 商品ごとの相手の広げ方と、その仕事に対する失敗 ===")
        print(f"  {'商品':8s} {'n':>6s} {'点数':>5s} {'相手カバー':>8s} {'そろい%':>7s}"
              f" {'相手カバー率%':>11s} {'同幅p3順%':>10s} {'同幅市場%':>10s} {'的中%':>7s}")
        for k in sorted(agg):
            a = agg[k]
            if not a["n"]:
                continue
            b = max(a["both"], 1)
            print(f"  {k:8s} {a['n']:6d} {a['npt']/a['n']:5.2f} {a['ncov']/a['n']:8.2f}"
                  f" {a['both']/a['n']*100:7.2f} {a['cov']/b*100:11.2f}"
                  f" {a['cov3']/b*100:10.2f} {a['need3']/b*100:10.2f}"
                  f" {a['hit']/a['n']*100:7.2f}")



if __name__ == "__main__":
    globals()[sys.argv[1] if len(sys.argv) > 1 else "main"]()
