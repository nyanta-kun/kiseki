#!/usr/bin/env python3
"""型ラボ 券種（bet type）の妥当性検証（2026-09-10・ユーザー依頼）。

## 先に本番を読んだ結果（CLAUDE.md「測る前に本番コードを読む」）

- 1レース = 1商品 = 10,000円。売る1つは `src.type_lab.sell_plans_for`
  （看板枠 → 型Aの3分割 → 型Fの種別）。7車で実際に売るのは
  A_ana / A_trio / A_hit / B_hit / C_hit / **D_hit(三連複)** / E_hit / F_hit / F_pay / F_sign。
- 買い目は `build_legs`、配分は `allocate`（`conf` 床 = 予算×2.0÷予測オッズ、残りは確率比例／
  `dutch` は ∝1/予測オッズ）。
- 入稿ゲートは2つ。**通してから比べる**:
    ① `MIN_POINT_ODDS = 2.0`   1点でも**予測**オッズ < 2.0 → レースごと見送り
    ② `MIN_MEAN_PAYOUT = 20,000`  想定払戻の**平均** <= 2万円 → 見送り
- 予測オッズは三連単だけがモデル（`odds_tf_n7`）。**三連複の予測オッズは
  `1/Σ_perm(1/PO_perm)` で三連単板から導出している**（`build_race_type_board.py`）。
  → 2車券も**同じ導出**で作れる。本稿の主測定はこの導出値で行う（＝新モデル不要）。
  実オッズは検算にだけ使う（実オッズをゲートに使うと look-ahead）。

再現:
    PYTHONPATH=. .venv/bin/python scripts/exp_type_lab/bet_type_build.py   # 台
    PYTHONPATH=. .venv/bin/python scripts/exp_type_lab/bet_type.py calib
    PYTHONPATH=. .venv/bin/python scripts/exp_type_lab/bet_type.py arms
    PYTHONPATH=. .venv/bin/python scripts/exp_type_lab/bet_type.py paired
"""
from __future__ import annotations

import itertools
import math
import pickle
import sys
from collections import defaultdict
from pathlib import Path
from statistics import median

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts" / "exp_type_lab"))

import common as C  # noqa: E402
from typef_racetype import ctx, _plan_for  # noqa: E402
from src.type_lab import (  # noqa: E402
    BUDGET, PLANS, Plan, SIGNBOARD_RACE_TYPES, allocate, build_legs,
    mean_expected_payout)

UNIT = 100
MIN_MEAN_PAYOUT, MIN_POINT_ODDS = 20_000, 2.0
PERMS = C.CANON
PAIRS = list(itertools.combinations(range(1, 8), 2))
PIDX = {frozenset(p): i for i, p in enumerate(PAIRS)}
ORD2 = list(itertools.permutations(range(1, 8), 2))
OIDX = {p: i for i, p in enumerate(ORD2)}
ROWS = Path("/tmp/bet_type_rows.pkl")

_2C = None


def two_car():
    """🔴 **NpzFile の添字アクセスは毎回全展開する**（MEMORY.md の既知の罠）。
    必ず dict へ実体化してから使う。ここを怠ると1行あたり 30MB の解凍が走る。"""
    global _2C
    if _2C is None:
        z = np.load("/tmp/bet_type_2car.npz")
        _2C = {k: z[k] for k in z.files}
    return _2C


# ══════════════════════════════════════════════════════════════════════════
# 2車券の「予測オッズ」と確率を三連単板から導く
#
# 🔴 三連複と同じ導出（`1/Σ(1/PO)`）。PO_perm = 払戻率/p_perm なので
#    Σ_{対象perm}(1/PO) = P(事象)/払戻率、よって 予測オッズ = 1/Σ(1/PO)。
#    `PAYBACK/Σ` と書くと一律 0.75 倍ずれる（board 構築の既知の罠と同型）。
# 🔴 対象 perm が1つでも欠けたら NaN（部分和は必ず過大評価になる）。
# ══════════════════════════════════════════════════════════════════════════
_W_PERMS = {fp: [t for t, p in enumerate(PERMS) if set(fp) <= set(p)] for fp in PIDX}
_Q_PERMS = {fp: [t for t, p in enumerate(PERMS) if set(p[:2]) == set(fp)] for fp in PIDX}
_E_PERMS = {o: [t for t, p in enumerate(PERMS) if p[:2] == o] for o in ORD2}
_W_KEYS = list(_W_PERMS)
_Q_KEYS = list(_Q_PERMS)
_E_KEYS = list(_E_PERMS)
_W_IDX = np.array([_W_PERMS[k] for k in _W_KEYS])      # (21,30)
_Q_IDX = np.array([_Q_PERMS[k] for k in _Q_KEYS])      # (21,10)
_E_IDX = np.array([_E_PERMS[k] for k in _E_KEYS])      # (42,5)


def _derive(inv, pra, keys, idx):
    """(予測オッズ dict, 確率 dict)。inv=1/PO の配列(210)・pra=確率の配列(210)。"""
    s = inv[idx].sum(axis=1)
    q = pra[idx].sum(axis=1)
    od = {k: float(1.0 / v) for k, v in zip(keys, s) if v > 0}
    pb = {k: float(v) for k, v in zip(keys, q)}
    return od, pb


def dutch(legs, odds, budget=BUDGET, unit=UNIT):
    """∝1/予測オッズ。組めなければ None。"""
    k = len(legs)
    n_units = budget // unit
    if k == 0 or k > n_units:
        return None
    o = [float(odds[c]) for c in legs]
    if any(x <= 0 for x in o):
        return None
    w = [1.0 / x for x in o]
    tot = sum(w)
    units = [max(int(n_units * x / tot), 0) for x in w]
    while sum(units) < n_units:
        j = min(range(k), key=lambda a: units[a] / max(w[a], 1e-12))
        units[j] += 1
    while sum(units) > n_units:
        j = max(range(k), key=lambda a: units[a] / max(w[a], 1e-12))
        units[j] -= 1
    if any(u <= 0 for u in units):
        return None
    return {c: u * unit for c, u in zip(legs, units)}


def gate_ok(stakes, odds):
    if not stakes:
        return False
    if min(float(odds[c]) for c in stakes) < MIN_POINT_ODDS:
        return False
    mean = sum(stakes[c] * float(odds[c]) for c in stakes) / len(stakes)
    return mean > MIN_MEAN_PAYOUT


def _pay(stakes, real, win_key):
    """確定オッズで採点。"""
    if win_key not in stakes:
        return 0.0
    o = real.get(win_key)
    if o is None or not np.isfinite(o) or o <= 0:
        return None            # 確定オッズ欠測 → 採点不能
    return float(stakes[win_key]) * float(o)


# ══════════════════════════════════════════════════════════════════════════
PR_NPZ = Path("/tmp/bet_type_prtf.npz")


def build_pr():
    """`rank_7t3_blend_probs`（λ/μ 込み・本番と同じ）を全レースぶん焼く。

    🔴 **行ごとの dict を pickle しない**（730 dict/レース × 36,427R で数GBになる）。
       確率だけを float32 の (N,210) で持ち、他は板から都度作る。
    """
    z = C.board()
    tp = np.array([str(v) for v in z["TYPE"]])
    N = len(tp)
    PR = np.zeros((N, 210), np.float32)
    OK = np.zeros(N, bool)
    from src.strategy_wt import rank_7t3_blend_probs
    cars = list(range(1, 8))
    idx = [int(i) for i in np.flatnonzero(np.isin(tp, list("ABCDEF")))]
    for n, i in enumerate(idx):
        if n % 4000 == 0:
            print(f"  {n:,}/{len(idx):,}", flush=True)
        p3 = {c: float(z["P3"][i][c - 1]) for c in cars}
        pw = {c: float(z["PW"][i][c - 1]) for c in cars}
        lg = {c: z["LG"][i][c - 1] for c in cars}
        lp = {c: z["A_line_pos"][i][c - 1] for c in cars}
        pr = rank_7t3_blend_probs(cars, pw, p3, line_group=lg, line_pos=lp)
        for t, perm in enumerate(PERMS):
            PR[i, t] = pr.get(perm, 0.0)
        OK[i] = True
    np.savez_compressed(PR_NPZ, PR=PR, OK=OK)
    print("saved", PR_NPZ)


_PR = None


def pr_board():
    """同上。NpzFile のまま添字すると毎回 30MB を解凍する。"""
    global _PR
    if _PR is None:
        z = np.load(PR_NPZ)
        _PR = {k: z[k] for k in z.files}
    return _PR


class Row(dict):
    pass


_ARR = None


def _arr():
    global _ARR
    if _ARR is None:
        z = C.board()
        _ARR = {k: z[k] for k in ("PO", "WIN", "PAY", "TRIO_PO", "TRIO_ODDS",
                                  "TRIO_WIN", "TRIO_PAY", "DATE", "TYPE", "RTYPE",
                                  "AXIS_SUM", "ARARE", "GAP", "P3", "PW")}
    return _ARR


def make_row(i: int, win: str) -> Row | None:
    from src.type_lab import RaceShape, win_entropy
    a = _arr()
    P = pr_board()
    if not P["OK"][i]:
        return None
    t2 = two_car()
    cars = list(range(1, 8))
    p3 = {c: float(a["P3"][i][c - 1]) for c in cars}
    pw = {c: float(a["PW"][i][c - 1]) for c in cars}
    pr = {PERMS[t]: float(P["PR"][i][t]) for t in range(210)}
    po_arr = a["PO"][i]
    po_tf = {PERMS[t]: float(po_arr[t]) for t in range(210)
             if np.isfinite(po_arr[t]) and po_arr[t] > 0}
    if len(po_tf) < 60:
        return None
    order = tuple(sorted(cars, key=lambda c: (-p3[c], c)))
    shape = RaceShape(str(a["TYPE"][i]), float(a["AXIS_SUM"][i]), int(a["ARARE"][i]),
                      float(a["GAP"][i]), False, order, win_entropy(pw))
    po_t3 = {frozenset(c): float(a["TRIO_PO"][i][j]) for j, c in enumerate(C.CANON3)
             if np.isfinite(a["TRIO_PO"][i][j]) and a["TRIO_PO"][i][j] > 0}
    pr_t3 = {frozenset(c): sum(pr.get(p, 0.0) for p in itertools.permutations(c))
             for c in C.CANON3}
    with np.errstate(divide="ignore", invalid="ignore"):
        inv = np.where(np.isfinite(po_arr) & (po_arr > 0), 1.0 / po_arr, np.nan)
    pra = P["PR"][i].astype(np.float64)
    po_w, pr_w = _derive(inv, pra, _W_KEYS, _W_IDX)
    po_q, pr_q = _derive(inv, pra, _Q_KEYS, _Q_IDX)
    po_e, pr_e = _derive(inv, pra, _E_KEYS, _E_IDX)
    po_w = {k: v for k, v in po_w.items() if np.isfinite(v) and v > 0}
    po_q = {k: v for k, v in po_q.items() if np.isfinite(v) and v > 0}
    po_e = {k: v for k, v in po_e.items() if np.isfinite(v) and v > 0}
    fin = PERMS[int(a["WIN"][i])]
    r = Row(i=i, win=win, date=str(a["DATE"][i]), type=str(a["TYPE"][i]),
            rtype=str(a["RTYPE"][i]), a1=order[0], a2=order[1], a3=order[2],
            fin=fin, order=list(order), pw_ent=float(shape.pw_ent),
            po_tf=po_tf, pr_tf=pr, po_t3=po_t3, pr_t3=pr_t3,
            win_tf=fin, pay_tf=float(a["PAY"][i]) / 100.0,
            win_t3=frozenset(C.CANON3[int(a["TRIO_WIN"][i])]),
            odds_t3=float(a["TRIO_PAY"][i]),
            po_w=po_w, pr_w=pr_w, po_q=po_q, pr_q=pr_q, po_e=po_e, pr_e=pr_e,
            rw={fp: float(t2["WIDE"][i][j]) for fp, j in PIDX.items()},
            rq={fp: float(t2["QN"][i][j]) for fp, j in PIDX.items()},
            re={o: float(t2["EX"][i][OIDX[o]]) for o in ORD2},
            shape=shape, sign_rt=tuple(SIGNBOARD_RACE_TYPES))
    return r


def iter_rows(win: str):
    """窓ぶんの Row を**1行ずつ**返す（全部メモリに載せると数GBになる）。"""
    z = C.board()
    tp = np.array([str(v) for v in z["TYPE"]])
    for i in C.select(None, win):
        i = int(i)
        if tp[i] not in "ABCDEF":
            continue
        r = make_row(i, win)
        if r is not None:
            r["_trio_ok"] = trio_ok(r) if r["type"] == "A" else False
            yield r


# ══════════════════════════════════════════════════════════════════════════
# 腕
# ══════════════════════════════════════════════════════════════════════════
def _score(stakes, real, wins):
    """(投資, 払戻)。wins は的中キーの集合（ワイドは1レースに3つ当たる）。
    確定オッズが欠けている的中があれば None（採点不能）。"""
    inv = float(sum(stakes.values()))
    pay = 0.0
    for k in stakes:
        if k in wins:
            o = real.get(k)
            if o is None or not np.isfinite(o) or o <= 0:
                return None
            pay += float(stakes[k]) * float(o)
    return inv, pay


def base_arm(r, budget=BUDGET):
    """現行の1商品（`sell_plans_for` 相当）。"""
    key = _plan_for(r["type"], r["rtype"], r["pw_ent"], r["_trio_ok"], r["sign_rt"])
    plan = PLANS[key]
    trio = plan.bet_type == "trio"
    pod, prb = ((r["po_t3"], r["pr_t3"]) if trio else (r["po_tf"], r["pr_tf"]))
    legs = build_legs(r["shape"], plan, pod, prb)
    if not legs:
        return None
    st = allocate(legs, pod, prb, plan, budget=budget)
    if not st:
        return None
    wins = {r["win_t3"]} if trio else {r["win_tf"]}
    real = ({r["win_t3"]: r["odds_t3"]} if trio else {r["win_tf"]: r["pay_tf"]})
    return dict(key=key, stakes=st, odds=pod, real=real, wins=wins)


def trio_ok(r):
    p = PLANS["A_trio"]
    legs = build_legs(r["shape"], p, r["po_t3"], r["pr_t3"])
    if not legs:
        return False
    st = allocate(legs, r["po_t3"], r["pr_t3"], p)
    return bool(st) and gate_ok(st, r["po_t3"])


def wide_wins(r):
    return {frozenset(p) for p in itertools.combinations(r["fin"], 2)}


def two_car_arm(r, kind, legs, budget=BUDGET):
    od = {"w": r["po_w"], "q": r["po_q"], "e": r["po_e"]}[kind]
    rl = {"w": r["rw"], "q": r["rq"], "e": r["re"]}[kind]
    if any(c not in od for c in legs):
        return None
    st = dutch(legs, od, budget=budget)
    if not st:
        return None
    if kind == "w":
        wins = wide_wins(r)
    elif kind == "q":
        wins = {frozenset(r["fin"][:2])}
    else:
        wins = {(r["fin"][0], r["fin"][1])}
    return dict(stakes=st, odds=od, real=rl, wins=wins)


def trio_arm(r, legs, budget=BUDGET):
    if not legs or any(c not in r["po_t3"] for c in legs):
        return None
    st = dutch(legs, r["po_t3"], budget=budget)
    if not st:
        return None
    return dict(stakes=st, odds=r["po_t3"], real={r["win_t3"]: r["odds_t3"]},
                wins={r["win_t3"]})


def _topk(pr, od, k, pool=None):
    cand = [c for c in od if pool is None or c in pool]
    cand.sort(key=lambda c: -pr.get(c, 0.0))
    return cand[:k] if len(cand) >= k else None


def arm(r, name):
    a1, a2, a3 = r["a1"], r["a2"], r["a3"]
    o = r["order"]
    if name == "現行":
        return base_arm(r)
    # ── ワイド ──
    if name == "ワイド 軸2車1点":
        return two_car_arm(r, "w", [frozenset({a1, a2})])
    if name == "ワイド 上位3車BOX3点":
        return two_car_arm(r, "w", [frozenset(p) for p in itertools.combinations(o[:3], 2)])
    if name == "ワイド 軸1流し2点":
        return two_car_arm(r, "w", [frozenset({a1, a2}), frozenset({a1, a3})])
    if name == "ワイド 確率上位3点":
        legs = _topk(r["pr_w"], r["po_w"], 3)
        return two_car_arm(r, "w", legs) if legs else None
    # ── 二車複 ──
    if name == "二車複 軸2車1点":
        return two_car_arm(r, "q", [frozenset({a1, a2})])
    if name == "二車複 上位3車BOX3点":
        return two_car_arm(r, "q", [frozenset(p) for p in itertools.combinations(o[:3], 2)])
    if name == "二車複 確率上位3点":
        legs = _topk(r["pr_q"], r["po_q"], 3)
        return two_car_arm(r, "q", legs) if legs else None
    if name == "二車複 確率上位5点":
        legs = _topk(r["pr_q"], r["po_q"], 5)
        return two_car_arm(r, "q", legs) if legs else None
    # ── 二車単 ──
    if name == "二車単 軸2車2順序":
        return two_car_arm(r, "e", [(a1, a2), (a2, a1)])
    if name == "二車単 確率上位3点":
        legs = _topk(r["pr_e"], r["po_e"], 3)
        return two_car_arm(r, "e", legs) if legs else None
    if name == "二車単 確率上位5点":
        legs = _topk(r["pr_e"], r["po_e"], 5)
        return two_car_arm(r, "e", legs) if legs else None
    if name == "二車単 確率上位8点":
        legs = _topk(r["pr_e"], r["po_e"], 8)
        return two_car_arm(r, "e", legs) if legs else None
    # ── 三連複 ──
    if name.startswith("三連複 軸2車流し"):
        m = int(name.split("し")[1][0])
        legs = [frozenset({a1, a2, c}) for c in o[2:2 + m]]
        return trio_arm(r, legs)
    if name.startswith("三連複 確率上位"):
        k = int(name.replace("三連複 確率上位", "").replace("点", ""))
        legs = _topk(r["pr_t3"], r["po_t3"], k)
        return trio_arm(r, legs) if legs else None
    raise KeyError(name)


MIX = {
    "現行8割+ワイド軸2車2割": ("w", "ワイド 軸2車1点", 0.8),
    "現行8割+二車複軸2車2割": ("q", "二車複 軸2車1点", 0.8),
    "現行7割+二車単軸2車3割": ("e", "二車単 軸2車2順序", 0.7),
    "現行7割+三連複軸2車流し3点3割": ("t3", "三連複 軸2車流し3点", 0.7),
}


def mix_arm(r, name):
    kind, sub, frac = MIX[name]
    b1 = int(BUDGET * frac)
    b2 = BUDGET - b1
    base = base_arm(r, budget=b1)
    if base is None:
        return None
    a1, a2 = r["a1"], r["a2"]
    if kind == "w":
        s = two_car_arm(r, "w", [frozenset({a1, a2})], budget=b2)
    elif kind == "q":
        s = two_car_arm(r, "q", [frozenset({a1, a2})], budget=b2)
    elif kind == "e":
        s = two_car_arm(r, "e", [(a1, a2), (a2, a1)], budget=b2)
    else:
        s = trio_arm(r, [frozenset({a1, a2, c}) for c in r["order"][2:5]], budget=b2)
    if s is None:
        return None
    # 合成（キーが券種で衝突しないよう (券種, キー) にする）
    st, od, rl, wins = {}, {}, {}, set()
    for tag, part in (("base", base), ("sub", s)):
        for k, v in part["stakes"].items():
            st[(tag, k)] = v
            od[(tag, k)] = part["odds"][k]
        for k, v in part["real"].items():
            rl[(tag, k)] = v
        wins |= {(tag, k) for k in part["wins"]}
    return dict(stakes=st, odds=od, real=rl, wins=wins)


# ══════════════════════════════════════════════════════════════════════════
# 集計 — **1レースにつき全腕をまとめて評価する1パス**
#   （腕ごとに走らせ直すと Row の再生成で時間が溶ける／全部メモリに載せると数GB）
# ══════════════════════════════════════════════════════════════════════════
ARMS = [
    "現行",
    "ワイド 軸2車1点", "ワイド 軸1流し2点", "ワイド 上位3車BOX3点", "ワイド 確率上位3点",
    "二車複 軸2車1点", "二車複 上位3車BOX3点", "二車複 確率上位3点", "二車複 確率上位5点",
    "二車単 軸2車2順序", "二車単 確率上位3点", "二車単 確率上位5点", "二車単 確率上位8点",
    "三連複 軸2車流し3点", "三連複 軸2車流し5点", "三連複 確率上位4点", "三連複 確率上位6点",
] + list(MIX)


def eval_race(r, names=ARMS):
    """1レース → {腕: rec or None}。rec は入稿ゲートを通ったものだけ。
    diag には「組めたか/どのゲートで落ちたか」も残す。"""
    out, diag = {}, {}
    for a in names:
        x = mix_arm(r, a) if a in MIX else arm(r, a)
        if x is None:
            diag[a] = "組めない"
            continue
        mn = min(float(x["odds"][c]) for c in x["stakes"])
        mp = sum(x["stakes"][c] * float(x["odds"][c]) for c in x["stakes"]) / len(x["stakes"])
        c1, c2 = mn >= MIN_POINT_ODDS, mp > MIN_MEAN_PAYOUT
        diag[a] = ("通過" if (c1 and c2) else
                   ("安すぎ" if not c1 else "") + ("+" if not c1 and not c2 else "")
                   + ("平均2万以下" if not c2 else ""))
        if not (c1 and c2):
            out[a] = None
            continue
        sc = _score(x["stakes"], x["real"], x["wins"])
        if sc is None:
            out[a] = None
            continue
        inv, pay = sc
        out[a] = dict(i=r["i"], date=r["date"], inv=inv, pay=pay, mean=mp,
                      k=len(x["stakes"]), type=r["type"], key=x.get("key", a),
                      minodds=mn)
    return out, diag


_CACHE: dict = {}


def scan(win: str, names=ARMS):
    """窓を1パスして {腕: [rec]} と診断カウンタを返す（結果はメモリ内キャッシュ）。"""
    ck = (win, tuple(names))
    if ck in _CACHE:
        return _CACHE[ck]
    recs = defaultdict(list)
    diagc = defaultdict(lambda: defaultdict(int))
    built = defaultdict(int)
    dates = set()
    n = 0
    for r in iter_rows(win):
        n += 1
        if n % 4000 == 0:
            print(f"  ... {win} {n:,}R", flush=True, file=sys.stderr)
        dates.add(r["date"])
        o, d = eval_race(r, names)
        for a, v in o.items():
            if v is not None:
                v["rtype"] = r["rtype"]
                recs[a].append(v)
        for a, v in d.items():
            diagc[a][v] += 1
            if v != "組めない":
                built[a] += 1
    res = (dict(recs), dict(diagc), dict(built), len(dates), n)
    _CACHE[ck] = res
    return res


def _stats(rs, nd):
    s = C.summarize(rs, nd)
    if s.get("n"):
        s["perday"] = len(rs) / nd
    return s


WINDOWS = (("explore", "探索 2024-07〜2025-12"), ("confirm", "確認 2026-01〜08"))


def cmd_arms(argv):
    for win, lbl in WINDOWS:
        recs, diagc, built, nd, n = scan(win)
        print(f"\n=== {lbl}  対象 {n:,}R / {nd}日 ===")
        print(C.HEAD)
        for a in ARMS:
            print(C.line(a, _stats(recs.get(a, []), nd)))


def cmd_gatepass(argv):
    """**そもそも売れるか**。組める率と、2つの入稿ゲートのどちらで落ちるか。"""
    for win, lbl in WINDOWS:
        recs, diagc, built, nd, n = scan(win)
        print(f"\n=== 入稿ゲート  {lbl}  n={n:,} ===")
        print(f"  {'腕':30s} {'組める%':>8s} {'通過%':>7s} {'安すぎで落ちる%':>15s} "
              f"{'平均2万以下で落ちる%':>19s} {'件/日':>6s}")
        for a in ARMS:
            d = diagc.get(a, {})
            b = built.get(a, 0)
            if not b:
                continue
            tot = sum(d.values())
            cheap = sum(v for k, v in d.items() if "安すぎ" in k)
            low = sum(v for k, v in d.items() if "平均2万以下" in k)
            print(f"  {a:30s} {b/tot*100:7.1f}% {d.get('通過',0)/b*100:6.1f}% "
                  f"{cheap/b*100:14.1f}% {low/b*100:18.1f}% "
                  f"{len(recs.get(a,[]))/nd:6.2f}")


def cmd_calib(argv):
    """導出した2車券の予測オッズが確定オッズをどれだけ当てているか。"""
    for win, lbl in WINDOWS:
        acc = defaultdict(list)
        for r in iter_rows(win):
            for nm, pk, rk in (("ワイド", "po_w", "rw"), ("二車複", "po_q", "rq"),
                               ("二車単", "po_e", "re")):
                for k, pv in r[pk].items():
                    q = r[rk].get(k)
                    if pv and q and np.isfinite(q) and q > 0:
                        acc[nm].append(math.log(q / pv))
            for nm, pv, q in (("三連複", r["po_t3"].get(r["win_t3"]), r["odds_t3"]),
                              ("三連単", r["po_tf"].get(r["win_tf"]), r["pay_tf"])):
                if pv and q and np.isfinite(q) and q > 0:
                    acc[nm].append(math.log(q / pv))
        print(f"\n=== 予測オッズの較正（三連単板からの導出値 vs 確定）  {lbl} ===")
        print(f"  {'券種':10s} {'n':>10s} {'log比 平均':>11s} {'logMAE':>8s} "
              f"{'確定/予測 中央':>14s}")
        for nm in ("ワイド", "二車複", "二車単", "三連複", "三連単"):
            a = np.array(acc.get(nm, []))
            if not len(a):
                continue
            print(f"  {nm:10s} {len(a):10,} {a.mean():11.4f} {np.abs(a).mean():8.4f} "
                  f"{math.exp(float(np.median(a))):13.3f}")


#: 型 → その型で**実際に売っている商品**と、その商品の仕事（設計意図）。
#: 🔴 外れの評価はここに照らして行う。`_pay`/`_ana`/`_sign` の表示的中が低いのは
#:    失敗ではなく設計どおり（`SIGNBOARD_TYPES` / `ANA_PW_ENT_MIN` の docstring）。
JOB = {
    "A_hit": "当てる", "A_trio": "当てる(順序を捨てる)", "A_ana": "軸1が飛ぶ側を取る",
    "B_hit": "当てる", "C_hit": "当てる(崩れ筋の帯)", "D_hit": "当てる(順序を捨てる)",
    "E_hit": "高い帯で当てる", "F_hit": "当てる", "F_pay": "一撃を取る",
    "F_sign": "看板を作る",
}


def cmd_bytype(argv):
    for win, lbl in WINDOWS:
        recs, diagc, built, nd, n = scan(win)
        base = recs.get("現行", [])
        keys = defaultdict(int)
        for x in base:
            keys[x["key"]] += 1
        for tl in "ABCDEF":
            kk = {k: v for k, v in keys.items() if k.startswith(tl)}
            kd = " / ".join(f"{k}[{JOB.get(k,'?')}] {v}" for k, v in
                            sorted(kk.items(), key=lambda x: -x[1]))
            print(f"\n=== 型{tl}  {lbl}  売る商品: {kd or '—'} ===")
            print(C.HEAD)
            for a in ARMS:
                rs = [x for x in recs.get(a, []) if x["type"] == tl]
                print(C.line(a, _stats(rs, nd)))


def _boot(pairs, n=2000, seed=0):
    rng = np.random.default_rng(seed)
    a = np.array([p[0] for p in pairs], float)
    b = np.array([p[1] for p in pairs], float)
    d = (b - a).mean() * 100
    m = len(a)
    if m < 2:
        return d, float("nan"), float("nan")
    idx = rng.integers(0, m, size=(n, m))
    ds = (b[idx] - a[idx]).mean(axis=1) * 100
    return d, float(np.percentile(ds, 2.5)), float(np.percentile(ds, 97.5))


def cmd_paired(argv):
    """🔴 **同じレースで比べる**（母集団が違う腕を並べると矛盾した結論が出る）。"""
    tl = argv[0] if argv else None
    for win, lbl in WINDOWS:
        recs, diagc, built, nd, n = scan(win)
        brec = {x["i"]: x for x in recs.get("現行", [])
                if tl is None or x["type"] == tl}
        print(f"\n=== 同一レース対応比較  {lbl}"
              + (f"  型{tl}" if tl else "") + " ===")
        print(f"  {'腕':30s} {'重なりR':>7s} {'現行表示的中':>12s} {'腕表示的中':>11s} "
              f"{'Δ':>8s} {'95%CI':>18s} {'現行ROI':>8s} {'腕ROI':>7s} "
              f"{'腕払戻中央':>11s} {'腕10万+/日':>10s}")
        for a in ARMS:
            if a == "現行":
                continue
            arec = {x["i"]: x for x in recs.get(a, [])
                    if tl is None or x["type"] == tl}
            common = [i for i in arec if i in brec]
            if len(common) < 30:
                print(f"  {a:30s} {len(common):7d}  (重なり不足)")
                continue
            pairs = [(1.0 if brec[i]["pay"] >= brec[i]["inv"] else 0.0,
                      1.0 if arec[i]["pay"] >= arec[i]["inv"] else 0.0) for i in common]
            d, lo, hi = _boot(pairs)
            bs = sum(p[0] for p in pairs) / len(pairs) * 100
            as_ = sum(p[1] for p in pairs) / len(pairs) * 100
            bi = sum(brec[i]["inv"] for i in common)
            bp = sum(brec[i]["pay"] for i in common)
            ai = sum(arec[i]["inv"] for i in common)
            ap = sum(arec[i]["pay"] for i in common)
            hp = sorted(arec[i]["pay"] for i in common if arec[i]["pay"] > 0)
            big = sum(1 for i in common if arec[i]["pay"] >= 100_000) / nd
            print(f"  {a:30s} {len(common):7d} {bs:11.2f}% {as_:10.2f}% "
                  f"{d:+8.2f} [{lo:+6.2f},{hi:+6.2f}] {bp/bi*100:7.1f} "
                  f"{ap/ai*100:6.1f} {median(hp) if hp else 0:11,.0f} {big:10.3f}")


def cmd_miss(argv):
    """外れを券種の観点で分解。②③（軸2車は3着以内）を各券種が拾えるか。"""
    for win, lbl in WINDOWS:
        buckets = defaultdict(lambda: defaultdict(int))
        catch = defaultdict(lambda: defaultdict(int))
        for r in iter_rows(win):
            o, _ = eval_race(r, ["現行", "ワイド 軸2車1点", "二車複 軸2車1点",
                                 "二車単 軸2車2順序", "三連複 軸2車流し3点"])
            b = o.get("現行")
            if b is None:
                continue
            top3 = set(r["fin"])
            both = r["a1"] in top3 and r["a2"] in top3
            if b["pay"] > 0:
                lab = "①的中"
            elif not both:
                lab = "④軸崩壊"
            else:
                lab = "②順序違い" if _set_bought(r) else "③相手外し"
            for k in ("all", r["type"]):
                buckets[k][lab] += 1
            if lab in ("②順序違い", "③相手外し"):
                for a in ("ワイド 軸2車1点", "二車複 軸2車1点", "二車単 軸2車2順序",
                          "三連複 軸2車流し3点"):
                    v = o.get(a)
                    if v is None:
                        catch[a]["売れない"] += 1
                    elif v["pay"] >= v["inv"]:
                        catch[a]["表示的中"] += 1
                    elif v["pay"] > 0:
                        catch[a]["ガミ"] += 1
                    else:
                        catch[a]["外れ"] += 1
        labs = ["①的中", "②順序違い", "③相手外し", "④軸崩壊"]
        print(f"\n=== 外れの分解  {lbl} ===")
        print(f"  {'層':6s} {'n':>6s} " + " ".join(f"{l:>10s}" for l in labs))
        for k in ["all"] + list("ABCDEF"):
            d = buckets.get(k)
            if not d:
                continue
            nn = sum(d.values())
            print(f"  {k:6s} {nn:6d} " + " ".join(f"{d[l]/nn*100:9.2f}%" for l in labs))
        print(f"\n  ②③（軸2車は3着以内なのに外した層）を券種が拾えるか  n="
              f"{buckets['all']['②順序違い']+buckets['all']['③相手外し']}")
        print(f"  {'券種(軸2車)':22s} {'売れない%':>9s} {'表示的中%':>9s} {'ガミ%':>7s} {'外れ%':>7s}")
        for a, d in catch.items():
            t = sum(d.values())
            print(f"  {a:22s} {d['売れない']/t*100:8.1f}% {d['表示的中']/t*100:8.1f}% "
                  f"{d['ガミ']/t*100:6.1f}% {d['外れ']/t*100:6.1f}%")


def _set_bought(r) -> bool:
    key = _plan_for(r["type"], r["rtype"], r["pw_ent"], r["_trio_ok"], r["sign_rt"])
    plan = PLANS[key]
    trio = plan.bet_type == "trio"
    pod, prb = ((r["po_t3"], r["pr_t3"]) if trio else (r["po_tf"], r["pr_tf"]))
    legs = build_legs(r["shape"], plan, pod, prb)
    if not legs:
        return False
    want = frozenset(r["fin"])
    return any(frozenset(c) == want for c in legs)


CMDS = dict(arms=cmd_arms, gatepass=cmd_gatepass, calib=cmd_calib,
            bytype=cmd_bytype, paired=cmd_paired, miss=cmd_miss)



def cmd_plan(argv):
    """🔴 **商品の仕事（設計意図）に照らして読むための表**。
    `_hit` は表示的中、`_pay`/`_ana`/`_sign` は払戻中央・10万+ が仕事。"""
    arms = argv or ["現行", "ワイド 軸2車1点", "二車複 軸2車1点", "二車単 軸2車2順序",
                    "二車単 確率上位3点", "三連複 軸2車流し3点", "三連複 確率上位4点",
                    "現行8割+二車複軸2車2割", "現行7割+二車単軸2車3割"]
    for win, lbl in WINDOWS:
        recs, diagc, built, nd, n = scan(win)
        base = recs.get("現行", [])
        pk = {x["i"]: x["key"] for x in base}
        print(f"\n=== 商品別（現行が売っているレースに限定）  {lbl} ===")
        for key in sorted(set(pk.values())):
            ids = {i for i, k in pk.items() if k == key}
            print(f"\n  【{key}】仕事={JOB.get(key,'?')}  n={len(ids)}")
            print(f"    {'腕':30s} {'売れる件/日':>10s} {'的中%':>7s} {'ガミ%':>6s} "
                  f"{'表示的中%':>9s} {'払戻中央':>9s} {'10万+/日':>9s} {'ROI%':>6s}")
            for a in arms:
                rs = [x for x in recs.get(a, []) if x["i"] in ids]
                if not rs:
                    print(f"    {a:30s}  (該当なし)")
                    continue
                s = C.summarize(rs, nd)
                print(f"    {a:30s} {len(rs)/nd:10.2f} {s['hit']:7.2f} {s['gami']:6.2f} "
                      f"{s['shown']:9.2f} {s['med_pay']:9,.0f} {s['big_per_day']:9.3f} "
                      f"{s['roi']:6.1f}")


CMDS["plan"] = cmd_plan


def cmd_jobmiss(argv):
    """🔴 **外れを「その商品の仕事」に照らして分解する**（フラットな表示的中では読めない）。

    従来の ①的中 ②順序違い ③相手外し ④軸崩壊 に、**⑤帯外し**を足す。
    `C_hit`(15倍+) / `E_hit`(30倍+) / `F_hit`(5倍+) は**安い決着を意図的に買っていない**ので、
    そこで外すのは失敗ではなく設計どおり。`_sign`/`_ana` は的中しないこと自体が設計なので
    別立てで「10万+ を作れたか」を見る。
    """
    for win, lbl in WINDOWS:
        agg = defaultdict(lambda: defaultdict(int))
        big = defaultdict(lambda: [0, 0])
        for r in iter_rows(win):
            o, _ = eval_race(r, ["現行"])
            b = o.get("現行")
            if b is None:
                continue
            key = b["key"]
            plan = PLANS[key]
            trio = plan.bet_type == "trio"
            pod = r["po_t3"] if trio else r["po_tf"]
            wk = r["win_t3"] if trio else r["win_tf"]
            top3 = set(r["fin"])
            both = r["a1"] in top3 and r["a2"] in top3
            wo = pod.get(wk)
            if b["pay"] > 0:
                lab = "①的中"
            elif plan.min_odds and wo is not None and wo < plan.min_odds:
                lab = "⑤帯外し(設計どおり)"
            elif not both:
                lab = "④軸崩壊"
            elif _set_bought(r):
                lab = "②順序違い"
            else:
                lab = "③相手外し"
            agg[key][lab] += 1
            big[key][0] += b["pay"] >= 100_000
            big[key][1] += 1
        labs = ["①的中", "②順序違い", "③相手外し", "④軸崩壊", "⑤帯外し(設計どおり)"]
        print(f"\n=== 商品の仕事に照らした外れの分解  {lbl} ===")
        print(f"  {'商品':8s} {'仕事':16s} {'n':>5s} "
              + " ".join(f"{l:>16s}" for l in labs) + f" {'10万+率':>8s}")
        for key in sorted(agg):
            d = agg[key]
            n = sum(d.values())
            print(f"  {key:8s} {JOB.get(key,'?'):16s} {n:5d} "
                  + " ".join(f"{d[l]/n*100:15.2f}%" for l in labs)
                  + f" {big[key][0]/max(big[key][1],1)*100:7.2f}%")


CMDS["jobmiss"] = cmd_jobmiss


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "arms"
    if cmd == "build":
        build_pr()
    else:
        CMDS[cmd](sys.argv[2:])
