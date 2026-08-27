#!/usr/bin/env python3
"""型A「鉄板」の商品設計 — 購入可能な買い方の網羅探索（2026-08-27）。

使い方: .venv/bin/python scripts/exp_type_lab/type_a.py [section]
  section: desc | trio | stake | tf | hybrid | agree | all（既定 all）
"""
from __future__ import annotations

import itertools
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

# 🔴 npz は遅延展開なので `z["P3"][i]` を呼ぶたびに配列全体を解凍する。
#    一度だけ materialize して common 側の束縛も差し替える（結果は同じ・速度だけ）。
_raw = np.load("/tmp/race_type_board.npz", allow_pickle=True)
Z = {k: _raw[k] for k in _raw.files}
C._Z = Z


def days_all(window: str) -> int:
    return C.days_of(C.select(None, window))


WIN = {"explore": "探索", "confirm": "確認"}


# ───────────────────────── 記述統計 ─────────────────────────

def section_desc() -> None:
    print("\n" + "=" * 100)
    print("§1 型A の姿（既知の再確認 + 商品設計に効く分布）")
    print("=" * 100)
    for w in ("explore", "confirm"):
        idx = C.select("A", w)
        nd = days_all(w)
        p3s = Z["AXIS_SUM"][idx]
        # 二軸そろい
        ok2 = 0
        trio_win_odds = []
        po_axis_trio = []      # 軸2車+相手1点(相手=p3 3番手)の予測オッズ
        for i in idx:
            o = C.p3_order(int(i))
            w3 = set(C.CANON3[int(Z["TRIO_WIN"][i])])
            if {o[0], o[1]} <= w3:
                ok2 += 1
            trio_win_odds.append(float(Z["TRIO_PAY"][i]))
            po_axis_trio.append(float(Z["TRIO_PO"][i][C.C3IDX[frozenset((o[0], o[1], o[2]))]]))
        trio_win_odds = np.array(trio_win_odds)
        po_axis_trio = np.array(po_axis_trio)
        print(f"\n[{WIN[w]}] {len(idx):,}R / {nd}日 = {len(idx)/nd:.2f}件/日")
        print(f"  axis_sum      中央 {np.median(p3s):.3f}  p25 {np.percentile(p3s,25):.3f}"
              f"  p75 {np.percentile(p3s,75):.3f}")
        print(f"  二軸そろい     {ok2/len(idx)*100:.1f}%")
        print(f"  確定三連複     中央 {np.median(trio_win_odds):.1f}倍"
              f"  p25 {np.percentile(trio_win_odds,25):.1f}  p75 {np.percentile(trio_win_odds,75):.1f}"
              f"  10倍+ {np.mean(trio_win_odds>=10)*100:.1f}%")
        f = np.isfinite(po_axis_trio)
        print(f"  予測三連複(軸2+p3 3番手) 中央 {np.median(po_axis_trio[f]):.2f}倍"
              f"  2倍未満 {np.mean(po_axis_trio[f]<2)*100:.1f}%")
        print(f"  印一致(AGREE)  {Z['AGREE'][idx].mean()*100:.1f}%")
        # 相手（p3 3〜7番手）の3着内率と、軸2車がそろった時の3着目の位置
        pos_hit = np.zeros(5)
        both = 0
        for i in idx:
            o = C.p3_order(int(i))
            w3 = set(C.CANON3[int(Z["TRIO_WIN"][i])])
            if {o[0], o[1]} <= w3:
                both += 1
                third = (w3 - {o[0], o[1]}).pop()
                pos_hit[o.index(third) - 2] += 1
        print("  3着目の位置（二軸そろい時）: " +
              " ".join(f"p3順位{j+3}={pos_hit[j]/both*100:5.1f}%" for j in range(5)))
        # 各相手の単独3着内率
        solo = np.zeros(5)
        for i in idx:
            o = C.p3_order(int(i))
            w3 = set(C.CANON3[int(Z["TRIO_WIN"][i])])
            for j in range(5):
                if o[j + 2] in w3:
                    solo[j] += 1
        print("  相手の3着内率(単独): " +
              " ".join(f"順位{j+3}={solo[j]/len(idx)*100:5.1f}%" for j in range(5)))


# ───────────────────────── 三連複の腕 ─────────────────────────

PARTNER_SETS = {
    # name -> 非軸5車を p3 降順に並べたときの取り位置（0=全体3番手 … 4=全体7番手）
    "相手1点(順位3)": [0],
    "相手1点(順位4)": [1],
    "相手1点(順位5)": [2],
    "相手1点(順位6)": [3],
    "相手1点(順位7)": [4],
    "相手2点(上位3,4)": [0, 1],
    "相手2点(3,5)": [0, 2],
    "相手2点(下位6,7)": [3, 4],
    "相手2点(5,6)": [2, 3],
    "相手3点(上位3,4,5)": [0, 1, 2],
    "相手3点(下位5,6,7)": [2, 3, 4],
    "相手3点(3,5,7)": [0, 2, 4],
    "相手4点(3-6)": [0, 1, 2, 3],
    "相手4点(4-7)": [1, 2, 3, 4],
    "相手5点(総流し)": [0, 1, 2, 3, 4],
}


def run_trio(idx: np.ndarray, picks, tilt: bool = True, nd: int | None = None) -> dict:
    """picks(i)-> list[frozenset] を評価。ゲートを通ったものだけ集計。"""
    recs = []
    for i in idx:
        i = int(i)
        combos = picks(i)
        if not combos:
            continue
        st = C.trio_stakes(i, combos, tilt=tilt)
        if st is None or not C.trio_gate(i, st):
            continue
        inv, pay, mean = C.trio_result(i, st)
        recs.append(dict(date=str(Z["DATE"][i]), inv=inv, pay=pay, mean=mean, k=len(combos)))
    return C.summarize(recs, nd)


def mk_partner_picks(pos: list[int]):
    def f(i: int):
        o = C.p3_order(i)
        a1, a2 = o[0], o[1]
        oth = o[2:]
        return [frozenset((a1, a2, oth[p])) for p in pos]
    return f


def section_trio() -> None:
    print("\n" + "=" * 100)
    print("§2 三連複 軸2車（p3上位2）+ 相手k点   ※配分=ダッチング（本番既定）")
    print("=" * 100)
    for w in ("explore", "confirm"):
        nd = days_all(w)
        idx = C.select("A", w)
        print(f"\n[{WIN[w]}] 母集団 {len(idx):,}R / {nd}日")
        print(C.HEAD)
        for name, pos in PARTNER_SETS.items():
            print(C.line(name, run_trio(idx, mk_partner_picks(pos), True, nd)))


def section_stake() -> None:
    print("\n" + "=" * 100)
    print("§3 配分 ダッチング vs 均等（代表的な腕のみ）")
    print("=" * 100)
    sel = ["相手1点(順位3)", "相手1点(順位5)", "相手2点(上位3,4)", "相手3点(上位3,4,5)",
           "相手3点(下位5,6,7)", "相手5点(総流し)"]
    for w in ("explore", "confirm"):
        nd = days_all(w)
        idx = C.select("A", w)
        print(f"\n[{WIN[w]}]")
        print(C.HEAD)
        for name in sel:
            pos = PARTNER_SETS[name]
            print(C.line(name + " ダッチ", run_trio(idx, mk_partner_picks(pos), True, nd)))
            print(C.line(name + " 均等", run_trio(idx, mk_partner_picks(pos), False, nd)))


# ───────────────────────── 三連単の腕 ─────────────────────────

def run_tf(idx: np.ndarray, picks, nd: int | None = None,
           min_po: float | None = None) -> dict:
    recs = []
    for i in idx:
        i = int(i)
        combos = picks(i)
        if not combos:
            continue
        st = C.tf_stakes(i, combos)
        if not C.tf_gate(i, st):
            continue
        po = [float(Z["PO"][i][C.CIDX[c]]) for c in combos]
        if min_po is not None and min(po) < min_po:
            continue
        inv, pay = C.tf_result(i, st)
        mean = sum(st[c] * p for c, p in zip(combos, po)) / len(combos)
        recs.append(dict(date=str(Z["DATE"][i]), inv=inv, pay=pay, mean=mean, k=len(combos)))
    return C.summarize(recs, nd)


def mk_tf_prob_top(n: int, restrict: str = "all", order: str = "prob"):
    """restrict: all=210点全体 / ax2=軸2車が3着内に入る目 / ax1first=軸1が1着"""
    def f(i: int):
        o = C.p3_order(i)
        a1, a2 = o[0], o[1]
        prob = Z["PROB"][i]
        po = Z["PO"][i]
        cand = []
        for t, c in enumerate(C.CANON):
            if restrict == "ax2" and not ({a1, a2} <= set(c)):
                continue
            if restrict == "ax1first" and c[0] != a1:
                continue
            if restrict == "ax12first" and c[0] not in (a1, a2):
                continue
            cand.append(t)
        if order == "prob":
            key = lambda t: -prob[t]                                        # noqa: E731
        else:                                                               # ev
            key = lambda t: -(prob[t] * (po[t] if np.isfinite(po[t]) else 0))  # noqa: E731
        cand.sort(key=key)
        return [C.CANON[t] for t in cand[:n]]
    return f


def section_tf() -> None:
    print("\n" + "=" * 100)
    print("§4 三連単（配分=均等・本番7T1/7T3と同じ）")
    print("=" * 100)
    arms = []
    for n in (1, 2, 3, 4, 6, 8, 12):
        arms.append((f"PROB上位{n}点(全体)", mk_tf_prob_top(n, "all", "prob")))
    for n in (2, 3, 4, 6):
        arms.append((f"PROB上位{n}点(軸2車3着内)", mk_tf_prob_top(n, "ax2", "prob")))
    for n in (2, 3, 4, 6):
        arms.append((f"PROB上位{n}点(軸1が1着)", mk_tf_prob_top(n, "ax1first", "prob")))
    for n in (2, 4, 6):
        arms.append((f"EV上位{n}点(全体)", mk_tf_prob_top(n, "all", "ev")))
    for w in ("explore", "confirm"):
        nd = days_all(w)
        idx = C.select("A", w)
        print(f"\n[{WIN[w]}] 母集団 {len(idx):,}R / {nd}日   ※ゲートは予測オッズ充足のみ")
        print(C.HEAD)
        for name, f in arms:
            print(C.line(name, run_tf(idx, f, nd)))
        print(f"\n[{WIN[w]}] 同じ腕に「1点でも予測<2.0倍なら見送り」を課した場合")
        print(C.HEAD)
        for name, f in arms:
            print(C.line(name, run_tf(idx, f, nd, min_po=2.0)))


# ───────────────────────── 1軸固定 + 2軸目2点 ─────────────────────────

def mk_hybrid_trio(n2: int, n3: int):
    """三連複: 軸1固定 × 2軸目候補n2車 × 相手n3車（重複は除く）。"""
    def f(i: int):
        o = C.p3_order(i)
        a1 = o[0]
        cands = o[1:1 + n2]
        others = o[1 + n2:1 + n2 + n3]
        out = []
        for b in cands:
            for c in others:
                s = frozenset((a1, b, c))
                if len(s) == 3 and s not in out:
                    out.append(s)
        return out
    return f


def mk_hybrid_tf(n2: int, n3: int):
    """三連単F: 1着=軸1 / 2着=2軸目候補n2車 / 3着=残りn3車。"""
    def f(i: int):
        o = C.p3_order(i)
        a1 = o[0]
        cands = o[1:1 + n2]
        thirds = [c for c in o[1:1 + n2 + n3] if True]
        out = []
        for b in cands:
            for c in thirds:
                if c != b:
                    out.append((a1, b, c))
        return out
    return f


def run_trio_weighted(idx, picks, weights_fn, nd=None):
    """賭け金に強弱を付ける版（weights_fn(i, combos)-> list[float]）。"""
    recs = []
    for i in idx:
        i = int(i)
        combos = picks(i)
        if not combos:
            continue
        wts = weights_fn(i, combos)
        po = [float(Z["TRIO_PO"][i][C.C3IDX[c]]) for c in combos]
        if any((not np.isfinite(x)) or x <= 0 for x in po):
            continue
        n_units = C.BUDGET // C.UNIT
        if n_units < len(combos):
            continue
        tot = sum(wts)
        units = [1] * len(combos)
        rest = n_units - len(combos)
        for j, x in enumerate(wts):
            units[j] += int(rest * x / tot)
        while sum(units) < n_units:
            j = min(range(len(units)), key=lambda k: units[k] / max(wts[k], 1e-12))
            units[j] += 1
        st = {c: u * C.UNIT for c, u in zip(combos, units)}
        if not C.trio_gate(i, st):
            continue
        inv, pay, mean = C.trio_result(i, st)
        recs.append(dict(date=str(Z["DATE"][i]), inv=inv, pay=pay, mean=mean, k=len(combos)))
    return C.summarize(recs, nd)


def section_hybrid() -> None:
    print("\n" + "=" * 100)
    print("§5 1軸固定 + 2軸目候補2点 + 3着流し（賭け金の強弱つき）")
    print("=" * 100)
    for w in ("explore", "confirm"):
        nd = days_all(w)
        idx = C.select("A", w)
        print(f"\n[{WIN[w]}] 三連複版  軸1固定 × 2軸目n2車 × 相手n3車")
        print(C.HEAD)
        for n2, n3 in ((2, 1), (2, 2), (2, 3), (3, 2), (2, 5)):
            f = mk_hybrid_trio(n2, n3)
            print(C.line(f"三連複 2軸目{n2}×相手{n3} ダッチ", run_trio(idx, f, True, nd)))
            print(C.line(f"三連複 2軸目{n2}×相手{n3} 均等", run_trio(idx, f, False, nd)))
        print(f"\n[{WIN[w]}] 三連単F版  1着=軸1 / 2着=n2車 / 3着=n3車（均等）")
        print(C.HEAD)
        for n2, n3 in ((2, 3), (2, 4), (2, 5), (3, 4)):
            print(C.line(f"三連単F 2着{n2}×3着{n3}", run_tf(idx, mk_hybrid_tf(n2, n3), nd)))
        print(f"\n[{WIN[w]}] 同上 + 「1点でも予測<2.0倍なら見送り」")
        print(C.HEAD)
        for n2, n3 in ((2, 3), (2, 4), (2, 5), (3, 4)):
            print(C.line(f"三連単F 2着{n2}×3着{n3}",
                         run_tf(idx, mk_hybrid_tf(n2, n3), nd, min_po=2.0)))


# ───────────────────────── 印一致で割る ─────────────────────────

def section_agree() -> None:
    print("\n" + "=" * 100)
    print("§6 印一致 / 不一致で割る（無商品の98.8%は印一致側）")
    print("=" * 100)
    sel = ["相手1点(順位3)", "相手1点(順位5)", "相手2点(上位3,4)", "相手3点(上位3,4,5)",
           "相手3点(下位5,6,7)", "相手5点(総流し)"]
    for w in ("explore", "confirm"):
        nd = days_all(w)
        for ag in (True, False):
            idx = C.select("A", w, agree=ag)
            lab = "印一致" if ag else "印不一致"
            print(f"\n[{WIN[w]}/{lab}] {len(idx):,}R")
            print(C.HEAD)
            for name in sel:
                print(C.line(name, run_trio(idx, mk_partner_picks(PARTNER_SETS[name]), True, nd)))


SECTIONS = dict(desc=section_desc, trio=section_trio, stake=section_stake,
                tf=section_tf, hybrid=section_hybrid, agree=section_agree)



# ═════════════════ §7 ゲートを制約と見た設計（追記 2026-08-27）═════════════════
# 🔴 ダッチングだと 平均想定払戻 = BUDGET / Σ(1/PO_j)。
#    ゲート「>2万円」⟺ **Σ(1/PO_j) < 0.5**（買い目の市場シェア < 0.5×0.75 = 37.5%）。
#    ＝ 型A（鉄板）で点数を増やせないのは配当が安いからではなく、この不等式のため。

def trio_prob(i: int) -> np.ndarray:
    """35点の三連複確率（位置別合成PLの三連単確率を組へ畳む）。"""
    p = Z["PROB"][i]
    out = np.zeros(35)
    for t, c in enumerate(C.CANON):
        out[C.C3IDX[frozenset(c)]] += p[t]
    return out


def cost_of(i: int) -> np.ndarray:
    po = Z["TRIO_PO"][i].astype(np.float64)
    return np.where(np.isfinite(po) & (po > 0), 1.0 / np.maximum(po, 1e-9), np.inf)


def mk_knapsack(kmax: int, budget: float = 0.5, by: str = "density"):
    """Σ(1/PO) < budget を守りながら三連複確率の和を最大化（貪欲・点数上限 kmax）。
    by: density=確率/コスト(=確率×予測オッズ=EV) 順 / prob=確率順"""
    def f(i: int):
        pr, cs = trio_prob(i), cost_of(i)
        order = np.argsort(-(pr * np.where(np.isfinite(cs), 1.0 / np.maximum(cs, 1e-9), 0))) \
            if by == "density" else np.argsort(-pr)
        got, tot = [], 0.0
        for j in order:
            if not np.isfinite(cs[j]) or len(got) >= kmax:
                continue
            if tot + cs[j] >= budget:
                continue
            got.append(j); tot += cs[j]
        return [frozenset(C.CANON3[j]) for j in got]
    return f


def mk_trio_prob_top(n: int):
    def f(i: int):
        pr = trio_prob(i)
        return [frozenset(C.CANON3[j]) for j in np.argsort(-pr)[:n]]
    return f


def mk_axis1_grid(bs: list[int], cs: list[int]):
    """軸1（p3 1位）固定 × 2軸目=p3順位 bs × 3着=p3順位 cs。順位は 2..7。"""
    def f(i: int):
        o = C.p3_order(i)
        out = []
        for b in bs:
            for c in cs:
                s = frozenset((o[0], o[b - 1], o[c - 1]))
                if len(s) == 3 and s not in out:
                    out.append(s)
        return out
    return f


def section_gate() -> None:
    print("\n" + "=" * 100)
    print("§7 ゲートを制約と見る（Σ1/予測オッズ < 0.5 のもとで確率を最大化）")
    print("=" * 100)
    arms = [(f"確率上位{n}点(制約なし)", mk_trio_prob_top(n)) for n in (1, 2, 3, 5)]
    arms += [(f"ゲート内 EV順 最大{k}点", mk_knapsack(k)) for k in (1, 2, 3, 4, 5, 6, 8)]
    arms += [(f"ゲート内 確率順 最大{k}点", mk_knapsack(k, by="prob")) for k in (2, 3, 4, 6)]
    for w in ("explore", "confirm"):
        nd = days_all(w)
        idx = C.select("A", w)
        print(f"\n[{WIN[w]}] 母集団 {len(idx):,}R / {nd}日")
        print(C.HEAD)
        for name, f in arms:
            print(C.line(name, run_trio(idx, f, True, nd)))


AXIS1_GRID = {
    "軸1×2着{2,3}×3着{4,5}": ([2, 3], [4, 5]),
    "軸1×2着{2,3}×3着{5,6}": ([2, 3], [5, 6]),
    "軸1×2着{2,3,4}×3着{5,6}": ([2, 3, 4], [5, 6]),
    "軸1×2着{2,3,4}×3着{5,6,7}": ([2, 3, 4], [5, 6, 7]),
    "軸1×2着{2,3}×3着{4,5,6}": ([2, 3], [4, 5, 6]),
    "軸1×2着{2,3,4}×3着{4,5,6}": ([2, 3, 4], [4, 5, 6]),
    "軸1×2着{2,3}×3着{6,7}": ([2, 3], [6, 7]),
    "軸1×2着{2}×3着{3,4,5}": ([2], [3, 4, 5]),
    "軸1×2着{2,3,4,5}×3着{5,6}": ([2, 3, 4, 5], [5, 6]),
    "軸1×2着{3,4}×3着{5,6}": ([3, 4], [5, 6]),
    "軸1×2着{2,3,4}×3着{6,7}": ([2, 3, 4], [6, 7]),
    "軸1×2着{2,3,4,5}×3着{6,7}": ([2, 3, 4, 5], [6, 7]),
}


def section_grid() -> None:
    print("\n" + "=" * 100)
    print("§8 軸1固定 × 2着帯 × 3着帯 の格子（三連複・ダッチング）")
    print("=" * 100)
    for w in ("explore", "confirm"):
        nd = days_all(w)
        idx = C.select("A", w)
        print(f"\n[{WIN[w]}] 母集団 {len(idx):,}R / {nd}日")
        print(C.HEAD)
        for name, (bs, cs) in AXIS1_GRID.items():
            print(C.line(name, run_trio(idx, mk_axis1_grid(bs, cs), True, nd)))


SECTIONS["gate"] = section_gate
SECTIONS["grid"] = section_grid



# ═════════════════ §9 販売の帯を動かすダイヤル ═════════════════
# 平均想定払戻 = BUDGET / Σ(1/PO) なので、**買い目の市場シェア上限を動かす**と
# そのまま「平均想定払戻の帯」が動く。売上KPI（3.0〜5.0万が最も売れる）に合わせる操作点。

def section_dial() -> None:
    print("\n" + "=" * 100)
    print("§9 市場シェア上限 B（Σ1/予測オッズ < B）を振る  ※確率順・点数上限5")
    print("   平均想定払戻 ≒ 10,000 / B  →  B=0.20:5.0万 / 0.25:4.0万 / 0.33:3.0万 / 0.5:2.0万")
    print("=" * 100)
    for w in ("explore", "confirm"):
        nd = days_all(w)
        idx = C.select("A", w)
        print(f"\n[{WIN[w]}] 母集団 {len(idx):,}R / {nd}日")
        print(C.HEAD)
        for B in (0.18, 0.20, 0.22, 0.25, 0.28, 0.30, 0.33, 0.40, 0.50):
            for k in (3, 5):
                print(C.line(f"B={B:.2f} 上限{k}点",
                             run_trio(idx, mk_knapsack(k, budget=B, by="prob"), True, nd)))


# ═════════════════ §10 採用候補の素性 ═════════════════

def _recs(idx, picks, tilt=True):
    out = []
    for i in idx:
        i = int(i)
        combos = picks(i)
        if not combos:
            continue
        st = C.trio_stakes(i, combos, tilt=tilt)
        if st is None or not C.trio_gate(i, st):
            continue
        inv, pay, mean = C.trio_result(i, st)
        out.append(dict(i=i, date=str(Z["DATE"][i]), inv=inv, pay=pay, mean=mean,
                        k=len(combos), combos=combos))
    return out


def _boot_shown(recs, n=2000, seed=0):
    rng = np.random.default_rng(seed)
    v = np.array([1.0 if (r["pay"] > r["inv"]) else 0.0 for r in recs])
    b = np.array([v[rng.integers(0, len(v), len(v))].mean() for _ in range(n)]) * 100
    return np.percentile(b, 2.5), np.percentile(b, 97.5)


CANDIDATES = {
    "D28 B=0.28 確率順 上限5点": (mk_knapsack(5, 0.28, "prob"), True),
    "D33 B=0.33 確率順 上限5点": (mk_knapsack(5, 0.33, "prob"), True),
    "D40 B=0.40 確率順 上限5点": (mk_knapsack(5, 0.40, "prob"), True),
    "K5 ゲート内確率順 上限5点": (mk_knapsack(5, 0.5, "prob"), True),
    "G6 軸1×2着{2,3,4}×3着{5,6}": (mk_axis1_grid([2, 3, 4], [5, 6]), True),
    "P1 三連複 確率上位1点": (mk_trio_prob_top(1), True),
    "T1 三連複 軸2+順位3の1点": (mk_partner_picks([0]), True),
}


def section_final() -> None:
    print("\n" + "=" * 100)
    print("§10 採用候補の素性（払戻分布・月次・信頼区間・買い目の中身）")
    print("=" * 100)
    for name, (f, tilt) in CANDIDATES.items():
        print(f"\n── {name} ──")
        for w in ("explore", "confirm"):
            nd = days_all(w)
            r = _recs(C.select("A", w), f, tilt)
            s = C.summarize(r, nd)
            lo, hi = _boot_shown(r)
            pays = np.array([x["pay"] for x in r if x["pay"] > x["inv"]])
            allp = np.array([x["pay"] for x in r])
            print(f"  [{WIN[w]}] {s['n']:,}件 {s['perday']:.2f}件/日 平均{s['k']:.2f}点"
                  f"  表示的中 {s['shown']:.2f}% CI[{lo:.2f},{hi:.2f}]  ガミ {s['gami']:.2f}%")
            print(f"          払戻中央 {s['med_pay']:,.0f}  平均払戻中央 {s['med_mean']:,.0f}"
                  f"  2倍+/日 {s['two_per_day']:.2f}  ROI {s['roi']:.1f}%")
            if len(pays):
                print(f"          表示的中の払戻 p25 {np.percentile(pays,25):,.0f} /"
                      f" 中央 {np.median(pays):,.0f} / p75 {np.percentile(pays,75):,.0f} /"
                      f" p95 {np.percentile(pays,95):,.0f}"
                      f"  5万+ {np.mean(allp>=50_000)*len(r)/nd:.2f}件/日"
                      f"  10万+ {np.mean(allp>=100_000)*len(r)/nd:.2f}件/日")
        # 買い目の中身（確認窓）
        r = _recs(C.select("A", "confirm"), f, tilt)
        cnt = {}
        for x in r:
            o = C.p3_order(x["i"])
            pos = {c: j + 1 for j, c in enumerate(o)}
            for cb in x["combos"]:
                key = tuple(sorted(pos[c] for c in cb))
                cnt[key] = cnt.get(key, 0) + 1
        tot = sum(cnt.values())
        top = sorted(cnt.items(), key=lambda kv: -kv[1])[:8]
        print("          買い目のp3順位構成(確認窓): " +
              " ".join(f"{'-'.join(map(str,k))}:{v/tot*100:.0f}%" for k, v in top))


def section_month() -> None:
    print("\n" + "=" * 100)
    print("§11 月次の安定性（確認窓 2026・K5 と G6）")
    print("=" * 100)
    for name in ("D28 B=0.28 確率順 上限5点", "D40 B=0.40 確率順 上限5点",
                 "K5 ゲート内確率順 上限5点", "G6 軸1×2着{2,3,4}×3着{5,6}"):
        f, tilt = CANDIDATES[name]
        print(f"\n── {name} ──")
        for w in ("explore", "confirm"):
            r = _recs(C.select("A", w), f, tilt)
            by = {}
            for x in r:
                by.setdefault(x["date"][:7], []).append(x)
            print(f"  [{WIN[w]}] " + " ".join(
                f"{m}:{len(v)}件/{sum(1 for y in v if y['pay']>y['inv'])/len(v)*100:.0f}%"
                for m, v in sorted(by.items())))


SECTIONS["dial"] = section_dial
SECTIONS["final"] = section_final
SECTIONS["month"] = section_month



# ═════════════════ §12 三連単も同じ制約で組む（ダッチング）═════════════════

def tf_stakes_tilt(i: int, combos: list[tuple]) -> dict | None:
    po = [float(Z["PO"][i][C.CIDX[c]]) for c in combos]
    if any((not np.isfinite(x)) or x <= 0 for x in po):
        return None
    w = [1.0 / x for x in po]
    n_units = C.BUDGET // C.UNIT
    if n_units < len(combos):
        return None
    tot = sum(w)
    units = [1] * len(combos)
    rest = n_units - len(combos)
    for j, x in enumerate(w):
        units[j] += int(rest * x / tot)
    while sum(units) < n_units:
        j = min(range(len(units)), key=lambda k: units[k] / max(w[k], 1e-12))
        units[j] += 1
    return {c: u * C.UNIT for c, u in zip(combos, units)}


def mk_tf_knapsack(kmax: int, budget: float):
    def f(i: int):
        pr, po = Z["PROB"][i].astype(np.float64), Z["PO"][i].astype(np.float64)
        cs = np.where(np.isfinite(po) & (po > 0), 1.0 / np.maximum(po, 1e-9), np.inf)
        got, tot = [], 0.0
        for j in np.argsort(-pr):
            if not np.isfinite(cs[j]) or len(got) >= kmax:
                continue
            if tot + cs[j] >= budget:
                continue
            got.append(int(j)); tot += cs[j]
        return [C.CANON[j] for j in got]
    return f


def run_tf_tilt(idx, picks, nd=None):
    recs = []
    for i in idx:
        i = int(i)
        combos = picks(i)
        if not combos:
            continue
        st = tf_stakes_tilt(i, combos)
        if st is None:
            continue
        po = {c: float(Z["PO"][i][C.CIDX[c]]) for c in combos}
        if min(po.values()) < C.MIN_POINT_ODDS:
            continue
        mean = sum(st[c] * po[c] for c in combos) / len(combos)
        if mean <= C.MIN_MEAN_PAYOUT:          # 三連複と同じ土俵で見るため同じゲートを課す
            continue
        inv, pay = C.tf_result(i, st)
        recs.append(dict(date=str(Z["DATE"][i]), inv=inv, pay=pay, mean=mean, k=len(combos)))
    return C.summarize(recs, nd)


def section_tftilt() -> None:
    print("\n" + "=" * 100)
    print("§12 三連単をダッチング＋市場シェア制約で組む（三連複と同条件で比較）")
    print("=" * 100)
    for w in ("explore", "confirm"):
        nd = days_all(w)
        idx = C.select("A", w)
        print(f"\n[{WIN[w]}] 母集団 {len(idx):,}R / {nd}日")
        print(C.HEAD)
        for B in (0.25, 0.33, 0.40, 0.50):
            for k in (3, 5, 8):
                print(C.line(f"三連単 B={B:.2f} 上限{k}点",
                             run_tf_tilt(idx, mk_tf_knapsack(k, B), nd)))


def section_ratio() -> None:
    print("\n" + "=" * 100)
    print("§13 予測オッズと実払戻のずれ（採用候補 D33・確認窓）")
    print("=" * 100)
    f, _ = CANDIDATES["D33 B=0.33 確率順 上限5点"]
    for w in ("explore", "confirm"):
        r = _recs(C.select("A", w), f)
        hit = [x for x in r if x["pay"] > 0]
        ratio = np.array([x["pay"] / x["mean"] for x in hit])
        rng = np.random.default_rng(0)
        roi = np.array([sum(x["pay"] for x in [r[j] for j in rng.integers(0, len(r), len(r))])
                        / sum(x["inv"] for x in [r[j] for j in rng.integers(0, len(r), len(r))])
                        for _ in range(400)]) * 100
        print(f"  [{WIN[w]}] 実払戻/想定 中央 {np.median(ratio):.3f}"
              f"  p25 {np.percentile(ratio,25):.3f} p75 {np.percentile(ratio,75):.3f}"
              f"  想定超え {np.mean(ratio>=1)*100:.1f}%"
              f"   ROI 95%CI[{np.percentile(roi,2.5):.1f},{np.percentile(roi,97.5):.1f}]")


SECTIONS["tftilt"] = section_tftilt
SECTIONS["ratio"] = section_ratio



# ═════════════════ §14 三連単ダッチの操作点さがし（点数上限5固定）═════════════════
# ⚠️ 上限を8点以上にすると、端数の1点(100円)に超高オッズが混ざり
#    「平均想定払戻」だけが名目上跳ねる（§12 の B=0.25 上限8点で 4.5万→6.2万）。
#    100円の量子化でダッチングが崩れるだけなのでゲート稼ぎにしかならない。上限は5点まで。

def _tf_recs(idx, picks):
    out = []
    for i in idx:
        i = int(i)
        combos = picks(i)
        if not combos:
            continue
        st = tf_stakes_tilt(i, combos)
        if st is None:
            continue
        po = {c: float(Z["PO"][i][C.CIDX[c]]) for c in combos}
        if min(po.values()) < C.MIN_POINT_ODDS:
            continue
        mean = sum(st[c] * po[c] for c in combos) / len(combos)
        if mean <= C.MIN_MEAN_PAYOUT:
            continue
        inv, pay = C.tf_result(i, st)
        out.append(dict(i=i, date=str(Z["DATE"][i]), inv=inv, pay=pay, mean=mean, k=len(combos)))
    return out


def section_tfdial() -> None:
    print("\n" + "=" * 100)
    print("§14 三連単ダッチ 市場シェア上限 B の細かい掃引（上限5点）")
    print("=" * 100)
    for w in ("explore", "confirm"):
        nd = days_all(w)
        idx = C.select("A", w)
        print(f"\n[{WIN[w]}] 母集団 {len(idx):,}R / {nd}日")
        print(C.HEAD)
        for B in (0.22, 0.25, 0.28, 0.30, 0.33, 0.36, 0.40, 0.45):
            print(C.line(f"三連単 B={B:.2f}", run_tf_tilt(idx, mk_tf_knapsack(5, B), nd)))


def section_tffinal() -> None:
    print("\n" + "=" * 100)
    print("§15 三連単ダッチ B=0.30 / 0.33 の素性と月次")
    print("=" * 100)
    for B in (0.30, 0.33):
        f = mk_tf_knapsack(5, B)
        print(f"\n── 三連単ダッチ B={B:.2f} 上限5点 ──")
        for w in ("explore", "confirm"):
            nd = days_all(w)
            r = _tf_recs(C.select("A", w), f)
            s = C.summarize(r, nd)
            lo, hi = _boot_shown(r)
            allp = np.array([x["pay"] for x in r])
            hits = allp[allp > 0]
            rng = np.random.default_rng(0)
            roi = []
            for _ in range(400):
                j = rng.integers(0, len(r), len(r))
                roi.append(sum(r[k]["pay"] for k in j) / sum(r[k]["inv"] for k in j) * 100)
            ratio = np.array([x["pay"] / x["mean"] for x in r if x["pay"] > 0])
            print(f"  [{WIN[w]}] {s['n']:,}件 {s['perday']:.2f}件/日 平均{s['k']:.2f}点"
                  f"  表示的中 {s['shown']:.2f}% CI[{lo:.2f},{hi:.2f}]  ガミ {s['gami']:.2f}%")
            print(f"          払戻中央 {s['med_pay']:,.0f}  平均払戻中央 {s['med_mean']:,.0f}"
                  f"  2倍+/日 {s['two_per_day']:.2f}"
                  f"  ROI {s['roi']:.1f}% CI[{np.percentile(roi,2.5):.1f},{np.percentile(roi,97.5):.1f}]")
            print(f"          的中払戻 p25 {np.percentile(hits,25):,.0f} / p75 {np.percentile(hits,75):,.0f}"
                  f" / p95 {np.percentile(hits,95):,.0f}   5万+ {np.mean(allp>=50_000)*len(r)/nd:.2f}件/日"
                  f"   実払戻/想定 中央 {np.median(ratio):.3f}")
            by = {}
            for x in r:
                by.setdefault(x["date"][:7], []).append(x)
            print("          月次: " + " ".join(
                f"{m[2:]}:{sum(1 for y in v if y['pay']>y['inv'])/len(v)*100:.0f}%"
                for m, v in sorted(by.items())))


SECTIONS["tfdial"] = section_tfdial
SECTIONS["tffinal"] = section_tffinal


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    for k, f in SECTIONS.items():
        if which in ("all", k):
            f()
