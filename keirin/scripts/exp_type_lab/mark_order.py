#!/usr/bin/env python3
"""WT印 ◎○△ の3車で決まったのに並び違いで外す回はどれだけあるか（2026-09-10）。

印は `A_prediction_mark`（1=◎ / 2=○ / 3=△ / 4=注）。板の index で
`miss_anatomy_rows.pkl`（本番の商品・買い目・結果）と突き合わせる。
"""
from __future__ import annotations

import pickle
from collections import Counter, defaultdict

import numpy as np

Z = np.load("/tmp/race_type_board.npz", allow_pickle=True)
MK = Z["A_prediction_mark"]
LG = Z["LG"]
ROWS = pickle.load(open("/tmp/miss_anatomy_rows.pkl", "rb"))
for r in ROWS:
    m = MK[r["i"]]
    r["mk"] = {int(c): int(m[c - 1]) for c in range(1, 8)}
    r["m3"] = tuple(c for c in range(1, 8) if r["mk"][c] in (1, 2, 3))
    r["hon"] = next((c for c in range(1, 8) if r["mk"][c] == 1), None)
    r["tai"] = next((c for c in range(1, 8) if r["mk"][c] == 2), None)
    r["ana"] = next((c for c in range(1, 8) if r["mk"][c] == 3), None)
    r["m3_set"] = frozenset(r["m3"])
    r["fin_set"] = frozenset(r["fin"])
    r["m3_hit"] = len(r["m3"]) == 3 and r["fin_set"] == r["m3_set"]
    lg = LG[r["i"]]
    r["hon_tai_same_line"] = (r["hon"] and r["tai"]
                              and str(lg[r["hon"] - 1]) == str(lg[r["tai"] - 1])
                              and str(lg[r["hon"] - 1]) not in ("", "0"))
    # 印順（◎○△）と指数順（p3）の一致
    r["mark_eq_p3"] = tuple(r["p3o"][:3]) == (r["hon"], r["tai"], r["ana"])
    r["hon_is_a1"] = r["hon"] == r["p3o"][0]
W = {w: [r for r in ROWS if r["win"] == w] for w in ("explore", "confirm")}


def main() -> None:
    for wn, lab in (("confirm", "確認 2026-01〜08"), ("explore", "探索 2024-07〜2025-12")):
        rows = [r for r in W[wn] if len(r["m3"]) == 3]
        n = len(rows)
        nd = len({r["date"] for r in rows})
        m3 = [r for r in rows if r["m3_hit"]]
        print("\n" + "=" * 104)
        print(f"=== {lab}   印◎○△が揃っている {n:,}商品 / {nd}日")
        print("=" * 104)
        print(f"  ■ 決着3車が ◎○△ ちょうど: {len(m3):,}件 = {len(m3)/n*100:.2f}%"
              f"（無作為なら 1/35 = 2.86%）")
        hit = sum(r["in_legs"] for r in m3)
        print(f"     うち買い目に入っていた（的中）: {hit:,} = {hit/len(m3)*100:.2f}%")
        print(f"     並び違いで外した            : {len(m3)-hit:,} = "
              f"{(len(m3)-hit)/len(m3)*100:.2f}%"
              f"   ＝全商品の {(len(m3)-hit)/n*100:.2f}%")
        miss = [r for r in rows if not r["in_legs"]]
        print(f"     外れ全体 {len(miss):,}件のうち "
              f"{(len(m3)-hit)/len(miss)*100:.2f}% がこの形")

        print("\n  ■ ◎○△ で決まったときの並び（6通り）と、我々が当てられた率")
        lab6 = {(1, 2, 3): "◎-○-△", (1, 3, 2): "◎-△-○", (2, 1, 3): "○-◎-△",
                (2, 3, 1): "○-△-◎", (3, 1, 2): "△-◎-○", (3, 2, 1): "△-○-◎"}
        cnt = Counter()
        hitc = Counter()
        for r in m3:
            key = tuple(r["mk"][c] for c in r["fin"])
            cnt[key] += 1
            hitc[key] += r["in_legs"]
        print(f"     {'並び':8s} {'件数':>6s} {'割合%':>7s} {'我々の的中%':>11s}")
        for k, name in lab6.items():
            c = cnt.get(k, 0)
            print(f"     {name:8s} {c:6,} {c/len(m3)*100:7.2f} "
                  f"{hitc.get(k,0)/c*100 if c else 0:11.2f}")

        print("\n  ■ 条件別: ◎○△ ちょうどで決まる率／そのときの取りこぼし率")
        def seg(name, sub):
            if len(sub) < 100:
                return
            s3 = [r for r in sub if r["m3_hit"]]
            if not s3:
                return
            h = sum(r["in_legs"] for r in s3)
            print(f"     {name:24s} n={len(sub):5,}  ◎○△決着 {len(s3)/len(sub)*100:5.2f}%"
                  f"  取りこぼし {(len(s3)-h)/len(s3)*100:5.1f}%"
                  f"  （全商品比 {(len(s3)-h)/len(sub)*100:4.2f}%）")
        for t in "ABCDEF":
            seg(f"型{t}", [r for r in rows if r["type"] == t])
        for p in sorted({r["plan"] for r in rows}):
            seg(f"  {p}", [r for r in rows if r["plan"] == p])
        for f in ("axis", "gap", "pw_ent", "sp5"):
            v = np.array([r[f] for r in rows])
            e = [np.percentile(v, 100 * j / 5) for j in range(1, 5)]
            d = np.digitize(v, e)
            for j in range(5):
                seg(f"{f} Q{j+1}", [r for r, k in zip(rows, d) if k == j])
        seg("◎○が同ライン", [r for r in rows if r["hon_tai_same_line"]])
        seg("◎○が別ライン", [r for r in rows if not r["hon_tai_same_line"]])
        seg("印順=指数順(◎○△)", [r for r in rows if r["mark_eq_p3"]])
        seg("印順≠指数順", [r for r in rows if not r["mark_eq_p3"]])
        seg("◎=指数1位", [r for r in rows if r["hon_is_a1"]])
        seg("◎≠指数1位", [r for r in rows if not r["hon_is_a1"]])


if __name__ == "__main__":
    main()
