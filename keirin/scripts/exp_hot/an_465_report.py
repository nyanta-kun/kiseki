#!/usr/bin/env python3
"""an465/<yid>.jsonl から券種・点数・形・的中帯を集計する。"""
from __future__ import annotations
import collections, json, statistics as st, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
NAMES = {465: "シュウの二車福", 354: "大河原修司", 401: "二ノ輪大嵐",
         585: "Equine Genius", 350: "鈴木誠"}


def q(xs, p):
    xs = sorted(x for x in xs if x is not None)
    return xs[min(len(xs) - 1, int(len(xs) * p))] if xs else None


def load(yid):
    p = HERE / "an465" / f"{yid}.jsonl"
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def shape(r):
    """買い目の形を1文字列にまとめる。"""
    parts = []
    for row in r["rows"]:
        c = row["cols"]
        if c:
            parts.append(f"{row['bet_type']}/{row['mode']}/{'-'.join(str(len(x)) for x in c)}")
        else:
            parts.append(f"{row['bet_type']}/{row['mode']}/pt")
    return " + ".join(parts)


def main():
    for yid, name in NAMES.items():
        p = HERE / "an465" / f"{yid}.jsonl"
        if not p.exists():
            print(f"\n## {yid} {name}: 未取得"); continue
        rs = load(yid)
        samp = [r for r in rs if "sample" in r["tags"]]
        hi = [r for r in rs if "hi" in r["tags"]]
        print(f"\n{'='*70}\n## {yid} {name}   sample={len(samp)} hi={len(hi)}")

        # --- 券種 ---
        bt = collections.Counter()
        for r in samp:
            bt["+".join(r["bet_types"] or ["?"])] += 1
        print("  券種構成(商品単位):", bt.most_common())
        # 行単位
        btr = collections.Counter()
        ptr = collections.Counter()
        for r in samp:
            for row in r["rows"]:
                btr[row["bet_type"]] += 1
                ptr[row["bet_type"]] += row["n_points"] or 0
        print("  券種(行数):", btr.most_common(), " 点数合計:", ptr.most_common())

        # --- 点数 ---
        npts = [r["n_points_total"] for r in samp]
        print(f"  点数: 中央={q(npts,.5)} min={min(npts)} p10={q(npts,.1)} "
              f"p90={q(npts,.9)} max={max(npts)}  平均={st.mean(npts):.2f}")
        print("  点数分布:", sorted(collections.Counter(npts).items())[:14])

        # --- 1点賭け金 ---
        ratios = []
        for r in samp:
            if r["unit_min"] and r["unit_max"]:
                ratios.append(r["unit_max"] / r["unit_min"])
        flat = sum(1 for x in ratios if x == 1.0)
        print(f"  1点賭け金: min中央={q([r['unit_min'] for r in samp],.5)} "
              f"max中央={q([r['unit_max'] for r in samp],.5)}  "
              f"均等={flat}/{len(ratios)}={flat/max(len(ratios),1)*100:.0f}%  "
              f"傾斜比 中央={q(ratios,.5):.2f} p90={q(ratios,.9):.2f} max={max(ratios):.2f}")

        # --- 形 ---
        md = collections.Counter()
        for r in samp:
            for row in r["rows"]:
                md[row["mode"]] += 1
        print("  mode(行):", md.most_common())
        print("  形 上位10:")
        for s, c in collections.Counter(shape(r) for r in samp).most_common(10):
            print(f"     {c:4d}  {s}")

        # --- 1着候補の車数（フォーメーション/流し系） ---
        c1 = collections.Counter(); c2 = collections.Counter(); c3 = collections.Counter()
        for r in samp:
            for row in r["rows"]:
                c = row["cols"]
                if len(c) >= 3:
                    c1[len(c[0])] += 1; c2[len(c[1])] += 1; c3[len(c[2])] += 1
                elif len(c) == 2:
                    c1[len(c[0])] += 1; c2[len(c[1])] += 1
        if c1:
            print(f"  1着候補車数: {sorted(c1.items())}  2着: {sorted(c2.items())}  3着: {sorted(c3.items())}")

        # --- 的中帯 ---
        hits = [r for r in samp if r.get("hit_row")]
        odds = [r["hit_row"]["hit_odds"] for r in hits]
        stakes = [r["hit_row"]["hit_stake"] for r in hits]
        pays = [r["hit_row"]["hit_payout"] for r in hits]
        if hits:
            print(f"  的中 {len(hits)}/{len(samp)}={len(hits)/len(samp)*100:.1f}%")
            print(f"    的中倍率: p10={q(odds,.1)} 中央={q(odds,.5)} p90={q(odds,.9)} max={max(odds)}")
            print(f"    的中点の賭け金: 中央={q(stakes,.5)}  払戻: 中央={q(pays,.5)} max={max(pays)}")
            band = collections.Counter()
            for o in odds:
                for lo, hi_ in ((0, 2), (2, 5), (5, 10), (10, 20), (20, 50), (50, 100), (100, 1e9)):
                    if lo <= o < hi_:
                        band[f"{lo}-{hi_ if hi_<1e9 else '+'}倍"] += 1
            print("    倍率帯:", [(k, v, f"{v/len(odds)*100:.0f}%") for k, v in
                                 sorted(band.items(), key=lambda x: float(x[0].split('-')[0]))])
            # ガミ
            gami = sum(1 for r in hits if (r["payout_detail"] or 0) <= (r["total_bet"] or 0))
            print(f"    ガミ {gami}/{len(hits)} = {gami/len(hits)*100:.0f}%")

        # --- 高額的中の分解 ---
        if hi:
            print(f"  --- 10万+ 的中 {len(hi)}件の分解 (賭け金 × 倍率 = 払戻) ---")
            for r in sorted(hi, key=lambda x: -(x["payout"] or 0)):
                h = r.get("hit_row")
                if not h:
                    print(f"    {r['date']} {r['venue']}{r['race_no']}R 払戻{r['payout']:,} (行未検出 "
                          f"{r['bet_types']} {r['n_points_total']}点)")
                    continue
                print(f"    {r['date']} {r['venue']}{r['race_no']:>2}R {r['race_name'][:12]:12s} "
                      f"{'/'.join(r['bet_types']):6s} {r['n_points_total']:>3}点 "
                      f"的中点{h['hit_stake']:>6,}円 x {h['hit_odds']:>7.1f}倍 = {h['hit_payout']:>8,}円 "
                      f"(商品払戻 {r['payout']:,})")
            st_ = [r["hit_row"]["hit_stake"] for r in hi if r.get("hit_row")]
            od_ = [r["hit_row"]["hit_odds"] for r in hi if r.get("hit_row")]
            if st_:
                print(f"    → 的中点の賭け金 中央={q(st_,.5):,}円 / 倍率 中央={q(od_,.5)}倍 "
                      f"(min={min(od_)} max={max(od_)})")


if __name__ == "__main__":
    main()
