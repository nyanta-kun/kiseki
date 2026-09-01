#!/usr/bin/env python3
"""7M1: 「3・4番手の2点」と「残り3点」を条件で振り分けられるか（2026-08-26・ユーザー案）。

> 3番手、4番手の相手2点で買うのはいいと思います。この2点が飛びそうであれば
> 他3点と振り分ける条件ができないかと思います。

台: `data/exp/7m1_partner_count_{2025,2026}.jsonl`（`exp_7m1_partner_count.py` が作る）
    予測オッズ・PL同時確率・p3・印・実着順・実払戻(円/100円)が1レース1行。
    2025 は train_end 2024-12-31 の vintage オッズモデルで再構築した独立窓。

採点は**本番と同じ**: `stake_allocation.tilted_stakes(predicted_odds=...)` の
ダッチング（賭け金 ∝ 1/予測オッズ）で 1レース 10,000円。
🔴 均等配分で採点すると増点が必ず不利に出る（CLAUDE.md の3例目）。

判断指標は **件数・的中率・2倍以上/日・倍率中央・ガミ率**。
🔴 この層は ROI で採否を決められない（±2.5pt に収めるのに約15.6年）。
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from statistics import median

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.chdir(REPO)

from src.stake_allocation import tilted_stakes                      # noqa: E402
from src.strategy_wt import rank_7m1_select_legs                    # noqa: E402

BUDGET, UNIT = 10_000, 100


def load(path: str) -> list[dict]:
    out = []
    for line in open(path):
        r = json.loads(line)
        r["p3"] = {int(k): v / 100.0 for k, v in r["p3"].items()}
        r["odds"] = {int(k): v for k, v in r["odds"].items()}
        r["prob"] = {int(k): v for k, v in r["prob"].items()}
        r["mark"] = {int(k): v for k, v in r["mark"].items()}
        out.append(r)
    return out


ROWS = load("data/exp/7m1_partner_count_2025.jsonl") + load("data/exp/7m1_partner_count_2026.jsonl")
WINDOWS = [("2025 独立", "2025-01-01", "2025-12-31"),
           ("2026 掃引", "2026-01-01", "2026-04-30"),
           ("2026 確認", "2026-05-01", "2026-12-31")]


def ranked(r: dict) -> list[int]:
    """相手5車を p3 の降順に並べる（本番の位置規則と同じ並び）。"""
    return sorted(r["others"], key=lambda c: (-r["p3"].get(c, 0.0), c))


def arm_current(r: dict) -> list[int]:
    """現行＝位置規則（`rank_7m1_select_legs` をそのまま呼ぶ）。"""
    return rank_7m1_select_legs(list(r["others"]), r["p3"])


def arm_rest(r: dict) -> list[int]:
    """現行が買わない残り。"""
    cur = set(arm_current(r))
    return [c for c in ranked(r) if c not in cur]


def score(r: dict, legs: list[int]) -> tuple[int, float, int]:
    """(投資, 払戻, 点数)。本番と同じダッチング配分。"""
    if not legs:
        return 0, 0.0, 0
    stakes, _ = tilted_stakes(list(legs), None, r["p3"], BUDGET, UNIT,
                              predicted_odds=r["odds"])
    third = set(r["top3"]) - {r["a1"], r["a2"]}
    pay = 0.0
    if len(third) == 1:
        t = third.pop()
        if t in stakes:
            pay = stakes[t] / 100.0 * r["trio"]
    return sum(stakes.values()), pay, len(legs)


def report(name: str, pick, rows: list[dict]) -> dict:
    inv = pay = 0.0
    hits: list[float] = []
    ratios: list[float] = []
    days = set()
    n = ng = 0
    for r in rows:
        legs = pick(r)
        if not legs:
            continue
        i, p, _ = score(r, legs)
        if i <= 0:
            continue
        n += 1
        days.add(r["date"])
        inv += i
        pay += p
        if p > 0:
            hits.append(p)
            ratios.append(p / i)
            if p < i:
                ng += 1
    nd = max(len(days), 1)
    two = sum(1 for x in ratios if x >= 2.0)
    return dict(name=name, n=n, perday=n / nd, hit=len(hits) / n * 100 if n else 0,
                gami=ng / len(hits) * 100 if hits else 0,
                two=two / nd, med=median(ratios) if ratios else 0,
                roi=pay / inv * 100 if inv else 0)


def show(rows: list[dict], arms: list[tuple], title: str) -> None:
    print(f"\n[{title}]  n={len(rows):,}")
    print("  腕                     件/日   的中%   ガミ%  2倍+/日  倍率中央   ROI%")
    for nm, f in arms:
        s = report(nm, f, rows)
        print(f"  {nm:20s} {s['perday']:6.2f} {s['hit']:7.2f} {s['gami']:6.2f}"
              f" {s['two']:8.2f} {s['med']:9.2f} {s['roi']:7.1f}")


def oracle(r: dict) -> list[int]:
    """上限: 3着車を含むほうのグループを事前に知って選ぶ（実現不能）。"""
    third = set(r["top3"]) - {r["a1"], r["a2"]}
    cur = arm_current(r)
    if len(third) == 1 and third & set(cur):
        return cur
    rest = arm_rest(r)
    if len(third) == 1 and third & set(rest):
        return rest
    return cur


def main() -> None:
    # ── 0. 現行が実際に何点買っているか / 相手の p3 順位はどこか
    cnt = defaultdict(int)
    pos = defaultdict(int)
    for r in ROWS:
        legs = arm_current(r)
        cnt[len(legs)] += 1
        rk = ranked(r)
        for c in legs:
            pos[rk.index(c) + 1] += 1
    tot = sum(pos.values())
    print(f"全 {len(ROWS):,}R  現行の点数分布: "
          + " / ".join(f"{k}点 {v} ({v/len(ROWS)*100:.1f}%)" for k, v in sorted(cnt.items())))
    print("  買っている相手の p3 順位: "
          + " ".join(f"{k}番手 {v/tot*100:.1f}%" for k, v in sorted(pos.items())))

    # ── 1. 二軸そろい時に3着車がどの順位から来るか
    print("\n[二軸そろい時に3着へ来る相手の p3 順位]")
    for wname, lo, hi in WINDOWS:
        rows = [r for r in ROWS if lo <= r["date"] <= hi]
        al = [r for r in rows if {r["a1"], r["a2"]} <= set(r["top3"])]
        d = defaultdict(int)
        for r in al:
            t = (set(r["top3"]) - {r["a1"], r["a2"]}).pop()
            d[ranked(r).index(t) + 1] += 1
        n = sum(d.values())
        inA = sum(1 for r in al
                  if (set(r["top3"]) - {r["a1"], r["a2"]}) & set(arm_current(r)))
        print(f"  {wname}: 二軸そろい {len(al):,}/{len(rows):,} ({len(al)/len(rows)*100:.1f}%)  "
              + " ".join(f"{k}番手 {d[k]/n*100:.1f}%" for k in sorted(d))
              + f"   → 現行が拾えているのは {inA/len(al)*100:.1f}%")

    # ── 2. 腕の比較（オラクル＝振り分けの上限）
    arms = [("現行(3・4番手)", arm_current), ("残り3点", arm_rest),
            ("総流し5点", lambda r: ranked(r)),
            ("オラクル(上限)", oracle)]
    for wname, lo, hi in WINDOWS:
        show([r for r in ROWS if lo <= r["date"] <= hi], arms, wname)


def _run():
    main()
    scan()
    sweep()
    sweep3()
    sweep_add()
    compare()


# ══════════════════════════════════════════════════════════════════════════
# 振り分けの条件探し
#
# 決めたいのは「3着が現行の買い目(A=3・4番手)から来るか、残り3点(B=1・2・5番手)か」。
# 判定は**朝の入稿時点で引ける量だけ**で作る（予測オッズ・PL同時確率・p3・印）。
# ══════════════════════════════════════════════════════════════════════════

def feats(r: dict) -> dict[str, float]:
    rk = ranked(r)
    A = set(arm_current(r))
    B = [c for c in rk if c not in A]
    q = {c: 1.0 / r["odds"][c] for c in r["others"] if r["odds"].get(c)}
    p = r["prob"]
    p3 = r["p3"]
    qA = sum(q.get(c, 0.0) for c in A)
    qB = sum(q.get(c, 0.0) for c in B)
    pA = sum(p.get(c, 0.0) for c in A)
    pB = sum(p.get(c, 0.0) for c in B)
    f1, f2 = rk[0], rk[1]
    return {
        # 市場（予測オッズ）が見る A/B の取り分
        "q_share_A": qA / (qA + qB) if qA + qB else 0.0,
        # モデルの同時確率が見る A/B の取り分
        "p_share_A": pA / (pA + pB) if pA + pB else 0.0,
        # 1番手が抜けているか（抜けているほど B へ寄せたい）
        "q_top1": q.get(f1, 0.0),
        "p3_top1": p3.get(f1, 0.0),
        # 1番手と3番手の p3 差（小さいほど A にも目がある）
        "p3_gap_1_3": p3.get(f1, 0.0) - p3.get(rk[2], 0.0),
        "p3_gap_1_2": p3.get(f1, 0.0) - p3.get(f2, 0.0),
        # 上位2枚の合計3着内率
        "p3_sum_top2": p3.get(f1, 0.0) + p3.get(f2, 0.0),
        # A 側の一番良い点の予測オッズ（高いほど A は薄い）
        "odds_bestA": min((r["odds"].get(c, 1e9) for c in A), default=1e9),
        # 印: 1番手が ○ か
        "top1_is_maru": 1.0 if r["mark"].get(f1) == 2 else 0.0,
    }


def auc(pairs: list[tuple[float, int]]) -> float:
    """ラベル1（=3着がAから来た）に対する順位AUC。"""
    pos = [v for v, y in pairs if y == 1]
    neg = [v for v, y in pairs if y == 0]
    if not pos or not neg:
        return 0.5
    allv = sorted(v for v, _ in pairs)
    rank = {}
    i = 0
    while i < len(allv):
        j = i
        while j + 1 < len(allv) and allv[j + 1] == allv[i]:
            j += 1
        rr = (i + j) / 2.0 + 1
        rank[allv[i]] = rr
        i = j + 1
    s = sum(rank[v] for v in pos)
    return (s - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))


def scan() -> None:
    print("\n[判別力] ラベル = 二軸そろい時に3着が A(現行の買い目) から来た  AUC")
    names = list(feats(ROWS[0]).keys())
    print(f"  {'特徴':14s}" + "".join(f"{w:>12s}" for w, _, _ in WINDOWS))
    for nm in names:
        line = f"  {nm:14s}"
        for _, lo, hi in WINDOWS:
            rows = [r for r in ROWS if lo <= r["date"] <= hi
                    and {r["a1"], r["a2"]} <= set(r["top3"])]
            pr = []
            for r in rows:
                y = 1 if (set(r["top3"]) - {r["a1"], r["a2"]}) & set(arm_current(r)) else 0
                pr.append((feats(r)[nm], y))
            line += f"{auc(pr):12.4f}"
        print(line)




def sweep() -> None:
    """q_share_A の閾値で A / B を振り分ける。掃引は 2026 前半だけを見て決める。"""
    print("\n[振り分け] q_share_A >= θ なら A(現行) / さもなくば B(残り3点)")
    print("   θ    窓        件/日   的中%   ガミ%  2倍+/日  倍率中央   ROI%   A採用%")
    for th in (0.00, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 1.01):
        def pick(r, th=th):
            return arm_current(r) if feats(r)["q_share_A"] >= th else arm_rest(r)
        for wname, lo, hi in WINDOWS:
            rows = [r for r in ROWS if lo <= r["date"] <= hi]
            s = report("sw", pick, rows)
            share = sum(1 for r in rows if feats(r)["q_share_A"] >= th) / len(rows) * 100
            print(f"  {th:4.2f}  {wname:8s} {s['perday']:6.2f} {s['hit']:7.2f} {s['gami']:6.2f}"
                  f" {s['two']:8.2f} {s['med']:9.2f} {s['roi']:7.1f} {share:8.1f}")
        print()


def sweep3() -> None:
    """3択: 高いほど A / 低いほど B / 中間は総流し5点。"""
    print("[3択] q>=hi→A / q<=lo→B / 中間→総流し5点")
    print("  lo   hi   窓        件/日   的中%   ガミ%  2倍+/日  倍率中央   ROI%")
    for lo_t, hi_t in ((0.10, 0.20), (0.15, 0.25), (0.15, 0.30), (0.20, 0.30), (0.10, 0.30)):
        def pick(r, lo_t=lo_t, hi_t=hi_t):
            q = feats(r)["q_share_A"]
            if q >= hi_t:
                return arm_current(r)
            if q <= lo_t:
                return arm_rest(r)
            return ranked(r)
        for wname, lo, hi in WINDOWS:
            rows = [r for r in ROWS if lo <= r["date"] <= hi]
            s = report("sw3", pick, rows)
            print(f"  {lo_t:4.2f} {hi_t:4.2f} {wname:8s} {s['perday']:6.2f} {s['hit']:7.2f}"
                  f" {s['gami']:6.2f} {s['two']:8.2f} {s['med']:9.2f} {s['roi']:7.1f}")
        print()




def sweep_add() -> None:
    """入れ替えではなく『A に保険を1点足す』形。足す相手と、足す条件を変える。"""
    def add_rank(r, idx):
        rk = ranked(r)
        cur = arm_current(r)
        c = rk[idx]
        return cur if c in cur else cur + [c]

    variants = [
        ("A のみ（現行）", lambda r: arm_current(r)),
        ("A+1番手 常時", lambda r: add_rank(r, 0)),
        ("A+2番手 常時", lambda r: add_rank(r, 1)),
        ("A+1,2番手 常時", lambda r: sorted(set(arm_current(r)) | {ranked(r)[0], ranked(r)[1]})),
    ]
    for th in (0.15, 0.20, 0.25):
        variants.append((f"A+1番手 q<{th:.2f}のみ",
                         lambda r, th=th: add_rank(r, 0) if feats(r)["q_share_A"] < th
                         else arm_current(r)))
    print("\n[保険を足す] A は常に買い、条件つきで上位を足す")
    print("  腕                     窓        件/日   的中%   ガミ%  2倍+/日  倍率中央   ROI%")
    for nm, f in variants:
        for wname, lo, hi in WINDOWS:
            rows = [r for r in ROWS if lo <= r["date"] <= hi]
            s = report(nm, f, rows)
            print(f"  {nm:22s} {wname:8s} {s['perday']:6.2f} {s['hit']:7.2f} {s['gami']:6.2f}"
                  f" {s['two']:8.2f} {s['med']:9.2f} {s['roi']:7.1f}")
        print()




def paired(nameA: str, fA, nameB: str, fB) -> None:
    """レース単位ペアブートストラップ（判断指標のみ）。"""
    import random
    print(f"\n[{nameA} − {nameB}] レース単位ペア bootstrap 95%CI")
    print("  窓        Δ的中pt          Δ2倍+/日            Δ倍率中央        ΔROIpt")
    for wname, lo, hi in WINDOWS:
        rows = [r for r in ROWS if lo <= r["date"] <= hi]
        recs = []
        for r in rows:
            iA, pA, _ = score(r, fA(r))
            iB, pB, _ = score(r, fB(r))
            if iA <= 0 or iB <= 0:
                continue
            recs.append((r["date"], iA, pA, iB, pB))
        nd = len(set(x[0] for x in recs))
        rng = random.Random(17)
        dh = []; dt = []; dm = []; dr = []
        for _ in range(2000):
            s = [recs[rng.randrange(len(recs))] for _ in range(len(recs))]
            hA = sum(1 for x in s if x[2] > 0) / len(s)
            hB = sum(1 for x in s if x[4] > 0) / len(s)
            tA = sum(1 for x in s if x[2] >= 2 * x[1]) / nd * (nd / len(s) * len(s) / nd)
            tB = sum(1 for x in s if x[4] >= 2 * x[3]) / nd
            tA = sum(1 for x in s if x[2] >= 2 * x[1]) / nd
            rA = [x[2] / x[1] for x in s if x[2] > 0]
            rB = [x[4] / x[3] for x in s if x[4] > 0]
            dh.append((hA - hB) * 100); dt.append(tA - tB)
            dm.append((median(rA) if rA else 0) - (median(rB) if rB else 0))
            dr.append((sum(x[2] for x in s) / sum(x[1] for x in s)
                       - sum(x[4] for x in s) / sum(x[3] for x in s)) * 100)
        def ci(v):
            v = sorted(v)
            return f"{sum(v)/len(v):+6.2f}[{v[int(.025*len(v))]:+6.2f},{v[int(.975*len(v))]:+6.2f}]"
        print(f"  {wname:8s} {ci(dh):22s} {ci(dt):22s} {ci(dm):22s} {ci(dr)}")


def compare() -> None:
    def add2(r):
        cur = arm_current(r); c = ranked(r)[1]
        return cur if c in cur else cur + [c]
    paired("A+2番手", add2, "現行A", arm_current)
    paired("A+2番手", add2, "残り3点B", arm_rest)

    def add1(r):
        cur = arm_current(r); c = ranked(r)[0]
        return cur if c in cur else cur + [c]
    paired("A+1番手", add1, "A+2番手", add2)

    def addq(r):
        """市場が A を薄いと見るときだけ 1番手、そうでなければ 2番手を足す。"""
        cur = arm_current(r)
        c = ranked(r)[0] if feats(r)["q_share_A"] < 0.20 else ranked(r)[1]
        return cur if c in cur else cur + [c]
    paired("A+条件つき1/2番手", addq, "A+2番手", add2)




# ══════════════════════════════════════════════════════════════════════════
# 【順位の呼び方の整理】2026-08-26
#   軸は全体の指数1・2番手。したがって
#     相手候補 o1 = 全体3番手 / o2 = 全体4番手 / o3..o5 = 全体5〜7番手
#   ユーザー案「3番手・4番手の相手2点」= **o1, o2**（現行が捨てている側）
#   現行の位置規則が買っているのは **o3, o4(, o5)**
# ══════════════════════════════════════════════════════════════════════════

def arm_top2(r: dict) -> list[int]:
    """案C: 相手候補の上位2枚（＝全体3・4番手）だけ2点。"""
    return ranked(r)[:2]


def arm_top3(r: dict) -> list[int]:
    return ranked(r)[:3]


def oracle_CA(r: dict) -> list[int]:
    third = set(r["top3"]) - {r["a1"], r["a2"]}
    C = arm_top2(r)
    if len(third) == 1 and third & set(C):
        return C
    A = arm_current(r)
    if len(third) == 1 and third & set(A):
        return A
    return C


def main2() -> None:
    print("\n【呼び方】軸=全体1・2番手 / 相手候補 o1=全体3番手 … o5=全体7番手")
    print("\n[二軸そろい時に3着へ来る相手（全体順位で）]")
    for wname, lo, hi in WINDOWS:
        rows = [r for r in ROWS if lo <= r["date"] <= hi]
        al = [r for r in rows if {r["a1"], r["a2"]} <= set(r["top3"])]
        d = defaultdict(int)
        for r in al:
            t = (set(r["top3"]) - {r["a1"], r["a2"]}).pop()
            d[ranked(r).index(t) + 3] += 1        # +3 = 全体順位
        n = sum(d.values())
        top2 = (d[3] + d[4]) / n * 100
        print(f"  {wname}: " + " ".join(f"全体{k}番手 {d[k]/n*100:.1f}%" for k in sorted(d))
              + f"   → **全体3・4番手だけで {top2:.1f}%**")

    arms = [("案C 上位2点(全体3,4)", arm_top2),
            ("案C' 上位3点(全体3,4,5)", arm_top3),
            ("現行A 下位(全体5〜7)", arm_current),
            ("総流し5点", lambda r: ranked(r)),
            ("オラクル C↔A", oracle_CA)]
    for wname, lo, hi in WINDOWS:
        show([r for r in ROWS if lo <= r["date"] <= hi], arms, wname)

    paired("案C 上位2点", arm_top2, "現行A", arm_current)

    print("\n[振り分け] 既定=案C、市場が『上位2枚は堅い/薄い』と見たら現行A へ")
    print("   θ    窓        件/日   的中%   ガミ%  2倍+/日  倍率中央   ROI%   C採用%")
    for th in (0.00, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 1.01):
        def pick(r, th=th):
            # qC = 上位2枚が持つ市場の取り分。高い＝上位2枚が堅い
            q = {c: 1.0 / r["odds"][c] for c in r["others"] if r["odds"].get(c)}
            tot = sum(q.values()) or 1.0
            qC = sum(q.get(c, 0.0) for c in arm_top2(r)) / tot
            return arm_top2(r) if qC >= th else arm_current(r)
        for wname, lo, hi in WINDOWS:
            rows = [r for r in ROWS if lo <= r["date"] <= hi]
            s = report("sw", pick, rows)
            def qc(r):
                q = {c: 1.0 / r["odds"][c] for c in r["others"] if r["odds"].get(c)}
                tot = sum(q.values()) or 1.0
                return sum(q.get(c, 0.0) for c in arm_top2(r)) / tot
            share = sum(1 for r in rows if qc(r) >= th) / len(rows) * 100
            print(f"  {th:4.2f}  {wname:8s} {s['perday']:6.2f} {s['hit']:7.2f} {s['gami']:6.2f}"
                  f" {s['two']:8.2f} {s['med']:9.2f} {s['roi']:7.1f} {share:8.1f}")
        print()




def main3() -> None:
    def add4th(r):     # 現行A + 全体4番手
        cur = arm_current(r); c = ranked(r)[1]
        return cur if c in cur else cur + [c]
    def only3rd(r):    # 全体3番手だけ1点（1点なら払戻比＝三連複オッズでガミ不能）
        return [ranked(r)[0]]
    def c_plus(r):     # 案C + 現行A の最上位（全体3,4,5番手）
        return ranked(r)[:3]
    arms = [("案C 全体3,4 の2点", arm_top2),
            ("全体3番手だけ1点", only3rd),
            ("現行A+全体4番手", add4th),
            ("現行A（全体5〜7）", arm_current)]
    for wname, lo, hi in WINDOWS:
        show([r for r in ROWS if lo <= r["date"] <= hi], arms, wname)
    paired("案C(全体3,4)", arm_top2, "現行A+全体4番手", add4th)
    paired("全体3番手だけ1点", only3rd, "案C(全体3,4)", arm_top2)




def main4() -> None:
    """『全体3,4番手』と『全体5,6,7番手』の開きで振り分けられるか（ユーザー案）。

    開きの定義を4通り作り、十分位ごとに **二軸そろい時に3着がどちらの組から来たか**
    を実測する。どこかの帯で下位組(A)が上位組(C)を上回るなら振り分けに価値がある。
    """
    def gaps(r):
        rk = ranked(r)
        p3 = r["p3"]
        q = {c: 1.0 / r["odds"][c] for c in r["others"] if r["odds"].get(c)}
        qC = sum(q.get(c, 0.0) for c in rk[:2])
        qA = sum(q.get(c, 0.0) for c in rk[2:])
        return {
            "p3 境目(4番手−5番手)": p3.get(rk[1], 0) - p3.get(rk[2], 0),
            "p3 平均差(3,4 − 5,6,7)": (p3.get(rk[0], 0) + p3.get(rk[1], 0)) / 2
                                     - sum(p3.get(c, 0) for c in rk[2:]) / 3,
            "市場 境目比(4番手/5番手)": (q.get(rk[1], 1e-9) / q.get(rk[2], 1e-9)),
            "市場 取り分 qC/(qC+qA)": qC / (qC + qA) if qC + qA else 0.0,
        }

    names = list(gaps(ROWS[0]).keys())
    for nm in names:
        print(f"\n[開きの十分位] {nm}")
        print("  帯   窓        n    二軸そろい  3着がC(3,4番手)  3着がA(5〜7番手)  差")
        for wname, lo, hi in WINDOWS:
            rows = [r for r in ROWS if lo <= r["date"] <= hi]
            vals = sorted(gaps(r)[nm] for r in rows)
            qs = [vals[int(len(vals) * k / 5)] for k in range(1, 5)]
            for b in range(5):
                sub = [r for r in rows
                       if (b == 0 or gaps(r)[nm] >= qs[b - 1])
                       and (b == 4 or gaps(r)[nm] < qs[b])]
                al = [r for r in sub if {r["a1"], r["a2"]} <= set(r["top3"])]
                if not al:
                    continue
                nc = sum(1 for r in al
                         if (set(r["top3"]) - {r["a1"], r["a2"]}) & set(ranked(r)[:2]))
                na = len(al) - nc
                print(f"   {b+1}   {wname:8s} {len(sub):5d}   {len(al)/len(sub)*100:6.1f}%"
                      f"      {nc/len(al)*100:6.1f}%          {na/len(al)*100:6.1f}%"
                      f"      {(nc-na)/len(al)*100:+6.1f}")
            print()




def main5() -> None:
    """既定=案C（全体3,4の2点）。**開きが狭いレースだけ**現行A（下位3点）へ回す。

    狭い帯では3着の出どころがほぼ互角（C 50.5〜57.3% / A 42.7〜49.5%）な一方、
    A の払戻は中央 5.8倍 ↔ C 2.5倍 と 2.3倍大きい。頻度×配当で逆転するか。
    """
    def gap(r):
        rk = ranked(r); p3 = r["p3"]
        return (p3.get(rk[0], 0) + p3.get(rk[1], 0)) / 2 - sum(p3.get(c, 0) for c in rk[2:]) / 3

    def gapq(r):
        rk = ranked(r)
        q = {c: 1.0 / r["odds"][c] for c in r["others"] if r["odds"].get(c)}
        return q.get(rk[1], 1e-9) / q.get(rk[2], 1e-9)

    for gname, gf in (("p3 平均差", gap), ("市場 境目比", gapq)):
        print(f"\n[狭い側だけ現行Aへ回す] 指標 = {gname}")
        print("  A比率  窓        件/日   的中%   ガミ%  2倍+/日  倍率中央   ROI%")
        for frac in (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 1.0):
            for wname, lo, hi in WINDOWS:
                rows = [r for r in ROWS if lo <= r["date"] <= hi]
                if frac <= 0:
                    thr = -1e18
                elif frac >= 1:
                    thr = 1e18
                else:
                    vs = sorted(gf(r) for r in rows)
                    thr = vs[int(len(vs) * frac)]
                def pick(r, thr=thr, gf=gf):
                    return arm_current(r) if gf(r) < thr else arm_top2(r)
                s = report("sw", pick, rows)
                print(f"  {frac:5.0%}  {wname:8s} {s['perday']:6.2f} {s['hit']:7.2f}"
                      f" {s['gami']:6.2f} {s['two']:8.2f} {s['med']:9.2f} {s['roi']:7.1f}")
            print()




# 入稿の優先順位（`netkeirin_submit_wt.RANK_ORDER`）。7M1 は最下位なので、
# 同じレースに上位ランクの候補があれば 7M1 は売られない。
HIGHER = ["RANK_7H2", "RANK_9H1", "RANK_7T1", "RANK_7T3", "RANK_7S",
          "RANK_9C", "RANK_7B", "RANK_7C", "RANK_7H1"]


def load_sold_keys() -> set[str]:
    """優先順位で譲った後に 7M1 が実際に取るレース（＝上位ランクの候補が無い）。"""
    import psycopg2
    con = psycopg2.connect(os.environ["KEIRIN_DB_URL"])
    cur = con.cursor()
    cur.execute("SELECT DISTINCT split_part(race_key,'#',1) FROM keirin.picks_history "
                "WHERE rank = ANY(%s)", (HIGHER,))
    taken = {r[0] for r in cur.fetchall()}
    con.close()
    return taken


def main6() -> None:
    global ROWS
    taken = load_sold_keys()
    sold = [r for r in ROWS if r["rk"] not in taken]
    print(f"7M1 候補 {len(ROWS):,}R  → 上位ランクに譲った後に残る {len(sold):,}R "
          f"({len(sold)/len(ROWS)*100:.1f}%)")

    def add4th(r):
        cur = arm_current(r); c = ranked(r)[1]
        return cur if c in cur else cur + [c]

    arms = [("案C 全体3,4 の2点", arm_top2),
            ("現行A+全体4番手", add4th),
            ("現行A（全体5〜7）", arm_current),
            ("総流し5点", lambda r: ranked(r))]
    for wname, lo, hi in WINDOWS:
        rows = [r for r in sold if lo <= r["date"] <= hi]
        show(rows, arms, f"{wname}（売る母集団のみ）")

    keep = ROWS
    ROWS = sold
    paired("案C(全体3,4)", arm_top2, "現行A", arm_current)
    paired("現行A+全体4番手", add4th, "現行A", arm_current)
    ROWS = keep




def main7() -> None:
    global ROWS
    taken = load_sold_keys()
    sold = [r for r in ROWS if r["rk"] not in taken]

    def pick_ranks(*idx):
        def f(r, idx=idx):
            rk = ranked(r)
            return [rk[i] for i in idx if i < len(rk)]
        return f

    def addk(k):
        def f(r, k=k):
            cur = arm_current(r); c = ranked(r)[k]
            return cur if c in cur else sorted(cur + [c], key=lambda x: -r["p3"].get(x, 0))
        return f

    arms = [("全体3のみ 1点", pick_ranks(0)),
            ("全体3,4 の2点(案C)", pick_ranks(0, 1)),
            ("全体3,4,5 の3点", pick_ranks(0, 1, 2)),
            ("現行A+全体3番手", addk(0)),
            ("現行A+全体4番手", addk(1)),
            ("現行A（全体5〜7）", arm_current)]
    for wname, lo, hi in WINDOWS:
        show([r for r in sold if lo <= r["date"] <= hi], arms, f"{wname}（売る母集団）")
    keep = ROWS
    ROWS = sold
    paired("案C(全体3,4)", pick_ranks(0, 1), "現行A+全体4番手", addk(1))
    paired("現行A+全体3番手", addk(0), "現行A+全体4番手", addk(1))
    ROWS = keep




# ══════════════════════════════════════════════════════════════════════════
# 入稿ゲートを通してから比べる（2026-08-26・ユーザー指摘）
#
# 🔴 ゲート前の倍率中央で腕を比べていたのは誤り。7M1 に効くゲートは2つ:
#     ① `MIN_POINT_ODDS = 2.0`  買い目の1点でも予測オッズ < 2.0 なら**レースごと**見送り
#     ② `MIN_MEAN_PAYOUT = 20,000`  入稿する買い目の想定払戻の平均 <= 2万円なら見送り
#    （`MIN_EXPECTED_PAYOUT_BY_RANK` は 7C/7S のみ＝7M1 には掛からない）
#   人気の相手を足すほど Σ(1/オッズ) が上がり平均払戻が下がるので、**足す腕ほど
#   ゲートで落ちる**。残った母数で比べないと点数の選択を評価できない。
# ══════════════════════════════════════════════════════════════════════════

def gated(r: dict, legs: list[int]) -> tuple[dict[int, int], float] | None:
    """ゲートを通れば (賭け金, 平均払戻)、落ちれば None。"""
    from src.stake_allocation import MIN_MEAN_PAYOUT, MIN_POINT_ODDS
    if not legs:
        return None
    odds = {c: r["odds"].get(c) for c in legs}
    if any(not o or o <= 0 for o in odds.values()):
        return None                                  # 判定不能は本番では「出す」側
    if min(odds.values()) < MIN_POINT_ODDS:
        return None                                  # ① 安すぎる目がある
    stakes, _ = tilted_stakes(list(legs), None, r["p3"], BUDGET, UNIT,
                              predicted_odds=r["odds"])
    mean = sum(stakes[c] * odds[c] for c in legs) / len(legs)
    if mean <= MIN_MEAN_PAYOUT:
        return None                                  # ② 平均払戻が安い
    return stakes, mean


def report_gated(pick, rows: list[dict]) -> dict:
    inv = pay = 0.0
    hits: list[float] = []
    ratios: list[float] = []
    means: list[float] = []
    days = {r["date"] for r in rows}
    n = ng = 0
    for r in rows:
        g = gated(r, pick(r))
        if g is None:
            continue
        stakes, mean = g
        third = set(r["top3"]) - {r["a1"], r["a2"]}
        p = 0.0
        if len(third) == 1:
            t = third.pop()
            if t in stakes:
                p = stakes[t] / 100.0 * r["trio"]
        i = sum(stakes.values())
        n += 1
        means.append(mean)
        inv += i
        pay += p
        if p > 0:
            hits.append(p)
            ratios.append(p / i)
            if p < i:
                ng += 1
    nd = max(len(days), 1)
    return dict(n=n, perday=n / nd, hit=len(hits) / n * 100 if n else 0,
                gami=ng / len(hits) * 100 if hits else 0,
                two=sum(1 for x in ratios if x >= 2.0) / nd,
                med=median(ratios) if ratios else 0,
                meanpay=median(means) if means else 0,
                roi=pay / inv * 100 if inv else 0)


def main8() -> None:
    taken = load_sold_keys()
    sold = [r for r in ROWS if r["rk"] not in taken]

    def pick_ranks(*idx):
        return lambda r, idx=idx: [ranked(r)[i] for i in idx if i < len(ranked(r))]

    def addk(k):
        def f(r, k=k):
            cur = arm_current(r); c = ranked(r)[k]
            return cur if c in cur else sorted(cur + [c], key=lambda x: -r["p3"].get(x, 0))
        return f

    arms = [("現行A（全体5〜7）", arm_current),
            ("現行A+全体4番手", addk(1)),
            ("現行A+全体3番手", addk(0)),
            ("全体3,4 の2点", pick_ranks(0, 1)),
            ("全体3,4,5 の3点", pick_ranks(0, 1, 2)),
            ("総流し5点", lambda r: ranked(r))]
    print("\n【入稿ゲート後】① 2倍未満の目があれば見送り ② 平均払戻<=2万円なら見送り")
    for wname, lo, hi in WINDOWS:
        rows = [r for r in sold if lo <= r["date"] <= hi]
        print(f"\n[{wname}]  ゲート前 {len(rows):,}R")
        print("  腕                     通過率  件/日   的中%   ガミ%  2倍+/日  倍率中央 平均払戻中央   ROI%")
        for nm, f in arms:
            s = report_gated(f, rows)
            print(f"  {nm:22s} {s['n']/len(rows)*100:5.1f}% {s['perday']:6.2f} {s['hit']:7.2f}"
                  f" {s['gami']:6.2f} {s['two']:8.2f} {s['med']:9.2f} {s['meanpay']:11,.0f} {s['roi']:7.1f}")




def main9() -> None:
    """ゲート後は腕ごとに母数が違うので、**日単位のブロックブートストラップ**で
    件/日・的中件/日・2倍+件/日 を比べる（レース単位のペアは組めない）。"""
    import random
    taken = load_sold_keys()
    sold = [r for r in ROWS if r["rk"] not in taken]

    def addk(k):
        def f(r, k=k):
            cur = arm_current(r); c = ranked(r)[k]
            return cur if c in cur else sorted(cur + [c], key=lambda x: -r["p3"].get(x, 0))
        return f

    def day_stats(pick, rows):
        d = defaultdict(lambda: [0, 0, 0])     # 出した / 的中 / 2倍+
        for r in rows:
            g = gated(r, pick(r))
            if g is None:
                continue
            stakes, _ = g
            third = set(r["top3"]) - {r["a1"], r["a2"]}
            p = 0.0
            if len(third) == 1:
                t = third.pop()
                if t in stakes:
                    p = stakes[t] / 100.0 * r["trio"]
            i = sum(stakes.values())
            a = d[r["date"]]
            a[0] += 1
            if p > 0:
                a[1] += 1
                if p >= 2 * i:
                    a[2] += 1
        return d

    arms = {"現行A": arm_current, "A+全体4番手": addk(1), "A+全体3番手": addk(0)}
    for wname, lo, hi in WINDOWS:
        rows = [r for r in sold if lo <= r["date"] <= hi]
        days = sorted({r["date"] for r in rows})
        D = {k: day_stats(f, rows) for k, f in arms.items()}
        print(f"\n[{wname}] {len(days)}日  日次ブロック bootstrap（対 現行A・95%CI）")
        for nm in ("A+全体4番手", "A+全体3番手"):
            rng = random.Random(23)
            d0 = d1 = d2 = []
            d0, d1, d2 = [], [], []
            for _ in range(2000):
                s = [days[rng.randrange(len(days))] for _ in range(len(days))]
                a = [sum(D[nm][x][i] for x in s) / len(s) for i in range(3)]
                b = [sum(D["現行A"][x][i] for x in s) / len(s) for i in range(3)]
                d0.append(a[0] - b[0]); d1.append(a[1] - b[1]); d2.append(a[2] - b[2])
            def ci(v):
                v = sorted(v)
                return f"{sum(v)/len(v):+6.3f}[{v[int(.025*len(v))]:+6.3f},{v[int(.975*len(v))]:+6.3f}]"
            print(f"  {nm:12s} Δ件/日 {ci(d0)}  Δ的中件/日 {ci(d1)}  Δ2倍+件/日 {ci(d2)}")




def main10() -> None:
    """ゲート後・売る母集団で、各腕の**払戻倍率の帯構成**を出す。
    7S/7C は中央 1.56/1.57倍（<2倍が63〜68%）、7B は 2.29倍。
    5〜10倍を埋めているのは現行 7M1 だけなので、案を替えると誰が埋めるかが問題になる。"""
    taken = load_sold_keys()
    sold = [r for r in ROWS if r["rk"] not in taken]

    def addk(k):
        def f(r, k=k):
            cur = arm_current(r); c = ranked(r)[k]
            return cur if c in cur else sorted(cur + [c], key=lambda x: -r["p3"].get(x, 0))
        return f

    def pick_ranks(*idx):
        return lambda r, idx=idx: [ranked(r)[i] for i in idx if i < len(ranked(r))]

    arms = [("現行A（全体5〜7）", arm_current), ("A+全体4番手", addk(1)),
            ("A+全体3番手", addk(0)), ("全体3,4 の2点", pick_ranks(0, 1)),
            ("総流し5点", lambda r: ranked(r))]
    BANDS = [("<2", 0, 2), ("2-5", 2, 5), ("5-10", 5, 10), ("10-20", 10, 20), ("20+", 20, 1e9)]
    for wname, lo, hi in WINDOWS:
        rows = [r for r in sold if lo <= r["date"] <= hi]
        print(f"\n[{wname}] 的中時の払戻倍率の帯（ゲート後・売る母集団）")
        print("  腕                     的中件/日  " + "  ".join(f"{b:>6s}" for b, _, _ in BANDS)
              + "   5倍以上の的中件/日")
        for nm, f in arms:
            rs = []
            days = {r["date"] for r in rows}
            for r in rows:
                g = gated(r, f(r))
                if g is None:
                    continue
                stakes, _ = g
                third = set(r["top3"]) - {r["a1"], r["a2"]}
                if len(third) == 1:
                    t = third.pop()
                    if t in stakes:
                        pay = stakes[t] / 100.0 * r["trio"]
                        if pay > 0:
                            rs.append(pay / sum(stakes.values()))
            nd = len(days)
            if not rs:
                continue
            cnt = {b: sum(1 for x in rs if a <= x < c) for b, a, c in BANDS}
            hi5 = sum(1 for x in rs if x >= 5)
            print(f"  {nm:22s} {len(rs)/nd:8.2f}  "
                  + "  ".join(f"{cnt[b]/len(rs)*100:5.0f}%" for b, _, _ in BANDS)
                  + f"   {hi5/nd:8.3f}")




def main11() -> None:
    """相手の全体順位ごとの予測三連複オッズと、各腕の『当たった三連複のオッズ』帯。

    既存ランクの実測（picks_history 2025〜・的中のみ）:
      7S オッズ中央 5.70（5-10が33%）/ 7C 3.60 / 7B 6.10 / 7M1 12.50
    """
    taken = load_sold_keys()
    sold = [r for r in ROWS if r["rk"] not in taken]

    print("\n[相手の全体順位ごとの予測三連複オッズ（売る母集団・中央値）]")
    for wname, lo, hi in WINDOWS:
        rows = [r for r in sold if lo <= r["date"] <= hi]
        cols = []
        for i in range(5):
            v = sorted(r["odds"][ranked(r)[i]] for r in rows if ranked(r)[i] in r["odds"])
            cols.append(median(v) if v else 0)
        print(f"  {wname:8s} " + "  ".join(f"全体{i+3}番手 {cols[i]:6.2f}倍" for i in range(5)))

    def addk(k):
        def f(r, k=k):
            cur = arm_current(r); c = ranked(r)[k]
            return cur if c in cur else sorted(cur + [c], key=lambda x: -r["p3"].get(x, 0))
        return f

    def pick_ranks(*idx):
        return lambda r, idx=idx: [ranked(r)[i] for i in idx if i < len(ranked(r))]

    arms = [("現行A（全体5〜7）", arm_current), ("A+全体4番手", addk(1)),
            ("A+全体3番手", addk(0)), ("全体3,4 の2点", pick_ranks(0, 1))]
    BANDS = [("<5", 0, 5), ("5-10", 5, 10), ("10-20", 10, 20), ("20-50", 20, 50), ("50+", 50, 1e9)]
    for wname, lo, hi in WINDOWS:
        rows = [r for r in sold if lo <= r["date"] <= hi]
        print(f"\n[{wname}] 当たった三連複のオッズ帯（ゲート後・売る母集団）")
        print("  腕                     的中件/日 オッズ中央  " + " ".join(f"{b:>6s}" for b, _, _ in BANDS))
        for nm, f in arms:
            os_ = []
            days = {r["date"] for r in rows}
            for r in rows:
                g = gated(r, f(r))
                if g is None:
                    continue
                stakes, _ = g
                third = set(r["top3"]) - {r["a1"], r["a2"]}
                if len(third) == 1 and third.copy().pop() in stakes:
                    os_.append(r["trio"] / 100.0)
            if not os_:
                continue
            cnt = {b: sum(1 for x in os_ if a <= x < c) for b, a, c in BANDS}
            print(f"  {nm:22s} {len(os_)/len(days):8.2f} {median(os_):9.2f}  "
                  + " ".join(f"{cnt[b]/len(os_)*100:5.0f}%" for b, _, _ in BANDS))


if __name__ == "__main__":
    main11()
