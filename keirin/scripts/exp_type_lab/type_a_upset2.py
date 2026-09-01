#!/usr/bin/env python3
"""型A の「波乱」を商品にできるか — 検出と買い方を分けて測る（2026-08-31）。

## 前提（HANDOFF_2026-08-31 の再検査で分かったこと）

1. 型A の確定三連単は 30倍+ が 35.2%・100倍+ が 16.2%（確認窓）。
2. **その波乱の中身は帯で全く違う**（`/tmp/reach.py` の分解・両窓で一致）:

       帯        割合   軸1が1着  {軸1,軸2}⊂3着内   軸崩壊
       <10倍    40.8%    71.2%        96.3%       0.0%
       10-30倍  24.0%    38.2%        81.9%       2.5%
       30-100倍 19.0%    11.1%        53.7%      13.1%
       100倍+   16.2%     0.6%        19.7%      40.5%

   ＝ **一番おいしい 100倍+ の 4割は軸1が3着にも入らない**。軸2車を前提にした
   商品では構造的に取れない（[[keirin_type_lab_outcome_matrix_2026_08_27]] の
   「軸崩壊で全プラン的中0%」と同じ）。

3. したがって測るべき目的は「荒れるか（U30）」ではなく
   **「軸で届く荒れか（T = {軸1,軸2}⊂3着内 ∧ 30倍+）」**。

## この台でやること

  ① 本番と同じ母集団（軸信頼ゲート）＋**本番と同じ入稿ゲート**まで通す
     🔴 HANDOFF の §2/§3 は入稿ゲート前で測っている。型A はゲートで 15% 落ちるが、
        落ちる側は素の的中 60.8%・表示的中 54.1% と**一番良い側**なので、
        ゲート前の数字は売っている商品の姿ではない。
  ② 買い方を替える（軸2車の順序を全部買う／三連複へ逃がす／確率上位）
  ③ レースを選ぶ（axis_sum 低位・pw_ent 上位）— **必ず同じ件数の無作為対照つき**
     （[[keirin_type_lab_race_filter_rejected_2026_08_27]]: 件数を減らす検証で
       無作為対照を置かないと、CI が広がっただけの上振れを効果と誤読する）

## 台と窓

  予測オッズは **2025 = vintage 板 / 2026 = 本番板**（本番モデルの train_end は
  2025-12-31 なので、2025 に本番板を使うと in-sample）。
  板は 2026-08-04 までなので確認窓はそこで切れる。

## 台の作り方

    # 本番オッズモデルの板（2026 用）
    .venv/bin/python scripts/exp_tf20/build_board.py        # → /tmp/tf20_board.npz
    mv /tmp/tf20_board.npz /tmp/tf20_board_prod.npz
    # vintage オッズモデルの板（2025 用・本番の train_end は 2025-12-31 なので必須）
    sed 's#/tmp/tf20_board.npz#/tmp/tf20_board_vint.npz#' scripts/exp_tf20/build_board.py \
        > scripts/exp_tf20/_vint.py
    KEIRIN_ODDS_TF_MODEL_DIR=$PWD/data/models_vintage/odds_tf_te20241231 \
        .venv/bin/python scripts/exp_tf20/_vint.py && rm scripts/exp_tf20/_vint.py

    PYTHONPATH=. .venv/bin/python scripts/exp_type_lab/type_a_upset2.py
"""
from __future__ import annotations

import importlib.util
import itertools
import json
import math
import random
import re
import sys
from collections import defaultdict
from pathlib import Path
from statistics import median

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from src.database import get_connection                            # noqa: E402
from src.marquee import is_fill_target                             # noqa: E402
from src.stake_allocation import MIN_MEAN_PAYOUT, MIN_POINT_ODDS   # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "gate", REPO.parent / "backend/src/services/keirin_type_lab_gate.py")
_GATE = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_GATE)                                    # type: ignore

CANON = list(itertools.permutations(range(1, 8), 3))
CIDX = {c: i for i, c in enumerate(CANON)}
BUDGET, UNIT = 10_000, 100
WINDOWS = {"探索 2025": ("2025-01-01", "2025-12-31"),
           "確認 2026": ("2026-01-01", "2026-08-04")}


# ───────────────────────── 台 ─────────────────────────

def load():
    # 🔴 NpzFile の添字アクセスは**毎回全展開**する。必ず先に実体化すること
    #    （[[keirin_type_lab_shipped_2026_08_27]] の落とし穴）。
    boards = {}
    bidx = {}
    for k in ("prod", "vint"):
        z = np.load(f"/tmp/tf20_board_{k}.npz", allow_pickle=True)
        boards[k] = {n: z[n] for n in ("PROB", "PO", "PAY", "KEY")}
        bidx[k] = {str(x): i for i, x in enumerate(boards[k]["KEY"])}

    with get_connection() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT race_key, race_date, race_type, axis_sum, arare, gap, p3_order, "
            "       win_combo, win_tf_odds, legs, pred_mean_payout, payout "
            "FROM type_lab_picks WHERE mode='paper' AND plan_key='A_hit' "
            "  AND settled_at IS NOT NULL AND n_entries=7 AND p3_order IS NOT NULL "
            "  AND win_combo IS NOT NULL")]
        rows = [d for d in rows
                if is_fill_target(d.get("race_type"), None)
                or _GATE.passes_axis_gate(
                    "A_hit", float(d["axis_sum"]) if d["axis_sum"] is not None else None, 7)]
        keys = sorted({d["race_key"] for d in rows})
        trio: dict = defaultdict(dict)
        ent: dict = defaultdict(dict)
        for i in range(0, len(keys), 400):
            ch = keys[i:i + 400]
            ph = ",".join("?" * len(ch))
            for r in c.execute(f"SELECT race_key, combination, odds_value FROM wt_odds "
                               f"WHERE bet_type='trio' AND race_key IN ({ph})", tuple(ch)):
                d = dict(r)
                trio[d["race_key"]][frozenset(
                    int(x) for x in re.findall(r"\d+", d["combination"]))] = \
                    float(d["odds_value"])
            for r in c.execute(f"SELECT race_key, frame_no, pred_win_pct FROM wt_entries "
                               f"WHERE race_key IN ({ph})", tuple(ch)):
                d = dict(r)
                if d["pred_win_pct"] is not None:
                    ent[d["race_key"]][int(d["frame_no"])] = float(d["pred_win_pct"])

    out = []
    for d in rows:
        date = str(d["race_date"])
        bk = "vint" if date <= "2025-12-31" else "prod"
        i = bidx[bk].get(d["race_key"])
        if i is None:
            continue
        o = [int(x) for x in str(d["p3_order"]).replace(",", "-").split("-") if x]
        f = [int(x) for x in str(d["win_combo"]).split("-")]
        if len(o) != 7 or len(f) != 3:
            continue
        pw = ent.get(d["race_key"], {})
        if len(pw) != 7:
            continue
        s = sum(pw.values()) or 1.0
        legs = d["legs"] if not isinstance(d["legs"], str) else json.loads(d["legs"])
        out.append(dict(
            key=d["race_key"], date=date, o=o, f=tuple(f),
            odds=float(d["win_tf_odds"]), axis_sum=float(d["axis_sum"] or 0),
            arare=int(d["arare"] or 0), gap=float(d["gap"] or 0),
            rtype=str(d["race_type"] or ""),
            pw_ent=-sum((v / s) * math.log(v / s + 1e-12) for v in pw.values()),
            PROB=boards[bk]["PROB"][i], PO=boards[bk]["PO"][i],
            PAY=float(boards[bk]["PAY"][i]),
            trio_final=trio.get(d["race_key"], {}),
            prod_legs=legs, prod_mean=float(d["pred_mean_payout"] or 0),
            prod_pay=int(d["payout"] or 0),
        ))
    return out


# ───────────────────────── 買い方 ─────────────────────────

def _tf_po(d, t):
    return float(d["PO"][CIDX[t]])


def _trio_po(d, s):
    inv = sum(1.0 / _tf_po(d, p) for p in itertools.permutations(sorted(s))
              if _tf_po(d, p) > 0)
    return 1.0 / inv if inv > 0 else 0.0


ARMS: dict = {}


def arm(name):
    def deco(fn):
        ARMS[name] = fn
        return fn
    return deco


@arm("A_hit 現行3点")
def _a_hit(d):
    a, b = d["o"][0], d["o"][1]
    return "tf", [(a, b, d["o"][k]) for k in (2, 3, 4)]


@arm("A_pay 6点")
def _a_pay(d):
    a = d["o"][0]
    return "tf", [(a, s, t) for s in (d["o"][1], d["o"][2])
                  for t in (d["o"][3], d["o"][4], d["o"][5]) if t != s]


@arm("軸2車+相手2車 6順列(12点)")
def _all6_2(d):
    a, b = d["o"][0], d["o"][1]
    return "tf", [p for k in (2, 3)
                  for p in itertools.permutations((a, b, d["o"][k]))]


@arm("軸2車+相手3車 6順列(18点)")
def _all6_3(d):
    a, b = d["o"][0], d["o"][1]
    return "tf", [p for k in (2, 3, 4)
                  for p in itertools.permutations((a, b, d["o"][k]))]


@arm("三連複 軸2車流し3点")
def _trio3(d):
    a, b = d["o"][0], d["o"][1]
    return "trio", [frozenset((a, b, d["o"][k])) for k in (2, 3, 4)]


@arm("三連複 軸2車流し5点")
def _trio5(d):
    a, b = d["o"][0], d["o"][1]
    return "trio", [frozenset((a, b, d["o"][k])) for k in (2, 3, 4, 5, 6)]


@arm("確率上位5点")
def _p5(d):
    idx = np.argsort(-d["PROB"])[:5]
    return "tf", [CANON[i] for i in idx]


@arm("確率上位10点")
def _p10(d):
    idx = np.argsort(-d["PROB"])[:10]
    return "tf", [CANON[i] for i in idx]


def play(d, name):
    """腕を1レースに当てる。入稿ゲートに落ちたら None。"""
    kind, combos = ARMS[name](d)
    combos = list(dict.fromkeys(combos))
    if not combos:
        return None
    po = [( _tf_po(d, c) if kind == "tf" else _trio_po(d, c)) for c in combos]
    if any(x <= 0 for x in po) or len(combos) * UNIT > BUDGET:
        return None
    # ダッチ（∝ 1/予測オッズ）
    w = [1.0 / x for x in po]
    n_units = BUDGET // UNIT
    units = [1] * len(combos)
    rest = n_units - len(combos)
    tot = sum(w)
    for j, x in enumerate(w):
        units[j] += int(rest * x / tot)
    while sum(units) < n_units:
        j = min(range(len(units)), key=lambda k: units[k] / max(w[k], 1e-12))
        units[j] += 1
    stakes = [u * UNIT for u in units]
    # 入稿ゲート（本番と同じ2つ）
    if min(po) < MIN_POINT_ODDS:
        return None
    if sum(s * o for s, o in zip(stakes, po)) / len(combos) <= MIN_MEAN_PAYOUT:
        return None
    inv = sum(stakes)
    pay = 0.0
    if kind == "tf":
        w_ = d["f"]
        if w_ in combos:
            pay = stakes[combos.index(w_)] / 100.0 * d["PAY"]
    else:
        w_ = frozenset(d["f"])
        if w_ in combos:
            fo = d["trio_final"].get(w_)
            if fo is None:
                return None
            pay = stakes[combos.index(w_)] * fo
    return dict(date=d["date"], inv=inv, pay=pay, k=len(combos))


# ───────────────────────── 集計 ─────────────────────────

def boot_roi(recs, n=1500, seed=0):
    rnd = random.Random(seed)
    m = len(recs)
    inv = [r["inv"] for r in recs]
    pay = [r["pay"] for r in recs]
    v = []
    for _ in range(n):
        a = b = 0.0
        for _ in range(m):
            j = rnd.randrange(m)
            a += inv[j]
            b += pay[j]
        v.append(b / a * 100 if a else 0.0)
    v.sort()
    return v[int(n * .025)], v[int(n * .975)]


def summ(recs, nd_all=None, ci=True):
    if not recs:
        return None
    nd = nd_all or len({r["date"] for r in recs})
    inv = sum(r["inv"] for r in recs)
    pay = sum(r["pay"] for r in recs)
    hits = [r for r in recs if r["pay"] > 0]
    shown = [r for r in hits if r["pay"] > r["inv"]]
    ps = sorted(r["pay"] for r in hits)
    s = dict(n=len(recs), perday=len(recs) / nd, k=sum(r["k"] for r in recs) / len(recs),
             hit=len(hits) / len(recs) * 100, shown=len(shown) / len(recs) * 100,
             med=median(ps) if ps else 0.0, roi=pay / inv * 100 if inv else 0.0,
             big=sum(1 for x in ps if x >= 100_000) / nd)
    if ci:
        s["lo"], s["hi"] = boot_roi(recs)
    return s


HDR = ("    {:<26}{:>6}{:>5}{:>9}{:>9}{:>10}{:>19}{:>9}"
       .format("腕", "件/日", "点", "素の的中", "表示的中", "払戻中央", "ROI(CI95)", "10万+/日"))


def row(name, s):
    if not s:
        return f"    {name:<26}  (該当なし)"
    ci = f"{s['roi']:.1f}[{s['lo']:.0f},{s['hi']:.0f}]" if "lo" in s else f"{s['roi']:.1f}"
    return (f"    {name:<26}{s['perday']:>6.2f}{s['k']:>5.0f}{s['hit']:>8.2f}%"
            f"{s['shown']:>8.2f}%{s['med']:>10,.0f}{ci:>19}{s['big']:>9.3f}")


def main() -> int:
    data = load()
    print(f"台 {len(data):,}R  {min(d['date'] for d in data)}〜{max(d['date'] for d in data)}")

    # 探索窓で選別の境界を決める（確認窓に窓内分位を当てない）
    ex = [d for d in data if WINDOWS["探索 2025"][0] <= d["date"] <= WINDOWS["探索 2025"][1]]
    ax_lo = sorted(d["axis_sum"] for d in ex)[len(ex) // 3]
    pe_hi = sorted(d["pw_ent"] for d in ex)[len(ex) * 2 // 3]
    print(f"選別の境界（探索窓の分位・確認窓へそのまま当てる）: "
          f"axis_sum < {ax_lo:.4f} / pw_ent > {pe_hi:.4f}")

    for win, (lo, hi) in WINDOWS.items():
        rs = [d for d in data if lo <= d["date"] <= hi]
        nd = len({d["date"] for d in rs})
        print(f"\n{'='*118}\n=== {win}  型A {len(rs):,}R / {nd}日 "
              f"（入稿ゲートは腕ごとに掛け直す・配分はダッチで統一）===")
        print(HDR)
        for name in ARMS:
            recs = [r for r in (play(d, name) for d in rs) if r]
            print(row(name, summ(recs, nd)))

        sels = {
            "axis_sum 低1/3": lambda d: d["axis_sum"] < ax_lo,
            "pw_ent 上位1/3": lambda d: d["pw_ent"] > pe_hi,
        }
        for sname, fn in sels.items():
            sub = [d for d in rs if fn(d)]
            print(f"\n  ── 選別「{sname}」 {len(sub):,}R ({len(sub)/len(rs):.0%}) ──")
            print(HDR)
            for name in ("A_hit 現行3点", "A_pay 6点", "軸2車+相手2車 6順列(12点)",
                         "三連複 軸2車流し5点", "確率上位10点"):
                recs = [r for r in (play(d, name) for d in sub) if r]
                print(row(name, summ(recs, nd)))
            # 無作為対照（同じ件数を20本）
            print("    ── 無作為に同数を取った対照 20本（ROI の分布）──")
            for name in ("A_hit 現行3点", "軸2車+相手2車 6順列(12点)"):
                rois = []
                for sd in range(20):
                    rnd = random.Random(sd)
                    pick = rnd.sample(rs, len(sub))
                    recs = [r for r in (play(d, name) for d in pick) if r]
                    s = summ(recs, nd, ci=False)
                    if s:
                        rois.append(s["roi"])
                rois.sort()
                real = summ([r for r in (play(d, name) for d in sub) if r], nd, ci=False)
                beat = sum(1 for x in rois if real["roi"] > x)
                print(f"      {name:<26} 対照 中央 {rois[10]:.1f}% "
                      f"[{rois[0]:.1f},{rois[-1]:.1f}]   選別 {real['roi']:.1f}%  "
                      f"→ 対照 {beat}/20 に勝ち")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
