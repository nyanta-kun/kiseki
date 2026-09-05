#!/usr/bin/env python3
"""買い目を「実際の組み合わせ」まで展開し、重複を合算した実効点数・実効配分を出す。

netkeiba の買い目は複数のフォーメーション行が重なることがあり、
行ごとの `n_points` を足すと同じ組み合わせを二重に数えてしまう。
"""
from __future__ import annotations
import collections, itertools, json, statistics as st
from pathlib import Path

HERE = Path(__file__).resolve().parent
NAMES = {465: "シュウの二車福", 354: "大河原修司", 401: "二ノ輪大嵐",
         585: "Equine Genius", 350: "鈴木誠"}
ORDERED = {"3連単", "2車単", "2枠単"}
SIZE = {"3連単": 3, "3連複": 3, "2車単": 2, "2車複": 2, "ワイド": 2, "2枠単": 2, "2枠複": 2}


def expand(row) -> list[tuple]:
    """1行 → 組み合わせのリスト（順序券種はタプル、非順序は sorted タプル）。"""
    bt = row["bet_type"]
    k = SIZE.get(bt, 3)
    cols, mode = row["cols"], row["mode"]
    out = []
    if cols:
        if mode == "ボックス" or len(cols) == 1:
            base = cols[0]
            if mode == "ボックス" or len(cols) == 1:
                if bt in ORDERED:
                    out = list(itertools.permutations(base, k))
                else:
                    out = [tuple(sorted(c)) for c in itertools.combinations(base, k)]
        if not out:
            for c in itertools.product(*cols):
                if len(set(c)) != len(c):
                    continue
                out.append(tuple(c) if bt in ORDERED else tuple(sorted(c)))
    elif row.get("combo"):
        c = tuple(int(x) for x in row["combo"])
        out = [c if bt in ORDERED else tuple(sorted(c))]
    return [(bt,) + c for c in out]


def product_stakes(r) -> dict:
    """商品 → {組み合わせ: 賭け金}"""
    s: dict = collections.defaultdict(int)
    for row in r["rows"]:
        u = row["unit"] or 0
        for c in expand(row):
            s[c] += u
    return dict(s)


def q(xs, p):
    xs = sorted(x for x in xs if x is not None)
    return xs[min(len(xs) - 1, int(len(xs) * p))] if xs else None


def main():
    for yid, name in NAMES.items():
        p = HERE / "an465" / f"{yid}.jsonl"
        if not p.exists():
            print(f"\n## {yid} {name}: 未取得"); continue
        rs = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
        samp = [r for r in rs if "sample" in r["tags"]]
        neff, ratio, dup, cover, sumchk = [], [], [], [], []
        for r in samp:
            s = product_stakes(r)
            if not s:
                continue
            neff.append(len(s))
            vals = list(s.values())
            ratio.append(max(vals) / min(vals))
            dup.append(r["n_points_total"] - len(s))
            sumchk.append(sum(vals) - (r["total_bet"] or 0))
        eq = sum(1 for x in ratio if x == 1.0)
        print(f"\n## {yid} {name}  n={len(neff)}")
        print(f"  実効点数(重複合算後): 中央={q(neff,.5)} min={min(neff)} p10={q(neff,.1)} "
              f"p90={q(neff,.9)} max={max(neff)} 平均={st.mean(neff):.2f}")
        print(f"  行合計との差(重複点数): 中央={q(dup,.5)} max={max(dup)} "
              f"重複あり={sum(1 for x in dup if x>0)}/{len(dup)}")
        print(f"  実効配分 均等={eq}/{len(ratio)}={eq/len(ratio)*100:.0f}%  "
              f"max/min 中央={q(ratio,.5):.2f} p90={q(ratio,.9):.2f} max={max(ratio):.2f}")
        print(f"  合計金額の一致チェック(展開合計-tfoot): 一致={sum(1 for x in sumchk if x==0)}/{len(sumchk)}")
        # 1点あたり金額の分布
        allv = [v for r in samp for v in product_stakes(r).values()]
        print(f"  1点賭け金の分布(全点): 中央={q(allv,.5)} p10={q(allv,.1)} p90={q(allv,.9)} "
              f"min={min(allv)} max={max(allv)}")


if __name__ == "__main__":
    main()
