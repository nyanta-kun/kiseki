#!/usr/bin/env python3
"""型C「堅いが崩れ筋」の商品設計ラボ（2026-08-27）。

台・作法は `scripts/exp_type_lab/common.py` を参照。
結論は `docs/type_lab/type_c.md`。

使い方: .venv/bin/python scripts/exp_type_lab/type_c.py <section> [...]
  diag        型Cの姿（二軸そろい・3着の出どころ・確定オッズ）
  trio        三連複 軸2車+相手k点（相手25通り × ダッチ/均等）
  trio_agree  同上を 印一致/不一致 で分割
  soft        1軸固定+2軸目2点+3着流し
  tf          三連単（確率順・EV順・軸制約・オッズ下限）
  sweep       三連単 均等 × 予測オッズ下限L × 点数k
  tfsweep     三連単 **ダッチ** × L × k（採用候補の掃引）
  tf2         三連単の軸制約つき・および賭け金の強弱
  tfctrl      採用候補の型間対照（A〜F・全体）
  tfagree     採用候補を 印一致/不一致 で分割
  ctrl        主要腕の型間対照
  ladder      点数のはしご（型A ↔ 型C ↔ 全体）
  boot        採用候補の 95%CI（型C ↔ 型C以外 の差も）
  stab        採用候補の四半期別
"""
from __future__ import annotations

import sys
from collections import Counter

import numpy as np

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import common as C  # noqa: E402

WIN = ("explore", "confirm")

# 🔴 NpzFile は毎回の添字アクセスで**配列全体を解凍し直す**。
#    素の common.board()["P3"][i] をループで呼ぶと実測で数百倍遅くなるので、
#    ここで一度だけ実体化して差し替える（値は同一）。
_Z = {k: C.board()[k] for k in C.board().files}
C._Z = _Z  # common 側の board() も dict を返すようにする（[] は同じ意味）


TL = "C"          # 対象の型（ctrl セクションだけ切り替える）


def pop(window: str, agree=None, tl: str | None = None) -> np.ndarray:
    return C.select(tl or TL, window, agree=agree)


# ───────────────────────── 診断 ─────────────────────────

def diag() -> None:
    z = C.board()
    for w in WIN:
        idx = pop(w)
        nd = C.days_of(idx)
        print(f"\n=== 型C {w}  {len(idx)}R / {nd}日 ({len(idx)/nd:.2f}件/日) ===")
        for ag in (None, True, False):
            j = pop(w, agree=ag)
            if len(j) == 0:
                continue
            two = 0
            third_pos = Counter()
            for i in j:
                o = C.p3_order(int(i))
                win = set(C.CANON3[int(z["TRIO_WIN"][i])])
                if o[0] in win and o[1] in win:
                    two += 1
                    rest = [c for c in win if c not in (o[0], o[1])][0]
                    third_pos[o.index(rest) + 1] += 1
            tot = sum(third_pos.values())
            dist = " ".join(f"{p}位{third_pos[p]/tot*100:4.1f}%" for p in range(3, 8))
            lab = {None: "全部", True: "印一致", False: "印不一致"}[ag]
            print(f"  {lab:6s} n={len(j):5d} 二軸そろい {two/len(j)*100:5.2f}%  3着の出どころ: {dist}")
        # 確定三連複オッズ
        ods = [float(z["TRIO_ODDS"][i][C.C3IDX[frozenset(C.CANON3[int(z['TRIO_WIN'][i])])]]) for i in idx]
        ods = sorted(x for x in ods if np.isfinite(x))
        print(f"  確定三連複オッズ  p25 {ods[len(ods)//4]:.1f} 中央 {ods[len(ods)//2]:.1f} p75 {ods[len(ods)*3//4]:.1f}")


# ───────────────────────── 相手の選び方 ─────────────────────────

def partner_sets(i: int, rule: str) -> list[list[int]] | None:
    """軸2車を除いた5車（レース全体の3〜7番手）から相手を選ぶ。
    返すのは相手車番のリスト（＝三連複の3枚目）。"""
    o = C.p3_order(i)
    a1, a2 = o[0], o[1]
    rest = o[2:]                       # 全体3,4,5,6,7番手（p3 降順）
    if rule.startswith("desc"):        # 上位から k 点
        k = int(rule[4:])
        return [rest[:k]]
    if rule.startswith("asc"):         # 下位から k 点
        k = int(rule[3:])
        return [rest[-k:]]
    if rule.startswith("pos"):         # 位置指定 "pos35" -> 全体3,5番手
        ps = [int(c) for c in rule[3:]]
        return [[rest[p - 3] for p in ps]]
    raise ValueError(rule)


def build_trio(i: int, rule: str) -> list[frozenset]:
    o = C.p3_order(i)
    a1, a2 = o[0], o[1]
    ps = partner_sets(i, rule)[0]
    return [frozenset((a1, a2, p)) for p in ps]


def run_trio(window: str, rule: str, tilt: bool, agree=None) -> tuple[dict, float]:
    idx = pop(window, agree=agree)
    nd = C.days_of(idx)
    recs, tried = [], 0
    for i in idx:
        i = int(i)
        combos = build_trio(i, rule)
        st = C.trio_stakes(i, combos, tilt=tilt)
        if st is None:
            continue
        tried += 1
        if not C.trio_gate(i, st):
            continue
        inv, pay, mean = C.trio_result(i, st)
        recs.append(dict(date=C.board()["DATE"][i], inv=inv, pay=pay, mean=mean, k=len(st)))
    s = C.summarize(recs, n_days_all=nd)
    return s, (len(recs) / tried * 100 if tried else 0.0)


def trio() -> None:
    rules = (["desc1", "desc2", "desc3", "desc4", "desc5",
              "asc1", "asc2", "asc3",
              "pos3", "pos4", "pos5", "pos6", "pos7",
              "pos34", "pos35", "pos45", "pos56", "pos57", "pos67",
              "pos345", "pos456", "pos567", "pos357", "pos3456", "pos4567"])
    for tilt in (True, False):
        for w in WIN:
            print(f"\n=== 三連複 軸2車+相手 / 配分={'ダッチ' if tilt else '均等'} / {w} ===")
            print(C.HEAD + "  通過%")
            for r in rules:
                s, g = run_trio(w, r, tilt)
                print(C.line(r, s) + f" {g:6.1f}")


def trio_agree() -> None:
    for ag in (True, False):
        for w in WIN:
            print(f"\n=== 三連複 / 印{'一致' if ag else '不一致'} / {w} ===")
            print(C.HEAD + "  通過%")
            for r in ("desc1", "desc2", "desc3", "desc5", "pos5", "pos45", "pos345", "pos567"):
                s, g = run_trio(w, r, True, agree=ag)
                print(C.line(r, s) + f" {g:6.1f}")


# ───────────────── 1軸固定 + 2軸目2点 + 3着流し ─────────────────

def build_soft(i: int, mode: str) -> list[frozenset]:
    """a1 = p3 1位固定。2軸目候補 = p3 2位・3位。3着流しの範囲を mode で変える。"""
    o = C.p3_order(i)
    a1, b1, b2 = o[0], o[1], o[2]
    rest = o[3:]                        # 全体4〜7番手
    if mode == "flow_all":              # 3着 = 残り全部（b の相方も含む）
        third = {b1: [b2] + rest, b2: [b1] + rest}
    elif mode == "flow4":               # 3着 = 全体4〜7番手
        third = {b1: rest, b2: rest}
    elif mode == "flow3":               # 3着 = 全体4〜6番手
        third = {b1: rest[:3], b2: rest[:3]}
    elif mode == "flow2":
        third = {b1: rest[:2], b2: rest[:2]}
    elif mode == "cross":               # b1-b2 を必ず含み + 各1点
        third = {b1: [b2] + rest[:1], b2: rest[:1]}
    else:
        raise ValueError(mode)
    out = []
    for b, ts in third.items():
        for t in ts:
            f = frozenset((a1, b, t))
            if len(f) == 3 and f not in out:
                out.append(f)
    return out


def run_soft(window: str, mode: str, tilt: bool) -> tuple[dict, float]:
    idx = pop(window)
    nd = C.days_of(idx)
    recs, tried = [], 0
    for i in idx:
        i = int(i)
        combos = build_soft(i, mode)
        st = C.trio_stakes(i, combos, tilt=tilt)
        if st is None:
            continue
        tried += 1
        if not C.trio_gate(i, st):
            continue
        inv, pay, mean = C.trio_result(i, st)
        recs.append(dict(date=C.board()["DATE"][i], inv=inv, pay=pay, mean=mean, k=len(st)))
    return C.summarize(recs, n_days_all=nd), (len(recs) / tried * 100 if tried else 0.0)


def soft() -> None:
    for tilt in (True, False):
        for w in WIN:
            print(f"\n=== 1軸固定+2軸目2点+3着流し / 配分={'ダッチ' if tilt else '均等'} / {w} ===")
            print(C.HEAD + "  通過%")
            for m in ("cross", "flow2", "flow3", "flow4", "flow_all"):
                s, g = run_soft(w, m, tilt)
                print(C.line(m, s) + f" {g:6.1f}")


# ───────────────────────── 三連単 ─────────────────────────

def build_tf(i: int, mode: str, k: int) -> list[tuple]:
    z = C.board()
    o = C.p3_order(i)
    pr = z["PROB"][i]
    po = z["PO"][i]
    if mode == "prob":                       # 確率上位k点（制約なし）
        order = np.argsort(-pr)
    elif mode == "ev":                       # 確率×予測オッズ上位k点
        ev = pr * np.where(np.isfinite(po), po, 0)
        order = np.argsort(-ev)
    elif mode == "axis2":                    # 軸2車が1・2着（順不同）
        cand = [j for j, c in enumerate(C.CANON) if set(c[:2]) == {o[0], o[1]}]
        cand.sort(key=lambda j: -pr[j])
        return [C.CANON[j] for j in cand[:k]]
    elif mode == "axis2_23":                 # 軸2車が2・3着（1着=他）
        cand = [j for j, c in enumerate(C.CANON) if set(c[1:]) == {o[0], o[1]}]
        cand.sort(key=lambda j: -pr[j])
        return [C.CANON[j] for j in cand[:k]]
    elif mode == "a1_1st":                   # 軸1が1着固定・確率順
        cand = [j for j, c in enumerate(C.CANON) if c[0] == o[0]]
        cand.sort(key=lambda j: -pr[j])
        return [C.CANON[j] for j in cand[:k]]
    elif mode == "axis2_any":                # 軸2車がどこかに2つとも入る
        cand = [j for j, c in enumerate(C.CANON) if {o[0], o[1]} <= set(c)]
        cand.sort(key=lambda j: -pr[j])
        return [C.CANON[j] for j in cand[:k]]
    elif mode.startswith("ax2odds"):         # 軸2車が1・2着（順不同）∧ 予測オッズ下限
        lo = float(mode[7:])
        cand = [j for j, c in enumerate(C.CANON)
                if set(c[:2]) == {o[0], o[1]} and np.isfinite(po[j]) and po[j] >= lo]
        cand.sort(key=lambda j: -pr[j])
        return [C.CANON[j] for j in cand[:k]]
    elif mode.startswith("anyodds"):         # 軸2車が3着までにそろう ∧ 予測オッズ下限
        lo = float(mode[7:])
        cand = [j for j, c in enumerate(C.CANON)
                if {o[0], o[1]} <= set(c) and np.isfinite(po[j]) and po[j] >= lo]
        cand.sort(key=lambda j: -pr[j])
        return [C.CANON[j] for j in cand[:k]]
    elif mode.startswith("a1odds"):          # 軸1が1着固定 ∧ 予測オッズ下限
        lo = float(mode[6:])
        cand = [j for j, c in enumerate(C.CANON)
                if c[0] == o[0] and np.isfinite(po[j]) and po[j] >= lo]
        cand.sort(key=lambda j: -pr[j])
        return [C.CANON[j] for j in cand[:k]]
    elif mode.startswith("odds"):            # 予測オッズ下限つき確率上位
        lo = float(mode[4:])
        cand = [j for j in range(210) if np.isfinite(po[j]) and po[j] >= lo]
        cand.sort(key=lambda j: -pr[j])
        return [C.CANON[j] for j in cand[:k]]
    else:
        raise ValueError(mode)
    return [C.CANON[j] for j in order[:k]]


def tf_stakes_tilt(i: int, combos: list[tuple]) -> dict:
    """三連単をダッチング（∝1/予測オッズ）。common.tf_stakes は均等のみなのでここで持つ。"""
    z = C.board()
    po = [float(z["PO"][i][C.CIDX[c]]) for c in combos]
    if any((not np.isfinite(x)) or x <= 0 for x in po):
        return {}
    w = [1.0 / x for x in po]
    n_units = C.BUDGET // C.UNIT
    if n_units < len(combos):
        return {}
    tot = sum(w)
    units = [1] * len(combos)
    rest = n_units - len(combos)
    for j, x in enumerate(w):
        units[j] += int(rest * x / tot)
    while sum(units) < n_units:
        j = min(range(len(units)), key=lambda t: units[t] / max(w[t], 1e-12))
        units[j] += 1
    return {c: u * C.UNIT for c, u in zip(combos, units)}


def run_tf(window: str, mode: str, k: int, tilt: bool = False) -> dict:
    idx = pop(window)
    nd = C.days_of(idx)
    z = C.board()
    recs = []
    for i in idx:
        i = int(i)
        combos = build_tf(i, mode, k)
        if len(combos) < k:
            continue
        st = tf_stakes_tilt(i, combos) if tilt else C.tf_stakes(i, combos)
        if not st:
            continue
        if not C.tf_gate(i, st):
            continue
        inv, pay = C.tf_result(i, st)
        mean = sum(st[c] / 100.0 * float(z["PO"][i][C.CIDX[c]]) * 100 for c in st) / len(st)
        recs.append(dict(date=z["DATE"][i], inv=inv, pay=pay, mean=mean, k=len(st)))
    return C.summarize(recs, n_days_all=nd)


def tfsweep() -> None:
    """三連単ダッチ配分の掃引（下限L × 点数k）。
    ダッチなら的中時の払戻は全点ほぼ一定 = 予算 / Σ(1/予測オッズ) >= L/k 倍。"""
    for w in WIN:
        print(f"\n=== 三連単ダッチ 予測オッズ下限L × 点数k / {w} ===")
        print(C.HEAD + "   下限倍率")
        for lo in (15.0, 20.0, 25.0, 30.0):
            for k in (8, 10, 12, 15, 20):
                s2 = run_tf(w, f"odds{lo}", k, tilt=True)
                print(C.line(f"L={lo:.0f} k={k}", s2) + f" {lo/k:9.2f}倍")
            print()


def tfctrl() -> None:
    """三連単ダッチの型間対照。"""
    global TL
    for w in WIN:
        print(f"\n=== 三連単ダッチ L=20 k=12 の型間対照 / {w} ===")
        print(C.HEAD)
        for tl in ("A", "B", "C", "D", "E", "F"):
            TL = tl
            for k in (10, 12):
                print(C.line(f"[{tl}] L=20 k={k}", run_tf(w, "odds20.0", k, tilt=True)))
        TL = "C"
        # 全体
        idx = C.select(None, w)
        nd = C.days_of(idx)
        z = C.board()
        for k in (10, 12):
            recs = []
            for i in idx:
                i = int(i)
                combos = build_tf(i, "odds20.0", k)
                if len(combos) < k:
                    continue
                st = tf_stakes_tilt(i, combos)
                if not st:
                    continue
                inv, pay = C.tf_result(i, st)
                mean = sum(st[c] / 100.0 * float(z["PO"][i][C.CIDX[c]]) * 100 for c in st) / len(st)
                recs.append(dict(date=z["DATE"][i], inv=inv, pay=pay, mean=mean, k=len(st)))
            print(C.line(f"[全体] L=20 k={k}", C.summarize(recs, n_days_all=nd)))


def tfagree() -> None:
    """採用候補を 印一致（＝現行どのランクも取っていない側）/ 不一致 で割る。"""
    z = C.board()
    for w in WIN:
        print(f"\n=== 三連単ダッチ L=20 k=12 / 印の一致で分割 / {w} ===")
        print(C.HEAD)
        base_days = C.days_of(pop(w))
        for ag, lab in ((None, "全部"), (True, "印一致(無商品側)"), (False, "印不一致")):
            idx = pop(w, agree=ag)
            recs = []
            for i in idx:
                i = int(i)
                combos = build_tf(i, "odds20.0", 12)
                if len(combos) < 12:
                    continue
                st = tf_stakes_tilt(i, combos)
                if not st:
                    continue
                inv, pay = C.tf_result(i, st)
                mean = sum(st[c] / 100.0 * float(z["PO"][i][C.CIDX[c]]) * 100 for c in st) / len(st)
                recs.append(dict(date=z["DATE"][i], inv=inv, pay=pay, mean=mean, k=len(st)))
            print(C.line(lab, C.summarize(recs, n_days_all=base_days)))


def stab() -> None:
    """採用候補の四半期別。片方の窓の一時期に依存していないか。"""
    z = C.board()
    idx = C.select("C", "all")
    rows = {}
    for i in idx:
        i = int(i)
        combos = build_tf(i, "odds20.0", 12)
        if len(combos) < 12:
            continue
        st = tf_stakes_tilt(i, combos)
        if not st:
            continue
        inv, pay = C.tf_result(i, st)
        mean = sum(st[c] / 100.0 * float(z["PO"][i][C.CIDX[c]]) * 100 for c in st) / len(st)
        d = str(z["DATE"][i])
        q = f"{d[:4]}Q{(int(d[5:7]) - 1) // 3 + 1}"
        rows.setdefault(q, []).append(dict(date=d, inv=inv, pay=pay, mean=mean, k=len(st)))
    print("\n=== 三連単ダッチ L=20 k=12 / 四半期別（型C全体） ===")
    print(C.HEAD)
    for q in sorted(rows):
        print(C.line(q, C.summarize(rows[q])))


def tf2() -> None:
    """軸2車の制約つき三連単・および賭け金の強弱。"""
    for w in WIN:
        print(f"\n=== 三連単（軸制約 × 予測オッズ下限）/ {w} ===")
        print(C.HEAD)
        for mode in ("ax2odds10.0", "ax2odds15.0", "anyodds15.0", "anyodds20.0",
                     "a1odds15.0", "a1odds20.0"):
            for k in (5, 8, 10, 12):
                print(C.line(f"{mode} k={k}", run_tf(w, mode, k)))
            print()
        print(f"--- 賭け金 ダッチ（∝1/予測オッズ）/ {w} ---")
        print(C.HEAD)
        for mode, k in (("odds20.0", 8), ("odds20.0", 10), ("odds20.0", 12),
                        ("anyodds15.0", 10), ("prob", 5)):
            print(C.line(f"{mode} k={k} ダッチ", run_tf(w, mode, k, tilt=True)))


def tf() -> None:
    for w in WIN:
        print(f"\n=== 三連単 / {w} ===")
        print(C.HEAD)
        for mode in ("prob", "ev", "axis2", "axis2_23", "axis2_any", "a1_1st",
                     "odds20.0", "odds30.0", "odds50.0"):
            for k in (1, 2, 3, 5, 10):
                s = run_tf(w, mode, k)
                print(C.line(f"{mode} k={k}", s))


# ───────────────────────── 型間の対照 ─────────────────────────

def ctrl() -> None:
    """同じ腕を型A/B/C/全体へ当てて「型Cに固有か」を見る。"""
    global TL
    arms_trio = [("pos45 均等", "pos45", False), ("pos456 均等", "pos456", False),
                 ("pos45 ダッチ", "pos45", True), ("pos3 1点", "pos3", True),
                 ("desc5 均等(総流し)", "desc5", False)]
    arms_tf = [("tf odds20 k=8", "odds20.0", 8), ("tf odds20 k=10", "odds20.0", 10),
               ("tf prob k=2", "prob", 2)]
    for w in WIN:
        print(f"\n=== 型間の対照 / {w} ===")
        print(C.HEAD)
        for tl in ("A", "B", "C", "D", "E", "F", None):
            lab = tl or "全体"
            TL = tl or "C"
            for name, rule, tilt in arms_trio:
                s, g = run_trio(w, rule, tilt) if tl else _all_trio(w, rule, tilt)
                print(C.line(f"[{lab}] {name}", s))
            for name, mode, k in arms_tf:
                s = run_tf(w, mode, k) if tl else _all_tf(w, mode, k)
                print(C.line(f"[{lab}] {name}", s))
            print()
    TL = "C"


def ladder() -> None:
    """点数のはしご。型A（鉄板）と型C（崩れ筋）と全体を同じ腕で並べる。
    仮説「Cは点数を増やす側」が正しければ、点数が増えるほど C が A に対して上がる。"""
    global TL
    for w in WIN:
        print(f"\n=== 点数のはしご（相手＝p3降順 k点・均等）/ {w} ===")
        print("  {:8s}".format("点数") + "".join(f"{lab:>26s}" for lab in
              ("型A 表示的中/払戻中央", "型C 表示的中/払戻中央", "全体 表示的中/払戻中央")) + "   C−A")
        for k in (1, 2, 3, 4, 5):
            row, vals = [], {}
            for tl in ("A", "C", None):
                TL = tl or "C"
                s2, _ = (run_trio(w, f"desc{k}", False) if tl else _all_trio(w, f"desc{k}", False))
                vals[tl] = s2
                row.append(f"{s2['shown']:11.2f}% {s2['med_pay']:11,.0f}円")
            d = vals["C"]["shown"] - vals["A"]["shown"]
            print(f"  {k}点     " + "".join(f"{x:>26s}" for x in row) + f"  {d:+6.2f}pt")
        TL = "C"
        print("  （件/日 参考）", end=" ")
        for k in (1, 3, 5):
            TL = "C"
            s2, _ = run_trio(w, f"desc{k}", False)
            print(f"C {k}点 {s2['perday']:.2f}件/日", end="  ")
        print()


def _all_idx(window: str) -> np.ndarray:
    return C.select(None, window)


def _all_trio(window: str, rule: str, tilt: bool):
    idx = _all_idx(window)
    nd = C.days_of(idx)
    recs, tried = [], 0
    for i in idx:
        i = int(i)
        st = C.trio_stakes(i, build_trio(i, rule), tilt=tilt)
        if st is None:
            continue
        tried += 1
        if not C.trio_gate(i, st):
            continue
        inv, pay, mean = C.trio_result(i, st)
        recs.append(dict(date=C.board()["DATE"][i], inv=inv, pay=pay, mean=mean, k=len(st)))
    return C.summarize(recs, n_days_all=nd), (len(recs) / tried * 100 if tried else 0.0)


def _all_tf(window: str, mode: str, k: int):
    idx = _all_idx(window)
    nd = C.days_of(idx)
    z = C.board()
    recs = []
    for i in idx:
        i = int(i)
        combos = build_tf(i, mode, k)
        if len(combos) < k:
            continue
        st = C.tf_stakes(i, combos)
        if not C.tf_gate(i, st):
            continue
        inv, pay = C.tf_result(i, st)
        mean = sum(st[c] / 100.0 * float(z["PO"][i][C.CIDX[c]]) * 100 for c in st) / len(st)
        recs.append(dict(date=z["DATE"][i], inv=inv, pay=pay, mean=mean, k=len(st)))
    return C.summarize(recs, n_days_all=nd)


# ───────────────────────── 三連単の掃引 ─────────────────────────

def sweep() -> None:
    for w in WIN:
        print(f"\n=== 三連単 予測オッズ下限 × 点数 / {w} ===")
        print(C.HEAD)
        for lo in (10.0, 15.0, 20.0, 25.0, 30.0):
            for k in (6, 8, 10, 12, 15, 20):
                s = run_tf(w, f"odds{lo}", k)
                print(C.line(f"odds{lo:.0f} k={k}", s))
            print()


def boot() -> None:
    """採用候補の 表示的中率 に 95%CI を付ける（レース単位ブートストラップ）。
    最後に「型C ↔ 型C以外」の差の CI も出す（型の効果が主張できるか）。"""
    rng = np.random.default_rng(7)
    cands = [("三連複 pos45 ダッチ2点", ("trio", "pos45", True)),
             ("三連複 pos456 均等3点", ("trio", "pos456", False)),
             ("三連複 pos3 ダッチ1点", ("trio", "pos3", True)),
             ("三連単 L20k12 ダッチ", ("tft", "odds20.0", 12)),
             ("三連単 L20k10 ダッチ", ("tft", "odds20.0", 10)),
             ("三連単 L15k12 ダッチ", ("tft", "odds15.0", 12)),
             ("三連単 L20k12 均等", ("tf", "odds20.0", 12))]
    for w in WIN:
        print(f"\n=== 95%CI（レース単位ブートストラップ 2,000回）/ {w} ===")
        for name, spec in cands:
            recs = _recs(w, spec)
            if not recs:
                continue
            shown = np.array([1.0 if (r["pay"] > r["inv"]) else 0.0 for r in recs])
            ratio = np.array([r["pay"] / r["inv"] for r in recs])
            n = len(recs)
            bs_s = [shown[rng.integers(0, n, n)].mean() * 100 for _ in range(2000)]
            bs_r = [ratio[rng.integers(0, n, n)].mean() * 100 for _ in range(2000)]
            print(f"  {name:22s} n={n:5d}  表示的中 {shown.mean()*100:5.2f}% "
                  f"[{np.percentile(bs_s,2.5):5.2f}, {np.percentile(bs_s,97.5):5.2f}]   "
                  f"ROI {ratio.mean()*100:6.1f}% [{np.percentile(bs_r,2.5):6.1f}, "
                  f"{np.percentile(bs_r,97.5):6.1f}]")

    # 型C ↔ 型C以外（同じ腕）
    spec = ("tft", "odds20.0", 12)
    for w in WIN:
        a = np.array([1.0 if r["pay"] > r["inv"] else 0.0 for r in _recs(w, spec)])
        b = np.array([1.0 if r["pay"] > r["inv"] else 0.0 for r in _recs(w, spec, others=True)])
        d = [a[rng.integers(0, len(a), len(a))].mean() * 100
             - b[rng.integers(0, len(b), len(b))].mean() * 100 for _ in range(2000)]
        print(f"\n  [{w}] 三連単L20k12ダッチ 型C {a.mean()*100:.2f}% ↔ 型C以外 {b.mean()*100:.2f}%"
              f"  差 {a.mean()*100-b.mean()*100:+.2f}pt "
              f"[{np.percentile(d,2.5):+.2f}, {np.percentile(d,97.5):+.2f}]")


def _recs(window: str, spec, others: bool = False) -> list[dict]:
    kind = spec[0]
    if others:
        z0 = C.board()
        allidx = C.select(None, window)
        idx = np.array([i for i in allidx if z0["TYPE"][i] != "C"])
    else:
        idx = pop(window)
    z = C.board()
    out = []
    for i in idx:
        i = int(i)
        if kind == "trio":
            st = C.trio_stakes(i, build_trio(i, spec[1]), tilt=spec[2])
            if st is None or not C.trio_gate(i, st):
                continue
            inv, pay, mean = C.trio_result(i, st)
        else:
            combos = build_tf(i, spec[1], spec[2])
            if len(combos) < spec[2]:
                continue
            st = tf_stakes_tilt(i, combos) if kind == "tft" else C.tf_stakes(i, combos)
            if not st or not C.tf_gate(i, st):
                continue
            inv, pay = C.tf_result(i, st)
            mean = 0.0
        out.append(dict(date=z["DATE"][i], inv=inv, pay=pay, mean=mean, k=len(st)))
    return out


if __name__ == "__main__":
    fn = {"diag": diag, "trio": trio, "trio_agree": trio_agree, "soft": soft, "tf": tf,
          "ctrl": ctrl, "sweep": sweep, "boot": boot, "ladder": ladder, "tf2": tf2, "tfsweep": tfsweep, "tfctrl": tfctrl, "tfagree": tfagree, "stab": stab}
    for a in sys.argv[1:]:
        fn[a]()
