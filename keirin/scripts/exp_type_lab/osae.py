#!/usr/bin/env python3
"""押さえ目（軸2車1-2着 ∧ 3着人気薄）の検証（2026-09-10）。台は `osae_build.py`。

  PYTHONPATH=. .venv/bin/python scripts/exp_type_lab/osae.py <cmd>
"""
from __future__ import annotations

import pickle
import sys
from collections import defaultdict
from statistics import median

import numpy as np

ROWS = pickle.load(open("/tmp/osae_rows.pkl", "rb"))
WINS = (("探索 2024-07〜2025-12", "explore"), ("確認 2026-01〜08", "confirm"))
NDAYS = {w: len({r["date"] for r in ROWS if r["win"] == w})
         for w in ("explore", "confirm")}


def summ(recs, nd):
    if not recs:
        return dict(n=0)
    inv = sum(r["inv"] for r in recs)
    pay = sum(r["pay"] for r in recs)
    hits = [r for r in recs if r["pay"] > 0]
    gami = [r for r in hits if r["pay"] < r["inv"]]
    pays = sorted(r["pay"] for r in hits)
    return dict(n=len(recs), perday=len(recs) / nd,
                k=sum(len(r["stakes"]) for r in recs) / len(recs),
                hit=len(hits) / len(recs) * 100,
                gami=len(gami) / len(hits) * 100 if hits else 0.0,
                shown=(len(hits) - len(gami)) / len(recs) * 100,
                med_pay=median(pays) if pays else 0.0,
                med_mean=median(sorted(r["mean"] for r in recs)),
                big=sum(1 for p in pays if p >= 100_000) / nd,
                roi=pay / inv * 100 if inv else 0.0)


HEAD = ("  {:30s} {:>6s} {:>5s} {:>7s} {:>6s} {:>9s} {:>9s} {:>10s} {:>8s} {:>7s}"
        .format("腕", "件/日", "点数", "的中%", "ガミ%", "表示的中%", "払戻中央",
                "平均払戻中央", "10万+/日", "ROI%"))


def line(name, s):
    if not s.get("n"):
        return f"  {name:30s}  (該当なし)"
    return (f"  {name:30s} {s['perday']:6.2f} {s['k']:5.2f} {s['hit']:7.2f} "
            f"{s['gami']:6.2f} {s['shown']:9.2f} {s['med_pay']:9,.0f} "
            f"{s['med_mean']:10,.0f} {s['big']:8.3f} {s['roi']:7.1f}")


def base_cmd() -> None:
    """台の健全性チェック（DESIGN.md 2.1 の表と突き合わせる）。"""
    for label, w in WINS:
        rr = [r for r in ROWS if r["win"] == w]
        nd = NDAYS[w]
        print(f"\n=== {label}  n={len(rr):,}  日数={nd} ===")
        print(HEAD)
        print(line("全商品", summ(rr, nd)))
        hitp = {"A_hit", "A_trio", "B_hit", "C_hit", "D_hit", "E_hit", "F_hit"}
        print(line("当てにいく商品", summ([r for r in rr if r["plan"] in hitp], nd)))
        print(line("一撃商品", summ([r for r in rr if r["plan"] not in hitp], nd)))
        for p in sorted({r["plan"] for r in rr}):
            print(line(f"  {p}", summ([r for r in rr if r["plan"] == p], nd)))




# ═══════════════════════════════════════════════════════════════════════════
# A. 成立条件 — 「軸2車が1-2着」の3着を、どう絞れば 100倍超が増えるか
#
# 🔴 100円の押さえが**表示的中（払戻>賭け金）**になるのは 確定オッズ > 100倍 のときだけ。
#    それ未満で当たるのはガミ＝ユーザーが明確に否定した状態。
# ═══════════════════════════════════════════════════════════════════════════

_PO = None


def PO():
    """予測オッズ 210列。🔴 NpzFile の添字アクセスは毎回全展開するので実体化する。"""
    global _PO
    if _PO is None:
        _PO = np.load("/tmp/race_type_board.npz", allow_pickle=True)["PO"]
    return _PO


import itertools  # noqa: E402
PERMS = list(itertools.permutations(range(1, 8), 3))
CIDX = {c: i for i, c in enumerate(PERMS)}


def po_of(r, leg):
    v = float(PO()[r["i"]][CIDX[tuple(leg)]])
    return v if np.isfinite(v) and v > 0 else None


def _solo(r, c):
    return str(r["lg"][c]) in ("", "0") or int(r["lsz"][c]) <= 1


def _otherline(r, c):
    g = str(r["lg"][c])
    if g in ("", "0"):
        return True
    return g != str(r["lg"][r["a1"]]) and g != str(r["lg"][r["a2"]])


#: 3着の絞り方。**order[2:]（指数3位以下）から候補を返す。**
FILTERS = {
    "指数3位以下(絞りなし)": lambda r: list(r["order"][2:]),
    "指数4位以下":          lambda r: list(r["order"][3:]),
    "指数5位以下":          lambda r: list(r["order"][4:]),
    "指数6位以下":          lambda r: list(r["order"][5:]),
    "指数7位のみ":          lambda r: list(r["order"][6:]),
    "無印(mark=0)":         lambda r: [c for c in r["order"][2:] if r["mk"][c] == 0],
    "◎○△以外":            lambda r: [c for c in r["order"][2:] if r["mk"][c] not in (1, 2, 3)],
    "p3<20%":              lambda r: [c for c in r["order"][2:] if r["p3"][c] < 0.20],
    "p3<15%":              lambda r: [c for c in r["order"][2:] if r["p3"][c] < 0.15],
    "別ライン":             lambda r: [c for c in r["order"][2:] if _otherline(r, c)],
    "別ライン3番手+/単騎":   lambda r: [c for c in r["order"][2:]
                                   if _otherline(r, c) and (int(r["lp"][c]) >= 3 or _solo(r, c))],
    "単騎":                lambda r: [c for c in r["order"][2:] if _solo(r, c)],
    "指数5位以下∧無印":     lambda r: [c for c in r["order"][4:] if r["mk"][c] == 0],
    "指数5位以下∧別ライン":  lambda r: [c for c in r["order"][4:] if _otherline(r, c)],
    "無印∧別ライン":        lambda r: [c for c in r["order"][2:]
                                   if r["mk"][c] == 0 and _otherline(r, c)],
    "指数6位以下∧無印":     lambda r: [c for c in r["order"][5:] if r["mk"][c] == 0],
}


def pick(r, f, how="lowp3"):
    """押さえる相手 X を1車選ぶ。無ければ None。"""
    cs = FILTERS[f](r)
    if not cs:
        return None
    if how == "lowp3":
        return min(cs, key=lambda c: (r["p3"][c], c))
    # 予測オッズが最も高い（=最も人気薄の目になる）相手
    best, bo = None, -1.0
    for c in cs:
        o = po_of(r, (r["a1"], r["a2"], c))
        if o and o > bo:
            best, bo = c, o
    return best


def cond_cmd() -> None:
    """親が出した『3着の指数順位』の表を台の上で再現し、絞り方を掃引する。"""
    for label, w in WINS:
        rr = [r for r in ROWS if r["win"] == w and not r["trio"]]
        nd = NDAYS[w]
        both = [r for r in rr if {r["fin"][0], r["fin"][1]} == {r["a1"], r["a2"]}]
        print(f"\n=== {label}  三連単を売る商品 n={len(rr):,} / "
              f"うち軸2車が1-2着 n={len(both):,} ({len(both)/len(rr)*100:.2f}%) ===")
        print(f"  {'3着の指数順位':16s} {'n':>6s} {'件/日':>6s} {'現行で的中':>9s} "
              f"{'確定中央':>8s} {'100倍超':>8s} {'50倍超':>8s}")
        for lo in (2, 3, 4, 5, 6):
            sel = [r for r in both
                   if r["order"].index(r["fin"][2]) >= lo]
            if not sel:
                continue
            o = sorted(r["pay_tf"] for r in sel)
            hit = sum(1 for r in sel if r["pay"] > 0) / len(sel) * 100
            print(f"  {lo+1}位以下{'':9s} {len(sel):6d} {len(sel)/nd:6.2f} {hit:8.1f}% "
                  f"{median(o):8.1f} {sum(1 for x in o if x > 100)/len(o)*100:7.1f}% "
                  f"{sum(1 for x in o if x > 50)/len(o)*100:7.1f}%")


def sweep_cmd() -> None:
    """絞り方 × 押さえ1点の成立条件。**分母は商品を売る全レース**（押さえは毎回買う）。"""
    how = sys.argv[2] if len(sys.argv) > 2 else "lowp3"
    for label, w in WINS:
        rr = [r for r in ROWS if r["win"] == w and not r["trio"]]
        nd = NDAYS[w]
        print(f"\n=== {label}  三連単を売る商品 n={len(rr):,}  相手の選び方={how} ===")
        print(f"  {'3着の絞り方':22s} {'候補有%':>7s} {'既買%':>6s} {'的中/日':>7s} "
                f"{'的中%':>7s} {'確定中央':>8s} {'100倍超%':>8s} {'救済/日':>7s} "
                f"{'押さえROI%':>9s} {'予測中央':>8s}")
        for f in FILTERS:
            n_have = n_bought = 0
            hits, resc, po_all = [], 0, []
            for r in rr:
                x = pick(r, f, how)
                if x is None:
                    continue
                leg = (r["a1"], r["a2"], x)
                leg2 = (r["a2"], r["a1"], x)
                o = po_of(r, leg)
                if o is None:
                    continue
                n_have += 1
                po_all.append(o)
                if leg in r["stakes"] and leg2 in r["stakes"]:
                    n_bought += 1
                if r["fin"] in (leg, leg2):
                    hits.append(r)
                    if r["pay_tf"] > 100 and r["pay"] < r["inv"]:
                        resc += 1
            if not n_have:
                continue
            pays = sorted(r["pay_tf"] for r in hits)
            roi = sum(pays) / n_have * 100 if n_have else 0.0
            print(f"  {f:22s} {n_have/len(rr)*100:6.1f}% {n_bought/n_have*100:5.1f}% "
                  f"{len(hits)/nd:7.3f} {len(hits)/n_have*100:6.2f}% "
                  f"{median(pays) if pays else 0:8.1f} "
                  f"{(sum(1 for x in pays if x > 100)/len(pays)*100 if pays else 0):7.1f}% "
                  f"{resc/nd:7.3f} {roi:8.1f}% {median(po_all):8.1f}")




# ═══════════════════════════════════════════════════════════════════════════
# A2. 1点（片方向）と2点（両方向）を**分けて**測る
#
# 🔴 前段の `sweep` は「どちらの並びでも当たり」で数えながら投資を1点分で割っていた。
#    両方向を買うなら投資は2倍。ここで分ける。
# ═══════════════════════════════════════════════════════════════════════════

def _dir1(r, x):
    """確率の高い側の並びを1つだけ返す。"""
    a, b = r["a1"], r["a2"]
    p1 = r["probs"].get((a, b, x), 0.0)
    p2 = r["probs"].get((b, a, x), 0.0)
    # 買い目に無い並びは probs に無いので、モデル確率は板から取らず p3 順で決める
    #（a1 は 3着内率1位＝1着に置く方が確率が高い。実測でも確認する）
    return (a, b, x) if p1 >= p2 else (b, a, x)


def boot_roi(vals, n, B=2000, seed=0):
    """押さえ1点の ROI（%）の 95%CI。vals=当たった目の確定オッズ・n=買った回数。"""
    rng = np.random.default_rng(seed)
    arr = np.zeros(n)
    arr[:len(vals)] = vals
    out = rng.choice(arr, size=(B, n), replace=True).mean(axis=1) * 100
    return float(np.percentile(out, 2.5)), float(np.percentile(out, 97.5))


def sweep2_cmd() -> None:
    for label, w in WINS:
        rr = [r for r in ROWS if r["win"] == w and not r["trio"]]
        nd = NDAYS[w]
        print(f"\n=== {label}  三連単を売る商品 n={len(rr):,} ===")
        print(f"  {'3着の絞り方':22s} {'向き':>4s} {'候補有%':>7s} {'的中/日':>7s} {'的中%':>7s} "
              f"{'確定中央':>8s} {'100倍超%':>8s} {'救済/日':>7s} {'押さえROI%':>10s} "
              f"{'ROI 95%CI':>18s}")
        for f in FILTERS:
            for nd_ in (1, 2):
                n_have, hits, resc = 0, [], 0
                for r in rr:
                    x = pick(r, f, "lowp3")
                    if x is None:
                        continue
                    d1 = _dir1(r, x)
                    d2 = (d1[1], d1[0], d1[2])
                    legs = [d1] if nd_ == 1 else [d1, d2]
                    if any(po_of(r, L) is None for L in legs):
                        continue
                    n_have += 1
                    if r["fin"] in legs:
                        hits.append(r["pay_tf"])
                        if r["pay_tf"] > 100 and r["pay"] < r["inv"]:
                            resc += 1
                if not n_have:
                    continue
                roi = sum(hits) / (n_have * nd_) * 100
                lo, hi = boot_roi([v / nd_ for v in hits], n_have)
                print(f"  {f:22s} {nd_:>3d}点 {n_have/len(rr)*100:6.1f}% "
                      f"{len(hits)/nd:7.3f} {len(hits)/n_have*100:6.2f}% "
                      f"{median(hits) if hits else 0:8.1f} "
                      f"{(sum(1 for x in hits if x > 100)/len(hits)*100 if hits else 0):7.1f}% "
                      f"{resc/nd:7.3f} {roi:9.1f}% [{lo:7.1f},{hi:7.1f}]")


def calib_cmd() -> None:
    """🔴 予測オッズで『100倍超』を事前に判定できるか（実装可否を決める）。

    分母は「押さえ候補として買った点」、分子は「当たったときの確定オッズ」。
    """
    for label, w in WINS:
        rr = [r for r in ROWS if r["win"] == w and not r["trio"]]
        print(f"\n=== {label}  押さえ候補（軸2車 → 指数最下位車・両方向）の較正 ===")
        bands = [(0, 30), (30, 50), (50, 75), (75, 100), (100, 150),
                 (150, 250), (250, 1e9)]
        agg = defaultdict(lambda: dict(n=0, hit=0, over=0, pays=[]))
        for r in rr:
            x = pick(r, "指数3位以下(絞りなし)", "lowp3")
            if x is None:
                continue
            for L in ((r["a1"], r["a2"], x), (r["a2"], r["a1"], x)):
                o = po_of(r, L)
                if o is None:
                    continue
                b = next(b for b in bands if b[0] <= o < b[1])
                a = agg[b]
                a["n"] += 1
                if r["fin"] == L:
                    a["hit"] += 1
                    a["pays"].append(r["pay_tf"])
                    a["over"] += r["pay_tf"] > 100
        print(f"  {'予測オッズ帯':14s} {'点数':>7s} {'的中':>5s} {'的中%':>7s} "
              f"{'確定中央':>8s} {'確定/予測 中央':>13s} {'確定>100倍':>10s} {'ROI%':>7s}")
        for b in bands:
            a = agg[b]
            if not a["n"]:
                continue
            ratio = median([p / o for p, o in zip(a["pays"], a["pays"])]) if False else None
            lo = f"{b[0]:.0f}-{'∞' if b[1]>1e8 else f'{b[1]:.0f}'}倍"
            roi = sum(a["pays"]) / a["n"] * 100
            print(f"  {lo:14s} {a['n']:7d} {a['hit']:5d} "
                  f"{a['hit']/a['n']*100:6.2f}% "
                  f"{median(a['pays']) if a['pays'] else 0:8.1f} "
                  f"{'':13s} "
                  f"{(a['over']/a['hit']*100 if a['hit'] else 0):9.1f}% {roi:6.1f}")




# ═══════════════════════════════════════════════════════════════════════════
# A3. 「軸2車 → 人気薄」の目を**予測オッズで選んで**押さえる
#
# 🔴 朝には確定オッズが無い。実装できるのは予測オッズによる選別だけなので、
#    ここが実装可否そのもの。`calib` で 予測150倍+ → 確定100倍超が 90〜100% と
#    分かったので、L を掃引して「何点買って・何回救えるか」を出す。
# ═══════════════════════════════════════════════════════════════════════════

def pin_legs(r, lo, kmax, both=True, ranks=2):
    """軸2車を1-2着に置き、3着が予測 lo 倍以上の目。予測オッズ降順に kmax 点まで。

    ranks: 3着に使う相手の下限順位（2 = 指数3位以下の全員）。
    """
    out = []
    for x in r["order"][ranks:]:
        dirs = [(r["a1"], r["a2"], x)]
        if both:
            dirs.append((r["a2"], r["a1"], x))
        else:
            dirs = [_dir1(r, x)]
        for L in dirs:
            if L in r["stakes"]:
                continue
            o = po_of(r, L)
            if o is not None and o >= lo:
                out.append((o, L))
    out.sort(key=lambda t: -t[0])
    return [L for _, L in out[:kmax]]


def pin_cmd() -> None:
    for label, w in WINS:
        rr = [r for r in ROWS if r["win"] == w and not r["trio"]]
        nd = NDAYS[w]
        print(f"\n=== {label}  三連単を売る商品 n={len(rr):,}（押さえは**まだ配分に入れていない**）===")
        print(f"  {'条件':30s} {'発火%':>6s} {'点/発火':>7s} {'費用/R':>7s} {'的中/日':>7s} "
              f"{'確定中央':>8s} {'100倍超%':>8s} {'救済/日':>7s} {'ガミ/日':>7s} {'押さえROI%':>10s}")
        for both in (True, False):
            for lo in (50, 75, 100, 125, 150, 200):
                for kmax in (1, 2, 4):
                    n_fire = n_pts = 0
                    hits, resc, gami = [], 0, 0
                    for r in rr:
                        legs = pin_legs(r, lo, kmax, both)
                        if not legs:
                            continue
                        n_fire += 1
                        n_pts += len(legs)
                        if r["fin"] in legs:
                            hits.append(r["pay_tf"])
                            if r["pay_tf"] > 100:
                                resc += r["pay"] < r["inv"]
                            else:
                                gami += 1
                    if not n_fire:
                        continue
                    inv = n_pts * 100
                    roi = sum(hits) * 100 / inv * 100 if inv else 0
                    tag = f"{'両' if both else '片'}向き 予測{lo}倍+ 最大{kmax}点"
                    print(f"  {tag:30s} {n_fire/len(rr)*100:5.1f}% {n_pts/n_fire:7.2f} "
                          f"{inv/len(rr):7.1f} {len(hits)/nd:7.3f} "
                          f"{median(hits) if hits else 0:8.1f} "
                          f"{(sum(1 for x in hits if x > 100)/len(hits)*100 if hits else 0):7.1f}% "
                          f"{resc/nd:7.3f} {gami/nd:7.3f} {roi:9.1f}%")




# ═══════════════════════════════════════════════════════════════════════════
# B. 商品として組む — 押さえを買い目に足し、残りを本番の配分に掛ける
#
# 🔴 本番の `allocate` をそのまま呼ぶ（予算だけ 10,000 − 押さえ代 に落とす）。
# 🔴 押さえは**床（`MIN_PAYOUT_MULT=2.0`）の外**で固定100円。床を掛けると
#    150倍の点でも 200円、30倍なら 700円になり「100円の押さえ」ではなくなる。
# 🔴 入稿ゲートは押さえも含めて判定する（本番の `mean_expected_payout` は
#    入稿する全点の平均）。落ちたら押さえを外して現行のまま売る（在庫を減らさない）。
# ═══════════════════════════════════════════════════════════════════════════

from dataclasses import replace as _replace  # noqa: E402

from src.type_lab import (  # noqa: E402
    PLANS, alloc_fallback, allocate, mean_expected_payout)

MIN_MEAN_PAYOUT, MIN_POINT_ODDS = 20_000, 2.0


def _plan_of(r):
    return _replace(PLANS[r["used"]], alloc=r["alloc"], floor_mult=r["floor_mult"])


def _shrink_prop(r, cost):
    """残り予算を本番の配分に掛け直す（全点から按分）。"""
    plan = _plan_of(r)
    legs = list(r["legs"])
    st = allocate(legs, r["pod"], r["probs"], plan, budget=10_000 - cost)
    if not st or len(st) != len(legs):
        fb = alloc_fallback(plan)
        if fb is not None:
            st = allocate(legs, r["pod"], r["probs"], fb, budget=10_000 - cost)
    return st if st and len(st) == len(legs) else None


def _shrink_tail(r, cost):
    """確率最下位の点から 100円ずつ削る（他の点の払戻は1円も変えない）。"""
    st = dict(r["stakes"])
    order = sorted(st, key=lambda c: r["probs"].get(c, 0.0))
    left = cost
    for c in order:
        while left > 0 and st[c] > 100:
            st[c] -= 100
            left -= 100
        if left <= 0:
            break
    return None if left > 0 else st


def build_arm(r, lo, kmax, both, mode, rng=None, ctrl=None):
    """(legs->stakes, fired) を返す。押さえが組めない/ゲートに落ちるなら現行のまま。"""
    if ctrl is None:
        pins = pin_legs(r, lo, kmax, both)
    else:
        pins = ctrl
    if not pins:
        return dict(r["stakes"]), False, r["pod"]
    cost = 100 * len(pins)
    st = (_shrink_prop if mode == "prop" else _shrink_tail)(r, cost)
    if st is None:
        return dict(r["stakes"]), False, r["pod"]
    full = dict(st)
    for p in pins:
        full[p] = 100
    pod = dict(r["pod"])
    for p in pins:
        o = po_of(r, p)
        if o is None:
            return dict(r["stakes"]), False, r["pod"]
        pod[p] = o
    if mean_expected_payout(full, pod) <= MIN_MEAN_PAYOUT:
        return dict(r["stakes"]), False, r["pod"]
    if min(pod[c] for c in full) < MIN_POINT_ODDS:
        return dict(r["stakes"]), False, r["pod"]
    return full, True, pod


def _res(r, st, pod=None):
    """🔴 三連複を売る商品（A_trio / D_hit）は frozenset キー・確定は `odds_t3`。"""
    if r["trio"]:
        pay = float(st[r["win_t3"]] * r["odds_t3"]) if r["win_t3"] in st else 0.0
    else:
        pay = float(st[r["fin"]] * r["pay_tf"]) if r["fin"] in st else 0.0
    inv = float(sum(st.values()))
    mean = (mean_expected_payout(st, pod) if pod else r["mean"])
    return dict(inv=inv, pay=pay, mean=mean, stakes=st)


def _ctrl_pins(r, k, lo, rng, band=True):
    """無作為対照。買っていない点から k 点（band=True なら予測 lo 倍以上に限る）。"""
    po = PO()[r["i"]]
    cand = [PERMS[t] for t in range(210)
            if np.isfinite(po[t]) and po[t] > 0 and PERMS[t] not in r["stakes"]
            and (not band or po[t] >= lo)]
    if len(cand) < k:
        return []
    return [cand[j] for j in rng.choice(len(cand), size=k, replace=False)]


def _stake_of(r, arm):
    return arm[0]


#: 腕の定義 (押さえ1点の額, 予測オッズ下限, 最大点数, 両向きか, 残りの削り方)
#: 🔴 **額と下限はセット**。押さえが表示的中になるのは 確定オッズ > 予算/額 のときだけ
#:    （100円なら100倍・200円なら50倍・300円なら33倍）。額を上げると必要な倍率は
#:    下がるが、そのぶん本線から抜く額が増える。
ARMS = [
    ("100円 予測100倍+ 2点 按分",  100, 100, 2, True, "prop"),
    ("100円 予測125倍+ 2点 按分",  100, 125, 2, True, "prop"),
    ("100円 予測150倍+ 2点 按分",  100, 150, 2, True, "prop"),
    ("100円 予測125倍+ 1点 按分",  100, 125, 1, True, "prop"),
    ("100円 予測125倍+ 2点 下位削り", 100, 125, 2, True, "tail"),
    ("100円 絞りなし 2点 按分",     100,   0, 2, True, "prop"),
    ("200円 予測60倍+ 2点 按分",   200,  60, 2, True, "prop"),
    ("200円 予測75倍+ 2点 按分",   200,  75, 2, True, "prop"),
    ("300円 予測40倍+ 2点 按分",   300,  40, 2, True, "prop"),
]


def build_arm2(r, stake, lo, kmax, both, mode, ctrl=None):
    """(stakes, 候補あり, 発火, pod)。ゲートに落ちたら現行のまま（在庫を減らさない）。"""
    # 🔴 ctrl は空リストで「押さえなし」を表す。None を「指定なし」と混同すると
    #    対照腕が黙って本案へ落ちる（実際に一度踏んだ）。
    pins = ctrl if ctrl is not None else pin_legs(r, lo, kmax, both)
    if not pins:
        return dict(r["stakes"]), False, False, r["pod"]
    cost = stake * len(pins)
    st = (_shrink_prop if mode == "prop" else _shrink_tail)(r, cost)
    if st is None:
        return dict(r["stakes"]), True, False, r["pod"]
    full = dict(st)
    pod = dict(r["pod"])
    for p in pins:
        o = po_of(r, p)
        if o is None:
            return dict(r["stakes"]), True, False, r["pod"]
        full[p] = stake
        pod[p] = o
    if (mean_expected_payout(full, pod) <= MIN_MEAN_PAYOUT
            or min(pod[c] for c in full) < MIN_POINT_ODDS):
        return dict(r["stakes"]), True, False, r["pod"]
    return full, True, True, pod


def _run(allr, rr, nd, stake, lo, kmax, both, mode, ctrl_fn=None):
    recs, have, fired, keep, brk, res = [], 0, 0, 0, 0, 0
    for r in allr:
        if r["trio"]:
            recs.append(_res(r, dict(r["stakes"])))
            continue
        ctrl = ctrl_fn(r) if ctrl_fn else None
        st, h, f, pod = build_arm2(r, stake, lo, kmax, both, mode, ctrl)
        have += h
        fired += f
        a = _res(r, st, pod)
        b0 = r["pay"] >= r["inv"] and r["pay"] > 0
        a0 = a["pay"] >= a["inv"] and a["pay"] > 0
        keep += b0 and a0
        brk += b0 and not a0
        res += a0 and not b0
        recs.append(a)
    s = summ(recs, nd)
    s.update(have=have / len(rr) * 100, fire=fired / len(rr) * 100,
             gate=fired / have * 100 if have else 0.0,
             keep=keep, brk=brk, res=res,
             shown_flags=[1 if (x["pay"] >= x["inv"] and x["pay"] > 0) else 0
                          for x in recs])
    return s


def _dshown_ci(a, b, B=4000, seed=0):
    """同一レース対応の Δ表示的中(pt) の 95%CI。"""
    d = np.array(a["shown_flags"], float) - np.array(b["shown_flags"], float)
    rng = np.random.default_rng(seed)
    bs = rng.choice(d, size=(B, len(d)), replace=True).mean(axis=1) * 100
    return d.mean() * 100, float(np.percentile(bs, 2.5)), float(np.percentile(bs, 97.5))


def prod_cmd() -> None:
    for label, w in WINS:
        allr = [r for r in ROWS if r["win"] == w]
        rr = [r for r in allr if not r["trio"]]
        nd = NDAYS[w]
        base = _run(allr, rr, nd, 100, 1e18, 1, True, "prop")   # 発火しない=現行
        print(f"\n=== {label}  全商品 n={len(allr):,}（うち三連単 {len(rr):,}）===")
        print(HEAD + "  " + "{:>7s} {:>7s} {:>7s}".format("候補%", "発火%", "ゲート%"))
        print(line("現行", base))
        for name, stake, lo, kmax, both, mode in ARMS:
            s = _run(allr, rr, nd, stake, lo, kmax, both, mode)
            d, clo, chi = _dshown_ci(s, base)
            print(line(name, s) +
                  f"  {s['have']:6.1f}% {s['fire']:6.1f}% {s['gate']:6.1f}%"
                  f"  Δ表示的中 {d:+.2f} [{clo:+.2f},{chi:+.2f}]"
                  f"  維持{s['keep']:5d} 破壊{s['brk']:3d} 救済{s['res']:3d}")




# ═══════════════════════════════════════════════════════════════════════════
# C. 対照 — 「軸2車が1-2着の形」であることが効いているのか
#
# 🔴 件数は減らないが**点を1〜2点足す**操作なので、同数の点を無作為に足した腕と
#    比べないと「点を足したこと」と「その形であること」を区別できない。
#    ①帯を揃えない対照（買っていない点から無作為）②帯を揃えた対照（予測 lo 倍以上）
# ═══════════════════════════════════════════════════════════════════════════

def ctrl_cmd() -> None:
    lo, kmax, stake = 125, 2, 100
    for label, w in WINS:
        allr = [r for r in ROWS if r["win"] == w]
        rr = [r for r in allr if not r["trio"]]
        nd = NDAYS[w]
        base = _run(allr, rr, nd, stake, 1e18, 1, True, "prop")
        arm = _run(allr, rr, nd, stake, lo, kmax, True, "prop")
        d, clo, chi = _dshown_ci(arm, base)
        print(f"\n=== {label}  100円 予測{lo}倍+ 最大{kmax}点 按分 vs 無作為対照 20seed ===")
        print(HEAD)
        print(line("現行", base))
        print(line("本案", arm) + f"  Δ{d:+.2f} [{clo:+.2f},{chi:+.2f}]")
        # 本案が実際に足した点数をレースごとに覚え、対照は同数を無作為に足す
        nk = {}
        for r in rr:
            nk[r["race_key"]] = len(pin_legs(r, lo, kmax, True))
        for band, tag in ((True, f"無作為(予測{lo}倍+・同数)"), (False, "無作為(帯なし・同数)")):
            ds, shs = [], []
            for seed in range(20):
                rng = np.random.default_rng(seed)

                def cf(r, rng=rng, band=band):
                    k = nk[r["race_key"]]
                    return (_ctrl_pins(r, k, lo, rng, band) or []) if k else []
                s = _run(allr, rr, nd, stake, lo, kmax, True, "prop", ctrl_fn=cf)
                ds.append(_dshown_ci(s, base)[0])
                shs.append(s["shown"])
            ds = np.array(ds)
            wins = int((np.array(_dshown_ci(arm, base)[0]) > ds).sum())
            print(f"  {tag:30s} 表示的中 中央 {np.median(shs):8.2f}%  "
                  f"Δ中央 {np.median(ds):+.2f}pt  範囲 [{ds.min():+.2f},{ds.max():+.2f}]  "
                  f"本案が勝った seed {wins}/20")


def gate_cmd() -> None:
    """ゲートに落ちるのはどこか。押さえ1点は 100×予測オッズ を平均へ持ち込む。"""
    lo, kmax, stake = 125, 2, 100
    for label, w in WINS:
        rr = [r for r in ROWS if r["win"] == w and not r["trio"]]
        print(f"\n=== {label}  ゲートの内訳（100円 予測{lo}倍+ 最大{kmax}点）===")
        rows = []
        for r in rr:
            pins = pin_legs(r, lo, kmax, True)
            if not pins:
                continue
            st, h, f, pod = build_arm2(r, stake, lo, kmax, True, "prop")
            po = [po_of(r, p) or 0.0 for p in pins]
            rows.append((r["mean"], f, len(pins), sum(po) / len(po), r["plan"]))
        print(f"  {'現行の平均想定払戻':18s} {'n':>6s} {'発火%':>7s} {'押さえの予測中央':>14s}")
        for lo_, hi_ in ((0, 22_000), (22_000, 25_000), (25_000, 30_000),
                         (30_000, 40_000), (40_000, 1e18)):
            sel = [x for x in rows if lo_ <= x[0] < hi_]
            if not sel:
                continue
            tag = f"{lo_/1000:.0f}〜{'∞' if hi_>1e17 else f'{hi_/1000:.0f}'}千円"
            print(f"  {tag:18s} {len(sel):6d} {sum(x[1] for x in sel)/len(sel)*100:6.1f}% "
                  f"{median([x[3] for x in sel]):14.1f}")
        print(f"  {'プラン別':18s} {'n':>6s} {'発火%':>7s}")
        for p in sorted({x[4] for x in rows}):
            sel = [x for x in rows if x[4] == p]
            print(f"  {p:18s} {len(sel):6d} {sum(x[1] for x in sel)/len(sel)*100:6.1f}%")




# ═══════════════════════════════════════════════════════════════════════════
# B2. 点の選び方 — 「1車の両方向を組で買う」か「オッズの高い順に拾う」か
#
# 🔴 `pin_legs` は予測オッズ降順に拾うので、**同じ車の逆向きばかり**が並びうる。
#    決着が a1→a2→X なのに買ったのが a2→a1→X なら取れない。
#    「1車を選んで両方向」を別腕として測る。
# ═══════════════════════════════════════════════════════════════════════════

def pair_legs(r, lo, ncar=1):
    """相手 X を選び、**その両方向**を買う。X は (a1,a2,X) の予測オッズ降順。"""
    cand = []
    for x in r["order"][2:]:
        d1, d2 = (r["a1"], r["a2"], x), (r["a2"], r["a1"], x)
        o1, o2 = po_of(r, d1), po_of(r, d2)
        if o1 is None or o2 is None or min(o1, o2) < lo:
            continue
        cand.append((min(o1, o2), [L for L in (d1, d2) if L not in r["stakes"]]))
    cand.sort(key=lambda t: -t[0])
    out = []
    for _, legs in cand[:ncar]:
        out += legs
    return out


def pair_cmd() -> None:
    for label, w in WINS:
        allr = [r for r in ROWS if r["win"] == w]
        rr = [r for r in allr if not r["trio"]]
        nd = NDAYS[w]
        base = _run(allr, rr, nd, 100, 1e18, 1, True, "prop")
        print(f"\n=== {label} ===")
        print(HEAD + "  " + "{:>7s} {:>7s}".format("候補%", "発火%"))
        print(line("現行", base))
        for lo in (75, 100, 125, 150):
            def cf(r, lo=lo):
                return pair_legs(r, lo, 1)
            s = _run(allr, rr, nd, 100, lo, 2, True, "prop", ctrl_fn=cf)
            d, clo, chi = _dshown_ci(s, base)
            print(line(f"1車の両方向 予測{lo}倍+", s) +
                  f"  {s['have']:6.1f}% {s['fire']:6.1f}%"
                  f"  Δ {d:+.2f} [{clo:+.2f},{chi:+.2f}]"
                  f"  維持{s['keep']:5d} 破壊{s['brk']:3d} 救済{s['res']:3d}")
        for lo in (100, 125):
            s = _run(allr, rr, nd, 100, lo, 2, True, "prop")
            d, clo, chi = _dshown_ci(s, base)
            print(line(f"オッズ降順2点 予測{lo}倍+", s) +
                  f"  {s['have']:6.1f}% {s['fire']:6.1f}%"
                  f"  Δ {d:+.2f} [{clo:+.2f},{chi:+.2f}]"
                  f"  維持{s['keep']:5d} 破壊{s['brk']:3d} 救済{s['res']:3d}")


def byplan_cmd() -> None:
    lo, kmax, stake = 125, 2, 100
    for label, w in WINS:
        allr = [r for r in ROWS if r["win"] == w]
        rr = [r for r in allr if not r["trio"]]
        nd = NDAYS[w]
        print(f"\n=== {label}  100円 予測{lo}倍+ 最大{kmax}点 按分・プラン別 ===")
        print(f"  {'プラン':10s} {'n':>6s} {'発火%':>7s} {'現行 表示的中':>12s} "
              f"{'本案 表示的中':>12s} {'Δpt':>7s} {'救済':>5s} {'破壊':>5s} "
              f"{'払戻中央 現行→本案':>22s}")
        for p in sorted({r["plan"] for r in rr}):
            sub = [r for r in rr if r["plan"] == p]
            b = _run(sub, sub, nd, stake, 1e18, 1, True, "prop")
            a = _run(sub, sub, nd, stake, lo, kmax, True, "prop")
            print(f"  {p:10s} {len(sub):6d} {a['fire']:6.1f}% {b['shown']:11.2f}% "
                  f"{a['shown']:11.2f}% {a['shown']-b['shown']:+7.2f} "
                  f"{a['res']:5d} {a['brk']:5d} "
                  f"{b['med_pay']:10,.0f} → {a['med_pay']:8,.0f}")




#: 🔴 一撃商品（`A_ana` / `*_sign`）へ押さえを足してはいけない（DESIGN.md 第5層 #3）。
#: あれは表示的中で測らない商品で、押さえは**その商品固有のKPI（払戻中央・10万+）を
#: 確実に下げる**。実測でも `F_sign` の払戻中央が 135,960 → 128,940円（−5%）。
HIT_PLANS = frozenset({"A_hit", "B_hit", "C_hit", "E_hit", "F_hit"})


def final_cmd() -> None:
    lo, kmax, stake = 125, 2, 100
    for label, w in WINS:
        allr = [r for r in ROWS if r["win"] == w]
        rr = [r for r in allr if not r["trio"]]
        nd = NDAYS[w]
        base = _run(allr, rr, nd, stake, 1e18, 1, True, "prop")
        print(f"\n=== {label} ===")
        print(HEAD + "  " + "{:>7s}".format("発火%"))
        print(line("現行", base))
        for tag, only in (("全商品へ押さえ", None), ("当てにいく商品だけへ押さえ", HIT_PLANS)):
            for lo_ in (100, 125):
                def cf(r, lo_=lo_, only=only):
                    if only is not None and r["plan"] not in only:
                        return []
                    return pin_legs(r, lo_, kmax, True)
                s = _run(allr, rr, nd, stake, lo_, kmax, True, "prop", ctrl_fn=cf)
                d, clo, chi = _dshown_ci(s, base)
                print(line(f"{tag} 予測{lo_}倍+", s) +
                      f"  {s['fire']:6.1f}%"
                      f"  Δ {d:+.2f} [{clo:+.2f},{chi:+.2f}]"
                      f"  維持{s['keep']:5d} 破壊{s['brk']:3d} 救済{s['res']:3d}")




def build_arm3(r, stake, lo, kmax, gate_on_base):
    """`gate_on_base=True` なら**押さえを入稿ゲートの計算から外す**。

    🔴 これはゲートの定義を変える案＝別の層の変更（DESIGN.md 第5層 #5b）。
       `MIN_MEAN_PAYOUT` は「張る額を減らす」ための自前ルールなので、
       100円の押さえを平均へ入れるかどうかは設計判断であって測定では決まらない。
    """
    pins = pin_legs(r, lo, kmax, True)
    if not pins:
        return dict(r["stakes"]), False, False, r["pod"]
    cost = stake * len(pins)
    st = _shrink_prop(r, cost)
    if st is None:
        return dict(r["stakes"]), True, False, r["pod"]
    pod = dict(r["pod"])
    for p in pins:
        o = po_of(r, p)
        if o is None:
            return dict(r["stakes"]), True, False, r["pod"]
        pod[p] = o
    full = dict(st)
    for p in pins:
        full[p] = stake
    judged = st if gate_on_base else full
    if (mean_expected_payout(judged, pod) <= MIN_MEAN_PAYOUT
            or min(pod[c] for c in full) < MIN_POINT_ODDS):
        return dict(r["stakes"]), True, False, r["pod"]
    return full, True, True, pod


def gateout_cmd() -> None:
    stake, kmax = 100, 2
    for label, w in WINS:
        allr = [r for r in ROWS if r["win"] == w]
        rr = [r for r in allr if not r["trio"]]
        nd = NDAYS[w]
        base = _run(allr, rr, nd, stake, 1e18, 1, True, "prop")
        print(f"\n=== {label} ===")
        print(HEAD + "  " + "{:>7s}".format("発火%"))
        print(line("現行", base))
        for gob in (False, True):
            for lo in (100, 125):
                recs, have, fired, keep, brk, res = [], 0, 0, 0, 0, 0
                for r in allr:
                    if r["trio"]:
                        recs.append(_res(r, dict(r["stakes"])))
                        continue
                    st, h, f, pod = build_arm3(r, stake, lo, kmax, gob)
                    have += h
                    fired += f
                    a = _res(r, st, pod)
                    b0 = r["pay"] >= r["inv"] and r["pay"] > 0
                    a0 = a["pay"] >= a["inv"] and a["pay"] > 0
                    keep += b0 and a0
                    brk += b0 and not a0
                    res += a0 and not b0
                    recs.append(a)
                s = summ(recs, nd)
                s["shown_flags"] = [1 if (x["pay"] >= x["inv"] and x["pay"] > 0) else 0
                                    for x in recs]
                d, clo, chi = _dshown_ci(s, base)
                tag = ("押さえをゲートから外す" if gob else "現行のゲート") + f" 予測{lo}倍+"
                print(line(tag, s) + f"  {fired/len(rr)*100:6.1f}%"
                      f"  Δ {d:+.2f} [{clo:+.2f},{chi:+.2f}]"
                      f"  維持{keep:5d} 破壊{brk:3d} 救済{res:3d}")




def who_cmd() -> None:
    """予測オッズで選んだ押さえは、結局どんな相手か（指数順位・印・ライン）。"""
    lo, kmax = 125, 2
    for label, w in WINS:
        rr = [r for r in ROWS if r["win"] == w and not r["trio"]]
        cnt = defaultdict(int)
        mk = defaultdict(int)
        ln = defaultdict(int)
        n = 0
        same_car = tot_race = 0
        for r in rr:
            pins = pin_legs(r, lo, kmax, True)
            if not pins:
                continue
            tot_race += 1
            if len({p[2] for p in pins}) == 1 and len(pins) == 2:
                same_car += 1
            for p in pins:
                x = p[2]
                n += 1
                cnt[r["order"].index(x) + 1] += 1
                mk[{0: "無印", 1: "◎", 2: "○", 3: "△", 4: "注"}[r["mk"][x]]] += 1
                ln["別ライン" if _otherline(r, x) else "軸と同ライン"] += 1
        print(f"\n=== {label}  押さえ（予測{lo}倍+ 最大{kmax}点）の相手 n={n:,}点 ===")
        print("  3着に置いた車の指数順位: " +
              " / ".join(f"{k}位 {cnt[k]/n*100:.1f}%" for k in sorted(cnt)))
        print("  その車のWT印: " +
              " / ".join(f"{k} {v/n*100:.1f}%" for k, v in
                         sorted(mk.items(), key=lambda t: -t[1])))
        print("  ライン: " + " / ".join(f"{k} {v/n*100:.1f}%" for k, v in ln.items()))
        print(f"  2点が同じ車の両方向だった割合: {same_car/tot_race*100:.1f}%")


if __name__ == "__main__":
    globals()[(sys.argv[1] if len(sys.argv) > 1 else "base") + "_cmd"]()
