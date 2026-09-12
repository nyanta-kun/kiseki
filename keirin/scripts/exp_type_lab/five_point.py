#!/usr/bin/env python3
"""三連単を5点前後にできるか（2026-09-10）。

腕はすべて本番と同じ `prob_top` / `alloc='conf'`(floor 2.0) で、**点数と帯だけ**が違う。
入稿ゲート（平均想定払戻 > 2万円・全点 >= 2.0倍）を通ったものだけを売る。

🔴 件数が変わるので、点数を絞る腕には**同じレース母集団での現行との対比**と
   **確率とは無関係な無作為5点20本**の両方を置く。
"""
from __future__ import annotations

import pickle
import sys
from pathlib import Path

import numpy as np

ROWS = pickle.load(open("/tmp/five_point_rows.pkl", "rb"))
W = {w: [r for r in ROWS if r["win"] == w] for w in ("explore", "confirm")}


def stat(rows, key, nd):
    sel = [r["arms"][key] for r in rows if r["arms"].get(key) and r["arms"][key]["gate"]]
    if not sel:
        return None
    inv = sum(a["inv"] for a in sel); pay = sum(a["pay"] for a in sel)
    hits = [a for a in sel if a["pay"] > 0]
    shown = [a for a in sel if a["pay"] >= a["inv"]]
    pays = sorted(a["pay"] for a in hits)
    return dict(n=len(sel), perday=len(sel) / nd, k=np.mean([a["k"] for a in sel]),
                hit=len(hits) / len(sel) * 100, shown=len(shown) / len(sel) * 100,
                roi=pay / inv * 100, med=float(np.median(pays)) if pays else 0.0,
                mean=float(np.median([a["mean"] for a in sel])),
                big=sum(1 for p in pays if p >= 100_000) / nd)


HEAD = ("    {:22s} {:>6s} {:>5s} {:>7s} {:>9s} {:>7s} {:>9s} {:>10s} {:>8s}"
        .format("腕", "件/日", "点数", "的中%", "表示的中%", "ROI%", "払戻中央",
                "想定払戻中央", "10万+/日"))


def main() -> None:
    for wn, lab in (("confirm", "確認 2026-01〜08（本番相当）"),
                    ("explore", "探索 2024-07〜2025-12")):
        rows = W[wn]
        nd = len({r["date"] for r in rows})
        print("\n" + "=" * 118)
        print(f"=== {lab}   母集団 {len(rows):,}レース / {nd}日")
        print("=" * 118)
        print(HEAD)
        for key, name in [("cur", "現行（型ごと3〜14点）")] + \
                [(f"k{k}_noband", f"全型 {k}点・帯なし") for k in (3, 4, 5, 6, 8, 12)] + \
                [(f"k{k}_band", f"全型 {k}点・現行の帯") for k in (3, 4, 5, 6, 8, 12)]:
            s = stat(rows, key, nd)
            if not s:
                continue
            print(f"    {name:22s} {s['perday']:6.2f} {s['k']:5.1f} {s['hit']:7.2f}"
                  f" {s['shown']:9.2f} {s['roi']:7.1f} {s['med']:9,.0f}"
                  f" {s['mean']:10,.0f} {s['big']:8.3f}")

        # ── 同じレースで現行と5点を突き合わせる（両方がゲートを通る回だけ）──
        for key, nm in (("k5_noband", "5点・帯なし"), ("k5_band", "5点・現行の帯")):
            pair = [r for r in rows
                    if r["arms"]["cur"] and r["arms"]["cur"]["gate"]
                    and r["arms"].get(key) and r["arms"][key]["gate"]]
            if not pair:
                continue
            a = [r["arms"]["cur"] for r in pair]
            b = [r["arms"][key] for r in pair]
            f = lambda v: (np.mean([x["pay"] >= x["inv"] for x in v]) * 100,
                           sum(x["pay"] for x in v) / sum(x["inv"] for x in v) * 100,
                           np.median([x["pay"] for x in v if x["pay"] > 0]))
            sa, ra, ma = f(a); sb, rb, mb = f(b)
            print(f"\n    ■ 同一レース {len(pair):,}件（両方が入稿ゲートを通る回）  {nm}")
            print(f"       現行  表示的中 {sa:5.2f}%  ROI {ra:5.1f}%  払戻中央 {ma:8,.0f}円")
            print(f"       {nm:10s} 表示的中 {sb:5.2f}%  ROI {rb:5.1f}%  払戻中央 {mb:8,.0f}円")

        # ── 条件で分ける: Σp5 十分位ごとに「5点で足りるか」──────────────
        print(f"\n    ■ Σp5 十分位ごとの 5点・帯なし（『5点で足りるレース』はあるか）")
        sp5 = np.array([r["sp5"] for r in rows])
        edges = [np.percentile(sp5, 10 * j) for j in range(1, 10)]
        d = np.digitize(sp5, edges)
        print(f"      {'十分位':7s} {'ゲート通過/日':>12s} {'通過率%':>8s} {'的中%':>7s} "
              f"{'表示的中%':>9s} {'ROI%':>7s} {'想定払戻中央':>12s}")
        for k in range(10):
            sub = [r for r, x in zip(rows, d) if x == k]
            s = stat(sub, "k5_noband", nd)
            if not s:
                print(f"      D{k+1:<6d} {'0':>12s}   （1件もゲートを通らない）")
                continue
            print(f"      D{k+1:<6d} {s['perday']:12.2f} {s['n']/len(sub)*100:8.1f}"
                  f" {s['hit']:7.2f} {s['shown']:9.2f} {s['roi']:7.1f} {s['mean']:12,.0f}")


if __name__ == "__main__":
    main()


def segment_arms() -> None:
    """条件で分ける: Σp5 の高い側だけ少点数にする（閾値は**探索窓で**作る）。"""
    ex = W["explore"]
    sp = np.array([r["sp5"] for r in ex])
    THR = {q: float(np.percentile(sp, q)) for q in (40, 50, 60, 70, 80)}
    print("\n" + "#" * 118)
    print("### 条件で分ける — Σp5（確率上位5点の厚み）が高い側だけ少点数にする")
    print(f"###   閾値は探索窓の分位: " + "  ".join(f"p{q}={v:.3f}" for q, v in THR.items()))
    print("#" * 118)
    for wn, lab in (("confirm", "確認 2026-01〜08（本番相当）"),
                    ("explore", "探索 2024-07〜2025-12")):
        rows = W[wn]
        nd = len({r["date"] for r in rows})
        print(f"\n=== {lab}   {nd}日")
        print(HEAD + "   {:>28s}".format("同一レースの現行との差"))
        for q in (40, 50, 60, 70, 80):
            for key, nm in (("k4_noband", "4点"), ("k5_noband", "5点"), ("k6_noband", "6点")):
                sub = [r for r in rows if r["sp5"] >= THR[q]]
                s = stat(sub, key, nd)
                if not s:
                    continue
                pair = [r for r in sub if r["arms"]["cur"] and r["arms"]["cur"]["gate"]
                        and r["arms"][key] and r["arms"][key]["gate"]]
                a = [r["arms"]["cur"] for r in pair]
                b = [r["arms"][key] for r in pair]
                g = lambda v: (np.mean([x["pay"] >= x["inv"] for x in v]) * 100,
                               sum(x["pay"] for x in v) / sum(x["inv"] for x in v) * 100)
                sa, ra = g(a); sb, rb = g(b)
                print(f"    {f'Σp5 上位{100-q}% × {nm}':22s} {s['perday']:6.2f} {s['k']:5.1f}"
                      f" {s['hit']:7.2f} {s['shown']:9.2f} {s['roi']:7.1f} {s['med']:9,.0f}"
                      f" {s['mean']:10,.0f} {s['big']:8.3f}"
                      f"   n={len(pair):4,} 表示的中 {sb-sa:+5.2f}pt ROI {rb-ra:+5.1f}")

        # 無作為対照: 同じ件数を全体から引いて 5点で売る
        print("\n    ■ 無作為対照20本（同じ件数を母集団から無作為に選んで5点で売る）")
        for q in (50, 70):
            sub = [r for r in rows if r["sp5"] >= THR[q]]
            s = stat(sub, "k5_noband", nd)
            if not s:
                continue
            pool = [r for r in rows if r["arms"]["k5_noband"] and r["arms"]["k5_noband"]["gate"]]
            cs, cr = [], []
            for seed in range(20):
                rng = np.random.default_rng(seed)
                pick = rng.choice(len(pool), size=min(s["n"], len(pool)), replace=False)
                v = [pool[j]["arms"]["k5_noband"] for j in pick]
                cs.append(np.mean([x["pay"] >= x["inv"] for x in v]) * 100)
                cr.append(sum(x["pay"] for x in v) / sum(x["inv"] for x in v) * 100)
            print(f"      Σp5 上位{100-q}% × 5点  表示的中 {s['shown']:5.2f}% ↔ 対照中央 "
                  f"{np.median(cs):5.2f}%  勝ち {sum(s['shown']>c for c in cs):2d}/20   "
                  f"ROI {s['roi']:5.1f} ↔ {np.median(cr):5.1f}  勝ち {sum(s['roi']>c for c in cr):2d}/20")


segment_arms()
