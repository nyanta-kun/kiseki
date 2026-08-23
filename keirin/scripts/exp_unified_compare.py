#!/usr/bin/env python3
"""🔴🔴 **使用禁止 — 欠陥があり結論に使えない。`exp_unified_compare2.py` を使うこと。**

2026-08-23 に3つの欠陥が見つかった（詳細は `exp_unified_compare2.py` の docstring と
`docs/strategy_rebuild_2026_08.md` §32）:

1. **in-sample 混入** — 探索窓で学習した直後に同じ窓を予測していた。
   モデル系の腕だけ持ち上がり、**選択そのものが壊れる**
2. **母集団不揃い** — 構成ごとに違うレースを落としたまま1つの表に ROI 降順で並べ、
   別のレース集合の数字を横に比べていた
3. **`LG_JOINT` の黙った退化** — 同時確率を `build_A`（p3上位2軸固定）から引くため、
   `AX_PAIR` / `AX_SWAP` では全件 lookup に失敗して p3 降順へ落ちていた
   （36構成のうち24構成が実質重複。エラーも警告も出ない）

**歴史として残すだけ。実行しないこと。**

---

**構成の統一比較台**（2026-08-23・ユーザー指摘）。

## 指摘

> 比較の統一も整備できていない。本日対応分の ROI は二軸総流しになっていそうだが、
> 相手の選別も元の検討に入っていた。適切に選別し、**相手点数を可変**、
> **投入金額が変わることを考慮**した比較が必要。

🔴 **そのとおり。** 本日の測定は §24=1点買い / §28.1=N点フォーメーション /
   §28.2=ペアの的中のみ / §29=7C形 と**買い方がバラバラ**で、横に比べられない。
   ここで**1枚の台**に載せ直す。

## 構成 = （軸ルール × 相手ルール × 点数ルール）

| 軸 | |
|---|---|
| `AX_P3` | 指数1位・2位（現行 7C） |
| `AX_PAIR` | ペア同時確率の argmax（§22/§24） |
| `AX_SWAP` | 2位を軸1にし、1位を軸から外す（ユーザー提案・§28.2） |

| 相手 | |
|---|---|
| `LG_P3` | `p3` 降順（現行） |
| `LG_JOINT` | **三者同時確率** `P(3車すべて3着内)` 降順（§24） |

| 点数 | |
|---|---|
| `N1`〜`N5` | 固定点数 |
| `N_GAP` | 7C の落差カット（`rank_7c_select_legs` → `cut_legs_by_gap`） |

## 投入金額の扱い（ここが指摘の核心）

**点数で1点あたりの賭け金が変わる**（`unit_stake(N)`・端数切捨て）。
さらに**構成によって買わないレースが出る**ので**件数が変わる**。
したがって ROI は「1レース平均」ではなく

    ROI = Σ払戻 ÷ Σ投資      Σ投資 = Σ(点数 × unit_stake(点数))

で測る。件数・Σ投資・1日あたり投資も**必ず併記**する
（`docs/product_portfolio_redesign_2026_08.md` の目的関数に合わせる）。

## 手順（事前登録に従う）

1. **探索窓 2024-01〜2025-12** で全構成を出し、ブロックごとに最良を選ぶ
2. **確認窓 2026-01〜06** で、**選んだ構成だけ**を当てる
3. 封印窓 2026-07-01〜08-22 は読まない
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.backfill_7t1_rank_wt import _load_finishes  # noqa: E402
from scripts.exp_axis1_bust_stratified import build as build_race  # noqa: E402
from scripts.exp_axis1_bust_stratified import load_rich  # noqa: E402
from scripts.exp_race_regime_3class import NAMES, fit_multi, label_P  # noqa: E402
from scripts.exp_trio_joint_partner import (  # noqa: E402
    build_A, fit, load_any, load_boards, load_entries)
from scripts.exp_trio_pair_model import build_rows as build_pairs  # noqa: E402
from src.result_top3 import winning_trifectas  # noqa: E402
from src.strategy_wt import (  # noqa: E402
    rank_7c_cut_legs_by_gap, rank_7c_select_legs, unit_stake)

SEARCH_END = "2025-12-31"
CONFIRM_START, CONFIRM_END = "2026-01-01", "2026-06-30"
PAYOUT_RATE = 0.7485
AXES = ["AX_P3", "AX_PAIR", "AX_SWAP"]
LEGS = ["LG_P3", "LG_JOINT"]
NPTS = ["N1", "N2", "N3", "N4", "N5", "N_GAP"]


def build_pool(rows, ent_pair, ent_rich, fin, board, jm, pm, rm, cuts):
    """1レース1辞書。全構成が共通で使う候補プール。"""
    Xa, _, ma = build_A(rows, ent_pair, fin)
    pa = jm.predict(Xa)
    joint = defaultdict(dict)
    for (key, _, a1, a2, c, _), p in zip(ma, pa):
        joint[key][(a1, a2, c)] = float(p)
    Xp, _, mp = build_pairs(rows, ent_pair, fin)
    pp = pm.predict(Xp)
    pair = defaultdict(dict)
    for (key, _, a, b, _, _), p in zip(mp, pp):
        pair[key][frozenset((a, b))] = float(p)
    _, _, _, Xr, _, mr = build_race(rows, ent_rich, fin)
    reg = rm.predict(Xr).argmax(1)
    blk = {m[0]: int(k) for m, k in zip(mr, reg)}
    p3_of = {r["key"]: r["p3"] for r in rows}
    ord_of = {r["key"]: r["order"] for r in rows}
    out = []
    for r in rows:
        key = r["key"]
        o3 = fin.get(key); b = board.get(key)
        if not o3 or not b or key not in blk or key not in pair:
            continue
        wins = {frozenset(w) for w in winning_trifectas(o3)}
        pays = [b[k] for k in wins if k in b]
        out.append(dict(key=key, date=r["date"], o=ord_of[key], p3=p3_of[key],
                        wins=wins, board=b, joint=joint.get(key, {}),
                        pair=pair[key], blk=blk[key],
                        pay=float(np.mean(pays)) if pays else None,
                        true_blk=label_P(float(np.mean(pays)), cuts) if pays else None))
    return out


def axes_of(r, rule):
    o = r["o"]
    if rule == "AX_P3":
        return o[0], o[1]
    if rule == "AX_PAIR":
        best = max(r["pair"].items(), key=lambda kv: kv[1])[0]
        a, b = sorted(best, key=lambda c: -r["p3"][c])
        return a, b
    # AX_SWAP: 2位を軸1に、1位を軸から外して相性最大の相方を選ぶ
    a1 = o[1]
    cand = [c for c in o if c not in (o[0], o[1])]
    if not cand:
        return None
    a2 = max(cand, key=lambda c: r["pair"].get(frozenset((a1, c)), 0.0))
    return a1, a2


def legs_of(r, a1, a2, rule):
    rest = [c for c in r["o"] if c not in (a1, a2)]
    if rule == "LG_P3":
        return sorted(rest, key=lambda c: (-r["p3"][c], c))
    j = r["joint"]
    return sorted(rest, key=lambda c: -j.get((a1, a2, c),
                                             r["p3"][c] * 1e-6))


def npts_of(r, a1, a2, legs, rule):
    if rule.startswith("N") and rule[1:].isdigit():
        n = int(rule[1:])
        return legs[:n]
    rest = [c for c in r["o"] if c not in (a1, a2)]
    sel = rank_7c_select_legs(rest, r["p3"])
    if len(sel) < 4:
        return []                       # 7C はこのレースを買わない
    cut = rank_7c_cut_legs_by_gap(sel, r["p3"])
    keep = {c for c in cut}
    return [c for c in legs if c in keep]


def evaluate(pool, ax, lg, np_, blocks=None):
    """→ {block: [(date, hit, pay, stake)]}"""
    out = defaultdict(list)
    for r in pool:
        if blocks is not None and r["blk"] not in blocks:
            continue
        a = axes_of(r, ax)
        if a is None:
            continue
        a1, a2 = a
        legs = npts_of(r, a1, a2, legs_of(r, a1, a2, lg), np_)
        if not legs:
            continue
        ks = [frozenset((a1, a2, c)) for c in legs]
        ks = [k for k in ks if k in r["board"]]
        if not ks:
            continue
        st = unit_stake(len(ks))
        hit = any(k in r["wins"] for k in ks)
        pay = sum(int(r["board"][k] * 100) * st // 100 for k in ks if k in r["wins"])
        out[r["blk"]].append((r["date"], int(hit), pay, len(ks) * st))
    return out


def agg(seg, n_days):
    if not seg:
        return None
    bet = sum(x[3] for x in seg); pay = sum(x[2] for x in seg)
    return dict(n=len(seg), per_day=len(seg) / n_days, bet=bet, pay=pay,
                roi=pay / bet, hit=sum(x[1] for x in seg) / len(seg),
                inv_day=bet / n_days)


def roi_ci(seg, B=3000, seed=5):
    by = defaultdict(lambda: [0.0, 0.0])
    for d, h, p, b in seg:
        z = by[d]; z[0] += b; z[1] += p
    v = np.array([[z[0], z[1]] for z in by.values()], float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(v), size=(B, len(v)))
    r = np.sort(v[idx, 1].sum(1) / v[idx, 0].sum(1))
    return r[int(B * .025)], r[int(B * .975)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache-a", default="data/exp/trio_rank_cache.jsonl")
    ap.add_argument("--cache-b", default="data/exp/tf_shape_cache4.jsonl")
    ap.add_argument("--rounds", type=int, default=400)
    args = ap.parse_args()

    allr = load_any(args.cache_a) + load_any(args.cache_b)
    S = [r for r in allr if r["date"] <= SEARCH_END]
    C = [r for r in allr if CONFIRM_START <= r["date"] <= CONFIRM_END]
    print(f"探索 {len(S):,}R / 確認 {len(C):,}R"
          f"   🔒 封印 2026-07-01〜08-22 は読まない\n")
    ks, kc = [r["key"] for r in S], [r["key"] for r in C]
    ep_s, ep_c = load_entries(ks), load_entries(kc)
    er_s, er_c = load_rich(ks), load_rich(kc)
    fs, fc = _load_finishes(ks), _load_finishes(kc)
    bs, bc = load_boards(ks), load_boards(kc)

    # 探索窓で3つのモデルを学習
    Xa, ya, _ = build_A(S, ep_s, fs)
    jm = fit(Xa, ya, args.rounds)
    Xp, yp, _ = build_pairs(S, ep_s, fs)
    pm = fit(Xp, yp, args.rounds)
    _, _, _, Xr, _, mr = build_race(S, er_s, fs)
    pays = []
    for m in mr:
        o3 = fs.get(m[0]); b = bs.get(m[0])
        w = [b[k] for k in {frozenset(x) for x in winning_trifectas(o3 or [])}
             if b and k in b] if o3 and b else []
        pays.append(float(np.mean(w)) if w else np.nan)
    pays = np.array(pays)
    ok = ~np.isnan(pays)
    cuts = tuple(np.quantile(pays[ok], [1 / 3, 2 / 3]))
    yreg = np.array([label_P(p, cuts) if o else 0 for p, o in zip(pays, ok)])
    rm = fit_multi(Xr[ok], yreg[ok], 500)
    print(f"レース型のしきい値（探索窓）: < {cuts[0]:.1f}倍 / < {cuts[1]:.1f}倍 / 以上")

    PS = build_pool(S, ep_s, er_s, fs, bs, jm, pm, rm, cuts)
    PC = build_pool(C, ep_c, er_c, fc, bc, jm, pm, rm, cuts)
    dS = len({r["date"] for r in PS}); dC = len({r["date"] for r in PC})
    print(f"プール 探索 {len(PS):,}R/{dS}日 ・ 確認 {len(PC):,}R/{dC}日\n")

    # ── 探索窓: 全構成 × ブロック ──
    best = {}
    for blk in range(3):
        print(f"===== [探索] ブロック「{NAMES[blk]}」 "
              f"{sum(1 for r in PS if r['blk']==blk):,}R =====")
        print(f"{'構成':>22}{'件/日':>8}{'投資/日':>10}{'的中%':>8}{'ROI':>8}"
              f"{'CI下限':>8}")
        rows = []
        for ax in AXES:
            for lg in LEGS:
                for np_ in NPTS:
                    seg = evaluate(PS, ax, lg, np_, {blk}).get(blk, [])
                    a = agg(seg, dS)
                    if not a or a["n"] < 300:
                        continue
                    lo, _ = roi_ci(seg)
                    rows.append((f"{ax}/{lg}/{np_}", a, lo))
        rows.sort(key=lambda x: -x[1]["roi"])
        for name, a, lo in rows[:8]:
            mk = " 🟢" if lo > PAYOUT_RATE else ""
            print(f"{name:>22}{a['per_day']:>8.2f}{a['inv_day']:>10,.0f}"
                  f"{a['hit']:>8.1%}{a['roi']:>8.1%}{lo:>8.1%}{mk}")
        base = next((r for r in rows if r[0] == "AX_P3/LG_P3/N_GAP"), None)
        if base:
            print(f"{'（現行相当）':>22}{base[1]['per_day']:>8.2f}"
                  f"{base[1]['inv_day']:>10,.0f}{base[1]['hit']:>8.1%}"
                  f"{base[1]['roi']:>8.1%}{base[2]:>8.1%}")
        best[blk] = rows[0][0] if rows else None
        print()

    # ── 確認窓: 選んだ構成だけ ──
    print("===== [確認] 探索窓で選んだ構成をそのまま当てる =====")
    print(f"{'ブロック':>10}{'構成':>22}{'件/日':>8}{'投資/日':>10}"
          f"{'的中%':>8}{'ROI':>8}{'CI下限':>8}")
    tot = []
    cur_tot = []
    for blk in range(3):
        if not best[blk]:
            continue
        ax, lg, np_ = best[blk].split("/")
        seg = evaluate(PC, ax, lg, np_, {blk}).get(blk, [])
        a = agg(seg, dC)
        if not a:
            continue
        lo, _ = roi_ci(seg)
        mk = " 🟢" if lo > PAYOUT_RATE else ""
        print(f"{NAMES[blk]:>10}{best[blk]:>22}{a['per_day']:>8.2f}"
              f"{a['inv_day']:>10,.0f}{a['hit']:>8.1%}{a['roi']:>8.1%}{lo:>8.1%}{mk}")
        tot += seg
        cur_tot += evaluate(PC, "AX_P3", "LG_P3", "N_GAP", {blk}).get(blk, [])
    for nm, seg in (("体系（ブロック別最良）", tot), ("現行相当（全ブロック一律）", cur_tot)):
        a = agg(seg, dC)
        if not a:
            continue
        lo, hi = roi_ci(seg)
        print(f"\n  {nm}: {a['per_day']:.2f}件/日 ・ 投資 {a['inv_day']:,.0f}円/日"
              f" ・ 的中 {a['hit']:.2%} ・ ROI {a['roi']:.1%} [{lo:.1%},{hi:.1%}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
