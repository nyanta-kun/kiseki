#!/usr/bin/env python3
"""`A_hit` の位置の段 — 出荷可否を決めるための測り直し（2026-09-14）。

`line_inner_2026_09_14.md` の採用候補には限界が2つあった:

  ① 段の**強さ**（強/中/弱）の選択に確認窓を見ている
  ② 台が本番の `build_with_gate_fallback` を通っていない
     （λr の並べ替え `apply_order_swap`・押さえ目 `apply_osae`・
      配分フォールバック `ALLOC_BEFORE_MIN_PAYOUT` が未適用。
      `rows.pkl` は `allocate` が None を返した行が丸ごと落ちている＝hit系の 8.3〜8.5%）

本稿はその2つを潰す。

🔴 **段の順序は探索窓の記述（`line_inner.py resid`）から決めたものを固定し、
   強さだけを探索窓で選ぶ。確認窓は最後に一度だけ見る。**
🔴 **`A_hit` には `GATE_FALLBACK` が無い**（`F_hit` / `C_hit` だけ）。
   ゲートに落ちたら商品は消える。件数の目減りは代替では埋まらない。

    PYTHONPATH=. .venv/bin/python scripts/exp_type_lab/a_hit_ship.py build|select|confirm
"""
from __future__ import annotations

import pickle
import random
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts" / "exp_type_lab"))

import common as C  # noqa: E402
from typef_racetype import ctx, _plan_for  # noqa: E402
from src.strategy_wt import rank_7t3_order_swap_probs  # noqa: E402
from src.type_lab import (  # noqa: E402
    MIN_MEAN_PAYOUT, PLANS, SIGNBOARD_RACE_TYPES, build_with_gate_fallback,
    mean_expected_payout)

#: 入稿ゲート。🔴 `build_with_gate_fallback` は**ゲートを判定しない**
#:    （「どれもゲートに落ちるなら元の結果をそのまま返す＝見送りの判断は入稿側」）。
#:    呼ぶ側で必ず掛けること。ここを忘れると `A_trio` が常に組めたことになり
#:    `_plan_for` が `A_hit` を一度も返さない（2026-09-14 に実際に踏んで 0行になった）。
MIN_POINT_ODDS = 2.0


def passes_gate(stakes, pred_odds) -> bool:
    return (mean_expected_payout(stakes, pred_odds) > MIN_MEAN_PAYOUT
            and min(float(pred_odds[c]) for c in stakes) >= MIN_POINT_ODDS)

OUT = Path("/tmp/ratebranch/a_hit_ship.pkl")

#: 位置の段。**順序は固定**（`line_inner.py resid` の記述・両窓一致）で、
#: 動かすのは強さだけ。(軸2車と同ライン, 軸1と同ライン近傍, 別ライン, 軸1のさらに後ろ)
TIER_SETS = {
    "強 2.0/1.2/1.0/0.4": (2.0, 1.2, 1.0, 0.4),
    "中 1.5/1.1/1.0/0.7": (1.5, 1.1, 1.0, 0.7),
    "弱 1.25/1.05/1.0/0.85": (1.25, 1.05, 1.0, 0.85),
    "罰のみ 1/1/1/0.4": (1.0, 1.0, 1.0, 0.4),
    "賞のみ 2/1/1/1": (2.0, 1.0, 1.0, 1.0),
}


def line_members(lg, lpos, g):
    return [c for _, c in sorted((float(lpos[c - 1]), c) for c in range(1, 8)
                                 if str(lg[c - 1]) == str(g))]


def group_of(lg, lpos, a1, a2, c):
    """非軸車 c の、軸2車から見た立ち位置。"""
    tgt = {str(lg[a1 - 1]), str(lg[a2 - 1])}
    g = str(lg[c - 1])
    if g not in tgt:
        return "別ライン"
    mem = line_members(lg, lpos, g)
    ax = [x for x in mem if x in (a1, a2)]
    if not ax:
        return "別ライン"
    pc = mem.index(c)
    pos = sorted(mem.index(x) for x in ax)
    if len(ax) == 2:
        return "軸2車と同ライン"
    if pc < pos[0]:
        return "軸1と同ライン近傍"
    return "軸1と同ライン近傍" if pc == pos[0] + 1 else "軸1のさらに後ろ"


def weights(lg, lpos, a1, a2, tier, rng=None):
    key = {"軸2車と同ライン": 0, "軸1と同ライン近傍": 1, "別ライン": 2, "軸1のさらに後ろ": 3}
    w = {c: tier[key[group_of(lg, lpos, a1, a2, c)]]
         for c in range(1, 8) if c not in (a1, a2)}
    if rng is None:
        return w
    vals = list(w.values())
    rng.shuffle(vals)
    return dict(zip(list(w), vals))


def build():
    """本番の `build_with_gate_fallback` で台を作り直す（A_hit を売るレースだけ）。"""
    z = C.board()
    rt = np.array([str(v) for v in z["RTYPE"]])
    tp = np.array([str(v) for v in z["TYPE"]])
    keys = np.array([str(v) for v in z["KEY"]])
    sign_rt = tuple(SIGNBOARD_RACE_TYPES)
    rows = []
    idx = []
    for w in ("explore", "confirm"):
        idx += [(w, int(i)) for i in C.select(None, w) if tp[int(i)] in "ABCDEF"]
    for n, (w, i) in enumerate(idx):
        if n % 5000 == 0:
            print(f"  {n:,}/{len(idx):,}", flush=True)
        if tp[i] != "A":
            continue
        x = ctx(i)
        if x is None:
            continue
        # A_trio が組めるかで型Aの売り物が決まる（本番 `_plan_for` と同じ）
        tr = PLANS["A_trio"]
        got_tr = build_with_gate_fallback(x.shape, tr, x.po_t3, x.pr_t3, 7)
        trio_ok = bool(got_tr) and passes_gate(got_tr[1], x.po_t3)
        key = _plan_for(tp[i], rt[i], x.shape.pw_ent, trio_ok, sign_rt)
        if key != "A_hit":
            continue
        lg = np.array([str(z["LG"][i][c]) for c in range(7)])
        lpos = z["A_line_pos"][i].astype(float)
        cars = list(range(1, 8))
        p3 = {c: float(z["P3"][i][c - 1]) for c in cars}
        pw = {c: float(z["PW"][i][c - 1]) for c in cars}
        op = rank_7t3_order_swap_probs(cars, pw, p3,
                                       line_group={c: lg[c - 1] for c in cars},
                                       line_pos={c: lpos[c - 1] for c in cars})
        rows.append(dict(win=w, i=i, race_key=str(keys[i]), date=x.date,
                         shape=x.shape, po=dict(x.po_tf), pr=dict(x.pr_tf),
                         op=op, lg=lg, lpos=lpos,
                         a1=x.shape.order[0], a2=x.shape.order[1],
                         fin=x.win_tf, pay_tf=float(x.pay_tf)))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("wb") as f:
        pickle.dump(rows, f)
    print(f"保存 {OUT}  {len(rows):,}行  "
          f"explore {sum(1 for r in rows if r['win']=='explore'):,} / "
          f"confirm {sum(1 for r in rows if r['win']=='confirm'):,}")


def _load():
    with OUT.open("rb") as f:
        return pickle.load(f)


def run(rows, tier=None, rng=None):
    """本番経路で A_hit を組み、1商品ずつ採点する。"""
    plan = PLANS["A_hit"]
    per, days = {}, set()
    inv = pay = 0.0
    pays = []
    for r in rows:
        prb = r["pr"]
        if tier is not None:
            w = weights(r["lg"], r["lpos"], r["a1"], r["a2"], tier, rng)
            prb = {c: v * float(np.prod([w.get(x, 1.0) for x in c]))
                   for c, v in prb.items()}
        got = build_with_gate_fallback(r["shape"], plan, r["po"], prb, 7,
                                       order_probs=r["op"])
        if not got:
            continue
        legs, st, _ = got
        if not passes_gate(st, r["po"]):
            continue
        bet = float(sum(st.values()))
        p = float(st[r["fin"]] / 100.0 * r["pay_tf"] * 100.0) if r["fin"] in st else 0.0
        inv += bet
        pay += p
        pays.append(p)
        days.add(r["date"])
        per[r["race_key"]] = 1 if p >= bet else 0
    n = len(pays)
    hits = [p for p in pays if p > 0]
    return dict(n=n, per_day=n / max(len(days), 1),
                shown=sum(per.values()) / n * 100 if n else 0.0,
                roi=pay / inv * 100 if inv else 0.0,
                med=float(np.median(hits)) if hits else 0.0, per=per)


def _ci(pairs, iters=2000, seed=7):
    rng = np.random.default_rng(seed)
    a = np.array([x[1] - x[0] for x in pairs], float)
    idx = rng.integers(0, len(a), size=(iters, len(a)))
    d = a[idx].mean(1) * 100
    return float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5))


def _paired(rows, tier, seeds=0):
    b, a = run(rows), run(rows, tier)
    common = [k for k in b["per"] if k in a["per"]]
    pairs = [(b["per"][k], a["per"][k]) for k in common]
    d = (np.mean([p[1] for p in pairs]) - np.mean([p[0] for p in pairs])) * 100
    lo, hi = _ci(pairs)
    ctrl = []
    for s in range(seeds):
        c = run(rows, tier, random.Random(5000 + s))
        cc = [k for k in b["per"] if k in c["per"]]
        ctrl.append((np.mean([c["per"][k] for k in cc])
                     - np.mean([b["per"][k] for k in cc])) * 100)
    return b, a, d, lo, hi, ctrl


def select():
    """🔴 段の強さを **探索窓だけ** で選ぶ。確認窓は見ない。"""
    rows = [r for r in _load() if r["win"] == "explore"]
    print(f"§1 段の強さの選択（探索窓 2024-07〜2025-12 のみ・n={len(rows):,}）\n")
    print(f"{'段':24s}{'件数':>14}{'表示的中':>10}{'Δ(対)':>9}{'95%CI':>18}{'ROI':>8}")
    b0 = run(rows)
    print(f"{'現行':24s}{b0['n']:6d}{'':8}{b0['shown']:9.2f}%{'':9}{'':18}{b0['roi']:7.1f}%")
    best = None
    for name, t in TIER_SETS.items():
        b, a, d, lo, hi, _ = _paired(rows, t)
        print(f"{name:24s}{b['n']:6d}→{a['n']:<7d}{a['shown']:9.2f}%{d:+8.2f}pt"
              f"{f'[{lo:+.2f},{hi:+.2f}]':>18}{a['roi']:7.1f}%")
        if lo > 0 and (best is None or d > best[1]):
            best = (name, d)
    print(f"\n→ 探索窓で選ばれる段: {best[0] if best else '（CI が 0 を跨がない段は無い）'}")


def confirm():
    """🔴 探索窓で選んだ段を、確認窓で**一度だけ**評価する。"""
    name = sys.argv[2] if len(sys.argv) > 2 else "中 1.5/1.1/1.0/0.7"
    t = TIER_SETS[name]
    all_rows = _load()
    print(f"§2 一度きり評価（段 = {name}）\n")
    print(f"{'窓':>8}{'件数':>14}{'件/日':>9}{'表示的中':>18}{'Δ(対)':>9}"
          f"{'95%CI':>18}{'ROI':>14}{'対照20seed':>12}")
    for w in ("explore", "confirm"):
        rows = [r for r in all_rows if r["win"] == w]
        b, a, d, lo, hi, ctrl = _paired(rows, t, seeds=20)
        wins = sum(1 for c in ctrl if d > c)
        print(f"{w:>8}{b['n']:6d}→{a['n']:<7d}{b['per_day']:4.2f}→{a['per_day']:<4.2f}"
              f"{b['shown']:8.2f}→{a['shown']:<8.2f}%{d:+8.2f}pt"
              f"{f'[{lo:+.2f},{hi:+.2f}]':>18}{b['roi']:6.1f}→{a['roi']:<6.1f}%"
              f"{wins:9d}/20")


if __name__ == "__main__":
    {"build": build, "select": select, "confirm": confirm}[sys.argv[1]]()
