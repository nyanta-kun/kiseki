#!/usr/bin/env python3
"""A-1: ペアモデルを**本番の軸選定**と比べ直す（2026-08-23）。

## なぜやるか

§22 の「二軸的中 +1.05pt」は比較相手が **`p3` 上位2車**だった。
ところが本番（`strategy_wt.rank_7s_select_axis`）は

    軸1 = pw（1着率）最上位
    軸2 = z(p3) − 0.3 × z(bad) の最上位（軸1を除く）

🔴 **つまり土台が違う。** +1.05pt が本番比でも残るかは未測定だった。

⚠️ `bad`（大敗確率）は `keirin.wt_entries` に**保存されていない**（pw/p3/top2/印は
   2024-26 とも100%ある）。4ヘッド揃うのは `tf_shape_cache4`＝2026 のみ。
   そこで本スクリプトは
     ① 2026 で **bad の有無が軸をどれだけ動かすか**を先に測り、
     ② 小さければ 2024-25 は bad なし近似で扱ってよい、と判断できるようにする。

## 併せて: WT◎○ と二軸が重なって**両方外す**レース（ユーザー指摘）

> 7S/7C で WT◎○ と二軸が重なり外れているレースが多数ある。
> この2車軸で外すのは予想として一番信頼を落とす。確実に当てる、
> 当てられないなら片方の軸はそれ以外の選手にする。

`wt_overlap_n`（◎○ と軸2車の重なり数・`rank_7s_wt_overlap_n`）ごとに
**二軸とも着外**の率を出す。ここが重なりで悪化しているなら、
「重なったら片方を外す」規則に意味がある。
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.backfill_7t1_rank_wt import _load_finishes  # noqa: E402
from scripts.exp_trio_joint_partner import fit, load_any  # noqa: E402
from scripts.exp_trio_pair_model import build_rows as build_pairs  # noqa: E402
from scripts.exp_trio_pair_model import load_entries  # noqa: E402
from src.result_top3 import winning_trifectas  # noqa: E402
from src.strategy_wt import rank_7s_select_axis, rank_7s_wt_overlap_n  # noqa: E402


def load_cache4(path: str) -> list[dict]:
    out = []
    with open(path) as f:
        for x in f:
            r = json.loads(x)
            if not r.get("win"):
                continue
            g = lambda k: {int(a): float(b) for a, b in (r.get(k) or {}).items()}  # noqa: E731
            mark = {int(a): int(b) for a, b in (r.get("mark") or {}).items()}
            out.append(dict(key=r["race_key"], date=r["race_date"],
                            p3=g("p3"), pw=g("pw"), bad=g("bad"), top2=g("top2"),
                            mark=mark))
    return out


def axes_variants(r: dict) -> dict[str, tuple[int, int] | None]:
    p3, pw, bad = r["p3"], r["pw"], r["bad"]
    order = sorted(p3, key=lambda c: (-p3[c], c))
    prod = rank_7s_select_axis(pw, p3, bad)
    nobad = rank_7s_select_axis(pw, p3, {c: 0.0 for c in bad})
    return {
        "本番(3ヘッド)": (prod[0], prod[1]) if prod else None,
        "本番(badなし)": (nobad[0], nobad[1]) if nobad else None,
        "p3上位2車": (order[0], order[1]) if len(order) >= 2 else None,
    }


def ci_diff(days, B=4000, seed=29):
    v = np.array([[d[0], d[1], d[2]] for d in days.values()], float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(v), size=(B, len(v)))
    tot = v[idx, 0].sum(1)
    d = np.sort(v[idx, 2].sum(1) / tot - v[idx, 1].sum(1) / tot)
    return d[int(B * .025)], d[int(B * .975)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", default="data/exp/trio_rank_cache.jsonl")
    ap.add_argument("--test", default="data/exp/tf_shape_cache4.jsonl")
    ap.add_argument("--rounds", type=int, default=400)
    args = ap.parse_args()

    te = load_cache4(args.test)
    fin = _load_finishes([r["key"] for r in te])
    te = [r for r in te if r["key"] in fin and len(r["p3"]) >= 7]
    print(f"検定 {len(te):,}R（2026・4ヘッド揃う唯一の窓）\n")

    top3_of = {r["key"]: {c for w in winning_trifectas(fin[r["key"]]) for c in w}
               for r in te}

    # ── ① 軸の一致率と二軸的中 ──
    names = ["本番(3ヘッド)", "本番(badなし)", "p3上位2車"]
    hit = {n: defaultdict(lambda: [0, 0, 0]) for n in names}
    agree = defaultdict(int)
    n = 0
    axes_all = {}
    for r in te:
        v = axes_variants(r)
        if any(v[k] is None for k in names):
            continue
        n += 1
        axes_all[r["key"]] = v
        t3 = top3_of[r["key"]]
        for k in names:
            a, b = v[k]
            z = hit[k][r["date"]]
            z[0] += 1
            z[2] += int(a in t3 and b in t3)
        agree["本番 vs p3上位2"] += int(set(v["本番(3ヘッド)"]) == set(v["p3上位2車"]))
        agree["本番 vs badなし"] += int(set(v["本番(3ヘッド)"]) == set(v["本番(badなし)"]))
        agree["軸1が一致(本番 vs p3)"] += int(v["本番(3ヘッド)"][0] == v["p3上位2車"][0])
    print(f"【① 軸選定の違い・{n:,}R】")
    for k, c in agree.items():
        print(f"  {k:24} {c/n:.1%}")
    print()
    print(f"{'軸の作り方':>16}{'二軸的中':>10}{'（p3上位2車との差）':>28}")
    base = hit["p3上位2車"]
    for k in names:
        h = sum(z[2] for z in hit[k].values()) / n
        if k == "p3上位2車":
            print(f"{k:>16}{h:>10.2%}")
            continue
        dd = {d: [base[d][0], base[d][2], hit[k][d][2]] for d in base}
        lo, hi = ci_diff(dd)
        b = sum(z[2] for z in base.values()) / n
        f = "🟢" if lo > 0 else ("🔴" if hi < 0 else "")
        print(f"{k:>16}{h:>10.2%}"
              f"{f'  Δ{(h-b)*100:+.2f}pt [{lo*100:+.2f},{hi*100:+.2f}]{f}':>28}")

    # ── ② ペアモデル（学習は2024-25・p3のみ）を本番の軸と比べる ──
    tr = load_any(args.train)
    ent_tr = load_entries([r["key"] for r in tr])
    fin_tr = _load_finishes([r["key"] for r in tr])
    Xtr, ytr, _ = build_pairs(tr, ent_tr, fin_tr)
    m = fit(Xtr, ytr, args.rounds)
    te_rows = [dict(key=r["key"], date=r["date"], p3=r["p3"],
                    order=sorted(r["p3"], key=lambda c: (-r["p3"][c], c)))
               for r in te if r["key"] in axes_all]
    ent_te = load_entries([r["key"] for r in te_rows])
    Xte, yte, mte = build_pairs(te_rows, ent_te, fin)
    pred = m.predict(Xte)
    best = {}
    for (key, _, a, b, _, _), p in zip(mte, pred):
        cur = best.get(key)
        if cur is None or p > cur[0]:
            best[key] = (float(p), frozenset((a, b)))
    d2 = defaultdict(lambda: [0, 0, 0])
    same = k2 = 0
    for r in te:
        v = axes_all.get(r["key"]); bb = best.get(r["key"])
        if not v or not bb:
            continue
        k2 += 1
        t3 = top3_of[r["key"]]
        pa, pb = v["本番(3ヘッド)"]
        z = d2[r["date"]]
        z[0] += 1
        z[1] += int(pa in t3 and pb in t3)
        z[2] += int(all(c in t3 for c in bb[1]))
        same += int(set((pa, pb)) == set(bb[1]))
    lo, hi = ci_diff(d2)
    hp = sum(z[1] for z in d2.values()) / k2
    hm = sum(z[2] for z in d2.values()) / k2
    f = "🟢" if lo > 0 else ("🔴" if hi < 0 else "")
    print(f"\n【② ペアモデル vs 本番の軸・{k2:,}R】")
    print(f"  本番(3ヘッド)   {hp:.2%}")
    print(f"  ペアモデル      {hm:.2%}   Δ{(hm-hp)*100:+.2f}pt "
          f"[{lo*100:+.2f},{hi*100:+.2f}]{f}")
    print(f"  同じペアを選んだ率: {same/k2:.1%}")

    # ── ③ WT◎○ との重なりと「二軸とも着外」──
    print(f"\n【③ WT◎○ との重なり別・本番の軸（{k2:,}R）】")
    print(f"{'重なり':>10}{'件数':>9}{'割合':>8}{'二軸的中':>10}"
          f"{'二軸とも着外':>13}{'軸1のみ着外':>12}{'軸2のみ着外':>12}")
    seg = defaultdict(lambda: [0, 0, 0, 0, 0])
    for r in te:
        v = axes_all.get(r["key"])
        if not v:
            continue
        a, b = v["本番(3ヘッド)"]
        mk = r["mark"]
        hon = next((c for c, x in mk.items() if x == 1), None)
        tai = next((c for c, x in mk.items() if x == 2), None)
        ov = rank_7s_wt_overlap_n(a, b, hon, tai)
        t3 = top3_of[r["key"]]
        ia, ib = a in t3, b in t3
        s = seg["欠損" if ov is None else f"{ov}車一致"]
        s[0] += 1
        s[1] += int(ia and ib)
        s[2] += int(not ia and not ib)
        s[3] += int(not ia and ib)
        s[4] += int(ia and not ib)
    tot = sum(s[0] for s in seg.values())
    for k in sorted(seg, key=lambda x: (x == "欠損", x)):
        s = seg[k]
        print(f"{k:>10}{s[0]:>9,}{s[0]/tot:>8.1%}{s[1]/s[0]:>10.2%}"
              f"{s[2]/s[0]:>13.2%}{s[3]/s[0]:>12.2%}{s[4]/s[0]:>12.2%}")
    # ── ④ 「◎○と完全一致なのに二軸とも着外」は予測できるか ──
    #    🔴 一律に「重なったら片方を外す」は**最も当たっている群を崩す**。
    #       狙うのは群全体ではなく、その中の危ない部分集合。
    print("\n【④ ◎○完全一致(2車一致)の中で危ない部分集合を探す】")
    pool = []
    for r in te:
        v = axes_all.get(r["key"])
        if not v:
            continue
        a, b = v["本番(3ヘッド)"]
        mk = r["mark"]
        hon = next((c for c, x in mk.items() if x == 1), None)
        tai = next((c for c, x in mk.items() if x == 2), None)
        if rank_7s_wt_overlap_n(a, b, hon, tai) != 2:
            continue
        t3 = top3_of[r["key"]]
        p3 = r["p3"]
        vals = np.array(sorted(p3.values(), reverse=True))
        q = vals / max(vals.sum(), 1e-9)
        pool.append(dict(
            date=r["date"], both_out=int(a not in t3 and b not in t3),
            axis_sum=p3[a] + p3[b],
            gap23=vals[1] - vals[2],
            ent=float(-(q * np.log(q + 1e-12)).sum() / np.log(len(q))),
            pw_sum=r["pw"][a] + r["pw"][b],
            bad_sum=r["bad"][a] + r["bad"][b],
        ))
    base = np.mean([x["both_out"] for x in pool])
    print(f"  母集団 {len(pool):,}R ・ 二軸とも着外 {base:.2%}"
          f"（{int(base*len(pool)):,}件）")
    print(f"{'切り口':>12}{'分位':>6}{'件数':>8}{'二軸とも着外':>13}{'対母集団':>10}")
    for name, key in (("軸2車のp3合計", "axis_sum"), ("2位-3位の差", "gap23"),
                      ("エントロピー", "ent"), ("軸2車のpw合計", "pw_sum"),
                      ("軸2車のbad合計", "bad_sum")):
        qs = np.quantile([x[key] for x in pool], [.2, .4, .6, .8])
        for i in range(5):
            lo = -9e9 if i == 0 else qs[i - 1]
            hi = 9e9 if i == 4 else qs[i]
            sub = [x for x in pool if lo <= x[key] < hi]
            if len(sub) < 200:
                continue
            m_ = np.mean([x["both_out"] for x in sub])
            mark = " 🔴" if m_ > base * 1.5 else ""
            print(f"{name if i == 0 else '':>12}{f'Q{i+1}':>6}{len(sub):>8,}"
                  f"{m_:>13.2%}{m_/base:>10.2f}x{mark}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
