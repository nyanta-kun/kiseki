#!/usr/bin/env python3
"""合成オッズ（= 1/Σ(1/予測オッズ)）を上げる腕の測り直し（2026-09-14・ユーザー観察）。

> 現在、合成2倍前後のレースも多くあるが、このレースだけでは50%以上の的中率が
> 必要で、だいぶ足りない。合成3倍以上の買い目点数に調整するなど必要に思うがどうか。

台: `synth_floor_build.py`（板 36,237R・**現行 `PLANS` を本番関数で回す**）。
窓: 探索 2024-07〜2025-12 / 確認 2026-01〜08（予測オッズ train_end 2025-12-31）。

🔴 **売る条件（入稿ゲート 平均想定払戻>2万・全点>=2.0倍）は全腕で同じ。**
   腕が動かすのは「どう組むか」だけなので在庫は原理的に減らない。

    PYTHONPATH=. .venv/bin/python scripts/exp_type_lab/synth_floor.py band|arms|breakeven
"""
from __future__ import annotations

import importlib.util
import pickle
import sys
from dataclasses import replace
from pathlib import Path
from statistics import median

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts" / "exp_type_lab"))

import common as C  # noqa: E402
from typef_racetype import _plan_for  # noqa: E402
from src.type_lab import (  # noqa: E402
    MIN_MEAN_PAYOUT, PLANS, RaceShape, SIGNBOARD_RACE_TYPES,
    build_with_gate_fallback, mean_expected_payout)

_spec = importlib.util.spec_from_file_location(
    "gate", REPO.parent / "backend/src/services/keirin_type_lab_gate.py")
_G = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_G)
AXIS_GATE_MIN = _G.AXIS_GATE_MIN

PERMS, C3 = C.CANON, C.CANON3
MIN_POINT_ODDS = 2.0
SIGN_RT = tuple(SIGNBOARD_RACE_TYPES)
BOARD = Path("/tmp/synthfloor/board.pkl")
HIT_PLANS = ("A_hit", "B_hit", "C_hit", "D_hit", "E_hit", "F_hit", "A_trio", "A_ana",
             "F_pay", "F_sign")
PROB_TOP = ("A_hit", "B_hit", "C_hit", "E_hit", "F_hit")


def _rows():
    with BOARD.open("rb") as f:
        return pickle.load(f)


def _ctx(r):
    po = {PERMS[t]: float(r["po"][t]) for t in range(210) if np.isfinite(r["po"][t])}
    pr = {PERMS[t]: float(r["pr"][t]) for t in range(210)}
    op = {PERMS[t]: float(r["op"][t]) for t in range(210)}
    pot = {frozenset(C3[t]): float(r["pot"][t]) for t in range(35)
           if np.isfinite(r["pot"][t])}
    prt = {frozenset(C3[t]): float(r["prt"][t]) for t in range(35)}
    shape = RaceShape(r["tl"], r["axis_sum"], r["arare"], r["gap"], False,
                      tuple(r["order"]), r["pw_ent"])
    return shape, po, pr, op, pot, prt


def _gate(stakes, pod) -> bool:
    return (mean_expected_payout(stakes, pod) > MIN_MEAN_PAYOUT
            and min(float(pod[c]) for c in stakes) >= MIN_POINT_ODDS)


# ───────────────────────── 腕 ─────────────────────────

def arm_plans(name: str) -> tuple[dict, float]:
    """(差し替えた PLANS, 組むときの目標払戻 T)。"""
    P = dict(PLANS)
    T = float(MIN_MEAN_PAYOUT)
    if name == "cur":
        return P, T
    if name == "b1":                       # C_hit の帯下差込を戻す（9/8 の取り消し）
        P["C_hit"] = replace(P["C_hit"], underband_min=0.0)
        return P, T
    if name.startswith("floor"):           # 合成下限（Σ(1/O) <= 1/X）
        x = float(name[5:])
        for k in PROB_TOP:
            P[k] = replace(P[k], sigma_max=1.0 / x)
        return P, T
    if name.startswith("T"):               # 組むときの目標払戻を上げる（τ適応を全プランへ）
        T = float(name[1:])
        for k in PROB_TOP:
            P[k] = replace(P[k], tau_adaptive=True)
        return P, T
    if name == "f8":                       # F_hit を 12点 → 8点
        P["F_hit"] = replace(P["F_hit"], max_legs=8)
        return P, T
    if name == "a2":                       # A_hit を 3点 → 2点
        P["A_hit"] = replace(P["A_hit"], max_legs=2)
        return P, T
    raise SystemExit(f"unknown arm {name}")


def run(rows, name: str, cap: bool = False) -> dict:
    P, T = arm_plans(name)
    out = []
    for r in rows:
        shape, po, pr, op, pot, prt = _ctx(r)
        # 型A は A_trio が組めるかで売り物が変わる（本番 `sell_plans_for` と同じ）
        trio_ok = False
        if r["tl"] == "A":
            g = build_with_gate_fallback(shape, P["A_trio"], pot, prt, 7, T, op)
            trio_ok = bool(g) and _gate(g[1], pot)
        key = _plan_for(r["tl"], r["rtype"], r["pw_ent"], trio_ok, SIGN_RT)
        if r["axis_sum"] < AXIS_GATE_MIN.get(key, 0.0):
            continue
        plan = P[key]
        pod, prb = (pot, prt) if plan.bet_type == "trio" else (po, pr)
        got = build_with_gate_fallback(shape, plan, pod, prb, 7, T, op)
        if not got:
            continue
        legs, st, used = got
        if not _gate(st, pod):
            continue
        bet = float(sum(st.values()))
        if plan.bet_type == "trio":
            w = frozenset(C3[r["win_t3"]])
            pay = float(st[w] * r["odds_t3"]) if w in st else 0.0
        else:
            w = PERMS[r["win_tf"]]
            pay = float(st[w] / 100.0 * r["pay_tf"] * 100.0) if w in st else 0.0
        s = sum(1.0 / float(pod[c]) for c in st)
        out.append(dict(key=r["key"], date=r["date"], plan=key, k=len(st),
                        bet=bet, pay=pay, syn=1.0 / s if s else 0.0,
                        mean=float(mean_expected_payout(st, pod))))
    return out


def summarize(rs, n_days_all: int | None = None) -> dict:
    """`common.summarize` と**同じ定義**で集計する（2026-09-21 是正）。

    🔴 このローカル複製は3か所で `common.summarize` と食い違っていた:

    | 量 | 旧（ここ） | 正（`common.summarize`） |
    |---|---|---|
    | 表示的中 | `pay >= bet`（元返しを的中に数える） | `pay > inv` |
    | ガミ率の分母 | 全件 | **的中数** |
    | 件/日 の分母 | **そのセグメントが出た日数** | 窓の全開催日 |

    件/日 の分母は既知の 3.1倍バグと同型で、実測で最大 **1.78倍**過大になる。
    `n_days_all` を渡すこと（呼び出し側が `C.days_of(C.select(None, win))`）。

    ⚠️ ここを `common.summarize` へ丸ごと寄せられないのは、`syn` / `lo` /
       `inv_day` という**この実験にしか無い列**があるため。共通部分の定義は
       上の表のとおり必ず一致させること。
    """
    n = len(rs)
    if not n:
        return dict(n=0)
    days = n_days_all or len({r["date"] for r in rs})
    shown = sum(1 for r in rs if r["pay"] > r["bet"])
    hit = sum(1 for r in rs if r["pay"] > 0)
    inv = sum(r["bet"] for r in rs)
    pay = sum(r["pay"] for r in rs)
    pays = [r["pay"] for r in rs if r["pay"] > 0]
    return dict(n=n, per_day=n / days, shown=shown / n * 100,
                hit=hit / n * 100,
                gami=(hit - shown) / hit * 100 if hit else 0.0,
                roi=pay / inv * 100, med=median(pays) if pays else 0.0,
                big=sum(1 for r in rs if r["pay"] >= 100_000) / days,
                syn=median(r["syn"] for r in rs), k=np.mean([r["k"] for r in rs]),
                lo=sum(1 for r in rs if r["syn"] < 2.5) / n * 100,
                inv_day=inv / days)


HDR = (f"{'腕':16s}{'件/日':>7}{'点数':>6}{'合成中':>7}{'<2.5':>7}"
       f"{'表示的中':>9}{'ガミ':>6}{'払戻中央':>10}{'10万+/日':>9}{'ROI':>7}{'投資/日':>10}")


def _line(name, t):
    return (f"{name:16s}{t['per_day']:7.2f}{t['k']:6.1f}{t['syn']:7.2f}{t['lo']:6.1f}%"
            f"{t['shown']:8.2f}%{t['gami']:5.2f}%{t['med']:10,.0f}{t['big']:9.3f}"
            f"{t['roi']:6.1f}%{t['inv_day']:10,.0f}")


def _ci(pairs, iters=2000, seed=7):
    rng = np.random.default_rng(seed)
    a = np.array([x[1] - x[0] for x in pairs], float)
    if not len(a):
        return 0.0, 0.0
    idx = rng.integers(0, len(a), size=(iters, len(a)))
    d = a[idx].mean(1) * 100
    return float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5))


def arms() -> None:
    names = sys.argv[2].split(",") if len(sys.argv) > 2 else [
        "cur", "b1", "floor2.5", "floor3.0", "floor3.5", "T25000", "T30000", "f8"]
    allrows = _rows()
    for label, win in (("確認 2026-01〜08", "confirm"), ("探索 2024-07〜2025-12", "explore")):
        rows = [r for r in allrows if r["win"] == win]
        # 🔴 件/日 の分母は**窓の全開催日**（そのセグメントが出た日数ではない）
        nd = C.days_of(C.select(None, win))
        print(f"\n=== {label}  n={len(rows):,}R / {nd}日 ===")
        print(HDR)
        base = None
        for nm in names:
            rs = run(rows, nm)
            t = summarize(rs, nd)
            print(_line(nm, t))
            if nm == "cur":
                base = {r["key"]: r for r in rs}
            elif base is not None:
                cur = {r["key"]: r for r in rs}
                com = [k for k in base if k in cur]
                pr = [(1 if base[k]["pay"] >= base[k]["bet"] else 0,
                       1 if cur[k]["pay"] >= cur[k]["bet"] else 0) for k in com]
                d = (np.mean([p[1] for p in pr]) - np.mean([p[0] for p in pr])) * 100
                lo, hi = _ci(pr)
                print(f"{'':16s}  Δ表示的中 {d:+.2f}pt [{lo:+.2f},{hi:+.2f}]  "
                      f"(対比較 n={len(com):,})")


def band() -> None:
    """合成帯ごとの損益分岐（親の表の再現）。"""
    allrows = _rows()
    for label, win in (("確認 2026", "confirm"), ("探索 2024-07〜2025-12", "explore")):
        rows = [r for r in allrows if r["win"] == win]
        rs = run(rows, "cur")
        # 🔴 窓の全開催日（そのセグメントが出た日数ではない）
        days = C.days_of(C.select(None, win))
        print(f"\n=== {label}  {len(rs):,}件 / {days}日 ===")
        print(f"{'合成帯':12s}{'n':>7}{'件/日':>7}{'必要的中':>9}{'表示的中':>9}"
              f"{'達成率':>8}{'ROI':>8}{'払戻中央':>10}{'10万+/日':>9}")
        for lo, hi in ((2.0, 2.5), (2.5, 3.0), (3.0, 4.0), (4.0, 6.0), (6.0, 10.0),
                       (10.0, 1e9), (0, 1e9)):
            g = [r for r in rs if lo <= r["syn"] < hi]
            if not g:
                continue
            t = summarize(g, days)
            need = 100.0 / np.mean([r["syn"] for r in g])
            tag = "全体" if lo == 0 else f"{lo:g}〜{hi:g}" if hi < 1e9 else f"{lo:g}〜"
            print(f"{tag:12s}{t['n']:7,}{t['n']/days:7.2f}{need:8.2f}%{t['shown']:8.2f}%"
                  f"{t['shown']/need*100:7.0f}%{t['roi']:7.1f}%{t['med']:10,.0f}{t['big']:9.3f}")




def plans() -> None:
    """腕ごとにプラン別の内訳を出す（誰が払うか）。"""
    names = sys.argv[2].split(",") if len(sys.argv) > 2 else ["cur", "b1", "T25000", "T30000"]
    allrows = _rows()
    for label, win in (("確認 2026-01〜08", "confirm"), ("探索 2024-07〜2025-12", "explore")):
        rows = [r for r in allrows if r["win"] == win]
        nd = C.days_of(C.select(None, win))   # 🔴 件/日 の分母は窓の全開催日
        print(f"\n=== {label}  /{nd}日 ===")
        res = {nm: run(rows, nm) for nm in names}
        keys = sorted({r["plan"] for r in res[names[0]]})
        for k in keys:
            print(f"\n[{k}]")
            print(f"{'腕':12s}{'件/日':>7}{'点数':>6}{'合成中':>7}{'<2.5':>7}{'表示的中':>9}{'払戻中央':>10}{'ROI':>7}")
            for nm in names:
                g = [r for r in res[nm] if r["plan"] == k]
                if not g:
                    continue
                t = summarize(g, nd)
                print(f"{nm:12s}{t['per_day']:7.2f}{t['k']:6.1f}{t['syn']:7.2f}{t['lo']:6.1f}%"
                      f"{t['shown']:8.2f}%{t['med']:10,.0f}{t['roi']:6.1f}%")

if __name__ == "__main__":
    {"arms": arms, "band": band, "plans": plans}[sys.argv[1] if len(sys.argv) > 1 else "arms"]()
