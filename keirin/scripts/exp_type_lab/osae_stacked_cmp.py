#!/usr/bin/env python3
"""`osae_stacked_build.py` の3つの pkl（⓪現行 / ②τ適応+λr / ③3つ全部）を突き合わせる。

    python osae_stacked_cmp.py base.pkl two.pkl three.pkl [--no-axis-gate]

🔴 知りたいのは **③−②**（押さえ目の純増分）。②−⓪ は既測の検算に使う。
"""
from __future__ import annotations

import math
import pickle
import sys
from statistics import median

import numpy as np

P0, P2, P3 = sys.argv[1], sys.argv[2], sys.argv[3]
AXIS_GATE = "--no-axis-gate" not in sys.argv
OSAE_KEYS = ("A_hit", "B_hit", "C_hit", "E_hit", "F_hit")
SCOPES = ([(f"{k} 単体", (k,)) for k in OSAE_KEYS]
          + [("押さえ対象5プラン計", OSAE_KEYS), ("ラインナップ全体", None)])
RNG = np.random.default_rng(20260911)
NBOOT = 4000


def load(p):
    return pickle.load(open(p, "rb"))


def sold(rows, win, keys=None):
    return {r["i"]: r for r in rows
            if r["win"] == win and r["gate"]
            and (r.get("axis_ok", True) or not AXIS_GATE)
            and (keys is None or r["key"] in keys)}


def days(rows, win):
    return len({r["date"] for r in rows if r["win"] == win})


def summ(rs):
    if not rs:
        return None
    n = len(rs)
    hits = [r for r in rs if r["pay"] > 0]
    gami = [r for r in hits if r["pay"] < r["inv"]]
    shown = [r for r in rs if r["pay"] > r["inv"]]
    pays = sorted(r["pay"] for r in hits)
    inv = sum(r["inv"] for r in rs)
    return dict(
        n=n, k=sum(r["k"] for r in rs) / n,
        hit=len(hits) / n * 100,
        gami=len(gami) / len(hits) * 100 if hits else 0.0,
        gami_n=len(gami),
        shown=len(shown) / n * 100,
        med_pay=median(pays) if pays else 0.0,
        big=sum(1 for p in pays if p >= 100_000),
        roi=sum(r["pay"] for r in rs) / inv * 100 if inv else 0.0,
        ofire=sum(1 for r in rs if r.get("osae_n", 0) > 0) / n * 100,
        ocand=sum(1 for r in rs if r.get("ocand_n", 0) > 0) / n * 100,
        ogate=(sum(1 for r in rs if r.get("osae_gated"))
               / max(1, sum(1 for r in rs if r.get("ocand_n", 0) > 0)) * 100),
        onofit=(sum(1 for r in rs if r.get("osae_nofit"))
                / max(1, sum(1 for r in rs if r.get("ocand_n", 0) > 0)) * 100),
        owin=sum(1 for r in rs if r.get("osae_win")),
    )


def mcnemar(brk: int, sav: int) -> float:
    """不一致ペアだけの二項検定（両側・正確）。破壊 brk / 救済 sav。"""
    n = brk + sav
    if n == 0:
        return 1.0
    k = max(brk, sav)
    tail = sum(math.comb(n, j) for j in range(k, n + 1)) / (2 ** n)
    return min(1.0, 2 * tail)


def boot_delta(pairs):
    a = np.array([p[0] for p in pairs], float)
    b = np.array([p[1] for p in pairs], float)
    pt = (b.mean() - a.mean()) * 100
    n = len(a)
    idx = RNG.integers(0, n, size=(NBOOT, n))
    d = (b[idx].mean(1) - a[idx].mean(1)) * 100
    return pt, float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5))


HEAD = ("  {:14s} {:>6s} {:>6s} {:>7s} {:>6s} {:>8s} {:>9s} {:>8s} {:>7s} "
        "{:>7s} {:>7s} {:>8s} {:>8s} {:>6s} {:>6s}"
        .format("腕", "件/日", "点数", "的中%", "ガミ%", "表示的中%", "払戻中央",
                "10万+/日", "ROI%", "発動%", "候補%", "ゲート落%", "床不足%", "押勝", "ガミ件"))


def row(name, s, nd):
    if s is None:
        return f"  {name:14s} (該当なし)"
    return (f"  {name:14s} {s['n']/nd:6.2f} {s['k']:6.2f} {s['hit']:7.2f} "
            f"{s['gami']:6.2f} {s['shown']:8.2f} {s['med_pay']:9,.0f} "
            f"{s['big']/nd:8.3f} {s['roi']:7.1f} {s['ofire']:7.1f} "
            f"{s['ocand']:7.1f} {s['ogate']:8.1f} {s['onofit']:8.1f} {s['owin']:6d}"
            f" {s['gami_n']:6d}")


def ctrl_series(s2, s3, common, tag):
    """無作為対照 20seed の (表示的中%, Δpt) 系列。② を土台に同数を無作為に足す。"""
    out = []
    n_seed = 0
    for i in common:
        c = s3[i].get("ctrl") or {}
        if tag in c:
            n_seed = max(n_seed, len(c[tag][0]))
    if not n_seed:
        return []
    base = np.array([s2[i]["pay"] > s2[i]["inv"] for i in common], float)
    for sd in range(n_seed):
        v = base.copy()
        for j, i in enumerate(common):
            c = (s3[i].get("ctrl") or {}).get(tag)
            if not c:
                continue
            p, iv = c[0][sd], c[1][sd]
            if p is None:
                continue
            v[j] = 1.0 if p > iv else 0.0
        out.append(v)
    return out


def main() -> None:
    A, B, D = load(P0), load(P2), load(P3)
    for win, label in (("confirm", "確認 2026-01〜08"),
                       ("explore", "探索 2024-07〜2025-12")):
        nd = days(A, win)
        print("")
        print("=" * 132)
        print(f"=== {label}  （営業日 {nd}・軸信頼ゲート {'あり' if AXIS_GATE else 'なし'}）===")
        for scope, keys in SCOPES:
            s0, s2, s3 = sold(A, win, keys), sold(B, win, keys), sold(D, win, keys)
            common = sorted(set(s0) & set(s2) & set(s3))
            if not common:
                continue
            print("")
            print(f"-- {scope}  ⓪ n={len(s0):,} / ② n={len(s2):,} / ③ n={len(s3):,} "
                  f"/ 共通 n={len(common):,} --")
            print(HEAD)
            print(row("⓪現行", summ([s0[i] for i in common]), nd))
            print(row("②τ+λr", summ([s2[i] for i in common]), nd))
            print(row("③+押さえ", summ([s3[i] for i in common]), nd))
            sh = lambda s, i: s[i]["pay"] > s[i]["inv"]
            for tag, x, y in (("③−②", s2, s3), ("③−⓪", s0, s3), ("②−⓪", s0, s2)):
                pairs = [(sh(x, i), sh(y, i)) for i in common]
                pt, lo, hi = boot_delta(pairs)
                keep = sum(1 for a, b in pairs if a and b)
                brk = sum(1 for a, b in pairs if a and not b)
                sav = sum(1 for a, b in pairs if b and not a)
                print(f"  Δ表示的中 {tag} = {pt:+.3f}pt  95%CI [{lo:+.3f}, {hi:+.3f}]"
                      f"   維持 {keep} / 破壊 {brk} / 救済 {sav}"
                      f"   McNemar p={mcnemar(brk, sav):.4f}")
            # ── 無作為対照 ──
            v3 = np.array([sh(s3, i) for i in common], float)
            v2 = np.array([sh(s2, i) for i in common], float)
            for tag, lab in (("band", f"無作為(帯あり・同数)"),
                             ("free", "無作為(帯なし・同数)")):
                ser = ctrl_series(s2, s3, common, tag)
                if not ser:
                    continue
                means = np.array([v.mean() * 100 for v in ser])
                wins = sum(1 for m in means if v3.mean() * 100 > m)
                nfail = ntot = 0
                for i in common:
                    c = (s3[i].get("ctrl") or {}).get(tag)
                    if not c:
                        continue
                    ntot += len(c[0])
                    nfail += sum(1 for x in c[0] if x is None)
                print(f"  {lab}: 平均 {means.mean():.3f}% "
                      f"[{means.min():.3f}, {means.max():.3f}]  "
                      f"（②={v2.mean()*100:.3f}% ③={v3.mean()*100:.3f}%）"
                      f"  本案が勝った seed {wins}/{len(means)}"
                      f"  対照が組めなかった {nfail/max(1,ntot)*100:.1f}%")

        # ── 押さえの中身（確定オッズ分布・ガミ）──
        s3a = sold(D, win, OSAE_KEYS)
        s2a = sold(B, win, OSAE_KEYS)
        common = sorted(set(s2a) & set(s3a))
        rs = [s3a[i] for i in common]
        fired = [r for r in rs if r["osae_n"] > 0]
        pts = sum(r["osae_n"] for r in fired)
        winr = [r for r in rs if r["osae_win"]]
        print("")
        print(f"-- 押さえの中身（押さえ対象5プラン n={len(rs):,}）--")
        print(f"  候補あり {sum(1 for r in rs if r['ocand_n']>0):,}件 "
              f"({sum(1 for r in rs if r['ocand_n']>0)/len(rs)*100:.1f}%) / "
              f"発動 {len(fired):,}件 ({len(fired)/len(rs)*100:.1f}%) / "
              f"ゲート見送り {sum(1 for r in rs if r['osae_gated']):,}件 "
              f"(候補ありの {sum(1 for r in rs if r['osae_gated'])/max(1,sum(1 for r in rs if r['ocand_n']>0))*100:.1f}%) / "
              f"床が置けず見送り {sum(1 for r in rs if r.get('osae_nofit')):,}件 "
              f"(同 {sum(1 for r in rs if r.get('osae_nofit'))/max(1,sum(1 for r in rs if r['ocand_n']>0))*100:.1f}%)")
        print(f"  押さえた点数 {pts:,}点（{pts/max(1,len(fired)):.2f}点/発動レース・"
              f"{pts/nd:.2f}点/日）")
        pr = [o for r in fired for o in r["osae_pred"]]
        if pr:
            q = np.percentile(pr, [25, 50, 75, 95])
            print(f"  押さえた点の予測オッズ 中央 {q[1]:,.0f}倍 "
                  f"(25% {q[0]:,.0f} / 75% {q[2]:,.0f} / 95% {q[3]:,.0f})")
        exp_hit = sum(1.0 / o for r in fired for o in r["osae_pred"] if o > 0)
        print(f"  予測オッズから期待される的中 {exp_hit:.1f}回"
              f"（控除率25%を引いた市場実勢なら {exp_hit*0.75:.1f}回）")
        print(f"  押さえで当たった {len(winr):,}件 "
              f"({len(winr)/nd:.3f}件/日・{nd/max(1,len(winr)):.1f}日に1回)")
        if winr:
            od = sorted(r["fin_odds"] for r in winr)
            lo = [o for o in od if o < 100]
            print(f"    確定オッズ 中央 {median(od):,.1f}倍 / "
                  f"最小 {od[0]:,.1f} / 最大 {od[-1]:,.1f}")
            print(f"    **100倍未満で当たった（＝ガミ）{len(lo):,}件 "
                  f"({len(lo)/len(winr)*100:.1f}%・{len(lo)/nd:.3f}件/日)**"
                  + (f"  内訳 {[f'{o:.1f}' for o in lo[:12]]}" if lo else ""))
            bins = [(0, 100), (100, 200), (200, 400), (400, 1000), (1000, 1e9)]
            print("    確定オッズ分布: " + " / ".join(
                f"{a:,.0f}-{'∞' if b>1e8 else format(b,',.0f')}倍 "
                f"{sum(1 for o in od if a<=o<b)}件"
                for a, b in bins))
        # 既存の的中がガミへ落ちた件数（②で表示的中 → ③でガミ）
        brk2 = [i for i in common
                if s2a[i]["pay"] > s2a[i]["inv"]
                and 0 < s3a[i]["pay"] <= s3a[i]["inv"]]
        print(f"  ②で表示的中だったのが③でガミへ落ちた: {len(brk2):,}件")


if __name__ == "__main__":
    main()
